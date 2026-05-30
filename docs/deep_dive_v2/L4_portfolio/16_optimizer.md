# 16 — optimizer.py：组合权重优化器（重写）

> 对应源文件：`src/portfolio/optimizer.py`
> 实际行数：**914 行**（计划快照为 712，新增 `optimize_topn_equal_weight_all_periods`、多项 Bug 修复，撰写前已核实）
> 处理方式：**重写**（新增 TopN EW 强制路径，三层 Fallback 有重大变化）
> 所属 Part：Part 5 — 组合构建层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 4  组合构建    ← 本文件（optimizer.py）
Layer 4  组合构建    covariance.py（提供 cov_dict）
Layer 3  信号合成    combiner.py / ridge_decay.py（提供 composite_panel）
Layer 8  Pipeline   stages.run_portfolio_stage（按 optimizer_mode 分发）
```

`optimizer.py` 是连接**信号层**和**回测层**的关键节点。它接受合成信号 α、基准权重 w_b、协方差矩阵 Σ，输出每期的目标权重向量。

### 两条组合构建路径

这是 v2 相比 v1 **最重要的新增内容**，理解这两条路径是理解所有实验结果的前提：

| 路径 | 入口函数 | 机制 | 用途 |
|------|---------|------|------|
| **QP 路径** | `optimize_all_periods` | 二次规划（cvxpy），三层 Fallback | 正式优化，L1→L2→L3 |
| **TopN EW 路径** | `optimize_topn_equal_weight_all_periods` | 直接 TopN 等权，无 QP | 隔离优化器影响的对照基线 |

`stages.run_portfolio_stage` 根据 `OptimizerSpec.optimizer_mode` 选择：
- `"qp"` → `optimize_all_periods`
- `"topn_ew"` → `optimize_topn_equal_weight_all_periods`

### 重大变化说明（计划快照 vs 实际）

| 变化 | 说明 |
|------|------|
| **新增 `optimize_topn_equal_weight_all_periods`**（L619–759）| 完整的 TopN EW 全周期优化路径，复用 `_topn_equal_weight` 的 L3 约束逻辑 |
| **新增 `_expand_cov_for_missing`**（L401–434）| 处理 period_codes ⊄ cov_codes 的边界情况 |
| **F6-001**：`constraint_compliant` 字段 | L3 路径新增约束可靠性标记，允许调用方检测极端情况 |
| **F6-002**：`cov_available` 字段 | 协方差缺失时强制 `fallback_level ≥ 1`，不伪装成 L1 |
| **F6-004**：`w_prev_source` 字段 | 明确标记上期权重的来源（目标权重，非实际持仓）|
| **Bug-2A/2B 修复** | 基准权重大的股票若未被 TopN 选中会导致偏离约束违约，修复为强制锁定 |
| **动态 `effective_topn`** | 保证选股数满足 `single_max_dev` 约束，而不是硬取 `topn` |
| **停牌偏离外生修复（Bug-1）** | 停牌股偏离量来自上期权重，不应叠加单股偏离约束 |
| **行数从 712 → 914** | 新增函数、完整 docstring、多处防御性检查 |

---

## 二、时间对齐与 PIT 假设

`optimizer.py` **不访问任何外部数据**，时间对齐完全由调用方保证：

| 输入 | 时间含义 | 保证方 |
|------|---------|--------|
| `alpha`（`composite_panel[T]`）| T 日收盘后可知的合成信号 | `stages.py` |
| `w_b`（`benchmark_weights[T]`）| T 日基准权重（CSI500 成分股快照）| `stages.py` |
| `cov`（`cov_dict[T]`）| 用 ≤T 日数据估计的日频协方差 | `covariance.py` |
| `w_prev`（上期结果）| 上一调仓日的**目标权重**（非实际持仓）| 优化器内部链式传递 |

**F6-004 说明**：`w_prev` 是目标权重的近似，不等于 T+1 执行后的实际持仓（实际持仓含交易成本、停牌、涨跌停的偏差）。`metadata_df["w_prev_source"] = "target_weight"` 明确标记此近似，防止误判成本计算。

输出的目标权重是 T 日盘后决策，供 `engine.py` 在 T+1 开盘执行。

---

## 三、模块顶部：导入与常量

```python
# L53–55
ACCEPTABLE_STATUSES: frozenset[str] = frozenset({"optimal", "optimal_inaccurate"})
MAX_NEG_WEIGHT_THRESHOLD: float = 0.005   # optimal_inaccurate 时最大负权重阈值
TRADING_DAYS_PER_YEAR: int = 252
```

**`ACCEPTABLE_STATUSES` 包含 `optimal_inaccurate`**：cvxpy 在 500×500 问题上偶尔返回此状态，表示求解精度略低于严格收敛。只要负权重绝对值 < 0.005（0.5%），认为数值误差足够小，后处理 clip+归一化可修复。

---

## 四、核心函数逐一解析

### 4.1 数据结构

```python
# L62–86
@dataclass
class OptimizeConfig:
    te_target_annual: float    # 年化 TE 目标，来自 cfg.OPT_TE_TARGET_ANNUAL
    industry_max_dev: float    # 行业权重最大偏离（绝对值）
    single_max_dev: float      # 单股权重最大偏离（绝对值）
    topn: int                  # L3 兜底取前 N 只
    turnover_lambda: float     # 换手成本惩罚系数；0 = 不启用
    max_solve_seconds: float = 30.0
    force_l2: bool = False     # 跳过 L1（TE 二次约束），直接从 L2 开始
    solver_order: list[str] = ["CLARABEL", "SCS"]

