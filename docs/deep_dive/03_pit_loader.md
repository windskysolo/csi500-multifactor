# 03 — PIT 财务数据提取工具 `src/data/pit_loader.py`

---

## 一、文件定位

```
流程位置：第 3 步（构建因子面板）的辅助工具
读取：通过 loader.get_financial_pit_raw() / get_indicator_pit_raw() 读取完整原始表
写入：无（纯计算，不写磁盘）
被谁调用：src/factors/financial_factors.py（计算所有财务因子）
调用谁：src/data/loader.py（获取原始 DataFrame）
```

本模块解决的问题：`loader.load_financial_pit()` 给出的是"每只股票最新一期财务数据的快照"，但财务因子的计算往往需要**多期历史数据**：

- TTM（过去12个月滚动值）：需要最新季报 + 去年同期季报 + 最近年报三期数据
- ROE 同比变化：需要当期和上年同期两期 ROE

`pit_loader` 补足了这个"单期快照不够用"的场景。

---

## 二、模块顶部

```python
import logging
from typing import Optional
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

STALE_THRESHOLD_MONTHS: int = 9     # 财报陈旧度阈值（月）
```

**`STALE_THRESHOLD_MONTHS = 9` 的由来（注释 L35-37）：**

A 股财报披露的最大间隔是 6 个月（Q3 → 年报，或年报 → Q1）。设置 9 个月的陈旧度阈值，保留 3 个月的"安全边际"。

历史上曾设置为 18 个月，结果导致合法数据被误过滤，覆盖率系统性偏低（应 ≥85% 但只有 ~43%）。修复后改为 9 个月。

---

## 三、核心函数逐一解析

### 3.1 `_filter_visible(pit_raw, rebalance_date, codes)` — PIT 可见性过滤（L40-66）

```python
def _filter_visible(
    pit_raw: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    codes: Optional[list[str]] = None,
) -> pd.DataFrame:
```

这是本模块所有函数共用的内部过滤器（下划线开头，不对外暴露）。

**两个过滤条件（L59-63）：**

```python
mask = (
    (pit_raw["pit_date"] <= rebalance_date)    # 公告日 ≤ 调仓日
    & (pit_raw["end_date"] < rebalance_date)   # 报告期 < 调仓日
)
```

与 `loader._pit_snapshot` 的双重过滤逻辑完全一致。区别在于：
- `loader._pit_snapshot` 在过滤后只保留每只股票最新一行（最终快照）
- `_filter_visible` 保留所有合法历史记录（为了后续的 TTM 计算需要多期数据）

**测试用例覆盖（test_pit.py 第 36-111 行）：**
- `test_pit_date_future_is_excluded`：公告日在 T 之后 → 不可见
- `test_pit_date_on_T_is_visible`：公告日恰好等于 T → 可见（当日公告当日可用）
- `test_end_date_same_period_excluded`：报告期 ≥ T → 不可见
- `test_both_conditions_must_hold`：两个条件缺一不可

---

### 3.2 `make_ttm(fp_raw, col, rebalance_date, codes, stale_months)` — TTM 计算（L69-208）

这是本文件最核心、最复杂的函数，也是财务因子计算的基础。

```python
def make_ttm(
    fp_raw: pd.DataFrame,
    col: str,               # 要做 TTM 化的字段（如 'n_income'）
    rebalance_date: pd.Timestamp,
    codes: Optional[list[str]] = None,
    stale_months: int = STALE_THRESHOLD_MONTHS,
) -> pd.Series:             # 返回：index=ts_code，value=TTM值
```

**为什么需要 TTM？**

财务报告按季度累计披露：
- Q1（1-3月累计）
- H1（1-6月累计）
- Q3（1-9月累计）
- 年报（1-12月累计）

如果直接用最新期的数值，会有严重的季节性偏差：Q1 的净利润通常远小于年报（只有 3 个月），即使同一家公司，用 Q1 数据算出的估值因子（EP = 净利润/市值）会比用年报数据小约 4 倍，导致 Q1 末调仓日时"所有股票都显得很贵"的假象。

TTM（Trailing Twelve Months）把不同报告期的数据统一成"过去 12 个月的总量"，消除季节性偏差。

**TTM 公式（两种情况）：**

```
情况1：最新可用报告期是年报（12月末）
  TTM = 年报值（本身就是 12 个月累计）

情况2：最新可用报告期是季报（Q1/H1/Q3）
  TTM = 最近年报值 + 最新季报累计值 - 上年同期季报累计值
```

**示例（以净利润为例）：**

假设 T = 2024-06-30，某公司的可用数据：
- 2024 Q1 年报（公告日 2024-04-28）：累计净利润 = 300 亿（= 2024年1-3月合计）
- 2023 年报（公告日 2024-03-31）：净利润 = 1200 亿（= 2023年1-12月合计）
- 2023 Q1（公告日 2023-04-29）：累计净利润 = 250 亿（= 2023年1-3月合计）

TTM = 1200 + 300 - 250 = 1250 亿（= 2024年1-3月 + 2023年4-12月）

