# 训练/验证结果产品化审查计划

生成日期：2026-05-21

本文件夹用于正式测试集 Run #2 之前的结果核查阶段。目标不是把 2022 验证期结果调好看，而是把“数据 → 因子 → 信号 → 组合 → 回测 → 归因 → 报告”的每一步产出按可发布产品标准审查，先确认结论可信，再决定哪些改进值得做。

## 当前边界

- 正式测试集为 2023-2025，历史 Run #1 已消耗且作废；当前只剩 Run #2 一次正式机会。
- 本审查阶段不得运行正式测试集，不得用 2023-2025 的结果做任何调参。
- 当前训练/验证公共流水线已跑通，最近完整 manifest 为 `data/processed/run_manifests/run_20260520_212950.json`。
- 当前验证期 V2 未达标：2022 年 V2 年化超额约 -0.86%，IR 约 -0.191；V1 baseline 年化超额约 +1.26%，IR 约 0.173。
- 当前权重 meta 显示 L1=67、L2=2、L3=15；15 期 `constraint_compliant=False`，必须作为产品风险审查。

## 文件说明

- `01_stage_audit_plan.md`：逐阶段审查计划、检查项、退出标准和改进方向。
- `02_current_artifact_triage.md`：基于当前产物的第一轮问题分诊，标出优先核查入口。
- `03_review_templates.md`：审查记录、改进实验、发布前门禁的模板。
- `04_step_by_step_execution_plan.md`：可逐阶段照做的审查执行手册，含停止条件、交付物和 Go/No-Go 门禁。
- `05_performance_diagnosis_plan.md`：效果不好原因诊断优先版计划，安全性只做轻量复核，重点判断结果好坏与原因归因。
- `performance_stage_01_scorecard.md`：阶段 0-1 的实际审查记录，覆盖测试集隔离轻量复核与 V1/V2 总体效果判定。
- `performance_stage_02_factor_diagnosis.md`：阶段 2 的实际审查记录，判断 6 个入模因子的训练/验证有效性、稳定性和失效原因。
- `performance_stage_03_signal_diagnosis.md`：阶段 3 的实际审查记录，比较等权与 IC_IR 合成，并核查滚动权重是否拖累。
- `performance_stage_04_optimizer_diagnosis.md`：阶段 4 的实际审查记录，判断 V2 优化是否通过分散化、行业/单股约束和 TE 目标稀释 alpha。
- `performance_stage_05_cost_diagnosis.md`：阶段 5 的实际审查记录，复核交易成本、换手、成交约束和现金拖累是否解释 V2 表现不佳。
- `performance_stage_06_attribution_diagnosis.md`：阶段 6 的实际审查记录，拆解 Brinson、行业、因子归因和 NAV 对账质量。
- `performance_stage_07_cause_tree.md`：阶段 7 的实际审查记录，把阶段 0-6 证据合并成原因排序、排除项和阶段 8 实验输入。

## 执行原则

1. 先查可信度，再查效果。
2. 每个改进必须有“问题证据 → 假设 → 非测试集验证 → 是否采纳”的记录。
3. 验证期 2022 只能用于稳健性判断，不能被当作新的训练集反复迎合。
4. 若发现未来函数、基准口径、PIT、成交假设或测试集污染问题，立即停止效果优化，先修口径。
5. 所有发布结论必须能从当前源码、配置、manifest 和 parquet 产物重建。
