"""
scripts/promote_run.py — 将完成的 run 晋升为主线
=================================================

用法：
  python -m scripts.promote_run \\
      --run-id 20260527_143000__baseline_expanding_ridge_te6_lam0050 \\
      --reason "clean baseline after artifact drift audit"

晋升前检查（由 registry.promote_run 执行）：
  1. run_dir 存在且有 RUN_FINISHED.json
  2. reports/self_check.md 存在
  3. 所有 REQUIRED_FULL_RUN_ARTIFACTS 存在

晋升结果：
  写入 registry/mainline.json
  追加 registry/archived_promotions.jsonl
"""

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.pipeline.registry import promote_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将完成的 run 晋升为 registry/mainline.json"
    )
    parser.add_argument(
        "--run-id", required=True,
        help="要晋升的 run_id，例如 20260527_143000__baseline_expanding_ridge_te6_lam0050",
    )
    parser.add_argument(
        "--reason", required=True,
        help="晋升理由（写入 registry，供审计追溯）",
    )
    parser.add_argument(
        "--scope", default="train_valid",
        choices=["train_valid", "test"],
        help="run 所在的 scope 目录（默认 train_valid）",
    )
    parser.add_argument(
        "--allow-test-set", action="store_true", default=False,
        help="允许晋升包含测试集数据的 run（默认禁止）",
    )
    parser.add_argument(
        "--runs-root", type=Path, default=None,
        help="runs 根目录（默认 runs/）",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    log.info("=" * 60)
    log.info("晋升 run: %s", args.run_id)
    log.info("理由    : %s", args.reason)
    log.info("scope   : %s", args.scope)
    log.info("=" * 60)

    try:
        promote_run(
            run_id      = args.run_id,
            slot        = "mainline",
            reason      = args.reason,
            runs_root   = args.runs_root,
            allow_test_set = args.allow_test_set,
            scope       = args.scope,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        log.error("晋升失败: %s", exc)
        sys.exit(1)

    log.info("=" * 60)
    log.info("晋升成功：registry/mainline.json 已更新")
    log.info("run_id  : %s", args.run_id)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
