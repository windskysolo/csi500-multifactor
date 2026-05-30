from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="decay_hl24_ridge_topn50_ew",
    description=(
        "Decay hl24 Ridge信号 + TopN50等权，无QP。"
        "完成三角对比：expanding / decay-hl24 / rolling-48m 三种信号的纯选股质量排序。"
        "复用信号: challenger_decay_ridge_hl24_te6_lam0050"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="decay_weighted_expanding",
        purge_months=2,
        half_life_months=24,
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
