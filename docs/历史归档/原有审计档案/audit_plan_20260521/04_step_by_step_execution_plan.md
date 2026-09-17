# 可照做的逐阶段审查执行计划

生成日期：2026-05-21

本计划用于正式测试集 Run #2 之前的全链路审查。目标是最大化发现问题的概率，并保证所有已定义的关键风险都被逐项审查、记录和裁决。不能承诺发现未知世界里的所有问题，但可以做到：每个高风险环节都有证据、有复算、有停止条件、有整改闭环。

## 总规则

1. 不运行正式测试集，不读取 2023-2025 测试结果。
2. 每发现一个问题，先写入问题登记表，再决定修复、降级披露或关闭。
3. 每阶段必须产出一份审查记录，不能只凭“看起来没问题”进入下一阶段。
4. 任一 P0 问题出现，立即停止效果优化，只修可信度问题。
5. 任一 P1 问题未修复时，必须在 Run #2 前写明是否降级接受，以及接受理由。
6. 所有策略改进必须先预登记实验设计，不能看结果后改规则。

建议问题登记文件：

- `audit_plan_20260521/issues_register.md`

建议阶段记录文件：

- `audit_plan_20260521/stage_00_baseline_review.md`
- `audit_plan_20260521/stage_01_data_pit_review.md`
- 后续按阶段编号新增。

## 阶段 0：冻结审查基线

目标：确定之后所有核查都针对同一版源码、配置和产物。

照做步骤：

1. 记录当前 git commit、工作区状态、关键产物修改时间。
2. 记录最新完整流水线 manifest：`data/processed/run_manifests/run_20260520_212950.json`。
3. 记录 `src/config.py` 的关键参数：训练/验证/测试日期、成本、行业口径、优化参数。
4. 记录关键报告和 parquet 的路径清单。
5. 确认没有 `data/processed/test_run_*` 或 `factor_panels_test_run_*` 被公共训练/验证流程误读。

必须检查的文件：

- `PROJECT_PLAN_v1.1.md`
- `src/config.py`
- `data/processed/run_manifests/run_20260520_212950.json`
- `check/test_set_run_log.md`
- `reports/analysis_v2_results.md`

产出：

- `stage_00_baseline_review.md`
- 一张“审查基线表”：commit、config hash、manifest、关键输入输出 hash。

停止条件：

- 发现公共产物混入测试期数据。
- manifest、config、报告口径互相冲突且无法解释。
- 当前报告数字无法追溯到 parquet。

通过标准：

- 可以明确回答“现在审查的是哪一次运行、哪一版配置、哪些产物”。

## 阶段 1：测试集纪律与隔离审查

目标：保证后续所有改进不会污染唯一剩余的正式测试集 Run #2。

照做步骤：

1. 读取测试集运行记录，确认 Run #1 已消耗，Run #2 是唯一剩余机会。
2. 检查所有脚本是否把测试集产物写入独立目录。
3. 检查公共训练/验证目录中是否存在 2023-2025 日期范围的信号、权重、回测、归因产物。
4. 检查 `scripts/run_test_pipeline.py` 是否有 run-id、防覆盖、产物隔离逻辑。
5. 运行测试集隔离相关单元测试，但不要运行真实测试集 pipeline。

必须检查的文件：

- `scripts/run_test_pipeline.py`
- `tests/test_test_pipeline_isolation.py`
- `check/test_set_run_log.md`
- `data/processed/`

产出：

- `stage_01_test_set_isolation_review.md`
- 测试集 Go/No-Go 初始结论。

停止条件：

- 任何训练/验证产物日期超过 2022-12-31。
- 测试集脚本会写公共信号、权重、协方差、回测或归因路径。
- run-id 可重复覆盖且无记录。

通过标准：

- 可以确认审查和改进只使用 2016-2022。

## 阶段 2：数据口径与 PIT 审查

目标：先确认数据可信，再讨论因子和收益。

照做步骤：

1. 抽查 `daily_quote.parquet`：确认股票收益是后复权口径。
2. 抽查 `index_quote.parquet`：确认基准是中证500全收益指数。
3. 抽查 `financial_pit.parquet`、`indicator_pit.parquet`：每个调仓日只取 `ann_date <= T` 的最新记录。
4. 抽查 `index_member.parquet`：成分股按生效日对齐。
5. 抽查 `stock_status.parquet`：ST、停牌、涨跌停、新股标记在调仓和回测中使用一致。
6. 抽查 `industry.parquet`：行业口径为 SW2021 一级。
7. 明确市值字段是否为自由流通市值；若不能确认，列为 P0/P1 并暂停中性化结论。

必须检查的文件：

