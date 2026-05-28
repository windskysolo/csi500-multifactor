"""
raw CSV → processed Parquet 主转换脚本

用法：
    python scripts/csv_to_parquet.py all          # 顺序执行全部模块
    python scripts/csv_to_parquet.py daily        # M1 日行情 + M2 基础指标
    python scripts/csv_to_parquet.py index        # M3 基准指数
    python scripts/csv_to_parquet.py industry     # M4 行业归属
    python scripts/csv_to_parquet.py financial    # M5-M8 财务 PIT 模块
    python scripts/csv_to_parquet.py status       # M9-M11 状态标记
    python scripts/csv_to_parquet.py universe     # M12 可投资域（依赖 status）
    python scripts/csv_to_parquet.py alternative  # M13-M24 备选数据（12 个新增接口）

执行顺序（存在依赖关系的在后）：
    M1(daily_quote) → M2(daily_basic) → M3(index_quote) → M4(industry)
    → M5(financial_pit) → M6(indicator_pit) → M7(holder_pit) → M8(dividend_pit)
    → M9(margin) → M10(moneyflow) → M11(stock_status) → M12(index_member)
    → M13-M24(alternative)
  M4 建议在 M1 之后执行，因为它会优先从 daily_quote.parquet 读取股票-日期对。
  M12 依赖 M11(stock_status)，all 模式下顺序已保证。
  运行 all 时最后生成 reports/processed_coverage_report.md 验收报告。
"""

import sys
import logging
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from tqdm import tqdm

# --------------------------------------------------------------------------
# 路径设置：让脚本在任意工作目录下都能找到 src/
# --------------------------------------------------------------------------
_SCRIPT_DIR  = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))
from src import config as cfg

# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ==========================================================================
# 通用工具函数
# ==========================================================================

def _parse_yyyymmdd(series: pd.Series) -> pd.Series:
    """把 YYYYMMDD 整数或字符串列转为 datetime64[ns]；NaN/空值返回 NaT。"""
    return pd.to_datetime(series.astype(str).str[:8], format="%Y%m%d", errors="coerce")


def _read_csv_safe(file_path: Path) -> pd.DataFrame | None:
    """读取单个 CSV；空文件或读取失败时返回 None。"""
    try:
        df = pd.read_csv(file_path, encoding="utf-8")
        if df.empty:
            return None
        return df
    except pd.errors.EmptyDataError:
        return None
    except Exception as exc:
        log.warning("读取失败 %s: %s", file_path.name, exc)
        return None


def _load_csv_dir(directory: Path, desc: str = "") -> pd.DataFrame:
    """
    并行读取目录下所有 *.csv 文件，返回纵向拼接的 DataFrame。

    Args:
        directory: CSV 文件所在目录
        desc: tqdm 显示名称
    Returns:
        拼接后的 DataFrame（忽略原始索引）
    Raises:
        ValueError: 目录内没有可读数据
    """
    files = sorted(directory.glob("*.csv"))
    if not files:
        raise ValueError(f"目录为空: {directory}")

    results: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=cfg.MAX_READ_WORKERS) as pool:
        futures = {pool.submit(_read_csv_safe, f): f for f in files}
        for fut in tqdm(as_completed(futures), total=len(files),
                        desc=desc or directory.name, leave=False):
            df = fut.result()
            if df is not None:
                results.append(df)

    if not results:
        raise ValueError(f"目录内无有效数据: {directory}")
    return pd.concat(results, ignore_index=True)


def _filter_date_range(
    df: pd.DataFrame,
    date_col: str = "trade_date",
    start: pd.Timestamp = cfg.MARKET_START,
    end: pd.Timestamp = cfg.MARKET_END,
) -> pd.DataFrame:
    return df[(df[date_col] >= start) & (df[date_col] <= end)].copy()


