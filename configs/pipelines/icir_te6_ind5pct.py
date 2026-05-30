from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

# D-02-A: 行业约束放松至 ±5%
# 复用信号：--from-stage portfolio --input-signal-run 20260528_133249__baseline_icir_te6_lam0050
SPEC = ExperimentSpec(
    experiment_id="icir_te6_ind5pct",
    description=(
        "D-02-A 诊断：IC_IR 信号 + QP TE=6% + 行业约束放松至 ±5%（原 ±3%）。"
        "与 ±3%(IR=0.402) 和 TopN50(IR=0.924) 对比，量化行业约束对 IR 的贡献。"
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
        te_target_annual=0.06,
        industry_max_dev=0.05,
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