- `src/data/*.py`
- `src/factors/preprocess.py`
- `data/processed/daily_quote.parquet`
- `data/processed/index_quote.parquet`
- `data/processed/index_member.parquet`
- `data/processed/financial_pit.parquet`
- `data/processed/indicator_pit.parquet`
- `data/processed/stock_status.parquet`

产出：

- `stage_02_data_pit_review.md`
- 5-10 只股票的 PIT 手工回放记录。
- 每月可投资域健康表：成分数、可投资数、停牌、ST、新股、财务覆盖率。

停止条件：

- 用了报告期 `end_date/Accper` 代替公告日。
- 基准不是全收益指数。
- 成分股不是按生效日对齐。
- 中性化市值字段含义无法确认。

通过标准：

- 数据层无 P0 问题，P1 都有记录和处置。

## 阶段 3：因子面板构建审查

目标：确认每个候选因子在 T 日只使用 T 日及以前信息，横截面处理没有样本泄漏。

照做步骤：

1. 对 27 个因子逐一建立 factor card。
2. 每个 factor card 记录：数据依赖、时间窗口、是否财务因子、金融股处理、缺失率、极值处理、中性化样本数。
3. 检查去极值边界是否只来自 T 日横截面。
4. 检查中性化是否只在 T 日横截面回归。
5. 检查 `factor_panel_diagnostics.parquet` 是否有断崖式覆盖率变化。
6. 对覆盖率低、极值比例高、金融股过滤异常的因子登记问题。

必须检查的文件：

- `src/factors/financial_factors.py`
- `src/factors/price_factors.py`
- `src/factors/preprocess.py`
- `scripts/build_factor_panels.py`
- `data/processed/factor_panels/*.parquet`
- `data/processed/factor_panel_diagnostics.parquet`

产出：

- `stage_03_factor_panel_review.md`
- `factor_cards/` 或一张因子健康总表。

停止条件：

- 任何入模因子发现 `shift(-n)` 或未来收益进入信号。
- 去极值、中性化、标准化使用全样本统计量。
- 财务因子未按 PIT 对齐。

通过标准：

- 每个入模因子都有清楚的数据依赖和时间边界说明。

## 阶段 4：单因子评价审查

目标：确认单因子“有效”不是错位、样本选择或过拟合造成。

照做步骤：

1. 对 `factor_summary.csv` 中每个因子检查 IC_IR、IC mean、BH p 值、分组 Sharpe。
2. 对 `shift_result.csv` 检查每个 `shift_warning=True` 的原因。
3. 对 `valid_comparison.csv` 检查训练强、验证弱或方向翻转的因子。
4. 将训练期拆成 2016-2018 和 2019-2021，复核训练内部稳定性。
5. 明确分组回测口径：原始个股等权、不含成本、不扣基准，不等同可交易超额。
6. 形成“可入模、观察、剔除、需修复”四类清单。

必须检查的文件：

- `reports/factor_evaluation/factor_summary.csv`
- `reports/factor_evaluation/ic_result.csv`
- `reports/factor_evaluation/quintile_summary.csv`
- `reports/factor_evaluation/shift_result.csv`
- `reports/factor_evaluation/seg_ic_ir.csv`
- `reports/factor_evaluation/valid_comparison.csv`

产出：

- `stage_04_single_factor_review.md`
- 因子入模复核表。

停止条件：

- IC 标签和因子日期错位。
- shift test 出现未来函数疑点且无法解释。
- 入模规则依赖 2022 验证期表现手工挑选。

通过标准：

- 最终因子集合能由训练期规则复现，验证期只作为一次稳健性复核。

## 阶段 5：因子合成与信号审查

目标：确认合成信号没有引入验证集或测试集信息，并判断 IC_IR 加权是否真的优于等权。

照做步骤：

1. 对齐 `final_factors.json` 和 `composite_signal_metadata.json`，确认因子集合一致。
2. 检查 `icir_weight_history.parquet` 每期权重只使用 T 之前 IC。
3. 对比等权信号和 IC_IR 信号的训练期、验证期表现。
4. 检查冷启动期处理，确认 13 期等权 fallback 被记录。
5. 检查合成信号横截面均值、标准差、缺失率、行业暴露、市值暴露。
6. 若提出替代合成方法，先写实验预登记，不直接改参数。

必须检查的文件：

- `reports/factor_evaluation/final_factors.json`
- `data/processed/ic_series_all_factors.parquet`
- `data/processed/icir_weight_history.parquet`
- `data/processed/composite_signal_ic_ir.parquet`
- `data/processed/composite_signal_equal.parquet`
- `data/processed/composite_signal_metadata.json`

产出：

