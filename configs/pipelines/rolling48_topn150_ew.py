from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="rolling48_topn150_ew",
    description=(
        "TopN ablation：rolling48 Ridge 信号 + TopN150 等权。"
        "更分散，探索信号稀释后 IR 下降幅度。"
        "对照：rolling48_topn50_ew(IR=1.771)。"
        "复用信号：--input-signal-run 20260527_142056__challenger_rolling48_te6_lam0050"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="rolling",
        purge_months=2,
        window_months=48,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
    ),
    optimizer=OptimizerSpec(
        optimizer_mode="topn_ew",
        topn=150,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
