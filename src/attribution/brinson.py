"""
src/attribution/brinson.py — BHB（Brinson-Hood-Beebower）行业归因
================================================================

公式（单期 T → T_next）：

  对每个行业 i：
    W_p_i  = Σ w_p[s],           s ∈ 行业 i         策略行业权重合计
    W_b_i  = Σ w_b[s],           s ∈ 行业 i         基准行业权重合计
    R_p_i  = Σ(w_p[s]·r[s]) / W_p_i                策略行业内加权收益
    R_b_i  = Σ(w_b[s]·r[s]) / W_b_i                基准行业内加权收益
    R_b    = Σ W_b_i · R_b_i                        基准总收益

  配置效应   = (W_p_i − W_b_i) × (R_b_i − R_b)
  选股效应   =  W_b_i          × (R_p_i − R_b_i)
  交叉效应   = (W_p_i − W_b_i) × (R_p_i − R_b_i)
  总超额     =  W_p_i · R_p_i  −  W_b_i · R_b_i

验证恒等式：Σ_i 总超额 = R_p_total − R_b_total

时间对齐：
  - 基准权重、行业映射来自月末调仓日 T（T 日收盘前已知）
  - 策略权重取 actual_weights 中 T 之后第一个可用日期（T+1 执行后收盘）
    策略权重保留原始权重和（≤1），现金仓位 = 1 - sum(w_p)（F8-002）
  - 期间收益：T+1 开盘买入，T_next 收盘卖出（F8-001）
    第一日: close_adj(T+1)/open_adj(T+1)-1；后续日: close_adj 收盘到收盘

数据依赖：
  - index_member.parquet  —— 基准权重（SW2021，百分比形式，除以 100 得小数）
  - industry.parquet      —— 行业分类（SW2021 一级，31 个行业 + 未分类）
  - daily_quote.parquet   —— close_adj / open_adj 列（后复权价格）
  - BacktestResult.actual_weights —— 策略日频权重
"""

import bisect
import logging
from typing import Optional

import numpy as np
import pandas as pd

