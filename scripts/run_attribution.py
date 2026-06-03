"""
scripts/run_attribution.py — 验证期业绩归因
=============================================

功能：
  1. 读取回测实际权重（backtest_weights_v2.parquet）
  2. 运行 Brinson BHB 行业归因（配置效应、选股效应、交叉效应）
  3. 运行因子收益归因（各因子组对超额收益的贡献）
  4. 写入归因结果，并生成 reconciliation 表和 metadata JSON（F8-007/F8-008）

输出（公共路径，仅验证期）：
  data/processed/brinson_attribution.parquet             行业归因详细（期间 × 行业）
  data/processed/brinson_period_summary.parquet          行业归因汇总（按月）
  data/processed/factor_attribution.parquet              因子归因详细（期间 × 因子组）
  data/processed/factor_attr_period_summary.parquet      因子归因汇总（按月）
  data/processed/attribution_nav_reconciliation.parquet  归因收益与 NAV 的 reconciliation（F8-008）
  data/processed/attribution_metadata.json               归因元数据与运行配置（F8-007）

注意：
  - 本脚本只归因 VALID_START ~ VALID_END，不触碰测试集。
  - 归因口径与回测引擎 T+1 开盘执行假设一致（F8-001/F8-002）。
  - F9-002: Brinson 或因子归因失败时退出非零码。
    若上游数据不完整但仍需查看部分结果，传入 --allow-partial 跳过失败的子归因。

用法：
  python -m scripts.run_attribution
  python -m scripts.run_attribution --allow-partial   # 允许部分归因失败（不推荐）
"""

import argparse
import hashlib
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
from src.attribution.brinson import compute_brinson_attribution
from src.attribution.factor_attr import compute_factor_attribution

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 旧默认路径（不传 output_dir 时的兼容路径）
# ---------------------------------------------------------------------------
_DEFAULT_ACTUAL_WEIGHTS = cfg.DATA_PROC / "backtest_weights_v2.parquet"
_DEFAULT_NAV_PATH       = cfg.DATA_PROC / "backtest_nav.parquet"

# 兼容旧脚本直接引用这些常量的情况
ACTUAL_WEIGHTS_PATH      = _DEFAULT_ACTUAL_WEIGHTS
BRINSON_PATH             = cfg.DATA_PROC / "brinson_attribution.parquet"
BRINSON_SUMMARY_PATH     = cfg.DATA_PROC / "brinson_period_summary.parquet"
FACTOR_ATTR_PATH         = cfg.DATA_PROC / "factor_attribution.parquet"
FACTOR_ATTR_SUMMARY_PATH = cfg.DATA_PROC / "factor_attr_period_summary.parquet"
RECONCILIATION_PATH      = cfg.DATA_PROC / "attribution_nav_reconciliation.parquet"
METADATA_PATH            = cfg.DATA_PROC / "attribution_metadata.json"
NAV_PATH                 = _DEFAULT_NAV_PATH


def _print_brinson_summary(period_summary: pd.DataFrame) -> None:
    """打印 Brinson 归因汇总表。"""
    print()
    print("=" * 70)
    print("Brinson BHB 行业归因汇总（验证期，月度）")
    print("=" * 70)
    cols = ["strategy_return", "benchmark_return", "excess_return",
            "allocation_effect", "selection_effect",
            "interaction_effect", "total_effect"]
    cols = [c for c in cols if c in period_summary.columns]
    display = period_summary[cols].copy()
    for c in display.columns:
        display[c] = display[c].map(lambda x: f"{x*100:.2f}%" if pd.notna(x) else "N/A")
    print(display.to_string())

    total_alloc  = period_summary.get("allocation_effect", pd.Series(0.0)).sum()
    total_select = period_summary.get("selection_effect", pd.Series(0.0)).sum()
    total_inter  = period_summary.get("interaction_effect", pd.Series(0.0)).sum()
    total_excess = period_summary.get("excess_return", pd.Series(0.0)).sum()
    print()
    print(f"累计超额收益: {total_excess*100:.2f}%")
    print(f"  配置效应:   {total_alloc*100:.2f}%")
    print(f"  选股效应:   {total_select*100:.2f}%")
    print(f"  交叉效应:   {total_inter*100:.2f}%")
    print()


