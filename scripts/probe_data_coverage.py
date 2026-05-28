#!/usr/bin/env python3
"""
数据可得性探针脚本 — 阶段 1
===========================
用法:
    python scripts/probe_data_coverage.py local     # 仅检查本地 raw 文件覆盖范围（无需 token）
    python scripts/probe_data_coverage.py api       # 仅检查新增接口可用性（需要 token）
    python scripts/probe_data_coverage.py all       # 完整探针（local + api）

输出:
    reports/data_expansion_probe_2010_2025.md

停止条件（任一触发则打印 STOP 并以非零退出）:
    - H00905.CSI 全收益指数在 2012-2015 有严重缺口（local 检查）
    - index_weight 2012-2015 任何调仓月权重合计明显偏离 100（local 检查）
    - hk_hold / report_rc 任一接口返回空或缺少关键字段（api 检查）
"""
import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from src import config as cfg

REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
OUTPUT_FILE = REPORTS_DIR / "data_expansion_probe_2010_2025.md"

RAW_DIR = cfg.DATA_RAW
DATA_DIR = ROOT / "data"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

# ── 结果收集 ──────────────────────────────────────────────
_results: list[str] = []   # markdown 行
_stop_flags: list[str] = []


def _h(text: str) -> None:
    _results.append(f"\n{text}\n")


def _row(text: str) -> None:
    _results.append(text)


def _warn(text: str) -> None:
    _results.append(f"> ⚠️  {text}")
    log.warning(text)


def _stop(text: str) -> None:
    _stop_flags.append(text)
    _results.append(f"> 🛑 **STOP 条件触发**: {text}")
    log.error(f"STOP: {text}")


def _ok(text: str) -> None:
    _results.append(f"> ✅ {text}")


# ============================================================
# 工具函数
# ============================================================

def _read_sample_csv(path: Path, date_col: str = "trade_date",
                     nrows: int = 0) -> pd.DataFrame:
    """安全读取 CSV，返回空 DataFrame 而不抛异常。"""
    try:
        if nrows:
            return pd.read_csv(path, dtype=str, nrows=nrows)
        return pd.read_csv(path, dtype=str)
    except Exception as e:
        log.warning(f"读取失败 {path}: {e}")
        return pd.DataFrame()


def _dir_coverage(subdir: str, date_col: str = "trade_date",
                  sample_n: int = 10) -> dict:
    """
    抽样检查按股票分文件的目录，返回覆盖情况。
    sample_n: 抽几只股票读，避免全量读取耗时过长。
    """
    d = RAW_DIR / subdir
    if not d.exists():
        return {"exists": False}

    files = sorted(d.glob("*.csv"))
    if not files:
        return {"exists": True, "file_count": 0, "min_date": None, "max_date": None}

    sample = files[:sample_n]
    dfs = []
    for f in sample:
        df = _read_sample_csv(f)
        if not df.empty and date_col in df.columns:
            dfs.append(df[[date_col]].dropna())

    if not dfs:
        return {"exists": True, "file_count": len(files), "min_date": None, "max_date": None,
                "note": f"date_col={date_col} not found or all empty"}

    combined = pd.concat(dfs, ignore_index=True)
    return {
        "exists": True,
        "file_count": len(files),
        "sample_n": len(sample),
        "min_date": combined[date_col].min(),
        "max_date": combined[date_col].max(),
        "row_sample": len(combined),
    }


def _single_file_coverage(path: Path, date_col: str = "trade_date") -> dict:
    """检查单文件的日期覆盖范围。"""
    if not path.exists():
        return {"exists": False}
    df = _read_sample_csv(path)
    if df.empty:
        return {"exists": True, "rows": 0, "min_date": None, "max_date": None}
    if date_col not in df.columns:
        return {"exists": True, "rows": len(df), "note": f"{date_col} column missing, cols={list(df.columns)[:8]}"}
    return {
        "exists": True,
        "rows": len(df),
        "min_date": df[date_col].min(),
        "max_date": df[date_col].max(),
    }


