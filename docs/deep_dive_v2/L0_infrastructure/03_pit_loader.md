# 03 — PIT 财务数据计算工具 `src/data/pit_loader.py`

---

## 一、文件定位

```
所属 Part  : Layer 0（数据基础层）
在数据流中 : loader.py → pit_loader.py → financial_factors.py
被谁调用   : src/factors/financial_factors.py（所有财务因子）
调用谁     : src/data/loader.py（get_financial_pit_raw / get_indicator_pit_raw 等）
```

`pit_loader.py` 解决的核心问题：**财务报告是"累计流量"值，不是"当期值"**。Tushare 财务三表里的利润、收入、现金流都是从年初累计到报告期末的值（Q1=1月累计，Q3=1-9月累计），直接使用会引入季节性偏差。TTM（过去12个月滚动值）消除了这种偏差。

> ⚠️ **与 v1 文档的重大差异**：v1 的 `pit_loader.py` 是基于 `PITLoader` 类的面向对象设计（有 `__init__`、`compute_ttm`、`compute_yoy` 方法）。**v2 已完全重构为函数式模块**，无任何类定义，所有函数独立可测。另外一个关键修复：`STALE_THRESHOLD_MONTHS` 从 18 改为 9，修复了覆盖率系统性偏低的 bug。

---

## 二、时间对齐与 PIT 假设

所有函数严格遵守**双重 PIT 过滤**：

```python
pit_date <= rebalance_date   # 公告日 ≤ 调仓日（数据已公开）
end_date < rebalance_date    # 报告期末 < 调仓日（报告期已结束）
```

两个条件缺一不可：
- 只有第一条：可能使用"报告期还未结束"的数据（如 Q3 公告在 9 月底前）
- 只有第二条：可能使用"已结束但尚未公告"的数据（如年报在 3 月 31 日前无法获取）

---

## 三、模块顶部

```python
import logging
from typing import Optional
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

STALE_THRESHOLD_MONTHS: int = 9
```

**`STALE_THRESHOLD_MONTHS = 9` 的含义与由来**：

最新可用报告期距离调仓日超过 9 个月，视为"过期数据"，相关数值置 NaN。

A 股财报披露周期：
- Q1（3月31日）→ 公告截止 4 月 30 日（间隔约 1 个月）
- H1（6月30日）→ 公告截止 8 月 31 日（间隔约 2 个月）
- Q3（9月30日）→ 公告截止 10 月 31 日（间隔约 1 个月）
- Annual（12月31日）→ 公告截止次年 4 月 30 日（间隔约 4 个月）

最长情景：Annual 公告截止 4 月 30 日，下一份 Q1 在 4 月 30 日公告。Q1 发布后，最新期从 Annual 切换到 Q1，间隔为 4+1=5 个月。考虑到部分公司公告较晚，设 9 个月为安全边际（3个月缓冲）。

**为何原来是 18 个月，现在改为 9 个月？**

原来 18 个月的阈值导致系统性 bug：某些股票最新 Q3 虽然已在 9 个月前公告，但按 18 个月门槛不算"过期"，导致系统误用非常陈旧的数据（如用 2022Q3 的数据在 2023Q4 进行计算）。实测修复后，财务因子覆盖率从约 43% 恢复到正常的 85% 以上。

---

## 四、核心函数逐一解析

### 4.1 `_filter_visible(pit_raw, rebalance_date, codes)` — 双重 PIT 过滤（L40-66）

```python
def _filter_visible(pit_raw, rebalance_date, codes=None) -> pd.DataFrame:
```

内部工具函数（`_` 前缀），其他三个函数都先调用它。

```python
mask = (
    (pit_raw["pit_date"] <= rebalance_date)   # L61
    & (pit_raw["end_date"] < rebalance_date)  # L62 注意是严格小于
)
vis = pit_raw[mask]
```

**注意 `end_date < rebalance_date`（严格小于，不含等于）**：例如 2022-09-30 是 Q3 的报告期末，调仓日也是 2022-09-30，此时 Q3 的报告期实际上还没"结束"（当天还在运营），所以用 `<` 而不是 `<=`。

---

### 4.2 `make_ttm(fp_raw, col, rebalance_date, codes, stale_months)` — TTM 计算（L69-208）

这是 `pit_loader.py` 最复杂的函数，共 140 行。

**为何需要 TTM？**

以净利润为例，Tushare 的 `n_income` 是从年初到报告期末的累计值：
- Q1 = 1月净利润
- H1 = 1-6月净利润合计
- Q3 = 1-9月净利润合计
- Annual = 1-12月净利润合计

