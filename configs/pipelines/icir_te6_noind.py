from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

# D-02-B: 完全去除行业约束（industry_max_dev=1.0，实际上不可能触发）
# 复用信号：--from-stage portfolio --input-signal-run 20260528_133249__baseline_icir_te6_lam0050
SPEC = ExperimentSpec(
    experiment_id="icir_te6_noind",
    description=(
        "D-02-B 诊断：IC_IR 信号 + QP TE=6% + 行业约束完全去除（upper=100%，实际无约束）。"
        "与有行业约束版本(IR=0.402) 和 TopN50(IR=0.924) 对比，"
        "验证行业中性化是否是 QP 劣于 TopN50 的独立成因。"
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
        industry_max_dev=1.0,     # 实际上永远不 binding
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
