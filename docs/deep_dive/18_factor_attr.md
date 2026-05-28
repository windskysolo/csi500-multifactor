# 18 — 因子收益归因：factor_attr.py 逐行精讲

> 对应源文件：`src/attribution/factor_attr.py`（783 行）  
> 前置依赖：`17_brinson.md`（期间划分和权重逻辑），`11_combiner.md`（因子组定义）  
> 核心任务：用 WLS 回归把每月超额收益拆解为 7 个因子组的贡献与残差

---

## 一、与 BHB 归因的区别

| 维度 | BHB（行业归因） | 因子归因 |
|------|---------------|---------|
| 问题 | 超额收益来自哪个行业？ | 超额收益来自哪个因子暴露？ |
| 方法 | 权重 × 收益分解 | 横截面 WLS 回归 |
| 输出 | 配置 / 选股 / 交叉效应 | 各因子组贡献 + 残差 |
| 时间口径 | 完全一致（F8-001, F8-002） | 完全一致（F8-001, F8-002） |

两个模块的 `strategy_return` 和 `benchmark_return` 序列应高度一致（最大偏差 < 1e-6）。

---

## 二、WLS 回归模型

**模型**：`r_i = α + Σ_k β_k × g_ki + ε_i`

- `r_i`：股票 i 的期间累计收益
- `g_ki`：股票 i 在因子组 k 的组合因子值（k = value, quality, growth, momentum, volatility, liquidity, fund_flow）
- `β_k`：因子组 k 的截面因子收益（每单位暴露获得的收益）
- `α`：截距（基准公共收益项，归因中会消除）

**权重**：`w_b_i`（基准权重），更重视市值大的股票。

**贡献**：`contribution_k = β_k × ΔE_k`，其中 `ΔE_k = Σ w_p_i × g_ki - Σ w_b_i × g_ki`（策略 vs 基准的因子暴露差）

**恒等式**：`Σ_k contribution_k + residual = actual_excess`

---

## 三、关键常量

### 3.1 `DEFAULT_FACTOR_GROUPS`（第 56-92 行）

28 个因子→7 个组的映射：
- `value`（6 因子）：ep_ttm, bp, sp_ttm, dy_ttm, cfp, fcfp
- `quality`（6 因子）：roe, roa, gross_margin, asset_turn, leverage, accrual
- `growth`（4 因子）：np_yoy, rev_yoy, roe_delta, q_roe
- `momentum`（4 因子）：ret_1m, mom_6_1, mom_12_1, holder_chg
- `volatility`（3 因子）：vol_60d, ivol_60d, max_ret
- `liquidity`（2 因子）：turn_20d, amihud
- `fund_flow`（3 因子）：margin_ratio, short_ratio, large_net_inflow

### 3.2 `GROUP_ORDER`（第 95-98 行）

```python
GROUP_ORDER = ["value", "quality", "growth", "momentum",
               "volatility", "liquidity", "fund_flow"]
```

输出 DataFrame 的列顺序固定，保证不同运行结果可比较。

---

## 四、F8-004：加载最终入模因子（第 107-153 行）

```python
def _load_final_factor_group_map() -> dict[str, str]:
    final_factors_path = cfg.ROOT / "reports" / "factor_evaluation" / "final_factors.json"
    if not final_factors_path.exists():
        log.warning("final_factors.json 不存在，退回到 DEFAULT_FACTOR_GROUPS（候选因子池）")
        return DEFAULT_FACTOR_GROUPS

    data = json.load(f)
    final_factors = data.get("final_factors", [])
    ...
    for factor in final_factors:
        if factor in DEFAULT_FACTOR_GROUPS:
            group_map[factor] = DEFAULT_FACTOR_GROUPS[factor]
        else:
            log.warning("因子 %s 不在 DEFAULT_FACTOR_GROUPS 中，从归因中排除", factor)
    return group_map
```

