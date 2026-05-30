from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="ablation_no_piotroski_f",
    description=(
        "消融实验：移除 piotroski_f（REMOVE_CANDIDATE，验证期 IC_IR=-0.200）。"
        "其余配置与主基线 baseline_expanding_ridge_te6_lam0050 完全一致。"
        "目标：量化 piotroski_f 对基线 IR 的边际贡献。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
        exclude_factors=["piotroski_f"],
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
