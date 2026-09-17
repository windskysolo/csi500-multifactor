# 16 — 回测绩效指标：metrics.py 逐行精讲

> 对应源文件：`src/backtest/metrics.py`（211 行）  
> 前置依赖：无（纯函数，只接受 pd.Series）  
> 核心任务：把 NAV 序列转换为夏普、最大回撤、信息比率等标准绩效指标

---

## 一、模块设计原则

所有函数只接受 `pd.Series`（`index=trade_date, values=float`），不依赖任何外部数据，纯函数，易于测试。

两个约定：
1. `nav` 和 `bench_nav` 均以起点 = 1.0 归一化（由 engine 保证）
2. `max_drawdown` / `excess_max_drawdown` 返回**负数**（-0.15 = 回撤 15%），展示时取绝对值

---

## 二、基础收益函数

### 2.1 `daily_returns`（第 18-26 行）

```python
def daily_returns(nav: pd.Series) -> pd.Series:
    return nav.pct_change()
```

第一个值为 NaN（`pct_change` 的起始点无法计算）。下游函数用 `dropna()` 或 `skipna=True` 处理。

### 2.2 `annualized_return`（第 29-40 行）

```python
def annualized_return(nav: pd.Series) -> float:
    n_years = len(nav) / TRADING_DAYS_PER_YEAR  # 252
    return float((nav.iloc[-1] / nav.iloc[0]) ** (1 / n_years) - 1)
```

**几何复利年化**，不是算术均值 × 252 的近似。  
公式：`(终值/起值)^(1/年数) - 1`

区别：
- 若月均 1%，算术近似 = 12%，几何年化 ≈ 12.68%
- 本函数用几何年化，更精确

注意 `nav.iloc[0]` 是起始净值（= 1.0），所以 `nav.iloc[-1] / nav.iloc[0] = nav.iloc[-1]`，但写成比值形式更语义清晰，且兼容起始值不是 1.0 的情况。

### 2.3 `annualized_vol`（第 43-52 行）

```python
def annualized_vol(nav: pd.Series) -> float:
    return float(daily_returns(nav).std() * np.sqrt(TRADING_DAYS_PER_YEAR))
```

**日收益率标准差 × √252**，这是标准的波动率年化公式，假设日收益率 i.i.d.（独立同分布）。

---

## 三、夏普比率（第 55-70 行）

```python
def sharpe(nav: pd.Series, rf_annual: float = 0.02) -> float:
    daily_rf = rf_annual / TRADING_DAYS_PER_YEAR
    excess_daily = daily_returns(nav).dropna() - daily_rf
    std = excess_daily.std()
    if std < 1e-10:
        return 0.0
    return float(excess_daily.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))
```

夏普 = `(E[r] - rf) / σ(r)`，年化形式 = `月夏普 × √12`（日频则 × √252）。

`rf_annual = 0.02`（2% 年化无风险利率）。A 股常用 3-4%，这里取 2% 是保守选择（更难达标）。

`daily_rf = rf_annual / 252`：把年化无风险率日化，用于每日超额收益的计算。

---

## 四、最大回撤（第 73-84 行）

```python
def max_drawdown(nav: pd.Series) -> float:
    rolling_max = nav.cummax()
    drawdown = (nav - rolling_max) / rolling_max
    return float(drawdown.min())
```

**算法**：
1. `cummax()`：截止每个时点的历史最高净值（running maximum）
2. `(nav - rolling_max) / rolling_max`：每日相对于历史高点的跌幅（≤ 0）
3. `min()`：取最大跌幅（最大负数）

返回负数：`-0.15` = 最大回撤 15%，展示时 `abs(max_drawdown(nav))` 得 0.15。

---

## 五、跟踪误差与信息比率

### 5.1 `tracking_error`（第 87-99 行）

```python
def tracking_error(nav: pd.Series, bench_nav: pd.Series) -> float:
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    excess = daily_returns(nav_aligned) - daily_returns(bench_aligned)
    return float(excess.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
```

**跟踪误差** = 超额日收益率的年化标准差（事后实测 TE）。

`nav.align(bench_nav, join="inner")`：取两者日期的交集，防止因 NAV 起止日期不同导致计算错误。

这是**事后 TE**，与优化器里的**事前 TE 约束**（基于协方差矩阵预测）不同。两者通常相差不多，但事后值是实际发生的，更准确。

### 5.2 `information_ratio`（第 102-117 行）

