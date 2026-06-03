"""
Stage 7/8 audit for the two problems in check/0526/problems.md.

Scope:
  - Validation period only: 2021-01-01 to 2022-12-31.
  - Formal test set is not read or run.
  - The old Pipeline B low-win-rate question uses the frozen replay artifacts
    under check/0526/tmp/stage2/pipeline_b_lam0050_replayed/.
  - rolling-48m uses the replay artifacts under check/0526/tmp/stage2/rolling_48m_replayed/.

Outputs:
  - Stage 7: monthly/yearly/state decomposition, Brinson industry attribution,
    and approximate stock contribution.
  - Stage 8: paired monthly tests, bootstrap confidence intervals, and
    rolling-window multiple-comparison disclosure.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "check" / "0526"
TMP_STAGE2 = OUT_DIR / "tmp" / "stage2"

sys.path.insert(0, str(ROOT))

from src import config as cfg  # noqa: E402
from src.attribution import brinson  # noqa: E402
from src.data.loader import get_rebalance_dates, load_daily_quote, load_industry, load_universe  # noqa: E402


TRADING_DAYS_PER_YEAR = 252
BOOTSTRAP_N = 10_000
BOOTSTRAP_BLOCK = 3
RANDOM_SEED = cfg.RANDOM_SEED


PRIMARY_NAVS = {
    "pipeline_b_frozen_replay": TMP_STAGE2 / "pipeline_b_lam0050_replayed" / "backtest_nav_valid.parquet",
    "rolling_48m_replay": TMP_STAGE2 / "rolling_48m_replayed" / "backtest_nav_valid.parquet",
}

PRIMARY_WEIGHTS = {
    "pipeline_b_frozen_replay": TMP_STAGE2 / "pipeline_b_lam0050_replayed" / "actual_weights_valid.parquet",
    "rolling_48m_replay": TMP_STAGE2 / "rolling_48m_replayed" / "actual_weights_valid.parquet",
}

ROLLING_WINDOW_NAVS = {
    "pipeline_b_frozen_replay": TMP_STAGE2 / "pipeline_b_lam0050_replayed" / "backtest_nav_valid.parquet",
    "rolling_36m_live_artifact": ROOT / "experiments" / "ridge_rolling" / "results" / "rolling_36m" / "backtest_nav_valid.parquet",
    "rolling_48m_replay": TMP_STAGE2 / "rolling_48m_replayed" / "backtest_nav_valid.parquet",
    "rolling_60m_live_artifact": ROOT / "experiments" / "ridge_rolling" / "results" / "rolling_60m" / "backtest_nav_valid.parquet",
}


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _to_dt_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def _fmt(value: Any, digits: int = 6) -> str:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if math.isnan(val):
        return "N/A"
    return f"{val:.{digits}g}"


def _pct(value: Any, digits: int = 2) -> str:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if math.isnan(val):
        return "N/A"
    return f"{val * 100:.{digits}f}%"


def _load_nav(path: Path) -> pd.DataFrame:
    nav = _to_dt_index(pd.read_parquet(path))
    missing = {"strategy", "benchmark"} - set(nav.columns)
    if missing:
        raise KeyError(f"{path} missing columns: {sorted(missing)}")
    return nav[["strategy", "benchmark"]].dropna()


def _daily_metrics(nav: pd.DataFrame) -> dict[str, float]:
    ret = nav[["strategy", "benchmark"]].pct_change().dropna()
    excess = ret["strategy"] - ret["benchmark"]
    ann_strategy = (1.0 + ret["strategy"]).prod() ** (TRADING_DAYS_PER_YEAR / len(ret)) - 1.0
    ann_benchmark = (1.0 + ret["benchmark"]).prod() ** (TRADING_DAYS_PER_YEAR / len(ret)) - 1.0
    ann_excess = ann_strategy - ann_benchmark
    te = float(excess.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))
    ir = ann_excess / te if te > 1e-12 else float("nan")
    excess_nav = (1.0 + excess).cumprod()
    drawdown = excess_nav / excess_nav.cummax() - 1.0
    return {
        "n_days": float(len(ret)),
        "ann_strategy_return": float(ann_strategy),
        "ann_benchmark_return": float(ann_benchmark),
        "ann_excess_return": float(ann_excess),
        "tracking_error": te,
        "ir_daily": float(ir),
        "max_excess_drawdown": float(drawdown.min()),
    }


def _monthly_from_nav(source_id: str, nav: pd.DataFrame) -> pd.DataFrame:
    monthly = nav.resample("ME").last().pct_change().dropna()
    monthly["monthly_excess_return"] = monthly["strategy"] - monthly["benchmark"]
    monthly["source_id"] = source_id
    monthly["is_positive_excess"] = monthly["monthly_excess_return"] > 0
    monthly.index.name = "month_end"
    return monthly.reset_index()


def _monthly_with_states() -> pd.DataFrame:
    frames = []
    for source_id, path in PRIMARY_NAVS.items():
        frames.append(_monthly_from_nav(source_id, _load_nav(path)))
    monthly = pd.concat(frames, ignore_index=True)

    benchmark_daily = _load_nav(PRIMARY_NAVS["pipeline_b_frozen_replay"])["benchmark"].pct_change().dropna()
    vol = benchmark_daily.groupby(benchmark_daily.index.to_period("M")).std(ddof=1)
    vol.index = vol.index.to_timestamp("M")
    median_vol = float(vol.median())

    monthly["month_end"] = pd.to_datetime(monthly["month_end"])
    monthly["year"] = monthly["month_end"].dt.year
    monthly["benchmark_state"] = np.where(monthly["benchmark"] >= 0.0, "benchmark_up", "benchmark_down")
    monthly["benchmark_monthly_vol"] = monthly["month_end"].map(vol)
    monthly["vol_state"] = np.where(monthly["benchmark_monthly_vol"] >= median_vol, "high_vol", "low_vol")
    monthly["return_state"] = monthly["benchmark_state"] + "_" + monthly["vol_state"]

    pivot = monthly.pivot(index="month_end", columns="source_id", values="monthly_excess_return")
    if {"pipeline_b_frozen_replay", "rolling_48m_replay"}.issubset(pivot.columns):
        pivot["rolling_minus_pipeline_b"] = pivot["rolling_48m_replay"] - pivot["pipeline_b_frozen_replay"]
        monthly = monthly.merge(
            pivot["rolling_minus_pipeline_b"].reset_index(),
            on="month_end",
            how="left",
        )
    return monthly


def _yearly_summary(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (source_id, year), grp in monthly.groupby(["source_id", "year"]):
        excess = grp["monthly_excess_return"].astype(float)
        monthly_std = float(excess.std(ddof=1))
        rows.append(
            {
                "source_id": source_id,
                "year": int(year),
                "n_months": int(len(grp)),
                "strategy_return_sum": float(grp["strategy"].sum()),
                "benchmark_return_sum": float(grp["benchmark"].sum()),
                "excess_return_sum": float(excess.sum()),
                "monthly_win_rate": float((excess > 0.0).mean()),
                "monthly_ir_sqrt12": float(excess.mean() / monthly_std * math.sqrt(12)) if monthly_std > 1e-12 else float("nan"),
                "best_month": str(pd.Timestamp(grp.loc[excess.idxmax(), "month_end"]).date()),
                "best_month_excess": float(excess.max()),
                "worst_month": str(pd.Timestamp(grp.loc[excess.idxmin(), "month_end"]).date()),
                "worst_month_excess": float(excess.min()),
            }
        )
    return pd.DataFrame(rows)


def _state_summary(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, grp in monthly.groupby(["source_id", "benchmark_state"]):
        source_id, benchmark_state = keys
        excess = grp["monthly_excess_return"].astype(float)
        rows.append(
            {
                "source_id": source_id,
                "state_type": "benchmark_direction",
                "state": benchmark_state,
                "n_months": int(len(grp)),
                "excess_sum": float(excess.sum()),
                "excess_mean": float(excess.mean()),
                "win_rate": float((excess > 0.0).mean()),
            }
        )
    for keys, grp in monthly.groupby(["source_id", "vol_state"]):
        source_id, vol_state = keys
        excess = grp["monthly_excess_return"].astype(float)
        rows.append(
            {
                "source_id": source_id,
                "state_type": "benchmark_volatility",
                "state": vol_state,
                "n_months": int(len(grp)),
                "excess_sum": float(excess.sum()),
                "excess_mean": float(excess.mean()),
                "win_rate": float((excess > 0.0).mean()),
            }
        )
    for keys, grp in monthly.groupby(["source_id", "return_state"]):
        source_id, return_state = keys
        excess = grp["monthly_excess_return"].astype(float)
        rows.append(
            {
                "source_id": source_id,
                "state_type": "direction_x_volatility",
                "state": return_state,
                "n_months": int(len(grp)),
                "excess_sum": float(excess.sum()),
                "excess_mean": float(excess.mean()),
                "win_rate": float((excess > 0.0).mean()),
            }
        )
    return pd.DataFrame(rows)


def _drawdown_summary() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source_id, path in PRIMARY_NAVS.items():
        nav = _load_nav(path)
        ret = nav.pct_change().dropna()
        excess_nav = (1.0 + ret["strategy"] - ret["benchmark"]).cumprod()
        dd = excess_nav / excess_nav.cummax() - 1.0
        monthly_dd = dd.resample("ME").last().dropna()
        worst = monthly_dd.sort_values().head(5)
        for month_end, value in worst.items():
            rows.append(
                {
                    "source_id": source_id,
                    "month_end": month_end,
                    "excess_drawdown": float(value),
                    "is_underwater": bool(value < 0.0),
                }
            )
    return pd.DataFrame(rows)


def _run_brinson() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    period_frames = []
    industry_frames = []
    reconcile_rows: list[dict[str, Any]] = []
    monthly = _monthly_with_states()
    monthly_pivot = monthly.pivot(index="month_end", columns="source_id", values="monthly_excess_return")

    for source_id, path in PRIMARY_WEIGHTS.items():
        weights = _to_dt_index(pd.read_parquet(path))
        industry_attr, period_summary = brinson.compute_brinson_attribution(
            actual_weights=weights,
            backtest_start=cfg.VALID_START,
            backtest_end=cfg.VALID_END,
        )

        ps = period_summary.reset_index()
        ps["source_id"] = source_id
        ps["effect_residual"] = ps["total_effect"] - ps[[
            "allocation_effect",
            "selection_effect",
            "interaction_effect",
        ]].sum(axis=1)
        period_frames.append(ps)

        ia = industry_attr.reset_index()
        ia["source_id"] = source_id
        ia["effect_residual"] = ia["total_effect"] - ia[[
            "allocation_effect",
            "selection_effect",
            "interaction_effect",
        ]].sum(axis=1)
        industry_frames.append(ia)

        # Brinson has 24 holding periods from 2020-12-31 anchors, while the
        # metrics monthly win rate uses 23 month-end pct_change observations.
        # Reconcile only where period_end maps to an available monthly metric.
        for row in ps.to_dict("records"):
            period_end = pd.Timestamp(row["period_end"]).to_period("M").to_timestamp("M")
            nav_monthly = float(monthly_pivot.loc[period_end, source_id]) if period_end in monthly_pivot.index else float("nan")
            reconcile_rows.append(
                {
                    "source_id": source_id,
                    "period_start": row["period_start"],
                    "period_end": row["period_end"],
                    "brinson_excess_return_gross": float(row["excess_return"]),
                    "nav_monthly_excess_return_net": nav_monthly,
                    "gross_minus_nav_net": float(row["excess_return"] - nav_monthly) if pd.notna(nav_monthly) else float("nan"),
                    "cash_weight": float(row["cash_weight"]),
                    "weight_sum": float(row["weight_sum"]),
                }
            )

    period = pd.concat(period_frames, ignore_index=True)
    industry = pd.concat(industry_frames, ignore_index=True)
    reconcile = pd.DataFrame(reconcile_rows)
    return period, industry, reconcile


def _industry_summary(industry: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        industry.groupby(["source_id", "industry_code", "industry_name"], dropna=False)
        .agg(
            total_effect_sum=("total_effect", "sum"),
            allocation_effect_sum=("allocation_effect", "sum"),
            selection_effect_sum=("selection_effect", "sum"),
            interaction_effect_sum=("interaction_effect", "sum"),
            W_p_mean=("W_p", "mean"),
            W_b_mean=("W_b", "mean"),
            active_weight_mean=("W_p", "mean"),
            n_periods=("period_start", "nunique"),
        )
        .reset_index()
    )
    grouped["active_weight_mean"] = grouped["W_p_mean"] - grouped["W_b_mean"]

    pivot = grouped.pivot_table(
        index=["industry_code", "industry_name"],
        columns="source_id",
        values="total_effect_sum",
        aggfunc="first",
    )
    if {"pipeline_b_frozen_replay", "rolling_48m_replay"}.issubset(pivot.columns):
        diff = (pivot["rolling_48m_replay"] - pivot["pipeline_b_frozen_replay"]).rename("rolling_minus_pipeline_b_total_effect")
        grouped = grouped.merge(diff.reset_index(), on=["industry_code", "industry_name"], how="left")
    return grouped


def _load_price_panels(codes: list[str], start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    dq = load_daily_quote(start, end, codes)
    close_adj = dq["close_adj"].unstack("ts_code")
    open_adj = dq["open_adj"].unstack("ts_code")
    return close_adj, open_adj


def _stock_contribution() -> tuple[pd.DataFrame, pd.DataFrame]:
    rebalance_dates = get_rebalance_dates()
    periods = brinson._get_period_list(rebalance_dates, cfg.VALID_START, cfg.VALID_END)
    if not periods:
        raise ValueError("No attribution periods in validation window.")

    strategy_codes: set[str] = set()
    for path in PRIMARY_WEIGHTS.values():
        weights = pd.read_parquet(path)
        strategy_codes.update(c for c in weights.columns if weights[c].notna().any())

    benchmark_codes: set[str] = set()
    for T, _ in periods:
        benchmark_codes.update(str(c) for c in load_universe(T).index)

    all_codes = sorted(strategy_codes | benchmark_codes)
    close_adj, open_adj = _load_price_panels(all_codes, cfg.VALID_START, cfg.VALID_END)
    industry = load_industry(periods[0][0], cfg.VALID_END, all_codes)
    industry_panel = industry["industry_code"]
    industry_names = (
        industry.reset_index()[["ts_code", "industry_code", "industry_name"]]
        .drop_duplicates(["ts_code", "industry_code"])
        .drop_duplicates("ts_code", keep="last")
        .set_index("ts_code")
    )

    period_rows: list[dict[str, Any]] = []
    stock_rows: list[dict[str, Any]] = []
    for source_id, path in PRIMARY_WEIGHTS.items():
        weights = _to_dt_index(pd.read_parquet(path))
        for T, T_next in periods:
            w_p = brinson._get_period_start_weights(weights, T)
            if w_p.empty:
                continue
            w_b = brinson._get_benchmark_weights(T)
            codes = sorted(set(w_p.index) | set(w_b.index))
            returns, _ = brinson._compute_period_returns_t1_open(T, T_next, codes, close_adj, open_adj)
            industry_map = brinson._get_industry_map(T, codes, industry_panel)

            w_p_aligned = w_p.reindex(codes).fillna(0.0).astype(float)
            w_b_aligned = w_b.reindex(codes).fillna(0.0).astype(float)
            r_aligned = returns.reindex(codes).fillna(0.0).astype(float)
            strategy_contrib = w_p_aligned * r_aligned
            benchmark_contrib = w_b_aligned * r_aligned
            active_contrib = strategy_contrib - benchmark_contrib

            period_rows.append(
                {
                    "source_id": source_id,
                    "period_start": T,
                    "period_end": T_next,
                    "strategy_stock_contribution": float(strategy_contrib.sum()),
                    "benchmark_stock_contribution": float(benchmark_contrib.sum()),
                    "active_stock_contribution": float(active_contrib.sum()),
                    "top10_positive_share_of_positive": _top_share(strategy_contrib),
                    "top10_active_share_of_positive": _top_share(active_contrib),
                }
            )

            for code in codes:
                stock_rows.append(
                    {
                        "source_id": source_id,
                        "period_start": T,
                        "period_end": T_next,
                        "ts_code": code,
                        "industry_code": industry_map.get(code, cfg.INDUSTRY_UNCLASSIFIED_CODE),
                        "industry_name": industry_names["industry_name"].get(code, cfg.INDUSTRY_UNCLASSIFIED_NAME)
                        if code in industry_names.index
                        else cfg.INDUSTRY_UNCLASSIFIED_NAME,
                        "strategy_weight": float(w_p_aligned[code]),
                        "benchmark_weight": float(w_b_aligned[code]),
                        "stock_period_return": float(r_aligned[code]),
                        "strategy_contribution": float(strategy_contrib[code]),
                        "benchmark_contribution": float(benchmark_contrib[code]),
                        "active_contribution": float(active_contrib[code]),
                    }
                )

    period_detail = pd.DataFrame(period_rows)
    stock_detail = pd.DataFrame(stock_rows)
    summary = (
        stock_detail.groupby(["source_id", "ts_code", "industry_code", "industry_name"], dropna=False)
        .agg(
            strategy_contribution_sum=("strategy_contribution", "sum"),
            benchmark_contribution_sum=("benchmark_contribution", "sum"),
            active_contribution_sum=("active_contribution", "sum"),
            avg_strategy_weight_when_held=("strategy_weight", lambda s: float(s[s > 0].mean()) if (s > 0).any() else 0.0),
            n_periods_held=("strategy_weight", lambda s: int((s > 0).sum())),
            avg_stock_period_return=("stock_period_return", "mean"),
        )
        .reset_index()
    )

    pivot = summary.pivot_table(index=["ts_code", "industry_code", "industry_name"], columns="source_id", values="active_contribution_sum", aggfunc="first")
    if {"pipeline_b_frozen_replay", "rolling_48m_replay"}.issubset(pivot.columns):
        diff = (pivot["rolling_48m_replay"] - pivot["pipeline_b_frozen_replay"]).rename("rolling_minus_pipeline_b_active_contribution")
        summary = summary.merge(diff.reset_index(), on=["ts_code", "industry_code", "industry_name"], how="left")

    return period_detail, summary


def _top_share(values: pd.Series, n: int = 10) -> float:
    pos = values[values > 0.0].sort_values(ascending=False)
    total = float(pos.sum())
    if total <= 1e-12:
        return float("nan")
    return float(pos.head(n).sum() / total)


def run_stage7() -> dict[str, pd.DataFrame]:
    monthly = _monthly_with_states()
    yearly = _yearly_summary(monthly)
    state = _state_summary(monthly)
    drawdown = _drawdown_summary()
    brinson_period, brinson_industry, brinson_reconcile = _run_brinson()
    industry_summary = _industry_summary(brinson_industry)
    stock_period, stock_summary = _stock_contribution()

    monthly.to_csv(OUT_DIR / "stage7_monthly_state_decomposition.csv", index=False, encoding="utf-8-sig")
    yearly.to_csv(OUT_DIR / "stage7_yearly_summary.csv", index=False, encoding="utf-8-sig")
    state.to_csv(OUT_DIR / "stage7_market_state_summary.csv", index=False, encoding="utf-8-sig")
    drawdown.to_csv(OUT_DIR / "stage7_excess_drawdown_months.csv", index=False, encoding="utf-8-sig")
    brinson_period.to_csv(OUT_DIR / "stage7_brinson_period_summary.csv", index=False, encoding="utf-8-sig")
    brinson_industry.to_csv(OUT_DIR / "stage7_brinson_industry_detail.csv", index=False, encoding="utf-8-sig")
    industry_summary.to_csv(OUT_DIR / "stage7_brinson_industry_summary.csv", index=False, encoding="utf-8-sig")
    brinson_reconcile.to_csv(OUT_DIR / "stage7_contribution_reconciliation.csv", index=False, encoding="utf-8-sig")
    stock_period.to_csv(OUT_DIR / "stage7_stock_contribution_by_period.csv", index=False, encoding="utf-8-sig")
    stock_summary.to_csv(OUT_DIR / "stage7_stock_contribution_summary.csv", index=False, encoding="utf-8-sig")

    return {
        "monthly": monthly,
        "yearly": yearly,
        "state": state,
        "drawdown": drawdown,
        "brinson_period": brinson_period,
        "brinson_industry": brinson_industry,
        "industry_summary": industry_summary,
        "brinson_reconcile": brinson_reconcile,
        "stock_period": stock_period,
        "stock_summary": stock_summary,
    }


def _monthly_ir(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 3:
        return float("nan")
    std = float(values.std(ddof=1))
    if std <= 1e-12:
        return float("nan")
    return float(values.mean() / std * math.sqrt(12))


def _bootstrap_indices(rng: np.random.Generator, n: int, n_boot: int) -> np.ndarray:
    return rng.integers(0, n, size=(n_boot, n))


def _block_bootstrap_indices(rng: np.random.Generator, n: int, n_boot: int, block: int) -> np.ndarray:
    starts = rng.integers(0, n, size=(n_boot, math.ceil(n / block)))
    result = np.empty((n_boot, n), dtype=int)
    for i in range(n_boot):
        seq: list[int] = []
        for start in starts[i]:
            seq.extend([(int(start) + j) % n for j in range(block)])
            if len(seq) >= n:
                break
        result[i, :] = seq[:n]
    return result


def _ci(values: np.ndarray) -> tuple[float, float, float]:
    clean = values[np.isfinite(values)]
    if len(clean) == 0:
        return float("nan"), float("nan"), float("nan")
    return (
        float(np.nanmean(clean)),
        float(np.nanpercentile(clean, 2.5)),
        float(np.nanpercentile(clean, 97.5)),
    )


def _paired_stats(monthly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    pivot = monthly.pivot(index="month_end", columns="source_id", values="monthly_excess_return").dropna()
    x = pivot["pipeline_b_frozen_replay"].to_numpy(dtype=float)
    y = pivot["rolling_48m_replay"].to_numpy(dtype=float)
    diff = y - x
    n = len(diff)
    t_res = scipy_stats.ttest_1samp(diff, popmean=0.0)
    try:
        wilcoxon_res = scipy_stats.wilcoxon(diff, zero_method="wilcox", alternative="two-sided")
        wilcoxon_stat = float(wilcoxon_res.statistic)
        wilcoxon_p = float(wilcoxon_res.pvalue)
    except ValueError:
        wilcoxon_stat = float("nan")
        wilcoxon_p = float("nan")

    rows = [
        {
            "comparison": "rolling_48m_replay_minus_pipeline_b_frozen_replay",
            "n_months": int(n),
            "mean_monthly_diff": float(diff.mean()),
            "std_monthly_diff": float(diff.std(ddof=1)),
            "sum_diff": float(diff.sum()),
            "rolling_better_months": int((diff > 0.0).sum()),
            "rolling_better_rate": float((diff > 0.0).mean()),
            "paired_t_stat": float(t_res.statistic),
            "paired_t_p_value": float(t_res.pvalue),
            "wilcoxon_stat": wilcoxon_stat,
            "wilcoxon_p_value": wilcoxon_p,
            "pipeline_monthly_ir_sqrt12": _monthly_ir(x),
            "rolling_monthly_ir_sqrt12": _monthly_ir(y),
            "monthly_ir_diff_sqrt12": _monthly_ir(y) - _monthly_ir(x),
        }
    ]
    monthly_diff = pivot.reset_index()
    monthly_diff["rolling_minus_pipeline_b"] = diff
    return pd.DataFrame(rows), monthly_diff


def _bootstrap_stats(monthly: pd.DataFrame) -> pd.DataFrame:
    pivot = monthly.pivot(index="month_end", columns="source_id", values="monthly_excess_return").dropna()
    x = pivot["pipeline_b_frozen_replay"].to_numpy(dtype=float)
    y = pivot["rolling_48m_replay"].to_numpy(dtype=float)
    n = len(x)
    rng = np.random.default_rng(RANDOM_SEED)

    rows: list[dict[str, Any]] = []
    for method, indices in [
        ("ordinary_month_bootstrap", _bootstrap_indices(rng, n, BOOTSTRAP_N)),
        ("circular_block_bootstrap_3m", _block_bootstrap_indices(rng, n, BOOTSTRAP_N, BOOTSTRAP_BLOCK)),
    ]:
        x_sample = x[indices]
        y_sample = y[indices]
        diff_sample = y_sample - x_sample
        mean_diff = diff_sample.mean(axis=1)
        sum_diff = diff_sample.sum(axis=1)
        x_ir = np.apply_along_axis(_monthly_ir, 1, x_sample)
        y_ir = np.apply_along_axis(_monthly_ir, 1, y_sample)
        ir_diff = y_ir - x_ir
        for metric, values in [
            ("mean_monthly_diff", mean_diff),
            ("sum_monthly_diff", sum_diff),
            ("pipeline_monthly_ir_sqrt12", x_ir),
            ("rolling_monthly_ir_sqrt12", y_ir),
            ("monthly_ir_diff_sqrt12", ir_diff),
        ]:
            mean, lower, upper = _ci(values)
            rows.append(
                {
                    "method": method,
                    "metric": metric,
                    "bootstrap_n": BOOTSTRAP_N,
                    "sample_months": int(n),
                    "mean": mean,
                    "ci_2p5": lower,
                    "ci_97p5": upper,
                    "prob_gt_0": float(np.mean(values > 0.0)),
                }
            )
    return pd.DataFrame(rows)


def _p_adjust_bh(p_values: list[float]) -> list[float]:
    p = np.asarray(p_values, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adjusted = np.empty(n, dtype=float)
    cumulative = 1.0
    for rank_from_end, idx in enumerate(order[::-1], start=1):
        rank = n - rank_from_end + 1
        value = min(cumulative, p[idx] * n / rank)
        cumulative = value
        adjusted[idx] = value
    return adjusted.tolist()


def _multi_window_comparison() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    monthly_map: dict[str, pd.Series] = {}
    for source_id, path in ROLLING_WINDOW_NAVS.items():
        if not path.exists():
            continue
        nav = _load_nav(path)
        daily = _daily_metrics(nav)
        monthly = _monthly_from_nav(source_id, nav).set_index("month_end")["monthly_excess_return"]
        monthly_map[source_id] = monthly
        rows.append(
            {
                "source_id": source_id,
                "artifact_path": _rel(path),
                "artifact_scope_note": "frozen_replay" if "replay" in source_id or "pipeline_b" in source_id else "live_experiments_artifact",
                "n_months": int(len(monthly)),
                "monthly_win_rate": float((monthly > 0.0).mean()),
                "monthly_excess_sum": float(monthly.sum()),
                "monthly_excess_mean": float(monthly.mean()),
                "monthly_ir_sqrt12": _monthly_ir(monthly.to_numpy(dtype=float)),
                **daily,
            }
        )

    result = pd.DataFrame(rows)
    if "pipeline_b_frozen_replay" in monthly_map:
        base = monthly_map["pipeline_b_frozen_replay"]
        p_values: list[float] = []
        diff_rows: list[tuple[int, float]] = []
        for idx, row in result.iterrows():
            source = row["source_id"]
            if source == "pipeline_b_frozen_replay":
                p_values.append(float("nan"))
                continue
            common = pd.concat([base.rename("base"), monthly_map[source].rename("candidate")], axis=1).dropna()
            diff = common["candidate"] - common["base"]
            if len(diff) < 3:
                p_val = float("nan")
                t_stat = float("nan")
            else:
                t_res = scipy_stats.ttest_1samp(diff.to_numpy(dtype=float), 0.0)
                t_stat = float(t_res.statistic)
                p_val = float(t_res.pvalue)
            diff_rows.append((idx, p_val))
            result.loc[idx, "paired_vs_pipeline_t_stat"] = t_stat
            result.loc[idx, "paired_vs_pipeline_p_value"] = p_val
            result.loc[idx, "monthly_mean_diff_vs_pipeline"] = float(diff.mean()) if len(diff) else float("nan")
            result.loc[idx, "monthly_sum_diff_vs_pipeline"] = float(diff.sum()) if len(diff) else float("nan")
            result.loc[idx, "better_months_vs_pipeline"] = int((diff > 0.0).sum()) if len(diff) else 0
            p_values.append(p_val)

        valid_pairs = [(idx, p) for idx, p in diff_rows if pd.notna(p)]
        if valid_pairs:
            bonf = [min(float(p) * len(valid_pairs), 1.0) for _, p in valid_pairs]
            bh = _p_adjust_bh([float(p) for _, p in valid_pairs])
            for (idx, _), bonf_p, bh_p in zip(valid_pairs, bonf, bh):
                result.loc[idx, "p_value_bonferroni_3_windows"] = bonf_p
                result.loc[idx, "p_value_bh_3_windows"] = bh_p
    return result


def _ic_stats_table() -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    stage4 = OUT_DIR / "stage4_ic_summary.csv"
    stage5 = OUT_DIR / "stage5_shift_test_summary.csv"
    rolling_summary = ROOT / "experiments" / "ridge_rolling" / "results" / "ic_stats_summary.csv"
    if stage4.exists():
        df = pd.read_csv(stage4)
        df["source_file"] = _rel(stage4)
        rows.append(df)
    if stage5.exists():
        df = pd.read_csv(stage5)
        df = df[(df.get("period") == "valid") & (df.get("test_name") == "original")].copy()
        df["source_file"] = _rel(stage5)
        rows.append(df)
    if rolling_summary.exists():
        df = pd.read_csv(rolling_summary)
        df["source_file"] = _rel(rolling_summary)
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True, sort=False)


def run_stage8(monthly: pd.DataFrame) -> dict[str, pd.DataFrame]:
    paired, monthly_diff = _paired_stats(monthly)
    bootstrap = _bootstrap_stats(monthly)
    multi_window = _multi_window_comparison()
    ic_stats = _ic_stats_table()

    paired.to_csv(OUT_DIR / "stage8_monthly_paired_tests.csv", index=False, encoding="utf-8-sig")
    monthly_diff.to_csv(OUT_DIR / "stage8_monthly_pair_diff_detail.csv", index=False, encoding="utf-8-sig")
    bootstrap.to_csv(OUT_DIR / "stage8_bootstrap_ci.csv", index=False, encoding="utf-8-sig")
    multi_window.to_csv(OUT_DIR / "stage8_multi_window_comparison.csv", index=False, encoding="utf-8-sig")
    ic_stats.to_csv(OUT_DIR / "stage8_ic_stats.csv", index=False, encoding="utf-8-sig")
    return {
        "paired": paired,
        "monthly_diff": monthly_diff,
        "bootstrap": bootstrap,
        "multi_window": multi_window,
        "ic_stats": ic_stats,
    }


def _top_lines(df: pd.DataFrame, value_col: str, source_id: str | None = None, n: int = 5, ascending: bool = False) -> list[str]:
    data = df.copy()
    if source_id is not None and "source_id" in data.columns:
        data = data[data["source_id"].eq(source_id)]
    if data.empty or value_col not in data.columns:
        return ["- 无可用数据。"]
    cols = [c for c in ["month_end", "period_start", "period_end", "industry_name", "industry_code", "ts_code", value_col] if c in data.columns]
    top = data.sort_values(value_col, ascending=ascending).head(n)
    lines = []
    for row in top[cols].to_dict("records"):
        label_parts = []
        for key in ["month_end", "period_start", "period_end"]:
            if key in row and pd.notna(row[key]):
                label_parts.append(f"{key}={pd.Timestamp(row[key]).date()}")
        for key in ["ts_code", "industry_name", "industry_code"]:
            if key in row and pd.notna(row[key]):
                label_parts.append(f"{key}={row[key]}")
        lines.append(f"- {', '.join(label_parts)}: {value_col}={_fmt(row[value_col])}")
    return lines


def _row(df: pd.DataFrame, **conditions: Any) -> dict[str, Any]:
    sub = df.copy()
    for key, value in conditions.items():
        if key not in sub.columns:
            return {}
        sub = sub[sub[key].eq(value)]
    return sub.iloc[0].to_dict() if not sub.empty else {}


def write_reports(stage7: dict[str, pd.DataFrame], stage8: dict[str, pd.DataFrame]) -> None:
    monthly = stage7["monthly"]
    yearly = stage7["yearly"]
    state = stage7["state"]
    industry = stage7["industry_summary"]
    stock = stage7["stock_summary"]
    reconcile = stage7["brinson_reconcile"]
    paired = stage8["paired"]
    bootstrap = stage8["bootstrap"]
    multi_window = stage8["multi_window"]

    payoff_rows = []
    for source_id, grp in monthly.groupby("source_id"):
        excess = grp["monthly_excess_return"].astype(float)
        pos = excess[excess > 0.0]
        neg = excess[excess < 0.0]
        payoff_rows.append(
            {
                "source_id": source_id,
                "n_months": int(len(grp)),
                "positive_count": int(len(pos)),
                "negative_count": int(len(neg)),
                "win_rate": float((excess > 0.0).mean()),
                "positive_mean": float(pos.mean()) if len(pos) else float("nan"),
                "negative_mean": float(neg.mean()) if len(neg) else float("nan"),
                "payoff_ratio": float(pos.mean() / abs(neg.mean())) if len(pos) and len(neg) and abs(neg.mean()) > 1e-12 else float("nan"),
                "net_sum": float(excess.sum()),
            }
        )
    payoff = pd.DataFrame(payoff_rows)

    p_row = paired.iloc[0].to_dict() if not paired.empty else {}
    boot_ir = _row(bootstrap, method="ordinary_month_bootstrap", metric="monthly_ir_diff_sqrt12")
    block_ir = _row(bootstrap, method="circular_block_bootstrap_3m", metric="monthly_ir_diff_sqrt12")
    boot_mean = _row(bootstrap, method="ordinary_month_bootstrap", metric="mean_monthly_diff")
    block_mean = _row(bootstrap, method="circular_block_bootstrap_3m", metric="mean_monthly_diff")

    payoff_lines = []
    for row in payoff.to_dict("records"):
        payoff_lines.append(
            "- `{source}`: n={n}, win={win}, pos={pos_count}, neg={neg_count}, "
            "pos_mean={pos_mean}, neg_mean={neg_mean}, payoff={payoff}, net_sum={net}".format(
                source=row["source_id"],
                n=row["n_months"],
                win=_pct(row["win_rate"]),
                pos_count=row["positive_count"],
                neg_count=row["negative_count"],
                pos_mean=_pct(row["positive_mean"]),
                neg_mean=_pct(row["negative_mean"]),
                payoff=_fmt(row["payoff_ratio"]),
                net=_pct(row["net_sum"]),
            )
        )

    yearly_lines = []
    for row in yearly.to_dict("records"):
        yearly_lines.append(
            "- `{source}` {year}: n={n}, excess_sum={excess}, win={win}, monthly_IR={ir}".format(
                source=row["source_id"],
                year=row["year"],
                n=row["n_months"],
                excess=_pct(row["excess_return_sum"]),
                win=_pct(row["monthly_win_rate"]),
                ir=_fmt(row["monthly_ir_sqrt12"]),
            )
        )

    state_lines = []
    for row in state[state["state_type"].eq("benchmark_direction")].to_dict("records"):
        state_lines.append(
            "- `{source}` {state}: n={n}, excess_sum={excess}, win={win}".format(
                source=row["source_id"],
                state=row["state"],
                n=row["n_months"],
                excess=_pct(row["excess_sum"]),
                win=_pct(row["win_rate"]),
            )
        )

    multi_lines = []
    for row in multi_window.to_dict("records"):
        if row["source_id"] == "pipeline_b_frozen_replay":
            continue
        multi_lines.append(
            "- `{source}`: daily_IR={ir}, monthly_win={win}, paired_p={p}, BH_p={bh}, note={note}".format(
                source=row["source_id"],
                ir=_fmt(row.get("ir_daily")),
                win=_pct(row.get("monthly_win_rate")),
                p=_fmt(row.get("paired_vs_pipeline_p_value")),
                bh=_fmt(row.get("p_value_bh_3_windows")),
                note=row.get("artifact_scope_note", ""),
            )
        )

    top_month_diff = monthly.drop_duplicates(["month_end", "rolling_minus_pipeline_b"])[["month_end", "rolling_minus_pipeline_b"]].dropna()
    top_industry_diff = industry.drop_duplicates(["industry_code", "industry_name", "rolling_minus_pipeline_b_total_effect"])
    top_stock_diff = stock.drop_duplicates(["ts_code", "industry_code", "industry_name", "rolling_minus_pipeline_b_active_contribution"])

    reconciliation_summary = (
        reconcile.groupby("source_id")
        .agg(
            avg_gross_minus_nav_net=("gross_minus_nav_net", "mean"),
            max_abs_gross_minus_nav_net=("gross_minus_nav_net", lambda s: float(s.abs().max())),
            n_reconciled=("gross_minus_nav_net", lambda s: int(s.notna().sum())),
        )
        .reset_index()
    )
    residual_summary = (
        stage7["brinson_period"].groupby("source_id")
        .agg(
            max_abs_effect_residual=("effect_residual", lambda s: float(s.abs().max())),
            avg_abs_effect_residual=("effect_residual", lambda s: float(s.abs().mean())),
        )
        .reset_index()
    )
    reconcile_lines = []
    for row in reconciliation_summary.to_dict("records"):
        reconcile_lines.append(
            "- `{source}`: reconciled_periods={n}, avg_gross_minus_nav_net={avg}, max_abs={max_abs}".format(
                source=row["source_id"],
                n=row["n_reconciled"],
                avg=_pct(row["avg_gross_minus_nav_net"]),
                max_abs=_pct(row["max_abs_gross_minus_nav_net"]),
            )
        )
    residual_lines = []
    for row in residual_summary.to_dict("records"):
        residual_lines.append(
            "- `{source}`: max_abs_effect_residual={max_abs}, avg_abs_effect_residual={avg_abs}".format(
                source=row["source_id"],
                max_abs=_pct(row["max_abs_effect_residual"]),
                avg_abs=_pct(row["avg_abs_effect_residual"]),
            )
        )

    lines = [
        "# 阶段 7/8：收益归因与显著性审查",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 正式测试集：未运行。",
        "- Pipeline B 使用冻结 replay：`check/0526/tmp/stage2/pipeline_b_lam0050_replayed/`。",
        "- rolling-48m 使用冻结 replay：`check/0526/tmp/stage2/rolling_48m_replayed/`。",
        "- rolling-36m/60m 仅有 `experiments/ridge_rolling/results/` 当前实验产物，因此只用于多窗口披露，不作为冻结证据。",
        "",
        "## 问题 A：Pipeline B 39.1% 月胜率的原因",
        "",
        *payoff_lines,
        "",
        "直接解释：Pipeline B 的 23 个有效月中只有 9 个正超额月，但正超额月平均幅度约为负超额月绝对幅度的 2 倍；因此低胜率与正净超额可以同时成立。这不是月胜率公式错误。",
        "",
        "年度拆解：",
        "",
        *yearly_lines,
        "",
        "市场状态拆解：",
        "",
        *state_lines,
        "",
        "## 问题 B：rolling-48m 弱 IC 但组合 IR 更高的解释",
        "",
        "rolling 相对 Pipeline B 贡献最大的月份：",
        "",
        *_top_lines(top_month_diff, "rolling_minus_pipeline_b", n=5, ascending=False),
        "",
        "rolling 相对 Pipeline B 总效应改善最大的行业：",
        "",
        *_top_lines(top_industry_diff, "rolling_minus_pipeline_b_total_effect", n=5, ascending=False),
        "",
        "rolling 相对 Pipeline B 主动贡献改善最大的股票：",
        "",
        *_top_lines(top_stock_diff, "rolling_minus_pipeline_b_active_contribution", n=5, ascending=False),
        "",
        "归因口径校验（Brinson/个股贡献为 T+1 开盘到下期收盘的股票端 gross 近似，NAV 为扣成本 net）：",
        "",
        *reconcile_lines,
        "",
        "Brinson 分项残差提示：行业 `total_effect` 与组合超额收益闭合；但组合存在少量现金/锁定权重，`allocation + selection + interaction` 与 `total_effect` 有小残差，因此本报告只用 `total_effect` 做行业贡献主证据。",
        "",
        *residual_lines,
        "",
        "阶段 7 判定：rolling 的优势集中在少数月份和若干行业/个股贡献上；阶段 5/6 未发现明显未来函数、成本更低或约束更松的证据，因此更合理的解释是验证期路径下的持仓差异被组合优化放大，但稳健性仍需按阶段 8 处理。",
        "",
        "## 阶段 8：显著性",
        "",
        "- 配对月度差值 rolling - PipelineB：mean={mean}, sum={sum_diff}, rolling_better_months={better}, t={t}, p={p}, Wilcoxon p={wp}。".format(
            mean=_pct(p_row.get("mean_monthly_diff")),
            sum_diff=_pct(p_row.get("sum_diff")),
            better=p_row.get("rolling_better_months", "N/A"),
            t=_fmt(p_row.get("paired_t_stat")),
            p=_fmt(p_row.get("paired_t_p_value")),
            wp=_fmt(p_row.get("wilcoxon_p_value")),
        ),
        "- 普通 bootstrap 月度 IR 差 95% CI：[{lo}, {hi}]，P(diff>0)={prob}。".format(
            lo=_fmt(boot_ir.get("ci_2p5")),
            hi=_fmt(boot_ir.get("ci_97p5")),
            prob=_pct(boot_ir.get("prob_gt_0")),
        ),
        "- 3 个月 block bootstrap 月均差 95% CI：[{lo}, {hi}]，P(diff>0)={prob}。".format(
            lo=_pct(block_mean.get("ci_2p5")),
            hi=_pct(block_mean.get("ci_97p5")),
            prob=_pct(block_mean.get("prob_gt_0")),
        ),
        "- 普通 bootstrap 月均差 95% CI：[{lo}, {hi}]，P(diff>0)={prob}。".format(
            lo=_pct(boot_mean.get("ci_2p5")),
            hi=_pct(boot_mean.get("ci_97p5")),
            prob=_pct(boot_mean.get("prob_gt_0")),
        ),
        "- 3 个月 block bootstrap 月度 IR 差 95% CI：[{lo}, {hi}]，P(diff>0)={prob}。".format(
            lo=_fmt(block_ir.get("ci_2p5")),
            hi=_fmt(block_ir.get("ci_97p5")),
            prob=_pct(block_ir.get("prob_gt_0")),
        ),
        "",
        "多窗口披露：",
        "",
        *(multi_lines if multi_lines else ["- 无 rolling-36m/60m 可用产物。"]),
        "",
        "阶段 8 判定：验证期只有 23 个有效月，rolling-48m 的组合 IR 点估计更高，但配对月度差异与 bootstrap 区间不足以支持“稳定显著优于 Pipeline B”的强结论；同时 48m 是多个窗口中事后最好者，必须披露多重比较风险。",
    ]
    (OUT_DIR / "stage7_stage8_problem_audit.md").write_text("\n".join(lines), encoding="utf-8")

    root_lines = [
        "# root_cause_report：0526 两个问题的阶段审查结论",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 审查范围：训练期与验证期；正式测试集 2023-2025 未运行。",
        "- 关键限制：旧 Pipeline B 使用冻结 replay；当前正式 `experiments/` 产物已发生漂移，因此不能再用当前 lam_0050 代表旧问题。",
        "",
        "## 问题一：Pipeline B 月度胜率 39.1% 是否真实",
        "",
        "- 结论：真实复现，不是 metrics bug。",
        "- 证据：`stage6_payoff_summary.csv`、`stage7_monthly_state_decomposition.csv`、`stage7_stage8_problem_audit.md`。",
        "- 原因：23 个有效月中 9 个正超额、14 个负超额；正超额月平均幅度约 1.75%，负超额月平均约 -0.87%，payoff ratio 约 2.01。",
        "- 分类：真实收益分布 + 样本有限；不是基准口径错误、同权重回测入口错误或月胜率复算错误。",
        "",
        "## 问题二：rolling-48m 弱 IC 但组合 IR 更强是否真实",
        "",
        "- 结论：组合层点估计真实复现，但不能升级为稳定改进结论。",
        "- 已排除：阶段 3/5 未发现全收益基准错误、训练窗口穿越预测日、CV 窗口穿越或 shift test 异常；阶段 6 未发现 rolling 依靠更低成本、更宽约束或非 T+1 执行取得优势。",
        "- 主要解释：rolling 与 Pipeline B 的信号排序差异较大，优化后持仓路径不同；验证期优势集中在少数月份、行业和个股主动贡献上，属于组合优化放大 + 样本路径效应。",
        "- 归因限制：行业 `total_effect` 与组合超额收益闭合；Brinson 三分项存在少量现金/锁定权重残差，不能过度解释 allocation/selection/interaction 的细分值。",
        "- 统计纪律：23 个有效月下，rolling - PipelineB 的配对月度差异 p 值不支持强显著；48m 又是 36/48/60 多窗口比较中的事后最优，存在选择偏差风险。",
        "- 分类：不是当前证据下的未来函数；不是交易成本/约束不对称；更接近“优化器放大 + 验证期样本噪声/路径效应”。",
        "",
        "## 下一步建议",
        "",
        "1. 不要直接把 rolling-48m 替换为主管线信号。",
        "2. 先恢复/固定产物版本，消除 `experiments/` 漂移；之后统一重跑 expanding、36m、48m、60m，禁止只保留最优窗口。",
        "3. 若继续研究 rolling，应在验证期内预注册窗口选择规则，再用最多 3 次正式测试集纪律做最终评估。",
    ]
    (OUT_DIR / "root_cause_report.md").write_text("\n".join(root_lines), encoding="utf-8")

    log_lines = [
        "# 2026-05-27 阶段 7/8 执行日志",
        "",
        "- 运行 `check/0526/run_stage7_stage8.py`。",
        "- 输出收益分布、年度/市场状态拆解、Brinson 行业归因、个股贡献近似、配对检验、bootstrap 和多窗口比较。",
        "- 正式测试集：未运行。",
        "- 关键报告：`stage7_stage8_problem_audit.md`、`root_cause_report.md`。",
    ]
    (OUT_DIR / "stage7_stage8_log.md").write_text("\n".join(log_lines), encoding="utf-8")

    audit_log = OUT_DIR / "audit_work_log.md"
    if audit_log.exists():
        append_lines = [
            "",
            "## 阶段 7/8：收益归因与显著性审查",
            "",
            f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "- 运行脚本：`check/0526/run_stage7_stage8.py`。",
            "- 关键报告：`stage7_stage8_problem_audit.md`、`root_cause_report.md`。",
            "- 结论摘要：Pipeline B 39.1% 月胜率真实，直接原因是正月少但正月幅度约为负月 2 倍；rolling-48m 组合 IR 点估计真实，但优势集中且统计显著性不足，不能直接定为稳定改进。",
            "- 正式测试集：未运行。",
        ]
        with audit_log.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(append_lines))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stage7 = run_stage7()
    stage8 = run_stage8(stage7["monthly"])
    write_reports(stage7, stage8)
    print(f"Wrote {OUT_DIR / 'stage7_stage8_problem_audit.md'}")
    print(f"Wrote {OUT_DIR / 'root_cause_report.md'}")


if __name__ == "__main__":
    main()
