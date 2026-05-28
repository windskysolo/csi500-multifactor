"""
tests/test_phase5_factors.py — 阶段 5 新因子工程测试

覆盖（全部基于合成数据，不读磁盘）：
  1. high_52w        ：比率计算逻辑、边界条件
  2. ind_adj_mom     ：行业均值去除逻辑
  3. mom_risk_adj    ：风险调整除法逻辑
  4. piotroski_f     ：7 个子项计算正确性、NaN 处理、有效分项门槛
  5. garp            ：rank 均值法、双负数规避、NaN 传播
"""

import numpy as np
import pandas as pd
import pytest

from src.data.pit_loader import get_field_yoy_delta, make_ttm
from src.factors.financial_factors import _binary_flag


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def _make_ip_df(records: list[dict]) -> pd.DataFrame:
    """构造最小 indicator_pit 格式（reset_index 后）。"""
    df = pd.DataFrame(records)
    df["pit_date"] = pd.to_datetime(df["pit_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    return df


def _make_fp_df(records: list[dict]) -> pd.DataFrame:
    """构造最小 financial_pit 格式（reset_index 后）。"""
    df = pd.DataFrame(records)
    df["pit_date"] = pd.to_datetime(df["pit_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    return df


# ---------------------------------------------------------------------------
# high_52w — 纯逻辑测试（不依赖 parquet）
# ---------------------------------------------------------------------------

class TestHigh52wLogic:
    """52 周高点比率的核心计算逻辑（不加载行情数据）。"""

    def test_ratio_computation(self):
        """close / max_high 计算正确。"""
        close_adj = pd.Series({"000001.SZ": 10.0, "000002.SZ": 8.0})
        high_52w_max = pd.Series({"000001.SZ": 12.0, "000002.SZ": 8.0})

        result = close_adj / high_52w_max.replace(0, np.nan)

        assert result["000001.SZ"] == pytest.approx(10.0 / 12.0)
        assert result["000002.SZ"] == pytest.approx(1.0)

    def test_result_at_most_1(self):
        """当前价不可能超过历史最高价（逻辑上），比率 ≤ 1。"""
        close_adj = pd.Series({"A": 9.0, "B": 10.0})
        high_max = pd.Series({"A": 15.0, "B": 10.0})

        result = close_adj / high_max.replace(0, np.nan)
        assert (result <= 1.0 + 1e-9).all()

    def test_zero_high_gives_nan(self):
        """max_high = 0 时结果为 NaN（防除零）。"""
        close_adj = pd.Series({"A": 5.0})
        high_max = pd.Series({"A": 0.0})

        result = close_adj / high_max.replace(0, np.nan)
        assert np.isnan(result["A"])


# ---------------------------------------------------------------------------
# ind_adj_mom — 行业均值去除逻辑
# ---------------------------------------------------------------------------

class TestIndAdjMomLogic:
    """行业调整动量：行业均值去除逻辑（不加载行情/行业 parquet）。"""

    def _adj(self, mom: pd.Series, industry: pd.Series) -> pd.Series:
        ind_mean = mom.groupby(industry).transform("mean")
        return mom - ind_mean

    def test_single_industry_mean_is_zero(self):
        """同一行业去均值后，均值为 0。"""
        mom = pd.Series({"A": 0.10, "B": 0.20, "C": 0.15}, name="mom_12_1")
        mom.index.name = "ts_code"
        ind = pd.Series({"A": "ind1", "B": "ind1", "C": "ind1"})

        result = self._adj(mom, ind)
        assert result.mean() == pytest.approx(0.0, abs=1e-10)

    def test_multi_industry_within_industry_mean_zero(self):
        """两个行业各自去均值后，各行业内部均值均为 0。"""
        mom = pd.Series({"A": 0.10, "B": 0.20, "C": 0.30, "D": 0.40})
        ind = pd.Series({"A": "g1", "B": "g1", "C": "g2", "D": "g2"})

        result = self._adj(mom, ind)

        assert result["A"] == pytest.approx(-0.05)
        assert result["B"] == pytest.approx(0.05)
        assert result["C"] == pytest.approx(-0.05)
        assert result["D"] == pytest.approx(0.05)

    def test_relative_order_preserved_within_industry(self):
        """行业内部相对排序保持不变。"""
        mom = pd.Series({"A": 0.05, "B": 0.15, "C": 0.25})
        ind = pd.Series({"A": "same", "B": "same", "C": "same"})

        result = self._adj(mom, ind)
        assert result["A"] < result["B"] < result["C"]

    def test_single_stock_industry_gives_zero(self):
        """行业内只有 1 只股票时，去均值后为 0。"""
        mom = pd.Series({"A": 0.30, "B": 0.10})
        ind = pd.Series({"A": "alone", "B": "other"})

        result = self._adj(mom, ind)
        assert result["A"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# mom_risk_adj — 风险调整动量
# ---------------------------------------------------------------------------

class TestMomRiskAdjLogic:
    """Sharpe 式动量：mom / ivol 计算逻辑。"""

    def test_basic_ratio(self):
        """mom / ivol 计算正确。"""
        mom  = pd.Series({"A": 0.10, "B": 0.20})
        ivol = pd.Series({"A": 0.20, "B": 0.10})

        result = mom / ivol.replace(0, np.nan)

        assert result["A"] == pytest.approx(0.5)
        assert result["B"] == pytest.approx(2.0)

    def test_zero_ivol_gives_nan(self):
        """ivol = 0 时结果为 NaN（防除零）。"""
        mom  = pd.Series({"A": 0.10})
        ivol = pd.Series({"A": 0.0})

        result = mom / ivol.replace(0, np.nan)
        assert np.isnan(result["A"])

    def test_nan_mom_gives_nan(self):
        """mom = NaN 时结果为 NaN。"""
        mom  = pd.Series({"A": np.nan})
        ivol = pd.Series({"A": 0.20})

        result = mom / ivol.replace(0, np.nan)
        assert np.isnan(result["A"])

    def test_nan_ivol_gives_nan(self):
        """ivol = NaN 时结果为 NaN。"""
        mom  = pd.Series({"A": 0.10})
        ivol = pd.Series({"A": np.nan})

        result = mom / ivol.replace(0, np.nan)
        assert np.isnan(result["A"])

    def test_negative_mom_preserves_sign(self):
        """负动量 / 正 ivol 结果仍为负。"""
        mom  = pd.Series({"A": -0.10})
        ivol = pd.Series({"A": 0.20})

        result = mom / ivol.replace(0, np.nan)
        assert result["A"] == pytest.approx(-0.5)


# ---------------------------------------------------------------------------
# _binary_flag（piotroski_f 内部工具函数）
# ---------------------------------------------------------------------------

class TestBinaryFlag:
    """_binary_flag 辅助函数：布尔 → 0/1/NaN 转换。"""

    def test_true_gives_one(self):
        s = pd.Series([5.0], name="x")
        result = _binary_flag(s > 0, s.notna())
        assert result.iloc[0] == pytest.approx(1.0)

    def test_false_gives_zero(self):
        s = pd.Series([-3.0], name="x")
        result = _binary_flag(s > 0, s.notna())
        assert result.iloc[0] == pytest.approx(0.0)

    def test_nan_source_gives_nan(self):
        s = pd.Series([np.nan], name="x")
        result = _binary_flag(s > 0, s.notna())
        assert np.isnan(result.iloc[0])


# ---------------------------------------------------------------------------
# piotroski_f — 子项逻辑与 NaN 处理
# ---------------------------------------------------------------------------

class TestPiotroskiComponents:
    """Piotroski F-Score 子项计算正确性与 NaN 处理。"""

    def test_f3_uses_yoy_delta(self):
        """F3 = ΔROA > 0，通过 get_field_yoy_delta 计算正确。"""
        T = pd.Timestamp("2023-01-31")
        ip_raw = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-04-30",
             "end_date": "2021-12-31", "roa": 5.0},
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15",
             "end_date": "2022-12-31", "roa": 7.0},  # 改善 +2.0
        ])
        delta = get_field_yoy_delta(ip_raw, "roa", T, ["000001.SZ"])

        f3 = _binary_flag(delta > 0, delta.notna())
        assert f3["000001.SZ"] == pytest.approx(1.0)

    def test_f3_roa_decline_gives_zero(self):
        """F3 = ΔROA < 0 时得 0。"""
        T = pd.Timestamp("2023-01-31")
        ip_raw = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-04-30",
             "end_date": "2021-12-31", "roa": 8.0},
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15",
             "end_date": "2022-12-31", "roa": 6.0},  # 下滑 -2.0
        ])
        delta = get_field_yoy_delta(ip_raw, "roa", T, ["000001.SZ"])

        f3 = _binary_flag(delta > 0, delta.notna())
        assert f3["000001.SZ"] == pytest.approx(0.0)

    def test_f5_debt_decline_gives_one(self):
        """F5 = Δdebt_to_assets < 0（杠杆下降）时得 1。"""
        T = pd.Timestamp("2023-01-31")
        ip_raw = _make_ip_df([
            {"ts_code": "000001.SZ", "pit_date": "2022-04-30",
             "end_date": "2021-12-31", "debt_to_assets": 60.0},
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15",
             "end_date": "2022-12-31", "debt_to_assets": 55.0},  # 下降 -5
        ])
        delta = get_field_yoy_delta(ip_raw, "debt_to_assets", T, ["000001.SZ"])

        f5 = _binary_flag(delta < 0, delta.notna())
        assert f5["000001.SZ"] == pytest.approx(1.0)

    def test_score_summation_7_valid(self):
        """7 个分项均有效时，总分 = 各子项之和。"""
        scores = pd.DataFrame({
            "f1": [1.0], "f2": [1.0], "f3": [0.0],
            "f4": [1.0], "f5": [0.0], "f8": [1.0], "f9": [1.0],
        })
        valid_count = scores.notna().sum(axis=1)
        f_score = scores.sum(axis=1, skipna=True)
        f_score[valid_count < 5] = np.nan

        assert f_score.iloc[0] == pytest.approx(5.0)

    def test_score_nan_when_fewer_than_5_valid(self):
        """有效分项 < 5 时 F-Score 置 NaN。"""
        scores = pd.DataFrame({
            "f1": [1.0], "f2": [np.nan], "f3": [np.nan],
            "f4": [np.nan], "f5": [np.nan], "f8": [1.0], "f9": [1.0],
        })
        valid_count = scores.notna().sum(axis=1)  # = 3
        f_score = scores.sum(axis=1, skipna=True)
        f_score[valid_count < 5] = np.nan

        assert np.isnan(f_score.iloc[0])

    def test_all_pass_gives_7(self):
        """7 个分项全部通过时总分 = 7。"""
        scores = pd.DataFrame({
            "f1": [1.0], "f2": [1.0], "f3": [1.0],
            "f4": [1.0], "f5": [1.0], "f8": [1.0], "f9": [1.0],
        })
        valid_count = scores.notna().sum(axis=1)
        f_score = scores.sum(axis=1, skipna=True)
        f_score[valid_count < 5] = np.nan

        assert f_score.iloc[0] == pytest.approx(7.0)

    def test_all_fail_gives_0(self):
        """7 个分项全部不通过时总分 = 0（不是 NaN，数据充分）。"""
        scores = pd.DataFrame({
            "f1": [0.0], "f2": [0.0], "f3": [0.0],
            "f4": [0.0], "f5": [0.0], "f8": [0.0], "f9": [0.0],
        })
        valid_count = scores.notna().sum(axis=1)
        f_score = scores.sum(axis=1, skipna=True)
        f_score[valid_count < 5] = np.nan

        assert f_score.iloc[0] == pytest.approx(0.0)

    def test_f2_f4_using_make_ttm(self):
        """F2（CFO > 0）和 F4（CFO > NI）通过 make_ttm 正确计算。"""
        T = pd.Timestamp("2023-01-31")
        fp_raw = _make_fp_df([
            # Q1: end_date 2022-03-31
            {"ts_code": "000001.SZ", "pit_date": "2022-04-30",
             "end_date": "2022-03-31", "n_cashflow_act": 100.0, "n_income": 80.0},
            # Q2: end_date 2022-06-30
            {"ts_code": "000001.SZ", "pit_date": "2022-08-31",
             "end_date": "2022-06-30", "n_cashflow_act": 210.0, "n_income": 160.0},
            # Q3: end_date 2022-09-30
            {"ts_code": "000001.SZ", "pit_date": "2022-10-31",
             "end_date": "2022-09-30", "n_cashflow_act": 310.0, "n_income": 240.0},
            # Annual: end_date 2022-12-31（累计）
            {"ts_code": "000001.SZ", "pit_date": "2023-01-15",
             "end_date": "2022-12-31", "n_cashflow_act": 450.0, "n_income": 360.0},
        ])
        ttm_cfo = make_ttm(fp_raw, "n_cashflow_act", T, ["000001.SZ"])
        ttm_ni  = make_ttm(fp_raw, "n_income", T, ["000001.SZ"])

        # Annual (Q4 increment = 450 - 310 = 140) + Q3 (=310-210=100) + Q2(=210-100=110) + Q1(=100)
        # TTM = Q4_inc + Q3_inc + Q2_inc + Q1 = 140 + 100 + 110 + 100 = 450 (full year = Annual)
        # Since Annual end_date is 2022-12-31 (fiscal year end), make_ttm returns Annual value directly
        assert ttm_cfo["000001.SZ"] == pytest.approx(450.0)
        assert ttm_ni["000001.SZ"]  == pytest.approx(360.0)

        f2 = _binary_flag(ttm_cfo > 0, ttm_cfo.notna())
        f4 = _binary_flag(ttm_cfo > ttm_ni,
                          ttm_cfo.notna() & ttm_ni.notna())
        assert f2["000001.SZ"] == pytest.approx(1.0)   # CFO > 0
        assert f4["000001.SZ"] == pytest.approx(1.0)   # CFO (450) > NI (360)


