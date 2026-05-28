"""
src/attribution/factor_attr.py — 因子收益归因
================================================================

方法：横截面加权最小二乘（WLS）回归，每月一次。

流程（单期 T → T_next）：
  1. 从 T 日因子面板提取各因子横截面值（已预处理/标准化）
  2. 按因子组分组，方向调整后等权合成"组合因子"
     （方向来自 factor_summary.csv，F8-005；默认只用 final_factors.json 中的入模因子，F8-004）
  3. WLS 回归估计因子组收益：
       r_i = α + Σ_k β_k × g_ki + ε_i
     权重 = w_b_i（基准成份权重，更重视指数权重大的股票）
     β_k 即该期因子组 k 的截面因子收益（因子每单位暴露的收益）
  4. 计算策略与基准的暴露差：
       ΔE_k = Σ_i w_p_i × g_ki − Σ_i w_b_i × g_ki
  5. 因子组贡献：contribution_k = β_k × ΔE_k
  6. 残差 = 实际超额 − Σ_k contribution_k
     （含个股特异收益、交易成本差异、模型拟合误差）

时间对齐：
  - 因子值、基准权重：月末调仓日 T（T 日收盘前已知）
  - 策略权重：actual_weights 中 T 之后第一个可用日期（T+1 执行后收盘）
    策略权重保留原始权重和（≤1），现金仓位 = 1 - sum(w_p)（F8-002）
  - 期间收益：T+1 开盘买入，T_next 收盘卖出（F8-001）

数据依赖：
  data/processed/factor_panels/{name}.parquet  —— 因子横截面面板
  data/processed/daily_quote.parquet           —— close_adj / open_adj 后复权价格
  data/processed/index_member.parquet          —— 基准权重
  reports/factor_evaluation/final_factors.json —— 最终入模因子列表（F8-004）
  reports/factor_evaluation/factor_summary.csv —— 因子方向（F8-005）

注意：该模块与 brinson.py 共用相同的期间划分逻辑和 T+1 权重逻辑，
两者的 strategy_return / benchmark_return 序列应高度一致（最大偏差 < 1e-6）。
"""

import bisect
import json
import logging
from typing import Optional

import numpy as np
import pandas as pd

from src import config as cfg
from src.data.loader import get_rebalance_dates, load_daily_quote, load_universe

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_FACTOR_GROUPS: dict[str, str] = {
    # 价值
    "ep_ttm":           "value",
    "bp":               "value",
    "sp_ttm":           "value",
    "dy_ttm":           "value",
    "cfp":              "value",
    "fcfp":             "value",
    # 质量
    "roe":              "quality",
    "roa":              "quality",
    "gross_margin":     "quality",
    "asset_turn":       "quality",
    "leverage":         "quality",
    "accrual":          "quality",
    "piotroski_f":      "quality",
    "gross_margin_trend": "quality",
    "roe_smoothed_4q":  "quality",
    "garp":             "quality",
    # 成长
    "np_yoy":           "growth",
    "rev_yoy":          "growth",
    "roe_delta":        "growth",
    "q_roe":            "growth",
    "roe_delta_3q":     "growth",
    "analyst_eps_revision": "growth",
    "analyst_rating_chg":   "growth",
    "rev_acceleration": "growth",
    # 动量
    "ret_1m":           "momentum",
    "mom_6_1":          "momentum",
    "mom_12_1":         "momentum",
    "holder_chg":       "momentum",
    "high_52w":         "momentum",
    "high_52w_v2":      "momentum",
    "ind_adj_mom":      "momentum",
    "mom_risk_adj":     "momentum",
    # 波动率
    "vol_60d":          "volatility",
    "ivol_60d":         "volatility",
    "max_ret":          "volatility",
    # 流动性
    "turn_20d":         "liquidity",
    "amihud":           "liquidity",
    # 资金流向
    "margin_ratio":     "fund_flow",
    "short_ratio":      "fund_flow",
    "large_net_inflow": "fund_flow",
    "hk_hold_ratio":    "fund_flow",
    "hk_hold_chg":      "fund_flow",
    "insider_net_buy":  "fund_flow",
    "pledge_ratio":     "fund_flow",
    "float_pct_30d":    "fund_flow",
}

# 输出中因子组列的固定顺序
GROUP_ORDER: list[str] = [
    "value", "quality", "growth", "momentum",
    "volatility", "liquidity", "fund_flow",
]

