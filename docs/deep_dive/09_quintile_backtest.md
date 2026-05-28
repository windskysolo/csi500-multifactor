# 09 — 分组回测：quintile_backtest.py 逐行精讲

> 对应源文件：`src/evaluation/quintile_backtest.py`（253 行）  
> 前置依赖：`08_ic_analysis.md`（forward return 的生成方式）  
> 核心任务：把因子从小到大切成 5 组，验证"高分组跑赢低分组"的单调性

---

## 一、模块定位与设计哲学

### 1.1 分组回测 vs IC 分析

IC 分析（08 节）告诉你：因子值与下期收益的**相关性**有多强。  
分组回测告诉你：如果把股票按因子值平均分成 5 份，各组的**实际收益率**走势是否单调。

这两者互补：
- IC 是线性相关的全局统计量，对异常值敏感
- 分组回测直接展示收益分布形态，是非参数方法，更直观

### 1.2 "等权 + 不含成本"的局限性

模块开头的 docstring 明确声明了三条局限：

```
- 不含交易成本（仅用于验证因子方向性，非精确回测）
- 等权假设忽略了实际组合的市值约束和流动性限制
- 若某组内样本数过少（< 5），该组收益置 NaN
```

这不是 bug，是设计取舍：**单因子分组回测的目的是验证预测方向性，不是模拟真实交易**。真实交易要考虑市值权重、流动性、成本，那是 `backtest/` 模块的职责。

---

## 二、常量与工具函数

### 2.1 常量定义（第 30-31 行）

```python
MIN_STOCKS_PER_GROUP = 5
DIRECTIONAL_LS_LABEL = "DirectionalLS"
```

`MIN_STOCKS_PER_GROUP = 5`：每组至少 5 只股票。这是保守下限——低于 5 只时，单只股票的异常波动就能主导组收益，统计无意义。

`DIRECTIONAL_LS_LABEL`：方向调整后的多空列名，提取为常量防止拼写错误。

### 2.2 `_normalize_direction`（第 34-39 行）

```python
def _normalize_direction(direction: Optional[float]) -> float:
    if direction is None or pd.isna(direction):
        return np.nan
    signed = float(np.sign(direction))
    return signed if signed != 0.0 else np.nan
```

**输入**：任意数值（正数、负数、0、None、NaN）  
**输出**：+1、-1、NaN 三种取值

核心是 `np.sign(direction)`，把原始数值映射到 {-1, 0, +1}。0 被视为 NaN（方向未知），因为方向为 0 没有实际意义。

**为什么需要归一化？** 调用方可能传入原始 IC_IR 值（如 0.45 或 -0.23），也可能传入明确的 +1/-1 标志。归一化确保后续乘法 `direction × (Q5-Q1)` 只用符号，不受数值大小影响。

### 2.3 `_annualized_sharpe`（第 42-47 行）

```python
def _annualized_sharpe(ret: pd.Series) -> float:
    valid = ret.dropna()
    if len(valid) > 1 and valid.std(ddof=1) > 0:
        return float(valid.mean() / valid.std(ddof=1) * np.sqrt(12))
    return np.nan
```

月频 Sharpe 年化公式：`Sharpe_annual = Sharpe_monthly × √12`

注意：
- `ddof=1`：样本标准差，自由度校正
- 分母为 0（收益完全固定）返回 NaN，不崩溃
- 假设无风险利率 ≈ 0（短期利率未纳入）

---

## 三、核心分组逻辑：`_assign_quintiles`

```python
def _assign_quintiles(factor: pd.Series, n_groups: int = 5) -> pd.Series:
    valid = factor.dropna()
    if len(valid) < n_groups * MIN_STOCKS_PER_GROUP:
        return pd.Series(dtype=int)

    try:
        labels = pd.qcut(valid, q=n_groups, labels=False, duplicates="drop") + 1
    except ValueError:
        return pd.Series(dtype=int)

    if labels.nunique() < n_groups:
        log.debug("因重复值过多，实际分组数 %d < %d", labels.nunique(), n_groups)
        return pd.Series(dtype=int)

    return labels.astype(int)
```

### 3.1 前置检查：总股票数

```python
if len(valid) < n_groups * MIN_STOCKS_PER_GROUP:
    return pd.Series(dtype=int)
```

5 组 × 5 只 = 25 只。如果整个截面有效股票少于 25 只，分组无意义，直接返回空 Series。

### 3.2 `pd.qcut` 等频分组

```python
labels = pd.qcut(valid, q=n_groups, labels=False, duplicates="drop") + 1
```

`pd.qcut` 按**分位数**分组，保证每组股票数量大致相等（等频，不是等宽）。

参数说明：
- `q=n_groups`：分成几组（此处 5）
- `labels=False`：返回 0-based 整数标签，而非区间字符串
- `duplicates="drop"`：当大量股票因子值完全相同时（例如停牌后的填充值），bin 边界重叠，`pd.qcut` 会合并相同边界，实际分组数减少

`+ 1`：把 0-based 改为 1-based（Q1 到 Q5）。

### 3.3 重复值过多的检测

```python
if labels.nunique() < n_groups:
    return pd.Series(dtype=int)
```

