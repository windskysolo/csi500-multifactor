"""
tests/test_optimizer.py — 协方差与组合优化单元测试

覆盖的高风险点（F6-006）：
  1. L3 停牌锁定：停牌股权重必须等于上期权重
  2. L3 跌停锁定：跌停股权重不得低于上期权重
  3. 缺失协方差不得汇报为 L1（fallback_level 必须 >= 1）
  4. 非 PSD 协方差缓存被正确识别并修复
  5. optimize_single_period 基本 L1 可行性
  6. L3 第一期无 w_prev 时不崩溃

测试设计原则：
  - 纯内存构造，不依赖 Parquet/CSV 数据文件
  - 参数设置偏松（TE_target=0.5, industry_max_dev=1.0, single_max_dev=1.0），
    确保 L1 在正常情况下可行，只在特定测试中主动触发降级
"""

import numpy as np
import pandas as pd
import pytest

from src.portfolio.optimizer import (
    OptimizeConfig,
    OptimizeResult,
    _topn_equal_weight,
    optimize_single_period,
    optimize_all_periods,
    optimize_topn_equal_weight_all_periods,
)
from src.portfolio.covariance import validate_and_repair_covariance


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------

def _make_alpha(codes: list[str], values: list[float] | None = None) -> pd.Series:
    """构造 alpha 向量。"""
    v = values if values is not None else list(range(len(codes), 0, -1))
    return pd.Series(dict(zip(codes, [float(x) for x in v])))


def _make_benchmark(codes: list[str]) -> pd.Series:
    """等权基准权重。"""
    w = 1.0 / len(codes)
    return pd.Series({c: w for c in codes})


def _identity_cov(n: int, scale: float = 1e-4) -> np.ndarray:
    """微小单位矩阵（保持 L1 TE 约束近乎无效，便于测试通过 L1）。"""
    return np.eye(n) * scale


def _loose_config() -> OptimizeConfig:
    """宽松参数：TE 松、偏离松，确保正常情况下 L1 可行。"""
    return OptimizeConfig(
        te_target_annual=0.50,
        industry_max_dev=1.00,
        single_max_dev=1.00,
        topn=10,
    )


# ---------------------------------------------------------------------------
# _topn_equal_weight 测试
# ---------------------------------------------------------------------------

class TestTopnEqualWeightHaltLock:
    """F6-001: L3 停牌锁定——停牌股权重必须等于上期权重。"""

    CODES  = ["A", "B", "C", "D", "E"]
    ALPHA  = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
    N      = 5
    TOPN   = 3

    def test_halt_stocks_keep_prev_weight(self):
        """停牌股（B、C）权重必须等于上期权重，不参与等权池。"""
        w_prev = np.array([0.20, 0.25, 0.15, 0.20, 0.20])
        halt   = {1, 2}   # B、C 停牌

        w, compliant = _topn_equal_weight(
            self.ALPHA, self.N, self.TOPN,
            halt_indices=halt,
            w_prev_vec=w_prev,
        )

        # 停牌股权重锁定
        assert w[1] == pytest.approx(w_prev[1], abs=1e-9), "B 停牌，权重应等于上期"
        assert w[2] == pytest.approx(w_prev[2], abs=1e-9), "C 停牌，权重应等于上期"
        # 权重和为 1
        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        # 非停牌股权重非负
        assert np.all(w >= 0.0)
        assert compliant is True

    def test_halt_budget_distributed_to_free_stocks(self):
        """剩余预算分配给非停牌、非涨跌停的 TopN 股票（topn 截断行为）。"""
        w_prev = np.array([0.10, 0.30, 0.20, 0.20, 0.20])
        halt   = {1}   # B 停牌（alpha[1]=4.0），权重 0.30

        # single_max_dev=1.0 令 n_min=1，effective_topn=max(3,1)=3，
        # 专门测试 topn 截断行为（E 排名第4、被截掉），不测偏离约束。
        w, compliant = _topn_equal_weight(
            self.ALPHA, self.N, self.TOPN,
            halt_indices=halt,
            w_prev_vec=w_prev,
            single_max_dev=1.0,
        )

        # ALPHA = [5.0, 4.0, 3.0, 2.0, 1.0]，B(idx=1) 停牌后
        # 非停牌 TopN=3：A(5.0, idx=0)、C(3.0, idx=2)、D(2.0, idx=3)
        free_budget   = 1.0 - 0.30        # 0.70
        expected_unit = free_budget / 3   # 约 0.2333
        assert w[0] == pytest.approx(expected_unit, abs=1e-9), "A 应等权"
        assert w[2] == pytest.approx(expected_unit, abs=1e-9), "C 应等权"
        assert w[3] == pytest.approx(expected_unit, abs=1e-9), "D 应等权"
        assert w[4] == pytest.approx(0.0, abs=1e-9), "E 排名第4，超出 TopN=3，权重应为0"
        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        assert compliant is True

    def test_dynamic_topn_expands_to_satisfy_single_max_dev(self):
        """单股偏离约束迫使 effective_topn 超过 topn，选更多股票。"""
        # 5 只股票，TOPN=3，single_max_dev=0.2 → n_min=ceil(1.0/0.2)=5
        # effective_topn=max(3,5)=5，全部候选被选中，unit_w=0.20
        alpha = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        n = 5

        w, compliant = _topn_equal_weight(
            alpha, n, topn=3,
            halt_indices=set(),
            single_max_dev=0.2,
        )

        assert compliant is True
        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        unit = 1.0 / 5
        assert w[4] == pytest.approx(unit, abs=1e-9), "strict single_max_dev 迫使选入排名第5的股票"

    def test_no_prev_weight_halt_excluded(self):
        """第一期无 w_prev 时，停牌股权重为 0，不崩溃。"""
        halt = {0, 1}
        w, compliant = _topn_equal_weight(
            self.ALPHA, self.N, self.TOPN,
            halt_indices=halt,
            w_prev_vec=None,
        )
        assert w[0] == pytest.approx(0.0), "无 w_prev 时停牌股权重为 0"
        assert w[1] == pytest.approx(0.0), "无 w_prev 时停牌股权重为 0"
        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        assert compliant is True


