from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

# 注：decay weighted expanding 训练模式尚未在 src/signal/ 中实现，本 spec 预注册占位。
# 必须与 hl36/hl48 同时预注册，禁止看完结果后补注册候选线。
SPEC = ExperimentSpec(
    experiment_id="challenger_decay_ridge_hl24_te6_lam0050",
    description="候选线：decay 加权 expanding Ridge，半衰期 24 个月 + TE 6% + lambda 0.005",
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
