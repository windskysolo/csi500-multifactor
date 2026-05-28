# 2026-05-26 审查工作总日志

本文汇总本轮围绕 `check/0526/problems.md` 已完成的工作、生成的证据文件和当前已经发现的结论。正式测试集 `2023-2025` 未运行。

## 1. 任务范围

用户目标：

1. 读取 `check/0526/problems.md`，制定阶段审查计划。
2. 按计划高质量实现阶段 0、阶段 1。
3. 继续实现阶段 2、阶段 3。
4. 汇总此前所有工作和已发现结果。

本轮审查只覆盖训练期和验证期产物，重点验证：

- Pipeline B 月度胜率 39.1% 是否是指标 bug。
- rolling-48m 验证期 IR 约 1.092 是否能从现有 NAV 独立复算。
- rolling vs expanding 对比是否存在回测入口不一致、产物新旧混用、基准口径错误、forward return 错位或 PIT 污染。

## 2. 已创建和更新的文件

### 计划文件

- `check/0526/plan.md`
  - 已根据 `problems.md` 写入完整阶段审查计划。
  - 覆盖阶段 0 到阶段 9、判定标准、输出文件、原因分类和执行优先级。

### 阶段 0/1

- `check/0526/run_stage0_stage1.py`
  - 只读项目研究产物，只写 `check/0526/`。
  - 冻结 artifact、config、git 状态。
  - 独立复算 NAV 指标和逐月超额收益。

- `check/0526/artifact_manifest.csv`
- `check/0526/config_snapshot.csv`
- `check/0526/git_status.txt`
- `check/0526/metric_recalc.csv`
- `check/0526/monthly_excess_review.csv`
- `check/0526/log.md`

### 阶段 2/3

- `check/0526/run_stage2_stage3.py`
  - 只读 `data/` 与 `experiments/` 下正式研究产物。
  - 临时重跑产物写入 `check/0526/tmp/stage2/`，未覆盖 `experiments/`。
  - 实现同一权重当前回测引擎重跑、expanding 当前优化器重建、基准审查、fwd_ret 抽样复算、PIT 抽样和因子面板日期覆盖审计。

- `check/0526/pipeline_equivalence.md`
- `check/0526/data_alignment_audit.md`
- `check/0526/stage2_stage3_log.md`
- `check/0526/stage2_equivalence_summary.csv`
- `check/0526/stage2_metric_diff.csv`
- `check/0526/stage2_weight_diff.csv`
- `check/0526/stage2_trade_log_diff.csv`
- `check/0526/stage2_fallback_diff.csv`
- `check/0526/stage2_nav_diff_pipeline_b_lam0050.csv`
- `check/0526/stage2_nav_diff_rolling_48m.csv`
- `check/0526/stage2_nav_diff_pipeline_b_rebuilt.csv`
- `check/0526/stage3_benchmark_audit.csv`
- `check/0526/stage3_benchmark_code_scan.csv`
- `check/0526/stage3_fwd_ret_audit.csv`
- `check/0526/stage3_fwd_ret_entry_exit_map.csv`
- `check/0526/stage3_fwd_ret_sample.csv`
- `check/0526/stage3_pit_snapshot_audit.csv`
- `check/0526/stage3_factor_panel_audit.csv`
- `check/0526/stage3_pit_source_scan.csv`

### 工程辅助

- `check/0526/.gitignore`
  - 忽略 `__pycache__/`。
  - 原因：对审计脚本做 `py_compile` 时生成了本地 `.pyc`，不应纳入审查证据。

## 3. 阶段 0：产物与口径冻结

执行脚本：`check/0526/run_stage0_stage1.py`

主要结果：

