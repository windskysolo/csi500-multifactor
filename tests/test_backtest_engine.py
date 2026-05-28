"""
tests/test_backtest_engine.py — 回测引擎单元测试

覆盖：
  F7-001  以 T+1 开盘前净值（而非上一日收盘）计算目标金额
  F7-002  执行日状态缺失或股票不在快照中 → LOCKED（非 FREE）
  F7-003  n_no_price 只统计持仓或目标权重非零的活跃股票
  其他    涨跌停约束、停牌 LOCKED、BUY_LIMIT 约束
"""

import pytest
import pandas as pd

from src.backtest.engine import (
    _execute_rebalance,
    _status_to_trade_state,
    _load_exec_status,
)
from src.data.universe import TradeState


# ─── 公共工具 ────────────────────────────────────────────────────────────────

EXEC_DATE = pd.Timestamp("2023-01-03")
REBAL_DATE = pd.Timestamp("2022-12-30")


def _make_status_snap(*codes: str, **overrides) -> pd.DataFrame:
    """
    构造只读状态快照 DataFrame。
    默认所有 bool 列为 False。overrides 形如 is_suspended=["A"]。
    """
    bool_cols = [
        "is_st", "is_new_stock", "is_suspended",
        "is_limit_locked", "is_limit_up_locked", "is_limit_down_locked",
    ]
    data = {c: [False] * len(codes) for c in bool_cols}
    df = pd.DataFrame(data, index=list(codes))
    for col, affected in overrides.items():
        df.loc[affected, col] = True
    return df


# ─── F7-001：开盘前净值作为目标基准 ─────────────────────────────────────────

class TestPretradePorfolioValue:
    """_execute_rebalance 应以调用方传入的开盘前净值计算目标金额。"""

    def test_no_trade_when_weights_unchanged_and_price_doubles(self):
        """
        场景：持有 1 股 A（开盘价 200），目标仍 100% in A。
        若以开盘前净值（200）为基准：target=200=current → 无买卖，无成本。
        """
        shares = {"A": 1.0}
        cash = 0.0
        target_w = pd.Series({"A": 1.0})
        open_prices = pd.Series({"A": 200.0})          # 昨收 100，今开 200
        status = pd.Series({"A": TradeState.FREE.value})
        pretrade_value = 200.0  # cash + 1 × 200（由 _run_main_loop 计算后传入）

        _, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            pretrade_value, EXEC_DATE, REBAL_DATE,
        )

        assert record.sell_value == pytest.approx(0.0, abs=1e-6), "不应有卖出"
        assert record.buy_value == pytest.approx(0.0, abs=1e-6), "不应有买入"
        assert record.cost == pytest.approx(0.0, abs=1e-6), "无交易应无成本"

    def test_trade_occurs_when_pretrade_value_is_stale_close(self):
        """
        展示 F7-001 修复前的错误行为：以上一日收盘净值（100）为基准时，
        相同目标权重会产生虚假卖出，从而证明修复的必要性。
        这里只做负例文档，不要求 sell_value=0。
        """
        shares = {"A": 1.0}
        cash = 0.0
        target_w = pd.Series({"A": 1.0})
        open_prices = pd.Series({"A": 200.0})
        status = pd.Series({"A": TradeState.FREE.value})
        stale_close_value = 100.0  # 上一日收盘净值，不是今日开盘前净值

        _, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            stale_close_value, EXEC_DATE, REBAL_DATE,
        )

        # target=100, current=200 → 产生约 100 的虚假卖出
        assert record.sell_value > 90.0, (
            "以旧收盘净值为基准会产生大额虚假卖出，印证 F7-001 的存在"
        )

    def test_partial_rebalance_with_correct_pretrade(self):
        """权重发生真实变化时，以正确开盘前净值为基准，成交量符合预期。"""
        # 初始：100% A，目标：50% A + 50% 现金
        shares = {"A": 1.0}
        cash = 0.0
        target_w = pd.Series({"A": 0.5})   # 减仓一半
        open_prices = pd.Series({"A": 100.0})
        status = pd.Series({"A": TradeState.FREE.value})
        pretrade_value = 100.0  # 1 股 × 100 元

        _, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            pretrade_value, EXEC_DATE, REBAL_DATE,
        )

        # 卖出约 50 元（从 100→50）
        assert record.sell_value == pytest.approx(50.0, abs=1.0)
        assert record.buy_value == pytest.approx(0.0, abs=1e-6)


# ─── F7-002：缺失状态默认 LOCKED ─────────────────────────────────────────────

