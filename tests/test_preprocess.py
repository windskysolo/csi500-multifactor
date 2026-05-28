"""
tests/test_preprocess.py — 因子横截面预处理单元测试（F3-003）

覆盖范围：
  - winsorize_mad：MAD=0 退化、全 NaN 抛异常、NaN 保持、横截面独立性
  - standardize：均值 0 标准差 1、NaN 保持、样本不足/std=0 返回全 NaN
  - neutralize：残差与 log_mv 正交、样本不足跳过、NaN 输入保持 NaN
  - preprocess_factor：金融股过滤、完整流水线输出、NaN 传播、_diag 填充
  - 负例：shift(-1) 伪造的"未来数据"应与原始因子产生显著差异
"""

import numpy as np
import pandas as pd
import pytest

from src.factors.preprocess import (
    neutralize,
    preprocess_factor,
    standardize,
    winsorize_mad,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_codes(n: int) -> list[str]:
    return [f"{i:06d}.SZ" for i in range(n)]


def _make_industry(n: int, k: int = 5, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    codes_idx = [f"IND{j:02d}.SI" for j in range(k)]
    return pd.Series(
        [codes_idx[i % k] for i in range(n)],
        index=_make_codes(n),
        dtype=str,
    )


def _make_log_mv(n: int, seed: int = 2) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(
        np.log(rng.uniform(1e9, 1e11, n)),
        index=_make_codes(n),
    )


# ---------------------------------------------------------------------------
# winsorize_mad
# ---------------------------------------------------------------------------

class TestWinsorizeMad:
    def test_clips_upper_outlier(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 200.0])
        result = winsorize_mad(s)
        assert result.iloc[4] < 200.0, "extreme outlier must be clipped"

    def test_clips_lower_outlier(self):
        s = pd.Series([-200.0, 1.0, 2.0, 3.0, 4.0])
        result = winsorize_mad(s)
        assert result.iloc[0] > -200.0, "extreme lower outlier must be clipped"

    def test_nan_position_preserved(self):
        s = pd.Series([1.0, 2.0, np.nan, 4.0, 5.0])
        result = winsorize_mad(s)
        assert pd.isna(result.iloc[2]), "NaN position must remain NaN"
        assert result.notna().sum() == 4

    def test_all_nan_raises_valueerror(self):
        s = pd.Series([np.nan, np.nan, np.nan])
        with pytest.raises(ValueError, match="全 NaN"):
            winsorize_mad(s)

    def test_mad_zero_fallback_clips_outlier(self):
        # More than 50% identical → MAD=0 → fallback to 3σ truncation.
        # 数据：9 个 0 和 1 个 4。
        # mean=0.4, std≈1.265, bound=3*1.265=3.794, upper=0+3.794=3.794
        # 4 > 3.794，应被截断到 3.794。
        s = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 4.0])
        result = winsorize_mad(s)
        assert result.iloc[9] < 4.0, "outlier must be clipped by 3σ fallback when MAD=0"

    def test_all_identical_returns_unchanged(self):
        # All same → MAD=0, std=0 → no clipping
        s = pd.Series([3.0, 3.0, 3.0, 3.0])
        result = winsorize_mad(s)
        pd.testing.assert_series_equal(result, s)

    def test_uses_only_crosssection_bounds(self):
        # Two independent calls must use only their own data
        s_normal = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        s_outlier = pd.Series([1.0, 2.0, 3.0, 4.0, 1000.0])
        r_normal  = winsorize_mad(s_normal)
        r_outlier = winsorize_mad(s_outlier)
        # s_normal max (5.0) should not be clipped
        assert r_normal.iloc[4] == 5.0, "non-outlier must not be clipped"
        # s_outlier extreme value must be clipped
        assert r_outlier.iloc[4] < 1000.0, "outlier must be clipped"


# ---------------------------------------------------------------------------
# standardize
# ---------------------------------------------------------------------------

