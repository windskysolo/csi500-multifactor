"""
src/backtest/engine.py — 回测主循环
=====================================

职责：接受目标权重 panel，按日执行调仓、计算成本、汇总 NAV，返回 BacktestResult。

时间对齐：
  T 日（月末）收盘后基于 ≤T 信息生成目标权重 → T+1 开盘价执行 →
  T+1 至次调仓日前按收盘价逐日估值。

缺失状态处理（F7-002，保守路径）：
  执行日 T+1 状态快照中完全缺失的股票 → 保守设为 LOCKED（状态未知不得交易）；
  整日状态数据缺失（load_stock_status 抛出 KeyError）→ 所有股票设为 LOCKED。
  任何 LOCKED 股票的现有持仓保持不变，目标权重被忽略，不计入 n_no_price 统计。

成本处理：
  调仓后按成本比例整体缩减所有头寸，使后续收盘估值自然反映净值扣除。

依赖：
  src.backtest.transaction  compute_trade_cost
  src.backtest.metrics      summarize
  src.data.loader           load_daily_quote / load_stock_status
  src.data.universe         TradeState
  src.config
"""

import bisect
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from src import config as cfg
from src.backtest.metrics import summarize
from src.backtest.transaction import compute_trade_cost
from src.data.loader import load_daily_quote, load_stock_status
from src.data.universe import TradeState

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    initial_value: float = 1.0    # 组合起始净值（规模单位）


@dataclass
class TradeRecord:
    exec_date:              pd.Timestamp
    rebalance_date:         pd.Timestamp
    sell_value:             float
    buy_value:              float
    cost:                   float
    portfolio_value_before: float
    portfolio_value_after:  float
    n_locked:               int    # 停牌锁定，无法成交
    n_no_buy:               int    # 涨停，无法加仓
    n_no_sell:              int    # 跌停，无法减仓
    n_no_price:             int    # 价格缺失（停牌开盘），无法成交


@dataclass
class BacktestResult:
    nav:             pd.Series      # 日频策略 NAV（起点=1.0）
    benchmark_nav:   pd.Series      # 基准 NAV（起点=1.0，同索引）
    excess_nav:      pd.Series      # nav / benchmark_nav
    actual_weights:  pd.DataFrame   # 每日实际权重（date × ts_code）
    trade_log:       pd.DataFrame   # 每次调仓记录
    metrics:         dict           # summarize() 输出


# ---------------------------------------------------------------------------
# 私有辅助函数
# ---------------------------------------------------------------------------

def _next_trading_day(
    date: pd.Timestamp,
    all_dates: list[pd.Timestamp],
) -> Optional[pd.Timestamp]:
    """
    二分查找 date 之后的第一个交易日。

    Args:
        date:      参考日期（通常为月末调仓日 T）
        all_dates: 已排序的交易日列表
    Returns:
        T+1 日期，若 date 已是最后一个交易日则返回 None
    """
    idx = bisect.bisect_right(all_dates, date)
    return all_dates[idx] if idx < len(all_dates) else None


def _build_execution_map(
    rebalance_dates:    list[pd.Timestamp],
    backtest_start:     pd.Timestamp,
    backtest_end:       pd.Timestamp,
    all_trading_dates:  list[pd.Timestamp],
) -> dict[pd.Timestamp, pd.Timestamp]:
    """
    构建调仓日到执行日的映射 {T: T+1}。

    过滤规则：T 在回测区间外，或 T+1 超过 backtest_end 的调仓日不纳入。
    特殊处理：回测起始日前的最后一个调仓日映射到回测第一个交易日，
    防止策略从空仓起步而基准已满仓的首月偏差。

    Args:
        rebalance_dates:   weights_panel 中的所有调仓日
        backtest_start:    回测起始日（含）
        backtest_end:      回测结束日（含）
        all_trading_dates: 回测区间内的所有交易日（已排序）
    Returns:
        {T: T+1} 字典
    """
    result: dict[pd.Timestamp, pd.Timestamp] = {}

    # 把回测前最后一次调仓的信号在回测第一天执行，消除首月空仓偏差
    pre_start = [T for T in rebalance_dates if T < backtest_start]
    if pre_start and all_trading_dates:
        T_last = max(pre_start)
        T1 = _next_trading_day(T_last, all_trading_dates)
        if T1 is not None and T1 <= backtest_end:
            result[T_last] = T1
            log.debug("包含回测前末次调仓 T=%s → 执行日=%s", T_last.date(), T1.date())

    for T in rebalance_dates:
        if T < backtest_start or T > backtest_end:
            continue
        T1 = _next_trading_day(T, all_trading_dates)
        if T1 is None or T1 > backtest_end:
            continue
        result[T] = T1
    log.debug("执行映射共 %d 期", len(result))
    return result


