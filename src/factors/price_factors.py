"""
src/factors/price_factors.py — 量价因子构建
============================================

实现 15 个量价类因子：
  动量/反转  ret_1m / mom_6_1 / mom_12_1 / holder_chg
  波动率     vol_60d / ivol_60d / max_ret
  流动性     turn_20d / amihud
  资金流向   margin_ratio / short_ratio / large_net_inflow
  阶段5 动量 high_52w / ind_adj_mom / mom_risk_adj
             high_52w_v2 / ind_adj_mom_6_1 / mom_consistency_6（Sprint 1 新增）

关键设计约束：
  - 所有窗口用实际交易日序列（来自 index_quote），禁止用 pandas 的 'NB'
  - 动量因子用 close_adj 两端之比计算收益，不用 ret.prod()（避免缺失行偏差）
  - NaN 在整个流水线保持 NaN，不在此处填充
  - holder_chg 是 PIT 因子，使用 available_date <= T 的最新与次新两期快照

单位说明：
  daily_quote.amount      千元（=close × vol_手 × 100 / 1000）
  moneyflow.net_mf_amount 万元（需 ×10 转千元才能与 amount 同量纲）
  margin.rzye             元
  daily_basic.total_mv    万元（需 ×10000 转元）
"""

import logging
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd

from src.data.loader import (
    get_holder_pit_raw,
    load_daily_basic,
    load_daily_quote,
    load_industry,
    load_margin,
    load_moneyflow,
)
from src import config as cfg

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 窗口参数常量
# ---------------------------------------------------------------------------
WINDOW_RET_1M   = 20   # 短期反转窗口（交易日）
WINDOW_MOM_SKIP = 20   # 动量跳过的最近交易日数
WINDOW_MOM_6    = 120  # 6 个月动量窗口（含跳过部分共 120 天）
WINDOW_MOM_12   = 240  # 12 个月动量窗口（含跳过部分共 240 天）
WINDOW_HIGH_52W = 252  # 52 周高点回看窗口（约 252 个交易日）
WINDOW_VOL     = 60    # 波动率计算窗口
WINDOW_TURN    = 20    # 换手率均值窗口
WINDOW_AMIHUD  = 20    # Amihud 非流动性均值窗口
WINDOW_FLOW    = 20    # 资金流向均值窗口
MIN_VOL_PERIODS = 40   # 波动率计算最少交易日数（低于此返回 NaN）
MIN_IVOL_PERIODS = 30  # IVOL 回归最少观测数（低于此返回 NaN）
HOLDER_STALE_MONTHS = 18  # 股东人数最新期距 T 超过此月数视为过期
WINDOW_MF_FLOW  = 20        # 融资净买入流量窗口（交易日）
WINDOW_MF_STOCK = 60        # 融资余额拥挤度窗口（交易日，备用）
_MF_WAN_TO_YUAN = 10_000.0  # daily_basic.circ_mv 万元→元

# Sprint 1 动量因子新增常量
WINDOW_CONSISTENCY_LOOKBACK = 6    # 月涨幅一致性：计算月度收益的月数
WINDOW_CONSISTENCY_SKIP     = 1    # 跳过最近几个月（标准动量 skip-1-month）
MIN_CONSISTENCY_MONTHS      = 4    # 最少有效月数，低于此返回 NaN
MONTHLY_RET_CAP             = 0.4  # 月度收益截尾上限（A 股涨跌停）


# ---------------------------------------------------------------------------
# 交易日序列（模块级缓存，避免重复 I/O）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _all_trading_dates() -> pd.DatetimeIndex:
    """
    返回全市场实际交易日序列（来自 index_quote 的日期索引）。

    使用 lru_cache 缓存，首次调用读盘，后续直接返回内存缓存。
    index_quote 仅 2430 行，读取速度极快。
    """
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    return iq.index.sort_values()


def _valid_dates_before(rebalance_date: pd.Timestamp) -> pd.DatetimeIndex:
    """返回 rebalance_date 及之前的所有实际交易日（含当日）。"""
    all_dates = _all_trading_dates()
    return all_dates[all_dates <= rebalance_date]


