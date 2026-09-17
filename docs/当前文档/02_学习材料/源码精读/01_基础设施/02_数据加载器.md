# 02 — 统一数据加载接口 `src/data/loader.py`

---

## 一、文件定位

```
所属 Part  : Layer 0（数据基础层）
在数据流中 : 所有 src/ 模块读取数据的唯一入口
被谁调用   : financial_factors.py / price_factors.py / alt_factors.py / backtest/engine.py 等
调用谁     : src/config.py（路径常量）
```

`loader.py` 是 `data/processed/` Parquet 文件与上层模块之间的**接口层**。上层代码永远不直接调用 `pd.read_parquet()`，只调用此处的函数。这样的好处：PIT 过滤逻辑集中在一处，改一次全模块受益。

> ⚠️ **与 v1 的重大差异**：v1 的 loader 只有 10 个左右函数（约 535 行）。v2 增加了 12 类备选数据的加载函数，新增了 `_pit_snapshot` 内部 PIT 工具函数，以及 `get_financial_pit_raw`、`get_indicator_pit_raw`、`get_holder_pit_raw` 等供 `pit_loader.py` 使用的全量原始数据接口。

---

## 二、时间对齐与 PIT 假设

**两类数据的不同时间保证**：

| 数据类型 | 时间保证 | 代表函数 |
|---|---|---|
| 日频市场数据（行情/市值/行业）| `trade_date` 当日收盘后即可用，T 日调仓可用 T 日收盘数据 | `load_daily_quote` / `load_daily_basic` |
| 财务 PIT 数据 | `pit_date <= T AND end_date < T`，即公告日不超过调仓日 | `load_financial_pit` / `load_indicator_pit` |

**PIT 过滤的执行位置**：`_pit_snapshot()` 内部函数统一执行，调用方无需额外处理。这是整个项目 PIT 正确性的**中央保证点**。

---

## 三、模块顶部

```python
import logging          # 标准日志，不用 print
from typing import Optional

import pandas as pd
from src import config as cfg
```

```python
_CACHE: dict[str, pd.DataFrame] = {}  # 模块级缓存，整表读取的中小型文件
```

`_CACHE` 是全局单例缓存。首次读取某个 Parquet 文件后，将整个 DataFrame 存入 `_CACHE`。后续所有调用直接返回内存中的 DataFrame，避免重复 I/O。适合中小型文件（`financial_pit`、`index_member`、`stock_status` 等），不适合大文件（`daily_quote` 约 270 万行）。

---

## 四、核心函数逐一解析

### 4.1 `_read_cached(filename)` — 整表缓存读取（L44-64）

```python
def _read_cached(filename: str) -> pd.DataFrame:
```

**逻辑**：
1. 检查 `_CACHE` 中是否已有该文件
2. 若有，直接返回（不重复读盘）
3. 若没有，从 `cfg.DATA_PROC / filename` 读取，存入 `_CACHE` 后返回

**注意**：`_CACHE` 存的是 DataFrame 的**引用**，不是副本。调用方若需修改，必须先 `.copy()`，否则会修改缓存中的原始数据（已在 `_pit_snapshot` 内部通过避免 inplace 操作来保证安全）。

---

### 4.2 `_read_parquet_range(filename, start, end, codes)` — 大表谓词下推（L67-101）

```python
def _read_parquet_range(filename, start, end, codes=None) -> pd.DataFrame:
```

对 `daily_quote`（约 270 万行）、`daily_basic`、`moneyflow` 等大宽表，不能整表缓存进内存。使用 **pyarrow 谓词下推**：在 Parquet 文件层面直接过滤，只读需要的行到内存。

```python
filters: list = [
    ("trade_date", ">=", start),
    ("trade_date", "<=", end),
]
if codes is not None:
    filters.append(("ts_code", "in", codes))

return pd.read_parquet(path, filters=filters)   # pyarrow 后端执行谓词下推
```

**谓词下推的条件**：Parquet 文件必须是**列式存储**且有**行组元数据**（`rowgroup statistics`）。`csv_to_parquet.py` 在写入时显式设置 `row_group_size`，保证谓词下推生效。

---

### 4.3 `_pit_snapshot(df, rebalance_date, codes)` — PIT 快照提取（L108-145）

```python
def _pit_snapshot(df, rebalance_date, codes=None) -> pd.DataFrame:
```