def _save_parquet(df: pd.DataFrame, output_path: Path, label: str) -> None:
    """保存 Parquet 并打印摘要。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=True)
    log.info("%s → %s  shape=%s", label, output_path.name, df.shape)


def _load_stock_pairs() -> pd.DataFrame:
    """
    返回 daily_quote 中所有 (ts_code, trade_date) 对（market 日期范围内）。
    优先读取已生成的 daily_quote.parquet；否则从 raw 文件回退（慢）。
    """
    proc = cfg.DATA_PROC / "daily_quote.parquet"
    if proc.exists():
        idx = pd.read_parquet(proc, columns=[]).index
        return idx.to_frame(index=False)

    log.info("daily_quote.parquet 不存在，从 raw 文件读取股票-日期对（较慢）…")
    files = sorted((cfg.DATA_RAW / "daily_quote").glob("*.csv"))
    dfs = []
    for f in tqdm(files, desc="读取 trade_date", leave=False):
        tmp = pd.read_csv(f, usecols=["ts_code", "trade_date"])
        dfs.append(tmp)
    combined = pd.concat(dfs, ignore_index=True)
    combined["trade_date"] = _parse_yyyymmdd(combined["trade_date"])
    return _filter_date_range(combined, "trade_date")[
        ["ts_code", "trade_date"]
    ].drop_duplicates()


def _build_pit_table(
    df: pd.DataFrame,
    pit_date_col: str,
    end_date_col: str = "end_date",
) -> pd.DataFrame:
    """
    对同一 (ts_code, end_date) 的多次修订，保留 pit_date 最大的版本（全局去重）。

    设计说明（已知限制）：
        这是简化版 PIT——先全局去重，再由 loader 按 pit_date <= T 过滤。
        若某报告期的修订日 > 首次公告日，修订前的 T 查询将得到空结果（保守）。
        工业级实现应保留所有版本并在 loader 中逐 T 去重；此处为学习项目可接受。

    含 update_flag 列时，同 pit_date 下优先保留 update_flag=1（修订版）。

    Args:
        df: 含 ts_code、end_date_col、pit_date_col 的 DataFrame
        pit_date_col: PIT 时间戳列名（datetime64）
        end_date_col: 报告期列名，默认 "end_date"
    Returns:
        每 (ts_code, end_date) 只保留最新版本的 DataFrame
    """
    sort_cols = [pit_date_col]
    if "update_flag" in df.columns:
        sort_cols.append("update_flag")
    return (
        df.sort_values(sort_cols)
        .groupby(["ts_code", end_date_col], as_index=False)
        .last()
    )


def _dedupe_same_pit_version(
    df: pd.DataFrame,
    pit_date_col: str,
    end_date_col: str = "end_date",
) -> pd.DataFrame:
    """
    仅去重同一 PIT 日期下的重复版本，保留不同 pit_date 的历史修订版本。

    用途：严格 PIT 数据应在 loader 查询 T 日时再做
    pit_date <= T 后的最新版本选择，不能在转换阶段全局删掉旧版本。
    """
    sort_cols = [pit_date_col]
    if "update_flag" in df.columns:
        sort_cols.append("update_flag")
    return (
        df.sort_values(sort_cols)
        .groupby(["ts_code", pit_date_col, end_date_col], as_index=False)
        .last()
    )


# ==========================================================================
# M1：日行情 → daily_quote.parquet
# ==========================================================================

def process_daily_quote() -> None:
    """
    合并 raw/daily_quote + raw/adj_factor，计算后复权价和收益率。

    输出字段：
        open, high, low, close, pre_close, pct_chg, vol, amount,
        adj_factor, close_adj, open_adj, ret
    索引：(trade_date datetime64, ts_code str)，已排序
    """
    log.info("=== M1: daily_quote ===")

    # ---- 并行加载原始文件 ----
    dq = _load_csv_dir(cfg.DATA_RAW / "daily_quote", "读取 daily_quote")
    af = _load_csv_dir(cfg.DATA_RAW / "adj_factor",  "读取 adj_factor")

    # ---- 日期解析 ----
    dq["trade_date"] = _parse_yyyymmdd(dq["trade_date"])
    af["trade_date"] = _parse_yyyymmdd(af["trade_date"])

    # ---- 日期过滤（先过滤减少内存压力）----
    dq = _filter_date_range(dq)
    af = _filter_date_range(af)

    # ---- 去除零成交行（节假日误入数据）----
    zero_vol = (dq["vol"] == 0) & (dq["amount"] == 0)
    n_zero = zero_vol.sum()
    if n_zero:
        log.info("  移除零成交行: %d 条", n_zero)
    dq = dq[~zero_vol].copy()

    # ---- 合并复权因子（左连接，以行情为主表）----
    df = dq.merge(
        af[["ts_code", "trade_date", "adj_factor"]],
        on=["ts_code", "trade_date"],
        how="left",
    )

    missing_af = df["adj_factor"].isna().sum()
    if missing_af:
        log.warning("  复权因子缺失: %d 行（将导致 close_adj/open_adj 为 NaN）", missing_af)

    # ---- 后复权价 & 收益率 ----
    df["close_adj"] = (df["close"] * df["adj_factor"]).astype("float64")
    df["open_adj"]  = (df["open"]  * df["adj_factor"]).astype("float64")
    # 后复权收益：分股票对 close_adj 做 pct_change，与价格口径严格一致。
    # pct_chg 在除权日与 close_adj.pct_change() 存在系统性偏差，不能用作后复权收益。
    df = df.sort_values(["ts_code", "trade_date"])
    df["ret"] = (
        df.groupby("ts_code", sort=False)["close_adj"]
        .pct_change(fill_method=None)
        .astype("float64")
    )

    # ---- 类型规范 ----
    df["ts_code"] = df["ts_code"].astype(str)
    float_cols = [
        "open", "high", "low", "close", "pre_close",
        "pct_chg", "vol", "amount", "adj_factor",
    ]
    for col in float_cols:
        df[col] = df[col].astype("float64")

    # ---- 输出列（丢弃 change 原始绝对变动值，用 ret 代替）----
    out_cols = [
        "trade_date", "ts_code",
        "open", "high", "low", "close", "pre_close",
        "pct_chg", "vol", "amount",
        "adj_factor", "close_adj", "open_adj", "ret",
    ]
    df = df[out_cols].set_index(["trade_date", "ts_code"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "daily_quote.parquet", "M1")

    # ---- 验证 ----
    n_rows = len(df)
    log.info("  行数: %d（预期 ~130万，因成分股含部分近年上市股票）", n_rows)
    assert n_rows > 1_000_000, f"行数异常偏少: {n_rows}"

    ret_p01 = float(df["ret"].quantile(0.01))
    ret_p99 = float(df["ret"].quantile(0.99))
    log.info("  ret p1/p99: %.4f / %.4f（预期在 ±11%% 以内）", ret_p01, ret_p99)
    assert ret_p99 <= 0.11,  f"ret p99 超限: {ret_p99:.4f}"
    assert ret_p01 >= -0.11, f"ret p1 超限: {ret_p01:.4f}"

    af_null_rate = df["adj_factor"].isna().mean()
    assert af_null_rate < 0.01, f"复权因子空值率过高: {af_null_rate:.2%}"

    log.info("  M1 验证通过 ✓")


# ==========================================================================
# M2：每日基础指标 → daily_basic.parquet
# ==========================================================================

def process_daily_basic() -> None:
    """
    加载 raw/daily_basic，计算自由流通市值（中性化核心字段）。

    输出字段：
        close, turnover_rate, turnover_rate_f, volume_ratio,
        pe, pe_ttm, pb, ps_ttm, dv_ttm,
        free_share, total_mv, circ_mv,
        free_float_mv, log_free_float_mv
    索引：(trade_date datetime64, ts_code str)，已排序

    单位说明：
        free_share   单位：万股
        free_float_mv = free_share × close × 10000  单位：元
        total_mv / circ_mv  单位：万元（Tushare 原始单位）
    """
    log.info("=== M2: daily_basic ===")

    db = _load_csv_dir(cfg.DATA_RAW / "daily_basic", "读取 daily_basic")

    db["trade_date"] = _parse_yyyymmdd(db["trade_date"])
    db = _filter_date_range(db)

    # ---- 自由流通市值（元）：用于因子中性化 ----
    # free_share 单位为万股，×10000 转为股，再×close(元/股) = 元
    db["free_float_mv"] = (db["free_share"] * db["close"] * 10_000).astype("float64")

    # 对数形式（中性化回归使用）
    # clip 防止极端小值导致 -inf；ln(1e7) ≈ 16.1，ln(1e12) ≈ 27.6 为合理范围
    db["log_free_float_mv"] = np.log(db["free_float_mv"].clip(lower=1.0))

    # ---- 类型规范 ----
    db["ts_code"] = db["ts_code"].astype(str)
    keep_cols = [
        "trade_date", "ts_code",
        "close", "turnover_rate", "turnover_rate_f", "volume_ratio",
        "pe", "pe_ttm", "pb", "ps_ttm", "dv_ttm",
        # 股本：total_share/float_share 用于 PMO/ABN TURN 口径；free_share 用于中性化
        "total_share", "float_share", "free_share",
        "total_mv", "circ_mv",
        "free_float_mv", "log_free_float_mv",
    ]
    float_cols = [c for c in keep_cols if c not in ("trade_date", "ts_code")]
    for col in float_cols:
        if col in db.columns:
            db[col] = db[col].astype("float64")

    df = db[keep_cols].set_index(["trade_date", "ts_code"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "daily_basic.parquet", "M2")

    # ---- 验证 ----
    assert (df["free_float_mv"] <= 0).sum() == 0, "free_float_mv 含零/负值"

    # NaN 率检查：自由流通市值是中性化核心变量，缺失需明确记录
    fs_nan  = int(df["free_share"].isna().sum())
    log_nan = int(df["log_free_float_mv"].isna().sum())
    fs_nan_rate  = fs_nan  / len(df)
    log_nan_rate = log_nan / len(df)
    log.info("  free_share 缺失: %d 行（%.4f%%）", fs_nan, fs_nan_rate * 100)
    log.info("  log_free_float_mv 非有限值: %d 行（%.4f%%）", log_nan, log_nan_rate * 100)
    if fs_nan > 0:
        log.warning(
            "  free_share 存在 %d 条缺失，对应 log_free_float_mv 为 NaN，"
            "中性化回归时该截面样本会减少，需在因子预处理层追踪",
            fs_nan,
        )
    assert log_nan_rate < 0.01, (
        f"log_free_float_mv 缺失率 {log_nan_rate:.2%} 超过 1%，"
        "请检查 free_share 字段或补充数据"
    )

    ffmv_log_min = float(df["log_free_float_mv"].dropna().min())
    ffmv_log_max = float(df["log_free_float_mv"].dropna().max())
    log.info(
        "  log_free_float_mv 范围: %.2f ~ %.2f（参考: ln(1亿)≈18.4, ln(1万亿)≈27.6）",
        ffmv_log_min, ffmv_log_max,
    )
    log.info("  M2 验证通过 ✓")


# ==========================================================================
# M3：基准指数 → index_quote.parquet
# ==========================================================================

def process_index_quote() -> None:
    """
    加载中证500全收益指数行情，验证代码，计算日收益率和净值序列。

    输出字段：trade_date, close, pct_chg, index_ret, nav
    索引：trade_date datetime64（单层，非 MultiIndex）

    注意：必须使用 H00905.CSI（全收益指数，含分红再投资），
         使用价格指数（000905.SH）会虚增约 2-3% 年化超额收益。
    """
    log.info("=== M3: index_quote ===")

    idx = pd.read_csv(cfg.DATA_RAW / "index_daily.csv", encoding="utf-8")
    idx["trade_date"] = _parse_yyyymmdd(idx["trade_date"])
    idx = _filter_date_range(idx)

    # 强制验证：必须是全收益指数
    codes = idx["ts_code"].unique()
    assert list(codes) == ["H00905.CSI"], (
        f"index_daily.csv 中发现非预期指数代码: {codes}。"
        "必须使用 H00905.CSI（中证500全收益指数）。"
    )

    idx = idx.sort_values("trade_date").set_index("trade_date")

    # 与股票交易日历对齐。若指数源缺少个别交易日，用下一条指数记录的
    # pre_close 反推该日 close，避免回测中策略与基准日历错位。
    stock_pairs = _load_stock_pairs()
    trading_dates = pd.Index(sorted(stock_pairs["trade_date"].unique()), name="trade_date")
    idx = idx.reindex(trading_dates)
    missing_close = idx["close"].isna()
    if missing_close.any():
        log.warning(
            "  基准指数缺失 %d 个股票交易日，将用下一交易日 pre_close 补齐: %s",
            int(missing_close.sum()),
            [d.date() for d in idx.index[missing_close]],
        )
        idx["close"] = idx["close"].fillna(idx["pre_close"].bfill())

    # 以对齐后的 close 重算日收益；首日为样本起点，收益设为 0，NAV 起点为 1。
    idx["index_ret"] = idx["close"].pct_change().fillna(0.0).astype("float64")
    idx["pct_chg"] = (idx["index_ret"] * 100).astype("float64")
    idx["nav"] = (1 + idx["index_ret"]).cumprod().astype("float64")

    float_cols = ["close", "pct_chg", "index_ret", "nav"]
    for col in float_cols:
        idx[col] = idx[col].astype("float64")

    out_cols = ["close", "pct_chg", "index_ret", "nav"]
    df = idx[out_cols].sort_index()

    _save_parquet(df, cfg.DATA_PROC / "index_quote.parquet", "M3")

    # ---- 验证 ----
    assert len(df) > 2000, f"指数行数偏少: {len(df)}"
    assert df.index.equals(trading_dates), "基准指数日历未与股票交易日历完全对齐"
    assert abs(float(df["nav"].iloc[0]) - 1.0) < 1e-12, "基准 NAV 首日不是 1"

    total_ret = float(df["nav"].iloc[-1])
    log.info(
        "  全收益基准累计涨幅: %.1f%%  (nav 从 1.0 → %.4f)",
        (total_ret - 1) * 100, total_ret,
    )
    # 全收益指数 10 年累计应在合理范围（不应为负或超过 10 倍）
    assert 0.5 < total_ret < 10.0, f"基准净值异常: {total_ret:.4f}"
    log.info("  M3 验证通过 ✓")


# ==========================================================================
# M4：行业归属 → industry.parquet
# ==========================================================================

def process_industry() -> None:
    """
    将 raw/industry_sw2021.csv（SW2021 一级行业）展开为每日行业归属。

    逻辑：对每个 (ts_code, trade_date) 对，选择满足
    in_date ≤ trade_date < out_date_eff 的有效行业记录；如多条有效，
    取 in_date 最新的一条。找不到行业的股票标记为"未分类"。

    输出字段：industry_code, industry_name（SW2021 一级）
    索引：(trade_date datetime64, ts_code str)，已排序
    """
    log.info("=== M4: industry ===")

    # ---- 加载行业历史成员表 ----
    # 优先读取新下载链路生成的 industry_sw2021.csv；兼容旧文件 industry_citics.csv
    _sw2021_path = cfg.DATA_RAW / "industry_sw2021.csv"
    _citics_path = cfg.DATA_RAW / "industry_citics.csv"
    if _sw2021_path.exists():
        ind = pd.read_csv(_sw2021_path, encoding="utf-8")
    elif _citics_path.exists():
        log.warning(
            "industry_sw2021.csv 不存在，降级使用 industry_citics.csv（旧下载链路）。"
            "建议重新运行 download_tushare.py 以生成规范文件。"
        )
        ind = pd.read_csv(_citics_path, encoding="utf-8")
    else:
        raise FileNotFoundError(
            f"行业文件不存在，请先运行 download_tushare.py: {_sw2021_path}"
        )
    ind = ind[["con_code", "in_date", "out_date", "industry_code", "industry_name"]].copy()
    ind = ind.rename(columns={"con_code": "ts_code"})

    # ---- 日期解析 ----
    ind["in_date"] = _parse_yyyymmdd(ind["in_date"])
    # out_date 含 NaN（当前仍在成分中）；先转为 float 再转 int 去小数点
    def _parse_out_date(val):
        if pd.isna(val):
            return pd.NaT
        return pd.to_datetime(str(int(float(val))), format="%Y%m%d")

    ind["out_date"] = ind["out_date"].apply(_parse_out_date)
    # 用远期日期填充 NaT，避免 merge 后需要单独判断 NaT
    FUTURE = pd.Timestamp("2099-12-31")
    ind["out_date_eff"] = ind["out_date"].fillna(FUTURE)

    # ---- 获取所有 (ts_code, trade_date) 对 ----
    pairs = _load_stock_pairs()
    log.info("  股票-日期对: %d 条（%d 只股票）",
             len(pairs), pairs["ts_code"].nunique())

    # ---- 按股票逐组做有效区间覆盖 ----
    ind_by_stock = {
        ts: grp.sort_values("in_date").reset_index(drop=True)
        for ts, grp in ind.groupby("ts_code")
    }

    chunks: list[pd.DataFrame] = []
    for ts_code, pairs_grp in tqdm(
        pairs.groupby("ts_code"), desc="展开行业", total=pairs["ts_code"].nunique()
    ):
        ind_grp = ind_by_stock.get(ts_code)

        if ind_grp is None:
            # 该股无行业记录，全标记为未分类
            chunk = pairs_grp[["ts_code", "trade_date"]].copy()
            chunk["industry_code"] = cfg.INDUSTRY_UNCLASSIFIED_CODE
            chunk["industry_name"] = cfg.INDUSTRY_UNCLASSIFIED_NAME
            chunks.append(chunk)
            continue

        chunk = pairs_grp[["ts_code", "trade_date"]].sort_values("trade_date").copy()
        chunk["industry_code"] = cfg.INDUSTRY_UNCLASSIFIED_CODE
        chunk["industry_name"] = cfg.INDUSTRY_UNCLASSIFIED_NAME

        # 按 in_date 升序覆盖；重叠区间自然保留最新 in_date 的行业。
        for _, row in ind_grp.iterrows():
            if pd.isna(row["in_date"]):
                continue
            mask = (
                (chunk["trade_date"] >= row["in_date"])
                & (chunk["trade_date"] < row["out_date_eff"])
            )
            if mask.any():
                chunk.loc[mask, "industry_code"] = row["industry_code"]
                chunk.loc[mask, "industry_name"] = row["industry_name"]

        chunks.append(chunk[["ts_code", "trade_date", "industry_code", "industry_name"]])

    result = pd.concat(chunks, ignore_index=True)

    # ---- 最终填充（防御性）----
    result["industry_name"] = result["industry_name"].fillna(cfg.INDUSTRY_UNCLASSIFIED_NAME)
    result["industry_code"] = result["industry_code"].fillna(cfg.INDUSTRY_UNCLASSIFIED_CODE)

    # ---- 整理输出 ----
    result["industry_code"] = result["industry_code"].astype(str)
    result["industry_name"] = result["industry_name"].astype(str)
    out_cols = ["trade_date", "ts_code", "industry_code", "industry_name"]
    df = result[out_cols].set_index(["trade_date", "ts_code"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "industry.parquet", "M4")

    # ---- 验证 ----
    n_total = len(pairs)
    n_classified = (df["industry_name"] != cfg.INDUSTRY_UNCLASSIFIED_NAME).sum()
    coverage = n_classified / n_total
    # 训练期扩展至 2012 后，SW2021 对 2012-2015 早期覆盖不完整属预期，整体约 92-93%；
    # 阈值从 0.95 调低至 0.88，仍能检测数据严重缺失（如 index_member 未对齐）。
    log.info("  行业归属覆盖率: %.2f%%（预期 > 88%%，2012 起扩展口径）", coverage * 100)
    assert coverage > 0.88, f"行业覆盖率过低: {coverage:.2%}"

    n_industries = df["industry_name"].nunique()
    log.info("  行业数量: %d（SW2021 预期 31 + 未分类）", n_industries)
    assert 30 <= n_industries <= 33, f"行业数量异常: {n_industries}"

    log.info("  M4 验证通过 ✓")


# ==========================================================================
# M5：财务三表 PIT → financial_pit.parquet
# ==========================================================================

def process_financial_pit() -> None:
    """
    合并利润表/资产负债表/现金流量表，生成 PIT 对齐的财务宽表。

    PIT 时间戳：f_ann_date（首次披露日）；缺失时退回 ann_date。
    合并口径：仅保留 report_type=1（合并报表），避免母公司报表重复。
    pit_date 取三表最大值（保守：三表全部可用后才使用该记录）。

    输出字段：
        revenue, total_profit, n_income, basic_eps, rd_exp（利润表）
        total_assets, total_liab, total_hldr_eqy_exc_min_int, money_cap（资产负债表）
        n_cashflow_act, free_cashflow（现金流量表）
    索引：(pit_date datetime64, ts_code str, end_date datetime64)，已排序
    """
    log.info("=== M5: financial_pit ===")

    def _load_one(directory: Path, desc: str, keep_fields: list[str]) -> pd.DataFrame:
        """
        加载单张财务表，过滤合并报表，保留 PIT 修订历史，返回需要的列。

        Args:
            directory: CSV 所在目录
            desc: tqdm 显示标签
            keep_fields: 最终保留的业务字段（ts_code/pit_date/end_date 自动保留）
        """
        raw = _load_csv_dir(directory, desc)
        raw["ann_date"] = _parse_yyyymmdd(raw["ann_date"])
        raw["end_date"] = _parse_yyyymmdd(raw["end_date"])

        if "f_ann_date" in raw.columns:
            raw["f_ann_date"] = _parse_yyyymmdd(raw["f_ann_date"])
            raw["pit_date"] = raw["f_ann_date"].fillna(raw["ann_date"])
        else:
            raw["pit_date"] = raw["ann_date"]

        # 只保留合并报表
        if "report_type" in raw.columns:
            raw = raw[raw["report_type"] == 1].copy()

        # 提前列筛选减内存：核心列 + 业务字段 + update_flag（用于同日版本去重）
        core_cols = ["ts_code", "pit_date", "end_date"]
        extra = [c for c in keep_fields if c not in core_cols and c in raw.columns]
        if "update_flag" in raw.columns:
            extra.append("update_flag")
        raw = raw[core_cols + extra].copy()

        # 只去重同一 pit_date 的重复行；不同 pit_date 的修订历史保留到 loader 查询时处理。
        raw = _dedupe_same_pit_version(raw, "pit_date")

        # 最终列筛选（丢弃 update_flag）
        final = core_cols + [c for c in keep_fields if c not in core_cols and c in raw.columns]
        return raw[final].copy()

    income_fields  = [
        "revenue", "total_revenue", "operate_profit", "total_profit", "n_income",
        "basic_eps", "rd_exp",
        "oper_cost", "sell_exp", "admin_exp", "fin_exp", "income_tax",
        "ebit", "ebitda",
    ]
    balance_fields = [
        "total_assets", "total_liab", "total_hldr_eqy_exc_min_int", "money_cap",
        "inventories", "accounts_receiv", "prepayment", "total_cur_assets",
        "fix_assets", "cip", "intan_assets", "goodwill",
        "st_borr", "lt_borr", "notes_payable", "bond_payable",
    ]
    cf_fields      = [
        "n_cashflow_act", "free_cashflow",
        "n_cashflow_inv_act", "n_cash_flows_fnc_act",
    ]

    inc = _load_one(cfg.DATA_RAW / "financial_income",   "读取利润表",     income_fields)
    bal = _load_one(cfg.DATA_RAW / "financial_balance",  "读取资产负债表", balance_fields)
    cf  = _load_one(cfg.DATA_RAW / "financial_cashflow", "读取现金流量表", cf_fields)

    # 三表分别重命名 pit_date，以便外连接后追踪来源
    inc = inc.rename(columns={"pit_date": "_pit_inc"})
    bal = bal.rename(columns={"pit_date": "_pit_bal"})
    cf  = cf.rename(columns={"pit_date": "_pit_cf"})

    # 外连接：保留任意表中出现的 (ts_code, end_date) 组合
    df = (
        inc
        .merge(bal, on=["ts_code", "end_date"], how="outer")
        .merge(cf,  on=["ts_code", "end_date"], how="outer")
    )

    # pit_date 取三表最大值（保守策略：三表全部披露后才使用该记录）
    # 若某表对该 end_date 无记录，对应列为 NaT，max(skipna=True) 自动忽略
    df["pit_date"] = df[["_pit_inc", "_pit_bal", "_pit_cf"]].max(axis=1)
    # 保留各表源可用日列，便于 PIT 审计（可还原"哪张表哪天才可用"）

    # 合并全版本三表后，同一可用日可能出现重复组合，保留最后一条即可；
    # 不同 pit_date 的历史版本必须保留，供 loader 在 T 日做 PIT 快照。
    df = (
        df.sort_values("pit_date")
          .groupby(["pit_date", "ts_code", "end_date"], as_index=False)
          .last()
    )

    # 日期过滤与类型规范
    df = df[df["pit_date"] >= cfg.EARLY_START].copy()
    df["ts_code"] = df["ts_code"].astype(str)
    # _pit_inc/_pit_bal/_pit_cf 是 datetime 列，不参与数值转换
    _pit_cols = {"_pit_inc", "_pit_bal", "_pit_cf"}
    num_cols = [c for c in df.columns
                if c not in {"ts_code", "pit_date", "end_date", "report_type"} | _pit_cols]
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    df = df.set_index(["pit_date", "ts_code", "end_date"]).sort_index()
    _save_parquet(df, cfg.DATA_PROC / "financial_pit.parquet", "M5")

    # 验证
    n = len(df)
    log.info("  行数: %d（预期 10-20 万）", n)
    assert n > 50_000, f"财务三表行数偏少: {n}"

    max_pit = df.index.get_level_values("pit_date").max()
    assert max_pit <= pd.Timestamp.now().normalize(), f"pit_date 含未来日期: {max_pit}"

    for col, label in [("revenue", "营收"), ("total_assets", "总资产"),
                        ("n_cashflow_act", "经营现金流")]:
        if col in df.columns:
            nan_r = df[col].isna().mean()
            log.info("  %s NaN 率: %.1f%%", label, nan_r * 100)

    log.info("  M5 验证通过 ✓")


# ==========================================================================
# M6：财务衍生指标 PIT → indicator_pit.parquet
# ==========================================================================

def process_indicator_pit() -> None:
    """
    加载财务衍生指标（financial_indicator），PIT 对齐后保存。

    PIT 时间戳：ann_date（此表无 f_ann_date 字段）。

    输出字段：
        roe, roa, grossprofit_margin, assets_turn,
        netprofit_yoy, or_yoy, bps, debt_to_assets, q_roe, ocf_to_debt
    索引：(pit_date datetime64, ts_code str, end_date datetime64)，已排序
    """
    log.info("=== M6: indicator_pit ===")

    raw = _load_csv_dir(cfg.DATA_RAW / "financial_indicator", "读取财务指标")
    raw["ann_date"] = _parse_yyyymmdd(raw["ann_date"])
    raw["end_date"] = _parse_yyyymmdd(raw["end_date"])
    raw["pit_date"] = raw["ann_date"]

    needed_fields = [
        # 盈利能力
        "roe", "roa", "grossprofit_margin", "netprofit_margin", "assets_turn",
        # 偿债能力
        "debt_to_assets", "current_ratio", "quick_ratio",
        # 成长性
        "netprofit_yoy", "or_yoy", "assets_yoy", "equity_yoy", "q_sales_yoy",
        # 现金流质量
        "ocf_to_debt", "ocf_to_shortdebt", "q_ocf_to_sales", "q_roe", "q_dt_roe",
        # 每股指标
        "bps",
        # 年化指标（年报 ROE/ROA）
        "roe_yearly", "roa_yearly", "roa2_yearly",
    ]
    core = ["ts_code", "pit_date", "end_date"]
    extra = [c for c in needed_fields if c in raw.columns]
    if "update_flag" in raw.columns:
        extra.append("update_flag")

    df = raw[core + extra].copy()
    df = _dedupe_same_pit_version(df, "pit_date")

    df = df[df["pit_date"] >= cfg.EARLY_START].copy()
    df["ts_code"] = df["ts_code"].astype(str)
    num_cols = [c for c in df.columns if c not in ("ts_code", "pit_date", "end_date")]
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    # 丢弃 update_flag（如被 .last() 保留）
    df = df.drop(columns=["update_flag"], errors="ignore")

    df = df.set_index(["pit_date", "ts_code", "end_date"]).sort_index()
    _save_parquet(df, cfg.DATA_PROC / "indicator_pit.parquet", "M6")

    n = len(df)
    log.info("  行数: %d（预期 10-20 万）", n)
    assert n > 50_000, f"indicator 行数偏少: {n}"

    for col in ["roe", "debt_to_assets"]:
        if col in df.columns:
            log.info("  %s NaN 率: %.1f%%", col, df[col].isna().mean() * 100)

    log.info("  M6 验证通过 ✓")


# ==========================================================================
# M7：股东人数 PIT → holder_pit.parquet
# ==========================================================================

def process_holder_pit() -> None:
    """
    加载股东人数数据，PIT 对齐后保存（季频，仅含公告日和报告期）。

    PIT 时间戳：ann_date。
    部分 Tushare 记录存在 ann_date <= end_date 的异常。为避免未来统计期泄漏，
    额外保存 available_date = max(pit_date, end_date)，loader 查询时要求
    available_date <= T 且 end_date < T。

    输出字段：
        holder_num（股东人数，float64）
        available_date（保守可用日期，用于 loader PIT 过滤）
    索引：(pit_date datetime64, ts_code str, end_date datetime64)，已排序
    """
    log.info("=== M7: holder_pit ===")

    raw = _load_csv_dir(cfg.DATA_RAW / "holder_number", "读取股东人数")
    raw["ann_date"] = _parse_yyyymmdd(raw["ann_date"])
    raw["end_date"] = _parse_yyyymmdd(raw["end_date"])
    raw["pit_date"] = raw["ann_date"]

    core = ["ts_code", "pit_date", "end_date", "holder_num"]
    avail = [c for c in core if c in raw.columns]
    df = raw[avail].copy()

    df["available_date"] = df[["pit_date", "end_date"]].max(axis=1)
    df = _dedupe_same_pit_version(df, "pit_date")
    df = df[df["pit_date"] >= cfg.EARLY_START].copy()
    df["ts_code"] = df["ts_code"].astype(str)
    df["holder_num"] = pd.to_numeric(df["holder_num"], errors="coerce").astype("float64")

    df = df.set_index(["pit_date", "ts_code", "end_date"]).sort_index()
    _save_parquet(df, cfg.DATA_PROC / "holder_pit.parquet", "M7")

    n = len(df)
    log.info("  行数: %d（预期 3-8 万）", n)
    assert n > 20_000, f"股东人数行数偏少: {n}"
    assert df["holder_num"].isna().mean() < 0.05, "holder_num 空值率过高"

    log.info("  M7 验证通过 ✓")


# ==========================================================================
# M8：分红派息 PIT → dividend_pit.parquet
# ==========================================================================

def process_dividend_pit() -> None:
    """
    加载分红派息数据，仅保留 div_proc='实施' 的记录，PIT 对齐后保存。

    PIT 时间戳：imp_ann_date（实施公告日）；缺失时退回 ann_date。
    不做 PIT 去重（每次分红为独立事件，允许同一股票同日有多条）。

    输出字段：end_date, cash_div_tax, stk_div, stk_bo_rate, ex_date, div_proc
    索引：(pit_date datetime64, ts_code str, event_id int)，已排序且唯一。
          event_id 是同一 pit_date + ts_code 下的事件序号，避免事件表索引重复。
    """
    log.info("=== M8: dividend_pit ===")

    raw = _load_csv_dir(cfg.DATA_RAW / "dividend", "读取分红数据")

    # 仅保留已实施分红（排除预案、股东大会通过等未落地记录）
    if "div_proc" in raw.columns:
        raw = raw[raw["div_proc"] == "实施"].copy()

    if raw.empty:
        log.warning("  分红数据过滤后为空（无已实施记录），跳过生成")
        return

    raw["ann_date"] = _parse_yyyymmdd(raw["ann_date"])
    raw["end_date"] = _parse_yyyymmdd(raw["end_date"])

    if "imp_ann_date" in raw.columns:
        raw["imp_ann_date"] = _parse_yyyymmdd(raw["imp_ann_date"])
        raw["pit_date"] = raw["imp_ann_date"].fillna(raw["ann_date"])
    else:
        raw["pit_date"] = raw["ann_date"]

    if "ex_date" in raw.columns:
        raw["ex_date"] = _parse_yyyymmdd(raw["ex_date"])

    raw = raw[raw["pit_date"] >= cfg.EARLY_START].copy()

    needed = ["ts_code", "pit_date", "end_date",
              "cash_div_tax", "stk_div", "stk_bo_rate", "ex_date", "div_proc"]
    avail = [c for c in needed if c in raw.columns]
    df = raw[avail].copy()

    df["ts_code"] = df["ts_code"].astype(str)
    for col in ["cash_div_tax", "stk_div", "stk_bo_rate"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    df = df.sort_values(["pit_date", "ts_code", "end_date", "ex_date"]).copy()
    df["event_id"] = df.groupby(["pit_date", "ts_code"]).cumcount().astype("int64")
    df = df.set_index(["pit_date", "ts_code", "event_id"]).sort_index()
    _save_parquet(df, cfg.DATA_PROC / "dividend_pit.parquet", "M8")

    n = len(df)
    log.info("  行数: %d（已实施分红记录）", n)
    assert n > 0, "分红数据为空"
    assert df.index.duplicated().sum() == 0, "分红事件索引存在重复"

    if "cash_div_tax" in df.columns:
        non_zero = (df["cash_div_tax"] > 0).mean()
        log.info("  现金分红（>0）占比: %.1f%%", non_zero * 100)

    log.info("  M8 验证通过 ✓")


# ==========================================================================
# M9：融资融券 → margin.parquet
# ==========================================================================

def process_margin() -> None:
    """
    合并 raw/margin 下所有股票的融资融券明细。
    空文件（非两融标的）正常跳过，不报错。

    衍生字段：
        rz_net     = rzmre - rzche   融资净买入（元）
        rq_net_vol = rqyl  - rqchl   融券净卖出（股）
    NOTE：rzrqye_ratio（余额/流通市值）需与 daily_basic 做 join，延迟到因子阶段计算。

    输出字段：rzye, rqye, rzmre, rzche, rqyl, rqchl, rqmcl, rzrqye, rz_net, rq_net_vol
    索引：(trade_date datetime64, ts_code str)，已排序
    """
    log.info("=== M9: margin ===")

    raw = _load_csv_dir(cfg.DATA_RAW / "margin", "读取融资融券")
    raw["trade_date"] = _parse_yyyymmdd(raw["trade_date"])
    raw = _filter_date_range(raw)
    raw["ts_code"] = raw["ts_code"].astype(str)

    raw["rz_net"]     = pd.to_numeric(raw.get("rzmre", np.nan), errors="coerce") \
                      - pd.to_numeric(raw.get("rzche", np.nan), errors="coerce")
    raw["rq_net_vol"] = pd.to_numeric(raw.get("rqyl",  np.nan), errors="coerce") \
                      - pd.to_numeric(raw.get("rqchl", np.nan), errors="coerce")

    keep = ["trade_date", "ts_code",
            "rzye", "rqye", "rzmre", "rzche",
            "rqyl", "rqchl", "rqmcl", "rzrqye",
            "rz_net", "rq_net_vol"]
    avail = [c for c in keep if c in raw.columns]
    for col in avail:
        if col not in ("trade_date", "ts_code"):
            raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    df = raw[avail].set_index(["trade_date", "ts_code"]).sort_index()
    _save_parquet(df, cfg.DATA_PROC / "margin.parquet", "M9")

    n = len(df)
    log.info("  行数: %d（仅两融标的，预期 100-200 万）", n)
    assert n > 100_000, f"margin 行数偏少: {n}"
    log.info("  M9 验证通过 ✓")


# ==========================================================================
# M10：个股资金流向 → moneyflow.parquet
# ==========================================================================

def process_moneyflow() -> None:
    """
    合并 raw/moneyflow 下所有股票的资金流向数据。

    保留字段：超大单/大单/中单/小单买卖额、主力净流入金额/量。
    NOTE：net_mf_ratio（净流入/成交额）需与 daily_quote.amount 做 join，
          延迟到因子阶段计算（两表单位不同，此处不预计算避免引入 join 风险）。

    输出字段：buy_elg_amount/sell_elg_amount, buy_lg_amount/sell_lg_amount,
              buy_md_amount, buy_sm_amount, net_mf_amount, net_mf_vol
    索引：(trade_date datetime64, ts_code str)，已排序
    """
    log.info("=== M10: moneyflow ===")

    raw = _load_csv_dir(cfg.DATA_RAW / "moneyflow", "读取资金流向")
    raw["trade_date"] = _parse_yyyymmdd(raw["trade_date"])
    raw = _filter_date_range(raw)
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = [
        "trade_date", "ts_code",
        "buy_elg_amount", "sell_elg_amount",
        "buy_lg_amount",  "sell_lg_amount",
        "buy_md_amount",  "buy_sm_amount",
        "net_mf_amount",  "net_mf_vol",
    ]
    avail = [c for c in keep if c in raw.columns]
    for col in avail:
        if col not in ("trade_date", "ts_code"):
            raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    df = raw[avail].set_index(["trade_date", "ts_code"]).sort_index()
    _save_parquet(df, cfg.DATA_PROC / "moneyflow.parquet", "M10")

    n = len(df)
    log.info("  行数: %d（预期 ~230万）", n)
    assert n > 1_000_000, f"moneyflow 行数偏少: {n}"

    if "net_mf_amount" in df.columns:
        log.info("  net_mf_amount NaN 率: %.1f%%", df["net_mf_amount"].isna().mean() * 100)
    log.info("  M10 验证通过 ✓")


# ==========================================================================
# M11：每日状态标记 → stock_status.parquet
# ==========================================================================

def _expand_date_intervals(
    records: pd.DataFrame,
    trading_dates: np.ndarray,
    code_col: str,
    start_col: str,
    end_col_eff: str,
    flag_col: str,
) -> pd.DataFrame:
    """
    将 (ts_code, start_date, end_date_eff) 区间记录展开为
    (ts_code, trade_date) 对，并附加 flag_col=True 标记。

    使用 searchsorted 在已排序的 trading_dates 数组上定位区间端点，
    避免逐日循环，性能 O(N_records × log(N_dates))。

    Args:
        records:       含 code_col / start_col / end_col_eff 的 DataFrame
        trading_dates: 已排序的 datetime64 数组（来自 daily_quote 的真实交易日）
        code_col:      股票代码列名
        start_col:     区间左边界（inclusive）列名
        end_col_eff:   区间右边界（exclusive）列名，NaN 已被替换为远期日期
        flag_col:      输出 bool 标记列名
    Returns:
        (ts_code, trade_date, flag_col=True) DataFrame，已 drop_duplicates
    """
    entries: list[pd.DataFrame] = []
    for _, row in records.iterrows():
        start = row[start_col]
        end   = row[end_col_eff]
        if pd.isna(start):
            continue
        lo = np.searchsorted(trading_dates, start)
        hi = np.searchsorted(trading_dates, end)   # exclusive
        if lo >= hi:
            continue
        dates = trading_dates[lo:hi]
        entries.append(pd.DataFrame({
            code_col:    row[code_col],
            "trade_date": dates,
        }))

    if not entries:
        return pd.DataFrame(columns=[code_col, "trade_date", flag_col])

    result = pd.concat(entries, ignore_index=True).drop_duplicates()
    result[flag_col] = True
    return result


def process_stock_status() -> None:
    """
    合并四类数据源，生成每日股票状态标记。

    标记列说明：
        is_suspended    当日停牌（来源：raw/suspend/）
        limit_status    当日涨跌停状态：'U'=涨停, 'D'=跌停, ''=正常（来源：raw/limit_list/）
        is_limit_locked 当日全天封板 open_times==0（次日调仓约束使用）
        is_st           当日 ST/∗ST 状态（来源：raw/namechange.csv）
        is_new_stock    上市不足 NEW_STOCK_DAYS(180) 天（来源：raw/stock_basic.csv）
        tradable        = ～is_suspended & ～is_st & ～is_new_stock
                         （涨跌停不纳入此列，在优化器约束中单独处理）

    数据局限性（已知）：
        namechange.csv 仅覆盖 2020-2026 的改名记录，
        2012-2019 期间的 ST 标记不完整，is_st 在该区间存在遗漏。
        对中证500策略影响有限（ST股通常已被指数剔除），但训练期统计结果需注意。

    索引：(trade_date datetime64, ts_code str)，已排序
    """
    log.info("=== M11: stock_status ===")

    # ------------------------------------------------------------------
    # Step 1: 全量网格
    # ------------------------------------------------------------------
    all_pairs = _load_stock_pairs()
    trading_dates = np.array(sorted(all_pairs["trade_date"].unique()))
    log.info("  全量 (ts_code, trade_date) 对: %d，交易日: %d，股票: %d",
             len(all_pairs), len(trading_dates), all_pairs["ts_code"].nunique())

    # ------------------------------------------------------------------
    # Step 2: 停牌标记（suspend/ 已是逐日格式，直接标记）
    # ------------------------------------------------------------------
    susp_raw = _load_csv_dir(cfg.DATA_RAW / "suspend", "读取停牌")
    susp_raw["trade_date"] = _parse_yyyymmdd(susp_raw["trade_date"])
    susp_raw["ts_code"]    = susp_raw["ts_code"].astype(str)
    susp_raw = _filter_date_range(susp_raw)
    susp_flag = susp_raw[["ts_code", "trade_date"]].drop_duplicates()
    susp_flag["is_suspended"] = True
    log.info("  停牌记录（日频）: %d 条", len(susp_flag))

    # 完全停牌（当天无行情）的股票不在 daily_quote 中，因此不在 all_pairs 网格里。
    # 若不补充，回测引擎遇到这些股票时会默认 FREE（可交易），在实盘中会下单失败。
    ap_idx = pd.MultiIndex.from_arrays(
        [all_pairs["ts_code"], all_pairs["trade_date"]]
    )
    susp_mi = pd.MultiIndex.from_arrays(
        [susp_flag["ts_code"], susp_flag["trade_date"]]
    )
    susp_fully = susp_flag.loc[
        ~susp_mi.isin(ap_idx), ["ts_code", "trade_date"]
    ].copy()
    if not susp_fully.empty:
        log.info("  完全停牌（无行情）补充到状态网格: %d 条", len(susp_fully))
        all_pairs = pd.concat([all_pairs, susp_fully], ignore_index=True)

    # ------------------------------------------------------------------
    # Step 3: 涨跌停标记（limit_list/ 按日期组织，部分日期文件为空）
    # ------------------------------------------------------------------
    try:
        limit_raw = _load_csv_dir(cfg.DATA_RAW / "limit_list", "读取涨跌停")
        limit_raw["trade_date"] = _parse_yyyymmdd(limit_raw["trade_date"])
        limit_raw["ts_code"]    = limit_raw["ts_code"].astype(str)
        limit_raw = _filter_date_range(limit_raw)

        # 重命名 'limit' 列避免与 Python 内置关键字冲突
        if "limit" in limit_raw.columns:
            limit_raw = limit_raw.rename(columns={"limit": "limit_status"})
        else:
            limit_raw["limit_status"] = ""

        # open_times==0 表示全天封板（次日完全无法成交）
        if "open_times" in limit_raw.columns:
            # open_times NaN 视为 1（不锁定），保守处理
            locked = limit_raw["open_times"].fillna(1.0)
            limit_raw["is_limit_locked"] = (locked == 0).astype(bool)
        else:
            limit_raw["is_limit_locked"] = False

        limit_cols = ["ts_code", "trade_date", "limit_status", "is_limit_locked"]
        limit_flag = (
            limit_raw[limit_cols]
            .drop_duplicates(subset=["ts_code", "trade_date"])
        )
        log.info("  涨跌停记录: %d 条", len(limit_flag))
    except ValueError:
        log.warning("  limit_list 目录无有效数据，涨跌停标记将全为默认值")
        limit_flag = pd.DataFrame(
            columns=["ts_code", "trade_date", "limit_status", "is_limit_locked"]
        )

    # ------------------------------------------------------------------
    # Step 4: ST 标记（namechange.csv 区间展开）
    # ------------------------------------------------------------------
    nc = pd.read_csv(cfg.DATA_RAW / "namechange.csv", encoding="utf-8")
    nc["start_date"] = _parse_yyyymmdd(nc["start_date"])
    nc["end_date"]   = _parse_yyyymmdd(nc["end_date"])
    nc["ts_code"]    = nc["ts_code"].astype(str)

    # end_date NaN（当前仍为该名称）→ MARKET_END 次日作为排他右边界
    _FUTURE = cfg.MARKET_END + pd.Timedelta(days=1)
    nc["end_date_eff"] = nc["end_date"].fillna(_FUTURE)

    # 过滤股票名称含 ST 的时间区间（含 *ST / ST / S*ST）
    st_mask    = nc["name"].str.contains("ST", na=False, case=False)
    st_records = nc[st_mask & nc["start_date"].notna()].copy()

    min_start = st_records["start_date"].min()
    log.info("  namechange ST 区间记录: %d 条，最早 start_date: %s",
             len(st_records), min_start)
    if pd.isna(min_start) or min_start.year > 2019:
        log.warning(
            "  【数据局限】namechange 最早 ST 记录仅从 %s 开始，"
            "2012-2019 期间 ST 标记不完整。",
            min_start,
        )

    st_flag = _expand_date_intervals(
        st_records, trading_dates,
        code_col="ts_code", start_col="start_date", end_col_eff="end_date_eff",
        flag_col="is_st",
    )
    log.info("  ST (ts_code, trade_date) 展开后: %d 条", len(st_flag))

    # ------------------------------------------------------------------
    # Step 5: 新股标记（stock_basic.csv 的 list_date）
    # ------------------------------------------------------------------
    sb = pd.read_csv(cfg.DATA_RAW / "stock_basic.csv", encoding="utf-8")
    sb["list_date"] = _parse_yyyymmdd(sb["list_date"])
    sb["ts_code"]   = sb["ts_code"].astype(str)
    sb = sb[["ts_code", "list_date"]].dropna(subset=["list_date"])

    # ------------------------------------------------------------------
    # Step 6: 组装全量网格
    # ------------------------------------------------------------------
    df = all_pairs.copy()

    def _fill_bool(series: pd.Series) -> pd.Series:
        """fillna(False) for bool columns without pandas FutureWarning."""
        return series.where(series.notna(), other=False).astype(bool)

    # 停牌
    df = df.merge(susp_flag, on=["ts_code", "trade_date"], how="left")
    df["is_suspended"] = _fill_bool(df["is_suspended"])

    # 涨跌停
    df = df.merge(limit_flag, on=["ts_code", "trade_date"], how="left")
    df["limit_status"]    = df["limit_status"].fillna("").astype(str)
    df["is_limit_locked"] = _fill_bool(df["is_limit_locked"])
    # 明确方向性封板约束：Z 表示触板/炸板类状态，不作为完全不可交易处理。
    df["is_limit_up_locked"] = (
        (df["limit_status"] == "U") & df["is_limit_locked"]
    )
    df["is_limit_down_locked"] = (
        (df["limit_status"] == "D") & df["is_limit_locked"]
    )

    # ST
    df = df.merge(
        st_flag[["ts_code", "trade_date", "is_st"]] if "is_st" in st_flag.columns
        else st_flag.assign(is_st=False),
        on=["ts_code", "trade_date"], how="left",
    )
    df["is_st"] = _fill_bool(df["is_st"])

    # 新股：(trade_date - list_date) 天数在 [0, NEW_STOCK_DAYS)
    df = df.merge(sb, on="ts_code", how="left")
    days_listed = (df["trade_date"] - df["list_date"]).dt.days
    is_new = (days_listed >= 0) & (days_listed < cfg.NEW_STOCK_DAYS)
    df["is_new_stock"] = _fill_bool(is_new)

    # tradable
    df["tradable"] = (~df["is_suspended"] & ~df["is_st"] & ~df["is_new_stock"])

    # ------------------------------------------------------------------
    # Step 7: 整理输出
    # ------------------------------------------------------------------
    out_cols = [
        "trade_date", "ts_code",
        "is_suspended", "limit_status", "is_limit_locked",
        "is_limit_up_locked", "is_limit_down_locked",
        "is_st", "is_new_stock", "tradable",
    ]
    df = df[out_cols].set_index(["trade_date", "ts_code"]).sort_index()
    _save_parquet(df, cfg.DATA_PROC / "stock_status.parquet", "M11")

    # ------------------------------------------------------------------
    # 验证
    # ------------------------------------------------------------------
    n = len(df)
    assert n == len(all_pairs), f"行数不符: {n} vs {len(all_pairs)}"
    log.info("  总行数: %d ✓", n)

    for col in [
        "is_suspended", "is_limit_locked", "is_limit_up_locked",
        "is_limit_down_locked", "is_st", "is_new_stock",
    ]:
        cnt = int(df[col].sum())
        pct = cnt / n * 100
        log.info("  %-20s True: %7d 条 (%4.1f%%)", col, cnt, pct)

    tradable_pct = df["tradable"].mean() * 100
    # 注：完全停牌股票不在 daily_quote 中，因此 is_suspended 仅含盘中停牌，
    # tradable 接近 100% 是正常的（~97%），不表示过滤失效
    log.info("  tradable=True: %.1f%%", tradable_pct)
    assert 60 < tradable_pct <= 99.5, f"tradable 比例异常: {tradable_pct:.1f}%"

    log.info("  M11 验证通过 ✓")


# ==========================================================================
# M12：可投资域快照 → index_member.parquet
# ==========================================================================

def process_index_member() -> None:
    """
    将中证500成分股权重与当日状态标记合并，生成每个调仓日的可投资域快照。

    输入：
        data/csi500_index_weight_201201_202512.csv  →  调仓日 × 成分股 × 基准权重
        stock_status.parquet                        →  tradable / limit_status / …

    合并逻辑：
        - trade_date（月末最后交易日）= rebalance_date
        - 按 (rebalance_date, ts_code) 左连接 stock_status
        - 对 stock_status 缺失的成分股，先用 raw/suspend 补全停牌标记
        - 仍缺失的状态填充为保守值（tradable=False）

    NOTE：index_weight 为百分比形式（如 0.227 表示 0.227%），每调仓日合计约 100%。

    输出字段：index_weight, tradable, limit_status, is_suspended, is_limit_locked,
              is_limit_up_locked, is_limit_down_locked, is_st, is_new_stock
    索引：(rebalance_date datetime64, ts_code str)，已排序
    """
    log.info("=== M12: index_member ===")

    # ---- 加载 CSI500 权重文件 ----
    # 文件含 UTF-8 BOM，使用 utf-8-sig 消除
    csi = pd.read_csv(cfg.CSI500_WEIGHT_FILE, encoding="utf-8-sig")
    # 防御性清理列名（应对 BOM 残留）
    csi.columns = [c.lstrip("﻿") for c in csi.columns]

    csi["trade_date"]  = _parse_yyyymmdd(csi["trade_date"])
    csi["ts_code"]     = csi["con_code"].astype(str)
    csi["index_weight"] = pd.to_numeric(csi["weight"], errors="coerce").astype("float64")
    csi = _filter_date_range(csi)
    csi = csi[["trade_date", "ts_code", "index_weight"]].copy()

    n_dates = csi["trade_date"].nunique()
    members_per_date = csi.groupby("trade_date").size()
    log.info("  调仓日: %d，每日成分股: %d~%d",
             n_dates, members_per_date.min(), members_per_date.max())
    assert (members_per_date == 500).all(), \
        f"部分调仓日成分股数量不是 500: {members_per_date[members_per_date != 500]}"

    # ---- 加载 stock_status ----
    ss_path = cfg.DATA_PROC / "stock_status.parquet"
    if not ss_path.exists():
        raise FileNotFoundError(
            "stock_status.parquet 不存在，请先执行 M11（python csv_to_parquet.py status）"
        )
    ss = pd.read_parquet(ss_path).reset_index()   # trade_date + ts_code 变为普通列

    # ---- 合并状态 ----
    df = csi.merge(ss, on=["trade_date", "ts_code"], how="left")

    missing_n = df["tradable"].isna().sum()
    if missing_n:
        missing_keys = df.loc[
            df["tradable"].isna(), ["trade_date", "ts_code"]
        ].drop_duplicates()
        suspend_hits: list[pd.DataFrame] = []
        for code, keys in missing_keys.groupby("ts_code"):
            suspend_file = cfg.DATA_RAW / "suspend" / f"{code}.csv"
            if not suspend_file.exists():
                continue
            susp = _read_csv_safe(suspend_file)
            if susp is None or not {"trade_date", "ts_code"}.issubset(susp.columns):
                continue
            susp = susp[["trade_date", "ts_code"]].copy()
            susp["trade_date"] = _parse_yyyymmdd(susp["trade_date"])
            susp["ts_code"] = susp["ts_code"].astype(str)
            susp = susp[susp["trade_date"].isin(keys["trade_date"])].drop_duplicates()
            if not susp.empty:
                suspend_hits.append(susp)

        if suspend_hits:
            suspend_flag = (
                pd.concat(suspend_hits, ignore_index=True)
                .drop_duplicates()
                .assign(_missing_raw_suspend=True)
            )
            df = df.merge(suspend_flag, on=["trade_date", "ts_code"], how="left")
            raw_susp_mask = df["_missing_raw_suspend"].eq(True)
            # stock_status 以 daily_quote 为网格，完全停牌无行情的股票会缺行；
            # 此处用原始停牌表补回停牌原因，避免回测阶段误判为普通不可交易。
            df.loc[raw_susp_mask, "is_suspended"] = True
            df.loc[raw_susp_mask, "tradable"] = False
            df = df.drop(columns="_missing_raw_suspend")
            log.warning(
                "  %d 条缺失状态中，%d 条命中 raw/suspend，已补 is_suspended=True",
                missing_n,
                len(suspend_flag),
            )
        log.warning(
            "  %d 条成分股在 stock_status 中无对应记录"
            "（可能为新上市或数据缺失），tradable 填充为 False",
            missing_n,
        )

    def _fill_bool_col(col: str) -> None:
        if col in df.columns:
            df[col] = df[col].where(df[col].notna(), other=False).astype(bool)
        else:
            df[col] = False

    for _c in [
        "tradable", "is_suspended", "is_limit_locked",
        "is_limit_up_locked", "is_limit_down_locked",
        "is_st", "is_new_stock",
    ]:
        _fill_bool_col(_c)
    df["limit_status"] = df["limit_status"].fillna("").astype(str) \
                         if "limit_status" in df.columns else ""

    # ---- 重命名 trade_date → rebalance_date，建立输出结构 ----
    df = df.rename(columns={"trade_date": "rebalance_date"})
    out_cols = [
        "rebalance_date", "ts_code",
        "index_weight", "tradable",
        "limit_status", "is_suspended", "is_limit_locked",
        "is_limit_up_locked", "is_limit_down_locked",
        "is_st", "is_new_stock",
    ]
    df = df[out_cols].set_index(["rebalance_date", "ts_code"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "index_member.parquet", "M12")

    # ---- 验证 ----
    weight_sums = df.groupby("rebalance_date")["index_weight"].sum()
    log.info("  index_weight 合计范围: %.2f ~ %.2f（预期约 100）",
             weight_sums.min(), weight_sums.max())
    assert (weight_sums > 90).all(), \
        f"部分调仓日权重合计低于 90: {weight_sums[weight_sums <= 90]}"

    tradable_pct = df["tradable"].mean() * 100
    non_tradable = (~df["tradable"]).groupby("rebalance_date").sum()
    log.info("  tradable=True: %.1f%%，每日不可交易股票均值 %.1f 只，最大 %d 只",
             tradable_pct, non_tradable.mean(), non_tradable.max())

    log.info("  M12 验证通过 ✓")


# ==========================================================================
# M13：北向持仓 → hk_hold.parquet
# ==========================================================================

def process_hk_hold() -> None:
    """
    北向持仓（沪深港通持股比例）→ hk_hold.parquet

    来源：raw/hk_hold/（按股票 CSV，2014-11-17 后有数据）
    去重键：(trade_date, ts_code)
    输出字段：vol, ratio, exchange
    索引：(trade_date, ts_code)
    """
    log.info("=== M13: hk_hold ===")
    raw_dir = cfg.DATA_RAW / "hk_hold"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/hk_hold/ 不存在或为空，跳过（请先运行 download_alternative_data）")
        return

    raw = _load_csv_dir(raw_dir, "读取 hk_hold")
    raw["trade_date"] = _parse_yyyymmdd(raw["trade_date"])
    raw = _filter_date_range(raw)
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["trade_date", "ts_code", "vol", "ratio", "exchange"]
    avail = [c for c in keep if c in raw.columns]
    missing = set(keep) - set(avail)
    if missing:
        log.warning("  hk_hold 缺字段: %s", missing)

    for col in ["vol", "ratio"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    raw = raw[avail].drop_duplicates(subset=["trade_date", "ts_code"]).copy()
    df = raw.set_index(["trade_date", "ts_code"]).sort_index()
    _save_parquet(df, cfg.DATA_PROC / "hk_hold.parquet", "M13")
    log.info("  行数: %d（2014-11-17 前无数据为正常）", len(df))
    log.info("  M13 验证通过 ✓")


# ==========================================================================
# M14：分析师研报 PIT → analyst_rc_pit.parquet
# ==========================================================================

def process_analyst_rc_pit() -> None:
    """
    分析师研报/评级/盈利预测 PIT → analyst_rc_pit.parquet

    来源：raw/analyst_rc/（按股票 CSV）
    PIT 时间戳：report_date
    输出字段：ts_code, pit_date(=report_date), org_name, author_name, eps, pe, rating
    索引：(pit_date, ts_code, event_id)  — 事件表，同日允许多条
    """
    log.info("=== M14: analyst_rc_pit ===")
    raw_dir = cfg.DATA_RAW / "analyst_rc"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/analyst_rc/ 不存在或为空，跳过")
        return

    raw = _load_csv_dir(raw_dir, "读取 analyst_rc")
    if "report_date" not in raw.columns:
        log.error("  analyst_rc 缺 report_date 列，无法建立 PIT 时间戳")
        return

    raw["report_date"] = _parse_yyyymmdd(raw["report_date"])
    raw = raw[raw["report_date"] >= cfg.EARLY_START].copy()
    raw = raw.rename(columns={"report_date": "pit_date"})
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["pit_date", "ts_code", "org_name", "author_name", "eps", "pe", "rating",
            "report_title", "max_price", "min_price"]
    avail = [c for c in keep if c in raw.columns]
    for col in ["eps", "pe"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    raw = raw[avail].sort_values(["pit_date", "ts_code"]).reset_index(drop=True)
    raw["event_id"] = raw.groupby(["pit_date", "ts_code"]).cumcount().astype("int64")
    df = raw.set_index(["pit_date", "ts_code", "event_id"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "analyst_rc_pit.parquet", "M14")
    log.info("  行数: %d", len(df))
    log.info("  M14 验证通过 ✓")


# ==========================================================================
# M15：解禁计划 → share_float.parquet
# ==========================================================================

def process_share_float() -> None:
    """
    限售股解禁计划 → share_float.parquet

    来源：raw/share_float/（按股票 CSV）
    PIT 用途：ann_date <= T 且 float_date > T（公告已发、解禁未到）
    输出字段：ts_code, ann_date, float_date, float_share, float_ratio, holder_name, share_type
    索引：(ann_date, ts_code, float_date)
    """
    log.info("=== M15: share_float ===")
    raw_dir = cfg.DATA_RAW / "share_float"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/share_float/ 不存在或为空，跳过")
        return

    raw = _load_csv_dir(raw_dir, "读取 share_float")
    raw["ann_date"]   = _parse_yyyymmdd(raw["ann_date"])
    raw["float_date"] = _parse_yyyymmdd(raw["float_date"])
    raw = raw[raw["ann_date"] >= cfg.EARLY_START].copy()
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["ann_date", "ts_code", "float_date",
            "float_share", "float_ratio", "holder_name", "share_type"]
    avail = [c for c in keep if c in raw.columns]
    for col in ["float_share", "float_ratio"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    raw = raw[avail].dropna(subset=["ann_date", "float_date"]).copy()
    raw = raw.drop_duplicates(subset=["ann_date", "ts_code", "float_date"])
    df = raw.set_index(["ann_date", "ts_code", "float_date"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "share_float.parquet", "M15")
    log.info("  行数: %d", len(df))
    log.info("  M15 验证通过 ✓")


# ==========================================================================
# M16：股东增减持 PIT → holder_trade_pit.parquet
# ==========================================================================

def process_holder_trade_pit() -> None:
    """
    股东/高管增减持 PIT → holder_trade_pit.parquet

    来源：raw/holder_trade/（按股票 CSV）
    PIT 时间戳：ann_date
    输出字段：ts_code, pit_date(=ann_date), holder_name, holder_type, in_de,
              change_vol, change_ratio, after_share, after_ratio, avg_price
    索引：(pit_date, ts_code, event_id)
    """
    log.info("=== M16: holder_trade_pit ===")
    raw_dir = cfg.DATA_RAW / "holder_trade"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/holder_trade/ 不存在或为空，跳过")
        return

    raw = _load_csv_dir(raw_dir, "读取 holder_trade")
    raw["ann_date"] = _parse_yyyymmdd(raw["ann_date"])
    raw = raw[raw["ann_date"] >= cfg.EARLY_START].copy()
    raw = raw.rename(columns={"ann_date": "pit_date"})
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["pit_date", "ts_code", "holder_name", "holder_type", "in_de",
            "change_vol", "change_ratio", "after_share", "after_ratio", "avg_price"]
    avail = [c for c in keep if c in raw.columns]
    for col in ["change_vol", "change_ratio", "after_share", "after_ratio", "avg_price"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    raw = raw[avail].sort_values(["pit_date", "ts_code"]).reset_index(drop=True)
    raw["event_id"] = raw.groupby(["pit_date", "ts_code"]).cumcount().astype("int64")
    df = raw.set_index(["pit_date", "ts_code", "event_id"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "holder_trade_pit.parquet", "M16")
    log.info("  行数: %d", len(df))
    log.info("  M16 验证通过 ✓")


# ==========================================================================
# M17：筹码分布摘要 → cyq_perf.parquet
# ==========================================================================

def process_cyq_perf() -> None:
    """
    筹码/成本分布摘要（日频）→ cyq_perf.parquet

    来源：raw/cyq_perf/（按股票 CSV）
    输出字段：vol, ratio, cost_5pct, cost_15pct, cost_50pct, cost_85pct, cost_95pct,
              weight_avg, winner_rate
    注意：用 winner_rate，不是 winner（实测修正）；2018 年前覆盖较少。
    索引：(trade_date, ts_code)
    """
    log.info("=== M17: cyq_perf ===")
    raw_dir = cfg.DATA_RAW / "cyq_perf"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/cyq_perf/ 不存在或为空，跳过")
        return

    raw = _load_csv_dir(raw_dir, "读取 cyq_perf")
    raw["trade_date"] = _parse_yyyymmdd(raw["trade_date"])
    raw = _filter_date_range(raw)
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["trade_date", "ts_code",
            "cost_5pct", "cost_15pct", "cost_50pct", "cost_85pct", "cost_95pct",
            "weight_avg", "winner_rate"]
    avail = [c for c in keep if c in raw.columns]
    num_cols = [c for c in avail if c not in ("trade_date", "ts_code")]
    for col in num_cols:
        raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    raw = raw[avail].drop_duplicates(subset=["trade_date", "ts_code"]).copy()
    df = raw.set_index(["trade_date", "ts_code"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "cyq_perf.parquet", "M17")
    log.info("  行数: %d（2018 年前覆盖有限为正常）", len(df))
    log.info("  M17 验证通过 ✓")


# ==========================================================================
# M18：股权质押统计 → pledge_stat.parquet
# ==========================================================================

def process_pledge_stat() -> None:
    """
    股权质押统计（季频）→ pledge_stat.parquet

    来源：raw/pledge_stat/（按股票 CSV）
    PIT：end_date 作统计截止日（无 ann_date，使用 end_date <= T 保守估计）
    输出字段：pledge_count, unrest_pledge, rest_pledge, total_share, pledge_ratio
    索引：(end_date, ts_code)
    """
    log.info("=== M18: pledge_stat ===")
    raw_dir = cfg.DATA_RAW / "pledge_stat"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/pledge_stat/ 不存在或为空，跳过")
        return

    raw = _load_csv_dir(raw_dir, "读取 pledge_stat")
    raw["end_date"] = _parse_yyyymmdd(raw["end_date"])
    raw = raw[raw["end_date"] >= cfg.EARLY_START].copy()
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["end_date", "ts_code",
            "pledge_count", "unrest_pledge", "rest_pledge", "total_share", "pledge_ratio"]
    avail = [c for c in keep if c in raw.columns]
    for col in ["pledge_count", "unrest_pledge", "rest_pledge", "total_share", "pledge_ratio"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    raw = raw[avail].drop_duplicates(subset=["end_date", "ts_code"]).copy()
    df = raw.set_index(["end_date", "ts_code"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "pledge_stat.parquet", "M18")
    log.info("  行数: %d", len(df))
    log.info("  M18 验证通过 ✓")


# ==========================================================================
# M19：北向/南向市场级资金流 → moneyflow_hsgt.parquet
# ==========================================================================

def process_moneyflow_hsgt() -> None:
    """
    北向/南向市场级资金流 → moneyflow_hsgt.parquet

    来源：raw/hsgt_flow.csv（单文件）
    输出字段：ggt_ss, ggt_sz, hgt, sgt, north_money, south_money
    索引：trade_date（单层）
    """
    log.info("=== M19: moneyflow_hsgt ===")
    raw_path = cfg.DATA_RAW / "hsgt_flow.csv"
    if not raw_path.exists():
        log.warning("  raw/hsgt_flow.csv 不存在，跳过（请先运行 download_alternative_data hsgt_flow）")
        return

    raw = pd.read_csv(raw_path, encoding="utf-8")
    if raw.empty:
        log.warning("  raw/hsgt_flow.csv 为空，跳过")
        return

    raw["trade_date"] = _parse_yyyymmdd(raw["trade_date"])
    raw = _filter_date_range(raw)

    keep = ["trade_date", "ggt_ss", "ggt_sz", "hgt", "sgt", "north_money", "south_money"]
    avail = [c for c in keep if c in raw.columns]
    missing = set(keep[1:]) - set(avail)
    if missing:
        log.warning("  moneyflow_hsgt 缺字段: %s", missing)

    num_cols = [c for c in avail if c != "trade_date"]
    for col in num_cols:
        raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    df = raw[avail].set_index("trade_date").sort_index()

    _save_parquet(df, cfg.DATA_PROC / "moneyflow_hsgt.parquet", "M19")
    assert len(df) > 100, f"moneyflow_hsgt 行数偏少: {len(df)}"
    log.info("  行数: %d（预期 ~3500 个交易日）", len(df))
    log.info("  M19 验证通过 ✓")


# ==========================================================================
# M20：龙虎榜个股事件 → top_list.parquet
# ==========================================================================

def process_top_list() -> None:
    """
    龙虎榜个股事件（按交易日汇总）→ top_list.parquet

    来源：raw/top_list/（按交易日 CSV）
    输出字段：ts_code, name, close, pct_change, turnover_rate, amount, net_amount, net_rate, reason
    索引：(trade_date, ts_code)
    """
    log.info("=== M20: top_list ===")
    raw_dir = cfg.DATA_RAW / "top_list"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/top_list/ 不存在或为空，跳过")
        return

    raw = _load_csv_dir(raw_dir, "读取 top_list")
    if raw.empty:
        log.warning("  top_list 无有效数据，跳过")
        return

    raw["trade_date"] = _parse_yyyymmdd(raw["trade_date"])
    raw = _filter_date_range(raw)
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["trade_date", "ts_code", "name", "close", "pct_change",
            "turnover_rate", "amount", "net_amount", "net_rate", "reason"]
    avail = [c for c in keep if c in raw.columns]
    num_cols = [c for c in avail if c not in ("trade_date", "ts_code", "name", "reason")]
    for col in num_cols:
        raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    raw = raw[avail].drop_duplicates(subset=["trade_date", "ts_code"]).copy()
    df = raw.set_index(["trade_date", "ts_code"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "top_list.parquet", "M20")
    log.info("  行数: %d", len(df))
    log.info("  M20 验证通过 ✓")


# ==========================================================================
# M21：龙虎榜机构席位 → top_inst.parquet
# ==========================================================================

def process_top_inst() -> None:
    """
    龙虎榜机构席位（按交易日汇总）→ top_inst.parquet

    来源：raw/top_inst/（按交易日 CSV）
    输出字段：ts_code, exalter, buy, buy_rate, sell, sell_rate, net_buy, side, reason
    索引：(trade_date, ts_code, exalter)
    """
    log.info("=== M21: top_inst ===")
    raw_dir = cfg.DATA_RAW / "top_inst"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/top_inst/ 不存在或为空，跳过")
        return

    raw = _load_csv_dir(raw_dir, "读取 top_inst")
    if raw.empty:
        log.warning("  top_inst 无有效数据，跳过")
        return

    raw["trade_date"] = _parse_yyyymmdd(raw["trade_date"])
    raw = _filter_date_range(raw)
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["trade_date", "ts_code", "exalter",
            "buy", "buy_rate", "sell", "sell_rate", "net_buy", "side", "reason"]
    avail = [c for c in keep if c in raw.columns]
    num_cols = [c for c in avail
                if c not in ("trade_date", "ts_code", "exalter", "side", "reason")]
    for col in num_cols:
        raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    if "exalter" in raw.columns:
        raw["exalter"] = raw["exalter"].fillna("").astype(str)

    # 去重：同一 (trade_date, ts_code, exalter) 保留最后一条
    dedup_keys = [k for k in ["trade_date", "ts_code", "exalter"] if k in raw.columns]
    raw = raw[avail].drop_duplicates(subset=dedup_keys).copy()
    df = raw.set_index(["trade_date", "ts_code", "exalter"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "top_inst.parquet", "M21")
    log.info("  行数: %d", len(df))
    log.info("  M21 验证通过 ✓")


# ==========================================================================
# M22：大宗交易 → block_trade.parquet
# ==========================================================================

def process_block_trade() -> None:
    """
    大宗交易（按交易日汇总）→ block_trade.parquet

    来源：raw/block_trade/（按交易日 CSV）
    输出字段：ts_code, price, vol, amount, buyer, seller
    注意：无 premium 字段，如需溢价率需在因子层计算（实测修正）。
    索引：(trade_date, ts_code, event_id)
    """
    log.info("=== M22: block_trade ===")
    raw_dir = cfg.DATA_RAW / "block_trade"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/block_trade/ 不存在或为空，跳过")
        return

    raw = _load_csv_dir(raw_dir, "读取 block_trade")
    if raw.empty:
        log.warning("  block_trade 无有效数据，跳过")
        return

    raw["trade_date"] = _parse_yyyymmdd(raw["trade_date"])
    raw = _filter_date_range(raw)
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["trade_date", "ts_code", "price", "vol", "amount", "buyer", "seller"]
    avail = [c for c in keep if c in raw.columns]
    for col in ["price", "vol", "amount"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    raw = raw[avail].sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    raw["event_id"] = raw.groupby(["trade_date", "ts_code"]).cumcount().astype("int64")
    df = raw.set_index(["trade_date", "ts_code", "event_id"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "block_trade.parquet", "M22")
    log.info("  行数: %d", len(df))
    log.info("  M22 验证通过 ✓")


# ==========================================================================
# M23：机构调研 → stk_surv.parquet
# ==========================================================================

def process_stk_surv() -> None:
    """
    机构调研记录 → stk_surv.parquet

    来源：raw/stk_surv/（按股票 CSV）
    PIT：surv_date <= T
    输出字段：ts_code, name, surv_date, fund_visitors, rece_place, rece_mode,
              rece_org, org_type, comp_rece
    注意：无 org_cnt 字段，如需需后续聚合（实测修正）。
    索引：(surv_date, ts_code, event_id)
    """
    log.info("=== M23: stk_surv ===")
    raw_dir = cfg.DATA_RAW / "stk_surv"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/stk_surv/ 不存在或为空，跳过")
        return

    raw = _load_csv_dir(raw_dir, "读取 stk_surv")
    raw["surv_date"] = _parse_yyyymmdd(raw["surv_date"])
    raw = raw[raw["surv_date"] >= cfg.EARLY_START].copy()
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["surv_date", "ts_code", "name", "fund_visitors",
            "rece_place", "rece_mode", "rece_org", "org_type", "comp_rece"]
    avail = [c for c in keep if c in raw.columns]

    raw = raw[avail].sort_values(["surv_date", "ts_code"]).reset_index(drop=True)
    raw["event_id"] = raw.groupby(["surv_date", "ts_code"]).cumcount().astype("int64")
    df = raw.set_index(["surv_date", "ts_code", "event_id"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "stk_surv.parquet", "M23")
    log.info("  行数: %d", len(df))
    log.info("  M23 验证通过 ✓")


# ==========================================================================
# M24：主营业务构成（研究用）→ fina_mainbz_raw.parquet
# ==========================================================================

def process_fina_mainbz_raw() -> None:
    """
    主营业务构成（raw-like processed）→ fina_mainbz_raw.parquet

    来源：raw/fina_mainbz/（按股票 CSV）
    注意：无 ann_date 字段，不能直接生成 PIT 因子，仅供研究使用。
          如需用于策略，需先从财务 PIT 表派生保守可用日期。
    输出字段：ts_code, end_date, bz_item, bz_code, bz_sales, bz_profit, bz_cost, curr_type
    索引：(end_date, ts_code, bz_item)
    """
    log.info("=== M24: fina_mainbz_raw ===")
    raw_dir = cfg.DATA_RAW / "fina_mainbz"
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        log.warning("  raw/fina_mainbz/ 不存在或为空，跳过")
        return

    raw = _load_csv_dir(raw_dir, "读取 fina_mainbz")
    raw["end_date"] = _parse_yyyymmdd(raw["end_date"])
    raw = raw[raw["end_date"] >= cfg.EARLY_START].copy()
    raw["ts_code"] = raw["ts_code"].astype(str)

    keep = ["end_date", "ts_code", "bz_item", "bz_code",
            "bz_sales", "bz_profit", "bz_cost", "curr_type"]
    avail = [c for c in keep if c in raw.columns]
    for col in ["bz_sales", "bz_profit", "bz_cost"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")

    if "bz_item" in raw.columns:
        raw["bz_item"] = raw["bz_item"].fillna("").astype(str)

    dedup_keys = [k for k in ["end_date", "ts_code", "bz_item"] if k in raw.columns]
    raw = raw[avail].drop_duplicates(subset=dedup_keys).copy()
    df = raw.set_index(["end_date", "ts_code", "bz_item"]).sort_index()

    _save_parquet(df, cfg.DATA_PROC / "fina_mainbz_raw.parquet", "M24")
    log.info("  行数: %d（研究用，无 ann_date，不直接进 PIT 因子）", len(df))
    log.info("  M24 验证通过 ✓")


# ==========================================================================
# 阶段4验收：processed 覆盖报告
# ==========================================================================

def _write_coverage_report() -> None:
    """
    读取全部 processed Parquet，生成 reports/processed_coverage_report.md。

    对缺失文件只标记，不抛出异常，确保报告始终能写出。
    在 main() 以 module="all" 运行完所有模块后调用。
    """
    import datetime

    report_dir = cfg.DATA_PROC.parent.parent / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "processed_coverage_report.md"

    # (显示名, 文件路径, 时间索引层名, 要检查 NaN 率的关键列列表)
    CHECKS = [
        ("M1  daily_quote",      cfg.DATA_PROC / "daily_quote.parquet",      "trade_date",      ["ret", "close_adj"]),
        ("M2  daily_basic",      cfg.DATA_PROC / "daily_basic.parquet",       "trade_date",      ["free_share", "free_float_mv", "log_free_float_mv"]),
        ("M3  index_quote",      cfg.DATA_PROC / "index_quote.parquet",       "trade_date",      ["index_ret", "nav"]),
        ("M4  industry",         cfg.DATA_PROC / "industry.parquet",          "trade_date",      ["industry_code"]),
        ("M5  financial_pit",    cfg.DATA_PROC / "financial_pit.parquet",     "pit_date",        ["revenue", "total_assets"]),
        ("M6  indicator_pit",    cfg.DATA_PROC / "indicator_pit.parquet",     "pit_date",        ["roe", "debt_to_assets"]),
        ("M7  holder_pit",       cfg.DATA_PROC / "holder_pit.parquet",        "pit_date",        ["holder_num"]),
        ("M8  dividend_pit",     cfg.DATA_PROC / "dividend_pit.parquet",      "pit_date",        ["cash_div_tax"]),
        ("M9  margin",           cfg.DATA_PROC / "margin.parquet",            "trade_date",      ["rzye", "rqye"]),
        ("M10 moneyflow",        cfg.DATA_PROC / "moneyflow.parquet",         "trade_date",      ["net_mf_amount"]),
        ("M11 stock_status",     cfg.DATA_PROC / "stock_status.parquet",      "trade_date",      ["tradable"]),
        ("M12 index_member",     cfg.DATA_PROC / "index_member.parquet",      "rebalance_date",  ["index_weight", "tradable"]),
        ("M13 hk_hold",          cfg.DATA_PROC / "hk_hold.parquet",           "trade_date",      ["ratio"]),
        ("M14 analyst_rc_pit",   cfg.DATA_PROC / "analyst_rc_pit.parquet",    "pit_date",        ["eps"]),
        ("M15 share_float",      cfg.DATA_PROC / "share_float.parquet",       "ann_date",        ["float_ratio"]),
        ("M16 holder_trade_pit", cfg.DATA_PROC / "holder_trade_pit.parquet",  "pit_date",        ["change_vol"]),
        ("M17 cyq_perf",         cfg.DATA_PROC / "cyq_perf.parquet",          "trade_date",      ["winner_rate", "weight_avg"]),
        ("M18 pledge_stat",      cfg.DATA_PROC / "pledge_stat.parquet",       "end_date",        ["pledge_ratio"]),
        ("M19 moneyflow_hsgt",   cfg.DATA_PROC / "moneyflow_hsgt.parquet",    "trade_date",      ["north_money"]),
        ("M20 top_list",         cfg.DATA_PROC / "top_list.parquet",          "trade_date",      ["net_amount"]),
        ("M21 top_inst",         cfg.DATA_PROC / "top_inst.parquet",          "trade_date",      ["net_buy"]),
        ("M22 block_trade",      cfg.DATA_PROC / "block_trade.parquet",       "trade_date",      ["amount"]),
        ("M23 stk_surv",         cfg.DATA_PROC / "stk_surv.parquet",          "surv_date",       ["fund_visitors"]),
        ("M24 fina_mainbz_raw",  cfg.DATA_PROC / "fina_mainbz_raw.parquet",   "end_date",        ["bz_sales"]),
    ]

    def _get_date_range(df: pd.DataFrame, level: str) -> tuple[str, str]:
        idx = df.index
        if isinstance(idx, pd.MultiIndex) and level in idx.names:
            dates = idx.get_level_values(level)
        elif isinstance(idx, pd.DatetimeIndex):
            dates = idx
        else:
            return "N/A", "N/A"
        return dates.min().strftime("%Y-%m-%d"), dates.max().strftime("%Y-%m-%d")

    lines = [
        "# processed 数据覆盖报告",
        "",
        f"生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| 模块 | 文件 | 行数 | 时间范围 | 关键列 NaN 率 | 状态 |",
        "|------|------|------|----------|--------------|------|",
    ]

    file_issues: list[str] = []
    for label, path, date_level, key_cols in CHECKS:
        fname = path.name
        if not path.exists():
            lines.append(f"| {label} | {fname} | — | — | — | ❌ 文件缺失 |")
            file_issues.append(f"{label}: 文件缺失")
            continue
        try:
            df = pd.read_parquet(path)
            n = len(df)
            d_min, d_max = _get_date_range(df, date_level)
            nan_parts = []
            for col in key_cols:
                if col in df.columns:
                    nan_parts.append(f"{col}={df[col].isna().mean():.1%}")
            nan_summary = ", ".join(nan_parts) if nan_parts else "—"
            lines.append(f"| {label} | {fname} | {n:,} | {d_min} ~ {d_max} | {nan_summary} | ✅ |")
        except Exception as exc:
            lines.append(f"| {label} | {fname} | — | — | — | ❌ 读取失败: {exc} |")
            file_issues.append(f"{label}: 读取失败: {exc}")

    # ---- 验收检查 ----
    lines += ["", "## 阶段4验收检查", ""]

    def _acceptance(desc: str, path: Path, check_fn) -> str:
        if not path.exists():
            return f"- ❌ {desc}（文件缺失）"
        try:
            df = pd.read_parquet(path)
            ok, note = check_fn(df)
            tag = "✅" if ok else "❌"
            return f"- {tag} {desc}" + (f"（{note}）" if note else "")
        except Exception as exc:
            return f"- ❌ {desc}（读取失败: {exc}）"

    lines.append(_acceptance(
        "daily_quote.parquet 覆盖 2011 起",
        cfg.DATA_PROC / "daily_quote.parquet",
        lambda df: (
            df.index.get_level_values("trade_date").min() <= pd.Timestamp("2011-03-01"),
            f"最早 {df.index.get_level_values('trade_date').min().date()}",
        ),
    ))
    lines.append(_acceptance(
        "daily_basic.parquet 含 free_share / free_float_mv / log_free_float_mv",
        cfg.DATA_PROC / "daily_basic.parquet",
        lambda df: (
            all(c in df.columns for c in ["free_share", "free_float_mv", "log_free_float_mv"]),
            "",
        ),
    ))
    lines.append(_acceptance(
        "index_quote.parquet 行数 > 2000",
        cfg.DATA_PROC / "index_quote.parquet",
        lambda df: (len(df) > 2000, f"{len(df)} 行"),
    ))
    lines.append(_acceptance(
        "index_member.parquet 覆盖 2012 起",
        cfg.DATA_PROC / "index_member.parquet",
        lambda df: (
            df.index.get_level_values("rebalance_date").min() <= pd.Timestamp("2012-06-01"),
            f"最早 {df.index.get_level_values('rebalance_date').min().date()}",
        ),
    ))
    lines.append(_acceptance(
        "financial_pit.parquet 行数 > 50000",
        cfg.DATA_PROC / "financial_pit.parquet",
        lambda df: (len(df) > 50_000, f"{len(df):,} 行"),
    ))
    lines.append(_acceptance(
        "indicator_pit.parquet 行数 > 50000",
        cfg.DATA_PROC / "indicator_pit.parquet",
        lambda df: (len(df) > 50_000, f"{len(df):,} 行"),
    ))

    # 12 个新增备选 Parquet 是否全部存在
    alt_files = [
        "hk_hold.parquet", "analyst_rc_pit.parquet", "share_float.parquet",
        "holder_trade_pit.parquet", "cyq_perf.parquet", "pledge_stat.parquet",
        "moneyflow_hsgt.parquet", "top_list.parquet", "top_inst.parquet",
        "block_trade.parquet", "stk_surv.parquet", "fina_mainbz_raw.parquet",
    ]
    missing_alt = [f for f in alt_files if not (cfg.DATA_PROC / f).exists()]
    if missing_alt:
        lines.append(f"- ❌ 新增 Parquet 缺失: {missing_alt}")
    else:
        lines.append("- ✅ 全部 12 个新增 Parquet 文件均已生成")

    if file_issues:
        lines += ["", "## 问题汇总", ""]
        for iss in file_issues:
            lines.append(f"- {iss}")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("覆盖率报告已写入: %s", report_path)


# ==========================================================================
# 入口
# ==========================================================================

MODULES = {
    "daily":       [process_daily_quote, process_daily_basic],
    "index":       [process_index_quote],
    "industry":    [process_industry],
    "financial":   [process_financial_pit, process_indicator_pit,
                    process_holder_pit, process_dividend_pit],
    "status":      [process_margin, process_moneyflow, process_stock_status],
    "universe":    [process_index_member],   # 依赖 status 先完成
    "alternative": [                         # 12 个备选数据模块
        process_hk_hold, process_analyst_rc_pit, process_share_float,
        process_holder_trade_pit, process_cyq_perf, process_pledge_stat,
        process_moneyflow_hsgt, process_top_list, process_top_inst,
        process_block_trade, process_stk_surv, process_fina_mainbz_raw,
    ],
}
MODULES["all"] = (
    MODULES["daily"] + MODULES["index"] +
    MODULES["industry"] + MODULES["financial"] +
    MODULES["status"] + MODULES["universe"] +
    MODULES["alternative"]
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CSV → Parquet 数据处理（M1-M12）"
    )
    parser.add_argument(
        "module",
        choices=list(MODULES.keys()),
        help="要执行的模块组（daily/index/industry/financial/status/universe/alternative/all）",
    )
    args = parser.parse_args()

    cfg.DATA_PROC.mkdir(parents=True, exist_ok=True)

    for func in MODULES[args.module]:
        func()

    if args.module == "all":
        _write_coverage_report()

    log.info("全部完成。")


if __name__ == "__main__":
    main()
