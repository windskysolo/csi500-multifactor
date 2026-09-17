# 阶段 C/D 修复完成度复核

> 生成日期：2026-05-20  
> 对应路线图：`check/19_final_remediation_plan.md` 的阶段 C、阶段 D。  
> 复核范围：当前源码、当前训练/验证期公共产物、C/D 相关单元测试。  
> 测试集纪律：本次未运行 `scripts/run_test_pipeline.py`，未消耗 2023-2025 测试集次数。

## 总体结论

| 阶段 | 结论 | 判断 |
|---|---|---|
| 阶段 C：因子面板与单因子评价重建 | 已闭环 | 失效标记已解除，诊断产物、`final_factors.json` metadata、forward return sidecar、shift test 新 schema 和报告口径均已落盘并通过测试。 |
| 阶段 D：信号合成与组合优化闭环 | 部分闭环 | 信号 provenance、权重历史、协方差 sidecar、公共组合 meta 均已重建；但 30 个 L3 日期仍因单股偏离超过 `OPT_SINGLE_MAX_DEV=1%` 被标记为 `constraint_compliant=False`，因此当前公共权重不能整体视为“满足交易约束的可交付权重”。 |

阶段 D 的核心变化是：旧问题中“违规但看起来成功”的状态已修正为“违规被明确标记”。这比旧产物安全，但还不等于满足阶段 D 的退出标准。进入阶段 E 前，回测脚本或报告门禁必须拒绝或明确隔离 `constraint_compliant=False` 的权重，或者重新设计 L3 fallback 使其满足单股偏离约束。

## 阶段 C 复核

| 检查项 | 当前结果 | 证据 |
|---|---|---|
| `INVALIDATED.md` | 已删除 | `reports/factor_evaluation/INVALIDATED.md` 不存在。 |
| 因子面板诊断产物 | 通过 | `data/processed/factor_panel_diagnostics.parquet` 为 2268 行，索引覆盖 84 个调仓日 × 27 个因子，字段包含 `n_raw`、`n_fin_filtered`、`n_winsor_clipped`、`n_neutralize_valid`、`skipped_neutralize`、`n_final`、`error_msg`。 |
| `final_factors.json` metadata | 通过 | JSON 含 `_metadata`；字段含 `run_id`、`generated_at`、`git_commit`、`factor_summary_md5`、`n_final`、`n_excluded`。当前最终因子数 6，集合与 `factor_summary.csv.final_include` 一致。 |
| forward return sidecar | 基本通过 | `data/processed/fwd_ret_panel.meta.json` 存在，记录 84 个调仓日；`fwd_ret_panel.parquet` 为 83 行，最后一期无 forward return 属正常边界。小建议：`benchmark_mode` 当前为 `null`，因单因子评价使用原始开盘到开盘收益，建议后续改成显式字符串如 `raw_open_to_open_no_benchmark`，避免审计时误解。 |
| shift test 产物 | 通过 | `shift_result.csv` 含 `lag2_ic_ir`、`lead1_ic_ir`、`diagnosis`、`lead_reverse`；当前有 14 个 `lead_reverse=True`，报告已按异常信号披露，未再宣称“无未来函数疑点”。 |
| 分组回测口径说明 | 通过 | `factor_evaluation_report.md` 第 4 节明确：原始个股等权收益、不含成本、不扣基准、不代表相对中证500全收益指数的可交易超额。 |

阶段 C 判断：可以标记为已闭环。`lead_reverse=True` 不是阶段 C 未完成的证据，而是当前报告正确暴露了诊断风险；真正需要保证的是这些异常不会被包装成“无未来函数风险”。

## 阶段 D 复核

