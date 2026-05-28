"""
tests/test_factor_time_boundary.py — 因子时间边界与后复权收益单元测试（F3-003）

覆盖范围：
  F3-001 / F3-003:
  - 量价因子（vol_60d / max_ret / amihud）使用 dq["ret"]（后复权收益），
    而非 pct_chg；通过 mock load_daily_quote 注入已知 ret 值验证
  - load_daily_quote 的 end 参数不超过调仓日 T（不使用未来数据）
  - _valid_dates_before(T) 返回结果不包含 T 之后的日期

所有测试使用合成数据 + monkeypatch，不依赖磁盘文件。

注意：_all_trading_dates 带 @lru_cache(maxsize=1)；通过
monkeypatching 模块属性替换函数引用可绕过缓存，但需在 teardown 时
cache_clear() 防止污染其他测试。
"""

import numpy as np
import pandas as pd
import pytest

import src.factors.price_factors as pf
from src.factors.price_factors import (
    _valid_dates_before,
    factor_amihud,
    factor_max_ret,
    factor_vol_60d,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _business_days(start: str = "2020-01-02", periods: int = 120) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=periods)


def _make_dq(dates, codes, ret_arr: np.ndarray | None = None) -> pd.DataFrame:
    """
    构造 (trade_date, ts_code) MultiIndex 的 daily_quote 替代 DataFrame。

    ret_arr: shape (len(dates),)，为单只股票的日收益序列。
    """
    if ret_arr is None:
        ret_arr = np.full(len(dates), 0.001)

    rows = []
    for i, d in enumerate(dates):
        for code in codes:
            rows.append({
                "trade_date": d,
                "ts_code":    code,
                "ret":        float(ret_arr[i]),
                "open_adj":   10.0,
                "close_adj":  10.0 * (1 + float(ret_arr[i])),
                "amount":     1000.0,
            })
    return pd.DataFrame(rows).set_index(["trade_date", "ts_code"])


# ---------------------------------------------------------------------------
# _valid_dates_before
# ---------------------------------------------------------------------------

class TestValidDatesBefore:
    def test_excludes_future_dates(self, monkeypatch):
        all_dates = _business_days(periods=100)
        # monkeypatch 替换模块属性后，_valid_dates_before 内的 _all_trading_dates()
        # 会查到 lambda；无需 cache_clear（旧的缓存函数不再被引用）。
        monkeypatch.setattr(pf, "_all_trading_dates", lambda: all_dates)

        T = all_dates[60]
        result = _valid_dates_before(T)

        assert result.max() <= T, "result must not include any date after T"
        assert len(result) == 61  # dates[0..60] inclusive

    def test_includes_rebalance_date_itself(self, monkeypatch):
        all_dates = _business_days(periods=50)
        monkeypatch.setattr(pf, "_all_trading_dates", lambda: all_dates)

        T = all_dates[30]
        result = _valid_dates_before(T)

        assert T in result, "rebalance date T itself must be in the result"

    def test_empty_history_returns_empty(self, monkeypatch):
        all_dates = _business_days("2025-01-02", periods=5)
        monkeypatch.setattr(pf, "_all_trading_dates", lambda: all_dates)

        T = pd.Timestamp("2010-01-01")  # before all dates
        result = _valid_dates_before(T)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Time boundary: load_daily_quote end must not exceed T
# ---------------------------------------------------------------------------

class TestFactorTimeBoundary:
    """
    确认因子函数在调用 load_daily_quote 时 end_date <= rebalance_date。
    通过 monkeypatch 记录实际请求的 (start, end) 参数验证。
    """

    def _setup_mocks(self, monkeypatch, all_dates, codes, ret_arr=None):
        requested = []

        def mock_valid_before(rbd):
            return all_dates[all_dates <= rbd]

        def mock_load_dq(start, end, codes=None):
            requested.append(end)
            dates_used = all_dates[(all_dates >= start) & (all_dates <= end)]
            return _make_dq(dates_used, codes or ["000001.SZ"], ret_arr)

        monkeypatch.setattr(pf, "_valid_dates_before", mock_valid_before)
        monkeypatch.setattr(pf, "load_daily_quote", mock_load_dq)
        return requested

    def test_vol_60d_does_not_use_future(self, monkeypatch):
        all_dates = _business_days(periods=100)
        T = all_dates[80]
        requested = self._setup_mocks(monkeypatch, all_dates, ["000001.SZ"])

        factor_vol_60d(T, ["000001.SZ"])

        assert all(end <= T for end in requested), (
            f"factor_vol_60d loaded data beyond T={T.date()}: {[e.date() for e in requested]}"
        )

    def test_max_ret_does_not_use_future(self, monkeypatch):
        all_dates = _business_days(periods=60)
        T = all_dates[40]
        requested = self._setup_mocks(monkeypatch, all_dates, ["000001.SZ"])

        factor_max_ret(T, ["000001.SZ"])

        assert all(end <= T for end in requested)

    def test_amihud_does_not_use_future(self, monkeypatch):
        all_dates = _business_days(periods=50)
        T = all_dates[35]
        requested = self._setup_mocks(monkeypatch, all_dates, ["000001.SZ"])

        factor_amihud(T, ["000001.SZ"])

        assert all(end <= T for end in requested)