`duplicates="drop"` 会减少 bin 数量。如果最终 bin 数 < 5，返回空 Series。这是第二道防线——第一道是 try/except，第二道是事后检查。

**为什么需要两道防线？**  
`pd.qcut` 在某些情况下不抛异常，但悄悄减少了分组数。事后检查 `nunique()` 能捕捉这种静默失败。

### 3.4 返回值结构

成功时返回以 `ts_code` 为 index 的整数 Series，取值 1-5，对应 Q1-Q5。

---

## 四、单截面分组收益：`compute_quintile_returns`

```python
def compute_quintile_returns(
    factor: pd.Series,
    fwd_ret: pd.Series,
    n_groups: int = 5,
) -> pd.Series:
    labels = _assign_quintiles(factor, n_groups)
    if labels.empty:
        return pd.Series(dtype=float)

    common = labels.index.intersection(fwd_ret.dropna().index)
    if len(common) < n_groups * MIN_STOCKS_PER_GROUP:
        return pd.Series(dtype=float)

    group_ret = fwd_ret[common].groupby(labels[common]).mean()

    # 逐组检查每组实际样本数
    group_sizes = labels[common].value_counts()
    small_groups = group_sizes[group_sizes < MIN_STOCKS_PER_GROUP].index
    if not small_groups.empty:
        group_ret[small_groups] = np.nan

    return group_ret
```

### 4.1 关键细节：取交集后再检查每组

```python
common = labels.index.intersection(fwd_ret.dropna().index)
```

因子分组用全截面因子值，但 forward return 可能因停牌等原因缺失。取交集后，**每组的实际样本数可能比分组时少**。

例如：Q3 有 20 只股票，但其中 18 只下期停牌无 forward return，最终 Q3 只有 2 只股票的 fwd_ret，远低于 MIN_STOCKS_PER_GROUP=5，此组收益不可信。

```python
group_sizes = labels[common].value_counts()
small_groups = group_sizes[group_sizes < MIN_STOCKS_PER_GROUP].index
if not small_groups.empty:
    group_ret[small_groups] = np.nan
```

**关键点**：这是在取交集之后再检查，而不是只检查原始因子的分组大小。这两次检查解决两个不同问题：
1. 第一次（总量 < 25）：整个截面样本太少
2. 第二次（某组 < 5）：某组与 fwd_ret 交集后样本太少

### 4.2 等权平均

```python
group_ret = fwd_ret[common].groupby(labels[common]).mean()
```

等权（mean）不是市值权重，这是设计选择。实际回测才用市值权重或优化器权重。

---

## 五、全周期回测：`run_quintile_backtest`

```python
def run_quintile_backtest(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    n_groups: int = 5,
    direction: Optional[float] = None,
) -> dict:
```

### 5.1 逐日遍历

```python
dates = factor_panel.index.intersection(fwd_ret_panel.index)
rows: list[pd.Series] = []
for d in dates:
    qrets = compute_quintile_returns(factor_panel.loc[d], fwd_ret_panel.loc[d], n_groups)
    if qrets.empty or len(qrets) < n_groups:
        continue
    row = qrets.rename(lambda x: f"Q{x}")
    row[ls_label] = row[f"Q{n_groups}"] - row["Q1"]
    if np.isfinite(factor_direction):
        row[DIRECTIONAL_LS_LABEL] = factor_direction * row[ls_label]
    else:
        row[DIRECTIONAL_LS_LABEL] = np.nan
    row.name = d
    rows.append(row)
```

每个调仓日独立计算，不跨期混合，逻辑清晰但效率低于向量化（在月频策略中可接受）。

### 5.2 Q5-Q1 与 DirectionalLS 的区别

**Q5-Q1**（`ls_label = "Q5-Q1"`）：原始多空组合，始终等于高分组减低分组。

**DirectionalLS**：`direction × (Q5-Q1)`

| direction | 因子含义 | Q5-Q1 | DirectionalLS |
|-----------|---------|-------|--------------|
| +1 | 因子正向（高分=预测好） | Q5-Q1 | 同 Q5-Q1 |
| -1 | 因子负向（低分=预测好，如波动率） | Q5-Q1（此时为负） | -(Q5-Q1)，变正 |

引入 DirectionalLS 后，正负向因子的多空收益都是正数，便于统一比较 Sharpe。

### 5.3 年化计算方式

```python
mean_rets = group_rets.mean()
ann_rets  = mean_rets * 12   # 月频近似年化（不复利）
cum_rets  = (1 + group_rets).cumprod()
```

`ann_rets = mean_rets * 12`：**简单年化**，不是复利年化。

对比：
- 简单年化：`12 × μ_monthly`
- 复利年化：`(1 + μ_monthly)^12 - 1`

月均 1% 时，简单年化 = 12%，复利年化 = 12.68%。简单年化低估了实际收益，更保守，符合"风险提示优于结果展示"原则。

`cum_rets = (1 + group_rets).cumprod()`：每期乘以 `(1 + r)`，以 1 为基准的净值曲线，这是正确的复利累乘。

**注意区别**：`ann_rets` 用简单乘法，`cum_rets` 用复利乘积，两者用途不同，不矛盾。

