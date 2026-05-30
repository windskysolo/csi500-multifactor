from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="expanding_ridge_topn50_ew",
    description=(
        "Expanding Ridge信号 + TopN50等权，无QP。"
        "对照冻结基线(ICIR+TopN50 EW IR=0.924)，隔离优化器影响，"
        "确认expanding Ridge纯选股能力基准。"
        "复用信号: baseline_expanding_ridge_te6_lam0050"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
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
