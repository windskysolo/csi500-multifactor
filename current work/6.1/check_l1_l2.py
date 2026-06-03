from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
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
from src.data.loader import get_rebalance_dates  # noqa: E402


FACTOR_PANEL_DIR = cfg.DATA_PROC / "factor_panels_test_run_1"
FINAL_FACTORS_JSON = ROOT / "reports" / "factor_evaluation" / "final_factors.json"
REPORT_MD = ROOT / "current work" / "6.1" / "l1_l2_inspection_report.md"
REPORT_JSON = ROOT / "current work" / "6.1" / "l1_l2_inspection_results.json"

CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp,)):
        return str(value.date())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if np.isnan(value):
            return None
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
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


def load_selected_factors() -> list[str]:
    if not FINAL_FACTORS_JSON.exists():
        raise FileNotFoundError(f"Missing final factors file: {FINAL_FACTORS_JSON}")
    with FINAL_FACTORS_JSON.open(encoding="utf-8") as fh:
        data = json.load(fh)
    factors = data.get("final_factors")
    if not isinstance(factors, list) or not factors:
        raise ValueError("final_factors.json has no non-empty final_factors list.")
    return [str(f) for f in factors]


def get_index_dates(df: pd.DataFrame, preferred_name: str = "trade_date") -> pd.Series:
    if isinstance(df.index, pd.MultiIndex):
        names = list(df.index.names)
        if preferred_name in names:
            vals = df.index.get_level_values(preferred_name)
        else:
            vals = df.index.get_level_values(0)
        return pd.Series(pd.to_datetime(vals), index=df.index)
    if preferred_name in df.columns:
        return pd.Series(pd.to_datetime(df[preferred_name]), index=df.index)
    if df.index.name is not None:
        return pd.Series(pd.to_datetime(df.index), index=df.index)
    return pd.Series(pd.to_datetime(df.index), index=df.index)


def get_index_codes(df: pd.DataFrame) -> pd.Index:
    if isinstance(df.index, pd.MultiIndex):
        names = list(df.index.names)
        if "ts_code" in names:
            return pd.Index(df.index.get_level_values("ts_code").astype(str))
        return pd.Index(df.index.get_level_values(-1).astype(str))
    if "ts_code" in df.columns:
        return pd.Index(df["ts_code"].astype(str))
    return pd.Index([])


def code_format_stats(codes: pd.Index) -> dict[str, Any]:
    unique_codes = pd.Index(codes.dropna().astype(str).unique())
    if len(unique_codes) == 0:
        return {"n_codes": 0, "n_bad": 0, "bad_examples": []}
    bad = [c for c in unique_codes if not CODE_RE.match(str(c))]
    return {
        "n_codes": int(len(unique_codes)),
        "n_bad": int(len(bad)),
        "bad_examples": bad[:10],
    }


def read_panel_index(path: Path) -> pd.DatetimeIndex:
    try:
        df = pd.read_parquet(path, columns=[])
    except Exception:
        df = pd.read_parquet(path)
    return pd.DatetimeIndex(pd.to_datetime(df.index))


def fmt_date_list(dates: list[pd.Timestamp] | pd.DatetimeIndex | set[pd.Timestamp]) -> list[str]:
    return [str(pd.Timestamp(d).date()) for d in sorted(dates)]


