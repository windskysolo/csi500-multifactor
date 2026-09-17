# 19 — metrics.py：回测绩效指标计算

> 对应源文件：`src/backtest/metrics.py`
> 实际行数：**211 行**（计划快照为 152，新增 `calmar_ratio`、`monthly_win_rate` 等函数，撰写前已核实）
> 处理方式：**沿用 + 核查更新**
> 所属 Part：Part 6 — 回测与归因层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 5  回测    ← 本文件（metrics.py）
Layer 5  回测    engine.py（调用 summarize）
Layer 5  回测    metrics.py
```

`metrics.py` 是纯计算模块，**不访问任何外部数据**，只接受 `pd.Series`（NAV 序列），输出 `float` 指标或汇总 `dict`。

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游 | `engine.run_backtest` | 传入 `nav_aligned, bench_aligned` |
| 下游 | `pipeline/stages.run_backtest_stage` | 从 `metrics.json` 读取指标，决定 PASS/FAIL |
| 下游 | `pipeline/compare.py` | 从各 run 的 `metrics.json` 汇总比较表 |

---

## 二、时间对齐与 PIT 假设

`metrics.py` **无 PIT 风险**，所有函数只做数学计算：
- 输入：已归一化的 NAV 序列（`engine.py` 保证 `inner` 对齐，起点=1.0）
- 输出：标量指标

**关键约定**：

| 约定 | 说明 |
|------|------|
| `max_drawdown` 返回**负数** | `-0.15` = 最大回撤 15%；展示时取绝对值 |
| `excess_max_drawdown` 同上 | 超额净值的最大回撤，也返回负数 |
| 年化基准 | `TRADING_DAYS_PER_YEAR = 252` |
| 无风险利率 | `rf_annual = 0.02`（Sharpe 的默认值）|

---

## 三、模块顶部：导入与常量

```python
from __future__ import annotations
import numpy as np
import pandas as pd
TRADING_DAYS_PER_YEAR = 252
```

模块级常量只有一个：`TRADING_DAYS_PER_YEAR`。

---

## 四、核心函数逐一解析

### 4.1 `daily_returns` — 日收益率

```python
# L18–27
def daily_returns(nav: pd.Series) -> pd.Series:
    return nav.pct_change()
```

返回序列与 `nav` 等长，首元素为 NaN（`pct_change` 产生）。下游函数通过 `.dropna()` 或 `skipna=True` 处理首元素 NaN。

---

### 4.2 `annualized_return` — 年化收益率（几何复利）

```python
# L30–40
def annualized_return(nav: pd.Series) -> float:
    n_years = len(nav) / TRADING_DAYS_PER_YEAR
    return float((nav.iloc[-1] / nav.iloc[0]) ** (1 / n_years) - 1)
```

**从累计净值反推年化**，不用算术均值近似。这保证了"年化收益率"的几何复利含义：

$$r_{ann} = \left(\frac{NAV_{T}}{NAV_0}\right)^{252/T} - 1$$

注意：`nav.iloc[0]` 通常等于 1.0（`engine.py` 保证 `inner` 对齐后起点归一化），但函数本身不假设这一点。

---

### 4.3 `annualized_vol` — 年化波动率

```python
# L43–52
def annualized_vol(nav: pd.Series) -> float:
    return float(daily_returns(nav).std() * np.sqrt(TRADING_DAYS_PER_YEAR))
```

日收益率标准差乘以 √252（Delta 法，标准做法）。

---

### 4.4 `sharpe` — 夏普比率

```python
# L55–70
def sharpe(nav: pd.Series, rf_annual: float = 0.02) -> float:
    daily_rf = rf_annual / TRADING_DAYS_PER_YEAR
    excess_daily = daily_returns(nav).dropna() - daily_rf
    std = excess_daily.std()
    if std < 1e-10: return 0.0
    return float(excess_daily.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))
```

Sharpe = 年化超额收益 / 年化波动，RF=2%（默认）。当策略净值不动时标准差=0，返回 0 而非无穷大。

---

### 4.5 `max_drawdown` — 最大回撤

```python
# L73–84
def max_drawdown(nav: pd.Series) -> float:
    rolling_max = nav.cummax()
    drawdown = (nav - rolling_max) / rolling_max
    return float(drawdown.min())
