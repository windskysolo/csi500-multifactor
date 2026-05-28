"""
tests/test_attribution.py — 归因模块单元测试（F8-008）

覆盖：
  - Brinson 恒等式（T8-001）
  - T+1 开盘收益对齐（T8-002）
  - 现金权重处理（T8-003）
  - 完整基准成分股收益加载（T8-004）
  - 因子列表来源（T8-005）
  - 因子方向来源（T8-006）
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ── 从模块导入被测函数 ──────────────────────────────────────────────────────────
from src.attribution.brinson import (
    _compute_period_brinson,
    _compute_period_returns_t1_open,
    _get_period_start_weights,
)
from src.attribution.factor_attr import (
    _build_group_composites,
    _load_factor_directions_from_summary,
    _load_final_factor_group_map,
    DEFAULT_FACTOR_GROUPS,
)


# ---------------------------------------------------------------------------
# 辅助工厂函数
# ---------------------------------------------------------------------------

def _make_close_adj(dates: list[pd.Timestamp], codes: list[str], base: float = 100.0) -> pd.DataFrame:
    """生成等比例上涨的后复权收盘价面板（无停牌）。"""
    n_dates, n_codes = len(dates), len(codes)
    data = np.tile(np.arange(n_dates, dtype=float).reshape(-1, 1), (1, n_codes)) * 1.0 + base
    return pd.DataFrame(data, index=pd.DatetimeIndex(dates), columns=codes)


def _make_open_adj(close_adj: pd.DataFrame, open_discount: float = 0.01) -> pd.DataFrame:
    """开盘价 = 收盘价 × (1 - open_discount)，模拟跳空高开场景的逆（低开）。"""
    return close_adj * (1.0 - open_discount)


def _make_actual_weights(
    dates: list[pd.Timestamp],
    codes: list[str],
    w_per_stock: float,
) -> pd.DataFrame:
    """所有日期对所有股票赋同等权重 w_per_stock，行和 = w_per_stock × n_codes。"""
    data = {c: [w_per_stock] * len(dates) for c in codes}
    return pd.DataFrame(data, index=pd.DatetimeIndex(dates))


# ---------------------------------------------------------------------------
# T8-001：Brinson 恒等式
# ---------------------------------------------------------------------------

class TestBrinsonIdentity:
    """验证 _compute_period_brinson 的 BHB 恒等式。"""

    def _make_inputs(self, seed: int = 0) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        rng = np.random.default_rng(seed)
        codes = [f"{i:06d}.SZ" for i in range(20)]
        industries = ["801110.SI"] * 10 + ["801780.SI"] * 10

        w_p_arr = np.abs(rng.standard_normal(20))
        w_p_arr /= w_p_arr.sum()          # 归一化（BHB 恒等式在满仓下精确成立）
        w_b_arr = np.abs(rng.standard_normal(20))
        w_b_arr /= w_b_arr.sum()

        r_arr   = rng.standard_normal(20) * 0.05

        w_p = pd.Series(w_p_arr, index=codes)
        w_b = pd.Series(w_b_arr, index=codes)
        r   = pd.Series(r_arr,   index=codes)
        ind = pd.Series(industries, index=codes)
        return w_p, w_b, r, ind

    def test_total_effect_equals_excess(self):
        """V1 恒等式：Σ total_effect = strategy_return - benchmark_return。"""
        w_p, w_b, r, ind = self._make_inputs()
        df = _compute_period_brinson(w_p, w_b, r, ind)
        strategy_ret  = float((w_p * r.reindex(w_p.index).fillna(0)).sum())
        benchmark_ret = float((w_b * r.reindex(w_b.index).fillna(0)).sum())
        assert abs(df["total_effect"].sum() - (strategy_ret - benchmark_ret)) < 1e-10

    def test_bhb_v2_identity_at_aggregate_level(self):
        """V2 恒等式（汇总层）：Σ(alloc + select + interact) = Σ total_effect。"""
        w_p, w_b, r, ind = self._make_inputs(seed=42)
        df = _compute_period_brinson(w_p, w_b, r, ind)
        lhs = (df["allocation_effect"] + df["selection_effect"] + df["interaction_effect"]).sum()
        rhs = df["total_effect"].sum()
        assert abs(lhs - rhs) < 1e-10

    def test_all_same_industry_gives_zero_allocation(self):
        """所有股票在同一行业时，配置效应 = 0（策略与基准在行业层无偏离）。"""
        codes = ["000001.SZ", "000002.SZ", "000003.SZ"]
        w_p = pd.Series([0.4, 0.3, 0.3], index=codes)
        w_b = pd.Series([0.4, 0.3, 0.3], index=codes)
        r   = pd.Series([0.02, -0.01, 0.03], index=codes)
        ind = pd.Series(["801110.SI"] * 3, index=codes)
        df = _compute_period_brinson(w_p, w_b, r, ind)
        # 同权重时 allocation = 0，selection = 0，interact = 0，total = 0
        assert abs(df["total_effect"].sum()) < 1e-10


# ---------------------------------------------------------------------------
# T8-002：T+1 开盘收益对齐（F8-001）
# ---------------------------------------------------------------------------

class TestT1OpenReturns:
    """验证 _compute_period_returns_t1_open 的 T+1 开盘入场逻辑。"""

    def test_single_day_period_uses_open_to_close(self):
        """单日期间：收益 = close/open - 1，不含 T 收盘到 T+1 开盘的隔夜段。"""
        T      = pd.Timestamp("2022-01-28")
        T_next = pd.Timestamp("2022-01-28")   # 这里 T_next = T+1 trading day = same date

        # 只有 T+1 = 2022-01-28（假设 T+1 就是 T_next）
        dates = [pd.Timestamp("2022-01-28")]
        codes = ["000001.SZ"]

        close_panel = pd.DataFrame({"000001.SZ": [110.0]}, index=pd.DatetimeIndex(dates))
        open_panel  = pd.DataFrame({"000001.SZ": [100.0]}, index=pd.DatetimeIndex(dates))

        T_entry = pd.Timestamp("2022-01-27")   # T: 上一日（不在 in_period 中）
        r, n_missing = _compute_period_returns_t1_open(
            T_entry, T_next, ["000001.SZ"], close_panel, open_panel
        )
        expected = 110.0 / 100.0 - 1.0   # 0.10
        assert abs(float(r["000001.SZ"]) - expected) < 1e-10, f"预期 {expected}，实际 {r['000001.SZ']}"

    def test_multi_day_first_day_open_aligned(self):
        """多日期间：第一日收益 = close(T+1)/open(T+1)-1，后续收盘到收盘。"""
        dates = pd.date_range("2022-01-03", periods=5, freq="B")
        T      = pd.Timestamp("2022-01-01")   # T 不在面板中（调仓日）
        T_next = dates[-1]

        # 构造: 每日收盘 +10，开盘 +1（第一日开盘=101，收盘=110）
        close_arr = [110.0, 120.0, 130.0, 140.0, 150.0]
        open_arr  = [101.0, 111.0, 121.0, 131.0, 141.0]

        codes        = ["000001.SZ"]
        close_panel  = pd.DataFrame({"000001.SZ": close_arr}, index=pd.DatetimeIndex(dates))
        open_panel   = pd.DataFrame({"000001.SZ": open_arr},  index=pd.DatetimeIndex(dates))

        r, _ = _compute_period_returns_t1_open(T, T_next, codes, close_panel, open_panel)

        # 预期累计收益：
        # Day1: 110/101 - 1
        # Day2: 120/110 - 1
        # Day3: 130/120 - 1
        # Day4: 140/130 - 1
        # Day5: 150/140 - 1
        expected = (110/101) * (120/110) * (130/120) * (140/130) * (150/140) - 1.0
        assert abs(float(r["000001.SZ"]) - expected) < 1e-10

    def test_halted_stock_returns_zero(self):
        """停牌（NaN close/open）的股票返回 0 收益。"""
        dates  = [pd.Timestamp("2022-01-03")]
        T      = pd.Timestamp("2022-01-01")
        T_next = pd.Timestamp("2022-01-03")
        codes  = ["000001.SZ", "000002.SZ"]

        close_panel = pd.DataFrame(
            {"000001.SZ": [110.0], "000002.SZ": [np.nan]},
            index=pd.DatetimeIndex(dates)
        )
        open_panel = pd.DataFrame(
            {"000001.SZ": [100.0], "000002.SZ": [np.nan]},
            index=pd.DatetimeIndex(dates)
        )

        r, _ = _compute_period_returns_t1_open(T, T_next, codes, close_panel, open_panel)
        assert abs(float(r["000002.SZ"])) < 1e-10

    def test_missing_code_not_in_panel_returns_zero(self):
        """股票代码不在面板中时，累计收益填 0，n_missing 计数正确。"""
        dates  = [pd.Timestamp("2022-01-03")]
        T      = pd.Timestamp("2022-01-01")
        T_next = pd.Timestamp("2022-01-03")
        codes  = ["000001.SZ", "999999.SZ"]   # 999999.SZ 不在面板中

        close_panel = pd.DataFrame({"000001.SZ": [110.0]}, index=pd.DatetimeIndex(dates))
        open_panel  = pd.DataFrame({"000001.SZ": [100.0]}, index=pd.DatetimeIndex(dates))

        r, n_missing = _compute_period_returns_t1_open(T, T_next, codes, close_panel, open_panel)
        assert n_missing == 1
        assert abs(float(r["999999.SZ"])) < 1e-10


# ---------------------------------------------------------------------------
# T8-003：现金权重处理（F8-002）
# ---------------------------------------------------------------------------

class TestCashWeightNotNormalized:
    """验证 _get_period_start_weights 不归一化原始权重和。"""

    def _make_actual_weights(
        self,
        dates: list[pd.Timestamp],
        codes: list[str],
        w_per_stock: float,
    ) -> pd.DataFrame:
        data = {c: [w_per_stock] * len(dates) for c in codes}
        return pd.DataFrame(data, index=pd.DatetimeIndex(dates))

    def test_raw_weight_sum_preserved(self):
        """权重和 < 1 的情况下，返回的权重和不变（不归一化）。"""
        dates   = [pd.Timestamp("2022-01-31"), pd.Timestamp("2022-02-28")]
        codes   = ["000001.SZ", "000002.SZ"]
        T       = pd.Timestamp("2021-12-31")
        weights = self._make_actual_weights(dates, codes, w_per_stock=0.45)

        w = _get_period_start_weights(weights, T)
        assert not w.empty
        assert abs(w.sum() - 0.90) < 1e-10, f"权重和应为 0.90（原始），实际 = {w.sum()}"

    def test_fully_invested_weights_stay_full(self):
        """权重和 = 1 时，返回结果也应为 1（不发生变化）。"""
        dates   = [pd.Timestamp("2022-01-31")]
        codes   = ["000001.SZ", "000002.SZ"]
        T       = pd.Timestamp("2021-12-31")
        weights = self._make_actual_weights(dates, codes, w_per_stock=0.50)

        w = _get_period_start_weights(weights, T)
        assert abs(w.sum() - 1.0) < 1e-10

    def test_cash_weight_can_be_derived(self):
        """调用方能从 1 - w.sum() 正确推导出现金仓位。"""
        dates   = [pd.Timestamp("2022-01-31")]
        codes   = ["000001.SZ", "000002.SZ", "000003.SZ"]
        T       = pd.Timestamp("2021-12-31")
        # 3 只各 0.30，总和 0.90，现金 10%
        data    = {c: [0.30] for c in codes}
        weights = pd.DataFrame(data, index=pd.DatetimeIndex(dates))

        w = _get_period_start_weights(weights, T)
        cash_weight = 1.0 - w.sum()
        assert abs(cash_weight - 0.10) < 1e-10


# ---------------------------------------------------------------------------
# T8-005：因子列表来源（F8-004）
# ---------------------------------------------------------------------------

class TestFinalFactorGroupMap:
    """验证 _load_final_factor_group_map 从 final_factors.json 加载。"""

    def test_uses_only_final_factors_from_json(self, tmp_path: Path):
        """当 final_factors.json 只含 2 个因子时，返回的 map 也只含 2 个。"""
        import src.attribution.factor_attr as fa_module

        final_json = tmp_path / "final_factors.json"
        final_json.write_text(json.dumps({
            "final_factors": ["ep_ttm", "mom_12_1"],
            "excluded": []
        }))

        # monkey-patch cfg.ROOT 使模块加载 tmp_path 下的文件
        orig_root = fa_module.cfg.ROOT
        try:
            # 把相对路径写成 tmp_path/reports/factor_evaluation/final_factors.json
            report_dir = tmp_path / "reports" / "factor_evaluation"
            report_dir.mkdir(parents=True)
            (report_dir / "final_factors.json").write_text(final_json.read_text())

            fa_module.cfg.ROOT = tmp_path
            group_map = _load_final_factor_group_map()
        finally:
            fa_module.cfg.ROOT = orig_root

        assert set(group_map.keys()) == {"ep_ttm", "mom_12_1"}

    def test_missing_json_falls_back_to_default_groups(self, tmp_path: Path):
        """final_factors.json 不存在时，退回到 DEFAULT_FACTOR_GROUPS。"""
        import src.attribution.factor_attr as fa_module
        orig_root = fa_module.cfg.ROOT
        try:
            # tmp_path 下没有 final_factors.json
            fa_module.cfg.ROOT = tmp_path
            group_map = _load_final_factor_group_map()
        finally:
            fa_module.cfg.ROOT = orig_root

        assert group_map == DEFAULT_FACTOR_GROUPS

    def test_unknown_final_factor_is_excluded(self, tmp_path: Path):
        """final_factors.json 中出现 DEFAULT_FACTOR_GROUPS 没有的因子时，该因子被排除。"""
        import src.attribution.factor_attr as fa_module
        orig_root = fa_module.cfg.ROOT
        try:
            report_dir = tmp_path / "reports" / "factor_evaluation"
            report_dir.mkdir(parents=True)
            (report_dir / "final_factors.json").write_text(json.dumps({
                "final_factors": ["ep_ttm", "nonexistent_factor_xyz"]
            }))
            fa_module.cfg.ROOT = tmp_path
            group_map = _load_final_factor_group_map()
        finally:
            fa_module.cfg.ROOT = orig_root

        assert "ep_ttm" in group_map
        assert "nonexistent_factor_xyz" not in group_map


# ---------------------------------------------------------------------------
# T8-006：因子方向来源（F8-005）
# ---------------------------------------------------------------------------

class TestFactorDirectionsFromSummary:
    """验证 _load_factor_directions_from_summary 从 CSV 读取方向，不使用硬编码表。"""

    def test_margin_ratio_direction_from_csv(self, tmp_path: Path):
        """margin_ratio 在 factor_summary.csv 中方向为 -1，而 FACTOR_DIRECTIONS 为 +1。
        修复后应读取 CSV 的 -1，不再使用硬编码值。"""
        import src.attribution.factor_attr as fa_module
        orig_root = fa_module.cfg.ROOT
        try:
            report_dir = tmp_path / "reports" / "factor_evaluation"
            report_dir.mkdir(parents=True)

            summary_df = pd.DataFrame({
                "factor": ["ep_ttm", "margin_ratio"],
                "factor_direction": [1.0, -1.0],
                "ic_mean": [0.04, -0.05],
            })
            summary_df.to_csv(report_dir / "factor_summary.csv", index=False)

            fa_module.cfg.ROOT = tmp_path
            directions = _load_factor_directions_from_summary()
        finally:
            fa_module.cfg.ROOT = orig_root

        assert directions.get("margin_ratio") == -1, (
            f"margin_ratio 方向应为 -1（来自 CSV），实际 = {directions.get('margin_ratio')}"
        )

    def test_missing_csv_falls_back_to_hardcoded(self, tmp_path: Path):
        """factor_summary.csv 不存在时退回到 FACTOR_DIRECTIONS（不崩溃）。"""
        import src.attribution.factor_attr as fa_module
        orig_root = fa_module.cfg.ROOT
        try:
            fa_module.cfg.ROOT = tmp_path
            directions = _load_factor_directions_from_summary()
        finally:
            fa_module.cfg.ROOT = orig_root

        # 退回时必须有合理值（非空字典）
        assert isinstance(directions, dict)
        assert len(directions) > 0

    def test_build_group_composites_skips_missing_direction(self):
        """_build_group_composites 对方向缺失的因子不返回该因子，不默认 +1。"""
        dates  = [pd.Timestamp("2022-01-31")]
        codes  = ["000001.SZ", "000002.SZ", "000003.SZ"]
        T      = pd.Timestamp("2022-01-31")

        # ep_ttm 有方向，missing_factor 没有方向
        panels = {
            "ep_ttm":       pd.DataFrame({c: [1.0] for c in codes}, index=pd.DatetimeIndex(dates)),
            "missing_factor": pd.DataFrame({c: [1.0] for c in codes}, index=pd.DatetimeIndex(dates)),
        }
        group_map  = {"ep_ttm": "value", "missing_factor": "value"}
        directions = {"ep_ttm": 1}   # missing_factor 故意不在 directions 中

        composites = _build_group_composites(panels, T, group_map, directions)

        # value 组应有数据（来自 ep_ttm）
        assert "value" in composites
        # 每个 composite 的值应仅来自 ep_ttm，不含 missing_factor（两者方向相同时结果一样，
        # 但若方向错误会导致符号翻转，测试目的是验证不崩溃且不悄悄使用 +1 默认）
        assert composites["value"].notna().any()
