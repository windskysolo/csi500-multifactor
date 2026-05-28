# 02 — 统一数据读取接口 `src/data/loader.py`

---

## 一、文件定位

```
流程位置：第 2 步后（csv_to_parquet 生成 Parquet 后，其他所有模块读数据的入口）
读取：data/processed/*.parquet（各种已处理数据表）
写入：无（只读）
被谁调用：几乎所有 src/ 模块（factors / evaluation / signal / portfolio / backtest / attribution）
调用谁：只依赖 src/config.py（路径常量）
```

loader.py 是数据访问的统一"门卫"。其他模块不允许直接 `pd.read_parquet()`，必须通过这里的接口。这样做的好处：

1. **缓存集中管理**：小表只读一次，大表用谓词下推按需裁剪，不缓存整表
2. **PIT 过滤内置**：调用方无需手动过滤 `pit_date`，减少因子层犯错的机会
3. **错误信息统一**：文件不存在时给出引导性提示而不是裸露的 Python 异常

---

## 二、模块顶部

```python
import logging                    # 内部日志，不用 print
from typing import Optional       # 类型注解

import pandas as pd

from src import config as cfg     # 路径常量

log = logging.getLogger(__name__) # 日志 logger，名称为模块全路径
```

```python
_CACHE: dict[str, pd.DataFrame] = {}  # 模块级缓存字典（生命周期 = 进程）
```

`_CACHE` 是模块级变量（下划线开头表示内部使用），在整个进程生命周期内持久存在。第一次调用时读磁盘，之后直接返回内存中的引用，避免重复 I/O。

---

## 三、核心函数逐一解析

### 3.1 `_read_cached(filename)` — 带缓存的整表读取（L44-64）

```python
def _read_cached(filename: str) -> pd.DataFrame:
```

**用途**：读取中小型 Parquet 文件并缓存到 `_CACHE`。第二次及以后调用直接返回内存中的 DataFrame。

**执行逻辑：**

```python
if filename not in _CACHE:                    # 未命中缓存
    path = cfg.DATA_PROC / filename
    if not path.exists():
        raise FileNotFoundError(...)          # 明确的错误提示
    _CACHE[filename] = pd.read_parquet(path)  # 读取并缓存
return _CACHE[filename]                        # 返回缓存（或刚读的）
```

**哪些表走整表缓存：**
- `financial_pit.parquet`（56K 行，财务三表）
- `indicator_pit.parquet`（56K 行，财务衍生指标）
- `holder_pit.parquet`（股东人数）
- `dividend_pit.parquet`（分红事件）
- `index_member.parquet`（60K 行，成分股快照）
- `stock_status.parquet`（~200 万行，约 50MB，**最大的整表缓存**）

> **注意**：`stock_status` 整表缓存约 50MB，是内存占用最大的单个缓存。如果需要在同一进程中处理大量数据，可调用 `clear_cache()` 释放。

---

### 3.2 `_read_parquet_range(filename, start, end, codes)` — 谓词下推读取（L67-101）

```python
def _read_parquet_range(
    filename: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    codes: Optional[list[str]] = None,
) -> pd.DataFrame:
```

**用途**：对 270 万行级别的大表（日行情、资金流向等），只读取指定日期范围 + 股票代码的切片，不把整表加载到内存。

**关键技术：pyarrow 谓词下推（Predicate Pushdown）**

Parquet 格式是列式存储，并且支持行组（row group）级别的元数据（记录每个 row group 中各列的最小/最大值）。谓词下推让读取器在磁盘层面就过滤掉不需要的行组，根本不把它们载入内存：

```python
filters: list = [
    ("trade_date", ">=", start),   # 谓词1：日期下界
    ("trade_date", "<=", end),     # 谓词2：日期上界
]
if codes is not None:
    filters.append(("ts_code", "in", codes))  # 谓词3：股票白名单
return pd.read_parquet(path, filters=filters)
```

**参数校验：**

```python
if start > end:
    raise ValueError(f"start ({start.date()}) > end ({end.date()})")
```

这是"边界处验证"原则的体现：只在系统边界（数据入口）校验，内部代码信任这里已校验过的数据。

**哪些表走谓词下推：**
- `daily_quote.parquet`（270 万行，日行情）
- `daily_basic.parquet`（日基础指标）
- `margin.parquet`（融资融券）
- `moneyflow.parquet`（资金流向）
- `industry.parquet`（行业归属）

---

### 3.3 `_pit_snapshot(df, rebalance_date, codes)` — PIT 横截面快照（L108-145）

```python
def _pit_snapshot(
    df: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    codes: Optional[list[str]] = None,
) -> pd.DataFrame:
```