class TestMissingStatusLocked:
    """不在状态快照中的股票应保守设为 LOCKED，而非 FREE。"""

    def test_stock_absent_from_snap_is_locked(self):
        """B 不在状态快照中，应得到 LOCKED 状态。"""
        snap = _make_status_snap("A")
        state = _status_to_trade_state(snap, ["A", "B"])

        assert state["A"] == TradeState.FREE.value, "A 在快照中且无约束，应为 FREE"
        assert state["B"] == TradeState.LOCKED.value, "B 不在快照中，应为 LOCKED"

    def test_suspended_stock_is_locked(self):
        """明确停牌的股票应为 LOCKED。"""
        snap = _make_status_snap("A", "B", is_suspended=["B"])
        state = _status_to_trade_state(snap, ["A", "B"])

        assert state["A"] == TradeState.FREE.value
        assert state["B"] == TradeState.LOCKED.value

    def test_limit_up_stock_is_no_buy(self):
        """涨停股票应为 NO_BUY。"""
        snap = _make_status_snap("A", is_limit_up_locked=["A"])
        state = _status_to_trade_state(snap, ["A"])
        assert state["A"] == TradeState.NO_BUY.value

    def test_limit_down_stock_is_no_sell(self):
        """跌停股票应为 NO_SELL。"""
        snap = _make_status_snap("A", is_limit_down_locked=["A"])
        state = _status_to_trade_state(snap, ["A"])
        assert state["A"] == TradeState.NO_SELL.value

    def test_st_stock_is_buy_limit(self):
        """ST 股票应为 BUY_LIMIT。"""
        snap = _make_status_snap("A", is_st=["A"])
        state = _status_to_trade_state(snap, ["A"])
        assert state["A"] == TradeState.BUY_LIMIT.value

    def test_missing_status_stock_cannot_be_bought(self):
        """状态缺失的股票设为 LOCKED，_execute_rebalance 应保留原有仓位不交易。"""
        shares = {}
        cash = 1.0
        target_w = pd.Series({"B": 1.0})  # 想买 B
        open_prices = pd.Series({"B": 100.0})
        # B 状态为 LOCKED（模拟缺失后的保守路径）
        status = pd.Series({"B": TradeState.LOCKED.value})

        new_shares, new_cash, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            cash, EXEC_DATE, REBAL_DATE,
        )

        assert record.buy_value == pytest.approx(0.0, abs=1e-6), (
            "LOCKED 状态股票不应被买入"
        )
        assert "B" not in new_shares or new_shares.get("B", 0.0) < 1e-10

    def test_load_exec_status_uses_locked_when_data_missing(self, monkeypatch):
        """load_stock_status 抛出 KeyError 时，所有股票应设为 LOCKED（非 FREE）。"""
        import src.backtest.engine as engine_module

        def _raise_key_error(_date):
            raise KeyError("无数据")

        monkeypatch.setattr(engine_module, "load_stock_status", _raise_key_error)

        codes = ["A", "B", "C"]
        result = _load_exec_status([EXEC_DATE], codes)

        for code in codes:
            assert result[EXEC_DATE][code] == TradeState.LOCKED.value, (
                f"整日状态缺失时 {code} 应为 LOCKED"
            )


# ─── F7-003：n_no_price 只计活跃股票 ─────────────────────────────────────────

class TestNNoPriceActiveOnly:
    """n_no_price 应只统计当前持仓或目标权重非零的股票。"""

    def test_inactive_zero_weight_zero_holding_not_counted(self):
        """
        B 目标权重=0 且无持仓，开盘价缺失也不应计入 n_no_price。
        """
        shares = {}
        cash = 1.0
        target_w = pd.Series({"A": 1.0, "B": 0.0})
        open_prices = pd.Series({"A": 100.0})   # B 无开盘价
        status = pd.Series({"A": TradeState.FREE.value, "B": TradeState.FREE.value})

        _, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            cash, EXEC_DATE, REBAL_DATE,
        )

        assert record.n_no_price == 0, (
            "B 无目标权重且无持仓，开盘价缺失不应计入 n_no_price"
        )

    def test_active_holding_no_price_is_counted(self):
        """
        持有 A 但 A 无开盘价（停牌），应计入 n_no_price 且 A 仓位保持不变。
        """
        shares = {"A": 1.0}
        cash = 0.0
        target_w = pd.Series({"A": 0.5})   # 想减仓，但停牌
        open_prices = pd.Series(dtype=float)  # A 无开盘价
        status = pd.Series({"A": TradeState.FREE.value})

        new_shares, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            100.0, EXEC_DATE, REBAL_DATE,
        )

        assert record.n_no_price == 1, "持仓 A 无开盘价应计入 n_no_price"
        assert new_shares.get("A", 0.0) == pytest.approx(1.0, abs=1e-9), (
            "无开盘价时原有持仓保持不变"
        )

    def test_target_nonzero_no_price_is_counted(self):
        """目标权重非零但无开盘价的股票，应计入 n_no_price。"""
        shares = {}
        cash = 1.0
        target_w = pd.Series({"A": 1.0})   # 想买 A
        open_prices = pd.Series(dtype=float)  # A 无开盘价
        status = pd.Series({"A": TradeState.FREE.value})

        _, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            cash, EXEC_DATE, REBAL_DATE,
        )

        assert record.n_no_price == 1, "目标非零但无开盘价的 A 应计入 n_no_price"

    def test_large_inactive_universe_not_counted(self):
        """
        权重面板包含大量历史股票（目标权重为 0），只有 1 只活跃股票 A。
        活跃股票 A 无开盘价 → n_no_price=1，不受非活跃股票数量影响。
        """
        inactive_codes = [f"X{i:04d}.SZ" for i in range(500)]
        shares = {"A": 1.0}
        cash = 0.0
        # 500 只历史股票权重为 0，活跃股 A 权重 1.0
        weights = {"A": 1.0}
        weights.update({c: 0.0 for c in inactive_codes})
        target_w = pd.Series(weights)
        open_prices = pd.Series(dtype=float)   # 所有股票都无开盘价
        all_codes = ["A"] + inactive_codes
        status = pd.Series(TradeState.FREE.value, index=all_codes)

        _, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            100.0, EXEC_DATE, REBAL_DATE,
        )

        assert record.n_no_price == 1, (
            "500 只非活跃股票不应被计入 n_no_price，只有持仓 A 算 1"
        )


