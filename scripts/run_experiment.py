"""
scripts/run_experiment.py — 统一实验入口（Phase 5）
====================================================

功能：
  按 spec 文件驱动完整实验流程：signal → portfolio → backtest
  每次运行生成不可变 run 目录，写入 manifest 和 RUN_FINISHED/RUN_FAILED。

用法：

  # 完整 pipeline
  python -m scripts.run_experiment \\
      --spec configs/pipelines/baseline_expanding_ridge_te6_lam0050.py

  # 从 portfolio 阶段开始（复用已有信号 run 的产物）
  python -m scripts.run_experiment \\
      --spec configs/pipelines/baseline_expanding_ridge_te6_lam0050.py \\
      --from-stage portfolio \\
      --input-signal-run 20260527_143000__baseline_expanding_ridge_te6_lam0050

  # 只跑 signal 阶段
  python -m scripts.run_experiment \\
      --spec configs/pipelines/baseline_expanding_ridge_te6_lam0050.py \\
      --to-stage signal

  # 指定 runs 根目录（默认 runs/）
  python -m scripts.run_experiment \\
      --spec configs/pipelines/... \\
      --runs-root /data/runs
"""

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.pipeline.artifacts import ArtifactLayout, file_sha256, REQUIRED_SIGNAL_ARTIFACTS
from src.pipeline.contracts import ExperimentSpec, RunContext

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_STAGE_ORDER = ["signal", "portfolio", "backtest"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一实验入口：按 spec 驱动 signal→portfolio→backtest")
    parser.add_argument(
        "--spec", required=True, type=Path,
        help="ExperimentSpec 配置文件路径，如 configs/pipelines/baseline_expanding_ridge_te6_lam0050.py",
    )
    parser.add_argument(
        "--from-stage", choices=_STAGE_ORDER, default="signal",
        help="从哪个阶段开始执行（默认 signal）",
    )
    parser.add_argument(
        "--to-stage", choices=_STAGE_ORDER, default="backtest",
        help="执行到哪个阶段为止（含，默认 backtest）",
    )
    parser.add_argument(
        "--input-signal-run", type=str, default=None,
        metavar="RUN_ID",
        help="跳过信号阶段时，指定已有信号 run 的 run_id（从该 run 读取 signal/composite.parquet）",
    )
    parser.add_argument(
        "--runs-root", type=Path, default=_ROOT / "runs",
        help="runs 根目录（默认 runs/）",
    )
    return parser.parse_args()


def _resolve_signal_path(
    from_stage: str,
    input_signal_run: Optional[str],
    run_dir: Path,
    runs_root: Path,
    scope: str,
) -> Path:
    """
    确定信号阶段的 composite.parquet 路径。
    - 若 from_stage == "signal"：写到本 run 的 signal 子目录（稍后由 stage 函数生成）
    - 否则：从 input_signal_run 指向的 run 中读取
    """
    if from_stage == "signal":
        return run_dir / "signal" / "composite.parquet"

    if not input_signal_run:
        raise ValueError(
            "--from-stage 不是 signal 时，必须通过 --input-signal-run 指定信号 run_id"
        )
    signal_run_dir = runs_root / scope / input_signal_run
    signal_path = signal_run_dir / "signal" / "composite.parquet"
    if not signal_path.exists():
        raise FileNotFoundError(
            f"信号 run 的 composite.parquet 不存在: {signal_path}\n"
            f"请检查 --input-signal-run '{input_signal_run}' 是否正确"
        )
    return signal_path


def _write_run_config(layout: ArtifactLayout, spec: ExperimentSpec, ctx: RunContext, args) -> None:
    config = {
        "run_id":        ctx.run_id,
        "experiment_id": spec.experiment_id,
        "scope":         ctx.scope,
        "started_at":    datetime.now(timezone.utc).isoformat(),
        "from_stage":    args.from_stage,
        "to_stage":      args.to_stage,
        "spec_file":     str(args.spec),
        "spec":          json.loads(spec.to_json()),
    }
    layout.run_config_path().write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_inputs_lock(
    layout: ArtifactLayout,
    signal_path: Optional[Path],
    from_stage: str,
) -> None:
    lock: dict = {"locked_at": datetime.now(timezone.utc).isoformat()}
    if from_stage != "signal" and signal_path and signal_path.exists():
        lock["input_signal"] = {
            "path":   str(signal_path),
            "sha256": file_sha256(signal_path),
        }
    layout.inputs_lock_path().write_text(
        json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_manifest(layout: ArtifactLayout, run_dir: Path) -> None:
    entries: dict = {}
    for p in sorted(run_dir.rglob("*.parquet")):
        rel = str(p.relative_to(run_dir))
        entries[rel] = {"sha256": file_sha256(p)}
    layout.write_manifest(entries)


def main() -> None:
    args = _parse_args()

    # 阶段范围
    try:
        from_idx = _STAGE_ORDER.index(args.from_stage)
        to_idx   = _STAGE_ORDER.index(args.to_stage)
    except ValueError as e:
        log.error("未知阶段: %s", e)
        sys.exit(1)
    if from_idx > to_idx:
        log.error("--from-stage (%s) 不能晚于 --to-stage (%s)", args.from_stage, args.to_stage)
        sys.exit(1)
    active_stages = _STAGE_ORDER[from_idx: to_idx + 1]

    # 加载 spec
    if not args.spec.exists():
        log.error("spec 文件不存在: %s", args.spec)
        sys.exit(1)
    spec = ExperimentSpec.from_config_file(args.spec)
    log.info("已加载 spec: %s", spec.experiment_id)

    # 创建 RunContext 和目录
    ctx    = RunContext.create(spec, runs_root=args.runs_root)
    layout = ArtifactLayout(ctx.run_dir)

    if layout.is_finished():
        log.error(
            "run_dir 已存在且有 RUN_FINISHED.json: %s\n"
            "重跑请等待新的时间戳自动生成新 run_id。",
            ctx.run_dir,
        )
        sys.exit(1)

    layout.create_dirs()
    log.info("=" * 60)
    log.info("run_id  : %s", ctx.run_id)
    log.info("run_dir : %s", ctx.run_dir)
    log.info("阶段    : %s", " → ".join(active_stages))
    log.info("=" * 60)

    # 确定信号路径
    signal_path = _resolve_signal_path(
        args.from_stage, args.input_signal_run,
        ctx.run_dir, args.runs_root, ctx.scope,
    )

    # 写入 run_config
    _write_run_config(layout, spec, ctx, args)
    _write_inputs_lock(layout, signal_path if args.from_stage != "signal" else None,
                       args.from_stage)

    # ── 执行各阶段 ────────────────────────────────────────────────────────────
    from src.pipeline.stages import (
        run_signal_stage, run_portfolio_stage, run_backtest_stage,
        write_self_check_md,
    )
    from src import config as cfg

    try:
        if "signal" in active_stages:
            log.info("── signal 阶段 ──────────────────────────────")
            run_signal_stage(spec, ctx.run_dir, data_proc=cfg.DATA_PROC)

        if "portfolio" in active_stages:
            log.info("── portfolio 阶段 ────────────────────────────")
            run_portfolio_stage(spec, ctx.run_dir, signal_path=signal_path,
                                data_proc=cfg.DATA_PROC)

        if "backtest" in active_stages:
            log.info("── backtest 阶段 ─────────────────────────────")
            run_backtest_stage(spec, ctx.run_dir, data_proc=cfg.DATA_PROC)

    except Exception as exc:
        tb = traceback.format_exc()
        log.error("阶段执行失败: %s\n%s", exc, tb)
        layout.write_run_failed(str(exc))
        log.error("RUN_FAILED.json 已写入: %s", ctx.run_dir)
        sys.exit(1)

    # ── 写入 manifest 和完成标志 ──────────────────────────────────────────────
    try:
        _write_manifest(layout, ctx.run_dir)
    except Exception as e:
        log.warning("manifest 写入失败（不阻断）: %s", e)

    layout.write_run_finished()

    # ── 生成 self_check.md（manifest/RUN_FINISHED 写入后，确保 checklist 准确）──
    if "backtest" in active_stages:
        try:
            log.info("── 生成 reports/self_check.md ───────────────")
            write_self_check_md(spec, ctx.run_dir)
        except Exception as e:
            log.warning("self_check.md 写入失败（不阻断）: %s", e)

    log.info("=" * 60)
    log.info("实验完成: %s", ctx.run_id)
    log.info("产物目录: %s", ctx.run_dir)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
