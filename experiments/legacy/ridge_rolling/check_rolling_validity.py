"""
experiments/ridge_rolling/check_rolling_validity.py
=====================================================
rolling-48m 实验（IR=1.092）可靠性补充检验

Check #4: 月度信号 IC vs 组合超额收益相关性
Check #5: TopN 等权对照（完全 bypass 优化器）
Check #6: 信号时移检验（超前1期 = 未来函数红旗；滞后1期 = 衰减特征）
Extra   : 逐月超额收益分布、年度分解、信号相关性分析

输出：experiments/ridge_rolling/results/validity_checks/
"""

import sys
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from experiments.legacy.ridge_signal.run_ridge_experiment import (
    compute_ic_series,
    compute_ic_stats,
    split_ic_by_period,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results" / "validity_checks"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TOPN = cfg.OPT_TOPN  # 50


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_data() -> dict:
    fwd_ret = pd.read_parquet(cfg.DATA_PROC / "fwd_ret_panel.parquet")
    index_member = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")

    signal_48m = pd.read_parquet(
        Path(__file__).parent / "results" / "rolling_48m_composite_panel.parquet"
    )
    signal_exp = pd.read_parquet(
        Path(__file__).parent.parent / "ridge_signal" / "results" / "ridge_composite_panel.parquet"
    )

    nav_48m = pd.read_parquet(
        Path(__file__).parent / "results" / "rolling_48m" / "backtest_nav_valid.parquet"
    )

    log.info("fwd_ret: %s  %s~%s", fwd_ret.shape,
             fwd_ret.index[0].date(), fwd_ret.index[-1].date())
    log.info("signal_48m: %s  %s~%s", signal_48m.shape,
             signal_48m.index[0].date(), signal_48m.index[-1].date())
    log.info("signal_exp: %s  %s~%s", signal_exp.shape,
             signal_exp.index[0].date(), signal_exp.index[-1].date())
    log.info("nav_48m: %s  %s~%s", nav_48m.shape,
             nav_48m.index[0].date(), nav_48m.index[-1].date())

    return dict(
        fwd_ret=fwd_ret,
        index_member=index_member,
        signal_48m=signal_48m,
        signal_exp=signal_exp,
        nav_48m=nav_48m,
    )


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def ic_stats_from_series(ic: pd.Series, label: str) -> dict:
    ic_clean = ic.dropna()
    if len(ic_clean) < 3:
        return {"label": label, "n": 0}
    mean = ic_clean.mean()
    std  = ic_clean.std(ddof=1)
    t_stat, p_val = scipy_stats.ttest_1samp(ic_clean, 0.0)
    return {
        "label":    label,
        "n":        len(ic_clean),
        "ic_mean":  mean,
        "ic_std":   std,
        "ic_ir":    mean / std if std > 0 else float("nan"),
        "win_rate": float((ic_clean > 0).mean()),
        "t_stat":   t_stat,
        "p_value":  p_val,
    }


def bench_ret_series(fwd_ret: pd.DataFrame, index_member: pd.DataFrame) -> pd.Series:
    dates = index_member.index.get_level_values("rebalance_date").unique()
    result = {}
    for T in fwd_ret.index:
        if T not in dates:
            continue
        snap = index_member.loc[T]
        w_b = snap["index_weight"].astype(float)
        fwd_T = fwd_ret.loc[T]
        common = w_b.index.intersection(fwd_T.dropna().index)
        if len(common) < 10:
            continue
        w_c = w_b.reindex(common)
        w_c = w_c / w_c.sum()
        result[T] = float((fwd_T.reindex(common) * w_c).sum())
    return pd.Series(result, name="bench_ret")


def topn_equal_weight_returns(
    signal: pd.DataFrame,
    fwd_ret: pd.DataFrame,
    index_member: pd.DataFrame,
    topn: int = 50,
) -> pd.Series:
    """
    纯信号选股 TopN 等权月度收益（不经过优化器）。

    Returns: pd.Series indexed by rebalance_date
    """
    dates_in_member = index_member.index.get_level_values("rebalance_date").unique()
    rets = {}
    for T in signal.index:
        if T not in fwd_ret.index:
            continue
        if T not in dates_in_member:
            continue
        # 当期成分股
        snap = index_member.loc[T]
        universe = set(snap.index)
        # 停牌排除
        halted = set(snap.index[snap["is_suspended"]])
        tradeable = universe - halted

        sig_t = signal.loc[T].reindex(list(tradeable)).dropna()
        if len(sig_t) < topn:
            continue
        top_stocks = sig_t.nlargest(topn).index
        fwd_t = fwd_ret.loc[T].reindex(top_stocks).dropna()
        if len(fwd_t) == 0:
            continue
        rets[T] = fwd_t.mean()
    return pd.Series(rets, name="topn_ret")


# ---------------------------------------------------------------------------
# Check #4: 信号 IC vs 组合超额收益相关性
# ---------------------------------------------------------------------------

def check4_ic_vs_excess(
    signal_48m: pd.DataFrame,
    signal_exp: pd.DataFrame,
    fwd_ret: pd.DataFrame,
    nav_48m: pd.DataFrame,
) -> dict:
    log.info("=" * 60)
    log.info("Check #4: 月度信号 IC vs 组合超额收益相关性")
    log.info("=" * 60)

    # IC 序列（验证期）
    ic_48m = compute_ic_series(signal_48m, fwd_ret)
    ic_exp  = compute_ic_series(signal_exp,  fwd_ret)

    ic_48m_valid = ic_48m[(ic_48m.index >= cfg.VALID_START) & (ic_48m.index <= cfg.VALID_END)]
    ic_exp_valid  = ic_exp[ (ic_exp.index  >= cfg.VALID_START) & (ic_exp.index  <= cfg.VALID_END)]

    # 月度组合超额收益（从 NAV 反推）
    # NAV 文件是日频，aggregation 到月末
    nav_48m = nav_48m.copy()
    nav_48m.index = pd.to_datetime(nav_48m.index)
    monthly_nav = nav_48m.resample("ME").last()
    excess_monthly = (
        monthly_nav["strategy"] / monthly_nav["strategy"].shift(1)
        - monthly_nav["benchmark"] / monthly_nav["benchmark"].shift(1)
    ).dropna()
    excess_monthly.index = excess_monthly.index.to_period("M").to_timestamp("M")

    # 对齐
    common = ic_48m_valid.index.intersection(excess_monthly.index)
    ic_aligned     = ic_48m_valid.reindex(common).dropna()
    excess_aligned = excess_monthly.reindex(common).dropna()
    common2 = ic_aligned.index.intersection(excess_aligned.index)

    ic_aligned     = ic_aligned.reindex(common2)
    excess_aligned = excess_aligned.reindex(common2)

    if len(common2) >= 5:
        corr, pval = scipy_stats.pearsonr(ic_aligned, excess_aligned)
    else:
        corr, pval = float("nan"), float("nan")

    # 同样对 expanding 做
    common_exp = ic_exp_valid.index.intersection(excess_monthly.index)
    if len(common_exp) >= 5:
        ic_exp_a = ic_exp_valid.reindex(common_exp).dropna()
        ex_a = excess_monthly.reindex(ic_exp_a.index).dropna()
        cmn = ic_exp_a.index.intersection(ex_a.index)
        corr_exp, pval_exp = scipy_stats.pearsonr(ic_exp_a.reindex(cmn), ex_a.reindex(cmn))
    else:
        corr_exp, pval_exp = float("nan"), float("nan")

    log.info(
        "rolling-48m: IC~超额相关=%+.3f  p=%.3f  n=%d",
        corr, pval, len(common2),
    )
    log.info(
        "expanding:   IC~超额相关=%+.3f  p=%.3f",
        corr_exp, pval_exp,
    )

    # 解读
    if not np.isnan(corr):
        if corr < 0.3:
            flag = "⚠️ 低相关：信号与组合收益脱钩，优化器/换手效应主导组合绩效"
        elif corr < 0.6:
            flag = "🔶 中等相关：信号部分驱动组合收益，有其他因素参与"
        else:
            flag = "✅ 高相关：信号是组合收益主要驱动力"
        log.info("解读: %s", flag)
    else:
        flag = "数据不足"

    return {
        "corr_48m":  corr,
        "pval_48m":  pval,
        "corr_exp":  corr_exp,
        "pval_exp":  pval_exp,
        "n":         len(common2),
        "flag":      flag,
        "ic_valid":  ic_48m_valid,
        "excess_monthly": excess_monthly,
    }


# ---------------------------------------------------------------------------
# Check #5: TopN 等权对照
# ---------------------------------------------------------------------------

def check5_topn_equalweight(
    signal_48m: pd.DataFrame,
    signal_exp: pd.DataFrame,
    fwd_ret: pd.DataFrame,
    index_member: pd.DataFrame,
) -> dict:
    log.info("=" * 60)
    log.info("Check #5: TopN%d 等权对照（bypass 优化器）", TOPN)
    log.info("=" * 60)

    bench_ret = bench_ret_series(fwd_ret, index_member)

    # 各信号 TopN 等权月度收益
    rets_48m = topn_equal_weight_returns(signal_48m, fwd_ret, index_member, TOPN)
    rets_exp  = topn_equal_weight_returns(signal_exp,  fwd_ret, index_member, TOPN)

    # 验证期
    valid_mask_48m = (rets_48m.index >= cfg.VALID_START) & (rets_48m.index <= cfg.VALID_END)
    valid_mask_exp = (rets_exp.index >= cfg.VALID_START)  & (rets_exp.index <= cfg.VALID_END)

    r48 = rets_48m[valid_mask_48m]
    rexp = rets_exp[valid_mask_exp]
    rb = bench_ret[(bench_ret.index >= cfg.VALID_START) & (bench_ret.index <= cfg.VALID_END)]

    # 超额收益
    def excess_stats(ret: pd.Series, bench: pd.Series, label: str) -> dict:
        common = ret.index.intersection(bench.index)
        if len(common) < 3:
            return {"label": label, "ir": float("nan")}
        ex = ret.reindex(common) - bench.reindex(common)
        ann_ex = ex.mean() * 12
        te = ex.std(ddof=1) * np.sqrt(12)
        ir = ann_ex / te if te > 0 else float("nan")
        win = float((ex > 0).mean())
        log.info(
            "%s TopN%d 等权: 年化超额=%.2f%%  TE=%.2f%%  IR=%.3f  月胜率=%.1f%%",
            label, TOPN, ann_ex * 100, te * 100, ir, win * 100,
        )
        return {"label": label, "ann_excess": ann_ex, "te": te, "ir": ir, "win_rate": win, "n": len(common)}

    stats_48m = excess_stats(r48, rb, "rolling-48m")
    stats_exp  = excess_stats(rexp, rb, "expanding")

    return {"rolling_48m": stats_48m, "expanding": stats_exp}


# ---------------------------------------------------------------------------
# Check #6: 信号时移检验
# ---------------------------------------------------------------------------

def check6_time_shift(
    signal_48m: pd.DataFrame,
    signal_exp: pd.DataFrame,
    fwd_ret: pd.DataFrame,
) -> dict:
    log.info("=" * 60)
    log.info("Check #6: 信号时移检验")
    log.info("=" * 60)

    def shifted_ic_stats(panel: pd.DataFrame, shift: int, label: str) -> dict:
        """
        shift=+1 → signal[T] = original[T-1]（滞后1期，测衰减）
        shift=-1 → signal[T] = original[T+1]（超前1期，测未来函数 — 红旗！）
        """
        idx = panel.index
        shifted = panel.copy()
        shifted.index = idx.shift(shift, freq="ME")
        ic_all = compute_ic_series(shifted, fwd_ret)
        # 验证期
        ic_valid = ic_all[
            (ic_all.index >= cfg.VALID_START) & (ic_all.index <= cfg.VALID_END)
        ]
        s = ic_stats_from_series(ic_valid, label)
        log.info(
            "  %s: IC_IR=%.4f  IC_mean=%.4f  t=%.2f  p=%.3f  n=%d",
            label,
            s.get("ic_ir",   float("nan")),
            s.get("ic_mean", float("nan")),
            s.get("t_stat",  float("nan")),
            s.get("p_value", float("nan")),
            s.get("n",       0),
        )
        return s

    # 原始
    log.info("--- 原始信号 ---")
    ic_48m_orig = compute_ic_series(signal_48m, fwd_ret)
    ic_exp_orig  = compute_ic_series(signal_exp,  fwd_ret)

    ic_48m_v = ic_48m_orig[(ic_48m_orig.index >= cfg.VALID_START) & (ic_48m_orig.index <= cfg.VALID_END)]
    ic_exp_v  = ic_exp_orig[ (ic_exp_orig.index  >= cfg.VALID_START) & (ic_exp_orig.index  <= cfg.VALID_END)]

    orig_48m = ic_stats_from_series(ic_48m_v, "rolling-48m 原始")
    orig_exp  = ic_stats_from_series(ic_exp_v,  "expanding 原始")
    log.info(
        "  rolling-48m 原始: IC_IR=%.4f  IC_mean=%.4f  t=%.2f  p=%.3f",
        orig_48m["ic_ir"], orig_48m["ic_mean"], orig_48m["t_stat"], orig_48m["p_value"]
    )
    log.info(
        "  expanding  原始: IC_IR=%.4f  IC_mean=%.4f  t=%.2f  p=%.3f",
        orig_exp["ic_ir"],  orig_exp["ic_mean"],  orig_exp["t_stat"],  orig_exp["p_value"]
    )

    # 超前1期（未来函数红旗）
    log.info("--- 超前1期（signal[T] = original[T+1]，未来函数红旗测试）---")
    lead1_48m = shifted_ic_stats(signal_48m, -1, "rolling-48m lead+1")
    lead1_exp  = shifted_ic_stats(signal_exp,  -1, "expanding  lead+1")

    # 滞后1期（衰减特征）
    log.info("--- 滞后1期（signal[T] = original[T-1]，信号衰减测试）---")
    lag1_48m = shifted_ic_stats(signal_48m, +1, "rolling-48m lag+1")
    lag1_exp  = shifted_ic_stats(signal_exp,  +1, "expanding  lag+1")

    # 判断：超前信号 IC_IR 是否 > 原始的 80%
    def leakage_flag(orig: dict, lead1: dict) -> str:
        o = orig.get("ic_ir", float("nan"))
        l = lead1.get("ic_ir", float("nan"))
        if np.isnan(o) or np.isnan(l):
            return "无法判断（数据不足）"
        ratio = abs(l) / abs(o) if abs(o) > 1e-6 else float("nan")
        if np.isnan(ratio):
            return "无法判断"
        if ratio > 0.8:
            return f"🚨 高风险：超前信号 IC_IR 保留 {ratio:.0%}（≥80%），疑似未来函数！"
        elif ratio > 0.5:
            return f"⚠️ 中风险：超前信号 IC_IR 保留 {ratio:.0%}（50-80%），需进一步排查"
        else:
            return f"✅ 低风险：超前信号 IC_IR 仅保留 {ratio:.0%}（<50%），无明显未来函数"

    flag_48m = leakage_flag(orig_48m, lead1_48m)
    flag_exp  = leakage_flag(orig_exp,  lead1_exp)
    log.info("rolling-48m 未来函数判断: %s", flag_48m)
    log.info("expanding   未来函数判断: %s", flag_exp)

    return {
        "orig_48m":  orig_48m,
        "orig_exp":  orig_exp,
        "lead1_48m": lead1_48m,
        "lead1_exp":  lead1_exp,
        "lag1_48m":  lag1_48m,
        "lag1_exp":   lag1_exp,
        "flag_48m":  flag_48m,
        "flag_exp":  flag_exp,
    }


# ---------------------------------------------------------------------------
# Extra: 逐月超额收益分布分析
# ---------------------------------------------------------------------------

def extra_monthly_analysis(nav_48m: pd.DataFrame, index_member: pd.DataFrame,
                            fwd_ret: pd.DataFrame) -> dict:
    log.info("=" * 60)
    log.info("Extra: 逐月超额收益分布")
    log.info("=" * 60)

    nav_48m = nav_48m.copy()
    nav_48m.index = pd.to_datetime(nav_48m.index)
    monthly_nav = nav_48m.resample("ME").last()
    monthly_strat = monthly_nav["strategy"].pct_change().dropna()
    monthly_bench = monthly_nav["benchmark"].pct_change().dropna()

    common = monthly_strat.index.intersection(monthly_bench.index)
    excess = (monthly_strat.reindex(common) - monthly_bench.reindex(common)).dropna()
    excess.index = excess.index.to_period("M").to_timestamp("M")

    valid_mask = (excess.index >= cfg.VALID_START) & (excess.index <= cfg.VALID_END)
    ex_valid = excess[valid_mask]

    log.info("验证期月度超额（rolling-48m）:")
    log.info("  月数=%d  均值=%.2f%%  std=%.2f%%  最大=%.2f%%  最小=%.2f%%",
             len(ex_valid), ex_valid.mean() * 100, ex_valid.std() * 100,
             ex_valid.max() * 100, ex_valid.min() * 100)

    # 哪几个月贡献最多？
    top5_positive = ex_valid.nlargest(5)
    top5_negative = ex_valid.nsmallest(5)
    log.info("  最强5个月: %s", {str(k.date()): f"{v:.2%}" for k, v in top5_positive.items()})
    log.info("  最弱5个月: %s", {str(k.date()): f"{v:.2%}" for k, v in top5_negative.items()})

    # 年度分解
    ex_by_year = ex_valid.groupby(ex_valid.index.year)
    for yr, grp in ex_by_year:
        ann = grp.sum()  # 月度相加近似年化（保守）
        log.info("  %d年: 月度超额累计=%.2f%%  月数=%d", yr, ann * 100, len(grp))

    # 集中度：前3大正超额月份占总超额多少比例
    total_pos = ex_valid[ex_valid > 0].sum()
    top3_sum  = top5_positive.head(3).sum()
    concentration = top3_sum / total_pos if total_pos > 0 else float("nan")
    log.info("  前3大正超额月份占总正超额比 = %.1f%%", concentration * 100)

    if concentration > 0.6:
        conc_flag = "⚠️ 收益高度集中（前3月>60%），稳定性存疑"
    elif concentration > 0.45:
        conc_flag = "🔶 收益中度集中（45-60%），注意持续性"
    else:
        conc_flag = "✅ 收益分散，月胜率较好"
    log.info("  集中度判断: %s", conc_flag)

    # 信号相关性：rolling-48m 与 expanding 信号的截面相关
    return {
        "ex_valid": ex_valid,
        "concentration": concentration,
        "conc_flag": conc_flag,
    }


# ---------------------------------------------------------------------------
# 信号相关性分析
# ---------------------------------------------------------------------------

def signal_correlation(signal_48m: pd.DataFrame, signal_exp: pd.DataFrame) -> None:
    log.info("=" * 60)
    log.info("信号相关性: rolling-48m vs expanding（验证期逐月截面相关）")
    log.info("=" * 60)

    valid_mask_48m = (signal_48m.index >= cfg.VALID_START) & (signal_48m.index <= cfg.VALID_END)
    valid_mask_exp = (signal_exp.index  >= cfg.VALID_START) & (signal_exp.index  <= cfg.VALID_END)
    s48  = signal_48m[valid_mask_48m]
    sexp  = signal_exp[valid_mask_exp]

    common_dates = s48.index.intersection(sexp.index)
    corr_list = []
    for T in common_dates:
        a = s48.loc[T].dropna()
        b = sexp.loc[T].reindex(a.index).dropna()
        a_aligned = a.reindex(b.index)
        if len(b) < 10:
            continue
        c = float(a_aligned.corr(b, method="spearman"))
        corr_list.append(c)

    if corr_list:
        arr = np.array(corr_list)
        log.info(
            "  截面 Spearman 相关: 均值=%.3f  std=%.3f  min=%.3f  max=%.3f  n=%d",
            arr.mean(), arr.std(), arr.min(), arr.max(), len(arr),
        )
        if arr.mean() > 0.85:
            log.info("  🔶 高度相关（>0.85）: 两信号几乎等价，IR差异主要来自优化器/参数")
        elif arr.mean() > 0.6:
            log.info("  ℹ️ 中等相关（0.6-0.85）: 滚动窗口带来实质性的 β 差异")
        else:
            log.info("  ✅ 低相关（<0.6）: 两信号实质不同，rolling-48m 有独立预测价值")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    data = load_data()
    fwd_ret      = data["fwd_ret"]
    index_member = data["index_member"]
    signal_48m   = data["signal_48m"]
    signal_exp   = data["signal_exp"]
    nav_48m      = data["nav_48m"]

    signal_correlation(signal_48m, signal_exp)

    r4 = check4_ic_vs_excess(signal_48m, signal_exp, fwd_ret, nav_48m)
    r5 = check5_topn_equalweight(signal_48m, signal_exp, fwd_ret, index_member)
    r6 = check6_time_shift(signal_48m, signal_exp, fwd_ret)
    rx = extra_monthly_analysis(nav_48m, index_member, fwd_ret)

    # 汇总报告
    log.info("")
    log.info("=" * 60)
    log.info("综合判断汇总")
    log.info("=" * 60)

    # Check #4
    corr4 = r4["corr_48m"]
    pval4 = r4["pval_48m"]
    log.info(
        "Check #4 IC~超额相关: r=%.3f  p=%.3f  → %s",
        corr4, pval4, r4["flag"]
    )

    # Check #5
    ir5_48m = r5["rolling_48m"].get("ir", float("nan"))
    ir5_exp  = r5["expanding"].get("ir", float("nan"))
    log.info(
        "Check #5 TopN%d等权: rolling-48m IR=%.3f  expanding IR=%.3f",
        TOPN, ir5_48m, ir5_exp
    )
    if not np.isnan(ir5_48m) and not np.isnan(ir5_exp):
        if ir5_48m > ir5_exp + 0.1:
            log.info(
                "  → ✅ 等权策略也领先（+%.3f），信号质量真实优于 expanding",
                ir5_48m - ir5_exp,
            )
        elif ir5_48m < ir5_exp - 0.1:
            log.info(
                "  → ⚠️ 等权策略反而落后（%.3f），组合 IR 提升主要来自优化器行为，不是信号",
                ir5_48m - ir5_exp,
            )
        else:
            log.info("  → ℹ️ 等权策略相近，信号质量差异不大（IR 差<0.1）")

    # Check #6
    log.info("Check #6 时移: %s", r6["flag_48m"])
    log.info("          expanding 对照: %s", r6["flag_exp"])

    # Extra
    log.info("Extra 集中度: %s", rx["conc_flag"])

    # 最终结论
    log.info("")
    log.info("=" * 60)
    log.info("最终结论")
    log.info("=" * 60)

    flags = [
        ("未来函数", r6["flag_48m"]),
        ("IC~超额脱钩", r4["flag"]),
        ("收益集中度", rx["conc_flag"]),
    ]
    red_flags = [f for label, f in flags if "🚨" in f]
    amber_flags = [f for label, f in flags if "⚠️" in f]

    if red_flags:
        log.info("🚨 发现高风险问题: %s", "; ".join(red_flags))
        log.info("建议：在确认未来函数来源之前，不应将 rolling-48m 结果用于决策")
    elif amber_flags:
        log.info("⚠️ 发现中风险问题，需进一步验证:")
        for f in amber_flags:
            log.info("  - %s", f)
        log.info("建议：结果值得关注，但需要更多样本期或参数鲁棒性检验")
    else:
        log.info("✅ 所有检验通过，无明显数据问题或过拟合迹象")
        log.info("建议：rolling-48m 信号可以作为进一步优化的基础")

    log.info("=" * 60)


if __name__ == "__main__":
    main()