MIN_STOCKS_FOR_REGRESSION: int = 50   # 回归所需最少有效股票数


# ---------------------------------------------------------------------------
# F8-004: 加载最终入模因子映射
# ---------------------------------------------------------------------------

def _load_final_factor_group_map() -> dict[str, str]:
    """
    从 final_factors.json 加载最终入模因子，并映射到因子组。（F8-004）

    默认从 reports/factor_evaluation/final_factors.json 读取 final_factors 列表，
    使用 DEFAULT_FACTOR_GROUPS 确定每个因子所属的组。

    若文件不存在或为空，退回到 DEFAULT_FACTOR_GROUPS（28 个候选因子）并记录 warning。
    退回到候选因子池的归因应命名为 style_factor_attribution，不等同于策略 alpha 归因。

    Returns:
        dict，key=因子名，value=因子组名
    """
    final_factors_path = cfg.ROOT / "reports" / "factor_evaluation" / "final_factors.json"
    if not final_factors_path.exists():
        log.warning(
            "final_factors.json 不存在（%s），退回到 DEFAULT_FACTOR_GROUPS（候选因子池）",
            final_factors_path,
        )
        return DEFAULT_FACTOR_GROUPS

    try:
        with open(final_factors_path, encoding="utf-8") as f:
            data = json.load(f)
        final_factors: list[str] = data.get("final_factors", [])
    except Exception as exc:
        log.warning("final_factors.json 读取失败（%s），退回到 DEFAULT_FACTOR_GROUPS", exc)
        return DEFAULT_FACTOR_GROUPS

    if not final_factors:
        log.warning("final_factors.json 中 final_factors 为空，退回到 DEFAULT_FACTOR_GROUPS")
        return DEFAULT_FACTOR_GROUPS

    group_map: dict[str, str] = {}
    unknown: list[str] = []
    for factor in final_factors:
        if factor in DEFAULT_FACTOR_GROUPS:
            group_map[factor] = DEFAULT_FACTOR_GROUPS[factor]
        else:
            unknown.append(factor)
            log.warning("因子 %s 不在 DEFAULT_FACTOR_GROUPS 中，从归因中排除", factor)

    log.info(
        "F8-004: 从 final_factors.json 加载 %d 个最终入模因子（跳过 %d 个未知因子）",
        len(group_map), len(unknown),
    )
    return group_map


# ---------------------------------------------------------------------------
# F8-005: 从 factor_summary.csv 加载因子方向
# ---------------------------------------------------------------------------

def _load_factor_directions_from_summary() -> dict[str, int]:
    """
    从 factor_summary.csv 读取因子方向，作为唯一权威来源。（F8-005）

    factor_summary.csv 由单因子评价阶段生成，包含 `factor` 和 `factor_direction` 列。
    若文件不存在或缺少必要列，退回到从 FACTOR_DIRECTIONS 硬编码表并记录 warning。

    对方向缺失的因子不提供默认值（不用 +1 补位），调用方应在 _build_group_composites
    中对方向缺失的因子报 warning 并跳过，保证归因方向可追溯。

    Returns:
        dict，key=因子名，value=方向（+1 或 -1）
    """
    summary_path = cfg.ROOT / "reports" / "factor_evaluation" / "factor_summary.csv"
    if not summary_path.exists():
        log.warning(
            "factor_summary.csv 不存在（%s），退回到 FACTOR_DIRECTIONS 硬编码表",
            summary_path,
        )
        from src.signal.combiner import FACTOR_DIRECTIONS
        return {k: int(v) for k, v in FACTOR_DIRECTIONS.items()}

    try:
        df = pd.read_csv(summary_path)
    except Exception as exc:
        log.warning("factor_summary.csv 读取失败（%s），退回到 FACTOR_DIRECTIONS", exc)
        from src.signal.combiner import FACTOR_DIRECTIONS
        return {k: int(v) for k, v in FACTOR_DIRECTIONS.items()}

    if "factor" not in df.columns or "factor_direction" not in df.columns:
        log.warning("factor_summary.csv 缺少 factor/factor_direction 列，退回到 FACTOR_DIRECTIONS")
        from src.signal.combiner import FACTOR_DIRECTIONS
        return {k: int(v) for k, v in FACTOR_DIRECTIONS.items()}

    directions: dict[str, int] = {}
    for _, row in df.iterrows():
        direction = row["factor_direction"]
        if pd.notna(direction):
            directions[str(row["factor"])] = int(direction)

    log.info("F8-005: 从 factor_summary.csv 加载 %d 个因子方向", len(directions))
    return directions


