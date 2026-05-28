"""
scripts/run_portfolio_optimization.py — 训练/验证期组合优化
============================================================

功能：
  1. 读取合成信号（composite_signal_ic_ir.parquet）
  2. 读取 index_member、industry 数据
  3. 估计/读取协方差矩阵缓存（data/processed/cov_cache/）
  4. 对训练/验证期每个调仓日运行 QP 组合优化
  5. 写入权重产物

旧用法（输出到 data/processed/，行为不变）：
  python -m scripts.run_portfolio_optimization
  python -m scripts.run_portfolio_optimization --rebuild-cov

新用法（输出到 run 目录，产物遵循 artifact contract）：
  python -m scripts.run_portfolio_optimization \\
      --signal-path runs/train_valid/<run_id>/signal/composite.parquet \\
      --output-dir  runs/train_valid/<run_id>
  # 输出：<output-dir>/portfolio/target_weights.parquet 等
"""

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from src.portfolio.covariance import estimate_covariance_lw, validate_and_repair_covariance
from src.portfolio.optimizer import OptimizeConfig, optimize_single_period

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 旧默认路径（不传 output_dir 时的兼容路径）
# ---------------------------------------------------------------------------
_DEFAULT_SIGNAL_PATH  = cfg.DATA_PROC / "composite_signal_ic_ir.parquet"
_DEFAULT_WEIGHTS_OPT  = cfg.DATA_PROC / "portfolio_weights_optimized.parquet"
_DEFAULT_WEIGHTS_BL   = cfg.DATA_PROC / "portfolio_weights_baseline.parquet"
_DEFAULT_WEIGHTS_META = cfg.DATA_PROC / "portfolio_weights_meta.parquet"
_DEFAULT_COV_DIR      = cfg.DATA_PROC / "cov_cache"

