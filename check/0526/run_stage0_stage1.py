"""
Stage 0/1 audit for check/0526.

This script is intentionally read-only for project research artifacts. It only
writes audit outputs under check/0526/ and does not run the formal test set.

Outputs:
  - artifact_manifest.csv
  - config_snapshot.csv
  - git_status.txt
  - metric_recalc.csv
  - monthly_excess_review.csv
  - log.md
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "check" / "0526"

sys.path.insert(0, str(ROOT))
from src import config as cfg  # noqa: E402


TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class NavSpec:
    source_id: str
    nav_path: Path
    benchmark_col: str = "benchmark"
    strategy_col: str | None = "strategy"
    metrics_path: Path | None = None
    primary: bool = True
    note: str = ""


ARTIFACTS: list[tuple[str, Path]] = [
    ("pipeline_a_signal_ic_ir", cfg.DATA_PROC / "composite_signal_ic_ir.parquet"),
    (
        "pipeline_b_ridge_expanding_signal",
        ROOT / "experiments" / "ridge_signal" / "results" / "ridge_composite_panel.parquet",
    ),
    (
        "pipeline_b_ridge_expanding_coef",
        ROOT / "experiments" / "ridge_signal" / "results" / "ridge_coef_history.parquet",
    ),
    (
        "pipeline_b_nav_valid",
        ROOT
        / "experiments"
        / "turnover_lambda_grid"
        / "results"
        / "lam_0050"
        / "backtest_nav_valid.parquet",
    ),
    (
        "pipeline_b_metrics_valid",
        ROOT
        / "experiments"
        / "turnover_lambda_grid"
        / "results"
        / "lam_0050"
        / "backtest_metrics_valid.parquet",
    ),
    (
        "pipeline_b_weights_optimized",
        ROOT
        / "experiments"
        / "turnover_lambda_grid"
        / "results"
        / "lam_0050"
        / "weights_optimized.parquet",
    ),
    (
        "rolling_48m_signal",
        ROOT / "experiments" / "ridge_rolling" / "results" / "rolling_48m_composite_panel.parquet",
    ),
    (
        "rolling_48m_coef",
        ROOT / "experiments" / "ridge_rolling" / "results" / "rolling_48m_coef_history.parquet",
    ),
    (
        "rolling_48m_nav_valid",
        ROOT
        / "experiments"
        / "ridge_rolling"
        / "results"
        / "rolling_48m"
        / "backtest_nav_valid.parquet",
    ),
    (
        "rolling_48m_metrics_valid",
        ROOT
        / "experiments"
        / "ridge_rolling"
        / "results"
        / "rolling_48m"
        / "backtest_metrics_valid.parquet",
    ),
    ("index_quote", cfg.DATA_PROC / "index_quote.parquet"),
    ("fwd_ret_panel", cfg.DATA_PROC / "fwd_ret_panel.parquet"),
    ("pipeline_a_nav", cfg.DATA_PROC / "backtest_nav.parquet"),
    ("pipeline_a_metrics", cfg.DATA_PROC / "backtest_metrics.parquet"),
]


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256_16(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _run_git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    text = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if err:
        text = f"{text}\n[stderr]\n{err}".strip()
    return text


def _jsonish(value: Any) -> str:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return _rel(value)
    if isinstance(value, (set, frozenset, list, tuple)):
        return json.dumps(list(value), ensure_ascii=False, default=str)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    return str(value)


def _looks_like_date(values: pd.Series, name: str | None = None) -> bool:
    if pd.api.types.is_datetime64_any_dtype(values):
        return True
    if name and "date" in str(name).lower():
        return True
    sample = values.dropna().astype(str).head(20)
    if sample.empty:
        return False
    pattern = r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    return bool(sample.str.match(pattern).mean() > 0.90)


def _datetime_level_summary(df: pd.DataFrame) -> tuple[str, str, int, str]:
    """Return date_min, date_max, n_unique_dates, date_level."""
    candidates: list[tuple[str, pd.Series]] = []

    if isinstance(df.index, pd.DatetimeIndex):
        candidates.append((df.index.name or "index", pd.Series(df.index)))
    elif isinstance(df.index, pd.MultiIndex):
        for i, name in enumerate(df.index.names):
            values = pd.Series(df.index.get_level_values(i))
            if not _looks_like_date(values, name):
                continue
            converted = pd.to_datetime(values, errors="coerce")
            if converted.notna().mean() > 0.90:
                candidates.append((name or f"level_{i}", converted))
    else:
        values = pd.Series(df.index)
        if _looks_like_date(values, df.index.name):
            converted = pd.to_datetime(values, errors="coerce")
            if converted.notna().mean() > 0.90:
                candidates.append((df.index.name or "index", converted))

    if not candidates:
        for col in df.columns:
            if "date" not in str(col).lower():
                continue
            converted = pd.to_datetime(df[col], errors="coerce")
            if converted.notna().mean() > 0.90:
                candidates.append((str(col), converted))

    if not candidates:
        return "", "", 0, ""

    level_name, values = candidates[0]
    values = pd.to_datetime(values, errors="coerce").dropna()
    if values.empty:
        return "", "", 0, level_name
    return (
        values.min().strftime("%Y-%m-%d"),
        values.max().strftime("%Y-%m-%d"),
        int(values.nunique()),
        level_name,
    )


def build_artifact_manifest() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    valid_start_tol = cfg.VALID_START + pd.Timedelta(days=7)
    valid_end_tol = cfg.VALID_END - pd.Timedelta(days=7)

    for role, path in ARTIFACTS:
        row: dict[str, Any] = {
            "role": role,
            "path": _rel(path),
            "exists": path.exists(),
            "size_bytes": np.nan,
            "mtime": "",
            "sha256_16": "",
            "n_rows": np.nan,
            "n_cols": np.nan,
            "index_names": "",
            "column_sample": "",
            "date_level": "",
            "date_min": "",
            "date_max": "",
            "n_unique_dates": 0,
            "covers_valid_period_tolerance": False,
            "coverage_note": "",
            "has_nav_col": False,
            "error": "",
        }
        if not path.exists():
            row["error"] = "missing"
            rows.append(row)
            continue

        stat = path.stat()
        row["size_bytes"] = stat.st_size
        row["mtime"] = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        row["sha256_16"] = _sha256_16(path)

        try:
            df = pd.read_parquet(path)
            row["n_rows"] = int(df.shape[0])
            row["n_cols"] = int(df.shape[1])
            index_names = list(df.index.names) if isinstance(df.index, pd.MultiIndex) else [df.index.name]
            row["index_names"] = json.dumps(index_names, ensure_ascii=False)
            row["column_sample"] = json.dumps([str(c) for c in df.columns[:20]], ensure_ascii=False)
            row["has_nav_col"] = "nav" in set(map(str, df.columns))
            date_min, date_max, n_dates, level = _datetime_level_summary(df)
            row["date_level"] = level
            row["date_min"] = date_min
            row["date_max"] = date_max
            row["n_unique_dates"] = n_dates
            if date_min and date_max:
                d_min = pd.Timestamp(date_min)
                d_max = pd.Timestamp(date_max)
                row["covers_valid_period_tolerance"] = bool(
                    d_min <= valid_start_tol and d_max >= valid_end_tol
                )
                previous_month_end = cfg.VALID_END - pd.offsets.MonthEnd(1)
                if (
                    not row["covers_valid_period_tolerance"]
                    and any(token in role for token in ["signal", "coef", "fwd_ret"])
                    and d_min <= valid_start_tol
                    and d_max >= previous_month_end - pd.Timedelta(days=7)
                ):
                    row["coverage_note"] = (
                        "截至验证期最后一个实际持有期所需调仓日附近；"
                        "VALID_END 月末调仓的执行日落在验证期之后"
                    )
            else:
                row["coverage_note"] = "未检测到日期索引或日期列"
        except Exception as exc:  # pragma: no cover - audit should record and continue
            row["error"] = repr(exc)

        rows.append(row)

    return pd.DataFrame(rows)


def build_config_snapshot() -> pd.DataFrame:
    keys = [
        "TRAIN_START",
        "TRAIN_END",
        "VALID_START",
        "VALID_END",
        "TEST_START",
        "TEST_END",
        "OPT_TE_TARGET_ANNUAL",
        "OPT_INDUSTRY_MAX_DEV",
        "OPT_SINGLE_MAX_DEV",
        "OPT_TOPN",
        "OPT_TURNOVER_LAMBDA",
        "COMMISSION_RATE",
        "SLIPPAGE_RATE",
        "STAMP_DUTY_BEFORE",
        "STAMP_DUTY_AFTER",
        "STAMP_DUTY_CUT_DATE",
    ]
    return pd.DataFrame(
        [
            {
                "key": key,
                "value": _jsonish(getattr(cfg, key)),
                "type": type(getattr(cfg, key)).__name__,
            }
            for key in keys
        ]
    )


def _as_validation_nav(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    out = out[(out.index >= cfg.VALID_START) & (out.index <= cfg.VALID_END)]
    return out


def _annualized_return(nav: pd.Series) -> float:
    if len(nav) < 2:
        return float("nan")
    n_years = len(nav) / TRADING_DAYS_PER_YEAR
    if n_years <= 0 or nav.iloc[0] == 0:
        return float("nan")
    return float((nav.iloc[-1] / nav.iloc[0]) ** (1 / n_years) - 1)


def _max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return float("nan")
    rolling_max = nav.cummax()
    return float(((nav - rolling_max) / rolling_max).min())


def _calc_metrics(
    source_id: str,
    nav_df: pd.DataFrame,
    strategy_col: str,
    benchmark_col: str,
    nav_path: Path,
    metrics_path: Path | None,
    primary: bool,
    note: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    missing_cols = [c for c in [strategy_col, benchmark_col] if c not in nav_df.columns]
    if missing_cols:
        raise KeyError(f"{source_id}: missing columns {missing_cols}")

    data = _as_validation_nav(nav_df[[strategy_col, benchmark_col]].dropna())
    data.columns = ["strategy", "benchmark"]
    if data.empty:
        raise ValueError(f"{source_id}: no validation NAV rows")

    strat = data["strategy"].astype(float)
    bench = data["benchmark"].astype(float)
    strat_ret = strat.pct_change()
    bench_ret = bench.pct_change()
    excess_daily = strat_ret - bench_ret

    ann_strategy = _annualized_return(strat)
    ann_benchmark = _annualized_return(bench)
    excess_return = ann_strategy - ann_benchmark
    tracking_error = float(excess_daily.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    information_ratio = (
        float(excess_return / tracking_error)
        if tracking_error > 1e-10
        else 0.0
    )

    monthly_nav = data.resample("ME").last()
    monthly_strategy = monthly_nav["strategy"].pct_change()
    monthly_benchmark = monthly_nav["benchmark"].pct_change()
    monthly = pd.DataFrame(
        {
            "strategy_month_return": monthly_strategy,
            "benchmark_month_return": monthly_benchmark,
        }
    ).dropna()
    monthly["monthly_excess_return"] = (
        monthly["strategy_month_return"] - monthly["benchmark_month_return"]
    )
    monthly["is_positive_excess"] = monthly["monthly_excess_return"] > 0
    monthly["source_id"] = source_id
    monthly["nav_path"] = _rel(nav_path)
    monthly["strategy_col"] = strategy_col
    monthly["benchmark_col"] = benchmark_col
    monthly.index.name = "month_end"
    monthly = monthly.reset_index()

    pos = monthly.loc[monthly["monthly_excess_return"] > 0, "monthly_excess_return"]
    neg = monthly.loc[monthly["monthly_excess_return"] < 0, "monthly_excess_return"]
    zero_count = int((monthly["monthly_excess_return"] == 0).sum())
    net_sum = float(monthly["monthly_excess_return"].sum()) if not monthly.empty else float("nan")
    pos_sum = float(pos.sum()) if not pos.empty else 0.0
    neg_sum = float(neg.sum()) if not neg.empty else 0.0

    row: dict[str, Any] = {
        "source_id": source_id,
        "primary": primary,
        "note": note,
        "nav_path": _rel(nav_path),
        "metrics_path": _rel(metrics_path) if metrics_path else "",
        "strategy_col": strategy_col,
        "benchmark_col": benchmark_col,
        "start_date": data.index.min().strftime("%Y-%m-%d"),
        "end_date": data.index.max().strftime("%Y-%m-%d"),
        "n_daily_obs": int(len(data)),
        "first_strategy_nav": float(strat.iloc[0]),
        "first_benchmark_nav": float(bench.iloc[0]),
        "last_strategy_nav": float(strat.iloc[-1]),
        "last_benchmark_nav": float(bench.iloc[-1]),
        "annualized_return": ann_strategy,
        "benchmark_return": ann_benchmark,
        "excess_return": excess_return,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "max_drawdown": _max_drawdown(strat),
        "excess_max_drawdown": _max_drawdown(strat / bench),
        "monthly_win_rate": float(monthly["is_positive_excess"].mean()) if not monthly.empty else float("nan"),
        "monthly_obs": int(len(monthly)),
        "monthly_positive_count": int(len(pos)),
        "monthly_negative_count": int(len(neg)),
        "monthly_zero_count": zero_count,
        "monthly_pos_mean": float(pos.mean()) if not pos.empty else float("nan"),
        "monthly_neg_mean": float(neg.mean()) if not neg.empty else float("nan"),
        "monthly_pos_sum": pos_sum,
        "monthly_neg_sum": neg_sum,
        "monthly_net_excess_sum": net_sum,
        "positive_contribution_ratio": (
            float(pos_sum / net_sum) if abs(net_sum) > 1e-12 else float("nan")
        ),
        "max_positive_month": (
            str(monthly.loc[monthly["monthly_excess_return"].idxmax(), "month_end"].date())
            if not monthly.empty
            else ""
        ),
        "max_positive_month_excess": (
            float(monthly["monthly_excess_return"].max()) if not monthly.empty else float("nan")
        ),
        "max_negative_month": (
            str(monthly.loc[monthly["monthly_excess_return"].idxmin(), "month_end"].date())
            if not monthly.empty
            else ""
        ),
        "max_negative_month_excess": (
            float(monthly["monthly_excess_return"].min()) if not monthly.empty else float("nan")
        ),
    }

    stored = _load_stored_metrics(metrics_path, strategy_col)
    for key in [
        "annualized_return",
        "benchmark_return",
        "excess_return",
        "tracking_error",
        "information_ratio",
        "excess_max_drawdown",
        "monthly_win_rate",
    ]:
        row[f"stored_{key}"] = stored.get(key, float("nan"))
        row[f"diff_{key}"] = (
            row[key] - row[f"stored_{key}"]
            if pd.notna(row[f"stored_{key}"])
            else float("nan")
        )

    return row, monthly


def _load_stored_metrics(metrics_path: Path | None, strategy_col: str) -> dict[str, float]:
    if metrics_path is None or not metrics_path.exists():
        return {}
    try:
        df = pd.read_parquet(metrics_path)
    except Exception:
        return {}

    result: dict[str, float] = {}
    metric_names = [
        "annualized_return",
        "benchmark_return",
        "excess_return",
        "tracking_error",
        "information_ratio",
        "excess_max_drawdown",
        "monthly_win_rate",
    ]
    mapped_col = ""
    if strategy_col.startswith("strategy_"):
        mapped_col = strategy_col.replace("strategy_", "", 1)
    candidate_cols = [
        strategy_col,
        mapped_col,
        mapped_col.upper(),
        "value",
        "strategy_v2",
        "strategy",
        "v2",
        "V2",
    ]

    for metric in metric_names:
        value = np.nan
        if metric in df.index:
            for col in candidate_cols:
                if col in df.columns:
                    value = df.loc[metric, col]
                    break
            if pd.isna(value) and len(df.columns) == 1:
                value = df.loc[metric, df.columns[0]]
        elif metric in df.columns:
            for idx in [strategy_col, "strategy_v2", "strategy", "V2", "v2"]:
                if idx in df.index:
                    value = df.loc[idx, metric]
                    break
            if pd.isna(value) and len(df.index) == 1:
                value = df.iloc[0][metric]
        try:
            result[metric] = float(value)
        except (TypeError, ValueError):
            result[metric] = float("nan")
    return result


def _discover_nav_specs() -> list[NavSpec]:
    pipeline_b_metrics = (
        ROOT
        / "experiments"
        / "turnover_lambda_grid"
        / "results"
        / "lam_0050"
        / "backtest_metrics_valid.parquet"
    )
    rolling_metrics = (
        ROOT
        / "experiments"
        / "ridge_rolling"
        / "results"
        / "rolling_48m"
        / "backtest_metrics_valid.parquet"
    )
    specs = [
        NavSpec(
            source_id="pipeline_b_ridge_expanding_lam0050",
            nav_path=ROOT
            / "experiments"
            / "turnover_lambda_grid"
            / "results"
            / "lam_0050"
            / "backtest_nav_valid.parquet",
            metrics_path=pipeline_b_metrics,
            note="Pipeline B existing validation NAV",
        ),
        NavSpec(
            source_id="rolling_48m",
            nav_path=ROOT
            / "experiments"
            / "ridge_rolling"
            / "results"
            / "rolling_48m"
            / "backtest_nav_valid.parquet",
            metrics_path=rolling_metrics,
            note="Pipeline E rolling-48m existing validation NAV",
        ),
    ]

    pipeline_a_nav = cfg.DATA_PROC / "backtest_nav.parquet"
    pipeline_a_metrics = cfg.DATA_PROC / "backtest_metrics.parquet"
    if pipeline_a_nav.exists():
        df = pd.read_parquet(pipeline_a_nav)
        benchmark_col = "benchmark" if "benchmark" in df.columns else ""
        strategy_cols = [c for c in df.columns if str(c).startswith("strategy")]
        if benchmark_col and strategy_cols:
            primary_col = "strategy_v2" if "strategy_v2" in strategy_cols else strategy_cols[-1]
            for col in strategy_cols:
                specs.append(
                    NavSpec(
                        source_id=f"pipeline_a_{col}",
                        nav_path=pipeline_a_nav,
                        strategy_col=str(col),
                        benchmark_col=benchmark_col,
                        metrics_path=pipeline_a_metrics,
                        primary=col == primary_col,
                        note=(
                            "Pipeline A processed NAV; primary is strategy_v2 when present"
                            if col == primary_col
                            else "Pipeline A auxiliary strategy column"
                        ),
                    )
                )
    return specs


def build_metric_outputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    metric_rows: list[dict[str, Any]] = []
    monthly_frames: list[pd.DataFrame] = []

    for spec in _discover_nav_specs():
        if not spec.nav_path.exists():
            metric_rows.append(
                {
                    "source_id": spec.source_id,
                    "primary": spec.primary,
                    "nav_path": _rel(spec.nav_path),
                    "error": "missing nav file",
                }
            )
            continue
        try:
            nav_df = pd.read_parquet(spec.nav_path)
            row, monthly = _calc_metrics(
                source_id=spec.source_id,
                nav_df=nav_df,
                strategy_col=spec.strategy_col or "strategy",
                benchmark_col=spec.benchmark_col,
                nav_path=spec.nav_path,
                metrics_path=spec.metrics_path,
                primary=spec.primary,
                note=spec.note,
            )
            row["error"] = ""
            metric_rows.append(row)
            monthly_frames.append(monthly)
        except Exception as exc:  # pragma: no cover - audit should record and continue
            metric_rows.append(
                {
                    "source_id": spec.source_id,
                    "primary": spec.primary,
                    "nav_path": _rel(spec.nav_path),
                    "strategy_col": spec.strategy_col,
                    "benchmark_col": spec.benchmark_col,
                    "error": repr(exc),
                }
            )

    monthly_df = (
        pd.concat(monthly_frames, ignore_index=True)
        if monthly_frames
        else pd.DataFrame()
    )
    if not monthly_df.empty:
        first_cols = ["source_id", "month_end", "strategy_col", "benchmark_col", "nav_path"]
        other_cols = [c for c in monthly_df.columns if c not in first_cols]
        monthly_df = monthly_df[first_cols + other_cols]
    return pd.DataFrame(metric_rows), monthly_df


def _fmt_pct(value: float, digits: int = 2) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{value * 100:+.{digits}f}%"


def write_log(
    manifest: pd.DataFrame,
    config_snapshot: pd.DataFrame,
    metrics: pd.DataFrame,
    git_commit: str,
    git_status: str,
) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    dirty_lines = [line for line in git_status.splitlines() if line.strip()]
    missing = manifest.loc[~manifest["exists"].astype(bool), ["role", "path"]]
    coverage_notes = manifest.loc[
        manifest["coverage_note"].astype(str).ne(""),
        ["role", "date_min", "date_max", "coverage_note"],
    ]
    nav_row = manifest.loc[manifest["role"] == "index_quote"]
    index_has_nav = bool(nav_row["has_nav_col"].iloc[0]) if not nav_row.empty else False

    metric_lines: list[str] = []
    for row in metrics.to_dict("records"):
        if row.get("error"):
            metric_lines.append(f"- `{row.get('source_id')}`: ERROR {row.get('error')}")
            continue
        marker = "primary" if row.get("primary") else "aux"
        metric_lines.append(
            "- `{source}` ({marker}): IR={ir:.6f}, 年化超额={excess}, "
            "月胜率={win:.2%} ({pos}/{obs}), 月度净超额和={net}".format(
                source=row["source_id"],
                marker=marker,
                ir=row["information_ratio"],
                excess=_fmt_pct(row["excess_return"]),
                win=row["monthly_win_rate"],
                pos=int(row["monthly_positive_count"]),
                obs=int(row["monthly_obs"]),
                net=_fmt_pct(row["monthly_net_excess_sum"]),
            )
        )

    metric_warning_lines: list[str] = []
    for key in ["information_ratio", "monthly_win_rate", "excess_return"]:
        diff_col = f"diff_{key}"
        if diff_col not in metrics.columns:
            continue
        for row in metrics.to_dict("records"):
            diff = row.get(diff_col)
            if pd.notna(diff) and abs(float(diff)) > 1e-8:
                metric_warning_lines.append(
                    f"- `{row.get('source_id')}` {key} 复算差异 {float(diff):+.12f}"
                )

    lines = [
        "# 2026-05-26 阶段 0/1 审查日志",
        "",
        f"- 生成时间：{generated_at}",
        f"- 工作目录：`{ROOT}`",
        f"- 当前 commit：`{git_commit.splitlines()[0] if git_commit else 'N/A'}`",
        f"- git dirty 项数量：{len(dirty_lines)}",
        f"- 正式测试集：未运行；本脚本不读取 `scripts/run_test_pipeline.py` 产物。",
        "",
        "## 阶段 0：产物与口径冻结",
        "",
        f"- manifest 输出：`check/0526/artifact_manifest.csv`，共 {len(manifest)} 个对象。",
        f"- config 输出：`check/0526/config_snapshot.csv`，共 {len(config_snapshot)} 个参数。",
        f"- `index_quote.parquet` 是否包含全收益 `nav` 列：{index_has_nav}",
        f"- 缺失对象数量：{len(missing)}",
    ]
    if not missing.empty:
        lines.extend([f"  - `{r.path}` ({r.role})" for r in missing.itertuples(index=False)])
    if not coverage_notes.empty:
        lines.extend(["", "### 覆盖说明"])
        for r in coverage_notes.itertuples(index=False):
            lines.append(
                f"- `{r.role}`: {r.date_min or 'N/A'} ~ {r.date_max or 'N/A'}；{r.coverage_note}"
            )

    lines.extend(
        [
            "",
            "## 阶段 1：独立指标复算",
            "",
            f"- 指标输出：`check/0526/metric_recalc.csv`，共 {len(metrics)} 个 NAV/策略组合。",
            "- 逐月输出：`check/0526/monthly_excess_review.csv`。",
            "",
            *metric_lines,
            "",
            "## 与已存 metrics 文件差异",
            "",
        ]
    )
    if metric_warning_lines:
        lines.extend(metric_warning_lines)
    else:
        lines.append("- 未发现 `information_ratio`、`monthly_win_rate`、`excess_return` 的复算差异超过 `1e-8`。")

    lines.extend(
        [
            "",
            "## 当前阶段结论",
            "",
            "- 阶段 0/1 只确认“产物状态”和“NAV 指标是否能独立复算”，不对 rolling-48m 是否真实改进下最终结论。",
            "- 若某一异常值已经能从 NAV 独立复算出来，下一步应进入阶段 2：同一权重使用当前回测引擎重跑，排除产物新旧混用。",
            "",
        ]
    )

    (OUT_DIR / "log.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    git_commit = _run_git(["rev-parse", "HEAD"])
    git_status = _run_git(["status", "--short"])
    (OUT_DIR / "git_status.txt").write_text(
        f"commit:\n{git_commit}\n\nstatus --short:\n{git_status}\n",
        encoding="utf-8",
    )

    manifest = build_artifact_manifest()
    manifest.to_csv(OUT_DIR / "artifact_manifest.csv", index=False, encoding="utf-8-sig")

    config_snapshot = build_config_snapshot()
    config_snapshot.to_csv(OUT_DIR / "config_snapshot.csv", index=False, encoding="utf-8-sig")

    metrics, monthly = build_metric_outputs()
    metrics.to_csv(OUT_DIR / "metric_recalc.csv", index=False, encoding="utf-8-sig")
    monthly.to_csv(OUT_DIR / "monthly_excess_review.csv", index=False, encoding="utf-8-sig")

    write_log(manifest, config_snapshot, metrics, git_commit, git_status)

    print(f"Wrote {OUT_DIR / 'artifact_manifest.csv'}")
    print(f"Wrote {OUT_DIR / 'config_snapshot.csv'}")
    print(f"Wrote {OUT_DIR / 'metric_recalc.csv'}")
    print(f"Wrote {OUT_DIR / 'monthly_excess_review.csv'}")
    print(f"Wrote {OUT_DIR / 'log.md'}")


if __name__ == "__main__":
    main()
