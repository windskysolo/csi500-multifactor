# 13 — 组合优化器：optimizer.py 逐行精讲

> 对应源文件：`src/portfolio/optimizer.py`（698 行）  
> 前置依赖：`12_covariance.md`（协方差矩阵），`04_universe.md`（TradeState）  
> 核心任务：把合成信号转化为满足约束的权重向量，内置三层 Fallback

---

## 一、优化问题结构

L1（严格）路线的完整 QP（二次规划）：

```
最大化  α^T · w
约束    (w - w_b)^T · Σ_daily · (w - w_b) ≤ TE²/252   跟踪误差（二次约束）
        |Σ_{行业i} (w_j - wb_j)| ≤ industry_max_dev    行业偏离（线性）
        |w_i - wb_i| ≤ single_max_dev                  单股偏离（线性）
        w_i = w_prev_i    ∀ 停牌股                      停牌锁定
        w_i ≤ w_prev_i   ∀ 涨停股                       涨停不加仓
        w_i ≥ w_prev_i   ∀ 跌停股                       跌停不减仓
        w_i ≥ 0                                         纯多头
        Σ w_i = 1                                       全仓
```

---

## 二、数据结构

### 2.1 `OptimizeConfig`（第 62-72 行）

```python
@dataclass
class OptimizeConfig:
    te_target_annual: float = cfg.OPT_TE_TARGET_ANNUAL   # 年化跟踪误差目标
    industry_max_dev: float = cfg.OPT_INDUSTRY_MAX_DEV   # 行业权重最大偏离
    single_max_dev: float   = cfg.OPT_SINGLE_MAX_DEV     # 单股权重最大偏离
    topn: int               = cfg.OPT_TOPN               # L3 兜底选 N 只
    max_solve_seconds: float = 30.0                      # 超时触发 Fallback
    solver_order: list[str] = ["CLARABEL", "SCS"]        # 求解器优先顺序
```

所有研究决策参数来自 `config.py`（集中管理），`max_solve_seconds` 是工程参数（与研究无关）。

### 2.2 `OptimizeResult`（第 74-82 行）

```python
@dataclass
class OptimizeResult:
    weights: pd.Series       # ts_code → weight，和=1
    fallback_level: int      # 0=L1, 1=L2, 2=L3
    solver_status: str       # cvxpy 状态；L3 时为 'topn_fallback'
    solve_time_s: float
    cov_available: bool = True          # 是否有真实协方差矩阵
    constraint_compliant: bool = True   # L3 权重是否满足全部约束
```

`fallback_level` 是审计指标。回测结束后应检查有多少期 L1/L2/L3，L3 期数过多说明优化器频繁失败，需要排查。

---

## 三、内部辅助函数

### 3.1 行业矩阵构建（第 89-118 行）

```python
def _build_industry_matrix(codes, industry_map) -> np.ndarray:
    # 返回 (n_ind × n_stocks) 矩阵 A
    # A[i, j] = 1 当且仅当 codes[j] 属于第 i 个行业
```

CVXPY 中行业约束写成矩阵形式：`|A @ (w - w_b)| ≤ max_dev`

`A @ w` 得到各行业权重向量，`A @ (w - w_b)` 得到各行业的偏离向量，约束每个元素的绝对值 ≤ `industry_max_dev`。

不在 `industry_map` 中的股票对应全零列，不参与任何行业约束（宽松处理）。

### 3.2 线性约束集合（第 121-167 行）

```python
def _build_portfolio_constraints(w, w_b_vec, A_ind, config,
                                  halt_indices, limit_up_idx, limit_dn_idx,
                                  w_prev_vec) -> list:
    dev = w - w_b_vec
    constraints = [
        cp.sum(w) == 1.0,         # 全仓
        w >= 0.0,                 # 纯多头
        dev <= config.single_max_dev,    # 单股上偏离
        dev >= -config.single_max_dev,   # 单股下偏离
    ]
    if A_ind.shape[0] > 0:
        ind_dev = A_ind @ dev
        constraints += [ind_dev <= config.industry_max_dev,
                        ind_dev >= -config.industry_max_dev]

    # 停牌：等式约束
    for i in halt_indices:
        constraints.append(w[i] == w_prev_vec[i])

    # 涨停：不等式（不重复添加停牌股）
    for i in limit_up_idx:
        if i not in halt_indices:
            constraints.append(w[i] <= w_prev_vec[i])

    # 跌停：不等式（不重复添加停牌股）
    for i in limit_dn_idx:
        if i not in halt_indices:
            constraints.append(w[i] >= w_prev_vec[i])

    return constraints
```