@dataclass
class OptimizeResult:
    weights: pd.Series          # ts_code → weight，和=1
    fallback_level: int         # 0=L1, 1=L2, 2=L3
    solver_status: str          # 'optimal'/'optimal_inaccurate'/'topn_fallback'
    solve_time_s: float
    cov_available: bool = True           # F6-002
    constraint_compliant: bool = True    # F6-001
```

**`force_l2=True` 的用途**：当 `optimizer_mode="qp"` 但已知协方差矩阵不可靠（如自定义诊断场景），可跳过 L1 直接用 L2（线性约束）。

**求解器顺序 CLARABEL → SCS**：CLARABEL 是 cvxpy 的新默认求解器（比 OSQP 更稳定），SCS 作为 fallback（无需许可证）。若环境中没有 CLARABEL，SCS 可单独完成求解。

---

### 4.2 `optimize_single_period` — 单期三层 Fallback 优化

```python
# L441–612
def optimize_single_period(
    alpha: pd.Series,
    w_b: pd.Series,
    cov: np.ndarray,
    industry_map: pd.Series,
    w_prev: pd.Series | None = None,
    halt_codes: set[str] | None = None,
    limit_up_codes: set[str] | None = None,
    limit_dn_codes: set[str] | None = None,
    config: OptimizeConfig | None = None,
) -> OptimizeResult:
```

**三层 Fallback 流程**：

```
L1: max α^T w - λ‖w-w_prev‖₁
    s.t. (w-w_b)^T Σ (w-w_b) ≤ TE²/252   ← 跟踪误差二次约束
         |Σ_ind(w-w_b)| ≤ industry_max_dev ← 行业约束
         |w_i-w_b_i| ≤ single_max_dev      ← 单股约束
         w ≥ 0, Σw = 1
    ↓ 失败（超时 / infeasible / optimal_inaccurate 且负权重 > 阈值）
L2: 同 L1，但去掉跟踪误差约束（仅线性约束）
    ↓ 失败
