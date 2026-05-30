# 15 — covariance.py：Ledoit-Wolf 协方差矩阵估计

> 对应源文件：`src/portfolio/covariance.py`
> 实际行数：**236 行**（计划快照为 160，新增 `validate_and_repair_covariance` F6-003，撰写前已核实）
> 处理方式：**沿用 + 核查更新**
> 所属 Part：Part 5 — 组合构建层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 4  组合构建    ← 本文件（covariance.py）
Layer 4  组合构建    optimizer.py（消费协方差矩阵）
Layer 3  信号合成    src/signal/（combiner / ridge_decay）
Layer 0  数据基础    src/data/loader.py（load_daily_quote）
```

`covariance.py` 的职责是**为优化器提供正定的日频协方差矩阵**。它不参与任何信号计算，也不写磁盘，只做两件事：

1. 从历史行情数据估计协方差（Ledoit-Wolf 收缩）
2. 保证输出矩阵严格正定（`_ensure_positive_definite`）

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游 | `src/data/loader.load_daily_quote` | 加载历史后复权收盘价 |
| 上游 | `src/factors/price_factors._all_trading_dates` | 确定 lookback 窗口的交易日 |
| 下游 | `src/portfolio/optimizer.optimize_all_periods` | 传入 cov_dict 供 QP 使用 |
| 下游 | `src/pipeline/stages.run_portfolio_stage` | Stage 层调度，当 QP 路径时调用 |

### 与计划快照的差异

| 变化 | 说明 |
|------|------|
| 新增 `validate_and_repair_covariance` | F6-003：供缓存读取方调用，验证矩阵的对称性和正定性并修复 |
| 行数从 160 → 236 | 主要增量来自 `validate_and_repair_covariance` 的完整 docstring 和对称性检查逻辑 |

---

## 二、时间对齐与 PIT 假设

`covariance.py` 的时间对齐规则非常简单，也是整个项目最少 PIT 风险的模块之一：

```
rebalance_date T
    ↓
取 ≤ T 的最近 lookback_days 个交易日
    ↓
用 close_adj（T 日收盘后可见）计算日收益率
    ↓
