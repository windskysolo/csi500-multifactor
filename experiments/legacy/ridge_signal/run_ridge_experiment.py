"""
experiments/ridge_signal/run_ridge_experiment.py
================================================

Step 3+4: Entry script for Ridge signal experiment (Plan A).

Pipeline:
  1. Load 16 final factor panels + fwd_ret_panel
  2. Verify data interfaces (Step 1)
  3. Select alpha via 5-fold walk-forward CV covering 2015-2019
  4. Build Ridge composite signal panel for all available dates
  5. Compute per-date Spearman IC for Ridge and IC_IR baseline
  6. Save outputs to experiments/ridge_signal/results/
  7. Generate comparison_report.md

Usage:
    python -m experiments.ridge_signal.run_ridge_experiment
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from experiments.legacy.ridge_signal.ridge_combiner import RidgeCombiner, verify_data_interfaces

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FACTOR_PANELS_DIR   = cfg.DATA_PROC / "factor_panels"
FWD_RET_PATH        = cfg.DATA_PROC / "fwd_ret_panel.parquet"
INDEX_MEMBER_PATH   = cfg.DATA_PROC / "index_member.parquet"
FINAL_FACTORS_PATH  = _ROOT / "reports" / "factor_evaluation" / "final_factors.json"
IC_IR_SIGNAL_PATH   = cfg.DATA_PROC / "composite_signal_ic_ir.parquet"
RESULTS_DIR         = Path(__file__).parent / "results"

# Wider alpha range: previous run showed alpha=500 was at the upper boundary
# (monotonically increasing CV curve). Extend to confirm true optimum.
_ALPHA_CANDIDATES = [0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_final_factors() -> list[str]:
    """Load the 16 Gate-filtered final factors from factor_evaluation report."""
    with open(FINAL_FACTORS_PATH, encoding="utf-8") as f:
        d = json.load(f)
    factors = d["final_factors"]
    log.info("Loaded %d final factors: %s", len(factors), factors)
    return factors


def load_factor_panels(factor_names: list[str]) -> dict[str, pd.DataFrame]:
    """Load preprocessed factor panel parquets for the given factor names."""
    panels: dict[str, pd.DataFrame] = {}
    for name in factor_names:
        path = FACTOR_PANELS_DIR / f"{name}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Factor panel not found: {path}")
        panels[name] = pd.read_parquet(path)
    log.info("Loaded %d factor panels from %s", len(panels), FACTOR_PANELS_DIR)
    return panels


def load_fwd_ret() -> pd.DataFrame:
    """Load realized 1M forward return panel."""
    fwd = pd.read_parquet(FWD_RET_PATH)
    log.info("fwd_ret_panel: shape=%s, range=%s ~ %s",
             fwd.shape, fwd.index[0].date(), fwd.index[-1].date())
    return fwd


def load_ic_ir_baseline() -> pd.DataFrame:
    """Load existing IC_IR-weighted composite signal for baseline comparison."""
    sig = pd.read_parquet(IC_IR_SIGNAL_PATH)
    log.info("IC_IR baseline: shape=%s, range=%s ~ %s",
             sig.shape, sig.index[0].date(), sig.index[-1].date())
    return sig


def compute_bench_ret_series(
    fwd_ret_panel: pd.DataFrame,
    index_member: pd.DataFrame,
) -> pd.Series:
    """
    Compute benchmark-weighted average forward return for each rebalance date.

    This is the "common market factor" subtracted from each stock's fwd_ret
    when use_excess_return=True, so Ridge trains on cross-sectional excess
    returns rather than absolute returns.

    Weight source: index_member['index_weight'] (percentage form, renormalized
    to sum=1 over stocks present in fwd_ret_panel at each date).

    Args:
        fwd_ret_panel: (date × ts_code) realized 1M forward return
        index_member:  MultiIndex (rebalance_date, ts_code) with index_weight col
    Returns:
        pd.Series indexed by rebalance_date, values = benchmark weighted fwd_ret
    """
    dates_in_member = index_member.index.get_level_values("rebalance_date").unique()
    bench_ret: dict[pd.Timestamp, float] = {}

    for T in fwd_ret_panel.index:
        if T not in dates_in_member:
            continue
        snap  = index_member.loc[T]
        w_b   = snap["index_weight"].astype(float)
        fwd_T = fwd_ret_panel.loc[T]

        common = w_b.index.intersection(fwd_T.dropna().index)
        if len(common) < 10:
            continue

        w_common = w_b.reindex(common)
        w_common = w_common / w_common.sum()   # renormalize for stocks missing fwd_ret
        bench_ret[T] = float((fwd_T.reindex(common) * w_common).sum())

    series = pd.Series(bench_ret, name="bench_ret")
    log.info(
        "bench_ret_series: %d dates, mean=%.2f%%, std=%.2f%%",
        len(series), series.mean() * 100, series.std() * 100,
    )
    return series


# ---------------------------------------------------------------------------
# IC computation
# ---------------------------------------------------------------------------

def compute_ic_series(
    signal_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
) -> pd.Series:
    """
    Compute per-date Spearman IC between signal and realized fwd_ret.

    Only dates present in both panels are evaluated.
    Stocks must be non-NaN in both signal and fwd_ret (aligned by ts_code).

    Returns:
        pd.Series, index=rebalance_date, values=Spearman IC (float or NaN)
    """
    common_dates = signal_panel.index.intersection(fwd_ret_panel.index)
    ic_values: dict[pd.Timestamp, float] = {}

    for T in common_dates:
        sig_t = signal_panel.loc[T].dropna()
        fwd_t = fwd_ret_panel.loc[T].reindex(sig_t.index).dropna()
        sig_aligned = sig_t.reindex(fwd_t.index)

        if len(fwd_t) < 10:
            ic_values[T] = np.nan
            continue

        ic = float(sig_aligned.corr(fwd_t, method="spearman"))
        ic_values[T] = ic

    return pd.Series(ic_values, name="ic")


def compute_ic_stats(ic_series: pd.Series, label: str = "") -> dict:
    """
    Compute IC summary statistics from a per-date IC series.

    Returns dict with keys: n, ic_mean, ic_std, ic_ir, win_rate, t_stat, p_value.
    IC_IR = ic_mean / ic_std.  t_stat = ic_mean / (ic_std / sqrt(n)).
    """
    valid = ic_series.dropna()
    n = len(valid)
    if n < 3:
        return {
            "label": label, "n": n,
            "ic_mean": np.nan, "ic_std": np.nan, "ic_ir": np.nan,
            "win_rate": np.nan, "t_stat": np.nan, "p_value": np.nan,
        }

    ic_mean = float(valid.mean())
    ic_std  = float(valid.std(ddof=1))
    ic_ir   = ic_mean / ic_std if ic_std > 1e-10 else np.nan
    win_rate = float((valid > 0).mean())
    t_stat  = ic_mean / (ic_std / np.sqrt(n)) if ic_std > 1e-10 else np.nan
    p_value = float(2 * scipy_stats.t.sf(abs(t_stat), df=n - 1)) if not np.isnan(t_stat) else np.nan

    return {
        "label": label, "n": n,
        "ic_mean": ic_mean, "ic_std": ic_std, "ic_ir": ic_ir,
        "win_rate": win_rate, "t_stat": t_stat, "p_value": p_value,
    }


def split_ic_by_period(
    ic_series: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """Split IC series into training and validation sub-series."""
    train_mask = ic_series.index <= cfg.TRAIN_END
    valid_mask = (ic_series.index >= cfg.VALID_START) & (ic_series.index <= cfg.VALID_END)
    return ic_series[train_mask], ic_series[valid_mask]


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _fmt(val: float | None, fmt: str = ".4f") -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "N/A"
    return format(val, fmt)


def _pct(val: float | None) -> str:
    return _fmt(val * 100 if val is not None and not np.isnan(val) else val, ".1f") + "%" \
        if val is not None and not np.isnan(val) else "N/A"


def generate_comparison_report(
    factor_names: list[str],
    interface_check: dict,
    selected_alpha: float,
    cv_results: pd.DataFrame,
    ridge_panel: pd.DataFrame,
    coef_history: pd.DataFrame,
    ridge_ic_all: pd.Series,
    baseline_ic_all: pd.Series,
    gen_time: str,
    use_excess_return: bool = True,
) -> str:
    """Build full markdown comparison report."""

    # Split IC series by period
    ridge_train_ic,    ridge_val_ic    = split_ic_by_period(ridge_ic_all)
    baseline_train_ic, baseline_val_ic = split_ic_by_period(baseline_ic_all)

    # IC stats
    ridge_full_stats   = compute_ic_stats(ridge_ic_all,       "Ridge (全期)")
    ridge_train_stats  = compute_ic_stats(ridge_train_ic,     "Ridge (训练期)")
    ridge_val_stats    = compute_ic_stats(ridge_val_ic,       "Ridge (验证期)")
    base_full_stats    = compute_ic_stats(baseline_ic_all,    "IC_IR (全期)")
    base_train_stats   = compute_ic_stats(baseline_train_ic,  "IC_IR (训练期)")
    base_val_stats     = compute_ic_stats(baseline_val_ic,    "IC_IR (验证期)")

    # Cold-start info
    n_cold = len([T for T in ridge_panel.index if T not in ridge_ic_all.index])
    first_signal_date = ridge_panel.index[0].date() if not ridge_panel.empty else "N/A"

    # Average factor coefficients
    avg_coef = coef_history.mean().sort_values(key=abs, ascending=False) \
        if coef_history is not None and not coef_history.empty else pd.Series()

    # CV fold summary table
    if not cv_results.empty:
        cv_pivot = cv_results.pivot_table(
            index="fold", columns="alpha", values="ic_ir", aggfunc="first"
        )
        cv_mean_row = cv_results.groupby("alpha")["ic_ir"].mean().rename("均值")
        cv_table_lines = ["| 折叠 | " + " | ".join(str(a) for a in sorted(cv_pivot.columns)) + " |",
                          "|------|" + "|".join(["---"] * len(cv_pivot.columns)) + "|"]
        for fold_i, row in cv_pivot.iterrows():
            cv_table_lines.append("| Fold " + str(fold_i) + " | " +
                                  " | ".join(_fmt(row.get(a)) for a in sorted(cv_pivot.columns)) + " |")
        cv_table_lines.append("| **均值** | " +
                               " | ".join(_fmt(cv_mean_row.get(a)) for a in sorted(cv_pivot.columns)) + " |")
        cv_table = "\n".join(cv_table_lines)
    else:
        cv_table = "*（CV 结果为空）*"

    def stats_row(s: dict) -> str:
        return (
            f"| {s['label']} | {s['n']} | {_fmt(s['ic_mean'])} "
            f"| {_fmt(s['ic_std'])} | {_fmt(s['ic_ir'])} "
            f"| {_pct(s['win_rate'])} | {_fmt(s['t_stat'])} | {_fmt(s['p_value'])} |"
        )

    target_label = "超额收益（fwd_ret − 基准加权收益）" if use_excess_return else "绝对收益（fwd_ret）"
    report = f"""\
