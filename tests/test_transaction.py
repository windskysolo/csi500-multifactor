"""
tests/test_transaction.py — 交易成本计算测试

验证规则：
  - 印花税在 2023-08-28 前为 10 bps（卖出单边），之后为 5 bps
  - 佣金双边各 2.5 bps
  - 滑点双边各 8 bps
  - 总成本 = 印花税 + 佣金 + 滑点

不依赖行情数据，纯函数测试。
"""

import pytest
import pandas as pd

from src import config as cfg
from src.backtest.transaction import stamp_duty_rate, compute_trade_cost


# 印花税切换日（取自 cfg，不硬编码）
CUT_DATE = cfg.STAMP_DUTY_CUT_DATE


class TestStampDutyRate:
    """印花税率切换测试。"""

    def test_before_cut_date_is_10bps(self):
        """2023-08-28 之前印花税为 10 bps。"""
        date = CUT_DATE - pd.Timedelta(days=1)
        assert stamp_duty_rate(date) == pytest.approx(0.001), "切换日前应为 10 bps"

    def test_on_cut_date_is_5bps(self):
        """2023-08-28 当日起印花税降为 5 bps。"""
        assert stamp_duty_rate(CUT_DATE) == pytest.approx(0.0005), "切换日当天应为 5 bps"

    def test_after_cut_date_is_5bps(self):
        """2023-08-28 之后印花税为 5 bps。"""
        date = CUT_DATE + pd.Timedelta(days=30)
        assert stamp_duty_rate(date) == pytest.approx(0.0005), "切换日后应为 5 bps"

    def test_far_before_cut_date(self):
        """2016 年的印花税也应为 10 bps。"""
        date = pd.Timestamp("2016-01-01")
        assert stamp_duty_rate(date) == pytest.approx(0.001)

    def test_far_after_cut_date(self):
        """2025 年的印花税应为 5 bps。"""
        date = pd.Timestamp("2025-12-31")
        assert stamp_duty_rate(date) == pytest.approx(0.0005)


class TestComputeTradeCost:
    """总交易成本计算测试。"""

    def _expected_cost(
        self,
        sell_value: float,
        buy_value: float,
        trade_date: pd.Timestamp,
    ) -> float:
        """手动计算预期成本，独立于被测函数。"""
        stamp = sell_value * stamp_duty_rate(trade_date)
        commission = (sell_value + buy_value) * cfg.COMMISSION_RATE
        slippage = (sell_value + buy_value) * cfg.SLIPPAGE_RATE
        return stamp + commission + slippage

    def test_cost_before_cut_date(self):
        """切换日前成本计算正确。"""
        trade_date = CUT_DATE - pd.Timedelta(days=1)
        sell, buy = 1_000_000.0, 1_000_000.0
        expected = self._expected_cost(sell, buy, trade_date)
        actual = compute_trade_cost(sell, buy, trade_date)
        assert actual == pytest.approx(expected, rel=1e-9)

    def test_cost_after_cut_date(self):
        """切换日后成本计算正确（印花税降低，总成本应减少）。"""
        trade_date_before = CUT_DATE - pd.Timedelta(days=1)
        trade_date_after  = CUT_DATE

        sell, buy = 1_000_000.0, 1_000_000.0
        cost_before = compute_trade_cost(sell, buy, trade_date_before)
        cost_after  = compute_trade_cost(sell, buy, trade_date_after)
        assert cost_after < cost_before, "印花税降低后总成本应减少"
        # 差值应等于印花税差（0.001 - 0.0005）× sell_value = 500 元
        assert abs((cost_before - cost_after) - sell * 0.0005) < 1e-6

    def test_stamp_duty_only_on_sell(self):
        """印花税只收卖出方，买入不计。"""
        date = pd.Timestamp("2025-01-01")
        # 情形 1：只卖不买
        cost_sell_only = compute_trade_cost(1_000_000.0, 0.0, date)
        # 情形 2：只买不卖
        cost_buy_only  = compute_trade_cost(0.0, 1_000_000.0, date)

        # 卖出场景应有印花税，买入场景没有
        stamp = 1_000_000.0 * stamp_duty_rate(date)
        commission = 1_000_000.0 * cfg.COMMISSION_RATE
        slippage   = 1_000_000.0 * cfg.SLIPPAGE_RATE

        assert cost_sell_only == pytest.approx(stamp + commission + slippage, rel=1e-9)
        assert cost_buy_only  == pytest.approx(commission + slippage, rel=1e-9)

    def test_zero_trade_has_zero_cost(self):
        """零成交量时成本为 0。"""
        date = pd.Timestamp("2021-06-30")
        assert compute_trade_cost(0.0, 0.0, date) == pytest.approx(0.0)

    def test_negative_sell_raises(self):
        """sell_value 为负数时应抛出 AssertionError。"""
        date = pd.Timestamp("2021-06-30")
        with pytest.raises(AssertionError):
            compute_trade_cost(-1.0, 100.0, date)

    def test_negative_buy_raises(self):
        """buy_value 为负数时应抛出 AssertionError。"""
        date = pd.Timestamp("2021-06-30")
        with pytest.raises(AssertionError):
            compute_trade_cost(100.0, -1.0, date)

    def test_commission_is_bilateral(self):
        """佣金双边收取（买卖各 2.5 bps），验证数值。"""
        date = pd.Timestamp("2020-01-01")
        sell, buy = 2_000_000.0, 3_000_000.0
        cost = compute_trade_cost(sell, buy, date)

        stamp      = sell * 0.001
        commission = (sell + buy) * cfg.COMMISSION_RATE
        slippage   = (sell + buy) * cfg.SLIPPAGE_RATE
        assert cost == pytest.approx(stamp + commission + slippage, rel=1e-9)
