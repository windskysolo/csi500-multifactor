"""
src/factors/preprocess.py — 因子横截面预处理流水线
==========================================================

每个因子在每个调仓日 T 的横截面上独立执行 5 步：

  Step 1  行业/状态过滤   金融股财务因子置 NaN（银行/非银财务比率失真）
  Step 2  MAD 去极值      边界只用 T 日横截面的非 NaN 值计算，不用历史全样本
  Step 3  Z-score 标准化  基于非 NaN 值的均值和标准差，NaN 保持 NaN
  Step 4  行业+市值中性化  OLS 回归取残差；NaN 保持 NaN，不参与回归
  Step 5  残差再标准化     消除回归残差量纲差异，维持均值 0、std 1

关键约束（违反视为 bug）：
  - NaN 在整个流水线中保持 NaN，本模块不做任何填充
  - 送优化器前填充 NaN 为 0 的操作在组合优化模块完成，与此处解耦
  - 单因子 IC 检验时直接 dropna，不在此处填充
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from src import config as cfg

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# MAD 与正态分布标准差的一致性系数：sigma ≈ 1.4826 × MAD（对正态分布成立）
_MAD_CONSISTENCY_FACTOR: float = 1.4826

# 以下常量已迁移至 src/config.py，直接引用保持单一来源：
# cfg.FINANCIAL_SECTOR_CODES — 金融板块行业代码（申万一级 SW2021）
# cfg.MIN_NEUTRALIZE_STOCKS  — 中性化最少有效股票数（默认 30）


# ---------------------------------------------------------------------------
# Step 2：MAD 去极值
# ---------------------------------------------------------------------------

def winsorize_mad(series: pd.Series, n: float = 3.0) -> pd.Series:
    """
    MAD 法横截面去极值（仅用 T 日横截面计算边界，禁止用历史全样本）。

    边界 = median ± n × 1.4826 × MAD
    超出边界的值被截断到边界（Winsorize），NaN 位置保持 NaN 不变。

    MAD=0 退化处理：当超过 50% 的值相同时，MAD 为 0，此时改用 3σ 截断；
    若 std 也为 0（所有值完全相同），则无需截断，直接返回。

    Args:
        series: 横截面因子值（允许含 NaN，至少有 1 个非 NaN 值）
        n:      截断系数（默认 3.0）
    Returns:
        去极值后的 Series（与输入等长，NaN 位置不变）
    Raises:
        ValueError: series 全为 NaN（无法计算任何统计量）
    """
    valid = series.dropna()
    if valid.empty:
        raise ValueError(
            "winsorize_mad 收到全 NaN 的 series，请检查数据完整性"
        )

    median = valid.median()
    mad = (valid - median).abs().median()

    if mad == 0:
        # MAD=0 说明中位数两侧超过一半的值完全相同，退化为 std 截断
        std = valid.std(ddof=1)
        if std < 1e-10:
            # 所有值几乎完全相同，无需截断
            return series.copy()
        bound = 3.0 * std
        log.warning(
            "winsorize_mad: MAD=0，退化为 3σ 截断（std=%.4f, bound=%.4f）", std, bound
        )
    else:
        bound = n * _MAD_CONSISTENCY_FACTOR * mad

    lower = median - bound
    upper = median + bound
    return series.clip(lower=lower, upper=upper)


# ---------------------------------------------------------------------------
# Step 3：Z-score 标准化
# ---------------------------------------------------------------------------

def standardize(series: pd.Series) -> pd.Series:
    """
    横截面 Z-score 标准化（均值 0、标准差 1）。

    基于非 NaN 值计算均值和标准差，NaN 位置保持 NaN。

    Args:
        series: 横截面因子值（允许含 NaN）
    Returns:
        标准化后的 Series；若非 NaN 值不足 2 个或 std≈0，返回全 NaN
    """
    valid = series.dropna()

    if len(valid) < 2:
        log.warning(
            "standardize: 非 NaN 样本数 %d < 2，返回全 NaN", len(valid)
        )
        return pd.Series(np.nan, index=series.index)

    mu  = valid.mean()
    std = valid.std(ddof=1)

    if std < 1e-10:
        log.warning(
            "standardize: std≈0（%.2e），因子值几乎全部相同，返回全 NaN", std
        )
        return pd.Series(np.nan, index=series.index)

    return (series - mu) / std


# ---------------------------------------------------------------------------
# Step 4：行业 + 市值中性化
# ---------------------------------------------------------------------------

def neutralize(
    factor:   pd.Series,
    industry: pd.Series,
    log_mv:   pd.Series,
    _diag:    dict | None = None,
) -> pd.Series:
    """
    行业 + log(自由流通市值) 中性化，返回 OLS 残差。

    回归模型：factor ~ C(industry) + log_mv
    在三个 Series 均非 NaN 的股票子集上做 OLS，残差 reindex 回原始索引。
    任一输入为 NaN 的股票，其残差也为 NaN（不参与回归）。

    时间对齐假设：
      factor / industry / log_mv 均来自同一调仓日 T 的横截面，
      log_mv 使用 log(自由流通市值)（free_float_mv，单位：元），
      而非 log(总市值)（total_mv 单位万元，量纲不同）。

    Args:
        factor:   已去极值、标准化的因子值（横截面）
        industry: 申万一级行业代码（字符串，如 '801180.SI'）
        log_mv:   log(自由流通市值)，来自 daily_basic.log_free_float_mv
    Returns:
        OLS 残差 Series，index 与 factor 相同；
        样本不足时原样返回 factor（记录警告，不抛出异常）
    """
    # 先用原始 Series 建 valid_mask，保证 NaN industry 在 astype(str) 前就被排除。
    # 不能先 astype(str) 再 dropna()：NaN → "nan" 字符串后 dropna 无法识别，
    # 导致 industry 缺失的股票带着 "nan" 哑变量进入 OLS，违反"任一输入为 NaN 则输出 NaN"契约。
    valid_mask = factor.notna() & industry.notna() & log_mv.notna()
    df = pd.DataFrame({
        "y":        factor[valid_mask],
        "industry": industry[valid_mask].astype(str),
        "log_mv":   log_mv[valid_mask],
    })

    if _diag is not None:
        _diag["n_neutralize_valid"] = len(df)

    if len(df) < cfg.MIN_NEUTRALIZE_STOCKS:
        log.warning(
            "neutralize: 有效样本数 %d < %d，跳过中性化，返回原因子值",
            len(df), cfg.MIN_NEUTRALIZE_STOCKS,
        )
        if _diag is not None:
            _diag["skipped_neutralize"] = True
        return factor

    if _diag is not None:
        _diag["skipped_neutralize"] = False

    try:
        model = smf.ols("y ~ C(industry) + log_mv", data=df).fit()
        # model.resid 是 numpy array，需显式绑定到 df.index（ts_code）
        resid = pd.Series(np.array(model.resid), index=df.index)
        return resid.reindex(factor.index)
    except Exception as exc:
        log.warning("neutralize: OLS 拟合失败（%s），返回原因子值", exc)
        if _diag is not None:
            _diag["skipped_neutralize"] = True
        return factor


# ---------------------------------------------------------------------------
# 完整流水线
# ---------------------------------------------------------------------------

def preprocess_factor(
    raw:              pd.Series,
    industry:         pd.Series,
    log_mv:           pd.Series,
    fin_sector_codes: Optional[frozenset[str]] = None,
    _diag:            dict | None = None,
) -> pd.Series:
    """
    完整因子预处理流水线（5 步）。

    NaN 在整个流水线中保持 NaN，不在此处填充。
    组合优化模块在将 alpha 传入 cvxpy 前统一将 NaN 填为 0（横截面均值）。

    Args:
        raw:              原始横截面因子值（调仓日 T 的截面）
        industry:         申万一级行业代码（与 raw 共享 ts_code index）
        log_mv:           log(自由流通市值)（与 raw 共享 ts_code index）
        fin_sector_codes: 财务因子传 FINANCIAL_SECTOR_CODES（银行/非银置 NaN）；
                          量价因子传 None（不过滤）
    Returns:
        预处理后的因子值 Series（残差再标准化）；缺失位置仍为 NaN
    """
    processed = raw.copy()

    # Step 1：金融股过滤（仅财务因子需要；量价因子传 fin_sector_codes=None 跳过）
    if fin_sector_codes:
        fin_mask = industry.isin(fin_sector_codes)
        n_filtered = int(fin_mask.sum())
        processed[fin_mask] = np.nan
        log.debug("Step1 金融股过滤：%d 只股票因子值置为 NaN", n_filtered)
        if _diag is not None:
            _diag["n_fin_filtered"] = n_filtered
    elif _diag is not None:
        _diag["n_fin_filtered"] = 0

    # Step 2：MAD 去极值（边界只用 T 日横截面，不用历史全样本）
    _before_winsor = processed.copy()
    processed = winsorize_mad(processed)
    if _diag is not None:
        valid_mask = _before_winsor.notna()
        _diag["n_winsor_clipped"] = int(
            (_before_winsor[valid_mask] != processed[valid_mask]).sum()
        )

    # Step 3：Z-score 标准化（基于非 NaN 值；NaN 保持 NaN）
    processed = standardize(processed)

    # Step 4：行业 + log市值中性化（在非 NaN 子集上 OLS；NaN 保持 NaN）
    residuals = neutralize(processed, industry, log_mv, _diag=_diag)

    # Step 5：残差再标准化（保证最终输出的均值≈0、std≈1）
    return standardize(residuals)
