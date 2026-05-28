"""
tests/test_data_expansion_config.py — 数据扩展配置一致性测试（阶段 8 门禁）

覆盖范围（对应 phase_plan.md 第 13 节）：
  - 样本切分日期与 phase_plan.md 第 0 节完全一致（2012-2020 / 2021-2022 / 2023-2025）
  - 日期单调性：EARLY_START < MARKET_START < TRAIN_START < TRAIN_END < VALID_END < TEST_END
  - 训练/验证/测试三段连续无间隔（日历天数差 1~2）
  - ALT_DATA_SUBDIRS 包含全部 12 个预期子目录，无重复，无多余
  - 交易成本关键常量（印花税切换日期、数值）正确

全部为 config 常量检查，不依赖磁盘文件，测试随时可运行。
"""

import pandas as pd
import pytest

from src import config as cfg


# ---------------------------------------------------------------------------
# 样本切分日期
# ---------------------------------------------------------------------------

class TestSampleSplitDates:
    """验证 6 个样本切分日期与 phase_plan.md 第 0 节定义完全一致。"""

    def test_train_start_is_2012_01_01(self):
        assert cfg.TRAIN_START == pd.Timestamp("2012-01-01"), (
            "TRAIN_START 应为 2012-01-01（数据扩展目标：从旧的 2016 切到 2012）"
        )

    def test_train_end_is_2020_12_31(self):
        assert cfg.TRAIN_END == pd.Timestamp("2020-12-31")

    def test_valid_start_is_2021_01_01(self):
        assert cfg.VALID_START == pd.Timestamp("2021-01-01")

    def test_valid_end_is_2022_12_31(self):
        assert cfg.VALID_END == pd.Timestamp("2022-12-31")

    def test_test_start_is_2023_01_01(self):
        assert cfg.TEST_START == pd.Timestamp("2023-01-01")

    def test_test_end_is_2025_12_31(self):
        assert cfg.TEST_END == pd.Timestamp("2025-12-31")

    def test_market_start_is_2011_01_01(self):
        assert cfg.MARKET_START == pd.Timestamp("2011-01-01"), (
            "MARKET_START=2011，为 TRAIN_START=2012 提供 1 年行情缓冲"
        )

    def test_early_start_is_2010_01_01(self):
        assert cfg.EARLY_START == pd.Timestamp("2010-01-01"), (
            "EARLY_START=2010，为财务/股东 PIT 数据提供提前量"
        )


# ---------------------------------------------------------------------------
# 日期排序与连续性
# ---------------------------------------------------------------------------

class TestDateOrdering:
    """验证各时间边界的单调性与段间连续性。"""

    def test_early_start_before_market_start(self):
        assert cfg.EARLY_START < cfg.MARKET_START, (
            "财务/股东数据起点（EARLY_START）应早于行情数据起点（MARKET_START）"
        )

    def test_market_start_before_train_start(self):
        assert cfg.MARKET_START < cfg.TRAIN_START, (
            "行情起点（MARKET_START=2011）应早于训练起点（TRAIN_START=2012），提供历史缓冲"
        )

    def test_train_before_valid(self):
        assert cfg.TRAIN_END < cfg.VALID_START, (
            "训练期终点应早于验证期起点（两段不得重叠）"
        )

    def test_valid_before_test(self):
        assert cfg.VALID_END < cfg.TEST_START, (
            "验证期终点应早于测试期起点（两段不得重叠）"
        )

    def test_train_valid_boundary_is_continuous(self):
        """训练期结束次日即为验证期起点，允许跨年 1 天差值。"""
        gap = (cfg.VALID_START - cfg.TRAIN_END).days
        assert 1 <= gap <= 2, (
            f"训练期 {cfg.TRAIN_END.date()} → 验证期 {cfg.VALID_START.date()} "
            f"差 {gap} 天，期望 1-2 天（2020-12-31 到 2021-01-01）"
        )

    def test_valid_test_boundary_is_continuous(self):
        """验证期结束次日即为测试期起点。"""
        gap = (cfg.TEST_START - cfg.VALID_END).days
        assert 1 <= gap <= 2, (
            f"验证期 {cfg.VALID_END.date()} → 测试期 {cfg.TEST_START.date()} "
            f"差 {gap} 天，期望 1-2 天（2022-12-31 到 2023-01-01）"
        )


# ---------------------------------------------------------------------------
# ALT_DATA_SUBDIRS 完整性
# ---------------------------------------------------------------------------

class TestAltDataSubdirs:
    """验证 ALT_DATA_SUBDIRS 与 phase_plan.md 第 7.1 节定义的 12 个子目录一致。"""

    _EXPECTED: frozenset[str] = frozenset({
        "hk_hold",
        "analyst_rc",
        "share_float",
        "holder_trade",
        "cyq_perf",
        "pledge_stat",
        "hsgt_flow",
        "top_list",
        "top_inst",
        "block_trade",
        "stk_surv",
        "fina_mainbz",
    })

    def test_all_expected_subdirs_present(self):
        actual = frozenset(cfg.ALT_DATA_SUBDIRS)
        missing = self._EXPECTED - actual
        assert not missing, (
            f"ALT_DATA_SUBDIRS 缺少以下子目录：{sorted(missing)}"
        )

    def test_no_unexpected_subdirs(self):
        actual = frozenset(cfg.ALT_DATA_SUBDIRS)
        extra = actual - self._EXPECTED
        assert not extra, (
            f"ALT_DATA_SUBDIRS 包含未预期的子目录：{sorted(extra)}，"
            "若要新增接口请先更新 phase_plan.md"
        )

    def test_exactly_12_subdirs(self):
        assert len(cfg.ALT_DATA_SUBDIRS) == 12, (
            f"ALT_DATA_SUBDIRS 应有 12 个子目录，实际 {len(cfg.ALT_DATA_SUBDIRS)} 个"
        )

    def test_no_duplicate_subdir_names(self):
        names = cfg.ALT_DATA_SUBDIRS
        assert len(names) == len(set(names)), (
            "ALT_DATA_SUBDIRS 包含重复条目"
        )


# ---------------------------------------------------------------------------
# 交易成本关键常量
# ---------------------------------------------------------------------------

class TestTransactionCostConfig:
    """
    验证印花税切换日期与数值（CLAUDE.md 4.3 节硬规则）。
    印花税 2023-08-28 起由 10 bps 降至 5 bps（卖出单边）。
    """

    def test_stamp_duty_cut_date(self):
        assert cfg.STAMP_DUTY_CUT_DATE == pd.Timestamp("2023-08-28"), (
            "印花税调整日应为 2023-08-28，写错会导致成本计算系统性偏误"
        )

    def test_stamp_duty_before_is_higher_than_after(self):
        assert cfg.STAMP_DUTY_BEFORE > cfg.STAMP_DUTY_AFTER, (
            "调整前印花税（10 bps）应高于调整后（5 bps）"
        )

    def test_stamp_duty_before_value(self):
        assert cfg.STAMP_DUTY_BEFORE == pytest.approx(0.001), (
            "调整前印花税应为 10 bps（0.001）"
        )

    def test_stamp_duty_after_value(self):
        assert cfg.STAMP_DUTY_AFTER == pytest.approx(0.0005), (
            "调整后印花税应为 5 bps（0.0005）"
        )
