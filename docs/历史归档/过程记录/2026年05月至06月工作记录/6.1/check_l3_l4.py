from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class CheckResult:
    group: str
    check_id: str
    name: str
    status: str
    message: str
    details: dict[str, Any]


def find_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "src" / "config.py").exists():
            return parent
    raise RuntimeError("Cannot locate repository root from script path.")


ROOT = find_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config as cfg  # noqa: E402
from src.backtest.transaction import compute_trade_cost, stamp_duty_rate  # noqa: E402
from src.data.loader import get_rebalance_dates  # noqa: E402
from src.evaluation.ic_analysis import build_exit_date_map  # noqa: E402
from scripts.test_set_ledger import count_test_set_runs, load_test_set_ledger  # noqa: E402


REPORT_MD = ROOT / "current work" / "6.1" / "l3_l4_inspection_report.md"
REPORT_JSON = ROOT / "current work" / "6.1" / "l3_l4_inspection_results.json"
L1_L2_JSON = ROOT / "current work" / "6.1" / "l1_l2_inspection_results.json"
TEST_PANEL_DIR = cfg.DATA_PROC / "factor_panels_test_run_1"
RUN_TEST_PIPELINE = ROOT / "scripts" / "run_test_pipeline.py"
RIDGE_COMBINER = ROOT / "experiments" / "legacy" / "ridge_signal" / "ridge_combiner.py"
ROLLING_COMBINER = ROOT / "experiments" / "legacy" / "ridge_rolling" / "rolling_combiner.py"
OPTIMIZER = ROOT / "src" / "portfolio" / "optimizer.py"
TRANSACTION = ROOT / "src" / "backtest" / "transaction.py"
MAINLINE_JSON = ROOT / "registry" / "mainline.json"


def to_jsonable(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return str(value.date())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        if np.isnan(value):
            return None
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, pd.Index, pd.DatetimeIndex)):
        return [to_jsonable(v) for v in list(value)]
    return value


def add_result(
    results: list[CheckResult],
    group: str,
    check_id: str,
    name: str,
    status: str,
    message: str,
    **details: Any,
) -> None:
    results.append(
        CheckResult(
            group=group,
            check_id=check_id,
            name=name,
            status=status,
            message=message,
            details=to_jsonable(details),
        )
    )


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_contains(path: Path, patterns: dict[str, str]) -> dict[str, bool]:
    text = read_text(path)
    return {name: bool(re.search(pattern, text, flags=re.S)) for name, pattern in patterns.items()}


def all_factor_dates_from_pipeline_dir() -> pd.DatetimeIndex:
    dates: set[pd.Timestamp] = set()
    for fp in TEST_PANEL_DIR.glob("*.parquet"):
        try:
            idx = pd.read_parquet(fp, columns=[]).index
        except Exception:
            idx = pd.read_parquet(fp).index
        dates.update(pd.Timestamp(d) for d in idx)
    return pd.DatetimeIndex(sorted(dates))


def l3_1_predict_cross_section(results: list[CheckResult]) -> None:
    patterns = source_contains(
        RIDGE_COMBINER,
        {
            "missing_factor_returns_none": r"if\s+T\s+not\s+in\s+factor_panels\[name\]\.index:\s+.*?return\s+None",
            "too_few_stocks_returns_none": r"predict_mask\.sum\(\)\s*<\s*10:\s+.*?return\s+None",
        },
    )
    l1_5_status = None
    l1_5_failures: dict[str, Any] | None = None
    if L1_L2_JSON.exists():
        l1_l2 = load_json(L1_L2_JSON)
        for row in l1_l2.get("results", []):
            if row.get("check_id") == "L1-5":
                l1_5_status = row.get("status")
                l1_5_failures = row.get("details", {}).get("failures", {})
                break
    failures: list[str] = []
    if not patterns["missing_factor_returns_none"]:
        failures.append("Cannot locate missing-factor return None path in RidgeCombiner.")
    if not patterns["too_few_stocks_returns_none"]:
        failures.append("Cannot locate too-few-stocks return None path in RidgeCombiner.")
    if l1_5_status not in {"PASS", "WARN"} or l1_5_failures:
        failures.append("L1-5 did not confirm predict_cross_section compatibility.")
    add_result(
        results,
        "L3",
        "L3-1",
        "_predict_cross_section None path sealed by data",
        "PASS" if not failures else "FAIL",
        "Known None-return paths exist, and L1-5 confirms test-period data will not trigger them."
        if not failures else "The known None-return path is not fully sealed by prior data checks.",
        source=str(RIDGE_COMBINER),
        source_patterns=patterns,
        l1_5_status=l1_5_status,
        l1_5_failures=l1_5_failures,
        failures=failures,
    )


