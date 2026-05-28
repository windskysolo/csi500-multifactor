# 逐阶段核查与改进计划

## 审查目标

把当前项目当成将要对外发布的量化研究产品审查。审查顺序是：

1. 结论可信度：是否存在未来函数、PIT 错误、基准错配、成交假设错误、测试集污染。
2. 工程可复现：是否能从同一配置和 manifest 重建同一结果。
3. 模型表现：在不污染测试集的前提下，定位 V2 弱于 V1 的原因。
4. 报告可发布：是否诚实披露未达标、fallback、归因偏差和剩余风险。

## 阶段 0：冻结审查基线

目标：先固定当前可审查对象，避免后续重跑后无法解释“到底审的是哪一版”。

重点产物：

- `PROJECT_PLAN_v1.1.md`
- `src/config.py`
- `data/processed/run_manifests/run_20260520_212950.json`
- `reports/factor_evaluation/*`
- `data/processed/backtest_metrics.parquet`
- `data/processed/portfolio_weights_meta.parquet`
- `data/processed/attribution_metadata.json`
- `reports/analysis_v2_results.md`

核查项：

- manifest 的 `git_commit`、`config_hash`、输入输出 hash 是否完整。
- 所有训练/验证产物的日期范围是否截止 2022-12-31。
- 公共目录是否没有测试集扩展产物混入。
- markdown、JSON、CSV、Parquet 能否被标准工具读取；JSON 不应含非标准 `NaN`。
- 报告中所有数字是否从 parquet 自动生成，而不是历史硬编码。

退出标准：

- 形成审查基线清单，记录每个关键产物的路径、修改时间、hash、日期范围。
- 若发现报告/metadata 与 parquet 不一致，先修复产物追溯，不进入效果优化。

## 阶段 1：数据与 PIT 审查

目标：确认上游数据没有口径错配和未来信息泄漏。

重点产物：

- `data/processed/daily_quote.parquet`
- `data/processed/index_quote.parquet`
- `data/processed/index_member.parquet`
- `data/processed/financial_pit.parquet`
- `data/processed/indicator_pit.parquet`
- `data/processed/holder_pit.parquet`
- `data/processed/stock_status.parquet`
- `data/processed/industry.parquet`

核查项：

- 股票收益全程使用后复权价/后复权收益率。
- 基准为中证500全收益指数，而不是价格指数。
- 财务、指标、股东数据只使用 `ann_date <= T` 的最新记录。
- 成分股按生效日对齐；调仓日可投资域是当日真实中证500成员。
- 股票代码全程为 Tushare `ts_code`，无 6 位裸代码混入。
- ST、停牌、涨跌停、新股小于 180 天的标记和排除规则一致。
- 行业为 SW2021 一级；金融股财务因子处理符合 `config.py`。
- 中性化用的市值字段是否确认为自由流通市值；若字段含义不明，必须停止并确认。

改进方向：

- 对每个调仓日生成数据健康表：成分数、可投资数、停牌数、ST 数、新股数、财务 PIT 覆盖率。
- 对 5-10 只随机股票人工回放 PIT 财务记录，保留审查证据。
- 若自由流通市值字段不确定，优先修字段，不做因子优化。

退出标准：

- 数据层没有 P0/P1 口径问题。
- `tests/test_pit.py`、`tests/test_universe.py`、`tests/test_transaction.py` 通过。

## 阶段 2：因子构建审查

目标：确认 27 个候选因子在每个 T 日只使用横截面当期和历史信息。

重点产物：

- `data/processed/factor_panels/*.parquet`
- `data/processed/factor_panel_diagnostics.parquet`
- `src/factors/*.py`
- `src/factors/preprocess.py`

核查项：