- 当前 commit：`7423f1e59bdd23a11b7c38474801def9ec0aeed1`
- git dirty 项数量：107
- 正式测试集：未运行。
- manifest 共记录 14 个关键对象，缺失对象数量为 0。
- `index_quote.parquet` 包含全收益 `nav` 列。
- `index_quote` 日期范围：2011-01-04 到 2025-12-31。
- Pipeline B 验证期 NAV：485 行，2021-01-04 到 2022-12-30。
- rolling-48m 验证期 NAV：485 行，2021-01-04 到 2022-12-30。
- Pipeline A 验证期 NAV：485 行，2021-01-04 到 2022-12-30。
- Ridge expanding 信号/系数、rolling-48m 信号/系数日期范围为 2014-03-31 到 2022-11-30。
- `fwd_ret_panel` 日期范围为 2012-01-31 到 2022-11-30。

阶段 0 结论：

- 审查所需关键产物均存在。
- 验证期 NAV 覆盖完整。
- 信号和 fwd_ret 到 2022-11-30 属于边界口径问题：验证期最后一个月末调仓的执行日落在验证期之后，因此这些产物自然不覆盖 2022-12-30 标签。

## 4. 阶段 1：独立指标复算

执行脚本：`check/0526/run_stage0_stage1.py`

复算对象和结论：

| 对象 | IR | 年化超额 | 月胜率 | 正超额月份 | 月度净超额和 |
|---|---:|---:|---:|---:|---:|
| Pipeline B expanding, lambda=0.005 | 0.483493 | +2.77% | 39.13% | 9/23 | +3.59% |
| rolling-48m | 1.091733 | +6.16% | 60.87% | 14/23 | +7.83% |
| Pipeline A strategy_v1 | -0.110137 | -0.78% | 52.17% | 12/23 | -2.39% |
| Pipeline A strategy_v2 | 0.341396 | +2.03% | 56.52% | 13/23 | +2.11% |

关键核验：

- `information_ratio`、`monthly_win_rate`、`excess_return` 与已存 metrics 文件差异均未超过 `1e-8`。
- Pipeline B 的 39.13% 月胜率可以从 NAV 独立复算出来，不是 metrics 读取或计算 bug。
- rolling-48m 的 IR 1.091733 也可以从现有 NAV 独立复算出来。

阶段 1 结论：

- 异常不是简单的指标文件错误。
- 下一步必须看回测入口、权重产物和数据对齐，而不能直接解释为 rolling 方法真实优越。

## 5. 阶段 2：回测入口和产物等价性审查

执行脚本：`check/0526/run_stage2_stage3.py`

### 5.1 同一旧权重 + 当前回测引擎

结果：

| 对象 | strategy NAV 最大差异 | benchmark 最大差异 | excess NAV 最大差异 |
|---|---:|---:|---:|
| Pipeline B 旧权重重跑 | 8.88e-16 | 0 | 8.88e-16 |
| rolling-48m 旧权重重跑 | 1.11e-15 | 0 | 1.33e-15 |

结论：

- 同一旧权重用当前 `src.backtest.engine.run_backtest` 重跑，Pipeline B 和 rolling-48m 的 NAV/metrics 均可复现到浮点误差级别。
- 回测入口本身可复现，不支持“旧 NAV 与当前回测引擎不一致”的假设。

### 5.2 当前优化器重建 expanding

重建对象：

- 使用现有 expanding Ridge 信号。
- 使用当前 `experiments.turnover_lambda_grid.run_turnover_lambda_grid` 中的优化逻辑。
- lambda 固定为 `0.005`。
- 写入 `check/0526/tmp/stage2/expanding_rebuilt_current_code/`。

最终保留结果：

| 对象 | 旧值 | 当前重建值 | 差异 |
|---|---:|---:|---:|
| 年化超额 | 0.027711 | 0.029920 | +0.002208 |
| IR | 0.483493 | 0.502677 | +0.019184 |
| 月胜率 | 0.391304 | 0.478261 | +0.086957 |
| tracking error | 0.057315 | 0.059521 | +0.002206 |

权重和交易日志差异：

- 目标权重最大差异：0.028572。
- 权重差异单元格数：8769。
- 有权重差异的日期数：106。
- fallback 分布不变：L0 144 期，L1 0 期，L2 0 期。
- 交易日志与旧产物出现明显差异。

阶段 2 结论：

