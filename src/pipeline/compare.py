"""
src/pipeline/compare.py — 多 run 横向比较逻辑
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List, Optional

import pandas as pd

_PROJECT_ROOT = Path(__file__).parents[2]
_DEFAULT_RUNS_ROOT = _PROJECT_ROOT / "runs"
_DEFAULT_REGISTRY_DIR = _PROJECT_ROOT / "registry"

# Hard threshold constants
IR_MIN = 0.5
MDD_MAX_ABS = 0.10
TO_MIN_PCT = 500.0
TO_MAX_PCT = 1500.0


def load_run_metrics(
    run_id: str,
    scope: str = "train_valid",
    runs_root: Optional[Path] = None,
) -> dict:
    """
    读取单个 run 的配置 + 回测指标，返回扁平化字典。

    Args:
        run_id: run 目录名
        scope: run 所在 scope（train_valid / test）
        runs_root: runs/ 根目录

    Returns:
        dict with keys: run_id, experiment_id, signal_method, training_mode,
        window_months, te_target_pct, turnover_lambda, topn,
        is_test_set, composite_sha256,
        IC_mean, IC_IR, IC_t, IC_p,
        fallback_L0_cnt … fallback_L3_cnt,
        IR, excess_return_pct, excess_max_drawdown_pct,
        tracking_error_pct, monthly_win_rate_pct, annual_turnover_pct,
        ir_pass, mdd_pass, to_pass, status
    """
    root = runs_root or _DEFAULT_RUNS_ROOT
    run_dir = root / scope / run_id
    row: dict = {"run_id": run_id, "scope": scope}

    config_path = run_dir / "run_config.json"
    if not config_path.exists():
        row["status"] = "no_config"
        return row

    config = json.loads(config_path.read_text(encoding="utf-8"))
    spec = config.get("spec", {})
    signal = spec.get("signal", {})
    opt = spec.get("optimizer", {})

    row["experiment_id"] = spec.get("experiment_id", run_id)
    row["signal_method"] = signal.get("method")
    row["training_mode"] = signal.get("training_mode")
    row["window_months"] = signal.get("window_months")
    row["te_target_pct"] = _round(opt.get("te_target_annual", float("nan")), scale=100, dp=1)
    row["turnover_lambda"] = opt.get("turnover_lambda")
    row["topn"] = opt.get("topn")
    row["is_test_set"] = bool(spec.get("allow_test_set", False))

    # Artifact hash (composite.parquet sha256 前 8 位，供快速审计)
    row["composite_sha256"] = _read_composite_hash(run_dir)

    finished = (run_dir / "RUN_FINISHED.json").exists()
    if not finished:
        row["status"] = "incomplete"
        return row

    # IC stats（验证期，从 composite.parquet + fwd_ret_panel 动态计算）
    row.update(_compute_ic_stats(run_dir))

    # Fallback 统计（来自 portfolio/optimizer_meta.parquet）
    row.update(_load_fallback_counts(run_dir))

    metrics_path = run_dir / "backtest" / "metrics_valid.parquet"
    if not metrics_path.exists():
        row["status"] = "no_metrics"
        return row

    metrics = pd.read_parquet(metrics_path)
    v2 = metrics["v2"] if "v2" in metrics.columns else metrics.iloc[:, -1]

    ir = _safe_float(v2.get("information_ratio"))
    mdd = _safe_float(v2.get("excess_max_drawdown"))
    excess_r = _safe_float(v2.get("excess_return"))
    te = _safe_float(v2.get("tracking_error"))
    win_rate = _safe_float(v2.get("monthly_win_rate"))

    trades_path = run_dir / "backtest" / "trades_valid.parquet"
    annual_to = _compute_annual_turnover(trades_path)

    row["IR"] = _round(ir, dp=3)
    row["excess_return_pct"] = _round(excess_r, scale=100, dp=2)
    row["excess_max_drawdown_pct"] = _round(mdd, scale=100, dp=2)
    row["tracking_error_pct"] = _round(te, scale=100, dp=2)
    row["monthly_win_rate_pct"] = _round(win_rate, scale=100, dp=1)
    row["annual_turnover_pct"] = _round(annual_to, dp=0)

    row["ir_pass"] = not math.isnan(ir) and ir >= IR_MIN
    row["mdd_pass"] = not math.isnan(mdd) and abs(mdd) <= MDD_MAX_ABS
    row["to_pass"] = not math.isnan(annual_to) and TO_MIN_PCT <= annual_to <= TO_MAX_PCT
    row["status"] = "finished"

    return row


def compare_runs(
    run_ids: List[str],
    scope: str = "train_valid",
    runs_root: Optional[Path] = None,
) -> pd.DataFrame:
    """
    比较多个 run 的关键指标。

    Args:
        run_ids: 要比较的 run_id 列表
        scope: run 所在 scope
        runs_root: runs/ 根目录

    Returns:
        DataFrame，每行一个 run，按 IR 降序排列（NaN 排末）
    """
    rows = [load_run_metrics(r, scope, runs_root) for r in run_ids]
    df = pd.DataFrame(rows)

    col_order = [
        "run_id", "experiment_id", "signal_method", "training_mode", "window_months",
        "te_target_pct", "turnover_lambda", "topn", "is_test_set", "composite_sha256",
        "IC_mean", "IC_IR", "IC_t", "IC_p",
        "fallback_L0_cnt", "fallback_L1_cnt", "fallback_L2_cnt", "fallback_L3_cnt",
        "IR", "excess_return_pct", "excess_max_drawdown_pct",
        "tracking_error_pct", "monthly_win_rate_pct", "annual_turnover_pct",
        "ir_pass", "mdd_pass", "to_pass", "status",
    ]
    cols = [c for c in col_order if c in df.columns]
    df = df[cols]

    if "IR" in df.columns:
        df = df.sort_values("IR", ascending=False, na_position="last")

    return df.reset_index(drop=True)


def build_board_from_registry(
    scope: str = "train_valid",
    runs_root: Optional[Path] = None,
    registry_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """
    从 registry/mainline.json + registry/challengers.json 自动收集所有 run，
    并附加 paired_p_vs_mainline（challenger 与主线月度超额收益的配对 t 检验 p 值）。

    Returns:
        比较 DataFrame，带 role 列和 paired_p_vs_mainline 列
    """
    from src.pipeline.registry import load_challengers, load_mainline

    reg_dir = registry_dir or _DEFAULT_REGISTRY_DIR
    root = runs_root or _DEFAULT_RUNS_ROOT

    run_entries: list[tuple[str, str]] = []
    mainline_run_id: str | None = None

    try:
        mainline = load_mainline(reg_dir)
        mainline_run_id = mainline["active_run_id"]
        run_entries.append((mainline_run_id, "mainline"))
    except FileNotFoundError:
        pass

    challengers = load_challengers(reg_dir)
    for c in challengers.get("challengers", []):
        rid = c.get("run_id")
        if rid:
            run_entries.append((rid, f"challenger/{c.get('name', rid)}"))

    if not run_entries:
        return pd.DataFrame()

    run_ids = [r for r, _ in run_entries]
    role_map = {r: role for r, role in run_entries}

    df = compare_runs(run_ids, scope=scope, runs_root=runs_root)
    df.insert(1, "role", df["run_id"].map(role_map).fillna("unknown"))

    # paired_p_vs_mainline: 每个 challenger 的月度超额收益与主线做配对 t 检验
    if mainline_run_id:
        mainline_nav_path = root / scope / mainline_run_id / "backtest" / "nav_valid.parquet"
        mainline_exc = _monthly_excess_returns(mainline_nav_path)

        def _paired_p(run_id: str) -> float:
            if run_id == mainline_run_id:
                return float("nan")
            nav_path = root / scope / run_id / "backtest" / "nav_valid.parquet"
            challenger_exc = _monthly_excess_returns(nav_path)
            return _compute_paired_p(mainline_exc, challenger_exc)

        df["paired_p_vs_mainline"] = df["run_id"].apply(_paired_p)

    return df


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _round(v, scale: float = 1, dp: int = 2) -> float:
    """Scale a value and round to dp decimal places; NaN passes through."""
    try:
        raw = float(v) * scale
        return round(raw, dp)
    except (TypeError, ValueError):
        return float("nan")


def _compute_annual_turnover(trades_path: Path) -> float:
    """Compute annualized two-way turnover (%) from trades_valid.parquet."""
    if not trades_path.exists():
        return float("nan")
    try:
        tl = pd.read_parquet(trades_path)
        if tl.empty or "buy_value" not in tl.columns:
            return float("nan")
        total_to = (
            (tl["buy_value"] + tl["sell_value"]) / tl["portfolio_value_before"]
        ).sum()
        n_months = len(tl)
        if n_months == 0:
            return float("nan")
        return total_to / n_months * 12 * 100
    except Exception:
        return float("nan")


def _read_composite_hash(run_dir: Path) -> str:
    """Read composite.parquet sha256 (first 8 chars) from manifest.json."""
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return ""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key, val in manifest.items():
            if "composite.parquet" in key:
                return val.get("sha256", "")[:8]
        return ""
    except Exception:
        return ""


def _load_fallback_counts(run_dir: Path) -> dict:
    """Count fallback levels from portfolio/optimizer_meta.parquet."""
    nan = float("nan")
    empty = {f"fallback_L{i}_cnt": nan for i in range(4)}
    meta_path = run_dir / "portfolio" / "optimizer_meta.parquet"
    if not meta_path.exists():
        return empty
    try:
        meta = pd.read_parquet(meta_path)
        counts = meta["fallback_level"].value_counts()
        return {f"fallback_L{i}_cnt": int(counts.get(i, 0)) for i in range(4)}
    except Exception:
        return empty


def _compute_ic_stats(run_dir: Path) -> dict:
    """
    Compute IC statistics for the validation period using composite.parquet
    and fwd_ret_panel.parquet from data/processed/.

    Returns dict: IC_mean, IC_IR, IC_t, IC_p (float, NaN on failure).
    """
    nan = float("nan")
    empty = {"IC_mean": nan, "IC_IR": nan, "IC_t": nan, "IC_p": nan}

    composite_path = run_dir / "signal" / "composite.parquet"
    if not composite_path.exists():
        return empty

    try:
        import numpy as np
        from scipy import stats as sp_stats
        from src import config as cfg

        fwd_path = cfg.DATA_PROC / "fwd_ret_panel.parquet"
        if not fwd_path.exists():
            return empty

        composite = pd.read_parquet(composite_path)
        fwd_ret = pd.read_parquet(fwd_path)

        valid_start = pd.Timestamp(cfg.VALID_START)
        valid_end = pd.Timestamp(cfg.VALID_END)

        common_dates = composite.index.intersection(fwd_ret.index)
        common_dates = common_dates[(common_dates >= valid_start) & (common_dates <= valid_end)]
        if len(common_dates) < 4:
            return empty

        common_stocks = composite.columns.intersection(fwd_ret.columns)
        comp = composite.loc[common_dates, common_stocks]
        fwd = fwd_ret.loc[common_dates, common_stocks]

        ic_list = []
        for dt in common_dates:
            sig = comp.loc[dt].dropna()
            ret = fwd.loc[dt].dropna()
            aligned = sig.index.intersection(ret.index)
            if len(aligned) < 10:
                continue
            ic_val, _ = sp_stats.spearmanr(sig[aligned].values, ret[aligned].values)
            if not math.isnan(float(ic_val)):
                ic_list.append(float(ic_val))

        if len(ic_list) < 4:
            return empty

        ic_arr = np.array(ic_list)
        ic_mean = float(np.mean(ic_arr))
        ic_std = float(np.std(ic_arr, ddof=1))
        if ic_std == 0:
            return empty

        ic_ir = ic_mean / ic_std
        n = len(ic_arr)
        ic_t = ic_ir * math.sqrt(n)
        ic_p = float(2 * (1 - sp_stats.t.cdf(abs(ic_t), df=n - 1)))

        return {
            "IC_mean": round(ic_mean, 4),
            "IC_IR": round(ic_ir, 3),
            "IC_t": round(ic_t, 3),
            "IC_p": round(ic_p, 4),
        }
    except Exception:
        return empty


def _monthly_excess_returns(nav_path: Path) -> pd.Series:
    """Compute monthly excess return series (strategy_v2 / benchmark) from nav_valid.parquet."""
    try:
        nav = pd.read_parquet(nav_path)
        excess_daily = nav["strategy_v2"] / nav["benchmark"]
        monthly = excess_daily.resample("ME").last()
        return monthly.pct_change().dropna()
    except Exception:
        return pd.Series(dtype=float)


def _compute_paired_p(mainline_exc: pd.Series, challenger_exc: pd.Series) -> float:
    """Paired t-test p-value between challenger and mainline monthly excess returns."""
    try:
        from scipy import stats as sp_stats
        common = mainline_exc.index.intersection(challenger_exc.index)
        if len(common) < 8:
            return float("nan")
        _, p = sp_stats.ttest_rel(
            challenger_exc.loc[common].values,
            mainline_exc.loc[common].values,
        )
        return round(float(p), 4)
    except Exception:
        return float("nan")