def l1_checks(results: list[CheckResult]) -> tuple[list[str], pd.DatetimeIndex]:
    selected = load_selected_factors()
    test_dates = pd.DatetimeIndex(get_rebalance_dates(start=cfg.TEST_START, end=cfg.TEST_END))
    test_date_set = set(test_dates)

    if not FACTOR_PANEL_DIR.exists():
        add_result(
            results, "L1", "L1-1", "factor panel directory",
            "FAIL", "factor_panels_test_run_1 directory is missing.",
            path=FACTOR_PANEL_DIR,
        )
        return selected, test_dates

    panel_files = {p.stem for p in FACTOR_PANEL_DIR.glob("*.parquet")}
    missing = [f for f in selected if f not in panel_files]
    add_result(
        results,
        "L1",
        "L1-1",
        "selected factors have panel files",
        "PASS" if not missing else "FAIL",
        "All selected factors have corresponding parquet files." if not missing else "Some selected factors are missing panel files.",
        panel_dir=FACTOR_PANEL_DIR,
        n_panel_files=len(panel_files),
        n_selected=len(selected),
        missing_factors=missing,
        extra_panel_files=sorted(panel_files - set(selected))[:50],
    )

    l1_2_status = "PASS" if len(test_dates) == 36 else "FAIL"
    add_result(
        results,
        "L1",
        "L1-2",
        "actual test rebalance dates",
        l1_2_status,
        f"Found {len(test_dates)} actual test rebalance dates.",
        count=len(test_dates),
        first=str(test_dates[0].date()) if len(test_dates) else None,
        last=str(test_dates[-1].date()) if len(test_dates) else None,
        first_five=fmt_date_list(test_dates[:5]),
        last_five=fmt_date_list(test_dates[-5:]),
    )

    fixed_factors = ["analyst_eps_revision", "hk_hold_chg", "hk_hold_ratio"]
    fixed_details: dict[str, Any] = {}
    fixed_failures: list[str] = []
    fixed_warnings: list[str] = []
    for factor in fixed_factors:
        fp = FACTOR_PANEL_DIR / f"{factor}.parquet"
        if not fp.exists():
            fixed_failures.append(f"{factor}: file missing")
            fixed_details[factor] = {"file_missing": True}
            continue
        df = pd.read_parquet(fp)
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index))
        test_slice = df.loc[df.index.intersection(test_dates)]
        missing_dates = test_date_set - set(df.index)
        valid_counts = test_slice.notna().sum(axis=1)
        avg_valid = float(valid_counts.mean()) if len(valid_counts) else float("nan")
        min_valid = int(valid_counts.min()) if len(valid_counts) else 0
        zero_valid_dates = valid_counts[valid_counts == 0].index
        if missing_dates:
            fixed_failures.append(f"{factor}: missing dates")
        if not np.isfinite(avg_valid) or avg_valid <= 50:
            fixed_failures.append(f"{factor}: low avg valid count")
        if len(zero_valid_dates) > 0:
            fixed_warnings.append(f"{factor}: {len(zero_valid_dates)} all-NaN test rows")
        fixed_details[factor] = {
            "date_min": df.index.min(),
            "date_max": df.index.max(),
            "rows": len(df),
            "test_rows": len(test_slice),
            "missing_test_dates": fmt_date_list(missing_dates),
            "zero_valid_dates": fmt_date_list(zero_valid_dates),
            "avg_valid_stocks": avg_valid,
            "min_valid_stocks": min_valid,
        }
    l1_3_status = "FAIL" if fixed_failures else ("WARN" if fixed_warnings else "PASS")
    add_result(
        results,
        "L1",
        "L1-3",
        "fixed ALT factor panels",
        l1_3_status,
        "The three repaired ALT factor panels cover all test dates with adequate non-null coverage."
        if l1_3_status == "PASS"
        else (
            "Repaired ALT factor panel rows exist, but some test rows are all-NaN and should be reviewed."
            if l1_3_status == "WARN"
            else "At least one repaired ALT factor panel has missing dates or inadequate coverage."
        ),
        failures=fixed_failures,
        warnings=fixed_warnings,
        factors=fixed_details,
    )

    coverage_issues: dict[str, Any] = {}
    panel_indexes: dict[str, pd.DatetimeIndex] = {}
    for factor in selected:
        fp = FACTOR_PANEL_DIR / f"{factor}.parquet"
        if not fp.exists():
            coverage_issues[factor] = {"file_missing": True}
            continue
        idx = read_panel_index(fp)
        panel_indexes[factor] = idx
        missing_dates = test_date_set - set(idx)
        if missing_dates:
            coverage_issues[factor] = {
                "missing_count": len(missing_dates),
                "missing_dates": fmt_date_list(missing_dates),
                "date_min": idx.min(),
                "date_max": idx.max(),
            }
    add_result(
        results,
        "L1",
        "L1-4",
        "all selected factor test coverage",
        "PASS" if not coverage_issues else "FAIL",
        "All selected factors cover every actual test rebalance date."
        if not coverage_issues else "Some selected factors do not cover every actual test rebalance date.",
        issues=coverage_issues,
        n_selected=len(selected),
    )

    predict_failures: dict[str, Any] = {}
    qualify_counts: dict[str, int] = {}
    if not missing:
        panels: dict[str, pd.DataFrame] = {}
        for factor in selected:
            fp = FACTOR_PANEL_DIR / f"{factor}.parquet"
            if fp.exists():
                panel = pd.read_parquet(fp)
                panel.index = pd.DatetimeIndex(pd.to_datetime(panel.index))
                panel.columns = panel.columns.astype(str)
                panels[factor] = panel
        for T in test_dates:
            missing_factor_rows = [f for f, p in panels.items() if T not in p.index]
            if missing_factor_rows:
                predict_failures[str(T.date())] = {"missing_factor_rows": missing_factor_rows}
                continue
            cs = pd.DataFrame({f: panels[f].loc[T] for f in selected})
            n_qualifying = int((cs.notna().sum(axis=1) >= cfg.SIGNAL_MIN_VALID_FACTORS).sum())
            qualify_counts[str(T.date())] = n_qualifying
            if n_qualifying < 10:
                predict_failures[str(T.date())] = {"n_qualifying": n_qualifying}
    min_qualifying = min(qualify_counts.values()) if qualify_counts else 0
    l1_5_status = "PASS" if not predict_failures and min_qualifying >= 10 else "FAIL"
    if l1_5_status == "PASS" and min_qualifying < 300:
        l1_5_status = "WARN"
    add_result(
        results,
        "L1",
        "L1-5",
        "_predict_cross_section compatibility",
        l1_5_status,
        "No test date should trigger the missing-row or too-few-stocks return None path."
        if l1_5_status in {"PASS", "WARN"} else "At least one test date can still trigger return None.",
        min_valid_factors=cfg.SIGNAL_MIN_VALID_FACTORS,
        n_dates_checked=len(test_dates),
        min_qualifying_stocks=min_qualifying,
        mean_qualifying_stocks=float(np.mean(list(qualify_counts.values()))) if qualify_counts else None,
        failures=predict_failures,
    )
    return selected, test_dates


