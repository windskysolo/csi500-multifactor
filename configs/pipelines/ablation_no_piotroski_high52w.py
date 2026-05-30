from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="ablation_no_piotroski_high52w",
    description=(
        "消融实验：同时移除 piotroski_f 和 high_52w_v2。"
        "其余配置与主基线完全一致。"
        "目标：了解两者共同移除后的 IR 下界，以及协同效应。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
        exclude_factors=["piotroski_f", "high_52w_v2"],
    ),
    optimizer=OptimizerSpec(
        te_target_annual=0.06,
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.005,
        topn=50,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
