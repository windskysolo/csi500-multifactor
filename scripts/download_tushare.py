#!/usr/bin/env python3
"""
Tushare 数据下载脚本 v1.1
=============================
用法:
    python download_tushare.py test          # 必须先运行：验证所有 API 可用性
    python download_tushare.py all           # 下载全部模块（会先自动跑 test）
    python download_tushare.py <module>      # 下载单个模块（支持断点续传）

模块名称:
    basic       股票基本信息（上市/退市日期）
    namechange  股票曾用名（ST 标记来源）
    industry    申万2021一级行业分类（SW2021，31个行业）
    index       中证500全收益基准日行情
    daily       股票日行情（未复权）
    adjfactor   复权因子
    dailybasic  每日基础指标（PE/PB/自由流通股本）
    suspend     停牌记录
    limitlist   涨跌停列表
    financial   财务三表 + 综合指标（PIT 核心）

行业体系说明：
    本项目统一使用申万2021（SW2021）一级行业（31个），与 src/config.py 及
    docs/PROJECT_PLAN_v1.1.md 保持一致。输出文件 industry_sw2021.csv。

所有模块支持断点续传：中断后重新运行同一命令，已完成的文件会自动跳过。
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

ROOT = Path(__file__).parent.parent          # E:\Acoding\Project\500
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
LOG_DIR = ROOT / "logs"

sys.path.insert(0, str(ROOT))
from src import config as cfg  # noqa: E402

MEMBERS_FILE    = cfg.CSI500_WEIGHT_FILE
MARKET_START    = cfg.MARKET_START.strftime("%Y%m%d")
MARKET_END      = cfg.MARKET_END.strftime("%Y%m%d")
FINANCIAL_START = cfg.EARLY_START.strftime("%Y%m%d")

# 中证500全收益指数代码（含分红再投资）
# test 命令会验证此代码，若不对会提示备选代码
INDEX_TOTAL_RETURN_CODE = "H00905.CSI"


def _env_float(name: str, default: float) -> float:
    """读取浮点型环境变量；格式错误时回退默认值。"""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# 高等级账号默认速度；如触发限流，可用环境变量临时调大间隔或调低 worker。
# 说明：全局间隔是账号级限速，worker 是并发隐藏网络延迟，不代表突破限速。
SLEEP_MARKET = _env_float("TINYSHARE_SLEEP_MARKET", 0.0)
SLEEP_FINANCIAL = _env_float("TINYSHARE_SLEEP_FINANCIAL", 0.0)
TUSHARE_CALL_INTERVAL = _env_float("TINYSHARE_TUSHARE_CALL_INTERVAL", 0.20)
MARKET_WORKERS = int(os.environ.get("TINYSHARE_MARKET_WORKERS", "8"))
FINANCIAL_WORKERS = int(os.environ.get("TINYSHARE_FINANCIAL_WORKERS", "4"))
RATE_LIMIT_COOLDOWN = _env_float("TINYSHARE_RATE_LIMIT_COOLDOWN", 60.0)

# ============================================================
# 初始化 Tushare
# ============================================================
ts.set_token(TOKEN)
pro = ts.pro_api()

# ============================================================
# 日志配置
# ============================================================
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "download.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ============================================================
# 目录初始化
# ============================================================
_SUBDIRS = [
    "daily_quote", "adj_factor", "daily_basic", "suspend", "limit_list",
    "financial_income", "financial_balance", "financial_cashflow", "financial_indicator",
]
for _s in _SUBDIRS:
    (RAW_DIR / _s).mkdir(parents=True, exist_ok=True)


# ============================================================
# 工具函数
# ============================================================
_rate_lock = threading.Lock()
_last_call_ts: float = 0.0
_cooldown_until: float = 0.0


def _wait_for_rate_slot() -> None:
    """全局账号级限速，所有 worker 共享，避免并发请求冲垮 API 限额。"""
    global _last_call_ts
    with _rate_lock:
        now = time.time()
        wait = max(_cooldown_until - now, TUSHARE_CALL_INTERVAL - (now - _last_call_ts))
        if wait > 0:
            time.sleep(wait)
        _last_call_ts = time.time()


def _trigger_cooldown(seconds: float = RATE_LIMIT_COOLDOWN) -> None:
    """触发限流时设置全局冷却窗口，所有线程一起降速等待。"""
    global _cooldown_until
    with _rate_lock:
        _cooldown_until = max(_cooldown_until, time.time() + seconds)


def _func_name(func) -> str:
    """兼容 functools.partial 的函数名获取。"""
    return getattr(func, '__name__', None) or getattr(getattr(func, 'func', None), '__name__', repr(func))


def _call(func, max_retry: int = 5, sleep: float = SLEEP_MARKET, **kwargs) -> pd.DataFrame:
    """带全局限速和指数退避重试的 Tushare API 调用。"""
    fname = _func_name(func)
    for attempt in range(max_retry):
        try:
            _wait_for_rate_slot()
            df = func(**kwargs)
            if sleep > 0:
                time.sleep(sleep)
            return df if df is not None else pd.DataFrame()
        except Exception as exc:
            msg = str(exc)
            if "限流" in msg or "频率" in msg or "rate" in msg.lower():
                log.warning("%s 触发频率限制，全局冷却 %.0fs ...", fname, RATE_LIMIT_COOLDOWN)
                _trigger_cooldown()
                time.sleep(RATE_LIMIT_COOLDOWN)
            else:
                wait = max(1.0, sleep) * (2 ** attempt)
                log.warning(f"{fname} 第 {attempt + 1} 次失败: {exc}，等待 {wait:.1f}s")
                time.sleep(wait)
    log.error(f"{fname} 连续 {max_retry} 次失败，kwargs={kwargs}")
    return pd.DataFrame()


def _stock_list() -> list[str]:
    """从成分股 CSV 提取所有曾入选的去重股票代码。"""
    df = pd.read_csv(MEMBERS_FILE, dtype=str, usecols=["con_code"])
    codes = sorted(df["con_code"].unique().tolist())
    return codes


def _trading_days() -> list[str]:
    """获取 MARKET_START~MARKET_END 的全量交易日列表。"""
    df = _call(pro.trade_cal, exchange="SSE",
               start_date=MARKET_START, end_date=MARKET_END, is_open="1")
    return sorted(df["cal_date"].tolist()) if not df.empty else []


def _done(subdir: str, name: str) -> bool:
    """断点续传判断：文件已存在即视为已完成。"""
    return (RAW_DIR / subdir / f"{name}.csv").exists()


def _save(subdir: str, name: str, df: pd.DataFrame) -> None:
    """保存到 raw/{subdir}/{name}.csv（df 为空时也写文件，标记该条目已处理）。"""
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


def _dl_stock_range(
    subdir: str,
    desc: str,
    stock_list: list[str],
    api_func,
    target_start: str,
    date_col: str,
    pk_cols: list[str],
    sleep: float = SLEEP_MARKET,
    workers: int | None = None,
    **api_kwargs,
) -> None:
    """
    下载 + 向前补历史的通用流程（按股票，并发但共享账号级限速）。

    三态：
      文件不存在 → 全量下载（target_start ~ MARKET_END）
      文件存在但最早日期 > target_start → 补历史（target_start ~ existing_min）并合并
      文件存在且已覆盖 target_start → 跳过

    Args:
        subdir:        raw/ 下的子目录名
        desc:          tqdm 进度条描述
        stock_list:    股票代码列表
        api_func:      Tushare API 函数
        target_start:  目标起始日期（YYYYMMDD 字符串）
        date_col:      用于最早日期检查的列名
        pk_cols:       去重主键列列表（传入 _save_merged）
        sleep:         每次 API 调用后等待秒数
        **api_kwargs:  额外 API 参数（如 report_type="1"）
    """
    todo_new = [s for s in stock_list if not _done(subdir, s)]
    todo_backfill = [
        (s, _get_existing_min_date(subdir, s, date_col))
        for s in stock_list
        if _done(subdir, s) and _backfill_needed(subdir, s, target_start, date_col)
    ]
    workers = workers or MARKET_WORKERS
    log.info(
        "%s: %d 待下载 / %d 待补历史 / %d 合计 (workers=%d, interval=%.2fs)",
        subdir, len(todo_new), len(todo_backfill), len(stock_list),
        workers, TUSHARE_CALL_INTERVAL,
    )

    tasks: list[tuple[str, str, str | None]] = (
        [(ts_code, "new", None) for ts_code in todo_new]
        + [(ts_code, "backfill", existing_min) for ts_code, existing_min in todo_backfill]
    )
    if not tasks:
        return

    failed: list[tuple[str, str]] = []

    def _worker(task: tuple[str, str, str | None]) -> tuple[str, str]:
        ts_code, mode, existing_min = task
        end_date = MARKET_END if mode == "new" or existing_min is None else existing_min
        df = _call(api_func, sleep=sleep,
                   ts_code=ts_code, start_date=target_start, end_date=end_date,
                   **api_kwargs)
        if mode == "new":
            _save(subdir, ts_code, df)
        else:
            _save_merged(subdir, ts_code, df, pk_cols)
        return ts_code, mode

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, task): task for task in tasks}
        for fut in tqdm(as_completed(futures), total=len(futures), desc=desc):
            task = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                ts_code, mode, _ = task
                failed.append((ts_code, mode))
                log.error("%s/%s %s 失败: %s", subdir, ts_code, mode, exc)

    if failed:
        out = LOG_DIR / f"{subdir}_failed.txt"
        out.write_text(
            "\n".join(f"{code},{mode}" for code, mode in failed) + "\n",
            encoding="utf-8",
        )
        log.warning("%s 失败 %d 项，已写入 %s", subdir, len(failed), out)


# ============================================================
# 测试模块（必须先运行，再执行任何下载）
# ============================================================

def test_connection() -> bool:
    """
    验证各 API 可用性。全部通过后才能执行 all 命令。
    重点检查：
      - free_share 字段是否在 daily_basic 中（自由流通市值计算依赖）
      - 全收益指数代码是否正确（是价格指数还是全收益指数直接影响超额 2-3%）
      - ann_date 字段是否在财务报表中（PIT 处理依赖）
    """
    results: dict[str, bool] = {}

    # 测试模式：max_retry=1，失败立即跳过，不阻塞几分钟
    _t = dict(max_retry=1, sleep=0.3)

    # T1: 基本连通性
    df = _call(pro.trade_cal, **_t, exchange="SSE",
               start_date="20230103", end_date="20230107", is_open="1")
    results["T1_基本连通"] = not df.empty

    # T2: 股票日行情
    df = _call(pro.daily, **_t, ts_code="000001.SZ",
               start_date="20230103", end_date="20230110")
    results["T2_日行情"] = not df.empty

    # T3: 复权因子
    df = _call(pro.adj_factor, **_t, ts_code="000001.SZ",
               start_date="20230103", end_date="20230110")
    results["T3_复权因子"] = not df.empty

    # T4: 每日基础指标 + free_share 字段校验
    df = _call(pro.daily_basic, **_t, ts_code="000001.SZ",
               start_date="20230103", end_date="20230110")
    results["T4_daily_basic"] = not df.empty
    if not df.empty:
        has_free_share = "free_share" in df.columns
        results["T4_free_share字段存在"] = has_free_share
        if not has_free_share:
            log.warning(f"daily_basic 无 free_share 字段，实际列: {list(df.columns)}")
            log.warning("  → 自由流通市值计算需要此字段，请确认 Tushare 版本")

    # T5: 全收益基准指数（核心验证，失败则整个项目基准口径有问题）
    df5 = _call(pro.index_daily, **_t, ts_code=INDEX_TOTAL_RETURN_CODE,
                start_date="20230103", end_date="20230110")
    results[f"T5_全收益指数({INDEX_TOTAL_RETURN_CODE})"] = not df5.empty
    if df5.empty:
        log.warning(f"全收益指数 {INDEX_TOTAL_RETURN_CODE} 拉取失败！尝试备选代码 ...")
        for alt in ["000905.SH", "000905.CSI", "399905.SZ", "H00905.SH"]:
            df_alt = _call(pro.index_daily, **_t, ts_code=alt,
                           start_date="20230103", end_date="20230110")
            if not df_alt.empty:
                log.warning(f"  !! 备选代码 {alt} 有数据，请确认它是【全收益指数】还是【价格指数】")
                log.warning(f"  !! 若确认后修改 INDEX_TOTAL_RETURN_CODE = '{alt}'")
                break
        else:
            log.error("  所有备选代码均无数据，Tushare 可能不提供全收益指数")
            log.error("  → 需要手动从中证指数官网下载，或改用价格指数+股息调整")

    # T6: 停牌记录
    df = _call(pro.suspend_d, **_t, ts_code="000001.SZ",
               start_date="20200101", end_date="20201231")
    results["T6_停牌记录"] = df is not None  # 允许空 DataFrame（无停牌即正常）

    # T7: 涨跌停列表（API 名称在不同版本中不同）
    limit_func_name = None
    for fname in ["limit_list_d", "limit_list"]:
        if hasattr(pro, fname):
            df = _call(getattr(pro, fname), **_t, trade_date="20230601")
            results[f"T7_涨跌停({fname})"] = df is not None
            limit_func_name = fname
            break
    else:
        results["T7_涨跌停"] = False
        log.warning("未找到 limit_list_d 或 limit_list，涨跌停模块将跳过")

    # T8: 利润表 + ann_date 字段校验（PIT 核心）
    df = _call(pro.income, **_t, ts_code="000001.SZ",
               start_date="20200101", end_date="20201231")
    results["T8_利润表"] = not df.empty
    if not df.empty:
        has_ann = "ann_date" in df.columns
        results["T8_ann_date字段存在"] = has_ann
        if not has_ann:
            log.warning(f"income 无 ann_date 字段！列: {list(df.columns)}")
            log.warning("  → PIT 处理依赖 ann_date，请检查 Tushare 账号权限")

    # T9: 综合财务指标
    df = _call(pro.fina_indicator, **_t, ts_code="000001.SZ",
               start_date="20200101", end_date="20221231")
    results["T9_财务指标"] = not df.empty

    # T10: 股票基本信息
    df = _call(pro.stock_basic, **_t, list_status="L", exchange="")
    results["T10_股票基本信息"] = not df.empty

    # 统计并输出
    print("\n" + "=" * 60)
    print("                  API 连接测试结果")
    print("=" * 60)
    all_pass = True
    for name, ok in results.items():
        tag = "[OK]  " if ok else "[FAIL]"
        print(f"  {tag}  {name}")
        if not ok:
            all_pass = False
    print("=" * 60)
    if all_pass:
        print("全部通过，可执行 all 命令开始下载。\n")
    else:
        print("存在失败项，请先解决后再运行下载命令。\n")

    if limit_func_name:
        log.info(f"涨跌停 API: pro.{limit_func_name}")

    return all_pass


# ============================================================
# 下载模块
# ============================================================

def dl_index_weight() -> None:
    """
    中证500成分股权重（月度快照）。

    API: pro.index_weight(index_code='000905.SH', start_date=..., end_date=...)
    字段:
        index_code  固定 000905.SH（成分来源指数，非收益基准）
        con_code    成分股 ts_code（含交易所后缀）
        trade_date  月末最后交易日（生效日），代表该月的成分和权重快照
        weight      成分权重（%），每期合计约 100

    注意：index_code=000905.SH 仅用于确定成分和权重；
          收益基准使用 H00905.CSI（全收益指数，含分红再投资）。

    此文件是后续所有下载的前置依赖：_stock_list() 从中提取股票池。
    如需重建，先删除文件再运行 indexweight 命令。
    """
    if MEMBERS_FILE.exists():
        log.info("csi500_index_weight.csv 已存在，跳过（重建请先删除文件）")
        return

    log.info("开始下载中证500成分股权重（按年分批）...")
    start_year = int(MARKET_START[:4])
    end_year   = int(MARKET_END[:4])
    dfs: list[pd.DataFrame] = []

    for year in range(start_year, end_year + 1):
        df = _call(
            pro.index_weight,
            index_code="000905.SH",
            start_date=f"{year}0101",
            end_date=f"{year}1231",
        )
        if not df.empty:
            dfs.append(df)
            log.info("  %d 年: %d 条", year, len(df))

    if not dfs:
        log.error("成分股权重下载失败，所有年份均无数据；请检查 token 积分和 API 权限")
        return

    result = pd.concat(dfs, ignore_index=True)
    result = result.drop_duplicates(subset=["index_code", "con_code", "trade_date"])
    result = result.sort_values(["trade_date", "con_code"])
    result.to_csv(MEMBERS_FILE, index=False, encoding="utf-8")
    log.info(
        "成分股权重下载完成: %d 条，%d 个调仓日，%s ~ %s",
        len(result),
        result["trade_date"].nunique(),
        result["trade_date"].min(),
        result["trade_date"].max(),
    )


def dl_stock_basic():
    """股票基本信息：上市/退市日期、名称，用于过滤新股（<6个月）和退市股。"""
    out = RAW_DIR / "stock_basic.csv"
    if out.exists():
        log.info("stock_basic.csv 已存在，跳过")
        return
    # list_status='' 同时拉在市、退市、暂停上市
    df = _call(pro.stock_basic, list_status="", exchange="")
    if not df.empty:
        df.to_csv(out, index=False, encoding="utf-8")
        log.info(f"stock_basic: {len(df)} 条")


def dl_namechange():
    """
    股票曾用名历史，用于逐日构造 ST/非ST 标记。
    字段: ts_code, name, start_date, end_date, ann_date, change_reason
    """
    out = RAW_DIR / "namechange.csv"
    if out.exists():
        log.info("namechange.csv 已存在，跳过")
        return
    df = _call(pro.namechange)
    if not df.empty:
        df.to_csv(out, index=False, encoding="utf-8")
        log.info(f"namechange: {len(df)} 条")


def dl_industry():
    """
    行业分类：申万2021（SW2021）一级行业，31个行业。

    F1-002: 本项目统一使用 SW2021，不再尝试 CITICS 并降级。
    输出文件：industry_sw2021.csv（旧文件 industry_citics.csv 不再生成）。

    流程：
      1. index_classify 获取 SW2021 一级行业树
      2. 对每个一级行业用 index_member 拉成员股
    注：此数据为静态快照，不含历史变更，因子中性化精度可接受。
    """
    out = RAW_DIR / "industry_sw2021.csv"
    if out.exists():
        log.info("industry_sw2021.csv 已存在，跳过")
        return

    src = "SW2021"
    cls_df = _call(pro.index_classify, level="L1", src=src)
    if cls_df.empty:
        log.error("SW2021 行业树拉取失败，跳过该模块")
        return

    cls_df.to_csv(RAW_DIR / f"industry_classify_{src}.csv", index=False, encoding="utf-8")
    log.info(f"行业分类源: {src}，共 {len(cls_df)} 个一级行业")

    # 列名自适应（Tushare 不同版本列名格式不统一）
    idx_col = next((c for c in cls_df.columns
                    if "index_code" in c.lower() or "indexcode" in c.lower()), None)
    name_col = next((c for c in cls_df.columns
                     if "industry" in c.lower() and "name" in c.lower()), None)
    if idx_col is None:
        log.error(f"index_classify 列名异常: {list(cls_df.columns)}，无法解析行业代码列")
        return

    all_members: list[pd.DataFrame] = []
    for _, row in tqdm(cls_df.iterrows(), total=len(cls_df), desc="SW2021 行业成员股"):
        idx_code = row[idx_col]
        ind_name = row[name_col] if name_col else ""
        mem_df = _call(pro.index_member, index_code=idx_code)
        if not mem_df.empty:
            mem_df["industry_code"] = idx_code
            mem_df["industry_name"] = ind_name
            mem_df["industry_src"] = src  # 始终为 SW2021
            all_members.append(mem_df)

    if not all_members:
        log.warning("未获取到任何行业成员股数据")
        return

    result = pd.concat(all_members, ignore_index=True)
    result.to_csv(out, index=False, encoding="utf-8")
    log.info(f"industry: {len(result)} 条（{src}，输出到 {out.name}）")


def dl_index_daily():
    """
    中证500全收益基准日行情。
    警告：必须是全收益指数（含分红再投资），用价格指数会产生 2-3% 虚假超额。
    若 test 阶段 T5 未通过，此模块会失败并提示修改 INDEX_TOTAL_RETURN_CODE。
    """
    out = RAW_DIR / "index_daily.csv"

    if out.exists():
        # 检查是否需要向前补历史
        try:
            existing = pd.read_csv(out, usecols=["trade_date"], dtype=str)
            existing_min = existing["trade_date"].dropna().str[:8].min()
            if existing_min <= MARKET_START:
                log.info("index_daily.csv 已覆盖 MARKET_START（%s），跳过", MARKET_START)
                return
            # 需要补历史：下载 MARKET_START ~ existing_min 并合并
            log.info(
                "index_daily.csv 最早日期 %s > %s，向前补历史...",
                existing_min, MARKET_START,
            )
            df_new = _call(pro.index_daily, ts_code=INDEX_TOTAL_RETURN_CODE,
                           start_date=MARKET_START, end_date=existing_min)
            if not df_new.empty:
                old_df = pd.read_csv(out, dtype=str)
                combined = pd.concat([old_df, df_new.astype(str)], ignore_index=True)
                combined = combined.drop_duplicates(subset=["trade_date"])
                combined = combined.sort_values("trade_date").reset_index(drop=True)
                combined.to_csv(out, index=False, encoding="utf-8")
                log.info(
                    "index_daily.csv 补历史完成: %s ~ %s（共 %d 条）",
                    combined["trade_date"].min(), combined["trade_date"].max(), len(combined),
                )
            else:
                log.warning("index_daily.csv 补历史拉取为空，跳过（指数代码：%s）", INDEX_TOTAL_RETURN_CODE)
        except Exception as exc:
            log.warning("index_daily.csv 补历史失败: %s，跳过", exc)
        return

    # 文件不存在，全量下载
    df = _call(pro.index_daily, ts_code=INDEX_TOTAL_RETURN_CODE,
               start_date=MARKET_START, end_date=MARKET_END)
    if df.empty:
        log.error(
            f"全收益指数 {INDEX_TOTAL_RETURN_CODE} 拉取失败！\n"
            "  请先运行 test 命令查看正确代码，\n"
            "  修改脚本顶部 INDEX_TOTAL_RETURN_CODE 后重新运行。"
        )
        return

    df.to_csv(out, index=False, encoding="utf-8")
    log.info(
        f"index_daily({INDEX_TOTAL_RETURN_CODE}): {len(df)} 条，"
        f"{df['trade_date'].min()} ~ {df['trade_date'].max()}"
    )


def dl_daily_quote(stock_list: list[str]):
    """
    股票日行情（未复权）。
    字段: ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount
    后复权价 = close × adj_factor，在 csv_to_parquet.py 阶段计算。
    """
    _dl_stock_range(
        "daily_quote", "日行情", stock_list, pro.daily,
        MARKET_START, "trade_date", ["trade_date"],
    )


def dl_adj_factor(stock_list: list[str]):
    """复权因子，用于计算后复权收益率。"""
    _dl_stock_range(
        "adj_factor", "复权因子", stock_list, pro.adj_factor,
        MARKET_START, "trade_date", ["trade_date"],
    )


def dl_daily_basic(stock_list: list[str]):
    """
    每日基础指标：估值倍数 + 自由流通股本。
    关键字段:
        pe_ttm, pb, ps_ttm, dv_ttm   → 价值因子原材料
        free_share / float_share / total_share → 流通股本（中性化 + PMO 口径）
        turnover_rate_f               → 换手率因子（基于自由流通股）
        circ_mv                       → 流通市值（备用）
    """
    _dl_stock_range(
        "daily_basic", "每日指标", stock_list, pro.daily_basic,
        MARKET_START, "trade_date", ["trade_date"],
    )


def dl_suspend(stock_list: list[str]):
    """停牌记录。组合优化时停牌股权重锁定为前一期值，不参与再平衡。"""
    _dl_stock_range(
        "suspend", "停牌记录", stock_list, pro.suspend_d,
        MARKET_START, "trade_date", ["trade_date"],
    )


def dl_limit_list(trading_days: list[str]):
    """
    涨跌停列表（每个交易日一个文件）。
    用途：涨停股不能买入，跌停股不能卖出（单边约束）。
    API 名称在不同 Tushare 版本中不同，自动探测 limit_list_d / limit_list。
    """
    subdir = "limit_list"
    todo = [d for d in trading_days if not _done(subdir, d)]
    log.info(f"limit_list: {len(todo)}/{len(trading_days)} 个交易日待下载")

    limit_func = None
    for fname in ["limit_list_d", "limit_list"]:
        if hasattr(pro, fname):
            limit_func = getattr(pro, fname)
            log.info(f"涨跌停 API: pro.{fname}")
            break
    if limit_func is None:
        log.error("未找到可用的涨跌停 API，跳过该模块")
        log.error("  → 后续可从 daily 数据推导：pct_chg ≈ ±10% 且 open == close")
        return

    for trade_date in tqdm(todo, desc="涨跌停"):
        df = _call(limit_func, trade_date=trade_date)
        _save(subdir, trade_date, df)


def dl_financial(stock_list: list[str]):
    """
    财务三表 + 综合指标（PIT 核心）。

    PIT 使用规则：
        T 日可用数据 = ann_date <= T 的所有记录中，同一 end_date 取最新 ann_date（最新修正版本）

    字段说明：
        ann_date  = 公告日期（对应 CSMAR Annodt），PIT 时间戳
        end_date  = 报告期（对应 CSMAR Accper）
        report_type='1' = 合并报表（年报/季报均包含）

    FINANCIAL_START（来自 cfg.EARLY_START）确保训练期起点可用的历史报表都被覆盖。
    """
    # (subdir, api_func, report_type kwarg or None)
    financial_modules = [
        ("financial_income",    pro.income,          "1"),
        ("financial_balance",   pro.balancesheet,    "1"),
        ("financial_cashflow",  pro.cashflow,        "1"),
        ("financial_indicator", pro.fina_indicator,  None),
    ]

    for subdir, func, rtype in financial_modules:
        extra = {"report_type": rtype} if rtype else {}
        _dl_stock_range(
            subdir, subdir, stock_list, func,
            FINANCIAL_START, "ann_date", ["ann_date", "end_date"],
            sleep=SLEEP_FINANCIAL, workers=FINANCIAL_WORKERS, **extra,
        )


# ============================================================
# 主入口
# ============================================================

RUN_ORDER = [
    "indexweight",                                      # 前置依赖：成分股权重 CSV，其余模块均依赖它
    "basic", "namechange", "industry", "index",
    "daily", "adjfactor", "dailybasic",
    "suspend", "limitlist", "financial",
]


def main(targets: list[str] | None = None):
    # indexweight 是所有其他模块的前置依赖；文件不存在时先下载再继续
    if not MEMBERS_FILE.exists():
        log.warning("成分股权重文件不存在，自动执行 indexweight 模块...")
        dl_index_weight()
        if not MEMBERS_FILE.exists():
            log.error("成分股权重下载失败，无法继续。请先手动运行: python %s indexweight", __file__)
            return
    stocks = _stock_list()
    log.info(f"成分股（去重去空）: {len(stocks)} 只")

    tdays = _trading_days()
    if tdays:
        log.info(f"交易日: {len(tdays)} 天（{tdays[0]} ~ {tdays[-1]}）")
    else:
        log.error("交易日历获取失败，suspendlist / limitlist 模块无法运行")

    dispatch = {
        "indexweight": dl_index_weight,
        "basic":       dl_stock_basic,
        "namechange":  dl_namechange,
        "industry":    dl_industry,
        "index":       dl_index_daily,
        "daily":       lambda: dl_daily_quote(stocks),
        "adjfactor":   lambda: dl_adj_factor(stocks),
        "dailybasic":  lambda: dl_daily_basic(stocks),
        "suspend":     lambda: dl_suspend(stocks),
        "limitlist":   lambda: dl_limit_list(tdays),
        "financial":   lambda: dl_financial(stocks),
    }

    run = [m for m in RUN_ORDER if (targets is None or m in targets)]

    for mod in run:
        log.info(f"\n{'=' * 55}\n  模块: {mod}\n{'=' * 55}")
        dispatch[mod]()

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
            log.error("连接测试未全部通过，请解决失败项后重试")
            sys.exit(1)
        main()

    elif cmd in RUN_ORDER:
        main(targets=[cmd])

    else:
        print(f"未知命令: {cmd}")
        print(f"可用命令: test | all | {' | '.join(RUN_ORDER)}")
        sys.exit(1)
