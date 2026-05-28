"""
src/factors/financial_factors.py — 财务因子构建（第二次对话产出）
==========================================================

实现 15 个财务因子（价值 5 + 质量 6 + 成长 4）。

返回值约定：
  - 每个 factor_xxx 函数返回以 ts_code 为 index 的原始因子值 Series
  - 原始值不经过去极值/标准化/中性化（由 preprocess_factor 处理）
  - NaN 表示数据缺失，不做任何填充

数据口径说明：
  - 价值因子的市值分母：daily_basic.total_mv（万元）× 10000 = 元
  - 财务分子：financial_pit 字段单位为元，量纲一致
  - 质量/成长因子：来自 indicator_pit，roe/roa/grossprofit_margin/yoy 为百分比，
    assets_turn 为倍数比率；因子直接使用原始百分比值（标准化后量纲消除）

PIT 保证：
  所有函数均通过 pit_loader 的 PIT 过滤确保仅使用 T 日之前已披露的数据。
  dy_ttm 例外：来自 daily_basic.dv_ttm（非严格 PIT），已在函数文档中标注。
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from src import config as cfg
from src.data.loader import (
    get_financial_pit_raw,
    get_indicator_pit_raw,
    load_daily_basic,
)
from src.data.pit_loader import (
    get_field_yoy_delta,
    get_pit_latest,
    get_quarterly_history,
    get_roe_delta,
    make_ttm,
)

log = logging.getLogger(__name__)

# 万元 → 元的转换系数（daily_basic.total_mv 单位为万元）
_WAN_TO_YUAN: float = 10_000.0

# roe_stability 至少需要的有效季度数（低于此数时标准差估计不稳定）
MIN_QUARTERS_STABILITY: int = 4


def _get_total_mv_yuan(rebalance_date: pd.Timestamp,
                       codes: list[str]) -> pd.Series:
    """
    从 daily_basic 取调仓日当天的总市值（转换为元）。

    total_mv 在 daily_basic 中以万元为单位，此处统一转为元，
    与 financial_pit 的货币字段（元）量纲一致。

    Returns:
        以 ts_code 为 index 的总市值 Series（元）；当天无数据的股票为 NaN
    """
    basic = load_daily_basic(rebalance_date, rebalance_date, codes=codes)
    if rebalance_date not in basic.index.get_level_values("trade_date"):
        log.warning("daily_basic 中无 %s 的数据", rebalance_date.date())
        return pd.Series(dtype=float, name="total_mv_yuan")
    mv_wan = basic.loc[rebalance_date]["total_mv"]
    mv_yuan = mv_wan * _WAN_TO_YUAN
    mv_yuan.name = "total_mv_yuan"
    return mv_yuan


# ---------------------------------------------------------------------------
# 价值类（5 个）
# ---------------------------------------------------------------------------

def factor_ep_ttm(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    收益率因子（TTM Earnings-to-Price）：TTM 净利润 / 总市值。

    严格 PIT：分子来自 financial_pit（pit_date <= T），分母来自 daily_basic（T 日收盘后）。
    TTM 净利润通过 make_ttm 从累计季报拼接，消除季节性偏差。

    预期方向：+（价值溢价，高 E/P 预期超额收益）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表（从 get_investable_universe 获取）
    Returns:
        ts_code → ep_ttm（无量纲比率）；数据缺失时为 NaN
    """
    fp_raw   = get_financial_pit_raw()
    ttm_ni   = make_ttm(fp_raw, "n_income", rebalance_date, codes)
    mv_yuan  = _get_total_mv_yuan(rebalance_date, codes)
    ep       = ttm_ni / mv_yuan.reindex(ttm_ni.index)
    ep.name  = "ep_ttm"
    return ep


