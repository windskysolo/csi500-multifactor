"""
scripts/run_signal_combination.py — 训练/验证期合成信号构建
============================================================

功能：
  1. 读取因子面板（data/processed/factor_panels/*.parquet）
  2. 读取 final_factors.json 确定入模因子列表
  3. 计算各因子 IC 序列
  4. 构建等权合成信号和 IC_IR 加权合成信号（仅 TRAIN_START ~ VALID_END）
  5. 写入公共路径，附带可追溯 metadata

输出：
  data/processed/composite_signal_equal.parquet
  data/processed/composite_signal_ic_ir.parquet
  data/processed/ic_series_all_factors.parquet
  data/processed/icir_weight_history.parquet
  data/processed/composite_signal_metadata.json

注意：
  - 只覆盖 TRAIN_START ~ VALID_END，绝不写入测试集产物。
  - 测试集合成信号由 scripts/run_test_pipeline.py 写入独立目录。

用法：
  python -m scripts.run_signal_combination
  python -m scripts.run_signal_combination --recompute-fwd-ret
"""

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from src.evaluation.ic_analysis import compute_ic_series, compute_forward_returns
from src.signal.combiner import (
    build_composite_panel,
    validate_directions_vs_summary,
    DEFAULT_WINDOW_MONTHS,
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
PANEL_DIR          = cfg.DATA_PROC / "factor_panels"
FWD_CACHE          = cfg.DATA_PROC / "fwd_ret_panel.parquet"
FWD_META_PATH      = cfg.DATA_PROC / "fwd_ret_panel.meta.json"
SIGNAL_EQUAL_PATH  = cfg.DATA_PROC / "composite_signal_equal.parquet"
SIGNAL_IC_IR_PATH  = cfg.DATA_PROC / "composite_signal_ic_ir.parquet"
IC_SERIES_PATH     = cfg.DATA_PROC / "ic_series_all_factors.parquet"
WEIGHT_HIST_PATH   = cfg.DATA_PROC / "icir_weight_history.parquet"
METADATA_PATH      = cfg.DATA_PROC / "composite_signal_metadata.json"

EVAL_DIR           = _ROOT / "reports" / "factor_evaluation"
INVALIDATED_PATH   = EVAL_DIR / "INVALIDATED.md"
EVAL_JSON_PATH     = EVAL_DIR / "final_factors.json"
FACTOR_SUMMARY_CSV = EVAL_DIR / "factor_summary.csv"

MIN_VALID_COUNT    = cfg.SIGNAL_MIN_VALID_FACTORS


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_ROOT), text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _load_factor_panels() -> dict[str, pd.DataFrame]:
    """加载所有因子面板并按 final_factors.json 过滤。"""
    if INVALIDATED_PATH.exists():
        log.error(
            "评估报告已标记失效（%s 存在）。"
            "请先重跑 run_factor_evaluation.py 修复上游数据后再构建信号。",
            INVALIDATED_PATH,
        )
        sys.exit(1)

    factor_files = sorted(PANEL_DIR.glob("*.parquet"))
    if not factor_files:
        raise FileNotFoundError(f"未找到因子面板: {PANEL_DIR}")

    all_panels: dict[str, pd.DataFrame] = {}
    for f in factor_files:
        all_panels[f.stem] = pd.read_parquet(f)
    log.info("读取因子面板: %d 个因子", len(all_panels))

    # 按 final_factors.json 筛选
    selected_factors: list[str] = []
    stability_weights: dict | None = None
    if EVAL_JSON_PATH.exists():
        with open(EVAL_JSON_PATH, encoding="utf-8") as f:
            eval_data = json.load(f)
        selected_factors  = eval_data["final_factors"]
        stability_weights = eval_data.get("stability_weights")
    else:
        log.warning("final_factors.json 不存在，使用全部因子面板")
        selected_factors = list(all_panels.keys())

    # 一致性校验
    if FACTOR_SUMMARY_CSV.exists() and EVAL_JSON_PATH.exists():
        summary_df = pd.read_csv(FACTOR_SUMMARY_CSV, index_col=0)
        if "final_include" in summary_df.columns:
            summary_set = set(summary_df[summary_df["final_include"]].index.tolist())
            final_set   = set(selected_factors)
            if summary_set != final_set:
                diff = summary_set.symmetric_difference(final_set)
                raise RuntimeError(
                    f"因子集合不一致：factor_summary.csv 与 final_factors.json 差异 {diff}。"
                    "请重新运行 run_factor_evaluation.py。"
                )
        mismatches = validate_directions_vs_summary(summary_df)
        if mismatches:
            log.warning("FACTOR_DIRECTIONS 方向不一致: %s（已记录，不中断）", mismatches)

    factor_panels = {k: v for k, v in all_panels.items() if k in selected_factors}
    log.info("使用因子: %d 个 → %s", len(factor_panels), selected_factors)
    return factor_panels, stability_weights