def l3_2_signal_range(results: list[CheckResult]) -> None:
    patterns = source_contains(
        RUN_TEST_PIPELINE,
        {
            "tv_dates_limited_to_valid_end": r"tv_dates\s*=\s*pd\.DatetimeIndex\(\[d\s+for\s+d\s+in\s+all_dates\s+if\s+d\s*<=\s*valid_end\]\)",
            "tv_panels_limited_to_valid_end": r"tv_panels\s*=\s*\{k:\s*v\.loc\[v\.index\s*<=\s*valid_end\]",
            "tv_fwd_limited_to_valid_end": r"tv_fwd\s*=\s*fwd_ret_panel\.loc\[fwd_ret_panel\.index\s*<=\s*valid_end\]",
            "alpha_uses_tv_only": r"select_alpha_walk_forward\(tv_panels,\s*tv_fwd,\s*tv_dates\)",
            "ridge_build_uses_full_inputs": r"build_ridge_panel\(\s*factor_panels,\s*fwd_ret_panel,\s*pd\.DatetimeIndex\(all_dates\)",
            "metadata_records_full_range": r'"date_range":\s*\[str\(all_dates\[0\]\.date\(\)\),\s*str\(all_dates\[-1\]\.date\(\)\)\]',
        },
    )
    failures = [name for name, ok in patterns.items() if not ok]
    add_result(
        results,
        "L3",
        "L3-2",
        "ridge signal TV isolation and full test range",
        "PASS" if not failures else "FAIL",
        "Alpha selection is restricted to train/valid inputs, while signal generation uses the full panel range."
        if not failures else "Ridge signal range or TV isolation code does not match the expected contract.",
        source=str(RUN_TEST_PIPELINE),
        source_patterns=patterns,
        failures=failures,
    )


def simulate_fwd_extension() -> dict[str, Any]:
    all_dates = all_factor_dates_from_pipeline_dir()
    public_fwd = pd.read_parquet(cfg.DATA_PROC / "fwd_ret_panel.parquet")
    public_fwd.index = pd.DatetimeIndex(pd.to_datetime(public_fwd.index))
    existing_dates = set(public_fwd.index)
    new_t_dates = pd.DatetimeIndex([d for d in all_dates[:-1] if d not in existing_dates])
    computation_dates = pd.DatetimeIndex(list(new_t_dates) + [all_dates[-1]]) if len(new_t_dates) else pd.DatetimeIndex([])
    exit_map = build_exit_date_map(list(computation_dates)) if len(computation_dates) else pd.Series(dtype="datetime64[ns]")
    test_dates = pd.DatetimeIndex(get_rebalance_dates(start=cfg.TEST_START, end=cfg.TEST_END))
    universe_counts: dict[str, int] = {}
    index_member = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    member_dates = set(pd.DatetimeIndex(index_member.index.get_level_values("rebalance_date").unique()))
    for T in new_t_dates:
        if T in member_dates:
            universe_counts[str(T.date())] = int(len(index_member.loc[T]))
        else:
            universe_counts[str(T.date())] = 0

    # Live retraining availability check for rolling48 + purge=2:
    # every label used to fit signal at T must have exit_date < T.
    violations: dict[str, list[str]] = {}
    window_months = 48
    purge_months = 2
    full_exit_map = build_exit_date_map(list(all_dates))
    for T in [d for d in all_dates if d >= cfg.TEST_START and d <= cfg.TEST_END]:
        cutoff = T - pd.DateOffset(months=purge_months)
        window_start = cutoff - pd.DateOffset(months=window_months)
        train_dates = [d for d in all_dates if d <= cutoff and d > window_start]
        bad_train_dates = []
        for d in train_dates:
            exit_date = full_exit_map.get(d, pd.NaT)
            if pd.isna(exit_date):
                continue
            if pd.Timestamp(exit_date) >= T:
                bad_train_dates.append(f"{pd.Timestamp(d).date()}->{pd.Timestamp(exit_date).date()}")
        if bad_train_dates:
            violations[str(T.date())] = bad_train_dates

    return {
        "all_dates_count": len(all_dates),
        "all_dates_first": all_dates[0] if len(all_dates) else None,
        "all_dates_last": all_dates[-1] if len(all_dates) else None,
        "public_fwd_first": public_fwd.index.min(),
        "public_fwd_last": public_fwd.index.max(),
        "public_fwd_rows": len(public_fwd),
        "new_t_count": len(new_t_dates),
        "new_t_first": new_t_dates[0] if len(new_t_dates) else None,
        "new_t_last": new_t_dates[-1] if len(new_t_dates) else None,
        "new_t_dates": list(new_t_dates),
        "computation_dates_last": computation_dates[-1] if len(computation_dates) else None,
        "exit_map_sample": {str(k.date()): str(v.date()) for k, v in exit_map.tail(5).items()} if len(exit_map) else {},
        "test_rebalance_count": len(test_dates),
        "min_universe_count_for_new_dates": min(universe_counts.values()) if universe_counts else None,
        "zero_universe_dates": [k for k, v in universe_counts.items() if v == 0],
        "label_availability_violations": violations,
    }


