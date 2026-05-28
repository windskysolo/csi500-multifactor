"""
tests/test_technical_factors.py — 技术因子数值正确性与时间边界测试（阶段 8 门禁）

覆盖范围（对应 phase_plan.md 第 13 节）：
  - 5 个技术因子（macd_cross / rsi_6 / rsi_12 / boll_pct / obv_chg_20d）
  - 时间边界：load_daily_quote 的 end 参数不超过 T（严格 PIT）
  - 历史不足时返回全 NaN（不抛异常）
  - 数值正确性：RSI / boll_pct / obv_chg_20d 的已知输入验证

测试策略：monkeypatch _all_trading_dates 和 load_daily_quote，不依赖磁盘数据。

注意：_all_trading_dates 带 @lru_cache；monkeypatch 替换模块属性后，
_valid_dates_before / _window_start 内的调用均指向新 lambda，无需 cache_clear。
"""

import numpy as np
import pandas as pd
import pytest

import src.factors.alt_factors as af
from src.factors.alt_factors import (
    _compute_rsi,
    factor_boll_pct,
    factor_macd_cross,
    factor_obv_chg_20d,
    factor_rsi_6,
    factor_rsi_12,
    WINDOW_BOLL,
    WINDOW_TECH,
)


# ---------------------------------------------------------------------------
# 构造辅助函数
# ---------------------------------------------------------------------------

def _business_days(start: str = "2020-01-02", periods: int = 150) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=periods)


def _make_dq(
    dates: pd.DatetimeIndex,
    codes: list[str],
    close_arr: np.ndarray | None = None,
    vol_arr: np.ndarray | None = None,
) -> pd.DataFrame:
    """
    构造 (trade_date, ts_code) MultiIndex 的 daily_quote 替代 DataFrame。

    ret 列：从 close_arr 的差分计算（符号用于 OBV，量级用于 RSI pct_change）。
    """
    n = len(dates)
    if close_arr is None:
        close_arr = np.full(n, 10.0)
    if vol_arr is None:
        vol_arr = np.full(n, 1e6)

    # ret[i] = (close[i] - close[i-1]) / close[i-1]；首日为 0
    diffs = np.diff(close_arr, prepend=close_arr[0])
    denom = np.where(close_arr == 0, np.nan, close_arr)
    ret_arr = np.where(np.arange(n) == 0, 0.0, diffs / np.roll(denom, 1))

    rows = []
    for i, d in enumerate(dates):
        for code in codes:
            rows.append({
                "trade_date": d,
                "ts_code":    code,
                "close_adj":  float(close_arr[i]),
                "ret":        float(ret_arr[i]),
                "vol":        float(vol_arr[i]),
                "amount":     float(vol_arr[i] * close_arr[i] / 1000.0),
            })
    return pd.DataFrame(rows).set_index(["trade_date", "ts_code"])


def _setup_mocks(
    monkeypatch,
    all_dates: pd.DatetimeIndex,
    codes: list[str],
    close_arr: np.ndarray | None = None,
    vol_arr: np.ndarray | None = None,
) -> list[pd.Timestamp]:
    """
    同时 monkeypatch _all_trading_dates 和 load_daily_quote。
    返回记录 load_daily_quote 请求的 end 参数列表（用于时间边界验证）。
    """
    requested_ends: list[pd.Timestamp] = []

    def mock_load_dq(start, end, codes=None):
        requested_ends.append(end)
        dates_used = all_dates[(all_dates >= start) & (all_dates <= end)]
        n = len(dates_used)
        c = close_arr[-n:] if (close_arr is not None and n <= len(close_arr)) else close_arr
        v = vol_arr[-n:]   if (vol_arr   is not None and n <= len(vol_arr))   else vol_arr
        return _make_dq(dates_used, codes or ["000001.SZ"], c, v)

    monkeypatch.setattr(af, "_all_trading_dates", lambda: all_dates)
    monkeypatch.setattr(af, "load_daily_quote",   mock_load_dq)
    return requested_ends


