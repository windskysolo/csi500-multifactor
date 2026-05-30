"""
tests/test_factor_health.py — 因子健康监控单元测试

覆盖范围：
  compute_rolling_ic_ir  : 基本计算、as_of 过滤、窗口截断、min_obs 边界、NaN 处理、常量序列
  compute_health_status  : 所有状态阈值及边界值（STABLE / WEAK / WARN / REVERSE / UNKNOWN）
  compute_trend_flag     : OK / DETERIORATING / INSUFFICIENT_HISTORY 及边界
  FactorHealthMonitor    : 快照行数、列字段、状态/趋势合法性、DETERIORATING 检测、
                           REVERSE 检测、排序、缺失 factor_direction 抛错
  build_report           : 验证期声明、as_of 日期、不回流筛选声明

所有测试使用合成数据，不依赖磁盘文件。
"""

import numpy as np
import pandas as pd
import pytest

from src.evaluation.factor_health import (
    DETERIORATING_GAP,
    IC_IR_STABLE_THRESHOLD,
    IC_IR_WEAK_THRESHOLD,
    MIN_VALID_OBS,
    STATUS_REVERSE,
    STATUS_STABLE,
    STATUS_UNKNOWN,
    STATUS_WARN,
    STATUS_WEAK,
    TREND_DETERIORATING,
    TREND_INSUFFICIENT,
    TREND_OK,
    FactorHealthMonitor,
    compute_health_status,
    compute_rolling_ic_ir,
    compute_trend_flag,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ic_history(
    n_dates: int = 40,
    factor_names: list[str] | None = None,
    mean: float = 0.05,
    std: float = 0.08,
    seed: int = 42,
) -> pd.DataFrame:
    """构造合成 IC 历史矩阵。"""
    if factor_names is None:
        factor_names = ["factor_a", "factor_b"]
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2015-01-01", periods=n_dates, freq="MS")
    data = rng.normal(loc=mean, scale=std, size=(n_dates, len(factor_names)))
    return pd.DataFrame(data, index=dates, columns=factor_names)


def _make_factor_directions(factor_names: list[str], direction: float = 1.0) -> pd.Series:
    return pd.Series({f: direction for f in factor_names})


# ---------------------------------------------------------------------------
# compute_rolling_ic_ir
# ---------------------------------------------------------------------------

class TestComputeRollingIcIr:

    def test_basic_computation_matches_manual(self):
        """给定已知 IC 序列，IC_IR 应等于 mean/std。"""
        dates = pd.date_range("2015-01-01", periods=20, freq="MS")
        ic = pd.Series(
            [0.04, 0.06, 0.05, 0.07, 0.03] * 4,
            index=dates,
            dtype=float,
        )
        as_of = dates[-1]
        result = compute_rolling_ic_ir(ic, window=20, as_of=as_of, min_obs=6)
        expected = ic.mean() / ic.std(ddof=1)
        assert abs(result - expected) < 1e-8

    def test_as_of_filters_future_dates(self):
        """as_of 应只使用 index <= as_of 的数据，不使用之后的数据。"""
        dates = pd.date_range("2015-01-01", periods=24, freq="MS")
        ic = pd.Series(range(24), index=dates, dtype=float)
        as_of = dates[11]  # 只用前 12 期
        result = compute_rolling_ic_ir(ic, window=24, as_of=as_of, min_obs=6)
        sub = ic.iloc[:12]
        expected = sub.mean() / sub.std(ddof=1)
        assert abs(result - expected) < 1e-8

    def test_window_truncates_to_last_n(self):
        """数据多于 window 时，应只取最后 window 期。"""
        dates = pd.date_range("2015-01-01", periods=30, freq="MS")
        ic = pd.Series(range(30), index=dates, dtype=float)
        as_of = dates[-1]
        result = compute_rolling_ic_ir(ic, window=12, as_of=as_of, min_obs=6)
        last12 = ic.iloc[-12:]
        expected = last12.mean() / last12.std(ddof=1)
        assert abs(result - expected) < 1e-8

    def test_insufficient_obs_returns_nan(self):
        """有效观测 < min_obs 时应返回 NaN。"""
        dates = pd.date_range("2015-01-01", periods=5, freq="MS")
        ic = pd.Series([0.05, np.nan, 0.08, np.nan, np.nan], index=dates)
        as_of = dates[-1]
        result = compute_rolling_ic_ir(ic, window=12, as_of=as_of, min_obs=6)
        assert np.isnan(result)

    def test_nan_in_series_excluded_from_window(self):
        """IC 序列中的 NaN 应被剔除后再计算，不占用 window 名额。"""
        dates = pd.date_range("2015-01-01", periods=20, freq="MS")
        # 前10个有效，中间5个NaN，后5个有效（共15个有效值，各有差异保证 std > 0）
        valid_vals = [0.04, 0.06, 0.05, 0.07, 0.03, 0.08, 0.02, 0.09, 0.04, 0.06,
                      0.04, 0.06, 0.05, 0.07, 0.03]
        full_vals  = valid_vals[:10] + [np.nan] * 5 + valid_vals[10:]
        ic = pd.Series(full_vals, index=dates)
        as_of = dates[-1]
        # window=20，但有效观测只有 15 个，应使用全部 15 个
        result = compute_rolling_ic_ir(ic, window=20, as_of=as_of, min_obs=6)
        valid = ic.dropna()
        expected = valid.mean() / valid.std(ddof=1)
        assert not np.isnan(result), "有效样本 15 个，不应返回 NaN"
        assert abs(result - expected) < 1e-8

    def test_constant_series_returns_nan(self):
        """IC 标准差为零（所有值相同）时，应返回 NaN。"""
        dates = pd.date_range("2015-01-01", periods=12, freq="MS")
        ic = pd.Series([0.05] * 12, index=dates)
        as_of = dates[-1]
        result = compute_rolling_ic_ir(ic, window=12, as_of=as_of, min_obs=6)
        assert np.isnan(result)

    def test_empty_series_returns_nan(self):
        """空序列（all NaN）应返回 NaN。"""
        dates = pd.date_range("2015-01-01", periods=10, freq="MS")
        ic = pd.Series([np.nan] * 10, index=dates)
        as_of = dates[-1]
        result = compute_rolling_ic_ir(ic, window=12, as_of=as_of, min_obs=6)
        assert np.isnan(result)

    def test_result_equals_mean_over_std(self):
        """IC_IR 定义验证：结果必须等于 mean(valid) / std(valid, ddof=1)。"""
        rng = np.random.default_rng(7)
        dates = pd.date_range("2016-01-01", periods=24, freq="MS")
        ic = pd.Series(rng.normal(0.05, 0.04, 24), index=dates)
        as_of = dates[-1]
        result = compute_rolling_ic_ir(ic, window=24, as_of=as_of, min_obs=6)
        expected = ic.mean() / ic.std(ddof=1)
        assert abs(result - expected) < 1e-8


# ---------------------------------------------------------------------------
# compute_health_status
# ---------------------------------------------------------------------------

class TestComputeHealthStatus:

    def test_stable_above_threshold(self):
        assert compute_health_status(IC_IR_STABLE_THRESHOLD) == STATUS_STABLE
        assert compute_health_status(0.50) == STATUS_STABLE
        assert compute_health_status(1.00) == STATUS_STABLE

    def test_weak_in_range(self):
        assert compute_health_status(IC_IR_WEAK_THRESHOLD) == STATUS_WEAK
        assert compute_health_status(0.25) == STATUS_WEAK
        assert compute_health_status(0.299) == STATUS_WEAK

    def test_warn_in_range(self):
        assert compute_health_status(0.10) == STATUS_WARN
        assert compute_health_status(0.19) == STATUS_WARN

    def test_warn_at_zero(self):
        """边界值 0.0 应为 WARN（>= 0，< 0.20）。"""
        assert compute_health_status(0.0) == STATUS_WARN

    def test_reverse_below_zero(self):
        assert compute_health_status(-0.001) == STATUS_REVERSE
        assert compute_health_status(-1.0) == STATUS_REVERSE

    def test_unknown_on_nan(self):
        assert compute_health_status(np.nan) == STATUS_UNKNOWN
        assert compute_health_status(float("nan")) == STATUS_UNKNOWN

    def test_stable_boundary_exactly_030(self):
        """边界值 0.30 属于 STABLE（>= 0.30）。"""
        assert compute_health_status(0.30) == STATUS_STABLE

    def test_weak_boundary_exactly_020(self):
        """边界值 0.20 属于 WEAK（>= 0.20，< 0.30）。"""
        assert compute_health_status(0.20) == STATUS_WEAK


# ---------------------------------------------------------------------------
# compute_trend_flag
# ---------------------------------------------------------------------------

class TestComputeTrendFlag:

    def test_ok_when_12m_close_to_36m(self):
        assert compute_trend_flag(0.35, 0.40) == TREND_OK

    def test_ok_when_12m_higher_than_36m(self):
        """近12期优于36期，应为 OK（改善趋势）。"""
        assert compute_trend_flag(0.50, 0.30) == TREND_OK

    def test_deteriorating_when_gap_exceeds_threshold(self):
        """adj_12m = 0.10 < adj_36m(0.40) - 0.15 = 0.25 → DETERIORATING。"""
        assert compute_trend_flag(0.10, 0.40) == TREND_DETERIORATING

    def test_deteriorating_when_12m_negative_36m_positive(self):
        assert compute_trend_flag(-0.30, 0.30) == TREND_DETERIORATING

    def test_insufficient_when_12m_nan(self):
        assert compute_trend_flag(np.nan, 0.40) == TREND_INSUFFICIENT

    def test_insufficient_when_36m_nan(self):
        assert compute_trend_flag(0.30, np.nan) == TREND_INSUFFICIENT

    def test_insufficient_when_both_nan(self):
        assert compute_trend_flag(np.nan, np.nan) == TREND_INSUFFICIENT

    def test_boundary_just_below_triggers_deteriorating(self):
        """gap 恰好超过 DETERIORATING_GAP 时触发；等于时不触发。"""
        adj_36m = 0.40
        threshold = adj_36m - DETERIORATING_GAP  # = 0.25
        # 刚好在边界下方 → DETERIORATING
        assert compute_trend_flag(threshold - 0.001, adj_36m) == TREND_DETERIORATING
        # 恰好等于边界（不满足严格小于）→ OK
        assert compute_trend_flag(threshold, adj_36m) == TREND_OK


# ---------------------------------------------------------------------------
# FactorHealthMonitor
# ---------------------------------------------------------------------------

class TestFactorHealthMonitor:

    def test_snapshot_row_count_equals_factor_count(self):
        """snapshot 行数应等于 ic_history 的列数（因子数）。"""
        n_factors = 5
        names = [f"f{i}" for i in range(n_factors)]
        ic = _make_ic_history(n_dates=30, factor_names=names)
        dirs = _make_factor_directions(names)
        monitor = FactorHealthMonitor(ic, dirs)
        snapshot = monitor.build_snapshot()
        assert len(snapshot) == n_factors

    def test_snapshot_has_required_columns(self):
        """snapshot 必须包含所有规定列。"""
        required = {
            "factor_direction", "n_valid_obs",
            "ic_ir_12m", "ic_ir_24m", "ic_ir_36m",
            "adj_ic_ir_12m", "adj_ic_ir_24m", "adj_ic_ir_36m",
            "status", "trend_flag", "suggested_action",
        }
        ic = _make_ic_history(n_dates=30)
        dirs = _make_factor_directions(["factor_a", "factor_b"])
        monitor = FactorHealthMonitor(ic, dirs)
        snapshot = monitor.build_snapshot()
        missing_cols = required - set(snapshot.columns)
        assert not missing_cols, f"缺少必要列：{missing_cols}"

    def test_status_values_are_valid(self):
        """status 列的所有值必须是合法状态字符串。"""
        valid = {STATUS_STABLE, STATUS_WEAK, STATUS_WARN, STATUS_REVERSE, STATUS_UNKNOWN}
        ic = _make_ic_history(n_dates=40)
        dirs = _make_factor_directions(["factor_a", "factor_b"])
        monitor = FactorHealthMonitor(ic, dirs)
        snapshot = monitor.build_snapshot()
        bad = set(snapshot["status"].unique()) - valid
        assert not bad, f"非法 status 值：{bad}"

    def test_trend_flag_values_are_valid(self):
        """trend_flag 列的所有值必须是合法趋势标志。"""
        valid = {TREND_OK, TREND_DETERIORATING, TREND_INSUFFICIENT}
        ic = _make_ic_history(n_dates=40)
        dirs = _make_factor_directions(["factor_a", "factor_b"])
        monitor = FactorHealthMonitor(ic, dirs)
        snapshot = monitor.build_snapshot()
        bad = set(snapshot["trend_flag"].unique()) - valid
        assert not bad, f"非法 trend_flag 值：{bad}"

    def test_missing_factor_direction_raises_value_error(self):
        """factor_directions 缺少某因子时，__init__ 应抛 ValueError 并提示缺失因子名。"""
        ic = _make_ic_history(factor_names=["factor_a", "factor_b"])
        dirs = _make_factor_directions(["factor_a"])  # 缺少 factor_b
        with pytest.raises(ValueError, match="factor_b"):
            FactorHealthMonitor(ic, dirs)

    def test_nan_direction_raises_value_error(self):
        """factor_directions 中包含 NaN 值时，应抛 ValueError。"""
        ic = _make_ic_history(factor_names=["factor_a"])
        dirs = pd.Series({"factor_a": np.nan})
        with pytest.raises(ValueError):
            FactorHealthMonitor(ic, dirs)

    def test_stable_factor_classified_correctly(self):
        """
        IC 均值很高（≈0.12）的正向因子，adj_IC_IR_24m 应 ≥ 0.30，健康状态应为 STABLE。
        """
        n = 40
        dates = pd.date_range("2015-01-01", periods=n, freq="MS")
        rng = np.random.default_rng(0)
        ic_vals = rng.normal(0.12, 0.04, n)  # IC_IR ≈ 3.0
        ic = pd.DataFrame({"strong": ic_vals}, index=dates)
        dirs = pd.Series({"strong": 1.0})
        monitor = FactorHealthMonitor(ic, dirs)
        snapshot = monitor.build_snapshot()
        assert snapshot.loc["strong", "status"] == STATUS_STABLE, (
            f"adj_IC_IR_24m={snapshot.loc['strong','adj_ic_ir_24m']:.4f}，应为 STABLE"
        )

    def test_reverse_factor_classified_correctly(self):
        """
        IC 均值为负的正向因子（方向设为 +1 但 IC 负），
        adj_IC_IR_24m < 0 → 状态应为 REVERSE。
        """
        n = 40
        dates = pd.date_range("2015-01-01", periods=n, freq="MS")
        rng = np.random.default_rng(1)
        ic_vals = rng.normal(-0.08, 0.04, n)
        ic = pd.DataFrame({"reverse_f": ic_vals}, index=dates)
        dirs = pd.Series({"reverse_f": 1.0})
        monitor = FactorHealthMonitor(ic, dirs)
        snapshot = monitor.build_snapshot()
        assert snapshot.loc["reverse_f", "status"] == STATUS_REVERSE, (
            f"adj_IC_IR_24m={snapshot.loc['reverse_f','adj_ic_ir_24m']:.4f}，应为 REVERSE"
        )

    def test_negative_direction_inverts_sign(self):
        """
        IC 均值为负但 factor_direction=-1（正确的负向因子），
        adj_IC_IR_24m = IC_IR * (-1) > 0 → 状态应为 STABLE（IC_IR 够强）。
        """
        n = 40
        dates = pd.date_range("2015-01-01", periods=n, freq="MS")
        rng = np.random.default_rng(2)
        ic_vals = rng.normal(-0.12, 0.04, n)  # IC_IR ≈ -3.0，方向 -1 后变 +3.0
        ic = pd.DataFrame({"neg_factor": ic_vals}, index=dates)
        dirs = pd.Series({"neg_factor": -1.0})
        monitor = FactorHealthMonitor(ic, dirs)
        snapshot = monitor.build_snapshot()
        assert snapshot.loc["neg_factor", "status"] == STATUS_STABLE, (
            f"负向因子方向校正后 adj_IC_IR_24m 应 ≥ 0.30，"
            f"实际={snapshot.loc['neg_factor','adj_ic_ir_24m']:.4f}"
        )

    def test_deteriorating_detected_in_snapshot(self):
        """
        早期 IC 高（约 0.10）、近期 IC 低（约 -0.08）的因子，
        应触发 DETERIORATING 趋势预警。
        """
        n_early, n_late = 36, 12
        dates = pd.date_range("2015-01-01", periods=n_early + n_late, freq="MS")
        rng = np.random.default_rng(999)
        early_ic = rng.normal(0.10, 0.04, n_early)   # 信号强、方向正
        late_ic  = rng.normal(-0.08, 0.04, n_late)   # 信号消失、方向反
        ic_vals  = np.concatenate([early_ic, late_ic])
        ic = pd.DataFrame({"det_factor": ic_vals}, index=dates)
        dirs = pd.Series({"det_factor": 1.0})
        monitor = FactorHealthMonitor(ic, dirs)
        snapshot = monitor.build_snapshot()

        adj_12m = snapshot.loc["det_factor", "adj_ic_ir_12m"]
        adj_36m = snapshot.loc["det_factor", "adj_ic_ir_36m"]
        assert not pd.isna(adj_12m), "adj_ic_ir_12m 不应为 NaN"
        assert not pd.isna(adj_36m), "adj_ic_ir_36m 不应为 NaN"
        assert snapshot.loc["det_factor", "trend_flag"] == TREND_DETERIORATING, (
            f"adj_12m={adj_12m:.4f} adj_36m={adj_36m:.4f}，差值={adj_36m-adj_12m:.4f}，"
            f"应触发 DETERIORATING（差值 > {DETERIORATING_GAP}）"
        )

    def test_snapshot_sorted_by_adj_ic_ir_24m_descending(self):
        """snapshot 应按 adj_ic_ir_24m 降序排列，NaN 在末尾。"""
        ic = _make_ic_history(n_dates=40, factor_names=["f1", "f2", "f3", "f4"])
        dirs = _make_factor_directions(["f1", "f2", "f3", "f4"])
        monitor = FactorHealthMonitor(ic, dirs)
        snapshot = monitor.build_snapshot()
        non_nan = snapshot["adj_ic_ir_24m"].dropna()
        assert (non_nan.diff().dropna() <= 0).all(), "adj_ic_ir_24m 应单调不递增"

    def test_as_of_restricts_data(self):
        """
        指定较早的 as_of 时，结果不应使用之后的 IC 数据；
        n_valid_obs 应 <= 全量时的值（先按因子名对齐再比较）。
        """
        ic = _make_ic_history(n_dates=60)
        dirs = _make_factor_directions(["factor_a", "factor_b"])
        monitor = FactorHealthMonitor(ic, dirs)
        as_of_full  = ic.index.max()
        as_of_early = ic.index[30]
        snap_full  = monitor.build_snapshot(as_of_full)
        snap_early = monitor.build_snapshot(as_of_early)
        # 两个 snapshot 排序可能不同，先按 factor 名对齐再比较
        common_factors = snap_full.index.intersection(snap_early.index)
        early_obs = snap_early.loc[common_factors, "n_valid_obs"]
        full_obs  = snap_full.loc[common_factors, "n_valid_obs"]
        assert (early_obs.values <= full_obs.values).all(), (
            "as_of 更早时 n_valid_obs 应 <= 全量时的值"
        )

    def test_insufficient_data_returns_unknown(self):
        """IC 序列有效观测极少时，状态应为 UNKNOWN。"""
        dates = pd.date_range("2015-01-01", periods=4, freq="MS")
        ic = pd.DataFrame({"sparse": [0.05, np.nan, np.nan, np.nan]}, index=dates)
        dirs = pd.Series({"sparse": 1.0})
        monitor = FactorHealthMonitor(ic, dirs, min_obs=6)
        snapshot = monitor.build_snapshot()
        assert snapshot.loc["sparse", "status"] == STATUS_UNKNOWN

    # ---- build_report ----

    def test_build_report_contains_validation_disclaimer(self):
        """health_report.md 必须包含验证期诊断声明和不回流筛选声明。"""
        ic = _make_ic_history(n_dates=40)
        dirs = _make_factor_directions(["factor_a", "factor_b"])
        monitor = FactorHealthMonitor(ic, dirs)
        as_of = ic.index.max()
        snapshot = monitor.build_snapshot(as_of)
        report = monitor.build_report(snapshot, as_of)
        assert "验证期" in report,     "报告必须提及验证期"
        assert "诊断" in report,       "报告必须说明仅供诊断"
        assert "回流" in report or "筛选" in report, "报告必须说明不得回流至筛选逻辑"

    def test_build_report_contains_as_of_date(self):
        """报告中应包含 as_of 日期字符串。"""
        ic = _make_ic_history(n_dates=40)
        dirs = _make_factor_directions(["factor_a", "factor_b"])
        monitor = FactorHealthMonitor(ic, dirs)
        as_of = ic.index.max()
        snapshot = monitor.build_snapshot(as_of)
        report = monitor.build_report(snapshot, as_of)
        assert str(as_of.date()) in report

    def test_build_report_contains_all_status_sections(self):
        """报告应包含 status 分组列表的标题。"""
        ic = _make_ic_history(n_dates=40)
        dirs = _make_factor_directions(["factor_a", "factor_b"])
        monitor = FactorHealthMonitor(ic, dirs)
        as_of = ic.index.max()
        snapshot = monitor.build_snapshot(as_of)
        report = monitor.build_report(snapshot, as_of)
        # 至少有一个状态在报告分组中出现
        found_any = any(s in report for s in
                        [STATUS_STABLE, STATUS_WEAK, STATUS_WARN, STATUS_REVERSE, STATUS_UNKNOWN])
        assert found_any, "报告缺少状态分组内容"

    def test_build_report_contains_suggested_actions(self):
        """报告应包含建议操作说明。"""
        ic = _make_ic_history(n_dates=40)
        dirs = _make_factor_directions(["factor_a", "factor_b"])
        monitor = FactorHealthMonitor(ic, dirs)
        as_of = ic.index.max()
        snapshot = monitor.build_snapshot(as_of)
        report = monitor.build_report(snapshot, as_of)
        assert "REVIEW_REMOVAL" in report or "HOLD" in report or "MONITOR" in report
