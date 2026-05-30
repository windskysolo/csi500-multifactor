from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="rolling48_ridge_topn50_ew",
    description=(
        "Rolling 48m Ridge信号 + TopN50等权，无QP。"
        "回答核心问题：rolling-48m IR=1.489是信号本身优秀还是QP友好。"
        "与冻结基线(ICIR+TopN50 EW IR=0.924)直接比较纯选股质量。"
        "复用信号: challenger_rolling48_te6_lam0050"
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
        topn=50,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
