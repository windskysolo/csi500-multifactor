"""
tests/test_phase4_factors.py — 阶段 4 低风险因子扩展测试

覆盖：
  1. get_quarterly_history：多期历史提取、陈旧度过滤、不足期数时退化
  2. get_field_yoy_delta：同比计算正确性、缺失上年同期时返回 NaN
  3. factor_roe_smoothed_4q：均值平滑、不足期数的 skipna 均值
  4. factor_roe_delta_3q：差值正确性、q3 缺失时返回 NaN
  5. factor_gross_margin_trend：同比变化正确
  6. PIT 约束：历史提取函数严格不使用未来数据
"""

import numpy as np
import pandas as pd
import pytest

from src.data.pit_loader import (
    get_field_yoy_delta,
    get_quarterly_history,
)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def _make_ip_df(records: list[dict]) -> pd.DataFrame:
    """构造最小 indicator_pit 格式（reset_index 后）。"""
    df = pd.DataFrame(records)
    df["pit_date"] = pd.to_datetime(df["pit_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    return df


# ---------------------------------------------------------------------------
# get_quarterly_history
# ---------------------------------------------------------------------------

class TestGetQuarterlyHistory:

    def test_returns_last_4_quarters_in_order(self):
        """q0 是最新期，q1/q2/q3 依次是前期，值正确。"""
        T = pd.Timestamp("2023-01-31")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-04-30", "end_date": "2022-03-31", "q_roe": 1.0},
            {"ts_code": "000001.SZ", "pit_date": "2022-08-31", "end_date": "2022-06-30", "q_roe": 2.0},
            {"ts_code": "000001.SZ", "pit_date": "2022-10-31", "end_date": "2022-09-30", "q_roe": 3.0},
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15", "end_date": "2022-12-31", "q_roe": 4.0},
        ])
        result = get_quarterly_history(df, "q_roe", T, ["000001.SZ"], n_quarters=4)

        assert result.loc["000001.SZ", "q0"] == pytest.approx(4.0)
        assert result.loc["000001.SZ", "q1"] == pytest.approx(3.0)
        assert result.loc["000001.SZ", "q2"] == pytest.approx(2.0)
        assert result.loc["000001.SZ", "q3"] == pytest.approx(1.0)

    def test_fewer_than_n_quarters_available(self):
        """只有 2 期数据时，q0/q1 有值，q2/q3 为 NaN。"""
        T = pd.Timestamp("2023-01-31")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-08-31", "end_date": "2022-06-30", "q_roe": 2.0},
            {"ts_code": "000001.SZ", "pit_date": "2022-10-31", "end_date": "2022-09-30", "q_roe": 3.0},
        ])
        result = get_quarterly_history(df, "q_roe", T, ["000001.SZ"], n_quarters=4)

        assert result.loc["000001.SZ", "q0"] == pytest.approx(3.0)
        assert result.loc["000001.SZ", "q1"] == pytest.approx(2.0)
        assert np.isnan(result.loc["000001.SZ", "q2"])
        assert np.isnan(result.loc["000001.SZ", "q3"])

    def test_future_pit_date_excluded(self):
        """pit_date 在 T 之后的记录不进入历史。"""
        T = pd.Timestamp("2023-01-15")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2023-01-20",  # 未来公告
             "end_date": "2022-12-31", "q_roe": 99.0},
            {"ts_code": "000001.SZ", "pit_date": "2022-10-31",
             "end_date": "2022-09-30", "q_roe": 3.0},
        ])
        result = get_quarterly_history(df, "q_roe", T, ["000001.SZ"], n_quarters=4)

        # 只有 Q3 2022 可见，Annual 2022 未来公告不可用
        assert result.loc["000001.SZ", "q0"] == pytest.approx(3.0)
        assert np.isnan(result.loc["000001.SZ", "q1"])

    def test_stale_data_returns_nan(self):
        """最新期 end_date 距 T 超过 stale_months，整行置 NaN。"""
        T = pd.Timestamp("2023-06-30")
        df = _make_ip_df([
            # end_date = 2021-12-31，距 T 约 18 个月，stale_months=9 → 过期
            {"ts_code": "000001.SZ", "pit_date": "2022-04-30", "end_date": "2021-12-31", "q_roe": 5.0},
        ])
        result = get_quarterly_history(df, "q_roe", T, ["000001.SZ"], n_quarters=4, stale_months=9)

        assert result.loc["000001.SZ"].isna().all(), "过期数据应全部为 NaN"

    def test_code_not_in_data_returns_nan(self):
        """无数据的股票返回全 NaN 行。"""
        T = pd.Timestamp("2023-01-31")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-10-31", "end_date": "2022-09-30", "q_roe": 3.0},
        ])
        result = get_quarterly_history(df, "q_roe", T, ["000001.SZ", "999999.SZ"], n_quarters=2)

        assert result.loc["999999.SZ"].isna().all()

    def test_latest_revision_wins(self):
        """同一 (ts_code, end_date) 的多次修订，取 pit_date 最新的修订值。"""
        T = pd.Timestamp("2023-01-31")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2023-01-10", "end_date": "2022-12-31", "q_roe": 4.0},
            {"ts_code": "000001.SZ", "pit_date": "2023-01-20", "end_date": "2022-12-31", "q_roe": 4.5},  # 最新修订
        ])
        result = get_quarterly_history(df, "q_roe", T, ["000001.SZ"], n_quarters=1)

        assert result.loc["000001.SZ", "q0"] == pytest.approx(4.5)


# ---------------------------------------------------------------------------
# get_field_yoy_delta
# ---------------------------------------------------------------------------