from src import config as cfg
from src.data.loader import (
    get_rebalance_dates,
    load_daily_quote,
    load_industry,
    load_universe,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _get_period_list(
    rebalance_dates: list[pd.Timestamp],
    backtest_start: pd.Timestamp,
    backtest_end: pd.Timestamp,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """
    提取归因所需的月度期间列表 [(T, T_next), ...]。

    包含 backtest_start 前最后一个调仓日（pre-start），以覆盖第一段持仓；
    若不存在 pre-start 则从第一个在区间内的调仓日开始。

    期间收益计算范围为 (T, T_next]：T 当天收盘后建仓，T_next 当天收盘后平仓。

    Args:
        rebalance_dates: 全部可用月末调仓日（已升序排列，来自 get_rebalance_dates）
        backtest_start:  回测起始日（含）
        backtest_end:    回测结束日（含）
    Returns:
        [(T, T_next), ...]；至少 1 个期间，否则调用方应提前检查
    """
    pre_start  = [d for d in rebalance_dates if d < backtest_start]
    in_range   = [d for d in rebalance_dates if backtest_start <= d <= backtest_end]

    anchors: list[pd.Timestamp] = []
    if pre_start:
        anchors.append(max(pre_start))   # 最近一次 backtest_start 之前的调仓
    anchors.extend(in_range)

    if len(anchors) < 2:
        return []
    return [(anchors[i], anchors[i + 1]) for i in range(len(anchors) - 1)]


def _get_period_start_weights(
    actual_weights: pd.DataFrame,
    T: pd.Timestamp,
) -> pd.Series:
    """
    取调仓日 T 之后 actual_weights 中第一个可用日期的策略权重（≈ T+1 执行后持仓）。

    F8-002: 保留原始权重和，不归一化。现金仓位 = 1 - sum(w)，由调用方从
    period_summary 的 cash_weight 列获取，不得将策略收益与全投满仓收益混淆。

    Args:
        actual_weights: BacktestResult.actual_weights（date × ts_code，日频）
        T:              月末调仓日
    Returns:
        pd.Series（ts_code → weight，和 ≤ 1，原始权重）；若无可用日期返回空 Series
    """
    sorted_dates = sorted(actual_weights.index.tolist())
    idx = bisect.bisect_right(sorted_dates, T)
    if idx >= len(sorted_dates):
        log.warning("T=%s 之后无可用权重日期，跳过本期", T.date())
        return pd.Series(dtype=float)

    w = actual_weights.iloc[idx].dropna()
    w = w[w > 1e-8]
    w_sum = w.sum()
    if w_sum < 1e-6:
        log.warning("T=%s 策略权重和=0，跳过本期", T.date())
        return pd.Series(dtype=float)

    log.debug("T=%s 权重和=%.4f，估算现金仓位=%.4f", T.date(), w_sum, 1.0 - w_sum)
    return w   # F8-002: 不归一化，保留原始权重和


def _get_benchmark_weights(T: pd.Timestamp) -> pd.Series:
    """
    从 index_member 加载月末调仓日 T 的基准权重，归一化为比例（和=1）。

    index_weight 原始为百分比（和≈100），除以 sum 后得小数。

    Args:
        T: 月末调仓日（须在 index_member 中存在）
    Returns:
        pd.Series（ts_code → weight，和=1）
    Raises:
        KeyError:   T 不在 index_member 中
        ValueError: 权重全为零
    """
    snap  = load_universe(T)
    w_b   = snap["index_weight"].astype(float)
    total = w_b.sum()
    if total < 1e-6:
        raise ValueError(f"基准权重全为零: {T.date()}")
    return w_b / total


def _get_industry_map(
    T: pd.Timestamp,
    codes: list[str],
    industry_panel: pd.Series,
) -> pd.Series:
    """
    取调仓日 T 的行业代码映射（ts_code → industry_code）。

    从预加载的 industry_panel 中查找 T 日数据；缺失代码填 INDUSTRY_UNCLASSIFIED_CODE。

    Args:
        T:                月末调仓日
        codes:            需要映射的股票代码列表
        industry_panel:   load_industry 的 industry_code 列（MultiIndex: trade_date × ts_code）
    Returns:
        pd.Series（ts_code → industry_code，index=codes）
    """
    try:
        ind_at_T = industry_panel.loc[T]          # Series: ts_code → industry_code
    except KeyError:
        log.warning("industry_panel 中缺少 %s 的数据，全部设为未分类", T.date())
        return pd.Series(cfg.INDUSTRY_UNCLASSIFIED_CODE, index=codes)

    return ind_at_T.reindex(codes).fillna(cfg.INDUSTRY_UNCLASSIFIED_CODE)


def _compute_period_returns_t1_open(
    T: pd.Timestamp,
    T_next: pd.Timestamp,
    codes: list[str],
    close_adj_panel: pd.DataFrame,
    open_adj_panel: pd.DataFrame,
) -> tuple[pd.Series, int]:
    """
    计算持有期 (T, T_next] 收益：T+1 开盘买入，T_next 收盘卖出。（F8-001）

    收益结构：
      - 第一日 (T 后首个交易日): close_adj / open_adj - 1（从开盘到收盘）
      - 后续日: close_adj(t) / close_adj(t-1) - 1（收盘到收盘）
      - 停牌 NaN → 0 日收益，与回测引擎一致

    与回测引擎 T+1 开盘成交假设保持一致，不包含 T 收盘到 T+1 开盘的隔夜段。

    Args:
        T:               期间起始调仓日（不含，T 日盘后确定权重）
        T_next:          期间结束调仓日（含，T_next 日收盘为出场价）
        codes:           需计算的股票代码列表
        close_adj_panel: 后复权收盘价面板（trade_date × ts_code）
        open_adj_panel:  后复权开盘价面板（trade_date × ts_code）
    Returns:
        (累计收益 pd.Series[code → return], n_missing_return 缺少价格数据的股票数)
    """
    in_period = close_adj_panel.index[
        (close_adj_panel.index > T) & (close_adj_panel.index <= T_next)
    ]
    n_missing = len([c for c in codes if c not in close_adj_panel.columns])

    if len(in_period) == 0:
        return pd.Series(0.0, index=codes), len(codes)

    valid_codes = [
        c for c in codes
        if c in close_adj_panel.columns and c in open_adj_panel.columns
    ]
    if not valid_codes:
        return pd.Series(0.0, index=codes), len(codes)

    first_date = in_period[0]

    # 第一日：收盘 / 开盘 - 1（T+1 开盘入场）
    first_close = close_adj_panel.loc[first_date, valid_codes]
    first_open  = open_adj_panel.loc[first_date, valid_codes]
    first_ret   = (first_close / first_open - 1.0).fillna(0.0)
    cum_prod    = 1.0 + first_ret

    if len(in_period) > 1:
        # 后续日：收盘到收盘（基于 close_adj 面板内的 pct_change）
        close_slice    = close_adj_panel.loc[first_date:in_period[-1], valid_codes]
        remaining_rets = close_slice.pct_change(fill_method=None).fillna(0.0)
        remaining_dates = in_period[1:]
        cum_prod = cum_prod * (1.0 + remaining_rets.loc[remaining_dates]).prod()

    result = cum_prod - 1.0
    return result.reindex(codes).fillna(0.0), n_missing


def _compute_period_brinson(
    w_p: pd.Series,
    w_b: pd.Series,
    r: pd.Series,
    industry_map: pd.Series,
) -> pd.DataFrame:
    """
    对单期执行 BHB 行业归因分解。

    BHB 四项效应全部使用向量化计算，无 groupby.apply，兼容 pandas 2.0+。

    【重要】BHB 可加性：alloc_i + select_i + interact_i ≠ total_i（单行业不成立）。
    差值 = ΔW_i × R_b_total，该项加总后为零（Σ ΔW_i = 0），因此：
      Σ_i (alloc + select + interact) = Σ_i total  ← 汇总层成立，是正确性验证点。

    注意：当 w_p 未归一化时（F8-002），Σ W_p_i < 1，Σ(W_p_i - W_b_i) = -cash_weight < 0，
    配置效应之和不再为零。strategy_return = Σ W_p_i·R_p_i 反映真实股票部分的净值贡献。

    Args:
        w_p:          策略权重（ts_code → weight，和 ≤ 1，原始权重）
        w_b:          基准权重（ts_code → weight，和=1）
        r:            期间累计收益（ts_code → return）
        industry_map: ts_code → industry_code；index 覆盖 w_p ∪ w_b 的全部股票
    Returns:
        index=industry_code 的 DataFrame，列：
        W_p, W_b, R_p, R_b,
        allocation_effect, selection_effect, interaction_effect, total_effect
    """
    all_codes = industry_map.index.tolist()

    df = pd.DataFrame({
        "industry_code": industry_map,
        "w_p": w_p.reindex(all_codes).fillna(0.0),
        "w_b": w_b.reindex(all_codes).fillna(0.0),
        "r":   r.reindex(all_codes).fillna(0.0),
    })
    # 预计算加权收益，避免 groupby.apply
    df["w_p_r"] = df["w_p"] * df["r"]
    df["w_b_r"] = df["w_b"] * df["r"]

    grp  = df.groupby("industry_code")
    W_p  = grp["w_p"].sum()
    W_b  = grp["w_b"].sum()
    wp_r = grp["w_p_r"].sum()
    wb_r = grp["w_b_r"].sum()

    # 行业内加权平均收益（权重和为零时约定 R=0，allocation/total 中该项贡献自然为 0）
    R_p = (wp_r / W_p.where(W_p > 1e-10)).fillna(0.0)
    R_b = (wb_r / W_b.where(W_b > 1e-10)).fillna(0.0)

    R_b_total = float((W_b * R_b).sum())

    allocation_effect  = (W_p - W_b) * (R_b - R_b_total)
    selection_effect   =  W_b        * (R_p - R_b)
    interaction_effect = (W_p - W_b) * (R_p - R_b)
    total_effect       =  W_p * R_p  -  W_b * R_b

    result = pd.DataFrame({
        "W_p":                W_p,
        "W_b":                W_b,
        "R_p":                R_p,
        "R_b":                R_b,
        "allocation_effect":  allocation_effect,
        "selection_effect":   selection_effect,
        "interaction_effect": interaction_effect,
        "total_effect":       total_effect,
    })
    result.index.name = "industry_code"
    return result


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


def compute_brinson_attribution(
    actual_weights: pd.DataFrame,
    backtest_start: pd.Timestamp,
    backtest_end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    BHB 行业归因：将每月超额收益分解为配置效应、选股效应、交叉效应。

    时间对齐假设：
      - 基准权重、行业映射取自月末调仓日 T（月末收盘前已可知）
      - 策略权重取 actual_weights 中 T 之后第一个可用日期（T+1 执行后收盘）
      - 策略权重保留原始权重和（F8-002），不归一化为满仓
      - 期间收益 = T+1 开盘买入至 T_next 收盘（F8-001），与回测引擎 T+1 成交假设一致
      - backtest_start 前最近一次调仓日作为第一期起点，防止首月无法归因

    数据依赖（内部加载，调用前无需预加载）：
      index_member.parquet / industry.parquet / daily_quote.parquet（close_adj, open_adj）

    F8-003: 内部加载完整基准成分股收益，无需 notebook 外部增广。

    Args:
        actual_weights: BacktestResult.actual_weights（date × ts_code 日频权重面板）
        backtest_start: 回测起始日（含）
        backtest_end:   回测结束日（含）
    Returns:
        industry_attr:  每期每行业归因效应
            index:   (period_start, industry_code) MultiIndex
            columns: W_p, W_b, R_p, R_b, industry_name, is_financial,
                     allocation_effect, selection_effect,
                     interaction_effect, total_effect
        period_summary: 每期汇总（各效应在行业维度加总）
            index:   period_start（月末调仓日）
            columns: period_end, strategy_return, benchmark_return,
                     excess_return, cash_weight, weight_sum,
                     allocation_effect, selection_effect,
                     interaction_effect, total_effect
    Raises:
        ValueError: 区间内月度期间数为 0
    """
    all_rebalance_dates = get_rebalance_dates()
    periods = _get_period_list(all_rebalance_dates, backtest_start, backtest_end)

    if not periods:
        raise ValueError(
            f"[{backtest_start.date()}, {backtest_end.date()}] 内月度期间为 0，"
            "请检查 backtest_start / backtest_end 是否在 index_member 范围内"
        )

    log.info(
        "Brinson 归因：%d 个月度期间，回测区间 [%s, %s]",
        len(periods), backtest_start.date(), backtest_end.date(),
    )

    # ── F8-003: 收集完整基准成分股代码（避免基准收益填0的错误） ─────────────────
    strategy_codes: set[str] = set(
        c for c in actual_weights.columns if actual_weights[c].notna().any()
    )
    benchmark_codes: set[str] = set()
    for T, _ in periods:
        try:
            snap = load_universe(T)
            benchmark_codes.update(str(c) for c in snap.index)
        except Exception as exc:
            log.warning("T=%s 基准成分股代码收集失败（%s），跳过", T.date(), exc)

    all_attr_codes = sorted(strategy_codes | benchmark_codes)
    log.info(
        "归因股票代码：策略 %d 只 + 基准 %d 只 = 合计 %d 只",
        len(strategy_codes), len(benchmark_codes), len(all_attr_codes),
    )

    # ── F8-001: 加载后复权收盘价与开盘价（替代 ret 列） ───────────────────────
    log.info("加载后复权价格数据（%d 只股票）...", len(all_attr_codes))
    dq = load_daily_quote(backtest_start, backtest_end, all_attr_codes)
    close_adj_panel: pd.DataFrame = dq["close_adj"].unstack("ts_code")
    open_adj_panel:  pd.DataFrame = dq["open_adj"].unstack("ts_code")

    earliest_T = periods[0][0]
    log.info("加载行业数据（起点 %s）...", earliest_T.date())
    ind_data       = load_industry(earliest_T, backtest_end)
    industry_panel = ind_data["industry_code"]   # MultiIndex (trade_date, ts_code)

    # industry_code → industry_name（静态映射，取一次即可）
    code_name_map: dict[str, str] = {}
    try:
        ind_slice   = ind_data.xs(earliest_T, level="trade_date")
        unique_rows = (
            ind_slice.reset_index()[["ts_code", "industry_code", "industry_name"]]
            .drop_duplicates("industry_code")
        )
        code_name_map = dict(zip(unique_rows["industry_code"], unique_rows["industry_name"]))
    except (KeyError, Exception) as exc:
        log.warning("行业名称映射加载失败（%s），industry_name 列将为 NaN", exc)
    code_name_map.setdefault(cfg.INDUSTRY_UNCLASSIFIED_CODE, cfg.INDUSTRY_UNCLASSIFIED_NAME)

    # ── 逐期归因 ──────────────────────────────────────────────────────────────
    industry_rows: list[pd.DataFrame] = []
    period_rows:   list[dict]         = []

    for T, T_next in periods:

        # 1. 策略权重（T+1 执行后，原始权重不归一化）
        w_p = _get_period_start_weights(actual_weights, T)
        if w_p.empty:
            continue
        w_p_sum     = float(w_p.sum())
        cash_weight = 1.0 - w_p_sum

        # 2. 基准权重（T 日月末，归一化）
        try:
            w_b = _get_benchmark_weights(T)
        except (KeyError, ValueError) as exc:
            log.warning("T=%s 基准权重加载失败（%s），跳过", T.date(), exc)
            continue

        # 3. 行业映射（T 日，覆盖策略 + 基准的并集）
        universe_codes = sorted(set(w_p.index) | set(w_b.index))
        industry_map   = _get_industry_map(T, universe_codes, industry_panel)

        # 4. 期间收益（T+1 开盘买入口径，F8-001）
        r, n_missing = _compute_period_returns_t1_open(
            T, T_next, universe_codes, close_adj_panel, open_adj_panel
        )
        if n_missing > 0:
            log.debug("T=%s 有 %d 只股票缺少价格数据（收益填 0）", T.date(), n_missing)

        # 5. 单期 BHB 分解
        raw_df = _compute_period_brinson(w_p, w_b, r, industry_map)

        # 6. 期间汇总（从 raw_df 聚合，保证内部一致性）
        strategy_return  = float((raw_df["W_p"] * raw_df["R_p"]).sum())
        benchmark_return = float((raw_df["W_b"] * raw_df["R_b"]).sum())

        period_rows.append({
            "period_start":       T,
            "period_end":         T_next,
            "strategy_return":    strategy_return,   # 股票部分净值贡献（不含现金）
            "benchmark_return":   benchmark_return,
            "excess_return":      strategy_return - benchmark_return,
            "cash_weight":        cash_weight,       # F8-002: 现金仓位
            "weight_sum":         w_p_sum,           # F8-002: 股票权重和
            "allocation_effect":  float(raw_df["allocation_effect"].sum()),
            "selection_effect":   float(raw_df["selection_effect"].sum()),
            "interaction_effect": float(raw_df["interaction_effect"].sum()),
            "total_effect":       float(raw_df["total_effect"].sum()),
        })

        # 7. 添加 period_start 层，拼接到 industry_rows
        raw_df = raw_df.copy()
        raw_df["period_start"] = T
        raw_df = raw_df.reset_index().set_index(["period_start", "industry_code"])
        industry_rows.append(raw_df)

    if not industry_rows:
        raise ValueError("所有期间均归因失败，请检查数据完整性")

    # ── 合并结果 ──────────────────────────────────────────────────────────────
    industry_attr   = pd.concat(industry_rows)
    period_summary  = pd.DataFrame(period_rows).set_index("period_start")

    # 附加行业名称与金融板块标记
    industry_codes = industry_attr.index.get_level_values("industry_code")
    industry_attr["industry_name"] = industry_codes.map(code_name_map)
    industry_attr["is_financial"]  = industry_codes.isin(cfg.FINANCIAL_SECTOR_CODES)

    # 内部一致性双验证
    # V1：strategy_return - benchmark_return ≈ total_effect（行业汇总层恒等式）
    v1_discrepancy = (
        (period_summary["strategy_return"] - period_summary["benchmark_return"])
        - period_summary["total_effect"]
    ).abs().max()
    # V2：alloc + select + interact ≈ total_effect（BHB 汇总层恒等式）
    v2_discrepancy = (
        period_summary[["allocation_effect", "selection_effect", "interaction_effect"]].sum(axis=1)
        - period_summary["total_effect"]
    ).abs().max()
    if v1_discrepancy > 1e-8 or v2_discrepancy > 1e-8:
        log.warning(
            "BHB 验证失败：V1 偏差=%.2e  V2 偏差=%.2e（理论均应 < 1e-8）",
            v1_discrepancy, v2_discrepancy,
        )
    else:
        log.info(
            "BHB 验证通过：V1=%.2e  V2=%.2e",
            v1_discrepancy, v2_discrepancy,
        )

    log.info(
        "Brinson 归因完成：%d 期，行业归因行数 %d，平均现金仓位=%.2f%%",
        len(period_summary), len(industry_attr),
        period_summary["cash_weight"].mean() * 100,
    )
    return industry_attr, period_summary


def summarize_by_segment(
    period_summary: pd.DataFrame,
    segments: Optional[list[tuple[pd.Timestamp, pd.Timestamp]]] = None,
) -> pd.DataFrame:
    """
    按时间段汇总 Brinson 归因的各效应（简单加总）。

    Brinson 模型假设收益线性可加（算术收益），跨期加总是标准做法。
    若需几何链接（精确，但本项目月度误差极小），在 notebook 中另行处理。

    Args:
        period_summary: compute_brinson_attribution 的第二个返回值
        segments:       [(start, end), ...] 自定义时间段；
                        None 时按年度汇总（取 period_start 的年份）
    Returns:
        每段汇总 DataFrame，列同 period_summary（去掉 period_end），
        新增 n_periods（有效期数）和 win_rate（超额 > 0 的月份比例）
    """
    effect_cols = [
        "strategy_return", "benchmark_return", "excess_return",
        "allocation_effect", "selection_effect", "interaction_effect", "total_effect",
    ]

    if segments is None:
        # 默认按年度分组
        groups = period_summary.groupby(period_summary.index.year)
        rows = []
        for year, grp in groups:
            row = grp[effect_cols].sum().to_dict()
            row["segment"]    = str(year)
            row["n_periods"]  = len(grp)
            row["win_rate"]   = float((grp["excess_return"] > 0).mean())
            rows.append(row)
        return pd.DataFrame(rows).set_index("segment")

    rows = []
    for start, end in segments:
        mask = (period_summary.index >= start) & (period_summary.index <= end)
        grp  = period_summary.loc[mask]
        if grp.empty:
            continue
        row = grp[effect_cols].sum().to_dict()
        row["segment"]   = f"{start.date()}~{end.date()}"
        row["n_periods"] = len(grp)
        row["win_rate"]  = float((grp["excess_return"] > 0).mean())
        rows.append(row)

    if not rows:
        return pd.DataFrame(columns=effect_cols + ["segment", "n_periods", "win_rate"])
    return pd.DataFrame(rows).set_index("segment")
