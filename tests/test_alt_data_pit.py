"""
tests/test_alt_data_pit.py — 备选数据 PIT 约束测试（阶段 8 测试门禁）

覆盖范围（对应 phase_plan.md 第 13 节）：
  - analyst_rc:    pit_date (report_date) <= T
  - holder_trade:  pit_date (ann_date)    <= T
  - share_float:   ann_date <= T < float_date <= T + horizon_days

测试策略：monkeypatch loader._read_cached 注入合成 DataFrame，不依赖磁盘数据。
"""

import pandas as pd
import pytest

import src.data.loader as loader


# ---------------------------------------------------------------------------
# 构造辅助函数
# ---------------------------------------------------------------------------

def _make_analyst_df(records: list[dict]) -> pd.DataFrame:
    """构造 analyst_rc_pit 格式的合成 DataFrame（RangeIndex，pit_date 为普通列）。"""
    df = pd.DataFrame(records)
    df["pit_date"] = pd.to_datetime(df["pit_date"])
    return df


def _make_holder_df(records: list[dict]) -> pd.DataFrame:
    """构造 holder_trade_pit 格式的合成 DataFrame。"""
    df = pd.DataFrame(records)
    df["pit_date"] = pd.to_datetime(df["pit_date"])
    return df


def _make_share_float_df(records: list[dict]) -> pd.DataFrame:
    """构造 share_float 格式的合成 DataFrame。"""
    df = pd.DataFrame(records)
    df["ann_date"]   = pd.to_datetime(df["ann_date"])
    df["float_date"] = pd.to_datetime(df["float_date"])
    return df


# ---------------------------------------------------------------------------
# load_analyst_rc_pit 的 PIT 约束
# ---------------------------------------------------------------------------

