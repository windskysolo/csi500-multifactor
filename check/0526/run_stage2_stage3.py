"""
Stage 2/3 audit for check/0526.

The script is read-only for research artifacts under data/ and experiments/.
It writes all audit artifacts under check/0526/ and check/0526/tmp/.

Stage 2:
  - replay existing target weights with the current backtest engine
  - rebuild the expanding Ridge+lambda=0.005 weights with current optimizer code
  - compare NAV, metrics, target weights, actual weights, trades, and fallback meta

Stage 3:
  - verify benchmark NAV usage
  - manually recalculate sampled forward returns with T+1 open to T'+1 open
  - audit PIT snapshots and factor panel date coverage
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "check" / "0526"
TMP_DIR = OUT_DIR / "tmp"
STAGE2_TMP = TMP_DIR / "stage2"

sys.path.insert(0, str(ROOT))

from src import config as cfg  # noqa: E402
from src.backtest.engine import BacktestConfig, run_backtest  # noqa: E402
from src.data.loader import load_daily_quote, load_financial_pit, load_indicator_pit  # noqa: E402
from experiments.turnover_lambda_grid.run_turnover_lambda_grid import (  # noqa: E402
    LAMBDA_CANDIDATES,
    build_period_data as build_turnover_period_data,
    build_weights_for_lambda,
    load_data as load_turnover_data,
)


LAM_BEST = 0.005
EPS = 1e-10
MATERIAL_NAV_EPS = 1e-8
MATERIAL_WEIGHT_EPS = 1e-6


@dataclass(frozen=True)
class ReplaySpec:
    source_id: str
    weights_path: Path
    nav_path: Path
    metrics_path: Path
    trade_path: Path | None = None
    actual_weights_path: Path | None = None
    meta_path: Path | None = None
    note: str = ""


PIPELINE_B_DIR = ROOT / "experiments" / "turnover_lambda_grid" / "results" / "lam_0050"
ROLLING_48M_DIR = ROOT / "experiments" / "ridge_rolling" / "results" / "rolling_48m"

REPLAY_SPECS = [
    ReplaySpec(
        source_id="pipeline_b_lam0050",
        weights_path=PIPELINE_B_DIR / "weights_optimized.parquet",
        nav_path=PIPELINE_B_DIR / "backtest_nav_valid.parquet",
        metrics_path=PIPELINE_B_DIR / "backtest_metrics_valid.parquet",
        trade_path=PIPELINE_B_DIR / "trades_v2.parquet",
        actual_weights_path=PIPELINE_B_DIR / "actual_weights_v2.parquet",
        meta_path=PIPELINE_B_DIR / "weights_meta.parquet",
        note="Pipeline B existing expanding Ridge lambda=0.005",
    ),
    ReplaySpec(
        source_id="rolling_48m",
        weights_path=ROLLING_48M_DIR / "weights_optimized.parquet",
        nav_path=ROLLING_48M_DIR / "backtest_nav_valid.parquet",
        metrics_path=ROLLING_48M_DIR / "backtest_metrics_valid.parquet",
        note="Rolling-window Ridge 48m existing weights",
    ),
]


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _to_dt_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    return out


def _write_parquet_if_not_empty(df: pd.DataFrame, path: Path) -> None:
    if not df.empty:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)


def _as_nav_df(result) -> pd.DataFrame:
    nav = pd.DataFrame(
        {
            "strategy": result.nav,
            "benchmark": result.benchmark_nav,
        }
    ).dropna()
    nav.index.name = "trade_date"
    return nav


def _read_metric_dict(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    df = pd.read_parquet(path)
    out: dict[str, float] = {}
    if len(df.columns) == 1:
        series = df.iloc[:, 0]
        for k, v in series.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
        return out
    for metric in df.index:
        values = df.loc[metric].dropna()
        if not values.empty:
            try:
                out[str(metric)] = float(values.iloc[0])
            except (TypeError, ValueError):
                pass
    return out


def _compare_metrics(
    source_id: str,
    metric_new: dict[str, float],
    metric_old: dict[str, float],
    comparison: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    keys = sorted(set(metric_new) | set(metric_old))
    for key in keys:
        new_val = metric_new.get(key, float("nan"))
        old_val = metric_old.get(key, float("nan"))
        rows.append(
            {
                "source_id": source_id,
                "comparison": comparison,
                "metric": key,
                "old_value": old_val,
                "new_value": new_val,
                "diff": (
                    float(new_val) - float(old_val)
                    if pd.notna(new_val) and pd.notna(old_val)
                    else float("nan")
                ),
            }
        )
    return rows


def _compare_nav(
    source_id: str,
    old_nav_path: Path,
    new_nav: pd.DataFrame,
    comparison: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    old_nav = _to_dt_index(pd.read_parquet(old_nav_path))
    old = old_nav[["strategy", "benchmark"]].dropna()
    new = new_nav[["strategy", "benchmark"]].dropna()
    aligned_old, aligned_new = old.align(new, join="inner", axis=0)

    diff = pd.DataFrame(index=aligned_old.index)
    diff["source_id"] = source_id
    diff["comparison"] = comparison
    diff["strategy_diff"] = aligned_new["strategy"] - aligned_old["strategy"]
    diff["benchmark_diff"] = aligned_new["benchmark"] - aligned_old["benchmark"]
    old_excess = aligned_old["strategy"] / aligned_old["benchmark"]
    new_excess = aligned_new["strategy"] / aligned_new["benchmark"]
    diff["excess_nav_diff"] = new_excess - old_excess
    diff.index.name = "trade_date"

    row = {
        "source_id": source_id,
        "comparison": comparison,
        "old_nav_path": _rel(old_nav_path),
        "new_rows": int(len(new)),
        "old_rows": int(len(old)),
        "aligned_rows": int(len(aligned_old)),
        "max_abs_strategy_diff": float(diff["strategy_diff"].abs().max()) if not diff.empty else float("nan"),
        "max_abs_benchmark_diff": float(diff["benchmark_diff"].abs().max()) if not diff.empty else float("nan"),
        "max_abs_excess_nav_diff": float(diff["excess_nav_diff"].abs().max()) if not diff.empty else float("nan"),
        "first_diff_date": (
            diff.index[(diff[["strategy_diff", "benchmark_diff", "excess_nav_diff"]].abs().max(axis=1) > EPS)][0].strftime("%Y-%m-%d")
            if not diff.empty
            and (diff[["strategy_diff", "benchmark_diff", "excess_nav_diff"]].abs().max(axis=1) > EPS).any()
            else ""
        ),
    }
    return row, diff.reset_index()


def _align_numeric_frames(
    old: pd.DataFrame,
    new: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    old2 = old.copy()
    new2 = new.copy()
    old2.index = pd.to_datetime(old2.index)
    new2.index = pd.to_datetime(new2.index)
    old2.columns = old2.columns.astype(str)
    new2.columns = new2.columns.astype(str)
    idx = old2.index.union(new2.index)
    cols = old2.columns.union(new2.columns)
    return (
        old2.reindex(index=idx, columns=cols).fillna(0.0).sort_index(),
        new2.reindex(index=idx, columns=cols).fillna(0.0).sort_index(),
    )


def _frame_diff_summary(
    source_id: str,
    comparison: str,
    old: pd.DataFrame,
    new: pd.DataFrame,
    old_path: Path,
    new_path: Path,
) -> dict[str, Any]:
    old_aligned, new_aligned = _align_numeric_frames(old, new)
    diff = (new_aligned - old_aligned).abs()
    return {
        "source_id": source_id,
        "comparison": comparison,
        "old_path": _rel(old_path),
        "new_path": _rel(new_path),
        "old_shape": f"{old.shape[0]}x{old.shape[1]}",
        "new_shape": f"{new.shape[0]}x{new.shape[1]}",
        "aligned_shape": f"{diff.shape[0]}x{diff.shape[1]}",
        "max_abs_diff": float(diff.max().max()) if not diff.empty else float("nan"),
        "mean_abs_diff": float(diff.stack().mean()) if not diff.empty else float("nan"),
        "nonzero_cells_gt_1e_10": int((diff > EPS).sum().sum()) if not diff.empty else 0,
        "dates_with_diff_gt_1e_10": int((diff.max(axis=1) > EPS).sum()) if not diff.empty else 0,
    }


def _compare_trade_logs(
    source_id: str,
    comparison: str,
    old_path: Path | None,
    new_trade: pd.DataFrame,
    new_path: Path,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source_id": source_id,
        "comparison": comparison,
        "old_path": _rel(old_path) if old_path else "",
        "new_path": _rel(new_path),
        "old_exists": bool(old_path and old_path.exists()),
        "new_rows": int(len(new_trade)),
        "max_abs_numeric_diff": float("nan"),
        "nonzero_numeric_cells_gt_1e_10": 0,
        "note": "",
    }
    if not old_path or not old_path.exists():
        row["note"] = "no old trade log"
        return row
    old_trade = pd.read_parquet(old_path)
    row["old_rows"] = int(len(old_trade))
    if old_trade.empty or new_trade.empty:
        row["note"] = "empty old or new trade log"
        return row
    keys = [c for c in ["exec_date", "rebalance_date"] if c in old_trade.columns and c in new_trade.columns]
    if len(keys) < 2:
        row["note"] = "missing date keys"
        return row
    old = old_trade.copy()
    new = new_trade.copy()
    for key in keys:
        old[key] = pd.to_datetime(old[key])
        new[key] = pd.to_datetime(new[key])
    old = old.set_index(keys).sort_index()
    new = new.set_index(keys).sort_index()
    num_cols = [c for c in old.columns.intersection(new.columns) if pd.api.types.is_numeric_dtype(old[c]) and pd.api.types.is_numeric_dtype(new[c])]
    if not num_cols:
        row["note"] = "no common numeric columns"
        return row
    old_a, new_a = old[num_cols].align(new[num_cols], join="outer", axis=0, fill_value=0.0)
    diff = (new_a - old_a).abs()
    row["aligned_rows"] = int(len(diff))
    row["max_abs_numeric_diff"] = float(diff.max().max()) if not diff.empty else float("nan")
    row["nonzero_numeric_cells_gt_1e_10"] = int((diff > EPS).sum().sum()) if not diff.empty else 0
    return row


def _fallback_distribution(meta: pd.DataFrame) -> dict[str, int]:
    if meta.empty or "fallback_level" not in meta.columns:
        return {}
    counts = meta["fallback_level"].value_counts(dropna=False).sort_index()
    return {str(k): int(v) for k, v in counts.items()}


def run_replay(spec: ReplaySpec) -> dict[str, Any]:
    out = STAGE2_TMP / f"{spec.source_id}_replayed"
    out.mkdir(parents=True, exist_ok=True)

    weights = _to_dt_index(pd.read_parquet(spec.weights_path))
    weights.columns = weights.columns.astype(str)
    result = run_backtest(weights, cfg.VALID_START, cfg.VALID_END, BacktestConfig(initial_value=1.0))

    nav = _as_nav_df(result)
    nav_path = out / "backtest_nav_valid.parquet"
    metric_path = out / "backtest_metrics_valid.parquet"
    trade_path = out / "trades_valid.parquet"
    actual_weight_path = out / "actual_weights_valid.parquet"
    nav.to_parquet(nav_path)
    pd.Series(result.metrics, name="value").to_frame().to_parquet(metric_path)
    _write_parquet_if_not_empty(result.trade_log, trade_path)
    _write_parquet_if_not_empty(result.actual_weights, actual_weight_path)

    nav_row, daily_diff = _compare_nav(
        spec.source_id,
        spec.nav_path,
        nav,
        "existing_weights_current_engine_vs_existing_nav",
    )
    daily_diff.to_csv(
        OUT_DIR / f"stage2_nav_diff_{spec.source_id}.csv",
        index=False,
        encoding="utf-8-sig",
    )
    metric_rows = _compare_metrics(
        spec.source_id,
        result.metrics,
        _read_metric_dict(spec.metrics_path),
        "existing_weights_current_engine_vs_existing_metrics",
    )
    trade_row = _compare_trade_logs(
        spec.source_id,
        "existing_weights_current_engine_vs_existing_trades",
        spec.trade_path,
        result.trade_log,
        trade_path,
    )

    actual_weight_row: dict[str, Any] | None = None
    if spec.actual_weights_path and spec.actual_weights_path.exists() and not result.actual_weights.empty:
        old_actual = pd.read_parquet(spec.actual_weights_path)
        actual_weight_row = _frame_diff_summary(
            spec.source_id,
            "existing_weights_current_engine_actual_weights_vs_existing_actual_weights",
            old_actual,
            result.actual_weights,
            spec.actual_weights_path,
            actual_weight_path,
        )

    return {
        "result": result,
        "weights": weights,
        "nav": nav,
        "nav_path": nav_path,
        "metric_path": metric_path,
        "trade_path": trade_path,
        "actual_weight_path": actual_weight_path,
        "nav_row": nav_row,
        "metric_rows": metric_rows,
        "trade_row": trade_row,
        "actual_weight_row": actual_weight_row,
    }


def rebuild_expanding_current_code() -> dict[str, Any]:
    out = STAGE2_TMP / "expanding_rebuilt_current_code"
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

    if LAM_BEST not in LAMBDA_CANDIDATES:
        raise ValueError(f"LAM_BEST {LAM_BEST} is not in LAMBDA_CANDIDATES={LAMBDA_CANDIDATES}")

    weights_panel, baseline_panel, meta_df = build_weights_for_lambda(
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
    result = run_backtest(weights_panel, cfg.VALID_START, cfg.VALID_END, BacktestConfig(initial_value=1.0))
    nav = _as_nav_df(result)

    weights_path = out / "weights_optimized.parquet"
    baseline_path = out / "weights_baseline.parquet"
    meta_path = out / "weights_meta.parquet"
    nav_path = out / "backtest_nav_valid.parquet"
    metric_path = out / "backtest_metrics_valid.parquet"
    trade_path = out / "trades_valid.parquet"
    actual_weight_path = out / "actual_weights_valid.parquet"
    weights_panel.to_parquet(weights_path)
    baseline_panel.to_parquet(baseline_path)
    meta_df.to_parquet(meta_path)
    nav.to_parquet(nav_path)
    pd.Series(result.metrics, name="value").to_frame().to_parquet(metric_path)
    _write_parquet_if_not_empty(result.trade_log, trade_path)
    _write_parquet_if_not_empty(result.actual_weights, actual_weight_path)

    return {
        "weights": weights_panel,
        "baseline": baseline_panel,
        "meta": meta_df,
        "result": result,
        "nav": nav,
        "paths": {
            "weights": weights_path,
            "baseline": baseline_path,
            "meta": meta_path,
            "nav": nav_path,
            "metrics": metric_path,
            "trades": trade_path,
            "actual_weights": actual_weight_path,
        },
    }


def run_stage2() -> dict[str, Any]:
    STAGE2_TMP.mkdir(parents=True, exist_ok=True)

    nav_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    weight_rows: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    replay_results: dict[str, Any] = {}

    for spec in REPLAY_SPECS:
        replay = run_replay(spec)
        replay_results[spec.source_id] = replay
        nav_rows.append(replay["nav_row"])
        metric_rows.extend(replay["metric_rows"])
        trade_rows.append(replay["trade_row"])
        if replay["actual_weight_row"] is not None:
            weight_rows.append(replay["actual_weight_row"])

    rebuilt = rebuild_expanding_current_code()
    old_b = REPLAY_SPECS[0]
    old_weights = pd.read_parquet(old_b.weights_path)
    weight_rows.append(
        _frame_diff_summary(
            "pipeline_b_lam0050",
            "rebuilt_expanding_current_optimizer_vs_existing_target_weights",
            old_weights,
            rebuilt["weights"],
            old_b.weights_path,
            rebuilt["paths"]["weights"],
        )
    )
    if old_b.meta_path and old_b.meta_path.exists():
        old_meta = _to_dt_index(pd.read_parquet(old_b.meta_path))
        old_dist = _fallback_distribution(old_meta)
        new_dist = _fallback_distribution(rebuilt["meta"])
        levels = sorted(set(old_dist) | set(new_dist))
        for lvl in levels:
            fallback_rows.append(
                {
                    "source_id": "pipeline_b_lam0050",
                    "comparison": "rebuilt_expanding_current_optimizer_vs_existing_meta",
                    "fallback_level": lvl,
                    "old_count": old_dist.get(lvl, 0),
                    "new_count": new_dist.get(lvl, 0),
                    "diff": new_dist.get(lvl, 0) - old_dist.get(lvl, 0),
                }
            )
        weight_rows.append(
            _frame_diff_summary(
                "pipeline_b_lam0050",
                "rebuilt_expanding_current_optimizer_meta_numeric_vs_existing_meta",
                old_meta.select_dtypes(include=[np.number]),
                rebuilt["meta"].select_dtypes(include=[np.number]),
                old_b.meta_path,
                rebuilt["paths"]["meta"],
            )
        )

    nav_row, rebuilt_nav_diff = _compare_nav(
        "pipeline_b_lam0050",
        old_b.nav_path,
        rebuilt["nav"],
        "rebuilt_expanding_current_optimizer_vs_existing_nav",
    )
    nav_rows.append(nav_row)
    rebuilt_nav_diff.to_csv(
        OUT_DIR / "stage2_nav_diff_pipeline_b_rebuilt.csv",
        index=False,
        encoding="utf-8-sig",
    )
    metric_rows.extend(
        _compare_metrics(
            "pipeline_b_lam0050",
            rebuilt["result"].metrics,
            _read_metric_dict(old_b.metrics_path),
            "rebuilt_expanding_current_optimizer_vs_existing_metrics",
        )
    )
    trade_rows.append(
        _compare_trade_logs(
            "pipeline_b_lam0050",
            "rebuilt_expanding_current_optimizer_vs_existing_trades",
            old_b.trade_path,
            rebuilt["result"].trade_log,
            rebuilt["paths"]["trades"],
        )
    )

    pd.DataFrame(nav_rows).to_csv(OUT_DIR / "stage2_equivalence_summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(metric_rows).to_csv(OUT_DIR / "stage2_metric_diff.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(trade_rows).to_csv(OUT_DIR / "stage2_trade_log_diff.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(weight_rows).to_csv(OUT_DIR / "stage2_weight_diff.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(fallback_rows).to_csv(OUT_DIR / "stage2_fallback_diff.csv", index=False, encoding="utf-8-sig")

    return {
        "nav_rows": nav_rows,
        "metric_rows": metric_rows,
        "trade_rows": trade_rows,
        "weight_rows": weight_rows,
        "fallback_rows": fallback_rows,
        "replay_results": replay_results,
        "rebuilt": rebuilt,
    }


def _load_index_quote() -> pd.DataFrame:
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    if "trade_date" in iq.columns:
        iq = iq.set_index("trade_date")
    iq.index = pd.to_datetime(iq.index)
    return iq.sort_index()


def _normalize_nav(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    s = series[(series.index >= start) & (series.index <= end)].dropna()
    if s.empty:
        return s
    return s / s.iloc[0]


def audit_benchmark() -> pd.DataFrame:
    iq = _load_index_quote()
    rows: list[dict[str, Any]] = []
    has_nav = "nav" in iq.columns

    for spec in REPLAY_SPECS:
        nav = _to_dt_index(pd.read_parquet(spec.nav_path))
        bench = nav["benchmark"].dropna()
        nav_norm = _normalize_nav(iq["nav"], bench.index.min(), bench.index.max()) if has_nav else pd.Series(dtype=float)
        old, new = bench.align(nav_norm, join="inner")
        rows.append(
            {
                "source_id": spec.source_id,
                "nav_path": _rel(spec.nav_path),
                "index_quote_has_nav": has_nav,
                "index_quote_columns": json.dumps([str(c) for c in iq.columns], ensure_ascii=False),
                "nav_file_benchmark_rows": int(len(bench)),
                "aligned_rows": int(len(old)),
                "max_abs_diff_vs_index_nav_normalized": float((old - new).abs().max()) if len(old) else float("nan"),
                "nav_file_benchmark_return": float(bench.iloc[-1] / bench.iloc[0] - 1.0) if len(bench) else float("nan"),
                "index_nav_return_same_span": float(nav_norm.iloc[-1] / nav_norm.iloc[0] - 1.0) if len(nav_norm) else float("nan"),
                "span_start": bench.index.min().strftime("%Y-%m-%d") if len(bench) else "",
                "span_end": bench.index.max().strftime("%Y-%m-%d") if len(bench) else "",
            }
        )
    out = pd.DataFrame(rows)
    out.to_csv(OUT_DIR / "stage3_benchmark_audit.csv", index=False, encoding="utf-8-sig")
    return out


def scan_benchmark_code() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for base in [ROOT / "src", ROOT / "scripts", ROOT / "experiments"]:
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for i, line in enumerate(lines, start=1):
                stripped = line.strip()
                if "index_quote" not in stripped and "benchmark_nav" not in stripped and "_load_benchmark_nav" not in stripped:
                    continue
                lower = stripped.lower()
                suspicious = (
                    "close" in lower
                    and "nav" not in lower
                    and "price" not in lower
                    and "missing_close" not in lower
                )
                rows.append(
                    {
                        "path": _rel(path),
                        "line_no": i,
                        "suspicious_close_benchmark_candidate": suspicious,
                        "line": stripped[:300],
                    }
                )
    out = pd.DataFrame(rows)
    out.to_csv(OUT_DIR / "stage3_benchmark_code_scan.csv", index=False, encoding="utf-8-sig")
    return out


def _next_after(date: pd.Timestamp, dates: pd.DatetimeIndex) -> pd.Timestamp | None:
    candidates = dates[dates > date]
    if len(candidates) == 0:
        return None
    return pd.Timestamp(candidates[0])


def _build_entry_exit_map() -> pd.DataFrame:
    iq = _load_index_quote()
    trading_dates = pd.DatetimeIndex(iq.index).sort_values()
    index_member = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    rebal_dates = pd.DatetimeIndex(
        sorted(index_member.index.get_level_values("rebalance_date").unique())
    )
    rows: list[dict[str, Any]] = []
    for i, T in enumerate(rebal_dates[:-1]):
        T_next = pd.Timestamp(rebal_dates[i + 1])
        entry = _next_after(pd.Timestamp(T), trading_dates)
        exit_ = _next_after(T_next, trading_dates)
        rows.append(
            {
                "rebalance_date": pd.Timestamp(T),
                "next_rebalance_date": T_next,
                "entry_date": entry,
                "exit_date": exit_,
                "entry_after_T": bool(entry is not None and entry > T),
                "exit_after_next_T": bool(exit_ is not None and exit_ > T_next),
                "exit_after_valid_end": bool(exit_ is not None and exit_ > cfg.VALID_END),
            }
        )
    return pd.DataFrame(rows)


def audit_fwd_ret() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    fwd = _to_dt_index(pd.read_parquet(cfg.DATA_PROC / "fwd_ret_panel.parquet"))
    entry_exit = _build_entry_exit_map()
    entry_exit_fwd = entry_exit[entry_exit["rebalance_date"].isin(fwd.index)].copy()

    factor_dir = cfg.DATA_PROC / "factor_panels"
    sample_factor_path = sorted(factor_dir.glob("*.parquet"))[0]
    sample_factor = _to_dt_index(pd.read_parquet(sample_factor_path))

    summary = pd.DataFrame(
        [
            {
                "check": "fwd_ret_shape",
                "value": f"{fwd.shape[0]}x{fwd.shape[1]}",
                "status": "INFO",
                "note": "",
            },
            {
                "check": "fwd_ret_date_min",
                "value": fwd.index.min().strftime("%Y-%m-%d"),
                "status": "INFO",
                "note": "",
            },
            {
                "check": "fwd_ret_date_max",
                "value": fwd.index.max().strftime("%Y-%m-%d"),
                "status": "INFO",
                "note": "",
            },
            {
                "check": "fwd_index_subset_of_index_member_rebalance_dates",
                "value": str(set(fwd.index).issubset(set(entry_exit["rebalance_date"]))),
                "status": "PASS" if set(fwd.index).issubset(set(entry_exit["rebalance_date"])) else "FAIL",
                "note": "",
            },
            {
                "check": "sample_factor_dates_cover_fwd_dates",
                "value": str(set(fwd.index).issubset(set(sample_factor.index))),
                "status": "PASS" if set(fwd.index).issubset(set(sample_factor.index)) else "FAIL",
                "note": f"sample={_rel(sample_factor_path)}",
            },
            {
                "check": "labels_exit_after_valid_end_count",
                "value": int(entry_exit_fwd["exit_after_valid_end"].sum()),
                "status": "WARN" if int(entry_exit_fwd["exit_after_valid_end"].sum()) else "PASS",
                "note": "这些标签跨过 VALID_END；本审计不读取对应区间价格手工复算。",
            },
        ]
    )
    summary.to_csv(OUT_DIR / "stage3_fwd_ret_audit.csv", index=False, encoding="utf-8-sig")

    entry_exit_fwd.to_csv(OUT_DIR / "stage3_fwd_ret_entry_exit_map.csv", index=False, encoding="utf-8-sig")

    eligible = entry_exit_fwd[
        (entry_exit_fwd["entry_date"].notna())
        & (entry_exit_fwd["exit_date"].notna())
        & (~entry_exit_fwd["exit_after_valid_end"])
        & (entry_exit_fwd["rebalance_date"] >= cfg.VALID_START)
    ].copy()
    if eligible.empty:
        sample_df = pd.DataFrame()
        sample_df.to_csv(OUT_DIR / "stage3_fwd_ret_sample.csv", index=False, encoding="utf-8-sig")
        return summary, entry_exit_fwd, sample_df

    selected_dates = [
        eligible["rebalance_date"].iloc[0],
        eligible["rebalance_date"].iloc[len(eligible) // 2],
        eligible["rebalance_date"].iloc[-1],
    ]
    selected_dates = list(dict.fromkeys(pd.to_datetime(selected_dates).tolist()))
    selected_codes = sorted(
        set().union(
            *[
                set(fwd.loc[T].dropna().sort_index().head(10).index.astype(str))
                for T in selected_dates
            ]
        )
    )
    start_load = pd.Timestamp(eligible.loc[eligible["rebalance_date"].isin(selected_dates), "entry_date"].min())
    end_load = pd.Timestamp(eligible.loc[eligible["rebalance_date"].isin(selected_dates), "exit_date"].max())
    dq = load_daily_quote(start_load, end_load, codes=selected_codes)
    open_pivot = dq["open_adj"].unstack("ts_code")

    sample_rows: list[dict[str, Any]] = []
    for T in selected_dates:
        row = eligible.loc[eligible["rebalance_date"] == T].iloc[0]
        entry = pd.Timestamp(row["entry_date"])
        exit_ = pd.Timestamp(row["exit_date"])
        codes = list(fwd.loc[T].dropna().sort_index().head(10).index.astype(str))
        for code in codes:
            stored = float(fwd.loc[T, code])
            entry_px = float(open_pivot.loc[entry, code]) if code in open_pivot.columns and entry in open_pivot.index else float("nan")
            exit_px = float(open_pivot.loc[exit_, code]) if code in open_pivot.columns and exit_ in open_pivot.index else float("nan")
            expected = exit_px / entry_px - 1.0 if entry_px > 0 and exit_px > 0 else float("nan")
            sample_rows.append(
                {
                    "rebalance_date": pd.Timestamp(T).strftime("%Y-%m-%d"),
                    "next_rebalance_date": pd.Timestamp(row["next_rebalance_date"]).strftime("%Y-%m-%d"),
                    "entry_date": entry.strftime("%Y-%m-%d"),
                    "exit_date": exit_.strftime("%Y-%m-%d"),
                    "ts_code": code,
                    "stored_fwd_ret": stored,
                    "manual_open_to_open_ret": expected,
                    "diff": stored - expected if pd.notna(expected) else float("nan"),
                }
            )
    sample_df = pd.DataFrame(sample_rows)
    sample_df.to_csv(OUT_DIR / "stage3_fwd_ret_sample.csv", index=False, encoding="utf-8-sig")
    return summary, entry_exit_fwd, sample_df


def audit_pit_and_factors() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    pit_rows: list[dict[str, Any]] = []
    index_member = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    sample_dates = [
        pd.Timestamp("2021-01-29"),
        pd.Timestamp("2021-12-31"),
        pd.Timestamp("2022-06-30"),
        pd.Timestamp("2022-12-30"),
    ]
    member_dates = pd.DatetimeIndex(sorted(index_member.index.get_level_values("rebalance_date").unique()))
    sample_dates = [d for d in sample_dates if d in member_dates]
    for T in sample_dates:
        codes = sorted(index_member.loc[T].index.astype(str))[:10]
        for table_name, loader in [
            ("financial_pit", load_financial_pit),
            ("indicator_pit", load_indicator_pit),
        ]:
            snap = loader(T, codes=codes)
            if snap.empty:
                pit_rows.append(
                    {
                        "table": table_name,
                        "rebalance_date": T.strftime("%Y-%m-%d"),
                        "n_rows": 0,
                        "future_pit_rows": 0,
                        "future_end_rows": 0,
                        "status": "WARN",
                        "note": "empty snapshot for sampled codes",
                    }
                )
                continue
            pit_date = pd.to_datetime(snap["pit_date"])
            end_date = pd.to_datetime(snap["end_date"])
            future_pit = int((pit_date > T).sum())
            future_end = int((end_date >= T).sum())
            pit_rows.append(
                {
                    "table": table_name,
                    "rebalance_date": T.strftime("%Y-%m-%d"),
                    "n_rows": int(len(snap)),
                    "future_pit_rows": future_pit,
                    "future_end_rows": future_end,
                    "max_pit_date": pit_date.max().strftime("%Y-%m-%d"),
                    "max_end_date": end_date.max().strftime("%Y-%m-%d"),
                    "status": "PASS" if future_pit == 0 and future_end == 0 else "FAIL",
                    "note": "loader snapshot requires pit_date <= T and end_date < T",
                }
            )
    pit_df = pd.DataFrame(pit_rows)
    pit_df.to_csv(OUT_DIR / "stage3_pit_snapshot_audit.csv", index=False, encoding="utf-8-sig")

    factor_rows: list[dict[str, Any]] = []
    for path in sorted((cfg.DATA_PROC / "factor_panels").glob("*.parquet")):
        df = _to_dt_index(pd.read_parquet(path))
        factor_rows.append(
            {
                "factor": path.stem,
                "path": _rel(path),
                "n_dates": int(len(df.index)),
                "n_cols": int(df.shape[1]),
                "date_min": df.index.min().strftime("%Y-%m-%d") if len(df) else "",
                "date_max": df.index.max().strftime("%Y-%m-%d") if len(df) else "",
                "dates_after_valid_end": int((df.index > cfg.VALID_END).sum()),
                "dates_in_test_period": int((df.index >= cfg.TEST_START).sum()),
                "status": "PASS" if int((df.index >= cfg.TEST_START).sum()) == 0 else "FAIL",
            }
        )
    factor_df = pd.DataFrame(factor_rows)
    factor_df.to_csv(OUT_DIR / "stage3_factor_panel_audit.csv", index=False, encoding="utf-8-sig")

    source_rows = []
    source_checks = {
        "src/data/loader.py": [
            "pit_date <= rebalance_date",
            "end_date < rebalance_date",
            "drop_duplicates(\"ts_code\", keep=\"last\")",
        ],
        "src/factors/preprocess.py": [
            "MAD 法横截面去极值",
            "仅用 T 日横截面计算边界",
            "log(自由流通市值)",
        ],
            "scripts/csv_to_parquet.py": [
            "raw[\"pit_date\"] = raw[\"f_ann_date\"].fillna(raw[\"ann_date\"])",
            "pit_date 取三表最大值",
        ],
    }
    for rel_path, needles in source_checks.items():
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        for needle in needles:
            source_rows.append(
                {
                    "path": rel_path,
                    "needle": needle,
                    "found": needle in text,
                }
            )
    source_df = pd.DataFrame(source_rows)
    source_df.to_csv(OUT_DIR / "stage3_pit_source_scan.csv", index=False, encoding="utf-8-sig")
    return pit_df, factor_df, source_df


def run_stage3() -> dict[str, Any]:
    benchmark = audit_benchmark()
    code_scan = scan_benchmark_code()
    fwd_summary, entry_exit, fwd_sample = audit_fwd_ret()
    pit_df, factor_df, source_df = audit_pit_and_factors()
    return {
        "benchmark": benchmark,
        "code_scan": code_scan,
        "fwd_summary": fwd_summary,
        "entry_exit": entry_exit,
        "fwd_sample": fwd_sample,
        "pit": pit_df,
        "factor": factor_df,
        "source": source_df,
    }


def _fmt_float(x: Any, digits: int = 12) -> str:
    try:
        val = float(x)
    except (TypeError, ValueError):
        return "N/A"
    if math.isnan(val):
        return "N/A"
    return f"{val:.{digits}g}"


def write_reports(stage2: dict[str, Any], stage3: dict[str, Any]) -> None:
    nav_df = pd.DataFrame(stage2["nav_rows"])
    metric_df = pd.DataFrame(stage2["metric_rows"])
    trade_df = pd.DataFrame(stage2["trade_rows"])
    weight_df = pd.DataFrame(stage2["weight_rows"])
    fallback_df = pd.DataFrame(stage2["fallback_rows"])
    benchmark_df = stage3["benchmark"]
    fwd_summary = stage3["fwd_summary"]
    fwd_sample = stage3["fwd_sample"]
    pit_df = stage3["pit"]
    factor_df = stage3["factor"]
    source_df = stage3["source"]

    stage2_lines = [
        "# 阶段 2：回测入口和产物等价性审查",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 正式测试集：未运行。",
        "- 写入目录：`check/0526/tmp/stage2/`；未覆盖 `experiments/` 产物。",
        "",
        "## 2.1 同一权重 + 当前回测引擎",
        "",
    ]
    for row in nav_df.to_dict("records"):
        stage2_lines.append(
            "- `{source}` / {cmp}: strategy max diff={sd}, benchmark max diff={bd}, excess max diff={ed}, rows={rows}".format(
                source=row.get("source_id"),
                cmp=row.get("comparison"),
                sd=_fmt_float(row.get("max_abs_strategy_diff")),
                bd=_fmt_float(row.get("max_abs_benchmark_diff")),
                ed=_fmt_float(row.get("max_abs_excess_nav_diff")),
                rows=row.get("aligned_rows"),
            )
        )

    stage2_lines.extend(["", "## 2.2 Metrics 差异", ""])
    key_metrics = {"information_ratio", "excess_return", "monthly_win_rate", "tracking_error"}
    for row in metric_df[metric_df["metric"].isin(key_metrics)].to_dict("records"):
        stage2_lines.append(
            "- `{source}` / {cmp} / `{metric}`: old={old}, new={new}, diff={diff}".format(
                source=row.get("source_id"),
                cmp=row.get("comparison"),
                metric=row.get("metric"),
                old=_fmt_float(row.get("old_value")),
                new=_fmt_float(row.get("new_value")),
                diff=_fmt_float(row.get("diff")),
            )
        )

    stage2_lines.extend(["", "## 2.3 权重、交易日志和 fallback", ""])
    for row in weight_df.to_dict("records"):
        stage2_lines.append(
            "- `{source}` / {cmp}: max_abs_diff={maxd}, nonzero_cells>{eps}={nz}, dates_with_diff={dd}".format(
                source=row.get("source_id"),
                cmp=row.get("comparison"),
                maxd=_fmt_float(row.get("max_abs_diff")),
                eps=EPS,
                nz=row.get("nonzero_cells_gt_1e_10"),
                dd=row.get("dates_with_diff_gt_1e_10"),
            )
        )
    for row in trade_df.to_dict("records"):
        stage2_lines.append(
            "- `{source}` / {cmp}: old_exists={old_exists}, max_trade_numeric_diff={diff}, nonzero_cells={nz}, note={note}".format(
                source=row.get("source_id"),
                cmp=row.get("comparison"),
                old_exists=row.get("old_exists"),
                diff=_fmt_float(row.get("max_abs_numeric_diff")),
                nz=row.get("nonzero_numeric_cells_gt_1e_10"),
                note=row.get("note", ""),
            )
        )
    if not fallback_df.empty:
        for row in fallback_df.to_dict("records"):
            stage2_lines.append(
                f"- fallback L{row.get('fallback_level')}: old={row.get('old_count')} new={row.get('new_count')} diff={row.get('diff')}"
            )

    stage2_lines.extend(
        [
            "",
            "## 阶段 2 初步判定",
            "",
        ]
    )
    nav_diff_cols = ["max_abs_strategy_diff", "max_abs_benchmark_diff", "max_abs_excess_nav_diff"]
    replay_nav_mask = nav_df["comparison"].astype(str).str.startswith("existing_weights_current_engine")
    rebuilt_nav_mask = nav_df["comparison"].astype(str).str.startswith("rebuilt_expanding_current_optimizer")
    max_replay_nav_diff = (
        float(nav_df.loc[replay_nav_mask, nav_diff_cols].abs().max().max())
        if replay_nav_mask.any()
        else float("nan")
    )
    max_rebuilt_nav_diff = (
        float(nav_df.loc[rebuilt_nav_mask, nav_diff_cols].abs().max().max())
        if rebuilt_nav_mask.any()
        else float("nan")
    )
    weight_mask = weight_df["comparison"].astype(str).str.contains("target_weights|actual_weights", regex=True)
    max_material_weight_diff = (
        float(weight_df.loc[weight_mask, "max_abs_diff"].abs().max())
        if not weight_df.empty and weight_mask.any()
        else float("nan")
    )
    if max_replay_nav_diff <= MATERIAL_NAV_EPS and max_rebuilt_nav_diff <= MATERIAL_NAV_EPS and (
        pd.isna(max_material_weight_diff) or max_material_weight_diff <= MATERIAL_WEIGHT_EPS
    ):
        stage2_lines.append("- 同权重重跑、当前代码重建 expanding 与现有产物在数值上等价，暂未看到产物新旧混用证据。")
    elif max_replay_nav_diff <= MATERIAL_NAV_EPS:
        stage2_lines.append(
            "- 同一旧权重用当前回测引擎重跑等价，回测入口可复现；但当前优化器重建 expanding 与旧 Pipeline B 产物存在显著差异，rolling 与 expanding 的机制解释需先排查优化器输入、求解器稳定性或产物冻结口径。"
        )
    else:
        stage2_lines.append("- 同一旧权重用当前回测引擎重跑已出现 NAV 差异；现有 NAV 与当前回测入口不可直接比较，应暂停策略原因解释。")

    (OUT_DIR / "pipeline_equivalence.md").write_text("\n".join(stage2_lines), encoding="utf-8")

    stage3_lines = [
        "# 阶段 3：数据口径和时间对齐审查",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 正式测试集：未运行；forward return 手工复算样本只选 exit_date <= VALID_END 的验证期标签。",
        "",
        "## 3.1 基准口径",
        "",
    ]
    for row in benchmark_df.to_dict("records"):
        stage3_lines.append(
            "- `{source}`: index_quote has nav={has_nav}, benchmark vs normalized nav max diff={diff}, span={start}~{end}".format(
                source=row.get("source_id"),
                has_nav=row.get("index_quote_has_nav"),
                diff=_fmt_float(row.get("max_abs_diff_vs_index_nav_normalized")),
                start=row.get("span_start"),
                end=row.get("span_end"),
            )
        )
    suspicious_count = int(stage3["code_scan"]["suspicious_close_benchmark_candidate"].sum()) if not stage3["code_scan"].empty else 0
    stage3_lines.append(f"- 代码扫描候选：`stage3_benchmark_code_scan.csv`，疑似 close 绕过候选 {suspicious_count} 条；需要人工看上下文，脚本不把注释/数据质量检查当作违规。")

    stage3_lines.extend(["", "## 3.2 Forward Return 标签", ""])
    for row in fwd_summary.to_dict("records"):
        stage3_lines.append(f"- `{row.get('check')}`: {row.get('value')} [{row.get('status')}] {row.get('note')}")
    if not fwd_sample.empty:
        max_sample_diff = float(fwd_sample["diff"].abs().max())
        stage3_lines.append(f"- 手工复算样本：{len(fwd_sample)} 行，max_abs_diff={_fmt_float(max_sample_diff)}。")

    stage3_lines.extend(["", "## 3.3 PIT 和因子面板", ""])
    pit_fail = int((pit_df["status"] == "FAIL").sum()) if not pit_df.empty else 0
    pit_warn = int((pit_df["status"] == "WARN").sum()) if not pit_df.empty else 0
    factor_test_rows = int(factor_df["dates_in_test_period"].sum()) if not factor_df.empty else 0
    source_missing = int((~source_df["found"]).sum()) if not source_df.empty else 0
    stage3_lines.append(f"- PIT loader 抽样：FAIL={pit_fail}, WARN={pit_warn}，详见 `stage3_pit_snapshot_audit.csv`。")
    stage3_lines.append(f"- 因子面板测试期日期行数：{factor_test_rows}，详见 `stage3_factor_panel_audit.csv`。")
    stage3_lines.append(f"- PIT/预处理源码关键语句未命中数量：{source_missing}，详见 `stage3_pit_source_scan.csv`。")

    stage3_lines.extend(["", "## 阶段 3 初步判定", ""])
    benchmark_ok = bool(
        benchmark_df["index_quote_has_nav"].all()
        and benchmark_df["max_abs_diff_vs_index_nav_normalized"].abs().max() <= EPS
    )
    fwd_ok = bool((fwd_sample["diff"].abs().max() <= EPS) if not fwd_sample.empty else False)
    pit_ok = pit_fail == 0 and source_missing == 0 and factor_test_rows == 0
    if benchmark_ok:
        stage3_lines.append("- 基准 NAV 口径与现有验证期 NAV 文件一致，未发现用价格指数替代全收益基准的证据。")
    else:
        stage3_lines.append("- 基准审计未完全通过，超额收益和 IR 结论需暂停。")
    if fwd_ok:
        stage3_lines.append("- 已抽样验证 fwd_ret_panel 的 T+1 开盘到 T'+1 开盘口径。")
    else:
        stage3_lines.append("- fwd_ret_panel 抽样未完全通过或样本为空，需要继续排查。")
    boundary_count = int(fwd_summary.loc[fwd_summary["check"] == "labels_exit_after_valid_end_count", "value"].iloc[0])
    if boundary_count:
        stage3_lines.append(f"- 发现 {boundary_count} 个 fwd_ret 标签的 exit_date 超过 VALID_END；这不是正式测试集回测，但会影响验证期 IC/训练标签边界解释，应在阶段 5 继续检查训练窗口是否按 exit_date purge。")
    if pit_ok:
        stage3_lines.append("- PIT 抽样和因子面板日期覆盖未发现明显未来数据。")
    else:
        stage3_lines.append("- PIT/因子面板审计存在未通过项，需先处理后再解释 rolling 优势。")

    (OUT_DIR / "data_alignment_audit.md").write_text("\n".join(stage3_lines), encoding="utf-8")

    combined_lines = [
        "# 2026-05-26 阶段 2/3 审查日志",
        "",
        "- `pipeline_equivalence.md`：阶段 2 结论。",
        "- `data_alignment_audit.md`：阶段 3 结论。",
        "- 关键 CSV：`stage2_equivalence_summary.csv`、`stage2_metric_diff.csv`、`stage2_weight_diff.csv`、`stage3_benchmark_audit.csv`、`stage3_fwd_ret_sample.csv`、`stage3_pit_snapshot_audit.csv`。",
        "- 正式测试集：未运行。",
        "",
        "## 摘要",
        "",
        stage2_lines[-1],
        stage3_lines[-3] if len(stage3_lines) >= 3 else "",
        stage3_lines[-2] if len(stage3_lines) >= 2 else "",
        stage3_lines[-1] if len(stage3_lines) >= 1 else "",
    ]
    (OUT_DIR / "stage2_stage3_log.md").write_text("\n".join(combined_lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    stage2 = run_stage2()
    stage3 = run_stage3()
    write_reports(stage2, stage3)
    print(f"Wrote {OUT_DIR / 'pipeline_equivalence.md'}")
    print(f"Wrote {OUT_DIR / 'data_alignment_audit.md'}")
    print(f"Wrote {OUT_DIR / 'stage2_stage3_log.md'}")


if __name__ == "__main__":
    main()
