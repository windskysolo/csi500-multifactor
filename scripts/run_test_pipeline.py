"""
scripts/run_test_pipeline.py
测试集完整评估流程 (2023-01-01 ~ 2025-12-31)

步骤：
  1. 扩展 fwd_ret_panel（2022-12-30 ~ 2025-11-28，35 个新期）→ 写入 test_run_dir/
  2. 重建合成信号 → 写入 test_run_dir/signal/
  3. 补充协方差矩阵缓存（仅 2023-2025 新期，写入 test_run_dir/cov_cache/）
  4. 优化权重 → 写入 test_run_dir/portfolio/
  5. 测试集回测 → 写入 test_run_dir/backtest/
  6. 打印测试集指标，写入 run_config.json / reports/self_check.md / RUN_FINISHED.json

框架集成（Phase 10）：
  - 自动从 registry/mainline.json 读取主基线 run_id，写入运行路径名与 run_config.json
  - 使用主基线 optimizer 参数（te_target / turnover_lambda / topn）
  - 产物目录：runs/test/test_run_{N}__<mainline_run_id>/（标准产物结构）
  - 产物格式兼容 compare_runs.py

安全机制（F9-003/F9-004）：
  - 所有测试集产物写入独立目录 runs/test/test_run_{N}__<mainline_run_id>/
  - fwd_ret_panel 扩展结果写入 test_run_dir，不覆盖公共 fwd_ret_panel.parquet
  - 测试期新增协方差缓存写入 test_run_dir/cov_cache/，不写公共 cov_cache/
  - 本地锁文件阻止同一 run-id 在 ledger 记录前重复运行
  - 传入 --resume-from-lock 可在审计过的中断后重试，已有产物自动跳过

运行方式（必须显式指定本次是第几次运行，最多允许 2 次）：
  python -m scripts.run_test_pipeline --run-id 1
"""

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from src.data.universe import get_investable_universe
from src.evaluation.ic_analysis import compute_ic_series, compute_forward_returns
from src.signal.combiner import (
    build_composite_panel,
    validate_directions_vs_summary,
    DEFAULT_WINDOW_MONTHS,
)
from src.portfolio.covariance import estimate_covariance_lw, validate_and_repair_covariance
from src.portfolio.optimizer import OptimizeConfig, optimize_single_period
from src.backtest.engine import BacktestConfig, run_backtest
from src.backtest.metrics import summarize
from src.pipeline.artifacts import file_sha256
from src.pipeline.registry import load_mainline
from src.pipeline.stages import write_self_check_md
from scripts.test_set_ledger import (
    LEDGER_PATH,
    MAX_TEST_SET_RUNS,
    count_test_set_runs,
    has_test_set_run,
    record_test_set_run,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
PANEL_DIR            = cfg.DATA_PROC / "factor_panels"
PUBLIC_FWD_CACHE     = cfg.DATA_PROC / "fwd_ret_panel.parquet"
PUBLIC_COV_CACHE_DIR = cfg.DATA_PROC / "cov_cache"

_EVAL_DIR           = _ROOT / "reports" / "factor_evaluation"
_INVALIDATED_PATH   = _EVAL_DIR / "INVALIDATED.md"
_EVAL_JSON_PATH     = _EVAL_DIR / "final_factors.json"
_FACTOR_SUMMARY_CSV = _EVAL_DIR / "factor_summary.csv"

MIN_VALID_COUNT = cfg.SIGNAL_MIN_VALID_FACTORS


# ---------------------------------------------------------------------------
# 框架集成：读取主基线 context
# ---------------------------------------------------------------------------

def _load_mainline_context() -> tuple[str, dict, dict]:
    """
    从 registry/mainline.json 读取主基线信息。

    Returns:
        (mainline_run_id, optimizer_spec_dict, signal_spec_dict)
        optimizer_spec_dict 键：te_target_annual, industry_max_dev, single_max_dev,
                                turnover_lambda, topn
        signal_spec_dict 键：method, training_mode, purge_months, window_months, alpha_grid, …
    """
    try:
        mainline = load_mainline()
        mainline_run_id = mainline["active_run_id"]
        log.info("主基线 run_id: %s", mainline_run_id)
    except FileNotFoundError:
        log.warning("registry/mainline.json 不存在，使用 cfg 默认优化器参数")
        mainline_run_id = "no_mainline"
        return mainline_run_id, {}, {}

    # 尝试读取主基线 run_config.json 获取 optimizer + signal spec
    mainline_run_config_path = (
        _ROOT / "runs" / "train_valid" / mainline_run_id / "run_config.json"
    )
    if not mainline_run_config_path.exists():
        log.warning(
            "主基线 run_config.json 不存在 (%s)，使用 cfg 默认参数",
            mainline_run_config_path,
        )
        return mainline_run_id, {}, {}

    run_config = json.loads(mainline_run_config_path.read_text(encoding="utf-8"))
    spec_dict  = run_config.get("spec", {})
    opt_spec    = spec_dict.get("optimizer", {})
    signal_spec = spec_dict.get("signal", {})
    log.info(
        "使用主基线优化器参数: te=%.0f%%  lambda=%.4f  topn=%d",
        opt_spec.get("te_target_annual", cfg.OPT_TE_TARGET_ANNUAL) * 100,
        opt_spec.get("turnover_lambda", cfg.OPT_TURNOVER_LAMBDA),
        opt_spec.get("topn", cfg.OPT_TOPN),
    )
    log.info(
        "使用主基线信号方法: method=%s  training_mode=%s",
        signal_spec.get("method", "icir"), signal_spec.get("training_mode", "expanding"),
    )
    return mainline_run_id, opt_spec, signal_spec


def _build_opt_config(opt_spec: dict) -> OptimizeConfig:
    """从 optimizer spec dict 构建 OptimizeConfig（缺失值回退到 cfg 默认）。"""
    return OptimizeConfig(
        te_target_annual  = opt_spec.get("te_target_annual",  cfg.OPT_TE_TARGET_ANNUAL),
        industry_max_dev  = opt_spec.get("industry_max_dev",  cfg.OPT_INDUSTRY_MAX_DEV),
        single_max_dev    = opt_spec.get("single_max_dev",    cfg.OPT_SINGLE_MAX_DEV),
        turnover_lambda   = opt_spec.get("turnover_lambda",   cfg.OPT_TURNOVER_LAMBDA),
        topn              = opt_spec.get("topn",              cfg.OPT_TOPN),
        max_solve_seconds = 30.0,
    )


def _write_run_config(
    run_dir: Path,
    run_id: str,
    run_number: int,
    mainline_run_id: str,
    opt_spec: dict,
    signal_spec: dict,
) -> None:
    """写入 run_config.json（供 compare_runs.py 读取）。"""
    _sig: dict = {
        "method":        signal_spec.get("method",        "icir"),
        "target":        signal_spec.get("target",        "excess_return"),
        "training_mode": signal_spec.get("training_mode", "expanding"),
        "purge_months":  signal_spec.get("purge_months",  2),
        "window_months": signal_spec.get("window_months"),
    }
    if signal_spec.get("method") == "ridge":
        _sig["alpha_grid"]            = signal_spec.get("alpha_grid", [0.1, 1.0, 10.0, 100.0, 500.0])
        _sig["selected_alpha_policy"] = signal_spec.get("selected_alpha_policy", "cv_train_only")

    config = {
        "run_id":           run_id,
        "experiment_id":    run_id,
        "scope":            "test",
        "mainline_run_id":  mainline_run_id,
        "started_at":       datetime.now(timezone.utc).isoformat(),
        "spec": {
            "experiment_id":  run_id,
            "description":    f"测试集评估 test_run_{run_number}（主基线={mainline_run_id}）",
            "period_scope":   f"test_run_{run_number}",
            "allow_test_set": True,
            "signal":         _sig,
            "optimizer": {
                "te_target_annual":  opt_spec.get("te_target_annual",  cfg.OPT_TE_TARGET_ANNUAL),
                "industry_max_dev":  opt_spec.get("industry_max_dev",  cfg.OPT_INDUSTRY_MAX_DEV),
                "single_max_dev":    opt_spec.get("single_max_dev",    cfg.OPT_SINGLE_MAX_DEV),
                "turnover_lambda":   opt_spec.get("turnover_lambda",   cfg.OPT_TURNOVER_LAMBDA),
                "topn":              opt_spec.get("topn",              cfg.OPT_TOPN),
            },
            "backtest": {
                "execution":  "tplus1_open",
                "cost_model": "china_a_share_v1",
                "benchmark":  "CSI500_TOTAL_RETURN",
            },
        },
    }
    (run_dir / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Step 1: 扩展 fwd_ret_panel（F9-003: 结果写入测试专用目录，不污染公共路径）
# ---------------------------------------------------------------------------
def _extend_fwd_ret_panel(
    all_dates: list[pd.Timestamp],
    output_dir: Path,
) -> pd.DataFrame:
    """
    增量扩展 fwd_ret_panel 到 all_dates 覆盖的范围。

    F9-003: 从公共路径读取训练/验证期数据，将扩展后的完整面板写入
    output_dir/fwd_ret_panel.parquet（测试专用），不修改公共路径。

    Args:
        all_dates:  全部调仓日（训练/验证 + 测试期）
        output_dir: 测试运行专用目录
    Returns:
        扩展后的完整 fwd_ret_panel（含训练/验证 + 测试期）
    """
    existing = pd.read_parquet(PUBLIC_FWD_CACHE)
    log.info(
        "公共 fwd_ret_panel: %s  %s ~ %s",
        existing.shape, existing.index[0].date(), existing.index[-1].date(),
    )

    existing_dates = set(existing.index)
    new_t_dates = [d for d in all_dates[:-1] if d not in existing_dates]

    if not new_t_dates:
        log.info("fwd_ret_panel 已覆盖所有调仓日，跳过计算新期")
        test_fwd_path = output_dir / "fwd_ret_panel.parquet"
        if not test_fwd_path.exists():
            existing.to_parquet(test_fwd_path)
            log.info("fwd_ret_panel 已写入测试专用路径（无新期）: %s", test_fwd_path.name)
        return existing

    log.info(
        "需要新计算 %d 个调仓日的 forward return (%s ~ %s)",
        len(new_t_dates), new_t_dates[0].date(), new_t_dates[-1].date(),
    )

    computation_dates = new_t_dates + [all_dates[-1]]

    codes_by_date: dict[pd.Timestamp, list[str]] = {}
    for T in new_t_dates:
        try:
            codes_by_date[T] = get_investable_universe(T).tolist()
        except Exception as e:
            log.warning("%s get_investable_universe 失败: %s，使用空列表", T.date(), e)
            codes_by_date[T] = []

    all_new_codes = sorted(set().union(*codes_by_date.values())) if codes_by_date else []
    if not all_new_codes:
        log.warning("新调仓日无有效股票代码，跳过 fwd_ret 扩展")
        return existing

    t0 = time.perf_counter()
    new_fwd = compute_forward_returns(
        rebalance_dates = computation_dates,
        codes           = all_new_codes,
        codes_by_date   = codes_by_date,
    )
    elapsed = time.perf_counter() - t0

    new_fwd = new_fwd.reindex(new_t_dates)
    log.info("新计算 %d 期 forward return，耗时 %.1fs", len(new_fwd), elapsed)

    merged = pd.concat([existing, new_fwd]).sort_index()
    merged = merged[~merged.index.duplicated(keep="last")]

    test_fwd_path = output_dir / "fwd_ret_panel.parquet"
    merged.to_parquet(test_fwd_path)
    log.info(
        "扩展后 fwd_ret_panel 已写入测试专用路径: %s  %s  %s ~ %s",
        test_fwd_path.name, merged.shape,
        merged.index[0].date(), merged.index[-1].date(),
    )
    log.info("公共 fwd_ret_panel.parquet 未被修改（F9-003 隔离）")
    return merged


# ---------------------------------------------------------------------------
# Step 2a: Ridge 信号路径（method=ridge 时使用）
# ---------------------------------------------------------------------------
def _build_composite_signals_ridge(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    all_dates: list[pd.Timestamp],
    output_dir: Path,
    run_id: str,
    signal_spec: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    使用 RidgeCombiner 构建测试集合成信号。

    alpha 选择只在训练/验证期（≤ VALID_END）进行；
    信号生成在全部日期（TV + 测试期）进行，expanding 窗口不引入未来信息。

    Returns:
        (composite_equal, composite_ridge)
    """
    from src import config as cfg
    from experiments.legacy.ridge_signal.ridge_combiner import RidgeCombiner
    from experiments.legacy.ridge_rolling.rolling_combiner import RidgeRollingCombiner
    from src.evaluation.ic_analysis import compute_ic_series
    from src.signal.combiner import build_composite_panel, DEFAULT_WINDOW_MONTHS

    signal_dir = output_dir / "signal"
    signal_dir.mkdir(parents=True, exist_ok=True)

    factor_names  = sorted(factor_panels.keys())
    mode          = signal_spec.get("training_mode", "expanding")
    alpha_grid    = signal_spec.get("alpha_grid",    [0.1, 1.0, 10.0, 100.0, 500.0])
    purge_months  = signal_spec.get("purge_months",  2)
    window_months = signal_spec.get("window_months")

    valid_end = pd.Timestamp(cfg.VALID_END)
    tv_dates  = pd.DatetimeIndex([d for d in all_dates if d <= valid_end])
    tv_panels = {k: v.loc[v.index <= valid_end] for k, v in factor_panels.items()}
    tv_fwd    = fwd_ret_panel.loc[fwd_ret_panel.index <= valid_end]

    if mode == "rolling":
        if not window_months:
            raise ValueError("rolling 模式需要 signal_spec.window_months")
        combiner = RidgeRollingCombiner(
            factor_names     = factor_names,
            window_months    = window_months,
            alpha_candidates = alpha_grid,
            purge_months     = purge_months,
        )
    else:
        combiner = RidgeCombiner(
            factor_names     = factor_names,
            alpha_candidates = alpha_grid,
            purge_months     = purge_months,
        )

    log.info("Ridge alpha 选择（TV 数据 %d 期）...", len(tv_dates))
    combiner.select_alpha_walk_forward(tv_panels, tv_fwd, tv_dates)
    log.info("已选 alpha=%.1f，构建 Ridge 合成信号（全部 %d 期）...",
             combiner.alpha_ or -1, len(all_dates))

    composite_ridge = combiner.build_ridge_panel(
        factor_panels, fwd_ret_panel, pd.DatetimeIndex(all_dates)
    )
    composite_ridge.columns = composite_ridge.columns.astype(str)
    composite_ridge.to_parquet(signal_dir / "composite.parquet")

    coef_hist = getattr(combiner, "coef_history_", None)
    if coef_hist is not None and not coef_hist.empty:
        coef_hist.to_parquet(signal_dir / "coef_history.parquet")

    ic_series_map = {
        name: compute_ic_series(panel, fwd_ret_panel)
        for name, panel in factor_panels.items()
    }
    composite_equal = build_composite_panel(
        factor_panels     = factor_panels,
        ic_series_map     = ic_series_map,
        rebalance_dates   = all_dates,
        method            = "equal",
        window_months     = DEFAULT_WINDOW_MONTHS,
        min_valid_factors = MIN_VALID_COUNT,
    )
    composite_equal.columns = composite_equal.columns.astype(str)
    composite_equal.to_parquet(signal_dir / "composite_equal.parquet")

    ic_df = pd.DataFrame(ic_series_map)
    ic_df.index.name = "rebalance_date"
    ic_df.to_parquet(signal_dir / "ic_detail.parquet")

    metadata = {
        "run_id":            run_id,
        "generated_at":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method":            "ridge",
        "training_mode":     mode,
        "window_months":     window_months,
        "selected_alpha":    combiner.alpha_,
        "purge_months":      purge_months,
        "n_factors":         len(factor_panels),
        "n_rebalance_dates": len(all_dates),
        "date_range":        [str(all_dates[0].date()), str(all_dates[-1].date())],
    }
    (signal_dir / "signal_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log.info(
        "Ridge 合成信号已保存: ridge=%s  equal=%s  alpha=%.1f",
        composite_ridge.shape, composite_equal.shape, combiner.alpha_ or -1,
    )
    return composite_equal, composite_ridge


# ---------------------------------------------------------------------------
# Step 2b: IC_IR 信号路径（method=icir 时使用）+ 入口分发
# ---------------------------------------------------------------------------
def _build_composite_signals(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    all_dates: list[pd.Timestamp],
    stability_weights: dict | None,
    output_dir: Path,
    run_id: str,
    signal_spec: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    计算并保存合成信号到 output_dir/signal/（标准产物子目录）。

    不得覆盖训练/验证公共路径。
    output_dir/signal/ 下落盘：
      composite.parquet           — 主信号（IC_IR 加权或 Ridge，标准命名）
      composite_equal.parquet     — 等权合成信号（参考）
      ic_detail.parquet           — 各因子 IC 序列
      weight_history.parquet      — 逐期因子权重历史（IC_IR）/ coef_history.parquet（Ridge）
      signal_metadata.json        — 元数据
    """
    _spec = signal_spec or {}
    if _spec.get("method") == "ridge":
        return _build_composite_signals_ridge(
            factor_panels, fwd_ret_panel, all_dates, output_dir, run_id, _spec
        )

    signal_dir = output_dir / "signal"
    signal_dir.mkdir(parents=True, exist_ok=True)

    log.info("计算各因子 IC 序列（%d 个因子，%d 期）...", len(factor_panels), len(all_dates))
    ic_series_map = {
        name: compute_ic_series(panel, fwd_ret_panel)
        for name, panel in factor_panels.items()
    }

    log.info("构建等权合成信号...")
    composite_equal = build_composite_panel(
        factor_panels     = factor_panels,
        ic_series_map     = ic_series_map,
        rebalance_dates   = all_dates,
        method            = "equal",
        window_months     = DEFAULT_WINDOW_MONTHS,
        min_valid_factors = MIN_VALID_COUNT,
    )

    log.info("构建 IC_IR 加权合成信号...")
    composite_ic_ir, diagnostics = build_composite_panel(
        factor_panels     = factor_panels,
        ic_series_map     = ic_series_map,
        rebalance_dates   = all_dates,
        method            = "ic_ir",
        window_months     = DEFAULT_WINDOW_MONTHS,
        min_valid_factors = MIN_VALID_COUNT,
        stability_weights = stability_weights,
        return_diagnostics = True,
    )

    composite_equal.columns = composite_equal.columns.astype(str)
    composite_ic_ir.columns = composite_ic_ir.columns.astype(str)

    composite_ic_ir.to_parquet(signal_dir / "composite.parquet")
    composite_equal.to_parquet(signal_dir / "composite_equal.parquet")

    ic_series_df = pd.DataFrame(ic_series_map)
    ic_series_df.index.name = "rebalance_date"
    ic_series_df.to_parquet(signal_dir / "ic_detail.parquet")

    weight_history = diagnostics["weight_history"]
    if weight_history:
        wh_df = pd.DataFrame(weight_history).T.rename_axis("rebalance_date")
        wh_df.index = pd.DatetimeIndex(wh_df.index)
        wh_df.to_parquet(signal_dir / "weight_history.parquet")
        log.info("权重历史已保存: %s", wh_df.shape)

    ic_series_bytes = ic_series_df.to_csv(index=False).encode()
    signal_bytes    = composite_ic_ir.to_csv(index=False).encode()

    metadata = {
        "run_id":               run_id,
        "generated_at":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method":               "ic_ir",
        "window_months":        DEFAULT_WINDOW_MONTHS,
        "min_valid_factors":    MIN_VALID_COUNT,
        "factor_list":          sorted(factor_panels.keys()),
        "n_factors":            len(factor_panels),
        "n_rebalance_dates":    len(all_dates),
        "date_range":           [str(all_dates[0].date()), str(all_dates[-1].date())],
        "cold_start_count":     diagnostics["cold_start_count"],
        "ic_series_hash":       hashlib.md5(ic_series_bytes).hexdigest()[:12],
        "signal_ic_ir_hash":    hashlib.md5(signal_bytes).hexdigest()[:12],
    }
    (signal_dir / "signal_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log.info(
        "合成信号已保存到 signal/: equal=%s  ic_ir=%s  ic_series=%s  冷启动=%d 期",
        composite_equal.shape, composite_ic_ir.shape,
        ic_series_df.shape, diagnostics["cold_start_count"],
    )
    return composite_equal, composite_ic_ir


# ---------------------------------------------------------------------------
# Step 3-4: 协方差估计 + 组合优化（全期，写入 portfolio/ 子目录）
# ---------------------------------------------------------------------------
def _run_optimization(
    composite_signal: pd.DataFrame,
    all_rebalance_dates: list[pd.Timestamp],
    output_dir: Path,
    opt_config: OptimizeConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    对 all_rebalance_dates 中所有期运行 QP 优化，利用已有协方差缓存。

    F6-005: 权重产物写入 output_dir/portfolio/（标准产物子目录）。
    F9-003: 新估计的协方差写入 output_dir/cov_cache/，不污染公共 cov_cache/。
    F6-003: 协方差缓存读取时验证对称性和正定性，必要时修复。
    F6-004: metadata 记录 w_prev_source='target_weight'，明确近似假设。
    """
    portfolio_dir = output_dir / "portfolio"
    portfolio_dir.mkdir(parents=True, exist_ok=True)

    test_cov_cache_dir = output_dir / "cov_cache"
    test_cov_cache_dir.mkdir(parents=True, exist_ok=True)

    log.info("加载 index_member 及行业数据...")
    index_member_raw = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    industry_raw     = pd.read_parquet(cfg.DATA_PROC / "industry.parquet")

    industry_pivot = (
        industry_raw["industry_code"]
        .unstack(level="ts_code")
        .sort_index()
        .ffill()
    )

    benchmark_weights_dict: dict = {}
    halt_dict:     dict = {}
    limit_up_dict: dict = {}
    limit_dn_dict: dict = {}
    industry_dict: dict = {}

    dates_in_member = index_member_raw.index.get_level_values("rebalance_date").unique()
    available_dates = [d for d in all_rebalance_dates if d in dates_in_member]
    if len(available_dates) < len(all_rebalance_dates):
        missing = set(all_rebalance_dates) - set(available_dates)
        log.warning("index_member 缺少 %d 个调仓日: %s", len(missing), sorted(missing)[:5])

    for T in available_dates:
        snap = index_member_raw.loc[T]
        w_b  = snap["index_weight"] / 100.0
        w_b  = w_b / w_b.sum()
        benchmark_weights_dict[T] = w_b
        halt_dict[T]     = set(snap.index[snap["is_suspended"]])
        limit_up_dict[T] = set(snap.index[snap["is_limit_up_locked"]])
        limit_dn_dict[T] = set(snap.index[snap["is_limit_down_locked"]])
        ind_dates = industry_pivot.index[industry_pivot.index <= T]
        if len(ind_dates) > 0:
            industry_dict[T] = industry_pivot.loc[ind_dates[-1]].dropna()
        else:
            industry_dict[T] = pd.Series(dtype=object)

    # 协方差缓存（读取优先级：公共缓存 → 测试专用缓存 → 新估计）
    cov_cache: dict = {}
    n_loaded = n_estimated = n_repaired = n_failed = 0
    t_cov = time.perf_counter()
    for T in available_dates:
        codes        = list(benchmark_weights_dict[T].index)
        public_cache = PUBLIC_COV_CACHE_DIR / f"{T.strftime('%Y%m%d')}.parquet"
        test_cache   = test_cov_cache_dir / f"{T.strftime('%Y%m%d')}.parquet"

        cache_path = public_cache if public_cache.exists() else (
            test_cache if test_cache.exists() else None
        )
        if cache_path is not None:
            try:
                cov_df = pd.read_parquet(cache_path).reindex(index=codes, columns=codes)
                if not cov_df.isna().any().any():
                    cov_arr = cov_df.values.astype(float)
                    cov_arr, was_valid, min_eig, _ = validate_and_repair_covariance(cov_arr)
                    if not was_valid:
                        log.warning(
                            "%s 缓存协方差不满足正定性 (min_eig=%.2e)，已修复", T.date(), min_eig,
                        )
                        n_repaired += 1
                    cov_cache[T] = cov_arr
                    n_loaded += 1
                    continue
            except Exception as e:
                log.warning("%s 缓存读取失败: %s，重新估计", T.date(), e)
        try:
            cov = estimate_covariance_lw(codes, T)
            cov_cache[T] = cov
            pd.DataFrame(cov, index=codes, columns=codes).to_parquet(test_cache)
            n_estimated += 1
        except Exception as e:
            log.warning("%s 协方差估计失败: %s", T.date(), e)
            n_failed += 1

    log.info(
        "协方差: 读缓存=%d（修复=%d）  新估计=%d（→cov_cache/）  失败=%d  耗时=%.1fs",
        n_loaded, n_repaired, n_estimated, n_failed, time.perf_counter() - t_cov,
    )

    baseline_n  = opt_config.topn
    opt_weights:    dict = {}
    baseline_weights: dict = {}
    meta_rows: list[dict] = []
    w_prev: pd.Series | None = None
    fb_counts: dict = {}

    t_opt = time.perf_counter()
    for i, T in enumerate(available_dates):
        codes = list(benchmark_weights_dict[T].index)
        n     = len(codes)

        alpha = composite_signal.loc[T].reindex(codes).fillna(0.0) \
            if T in composite_signal.index else pd.Series(0.0, index=codes)

        cov_available = T in cov_cache
        cov = cov_cache[T] if cov_available else np.eye(n) * 1e-4
        if not cov_available:
            log.warning("%s 无协方差缓存，降级为 L2 逻辑（F6-002）", T.date())

        w_prev_aligned = w_prev.reindex(codes) if w_prev is not None else None

        result = optimize_single_period(
            alpha          = alpha,
            w_b            = benchmark_weights_dict[T],
            cov            = cov,
            industry_map   = industry_dict[T],
            w_prev         = w_prev_aligned,
            halt_codes     = halt_dict[T],
            limit_up_codes = limit_up_dict[T],
            limit_dn_codes = limit_dn_dict[T],
            config         = opt_config,
        )

        effective_fb     = result.fallback_level
        effective_status = result.solver_status
        if not cov_available and result.fallback_level == 0:
            effective_fb     = 1
            effective_status = result.solver_status + "+cov_missing"

        opt_weights[T] = result.weights
        w_prev = result.weights
        fb_counts[effective_fb] = fb_counts.get(effective_fb, 0) + 1

        meta_rows.append({
            "rebalance_date": T,
            "fallback_level": effective_fb,
            "solver_status":  effective_status,
            "solve_time_s":   result.solve_time_s,
            "cov_available":  cov_available,
            "w_prev_source":  "target_weight",
        })

        avail   = alpha.drop(index=list(halt_dict[T] & set(alpha.index)), errors="ignore")
        top_sel = avail.dropna().nlargest(baseline_n).index
        w_bl    = pd.Series(0.0, index=codes)
        if len(top_sel) > 0:
            w_bl[top_sel] = 1.0 / len(top_sel)
        baseline_weights[T] = w_bl

        if (i + 1) % 10 == 0 or (i + 1) == len(available_dates):
            log.info("优化进度: %d/%d  已用时: %.1fs",
                     i + 1, len(available_dates), time.perf_counter() - t_opt)

    total_elapsed = time.perf_counter() - t_opt
    log.info(
        "优化完成: %d 期  耗时=%.1fs  均值=%.2fs/期  Fallback: L0=%d  L1=%d  L2=%d",
        len(available_dates), total_elapsed, total_elapsed / max(len(available_dates), 1),
        fb_counts.get(0, 0), fb_counts.get(1, 0), fb_counts.get(2, 0),
    )

    weights_panel  = pd.DataFrame(opt_weights).T.fillna(0.0)
    baseline_panel = pd.DataFrame(baseline_weights).T.fillna(0.0)
    weights_panel.index.name  = "rebalance_date"
    baseline_panel.index.name = "rebalance_date"
    weights_panel.columns  = weights_panel.columns.astype(str)
    baseline_panel.columns = baseline_panel.columns.astype(str)

    weights_panel.to_parquet(portfolio_dir / "target_weights.parquet")
    baseline_panel.to_parquet(portfolio_dir / "baseline_weights.parquet")

    meta_df = pd.DataFrame(meta_rows).set_index("rebalance_date")
    meta_df.index = pd.DatetimeIndex(meta_df.index)
    meta_df.to_parquet(portfolio_dir / "optimizer_meta.parquet")

    n_cov_missing = int((~meta_df["cov_available"]).sum())
    log.info(
        "权重已保存到 portfolio/: opt=%s  baseline=%s  meta=%s  cov缺失期=%d",
        weights_panel.shape, baseline_panel.shape, meta_df.shape, n_cov_missing,
    )

    return baseline_panel, weights_panel


# ---------------------------------------------------------------------------
# Step 5: 测试集回测（写入 backtest/ 子目录）
# ---------------------------------------------------------------------------
def _fmt_metrics(metrics: dict) -> str:
    return (
        f"年化超额={metrics['excess_return']*100:.2f}%  "
        f"IR={metrics['information_ratio']:.3f}  "
        f"超额最大回撤={abs(metrics['excess_max_drawdown'])*100:.2f}%  "
        f"TE={metrics['tracking_error']*100:.2f}%  "
        f"月胜率={metrics['monthly_win_rate']*100:.1f}%"
    )


def _annual_turnover_pct(trade_log: pd.DataFrame, n_months: int) -> float:
    if trade_log.empty or n_months == 0:
        return float("nan")
    total_to = (
        (trade_log["buy_value"] + trade_log["sell_value"])
        / trade_log["portfolio_value_before"]
    ).sum()
    return total_to / n_months * 12 * 100


def _run_test_backtest(
    weights_v1: pd.DataFrame,
    weights_v2: pd.DataFrame,
    run_tag: str,
    output_dir: Path,
) -> None:
    """Run backtest for TEST_START ~ TEST_END; save outputs to output_dir/backtest/."""
    backtest_dir = output_dir / "backtest"
    backtest_dir.mkdir(parents=True, exist_ok=True)
    config = BacktestConfig(initial_value=1.0)

    log.info("运行 V1 Baseline 测试集回测...")
    result_v1 = run_backtest(weights_v1, cfg.TEST_START, cfg.TEST_END, config)

    log.info("运行 V2 优化权重测试集回测...")
    result_v2 = run_backtest(weights_v2, cfg.TEST_START, cfg.TEST_END, config)

    n_months = len(result_v2.trade_log) if not result_v2.trade_log.empty else 0

    # 标准产物（用于框架读取）
    nav_df = pd.DataFrame({
        "strategy_v1": result_v1.nav,
        "strategy_v2": result_v2.nav,
        "benchmark":   result_v1.benchmark_nav,
    }).dropna()
    nav_df.index.name = "trade_date"
    nav_df.to_parquet(backtest_dir / "nav_valid.parquet")

    metrics_df = pd.DataFrame({
        "v1": pd.Series(result_v1.metrics),
        "v2": pd.Series(result_v2.metrics),
    })
    metrics_df.index.name = "metric"
    metrics_df.to_parquet(backtest_dir / "metrics_valid.parquet")

    if not result_v2.trade_log.empty:
        result_v2.trade_log.to_parquet(backtest_dir / "trades_valid.parquet")
    if not result_v2.actual_weights.empty:
        result_v2.actual_weights.to_parquet(backtest_dir / "actual_weights_valid.parquet")

    # 额外保存 V1 数据供参考
    if not result_v1.trade_log.empty:
        result_v1.trade_log.to_parquet(backtest_dir / "trades_v1.parquet")
    if not result_v1.actual_weights.empty:
        result_v1.actual_weights.to_parquet(backtest_dir / "actual_weights_v1.parquet")

    print()
    print("=" * 70)
    print(f"{run_tag} 测试集回测结果")
    print(f"回测区间：{result_v1.nav.index[0].date()} → {result_v1.nav.index[-1].date()}")
    print("=" * 70)

    for ver, res in [("V1 Baseline", result_v1), ("V2 优化权重", result_v2)]:
        m      = res.metrics
        to_pct = _annual_turnover_pct(res.trade_log, n_months)
        print(f"\n{ver}:")
        print(f"  年化超额:     {m['excess_return']*100:+.2f}%")
        print(f"  IR:           {m['information_ratio']:.3f}")
        print(f"  超额最大回撤: -{abs(m['excess_max_drawdown'])*100:.2f}%")
        print(f"  跟踪误差:     {m['tracking_error']*100:.2f}%")
        print(f"  月胜率:       {m['monthly_win_rate']*100:.1f}%")
        print(f"  年化双边换手: {to_pct:.0f}%")
        print(f"  绝对收益:     {m['annualized_return']*100:+.2f}%")
        print(f"  基准收益:     {m['benchmark_return']*100:+.2f}%")

    print()
    print(f"测试集产物已落盘到 backtest/：")
    for fname in ["nav_valid.parquet", "metrics_valid.parquet", "trades_valid.parquet"]:
        p = backtest_dir / fname
        if p.exists():
            print(f"  {fname}")

    m2    = result_v2.metrics
    to_v2 = _annual_turnover_pct(result_v2.trade_log, n_months)
    print()
    print("硬指标检查（目标：IR≥0.5，超额最大回撤≤10%，年化换手5-15倍）：")
    _check("IR ≥ 0.5", m2["information_ratio"] >= 0.5, f"{m2['information_ratio']:.3f}")
    _check("超额最大回撤 ≤ 10%", abs(m2["excess_max_drawdown"]) <= 0.10,
           f"{abs(m2['excess_max_drawdown'])*100:.2f}%")
    _check("年化双边换手 500-1500%", 500 <= to_v2 <= 1500, f"{to_v2:.0f}%")


def _check(label: str, passed: bool, value: str) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}: {value}")


# ---------------------------------------------------------------------------
# 测试集标准产物写入
# ---------------------------------------------------------------------------

def _write_run_failed(run_dir: Path, run_id: str, reason: str, git_commit: str) -> None:
    payload = {
        "run_id":     run_id,
        "status":     "failed",
        "reason":     reason,
        "failed_at":  datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
    }
    (run_dir / "RUN_FAILED.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_inputs_lock(
    run_dir: Path,
    test_run_id: str,
    run_number: int,
    mainline_run_id: str,
    panel_dir: Path,
    factor_files: list[Path],
    git_commit: str,
) -> None:
    mainline_cfg_path = _ROOT / "runs" / "train_valid" / mainline_run_id / "run_config.json"
    factor_hashes = {f.name: file_sha256(f) for f in sorted(factor_files)}
    lock = {
        "generated_at":             datetime.now(timezone.utc).isoformat(),
        "test_run_id":              test_run_id,
        "period_scope":             f"test_run_{run_number}",
        "TEST_START":               str(cfg.TEST_START.date()),
        "TEST_END":                 str(cfg.TEST_END.date()),
        "mainline_run_id":          mainline_run_id,
        "mainline_run_config_hash": file_sha256(mainline_cfg_path),
        "factor_panel_dir":         str(panel_dir),
        "factor_files":             factor_hashes,
        "final_factors_exists":     _EVAL_JSON_PATH.exists(),
        "INVALIDATED_md_exists":    _INVALIDATED_PATH.exists(),
        "git_commit":               git_commit,
    }
    (run_dir / "inputs.lock.json").write_text(
        json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("inputs.lock.json 已写入: %d 个因子文件", len(factor_hashes))


def _write_test_set_run_lock(
    run_dir: Path,
    run_id_num: int,
    test_run_id: str,
    mainline_run_id: str,
    status: str,
    started_at: str,
    finished_at: str | None,
    panel_dir: Path,
    git_commit: str,
) -> None:
    lock = {
        "run_id":           run_id_num,
        "test_run_id":      test_run_id,
        "mainline_run_id":  mainline_run_id,
        "status":           status,
        "started_at":       started_at,
        "finished_at":      finished_at,
        "ledger_path":      str(LEDGER_PATH),
        "factor_panel_dir": str(panel_dir),
        "git_commit":       git_commit,
    }
    (run_dir / "TEST_SET_RUN_LOCK.json").write_text(
        json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_manifest(run_dir: Path) -> None:
    entries: dict = {}
    for fname in ["run_config.json", "inputs.lock.json", "TEST_SET_RUN_LOCK.json", "RUN_FINISHED.json"]:
        p = run_dir / fname
        if p.exists():
            entries[fname] = {"sha256": file_sha256(p)}
    for p in sorted(run_dir.rglob("*.parquet")):
        rel = str(p.relative_to(run_dir))
        entries[rel] = {"sha256": file_sha256(p)}
    (run_dir / "manifest.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("manifest.json 已写入: %d 个文件", len(entries))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _git_commit_has_run_tag(run_id: int) -> bool:
    return has_test_set_run(run_id)


def _write_lock_file(lock_path: Path, run_id: int, status: str) -> None:
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        git_commit = "unknown"

    lock_data = {
        "run_id":     run_id,
        "status":     status,
        "git_commit": git_commit,
        "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    lock_path.write_text(json.dumps(lock_data, ensure_ascii=False, indent=2), encoding="utf-8")


def _current_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="测试集评估流程（最多运行 2 次）")
    parser.add_argument(
        "--run-id", type=int, required=True,
        help="本次是第几次测试集运行（1 或 2），必须与 ledger 中已有次数 +1 一致",
    )
    parser.add_argument(
        "--resume-from-lock", action="store_true",
        help="若 RUN_STARTED.json 存在但尚未提交，允许从中断点重试（谨慎使用）",
    )
    parser.add_argument(
        "--factor-panel-dir", default=None, metavar="DIR",
        help=(
            "因子面板目录（覆盖默认 data/processed/factor_panels/）。"
            "测试集运行时应指向 build_factor_panels --allow-test-set 生成的目录"
        ),
    )
    args = parser.parse_args()

    # ── ledger 守卫 ───────────────────────────────────────────────────────────
    MAX_TEST_RUNS = MAX_TEST_SET_RUNS
    completed = count_test_set_runs()
    if completed >= MAX_TEST_RUNS:
        log.error(
            "测试集已运行 %d 次（上限 %d 次），拒绝执行。"
            "如需第三次评估，请先修改 MAX_TEST_RUNS 并记录理由。",
            completed, MAX_TEST_RUNS,
        )
        sys.exit(1)
    if args.run_id != completed + 1:
        log.error(
            "--run-id %d 与 ledger 中已有次数 %d 不符（应为 %d），请确认后重试。",
            args.run_id, completed, completed + 1,
        )
        sys.exit(1)

    # ── 读取主基线 context ────────────────────────────────────────────────────
    mainline_run_id, opt_spec, signal_spec = _load_mainline_context()
    opt_config  = _build_opt_config(opt_spec)
    test_run_id = f"test_run_{args.run_id}__{mainline_run_id}"

    test_run_dir = _ROOT / "runs" / "test" / test_run_id
    test_run_dir.mkdir(parents=True, exist_ok=True)
    log.info("测试 run 目录: %s", test_run_dir)

    lock_started  = test_run_dir / "RUN_STARTED.json"
    lock_finished = test_run_dir / "RUN_FINISHED.json"

    # ── F9-004 守卫：本地锁文件防止同一 run-id 重复运行 ──────────────────────
    if lock_started.exists():
        if _git_commit_has_run_tag(args.run_id):
            log.error(
                "run-id=%d 的 RUN_STARTED 锁文件已存在，且 ledger 已有对应记录。"
                "该次测试集运行已完成，拒绝重复执行。",
                args.run_id,
            )
            sys.exit(1)
        elif not args.resume_from_lock:
            log.error(
                "run-id=%d 的 RUN_STARTED 锁文件已存在（可能是上次中断的运行），"
                "但 ledger 中尚无对应有效记录。\n"
                "若要重试，请显式传入 --resume-from-lock；\n"
                "若要彻底重建，请手动删除 %s 后重试。",
                args.run_id, test_run_dir,
            )
            sys.exit(1)
        else:
            log.warning(
                "检测到未提交的 RUN_STARTED 锁（--resume-from-lock 已传入），"
                "继续本次运行。请确保这是受审计的重试，而非第二次独立测试集评估。",
            )

    # ── F5-001 守卫：评估报告失效检查 ────────────────────────────────────────
    if _INVALIDATED_PATH.exists():
        log.error(
            "评估报告已标记失效（%s 存在）。"
            "请先按 INVALIDATED.md 中的重建步骤修复上游数据后再运行测试流水线。",
            _INVALIDATED_PATH,
        )
        sys.exit(1)

    # ── F5-002 守卫：测试运行专用目录冲突检查 ─────────────────────────────────
    _test_signal_sentinel = test_run_dir / "signal" / "composite.parquet"
    if _test_signal_sentinel.exists() and not args.resume_from_lock:
        log.error(
            "test_run_%d 信号文件已存在（%s）。"
            "拒绝覆盖；若需重建本次测试，请手动删除 %s 后重试，"
            "或传入 --resume-from-lock 跳过已完成的步骤。",
            args.run_id, _test_signal_sentinel, test_run_dir,
        )
        sys.exit(1)

    git_commit = _current_git_commit()
    started_at = datetime.now(timezone.utc).isoformat()
    _write_lock_file(lock_started, args.run_id, "started")
    log.info("本地锁文件已创建：%s", lock_started)

    run_tag = f"[TEST_SET_RUN_{args.run_id}]"
    t_total = time.perf_counter()
    log.info("=" * 60)
    log.info("%s 测试集评估流程启动（第 %d/%d 次）  主基线=%s",
             run_tag, args.run_id, MAX_TEST_RUNS, mainline_run_id)
    log.info("=" * 60)

    # ── 写入 run_config.json（供 compare_runs.py 读取）────────────────────────
    _write_run_config(test_run_dir, test_run_id, args.run_id, mainline_run_id, opt_spec, signal_spec)

    # ── 加载因子面板 ──────────────────────────────────────────────────────────
    if args.factor_panel_dir:
        panel_dir = Path(args.factor_panel_dir)
    else:
        _test_panel_dir = cfg.DATA_PROC / f"factor_panels_test_run_{args.run_id}"
        if _test_panel_dir.exists():
            panel_dir = _test_panel_dir
            log.info("自动检测到测试集专用因子面板目录: %s", panel_dir)
        else:
            panel_dir = PANEL_DIR
            log.warning(
                "未找到测试集专用因子面板目录 %s，使用默认面板（仅含训练/验证期）。\n"
                "如需完整测试期信号覆盖，请先执行：\n"
                "  python -m scripts.build_factor_panels --allow-test-set --run-id %d --end-date 2025-12-31",
                _test_panel_dir, args.run_id,
            )
    if not panel_dir.exists():
        raise FileNotFoundError(
            f"因子面板目录不存在: {panel_dir}\n"
            "测试集运行前请先执行：\n"
            f"  python -m scripts.build_factor_panels "
            f"--allow-test-set --run-id {args.run_id} "
            f"--end-date 2025-12-31 --resume"
        )
    factor_files = sorted(panel_dir.glob("*.parquet"))
    if not factor_files:
        raise FileNotFoundError(f"未找到因子面板: {panel_dir}")

    factor_panels: dict[str, pd.DataFrame] = {}
    for f in factor_files:
        factor_panels[f.stem] = pd.read_parquet(f)

    all_dates = sorted(set().union(*[set(p.index) for p in factor_panels.values()]))
    log.info("因子面板: %d 个因子  %d 期  %s ~ %s",
             len(factor_panels), len(all_dates),
             all_dates[0].date(), all_dates[-1].date())

    if _EVAL_JSON_PATH.exists():
        with open(_EVAL_JSON_PATH, encoding="utf-8") as f:
            eval_data = json.load(f)
        selected_factors  = eval_data["final_factors"]
        stability_weights = eval_data.get("stability_weights")
    else:
        selected_factors  = list(factor_panels.keys())
        stability_weights = None

    # F5-001 一致性断言
    if _FACTOR_SUMMARY_CSV.exists() and _EVAL_JSON_PATH.exists():
        summary_df = pd.read_csv(_FACTOR_SUMMARY_CSV, index_col=0)
        if "final_include" in summary_df.columns:
            summary_final_set = set(summary_df[summary_df["final_include"]].index.tolist())
            final_factors_set = set(selected_factors)
            if summary_final_set != final_factors_set:
                diff = summary_final_set.symmetric_difference(final_factors_set)
                raise RuntimeError(
                    f"因子集合不一致：factor_summary.csv 与 final_factors.json 差异因子 {diff}。"
                    "请重新运行 run_factor_evaluation.py 修复后再试。"
                )
            log.info("因子集合一致性验证通过：%d 个最终因子", len(selected_factors))
        mismatches = validate_directions_vs_summary(summary_df)
        if mismatches:
            log.warning("FACTOR_DIRECTIONS 与评估方向不一致（已记录，不中断运行）: %s", mismatches)

    factor_panels = {k: v for k, v in factor_panels.items() if k in selected_factors}
    log.info("选用因子: %d 个", len(factor_panels))

    # ── F9-008: 因子面板测试期覆盖检查 ─────────────────────────────────────────
    _panel_last_date = all_dates[-1]
    _test_end_ts = pd.Timestamp(cfg.TEST_END)
    if _panel_last_date < _test_end_ts - pd.DateOffset(months=2):
        _coverage_reason = (
            f"因子面板最后日期 {_panel_last_date.date()} 距 TEST_END={cfg.TEST_END} "
            "超过 2 个月，"
            + (
                "当前使用默认面板目录（仅含训练/验证期数据）。\n"
                "测试集运行前请先执行：\n"
                f"  python -m scripts.build_factor_panels "
                f"--allow-test-set --run-id {args.run_id} --end-date 2025-12-31"
                if panel_dir == PANEL_DIR
                else f"请检查因子面板目录: {panel_dir}"
            )
        )
        log.error("因子面板覆盖检查失败: %s", _coverage_reason)
        _write_run_failed(test_run_dir, test_run_id, _coverage_reason, git_commit)
        sys.exit(1)

    # ── 写入 inputs.lock.json ────────────────────────────────────────────────
    _write_inputs_lock(
        test_run_dir, test_run_id, args.run_id, mainline_run_id,
        panel_dir, factor_files, git_commit,
    )

    # ── 写入 TEST_SET_RUN_LOCK.json (status=started) ─────────────────────────
    _write_test_set_run_lock(
        test_run_dir, args.run_id, test_run_id, mainline_run_id,
        status="started", started_at=started_at, finished_at=None,
        panel_dir=panel_dir, git_commit=git_commit,
    )

    # ── F9-004: resume 辅助函数 ──────────────────────────────────────────────
    def _should_skip_step(sentinel: Path, step_name: str) -> bool:
        if args.resume_from_lock and sentinel.exists():
            log.info(
                "--resume-from-lock: %s 已存在，跳过 %s（不覆盖已有产物）",
                sentinel.relative_to(test_run_dir), step_name,
            )
            return True
        return False

    # ── Steps 1-5: 统一异常处理（失败写 RUN_FAILED.json，不写 RUN_FINISHED）──
    try:
        # ── Step 1: 扩展 fwd_ret_panel ──────────────────────────────────────────
        _step1_sentinel = test_run_dir / "fwd_ret_panel.parquet"
        if _should_skip_step(_step1_sentinel, "Step 1"):
            fwd_ret_panel = pd.read_parquet(_step1_sentinel)
            log.info("从已有产物加载 fwd_ret_panel")
        else:
            log.info("--- Step 1: 扩展 fwd_ret_panel ---")
            fwd_ret_panel = _extend_fwd_ret_panel(all_dates, output_dir=test_run_dir)

        # ── Step 2: 重建合成信号 ─────────────────────────────────────────────────
        _step2_sentinel = test_run_dir / "signal" / "composite.parquet"
        if _should_skip_step(_step2_sentinel, "Step 2"):
            composite_ic_ir = pd.read_parquet(_step2_sentinel)
            log.info("从已有产物加载 signal/composite.parquet")
        else:
            log.info("--- Step 2: 重建合成信号（输出→signal/）---")
            _, composite_ic_ir = _build_composite_signals(
                factor_panels, fwd_ret_panel, all_dates, stability_weights,
                output_dir=test_run_dir, run_id=test_run_id, signal_spec=signal_spec,
            )

        # ── Step 3-4: 协方差 + 组合优化 ─────────────────────────────────────────
        _step34_sentinel = test_run_dir / "portfolio" / "target_weights.parquet"
        if _should_skip_step(_step34_sentinel, "Step 3-4"):
            weights_v2 = pd.read_parquet(_step34_sentinel)
            weights_v1 = pd.read_parquet(test_run_dir / "portfolio" / "baseline_weights.parquet")
            log.info("从已有产物加载 portfolio/target_weights.parquet")
        else:
            log.info("--- Step 3-4: 协方差估计 + 组合优化（输出→portfolio/）---")
            weights_v1, weights_v2 = _run_optimization(
                composite_ic_ir, all_dates, test_run_dir, opt_config,
            )

        # ── Step 5: 测试集回测 ───────────────────────────────────────────────────
        _step5_sentinel = test_run_dir / "backtest" / "nav_valid.parquet"
        if _should_skip_step(_step5_sentinel, "Step 5"):
            log.info("从已有产物加载 backtest/nav_valid.parquet，跳过回测重算（resume 模式）")
        else:
            log.info("--- Step 5: 测试集回测（%s ~ %s，输出→backtest/）---",
                     cfg.TEST_START.date(), cfg.TEST_END.date())
            _run_test_backtest(weights_v1, weights_v2, run_tag, test_run_dir)

    except Exception as exc:
        _tb = traceback.format_exc()
        log.error("测试集流程失败: %s\n%s", exc, _tb)
        _write_run_failed(test_run_dir, test_run_id, str(exc), git_commit)
        _write_test_set_run_lock(
            test_run_dir, args.run_id, test_run_id, mainline_run_id,
            status="failed", started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            panel_dir=panel_dir, git_commit=git_commit,
        )
        sys.exit(1)

    # ── 写入 RUN_FINISHED.json ────────────────────────────────────────────────
    _write_lock_file(lock_finished, args.run_id, "finished")
    finished_at = datetime.now(timezone.utc).isoformat()

    # ── 写入 manifest.json（RUN_FINISHED 写入后，确保包含 RUN_FINISHED.json）──
    try:
        _write_manifest(test_run_dir)
    except Exception as e:
        log.warning("manifest.json 写入失败（不阻断）: %s", e)

    # ── 更新 TEST_SET_RUN_LOCK.json (status=finished) ─────────────────────────
    _write_test_set_run_lock(
        test_run_dir, args.run_id, test_run_id, mainline_run_id,
        status="finished", started_at=started_at, finished_at=finished_at,
        panel_dir=panel_dir, git_commit=git_commit,
    )

    # ── ledger 记录 ──────────────────────────────────────────────────────────
    record_test_set_run(args.run_id, status="finished", git_commit=git_commit)

    # ── 生成 reports/self_check.md（RUN_FINISHED 写入后，checklist 才准确）─────
    try:
        from src.pipeline.contracts import ExperimentSpec, SignalSpec, OptimizerSpec, BacktestSpec

        _spec = ExperimentSpec(
            experiment_id  = test_run_id,
            description    = f"测试集评估 test_run_{args.run_id}（主基线={mainline_run_id}）",
            period_scope   = f"test_run_{args.run_id}",
            allow_test_set = True,
            signal         = SignalSpec(
                method        = signal_spec.get("method",        "icir"),
                target        = signal_spec.get("target",        "excess_return"),
                training_mode = signal_spec.get("training_mode", "expanding"),
                purge_months  = signal_spec.get("purge_months",  2),
                window_months = signal_spec.get("window_months"),
            ),
            optimizer      = OptimizerSpec(
                te_target_annual = opt_config.te_target_annual,
                turnover_lambda  = opt_config.turnover_lambda,
                topn             = opt_config.topn,
            ),
            backtest       = BacktestSpec(),
        )
        write_self_check_md(_spec, test_run_dir)
    except Exception as e:
        log.warning("self_check.md 生成失败（不阻断）: %s", e)

    elapsed_min = (time.perf_counter() - t_total) / 60
    log.info("=" * 60)
    log.info("%s 全流程完成，总耗时 %.1f 分钟", run_tag, elapsed_min)
    log.info("测试 run 目录: %s", test_run_dir)
    log.info("=" * 60)
    print()
    print("=" * 70)
    print("⚠️  请立即执行以下 git commit，否则下次运行会因锁文件被阻止：")
    print(f"   git add -A")
    print(f"   git commit -m \"{run_tag} 测试集评估完成 (2023-2025)\"")
    print("=" * 70)


if __name__ == "__main__":
    main()
