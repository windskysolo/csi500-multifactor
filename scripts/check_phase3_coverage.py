#!/usr/bin/env python3
"""
阶段3数据质量检查脚本 — 补充数据 + 备选数据覆盖验证
================================================================
用法:
    python -m scripts.check_phase3_coverage all         # 检查全部模块
    python -m scripts.check_phase3_coverage supplement  # 只检查 supplement 模块
    python -m scripts.check_phase3_coverage alt         # 只检查 alt data 模块
    python -m scripts.check_phase3_coverage moneyflow   # 检查单个模块

输出:
    控制台摘要
    reports/alt_data_coverage_report.md   (覆盖率报告)
    logs/phase3_quality_check.log         (详细日志)

设计原则:
    - 纯本地检查，无需 API 访问
    - 按模块独立检查，可随时对已完成模块运行
    - 样本抽检（非全量读取），控制运行时间
"""
import io
import sys
import random
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# Windows 终端默认 GBK 编码，emoji 会崩溃；强制 stdout 使用 UTF-8
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src import config as cfg  # noqa: E402

RAW_DIR   = cfg.DATA_RAW
LOG_DIR   = ROOT / "logs"
REPORT_DIR = ROOT / "reports"
LOG_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "phase3_quality_check.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

MARKET_START_STR = cfg.MARKET_START.strftime("%Y%m%d")  # "20110101"
HK_HOLD_START    = "20141117"   # 沪深港通开通日

# ============================================================
# 每个模块的检查规格
# ============================================================
#   type:    "stock" | "day" | "single"
#   dir:     raw/ 下的子目录名（single 用 file 字段）
#   date_col: 代表时间覆盖的主日期列
#   min_date: 预期数据起点（YYYYMMDD）；None 表示无硬性要求
#   req_cols: 必须存在的列
#   expected: "stock" 表示对齐成分股池；int 为固定值；None 跳过计数校验

MODULE_SPEC: dict[str, dict] = {
    # ── supplement ──────────────────────────────────────────
    "moneyflow": {
        "type": "stock", "dir": "moneyflow",
        "date_col": "trade_date", "min_date": MARKET_START_STR,
        "req_cols": ["ts_code", "trade_date"],
        "expected": "stock",
    },
    "holder_number": {
        "type": "stock", "dir": "holder_number",
        "date_col": "ann_date", "min_date": None,
        "req_cols": ["ts_code", "ann_date"],
        "expected": "stock",
    },
    "dividend": {
        "type": "stock", "dir": "dividend",
        "date_col": "ann_date", "min_date": None,
        "req_cols": ["ts_code", "ann_date"],
        "expected": "stock",
    },
    "margin": {
        "type": "stock", "dir": "margin",
        "date_col": "trade_date",
        # 融资融券 2012-2013 才对 CSI500 股票广泛开放，不强制 2011 起点
        "min_date": "20120101",
        "req_cols": ["ts_code", "trade_date"],
        "expected": "stock",
    },
    # ── alt data — per stock ─────────────────────────────────
    "hk_hold": {
        "type": "stock", "dir": "hk_hold",
        "date_col": "trade_date",
        # 深港通 2016-12-05 开通；CSI500 以深市小盘股为主，多数数据从 2016-12 起
        # 沪市股票从 2014-11-17 起；两者混合，使用深港通日期作为检查基准
        "min_date": "20161201",
        "req_cols": ["ts_code", "trade_date", "vol", "ratio", "exchange"],
        "expected": "stock",
    },
    "analyst_rc": {
        "type": "stock", "dir": "analyst_rc",
        "date_col": "report_date", "min_date": None,
        "req_cols": ["ts_code", "report_date"],
        "expected": "stock",
    },
    "share_float": {
        "type": "stock", "dir": "share_float",
        "date_col": "ann_date", "min_date": None,
        "req_cols": ["ts_code", "ann_date", "float_date", "share_type"],
        "expected": "stock",
    },
    "holder_trade": {
        "type": "stock", "dir": "holder_trade",
        "date_col": "ann_date", "min_date": None,
        "req_cols": ["ts_code", "ann_date", "in_de", "change_vol"],
        "expected": "stock",
    },
    "cyq_perf": {
        "type": "stock", "dir": "cyq_perf",
        "date_col": "trade_date",
        # API 实测数据从 ~2018 起；早期无数据属已知限制（phase_plan 6.2 节已记录）
        "min_date": None,
        "req_cols": ["ts_code", "trade_date", "weight_avg", "winner_rate"],
        "expected": "stock",
    },
    "pledge_stat": {
        "type": "stock", "dir": "pledge_stat",
        "date_col": "end_date", "min_date": None,
        "req_cols": ["ts_code", "end_date", "pledge_ratio"],
        "expected": "stock",
    },
    "stk_surv": {
        "type": "stock", "dir": "stk_surv",
        "date_col": "surv_date", "min_date": None,
        "req_cols": ["ts_code", "surv_date"],
        "expected": "stock",
    },
    "fina_mainbz": {
        "type": "stock", "dir": "fina_mainbz",
        "date_col": "end_date", "min_date": None,
        "req_cols": ["ts_code", "end_date", "bz_item"],
        "expected": "stock",
    },
    # ── alt data — per trading day ───────────────────────────
    "top_list": {
        "type": "day", "dir": "top_list",
        "date_col": "trade_date", "min_date": MARKET_START_STR,
        "req_cols": ["trade_date", "ts_code", "net_amount"],
        "expected": None,
    },
    "top_inst": {
        "type": "day", "dir": "top_inst",
        "date_col": "trade_date", "min_date": MARKET_START_STR,
        "req_cols": ["trade_date", "ts_code", "net_buy"],
        "expected": None,
    },
    "block_trade": {
        "type": "day", "dir": "block_trade",
        "date_col": "trade_date", "min_date": MARKET_START_STR,
        "req_cols": ["ts_code", "trade_date", "vol", "amount"],
        "expected": None,
    },
    # ── alt data — single file ───────────────────────────────
    "hsgt_flow": {
        "type": "single", "file": "hsgt_flow.csv",
        "date_col": "trade_date", "min_date": HK_HOLD_START,
        "req_cols": ["trade_date", "north_money", "south_money"],
        "expected": None,
    },
}