**停牌优先于涨跌停**：`if i not in halt_indices` 确保停牌股只添加等式约束，不重复添加不等式。停牌的等式约束已经比涨跌停的不等式更严格。

**`w_prev_vec is None` 时不添加涨跌停约束**：第一期无历史权重，无法约束涨跌停，直接跳过。

### 3.3 权重后处理（第 170-199 行）

```python
def _postprocess_weights(w_vals, status) -> tuple[np.ndarray, bool]:
    min_val = float(np.min(w_vals))
    trigger_l2 = False

    if status == "optimal_inaccurate":
        if min_val < -MAX_NEG_WEIGHT_THRESHOLD:  # -0.005
            trigger_l2 = True  # 负权重太大，质量不可接受，降级 L2
        else:
            pass  # 小的负权重，后处理修复

    w = np.clip(w_vals, 0.0, None)  # 裁剪负权重
    total = float(w.sum())
    w = w / total if total > 1e-10 else np.ones(len(w)) / len(w)
    return w, trigger_l2
```

`optimal_inaccurate`：CVXPY 返回的状态，说明优化器到达了接近最优的解，但数值精度不足以确认精确最优。通常因浮点误差产生微小负权重（如 `-0.0001`）。

- 若最大负权重 < -0.005（绝对值超阈值）：质量太差，触发 L2 降级
- 否则：裁零 + 归一化修复，接受该解

### 3.4 求解器重试（第 202-235 行）

```python
def _try_solve(problem, config) -> tuple[bool, str, float]:
    for solver_name in config.solver_order:  # ["CLARABEL", "SCS"]
        try:
            problem.solve(solver=solver_const, warm_start=True)
            status = problem.status
            if status in ACCEPTABLE_STATUSES and elapsed <= config.max_solve_seconds:
                return True, status, elapsed
        except Exception as exc:
            log.warning(...)
    return False, status, elapsed
```

`ACCEPTABLE_STATUSES = {"optimal", "optimal_inaccurate"}`：只有这两个状态视为可用。

`warm_start=True`：将上一次求解的结果作为初始点，大幅加快本次求解（月度组合变化不大，热启动效果很好）。

求解器优先级：CLARABEL（精度高，新版 CVXPY 默认）→ SCS（速度快，精度低）。若 CLARABEL 超时，SCS 作为备选。

---

## 四、L3 TopN 等权（第 238-348 行）

L3 是所有优化失败后的最后防线，不再求解 QP，直接选因子值最高的前 N 只股票等权。

```python
def _topn_equal_weight(
    alpha_vec, n, topn,
    halt_indices, no_buy_indices, limit_dn_indices,
    w_prev_vec, single_max_dev,
) -> tuple[np.ndarray, bool]:
```

### 4.1 约束优先级

```
1. 停牌 → 锁定上期权重（必须执行）
2. 跌停（非停牌）→ 持有上期权重，不减仓
3. 涨停（非停牌）→ 排除出选股池
4. 剩余可交易预算 → TopN 等权
```

### 4.2 动态计算最少选股数

```python
n_min = math.ceil(free_budget / single_max_dev) if single_max_dev > 0 else topn
effective_topn = max(topn, n_min)
```

`single_max_dev` 是单股权重相对基准的最大偏离。假设 w_b=0（基准权重为 0），则持有任何正权重都算偏离。等权分配 `free_budget / effective_topn` 个单位给每只股票，要保证 `free_budget / effective_topn ≤ single_max_dev`，即 `effective_topn ≥ free_budget / single_max_dev`。

### 4.3 紧急回退（constraint_compliant=False）

```python
if selected:  # 正常路径
    ...
    return w, True

# 无任何候选（停牌/涨停/跌停全覆盖）
emergency_excluded = halt_indices | no_buy_indices
emergency_stocks = [i for i in range(n) if i not in emergency_excluded]
...
return w, False  # 不合规
```

`constraint_compliant=False` 向上层报告：此权重违反了涨停/偏离约束。调用方（`optimize_all_periods`）会在 metadata 中记录，回测引擎收到后需做相应处理。

---

## 五、单期优化主函数（第 391-543 行）