- 同一旧权重回测可复现，说明回测入口不是问题。
- 但当前优化器重建 expanding 与旧 Pipeline B 产物存在显著差异。
- 因此 rolling vs expanding 的机制解释必须先排查：
  - 优化器输入是否完全一致。
  - 协方差缓存、行业映射、状态约束、benchmark weight 是否存在版本差异。
  - cvxpy/求解器是否存在数值稳定性或 warm-start 相关的不确定性。
  - 旧 Pipeline B 产物是否被冻结在不同代码口径下。

补充说明：

- 在阶段 2 执行过程中，曾观察到一次 expanding 重建几乎复现旧产物，随后完整重跑出现显著差异。最终报告以最后一次完整运行产生的 CSV 和 markdown 为准。
- 这个现象本身提示“优化器重建稳定性”需要单独作为后续审查对象。

## 6. 阶段 3：数据口径和时间对齐审查

执行脚本：`check/0526/run_stage2_stage3.py`

### 6.1 基准口径

结果：

- `index_quote.parquet` 存在 `nav` 列。
- Pipeline B NAV 文件中的 benchmark 与 `index_quote.nav` 归一化结果完全一致。
- rolling-48m NAV 文件中的 benchmark 与 `index_quote.nav` 归一化结果完全一致。
- 代码扫描未发现绕过 `run_backtest` 并用 `close` 作为验证期 benchmark 的候选。

结论：

- 当前证据未发现价格指数替代全收益基准的问题。
- 阶段 3 范围内，基准口径可以暂时排除为主要原因。

### 6.2 forward return 标签

结果：

- `fwd_ret_panel` shape：131 x 1329。
- 日期范围：2012-01-31 到 2022-11-30。
- `fwd_ret_panel.index` 是 `index_member` 调仓日子集。
- 抽样 30 行，用 `open_adj[T'+1] / open_adj[T+1] - 1` 手工复算，最大差异为 0。

发现：

- 存在 1 个 `fwd_ret` 标签的 `exit_date` 超过 `VALID_END`。

结论：

- 抽样证据支持 forward return 是 T+1 开盘到 T'+1 开盘口径。
- 边界上有 1 个标签跨过验证期结束日。这不是正式测试集回测，但会影响验证期 IC 和模型训练标签边界解释。
- 后续阶段 5 必须检查 Ridge 训练窗口是否按 `exit_date` purge，而不是只按 label index `T` 切分。

### 6.3 PIT 与因子面板

PIT 抽样：

- 抽查日期：2021-01-29、2021-12-31、2022-06-30、2022-12-30。
- 抽查表：`financial_pit`、`indicator_pit`。
- 结果：future pit rows = 0，future end rows = 0。

因子面板：

- `data/processed/factor_panels/` 下 58 个 parquet。
- 日期范围均为 2012-01-31 到 2022-12-30。
- 测试期日期行数为 0。

源码扫描：

- `src/data/loader.py` 命中 `pit_date <= rebalance_date` 和 `end_date < rebalance_date`。
- `src/factors/preprocess.py` 命中 T 日横截面 MAD 去极值、自由流通市值中性化说明。
- `scripts/csv_to_parquet.py` 命中 `f_ann_date` 优先的 PIT 日期构建和三表最大 PIT 日期逻辑。

阶段 3 结论：

- PIT 抽样和因子面板日期覆盖未发现明显未来数据。
- 当前阶段未发现基准、forward return 抽样或 PIT 抽样层面的硬性领域错误。
- 不能据此证明 rolling 优势真实，只能说明阶段 3 覆盖的几个高危口径暂未发现违规。

## 7. 当前最重要的发现

1. Pipeline B 的低月胜率是真实进入 NAV 的结果，不是 metrics bug。
2. rolling-48m 的高 IR 也能从现有 NAV 独立复算，不是 metrics bug。
3. 同一旧权重使用当前回测引擎重跑完全可复现，因此回测入口本身不是当前首要问题。
4. 当前优化器重建 expanding 与旧 Pipeline B 产物出现显著差异，这是当前最高优先级风险。
5. 基准全收益口径在阶段 3 审计中通过。
6. forward return 抽样复算通过，但发现 1 个验证边界标签跨过 `VALID_END`。
7. PIT 抽样和因子面板测试期隔离暂未发现明显未来函数。

