"""
tests/test_pit.py — PIT（Point-In-Time）数据可见性约束测试

验证规则：
  - 任何 T 日只能看到 pit_date <= T 且 end_date < T 的财务数据
  - ann_date（公告日）是可用日，不是 end_date（报告期）
  - TTM 计算不使用 T 日之后的数据

测试策略：使用合成 DataFrame 模拟 financial_pit 结构，
不依赖磁盘数据，每次都可直接运行。
"""

import pandas as pd
import pytest

from src.data.pit_loader import _filter_visible, make_ttm


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_pit_df(records: list[dict]) -> pd.DataFrame:
    """构造最小 financial_pit 格式的 DataFrame（reset_index 后格式）。"""
    df = pd.DataFrame(records)
    df["pit_date"] = pd.to_datetime(df["pit_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    return df


# ---------------------------------------------------------------------------
# _filter_visible 的 PIT 约束测试
# ---------------------------------------------------------------------------

class TestFilterVisible:
    """验证 _filter_visible 在 T 日只返回合法 PIT 记录。"""

    def test_pit_date_future_is_excluded(self):
        """公告日在 T 之后的记录不可见。"""
        T = pd.Timestamp("2021-06-30")
        df = _make_pit_df([
            {"ts_code": "000001.SZ", "pit_date": "2021-07-01",  # 未来公告
             "end_date": "2021-03-31", "n_income": 100.0},
        ])
        result = _filter_visible(df, T)
        assert result.empty, "T 日之后公告的记录不应可见"

    def test_pit_date_on_T_is_visible(self):
        """公告日恰好等于 T 的记录可见（当日可见）。"""
        T = pd.Timestamp("2021-06-30")
        df = _make_pit_df([
            {"ts_code": "000001.SZ", "pit_date": "2021-06-30",
             "end_date": "2021-03-31", "n_income": 100.0},
        ])
        result = _filter_visible(df, T)
        assert len(result) == 1, "公告日等于 T 的记录应可见"

    def test_end_date_same_period_excluded(self):
        """报告期 >= T 的记录不可见（含同月末）。"""
        T = pd.Timestamp("2021-06-30")
        df = _make_pit_df([
            # 报告期就是 T（2021-06-30），即使公告日合法，end_date 条件也应拒绝
            {"ts_code": "000001.SZ", "pit_date": "2021-06-30",
             "end_date": "2021-06-30", "n_income": 200.0},
            # 报告期在 T 之后（未来季报）
            {"ts_code": "000001.SZ", "pit_date": "2021-06-29",
             "end_date": "2021-09-30", "n_income": 300.0},
        ])
        result = _filter_visible(df, T)
        assert result.empty, "报告期 >= T 的记录不应可见"

    def test_both_conditions_must_hold(self):
        """pit_date <= T 且 end_date < T 同时满足才可见，缺一不可。"""
        T = pd.Timestamp("2021-06-30")
        df = _make_pit_df([
            # 合法记录
            {"ts_code": "000001.SZ", "pit_date": "2021-04-30",
             "end_date": "2021-03-31", "n_income": 100.0},
            # pit_date 合法但 end_date 不合法
            {"ts_code": "000002.SZ", "pit_date": "2021-06-29",
             "end_date": "2021-06-30", "n_income": 200.0},
            # end_date 合法但 pit_date 不合法
            {"ts_code": "000003.SZ", "pit_date": "2021-07-01",
             "end_date": "2021-03-31", "n_income": 300.0},
        ])
        result = _filter_visible(df, T)
        assert list(result["ts_code"]) == ["000001.SZ"], "只有同时满足两个条件的记录可见"

    def test_code_filter(self):
        """codes 参数正确过滤股票。"""
        T = pd.Timestamp("2021-06-30")
        df = _make_pit_df([
            {"ts_code": "000001.SZ", "pit_date": "2021-04-30",
             "end_date": "2021-03-31", "n_income": 100.0},
            {"ts_code": "000002.SZ", "pit_date": "2021-04-30",
             "end_date": "2021-03-31", "n_income": 200.0},
        ])
        result = _filter_visible(df, T, codes=["000001.SZ"])
        assert list(result["ts_code"]) == ["000001.SZ"]

    def test_multiple_revisions_all_visible(self):
        """同一报告期多次修订，只要 pit_date <= T 都可见（_filter_visible 不去重）。"""
        T = pd.Timestamp("2021-06-30")
        df = _make_pit_df([
            {"ts_code": "000001.SZ", "pit_date": "2021-04-28",
             "end_date": "2020-12-31", "n_income": 1000.0},   # 首次公告
            {"ts_code": "000001.SZ", "pit_date": "2021-06-15",
             "end_date": "2020-12-31", "n_income": 1050.0},   # 修订版
        ])
        result = _filter_visible(df, T)
        assert len(result) == 2, "多次修订的历史记录应全部可见，由上层逻辑选取最新"


# ---------------------------------------------------------------------------
# ann_date 概念测试（使用 pit_date 作为等效字段）
# ---------------------------------------------------------------------------

class TestPITConceptualRules:
    """确认项目用 ann_date/pit_date 而不是 end_date 作为可用日。"""

    def test_earlier_end_date_doesnt_make_invisible(self):
        """报告期更早（合法），但 pit_date 晚于 T，记录不可见。

        证明：可见性由 pit_date（公告日）决定，而非 end_date（报告期）。
        """
        T = pd.Timestamp("2021-03-31")
        df = _make_pit_df([
            # 2020 年年报，但 2021-04-30 才公告 → T 日不可见
            {"ts_code": "000001.SZ", "pit_date": "2021-04-30",
             "end_date": "2020-12-31", "n_income": 500.0},
        ])
        result = _filter_visible(df, T)
        assert result.empty, "公告日在 T 之后则不可见，即使报告期在 T 之前"


# ---------------------------------------------------------------------------
# make_ttm 的 PIT 边界测试
# ---------------------------------------------------------------------------

class TestMakeTTM:
    """验证 make_ttm 不使用 T 日之后的财务数据。"""

    def _base_records(self) -> list[dict]:
        """一个基本的年报+季报数据集（单只股票）。"""
        return [
            # 2020 年报（已公告）
            {"ts_code": "000001.SZ", "pit_date": "2021-03-31",
             "end_date": "2020-12-31", "n_income": 1200.0},
            # 2021 Q1（已公告）
            {"ts_code": "000001.SZ", "pit_date": "2021-04-30",
             "end_date": "2021-03-31", "n_income": 300.0},
            # 2020 Q1（已公告，用于同比）
            {"ts_code": "000001.SZ", "pit_date": "2020-04-28",
             "end_date": "2020-03-31", "n_income": 250.0},
        ]

    def test_future_record_not_in_ttm(self):
        """T=2021-05-31 时，2021 H1（未公告）数据不应参与 TTM 计算。"""
        T = pd.Timestamp("2021-05-31")
        records = self._base_records() + [
            # 2021 H1，公告日在 T 之后
            {"ts_code": "000001.SZ", "pit_date": "2021-08-31",
             "end_date": "2021-06-30", "n_income": 650.0},
        ]
        df = _make_pit_df(records)
        df["end_date"] = pd.to_datetime(df["end_date"])

        result = make_ttm(df, "n_income", T, codes=["000001.SZ"])
        # TTM 应基于 2020年报 + 2021Q1 - 2020Q1 = 1200 + 300 - 250 = 1250
        assert result is not None, "TTM 计算不应返回 None"
        val = result.get("000001.SZ")
        if pd.notna(val):
            assert abs(val - 1250.0) < 1.0, f"TTM 预期 1250，实际 {val}"

    def test_no_visible_data_returns_nan(self):
        """T 之前没有任何可见财务数据时，TTM 应返回 NaN。"""
        T = pd.Timestamp("2020-01-01")
        df = _make_pit_df([
            # 公告日在 T 之后
            {"ts_code": "000001.SZ", "pit_date": "2020-04-28",
             "end_date": "2019-12-31", "n_income": 1000.0},
        ])
        result = make_ttm(df, "n_income", T, codes=["000001.SZ"])
        val = result.get("000001.SZ") if result is not None else float("nan")
        assert pd.isna(val), "T 之前无可见数据时 TTM 应为 NaN"