直接比较 Q1（假设50亿）和 Annual（假设200亿），会误认为利润跌了 75%。TTM 化后，Q1 的"过去12个月利润"= 上年 Annual + 本年 Q1 − 上年 Q1，消除了这种季节性偏差。

**TTM 计算公式**：

```
若最新可用期是年报（end_date 月份=12）：
    TTM = 年报值（本身就是12个月累计）

若最新可用期是季报（Q1/H1/Q3）：
    TTM = 最近年报值 + 最新季报累计 - 上年同期季报累计

示例（最新期为 2023Q3）：
    TTM = 2022年报 + 2023Q3累计 - 2022Q3累计
```

**六步实现逻辑（L110-207）**：

```python
# Step A（L120-124）：每只股票的最新可用记录（最新 end_date + 最新 pit_date）
# 用 dropna + groupby().last() 取最新非NaN值
latest = (
    vis.dropna(subset=[col])
    .groupby("ts_code")[["end_date", col]]
    .last()
)
```

注意：这里的 `groupby().last()` 接受，因为 `dropna(subset=[col])` 已排除 `col=NaN` 的行，且前面已经 `sort_values`，所以每组的最后一行就是整行最新值（无跨行混淆风险）。

```python
# Step B（L127-140）：最近年报记录
annual = vis[vis["end_date"].dt.month == 12]   # 12月末 = 年报
```

```python
# Step C（L143-144）：判断最新期是否为年报
is_annual = df["latest_end"].dt.month == 12
```

```python
# Step D（L147-154）：构建上年同期查找表
vis_dedup = vis.drop_duplicates(subset=["ts_code", "end_date"], keep="last")
prev_lookup = vis_dedup.set_index(["ts_code", "_year", "_month"])[col]
# key = (ts_code, year-1, month)，用于 .get() 批量查找
```

```python
# Step E（L156-197）：组装 TTM
result[is_annual] = df.loc[is_annual, "latest_val"]   # 年报直接用

# 非年报：TTM = annual_val + latest_val - prev_year_same_val
# 有重要的年报年份匹配校验（L181-184）：
annual_year_ok = (
    na_df["annual_end"].notna()
    & (na_df["annual_end"].dt.year == na_df["latest_end"].dt.year - 1)
)
# 若最近年报不是上一年的年报（如最新 2024Q1，最近年报是 2022 年报）
# 则 TTM 结果不可信，置 NaN
```

**年报年份校验的重要性**：如果某公司 2023 年年报还没出，最近年报是 2022 年的，而最新期已是 2024Q1，则：`TTM = 2022年报 + 2024Q1 - 2023Q1`，混用了相差两年的数据，结果错误，必须置 NaN。

```python
# Step F（L199-201）：陈旧度过滤
stale_mask = df["latest_end"] < stale_cutoff   # stale_cutoff = T - 9 个月
result[stale_mask] = np.nan
```

---

### 4.3 `get_pit_latest(pit_raw, rebalance_date, codes, stale_months)` — 存量指标快照（L211-257）

```python
def get_pit_latest(pit_raw, rebalance_date, codes=None, stale_months=STALE_THRESHOLD_MONTHS) -> pd.DataFrame:
```

用于**存量指标**（资产负债表字段：`total_assets`、`total_hldr_eqy_exc_min_int` 等），直接取最新可用报告期的值，不需要 TTM 化。

与 `loader._pit_snapshot()` 的区别：超过 `stale_months` 的股票**数值列置 NaN**，但保留 `end_date` 列用于审计。

```python
# L239-243：使用 drop_duplicates 取整行（不用 groupby().last()）
latest = (
    vis.sort_values(["end_date", "pit_date"])
    .drop_duplicates(subset=["ts_code"], keep="last")
    .set_index("ts_code")
)

# L246-251：陈旧度过滤，只将数值列置 NaN
num_cols = latest.select_dtypes(include=np.number).columns
latest.loc[stale_mask, num_cols] = np.nan
```

---

### 4.4 `get_roe_delta(ip_raw, rebalance_date, codes)` — ROE 同比变化（L260-328）

计算当期最新 ROE − 上年同期 ROE，用于 `roe_delta` 因子。

**"上年同期"的精确定义**：按报告期月份对齐，而非日历年对齐：
- 最新期是 2023-09-30（Q3）→ 上年同期是 2022-09-30（Q3）
- 最新期是 2023-12-31（Annual）→ 上年同期是 2022-12-31（Annual）

这与 `make_ttm` 的 Step D 使用相同的 `(ts_code, year-1, month)` 查找表模式：