- 每个因子面板索引为调仓日，列为 `ts_code`，日期范围为 2016-01-29 至 2022-12-30。
- 去极值边界只用 T 日横截面计算，不用全样本统计。
- 中性化回归为 `factor ~ industry_dummies + log(free_float_mcap)`，取残差。
- 金融股财务因子是否置空或单独处理；置空数量应能从 diagnostics 解释。
- `n_raw`、`n_final`、`n_winsor_clipped` 不应出现无法解释的断崖。
- 动量、波动、换手、资金流、两融因子是否严格用 T 日及以前数据。
- 所有未来收益标签变量必须以 `future_` 命名，且不进入策略信号。

改进方向：

- 为每个因子补一张 health card：覆盖率、极值比例、中性化有效样本数、金融股过滤数、缺失原因。
- 对覆盖率低或断点明显的因子先做数据质量修复，而不是直接纳入合成。
- 将 diagnostics 中的异常阈值变成发布门禁，例如单期 `n_final < 300` 或极值比例异常需人工复核。

退出标准：

- 每个入模因子都有可解释的数据依赖、时间边界和 health card。
- `tests/test_preprocess.py`、`tests/test_factor_time_boundary.py` 通过。

## 阶段 3：单因子检验审查

目标：确认因子有效性评估不是由错位、样本选择或统计口径造成。

重点产物：

- `reports/factor_evaluation/ic_result.csv`
- `reports/factor_evaluation/quintile_summary.csv`
- `reports/factor_evaluation/shift_result.csv`
- `reports/factor_evaluation/seg_ic_ir.csv`
- `reports/factor_evaluation/valid_comparison.csv`
- `reports/factor_evaluation/factor_correlation.csv`
- `reports/factor_evaluation/factor_summary.csv`

当前优先疑点：

- 多个训练期强因子在 2022 验证期方向翻转，例如 `q_roe`、`roa`、`roe`、`np_yoy`、`ep_ttm`、`gross_margin`。
- 当前入模 6 因子中，`ep_ttm` 和 `gross_margin` 在 `valid_comparison.csv` 中验证期 IC_IR 为负。
- 多个因子存在 `shift_warning=True`，需要明确 warning 含义和阻断级别。
- 分组回测是原始个股等权收益，不含成本、不扣基准，不能直接解释为可交易超额。

核查项：

- IC 标签是否是下期收益，且只作为 `future_` 标签进入评价。
- IC 窗口、分组回测、shift test 的调仓日期完全一致。
- IC_IR、t 统计、p 值、BH 校正是否完整且报告含义准确。
- 分段 IC 是否覆盖训练期内部多个市场环境，而不是只看 2016-2021 总体。
- 方向选择是否只基于训练期，不使用 2022 或测试期。
- 高相关因子剔除是否有确定规则，避免随结果手工挑选。

改进方向：

- 把训练期拆成 2016-2018、2019-2021，先看训练内部稳定性，再看 2022。
- 对方向翻转因子建立“降权/剔除候选”规则，但规则只能用训练期统计和预先定义的稳健性标准。
- 做成本前单因子 TopN/BottomN 与行业中性后分组对比，定位是选股信号弱还是组合层压制。

退出标准：

- 因子入模规则可复现，不依赖人工挑 2022 结果。
- 所有 `shift_warning` 都有解释：是阻断、警告还是预期现象。

## 阶段 4：因子合成与信号审查

目标：定位合成信号是否比单因子更稳定，以及 IC_IR 加权是否引入不必要噪声。

重点产物：

- `reports/factor_evaluation/final_factors.json`
- `data/processed/ic_series_all_factors.parquet`
- `data/processed/icir_weight_history.parquet`
- `data/processed/composite_signal_ic_ir.parquet`
- `data/processed/composite_signal_equal.parquet`
- `data/processed/composite_signal_metadata.json`

核查项：

- `final_factors.json` 的因子集合与合成信号 metadata 完全一致。
- `icir_weight_history.parquet` 每期权重只使用严格早于 T 的 IC 记录。
- 冷启动期共 13 期，等权 fallback 是否符合配置和报告披露。
- IC_IR 权重是否允许负权重；若允许，经济含义和方向处理是否一致。
- 等权合成与 IC_IR 合成在训练、验证、分段上的表现差异。
- 合成信号横截面均值、标准差、缺失率、行业暴露、市值暴露是否稳定。