# Ridge 信号合成实验报告（Plan A — 超额收益目标）

> 自动生成时间：{gen_time}
> 分支：feature/expand-train-2012
> 实验目录：`experiments/ridge_signal/`

---

## 1. 实验设计摘要

| 项目 | 内容 |
|------|------|
| 实现方法 | Walk-forward Pooled Ridge Regression |
| **训练目标 y** | **{target_label}** |
| 候选因子 | {len(factor_names)} 个（Gate-2 最终筛选：`final_factors.json`） |
| 数据期间 | {interface_check['date_range'][0]} ~ {interface_check['date_range'][1]} |
| 公共日期数 | {interface_check['n_common_dates']} 个月 |
| Alpha 候选 | {", ".join(str(a) for a in _ALPHA_CANDIDATES)} |
| CV 折叠 | 5 折（每折验证 1 年：2015-2019） |
| Purge 月数 | 2 个月（防止未来函数） |
| 冷启动期 | 前 24 个月（最小训练月数） |
| 首个信号日期 | {first_signal_date} |
| 选定 Alpha | **{selected_alpha}** |
| NaN 处理 | 有效因子数 ≥ 8 时纳入，缺失填 0（z-score 中性值） |

---

## 2. 数据接口核查（Step 1）

| 检查项 | 结果 |
|--------|------|
| 缺失因子 | {interface_check['missing_factors'] or '无'} |
| fwd_ret 日期数 | {interface_check['n_fwd_dates']} |
| 公共日期数 | {interface_check['n_common_dates']} |
| 接口验证通过 | {'✅' if interface_check['valid'] else '❌'} |

