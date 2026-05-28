"""
scripts/compare_runs.py — 多 run 横向比较板 CLI
================================================

用法：
  # 从 registry 自动读取（mainline + 有 run_id 的 challengers）
  python -m scripts.compare_runs

  # 指定 run_id 列表
  python -m scripts.compare_runs --run-ids <run_id_1> <run_id_2> ...

  # 指定输出目录
  python -m scripts.compare_runs --output-dir reports/

输出：
  reports/experiment_board.csv   — 原始数据，可导入 Excel
  reports/experiment_board.md    — Markdown 表格，便于代码审阅
"""

import argparse
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.pipeline.compare import (
    IR_MIN,
    MDD_MAX_ABS,
    TO_MAX_PCT,
    TO_MIN_PCT,
    build_board_from_registry,
    compare_runs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成跨 run 横向比较板（experiment_board.csv / .md）"
    )
    parser.add_argument(
        "--run-ids", nargs="+", default=None,
        help="指定 run_id 列表（默认：从 registry 读取 mainline + challengers）",
    )
    parser.add_argument(
        "--scope", default="train_valid", choices=["train_valid", "test"],
        help="run 所在 scope（默认 train_valid）",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="输出目录（默认 reports/）",
    )
    parser.add_argument(
        "--runs-root", type=Path, default=None,
        help="runs/ 根目录（默认自动推断）",
    )
    return parser.parse_args()


def _fmt(v) -> str:
    """Format a cell value for Markdown display."""
    if isinstance(v, float) and math.isnan(v):
        return "—"
    if isinstance(v, bool):
        return "✅" if v else "❌"
    return str(v)


def _df_to_markdown(df) -> str:
    """Convert DataFrame to a GitHub-flavored Markdown table."""
    headers = list(df.columns)
    rows = [[_fmt(v) for v in row] for row in df.itertuples(index=False)]

    # Column widths: max of header length and longest cell
    widths = [
        max(len(h), max((len(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]

    header_row = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    sep_row    = "| " + " | ".join("-" * w for w in widths) + " |"
    data_rows  = [
        "| " + " | ".join(r[i].ljust(widths[i]) for i in range(len(headers))) + " |"
        for r in rows
    ]

    return "\n".join([header_row, sep_row] + data_rows)


def _write_outputs(df, out_dir: Path, scope: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # CSV — strip bool columns for cleaner spreadsheet import
    csv_path = out_dir / "experiment_board.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log.info("CSV 已写入: %s", csv_path)

    # Markdown
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md_lines = [
        "# Experiment Board",
        "",
        f"Generated : {now}",
        f"Scope     : `{scope}`",
        f"Thresholds: IR ≥ {IR_MIN} · "
        f"Excess MDD ≤ {MDD_MAX_ABS*100:.0f}% · "
        f"Annual Turnover {TO_MIN_PCT:.0f}–{TO_MAX_PCT:.0f}%",
        "",
        _df_to_markdown(df),
    ]

    md_path = out_dir / "experiment_board.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    log.info("Markdown 已写入: %s", md_path)


def main() -> None:
    args = _parse_args()
    out_dir = args.output_dir or (_ROOT / "reports")

    log.info("=" * 60)

    if args.run_ids:
        log.info("比较指定 run_ids: %s", args.run_ids)
        df = compare_runs(args.run_ids, scope=args.scope, runs_root=args.runs_root)
    else:
        log.info("从 registry 读取 mainline + challengers …")
        df = build_board_from_registry(scope=args.scope, runs_root=args.runs_root)

    if df.empty:
        log.warning("没有可比较的 run，退出")
        sys.exit(0)

    log.info("共 %d 条 run 记录", len(df))

    _write_outputs(df, out_dir, args.scope)

    log.info("=" * 60)

    # Print to terminal for quick review
    print("\n" + df.to_string(index=False) + "\n")


if __name__ == "__main__":
    main()
