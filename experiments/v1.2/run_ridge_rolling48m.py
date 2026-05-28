"""
experiments/v1.2/run_ridge_rolling48m.py
=========================================

用 v1.2 的 18 因子集，重跑 Ridge rolling-48m，结果写入
experiments/v1.2/results/ridge_rolling_48m/

对比基准：v1.2 expanding（IR=0.503）

用法：
    python -m experiments.v1_2.run_ridge_rolling48m
"""

import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from src.portfolio.covariance import validate_and_repair_covariance
from src.portfolio.optimizer import OptimizeConfig, optimize_single_period
from src.backtest.engine import BacktestConfig, run_backtest

import json

from experiments.legacy.ridge_signal.run_ridge_experiment import (
    load_factor_panels,
    load_fwd_ret,
    compute_bench_ret_series,
    compute_ic_series,
    compute_ic_stats,
    split_ic_by_period,
)
from experiments.legacy.ridge_rolling.rolling_combiner import RidgeRollingCombiner

# _ROOT in the legacy script is wrong after moving into legacy/; define path locally
_FINAL_FACTORS_PATH = _ROOT / "reports" / "factor_evaluation" / "final_factors.json"


def load_final_factors() -> list[str]:
    with open(_FINAL_FACTORS_PATH, encoding="utf-8") as f:
        d = json.load(f)
    factors = d.get("final_factors", d.get("factors", []))
    log.info("加载 %d 个因子: %s", len(factors), factors)
    return factors

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

WINDOW_MONTHS = 48
LAM           = 0.005
TE_FIXED      = 0.06
ALPHA_CANDS   = [0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0]

RESULTS_DIR = Path(__file__).parent / "results" / "ridge_rolling_48m"
COV_CACHE_DIR = cfg.DATA_PROC / "cov_cache"
INDEX_MEMBER_PATH = cfg.DATA_PROC / "index_member.parquet"

# v1.2 expanding baseline（直接加载，不重新跑）
V12_EXPANDING_SIGNAL  = Path(__file__).parent / "results" / "ridge_composite_panel.parquet"
V12_EXPANDING_METRICS = Path(__file__).parent / "results" / "ridge_lam_0050" / "backtest_metrics_valid.parquet"
V12_EXPANDING_TO      = Path(__file__).parent / "results" / "ridge_lam_0050" / "turnover_summary.parquet"


# ---------------------------------------------------------------------------
# 协方差缓存
# ---------------------------------------------------------------------------