# ---------------------------------------------------------------------------
# 私有辅助函数
# ---------------------------------------------------------------------------

def _get_period_list(
    rebalance_dates: list[pd.Timestamp],
    backtest_start: pd.Timestamp,
    backtest_end: pd.Timestamp,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """
    提取归因所需的月度期间列表 [(T, T_next), ...]。

    包含 backtest_start 前最近一次调仓日（pre-start anchor），
    使第一段持仓可以被正确归因；逻辑与 brinson._get_period_list 完全一致。

    Args:
        rebalance_dates: 全部可用月末调仓日（升序）
        backtest_start:  回测起始日（含）
        backtest_end:    回测结束日（含）
    Returns:
        [(T, T_next), ...]；空列表表示区间内无有效期间
    """
    pre_start = [d for d in rebalance_dates if d < backtest_start]
    in_range  = [d for d in rebalance_dates if backtest_start <= d <= backtest_end]

    anchors: list[pd.Timestamp] = []
    if pre_start:
        anchors.append(max(pre_start))
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

    F8-002: 保留原始权重和，不归一化为满仓。逻辑与 brinson._get_period_start_weights 一致。

    Args:
        actual_weights: BacktestResult.actual_weights（date × ts_code 日频）
        T:              月末调仓日
    Returns:
        pd.Series（ts_code → weight，和 ≤ 1）；若无可用日期返回空 Series
    """
    sorted_dates = sorted(actual_weights.index.tolist())
    idx = bisect.bisect_right(sorted_dates, T)
    if idx >= len(sorted_dates):
        log.warning("T=%s 之后无可用权重日期，跳过", T.date())
        return pd.Series(dtype=float)

    w = actual_weights.iloc[idx].dropna()
    w = w[w > 1e-8]
    w_sum = w.sum()
    if w_sum < 1e-6:
        log.warning("T=%s 策略权重和=0，跳过", T.date())
        return pd.Series(dtype=float)

    log.debug("T=%s 权重和=%.4f，估算现金仓位=%.4f", T.date(), w_sum, 1.0 - w_sum)
    return w   # F8-002: 不归一化


def _get_benchmark_weights(T: pd.Timestamp) -> pd.Series:
    """
    从 index_member 加载月末调仓日 T 的基准权重，归一化为比例（和=1）。

    index_weight 原始为百分比形式（和≈100）。

    Args:
        T: 月末调仓日
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


def _compute_period_returns_t1_open(
    T: pd.Timestamp,
    T_next: pd.Timestamp,
    codes: list[str],
    close_adj_panel: pd.DataFrame,
    open_adj_panel: pd.DataFrame,
) -> tuple[pd.Series, int]:
    """
    计算持有期 (T, T_next] 收益：T+1 开盘买入，T_next 收盘卖出。（F8-001）

    逻辑与 brinson._compute_period_returns_t1_open 完全一致，保证两模块结果可对比。

    Args:
        T:               期间起始调仓日（不含）
        T_next:          期间结束调仓日（含）
        codes:           需计算的股票代码
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

    first_close = close_adj_panel.loc[first_date, valid_codes]
    first_open  = open_adj_panel.loc[first_date, valid_codes]
    first_ret   = (first_close / first_open - 1.0).fillna(0.0)
    cum_prod    = 1.0 + first_ret

    if len(in_period) > 1:
        close_slice    = close_adj_panel.loc[first_date:in_period[-1], valid_codes]
        remaining_rets = close_slice.pct_change(fill_method=None).fillna(0.0)
        remaining_dates = in_period[1:]
        cum_prod = cum_prod * (1.0 + remaining_rets.loc[remaining_dates]).prod()

    result = cum_prod - 1.0
    return result.reindex(codes).fillna(0.0), n_missing


def _load_available_factor_panels(
    factor_names: list[str],
) -> dict[str, pd.DataFrame]:
    """
    从 data/processed/factor_panels/ 批量加载因子面板。

    面板格式：index=rebalance_date，columns=ts_code，值=已预处理的因子值（含 NaN）。

    Args:
        factor_names: 待加载的因子名列表
    Returns:
        dict，key=因子名，value=因子面板 DataFrame；缺失文件的因子自动跳过
    """
    panels: dict[str, pd.DataFrame] = {}
    for name in factor_names:
        path = cfg.DATA_PROC / "factor_panels" / f"{name}.parquet"
        if not path.exists():
            log.debug("因子面板文件不存在，跳过: %s.parquet", name)
            continue
        try:
            panels[name] = pd.read_parquet(path)
        except Exception as exc:
            log.warning("因子面板加载失败 %s: %s", name, exc)
    log.info("因子面板加载完成：%d / %d 个", len(panels), len(factor_names))
    return panels


def _build_group_composites(
    factor_panels: dict[str, pd.DataFrame],
    T: pd.Timestamp,
    factor_group_map: dict[str, str],
    factor_directions: dict[str, int],
) -> dict[str, pd.Series]:
    """
    在调仓日 T，将各因子方向调整后按组等权合成"组合因子"。

    F8-005: 方向来自 factor_summary.csv（通过 factor_directions 参数传入），不再使用
    硬编码的 FACTOR_DIRECTIONS。方向缺失的因子报 warning 并跳过，不默认 +1。

    组内等权均值：pd.DataFrame.mean(axis=1)，NaN-safe（某因子缺失不影响同组其他因子）。
    全组 NaN 的组合因子被排除，不参与本期回归。

    Args:
        factor_panels:    dict，key=因子名，value=因子面板（index=rebalance_date）
        T:                月末调仓日
        factor_group_map: 因子名 → 因子组名的映射
        factor_directions: 因子名 → 方向（+1/-1），来自 factor_summary.csv
    Returns:
        dict，key=因子组名，value=pd.Series（ts_code → 组合因子值，可含 NaN）
    """
    group_series: dict[str, list[pd.Series]] = {}

    for factor_name, group in factor_group_map.items():
        panel = factor_panels.get(factor_name)
        if panel is None:
            continue
        if T not in panel.index:
            continue

        direction = factor_directions.get(factor_name)
        if direction is None:
            log.warning(
                "因子 %s 方向缺失（不在 factor_summary.csv 中），跳过该因子",
                factor_name,
            )
            continue

        s = panel.loc[T].astype(float) * int(direction)
        if s.notna().any():
            group_series.setdefault(group, []).append(s)

    result: dict[str, pd.Series] = {}
    for group, series_list in group_series.items():
        composite = pd.concat(series_list, axis=1).mean(axis=1)
        if composite.notna().any():
            result[group] = composite

    return result


def _run_wls_regression(
    group_composites: dict[str, pd.Series],
    stock_returns: pd.Series,
    wls_weights: pd.Series,
    min_stocks: int = MIN_STOCKS_FOR_REGRESSION,
) -> tuple[dict[str, float], float, int]:
    """
    横截面 WLS 回归估计各因子组收益。

    模型：r_i = α + Σ_k β_k × g_ki + ε_i，权重 = w_b_i。
    WLS 变换：将 X、y 乘以 sqrt(w_b) 后调用 numpy.linalg.lstsq。

    截距 α 表示基准收益的公共项，在超额收益归因中自然消除（α 对 w_p 和 w_b 的贡献相同）。

    Args:
        group_composites: group_name → (ts_code → 组合因子值)
        stock_returns:    ts_code → 期间累计收益
        wls_weights:      ts_code → 基准权重（用作 WLS 权重）
        min_stocks:       有效股票最少数量
    Returns:
        (factor_returns, r2_weighted, n_stocks_used)
        factor_returns:  group_name → β_k；回归失败时各值为 NaN
        r2_weighted:     加权 R²；无效时为 NaN
        n_stocks_used:   实际参与回归的股票数
    """
    group_names = sorted(group_composites.keys())
    if not group_names:
        return {}, np.nan, 0

    # 仅保留有完整数据（所有组合因子、收益、正权重）的股票
    df = pd.DataFrame({g: group_composites[g] for g in group_names})
    df["__r__"] = stock_returns.reindex(df.index)
    df["__w__"] = wls_weights.reindex(df.index).fillna(0.0)
    df = df.dropna(subset=group_names + ["__r__"])
    df = df[df["__w__"] > 1e-10]

    n_stocks = len(df)
    if n_stocks < min_stocks:
        log.debug("WLS 有效股票数 %d < %d，跳过回归", n_stocks, min_stocks)
        return {g: np.nan for g in group_names}, np.nan, n_stocks

    X = df[group_names].values.astype(float)    # (n_stocks, n_groups)
    y = df["__r__"].values.astype(float)
    w = df["__w__"].values.astype(float)

    # 加入截距列（第 0 列）
    X_with_const = np.column_stack([np.ones(n_stocks), X])

    # WLS 变换：乘以 sqrt(w)
    sqrt_w = np.sqrt(w)
    X_w    = X_with_const * sqrt_w[:, np.newaxis]
    y_w    = y * sqrt_w

    try:
        coef, _, _, _ = np.linalg.lstsq(X_w, y_w, rcond=None)
    except np.linalg.LinAlgError as exc:
        log.warning("WLS 求解失败: %s", exc)
        return {g: np.nan for g in group_names}, np.nan, n_stocks

    # 加权 R²
    y_pred  = X_with_const @ coef
    ss_res  = float(np.sum(w * (y - y_pred) ** 2))
    y_mean  = float(np.average(y, weights=w))
    ss_tot  = float(np.sum(w * (y - y_mean) ** 2))
    r2      = (1.0 - ss_res / ss_tot) if ss_tot > 1e-12 else np.nan

    # coef[0] = 截距，coef[1:] = 各因子组收益 β_k
    factor_returns = {g: float(coef[i + 1]) for i, g in enumerate(group_names)}
    return factor_returns, r2, n_stocks


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def compute_factor_attribution(
    actual_weights: pd.DataFrame,
    backtest_start: pd.Timestamp,
    backtest_end: pd.Timestamp,
    factor_group_map: dict[str, str] | None = None,
    factor_directions: dict[str, int] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    因子收益归因：将每月超额收益拆解为各因子组的贡献与残差。

    时间对齐假设：
      - 因子值、基准权重：T 日月末（收盘前已知）
      - 策略权重：actual_weights 中 T 之后第一个可用日期（T+1 执行后），不归一化（F8-002）
      - 期间收益：T+1 开盘买入至 T_next 收盘（F8-001），与 brinson 模块一致

    默认行为（F8-004, F8-005）：
      - factor_group_map=None 时从 final_factors.json 加载最终入模因子并映射到组
      - factor_directions=None 时从 factor_summary.csv 读取因子方向

    Args:
        actual_weights:   BacktestResult.actual_weights（date × ts_code 日频权重面板）
        backtest_start:   回测起始日（含）
        backtest_end:     回测结束日（含）
        factor_group_map: 因子名 → 组名映射；None 时从 final_factors.json 自动加载
        factor_directions: 因子名 → 方向；None 时从 factor_summary.csv 自动加载
    Returns:
        period_factor_attr: 每期每因子组的详细归因
            index:   (period_start, group_name) MultiIndex
            columns: factor_return（β_k）, portfolio_exposure（E_p_k）,
                     benchmark_exposure（E_b_k）, exposure_diff（ΔE_k）,
                     contribution（β_k × ΔE_k）
        period_summary: 每期汇总
            index:   period_start
            columns: period_end, strategy_return, benchmark_return, total_excess,
                     cash_weight, weight_sum,
                     [value, quality, growth, momentum, volatility, liquidity, fund_flow],
                     residual, regression_r2, n_stocks,
                     factor_list_source
    Raises:
        ValueError: 区间内无有效期间，或所有期间均归因失败
    """
    # F8-004: 默认加载最终入模因子
    if factor_group_map is None:
        factor_group_map = _load_final_factor_group_map()
        factor_list_source = "final_factors.json"
    else:
        factor_list_source = "caller_provided"

    # F8-005: 默认从 factor_summary.csv 读取因子方向
    if factor_directions is None:
        factor_directions = _load_factor_directions_from_summary()

    all_rebalance_dates = get_rebalance_dates()
    periods = _get_period_list(all_rebalance_dates, backtest_start, backtest_end)

    if not periods:
        raise ValueError(
            f"[{backtest_start.date()}, {backtest_end.date()}] 内月度期间为 0，"
            "请检查 backtest_start / backtest_end 是否在 index_member 范围内"
        )

    log.info(
        "因子归因：%d 个月度期间，回测区间 [%s, %s]，因子来源=%s，因子数=%d",
        len(periods), backtest_start.date(), backtest_end.date(),
        factor_list_source, len(factor_group_map),
    )

    # ── 一次性批量加载因子面板 ─────────────────────────────────────────────────
    factor_names = sorted(set(factor_group_map.keys()))
    factor_panels = _load_available_factor_panels(factor_names)

    if not factor_panels:
        raise ValueError(
            "未找到任何因子面板文件，请检查 data/processed/factor_panels/ 目录"
        )

    # ── 确定需要加载日价格的全量股票代码 ──────────────────────────────────────
    all_codes: set[str] = set()
    for panel in factor_panels.values():
        all_codes.update(str(c) for c in panel.columns)
    all_codes.update(
        c for c in actual_weights.columns
        if actual_weights[c].notna().any()
    )

    # ── F8-001: 加载后复权收盘价和开盘价（替代 ret 列）─────────────────────────
    log.info("加载后复权价格数据（%d 只股票）...", len(all_codes))
    dq = load_daily_quote(backtest_start, backtest_end, sorted(all_codes))
    close_adj_panel: pd.DataFrame = dq["close_adj"].unstack("ts_code")
    open_adj_panel:  pd.DataFrame = dq["open_adj"].unstack("ts_code")

    # ── 逐期归因 ──────────────────────────────────────────────────────────────
    factor_attr_rows: list[pd.DataFrame] = []
    summary_rows: list[dict]             = []

    for T, T_next in periods:

        # 1. 策略权重（T+1 执行后，原始权重不归一化）
        w_p = _get_period_start_weights(actual_weights, T)
        if w_p.empty:
            continue
        w_p_sum     = float(w_p.sum())
        cash_weight = 1.0 - w_p_sum

        # 2. 基准权重（T 日）
        try:
            w_b = _get_benchmark_weights(T)
        except (KeyError, ValueError) as exc:
            log.warning("T=%s 基准权重加载失败，跳过: %s", T.date(), exc)
            continue

        # 3. 因子组合成（T 日横截面，方向来自 factor_summary.csv）
        group_composites = _build_group_composites(
            factor_panels, T, factor_group_map, factor_directions
        )
        if not group_composites:
            log.warning("T=%s 无有效因子数据，跳过", T.date())
            continue

        # 4. 期间收益（T+1 开盘买入口径，F8-001）
        universe_codes = sorted(set(w_p.index) | set(w_b.index))
        stock_returns, n_missing = _compute_period_returns_t1_open(
            T, T_next, universe_codes, close_adj_panel, open_adj_panel
        )
        if n_missing > 0:
            log.debug("T=%s 有 %d 只股票缺少价格数据（收益填 0）", T.date(), n_missing)

        # 5. 实际策略和基准收益（与 brinson.py 保持一致）
        wp_full          = w_p.reindex(universe_codes).fillna(0.0)
        wb_full          = w_b.reindex(universe_codes).fillna(0.0)
        strategy_return  = float((wp_full * stock_returns).sum())
        benchmark_return = float((wb_full * stock_returns).sum())
        actual_excess    = strategy_return - benchmark_return

        # 6. WLS 回归估计因子组收益（回归权重 = w_b，已归一化）
        factor_returns, r2, n_stocks = _run_wls_regression(
            group_composites,
            stock_returns,
            wb_full,
        )

        # 7. 计算每因子组的暴露差和贡献
        period_rows_list: list[dict] = []
        total_factor_contribution = 0.0

        for group in sorted(group_composites.keys()):
            g_vals = group_composites[group]

            E_p = float((w_p.reindex(g_vals.index).fillna(0.0) * g_vals.fillna(0.0)).sum())
            E_b = float((w_b.reindex(g_vals.index).fillna(0.0) * g_vals.fillna(0.0)).sum())

            beta         = factor_returns.get(group, np.nan)
            contribution = float(beta * (E_p - E_b)) if pd.notna(beta) else np.nan

            if pd.notna(contribution):
                total_factor_contribution += contribution

            period_rows_list.append({
                "period_start":        T,
                "group_name":          group,
                "factor_return":       beta,
                "portfolio_exposure":  E_p,
                "benchmark_exposure":  E_b,
                "exposure_diff":       E_p - E_b,
                "contribution":        contribution,
            })

        # 8. 收集本期行级数据
        period_df = (
            pd.DataFrame(period_rows_list)
            .set_index(["period_start", "group_name"])
        )
        factor_attr_rows.append(period_df)

        # 9. 期间汇总行
        residual    = actual_excess - total_factor_contribution
        summary_row: dict = {
            "period_start":        T,
            "period_end":          T_next,
            "strategy_return":     strategy_return,
            "benchmark_return":    benchmark_return,
            "total_excess":        actual_excess,
            "cash_weight":         cash_weight,       # F8-002
            "weight_sum":          w_p_sum,           # F8-002
            "residual":            residual,
            "regression_r2":       r2,
            "n_stocks":            n_stocks,
            "factor_list_source":  factor_list_source,
        }
        # 将各组贡献平铺到 summary 行，方便后续按列绘图
        contrib_map = {
            row["group_name"]: row["contribution"]
            for row in period_rows_list
        }
        for g in GROUP_ORDER:
            summary_row[g] = contrib_map.get(g, np.nan)

        summary_rows.append(summary_row)

    if not factor_attr_rows:
        raise ValueError("所有期间均归因失败，请检查数据完整性")

    # ── 合并输出 ──────────────────────────────────────────────────────────────
    period_factor_attr = pd.concat(factor_attr_rows)
    period_factor_attr.index.names = ["period_start", "group_name"]

    period_summary = pd.DataFrame(summary_rows).set_index("period_start")

    # 列顺序规范化
    base_cols   = ["period_end", "strategy_return", "benchmark_return", "total_excess",
                   "cash_weight", "weight_sum"]
    group_cols  = [g for g in GROUP_ORDER if g in period_summary.columns]
    tail_cols   = ["residual", "regression_r2", "n_stocks", "factor_list_source"]
    period_summary = period_summary[base_cols + group_cols + tail_cols]

    # 会计恒等式校验：Σ_k contribution_k + residual = total_excess（由构造保证，永远成立）
    check = (
        period_summary[group_cols].sum(axis=1)
        + period_summary["residual"]
        - period_summary["total_excess"]
    ).abs().max()
    if check > 1e-8:
        log.warning("因子归因会计恒等式校验失败：最大偏差 = %.2e（应为 0）", check)
    else:
        log.info("因子归因会计恒等式校验通过（偏差 = %.2e）", check)

    log.info(
        "因子归因完成：%d 期，因子来源=%s，因子组归因行数 %d，平均现金仓位=%.2f%%",
        len(period_summary), factor_list_source, len(period_factor_attr),
        period_summary["cash_weight"].mean() * 100,
    )
    return period_factor_attr, period_summary


def summarize_factor_attr_by_segment(
    period_summary: pd.DataFrame,
    segments: Optional[list[tuple[pd.Timestamp, pd.Timestamp]]] = None,
) -> pd.DataFrame:
    """
    按时间段汇总因子归因结果（各效应简单加总）。

    Args:
        period_summary: compute_factor_attribution 的第二个返回值
        segments:       [(start, end), ...] 自定义时间段；
                        None 时按年度（period_start 年份）自动分组
    Returns:
        每段汇总 DataFrame，columns 同 period_summary（去掉 period_end、n_stocks、r2），
        新增 n_periods（有效期数）和 win_rate（超额 > 0 的月份比例）
    """
    effect_cols = (
        ["strategy_return", "benchmark_return", "total_excess"]
        + [g for g in GROUP_ORDER if g in period_summary.columns]
        + ["residual"]
    )

    def _aggregate(grp: pd.DataFrame, label: str) -> dict:
        row            = grp[effect_cols].sum().to_dict()
        row["segment"] = label
        row["n_periods"] = len(grp)
        row["win_rate"]  = float((grp["total_excess"] > 0).mean())
        row["avg_r2"]    = float(grp["regression_r2"].mean())
        return row

    if segments is None:
        rows = [
            _aggregate(grp, str(year))
            for year, grp in period_summary.groupby(period_summary.index.year)
        ]
    else:
        rows = []
        for start, end in segments:
            mask = (period_summary.index >= start) & (period_summary.index <= end)
            grp  = period_summary.loc[mask]
            if grp.empty:
                continue
            rows.append(_aggregate(grp, f"{start.date()}~{end.date()}"))

    if not rows:
        return pd.DataFrame(
            columns=effect_cols + ["segment", "n_periods", "win_rate", "avg_r2"]
        )
    return pd.DataFrame(rows).set_index("segment")
