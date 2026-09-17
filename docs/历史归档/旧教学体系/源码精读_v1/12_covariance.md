# 12 — 协方差估计：covariance.py 逐行精讲

> 对应源文件：`src/portfolio/covariance.py`（236 行）  
> 前置依赖：`11_combiner.md`（理解信号输出），`13_optimizer.md`（理解协方差的用途）  
> 核心任务：估计 500 只股票的收益协方差矩阵，保证数值正定供优化器使用

---

## 一、为什么协方差矩阵是难题

组合优化的跟踪误差约束是：`(w - w_b)ᵀ Σ (w - w_b) ≤ TE²/252`

其中 Σ 是 N×N 协方差矩阵（N≈500）。

**问题**：用 252 天日收益率估计 500×500 协方差矩阵，样本数（252）< 变量数（500），矩阵**奇异（不可逆）**，数值计算极不稳定，最小特征值可能为 0 或负数。

**解决方案**：Ledoit-Wolf 收缩估计 —— 将样本协方差向缩放单位矩阵方向收缩，自动确定最优收缩系数，保证矩阵正定且数值稳定。

---

## 二、常量（第 46-48 行）

```python
MIN_SAMPLE_DAYS: int   = 60        # 最少有效交易日数
EPSILON_DIAG: float    = 1e-6      # 正定性阈值：最小特征值 < 此值时修复
DEFAULT_LOOKBACK_DAYS: int = 252   # 默认回看窗口（约 1 个自然年）
```

`EPSILON_DIAG = 1e-6`：这不是 LW 收缩不够，而是 CVXPY 内部用 ARPACK 求特征值时的数值容忍度。LW 保证理论正定，但浮点运算后最小特征值可能是 `1e-9` 这样的极小正数，ARPACK 迭代可能认为它是 0。加对角扰动把最小特征值保证在 `1e-6` 以上。

---

## 三、正定性保证函数

### 3.1 `_ensure_positive_definite`（第 55-76 行）

```python
def _ensure_positive_definite(cov: np.ndarray, eps: float = EPSILON_DIAG) -> np.ndarray:
    min_eigval = float(np.linalg.eigvalsh(cov).min())
    if min_eigval < eps:
        delta = eps - min_eigval
        cov = cov + delta * np.eye(cov.shape[0])
        log.info("协方差正定性修复：δ=%.2e（修复前最小特征值=%.2e，修复后≥%.2e）", ...)
    return cov
```

**`eigvalsh` vs `eigvals`**：`eigvalsh` 专为**对称**（Hermitian）矩阵设计，利用对称性只计算实特征值，比通用 `eigvals` 快约 3-5 倍，数值也更稳定。协方差矩阵一定是对称正半定的，用 `eigvalsh` 是正确选择。

修复量 `δ = eps - min_eigval`：精确让最小特征值从 `min_eigval` 抬升到 `eps`，不多加（避免过度收缩）。

### 3.2 `validate_and_repair_covariance`（第 79-123 行）

这是为**缓存读取场景**设计的更完整版本，比 `_ensure_positive_definite` 多一步对称性检查：

```python
sym_err = float(np.max(np.abs(cov - cov.T)))
if sym_err > symmetry_tol:
    log.warning("矩阵不对称 (max|Σ-Σᵀ|=%.2e)，强制对称化后继续", sym_err)
    cov = (cov + cov.T) / 2.0  # 强制对称化
```

为什么从缓存读出的矩阵可能不对称？Parquet 存储浮点数时有截断，`Σ[i,j]` 和 `Σ[j,i]` 可能差 `1e-15` 量级。`(Σ + Σᵀ)/2` 强制对称化不改变数值含义，只是修复浮点截断误差。

返回四元组 `(repaired_cov, was_already_valid, min_eig_before, diag_delta)` 便于调用方记录日志，知道是否做了修复以及修复量是多少。

---

## 四、单期协方差估计（第 130-193 行）

```python
def estimate_covariance_lw(
    codes: list[str],
    rebalance_date: pd.Timestamp,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> np.ndarray:
```