def l2_fwd_ret(results: list[CheckResult]) -> None:
    path = cfg.DATA_PROC / "fwd_ret_panel.parquet"
    if not path.exists():
        add_result(results, "L2", "L2-1", "public fwd_ret cache", "FAIL", "fwd_ret_panel.parquet is missing.", path=path)
        return
    fwd = pd.read_parquet(path)
    fwd.index = pd.DatetimeIndex(pd.to_datetime(fwd.index))
    test_rows = fwd[fwd.index >= cfg.TEST_START]
    non_null = fwd.notna().sum(axis=1)
    status = "PASS"
    failures: list[str] = []
    warnings: list[str] = []
    if len(test_rows) > 0:
        failures.append("public cache already contains test-period rows")
    if fwd.index.max() > cfg.VALID_END:
        failures.append("public cache extends beyond VALID_END")
    if fwd.index.max() < cfg.VALID_END - pd.DateOffset(months=2):
        warnings.append("public cache ends more than two months before VALID_END")
    if float(non_null.mean()) < 300:
        warnings.append("average non-null stocks per row is below 300")
    if failures:
        status = "FAIL"
    elif warnings:
        status = "WARN"
    add_result(
        results,
        "L2",
        "L2-1",
        "public fwd_ret cache",
        status,
        "Public fwd_ret cache has no test-period contamination and can be extended by test pipeline."
        if status == "PASS" else "Review public fwd_ret cache coverage before running test pipeline.",
        date_min=fwd.index.min(),
        date_max=fwd.index.max(),
        rows=len(fwd),
        n_test_rows=len(test_rows),
        avg_non_null_stocks=float(non_null.mean()),
        min_non_null_stocks=int(non_null.min()) if len(non_null) else 0,
        overall_nan_rate=float(fwd.isna().mean().mean()),
        failures=failures,
        warnings=warnings,
    )


