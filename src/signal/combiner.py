"""
src/signal/combiner.py — 多因子合成信号
==========================================================

合成方法：
  equal  : 简单等权（baseline，不依赖 IC 历史）
  ic_ir  : IC_IR 加权（推荐），权重正负携带方向信息

合成流程（ic_ir 方法，每个调仓日 T）：
  1. 从历史 IC 序列取 before_date < T 的最近 window_months 期
  2. 计算滚动 IC_IR = IC_mean / IC_std（ddof=1）；不足 MIN_IC_HISTORY 期则该因子权重为 NaN
  3. 若所有因子 IC_IR 均为 NaN（冷启动期）→ 回退到等权
  4. 截面合成：composite[i] = Σ(w_k × f_k[i]) / Σ(|w_k|)，只对 w_k 和 f_k[i] 均非 NaN 的 k 求和
  5. 有效因子数 < min_valid_factors 的股票置 NaN
  6. MAD 去极值 + Z-score 标准化

关键约束：
  - IC_IR 窗口严格 < T（index < before_date），不含当期，无信号泄露
  - NaN 全程保持 NaN，不在本模块填充（优化器层统一处理）
  - IC_IR 负值的因子贡献自动反向（无需手工翻转方向）
"""

import logging

import numpy as np
import pandas as pd

from src import config as cfg
from src.factors.preprocess import standardize, winsorize_mad

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量（从 src/config.py 读取，保留模块级别名以兼容现有 import）
# ---------------------------------------------------------------------------

MIN_IC_HISTORY: int = cfg.SIGNAL_MIN_IC_HISTORY
DEFAULT_WINDOW_MONTHS: int = cfg.SIGNAL_WINDOW_MONTHS
DEFAULT_MIN_VALID_FACTORS: int = cfg.SIGNAL_DEFAULT_MIN_VALID

# 因子预期方向：+1 值越高越好，-1 值越高越差。
# IC_IR 加权模式下符号由历史 IC 自动决定；等权/冷启动回退时须用此表手工赋符号。
# 不在表中的因子默认 +1（保守）。
FACTOR_DIRECTIONS: dict[str, int] = {
    # 价值
    "ep_ttm":           +1,
    "bp":               +1,
    "sp_ttm":           +1,
    "dy_ttm":           +1,
    "cfp":              +1,
    "fcfp":             +1,   # 自由现金流收益率，方向同 cfp
    # 质量
    "roe":              +1,
    "roa":              +1,
    "gross_margin":     +1,
    "asset_turn":       +1,
    "leverage":         -1,   # 高杠杆 = 高风险，预期负超额
    "accrual":          -1,   # 高应计 = 低质量盈余
    # 成长
    "np_yoy":           +1,
    "rev_yoy":          +1,
    "roe_delta":        +1,
    "q_roe":            +1,
    # 动量 / 反转
    "ret_1m":           -1,   # 短期反转：高 1M 收益 → 下期回调
    "mom_6_1":          +1,
    "mom_12_1":         +1,
    "holder_chg":       +1,   # 公式已含负号，值越高 = 股东减少 = 利好
    # 波动率
    "vol_60d":          -1,   # 高波动溢价为负
    "ivol_60d":         -1,
    "max_ret":          -1,   # MAX 效应：彩票偏好股次期回调
    # 流动性
    "turn_20d":         -1,   # 低换手溢价
    "amihud":           +1,   # 非流动性溢价
    # 资金流向
    "margin_ratio":     -1,   # 融资余额高 → 超买信号，训练集 IC 均值 < 0
    "short_ratio":      -1,   # 融券余额高 = 看空信号
    "large_net_inflow": +1,
}