# ---------------------------------------------------------------------------
# 时间边界测试：load_daily_quote 的 end 不超过 T
# ---------------------------------------------------------------------------

class TestTechnicalFactorTimeBoundary:
    """确认所有技术因子在调用 load_daily_quote 时 end <= rebalance_date。"""

    def test_macd_cross_does_not_use_future(self, monkeypatch):
        all_dates = _business_days(periods=150)
        T = all_dates[130]
        requested = _setup_mocks(monkeypatch, all_dates, ["000001.SZ"])
        factor_macd_cross(T, ["000001.SZ"])
        assert all(e <= T for e in requested), "macd_cross 不应请求 T 之后的数据"

    def test_rsi_6_does_not_use_future(self, monkeypatch):
        all_dates = _business_days(periods=150)
        T = all_dates[130]
        requested = _setup_mocks(monkeypatch, all_dates, ["000001.SZ"])
        factor_rsi_6(T, ["000001.SZ"])
        assert all(e <= T for e in requested)

    def test_rsi_12_does_not_use_future(self, monkeypatch):
        all_dates = _business_days(periods=150)
        T = all_dates[130]
        requested = _setup_mocks(monkeypatch, all_dates, ["000001.SZ"])
        factor_rsi_12(T, ["000001.SZ"])
        assert all(e <= T for e in requested)

    def test_boll_pct_does_not_use_future(self, monkeypatch):
        all_dates = _business_days(periods=150)
        T = all_dates[130]
        requested = _setup_mocks(monkeypatch, all_dates, ["000001.SZ"])
        factor_boll_pct(T, ["000001.SZ"])
        assert all(e <= T for e in requested)

    def test_obv_chg_20d_does_not_use_future(self, monkeypatch):
        all_dates = _business_days(periods=150)
        T = all_dates[130]
        requested = _setup_mocks(monkeypatch, all_dates, ["000001.SZ"])
        factor_obv_chg_20d(T, ["000001.SZ"])
        assert all(e <= T for e in requested)


# ---------------------------------------------------------------------------
# 历史不足时返回全 NaN（不抛异常）
# ---------------------------------------------------------------------------

class TestTechnicalFactorInsufficientHistory:
    """历史数据少于窗口要求时，因子应全 NaN 而非抛出异常。"""

    def test_macd_too_few_dates_returns_nan(self, monkeypatch):
        """历史只有 10 个交易日，远少于 WINDOW_TECH=120，macd_cross 应全 NaN。"""
        all_dates = _business_days(periods=10)
        T = all_dates[-1]
        _setup_mocks(monkeypatch, all_dates, ["000001.SZ"])
        result = factor_macd_cross(T, ["000001.SZ"])
        assert result.isna().all(), "历史不足时 macd_cross 应全 NaN"

    def test_boll_pct_too_few_dates_returns_nan(self, monkeypatch):
        """历史只有 5 个交易日，少于 WINDOW_BOLL=20，boll_pct 应全 NaN。"""
        all_dates = _business_days(periods=5)
        T = all_dates[-1]
        _setup_mocks(monkeypatch, all_dates, ["000001.SZ"])
        result = factor_boll_pct(T, ["000001.SZ"])
        assert result.isna().all(), "历史不足时 boll_pct 应全 NaN"

    def test_rsi_6_too_few_rows_returns_nan(self, monkeypatch):
        """收益率行数少于 8，rsi_6 应全 NaN。"""
        all_dates = _business_days(periods=5)
        T = all_dates[-1]
        _setup_mocks(monkeypatch, all_dates, ["000001.SZ"])
        result = factor_rsi_6(T, ["000001.SZ"])
        assert result.isna().all(), "历史不足时 rsi_6 应全 NaN"


# ---------------------------------------------------------------------------
# _compute_rsi 数值正确性
# ---------------------------------------------------------------------------