SUPPLEMENT_MODULES = ["moneyflow", "holder_number", "dividend", "margin"]
ALT_MODULES = [
    "hk_hold", "analyst_rc", "share_float", "holder_trade",
    "cyq_perf", "pledge_stat", "hsgt_flow",
    "top_list", "top_inst", "block_trade",
    "stk_surv", "fina_mainbz",
]


# ============================================================
# 成分股池
# ============================================================

def _stock_count() -> int:
    """从权重文件读取成分股数量（去重）。"""
    if not cfg.CSI500_WEIGHT_FILE.exists():
        return 1539  # fallback
    try:
        df = pd.read_csv(cfg.CSI500_WEIGHT_FILE, dtype=str, usecols=["con_code"])
        return df["con_code"].nunique()
    except Exception:
        return 1539


# ============================================================
# 结果数据类
# ============================================================

@dataclass
class ModuleResult:
    module:        str
    file_count:    int = 0
    expected:      int = 0            # 0 = N/A
    empty_files:   int = 0
    sample_min:    str = "-"
    sample_max:    str = "-"
    missing_cols:  list[str] = field(default_factory=list)
    date_ok:       bool = True        # False if min_date constraint not met
    issues:        list[str] = field(default_factory=list)
    status:        str = "❌ 未下载"


# ============================================================
# 检查函数
# ============================================================

def _read_sample_dates(path: Path, date_col: str) -> tuple[str, str]:
    """读取单个文件的最早/最晚日期，只读前 5000 行。"""
    try:
        df = pd.read_csv(path, usecols=[date_col], dtype=str, nrows=5000)
        if df.empty or df[date_col].isna().all():
            return "-", "-"
        dates = df[date_col].dropna().str[:8]
        return str(dates.min()), str(dates.max())
    except Exception:
        return "-", "-"


def _check_fields(path: Path, req_cols: list[str]) -> list[str]:
    """返回缺失的必需列名。"""
    try:
        header = pd.read_csv(path, nrows=0).columns.tolist()
        return [c for c in req_cols if c not in header]
    except Exception:
        return req_cols