def l2_index_member(results: list[CheckResult], test_dates: pd.DatetimeIndex) -> None:
    path = cfg.DATA_PROC / "index_member.parquet"
    if not path.exists():
        add_result(results, "L2", "L2-2", "index_member coverage and quality", "FAIL", "index_member.parquet is missing.", path=path)
        return
    idx = pd.read_parquet(path)
    failures: list[str] = []
    warnings: list[str] = []
    if not isinstance(idx.index, pd.MultiIndex):
        failures.append("index_member index is not MultiIndex")
        dates = pd.DatetimeIndex(pd.to_datetime(idx.index.unique()))
        codes = get_index_codes(idx)
    else:
        names = list(idx.index.names)
        date_level = "rebalance_date" if "rebalance_date" in names else names[0]
        code_level = "ts_code" if "ts_code" in names else names[-1]
        dates = pd.DatetimeIndex(pd.to_datetime(idx.index.get_level_values(date_level).unique()))
        codes = pd.Index(idx.index.get_level_values(code_level).astype(str))
    test_date_set = set(test_dates)
    missing_dates = test_date_set - set(dates)
    if missing_dates:
        failures.append("missing test rebalance dates")

    n_stats: dict[str, Any] = {}
    wt_stats: dict[str, Any] = {}
    status_null_rates: dict[str, float] = {}
    if isinstance(idx.index, pd.MultiIndex):
        n_per_date = idx.groupby(level=0).size()
        test_n = n_per_date[n_per_date.index.isin(test_dates)]
        if len(test_n):
            n_stats = {
                "min": int(test_n.min()),
                "max": int(test_n.max()),
                "mean": float(test_n.mean()),
            }
            if int(test_n.min()) < 450 or int(test_n.max()) > 550:
                failures.append("test-period member count outside 450-550")
        weight_col = "index_weight" if "index_weight" in idx.columns else ("weight" if "weight" in idx.columns else None)
        if weight_col is None:
            failures.append("missing index weight column")
        else:
            wt_sum_raw = idx[weight_col].groupby(level=0).sum()
            wt_sum = wt_sum_raw / 100.0 if float(wt_sum_raw.median()) > 10 else wt_sum_raw
            test_wt = wt_sum[wt_sum.index.isin(test_dates)]
            wt_stats = {
                "column": weight_col,
                "raw_median_sum": float(wt_sum_raw.median()),
                "normalized_min": float(test_wt.min()) if len(test_wt) else None,
                "normalized_max": float(test_wt.max()) if len(test_wt) else None,
            }
            if len(test_wt) and (float(test_wt.min()) < 0.99 or float(test_wt.max()) > 1.01):
                failures.append("normalized index weights do not sum to approximately 1")

    required_status_cols = [
        "tradable",
        "is_suspended",
        "is_limit_locked",
        "is_limit_up_locked",
        "is_limit_down_locked",
        "is_st",
        "is_new_stock",
    ]
    missing_status_cols = [c for c in required_status_cols if c not in idx.columns]
    if missing_status_cols:
        failures.append("missing required status columns")
    for col in required_status_cols:
        if col in idx.columns:
            rate = float(idx[col].isna().mean())
            status_null_rates[col] = rate
            if rate > 0.05:
                failures.append(f"{col} null rate > 5%")

    code_stats = code_format_stats(codes)
    if code_stats["n_bad"] > 0:
        failures.append("ts_code format contains non-suffixed or invalid codes")

    add_result(
        results,
        "L2",
        "L2-2",
        "index_member coverage and quality",
        "PASS" if not failures else "FAIL",
        "index_member covers all test dates with plausible member counts, weights, states, and ts_code format."
        if not failures else "index_member has coverage or quality issues.",
        date_min=dates.min() if len(dates) else None,
        date_max=dates.max() if len(dates) else None,
        n_dates=len(dates),
        missing_test_dates=fmt_date_list(missing_dates),
        member_count_stats=n_stats,
        weight_sum_stats=wt_stats,
        missing_status_cols=missing_status_cols,
        status_null_rates=status_null_rates,
        code_format=code_stats,
        failures=failures,
        warnings=warnings,
    )


def l2_daily_quote(results: list[CheckResult]) -> None:
    path = cfg.DATA_PROC / "daily_quote.parquet"
    if not path.exists():
        add_result(results, "L2", "L2-3", "daily_quote coverage and adjusted prices", "FAIL", "daily_quote.parquet is missing.", path=path)
        return
    try:
        import pyarrow.parquet as pq

        schema_names = set(pq.read_schema(path).names)
        cols = [c for c in ["trade_date", "ts_code", "open_adj", "close_adj", "ret"] if c in schema_names]
        dq = pd.read_parquet(path, columns=cols or None)
    except Exception:
        dq = pd.read_parquet(path)
    failures: list[str] = []
    warnings: list[str] = []
    dates = get_index_dates(dq, "trade_date")
    codes = get_index_codes(dq)
    unique_dates = pd.DatetimeIndex(pd.to_datetime(pd.Index(dates).unique())).sort_values()
    if len(unique_dates) == 0:
        failures.append("no trade dates found")
    elif unique_dates.max() < cfg.TEST_END:
        failures.append("daily_quote does not cover through TEST_END")
    test_mask = dates >= cfg.TEST_START
    n_test_trading_days = int(pd.Index(dates[test_mask]).nunique())
    if n_test_trading_days < 700:
        failures.append("fewer than 700 unique trading days in 2023-2025")
    required_cols = ["open_adj", "close_adj"]
    missing_cols = [c for c in required_cols if c not in dq.columns]
    if missing_cols:
        failures.append("missing adjusted price columns")
    nan_rates: dict[str, float] = {}
    for col in required_cols:
        if col in dq.columns and test_mask.any():
            rate = float(dq.loc[test_mask.to_numpy(), col].isna().mean())
            nan_rates[col] = rate
            if rate > 0.05:
                failures.append(f"{col} NaN rate > 5% in test period")
    code_stats = code_format_stats(codes)
    if code_stats["n_bad"] > 0:
        failures.append("daily_quote ts_code format contains invalid codes")
    add_result(
        results,
        "L2",
        "L2-3",
        "daily_quote coverage and adjusted prices",
        "PASS" if not failures else "FAIL",
        "daily_quote has test-period coverage, adjusted open/close prices, and suffixed ts_code values."
        if not failures else "daily_quote has coverage or adjusted-price issues.",
        date_min=unique_dates.min() if len(unique_dates) else None,
        date_max=unique_dates.max() if len(unique_dates) else None,
        n_unique_trading_days=len(unique_dates),
        n_test_trading_days=n_test_trading_days,
        columns=list(dq.columns),
        missing_required_cols=missing_cols,
        test_nan_rates=nan_rates,
        code_format=code_stats,
        failures=failures,
        warnings=warnings,
    )