- `stage_05_signal_combination_review.md`
- 等权 vs IC_IR 对照表。
- 信号暴露诊断表。

停止条件：

- IC_IR 权重使用包含 T 或 T 之后的信息。
- 合成信号 metadata 与实际产物不一致。
- 新合成方案未经预登记就用验证期挑选。

通过标准：

- 合成方法选择有训练内证据，且不依赖测试期。

## 阶段 6：协方差与组合优化审查

目标：解释 V2 弱于 V1 的来源，并确认优化权重可交易、可解释、可复现。

照做步骤：

1. 检查每期协方差样本窗口是否严格早于 T。
2. 检查 LedoitWolf、正定修复、缺失协方差处理是否有 meta 记录。
3. 列出 L1/L2/L3 fallback 日期、原因、求解器状态、耗时。
4. 对每期权重复算：权重和、单股偏离、行业偏离、TE 预测值、alpha 暴露。
5. 单独分析 15 期 `constraint_compliant=False` 的原因和收益影响。
6. 对比 V1/V2：alpha 暴露、行业偏离、换手、成本、月度收益差。
7. 判断 V2 不佳是信号问题、约束问题、fallback 问题、成本问题还是市场风格问题。

必须检查的文件：

- `src/portfolio/covariance.py`
- `src/portfolio/optimizer.py`
- `data/processed/cov_cache/*.meta.json`
- `data/processed/portfolio_weights_baseline.parquet`
- `data/processed/portfolio_weights_optimized.parquet`
- `data/processed/portfolio_weights_meta.parquet`

产出：

- `stage_06_portfolio_optimization_review.md`
- fallback 明细表。
- V1/V2 优化影响拆解表。

停止条件：

- 非合规权重被当作合规权重进入报告。
- 协方差使用未来收益。
- 停牌、涨跌停、单股偏离、行业偏离约束无法复算。

通过标准：

- 每期权重状态都能解释；非合规期已修复或明确披露。

## 阶段 7：回测与交易成本审查

目标：确认回测收益是可交易口径，成本、换手、NAV 能对账。

照做步骤：

1. 检查调仓时点：T 日盘后生成权重，T+1 开盘成交。
2. 检查成本函数：佣金双边 2.5 bps、滑点 5-10 bps、印花税日期切换。
3. 检查停牌、涨停、跌停、一字板处理。
4. 用交易日志复算月度成本、换手和现金权重。
5. 用 NAV 复算 `backtest_metrics.parquet` 中全部指标。
6. 对 V2 每个月相对 V1 的收益差做归因前拆解。

必须检查的文件：

- `src/backtest/engine.py`
- `src/backtest/transaction.py`
- `src/backtest/metrics.py`
- `data/processed/backtest_nav.parquet`
- `data/processed/backtest_metrics.parquet`
- `data/processed/backtest_trades_v1.parquet`
- `data/processed/backtest_trades_v2.parquet`
- `data/processed/backtest_weights_v1.parquet`
- `data/processed/backtest_weights_v2.parquet`

产出：

- `stage_07_backtest_review.md`
- NAV/成本/换手对账表。
- V2 vs V1 月度差异表。

停止条件：

- 不是 T+1 开盘成交。
- 成本函数与配置不一致。
- 指标无法由 NAV 复算。
- 停牌/涨跌停状态未生效。

通过标准：

- 回测结果可由权重、行情、交易日志逐项复算。

## 阶段 8：归因与报告审查

目标：确认报告能解释结果不好在哪里，并诚实披露限制。

照做步骤：

1. 检查 Brinson 行业归因是否与 T+1 回测口径一致。
2. 检查因子归因使用的因子集合和方向来源。
3. 检查 reconciliation：回测 NAV 月收益 vs Brinson/factor attribution 月收益。
4. 修复或解释 metadata 中的非标准 `NaN` 和异常 hash。
5. 检查报告所有数字是否来自 parquet，不含旧硬编码结果。
6. 检查报告是否明确写出：V2 未达 IR、L3 非合规、归因偏差、Run #2 剩余一次。

必须检查的文件：

- `src/attribution/brinson.py`
- `src/attribution/factor_attr.py`
- `scripts/run_attribution.py`
- `scripts/generate_backtest_report.py`
- `data/processed/brinson_attribution.parquet`
- `data/processed/factor_attribution.parquet`
- `data/processed/attribution_nav_reconciliation.parquet`
- `data/processed/attribution_metadata.json`
- `reports/analysis_v2_results.md`

产出：

- `stage_08_attribution_report_review.md`
- 报告数字对账表。
- 已知限制披露清单。

停止条件：

- 报告宣称超出证据的结论。
- attribution metadata 不是严格可解析 JSON。
- 归因结果与 NAV 偏差超过阈值且无解释。

