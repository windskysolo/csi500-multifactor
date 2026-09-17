# 21 — factor_attr.py：因子收益归因

> 对应源文件：`src/attribution/factor_attr.py`
> 实际行数：**800 行**（计划快照为 615，新增 F8-004/005、`DEFAULT_FACTOR_GROUPS`、`GROUP_ORDER`，撰写前已核实）
> 处理方式：**沿用 + 核查更新**
> 所属 Part：Part 6 — 回测与归因层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 6  归因    ← 本文件（factor_attr.py）
Layer 6  归因    brinson.py（并列，共用时间对齐逻辑但不互相依赖）
Layer 5  回测    engine.py（提供 actual_weights）
Layer 2  因子评估 src/evaluation/（提供 factor_panels）
Layer 0  数据    loader.py / config.py / final_factors.json / factor_summary.csv
```

`factor_attr.py` 将月度超额收益**按因子组（价值/质量/成长/动量/波动率/流动性/资金流向）分解**，回答"超额来自哪类因子的暴露"。

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游 | `BacktestResult.actual_weights` | 策略日频持仓权重（不归一化，F8-002）|
| 上游 | `data/processed/factor_panels/{name}.parquet` | 已预处理的因子横截面面板 |
| 上游 | `reports/factor_evaluation/final_factors.json` | 最终入模因子列表（F8-004）|
| 上游 | `reports/factor_evaluation/factor_summary.csv` | 因子方向（F8-005）|
| 上游 | `load_daily_quote` | 后复权价格（F8-001）|
| 下游 | `pipeline/stages.run_attribution_stage` | 写落盘产物 |

### 计划快照 vs 实际变化

| 变化 | 说明 |
|------|------|
| **F8-004**：从 `final_factors.json` 加载因子 | 仅对入模因子做归因，不再用全量 `DEFAULT_FACTOR_GROUPS` |
| **F8-005**：从 `factor_summary.csv` 加载方向 | 方向可追溯到单因子评估结果，不再硬编码 |
| `DEFAULT_FACTOR_GROUPS`（L56–109）| 47 个候选因子的组映射，作为 F8-004 的 fallback 和 ID 表 |
| `GROUP_ORDER`（L112–115）| 输出列顺序固定为 value/quality/growth/momentum/volatility/liquidity/fund_flow |
| 行数从 615 → 800 | `DEFAULT_FACTOR_GROUPS` 大幅扩展（从约 20 个到 47 个）+ F8 系列修复 |

---

## 二、时间对齐与 PIT 假设

`factor_attr.py` 与 `brinson.py` 共用**完全相同**的时间对齐规则：

```
调仓日 T（月末）：
  - 因子值：T 日因子面板（已预处理，PIT 安全）
  - 基准权重 w_b：T 日（归一化，和=1）
  - 策略权重 w_p：actual_weights 中 T 之后第一个可用日期（≈T+1 执行后，不归一化，F8-002）

期间收益（T → T_next）：
  - T+1 开盘买入，T_next 收盘卖出（F8-001，与 brinson.py 完全相同逻辑）