def _load_cov_cache(dates: list[pd.Timestamp], codes_map: dict) -> dict:
    cov_cache: dict = {}
    n_loaded = n_miss = 0
    for T in dates:
        codes = list(codes_map[T])
        n = len(codes)
        path = COV_CACHE_DIR / f"{T.strftime('%Y%m%d')}.parquet"
        if path.exists():
            try:
                cov_df = pd.read_parquet(path).reindex(index=codes, columns=codes)
                if not cov_df.isna().any().any():
                    arr, _, _, _ = validate_and_repair_covariance(cov_df.values.astype(float))
                    cov_cache[T] = arr
                    n_loaded += 1
                    continue
            except Exception:
                pass
        cov_cache[T] = np.eye(n) * 1e-4
        n_miss += 1
    log.info("协方差缓存: 命中=%d  miss=%d", n_loaded, n_miss)
    return cov_cache


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. 数据加载 ─────────────────────────────────────────────────────────
    log.info("=== 加载数据（18因子）===")
    factor_names  = load_final_factors()
    log.info("因子数: %d  列表: %s", len(factor_names), factor_names)

    factor_panels = load_factor_panels(factor_names)
    fwd_ret_panel = load_fwd_ret()
    all_dates     = fwd_ret_panel.index

    index_member     = pd.read_parquet(INDEX_MEMBER_PATH)
    bench_ret_series = compute_bench_ret_series(fwd_ret_panel, index_member)

    # ── 2. 构建 rolling-48m 信号 ───────────────────────────────────────────
    log.info("=== 构建 rolling-48m 信号 ===")
    combiner = RidgeRollingCombiner(
        factor_names,
        window_months=WINDOW_MONTHS,
        alpha_candidates=ALPHA_CANDS,
        use_excess_return=True,
    )
    selected_alpha = combiner.select_alpha_walk_forward(
        factor_panels, fwd_ret_panel, all_dates,
        bench_ret_series=bench_ret_series,
    )
    log.info("rolling-48m 选定 alpha=%.1f", selected_alpha)

    signal_panel = combiner.build_ridge_panel(
        factor_panels, fwd_ret_panel, all_dates,
        bench_ret_series=bench_ret_series,
    )
    log.info("信号面板: %s  %s ~ %s",
             signal_panel.shape,
             signal_panel.index[0].date(),
             signal_panel.index[-1].date())

    signal_panel.to_parquet(RESULTS_DIR / "composite_panel.parquet")
    if combiner.coef_history_ is not None and not combiner.coef_history_.empty:
        combiner.coef_history_.to_parquet(RESULTS_DIR / "coef_history.parquet")
    combiner.cv_results_.to_csv(RESULTS_DIR / "cv_results.csv", index=False)

    # ── 3. IC 统计 ─────────────────────────────────────────────────────────
    log.info("=== 计算 IC 统计 ===")
    ic_series = compute_ic_series(signal_panel, fwd_ret_panel)
    ic_train, ic_valid = split_ic_by_period(ic_series)
    stats_train = compute_ic_stats(ic_train, "train")
    stats_valid = compute_ic_stats(ic_valid, "valid")

    log.info("rolling-48m [train] IC=%.4f  IC_IR=%.4f  t=%.2f",
             stats_train["ic_mean"], stats_train["ic_ir"], stats_train["t_stat"])
    log.info("rolling-48m [valid] IC=%.4f  IC_IR=%.4f  t=%.2f",
             stats_valid["ic_mean"], stats_valid["ic_ir"], stats_valid["t_stat"])

    # ── 4. 调仓日预处理 ────────────────────────────────────────────────────
    log.info("=== 调仓日预处理 ===")
    industry_raw   = pd.read_parquet(cfg.DATA_PROC / "industry.parquet")
    industry_pivot = (
        industry_raw["industry_code"]
        .unstack(level="ts_code")
        .sort_index()
        .ffill()
    )

    available_dates = sorted(
        d for d in index_member.index.get_level_values("rebalance_date").unique()
        if d <= cfg.VALID_END
    )
    bw_dict = halt_dict = limit_up_dict = limit_dn_dict = {}
    industry_dict: dict = {}
    codes_map: dict = {}
    bw_dict = {}; halt_dict = {}; limit_up_dict = {}; limit_dn_dict = {}

    for T in available_dates:
        snap = index_member.loc[T]
        w_b  = snap["index_weight"] / 100.0
        w_b  = w_b / w_b.sum()
        bw_dict[T]       = w_b
        halt_dict[T]     = set(snap.index[snap["is_suspended"]])
        limit_up_dict[T] = set(snap.index[snap["is_limit_up_locked"]])
        limit_dn_dict[T] = set(snap.index[snap["is_limit_down_locked"]])
        codes_map[T]     = list(w_b.index)
        ind_dates = industry_pivot.index[industry_pivot.index <= T]
        industry_dict[T] = (
            industry_pivot.loc[ind_dates[-1]].dropna()
            if len(ind_dates) > 0 else pd.Series(dtype=object)
        )

    cov_cache = _load_cov_cache(available_dates, codes_map)

    # ── 5. 优化器 ──────────────────────────────────────────────────────────
    log.info("=== 优化器（λ=%.3f）===", LAM)
    opt_cfg = OptimizeConfig(
        te_target_annual = TE_FIXED,
        industry_max_dev = cfg.OPT_INDUSTRY_MAX_DEV,
        single_max_dev   = cfg.OPT_SINGLE_MAX_DEV,
        topn             = cfg.OPT_TOPN,
        turnover_lambda  = LAM,
        max_solve_seconds= 30.0,
    )

    tv_signal   = signal_panel.loc[signal_panel.index <= cfg.VALID_END]
    opt_weights: dict = {}
    w_prev: pd.Series | None = None
    t0 = time.perf_counter()

    for i, T in enumerate(available_dates):
        codes = codes_map[T]
        alpha = (
            tv_signal.loc[T].reindex(codes).fillna(0.0)
            if T in tv_signal.index
            else pd.Series(0.0, index=codes)
        )
        cov            = cov_cache.get(T, np.eye(len(codes)) * 1e-4)
        w_prev_aligned = w_prev.reindex(codes) if w_prev is not None else None

        result = optimize_single_period(
            alpha=alpha, w_b=bw_dict[T], cov=cov,
            industry_map=industry_dict[T], w_prev=w_prev_aligned,
            halt_codes=halt_dict[T], limit_up_codes=limit_up_dict[T],
            limit_dn_codes=limit_dn_dict[T], config=opt_cfg,
        )
        opt_weights[T] = result.weights
        w_prev = result.weights

        if (i + 1) % 24 == 0 or (i + 1) == len(available_dates):
            log.info("  优化进度 %d/%d  %.1fs", i + 1, len(available_dates),
                     time.perf_counter() - t0)

    weights_panel = pd.DataFrame(opt_weights).T.fillna(0.0)
    weights_panel.index.name = "rebalance_date"
    weights_panel.columns = weights_panel.columns.astype(str)
    weights_panel.to_parquet(RESULTS_DIR / "weights_optimized.parquet")

    # ── 6. 回测 ────────────────────────────────────────────────────────────
    log.info("=== 回测 ===")
    bt_cfg = BacktestConfig(initial_value=1.0)
    result_train = run_backtest(weights_panel, cfg.TRAIN_START, cfg.TRAIN_END, bt_cfg)
    result_valid = run_backtest(weights_panel, cfg.VALID_START, cfg.VALID_END, bt_cfg)

    pd.Series(result_train.metrics, name="value").to_frame().to_parquet(
        RESULTS_DIR / "backtest_metrics_train.parquet"
    )
    pd.Series(result_valid.metrics, name="value").to_frame().to_parquet(
        RESULTS_DIR / "backtest_metrics_valid.parquet"
    )
    nav_valid = pd.DataFrame({
        "strategy":  result_valid.nav,
        "benchmark": result_valid.benchmark_nav,
    }).dropna()
    nav_valid.index.name = "trade_date"
    nav_valid.to_parquet(RESULTS_DIR / "backtest_nav_valid.parquet")

    # 换手率
    def _annual_to(trade_log, n_months):
        if trade_log.empty or n_months == 0:
            return float("nan")
        return (
            (trade_log["buy_value"] + trade_log["sell_value"])
            / trade_log["portfolio_value_before"]
        ).sum() / n_months * 12 * 100

    to_train = _annual_to(result_train.trade_log, len(result_train.trade_log))
    to_valid = _annual_to(result_valid.trade_log, len(result_valid.trade_log))

    # ── 7. 对比 v1.2 expanding 基准 ───────────────────────────────────────
    log.info("=== 对比 v1.2 expanding ===")
    exp_ir = float("nan")
    if V12_EXPANDING_METRICS.exists():
        exp_metrics = pd.read_parquet(V12_EXPANDING_METRICS)["value"].to_dict()
        exp_ir = exp_metrics.get("information_ratio", float("nan"))

    m = result_valid.metrics
    ir48 = m["information_ratio"]

    log.info("=" * 55)
    log.info("验证期结果对比（2021-2022）")
    log.info("%-25s  %-12s  %-12s", "指标", "expanding(v1.2)", "rolling-48m(v1.2)")
    log.info("%-25s  %-12.3f  %-12.3f", "IR",            exp_ir, ir48)
    log.info("%-25s  %-12.2f%%  %-12.2f%%", "年化超额",
             exp_metrics.get("excess_return", float("nan")) * 100 if V12_EXPANDING_METRICS.exists() else float("nan"),
             m["excess_return"] * 100)
    log.info("%-25s  %-12.2f%%  %-12.2f%%", "超额最大回撤",
             abs(exp_metrics.get("excess_max_drawdown", float("nan"))) * 100 if V12_EXPANDING_METRICS.exists() else float("nan"),
             abs(m["excess_max_drawdown"]) * 100)
    log.info("%-25s  %-12.1f%%  %-12.1f%%", "月胜率",
             exp_metrics.get("monthly_win_rate", float("nan")) * 100 if V12_EXPANDING_METRICS.exists() else float("nan"),
             m["monthly_win_rate"] * 100)
    log.info("%-25s  %-12.0f%%  %-12.0f%%", "年化换手(验证期)", float("nan"), to_valid)
    log.info("=" * 55)

    if not np.isnan(ir48) and not np.isnan(exp_ir):
        if ir48 > exp_ir:
            log.info("✅ rolling-48m IR(%.3f) > expanding IR(%.3f)，提升 +%.3f",
                     ir48, exp_ir, ir48 - exp_ir)
        else:
            log.info("❌ rolling-48m IR(%.3f) < expanding IR(%.3f)，下降 %.3f",
                     ir48, exp_ir, ir48 - exp_ir)

    log.info("结果目录: %s", RESULTS_DIR)


if __name__ == "__main__":
    main()
