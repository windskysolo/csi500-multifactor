"""
scripts/run_backtest.py — 验证期策略回测
=========================================

功能：
  1. 读取组合权重（portfolio_weights_optimized.parquet、portfolio_weights_baseline.parquet）
  2. 对验证期（VALID_START ~ VALID_END）运行完整回测
  3. 写入 NAV、指标、交易记录、实际权重

旧用法（输出到 data/processed/，行为不变）：
  python -m scripts.run_backtest

新用法（输出到 run 目录，产物遵循 artifact contract）：
  python -m scripts.run_backtest \\
      --weights-path          runs/train_valid/<run_id>/portfolio/target_weights.parquet \\
      --baseline-weights-path runs/train_valid/<run_id>/portfolio/baseline_weights.parquet \\
      --weights-meta-path     runs/train_valid/<run_id>/portfolio/optimizer_meta.parquet \\
      --output-dir            runs/train_valid/<run_id>
  # 输出：<output-dir>/backtest/nav_valid.parquet 等
"""

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from src.backtest.engine import BacktestConfig, run_backtest
from src.backtest.metrics import summarize

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 旧默认路径（不传 output_dir 时的兼容路径）
# ---------------------------------------------------------------------------
_DEFAULT_WEIGHTS_OPT  = cfg.DATA_PROC / "portfolio_weights_optimized.parquet"
_DEFAULT_WEIGHTS_BL   = cfg.DATA_PROC / "portfolio_weights_baseline.parquet"
_DEFAULT_WEIGHTS_META = cfg.DATA_PROC / "portfolio_weights_meta.parquet"

# 兼容旧脚本直接引用这些常量的情况
WEIGHTS_OPT_PATH  = _DEFAULT_WEIGHTS_OPT
WEIGHTS_BL_PATH   = _DEFAULT_WEIGHTS_BL
WEIGHTS_META_PATH = _DEFAULT_WEIGHTS_META
NAV_PATH          = cfg.DATA_PROC / "backtest_nav.parquet"
METRICS_PATH      = cfg.DATA_PROC / "backtest_metrics.parquet"
TRADES_V1_PATH    = cfg.DATA_PROC / "backtest_trades_v1.parquet"
TRADES_V2_PATH    = cfg.DATA_PROC / "backtest_trades_v2.parquet"
WEIGHTS_V1_PATH   = cfg.DATA_PROC / "backtest_weights_v1.parquet"
WEIGHTS_V2_PATH   = cfg.DATA_PROC / "backtest_weights_v2.parquet"


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_ROOT), text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _check_weight_compliance(
    allow_noncompliant: bool,
    weights_meta_path: Path,
    period_start: Optional[pd.Timestamp] = None,
    period_end: Optional[pd.Timestamp] = None,
) -> None:
    """读取 portfolio_weights_meta，在回测区间内存在 constraint_compliant=False 时失败或警告。

    Args:
        allow_noncompliant: True 则警告后继续，False 则中止。
        weights_meta_path:  optimizer_meta parquet 路径。
        period_start:       回测区间起点（含）；None 表示检查所有期。
        period_end:         回测区间终点（含）；None 表示检查所有期。
    """
    if not weights_meta_path.exists():
        log.warning("portfolio_weights_meta.parquet 不存在，跳过合规性检查")
        return

    meta = pd.read_parquet(weights_meta_path)
    if "constraint_compliant" not in meta.columns:
        log.warning("portfolio_weights_meta 无 constraint_compliant 列，跳过合规性检查")
        return

    # 只检查回测区间内的期，避免训练期 L3 违规误触发回测中止
    if period_start is not None or period_end is not None:
        mask = pd.Series(True, index=meta.index)
        if period_start is not None:
            mask &= meta.index >= period_start
        if period_end is not None:
            mask &= meta.index <= period_end
        n_out_of_range = (~mask).sum()
        meta_in_range = meta[mask]
        log.info(
            "权重合规性检查区间: %s ~ %s（共 %d 期，跳过区间外 %d 期）",
            period_start.date() if period_start else "earliest",
            period_end.date() if period_end else "latest",
            len(meta_in_range), n_out_of_range,
        )
    else:
        meta_in_range = meta

    noncompliant = meta_in_range[~meta_in_range["constraint_compliant"]]
    if noncompliant.empty:
        log.info("权重合规性检查通过：回测区间内 %d 期均合规", len(meta_in_range))
        return

    n_bad, n_total = len(noncompliant), len(meta_in_range)
    bad_dates = noncompliant.index.strftime("%Y-%m-%d").tolist()
    summary = (
        f"{n_bad}/{n_total} 期权重 constraint_compliant=False "
        f"（L3 TopN 等权，单股偏离超过 OPT_SINGLE_MAX_DEV={cfg.OPT_SINGLE_MAX_DEV:.1%}）。"
        f"\n  非合规日期: {bad_dates}"
    )

    if allow_noncompliant:
        log.warning("[--allow-noncompliant-weights 已启用] %s", summary)
        log.warning("继续回测，请在报告中披露该合规风险。")
    else:
        log.error("权重合规性检查失败：%s", summary)
        log.error(
            "回测已中止。若确认接受此风险，请添加 --allow-noncompliant-weights 参数，"
            "并在报告中披露。"
        )
        sys.exit(1)