def check_stock_module(mod: str, spec: dict, n_stock: int, n_sample: int = 8) -> ModuleResult:
    """检查按股票存储的模块。"""
    res = ModuleResult(module=mod)
    d = RAW_DIR / spec["dir"]

    if not d.exists():
        res.issues.append("目录不存在")
        return res

    files = list(d.glob("*.csv"))
    res.file_count = len(files)
    res.expected   = n_stock if spec["expected"] == "stock" else spec.get("expected", 0)

    if not files:
        res.issues.append("目录为空")
        return res

    # 空文件（< 80 字节约等于只有表头）
    empty = [f for f in files if f.stat().st_size < 80]
    res.empty_files = len(empty)
    non_empty = [f for f in files if f.stat().st_size >= 80]

    if not non_empty:
        res.issues.append("全为空文件（API 无数据属正常）")
        res.status = "⚠️ 全空"
        return res

    # 随机抽样（固定 seed 保证可复现）
    rng = random.Random(42)
    sample = rng.sample(non_empty, min(n_sample, len(non_empty)))

    # 日期范围
    date_col = spec["date_col"]
    all_min, all_max = [], []
    for f in sample:
        dmin, dmax = _read_sample_dates(f, date_col)
        if dmin != "-":
            all_min.append(dmin)
            all_max.append(dmax)

    if all_min:
        res.sample_min = min(all_min)
        res.sample_max = max(all_max)

    # 字段检查（只检查第一个样本文件）
    res.missing_cols = _check_fields(sample[0], spec["req_cols"])
    if res.missing_cols:
        res.issues.append(f"缺字段: {res.missing_cols}")

    # 日期起点校验（宽松 7 天：1月1日不是交易日，第一条数据可能是 1月4日）
    if spec["min_date"] and res.sample_min != "-":
        grace_ts = pd.Timestamp(spec["min_date"]) + pd.Timedelta(days=7)
        if pd.Timestamp(res.sample_min) > grace_ts:
            res.date_ok = False
            res.issues.append(
                f"数据起点 {res.sample_min} 晚于预期 {spec['min_date']}（可能未补历史）"
            )

    # 状态判断
    gap = res.expected - res.file_count if res.expected else 0
    if res.file_count == 0:
        res.status = "❌ 未下载"
    elif gap > 50:
        res.status = f"⚠️ 缺 {gap} 只"
    elif res.empty_files > len(non_empty):
        res.status = "⚠️ 空文件偏多"
    elif not res.date_ok:
        res.status = "⚠️ 历史不足"
    elif res.missing_cols:
        res.status = "⚠️ 字段缺失"
    else:
        res.status = "✅"

    return res


def check_day_module(mod: str, spec: dict, n_sample: int = 5) -> ModuleResult:
    """检查按交易日存储的模块。"""
    res = ModuleResult(module=mod, expected=0)
    d = RAW_DIR / spec["dir"]

    if not d.exists():
        res.issues.append("目录不存在")
        return res

    files = sorted(d.glob("*.csv"))
    res.file_count = len(files)

    if not files:
        res.issues.append("目录为空")
        return res

    empty = [f for f in files if f.stat().st_size < 80]
    res.empty_files = len(empty)
    non_empty = [f for f in files if f.stat().st_size >= 80]

    # 日期范围从文件名推断（YYYYMMDD.csv）
    stems = sorted(f.stem for f in files)
    if stems:
        res.sample_min = stems[0]
        res.sample_max = stems[-1]

    # 字段检查（抽一个非空文件）
    if non_empty:
        rng = random.Random(42)
        sample = rng.sample(non_empty, min(n_sample, len(non_empty)))
        res.missing_cols = _check_fields(sample[0], spec["req_cols"])
        if res.missing_cols:
            res.issues.append(f"缺字段: {res.missing_cols}")

    # 日期起点校验（宽松 7 天）
    if spec["min_date"] and res.sample_min != "-":
        grace_ts = pd.Timestamp(spec["min_date"]) + pd.Timedelta(days=7)
        if pd.Timestamp(res.sample_min) > grace_ts:
            res.date_ok = False
            res.issues.append(f"数据起点 {res.sample_min} 晚于预期 {spec['min_date']}")

    if res.file_count == 0:
        res.status = "❌ 未下载"
    elif non_empty:
        res.status = "✅" if not res.issues else "⚠️ " + "; ".join(res.issues[:1])
    else:
        res.status = "⚠️ 全空（龙虎榜/大宗稀疏属正常）"

    return res