def _window_dates(
    rebalance_date: pd.Timestamp,
    n_window: int,
    skip_recent: int = 0,
    extra_base: int = 1,
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """
    计算窗口的起止日期（含额外基点日），用于加载 close_adj 两端数据。

    定位方式（valid = 所有 <= T 的交易日，从旧到新排列）：
      end_date   = valid[n - 1 - skip_recent]  (窗口末端)
      start_date = valid[n - 1 - skip_recent - n_window]  (基点，窗口前一天)

    其中 extra_base=1 表示多取一个基点日，使得：
      cumret = close_adj[end_date] / close_adj[start_date] - 1
    包含恰好 n_window 个交易日的收益。

    Args:
        n_window:    窗口内的交易日数（不含基点日）
        skip_recent: 从 T 往前跳过的交易日数（0 表示窗口末端即 T）
        extra_base:  基点日数量（通常为 1）
    Returns:
        (start_date, end_date) 或 (None, None) 表示历史不足
    """
    valid = _valid_dates_before(rebalance_date)
    n = len(valid)
    total_needed = n_window + skip_recent + extra_base
    if n < total_needed:
        return None, None

    end_pos   = n - 1 - skip_recent
    start_pos = end_pos - n_window     # 含 extra_base=1 的基点
    return valid[start_pos], valid[end_pos]


def _prev_month_end_dates(
    rebalance_date: pd.Timestamp,
    n: int,
) -> list[pd.Timestamp]:
    """
    返回 rebalance_date 之前 n 个自然月的最后交易日列表（从旧到新）。

    例：rebalance_date = 2019-06-28，n=7
      → [last_td(Nov2018), last_td(Dec2018), ..., last_td(May2019)]

    实现：ref_k = rebalance_date - DateOffset(months=k)，取 ref_k.year/month
    在交易日历中找该月最后一个交易日。

    注意 DateOffset 对月末日期的处理：
      2013-03-29 - DateOffset(months=1) = 2013-02-28（pandas 自动截断）
      → 正确定位 2013 年 2 月，找到最后交易日 ✓

    Args:
        rebalance_date: 调仓日 T
        n:              回看的自然月数
    Returns:
        日期列表（从旧到新）；任一月无交易日则返回空列表（历史不足）
    """
    all_dates = _all_trading_dates()
    result = []
    for k in range(n, 0, -1):          # k 从大（最老）到小（最近），输出从旧到新
        ref = rebalance_date - pd.DateOffset(months=k)
        y, m = ref.year, ref.month
        month_dates = all_dates[(all_dates.year == y) & (all_dates.month == m)]
        if len(month_dates) == 0:
            return []                  # 该月无交易日，历史不足
        result.append(month_dates[-1]) # 取该月最后一个交易日
    return result


# ---------------------------------------------------------------------------
# 动量/反转类（4 个）
# ---------------------------------------------------------------------------

def factor_ret_1m(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    短期反转因子（20 个交易日收益率）。

    使用 close_adj 两端比率：
      ret_1m = close_adj[T] / close_adj[T_minus_20] - 1
    其中 T_minus_20 是窗口的基点日（T 前第 21 个交易日的收盘价）。
    这比用 ret.prod() 更鲁棒：后者在缺失行时会累乘更少项导致收益偏差。

    预期方向：-（短期反转效应，近期涨幅大的股票下期倾向回调）

    Returns:
        ts_code → 20 日累计收益率（小数形式）
    """
    start, end = _window_dates(rebalance_date, WINDOW_RET_1M, skip_recent=0)
    if start is None:
        return pd.Series(dtype=float, name="ret_1m")
    dq = load_daily_quote(start, end, codes=codes)
    if dq.empty:
        return pd.Series(dtype=float, name="ret_1m")

    adj = dq["close_adj"].unstack("ts_code")
    result = adj.iloc[-1] / adj.iloc[0] - 1
    result.name = "ret_1m"
    return result


def factor_mom_6_1(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    中期动量因子（6 个月，跳过最近 1 个月）。

    窗口：T 前第 121 个交易日（基点）到 T 前第 21 个交易日（末端），共 100 天。
      mom_6_1 = close_adj[T-20] / close_adj[T-120] - 1

    跳过最近 20 天是标准做法，消除短期反转效应对中期动量的干扰。

    预期方向：+（动量溢价，中期涨幅大的股票短期延续）

    Returns:
        ts_code → 100 日累计收益率（小数）
    """
    n_window = WINDOW_MOM_6 - WINDOW_MOM_SKIP  # 100 天实际收益窗口
    start, end = _window_dates(rebalance_date, n_window, skip_recent=WINDOW_MOM_SKIP)
    if start is None:
        return pd.Series(dtype=float, name="mom_6_1")
    dq = load_daily_quote(start, end, codes=codes)
    if dq.empty:
        return pd.Series(dtype=float, name="mom_6_1")

    adj = dq["close_adj"].unstack("ts_code")
    result = adj.iloc[-1] / adj.iloc[0] - 1
    result.name = "mom_6_1"
    return result


def factor_mom_12_1(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    长期动量因子（12 个月，跳过最近 1 个月）。

    窗口：T 前第 241 个交易日（基点）到 T 前第 21 个交易日（末端），共 220 天。
      mom_12_1 = close_adj[T-20] / close_adj[T-240] - 1

    早期调仓日（训练期起点前后数月）可能因历史不足返回全 NaN，属正常情况。

    预期方向：+（长期动量溢价）

    Returns:
        ts_code → 220 日累计收益率（小数）
    """
    n_window = WINDOW_MOM_12 - WINDOW_MOM_SKIP  # 220 天实际收益窗口
    start, end = _window_dates(rebalance_date, n_window, skip_recent=WINDOW_MOM_SKIP)
    if start is None:
        return pd.Series(dtype=float, name="mom_12_1")
    dq = load_daily_quote(start, end, codes=codes)
    if dq.empty:
        return pd.Series(dtype=float, name="mom_12_1")

    adj = dq["close_adj"].unstack("ts_code")
    result = adj.iloc[-1] / adj.iloc[0] - 1
    result.name = "mom_12_1"
    return result


def factor_holder_chg(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    股东人数变化率因子（PIT，反向指标）。

    公式：holder_chg = -(holder_num_latest - holder_num_prev) / holder_num_prev
    「latest」= available_date <= T 的最新公告；「prev」= 紧接上一期公告。

    负号含义：股东人数减少（筹码集中）视为利好，因子值越大越好。
    若两期时间跨度 > HOLDER_STALE_MONTHS，返回 NaN（数据可能已过期）。
    若只有一期数据（新上市股票），返回 NaN。

    预期方向：+（股东人数减少 → 机构集中持仓 → 预期超额收益）

    Returns:
        ts_code → holder_chg（小数形式变化率）；数据不足时为 NaN
    """
    hp = get_holder_pit_raw()

    # PIT 过滤：available_date <= T 且 end_date < T 且 pit_date <= T
    vis = hp[
        (hp["available_date"] <= rebalance_date)
        & (hp["end_date"] < rebalance_date)
        & (hp["pit_date"] <= rebalance_date)
    ]
    if codes is not None:
        vis = vis[vis["ts_code"].isin(codes)]
    if vis.empty:
        return pd.Series(dtype=float, name="holder_chg")

    # 去重：同一 (ts_code, end_date) 保留最新 available_date
    vis = vis.sort_values(["ts_code", "end_date", "available_date"])
    vis_dedup = vis.drop_duplicates(subset=["ts_code", "end_date"], keep="last")

    # 对每只股票取最新两期
    vis_dedup = vis_dedup.sort_values(["ts_code", "end_date"])
    vis_dedup["_rank"] = vis_dedup.groupby("ts_code").cumcount(ascending=False)

    latest = vis_dedup[vis_dedup["_rank"] == 0].set_index("ts_code")
    prev   = vis_dedup[vis_dedup["_rank"] == 1].set_index("ts_code")

    common = latest.index.intersection(prev.index)
    if common.empty:
        return pd.Series(dtype=float, name="holder_chg")

    # 陈旧度检查（双重）：
    # 1. 最新期距 T 不超过 HOLDER_STALE_MONTHS：防止用远古数据（如 2017 年两期）产生 2025 的因子值
    # 2. 两期间隔不超过 HOLDER_STALE_MONTHS：防止跨度过大时变化率失去可比性
    stale_cutoff = rebalance_date - pd.DateOffset(months=HOLDER_STALE_MONTHS)
    fresh = latest.loc[common, "end_date"] >= stale_cutoff

    gap_days = (
        latest.loc[common, "end_date"] - prev.loc[common, "end_date"]
    ).dt.days
    max_gap_days = HOLDER_STALE_MONTHS * 31   # 近似月换算
    reasonable_gap = gap_days <= max_gap_days

    denom = prev.loc[common, "holder_num"].replace(0, np.nan)
    delta = latest.loc[common, "holder_num"] - prev.loc[common, "holder_num"]
    chg   = -delta / denom

    # 任一陈旧条件触发则置 NaN
    chg[~fresh | ~reasonable_gap] = np.nan
    chg.name = "holder_chg"

    # Reindex 到全部 codes，缺失的股票自动为 NaN
    return chg.reindex(codes)


# ---------------------------------------------------------------------------
# 波动率类（3 个）
# ---------------------------------------------------------------------------

def factor_vol_60d(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    历史波动率因子（60 个交易日收益标准差）。

    使用 daily_quote.ret（日收益率），停牌日 ret=0 保留（股价不变 = 零方差贡献）。
    非 NaN 有效天数 < MIN_VOL_PERIODS 的股票返回 NaN。

    预期方向：-（低波动溢价，低波动率股票预期超额收益）

    Returns:
        ts_code → 60 日收益标准差（小数）
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < WINDOW_VOL:
        return pd.Series(dtype=float, name="vol_60d")

    start = valid[-WINDOW_VOL]
    dq = load_daily_quote(start, rebalance_date, codes=codes)
    if dq.empty:
        return pd.Series(dtype=float, name="vol_60d")

    ret_pivot = dq["ret"].unstack("ts_code")
    result = ret_pivot.std(ddof=1, skipna=True)
    # 有效天数不足的股票置 NaN
    valid_counts = ret_pivot.notna().sum()
    result[valid_counts < MIN_VOL_PERIODS] = np.nan
    result.name = "vol_60d"
    return result


def factor_ivol_60d(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    特质波动率因子（60 日 CAPM 残差标准差）。

    回归模型：ret_stock = alpha + beta × ret_market + epsilon
    ivol = std(epsilon)

    向量化实现：对 N 只股票同时做 OLS（X 形状 T×2，Y 形状 T×N），
    避免逐股回归的性能瓶颈。

    NaN 处理：先将缺失天填充为 0（停牌 = 零收益假设），再做 OLS。
    有效天数 < MIN_IVOL_PERIODS 的股票在最后置 NaN。

    数据依赖：daily_quote.ret + index_quote.index_ret

    预期方向：-（低特质波动率溢价）

    Returns:
        ts_code → 60 日特质波动率（小数）
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < WINDOW_VOL:
        return pd.Series(dtype=float, name="ivol_60d")

    start = valid[-WINDOW_VOL]
    dq = load_daily_quote(start, rebalance_date, codes=codes)
    if dq.empty:
        return pd.Series(dtype=float, name="ivol_60d")

    # 市场收益率
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")["index_ret"]
    mkt = iq.loc[start:rebalance_date]

    ret_pivot = dq["ret"].unstack("ts_code")
    # 对齐日期（只保留两者均有数据的交易日）
    common_dates = ret_pivot.index.intersection(mkt.index)
    ret_pivot = ret_pivot.loc[common_dates]
    mkt = mkt.loc[common_dates]

    valid_counts = ret_pivot.notna().sum()
    # 填充缺失为 0（停牌→零收益假设），便于向量化 OLS
    Y = ret_pivot.fillna(0).values           # (T, N)
    X = np.column_stack([np.ones(len(mkt)), mkt.values])  # (T, 2)

    # 向量化 OLS：beta = (X'X)^{-1} X' Y
    try:
        beta, *_ = np.linalg.lstsq(X, Y, rcond=None)   # (2, N)
    except np.linalg.LinAlgError:
        return pd.Series(np.nan, index=pd.Index(codes, name="ts_code"), name="ivol_60d")

    residuals = Y - X @ beta                 # (T, N)
    ivol = residuals.std(axis=0, ddof=1)     # (N,)
    result = pd.Series(ivol, index=ret_pivot.columns, name="ivol_60d")

    # 有效天数不足的股票置 NaN
    result[valid_counts < MIN_IVOL_PERIODS] = np.nan
    return result.reindex(codes)


def factor_max_ret(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    最大日收益率因子（过去 20 个交易日最大单日涨幅）。

    基于 Bali et al. (2011) 的 MAX 效应：极端涨幅的股票次期倾向回调。
    使用 daily_quote.ret（停牌日 ret=0，不影响最大值计算）。

    预期方向：-（高 MAX 的股票预期负超额收益）

    Returns:
        ts_code → 20 日最大单日收益率（小数）
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < WINDOW_RET_1M:
        return pd.Series(dtype=float, name="max_ret")

    start = valid[-WINDOW_RET_1M]
    dq = load_daily_quote(start, rebalance_date, codes=codes)
    if dq.empty:
        return pd.Series(dtype=float, name="max_ret")

    ret_pivot = dq["ret"].unstack("ts_code")
    result = ret_pivot.max(skipna=True)
    result.name = "max_ret"
    return result.reindex(codes)


# ---------------------------------------------------------------------------
# 流动性类（2 个）
# ---------------------------------------------------------------------------

def factor_turn_20d(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    换手率因子（过去 20 个交易日自由流通换手率均值）。

    使用 daily_basic.turnover_rate_f（自由流通换手率，百分比）。
    自由流通换手率比全流通换手率更能反映活跃交易者的行为。

    预期方向：-（低换手溢价，换手率低的股票预期超额收益）

    Returns:
        ts_code → 20 日平均自由流通换手率（百分比）
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < WINDOW_TURN:
        return pd.Series(dtype=float, name="turn_20d")

    start = valid[-WINDOW_TURN]
    basic = load_daily_basic(start, rebalance_date, codes=codes)
    if basic.empty:
        return pd.Series(dtype=float, name="turn_20d")

    turn_pivot = basic["turnover_rate_f"].unstack("ts_code")
    result = turn_pivot.mean(skipna=True)
    result.name = "turn_20d"
    return result.reindex(codes)


def factor_amihud(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    Amihud 非流动性因子（过去 20 个交易日 |ret|/amount 均值）。

    公式：amihud_i = mean(|ret_t| / amount_t)，amount 单位千元。
    停牌日 amount=0 排除（除零处理），这些日也不计入均值。
    Amihud 值越大 = 流动性越差 = 非流动性溢价越高。

    注：因子值量纲为 1/千元，数值较小，但排序一致性不受影响（标准化后消除量纲）。

    预期方向：+（高非流动性溢价，非流动性越大预期收益越高）

    Returns:
        ts_code → 20 日平均 Amihud 比率（1/千元）
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < WINDOW_AMIHUD:
        return pd.Series(dtype=float, name="amihud")

    start = valid[-WINDOW_AMIHUD]
    dq = load_daily_quote(start, rebalance_date, codes=codes)
    if dq.empty:
        return pd.Series(dtype=float, name="amihud")

    # 计算每日比率，停牌日 amount=0 → NaN（不参与均值）
    ret_abs = dq["ret"].abs()
    amount  = dq["amount"].replace(0, np.nan)
    daily_ratio = ret_abs / amount

    result = daily_ratio.unstack("ts_code").mean(skipna=True)
    result.name = "amihud"
    return result.reindex(codes)


# ---------------------------------------------------------------------------
# 资金流向类（2 个）
# ---------------------------------------------------------------------------

def factor_margin_ratio(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    融资余额占比因子（融资余额 / 总市值）。

    仅在 T 日截面取值（非窗口均值）：rzye / (total_mv × 10000)。
    非两融标的股票 rzye=NaN，因子值保持 NaN（由组合优化模块统一填 0，preprocess 不填充）。

    单位转换：rzye 元 / (total_mv 万元 × 10000) = 无量纲比率。

    预期方向：待测（高融资余额可能反映机构看多，也可能是拥挤交易信号）

    Returns:
        ts_code → 融资余额 / 总市值（无量纲）
    """
    # 融资数据（pyarrow 谓词下推，仅取当日）
    mg = load_margin(rebalance_date, rebalance_date, codes=codes)
    if rebalance_date not in mg.index.get_level_values("trade_date"):
        return pd.Series(dtype=float, name="margin_ratio")
    rzye_t = mg.loc[rebalance_date]["rzye"]

    # 总市值（万元 → 元）
    basic = load_daily_basic(rebalance_date, rebalance_date, codes=codes)
    if rebalance_date not in basic.index.get_level_values("trade_date"):
        return pd.Series(dtype=float, name="margin_ratio")
    mv_yuan = basic.loc[rebalance_date]["total_mv"] * 10_000.0

    # 对齐 index（两融标的子集 ∩ 成分股）
    common = rzye_t.index.intersection(mv_yuan.index)
    result = rzye_t[common] / mv_yuan[common]
    result.name = "margin_ratio"
    return result.reindex(codes)


def factor_short_ratio(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    融券余额占比因子（融券余额 / 总市值）。

    仅在 T 日截面取值：rqye / (total_mv × 10000)。
    非两融标的股票 rqye=NaN，因子值保持 NaN（preprocess 不填充）。
    与 margin_ratio（融资余额占比）方向相反：融券余额高反映做空意愿强。

    单位转换：rqye 元 / (total_mv 万元 × 10000) = 无量纲比率。

    预期方向：-（融券余额高预期负超额收益，市场看空信号）

    Returns:
        ts_code → 融券余额 / 总市值（无量纲）
    """
    mg = load_margin(rebalance_date, rebalance_date, codes=codes)
    if rebalance_date not in mg.index.get_level_values("trade_date"):
        return pd.Series(dtype=float, name="short_ratio")
    rqye_t = mg.loc[rebalance_date]["rqye"]

    basic = load_daily_basic(rebalance_date, rebalance_date, codes=codes)
    if rebalance_date not in basic.index.get_level_values("trade_date"):
        return pd.Series(dtype=float, name="short_ratio")
    mv_yuan = basic.loc[rebalance_date]["total_mv"] * 10_000.0

    common = rqye_t.index.intersection(mv_yuan.index)
    result = rqye_t[common] / mv_yuan[common]
    result.name = "short_ratio"
    return result.reindex(codes)


def factor_large_net_inflow(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    大单净流入占比因子（过去 20 个交易日大单净流入 / 成交额均值）。

    公式（日度）：ratio_t = net_mf_amount_t × 10 / amount_t
      net_mf_amount 单位万元，×10 转千元；amount 单位千元
      → ratio 为无量纲比率

    20 日均值消除单日噪声，反映近期机构资金流向趋势。
    无 moneyflow 数据的股票（或成交额为 0 的日期）自动为 NaN。

    预期方向：+（大单净流入越大预期越正向，反映机构看多）

    Returns:
        ts_code → 20 日平均大单净流入占比（无量纲）
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < WINDOW_FLOW:
        return pd.Series(dtype=float, name="large_net_inflow")

    start = valid[-WINDOW_FLOW]
    mf = load_moneyflow(start, rebalance_date, codes=codes)
    dq = load_daily_quote(start, rebalance_date, codes=codes)

    if mf.empty or dq.empty:
        return pd.Series(dtype=float, name="large_net_inflow")

    # 对齐两个 MultiIndex DataFrame
    net_mf  = mf["net_mf_amount"]             # 万元
    amount  = dq["amount"].replace(0, np.nan)  # 千元

    # 单位统一：net_mf 万元 × 10 = 千元
    daily_ratio = (net_mf * 10) / amount

    result = daily_ratio.unstack("ts_code").mean(skipna=True)
    result.name = "large_net_inflow"
    return result.reindex(codes)


# ---------------------------------------------------------------------------
# 阶段 5：动量类新因子（high_52w / ind_adj_mom / mom_risk_adj）
# ---------------------------------------------------------------------------

def factor_high_52w(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    52 周高点比率因子：T 日后复权收盘价 / 过去 252 交易日（跳过最近 20 日）最高后复权价。

    公式：close_adj[T] / max(high × adj_factor, T-252 到 T-20 交易日)
    跳过最近 20 个交易日，与 mom_12_1 保持一致，消除短期反转干扰。

    经济含义：接近 52 周高点的股票具有强势动能支撑；散户心理锚点效应。
    比纯价格动量（mom_12_1）更鲁棒——衡量相对稀缺性而非绝对涨跌幅；
    在股灾后市场中，接近高点的股票通常是真正抗跌的。

    历史不足 252 个交易日时返回全 NaN（训练期早期调仓日正常现象）。

    预期方向：+（高比率预期正超额收益）

    Returns:
        ts_code → close_adj / max_high_adj（无量纲，取值区间 (0, 1]）
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < WINDOW_HIGH_52W:
        return pd.Series(dtype=float, index=pd.Index(codes, name="ts_code"), name="high_52w")

    close_date   = valid[-1]                          # T 日
    window_end   = valid[-(WINDOW_MOM_SKIP + 1)]      # T 前第 21 个交易日（跳过最近 20 日）
    window_start = valid[-WINDOW_HIGH_52W]            # T 前约 252 个交易日

    dq_hist  = load_daily_quote(window_start, window_end, codes=codes)
    dq_today = load_daily_quote(close_date, close_date, codes=codes)

    if dq_hist.empty or dq_today.empty:
        return pd.Series(dtype=float, index=pd.Index(codes, name="ts_code"), name="high_52w")

    # high_adj = high × adj_factor（两者均在 daily_quote 中预计算）
    high_adj_hist = (dq_hist["high"] * dq_hist["adj_factor"]).unstack("ts_code")
    high_52w_max  = high_adj_hist.max().replace(0, np.nan)

    close_t = dq_today["close_adj"].unstack("ts_code").iloc[-1]

    result      = close_t / high_52w_max
    result.name = "high_52w"
    return result.reindex(codes)


def factor_high_52w_v2(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    修正版 52 周高点比率（v2）：分子用 T-20 收盘价，消除机械拖累。

    原版问题：分子 = close_adj(T)，与 fwd_ret[T]=close(T+1)/close(T) 共享 close(T)，
    产生机械负相关，IC 被压低至 0.054，lead_ratio = 37.6x（Gate 2 拒绝）。

    修正：分子改为 close_adj(T-20td)，与分母窗口终点相同，
    T-20td 不出现在 fwd_ret[T] 的计算中，消除机械拖累。

    窗口：[T-271td, T-20td]（共 252 个交易日，跳过近 20 日后仍保留完整 52 周窗口）
    分子：close_adj(T-20td)
    分母：max(high × adj_factor, T-271td 到 T-20td)

    Args:
        rebalance_date: 调仓日 T
        codes:          当期可投资域股票代码
    Returns:
        ts_code → close_adj(T-20) / max_52w_high（无量纲，≤ 1）；历史不足时为 NaN
    Time alignment: 窗口 [T-271td, T-20td]，不包含 T 日数据，无未来数据
    Data deps: daily_quote.parquet
    """
    valid = _valid_dates_before(rebalance_date)
    needed = WINDOW_HIGH_52W + WINDOW_MOM_SKIP   # 252 + 20 = 272
    if len(valid) < needed:
        return pd.Series(
            np.nan,
            index=pd.Index(codes, name="ts_code"),
            name="high_52w_v2",
        )

    window_end_pos   = len(valid) - 1 - WINDOW_MOM_SKIP         # T-20 的位置
    window_start_pos = window_end_pos - WINDOW_HIGH_52W + 1     # 向前取满 252 个交易日
    window_end       = valid[window_end_pos]                     # T-20 日
    window_start     = valid[window_start_pos]                   # T-271 日

    dq = load_daily_quote(window_start, window_end, codes=codes)
    if dq.empty:
        return pd.Series(
            np.nan,
            index=pd.Index(codes, name="ts_code"),
            name="high_52w_v2",
        )

    # 分母：52 周最高后复权价
    high_adj     = (dq["high"] * dq["adj_factor"]).unstack("ts_code")
    high_52w_max = high_adj.max().replace(0, np.nan)

    # 分子：T-20 日后复权收盘价（不引入 T 日价格，消除与 fwd_ret 的机械相关）
    close_adj_panel = dq["close_adj"].unstack("ts_code")
    if window_end not in close_adj_panel.index:
        return pd.Series(
            np.nan,
            index=pd.Index(codes, name="ts_code"),
            name="high_52w_v2",
        )
    close_t_minus_20 = close_adj_panel.loc[window_end]

    result      = close_t_minus_20 / high_52w_max
    result.name = "high_52w_v2"
    return result.reindex(codes)


def factor_ind_adj_mom_6_1(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    行业调整 6 个月动量（ind_adj_mom 的 6 个月修正版）。

    公式：mom_6_1_i − equal_weight_mean(mom_6_1, 同 SW2021 行业)

    原版 ind_adj_mom 底层用 mom_12_1（lead_ratio=3.25x，Gate 2 拒绝）；
    本版改用 mom_6_1（窗口 [T-120td, T-20td]，lead_ratio=0.0），消除数据污染。

    行业均值：等权，只在当期中证500宇宙内计算。
    若行业数据在调仓日不可用，返回全 NaN（不降级为 mom_6_1，避免信息重叠）。

    Args:
        rebalance_date: 调仓日 T
        codes:          当期可投资域股票代码
    Returns:
        ts_code → 行业调整 6 个月动量（小数形式）；数据不足时为 NaN
    Time alignment: 底层 mom_6_1 窗口 [T-120td, T-20td]，无未来数据
    Data deps: daily_quote.parquet, industry.parquet
    """
    raw_mom = factor_mom_6_1(rebalance_date, codes)
    if raw_mom.isna().all():
        return raw_mom.rename("ind_adj_mom_6_1")

    ind_df = load_industry(rebalance_date, rebalance_date, codes=codes)
    if ind_df.empty:
        return pd.Series(np.nan, index=raw_mom.index, name="ind_adj_mom_6_1")

    trade_dates = ind_df.index.get_level_values("trade_date")
    if rebalance_date not in trade_dates:
        return pd.Series(np.nan, index=raw_mom.index, name="ind_adj_mom_6_1")

    ind_series  = ind_df.loc[rebalance_date]["industry_code"]
    ind_aligned = ind_series.reindex(raw_mom.index)

    # 等权行业均值（transform 保持 index 对齐；行业 NaN 的股票自动排除，不影响其余行业）
    ind_mean    = raw_mom.groupby(ind_aligned).transform("mean")
    result      = raw_mom - ind_mean
    result.name = "ind_adj_mom_6_1"
    return result


def factor_mom_consistency_6(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    月涨幅一致性动量因子：过去 6 个月（跳过最近 1 个月）中正收益月数占比 × 总收益方向。

    公式：
      正收益月数比例 = count(monthly_ret > 0) / 6
      总收益方向    = sign(close_adj[T-1m末] / close_adj[T-7m末] - 1)
      因子值        = 正收益月数比例 × 总收益方向，取值 ∈ [-1, +1]

    优势：抗单月爆涨干扰——5 个月平盘 + 1 个月暴涨的股票，累计收益可能很高，
    但一致性因子分更低，更能捕捉持续性趋势而非单期噪声。

    月度收益截尾 [-0.4, +0.4]：停牌复牌的极端月收益属异常值，截尾后计数更稳健。

    Args:
        rebalance_date: 调仓日 T
        codes:          当期可投资域股票代码
    Returns:
        ts_code → 一致性动量（[-1, +1] 区间）；有效月数 < 4 时为 NaN
    Time alignment: 最新用到 T-1m 月末价格，窗口 [T-7m末, T-1m末]，无未来数据
    Data deps: daily_quote.parquet
    """
    n_calendar_months = WINDOW_CONSISTENCY_LOOKBACK + WINDOW_CONSISTENCY_SKIP  # 7

    # 获取 T 前 7 个自然月末的实际交易日（最老 → 最新）
    # 结果为 [T-7m末, T-6m末, T-5m末, T-4m末, T-3m末, T-2m末, T-1m末]
    month_pool = _prev_month_end_dates(rebalance_date, n=n_calendar_months)
    if len(month_pool) < n_calendar_months:
        return pd.Series(
            np.nan,
            index=pd.Index(codes, name="ts_code"),
            name="mom_consistency_6",
        )
    month_ends = month_pool[: WINDOW_CONSISTENCY_LOOKBACK + 1]   # 全部 7 个月末

    dq = load_daily_quote(month_ends[0], month_ends[-1], codes=codes)
    if dq.empty:
        return pd.Series(
            np.nan,
            index=pd.Index(codes, name="ts_code"),
            name="mom_consistency_6",
        )

    close_adj      = dq["close_adj"].unstack("ts_code")   # 日期 × ts_code
    monthly_prices = close_adj.reindex(month_ends)         # 7 个月末价格（停牌月为 NaN）

    # 月度收益：显式除法，不用 pct_change() 默认前向填充（停牌月保持 NaN）
    monthly_rets = (monthly_prices / monthly_prices.shift(1) - 1).iloc[1:]  # 6 行
    monthly_rets = monthly_rets.clip(-MONTHLY_RET_CAP, MONTHLY_RET_CAP)

    valid_months   = monthly_rets.notna().sum()
    positive_count = (monthly_rets > 0).sum()

    # 分母固定为 6（有效月数不足由下方 valid_months 门槛兜底）
    positive_ratio = positive_count / WINDOW_CONSISTENCY_LOOKBACK

    # 6 个月累计收益方向（T-7m末 → T-1m末）
    cum_ret  = monthly_prices.iloc[-1] / monthly_prices.iloc[0] - 1
    cum_sign = np.sign(cum_ret)

    result = positive_ratio * cum_sign
    result[valid_months < MIN_CONSISTENCY_MONTHS] = np.nan
    result.name = "mom_consistency_6"
    return result.reindex(codes)


def factor_ind_adj_mom(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    行业调整动量因子（12 个月，跳过最近 1 个月）。

    公式：mom_12_1_i − mean(mom_12_1, 同申万一级行业)

    经济含义：剔除行业轮动效应后的纯个股超强/超弱动量。
    A 股 2016-2021 行业轮动极强（消费→科技→周期快速切换），纯价格动量大量被
    行业动量污染；行业调整后的个股内部动量在截面上更稳定。

    若当期行业分类数据不可用，返回全 NaN（不降级为原始动量，避免与 mom_12_1 信息重叠）。

    预期方向：+（行业内跑赢的股票下期继续跑赢）

    Returns:
        ts_code → ind_adj_mom（小数收益率，行业均值已去除）；历史不足时为 NaN
    """
    raw_mom = factor_mom_12_1(rebalance_date, codes)
    if raw_mom.isna().all():
        return raw_mom.rename("ind_adj_mom")

    ind_df = load_industry(rebalance_date, rebalance_date, codes=codes)
    if ind_df.empty:
        return pd.Series(np.nan, index=raw_mom.index, name="ind_adj_mom")

    trade_dates = ind_df.index.get_level_values("trade_date")
    if rebalance_date not in trade_dates:
        return pd.Series(np.nan, index=raw_mom.index, name="ind_adj_mom")

    ind_series  = ind_df.loc[rebalance_date]["industry_code"]    # ts_code → 行业代码
    ind_aligned = ind_series.reindex(raw_mom.index)              # 对齐到 mom 的 index

    ind_mean    = raw_mom.groupby(ind_aligned).transform("mean")
    result      = raw_mom - ind_mean
    result.name = "ind_adj_mom"
    return result


def factor_mom_risk_adj(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    风险调整动量因子（Sharpe 式动量）：mom_12_1 / ivol_60d。

    经济含义：相同涨幅下，低波动率的动量信号质量更高——
    高波动股的涨幅往往是随机游走，不具有持续性；
    低波动股的涨幅更可能是基本面驱动的持续性信号。

    A 股特殊价值：2015 年股灾中高波动+高动量股票跌幅最惨（杠杆驱动而非基本面），
    风险调整能过滤掉此类"虚假动量"。

    ivol_60d = 0 时结果为 NaN（防止除零）。

    预期方向：+（高风险调整动量预期正超额收益）

    Returns:
        ts_code → mom_12_1 / ivol_60d（无量纲）；任一因子缺失时为 NaN
    """
    mom  = factor_mom_12_1(rebalance_date, codes)
    ivol = factor_ivol_60d(rebalance_date, codes)

    result      = mom / ivol.replace(0, np.nan)
    result.name = "mom_risk_adj"
    return result


def factor_share_issuance(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    股本稀释因子（取反后为正向因子）。

    经济逻辑：管理层在股价高估时倾向增发（负向信号）；在低估时回购（正向信号）。
    Loughran & Ritter (1995) 发现增发后 5 年内股票系统性跑输；
    A 股创业板/科创板定向增发极为频繁，使该因子具有持续差异化价值。

    share_change = (total_share_T - total_share_{T-252td}) / total_share_{T-252td}
    Factor = -share_change（增发 = 负向，回购/注销 = 正向，取反后正向因子）

    PIT 合规：daily_basic.total_share 是交易所实时数据，无前视偏差。
    极端值截尾至 [-1, 1]（100% 增发/回购视为异常重组事件）。

    预期方向：+（股本缩减的公司预期超额收益更高）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → -yoy_share_change（小数）；历史不足时为 NaN
    Time alignment: 两端均为已发生的市场数据，无未来函数
    Data deps: daily_basic.parquet
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) <= WINDOW_HIGH_52W:
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="share_issuance")

    one_year_ago = valid[-(WINDOW_HIGH_52W + 1)]   # T 前第 252 个交易日（不含 T）

    basic_now  = load_daily_basic(rebalance_date, rebalance_date,  codes=codes)
    basic_prev = load_daily_basic(one_year_ago,   one_year_ago,    codes=codes)

    now_dates  = basic_now.index.get_level_values("trade_date")
    prev_dates = basic_prev.index.get_level_values("trade_date")
    if rebalance_date not in now_dates or one_year_ago not in prev_dates:
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="share_issuance")

    shares_now  = basic_now.loc[rebalance_date]["total_share"]
    shares_prev = basic_prev.loc[one_year_ago]["total_share"]

    common = shares_now.index.intersection(shares_prev.index)
    denom  = shares_prev[common].replace(0, np.nan)

    share_change = ((shares_now[common] - shares_prev[common]) / denom).clip(-1, 1)

    result      = -share_change
    result.name = "share_issuance"
    return result.reindex(codes)


def factor_mf_flow_ratio(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    近期融资净买入流量比（正向动量信号）。

    mf_flow_ratio = sum(rz_net, 近20交易日) / 当日流通市值

    经济逻辑：近期本土杠杆资金净流入 → 短期需求支撑 → 正向动量信号。
    与 hk_hold_chg（北向资金）互补，覆盖 A 股本土散户/杠杆资金视角；
    中证500成分股两融覆盖率 > 95%，覆盖率充足。

    与现有因子的相关性预估：
      turn_20d：r ≈ 0.25-0.40（高融资买入伴随高换手，Ridge 可自然处理）
      hk_hold_chg：r < 0.20（不同资金来源，独立性强）

    注意：优先使用 rz_net 直接列；若缺失则由 rzmre - rzche 计算；
    两者均不可用时返回全 NaN（不回退到 rzye）。

    预期方向：+（融资净流入越多，短期价格支撑越强）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → 20日融资净买入 / 流通市值（无量纲）；无数据时为 NaN
    Time alignment: 使用 [T-20td, T] 的融资流量数据，不包含未来
    Data deps: margin.parquet, daily_basic.parquet
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < WINDOW_MF_FLOW:
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_flow_ratio")

    start = valid[-WINDOW_MF_FLOW]
    mg    = load_margin(start, rebalance_date, codes=codes)

    if mg.empty:
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_flow_ratio")

    if "rz_net" in mg.columns:
        flow = mg["rz_net"]
    elif {"rzmre", "rzche"}.issubset(mg.columns):
        flow = mg["rzmre"] - mg["rzche"]
    else:
        log.warning("mf_flow_ratio: margin 缓存缺少 rz_net 或 rzmre/rzche，返回全 NaN")
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_flow_ratio")

    net_buy_20d = flow.unstack("ts_code").fillna(0.0).sum(axis=0)   # 20日累计净买入（元）

    # 流通市值（daily_basic.circ_mv 万元 → 元，与 margin 数据元单位对齐）
    basic = load_daily_basic(rebalance_date, rebalance_date, codes=codes)
    if rebalance_date not in basic.index.get_level_values("trade_date"):
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_flow_ratio")
    circ_mv_yuan = basic.loc[rebalance_date]["circ_mv"] * _MF_WAN_TO_YUAN

    common = net_buy_20d.index.intersection(circ_mv_yuan.index)
    result = net_buy_20d[common] / circ_mv_yuan[common].replace(0, np.nan)
    result.name = "mf_flow_ratio"
    return result.reindex(codes)


# ---------------------------------------------------------------------------
# 批量构建入口
# ---------------------------------------------------------------------------

_PRICE_FACTOR_BUILDERS = {
    # 动量/反转
    "ret_1m":           factor_ret_1m,
    "mom_6_1":          factor_mom_6_1,
    "mom_12_1":         factor_mom_12_1,
    "holder_chg":       factor_holder_chg,
    # 波动率
    "vol_60d":          factor_vol_60d,
    "ivol_60d":         factor_ivol_60d,
    "max_ret":          factor_max_ret,
    # 流动性
    "turn_20d":         factor_turn_20d,
    "amihud":           factor_amihud,
    # 资金流向
    "margin_ratio":     factor_margin_ratio,
    "short_ratio":      factor_short_ratio,
    "large_net_inflow": factor_large_net_inflow,
    # 阶段 5：动量类因子（原有）
    "high_52w":           factor_high_52w,
    "ind_adj_mom":        factor_ind_adj_mom,
    "mom_risk_adj":       factor_mom_risk_adj,
    # Sprint 1：修正版动量因子
    "high_52w_v2":        factor_high_52w_v2,
    "ind_adj_mom_6_1":    factor_ind_adj_mom_6_1,
    "mom_consistency_6":  factor_mom_consistency_6,
    # 阶段 7：股本行为 + 融资资金
    "share_issuance":     factor_share_issuance,
    "mf_flow_ratio":      factor_mf_flow_ratio,
}


def build_all_price_factors(
    rebalance_date: pd.Timestamp,
    codes: list[str],
    factor_names: Optional[list[str]] = None,
) -> dict[str, pd.Series]:
    """
    构建指定调仓日的所有量价因子原始值。

    返回值为原始因子（未预处理），需经 preprocess_factor 后用于 IC 检验或优化。

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表（来自 get_investable_universe）
        factor_names:   指定因子子集；None 则构建全部 18 个因子

    Returns:
        dict：key = 因子名，value = ts_code → 原始因子值 Series
    """
    names = factor_names if factor_names is not None else list(_PRICE_FACTOR_BUILDERS)
    unknown = set(names) - set(_PRICE_FACTOR_BUILDERS)
    if unknown:
        raise ValueError(f"未知因子名：{sorted(unknown)}")

    result: dict[str, pd.Series] = {}
    for name in names:
        try:
            result[name] = _PRICE_FACTOR_BUILDERS[name](rebalance_date, codes)
        except Exception as exc:
            log.error("价格因子 %s 在 %s 构建失败：%s", name, rebalance_date.date(), exc)
            result[name] = pd.Series(np.nan,
                                     index=pd.Index(codes, name="ts_code"),
                                     name=name)

    log.info(
        "build_all_price_factors(%s): 构建 %d 个因子，代码 %d 只",
        rebalance_date.date(), len(result), len(codes),
    )
    return result