def validate_directions_vs_summary(factor_summary: "pd.DataFrame") -> list[str]:
    """
    校验 FACTOR_DIRECTIONS 与训练集评估 IC 方向是否一致。

    训练集 IC 均值 > 0 → 评估方向 +1；< 0 → 评估方向 -1。
    返回不一致的因子名列表，并对每个不一致因子输出 warning 日志。
    调用方应在合成前执行此校验并记录结果；不影响合成逻辑。

    Args:
        factor_summary: factor_summary.csv 读入的 DataFrame，需含 ic_mean 列
    Returns:
        方向不一致的因子名列表（空列表表示全部一致）
    """
    if "ic_mean" not in factor_summary.columns:
        log.debug("validate_directions_vs_summary: factor_summary 缺少 ic_mean 列，跳过校验")
        return []

    mismatches: list[str] = []
    for name, row in factor_summary.iterrows():
        ic_mean = row.get("ic_mean", np.nan)
        if pd.isna(ic_mean) or name not in FACTOR_DIRECTIONS:
            continue
        eval_direction = 1 if float(ic_mean) > 0 else -1
        if eval_direction != FACTOR_DIRECTIONS[name]:
            mismatches.append(str(name))
            log.warning(
                "FACTOR_DIRECTIONS 与评估方向不一致: %s hardcoded=%+d eval=%+d (ic_mean=%.4f)",
                name, FACTOR_DIRECTIONS[name], eval_direction, float(ic_mean),
            )

    if not mismatches:
        log.debug("validate_directions_vs_summary: 所有因子方向一致")
    return mismatches


# ---------------------------------------------------------------------------
# 滚动 IC_IR 权重
# ---------------------------------------------------------------------------

def compute_rolling_ic_ir(
    ic_series_map: dict[str, pd.Series],
    before_date: pd.Timestamp,
    window_months: int = DEFAULT_WINDOW_MONTHS,
) -> dict[str, float]:
    """
    计算每个因子在 before_date 之前的滚动 IC_IR（含正负方向）。

    时间约束：取 index < before_date 的 IC 序列后再丢弃最近一期。
    原因：IC(T-1) = corr(factor[T-1], fwd_ret[T-1])，而 fwd_ret[T-1] 的 exit
    是 T 之后的第一个交易日，在 T 盘后信号生成时尚未可知。丢弃最近一期使 IC
    权重只依赖 exit_date < T 的历史收益，消除时间可得性错误。
    冷启动：有效观测数 < MIN_IC_HISTORY 时返回 NaN，build_composite_panel 会回退到等权。

    Args:
        ic_series_map:  dict，key=因子名，value=IC 时序 Series（index=调仓日）
        before_date:    当前调仓日 T；只用严格小于 T 且 exit_date < T 的历史 IC
        window_months:  回看最近 N 期 IC（默认 24）
    Returns:
        dict，key=因子名，value=IC_IR（float；冷启动时为 NaN）
    """
    result: dict[str, float] = {}
    for name, ic_s in ic_series_map.items():
        # 丢弃最近一期：IC(T-1) 的 exit_date ≈ T+1，T 盘后不可得
        past = ic_s[ic_s.index < before_date].iloc[:-1]
        if len(past) == 0:
            result[name] = np.nan
            continue

        recent = past.iloc[-window_months:]
        if len(recent) < MIN_IC_HISTORY:
            result[name] = np.nan
            continue

        ic_mean = float(recent.mean())
        ic_std  = float(recent.std(ddof=1))
        if ic_std < 1e-10:
            result[name] = np.nan
            continue

        result[name] = ic_mean / ic_std

    return result


def _equal_weight_map(factor_names: list[str]) -> dict[str, float]:
    """等权：按 FACTOR_DIRECTIONS 确定符号（±1），未在表中的因子默认 +1。"""
    return {name: float(FACTOR_DIRECTIONS.get(name, 1)) for name in factor_names}


# ---------------------------------------------------------------------------
# 单截面合成
# ---------------------------------------------------------------------------

