"""
tests/test_hk_hold_coverage.py — 北向持仓因子覆盖范围测试（阶段 8 门禁）

覆盖范围（对应 phase_plan.md 第 13 节）：
  - 2014-11-17 前（沪深港通开通前）hk_hold 无数据：
      hk_hold_ratio / hk_hold_chg 因子应全 NaN（预期行为，非错误）
  - 2014-11-17 后有数据时：因子应返回有效数值
  - ratio 快照取最近交易日
  - hk_hold_chg 方向正确（ratio 上升 → 正值，下降 → 负值）

测试策略：monkeypatch alt_factors 模块中的 load_hk_hold，不依赖磁盘数据。
"""

import pandas as pd
import pytest

import src.factors.alt_factors as af
from src.factors.alt_factors import factor_hk_hold_chg, factor_hk_hold_ratio


# ---------------------------------------------------------------------------
# 构造辅助函数
# ---------------------------------------------------------------------------

def _make_hk_hold(records: list[dict]) -> pd.DataFrame:
    """构造 (trade_date, ts_code) MultiIndex 的 hk_hold 格式 DataFrame。"""
    df = pd.DataFrame(records)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index(["trade_date", "ts_code"])


# ---------------------------------------------------------------------------
# factor_hk_hold_ratio：北向持仓比例
# ---------------------------------------------------------------------------

