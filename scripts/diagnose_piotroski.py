"""
scripts/diagnose_piotroski.py — Piotroski F-Score 专项诊断
=============================================================

对 piotroski_f 因子的验证期 IC 反转做根因诊断，输出三类分析：
  1. 分期 IC_IR：2012-2015 / 2016-2018 / 2019-2020 / 2021-2022
  2. 分项 IC：F1/F2/F3/F4/F5/F8/F9 各分项对 forward return 的 Rank IC
  3. 行业分层 IC：各 SW2021 一级行业的 IC 表现（重点 2021-2022）

数据依赖（只读，不修改）：
  reports/factor_evaluation/ic_history_research_all.parquet   阶段一产出
  data/processed/fwd_ret_panel.parquet
  data/processed/factor_panels/piotroski_f.parquet
  data/processed/indicator_pit.parquet
  data/processed/financial_pit.parquet
  data/processed/industry.parquet

输出（全部写入 check/0529/，不影响主线报告）：
  piotroski_diagnosis.md        根因诊断报告（含结论代码）
  piotroski_component_ic.csv    分项 IC 统计（period × component）
  piotroski_industry_ic.csv     行业分层 IC 统计（period × industry）

使用方法：
  python -m scripts.diagnose_piotroski [--ic-history PATH] [--output-dir PATH]
        [--no-component] [--periods period1,period2]

分析纪律：
  - 严格限于训练+验证期（2012-2022），不读取测试集数据
  - 分析结论不能直接修改 final_factors.json；若建议调池，必须另起实验验证
  - 分项计算口径与 factor_piotroski_f() 保持一致
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.loader import get_financial_pit_raw, get_indicator_pit_raw
from src.data.pit_loader import get_field_yoy_delta, get_pit_latest, make_ttm
from src.evaluation.ic_analysis import compute_rank_ic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────────────────

# 严禁越过验证期末（测试集保护）
VALID_END = pd.Timestamp("2022-12-31")

SUB_PERIODS: OrderedDict[str, tuple[pd.Timestamp, pd.Timestamp]] = OrderedDict([
    ("2012-2015", (pd.Timestamp("2012-01-01"), pd.Timestamp("2015-12-31"))),
    ("2016-2018", (pd.Timestamp("2016-01-01"), pd.Timestamp("2018-12-31"))),
    ("2019-2020", (pd.Timestamp("2019-01-01"), pd.Timestamp("2020-12-31"))),
    ("2021-2022", (pd.Timestamp("2021-01-01"), pd.Timestamp("2022-12-31"))),
])

# 分项名称 → 可读标签（与 factor_piotroski_f() 保持一致）
COMPONENT_LABELS: dict[str, str] = OrderedDict([
    ("f1_roa_pos",          "F1: ROA>0（盈利能力）"),
    ("f2_cfo_pos",          "F2: TTM CFO>0（现金流入）"),
    ("f3_delta_roa_pos",    "F3: ΔROA>0（盈利同比改善）"),
    ("f4_cfo_gt_ni",        "F4: CFO>NI（利润含金量）"),
    ("f5_deleverage",       "F5: Δ负债率<0（去杠杆）"),
    ("f8_margin_improve",   "F8: Δ毛利率>0（护城河）"),
    ("f9_turnover_improve", "F9: Δ周转率>0（运营效率）"),
])

MIN_IC_OBS = 6          # 最少观测数才计算 IC_IR
MIN_INDUSTRY_STOCKS = 10  # 每期每行业最少股票数才计算 IC

_DEFAULT_IC_HISTORY     = "reports/factor_evaluation/ic_history_research_all.parquet"
_DEFAULT_FACTOR_PANEL   = "data/processed/factor_panels/piotroski_f.parquet"
_DEFAULT_FWD_RET        = "data/processed/fwd_ret_panel.parquet"
_DEFAULT_INDUSTRY       = "data/processed/industry.parquet"
_DEFAULT_OUTPUT_DIR     = "check/0529"


# ── IC 统计量 ──────────────────────────────────────────────────────────────────

def _ic_stats(ic_vals: np.ndarray | list) -> dict:
    """
    计算 IC 序列的统计量。

    Args:
        ic_vals: IC 值列表或数组（允许含 NaN）
    Returns:
        含 n, ic_mean, ic_std, ic_ir, t_stat, p_value, pct_positive 的字典
    """
    arr = np.array(ic_vals, dtype=float)
    arr = arr[~np.isnan(arr)]
    n = len(arr)
    if n < MIN_IC_OBS:
        return {"n": n, "ic_mean": np.nan, "ic_std": np.nan,
                "ic_ir": np.nan, "t_stat": np.nan, "p_value": np.nan,
                "pct_positive": np.nan}
    ic_mean = arr.mean()
    ic_std  = arr.std(ddof=1)
    ic_ir   = ic_mean / ic_std if ic_std > 0 else np.nan
    t_stat  = ic_ir * np.sqrt(n) if not np.isnan(ic_ir) else np.nan
    p_value = 2 * stats.t.sf(abs(t_stat), df=n - 1) if not np.isnan(t_stat) else np.nan
    return {
        "n": n,
        "ic_mean":     float(ic_mean),
        "ic_std":      float(ic_std),
        "ic_ir":       float(ic_ir) if not np.isnan(ic_ir) else np.nan,
        "t_stat":      float(t_stat) if not np.isnan(t_stat) else np.nan,
        "p_value":     float(p_value) if not np.isnan(p_value) else np.nan,
        "pct_positive": float((arr > 0).mean()),
    }


# ── 分析一：分期 IC_IR ─────────────────────────────────────────────────────────

def analyze_sub_periods(ic_series: pd.Series) -> pd.DataFrame:
    """
    对 piotroski_f 的月度 IC 序列做分期统计。

    Args:
        ic_series: 月度 Rank IC 序列（index=rebalance_date）
    Returns:
        DataFrame，index=period_name，columns=统计量
    """
    rows = []
    for period_name, (start, end) in SUB_PERIODS.items():
        sub = ic_series[(ic_series.index >= start) & (ic_series.index <= end)].dropna()
        row = _ic_stats(sub.values)
        row["period"] = period_name
        rows.append(row)
    df = pd.DataFrame(rows).set_index("period")
    return df[["n", "ic_mean", "ic_std", "ic_ir", "t_stat", "p_value", "pct_positive"]]


# ── 分析二：分项 IC ─────────────────────────────────────────────────────────────

def _build_components_at_date(
    ip_raw: pd.DataFrame,
    fp_raw: pd.DataFrame,
    date: pd.Timestamp,
) -> dict[str, pd.Series]:
    """
    计算单个调仓日的所有 F 分项值（严格复用 factor_piotroski_f 口径）。

    Args:
        ip_raw: reset_index 后的完整 indicator_pit DataFrame
        fp_raw: reset_index 后的完整 financial_pit DataFrame
        date:   调仓日
    Returns:
        dict: component_name → binary 0/1/NaN Series（index=ts_code）
              若该日无 PIT 数据则返回空字典
    """
    latest = get_pit_latest(ip_raw, date)
    if latest.empty:
        return {}

    roa       = latest["roa"]
    delta_roa = get_field_yoy_delta(ip_raw, "roa",                date)
    delta_da  = get_field_yoy_delta(ip_raw, "debt_to_assets",     date)
    delta_gm  = get_field_yoy_delta(ip_raw, "grossprofit_margin", date)
    delta_at  = get_field_yoy_delta(ip_raw, "assets_turn",        date)
    ttm_cfo   = make_ttm(fp_raw, "n_cashflow_act", date)
    ttm_ni    = make_ttm(fp_raw, "n_income",       date)

    # 统一对齐到各 Series 索引的并集，避免异标签 Series 直接比较报错
    all_idx = (
        roa.index
        .union(ttm_cfo.index)
        .union(ttm_ni.index)
        .union(delta_roa.index)
        .union(delta_da.index)
        .union(delta_gm.index)
        .union(delta_at.index)
    )
    roa_a       = roa.reindex(all_idx)
    delta_roa_a = delta_roa.reindex(all_idx)
    delta_da_a  = delta_da.reindex(all_idx)
    delta_gm_a  = delta_gm.reindex(all_idx)
    delta_at_a  = delta_at.reindex(all_idx)
    ttm_cfo_a   = ttm_cfo.reindex(all_idx)
    ttm_ni_a    = ttm_ni.reindex(all_idx)

    return {
        "f1_roa_pos":          (roa_a > 0).astype(float).where(roa_a.notna()),
        "f2_cfo_pos":          (ttm_cfo_a > 0).astype(float).where(ttm_cfo_a.notna()),
        "f3_delta_roa_pos":    (delta_roa_a > 0).astype(float).where(delta_roa_a.notna()),
        "f4_cfo_gt_ni":        (ttm_cfo_a > ttm_ni_a).astype(float).where(
                                   ttm_cfo_a.notna() & ttm_ni_a.notna()),
        "f5_deleverage":       (delta_da_a < 0).astype(float).where(delta_da_a.notna()),
        "f8_margin_improve":   (delta_gm_a > 0).astype(float).where(delta_gm_a.notna()),
        "f9_turnover_improve": (delta_at_a > 0).astype(float).where(delta_at_a.notna()),
    }


def compute_component_ic_df(
    ip_raw: pd.DataFrame,
    fp_raw: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    rebalance_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """
    逐期计算各 F 分项与 forward return 的 Rank IC，按分期汇总。

    注意：F 分项为二值（0/1/NaN），Spearman IC 即点双列相关系数，数值范围受限，
    与连续因子的 IC 不可直接类比。

    Args:
        ip_raw:           完整 indicator_pit（reset_index）
        fp_raw:           完整 financial_pit（reset_index）
        fwd_ret_panel:    forward return 宽表（date × ts_code）
        rebalance_dates:  需要计算的调仓日列表
    Returns:
        长格式 DataFrame：period | component | label | n | ic_mean | ic_ir | t_stat | p_value | pct_positive
    """
    # 收集每期每分项 IC 值
    ic_accumulator: dict[str, dict[pd.Timestamp, float]] = {
        name: {} for name in COMPONENT_LABELS
    }

    n_dates = len(rebalance_dates)
    for i, date in enumerate(rebalance_dates):
        if i % 24 == 0:
            log.info("分项 IC 计算 %d/%d (%s) …", i + 1, n_dates, date.date())

        if date not in fwd_ret_panel.index:
            continue
        fwd = fwd_ret_panel.loc[date].dropna()
        if fwd.empty:
            continue

        components = _build_components_at_date(ip_raw, fp_raw, date)
        if not components:
            continue

        for comp_name, comp_vals in components.items():
            ic = compute_rank_ic(comp_vals, fwd)
            ic_accumulator[comp_name][date] = ic

    # 按分期汇总
    rows = []
    for comp_name, label in COMPONENT_LABELS.items():
        date_ic = pd.Series(ic_accumulator[comp_name]).dropna()
        for period_name, (start, end) in SUB_PERIODS.items():
            sub = date_ic[(date_ic.index >= start) & (date_ic.index <= end)]
            stats_row = _ic_stats(sub.values)
            rows.append({
                "component": comp_name,
                "label": label,
                "period": period_name,
                **stats_row,
            })

    cols = ["component", "label", "period", "n", "ic_mean", "ic_std",
            "ic_ir", "t_stat", "p_value", "pct_positive"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)[cols]


# ── 分析三：行业分层 IC ────────────────────────────────────────────────────────

def _get_industry_snapshot(
    industry_df: pd.DataFrame,
    date: pd.Timestamp,
) -> pd.DataFrame:
    """
    获取给定日期（取 <= date 的最新 trade_date）的行业快照。

    Args:
        industry_df: (trade_date, ts_code) MultiIndex DataFrame，含 industry_code / industry_name
        date:        目标日期
    Returns:
        以 ts_code 为 index 的 DataFrame（含 industry_code / industry_name）；无数据时返回空 DataFrame
    """
    trade_dates = industry_df.index.get_level_values("trade_date").unique()
    valid = trade_dates[trade_dates <= date]
    if len(valid) == 0:
        return pd.DataFrame()
    latest = valid.max()
    return industry_df.xs(latest, level="trade_date")


def compute_industry_ic_df(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    industry_df: pd.DataFrame,
    rebalance_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """
    对每个 SW2021 一级行业，计算 piotroski_f 的 IC，按分期汇总。

    行业划分：使用每期最新可用行业快照；每期每行业有效股票数 < MIN_INDUSTRY_STOCKS 时跳过。

    Args:
        factor_panel:    piotroski_f 因子面板（date × ts_code）
        fwd_ret_panel:   forward return 面板（date × ts_code）
        industry_df:     行业快照 DataFrame（MultiIndex: trade_date, ts_code）
        rebalance_dates: 需要计算的调仓日
    Returns:
        长格式 DataFrame：period | industry_code | industry_name | n | ic_mean | ic_ir | t_stat | p_value
    """
    # ic_store[period_name][industry_code] = [ic_val1, ic_val2, ...]
    ic_store: dict[str, dict[str, list]] = {p: {} for p in SUB_PERIODS}
    ind_name_map: dict[str, str] = {}  # industry_code → industry_name

    n_dates = len(rebalance_dates)
    for i, date in enumerate(rebalance_dates):
        if i % 24 == 0:
            log.info("行业 IC 计算 %d/%d (%s) …", i + 1, n_dates, date.date())

        if date not in factor_panel.index or date not in fwd_ret_panel.index:
            continue

        factor_t = factor_panel.loc[date].dropna()
        fwd_t    = fwd_ret_panel.loc[date].dropna()
        ind_snap = _get_industry_snapshot(industry_df, date)

        if ind_snap.empty or "industry_code" not in ind_snap.columns:
            continue

        # 只计算该日期属于哪个分期
        period_name = None
        for pname, (start, end) in SUB_PERIODS.items():
            if start <= date <= end:
                period_name = pname
                break
        if period_name is None:
            continue

        # 逐行业计算 IC
        ind_series = ind_snap["industry_code"]
        for ind_code in ind_series.unique():
            if not isinstance(ind_code, str):
                continue
            ind_stocks = ind_series[ind_series == ind_code].index
            common = ind_stocks.intersection(factor_t.index).intersection(fwd_t.index)
            if len(common) < MIN_INDUSTRY_STOCKS:
                continue

            ic = compute_rank_ic(factor_t.reindex(common), fwd_t.reindex(common))
            if np.isnan(ic):
                continue

            if ind_code not in ic_store[period_name]:
                ic_store[period_name][ind_code] = []
            ic_store[period_name][ind_code].append(ic)

            # 缓存行业名称
            if ind_code not in ind_name_map and "industry_name" in ind_snap.columns:
                names = ind_snap.loc[ind_snap["industry_code"] == ind_code, "industry_name"]
                if not names.empty:
                    ind_name_map[ind_code] = str(names.iloc[0])

    rows = []
    for period_name, ind_dict in ic_store.items():
        for ind_code, ic_vals in sorted(ind_dict.items()):
            stats_row = _ic_stats(ic_vals)
            if stats_row["n"] < MIN_IC_OBS:
                continue
            rows.append({
                "period": period_name,
                "industry_code": ind_code,
                "industry_name": ind_name_map.get(ind_code, ""),
                **{k: v for k, v in stats_row.items()
                   if k in ("n", "ic_mean", "ic_ir", "t_stat", "p_value", "pct_positive")},
            })

    cols = ["period", "industry_code", "industry_name",
            "n", "ic_mean", "ic_ir", "t_stat", "p_value", "pct_positive"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)[cols]


# ── 诊断结论 ───────────────────────────────────────────────────────────────────

def _determine_conclusion(
    sub_period_df: pd.DataFrame,
    comp_df: pd.DataFrame,
) -> tuple[str, str]:
    """
    依据分期 IC_IR 和分项 IC_IR（验证期）自动给出诊断结论。

    可能结论（来自计划规范）：
      KEEP_MONITOR         → 整体方向未明确反转，继续观察
      REMOVE_CANDIDATE     → 整体失效且分项也大部分无效，建议移除候选
      REBUILD_COMPONENTS   → 整体失效但部分分项仍有效，建议用有效分项重建
      REVERSE_USE_CANDIDATE → 持续负向，可考虑反向使用（需实验验证）

    Args:
        sub_period_df: 分期统计 DataFrame（index=period_name）
        comp_df:       分项 IC 统计 DataFrame（长格式）
    Returns:
        (conclusion_code, reasoning) 二元组
    """
    all_ir = sub_period_df["ic_ir"].dropna()
    if all_ir.empty:
        return "KEEP_MONITOR", "IC 数据不足，无法判断"

    n_positive = int((all_ir > 0).sum())
    n_negative = int((all_ir < 0).sum())
    recent_ir = sub_period_df.loc["2021-2022", "ic_ir"] if "2021-2022" in sub_period_df.index else np.nan
    early_ir  = sub_period_df.loc["2012-2015", "ic_ir"] if "2012-2015" in sub_period_df.index else np.nan

    # 统计验证期（2021-2022）分项有效情况；若分项数据未计算，标记为 unknown
    comp_available = not comp_df.empty and "period" in comp_df.columns
    comp_valid = comp_df[comp_df["period"] == "2021-2022"] if comp_available else pd.DataFrame()
    n_comp_pos = int((comp_valid["ic_ir"] > 0).sum()) if not comp_valid.empty else None

    suffix = "需要另起训练/验证实验验证，不能直接改主线因子池。"

    # 决策树
    if n_negative == len(all_ir) and len(all_ir) >= 3:
        return (
            "REVERSE_USE_CANDIDATE",
            f"所有分期 IC_IR 均为负（{n_negative}/{len(all_ir)} 期），"
            "因子方向持续反向，可考虑以 factor_direction=-1 重新测试。" + suffix,
        )

    if n_negative > n_positive:
        if n_comp_pos is None:
            return (
                "REBUILD_COMPONENTS",
                f"整体 IC 偏负（{n_negative}/{len(all_ir)} 期为负），"
                "分项分析未运行（--no-component），无法进一步区分；"
                "建议补充分项诊断后再决定是重建还是移除。" + suffix,
            )
        if n_comp_pos >= 3:
            return (
                "REBUILD_COMPONENTS",
                f"整体 IC 偏负（{n_negative}/{len(all_ir)} 期为负），"
                f"但验证期有 {n_comp_pos}/7 个分项 IC_IR>0，"
                "建议以有效分项重新构造质量因子。" + suffix,
            )
        return (
            "REMOVE_CANDIDATE",
            f"整体 IC 偏负（{n_negative}/{len(all_ir)} 期为负），"
            f"验证期分项有效数 {n_comp_pos}/7，不足以支撑重建。"
            "建议移除因子候选，或等待市场风格切换后重评。" + suffix,
        )

    if not np.isnan(recent_ir) and not np.isnan(early_ir) and recent_ir < 0 < early_ir:
        if n_comp_pos is None:
            # 早期有效 + 近期反转，但无分项数据 → 保守结论：等待分项分析
            return (
                "REBUILD_COMPONENTS",
                f"早期（2012-2015）IC_IR={early_ir:.3f} 有效，"
                f"验证期（2021-2022）IC_IR={recent_ir:.3f} 反转，"
                "分项分析未运行（--no-component），建议补充后再判断是否重建。" + suffix,
            )
        if n_comp_pos >= 3:
            return (
                "REBUILD_COMPONENTS",
                f"早期（2012-2015）IC_IR={early_ir:.3f} 有效，"
                f"验证期（2021-2022）IC_IR={recent_ir:.3f} 反转，"
                f"但仍有 {n_comp_pos}/7 个分项在验证期有正 IC_IR，"
                "建议以有效分项重建。" + suffix,
            )
        return (
            "REMOVE_CANDIDATE",
            f"早期有效（IC_IR={early_ir:.3f}），验证期反转（IC_IR={recent_ir:.3f}），"
            f"分项有效数 {n_comp_pos}/7 不足，建议移除候选。" + suffix,
        )

    return (
        "KEEP_MONITOR",
        f"IC_IR 整体偏弱但方向未明确反转（正向分期 {n_positive}/{len(all_ir)}），建议继续监控。",
    )


# ── 报告生成 ───────────────────────────────────────────────────────────────────

def _fmt(val, fmt=".4f") -> str:
    """格式化数值，NaN 显示为 '—'。"""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "—"
    return format(val, fmt)


def _sub_period_table(sub_df: pd.DataFrame) -> str:
    lines = ["| 分期 | n | IC均值 | IC_IR | t统计量 | p值 | IC>0占比 |",
             "|------|---|--------|-------|---------|-----|----------|"]
    for period, row in sub_df.iterrows():
        lines.append(
            f"| {period} | {int(row['n']) if not np.isnan(row['n']) else '—'} "
            f"| {_fmt(row['ic_mean'])} | {_fmt(row['ic_ir'], '.3f')} "
            f"| {_fmt(row['t_stat'], '.2f')} | {_fmt(row['p_value'], '.4f')} "
            f"| {_fmt(row['pct_positive'], '.1%')} |"
        )
    return "\n".join(lines)


def _component_table_for_period(comp_df: pd.DataFrame, period: str) -> str:
    sub = comp_df[comp_df["period"] == period].copy()
    if sub.empty:
        return "_该期无有效分项数据。_"
    lines = ["| 分项 | 标签 | n | IC均值 | IC_IR | t统计量 | IC>0占比 |",
             "|------|------|---|--------|-------|---------|----------|"]
    for _, row in sub.iterrows():
        lines.append(
            f"| {row['component']} | {row['label']} "
            f"| {int(row['n']) if not np.isnan(row['n']) else '—'} "
            f"| {_fmt(row['ic_mean'])} | {_fmt(row['ic_ir'], '.3f')} "
            f"| {_fmt(row['t_stat'], '.2f')} | {_fmt(row['pct_positive'], '.1%')} |"
        )
    return "\n".join(lines)


def _industry_table_for_period(ind_df: pd.DataFrame, period: str, top_n: int = 15) -> str:
    sub = ind_df[ind_df["period"] == period].copy()
    if sub.empty:
        return "_该期无足够行业 IC 数据（每行业需 ≥ 10 只股票且 ≥ 6 个观测期）。_"
    sub = sub.sort_values("ic_ir", ascending=False)
    lines = [f"（显示 IC_IR 最高/最低各 {top_n//2} 行）",
             "| 行业代码 | 行业名称 | n | IC均值 | IC_IR | t统计量 |",
             "|----------|----------|---|--------|-------|---------|"]
    display = pd.concat([sub.head(top_n // 2), sub.tail(top_n // 2)]).drop_duplicates()
    for _, row in display.iterrows():
        lines.append(
            f"| {row['industry_code']} | {row['industry_name']} "
            f"| {int(row['n']) if not np.isnan(row['n']) else '—'} "
            f"| {_fmt(row['ic_mean'])} | {_fmt(row['ic_ir'], '.3f')} "
            f"| {_fmt(row['t_stat'], '.2f')} |"
        )
    return "\n".join(lines)


def generate_report(
    sub_df: pd.DataFrame,
    comp_df: pd.DataFrame,
    ind_df: pd.DataFrame,
    conclusion_code: str,
    conclusion_reason: str,
    as_of: str,
) -> str:
    """
    生成 Markdown 格式的诊断报告。

    Args:
        sub_df:            分期 IC_IR 统计
        comp_df:           分项 IC 统计（长格式）
        ind_df:            行业分层 IC 统计（长格式）
        conclusion_code:   诊断结论代码
        conclusion_reason: 结论理由
        as_of:             报告生成日期字符串
    Returns:
        Markdown 字符串
    """
    lines = [
        f"# Piotroski F-Score 专项诊断报告",
        f"",
        f"> 生成日期：{as_of}",
        f"> 数据范围：2012-2022（训练期 + 验证期），**不含测试集**",
        f"> 本报告仅供诊断参考，结论不能直接修改 `final_factors.json`，若建议调池需另起实验。",
        f"",
        f"---",
        f"",
        f"## 一、诊断结论",
        f"",
        f"**结论代码**：`{conclusion_code}`",
        f"",
        f"**理由**：{conclusion_reason}",
        f"",
        f"---",
        f"",
        f"## 二、分期 IC_IR",
        f"",
        f"> IC_IR = IC均值 / IC标准差（ddof=1），t统计量 = IC_IR × √n",
        f"",
        _sub_period_table(sub_df),
        f"",
        f"---",
        f"",
        f"## 三、分项 IC（F1-F9）",
        f"",
        f"> 注：F 分项为二值（0/1/NaN），Spearman IC 即点双列相关系数，",
        f"> 绝对值通常小于连续因子的 IC，两者不宜直接比较大小。",
        f"",
        f"### 验证期 2021-2022",
        f"",
        _component_table_for_period(comp_df, "2021-2022"),
        f"",
        f"### 训练期 2019-2020（对比）",
        f"",
        _component_table_for_period(comp_df, "2019-2020"),
        f"",
        f"### 训练期 2012-2015（历史基准）",
        f"",
        _component_table_for_period(comp_df, "2012-2015"),
        f"",
        f"---",
        f"",
        f"## 四、行业分层 IC",
        f"",
        f"> 使用 SW2021 一级行业，每期每行业 ≥ {MIN_INDUSTRY_STOCKS} 只股票才计算 IC，",
        f"> 每行业 ≥ {MIN_IC_OBS} 个观测期才汇报。",
        f"",
        f"### 验证期 2021-2022（重点）",
        f"",
        _industry_table_for_period(ind_df, "2021-2022"),
        f"",
        f"### 训练期 2019-2020（对比）",
        f"",
        _industry_table_for_period(ind_df, "2019-2020"),
        f"",
        f"---",
        f"",
        f"## 五、后续建议",
        f"",
        f"依据结论 `{conclusion_code}`：",
    ]

    if conclusion_code == "REBUILD_COMPONENTS":
        lines += [
            f"1. 从 `comp_df[comp_df['period']=='2021-2022']` 中找 IC_IR>0 的分项",
            f"2. 将有效分项线性合成为新质量因子，命名如 `quality_composite_v2`",
            f"3. **另起一次训练/验证期实验**（新 Spec + `run_experiment.py`），对比与 `piotroski_f` 的 IC_IR",
            f"4. 实验通过后，通过 `compare_runs.py` 和 `promote_run.py` 决定是否替换",
        ]
    elif conclusion_code == "REMOVE_CANDIDATE":
        lines += [
            f"1. 在 `docs/当前文档/04_研究与改进/当前问题与改进路线.md` 中登记 `piotroski_f` 的 `warn→remove` 候选结论",
            f"2. **另起一次不含 `piotroski_f` 的训练/验证期实验**，确认 IR 不下降",
            f"3. 若 IR 不下降或提升，才从 `final_factors.json` 中移除",
        ]
    elif conclusion_code == "REVERSE_USE_CANDIDATE":
        lines += [
            f"1. **另起一次训练/验证期实验**，将 `piotroski_f` 的 `factor_direction` 改为 -1",
            f"2. 重新评估 IC_IR（应从负转正）",
            f"3. 若确认有效，在 `factor_summary.csv` 中更新 `factor_direction=-1`",
        ]
    else:  # KEEP_MONITOR
        lines += [
            f"1. 继续按月监控健康快照（`run_factor_health.py`）",
            f"2. 若连续 3 个月出现 DETERIORATING 趋势，启动新诊断",
        ]

    lines += [f"", f"---", f"", f"_本报告由 `diagnose_piotroski.py` 自动生成_"]
    return "\n".join(lines)


# ── 参数解析 ───────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="piotroski_f 专项诊断")
    parser.add_argument(
        "--ic-history",
        default=str(_PROJECT_ROOT / _DEFAULT_IC_HISTORY),
        help=f"IC 历史 parquet（默认：{_DEFAULT_IC_HISTORY}）",
    )
    parser.add_argument(
        "--factor-panel",
        default=str(_PROJECT_ROOT / _DEFAULT_FACTOR_PANEL),
        help=f"piotroski_f 因子面板（默认：{_DEFAULT_FACTOR_PANEL}）",
    )
    parser.add_argument(
        "--fwd-ret",
        default=str(_PROJECT_ROOT / _DEFAULT_FWD_RET),
        help=f"forward return 面板（默认：{_DEFAULT_FWD_RET}）",
    )
    parser.add_argument(
        "--industry",
        default=str(_PROJECT_ROOT / _DEFAULT_INDUSTRY),
        help=f"行业数据（默认：{_DEFAULT_INDUSTRY}）",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_PROJECT_ROOT / _DEFAULT_OUTPUT_DIR),
        help=f"输出目录（默认：{_DEFAULT_OUTPUT_DIR}）",
    )
    parser.add_argument(
        "--no-component",
        action="store_true",
        help="跳过分项 IC 计算（速度较慢，约 1-3 分钟）",
    )
    return parser.parse_args()


# ── 主流程 ─────────────────────────────────────────────────────────────────────

def main() -> None:  # noqa: C901
    args = _parse_args()

    # ── 1. 加载 IC 历史 ──────────────────────────────────────────────────────
    ic_path = Path(args.ic_history)
    if not ic_path.exists():
        log.error("IC 历史文件不存在，请先运行 run_factor_evaluation.py：%s", ic_path)
        sys.exit(1)
    ic_history = pd.read_parquet(ic_path)
    log.info("IC 历史已加载：%s  shape=%s", ic_path.name, ic_history.shape)

    if "piotroski_f" not in ic_history.columns:
        log.error("IC 历史中不含 piotroski_f 列，请检查 ic_history_research_all.parquet")
        sys.exit(1)

    piotroski_ic = ic_history["piotroski_f"].dropna()
    log.info("piotroski_f IC：%d 期，范围 %s ~ %s",
             len(piotroski_ic), piotroski_ic.index.min().date(), piotroski_ic.index.max().date())

    # 测试集保护：只使用训练+验证期
    rebalance_dates = ic_history.index[ic_history.index <= VALID_END]
    log.info("调仓日数（训练+验证）：%d", len(rebalance_dates))

    # ── 2. 加载其他数据 ──────────────────────────────────────────────────────
    fwd_path = Path(args.fwd_ret)
    if not fwd_path.exists():
        log.error("forward return 面板不存在：%s", fwd_path)
        sys.exit(1)
    fwd_ret_panel = pd.read_parquet(fwd_path)
    # 只保留训练+验证期
    fwd_ret_panel = fwd_ret_panel[fwd_ret_panel.index <= VALID_END]
    log.info("forward return 已加载：shape=%s", fwd_ret_panel.shape)

    factor_path = Path(args.factor_panel)
    if not factor_path.exists():
        log.error("piotroski_f 因子面板不存在：%s", factor_path)
        sys.exit(1)
    factor_panel = pd.read_parquet(factor_path)
    factor_panel = factor_panel[factor_panel.index <= VALID_END]
    log.info("piotroski_f 因子面板已加载：shape=%s", factor_panel.shape)

    ind_path = Path(args.industry)
    if not ind_path.exists():
        log.error("行业数据不存在：%s", ind_path)
        sys.exit(1)
    industry_df = pd.read_parquet(ind_path)
    log.info("行业数据已加载：shape=%s", industry_df.shape)

    # ── 3. 加载 PIT 原始数据（一次性加载，避免循环重复 I/O）────────────────
    if not args.no_component:
        log.info("加载 indicator_pit 和 financial_pit（可能需要数十秒）…")
        ip_raw = get_indicator_pit_raw()
        fp_raw = get_financial_pit_raw()
        log.info("PIT 数据已加载")

    # ── 4. 分析一：分期 IC_IR ─────────────────────────────────────────────
    log.info("=== 分析一：分期 IC_IR ===")
    sub_df = analyze_sub_periods(piotroski_ic)
    log.info("\n%s", sub_df.round(4).to_string())

    # ── 5. 分析二：分项 IC ────────────────────────────────────────────────
    if args.no_component:
        log.info("=== 分析二：分项 IC（已跳过，--no-component）===")
        comp_df = pd.DataFrame(columns=[
            "component", "label", "period", "n", "ic_mean", "ic_std",
            "ic_ir", "t_stat", "p_value", "pct_positive",
        ])
    else:
        log.info("=== 分析二：分项 IC（约 1-3 分钟）===")
        comp_df = compute_component_ic_df(ip_raw, fp_raw, fwd_ret_panel, rebalance_dates)
        log.info("分项 IC 计算完成，%d 行", len(comp_df))

    # ── 6. 分析三：行业分层 IC ────────────────────────────────────────────
    log.info("=== 分析三：行业分层 IC ===")
    ind_df = compute_industry_ic_df(factor_panel, fwd_ret_panel, industry_df, rebalance_dates)
    log.info("行业 IC 计算完成，%d 行", len(ind_df))

    # ── 7. 给出诊断结论 ──────────────────────────────────────────────────
    conclusion_code, conclusion_reason = _determine_conclusion(sub_df, comp_df)
    log.info("诊断结论：%s", conclusion_code)
    log.info("理由：%s", conclusion_reason)

    # ── 8. 保存输出 ──────────────────────────────────────────────────────
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    comp_out = output_dir / "piotroski_component_ic.csv"
    comp_df.to_csv(comp_out, index=False, encoding="utf-8-sig")
    log.info("分项 IC 已保存：%s  shape=%s", comp_out.name, comp_df.shape)

    ind_out = output_dir / "piotroski_industry_ic.csv"
    ind_df.to_csv(ind_out, index=False, encoding="utf-8-sig")
    log.info("行业 IC 已保存：%s  shape=%s", ind_out.name, ind_df.shape)

    today_str = pd.Timestamp.today().strftime("%Y-%m-%d")
    report_text = generate_report(
        sub_df, comp_df, ind_df,
        conclusion_code, conclusion_reason,
        today_str,
    )
    report_out = output_dir / "piotroski_diagnosis.md"
    report_out.write_text(report_text, encoding="utf-8")
    log.info("诊断报告已保存：%s", report_out.name)

    # ── 9. 控制台汇总 ────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print(f"Piotroski F-Score 诊断汇总")
    print("=" * 65)
    print(f"\n[分期 IC_IR]")
    for period, row in sub_df.iterrows():
        mark = "[+]" if (not np.isnan(row["ic_ir"]) and row["ic_ir"] > 0) else "[-]"
        print(f"  {mark} {period}: n={int(row['n']) if not np.isnan(row['n']) else '—'}  "
              f"IC_IR={_fmt(row['ic_ir'], '.3f')}  t={_fmt(row['t_stat'], '.2f')}")
    print(f"\n[结论] {conclusion_code}")
    print(f"  {conclusion_reason}")
    print(f"\n[输出目录] {output_dir}")
    print("=" * 65)


if __name__ == "__main__":
    main()
