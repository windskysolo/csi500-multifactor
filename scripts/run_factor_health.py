"""
scripts/run_factor_health.py — 因子健康监控入口脚本
====================================================

读取阶段一产出的 IC 历史矩阵和因子摘要，生成健康快照 CSV、Markdown 报告、面板图。

输入：
    ic_history_final_research.parquet  （阶段一产出，默认路径）
    factor_summary.csv                 （run_factor_evaluation 产出，含 factor_direction）

输出：
    reports/factor_health/
        health_snapshot.csv     每个因子的 12/24/36m IC_IR、状态、趋势、建议操作
        health_report.md        Markdown 报告（含验证期诊断声明）
        health_panel.png        健康面板图（--no-plot 时跳过）

使用方法：
    python -m scripts.run_factor_health [--ic-history PATH] [--factor-summary PATH]
                                        [--output-dir PATH] [--as-of YYYY-MM-DD]
                                        [--no-plot] [--chow]
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.evaluation.factor_health import (
    FactorHealthMonitor,
    STATUS_STABLE, STATUS_WEAK, STATUS_WARN, STATUS_REVERSE, STATUS_UNKNOWN,
    TREND_DETERIORATING,
    plot_health_panel,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_IC_HISTORY     = "reports/factor_evaluation/ic_history_final_research.parquet"
_DEFAULT_FACTOR_SUMMARY = "reports/factor_evaluation/factor_summary.csv"
_DEFAULT_OUTPUT_DIR     = "reports/factor_health"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="因子健康监控")
    parser.add_argument(
        "--ic-history",
        default=str(_PROJECT_ROOT / _DEFAULT_IC_HISTORY),
        help=f"IC 历史矩阵 parquet 路径（默认：{_DEFAULT_IC_HISTORY}）",
    )
    parser.add_argument(
        "--factor-summary",
        default=str(_PROJECT_ROOT / _DEFAULT_FACTOR_SUMMARY),
        help=f"factor_summary.csv 路径（默认：{_DEFAULT_FACTOR_SUMMARY}）",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_PROJECT_ROOT / _DEFAULT_OUTPUT_DIR),
        help=f"输出目录（默认：{_DEFAULT_OUTPUT_DIR}）",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="评估截止日期（YYYY-MM-DD）；默认使用 IC 历史最后一期",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="跳过面板图，只输出 CSV 和 Markdown",
    )
    parser.add_argument(
        "--chow",
        action="store_true",
        help="（阶段四实现后启用）Chow 结构突变检验",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # 1. 加载 IC 历史
    ic_path = Path(args.ic_history)
    if not ic_path.exists():
        log.error("IC 历史文件不存在，请先运行 run_factor_evaluation.py：%s", ic_path)
        sys.exit(1)
    ic_history = pd.read_parquet(ic_path)
    log.info("IC 历史已加载：%s  shape=%s", ic_path.name, ic_history.shape)

    # 2. 加载 factor_summary.csv，提取 factor_direction
    summary_path = Path(args.factor_summary)
    if not summary_path.exists():
        log.error("factor_summary.csv 不存在：%s", summary_path)
        sys.exit(1)
    factor_summary = pd.read_csv(summary_path, index_col=0)

    if "factor_direction" not in factor_summary.columns:
        log.error(
            "factor_summary.csv 缺少 factor_direction 列，无法运行健康监控。\n"
            "请先运行 run_factor_evaluation.py 重新生成 factor_summary.csv。"
        )
        sys.exit(1)

    # 3. 验证 factor_direction 覆盖率（严禁默认全部正向）
    factors_in_ic   = set(ic_history.columns)
    valid_dir_index = set(
        factor_summary.index[factor_summary["factor_direction"].notna()]
    )
    missing_dir = sorted(factors_in_ic - valid_dir_index)
    if missing_dir:
        log.error(
            "factor_summary.csv 缺少以下 %d 个因子的 factor_direction，无法继续：\n  %s",
            len(missing_dir), missing_dir,
        )
        sys.exit(1)

    factor_directions = factor_summary["factor_direction"]

    # 4. 解析 as_of
    last_ic_date = ic_history.index.max()
    if args.as_of:
        as_of = pd.Timestamp(args.as_of)
        if as_of > last_ic_date:
            log.warning(
                "as_of=%s 晚于 IC 历史最后一期 %s，已自动调整",
                as_of.date(), last_ic_date.date(),
            )
            as_of = last_ic_date
    else:
        as_of = last_ic_date
    log.info("评估截止日期（as_of）：%s", as_of.date())

    # 5. Chow 检验（阶段四）
    if args.chow:
        log.warning("--chow 参数当前未实现（阶段四），已忽略，其余分析正常继续")

    # 6. 创建监控器并构建快照
    try:
        monitor = FactorHealthMonitor(ic_history, factor_directions)
    except ValueError as e:
        log.error("FactorHealthMonitor 初始化失败：%s", e)
        sys.exit(1)

    snapshot = monitor.build_snapshot(as_of)

    # 7. 保存输出
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = output_dir / "health_snapshot.csv"
    snapshot.to_csv(snapshot_path)
    log.info("健康快照已保存：%s  shape=%s", snapshot_path.name, snapshot.shape)

    report_text = monitor.build_report(snapshot, as_of)
    report_path = output_dir / "health_report.md"
    report_path.write_text(report_text, encoding="utf-8")
    log.info("健康报告已保存：%s", report_path.name)

    if not args.no_plot:
        plot_health_panel(
            ic_history, snapshot, as_of,
            output_dir / "health_panel.png",
        )

    # 8. 控制台汇总
    print("\n" + "=" * 65)
    print(f"因子健康监控汇总（as_of={as_of.date()}）")
    print(f"IC 历史：{ic_path.name}  {ic_history.shape}")
    print("=" * 65)

    status_counts = snapshot["status"].value_counts()
    for s in [STATUS_STABLE, STATUS_WEAK, STATUS_WARN, STATUS_REVERSE, STATUS_UNKNOWN]:
        n = status_counts.get(s, 0)
        if n > 0:
            factors_s = snapshot[snapshot["status"] == s].index.tolist()
            print(f"  {s:<25}: {n} 个  {factors_s}")

    det_list = snapshot[snapshot["trend_flag"] == TREND_DETERIORATING].index.tolist()
    if det_list:
        print(f"\n[!] DETERIORATING 因子（{len(det_list)} 个）：{det_list}")
    else:
        print("\n[OK] 无 DETERIORATING 趋势预警")

    review = snapshot[snapshot["suggested_action"].str.startswith("REVIEW_REMOVAL")].index.tolist()
    if review:
        print(f"\n[WARN] 需要复核（REVIEW_REMOVAL）：{review}")
        print("   注意：复核仅说明需要另起实验验证，不能直接修改主线因子池。")

    print(f"\n[输出目录] {output_dir}")
    print("=" * 65)


if __name__ == "__main__":
    main()