### 4.1 时间窗口选取

```python
all_dates = _all_trading_dates()
dates_before = all_dates[all_dates <= rebalance_date]
window_dates = dates_before[-lookback_days:]
start, end   = window_dates[0], window_dates[-1]
```

`all_dates[all_dates <= rebalance_date]`：取调仓日当天及之前的交易日，符合 T 日收盘后可用收盘价的时间约束。

`[-lookback_days:]`：取最近 252 个交易日，若历史不足则取全部。

### 4.2 停牌处理

```python
daily_ret = close_adj.pct_change(fill_method=None).iloc[1:]
daily_ret = daily_ret.fillna(0.0)   # 停牌 → 零收益
```

停牌日收盘价经过 `ffill` 填充，所以 `pct_change` 得到 0（不是 NaN）。  
但这里 `fill_method=None` 保留 NaN（防止 `pct_change` 内部做不想要的填充），再用 `fillna(0.0)` 统一处理。

零收益假设对停牌股是合理的：停牌期间无法交易，收益设为 0 意味着不贡献协方差，与回测引擎的处理一致（停牌锁定持仓）。

### 4.3 LedoitWolf 估计

```python
lw = LedoitWolf()
lw.fit(daily_ret.values)  # 输入 (T × N)
cov = lw.covariance_       # (N × N) 日频协方差
```

`lw.shrinkage_`（收缩系数 ∈ [0, 1]）记录在 debug 日志里。收缩系数越高，说明样本协方差越不可靠，收缩越强。实践中 500 只股票、252 天样本时，收缩系数通常在 0.3-0.7。

### 4.4 代码列顺序

```python
close_adj = (
    dq["close_adj"]
    .unstack("ts_code")
    .reindex(columns=codes)   # ← 确保列顺序与 codes 一致
)
```

这是容易忽略的细节：优化器中协方差矩阵的行/列顺序必须与 `alpha.index`（codes 列表）完全一致，否则约束矩阵会错位。`reindex(columns=codes)` 强制对齐。

---

## 五、批量估计（第 200-235 行）

```python
def batch_estimate_covariance(
    codes: list[str],
    rebalance_dates: list[pd.Timestamp],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[pd.Timestamp, np.ndarray]:
    result = {}
    failed = 0
    for T in rebalance_dates:
        try:
            result[T] = estimate_covariance_lw(codes, T, lookback_days)
        except Exception as exc:
            log.warning("batch_estimate_covariance: %s 估计失败（%s），跳过", T.date(), exc)
            failed += 1
    return result
```

失败时跳过而不是抛出异常，保证批量任务的健壮性。下游优化器（`optimize_all_periods`）对缺失的协方差有专门的 Fallback 处理（F6-002：降级为 L2）。

---

## 六、输出规格

| 属性 | 说明 |
|------|------|
| 形状 | `(len(codes) × len(codes))` |
| 单位 | **日频**协方差，即 `(日收益率)²` |
| 正定性 | 保证最小特征值 ≥ `EPSILON_DIAG = 1e-6` |
| 列顺序 | 与传入 `codes` 列表完全对应 |

**重要：日频 vs 年化**  
协方差矩阵输出是日频的，优化器中跟踪误差约束用年化目标，所以约束写法是：

```
(w - w_b)ᵀ Σ_daily (w - w_b) ≤ TE_annual² / 252
```

252 是除数（日化），不是乘数。

---

## 七、与优化器的衔接

```python
# optimizer.py 中的约束：
te_limit = (config.te_target_annual ** 2) / TRADING_DAYS_PER_YEAR   # TE²/252
cp.quad_form(w - w_b_vec, cp.psd_wrap(cov)) <= te_limit
```

`cp.psd_wrap(cov)` 告知 CVXPY 矩阵已经保证正定，跳过内部特征值验证（ARPACK 在 500×500 矩阵上常迭代不收敛）。这是性能优化，前提是 `_ensure_positive_definite` 已经保证了正定性。