用年报口径 1200 vs TTM 口径 1250，差别约 4%，在实际使用中是合理的近似。

**函数的六步实现：**

**Step A（L116-124）：找到每只股票的最新可用记录**

```python
latest = (
    vis.dropna(subset=[col])         # 排除该字段为 NaN 的行
    .groupby("ts_code")[["end_date", col]]
    .last()                          # 取最新 end_date 对应的行
)
latest.columns = ["latest_end", "latest_val"]
```

注意：这里先 `dropna(subset=[col])` 再 `groupby().last()`，确保 `end_date` 和 `col` 来自同一行（避免跨行混淆的问题，同 `_pit_snapshot` 中的注释）。

**Step B（L127-140）：找到每只股票最近的年报**

```python
annual_vis = vis[vis["end_date"].dt.month == 12]   # 年报 = 12月末
annual = (
    annual_vis.dropna(subset=[col])
    .groupby("ts_code")[["end_date", col]]
    .last()
)
annual.columns = ["annual_end", "annual_val"]
```

`end_date.dt.month == 12` 识别年报（报告期为 12 月 31 日）。

**Step C（L143）：判断最新期是否本身就是年报**

```python
is_annual = df["latest_end"].dt.month == 12
```

如果是年报，直接用年报值作为 TTM，跳过复杂的加减计算。

**Step D（L147-153）：构建"上年同期"查找表**

```python
vis_dedup = vis.drop_duplicates(subset=["ts_code", "end_date"], keep="last")
vis_dedup = vis_dedup.assign(
    _year=vis_dedup["end_date"].dt.year,
    _month=vis_dedup["end_date"].dt.month,
)
prev_lookup = vis_dedup.set_index(["ts_code", "_year", "_month"])[col]
```

这建了一个以 `(ts_code, 年, 月)` 为键的查找表，用于快速找"上年同期"的值。

例如：想找 "000001.SZ" 在 2023Q1（2023年3月）的上年同期数据，就查 `prev_lookup[("000001.SZ", 2022, 3)]`。

**Step E（L156-197）：计算 TTM**

```python
# 年报：直接赋值
result[is_annual] = df.loc[is_annual, "latest_val"]

# 非年报：TTM = annual_val + latest_val - prev_year_same_val
for 非年报的每只股票:
    prev_year_val = prev_lookup[(ts_code, latest_year-1, latest_month)]
    if annual 是 latest 的上一年 and prev_year_val 存在 and latest_val 存在:
        result = annual_val + latest_val - prev_year_val
    else:
        result = NaN
```

**为什么要检查 `annual_year_ok`（L181-183）？**

```python
annual_year_ok = (
    na_df["annual_end"].notna()
    & (na_df["annual_end"].dt.year == na_df["latest_end"].dt.year - 1)
)
```

这个检查防止"年报年份不匹配"的错误。

错误场景：T = 2024-06-30，某公司：
- 最新可用季报：2024 Q1（latest_end = 2024-03-31）
- 最近年报：2022年报（annual_end = 2022-12-31，因为 2023 年报还没公告）

如果不检查，TTM = 2022年报 + 2024Q1 - 2023Q1，但这里跨越了 2 年，中间缺失了 2023 年全年数据，计算结果完全错误。正确做法：此时应该置 NaN。

**Step F（L199-201）：陈旧度过滤**

```python
stale_cutoff = rebalance_date - pd.DateOffset(months=stale_months)
stale_mask = df["latest_end"] < stale_cutoff
result[stale_mask] = np.nan
```

如果一只股票最新的报告期距今超过 9 个月，说明公司可能有异常（长期不披露财报），这个 TTM 值不可靠，置 NaN。

---

### 3.3 `get_pit_latest(pit_raw, rebalance_date, codes, stale_months)` — 最新快照+陈旧过滤（L211-257）

```python
def get_pit_latest(
    pit_raw: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    codes: Optional[list[str]] = None,
    stale_months: int = STALE_THRESHOLD_MONTHS,
) -> pd.DataFrame:
```

**用途**：对于不需要 TTM 化的存量指标（`total_assets`、`total_hldr_eqy_exc_min_int` 等），直接取最新 PIT 快照，但加上陈旧度过滤。

**与 `loader._pit_snapshot` 的区别：**

| | `loader._pit_snapshot` | `pit_loader.get_pit_latest` |
|---|---|---|
| PIT 过滤 | 是 | 是 |
| 陈旧度过滤 | **否** | **是** |
| 陈旧数据处理 | 直接返回（包含陈旧数据）| 数值列置 NaN（保留 end_date 用于审计）|
| 适用场景 | 不需要陈旧度判断的场景 | 财务因子的存量指标取值 |

**执行逻辑（L239-243）：**

```python
latest = (
    vis.sort_values(["end_date", "pit_date"])
    .drop_duplicates(subset=["ts_code"], keep="last")  # 整行去重，不跨行混淆
    .set_index("ts_code")
)
```

**陈旧数据处理（L245-254）：**

