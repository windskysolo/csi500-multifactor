# 20 — brinson.py：BHB 行业归因

> 对应源文件：`src/attribution/brinson.py`
> 实际行数：**553 行**（计划快照为 410，新增 F8-001~003 修复、F8-002 现金仓位处理，撰写前已核实）
> 处理方式：**沿用 + 核查更新**
> 所属 Part：Part 6 — 回测与归因层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 6  归因    ← 本文件（brinson.py）
Layer 6  归因    factor_attr.py（并列，不互相依赖）
Layer 5  回测    engine.py（提供 BacktestResult.actual_weights）
Layer 0  数据    loader.py（load_universe / load_industry / load_daily_quote）
```

`brinson.py` 将月度超额收益**分解为行业配置效应和行业内选股效应**，回答"超额来自押注行业还是选股能力"。

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游 | `BacktestResult.actual_weights` | 策略日频持仓权重（不归一化）|
| 上游 | `load_universe(T)` | 基准权重（CSI500 成分股权重）|
| 上游 | `load_industry(start, end)` | 行业分类（SW2021 一级）|
| 上游 | `load_daily_quote(start, end, codes)` | 后复权 close_adj/open_adj |
| 下游 | `pipeline/stages.run_attribution_stage` | 写落盘产物 |

### 计划快照 vs 实际变化

| 变化 | 说明 |
|------|------|
| **F8-001**：期间收益改用 T+1 开盘买入口径 | 与回测引擎的 T+1 成交假设一致，而非用 ret 列（T 收盘→T_next 收盘）|
| **F8-002**：策略权重不归一化 | 现金仓位 = 1 - sum(w_p) 单独记录，不将策略"拉满仓"归因 |
| **F8-003**：内部加载完整基准成分股收益 | 防止基准股票收益被填 0 导致 benchmark_return 低估 |
| 新增 `summarize_by_segment` | 按年度或自定义时段汇总归因结果（线性可加）|
| 行数从 410 → 553 | F8 系列修复、代码注释、`summarize_by_segment`、两个内部一致性验证 |

---

## 二、时间对齐与 PIT 假设

BHB 归因的时间对齐是本模块最复杂、最容易出错的部分：

```
调仓日 T（月末）：
  - 基准权重 w_b：从 index_member.parquet 加载 T 日成分股（T 日收盘前已知）
  - 行业映射：T 日（T 日收盘前已知）
  - 策略权重 w_p：actual_weights 中 T 之后第一个可用日期（约等于 T+1 执行后收盘）

期间收益（T → T_next）：（F8-001）
  - T+1 开盘买入，T_next 收盘卖出
  - 第一日收益 = close_adj(T+1) / open_adj(T+1) - 1（从开盘到收盘）
  - 后续日收益 = close_adj(t) / close_adj(t-1) - 1（收盘到收盘）
  - 停牌 NaN → 0（与回测引擎一致）
```

**为何策略权重取 T+1 而非 T**：T 日收盘后才能生成目标权重，T+1 开盘才执行完成。若用 T 日权重，等于用"还没执行的权重"计算归因，期间收益与权重的时间点不对齐。

**F8-002：策略权重不归一化**（L99–121）：
```python
return w   # F8-002: 不归一化，保留原始权重和
```
由于停牌/涨跌停约束，执行后的实际持仓权重和通常 < 1（现金仓位 > 0）。若强制归一化（÷sum），会让 Brinson 归因误以为策略满仓，导致选股效应被放大。保留原始权重和，则 `cash_weight = 1 - w_p_sum` 被单独记录到 `period_summary["cash_weight"]`。

**F8-003：基准成分股代码**（L361–376）：
```python
benchmark_codes: set[str] = set()
for T, _ in periods:
    snap = load_universe(T)
    benchmark_codes.update(str(c) for c in snap.index)