def l3_3_fwd_extension(results: list[CheckResult]) -> None:
    patterns = source_contains(
        RUN_TEST_PIPELINE,
        {
            "reads_public_cache": r"existing\s*=\s*pd\.read_parquet\(PUBLIC_FWD_CACHE\)",
            "uses_all_dates_excluding_last": r"new_t_dates\s*=\s*\[d\s+for\s+d\s+in\s+all_dates\[:-1\]\s+if\s+d\s+not\s+in\s+existing_dates\]",
            "adds_final_date_for_exit": r"computation_dates\s*=\s*new_t_dates\s*\+\s*\[all_dates\[-1\]\]",
            "uses_dynamic_universe": r"codes_by_date\[T\]\s*=\s*get_investable_universe\(T\)\.tolist\(\)",
            "passes_codes_by_date": r"codes_by_date\s*=\s*codes_by_date",
            "writes_test_run_dir_only": r"test_fwd_path\s*=\s*output_dir\s*/\s*\"fwd_ret_panel\.parquet\".*?merged\.to_parquet\(test_fwd_path\)",
        },
    )
    simulation = simulate_fwd_extension()
    failures = [name for name, ok in patterns.items() if not ok]
    warnings: list[str] = []
    if simulation["new_t_count"] != 36:
        failures.append("expected 36 new forward-return labels from 2022-12 through 2025-11")
    if simulation["new_t_last"] is None or pd.Timestamp(simulation["new_t_last"]) < pd.Timestamp("2025-11-01"):
        failures.append("new forward-return labels do not extend through 2025-11")
    if simulation["min_universe_count_for_new_dates"] is None or simulation["min_universe_count_for_new_dates"] <= 0:
        failures.append("at least one new forward-return date has no investable universe")
    if simulation["label_availability_violations"]:
        failures.append("rolling training would use labels whose exit_date is not before prediction date")
    if simulation["new_t_first"] is not None and pd.Timestamp(simulation["new_t_first"]) <= cfg.VALID_END:
        warnings.append(
            "extension includes the 2022-12 label; this is acceptable for later live rolling retraining only "
            "because the availability simulation verifies exit_date < prediction T"
        )
    status = "FAIL" if failures else ("WARN" if warnings else "PASS")
    add_result(
        results,
        "L3",
        "L3-3",
        "forward-return extension and label availability",
        status,
        "fwd_ret extension writes only to the test run directory and rolling training labels are available before prediction."
        if status == "PASS"
        else (
            "fwd_ret extension is mechanically safe, with an explicit warning on the 2022-12 boundary label."
            if status == "WARN"
            else "fwd_ret extension or label availability has a blocking issue."
        ),
        source=str(RUN_TEST_PIPELINE),
        source_patterns=patterns,
        simulation=simulation,
        failures=failures,
        warnings=warnings,
    )


