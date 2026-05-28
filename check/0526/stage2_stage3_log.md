# 2026-05-26 阶段 2/3 审查日志

- `pipeline_equivalence.md`：阶段 2 结论。
- `data_alignment_audit.md`：阶段 3 结论。
- 关键 CSV：`stage2_equivalence_summary.csv`、`stage2_metric_diff.csv`、`stage2_weight_diff.csv`、`stage3_benchmark_audit.csv`、`stage3_fwd_ret_sample.csv`、`stage3_pit_snapshot_audit.csv`。
- 正式测试集：未运行。

## 摘要

- 同一旧权重用当前回测引擎重跑等价，回测入口可复现；但当前优化器重建 expanding 与旧 Pipeline B 产物存在显著差异，rolling 与 expanding 的机制解释需先排查优化器输入、求解器稳定性或产物冻结口径。
- 已抽样验证 fwd_ret_panel 的 T+1 开盘到 T'+1 开盘口径。
- 发现 1 个 fwd_ret 标签的 exit_date 超过 VALID_END；这不是正式测试集回测，但会影响验证期 IC/训练标签边界解释，应在阶段 5 继续检查训练窗口是否按 exit_date purge。
- PIT 抽样和因子面板日期覆盖未发现明显未来数据。