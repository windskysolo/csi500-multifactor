from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec


SPEC = ExperimentSpec(
    experiment_id="frozen_baseline_icir_topn50_ew",
    description=(
        "Frozen baseline: ICIR signal plus forced TopN equal-weight portfolio. "
        "No QP optimizer is used; the portfolio path reuses L3 halt/limit handling."
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="icir",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
    ),
    optimizer=OptimizerSpec(
        optimizer_mode="topn_ew",
        topn=50,
    ),
    backtest=BacktestSpec(),
)
