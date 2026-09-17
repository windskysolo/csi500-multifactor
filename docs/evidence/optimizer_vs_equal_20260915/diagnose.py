"""Read-only validation-period diagnostics for QP versus TopN portfolios.

This script only reads ``runs/train_valid`` and the frozen Rolling-48 signal.
It deliberately excludes ``runs/test`` and does not rerun any strategy stage.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
RUN_ROOT = ROOT / "runs" / "train_valid"
SOURCE_RUN = "20260527_142056__challenger_rolling48_te6_lam0050"

RUNS = {
    "qp_full": SOURCE_RUN,
    "topn50_forced": "20260529_081232__rolling48_ridge_topn50_ew",
    "qp_prefilter_zero": "20260530_105347__rolling48_prefilter_zero_qp",
    "qp_prefilter_lock": "20260530_105350__rolling48_prefilter_lock_qp",
    "topn75": "20260530_111126__rolling48_topn75_ew",
    "topn100": "20260530_111128__rolling48_topn100_ew",
    "topn150": "20260530_111129__rolling48_topn150_ew",
    "topn200": "20260530_111809__rolling48_topn200_ew",
    "topn250": "20260530_111810__rolling48_topn250_ew",
    "topn300": "20260530_111811__rolling48_topn300_ew",
}


def _benchmark_on(date: pd.Timestamp, columns: pd.Index) -> pd.Series:
    members = pd.read_parquet(ROOT / "data" / "processed" / "index_member.parquet")
    weights = members.loc[date, "index_weight"].astype("float64")
    weights = weights / weights.sum()
    return weights.reindex(columns).fillna(0.0)


def _metric(run_id: str) -> dict[str, float]:
    frame = pd.read_parquet(RUN_ROOT / run_id / "backtest" / "metrics_valid.parquet")
    series = frame["v2"]
    return {
        key: float(series.loc[key])
        for key in [
            "excess_return",
            "tracking_error",
            "information_ratio",
            "excess_max_drawdown",
            "monthly_win_rate",
        ]
    }


def _portfolio_stats(run_id: str, signal: pd.DataFrame) -> dict[str, float]:
    weights = pd.read_parquet(RUN_ROOT / run_id / "portfolio" / "target_weights.parquet")
    dates = weights.index.intersection(signal.index)
    dates = dates[(dates >= pd.Timestamp("2021-01-01")) & (dates <= pd.Timestamp("2022-12-31"))]

    rows: list[dict[str, float]] = []
    for date in dates:
        w = weights.loc[date].astype("float64")
        alpha = signal.loc[date].reindex(w.index).astype("float64").fillna(0.0)
        benchmark = _benchmark_on(date, w.index)
        positive = w[w > 1e-10]
        active = w - benchmark
        rows.append(
            {
                "holdings": float(len(positive)),
                "effective_holdings": float(1.0 / np.square(w).sum()),
                "max_weight": float(w.max()),
                "top10_weight": float(w.nlargest(10).sum()),
                "active_share": float(0.5 * active.abs().sum()),
                "raw_alpha_exposure": float((w * alpha).sum()),
                "active_alpha_exposure": float((active * alpha).sum()),
            }
        )

    trades = pd.read_parquet(RUN_ROOT / run_id / "backtest" / "trades_valid.parquet")
    trade_stats = {
        "annualized_one_way_turnover": float(
            ((trades["sell_value"] + trades["buy_value"]) / trades["portfolio_value_before"] / 2).mean() * 12
        ),
        "total_cost_over_period": float((trades["cost"] / trades["portfolio_value_before"]).sum()),
    }
    means = pd.DataFrame(rows).mean().to_dict()
    return {**{key: float(value) for key, value in means.items()}, **trade_stats}


def main() -> None:
    signal = pd.read_parquet(RUN_ROOT / SOURCE_RUN / "signal" / "composite.parquet")
    result: dict[str, object] = {
        "scope": "validation only: 2021-01-01 through 2022-12-31",
        "warning": "Historical runs are not a fully controlled same-code experiment.",
        "runs": {},
    }
    for label, run_id in RUNS.items():
        result["runs"][label] = {
            "run_id": run_id,
            "metrics": _metric(run_id),
            "portfolio": _portfolio_stats(run_id, signal),
        }

    original = RUN_ROOT / SOURCE_RUN / "portfolio"
    baseline = pd.read_parquet(original / "baseline_weights.parquet")
    target = pd.read_parquet(original / "target_weights.parquet")
    common_dates = baseline.index.intersection(target.index)
    common_dates = common_dates[(common_dates >= "2021-01-01") & (common_dates <= "2022-12-31")]
    result["original_run_weight_difference"] = {
        "mean_l1_distance": float((baseline.loc[common_dates] - target.loc[common_dates]).abs().sum(axis=1).mean()),
        "mean_baseline_holdings": float((baseline.loc[common_dates] > 1e-10).sum(axis=1).mean()),
        "mean_qp_holdings": float((target.loc[common_dates] > 1e-10).sum(axis=1).mean()),
    }

    # A rank-bucket diagnostic tests whether the largest score magnitudes were
    # actually the strongest part of the signal. This is descriptive only: the
    # forward-return panel is not a substitute for the executable backtest.
    forward = pd.read_parquet(ROOT / "data" / "processed" / "fwd_ret_panel.parquet")
    members = pd.read_parquet(ROOT / "data" / "processed" / "index_member.parquet")
    rank_groups = {
        "rank_1_25": (0, 25),
        "rank_26_50": (25, 50),
        "rank_51_75": (50, 75),
        "rank_76_100": (75, 100),
        "rank_101_150": (100, 150),
        "rank_151_250": (150, 250),
        "rank_251_500": (250, 500),
    }
    grouped_returns: dict[str, list[float]] = {key: [] for key in rank_groups}
    ic_values: list[float] = []
    rank_dates = signal.index.intersection(forward.index)
    rank_dates = rank_dates[(rank_dates >= "2021-01-01") & (rank_dates <= "2022-12-31")]
    for date in rank_dates:
        universe = members.loc[date].index
        alpha = signal.loc[date].reindex(universe).dropna().sort_values(ascending=False)
        future_return = forward.loc[date].reindex(alpha.index)
        valid = future_return.notna()
        ic_values.append(float(alpha[valid].corr(future_return[valid], method="spearman")))
        for label, (start, stop) in rank_groups.items():
            grouped_returns[label].append(float(future_return.reindex(alpha.index[start:stop]).mean()))
    result["rank_bucket_diagnostic"] = {
        "n_dates": len(rank_dates),
        "mean_spearman_ic": float(np.nanmean(ic_values)),
        "ic_ir_monthly": float(np.nanmean(ic_values) / np.nanstd(ic_values, ddof=1)),
        "mean_monthly_forward_return": {
            label: float(np.nanmean(values)) for label, values in grouped_returns.items()
        },
        "caveat": "Raw equal-weight forward returns; descriptive signal-shape evidence, not executable excess returns.",
    }

    output = Path(__file__).with_name("diagnosis_summary.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
