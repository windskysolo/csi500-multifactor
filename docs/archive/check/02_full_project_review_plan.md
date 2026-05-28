# 全项目审查方案

> 生成日期：2026-05-19。
> 审查目标：把当前研究按产品化研究评审标准审查，优先发现会导致结论失真的量化错误，其次处理工程复现与测试覆盖问题。

## 1. 审查总原则

本轮审查不以“代码能跑”为合格标准，而以“结论可信、口径一致、可复现、可解释”为合格标准。

默认不运行 2023-2025 测试集流水线。任何测试集运行必须由用户明确授权，并在 git commit message 或专门记录中包含 `[TEST_SET_RUN_N]`，总次数不超过 2。

## 2. 阻断级验收门槛

以下问题一旦确认，应先修复再继续看收益表现：

- 使用价格指数而非中证500全收益指数作为基准。
- 股票收益、成交价格或基准收益出现复权口径混用。
- 财务、股东、分红等数据用报告期或未来公告信息进入 T 日信号。
- 调仓日投资域使用未来成分、静态成分或公告日错配。
- 因子去极值、中性化、合成权重使用全样本或测试期信息。
- 测试期 2023-2025 被反复运行、调参或用于因子选择。
- 回测成交不是 T+1 开盘，或忽略停牌/涨跌停/一字板约束。
- 交易成本未按 2023-08-28 切换印花税，或滑点/佣金明显低估。

## 3. 审查阶段

### 阶段 0：版本库与审查基线

目标：先建立可审查的静态基线，防止把数据产物、缓存、测试集运行和源码改动混在一起。

检查项：

- 当前 git 状态分类：源码、notebook、报告、数据、缓存分别列出。
- `.gitignore` 是否存在并覆盖 `data/raw/`、`data/processed/`、`*.parquet`、`*.csv`、`__pycache__/`、`.pytest_cache/`。
- 是否存在测试集运行记录；若没有，不补跑测试集。
- 文档口径统一：`AGENTS.md`、`PROJECT_PLAN_v1.1.md`、`DONE.md`、`src/config.py` 对数据源、行业体系、日期、成本、基准的描述是否一致。

产出：

- `check/03_findings_register.md`：问题登记表。
- `check/04_repo_hygiene_audit.md`：版本库与产物管理审查。

### 阶段 1：配置与项目事实口径

目标：确认所有关键口径集中、明确、没有互相冲突。

检查项：

- `src/config.py` 中样本期、成本、行业体系、新股过滤、随机种子是否完整。
- 计划文档中 CSMAR/Tushare、CITICS/SW2021 等冲突是否需要修订。
- 是否缺少依赖声明文件，例如 `requirements.txt` 或等价环境说明。
- 禁用依赖是否未被引入：`vectorbt`、`backtrader`、`zipline`、Streamlit、Dash 等。

产出：

- 配置缺口和文档冲突清单。
- 必要时提出最小修订方案，不直接扩大依赖。

### 阶段 2：数据与 PIT 审查

目标：验证数据层没有未来信息、口径错配和生存者偏差。

重点文件：

- `scripts/download_tushare.py`
- `scripts/download_supplement.py`
- `scripts/csv_to_parquet.py`
- `src/data/loader.py`
- `src/data/pit_loader.py`
- `src/data/universe.py`
- `data/processed/*.parquet`

检查项：

- 股票代码是否统一为字符串，日期是否为 `datetime64` 或 `pd.Timestamp`。
- `daily_quote.parquet` 是否同时保留原始价和后复权 `open_adj`、`close_adj`，策略收益是否只用后复权口径。
- `index_quote.parquet` 是否为中证500全收益指数，不是价格指数。
- `index_member.parquet` 是否按生效日对齐，调仓日可投资域是否来自当日实际成分。
- `financial_pit.parquet`、`indicator_pit.parquet`、`holder_pit.parquet`、`dividend_pit.parquet` 的 `pit_date` 是否不晚于 T。
- 自由流通市值是否来自 `free_share * price`，并用于 `log_free_float_mv`。
- `stock_status.parquet` 是否覆盖 ST、新股、停牌、涨停、跌停、一字板。
- 随机抽样 5-10 只股票手工核对 PIT 可见性。

建议测试：

- 新增 `tests/test_pit.py`。
- 新增 `tests/test_universe.py`。
- 新增数据 schema 和日期边界测试。

产出：

- `check/05_data_pit_audit.md`。

### 阶段 3：因子构建与预处理审查

目标：确认因子只使用 T 日及以前信息，横截面处理不泄漏样本期信息。

重点文件：

- `src/factors/financial_factors.py`
- `src/factors/price_factors.py`
- `src/factors/preprocess.py`
- `scripts/build_factor_panels.py`
- `data/processed/factor_panels/`

检查项：

- 每个因子的输入窗口是否严格 `<= T`。
- 价格动量和波动因子是否避免 `shift(-n)` 进入信号。
- 财务 TTM 和同比计算是否基于 PIT 最新可见数据。
- MAD 去极值边界是否只来自 T 日横截面。
- 中性化是否为 `factor ~ C(industry) + log_free_float_mv`。
- 银行、非银金融财务因子是否置空或单独处理。
- 因子数量是否仍在 20-30 个候选范围内，超出时需说明。

建议测试：

- `tests/test_preprocess.py`：MAD、标准化、中性化、NaN 传播。
- 每个代表性因子的时间边界测试。

产出：

- `check/06_factor_construction_audit.md`。

### 阶段 4：单因子评价与统计严谨性

目标：确认因子有效性评估没有前视偏差，统计口径完整。

重点文件：

- `src/evaluation/ic_analysis.py`
- `src/evaluation/quintile_backtest.py`
- `src/evaluation/shift_test.py`
- `scripts/run_factor_evaluation.py`
- `reports/factor_evaluation/`