这是整个项目 PIT 正确性的核心函数。双重过滤逻辑：

```python
snap = df[
    (df["pit_date"] <= rebalance_date)   # 公告日 ≤ 调仓日（已公开）
    & (df["end_date"] < rebalance_date)  # 报告期 < 调仓日（报告期已结束）
]
```

**第二个条件的必要性**：防止使用"当期正在进行中的季度"的数据。例如 2022-03-31 的 Q1 年报，即使公告日早于调仓日，若调仓日是 2022-03-15，则 end_date=2022-03-31 > 调仓日，过滤掉。

**去重逻辑**（L141-145）：

```python
return (
    snap.sort_values(["end_date", "pit_date"])
        .drop_duplicates("ts_code", keep="last")   # 取整行最新记录
        .set_index("ts_code")
)
```

**关键细节**：使用 `drop_duplicates` 取整行，而不是 `groupby().last()`。原因：`groupby().last()` 在 pandas ≥2.0 对每列独立取最后非 NaN 值（`skipna=True`），会把 `end_date` 来自最新行但 `col` 值来自旧行的不同行拼在一起，导致日期与数值不对应（"行混淆"）。`drop_duplicates` 始终保留完整的一行。

---

### 4.4 `load_daily_quote(start, end, codes)` — 日行情（L152-172）

```python
def load_daily_quote(start, end, codes=None) -> pd.DataFrame:
```

返回后复权价格和收益率。关键列：

| 列名 | 含义 | 单位/类型 |
|---|---|---|
| `close_adj` | 后复权收盘价 | 元 |
| `open_adj` | 后复权开盘价 | 元 |
| `ret` | 后复权日收益率 | 小数（0.01 = 1%）|
| `vol` | 成交量 | 手（100股/手）|
| `amount` | 成交额 | **千元**（注意：不是元，不是万元）|

**时间对齐**：`trade_date` 当日收盘后即可用，调仓日 T 可用 T 日数据。量价因子计算窗口的 `end=rebalance_date` 参数保证不使用 T+1 之后的数据。

---

### 4.5 `load_daily_basic(start, end, codes)` — 每日基础指标（L175-193）

返回市值相关指标。中性化最重要的列：

| 列名 | 含义 | 单位 |
|---|---|---|
| `total_mv` | 总市值 | **万元**（因子计算时需 ×10000 转元）|
| `free_float_mv` | 自由流通市值 | 万元 |
| `log_free_float_mv` | log(自由流通市值) | 预处理时直接使用 |
| `dv_ttm` | TTM 股息率（近似 PIT）| 百分比（如 2.5 表示 2.5%）|

**单位陷阱**：`total_mv` 单位是**万元**，`financial_pit` 中的财务字段单位是**元**。计算 E/P 等比率时必须统一单位：`financial_factors.py` 中用 `_WAN_TO_YUAN = 10_000.0` 做转换。

---

### 4.6 `load_financial_pit(rebalance_date, codes)` — 财务三表 PIT（L196-223）

```python
def load_financial_pit(rebalance_date, codes=None) -> pd.DataFrame:
```

内部调用 `_pit_snapshot` 执行 PIT 过滤。注意：此函数使用 `f_ann_date`（首次披露日）作为 `pit_date`，比 `ann_date`（公告日）**更早几天**，因此更保守（某些数据可能尚未在系统中更新）。

返回的主要字段（元为单位）：

| 字段 | 含义 |
|---|---|
| `revenue` | 营业收入（累计值，需 TTM 化）|
| `n_income` | 净利润（归母，累计值，需 TTM 化）|
| `n_cashflow_act` | 经营活动现金流净额（累计值，需 TTM 化）|
| `free_cashflow` | 自由现金流（Tushare 预计算，需 TTM 化）|
| `total_assets` | 总资产（存量，取最新快照）|
| `total_hldr_eqy_exc_min_int` | 归母净资产（存量，取最新快照）|
| `pit_date` | 公告日（用于审计时间对齐）|
| `end_date` | 报告期末（如 2023-09-30）|

---

### 4.7 `load_indicator_pit(rebalance_date, codes)` — 财务衍生指标 PIT（L226-248）

使用 `ann_date`（公告日）而非 `f_ann_date` 作为 `pit_date`，比 `load_financial_pit` 略保守（ann_date ≥ f_ann_date，通常晚 0-3 天）。

提供预计算的衍生指标，避免因子层自己算时出现口径不一致：

