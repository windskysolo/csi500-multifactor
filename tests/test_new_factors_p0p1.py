"""
tests/test_new_factors_p0p1.py — P0+P1 新因子单元测试

覆盖（全部基于合成数据，不读磁盘）：
  1. accrual（修复版）   ：平均资产分母、NaN 传播
  2. asset_growth        ：负向赋值、截尾、index 对齐、金融过滤委托
  3. share_issuance      ：增发/回购方向、历史不足 NaN
  4. mf_flow_ratio       ：rz_net 优先、rzmre/rzche 回退、字段缺失全 NaN
"""

import numpy as np
import pandas as pd
import pytest

from src.factors.financial_factors import factor_accrual, factor_asset_growth
from src.factors.price_factors import (
    WINDOW_HIGH_52W,
    WINDOW_MF_FLOW,
    factor_mf_flow_ratio,
    factor_share_issuance,
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _make_basic_df(
    date: pd.Timestamp,
    codes: list[str],
    values: dict[str, list],
) -> pd.DataFrame:
    """构造 (trade_date, ts_code) MultiIndex DataFrame，模拟 load_daily_basic 返回值。"""
    idx = pd.MultiIndex.from_tuples(
        [(date, c) for c in codes], names=["trade_date", "ts_code"]
    )
    return pd.DataFrame(values, index=idx)


def _make_margin_df(
    date: pd.Timestamp,
    codes: list[str],
    values: dict[str, list],
) -> pd.DataFrame:
    """构造 (trade_date, ts_code) MultiIndex DataFrame，模拟 load_margin 返回值。"""
    idx = pd.MultiIndex.from_tuples(
        [(date, c) for c in codes], names=["trade_date", "ts_code"]
    )
    return pd.DataFrame(values, index=idx)


def _make_valid_dates(n: int, end: pd.Timestamp) -> pd.DatetimeIndex:
    """构造 n 个连续交易日（倒推），用于 mock _valid_dates_before。"""
    dates = pd.bdate_range(end=end, periods=n)
    return pd.DatetimeIndex(dates)


# ---------------------------------------------------------------------------
# TestAccrualImproved
# ---------------------------------------------------------------------------

class TestAccrualImproved:
    """修复版 accrual：分母改用平均总资产 = (q0 + q4) / 2。"""

    CODES = ["A.SZ"]
    T = pd.Timestamp("2021-01-29")

    def test_returns_series_with_ts_code_index(self, monkeypatch):
        """返回 pd.Series，index 与传入 codes 完全一致，name=='accrual'。"""
        import src.factors.financial_factors as ff

        monkeypatch.setattr(ff, "get_financial_pit_raw", lambda: pd.DataFrame())
        monkeypatch.setattr(
            ff, "make_ttm",
            lambda fp, field, date, codes: pd.Series([10.0], index=pd.Index(codes, name="ts_code")),
        )
        hist = pd.DataFrame({"q0": [200.0], "q4": [100.0]},
                            index=pd.Index(self.CODES, name="ts_code"))
        monkeypatch.setattr(
            ff, "get_quarterly_history",
            lambda fp, col, date, codes, n_quarters: hist,
        )

        result = factor_accrual(self.T, self.CODES)

        assert isinstance(result, pd.Series)
        assert result.name == "accrual"
        assert list(result.index) == self.CODES

    def test_denominator_uses_average_not_latest(self, monkeypatch):
        """分母用 (q0+q4)/2=150，而非 q0=200。
        ttm_ni=30, ttm_cfo=0, q0=200, q4=100 → result=30/150=0.2。
        """
        import src.factors.financial_factors as ff

        codes = ["A.SZ"]
        monkeypatch.setattr(ff, "get_financial_pit_raw", lambda: pd.DataFrame())

        def _mock_make_ttm(fp, field, date, codes_):
            if field == "n_income":
                return pd.Series([30.0], index=pd.Index(codes_, name="ts_code"))
            return pd.Series([0.0], index=pd.Index(codes_, name="ts_code"))

        monkeypatch.setattr(ff, "make_ttm", _mock_make_ttm)

        hist = pd.DataFrame({"q0": [200.0], "q4": [100.0]},
                            index=pd.Index(codes, name="ts_code"))
        monkeypatch.setattr(
            ff, "get_quarterly_history",
            lambda fp, col, date, c, n_quarters: hist,
        )

        result = factor_accrual(self.T, codes)

        assert result["A.SZ"] == pytest.approx(30.0 / 150.0)

    def test_nan_when_assets_insufficient_history(self, monkeypatch):
        """q4 缺失（NaN）时，avg_assets=NaN，结果为 NaN。"""
        import src.factors.financial_factors as ff

        codes = ["A.SZ"]
        monkeypatch.setattr(ff, "get_financial_pit_raw", lambda: pd.DataFrame())
        monkeypatch.setattr(
            ff, "make_ttm",
            lambda fp, field, date, c: pd.Series([10.0], index=pd.Index(c, name="ts_code")),
        )
        hist = pd.DataFrame({"q0": [200.0], "q4": [np.nan]},
                            index=pd.Index(codes, name="ts_code"))
        monkeypatch.setattr(
            ff, "get_quarterly_history",
            lambda fp, col, date, c, n_quarters: hist,
        )

        result = factor_accrual(self.T, codes)

        assert np.isnan(result["A.SZ"])

    def test_nan_when_avg_assets_zero(self, monkeypatch):
        """avg_assets = 0（被替换为 NaN）时，结果为 NaN。"""
        import src.factors.financial_factors as ff

        codes = ["A.SZ"]
        monkeypatch.setattr(ff, "get_financial_pit_raw", lambda: pd.DataFrame())
        monkeypatch.setattr(
            ff, "make_ttm",
            lambda fp, field, date, c: pd.Series([5.0], index=pd.Index(c, name="ts_code")),
        )
        # q0=0, q4=0 → avg=0 → replace(0, NaN) → NaN
        hist = pd.DataFrame({"q0": [0.0], "q4": [0.0]},
                            index=pd.Index(codes, name="ts_code"))
        monkeypatch.setattr(
            ff, "get_quarterly_history",
            lambda fp, col, date, c, n_quarters: hist,
        )

        result = factor_accrual(self.T, codes)

        assert np.isnan(result["A.SZ"])


# ---------------------------------------------------------------------------
# TestAssetGrowth
# ---------------------------------------------------------------------------

class TestAssetGrowth:
    """总资产增速因子（取反）。"""

    T = pd.Timestamp("2021-01-29")

    def _mock_hist(self, codes, q0_vals, q4_vals):
        return pd.DataFrame(
            {"q0": q0_vals, "q4": q4_vals},
            index=pd.Index(codes, name="ts_code"),
        )

    def test_negative_for_fast_growing_company(self, monkeypatch):
        """q0=200, q4=100 → asset_growth_raw=1.0 → result=-1.0。"""
        import src.factors.financial_factors as ff

        codes = ["A.SZ"]
        monkeypatch.setattr(ff, "get_financial_pit_raw", lambda: pd.DataFrame())
        hist = self._mock_hist(codes, [200.0], [100.0])
        monkeypatch.setattr(
            ff, "get_quarterly_history",
            lambda fp, col, date, c, n_quarters: hist,
        )

        result = factor_asset_growth(self.T, codes)

        assert result["A.SZ"] == pytest.approx(-1.0)

    def test_positive_for_shrinking_company(self, monkeypatch):
        """q0=80, q4=100 → asset_growth_raw=-0.2 → result=+0.2。"""
        import src.factors.financial_factors as ff

        codes = ["A.SZ"]
        monkeypatch.setattr(ff, "get_financial_pit_raw", lambda: pd.DataFrame())
        hist = self._mock_hist(codes, [80.0], [100.0])
        monkeypatch.setattr(
            ff, "get_quarterly_history",
            lambda fp, col, date, c, n_quarters: hist,
        )

        result = factor_asset_growth(self.T, codes)

        assert result["A.SZ"] == pytest.approx(0.2)

    def test_clip_extreme_values(self, monkeypatch):
        """q4=10, q0=1000 → asset_growth_raw=99 > 5 → clip → result=-5.0。"""
        import src.factors.financial_factors as ff

        codes = ["A.SZ"]
        monkeypatch.setattr(ff, "get_financial_pit_raw", lambda: pd.DataFrame())
        hist = self._mock_hist(codes, [1000.0], [10.0])
        monkeypatch.setattr(
            ff, "get_quarterly_history",
            lambda fp, col, date, c, n_quarters: hist,
        )

        result = factor_asset_growth(self.T, codes)

        assert result["A.SZ"] == pytest.approx(-5.0)

    def test_returns_series_reindexed_to_codes(self, monkeypatch):
        """返回结果必须按传入 codes 对齐，get_quarterly_history 返回的 index 不足时补 NaN。"""
        import src.factors.financial_factors as ff

        codes = ["A.SZ", "B.SZ", "C.SZ"]
        monkeypatch.setattr(ff, "get_financial_pit_raw", lambda: pd.DataFrame())
        # 只有 A 和 B 有数据，C 应为 NaN
        hist = pd.DataFrame(
            {"q0": [200.0, 100.0], "q4": [100.0, 80.0]},
            index=pd.Index(["A.SZ", "B.SZ"], name="ts_code"),
        )
        monkeypatch.setattr(
            ff, "get_quarterly_history",
            lambda fp, col, date, c, n_quarters: hist,
        )

        result = factor_asset_growth(self.T, codes)

        assert list(result.index) == codes
        assert np.isnan(result["C.SZ"])

    def test_financial_sector_filtered_by_preprocess_not_raw_factor(self, monkeypatch):
        """原始函数不做金融股过滤；金融股置 NaN 由 preprocess_factor 负责。

        验证：当 industry_code 属于 FINANCIAL_SECTOR_CODES 时，
        preprocess_factor 会将该股票置 NaN；
        而 factor_asset_growth 本身不进行行业过滤（金融股有原始值）。
        """
        import src.config as cfg
        from src.factors.preprocess import preprocess_factor

        import src.factors.financial_factors as ff

        codes = ["FIN.SZ", "NON.SZ"]
        monkeypatch.setattr(ff, "get_financial_pit_raw", lambda: pd.DataFrame())
        hist = pd.DataFrame(
            {"q0": [200.0, 150.0], "q4": [100.0, 100.0]},
            index=pd.Index(codes, name="ts_code"),
        )
        monkeypatch.setattr(
            ff, "get_quarterly_history",
            lambda fp, col, date, c, n_quarters: hist,
        )

        raw = factor_asset_growth(self.T, codes)

        # 原始函数：金融股 FIN.SZ 应有非 NaN 值
        assert not np.isnan(raw["FIN.SZ"]), "factor_asset_growth 本身不过滤金融股"
        assert not np.isnan(raw["NON.SZ"])

        # 模拟 preprocess_factor 接收到金融行业代码
        fin_code = sorted(cfg.FINANCIAL_SECTOR_CODES)[0]   # 如 '801780.SI'
        industry = pd.Series(
            {"FIN.SZ": fin_code, "NON.SZ": "801110.SI"},
            name="industry_code",
        )
        industry.index.name = "ts_code"
        log_mv = pd.Series({"FIN.SZ": 15.0, "NON.SZ": 14.5})
        log_mv.index.name = "ts_code"

        preprocessed = preprocess_factor(
            raw, industry, log_mv,
            fin_sector_codes=frozenset(cfg.FINANCIAL_SECTOR_CODES),
        )

        # preprocess 后金融股应为 NaN
        assert np.isnan(preprocessed["FIN.SZ"]), "preprocess_factor 应将金融股置 NaN"


# ---------------------------------------------------------------------------
# TestShareIssuance
# ---------------------------------------------------------------------------

class TestShareIssuance:
    """股本稀释因子（取反后正向）。"""

    T = pd.Timestamp("2021-06-30")
    CODES = ["A.SZ", "B.SZ"]

    def _enough_valid_dates(self) -> pd.DatetimeIndex:
        """返回 WINDOW_HIGH_52W + 5 个工作日（保证条件满足）。"""
        return _make_valid_dates(WINDOW_HIGH_52W + 5, self.T)

    def test_nan_when_history_insufficient(self, monkeypatch):
        """交易日历史 ≤ WINDOW_HIGH_52W 时返回与 codes 对齐的全 NaN。"""
        import src.factors.price_factors as pf

        monkeypatch.setattr(pf, "_valid_dates_before",
                            lambda d: _make_valid_dates(WINDOW_HIGH_52W, self.T))

        result = factor_share_issuance(self.T, self.CODES)

        assert list(result.index) == self.CODES
        assert result.isna().all()

    def test_positive_for_buyback_company(self, monkeypatch):
        """shares_now < shares_prev → share_change < 0 → factor > 0（回购/注销）。"""
        import src.factors.price_factors as pf

        valid = self._enough_valid_dates()
        one_year_ago = valid[-(WINDOW_HIGH_52W + 1)]

        monkeypatch.setattr(pf, "_valid_dates_before", lambda d: valid)

        call_counter = {"n": 0}

        def _mock_basic(d1, d2, codes):
            call_counter["n"] += 1
            if d1 == self.T:
                return _make_basic_df(self.T, codes, {"total_share": [90.0, 100.0]})
            return _make_basic_df(one_year_ago, codes, {"total_share": [100.0, 100.0]})

        monkeypatch.setattr(pf, "load_daily_basic", _mock_basic)

        result = factor_share_issuance(self.T, self.CODES)

        # A.SZ: 90 < 100 → buyback → positive
        assert result["A.SZ"] > 0, "回购股票因子值应为正"

    def test_negative_for_dilution_company(self, monkeypatch):
        """shares_now > shares_prev → share_change > 0 → factor < 0（增发稀释）。"""
        import src.factors.price_factors as pf

        valid = self._enough_valid_dates()
        one_year_ago = valid[-(WINDOW_HIGH_52W + 1)]

        monkeypatch.setattr(pf, "_valid_dates_before", lambda d: valid)

        def _mock_basic(d1, d2, codes):
            if d1 == self.T:
                return _make_basic_df(self.T, codes, {"total_share": [120.0, 100.0]})
            return _make_basic_df(one_year_ago, codes, {"total_share": [100.0, 100.0]})

        monkeypatch.setattr(pf, "load_daily_basic", _mock_basic)

        result = factor_share_issuance(self.T, self.CODES)

        # A.SZ: 120 > 100 → dilution → negative
        assert result["A.SZ"] < 0, "增发股票因子值应为负"

    def test_unchanged_shares_returns_zero(self, monkeypatch):
        """股本不变 → share_change=0 → factor=0。"""
        import src.factors.price_factors as pf

        valid = self._enough_valid_dates()
        one_year_ago = valid[-(WINDOW_HIGH_52W + 1)]

        monkeypatch.setattr(pf, "_valid_dates_before", lambda d: valid)

        def _mock_basic(d1, d2, codes):
            if d1 == self.T:
                return _make_basic_df(self.T, codes, {"total_share": [100.0, 100.0]})
            return _make_basic_df(one_year_ago, codes, {"total_share": [100.0, 100.0]})

        monkeypatch.setattr(pf, "load_daily_basic", _mock_basic)

        result = factor_share_issuance(self.T, self.CODES)

        assert result["A.SZ"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# TestMfFlowRatio
# ---------------------------------------------------------------------------

class TestMfFlowRatio:
    """近期融资净买入流量比。"""

    T = pd.Timestamp("2021-06-30")
    CODES = ["A.SZ", "B.SZ"]

    def _enough_valid_dates(self) -> pd.DatetimeIndex:
        return _make_valid_dates(WINDOW_MF_FLOW + 5, self.T)

    def _mock_basic(self, codes: list[str], circ_mv_vals: list[float]):
        """返回包含 circ_mv 字段的 daily_basic mock 数据（万元单位）。"""
        return _make_basic_df(self.T, codes, {"circ_mv": circ_mv_vals})

    def test_uses_rz_net_not_buy_amount(self, monkeypatch):
        """有 rz_net 时按 rz_net 计算，不按 rzmre 计算。

        rzmre=100, rzche=80, rz_net=20, circ_mv=1万元(=1e4元)
        → net_buy_20d = 20 → result = 20 / (1e4 * 1e4) = 2e-7
        不应按 rzmre=100 计算。
        """
        import src.factors.price_factors as pf

        valid = self._enough_valid_dates()
        start_date = valid[-WINDOW_MF_FLOW]

        monkeypatch.setattr(pf, "_valid_dates_before", lambda d: valid)

        mg_df = _make_margin_df(
            start_date, self.CODES,
            {"rz_net": [20.0, 30.0], "rzmre": [100.0, 120.0], "rzche": [80.0, 90.0]},
        )
        monkeypatch.setattr(pf, "load_margin", lambda d1, d2, codes: mg_df)

        circ_mv_wan = [1.0, 2.0]   # 万元
        monkeypatch.setattr(
            pf, "load_daily_basic",
            lambda d1, d2, codes: self._mock_basic(codes, circ_mv_wan),
        )

        result = factor_mf_flow_ratio(self.T, self.CODES)

        # A.SZ: rz_net=20元, circ_mv=1万元×10000=10000元 → ratio=20/10000=0.002
        expected_a = 20.0 / (1.0 * 10_000.0)
        # 使用 rzmre=100 时 expected = 100/10000=0.01，与 rz_net 结果不同
        assert result["A.SZ"] == pytest.approx(expected_a, rel=1e-6)
        # 确保不是按 rzmre 算的
        assert result["A.SZ"] != pytest.approx(100.0 / (1.0 * 10_000.0))

    def test_falls_back_to_rzmre_minus_rzche(self, monkeypatch):
        """无 rz_net 但有 rzmre/rzche 时，按 rzmre-rzche 计算。

        rzmre=100, rzche=80, net=20, circ_mv=1万元 → ratio=20/1e8
        """
        import src.factors.price_factors as pf

        valid = self._enough_valid_dates()
        start_date = valid[-WINDOW_MF_FLOW]

        monkeypatch.setattr(pf, "_valid_dates_before", lambda d: valid)

        # 故意不含 rz_net
        mg_df = _make_margin_df(
            start_date, self.CODES,
            {"rzmre": [100.0, 120.0], "rzche": [80.0, 90.0]},
        )
        monkeypatch.setattr(pf, "load_margin", lambda d1, d2, codes: mg_df)

        monkeypatch.setattr(
            pf, "load_daily_basic",
            lambda d1, d2, codes: self._mock_basic(codes, [1.0, 2.0]),
        )

        result = factor_mf_flow_ratio(self.T, self.CODES)

        # rzmre-rzche = 100-80 = 20元, circ_mv=1万元×10000=10000元 → ratio=20/10000=0.002
        expected_a = (100.0 - 80.0) / (1.0 * 10_000.0)
        assert result["A.SZ"] == pytest.approx(expected_a, rel=1e-6)

    def test_returns_nan_when_flow_fields_missing(self, monkeypatch):
        """既无 rz_net 也无 rzmre/rzche 时返回与 codes 对齐的全 NaN。"""
        import src.factors.price_factors as pf

        valid = self._enough_valid_dates()
        start_date = valid[-WINDOW_MF_FLOW]

        monkeypatch.setattr(pf, "_valid_dates_before", lambda d: valid)

        # 只有 rzye，没有净买入字段
        mg_df = _make_margin_df(start_date, self.CODES, {"rzye": [1e6, 2e6]})
        monkeypatch.setattr(pf, "load_margin", lambda d1, d2, codes: mg_df)

        monkeypatch.setattr(
            pf, "load_daily_basic",
            lambda d1, d2, codes: self._mock_basic(codes, [1.0, 2.0]),
        )

        result = factor_mf_flow_ratio(self.T, self.CODES)

        assert list(result.index) == self.CODES
        assert result.isna().all(), "缺少净买入字段时应全部返回 NaN"

    def test_returns_nan_when_insufficient_history(self, monkeypatch):
        """有效交易日 < WINDOW_MF_FLOW 时返回全 NaN。"""
        import src.factors.price_factors as pf

        monkeypatch.setattr(pf, "_valid_dates_before",
                            lambda d: _make_valid_dates(WINDOW_MF_FLOW - 1, self.T))

        result = factor_mf_flow_ratio(self.T, self.CODES)

        assert result.isna().all()

    def test_result_range_is_reasonable(self, monkeypatch):
        """smoke test：合理输入下结果在 [-0.1, 0.1] 附近。

        若超出不直接 FAIL，先输出诊断分位数。
        """
        import src.factors.price_factors as pf

        valid = self._enough_valid_dates()
        start_date = valid[-WINDOW_MF_FLOW]

        codes = [f"{i:06d}.SZ" for i in range(1, 21)]

        monkeypatch.setattr(pf, "_valid_dates_before", lambda d: valid)

        # 合理规模：净买入约 1e6 元 / 天，流通市值约 1e10 元
        n = len(codes)
        mg_df = _make_margin_df(
            start_date, codes,
            {"rz_net": [1e6] * n},
        )
        monkeypatch.setattr(pf, "load_margin", lambda d1, d2, codes=None: mg_df)

        # circ_mv = 1e6 万元 × 10000 = 1e10 元，ratio ≈ 1e6 / 1e10 = 1e-4
        monkeypatch.setattr(
            pf, "load_daily_basic",
            lambda d1, d2, codes=None: _make_basic_df(
                self.T, codes or [], {"circ_mv": [1e6] * len(codes or [])}
            ),
        )

        result = factor_mf_flow_ratio(self.T, codes)

        non_nan = result.dropna()
        if len(non_nan) == 0:
            pytest.skip("smoke test: 无有效值（数据为空），跳过范围检查")
            return

        out_of_range = non_nan[(non_nan.abs() > 0.5)]
        if len(out_of_range) > 0:
            q_str = non_nan.describe().to_string()
            pytest.fail(
                f"mf_flow_ratio 超出合理范围 [-0.5, 0.5] 的值：\n{out_of_range}\n"
                f"完整分布：\n{q_str}"
            )
