from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="challenger_icir_rolling48_topn150_ew",
    description=(
        "Rolling 48m ICIR + TopN150 EW。"
        "对标 rolling48_topn150_ew (Ridge48+TopN150, IR=2.274)。"
        "验证 ICIR 合成方式在相同窗口和选股数下的相对优劣。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="icir",
        target="excess_return",
        training_mode="rolling",
        window_months=48,
        purge_months=2,
    ),
    optimizer=OptimizerSpec(
        optimizer_mode="topn_ew",
        topn=150,
        te_target_annual=0.06,
        turnover_lambda=0.005,
        industry_max_dev=0.03,
        single_max_dev=0.015,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