# ─── 涨跌停约束边界用例 ───────────────────────────────────────────────────────

class TestLimitConstraints:
    """涨跌停约束：涨停不能加仓，跌停不能减仓。"""

    def test_limit_up_cannot_add_position(self):
        """涨停时，想加仓但不能；持仓保持原值，n_no_buy 计数。"""
        shares = {"A": 0.5}   # 已有 0.5 股
        cash = 50.0
        target_w = pd.Series({"A": 1.0})   # 想增仓到 100%
        open_prices = pd.Series({"A": 100.0})
        status = pd.Series({"A": TradeState.NO_BUY.value})

        new_shares, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            100.0, EXEC_DATE, REBAL_DATE,
        )

        assert record.n_no_buy == 1, "涨停时无法加仓，应记录 n_no_buy"
        assert new_shares.get("A", 0.0) == pytest.approx(0.5, abs=1e-6), (
            "涨停时持仓不变"
        )

    def test_limit_up_allows_reduce(self):
        """涨停时，减仓到目标是允许的。"""
        shares = {"A": 1.0}
        cash = 0.0
        target_w = pd.Series({"A": 0.5})   # 减仓
        open_prices = pd.Series({"A": 100.0})
        status = pd.Series({"A": TradeState.NO_BUY.value})

        _, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            100.0, EXEC_DATE, REBAL_DATE,
        )

        assert record.sell_value == pytest.approx(50.0, abs=1.0), (
            "涨停时减仓允许"
        )

    def test_limit_down_cannot_reduce_position(self):
        """跌停时，想减仓但不能；持仓保持，n_no_sell 计数。"""
        shares = {"A": 1.0}
        cash = 0.0
        target_w = pd.Series({"A": 0.0})   # 想清仓
        open_prices = pd.Series({"A": 100.0})
        status = pd.Series({"A": TradeState.NO_SELL.value})

        new_shares, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            100.0, EXEC_DATE, REBAL_DATE,
        )

        assert record.n_no_sell == 1, "跌停时无法减仓，应记录 n_no_sell"
        assert new_shares.get("A", 0.0) == pytest.approx(1.0, abs=1e-6), (
            "跌停时持仓不变"
        )

    def test_locked_stock_keeps_position(self):
        """停牌（LOCKED）时，无论目标如何，仓位保持不变。"""
        shares = {"A": 1.0}
        cash = 0.0
        target_w = pd.Series({"A": 0.0})   # 想清仓
        open_prices = pd.Series({"A": 100.0})
        status = pd.Series({"A": TradeState.LOCKED.value})

        new_shares, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            100.0, EXEC_DATE, REBAL_DATE,
        )

        assert record.n_locked == 1
        assert new_shares.get("A", 0.0) == pytest.approx(1.0, abs=1e-6), (
            "停牌时持仓不变"
        )

    def test_buy_limit_cannot_add_but_can_reduce(self):
        """BUY_LIMIT（ST/次新股）：不能加仓，可以减仓。"""
        shares = {"A": 0.5}
        cash = 50.0
        target_w = pd.Series({"A": 0.3})  # 减仓
        open_prices = pd.Series({"A": 100.0})
        status = pd.Series({"A": TradeState.BUY_LIMIT.value})

        _, _, record = _execute_rebalance(
            shares, cash, target_w, open_prices, status,
            100.0, EXEC_DATE, REBAL_DATE,
        )
        assert record.sell_value == pytest.approx(20.0, abs=1.0), (
            "BUY_LIMIT 时允许减仓"
        )
        # 反方向：想加仓
        target_w_add = pd.Series({"A": 0.8})
        _, _, record2 = _execute_rebalance(
            shares, cash, target_w_add, open_prices, status,
            100.0, EXEC_DATE, REBAL_DATE,
        )
        assert record2.n_no_buy == 1, "BUY_LIMIT 时无法加仓"
