from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="rolling48_topn150_ew_hk_quarterly",
    description=(
        "hk_hold 季度化修复后的主线重跑（2026-06 新线）。"
        "hk_hold_ratio / hk_hold_chg 已改为季度 PIT 快照因子（2024-08-19 披露频率变更修复）。"
        "其余 59 个因子面板直接复用，仅信号+下游重跑。"
        "对照基准：rolling48_topn150_ew(run_id=20260530_111129, IR=2.274)。"
        "关联问题：current work/6.1/issues_and_repair_notes.md P0（hk_hold 全空月）。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="rolling",
        purge_months=2,
        window_months=48,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
    ),
    optimizer=OptimizerSpec(
        optimizer_mode="topn_ew",
        topn=150,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
