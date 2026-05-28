"""
tests/test_universe.py — 可投资域过滤与约束状态测试

验证规则：
  - 新股（上市 < 180 天）被排除出可投资域（tradable=False）
  - ST 状态被排除（tradable=False 或 BUY_LIMIT 约束）
  - 停牌被排除（tradable=False 且 LOCKED 约束）
  - 约束状态优先级：LOCKED > NO_BUY > NO_SELL > BUY_LIMIT > FREE

测试策略：通过 monkeypatch 注入合成 universe 快照，不依赖磁盘数据。
"""

import pandas as pd
import pytest

from src.data.universe import (
    TradeState,
    get_constraint_states,
    get_investable_universe,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_universe_snap(stocks: list[dict]) -> pd.DataFrame:
    """
    构造最小 universe 快照（等效于 load_universe 返回值）。

    stocks: 每个元素是 {ts_code, tradable, is_st, is_new_stock,
                         is_suspended, is_limit_up_locked, is_limit_down_locked}
    """
    df = pd.DataFrame(stocks)
    df = df.set_index("ts_code")
    # 确保布尔类型
    for col in ["tradable", "is_st", "is_new_stock",
                "is_suspended", "is_limit_up_locked", "is_limit_down_locked"]:
        df[col] = df[col].astype(bool)
    return df


def _default_stock(ts_code: str, **overrides) -> dict:
    """返回一个普通可交易股票的默认字典，可按需覆盖字段。"""
    base = dict(
        ts_code=ts_code,
        tradable=True,
        is_st=False,
        is_new_stock=False,
        is_suspended=False,
        is_limit_up_locked=False,
        is_limit_down_locked=False,
    )
    base.update(overrides)
    return base


T = pd.Timestamp("2021-06-30")   # 固定调仓日，不依赖实际数据


# ---------------------------------------------------------------------------
# get_investable_universe：tradable 过滤测试
# ---------------------------------------------------------------------------

class TestGetInvestableUniverse:
    """验证 get_investable_universe 只返回 tradable=True 的股票。"""

    def test_normal_stocks_are_investable(self, monkeypatch):
        """普通可交易股票应出现在可投资域中。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ"),
            _default_stock("000002.SZ"),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        result = get_investable_universe(T)
        assert "000001.SZ" in result
        assert "000002.SZ" in result

    def test_st_excluded_from_investable(self, monkeypatch):
        """ST 股票 tradable=False 时不应出现在可投资域。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ"),
            _default_stock("000002.SZ", tradable=False, is_st=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        result = get_investable_universe(T)
        assert "000001.SZ" in result
        assert "000002.SZ" not in result, "ST 股票不应在可投资域"

    def test_new_stock_excluded_from_investable(self, monkeypatch):
        """新股（上市<180天）tradable=False 时不应出现在可投资域。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ"),
            _default_stock("600000.SH", tradable=False, is_new_stock=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        result = get_investable_universe(T)
        assert "000001.SZ" in result
        assert "600000.SH" not in result, "新股不应在可投资域"

    def test_suspended_excluded_from_investable(self, monkeypatch):
        """停牌股票 tradable=False 时不应出现在可投资域。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ"),
            _default_stock("000003.SZ", tradable=False, is_suspended=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        result = get_investable_universe(T)
        assert "000003.SZ" not in result, "停牌股票不应在可投资域"

    def test_empty_universe_returns_empty_index(self, monkeypatch):
        """全部不可投资时返回空 Index。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ", tradable=False, is_st=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        result = get_investable_universe(T)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# get_constraint_states：约束状态优先级测试
# ---------------------------------------------------------------------------

class TestGetConstraintStates:
    """验证约束状态赋值的优先级规则。"""

    def test_free_by_default(self, monkeypatch):
        """无任何限制的股票状态为 FREE。"""
        snap = _make_universe_snap([_default_stock("000001.SZ")])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        states = get_constraint_states(T)
        assert states["000001.SZ"] == TradeState.FREE.value

    def test_suspended_is_locked(self, monkeypatch):
        """停牌股票状态应为 LOCKED（最高优先级）。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ", tradable=False, is_suspended=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        states = get_constraint_states(T)
        assert states["000001.SZ"] == TradeState.LOCKED.value

    def test_limit_up_is_no_buy(self, monkeypatch):
        """一字涨停状态应为 NO_BUY。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ", is_limit_up_locked=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        states = get_constraint_states(T)
        assert states["000001.SZ"] == TradeState.NO_BUY.value

    def test_limit_down_is_no_sell(self, monkeypatch):
        """一字跌停状态应为 NO_SELL。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ", is_limit_down_locked=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        states = get_constraint_states(T)
        assert states["000001.SZ"] == TradeState.NO_SELL.value

    def test_st_is_buy_limit(self, monkeypatch):
        """ST 股票状态应为 BUY_LIMIT。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ", is_st=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        states = get_constraint_states(T)
        assert states["000001.SZ"] == TradeState.BUY_LIMIT.value

    def test_new_stock_is_buy_limit(self, monkeypatch):
        """新股状态应为 BUY_LIMIT。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ", is_new_stock=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        states = get_constraint_states(T)
        assert states["000001.SZ"] == TradeState.BUY_LIMIT.value

    def test_suspended_overrides_st(self, monkeypatch):
        """同时停牌且 ST 时，LOCKED 优先级高于 BUY_LIMIT。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ", is_suspended=True, is_st=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        states = get_constraint_states(T)
        assert states["000001.SZ"] == TradeState.LOCKED.value, (
            "LOCKED 应覆盖 BUY_LIMIT"
        )

    def test_suspended_overrides_limit_up(self, monkeypatch):
        """同时停牌且涨停时，LOCKED 优先级高于 NO_BUY。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ", is_suspended=True, is_limit_up_locked=True),
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        states = get_constraint_states(T)
        assert states["000001.SZ"] == TradeState.LOCKED.value

    def test_all_states_in_result(self, monkeypatch):
        """多只股票各有不同状态，全部正确赋值。"""
        snap = _make_universe_snap([
            _default_stock("000001.SZ"),                                       # FREE
            _default_stock("000002.SZ", is_st=True),                           # BUY_LIMIT
            _default_stock("000003.SZ", is_limit_down_locked=True),            # NO_SELL
            _default_stock("000004.SZ", is_limit_up_locked=True),              # NO_BUY
            _default_stock("000005.SZ", is_suspended=True),                    # LOCKED
        ])
        monkeypatch.setattr("src.data.universe.load_universe", lambda *a, **k: snap)
        states = get_constraint_states(T)

        assert states["000001.SZ"] == TradeState.FREE.value
        assert states["000002.SZ"] == TradeState.BUY_LIMIT.value
        assert states["000003.SZ"] == TradeState.NO_SELL.value
        assert states["000004.SZ"] == TradeState.NO_BUY.value
        assert states["000005.SZ"] == TradeState.LOCKED.value