**F8-004 的意义**：归因应该只用**实际进入策略的因子**（`final_factors.json`），而不是所有候选因子（28 个）。用候选因子池归因会包含被筛掉的弱因子，归因结论与实际策略不符。

退回到候选因子池时，结果应被标注为"风格因子归因"而非"策略 alpha 归因"（注释中有说明）。

---

## 五、F8-005：从评估报告读方向（第 160-201 行）

```python
def _load_factor_directions_from_summary() -> dict[str, int]:
    summary_path = cfg.ROOT / "reports" / "factor_evaluation" / "factor_summary.csv"
    ...
    for _, row in df.iterrows():
        direction = row["factor_direction"]
        if pd.notna(direction):
            directions[str(row["factor"])] = int(direction)
    return directions
```

**F8-005 的意义**：因子方向应来自训练集的实测 IC 均值符号（`factor_summary.csv`），不用 `FACTOR_DIRECTIONS` 硬编码表。这避免了手工配置与实测结果不一致的问题。

对方向缺失的因子，不提供默认值（不用 +1 补位），`_build_group_composites` 遇到方向缺失的因子会 warning + 跳过，保证归因方向可追溯。

---

## 六、因子组合成（第 373-423 行）

```python
def _build_group_composites(factor_panels, T, factor_group_map, factor_directions):
    for factor_name, group in factor_group_map.items():
        direction = factor_directions.get(factor_name)
        if direction is None:
            log.warning("因子 %s 方向缺失，跳过", factor_name)
            continue
        s = panel.loc[T].astype(float) * int(direction)   # 方向调整
        group_series.setdefault(group, []).append(s)

    for group, series_list in group_series.items():
        composite = pd.concat(series_list, axis=1).mean(axis=1)  # 等权合成
        if composite.notna().any():
            result[group] = composite
    return result
```

组内**等权合成**：`pd.DataFrame(...).mean(axis=1)` 是 NaN-safe 的，某因子缺失不影响同组其他因子。

方向调整：`× direction`，方向调整后，因子值越高=越好（统一方向）。

---

## 七、WLS 回归实现（第 426-494 行）

```python
def _run_wls_regression(group_composites, stock_returns, wls_weights, min_stocks=50):
    X = df[group_names].values.astype(float)   # (n_stocks, n_groups)
    y = df["__r__"].values.astype(float)
    w = df["__w__"].values.astype(float)

    X_with_const = np.column_stack([np.ones(n_stocks), X])   # 加截距列

    # WLS 变换：乘以 sqrt(w)
    sqrt_w = np.sqrt(w)
    X_w    = X_with_const * sqrt_w[:, np.newaxis]
    y_w    = y * sqrt_w

    coef, _, _, _ = np.linalg.lstsq(X_w, y_w, rcond=None)
    # coef[0] = 截距，coef[1:] = 各因子组 β_k
```

### 7.1 WLS 的数学原理

WLS 是 OLS 的加权版本。等价于：在普通 OLS 中，给第 i 个样本乘以权重 `w_i`，即最小化 `Σ w_i (r_i - ŷ_i)²`。

**实现方式**：将 X 和 y 各乘以 `√w_i`，然后用 OLS（lstsq）求解。数学上等价，实现更简单。

### 7.2 截距的作用

截距 α 是基准收益的公共项。在计算暴露差时：
- 策略暴露差：`ΔE_k = Σ w_p_i × g_ki - Σ w_b_i × g_ki`
- 贡献：`β_k × ΔE_k`

截距 α 对策略和基准的贡献相同（`Σ w_p × 1 = 1` 和 `Σ w_b × 1 = 1`），差值为 0，所以截距自动在超额收益归因中消除。不需要显式处理。

### 7.3 加权 R²

```python
y_pred  = X_with_const @ coef
ss_res  = float(np.sum(w * (y - y_pred) ** 2))
y_mean  = float(np.average(y, weights=w))
ss_tot  = float(np.sum(w * (y - y_mean) ** 2))
r2      = (1.0 - ss_res / ss_tot) if ss_tot > 1e-12 else np.nan
```

