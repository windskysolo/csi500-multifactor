"""
tests/test_ridge_decay.py — RidgeDecayCombiner 单元测试

覆盖计划要求的四类测试：
  1. sample_weight 长度、顺序、均值归一化
  2. half_life_months <= 0 的参数校验
  3. _build_training_matrix_with_counts 的 n_per_date 对齐
  4. stages.py 未知 training_mode 的 ValueError 保护
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from src.signal.ridge_decay import RidgeDecayCombiner


# ---------------------------------------------------------------------------
# 测试辅助：生成最小可用的合成数据
# ---------------------------------------------------------------------------

def _make_panels(
    n_dates: int = 5,
    n_stocks: int = 20,
    n_factors: int = 3,
    seed: int = 42,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, list[str]]:
    """
    生成满足 RidgeCombiner 接口要求的合成因子面板和收益面板。

    n_stocks >= 10：满足 _build_training_matrix_with_counts 的 keep_mask.sum() >= 10 条件。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2015-01-31", periods=n_dates, freq="ME")
    stocks = [f"{i:06d}.SZ" for i in range(n_stocks)]
    factor_names = [f"f{i}" for i in range(n_factors)]

    factor_panels = {
        name: pd.DataFrame(
            rng.standard_normal((n_dates, n_stocks)),
            index=dates,
            columns=stocks,
        )
        for name in factor_names
    }
    fwd_ret_panel = pd.DataFrame(
        rng.standard_normal((n_dates, n_stocks)),
        index=dates,
        columns=stocks,
    )
    return factor_panels, fwd_ret_panel, factor_names


# ---------------------------------------------------------------------------
# 1. _build_sample_weights：长度、顺序、均值归一化
# ---------------------------------------------------------------------------

class TestBuildSampleWeights:
    def test_length_equals_sum_n_per_date(self):
        combiner = RidgeDecayCombiner(["f0", "f1"], half_life_months=12)
        dates = pd.date_range("2015-01-31", periods=4, freq="ME")
        n_per_date = [10, 15, 12, 8]
        sw = combiner._build_sample_weights(dates, n_per_date)
        assert len(sw) == sum(n_per_date)

    def test_older_weight_less_than_newer(self):
        """最旧日期权重 < 较新日期权重 < 最新日期权重（归一化不改变顺序）。"""
        combiner = RidgeDecayCombiner(["f0"], half_life_months=12)
        dates = pd.date_range("2015-01-31", periods=3, freq="ME")
        n_per_date = [1, 1, 1]
        sw = combiner._build_sample_weights(dates, n_per_date)
        assert sw[0] < sw[1] < sw[2]

    def test_mean_approximately_one(self):
        combiner = RidgeDecayCombiner(["f0"], half_life_months=24)
        dates = pd.date_range("2015-01-31", periods=10, freq="ME")
        n_per_date = [5] * 10
        sw = combiner._build_sample_weights(dates, n_per_date)
        assert abs(sw.mean() - 1.0) < 1e-10

    def test_zero_count_dates_skipped_gracefully(self):
        """n_obs=0 的日期不贡献任何权重行，不影响其他日期的权重。"""
        combiner = RidgeDecayCombiner(["f0"], half_life_months=12)
        dates = pd.date_range("2015-01-31", periods=4, freq="ME")
        n_per_date = [0, 10, 0, 8]
        sw = combiner._build_sample_weights(dates, n_per_date)
        assert len(sw) == 18  # 0+10+0+8

    def test_length_mismatch_raises(self):
        combiner = RidgeDecayCombiner(["f0"], half_life_months=12)
        dates = pd.date_range("2015-01-31", periods=3, freq="ME")
        with pytest.raises(ValueError, match="不匹配"):
            combiner._build_sample_weights(dates, [1, 2])  # len 2 ≠ 3


# ---------------------------------------------------------------------------
# 2. __init__：half_life_months 参数校验
# ---------------------------------------------------------------------------

class TestInit:
    def test_requires_positive_half_life_zero(self):
        with pytest.raises(ValueError):
            RidgeDecayCombiner(["f0"], half_life_months=0)

    def test_requires_positive_half_life_negative(self):
        with pytest.raises(ValueError):
            RidgeDecayCombiner(["f0"], half_life_months=-5)

    def test_lambda_formula_hl24(self):
        combiner = RidgeDecayCombiner(["f0"], half_life_months=24)
        assert abs(combiner.lam - 0.5 ** (1.0 / 24)) < 1e-12

    def test_half_life_stored_as_int(self):
        combiner = RidgeDecayCombiner(["f0"], half_life_months=24)
        assert isinstance(combiner.half_life_months, int)


