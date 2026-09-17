"""Read-only project checks for the interview factbook (not a strategy run).

Inputs: current source, the registered train_valid run, and existing research
artifacts. Outputs: a JSON evidence snapshot beside this script. No downloads,
factor generation, model fitting, backtests, or test-period performance reads.
Financial observations are restricted to dates no later than 2022-12-31.
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import platform
from importlib.metadata import version
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
CUTOFF = pd.Timestamp("2022-12-31")
OUT = Path(__file__).with_name("verified_snapshot.json")


def digest(path: Path) -> str:
    """Return SHA256 of an existing file without changing it."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_research(path: Path) -> pd.DataFrame:
    """Read a Parquet date index with an explicit <= validation-end filter."""
    schema = pq.read_schema(path)
    meta = json.loads(schema.metadata[b"pandas"])
    idx = meta["index_columns"][0]
    column_date = not isinstance(idx, str)
    if column_date:
        if "exec_date" not in schema.names:
            raise ValueError(f"No materialized date index: {path}")
        idx = "exec_date"
    df = pd.read_parquet(path, filters=[(idx, "<=", CUTOFF.to_pydatetime())])
    dates = df[idx] if column_date else df.index.get_level_values(0)
    if len(dates) and dates.max() > CUTOFF:
        raise ValueError(f"Date boundary violated: {path}")
    return df