def combine_factors_cross_section(
    factor_dict: dict[str, pd.Series],
    ic_ir_weights: dict[str, float],
    min_valid_factors: int = DEFAULT_MIN_VALID_FACTORS,
) -> pd.Series:
    """
    对单个截面将多因子合成为复合信号。

    公式（IC_IR 加权）：
      composite[i] = Σ_k(w_k × f_k[i]) / Σ_k(|w_k|)
      其中 k 只遍历 w_k 非 NaN 且 f_k[i] 非 NaN 的因子

    NaN 处理：
      - ic_ir_weights 中 NaN 的因子完全不参与求和（视为该因子不存在）
      - 某股票有效因子数 < min_valid_factors 时，该股票合成值置 NaN
      - 合成后执行 MAD 去极值 + Z-score 标准化

    等权调用约定：
      传入 ic_ir_weights = {name: 1.0 for each factor}，则公式退化为等权均值。

    Args:
        factor_dict:        dict，key=因子名，value=横截面因子 Series（ts_code 为 index）
        ic_ir_weights:      dict，key=因子名，value=IC_IR（NaN 表示该因子当期排除）
        min_valid_factors:  每只股票合成时最少有效因子数
    Returns:
        合成后经去极值+标准化的 pd.Series（ts_code 为 index，name='composite'）
    """
    # 筛选有效因子：IC_IR 非 NaN 且在 factor_dict 中存在
    valid_factors = {
        name: weight
        for name, weight in ic_ir_weights.items()
        if name in factor_dict and pd.notna(weight)
    }

    # 收集所有股票代码（以 factor_dict 中出现的 codes 的并集）
    all_codes: pd.Index = pd.Index([])
    for s in factor_dict.values():
        all_codes = all_codes.union(s.index)

    if not valid_factors:
        log.debug("combine_factors_cross_section: 无有效权重因子，返回全 NaN")
        return pd.Series(np.nan, index=all_codes, name="composite")

    factor_names = list(valid_factors.keys())
    weights     = np.array([valid_factors[n] for n in factor_names], dtype=float)
    abs_weights = np.abs(weights)

    # 构建截面矩阵 (n_stocks × n_factors)
    factor_matrix = pd.DataFrame(
        {name: factor_dict[name].reindex(all_codes) for name in factor_names}
    )

    # 向量化加权合成
    arr        = factor_matrix.values.astype(float)     # (n_stocks, n_factors)
    valid_mask = ~np.isnan(arr)                          # True = 有值
    valid_count = valid_mask.sum(axis=1)                 # 每只股票的有效因子数

    arr_filled  = np.where(valid_mask, arr, 0.0)
    numerator   = arr_filled  @ weights                  # (n_stocks,)
    denominator = valid_mask.astype(float) @ abs_weights  # (n_stocks,)

    composite_vals = np.where(denominator > 0, numerator / denominator, np.nan)
    composite_vals = np.where(valid_count >= min_valid_factors, composite_vals, np.nan)

    result = pd.Series(composite_vals, index=all_codes, name="composite")

    # 再去极值 + 标准化（合成结果量纲不统一，必须重新标准化）
    n_valid = result.notna().sum()
    if n_valid >= 2:
        result = winsorize_mad(result)
        result = standardize(result)

    return result


# ---------------------------------------------------------------------------
# 全周期合成面板
# ---------------------------------------------------------------------------