加权 R² 衡量因子模型的解释力：R² 越高，说明这几个因子组能解释更多的截面收益差异。R² 低不一定是问题，因子模型本来就难以解释所有个股收益。

### 7.4 最少股票数检查

```python
if n_stocks < min_stocks:  # min_stocks = 50
    return {g: np.nan for g in group_names}, np.nan, n_stocks
```

回归需要足够多样本（变量数约 7 个，至少需要 50 只股票）。少于 50 只时，各组 β 估计太不稳定，置 NaN。

---

## 八、暴露差计算（第 645-666 行）

```python
for group in sorted(group_composites.keys()):
    g_vals = group_composites[group]
    E_p = float((w_p.reindex(g_vals.index).fillna(0.0) * g_vals.fillna(0.0)).sum())
    E_b = float((w_b.reindex(g_vals.index).fillna(0.0) * g_vals.fillna(0.0)).sum())

    beta         = factor_returns.get(group, np.nan)
    contribution = float(beta * (E_p - E_b)) if pd.notna(beta) else np.nan
```

`E_p`（策略对因子组 k 的暴露）= `Σ w_p_i × g_ki`，即策略持仓在该因子组上的加权平均值。  
`E_b`（基准暴露）= `Σ w_b_i × g_ki`。  
`ΔE_k = E_p - E_b`：策略相对基准的超额暴露。

**经济含义**：若策略超配 quality 因子（ΔE_quality > 0），且质量因子这个月的截面收益 β_quality > 0，则 `contribution_quality = β_quality × ΔE_quality > 0`，策略获得正超额。

---

## 九、会计恒等式校验（第 717-723 行）

```python
check = (
    period_summary[group_cols].sum(axis=1)
    + period_summary["residual"]
    - period_summary["total_excess"]
).abs().max()
```

`total_excess = Σ contribution_k + residual` 由构造保证永远成立（residual 的定义就是余项）。这个校验是防御性检查，如果失败说明代码有 bug（如 NaN 传播问题）。

---

## 十、归因输出结构

**`period_factor_attr`**：MultiIndex `(period_start, group_name)`

| 列 | 含义 |
|----|------|
| factor_return | β_k（因子组截面收益） |
| portfolio_exposure | E_p（策略暴露） |
| benchmark_exposure | E_b（基准暴露） |
| exposure_diff | ΔE_k = E_p - E_b |
| contribution | β_k × ΔE_k |

**`period_summary`**：index = period_start

| 列 | 含义 |
|----|------|
| total_excess | 实际超额收益 |
| value/quality/.../fund_flow | 各因子组贡献 |
| residual | 未被因子解释的超额（含个股特异 + 成本差 + 模型误差） |
| regression_r2 | WLS 拟合优度 |
| n_stocks | 实际参与回归的股票数 |

---

## 十一、归因结果解读指南

| 现象 | 可能含义 |
|------|---------|
| `value` 贡献持续正 | 策略超配低估值股票，且估值因子在该期有效 |
| `momentum` 贡献波动大 | 动量因子在不同市场环境下方向不稳定 |
| `residual` 绝对值大 | 有大量超额来自因子模型未捕捉的个股特异 alpha 或交易成本差 |
| `regression_r2` 低（< 0.1） | 7 个因子组解释力有限，该月超额更多来自个股选择 |
| `n_stocks` 远低于 500 | 大量股票因数据缺失未进入回归，结果可靠性下降 |

---

## 十二、因子归因 vs BHB 归因的互补关系

两个归因模块回答不同问题：

```
BHB（行业归因）：            因子归因：
超额来自哪些行业？            超额来自哪些因子暴露？

配置：行业权重配置正确         value：低估值股超配
选股：行业内选股优秀           quality：高质量股超配
                              residual：模型之外的特异 alpha

两者的 strategy_return ≈ benchmark_return 应一致（< 1e-6 差异），
若偏差大说明期间收益计算口径不同，需排查。
```
