#!/usr/bin/env python3
"""
备选数据下载脚本 — 北向持仓/分析师研报/解禁/增减持/筹码/质押/资金流/龙虎榜/大宗交易/调研/主营业务
================================================================
用法:
    python -m scripts.download_alternative_data test       # 验证接口可用性（必须先跑）
    python -m scripts.download_alternative_data all        # 下载全部 12 个模块
    python -m scripts.download_alternative_data <module>   # 下载单个模块

模块名称:
    hk_hold       北向持仓（按股票，2014-11-17 起有数据）
    analyst_rc    分析师研报/预期（按股票）
    share_float   解禁计划（按股票）
    holder_trade  股东增减持（按股票）
    cyq_perf      筹码/成本分布摘要（按股票）
    pledge_stat   股权质押统计（按股票）
    hsgt_flow     北向/南向市场级资金流（单 CSV）
    top_list      龙虎榜个股事件（按交易日）
    top_inst      龙虎榜机构席位（按交易日）
    block_trade   大宗交易（按交易日）
    stk_surv      机构调研（按股票）
    fina_mainbz   主营业务构成（按股票，无 ann_date，不直接进 PIT 因子）

实测字段（以此为准，不用文档字段名）：
    hk_hold       code, trade_date, ts_code, name, vol, ratio, exchange
    analyst_rc    ts_code, name, report_date, org_name, author_name, eps, pe, rating
    share_float   ts_code, ann_date, float_date, float_share, float_ratio, holder_name, share_type
    holder_trade  ts_code, ann_date, holder_name, holder_type, in_de, change_vol, change_ratio
    cyq_perf      ts_code, trade_date, weight_avg, winner_rate, cost_5pct~cost_95pct
    pledge_stat   ts_code, end_date, pledge_count, unrest_pledge, rest_pledge, total_share, pledge_ratio
    hsgt_flow     trade_date, ggt_ss, ggt_sz, hgt, sgt, north_money, south_money
    top_list      trade_date, ts_code, name, close, pct_change, turnover_rate, amount, net_amount, net_rate, reason
    top_inst      trade_date, ts_code, exalter, buy, sell, net_buy, side
    block_trade   ts_code, trade_date, price, vol, amount, buyer, seller
    stk_surv      ts_code, name, surv_date, fund_visitors, rece_org, org_type
    fina_mainbz   ts_code, end_date, bz_item, bz_code, bz_sales, bz_profit, bz_cost, curr_type
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

# 沪深港通 2014-11-17 开通；之前 hk_hold 数据为空（正常）
HK_HOLD_START = "20141117"

# 并发参数（高等级账号默认值；可用环境变量临时覆盖）
MAX_WORKERS   = int(os.environ.get("TINYSHARE_MAX_WORKERS", "12"))
CALL_INTERVAL = float(os.environ.get("TINYSHARE_CALL_INTERVAL", "0.03"))

# 测试用样本（test 命令使用，不依赖成分股权重文件）
_TEST_STOCKS = ["000001.SZ", "600000.SH", "601318.SH"]
_TEST_DATE   = "20231201"
_TEST_START  = "20231101"
_TEST_END    = "20231201"

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
        logging.FileHandler(LOG_DIR / "download_alt.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# 创建各模块子目录（hsgt_flow 是单文件，保存到 raw/ 根，不需要子目录）
for _s in cfg.ALT_DATA_SUBDIRS:
    if _s != "hsgt_flow":
        (RAW_DIR / _s).mkdir(parents=True, exist_ok=True)

# 跨模块失败记录（all 命令结束后写入 logs/alt_data_failed.txt）
_FAILED_RECORDS: list[str] = []


# ============================================================
# 速率控制 + 重试
# ============================================================
_rate_lock = threading.Lock()
_last_call_ts: float = 0.0


def _call(func, max_retry: int = 5, **kwargs) -> pd.DataFrame:
    """
    全局速率限制 + 指数退避重试。
    多线程共享同一把锁，保证合计调用频率不超过 1/CALL_INTERVAL 次/秒。
    """
    global _last_call_ts
    fname = getattr(func, "__name__", repr(func))

    for attempt in range(max_retry):
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
    """从 CSI500 权重文件提取全部曾入选成分股（去重，排序）。"""
    if not MEMBERS_FILE.exists():
        raise FileNotFoundError(
            f"成分股权重文件不存在: {MEMBERS_FILE}\n"
            "请先运行: python -m scripts.download_tushare indexweight"
        )
    df = pd.read_csv(MEMBERS_FILE, dtype=str, usecols=["con_code"])
    return sorted(df["con_code"].unique().tolist())


def _trading_days() -> list[str]:
    """获取 MARKET_START ~ MARKET_END 全量交易日列表（YYYYMMDD 字符串）。"""
    df = _call(pro.trade_cal, exchange="SSE",
               start_date=MARKET_START, end_date=MARKET_END, is_open="1")
    return sorted(df["cal_date"].tolist()) if not df.empty else []


def _done(subdir: str, name: str) -> bool:
    """断点续传判断：文件已存在即视为已完成。"""
    return (RAW_DIR / subdir / f"{name}.csv").exists()


def _save(subdir: str, name: str, df: pd.DataFrame) -> None:
    """保存到 raw/{subdir}/{name}.csv（df 为空时也写文件，作断点标记）。"""
    path = RAW_DIR / subdir / f"{name}.csv"
    (df if df is not None else pd.DataFrame()).to_csv(path, index=False, encoding="utf-8")


def _run_parallel(subdir: str, desc: str, tasks: list[tuple]) -> None:
    """
    通用并发下载框架。
    tasks 格式: [(name, api_func, call_kwargs, post_fn_or_None), ...]
    post_fn: 对 df 做额外处理，返回处理后的 df。
    """
    todo = [(name, fn, kw, post) for name, fn, kw, post in tasks
            if not _done(subdir, name)]
    log.info(f"{subdir}: {len(todo)}/{len(tasks)} 待下载 (workers={MAX_WORKERS})")

    if not todo:
        return

    def _worker(args):
        name, fn, kw, post = args
        df = _call(fn, **kw)
        if post and not df.empty:
            df = post(df)
        _save(subdir, name, df)
        return name

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_worker, t): t[0] for t in todo}
        failed = []
        for fut in tqdm(as_completed(futures), total=len(futures), desc=desc):
            try:
                fut.result()
            except Exception as e:
                name = futures[fut]
                log.error(f"{subdir}/{name} 最终失败: {e}")
                failed.append(name)

    if failed:
        _FAILED_RECORDS.extend(f"{subdir}/{name}" for name in failed)
        log.warning(f"{subdir} 失败 {len(failed)} 项: {failed[:10]}")


# ============================================================
# 连接测试
# ============================================================

# 每个模块的必需关键字段（结合实测结果，第3节）
_REQUIRED_FIELDS: dict[str, set[str]] = {
    "hk_hold":     {"ts_code", "trade_date", "vol", "ratio", "exchange"},
    "analyst_rc":  {"ts_code", "report_date"},
    "share_float": {"ts_code", "ann_date", "float_date", "share_type"},
    "holder_trade":{"ts_code", "ann_date", "in_de", "change_vol"},
    "cyq_perf":    {"ts_code", "trade_date", "weight_avg", "winner_rate"},
    "pledge_stat": {"ts_code", "end_date", "pledge_ratio"},
    "hsgt_flow":   {"trade_date", "north_money", "south_money"},
    "top_list":    {"trade_date", "ts_code", "net_amount"},
    "top_inst":    {"trade_date", "ts_code", "net_buy"},
    "block_trade": {"ts_code", "trade_date", "vol", "amount"},
    "stk_surv":    {"ts_code", "surv_date"},
    "fina_mainbz": {"ts_code", "end_date", "bz_item"},
}


def test_connection() -> bool:
    """
    验证 12 个备选数据接口的可用性及关键字段。

    策略：
      - 每个模块用小样本（3 只股票 + 1 个交易日）验证
      - API 抛异常 → FAIL
      - 有数据但缺关键字段 → FAIL
      - 返回空 DataFrame → 记录 NOTE，不作为 FAIL（部分 API 并非所有股票都有数据）
      - hk_hold / cyq_perf / hsgt_flow 为关键接口，空数据也视为 FAIL
    """
    results: dict[str, bool] = {}
    _t = dict(max_retry=1)
    s0 = _TEST_STOCKS[0]  # "000001.SZ"

    def _check(key: str, df: pd.DataFrame, required: bool = False) -> None:
        """检查 df 的字段完整性，更新 results。"""
        if df is None:
            results[key] = False
            return
        if df.empty:
            if required:
                results[key] = False
                log.warning(f"  {key}: 返回空数据（关键接口，视为 FAIL）")
            else:
                results[key] = True
                log.info(f"  {key}: 返回空数据（非强制，视为 OK — 该股可能无此数据）")
            return
        missing = _REQUIRED_FIELDS[key.split("_字段")[0] if "_字段" in key else key] - set(df.columns)
        if missing:
            results[key] = False
            log.warning(f"  {key} 缺字段: {missing}，实际列: {sorted(df.columns)}")
        else:
            results[key] = True

    # T1: hk_hold — 关键接口（北向持仓，2014-11-17 后必须有数据）
    df = _call(pro.hk_hold, **_t, ts_code=s0, start_date=_TEST_START, end_date=_TEST_END)
    _check("hk_hold", df, required=True)

    # T2: report_rc（分析师研报）
    df = _call(pro.report_rc, **_t, ts_code=s0, start_date=_TEST_START, end_date=_TEST_END)
    _check("analyst_rc", df, required=False)

    # T3: share_float（解禁计划）
    df = _call(pro.share_float, **_t, ts_code=s0, start_date=_TEST_START, end_date=_TEST_END)
    _check("share_float", df, required=False)

    # T4: stk_holdertrade（股东增减持）
    df = _call(pro.stk_holdertrade, **_t, ts_code=s0, start_date=_TEST_START, end_date=_TEST_END)
    _check("holder_trade", df, required=False)

    # T5: cyq_perf — 关键接口（筹码分布，应有数据）
    df = _call(pro.cyq_perf, **_t, ts_code=s0, start_date=_TEST_START, end_date=_TEST_END)
    _check("cyq_perf", df, required=True)

    # T6: pledge_stat（股权质押）
    df = _call(pro.pledge_stat, **_t, ts_code=s0)
    _check("pledge_stat", df, required=False)

    # T7: moneyflow_hsgt — 关键接口（市场级资金流，必须有数据）
    df = _call(pro.moneyflow_hsgt, **_t, start_date=_TEST_START, end_date=_TEST_END)
    _check("hsgt_flow", df, required=True)

    # T8: top_list（龙虎榜）
    df = _call(pro.top_list, **_t, trade_date=_TEST_DATE)
    _check("top_list", df, required=False)

    # T9: top_inst（龙虎榜机构席位）
    df = _call(pro.top_inst, **_t, trade_date=_TEST_DATE)
    _check("top_inst", df, required=False)

    # T10: block_trade（大宗交易）
    df = _call(pro.block_trade, **_t, trade_date=_TEST_DATE)
    _check("block_trade", df, required=False)

    # T11: stk_surv（机构调研）
    df = _call(pro.stk_surv, **_t, ts_code=s0, start_date=_TEST_START, end_date=_TEST_END)
    _check("stk_surv", df, required=False)

    # T12: fina_mainbz（主营业务构成）
    df = _call(pro.fina_mainbz, **_t, ts_code=s0)
    _check("fina_mainbz", df, required=False)

    # 汇总报告
    print("\n" + "=" * 65)
    print("              备选数据 API 测试结果")
    print("=" * 65)
    all_pass = True
    for name, ok in results.items():
        tag = "[OK]  " if ok else "[FAIL]"
        print(f"  {tag}  {name}")
        if not ok:
            all_pass = False
    print("=" * 65)
    if all_pass:
        print("全部通过，可执行 all 命令开始下载。\n")
    else:
        print("存在失败项，请检查后再运行下载命令。\n")
    return all_pass


# ============================================================
# 下载模块 — 按股票类
# ============================================================

def dl_hk_hold(stock_list: list[str]) -> None:
    """
    北向持仓（沪深港通）。

    HK_HOLD_START(2014-11-17) 前无数据，下载时会返回空，正常写空文件标记已处理。
    字段：code, trade_date, ts_code, name, vol（持股量，股）, ratio（持股比例%）, exchange
    PIT：trade_date <= T 直接使用，无披露延迟。
    """
    tasks = [
        (code, pro.hk_hold,
         dict(ts_code=code, start_date=HK_HOLD_START, end_date=MARKET_END),
         None)
        for code in stock_list
    ]
    _run_parallel("hk_hold", "北向持仓", tasks)


def dl_analyst_rc(stock_list: list[str]) -> None:
    """
    分析师研报/评级/盈利预测。

    PIT：report_date <= T。
    字段：ts_code, name, report_date, org_name, author_name, eps, pe, rating 等。
    覆盖率：非全覆盖，小盘股/冷门股可能无研报，空文件为正常。
    """
    tasks = [
        (code, pro.report_rc,
         dict(ts_code=code, start_date=EARLY_START, end_date=MARKET_END),
         None)
        for code in stock_list
    ]
    _run_parallel("analyst_rc", "分析师研报", tasks)


def dl_share_float(stock_list: list[str]) -> None:
    """
    限售股解禁计划。

    PIT：ann_date <= T 且 float_date > T（公告已发、解禁未到）。
    字段：ts_code, ann_date, float_date, float_share, float_ratio, holder_name, share_type
    """
    tasks = [
        (code, pro.share_float,
         dict(ts_code=code, start_date=EARLY_START, end_date=MARKET_END),
         None)
        for code in stock_list
    ]
    _run_parallel("share_float", "限售解禁", tasks)


def dl_holder_trade(stock_list: list[str]) -> None:
    """
    股东/高管增减持。

    PIT：ann_date <= T。
    字段：ts_code, ann_date, holder_name, holder_type, in_de, change_vol, change_ratio, after_share, after_ratio, avg_price, total_share
    """
    tasks = [
        (code, pro.stk_holdertrade,
         dict(ts_code=code, start_date=EARLY_START, end_date=MARKET_END),
         None)
        for code in stock_list
    ]
    _run_parallel("holder_trade", "股东增减持", tasks)


def dl_cyq_perf(stock_list: list[str]) -> None:
    """
    筹码/成本分布摘要（日频）。

    字段：ts_code, trade_date, his_low, his_high, cost_5pct~cost_95pct, weight_avg, winner_rate
    注意：用 winner_rate，不是 winner（实测修正）。
    早期数据覆盖有限，2018 年前可能缺失。
    """
    tasks = [
        (code, pro.cyq_perf,
         dict(ts_code=code, start_date=MARKET_START, end_date=MARKET_END),
         None)
        for code in stock_list
    ]
    _run_parallel("cyq_perf", "筹码分布", tasks)


def dl_pledge_stat(stock_list: list[str]) -> None:
    """
    股权质押统计（按报告期）。

    PIT：end_date <= T（保守，无 ann_date 字段）。
    字段：ts_code, end_date, pledge_count, unrest_pledge, rest_pledge, total_share, pledge_ratio
    注意：无 qd_pct 字段（实测修正）。
    下载全量历史（无日期过滤），数据量较少。
    """
    tasks = [
        (code, pro.pledge_stat, dict(ts_code=code), None)
        for code in stock_list
    ]
    _run_parallel("pledge_stat", "股权质押", tasks)


def dl_stk_surv(stock_list: list[str]) -> None:
    """
    机构调研记录。

    PIT：surv_date <= T。
    字段：ts_code, name, surv_date, fund_visitors, rece_place, rece_mode, rece_org, org_type, comp_rece
    注意：无 org_cnt 字段，需后续聚合（实测修正）。
    """
    tasks = [
        (code, pro.stk_surv,
         dict(ts_code=code, start_date=EARLY_START, end_date=MARKET_END),
         None)
        for code in stock_list
    ]
    _run_parallel("stk_surv", "机构调研", tasks)


def dl_fina_mainbz(stock_list: list[str]) -> None:
    """
    主营业务构成（按报告期）。

    无 ann_date 字段，不能直接生成 PIT 因子，仅作研究使用。
    字段：ts_code, end_date, bz_item, bz_code, bz_sales, bz_profit, bz_cost, curr_type
    下载全量历史（无日期过滤）。
    """
    tasks = [
        (code, pro.fina_mainbz, dict(ts_code=code), None)
        for code in stock_list
    ]
    _run_parallel("fina_mainbz", "主营业务", tasks)


# ============================================================
# 下载模块 — 单文件类
# ============================================================

def dl_hsgt_flow() -> None:
    """
    北向/南向市场级资金流（moneyflow_hsgt）。

    保存为单文件：raw/hsgt_flow.csv（不在子目录中）。
    按年分批下载避免超时；已存在则跳过。
    字段：trade_date, ggt_ss, ggt_sz, hgt, sgt, north_money, south_money
    """
    out_path = RAW_DIR / "hsgt_flow.csv"
    if out_path.exists():
        log.info("hsgt_flow.csv 已存在，跳过（重建请先删除文件）")
        return

    start_year = int(cfg.MARKET_START.year)
    end_year   = int(cfg.MARKET_END.year)
    dfs: list[pd.DataFrame] = []

    for year in range(start_year, end_year + 1):
        df = _call(pro.moneyflow_hsgt,
                   start_date=f"{year}0101",
                   end_date=f"{year}1231")
        if not df.empty:
            dfs.append(df)
            log.info("  hsgt_flow %d 年: %d 条", year, len(df))

    if not dfs:
        log.warning("  hsgt_flow 无数据（moneyflow_hsgt API 不可用）")
        pd.DataFrame().to_csv(out_path, index=False, encoding="utf-8")
        return

    result = pd.concat(dfs, ignore_index=True)
    result = result.drop_duplicates(subset=["trade_date"])
    result = result.sort_values("trade_date")
    result.to_csv(out_path, index=False, encoding="utf-8")
    log.info(
        "hsgt_flow: %d 条，%s ~ %s",
        len(result), result["trade_date"].min(), result["trade_date"].max(),
    )


# ============================================================
# 下载模块 — 按交易日类
# ============================================================

def dl_top_list(trading_days: list[str]) -> None:
    """
    龙虎榜个股事件（按交易日）。

    字段：trade_date, ts_code, name, close, pct_change, turnover_rate, amount,
          l_sell, l_buy, l_amount, net_amount, net_rate, amount_rate, float_values, reason
    注意：多数交易日无龙虎榜（正常），空文件作断点标记。
    """
    subdir = "top_list"
    todo = [d for d in trading_days if not _done(subdir, d)]
    log.info(f"top_list: {len(todo)}/{len(trading_days)} 个交易日待下载")

    for trade_date in tqdm(todo, desc="龙虎榜"):
        df = _call(pro.top_list, trade_date=trade_date)
        _save(subdir, trade_date, df)


def dl_top_inst(trading_days: list[str]) -> None:
    """
    龙虎榜机构席位（按交易日）。

    字段：trade_date, ts_code, exalter, buy, buy_rate, sell, sell_rate, net_buy, side, reason
    """
    subdir = "top_inst"
    todo = [d for d in trading_days if not _done(subdir, d)]
    log.info(f"top_inst: {len(todo)}/{len(trading_days)} 个交易日待下载")

    for trade_date in tqdm(todo, desc="龙虎榜机构"):
        df = _call(pro.top_inst, trade_date=trade_date)
        _save(subdir, trade_date, df)


def dl_block_trade(trading_days: list[str]) -> None:
    """
    大宗交易（按交易日）。

    字段：ts_code, trade_date, price, vol, amount, buyer, seller
    注意：无 premium 字段，如需溢价率需后续自行计算（实测修正）。
    """
    subdir = "block_trade"
    todo = [d for d in trading_days if not _done(subdir, d)]
    log.info(f"block_trade: {len(todo)}/{len(trading_days)} 个交易日待下载")

    for trade_date in tqdm(todo, desc="大宗交易"):
        df = _call(pro.block_trade, trade_date=trade_date)
        _save(subdir, trade_date, df)


# ============================================================
# 主入口
# ============================================================

# ============================================================
# 报告生成
# ============================================================

# 每个模块的 PIT/时间日期列名（用于抽样日期覆盖）
_DATE_COLS: dict[str, str] = {
    "hk_hold":      "trade_date",
    "analyst_rc":   "report_date",
    "share_float":  "ann_date",
    "holder_trade": "ann_date",
    "cyq_perf":     "trade_date",
    "pledge_stat":  "end_date",
    "stk_surv":     "surv_date",
    "fina_mainbz":  "end_date",
    "top_list":     "trade_date",
    "top_inst":     "trade_date",
    "block_trade":  "trade_date",
}


def _sample_date_range(path: Path, date_col: str) -> tuple[str, str]:
    """
    从单个 CSV 抽取最早/最晚日期（只读前 10000 行，避免全量 IO）。
    Returns ("YYYYMMDD", "YYYYMMDD") or ("-", "-") on failure.
    """
    try:
        df = pd.read_csv(path, usecols=[date_col], dtype=str, nrows=10000)
        if df.empty or df[date_col].isna().all():
            return "-", "-"
        dates = df[date_col].dropna().str[:8]
        return str(dates.min()), str(dates.max())
    except Exception:
        return "-", "-"


def _write_failed_log() -> None:
    """将 _FAILED_RECORDS 持久化到 logs/alt_data_failed.txt。"""
    log_path = LOG_DIR / "alt_data_failed.txt"
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(f"# 备选数据下载失败记录\n")
        fh.write(f"# 生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        if _FAILED_RECORDS:
            fh.write(f"# 共 {len(_FAILED_RECORDS)} 项失败\n\n")
            for rec in sorted(_FAILED_RECORDS):
                fh.write(rec + "\n")
        else:
            fh.write("# 无失败记录（全部完成或尚未运行）\n")
    log.info("失败记录已写入: %s", log_path)


def generate_coverage_report(stock_list: list[str]) -> None:
    """
    扫描 raw/ 各备选数据目录，生成 reports/alt_data_coverage_report.md。

    统计维度：文件总数、非空数、空文件数、与股票池差距、抽样日期范围、状态。
    不全量读取文件；用文件大小判空（< 80 字节 ≈ 只有表头），抽取 3 个非空文件做日期采样。
    """
    (ROOT / "reports").mkdir(exist_ok=True)
    out_path = ROOT / "reports" / "alt_data_coverage_report.md"
    n_stocks = len(stock_list)

    lines: list[str] = [
        "# 备选数据覆盖报告",
        "",
        f"**生成时间**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**成分股池**: {n_stocks} 只  ",
        f"**目标下载范围**: MARKET_START={MARKET_START} ~ MARKET_END={MARKET_END}  ",
        "",
        "---",
        "",
        "## 一、按股票下载模块",
        "",
        "| 模块 | 总文件 | 非空 | 空文件 | 与股票池差距 | 日期范围（抽样） | 状态 |",
        "|---|---|---|---|---|---|---|",
    ]

    stock_based = [
        "hk_hold", "analyst_rc", "share_float", "holder_trade",
        "cyq_perf", "pledge_stat", "stk_surv", "fina_mainbz",
    ]
    for mod in stock_based:
        d = RAW_DIR / mod
        if not d.exists():
            lines.append(f"| `{mod}` | 0 | 0 | 0 | -{n_stocks} | — | ❌ 目录缺失 |")
            continue

        files = list(d.glob("*.csv"))
        n_total = len(files)
        n_empty = sum(1 for f in files if f.stat().st_size < 80)
        n_with_data = n_total - n_empty
        gap = n_stocks - n_total

        sample = [f for f in files if f.stat().st_size >= 80][:3]
        dmin_all, dmax_all = [], []
        date_col = _DATE_COLS[mod]
        for sf in sample:
            dmin, dmax = _sample_date_range(sf, date_col)
            if dmin != "-":
                dmin_all.append(dmin)
                dmax_all.append(dmax)
        date_range = f"{min(dmin_all)} ~ {max(dmax_all)}" if dmin_all else "无数据"

        if n_total == 0:
            status = "❌ 未下载"
        elif gap > 50:
            status = f"⚠️ 缺 {gap} 只"
        elif n_empty > n_with_data:
            status = "⚠️ 空文件偏多"
        else:
            status = "✅"
        lines.append(
            f"| `{mod}` | {n_total} | {n_with_data} | {n_empty} "
            f"| {gap:+d} | {date_range} | {status} |"
        )

    lines += [
        "",
        "## 二、按交易日下载模块",
        "",
        "| 模块 | 总文件 | 非空 | 空文件 | 日期范围 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    for mod in ["top_list", "top_inst", "block_trade"]:
        d = RAW_DIR / mod
        if not d.exists():
            lines.append(f"| `{mod}` | 0 | 0 | 0 | — | ❌ 目录缺失 |")
            continue
        files = sorted(d.glob("*.csv"))
        n_total = len(files)
        n_empty = sum(1 for f in files if f.stat().st_size < 80)
        n_with_data = n_total - n_empty
        stems = sorted(f.stem for f in files)
        date_range = f"{stems[0]} ~ {stems[-1]}" if stems else "无数据"

        if n_total == 0:
            status = "❌ 未下载"
        elif n_with_data == 0:
            status = "⚠️ 全空（龙虎榜/大宗稀疏属正常）"
        else:
            status = "✅"
        lines.append(
            f"| `{mod}` | {n_total} | {n_with_data} | {n_empty} | {date_range} | {status} |"
        )

    lines += [
        "",
        "## 三、单文件模块",
        "",
        "| 模块 | 文件路径 | 行数 | 日期范围 | 状态 |",
        "|---|---|---|---|---|",
    ]
    hsgt_path = RAW_DIR / "hsgt_flow.csv"
    if hsgt_path.exists() and hsgt_path.stat().st_size > 80:
        try:
            df_hsgt = pd.read_csv(hsgt_path, usecols=["trade_date"], dtype=str)
            dmin = df_hsgt["trade_date"].min()
            dmax = df_hsgt["trade_date"].max()
            lines.append(f"| `hsgt_flow` | raw/hsgt_flow.csv | {len(df_hsgt)} | {dmin} ~ {dmax} | ✅ |")
        except Exception as exc:
            lines.append(f"| `hsgt_flow` | raw/hsgt_flow.csv | — | — | ⚠️ 读取失败: {exc} |")
    else:
        lines.append("| `hsgt_flow` | raw/hsgt_flow.csv | 0 | — | ❌ 未下载 |")

    lines += ["", "## 四、失败记录汇总", ""]
    if _FAILED_RECORDS:
        lines.append(f"共 **{len(_FAILED_RECORDS)}** 项失败（详见 `logs/alt_data_failed.txt`）：")
        lines.append("")
        for rec in sorted(_FAILED_RECORDS)[:20]:
            lines.append(f"- `{rec}`")
        if len(_FAILED_RECORDS) > 20:
            lines.append(f"- …还有 {len(_FAILED_RECORDS) - 20} 项，见日志文件")
    else:
        lines.append("无失败记录。")

    lines += [
        "",
        "---",
        f"*由 `scripts/download_alternative_data.py report` 生成*",
    ]

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    log.info("覆盖率报告已写入: %s", out_path)


# ============================================================
# 支持的模块名称列表（按下载顺序排列）
RUN_ORDER = [
    "hk_hold", "analyst_rc", "share_float", "holder_trade",
    "cyq_perf", "pledge_stat", "hsgt_flow",
    "top_list", "top_inst", "block_trade",
    "stk_surv", "fina_mainbz",
]

# 按股票下载的模块
_STOCK_MODULES = {
    "hk_hold", "analyst_rc", "share_float", "holder_trade",
    "cyq_perf", "pledge_stat", "stk_surv", "fina_mainbz",
}

# 按交易日下载的模块
_DAY_MODULES = {"top_list", "top_inst", "block_trade"}

# 单文件模块
_SINGLE_MODULES = {"hsgt_flow"}


def main(targets: list[str] | None = None) -> None:
    stocks: list[str] = []
    tdays:  list[str] = []

    need_stocks = targets is None or any(m in _STOCK_MODULES for m in (targets or []))
    need_days   = targets is None or any(m in _DAY_MODULES for m in (targets or []))

    if need_stocks:
        stocks = _stock_list()
        log.info(f"成分股（去重）: {len(stocks)} 只，并发线程: {MAX_WORKERS}")

    if need_days:
        tdays = _trading_days()
        if tdays:
            log.info(f"交易日: {len(tdays)} 天（{tdays[0]} ~ {tdays[-1]}）")
        else:
            log.error("交易日历获取失败，per-day 模块无法运行")

    dispatch: dict = {
        "hk_hold":      lambda: dl_hk_hold(stocks),
        "analyst_rc":   lambda: dl_analyst_rc(stocks),
        "share_float":  lambda: dl_share_float(stocks),
        "holder_trade": lambda: dl_holder_trade(stocks),
        "cyq_perf":     lambda: dl_cyq_perf(stocks),
        "pledge_stat":  lambda: dl_pledge_stat(stocks),
        "hsgt_flow":    dl_hsgt_flow,
        "top_list":     lambda: dl_top_list(tdays),
        "top_inst":     lambda: dl_top_inst(tdays),
        "block_trade":  lambda: dl_block_trade(tdays),
        "stk_surv":     lambda: dl_stk_surv(stocks),
        "fina_mainbz":  lambda: dl_fina_mainbz(stocks),
    }

    run = [m for m in RUN_ORDER if targets is None or m in targets]

    for mod in run:
        log.info(f"\n{'='*55}\n  模块: {mod}\n{'='*55}")
        t0 = time.time()
        dispatch[mod]()
        log.info(f"  模块 {mod} 完成，耗时 {(time.time()-t0)/60:.1f} 分钟")

    log.info("\n全部指定模块下载完成！")

    # all 命令：写失败记录 + 生成覆盖率报告
    if targets is None:
        _write_failed_log()
        try:
            report_stocks = stocks if stocks else _stock_list()
            generate_coverage_report(report_stocks)
        except Exception as exc:
            log.warning("覆盖率报告生成失败: %s", exc)


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
            log.error("测试未通过，请先解决失败项后重试")
            sys.exit(1)
        main()

    elif cmd in RUN_ORDER:
        main(targets=[cmd])

    elif cmd == "report":
        # 仅生成覆盖率报告，不重新下载
        try:
            stocks = _stock_list()
        except FileNotFoundError as exc:
            log.error("%s", exc)
            sys.exit(1)
        _write_failed_log()
        generate_coverage_report(stocks)

    else:
        print(f"未知命令: {cmd}")
        print(f"可用命令: test | all | report | {' | '.join(RUN_ORDER)}")
        sys.exit(1)
