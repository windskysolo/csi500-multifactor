"""
Stage 2b/4 audit for check/0526.

The script is read-only for project research artifacts. It writes only under
check/0526/ and check/0526/tmp/.

Stage 2b:
  - rebuild expanding Ridge + lambda=0.005 multiple times with current optimizer
  - compare rebuilds with each other and with the frozen Pipeline B artifact
  - export per-period static input hashes and solver/fallback summaries

Stage 4:
  - audit signal panel coverage, cross-sectional distribution, Top50 overlap,
    rank correlation, signal turnover, optimized target weight turnover, and
    per-date/per-period IC details for IC_IR, expanding Ridge, and rolling-48m

Formal test set is not used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
TMP_DIR = OUT_DIR / "tmp"
STAGE2B_TMP = TMP_DIR / "stage2b"

sys.path.insert(0, str(ROOT))

from src import config as cfg  # noqa: E402
from src.backtest.engine import BacktestConfig, run_backtest  # noqa: E402
from src.data.loader import load_daily_basic  # noqa: E402
from experiments.turnover_lambda_grid.run_turnover_lambda_grid import (  # noqa: E402
    build_period_data as build_turnover_period_data,
    build_weights_for_lambda,
    load_data as load_turnover_data,
)


LAM_BEST = 0.005
EPS = 1e-10
MATERIAL_NAV_EPS = 1e-8
MATERIAL_WEIGHT_EPS = 1e-6
TOP_N = 50

PIPELINE_B_DIR = ROOT / "experiments" / "turnover_lambda_grid" / "results" / "lam_0050"
ROLLING_48M_DIR = ROOT / "experiments" / "ridge_rolling" / "results" / "rolling_48m"

SIGNAL_SPECS: dict[str, Path] = {
    "ic_ir": cfg.DATA_PROC / "composite_signal_ic_ir.parquet",
    "expanding_ridge": ROOT / "experiments" / "ridge_signal" / "results" / "ridge_composite_panel.parquet",
    "rolling_48m": ROOT / "experiments" / "ridge_rolling" / "results" / "rolling_48m_composite_panel.parquet",
}

WEIGHT_SPECS: dict[str, Path] = {
    "expanding_ridge": PIPELINE_B_DIR / "weights_optimized.parquet",
    "rolling_48m": ROLLING_48M_DIR / "weights_optimized.parquet",
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


def _as_nav_df(result) -> pd.DataFrame:
    nav = pd.DataFrame(
        {
            "strategy": result.nav,
            "benchmark": result.benchmark_nav,
        }
    ).dropna()
    nav.index.name = "trade_date"
    return nav


def _series_hash(series: pd.Series) -> str:
    s = series.copy()
    s.index = s.index.astype(str)
    s = s.sort_index()
    hashed = pd.util.hash_pandas_object(s, index=True).values.tobytes()
    return hashlib.sha256(hashed).hexdigest()[:16]


def _array_hash(arr: np.ndarray) -> str:
    a = np.ascontiguousarray(arr)
    h = hashlib.sha256()
    h.update(str(a.shape).encode("ascii"))
    h.update(a.tobytes())
    return h.hexdigest()[:16]


def _set_hash(values: set[str] | set[Any]) -> str:
    payload = json.dumps(sorted(str(v) for v in values), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _dict_hash(values: dict[str, str]) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _frame_aligned_abs_diff(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    old2 = old.copy()
    new2 = new.copy()
    old2.index = pd.to_datetime(old2.index)
    new2.index = pd.to_datetime(new2.index)
    old2.columns = old2.columns.astype(str)
    new2.columns = new2.columns.astype(str)
    idx = old2.index.union(new2.index)
    cols = old2.columns.union(new2.columns)
    return (
        new2.reindex(index=idx, columns=cols).fillna(0.0)
        - old2.reindex(index=idx, columns=cols).fillna(0.0)
    ).abs().sort_index()


def _metric_dict_to_frame(metrics: dict[str, float]) -> pd.DataFrame:
    return pd.Series(metrics, name="value").to_frame()


def _read_metrics(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    df = pd.read_parquet(path)
    if len(df.columns) == 1:
        raw = df.iloc[:, 0]
    else:
        raw = df.iloc[:, 0]
    result: dict[str, float] = {}
    for key, value in raw.items():
        try:
            result[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def _metric_diff_row(
    run_id: str,
    comparison: str,
    old_metrics: dict[str, float],
    new_metrics: dict[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(set(old_metrics) | set(new_metrics)):
        old_val = old_metrics.get(key, float("nan"))
        new_val = new_metrics.get(key, float("nan"))
        rows.append(
            {
                "run_id": run_id,
                "comparison": comparison,
                "metric": key,
                "old_value": old_val,
                "new_value": new_val,
                "diff": (
                    new_val - old_val
                    if pd.notna(old_val) and pd.notna(new_val)
                    else float("nan")
                ),
            }
        )
    return rows


def _nav_diff_summary(
    run_id: str,
    comparison: str,
    old_nav: pd.DataFrame,
    new_nav: pd.DataFrame,
) -> dict[str, Any]:
    old = _to_dt_index(old_nav)[["strategy", "benchmark"]].dropna()
    new = _to_dt_index(new_nav)[["strategy", "benchmark"]].dropna()
    old_a, new_a = old.align(new, join="inner", axis=0)
    diff_strategy = new_a["strategy"] - old_a["strategy"]
    diff_benchmark = new_a["benchmark"] - old_a["benchmark"]
    diff_excess = new_a["strategy"] / new_a["benchmark"] - old_a["strategy"] / old_a["benchmark"]
    abs_max = pd.concat(
        [diff_strategy.abs(), diff_benchmark.abs(), diff_excess.abs()],
        axis=1,
    ).max(axis=1)
    return {
        "run_id": run_id,
        "comparison": comparison,
        "aligned_rows": int(len(old_a)),
        "max_abs_strategy_diff": float(diff_strategy.abs().max()) if len(diff_strategy) else float("nan"),
        "max_abs_benchmark_diff": float(diff_benchmark.abs().max()) if len(diff_benchmark) else float("nan"),
        "max_abs_excess_nav_diff": float(diff_excess.abs().max()) if len(diff_excess) else float("nan"),
        "first_diff_date": (
            abs_max.index[abs_max > EPS][0].strftime("%Y-%m-%d")
            if (abs_max > EPS).any()
            else ""
        ),
    }


def _weight_diff_summary(
    run_id: str,
    comparison: str,
    old_weights: pd.DataFrame,
    new_weights: pd.DataFrame,
) -> dict[str, Any]:
    diff = _frame_aligned_abs_diff(old_weights, new_weights)
    per_date = diff.max(axis=1) if not diff.empty else pd.Series(dtype=float)
    return {
        "run_id": run_id,
        "comparison": comparison,
        "aligned_shape": f"{diff.shape[0]}x{diff.shape[1]}",
        "max_abs_weight_diff": float(diff.max().max()) if not diff.empty else float("nan"),
        "mean_abs_weight_diff": float(diff.stack().mean()) if not diff.empty else float("nan"),
        "nonzero_cells_gt_1e_10": int((diff > EPS).sum().sum()) if not diff.empty else 0,
        "dates_with_diff_gt_1e_10": int((per_date > EPS).sum()) if not per_date.empty else 0,
        "dates_with_material_diff_gt_1e_6": int((per_date > MATERIAL_WEIGHT_EPS).sum()) if not per_date.empty else 0,
        "first_diff_date": per_date.index[per_date > EPS][0].strftime("%Y-%m-%d") if (per_date > EPS).any() else "",
        "first_material_diff_date": (
            per_date.index[per_date > MATERIAL_WEIGHT_EPS][0].strftime("%Y-%m-%d")
            if (per_date > MATERIAL_WEIGHT_EPS).any()
            else ""
        ),
    }


def build_static_input_hashes() -> pd.DataFrame:
    ridge_signal, index_member, industry_pivot = load_turnover_data()
    (
        available_dates,
        benchmark_weights_dict,
        halt_dict,
        limit_up_dict,
        limit_dn_dict,
        industry_dict,
        codes_map,
        cov_cache,
    ) = build_turnover_period_data(ridge_signal, index_member, industry_pivot)

    tv_signal = ridge_signal.loc[ridge_signal.index <= cfg.VALID_END]
    rows: list[dict[str, Any]] = []
    for T in available_dates:
        codes = list(codes_map[T])
        alpha = (
            tv_signal.loc[T].reindex(codes).fillna(0.0)
            if T in tv_signal.index
            else pd.Series(0.0, index=codes)
        )
        industry_map = industry_dict[T].reindex(codes).fillna("").astype(str).to_dict()
        hashes = {
            "alpha_hash": _series_hash(alpha),
            "benchmark_weight_hash": _series_hash(benchmark_weights_dict[T].reindex(codes).fillna(0.0)),
            "cov_hash": _array_hash(np.asarray(cov_cache[T])),
            "industry_hash": _dict_hash(industry_map),
            "halt_hash": _set_hash(halt_dict[T]),
            "limit_up_hash": _set_hash(limit_up_dict[T]),
            "limit_dn_hash": _set_hash(limit_dn_dict[T]),
        }
        combined = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        rows.append(
            {
                "rebalance_date": pd.Timestamp(T),
                "n_codes": len(codes),
                "has_signal": bool(T in tv_signal.index),
                "static_input_hash": combined,
                **hashes,
            }
        )
    return pd.DataFrame(rows)


def rebuild_expanding_once(run_idx: int) -> dict[str, Any]:
    run_id = f"rebuild_{run_idx:02d}"
    out = STAGE2B_TMP / run_id
    out.mkdir(parents=True, exist_ok=True)

    ridge_signal, index_member, industry_pivot = load_turnover_data()
    (
        available_dates,
        benchmark_weights_dict,
        halt_dict,
        limit_up_dict,
        limit_dn_dict,
        industry_dict,
        codes_map,
        cov_cache,
    ) = build_turnover_period_data(ridge_signal, index_member, industry_pivot)

    weights, baseline, meta = build_weights_for_lambda(
        LAM_BEST,
        ridge_signal,
        available_dates,
        benchmark_weights_dict,
        halt_dict,
        limit_up_dict,
        limit_dn_dict,
        industry_dict,
        codes_map,
        cov_cache,
    )
    result = run_backtest(weights, cfg.VALID_START, cfg.VALID_END, BacktestConfig(initial_value=1.0))
    nav = _as_nav_df(result)

    paths = {
        "weights": out / "weights_optimized.parquet",
        "baseline": out / "weights_baseline.parquet",
        "meta": out / "weights_meta.parquet",
        "nav": out / "backtest_nav_valid.parquet",
        "metrics": out / "backtest_metrics_valid.parquet",
        "trades": out / "trades_valid.parquet",
        "actual_weights": out / "actual_weights_valid.parquet",
    }
    weights.to_parquet(paths["weights"])
    baseline.to_parquet(paths["baseline"])
    meta.to_parquet(paths["meta"])
    nav.to_parquet(paths["nav"])
    _metric_dict_to_frame(result.metrics).to_parquet(paths["metrics"])
    if not result.trade_log.empty:
        result.trade_log.to_parquet(paths["trades"])
    if not result.actual_weights.empty:
        result.actual_weights.to_parquet(paths["actual_weights"])

    return {
        "run_id": run_id,
        "weights": weights,
        "meta": meta,
        "nav": nav,
        "metrics": result.metrics,
        "paths": paths,
    }


def run_stage2b(n_rebuilds: int) -> dict[str, Any]:
    STAGE2B_TMP.mkdir(parents=True, exist_ok=True)
    np.random.seed(cfg.RANDOM_SEED)

    input_hashes = build_static_input_hashes()
    input_hashes.to_csv(OUT_DIR / "stage2b_static_input_hash.csv", index=False, encoding="utf-8-sig")

    runs = [rebuild_expanding_once(i) for i in range(1, n_rebuilds + 1)]

    existing_weights = _to_dt_index(pd.read_parquet(PIPELINE_B_DIR / "weights_optimized.parquet"))
    existing_nav = _to_dt_index(pd.read_parquet(PIPELINE_B_DIR / "backtest_nav_valid.parquet"))
    existing_metrics = _read_metrics(PIPELINE_B_DIR / "backtest_metrics_valid.parquet")

    run_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []
    weight_rows: list[dict[str, Any]] = []
    meta_rows: list[dict[str, Any]] = []

    for run in runs:
        metrics = run["metrics"]
        meta = run["meta"]
        status_counts = meta["solver_status"].astype(str).value_counts(dropna=False).to_dict() if "solver_status" in meta else {}
        fallback_counts = meta["fallback_level"].value_counts(dropna=False).to_dict() if "fallback_level" in meta else {}
        run_rows.append(
            {
                "run_id": run["run_id"],
                "weights_path": _rel(run["paths"]["weights"]),
                "nav_path": _rel(run["paths"]["nav"]),
                "information_ratio": metrics.get("information_ratio", float("nan")),
                "excess_return": metrics.get("excess_return", float("nan")),
                "monthly_win_rate": metrics.get("monthly_win_rate", float("nan")),
                "tracking_error": metrics.get("tracking_error", float("nan")),
                "excess_max_drawdown": metrics.get("excess_max_drawdown", float("nan")),
                "fallback_counts": json.dumps({str(k): int(v) for k, v in fallback_counts.items()}, ensure_ascii=False),
                "solver_status_counts": json.dumps({str(k): int(v) for k, v in status_counts.items()}, ensure_ascii=False),
                "solve_time_sum_s": float(meta["solve_time_s"].sum()) if "solve_time_s" in meta else float("nan"),
                "solve_time_mean_s": float(meta["solve_time_s"].mean()) if "solve_time_s" in meta else float("nan"),
            }
        )
        metric_rows.extend(
            _metric_diff_row(run["run_id"], "current_rebuild_vs_existing_pipeline_b", existing_metrics, metrics)
        )
        nav_rows.append(
            _nav_diff_summary(run["run_id"], "current_rebuild_vs_existing_pipeline_b", existing_nav, run["nav"])
        )
        weight_rows.append(
            _weight_diff_summary(run["run_id"], "current_rebuild_vs_existing_pipeline_b", existing_weights, run["weights"])
        )
        meta_copy = meta.copy()
        meta_copy.index = pd.to_datetime(meta_copy.index)
        meta_copy = meta_copy.reset_index().rename(columns={"index": "rebalance_date"})
        meta_copy.insert(0, "run_id", run["run_id"])
        meta_rows.extend(meta_copy.to_dict("records"))

    base = runs[0]
    for run in runs[1:]:
        metric_rows.extend(
            _metric_diff_row(run["run_id"], f"{run['run_id']}_vs_{base['run_id']}", base["metrics"], run["metrics"])
        )
        nav_rows.append(
            _nav_diff_summary(run["run_id"], f"{run['run_id']}_vs_{base['run_id']}", base["nav"], run["nav"])
        )
        weight_rows.append(
            _weight_diff_summary(run["run_id"], f"{run['run_id']}_vs_{base['run_id']}", base["weights"], run["weights"])
        )

    run_df = pd.DataFrame(run_rows)
    metric_df = pd.DataFrame(metric_rows)
    nav_df = pd.DataFrame(nav_rows)
    weight_df = pd.DataFrame(weight_rows)
    meta_df = pd.DataFrame(meta_rows)

    run_df.to_csv(OUT_DIR / "stage2b_rebuild_runs.csv", index=False, encoding="utf-8-sig")
    metric_df.to_csv(OUT_DIR / "stage2b_metric_diff.csv", index=False, encoding="utf-8-sig")
    nav_df.to_csv(OUT_DIR / "stage2b_nav_diff.csv", index=False, encoding="utf-8-sig")
    weight_df.to_csv(OUT_DIR / "stage2b_weight_diff.csv", index=False, encoding="utf-8-sig")
    meta_df.to_csv(OUT_DIR / "stage2b_solver_meta.csv", index=False, encoding="utf-8-sig")

    return {
        "runs": run_df,
        "metric_diff": metric_df,
        "nav_diff": nav_df,
        "weight_diff": weight_df,
        "solver_meta": meta_df,
        "input_hash": input_hashes,
    }


def _load_signals() -> dict[str, pd.DataFrame]:
    signals: dict[str, pd.DataFrame] = {}
    for name, path in SIGNAL_SPECS.items():
        df = _to_dt_index(pd.read_parquet(path))
        df.columns = df.columns.astype(str)
        signals[name] = df
    return signals


def _load_index_member() -> pd.DataFrame:
    index_member = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    index_member.index = index_member.index.set_levels(
        [pd.to_datetime(index_member.index.levels[0]), index_member.index.levels[1]],
        level=[0, 1],
    )
    return index_member


def _load_industry_pivot() -> pd.DataFrame:
    industry_raw = pd.read_parquet(cfg.DATA_PROC / "industry.parquet")
    industry_raw.index = industry_raw.index.set_levels(
        [pd.to_datetime(industry_raw.index.levels[0]), industry_raw.index.levels[1]],
        level=[0, 1],
    )
    return (
        industry_raw["industry_code"]
        .unstack(level="ts_code")
        .sort_index()
        .ffill()
    )


def _industry_snapshot(industry_pivot: pd.DataFrame, T: pd.Timestamp) -> pd.Series:
    dates = industry_pivot.index[industry_pivot.index <= T]
    if len(dates) == 0:
        return pd.Series(dtype=object)
    return industry_pivot.loc[dates[-1]].dropna()


def _period_label(T: pd.Timestamp) -> str:
    if T <= cfg.TRAIN_END:
        return "train"
    if cfg.VALID_START <= T <= cfg.VALID_END:
        return "valid"
    if cfg.TEST_START <= T <= cfg.TEST_END:
        return "test"
    return "other"


def compute_ic_detail(signal_name: str, signal: pd.DataFrame, fwd: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    common_dates = signal.index.intersection(fwd.index)
    for T in common_dates:
        sig_t = signal.loc[T]
        fwd_t = fwd.loc[T].reindex(sig_t.index)
        mask = sig_t.notna() & fwd_t.notna()
        n_obs = int(mask.sum())
        ic = float(sig_t[mask].corr(fwd_t[mask], method="spearman")) if n_obs >= 10 else float("nan")
        rows.append(
            {
                "source_id": signal_name,
                "rebalance_date": pd.Timestamp(T),
                "period": _period_label(pd.Timestamp(T)),
                "year": pd.Timestamp(T).year,
                "n_obs": n_obs,
                "ic": ic,
            }
        )
    return pd.DataFrame(rows)


def _ic_stats(values: pd.Series, source_id: str, period: str) -> dict[str, Any]:
    valid = values.dropna()
    n = int(len(valid))
    if n < 3:
        return {
            "source_id": source_id,
            "period": period,
            "n": n,
            "ic_mean": float("nan"),
            "ic_std": float("nan"),
            "ic_ir": float("nan"),
            "positive_count": int((valid > 0).sum()),
            "positive_rate": float("nan"),
            "t_stat": float("nan"),
            "p_value": float("nan"),
        }
    mean = float(valid.mean())
    std = float(valid.std(ddof=1))
    t_stat = mean / (std / math.sqrt(n)) if std > 1e-12 else float("nan")
    p_value = float(2 * scipy_stats.t.sf(abs(t_stat), df=n - 1)) if pd.notna(t_stat) else float("nan")
    return {
        "source_id": source_id,
        "period": period,
        "n": n,
        "ic_mean": mean,
        "ic_std": std,
        "ic_ir": mean / std if std > 1e-12 else float("nan"),
        "positive_count": int((valid > 0).sum()),
        "positive_rate": float((valid > 0).mean()),
        "t_stat": t_stat,
        "p_value": p_value,
    }


def summarize_ic(ic_detail: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source_id, grp in ic_detail.groupby("source_id"):
        rows.append(_ic_stats(grp["ic"], source_id, "all_common_fwd"))
        for period in ["train", "valid"]:
            rows.append(_ic_stats(grp.loc[grp["period"] == period, "ic"], source_id, period))
        for year in [2021, 2022]:
            rows.append(_ic_stats(grp.loc[grp["year"] == year, "ic"], source_id, str(year)))
    return pd.DataFrame(rows)


def audit_signal_coverage(
    signals: dict[str, pd.DataFrame],
    index_member: pd.DataFrame,
    industry_pivot: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, set[str]]:
    rows: list[dict[str, Any]] = []
    top_rows: list[dict[str, Any]] = []
    top_code_union: set[str] = set()
    member_dates = set(index_member.index.get_level_values("rebalance_date"))

    for source_id, signal in signals.items():
        for T in signal.index:
            T = pd.Timestamp(T)
            sig = signal.loc[T]
            valid = sig.dropna()
            n_valid = int(len(valid))
            values = valid.astype(float)
            member_codes: set[str] = set()
            universe_count = 0
            overlap_count = 0
            top_overlap_count = 0
            top_weight_sum = float("nan")
            if T in member_dates:
                snap = index_member.loc[T]
                member_codes = set(snap.index.astype(str))
                universe_count = int(len(member_codes))
                valid_codes = set(valid.index.astype(str))
                overlap_count = int(len(valid_codes & member_codes))
            top = values.nlargest(TOP_N) if n_valid else pd.Series(dtype=float)
            top_codes = set(top.index.astype(str))
            top_code_union.update(top_codes)
            if T in member_dates and top_codes:
                top_overlap_count = int(len(top_codes & member_codes))
                snap = index_member.loc[T]
                top_weight_sum = float((snap["index_weight"].reindex(list(top_codes)).fillna(0.0) / 100.0).sum())
            industry = _industry_snapshot(industry_pivot, T)
            top_ind = industry.reindex(list(top_codes)).dropna() if top_codes else pd.Series(dtype=object)
            top_ind_counts = top_ind.value_counts()
            top_rows.append(
                {
                    "source_id": source_id,
                    "rebalance_date": T,
                    "period": _period_label(T),
                    "top_n": int(len(top_codes)),
                    "top_in_universe_count": top_overlap_count,
                    "top_in_universe_ratio": top_overlap_count / len(top_codes) if top_codes else float("nan"),
                    "top_benchmark_weight_sum": top_weight_sum,
                    "top_industry_nunique": int(top_ind.nunique()) if not top_ind.empty else 0,
                    "top_industry_max_share": float(top_ind_counts.iloc[0] / len(top_ind)) if not top_ind.empty else float("nan"),
                    "top_industry_mode": str(top_ind_counts.index[0]) if not top_ind_counts.empty else "",
                }
            )
            rows.append(
                {
                    "source_id": source_id,
                    "rebalance_date": T,
                    "period": _period_label(T),
                    "n_columns": int(signal.shape[1]),
                    "n_non_nan": n_valid,
                    "nan_rate": float(sig.isna().mean()),
                    "universe_count": universe_count,
                    "valid_universe_overlap_count": overlap_count,
                    "valid_universe_overlap_ratio": overlap_count / universe_count if universe_count else float("nan"),
                    "nonuniverse_valid_count": n_valid - overlap_count if universe_count else float("nan"),
                    "mean": float(values.mean()) if n_valid else float("nan"),
                    "std": float(values.std(ddof=1)) if n_valid >= 2 else float("nan"),
                    "skew": float(values.skew()) if n_valid >= 3 else float("nan"),
                    "kurt": float(values.kurt()) if n_valid >= 4 else float("nan"),
                    "min": float(values.min()) if n_valid else float("nan"),
                    "max": float(values.max()) if n_valid else float("nan"),
                    "p01": float(values.quantile(0.01)) if n_valid else float("nan"),
                    "p99": float(values.quantile(0.99)) if n_valid else float("nan"),
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(top_rows), top_code_union


def add_top50_mcap(top_df: pd.DataFrame, top_code_union: set[str]) -> pd.DataFrame:
    if top_df.empty or not top_code_union:
        top_df["top_log_free_float_mv_median"] = np.nan
        return top_df
    dates = pd.to_datetime(top_df["rebalance_date"])
    start = pd.Timestamp(dates.min())
    end = pd.Timestamp(dates.max())
    try:
        basic = load_daily_basic(start, end, codes=sorted(top_code_union))
    except Exception:
        top_df["top_log_free_float_mv_median"] = np.nan
        top_df["top_log_free_float_mv_p90"] = np.nan
        return top_df
    if basic.empty or "log_free_float_mv" not in basic.columns:
        top_df["top_log_free_float_mv_median"] = np.nan
        top_df["top_log_free_float_mv_p90"] = np.nan
        return top_df
    log_mv = basic["log_free_float_mv"].unstack("ts_code").sort_index().ffill()

    signals = _load_signals()
    medians: list[float] = []
    p90s: list[float] = []
    for row in top_df.itertuples(index=False):
        source_id = row.source_id
        T = pd.Timestamp(row.rebalance_date)
        sig = signals[source_id].loc[T].dropna().astype(float)
        top_codes = list(sig.nlargest(TOP_N).index.astype(str))
        mv_dates = log_mv.index[log_mv.index <= T]
        if len(mv_dates) == 0:
            medians.append(float("nan"))
            p90s.append(float("nan"))
            continue
        mv = log_mv.loc[mv_dates[-1]].reindex(top_codes).dropna()
        medians.append(float(mv.median()) if not mv.empty else float("nan"))
        p90s.append(float(mv.quantile(0.90)) if not mv.empty else float("nan"))
    out = top_df.copy()
    out["top_log_free_float_mv_median"] = medians
    out["top_log_free_float_mv_p90"] = p90s
    return out


def summarize_coverage(coverage: pd.DataFrame, top_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source_id, grp in coverage.groupby("source_id"):
        for period in ["all", "train", "valid"]:
            sub = grp if period == "all" else grp[grp["period"] == period]
            top_sub = top_df[top_df["source_id"].eq(source_id)] if period == "all" else top_df[
                top_df["source_id"].eq(source_id) & top_df["period"].eq(period)
            ]
            if sub.empty:
                continue
            rows.append(
                {
                    "source_id": source_id,
                    "period": period,
                    "n_dates": int(len(sub)),
                    "date_min": pd.to_datetime(sub["rebalance_date"]).min().strftime("%Y-%m-%d"),
                    "date_max": pd.to_datetime(sub["rebalance_date"]).max().strftime("%Y-%m-%d"),
                    "avg_non_nan": float(sub["n_non_nan"].mean()),
                    "min_non_nan": int(sub["n_non_nan"].min()),
                    "avg_nan_rate": float(sub["nan_rate"].mean()),
                    "avg_universe_overlap_ratio": float(sub["valid_universe_overlap_ratio"].mean()),
                    "avg_top_in_universe_ratio": float(top_sub["top_in_universe_ratio"].mean()) if not top_sub.empty else float("nan"),
                    "avg_top_industry_nunique": float(top_sub["top_industry_nunique"].mean()) if not top_sub.empty else float("nan"),
                    "avg_top_industry_max_share": float(top_sub["top_industry_max_share"].mean()) if not top_sub.empty else float("nan"),
                    "avg_top_log_free_float_mv_median": float(top_sub["top_log_free_float_mv_median"].mean()) if "top_log_free_float_mv_median" in top_sub else float("nan"),
                    "std_mean": float(sub["mean"].mean()),
                    "avg_cross_section_std": float(sub["std"].mean()),
                    "max_abs_signal": float(max(abs(sub["min"].min()), abs(sub["max"].max()))),
                }
            )
    return pd.DataFrame(rows)


def rank_correlations(signals: dict[str, pd.DataFrame]) -> pd.DataFrame:
    pairs = [
        ("expanding_ridge", "rolling_48m"),
        ("ic_ir", "expanding_ridge"),
        ("ic_ir", "rolling_48m"),
    ]
    rows: list[dict[str, Any]] = []
    for left, right in pairs:
        common_dates = signals[left].index.intersection(signals[right].index)
        for T in common_dates:
            a = signals[left].loc[T]
            b = signals[right].loc[T].reindex(a.index)
            mask = a.notna() & b.notna()
            n = int(mask.sum())
            corr = float(a[mask].corr(b[mask], method="spearman")) if n >= 10 else float("nan")
            rows.append(
                {
                    "left": left,
                    "right": right,
                    "rebalance_date": pd.Timestamp(T),
                    "period": _period_label(pd.Timestamp(T)),
                    "n_obs": n,
                    "spearman_rank_corr": corr,
                }
            )
    return pd.DataFrame(rows)


def signal_turnover(signals: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source_id, signal in signals.items():
        previous_top: set[str] | None = None
        previous_rank: pd.Series | None = None
        for T in signal.index:
            sig = signal.loc[T].dropna().astype(float)
            top_codes = set(sig.nlargest(TOP_N).index.astype(str))
            if previous_top is None:
                top_turnover = float("nan")
                retained = float("nan")
            else:
                retained_count = len(previous_top & top_codes)
                retained = retained_count / TOP_N
                top_turnover = 1.0 - retained
            rank_corr_prev = float("nan")
            if previous_rank is not None:
                common = sig.index.intersection(previous_rank.index)
                if len(common) >= 10:
                    rank_corr_prev = float(sig.reindex(common).corr(previous_rank.reindex(common), method="spearman"))
            rows.append(
                {
                    "source_id": source_id,
                    "rebalance_date": pd.Timestamp(T),
                    "period": _period_label(pd.Timestamp(T)),
                    "top50_retention": retained,
                    "top50_turnover": top_turnover,
                    "rank_corr_vs_prev": rank_corr_prev,
                }
            )
            previous_top = top_codes
            previous_rank = sig.copy()
    return pd.DataFrame(rows)


def target_weight_turnover() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source_id, path in WEIGHT_SPECS.items():
        weights = _to_dt_index(pd.read_parquet(path))
        weights.columns = weights.columns.astype(str)
        prev: pd.Series | None = None
        for T in weights.index:
            w = weights.loc[T].astype(float)
            if prev is None:
                turnover = float("nan")
                active_names = int((w.abs() > EPS).sum())
            else:
                idx = w.index.union(prev.index)
                turnover = float((w.reindex(idx).fillna(0.0) - prev.reindex(idx).fillna(0.0)).abs().sum())
                active_names = int((w.abs() > EPS).sum())
            rows.append(
                {
                    "source_id": source_id,
                    "rebalance_date": pd.Timestamp(T),
                    "period": _period_label(pd.Timestamp(T)),
                    "target_weight_l1_turnover": turnover,
                    "active_names": active_names,
                    "top10_weight_sum": float(w.nlargest(10).sum()),
                    "top50_weight_sum": float(w.nlargest(50).sum()),
                }
            )
            prev = w.copy()
    return pd.DataFrame(rows)


def run_stage4() -> dict[str, pd.DataFrame]:
    signals = _load_signals()
    fwd = _to_dt_index(pd.read_parquet(cfg.DATA_PROC / "fwd_ret_panel.parquet"))
    fwd.columns = fwd.columns.astype(str)
    index_member = _load_index_member()
    industry_pivot = _load_industry_pivot()

    coverage, top_df, top_code_union = audit_signal_coverage(signals, index_member, industry_pivot)
    top_df = add_top50_mcap(top_df, top_code_union)
    summary = summarize_coverage(coverage, top_df)
    corr = rank_correlations(signals)
    sig_to = signal_turnover(signals)
    weight_to = target_weight_turnover()

    ic_detail = pd.concat(
        [compute_ic_detail(name, panel, fwd) for name, panel in signals.items()],
        ignore_index=True,
    )
    ic_summary = summarize_ic(ic_detail)

    coverage.to_csv(OUT_DIR / "stage4_signal_coverage_by_date.csv", index=False, encoding="utf-8-sig")
    top_df.to_csv(OUT_DIR / "stage4_top50_audit.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUT_DIR / "stage4_signal_summary_by_period.csv", index=False, encoding="utf-8-sig")
    corr.to_csv(OUT_DIR / "stage4_rank_correlation.csv", index=False, encoding="utf-8-sig")
    sig_to.to_csv(OUT_DIR / "stage4_signal_turnover.csv", index=False, encoding="utf-8-sig")
    weight_to.to_csv(OUT_DIR / "stage4_target_weight_turnover.csv", index=False, encoding="utf-8-sig")
    ic_detail.to_csv(OUT_DIR / "stage4_ic_detail.csv", index=False, encoding="utf-8-sig")
    ic_summary.to_csv(OUT_DIR / "stage4_ic_summary.csv", index=False, encoding="utf-8-sig")

    return {
        "coverage": coverage,
        "top50": top_df,
        "summary": summary,
        "rank_corr": corr,
        "signal_turnover": sig_to,
        "weight_turnover": weight_to,
        "ic_detail": ic_detail,
        "ic_summary": ic_summary,
    }


def _fmt(value: Any, digits: int = 6) -> str:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if math.isnan(val):
        return "N/A"
    return f"{val:.{digits}g}"


def _period_row(df: pd.DataFrame, source_id: str, period: str) -> dict[str, Any]:
    sub = df[(df["source_id"] == source_id) & (df["period"] == period)]
    return sub.iloc[0].to_dict() if not sub.empty else {}


def write_stage2b_report(result: dict[str, Any]) -> None:
    runs = result["runs"]
    nav = result["nav_diff"]
    weight = result["weight_diff"]
    metrics = result["metric_diff"]
    input_hash = result["input_hash"]

    run_lines = []
    for row in runs.to_dict("records"):
        run_lines.append(
            "- `{run}`: IR={ir}, 年化超额={excess}, 月胜率={win}, TE={te}, solver={solver}, fallback={fallback}".format(
                run=row["run_id"],
                ir=_fmt(row["information_ratio"]),
                excess=_fmt(row["excess_return"]),
                win=_fmt(row["monthly_win_rate"]),
                te=_fmt(row["tracking_error"]),
                solver=row["solver_status_counts"],
                fallback=row["fallback_counts"],
            )
        )

    diff_lines = []
    for row in weight.to_dict("records"):
        diff_lines.append(
            "- `{run}` / {cmp}: max_weight_diff={wd}, material_dates={md}, first_material_date={fd}".format(
                run=row["run_id"],
                cmp=row["comparison"],
                wd=_fmt(row["max_abs_weight_diff"]),
                md=row["dates_with_material_diff_gt_1e_6"],
                fd=row.get("first_material_diff_date", ""),
            )
        )
    for row in nav.to_dict("records"):
        diff_lines.append(
            "- `{run}` / {cmp}: max_strategy_nav_diff={sd}, max_excess_nav_diff={ed}, first_diff={fd}".format(
                run=row["run_id"],
                cmp=row["comparison"],
                sd=_fmt(row["max_abs_strategy_diff"]),
                ed=_fmt(row["max_abs_excess_nav_diff"]),
                fd=row.get("first_diff_date", ""),
            )
        )

    key_metrics = metrics[metrics["metric"].isin(["information_ratio", "excess_return", "monthly_win_rate", "tracking_error"])]
    metric_lines = [
        "- `{run}` / {cmp} / `{metric}`: old={old}, new={new}, diff={diff}".format(
            run=row["run_id"],
            cmp=row["comparison"],
            metric=row["metric"],
            old=_fmt(row["old_value"]),
            new=_fmt(row["new_value"]),
            diff=_fmt(row["diff"]),
        )
        for row in key_metrics.to_dict("records")
    ]

    pairwise = weight[weight["comparison"].str.contains("_vs_rebuild_01", na=False)]
    existing = weight[weight["comparison"].eq("current_rebuild_vs_existing_pipeline_b")]
    pairwise_stable = bool(pairwise.empty or pairwise["max_abs_weight_diff"].max() <= MATERIAL_WEIGHT_EPS)
    existing_match = bool(not existing.empty and existing["max_abs_weight_diff"].max() <= MATERIAL_WEIGHT_EPS)
    if pairwise_stable and existing_match:
        conclusion = "多次当前重建之间稳定，且与旧 Pipeline B 权重一致；阶段 2b 未发现优化器重建问题。"
    elif pairwise_stable:
        conclusion = "多次当前重建之间稳定，但与旧 Pipeline B 权重不一致；优先排查旧产物冻结口径、输入缓存版本或代码变更。"
    else:
        conclusion = "多次当前重建之间也不稳定；优先排查 cvxpy 求解器、warm_start、数值容差和优化后处理。"

    lines = [
        "# 阶段 2b：优化器重建稳定性审查",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 正式测试集：未运行。",
        f"- 重建次数：{len(runs)}。",
        "- 重建产物写入：`check/0526/tmp/stage2b/`。",
        "",
        "## 静态输入哈希",
        "",
        f"- 已导出 `stage2b_static_input_hash.csv`，共 {len(input_hash)} 个调仓日。",
        "- 哈希覆盖 alpha、benchmark weight、covariance、industry、停牌、涨停、跌停状态；不包含动态 `w_prev`。",
        "",
        "## 每次重建结果",
        "",
        *run_lines,
        "",
        "## 权重和 NAV 差异",
        "",
        *diff_lines,
        "",
        "## 关键 metrics 差异",
        "",
        *metric_lines,
        "",
        "## 阶段 2b 判定",
        "",
        f"- {conclusion}",
    ]
    (OUT_DIR / "stage2b_optimizer_stability.md").write_text("\n".join(lines), encoding="utf-8")


def write_stage4_report(result: dict[str, pd.DataFrame]) -> None:
    summary = result["summary"]
    corr = result["rank_corr"]
    sig_to = result["signal_turnover"]
    weight_to = result["weight_turnover"]
    ic_summary = result["ic_summary"]

    summary_lines = []
    for source_id in SIGNAL_SPECS:
        valid = _period_row(summary, source_id, "valid")
        allp = _period_row(summary, source_id, "all")
        summary_lines.append(
            "- `{source}`: dates={dates}, valid avg_non_nan={non_nan}, valid overlap={overlap}, top50 in universe={topu}, avg cs_std={std}".format(
                source=source_id,
                dates=allp.get("n_dates", "N/A"),
                non_nan=_fmt(valid.get("avg_non_nan")),
                overlap=_fmt(valid.get("avg_universe_overlap_ratio")),
                topu=_fmt(valid.get("avg_top_in_universe_ratio")),
                std=_fmt(valid.get("avg_cross_section_std")),
            )
        )

    corr_lines = []
    if not corr.empty:
        for (left, right, period), grp in corr.groupby(["left", "right", "period"]):
            if period != "valid":
                continue
            corr_lines.append(
                f"- `{left}` vs `{right}` validation rank corr: mean={_fmt(grp['spearman_rank_corr'].mean())}, min={_fmt(grp['spearman_rank_corr'].min())}, n={len(grp)}"
            )

    turnover_lines = []
    for source_id in SIGNAL_SPECS:
        valid_to = sig_to[(sig_to["source_id"] == source_id) & (sig_to["period"] == "valid")]
        if valid_to.empty:
            continue
        turnover_lines.append(
            f"- `{source_id}` signal top50 turnover valid mean={_fmt(valid_to['top50_turnover'].mean())}, rank_corr_vs_prev mean={_fmt(valid_to['rank_corr_vs_prev'].mean())}"
        )
    for source_id in WEIGHT_SPECS:
        valid_to = weight_to[(weight_to["source_id"] == source_id) & (weight_to["period"] == "valid")]
        if valid_to.empty:
            continue
        turnover_lines.append(
            f"- `{source_id}` target weight L1 turnover valid mean={_fmt(valid_to['target_weight_l1_turnover'].mean())}, active_names mean={_fmt(valid_to['active_names'].mean())}"
        )

    ic_lines = []
    for source_id in SIGNAL_SPECS:
        for period in ["valid", "2021", "2022"]:
            row = _period_row(ic_summary, source_id, period)
            if not row:
                continue
            ic_lines.append(
                "- `{source}` {period}: n={n}, IC_mean={mean}, IC_std={std}, IC_IR={ir}, positive={pos}, t={t}, p={p}".format(
                    source=source_id,
                    period=period,
                    n=row.get("n"),
                    mean=_fmt(row.get("ic_mean")),
                    std=_fmt(row.get("ic_std")),
                    ir=_fmt(row.get("ic_ir")),
                    pos=_fmt(row.get("positive_rate")),
                    t=_fmt(row.get("t_stat")),
                    p=_fmt(row.get("p_value")),
                )
            )

    valid_summary = summary[summary["period"].eq("valid")]
    coverage_flag = ""
    if not valid_summary.empty:
        min_overlap = valid_summary["avg_universe_overlap_ratio"].min()
        coverage_flag = (
            "验证期三个信号与成分股交集覆盖率未见显著低覆盖。"
            if min_overlap > 0.90
            else "验证期存在信号与成分股交集覆盖率偏低，需要优先排查隐式股票池筛选。"
        )

    exp_roll_corr = corr[
        corr["left"].eq("expanding_ridge")
        & corr["right"].eq("rolling_48m")
        & corr["period"].eq("valid")
    ]
    corr_flag = ""
    if not exp_roll_corr.empty:
        mean_corr = float(exp_roll_corr["spearman_rank_corr"].mean())
        corr_flag = (
            f"expanding 与 rolling-48m 验证期 rank correlation 均值为 {_fmt(mean_corr)}。"
        )

    lines = [
        "# 阶段 4：信号面板审查",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 正式测试集：未运行。",
        "",
        "## 覆盖和横截面分布",
        "",
        *summary_lines,
        "",
        "## 信号 Rank Correlation",
        "",
        *(corr_lines if corr_lines else ["- 无可用 rank correlation。"]),
        "",
        "## 信号换手和目标权重换手",
        "",
        *turnover_lines,
        "",
        "## IC 明细汇总",
        "",
        *ic_lines,
        "",
        "## 阶段 4 判定",
        "",
        f"- {coverage_flag}",
        f"- {corr_flag}",
        "- 阶段 4 只说明信号层和权重层的差异特征，不单独证明 rolling-48m 的 IR 改善真实稳健；仍需结合阶段 5 的训练窗口/未来函数审查和阶段 6 的优化器归因。",
    ]
    (OUT_DIR / "stage4_signal_audit.md").write_text("\n".join(lines), encoding="utf-8")


def write_combined_log(stage2b: dict[str, Any], stage4: dict[str, pd.DataFrame]) -> None:
    stage2b_md = (OUT_DIR / "stage2b_optimizer_stability.md").read_text(encoding="utf-8")
    stage4_md = (OUT_DIR / "stage4_signal_audit.md").read_text(encoding="utf-8")
    stage2b_conclusion = stage2b_md.split("## 阶段 2b 判定", 1)[-1].strip()
    stage4_conclusion = stage4_md.split("## 阶段 4 判定", 1)[-1].strip()
    lines = [
        "# 2026-05-26 阶段 2b/4 审查日志",
        "",
        "- `stage2b_optimizer_stability.md`：优化器重建稳定性审查。",
        "- `stage4_signal_audit.md`：信号面板审查。",
        "- 正式测试集：未运行。",
        "",
        "## 阶段 2b 摘要",
        "",
        stage2b_conclusion,
        "",
        "## 阶段 4 摘要",
        "",
        stage4_conclusion,
    ]
    (OUT_DIR / "stage2b_stage4_log.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage2b-runs", type=int, default=3)
    parser.add_argument("--skip-stage2b", action="store_true")
    parser.add_argument("--skip-stage4", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    stage2b = None
    stage4 = None
    if not args.skip_stage2b:
        stage2b = run_stage2b(args.stage2b_runs)
        write_stage2b_report(stage2b)
    if not args.skip_stage4:
        stage4 = run_stage4()
        write_stage4_report(stage4)
    if stage2b is not None and stage4 is not None:
        write_combined_log(stage2b, stage4)

    print(f"Wrote {OUT_DIR / 'stage2b_optimizer_stability.md'}")
    print(f"Wrote {OUT_DIR / 'stage4_signal_audit.md'}")
    if (OUT_DIR / "stage2b_stage4_log.md").exists():
        print(f"Wrote {OUT_DIR / 'stage2b_stage4_log.md'}")


if __name__ == "__main__":
    main()