class TestTopnEqualWeightLimitDn:
    """F6-001: L3 跌停锁定——跌停股权重不得低于上期权重。"""

    CODES = ["A", "B", "C", "D", "E"]
    ALPHA = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
    N     = 5
    TOPN  = 2

    def test_limit_dn_stock_keeps_prev_weight(self):
        """跌停股（D）被锁定在上期权重，不参与自由分配。"""
        w_prev  = np.array([0.20, 0.20, 0.20, 0.25, 0.15])
        lim_dn  = {3}   # D 跌停

        w, compliant = _topn_equal_weight(
            self.ALPHA, self.N, self.TOPN,
            halt_indices=set(),
            limit_dn_indices=lim_dn,
            w_prev_vec=w_prev,
        )

        assert w[3] == pytest.approx(w_prev[3], abs=1e-9), "D 跌停，权重不得低于上期"
        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        assert np.all(w >= 0.0)
        assert compliant is True

    def test_halt_overrides_limit_dn(self):
        """同时停牌且跌停：停牌优先，权重来自 w_prev（效果相同）。"""
        w_prev = np.array([0.20, 0.20, 0.20, 0.20, 0.20])
        halt   = {0}
        lim_dn = {0}   # 同一只股票

        w, compliant = _topn_equal_weight(
            self.ALPHA, self.N, self.TOPN,
            halt_indices=halt,
            limit_dn_indices=lim_dn,
            w_prev_vec=w_prev,
        )

        assert w[0] == pytest.approx(w_prev[0], abs=1e-9)
        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        assert compliant is True


# ---------------------------------------------------------------------------
# optimize_single_period 测试
# ---------------------------------------------------------------------------