改进方向：

- 若 IC_IR 加权在验证期不稳定，优先评估“等权/收缩 IC_IR/截断 IC_IR”三种简单方案。
- 对权重历史加稳定性约束，例如单因子权重上限、负权重处理、滚动窗口缩短/延长敏感性。
- 改进必须先在训练期滚动切分中通过，再作为验证期复核候选。

退出标准：

- 选定的合成方法有训练内稳健性证据。
- 不因 2022 单年表现反复调参。

## 阶段 5：协方差与组合优化审查

目标：解释为什么 V2 优化组合弱于 V1 等权，并确认优化层没有制造不可交易或不可解释风险。

重点产物：

- `data/processed/cov_cache/*.parquet`
- `data/processed/cov_cache/*.meta.json`
- `data/processed/portfolio_weights_baseline.parquet`
- `data/processed/portfolio_weights_optimized.parquet`
- `data/processed/portfolio_weights_meta.parquet`
- `src/portfolio/covariance.py`
- `src/portfolio/optimizer.py`

当前优先疑点：

- V2 2022 年 IR 为 -0.191，低于 V1 的 0.173。
- `portfolio_weights_meta.parquet` 显示 15 期 L3 fallback，且 15 期 `constraint_compliant=False`。
- `w_prev_source="target_weight"`，说明优化器使用目标权重近似上一期持仓，尚未形成真实执行权重反馈闭环。

核查项：

- LedoitWolf 协方差是否只使用 T 之前有效日收益，且样本数满足最低要求。
- 协方差正定性、对角修复、条件数、缺失期处理是否记录。
- L1/L2/L3 fallback 触发日期、原因、求解器状态、耗时是否可追踪。
- 单股偏离、行业偏离、跟踪误差、停牌锁定、涨跌停约束是否在权重产物中可验证。
- V2 相比 V1 的差异来自 alpha 暴露下降、风险约束、fallback、交易状态，还是成本。
- 真实执行后的实际持仓是否应反馈给下一期优化器；当前近似需要在报告中披露。

改进方向：

- 优先修 L3 fallback：不可行时考虑上一期权重/基准权重/可行线性投影，而不是直接 TopN 等权违规。
- 增加优化诊断表：每期 TE 预测值、实际行业偏离、单股最大偏离、alpha 暴露、active share、fallback 原因。
- 对 V1、V2 做同一信号下的 pre-cost/post-cost 对比，明确优化层是否真的增益。

退出标准：

- 不再把 `constraint_compliant=False` 的权重当作合规产品权重。
- 若保留非合规 fallback，报告必须以限制项披露，且 Run #2 前完成预登记。

## 阶段 6：回测与交易成本审查

目标：确认回测是可交易口径，不是纸面收益。

重点产物：

- `data/processed/backtest_nav.parquet`
- `data/processed/backtest_metrics.parquet`
- `data/processed/backtest_trades_v1.parquet`
- `data/processed/backtest_trades_v2.parquet`
- `data/processed/backtest_weights_v1.parquet`
- `data/processed/backtest_weights_v2.parquet`
- `src/backtest/engine.py`
- `src/backtest/transaction.py`
- `src/backtest/metrics.py`

核查项：

- T 日盘后生成权重，T+1 开盘价成交。
- 佣金双边 2.5 bps、滑点 5-10 bps、印花税 2023-08-28 前后切换函数正确。
- 停牌权重锁定；涨停不买、跌停不卖；一字板延后成交。
- `n_no_price` 只统计当前持仓或目标非零股票。
- NAV、成本、现金权重、换手率、月度收益之间能逐项对账。
- 年化双边换手 5-15 倍口径与 `backtest_trades_*.parquet` 一致。
- V1/V2 的超额收益、IR、TE、超额最大回撤、月胜率计算经极端路径测试覆盖。

改进方向：

