from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _find_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in [Path.cwd(), *here.parents]:
        if (candidate / "src" / "config.py").exists():
            return candidate
    raise RuntimeError("Could not locate project root")


ROOT = _find_root()
sys.path.insert(0, str(ROOT))

from src import config as cfg  # noqa: E402
from src.data.loader import load_analyst_rc_pit, load_hk_hold  # noqa: E402
from src.data.universe import get_investable_universe, get_rebalance_dates  # noqa: E402
from src.factors.alt_factors import WINDOW_ANALYST, WINDOW_HK_HOLD, _window_start  # noqa: E402


OUT_DIR = ROOT / "current work" / "6.1"
ALT_FACTORS = ["analyst_eps_revision", "hk_hold_chg", "hk_hold_ratio"]
STATUS_ORDER = {"FAIL": 0, "WARN": 1, "PENDING": 2, "PASS": 3}


def _json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _item(check_id: str, title: str, status: str, summary: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "summary": summary,
        "details": details,
    }


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_function(text: str, name: str) -> str:
    match = re.search(rf"^def\s+{re.escape(name)}\s*\(", text, flags=re.MULTILINE)
    if not match:
        return ""
    next_match = re.search(r"^def\s+\w+\s*\(", text[match.end() :], flags=re.MULTILINE)
    if not next_match:
        return text[match.start() :]
    return text[match.start() : match.end() + next_match.start()]


def _date_list(values: list[pd.Timestamp]) -> list[str]:
    return [pd.Timestamp(v).strftime("%Y-%m-%d") for v in values]