def _load_or_compute_fwd_ret(
    factor_panels: dict[str, pd.DataFrame],
    recompute: bool,
) -> pd.DataFrame:
    """读取 fwd_ret_panel.parquet，若缺少 sidecar 或 --recompute-fwd-ret 则重算。"""
    from src.data.universe import get_investable_universe

    # 收集训练/验证期调仓日
    all_dates = sorted(set().union(*[set(p.index) for p in factor_panels.values()]))
    tv_dates  = [d for d in all_dates if d <= cfg.VALID_END]

    if FWD_CACHE.exists() and not recompute:
        if not FWD_META_PATH.exists():
            # F4-003: sidecar 缺失时拒绝加载，避免不可追溯的旧缓存进入生产流程
            raise RuntimeError(
                f"fwd_ret_panel 缓存存在但无 metadata sidecar（{FWD_META_PATH}），"
                "无法校验配置一致性。请使用 --recompute-fwd-ret 参数强制重算并生成 metadata。"
            )
        existing = pd.read_parquet(FWD_CACHE)
        # 检查训练/验证期是否已覆盖
        tv_date_set = set(tv_dates[:-1])  # 最后一期无 forward return
        missing = tv_date_set - set(existing.index)
        if not missing:
            log.info("fwd_ret_panel 缓存命中（%s ~ %s）", existing.index[0].date(), existing.index[-1].date())
            return existing.loc[[d for d in existing.index if d <= cfg.VALID_END]]
        log.info("缓存缺少 %d 个调仓日，部分重算", len(missing))

    # 计算所有训练/验证期的 forward return
    log.info("计算 forward return（%d 个调仓日）...", len(tv_dates))
    codes_by_date: dict = {}
    for T in tv_dates[:-1]:
        try:
            codes_by_date[T] = get_investable_universe(T).tolist()
        except Exception as e:
            log.warning("%s 可投资域失败: %s", T.date(), e)
            codes_by_date[T] = []

    all_codes = sorted(set().union(*codes_by_date.values())) if codes_by_date else []
    fwd = compute_forward_returns(
        rebalance_dates=tv_dates,
        codes=all_codes,
        codes_by_date=codes_by_date,
    )
    fwd = fwd.reindex(tv_dates[:-1])
    fwd.to_parquet(FWD_CACHE)
    log.info("fwd_ret_panel 已写入: %s  %s ~ %s", fwd.shape, fwd.index[0].date(), fwd.index[-1].date())

    # 写入 sidecar metadata
    fwd_hash = hashlib.md5(fwd.to_csv(index=False).encode()).hexdigest()[:12]
    meta = {
        "start":                str(fwd.index[0].date()),
        "end":                  str(fwd.index[-1].date()),
        "n_dates":              len(fwd),
        "rebalance_dates_hash": fwd_hash,
        "benchmark_mode":       "H00905.CSI 全收益",
        "source_commit":        _git_commit(),
        "generated_at":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    FWD_META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return fwd


def main(recompute_fwd_ret: bool = False) -> None:
    log.info("=" * 60)
    log.info("合成信号构建（训练/验证期 %s ~ %s）", cfg.TRAIN_START.date(), cfg.VALID_END.date())
    log.info("=" * 60)

    factor_panels, stability_weights = _load_factor_panels()

    # 获取训练/验证期调仓日
    all_dates = sorted(set().union(*[set(p.index) for p in factor_panels.values()]))
    tv_dates  = [d for d in all_dates if d <= cfg.VALID_END]
    log.info("训练/验证期调仓日: %d 期  %s ~ %s", len(tv_dates), tv_dates[0].date(), tv_dates[-1].date())

    # 加载 fwd_ret_panel（仅训练/验证期）
    fwd_ret_panel = _load_or_compute_fwd_ret(factor_panels, recompute_fwd_ret)

    # 计算各因子 IC 序列
    log.info("计算各因子 IC 序列...")
    ic_series_map: dict = {}
    for name, panel in factor_panels.items():
        tv_panel = panel.loc[[d for d in panel.index if d <= cfg.VALID_END]]
        ic_series_map[name] = compute_ic_series(tv_panel, fwd_ret_panel)

    # 构建等权合成信号
    log.info("构建等权合成信号...")
    composite_equal = build_composite_panel(
        factor_panels     = {k: v.loc[[d for d in v.index if d <= cfg.VALID_END]]
                             for k, v in factor_panels.items()},
        ic_series_map     = ic_series_map,
        rebalance_dates   = tv_dates,
        method            = "equal",
        window_months     = DEFAULT_WINDOW_MONTHS,
        min_valid_factors = MIN_VALID_COUNT,
    )

    # 构建 IC_IR 加权合成信号
    log.info("构建 IC_IR 加权合成信号...")
    composite_ic_ir, diagnostics = build_composite_panel(
        factor_panels     = {k: v.loc[[d for d in v.index if d <= cfg.VALID_END]]
                             for k, v in factor_panels.items()},
        ic_series_map     = ic_series_map,
        rebalance_dates   = tv_dates,
        method            = "ic_ir",
        window_months     = DEFAULT_WINDOW_MONTHS,
        min_valid_factors = MIN_VALID_COUNT,
        stability_weights = stability_weights,
        return_diagnostics = True,
    )

    # 强制列名 str
    composite_equal.columns  = composite_equal.columns.astype(str)
    composite_ic_ir.columns  = composite_ic_ir.columns.astype(str)

    # 落盘信号
    composite_equal.to_parquet(SIGNAL_EQUAL_PATH)
    composite_ic_ir.to_parquet(SIGNAL_IC_IR_PATH)
    log.info("等权信号: %s  IC_IR 信号: %s", composite_equal.shape, composite_ic_ir.shape)

    # IC 序列
    ic_df = pd.DataFrame(ic_series_map)
    ic_df.index.name = "rebalance_date"
    ic_df.to_parquet(IC_SERIES_PATH)

    # IC_IR 权重历史
    weight_history = diagnostics.get("weight_history", {})
    if weight_history:
        wh_df = pd.DataFrame(weight_history).T.rename_axis("rebalance_date")
        wh_df.index = pd.DatetimeIndex(wh_df.index)
        wh_df.to_parquet(WEIGHT_HIST_PATH)
        log.info("权重历史: %s", wh_df.shape)

    # 生成可追溯 metadata
    ic_hash     = hashlib.md5(ic_df.to_csv(index=False).encode()).hexdigest()[:12]
    signal_hash = hashlib.md5(composite_ic_ir.to_csv(index=False).encode()).hexdigest()[:12]
    metadata = {
        "run_id":             datetime.now().strftime("%Y%m%d_%H%M%S"),
        "generated_at":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit":         _git_commit(),
        "pipeline":           "training_validation",
        "method":             "ic_ir",
        "window_months":      DEFAULT_WINDOW_MONTHS,
        "min_valid_factors":  MIN_VALID_COUNT,
        "factor_list":        sorted(factor_panels.keys()),
        "n_factors":          len(factor_panels),
        "n_rebalance_dates":  len(tv_dates),
        "date_range":         [str(tv_dates[0].date()), str(tv_dates[-1].date())],
        "cold_start_count":   diagnostics.get("cold_start_count", 0),
        "ic_series_hash":     ic_hash,
        "signal_ic_ir_hash":  signal_hash,
    }
    METADATA_PATH.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log.info("=" * 60)
    log.info("合成信号构建完成，输出到 %s", cfg.DATA_PROC)
    log.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="训练/验证期合成信号构建")
    parser.add_argument(
        "--recompute-fwd-ret",
        action="store_true",
        help="强制重算 forward return 并生成 sidecar metadata",
    )
    args = parser.parse_args()
    main(recompute_fwd_ret=args.recompute_fwd_ret)