def l3_4_topn_metadata(results: list[CheckResult]) -> None:
    patterns = source_contains(
        OPTIMIZER,
        {
            "function_exists": r"def\s+optimize_topn_equal_weight_all_periods\(",
            "fallback_level_2": r'"fallback_level":\s*2',
            "solver_status_forced": r'"solver_status":\s*"topn_ew_forced"',
            "optimizer_mode_topn": r'"optimizer_mode":\s*"topn_ew"',
            "n_holdings_recorded": r'"n_holdings":\s*n_holdings',
            "constraint_compliant_recorded": r'"constraint_compliant":\s*bool\(compliant\)',
        },
    )
    failures = [name for name, ok in patterns.items() if not ok]
    add_result(
        results,
        "L3",
        "L3-4",
        "TopN EW optimizer metadata contract",
        "PASS" if not failures else "FAIL",
        "TopN EW metadata is expected to report fallback_level=2 and solver_status=topn_ew_forced."
        if not failures else "TopN EW metadata does not match the expected audit contract.",
        source=str(OPTIMIZER),
        source_patterns=patterns,
        failures=failures,
    )


def l3_5_ledger_write(results: list[CheckResult]) -> None:
    patterns = source_contains(
        RUN_TEST_PIPELINE,
        {
            "ledger_guard_count": r"completed\s*=\s*count_test_set_runs\(\)",
            "run_id_guard": r"args\.run_id\s*!=\s*completed\s*\+\s*1",
            "run_tag_from_run_id": r"run_tag\s*=\s*f\"\[TEST_SET_RUN_\{args\.run_id\}\]\"",
            "record_finished": r"record_test_set_run\(args\.run_id,\s*status=\"finished\",\s*git_commit=git_commit\)",
            "record_after_finished_lock": r"_write_test_set_run_lock\([\s\S]*?status=\"finished\"[\s\S]*?\)\s*# ── ledger 记录[\s\S]*?record_test_set_run",
        },
    )
    ledger = load_test_set_ledger()
    completed = count_test_set_runs()
    failures = [name for name, ok in patterns.items() if not ok]
    if completed != 0:
        failures.append("ledger active run count is not 0; expected next run-id may not be 1")
    add_result(
        results,
        "L3",
        "L3-5",
        "test_set_runs ledger guard and write timing",
        "PASS" if not failures else "FAIL",
        "run_id guard expects the next active run, and successful completion records the run in ledger."
        if not failures else "Ledger guard/write behavior is not ready for run-id=1.",
        source=str(RUN_TEST_PIPELINE),
        source_patterns=patterns,
        completed_runs=completed,
        expected_next_run_id=completed + 1,
        ledger=ledger,
        failures=failures,
    )


def l3_6_stamp_duty(results: list[CheckResult]) -> None:
    patterns = source_contains(
        TRANSACTION,
        {
            "uses_config_cut_date": r"trade_date\s*>=\s*cfg\.STAMP_DUTY_CUT_DATE",
            "uses_before_rate": r"return\s+cfg\.STAMP_DUTY_BEFORE",
            "uses_after_rate": r"return\s+cfg\.STAMP_DUTY_AFTER",
            "sell_only_stamp": r"stamp_duty\s*=\s*sell_value\s*\*\s*stamp_duty_rate\(trade_date\)",
            "commission_both_sides": r"commission\s*=\s*\(sell_value\s*\+\s*buy_value\)\s*\*\s*cfg\.COMMISSION_RATE",
            "slippage_both_sides": r"slippage\s*=\s*\(sell_value\s*\+\s*buy_value\)\s*\*\s*cfg\.SLIPPAGE_RATE",
        },
    )
    before = stamp_duty_rate(pd.Timestamp("2023-08-27"))
    cut = stamp_duty_rate(pd.Timestamp("2023-08-28"))
    after = stamp_duty_rate(pd.Timestamp("2023-08-29"))
    cost_before = compute_trade_cost(1.0, 1.0, pd.Timestamp("2023-08-27"))
    cost_after = compute_trade_cost(1.0, 1.0, pd.Timestamp("2023-08-28"))
    failures = [name for name, ok in patterns.items() if not ok]
    if before != cfg.STAMP_DUTY_BEFORE:
        failures.append("pre-cut stamp duty rate mismatch")
    if cut != cfg.STAMP_DUTY_AFTER or after != cfg.STAMP_DUTY_AFTER:
        failures.append("post-cut stamp duty rate mismatch")
    if not cost_before > cost_after:
        failures.append("transaction cost did not decrease after stamp duty cut")
    add_result(
        results,
        "L3",
        "L3-6",
        "stamp duty date switch and cost formula",
        "PASS" if not failures else "FAIL",
        "Stamp duty switches on 2023-08-28, applies sell-side only, with commission/slippage on both sides."
        if not failures else "Transaction-cost stamp duty logic is not compliant.",
        source=str(TRANSACTION),
        source_patterns=patterns,
        stamp_duty_rates={
            "2023-08-27": before,
            "2023-08-28": cut,
            "2023-08-29": after,
        },
        cost_for_sell1_buy1={
            "2023-08-27": cost_before,
            "2023-08-28": cost_after,
        },
        failures=failures,
    )