def _load_all_trading_dates(
    backtest_start: pd.Timestamp,
    backtest_end:   pd.Timestamp,
) -> list[pd.Timestamp]:
    """
    从 index_quote.parquet 获取回测区间内的所有交易日（已排序）。

    Args:
        backtest_start: 回测起始日（含）
        backtest_end:   回测结束日（含）
    Returns:
        已排序的 pd.Timestamp 列表
    """
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    tdates = iq.index.sort_values()
    return [d for d in tdates if backtest_start <= d <= backtest_end]


def _load_price_panels(
    all_codes:      list[str],
    backtest_start: pd.Timestamp,
    backtest_end:   pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    加载并整理 open_adj 和 close_adj 面板。

    - close_adj：停牌 NaN → ffill（保证估值连续）
    - open_adj：保持 NaN（NaN = 停牌未开盘，引擎视为无法成交）

    Args:
        all_codes:      weights_panel 中所有出现过的股票代码
        backtest_start: 回测起始日
        backtest_end:   回测结束日
    Returns:
        (open_adj_panel, close_adj_panel)，shape=(trading_days, n_codes)
    """
    dq = load_daily_quote(backtest_start, backtest_end, codes=all_codes)
    open_adj  = dq["open_adj"].unstack("ts_code").reindex(columns=all_codes)
    close_adj = dq["close_adj"].unstack("ts_code").reindex(columns=all_codes)
    close_adj = close_adj.ffill()   # 停牌日用上一收盘价估值，open_adj 不 ffill
    return open_adj, close_adj


def _status_to_trade_state(
    status_snap: pd.DataFrame,
    all_codes:   list[str],
) -> pd.Series:
    """
    将 load_stock_status 返回的快照转换为 TradeState Series。

    优先级（高覆盖低）：LOCKED > NO_BUY > NO_SELL > BUY_LIMIT > FREE。
    不在 status_snap 中的股票默认为 LOCKED（保守：状态未知不得交易）。

    Args:
        status_snap: load_stock_status 的返回值（ts_code 为 index）
        all_codes:   需要赋状态的所有股票代码
    Returns:
        pd.Series，index=all_codes，values=TradeState 字符串值
    """
    state = pd.Series(TradeState.FREE.value, index=all_codes, dtype=object)
    snap  = status_snap.reindex(all_codes)   # 对齐，缺失行填 NaN

    def _to_bool(col: str) -> "np.ndarray":
        # numpy path avoids pandas FutureWarning about object-dtype fillna auto-downcast
        vals = snap[col].to_numpy(dtype=object)
        na = pd.isna(vals)
        out = np.zeros(len(vals), dtype=bool)
        if not na.all():
            out[~na] = np.asarray(vals[~na], dtype=bool)
        return out

    buy_limit = _to_bool("is_st") | _to_bool("is_new_stock")
    state[buy_limit]                        = TradeState.BUY_LIMIT.value
    state[_to_bool("is_limit_down_locked")] = TradeState.NO_SELL.value
    state[_to_bool("is_limit_up_locked")]   = TradeState.NO_BUY.value
    state[_to_bool("is_suspended")]         = TradeState.LOCKED.value

    # F7-002: 完全不在状态快照中的股票 → LOCKED（保守：状态未知不得交易）
    missing_mask = snap.isna().all(axis=1)
    n_missing = int(missing_mask.sum())
    if n_missing > 0:
        log.debug("状态快照缺失 %d 只股票，保守设为 LOCKED", n_missing)
        state[missing_mask] = TradeState.LOCKED.value

    return state


def _load_exec_status(
    exec_dates: list[pd.Timestamp],
    all_codes:  list[str],
) -> dict[pd.Timestamp, pd.Series]:
    """
    预加载所有 T+1 执行日的 TradeState，避免主循环内重复 I/O。

    Args:
        exec_dates: 所有 T+1 执行日列表
        all_codes:  需要赋状态的所有股票代码
    Returns:
        {T+1: pd.Series(ts_code → TradeState 字符串)}
    """
    result: dict[pd.Timestamp, pd.Series] = {}
    for d in exec_dates:
        try:
            snap = load_stock_status(d)
            result[d] = _status_to_trade_state(snap, all_codes)
        except KeyError:
            # F7-002: 整日状态缺失 → 全部 LOCKED（无法确认可交易，不应放行）
            log.warning("load_stock_status(%s) 无数据，保守设所有股票为 LOCKED", d.date())
            result[d] = pd.Series(TradeState.LOCKED.value, index=all_codes, dtype=object)
    return result


def _load_benchmark_nav(
    backtest_start: pd.Timestamp,
    backtest_end:   pd.Timestamp,
) -> pd.Series:
    """
    加载基准净值曲线，归一化到 backtest_start = 1.0。

    优先使用 index_quote.parquet 中的 nav 列（全收益，已含分红再投资）；
    若不存在则降级到 close 列（价格指数，会低估真实超额收益约 2-3% p.a.）。

    Args:
        backtest_start: 回测起始日（含）
        backtest_end:   回测结束日（含）
    Returns:
        pd.Series，index=trade_date，起点=1.0
    Raises:
        KeyError:   index_quote.parquet 无 nav/close 列
        ValueError: 指定范围内无数据
    """
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    if "nav" in iq.columns:
        bench_raw = iq["nav"]
    elif "close" in iq.columns:
        raise ValueError(
            "index_quote.parquet 缺少 nav 列（全收益指数），不允许降级使用 close（价格指数）。"
            "项目硬规则：基准必须使用中证500全收益指数（含分红再投资），"
            f"用价格指数会虚增约 2-3% 超额收益。当前列：{iq.columns.tolist()}"
        )
    else:
        raise KeyError(
            f"index_quote.parquet 缺少 nav/close 列，实际列：{iq.columns.tolist()}"
        )

    bench_raw = bench_raw[
        (bench_raw.index >= backtest_start) & (bench_raw.index <= backtest_end)
    ]
    if bench_raw.empty:
        raise ValueError(
            f"基准数据在 [{backtest_start.date()}, {backtest_end.date()}] 范围内为空"
        )
    return bench_raw / bench_raw.iloc[0]


def _execute_rebalance(
    shares:          dict[str, float],
    cash:            float,
    target_w:        pd.Series,
    open_prices:     pd.Series,
    status:          pd.Series,
    portfolio_value: float,
    exec_date:       pd.Timestamp,
    rebalance_date:  pd.Timestamp,
) -> tuple[dict[str, float], float, TradeRecord]:
    """
    在 T+1 开盘价按约束状态执行单次调仓。

    两阶段执行保证资金守恒：
      Phase 1：处理所有卖出（含 NO_BUY/BUY_LIMIT 被动减仓），卖出金额归入现金。
      Phase 2：用可用现金（原有现金 + 卖出所得）按比例买入；资金不足时等比缩减，不允许隐含融资。
    成本直接从现金扣除。

    约束逻辑：
      LOCKED    → 保留原有头寸，不参与调仓
      NO_BUY    → 只能减仓到目标，不能加仓（涨停）
      NO_SELL   → 只能加仓到目标，不能减仓（跌停）
      BUY_LIMIT → 只能减仓，不能加仓（ST / 次新股，不依赖优化器预过滤）
      FREE      → 直接调至目标权重

    Args:
        shares:          当前持仓股数 {ts_code: shares}
        cash:            当前现金（non-negative）
        target_w:        目标权重（调用前已归一化，和=1）
        open_prices:     T+1 开盘价（NaN = 停牌未开盘）
        status:          T+1 约束状态（TradeState 字符串值）
        portfolio_value: 执行前净值（按 T+1 开盘估算，用于计算目标金额；由调用方在开盘前计算）
        exec_date:       实际执行日（T+1）
        rebalance_date:  调仓日（T）
    Returns:
        (new_shares, cash_after, TradeRecord)；new_shares 是 shares 的新副本，不修改原始
    """
    new_shares = dict(shares)
    n_locked = n_no_buy = n_no_sell = n_no_price = 0
    sell_value = 0.0

    # {code: (actual_target_value, price, current_value)}
    buy_orders: dict[str, tuple[float, float, float]] = {}

    # F7-003: 只处理当前持仓或目标权重为正的股票，避免 n_no_price 被历史全量股票污染
    all_codes = set(shares.keys()) | {
        c for c in target_w.index if float(target_w.get(c, 0.0)) > 1e-10
    }

    # ── Phase 1：确定每只股票的期望仓位，卖出类立即执行 ────────────────────────
    for code in all_codes:
        raw_price = open_prices.get(code)
        price = float(raw_price) if raw_price is not None else float("nan")

        if not (price > 0):             # NaN 或 ≤0 → 停牌未开盘
            n_no_price += 1
            continue

        current_shares = shares.get(code, 0.0)
        current_value  = current_shares * price
        target_value   = float(target_w.get(code, 0.0)) * portfolio_value
        state          = status.get(code, TradeState.FREE)

        if state == TradeState.LOCKED:
            n_locked += 1
            continue

        if state == TradeState.NO_BUY:
            if target_value < current_value:
                actual_value = target_value     # 允许减仓
            else:
                n_no_buy += 1
                continue                        # 想加仓但涨停，保持原有
        elif state == TradeState.NO_SELL:
            if target_value > current_value:
                actual_value = target_value     # 允许加仓
            else:
                n_no_sell += 1
                continue                        # 想减仓但跌停，保持原有
        elif state == TradeState.BUY_LIMIT:     # ST / 次新股
            if target_value < current_value:
                actual_value = target_value     # 允许减仓
            else:
                n_no_buy += 1
                continue                        # 禁止加仓，保持原有
        else:                                   # FREE
            actual_value = max(target_value, 0.0)

        delta = actual_value - current_value
        if delta < -1e-6:                       # 卖出：直接执行
            sell_value += abs(delta)
            new_shares[code] = actual_value / price
        elif delta > 1e-6:                      # 买入：推迟到 Phase 2
            buy_orders[code] = (actual_value, price, current_value)
        # delta ≈ 0：无需操作

    # ── Phase 2：用可用现金按比例买入，防止融资 ─────────────────────────────────
    available_cash  = cash + sell_value
    total_buy_need  = sum(av - cv for av, _p, cv in buy_orders.values())
    buy_value       = 0.0

    if total_buy_need <= available_cash + 1e-6:
        # 资金充足，按目标全额买入
        for code, (actual_value, price, current_value) in buy_orders.items():
            new_shares[code] = actual_value / price
            buy_value += actual_value - current_value
    elif total_buy_need > 1e-10 and available_cash > 1e-10:
        # 资金不足，等比缩减（保持相对权重不变）
        buy_scale = available_cash / total_buy_need
        for code, (actual_value, price, current_value) in buy_orders.items():
            actual_buy = (actual_value - current_value) * buy_scale
            new_shares[code] = (current_value + actual_buy) / price
            buy_value += actual_buy
    # else: 无可用资金，buy_orders 中的股票维持原有持仓（new_shares 已有旧值）

    # ── 扣除成本，清理零仓位 ──────────────────────────────────────────────────
    cost = compute_trade_cost(sell_value, buy_value, exec_date)
    # 满仓时 available_cash - buy_value ≈ 0，直接截断会将 cost 静默吸收为 0。
    # 改为等比缩减全部头寸（含现金），保证：
    #   cash_after + Σ new_shares[c]*open_price[c] = portfolio_value - cost
    raw_cash = available_cash - buy_value
    scale = max(0.0, 1.0 - cost / portfolio_value) if portfolio_value > 1e-10 else 1.0
    new_shares = {c: s * scale for c, s in new_shares.items() if s * scale > 1e-10}
    cash_after = max(0.0, raw_cash) * scale

    record = TradeRecord(
        exec_date              = exec_date,
        rebalance_date         = rebalance_date,
        sell_value             = sell_value,
        buy_value              = buy_value,
        cost                   = cost,
        portfolio_value_before = portfolio_value,
        portfolio_value_after  = portfolio_value - cost,
        n_locked               = n_locked,
        n_no_buy               = n_no_buy,
        n_no_sell              = n_no_sell,
        n_no_price             = n_no_price,
    )
    return new_shares, cash_after, record


def _run_main_loop(
    weights_panel:   pd.DataFrame,
    execution_map:   dict[pd.Timestamp, pd.Timestamp],
    open_adj_panel:  pd.DataFrame,
    close_adj_panel: pd.DataFrame,
    exec_status:     dict[pd.Timestamp, pd.Series],
    trading_dates:   list[pd.Timestamp],
    config:          BacktestConfig,
) -> tuple[dict, list, list]:
    """
    主循环：按交易日迭代，执行调仓 + 每日估值。

    状态：cash（现金）+ shares（股票持仓）。
    每日 portfolio_value = cash + Σ shares × close_adj。
    调仓当天的 cash 由 _execute_rebalance 更新（卖出 → 加现金，买入 → 减现金，成本 → 减现金）。

    Returns:
        (nav_dict, weight_rows, trade_records)
    """
    shares:          dict[str, float]        = {}
    cash:            float                   = config.initial_value   # 初始全仓现金
    portfolio_value: float                   = config.initial_value
    nav_dict:        dict[pd.Timestamp, float] = {}
    weight_rows:     list[dict]              = []
    trade_records:   list[TradeRecord]       = []

    exec_date_to_T = {v: k for k, v in execution_map.items()}

    for date in trading_dates:

        # ── 调仓执行（T+1 触发）──────────────────────────────────────────────
        if date in exec_date_to_T:
            T        = exec_date_to_T[date]
            target_w = weights_panel.loc[T].copy()

            # 权重归一化（防止优化器数值误差）
            w_sum = target_w.sum()
            if abs(w_sum - 1.0) > 1e-4:
                log.warning("T=%s 权重和 %.6f ≠ 1，强制归一化", T.date(), w_sum)
                target_w = target_w / w_sum

            open_prices = (
                open_adj_panel.loc[date]
                if date in open_adj_panel.index
                else pd.Series(dtype=float)
            )
            status = exec_status.get(date, pd.Series(dtype=object))

            # F7-001: 用 T+1 开盘价计算执行前净值，避免隔夜价格变化产生虚假交易
            # 停牌无开盘价的持仓以最近收盘价（已 ffill）估值
            if shares:
                s_series = pd.Series(shares)
                op = open_prices.reindex(s_series.index)
                cl = (
                    close_adj_panel.loc[date].reindex(s_series.index)
                    if date in close_adj_panel.index
                    else pd.Series(0.0, index=s_series.index)
                )
                prices_pretrade = op.where(op > 0, cl).fillna(0.0)
                pretrade_value = cash + float((s_series * prices_pretrade).sum())
            else:
                pretrade_value = cash

            new_shares, new_cash, record = _execute_rebalance(
                shares, cash, target_w, open_prices, status,
                pretrade_value, date, T,
            )
            shares = new_shares
            cash   = new_cash
            trade_records.append(record)

        # ── 每日收盘估值 ─────────────────────────────────────────────────────
        if date not in close_adj_panel.index:
            nav_dict[date] = portfolio_value / config.initial_value
            continue

        close_prices = close_adj_panel.loc[date]
        stock_value  = (
            sum(
                shares.get(code, 0.0) * float(close_prices.get(code, 0.0) or 0.0)
                for code in shares
            )
            if shares else 0.0
        )
        portfolio_value = cash + stock_value
        nav_dict[date]  = portfolio_value / config.initial_value

        # ── 记录每日实际权重（股票部分占总净值的比例）──────────────────────────
        if shares and portfolio_value > 1e-10:
            weight_rows.append({
                "date": date,
                **{
                    code: shares[code] * float(close_prices.get(code, 0.0) or 0.0) / portfolio_value
                    for code in shares
                    if shares[code] > 1e-10
                },
            })

    return nav_dict, weight_rows, trade_records


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def run_backtest(
    weights_panel:  pd.DataFrame,
    backtest_start: pd.Timestamp,
    backtest_end:   pd.Timestamp,
    config:         BacktestConfig = BacktestConfig(),
) -> BacktestResult:
    """
    运行回测，返回 BacktestResult。

    时间对齐假设：
      weights_panel.index 为月末调仓日（与 index_member 一致）；
      backtest_start / backtest_end 为实际交易日；
      执行价格为 T+1 开盘价（open_adj）。

    Args:
        weights_panel:  (rebalance_date × ts_code) 目标权重，行和=1
        backtest_start: 回测起始日（含，须为实际交易日）
        backtest_end:   回测结束日（含，须为实际交易日）
        config:         BacktestConfig（default: initial_value=1.0）
    Returns:
        BacktestResult
    Raises:
        ValueError: 价格数据或基准数据缺失
    """
    log.info("回测区间 [%s, %s]", backtest_start.date(), backtest_end.date())

    # 阶段 0：构建执行映射
    trading_dates   = _load_all_trading_dates(backtest_start, backtest_end)
    rebalance_dates = sorted(weights_panel.index.tolist())
    execution_map   = _build_execution_map(
        rebalance_dates, backtest_start, backtest_end, trading_dates
    )
    log.info("执行映射：%d 个调仓日 → %d 个执行日", len(rebalance_dates), len(execution_map))

    # 阶段 1：加载价格面板
    all_codes = weights_panel.columns.tolist()
    open_adj_panel, close_adj_panel = _load_price_panels(
        all_codes, backtest_start, backtest_end
    )

    # 阶段 2：预加载执行日状态
    exec_dates  = sorted(set(execution_map.values()))
    exec_status = _load_exec_status(exec_dates, all_codes)

    # 阶段 3：主循环
    nav_dict, weight_rows, trade_records = _run_main_loop(
        weights_panel, execution_map,
        open_adj_panel, close_adj_panel,
        exec_status, trading_dates, config,
    )

    # 阶段 4：汇总结果
    nav_series    = pd.Series(nav_dict, name="strategy")
    benchmark_nav = _load_benchmark_nav(backtest_start, backtest_end)

    # E13：inner 对齐，防止日期不一致导致 NaN 传播
    nav_aligned, bench_aligned = nav_series.align(benchmark_nav, join="inner")

    metrics_dict = summarize(nav_aligned, bench_aligned)
    log.info(
        "回测完成 | 年化超额 %.2f%% | IR %.3f | 超额最大回撤 %.2f%%",
        metrics_dict["excess_return"]      * 100,
        metrics_dict["information_ratio"],
        metrics_dict["excess_max_drawdown"] * 100,
    )

    actual_weights_df = (
        pd.DataFrame(weight_rows).set_index("date").fillna(0.0)
        if weight_rows
        else pd.DataFrame()
    )
    trade_log_df = (
        pd.DataFrame([vars(r) for r in trade_records])
        if trade_records
        else pd.DataFrame()
    )

    return BacktestResult(
        nav            = nav_aligned,
        benchmark_nav  = bench_aligned,
        excess_nav     = nav_aligned / bench_aligned,
        actual_weights = actual_weights_df,
        trade_log      = trade_log_df,
        metrics        = metrics_dict,
    )
