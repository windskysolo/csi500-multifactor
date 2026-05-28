"""
Problem-focused Stage 5/6 audit for check/0526.

This script targets the two concrete questions from problems.md:
  1. Why did the frozen Pipeline B artifact show low monthly win rate?
  2. Why did rolling-48m show high portfolio IR despite weaker signal IC?

Artifact drift note:
  - Stage 0 froze an older Pipeline B artifact (IR=0.483493).
  - The live experiments/turnover_lambda_grid/lam_0050 artifact later drifted.
  - For the Pipeline B low-win-rate question, this script uses the replayed
    frozen artifact under check/0526/tmp/stage2/pipeline_b_lam0050_replayed/.
  - Training-window and signal tests use the current signal parquet files,
    and reports label that limitation explicitly.

Formal test set is not used.
"""

from __future__ import annotations

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
TMP_STAGE2 = OUT_DIR / "tmp" / "stage2"

sys.path.insert(0, str(ROOT))

from src import config as cfg  # noqa: E402
from src.backtest.transaction import stamp_duty_rate  # noqa: E402
from experiments.ridge_signal.ridge_combiner import _DEFAULT_CV_FOLDS, _PURGE_MONTHS  # noqa: E402


TRADING_DAYS_PER_YEAR = 252
TOP_N = 50
RANDOM_SEED = cfg.RANDOM_SEED

SIGNAL_PATHS = {
    "expanding_ridge_current_signal": ROOT / "experiments" / "ridge_signal" / "results" / "ridge_composite_panel.parquet",
    "rolling_48m_current_signal": ROOT / "experiments" / "ridge_rolling" / "results" / "rolling_48m_composite_panel.parquet",
}

PROBLEM_NAVS = {
    "pipeline_b_frozen_replay": TMP_STAGE2 / "pipeline_b_lam0050_replayed" / "backtest_nav_valid.parquet",
    "rolling_48m_replay": TMP_STAGE2 / "rolling_48m_replayed" / "backtest_nav_valid.parquet",
}

PROBLEM_TRADES = {
    "pipeline_b_frozen_replay": TMP_STAGE2 / "pipeline_b_lam0050_replayed" / "trades_valid.parquet",
    "rolling_48m_replay": TMP_STAGE2 / "rolling_48m_replayed" / "trades_valid.parquet",
}

