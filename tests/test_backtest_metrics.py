"""
tests/test_backtest_metrics.py — 回测指标单元测试

覆盖：
  - max_drawdown / excess_max_drawdown 极端 NAV 路径
  - annualized_return（不同时间长度）
  - information_ratio（跟踪误差为 0 时不崩溃）
  - tracking_error / monthly_win_rate 边界
  - summarize 集成验证
"""

import pytest
import numpy as np
import pandas as pd

from src.backtest.metrics import (
    annualized_return,
    annualized_vol,
    daily_returns,
    excess_max_drawdown,
    excess_nav,
    information_ratio,
    max_drawdown,
    monthly_win_rate,
    summarize,
    tracking_error,
    TRADING_DAYS_PER_YEAR,
)


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def _make_nav(values: list[float], start: str = "2022-01-04") -> pd.Series:
    """用给定值列表构造日频 NAV（工作日索引）。"""
    dates = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=dates, dtype=float)


def _flat_nav(n: int, start: float = 1.0) -> pd.Series:
    """返回全为 start 的 n 天 NAV。"""
    return _make_nav([start] * n)


def _linear_nav(n: int, daily_ret: float = 0.001) -> pd.Series:
    """按固定日收益率线性复利构造 NAV。"""
    values = [1.0 * ((1 + daily_ret) ** i) for i in range(n)]
    return _make_nav(values)


# ─── max_drawdown ────────────────────────────────────────────────────────────

class TestMaxDrawdown:
    """max_drawdown 应返回非正数，极端场景不崩溃。"""

    def test_flat_nav_has_zero_drawdown(self):
        nav = _flat_nav(252)
        assert max_drawdown(nav) == pytest.approx(0.0, abs=1e-10)

    def test_monotone_rising_has_zero_drawdown(self):
        nav = _linear_nav(252, daily_ret=0.001)
        assert max_drawdown(nav) == pytest.approx(0.0, abs=1e-10)

    def test_one_step_drop(self):
        """从 1.0 跌到 0.8 再恢复：最大回撤 = -20%。"""
        values = [1.0, 1.0, 0.8, 0.9, 1.0]
        nav = _make_nav(values)
        assert max_drawdown(nav) == pytest.approx(-0.2, abs=1e-9)

    def test_nav_drops_to_half(self):
        """从 1.0 跌到 0.5：最大回撤 = -50%。"""
        values = [1.0] * 50 + [0.5] * 50
        nav = _make_nav(values)
        assert max_drawdown(nav) == pytest.approx(-0.5, abs=1e-9)

    def test_nav_collapses_to_near_zero(self):
        """极端情形：NAV 接近归零，最大回撤接近 -100%。"""
        values = [1.0] * 10 + [0.01] * 10
        nav = _make_nav(values)
        mdd = max_drawdown(nav)
        assert mdd < -0.98, "NAV 跌至 0.01 时最大回撤应接近 -100%"
        assert mdd >= -1.0, "最大回撤不得低于 -100%"

    def test_multiple_peaks_uses_deepest(self):
        """多个局部峰值时，应取最深的那次回撤。"""
        # 第一次峰 1.0→0.9（-10%），第二次峰 1.2→0.9（-25%）
        values = [1.0, 0.9, 0.95, 1.2, 0.9]
        nav = _make_nav(values)
        mdd = max_drawdown(nav)
        assert mdd == pytest.approx(-0.25, abs=1e-6)

    def test_returns_negative_number(self):
        """max_drawdown 必须返回非正数。"""
        nav = _linear_nav(100, daily_ret=-0.002)
        assert max_drawdown(nav) <= 0.0


# ─── excess_max_drawdown ─────────────────────────────────────────────────────