```python
prev_roes = pd.Series([
    roe_lookup.get((ts_code, int(py), int(pm)), np.nan)   # L313-316
    for ts_code, py, pm in zip(latest.index, prev_year, prev_month)
], index=latest.index)
```

---

### 4.5 `get_quarterly_history(ip_raw, col, rebalance_date, codes, n_quarters, stale_months)` — 季度历史（L331-402）

返回每只股票最近 `n_quarters` 个季度的字段值，用于：
- `factor_accrual`：需要当期 vs 4季前的总资产（n_quarters=5，取 q0 和 q4）
- `factor_roe_smoothed_4q`：需要最近 4 季 q_roe（n_quarters=4）
- `factor_roe_delta_3q`：需要 q0 和 q3（n_quarters=4）
- `factor_roe_stability`：需要最近 4 季 q_dt_roe

返回 DataFrame 列名约定：`q0`（最新期）、`q1`（次新）…… `q{n-1}`（最旧）。

```python
# q0 = last_n[-1]（最新值），q1 = last_n[-2]……（L383-386）
for i in range(n_quarters):
    pos = n - 1 - i
    row[f"q{i}"] = float(last_n[pos]) if pos >= 0 else np.nan
```

---

### 4.6 `get_field_yoy_delta(pit_raw, col, rebalance_date, codes)` — 任意字段同比变化（L405-469）

通用版 `get_roe_delta`，支持任意字段（如 `grossprofit_margin`）的同比变化。用于 `gross_margin_trend` 因子。

---

## 五、内部辅助函数

| 函数 | 说明 |
|---|---|
| `_filter_visible(pit_raw, date, codes)` | 双重 PIT 过滤，所有公开函数的前置步骤 |

---

## 六、落盘产物

无。`pit_loader.py` 不写任何文件，纯计算模块。

---

## 七、相关测试

- `tests/test_pit.py`：随机抽样 5-10 只股票手动核对：
  - `make_ttm` 的结果是否与手动计算一致
  - 陈旧度过滤（9 个月阈值）是否按预期执行
  - `get_roe_delta` 的上年同期对齐是否正确

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| `vis.empty`（无可见 PIT 数据）| 返回空 Series/DataFrame（不抛异常）|
| TTM 三个前提条件任一缺失 | 该股票的 TTM 置 NaN（不抛异常）|
| 年报年份不匹配（如最新期 2024Q1 但只有 2022 年报）| TTM 置 NaN |
| 最新期超过 9 个月未更新 | 数值列置 NaN |

---

## 九、数据流图

```
loader.get_financial_pit_raw() → fp_raw (完整 financial_pit, reset_index)
loader.get_indicator_pit_raw() → ip_raw (完整 indicator_pit, reset_index)
         ↓
_filter_visible(fp_raw / ip_raw, rebalance_date)
         ↓
make_ttm          → 累计流量指标的 TTM Series
                    （n_income / revenue / n_cashflow_act / free_cashflow）
get_pit_latest    → 存量指标最新快照
                    （total_assets / total_hldr_eqy_exc_min_int / roe / roa...）
get_roe_delta     → ROE 同比变化 Series
get_quarterly_history → 季度历史 DataFrame（q0/q1/...）
get_field_yoy_delta   → 任意字段同比变化 Series
         ↓
financial_factors.py（所有财务因子函数直接调用上述函数）
```

---

## 十、领域知识补充

**为什么累计值需要 TTM 而存量值不需要？**

财务报表有两类数据：
- **累计流量**（Income Statement / Cash Flow）：从年初到报告期末的累积，季节性强
- **存量**（Balance Sheet）：某一时点的资产负债快照，无季节性问题

TTM 只应用于累计流量字段（`n_income`、`revenue`、`n_cashflow_act`、`free_cashflow`），存量字段（`total_assets`、`total_hldr_eqy_exc_min_int`）直接取最新快照。

**A 股财报披露规律**（影响 PIT 可用性）：

| 报告类型 | 报告期末 | 披露截止日 | 最晚公告间隔 |
|---|---|---|---|
| Q1（季报）| 3 月 31 日 | 4 月 30 日 | 约 1 个月 |
| H1（半年报）| 6 月 30 日 | 8 月 31 日 | 约 2 个月 |
| Q3（季报）| 9 月 30 日 | 10 月 31 日 | 约 1 个月 |
| Annual（年报）| 12 月 31 日 | 次年 4 月 30 日 | 约 4 个月 |

这意味着：调仓日在 4 月末之前时，很多公司的年报尚未公告，只能使用 Q3 数据。因此在同一个调仓日，不同股票可能处于不同的"最新可用报告期"（有的已可用 Annual，有的只能用 Q3），这是 TTM 需要同时处理多种情景的根本原因。