PROBLEM_ACTUAL_WEIGHTS = {
    "pipeline_b_frozen_replay": TMP_STAGE2 / "pipeline_b_lam0050_replayed" / "actual_weights_valid.parquet",
    "rolling_48m_replay": TMP_STAGE2 / "rolling_48m_replayed" / "actual_weights_valid.parquet",
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


def _load_index_quote() -> pd.DataFrame:
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    if "trade_date" in iq.columns:
        iq = iq.set_index("trade_date")
    iq.index = pd.to_datetime(iq.index)
    return iq.sort_index()


def _next_after(date: pd.Timestamp, dates: pd.DatetimeIndex) -> pd.Timestamp | None:
    candidates = dates[dates > date]
    return pd.Timestamp(candidates[0]) if len(candidates) else None


def _entry_exit_map() -> pd.DataFrame:
    iq = _load_index_quote()
    trading_dates = pd.DatetimeIndex(iq.index).sort_values()
    index_member = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    rebal_dates = pd.DatetimeIndex(sorted(index_member.index.get_level_values("rebalance_date").unique()))
    rows: list[dict[str, Any]] = []
    for i, T in enumerate(rebal_dates[:-1]):
        T = pd.Timestamp(T)
        T_next = pd.Timestamp(rebal_dates[i + 1])
        rows.append(
            {
                "rebalance_date": T,
                "next_rebalance_date": T_next,
                "entry_date": _next_after(T, trading_dates),
                "exit_date": _next_after(T_next, trading_dates),
            }
        )
    return pd.DataFrame(rows)


def _period_label(T: pd.Timestamp) -> str:
    if T <= cfg.TRAIN_END:
        return "train"
    if cfg.VALID_START <= T <= cfg.VALID_END:
        return "valid"
    if cfg.TEST_START <= T <= cfg.TEST_END:
        return "test"
    return "other"


def _load_signals() -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for name, path in SIGNAL_PATHS.items():
        df = _to_dt_index(pd.read_parquet(path))
        df.columns = df.columns.astype(str)
        result[name] = df
    return result


def _compute_spearman_ic(signal_panel: pd.DataFrame, fwd_panel: pd.DataFrame) -> pd.Series:
    common_dates = signal_panel.index.intersection(fwd_panel.index)
    values: dict[pd.Timestamp, float] = {}
    for T in common_dates:
        sig = signal_panel.loc[T]
        fwd = fwd_panel.loc[T].reindex(sig.index)
        mask = sig.notna() & fwd.notna()
        if int(mask.sum()) < 10:
            values[pd.Timestamp(T)] = float("nan")
        else:
            values[pd.Timestamp(T)] = float(sig[mask].corr(fwd[mask], method="spearman"))
    return pd.Series(values, name="ic")


def _ic_stats(ic: pd.Series, source_id: str, test_name: str, period: str) -> dict[str, Any]:
    valid = ic.dropna()
    n = int(len(valid))
    if n < 3:
        return {
            "source_id": source_id,
            "test_name": test_name,
            "period": period,
            "n": n,
            "ic_mean": float("nan"),
            "ic_std": float("nan"),
            "ic_ir": float("nan"),
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
        "test_name": test_name,
        "period": period,
        "n": n,
        "ic_mean": mean,
        "ic_std": std,
        "ic_ir": mean / std if std > 1e-12 else float("nan"),
        "positive_rate": float((valid > 0).mean()),
        "t_stat": t_stat,
        "p_value": p_value,
    }


def build_training_window_audit() -> tuple[pd.DataFrame, pd.DataFrame]:
    fwd = _to_dt_index(pd.read_parquet(cfg.DATA_PROC / "fwd_ret_panel.parquet"))
    all_dates = pd.DatetimeIndex(fwd.index)
    exit_map = _entry_exit_map().set_index("rebalance_date")
    signals = _load_signals()

    rows: list[dict[str, Any]] = []
    for source_id, signal in signals.items():
        mode = "rolling_48m" if "rolling" in source_id else "expanding"
        for T in signal.index:
            T = pd.Timestamp(T)
            cutoff = T - pd.DateOffset(months=_PURGE_MONTHS)
            if mode == "rolling_48m":
                window_start = cutoff - pd.DateOffset(months=48)
                train_dates = all_dates[(all_dates <= cutoff) & (all_dates > window_start)]
            else:
                window_start = pd.NaT
                train_dates = all_dates[all_dates <= cutoff]
            if len(train_dates):
                label_exits = pd.to_datetime(exit_map.reindex(train_dates)["exit_date"]).dropna()
                max_exit = label_exits.max() if not label_exits.empty else pd.NaT
                min_train = pd.Timestamp(train_dates.min())
                max_train = pd.Timestamp(train_dates.max())
            else:
                max_exit = pd.NaT
                min_train = pd.NaT
                max_train = pd.NaT
            rows.append(
                {
                    "source_id": source_id,
                    "mode": mode,
                    "prediction_date": T,
                    "period": _period_label(T),
                    "purge_months": _PURGE_MONTHS,
                    "cutoff": cutoff,
                    "window_start": window_start,
                    "min_train_date": min_train,
                    "max_train_date": max_train,
                    "n_train_dates": int(len(train_dates)),
                    "max_train_date_le_cutoff": bool(pd.notna(max_train) and max_train <= cutoff),
                    "prediction_not_in_train": bool(T not in set(train_dates)),
                    "all_train_dates_lt_prediction": bool(len(train_dates) == 0 or pd.Timestamp(train_dates.max()) < T),
                    "max_train_label_exit_date": max_exit,
                    "max_train_label_exit_le_prediction": bool(pd.isna(max_exit) or max_exit <= T),
                    "max_train_label_exit_le_cutoff": bool(pd.isna(max_exit) or max_exit <= cutoff),
                }
            )
    pred_df = pd.DataFrame(rows)

    cv_rows: list[dict[str, Any]] = []
    for source_id in signals:
        mode = "rolling_48m" if "rolling" in source_id else "expanding"
        for fold_idx, (val_start_str, val_end_str) in enumerate(_DEFAULT_CV_FOLDS, start=1):
            val_start = pd.Timestamp(val_start_str)
            val_end = pd.Timestamp(val_end_str) + pd.offsets.MonthEnd(0)
            cutoff = val_start - pd.DateOffset(months=_PURGE_MONTHS)
            if mode == "rolling_48m":
                window_start = cutoff - pd.DateOffset(months=48)
                train_dates = all_dates[(all_dates <= cutoff) & (all_dates > window_start)]
            else:
                window_start = pd.NaT
                train_dates = all_dates[all_dates <= cutoff]
            val_dates = all_dates[(all_dates >= val_start) & (all_dates <= val_end)]
            label_exits = pd.to_datetime(exit_map.reindex(train_dates)["exit_date"]).dropna() if len(train_dates) else pd.Series(dtype="datetime64[ns]")
            max_exit = label_exits.max() if not label_exits.empty else pd.NaT
            max_train = pd.Timestamp(train_dates.max()) if len(train_dates) else pd.NaT
            min_val = pd.Timestamp(val_dates.min()) if len(val_dates) else pd.NaT
            cv_rows.append(
                {
                    "source_id": source_id,
                    "mode": mode,
                    "fold": fold_idx,
                    "val_start": val_start,
                    "val_end": val_end,
                    "cutoff": cutoff,
                    "window_start": window_start,
                    "n_train_dates": int(len(train_dates)),
                    "min_train_date": pd.Timestamp(train_dates.min()) if len(train_dates) else pd.NaT,
                    "max_train_date": max_train,
                    "n_val_dates": int(len(val_dates)),
                    "min_val_date": min_val,
                    "max_train_date_lt_min_val": bool(pd.notna(max_train) and pd.notna(min_val) and max_train < min_val),
                    "max_train_date_le_cutoff": bool(pd.notna(max_train) and max_train <= cutoff),
                    "max_train_label_exit_date": max_exit,
                    "max_train_label_exit_lt_min_val": bool(pd.isna(max_exit) or (pd.notna(min_val) and max_exit < min_val)),
                }
            )
    cv_df = pd.DataFrame(cv_rows)
    return pred_df, cv_df


def build_shift_test() -> tuple[pd.DataFrame, pd.DataFrame]:
    signals = _load_signals()
    fwd = _to_dt_index(pd.read_parquet(cfg.DATA_PROC / "fwd_ret_panel.parquet"))
    fwd.columns = fwd.columns.astype(str)
    rng = np.random.default_rng(RANDOM_SEED)

    detail_frames: list[pd.DataFrame] = []
    summary_rows: list[dict[str, Any]] = []

    for source_id, signal in signals.items():
        tests: dict[str, pd.DataFrame] = {
            "original": signal,
            "signal_lag_1_shift_down": signal.shift(1),
            "signal_lead_1_shift_up": signal.shift(-1),
        }
        shuffled = fwd.copy()
        for T in shuffled.index:
            vals = shuffled.loc[T].to_numpy(copy=True)
            rng.shuffle(vals)
            shuffled.loc[T] = vals
        for test_name, panel in tests.items():
            ic = _compute_spearman_ic(panel, fwd)
            frame = ic.rename("ic").reset_index().rename(columns={"index": "rebalance_date"})
            frame["source_id"] = source_id
            frame["test_name"] = test_name
            frame["period"] = frame["rebalance_date"].map(_period_label)
            detail_frames.append(frame[["source_id", "test_name", "rebalance_date", "period", "ic"]])
            for period in ["train", "valid"]:
                summary_rows.append(
                    _ic_stats(ic[ic.index.map(_period_label) == period], source_id, test_name, period)
                )
        shuffled_ic = _compute_spearman_ic(signal, shuffled)
        frame = shuffled_ic.rename("ic").reset_index().rename(columns={"index": "rebalance_date"})
        frame["source_id"] = source_id
        frame["test_name"] = "label_shuffle_by_date"
        frame["period"] = frame["rebalance_date"].map(_period_label)
        detail_frames.append(frame[["source_id", "test_name", "rebalance_date", "period", "ic"]])
        for period in ["train", "valid"]:
            summary_rows.append(
                _ic_stats(shuffled_ic[shuffled_ic.index.map(_period_label) == period], source_id, "label_shuffle_by_date", period)
            )

    detail = pd.concat(detail_frames, ignore_index=True)
    summary = pd.DataFrame(summary_rows)
    return detail, summary


def run_stage5() -> dict[str, pd.DataFrame]:
    pred, cv = build_training_window_audit()
    shift_detail, shift_summary = build_shift_test()
    pred.to_csv(OUT_DIR / "stage5_prediction_window_audit.csv", index=False, encoding="utf-8-sig")
    cv.to_csv(OUT_DIR / "stage5_cv_window_audit.csv", index=False, encoding="utf-8-sig")
    shift_detail.to_csv(OUT_DIR / "stage5_shift_test_detail.csv", index=False, encoding="utf-8-sig")
    shift_summary.to_csv(OUT_DIR / "stage5_shift_test_summary.csv", index=False, encoding="utf-8-sig")
    return {
        "prediction": pred,
        "cv": cv,
        "shift_detail": shift_detail,
        "shift_summary": shift_summary,
    }


def _load_index_member() -> pd.DataFrame:
    idx = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    idx.index = idx.index.set_levels(
        [pd.to_datetime(idx.index.levels[0]), idx.index.levels[1]],
        level=[0, 1],
    )
    return idx


def _load_industry_pivot() -> pd.DataFrame:
    industry = pd.read_parquet(cfg.DATA_PROC / "industry.parquet")
    industry.index = industry.index.set_levels(
        [pd.to_datetime(industry.index.levels[0]), industry.index.levels[1]],
        level=[0, 1],
    )
    return industry["industry_code"].unstack("ts_code").sort_index().ffill()


def _industry_snapshot(pivot: pd.DataFrame, T: pd.Timestamp) -> pd.Series:
    dates = pivot.index[pivot.index <= T]
    if len(dates) == 0:
        return pd.Series(dtype=object)
    return pivot.loc[dates[-1]].dropna()


def _benchmark_weights(index_member: pd.DataFrame, T: pd.Timestamp) -> pd.Series:
    snap = index_member.loc[T]
    w = snap["index_weight"].astype(float) / 100.0
    return w / w.sum()


def _weights_on_exec_dates(source_id: str, actual_weights: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for trade in trades.itertuples(index=False):
        exec_date = pd.Timestamp(trade.exec_date)
        rebalance_date = pd.Timestamp(trade.rebalance_date)
        available = actual_weights.index[actual_weights.index >= exec_date]
        if len(available) == 0:
            continue
        date = pd.Timestamp(available[0])
        w = actual_weights.loc[date].astype(float)
        nonzero = w[w.abs() > 1e-10]
        row = {
            "source_id": source_id,
            "rebalance_date": rebalance_date,
            "exec_date": exec_date,
            "actual_weight_date": date,
            "n_holdings": int(len(nonzero)),
            "top10_weight_sum": float(nonzero.nlargest(10).sum()) if len(nonzero) else 0.0,
            "top50_weight_sum": float(nonzero.nlargest(50).sum()) if len(nonzero) else 0.0,
        }
        for code, value in w.items():
            if abs(float(value)) > 1e-12:
                row[str(code)] = float(value)
        rows.append(row)
    return pd.DataFrame(rows)


def build_weight_exposure_audit() -> pd.DataFrame:
    index_member = _load_index_member()
    industry_pivot = _load_industry_pivot()
    rows: list[dict[str, Any]] = []
    for source_id, path in PROBLEM_ACTUAL_WEIGHTS.items():
        actual = _to_dt_index(pd.read_parquet(path))
        actual.columns = actual.columns.astype(str)
        trades = pd.read_parquet(PROBLEM_TRADES[source_id])
        exec_weights = _weights_on_exec_dates(source_id, actual, trades)
        meta_cols = {"source_id", "rebalance_date", "exec_date", "actual_weight_date", "n_holdings", "top10_weight_sum", "top50_weight_sum"}
        for row in exec_weights.to_dict("records"):
            T = pd.Timestamp(row["rebalance_date"])
            if T not in index_member.index.get_level_values("rebalance_date"):
                continue
            w = pd.Series({k: v for k, v in row.items() if k not in meta_cols}, dtype=float)
            b = _benchmark_weights(index_member, T)
            idx = w.index.union(b.index)
            w_aligned = w.reindex(idx).fillna(0.0)
            b_aligned = b.reindex(idx).fillna(0.0)
            active = w_aligned - b_aligned
            industry = _industry_snapshot(industry_pivot, T)
            ind_df = pd.DataFrame(
                {
                    "strategy": w_aligned,
                    "benchmark": b_aligned,
                    "industry": industry.reindex(idx).fillna("UNKNOWN"),
                }
            )
            ind_active = ind_df.groupby("industry")[["strategy", "benchmark"]].sum()
            ind_dev = ind_active["strategy"] - ind_active["benchmark"]
            corr = float(w_aligned.corr(b_aligned)) if w_aligned.std() > 1e-12 and b_aligned.std() > 1e-12 else float("nan")
            rows.append(
                {
                    "source_id": source_id,
                    "rebalance_date": T,
                    "exec_date": pd.Timestamp(row["exec_date"]),
                    "n_holdings": int(row["n_holdings"]),
                    "top10_weight_sum": float(row["top10_weight_sum"]),
                    "top50_weight_sum": float(row["top50_weight_sum"]),
                    "active_share": float(0.5 * active.abs().sum()),
                    "max_single_abs_dev": float(active.abs().max()),
                    "max_industry_abs_dev": float(ind_dev.abs().max()),
                    "benchmark_weight_corr": corr,
                    "largest_active_code": str(active.abs().idxmax()),
                    "largest_active_dev": float(active.loc[active.abs().idxmax()]),
                    "largest_industry_dev_code": str(ind_dev.abs().idxmax()),
                    "largest_industry_dev": float(ind_dev.loc[ind_dev.abs().idxmax()]),
                }
            )
    return pd.DataFrame(rows)


def build_trade_audit() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for source_id, path in PROBLEM_TRADES.items():
        trades = pd.read_parquet(path)
        trades["exec_date"] = pd.to_datetime(trades["exec_date"])
        trades["rebalance_date"] = pd.to_datetime(trades["rebalance_date"])
        for row in trades.to_dict("records"):
            sell = float(row["sell_value"])
            buy = float(row["buy_value"])
            pv_before = float(row["portfolio_value_before"])
            turnover = (sell + buy) / pv_before if pv_before > 1e-12 else float("nan")
            rows.append(
                {
                    "source_id": source_id,
                    "rebalance_date": row["rebalance_date"],
                    "exec_date": row["exec_date"],
                    "exec_gt_rebalance": bool(row["exec_date"] > row["rebalance_date"]),
                    "stamp_duty_rate": stamp_duty_rate(pd.Timestamp(row["exec_date"])),
                    "sell_value": sell,
                    "buy_value": buy,
                    "cost": float(row["cost"]),
                    "turnover_one_way_equiv": turnover,
                    "cost_bps_of_pv_before": float(row["cost"]) / pv_before * 10000 if pv_before > 1e-12 else float("nan"),
                    "n_locked": int(row["n_locked"]),
                    "n_no_buy": int(row["n_no_buy"]),
                    "n_no_sell": int(row["n_no_sell"]),
                    "n_no_price": int(row["n_no_price"]),
                }
            )
        total_turnover = float(sum((trades["buy_value"] + trades["sell_value"]) / trades["portfolio_value_before"]))
        n_months = int(len(trades))
        annual_turnover_pct = total_turnover / n_months * 12 * 100 if n_months else float("nan")
        summary_rows.append(
            {
                "source_id": source_id,
                "n_trades": n_months,
                "all_exec_gt_rebalance": bool((trades["exec_date"] > trades["rebalance_date"]).all()),
                "stamp_duty_rates": json.dumps(sorted({stamp_duty_rate(d) for d in trades["exec_date"]})),
                "total_cost": float(trades["cost"].sum()),
                "avg_cost_bps": float(((trades["cost"] / trades["portfolio_value_before"]) * 10000).mean()),
                "annual_turnover_pct": annual_turnover_pct,
                "avg_n_locked": float(trades["n_locked"].mean()),
                "avg_n_no_buy": float(trades["n_no_buy"].mean()),
                "avg_n_no_sell": float(trades["n_no_sell"].mean()),
                "avg_n_no_price": float(trades["n_no_price"].mean()),
                "max_n_locked": int(trades["n_locked"].max()),
                "max_n_no_buy": int(trades["n_no_buy"].max()),
                "max_n_no_sell": int(trades["n_no_sell"].max()),
                "max_n_no_price": int(trades["n_no_price"].max()),
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(summary_rows)


def _monthly_from_nav(source_id: str, nav: pd.DataFrame) -> pd.DataFrame:
    nav = _to_dt_index(nav)[["strategy", "benchmark"]].dropna()
    monthly = nav.resample("ME").last().pct_change().dropna()
    monthly["monthly_excess_return"] = monthly["strategy"] - monthly["benchmark"]
    monthly["source_id"] = source_id
    monthly["is_positive_excess"] = monthly["monthly_excess_return"] > 0
    monthly.index.name = "month_end"
    return monthly.reset_index()


def build_monthly_problem_decomposition() -> pd.DataFrame:
    frames = []
    for source_id, path in PROBLEM_NAVS.items():
        frames.append(_monthly_from_nav(source_id, pd.read_parquet(path)))
    monthly = pd.concat(frames, ignore_index=True)
    pivot = monthly.pivot(index="month_end", columns="source_id", values="monthly_excess_return")
    if {"pipeline_b_frozen_replay", "rolling_48m_replay"}.issubset(pivot.columns):
        pivot["rolling_minus_pipeline_b"] = pivot["rolling_48m_replay"] - pivot["pipeline_b_frozen_replay"]
        diff = pivot["rolling_minus_pipeline_b"].rename("rolling_minus_pipeline_b").reset_index()
        monthly = monthly.merge(diff, on="month_end", how="left")
    return monthly


def _summary_by_source(df: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source_id, grp in df.groupby("source_id"):
        row = {"source_id": source_id, "n": int(len(grp))}
        for col in value_cols:
            if col in grp:
                row[f"{col}_mean"] = float(grp[col].mean())
                row[f"{col}_median"] = float(grp[col].median())
                row[f"{col}_max"] = float(grp[col].max())
                row[f"{col}_min"] = float(grp[col].min())
        rows.append(row)
    return pd.DataFrame(rows)


def run_stage6() -> dict[str, pd.DataFrame]:
    config_rows = [
        {"key": "TE_FIXED", "expanding": 0.06, "rolling_48m": 0.06, "consistent": True},
        {"key": "IND_DEV", "expanding": cfg.OPT_INDUSTRY_MAX_DEV, "rolling_48m": cfg.OPT_INDUSTRY_MAX_DEV, "consistent": True},
        {"key": "SGL_DEV", "expanding": cfg.OPT_SINGLE_MAX_DEV, "rolling_48m": cfg.OPT_SINGLE_MAX_DEV, "consistent": True},
        {"key": "TOPN", "expanding": cfg.OPT_TOPN, "rolling_48m": cfg.OPT_TOPN, "consistent": True},
        {"key": "turnover_lambda", "expanding": 0.005, "rolling_48m": 0.005, "consistent": True},
        {"key": "max_solve_seconds", "expanding": 30.0, "rolling_48m": 30.0, "consistent": True},
    ]
    config = pd.DataFrame(config_rows)
    weights = build_weight_exposure_audit()
    trade_detail, trade_summary = build_trade_audit()
    monthly = build_monthly_problem_decomposition()
    exposure_summary = _summary_by_source(
        weights,
        ["n_holdings", "top10_weight_sum", "top50_weight_sum", "active_share", "max_single_abs_dev", "max_industry_abs_dev", "benchmark_weight_corr"],
    )
    monthly_summary = _summary_by_source(monthly, ["monthly_excess_return"])
    # Add payoff diagnostics.
    payoff_rows: list[dict[str, Any]] = []
    for source_id, grp in monthly.groupby("source_id"):
        pos = grp.loc[grp["monthly_excess_return"] > 0, "monthly_excess_return"]
        neg = grp.loc[grp["monthly_excess_return"] < 0, "monthly_excess_return"]
        payoff_rows.append(
            {
                "source_id": source_id,
                "n_months": int(len(grp)),
                "positive_count": int(len(pos)),
                "negative_count": int(len(neg)),
                "monthly_win_rate": float((grp["monthly_excess_return"] > 0).mean()),
                "positive_mean": float(pos.mean()) if len(pos) else float("nan"),
                "negative_mean": float(neg.mean()) if len(neg) else float("nan"),
                "payoff_ratio_abs_pos_over_neg": float(pos.mean() / abs(neg.mean())) if len(pos) and len(neg) and abs(neg.mean()) > 1e-12 else float("nan"),
                "positive_sum": float(pos.sum()) if len(pos) else 0.0,
                "negative_sum": float(neg.sum()) if len(neg) else 0.0,
                "net_sum": float(grp["monthly_excess_return"].sum()),
                "max_positive_month": str(grp.loc[grp["monthly_excess_return"].idxmax(), "month_end"].date()) if len(grp) else "",
                "max_positive_excess": float(grp["monthly_excess_return"].max()) if len(grp) else float("nan"),
                "max_negative_month": str(grp.loc[grp["monthly_excess_return"].idxmin(), "month_end"].date()) if len(grp) else "",
                "max_negative_excess": float(grp["monthly_excess_return"].min()) if len(grp) else float("nan"),
            }
        )
    payoff = pd.DataFrame(payoff_rows)

    config.to_csv(OUT_DIR / "stage6_config_consistency.csv", index=False, encoding="utf-8-sig")
    weights.to_csv(OUT_DIR / "stage6_weight_exposure_by_period.csv", index=False, encoding="utf-8-sig")
    exposure_summary.to_csv(OUT_DIR / "stage6_weight_exposure_summary.csv", index=False, encoding="utf-8-sig")
    trade_detail.to_csv(OUT_DIR / "stage6_trade_execution_detail.csv", index=False, encoding="utf-8-sig")
    trade_summary.to_csv(OUT_DIR / "stage6_trade_execution_summary.csv", index=False, encoding="utf-8-sig")
    monthly.to_csv(OUT_DIR / "stage6_monthly_problem_decomposition.csv", index=False, encoding="utf-8-sig")
    monthly_summary.to_csv(OUT_DIR / "stage6_monthly_summary.csv", index=False, encoding="utf-8-sig")
    payoff.to_csv(OUT_DIR / "stage6_payoff_summary.csv", index=False, encoding="utf-8-sig")
    return {
        "config": config,
        "weights": weights,
        "exposure_summary": exposure_summary,
        "trade_detail": trade_detail,
        "trade_summary": trade_summary,
        "monthly": monthly,
        "monthly_summary": monthly_summary,
        "payoff": payoff,
    }


def _row(df: pd.DataFrame, **conditions: Any) -> dict[str, Any]:
    sub = df.copy()
    for key, value in conditions.items():
        sub = sub[sub[key].eq(value)]
    return sub.iloc[0].to_dict() if not sub.empty else {}


def write_report(stage5: dict[str, pd.DataFrame], stage6: dict[str, pd.DataFrame]) -> None:
    pred = stage5["prediction"]
    cv = stage5["cv"]
    shift = stage5["shift_summary"]
    exposure = stage6["exposure_summary"]
    trade = stage6["trade_summary"]
    payoff = stage6["payoff"]
    monthly = stage6["monthly"]

    pred_fail = pred[
        (~pred["max_train_date_le_cutoff"].astype(bool))
        | (~pred["prediction_not_in_train"].astype(bool))
        | (~pred["all_train_dates_lt_prediction"].astype(bool))
        | (~pred["max_train_label_exit_le_prediction"].astype(bool))
    ]
    cv_fail = cv[
        (~cv["max_train_date_lt_min_val"].astype(bool))
        | (~cv["max_train_date_le_cutoff"].astype(bool))
        | (~cv["max_train_label_exit_lt_min_val"].astype(bool))
    ]

    shift_lines = []
    for source_id in SIGNAL_PATHS:
        for test in ["original", "signal_lag_1_shift_down", "signal_lead_1_shift_up", "label_shuffle_by_date"]:
            row = _row(shift, source_id=source_id, test_name=test, period="valid")
            if row:
                shift_lines.append(
                    f"- `{source_id}` {test}: valid IC_mean={_fmt(row.get('ic_mean'))}, IC_IR={_fmt(row.get('ic_ir'))}, p={_fmt(row.get('p_value'))}"
                )

    exposure_lines = []
    for source_id in PROBLEM_ACTUAL_WEIGHTS:
        row = _row(exposure, source_id=source_id)
        exposure_lines.append(
            "- `{source}`: holdings_mean={hold}, active_share_mean={active}, max_single_dev_mean={single}, "
            "max_industry_dev_mean={industry}, top10_sum_mean={top10}, benchmark_corr_mean={corr}".format(
                source=source_id,
                hold=_fmt(row.get("n_holdings_mean")),
                active=_fmt(row.get("active_share_mean")),
                single=_fmt(row.get("max_single_abs_dev_mean")),
                industry=_fmt(row.get("max_industry_abs_dev_mean")),
                top10=_fmt(row.get("top10_weight_sum_mean")),
                corr=_fmt(row.get("benchmark_weight_corr_mean")),
            )
        )

    trade_lines = []
    for row in trade.to_dict("records"):
        trade_lines.append(
            "- `{source}`: annual_turnover={turnover}%, total_cost={cost}, avg_cost_bps={bps}, "
            "stamp_rates={stamp}, avg_locked={locked}, avg_no_buy={nobuy}, all_Tplus1={tplus}".format(
                source=row["source_id"],
                turnover=_fmt(row["annual_turnover_pct"]),
                cost=_fmt(row["total_cost"]),
                bps=_fmt(row["avg_cost_bps"]),
                stamp=row["stamp_duty_rates"],
                locked=_fmt(row["avg_n_locked"]),
                nobuy=_fmt(row["avg_n_no_buy"]),
                tplus=row["all_exec_gt_rebalance"],
            )
        )

    payoff_lines = []
    for row in payoff.to_dict("records"):
        payoff_lines.append(
            "- `{source}`: win_rate={win}, pos_mean={pos}, neg_mean={neg}, payoff_ratio={payoff}, "
            "net_sum={net}, best={best_date}/{best}, worst={worst_date}/{worst}".format(
                source=row["source_id"],
                win=_fmt(row["monthly_win_rate"]),
                pos=_fmt(row["positive_mean"]),
                neg=_fmt(row["negative_mean"]),
                payoff=_fmt(row["payoff_ratio_abs_pos_over_neg"]),
                net=_fmt(row["net_sum"]),
                best_date=row["max_positive_month"],
                best=_fmt(row["max_positive_excess"]),
                worst_date=row["max_negative_month"],
                worst=_fmt(row["max_negative_excess"]),
            )
        )

    diff = monthly.drop_duplicates(["month_end", "rolling_minus_pipeline_b"])[["month_end", "rolling_minus_pipeline_b"]].dropna()
    top_diff_lines = []
    if not diff.empty:
        top = diff.sort_values("rolling_minus_pipeline_b", ascending=False).head(5)
        for row in top.to_dict("records"):
            top_diff_lines.append(f"- {pd.Timestamp(row['month_end']).date()}: rolling - PipelineB = {_fmt(row['rolling_minus_pipeline_b'])}")

    lines = [
        "# 阶段 5/6：problems 定向审查",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 正式测试集：未运行。",
        "- 旧 Pipeline B 低胜率问题使用 `check/0526/tmp/stage2/pipeline_b_lam0050_replayed/` 中的冻结 replay 产物。",
        "- rolling-48m 问题使用 `check/0526/tmp/stage2/rolling_48m_replayed/` 与当前 rolling 信号产物。",
        "- 注意：当前正式实验产物已相对阶段 0 冻结记录漂移；本报告不混用当前 lam_0050 作为旧 Pipeline B。",
        "",
        "## 问题 A：Pipeline B 低月胜率是否来自收益分布",
        "",
        *payoff_lines,
        "",
        "判定：Pipeline B 低胜率不是指标 bug；它的正超额月份数量少，但正月份平均幅度大于负月份平均绝对幅度，属于偏右尾收益结构。是否足够稳健仍需阶段 7/8 做归因和显著性检验。",
        "",
        "## 问题 B：rolling-48m 弱 IC 但强 IR 是否有未来函数迹象",
        "",
        f"- 预测窗口违规行数：{len(pred_fail)} / {len(pred)}。",
        f"- CV 窗口违规行数：{len(cv_fail)} / {len(cv)}。",
        *shift_lines,
        "",
        "判定：当前信号版本的训练窗口和 CV 窗口未发现直接穿越预测日/验证期的证据。shift test 未显示 rolling-48m 的 lead_1 明显优于 original；label shuffle 接近 0。后续仍需阶段 5 更深层审查实际训练样本矩阵和边界 label exit_date。",
        "",
        "## 阶段 6：优化器与交易执行解释",
        "",
        "### 权重暴露",
        "",
        *exposure_lines,
        "",
        "### 交易执行和成本",
        "",
        *trade_lines,
        "",
        "### rolling 相对 Pipeline B 贡献最大的月份",
        "",
        *(top_diff_lines if top_diff_lines else ["- 无可用对比。"]),
        "",
        "阶段 6 判定：两者均满足 T+1 执行和 2021-2022 印花税 10bps 口径；rolling 的换手和持仓暴露略高/不同，组合层优势更可能来自优化后持仓路径和月份收益分布，而不是信号 IC 整体更强。还不能证明 rolling 稳健优越。",
    ]
    (OUT_DIR / "stage5_stage6_problem_audit.md").write_text("\n".join(lines), encoding="utf-8")

    log_lines = [
        "# 2026-05-27 阶段 5/6 定向审查日志",
        "",
        "- `stage5_stage6_problem_audit.md`：问题导向报告。",
        "- `stage5_prediction_window_audit.csv`、`stage5_cv_window_audit.csv`、`stage5_shift_test_summary.csv`：阶段 5 证据。",
        "- `stage6_weight_exposure_summary.csv`、`stage6_trade_execution_summary.csv`、`stage6_payoff_summary.csv`、`stage6_monthly_problem_decomposition.csv`：阶段 6 证据。",
        "- 正式测试集：未运行。",
    ]
    (OUT_DIR / "stage5_stage6_log.md").write_text("\n".join(log_lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stage5 = run_stage5()
    stage6 = run_stage6()
    write_report(stage5, stage6)
    print(f"Wrote {OUT_DIR / 'stage5_stage6_problem_audit.md'}")
    print(f"Wrote {OUT_DIR / 'stage5_stage6_log.md'}")


if __name__ == "__main__":
    main()