def _annual_turnover_pct(trade_log: pd.DataFrame, n_months: int) -> float:
    """双边年化换手率（%）。"""
    if trade_log.empty or n_months == 0:
        return float("nan")
    total_to = (
        (trade_log["buy_value"] + trade_log["sell_value"])
        / trade_log["portfolio_value_before"]
    ).sum()
    return total_to / n_months * 12 * 100


def main(
    weights_path: Optional[Path] = None,
    baseline_weights_path: Optional[Path] = None,
    weights_meta_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    allow_noncompliant: bool = False,
) -> None:
    """
    Args:
        weights_path:           优化权重 parquet；None 使用旧默认路径。
        baseline_weights_path:  Baseline 权重 parquet；None 使用旧默认路径。
        weights_meta_path:      optimizer_meta parquet；None 使用旧默认路径。
        output_dir:             run 根目录；None 输出到 data/processed/（旧行为）。
        allow_noncompliant:     允许 constraint_compliant=False 的权重继续回测。
    """
    _w_opt  = weights_path          or _DEFAULT_WEIGHTS_OPT
    _w_bl   = baseline_weights_path or _DEFAULT_WEIGHTS_BL
    _w_meta = weights_meta_path     or _DEFAULT_WEIGHTS_META

    if output_dir is None:
        bt_dir       = cfg.DATA_PROC
        _nav_path    = bt_dir / "backtest_nav.parquet"
        _metrics     = bt_dir / "backtest_metrics.parquet"
        _trades_v1   = bt_dir / "backtest_trades_v1.parquet"
        _trades_v2   = bt_dir / "backtest_trades_v2.parquet"
        _weights_v1  = bt_dir / "backtest_weights_v1.parquet"
        _weights_v2  = bt_dir / "backtest_weights_v2.parquet"
    else:
        bt_dir = output_dir / "backtest"
        bt_dir.mkdir(parents=True, exist_ok=True)
        _nav_path   = bt_dir / "nav_valid.parquet"
        _metrics    = bt_dir / "metrics_valid.parquet"
        _trades_v1  = bt_dir / "trades_baseline.parquet"
        _trades_v2  = bt_dir / "trades_valid.parquet"
        _weights_v1 = bt_dir / "actual_weights_baseline.parquet"
        _weights_v2 = bt_dir / "actual_weights_valid.parquet"

    log.info("=" * 60)
    log.info("验证期回测（%s ~ %s）", cfg.VALID_START.date(), cfg.VALID_END.date())
    log.info("=" * 60)

    for p, label in [(_w_opt, "优化权重"), (_w_bl, "Baseline 权重")]:
        if not p.exists():
            log.error("%s 文件不存在：%s\n请先运行 run_portfolio_optimization.py", label, p)
            sys.exit(1)

    weights_v2 = pd.read_parquet(_w_opt)
    weights_v1 = pd.read_parquet(_w_bl)
    log.info("读取权重: V1=%s  V2=%s", weights_v1.shape, weights_v2.shape)

    # F6-001: 检查回测区间内的权重合规性；训练期 L3 违规不影响验证期回测
    _check_weight_compliance(
        allow_noncompliant,
        weights_meta_path=_w_meta,
        period_start=cfg.VALID_START,
        period_end=cfg.VALID_END,
    )

    config = BacktestConfig(initial_value=1.0)

    log.info("运行 V1 Baseline 回测...")
    result_v1 = run_backtest(weights_v1, cfg.VALID_START, cfg.VALID_END, config)

    log.info("运行 V2 优化权重回测...")
    result_v2 = run_backtest(weights_v2, cfg.VALID_START, cfg.VALID_END, config)

    n_months = len(result_v2.trade_log) if not result_v2.trade_log.empty else 0

    # NAV
    nav_df = pd.DataFrame({
        "strategy_v1": result_v1.nav,
        "strategy_v2": result_v2.nav,
        "benchmark":   result_v1.benchmark_nav,
    }).dropna()
    nav_df.index.name = "trade_date"
    nav_df.to_parquet(_nav_path)

    # 指标
    metrics_raw = pd.DataFrame({
        "v1": pd.Series(result_v1.metrics),
        "v2": pd.Series(result_v2.metrics),
    })
    metrics_raw.index.name = "metric"
    metrics_raw.to_parquet(_metrics)

    # 交易记录和实际权重
    for res, path_t, path_w in [
        (result_v1, _trades_v1, _weights_v1),
        (result_v2, _trades_v2, _weights_v2),
    ]:
        if not res.trade_log.empty:
            res.trade_log.to_parquet(path_t)
        if not res.actual_weights.empty:
            res.actual_weights.to_parquet(path_w)

    # 打印验证集指标
    print()
    print("=" * 70)
    print(f"验证期回测结果（{result_v1.nav.index[0].date()} → {result_v1.nav.index[-1].date()}）")
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

    # 硬指标检查
    m2    = result_v2.metrics
    to_v2 = _annual_turnover_pct(result_v2.trade_log, n_months)
    print("硬指标检查（目标：IR≥0.5，超额最大回撤≤10%，年化换手500-1500%）：")
    _check("IR ≥ 0.5",          m2["information_ratio"] >= 0.5,        f"{m2['information_ratio']:.3f}")
    _check("超额最大回撤 ≤ 10%", abs(m2["excess_max_drawdown"]) <= 0.10, f"{abs(m2['excess_max_drawdown'])*100:.2f}%")
    _check("年化双边换手 500-1500%", 500 <= to_v2 <= 1500,              f"{to_v2:.0f}%")
    print()

    out_label = str(output_dir) if output_dir else str(cfg.DATA_PROC)
    log.info("=" * 60)
    log.info("验证期回测完成，输出到 %s", out_label)
    log.info("=" * 60)