class TestOptimizeSinglePeriod:
    """基本可行性和降级逻辑测试。"""

    CODES = [f"S{i:02d}" for i in range(10)]
    N     = 10

    def _alpha(self, v=None):
        return _make_alpha(self.CODES, v)

    def _bench(self):
        return _make_benchmark(self.CODES)

    def _cov(self):
        return _identity_cov(self.N, scale=1e-4)

    def _ind_map(self):
        # 均属同一行业，不影响测试目的
        return pd.Series({c: "IND_A" for c in self.CODES})

    def test_l1_feasible_returns_level_0(self):
        """宽松参数下 L1 应可行，fallback_level=0。"""
        result = optimize_single_period(
            alpha=self._alpha(),
            w_b=self._bench(),
            cov=self._cov(),
            industry_map=self._ind_map(),
            config=_loose_config(),
        )
        assert isinstance(result, OptimizeResult)
        assert result.fallback_level == 0
        assert result.weights.sum() == pytest.approx(1.0, abs=1e-6)
        assert result.weights.min() >= -1e-8   # 权重非负（允许微小数值误差）

    def test_weights_sum_to_one(self):
        """无论哪个 fallback 层，权重和必须等于 1。"""
        result = optimize_single_period(
            alpha=self._alpha(),
            w_b=self._bench(),
            cov=self._cov(),
            industry_map=self._ind_map(),
            config=_loose_config(),
        )
        assert result.weights.sum() == pytest.approx(1.0, abs=1e-6)

    def test_first_period_no_w_prev(self):
        """第一期 w_prev=None 不崩溃，权重和=1。"""
        result = optimize_single_period(
            alpha=self._alpha(),
            w_b=self._bench(),
            cov=self._cov(),
            industry_map=self._ind_map(),
            w_prev=None,
            config=_loose_config(),
        )
        assert result.weights.sum() == pytest.approx(1.0, abs=1e-6)

    def test_halt_codes_lock_weight(self):
        """停牌股在所有 fallback 层下权重应等于上期权重（至少 L1/L2 有约束）。"""
        codes  = self.CODES
        halt_c = codes[0]
        w_prev = pd.Series({c: 1.0 / self.N for c in codes})

        result = optimize_single_period(
            alpha=self._alpha(),
            w_b=self._bench(),
            cov=self._cov(),
            industry_map=self._ind_map(),
            w_prev=w_prev,
            halt_codes={halt_c},
            config=_loose_config(),
        )
        expected = w_prev[halt_c]
        assert result.weights[halt_c] == pytest.approx(expected, abs=1e-6), (
            f"停牌股 {halt_c} 权重应锁定在上期 {expected:.4f}，"
            f"实际 {result.weights[halt_c]:.4f}"
        )


# ---------------------------------------------------------------------------
# optimize_all_periods 中的 F6-002 测试
# ---------------------------------------------------------------------------