def factor_bp(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    账面市值比因子（Book-to-Price）：PIT 归母净资产 / 总市值。

    净资产为资产负债表存量指标，取最新 PIT 快照（无需 TTM 化）。
    净资产可能为负（高杠杆或 ST 股票）→ winsorize 后仍有效。

    预期方向：+（价值溢价）

    Returns:
        ts_code → bp（无量纲比率）
    """
    fp_raw  = get_financial_pit_raw()
    latest  = get_pit_latest(fp_raw, rebalance_date, codes)
    if latest.empty:
        return pd.Series(dtype=float, name="bp")

    equity  = latest["total_hldr_eqy_exc_min_int"]   # 元
    mv_yuan = _get_total_mv_yuan(rebalance_date, codes)
    bp      = equity / mv_yuan.reindex(equity.index)
    bp.name = "bp"
    return bp


def factor_sp_ttm(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    销售收益率因子（TTM Sales-to-Price）：TTM 营业收入 / 总市值。

    预期方向：+（价值溢价）

    Returns:
        ts_code → sp_ttm（无量纲比率）
    """
    fp_raw   = get_financial_pit_raw()
    ttm_rev  = make_ttm(fp_raw, "revenue", rebalance_date, codes)
    mv_yuan  = _get_total_mv_yuan(rebalance_date, codes)
    sp       = ttm_rev / mv_yuan.reindex(ttm_rev.index)
    sp.name  = "sp_ttm"
    return sp


def factor_dy_ttm(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    股息率因子（TTM Dividend Yield）：来自 daily_basic.dv_ttm。

    ⚠️ 非严格 PIT：Tushare 的 dv_ttm 基于最新财报计算，可能有 0-60 天前视偏差。
    正式评估时需用 dividend_pit 重建，此处作为探索性近似版本。
    dv_ttm 单位为百分比（如 2.5 表示 2.5%），直接使用无需转换（标准化后量纲消除）。

    预期方向：+（高股息溢价）

    Returns:
        ts_code → dy_ttm（百分比，近似 PIT）
    """
    basic = load_daily_basic(rebalance_date, rebalance_date, codes=codes)
    if rebalance_date not in basic.index.get_level_values("trade_date"):
        return pd.Series(dtype=float, name="dy_ttm")
    dy      = basic.loc[rebalance_date]["dv_ttm"].reindex(codes)
    dy.name = "dy_ttm"
    return dy


def factor_cfp(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    现金流收益率因子（TTM Cash Flow-to-Price）：TTM 经营现金流净额 / 总市值。

    n_cashflow_act 为累计值，需 TTM 化消除季节性偏差。
    经营现金流可为负（资本密集型企业），不做截断。

    预期方向：+（高现金流溢价）

    Returns:
        ts_code → cfp（无量纲比率）
    """
    fp_raw   = get_financial_pit_raw()
    ttm_cfo  = make_ttm(fp_raw, "n_cashflow_act", rebalance_date, codes)
    mv_yuan  = _get_total_mv_yuan(rebalance_date, codes)
    cfp      = ttm_cfo / mv_yuan.reindex(ttm_cfo.index)
    cfp.name = "cfp"
    return cfp


def factor_fcfp(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    自由现金流收益率因子（TTM Free Cash Flow-to-Price）：TTM 自由现金流 / 总市值。

    free_cashflow = 经营现金流净额 - 资本支出（Tushare 预计算），
    比 n_cashflow_act 扣除了维持性资本开支，对重资产行业（制造、地产）区分度更高。
    与 cfp（经营现金流收益率）互补：两者差距大时反映资本密集度高。

    TTM 化消除季节性偏差；free_cashflow 可为负（高资本支出期），不做截断。

    预期方向：+（高自由现金流溢价）

    Returns:
        ts_code → fcfp（无量纲比率）；数据缺失时为 NaN
    """
    fp_raw    = get_financial_pit_raw()
    ttm_fcf   = make_ttm(fp_raw, "free_cashflow", rebalance_date, codes)
    mv_yuan   = _get_total_mv_yuan(rebalance_date, codes)
    fcfp      = ttm_fcf / mv_yuan.reindex(ttm_fcf.index)
    fcfp.name = "fcfp"
    return fcfp


# ---------------------------------------------------------------------------
# 质量类（5 个）
# ---------------------------------------------------------------------------

def factor_roe(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    净资产收益率因子（ROE）：来自 indicator_pit.roe。

    单位：百分比（如 10.5 表示 ROE=10.5%）。
    Tushare 使用加权平均净资产计算，方法论与 wind 一致。
    无需 TTM 化（indicator_pit 的 roe 已是该报告期的全期值）。

    预期方向：+（高 ROE 预期超额收益）

    Returns:
        ts_code → roe（百分比）
    """
    ip_raw  = get_indicator_pit_raw()
    latest  = get_pit_latest(ip_raw, rebalance_date, codes)
    if latest.empty:
        return pd.Series(dtype=float, name="roe")
    roe      = latest["roe"].reindex(codes)
    roe.name = "roe"
    return roe


def factor_roa(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    总资产收益率因子（ROA）：来自 indicator_pit.roa。

    单位：百分比。ROA 使用平均资产作为分母（与直接用 financial_pit 的
    年末 total_assets 计算有差异），直接使用 Tushare 的预计算值更准确。

    预期方向：+（高 ROA 预期超额收益）

    Returns:
        ts_code → roa（百分比）
    """
    ip_raw  = get_indicator_pit_raw()
    latest  = get_pit_latest(ip_raw, rebalance_date, codes)
    if latest.empty:
        return pd.Series(dtype=float, name="roa")
    roa      = latest["roa"].reindex(codes)
    roa.name = "roa"
    return roa


def factor_gross_margin(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    毛利率因子（Gross Profit Margin）：来自 indicator_pit.grossprofit_margin。

    单位：百分比。金融股的毛利率定义不同，建议在 preprocess_factor 时
    传入 fin_sector_codes=FINANCIAL_SECTOR_CODES 过滤。

    预期方向：+（高毛利率 = 更强护城河，预期超额收益）

    Returns:
        ts_code → gross_margin（百分比）
    """
    ip_raw  = get_indicator_pit_raw()
    latest  = get_pit_latest(ip_raw, rebalance_date, codes)
    if latest.empty:
        return pd.Series(dtype=float, name="gross_margin")
    gm      = latest["grossprofit_margin"].reindex(codes)
    gm.name = "gross_margin"
    return gm


def factor_asset_turn(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    总资产周转率因子（Asset Turnover）：来自 indicator_pit.assets_turn。

    单位：倍数比率（如 0.27 表示年营收约为总资产的 27%），非百分比。
    高周转率反映资产利用效率高，对制造业和零售业尤其有效。

    预期方向：+（高周转 = 高效率，预期超额收益）

    Returns:
        ts_code → asset_turn（倍数比率）
    """
    ip_raw  = get_indicator_pit_raw()
    latest  = get_pit_latest(ip_raw, rebalance_date, codes)
    if latest.empty:
        return pd.Series(dtype=float, name="asset_turn")
    at      = latest["assets_turn"].reindex(codes)
    at.name = "asset_turn"
    return at


def factor_leverage(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    资产负债率因子（Debt-to-Assets）：来自 indicator_pit.debt_to_assets。

    单位：百分比（如 60.5 表示负债占总资产 60.5%）。
    直接使用 Tushare 预计算值，口径与主流数据库一致。
    注意：金融股（银行/保险）资产负债率天然 > 90%，preprocess 中已过滤金融股财务因子。

    预期方向：-（低杠杆溢价，低负债率公司财务健康、违约风险低）

    Returns:
        ts_code → debt_to_assets（百分比）；数据缺失时为 NaN
    """
    ip_raw   = get_indicator_pit_raw()
    latest   = get_pit_latest(ip_raw, rebalance_date, codes)
    if latest.empty:
        return pd.Series(dtype=float, name="leverage")
    lev      = latest["debt_to_assets"].reindex(codes)
    lev.name = "leverage"
    return lev


def factor_accrual(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    应计项目因子（Accrual）：(TTM净利润 - TTM经营现金流) / 最新总资产。

    高应计 = 净利润中非现金成分多 = 盈利质量差 = 预期负超额收益。
    分子：TTM 化消除累计口径的季节性；分母：存量指标，取最新 PIT 快照。

    guard：total_assets = 0 或 NaN 时返回 NaN（极罕见，但需防范除零）。

    预期方向：-（高应计对应低未来收益，即 Sloan 1996 异象）

    Returns:
        ts_code → accrual（无量纲比率）
    """
    fp_raw         = get_financial_pit_raw()
    ttm_ni         = make_ttm(fp_raw, "n_income",       rebalance_date, codes)
    ttm_cfo        = make_ttm(fp_raw, "n_cashflow_act", rebalance_date, codes)
    latest         = get_pit_latest(fp_raw, rebalance_date, codes)

    if latest.empty:
        return pd.Series(dtype=float, name="accrual")

    total_assets   = latest["total_assets"].reindex(ttm_ni.index)
    # 防止除以零或近零资产
    total_assets   = total_assets.replace(0, np.nan)

    accrual        = (ttm_ni - ttm_cfo) / total_assets
    accrual.name   = "accrual"
    return accrual


# ---------------------------------------------------------------------------
# 成长类（4 个）
# ---------------------------------------------------------------------------

def factor_np_yoy(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    净利润同比增速因子：来自 indicator_pit.netprofit_yoy。

    单位：百分比（如 15.3 表示同比增长 15.3%）。
    Tushare 直接提供同比值，无需重新计算。极端值（>500%）常出现在基数极低
    的小公司，winsorize 会截断。

    预期方向：+（高利润增速预期超额收益）

    Returns:
        ts_code → np_yoy（百分比）
    """
    ip_raw  = get_indicator_pit_raw()
    latest  = get_pit_latest(ip_raw, rebalance_date, codes)
    if latest.empty:
        return pd.Series(dtype=float, name="np_yoy")
    np_yoy      = latest["netprofit_yoy"].reindex(codes)
    np_yoy.name = "np_yoy"
    return np_yoy


def factor_rev_yoy(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    营业收入同比增速因子：来自 indicator_pit.or_yoy。

    单位：百分比。收入增速比利润增速更稳定（不受会计操纵影响），
    与 np_yoy 合用可区分增长质量。

    预期方向：+（高收入增速预期超额收益）

    Returns:
        ts_code → rev_yoy（百分比）
    """
    ip_raw  = get_indicator_pit_raw()
    latest  = get_pit_latest(ip_raw, rebalance_date, codes)
    if latest.empty:
        return pd.Series(dtype=float, name="rev_yoy")
    rv      = latest["or_yoy"].reindex(codes)
    rv.name = "rev_yoy"
    return rv


def factor_roe_delta(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    ROE 同比变化因子：当期 ROE - 上年同期 ROE（均来自 indicator_pit）。

    「上年同期」按报告期月份对齐：Q3 2023 对比 Q3 2022，Annual 2023 对比 Annual 2022。
    对于新上市股票（无上年同期数据），返回 NaN。

    单位：百分比差值（如 +2.3 表示 ROE 提升了 2.3 个百分点）。

    预期方向：+（ROE 改善趋势预期超额收益）

    Returns:
        ts_code → roe_delta（百分比差值）
    """
    ip_raw    = get_indicator_pit_raw()
    delta     = get_roe_delta(ip_raw, rebalance_date, codes)
    delta     = delta.reindex(codes)
    delta.name = "roe_delta"
    return delta


def factor_q_roe(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    单季 ROE 因子：来自 indicator_pit.q_roe。

    单季 ROE 反映当季盈利能力，比全年累计 ROE 对近期变化更敏感。
    单位：百分比（单季净利润 / 平均净资产）。
    注意：单季 ROE 方差远大于年化 ROE（极端值常见），winsorize 尤为重要。

    预期方向：+（高单季 ROE 预期超额收益）

    Returns:
        ts_code → q_roe（百分比）
    """
    ip_raw  = get_indicator_pit_raw()
    latest  = get_pit_latest(ip_raw, rebalance_date, codes)
    if latest.empty:
        return pd.Series(dtype=float, name="q_roe")
    qr      = latest["q_roe"].reindex(codes)
    qr.name = "q_roe"
    return qr


# ---------------------------------------------------------------------------
# 阶段 4：低风险因子扩展（时序平滑、改善速度、相对历史估值）
# ---------------------------------------------------------------------------

def factor_roe_smoothed_4q(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    4 季度单季 ROE 时序平滑因子：过去 4 个季报的 q_roe 均值。

    用途：去除单季一次性事件干扰（如资产处置利润），表达"持续盈利能力"。
    与 q_roe（单季快照）互补：q_roe 灵敏，roe_smoothed_4q 稳健。

    实现：取最近 4 个非 NaN q_roe 报告期的算术均值（不足 4 期时用可用期数）。
    陈旧度：最新期距 T 超过 9 个月则置 NaN（同 STALE_THRESHOLD_MONTHS）。

    预期方向：+（持续高 ROE 预期超额收益）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → roe_smoothed_4q（百分比，4 期均值）；缺失时为 NaN
    """
    ip_raw = get_indicator_pit_raw()
    hist   = get_quarterly_history(ip_raw, "q_roe", rebalance_date, codes, n_quarters=4)

    result      = hist.mean(axis=1, skipna=True)
    result.name = "roe_smoothed_4q"
    return result


def factor_roe_delta_3q(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    ROE 3 季度改善速度因子：最新单季 ROE − 3 个季报期前的单季 ROE。

    与 roe_delta（同比）的区别：
      - roe_delta：当期 vs 上年同期，反映跨年趋势（已在因子库中）
      - roe_delta_3q：当期 vs 3 季前，反映近期加速/减速，时间窗口更短

    需要至少 4 期有效 q_roe 数据（q0 和 q3 均非 NaN）。

    预期方向：+（ROE 近期加速改善预期超额收益）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → roe_delta_3q（百分比差值）；不足 4 期时为 NaN
    """
    ip_raw = get_indicator_pit_raw()
    hist   = get_quarterly_history(ip_raw, "q_roe", rebalance_date, codes, n_quarters=4)

    result      = hist["q0"] - hist["q3"]   # q0 = 最新, q3 = 3 期前
    result.name = "roe_delta_3q"
    return result


def factor_roe_stability(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    ROE 稳定性因子：-std(最近 MIN_QUARTERS_STABILITY 季单季扣非 ROE)。

    经济逻辑：稳定盈利能力（低方差 ROE）通常来自竞争护城河，代表高质量企业。
    标准差取负号，使"稳定 → 高分"，预期与正向超额收益挂钩。

    使用 q_dt_roe（单季扣非 ROE）而非 roe（累计 ROE）：
      - 单季值对当期盈利能力变化更敏感
      - 扣非避免非经常性损益干扰（如大额资产出售）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → -std(q_dt_roe)；有效季度数 < MIN_QUARTERS_STABILITY 时为 NaN
    """
    ip_raw = get_indicator_pit_raw()
    hist = get_quarterly_history(
        ip_raw, "q_dt_roe", rebalance_date, codes, n_quarters=MIN_QUARTERS_STABILITY
    )

    valid_mask = hist.notna().sum(axis=1) >= MIN_QUARTERS_STABILITY
    std_vals   = hist.std(axis=1, ddof=1)
    std_vals[~valid_mask] = np.nan

    result      = -std_vals
    result.name = "roe_stability"
    return result.reindex(codes)


def factor_rev_acceleration(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    营收增速加速度：本期同比增速 − 上期同比增速。

    经济逻辑：营收增速的变化（二阶导数）比增速本身更能反映企业动能转折：
      - 增速从低谷回升：企业可能重回增长轨道（看多信号）
      - 增速从高点下滑：企业可能进入成熟/衰退期（看空信号）
    这是成长型投资者关注的"加速增长"信号，与 rev_yoy（水平）互补。

    计算方式（需 6 季度数据：q0, q1, q4, q5）：
      - 本期同比增速 = (q0 - q4) / |q4|
      - 上期同比增速 = (q1 - q5) / |q5|
      - 加速度 = 本期增速 - 上期增速
    分母取绝对值，避免去年亏损时方向颠倒；分母 ≈ 0 时置 NaN。

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → 营收增速加速度（小数）；数据不足时为 NaN
    """
    fp_raw = get_financial_pit_raw()
    hist = get_quarterly_history(fp_raw, "revenue", rebalance_date, codes, n_quarters=6)

    denom_curr = hist["q4"].abs().replace(0, np.nan)
    denom_prev = hist["q5"].abs().replace(0, np.nan)

    yoy_curr = (hist["q0"] - hist["q4"]) / denom_curr
    yoy_prev = (hist["q1"] - hist["q5"]) / denom_prev

    result      = yoy_curr - yoy_prev
    result.name = "rev_acceleration"
    return result.reindex(codes)


def factor_gross_margin_trend(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    毛利率同比趋势因子：当期累计毛利率 − 上年同期累计毛利率。

    使用同比（year-over-year）对比而非环比，原因：
      - 毛利率具有季节性（Q1 通常低于 Q3/Q4），环比对比噪音大
      - 同期对比（Q1 vs Q1、Annual vs Annual）天然消除季节性

    与 gross_margin（绝对水平）互补：gross_margin 衡量护城河宽度，
    gross_margin_trend 衡量护城河是在变宽还是变窄。

    预期方向：+（毛利率持续改善预期超额收益）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → gross_margin_trend（百分比差值）；缺失时为 NaN
    """
    ip_raw = get_indicator_pit_raw()
    delta  = get_field_yoy_delta(ip_raw, "grossprofit_margin", rebalance_date, codes)

    result      = delta.reindex(codes)
    result.name = "gross_margin_trend"
    return result


def factor_ep_vs_history(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    相对历史估值因子：当前 EP_TTM / 过去 3 年 EP_TTM 均值。

    经济含义：
      > 1.0：当前盈利收益率高于历史水平 → 相对自身历史偏便宜
      < 1.0：当前盈利收益率低于历史水平 → 相对自身历史偏贵

    与横截面 ep_ttm 互补：ep_ttm 衡量"当期横截面中哪些股票便宜"，
    ep_vs_history 衡量"某只股票相对其自身历史是否便宜"，两者信息来源不同。

    历史参考点：T-12 月、T-24 月、T-36 月（精确到最近交易日）。
    PIT 保证：历史参考点的 TTM 净利润和市值均使用该历史时点可见数据。

    注意：
      - 历史均值 <= 0（历史平均亏损）时置 NaN（比率无经济含义）
      - T 前历史少于 12 个月时（训练集早期）覆盖率偏低，属正常情况

    预期方向：+（高于历史盈利收益率预期超额收益）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → ep_vs_history（无量纲比率）；缺失时为 NaN
    """
    fp_raw = get_financial_pit_raw()

    # 当前 EP_TTM
    current_ni = make_ttm(fp_raw, "n_income", rebalance_date, codes)
    current_mv = _get_total_mv_yuan(rebalance_date, codes)
    current_ep = current_ni / current_mv.reindex(current_ni.index)

    # 交易日历（用于定位历史参考日期）
    iq        = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    all_dates = iq.index.sort_values()

    # 计算 T-12、T-24、T-36 月的历史 EP_TTM
    hist_eps: list[pd.Series] = []
    for n_months in [12, 24, 36]:
        target      = rebalance_date - pd.DateOffset(months=n_months)
        valid_before = all_dates[all_dates <= target]
        if valid_before.empty:
            continue
        hist_date = valid_before[-1]

        hist_ni = make_ttm(fp_raw, "n_income", hist_date, codes)
        hist_mv = _get_total_mv_yuan(hist_date, codes)
        h_ep    = hist_ni / hist_mv.reindex(hist_ni.index)
        h_ep.name = n_months
        hist_eps.append(h_ep)

    if not hist_eps:
        return pd.Series(np.nan, index=pd.Index(codes, name="ts_code"),
                         name="ep_vs_history")

    hist_mean = pd.concat(hist_eps, axis=1).mean(axis=1, skipna=True)

    # 历史均值 <= 0 时比率无经济含义，置 NaN
    hist_mean[hist_mean <= 0] = np.nan

    result      = current_ep.reindex(hist_mean.index) / hist_mean
    result.name = "ep_vs_history"
    return result.reindex(codes)


# ---------------------------------------------------------------------------
# 阶段 5：质量综合因子（piotroski_f / garp）
# ---------------------------------------------------------------------------

def _binary_flag(condition: pd.Series, mask: pd.Series) -> pd.Series:
    """将布尔条件转为 0/1/NaN，其中 mask=False 的位置（数据缺失）置 NaN。"""
    return condition.astype(float).where(mask)


def factor_piotroski_f(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    Piotroski F-Score（简化版，7 个财务健康指标综合评分）。

    原始论文：Piotroski (2000)，针对 A 股数据可用性简化：
    实现 7 个分项（F1-F5, F8, F9），跳过 F6（流动比率，字段缺失）和
    F7（未增发新股，需股本历史数据）。

    盈利能力（3 项）：
      F1 = ROA > 0（当期盈利）
      F2 = TTM 经营现金流 > 0（现金真实流入）
      F3 = ΔROA > 0（同比改善，Year-on-Year）
    盈利质量（1 项）：
      F4 = TTM 经营现金流 > TTM 净利润（应计项目低，利润由现金支撑）
    杠杆稳健（1 项）：
      F5 = 资产负债率同比下降（去杠杆）
    经营效率（2 项）：
      F8 = 毛利率同比上升（护城河变宽）
      F9 = 资产周转率同比上升（运营效率提升）

    计分规则：总分 0-7；有效分项 ≥ 5 时才输出分数，否则置 NaN（防数据稀疏导致噪音）。
    严格 PIT：所有数据仅使用 pit_date ≤ T 的可见记录。

    预期方向：+（高 F-Score 代表财务更健康，预期正超额收益）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → piotroski_f（0-7 整数评分）；有效分项 < 5 时为 NaN
    """
    ip_raw = get_indicator_pit_raw()
    fp_raw = get_financial_pit_raw()

    latest = get_pit_latest(ip_raw, rebalance_date, codes)
    if latest.empty:
        return pd.Series(dtype=float, index=pd.Index(codes, name="ts_code"),
                         name="piotroski_f")

    all_idx = pd.Index(codes, name="ts_code") if codes else latest.index

    # ── YoY 同比变化 ─────────────────────────────────────────────────────────
    delta_roa = get_field_yoy_delta(ip_raw, "roa",                rebalance_date, codes)
    delta_da  = get_field_yoy_delta(ip_raw, "debt_to_assets",     rebalance_date, codes)
    delta_gm  = get_field_yoy_delta(ip_raw, "grossprofit_margin", rebalance_date, codes)
    delta_at  = get_field_yoy_delta(ip_raw, "assets_turn",        rebalance_date, codes)

    # ── TTM 现金流与净利润 ────────────────────────────────────────────────────
    ttm_cfo = make_ttm(fp_raw, "n_cashflow_act", rebalance_date, codes)
    ttm_ni  = make_ttm(fp_raw, "n_income",       rebalance_date, codes)

    roa = latest["roa"].reindex(all_idx)
    cfo = ttm_cfo.reindex(all_idx)
    ni  = ttm_ni.reindex(all_idx)
    d_roa = delta_roa.reindex(all_idx)
    d_da  = delta_da.reindex(all_idx)
    d_gm  = delta_gm.reindex(all_idx)
    d_at  = delta_at.reindex(all_idx)

    # ── 7 个分项 ──────────────────────────────────────────────────────────────
    scores = pd.DataFrame(index=all_idx)
    scores["f1"] = _binary_flag(roa > 0,   roa.notna())
    scores["f2"] = _binary_flag(cfo > 0,   cfo.notna())
    scores["f3"] = _binary_flag(d_roa > 0, d_roa.notna())
    scores["f4"] = _binary_flag(cfo > ni,  cfo.notna() & ni.notna())
    scores["f5"] = _binary_flag(d_da < 0,  d_da.notna())
    scores["f8"] = _binary_flag(d_gm > 0,  d_gm.notna())
    scores["f9"] = _binary_flag(d_at > 0,  d_at.notna())

    valid_count = scores.notna().sum(axis=1)
    f_score     = scores.sum(axis=1, skipna=True)
    f_score[valid_count < 5] = np.nan   # 数据不足时结果不可靠

    f_score.name = "piotroski_f"
    return f_score


def factor_garp(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    GARP 交叉因子（Growth at a Reasonable Price）：rank 均值法。

    公式：(ep_ttm_percentile_rank + roe_percentile_rank) / 2

    设计要点：
    ① rank 均值法规避乘积法的双负数问题——
       EP_zscore × ROE_zscore 在两者均为负时得正值（"既贵又差"被误判为好信号）；
       rank 均值中，负 EP 对应低 EP_rank，负 ROE 对应低 ROE_rank，两差均差。
    ② 同时选择"便宜且盈利能力强"的股票，过滤"便宜但盈利差"和"盈利好但太贵"。
    ③ 与横截面 ep_ttm、roe 互补：后两者各自单维度看，GARP 从双维度联合筛选。

    ep_ttm 和 roe 任一为 NaN 时，GARP 结果也为 NaN。

    数据依赖：daily_basic（市值）+ financial_pit（净利润）+ indicator_pit（ROE）
    预期方向：+（高 GARP 分数预期正超额收益）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → garp（[0, 1] 分位排名均值）；子因子任一为 NaN 时结果为 NaN
    """
    ep  = factor_ep_ttm(rebalance_date, codes)
    roe = factor_roe(rebalance_date, codes)

    # pct=True 返回 [0, 1] 分位排名；na_option="keep" 保留 NaN 输入对应 NaN 输出
    ep_rank  = ep.rank(pct=True, na_option="keep")
    roe_rank = roe.rank(pct=True, na_option="keep")

    # 两者均非 NaN 才计算均值；任一为 NaN 则结果为 NaN（pandas 默认 NaN + x = NaN）
    result      = (ep_rank + roe_rank) / 2
    result.name = "garp"
    return result.reindex(codes)


# ---------------------------------------------------------------------------
# 批量构建入口
# ---------------------------------------------------------------------------

_FACTOR_BUILDERS = {
    # 价值（严格 PIT）
    "ep_ttm":      factor_ep_ttm,
    "bp":          factor_bp,
    "sp_ttm":      factor_sp_ttm,
    "cfp":         factor_cfp,
    "fcfp":        factor_fcfp,
    # 质量
    "roe":         factor_roe,
    "roa":         factor_roa,
    "gross_margin": factor_gross_margin,
    "asset_turn":  factor_asset_turn,
    "leverage":    factor_leverage,
    "accrual":     factor_accrual,
    # 成长
    "np_yoy":      factor_np_yoy,
    "rev_yoy":     factor_rev_yoy,
    "roe_delta":   factor_roe_delta,
    "q_roe":       factor_q_roe,
    # 阶段 4：低风险因子扩展
    "roe_smoothed_4q":    factor_roe_smoothed_4q,
    "roe_delta_3q":       factor_roe_delta_3q,
    "roe_stability":      factor_roe_stability,
    "rev_acceleration":   factor_rev_acceleration,
    "gross_margin_trend": factor_gross_margin_trend,
    "ep_vs_history":      factor_ep_vs_history,
    # 阶段 5：质量综合因子
    "piotroski_f":        factor_piotroski_f,
    "garp":               factor_garp,
}

# dy_ttm 来自 daily_basic.dv_ttm，有 0-60 天前视偏差，不纳入默认集合。
# 如需使用，显式传入 factor_names=["dy_ttm", ...]，并在评估时标注其非严格 PIT。
_FACTOR_BUILDERS_NONSTRICT_PIT = {
    "dy_ttm": factor_dy_ttm,
}


def build_all_financial_factors(
    rebalance_date: pd.Timestamp,
    codes: list[str],
    factor_names: Optional[list[str]] = None,
) -> dict[str, pd.Series]:
    """
    构建指定调仓日的所有财务因子原始值。

    返回的因子值是原始值（未去极值/标准化/中性化），需经过
    preprocess_factor 处理后才能用于 IC 检验或组合优化。

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表（通常来自 get_investable_universe）
        factor_names:   指定因子子集；None 则构建全部 15 个严格 PIT 因子。
                        如需 dy_ttm（非严格 PIT），须显式传入并自行标注偏差风险。

    Returns:
        dict，key = 因子名（如 "roe"），value = ts_code → 原始因子值 Series
    """
    _all_builders = {**_FACTOR_BUILDERS, **_FACTOR_BUILDERS_NONSTRICT_PIT}
    names = factor_names if factor_names is not None else list(_FACTOR_BUILDERS)
    unknown = set(names) - set(_all_builders)
    if unknown:
        raise ValueError(f"未知因子名：{sorted(unknown)}")

    result: dict[str, pd.Series] = {}
    for name in names:
        try:
            result[name] = _all_builders[name](rebalance_date, codes)
        except Exception as exc:
            log.error("因子 %s 在 %s 构建失败：%s", name, rebalance_date.date(), exc)
            result[name] = pd.Series(np.nan, index=pd.Index(codes, name="ts_code"),
                                     name=name)

    log.info(
        "build_all_financial_factors(%s): 构建 %d 个因子，代码 %d 只",
        rebalance_date.date(), len(result), len(codes),
    )
    return result