### 5.4 输出 dict 结构

```python
return {
    "group_rets": group_rets,   # DataFrame, 行=日期, 列=Q1-Q5+Q5-Q1+DirectionalLS
    "mean_rets":  mean_rets,    # Series, 各组期均收益
    "ann_rets":   ann_rets,     # Series, 各组年化收益（月均×12）
    "cum_rets":   cum_rets,     # DataFrame, 累计净值曲线
    "sharpe_ls":  sharpe_ls,    # float, Q5-Q1 原始多空 Sharpe
    "directional_sharpe_ls": directional_sharpe_ls,  # float, 方向调整后 Sharpe
    "factor_direction": factor_direction,   # +1/-1/NaN
    "n_periods":  len(group_rets),          # 有效期数
}
```

---

## 六、批量汇总：`batch_quintile_summary`

```python
def batch_quintile_summary(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    n_groups: int = 5,
    factor_directions: Optional[dict[str, float] | pd.Series] = None,
) -> pd.DataFrame:
```

### 6.1 方向自动推断

```python
def _direction_for(name: str, panel: pd.DataFrame) -> float:
    if factor_directions is not None:
        ...  # 从传入的 dict/Series 查找
        return _normalize_direction(raw_direction)

    from src.evaluation.ic_analysis import compute_ic_series
    ic_series = compute_ic_series(panel, fwd_ret_panel)
    return _normalize_direction(ic_series.mean() if len(ic_series) else np.nan)
```

若调用方提供了 `factor_directions`，直接查字典；否则**自动计算 IC 均值的符号**作为因子方向。

这个设计允许快速调用（不需要提前计算 IC），但代价是在 `batch_quintile_summary` 内部额外做了 IC 计算，可能有重复计算。

### 6.2 排序规则

```python
return pd.DataFrame(rows).set_index("factor").sort_values(
    "directional_sharpe_ls", ascending=False, na_position="last"
)
```

按 `directional_sharpe_ls` **降序**排列，最强因子在最前。NaN 排最后（没有方向信息的因子）。

---

## 七、完整数据流图

```
factor_panel (T × N)
fwd_ret_panel (T × N)
        │
        ▼ for each date d
┌─────────────────────────────────────┐
│  factor_panel.loc[d]  →  Series(N)  │
│  _assign_quintiles()                │
│    pd.qcut(q=5, duplicates="drop")  │
│    check: nunique() < 5 → skip      │
│    returns: labels Series(N) 1~5    │
│                                     │
│  fwd_ret_panel.loc[d] →  Series(N)  │
│    取交集 common = (labels ∩ fwd_ret)│
│    check: len(common) < 25 → skip   │
│                                     │
│  groupby(labels).mean()             │
│    check: each group size ≥ 5       │
│    small groups → NaN               │
│                                     │
│  row = {Q1, Q2, Q3, Q4, Q5,        │
│         Q5-Q1, DirectionalLS}       │
└─────────────────────────────────────┘
        │ rows 列表
        ▼
group_rets = pd.DataFrame(rows)   (T_valid × 7)
mean_rets  = group_rets.mean()
ann_rets   = mean_rets * 12
cum_rets   = (1+group_rets).cumprod()
sharpe_ls  = mean/std * sqrt(12)
```

---

## 八、常见误区与排查

### 误区 1：Q5 永远是"好组"

`Q5` 是**因子值最高**的组，不一定是预测最好的组。  
- 对正向因子（如 ROE）：Q5 = 高 ROE = 预期好 → Q5 应跑赢
- 对负向因子（如 vol_60d）：Q5 = 高波动 = 预期差 → Q1 应跑赢

这就是 `DirectionalLS` 存在的原因：对负向因子，`direction = -1`，DirectionalLS = -(Q5-Q1) = Q1-Q5，反转多空方向。

### 误区 2：`ann_rets` 和 `cum_rets` 应该一致

两者用不同方法计算，本来就有差异：
- `ann_rets` = 月均 × 12（简单算术）
- `cum_rets` 末值 - 1 = 复利增长总收益

年化收益用哪个？取决于用途：比较因子强弱用简单年化（更保守）；汇报绝对收益用复利净值。

### 误区 3：分组 Sharpe 高 = 因子好

分组回测的 Sharpe 不含成本。加上 5-10 bps 滑点 + 5-10 bps 印花税 + 佣金后，实际 Sharpe 可能大幅下降。分组回测只做方向性验证，不能直接用于评估实盘效果。

---

## 九、与其他模块的协作关系

| 上游 | 提供 |
|------|------|
| `ic_analysis.compute_forward_returns` | `fwd_ret_panel`（T+1 开盘买入，T'+1 开盘卖出） |
| `factors/` 模块 | `factor_panel`（预处理后的因子面板） |
| `ic_analysis.compute_ic_series` | 方向自动推断时被内部调用 |

| 下游 | 用途 |
|------|------|
| `scripts/run_factor_evaluation.py` | 批量执行，写出 `reports/factor_evaluation/quintile_summary.csv` |
| 可视化模块 | `cum_rets` 绘制分组净值曲线 |
