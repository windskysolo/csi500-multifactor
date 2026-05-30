from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="ablation_no_high_52w_v2",
    description=(
        "消融实验：移除 high_52w_v2（健康状态 WARN，验证期 adj_ic_ir_24m 偏低）。"
        "其余配置与主基线完全一致。"
        "目标：量化 52 周高动量因子对基线 IR 的边际贡献。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
        exclude_factors=["high_52w_v2"],
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