class TestAnalystRcPIT:
    """验证 load_analyst_rc_pit 严格执行 pit_date(report_date) <= T 过滤。"""

    def test_future_pit_date_excluded(self, monkeypatch):
        """report_date > T 的研报不应可见。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_analyst_df([
            {"pit_date": "2021-07-01", "ts_code": "000001.SZ", "eps": 1.0, "rating": "买入"},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_analyst_rc_pit(T, codes=["000001.SZ"])
        assert result.empty, "pit_date > T 的研报不应可见"

    def test_pit_date_on_T_is_visible(self, monkeypatch):
        """report_date == T（当日研报）应可见。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_analyst_df([
            {"pit_date": "2021-06-30", "ts_code": "000001.SZ", "eps": 1.0, "rating": "买入"},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_analyst_rc_pit(T, codes=["000001.SZ"])
        assert len(result) == 1, "pit_date == T 的研报应可见"

    def test_lookback_window_excludes_old_records(self, monkeypatch):
        """超出 lookback_days 的研报不应返回（即使 pit_date <= T）。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_analyst_df([
            {"pit_date": "2021-01-01", "ts_code": "000001.SZ", "eps": 1.0, "rating": "买入"},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        # lookback_days=30：2021-01-01 距 T 约 180 天，超出窗口
        result = loader.load_analyst_rc_pit(T, codes=["000001.SZ"], lookback_days=30)
        assert result.empty, "超出 lookback_days 的研报不应返回"

    def test_only_valid_record_returned_when_mixed(self, monkeypatch):
        """合法记录与未来记录混合时，只返回合法的。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_analyst_df([
            {"pit_date": "2021-06-01", "ts_code": "000001.SZ", "eps": 1.0, "rating": "增持"},
            {"pit_date": "2021-07-15", "ts_code": "000001.SZ", "eps": 2.0, "rating": "买入"},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_analyst_rc_pit(T, codes=["000001.SZ"])
        assert len(result) == 1
        assert float(result["eps"].iloc[0]) == pytest.approx(1.0), "只应返回合法研报"

    def test_codes_filter_applies(self, monkeypatch):
        """codes 白名单正确过滤股票。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_analyst_df([
            {"pit_date": "2021-06-01", "ts_code": "000001.SZ", "eps": 1.0, "rating": "买入"},
            {"pit_date": "2021-06-01", "ts_code": "000002.SZ", "eps": 2.0, "rating": "增持"},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_analyst_rc_pit(T, codes=["000001.SZ"])
        assert set(result["ts_code"]) == {"000001.SZ"}


# ---------------------------------------------------------------------------
# load_holder_trade_pit 的 PIT 约束
# ---------------------------------------------------------------------------

class TestHolderTradePIT:
    """验证 load_holder_trade_pit 严格执行 pit_date(ann_date) <= T 过滤。"""

    def test_future_pit_date_excluded(self, monkeypatch):
        """ann_date > T 的增减持公告不应可见。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_holder_df([
            {"pit_date": "2021-07-10", "ts_code": "000001.SZ",
             "in_de": "增", "change_ratio": 0.01},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_holder_trade_pit(T, codes=["000001.SZ"])
        assert result.empty, "ann_date > T 的增减持记录不应可见"

    def test_pit_date_on_T_is_visible(self, monkeypatch):
        """ann_date == T 的事件应可见。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_holder_df([
            {"pit_date": "2021-06-30", "ts_code": "000001.SZ",
             "in_de": "增", "change_ratio": 0.01},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_holder_trade_pit(T, codes=["000001.SZ"])
        assert len(result) == 1

    def test_in_de_field_preserved(self, monkeypatch):
        """增持/减持方向字段正确保留，供因子层使用。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_holder_df([
            {"pit_date": "2021-05-01", "ts_code": "000001.SZ",
             "in_de": "增", "change_ratio": 0.02},
            {"pit_date": "2021-04-01", "ts_code": "000001.SZ",
             "in_de": "减", "change_ratio": 0.01},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_holder_trade_pit(T, codes=["000001.SZ"])
        assert set(result["in_de"]) == {"增", "减"}, "in_de 方向字段应完整保留"

    def test_lookback_window_excludes_old_events(self, monkeypatch):
        """超出 lookback_days 窗口的事件应被排除。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_holder_df([
            {"pit_date": "2021-01-01", "ts_code": "000001.SZ",
             "in_de": "增", "change_ratio": 0.01},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_holder_trade_pit(T, codes=["000001.SZ"], lookback_days=30)
        assert result.empty, "超出 lookback_days 的事件应被排除"


# ---------------------------------------------------------------------------
# load_share_float 的三重 PIT 过滤
# ---------------------------------------------------------------------------

class TestShareFloatPIT:
    """
    验证 load_share_float 的三重时间过滤：
      ann_date <= T  AND  float_date > T  AND  float_date <= T + horizon_days
    """

    def test_future_ann_date_excluded(self, monkeypatch):
        """ann_date > T 的解禁公告尚未发布，不可见。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_share_float_df([
            {"ts_code": "000001.SZ", "ann_date": "2021-07-05",
             "float_date": "2021-07-20", "float_ratio": 5.0, "share_type": "限售股"},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_share_float(T, horizon_days=30, codes=["000001.SZ"])
        assert result.empty, "ann_date > T 的解禁公告不应可见"

    def test_already_unlocked_excluded(self, monkeypatch):
        """float_date <= T 的解禁已发生，不在前瞻窗口内，不应返回。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_share_float_df([
            {"ts_code": "000001.SZ", "ann_date": "2021-06-01",
             "float_date": "2021-06-25", "float_ratio": 5.0, "share_type": "限售股"},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_share_float(T, horizon_days=30, codes=["000001.SZ"])
        assert result.empty, "float_date <= T 已解禁，不在前瞻窗口"

    def test_float_date_beyond_horizon_excluded(self, monkeypatch):
        """float_date > T + horizon_days 超出前瞻窗口，不应返回。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_share_float_df([
            {"ts_code": "000001.SZ", "ann_date": "2021-06-01",
             "float_date": "2021-08-15", "float_ratio": 5.0, "share_type": "限售股"},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_share_float(T, horizon_days=30, codes=["000001.SZ"])
        assert result.empty, "float_date > T+30 超出前瞻窗口"

    def test_valid_unlock_event_visible_with_correct_ratio(self, monkeypatch):
        """三个条件均满足时，记录可见且 float_ratio 正确。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_share_float_df([
            {"ts_code": "000001.SZ", "ann_date": "2021-06-15",
             "float_date": "2021-07-20", "float_ratio": 8.5, "share_type": "限售股"},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_share_float(T, horizon_days=30, codes=["000001.SZ"])
        assert not result.empty
        assert "000001.SZ" in result.index
        assert float(result.loc["000001.SZ", "float_ratio"]) == pytest.approx(8.5)

    def test_all_three_conditions_required(self, monkeypatch):
        """三重过滤缺一不可：只有同时满足三个条件的记录可见。"""
        T = pd.Timestamp("2021-06-30")
        raw = _make_share_float_df([
            # 合法：所有条件满足
            {"ts_code": "000001.SZ", "ann_date": "2021-06-15",
             "float_date": "2021-07-20", "float_ratio": 5.0, "share_type": "限售股"},
            # 违规：ann_date 未来
            {"ts_code": "000002.SZ", "ann_date": "2021-07-01",
             "float_date": "2021-07-20", "float_ratio": 3.0, "share_type": "限售股"},
            # 违规：float_date 已过
            {"ts_code": "000003.SZ", "ann_date": "2021-06-01",
             "float_date": "2021-06-25", "float_ratio": 4.0, "share_type": "限售股"},
        ])
        monkeypatch.setattr(loader, "_read_cached", lambda f: raw)

        result = loader.load_share_float(T, horizon_days=30)
        assert set(result.index) == {"000001.SZ"}, "只有同时满足三个条件的记录可见"
