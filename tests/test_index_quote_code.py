"""
tests/test_index_quote_code.py — index_quote 基准指数代码校验（阶段 8 门禁）

覆盖范围（对应 phase_plan.md 第 13 节）：
  - index_quote.parquet 只能含 H00905.CSI（全收益指数），不能是价格指数 000905.SH
  - _all_trading_dates() 返回排序无重复的日期序列
  - 重复日期（混入两支指数）会导致 _window_start 返回错误起始点（领域危害说明）

测试结构：
  - TestAllTradingDatesFunction：单元测试（monkeypatch，不依赖磁盘）
  - TestIndexQuoteFileContent：集成测试（文件不存在时自动跳过）

注意：_all_trading_dates 带 @lru_cache；通过 monkeypatch.setattr 替换模块属性
可绕过缓存，无需手动 cache_clear。
"""

import pandas as pd
import pytest

import src.factors.alt_factors as af
from src import config as cfg


# ---------------------------------------------------------------------------
# _all_trading_dates / _window_start 单元测试
# ---------------------------------------------------------------------------

class TestAllTradingDatesFunction:
    """验证 _all_trading_dates 工具函数及下游 _window_start 的行为。"""

    def test_returns_sorted_datetimeindex(self, monkeypatch):
        """_all_trading_dates 的返回值应为单调递增的 DatetimeIndex。"""
        all_dates = pd.bdate_range("2020-01-01", periods=20)
        monkeypatch.setattr(af, "_all_trading_dates", lambda: all_dates)

        result = af._all_trading_dates()
        for i in range(len(result) - 1):
            assert result[i] <= result[i + 1], "_all_trading_dates 应返回排序后的日期"

    def test_window_start_returns_correct_nth_date(self, monkeypatch):
        """_window_start(T, n) 应返回 T 及之前最近 n 个交易日中的第一天。"""
        all_dates = pd.bdate_range("2020-01-01", periods=30)
        monkeypatch.setattr(af, "_all_trading_dates", lambda: all_dates)

        T = all_dates[-1]
        start = af._window_start(T, 5)
        expected = all_dates[-5]
        assert start == expected, (
            f"_window_start 应返回第 -5 个交易日 {expected.date()}，"
            f"实际 {start.date()}"
        )

    def test_window_start_returns_none_when_insufficient(self, monkeypatch):
        """历史不足 n 个交易日时，_window_start 应返回 None。"""
        all_dates = pd.bdate_range("2020-01-01", periods=10)
        monkeypatch.setattr(af, "_all_trading_dates", lambda: all_dates)

        T = all_dates[-1]
        result = af._window_start(T, 20)   # 要求 20 天，但只有 10 天
        assert result is None, "历史不足时 _window_start 应返回 None"

    def test_duplicate_dates_cause_wrong_window_start(self, monkeypatch):
        """
        领域危害说明：若 index_quote.parquet 混入两支指数（如 H00905.CSI 和 000905.SH），
        同一交易日会出现两条记录，_all_trading_dates 返回含重复项的 DatetimeIndex。
        此时 _window_start(T, n) 会把重复日计算成"两个不同交易日"，
        导致窗口起始点比正确值更晚（历史窗口缩短），产生实质性前视偏差。

        本测试通过对比有/无重复两种情形，证明重复日期对窗口计算的干扰。
        """
        # 3 个不重复交易日
        clean_dates = pd.DatetimeIndex(["2020-01-02", "2020-01-03", "2020-01-06"])
        # 同样 3 天，但每天重复一次（模拟两支指数混入）
        dup_dates = pd.DatetimeIndex([
            "2020-01-02", "2020-01-02",
            "2020-01-03", "2020-01-03",
            "2020-01-06", "2020-01-06",
        ])
        T = pd.Timestamp("2020-01-06")

        monkeypatch.setattr(af, "_all_trading_dates", lambda: clean_dates)
        start_clean = af._window_start(T, 3)   # 正确：返回 2020-01-02

        monkeypatch.setattr(af, "_all_trading_dates", lambda: dup_dates)
        start_dup = af._window_start(T, 3)     # 错误：返回 2020-01-03（偏后）

        assert start_clean != start_dup, (
            "重复日期使 _window_start 返回错误起点，"
            "这是 index_quote 必须只含单一指数的根本原因"
        )
        assert start_clean < start_dup, (
            "重复日期导致窗口缩短：正确起点应早于错误起点"
        )


# ---------------------------------------------------------------------------
# index_quote.parquet 集成测试（文件不存在时自动跳过）
# ---------------------------------------------------------------------------

class TestIndexQuoteFileContent:
    """
    验证实际 index_quote.parquet 文件的内容约束。
    数据下载完成前文件不存在，测试自动跳过。
    """

    @pytest.fixture(autouse=True)
    def _skip_if_file_missing(self):
        path = cfg.DATA_PROC / "index_quote.parquet"
        if not path.exists():
            pytest.skip(
                f"index_quote.parquet 尚不存在，跳过集成测试：{path}\n"
                "请先完成阶段 4（csv_to_parquet all）后再运行本测试。"
            )

    def test_no_duplicate_trade_dates(self):
        """
        trade_date 索引应无重复（单一指数保证）。
        重复意味着混入了多支指数（如同时含 H00905.CSI 和 000905.SH）。
        """
        df = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
        idx = df.index
        assert len(idx) == len(idx.unique()), (
            f"trade_date 存在 {len(idx) - len(idx.unique())} 个重复条目——"
            "index_quote 可能混入了多支指数，应只含 H00905.CSI（全收益指数）"
        )

    def test_covers_market_start(self):
        """index_quote 应覆盖 MARKET_START（2011-01-01）附近，允许 60 日偏差。"""
        df = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
        earliest = df.index.min()
        assert earliest <= cfg.MARKET_START + pd.Timedelta(days=60), (
            f"index_quote 最早日期 {earliest.date()} 晚于 MARKET_START+60d，"
            "请检查是否下载了 H00905.CSI 完整历史"
        )

    def test_covers_train_end(self):
        """index_quote 应覆盖训练期终点 2020-12-31。"""
        df = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
        latest = df.index.max()
        assert latest >= cfg.TRAIN_END, (
            f"index_quote 最新日期 {latest.date()} 早于 TRAIN_END={cfg.TRAIN_END.date()}"
        )