def l4_1_mainline(results: list[CheckResult]) -> dict[str, Any] | None:
    if not MAINLINE_JSON.exists():
        add_result(results, "L4", "L4-1", "mainline registry", "FAIL", "registry/mainline.json is missing.", path=MAINLINE_JSON)
        return None
    mainline = load_json(MAINLINE_JSON)
    active_run_id = mainline.get("active_run_id")
    run_dir = ROOT / "runs" / "train_valid" / str(active_run_id)
    run_config = run_dir / "run_config.json"
    failures: list[str] = []
    warnings: list[str] = []
    if not active_run_id:
        failures.append("active_run_id missing")
    if not run_dir.exists():
        failures.append("active_run_id directory missing under runs/train_valid")
    if not run_config.exists():
        failures.append("active run_config.json missing")
    if mainline.get("scope") != "train_valid":
        failures.append("mainline scope is not train_valid")
    if mainline.get("test_set_used") is not False:
        warnings.append("mainline.test_set_used is not false")
    status = "FAIL" if failures else ("WARN" if warnings else "PASS")
    add_result(
        results,
        "L4",
        "L4-1",
        "mainline registry",
        status,
        "mainline registry points to an existing train_valid run and is not marked as test-set used."
        if status == "PASS" else "Review mainline registry before running the test pipeline.",
        mainline=mainline,
        run_dir=run_dir,
        run_config=run_config,
        failures=failures,
        warnings=warnings,
    )
    return mainline if not failures else None


def l4_2_run_config(results: list[CheckResult], mainline: dict[str, Any] | None) -> None:
    if not mainline:
        add_result(results, "L4", "L4-2", "mainline run_config parameters", "FAIL", "Cannot inspect run_config because L4-1 failed.")
        return
    run_config_path = ROOT / "runs" / "train_valid" / str(mainline["active_run_id"]) / "run_config.json"
    if not run_config_path.exists():
        add_result(results, "L4", "L4-2", "mainline run_config parameters", "FAIL", "run_config.json is missing.", path=run_config_path)
        return
    rc = load_json(run_config_path)
    spec = rc.get("spec", {})
    signal = spec.get("signal", {})
    optimizer = spec.get("optimizer", {})
    backtest = spec.get("backtest", {})
    expected = {
        "signal.method": ("ridge", signal.get("method")),
        "signal.training_mode": ("rolling", signal.get("training_mode")),
        "signal.window_months": (48, signal.get("window_months")),
        "signal.purge_months": (2, signal.get("purge_months")),
        "optimizer.optimizer_mode": ("topn_ew", optimizer.get("optimizer_mode")),
        "optimizer.topn": (150, optimizer.get("topn")),
        "optimizer.te_target_annual": (0.06, optimizer.get("te_target_annual")),
        "backtest.execution": ("tplus1_open", backtest.get("execution")),
        "backtest.benchmark": ("CSI500_TOTAL_RETURN", backtest.get("benchmark")),
        "allow_test_set": (False, spec.get("allow_test_set")),
    }
    mismatches = {
        key: {"expected": exp, "actual": actual}
        for key, (exp, actual) in expected.items()
        if actual != exp
    }
    warnings: list[str] = []
    if signal.get("target") == "excess_return":
        warnings.append(
            "signal.target is excess_return, but the current RidgeCombiner construction does not pass use_excess_return=True; "
            "this appears to match the promoted train_valid run, so do not change it before the test run without re-promoting mainline."
        )
    status = "FAIL" if mismatches else ("WARN" if warnings else "PASS")
    add_result(
        results,
        "L4",
        "L4-2",
        "mainline run_config parameters",
        status,
        "Mainline run_config matches ridge/rolling48/topn150 topn_ew test-run expectations."
        if status == "PASS"
        else (
            "Mainline parameters match the hard expectations, with one semantic warning to keep unchanged before test."
            if status == "WARN"
            else "Mainline run_config has parameter drift and must not be used for the test run."
        ),
        run_config_path=run_config_path,
        experiment_id=rc.get("experiment_id"),
        spec_file=rc.get("spec_file"),
        signal=signal,
        optimizer=optimizer,
        backtest=backtest,
        mismatches=mismatches,
        warnings=warnings,
    )


