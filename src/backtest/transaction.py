"""
src/backtest/transaction.py — 交易成本计算
==========================================

职责：根据交易日期、卖出金额、买入金额计算三类交易成本之和：
  - 印花税（卖出单边，2023-08-28 前 10 bps，之后 5 bps）
  - 佣金（双边各 2.5 bps）
  - 滑点（双边各 8 bps）

不依赖任何行情数据，只读 src.config 常量。
"""

import pandas as pd

from src import config as cfg


def stamp_duty_rate(trade_date: pd.Timestamp) -> float:
    """
    返回 trade_date 对应的印花税率（小数形式）。

    Args:
        trade_date: 实际执行交易的日期（T+1 开盘日），不是调仓日 T
    Returns:
        0.001（10 bps）或 0.0005（5 bps）
    """
    if trade_date >= cfg.STAMP_DUTY_CUT_DATE:
        return cfg.STAMP_DUTY_AFTER
    return cfg.STAMP_DUTY_BEFORE


def compute_trade_cost(
    sell_value: float,
    buy_value: float,
    trade_date: pd.Timestamp,
) -> float:
    """
    计算单次调仓的总交易成本（印花税 + 佣金 + 滑点）。

    Args:
        sell_value: 本次卖出的总金额（非负，调用方保证）
        buy_value:  本次买入的总金额（非负，调用方保证）
        trade_date: 实际执行日（T+1 开盘日），用于确定印花税率
    Returns:
        总交易成本（与 portfolio_value 同单位），engine 负责从净值中扣除
    Raises:
        AssertionError: sell_value 或 buy_value 为负数
    """
    assert sell_value >= 0 and buy_value >= 0, (
        f"sell_value({sell_value}) 和 buy_value({buy_value}) 必须为非负数"
    )

    # 印花税：仅卖出单边收取
    stamp_duty = sell_value * stamp_duty_rate(trade_date)
    # 佣金和滑点：买卖双边均计
    commission = (sell_value + buy_value) * cfg.COMMISSION_RATE
    slippage = (sell_value + buy_value) * cfg.SLIPPAGE_RATE

    return stamp_duty + commission + slippage