```python
def information_ratio(nav: pd.Series, bench_nav: pd.Series) -> float:
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    excess_ann = annualized_return(nav_aligned) - annualized_return(bench_aligned)
    te = tracking_error(nav_aligned, bench_aligned)
    if te < 1e-10:
        return 0.0
    return float(excess_ann / te)
```

**信息比率** = `年化超额收益 / 跟踪误差`

注意：分子用的是**年化几何超额**（两个年化收益相减），分母是**年化 TE**（标准差）。

项目目标：IR ≥ 0.5。这意味着跟踪误差为 5% 时，年化超额需 ≥ 2.5%。

---

## 六、超额净值（第 120-134 行）

```python
def excess_nav(nav: pd.Series, bench_nav: pd.Series) -> pd.Series:
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    return nav_aligned / bench_aligned
```

超额净值 = 策略净值 / 基准净值，起点 = 1.0（因为两者都归一化为 1.0）。

超额净值上升 = 策略跑赢基准；下降 = 跑输。画出这条曲线，一眼看出超额收益的积累过程。

---

## 七、月胜率（第 150-166 行）

```python
def monthly_win_rate(nav: pd.Series, bench_nav: pd.Series) -> float:
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    monthly_ret = nav_aligned.resample("ME").last().pct_change().dropna()
    monthly_bench_ret = bench_aligned.resample("ME").last().pct_change().dropna()
    monthly_ret, monthly_bench_ret = monthly_ret.align(monthly_bench_ret, join="inner")
    excess = monthly_ret - monthly_bench_ret
    return float((excess > 0).mean())
```

**月胜率** = 策略月收益高于基准的月份比例。

`resample("ME").last()`：取每月最后一个交易日的净值（月末），再 `pct_change()` 得月度收益率。

注意两次 `align`：
1. 第一次：策略和基准日频对齐
2. 第二次：月频重采样后再次对齐（月末日期可能因节假日不完全一致）

---

## 八、卡玛比率（第 169-182 行）

```python
def calmar_ratio(nav: pd.Series) -> float:
    ann_ret = annualized_return(nav)
    mdd = max_drawdown(nav)
    if abs(mdd) < 1e-10:
        return 0.0
    return float(ann_ret / abs(mdd))
```

**卡玛比率** = `年化收益 / |最大回撤|`，衡量承受单位最大损失获得的年化回报。

目标：卡玛 > 1 说明年化收益高于最大回撤，策略总体稳健。

---

## 九、汇总函数（第 185-210 行）

```python
def summarize(nav: pd.Series, bench_nav: pd.Series, rf_annual: float = 0.02) -> dict:
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    return {
        "annualized_return":   annualized_return(nav_aligned),
        "annualized_vol":      annualized_vol(nav_aligned),
        "sharpe":              sharpe(nav_aligned, rf_annual),
        "max_drawdown":        max_drawdown(nav_aligned),
        "calmar_ratio":        calmar_ratio(nav_aligned),
        "benchmark_return":    annualized_return(bench_aligned),
        "excess_return":       annualized_return(nav_aligned) - annualized_return(bench_aligned),
        "tracking_error":      tracking_error(nav_aligned, bench_aligned),
        "information_ratio":   information_ratio(nav_aligned, bench_aligned),
        "excess_max_drawdown": excess_max_drawdown(nav_aligned, bench_aligned),
        "monthly_win_rate":    monthly_win_rate(nav_aligned, bench_aligned),
    }
```

先做一次 `align`，避免后续每个函数内部重复对齐（性能优化）。

**项目完成标准对照**：

| 指标 | 目标 | 对应字段 |
|------|------|---------|
| IR ≥ 0.5 | `information_ratio >= 0.5` | `"information_ratio"` |
| 超额最大回撤 ≤ 10% | `abs(excess_max_drawdown) <= 0.10` | `"excess_max_drawdown"` |

---

## 十、使用注意事项

`max_drawdown` 和 `excess_max_drawdown` 返回负数：
```python
mdd = metrics["max_drawdown"]   # 例如 -0.23
print(f"最大回撤: {abs(mdd)*100:.1f}%")  # 应用 abs
```

`excess_return` 是两个几何年化收益相减，不是几何超额（严格讲，几何超额应为 `(1+r_p)/(1+r_b) - 1`），对于年化 < 20% 的情形两者相差极小，算术相减是工程上合理的近似。