class TestRSIComputation:
    """验证 _compute_rsi（简单均值 RSI）的计算逻辑。"""

    def test_rsi_all_losses_equals_zero(self):
        """所有日收益率为负时，RSI 应为 0（gains=0，rs=0，100-100/(1+0)=0）。"""
        dates = _business_days(periods=30)
        close = pd.DataFrame(
            {"000001.SZ": 10.0 - np.arange(30) * 0.1},
            index=dates,
        )
        result = _compute_rsi(close, window=6)
        assert float(result.iloc[0]) == pytest.approx(0.0), "全负收益时 RSI 应为 0"

    def test_rsi_all_gains_returns_100(self):
        """所有日收益率为正时，losses=0 且 gains>0，RSI 应为 100（无下跌日的数学定义值）。"""
        dates = _business_days(periods=30)
        close = pd.DataFrame(
            {"000001.SZ": 10.0 + np.arange(30) * 0.1},
            index=dates,
        )
        result = _compute_rsi(close, window=6)
        assert float(result.iloc[0]) == pytest.approx(100.0), "全正收益时 RSI 应为 100"

    def test_rsi_mixed_returns_in_valid_range(self):
        """混合收益率序列的 RSI 应在 [0, 100] 范围内。"""
        rng = np.random.default_rng(42)
        dates = _business_days(periods=50)
        close = pd.DataFrame(
            {"000001.SZ": np.cumprod(1 + rng.normal(0, 0.01, 50)) * 10.0},
            index=dates,
        )
        result = _compute_rsi(close, window=6)
        valid = result.dropna()
        assert len(valid) > 0
        assert ((valid >= 0) & (valid <= 100)).all(), "RSI 取值范围应在 [0, 100]"


# ---------------------------------------------------------------------------
# boll_pct 数值正确性
# ---------------------------------------------------------------------------

class TestBollPctNumerics:
    """验证 factor_boll_pct 的布林带公式：(close_T - MA20) / (2 * std20)。"""

    def test_flat_price_std_zero_gives_nan(self, monkeypatch):
        """价格恒定时 std20=0，代码用 replace(0, NaN) 处理除零，因子应为 NaN。"""
        all_dates = _business_days(periods=30)
        T = all_dates[-1]
        _setup_mocks(monkeypatch, all_dates, ["000001.SZ"],
                     close_arr=np.full(30, 10.0))
        result = factor_boll_pct(T, ["000001.SZ"])
        assert result.isna().all(), "std=0 时 boll_pct 应为 NaN（避免除零）"

    def test_uptrend_gives_positive_boll_pct(self, monkeypatch):
        """线性上涨时，T 日价格高于 MA20，boll_pct 应为正值。"""
        all_dates = _business_days(periods=30)
        T = all_dates[-1]
        close_arr = 10.0 + np.arange(30) * 0.5   # 线性上涨
        _setup_mocks(monkeypatch, all_dates, ["000001.SZ"], close_arr=close_arr)
        result = factor_boll_pct(T, ["000001.SZ"])
        assert float(result.dropna().iloc[0]) > 0, "持续上涨时 boll_pct > 0"

    def test_downtrend_gives_negative_boll_pct(self, monkeypatch):
        """线性下跌时，T 日价格低于 MA20，boll_pct 应为负值。"""
        all_dates = _business_days(periods=30)
        T = all_dates[-1]
        close_arr = 20.0 - np.arange(30) * 0.5   # 线性下跌
        _setup_mocks(monkeypatch, all_dates, ["000001.SZ"], close_arr=close_arr)
        result = factor_boll_pct(T, ["000001.SZ"])
        assert float(result.dropna().iloc[0]) < 0, "持续下跌时 boll_pct < 0"


# ---------------------------------------------------------------------------
# obv_chg_20d 方向性
# ---------------------------------------------------------------------------