| 字段 | 含义 |
|---|---|
| `roe` | 净资产收益率（加权平均，百分比）|
| `roa` | 总资产收益率（百分比）|
| `grossprofit_margin` | 毛利率（百分比）|
| `assets_turn` | 资产周转率（倍数）|
| `debt_to_assets` | 资产负债率（百分比）|
| `netprofit_yoy` | 净利润同比增速（百分比）|
| `or_yoy` | 营收同比增速（百分比）|
| `q_roe` | 单季 ROE（百分比）|
| `q_dt_roe` | 单季扣非 ROE（百分比，用于 roe_stability）|

---

### 4.8 `load_holder_pit(rebalance_date, codes)` — 股东人数 PIT（L251-291）

股东人数数据有一个特殊的 PIT 处理：原始数据存在少量 `ann_date <= end_date` 的异常记录（公告日居然早于或等于报告期，可能是数据录入错误）。为保守起见，使用**双重可用日保证**：

```python
available_date = max(pit_date, end_date)   # 取公告日和报告期中较晚的一个
# 过滤条件：available_date <= rebalance_date AND end_date < rebalance_date
```

这样即使数据录入有误（ann_date 早于实际可用），也不会引入未来信息。

---

### 4.9 `load_dividend_events(start, end, codes, date_col)` — 分红事件（L294-332）

与其他 PIT 函数不同，分红数据是**事件表**（不是快照），每行是一个除权除息事件。调用方根据需要做 `groupby(ts_code)` 聚合计算 TTM 股息率。

`date_col` 参数支持两种过滤基准：
- `"ex_date"`（除权日，默认）：用于统计过去 12 个月实际发放分红
- `"pit_date"`：用于按公告可用性审计

---

### 4.10 `load_universe(rebalance_date)` — 成分股快照（L335-365）

返回指定调仓日的中证500全部 500 只成分股（含 `tradable=False` 的停牌等股票）。

**关键行**：
```python
df = _read_cached("index_member.parquet")
avail = df.index.get_level_values("rebalance_date")
if rebalance_date not in avail:
    raise KeyError(...)  # L359-363，明确的错误信息含最近3个可用日期
return df.loc[rebalance_date]  # 直接用 rebalance_date 作为第一级索引
```

`rebalance_date` 必须是 `index_member.parquet` 中**已有的月末交易日**。如果传入非交易日或者数据未下载到该月，会抛出 `KeyError` 并提示最近可用日期。

主要列：

| 列名 | 含义 |
|---|---|
| `tradable` | 综合停牌/ST/新股过滤后的可投资标志（布尔）|
| `is_suspended` | 是否停牌 |
| `is_limit_up_locked` | 是否涨停一字板（不可加仓）|
| `is_limit_down_locked` | 是否跌停一字板（不可减仓）|
| `is_st` | 是否 ST/\*ST |
| `is_new_stock` | 是否上市不足 `NEW_STOCK_DAYS` 天 |
| `index_weight` | 基准权重（小数，合计约等于 1.0）|

---

### 4.11 备选数据加载函数（L542-845）

v2 新增 11 个备选数据加载函数，结构类似，核心差异在时间对齐方式：

| 函数 | 类型 | PIT 方式 | 注意事项 |
|---|---|---|---|
| `load_hk_hold` | 日频快照 | trade_date 当日 | 2014-11-17 前无数据 |
| `load_analyst_rc_pit` | 事件窗口 | pit_date ≤ T，lookback_days=180 | 返回窗口内所有研报，调用方需聚合 |
| `load_share_float` | 事件（未来） | ann_date ≤ T < float_date ≤ T+horizon | 查未来 30 天内即将解禁的股票 |
| `load_holder_trade_pit` | 事件窗口 | pit_date ≤ T，lookback_days=90 | 股东/高管增减持事件 |
| `load_cyq_perf` | 日频快照 | trade_date 当日 | 2018 年前覆盖较少 |
| `load_pledge_stat` | 快照 | end_date ≤ T（无 ann_date）| 保守 PIT：用 end_date 代替 ann_date |
| `load_moneyflow_hsgt` | 日频市场级 | trade_date 当日 | 全市场聚合，非个股截面 |
| `load_top_list` / `load_top_inst` | 日频事件 | trade_date 当日 | 仅部分股票有记录（触发条件） |
| `load_block_trade` | 日频事件 | trade_date 当日 | 无 premium 字段，需自行计算 |
| `load_stk_surv` | 事件窗口 | surv_date ≤ T，lookback_days=90 | 机构调研记录 |

