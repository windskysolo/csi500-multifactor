"""
src/portfolio/covariance.py — 协方差矩阵估计
==========================================================

方法：Ledoit-Wolf 收缩估计（sklearn.covariance.LedoitWolf）

为什么不用样本协方差：
  500 只股票 × 252 天样本，变量数 > 样本数，协方差矩阵严重病态（病条件数）。
  LedoitWolf 向缩放单位矩阵的方向最优收缩，保证数值稳定的同时减少估计误差。

输出规格：
  - 日频协方差矩阵（非年化），单位 (日收益率)^2
  - 供优化器使用时，若需年化跟踪误差约束须在优化器侧乘以 252：
      (w-w_b)^T Σ_daily (w-w_b) ≤ TE_annual^2 / 252
  - 形状 (len(codes) × len(codes))，与传入 codes 列表顺序对应
  - 保证正定（最小特征值 ≥ EPSILON_DIAG）

时间对齐：
  仅使用 rebalance_date 当日及之前的 lookback_days 个交易日，不含未来数据。
  T 日收盘价（close_adj）当日收盘后即可用，无前视偏差。

停牌处理：
  close_adj NaN（停牌/未上市）→ 日收益率 NaN → 填 0（零收益假设）。
  与 factor_ivol_60d 使用的假设保持一致。

调用方注意：
  codes 应为当期可投资域股票，若包含历史上从未在 lookback 窗口内交易的股票，
  其收益率全为 0，协方差估计偏低但不影响正定性（LedoitWolf 处理）。
"""

import logging

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

from src.data.loader import load_daily_quote
from src.factors.price_factors import _all_trading_dates

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MIN_SAMPLE_DAYS: int   = 60        # 最少有效交易日数；不足时拒绝估计（抛出异常）
EPSILON_DIAG: float    = 1e-6      # 正定性修复：最小特征值阈值（足够大以避免 CVXPY ARPACK 数值问题）
DEFAULT_LOOKBACK_DAYS: int = 252   # 默认回看窗口（约 1 个自然年）


# ---------------------------------------------------------------------------
# 正定性保证
# ---------------------------------------------------------------------------

def _ensure_positive_definite(cov: np.ndarray, eps: float = EPSILON_DIAG) -> np.ndarray:
    """
    检查协方差矩阵的正定性；若最小特征值 < eps，加对角扰动修复。

    修复量 δ = eps - min_eigval（使修复后最小特征值恰好等于 eps）。
    使用 eigvalsh（专为对称矩阵优化）：比 eigvals 更快且数值更稳定。

    Args:
        cov: 对称半正定方阵（N×N，float64）
        eps: 最小特征值阈值（默认 1e-8）
    Returns:
        最小特征值 ≥ eps 的协方差矩阵（原地修改副本）
    """
    min_eigval = float(np.linalg.eigvalsh(cov).min())
    if min_eigval < eps:
        delta = eps - min_eigval
        cov = cov + delta * np.eye(cov.shape[0])
        log.info(
            "协方差正定性修复：δ=%.2e（修复前最小特征值=%.2e，修复后≥%.2e）",
            delta, min_eigval, eps,
        )
    return cov


def validate_and_repair_covariance(
    cov: np.ndarray,
    eps: float = EPSILON_DIAG,
    symmetry_tol: float = 1e-6,
) -> tuple[np.ndarray, bool, float, float]:
    """
    F6-003: 验证协方差矩阵的对称性和正定性，必要时自动修复。

    供缓存读取方调用，保证读取的矩阵与重新估计时经过相同质量检查。

    Args:
        cov:          (N×N) 协方差矩阵（float64）
        eps:          正定性阈值；最小特征值 < eps 时触发对角扰动修复
        symmetry_tol: 对称性容忍度；max|Σ - Σ^T| 超过此值记录 warning

    Returns:
        (repaired_cov, was_already_valid, min_eig_before, diag_delta)
        was_already_valid=True  表示原矩阵无需修复
        min_eig_before:         修复前最小特征值
        diag_delta:             实际加入的对角扰动量（0 = 无修复）
    """
    # 对称性检查（不修复，只记录）
    sym_err = float(np.max(np.abs(cov - cov.T)))
    if sym_err > symmetry_tol:
        log.warning(
            "validate_and_repair_covariance: 矩阵不对称 (max|Σ-Σᵀ|=%.2e)，"
            "强制对称化后继续",
            sym_err,
        )
        cov = (cov + cov.T) / 2.0

    min_eig_before = float(np.linalg.eigvalsh(cov).min())
    was_valid = min_eig_before >= eps
    diag_delta = 0.0

    if not was_valid:
        diag_delta = eps - min_eig_before
        cov = cov + diag_delta * np.eye(cov.shape[0])
        log.info(
            "validate_and_repair_covariance: 正定修复 δ=%.2e"
            "（修复前最小特征值=%.2e）",
            diag_delta, min_eig_before,
        )

    return cov, was_valid, min_eig_before, diag_delta


