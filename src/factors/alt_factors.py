"""
src/factors/alt_factors.py — 备选数据因子构建（阶段 5 实现）
=============================================================

实现备选因子（12 个，已从初始 15 个中剔除低质量因子）：
  技术因子（5）：macd_cross / rsi_6 / rsi_12 / boll_pct / obv_chg_20d
  外部数据因子（7）：hk_hold_ratio / hk_hold_chg / analyst_eps_revision /
                     analyst_rating_chg / float_pct_30d / insider_net_buy / pledge_ratio

已剔除因子（函数实现保留作为历史参考，不进入因子池）：
  - chip_winner_rate / cost_deviation：依赖 cyq_perf，训练期仅 36/108 期有数据（~3年），IC_IR 估计不稳定
  - north_flow_5d：广播因子（所有股票相同值），截面标准差=0，预处理后全 NaN，无选股价值

所有因子函数：
  - 签名：factor_xxx(rebalance_date: pd.Timestamp, codes: list[str]) -> pd.Series
  - 返回：index=ts_code（字符串），name=因子名，dtype=float64
  - T 日只使用 <= T 的数据（严格 PIT）
  - NaN 在整个流水线保持 NaN，仅 float_pct_30d / insider_net_buy 无事件时填 0

已知覆盖限制：
  - hk_hold*：2014-11-17 前无数据（沪深港通开通前）→ 全 NaN 属于预期
  - hk_hold*：2024-08-19 后北向持股改为季度披露（季末后约5个交易日公布上季度末数据），
    因子定义已同步改为"季度 PIT 快照"，见 current work/6.2/implementation_plan.md

数据依赖：
  daily_quote.parquet / hk_hold.parquet / analyst_rc_pit.parquet /
  share_float.parquet / holder_trade_pit.parquet / cyq_perf.parquet /
  pledge_stat.parquet / moneyflow_hsgt.parquet
"""

import logging
from functools import lru_cache
from typing import Callable, Optional

import numpy as np
import pandas as pd