class TestMissingCovarianceNotReportedAsL1:
    """F6-002: 缺失协方差的期不得汇报为 fallback_level=0 (L1)。"""

    CODES  = [f"S{i:02d}" for i in range(8)]
    N      = 8
    DATES  = [
        pd.Timestamp("2022-01-31"),
        pd.Timestamp("2022-02-28"),
        pd.Timestamp("2022-03-31"),
    ]

    def _make_inputs(self):
        bench = _make_benchmark(self.CODES)
        alpha = _make_alpha(self.CODES)

        composite_panel = pd.DataFrame(
            {c: 0.5 for c in self.CODES},
            index=self.DATES,
        )
        composite_panel.index.name = "rebalance_date"

        benchmark_weights = {T: bench for T in self.DATES}
        industry_map = pd.Series({c: "IND_A" for c in self.CODES})

        return composite_panel, benchmark_weights, industry_map

    def test_missing_cov_not_l1(self):
        """
        第二期协方差缺失：optimize_all_periods 应将该期 fallback_level 覆盖为 1，
        而不是允许它以 L1 (0) 呈现。
        """
        composite_panel, benchmark_weights, industry_map = self._make_inputs()

        # 只提供第一期和第三期的协方差，第二期缺失
        real_cov = _identity_cov(self.N, scale=2e-5)
        cov_dict = {
            self.DATES[0]: real_cov,
            # self.DATES[1] 缺失
            self.DATES[2]: real_cov,
        }

        _, meta_df = optimize_all_periods(
            composite_panel=composite_panel,
            benchmark_weights=benchmark_weights,
            cov_dict=cov_dict,
            cov_codes=self.CODES,
            industry_map=industry_map,
            rebalance_dates=self.DATES,
            config=_loose_config(),
        )

        missing_date = self.DATES[1]
        assert missing_date in meta_df.index, "缺失协方差期应在 meta_df 中"

        fb_level = meta_df.loc[missing_date, "fallback_level"]
        assert fb_level >= 1, (
            f"协方差缺失期 {missing_date.date()} 的 fallback_level 应 >= 1，"
            f"实际为 {fb_level}（F6-002 违规）"
        )

        cov_avail = meta_df.loc[missing_date, "cov_available"]
        assert cov_avail is False or cov_avail == 0, (
            f"cov_available 应为 False，实际为 {cov_avail}"
        )

    def test_available_cov_can_be_l1(self):
        """协方差可用的期，fallback_level 仍可以为 0（L1）。"""
        composite_panel, benchmark_weights, industry_map = self._make_inputs()
        real_cov = _identity_cov(self.N, scale=2e-5)
        cov_dict = {T: real_cov for T in self.DATES}

        _, meta_df = optimize_all_periods(
            composite_panel=composite_panel,
            benchmark_weights=benchmark_weights,
            cov_dict=cov_dict,
            cov_codes=self.CODES,
            industry_map=industry_map,
            rebalance_dates=self.DATES,
            config=_loose_config(),
        )

        # 至少有一期 cov_available=True
        assert meta_df["cov_available"].any(), "应存在协方差可用的期"

    def test_meta_has_required_columns(self):
        """meta_df 必须包含 cov_available 和 w_prev_source 字段（F6-002/F6-004）。"""
        composite_panel, benchmark_weights, industry_map = self._make_inputs()
        real_cov = _identity_cov(self.N, scale=2e-5)
        cov_dict = {T: real_cov for T in self.DATES}

        _, meta_df = optimize_all_periods(
            composite_panel=composite_panel,
            benchmark_weights=benchmark_weights,
            cov_dict=cov_dict,
            cov_codes=self.CODES,
            industry_map=industry_map,
            rebalance_dates=self.DATES,
            config=_loose_config(),
        )

        assert "cov_available"  in meta_df.columns, "meta_df 缺少 cov_available 列"
        assert "w_prev_source"  in meta_df.columns, "meta_df 缺少 w_prev_source 列"
        assert "fallback_level" in meta_df.columns, "meta_df 缺少 fallback_level 列"


# ---------------------------------------------------------------------------
# validate_and_repair_covariance 测试（F6-003）
# ---------------------------------------------------------------------------

class TestCovarianceCacheValidation:
    """F6-003: 非 PSD / 非对称协方差缓存应被识别并修复。"""

    N = 6

    def _valid_psd(self) -> np.ndarray:
        """构造合法 PSD 矩阵（对角线为方差，非对角线为协方差）。"""
        rng = np.random.default_rng(42)
        A = rng.standard_normal((20, self.N))
        cov = A.T @ A / 20 + np.eye(self.N) * 1e-4
        return cov

    def test_valid_psd_returns_was_valid_true(self):
        """合法 PSD 矩阵：was_already_valid=True，diag_delta=0。"""
        cov = self._valid_psd()
        repaired, was_valid, min_eig, delta = validate_and_repair_covariance(cov)
        assert was_valid is True
        assert delta == pytest.approx(0.0)
        # 修复后最小特征值应 >= EPSILON_DIAG
        assert float(np.linalg.eigvalsh(repaired).min()) > 0.0

    def test_non_psd_is_repaired(self):
        """最小特征值为负的矩阵：was_already_valid=False，修复后正定。"""
        cov = self._valid_psd()
        # 强制让最小特征值为负
        cov[0, 0] = -1e-3
        cov = (cov + cov.T) / 2.0   # 保持对称

        repaired, was_valid, min_eig_before, delta = validate_and_repair_covariance(cov)
        assert was_valid is False
        assert min_eig_before < 0.0
        assert delta > 0.0
        assert float(np.linalg.eigvalsh(repaired).min()) >= 0.0

    def test_asymmetric_matrix_is_symmetrized(self):
        """不对称矩阵应被强制对称化，返回对称矩阵。"""
        cov = self._valid_psd()
        cov[0, 1] += 1e-3   # 破坏对称性

        repaired, _, _, _ = validate_and_repair_covariance(cov)
        sym_err = float(np.max(np.abs(repaired - repaired.T)))
        assert sym_err < 1e-12, f"对称化后不对称误差应 < 1e-12，实际 {sym_err:.2e}"

    def test_identity_matrix_passes_validation(self):
        """单位矩阵：合法 PSD，无需修复。"""
        cov = np.eye(self.N)
        _, was_valid, _, delta = validate_and_repair_covariance(cov)
        assert was_valid is True
        assert delta == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# F6-001 新增：涨停排除与 constraint_compliant 行为验证