通过标准：

- 报告可发布为“训练/验证阶段审查报告”，但不冒充测试集结论。

## 阶段 9：整改闭环

目标：把前 8 个阶段发现的问题逐一关闭，而不是只列清单。

照做步骤：

1. 汇总所有问题到 `issues_register.md`。
2. 按 P0/P1/P2/P3 排序。
3. P0 必须修复并重新跑相关最小流水线。
4. P1 要么修复，要么写明降级接受理由和报告披露方式。
5. 每个修复都补对应测试或复算证据。
6. 修复后重跑受影响阶段，而不是只看局部文件。

产出：

- `stage_09_remediation_closure.md`
- 关闭版问题登记表。
- 重新生成的关键产物 hash 表。

停止条件：

- P0 未关闭。
- P1 无处置结论。
- 修复后没有测试或复算证据。

通过标准：

- 所有问题都有状态：Closed、Accepted with Disclosure、Deferred、Rejected。

## 阶段 10：非测试集稳健性复核

目标：在不碰测试集的情况下，判断改进是否真的稳健。

照做步骤：

1. 使用训练期内部切分做稳健性复核，例如 2016-2018 / 2019-2021。
2. 对改进前后比较：IC_IR、分组、合成信号、V1/V2 回测、TE、超额回撤、换手。
3. 只使用预登记过的实验。
4. 不因为 2022 单年结果好看就采纳。
5. 对每个候选改进给出采纳/拒绝理由。

产出：

- `stage_10_validation_robustness_review.md`
- 改进实验结果表。
- 最终冻结配置候选。

停止条件：

- 改进方案只改善 2022，但训练内部不稳。
- 改进方案参数是看验证结果后追加的。

通过标准：

- 最终方案有训练内稳健性证据，验证期只是复核。

## 阶段 11：Run #2 前预登记与 Go/No-Go

目标：在正式测试集唯一剩余机会前冻结方案。

照做步骤：

1. 写 Run #2 预登记文档。
2. 记录 git commit、config hash、最终因子、合成方法、优化参数、成本参数、fallback 规则。
3. 记录所有 Accepted with Disclosure 的问题。
4. 记录预期风险：验证期 V2 未达标、2024 小微盘冲击、L3 fallback 历史限制。
5. 跑全量 pytest 和关键复算脚本。
6. 做 Go/No-Go 会议式结论：Go、No-Go、Go with Disclosure。

产出：

- `stage_11_pre_run2_go_no_go.md`
- Run #2 预登记文件。
- 全量测试记录。

No-Go 条件：

- 任一 P0 未关闭。
- 测试集隔离仍有疑点。
- 最终配置未冻结。
- 关键报告数字无法复算。
- 用户未明确授权正式 Run #2。

Go 条件：

- P0 全部关闭。
- P1 修复或降级披露。
- 全量测试通过。
- Run #2 预登记完成。
- 用户明确授权。

## 阶段 12：正式 Run #2 后审查

目标：正式测试集只运行一次，并对结果做冷静审查。

照做步骤：

1. 确认 commit message 含 `[TEST_SET_RUN_2]`。
2. 确认所有测试集产物写入独立目录。
3. 运行后先做未来函数和产物隔离核查，不先解释收益。
4. 若测试集结果异常好，优先排查未来函数、基准错配、成本漏计、测试期污染。
5. 若测试集结果差，按归因框架解释，不回改参数重跑。
6. 生成最终报告，明确训练、验证、测试三段边界。

产出：

- `stage_12_test_run2_post_review.md`
- 测试集产物隔离证明。
- 最终测试集报告。

停止条件：

- 测试集运行后发现产物污染公共训练/验证目录。
- 结果异常好但未来函数检查未完成。
- 有人试图看结果后改参数再重跑。

通过标准：

- 测试集结果可复现、可解释、可披露，且没有二次污染。

## 最小每日执行节奏

每天开始：

1. 看 `issues_register.md`，只处理当前最高优先级问题。
2. 确认不触碰测试集。
3. 确认当天要产出的审查记录文件。

每天结束：

1. 更新问题状态。
2. 写明已查证据路径。
3. 写明未解决阻断项。
4. 若有代码或产物变化，记录影响范围和需要重跑的阶段。

## 最终保证口径

完成本计划后，可以保证：

- 已定义的高风险点全部被逐项审查。
- 每个发现的问题都有证据、优先级、处置状态。
- 所有接受的剩余风险都被报告披露。
- 正式 Run #2 前的方案、配置和风险已冻结。

不能保证：

- 策略一定达标。
- 所有未知问题都必然被发现。
- 测试集表现会优于验证期。