因子 NaN 率与平均覆盖股票数：

| 因子 | NaN 率 | 平均覆盖股数 |
|------|--------|------------|
""" + "\n".join(
        f"| {n} | {_fmt(interface_check['nan_rates'].get(n, float('nan')), '.1%')} "
        f"| {_fmt(interface_check['avg_stocks_per_date'].get(n, float('nan')), '.0f')} |"
        for n in factor_names
    ) + f"""

---

## 3. Walk-forward CV Alpha 选择

CV 折叠 IC_IR 矩阵（alpha × 折叠）：

{cv_table}

> **选定 alpha = {selected_alpha}**（CV 验证期均值 IC_IR 最高）

---

## 4. Ridge 信号覆盖统计

| 指标 | 值 |
|------|---|
| 总信号日期数 | {len(ridge_panel)} |
| 平均覆盖股数/日期 | {_fmt(ridge_panel.notna().sum(axis=1).mean(), '.0f')} |
| 最小覆盖股数 | {_fmt(float(ridge_panel.notna().sum(axis=1).min()), '.0f')} |
| 最大覆盖股数 | {_fmt(float(ridge_panel.notna().sum(axis=1).max()), '.0f')} |

---

## 5. IC 对比分析（Ridge vs IC_IR 基准）

> IC = Spearman rank correlation(signal_T, fwd_ret_T)
> IC_IR = IC均值 / IC标准差；t_stat = IC均值 / (IC标准差 / sqrt(n))
> 训练期：{cfg.TRAIN_START.date()} ~ {cfg.TRAIN_END.date()}；验证期：{cfg.VALID_START.date()} ~ {cfg.VALID_END.date()}
> ⚠️ Ridge 训练期 IC 含 alpha 选择带来的轻微 in-sample 偏差；验证期 IC 为完全 OOS。