| 检查项 | 当前结果 | 证据 |
|---|---|---|
| 合成信号 provenance | 通过 | `data/processed/composite_signal_metadata.json` 存在，含 `factor_list`、`ic_series_hash`、`signal_ic_ir_hash`、`git_commit`、`cold_start_count`；因子集合与 `final_factors.json` 一致。 |
| IC_IR 权重历史 | 通过 | `data/processed/icir_weight_history.parquet` 为 84 × 6，覆盖 2016-01-29 至 2022-12-30。 |
| Notebook 03 自检 | 通过 | `notebooks/03_factor_combination.ipynb` 已改为 PASS/WARN/FAIL 三状态；当 IC_IR 加权弱于等权时不再无条件输出“可交付”。 |
| 公共组合 meta | 通过但有非合规权重 | `portfolio_weights_meta.parquet` 为 84 × 6，含 `cov_available`、`w_prev_source`、`constraint_compliant`。Fallback 分布：L1=52、L2=2、L3=30；`constraint_compliant=False` 为 30 期，全部来自 L3。 |
| 缺失协方差伪装 L1 | 已修复 | 3 个协方差缺失期均 `cov_available=False` 且 `fallback_level>=1`；不再显示为 L1 `optimal`。 |
| 协方差审计 metadata | 基本通过 | `data/processed/cov_cache/` 有 81 个 parquet 和 81 个 `.meta.json`，sidecar 含 `date`、`n_codes`、`codes_hash`、`min_eig_before`、`diag_delta`、`was_repaired`、`source_commit`、`generated_at`。 |
| L3 停牌/涨跌停约束 | 通过 | 对当前 30 个 L3 日期复核，停牌锁定违约 0、涨停加仓违约 0、跌停减仓违约 0。 |
| L3 单股偏离约束 | 未闭环 | 30 个 L3 日期全部存在单股偏离超限，合计 1578 个股票-日期，最大单股偏离约 1.954%，高于 `OPT_SINGLE_MAX_DEV=1%`。当前 meta 已正确标为 `constraint_compliant=False`。 |
| `_topn_equal_weight()` 无候选极端边界 | 部分修复 | 极端样例仍可能返回不可交易的等权退化权重，但会返回 `constraint_compliant=False`。这已避免“假合规”，但下游必须拒绝把该权重当作合规目标。 |
| Notebook 04 生产口径 | 未完全同步 | `notebooks/04_portfolio_optimization.ipynb` 未检索到 `validate_and_repair_covariance`、`cov_available`、`w_prev_source`、`constraint_compliant` 等新字段。公共脚本产物已更新，但如果 notebook 仍作为交付物，需要同步。 |

阶段 D 判断：不能标记为完全完成。可标记为“审计闭环基本完成，组合权重合规性未闭环”。下一步应在阶段 E 前增加强门禁：

1. `run_backtest.py` 或 pipeline 在发现 `portfolio_weights_meta.constraint_compliant=False` 时默认失败，除非显式 `--allow-noncompliant-weights` 并在报告中披露。
2. 或重做 L3 fallback：在无法满足 `single_max_dev` 时使用更保守的上一期权重/基准权重/可行线性投影，而不是 TopN 等权。
3. 同步 `notebooks/04_portfolio_optimization.ipynb`，避免 notebook 继续展示旧协方差和旧 meta 口径。

## 测试结果

```powershell
python -m pytest tests\test_preprocess.py tests\test_factor_time_boundary.py tests\test_evaluation.py tests\test_signal_combiner.py tests\test_optimizer.py -q --basetemp=.codex_tmp_stage_cd
# 85 passed, 2 warnings

python -m pytest tests/ -q --basetemp=.codex_tmp_stage_cd_all
# 200 passed, 7 warnings
```

warning 说明：

- 1 个 `ConstantInputWarning` 来自异常 shift test 合成用例。
- 5 个 `FutureWarning` 来自回测状态布尔列 fillna 的 pandas 行为变化。
- 1 个 `PytestCacheWarning` 仍来自 `.codex_tmp_pytest_cache` 权限问题，属于阶段 A/F 的环境门禁残留，不是 C/D 业务逻辑失败。

## 未消耗测试集确认

- 未运行 `scripts/run_test_pipeline.py`。
- 未创建或覆盖 `data/processed/test_run_*`。
- 本次检查只覆盖训练/验证期公共产物。

## 自检

- 高频犯错点：未触碰测试集；单因子评价报告已避免“无未来函数”强结论；分组回测口径已说明不含成本、不扣基准；L3 停牌/涨跌停约束已复核。
- 工程质量：C/D 相关测试通过；公共产物已有 metadata；但 `notebooks/04_portfolio_optimization.ipynb` 未同步新优化口径。
- 统计严谨性：当前 `lead_reverse=True` 和 L3 非合规权重均作为风险暴露，不作为达标结论。
- 待优化事项：阶段 D 的 `constraint_compliant=False` 30 期是进入阶段 E 前的主要阻断项。