all_attr_codes = sorted(strategy_codes | benchmark_codes)
```
若只加载策略持仓的股票收益，则基准中有但策略未持有的股票的收益为 0，导致 `benchmark_return` 被低估（基准好像什么都没赚）。F8-003 先收集所有出现过的基准成分股，一次性加载后复权价格，保证基准收益准确。

---

## 三、模块顶部：导入与常量

```python
# L35–50
import bisect, logging
import numpy as np
import pandas as pd
from src import config as cfg
from src.data.loader import (
    get_rebalance_dates,
    load_daily_quote,
    load_industry,
    load_universe,
)
```

无模块级常量（BHB 公式本身不需要）。行业分类中"未分类"股票使用 `cfg.INDUSTRY_UNCLASSIFIED_CODE` 占位，金融板块标记使用 `cfg.FINANCIAL_SECTOR_CODES`。

---

## 四、核心函数逐一解析

### 4.1 `compute_brinson_attribution` — 主入口

```python
# L308–500
def compute_brinson_attribution(
    actual_weights: pd.DataFrame,
    backtest_start: pd.Timestamp,
    backtest_end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
```

**返回值**：
- `industry_attr`：每期每行业归因效应，MultiIndex=(period_start, industry_code)，列：W_p/W_b/R_p/R_b/4种效应
- `period_summary`：每期汇总，index=period_start，包含 strategy_return/benchmark_return/excess_return/cash_weight 等

**内部一致性双验证**（L474–490）：
```python
# V1: strategy_return - benchmark_return ≈ total_effect
v1_discrepancy = (period_summary["strategy_return"] - period_summary["benchmark_return"]
                  - period_summary["total_effect"]).abs().max()

# V2: alloc + select + interact ≈ total_effect
v2_discrepancy = (period_summary[["allocation_effect","selection_effect","interaction_effect"]].sum(axis=1)
                  - period_summary["total_effect"]).abs().max()

if v1_discrepancy > 1e-8 or v2_discrepancy > 1e-8:
    log.warning("BHB 验证失败...")
```
这两个验证在数学上恒成立（由 BHB 公式构造保证），若超过 1e-8 说明代码有 bug 或浮点精度问题。

---

### 4.2 `_compute_period_brinson` — 单期 BHB 分解

```python
# L232–300
def _compute_period_brinson(
    w_p: pd.Series,    # 策略权重（和 ≤ 1，不归一化）
    w_b: pd.Series,    # 基准权重（和 = 1）
    r: pd.Series,      # 期间累计收益
    industry_map: pd.Series,  # ts_code → industry_code
) -> pd.DataFrame:
```

**BHB 四效应计算**（向量化，无 groupby.apply）：

```python
# 行业层聚合
W_p  = grp["w_p"].sum()      # 策略行业权重
W_b  = grp["w_b"].sum()      # 基准行业权重
R_p  = wp_r / W_p             # 策略行业内加权平均收益（W_p=0 时约定 R_p=0）
R_b  = wb_r / W_b             # 基准行业内加权平均收益
R_b_total = (W_b * R_b).sum()  # 基准总收益

# 四效应
allocation_effect  = (W_p - W_b) * (R_b - R_b_total)   # 配置效应
selection_effect   =  W_b        * (R_p - R_b)           # 选股效应
interaction_effect = (W_p - W_b) * (R_p - R_b)           # 交叉效应
total_effect       =  W_p * R_p  -  W_b * R_b            # 总效应
```

**F8-002 对 BHB 的影响**（L244–248）：
> 当 w_p 未归一化时（F8-002），Σ W_p_i < 1，Σ(W_p_i - W_b_i) = -cash_weight < 0，
> 配置效应之和不再为零。strategy_return = Σ W_p_i·R_p_i 反映真实股票部分的净值贡献。

这是正确行为：若策略有 5% 现金仓位，这 5% 没有参与行业配置，配置效应反映的是"有偏权重"的效果。

---

### 4.3 `_compute_period_returns_t1_open` — F8-001 期间收益

```python
# L172–229
def _compute_period_returns_t1_open(
    T, T_next, codes, close_adj_panel, open_adj_panel,
) -> tuple[pd.Series, int]:
```

**收益计算口径**（F8-001）：

```python
first_date = in_period[0]   # T 之后第一个交易日（= T+1）

# 第一日：从 T+1 开盘到 T+1 收盘
first_ret = close_adj(T+1) / open_adj(T+1) - 1.0

# 后续日：收盘到收盘
remaining_rets = close_slice.pct_change()   # 收盘价序列的日收益率

# 累积
cum_prod = (1 + first_ret) × Π(1 + remaining_rets)
result = cum_prod - 1.0
```

**与回测引擎的对齐**：回测引擎的成交价格是 T+1 开盘价（`open_adj`），因此归因的期间收益也应从 T+1 开盘开始计量。若从 T 收盘开始（含隔夜段 T 收盘→T+1 开盘），则归因的收益口径与实际持仓成本不一致，会产生虚假的选股效应。

---

### 4.4 `summarize_by_segment` — 分段汇总

```python
# L503–552
def summarize_by_segment(
    period_summary: pd.DataFrame,
    segments: Optional[list[tuple[pd.Timestamp, pd.Timestamp]]] = None,
) -> pd.DataFrame:
```

`segments=None` 时按年度（`period_start.year`）自动分组。各效应简单相加（算术收益的可加性，月度尺度近似误差极小）。新增 `n_periods`（有效期数）和 `win_rate`（超额 > 0 的月份比例）。

---

## 五、内部辅助函数

| 函数 | 位置 | 说明 |
|------|------|------|
| `_get_period_list` | L58–88 | 构建 [(T, T_next), ...] 列表，包含 pre-start 锚点 |
| `_get_period_start_weights` | L91–121 | 取 T 之后第一个可用权重（F8-002 不归一化）|
| `_get_benchmark_weights` | L124–143 | 加载 T 日基准权重并归一化（和=1）|
| `_get_industry_map` | L146–169 | 从预加载的 industry_panel 取 T 日行业映射 |
| `_compute_period_returns_t1_open` | L172–229 | F8-001 T+1 开盘买入口径期间收益 |
| `_compute_period_brinson` | L232–300 | BHB 四效应向量化计算 |

---

## 六、落盘产物（Artifacts）

`brinson.py` 不直接写文件，由 `run_attribution_stage` 写入：

| 产物 | 路径 | 格式 | 内容 |
|------|------|------|------|
| `industry_attr.parquet` | `runs/.../attribution/industry_attr.parquet` | Parquet | MultiIndex=(period_start, industry_code)，4 种效应 |
| `period_summary.parquet` | `runs/.../attribution/period_summary.parquet` | Parquet | 每期汇总，含 cash_weight/weight_sum |

`period_summary` 中的关键列：

| 列 | 含义 |
|----|------|
| `strategy_return` | 股票部分净值贡献（不含现金，F8-002）|
| `benchmark_return` | 基准期间收益（含全量成分股，F8-003）|
| `excess_return` | strategy_return - benchmark_return |
| `cash_weight` | 1 - sum(w_p)，现金仓位（F8-002）|
| `total_effect` | 行业汇总总超额（应 ≈ excess_return）|

---

## 七、相关测试

**当前状态**：无专门的 `test_brinson.py`（TODO）。

**必须补充的测试**：

| 场景 | 要点 |
|------|------|
| BHB 恒等式 V1 | 构造已知 w_p/w_b/r，验证 strategy - benchmark ≈ total_effect |
| BHB 恒等式 V2 | alloc + select + interact ≈ total_effect（行业汇总层）|
| F8-002 现金仓位 | w_p 权重和 < 1 时 cash_weight = 1 - sum(w_p) > 0 |
| F8-001 第一日收益 | 验证用 close/open - 1 而非 close_to_close |
| 纯配置效应场景 | 策略和基准持有相同行业内股票（R_p=R_b），只有配置效应 |
| 纯选股效应场景 | 策略行业权重等于基准（W_p=W_b），只有选股效应 |

---

## 八、失败与降级路径

| 失败场景 | 行为 |
|---------|------|
| `periods` 为空（区间内无月度期间）| `raise ValueError` |
| T 不在 `index_member` 中 | `_get_benchmark_weights` 抛 `KeyError`，该期被 `log.warning` 后跳过 |
| T 不在 `industry_panel` 中 | 该期所有股票行业设为 `UNCLASSIFIED_CODE`，继续（有偏但不崩溃）|
| `actual_weights` 在 T 之后无权重日期 | `_get_period_start_weights` 返回空，该期跳过 |
| 所有期都归因失败 | `raise ValueError("所有期间均归因失败...")`|
| BHB 验证失败（V1/V2 偏差 > 1e-8）| `log.warning`，继续返回（调用方需检查日志）|

---

## 九、数据流图

```
BacktestResult.actual_weights (date × ts_code, 不归一化)
    │
    ▼
get_rebalance_dates() → all_dates
_get_period_list(all_dates, start, end) → [(T, T_next), ...]
    │
    ├── 一次性加载（F8-003）──────────────────────────────────────┐
    │   strategy_codes ∪ benchmark_codes → all_attr_codes         │
    │   load_daily_quote → close_adj_panel, open_adj_panel        │
    │   load_industry → industry_panel                            │
    └─────────────────────────────────────────────────────────────┘
    │
    ▼ 逐期处理
for T, T_next in periods:
    │
    ├── w_p = _get_period_start_weights(actual_weights, T)   ← T+1 实际持仓（F8-002）
    ├── w_b = _get_benchmark_weights(T)                       ← T 日基准权重（归一化）
    ├── industry_map = _get_industry_map(T, codes, panel)     ← T 日行业映射
    ├── r, n_miss = _compute_period_returns_t1_open(...)     ← T+1 开盘入场（F8-001）
    │
    └── _compute_period_brinson(w_p, w_b, r, industry_map)
            │
            ▼
        per-industry DataFrame：W_p / W_b / R_p / R_b /
        allocation / selection / interaction / total
    │
    ▼
concat(industry_rows) → industry_attr (MultiIndex)
DataFrame(period_rows) → period_summary
    │
    ▼
内部一致性验证（V1 + V2）
    │
    ▼
return (industry_attr, period_summary)
```

---

## 十、领域知识补充

### BHB 四效应的经济含义

**配置效应** = (W_p_i - W_b_i) × (R_b_i - R_b)：
> 将行业权重偏离基准，押注"好行业"（R_b_i > R_b）带来的超额。
> 若策略超配了涨幅好于基准的行业，配置效应为正。

**选股效应** = W_b_i × (R_p_i - R_b_i)：
> 在行业内部，策略持有的股票跑赢了基准同行业股票的收益。
> 这才是多因子选股策略的核心 alpha 来源。

**交叉效应** = (W_p_i - W_b_i) × (R_p_i - R_b_i)：
> 行业超配 × 行业内选股超额的交叉项。实践中交叉效应通常较小，有时被并入选股效应。

**总效应** = W_p_i × R_p_i - W_b_i × R_b_i：
> 当期第 i 行业的净超额贡献。三效应之和不等于总效应（单行业层），但**所有行业汇总后**恒等（V2 验证的内容）。

### BHB 可加性

BHB 模型基于算术收益（非对数收益），因此月度结果可以简单相加得到年度结果。这种线性可加性是 `summarize_by_segment` 的理论基础。

若使用对数收益（几何链接），归因结果无法简单相加，需要更复杂的"几何归因"框架（如 Bacon 模型）。本项目月度换手，误差可以接受。

### 金融股的特殊处理

`is_financial` 标记（L470–471）识别银行/保险/证券行业：
```python
industry_attr["is_financial"] = industry_codes.isin(cfg.FINANCIAL_SECTOR_CODES)
```
金融股的财务数据（ROE、净利润率等）与非金融股的同名指标有不同含义（如银行的"负债"是主营业务），多因子模型对金融股的预测能力通常较弱。`is_financial` 标记让下游分析可以单独观察金融/非金融板块的选股效应，判断超额来源是否依赖金融行业的配置。
