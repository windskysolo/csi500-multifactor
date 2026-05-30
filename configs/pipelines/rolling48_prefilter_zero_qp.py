from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="rolling48_prefilter_zero_qp",
    description=(
        "方法A：先 Top50 预筛（非 top50 的 alpha 清零），再全量 QP 优化。"
        "对照组：rolling48_ridge_topn50_ew（IR=1.771）和 challenger_rolling48_te6_lam0050（QP 全量，IR=1.489）。"
        "复用信号：--input-signal-run 20260529_081232__rolling48_ridge_topn50_ew"
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
        optimizer_mode="qp",
        te_target_annual=0.06,
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.005,
        topn=50,
        prefilter_topn=50,
        prefilter_mode="zero_alpha",
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