# ---------------------------------------------------------------------------
# F3-001: 量价因子使用后复权 ret，而非 pct_chg
#
# 设计思路：向 load_daily_quote mock 注入已知 ret 序列，
# 验证因子输出与基于该 ret 的预期值一致。
# 若因子误用了别的列（如 pct_chg），则输出与预期不符。
# ---------------------------------------------------------------------------

class TestFactorUsesAdjustedRet:
    def _setup_factor_mock(self, monkeypatch, all_dates, codes, ret_arr):
        def mock_valid_before(rbd):
            return all_dates[all_dates <= rbd]

        def mock_load_dq(start, end, codes=None):
            dates_used = all_dates[(all_dates >= start) & (all_dates <= end)]
            return _make_dq(dates_used, codes or ["000001.SZ"],
                            ret_arr[-len(dates_used):] if len(dates_used) <= len(ret_arr)
                            else ret_arr)

        monkeypatch.setattr(pf, "_valid_dates_before", mock_valid_before)
        monkeypatch.setattr(pf, "load_daily_quote", mock_load_dq)

    def test_vol_60d_matches_known_ret(self, monkeypatch):
        """
        vol_60d 应等于 dq['ret'] 的标准差。
        构造 60 个已知 ret 值，验证 vol_60d 输出一致。
        """
        all_dates = _business_days(periods=80)
        T = all_dates[70]
        codes = ["000001.SZ"]

        rng = np.random.default_rng(7)
        ret_arr = rng.normal(0, 0.01, 70)
        # 最后 60 个为窗口内数据（vol_60d 窗口 = 60）
        expected_vol = float(np.std(ret_arr[-60:], ddof=1))

        self._setup_factor_mock(monkeypatch, all_dates, codes, ret_arr)

        result = factor_vol_60d(T, codes)
        assert not result.empty
        assert abs(result.iloc[0] - expected_vol) < 0.002, (
            f"vol_60d={result.iloc[0]:.6f} should be ≈ {expected_vol:.6f} "
            f"(uses ret, not some other column)"
        )

    def test_max_ret_matches_known_ret(self, monkeypatch):
        """
        max_ret 应等于 dq['ret'] 在窗口内的最大值。
        """
        all_dates = _business_days(periods=40)
        T = all_dates[30]
        codes = ["000001.SZ"]

        rng = np.random.default_rng(8)
        ret_arr = np.abs(rng.normal(0, 0.01, 30))
        ret_arr[15] = 0.08  # 注入一个已知峰值
        # max_ret 窗口 = 20
        expected_max = float(ret_arr[-20:].max())

        self._setup_factor_mock(monkeypatch, all_dates, codes, ret_arr)

        result = factor_max_ret(T, codes)
        assert not result.empty
        assert abs(result.iloc[0] - expected_max) < 1e-8, (
            f"max_ret={result.iloc[0]:.6f} should be ≈ {expected_max:.6f}"
        )

    def test_amihud_matches_known_ret_and_amount(self, monkeypatch):
        """
        amihud = mean(|ret| / amount)，amount 单位千元。
        注入固定 ret 和 amount=1000，验证输出与预期一致。
        """
        all_dates = _business_days(periods=35)
        T = all_dates[25]
        codes = ["000001.SZ"]

        rng = np.random.default_rng(9)
        ret_arr = np.abs(rng.normal(0, 0.01, 25))  # 窗口 20 天
        expected_amihud = float(np.mean(ret_arr[-20:]) / 1000.0)

        self._setup_factor_mock(monkeypatch, all_dates, codes, ret_arr)

        result = factor_amihud(T, codes)
        assert not result.empty
        assert abs(result.iloc[0] - expected_amihud) < 1e-9, (
            f"amihud={result.iloc[0]:.10f} should be ≈ {expected_amihud:.10f}"
        )

    def test_vol_60d_differs_with_wrong_ret(self, monkeypatch):
        """
        反例：若 ret 中含有一个除权引起的大幅跳变（类似未复权 pct_chg），
        则 vol_60d 会显著偏大。
        验证正确 ret（平滑小波动）与含跳变 ret 计算的 vol_60d 确实不同。
        """
        all_dates = _business_days(periods=80)
        T = all_dates[70]
        codes = ["000001.SZ"]

        rng = np.random.default_rng(11)
        # 正确 ret：小幅波动
        correct_ret = rng.normal(0, 0.005, 70)
        # 错误 ret：在第 30 天插入 -0.15 的大跳变（模拟未复权 pct_chg）
        wrong_ret = correct_ret.copy()
        wrong_ret[30 - 1] = -0.15

        vol_correct = float(np.std(correct_ret[-60:], ddof=1))
        vol_wrong   = float(np.std(wrong_ret[-60:], ddof=1))

        # 两者应显著不同（验证测试的区分能力）
        assert abs(vol_correct - vol_wrong) > 0.01, (
            "correct and wrong ret should give significantly different vol_60d"
        )