**用途**：这是 PIT（Point-In-Time）过滤的核心实现，被 `load_financial_pit` 和 `load_indicator_pit` 调用。

**双重过滤条件（L130-133）：**

```python
snap = df[
    (df["pit_date"] <= rebalance_date)    # 条件1：公告日 ≤ T
    & (df["end_date"] < rebalance_date)   # 条件2：报告期 < T
]
```

- **条件1（`pit_date <= T`）**：公告日在 T 日当天或之前，说明数据已经对外公布，T 日可以看到
- **条件2（`end_date < T`）**：报告期早于 T 日，确保我们不会用到"当前季度"还未结束的报告

两个条件同时满足，才是真正"T 日可用"的数据。只满足其一是不够的：
- 满足条件1但不满足条件2：说明这是当季数据（端午前公告了6月底的数据），实际上是用了未来的报告期
- 满足条件2但不满足条件1：报告期合法但尚未公告，属于未来信息

**去重逻辑（L141-145）：**

```python
return (
    snap.sort_values(["end_date", "pit_date"])
        .drop_duplicates("ts_code", keep="last")  # 每只股票保留一行
        .set_index("ts_code")
)
```

同一只股票可能有多条记录（不同报告期、同一报告期的多次修订）。`sort_values` 后 `drop_duplicates(keep="last")` 取的是：
- 报告期（`end_date`）最新的那条（最新季报/年报）
- 如果最新报告期有多次修订（`pit_date` 不同），取修订版本最新的那条

**为什么用 `drop_duplicates` 而不是 `groupby().last()`？**

这是一个重要的正确性细节（已在代码注释中说明，L139-140）：

`groupby().last()` 在 pandas ≥2.0 中对每一列独立取"最后一个非 NaN 值"，可能导致 `end_date` 来自一行、数值列来自另一行，数据错位。`drop_duplicates` 取的是整行，保证所有字段来自同一条记录。

---

### 3.4 `load_daily_quote(start, end, codes)` — 日行情（L152-172）

```python
def load_daily_quote(
    start: pd.Timestamp,
    end: pd.Timestamp,
    codes: Optional[list[str]] = None,
) -> pd.DataFrame:
```

**返回的 DataFrame 结构：**

| 列名 | 类型 | 含义 |
|------|------|------|
| `open` / `close` | float64 | 原始（非复权）开/收盘价 |
| `pre_close` | float64 | 前一交易日收盘价 |
| `pct_chg` | float64 | 涨跌幅（百分比，如 -1.23 表示跌 1.23%）|
| `vol` | float64 | 成交量（手）|
| `amount` | float64 | 成交额（千元）|
| `adj_factor` | float64 | 后复权因子（历史累积）|
| `close_adj` | float64 | **后复权收盘价** = close × adj_factor |
| `open_adj` | float64 | **后复权开盘价** = open × adj_factor |
| `ret` | float64 | **后复权日收益率** = close_adj[t] / close_adj[t-1] - 1 |

索引：`(trade_date, ts_code)` MultiIndex

**重要**：策略全程使用 `close_adj`、`open_adj`、`ret` 这三列，不使用原始价格。后复权价格已经把历史上的拆股、配股、分红等影响折算进去，不同时间点的价格可直接比较。

---

### 3.5 `load_daily_basic(start, end, codes)` — 每日基础指标（L175-193）

**主要用途**：提供 `free_float_mv`（自由流通市值）和 `log_free_float_mv`（对数自由流通市值），用于因子中性化。

**自由流通市值 vs 总市值 vs 流通市值**：
- 总市值 = 所有股份（含限售股）× 当日收盘价
- 流通市值 = 可流通股份（含战略配售等）× 当日收盘价
- **自由流通市值** = 可自由买卖的股份（不含机构锁定、大股东持仓等）× 收盘价

中性化用自由流通市值，因为它更接近市场实际可交易的"体量"，是市值风格的更准确度量。用总市值会让很多大公司（如国有大行）的市值被大股东持股虚增。

---

### 3.6 `load_financial_pit(rebalance_date, codes)` — 财务三表 PIT（L196-223）

```python
def load_financial_pit(
    rebalance_date: pd.Timestamp,
    codes: Optional[list[str]] = None,
) -> pd.DataFrame:
```

**执行流程（两行代码）：**

```python
raw = _read_cached("financial_pit.parquet").reset_index()  # 读整表（有缓存则直接返回）
return _pit_snapshot(raw, rebalance_date, codes)            # 应用 PIT 过滤
```

**返回的关键列：**

