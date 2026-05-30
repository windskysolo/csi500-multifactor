from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

# D-01-B: TE 扫描，15%
# 复用信号：--from-stage portfolio --input-signal-run 20260528_133249__baseline_icir_te6_lam0050
SPEC = ExperimentSpec(
    experiment_id="icir_te15_lam0050",
    description=(
        "D-01-B 诊断：IC_IR 信号 + QP TE=15%。"
        "与 TE=6%(IR=0.402)、TE=10% 和 TopN50(IR=0.924) 对比，"
        "验证高 TE 目标下 QP 能否接近 TopN50 的 IR 水平。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="icir",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
    ),
    optimizer=OptimizerSpec(
        optimizer_mode="qp",
        te_target_annual=0.15,
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