---

### 4.12 全量原始数据接口（L490-530）

三个供 `pit_loader.py` 使用的特殊函数，返回整张表的 `reset_index()` 版本：

```python
def get_financial_pit_raw() -> pd.DataFrame:   # L506-517
def get_indicator_pit_raw() -> pd.DataFrame:   # L520-530
def get_holder_pit_raw() -> pd.DataFrame:      # L490-503
```

**为何需要这些函数**：`pit_loader.py` 的 `make_ttm`、`get_roe_delta` 等函数需要**多个历史报告期**（例如 Q1 TTM 需要同年 Q1 和上年年报以及上年 Q1 三期数据），不能只用 `load_financial_pit` 的单期快照。这三个函数返回完整历史，由 `pit_loader.py` 负责在计算时按日期过滤。

---

### 4.13 `clear_cache()` — 清空缓存（L532-535）

```python
def clear_cache() -> None:
```

清空 `_CACHE` 字典。用于两种场景：
1. **内存受限**：长时间运行后内存压力大，可主动清除
2. **测试隔离**：单元测试中不同测试用例需要使用不同数据时，清除跨用例的缓存污染

---

## 五、内部辅助函数

| 函数 | 说明 |
|---|---|
| `_read_cached(filename)` | 整表缓存读取，private |
| `_read_parquet_range(filename, start, end, codes)` | 大表谓词下推，private |
| `_pit_snapshot(df, rebalance_date, codes)` | PIT 核心逻辑，private |

---

## 六、落盘产物

无。`loader.py` 只读数据，不写任何文件。

---

## 七、相关测试

- `tests/test_pit.py`：随机抽样股票手动核对 PIT 过滤结果（核心正确性测试）
- `tests/test_pipeline_contracts.py`：间接依赖 loader

**高风险未测点**：`_pit_snapshot` 的 `drop_duplicates` vs `groupby().last()` 差异（行混淆问题）目前依赖人工审计，建议补充针对性单元测试。

---

## 八、失败与降级路径

| 失败场景 | 行为 |
|---|---|
| Parquet 文件不存在 | `FileNotFoundError`（含提示运行 `csv_to_parquet.py`）|
| `rebalance_date` 不在 `index_member` | `KeyError`（含最近 3 个可用日期提示）|
| `trade_date` 不在 `stock_status` | `KeyError`（提示可能是非交易日）|
| `start > end` | `ValueError` |

---

## 九、数据流图

```
data/processed/*.parquet
    ↓ _read_cached()（小表，整表入内存缓存）
    ↓ _read_parquet_range()（大表，谓词下推）
    ↓ _pit_snapshot()（PIT 过滤，仅财务类）
load_financial_pit()  → financial_factors.py
load_indicator_pit()  → financial_factors.py
load_daily_quote()    → price_factors.py / alt_factors.py / backtest/engine.py
load_daily_basic()    → financial_factors.py / price_factors.py
load_universe()       → universe.py
load_stock_status()   → backtest/engine.py
load_industry()       → factors/preprocess.py
get_financial_pit_raw() / get_indicator_pit_raw() / get_holder_pit_raw()
                      → pit_loader.py（TTM 计算需要多期历史）
```

---

## 十、领域知识补充

**模块级缓存 vs 函数级缓存**：

`loader.py` 用 `_CACHE` 字典缓存整张表（适合 `index_member`、`financial_pit` 等中等大小但被频繁调用的表）。`price_factors.py` 和 `alt_factors.py` 用 `@lru_cache(maxsize=1)` 缓存交易日序列（只读一次的小对象）。两种缓存策略针对不同的访问模式：频繁用相同 key 访问 = `_CACHE` 字典；几乎只有一个值 = `lru_cache`。

**谓词下推（predicate pushdown）的局限性**：

谓词下推只有在 Parquet 行组的 `statistics`（min/max）与过滤条件对齐时才有效。如果数据是按 `ts_code` 排序写入的（每个行组都跨越所有日期），则 `trade_date` 的谓词下推无法跳过行组。`csv_to_parquet.py` 写入时按 `(trade_date, ts_code)` 排序，使 `trade_date` 范围查询能有效跳过行组，大幅加速读取。