检查项：

- 未来收益是否用 T+1 开盘到下一执行窗口开盘，变量不进入策略。
- IC 是否按调仓日横截面计算，空值和退市/出成分处理是否合理。
- 时间错位测试是否执行且结果落盘。
- IC_IR、t 统计量、p 值和多重检验校正是否齐全。
- 单因子筛选是否只使用训练集，验证集只用于稳健性确认。
- 分组回测是否用全收益基准和合理交易假设解释，不能作为实盘收益承诺。

建议测试：

- `tests/test_evaluation.py`：未来收益对齐、shift test、IC 极端样例。

产出：

- `check/07_factor_evaluation_audit.md`。

### 阶段 5：信号合成审查

目标：确认因子权重和合成信号不污染验证集和测试集。

重点文件：

- `src/signal/combiner.py`
- `notebooks/03_factor_combination.ipynb`
- `reports/factor_combination/`

检查项：

- 等权和 IC_IR 权重的训练/验证边界是否清晰。
- 滚动 IC_IR 窗口是否严格早于 T。
- 因子方向是否来自训练集，不在验证/测试集反复调整。
- 高相关因子过滤是否只用训练期统计。
- 合成信号面板是否与成分股、行业、市值和状态数据对齐。

产出：

- `check/08_signal_combination_audit.md`。

### 阶段 6：协方差与组合优化审查

目标：确认优化约束可解释、可行、失败有记录，且没有用未来风险信息。

重点文件：

- `src/portfolio/covariance.py`
- `src/portfolio/optimizer.py`
- `notebooks/04_portfolio_optimization.ipynb`
- `data/processed/cov_cache/`
- `data/processed/portfolio_weights_*.parquet`

检查项：

- 协方差估计窗口是否严格使用 T 之前收益。
- Ledoit-Wolf 收缩是否实际使用，正定修复是否记录。
- 个股偏离、行业偏离、跟踪误差约束是否符合项目风险预算。
- 停牌锁定、涨停不可买、跌停不可卖是否进入优化约束。
- L1/L2/L3 fallback 触发条件、次数、日期是否可追踪。
- 权重归一化、空权重、极端 alpha、缺协方差股票是否有防御。

建议测试：

- `tests/test_optimizer.py`：停牌、涨停、跌停、不可行 fallback、正定修复。

产出：

- `check/09_portfolio_optimization_audit.md`。

### 阶段 7：回测、成本与指标审查

目标：确认回测不是“纸面收益”，而是遵守成交、成本、状态约束和基准口径。

重点文件：

- `src/backtest/transaction.py`
- `src/backtest/metrics.py`
- `src/backtest/engine.py`
- `backtest_design.md`
- `notebooks/05_backtest.ipynb`
- `data/processed/backtest_*.parquet`

检查项：

- 调仓权重是否 T 日盘后生成，交易是否 T+1 开盘执行。
- `open_adj` 是否不前填；停牌无法成交是否体现在 NaN 和锁定逻辑中。
- 佣金、滑点、印花税切换是否逐笔或逐调仓正确计算。
- 涨停、跌停、一字板在成交层是否再次生效。
- 基准 NAV 是否来自中证500全收益指数并同日起点归一化。
- IR、TE、年化收益、最大回撤、超额最大回撤、月胜率、换手是否计算正确。
- 异常优秀结果是否触发未来函数排查清单。

建议测试：

- `tests/test_transaction.py`：2023-08-27、2023-08-28 两侧印花税。
- `tests/test_backtest_metrics.py`：收益、回撤、IR 极端路径。
- `tests/test_backtest_engine.py`：停牌、涨跌停、T+1 成交。

产出：

- `check/10_backtest_audit.md`。

### 阶段 8：归因与报告审查

目标：确认报告结论可解释、诚实暴露风险，不把验证或测试结果包装成稳定 alpha。

重点文件：

- `src/attribution/brinson.py`
- `src/attribution/factor_attr.py`
- `notebooks/06_attribution.ipynb`
- `reports/analysis_v2_results.md`

检查项：

- Brinson 归因的行业权重、行业收益和交互项是否对齐调仓期。
- 因子归因是否避免使用同期间未来因子暴露。
- 2024 小微盘危机是否有单独分析。
- 报告是否明确样本期、成本、滑点、基准、测试集运行次数。
- 结论是否附带统计显著性和局限性。

产出：

- `check/11_attribution_report_audit.md`。

### 阶段 9：复现、测试与发布前清单

目标：形成可重复的最小复现实验和发布前阻断清单。

检查项：

- 是否能从 raw 数据重建 processed 数据。
- 是否能从 processed 数据重建因子、信号、组合、回测和归因。
- 单元测试是否覆盖 PIT、信号错位、成本、状态约束、指标极端样例。
- 所有随机过程是否固定 seed。
- 日志是否足以定位数据缺失、fallback、优化失败、测试集运行。

产出：

- `check/12_repro_test_release_checklist.md`。

## 4. 严重度定义

- `Blocker`：会直接导致结论失真、未来函数、测试集污染、基准错误或无法复现。
- `High`：可能显著夸大收益或低估风险，但可通过局部修复验证。
- `Medium`：影响稳健性、可维护性、异常路径或报告可信度。
- `Low`：命名、文档、样式、局部工程卫生问题。

## 5. 推荐执行顺序

1. 先做阶段 0-1，解决版本库卫生和口径冲突。
2. 再做阶段 2-4，优先排除数据和因子未来函数。
3. 然后做阶段 5-7，检查信号、优化和回测是否真实可交易。
4. 最后做阶段 8-9，完善归因、报告、测试和复现。

在阶段 2-7 完成前，不应解读 2023-2025 测试期收益表现。