L3: TopN 等权（_topn_equal_weight），保留停牌/涨跌停约束
```

**L1 特殊细节**（L522–524）：
```python
l1_constraints.append(
    cp.quad_form(w_l1 - w_b_vec, cp.psd_wrap(cov)) <= te_limit
)
```
`cp.psd_wrap(cov)` 告知 CVXPY 矩阵已由 `_ensure_positive_definite` 保证正定，跳过 ARPACK 特征值验证（500×500 矩阵上 ARPACK 常迭代不收敛，导致假阴性）。

**Bug-1 修复：停牌偏离外生**（L144–157）：
停牌股的权重被强制等于 `w_prev`，其偏离量 `w_prev_i - w_b_i` 是外生的（非优化器决策）。若对停牌股同时施加 `|w_i - w_b_i| ≤ single_max_dev` 约束，当 `|w_prev_i - w_b_i| > single_max_dev` 时 L2 会真实不可行。修复方案：只对非停牌的自由股施加单股偏离约束。

```python
if w_prev_vec is not None and halt_indices:
    free_mask = [i for i in range(n_stocks) if i not in halt_indices]
    constraints += [dev[free_mask] <= single_max_dev, dev[free_mask] >= -single_max_dev]
else:
    constraints += [dev <= single_max_dev, dev >= -single_max_dev]
```

---

### 4.3 `_topn_equal_weight` — L3 等权兜底（详细）

```python
# L253–398
def _topn_equal_weight(
    alpha_vec: np.ndarray,
    n: int,
    topn: int,
    halt_indices: set[int],
    no_buy_indices: set[int] | None = None,
    limit_dn_indices: set[int] | None = None,
    w_prev_vec: np.ndarray | None = None,
    single_max_dev: float = 0.01,
    w_b_vec: np.ndarray | None = None,
) -> tuple[np.ndarray, bool]:  # F6-001: 返回 (weights, constraint_compliant)
```

这是整个优化器中**最复杂的函数**，也是 TopN EW 路径的核心。理解此函数等于理解为何 TopN EW 在当前项目中比 QP 表现更好。

**约束优先级**（从高到低）：
1. 停牌股：`w[i] = w_prev[i]`（锁定上期权重）
2. 跌停股（非停牌）：`w[i] = w_prev[i]`（不得减仓）
3. 涨停股（非停牌非跌停）：排除出候选池（不可买入）
4. `must_hold_set`（Bug-2A 修复）：基准权重 > `single_max_dev` 的股票若未被选中，偏离量必然违约，强制锁定为基准权重
5. 剩余候选：按 α 值降序排序，取前 `effective_topn` 只等权分配

**动态 `effective_topn` 计算**（F6-001 修复核心）：
```python
max_unit_w = single_max_dev + min_w_b   # 每股最大允许权重
n_min = math.ceil(free_budget / max_unit_w)
effective_topn = max(topn, n_min)
```
若 `topn=50`，`single_max_dev=0.01`，`free_budget≈1.0`，则 `n_min=100`，实际选 100 只（而非 50 只），否则每只权重 2% > 1%+基准偏离，违约。

**Bug-2B 修复：上限截断 + 重分配**（L358–375）：
```python
while to_assign:
    unit_w = remaining / len(to_assign)
    next_round = []
    for i in to_assign:
        cap = float(w_b_vec[i]) + single_max_dev
        if unit_w > cap + 1e-9:
            w[i] = cap          # 超上限 → 截断
            remaining -= cap
        else:
            next_round.append(i)
    if len(next_round) == len(to_assign):
        for i in next_round: w[i] = unit_w
        break
    to_assign = next_round