| 列名 | 含义 |
|------|------|
| `revenue` | 营业总收入（累计，流量指标）|
| `total_profit` | 利润总额（累计）|
| `n_income` | 净利润（归母，累计，**最常用**）|
| `total_assets` | 总资产（存量指标，取最新期即可）|
| `total_liab` | 总负债（存量）|
| `total_hldr_eqy_exc_min_int` | 归母净资产（存量，BP因子分子）|
| `n_cashflow_act` | 经营活动现金流（累计，CFP因子分子）|
| `pit_date` | 本条记录的公告日（用于审计）|
| `end_date` | 本条记录的报告期（用于审计）|

---

### 3.7 `load_indicator_pit(rebalance_date, codes)` — 财务衍生指标 PIT（L226-248）

类似 `load_financial_pit`，但来源是 Tushare 的 `fina_indicator` 接口（预计算好的衍生指标）。

**与 `load_financial_pit` 的区别**：
- `financial_pit` 是原始三表（利润表、资产负债表、现金流量表），数值是"绝对额"（元）
- `indicator_pit` 是衍生指标（ROE、ROA、毛利率等），数值是"比率"（百分比或小数）
- PIT 逻辑相同，但 `indicator_pit` 使用 `ann_date` 而非 `f_ann_date` 作为 `pit_date`（略保守，通常晚 0-3 天）

**关键列：**

| 列名 | 含义 |
|------|------|
| `roe` | 净资产收益率（%）|
| `roa` | 总资产收益率（%）|
| `grossprofit_margin` | 毛利率（%）|
| `assets_turn` | 总资产周转率（次）|
| `netprofit_yoy` | 净利润同比增速（%）|
| `or_yoy` | 营收同比增速（%）|
| `debt_to_assets` | 资产负债率（%）|

---

### 3.8 `load_holder_pit(rebalance_date, codes)` — 股东人数 PIT（L251-291）

股东人数数据有一个特殊问题：存在少量 `ann_date <= end_date` 的异常记录（公告日比报告期还早，逻辑上不可能）。

解决方案（L273-275）：引入 `available_date = max(pit_date, end_date)`，用更保守的可用日期做过滤：

```python
if "available_date" not in raw.columns:
    raw["available_date"] = raw[["pit_date", "end_date"]].max(axis=1)
```

这样即使 `pit_date` 异常早，`available_date` 也会保证至少等于报告期，不会引入前视偏差。

---

### 3.9 `load_dividend_events(start, end, codes, date_col)` — 分红事件（L294-332）

```python
def load_dividend_events(
    start, end, codes=None, date_col="ex_date"
) -> pd.DataFrame:
```

分红数据是"事件表"（每行一个分红事件），与其他快照表不同。

`date_col` 参数的两种用途：
- `date_col="ex_date"`（默认）：按除权除息日过滤，用于计算过去 12 个月的实际分红（股息率因子）
- `date_col="pit_date"`：按公告日过滤，用于审计时间可见性

---

### 3.10 `load_universe(rebalance_date)` — 成分股快照（L335-365）

```python
def load_universe(rebalance_date: pd.Timestamp) -> pd.DataFrame:
```

**返回**：以 `ts_code` 为索引的 DataFrame，包含全部 500 只成分股及其状态标志。

**关键列：**

| 列名 | 类型 | 含义 |
|------|------|------|
| `index_weight` | float64 | 在中证500中的权重（%）|
| `tradable` | bool | 是否可交易（综合停牌/ST/新股状态）|
| `is_suspended` | bool | 停牌 |
| `is_limit_up_locked` | bool | 一字涨停（无法成交）|
| `is_limit_down_locked` | bool | 一字跌停（无法成交）|
| `is_st` | bool | ST或ST* |
| `is_new_stock` | bool | 上市 <180 天 |

**错误处理（L358-365）**：如果传入的日期不在 `index_member` 中（如非月末交易日），给出最近3个有效日期的提示：

```python
if rebalance_date not in avail:
    recent = sorted(avail.unique())[-3:]
    raise KeyError(
        f"{rebalance_date.date()} 不在 index_member 中。"
        f"最近可用日期：{[d.date() for d in recent]}"
    )
```

---

### 3.11 `load_stock_status(trade_date, codes)` — 交易日股票状态（L368-398）

```python
def load_stock_status(
    trade_date: pd.Timestamp,
    codes: Optional[list[str]] = None,
) -> pd.DataFrame:
```

**与 `load_universe` 的区别**：

| | `load_universe` | `load_stock_status` |
|---|---|---|
| 时间粒度 | 月末调仓日 | 任意交易日 |
| 数据来源 | `index_member.parquet` | `stock_status.parquet` |
| 用途 | 因子计算、信号生成 | 回测引擎（每日成交约束）|
| 覆盖范围 | 中证500成分股 | 全市场 |

