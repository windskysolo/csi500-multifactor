from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

# D-07: 去掉 TE 二次约束，只保留线性约束（单股±1.5%、行业±3%）
# 目的：隔离"TE 约束"对 IR 的损耗。若 IR 显著高于 icir+QP(0.402)，
# 说明 TE=6% 约束是 QP 劣于 TopN50 的主要原因。
# 复用信号：--from-stage portfolio --input-signal-run 20260528_133249__baseline_icir_te6_lam0050
SPEC = ExperimentSpec(
    experiment_id="icir_l2_forced",
    description=(
        "D-07 诊断：IC_IR 信号 + 纯 L2（无跟踪误差二次约束）。"
        "仅保留线性约束（单股±1.5%、行业±3%），与 icir_te6(IR=0.402) 和 TopN50(IR=0.924) 对比，"
        "量化 TE 约束对 IR 的贡献。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="icir",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
    ),
    optimizer=OptimizerSpec(
        optimizer_mode="l2_forced",
        te_target_annual=0.06,    # 仅作记录用，l2_forced 不使用此值
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