```
迭代截断：若等权分配后某股超过上限（`w_b_i + single_max_dev`），截断到上限，将剩余预算分给其他股；循环直至无截断或所有股票均分完成。

**F6-001：`constraint_compliant` 标记**：
正常路径返回 `(w, True)`。极端情况（所有候选均涨停/跌停/停牌，selected=[]）进入紧急退化路径：
```python
emergency_stocks = [i for i in range(n) if i not in halt_indices | no_buy]
# 给非停牌非涨停股分配预算（可能违反跌停不减仓约束）
return w, False   # constraint_compliant=False
```
调用方会在 `metadata_df` 中标记此期，供后续排查。

---

### 4.4 `optimize_topn_equal_weight_all_periods` — 强制 TopN EW 全周期

```python
# L619–759
def optimize_topn_equal_weight_all_periods(
    composite_panel: pd.DataFrame,
    benchmark_weights: dict[pd.Timestamp, pd.Series],
    rebalance_dates: list[pd.Timestamp],
    config: OptimizeConfig | None = None,
    halt_dict: dict[pd.Timestamp, set[str]] | None = None,
    limit_up_dict: dict[pd.Timestamp, set[str]] | None = None,
    limit_dn_dict: dict[pd.Timestamp, set[str]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
```

**设计意图**：这是 `frozen_baseline_icir_topn50_ew` 和 `rolling48_ridge_topn50_ew` 等实验的底层支撑。**不调用 QP**，直接复用 `_topn_equal_weight` 的 L3 逻辑，但以"正式路径"而非"Fallback"的语义运行。

**与 `optimize_all_periods` 的关键区别**：

| 方面 | `optimize_all_periods`（QP） | `optimize_topn_equal_weight_all_periods` |
|------|------------------------------|------------------------------------------|
| 协方差矩阵 | 需要（缺失时占位降级）| **不需要**（`cov_available=False`）|
| 目标函数 | 最大化 α^T w - λ·换手 | 无明确目标函数（排序选股）|
| 约束 | TE + 行业 + 单股 + 停牌/涨跌停 | 仅停牌/涨跌停 + 单股上限（soft）|
| 信号利用方式 | 绝对量级（α 值作为线性目标）| **序数**（只用 α 排名）|
| `fallback_level` | 0/1/2（记录实际发生的层）| 固定为 2（TopN 等权）|
| `solver_status` | 'optimal' 等 | 固定为 `'topn_ew_forced'` |

**为何 TopN EW 比 QP 更好（已诊断结论，2026-05-29）**：
信号层产出的是 z-score 化的 α，绝对值差异更多反映截面分布噪声而非真实预测信心。QP 将 α 的绝对量级用于线性目标（`max α^T w`），会放大噪声。TopN 仅用 α 排名（序数），对绝对值不敏感，更鲁棒。

**`metadata_df` 新增字段**（L715–730）：
```python
{
    "fallback_level":       2,
    "solver_status":        "topn_ew_forced",
    "cov_available":        False,          # 明确标记不使用协方差
    "optimizer_mode":       "topn_ew",
    "requested_topn":       config.topn,
    "n_holdings":           n_holdings,     # 实际持仓数
    "n_halt":               len(halt_idx),
    "n_limit_up":           len(lim_up_idx),
    "n_limit_down":         len(lim_dn_idx),
}
```

---

### 4.5 `optimize_all_periods` — QP 全周期批量

```python
# L766–913
def optimize_all_periods(
    composite_panel, benchmark_weights, cov_dict, cov_codes,
    industry_map, rebalance_dates, config=None,
    halt_dict=None, limit_up_dict=None, limit_dn_dict=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
```

**协方差对齐逻辑**（L829–850）：
```python
if T in cov_dict:
    if all(c in cov_code_idx for c in period_codes):
        idx = [cov_code_idx[c] for c in period_codes]
        cov_period = cov_dict[T][np.ix_(idx, idx)]   # 子集化
    else:
        cov_period = _expand_cov_for_missing(...)    # 扩展填充
else:
    # F6-002: 协方差缺失，用微小单位矩阵占位
    cov_period = np.eye(len(period_codes)) * 1e-4
```

**F6-002 强制降级**（L870–879）：
```python
if not cov_available and result.fallback_level == 0:
    effective_fallback = 1
    effective_status   = result.solver_status + "+cov_missing"
```
即便 L1 求解成功（使用了 `1e-4·I` 的占位协方差），也强制将 `fallback_level` 改为 1，避免伪装成"有可靠 TE 约束的 L1 解"。

---

## 五、内部辅助函数

| 函数 | 位置 | 说明 |
|------|------|------|
| `_build_industry_matrix` | L92–121 | 构建行业指示矩阵 A（n_ind × n_stocks），A[i,j]=1 当股票 j 属于行业 i |
| `_build_portfolio_constraints` | L124–182 | 构建 L1/L2 共用的线性约束集合（全仓 + 纯多头 + 单股偏离 + 行业偏离 + 停牌/涨跌停）|
| `_postprocess_weights` | L185–214 | clip+归一化，处理 optimal_inaccurate 的数值误差 |
| `_try_solve` | L217–250 | 按 solver_order 轮试，返回第一个 ACCEPTABLE 结果 |
| `_expand_cov_for_missing` | L401–434 | 将 base_codes 的协方差矩阵扩展到 target_codes，缺失股票用对角均值填充 |

---

## 六、落盘产物（Artifacts）

`optimizer.py` **不直接写文件**，所有产物由 `run_portfolio_stage` 写入：

| 产物 | 路径 | 内容 |
|------|------|------|
| `weights_panel.parquet` | `runs/.../portfolio/weights_panel.parquet` | 行=调仓日，列=ts_code，目标权重 |
| `metadata_df.parquet` | `runs/.../portfolio/metadata_df.parquet` | 行=调仓日，列=fallback_level/solver_status 等 |

`metadata_df` 的关键列：

| 列名 | 含义 |
|------|------|
| `fallback_level` | 0=L1 成功, 1=L2, 2=L3（TopN 兜底）|
| `solver_status` | cvxpy 状态字符串，L3 时为 `'topn_fallback'`，TopN EW 模式为 `'topn_ew_forced'` |
| `cov_available` | 当期是否有真实协方差矩阵（F6-002）|
| `constraint_compliant` | L3 路径是否满足停牌/涨跌停约束（F6-001）|
| `w_prev_source` | 固定为 `'target_weight'`（F6-004）|
| `optimizer_mode` | `'topn_ew'` 或无此字段（QP 路径）|

---

## 七、相关测试

**测试文件**：`tests/test_optimizer.py`，覆盖范围最广的测试模块之一。

| 测试类 | 覆盖点 |
|--------|--------|
| `TestTopnEqualWeightHaltLock` | F6-001：停牌锁定、预算分配、动态 effective_topn、第一期无 w_prev |
| `TestTopnEqualWeightLimitDn` | F6-001：跌停锁定、停牌优先于跌停 |
| `TestOptimizeSinglePeriod` | L1 可行性、权重和=1、停牌约束、第一期不崩溃 |
| `TestMissingCovarianceNotReportedAsL1` | F6-002：协方差缺失期 fallback_level ≥ 1 |
| `TestCovarianceCacheValidation` | F6-003：非 PSD 矩阵被修复（调用 covariance.py）|
| `TestTopnLimitUpExclusion` | F6-001：涨停股在紧急退化路径中不获分配 |
| `TestForcedTopnEqualWeightAllPeriods` | TopN EW 全周期路径：metadata 格式、停牌/涨停约束 |
| `TestOptimizeAllPeriodsConstraintCompliant` | `constraint_compliant` 列存在且正常情况为 True |

**高优先级待补充测试（TODO）**：

| 场景 | 要点 |
|------|------|
| L1 → L2 降级触发 | 极紧 TE 约束下 L1 infeasible，验证 L2 接管 |
| L2 → L3 降级触发 | 构造不可行线性约束，验证 L3 接管 |
| 换手惩罚 `turnover_lambda` 生效 | 有上期权重时目标函数含 L1 惩罚项 |
| Bug-2A/2B 场景 | 基准权重 > single_max_dev 时 must_hold_set 被激活 |

---

## 八、失败与降级路径

完整三层 Fallback 路线（QP 路径）：

```
L1（完整 QP）
  ↓ 失败条件：求解器返回 infeasible / unbounded / 超时 /
              optimal_inaccurate 且 min_w < -0.005
L2（去掉 TE 约束的 LP）
  ↓ 失败条件：求解器返回 infeasible / optimal_inaccurate 且 min_w < -0.005
L3（TopN 等权，_topn_equal_weight）
  → 始终成功（降级到全股等权 / 紧急回退），但 constraint_compliant 可能 False

优先级 1：cov 缺失 → 跳过 L1（用占位 cov），即便 L1 成功也强制 fallback_level=1
优先级 2：force_l2=True → 直接跳过 L1
```

TopN EW 路径（`optimize_topn_equal_weight_all_periods`）不存在 Fallback 概念，`_topn_equal_weight` 本身始终返回权重（极端情况下等权退化）。

---

## 九、数据流图

### QP 路径（optimize_all_periods）

```
composite_panel[T]               benchmark_weights[T]
   (α 信号)                          (w_b 基准权重)
       │                                  │
       ▼                                  ▼
  alpha = composite_panel.loc[T].reindex(period_codes)
  w_b   = benchmark_weights[T]
  period_codes = list(w_b.index)
       │
       ├── cov_dict[T] 存在 ──────────────▼
       │                        cov_period = cov_dict[T][sub_idx]
       │                        cov_available = True
       │
       └── cov_dict[T] 不存在 ─────────────▼
                                cov_period = 1e-4 * I
                                cov_available = False
                                    │
                                    ▼
                        optimize_single_period(alpha, w_b, cov_period, ...)
                            │
                            ▼
                        L1 QP（含 TE 约束）
                            │ 失败
                            ▼
                        L2 LP（无 TE 约束）
                            │ 失败
                            ▼
                        L3 TopN EW（_topn_equal_weight）
                            │
                            ▼
                        OptimizeResult(weights, fallback_level, ...)
                            │
                            ▼（F6-002）
                        if cov_available=False and fallback_level==0:
                            fallback_level = 1（强制）
```

### TopN EW 路径（optimize_topn_equal_weight_all_periods）

```
composite_panel[T]               benchmark_weights[T]
       │                                  │
       ▼                                  ▼
  alpha_vec = composite_panel.loc[T].reindex(period_codes)
  w_b_vec   = benchmark_weights[T]
       │
       ├── halt_dict[T], limit_up_dict[T], limit_dn_dict[T]
       │
       ▼
  _topn_equal_weight(alpha_vec, n, topn, halt_idx, lim_up_idx, lim_dn_idx, ...)
       │
       ▼
  weights = pd.Series(w_vals, index=period_codes)
  meta = {fallback_level=2, solver_status='topn_ew_forced', cov_available=False, ...}
```

---

## 十、领域知识补充

### QP 问题的数学形式

优化器求解以下二次规划（L1 路径）：

$$\max_w \; \alpha^T w - \lambda_{TC} \|w - w_{prev}\|_1$$

$$\text{s.t.} \quad (w - w_b)^T \Sigma (w - w_b) \leq \frac{TE^2}{252}$$

$$|I_k(w - w_b)| \leq \delta_{ind} \quad \forall k \text{（行业）}$$

$$|w_i - w_{b,i}| \leq \delta_{single} \quad \forall i \text{（单股，停牌股除外）}$$

$$w_i \geq 0, \quad \sum_i w_i = 1$$

### 为何信号层的 TopN EW 优于 QP

当前诊断结论（详见 `current work/5.29/qp_constraint_diagnosis_conclusion.md`）：

1. **信号是 z-score**：横截面标准化后 α 的绝对值差异反映噪声，而非真实预测信心的量级差异
2. **QP 用 α 量级**：线性目标 `max α^T w` 将 α 的绝对差异转化为权重差异，噪声被放大
3. **TopN 仅用 α 排名**：对绝对值不敏感，选最高排名的 N 只，更鲁棒
4. **QP 的 TE 约束**：对于 IC_IR 信号（已被行业/市值中性化），TE 约束本不需要；但即便松约束 (TE=15%) 仍使 QP 变差，说明问题不在约束松紧，而在目标函数

结论：QP 优化器对**高噪声信号**是弱假设（假设绝对量级有意义），TopN 是**强先验**（只信排名）。当信号 IC 较低时，前者不如后者。

### BHB 恒等式与行业约束的关系

行业偏离约束 `|Σ_ind(w - w_b)| ≤ δ_ind` 防止优化器在行业上做大赌注。这在多因子选股中很重要：若因子组合碰巧在某年对金融板块有强信号，QP 会集中配置金融，导致超额收益来自"行业押注"而非"选股"。行业约束隔离了这两种来源，使 Brinson 归因中的选股效应更可信。
