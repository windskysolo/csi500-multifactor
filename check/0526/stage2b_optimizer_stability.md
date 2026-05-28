# 阶段 2b：优化器重建稳定性审查

- 生成时间：2026-05-26 20:21:37
- 正式测试集：未运行。
- 重建次数：3。
- 重建产物写入：`check/0526/tmp/stage2b/`。
- 重要警示：`artifact_drift_after_stage2b.md` 显示 `experiments/turnover_lambda_grid/results/lam_0050` 已相对阶段 0 冻结记录发生漂移。本报告中的 `existing_pipeline_b` 指“当前文件系统中的 lam_0050 产物”，不是阶段 0/1 复算时的旧产物版本。

## 静态输入哈希

- 已导出 `stage2b_static_input_hash.csv`，共 144 个调仓日。
- 哈希覆盖 alpha、benchmark weight、covariance、industry、停牌、涨停、跌停状态；不包含动态 `w_prev`。

## 每次重建结果

- `rebuild_01`: IR=0.502677, 年化超额=0.0299197, 月胜率=0.478261, TE=0.0595207, solver={"optimal": 142, "optimal_inaccurate": 2}, fallback={"0": 144}
- `rebuild_02`: IR=0.502677, 年化超额=0.0299197, 月胜率=0.478261, TE=0.0595207, solver={"optimal": 142, "optimal_inaccurate": 2}, fallback={"0": 144}
- `rebuild_03`: IR=0.502677, 年化超额=0.0299197, 月胜率=0.478261, TE=0.0595207, solver={"optimal": 142, "optimal_inaccurate": 2}, fallback={"0": 144}

## 权重和 NAV 差异

- `rebuild_01` / current_rebuild_vs_existing_pipeline_b: max_weight_diff=8.90949e-10, material_dates=0, first_material_date=
- `rebuild_02` / current_rebuild_vs_existing_pipeline_b: max_weight_diff=8.90949e-10, material_dates=0, first_material_date=
- `rebuild_03` / current_rebuild_vs_existing_pipeline_b: max_weight_diff=8.90949e-10, material_dates=0, first_material_date=
- `rebuild_02` / rebuild_02_vs_rebuild_01: max_weight_diff=0, material_dates=0, first_material_date=
- `rebuild_03` / rebuild_03_vs_rebuild_01: max_weight_diff=0, material_dates=0, first_material_date=
- `rebuild_01` / current_rebuild_vs_existing_pipeline_b: max_strategy_nav_diff=4.91251e-12, max_excess_nav_diff=4.42624e-12, first_diff=
- `rebuild_02` / current_rebuild_vs_existing_pipeline_b: max_strategy_nav_diff=4.91251e-12, max_excess_nav_diff=4.42624e-12, first_diff=
- `rebuild_03` / current_rebuild_vs_existing_pipeline_b: max_strategy_nav_diff=4.91251e-12, max_excess_nav_diff=4.42624e-12, first_diff=
- `rebuild_02` / rebuild_02_vs_rebuild_01: max_strategy_nav_diff=0, max_excess_nav_diff=0, first_diff=
- `rebuild_03` / rebuild_03_vs_rebuild_01: max_strategy_nav_diff=0, max_excess_nav_diff=0, first_diff=

## 关键 metrics 差异

- `rebuild_01` / current_rebuild_vs_existing_pipeline_b / `excess_return`: old=0.0299197, new=0.0299197, diff=2.08655e-12
- `rebuild_01` / current_rebuild_vs_existing_pipeline_b / `information_ratio`: old=0.502677, new=0.502677, diff=3.44298e-11
- `rebuild_01` / current_rebuild_vs_existing_pipeline_b / `monthly_win_rate`: old=0.478261, new=0.478261, diff=0
- `rebuild_01` / current_rebuild_vs_existing_pipeline_b / `tracking_error`: old=0.0595207, new=0.0595207, diff=7.41282e-14
- `rebuild_02` / current_rebuild_vs_existing_pipeline_b / `excess_return`: old=0.0299197, new=0.0299197, diff=2.08655e-12
- `rebuild_02` / current_rebuild_vs_existing_pipeline_b / `information_ratio`: old=0.502677, new=0.502677, diff=3.44298e-11
- `rebuild_02` / current_rebuild_vs_existing_pipeline_b / `monthly_win_rate`: old=0.478261, new=0.478261, diff=0
- `rebuild_02` / current_rebuild_vs_existing_pipeline_b / `tracking_error`: old=0.0595207, new=0.0595207, diff=7.41282e-14
- `rebuild_03` / current_rebuild_vs_existing_pipeline_b / `excess_return`: old=0.0299197, new=0.0299197, diff=2.08655e-12
- `rebuild_03` / current_rebuild_vs_existing_pipeline_b / `information_ratio`: old=0.502677, new=0.502677, diff=3.44298e-11
- `rebuild_03` / current_rebuild_vs_existing_pipeline_b / `monthly_win_rate`: old=0.478261, new=0.478261, diff=0
- `rebuild_03` / current_rebuild_vs_existing_pipeline_b / `tracking_error`: old=0.0595207, new=0.0595207, diff=7.41282e-14
- `rebuild_02` / rebuild_02_vs_rebuild_01 / `excess_return`: old=0.0299197, new=0.0299197, diff=0
- `rebuild_02` / rebuild_02_vs_rebuild_01 / `information_ratio`: old=0.502677, new=0.502677, diff=0
- `rebuild_02` / rebuild_02_vs_rebuild_01 / `monthly_win_rate`: old=0.478261, new=0.478261, diff=0
- `rebuild_02` / rebuild_02_vs_rebuild_01 / `tracking_error`: old=0.0595207, new=0.0595207, diff=0
- `rebuild_03` / rebuild_03_vs_rebuild_01 / `excess_return`: old=0.0299197, new=0.0299197, diff=0
- `rebuild_03` / rebuild_03_vs_rebuild_01 / `information_ratio`: old=0.502677, new=0.502677, diff=0
- `rebuild_03` / rebuild_03_vs_rebuild_01 / `monthly_win_rate`: old=0.478261, new=0.478261, diff=0
- `rebuild_03` / rebuild_03_vs_rebuild_01 / `tracking_error`: old=0.0595207, new=0.0595207, diff=0

## 阶段 2b 判定

- 多次当前重建之间稳定，且与当前文件系统中的 Pipeline B 权重一致；阶段 2b 未发现“当前代码 + 当前产物”下的优化器非确定性。
- 但由于正式实验产物已相对阶段 0 冻结记录漂移，不能把本报告直接用于解释阶段 0/1 中 Pipeline B IR=0.483493 的旧产物；旧产物版本需要从备份或 git/artifact 存档恢复后才能继续做严格对比。