## 8. 尚不能下的结论

当前不能直接断言：

- rolling-48m 的 IR 1.092 是真实稳健提升。
- Pipeline B 的 39.1% 月胜率已经有完整原因解释。
- rolling 与 expanding 的差异来自信号窗口机制。

原因：

- 阶段 2 已显示当前优化器重建 expanding 与旧产物差异显著。
- 如果 expanding 基准本身在重建时不稳定，直接解释 rolling 相对 expanding 的收益差异会混入产物口径和求解器稳定性问题。

## 9. 推荐下一步

优先进入阶段 4/5 前，建议先补一个“阶段 2b：优化器重建稳定性审查”：

1. 固定同一输入，连续重建 expanding 权重 3 次，比较权重矩阵、solver_status、solve_time、NAV 和 metrics。
2. 将每期优化器输入哈希化：alpha、benchmark weight、cov、industry、halt/limit 状态、w_prev。
3. 定位第一期出现权重分叉的 rebalance_date。
4. 检查 cvxpy 求解器、warm_start、fallback 后处理和 ties/排序是否导致非确定性。
5. 确认旧 Pipeline B 产物对应的代码 commit、依赖版本和数据缓存 mtime。

之后再继续：

- 阶段 4：信号面板覆盖、横截面分布和 IC 明细。
- 阶段 5：Ridge 训练窗口、CV 选择、purge 和未来函数审查。
- 阶段 6：优化器约束、行业/单股偏离、换手和交易状态归因。

## 10. 自检

- A. 高频犯错点：未运行正式测试集；基准检查使用全收益 `nav`；fwd_ret 抽样按 T+1 open 到 T'+1 open；PIT 抽样检查 `pit_date <= T` 和 `end_date < T`；发现 1 个 validation 边界标签跨过 `VALID_END`，已记录为后续风险。
- B. 工程质量：新增脚本集中在 `check/0526/`；临时重跑产物只写 `check/0526/tmp/`；未覆盖正式实验产物；脚本通过 `py_compile`。
- C. 统计严谨性：当前仅完成复算和口径审查，未对 rolling 优势做显著性结论；未声称策略可用或可投资。
- D. 待优化事项：最高优先级是解释当前优化器重建 expanding 与旧产物不一致的问题。

## 11. 后续补充：阶段 2b/4 后发现产物漂移

阶段 2b/4 执行后新增：

- `check/0526/run_stage2b_stage4.py`
- `check/0526/stage2b_optimizer_stability.md`
- `check/0526/stage4_signal_audit.md`
- `check/0526/stage2b_stage4_log.md`
- `check/0526/artifact_drift_after_stage2b.csv`
- `check/0526/artifact_drift_after_stage2b.md`

重要发现：

- 对比 `artifact_manifest.csv` 的阶段 0 冻结记录，当前文件系统中多个正式产物已发生漂移。
- 变化对象包括 `composite_signal_ic_ir.parquet`、`ridge_composite_panel.parquet`、`ridge_coef_history.parquet`、`turnover_lambda_grid/results/lam_0050` 下的 NAV/metrics/weights，以及 Pipeline A 的 NAV/metrics。
- 当前 `lam_0050/backtest_metrics_valid.parquet` 中 Pipeline B IR 已为约 0.502677，而阶段 0/1 复算旧产物时为 0.483493。

解释：

- 阶段 2b 三次当前重建之间完全稳定，并且与当前文件系统中的 `lam_0050` 权重一致。
- 这只能说明“当前代码 + 当前产物”下优化器是稳定的。
- 不能用阶段 2b 当前结果直接解释阶段 0/1 中旧 Pipeline B 产物的 IR=0.483493 和月胜率 39.13%。
- 后续若要继续严格 root cause，需要先决定审查基准：恢复阶段 0 冻结旧产物，或接受当前刷新后产物作为新的基准并重跑阶段 0/1。