输出：日频协方差矩阵（T 日可用）
```

**关键约束**：

1. `dates_before = all_dates[all_dates <= rebalance_date]`（L151）：严格 ≤ T，不含 T+1 的任何信息
2. `close_adj`（后复权收盘价）在 T 日收盘后即可知，无前视偏差
3. 停牌 → 日收益率 NaN → `fillna(0.0)`（零收益假设）

**单位**：输出为**日频协方差矩阵**，单位 (日收益率)²。优化器侧使用年化 TE 约束时需除以 252：
```
(w-w_b)^T Σ_daily (w-w_b) ≤ TE_annual² / 252
```

---

## 三、模块顶部：导入与常量

```python
# L46–48
MIN_SAMPLE_DAYS: int    = 60        # 最少有效交易日数；不足时拒绝估计（抛出异常）
EPSILON_DIAG: float     = 1e-6      # 正定性修复：最小特征值阈值
DEFAULT_LOOKBACK_DAYS: int = 252    # 默认回看窗口（约 1 个自然年）
```

**`EPSILON_DIAG = 1e-6` 的选择**：比普通数值容差（1e-8~1e-10）大，原因是 CVXPY 的 ARPACK 特征值验证在 500×500 矩阵上的数值不稳定性较高，`1e-6` 能可靠阻止"矩阵理论正定但 CVXPY 误判为不正定"的情况。

---

## 四、核心函数逐一解析

### 4.1 `estimate_covariance_lw` — 单期 Ledoit-Wolf 估计

```python
# L130–193
def estimate_covariance_lw(
    codes: list[str],
    rebalance_date: pd.Timestamp,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> np.ndarray:
```

**执行逻辑**（逐阶段）：

**阶段 1：确定时间窗口**（L150–159）
```python
all_dates     = _all_trading_dates()
dates_before  = all_dates[all_dates <= rebalance_date]
window_dates  = dates_before[-lookback_days:]     # 取最近 lookback_days 个交易日
start, end    = window_dates[0], window_dates[-1]
```
二分法取 ≤ T 的日期，再截取最后 `lookback_days` 个。若 T 之前无交易日，抛出 `ValueError`。

**阶段 2：加载行情并构建收益率矩阵**（L161–181）
```python
close_adj = (
    dq["close_adj"]
    .unstack("ts_code")
    .reindex(columns=codes)            # 确保列顺序与 codes 一致
)
daily_ret = close_adj.pct_change(fill_method=None).iloc[1:]   # 去掉第一行（全 NaN）
daily_ret = daily_ret.fillna(0.0)             # 停牌 → 零收益
```
`fill_method=None` 告知 pandas 不进行前向填充（停牌期 NaN 保持到填 0）。`.iloc[1:]` 去除第一行（pct_change 产生的全 NaN 行）。

**阶段 3：样本数检查**（L176–181）
```python
if n_samples < MIN_SAMPLE_DAYS:
    raise ValueError(...)
```
不足 60 个交易日时拒绝估计，上游 `batch_estimate_covariance` 会捕获此异常并跳过。

**阶段 4：LedoitWolf 拟合**（L183–185）
```python
lw = LedoitWolf()
lw.fit(daily_ret.values)   # 输入形状 (T × N)
cov = lw.covariance_       # 输出 (N × N) 日频协方差
```
`lw.shrinkage_` 是自动学习的收缩系数，记录在 debug 日志中。

**阶段 5：正定性修复**（L187）
```python
cov = _ensure_positive_definite(cov)
```

**关键注意事项**：
- `codes` 参数决定输出矩阵的行/列顺序，必须与优化器中的 `codes` 列表完全一致
- `stop_pad_adj`（NaN → 0）改变了样本分布，但维持了矩阵维度，LedoitWolf 能正确处理
- `lw.shrinkage_` 接近 1.0 说明样本严重不足（接近单位矩阵），需关注

---

### 4.2 `batch_estimate_covariance` — 批量估计

```python
# L200–235
def batch_estimate_covariance(
    codes: list[str],
    rebalance_dates: list[pd.Timestamp],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[pd.Timestamp, np.ndarray]:
```

**设计决策**：逐期调用 `estimate_covariance_lw`，失败时记录 warning 并跳过，**不传播异常**。

```python
try:
    result[T] = estimate_covariance_lw(codes, T, lookback_days)
except Exception as exc:
    log.warning("batch_estimate_covariance: %s 估计失败（%s），跳过", T.date(), exc)
    failed += 1
```

返回的 `dict` 可能缺少某些日期（估计失败的）。下游 `optimize_all_periods` 中对缺失 T 的处理：
```python
# optimizer.py L843–850
if not cov_available:
    cov_period = np.eye(len(period_codes)) * 1e-4  # 占位，触发 F6-002
```
即降级到 L2（见文档 `16_optimizer.md`）。

---

### 4.3 `validate_and_repair_covariance` — 缓存验证与修复（F6-003）

```python
# L79–123
def validate_and_repair_covariance(
    cov: np.ndarray,
    eps: float = EPSILON_DIAG,
    symmetry_tol: float = 1e-6,
) -> tuple[np.ndarray, bool, float, float]:
```

**设计目的**：协方差矩阵可能被序列化（`.npy`、`.parquet`）后读取，浮点精度损失可能破坏正定性或对称性。此函数供读取方调用，保证与重新估计时经过相同质量检查。

**两步检查**：

1. **对称性检查**（不修复，强制对称化后继续）：
```python
sym_err = float(np.max(np.abs(cov - cov.T)))
if sym_err > symmetry_tol:
    log.warning(...)
    cov = (cov + cov.T) / 2.0   # 强制对称化
```

2. **正定性修复**（与 `_ensure_positive_definite` 相同逻辑，但额外返回 was_valid）：
```python
if not was_valid:
    diag_delta = eps - min_eig_before
    cov = cov + diag_delta * np.eye(cov.shape[0])
```

**返回值**：`(repaired_cov, was_already_valid, min_eig_before, diag_delta)`
- `was_already_valid=True` 表示原矩阵合法，无需修复
- `diag_delta=0.0` 表示未添加对角扰动

---

## 五、内部辅助函数

| 函数 | 位置 | 说明 |
|------|------|------|
| `_ensure_positive_definite` | L55–76 | 基础正定修复：计算最小特征值，若 < eps 则加对角扰动 `δ·I`；内部调用 `np.linalg.eigvalsh`（专为对称矩阵优化）|

`_ensure_positive_definite` 与 `validate_and_repair_covariance` 的区别：

| | `_ensure_positive_definite` | `validate_and_repair_covariance` |
|--|--|--|
| 用途 | 估计流程内部，快速修复 | 缓存读取方调用，完整验证 |
| 对称性检查 | 无 | 有（记录并强制对称化）|
| 返回值 | 修复后矩阵 | `(矩阵, was_valid, min_eig, delta)` 四元组 |
| 调用方 | `estimate_covariance_lw` | 外部调用（测试、缓存读取）|

---

## 六、落盘产物（Artifacts）

`covariance.py` **不直接写任何文件**。协方差矩阵以 `dict[pd.Timestamp, np.ndarray]` 形式在内存中传递给优化器。

若需缓存（性能优化目的），由调用方（如 `run_portfolio_stage`）负责：
- 写入：`np.save(path, cov)` 或 `np.savez_compressed`
- 读取后：必须调用 `validate_and_repair_covariance` 验证

---

## 七、相关测试

**测试文件**：`tests/test_optimizer.py` 中的 `TestCovarianceCacheValidation` 类

| 测试 | 函数 | 验证内容 |
|------|------|---------|
| 合法 PSD 矩阵无需修复 | `test_valid_psd_returns_was_valid_true` | `was_already_valid=True`, `diag_delta=0` |
| 非 PSD 矩阵被修复 | `test_non_psd_is_repaired` | `was_valid=False`, 修复后最小特征值 ≥ 0 |
| 不对称矩阵被强制对称化 | `test_asymmetric_matrix_is_symmetrized` | 对称误差 < 1e-12 |
| 单位矩阵通过验证 | `test_identity_matrix_passes_validation` | `was_valid=True`, `delta=0` |

**高优先级待补充测试（TODO）**：

| 场景 | 要点 |
|------|------|
| 停牌导致全行为零收益列 | 全零列对 LedoitWolf 的影响（收缩系数是否异常升高？）|
| `lookback_days=60` 边界 | n_samples=59 时应抛 `ValueError`，n_samples=60 时应通过 |
| `batch_estimate_covariance` 部分失败 | 返回 dict 中某些日期缺失，下游不崩溃 |

---

## 八、失败与降级路径

| 失败场景 | `estimate_covariance_lw` 行为 | `batch_estimate_covariance` 行为 |
|---------|----------------------------|---------------------------------|
| 样本天数 < 60 | `raise ValueError` | 捕获，记录 warning，跳过该期 |
| `daily_quote` 数据为空 | `raise ValueError` | 捕获，记录 warning，跳过该期 |
| T 之前无交易日 | `raise ValueError` | 捕获，记录 warning，跳过该期 |
| LedoitWolf 数值不收敛 | 理论上不抛（sklearn 内部处理） | 不需要 catch |

**下游降级**：`optimize_all_periods` 检测到 `T not in cov_dict` 时，用 `1e-4 · I` 占位并强制 `fallback_level ≥ 1`（F6-002，详见 `16_optimizer.md`）。

---

## 九、数据流图

```
rebalance_date T
    │
    ▼
_all_trading_dates()
    │
    ▼
dates_before = all_dates[all_dates <= T]
window_dates = dates_before[-lookback_days:]
start, end = window_dates[0], window_dates[-1]
    │
    ▼
load_daily_quote(start, end, codes=codes)
    │
    ▼
close_adj.unstack("ts_code")
.reindex(columns=codes)          # 列顺序与 codes 一致
    │
    ▼
pct_change(fill_method=None)     # 日收益率
.iloc[1:]                        # 去掉第一行 NaN
.fillna(0.0)                     # 停牌 → 零收益
    │
    ▼
[n_samples < MIN_SAMPLE_DAYS] → raise ValueError
    │
    ▼
LedoitWolf().fit(daily_ret)
cov = lw.covariance_             # (N×N) 日频协方差（初步）
    │
    ▼
_ensure_positive_definite(cov)   # 若最小特征值 < EPSILON_DIAG 则加 δ·I
    │
    ▼
(N×N) 正定日频协方差矩阵
```

---

## 十、领域知识补充

### 为何 500×500 样本协方差不可用

**样本协方差**：若样本数 T = 252，股票数 N = 500，则 T < N，协方差矩阵秩 ≤ 252（而非 500），不可逆（奇异）。即便不奇异（T > N），条件数仍极大（> 10⁶），求逆后误差被放大。

$$\hat{\Sigma}_{ij} = \frac{1}{T-1} \sum_{t=1}^T (r_{it} - \bar{r}_i)(r_{jt} - \bar{r}_j)$$

**Ledoit-Wolf 收缩**：向"有结构的目标矩阵"（此处为 `μ · I`，单位阵的倍数）收缩：

$$\hat{\Sigma}_{LW} = (1 - \hat{\alpha}) \cdot \hat{\Sigma}_{sample} + \hat{\alpha} \cdot \hat{\mu} \cdot I$$

`scikit-learn` 的 `LedoitWolf` 用 Oracle Approximating Shrinkage（OAS）或 Ledoit-Wolf 解析公式（两者均渐近最优）自动计算最优收缩系数 $\hat{\alpha}$，无需手动调参。

`lw.shrinkage_` 接近 1.0 意味着估计器几乎完全依赖单位矩阵，说明样本不足或股票间相关性极低。

### 停牌股零收益假设的影响

停牌期将 NaN 填 0 会：
1. 人为降低该股的波动率（方差偏低）
2. 降低该股与其他股票的协方差（偏向零）
3. 综合效果：该股的协方差行/列偏向"不相关低波动"状态

这与回测引擎中停牌股"不交易、估值用上期收盘价"的假设一致（实质上停牌期间的市值贡献也被冻结），所以该假设在本项目内部是一致的。

### 日频协方差 vs 月频协方差

本模块估计**日频**协方差，目的是与 cvxpy 中的 TE 约束（也用日频）一致：

```
TE_annual² / 252 = TE_daily_target_var
(w-w_b)^T Σ_daily (w-w_b) ≤ TE_daily_target_var
```

若使用月频协方差，需要在 TE 约束中使用 `/12`，且月度协方差的估计噪声更大（样本点少 21 倍）。日频估计是业内主流做法。