def read_raw_index_codes() -> dict[str, Any]:
    raw_path = cfg.DATA_RAW / "index_daily.csv"
    if not raw_path.exists():
        return {"raw_path": raw_path, "exists": False, "codes": []}
    raw = pd.read_csv(raw_path, nrows=200000)
    code_col = None
    for candidate in ["ts_code", "index_code", "code"]:
        if candidate in raw.columns:
            code_col = candidate
            break
    if code_col is None:
        return {"raw_path": raw_path, "exists": True, "code_col": None, "codes": []}
    codes = sorted(str(c) for c in pd.Index(raw[code_col].dropna().astype(str).unique()))
    return {"raw_path": raw_path, "exists": True, "code_col": code_col, "codes": codes}


def l2_index_quote(results: list[CheckResult]) -> None:
    path = cfg.DATA_PROC / "index_quote.parquet"
    if not path.exists():
        add_result(results, "L2", "L2-4", "index_quote total-return benchmark", "FAIL", "index_quote.parquet is missing.", path=path)
        return
    iq = pd.read_parquet(path)
    failures: list[str] = []
    warnings: list[str] = []
    dates = get_index_dates(iq, "trade_date")
    unique_dates = pd.DatetimeIndex(pd.to_datetime(pd.Index(dates).unique())).sort_values()
    if "nav" not in iq.columns:
        failures.append("missing nav column for total-return benchmark")
    if len(unique_dates) == 0:
        failures.append("no index dates found")
    elif unique_dates.max() < cfg.TEST_END:
        failures.append("index_quote does not cover through TEST_END")
    nav_jump_max = None
    nav_consistency_max_abs = None
    if "nav" in iq.columns:
        nav = pd.Series(iq["nav"].astype(float).values, index=pd.to_datetime(dates)).sort_index()
        nav_jump_max = float(nav.pct_change().abs().max())
        if nav_jump_max > 0.15:
            failures.append("index nav has >15% one-day jump")
        if "index_ret" in iq.columns:
            ret = pd.Series(iq["index_ret"].astype(float).values, index=pd.to_datetime(dates)).sort_index()
            recalc = (1.0 + ret.fillna(0.0)).cumprod()
            scale = nav.iloc[0] / recalc.iloc[0] if recalc.iloc[0] != 0 else np.nan
            aligned = recalc * scale
            nav_consistency_max_abs = float((nav - aligned).abs().max())
            if nav_consistency_max_abs > 1e-8:
                warnings.append("nav is not exactly reconstructed from index_ret within 1e-8")
    raw_code_info = read_raw_index_codes()
    source_ok = False
    if "ts_code" in iq.columns:
        codes = sorted(str(c) for c in pd.Index(iq["ts_code"].dropna().astype(str).unique()))
        source_ok = "H00905.CSI" in codes and not any(c in {"000905.SH", "000905.CSI", "399905.SZ"} for c in codes)
        raw_code_info["processed_codes"] = codes
    else:
        codes = raw_code_info.get("codes", [])
        source_ok = "H00905.CSI" in codes and not any(c in {"000905.SH", "000905.CSI", "399905.SZ"} for c in codes)
    if not source_ok:
        failures.append("cannot confirm H00905.CSI total-return source, or price-index code is present")
    status = "FAIL" if failures else ("WARN" if warnings else "PASS")
    add_result(
        results,
        "L2",
        "L2-4",
        "index_quote total-return benchmark",
        status,
        "index_quote is confirmed as H00905.CSI total-return benchmark with valid nav coverage."
        if status == "PASS" else "Review index_quote benchmark source or nav integrity.",
        date_min=unique_dates.min() if len(unique_dates) else None,
        date_max=unique_dates.max() if len(unique_dates) else None,
        columns=list(iq.columns),
        raw_code_info=raw_code_info,
        nav_jump_max=nav_jump_max,
        nav_consistency_max_abs=nav_consistency_max_abs,
        failures=failures,
        warnings=warnings,
    )