| 方法 | n日期 | IC均值 | IC标准差 | IC_IR | IC胜率 | t统计量 | p值 |
|------|------|--------|--------|-------|--------|--------|-----|
{stats_row(ridge_full_stats)}
{stats_row(ridge_train_stats)}
{stats_row(ridge_val_stats)}
{stats_row(base_full_stats)}
{stats_row(base_train_stats)}
{stats_row(base_val_stats)}

---

## 6. 因子系数均值（|β| 降序，alpha={selected_alpha}）

> 系数来自横截面标准化后的因子（z-score），绝对值越大代表 Ridge 对该因子给予越高权重。

| 因子 | 均值系数 |
|------|--------|
""" + "\n".join(
        f"| {name} | {_fmt(float(coef), '+.4f')} |"
        for name, coef in avg_coef.items()
    ) + """

---

## 7. 结论与建议

"""

    # Add conclusions based on results
    conclusions = []
    ridge_val_ir = ridge_val_stats.get("ic_ir", np.nan)
    base_val_ir  = base_val_stats.get("ic_ir", np.nan)

    if not np.isnan(ridge_val_ir) and not np.isnan(base_val_ir):
        if ridge_val_ir > base_val_ir + 0.1:
            conclusions.append(
                f"- ✅ Ridge 验证期 IC_IR（{_fmt(ridge_val_ir)}）显著优于 IC_IR 基准"
                f"（{_fmt(base_val_ir)}），差值 {_fmt(ridge_val_ir - base_val_ir)}，"
                "可考虑替换主管线信号合成方法。"
            )
        elif ridge_val_ir > base_val_ir:
            conclusions.append(
                f"- ⚠️ Ridge 验证期 IC_IR（{_fmt(ridge_val_ir)}）略优于 IC_IR 基准"
                f"（{_fmt(base_val_ir)}），差值 {_fmt(ridge_val_ir - base_val_ir)}，"
                "提升有限，需权衡引入复杂度的收益。"
            )
        else:
            conclusions.append(
                f"- ❌ Ridge 验证期 IC_IR（{_fmt(ridge_val_ir)}）不优于 IC_IR 基准"
                f"（{_fmt(base_val_ir)}），差值 {_fmt(ridge_val_ir - base_val_ir)}，"
                "暂不建议替换主管线。"
            )

    ridge_val_ic_mean = ridge_val_stats.get("ic_mean", np.nan)
    if not np.isnan(ridge_val_ic_mean):
        if ridge_val_ic_mean > 0.03:
            conclusions.append(
                f"- Ridge 验证期 IC 均值 {_fmt(ridge_val_ic_mean)} > 3%，"
                "信号具有实质性预测力。"
            )
        elif ridge_val_ic_mean > 0:
            conclusions.append(
                f"- Ridge 验证期 IC 均值 {_fmt(ridge_val_ic_mean)}（正方向，但偏低）。"
            )
        else:
            conclusions.append(
                f"- ⚠️ Ridge 验证期 IC 均值为负 ({_fmt(ridge_val_ic_mean)})，"
                "验证期信号方向反转，需检查训练数据或参数。"
            )

    ridge_val_pval = ridge_val_stats.get("p_value", np.nan)
    if not np.isnan(ridge_val_pval):
        conclusions.append(
            f"- Ridge 验证期 t 统计量 = {_fmt(ridge_val_stats['t_stat'])}，"
            f"p 值 = {_fmt(ridge_val_pval)}（{'显著' if ridge_val_pval < 0.05 else '不显著'}，α=5%）。"
        )

    conclusions.append(
        "- 后续可考虑：(1) Elastic Net 替代 Ridge（Plan B，同时选择因子）；"
        "(2) 非线性模型（LightGBM）；(3) 以 Ridge 信号替换 IC_IR 跑完整回测对比。"
    )

    report += "\n".join(conclusions) + "\n"
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    gen_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # --- 1. Load data ---
    log.info("=== Step 1: Loading data ===")
    factor_names   = load_final_factors()
    factor_panels  = load_factor_panels(factor_names)
    fwd_ret_panel  = load_fwd_ret()
    ic_ir_baseline = load_ic_ir_baseline()

    # Load index_member and compute benchmark return series for excess-return training
    index_member   = pd.read_parquet(INDEX_MEMBER_PATH)
    bench_ret_series = compute_bench_ret_series(fwd_ret_panel, index_member)
    bench_ret_series.to_csv(RESULTS_DIR / "bench_ret_series.csv")

    # --- 2. Verify interfaces ---
    log.info("=== Step 2: Verifying interfaces ===")
    interface_check = verify_data_interfaces(factor_panels, fwd_ret_panel, factor_names)
    if not interface_check["valid"]:
        log.error("Interface check FAILED: %s", interface_check)
        sys.exit(1)

    all_dates = fwd_ret_panel.index

    # --- 3. Walk-forward CV for alpha selection (use_excess_return=True) ---
    log.info("=== Step 3: Walk-forward CV (5 folds: 2015-2019, excess-return target) ===")
    combiner = RidgeCombiner(
        factor_names,
        alpha_candidates=_ALPHA_CANDIDATES,
        use_excess_return=True,
    )
    selected_alpha = combiner.select_alpha_walk_forward(
        factor_panels, fwd_ret_panel, all_dates,
        bench_ret_series=bench_ret_series,
    )
    log.info("Selected alpha: %.1f", selected_alpha)

    cv_path = RESULTS_DIR / "cv_alpha_selection.csv"
    combiner.cv_results_.to_csv(cv_path, index=False)
    log.info("CV results saved to %s", cv_path)

    # --- 4. Build Ridge signal panel ---
    log.info("=== Step 4: Building Ridge signal panel ===")
    ridge_panel = combiner.build_ridge_panel(
        factor_panels, fwd_ret_panel, all_dates,
        bench_ret_series=bench_ret_series,
    )
    coef_history = combiner.coef_history_

    panel_path = RESULTS_DIR / "ridge_composite_panel.parquet"
    coef_path  = RESULTS_DIR / "ridge_coef_history.parquet"
    ridge_panel.to_parquet(panel_path)
    if coef_history is not None and not coef_history.empty:
        coef_history.to_parquet(coef_path)
    log.info("Ridge panel saved to %s (%s)", panel_path, ridge_panel.shape)
    log.info("Coef history saved to %s (%s)",
             coef_path, coef_history.shape if coef_history is not None else "empty")

    # --- 5. Compute IC series ---
    log.info("=== Step 5: Computing IC series ===")
    ridge_ic    = compute_ic_series(ridge_panel, fwd_ret_panel)
    baseline_ic = compute_ic_series(ic_ir_baseline, fwd_ret_panel)

    ic_df = pd.DataFrame({"ridge_ic": ridge_ic, "baseline_ic": baseline_ic})
    ic_df.to_csv(RESULTS_DIR / "ic_comparison.csv")
    log.info("IC series saved (%d dates for Ridge, %d for baseline)",
             ridge_ic.notna().sum(), baseline_ic.notna().sum())

    r_train, r_val = split_ic_by_period(ridge_ic)
    b_train, b_val = split_ic_by_period(baseline_ic)

    def _print_stats(label: str, ic_s: pd.Series) -> None:
        s = compute_ic_stats(ic_s, label)
        log.info(
            "  %-30s n=%3d  IC=%.4f  IC_IR=%.4f  WinRate=%.1f%%  t=%.2f  p=%.3f",
            s["label"], s["n"],
            s["ic_mean"] if not np.isnan(s["ic_mean"]) else 0.0,
            s["ic_ir"]   if not np.isnan(s["ic_ir"])   else 0.0,
            (s["win_rate"] * 100) if not np.isnan(s["win_rate"]) else 0.0,
            s["t_stat"]  if not np.isnan(s["t_stat"])  else 0.0,
            s["p_value"] if not np.isnan(s["p_value"]) else 1.0,
        )

    log.info("--- IC Summary ---")
    _print_stats("Ridge-ExcessRet (训练期)", r_train)
    _print_stats("Ridge-ExcessRet (验证期)", r_val)
    _print_stats("IC_IR baseline  (训练期)", b_train)
    _print_stats("IC_IR baseline  (验证期)", b_val)

    # --- 6. Generate comparison report ---
    log.info("=== Step 6: Generating comparison report ===")
    report = generate_comparison_report(
        factor_names=factor_names,
        interface_check=interface_check,
        selected_alpha=selected_alpha,
        cv_results=combiner.cv_results_,
        ridge_panel=ridge_panel,
        coef_history=coef_history,
        ridge_ic_all=ridge_ic,
        baseline_ic_all=baseline_ic,
        gen_time=gen_time,
        use_excess_return=True,
    )

    report_path = RESULTS_DIR / "comparison_report.md"
    report_path.write_text(report, encoding="utf-8")
    log.info("Comparison report saved to %s", report_path)
    log.info("=== Experiment complete ===")


if __name__ == "__main__":
    main()
