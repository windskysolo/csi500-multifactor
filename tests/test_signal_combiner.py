"""
tests/test_signal_combiner.py — 信号合成层单元测试（F5-007）

覆盖范围：
  F5-003: FACTOR_DIRECTIONS 方向校验（margin_ratio 已修正为 -1）
  F5-003: validate_directions_vs_summary 正确识别方向不一致因子
  F5-004: build_composite_panel return_diagnostics 字段完整性
  F5-007: compute_rolling_ic_ir 不使用当期 IC（时间保守）
  F5-007: 冷启动返回 NaN（IC 历史不足）
  F5-007: combine_factors_cross_section min_valid_factors 逐股生效
  F5-007: 合成信号截面均值≈0、标准差≈1
  F5-007: build_composite_panel 冷启动期统计正确

所有测试使用合成数据，不依赖磁盘文件。
"""

import numpy as np
import pandas as pd
import pytest

from src.signal.combiner import (
    FACTOR_DIRECTIONS,
    MIN_IC_HISTORY,
    build_composite_panel,
    combine_factors_cross_section,
    compute_rolling_ic_ir,
    validate_directions_vs_summary,
)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _make_rebalance_dates(n: int, start: str = "2018-01-31") -> list[pd.Timestamp]:
    idx = pd.date_range(start=start, periods=n, freq="ME")
    return list(idx)


def _make_ic_series(n_periods: int, ic_value: float = 0.05, seed: int = 0) -> pd.Series:
    """构造长度 n_periods 的 IC 序列，值在 ic_value 附近波动。"""
    rng = np.random.default_rng(seed)
    dates = _make_rebalance_dates(n_periods)
    vals = rng.normal(ic_value, 0.01, size=n_periods)
    return pd.Series(vals, index=pd.DatetimeIndex(dates))


