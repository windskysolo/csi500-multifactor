"""scripts/backfill_signal_quality.py

对没有 signal_quality_report.json 的已有 run 补跑 TC 诊断。
信号来源优先使用 run 自身的 composite.parquet；若 signal/ 为空（--from-stage portfolio 产生），
则从 inputs.lock.json 读取源路径。

运行方式：
    python scripts/backfill_signal_quality.py

产物写入：runs/train_valid/<run_id>/reports/signal_quality_report.json
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import pandas as pd

from src import config as cfg
from src.evaluation.signal_quality import SignalToPositionDiagnostics

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

RUNS_ROOT = Path("runs/train_valid")
FWD_RET_PATH = Path(cfg.DATA_PROC) / "fwd_ret_panel.parquet"

# ── 待回填的 run_id 列表（按信号分组排列）────────────────────────────────────
# 说明：frozen_baseline 已有报告，运行时会自动跳过（不重复计算）
BACKFILL_RUN_IDS: list[str] = [
    # Group A: ICIR expanding 信号（sha256=23024863）
    # 冻结基线已有，此处包含以便跳过逻辑生效并在日志中确认
    "20260529_034947__frozen_baseline_icir_topn50_ew",   # ✅ 已有，跳过
    "20260528_133249__baseline_icir_te6_lam0050",         # QP TE=6%
    "20260529_145209__icir_l2_forced",                    # L2 forced
    "20260529_145838__icir_te10_lam0050",                 # QP TE=10%
    "20260529_150439__icir_te15_lam0050",                 # QP TE=15%
    "20260529_151545__icir_te6_noind",                    # QP 无行业约束
    "20260529_150951__icir_te6_ind5pct",                  # QP 行业±5%
    # Group B: Expanding Ridge 信号（sha256=40cb47bc）
    "20260527_141545__baseline_expanding_ridge_te6_lam0050",  # QP TE=6%
    "20260529_081253__expanding_ridge_topn50_ew",             # TopN50 EW
    # Group C: Rolling48 Ridge 信号（sha256=a1e6893b）
    "20260527_142056__challenger_rolling48_te6_lam0050",      # QP TE=6%
    "20260529_081232__rolling48_ridge_topn50_ew",             # TopN50 EW
    # Group D: Decay HL24 Ridge 信号（sha256=84abd1c3）
    "20260528_141041__challenger_decay_ridge_hl24_te6_lam0050",  # QP TE=6%
    "20260529_081310__decay_hl24_ridge_topn50_ew",               # TopN50 EW
]


def _resolve_signal_path(run_dir: Path) -> Path:
    """
    解析 run 的信号文件路径。

    优先级：
      1. run 自身的 signal/composite.parquet（原始跑出信号的 run）
      2. inputs.lock.json["input_signal"]["path"]（--from-stage portfolio 的 run）

    Raises:
        FileNotFoundError: 两种来源均不可用时
    """
    own_signal = run_dir / "signal" / "composite.parquet"
    if own_signal.exists():
        return own_signal

    lock_path = run_dir / "inputs.lock.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        src_str = lock.get("input_signal", {}).get("path")
        if src_str:
            src = Path(src_str)
            if not src.exists():
                raise FileNotFoundError(
                    f"inputs.lock.json 指向的信号文件不存在（路径可能因项目目录变更而失效）:\n"
                    f"  lock 路径: {lock_path}\n"
                    f"  信号路径: {src}"
                )
            return src

    raise FileNotFoundError(
        f"无法确定 {run_dir.name} 的信号来源：\n"
        f"  - signal/composite.parquet 不存在\n"
        f"  - inputs.lock.json 缺失或无 input_signal 字段"
    )


def _fmt(v: float, fmt: str) -> str:
    """格式化浮点数，NaN 返回 'N/A'。"""
    if math.isnan(v):
        return "N/A"
    return format(v, fmt)


def backfill_one(run_id: str, fwd_ret: pd.DataFrame) -> bool:
    """
    对单个 run 生成 signal_quality_report.json。

    Returns:
        True 表示成功（含跳过），False 表示失败
    """
    run_dir = RUNS_ROOT / run_id
    out_path = run_dir / "reports" / "signal_quality_report.json"

    if out_path.exists():
        log.info("跳过（已有）: %s", run_id)
        return True

    if not run_dir.exists():
        log.error("run 目录不存在，跳过: %s", run_id)
        return False

    try:
        signal_path = _resolve_signal_path(run_dir)
        weights_path = run_dir / "portfolio" / "target_weights.parquet"

        if not weights_path.exists():
            log.warning("缺少 target_weights.parquet，跳过: %s", run_id)
            return False

        signals = pd.read_parquet(signal_path)
        weights = pd.read_parquet(weights_path)
        signals.index = pd.DatetimeIndex(signals.index)
        weights.index = pd.DatetimeIndex(weights.index)

        diag = SignalToPositionDiagnostics(min_stocks=10)
        report = diag.run(
            signals=signals,
            weights=weights,
            returns=fwd_ret,
            valid_start=pd.Timestamp(cfg.VALID_START),
            valid_end=pd.Timestamp(cfg.VALID_END),
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(
            "✓ %s  TC=%s  truncation=%s  ir_loss=%s",
            run_id,
            _fmt(report.tc, ".3f"),
            _fmt(report.truncation_loss * 100, ".1f") + "%",
            _fmt(report.ir_loss_pct, ".1f") + "%",
        )
        return True

    except FileNotFoundError as e:
        log.error("✗ %s — 文件不存在:\n  %s", run_id, e)
        return False
    except Exception as e:
        log.error("✗ %s — 意外错误: %s", run_id, e, exc_info=True)
        return False


def main() -> None:
    if not FWD_RET_PATH.exists():
        raise FileNotFoundError(f"前瞻收益面板不存在: {FWD_RET_PATH}")

    log.info("加载前瞻收益面板: %s", FWD_RET_PATH)
    fwd_ret = pd.read_parquet(FWD_RET_PATH)
    fwd_ret.index = pd.DatetimeIndex(fwd_ret.index)
    log.info("面板加载完成，共 %d 期", len(fwd_ret))

    total = len(BACKFILL_RUN_IDS)
    ok = sum(backfill_one(rid, fwd_ret) for rid in BACKFILL_RUN_IDS)
    log.info("完成 %d/%d（含已有跳过）", ok, total)


if __name__ == "__main__":
    main()