```

返回**负数**（如 -0.15 表示 15% 最大回撤）。实现是标准的"当前价格相对历史最高点的跌幅"。

---

### 4.6 `tracking_error` — 跟踪误差

```python
# L87–99
def tracking_error(nav: pd.Series, bench_nav: pd.Series) -> float:
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    excess = daily_returns(nav_aligned) - daily_returns(bench_aligned)
    return float(excess.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
```

**事后实测 TE**（ex-post），不是优化器的 TE 目标（ex-ante）。两者的差异反映模型估计误差。

---

### 4.7 `information_ratio` — 信息比率（IR）

```python
# L102–117
def information_ratio(nav: pd.Series, bench_nav: pd.Series) -> float:
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    excess_ann = annualized_return(nav_aligned) - annualized_return(bench_aligned)
    te = tracking_error(nav_aligned, bench_aligned)
    if te < 1e-10: return 0.0
    return float(excess_ann / te)
```

$$IR = \frac{\text{年化超额收益}}{\text{年化跟踪误差}}$$

这是项目的**核心评估指标**（完成标准：IR ≥ 0.5）。注意：IR 不是唯一指标，`summarize()` 强制计算所有六项必报指标。

---

### 4.8 `excess_nav` — 超额净值曲线

```python
# L120–134
def excess_nav(nav: pd.Series, bench_nav: pd.Series) -> pd.Series:
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    return nav_aligned / bench_aligned
```

超额净值 = 策略 NAV / 基准 NAV，起点约为 1.0（两者同时归一化时精确等于 1.0）。

---

### 4.9 `excess_max_drawdown` — 超额最大回撤

```python
# L137–147
def excess_max_drawdown(nav: pd.Series, bench_nav: pd.Series) -> float:
    return max_drawdown(excess_nav(nav, bench_nav))
```

超额净值的最大回撤，返回负数。项目完成标准：`|excess_max_drawdown| ≤ 10%`（即 `excess_max_drawdown ≥ -0.10`）。

---

### 4.10 `monthly_win_rate` — 月度胜率

```python
# L150–166
def monthly_win_rate(nav: pd.Series, bench_nav: pd.Series) -> float:
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    monthly_ret = nav_aligned.resample("ME").last().pct_change().dropna()
    monthly_bench_ret = bench_aligned.resample("ME").last().pct_change().dropna()
    monthly_ret, monthly_bench_ret = monthly_ret.align(monthly_bench_ret, join="inner")
    excess = monthly_ret - monthly_bench_ret
    return float((excess > 0).mean())
```

`resample("ME").last()`：取每月最后一个交易日的 NAV，再计算月度收益率。`dropna()` 去除序列首元素（pct_change 产生的 NaN）。

**二次对齐**（L164）：月度 pct_change 可能因端点不完全相同而产生轻微不对齐，故再做一次 `inner` 对齐。

---

### 4.11 `calmar_ratio` — 卡玛比率

```python
# L169–182
def calmar_ratio(nav: pd.Series) -> float:
    ann_ret = annualized_return(nav)
    mdd = max_drawdown(nav)
    if abs(mdd) < 1e-10: return 0.0
    return float(ann_ret / abs(mdd))
```

卡玛比率 = 年化收益 / |最大回撤|，衡量每单位最大损失能获得的年化回报。

---

### 4.12 `summarize` — 汇总所有指标

```python
# L185–210
def summarize(nav: pd.Series, bench_nav: pd.Series, rf_annual: float = 0.02) -> dict:
```

**返回字典的所有键**：

| 键 | 函数 | 符号 |
|----|------|------|
| `annualized_return` | `annualized_return(nav)` | 策略年化收益 |
| `annualized_vol` | `annualized_vol(nav)` | 策略年化波动 |
| `sharpe` | `sharpe(nav, rf_annual)` | 夏普比率 |
| `max_drawdown` | `max_drawdown(nav)` | 策略最大回撤（负数）|
| `calmar_ratio` | `calmar_ratio(nav)` | 卡玛比率 |
| `benchmark_return` | `annualized_return(bench)` | 基准年化收益 |
| `excess_return` | `ann_ret(nav) - ann_ret(bench)` | 年化超额收益 |
| `tracking_error` | `tracking_error(nav, bench)` | 跟踪误差 |
| `information_ratio` | `information_ratio(nav, bench)` | 信息比率（IR）|
| `excess_max_drawdown` | `excess_max_drawdown(nav, bench)` | 超额最大回撤（负数）|
| `monthly_win_rate` | `monthly_win_rate(nav, bench)` | 月度胜率 |

这 11 个指标中，以下 6 个是项目**必报指标**（CLAUDE.md §6.3 规定）：
`information_ratio`、`excess_return`、`excess_max_drawdown`、`tracking_error`、`monthly_win_rate`、（换手率由 `portfolio/metadata_df` 提供）

---

## 五、内部辅助函数

无私有辅助函数，所有函数均为 public。

---

## 六、落盘产物（Artifacts）

`metrics.py` 不直接写文件。`summarize()` 返回 dict，由 `run_backtest_stage` 写入 `metrics.json`：

```json
{
  "annualized_return": 0.156,
  "annualized_vol": 0.234,
  "sharpe": 0.614,
  "max_drawdown": -0.234,
  "calmar_ratio": 0.666,
  "benchmark_return": 0.089,
  "excess_return": 0.067,
  "tracking_error": 0.071,
  "information_ratio": 0.943,
  "excess_max_drawdown": -0.082,
  "monthly_win_rate": 0.583
}
```

---

## 七、相关测试

**当前状态**：无专门的 `test_metrics.py`（TODO）。

**必须补充的测试**：

| 测试场景 | 要点 |
|---------|------|
| NAV 单调递增 | `max_drawdown = 0.0`，`monthly_win_rate = 1.0` |
| NAV 等于基准 | `information_ratio = 0.0`，`excess_max_drawdown = 0.0` |
| 极端回撤 | NAV 从 1.0 跌至 0.5 后恢复，`max_drawdown = -0.5` |
| 月度胜率计算 | 手动构造 12 个月数据，验证胜率计算 |
| `max_drawdown` 返回负数 | 验证约定（调用方取 abs 展示）|
| 年化收益的几何复利 | 两年净值翻倍：`annualized_return ≈ 0.414`（√2 - 1）|

---

## 八、失败与降级路径

| 失败场景 | 行为 |
|---------|------|
| `nav` 长度 < 2 | `pct_change()` 返回 [NaN]，后续 `.std()` 为 NaN |
| `tracking_error = 0`（完美跟踪）| `information_ratio` 返回 0.0（不抛 ZeroDivisionError）|
| `max_drawdown = 0`（无回撤）| `calmar_ratio` 返回 0.0 |
| `nav` 与 `bench_nav` 日期不对齐 | 各函数内部用 `inner` 对齐，自动处理 |

---

## 九、数据流图

```
nav (pd.Series, 日频, 起点≈1.0)
bench_nav (pd.Series, 日频, 起点≈1.0)
    │
    ▼
nav.align(bench_nav, join="inner")
    → nav_aligned, bench_aligned（同日期范围）
    │
    ├──▶ annualized_return(nav)         → float
    ├──▶ annualized_vol(nav)            → float
    ├──▶ sharpe(nav)                    → float
    ├──▶ max_drawdown(nav)              → float（负数）
    ├──▶ calmar_ratio(nav)              → float
    ├──▶ annualized_return(bench)       → float
    ├──▶ excess_return = ann_ret(nav) - ann_ret(bench)
    ├──▶ tracking_error(nav, bench)     → float
    ├──▶ information_ratio(nav, bench)  → float
    ├──▶ excess_max_drawdown(nav, bench)→ float（负数）
    └──▶ monthly_win_rate(nav, bench)   → float [0,1]
    │
    ▼
summarize → dict（11 项指标）
    │
    ▼
run_backtest_stage 写入 metrics.json
    │
    ▼
compare.py 读取，汇总到 experiment_board.md
```

---

## 十、领域知识补充

### 为什么必须同时报告 6 项指标

CLAUDE.md §6.3 明确要求**禁止只写 IR**，原因如下：

| 单独 IR 的盲区 | 需要哪项指标揭示 |
|---------------|----------------|
| IR 高但年化超额很小（如 0.1%）| `excess_return`（绝对 alpha 水平）|
| IR 高但某段连续亏损严重 | `excess_max_drawdown`（极端下行）|
| IR 相同但 TE 差异巨大 | `tracking_error`（风险预算使用量）|
| IR 高但月胜率只有 40% | `monthly_win_rate`（稳定性）|
| IR 高但换手 2000% 成本吃掉 alpha | 换手率（从 `metadata_df` 读取）|

一个好的策略需要在六项指标上综合合格，而不是单一 IR 最大化。

### 事后 TE vs 事前 TE 目标

| 类型 | 含义 | 来源 |
|------|------|------|
| **事前 TE**（ex-ante）| 优化器使用的 TE 约束目标 | `OptimizeConfig.te_target_annual`（来自 `config.py`）|
| **事后 TE**（ex-post）| 回测结束后实测的跟踪误差 | `tracking_error()` 函数 |

两者差异的来源：
1. 优化器使用的协方差矩阵基于历史数据，与未来实际波动不完全一致
2. 停牌/涨跌停约束导致实际持仓偏离目标权重
3. T+1 成交滑点改变了实际权重

TopN EW 模式没有 ex-ante TE 约束，其事后 TE 由持仓集中度和市场波动决定。通常比 QP 的 TE 更高（因为无 TE 约束），但 IR 更好（因为选股更鲁棒）。

### 月度胜率的统计意义

月度胜率 = 超额收益为正的月份比例。

对于 IR ≈ 0.9 的策略，期望月度胜率约为 55-65%（IR 越高胜率越高，但非线性关系）。月度胜率 < 50% 通常是问题信号——即便年化超额为正，也意味着超额主要集中在少数几个月，策略稳定性差，实盘跟踪难度大。