class TestExcessMaxDrawdown:
    """超额净值的最大回撤。"""

    def test_identical_nav_has_zero_excess_drawdown(self):
        """策略与基准完全一致时，超额最大回撤为 0。"""
        nav   = _linear_nav(252, daily_ret=0.0005)
        bench = nav.copy()
        assert excess_max_drawdown(nav, bench) == pytest.approx(0.0, abs=1e-10)

    def test_strategy_consistently_outperforms(self):
        """策略持续跑赢基准时，超额净值单调上涨，超额回撤为 0。"""
        nav   = _linear_nav(252, daily_ret=0.0015)
        bench = _linear_nav(252, daily_ret=0.0005)
        assert excess_max_drawdown(nav, bench) == pytest.approx(0.0, abs=1e-10)

    def test_strategy_loses_excess_and_recovers(self):
        """策略先跑赢后跑输再恢复：超额回撤应为负数。"""
        n = 60
        nav_values   = [1.0 * 1.001 ** i for i in range(n // 2)] + \
                       [1.0 * 1.001 ** (n // 2 - 1) * 0.99 ** (i + 1) for i in range(n // 2)]
        bench_values = [1.0 * 1.0005 ** i for i in range(n)]
        nav   = _make_nav(nav_values)
        bench = _make_nav(bench_values)
        emd = excess_max_drawdown(nav, bench)
        assert emd < 0.0, "策略后半段跑输时超额最大回撤应为负"

    def test_excess_drawdown_le_abs_strategy_drawdown(self):
        """超额回撤绝对值 ≤ 策略回撤绝对值（基准正向时）。"""
        nav   = _make_nav([1.0, 1.0, 0.8, 0.9])
        bench = _make_nav([1.0, 1.0, 1.0, 1.0])
        emd = excess_max_drawdown(nav, bench)
        mdd = max_drawdown(nav)
        assert abs(emd) <= abs(mdd) + 1e-9


# ─── annualized_return ───────────────────────────────────────────────────────

class TestAnnualizedReturn:
    """年化收益率应从累计净值几何复利还原。"""

    def test_one_year_up_10pct(self):
        """
        253 点（252 个交易日区间）、终值 1.10：年化约 10%。
        允许 1% 相对误差，因 annualized_return 用 len(nav)/252 计算年数（253/252≈1.004）。
        """
        nav = _make_nav([1.0 * (1.10 ** (i / 252)) for i in range(253)])
        assert annualized_return(nav) == pytest.approx(0.10, rel=0.01)

    def test_flat_nav_zero_return(self):
        nav = _flat_nav(252)
        assert annualized_return(nav) == pytest.approx(0.0, abs=1e-9)

    def test_two_day_nav(self):
        """仅 2 天数据也不应崩溃。"""
        nav = _make_nav([1.0, 1.10])
        ret = annualized_return(nav)
        assert isinstance(ret, float)
        assert ret > 0

    def test_negative_return(self):
        """下跌情形返回负值。"""
        nav = _make_nav([1.0, 0.9])
        assert annualized_return(nav) < 0


# ─── information_ratio ───────────────────────────────────────────────────────

class TestInformationRatio:
    """IR = 年化超额 / TE；TE=0 时返回 0.0，不抛异常。"""

    def test_identical_nav_returns_zero(self):
        """策略与基准一致 → TE=0 → IR=0 (不 ZeroDivisionError)。"""
        nav   = _linear_nav(252, daily_ret=0.001)
        bench = nav.copy()
        assert information_ratio(nav, bench) == pytest.approx(0.0, abs=1e-9)

    def test_positive_excess_positive_ir(self):
        """
        策略日均收益高于基准且有独立噪声（TE>0）时，IR 应为正。
        使用 nav 和 bench 独立随机路径，确保超额收益有方差。
        """
        rng = np.random.default_rng(42)
        dates = pd.bdate_range("2022-01-04", periods=252)
        bench_rets = rng.normal(0.0000, 0.01, 252)
        nav_rets   = rng.normal(0.0020, 0.01, 252)   # 日均超额 +20bps
        nav   = pd.Series((1 + nav_rets).cumprod(), index=dates)
        bench = pd.Series((1 + bench_rets).cumprod(), index=dates)
        ir = information_ratio(nav, bench)
        assert ir > 0, f"策略跑赢时 IR 应为正，实际 {ir:.4f}"

    def test_negative_excess_negative_ir(self):
        """
        策略日均收益低于基准且有独立噪声（TE>0）时，IR 应为负。
        """
        rng = np.random.default_rng(123)
        dates = pd.bdate_range("2022-01-04", periods=252)
        bench_rets = rng.normal(0.0020, 0.01, 252)   # 日均 +20bps
        nav_rets   = rng.normal(0.0000, 0.01, 252)   # 日均 0bps
        nav   = pd.Series((1 + nav_rets).cumprod(), index=dates)
        bench = pd.Series((1 + bench_rets).cumprod(), index=dates)
        ir = information_ratio(nav, bench)
        assert ir < 0, f"策略跑输时 IR 应为负，实际 {ir:.4f}"


# ─── tracking_error ──────────────────────────────────────────────────────────

class TestTrackingError:
    """TE = 超额日收益标准差 × √252。"""

    def test_identical_nav_zero_te(self):
        nav   = _linear_nav(252, daily_ret=0.001)
        bench = nav.copy()
        assert tracking_error(nav, bench) == pytest.approx(0.0, abs=1e-10)

    def test_te_non_negative(self):
        nav   = _linear_nav(252, daily_ret=0.001)
        bench = _linear_nav(252, daily_ret=0.0005)
        assert tracking_error(nav, bench) >= 0.0

    def test_te_scales_with_daily_noise(self):
        """每日额外波动越大，TE 应越大。"""
        rng = np.random.default_rng(42)
        dates = pd.bdate_range("2022-01-04", periods=252)
        bench_rets = pd.Series(0.0, index=dates)
        nav_high_te = pd.Series((1 + bench_rets + rng.normal(0, 0.02, 252)).cumprod(), index=dates)
        nav_low_te  = pd.Series((1 + bench_rets + rng.normal(0, 0.005, 252)).cumprod(), index=dates)
        bench       = pd.Series((1 + bench_rets).cumprod(), index=dates)
        te_high = tracking_error(nav_high_te, bench)
        te_low  = tracking_error(nav_low_te, bench)
        assert te_high > te_low


# ─── monthly_win_rate ────────────────────────────────────────────────────────

class TestMonthlyWinRate:
    """月胜率：月月超额为正 → 1.0；月月跑输 → 0.0。"""

    def test_always_beat_bench(self):
        """策略每日严格跑赢基准，月胜率应为 1.0。"""
        nav   = _linear_nav(252, daily_ret=0.002)
        bench = _linear_nav(252, daily_ret=0.001)
        assert monthly_win_rate(nav, bench) == pytest.approx(1.0, abs=1e-9)

    def test_never_beat_bench(self):
        """策略每日严格跑输，月胜率应为 0.0。"""
        nav   = _linear_nav(252, daily_ret=0.001)
        bench = _linear_nav(252, daily_ret=0.002)
        assert monthly_win_rate(nav, bench) == pytest.approx(0.0, abs=1e-9)

    def test_win_rate_between_0_and_1(self):
        """混合超跌场景月胜率应在 [0, 1] 区间。"""
        rng = np.random.default_rng(99)
        dates = pd.bdate_range("2022-01-04", periods=252)
        rets = rng.normal(0, 0.01, 252)
        nav   = pd.Series((1 + rets).cumprod(), index=dates)
        bench = pd.Series(np.ones(252), index=dates)
        rate = monthly_win_rate(nav, bench)
        assert 0.0 <= rate <= 1.0


# ─── summarize 集成 ──────────────────────────────────────────────────────────

class TestSummarize:
    """summarize 返回全部预期键，数值类型和符号正确。"""

    EXPECTED_KEYS = {
        "annualized_return", "annualized_vol", "sharpe", "max_drawdown",
        "calmar_ratio", "benchmark_return", "excess_return",
        "tracking_error", "information_ratio", "excess_max_drawdown",
        "monthly_win_rate",
    }

    def test_keys_complete(self):
        nav   = _linear_nav(252, daily_ret=0.001)
        bench = _linear_nav(252, daily_ret=0.0008)
        result = summarize(nav, bench)
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_negative_drawdown_keys(self):
        """max_drawdown 和 excess_max_drawdown 必须为非正数。"""
        nav   = _make_nav([1.0, 1.1, 0.9, 1.05])
        bench = _make_nav([1.0, 1.0, 1.0, 1.0])
        result = summarize(nav, bench)
        assert result["max_drawdown"] <= 0.0
        assert result["excess_max_drawdown"] <= 0.0

    def test_vol_non_negative(self):
        nav   = _linear_nav(252, daily_ret=0.001)
        bench = _linear_nav(252, daily_ret=0.0005)
        result = summarize(nav, bench)
        assert result["annualized_vol"] >= 0.0
        assert result["tracking_error"] >= 0.0

    def test_monthly_win_rate_in_range(self):
        nav   = _linear_nav(252, daily_ret=0.001)
        bench = _linear_nav(252, daily_ret=0.0005)
        result = summarize(nav, bench)
        assert 0.0 <= result["monthly_win_rate"] <= 1.0

    def test_identical_nav_zero_excess_and_ir(self):
        """策略与基准相同时，超额收益=0，IR=0，超额回撤=0。"""
        nav   = _linear_nav(252, daily_ret=0.001)
        bench = nav.copy()
        result = summarize(nav, bench)
        assert result["excess_return"] == pytest.approx(0.0, abs=1e-9)
        assert result["information_ratio"] == pytest.approx(0.0, abs=1e-9)
        assert result["excess_max_drawdown"] == pytest.approx(0.0, abs=1e-9)

    def test_all_values_are_float(self):
        nav   = _linear_nav(100, daily_ret=0.001)
        bench = _linear_nav(100, daily_ret=0.0008)
        result = summarize(nav, bench)
        for key, val in result.items():
            assert isinstance(val, float), f"{key} 应为 float，实际为 {type(val)}"