```python
def optimize_single_period(alpha, w_b, cov, industry_map,
                           w_prev, halt_codes, limit_up_codes, limit_dn_codes,
                           config) -> OptimizeResult:
```

### 5.1 NaN alpha 的处理

```python
alpha_vec = alpha.reindex(codes).fillna(0.0).values.astype(float)
```

NaN alpha（无观点股票）填充为 0，让优化器根据约束决定权重，不将其完全排除出可行域。

**为什么不排除？** 排除 NaN 股票会改变约束中的 w_b 归一化（基准权重之和变小）和行业矩阵，造成权重和不等于 1 的问题，比填 0 风险更大。

### 5.2 psd_wrap 跳过 ARPACK 验证

```python
l1_constraints.append(
    cp.quad_form(w_l1 - w_b_vec, cp.psd_wrap(cov)) <= te_limit
)
```

`cp.psd_wrap(cov)` 声明矩阵已正定，绕过 CVXPY 内部的 ARPACK 特征值验证。在 500×500 矩阵上 ARPACK 常迭代不收敛，`psd_wrap` 是工程上必要的优化。前提是 `covariance.py` 中已保证正定性。

### 5.3 三层 Fallback 流程

```
L1 QP（含 TE 二次约束）
    ├── 成功（optimal/optimal_inaccurate 且负权重小）→ 返回 fallback_level=0
    └── 失败或负权重超阈值
            ↓
        L2 LP（去掉 TE 约束，保留全部线性约束）
            ├── 成功 → 返回 fallback_level=1
            └── 失败或负权重超阈值
                    ↓
                L3 TopN 等权 → 返回 fallback_level=2
```

---

## 六、批量优化（第 550-697 行）

```python
def optimize_all_periods(
    composite_panel, benchmark_weights, cov_dict, cov_codes,
    industry_map, rebalance_dates, config,
    halt_dict, limit_up_dict, limit_dn_dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
```

### 6.1 协方差对齐

```python
if all_in_base:
    idx = [cov_code_idx[c] for c in period_codes]
    cov_period = cov_dict[T][np.ix_(idx, idx)]  # 子集化
else:
    cov_period = _expand_cov_for_missing(...)    # 填充缺失
```

`np.ix_(idx, idx)`：NumPy 的高级索引，同时在行和列方向取子集，等价于 `cov[np.array(idx)][:, np.array(idx)]`，但更高效。

### 6.2 F6-002：协方差缺失的处理

```python
if not cov_available:
    log.warning("optimize_all_periods: %s 无协方差矩阵，降级为 L2（F6-002）", ...)
    cov_period = np.eye(len(period_codes)) * 1e-4  # 占位矩阵

...
# 优化后修复 fallback_level
if not cov_available and result.fallback_level == 0:
    effective_fallback = 1
    effective_status   = result.solver_status + "+cov_missing"
```

为什么用 `1e-4 * I` 占位而不是直接跳 L2？因为 `optimize_single_period` 的接口固定接受 cov 参数，传 `1e-4 * I` 后，TE 约束形同虚设（非常宽松），实际上就是无 TE 约束的 L2 求解。之后再强制覆盖 `fallback_level = 1` 确保外部记录准确。

### 6.3 `w_prev` 传递

```python
w_prev = result.weights  # 上期结果作为下期 w_prev
```

每期优化后，把当期权重存为 `w_prev`，传给下期作为历史权重，用于涨跌停约束。注意这是**目标权重**，不是实际执行后的持仓（注释中标注 `"w_prev_source": "target_weight"`），因为实际持仓会受到停牌、价格等影响产生偏差。

---

## 七、fallback_level 分布分析

回测完成后，`metadata_df["fallback_level"]` 的分布是重要的健康指标：

| fallback_level | 含义 | 正常范围 |
|----------------|------|---------|
| 0 (L1) | QP 完整求解成功 | 应 > 90% |
| 1 (L2) | TE 约束太紧或协方差缺失 | 偶尔出现 |
| 2 (L3) | QP 和 LP 都失败 | 应 < 5%，否则排查 |

如果 L2 频繁出现，检查：
- 跟踪误差目标是否设置过紧（`OPT_TE_TARGET_ANNUAL`）
- 协方差矩阵估计是否频繁失败

如果 L3 频繁出现，检查：
- 停牌比例是否异常高（可从 `trade_log` 的 `n_locked` 列看）
- 单股偏离约束是否过紧导致可行域为空