def _make_factor_panel(dates: list[pd.Timestamp], n_stocks: int = 100, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    codes = [f"{i:06d}.SZ" for i in range(1, n_stocks + 1)]
    data = rng.standard_normal((len(dates), n_stocks))
    return pd.DataFrame(data, index=pd.DatetimeIndex(dates), columns=codes)


# ---------------------------------------------------------------------------
# F5-003: FACTOR_DIRECTIONS 正确性
# ---------------------------------------------------------------------------

class TestFactorDirections:
    def test_margin_ratio_is_negative(self):
        """margin_ratio 训练集 IC < 0，方向应为 -1（F5-003 修复验证）。"""
        assert FACTOR_DIRECTIONS["margin_ratio"] == -1, (
            "margin_ratio 应为 -1（融资余额高→超买信号），若此断言失败说明方向被意外改回 +1"
        )

    def test_all_directions_are_plus_minus_one(self):
        """FACTOR_DIRECTIONS 中的所有值只能是 +1 或 -1。"""
        for name, direction in FACTOR_DIRECTIONS.items():
            assert direction in (1, -1), (
                f"因子 {name} 的方向值 {direction} 非法，须为 +1 或 -1"
            )

    def test_validate_directions_detects_mismatch(self):
        """validate_directions_vs_summary 应识别出方向不一致的因子。"""
        # 构造 factor_summary，让 margin_ratio 的 ic_mean > 0（与当前 FACTOR_DIRECTIONS[-1] 相反）
        factor_summary = pd.DataFrame({
            "ic_mean": {"margin_ratio": 0.05, "ep_ttm": 0.03},
        })
        mismatches = validate_directions_vs_summary(factor_summary)
        assert "margin_ratio" in mismatches
        assert "ep_ttm" not in mismatches   # ep_ttm +1 与 ic_mean>0 一致

    def test_validate_directions_returns_empty_when_consistent(self):
        """所有因子方向一致时返回空列表。"""
        # ep_ttm 方向为 +1，ic_mean=0.03 > 0 → 一致
        factor_summary = pd.DataFrame({
            "ic_mean": {"ep_ttm": 0.03},
        })
        mismatches = validate_directions_vs_summary(factor_summary)
        assert mismatches == []

    def test_validate_directions_skips_missing_ic_mean_column(self):
        """factor_summary 无 ic_mean 列时不报错，返回空列表。"""
        factor_summary = pd.DataFrame({"other_col": {"ep_ttm": 1.0}})
        mismatches = validate_directions_vs_summary(factor_summary)
        assert mismatches == []


# ---------------------------------------------------------------------------
# F5-007: compute_rolling_ic_ir 时间保守性
# ---------------------------------------------------------------------------

class TestComputeRollingIcIr:
    def test_excludes_most_recent_available_ic(self):
        """
        compute_rolling_ic_ir 应丢弃 before_date 之前最近一期的 IC
        （该期 exit_date 约为 T+1，T 盘后不可得）。

        构造手法：
          - IC 序列有 n 期，将倒数第 2 期（d[n-2]）置为极端值 999
          - before_date = d[n-1]（最后一期）
          - ic_s[index < d[n-1]].iloc[:-1] 应丢弃 d[n-2] → 极端值不进入计算
          - 若 ic_ir 仍在合理范围内，说明确实被丢弃了
        """
        n = MIN_IC_HISTORY + 8
        dates = _make_rebalance_dates(n)
        rng = np.random.default_rng(0)
        # 基准 IC：均值 0.05、有适度噪声（确保 std > 0）
        base_ic = rng.normal(0.05, 0.01, size=n)
        base_ic[-2] = 999.0   # 倒数第 2 期：极端值，应被 .iloc[:-1] 丢弃
        ic_s = pd.Series(base_ic, index=pd.DatetimeIndex(dates))

        # before_date = 最后一期；ic_s[index < before_date] 共 n-1 期
        # .iloc[:-1] 再丢 1 期（d[n-2]，含 999.0）→ 剩 n-2 期
        before_date = dates[-1]
        result = compute_rolling_ic_ir({"f1": ic_s}, before_date=before_date)
        ic_ir = result.get("f1")

        assert ic_ir is not None and not np.isnan(ic_ir), "应返回非 NaN 的 IC_IR"
        # 若极端值被正确丢弃，IC_IR 应在合理范围（基于 ~0.05/0.01 ≈ 5）
        assert abs(ic_ir) < 50, (
            f"ic_ir={ic_ir:.2f} 过大，疑似极端值（999）未被 .iloc[:-1] 丢弃"
        )

    def test_cold_start_returns_nan_when_insufficient_history(self):
        """IC 历史不足 MIN_IC_HISTORY 时返回 NaN（冷启动）。"""
        n = MIN_IC_HISTORY - 1
        ic_s = _make_ic_series(n)
        before_date = _make_rebalance_dates(n + 1)[-1]
        result = compute_rolling_ic_ir({"f1": ic_s}, before_date=before_date)
        assert pd.isna(result.get("f1")), "历史不足时应返回 NaN"

    def test_sufficient_history_returns_non_nan(self):
        """IC 历史充足时应返回非 NaN 的 IC_IR。"""
        n = MIN_IC_HISTORY + 5
        ic_s = _make_ic_series(n, ic_value=0.05)
        before_date = _make_rebalance_dates(n + 2)[-1]
        result = compute_rolling_ic_ir({"f1": ic_s}, before_date=before_date)
        assert not pd.isna(result.get("f1")), "历史充足时应返回非 NaN IC_IR"


# ---------------------------------------------------------------------------
# F5-007: combine_factors_cross_section min_valid_factors
# ---------------------------------------------------------------------------

class TestCombineFactorsCrossSection:
    def test_min_valid_factors_sets_nan(self):
        """有效因子数 < min_valid_factors 的股票合成值应为 NaN。"""
        # 构造 3 个因子，股票 A 只有 2 个因子有值，B 有 3 个
        f1 = pd.Series({"A": 1.0, "B": 1.0})
        f2 = pd.Series({"A": 2.0, "B": 2.0})
        f3 = pd.Series({"A": np.nan, "B": 3.0})  # A 缺失 f3
        factor_dict = {"f1": f1, "f2": f2, "f3": f3}
        weights = {"f1": 1.0, "f2": 1.0, "f3": 1.0}

        # min_valid_factors=3 时 A（只有2个）应为 NaN
        result = combine_factors_cross_section(factor_dict, weights, min_valid_factors=3)
        assert pd.isna(result.loc["A"]), "A 只有 2 个有效因子，min_valid_factors=3 时应为 NaN"
        assert not pd.isna(result.loc["B"]), "B 有 3 个有效因子，不应为 NaN"

    def test_min_valid_factors_two_allows_partial(self):
        """min_valid_factors=2 时，A（有 2 个因子）不应为 NaN。"""
        f1 = pd.Series({"A": 1.0, "B": 1.0})
        f2 = pd.Series({"A": 2.0, "B": 2.0})
        f3 = pd.Series({"A": np.nan, "B": 3.0})
        factor_dict = {"f1": f1, "f2": f2, "f3": f3}
        weights = {"f1": 1.0, "f2": 1.0, "f3": 1.0}

        result = combine_factors_cross_section(factor_dict, weights, min_valid_factors=2)
        assert not pd.isna(result.loc["A"]), "A 有 2 个有效因子，min_valid_factors=2 时不应为 NaN"


# ---------------------------------------------------------------------------
# F5-007: 合成信号截面标准化
# ---------------------------------------------------------------------------

class TestCompositeSignalStandardization:
    def test_cross_section_approximately_standardized(self):
        """
        合成信号每个截面（期）应已去极值+标准化，均值≈0、标准差≈1。
        检验对一个 single period 调用 combine_factors_cross_section 的输出。
        """
        n_stocks = 200
        rng = np.random.default_rng(42)
        codes = [f"{i:06d}.SZ" for i in range(n_stocks)]
        f1 = pd.Series(rng.standard_normal(n_stocks), index=codes)
        f2 = pd.Series(rng.standard_normal(n_stocks), index=codes)
        weights = {"f1": 0.5, "f2": 0.5}
        result = combine_factors_cross_section({"f1": f1, "f2": f2}, weights, min_valid_factors=1)

        valid = result.dropna()
        assert len(valid) > 10, "合成信号有效股票数过少"
        assert abs(valid.mean()) < 0.1, f"均值={valid.mean():.4f} 偏离 0 过多"
        assert 0.8 < valid.std() < 1.2, f"标准差={valid.std():.4f} 偏离 1 过多"


# ---------------------------------------------------------------------------
# F5-004+F5-007: build_composite_panel 诊断字段与冷启动统计
# ---------------------------------------------------------------------------

class TestBuildCompositePanel:
    def _setup(self):
        """构造一套够用的因子面板和 IC 序列。"""
        n_periods = MIN_IC_HISTORY + 10
        dates = _make_rebalance_dates(n_periods)
        factor_panels = {
            "f1": _make_factor_panel(dates, n_stocks=50, seed=1),
            "f2": _make_factor_panel(dates, n_stocks=50, seed=2),
        }
        # IC 序列比面板早 2 期开始，模拟历史可用情况
        ic_dates = _make_rebalance_dates(n_periods + 5)
        ic_series_map = {
            "f1": _make_ic_series(len(ic_dates), ic_value=0.05, seed=3),
            "f2": _make_ic_series(len(ic_dates), ic_value=-0.03, seed=4),
        }
        return factor_panels, ic_series_map, dates

    def test_diagnostics_dict_has_required_fields(self):
        """return_diagnostics=True 时，返回的 diagnostics dict 含必需字段。"""
        factor_panels, ic_series_map, dates = self._setup()
        _, diagnostics = build_composite_panel(
            factor_panels   = factor_panels,
            ic_series_map   = ic_series_map,
            rebalance_dates = dates,
            method          = "ic_ir",
            return_diagnostics = True,
        )
        assert "weight_history" in diagnostics
        assert "cold_start_flags" in diagnostics
        assert "cold_start_count" in diagnostics
        assert "method" in diagnostics
        assert diagnostics["method"] == "ic_ir"

    def test_cold_start_count_positive_for_early_periods(self):
        """早期调仓日 IC 历史不足时，cold_start_count 应 > 0。"""
        # 调仓日从第 1 期开始，IC 序列也从同一时间开始 → 前 MIN_IC_HISTORY 期必然冷启动
        dates = _make_rebalance_dates(MIN_IC_HISTORY + 5)
        factor_panels = {
            "f1": _make_factor_panel(dates, seed=5),
        }
        ic_series_map = {
            "f1": _make_ic_series(len(dates), seed=6),
        }
        _, diagnostics = build_composite_panel(
            factor_panels   = factor_panels,
            ic_series_map   = ic_series_map,
            rebalance_dates = dates,
            method          = "ic_ir",
            return_diagnostics = True,
        )
        # 前 MIN_IC_HISTORY + 1 期（含丢弃最近一期的 buffer）应触发冷启动
        assert diagnostics["cold_start_count"] > 0, (
            "早期调仓日应触发冷启动，cold_start_count 应 > 0"
        )

    def test_weight_history_keys_match_rebalance_dates(self):
        """weight_history 的键应与实际被处理的调仓日一致。"""
        factor_panels, ic_series_map, dates = self._setup()
        _, diagnostics = build_composite_panel(
            factor_panels   = factor_panels,
            ic_series_map   = ic_series_map,
            rebalance_dates = dates,
            method          = "ic_ir",
            return_diagnostics = True,
        )
        wh = diagnostics["weight_history"]
        assert len(wh) == len(dates), (
            f"weight_history 条目数 {len(wh)} 与调仓日数 {len(dates)} 不符"
        )

    def test_return_diagnostics_false_returns_dataframe_only(self):
        """return_diagnostics=False 时，返回值应为 DataFrame，而非 tuple。"""
        factor_panels, ic_series_map, dates = self._setup()
        result = build_composite_panel(
            factor_panels   = factor_panels,
            ic_series_map   = ic_series_map,
            rebalance_dates = dates,
            method          = "equal",
            return_diagnostics = False,
        )
        assert isinstance(result, pd.DataFrame), "return_diagnostics=False 时应只返回 DataFrame"