from src import config as cfg
from src.data.loader import (
    load_analyst_rc_pit,
    load_cyq_perf,
    load_daily_quote,
    load_hk_hold,
    load_holder_trade_pit,
    load_moneyflow_hsgt,
    load_pledge_stat,
    load_share_float,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 窗口常量
# ---------------------------------------------------------------------------
WINDOW_TECH     = 120   # 技术因子统一历史窗口（足够 MACD EMA 稳定）
WINDOW_BOLL     = 20    # 布林带窗口（交易日）
WINDOW_OBV      = 20    # OBV 变化窗口（交易日）
# hk_hold 因子于 2026-06 改为季度 PIT 快照（2024-08-19 后北向持股披露改为季度制）
WINDOW_HK_HOLD             = 30   # DEPRECATED：原 30 交易日回看，保留避免外部引用报错
WINDOW_HK_HOLD_QUARTERLY   = 270  # 季度快照回看窗口（日历天，约3个自然季度）
MAX_HK_HOLD_STALENESS_DAYS = 120  # 最大允许披露滞后天数（超过则置 NaN；覆盖约1个季度）
HK_HOLD_QUARTERLY_OFFSET_DAYS = 10  # PIT 偏移：季末后约5个交易日 ≈ 10日历天
HK_HOLD_CHG_SPLIT_DAYS     = 91   # chg 分割：约1个自然季度（91天）前为"先前快照"
WINDOW_ANALYST  = 180   # 分析师覆盖回看天数
ANALYST_SPLIT   = 90    # 分析师近期 / 远期分割点（天）
WINDOW_INSIDER  = 90    # 股东增减持回看天数
MIN_ANALYST_CNT    = 2  # 每只股票近期 / 远期各自至少需要的研报数
MIN_DISPERSION_CNT = 3  # eps_dispersion 至少需要的机构数

# 分析师评级字符串 → 数字映射（数越大越看多）
RATING_MAP: dict[str, float] = {
    "买入":   5.0, "强烈推荐": 5.0, "强推":   5.0,
    "增持":   4.0, "推荐":     4.0,
    "中性":   3.0, "持有":     3.0, "观望":   3.0,
    "减持":   2.0, "回避":     2.0,
    "卖出":   1.0,
}


# ---------------------------------------------------------------------------
# 工具函数（与 price_factors.py 模式一致，本模块内独立实现）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _all_trading_dates() -> pd.DatetimeIndex:
    """全市场实际交易日序列（首次调用读盘，后续走缓存）。"""
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    return iq.index.sort_values()


def _valid_dates_before(T: pd.Timestamp) -> pd.DatetimeIndex:
    """返回 T 及之前的所有实际交易日（含 T）。"""
    return _all_trading_dates()[_all_trading_dates() <= T]


def _window_start(T: pd.Timestamp, n: int) -> pd.Timestamp | None:
    """
    取 T 之前（含 T）最近 n 个交易日中第一天。
    历史不足 n 个交易日时返回 None。
    """
    valid = _valid_dates_before(T)
    if len(valid) < n:
        return None
    return valid[-n]


def _nan_series(codes: list[str], name: str) -> pd.Series:
    """历史不足或数据缺失时返回全 NaN 的因子 Series。"""
    return pd.Series(np.nan, index=pd.Index(codes, name="ts_code"), name=name, dtype=float)


def _agg_with_min(series: pd.Series, min_cnt: int, agg: str) -> float:
    """
    对 Series 去 NaN 后做聚合（mean / median）；有效值少于 min_cnt 时返回 NaN。

    Args:
        series:  分组内的原始值
        min_cnt: 最少有效观测数
        agg:     "mean" 或 "median"
    """
    vals = series.dropna()
    if len(vals) < min_cnt:
        return np.nan
    return float(vals.mean() if agg == "mean" else vals.median())


# ---------------------------------------------------------------------------
# 技术因子（5 个，来源：daily_quote.close_adj / vol / ret）
# ---------------------------------------------------------------------------

def factor_macd_cross(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    MACD 柱状图因子（EMA 12/26/9），按收盘价归一化消除量纲。

    因子值 = (MACD_line - Signal_line)_T / close_adj_T。
    T 日只使用 T 及之前 WINDOW_TECH(120) 个交易日数据。
    预期方向：+（多头动量时超额收益更高）。

    Args:
        rebalance_date: 调仓日（T 日）
        codes:          可投资股票代码列表

    Returns:
        ts_code → MACD 柱状图 / 收盘价（无量纲）
    """
    start = _window_start(rebalance_date, WINDOW_TECH)
    if start is None:
        return _nan_series(codes, "macd_cross")

    dq = load_daily_quote(start, rebalance_date, codes=codes)
    if dq.empty:
        return _nan_series(codes, "macd_cross")

    close = dq["close_adj"].unstack("ts_code")
    if close.shape[0] < 35:   # EMA 26 + Signal 9 最低需求
        return _nan_series(codes, "macd_cross")

    macd   = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    hist_T = (macd - signal).iloc[-1]
    close_T = close.iloc[-1].replace(0, np.nan)

    result = hist_T / close_T
    result.name = "macd_cross"
    return result.reindex(codes)


def _compute_rsi(close: pd.DataFrame, window: int) -> pd.Series:
    """
    按列计算简单 RSI（无 Wilder 平滑，用均值代替，更稳健无参数偏差）。

    avg_gain = 最近 window 日正收益均值；
    avg_loss = 最近 window 日负收益绝对值均值。
    RSI = 100 - 100 / (1 + avg_gain / avg_loss)。
    losses=0, gains>0（全涨日）→ RSI=100（数学定义）。
    losses=0, gains=0（停牌/无变动）→ NaN。
    """
    ret = close.pct_change()
    recent = ret.iloc[-window:]
    gains  = recent.clip(lower=0).mean()
    losses = (-recent.clip(upper=0)).mean()
    rs  = gains / losses.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    # losses=0 且 gains>0 时 rsi 为 NaN，修正为数学正确值 100
    no_loss_has_gain = (losses == 0) & (gains > 0)
    return rsi.where(~no_loss_has_gain, other=100.0)


def factor_rsi_6(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    6 日 RSI（T 日只使用 T 及之前 WINDOW_TECH 个交易日数据）。
    预期方向：-（超买时短期反转）。

    Returns:
        ts_code → RSI（0~100）
    """
    start = _window_start(rebalance_date, WINDOW_TECH)
    if start is None:
        return _nan_series(codes, "rsi_6")
    dq = load_daily_quote(start, rebalance_date, codes=codes)
    if dq.empty:
        return _nan_series(codes, "rsi_6")
    close = dq["close_adj"].unstack("ts_code")
    if close.shape[0] < 8:    # pct_change 后至少需要 7 行有效收益
        return _nan_series(codes, "rsi_6")
    result = _compute_rsi(close, window=6)
    result.name = "rsi_6"
    return result.reindex(codes)


def factor_rsi_12(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    12 日 RSI（T 日只使用 T 及之前 WINDOW_TECH 个交易日数据）。
    预期方向：-（超买时短期反转）。

    Returns:
        ts_code → RSI（0~100）
    """
    start = _window_start(rebalance_date, WINDOW_TECH)
    if start is None:
        return _nan_series(codes, "rsi_12")
    dq = load_daily_quote(start, rebalance_date, codes=codes)
    if dq.empty:
        return _nan_series(codes, "rsi_12")
    close = dq["close_adj"].unstack("ts_code")
    if close.shape[0] < 14:
        return _nan_series(codes, "rsi_12")
    result = _compute_rsi(close, window=12)
    result.name = "rsi_12"
    return result.reindex(codes)


def factor_boll_pct(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    布林带位置因子：(close_T - MA20) / (2 * std20)。

    只用 T 日及之前 WINDOW_BOLL(20) 个交易日（严格 PIT，无前视偏差）。
    预期方向：-（价格过热时短期反转）。

    Returns:
        ts_code → 布林带相对位置（无量纲，正值表示偏上轨）
    """
    start = _window_start(rebalance_date, WINDOW_BOLL)
    if start is None:
        return _nan_series(codes, "boll_pct")
    dq = load_daily_quote(start, rebalance_date, codes=codes)
    if dq.empty:
        return _nan_series(codes, "boll_pct")
    close = dq["close_adj"].unstack("ts_code")
    if close.shape[0] < WINDOW_BOLL:
        return _nan_series(codes, "boll_pct")

    ma  = close.mean()
    std = close.std(ddof=1).replace(0, np.nan)
    result = (close.iloc[-1] - ma) / (2 * std)
    result.name = "boll_pct"
    return result.reindex(codes)


def factor_obv_chg_20d(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    OBV 20 交易日变化率：(OBV_T - OBV_{T-20}) / avg_vol_20d。

    OBV_daily = +vol（上涨日）/ -vol（下跌日）/ 0（平盘日）。
    按 20 日均成交量归一化消除量纲，便于横截面比较。
    预期方向：+（OBV 上升表示筹码积累）。

    Returns:
        ts_code → OBV 变化 / 平均成交量（无量纲）
    """
    n_needed = WINDOW_OBV + 1
    start = _window_start(rebalance_date, WINDOW_TECH)
    if start is None:
        return _nan_series(codes, "obv_chg_20d")
    dq = load_daily_quote(start, rebalance_date, codes=codes)
    if dq.empty:
        return _nan_series(codes, "obv_chg_20d")

    ret = dq["ret"].unstack("ts_code")
    vol = dq["vol"].unstack("ts_code")
    if ret.shape[0] < n_needed:
        return _nan_series(codes, "obv_chg_20d")

    obv_cum  = (np.sign(ret) * vol).cumsum()
    avg_vol  = vol.iloc[-WINDOW_OBV:].mean().replace(0, np.nan)
    result   = (obv_cum.iloc[-1] - obv_cum.iloc[-(WINDOW_OBV + 1)]) / avg_vol
    result.name = "obv_chg_20d"
    return result.reindex(codes)


# ---------------------------------------------------------------------------
# 外部数据因子（10 个）
# ---------------------------------------------------------------------------

def factor_hk_hold_ratio(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    北向持仓比例 — 季度 PIT 快照版（2026-06 修订）。

    2024-08-19 起港交所改为每季度第5个交易日公布上季度末持仓数据，
    因子定义同步调整为"最近可得季度披露快照"：
      - PIT 偏移：只使用 trade_date <= T - HK_HOLD_QUARTERLY_OFFSET_DAYS(10) 的记录，
        规避季末披露公告的约5交易日滞后
      - 在 [pit_cutoff - 270d, pit_cutoff] 窗口内取每只股票最近一次非 NaN ratio
      - 若最新记录距 T 超过 MAX_HK_HOLD_STALENESS_DAYS(120) 天 → 置 NaN
    2014-11-17 前无数据 → 全 NaN，属于预期。

    Args:
        rebalance_date: 调仓日（T 日）
        codes:          可投资股票代码列表

    Returns:
        ts_code → 北向持仓比例（%）；无有效快照或超滞后时为 NaN
    """
    pit_cutoff   = rebalance_date - pd.Timedelta(days=HK_HOLD_QUARTERLY_OFFSET_DAYS)
    window_start = pit_cutoff - pd.Timedelta(days=WINDOW_HK_HOLD_QUARTERLY)

    hk = load_hk_hold(window_start, pit_cutoff, codes=codes)
    if hk.empty:
        return _nan_series(codes, "hk_hold_ratio")

    df = hk[["ratio"]].reset_index().dropna(subset=["ratio"])
    if df.empty:
        return _nan_series(codes, "hk_hold_ratio")

    # 每只股票取窗口内最新披露记录（sort 保证 .last() 对应时间最新的行）
    last_obs = df.sort_values("trade_date").groupby("ts_code").last()

    # 超过最大滞后阈值时置 NaN（近期披露缺失说明数据不可用）
    staleness_days = (rebalance_date - last_obs["trade_date"]).dt.days
    snap = last_obs["ratio"].where(staleness_days <= MAX_HK_HOLD_STALENESS_DAYS)

    snap.name = "hk_hold_ratio"
    return snap.reindex(codes)


def factor_hk_hold_chg(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    北向持仓比例变化 — 季度 PIT 快照差分版（2026-06 修订）。

    将回看窗口以 HK_HOLD_CHG_SPLIT_DAYS(91) 天为界分为两段：
      - 近端 (mid_cutoff, pit_cutoff]：取最新披露快照（latest）
      - 远端 [window_start, mid_cutoff]：取最新披露快照（prior）
    change = latest_ratio - prior_ratio

    滞后保护：近端最新记录距 T 超过 MAX_HK_HOLD_STALENESS_DAYS(120) 天 → 置 NaN。
    任一端无数据（含北向开通前） → 置 NaN。

    设计一致性：
      - 2024-08-19 前为日频数据，此定义等价于"约一季度的持仓变动幅度"；
      - 2024-08-19 后为季度数据，自然对应相邻两次季度披露之差。

    Args:
        rebalance_date: 调仓日（T 日）
        codes:          可投资股票代码列表

    Returns:
        ts_code → 持仓比例变化（百分点）；数据不足或超滞后时为 NaN
    """
    pit_cutoff   = rebalance_date - pd.Timedelta(days=HK_HOLD_QUARTERLY_OFFSET_DAYS)
    window_start = pit_cutoff - pd.Timedelta(days=WINDOW_HK_HOLD_QUARTERLY)
    mid_cutoff   = pit_cutoff - pd.Timedelta(days=HK_HOLD_CHG_SPLIT_DAYS)

    hk = load_hk_hold(window_start, pit_cutoff, codes=codes)
    if hk.empty:
        return _nan_series(codes, "hk_hold_chg")

    df = hk[["ratio"]].reset_index().dropna(subset=["ratio"])
    if df.empty:
        return _nan_series(codes, "hk_hold_chg")

    df = df.sort_values("trade_date")
    latest_df = df[df["trade_date"] >  mid_cutoff]
    prior_df  = df[df["trade_date"] <= mid_cutoff]

    if latest_df.empty or prior_df.empty:
        return _nan_series(codes, "hk_hold_chg")

    latest_obs  = latest_df.groupby("ts_code")["ratio"].last()
    latest_date = latest_df.groupby("ts_code")["trade_date"].last()
    prior_obs   = prior_df.groupby("ts_code")["ratio"].last()

    # 近端超过最大滞后阈值时置 NaN（差分基准不可信）
    staleness_days = (rebalance_date - latest_date).dt.days
    valid_latest   = latest_obs.where(staleness_days <= MAX_HK_HOLD_STALENESS_DAYS)

    result = valid_latest - prior_obs
    result.name = "hk_hold_chg"
    return result.reindex(codes)


def factor_analyst_eps_revision(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    分析师 EPS 修正幅度：median(近期eps) / median(远期eps) - 1。

    近期 = (T-ANALYST_SPLIT, T]；远期 = (T-WINDOW_ANALYST, T-ANALYST_SPLIT]。
    两端 median 之一 <= 0，或任一端报告数 < MIN_ANALYST_CNT 时返回 NaN。

    Returns:
        ts_code → EPS 修正幅度（小数）；覆盖率不足的股票为 NaN
    """
    rc = load_analyst_rc_pit(rebalance_date, codes=codes, lookback_days=WINDOW_ANALYST)
    if rc.empty or "eps" not in rc.columns:
        return _nan_series(codes, "analyst_eps_revision")

    split_date = rebalance_date - pd.Timedelta(days=ANALYST_SPLIT)
    recent = rc[rc["pit_date"] >  split_date]
    prior  = rc[rc["pit_date"] <= split_date]

    med_recent = recent.groupby("ts_code")["eps"].apply(
        lambda x: _agg_with_min(x, MIN_ANALYST_CNT, "median")
    )
    med_prior = prior.groupby("ts_code")["eps"].apply(
        lambda x: _agg_with_min(x, MIN_ANALYST_CNT, "median")
    )

    valid    = (med_recent > 0) & (med_prior > 0)
    revision = (med_recent / med_prior - 1).where(valid)
    revision.name = "analyst_eps_revision"
    return revision.reindex(codes)


def factor_analyst_rating_chg(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    分析师评级变化：mean(近期数值评级) - mean(远期数值评级)。

    评级映射见模块常量 RATING_MAP（买入=5, 增持=4, 中性/持有=3, 减持=2, 卖出=1）。
    未识别评级字符串视为 NaN。

    Returns:
        ts_code → 评级变化量（正值表示评级上调）
    """
    rc = load_analyst_rc_pit(rebalance_date, codes=codes, lookback_days=WINDOW_ANALYST)
    if rc.empty or "rating" not in rc.columns:
        return _nan_series(codes, "analyst_rating_chg")

    rc = rc.copy()
    rc["rating_num"] = rc["rating"].map(RATING_MAP)

    split_date = rebalance_date - pd.Timedelta(days=ANALYST_SPLIT)
    recent = rc[rc["pit_date"] >  split_date]
    prior  = rc[rc["pit_date"] <= split_date]

    avg_recent = recent.groupby("ts_code")["rating_num"].apply(
        lambda x: _agg_with_min(x, MIN_ANALYST_CNT, "mean")
    )
    avg_prior = prior.groupby("ts_code")["rating_num"].apply(
        lambda x: _agg_with_min(x, MIN_ANALYST_CNT, "mean")
    )

    chg = avg_recent - avg_prior
    chg.name = "analyst_rating_chg"
    return chg.reindex(codes)


def factor_eps_dispersion(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    分析师 EPS 预测分歧度（取负号，分歧小 → 高分）。

    经济逻辑（Miller 1977 异质信念）：投资者意见分歧越大，悲观者因卖空约束被
    排除在外，价格只反映乐观预期 → 后续收益倾向于向下均值回归，因此分歧大的
    股票预期收益更低。

    构建步骤：
      1. 取近 WINDOW_ANALYST 天内所有研报，按机构去重（同一机构只取最新一条，
         避免同机构多期报告虚增分歧）
      2. 对各机构 EPS 预测计算变异系数 CV = std / |mean|
      3. 取负号：因子 = -CV，使"分歧小（CV 低） → 因子值高"

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → -CV(EPS)；有效机构数 < MIN_DISPERSION_CNT 时为 NaN
    """
    rc = load_analyst_rc_pit(rebalance_date, codes=codes, lookback_days=WINDOW_ANALYST)
    if rc.empty or "eps" not in rc.columns:
        return _nan_series(codes, "eps_dispersion")

    rc = rc.copy()
    rc["eps"] = pd.to_numeric(rc["eps"], errors="coerce")
    rc = rc.dropna(subset=["eps", "org_name"])

    # 同一机构取最新一条报告，避免同机构多期导致虚高分歧度
    rc = (
        rc.sort_values("pit_date")
        .groupby(["ts_code", "org_name"])
        .last()
        .reset_index()
    )

    def _neg_cv(grp: pd.DataFrame) -> float:
        eps_vals = grp["eps"].dropna()
        if len(eps_vals) < MIN_DISPERSION_CNT:
            return np.nan
        mean_val = eps_vals.mean()
        if abs(mean_val) < 1e-8:
            return np.nan
        return float(-eps_vals.std() / abs(mean_val))

    result = rc.groupby("ts_code")[["eps"]].apply(_neg_cv)
    result = result.reindex(codes)
    result.name = "eps_dispersion"
    return result


def factor_analyst_cnt_chg(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    分析师覆盖机构数变化：近期唯一机构数 - 远期唯一机构数。

    经济逻辑（Merton 1987 信息摩擦）：分析师开始覆盖 → 更多投资者了解该股票 →
    持有者基础扩大、定价效率提升 → 预期正向超额收益。覆盖减少则相反。

    近期 = (T-ANALYST_SPLIT, T]；远期 = (T-WINDOW_ANALYST, T-ANALYST_SPLIT]。
    各段按机构名去重后计算唯一机构数；两段均无覆盖时为 NaN。

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → Δ覆盖机构数；两段均无数据时为 NaN
    """
    rc = load_analyst_rc_pit(rebalance_date, codes=codes, lookback_days=WINDOW_ANALYST)
    if rc.empty or "org_name" not in rc.columns:
        return _nan_series(codes, "analyst_cnt_chg")

    rc = rc.dropna(subset=["org_name"])
    split_date = rebalance_date - pd.Timedelta(days=ANALYST_SPLIT)
    recent = rc[rc["pit_date"] >  split_date]
    prior  = rc[rc["pit_date"] <= split_date]

    cnt_recent = recent.groupby("ts_code")["org_name"].nunique()
    cnt_prior  = prior.groupby("ts_code")["org_name"].nunique()

    # fill_value=0：某段无数据等价于该段 0 家机构
    # 两段均无数据的股票不出现在任一 index → reindex 后自然为 NaN
    chg = cnt_recent.subtract(cnt_prior, fill_value=0)
    result = chg.reindex(codes)
    result.name = "analyst_cnt_chg"
    return result


def factor_float_pct_30d(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    30 日内即将解禁股份比例（PIT 严格：ann_date <= T < float_date <= T+30）。

    无即将解禁事件的股票填 0（非 NaN，无解禁压力 = 0 压力）。
    预期方向：-（解禁压力越大，超额收益越低）。

    Returns:
        ts_code → float_ratio（%）；无解禁填 0
    """
    sf = load_share_float(rebalance_date, horizon_days=30, codes=codes)
    if sf.empty or "float_ratio" not in sf.columns:
        return pd.Series(0.0, index=pd.Index(codes, name="ts_code"), name="float_pct_30d")
    result = sf["float_ratio"].reindex(codes).fillna(0.0)
    result.name = "float_pct_30d"
    return result


def factor_insider_net_buy(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    股东 / 高管增减持 90 日净比率。

    净比率 = sum(增持 change_ratio) - sum(减持 change_ratio)。
    无增减持事件的股票填 0（非 NaN，无动作 = 中性）。
    预期方向：+（内部人净买入时表现更好）。

    Returns:
        ts_code → 净增减持比率；无事件填 0
    """
    ht = load_holder_trade_pit(rebalance_date, codes=codes, lookback_days=WINDOW_INSIDER)
    if ht.empty or "in_de" not in ht.columns or "change_ratio" not in ht.columns:
        return pd.Series(0.0, index=pd.Index(codes, name="ts_code"), name="insider_net_buy")

    ht = ht.copy()
    # Tushare holder_trade API 使用 "IN"/"DE"，而非中文"增"/"减"
    sign = ht["in_de"].map({"IN": 1.0, "DE": -1.0}).fillna(0.0)
    ht["net"] = sign * ht["change_ratio"].abs()
    net = ht.groupby("ts_code")["net"].sum().reindex(codes).fillna(0.0)
    net.name = "insider_net_buy"
    return net


def factor_chip_winner_rate(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    筹码获利盘比例（当前价格下盈利的持仓占总持仓比例）。

    取 T 日及之前 5 个日历日内最近一个有数据交易日的 winner_rate。
    2018 年前数据稀疏 → NaN 属于预期。

    Returns:
        ts_code → winner_rate（%）
    """
    start = rebalance_date - pd.Timedelta(days=5)
    cyq = load_cyq_perf(start, rebalance_date, codes=codes)
    if cyq.empty or "winner_rate" not in cyq.columns:
        return _nan_series(codes, "chip_winner_rate")
    snap = cyq["winner_rate"].groupby(level="ts_code").last()
    snap.name = "chip_winner_rate"
    return snap.reindex(codes)


def factor_cost_deviation(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    成本偏离度：weight_avg / cost_50pct - 1。

    测量加权平均成本相对中位数成本的偏离，反映筹码分布的不对称性。
    2018 年前数据稀疏 → NaN 属于预期。

    Returns:
        ts_code → 成本偏离度（小数）
    """
    start = rebalance_date - pd.Timedelta(days=5)
    cyq = load_cyq_perf(start, rebalance_date, codes=codes)
    if cyq.empty or "weight_avg" not in cyq.columns or "cost_50pct" not in cyq.columns:
        return _nan_series(codes, "cost_deviation")

    snap = cyq[["weight_avg", "cost_50pct"]].groupby(level="ts_code").last()
    med  = snap["cost_50pct"].replace(0, np.nan)
    result = snap["weight_avg"] / med - 1.0
    result.name = "cost_deviation"
    return result.reindex(codes)


def factor_pledge_ratio(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    股权质押比例（end_date <= T 的最新报告期快照）。

    无质押记录的股票返回 NaN（无数据与无质押无法区分，不填 0）。
    注意：pledge_stat 无 ann_date，用 end_date 保守估计 PIT 可用日期。

    Returns:
        ts_code → pledge_ratio（%）
    """
    ps = load_pledge_stat(rebalance_date, codes=codes)
    if ps.empty or "pledge_ratio" not in ps.columns:
        return _nan_series(codes, "pledge_ratio")
    result = ps["pledge_ratio"].reindex(codes)
    result.name = "pledge_ratio"
    return result


def factor_north_flow_5d(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    北向资金 5 交易日累计净流入（全市场广播因子）。

    这是全市场环境变量——所有股票接收相同值。
    经行业 + 市值中性化后截面变化约为 0，IC 预期接近 0。
    保留实现，由因子评价阶段决定是否纳入最终因子池。

    Returns:
        ts_code → 5 日累计北向净流入（单位与 moneyflow_hsgt.north_money 相同）
    """
    start = rebalance_date - pd.Timedelta(days=15)   # 约 10 个交易日缓冲
    hsgt = load_moneyflow_hsgt(start, rebalance_date)
    if hsgt.empty or "north_money" not in hsgt.columns:
        return _nan_series(codes, "north_flow_5d")

    flow_5d = float(hsgt["north_money"].tail(5).sum())
    return pd.Series(flow_5d, index=pd.Index(codes, name="ts_code"), name="north_flow_5d")


# ---------------------------------------------------------------------------
# 注册字典与批量构建入口
# ---------------------------------------------------------------------------

_ALT_FACTOR_BUILDERS: dict[str, Callable] = {
    # 技术因子
    "macd_cross":           factor_macd_cross,
    "rsi_6":                factor_rsi_6,
    "rsi_12":               factor_rsi_12,
    "boll_pct":             factor_boll_pct,
    "obv_chg_20d":          factor_obv_chg_20d,
    # 外部数据因子
    "hk_hold_ratio":        factor_hk_hold_ratio,
    "hk_hold_chg":          factor_hk_hold_chg,
    "analyst_eps_revision":  factor_analyst_eps_revision,
    "analyst_rating_chg":   factor_analyst_rating_chg,
    "eps_dispersion":        factor_eps_dispersion,
    "analyst_cnt_chg":       factor_analyst_cnt_chg,
    "float_pct_30d":        factor_float_pct_30d,
    "insider_net_buy":      factor_insider_net_buy,
    "pledge_ratio":         factor_pledge_ratio,
    # chip_winner_rate / cost_deviation / north_flow_5d 已从因子池剔除（见 execution_log.md 第 4.5 步）
}


def build_all_alt_factors(
    rebalance_date: pd.Timestamp,
    codes: list[str],
    factor_names: Optional[list[str]] = None,
) -> dict[str, pd.Series]:
    """
    构建指定调仓日的所有备选因子原始值。

    返回原始因子（未预处理），需经 preprocess_factor 后用于 IC 检验或优化。
    单个因子构建失败时记录 ERROR 并存全 NaN Series，不中断其他因子。

    Args:
        rebalance_date:  调仓日（T 日）
        codes:           可投资股票代码列表（来自 get_investable_universe）
        factor_names:    指定因子子集；None 则构建全部 15 个因子

    Returns:
        dict：key = 因子名，value = ts_code → 原始因子值 Series（float64）
    """
    names = factor_names if factor_names is not None else list(_ALT_FACTOR_BUILDERS)
    unknown = set(names) - set(_ALT_FACTOR_BUILDERS)
    if unknown:
        raise ValueError(f"未知备选因子名：{sorted(unknown)}")

    result: dict[str, pd.Series] = {}
    for name in names:
        try:
            result[name] = _ALT_FACTOR_BUILDERS[name](rebalance_date, codes)
        except Exception as exc:
            log.error("备选因子 %s 在 %s 构建失败：%s", name, rebalance_date.date(), exc)
            result[name] = pd.Series(
                np.nan,
                index=pd.Index(codes, name="ts_code"),
                name=name,
                dtype=float,
            )

    log.info(
        "build_all_alt_factors(%s): 构建 %d 个因子，代码 %d 只",
        rebalance_date.date(), len(result), len(codes),
    )
    return result
