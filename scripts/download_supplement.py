#!/usr/bin/env python3
"""
补充数据下载脚本 — 融资融券 / 资金流向 / 股东人数 / 分红派息
================================================================
用法:
    python download_supplement.py test        # 验证接口可用性（必须先跑）
    python download_supplement.py all         # 下载全部模块
    python download_supplement.py <module>    # 下载单个模块

模块名称:
    margin      融资融券明细（日频，按股票）
    moneyflow   个股资金流向（日频，按股票）
    holder      股东人数（季频，按股票）
    dividend    分红派息（事件驱动，按股票）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIT 使用规则（因子构建时的关键）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
margin / moneyflow（纯市场数据，无 PIT 问题）：
  T 日字段仅反映 T 日盘后已知信息，按 trade_date 直接使用。

holder（股东人数，季频，有披露延迟）：
  T 日可用数据 = ann_date <= T 的最新记录（同一 end_date 取最新 ann_date）。
  规则与财务三表相同：按 ann_date 做 PIT 对齐，绝不能用 end_date。

dividend（分红派息，事件驱动）：
  只使用 div_proc='实施' 的记录（排除预案/取消）。
  PIT 时间戳：优先用 imp_ann_date（实施公告日），缺失时退回 ann_date。
  注意：ex_date（除权日）晚于 imp_ann_date 约 1-2 周，不能作为 PIT 时间戳。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os
import sys
import time
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import tinyshare as ts
from tqdm import tqdm

# ============================================================
# 常量配置
# ============================================================
TOKEN = os.environ.get("TINYSHARE_TOKEN") or os.environ.get("TUSHARE_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "环境变量 TINYSHARE_TOKEN 未设置。"
        "请先执行: $env:TINYSHARE_TOKEN='<your_token>'  (PowerShell)"
        " 或 export TINYSHARE_TOKEN=<your_token>  (bash)"
    )
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
LOG_DIR = ROOT / "logs"

sys.path.insert(0, str(ROOT))
from src import config as cfg  # noqa: E402

MEMBERS_FILE = cfg.CSI500_WEIGHT_FILE
MARKET_START = cfg.MARKET_START.strftime("%Y%m%d")
MARKET_END   = cfg.MARKET_END.strftime("%Y%m%d")
EARLY_START  = cfg.EARLY_START.strftime("%Y%m%d")

# ── 并发参数（高等级账号默认值；可用环境变量临时覆盖） ─────────
MAX_WORKERS    = int(os.environ.get("TINYSHARE_MAX_WORKERS", "12"))
CALL_INTERVAL  = float(os.environ.get("TINYSHARE_CALL_INTERVAL", "0.03"))
# ──────────────────────────────────────────────────────────

# ============================================================
# 初始化
# ============================================================
ts.set_token(TOKEN)
pro = ts.pro_api()

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "download_supplement.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

for _s in ["margin", "moneyflow", "holder_number", "dividend"]:
    (RAW_DIR / _s).mkdir(parents=True, exist_ok=True)


# ============================================================
# 速率控制 + 重试
# ============================================================
_rate_lock = threading.Lock()
_last_call_ts: float = 0.0


def _call(func, max_retry: int = 5, **kwargs) -> pd.DataFrame:
    """
    全局速率限制 + 指数退避重试。
    多线程共享同一把锁，保证所有线程合计调用频率不超过 1/CALL_INTERVAL 次/秒。
    """
    global _last_call_ts
    fname = getattr(func, "__name__", repr(func))

    for attempt in range(max_retry):
        # 速率限制：等到距上次调用超过 CALL_INTERVAL 才发请求
        with _rate_lock:
            gap = CALL_INTERVAL - (time.time() - _last_call_ts)
            if gap > 0:
                time.sleep(gap)
            _last_call_ts = time.time()

        try:
            df = func(**kwargs)
            return df if df is not None else pd.DataFrame()
        except Exception as exc:
            msg = str(exc)
            if "限流" in msg or "频率" in msg or "rate" in msg.lower():
                log.warning(f"[{fname}] 触发频率限制，暂停 60s ...")
                time.sleep(60)
            else:
                wait = 1.0 * (2 ** attempt)
                log.warning(f"[{fname}] 第 {attempt+1} 次失败: {exc}，等待 {wait:.0f}s")
                time.sleep(wait)

    log.error(f"[{fname}] 连续 {max_retry} 次失败，kwargs={kwargs}")
    return pd.DataFrame()


# ============================================================
# 工具函数
# ============================================================

def _stock_list() -> list[str]:
    df = pd.read_csv(MEMBERS_FILE, dtype=str, usecols=["con_code"])
    return sorted(df["con_code"].unique().tolist())


def _done(subdir: str, name: str) -> bool:
    return (RAW_DIR / subdir / f"{name}.csv").exists()


def _save(subdir: str, name: str, df: pd.DataFrame) -> None:
    path = RAW_DIR / subdir / f"{name}.csv"
    (df if df is not None else pd.DataFrame()).to_csv(path, index=False, encoding="utf-8")


def _get_existing_min_date(subdir: str, name: str, date_col: str) -> str | None:
    """
    返回已存在 CSV 文件中 date_col 列的最早日期（YYYYMMDD 字符串）。
    文件不存在、为空或列不存在时返回 None。

    Args:
        subdir:   raw/ 下的子目录名
        name:     CSV 文件名（不含 .csv 后缀）
        date_col: 日期列名（如 "trade_date" 或 "ann_date"）
    """
    path = RAW_DIR / subdir / f"{name}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, usecols=[date_col], dtype=str)
        if df.empty or df[date_col].isna().all():
            return None
        dates = df[date_col].dropna().str[:8]
        return str(dates.min())
    except Exception as exc:
        log.debug("_get_existing_min_date %s/%s: %s", subdir, name, exc)
        return None


def _backfill_needed(subdir: str, name: str, target_start: str, date_col: str) -> bool:
    """
    判断是否需要向前补历史数据。

    当文件不存在，或文件中最早日期晚于 target_start 时返回 True。

    Args:
        subdir:        raw/ 下的子目录名
        name:          CSV 文件名（不含 .csv 后缀）
        target_start:  目标起始日期（YYYYMMDD 字符串）
        date_col:      日期列名
    """
    min_date = _get_existing_min_date(subdir, name, date_col)
    if min_date is None:
        return True
    return min_date > target_start


def _save_merged(subdir: str, name: str, new_df: pd.DataFrame, pk_cols: list[str]) -> None:
    """
    将 new_df 与已有 CSV 合并后保存，按 pk_cols 去重（保留最新值）并排序。
    若文件不存在或合并失败，退化为直接覆盖写入。

    主键规则（调用方传入）：
        trade_date 类日频文件  → pk_cols=["trade_date"]
        财务/股东类文件         → pk_cols=["ann_date", "end_date"]

    Args:
        subdir:   raw/ 下的子目录名
        name:     CSV 文件名（不含 .csv 后缀）
        new_df:   新下载的 DataFrame
        pk_cols:  去重主键列列表
    """
    if new_df is None or new_df.empty:
        return
    path = RAW_DIR / subdir / f"{name}.csv"
    if path.exists():
        try:
            existing = pd.read_csv(path, dtype=str)
            if not existing.empty:
                combined = pd.concat([existing, new_df.astype(str)], ignore_index=True)
                combined = combined.drop_duplicates(subset=pk_cols, keep="last")
                combined = combined.sort_values(pk_cols).reset_index(drop=True)
                combined.to_csv(path, index=False, encoding="utf-8")
                return
        except Exception as exc:
            log.warning("_save_merged %s/%s 合并失败: %s，改为覆盖写入", subdir, name, exc)
    _save(subdir, name, new_df)


def _run_parallel(subdir: str, desc: str, tasks: list[tuple]) -> None:
    """
    通用并发下载框架。
    tasks 格式: [(ts_code, api_func, call_kwargs, post_fn_or_None), ...]
    post_fn: 下载后对 df 做额外处理（如过滤），返回处理后的 df。
    """
    todo = [(code, fn, kw, post) for code, fn, kw, post in tasks
            if not _done(subdir, code)]
    log.info(f"{subdir}: {len(todo)}/{len(tasks)} 只待下载 (workers={MAX_WORKERS})")

    if not todo:
        return

    def _worker(args):
        code, fn, kw, post = args
        df = _call(fn, **kw)
        if post and not df.empty:
            df = post(df)
        _save(subdir, code, df)
        return code

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_worker, t): t[0] for t in todo}
        failed = []
        for fut in tqdm(as_completed(futures), total=len(futures), desc=desc):
            try:
                fut.result()
            except Exception as e:
                code = futures[fut]
                log.error(f"{subdir}/{code} 最终失败: {e}")
                failed.append(code)

    if failed:
        log.warning(f"{subdir} 失败 {len(failed)} 只: {failed[:10]}")


def _run_backfill_parallel(
    subdir: str,
    desc: str,
    stock_list: list[str],
    api_func,
    target_start: str,
    date_col: str,
    pk_cols: list[str],
    post_fn=None,
) -> None:
    """
    向前补历史并发框架。

    仅处理"文件已存在但最早日期 > target_start"的股票。
    新文件（_done 为 False）由 _run_parallel 负责，不在此处理。

    Args:
        subdir:       raw/ 下的子目录名
        desc:         tqdm 显示描述
        stock_list:   全量股票列表（函数内自行筛选需要补历史的子集）
        api_func:     Tushare API 函数（接受 ts_code, start_date, end_date 参数）
        target_start: 目标起始日期（YYYYMMDD 字符串）
        date_col:     日期列名（用于最早日期判断）
        pk_cols:      去重主键列（传给 _save_merged）
        post_fn:      可选后处理函数（对 df 做转换，返回新 df）
    """
    backfill_tasks = [
        (code, _get_existing_min_date(subdir, code, date_col))
        for code in stock_list
        if _done(subdir, code) and _backfill_needed(subdir, code, target_start, date_col)
    ]
    backfill_tasks = [(code, emi) for code, emi in backfill_tasks if emi is not None]
    log.info(f"{subdir}: {len(backfill_tasks)} 只待补历史 (workers={MAX_WORKERS})")

    if not backfill_tasks:
        return

    def _worker(args):
        code, existing_min = args
        df = _call(api_func, ts_code=code, start_date=target_start, end_date=existing_min)
        if post_fn and not df.empty:
            df = post_fn(df)
        _save_merged(subdir, code, df, pk_cols)
        return code

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_worker, t): t[0] for t in backfill_tasks}
        failed = []
        for fut in tqdm(as_completed(futures), total=len(futures), desc=f"{desc}(补历史)"):
            try:
                fut.result()
            except Exception as e:
                code = futures[fut]
                log.error(f"{subdir}/{code} 补历史失败: {e}")
                failed.append(code)

    if failed:
        log.warning(f"{subdir} 补历史失败 {len(failed)} 只: {failed[:10]}")


# ============================================================
# 连接测试
# ============================================================

def test_connection() -> bool:
    """验证四个模块的接口可用性并检查关键字段。"""
    results: dict[str, bool] = {}
    _t = dict(max_retry=1)
    s = "000001.SZ"

    df = _call(pro.margin_detail, **_t, ts_code=s, start_date="20230101", end_date="20230131")
    results["T1_融资融券明细"] = not df.empty
    if not df.empty:
        missing = {"trade_date","ts_code","rzye","rqye","rzmre","rzrqye"} - set(df.columns)
        results["T1_关键字段齐全"] = not missing
        if missing:
            log.warning(f"margin_detail 缺字段: {missing}")

    df = _call(pro.moneyflow, **_t, ts_code=s, start_date="20230101", end_date="20230131")
    results["T2_个股资金流向"] = not df.empty
    if not df.empty:
        missing = {"trade_date","ts_code","buy_elg_amount","sell_elg_amount","net_mf_amount"} - set(df.columns)
        results["T2_关键字段齐全"] = not missing
        if missing:
            log.warning(f"moneyflow 缺字段: {missing}")

    df = _call(pro.stk_holdernumber, **_t, ts_code=s, start_date="20230101", end_date="20231231")
    results["T3_股东人数"] = df is not None
    if df is not None and not df.empty:
        results["T3_ann_date字段(PIT必需)"] = "ann_date" in df.columns

    df = _call(pro.dividend, **_t, ts_code=s)
    results["T4_分红派息"] = df is not None
    if df is not None and not df.empty:
        missing = {"ann_date","ex_date","cash_div_tax","div_proc","imp_ann_date"} - set(df.columns)
        results["T4_关键字段齐全"] = not missing
        if missing:
            log.warning(f"dividend 缺字段: {missing}")

    print("\n" + "=" * 60)
    print("           补充数据 API 测试结果")
    print("=" * 60)
    all_pass = True
    for name, ok in results.items():
        print(f"  {'[OK]  ' if ok else '[FAIL]'}  {name}")
        if not ok:
            all_pass = False
    print("=" * 60)
    print("全部通过，可执行 all 命令。\n" if all_pass else "存在失败项，请检查后重试。\n")
    return all_pass


# ============================================================
# 下载模块
# ============================================================

def dl_margin(stock_list: list[str]):
    """
    融资融券明细（日频）。

    关键字段：
        rzye     融资余额（元）
        rqye     融券余额（元）
        rzmre    融资买入额（元）—— 当日新增融资买入
        rzche    融资偿还额（元）
        rqyl     融券卖出量（股）
        rqchl    融券偿还量（股）
        rzrqye   融资融券余额合计

    PIT: 纯市场数据，trade_date 即可用日期，无延迟。
    注: 非两融标的股票返回空，正常保存空文件作为断点标记。
    """
    tasks = [
        (code, pro.margin_detail, dict(ts_code=code, start_date=MARKET_START, end_date=MARKET_END), None)
        for code in stock_list
    ]
    _run_parallel("margin", "融资融券", tasks)
    _run_backfill_parallel("margin", "融资融券", stock_list,
                           pro.margin_detail, MARKET_START, "trade_date", ["trade_date"])


def dl_moneyflow(stock_list: list[str]):
    """
    个股资金流向（日频）。

    关键字段：
        buy_elg_amount   超大单买入额（万元，单笔 ≥500万 或 ≥100手）
        sell_elg_amount  超大单卖出额
        buy_lg_amount    大单买入额（单笔 100-500万）
        sell_lg_amount   大单卖出额
        net_mf_amount    主力净流入 = (超大单+大单买) - (超大单+大单卖)，万元

    因子构建：
        主力净流入比 = net_mf_amount / daily_quote.amount（需与行情数据 join）
        N 日累积净流入 = rolling_sum(net_mf_amount, N)

    PIT: 纯市场数据，trade_date 即可用日期，无延迟。
    """
    tasks = [
        (code, pro.moneyflow, dict(ts_code=code, start_date=MARKET_START, end_date=MARKET_END), None)
        for code in stock_list
    ]
    _run_parallel("moneyflow", "资金流向", tasks)
    _run_backfill_parallel("moneyflow", "资金流向", stock_list,
                           pro.moneyflow, MARKET_START, "trade_date", ["trade_date"])


def dl_holder_number(stock_list: list[str]):
    """
    股东人数（季频披露）。

    关键字段：
        ann_date    公告日期（PIT 时间戳，必须用此字段做 PIT 对齐）
        end_date    统计截止日（报告期）
        holder_num  股东人数

    PIT 规则：
        T 日可用 = ann_date <= T 的最新记录（同一 end_date 取最大 ann_date）
        绝对不能用 end_date，end_date 早于 ann_date 约 1-3 个月。

    因子：股东人数变化率（减少 → 持仓集中 → 正向 alpha，A 股实证显著）
    从 EARLY_START（cfg.EARLY_START）开始，确保训练期起点有历史数据可用。
    """
    tasks = [
        (code, pro.stk_holdernumber,
         dict(ts_code=code, start_date=EARLY_START, end_date=MARKET_END), None)
        for code in stock_list
    ]
    _run_parallel("holder_number", "股东人数", tasks)
    _run_backfill_parallel("holder_number", "股东人数", stock_list,
                           pro.stk_holdernumber, EARLY_START, "ann_date", ["ann_date", "end_date"])


def dl_dividend(stock_list: list[str]):
    """
    分红派息（事件驱动）。

    关键字段：
        ann_date       分红方案首次公告日
        imp_ann_date   实施公告日（推荐 PIT 时间戳）
        ex_date        除权除息日（不能作为 PIT 时间戳，此时股价已调整）
        div_proc       实施状态：预案 / 股东大会预案 / 实施 / 取消
        cash_div       每股分红（元，含税）
        cash_div_tax   每股分红（元，税后，推荐用于股息率因子）
        stk_div        每股送股数
        stk_bo_rate    每股转增数

    PIT 规则：
        只用 div_proc='实施' 的记录。
        PIT 时间戳：imp_ann_date（缺失时退回 ann_date）。
        ex_date 是股价已调整后的日期，以此为时间戳会引入未来数据。

    不设日期范围，拉取全量历史，保证股息率序列完整。
    下载后在此处过滤掉已取消的预案，减少磁盘占用。
    """
    def _filter(df: pd.DataFrame) -> pd.DataFrame:
        return df[df["div_proc"] != "取消"].copy()

    tasks = [
        (code, pro.dividend, dict(ts_code=code), _filter)
        for code in stock_list
    ]
    _run_parallel("dividend", "分红派息", tasks)


# ============================================================
# 主入口
# ============================================================

RUN_ORDER = ["margin", "moneyflow", "holder", "dividend"]


def main(targets: list[str] | None = None):
    stocks = _stock_list()
    log.info(f"成分股（去重）: {len(stocks)} 只，并发线程: {MAX_WORKERS}")

    dispatch = {
        "margin":    lambda: dl_margin(stocks),
        "moneyflow": lambda: dl_moneyflow(stocks),
        "holder":    lambda: dl_holder_number(stocks),
        "dividend":  lambda: dl_dividend(stocks),
    }

    run = [m for m in RUN_ORDER if targets is None or m in targets]
    for mod in run:
        log.info(f"\n{'='*55}\n  模块: {mod}\n{'='*55}")
        t0 = time.time()
        dispatch[mod]()
        log.info(f"  模块 {mod} 完成，耗时 {(time.time()-t0)/60:.1f} 分钟")

    log.info("\n全部指定模块下载完成！")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "test":
        ok = test_connection()
        sys.exit(0 if ok else 1)
    elif cmd == "all":
        if not test_connection():
            log.error("测试未通过，请先解决失败项")
            sys.exit(1)
        main()
    elif cmd in RUN_ORDER:
        main(targets=[cmd])
    else:
        print(f"未知命令: {cmd}\n可用: test | all | {' | '.join(RUN_ORDER)}")
        sys.exit(1)