def l4_3_config_dates(results: list[CheckResult]) -> None:
    actual = {
        "TRAIN_START": cfg.TRAIN_START,
        "TRAIN_END": cfg.TRAIN_END,
        "VALID_START": cfg.VALID_START,
        "VALID_END": cfg.VALID_END,
        "TEST_START": cfg.TEST_START,
        "TEST_END": cfg.TEST_END,
        "STAMP_DUTY_CUT_DATE": cfg.STAMP_DUTY_CUT_DATE,
    }
    expected = {
        "VALID_END": pd.Timestamp("2022-12-31"),
        "TEST_START": pd.Timestamp("2023-01-01"),
        "TEST_END": pd.Timestamp("2025-12-31"),
        "STAMP_DUTY_CUT_DATE": pd.Timestamp("2023-08-28"),
    }
    mismatches = {
        key: {"expected": expected[key], "actual": actual.get(key)}
        for key in expected
        if actual.get(key) != expected[key]
    }
    add_result(
        results,
        "L4",
        "L4-3",
        "src.config date boundaries",
        "PASS" if not mismatches else "FAIL",
        "Config sample boundaries and stamp-duty cut date match the project contract."
        if not mismatches else "Config date boundaries do not match the project contract.",
        config_path=ROOT / "src" / "config.py",
        actual=actual,
        mismatches=mismatches,
    )


def write_reports(results: list[CheckResult]) -> None:
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "root": str(ROOT),
        "summary": {
            "PASS": sum(r.status == "PASS" for r in results),
            "WARN": sum(r.status == "WARN" for r in results),
            "FAIL": sum(r.status == "FAIL" for r in results),
        },
        "results": [asdict(r) for r in results],
    }
    REPORT_JSON.write_text(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# L3/L4 Inspection Report")
    lines.append("")
    lines.append(f"- Generated: {payload['generated_at']}")
    lines.append(f"- Root: `{ROOT}`")
    lines.append(f"- Summary: PASS={payload['summary']['PASS']} WARN={payload['summary']['WARN']} FAIL={payload['summary']['FAIL']}")
    lines.append("")
    for group in ["L3", "L4"]:
        lines.append(f"## {group}")
        lines.append("")
        for r in [x for x in results if x.group == group]:
            lines.append(f"### {r.check_id} - {r.name}: {r.status}")
            lines.append("")
            lines.append(r.message)
            lines.append("")
            if r.details:
                lines.append("```json")
                lines.append(json.dumps(to_jsonable(r.details), ensure_ascii=False, indent=2))
                lines.append("```")
                lines.append("")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results: list[CheckResult] = []
    l3_1_predict_cross_section(results)
    l3_2_signal_range(results)
    l3_3_fwd_extension(results)
    l3_4_topn_metadata(results)
    l3_5_ledger_write(results)
    l3_6_stamp_duty(results)
    mainline = l4_1_mainline(results)
    l4_2_run_config(results, mainline)
    l4_3_config_dates(results)
    write_reports(results)

    counts = {
        "PASS": sum(r.status == "PASS" for r in results),
        "WARN": sum(r.status == "WARN" for r in results),
        "FAIL": sum(r.status == "FAIL" for r in results),
    }
    print(f"L3/L4 inspection complete: PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    print(f"Markdown report: {REPORT_MD}")
    print(f"JSON report:     {REPORT_JSON}")
    for r in results:
        print(f"[{r.status}] {r.check_id} {r.name} - {r.message}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