def registry_names(path: Path, name: str) -> list[str]:
    """Read a literal registration dictionary without importing data modules."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return [ast.literal_eval(k) for k in node.value.keys]
    raise ValueError(name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    registry = read_json(ROOT / "registry/mainline.json")
    if registry["scope"] != "train_valid":
        raise ValueError("Only a train_valid mainline may be inspected")
    run = ROOT / "runs/train_valid" / registry["active_run_id"]
    manifest = read_json(run / "manifest.json")
    hashes = {}
    for relative, expected in manifest.items():
        path = (run / relative.replace("\\", "/")).resolve()
        if not path.is_relative_to(run.resolve()):
            raise ValueError("Manifest path outside mainline")
        actual = digest(path) if path.is_file() else None
        hashes[relative] = {"expected": expected["sha256"], "actual": actual, "match": actual == expected["sha256"]}

    nav = read_research(run / "backtest/nav_valid.parquet")
    s, b = nav["strategy_v2"], nav["benchmark"]
    sr, br = s.pct_change(fill_method=None), b.pct_change(fill_method=None)
    # Reproduce the project's convention, which annualizes by len(nav)/252.
    ann_s = float((s.iloc[-1] / s.iloc[0]) ** (252 / len(s)) - 1)
    ann_b = float((b.iloc[-1] / b.iloc[0]) ** (252 / len(b)) - 1)
    te = float((sr - br).std(ddof=1) * np.sqrt(252))
    ratio = s / b
    month = nav.resample("ME").last().pct_change(fill_method=None).dropna()
    computed = {
        "annualized_return": ann_s, "benchmark_return": ann_b,
        "excess_return": ann_s - ann_b, "tracking_error": te,
        "information_ratio": (ann_s - ann_b) / te,
        "max_drawdown": float((s / s.cummax() - 1).min()),
        "excess_max_drawdown": float((ratio / ratio.cummax() - 1).min()),
        "monthly_win_rate": float((month.strategy_v2 > month.benchmark).mean()),
    }
    recorded = pd.read_parquet(run / "backtest/metrics_valid.parquet")["v2"]
    comparisons = {k: {"recorded": float(recorded[k]), "recomputed": v, "abs_diff": abs(float(recorded[k]) - v)} for k, v in computed.items()}
    trades = read_research(run / "backtest/trades_valid.parquet")
    turnover = (trades.buy_value + trades.sell_value) / trades.portfolio_value_before
    weights = read_research(run / "portfolio/target_weights.parquet")
    optmeta = read_research(run / "portfolio/optimizer_meta.parquet")
    comp = read_research(run / "signal/composite.parquet")
    coefs = read_research(run / "signal/coef_history.parquet")
    fact = read_json(ROOT / "reports/factor_evaluation/final_factors.json")
    registries = {
        "financial": registry_names(ROOT / "src/factors/financial_factors.py", "_FACTOR_BUILDERS"),
        "price": registry_names(ROOT / "src/factors/price_factors.py", "_PRICE_FACTOR_BUILDERS"),
        "alt": registry_names(ROOT / "src/factors/alt_factors.py", "_ALT_FACTOR_BUILDERS"),
    }
    selected = fact["final_factors"]
    paneldir = ROOT / "data/processed/factor_panels"
    all_names = set().union(*map(set, registries.values()))
    disk_names = {p.stem for p in paneldir.glob("*.parquet")}
    panels = {}
    for name in selected:
        path = paneldir / f"{name}.parquet"
        df = read_research(path)
        active = df.notna().any(axis=1)
        panels[name] = {"research_shape": list(df.shape), "first_date": str(df.index.min()), "last_date": str(df.index.max()), "first_nonempty_date": str(df.index[active].min()), "empty_dates": [str(x.date()) for x in df.index[~active]]}
    summary = pd.read_csv(ROOT / "reports/factor_evaluation/factor_summary.csv", index_col=0)
    ic = pd.read_csv(ROOT / "reports/factor_evaluation/ic_result.csv", index_col=0)
    valid = pd.read_csv(ROOT / "reports/factor_evaluation/valid_comparison.csv", index_col=0)
    # Verify five current cached labels using observed opens in validation only.
    # This checks today's cache; the historical run did not lock that input.
    fwd = read_research(ROOT / "data/processed/fwd_ret_panel.parquet")
    label_date = pd.Timestamp("2021-01-29")
    next_label_date = pd.Timestamp("2021-02-26")
    label_codes = fwd.loc[label_date].dropna().index[:5].tolist()
    quote_sample = pd.read_parquet(
        ROOT / "data/processed/daily_quote.parquet", columns=["open_adj"],
        filters=[("trade_date", ">", label_date.to_pydatetime()),
                 ("trade_date", "<=", pd.Timestamp("2021-03-05").to_pydatetime()),
                 ("ts_code", "in", label_codes)],
    )["open_adj"].unstack("ts_code")
    entry = quote_sample.index[quote_sample.index > label_date].min()
    exit_date = quote_sample.index[quote_sample.index > next_label_date].min()
    raw_label = quote_sample.loc[exit_date] / quote_sample.loc[entry] - 1
    label_checks = {code: {"cached": float(fwd.loc[label_date, code]), "open_to_open": float(raw_label[code]), "abs_diff": abs(float(fwd.loc[label_date, code]) - float(raw_label[code]))} for code in label_codes}
    # Synthetic probes exercise the existing functions; no real market data.
    from src.backtest.engine import _execute_rebalance
    from src.data.universe import TradeState
    from src.portfolio.optimizer import _topn_equal_weight
    from experiments.legacy.ridge_rolling.rolling_combiner import RidgeRollingCombiner

    ns, cash, record = _execute_rebalance(
        {"000001.SZ": 1.0}, 100.0,
        pd.Series({"000001.SZ": .5, "000002.SZ": .5}),
        pd.Series({"000001.SZ": 100.0, "000002.SZ": 100.0}),
        pd.Series({"000001.SZ": TradeState.LOCKED, "000002.SZ": TradeState.FREE}),
        200.0, pd.Timestamp("2021-02-01"), pd.Timestamp("2021-01-29"),
    )
    nan_w, compliant = _topn_equal_weight(np.array([np.nan, np.nan, np.nan]), 3, 2, set(), single_max_dev=1.0)
    model = RidgeRollingCombiner(["x"], window_months=48, purge_months=2)
    dates = pd.date_range("2010-01-31", "2022-12-31", freq="ME")
    prediction = pd.Timestamp("2022-12-30")
    cutoff = prediction - pd.DateOffset(months=2)
    actual_dates = model._rolling_train_dates(cutoff, dates)
    ledger = read_json(ROOT / "docs/logs/test_set_runs.json")
    # Read only operational ledger fields; do not export historical performance notes.
    snapshot = {
        "as_of": "2026-09-13", "scope": "current code plus historical train_valid artifacts",
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "environment": {"python": platform.python_version(), **{name: version(name) for name in ["pandas", "numpy", "pyarrow", "scipy", "scikit-learn", "cvxpy", "pytest"]}},
        "source_sha256": {str(p.relative_to(ROOT)): digest(p) for folder in ["src", "scripts", "configs", "tests", "experiments/legacy/ridge_signal", "experiments/legacy/ridge_rolling"] for p in sorted((ROOT / folder).rglob("*.py"))},
        "data_resolved_path": str((ROOT / "data").resolve()),
        "run_id": run.name, "run_config": read_json(run / "run_config.json"),
        "inputs_lock": read_json(run / "inputs.lock.json"), "manifest_checks": hashes,
        "metric_checks": comparisons,
        "nav": {"rows": len(nav), "start": str(nav.index.min()), "end": str(nav.index.max()), "missing_cells": int(nav.isna().sum().sum()), "v1_equals_v2": bool(nav.strategy_v1.equals(nav.strategy_v2)), "monthly_return_observations": len(month)},
        "turnover": {"trade_records": len(trades), "code_convention_annual_two_way_multiple": float(turnover.sum() / len(trades) * 12), "total_two_way_multiple": float(turnover.sum())},
        "portfolio": {"shape": list(weights.shape), "start": str(weights.index.min()), "end": str(weights.index.max()), "baseline_equals_target": bool(weights.equals(read_research(run / "portfolio/baseline_weights.parquet"))), "holdings_min": int((weights > 1e-12).sum(axis=1).min()), "holdings_max": int((weights > 1e-12).sum(axis=1).max()), "metadata_counts": {c: optmeta[c].astype(str).value_counts().to_dict() for c in ["fallback_level", "solver_status", "w_prev_source", "constraint_compliant"]}},
        "signal": {"shape": list(comp.shape), "start": str(comp.index.min()), "end": str(comp.index.max()), "coef_names": list(coefs.columns), "coef_names_match_current_selected": set(coefs.columns) == set(selected), "metadata": read_json(run / "signal/signal_metadata.json")},
        "current_label_sample": {"T": str(label_date), "entry": str(entry), "exit": str(exit_date), "checks": label_checks},
        "factors": {"registries": registries, "registered_count": len(all_names), "panel_file_count": len(disk_names), "panels_not_registered": sorted(disk_names - all_names), "registered_not_historical_evaluation": sorted(all_names - set(summary.index)), "selection": fact, "historical_evaluation_rows": len(summary), "selection_matches_summary": set(summary.index[summary.final_include]) == set(selected), "selected_statistics": {name: {"train": ic.loc[name].to_dict(), "valid": valid.loc[name].to_dict(), "screen": summary.loc[name].to_dict()} for name in selected}, "panels_research_only": panels},
        "synthetic_probes": {
            "locked_share_cost": {"original_locked_shares": 1.0, "returned_locked_shares": ns["000001.SZ"], "cost": record.cost, "cash_after": cash, "locked_count": record.n_locked, "violation_reproduced": ns["000001.SZ"] != 1.0},
            "all_nan_topn": {"weights": nan_w.tolist(), "compliant_flag": compliant, "nonzero_despite_all_nan": bool((nan_w > 0).any())},
            "rolling_window": {"T": str(prediction), "cutoff": str(cutoff), "left_exclusive": str(cutoff - pd.DateOffset(months=48)), "selected_count": len(actual_dates), "first": str(actual_dates.min()), "last": str(actual_dates.max())},
        },
        "test_ledger_operational_only": {"max_runs": ledger["max_runs"], "active_runs": ledger["active_runs"], "correction_count": len(ledger.get("corrections", []))},
        "test_inventory": {p.name: sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_") for n in ast.walk(ast.parse(p.read_text(encoding="utf-8-sig")))) for p in sorted((ROOT / "tests").glob("test_*.py"))},
        "limitations": ["No source-data financial audit", "No backtest rerun", "Output hashes do not prove locked inputs", "Synthetic probes do not quantify historical performance bias"],
    }
    # Keep full float precision while exporting NaN as null (not zero).
    def normalize(value):
        if isinstance(value, dict):
            return {str(k): normalize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(v) for v in value]
        if isinstance(value, np.generic):
            return normalize(value.item())
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    clean = normalize(snapshot)
    OUT.write_text(json.dumps(clean, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    logging.info("Wrote %s; manifest matches %d/%d; largest metric difference %.3g", OUT, sum(x["match"] for x in hashes.values()), len(hashes), max(x["abs_diff"] for x in comparisons.values()))


if __name__ == "__main__":
    main()