def check_single_module(mod: str, spec: dict) -> ModuleResult:
    """检查单文件模块（如 hsgt_flow）。"""
    res = ModuleResult(module=mod, expected=0)
    path = RAW_DIR / spec["file"]

    if not path.exists() or path.stat().st_size < 80:
        res.status = "❌ 文件不存在或为空"
        res.issues.append("文件不存在或为空")
        return res

    res.file_count = 1
    date_col = spec["date_col"]
    res.sample_min, res.sample_max = _read_sample_dates(path, date_col)
    res.missing_cols = _check_fields(path, spec["req_cols"])

    if res.missing_cols:
        res.issues.append(f"缺字段: {res.missing_cols}")

    if spec["min_date"] and res.sample_min != "-":
        if res.sample_min > spec["min_date"]:
            res.date_ok = False
            res.issues.append(f"数据起点 {res.sample_min} 晚于预期 {spec['min_date']}")

    res.status = "✅" if not res.issues else "⚠️ " + "; ".join(res.issues[:1])
    return res


def check_module(mod: str, n_stock: int) -> ModuleResult:
    """按模块类型分发检查。"""
    spec = MODULE_SPEC.get(mod)
    if spec is None:
        log.warning("未知模块: %s", mod)
        return ModuleResult(module=mod, status="❌ 未知模块")

    if spec["type"] == "stock":
        return check_stock_module(mod, spec, n_stock)
    if spec["type"] == "day":
        return check_day_module(mod, spec)
    if spec["type"] == "single":
        return check_single_module(mod, spec)
    return ModuleResult(module=mod, status="❌ 未知类型")


# ============================================================
# 报告输出
# ============================================================

def _print_result(res: ModuleResult) -> None:
    count_str = (
        f"{res.file_count}/{res.expected}"
        if res.expected > 0
        else str(res.file_count)
    )
    date_str = (
        f"{res.sample_min} ~ {res.sample_max}"
        if res.sample_min != "-"
        else "—"
    )
    issues_str = " | ".join(res.issues) if res.issues else ""
    log.info(
        "  %-16s  %s  count=%-12s  date=%s  %s",
        res.module, res.status, count_str, date_str, issues_str,
    )