def _check(label: str, passed: bool, value: str) -> None:
    icon = "PASS" if passed else "FAIL"
    print(f"  [{icon}] {label}: {value}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="验证期策略回测")
    parser.add_argument("--valid-only", action="store_true", default=True,
                        help="只回测验证期（默认行为）")
    parser.add_argument(
        "--allow-noncompliant-weights", action="store_true", default=False,
        help="允许使用 constraint_compliant=False 的权重，运行时记录 WARNING",
    )
    parser.add_argument(
        "--weights-path", type=Path, default=None,
        help="优化权重 parquet；不传则使用 data/processed/portfolio_weights_optimized.parquet",
    )
    parser.add_argument(
        "--baseline-weights-path", type=Path, default=None,
        help="Baseline 权重 parquet；不传则使用 data/processed/portfolio_weights_baseline.parquet",
    )
    parser.add_argument(
        "--weights-meta-path", type=Path, default=None,
        help="optimizer_meta parquet；不传则使用 data/processed/portfolio_weights_meta.parquet",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="run 根目录；不传则输出到 data/processed/（旧行为）",
    )
    args = parser.parse_args()
    main(
        weights_path=args.weights_path,
        baseline_weights_path=args.baseline_weights_path,
        weights_meta_path=args.weights_meta_path,
        output_dir=args.output_dir,
        allow_noncompliant=args.allow_noncompliant_weights,
    )