- 建立收益拆解：gross alpha、优化约束影响、交易成本、现金拖累、不可交易状态拖累。
- 做成本敏感性只作为稳健性披露，不用来挑最优成本参数。
- 对 2022 每个月列出 V2 相对 V1 的收益差和主要持仓/行业差异。

退出标准：

- 回测关键指标可由交易日志和 NAV 复算。
- `tests/test_backtest_engine.py`、`tests/test_backtest_metrics.py` 通过。

## 阶段 7：归因与报告审查

目标：让“为什么效果不好”能被清楚解释，而不是只报告总收益。

重点产物：

- `data/processed/brinson_attribution.parquet`
- `data/processed/brinson_period_summary.parquet`
- `data/processed/factor_attribution.parquet`
- `data/processed/factor_attr_period_summary.parquet`
- `data/processed/attribution_nav_reconciliation.parquet`
- `data/processed/attribution_metadata.json`
- `reports/analysis_v2_results.md`

当前优先疑点：

- 2022 V2 Brinson：行业配置约 +2.66%，选股约 -3.48%，总超额约 -0.83%。
- 因子归因平均加权 R2 约 0.048，残差项约 +3.14%，说明当前风格解释力较弱。
- `attribution_metadata.json` 中存在非标准 `NaN`，且 `backtest_weights_v2` input hash 显示为 `e3b0c442`，需要核查 hash 生成逻辑。
- `attribution_nav_reconciliation.parquet` 首期 NAV 收益为 NaN，应明确是否为 warm-up 或应排除。

核查项：

- Brinson 行业收益、行业权重、交互项和回测 T+1 口径一致。
- 因子归因只使用入模因子或明确披露的风格因子集合。
- 归因 reconciliation 偏差阈值、偏差来源和 warning 逻辑准确。
- 报告中没有“测试集已证明”“稳定 alpha”等超出证据的表述。
- 报告必须披露：V2 未达 IR 目标、L3 非合规期、归因偏差、测试集剩余次数。

改进方向：

- 按月份拆解 2022 负超额来源，优先解释选股损失月份。
- 对行业内选股失败做因子暴露检查：价值、质量、成长、动量、流动性是否在弱势行业集中。
- 修复 metadata 严格 JSON、hash、NaN warm-up 表示，提升发布质量。

退出标准：

- 报告数字与 parquet 一致。
- 归因能解释主要收益差异，不能解释的部分作为残差和限制披露。

## 阶段 8：发布前门禁与 Run #2 预登记

目标：在唯一剩余正式测试集机会前，固定最终方案和风险披露。

门禁：

- 无 P0 问题：未来函数、PIT、基准、成交、测试集污染。
- 所有 P1 问题已修复或有明确降级披露。
- 最终因子集合、合成方法、优化参数、成本参数、fallback 策略冻结。
- 完整训练/验证 pipeline 可重跑，manifest 成功且 hash 记录完整。
- 全量 pytest 通过；warning 已分类，不得把环境 warning 写成业务通过证据。
- Run #2 的目录、run-id、预登记文档和 commit 纪律已准备。

Run #2 前必须写入预登记：

- 当前 git commit。
- 冻结配置 hash。
- 最终因子列表和合成方法。
- 优化参数和 fallback 规则。
- 预期风险：2024 小微盘冲击、验证期 V2 未达标、L3 fallback 历史问题。
- 禁止事项：看到测试结果后不得回改参数再重跑。

## 优先级定义

- P0 阻断：会让结果失真或污染测试集，必须先修。
- P1 高优先：不一定失真，但会让产品结论不可发布或无法解释。
- P2 改进：有助于提升稳健性或可读性，但不阻断 Run #2。
- P3 展示：图表、排版、文字优化。

当前建议优先级：

1. P0/P1：严格核查数据口径、PIT、测试集隔离、metadata/hash、L3 非合规权重。
2. P1：解释 V2 弱于 V1 的来源，尤其是优化层和 2022 选股效应。
3. P1/P2：重新评估入模因子稳定性与 IC_IR 加权，不迎合验证期。
4. P2：完善报告编码、严格 JSON、审查表和发布说明。