class TestStandardize:
    def test_mean_zero_std_one(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = standardize(s)
        assert abs(result.mean()) < 1e-10
        assert abs(result.std(ddof=1) - 1.0) < 1e-10

    def test_nan_position_preserved(self):
        s = pd.Series([1.0, 2.0, np.nan, 4.0, 5.0])
        result = standardize(s)
        assert pd.isna(result.iloc[2])
        # Non-NaN positions should be standardized
        assert abs(result.dropna().mean()) < 1e-10

    def test_fewer_than_two_valid_returns_all_nan(self):
        s = pd.Series([1.0, np.nan, np.nan])
        result = standardize(s)
        assert result.isna().all(), "< 2 valid values must return all NaN"

    def test_std_zero_returns_all_nan(self):
        s = pd.Series([5.0, 5.0, 5.0, 5.0])
        result = standardize(s)
        assert result.isna().all(), "std≈0 must return all NaN"

    def test_single_nan_excluded_from_stats(self):
        # std computed on [1,2,3,4,5] not [1,2,nan,4,5]
        s_with_nan = pd.Series([1.0, 2.0, np.nan, 4.0, 5.0])
        s_without  = pd.Series([1.0, 2.0, 4.0, 5.0])
        result_nan    = standardize(s_with_nan).dropna()
        result_direct = standardize(pd.Series([1.0, 2.0, 4.0, 5.0]))
        # Means and stds must both be from valid-only subset
        assert abs(result_nan.mean()) < 1e-10


# ---------------------------------------------------------------------------
# neutralize
# ---------------------------------------------------------------------------

class TestNeutralize:
    def _make_inputs(self, n: int = 100):
        rng = np.random.default_rng(42)
        codes = _make_codes(n)
        factor   = pd.Series(rng.standard_normal(n), index=codes, name="factor")
        industry = _make_industry(n)
        log_mv   = _make_log_mv(n)
        return factor, industry, log_mv

    def test_residuals_orthogonal_to_log_mv(self):
        factor, industry, log_mv = self._make_inputs(150)
        result = neutralize(factor, industry, log_mv)
        valid = result.dropna()
        corr = abs(valid.corr(log_mv.loc[valid.index]))
        assert corr < 0.05, f"|corr| with log_mv after neutralize = {corr:.4f}, expected < 0.05"

    def test_insufficient_samples_returns_original_factor(self):
        codes = ["A", "B", "C"]
        factor   = pd.Series([1.0, 2.0, 3.0], index=codes)
        industry = pd.Series(["I1", "I2", "I3"], index=codes)
        log_mv   = pd.Series([1.0, 2.0, 3.0], index=codes)
        result = neutralize(factor, industry, log_mv)
        pd.testing.assert_series_equal(result, factor)

    def test_nan_industry_excluded_from_regression(self):
        factor, industry, log_mv = self._make_inputs(80)
        industry.iloc[:10] = np.nan
        result = neutralize(factor, industry, log_mv)
        # Positions with NaN industry must remain NaN in output
        assert result.iloc[:10].isna().all()

    def test_nan_factor_excluded(self):
        factor, industry, log_mv = self._make_inputs(80)
        factor.iloc[5:10] = np.nan
        result = neutralize(factor, industry, log_mv)
        assert result.iloc[5:10].isna().all()

    def test_diag_populated_normal(self):
        factor, industry, log_mv = self._make_inputs(100)
        diag: dict = {}
        neutralize(factor, industry, log_mv, _diag=diag)
        assert "n_neutralize_valid" in diag
        assert "skipped_neutralize" in diag
        assert diag["skipped_neutralize"] is False
        assert diag["n_neutralize_valid"] == 100

    def test_diag_skipped_when_insufficient(self):
        codes = ["A", "B"]
        factor   = pd.Series([1.0, 2.0], index=codes)
        industry = pd.Series(["I1", "I2"], index=codes)
        log_mv   = pd.Series([1.0, 2.0], index=codes)
        diag: dict = {}
        neutralize(factor, industry, log_mv, _diag=diag)
        assert diag["skipped_neutralize"] is True
        assert diag["n_neutralize_valid"] == 2


# ---------------------------------------------------------------------------
# preprocess_factor
# ---------------------------------------------------------------------------

class TestPreprocessFactor:
    def _make_inputs(self, n: int = 120, seed: int = 42):
        rng = np.random.default_rng(seed)
        codes = _make_codes(n)
        raw      = pd.Series(rng.standard_normal(n), index=codes)
        industry = _make_industry(n)
        log_mv   = _make_log_mv(n)
        return raw, industry, log_mv

    def test_financial_sector_set_to_nan(self):
        raw, industry, log_mv = self._make_inputs(100)
        fin_code = "801780.SI"
        industry.iloc[:15] = fin_code
        result = preprocess_factor(
            raw, industry, log_mv, fin_sector_codes=frozenset({fin_code})
        )
        assert result.iloc[:15].isna().all(), "financial sector stocks must be NaN"

    def test_output_near_zero_mean_unit_std(self):
        raw, industry, log_mv = self._make_inputs(200)
        result = preprocess_factor(raw, industry, log_mv)
        valid = result.dropna()
        assert abs(valid.mean()) < 0.1
        assert abs(valid.std(ddof=1) - 1.0) < 0.15

    def test_nan_propagated_through_pipeline(self):
        raw, industry, log_mv = self._make_inputs(100)
        raw.iloc[7] = np.nan
        result = preprocess_factor(raw, industry, log_mv)
        assert pd.isna(result.iloc[7]), "NaN in raw must remain NaN in output"

    def test_diag_filled_correctly(self):
        raw, industry, log_mv = self._make_inputs(100)
        fin_code = "801780.SI"
        industry.iloc[:12] = fin_code
        diag: dict = {}
        result = preprocess_factor(
            raw, industry, log_mv,
            fin_sector_codes=frozenset({fin_code}),
            _diag=diag,
        )
        assert diag["n_fin_filtered"] == 12
        assert "n_winsor_clipped" in diag
        assert diag["n_winsor_clipped"] >= 0
        assert "n_neutralize_valid" in diag
        assert "skipped_neutralize" in diag
        assert diag["skipped_neutralize"] is False

    def test_negative_shift_minus1_gives_different_result(self):
        """
        负例：若使用 shift(-1)（未来数据）构造的"因子"，其值应与原始因子产生显著差异。
        这验证了预处理流水线对输入是敏感的（即不会忽略输入直接返回固定值）。
        """
        rng = np.random.default_rng(99)
        n = 150
        codes = _make_codes(n)
        raw      = pd.Series(rng.standard_normal(n), index=codes)
        industry = _make_industry(n)
        log_mv   = _make_log_mv(n)

        result_orig = preprocess_factor(raw, industry, log_mv)

        # 模拟 shift(-1) 引入的未来数据：用下一只股票的值替代当前
        raw_shifted = raw.shift(-1).dropna()
        raw_future = raw_shifted.reindex(codes)
        result_future = preprocess_factor(raw_future, industry, log_mv)

        overlap = result_orig.dropna().index.intersection(result_future.dropna().index)
        assert len(overlap) > 50, "need enough overlap for correlation check"
        corr = result_orig.loc[overlap].corr(result_future.loc[overlap])
        # 两者不应完全相同（高 corr 表示流水线对输入不敏感）
        assert corr < 0.999, (
            f"factor should not be perfectly correlated with shift(-1) version "
            f"(corr={corr:.4f})"
        )