def write_coverage_report(results: list[ModuleResult], n_stock: int) -> None:
    """生成 reports/alt_data_coverage_report.md。"""
    out = REPORT_DIR / "alt_data_coverage_report.md"
    lines = [
        "# 阶段3数据覆盖质量报告",
        "",
        f"**生成时间**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**成分股池**: {n_stock} 只  ",
        f"**目标范围**: {cfg.MARKET_START.date()} ~ {cfg.MARKET_END.date()}  ",
        "",
        "---",
        "",
        "## 一、补充数据（supplement）",
        "",
        "| 模块 | 文件数/预期 | 空文件 | 日期范围（抽样） | 状态 |",
        "|---|---|---|---|---|",
    ]
    supp = [r for r in results if r.module in SUPPLEMENT_MODULES]
    for r in supp:
        cnt = f"{r.file_count}/{r.expected}" if r.expected else str(r.file_count)
        lines.append(
            f"| `{r.module}` | {cnt} | {r.empty_files} "
            f"| {r.sample_min} ~ {r.sample_max} | {r.status} |"
        )

    lines += [
        "",
        "## 二、备选数据 — 按股票（alt/stock）",
        "",
        "| 模块 | 文件数/预期 | 空文件 | 日期范围（抽样） | 状态 |",
        "|---|---|---|---|---|",
    ]
    alt_stock = [r for r in results if r.module in [
        "hk_hold", "analyst_rc", "share_float", "holder_trade",
        "cyq_perf", "pledge_stat", "stk_surv", "fina_mainbz",
    ]]
    for r in alt_stock:
        cnt = f"{r.file_count}/{r.expected}" if r.expected else str(r.file_count)
        lines.append(
            f"| `{r.module}` | {cnt} | {r.empty_files} "
            f"| {r.sample_min} ~ {r.sample_max} | {r.status} |"
        )

    lines += [
        "",
        "## 三、备选数据 — 按交易日（alt/day）",
        "",
        "| 模块 | 文件数 | 空文件 | 日期范围 | 状态 |",
        "|---|---|---|---|---|",
    ]
    alt_day = [r for r in results if r.module in ["top_list", "top_inst", "block_trade"]]
    for r in alt_day:
        lines.append(
            f"| `{r.module}` | {r.file_count} | {r.empty_files} "
            f"| {r.sample_min} ~ {r.sample_max} | {r.status} |"
        )

    lines += [
        "",
        "## 四、备选数据 — 单文件（alt/single）",
        "",
        "| 模块 | 行数 | 日期范围 | 状态 |",
        "|---|---|---|---|",
    ]
    alt_single = [r for r in results if r.module in ["hsgt_flow"]]
    for r in alt_single:
        lines.append(
            f"| `{r.module}` | {r.file_count} "
            f"| {r.sample_min} ~ {r.sample_max} | {r.status} |"
        )

    # 问题汇总
    problems = [r for r in results if r.issues or "⚠️" in r.status or "❌" in r.status]
    lines += ["", "## 五、问题汇总", ""]
    if problems:
        for r in problems:
            lines.append(f"- **{r.module}** ({r.status}): {'; '.join(r.issues) or '见上表'}")
    else:
        lines.append("无问题。")

    lines += [
        "",
        "---",
        f"*由 `scripts/check_phase3_coverage.py` 生成*",
    ]

    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    log.info("覆盖率报告已写入: %s", out)


def write_failed_files(results: list[ModuleResult]) -> None:
    """将有问题的模块写入 logs/alt_data_failed.txt。"""
    out = LOG_DIR / "alt_data_failed.txt"
    failed = [r for r in results if "❌" in r.status or "⚠️" in r.status]
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# 阶段3数据质量问题记录\n")
        fh.write(f"# 生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        if failed:
            fh.write(f"# 共 {len(failed)} 个模块有问题\n\n")
            for r in failed:
                fh.write(f"{r.module}  {r.status}  {'; '.join(r.issues)}\n")
        else:
            fh.write("# 全部模块检查通过\n")
    log.info("问题记录已写入: %s", out)


# ============================================================
# 主函数
# ============================================================

def main() -> None:
    targets_arg = sys.argv[1] if len(sys.argv) > 1 else "all"

    if targets_arg == "supplement":
        modules = SUPPLEMENT_MODULES
    elif targets_arg == "alt":
        modules = ALT_MODULES
    elif targets_arg == "all":
        modules = SUPPLEMENT_MODULES + ALT_MODULES
    elif targets_arg in MODULE_SPEC:
        modules = [targets_arg]
    else:
        log.error("未知目标: %s", targets_arg)
        log.error("用法: python -m scripts.check_phase3_coverage [all|supplement|alt|<module>]")
        sys.exit(1)

    n_stock = _stock_count()
    log.info("=== 阶段3数据质量检查 ===")
    log.info("成分股池: %d 只  检查模块: %s", n_stock, modules)

    results: list[ModuleResult] = []
    for mod in modules:
        log.info("--- %s ---", mod)
        res = check_module(mod, n_stock)
        results.append(res)
        _print_result(res)

    # 汇总
    ok  = sum(1 for r in results if "✅" in r.status)
    warn = sum(1 for r in results if "⚠️" in r.status)
    fail = sum(1 for r in results if "❌" in r.status)
    log.info("=== 检查完成: ✅ %d  ⚠️ %d  ❌ %d ===", ok, warn, fail)

    # 仅在检查了多个模块时生成报告文件
    if len(modules) > 1:
        write_coverage_report(results, n_stock)
        write_failed_files(results)


if __name__ == "__main__":
    main()
