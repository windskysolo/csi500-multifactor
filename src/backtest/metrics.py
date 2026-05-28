"""
src/backtest/metrics.py — 回测绩效指标计算
==========================================

所有函数仅接受 pd.Series（index=trade_date，values=float）。
nav 和 bench_nav 均假设起点归一化为 1.0（由 engine 保证）。
max_drawdown / excess_max_drawdown 返回负数（-0.15 = 最大回撤 15%），展示时取绝对值。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def daily_returns(nav: pd.Series) -> pd.Series:
    """
    计算日收益率序列（第一个值为 NaN，下游自行 dropna 或依赖 skipna=True）。

    Args:
        nav: 日频 NAV 序列
    Returns:
        日收益率序列，与 nav 等长，首元素为 NaN
    """
    return nav.pct_change()


def annualized_return(nav: pd.Series) -> float:
    """
    年化收益率（几何复利），从累计净值反推，不用算术均值近似。

    Args:
        nav: 日频 NAV 序列
    Returns:
        年化收益率（小数，如 0.15 = 15%）
    """
    n_years = len(nav) / TRADING_DAYS_PER_YEAR
    return float((nav.iloc[-1] / nav.iloc[0]) ** (1 / n_years) - 1)


def annualized_vol(nav: pd.Series) -> float:
    """
    年化波动率（日收益率标准差 × √252）。

    Args:
        nav: 日频 NAV 序列
    Returns:
        年化波动率（正数）
    """
    return float(daily_returns(nav).std() * np.sqrt(TRADING_DAYS_PER_YEAR))


def sharpe(nav: pd.Series, rf_annual: float = 0.02) -> float:
    """
    夏普比率（年化超额收益 / 年化波动）。

    Args:
        nav: 日频 NAV 序列
        rf_annual: 年化无风险利率（默认 2%）
    Returns:
        夏普比率；当日波动率为 0 时返回 0.0
    """
    daily_rf = rf_annual / TRADING_DAYS_PER_YEAR
    excess_daily = daily_returns(nav).dropna() - daily_rf
    std = excess_daily.std()
    if std < 1e-10:
        return 0.0
    return float(excess_daily.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))


def max_drawdown(nav: pd.Series) -> float:
    """
    最大回撤（负数，-0.15 = 最大回撤 15%）。

    Args:
        nav: 日频 NAV 序列
    Returns:
        最大回撤，非正数；展示时取绝对值
    """
    rolling_max = nav.cummax()
    drawdown = (nav - rolling_max) / rolling_max
    return float(drawdown.min())


def tracking_error(nav: pd.Series, bench_nav: pd.Series) -> float:
    """
    跟踪误差（超额日收益的年化标准差，事后实测 TE）。

    Args:
        nav: 策略日频 NAV
        bench_nav: 基准日频 NAV（与 nav 做 inner 对齐）
    Returns:
        年化跟踪误差（非负数）
    """
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    excess = daily_returns(nav_aligned) - daily_returns(bench_aligned)
    return float(excess.std() * np.sqrt(TRADING_DAYS_PER_YEAR))


def information_ratio(nav: pd.Series, bench_nav: pd.Series) -> float:
    """
    信息比率（年化超额收益 / 跟踪误差）。

    Args:
        nav: 策略日频 NAV
        bench_nav: 基准日频 NAV
    Returns:
        信息比率；跟踪误差为 0 时返回 0.0
    """
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    excess_ann = annualized_return(nav_aligned) - annualized_return(bench_aligned)
    te = tracking_error(nav_aligned, bench_aligned)
    if te < 1e-10:
        return 0.0
    return float(excess_ann / te)


def excess_nav(nav: pd.Series, bench_nav: pd.Series) -> pd.Series:
    """
    超额净值曲线（nav / bench_nav，inner 对齐后相除）。

    前提：nav 和 bench_nav 均已归一化到起点 = 1.0（由 engine 保证）。
    结果起点也为 1.0。

    Args:
        nav: 策略日频 NAV
        bench_nav: 基准日频 NAV
    Returns:
        超额净值序列，起点 = 1.0
    """
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    return nav_aligned / bench_aligned


def excess_max_drawdown(nav: pd.Series, bench_nav: pd.Series) -> float:
    """
    超额净值的最大回撤（负数）。

    Args:
        nav: 策略日频 NAV
        bench_nav: 基准日频 NAV
    Returns:
        超额净值最大回撤，非正数
    """
    return max_drawdown(excess_nav(nav, bench_nav))


def monthly_win_rate(nav: pd.Series, bench_nav: pd.Series) -> float:
    """
    月胜率（策略月收益高于基准的月份比例）。

    Args:
        nav: 策略日频 NAV
        bench_nav: 基准日频 NAV
    Returns:
        月胜率，[0.0, 1.0]
    """
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    monthly_ret = nav_aligned.resample("ME").last().pct_change().dropna()
    monthly_bench_ret = bench_aligned.resample("ME").last().pct_change().dropna()
    # 防御：月度序列端点可能因 pct_change 产生轻微不对齐
    monthly_ret, monthly_bench_ret = monthly_ret.align(monthly_bench_ret, join="inner")
    excess = monthly_ret - monthly_bench_ret
    return float((excess > 0).mean())


def calmar_ratio(nav: pd.Series) -> float:
    """
    卡玛比率（年化收益 / |最大回撤|）。

    Args:
        nav: 日频 NAV 序列
    Returns:
        卡玛比率；最大回撤为 0 时返回 0.0
    """
    ann_ret = annualized_return(nav)
    mdd = max_drawdown(nav)
    if abs(mdd) < 1e-10:
        return 0.0
    return float(ann_ret / abs(mdd))


def summarize(nav: pd.Series, bench_nav: pd.Series, rf_annual: float = 0.02) -> dict:
    """
    汇总全部绩效指标，返回 dict。

    Args:
        nav: 策略日频 NAV（起点=1.0）
        bench_nav: 基准日频 NAV（起点=1.0，与 nav 对齐）
        rf_annual: 年化无风险利率
    Returns:
        dict：key 为指标名（英文），value 为 float。
        max_drawdown / excess_max_drawdown 为负数，展示时取绝对值。
    """
    nav_aligned, bench_aligned = nav.align(bench_nav, join="inner")
    return {
        "annualized_return":   annualized_return(nav_aligned),
        "annualized_vol":      annualized_vol(nav_aligned),
        "sharpe":              sharpe(nav_aligned, rf_annual),
        "max_drawdown":        max_drawdown(nav_aligned),
        "calmar_ratio":        calmar_ratio(nav_aligned),
        "benchmark_return":    annualized_return(bench_aligned),
        "excess_return":       annualized_return(nav_aligned) - annualized_return(bench_aligned),
        "tracking_error":      tracking_error(nav_aligned, bench_aligned),
        "information_ratio":   information_ratio(nav_aligned, bench_aligned),
        "excess_max_drawdown": excess_max_drawdown(nav_aligned, bench_aligned),
        "monthly_win_rate":    monthly_win_rate(nav_aligned, bench_aligned),
    }