## 12. 后续补充：阶段 5/6 定向审查两个 problems

阶段 5/6 执行后新增：

- `check/0526/run_stage5_stage6.py`
- `check/0526/stage5_stage6_problem_audit.md`
- `check/0526/stage5_stage6_log.md`
- `check/0526/stage5_prediction_window_audit.csv`
- `check/0526/stage5_cv_window_audit.csv`
- `check/0526/stage5_shift_test_detail.csv`
- `check/0526/stage5_shift_test_summary.csv`
- `check/0526/stage6_config_consistency.csv`
- `check/0526/stage6_weight_exposure_by_period.csv`
- `check/0526/stage6_weight_exposure_summary.csv`
- `check/0526/stage6_trade_execution_detail.csv`
- `check/0526/stage6_trade_execution_summary.csv`
- `check/0526/stage6_monthly_problem_decomposition.csv`
- `check/0526/stage6_payoff_summary.csv`

### Problem A：Pipeline B 月胜率 39.1%

审查对象：

- 使用 `check/0526/tmp/stage2/pipeline_b_lam0050_replayed/` 中阶段 2 保存的冻结 replay 产物。
- 不使用当前已漂移的 `experiments/turnover_lambda_grid/results/lam_0050`。

结论：

- Pipeline B 月胜率仍为 39.13%，正超额月份 9/23。
- 正超额月份平均值约 +1.75%，负超额月份平均值约 -0.87%。
- payoff ratio 约 2.01。
- 因此低胜率不是指标 bug，也不必然代表负收益；它是“赢得少但单月赢幅较大”的右尾收益结构。
- 但这还不能证明稳健，需要阶段 7/8 做月份、行业、个股贡献和显著性检验。

### Problem B：rolling-48m 弱 IC 但组合 IR 高

阶段 5 未来函数/错位审查：

- 预测窗口违规行数：0 / 210。
- CV 窗口违规行数：0 / 10。
- rolling-48m 当前信号验证期 original IC_mean ≈ 0.0325，IC_IR ≈ 0.441。
- rolling-48m `signal_lag_1` 验证期 IC_IR 降至约 0.124。
- rolling-48m `signal_lead_1` 验证期 IC_mean 为负，IC_IR 约 -0.184。
- label shuffle 验证期 IC 接近 0。

阶段 6 优化器/交易执行审查：

- Pipeline B frozen replay 与 rolling-48m replay 均满足 T+1 执行。
- 2021-2022 验证期印花税均为 10 bps。
- 年化双边换手：
  - Pipeline B frozen replay：约 1003.5%
  - rolling-48m replay：约 1009.2%
- active share、行业偏离、单股偏离和持仓数量整体相近，未发现 rolling 因约束明显更宽松而获利的证据。
- rolling 相对 Pipeline B 贡献最大的月份包括 2021-04、2022-03、2021-06、2021-08、2022-08。

结论：

- 当前信号版本没有显示 rolling-48m 存在明显未来函数迹象。
- rolling-48m 的组合层优势不来自验证期 IC 更强；更可能来自优化后持仓路径、月份收益分布和少数月份贡献。
- 这仍不是“rolling 稳健优越”的最终结论，下一步应进入阶段 7/8 做收益归因和统计显著性审查。

## 阶段 7/8：收益归因与显著性审查

- 生成时间：2026-05-27 09:38:49
- 运行脚本：`check/0526/run_stage7_stage8.py`。
- 关键报告：`stage7_stage8_problem_audit.md`、`root_cause_report.md`。
- 结论摘要：Pipeline B 39.1% 月胜率真实，直接原因是正月少但正月幅度约为负月 2 倍；rolling-48m 组合 IR 点估计真实，但优势集中且统计显著性不足，不能直接定为稳定改进。
- 归因限制：行业 `total_effect` 与组合超额收益闭合；Brinson 三分项存在少量现金/锁定权重残差，已在报告中标注。
- 正式测试集：未运行。