回测引擎在 T+1 日判断能否成交时，需要查 T+1 日（执行日）的股票状态，这时用 `load_stock_status(T+1)` 而非 `load_universe`（后者是月末快照）。

---

### 3.12 `load_industry(start, end, codes)` — 行业归属（L447-465）

返回每日行业归属，由 `csv_to_parquet` 阶段从申万行业在职期间（`in_date` 到 `out_date`）展开成日频数据。

---

### 3.13 `get_rebalance_dates(start, end)` — 调仓日列表（L468-487）

```python
def get_rebalance_dates(
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
) -> list[pd.Timestamp]:
```

返回 `index_member` 中所有月末调仓日的列表。

**为什么不用 `pd.date_range(freq='BME')` 自己生成？**

因为 `pd.date_range` 生成的是理论上的月末交易日，不考虑实际节假日。`index_member` 的日期是真实成分股快照的日期，保证是实际交易日，更可靠。

---

### 3.14 `get_financial_pit_raw()` / `get_indicator_pit_raw()` / `get_holder_pit_raw()` — 全量原始表（L490-529）

这三个函数返回对应表的**完整历史数据**（不做 PIT 过滤），供需要多期数据的模块使用。

使用场景：
- `get_financial_pit_raw()`：`pit_loader.make_ttm()` 需要同时看最新期和上年同期，无法用单期快照
- `get_indicator_pit_raw()`：`pit_loader.get_roe_delta()` 需要对比当期和上年同期 ROE
- `get_holder_pit_raw()`：`holder_chg` 因子需要对比最新和次新两期股东人数变化

---

### 3.15 `clear_cache()` — 清空缓存（L532-535）

```python
def clear_cache() -> None:
    _CACHE.clear()
    log.info("loader 缓存已清空")
```

在内存受限或测试隔离时调用。例如：
- 跑完训练期数据后，切换到测试期需要释放内存
- `pytest` 中不同测试用例之间需要隔离（避免一个用例的缓存污染另一个）

---

## 四、内部辅助函数

| 函数 | 说明 |
|------|------|
| `_read_cached` | 整表读取 + 缓存（被 `load_financial_pit` 等调用）|
| `_read_parquet_range` | 谓词下推读取（被 `load_daily_quote` 等调用）|
| `_pit_snapshot` | PIT 过滤核心逻辑（被 `load_financial_pit` 等调用）|

---

## 五、数据流图

```
data/processed/
├── financial_pit.parquet     ──→  _read_cached()  ──→  _pit_snapshot()  ──→  load_financial_pit()
├── indicator_pit.parquet     ──→  _read_cached()  ──→  _pit_snapshot()  ──→  load_indicator_pit()
├── holder_pit.parquet        ──→  _read_cached()  ──→  （自定义过滤）  ──→  load_holder_pit()
├── dividend_pit.parquet      ──→  _read_cached()  ──→  （日期过滤）    ──→  load_dividend_events()
├── index_member.parquet      ──→  _read_cached()  ──→  （日期切片）    ──→  load_universe()
├── stock_status.parquet      ──→  _read_cached()  ──→  （日期切片）    ──→  load_stock_status()
├── daily_quote.parquet       ──→  _read_parquet_range()  ──→  load_daily_quote()
├── daily_basic.parquet       ──→  _read_parquet_range()  ──→  load_daily_basic()
├── margin.parquet            ──→  _read_parquet_range()  ──→  load_margin()
├── moneyflow.parquet         ──→  _read_parquet_range()  ──→  load_moneyflow()
└── industry.parquet          ──→  _read_parquet_range()  ──→  load_industry()

         调用方（factors / evaluation / backtest 等）
```

---

## 六、领域知识补充

**Parquet 格式 vs CSV 格式**

| 维度 | CSV | Parquet |
|------|-----|---------|
| 存储格式 | 行式文本 | 列式二进制 |
| 读取速度（同等数据）| 慢（需解析文本）| 快（10x）|
| 谓词下推 | 不支持（需读全文件）| 支持（跳过不需要的行组）|
| 类型信息 | 无（需推断）| 有（精确保存 float64、Timestamp 等）|
| 压缩 | 无 | 自带 snappy/zstd 压缩（~3-10x 压缩率）|

对于 270 万行的 `daily_quote.parquet`，用谓词下推读 1 年的数据（约 65K 行），比读整个 CSV 快约 50 倍。

**`(trade_date, ts_code)` MultiIndex 的使用模式**

大多数返回的 DataFrame 使用 MultiIndex，常见操作：

```python
# 取某一天的所有股票
df.loc["2021-06-30"]

# 取某只股票的所有日期
df.xs("000001.SZ", level="ts_code")

# 按日期展开为宽表（行=日期，列=股票代码）
df["close_adj"].unstack("ts_code")
```
