"""
scripts/run_pipeline.py — 全流程一键重跑入口（训练/验证期，不触碰测试集）
=============================================

阶段定义（按顺序）：
    download_core       → 从 Tushare 下载核心原始数据（通常只做一次）
    download_supplement → 下载补充数据（停牌状态、指数成分权重等）
    convert             → CSV 转 Parquet
    quality             → 数据质量筛选（占位，待实现后自动生效）
    factors             → 构建因子面板
    evaluate            → 因子有效性检验
    signal              → 合成信号（训练/验证期）
    portfolio           → 组合优化（训练/验证期）
    backtest            → 策略回测（验证期）
    attribution         → 业绩归因（验证期）

注意：所有阶段仅覆盖 TRAIN_START ~ VALID_END，绝不写入测试集产物。
      测试集评估请使用 scripts/run_test_pipeline.py --run-id N。

使用示例：
    # 重跑全部流程（跳过数据下载）
    python -m scripts.run_pipeline --from-stage convert

    # 只重跑因子检验
    python -m scripts.run_pipeline --from-stage evaluate

    # 重跑因子面板和后续所有阶段
    python -m scripts.run_pipeline --from-stage factors

    # 完整重跑（含数据下载）
    python -m scripts.run_pipeline

    # 跳过下载，从合成信号开始
    python -m scripts.run_pipeline --from-stage signal
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

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.test_set_ledger import count_test_set_runs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 阶段定义：name, description, script_module, default_extra_args
# ---------------------------------------------------------------------------
STAGE_NAMES = [
    "download_core",
    "download_supplement",
    "convert",
    "quality",
    "factors",
    "evaluate",
    "signal",
    "portfolio",
    "backtest",
    "attribution",
]

STAGE_META = {
    "download_core": {
        "desc": "从 Tushare 下载核心原始数据",
        "module": "scripts.download_tushare",
        "extra": ["all"],
    },
    "download_supplement": {
        "desc": "下载补充数据（停牌状态、指数成分权重等）",
        "module": "scripts.download_supplement",
        "extra": ["all"],
    },
    "convert": {
        "desc": "CSV → Parquet 转换",
        "module": "scripts.csv_to_parquet",
        "extra": ["all"],
    },
    "quality": {
        "desc": "数据质量筛选",
        "module": None,
        "placeholder_msg": (
            "[WARN]  quality 阶段尚未实现（scripts/run_data_quality.py 不存在）。\n"
            "   若要继续后续阶段，请显式传入 --skip quality。\n"
            "   不允许静默跳过，以防上游数据质量问题被忽略。"
        ),
        "extra": [],
    },
    "factors": {
        "desc": "构建因子面板（训练/验证期）",
        "module": "scripts.build_factor_panels",
        "extra": [],
    },
    "evaluate": {
        "desc": "因子有效性检验（训练期）",
        "module": "scripts.run_factor_evaluation",
        "extra": [],
    },
    "signal": {
        "desc": "合成信号构建（训练/验证期）",
        "module": "scripts.run_signal_combination",
        "extra": [],
    },
    "portfolio": {
        "desc": "组合优化（训练/验证期）",
        "module": "scripts.run_portfolio_optimization",
        "extra": [],
    },
    "backtest": {
        "desc": "策略回测（验证期）",
        "module": "scripts.run_backtest",
        # L3 fallback（TopN等权）会超过单股偏离上限，属于已知合规风险，pipeline 中显式接受
        "extra": ["--allow-noncompliant-weights"],
    },
    "attribution": {
        "desc": "业绩归因（验证期）",
        "module": "scripts.run_attribution",
        "extra": [],
    },
}

# ---------------------------------------------------------------------------
# 关键产物存在性检查（每个阶段结束后校验输出是否真的生成了）
# ---------------------------------------------------------------------------
_ARTIFACT_CHECKS: dict[str, list[Path]] = {}

def _init_artifact_checks() -> None:
    """初始化关键产物路径（需在导入 config 后调用）。"""
    sys.path.insert(0, str(_PROJECT_ROOT))
    from src import config as cfg

    _ARTIFACT_CHECKS.update({
        "convert": [
            cfg.DATA_PROC / "daily_quote.parquet",
            cfg.DATA_PROC / "index_quote.parquet",
            cfg.DATA_PROC / "index_member.parquet",
            cfg.DATA_PROC / "stock_status.parquet",
        ],
        "factors": [
            cfg.DATA_PROC / "factor_panels",
        ],
        "evaluate": [
            _PROJECT_ROOT / "reports" / "factor_evaluation" / "factor_summary.csv",
            _PROJECT_ROOT / "reports" / "factor_evaluation" / "final_factors.json",
        ],
        "signal": [
            cfg.DATA_PROC / "composite_signal_ic_ir.parquet",
            cfg.DATA_PROC / "composite_signal_equal.parquet",
        ],
        "portfolio": [
            cfg.DATA_PROC / "portfolio_weights_optimized.parquet",
            cfg.DATA_PROC / "portfolio_weights_baseline.parquet",
        ],
        "backtest": [
            cfg.DATA_PROC / "backtest_nav.parquet",
            cfg.DATA_PROC / "backtest_metrics.parquet",
        ],
        "attribution": [
            cfg.DATA_PROC / "brinson_attribution.parquet",
        ],
    })


def _check_artifacts(stage: str) -> bool:
    """检查阶段产物是否存在，任一缺失返回 False 并打印具体路径。"""
    paths = _ARTIFACT_CHECKS.get(stage, [])
    if not paths:
        return True

    all_ok = True
    for p in paths:
        if not p.exists():
            print(f"  [FAIL] 产物缺失：{p}")
            all_ok = False
        else:
            print(f"  [OK] 产物确认：{p.name}")
    return all_ok


# ---------------------------------------------------------------------------
# 运行单个阶段
# ---------------------------------------------------------------------------

def _run_stage(name: str, extra_args: list[str], skip_stages: set[str]) -> bool:
    """
    运行一个阶段，返回是否成功。

    Args:
        name:        阶段名
        extra_args:  附加命令行参数
        skip_stages: 需要跳过的阶段集合（由调用方传入，用于 quality 守卫判断）
    Returns:
        True 表示成功（退出码 0），False 表示失败
    """
    meta = STAGE_META[name]

    # 占位阶段：若未在 --skip 中显式跳过，则强制失败
    if meta["module"] is None:
        print(f"\n{'─' * 60}")
        print(f"[{name}] {meta['desc']}")
        print(meta.get("placeholder_msg", f"  （{name} 暂无脚本）"))
        print(f"{'─' * 60}")
        if name in skip_stages:
            print(f"  [WARN]  [{name}] 已通过 --skip 显式跳过（使用者需自行确认数据质量）")
            return True
        # 未显式跳过时直接失败，阻止继续
        print(f"  [FAIL] [{name}] 未实现且未被 --skip 跳过，pipeline 中止。")
        print(f"     若确认数据质量无问题，请传入 --skip {name} 显式跳过此阶段。")
        return False

    cmd = [sys.executable, "-m", meta["module"]] + extra_args
    print(f"\n{'─' * 60}")
    print(f"[{name}] {meta['desc']}")
    print(f"  命令：{' '.join(cmd)}")
    print(f"{'─' * 60}")

    t0 = time.monotonic()
    result = subprocess.run(cmd, cwd=str(_PROJECT_ROOT))
    elapsed = time.monotonic() - t0

    if result.returncode != 0:
        print(f"  [FAIL] {name} 失败（退出码 {result.returncode}，用时 {elapsed:.1f}s）")
        return False

    print(f"  [OK] {name} 命令执行完成（{elapsed:.1f}s）")

    # F9-001: 产物缺失必须让阶段失败，不能只 warning 继续
    if not _check_artifacts(name):
        print(f"  [FAIL] [{name}] 关键产物缺失，阶段失败。请检查脚本日志后重跑。")
        return False
    return True


# ---------------------------------------------------------------------------
# 生成 run manifest
# ---------------------------------------------------------------------------

def _hash_file(path: "Path") -> str:
    """计算文件 SHA256 前 12 位，文件不存在时返回 'missing'。"""
    if not path.exists():
        return "missing"
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except Exception:
        return "error"


def _count_test_set_runs_ledger() -> int:
    """统计 ledger 中的有效测试集运行次数，用于 manifest 可追溯字段。"""
    return count_test_set_runs()


def _write_run_manifest(
    run_id: str,
    stages_executed: list[str],
    stages_skipped: list[str],
    start_time: str,
    elapsed_s: float,
    success: bool,
    failed_stage: str | None = None,
) -> None:
    """
    在 data/processed/run_manifests/ 下写入本次 pipeline 运行记录。

    F9-006: 增加 config_hash、输入文件 hash、输出文件 hash、
            skipped_stages、test_set_run_count 以达到发布追溯要求。
    """
    try:
        sys.path.insert(0, str(_PROJECT_ROOT))
        from src import config as cfg

        manifest_dir = cfg.DATA_PROC / "run_manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)

        # git commit hash
        try:
            git_commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(_PROJECT_ROOT), text=True, stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            git_commit = "unknown"

        # F9-006: config hash（src/config.py）
        config_py = _PROJECT_ROOT / "src" / "config.py"
        config_hash = _hash_file(config_py)

        # F9-006: 关键输入/输出文件 hash
        input_hashes = {
            "daily_quote":    _hash_file(cfg.DATA_PROC / "daily_quote.parquet"),
            "index_member":   _hash_file(cfg.DATA_PROC / "index_member.parquet"),
            "stock_status":   _hash_file(cfg.DATA_PROC / "stock_status.parquet"),
            "fwd_ret_panel":  _hash_file(cfg.DATA_PROC / "fwd_ret_panel.parquet"),
        }
        output_hashes = {
            "composite_signal": _hash_file(cfg.DATA_PROC / "composite_signal_ic_ir.parquet"),
            "portfolio_weights": _hash_file(cfg.DATA_PROC / "portfolio_weights_optimized.parquet"),
            "backtest_nav":      _hash_file(cfg.DATA_PROC / "backtest_nav.parquet"),
            "brinson_attr":      _hash_file(cfg.DATA_PROC / "brinson_attribution.parquet"),
        }

        # F9-006: 测试集已运行次数（可追溯）
        test_set_run_count = _count_test_set_runs_ledger()

        manifest = {
            "run_id":                run_id,
            "pipeline":              "training_validation",
            "started_at":            start_time,
            "finished_at":           datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "elapsed_seconds":       round(elapsed_s, 1),
            "success":               success,
            "failed_stage":          failed_stage,
            "git_commit":            git_commit,
            "config_hash":           config_hash,
            "stages_executed":       stages_executed,
            "stages_skipped":        stages_skipped,
            "train_end":             str(cfg.TRAIN_END.date()),
            "valid_end":             str(cfg.VALID_END.date()),
            "test_set_run_count":    test_set_run_count,
            "input_hashes":          input_hashes,
            "output_hashes":         output_hashes,
        }

        manifest_path = manifest_dir / f"run_{run_id}.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  Run manifest 已写入：{manifest_path.name}")
    except Exception as e:
        log.warning("写入 run manifest 失败（非致命）: %s", e)


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def run_pipeline(
    from_stage: str,
    skip_stages: set[str],
    eval_no_plots: bool,
) -> None:
    """
    按顺序执行从 from_stage 开始的阶段（仅覆盖训练/验证期）。

    Args:
        from_stage:    起始阶段名（含），之前的阶段全部跳过
        skip_stages:   需要跳过的阶段名集合
        eval_no_plots: True 则向 evaluate 阶段传入 --no-plots
    """
    _init_artifact_checks()

    if eval_no_plots:
        STAGE_META["evaluate"]["extra"] = ["--no-plots"]

    start_idx = STAGE_NAMES.index(from_stage)
    pending = STAGE_NAMES[start_idx:]

    total = len(pending)
    start_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"\n{'=' * 60}")
    print(f"中证500多因子 Pipeline 启动（训练/验证期）")
    print(f"阶段顺序：{' → '.join(STAGE_NAMES)}")
    print(f"执行范围：{' → '.join(pending)}")
    if skip_stages:
        print(f"跳过阶段：{sorted(skip_stages)}")
    print(f"{'=' * 60}")

    t_pipeline_start = time.monotonic()
    stages_executed: list[str] = []
    stages_skipped:  list[str] = []

    for i, stage in enumerate(pending, 1):
        if stage in skip_stages and STAGE_META[stage]["module"] is not None:
            print(f"\n[{stage}] 已跳过（--skip）")
            stages_skipped.append(stage)
            continue

        extra = list(STAGE_META[stage]["extra"])
        success = _run_stage(stage, extra, skip_stages)
        if stage not in skip_stages:
            stages_executed.append(stage)

        if not success:
            elapsed = time.monotonic() - t_pipeline_start
            _write_run_manifest(
                run_id, stages_executed, stages_skipped,
                start_time, elapsed, False, failed_stage=stage,
            )
            print(f"\n[FAIL] Pipeline 在 [{stage}] 阶段中止（{i}/{total}）。")
            print(f"   修复问题后可用 --from-stage {stage} 从此处重跑。")
            sys.exit(1)

    elapsed_total = time.monotonic() - t_pipeline_start
    _write_run_manifest(
        run_id, stages_executed, stages_skipped,
        start_time, elapsed_total, True,
    )

    print(f"\n{'=' * 60}")
    print(f"[OK] Pipeline 全部完成（共 {elapsed_total:.1f}s）")
    print(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="中证500多因子 Pipeline — 全流程一键重跑（训练/验证期）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
阶段说明：
  download_core       从 Tushare 下载核心数据（日行情、财务、停牌等）
  download_supplement 下载补充数据（指数成分权重等）
  convert             CSV → Parquet 转换
  quality             数据质量筛选（未实现，须显式 --skip quality 跳过）
  factors             构建因子面板
  evaluate            因子有效性检验（因子筛选，训练期）
  signal              合成信号（IC_IR 加权 + 等权，训练/验证期）
  portfolio           QP 组合优化（训练/验证期权重）
  backtest            验证期回测（NAV、成本、换手）
  attribution         Brinson + 因子归因（验证期）

示例：
  # 数据已就绪，跳过下载，从转换开始全流程
  python -m scripts.run_pipeline --from-stage convert --skip quality

  # 重跑因子面板及后续所有阶段
  python -m scripts.run_pipeline --from-stage factors --skip quality

  # 只重跑合成信号及后续（因子评价已完成）
  python -m scripts.run_pipeline --from-stage signal
        """,
    )
    parser.add_argument(
        "--from-stage",
        default="download_core",
        choices=STAGE_NAMES,
        metavar="STAGE",
        help=f"从指定阶段开始执行（默认：download_core）",
    )
    parser.add_argument(
        "--skip",
        nargs="+",
        default=[],
        choices=STAGE_NAMES,
        metavar="STAGE",
        help="跳过指定阶段（quality 必须通过此参数显式跳过）",
    )
    parser.add_argument(
        "--eval-no-plots",
        action="store_true",
        help="因子检验阶段跳过可视化（加快速度）",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.from_stage not in STAGE_NAMES:
        log.error("未知阶段：%s，可选：%s", args.from_stage, STAGE_NAMES)
        sys.exit(1)

    run_pipeline(
        from_stage=args.from_stage,
        skip_stages=set(args.skip),
        eval_no_plots=args.eval_no_plots,
    )


if __name__ == "__main__":
    main()