```python
stale_cutoff = rebalance_date - pd.DateOffset(months=stale_months)
stale_mask = latest["end_date"] < stale_cutoff

if stale_mask.any():
    latest = latest.copy()
    num_cols = latest.select_dtypes(include=np.number).columns
    latest.loc[stale_mask, num_cols] = np.nan    # 只把数值列置 NaN
    # end_date 保留（用于审计：可以知道是哪期数据过期了）
```

---

### 3.4 `get_roe_delta(ip_raw, rebalance_date, codes)` — ROE 同比变化（L260-328）

```python
def get_roe_delta(
    ip_raw: pd.DataFrame,       # indicator_pit 完整原始表
    rebalance_date: pd.Timestamp,
    codes: Optional[list[str]] = None,
) -> pd.Series:                 # index=ts_code，value=roe_delta（百分比）
```

**用途**：计算 `roe_chg` 因子（ROE 环比 / 同比变化），反映盈利质量的趋势。

**"上年同期"的定义：**
- 最新期是 2024 Q1（2024-03-31）→ 上年同期是 2023 Q1（2023-03-31）
- 最新期是 2023 年报（2023-12-31）→ 上年同期是 2022 年报（2022-12-31）

不是简单的"上一个季度"（环比），而是"去年同季"（同比），消除季节性影响。

**执行逻辑（L294-320）：**

```python
# Step1：每只股票最新的 ROE
latest = (
    vis.dropna(subset=["roe"])
    .groupby("ts_code")[["end_date", "roe"]]
    .last()
)

# Step2：建立 (ts_code, year, month) → roe 查找表
roe_lookup = vis_dedup.set_index(["ts_code", "_year", "_month"])["roe"]

# Step3：查找上年同期 ROE
prev_year  = latest["latest_end"].dt.year - 1
prev_month = latest["latest_end"].dt.month   # 同月

prev_roes = pd.Series([
    roe_lookup.get((ts_code, int(py), int(pm)), np.nan)
    for ts_code, py, pm in zip(latest.index, prev_year, prev_month)
], index=latest.index)

# Step4：差值
roe_delta = latest["latest_roe"] - prev_roes
```

**返回 NaN 的情况：**
- 新上市公司（没有上年同期数据）
- 上年同期数据缺失（公告异常）
- 当期数据陈旧（超过 9 个月）

---

## 四、内部辅助函数

| 函数 | 说明 |
|------|------|
| `_filter_visible` | PIT 双重过滤，被 `make_ttm` / `get_pit_latest` / `get_roe_delta` 共用 |

---

## 五、数据流图

```
loader.get_financial_pit_raw()  ──→  fp_raw (reset_index 后的完整表)
                                        │
                    ┌───────────────────┴────────────────────┐
                    ↓                                         ↓
            make_ttm(fp_raw, col, T)                 get_pit_latest(fp_raw, T)
            （流量指标 TTM 化）                       （存量指标最新快照 + 陈旧过滤）
                    │                                         │
                    └──────────────┬──────────────────────────┘
                                   ↓
                     pd.Series / pd.DataFrame（index=ts_code）
                                   │
                    financial_factors.py 组装因子值
```

```
loader.get_indicator_pit_raw()  ──→  ip_raw
                                        │
                                        ↓
                                 get_roe_delta(ip_raw, T)
                                        │
                                 pd.Series（roe_delta，index=ts_code）
                                        │
                             financial_factors.py（roe_chg 因子）
```

---

## 六、领域知识补充

**为什么财务数据不能直接用报告期（end_date）作为可用日期？**

A 股上市公司的财报披露时间表（法规要求的最晚时间）：

| 报告类型 | 最晚披露时间 |
|--------|-------------|
| 年报 | 次年 4 月 30 日 |
| 半年报 | 下半年 8 月 31 日 |
| Q1 / Q3 季报 | 报告期后 1 个月内 |

实际中：
- 一家公司 2023 年报的报告期是 2023-12-31
- 但年报最晚可以到 2024-04-30 才披露

如果策略在 2024-01-31（1月末调仓日）就使用 2023 年报数据，相当于用了"最快还没公告"的数据，引入了严重的前视偏差。

**这就是为什么必须用 `pit_date`（公告日）而不是 `end_date`（报告期）来判断数据是否可用。**

A 股财报的实际公告时间分布：
- 年报：大多数集中在 3-4 月（约 80% 在 4 月披露）
- 半年报：集中在 8 月
- Q1/Q3 季报：集中在 4 月底和 10 月底

因此，在 3 月末调仓时，通常只能看到去年 Q3 的财务数据（因为年报多数还未公告）。

**TTM 计算中的一个常见误区：**

误区：用最新季报的值（如 Q1 净利润 300 亿）直接 × 4 来年化，得到年化 TTM = 1200 亿。

问题：这种简单年化忽视了季节性（有些公司 Q1 利润特别少，Q4 特别多），也没有考虑同期对比。正确的 TTM 公式用的是实际的加减法，而不是乘法。