def _run_git(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def check_preflight() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    mainline = _read_json(ROOT / "registry" / "mainline.json")
    ledger = _read_json(ROOT / "docs" / "logs" / "test_set_runs.json")

    active_run_id = mainline["active_run_id"]
    completed_runs = ledger.get("active_runs", [])
    expected_run_id = len(completed_runs) + 1
    expected_dir = ROOT / "runs" / "test" / f"test_run_{expected_run_id}__{active_run_id}"
    composite_path = expected_dir / "signal" / "composite.parquet"
    lock_path = expected_dir / "RUN_STARTED.json"

    if expected_dir.exists() and composite_path.exists():
        status = "FAIL"
        summary = "Expected run directory still contains signal/composite.parquet and would block rerun."
    else:
        status = "PASS"
        summary = "Expected run directory is absent, or does not contain a stale composite artifact."
    items.append(
        _item(
            "P-1",
            "old invalid run directory conflict",
            status,
            summary,
            {
                "mainline_run_id": active_run_id,
                "expected_run_id": expected_run_id,
                "expected_dir": expected_dir,
                "expected_dir_exists": expected_dir.exists(),
                "stale_composite_exists": composite_path.exists(),
            },
        )
    )

    if lock_path.exists():
        status = "FAIL"
        summary = "RUN_STARTED.json exists under the expected rerun directory."
    else:
        status = "PASS"
        summary = "No stale RUN_STARTED.json exists under the expected rerun directory."
    items.append(
        _item(
            "P-2",
            "RUN_STARTED lock check",
            status,
            summary,
            {
                "expected_dir": expected_dir,
                "lock_path": lock_path,
                "lock_exists": lock_path.exists(),
            },
        )
    )

    command = f"python scripts/run_test_pipeline.py --run-id {expected_run_id}"
    forbidden = ["--resume-from-lock", "--factor-panel-dir"]
    found_forbidden = [flag for flag in forbidden if flag in command]
    items.append(
        _item(
            "P-3",
            "rerun command shape",
            "PASS" if not found_forbidden else "FAIL",
            "Recommended rerun command does not include resume or custom panel flags."
            if not found_forbidden
            else "Recommended rerun command contains forbidden flags.",
            {
                "recommended_command": command,
                "forbidden_flags_found": found_forbidden,
            },
        )
    )
    return items


def _sample_test_dates(test_dates: list[pd.Timestamp]) -> list[pd.Timestamp]:
    wanted = [
        pd.Timestamp("2023-03-31"),
        pd.Timestamp("2023-12-29"),
        pd.Timestamp("2024-08-30"),
        pd.Timestamp("2025-10-31"),
        pd.Timestamp("2025-12-31"),
    ]
    available = set(test_dates)
    sample = [d for d in wanted if d in available]
    if len(sample) < 5:
        sample = list(test_dates[:: max(1, len(test_dates) // 5)])[:5]
    return sample


def check_hk_hold_pit(test_dates: list[pd.Timestamp]) -> dict[str, Any]:
    alt_source = _source(ROOT / "src" / "factors" / "alt_factors.py")
    loader_source = _source(ROOT / "src" / "data" / "loader.py")
    ratio_func = _extract_function(alt_source, "factor_hk_hold_ratio")
    chg_func = _extract_function(alt_source, "factor_hk_hold_chg")
    hk_loader = _extract_function(loader_source, "load_hk_hold")

    # 2026-06-02 季度化修复后更新：新实现用 pit_cutoff = rebalance_date - 10d 作为 end，
    # 比直接传 rebalance_date 更保守（含披露滞后偏移）。检查模式同步更新。
    source_patterns = {
        "ratio_uses_pit_cutoff_as_end": (
            "pit_cutoff" in ratio_func
            and "load_hk_hold(window_start, pit_cutoff" in ratio_func
        ),
        "chg_uses_pit_cutoff_as_end": (
            "pit_cutoff" in chg_func
            and "load_hk_hold(window_start, pit_cutoff" in chg_func
        ),
        "pit_cutoff_le_rebalance_date": (
            "rebalance_date - pd.Timedelta" in ratio_func
            and "pit_cutoff" in ratio_func
        ),
        "loader_uses_range_reader": '_read_parquet_range("hk_hold.parquet", start, end, codes)' in hk_loader,
        "negative_shift_in_hk_functions": bool(re.search(r"shift\s*\(\s*-", ratio_func + chg_func)),
    }

    # 季度化实现的采样窗口：pit_cutoff = T-10d，window_start = T-270d
    _PIT_OFFSET_DAYS = 10
    _QUARTERLY_WINDOW_DAYS = 270

    samples: list[dict[str, Any]] = []
    failures: list[str] = []
    warnings: list[str] = []
    for date in _sample_test_dates(test_dates):
        pit_cutoff = date - pd.Timedelta(days=_PIT_OFFSET_DAYS)
        window_start = date - pd.Timedelta(days=_QUARTERLY_WINDOW_DAYS)
        codes = list(get_investable_universe(date))[:80]
        ratio_raw = load_hk_hold(window_start, pit_cutoff, codes=codes)
        chg_raw = load_hk_hold(window_start, pit_cutoff, codes=codes)

        def _max_trade_date(df: pd.DataFrame) -> pd.Timestamp | None:
            if df.empty:
                return None
            return pd.Timestamp(df.index.get_level_values("trade_date").max())

        ratio_max = _max_trade_date(ratio_raw)
        chg_max = _max_trade_date(chg_raw)
        # PIT 违规：max_trade_date 必须 <= pit_cutoff（不得超过 T-10d）
        if ratio_max is not None and ratio_max > pit_cutoff:
            failures.append(
                f"{date.date()} ratio loader returned trade_date {ratio_max.date()} > pit_cutoff {pit_cutoff.date()}"
            )
        if chg_max is not None and chg_max > pit_cutoff:
            failures.append(
                f"{date.date()} chg loader returned trade_date {chg_max.date()} > pit_cutoff {pit_cutoff.date()}"
            )
        if ratio_raw.empty:
            warnings.append(f"{date.date()} quarterly 270-day ratio sample is empty (check hk_hold.parquet coverage)")

        samples.append(
            {
                "T": date,
                "pit_cutoff": pit_cutoff,
                "window_start": window_start,
                "sample_codes": codes[:3],
                "ratio_rows": len(ratio_raw),
                "ratio_max_trade_date": ratio_max,
                "chg_rows": len(chg_raw),
                "chg_max_trade_date": chg_max,
            }
        )

    if not source_patterns["ratio_uses_pit_cutoff_as_end"]:
        failures.append("factor_hk_hold_ratio does not use pit_cutoff as the loader end date")
    if not source_patterns["chg_uses_pit_cutoff_as_end"]:
        failures.append("factor_hk_hold_chg does not use pit_cutoff as the loader end date")
    if not source_patterns["pit_cutoff_le_rebalance_date"]:
        failures.append("pit_cutoff is not clearly derived from rebalance_date - pd.Timedelta in ratio function")
    if not source_patterns["loader_uses_range_reader"]:
        failures.append("load_hk_hold does not clearly use the shared date-range reader")
    if source_patterns["negative_shift_in_hk_functions"]:
        failures.append("negative shift detected in hk_hold factor functions")

    return {
        "source_patterns": source_patterns,
        "samples": samples,
        "failures": failures,
        "warnings": warnings,
    }


def check_analyst_pit(test_dates: list[pd.Timestamp]) -> dict[str, Any]:
    alt_source = _source(ROOT / "src" / "factors" / "alt_factors.py")
    loader_source = _source(ROOT / "src" / "data" / "loader.py")
    eps_func = _extract_function(alt_source, "factor_analyst_eps_revision")
    analyst_loader = _extract_function(loader_source, "load_analyst_rc_pit")

    source_patterns = {
        "factor_uses_pit_loader": "load_analyst_rc_pit(rebalance_date" in eps_func,
        "factor_splits_by_pit_date": 'rc["pit_date"]' in eps_func,
        "loader_filters_pit_le_rebalance": 'raw["pit_date"] <= rebalance_date' in analyst_loader,
        "loader_filters_lookback_start": 'raw["pit_date"] >= start_window' in analyst_loader,
        "negative_shift_in_function": bool(re.search(r"shift\s*\(\s*-", eps_func)),
    }

    samples: list[dict[str, Any]] = []
    failures: list[str] = []
    warnings: list[str] = []
    for date in _sample_test_dates(test_dates):
        codes = list(get_investable_universe(date))[:80]
        rc = load_analyst_rc_pit(date, codes=codes, lookback_days=WINDOW_ANALYST)
        if rc.empty:
            warnings.append(f"{date.date()} analyst_rc_pit sample is empty for first 80 investable codes")
            min_pit = None
            max_pit = None
        else:
            min_pit = pd.Timestamp(rc["pit_date"].min())
            max_pit = pd.Timestamp(rc["pit_date"].max())
            start_window = date - pd.Timedelta(days=WINDOW_ANALYST)
            if max_pit > date:
                failures.append(f"{date.date()} analyst loader returned future pit_date {max_pit.date()}")
            if min_pit < start_window:
                failures.append(f"{date.date()} analyst loader returned pit_date before lookback window {min_pit.date()}")
        samples.append(
            {
                "T": date,
                "sample_codes": codes[:3],
                "rows": len(rc),
                "min_pit_date": min_pit,
                "max_pit_date": max_pit,
            }
        )

    if not source_patterns["factor_uses_pit_loader"]:
        failures.append("factor_analyst_eps_revision does not clearly use load_analyst_rc_pit")
    if not source_patterns["factor_splits_by_pit_date"]:
        failures.append("factor_analyst_eps_revision does not clearly split on pit_date")
    if not source_patterns["loader_filters_pit_le_rebalance"]:
        failures.append("load_analyst_rc_pit does not clearly filter pit_date <= rebalance_date")
    if source_patterns["negative_shift_in_function"]:
        failures.append("negative shift detected in factor_analyst_eps_revision")

    return {
        "source_patterns": source_patterns,
        "samples": samples,
        "failures": failures,
        "warnings": warnings,
    }


def check_alt_nan_quality(test_dates: list[pd.Timestamp]) -> dict[str, Any]:
    panel_dir = ROOT / "data" / "processed" / "factor_panels_test_run_1"
    failures: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {}
    thresholds = {
        "analyst_eps_revision": 100,
        "hk_hold_chg": 50,
        "hk_hold_ratio": 50,
    }
    for factor in ALT_FACTORS:
        path = panel_dir / f"{factor}.parquet"
        if not path.exists():
            failures.append(f"{factor}: panel file is missing")
            continue
        df = pd.read_parquet(path)
        test_slice = df[df.index.isin(test_dates)]
        valid_by_date = test_slice.notna().sum(axis=1)
        zero_valid_dates = [pd.Timestamp(d) for d in valid_by_date[valid_by_date == 0].index]
        avg_valid = float(valid_by_date.mean()) if len(valid_by_date) else 0.0
        min_valid = int(valid_by_date.min()) if len(valid_by_date) else 0
        threshold = thresholds[factor]
        if avg_valid < threshold:
            failures.append(f"{factor}: avg_valid_stocks {avg_valid:.1f} < threshold {threshold}")
        if zero_valid_dates:
            warnings.append(f"{factor}: {len(zero_valid_dates)} all-NaN test rows")
        stats[factor] = {
            "rows": len(df),
            "date_min": pd.Timestamp(df.index.min()) if len(df.index) else None,
            "date_max": pd.Timestamp(df.index.max()) if len(df.index) else None,
            "test_rows": len(test_slice),
            "nan_rate_test": float(test_slice.isna().mean().mean()) if not test_slice.empty else None,
            "avg_valid_stocks": avg_valid,
            "min_valid_stocks": min_valid,
            "threshold": threshold,
            "zero_valid_dates": _date_list(zero_valid_dates),
        }
    return {"panel_dir": panel_dir, "factors": stats, "failures": failures, "warnings": warnings}


def _index_dates(df: pd.DataFrame) -> pd.DatetimeIndex:
    if isinstance(df.index, pd.MultiIndex):
        raw = df.index.get_level_values(0)
    else:
        raw = df.index
    return pd.DatetimeIndex(pd.to_datetime(raw)).sort_values().unique()


def validate_post_run_artifacts(run_dir: Path) -> dict[str, Any]:
    test_dates = list(get_rebalance_dates(start=cfg.TEST_START, end=cfg.TEST_END))
    failures: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {"run_dir": run_dir}

    comp = pd.read_parquet(run_dir / "signal" / "composite.parquet")
    comp_dates = _index_dates(comp)
    comp_test_dates = [d for d in test_dates if d in set(comp_dates)]
    comp_nonempty_test = comp.loc[comp.index.isin(test_dates)].notna().any(axis=1) if not isinstance(comp.index, pd.MultiIndex) else pd.Series(dtype=bool)
    if len(comp_test_dates) != len(test_dates):
        failures.append(f"composite covers {len(comp_test_dates)}/{len(test_dates)} test rebalance dates")
    if not comp_nonempty_test.empty and not bool(comp_nonempty_test.all()):
        failures.append("composite has all-NaN rows in the test period")
    details["composite"] = {
        "rows": len(comp),
        "date_min": comp_dates.min() if len(comp_dates) else None,
        "date_max": comp_dates.max() if len(comp_dates) else None,
        "test_dates_covered": len(comp_test_dates),
        "all_test_rows_nonempty": None if comp_nonempty_test.empty else bool(comp_nonempty_test.all()),
    }

    meta = pd.read_parquet(run_dir / "portfolio" / "optimizer_meta.parquet")
    meta_dates = _index_dates(meta)
    meta_test = meta.loc[meta.index.isin(test_dates)] if not isinstance(meta.index, pd.MultiIndex) else meta
    if len(meta_test) < len(test_dates):
        failures.append(f"optimizer_meta has {len(meta_test)} test rows, expected at least {len(test_dates)}")
    if "solver_status" in meta_test.columns and not (meta_test["solver_status"] == "topn_ew_forced").all():
        failures.append("optimizer_meta solver_status is not fully topn_ew_forced")
    if "fallback_level" in meta_test.columns and not (meta_test["fallback_level"] == 2).all():
        failures.append("optimizer_meta fallback_level is not fully 2 for TopN EW")
    if "n_holdings" in meta_test.columns:
        n_hold_min = int(meta_test["n_holdings"].min()) if len(meta_test) else None
        n_hold_max = int(meta_test["n_holdings"].max()) if len(meta_test) else None
        if n_hold_min is not None and (n_hold_min < 120 or n_hold_max > 180):
            warnings.append(f"n_holdings range {n_hold_min}-{n_hold_max} is far from target topn=150")
    else:
        n_hold_min = None
        n_hold_max = None
    details["optimizer_meta"] = {
        "rows": len(meta),
        "test_rows": len(meta_test),
        "date_min": meta_dates.min() if len(meta_dates) else None,
        "date_max": meta_dates.max() if len(meta_dates) else None,
        "solver_status_counts": meta_test["solver_status"].value_counts().to_dict() if "solver_status" in meta_test.columns else {},
        "fallback_level_counts": meta_test["fallback_level"].value_counts().to_dict() if "fallback_level" in meta_test.columns else {},
        "n_holdings_min": n_hold_min,
        "n_holdings_max": n_hold_max,
    }

    trades = pd.read_parquet(run_dir / "backtest" / "trades_valid.parquet")
    trade_activity_cols = [c for c in ["buy_value", "sell_value", "turnover"] if c in trades.columns]
    has_trade_activity = False
    if trade_activity_cols:
        has_trade_activity = bool((trades[trade_activity_cols].fillna(0).abs().sum(axis=1) > 0).any())
    if len(trades) <= 10:
        failures.append(f"trades_valid has only {len(trades)} rows")
    if trade_activity_cols and not has_trade_activity:
        failures.append("trades_valid has no buy/sell/turnover activity")
    details["trades"] = {
        "rows": len(trades),
        "activity_columns": trade_activity_cols,
        "has_trade_activity": has_trade_activity,
    }

    weights = pd.read_parquet(run_dir / "backtest" / "actual_weights_valid.parquet")
    weight_dates = _index_dates(weights)
    dynamic_l1 = None
    if len(weight_dates) > 10:
        try:
            if isinstance(weights.index, pd.MultiIndex):
                w1 = weights.xs(weight_dates[0], level=0)
                w2 = weights.xs(weight_dates[min(30, len(weight_dates) - 1)], level=0)
            else:
                w1 = weights.loc[weight_dates[0]]
                w2 = weights.loc[weight_dates[min(30, len(weight_dates) - 1)]]
            if isinstance(w1, pd.DataFrame):
                w1 = w1.squeeze()
            if isinstance(w2, pd.DataFrame):
                w2 = w2.squeeze()
            common = w1.index.union(w2.index)
            dynamic_l1 = float((w1.reindex(common).fillna(0) - w2.reindex(common).fillna(0)).abs().sum())
            if dynamic_l1 <= 0.01:
                failures.append(f"actual_weights appear static; L1 difference is {dynamic_l1:.6f}")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not compute actual_weights dynamic L1 difference: {exc}")
    else:
        failures.append(f"actual_weights has too few unique dates: {len(weight_dates)}")
    details["actual_weights"] = {
        "rows": len(weights),
        "unique_dates": len(weight_dates),
        "date_min": weight_dates.min() if len(weight_dates) else None,
        "date_max": weight_dates.max() if len(weight_dates) else None,
        "dynamic_l1_sample": dynamic_l1,
    }

    details["failures"] = failures
    details["warnings"] = warnings
    return details


def check_l5() -> list[dict[str, Any]]:
    test_dates = list(get_rebalance_dates(start=cfg.TEST_START, end=cfg.TEST_END))
    hk = check_hk_hold_pit(test_dates)
    analyst = check_analyst_pit(test_dates)
    nan_quality = check_alt_nan_quality(test_dates)

    items: list[dict[str, Any]] = []
    items.append(
        _item(
            "L5-1",
            "hk_hold PIT date discipline",
            "FAIL" if hk["failures"] else ("WARN" if hk["warnings"] else "PASS"),
            "hk_hold loaders and factor functions do not return future trade_date values."
            if not hk["failures"]
            else "hk_hold PIT validation found future-date or source-pattern failures.",
            hk,
        )
    )
    items.append(
        _item(
            "L5-2",
            "analyst_eps_revision PIT date discipline",
            "FAIL" if analyst["failures"] else ("WARN" if analyst["warnings"] else "PASS"),
            "analyst EPS revision uses pit_date <= T and no future pit_date was found in samples."
            if not analyst["failures"]
            else "analyst EPS revision PIT validation found future-date or source-pattern failures.",
            analyst,
        )
    )
    items.append(
        _item(
            "L5-3",
            "repaired ALT factor NaN quality",
            "FAIL" if nan_quality["failures"] else ("WARN" if nan_quality["warnings"] else "PASS"),
            "Average valid-stock coverage passes thresholds; all-NaN hk_hold months remain a signal-quality warning."
            if not nan_quality["failures"]
            else "At least one repaired ALT factor has insufficient average valid-stock coverage.",
            nan_quality,
        )
    )
    return items


def check_l6_gate() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    mainline = _read_json(ROOT / "registry" / "mainline.json")
    ledger = _read_json(ROOT / "docs" / "logs" / "test_set_runs.json")
    expected_run_id = len(ledger.get("active_runs", [])) + 1
    run_dir = ROOT / "runs" / "test" / f"test_run_{expected_run_id}__{mainline['active_run_id']}"

    status_code, status_out, status_err = _run_git(["status", "--short"])
    log_code, log_out, log_err = _run_git(["log", "-1", "--oneline"])
    dirty_lines = [line for line in status_out.splitlines() if line.strip()]
    has_test_tag = f"[TEST_SET_RUN_{expected_run_id}]" in log_out

    if status_code != 0:
        status = "WARN"
        summary = "Could not read git status cleanly; commit readiness must be checked manually."
    elif dirty_lines:
        status = "PENDING"
        summary = "Working tree is dirty; make a deliberate pre-test commit before the formal test run."
    elif not has_test_tag:
        status = "PENDING"
        summary = "Working tree is clean, but latest commit does not contain the required test-set tag."
    else:
        status = "PASS"
        summary = "Latest commit contains the expected test-set tag and working tree is clean."
    items.append(
        _item(
            "L6-1",
            "pre-test git commit gate",
            status,
            summary,
            {
                "expected_tag": f"[TEST_SET_RUN_{expected_run_id}]",
                "git_status_returncode": status_code,
                "git_status_stderr": status_err,
                "dirty_line_count": len(dirty_lines),
                "dirty_preview": dirty_lines[:20],
                "git_log_returncode": log_code,
                "git_log_stderr": log_err,
                "latest_commit": log_out,
                "latest_commit_has_expected_tag": has_test_tag,
            },
        )
    )

    items.append(
        _item(
            "L6-2",
            "formal test pipeline run",
            "PASS" if run_dir.exists() else "PENDING",
            "Expected test run directory exists."
            if run_dir.exists()
            else "Formal test pipeline was intentionally not executed by this inspection script.",
            {
                "command_to_run_after_user_approval": f"python scripts/run_test_pipeline.py --run-id {expected_run_id}",
                "must_not_use": ["--resume-from-lock", "--factor-panel-dir"],
                "would_consume_effective_test_run": True,
                "expected_run_dir": run_dir,
                "expected_run_dir_exists": run_dir.exists(),
            },
        )
    )

    expected_outputs = {
        "composite": run_dir / "signal" / "composite.parquet",
        "optimizer_meta": run_dir / "portfolio" / "optimizer_meta.parquet",
        "trades": run_dir / "backtest" / "trades_valid.parquet",
        "actual_weights": run_dir / "backtest" / "actual_weights_valid.parquet",
    }
    existing_outputs = {name: path.exists() for name, path in expected_outputs.items()}
    if all(existing_outputs.values()):
        post_details = validate_post_run_artifacts(run_dir)
        status = "FAIL" if post_details["failures"] else ("WARN" if post_details["warnings"] else "PASS")
        summary = (
            "Post-run artifacts passed key coverage/activity checks."
            if status == "PASS"
            else "Post-run artifacts exist but have validation issues."
        )
        post_details["expected_outputs"] = expected_outputs
        post_details["existing_outputs"] = existing_outputs
    else:
        status = "PENDING"
        summary = "Post-run artifacts are not expected until the formal test pipeline finishes."
        post_details = {
            "expected_run_dir": run_dir,
            "expected_outputs": expected_outputs,
            "existing_outputs": existing_outputs,
        }
    items.append(_item("L6-3", "post-run artifact validation", status, summary, post_details))

    items.append(
        _item(
            "L6-4",
            "post-run project state updates",
            "PENDING",
            "CLAUDE.md/test ledger/result analysis updates are intentionally deferred until a real test run completes.",
            {
                "updates_after_successful_run": [
                    "CLAUDE.md test-set run count",
                    "CLAUDE.md IR and hard-metric result",
                    "docs/logs/test_set_runs.json verification",
                    "current work/6.1 analysis report refresh if result differs from invalid 0.149 run",
                ],
            },
        )
    )
    return items


def render_markdown(results: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Remaining Pre-Test Inspection Report")
    lines.append("")
    lines.append(f"- Generated: {results['generated_at']}")
    lines.append(f"- Root: `{results['root']}`")
    summary = results["summary"]
    lines.append(
        "- Summary: "
        + " ".join(f"{k}={summary.get(k, 0)}" for k in ["PASS", "WARN", "FAIL", "PENDING"])
    )
    lines.append("- Note: L6 formal test execution was not run by this inspection.")
    lines.append("")
    for phase in results["phases"]:
        lines.append(f"## {phase['name']}")
        lines.append("")
        for item in phase["items"]:
            lines.append(f"### {item['id']} - {item['title']}: {item['status']}")
            lines.append("")
            lines.append(item["summary"])
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(item["details"], ensure_ascii=False, indent=2, default=_json_default))
            lines.append("```")
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    phases = [
        {"name": "Preflight", "items": check_preflight()},
        {"name": "L5", "items": check_l5()},
        {"name": "L6 Gate", "items": check_l6_gate()},
    ]
    all_items = [item for phase in phases for item in phase["items"]]
    summary: dict[str, int] = {"PASS": 0, "WARN": 0, "FAIL": 0, "PENDING": 0}
    for item in all_items:
        summary[item["status"]] = summary.get(item["status"], 0) + 1

    results = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "root": ROOT,
        "summary": summary,
        "phases": phases,
    }

    json_path = OUT_DIR / "remaining_inspection_results.json"
    md_path = OUT_DIR / "remaining_inspection_report.md"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    md_path.write_text(render_markdown(results), encoding="utf-8")

    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")
    print(
        "Summary: "
        + " ".join(f"{k}={summary.get(k, 0)}" for k in ["PASS", "WARN", "FAIL", "PENDING"])
    )
    return 1 if summary.get("FAIL", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