def l2_industry(results: list[CheckResult], test_dates: pd.DatetimeIndex) -> None:
    path = cfg.DATA_PROC / "industry.parquet"
    if not path.exists():
        add_result(results, "L2", "L2-5", "industry coverage and SW2021 breadth", "FAIL", "industry.parquet is missing.", path=path)
        return
    ind = pd.read_parquet(path)
    failures: list[str] = []
    warnings: list[str] = []
    dates = get_index_dates(ind, "trade_date")
    codes = get_index_codes(ind)
    unique_dates = pd.DatetimeIndex(pd.to_datetime(pd.Index(dates).unique())).sort_values()
    missing_test_dates = set(test_dates) - set(unique_dates)
    if missing_test_dates:
        failures.append("industry data missing actual test rebalance dates")
    if len(unique_dates) == 0:
        failures.append("no industry dates found")
    elif unique_dates.max() < cfg.TEST_END:
        failures.append("industry data does not cover through TEST_END")
    ind_col = "industry_code" if "industry_code" in ind.columns else ("industry" if "industry" in ind.columns else "sw_l1" if "sw_l1" in ind.columns else None)
    n_industries = None
    if ind_col is None:
        failures.append("missing industry classification column")
    else:
        test_mask = dates >= cfg.TEST_START
        n_industries = int(ind.loc[test_mask.to_numpy(), ind_col].dropna().nunique()) if test_mask.any() else 0
        if n_industries < 25 or n_industries > 35:
            warnings.append("test-period industry count is outside expected SW2021 first-level range")
    code_stats = code_format_stats(codes)
    if code_stats["n_bad"] > 0:
        failures.append("industry ts_code format contains invalid codes")
    status = "FAIL" if failures else ("WARN" if warnings else "PASS")
    add_result(
        results,
        "L2",
        "L2-5",
        "industry coverage and SW2021 breadth",
        status,
        "industry data covers test dates with plausible SW2021 first-level classification breadth."
        if status == "PASS" else "Review industry coverage or classification breadth.",
        date_min=unique_dates.min() if len(unique_dates) else None,
        date_max=unique_dates.max() if len(unique_dates) else None,
        n_unique_dates=len(unique_dates),
        missing_test_dates=fmt_date_list(missing_test_dates),
        industry_column=ind_col,
        n_test_industries=n_industries,
        columns=list(ind.columns),
        code_format=code_stats,
        failures=failures,
        warnings=warnings,
    )


def l2_checks(results: list[CheckResult], test_dates: pd.DatetimeIndex) -> None:
    l2_fwd_ret(results)
    l2_index_member(results, test_dates)
    l2_daily_quote(results)
    l2_index_quote(results)
    l2_industry(results, test_dates)


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
    lines.append("# L1/L2 Inspection Report")
    lines.append("")
    lines.append(f"- Generated: {payload['generated_at']}")
    lines.append(f"- Root: `{ROOT}`")
    lines.append(f"- Summary: PASS={payload['summary']['PASS']} WARN={payload['summary']['WARN']} FAIL={payload['summary']['FAIL']}")
    lines.append("")
    for group in ["L1", "L2"]:
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
    selected, test_dates = l1_checks(results)
    _ = selected
    l2_checks(results, test_dates)
    write_reports(results)

    counts = {
        "PASS": sum(r.status == "PASS" for r in results),
        "WARN": sum(r.status == "WARN" for r in results),
        "FAIL": sum(r.status == "FAIL" for r in results),
    }
    print(f"L1/L2 inspection complete: PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    print(f"Markdown report: {REPORT_MD}")
    print(f"JSON report:     {REPORT_JSON}")
    for r in results:
        print(f"[{r.status}] {r.check_id} {r.name} - {r.message}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
