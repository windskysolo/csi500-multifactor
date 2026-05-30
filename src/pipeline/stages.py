"""
src/pipeline/stages.py — 实验各阶段的编排包装层

每个 stage 函数接收 ExperimentSpec + 路径，调用底层 src/ 模块，
把产物写到 run_dir 下的标准子目录，并返回产物路径字典。

stage 函数不重写金融逻辑，只负责：
  1. 根据 spec 选择正确的实现
  2. 把输入/输出路径对齐到 artifact contract
  3. 捕获失败并抛出带上下文的异常
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal stage
# ---------------------------------------------------------------------------

def run_signal_stage(
    spec,
    run_dir: Path,
    data_proc: Optional[Path] = None,
) -> dict[str, Path]:
    """
    运行信号阶段，把产物写到 run_dir/signal/。

    支持的 spec.signal.method:
      "icir"                    — IC_IR 加权合成信号
      "ridge" + "expanding"     — expanding window Ridge 回归信号
      "ridge" + "rolling"       — rolling window Ridge 回归信号（需 window_months）
      "ridge" + "decay_weighted_expanding" — 指数衰减 Ridge，需 spec.signal.half_life_months

    Returns:
        dict with key "composite" → Path to composite.parquet
    """
    from src import config as cfg

    _data_proc = data_proc or cfg.DATA_PROC
    signal_dir = run_dir / "signal"
    signal_dir.mkdir(parents=True, exist_ok=True)

    sig = spec.signal
    method = sig.method
    mode   = sig.training_mode

    log.info("信号阶段: method=%s  training_mode=%s", method, mode)

    if method == "icir":
        return _run_signal_icir(sig, signal_dir, _data_proc, spec)
    elif method == "ridge":
        return _run_signal_ridge(sig, signal_dir, _data_proc, spec, mode)
    else:
        raise ValueError(f"未知的信号方法: '{method}'。支持: 'icir', 'ridge'")


def _run_signal_icir(sig, signal_dir: Path, data_proc: Path, spec) -> dict[str, Path]:
    """IC_IR 加权合成信号。"""
    import pandas as pd
    from src import config as cfg
    from src.evaluation.ic_analysis import compute_ic_series
    from src.signal.combiner import build_composite_panel, DEFAULT_WINDOW_MONTHS

    panel_dir = data_proc / "factor_panels"
    fwd_path  = data_proc / "fwd_ret_panel.parquet"
    eval_dir  = Path(__file__).parents[2] / "reports" / "factor_evaluation"

    # 加载因子面板
    factor_panels = _load_factor_panels(
        panel_dir, eval_dir,
        exclude_factors=sig.exclude_factors or [],
    )
    fwd_ret_panel = _load_fwd_ret(fwd_path, cfg.VALID_END)

    all_dates = pd.DatetimeIndex(
        sorted(set().union(*[set(p.index) for p in factor_panels.values()]))
    )
    tv_dates  = all_dates[all_dates <= cfg.VALID_END].tolist()

    ic_series_map: dict = {}
    for name, panel in factor_panels.items():
        tv_panel = panel.loc[panel.index <= cfg.VALID_END]
        ic_series_map[name] = compute_ic_series(tv_panel, fwd_ret_panel)

    # 读取 stability_weights（如果有）
    eval_json = eval_dir / "final_factors.json"
    stability_weights = None
    if eval_json.exists():
        with open(eval_json, encoding="utf-8") as f:
            stability_weights = json.load(f).get("stability_weights")

    composite, diagnostics = build_composite_panel(
        factor_panels     = {k: v.loc[[d for d in v.index if d <= cfg.VALID_END]]
                             for k, v in factor_panels.items()},
        ic_series_map     = ic_series_map,
        rebalance_dates   = tv_dates,
        method            = "ic_ir",
        window_months     = sig.window_months if sig.window_months is not None else DEFAULT_WINDOW_MONTHS,
        min_valid_factors = cfg.SIGNAL_MIN_VALID_FACTORS,
        stability_weights = stability_weights,
        return_diagnostics = True,
    )
    composite.columns = composite.columns.astype(str)
    out_path = signal_dir / "composite.parquet"
    composite.to_parquet(out_path)

    # IC 明细
    ic_df = pd.DataFrame(ic_series_map)
    ic_df.index.name = "rebalance_date"
    ic_df.to_parquet(signal_dir / "ic_detail.parquet")

    # 权重历史
    weight_history = diagnostics.get("weight_history", {})
    if weight_history:
        wh = pd.DataFrame(weight_history).T.rename_axis("rebalance_date")
        wh.index = pd.DatetimeIndex(wh.index)
        wh.to_parquet(signal_dir / "weight_history.parquet")

    # Metadata
    _write_signal_metadata(signal_dir, spec, {
        "method": "ic_ir",
        "n_factors": len(factor_panels),
        "n_rebalance_dates": len(tv_dates),
        "cold_start_count": diagnostics.get("cold_start_count", 0),
    })

    log.info("信号写入 %s  shape=%s", out_path, composite.shape)
    return {"composite": out_path}


def _run_signal_ridge(sig, signal_dir: Path, data_proc: Path, spec, mode: str) -> dict[str, Path]:
    """Ridge 回归信号（expanding 或 rolling 窗口）。"""
    import pandas as pd
    from src import config as cfg

    panel_dir = data_proc / "factor_panels"
    fwd_path  = data_proc / "fwd_ret_panel.parquet"
    eval_dir  = Path(__file__).parents[2] / "reports" / "factor_evaluation"

    factor_panels = _load_factor_panels(
        panel_dir, eval_dir,
        exclude_factors=sig.exclude_factors or [],
    )
    fwd_ret_panel = _load_fwd_ret(fwd_path, cfg.VALID_END)

    all_dates = pd.DatetimeIndex(
        sorted(set().union(*[set(p.index) for p in factor_panels.values()]))
    )
    tv_dates  = all_dates[all_dates <= cfg.VALID_END]
    factor_names = sorted(factor_panels.keys())

    # tv_panels：只保留训练/验证期
    tv_panels = {k: v.loc[v.index <= cfg.VALID_END]
                 for k, v in factor_panels.items()}

    from experiments.legacy.ridge_signal.ridge_combiner import RidgeCombiner
    from experiments.legacy.ridge_rolling.rolling_combiner import RidgeRollingCombiner

    if mode == "rolling":
        if not sig.window_months:
            raise ValueError("rolling 模式需要 spec.signal.window_months")
        combiner = RidgeRollingCombiner(
            factor_names     = factor_names,
            window_months    = sig.window_months,
            alpha_candidates = sig.alpha_grid,
            purge_months     = sig.purge_months,
        )
    elif mode == "decay_weighted_expanding":
        if not sig.half_life_months:
            raise ValueError("decay_weighted_expanding 模式需要 spec.signal.half_life_months")
        from src.signal.ridge_decay import RidgeDecayCombiner
        combiner = RidgeDecayCombiner(
            factor_names     = factor_names,
            half_life_months = sig.half_life_months,
            alpha_candidates = sig.alpha_grid,
            purge_months     = sig.purge_months,
        )
    elif mode == "expanding":
        combiner = RidgeCombiner(
            factor_names     = factor_names,
            alpha_candidates = sig.alpha_grid,
            purge_months     = sig.purge_months,
        )
    else:
        raise ValueError(
            f"未知 ridge training_mode: {mode!r}。"
            "支持: 'expanding', 'rolling', 'decay_weighted_expanding'"
        )
    combiner.select_alpha_walk_forward(tv_panels, fwd_ret_panel, tv_dates)
    composite = combiner.build_ridge_panel(tv_panels, fwd_ret_panel, tv_dates)
    composite.columns = composite.columns.astype(str)

    out_path = signal_dir / "composite.parquet"
    composite.to_parquet(out_path)

    # 系数历史
    coef_hist = getattr(combiner, "coef_history_", None)
    if coef_hist is not None and not coef_hist.empty:
        coef_hist.to_parquet(signal_dir / "coef_history.parquet")

    _write_signal_metadata(signal_dir, spec, {
        "method": "ridge",
        "training_mode": mode,
        "window_months": sig.window_months if mode == "rolling" else None,
        "half_life_months": sig.half_life_months if mode == "decay_weighted_expanding" else None,
        "selected_alpha": getattr(combiner, "alpha_", None),
        "n_factors": len(factor_names),
        "n_rebalance_dates": len(tv_dates),
    })

    log.info("信号写入 %s  shape=%s", out_path, composite.shape)
    return {"composite": out_path}


# ---------------------------------------------------------------------------
# Portfolio stage
# ---------------------------------------------------------------------------

def run_portfolio_stage(
    spec,
    run_dir: Path,
    signal_path: Path,
    data_proc: Optional[Path] = None,
) -> dict[str, Path]:
    """
    运行组合优化阶段，把产物写到 run_dir/portfolio/。

    Args:
        signal_path: composite.parquet 路径（来自信号阶段或 --input-signal-run）。
    """
    from src import config as cfg
    from src.portfolio.optimizer import OptimizeConfig
    from scripts.run_portfolio_optimization import main as portfolio_main

    _data_proc = data_proc or cfg.DATA_PROC

    opt = spec.optimizer
    optimizer_config = OptimizeConfig(
        te_target_annual  = opt.te_target_annual,
        industry_max_dev  = opt.industry_max_dev,
        single_max_dev    = opt.single_max_dev,
        turnover_lambda   = opt.turnover_lambda,
        topn              = opt.topn,
        max_solve_seconds = 30.0,
        prefilter_topn    = getattr(opt, "prefilter_topn", 0),
        prefilter_mode    = getattr(opt, "prefilter_mode", "none"),
    )

    optimizer_mode = getattr(opt, "optimizer_mode", "qp")
    log.info("组合优化阶段: mode=%s  te=%.0f%%  lambda=%.4f  topn=%d",
             optimizer_mode, opt.te_target_annual * 100, opt.turnover_lambda, opt.topn)

    effective_signal_path = signal_path
    if getattr(opt, "use_rank_transform", False):
        effective_signal_path = _apply_rank_transform(signal_path, run_dir)

    portfolio_main(
        signal_path      = effective_signal_path,
        output_dir       = run_dir,
        cov_cache_dir    = _data_proc / "cov_cache",
        optimizer_config = optimizer_config,
        optimizer_mode   = optimizer_mode,
    )

    port_dir = run_dir / "portfolio"
    return {
        "target_weights":   port_dir / "target_weights.parquet",
        "baseline_weights": port_dir / "baseline_weights.parquet",
        "optimizer_meta":   port_dir / "optimizer_meta.parquet",
    }


# ---------------------------------------------------------------------------
# Backtest stage
# ---------------------------------------------------------------------------

def run_backtest_stage(
    spec,
    run_dir: Path,
    data_proc: Optional[Path] = None,
) -> dict[str, Path]:
    """
    运行回测阶段，把产物写到 run_dir/backtest/。
    输入权重从 run_dir/portfolio/ 读取。
    """
    from src import config as cfg
    from scripts.run_backtest import main as backtest_main

    port_dir = run_dir / "portfolio"
    log.info("回测阶段: valid %s ~ %s", cfg.VALID_START.date(), cfg.VALID_END.date())

    backtest_main(
        weights_path          = port_dir / "target_weights.parquet",
        baseline_weights_path = port_dir / "baseline_weights.parquet",
        weights_meta_path     = port_dir / "optimizer_meta.parquet",
        output_dir            = run_dir,
        allow_noncompliant    = False,
    )

    bt_dir = run_dir / "backtest"
    return {
        "nav_valid":             bt_dir / "nav_valid.parquet",
        "metrics_valid":         bt_dir / "metrics_valid.parquet",
        "trades_valid":          bt_dir / "trades_valid.parquet",
        "actual_weights_valid":  bt_dir / "actual_weights_valid.parquet",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_rank_transform(signal_path: Path, run_dir: Path) -> Path:
    """
    对截面 Alpha 做排名百分位变换，写到 signal/ 目录，返回新路径。

    rank(pct=True, na_option='keep') 输出 (0,1]，减 0.5 后变为 (-0.5, 0.5]。
    保留 NaN（optimizer 内部对无观点股票置 0 处理）。

    为什么在 stages 层而非 optimizer 层做：
      optimizer.py 只负责给定 Alpha 向量后的权重求解；排名变换是信号后处理，
      属于信号→组合衔接逻辑，放在此处边界最清晰。
    """
    import pandas as pd

    signal = pd.read_parquet(signal_path)
    ranked = signal.rank(axis=1, pct=True, na_option="keep") - 0.5
    out_path = run_dir / "signal" / "composite_rank_transformed.parquet"
    ranked.to_parquet(out_path)
    log.info("排名变换完成，写入 %s  shape=%s", out_path, ranked.shape)
    return out_path


def _load_factor_panels(
    panel_dir: Path,
    eval_dir: Path,
    exclude_factors: list[str] | None = None,
) -> dict:
    import json

    factor_files = sorted(panel_dir.glob("*.parquet"))
    if not factor_files:
        raise FileNotFoundError(f"未找到因子面板: {panel_dir}")

    import pandas as pd
    all_panels = {f.stem: pd.read_parquet(f) for f in factor_files}

    eval_json = eval_dir / "final_factors.json"
    if eval_json.exists():
        with open(eval_json, encoding="utf-8") as f:
            selected = json.load(f)["final_factors"]
        result = {k: v for k, v in all_panels.items() if k in selected}
    else:
        log.warning("final_factors.json 不存在，使用全部 %d 个因子面板", len(all_panels))
        result = all_panels

    if exclude_factors:
        excluded_found = [f for f in exclude_factors if f in result]
        result = {k: v for k, v in result.items() if k not in exclude_factors}
        log.info("已排除因子：%s  剩余 %d 个", excluded_found, len(result))
    return result


def _load_fwd_ret(fwd_path: Path, valid_end) -> "pd.DataFrame":
    import pandas as pd

    if not fwd_path.exists():
        raise FileNotFoundError(
            f"fwd_ret_panel.parquet 不存在: {fwd_path}。"
            "请先运行 run_signal_combination.py --recompute-fwd-ret"
        )
    fwd = pd.read_parquet(fwd_path)
    return fwd.loc[fwd.index <= valid_end]


def write_self_check_md(spec, run_dir: Path) -> Path:
    """
    从已完成的 run 产物自动生成 reports/self_check.md。
    读取 backtest/metrics_valid.parquet 和 backtest/trades_valid.parquet，
    写入关键指标及硬阈值 PASS/FAIL 结果。
    返回 self_check.md 的路径。
    """
    import pandas as pd
    from src import config as cfg

    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "self_check.md"

    metrics_path = run_dir / "backtest" / "metrics_valid.parquet"
    trades_path  = run_dir / "backtest" / "trades_valid.parquet"

    lines: list[str] = [
        f"# Self Check: {spec.experiment_id}",
        "",
        f"Run dir : `{run_dir.name}`",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        (
            f"TEST period: {cfg.TEST_START.date()} ~ {cfg.TEST_END.date()}"
            if getattr(spec, "period_scope", "").startswith("test_run_")
            else f"Valid period: {cfg.VALID_START.date()} ~ {cfg.VALID_END.date()}"
        ),
        "",
    ]

    if not metrics_path.exists():
        lines += [
            "## ⚠️ metrics_valid.parquet 不存在，无法生成指标",
            "",
            "_Self-check 自动生成失败，请手动补充。_",
        ]
        out_path.write_text("\n".join(lines), encoding="utf-8")
        log.warning("self_check.md: metrics_valid.parquet 不存在，写入占位文件")
        return out_path

    metrics = pd.read_parquet(metrics_path)
    v2 = metrics["v2"] if "v2" in metrics.columns else metrics.iloc[:, -1]

    ir        = float(v2.get("information_ratio", float("nan")))
    excess_r  = float(v2.get("excess_return",     float("nan")))
    mdd       = float(v2.get("excess_max_drawdown", float("nan")))
    te        = float(v2.get("tracking_error",    float("nan")))
    win_rate  = float(v2.get("monthly_win_rate",  float("nan")))
    ann_ret   = float(v2.get("annualized_return", float("nan")))
    bench_ret = float(v2.get("benchmark_return",  float("nan")))

    # 年化换手率（从交易记录计算）
    annual_to_pct = float("nan")
    if trades_path.exists():
        try:
            tl = pd.read_parquet(trades_path)
            n_months = len(tl) if not tl.empty else 0
            if n_months > 0 and "buy_value" in tl.columns:
                total_to = (
                    (tl["buy_value"] + tl["sell_value"])
                    / tl["portfolio_value_before"]
                ).sum()
                annual_to_pct = total_to / n_months * 12 * 100
        except Exception as e:
            log.warning("计算年化换手率失败: %s", e)

    def _fmt_pct(v):
        return f"{v*100:+.2f}%" if not _nan(v) else "N/A"

    def _nan(v):
        import math
        return math.isnan(v)

    def _check(label, passed, value):
        icon = "✅ PASS" if passed else "❌ FAIL"
        return f"| {label} | {value} | {icon} |"

    lines += [
        "## 验证期指标（V2 优化权重）",
        "",
        "| 指标 | 值 |",
        "|------|----|",
        f"| 年化超额收益 | {_fmt_pct(excess_r)} |",
        f"| 年化绝对收益 | {_fmt_pct(ann_ret)} |",
        f"| 基准收益 | {_fmt_pct(bench_ret)} |",
        f"| 跟踪误差 | {_fmt_pct(te) if not _nan(te) else 'N/A'} |",
        f"| 月胜率 | {win_rate*100:.1f}% |" if not _nan(win_rate) else "| 月胜率 | N/A |",
        "",
        "## 硬指标检查",
        "",
        "| 指标 | 值 | 结论 |",
        "|------|----|----|",
        _check("IR ≥ 0.5",
               not _nan(ir) and ir >= 0.5,
               f"{ir:.3f}" if not _nan(ir) else "N/A"),
        _check("超额最大回撤 ≤ 10%",
               not _nan(mdd) and abs(mdd) <= 0.10,
               f"{abs(mdd)*100:.2f}%" if not _nan(mdd) else "N/A"),
        _check("年化双边换手 500–1500%",
               not _nan(annual_to_pct) and 500 <= annual_to_pct <= 1500,
               f"{annual_to_pct:.0f}%" if not _nan(annual_to_pct) else "N/A"),
        "",
        "## 产物清单",
        "",
    ]

    artifact_checks = [
        "signal/composite.parquet",
        "portfolio/target_weights.parquet",
        "backtest/nav_valid.parquet",
        "backtest/metrics_valid.parquet",
        "manifest.json",
        "RUN_FINISHED.json",
    ]
    for a in artifact_checks:
        exists = (run_dir / a).exists()
        lines.append(f"- [{'x' if exists else ' '}] `{a}`")

    lines += [
        "",
        "## 规格摘要",
        "",
        f"- experiment_id: `{spec.experiment_id}`",
        f"- signal.method: `{spec.signal.method}`",
        f"- signal.training_mode: `{spec.signal.training_mode}`",
        f"- optimizer.te_target_annual: `{spec.optimizer.te_target_annual:.0%}`",
        f"- optimizer.turnover_lambda: `{spec.optimizer.turnover_lambda}`",
        f"- optimizer.topn: `{spec.optimizer.topn}`",
        f"- optimizer.optimizer_mode: `{getattr(spec.optimizer, 'optimizer_mode', 'qp')}`",
        f"- optimizer.use_rank_transform: `{getattr(spec.optimizer, 'use_rank_transform', False)}`",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("self_check.md 已写入: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Diagnosis stage（信号实现质量诊断）
# ---------------------------------------------------------------------------

def run_diagnosis_stage(
    spec,
    run_dir: Path,
    data_proc=None,
) -> dict:
    """
    运行信号实现质量诊断，产物写到 run_dir/reports/。

    输入（从已完成的 signal / portfolio 阶段读取）：
        signal/composite.parquet          → 截面信号
        portfolio/target_weights.parquet  → 实际持仓权重
        data_proc/fwd_ret_panel.parquet   → 前瞻收益

    输出：
        reports/signal_quality_report.json  ← 结构化 JSON，供 compare_runs 读取
        reports/self_check.md               ← 追加诊断节（文件已存在时）

    本函数为非阻断设计：调用方应自行 try/except，失败不影响 run 状态。

    Returns:
        dict with key "signal_quality_report" → Path（成功时），或空 dict（失败时）
    """
    import json as _json

    import pandas as pd
    from src import config as cfg
    from src.evaluation.signal_quality import SignalToPositionDiagnostics

    _data_proc = data_proc or cfg.DATA_PROC

    signal_path  = run_dir / "signal" / "composite.parquet"
    weights_path = run_dir / "portfolio" / "target_weights.parquet"
    fwd_path     = _data_proc / "fwd_ret_panel.parquet"
    reports_dir  = run_dir / "reports"
    out_json     = reports_dir / "signal_quality_report.json"
    out_md       = reports_dir / "self_check.md"

    # 检查必须的输入文件
    missing = [p for p in [signal_path, weights_path, fwd_path] if not p.exists()]
    if missing:
        log.warning("diagnosis: 缺少输入文件，跳过诊断: %s",
                    [str(m) for m in missing])
        return {}

    signals = pd.read_parquet(signal_path)
    weights = pd.read_parquet(weights_path)
    fwd_ret = pd.read_parquet(fwd_path)

    # 确保 index 为 Timestamp
    signals.index = pd.DatetimeIndex(signals.index)
    weights.index = pd.DatetimeIndex(weights.index)
    fwd_ret.index = pd.DatetimeIndex(fwd_ret.index)

    diag = SignalToPositionDiagnostics(min_stocks=10)
    report = diag.run(
        signals=signals,
        weights=weights,
        returns=fwd_ret,
        valid_start=pd.Timestamp(cfg.VALID_START),
        valid_end=pd.Timestamp(cfg.VALID_END),
    )

    # 写 JSON
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        _json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("signal_quality_report.json 已写入: %s", out_json)

    # 追加到 self_check.md（若存在）
    if out_md.exists():
        section = diag.format_report_section(report)
        existing = out_md.read_text(encoding="utf-8")
        # 避免重复追加（幂等）
        if "Signal Quality Diagnostics" not in existing:
            out_md.write_text(existing + section, encoding="utf-8")
            log.info("诊断节已追加到 self_check.md")

    return {"signal_quality_report": out_json}


def _write_signal_metadata(signal_dir: Path, spec, extra: dict) -> None:
    meta = {
        "experiment_id": spec.experiment_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    (signal_dir / "signal_metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