# ============================================================
# Phase 1.1 本地数据覆盖检查
# ============================================================

def check_local_coverage() -> None:
    _h("## 1. 现有接口本地数据覆盖情况")
    _row(f"检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _row(f"RAW_DIR：`{RAW_DIR}`")
    _row(f"目标 MARKET_START：`{cfg.MARKET_START.date()}`")
    _row(f"目标 EARLY_START：`{cfg.EARLY_START.date()}`\n")

    # ── 1.1 成分股权重文件 ──────────────────────────────
    _h("### 1.1 成分股权重文件（前置依赖）")

    weight_files = sorted(DATA_DIR.glob("csi500_index_weight_*.csv"))
    if not weight_files:
        _stop("成分股权重文件不存在，所有模块均无法运行")
        return

    for wf in weight_files:
        r = _single_file_coverage(wf, date_col="trade_date")
        _row(f"- `{wf.name}`：{r}")
        if r.get("min_date"):
            min_d = r["min_date"]
            if min_d > "20121231":
                # 本地文件从 2016 起是预期行为，标记为需扩展，不是 STOP
                _warn(f"权重文件最早日期 {min_d} 晚于 2012-12-31 → 阶段 3A 需重建权重文件到 2012 起")
                _row("  > 注：这是预期状态，本次扩展计划的目标之一就是补齐 2012-2015 权重数据")
            else:
                _ok(f"权重文件已覆盖至 {min_d}，满足 2012 训练期要求")

            # 检查已有数据的权重合计（仅对已有数据做质量校验）
            try:
                df_w = pd.read_csv(wf, dtype=str)
                df_w["weight"] = pd.to_numeric(df_w["weight"], errors="coerce")
                df_w["trade_date"] = df_w["trade_date"].astype(str)
                existing = df_w[df_w["trade_date"] >= min_d]
                by_date = existing.groupby("trade_date")["weight"].sum()
                bad = by_date[by_date < 80]
                if not bad.empty:
                    # 已有数据权重合计异常才是真正的质量问题
                    _stop(f"权重文件中 {len(bad)} 个调仓月权重合计低于 80%（数据质量问题）：{bad.head(3).to_dict()}")
                else:
                    _ok(f"已有 {len(by_date)} 个调仓月，权重合计均 ≥ 80%（数据质量正常）")
            except Exception as e:
                _warn(f"权重合计校验异常：{e}")

    # ── 1.2 行情类 ──────────────────────────────────────
    _h("### 1.2 行情类数据（按股票分文件）")
    _row("| 目录 | 文件数 | 抽样最早日期 | 抽样最晚日期 | 目标起点 | 状态 |")
    _row("|---|---|---|---|---|---|")

    _row(f"\n> 注：'需补历史'表示本地缓存未覆盖目标起点，这是**预期状态**，阶段 3A 会修复。")
    _row(f"> 只有'目录不存在'或'无日期列'才需要人工介入。\n")

    market_modules = [
        ("daily_quote",  "trade_date", cfg.MARKET_START.strftime("%Y%m%d")),
        ("adj_factor",   "trade_date", cfg.MARKET_START.strftime("%Y%m%d")),
        ("daily_basic",  "trade_date", cfg.MARKET_START.strftime("%Y%m%d")),
        ("suspend",      "trade_date",   cfg.MARKET_START.strftime("%Y%m%d")),
    ]

    for subdir, dcol, target in market_modules:
        r = _dir_coverage(subdir, date_col=dcol)
        if not r.get("exists"):
            _stop(f"`{subdir}` 目录不存在，阶段 3A 无法运行")
            _row(f"| `{subdir}` | — | — | — | {target} | ❌ 目录不存在 |")
            continue
        min_d = r.get("min_date", "N/A")
        max_d = r.get("max_date", "N/A")
        note = r.get("note", "")
        if note:
            status = f"❓ {note}"
        elif not min_d or min_d == "N/A":
            status = "❓ 无日期列"
        elif min_d <= target:
            status = "✅ 已覆盖"
        else:
            status = "⚠️ 需补历史（预期）"
        _row(f"| `{subdir}` | {r['file_count']} | {min_d} | {max_d} | {target} | {status} |")

    # ── 1.3 停牌 / 涨跌停 ──────────────────────────────
    _h("### 1.3 停牌 / 涨跌停（按日期分文件）")
    limit_dir = RAW_DIR / "limit_list"
    target_yyyymmdd = cfg.MARKET_START.strftime("%Y%m%d")
    if limit_dir.exists():
        limit_files = sorted(limit_dir.glob("*.csv"))
        if limit_files:
            earliest = limit_files[0].stem
            latest = limit_files[-1].stem
            _row(f"- `limit_list`：{len(limit_files)} 个交易日文件，最早 {earliest}，最晚 {latest}")
            if earliest > target_yyyymmdd:
                _warn(f"limit_list 最早日期 {earliest} 晚于目标 {target_yyyymmdd} → 阶段 3A 需补历史（预期）")
            else:
                _ok(f"limit_list 已覆盖 {earliest} 起，满足要求")
        else:
            _stop("limit_list 目录存在但完全为空，涨跌停约束无法工作")
    else:
        _stop("limit_list 目录不存在")

    # ── 1.4 基准指数 ──────────────────────────────────
    _h("### 1.4 基准指数（全收益指数，关键）")
    index_path = RAW_DIR / "index_daily.csv"
    r = _single_file_coverage(index_path, "trade_date")
    _row(f"- `index_daily.csv`：{r}")

    if r.get("exists") and r.get("rows", 0) > 0:
        try:
            df_idx = pd.read_csv(index_path, dtype=str)
            codes = df_idx["ts_code"].unique().tolist() if "ts_code" in df_idx.columns else []
            _row(f"  - ts_code 唯一值：`{codes}`")
            # 代码口径是真正的 STOP（用错基准会产生永久性偏差）
            if "H00905.CSI" in codes:
                _ok("确认为全收益指数 H00905.CSI ✓")
            elif "000905.SH" in codes or "000905.CSI" in codes:
                _stop(f"index_daily.csv 代码为 {codes}，疑似价格指数而非全收益指数，会产生 2-3% 虚假超额，必须重新下载")
            else:
                _warn(f"index_daily.csv ts_code={codes}，请人工确认是否为全收益指数")

            min_d = r.get("min_date")
            target = cfg.MARKET_START.strftime("%Y%m%d")
            if min_d and min_d > target:
                # 本地文件起点晚于目标是预期行为（待扩展），不是 STOP
                _warn(f"全收益指数本地最早日期 {min_d} 晚于目标 {target} → 阶段 3A 需扩展（预期）")
            elif min_d:
                _ok(f"全收益指数本地已覆盖至 {min_d}")
        except Exception as e:
            _warn(f"全收益指数校验异常：{e}")
    else:
        _stop("index_daily.csv 不存在或为空，无法确认全收益指数代码口径")

    # ── 1.5 财务类 ──────────────────────────────────
    _h("### 1.5 财务类数据（按股票，PIT，ann_date 为主键）")
    _row("| 目录 | 文件数 | 抽样最早 ann_date | 抽样最晚 ann_date | 目标起点 | 状态 |")
    _row("|---|---|---|---|---|---|")

    fin_modules = [
        ("financial_income",    "ann_date", cfg.EARLY_START.strftime("%Y%m%d")),
        ("financial_balance",   "ann_date", cfg.EARLY_START.strftime("%Y%m%d")),
        ("financial_cashflow",  "ann_date", cfg.EARLY_START.strftime("%Y%m%d")),
        ("financial_indicator", "ann_date", cfg.EARLY_START.strftime("%Y%m%d")),
        ("holder_number",       "ann_date", cfg.EARLY_START.strftime("%Y%m%d")),
    ]

    _row(f"\n> 注：财务目录最早日期晚于 EARLY_START 是预期状态，阶段 3A 会修复。\n")

    for subdir, dcol, target in fin_modules:
        r = _dir_coverage(subdir, date_col=dcol)
        if not r.get("exists"):
            _stop(f"`{subdir}` 目录不存在")
            _row(f"| `{subdir}` | — | — | — | {target} | ❌ 目录不存在 |")
            continue
        min_d = r.get("min_date", "N/A")
        max_d = r.get("max_date", "N/A")
        note = r.get("note", "")
        if note:
            status = f"❓ {note}"
        elif not min_d or min_d == "N/A":
            status = "❓ 无日期列"
        elif min_d <= target:
            status = "✅ 已覆盖"
        else:
            status = "⚠️ 需补历史（预期）"
        _row(f"| `{subdir}` | {r['file_count']} | {min_d} | {max_d} | {target} | {status} |")

    # ── daily_basic 的 free_share 字段检查 ──
    _h("### 1.6 daily_basic free_share 字段校验（中性化依赖）")
    db_dir = RAW_DIR / "daily_basic"
    if db_dir.exists():
        sample_files = sorted(db_dir.glob("*.csv"))[:5]
        for sf in sample_files:
            df = _read_sample_csv(sf, nrows=3)
            if not df.empty:
                has_fs = "free_share" in df.columns
                _row(f"- `{sf.name}`：列名 = `{list(df.columns)}`")
                if has_fs:
                    _ok(f"{sf.name} 含 free_share 字段")
                else:
                    _stop(f"{sf.name} 缺少 free_share 字段，自由流通市值中性化将失效")
                break
    else:
        _warn("daily_basic 目录不存在")

    # ── 1.7 补充数据覆盖 ──────────────────────────────
    _h("### 1.7 补充数据（margin / moneyflow / dividend）")
    _row("| 目录 | 文件数 | 抽样最早日期 | 抽样最晚日期 | 说明 |")
    _row("|---|---|---|---|---|")

    supp_modules = [
        ("margin",     "trade_date", "早期无数据属正常（两融2010年才开放）"),
        ("moneyflow",  "trade_date", "早期无数据属正常"),
        ("dividend",   "ann_date",   "全量历史，无起点限制"),
    ]

    for subdir, dcol, note in supp_modules:
        r = _dir_coverage(subdir, date_col=dcol)
        if not r.get("exists"):
            _row(f"| `{subdir}` | — | — | — | ❌ 目录不存在 |")
            continue
        min_d = r.get("min_date", "N/A")
        max_d = r.get("max_date", "N/A")
        _row(f"| `{subdir}` | {r['file_count']} | {min_d} | {max_d} | {note} |")


# ============================================================
# Phase 1.2 新增接口可用性检查（需要 token）
# ============================================================

def check_api_availability() -> None:
    """
    用 v2.1 实测字段做校验。字段名来自 phase_plan.md 第3节实测记录，
    与原 Tushare 文档不完全一致（如 exchange 而非 exchange_type，eps 而非 eps_y1）。
    """
    _h("## 2. 新增接口可用性验证（TinyShare）")

    TOKEN = os.environ.get("TINYSHARE_TOKEN") or os.environ.get("TUSHARE_TOKEN")
    if not TOKEN:
        _warn("未检测到 TINYSHARE_TOKEN / TUSHARE_TOKEN 环境变量，跳过 API 检查")
        _row("\n```powershell")
        _row("$env:TINYSHARE_TOKEN = '<your_token>'")
        _row("python scripts/probe_data_coverage.py api")
        _row("```")
        return

    try:
        import tinyshare as ts
        ts.set_token(TOKEN)
        pro = ts.pro_api()
        log.info("TinyShare 初始化成功")
    except ImportError:
        _warn("tinyshare 未安装，跳过 API 检查")
        return
    except Exception as e:
        _stop(f"TinyShare 初始化失败：{e}")
        return

    def _call(func, **kwargs) -> pd.DataFrame:
        fname = getattr(func, "__name__", repr(func))
        try:
            time.sleep(0.6)
            df = func(**kwargs)
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            log.warning(f"[{fname}] 调用失败: {e}")
            return pd.DataFrame()

    TEST_CODE  = "000001.SZ"
    TEST_START = "20230101"
    TEST_END   = "20230131"

    _row(f"\n抽样股票：`{TEST_CODE}`，抽样区间：{TEST_START} ~ {TEST_END}\n")
    _row("| 接口 | 优先级 | 状态 | 实际列数 | 缺失字段 | 备注 |")
    _row("|---|---|---|---|---|---|")

    # 字段列表来自 phase_plan.md v2.1 第 3 节实测，不是文档推测
    checks = [
        # (模块名, call_lambda, 必需字段集, 优先级, 特殊stop规则)
        (
            "hk_hold",
            lambda: _call(pro.hk_hold, ts_code=TEST_CODE,
                          start_date=TEST_START, end_date=TEST_END),
            {"ts_code", "trade_date", "vol", "ratio", "exchange"},
            "🔴 高",
            "empty_stop",        # 空结果 → STOP
        ),
        (
            "report_rc",
            lambda: _call(pro.report_rc, ts_code=TEST_CODE,
                          start_date="20220101", end_date="20231231"),
            {"ts_code", "report_date", "eps", "rating"},
            "🔴 高",
            "empty_stop",
        ),
        (
            "share_float",
            lambda: _call(pro.share_float, ts_code=TEST_CODE),
            # ann_date 是 PIT 时间戳，必须存在；share_type 替代旧的 float_type
            {"ts_code", "ann_date", "float_date", "float_ratio", "share_type"},
            "🟡 中",
            "ann_date_stop",     # ann_date 缺失 → STOP
        ),
        (
            "stk_holdertrade",
            # 用 600519.SH + 两年窗口，避免 000001.SZ 某月无公告导致误判空结果
            lambda: _call(pro.stk_holdertrade, ts_code="600519.SH",
                          start_date="20220101", end_date="20231231"),
            {"ts_code", "ann_date", "change_vol", "change_ratio"},
            "🟡 中",
            None,
        ),
        (
            "cyq_perf",
            lambda: _call(pro.cyq_perf, ts_code=TEST_CODE,
                          start_date="20230101", end_date="20230131"),
            # winner_rate 是实测字段名，原文档写 winner
            {"ts_code", "trade_date", "weight_avg", "winner_rate", "cost_5pct"},
            "🟡 中（数据从2018起）",
            None,
        ),
        (
            "pledge_stat",
            lambda: _call(pro.pledge_stat, ts_code=TEST_CODE),
            # qd_pct 实测不存在，去掉；end_date 替代 trade_date
            {"ts_code", "end_date", "pledge_ratio"},
            "🟡 中",
            None,
        ),
        (
            "moneyflow_hsgt",
            lambda: _call(pro.moneyflow_hsgt,
                          start_date=TEST_START, end_date=TEST_END),
            {"trade_date", "north_money", "south_money"},
            "🟡 中",
            None,
        ),
        (
            "top_list",
            lambda: _call(pro.top_list, trade_date="20230601"),
            {"trade_date", "ts_code", "net_amount", "reason"},
            "🟢 低",
            None,
        ),
        (
            "top_inst",
            lambda: _call(pro.top_inst, trade_date="20230601"),
            {"trade_date", "ts_code", "buy", "sell", "net_buy"},
            "🟢 低",
            None,
        ),
        (
            "block_trade",
            lambda: _call(pro.block_trade, ts_code=TEST_CODE,
                          start_date=TEST_START, end_date=TEST_END),
            # premium 需后续自行计算（price/close-1），不校验
            {"ts_code", "trade_date", "price", "vol", "buyer", "seller"},
            "🟢 低",
            None,
        ),
        (
            "stk_surv",
            lambda: _call(pro.stk_surv, ts_code=TEST_CODE,
                          start_date=TEST_START, end_date="20231231"),
            # org_cnt 需聚合生成，不在原始字段里
            {"ts_code", "surv_date", "rece_org"},
            "🟢 低",
            None,
        ),
        (
            "fina_mainbz",
            lambda: _call(pro.fina_mainbz, ts_code=TEST_CODE,
                          start_date="20220101", end_date="20231231"),
            # 实测无 ann_date / bz_type；end_date 是报告期，不能直接做 PIT
            {"ts_code", "end_date", "bz_item", "bz_sales"},
            "🟢 低",
            "no_ann_date_warn",  # 记录无 ann_date，不能直接生成 PIT 因子
        ),
    ]

    for name, call_fn, required_fields, priority, special in checks:
        df = call_fn()
        actual_cols = len(df.columns) if not df.empty else 0

        if df.empty:
            status = "❌ 空结果"
            missing_str = "—"
            note = ""
            if special == "empty_stop":
                _stop(f"`{name}` 返回空结果，高优先级接口不可用，请检查账号权限")
        else:
            missing = required_fields - set(df.columns)
            # 记录实际列名供调试
            log.info(f"[{name}] 实际列: {list(df.columns)}")
            if missing:
                status = "⚠️ 字段缺失"
                missing_str = str(missing)
                note = ""
                if special == "ann_date_stop" and "ann_date" in missing:
                    _stop(f"`{name}.ann_date` 不可用，PIT 对齐无法完成")
                elif special == "empty_stop":
                    _stop(f"`{name}` 缺少关键字段 {missing}")
            else:
                status = f"✅ OK (rows={len(df)})"
                missing_str = "无"
                note = ""

            if special == "no_ann_date_warn":
                if "ann_date" not in df.columns:
                    note = "无 ann_date，只能研究用，不直接生成 PIT 因子"
                    _warn(f"`fina_mainbz` 无 ann_date 字段（实测确认）：只能下载做研究，不能直接进 PIT 因子链路")
                else:
                    note = "有 ann_date（意外，请确认）"

        _row(f"| `{name}` | {priority} | {status} | {actual_cols} | {missing_str} | {note} |")


# ============================================================
# 主函数 + 报告写出
# ============================================================

def write_report() -> None:
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(f"# 数据可得性探针报告\n\n")
        f.write(f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n")
        f.write(f"**脚本**：`scripts/probe_data_coverage.py`  \n")
        f.write(f"**目标 MARKET_START**：{cfg.MARKET_START.date()}  \n")
        f.write(f"**目标 EARLY_START**：{cfg.EARLY_START.date()}  \n\n")
        f.write("---\n")
        f.write("\n".join(_results))
        f.write("\n\n---\n")

        if _stop_flags:
            f.write("\n## ⛔ 汇总：触发停止条件\n\n")
            for s in _stop_flags:
                f.write(f"- {s}\n")
            f.write("\n**结论：请先解决以上问题，再进入阶段 2。**\n")
        else:
            f.write("\n## ✅ 汇总：未触发停止条件\n\n")
            f.write("本地数据检查通过，可进入阶段 2 准备工作。\n")
            if "## 2." not in "\n".join(_results) or "未检测到" in "\n".join(_results):
                f.write("\n> ⚠️  API 部分尚未验证（需要 token），请在设置 token 后运行 `python scripts/probe_data_coverage.py api`。\n")

    log.info(f"报告已写出：{OUTPUT_FILE}")


def main():
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    if mode in ("local", "all"):
        check_local_coverage()
    if mode in ("api", "all"):
        check_api_availability()
    write_report()
    if _stop_flags:
        log.error(f"探针发现 {len(_stop_flags)} 个停止条件，请查看报告：{OUTPUT_FILE}")
        sys.exit(1)
    else:
        log.info("探针完成，未触发停止条件")
        sys.exit(0)


if __name__ == "__main__":
    main()