def build_composite_panel(
    factor_panels: dict[str, pd.DataFrame],
    ic_series_map: dict[str, pd.Series],
    rebalance_dates: list[pd.Timestamp],
    method: str = "ic_ir",
    window_months: int = DEFAULT_WINDOW_MONTHS,
    min_valid_factors: int = DEFAULT_MIN_VALID_FACTORS,
    stability_weights: dict[str, float] | None = None,
    return_diagnostics: bool = False,
) -> "pd.DataFrame | tuple[pd.DataFrame, dict]":
    """
    对所有调仓日构建复合信号面板。

    等权（method='equal'）：各因子恒等权，不依赖 IC 历史，不受冷启动期限制，
      适合作为 baseline 或验证时的对照组。

    IC_IR 加权（method='ic_ir'）：每期 T 用严格 < T 的滚动 IC_IR 加权；
      - 若所有因子 IC 历史不足 MIN_IC_HISTORY，当期回退到等权并记录日志
      - 仅部分因子不足时，该因子权重为 NaN（排除在外，其余正常加权）

    稳定性权重（stability_weights，仅 ic_ir 非冷启动生效）：
      effective_weight[k] = IC_IR[k] × stability_weights.get(k, 1.0)。
      来源：run_factor_evaluation.py 的 summary["stability_weight"]；
      验证集方向翻转因子取 0.5，完全排除取 0.0。

    时间对齐约束：IC_IR 窗口严格截断到 before_date < T，无信号泄露。

    Args:
        factor_panels:      dict，key=因子名，value=行=调仓日、列=ts_code 的因子面板
        ic_series_map:      dict，key=因子名，value=IC 时序（compute_ic_series 输出）
        rebalance_dates:    调仓日列表（有序升序）
        method:             'equal'（等权）或 'ic_ir'（推荐）
        window_months:      滚动 IC_IR 回看期数
        min_valid_factors:  每只股票最少有效因子数
        stability_weights:  dict[因子名, 稳定性系数 ∈ [0, 1]]；None 表示全部 1.0
        return_diagnostics: 若 True，同时返回 diagnostics dict（包含逐期权重历史）
    Returns:
        return_diagnostics=False: 行=调仓日、列=ts_code 的合成信号 DataFrame
        return_diagnostics=True:  (panel, diagnostics)，diagnostics 含：
            weight_history     dict[Timestamp, dict[factor, weight]] — 逐期实际权重
            cold_start_flags   dict[Timestamp, bool] — 是否冷启动
            cold_start_count   int — 冷启动期数
            method             str — 合成方法
    Raises:
        ValueError: method 不在支持列表中
    """
    if method not in ("equal", "ic_ir"):
        raise ValueError(f"method 须为 'equal' 或 'ic_ir'，收到: '{method}'")

    rows: dict[pd.Timestamp, pd.Series] = {}
    cold_start_count = 0
    weight_history: dict[pd.Timestamp, dict] = {}
    cold_start_flags: dict[pd.Timestamp, bool] = {}

    for T in rebalance_dates:
        # 提取当期各因子截面（跳过该日期在 panel 中不存在的因子）
        factor_dict: dict[str, pd.Series] = {
            name: panel.loc[T]
            for name, panel in factor_panels.items()
            if T in panel.index
        }

        if not factor_dict:
            log.debug("build_composite_panel: %s 无因子数据，跳过", T.date())
            continue

        is_cold_start = False
        if method == "equal":
            weights = _equal_weight_map(list(factor_dict.keys()))
        else:
            weights = compute_rolling_ic_ir(ic_series_map, before_date=T, window_months=window_months)
            # 冷启动：所有因子 IC_IR 均为 NaN → 等权回退（不应用 stability_weights）
            if all(not pd.notna(v) for v in weights.values()):
                is_cold_start = True
                cold_start_count += 1
                log.debug("build_composite_panel: %s 所有因子 IC_IR 不足，等权回退", T.date())
                weights = _equal_weight_map(list(factor_dict.keys()))
            elif stability_weights is not None:
                weights = {
                    name: (w * stability_weights.get(name, 1.0) if pd.notna(w) else w)
                    for name, w in weights.items()
                }

        if return_diagnostics:
            weight_history[T] = dict(weights)
            cold_start_flags[T] = is_cold_start

        rows[T] = combine_factors_cross_section(factor_dict, weights, min_valid_factors)

    if cold_start_count:
        log.info("build_composite_panel: 共 %d 期冷启动期回退到等权", cold_start_count)

    if not rows:
        log.warning("build_composite_panel: 所有调仓日均无有效数据，返回空 DataFrame")
        empty = pd.DataFrame()
        if return_diagnostics:
            return empty, {"weight_history": {}, "cold_start_flags": {}, "cold_start_count": 0, "method": method}
        return empty

    panel = pd.DataFrame(rows).T
    panel.index.name = "rebalance_date"
    log.info(
        "build_composite_panel: 合成完成 method=%s，%d 期，股票数≈%d",
        method, len(panel), panel.shape[1],
    )

    if return_diagnostics:
        diagnostics = {
            "weight_history": weight_history,
            "cold_start_flags": cold_start_flags,
            "cold_start_count": cold_start_count,
            "method": method,
        }
        return panel, diagnostics
    return panel