# 兼容旧脚本直接引用这些常量的情况
SIGNAL_IC_IR_PATH = _DEFAULT_SIGNAL_PATH
WEIGHTS_OPT_PATH  = _DEFAULT_WEIGHTS_OPT
WEIGHTS_BL_PATH   = _DEFAULT_WEIGHTS_BL
WEIGHTS_META_PATH = _DEFAULT_WEIGHTS_META
COV_CACHE_DIR     = _DEFAULT_COV_DIR


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_ROOT), text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _write_cov_metadata(
    cache_path: Path,
    codes: list[str],
    min_eig_before: float,
    diag_delta: float,
    was_repaired: bool,
) -> None:
    """F6-003: 写入协方差缓存的审计 metadata sidecar（.meta.json）。"""
    meta_path = cache_path.with_suffix(".meta.json")
    codes_hash = hashlib.md5("|".join(codes).encode()).hexdigest()[:12]
    meta_path.write_text(
        json.dumps({
            "date":            cache_path.stem,
            "n_codes":         len(codes),
            "codes_hash":      codes_hash,
            "min_eig_before":  min_eig_before,
            "diag_delta":      diag_delta,
            "was_repaired":    was_repaired,
            "source_commit":   _git_commit(),
            "generated_at":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolve_paths(
    signal_path: Optional[Path],
    output_dir: Optional[Path],
    cov_cache_dir: Optional[Path],
) -> tuple[Path, Path, Path, Path, Path]:
    """返回 (signal_path, weights_opt, weights_bl, weights_meta, cov_dir)。"""
    _signal = signal_path or _DEFAULT_SIGNAL_PATH
    _cov    = cov_cache_dir or _DEFAULT_COV_DIR

    if output_dir is None:
        return _signal, _DEFAULT_WEIGHTS_OPT, _DEFAULT_WEIGHTS_BL, _DEFAULT_WEIGHTS_META, _cov

    port_dir = output_dir / "portfolio"
    port_dir.mkdir(parents=True, exist_ok=True)
    return (
        _signal,
        port_dir / "target_weights.parquet",
        port_dir / "baseline_weights.parquet",
        port_dir / "optimizer_meta.parquet",
        _cov,
    )


def main(
    rebuild_cov: bool = False,
    signal_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    cov_cache_dir: Optional[Path] = None,
    optimizer_config: Optional[OptimizeConfig] = None,
) -> None:
    """
    Args:
        rebuild_cov:      强制重新估计所有协方差。
        signal_path:      合成信号路径；None 使用旧默认路径（data/processed/）。
        output_dir:       run 根目录；None 输出到旧默认路径（data/processed/）。
        cov_cache_dir:    协方差缓存目录；None 使用 data/processed/cov_cache/。
        optimizer_config: 优化器配置；None 使用 cfg 中的默认值。
    """
    _signal_path, _w_opt, _w_bl, _w_meta, _cov_dir = _resolve_paths(
        signal_path, output_dir, cov_cache_dir
    )
    _opt_config = optimizer_config or OptimizeConfig(
        te_target_annual  = cfg.OPT_TE_TARGET_ANNUAL,
        industry_max_dev  = cfg.OPT_INDUSTRY_MAX_DEV,
        single_max_dev    = cfg.OPT_SINGLE_MAX_DEV,
        topn              = cfg.OPT_TOPN,
        max_solve_seconds = 30.0,
    )
    _baseline_n = _opt_config.topn

    log.info("=" * 60)
    log.info("组合优化（训练/验证期 %s ~ %s）", cfg.TRAIN_START.date(), cfg.VALID_END.date())
    log.info("=" * 60)

    if not _signal_path.exists():
        log.error("合成信号文件不存在：%s\n请先运行 run_signal_combination.py", _signal_path)
        sys.exit(1)

    composite_signal = pd.read_parquet(_signal_path)
    log.info("读取合成信号: %s  %s ~ %s",
             composite_signal.shape,
             composite_signal.index[0].date(),
             composite_signal.index[-1].date())

    # 限制到训练/验证期（防止误用含测试期信号的文件）
    tv_mask = composite_signal.index <= cfg.VALID_END
    composite_signal = composite_signal.loc[tv_mask]
    log.info("训练/验证期调仓日: %d 期", len(composite_signal))

    # 读取 index_member、industry
    log.info("读取 index_member 及行业数据...")
    index_member_raw = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    industry_raw     = pd.read_parquet(cfg.DATA_PROC / "industry.parquet")

    industry_pivot = (
        industry_raw["industry_code"]
        .unstack(level="ts_code")
        .sort_index()
        .ffill()
    )

    rebalance_dates = list(composite_signal.index)
    dates_in_member = index_member_raw.index.get_level_values("rebalance_date").unique()
    available_dates = [d for d in rebalance_dates if d in dates_in_member]
    if len(available_dates) < len(rebalance_dates):
        missing = set(rebalance_dates) - set(available_dates)
        log.warning("index_member 缺少 %d 个调仓日: %s", len(missing), sorted(missing)[:5])

    # 构建逐期输入字典
    benchmark_weights_dict: dict = {}
    halt_dict:     dict = {}
    limit_up_dict: dict = {}
    limit_dn_dict: dict = {}
    industry_dict: dict = {}

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

    # 协方差估计（带缓存）
    _cov_dir.mkdir(exist_ok=True)
    cov_cache: dict = {}
    n_loaded = n_estimated = n_repaired = n_failed = 0
    t_cov = time.perf_counter()

    for T in available_dates:
        codes      = list(benchmark_weights_dict[T].index)
        cache_path = _cov_dir / f"{T.strftime('%Y%m%d')}.parquet"

        if cache_path.exists() and not rebuild_cov:
            try:
                cov_df = pd.read_parquet(cache_path).reindex(index=codes, columns=codes)
                if not cov_df.isna().any().any():
                    cov_arr = cov_df.values.astype(float)
                    cov_arr, was_valid, min_eig, diag_delta = validate_and_repair_covariance(cov_arr)
                    if not was_valid:
                        log.warning("%s 协方差不满足正定性 (min_eig=%.2e)，已修复", T.date(), min_eig)
                        n_repaired += 1
                    # F6-003: 为所有缓存补写 sidecar（无论是否修复，旧缓存无 sidecar）
                    meta_path = cache_path.with_suffix(".meta.json")
                    if not meta_path.exists():
                        _write_cov_metadata(cache_path, codes, min_eig, diag_delta, was_repaired=(not was_valid))
                    cov_cache[T] = cov_arr
                    n_loaded += 1
                    continue
            except Exception as e:
                log.warning("%s 缓存读取失败: %s，重新估计", T.date(), e)

        try:
            cov = estimate_covariance_lw(codes, T)
            # F6-003: 验证并记录审计元数据
            cov_validated, was_valid, min_eig_before, diag_delta = validate_and_repair_covariance(cov)
            cov_cache[T] = cov_validated
            pd.DataFrame(cov_validated, index=codes, columns=codes).to_parquet(cache_path)
            _write_cov_metadata(cache_path, codes, min_eig_before, diag_delta, was_repaired=(not was_valid))
            n_estimated += 1
        except Exception as e:
            log.warning("%s 协方差估计失败: %s", T.date(), e)
            n_failed += 1

    log.info(
        "协方差: 读缓存=%d（修复=%d）  新估计=%d  失败=%d  用时=%.1fs",
        n_loaded, n_repaired, n_estimated, n_failed, time.perf_counter() - t_cov,
    )

    # QP 优化主循环
    opt_weights:    dict = {}
    baseline_weights: dict = {}
    meta_rows: list[dict] = []
    w_prev: pd.Series | None = None
    fb_counts: dict = {0: 0, 1: 0, 2: 0}

    t_opt = time.perf_counter()
    for i, T in enumerate(available_dates):
        codes = list(benchmark_weights_dict[T].index)
        n     = len(codes)

        alpha = composite_signal.loc[T].reindex(codes).fillna(0.0) \
            if T in composite_signal.index else pd.Series(0.0, index=codes)

        cov_available = T in cov_cache
        if cov_available:
            cov = cov_cache[T]
        else:
            log.warning("%s 无协方差缓存，降级 L2（F6-002）", T.date())
            cov = np.eye(n) * 1e-4

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
            config         = _opt_config,
        )

        effective_fb     = result.fallback_level
        effective_status = result.solver_status
        if not cov_available and result.fallback_level == 0:
            effective_fb     = 1
            effective_status = result.solver_status + "+cov_missing"

        opt_weights[T]      = result.weights
        w_prev              = result.weights
        baseline_weights[T] = _compute_baseline(alpha, halt_dict[T], codes, topn=_opt_config.topn)
        fb_counts[effective_fb] = fb_counts.get(effective_fb, 0) + 1

        meta_rows.append({
            "rebalance_date":      T,
            "fallback_level":      effective_fb,
            "solver_status":       effective_status,
            "solve_time_s":        result.solve_time_s,
            "cov_available":       cov_available,
            "w_prev_source":       "target_weight",
            "constraint_compliant": result.constraint_compliant,  # F6-001
        })

        if (i + 1) % 10 == 0 or (i + 1) == len(available_dates):
            log.info("优化进度: %d/%d  用时: %.1fs", i + 1, len(available_dates),
                     time.perf_counter() - t_opt)

    total_elapsed = time.perf_counter() - t_opt
    log.info(
        "优化完成: %d 期  用时=%.1fs  Fallback: L1=%d  L2=%d  L3=%d",
        len(available_dates), total_elapsed,
        fb_counts.get(0, 0), fb_counts.get(1, 0), fb_counts.get(2, 0),
    )

    # 组装 DataFrame
    weights_panel  = pd.DataFrame(opt_weights).T.fillna(0.0)
    baseline_panel = pd.DataFrame(baseline_weights).T.fillna(0.0)
    weights_panel.index.name  = "rebalance_date"
    baseline_panel.index.name = "rebalance_date"
    weights_panel.columns  = weights_panel.columns.astype(str)
    baseline_panel.columns = baseline_panel.columns.astype(str)

    weights_panel.to_parquet(_w_opt)
    baseline_panel.to_parquet(_w_bl)

    meta_df = pd.DataFrame(meta_rows).set_index("rebalance_date")
    meta_df.index = pd.DatetimeIndex(meta_df.index)
    meta_df.to_parquet(_w_meta)

    n_cov_missing = int((~meta_df["cov_available"]).sum())
    out_label = str(output_dir) if output_dir else str(cfg.DATA_PROC)
    log.info(
        "权重已写入: opt=%s  baseline=%s  meta=%s  cov缺失期=%d",
        weights_panel.shape, baseline_panel.shape, meta_df.shape, n_cov_missing,
    )
    log.info("=" * 60)
    log.info("组合优化完成，输出到 %s", out_label)
    log.info("=" * 60)


def _compute_baseline(
    alpha: pd.Series,
    halt_codes: set,
    codes: list[str],
    topn: int = 50,
) -> pd.Series:
    """TopN 等权基准权重（排除停牌股）。"""
    avail   = alpha.drop(index=list(halt_codes & set(alpha.index)), errors="ignore")
    top_sel = avail.dropna().nlargest(topn).index
    w_bl    = pd.Series(0.0, index=codes)
    if len(top_sel) > 0:
        w_bl[top_sel] = 1.0 / len(top_sel)
    return w_bl


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="训练/验证期组合优化")
    parser.add_argument(
        "--rebuild-cov", action="store_true",
        help="强制重新估计所有协方差（忽略已有缓存）",
    )
    parser.add_argument(
        "--signal-path", type=Path, default=None,
        help="合成信号 parquet 路径；不传则使用 data/processed/composite_signal_ic_ir.parquet",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="run 根目录；不传则输出到 data/processed/（旧行为）",
    )
    parser.add_argument(
        "--cov-cache-dir", type=Path, default=None,
        help="协方差缓存目录；不传则使用 data/processed/cov_cache/",
    )
    args = parser.parse_args()
    main(
        rebuild_cov=args.rebuild_cov,
        signal_path=args.signal_path,
        output_dir=args.output_dir,
        cov_cache_dir=args.cov_cache_dir,
    )