# ---------------------------------------------------------------------------

class TestTopnLimitUpExclusion:
    """F6-001: 涨停股在 no_candidates 紧急退化路径中不获分配预算。"""

    CODES = ["A", "B", "C", "D", "E"]
    N     = 5
    TOPN  = 3

    def test_limit_up_stock_zero_weight_when_no_candidates(self):
        """
        当所有可选候选因跌停/停牌全覆盖而 selected=[] 时，
        紧急回退不得给涨停股（no_buy_indices）分配预算。
        F6-001 修复点：emergency_excluded = halt_indices | no_buy（非旧逻辑仅排停牌）。
        """
        # 全部可用候选被跌停锁定；涨停股 D(idx=3) 不可买入
        alpha   = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        w_prev  = np.array([0.20, 0.20, 0.20, 0.20, 0.20])
        halt    = set()
        lim_dn  = {0, 1, 2, 4}   # A/B/C/E 全部跌停，锁定上期权重
        no_buy  = {3}             # D 涨停，不可买入

        w, compliant = _topn_equal_weight(
            alpha, self.N, self.TOPN,
            halt_indices=halt,
            no_buy_indices=no_buy,
            limit_dn_indices=lim_dn,
            w_prev_vec=w_prev,
        )

        # 所有跌停股保持上期权重，free_budget ≈ 0；D 涨停不得获分配
        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        assert w[3] == pytest.approx(0.0, abs=1e-9), (
            "涨停股 D 在无候选紧急路径中不得获得预算"
        )

    def test_constraint_compliant_false_when_no_candidates(self):
        """
        所有非停牌股均涨停时（selected=[]，紧急路径），
        constraint_compliant 必须为 False。
        """
        # 股票数量少，让涨停完全覆盖可选股
        n     = 4
        alpha = np.array([3.0, 2.0, 1.0, 0.5])
        halt  = set()
        no_buy = {0, 1, 2, 3}   # 全部涨停

        w, compliant = _topn_equal_weight(
            alpha, n, topn=2,
            halt_indices=halt,
            no_buy_indices=no_buy,
            w_prev_vec=None,
        )

        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        assert compliant is False, (
            "全部股票涨停（无可选候选）时，constraint_compliant 必须为 False"
        )

    def test_constraint_compliant_true_when_candidates_available(self):
        """正常有候选时 constraint_compliant 必须为 True。"""
        alpha  = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        w_prev = np.array([0.20, 0.20, 0.20, 0.20, 0.20])
        halt   = {0}    # A 停牌
        no_buy = {1}    # B 涨停

        # C/D/E 可自由买入，candidates 非空
        w, compliant = _topn_equal_weight(
            alpha, self.N, self.TOPN,
            halt_indices=halt,
            no_buy_indices=no_buy,
            w_prev_vec=w_prev,
        )

        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        assert compliant is True