# ---------------------------------------------------------------------------
# 单期协方差估计
# ---------------------------------------------------------------------------

def estimate_covariance_lw(
    codes: list[str],
    rebalance_date: pd.Timestamp,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> np.ndarray:
    """
    用 Ledoit-Wolf 收缩估计调仓日 T 的日频协方差矩阵。

    时间对齐：仅用 rebalance_date 当日及之前的 lookback_days 个交易日，无前视偏差。

    Args:
        codes:          股票代码列表（须与优化器中的排列顺序完全一致）
        rebalance_date: 调仓日 T
        lookback_days:  回看窗口长度（交易日数，默认 252）
    Returns:
        (len(codes) × len(codes)) 的日频协方差矩阵（float64，正定）
    Raises:
        ValueError: 可用样本天数 < MIN_SAMPLE_DAYS，或指定日期前无交易日
        FileNotFoundError: daily_quote.parquet 不存在
    """
    all_dates = _all_trading_dates()
    dates_before = all_dates[all_dates <= rebalance_date]

    if len(dates_before) == 0:
        raise ValueError(
            f"estimate_covariance_lw: {rebalance_date.date()} 之前无可用交易日"
        )

    window_dates = dates_before[-lookback_days:]
    start, end   = window_dates[0], window_dates[-1]

    dq = load_daily_quote(start, end, codes=codes)
    if dq.empty:
        raise ValueError(
            f"estimate_covariance_lw: {start.date()}~{end.date()} 无行情数据"
        )

    # 构建 close_adj 面板并计算日收益率
    close_adj = (
        dq["close_adj"]
        .unstack("ts_code")
        .reindex(columns=codes)            # 确保列顺序与 codes 一致
    )
    daily_ret = close_adj.pct_change(fill_method=None).iloc[1:]   # 去掉第一行（全 NaN）
    daily_ret = daily_ret.fillna(0.0)             # 停牌 → 零收益

    n_samples = len(daily_ret)
    if n_samples < MIN_SAMPLE_DAYS:
        raise ValueError(
            f"estimate_covariance_lw: 有效样本仅 {n_samples} 天"
            f"（需 ≥ {MIN_SAMPLE_DAYS}），协方差估计不可靠"
        )

    lw = LedoitWolf()
    lw.fit(daily_ret.values)         # 输入 (T × N)
    cov = lw.covariance_             # (N × N) 日频协方差

    cov = _ensure_positive_definite(cov)

    log.debug(
        "estimate_covariance_lw: %s，%d 只股票，%d 样本天，LW 收缩系数=%.4f",
        rebalance_date.date(), len(codes), n_samples, lw.shrinkage_,
    )
    return cov


# ---------------------------------------------------------------------------
# 批量协方差估计
# ---------------------------------------------------------------------------

def batch_estimate_covariance(
    codes: list[str],
    rebalance_dates: list[pd.Timestamp],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[pd.Timestamp, np.ndarray]:
    """
    批量估计所有调仓日的日频协方差矩阵。

    失败的日期记录警告并跳过（不传播异常），保证批量任务的健壮性。
    下游优化器收到空 dict（或缺少某日期）时应触发 Fallback 降级路线。

    Args:
        codes:           股票代码列表（须与优化器中的排列顺序完全一致）
        rebalance_dates: 调仓日列表（有序升序）
        lookback_days:   每期回看窗口（交易日数）
    Returns:
        dict，key=成功估计的调仓日，value=(N×N) 日频协方差矩阵（正定）
    """
    result: dict[pd.Timestamp, np.ndarray] = {}
    failed = 0

    for T in rebalance_dates:
        try:
            result[T] = estimate_covariance_lw(codes, T, lookback_days)
        except Exception as exc:
            log.warning(
                "batch_estimate_covariance: %s 估计失败（%s），跳过",
                T.date(), exc,
            )
            failed += 1

    log.info(
        "batch_estimate_covariance: 完成 %d/%d 期，失败 %d 期",
        len(result), len(rebalance_dates), failed,
    )
    return result