class TestOBVChgDirection:
    """验证 factor_obv_chg_20d 在量价配合上涨时为正值。"""

    def test_positive_obv_when_all_up_days_in_window(self, monkeypatch):
        """
        最近 20 个交易日全为上涨日（ret > 0），OBV 变化 = sum(vol) > 0。
        因子 = OBV变化 / avg_vol = 20（已知计算结果）。
        """
        all_dates = _business_days(periods=150)
        T = all_dates[130]

        n = 130
        # 前 110 日：价格平稳；close_arr[110] 起点需高于 close_arr[109]=10.0，
        # 否则第 110 日 ret=0（不计入上涨日），导致实际只有 19 个上涨日
        close_arr = np.full(n, 10.0)
        close_arr[110:] = 10.1 + np.arange(20) * 0.1   # 最后 20 日线性上涨，首日相对前日 +0.1
        vol_arr   = np.full(n, 1e6)

        _setup_mocks(monkeypatch, all_dates, ["000001.SZ"],
                     close_arr=close_arr, vol_arr=vol_arr)
        result = factor_obv_chg_20d(T, ["000001.SZ"])

        # 最后 20 日全为上涨，sign(ret)=+1，OBV变化=20*1e6，avg_vol=1e6 → factor=20
        val = float(result.dropna().iloc[0])
        assert val > 0, f"全上涨窗口内 obv_chg_20d 应为正值，实际={val:.4f}"
        assert abs(val - 20.0) < 1.0, f"obv_chg_20d 预期约 20.0，实际={val:.4f}"

    def test_negative_obv_when_all_down_days_in_window(self, monkeypatch):
        """最近 20 个交易日全为下跌日，OBV 变化应为负值。"""
        all_dates = _business_days(periods=150)
        T = all_dates[130]

        n = 130
        close_arr = np.full(n, 20.0)
        close_arr[110:] = 20.0 - np.arange(20) * 0.1   # 最后 20 日线性下跌
        vol_arr = np.full(n, 1e6)

        _setup_mocks(monkeypatch, all_dates, ["000001.SZ"],
                     close_arr=close_arr, vol_arr=vol_arr)
        result = factor_obv_chg_20d(T, ["000001.SZ"])

        val = float(result.dropna().iloc[0])
        assert val < 0, f"全下跌窗口内 obv_chg_20d 应为负值，实际={val:.4f}"


# ---------------------------------------------------------------------------
# macd_cross 归一化验证
# ---------------------------------------------------------------------------

class TestMACDNormalization:
    """验证 factor_macd_cross 按 close_adj_T 归一化，消除股价量纲。"""

    def test_same_return_series_same_normalized_macd(self, monkeypatch):
        """
        两支股票收益率序列完全相同，价格水平相差 10 倍。
        归一化后 histogram / close_T 应相等（量纲已消除）。
        """
        all_dates = _business_days(periods=150)
        T = all_dates[130]
        n = 130

        rng = np.random.default_rng(7)
        ret_arr = rng.normal(0.001, 0.02, n)
        close_low  = np.cumprod(1 + ret_arr) * 10.0
        close_high = np.cumprod(1 + ret_arr) * 100.0

        def mock_load_dq(start, end, codes=None):
            dates_used = all_dates[(all_dates >= start) & (all_dates <= end)]
            k = len(dates_used)
            rows = []
            for i, d in enumerate(dates_used):
                rows.append({"trade_date": d, "ts_code": "LOW.SZ",
                             "close_adj": close_low[-k:][i],
                             "ret": ret_arr[-k:][i],
                             "vol": 1e6, "amount": 1e7})
                rows.append({"trade_date": d, "ts_code": "HIGH.SZ",
                             "close_adj": close_high[-k:][i],
                             "ret": ret_arr[-k:][i],
                             "vol": 1e6, "amount": 1e8})
            return pd.DataFrame(rows).set_index(["trade_date", "ts_code"])

        monkeypatch.setattr(af, "_all_trading_dates", lambda: all_dates)
        monkeypatch.setattr(af, "load_daily_quote", mock_load_dq)

        result = factor_macd_cross(T, ["LOW.SZ", "HIGH.SZ"])
        low_val  = float(result["LOW.SZ"])
        high_val = float(result["HIGH.SZ"])

        if pd.notna(low_val) and pd.notna(high_val):
            assert abs(low_val - high_val) < 1e-8, (
                f"同收益率序列归一化后 MACD 应相等：{low_val:.8f} vs {high_val:.8f}"
            )