def _print_factor_summary(factor_summary: pd.DataFrame) -> None:
    """打印因子归因汇总（各因子组累计贡献）。"""
    print()
    print("=" * 70)
    print("因子收益归因汇总（验证期，因子组维度）")
    print("=" * 70)
    group_cols = [c for c in ["value", "quality", "growth", "momentum",
                               "volatility", "liquidity", "fund_flow", "residual"]
                  if c in factor_summary.columns]
    if group_cols:
        sums = factor_summary[group_cols].sum()
        for g, v in sums.items():
            print(f"  {g:<15}: {v*100:+.2f}%")
    print()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_ROOT), text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _file_sha256(path: Path) -> str:
    """计算文件的 SHA-256（前 8 位）用于产物可追溯性。"""
    if not path.exists():
        return "missing"
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:8]


def _compute_nav_reconciliation(
    brinson_summary: pd.DataFrame,
    nav_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    对比 Brinson 归因每期收益与回测 NAV 的月度收益。

    discrepancy = nav_strategy_return - brinson_strategy_return
    预期差异来源：
      1. 交易成本拖累（brinson 不含成本）
      2. 现金拖累（brinson strategy_return 只计股票部分，cash × 0 = 0）
    若 discrepancy > 0 则可能存在口径不一致，需人工排查。

    Args:
        brinson_summary: compute_brinson_attribution 的 period_summary 输出
    Returns:
        index=period_start 的 DataFrame，每行包含双侧收益和差异
    """
    _nav = nav_path or _DEFAULT_NAV_PATH
    if not _nav.exists():
        log.warning("backtest_nav.parquet 不存在，跳过 reconciliation 计算")
        return pd.DataFrame()

    nav_df = pd.read_parquet(_nav)
    nav_v2  = nav_df.get("strategy_v2")
    nav_bm  = nav_df.get("benchmark")
    if nav_v2 is None:
        log.warning("backtest_nav.parquet 缺少 strategy_v2 列，跳过 reconciliation")
        return pd.DataFrame()

    rows = []
    for period_start, row in brinson_summary.iterrows():
        period_end = row["period_end"]

        def _asof(series: pd.Series, ts: pd.Timestamp) -> float:
            """取 ts 当日或之前最近可用的 NAV 值。"""
            try:
                return float(series.asof(ts))
            except Exception:
                return float("nan")

        nav_start  = _asof(nav_v2, period_start)
        nav_end    = _asof(nav_v2, period_end)
        bm_start   = _asof(nav_bm, period_start) if nav_bm is not None else float("nan")
        bm_end     = _asof(nav_bm, period_end)   if nav_bm is not None else float("nan")

        nav_strat = nav_end / nav_start - 1.0 if nav_start > 1e-10 else float("nan")
        nav_bm_r  = bm_end  / bm_start  - 1.0 if bm_start  > 1e-10 else float("nan")
        nav_excess = nav_strat - nav_bm_r

        brinson_strat  = float(row.get("strategy_return", float("nan")))
        brinson_bm     = float(row.get("benchmark_return", float("nan")))
        brinson_excess = float(row.get("excess_return", float("nan")))

        rows.append({
            "period_start":           period_start,
            "period_end":             period_end,
            "nav_strategy_return":    nav_strat,
            "nav_benchmark_return":   nav_bm_r,
            "nav_excess_return":      nav_excess,
            "brinson_strategy_return": brinson_strat,
            "brinson_benchmark_return": brinson_bm,
            "brinson_excess_return":  brinson_excess,
            # strategy 差异主要来自成本拖累 + 现金拖累（应 ≤ 0 或接近 0）
            "strategy_discrepancy":   nav_strat - brinson_strat,
            "excess_discrepancy":     nav_excess - brinson_excess,
            "cash_weight":            float(row.get("cash_weight", float("nan"))),
        })

    return pd.DataFrame(rows).set_index("period_start")


def _write_attribution_metadata(
    brinson_ok:  bool,
    factor_ok:   bool,
    reconciliation: pd.DataFrame,
    output_paths: dict,
) -> None:
    """
    写入 attribution_metadata.json，记录归因口径、测试集运行次数、未修复风险。（F8-007）

    口径说明列于 metadata，让报告可以直接引用而不散落在代码中。
    """
    recon_max_disc = float("nan")
    if not reconciliation.empty and "excess_discrepancy" in reconciliation.columns:
        recon_max_disc = float(reconciliation["excess_discrepancy"].abs().max())

    known_unfixed_risks: list[str] = []
    if pd.notna(recon_max_disc) and recon_max_disc > 0.005:
        known_unfixed_risks.append(
            f"ATTR-RECON: 归因超额收益与 NAV 最大偏差 {recon_max_disc:.2%} > 0.5%；"
            "需继续核对成本、现金和 T+1 开盘执行口径。"
        )

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit":   _git_commit(),
        "backtest_start": str(cfg.VALID_START.date()),
        "backtest_end":   str(cfg.VALID_END.date()),
        "test_set_run_count": 0,           # 测试集（2023-2025）尚未运行
        "test_set_remaining": 3,           # 剩余可运行次数（上限 3）
        "attribution_config": {
            "execution_assumption": "T+1 open price (F8-001)",
            "weight_normalization": "none — cash_weight = 1 - sum(w_p) (F8-002)",
            "benchmark_loading":    "internal, full index member universe (F8-003)",
            "factor_list_source":   "final_factors.json (F8-004)",
            "factor_direction_source": "factor_summary.csv (F8-005)",
        },
        "output_files": {k: str(v) for k, v in output_paths.items()},
        "input_hashes": {
            "actual_weights": _file_sha256(output_paths.get("_actual_weights_input", Path())),
            "backtest_nav":   _file_sha256(output_paths.get("_nav_input", Path())),
        },
        "run_status": {
            "brinson_ok": brinson_ok,
            "factor_ok":  factor_ok,
            "reconciliation_max_excess_discrepancy": recon_max_disc,
        },
        "known_unfixed_risks": known_unfixed_risks,
    }

    meta_path = output_paths.get("_metadata_path", METADATA_PATH)
    meta_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("attribution_metadata.json 已写入: %s", meta_path)


def main(
    actual_weights_path: Optional[Path] = None,
    nav_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    allow_partial: bool = False,
) -> None:
    """
    Args:
        actual_weights_path: 实际持仓权重 parquet；None 使用旧默认路径。
        nav_path:            回测 NAV parquet（用于 reconciliation）；None 使用旧默认路径。
        output_dir:          run 根目录；None 输出到 data/processed/（旧行为）。
        allow_partial:       允许部分归因失败后继续（仅供调试）。
    """
    _actual = actual_weights_path or _DEFAULT_ACTUAL_WEIGHTS
    _nav    = nav_path            or _DEFAULT_NAV_PATH

    if output_dir is None:
        attr_dir        = cfg.DATA_PROC
        _brinson        = attr_dir / "brinson_attribution.parquet"
        _brinson_sum    = attr_dir / "brinson_period_summary.parquet"
        _factor_attr    = attr_dir / "factor_attribution.parquet"
        _factor_sum     = attr_dir / "factor_attr_period_summary.parquet"
        _reconciliation = attr_dir / "attribution_nav_reconciliation.parquet"
        _meta_path      = attr_dir / "attribution_metadata.json"
    else:
        attr_dir = output_dir / "attribution"
        attr_dir.mkdir(parents=True, exist_ok=True)
        _brinson        = attr_dir / "brinson_industry.parquet"
        _brinson_sum    = attr_dir / "brinson_period.parquet"
        _factor_attr    = attr_dir / "factor_attr_detail.parquet"
        _factor_sum     = attr_dir / "factor_attr_period.parquet"
        _reconciliation = attr_dir / "reconciliation.parquet"
        _meta_path      = attr_dir / "attribution_metadata.json"

    # 用于 metadata 写入的路径字典
    _paths = {
        "brinson_industry":    _brinson,
        "brinson_period":      _brinson_sum,
        "factor_attr_detail":  _factor_attr,
        "factor_attr_period":  _factor_sum,
        "reconciliation":      _reconciliation,
        "_actual_weights_input": _actual,
        "_nav_input":            _nav,
        "_metadata_path":        _meta_path,
    }

    log.info("=" * 60)
    log.info("验证期业绩归因（%s ~ %s）", cfg.VALID_START.date(), cfg.VALID_END.date())
    if allow_partial:
        log.warning("--allow-partial 已启用：子归因失败不中止脚本（仅供调试）")
    log.info("=" * 60)

    if not _actual.exists():
        log.error("实际权重文件不存在：%s\n请先运行 run_backtest.py", _actual)
        sys.exit(1)

    actual_weights = pd.read_parquet(_actual)
    log.info("读取实际权重: %s  %s ~ %s",
             actual_weights.shape,
             actual_weights.index[0].date(),
             actual_weights.index[-1].date())

    exit_code = 0

    # ── Brinson BHB 行业归因 ─────────────────────────────────────────────────
    log.info("运行 Brinson BHB 行业归因...")
    try:
        industry_attr, period_summary = compute_brinson_attribution(
            actual_weights = actual_weights,
            backtest_start = cfg.VALID_START,
            backtest_end   = cfg.VALID_END,
        )
        industry_attr.to_parquet(_brinson)
        period_summary.to_parquet(_brinson_sum)
        log.info("Brinson 归因完成: 行业归因 %s  期间汇总 %s",
                 industry_attr.shape, period_summary.shape)
        _print_brinson_summary(period_summary)
    except Exception as e:
        log.error("Brinson 归因失败: %s", e)
        if allow_partial:
            log.warning("--allow-partial: 跳过 Brinson 归因失败，继续因子归因")
        else:
            log.error("Brinson 归因失败且未传入 --allow-partial，退出码 1")
            sys.exit(1)

    # ── 因子收益归因 ──────────────────────────────────────────────────────────
    log.info("运行因子收益归因...")
    try:
        factor_attr, factor_period_summary = compute_factor_attribution(
            actual_weights = actual_weights,
            backtest_start = cfg.VALID_START,
            backtest_end   = cfg.VALID_END,
        )
        factor_attr.to_parquet(_factor_attr)
        factor_period_summary.to_parquet(_factor_sum)
        log.info("因子归因完成: 详细 %s  汇总 %s",
                 factor_attr.shape, factor_period_summary.shape)
        _print_factor_summary(factor_period_summary)
    except Exception as e:
        log.error("因子归因失败: %s", e)
        if allow_partial:
            log.warning("--allow-partial: 跳过因子归因失败")
            exit_code = 1
        else:
            log.error("因子归因失败且未传入 --allow-partial，退出码 1")
            sys.exit(1)

    # ── Reconciliation（F8-008）─────────────────────────────────────────────
    brinson_ok = _brinson_sum.exists()
    factor_ok  = _factor_sum.exists()

    reconciliation = pd.DataFrame()
    if brinson_ok:
        log.info("计算归因收益与 NAV 的 reconciliation 表...")
        try:
            period_summary_for_recon = pd.read_parquet(_brinson_sum)
            reconciliation = _compute_nav_reconciliation(period_summary_for_recon, nav_path=_nav)
            if not reconciliation.empty:
                reconciliation.to_parquet(_reconciliation)
                max_disc = reconciliation["excess_discrepancy"].abs().max()
                log.info("Reconciliation 完成：%d 期，超额收益最大偏差=%.4f%%",
                         len(reconciliation), max_disc * 100)
                if max_disc > 0.005:
                    log.warning("超额收益偏差 %.4f%% > 0.5%%，请检查口径（预期来源：成本/现金拖累）",
                                max_disc * 100)
        except Exception as e:
            log.warning("Reconciliation 计算失败（不阻断主流程）: %s", e)

    # ── 写入归因 metadata（F8-007）──────────────────────────────────────────
    try:
        _write_attribution_metadata(brinson_ok, factor_ok, reconciliation, _paths)
    except Exception as e:
        log.warning("attribution_metadata.json 写入失败（不阻断主流程）: %s", e)

    # ── 生成报告（F7-004）────────────────────────────────────────────────────
    try:
        import importlib
        report_mod = importlib.import_module("scripts.generate_backtest_report")
        report_mod.generate()
        log.info("验证期报告已生成")
    except Exception as e:
        log.warning("报告生成失败（不阻断主流程）: %s", e)

    out_label = str(output_dir) if output_dir else str(cfg.DATA_PROC)
    log.info("=" * 60)
    log.info("业绩归因完成，输出到 %s", out_label)
    log.info("=" * 60)

    if exit_code != 0:
        sys.exit(exit_code)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="验证期业绩归因")
    parser.add_argument(
        "--allow-partial", action="store_true",
        help="允许部分归因失败后继续（仅供调试；生产路径不应使用此选项）",
    )
    parser.add_argument(
        "--actual-weights-path", type=Path, default=None,
        help="实际持仓权重 parquet；不传则使用 data/processed/backtest_weights_v2.parquet",
    )
    parser.add_argument(
        "--nav-path", type=Path, default=None,
        help="回测 NAV parquet（用于 reconciliation）；不传则使用 data/processed/backtest_nav.parquet",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="run 根目录；不传则输出到 data/processed/（旧行为）",
    )
    args = parser.parse_args()
    main(
        actual_weights_path=args.actual_weights_path,
        nav_path=args.nav_path,
        output_dir=args.output_dir,
        allow_partial=args.allow_partial,
    )
