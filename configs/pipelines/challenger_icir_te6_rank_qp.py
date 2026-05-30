from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="challenger_icir_te6_rank_qp",
    description=(
        "ICIR 信号 + 排名百分位变换 + QP（TE=6%）。"
        "验证假设：截面 Alpha 的排名变换能否修复 QP 的噪声放大问题。"
        "对比：冻结基线 ICIR+TopN50 IR=0.924；QP 基线 ICIR+QP IR=0.402。"
        "可证伪：IR≥0.7 → 排名变换有效；IR<0.5 → 重新诊断。"
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
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.005,
        topn=50,
        use_rank_transform=True,
    ),
    backtest=BacktestSpec(),
)
