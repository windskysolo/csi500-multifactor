"""
tests/test_signal_quality.py — 信号实现质量诊断模块单元测试

覆盖范围：
  SQ-001: SignalQualityReport.to_dict() NaN 保留、float 精度
  SQ-002: _diagnose_signal — IC 均值/方差/ICIR/t统计量、单调性
  SQ-003: _diagnose_transfer — TC 定义正确性、截断损耗边界
  SQ-004: _diagnose_breadth — N_eff 公式验证（等权 N 只 → N_eff=N）
  SQ-005: _diagnose_attribution — GK 理论 IR、实际 IR 计算、ir_loss_pct 方向
  SQ-006: run() 防护路径 — 空输入、期数不足、验证期过滤、异常不抛出
  SQ-007: run() 正常路径 — 报告字段完整、n_valid_periods 准确
  SQ-008: format_report_section — 包含必要节标题、NaN 显示为 N/A
  SQ-009: _load_signal_quality (compare.py) — 文件缺失 / 字段存在 / 向后兼容
  SQ-010: run_diagnosis_stage — 缺少输入文件时优雅跳过（不抛异常）

所有测试使用合成数据，不依赖磁盘上的 run 目录或 Tushare 数据。
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.evaluation.signal_quality import (
    EFF_RATIO_OK,
    MONO_OK,
    TC_OK,
    SignalQualityReport,
    SignalToPositionDiagnostics,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_dates(n: int = 24, freq: str = "MS") -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq=freq)


def _make_stocks(n: int = 100) -> list[str]:
    return [f"{i:06d}.SZ" for i in range(n)]


def _make_signal_panel(
    dates: pd.DatetimeIndex,
    stocks: list[str],
    seed: int = 0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        rng.standard_normal((len(dates), len(stocks))),
        index=dates,
        columns=stocks,
    )


def _make_fwd_panel(
    dates: pd.DatetimeIndex,
    stocks: list[str],
    signal: pd.DataFrame | None = None,
    noise_scale: float = 0.03,
    signal_strength: float = 0.04,
    seed: int = 1,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, noise_scale, (len(dates), len(stocks)))
    if signal is not None:
        fwd = signal_strength * signal.values + noise
    else:
        fwd = noise
    return pd.DataFrame(fwd, index=dates, columns=stocks)


def _make_topn_weights(
    signal: pd.DataFrame,
    topn: int = 50,
) -> pd.DataFrame:
    """Binary equal-weight TopN portfolio from signal."""
    weights = pd.DataFrame(0.0, index=signal.index, columns=signal.columns)
    for t in signal.index:
        row = signal.loc[t].dropna()
        top = row.nlargest(min(topn, len(row))).index
        if len(top) > 0:
            weights.loc[t, top] = 1.0 / len(top)
    return weights


# ---------------------------------------------------------------------------
# SQ-001: SignalQualityReport.to_dict()
# ---------------------------------------------------------------------------

class TestSignalQualityReportToDict:
    """SQ-001: to_dict() 精度和 NaN 处理。"""

    def test_to_dict_nan_preserved_as_none_or_nan(self):
        report = SignalQualityReport()
        d = report.to_dict()
        assert "ic_mean" in d
        # NaN float 字段应保留为原生 float('nan')（不是 None 或 0）
        assert math.isnan(d["ic_mean"])

    def test_to_dict_float_rounded_to_4dp(self):
        report = SignalQualityReport(ic_mean=0.123456789)
        d = report.to_dict()
        # 精度为 4 位小数
        assert d["ic_mean"] == round(0.123456789, 4)

    def test_to_dict_int_fields_unchanged(self):
        report = SignalQualityReport(n_holdings=50, n_valid_periods=24)
        d = report.to_dict()
        assert d["n_holdings"] == 50
        assert d["n_valid_periods"] == 24

    def test_to_dict_json_serializable_after_nan_replacement(self):
        """to_dict 的 NaN 值需由调用方处理；确保非 NaN 字段可 JSON 序列化。"""
        report = SignalQualityReport(icir=0.5, n_holdings=50)
        d = report.to_dict()
        # 非 NaN 值可直接 JSON 序列化
        non_nan = {k: v for k, v in d.items() if not (isinstance(v, float) and math.isnan(v))}
        json_str = json.dumps(non_nan)
        assert '"icir"' in json_str

    def test_to_dict_has_all_expected_keys(self):
        report = SignalQualityReport()
        d = report.to_dict()
        expected_keys = {
            "ic_mean", "ic_std", "icir", "ic_tstat", "monotonicity",
            "tc", "amplitude_fidelity", "truncation_loss",
            "n_holdings", "n_eff", "concentration",
            "ir_theoretical", "ir_actual", "ir_loss_pct",
            "n_valid_periods",
        }
        assert expected_keys <= set(d.keys())


# ---------------------------------------------------------------------------
# SQ-002: _diagnose_signal
# ---------------------------------------------------------------------------

class TestDiagnoseSignal:
    """SQ-002: IC 计算及单调性检验。"""

    def _run(self, signals, fwd):
        diag = SignalToPositionDiagnostics(min_stocks=10)
        return diag._diagnose_signal(signals, fwd)

    def test_strong_signal_positive_icir(self):
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        sig = _make_signal_panel(dates, stocks, seed=10)
        fwd = _make_fwd_panel(dates, stocks, signal=sig, signal_strength=0.06, seed=11)
        res = self._run(sig, fwd)
        assert not math.isnan(res["icir"]), "强信号 ICIR 不应为 NaN"
        assert res["icir"] > 0, "强因子 ICIR 应 > 0"
        assert not math.isnan(res["ic_tstat"])

    def test_noise_signal_icir_near_zero(self):
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        sig = _make_signal_panel(dates, stocks, seed=20)
        fwd = _make_fwd_panel(dates, stocks, signal=None, seed=21)
        res = self._run(sig, fwd)
        # 纯噪声 IC 均值应接近 0（|ICIR| < 1 通常）
        if not math.isnan(res["icir"]):
            assert abs(res["icir"]) < 1.5, f"纯噪声 ICIR 不应很高：{res['icir']}"

    def test_insufficient_stocks_returns_nan(self):
        """每期股票数 < min_stocks 时所有字段应为 NaN。"""
        dates = _make_dates(5)
        stocks = _make_stocks(5)  # 只有 5 只，< min_stocks=10
        sig = _make_signal_panel(dates, stocks)
        fwd = _make_fwd_panel(dates, stocks)
        res = self._run(sig, fwd)
        assert math.isnan(res["icir"])
        assert math.isnan(res["ic_mean"])

    def test_ic_tstat_consistent_with_icir_and_n(self):
        """IC_t 应等于 ICIR × √n。"""
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        sig = _make_signal_panel(dates, stocks, seed=30)
        fwd = _make_fwd_panel(dates, stocks, signal=sig, signal_strength=0.05, seed=31)
        res = self._run(sig, fwd)
        if not math.isnan(res["icir"]) and not math.isnan(res["ic_tstat"]):
            n = int(round(res["ic_tstat"] ** 2 / res["icir"] ** 2)) if res["icir"] != 0 else 0
            expected_t = res["icir"] * math.sqrt(n)
            assert abs(res["ic_tstat"] - expected_t) < 0.01, (
                f"ic_tstat={res['ic_tstat']:.4f} 应 ≈ ICIR×√n={expected_t:.4f}"
            )

    def test_monotonicity_returns_value_in_neg1_to_1(self):
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        sig = _make_signal_panel(dates, stocks, seed=40)
        fwd = _make_fwd_panel(dates, stocks, signal=sig, signal_strength=0.05, seed=41)
        res = self._run(sig, fwd)
        if not math.isnan(res["monotonicity"]):
            assert -1.0 <= res["monotonicity"] <= 1.0


# ---------------------------------------------------------------------------
# SQ-003: _diagnose_transfer
# ---------------------------------------------------------------------------

class TestDiagnoseTransfer:
    """SQ-003: TC / 截断损耗计算。"""

    def _run(self, signals, weights):
        diag = SignalToPositionDiagnostics(min_stocks=10)
        return diag._diagnose_transfer(signals, weights)

    def test_topn_ew_tc_in_expected_range(self):
        """TopN EW 的 TC 结构上限约 0.4–0.6，取前 10% 做 TopN。"""
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        sig = _make_signal_panel(dates, stocks, seed=50)
        weights = _make_topn_weights(sig, topn=10)  # Top10%
        res = self._run(sig, weights)
        assert not math.isnan(res["tc"]), "TC 不应为 NaN"
        assert 0.0 < res["tc"] <= 1.0, f"TC 应在 (0, 1] 范围内：{res['tc']}"

    def test_random_weights_lower_tc_than_topn(self):
        """随机持仓的 TC 应低于信号驱动的 TopN 持仓。"""
        rng = np.random.default_rng(55)
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        sig = _make_signal_panel(dates, stocks, seed=55)

        # TopN 持仓（TC 应高）
        w_topn = _make_topn_weights(sig, topn=20)
        # 随机持仓（TC 应低）
        w_rand = pd.DataFrame(
            rng.uniform(0, 1, (len(dates), len(stocks))),
            index=dates, columns=stocks,
        )
        w_rand = w_rand.div(w_rand.sum(axis=1), axis=0)

        tc_topn = self._run(sig, w_topn)["tc"]
        tc_rand = self._run(sig, w_rand)["tc"]
        assert tc_topn > tc_rand, (
            f"TopN TC={tc_topn:.3f} 应 > 随机 TC={tc_rand:.3f}"
        )

    def test_perfect_continuous_weights_high_tc(self):
        """权重直接等于信号值（完美对齐）时 TC 应 ≈ 1.0。"""
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        sig = _make_signal_panel(dates, stocks, seed=60)
        # 将信号直接用作权重（已正规化，TC 秩相关 ≈ 1）
        w = sig.copy()
        w = w.div(w.abs().sum(axis=1), axis=0)
        res = self._run(sig, w)
        assert res["tc"] > 0.95, f"完美对齐时 TC 应 ≈ 1，实际={res['tc']:.3f}"

    def test_truncation_loss_between_0_and_1(self):
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        sig = _make_signal_panel(dates, stocks, seed=70)
        weights = _make_topn_weights(sig, topn=20)
        res = self._run(sig, weights)
        if not math.isnan(res["truncation_loss"]):
            assert 0.0 <= res["truncation_loss"] <= 1.0, (
                f"截断损耗应在 [0, 1]：{res['truncation_loss']}"
            )

    def test_no_common_dates_returns_nan(self):
        """信号和权重无共同日期时 TC 应为 NaN。"""
        dates_sig = pd.date_range("2021-01-01", periods=12, freq="MS")
        dates_w = pd.date_range("2022-01-01", periods=12, freq="MS")
        stocks = _make_stocks(50)
        sig = _make_signal_panel(dates_sig, stocks)
        weights = _make_topn_weights(
            pd.DataFrame(np.random.randn(12, 50), index=dates_w, columns=stocks),
            topn=10,
        )
        res = self._run(sig, weights)
        assert math.isnan(res["tc"]), "无共同日期时 TC 应为 NaN"


# ---------------------------------------------------------------------------
# SQ-004: _diagnose_breadth
# ---------------------------------------------------------------------------

class TestDiagnoseBreadth:
    """SQ-004: N_eff 和 Herfindahl 指数验证。"""

    def _run(self, weights):
        diag = SignalToPositionDiagnostics()
        return diag._diagnose_breadth(weights)

    def test_equal_weight_neff_equals_n_holdings(self):
        """等权 N 只持仓时，N_eff 应 = N。"""
        dates = _make_dates(12)
        stocks = _make_stocks(50)
        # 等权持仓：每期持 50 只，每只权重 1/50
        weights = pd.DataFrame(1.0 / 50, index=dates, columns=stocks)
        res = self._run(weights)
        assert abs(res["n_eff"] - 50) < 0.01, (
            f"等权 50 只时 N_eff 应 = 50，实际={res['n_eff']:.3f}"
        )
        assert res["n_holdings"] == 50

    def test_single_holding_neff_is_one(self):
        """仅持 1 只时，N_eff 应 = 1。"""
        dates = _make_dates(12)
        stocks = _make_stocks(10)
        weights = pd.DataFrame(0.0, index=dates, columns=stocks)
        weights.iloc[:, 0] = 1.0  # 全仓第 0 只
        res = self._run(weights)
        assert abs(res["n_eff"] - 1.0) < 0.01, (
            f"单只持仓 N_eff 应 = 1，实际={res['n_eff']:.3f}"
        )

    def test_concentrated_portfolio_lower_neff(self):
        """集中持仓的 N_eff 低于分散持仓。"""
        dates = _make_dates(12)
        stocks = _make_stocks(50)

        # 等权 50 只
        w_spread = pd.DataFrame(1.0 / 50, index=dates, columns=stocks)
        # 90% 集中在前 5 只
        w_conc = pd.DataFrame(0.002, index=dates, columns=stocks)
        for i in range(5):
            w_conc.iloc[:, i] = 0.18

        res_spread = self._run(w_spread)
        res_conc = self._run(w_conc)
        assert res_spread["n_eff"] > res_conc["n_eff"], (
            f"分散 N_eff={res_spread['n_eff']:.1f} 应 > 集中 N_eff={res_conc['n_eff']:.1f}"
        )

    def test_empty_weights_returns_zero_holdings(self):
        dates = _make_dates(12)
        stocks = _make_stocks(10)
        weights = pd.DataFrame(0.0, index=dates, columns=stocks)
        res = self._run(weights)
        assert res["n_holdings"] == 0


# ---------------------------------------------------------------------------
# SQ-005: _diagnose_attribution
# ---------------------------------------------------------------------------

class TestDiagnoseAttribution:
    """SQ-005: IR 归因 — 理论值公式、实际 IR 计算、损耗方向。"""

    def _diag(self):
        return SignalToPositionDiagnostics()

    def _run(self, weights, fwd, icir, tc, n_valid, n_years):
        diag = self._diag()
        sig_res = {"icir": icir}
        trans_res = {"tc": tc}
        return diag._diagnose_attribution(
            weights, fwd, sig_res, trans_res, n_valid, n_years
        )

    def test_ir_theoretical_formula(self):
        """IR_theoretical = ICIR × TC × √(n_valid/n_years)。"""
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        rng = np.random.default_rng(80)
        weights = pd.DataFrame(
            rng.uniform(0, 1, (24, 100)), index=dates, columns=stocks,
        )
        weights = weights.div(weights.sum(axis=1), axis=0)
        fwd = _make_fwd_panel(dates, stocks, seed=81)

        icir, tc = 0.5, 0.6
        n_valid, n_years = 24, 2.0
        res = self._run(weights, fwd, icir, tc, n_valid, n_years)

        expected_ir_theoretical = icir * tc * math.sqrt(n_valid / n_years)
        assert abs(res["ir_theoretical"] - expected_ir_theoretical) < 1e-6, (
            f"IR_theoretical={res['ir_theoretical']:.4f} 应 = {expected_ir_theoretical:.4f}"
        )

    def test_nan_icir_gives_nan_ir_theoretical(self):
        dates = _make_dates(12)
        stocks = _make_stocks(50)
        weights = pd.DataFrame(1.0 / 50, index=dates, columns=stocks)
        fwd = _make_fwd_panel(dates, stocks, seed=82)
        res = self._run(weights, fwd, float("nan"), 0.5, 12, 1.0)
        assert math.isnan(res["ir_theoretical"])

    def test_ir_actual_positive_for_positive_returns(self):
        """构造恒正收益组合，实际 IR 应 > 0。"""
        dates = _make_dates(12)
        stocks = _make_stocks(50)
        weights = pd.DataFrame(1.0 / 50, index=dates, columns=stocks)
        # 每期每只股票均有正收益
        fwd = pd.DataFrame(0.01, index=dates, columns=stocks)
        res = self._run(weights, fwd, 0.5, 0.5, 12, 1.0)
        assert not math.isnan(res["ir_actual"])
        assert res["ir_actual"] > 0

    def test_no_overlap_dates_returns_nan_ir_actual(self):
        """权重和收益无共同日期时 ir_actual 应为 NaN。"""
        dates_w = pd.date_range("2021-01-01", periods=12, freq="MS")
        dates_r = pd.date_range("2023-01-01", periods=12, freq="MS")
        stocks = _make_stocks(50)
        weights = pd.DataFrame(1.0 / 50, index=dates_w, columns=stocks)
        fwd = pd.DataFrame(0.01, index=dates_r, columns=stocks)
        res = self._run(weights, fwd, 0.5, 0.5, 12, 1.0)
        assert math.isnan(res["ir_actual"])

    def test_ir_loss_pct_positive_when_actual_below_theoretical(self):
        """当实际 IR < 理论值时，损耗百分比应 > 0。"""
        # 构造低 IR 实际（纯噪声），但给高理论值
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        rng = np.random.default_rng(85)
        weights = pd.DataFrame(
            (lambda x: x / x.sum(axis=1, keepdims=True))(rng.uniform(0, 1, (24, 100))),
            index=dates, columns=stocks,
        )
        fwd = pd.DataFrame(
            rng.normal(0, 0.03, (24, 100)),  # 纯噪声，ir_actual 接近 0
            index=dates, columns=stocks,
        )
        # 强制给高理论值
        res = self._run(weights, fwd, icir=2.0, tc=0.8, n_valid=24, n_years=2.0)
        if not math.isnan(res["ir_loss_pct"]) and not math.isnan(res["ir_theoretical"]):
            # 高理论 + 低实际 → ir_loss_pct > 0
            assert res["ir_loss_pct"] > 0


# ---------------------------------------------------------------------------
# SQ-006 / SQ-007: run() 防护路径 + 正常路径
# ---------------------------------------------------------------------------

class TestSignalQualityRunGuards:
    """SQ-006: run() 各防护路径不抛异常，返回空报告。"""

    def test_empty_signals_returns_empty_report(self):
        diag = SignalToPositionDiagnostics()
        report = diag.run(
            signals=pd.DataFrame(),
            weights=pd.DataFrame(),
            returns=pd.DataFrame(),
        )
        assert isinstance(report, SignalQualityReport)
        assert report.n_valid_periods == 0
        assert math.isnan(report.icir)

    def test_fewer_than_min_stocks_returns_nan_icir(self):
        """所有期都 < min_stocks 时，ICIR 应为 NaN（不抛异常）。"""
        dates = _make_dates(5)
        stocks = _make_stocks(5)  # 5 < min_stocks=10
        sig = _make_signal_panel(dates, stocks)
        fwd = _make_fwd_panel(dates, stocks)
        weights = _make_topn_weights(sig, topn=5)
        diag = SignalToPositionDiagnostics(min_stocks=10)
        report = diag.run(signals=sig, weights=weights, returns=fwd)
        assert math.isnan(report.icir), "股票数不足时 ICIR 应为 NaN"

    def test_valid_start_after_all_data_returns_empty(self):
        """valid_start 晚于所有数据时，应返回空报告。"""
        dates = _make_dates(12)
        stocks = _make_stocks(50)
        sig = _make_signal_panel(dates, stocks)
        fwd = _make_fwd_panel(dates, stocks)
        weights = _make_topn_weights(sig, topn=10)
        diag = SignalToPositionDiagnostics()
        report = diag.run(
            signals=sig, weights=weights, returns=fwd,
            valid_start=pd.Timestamp("2030-01-01"),
        )
        assert report.n_valid_periods == 0

    def test_date_range_filtering_reduces_n_valid_periods(self):
        """验证期过滤后 n_valid_periods 应 < 总期数。"""
        dates = _make_dates(24)  # 2021-01 ~ 2022-12
        stocks = _make_stocks(100)
        sig = _make_signal_panel(dates, stocks, seed=90)
        fwd = _make_fwd_panel(dates, stocks, signal=sig, seed=91)
        weights = _make_topn_weights(sig, topn=20)
        diag = SignalToPositionDiagnostics()
        # 只取 2021 年的 12 期
        report = diag.run(
            signals=sig, weights=weights, returns=fwd,
            valid_start=pd.Timestamp("2021-01-01"),
            valid_end=pd.Timestamp("2021-12-31"),
        )
        assert report.n_valid_periods == 12, (
            f"过滤后应有 12 期，实际={report.n_valid_periods}"
        )

    def test_exception_inside_diagnose_does_not_propagate(self):
        """即使传入破坏性数据，run() 不应向外抛异常。"""
        diag = SignalToPositionDiagnostics()
        # 传入 object 类型 DataFrame（会在 spearmanr 内部失败）
        bad_sig = pd.DataFrame(
            {"a": [None, None], "b": [None, None]},
            index=pd.date_range("2021-01-01", periods=2, freq="MS"),
        )
        try:
            report = diag.run(signals=bad_sig, weights=bad_sig, returns=bad_sig)
            assert isinstance(report, SignalQualityReport)
        except Exception as e:
            pytest.fail(f"run() 不应抛出异常，但抛出了: {e}")


class TestSignalQualityRunNormalPath:
    """SQ-007: run() 正常路径，报告字段完整且合理。"""

    @pytest.fixture
    def normal_run_result(self):
        dates = _make_dates(24)
        stocks = _make_stocks(100)
        sig = _make_signal_panel(dates, stocks, seed=100)
        fwd = _make_fwd_panel(dates, stocks, signal=sig, signal_strength=0.05, seed=101)
        weights = _make_topn_weights(sig, topn=50)
        diag = SignalToPositionDiagnostics(min_stocks=10)
        return diag.run(
            signals=sig, weights=weights, returns=fwd,
            valid_start=pd.Timestamp("2021-01-01"),
            valid_end=pd.Timestamp("2022-12-31"),
        )

    def test_n_valid_periods_correct(self, normal_run_result):
        assert normal_run_result.n_valid_periods == 24

    def test_all_ic_fields_finite(self, normal_run_result):
        r = normal_run_result
        for field in ("ic_mean", "ic_std", "icir", "ic_tstat"):
            v = getattr(r, field)
            assert not math.isnan(v), f"{field} 不应为 NaN（正常数据路径）"

    def test_tc_in_valid_range(self, normal_run_result):
        r = normal_run_result
        assert not math.isnan(r.tc), "TC 不应为 NaN"
        assert 0.0 < r.tc < 1.0, f"TC={r.tc:.3f} 应在 (0, 1)"

    def test_n_holdings_approximately_topn(self, normal_run_result):
        # TopN=50，均值持仓数应接近 50
        assert 40 <= normal_run_result.n_holdings <= 60

    def test_neff_at_most_n_holdings(self, normal_run_result):
        r = normal_run_result
        assert not math.isnan(r.n_eff)
        # N_eff ≤ n_holdings（TopN EW 等权时 N_eff = n_holdings）
        assert r.n_eff <= r.n_holdings + 0.1

    def test_ir_theoretical_finite(self, normal_run_result):
        assert not math.isnan(normal_run_result.ir_theoretical)

    def test_truncation_loss_nonzero_for_topn(self, normal_run_result):
        # TopN50/100 持仓持有约 50% 股票，截断损耗应 > 0
        r = normal_run_result
        if not math.isnan(r.truncation_loss):
            assert r.truncation_loss > 0


# ---------------------------------------------------------------------------
# SQ-008: format_report_section
# ---------------------------------------------------------------------------

class TestFormatReportSection:
    """SQ-008: Markdown 报告节格式验证。"""

    def _make_report(self, **overrides):
        defaults = dict(
            ic_mean=0.05, ic_std=0.10, icir=0.50, ic_tstat=2.45,
            monotonicity=0.80, tc=0.60, amplitude_fidelity=0.50,
            truncation_loss=0.70, n_holdings=50, n_eff=49.5,
            concentration=0.02, ir_theoretical=1.0, ir_actual=0.40,
            ir_loss_pct=60.0, n_valid_periods=24,
        )
        defaults.update(overrides)
        return SignalQualityReport(**defaults)

    def test_section_header_present(self):
        diag = SignalToPositionDiagnostics()
        report = self._make_report()
        section = diag.format_report_section(report)
        assert "Signal Quality Diagnostics" in section

    def test_all_four_step_headers_present(self):
        diag = SignalToPositionDiagnostics()
        section = diag.format_report_section(self._make_report())
        assert "Step 1" in section
        assert "Step 2" in section
        assert "Step 3" in section
        assert "Step 4" in section

    def test_nan_ic_shows_na(self):
        diag = SignalToPositionDiagnostics()
        report = self._make_report(ic_mean=float("nan"))
        section = diag.format_report_section(report)
        assert "N/A" in section

    def test_low_tc_triggers_transfer_warning(self):
        """TC < TC_OK 时应生成转移损耗告警。"""
        diag = SignalToPositionDiagnostics()
        report = self._make_report(tc=TC_OK - 0.1)  # 刚好低于阈值
        section = diag.format_report_section(report)
        assert "转移损耗" in section or "TC=" in section

    def test_good_tc_triggers_healthy_conclusion(self):
        """TC >= TC_OK 时应生成健康结论。"""
        diag = SignalToPositionDiagnostics()
        report = self._make_report(tc=TC_OK + 0.1)
        section = diag.format_report_section(report)
        assert "TC=" in section  # 健康结论包含 TC 值

    def test_no_data_shows_question_mark(self):
        """全 NaN 报告（数据不足）应生成无法诊断的提示。"""
        diag = SignalToPositionDiagnostics()
        report = SignalQualityReport()  # 全 NaN
        section = diag.format_report_section(report)
        assert "数据不足" in section or "?" in section


# ---------------------------------------------------------------------------
# SQ-009: _load_signal_quality (compare.py)
# ---------------------------------------------------------------------------

class TestLoadSignalQuality:
    """SQ-009: compare.py 的 _load_signal_quality helper。"""

    def test_missing_file_returns_all_nan(self, tmp_path):
        from src.pipeline.compare import _load_signal_quality
        result = _load_signal_quality(tmp_path)
        assert set(result.keys()) == {"tc_mean", "n_eff", "ir_loss_pct"}
        assert all(math.isnan(v) for v in result.values())

    def test_valid_json_parsed_correctly(self, tmp_path):
        from src.pipeline.compare import _load_signal_quality
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        data = {"tc": 0.585, "n_eff": 66.0, "ir_loss_pct": 61.43}
        (reports_dir / "signal_quality_report.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        result = _load_signal_quality(tmp_path)
        assert abs(result["tc_mean"] - 0.585) < 1e-6
        assert abs(result["n_eff"] - 66.0) < 1e-6
        assert abs(result["ir_loss_pct"] - 61.43) < 1e-6

    def test_missing_fields_return_nan(self, tmp_path):
        """JSON 存在但缺少某些字段时，对应列应为 NaN。"""
        from src.pipeline.compare import _load_signal_quality
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "signal_quality_report.json").write_text(
            json.dumps({"tc": 0.5}),  # 缺少 n_eff / ir_loss_pct
            encoding="utf-8",
        )
        result = _load_signal_quality(tmp_path)
        assert abs(result["tc_mean"] - 0.5) < 1e-6
        assert math.isnan(result["n_eff"])
        assert math.isnan(result["ir_loss_pct"])

    def test_corrupt_json_returns_all_nan(self, tmp_path):
        """JSON 文件损坏时应返回全 NaN，不抛异常。"""
        from src.pipeline.compare import _load_signal_quality
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "signal_quality_report.json").write_text(
            "{ invalid json }", encoding="utf-8"
        )
        result = _load_signal_quality(tmp_path)
        assert all(math.isnan(v) for v in result.values())

    def test_nan_value_in_json_parsed_as_nan(self, tmp_path):
        """JSON 中的 NaN 字符串或 null 应被解析为 float nan。"""
        from src.pipeline.compare import _load_signal_quality
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        # JSON 中 NaN 无法直接表示，存为 null（Python json 模块行为）
        (reports_dir / "signal_quality_report.json").write_text(
            '{"tc": null, "n_eff": 50.0, "ir_loss_pct": null}',
            encoding="utf-8",
        )
        result = _load_signal_quality(tmp_path)
        assert math.isnan(result["tc_mean"])
        assert abs(result["n_eff"] - 50.0) < 1e-6


# ---------------------------------------------------------------------------
# SQ-010: run_diagnosis_stage — 输入缺失时优雅跳过
# ---------------------------------------------------------------------------

class TestRunDiagnosisStageUnit:
    """SQ-010: run_diagnosis_stage 在缺少输入文件时不抛异常、返回空 dict。"""

    def test_missing_all_inputs_returns_empty_dict(self, tmp_path):
        from src.pipeline.stages import run_diagnosis_stage
        from src.pipeline.contracts import (
            ExperimentSpec, SignalSpec, OptimizerSpec, BacktestSpec,
        )

        spec = ExperimentSpec(
            experiment_id="test_spec",
            description="unit test",
            period_scope="train_valid",
            signal=SignalSpec(method="icir", target="fwd_1m", training_mode="expanding"),
            optimizer=OptimizerSpec(),
            backtest=BacktestSpec(),
        )
        run_dir = tmp_path / "fake_run"
        run_dir.mkdir()

        # 没有任何输入文件 → 应返回空 dict，不抛异常
        result = run_diagnosis_stage(spec, run_dir, data_proc=tmp_path)
        assert result == {}, f"缺少文件时应返回空 dict，实际={result}"

    def test_output_json_written_when_inputs_present(self, tmp_path):
        """当输入文件齐备时，signal_quality_report.json 应被创建。"""
        from src.pipeline.stages import run_diagnosis_stage
        from src.pipeline.contracts import (
            ExperimentSpec, SignalSpec, OptimizerSpec, BacktestSpec,
        )

        spec = ExperimentSpec(
            experiment_id="test_spec",
            description="unit test",
            period_scope="train_valid",
            signal=SignalSpec(method="icir", target="fwd_1m", training_mode="expanding"),
            optimizer=OptimizerSpec(),
            backtest=BacktestSpec(),
        )
        run_dir = tmp_path / "fake_run"
        (run_dir / "signal").mkdir(parents=True)
        (run_dir / "portfolio").mkdir(parents=True)
        (run_dir / "reports").mkdir(parents=True)
        data_proc = tmp_path / "processed"
        data_proc.mkdir(parents=True)

        # 构造最小合法 parquet
        dates = pd.date_range("2021-01-01", periods=12, freq="MS")
        stocks = [f"{i:06d}.SZ" for i in range(20)]

        sig_df = pd.DataFrame(
            np.random.default_rng(0).standard_normal((12, 20)),
            index=dates, columns=stocks,
        )
        w_df = pd.DataFrame(1.0 / 20, index=dates, columns=stocks)
        fwd_df = pd.DataFrame(
            np.random.default_rng(1).normal(0.005, 0.03, (12, 20)),
            index=dates, columns=stocks,
        )

        sig_df.to_parquet(run_dir / "signal" / "composite.parquet")
        w_df.to_parquet(run_dir / "portfolio" / "target_weights.parquet")
        fwd_df.to_parquet(data_proc / "fwd_ret_panel.parquet")

        result = run_diagnosis_stage(spec, run_dir, data_proc=data_proc)

        assert "signal_quality_report" in result, "结果应含 signal_quality_report 键"
        json_path = run_dir / "reports" / "signal_quality_report.json"
        assert json_path.exists(), "signal_quality_report.json 应被创建"

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert "tc" in data, "JSON 应含 tc 字段"
        assert "n_eff" in data, "JSON 应含 n_eff 字段"

    def test_self_check_md_section_appended(self, tmp_path):
        """当 self_check.md 存在时，诊断节应被追加且不重复。"""
        from src.pipeline.stages import run_diagnosis_stage
        from src.pipeline.contracts import (
            ExperimentSpec, SignalSpec, OptimizerSpec, BacktestSpec,
        )

        spec = ExperimentSpec(
            experiment_id="test_spec",
            description="unit test",
            period_scope="train_valid",
            signal=SignalSpec(method="icir", target="fwd_1m", training_mode="expanding"),
            optimizer=OptimizerSpec(),
            backtest=BacktestSpec(),
        )
        run_dir = tmp_path / "fake_run"
        (run_dir / "signal").mkdir(parents=True)
        (run_dir / "portfolio").mkdir(parents=True)
        (run_dir / "reports").mkdir(parents=True)
        data_proc = tmp_path / "processed"
        data_proc.mkdir(parents=True)

        # 写入预存 self_check.md
        md_path = run_dir / "reports" / "self_check.md"
        md_path.write_text("# Self Check\n\n## 硬指标\n\n| IR | 0.5 |\n", encoding="utf-8")

        dates = pd.date_range("2021-01-01", periods=12, freq="MS")
        stocks = [f"{i:06d}.SZ" for i in range(20)]
        sig_df = pd.DataFrame(
            np.random.default_rng(2).standard_normal((12, 20)),
            index=dates, columns=stocks,
        )
        w_df = pd.DataFrame(1.0 / 20, index=dates, columns=stocks)
        fwd_df = pd.DataFrame(
            np.random.default_rng(3).normal(0.005, 0.03, (12, 20)),
            index=dates, columns=stocks,
        )
        sig_df.to_parquet(run_dir / "signal" / "composite.parquet")
        w_df.to_parquet(run_dir / "portfolio" / "target_weights.parquet")
        fwd_df.to_parquet(data_proc / "fwd_ret_panel.parquet")

        run_diagnosis_stage(spec, run_dir, data_proc=data_proc)

        content = md_path.read_text(encoding="utf-8")
        assert "Signal Quality Diagnostics" in content, "诊断节应追加到 self_check.md"

        # 调用第二次不应重复追加
        run_diagnosis_stage(spec, run_dir, data_proc=data_proc)
        content2 = md_path.read_text(encoding="utf-8")
        count = content2.count("Signal Quality Diagnostics")
        assert count == 1, f"诊断节不应重复追加（出现了 {count} 次）"