class TestForcedTopnEqualWeightAllPeriods:
    """Forced topn_ew mode must reuse L3 constraints and emit full metadata."""

    DATES = [
        pd.Timestamp("2022-01-31"),
        pd.Timestamp("2022-02-28"),
    ]
    CODES = ["A", "B", "C", "D", "E"]

    def _benchmark_weights(self) -> dict[pd.Timestamp, pd.Series]:
        bench = pd.Series(1.0 / len(self.CODES), index=self.CODES)
        return {T: bench for T in self.DATES}

    def test_forced_topn_emits_expected_metadata(self):
        composite = pd.DataFrame(
            [
                [5.0, 4.0, 3.0, 2.0, 1.0],
                [1.0, 2.0, 5.0, 4.0, 3.0],
            ],
            index=self.DATES,
            columns=self.CODES,
        )
        config = OptimizeConfig(topn=2, single_max_dev=1.0)

        weights, meta = optimize_topn_equal_weight_all_periods(
            composite_panel=composite,
            benchmark_weights=self._benchmark_weights(),
            rebalance_dates=self.DATES,
            config=config,
        )

        assert weights.loc[self.DATES[0], "A"] == pytest.approx(0.5)
        assert weights.loc[self.DATES[0], "B"] == pytest.approx(0.5)
        assert weights.loc[self.DATES[0], ["C", "D", "E"]].sum() == pytest.approx(0.0)
        assert (meta["fallback_level"] == 2).all()
        assert (meta["solver_status"] == "topn_ew_forced").all()
        assert (meta["optimizer_mode"] == "topn_ew").all()
        assert (meta["cov_available"] == False).all()  # noqa: E712
        assert meta["constraint_compliant"].all()

    def test_forced_topn_respects_halt_and_limit_up_state(self):
        composite = pd.DataFrame(
            [
                [5.0, 4.0, 3.0, 2.0, 1.0],
                [1.0, 2.0, 5.0, 4.0, 3.0],
            ],
            index=self.DATES,
            columns=self.CODES,
        )
        config = OptimizeConfig(topn=2, single_max_dev=1.0)

        weights, meta = optimize_topn_equal_weight_all_periods(
            composite_panel=composite,
            benchmark_weights=self._benchmark_weights(),
            rebalance_dates=self.DATES,
            config=config,
            halt_dict={self.DATES[1]: {"A"}},
            limit_up_dict={self.DATES[1]: {"C"}},
        )

        second = weights.loc[self.DATES[1]]
        assert second["A"] == pytest.approx(0.5), "halted prior holding must stay locked"
        assert second["C"] == pytest.approx(0.0), "limit-up stock must not be newly bought"
        assert second[["D", "E"]].sum() == pytest.approx(0.5)
        assert meta.loc[self.DATES[1], "n_halt"] == 1
        assert meta.loc[self.DATES[1], "n_limit_up"] == 1
        assert bool(meta.loc[self.DATES[1], "constraint_compliant"]) is True


class TestOptimizeAllPeriodsConstraintCompliant:
    """F6-001: optimize_all_periods meta_df 必须含 constraint_compliant 字段。"""

    CODES = [f"S{i:02d}" for i in range(6)]
    N     = 6
    DATES = [
        pd.Timestamp("2022-01-31"),
        pd.Timestamp("2022-02-28"),
    ]

    def _make_inputs(self):
        bench = _make_benchmark(self.CODES)
        composite_panel = pd.DataFrame(
            {c: float(i) for i, c in enumerate(self.CODES)},
            index=self.DATES,
        )
        composite_panel.index.name = "rebalance_date"
        benchmark_weights = {T: bench for T in self.DATES}
        industry_map = pd.Series({c: "IND_A" for c in self.CODES})
        return composite_panel, benchmark_weights, industry_map

    def test_meta_has_constraint_compliant_column(self):
        """optimize_all_periods 返回的 meta_df 必须包含 constraint_compliant 列。"""
        composite_panel, benchmark_weights, industry_map = self._make_inputs()
        real_cov = _identity_cov(self.N, scale=2e-5)
        cov_dict = {T: real_cov for T in self.DATES}

        _, meta_df = optimize_all_periods(
            composite_panel=composite_panel,
            benchmark_weights=benchmark_weights,
            cov_dict=cov_dict,
            cov_codes=self.CODES,
            industry_map=industry_map,
            rebalance_dates=self.DATES,
            config=_loose_config(),
        )

        assert "constraint_compliant" in meta_df.columns, (
            "meta_df 必须包含 constraint_compliant 列（F6-001）"
        )

    def test_normal_run_constraint_compliant_true(self):
        """正常优化运行（无极端停牌/涨停情况）时 constraint_compliant 应全为 True。"""
        composite_panel, benchmark_weights, industry_map = self._make_inputs()
        real_cov = _identity_cov(self.N, scale=2e-5)
        cov_dict = {T: real_cov for T in self.DATES}

        _, meta_df = optimize_all_periods(
            composite_panel=composite_panel,
            benchmark_weights=benchmark_weights,
            cov_dict=cov_dict,
            cov_codes=self.CODES,
            industry_map=industry_map,
            rebalance_dates=self.DATES,
            config=_loose_config(),
        )

        all_compliant = meta_df["constraint_compliant"].all()
        assert all_compliant, (
            "正常运行时所有期 constraint_compliant 应为 True"
        )
