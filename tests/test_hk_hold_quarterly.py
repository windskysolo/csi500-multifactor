"""
tests/test_hk_hold_quarterly.py — 北向持仓季度 PIT 快照因子测试
===============================================================

对应实施计划：current work/6.2/implementation_plan.md Step 2

覆盖 2026-06 修订后 factor_hk_hold_ratio / factor_hk_hold_chg 的新定义：
  - 季度 PIT 快照：pit_cutoff = T - HK_HOLD_QUARTERLY_OFFSET_DAYS(10)
  - 最大滞后保护：MAX_HK_HOLD_STALENESS_DAYS = 120 天
  - chg 分割：mid_cutoff = pit_cutoff - HK_HOLD_CHG_SPLIT_DAYS(91)

测试策略：
  _filtered_loader 模拟真实 loader 的日期过滤行为（仅返回 [start, end] 内的记录），
  使因子传入的日期参数能被隐式验证，不依赖磁盘数据。
"""

import pandas as pd
import pytest

import src.factors.alt_factors as af
from src.factors.alt_factors import (
    HK_HOLD_CHG_SPLIT_DAYS,
    HK_HOLD_QUARTERLY_OFFSET_DAYS,
    MAX_HK_HOLD_STALENESS_DAYS,
    factor_hk_hold_chg,
    factor_hk_hold_ratio,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_hk_hold(records: list[dict]) -> pd.DataFrame:
    """构造 (trade_date, ts_code) MultiIndex 的 hk_hold 格式 DataFrame。"""
    df = pd.DataFrame(records)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index(["trade_date", "ts_code"])


def _filtered_loader(hk_full: pd.DataFrame):
    """
    构造模拟真实 loader 日期过滤行为的 monkeypatch 函数。

    只返回 trade_date ∈ [start, end] 且 ts_code ∈ codes 的行。
    因子传入的日期范围（pit_cutoff / window_start）被隐式验证：
    若因子传错日期，某些数据会被意外过滤，断言会失败。
    """
    def _load(start: pd.Timestamp, end: pd.Timestamp, codes=None):
        if hk_full.empty:
            return hk_full
        idx_dates = hk_full.index.get_level_values("trade_date")
        mask = (idx_dates >= start) & (idx_dates <= end)
        if codes is not None:
            mask &= hk_full.index.get_level_values("ts_code").isin(codes)
        return hk_full[mask]
    return _load


# ---------------------------------------------------------------------------
# factor_hk_hold_ratio 测试
# ---------------------------------------------------------------------------

class TestHkHoldRatioQuarterly:
    """验证 factor_hk_hold_ratio 季度 PIT 快照版的各种场景。"""

    def test_empty_data_returns_all_nan(self, monkeypatch):
        """load_hk_hold 返回空 DataFrame（沪深港通开通前）→ 全 NaN，不抛异常。"""
        T = pd.Timestamp("2013-06-28")
        monkeypatch.setattr(af, "load_hk_hold",
                            lambda start, end, codes=None: pd.DataFrame())

        result = factor_hk_hold_ratio(T, ["000001.SZ", "000002.SZ"])
        assert result.isna().all(), "无数据时 hk_hold_ratio 应全 NaN"

    def test_valid_quarterly_snapshot_returned(self, monkeypatch):
        """季度末披露日距 T 在 120 天以内 → 返回正确 ratio 值。"""
        T = pd.Timestamp("2024-11-29")
        # 2024-09-30 距 T 约 60 天，满足滞后阈值
        hk_data = _make_hk_hold([
            {"trade_date": "2024-09-30", "ts_code": "000001.SZ",
             "vol": 1000, "ratio": 4.2, "exchange": "sh"},
            {"trade_date": "2024-09-30", "ts_code": "000002.SZ",
             "vol": 500,  "ratio": 1.8, "exchange": "sz"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_ratio(T, ["000001.SZ", "000002.SZ"])
        assert not result.isna().any(), "120天内的有效披露不应为 NaN"
        assert float(result["000001.SZ"]) == pytest.approx(4.2)
        assert float(result["000002.SZ"]) == pytest.approx(1.8)

    def test_staleness_over_threshold_returns_nan(self, monkeypatch):
        """最近一次披露距 T 超过 MAX_HK_HOLD_STALENESS_DAYS(120) 天 → NaN。"""
        T = pd.Timestamp("2024-11-29")
        stale_days = MAX_HK_HOLD_STALENESS_DAYS + 5  # 125 天，超出阈值
        stale_date = T - pd.Timedelta(days=stale_days)

        hk_data = _make_hk_hold([
            {"trade_date": stale_date, "ts_code": "000001.SZ",
             "vol": 800, "ratio": 3.0, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_ratio(T, ["000001.SZ"])
        assert pd.isna(result["000001.SZ"]), (
            f"披露滞后 {stale_days} 天 > {MAX_HK_HOLD_STALENESS_DAYS} 天阈值，应返回 NaN"
        )

    def test_staleness_exactly_at_threshold_is_valid(self, monkeypatch):
        """最近一次披露距 T 恰好 = MAX_HK_HOLD_STALENESS_DAYS(120) 天 → 有效（边界包含）。"""
        T = pd.Timestamp("2024-11-29")
        boundary_date = T - pd.Timedelta(days=MAX_HK_HOLD_STALENESS_DAYS)

        hk_data = _make_hk_hold([
            {"trade_date": boundary_date, "ts_code": "000001.SZ",
             "vol": 800, "ratio": 2.5, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_ratio(T, ["000001.SZ"])
        assert not pd.isna(result["000001.SZ"]), "恰好 120 天边界应视为有效"
        assert float(result["000001.SZ"]) == pytest.approx(2.5)

    def test_pit_cutoff_excludes_undisclosed_quarterly_data(self, monkeypatch):
        """
        PIT 偏移验证：trade_date 在 T - HK_HOLD_QUARTERLY_OFFSET_DAYS(10) 之后的
        数据尚未被披露，不应被使用。

        场景：T = 2024-10-08（季末后第 8 天），2024-09-30 季度数据约需 10 天才披露，
        因此 pit_cutoff = T - 10 = 2024-09-28，季末 9-30 的数据被正确排除，
        因子应回退使用 2024-06-30 的上季度数据。
        """
        T = pd.Timestamp("2024-10-08")
        # pit_cutoff = 2024-09-28；2024-09-30 > pit_cutoff → 被 _filtered_loader 过滤
        hk_data = _make_hk_hold([
            {"trade_date": "2024-09-30", "ts_code": "000001.SZ",
             "vol": 900, "ratio": 5.0, "exchange": "sh"},  # 未披露，应被排除
            {"trade_date": "2024-06-30", "ts_code": "000001.SZ",
             "vol": 800, "ratio": 3.5, "exchange": "sh"},  # 上季度，合法
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_ratio(T, ["000001.SZ"])
        assert not pd.isna(result["000001.SZ"]), "上季度数据应可用"
        assert float(result["000001.SZ"]) == pytest.approx(3.5), (
            "PIT 偏移保护：应用上季度 3.5，而非尚未披露的 5.0"
        )

    def test_takes_most_recent_snapshot_among_multiple_dates(self, monkeypatch):
        """窗口内有多个有效披露日时，应取 trade_date 最新的那个。"""
        T = pd.Timestamp("2025-01-31")
        hk_data = _make_hk_hold([
            {"trade_date": "2024-06-30", "ts_code": "000001.SZ",
             "vol": 800, "ratio": 2.0, "exchange": "sh"},
            {"trade_date": "2024-09-30", "ts_code": "000001.SZ",
             "vol": 900, "ratio": 3.5, "exchange": "sh"},
            {"trade_date": "2024-12-31", "ts_code": "000001.SZ",
             "vol": 950, "ratio": 4.8, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_ratio(T, ["000001.SZ"])
        assert float(result["000001.SZ"]) == pytest.approx(4.8), (
            "应取最新披露日 2024-12-31 的 ratio=4.8，而非早期的 2.0 或 3.5"
        )

    def test_stock_without_hk_data_returns_nan(self, monkeypatch):
        """无北向持仓记录的股票应返回 NaN，不影响其他股票的正常计算。"""
        T = pd.Timestamp("2024-11-29")
        hk_data = _make_hk_hold([
            {"trade_date": "2024-09-30", "ts_code": "000001.SZ",
             "vol": 900, "ratio": 3.0, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_ratio(T, ["000001.SZ", "999999.SZ"])
        assert not pd.isna(result["000001.SZ"]), "有持仓记录的股票应返回有效值"
        assert pd.isna(result["999999.SZ"]),     "无持仓记录的股票应返回 NaN"


# ---------------------------------------------------------------------------
# factor_hk_hold_chg 测试
# ---------------------------------------------------------------------------

class TestHkHoldChgQuarterly:
    """验证 factor_hk_hold_chg 季度 PIT 快照差分版的各种场景。"""

    def test_empty_data_returns_all_nan(self, monkeypatch):
        """load_hk_hold 返回空 DataFrame → 全 NaN，不抛异常。"""
        T = pd.Timestamp("2013-06-28")
        monkeypatch.setattr(af, "load_hk_hold",
                            lambda start, end, codes=None: pd.DataFrame())

        result = factor_hk_hold_chg(T, ["000001.SZ"])
        assert result.isna().all(), "无数据时 hk_hold_chg 应全 NaN"

    def test_only_latest_window_data_returns_nan(self, monkeypatch):
        """
        只有近端（mid_cutoff, pit_cutoff] 数据，先端无数据 → NaN。
        差分需要两个时间点，缺少先前快照时不能计算。
        """
        T = pd.Timestamp("2025-01-31")
        pit_cutoff = T - pd.Timedelta(days=HK_HOLD_QUARTERLY_OFFSET_DAYS)
        mid_cutoff = pit_cutoff - pd.Timedelta(days=HK_HOLD_CHG_SPLIT_DAYS)

        latest_date = mid_cutoff + pd.Timedelta(days=20)  # 在近端窗口内
        hk_data = _make_hk_hold([
            {"trade_date": latest_date, "ts_code": "000001.SZ",
             "vol": 900, "ratio": 4.0, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_chg(T, ["000001.SZ"])
        assert pd.isna(result["000001.SZ"]), "缺少先前快照时 hk_hold_chg 应为 NaN"

    def test_only_prior_window_data_returns_nan(self, monkeypatch):
        """
        只有先端数据（≤ mid_cutoff），近端无数据 → NaN。
        没有近端快照则无法表达"当前"状态。
        """
        T = pd.Timestamp("2025-01-31")
        pit_cutoff = T - pd.Timedelta(days=HK_HOLD_QUARTERLY_OFFSET_DAYS)
        mid_cutoff = pit_cutoff - pd.Timedelta(days=HK_HOLD_CHG_SPLIT_DAYS)

        prior_date = mid_cutoff - pd.Timedelta(days=20)  # 在先端窗口内
        hk_data = _make_hk_hold([
            {"trade_date": prior_date, "ts_code": "000001.SZ",
             "vol": 800, "ratio": 2.5, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_chg(T, ["000001.SZ"])
        assert pd.isna(result["000001.SZ"]), "缺少近端快照时 hk_hold_chg 应为 NaN"

    def test_chg_positive_when_ratio_increases(self, monkeypatch):
        """近端 ratio 高于先端时，hk_hold_chg 应为正值。"""
        T = pd.Timestamp("2025-01-31")
        pit_cutoff = T - pd.Timedelta(days=HK_HOLD_QUARTERLY_OFFSET_DAYS)
        mid_cutoff = pit_cutoff - pd.Timedelta(days=HK_HOLD_CHG_SPLIT_DAYS)

        prior_date  = mid_cutoff - pd.Timedelta(days=10)  # 先端
        latest_date = mid_cutoff + pd.Timedelta(days=10)  # 近端

        hk_data = _make_hk_hold([
            {"trade_date": prior_date,  "ts_code": "000001.SZ",
             "vol": 800, "ratio": 2.0, "exchange": "sh"},
            {"trade_date": latest_date, "ts_code": "000001.SZ",
             "vol": 950, "ratio": 4.5, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_chg(T, ["000001.SZ"])
        assert not pd.isna(result["000001.SZ"]), "有效双端数据不应返回 NaN"
        assert float(result["000001.SZ"]) == pytest.approx(4.5 - 2.0), (
            "hk_hold_chg = latest_ratio(4.5) - prior_ratio(2.0) = 2.5"
        )

    def test_chg_negative_when_ratio_declines(self, monkeypatch):
        """近端 ratio 低于先端时，hk_hold_chg 应为负值。"""
        T = pd.Timestamp("2025-01-31")
        pit_cutoff = T - pd.Timedelta(days=HK_HOLD_QUARTERLY_OFFSET_DAYS)
        mid_cutoff = pit_cutoff - pd.Timedelta(days=HK_HOLD_CHG_SPLIT_DAYS)

        prior_date  = mid_cutoff - pd.Timedelta(days=10)
        latest_date = mid_cutoff + pd.Timedelta(days=10)

        hk_data = _make_hk_hold([
            {"trade_date": prior_date,  "ts_code": "000001.SZ",
             "vol": 950, "ratio": 5.0, "exchange": "sh"},
            {"trade_date": latest_date, "ts_code": "000001.SZ",
             "vol": 700, "ratio": 2.0, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_chg(T, ["000001.SZ"])
        assert float(result["000001.SZ"]) < 0, "持仓比例下降时 hk_hold_chg 应为负值"
        assert float(result["000001.SZ"]) == pytest.approx(2.0 - 5.0)

    def test_real_quarterly_dates_scenario(self, monkeypatch):
        """
        真实季度制场景验证：T = 2025-01-31，近端 = 2024-12-31，先端 = 2024-09-30。

        pit_cutoff = 2025-01-21，mid_cutoff ≈ 2024-10-21：
          2024-12-31 > 2024-10-21 → 近端 ✓
          2024-09-30 ≤ 2024-10-21 → 先端 ✓
        """
        T = pd.Timestamp("2025-01-31")
        hk_data = _make_hk_hold([
            {"trade_date": "2024-09-30", "ts_code": "000001.SZ",
             "vol": 800, "ratio": 3.0, "exchange": "sh"},
            {"trade_date": "2024-12-31", "ts_code": "000001.SZ",
             "vol": 900, "ratio": 4.5, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_chg(T, ["000001.SZ"])
        assert float(result["000001.SZ"]) == pytest.approx(4.5 - 3.0), (
            "季度变化 = 2024-12-31 ratio(4.5) - 2024-09-30 ratio(3.0) = 1.5"
        )

    def test_stock_missing_prior_data_returns_nan_without_affecting_others(
        self, monkeypatch
    ):
        """
        股票 A 有双端数据，股票 B 仅有近端数据。
        B 应返回 NaN，A 正常计算，互不干扰。
        """
        T = pd.Timestamp("2025-01-31")
        pit_cutoff = T - pd.Timedelta(days=HK_HOLD_QUARTERLY_OFFSET_DAYS)
        mid_cutoff = pit_cutoff - pd.Timedelta(days=HK_HOLD_CHG_SPLIT_DAYS)

        prior_date  = mid_cutoff - pd.Timedelta(days=10)
        latest_date = mid_cutoff + pd.Timedelta(days=10)

        hk_data = _make_hk_hold([
            # 股票 A：先端 + 近端均有
            {"trade_date": prior_date,  "ts_code": "000001.SZ",
             "vol": 800, "ratio": 2.0, "exchange": "sh"},
            {"trade_date": latest_date, "ts_code": "000001.SZ",
             "vol": 900, "ratio": 3.5, "exchange": "sh"},
            # 股票 B：仅近端有数据
            {"trade_date": latest_date, "ts_code": "000002.SZ",
             "vol": 700, "ratio": 1.5, "exchange": "sh"},
        ])
        monkeypatch.setattr(af, "load_hk_hold", _filtered_loader(hk_data))

        result = factor_hk_hold_chg(T, ["000001.SZ", "000002.SZ"])
        assert float(result["000001.SZ"]) == pytest.approx(3.5 - 2.0), \
            "A 应正常计算：3.5 - 2.0 = 1.5"
        assert pd.isna(result["000002.SZ"]), \
            "B 缺少先端数据，应返回 NaN"