# ---------------------------------------------------------------------------
# 3. _build_training_matrix_with_counts：n_per_date 对齐不变量
# ---------------------------------------------------------------------------

class TestBuildTrainingMatrixWithCounts:
    def test_sum_n_per_date_equals_x_rows(self):
        """sum(n_per_date) == len(X_train) 是 sample_weight 构造的基础。"""
        factor_panels, fwd_ret_panel, factor_names = _make_panels(
            n_dates=5, n_stocks=20, n_factors=3
        )
        combiner = RidgeDecayCombiner(
            factor_names, half_life_months=12, min_valid_factors=2
        )
        dates = pd.DatetimeIndex(factor_panels[factor_names[0]].index)
        X, y, n_per_date = combiner._build_training_matrix_with_counts(
            factor_panels, fwd_ret_panel, dates
        )
        assert sum(n_per_date) == len(X)
        assert len(X) == len(y)

    def test_n_per_date_length_matches_training_dates(self):
        """len(n_per_date) == len(training_dates)，包括跳过的日期。"""
        factor_panels, fwd_ret_panel, factor_names = _make_panels(
            n_dates=6, n_stocks=20, n_factors=3
        )
        combiner = RidgeDecayCombiner(
            factor_names, half_life_months=12, min_valid_factors=2
        )
        # 只取前 4 期作为训练日期
        dates = pd.DatetimeIndex(factor_panels[factor_names[0]].index[:4])
        X, y, n_per_date = combiner._build_training_matrix_with_counts(
            factor_panels, fwd_ret_panel, dates
        )
        assert len(n_per_date) == 4

    def test_sample_weight_integrates_with_matrix(self):
        """_build_sample_weights 能接受 _build_training_matrix_with_counts 的输出。"""
        factor_panels, fwd_ret_panel, factor_names = _make_panels(
            n_dates=5, n_stocks=20, n_factors=3
        )
        combiner = RidgeDecayCombiner(
            factor_names, half_life_months=12, min_valid_factors=2
        )
        dates = pd.DatetimeIndex(factor_panels[factor_names[0]].index)
        X, y, n_per_date = combiner._build_training_matrix_with_counts(
            factor_panels, fwd_ret_panel, dates
        )
        sw = combiner._build_sample_weights(dates, n_per_date)
        assert len(sw) == len(X)

    def test_empty_dates_returns_empty_arrays(self):
        factor_panels, fwd_ret_panel, factor_names = _make_panels()
        combiner = RidgeDecayCombiner(factor_names, half_life_months=12)
        empty_dates = pd.DatetimeIndex([])
        X, y, n_per_date = combiner._build_training_matrix_with_counts(
            factor_panels, fwd_ret_panel, empty_dates
        )
        assert len(X) == 0
        assert len(n_per_date) == 0


# ---------------------------------------------------------------------------
# 4. stages.py：未知 training_mode 必须抛 ValueError
# ---------------------------------------------------------------------------

class TestUnknownRidgeTrainingMode:
    def test_unknown_mode_raises_value_error(self, tmp_path):
        """拼错 training_mode 应该抛 ValueError，而不是静默跑成 expanding。"""
        dates = pd.date_range("2015-01-31", periods=30, freq="ME")
        stocks = [f"{i:06d}.SZ" for i in range(10)]
        rng = np.random.default_rng(0)
        mock_panel = pd.DataFrame(
            rng.standard_normal((30, 10)), index=dates, columns=stocks
        )
        mock_panels = {"f1": mock_panel}

        sig = MagicMock()
        sig.window_months = None
        sig.half_life_months = None
        sig.alpha_grid = [1.0]
        sig.purge_months = 2

        spec = MagicMock()
        spec.experiment_id = "test_unknown_mode"

        with (
            patch("src.pipeline.stages._load_factor_panels", return_value=mock_panels),
            patch("src.pipeline.stages._load_fwd_ret", return_value=mock_panel),
        ):
            from src.pipeline.stages import _run_signal_ridge
            with pytest.raises(ValueError, match="未知"):
                _run_signal_ridge(
                    sig, tmp_path, tmp_path, spec, "totally_invalid_mode"
                )