class TestHkHoldRatioCoverage:
    """验证 factor_hk_hold_ratio 在不同时间段的覆盖行为。"""

    def test_pre_connect_empty_data_returns_all_nan(self, monkeypatch):
        """
        2014-11-17 前无北向数据，load_hk_hold 返回空 DataFrame。
        因子应全 NaN——这是预期行为，不是错误。
        """
        T = pd.Timestamp("2013-06-30")
        monkeypatch.setattr(af, "load_hk_hold",
                            lambda start, end, codes=None: pd.DataFrame())

        result = factor_hk_hold_ratio(T, ["000001.SZ", "000002.SZ"])
        assert result.isna().all(), "2014-11-17 前无数据，hk_hold_ratio 应全 NaN"

    def test_post_connect_with_data_returns_correct_ratio(self, monkeypatch):
        """2014-11-17 后有持仓数据时，因子返回正确的 ratio 值。"""
        T = pd.Timestamp("2020-06-30")
        hk_data = _make_hk_hold([
            {"trade_date": "2020-06-30", "ts_code": "000001.SZ",
             "vol": 1000.0, "ratio": 3.5, "exchange": "sh"},
            {"trade_date": "2020-06-30", "ts_code": "000002.SZ",
             "vol": 500.0,  "ratio": 1.2, "exchange": "sz"},
        ])
        monkeypatch.setattr(af, "load_hk_hold",
                            lambda start, end, codes=None: hk_data)

        result = factor_hk_hold_ratio(T, ["000001.SZ", "000002.SZ"])
        assert not result.isna().all(), "有数据时 hk_hold_ratio 不应全 NaN"
        assert float(result["000001.SZ"]) == pytest.approx(3.5)
        assert float(result["000002.SZ"]) == pytest.approx(1.2)

    def test_stock_not_in_hk_data_returns_nan(self, monkeypatch):
        """非北向标的（无持仓记录）的股票应返回 NaN，北向标的正常返回。"""
        T = pd.Timestamp("2020-06-30")
        hk_data = _make_hk_hold([
            {"trade_date": "2020-06-30", "ts_code": "000001.SZ",
             "vol": 1000.0, "ratio": 3.5, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold",
                            lambda start, end, codes=None: hk_data)

        result = factor_hk_hold_ratio(T, ["000001.SZ", "999999.SZ"])
        assert not pd.isna(result["000001.SZ"]), "有持仓的股票不应为 NaN"
        assert pd.isna(result["999999.SZ"]),     "无持仓记录的股票应为 NaN"

    def test_takes_most_recent_trading_day_snapshot(self, monkeypatch):
        """多个交易日均有数据时，应取最近一个交易日的 ratio（groupby.last）。"""
        T = pd.Timestamp("2020-06-30")
        hk_data = _make_hk_hold([
            {"trade_date": "2020-06-25", "ts_code": "000001.SZ",
             "vol": 1000.0, "ratio": 3.0, "exchange": "sh"},   # 较早
            {"trade_date": "2020-06-29", "ts_code": "000001.SZ",
             "vol": 1200.0, "ratio": 3.8, "exchange": "sh"},   # 最新
        ])
        monkeypatch.setattr(af, "load_hk_hold",
                            lambda start, end, codes=None: hk_data)

        result = factor_hk_hold_ratio(T, ["000001.SZ"])
        assert float(result["000001.SZ"]) == pytest.approx(3.8), (
            "应取最近交易日的 ratio（3.8），而非早日的 3.0"
        )


# ---------------------------------------------------------------------------
# factor_hk_hold_chg：北向持仓 30 日变化量
# ---------------------------------------------------------------------------

class TestHkHoldChgCoverage:
    """验证 factor_hk_hold_chg 的变化量计算与覆盖范围。"""

    def test_pre_connect_empty_data_returns_all_nan(self, monkeypatch):
        """
        2014-11-17 前无数据：hk_hold_chg 应全 NaN。
        因子已改为季度定义，不再依赖 _all_trading_dates。
        """
        T = pd.Timestamp("2013-06-28")
        monkeypatch.setattr(af, "load_hk_hold",
                            lambda start, end, codes=None: pd.DataFrame())

        result = factor_hk_hold_chg(T, ["000001.SZ"])
        assert result.isna().all(), "无数据时 hk_hold_chg 应全 NaN"

    def test_chg_positive_when_ratio_increases(self, monkeypatch):
        """
        ratio 从先端快照到近端快照上升时，hk_hold_chg 应为正值。

        因子已改为季度 PIT 差分定义：
          mid_cutoff = pit_cutoff - 91天，先端 ≤ mid_cutoff，近端 > mid_cutoff。
        数据日期必须分处两侧，否则 prior_df 为空 → NaN。
        """
        T = pd.Timestamp("2020-06-30")
        # pit_cutoff = 2020-06-20；mid_cutoff = 2020-03-21
        # 先端：2020-03-01 ≤ mid_cutoff；近端：2020-05-15 > mid_cutoff
        hk_data = _make_hk_hold([
            {"trade_date": "2020-03-01", "ts_code": "000001.SZ",
             "vol": 900.0,  "ratio": 2.0, "exchange": "sh"},   # 先端快照
            {"trade_date": "2020-05-15", "ts_code": "000001.SZ",
             "vol": 1200.0, "ratio": 4.5, "exchange": "sh"},   # 近端快照
        ])
        monkeypatch.setattr(af, "load_hk_hold",
                            lambda start, end, codes=None: hk_data)

        result = factor_hk_hold_chg(T, ["000001.SZ"])
        assert not result.isna().all(), "有双端数据时 hk_hold_chg 不应全 NaN"
        assert float(result["000001.SZ"]) == pytest.approx(4.5 - 2.0), (
            "hk_hold_chg = 近端 ratio(4.5) - 先端 ratio(2.0) = 2.5"
        )

    def test_chg_negative_when_ratio_declines(self, monkeypatch):
        """ratio 从先端快照到近端快照下降时，hk_hold_chg 应为负值。"""
        T = pd.Timestamp("2020-06-30")
        # pit_cutoff = 2020-06-20；mid_cutoff = 2020-03-21
        hk_data = _make_hk_hold([
            {"trade_date": "2020-03-01", "ts_code": "000001.SZ",
             "vol": 1200.0, "ratio": 5.0, "exchange": "sh"},   # 先端快照
            {"trade_date": "2020-05-15", "ts_code": "000001.SZ",
             "vol": 900.0,  "ratio": 3.0, "exchange": "sh"},   # 近端快照
        ])
        monkeypatch.setattr(af, "load_hk_hold",
                            lambda start, end, codes=None: hk_data)

        result = factor_hk_hold_chg(T, ["000001.SZ"])
        assert float(result["000001.SZ"]) < 0, "ratio 下降时 hk_hold_chg 应为负值"
        assert float(result["000001.SZ"]) == pytest.approx(3.0 - 5.0)