# ---------------------------------------------------------------------------
# garp — rank 均值法
# ---------------------------------------------------------------------------

class TestGarpRankLogic:
    """GARP rank 均值法：双正选股、双负规避、NaN 传播。"""

    def test_output_bounded_0_1(self):
        """rank 均值输出范围在 [0, 1]。"""
        ep  = pd.Series({"A": 0.10, "B": 0.05, "C": 0.01})
        roe = pd.Series({"A": 15.0, "B": 10.0, "C": 5.0})

        ep_rank  = ep.rank(pct=True, na_option="keep")
        roe_rank = roe.rank(pct=True, na_option="keep")
        garp = (ep_rank + roe_rank) / 2

        assert garp.min() >= 0.0 - 1e-9
        assert garp.max() <= 1.0 + 1e-9

    def test_best_stock_has_highest_garp(self):
        """高 EP + 高 ROE 的股票 GARP 得分最高。"""
        ep  = pd.Series({"best": 0.15, "mid": 0.08, "worst": -0.05})
        roe = pd.Series({"best": 20.0, "mid": 10.0, "worst": -5.0})

        ep_rank  = ep.rank(pct=True, na_option="keep")
        roe_rank = roe.rank(pct=True, na_option="keep")
        garp = (ep_rank + roe_rank) / 2

        assert garp["best"] > garp["mid"] > garp["worst"]

    def test_avoids_double_negative_problem(self):
        """双负（低 EP + 低 ROE）不会被误判为高 GARP（乘积法陷阱）。"""
        ep  = pd.Series({"bad": -0.05, "good": 0.15})
        roe = pd.Series({"bad":  -5.0, "good": 20.0})

        # rank 均值法
        ep_rank  = ep.rank(pct=True, na_option="keep")
        roe_rank = roe.rank(pct=True, na_option="keep")
        garp_rank = (ep_rank + roe_rank) / 2

        # 乘积法（错误示范）
        ep_z  = (ep  - ep.mean())  / ep.std()
        roe_z = (roe - roe.mean()) / roe.std()
        garp_product = ep_z * roe_z   # 双负相乘会给 "bad" 高分

        # rank 均值：bad 分数低（正确）
        assert garp_rank["good"] > garp_rank["bad"]
        # 乘积法：bad 分数非负（有时甚至比 good 高，展示陷阱）
        # 注：两只股票的乘积法中，(-1)×(-1)=+1，(+1)×(+1)=+1，两者相同 → 无法区分
        # 而 rank 法能明确区分
        assert garp_rank["good"] != pytest.approx(garp_rank["bad"])

    def test_nan_propagation_ep(self):
        """EP 为 NaN 时 GARP 为 NaN。"""
        ep  = pd.Series({"A": np.nan, "B": 0.10})
        roe = pd.Series({"A": 10.0,   "B": 10.0})

        ep_rank  = ep.rank(pct=True, na_option="keep")
        roe_rank = roe.rank(pct=True, na_option="keep")
        garp = (ep_rank + roe_rank) / 2

        assert np.isnan(garp["A"])
        assert not np.isnan(garp["B"])

    def test_nan_propagation_roe(self):
        """ROE 为 NaN 时 GARP 为 NaN。"""
        ep  = pd.Series({"A": 0.10, "B": 0.10})
        roe = pd.Series({"A": np.nan, "B": 10.0})

        ep_rank  = ep.rank(pct=True, na_option="keep")
        roe_rank = roe.rank(pct=True, na_option="keep")
        garp = (ep_rank + roe_rank) / 2

        assert np.isnan(garp["A"])
        assert not np.isnan(garp["B"])

    def test_symmetric_stocks_equal_garp(self):
        """EP 和 ROE 排名完全对称的两只股票，GARP 得分相同。"""
        ep  = pd.Series({"A": 0.15, "B": 0.05})
        roe = pd.Series({"A": 5.0,  "B": 15.0})   # ROE 顺序与 EP 相反

        ep_rank  = ep.rank(pct=True, na_option="keep")
        roe_rank = roe.rank(pct=True, na_option="keep")
        garp = (ep_rank + roe_rank) / 2

        assert garp["A"] == pytest.approx(garp["B"])