class TestGetFieldYoyDelta:

    def test_yoy_delta_basic(self):
        """同比变化 = 当期 - 上年同期，计算正确。"""
        T = pd.Timestamp("2023-01-31")
        df = _make_ip_df([
            # 上年同期：Annual 2021
            {"ts_code": "000001.SZ", "pit_date": "2022-04-30", "end_date": "2021-12-31", "grossprofit_margin": 30.0},
            # 当期：Annual 2022
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15", "end_date": "2022-12-31", "grossprofit_margin": 32.5},
        ])
        result = get_field_yoy_delta(df, "grossprofit_margin", T, ["000001.SZ"])

        assert result.loc["000001.SZ"] == pytest.approx(2.5)

    def test_no_prior_year_returns_nan(self):
        """无上年同期数据时返回 NaN（新上市股票）。"""
        T = pd.Timestamp("2023-01-31")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15", "end_date": "2022-12-31", "grossprofit_margin": 32.5},
        ])
        result = get_field_yoy_delta(df, "grossprofit_margin", T, ["000001.SZ"])

        assert np.isnan(result.loc["000001.SZ"])

    def test_future_pit_excluded(self):
        """当期公告在 T 之后，不可见，应返回 NaN 或使用更早的可见数据。"""
        T = pd.Timestamp("2023-01-10")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-04-30", "end_date": "2021-12-31", "grossprofit_margin": 30.0},
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15",  # T 之后
             "end_date": "2022-12-31", "grossprofit_margin": 32.5},
        ])
        result = get_field_yoy_delta(df, "grossprofit_margin", T, ["000001.SZ"])

        # Annual 2022 不可见（公告日 2023-01-15 > T）；
        # 最新可见是 Annual 2021，无上年同期（Annual 2020 未提供）→ NaN
        assert np.isnan(result.loc["000001.SZ"])

    def test_quarterly_period_yoy(self):
        """季报同比：Q3 2022 vs Q3 2021。"""
        T = pd.Timestamp("2022-11-30")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2021-10-31", "end_date": "2021-09-30", "grossprofit_margin": 28.0},
            {"ts_code": "000001.SZ", "pit_date": "2022-10-31", "end_date": "2022-09-30", "grossprofit_margin": 31.0},
        ])
        result = get_field_yoy_delta(df, "grossprofit_margin", T, ["000001.SZ"])

        assert result.loc["000001.SZ"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# 因子函数级别的集成测试（使用合成数据，不读磁盘）
# ---------------------------------------------------------------------------

class TestPhase4FactorSmoothing:
    """验证 roe_smoothed_4q 和 roe_delta_3q 的计算逻辑（通过 get_quarterly_history）。"""

    def test_roe_smoothed_4q_mean(self):
        """4 期均值正确。"""
        T = pd.Timestamp("2023-01-31")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-04-30", "end_date": "2022-03-31", "q_roe": 10.0},
            {"ts_code": "000001.SZ", "pit_date": "2022-08-31", "end_date": "2022-06-30", "q_roe": 12.0},
            {"ts_code": "000001.SZ", "pit_date": "2022-10-31", "end_date": "2022-09-30", "q_roe": 11.0},
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15", "end_date": "2022-12-31", "q_roe": 13.0},
        ])
        hist = get_quarterly_history(df, "q_roe", T, ["000001.SZ"], n_quarters=4)
        smoothed = hist.mean(axis=1, skipna=True)

        expected = (10.0 + 12.0 + 11.0 + 13.0) / 4
        assert smoothed.loc["000001.SZ"] == pytest.approx(expected)

    def test_roe_smoothed_4q_skipna_when_fewer_periods(self):
        """不足 4 期时，对可用期做 skipna 均值，不强制要求 4 期全有。"""
        T = pd.Timestamp("2023-01-31")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-10-31", "end_date": "2022-09-30", "q_roe": 11.0},
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15", "end_date": "2022-12-31", "q_roe": 13.0},
        ])
        hist = get_quarterly_history(df, "q_roe", T, ["000001.SZ"], n_quarters=4)
        smoothed = hist.mean(axis=1, skipna=True)

        expected = (11.0 + 13.0) / 2
        assert smoothed.loc["000001.SZ"] == pytest.approx(expected)

    def test_roe_delta_3q_correct(self):
        """roe_delta_3q = q0 - q3。"""
        T = pd.Timestamp("2023-01-31")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-04-30", "end_date": "2022-03-31", "q_roe": 8.0},  # q3
            {"ts_code": "000001.SZ", "pit_date": "2022-08-31", "end_date": "2022-06-30", "q_roe": 9.0},  # q2
            {"ts_code": "000001.SZ", "pit_date": "2022-10-31", "end_date": "2022-09-30", "q_roe": 10.0}, # q1
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15", "end_date": "2022-12-31", "q_roe": 12.0}, # q0
        ])
        hist = get_quarterly_history(df, "q_roe", T, ["000001.SZ"], n_quarters=4)
        delta_3q = hist["q0"] - hist["q3"]

        assert delta_3q.loc["000001.SZ"] == pytest.approx(12.0 - 8.0)

    def test_roe_delta_3q_nan_when_q3_missing(self):
        """不足 4 期时，q3 为 NaN，delta_3q 也为 NaN。"""
        T = pd.Timestamp("2023-01-31")
        df = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-10-31", "end_date": "2022-09-30", "q_roe": 10.0},
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15", "end_date": "2022-12-31", "q_roe": 12.0},
        ])
        hist = get_quarterly_history(df, "q_roe", T, ["000001.SZ"], n_quarters=4)
        delta_3q = hist["q0"] - hist["q3"]

        assert np.isnan(delta_3q.loc["000001.SZ"])