两模块的 strategy_return / benchmark_return 序列应高度一致（最大偏差 < 1e-6）。
```

**因子值的 PIT 安全保证**：`factor_panels/` 中的数据由 `build_factor_panels.py` 生成，财务因子使用 `ann_date`（公告日期）而非 `accper`（报告期）。T 日因子面板仅包含 T 日公告日期 ≤ T 的最新财务数据，无前视偏差。

---

## 三、模块顶部：导入与常量

### `DEFAULT_FACTOR_GROUPS`（L56–109）

47 个候选因子到因子组的映射表：

```python
DEFAULT_FACTOR_GROUPS: dict[str, str] = {
    # 价值（6个）
    "ep_ttm": "value", "bp": "value", "sp_ttm": "value",
    "dy_ttm": "value", "cfp": "value", "fcfp": "value",
    # 质量（10个）
    "roe": "quality", "roa": "quality", "gross_margin": "quality",
    "asset_turn": "quality", "leverage": "quality",
    "accrual": "quality", "piotroski_f": "quality",
    "gross_margin_trend": "quality", "roe_smoothed_4q": "quality",
    "garp": "quality",
    # 成长（8个）
    "np_yoy": "growth", "rev_yoy": "growth", "roe_delta": "growth",
    "q_roe": "growth", "roe_delta_3q": "growth",
    "analyst_eps_revision": "growth", "analyst_rating_chg": "growth",
    "rev_acceleration": "growth",
    # 动量（8个）
    "ret_1m": "momentum", "mom_6_1": "momentum", "mom_12_1": "momentum",
    "holder_chg": "momentum", "high_52w": "momentum",
    "high_52w_v2": "momentum", "ind_adj_mom": "momentum",
    "mom_risk_adj": "momentum",
    # 波动率（3个）
    "vol_60d": "volatility", "ivol_60d": "volatility", "max_ret": "volatility",
    # 流动性（2个）
    "turn_20d": "liquidity", "amihud": "liquidity",
    # 资金流向（8个）
    "margin_ratio": "fund_flow", "short_ratio": "fund_flow",
    "large_net_inflow": "fund_flow", "hk_hold_ratio": "fund_flow",
    "hk_hold_chg": "fund_flow", "insider_net_buy": "fund_flow",
    "pledge_ratio": "fund_flow", "float_pct_30d": "fund_flow",
}
```

`DEFAULT_FACTOR_GROUPS` 既是 F8-004 的 fallback（当 `final_factors.json` 不存在时），也是"因子 → 组"的 ID 表（即便 `final_factors.json` 只列出子集，也通过此表查找组名）。

### `GROUP_ORDER`（L112–115）

```python
GROUP_ORDER: list[str] = [
    "value", "quality", "growth", "momentum",
    "volatility", "liquidity", "fund_flow",
]
```

固定列顺序保证不同 run 的 `period_summary` 列对齐，便于 compare_runs 横向比较。

---

## 四、核心函数逐一解析

### 4.1 `compute_factor_attribution` — 主入口

```python
# L518–749
def compute_factor_attribution(
    actual_weights: pd.DataFrame,
    backtest_start: pd.Timestamp,
    backtest_end: pd.Timestamp,
    factor_group_map: dict[str, str] | None = None,
    factor_directions: dict[str, int] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
```

**返回值**：
- `period_factor_attr`：MultiIndex=(period_start, group_name)，列：factor_return/portfolio_exposure/benchmark_exposure/exposure_diff/contribution
- `period_summary`：index=period_start，按 GROUP_ORDER 排列各组贡献

**会计恒等式校验**（L733–740）：
```python
check = (
    period_summary[group_cols].sum(axis=1)  # 所有组贡献之和
    + period_summary["residual"]
    - period_summary["total_excess"]
).abs().max()
```
恒等式 `Σ_k contribution_k + residual = total_excess` 由构造保证永远成立（residual 的定义就是差值）。若校验失败（> 1e-8）说明代码 bug。

---

### 4.2 `_load_final_factor_group_map` — F8-004

```python
# L124–170
def _load_final_factor_group_map() -> dict[str, str]:
```

**加载逻辑**：
```
final_factors.json 存在？
    ├── 是 → 读取 final_factors 列表
    │         ├── 非空 → 在 DEFAULT_FACTOR_GROUPS 中查组名
    │         │          ├── 未知因子 → warning + 跳过
    │         │          └── 返回已知因子的组映射
    │         └── 为空 → warning + fallback 到 DEFAULT_FACTOR_GROUPS
    └── 否 → warning + fallback 到 DEFAULT_FACTOR_GROUPS
```

**Fallback 的语义差异**：
- 从 `final_factors.json` 加载：归因基于**入模因子**，`factor_list_source="final_factors.json"`
- Fallback 到 DEFAULT_FACTOR_GROUPS：归因基于**候选因子池**，`factor_list_source="caller_provided"` 或 `"final_factors.json"` 的 fallback 分支

`period_summary["factor_list_source"]` 记录此信息，让读者知道归因的因子集合是哪个。

---

### 4.3 `_load_factor_directions_from_summary` — F8-005

```python
# L177–218
def _load_factor_directions_from_summary() -> dict[str, int]:
```

**加载逻辑**：
```
factor_summary.csv 存在？
    ├── 是 → 读取 factor/factor_direction 列
    │         → 返回 {factor_name: int(direction)}
    └── 否 → warning + fallback 到 FACTOR_DIRECTIONS（combiner.py 中的硬编码表）
```

方向缺失的因子**不提供默认值**（不用 +1 补位），在 `_build_group_composites` 中被跳过并 warning，保证归因方向可追溯。

---

### 4.4 `_build_group_composites` — 单期组合因子合成

```python
# L390–440
def _build_group_composites(
    factor_panels: dict[str, pd.DataFrame],
    T: pd.Timestamp,
    factor_group_map: dict[str, str],
    factor_directions: dict[str, int],
) -> dict[str, pd.Series]:
```

**合成逻辑**：
```python
for factor_name, group in factor_group_map.items():
    panel = factor_panels.get(factor_name)
    if panel is None or T not in panel.index: continue

    direction = factor_directions.get(factor_name)
    if direction is None:
        log.warning("因子 %s 方向缺失，跳过", factor_name)
        continue

    s = panel.loc[T].astype(float) * int(direction)   # 方向调整
    if s.notna().any():
        group_series.setdefault(group, []).append(s)

# 组内等权均值（NaN-safe）
result[group] = pd.concat(series_list, axis=1).mean(axis=1)
```

方向调整后再等权合成，保证所有组合因子对收益的预测方向一致（高值 → 高预期收益）。

---

### 4.5 `_run_wls_regression` — 横截面 WLS 回归

```python
# L443–511
def _run_wls_regression(
    group_composites: dict[str, pd.Series],   # group_name → (ts_code → 因子值)
    stock_returns: pd.Series,                  # ts_code → 期间收益
    wls_weights: pd.Series,                    # ts_code → 基准权重（WLS 权重）
    min_stocks: int = 50,
) -> tuple[dict[str, float], float, int]:
```

**WLS 回归模型**：

$$r_i = \alpha + \sum_k \beta_k \cdot g_{ki} + \varepsilon_i$$

- $r_i$：股票 i 的期间累计收益
- $g_{ki}$：股票 i 在第 k 因子组上的合成因子值
- $\beta_k$：第 k 因子组的截面因子收益（每单位暴露的期间收益）
- $\alpha$：截距（基准收益的公共项，在超额分析中消除）
- WLS 权重 = $w_{b,i}$（基准权重，更重视指数权重大的股票）

**WLS 实现**（L487–510）：
```python
X_with_const = np.column_stack([np.ones(n_stocks), X])   # 加截距列
sqrt_w = np.sqrt(w)
X_w = X_with_const * sqrt_w[:, np.newaxis]   # WLS 变换
y_w = y * sqrt_w

coef, _, _, _ = np.linalg.lstsq(X_w, y_w, rcond=None)
# coef[0] = 截距，coef[1:] = 各因子组收益 β_k
```

**截距的处理**：`α` 表示 WLS 回归中控制所有因子组暴露后的公共项。计算暴露差 `ΔE_k = E_p_k - E_b_k` 时，截距对策略和基准的贡献相同（两者的常数暴露都是 1），因此在 `contribution_k = β_k × ΔE_k` 中自然消除。

**加权 R²**（L502–507）：
```python
y_pred = X_with_const @ coef
ss_res = np.sum(w * (y - y_pred) ** 2)
ss_tot = np.sum(w * (y - y_mean) ** 2)
r2 = 1.0 - ss_res / ss_tot
```
加权 R² 反映因子组合成模型对横截面收益的解释能力，记录在 `period_summary["regression_r2"]`。

---

### 4.6 暴露差与贡献计算

```python
# 在 compute_factor_attribution 主循环中（L666–683）
for group in sorted(group_composites.keys()):
    g_vals = group_composites[group]

    E_p = float((w_p.reindex(g_vals.index).fillna(0.0) * g_vals.fillna(0.0)).sum())
    E_b = float((w_b.reindex(g_vals.index).fillna(0.0) * g_vals.fillna(0.0)).sum())

    beta = factor_returns.get(group, np.nan)
    contribution = float(beta * (E_p - E_b)) if pd.notna(beta) else np.nan
```

$$\Delta E_k = E_{p,k} - E_{b,k} = \sum_i w_{p,i} \cdot g_{k,i} - \sum_i w_{b,i} \cdot g_{k,i}$$

$$\text{contribution}_k = \beta_k \times \Delta E_k$$

`contribution_k` 的正负含义：
- 正：策略在该因子组上的暴露高于基准（`ΔE_k > 0`），且该因子组本期有正收益（`β_k > 0`）
- 负：两种组合之一：高暴露遇上负收益，或低暴露遇上正收益

---

## 五、内部辅助函数

| 函数 | 位置 | 说明 |
|------|------|------|
| `_get_period_list` | L225–253 | 与 brinson.py 完全相同的逻辑（代码重复，未抽共用）|
| `_get_period_start_weights` | L256–285 | 与 brinson.py 相同（F8-002 不归一化）|
| `_get_benchmark_weights` | L288–307 | 与 brinson.py 相同 |
| `_compute_period_returns_t1_open` | L310–360 | 与 brinson.py 相同（F8-001）|
| `_load_available_factor_panels` | L363–387 | 批量加载 `data/processed/factor_panels/`，缺失文件自动跳过 |
| `_build_group_composites` | L390–440 | 单期方向调整 + 组内等权合成 |
| `_run_wls_regression` | L443–511 | 横截面 WLS 回归，返回 β_k、R² 和样本数 |

**注意**：`_get_period_list`/`_get_period_start_weights`/`_get_benchmark_weights`/`_compute_period_returns_t1_open` 在 brinson.py 和 factor_attr.py 中**代码重复**（各有一套相同实现）。这是当前的技术债务，潜在的维护风险是：若其中一个修改了逻辑，另一个可能未同步更新。目前两个文件的实现完全一致（可通过 diff 验证）。

---

## 六、落盘产物（Artifacts）

`factor_attr.py` 不直接写文件，由 `run_attribution_stage` 写入：

| 产物 | 路径 | 格式 | 内容 |
|------|------|------|------|
| `factor_attr.parquet` | `runs/.../attribution/factor_attr.parquet` | Parquet | MultiIndex=(period_start, group_name)，5 列 |
| `factor_summary.parquet` | `runs/.../attribution/factor_summary.parquet` | Parquet | 每期汇总，含各组贡献和 residual |

`period_summary` 列顺序（按 GROUP_ORDER 排列）：
```
period_end, strategy_return, benchmark_return, total_excess,
cash_weight, weight_sum,
value, quality, growth, momentum, volatility, liquidity, fund_flow,
residual, regression_r2, n_stocks, factor_list_source
```

---

## 七、相关测试

**当前状态**：无专门的 `test_factor_attr.py`（TODO）。

**必须补充的测试**：

| 场景 | 要点 |
|------|------|
| 会计恒等式 | `Σ contribution + residual = total_excess`（构造已知 β_k/ΔE_k）|
| WLS 基本可行性 | 50+ 股票的截面回归不崩溃，R² ∈ [0, 1] |
| 方向缺失跳过 | 方向缺失的因子不参与回归，warning 被记录 |
| F8-004 fallback | `final_factors.json` 不存在时使用 DEFAULT_FACTOR_GROUPS |
| F8-005 fallback | `factor_summary.csv` 不存在时从 combiner.FACTOR_DIRECTIONS 退回 |
| 负方向因子 | `direction=-1` 时该因子值被翻转再合成 |

---

## 八、失败与降级路径

| 失败场景 | 行为 |
|---------|------|
| `final_factors.json` 不存在 | warning + fallback 到 DEFAULT_FACTOR_GROUPS |
| `factor_summary.csv` 不存在 | warning + fallback 到 FACTOR_DIRECTIONS |
| 某期无有效因子数据 | `group_composites={}` → 跳过该期 |
| WLS 有效股票 < 50 | 返回 `{group: NaN}`, `r2=NaN`, `n_stocks=实际数` |
| `np.linalg.lstsq` 失败 | 捕获 `LinAlgError`，返回 NaN 的 factor_returns |
| 所有期归因失败 | `raise ValueError("所有期间均归因失败")` |

---

## 九、数据流图

```
BacktestResult.actual_weights
    │
    ▼
F8-004: _load_final_factor_group_map()    ← final_factors.json（或 fallback）
F8-005: _load_factor_directions_from_summary()  ← factor_summary.csv（或 fallback）
    │
    ▼
get_rebalance_dates() + _get_period_list() → [(T, T_next), ...]
    │
    ├── 一次性加载 ──────────────────────────────────────────────────────
    │   _load_available_factor_panels(factor_names)  ← data/processed/factor_panels/
    │   load_daily_quote → close_adj_panel, open_adj_panel
    └──────────────────────────────────────────────────────────────────
    │
    ▼ 逐期处理
for T, T_next in periods:
    │
    ├── w_p = _get_period_start_weights(actual_weights, T)   ← F8-002 不归一化
    ├── w_b = _get_benchmark_weights(T)
    ├── group_composites = _build_group_composites(...)      ← 方向调整 + 组内等权
    ├── r = _compute_period_returns_t1_open(...)             ← F8-001 T+1 开盘口径
    │
    ├── strategy_return  = (w_p × r).sum()
    ├── benchmark_return = (w_b × r).sum()
    │
    ├── _run_wls_regression(group_composites, r, w_b)
    │       → β_k（各组因子收益）、r2、n_stocks
    │
    └── for group in group_composites:
            E_p = (w_p × g_k).sum()
            E_b = (w_b × g_k).sum()
            contribution_k = β_k × (E_p - E_b)
    │
    ▼
period_factor_attr + period_summary
    │
    ▼
会计恒等式校验：Σ contribution + residual = total_excess
```

---

## 十、领域知识补充

### 横截面 WLS 与时序 OLS 的区别

**时序 OLS**（Fama-French 三因子的常见形式）：
$$r_{i,t} = \alpha_i + \beta_i^{MKT} F_{MKT,t} + \beta_i^{SMB} F_{SMB,t} + ... + \varepsilon_{i,t}$$
用**时间序列**回归，估计股票 i 对市场因子的暴露，因子收益是外生给定的（如市场超额收益）。

**横截面 WLS**（本模块采用）：
$$r_i = \alpha + \sum_k \beta_k \cdot g_{ki} + \varepsilon_i$$
用**单期横截面**回归，直接从当期股票收益反推因子组的收益 $\beta_k$。因子暴露 $g_{ki}$ 来自我们的因子面板，$\beta_k$ 是内生估计的。

这两种方法估计的是不同的东西：前者估计个股对系统性因子的风险暴露，后者估计因子组对当期收益的贡献。本项目的问题是"我的因子组合成的信号对超额有多少贡献"，横截面方法更直接。

### 残差的含义

$$\text{residual} = \text{total\_excess} - \sum_k \text{contribution}_k$$

残差包含：
1. **个股特异收益（alpha）**：不能被任何因子组解释的超额部分
2. **模型拟合误差**：因子合成（等权）不完美，与策略实际用的 Ridge 权重有差异
3. **交易成本差异**：策略权重变化（换手）产生的成本与 `strategy_return`（不含成本）之间的差

理想情况下，残差应接近零，说明因子归因的解释能力强。若残差持续显著大于各因子组贡献，可能意味着策略有未被捕捉的信号来源（如个股特异事件驱动）。

### 关于 `piotroski_f` 的特殊说明

`DEFAULT_FACTOR_GROUPS` 中保留了 `piotroski_f`，但根据消融实验结论（2026-05-29），`piotroski_f` 被标记为 `REMOVE_CANDIDATE`。若 `final_factors.json` 中已剔除 `piotroski_f`，则 F8-004 加载时不会包含它，因子归因中不会出现 piotroski_f 的贡献。若 `final_factors.json` 未更新仍包含 `piotroski_f`，归因结果中会有 quality 组的部分来自它。这是为何 `factor_list_source` 字段很重要——读者可以从 `final_factors.json` 核实归因所用的因子集合。
