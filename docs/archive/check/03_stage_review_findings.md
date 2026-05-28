# 分阶段审查问题登记表

> 生成日期：2026-05-19  
> 审查依据：`check/00_review_requirements.md`、`check/02_full_project_review_plan.md`  
> 本轮范围：阶段 0（版本库与审查基线）、阶段 1（配置与项目事实口径）、阶段 2（数据与 PIT 审查）、阶段 3（因子构建与预处理审查）、阶段 4（单因子评价与统计严谨性审查）、阶段 5（信号合成审查）、阶段 6（协方差与组合优化审查）、阶段 7（回测、成本与指标审查）、阶段 8（归因与报告审查）和阶段 9（复现、测试与发布前清单）。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未修改业务代码。

## 阶段状态总览

| 阶段 | 名称 | 本轮状态 | 结论 |
|---:|---|---|---|
| 0 | 版本库与审查基线 | 已审查 | 不通过。版本库卫生和测试集运行基线存在高风险。 |
| 1 | 配置与项目事实口径 | 已审查 | 不通过。数据源、行业体系、依赖声明和参数集中存在问题。 |
| 2 | 数据与 PIT 审查 | 已审查 | 不通过。后复权收益、停牌状态覆盖、成分股权重来源和数据凭证管理存在高风险。 |
| 3 | 因子构建与预处理审查 | 已审查 | 不通过。量价因子继承收益口径错误，测试期扩展缺少防护，测试覆盖不足。 |
| 4 | 单因子评价与统计严谨性 | 已审查 | 不通过。单因子评价结果继承收益口径污染，报告产物与下游 JSON 不一致，时间错位与缓存校验不足。 |
| 5 | 信号合成审查 | 已审查 | 不通过。合成信号继承不一致的最终因子集合，测试流水线会覆盖通用信号产物，冷启动方向和审计产物不足。 |
| 6 | 协方差与组合优化审查 | 已审查 | 不通过。L3 fallback 违反停牌锁定和单股偏离约束，缺失协方差被伪装为 L1，测试流水线会覆盖通用权重产物。详见 `check/09_portfolio_optimization_audit.md`。 |
| 7 | 回测、成本与指标审查 | 已审查 | 不通过。T+1 开盘调仓使用上一日收盘净值，缺失状态默认 FREE，当前报告与落盘指标不一致。详见 `check/10_backtest_audit.md`。 |
| 8 | 归因与报告审查 | 已审查 | 不通过。归因收益口径与 T+1 开盘净值回测不一致，实际权重被重新归一导致成本/现金被抹掉，Brinson 依赖 notebook 外部补齐基准成分，因子归因未使用真实入模因子集合。详见 `check/11_attribution_report_audit.md`。 |
| 9 | 复现、测试与发布前清单 | 已审查 | 不通过。一键复现链路不完整，测试流水线仍会覆盖通用产物，测试覆盖和 run manifest 不足。详见 `check/12_repro_test_release_checklist.md`。 |

## 审查命令摘要

本轮使用静态检查为主，避免触发测试集或重生成产物。

- `git -c core.excludesfile= status --porcelain=v1 --untracked-files=all`
- `git -c core.excludesfile= log --oneline --grep='\[TEST_SET_RUN_' --all`
- `git -c core.excludesfile= show -s --format='%h %ci %s' HEAD`
- `rg -n "CSMAR|Tushare|中信|SW2021|CITICS|数据源|行业分类|全收益|价格口径|基准指数" ...`
- `rg -n "vectorbt|backtrader|zipline|streamlit|dash|rqalpha|jqdatasdk|joinquant|tqsdk" src scripts`
- `Get-ChildItem tests -Force -Recurse`
- `python -m pytest tests/ -q`
- `git -c core.excludesfile= ls-files data`
- `git -c core.excludesfile= ls-files "*.csv" "*.parquet"`
- `git -c core.excludesfile= ls-files "*__pycache__*" "*.pyc"`

## 阶段 0：版本库与审查基线

### F0-001 缺少 `.gitignore`，大量数据和生成产物进入 git 状态

**严重度**：Blocker

**证据**：

- 根目录未发现 `.gitignore`、`requirements.txt`、`README.md` 等基础文件。
- `PROJECT_PLAN_v1.1.md:560-569` 明确要求 `.gitignore` 排除：
  - `data/raw/`
  - `data/processed/`
  - `data/factor/`
  - `*.parquet`
  - `*.csv`
  - `.ipynb_checkpoints/`
  - `__pycache__/`
- `check/00_review_requirements.md:38` 要求生成文件、数据产物和源码分离。
- 当前 git 状态汇总显示：
  - `17611` 个 `data/` 文件处于 `A` 状态。
  - `127` 个 `data/` 文件处于未跟踪状态。
  - 扩展名维度：`17612` 个 `.csv`、`139` 个 `.parquet`、`8` 个 `.png`、`6` 个 `.pyc` 出现在 git 状态中。

**影响**：

这会让版本库无法作为可靠审查基线。原始数据、处理后数据、缓存、图片、pyc 混入源码变更后，无法区分“研究逻辑变化”和“产物变化”。对于产品化研究，这属于发布前阻断问题：任何收益结果都可能对应某个不可复现的本地产物状态。

**修复建议**：

1. 新增 `.gitignore`，至少覆盖计划文档列出的数据、缓存和生成产物。
2. 使用非破坏性方式将已进入 index 的生成产物移出版本库，例如 `git rm --cached`，不删除本地数据。
3. 若确需提交小样本测试数据，放到独立的 `tests/fixtures/`，并明确大小、来源和脱敏规则。
4. 报告图片如需入库，应移动到 `reports/figures/` 并只保留最终报告必要图片。

**验证方式**：

- `git status --short` 不再显示 `data/raw/`、`data/processed/`、`*.parquet`、`__pycache__/`。
- `git check-ignore -v data/processed/daily_quote.parquet` 能显示命中的 `.gitignore` 规则。

### F0-002 工作区混合了源码、notebook、报告、数据和缓存，无法建立干净审查基线

**严重度**：High

**证据**：

当前非数据源码相关状态包括：

- `A  AGENTS.md`
- `A  DONE.md`
- `A  backtest_design.md`
- `M  scripts/csv_to_parquet.py`
- `M  scripts/download_supplement.py`
- `M  scripts/download_tushare.py`
- `M  scripts/run_pipeline.py`
- `M  notebooks/02_factor_evaluation.ipynb`
- `M  notebooks/03_factor_combination.ipynb`
- `M  notebooks/04_portfolio_optimization.ipynb`
- `M  notebooks/05_backtest.ipynb`
- `M  notebooks/06_attribution.ipynb`
- 多个 `scripts/__pycache__/*.pyc` 和 `src/signal/__pycache__/combiner.cpython-313.pyc`

**影响**：

当前无法判断 notebook 输出、报告产物和 `src/`/`scripts/` 代码是否来自同一次可复现流水线。继续审查收益或因子效果前，必须先固定源码版本和数据产物版本，否则任何结论都可能被后续“顺手改动”污染。

**修复建议**：

1. 先完成 F0-001，把数据和缓存移出版本控制。
2. 将源码变更、notebook 结果、报告产物拆成不同 commit 或至少不同审查批次。
3. 对每个 notebook 记录其依赖的 commit、输入 parquet 版本和生成时间。

**验证方式**：

- 重新运行 `git status --short`，源码、notebook、报告、数据分别可清晰归类。
- 审查前能明确回答“本次收益结果对应哪个代码 commit 和哪批输入数据”。

### F0-003 测试集已运行 1 次，且测试集脚本硬编码 `[TEST_SET_RUN_1]`

**严重度**：High

**证据**：

- git 历史存在一次测试集运行记录：
  - `80708d4 2026-05-12 16:44:45 +0800 [TEST_SET_RUN_1] 测试集评估完成 (2023-2025)`
- `scripts/run_test_pipeline.py:3` 标记为 `[TEST_SET_RUN_1]`。
- `scripts/run_test_pipeline.py:415` 打印 `[TEST_SET_RUN_1] 测试集回测结果`。
- `scripts/run_test_pipeline.py:464` 记录 `[TEST_SET_RUN_1] 测试集评估流程启动`。
- `scripts/run_test_pipeline.py:515` 记录 `[TEST_SET_RUN_1] 全流程完成`。

**影响**：

项目纪律要求测试集 2023-2025 总运行次数不超过 2 次。当前已经有一次正式记录，剩余最多 1 次。脚本硬编码 `TEST_SET_RUN_1` 会让第二次运行也显示为 Run 1，导致测试集运行计数失真，给后续调参污染测试集留下操作风险。

**修复建议**：

1. 新增 `check/test_set_run_log.md`，记录每次测试集运行的日期、commit、目的、命令、输出文件和结论。
2. 将 `scripts/run_test_pipeline.py` 的 run id 改为显式 CLI 参数，例如 `--run-id 2`，默认拒绝运行。
3. 脚本启动时读取运行记录，若已有 2 次 `[TEST_SET_RUN_N]`，直接退出。
4. 在审查阶段 2-7 完成前，不再运行测试集。

**验证方式**：

- `git log --oneline --grep='\[TEST_SET_RUN_' --all` 返回的次数与 `check/test_set_run_log.md` 一致。
- 脚本中不再出现硬编码 `[TEST_SET_RUN_1]`。

### F0-004 `.pytest_cache/` 目录权限异常，git 状态检查不完整

**严重度**：Low

**证据**：

- `git status` 汇总时出现警告：`warning: could not open directory '.pytest_cache/': Permission denied`。

**影响**：

这本身不会改变研究结果，但会让 git 状态检查产生噪音，并可能隐藏 `.pytest_cache/` 下的本地产物。该目录本应被忽略，不应进入审查范围。

**修复建议**：

1. 在 `.gitignore` 加入 `.pytest_cache/`。
2. 修复该目录权限或在确认无必要后由用户清理。

**验证方式**：

- `git status --short` 不再输出 `.pytest_cache` 权限警告。

### F0-005 `tests/` 目录没有源码测试文件

**严重度**：High

**证据**：

- `Get-ChildItem tests -Force -Recurse` 只发现 `tests/__pycache__/`，未发现 `tests/test_*.py`。
- `AGENTS.md:145-152` 要求 PIT、信号错位、成本、涨跌停/停牌、收益与回撤极端场景必须有单元测试。
- `PROJECT_PLAN_v1.1.md:111-115` 计划包含 `test_universe.py`、`test_preprocess.py`、`test_pit.py`、`test_backtest.py`。

**影响**：

当前关键金融逻辑无法自动验证。尤其是 PIT、测试集纪律、T+1 成交、印花税切换、涨跌停/停牌约束，这些都是会直接扭曲回测结论的高风险点。没有测试时，后续任何重构都缺少回归保护。

**修复建议**：

按风险优先级补齐测试：

1. `tests/test_pit.py`
2. `tests/test_transaction.py`
3. `tests/test_universe.py`
4. `tests/test_backtest_metrics.py`
5. `tests/test_backtest_engine.py`
6. `tests/test_evaluation.py`
7. `tests/test_optimizer.py`

**验证方式**：

- `python -m pytest -q` 能发现并运行测试。
- 每个测试文件覆盖本项目硬性规则中的至少一个高风险点。

### F0-006 本地工具设置文件进入 git 状态

**严重度**：Medium

**证据**：

- `git status` 显示 `A  .claude/settings.local.json`。

**影响**：

本地工具设置通常包含个人路径、权限或环境偏好，不应进入产品化研究仓库。它会降低跨机器复现性，也可能暴露本地环境信息。

**修复建议**：

1. 将 `.claude/settings.local.json` 移出版本控制。
2. 在 `.gitignore` 中忽略该文件。
3. 若确需团队共享设置，应提供无本地路径、无敏感信息的模板文件。

**验证方式**：

- `git status --short -- .claude/settings.local.json` 无输出。

## 阶段 1：配置与项目事实口径

### F1-001 数据源口径在文档中冲突：Tushare Pro 与 CSMAR 混用

**严重度**：High

**证据**：

- `AGENTS.md:12`：数据源为 Tushare Pro，非 CSMAR。
- `DONE.md:4`：数据来源为 Tushare Pro。
- `PROJECT_PLAN_v1.1.md:25`：边界声明写“仅用 Tushare Pro 公开数据”。
- `PROJECT_PLAN_v1.1.md:35`：关键决策表仍写“数据源 CSMAR”。
- `PROJECT_PLAN_v1.1.md:81`：禁用库原因写“与 CSMAR 不兼容”。
- `PROJECT_PLAN_v1.1.md:95`：目录结构注释写 `data/raw/` 是 CSMAR 原始 CSV。
- `PROJECT_PLAN_v1.1.md:180`：分周计划写“从 CSMAR 下载”。
- `PROJECT_PLAN_v1.1.md:208`：PIT 风险用 CSMAR 字段 `Annodt` / `Accper` 描述。
- `scripts/download_tushare.py:466-467` 将 Tushare 字段映射为 `ann_date` 对应 CSMAR `Annodt`、`end_date` 对应 `Accper`。

**影响**：

数据源不是纯文档问题。Tushare 与 CSMAR 的字段名、单位、复权口径、公告日字段、行业接口、指数代码都不同。文档冲突会导致后续审查和复现时使用错误字段或错误单位，尤其会影响 PIT 审查、自由流通市值、行业中性化和基准确认。

**修复建议**：

1. 将 `PROJECT_PLAN_v1.1.md` 统一更新为 Tushare Pro 口径。
2. 保留一个字段映射表：`Tushare ann_date/f_ann_date/end_date` 对应项目中的 PIT 概念。
3. 删除或标注 CSMAR 旧口径为历史遗留，不得作为当前实现依据。

**验证方式**：

- `rg -n "CSMAR|Annodt|Accper" PROJECT_PLAN_v1.1.md AGENTS.md DONE.md src scripts` 只剩下字段映射或历史说明，不再作为当前数据源方案。

### F1-002 行业体系口径冲突：计划文档仍写中信一级，实际代码和数据为 SW2021

**严重度**：High

**证据**：

- `AGENTS.md:75`：行业用申万2021一级（SW2021，31 个行业）。
- `src/config.py:43-47`：`INDUSTRY_SRC = "SW2021"`。
- `src/config.py:59-63`：金融行业代码按申万一级 SW2021 定义。
- `DONE.md:306-308`：说明 `industry_citics.csv` 文件名含 citics，但实际为 SW2021；OpenClaw 代理不提供中信数据。
- `DONE.md:326`：行业中性化为 SW2021 申万一级，非中信一级。
- `PROJECT_PLAN_v1.1.md:47`：中性化基准仍写“行业（中信一级）+ log(自由流通市值)”。
- `PROJECT_PLAN_v1.1.md:493`：风险登记表仍写“全程统一中信一级”。
- `scripts/download_tushare.py:296-311`：逻辑为优先 CITICS，失败降级 SW2021。
- `scripts/csv_to_parquet.py:421-427`：实际处理 `industry_citics.csv` 为 SW2021。

**影响**：

行业体系会直接影响中性化残差、行业偏离约束、Brinson 归因和金融股处理。若报告按中信解释、代码按申万运行，会导致风险暴露解释错误。产品化评审中，这会被视为模型口径不可追溯。

**修复建议**：

1. 当前项目事实应统一为 SW2021，除非重新下载并验证中信行业历史成分。
2. 将 `PROJECT_PLAN_v1.1.md` 的中信一级表述改为 SW2021。
3. 将 `industry_citics.csv` 改名或在生成产物 metadata 中强制记录 `industry_src=SW2021`，避免文件名误导。
4. 将 `scripts/download_tushare.py` 的“优先 CITICS”逻辑和文档说明改为与实际数据一致，或显式输出最终行业源。

**验证方式**：

- `src/config.py`、计划文档、报告和产物字段均显示同一行业源。
- 抽查 `data/processed/industry.parquet` 的行业数量为 SW2021 一级 31 个加未分类。

### F1-003 缺少依赖和环境声明文件

**严重度**：High

**证据**：

- 根目录未发现 `requirements.txt`、`pyproject.toml`、`environment.yml`、`Pipfile` 等环境声明。
- `PROJECT_PLAN_v1.1.md:91` 计划根目录包含 `requirements.txt`。
- `PROJECT_PLAN_v1.1.md:576` 明确 `requirements.txt` 应进入 git。
- 实际代码依赖包括但不限于：
  - `tushare`：`scripts/download_tushare.py:31`、`scripts/download_supplement.py:41`
  - `cvxpy`：`src/portfolio/optimizer.py:39`
  - `sklearn`：`src/portfolio/covariance.py:35`
  - `statsmodels`：`src/factors/preprocess.py:24`
  - `scipy`：`src/evaluation/ic_analysis.py:35`
  - `matplotlib`：`scripts/run_factor_evaluation.py:34-36`
  - `nbformat`：`scripts/build_data_quality_notebook.py:10`

**影响**：

无法在新机器或干净环境复现研究。对量化研究而言，pandas、cvxpy、求解器、sklearn 和 pyarrow 的版本差异会改变日期频率、Parquet schema、优化器可用性和数值结果。

**修复建议**：

1. 新增 `requirements.txt` 或 `pyproject.toml`。
2. 至少声明计划中的核心依赖及当前实际额外依赖：`tushare`、`nbformat`。
3. 对 `cvxpy` 和求解器版本给出已验证下限。
4. 在 README 中写明 Python 版本和安装命令。

**验证方式**：

- 在干净虚拟环境中执行安装命令。
- 运行导入冒烟测试：`python -c "import pandas, numpy, pyarrow, scipy, statsmodels, sklearn, cvxpy, tushare"`。

### F1-004 缺少 README 或等价复现入口文档

**严重度**：Medium

**证据**：

- 根目录未发现 `README.md`。
- `PROJECT_PLAN_v1.1.md:89-91` 的理想结构包含 `README.md` 和 `requirements.txt`。

**影响**：

当前项目依赖 `PROJECT_PLAN_v1.1.md`、`DONE.md` 和脚本注释拼接理解。对产品化研究审查来说，缺少一个权威入口会导致复现路径、测试集纪律、数据构建顺序和报告生成方式不清晰。

**修复建议**：

新增 `README.md`，最少包含：

1. 项目目标和非投资建议声明。
2. 数据源与样本期。
3. 环境安装。
4. 从 raw 到 processed、factor、signal、portfolio、backtest、attribution 的命令顺序。
5. 测试集运行纪律和当前 `[TEST_SET_RUN_1]` 状态。

**验证方式**：

- 新成员只依赖 README 能完成训练/验证期复现，不触发测试集。

### F1-005 关键研究参数未完全集中到 `src/config.py`

**严重度**：Medium

**证据**：

`src/config.py` 已集中样本期、成本、行业源、新股过滤、随机种子、读取线程数，但仍有多处研究参数散落：

- `src/portfolio/optimizer.py:59-65`：`te_target_annual=0.05`、`industry_max_dev=0.02`、`single_max_dev=0.01`、`topn=50`、`max_solve_seconds=30.0`。
- `scripts/run_test_pipeline.py:72-75`：重复指定优化参数。
- `scripts/run_factor_evaluation.py:73-75`：`HIGH_CORR_THRESHOLD=0.70`、`SHARPE_LS_THRESHOLD=0.50`、`IC_EFFECTIVE_CRITERIA`。
- `src/evaluation/ic_analysis.py:46-49`：IC 有效性阈值。
- `src/signal/combiner.py:36-38`：IC 历史窗口、滚动窗口、最少有效因子数。
- `src/factors/price_factors.py:53-54`：波动率和残差波动率最少观测期。

**影响**：

参数分散会降低复现性，也增加测试集污染风险。后续如果为了改善测试期结果修改某个脚本常量，很难在审查时发现该参数是否属于训练期前已冻结的研究设定。

**修复建议**：

1. 将研究级参数集中到 `src/config.py` 或新增 `src/research_config.py`。
2. 将 CLI 或脚本覆盖参数写入产物 metadata。
3. 明确哪些参数在训练期冻结，哪些仅为工程超时或安全阈值。

**验证方式**：

- `rg -n "THRESHOLD|DEFAULT_|WINDOW|topn|max_dev|te_target|MIN_" src scripts` 中除局部实现常量外，研究参数都有配置来源。
- 报告产物记录本次运行使用的配置快照。

### F1-006 禁用依赖未在源码和脚本中发现，但仍需纳入依赖审查门禁

**严重度**：Low

**证据**：

- 在 `src/` 和 `scripts/` 中检索 `vectorbt|backtrader|zipline|streamlit|dash|rqalpha|jqdatasdk|joinquant|tqsdk` 未发现源码引用。
- `AGENTS.md:123-128` 禁止这些库。

**影响**：

当前没有违规引入禁用框架，这是阶段 1 的通过项。但由于缺少依赖清单，仍不能从环境层面确认这些库不会被误用。

**修复建议**：

在依赖清单和 README 中明确本项目不依赖这些库。后续 CI 或本地检查可增加一个简单的禁用依赖扫描。

**验证方式**：

- `rg` 扫描源码无禁用依赖。
- `requirements.txt` 或 `pyproject.toml` 中无禁用依赖。

## 阶段 0-1 总结

本轮最需要先修的是版本库卫生和事实口径统一。当前不适合继续解读收益表现，也不应运行第二次测试集。

优先级建议：

1. 先修 F0-001：新增 `.gitignore` 并将数据、缓存、生成产物移出版本控制。
2. 固化测试集运行记录：确认 `[TEST_SET_RUN_1]` 是唯一一次正式测试集运行，剩余最多 1 次。
3. 统一 `PROJECT_PLAN_v1.1.md` 的当前事实口径：Tushare Pro + SW2021。
4. 新增依赖声明和 README。
5. 补齐最小测试骨架，至少先覆盖 PIT、成本、交易状态和指标极端场景。

## 阶段 2：数据与 PIT 审查

> 审查日期：2026-05-19  
> 审查范围：`scripts/download_tushare.py`、`scripts/download_supplement.py`、`scripts/csv_to_parquet.py`、`src/data/loader.py`、`src/data/pit_loader.py`、`src/data/universe.py`、核心 `data/processed/*.parquet`。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未重建数据产物，未修改业务代码。

### 阶段 2 只读抽查摘要

本阶段用静态代码审查和本地 Parquet 只读抽查交叉验证。关键结果如下：

- `daily_quote.parquet`：2,692,934 行，2016-01-04 至 2025-12-31，1264 只股票，包含 `open_adj`、`close_adj`、`ret`。
- `index_quote.parquet`：2430 行，日期与股票行情交易日完全一致；源文件 `index_daily.csv` 仅含 `H00905.CSI`；`nav` 可由 `index_ret` 完全重算。
- `index_member.parquet`：120 个调仓日，每日 500 只；权重合计范围 99.984 至 100.019。
- `financial_pit.parquet` / `indicator_pit.parquet`：`pit_date` 与 `end_date` 均无空值，且没有 `pit_date <= end_date` 的财务主表异常；随机抽样 PIT 快照均满足 `pit_date <= T` 且 `end_date < T`。
- `holder_pit.parquet`：有 427 条 `pit_date <= end_date` 的原始异常，但 `available_date=max(pit_date,end_date)` 已保存，`loader.load_holder_pit` 查询时使用 `available_date <= T`，方向是保守的。
- `industry.parquet`：行业覆盖率 100%，行业名称数 32（SW2021 31 个一级行业 + 未分类口径）。

### F2-001 `daily_quote.ret` 不是可靠的后复权收益

**严重度**：Blocker

**证据**：
- `scripts/csv_to_parquet.py:236-240` 先计算 `close_adj = close * adj_factor` 和 `open_adj = open * adj_factor`，但 `ret` 直接取 `pct_chg / 100`。
- 只读抽查显示：`ret` 与 `pct_chg/100` 完全一致，但它与 `close_adj` 分股票计算的后复权收益并不一致：
  - `close_adj.pct_change()` 与 `ret` 的最大绝对差约 `0.17656`。
  - 差异超过 1 bp 的行数为 `3650`。
  - 例：`300159.SZ` 在 2025-12-24 的 `ret` 为 `+17.3611%`，但 `close_adj` 从 62.30142 到 62.11764，对应收益为 `-0.2950%`。
  - 例：`000998.SZ` 在 2020-01-02 的 `ret` 为 `+9.9932%`，但 `close_adj` 收益为 `+7.0918%`。
- `src/factors/price_factors.py:293-405` 的 `vol_60d`、`ivol_60d`、`max_ret` 使用 `daily_quote.ret`。
- `src/attribution/brinson.py:325` 和 `src/attribution/factor_attr.py:432` 使用 `dq["ret"].unstack("ts_code")` 做归因期股票收益。

**影响**：
本项目硬规则要求股票收益全程使用后复权收益率。当前 `ret` 实际继承了 Tushare `pct_chg` 口径，不能保证等于 `close_adj` 的后复权价格收益。它会直接污染价格类因子、风险归因和任何依赖 `daily_quote.ret` 的结果。这个问题未修复前，不应解释价格类因子表现、波动率因子表现或归因结果。

**修复建议**：
1. 在 `process_daily_quote()` 中按 `ts_code` 排序后，用 `close_adj.groupby(ts_code).pct_change(fill_method=None)` 生成 `ret`。
2. 对每只股票第一条记录使用 `NaN` 或显式置 0，但必须在因子与回测层统一处理，不要让首日收益进入统计窗口。
3. 增加数据测试：`ret` 必须等于 `close_adj` 分股票 `pct_change`，允许极小浮点误差。
4. 对已经生成的 `factor_panels`、协方差缓存、归因结果全部标记为依赖旧收益口径，修复后必须重建。

**验证方式**：
- `max(abs(ret - close_adj.groupby(ts_code).pct_change())) < 1e-10`，首行除外。
- `rg -n 'daily_quote.ret|dq\\["ret"\\]|ret_pivot' src scripts`，确认下游模块拿到的是修复后的后复权收益。

### F2-002 `stock_status.parquet` 未覆盖完全停牌的无行情日期，回测层又把缺失状态默认为可交易

**严重度**：Blocker

**证据**：
- `scripts/csv_to_parquet.py:984` 使用 `_load_stock_pairs()` 作为 `stock_status` 全量网格；该网格来自 `daily_quote.parquet`。
- `scripts/csv_to_parquet.py:1146-1147` 注释承认“完全停牌股票不在 daily_quote 中”。
- 本地原始停牌数据只读抽查：
  - `raw/suspend` 去重后有 `43,601` 个 `(trade_date, ts_code)` 停牌键。
  - 其中只有 `2,040` 个存在于 `stock_status.parquet`。
  - `41,561` 个原始停牌键缺失于 `stock_status.parquet`。
  - `index_member.parquet` 中也有 `1,048` 个 `(rebalance_date, ts_code)` 不在 `stock_status` 网格内，其中 `928` 个被 M12 补成 `is_suspended=True`。
- `src/backtest/engine.py:187` 明确“不在 status_snap 中的股票默认为 FREE”。
- `src/backtest/engine.py:195-202` 对缺失行 `fillna(False)`，最终不会触发 `LOCKED`。

**影响**：
这违反停牌约束硬规则。完全停牌日没有行情行时，`stock_status` 无法表达“该股票停牌且不能成交”。虽然 `index_member` 在月末快照层做了部分补救，但回测真实执行在 T+1 开盘，执行日状态仍依赖 `load_stock_status(T+1)`。缺失状态默认 `FREE` 会把无法成交的股票视为可交易，导致换仓、成本、权重漂移和收益路径失真。

**修复建议**：
1. 重建 `stock_status.parquet` 时，不要只用 `daily_quote` 网格；应使用真实交易日历 × 相关股票池，并结合上市/退市日期裁剪。
2. 将 `raw/suspend` 中所有停牌键完整写入 `stock_status`，即使当天没有 `daily_quote` 行。
3. `load_stock_status()` 或回测 `_status_to_trade_state()` 对权重面板中的缺失股票不应默认 `FREE`；至少应记录并按 `LOCKED` 或显式报错处理。
4. 增加测试：随机抽取 `raw/suspend` 的 50 个停牌键，必须在 `stock_status` 中存在且 `is_suspended=True`。

**验证方式**：
- `raw_suspend_index.difference(stock_status.index)` 必须为空，或所有差异都有退市/上市前后的明确解释。
- 对 T+1 执行日的全部持仓股票，`load_stock_status(T+1).reindex(codes)` 不应出现未解释缺失。

### F2-003 成分股权重快照来源不可复现，缺少下载脚本、校验和与元数据

**严重度**：High

**证据**：
- `src/config.py:74` 将 `CSI500_WEIGHT_FILE` 指向 `data/csi500_index_weight_201601_202512.csv`。
- `scripts/download_tushare.py:43` 和 `scripts/download_supplement.py:54` 只把该文件作为输入读取，不负责生成它。
- `scripts/download_tushare.py:118-122` 从该 CSV 提取历史成分股股票列表，后续所有股票数据下载都依赖它。
- `scripts/csv_to_parquet.py:1182-1190` 直接读取该 CSV 并生成 `index_member.parquet`。
- 该 CSV 头部显示 `index_code=000905.SH`、`con_code`、`trade_date`、`weight`；`DONE.md:40-53` 说明用途，但没有记录数据源、下载命令、生成时间、校验和、供应商字段说明或生效日定义。

**影响**：
成分股快照决定可投资域、基准权重、行业偏离、归因基准和下载股票池。当前文件的数值形态看起来合理：120 个调仓日、每日 500 只、权重合计约 100%。但产品级审查不能只接受“看起来合理”。缺少可复现来源时，无法排除静态成分、公告日错配、生效日错配、手工拼接错误或幸存者偏差。

**修复建议**：
1. 增加一个可复现的成分股权重下载或导入脚本，记录 API、参数、字段、时间范围。
2. 为 `csi500_index_weight_201601_202512.csv` 增加元数据文件，至少包含来源、生成时间、下载命令、文件 SHA256、字段单位、`trade_date` 的生效日解释。
3. 明确 `index_code=000905.SH` 仅用于成分和权重，收益基准仍为 `H00905.CSI` 全收益指数。
4. 增加测试：每个 `trade_date` 必须为当月最后一个交易日，每期 500 只，权重合计在 `[99.9, 100.1]`，且不得出现重复 `(trade_date, con_code)`。

**验证方式**：
- 从脚本重新生成同一文件后 SHA256 一致，或差异有供应商修订记录。
- `index_member.parquet` 的全部 `rebalance_date` 均能在 `daily_quote` 与 `index_quote` 交易日中找到。

### F2-004 下载脚本包含明文默认 Token 和非标准 API 地址

**严重度**：High

**证据**：
- `scripts/download_tushare.py:37` 在 `os.environ.get("TUSHARE_TOKEN", ...)` 的默认值中包含明文 Token。
- `scripts/download_supplement.py:47` 同样包含明文默认 Token。
- `scripts/download_supplement.py:48` 将 `API_URL` 指向非官方域名。
- `scripts/download_tushare.py:61` 和 `scripts/download_supplement.py:70` 直接覆盖 Tushare SDK HTTP 地址。

**影响**：
这是凭证管理和数据来源可信度问题。产品化研究不能把可用 Token 写进源码，也不能在没有数据供应商说明、版本记录和访问控制的情况下默认使用非标准 API 地址。它会影响安全、可审计性和数据一致性，也不利于多人复现。

**修复建议**：
1. 删除源码中的默认 Token，改为必须从环境变量或本地未入库配置读取。
2. 将 `.env`、本地凭证文件和日志中的敏感字段加入 `.gitignore`。
3. 旋转已经暴露过的 Token。
4. 对非官方 API 地址给出书面说明；如果只是本地代理，应默认关闭，并通过显式配置启用。

**验证方式**：
- `rg -n "TUSHARE_TOKEN|token|c9ad|api.tushare|tsdata" scripts src` 中不得再出现真实 Token。
- 缺少 `TUSHARE_TOKEN` 时下载脚本应直接失败并给出清晰错误，而不是静默使用默认凭证。

### F2-005 正式规则要求 6 位股票代码，但实现统一使用 `ts_code` 后缀格式

**严重度**：Medium

**证据**：
- `AGENTS.md:66` 要求股票代码统一为 6 位字符串。
- `PROJECT_PLAN_v1.1.md:210` 和 `PROJECT_PLAN_v1.1.md:502` 也要求 6 位股票代码格式。
- 实际所有核心表使用 Tushare `ts_code`，例如 `000006.SZ`。
- `scripts/build_data_quality_notebook.py:318` 的检查函数说明为“6 位代码 + 交易所后缀，如 000001.SZ”。

**影响**：
当前内部实现是相对一致的，所以这不是立即导致收益失真的问题。但它违反了书面硬规则，会在对接外部数据、报告导出、人工抽样核对和后续测试时造成口径冲突。如果继续使用 `ts_code` 是合理选择，必须修订硬规则；如果坚持 6 位代码，应把交易所后缀拆成单独字段。

**修复建议**：
1. 明确项目标准是 `symbol6` 还是 `ts_code`。
2. 若保留 Tushare `ts_code`，更新 `AGENTS.md` 和 `PROJECT_PLAN_v1.1.md`，并在数据字典中说明格式。
3. 若改成 6 位代码，必须增加交易所字段，避免未来接入指数、停牌和行业接口时丢失交易所信息。

**验证方式**：
- 数据字典、代码检查、所有 Parquet schema 对股票代码格式给出同一标准。

### F2-006 `daily_basic` 的自由流通市值字段存在少量缺失，当前校验没有拦截 NaN

**严重度**：Medium

**证据**：
- `scripts/csv_to_parquet.py:306-312` 用 `free_share * close * 10000` 计算 `free_float_mv` 和 `log_free_float_mv`。
- `scripts/csv_to_parquet.py:333` 只断言 `free_float_mv <= 0` 的数量为 0，没有检查 NaN。
- 只读抽查显示：
  - `daily_basic.parquet` 比 `daily_quote.parquet` 少 2 个行情键。
  - `free_share` 缺失率约 `0.0119%`。
  - `log_free_float_mv` 非有限值 320 行。

**影响**：
自由流通市值是中性化核心变量。少量缺失不一定会显著改变结果，但必须被显式记录并在因子预处理层可追踪，否则某些截面的中性化样本数、金融股剔除和回归残差会发生隐性变化。

**修复建议**：
1. 在转换阶段增加 `free_share`、`free_float_mv`、`log_free_float_mv` 的 NaN 率检查。
2. 对缺失自由流通股本的股票，明确策略：剔除、用最近可见值前向填充、或退化到 `circ_mv`。不能静默进入中性化。
3. 每个调仓日输出自由流通市值覆盖率，低于阈值时中断。

**验证方式**：
- 每个 `rebalance_date` 的可投资域中 `log_free_float_mv` 覆盖率达到预设阈值。
- 中性化函数遇到 `log_free_float_mv` 缺失时有可测试、可记录的处理路径。

### F2-007 财务三表 PIT 宽表合并会混合三张报表的修订版本，缺少源可用日追踪列

**严重度**：Medium

**证据**：
- `scripts/csv_to_parquet.py:571-572` 对单表只去重同一 `pit_date` 的重复行，保留不同 `pit_date` 的修订历史，这个方向正确。
- `scripts/csv_to_parquet.py:587-602` 随后把利润表、资产负债表、现金流量表按 `ts_code,end_date` 外连接，并把三张表的 PIT 日期取最大值作为合成记录 `pit_date`。
- `scripts/csv_to_parquet.py:604-610` 再按 `(pit_date, ts_code, end_date)` 去重，最终丢弃 `_pit_inc`、`_pit_bal`、`_pit_cf`。
- 只读抽查显示 `financial_pit.parquet` 中有 `192` 个 `(ts_code,end_date)` 存在多个 PIT 版本。

**影响**：
当前做法偏保守，未观察到直接未来函数：合成记录要等三表最大 `pit_date` 才可用。但它会把三张表不同修订版本的字段合并到一行，且保存后无法追溯每个字段来自哪张表、哪一天披露或修订。产品级 PIT 审计需要能解释“这个 T 日这个字段为什么可见”，当前宽表不够透明。

**修复建议**：
1. 在 `financial_pit.parquet` 中保留 `_pit_inc`、`_pit_bal`、`_pit_cf` 或等价的源可用日列。
2. 更严格的实现方式：查询 T 日时先分别对三张表执行 `pit_date <= T` 和最新版本选择，再横向合并快照，而不是在转换阶段提前合成所有版本笛卡尔组合。
3. 为至少 5 只股票、3 个调仓日输出字段级 PIT 审计样例，包含每个字段来源表和可用日。

**验证方式**：
- 对随机样本，任何字段的源表 `pit_date` 均 `<= T`。
- 同一 `(T, ts_code, end_date)` 的宽表字段能追溯到源 CSV 原始行。

### 阶段 2 通过项与保留意见

以下项目在本阶段只读抽查中未发现直接违规，但仍需要单元测试固化：

- 基准行情：`index_daily.csv` 仅含 `H00905.CSI`，`index_quote.parquet` 日期与股票交易日完全对齐，`nav` 与 `index_ret` 自洽。
- PIT 可见性：财务主表和指标表未发现 `pit_date <= end_date` 异常；随机抽样 12 个 `(T, 股票)` 的财务、指标、股东快照均满足可见性约束。
- 行业归属：`industry.parquet` 无未分类覆盖缺口，SW2021 口径与实际产物一致。
- 成分股数量和权重：`index_member.parquet` 每个调仓日 500 只，权重合计接近 100%。

但阶段 2 结论仍为不通过。`daily_quote.ret` 和 `stock_status` 覆盖问题会直接扭曲因子、归因、交易约束和回测结果，属于继续解释表现前必须修复的缺陷。

## 阶段 3：因子构建与预处理审查

> 审查日期：2026-05-19  
> 审查范围：`src/factors/financial_factors.py`、`src/factors/price_factors.py`、`src/factors/preprocess.py`、`scripts/build_factor_panels.py`、`data/processed/factor_panels/*.parquet`。  
> 明确未做：未运行因子重建脚本，未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未修改业务代码。

### 阶段 3 只读抽查摘要

- 当前落盘因子面板共 27 个，符合候选因子 20-30 个的约束。
- 现有面板日期范围为 2016-01-29 至 2022-12-30，共 84 个调仓日；未发现已落盘的 2023-2025 测试期因子面板。
- `src/factors` 与 `scripts/build_factor_panels.py` 中未发现 `shift(-...)` 进入因子构建路径。
- `preprocess_factor()` 的 MAD 去极值、标准化和中性化均在当期横截面执行；抽样检查显示因子面板截面均值接近 0、标准差为 1。
- 金融行业过滤抽查：15 个财务因子在 SW2021 银行/非银金融观测上全部为 NaN，未发现金融股财务因子残留。
- 抽样中性化检查：年度样本上因子与 `log_free_float_mv` 的最大绝对相关约为 `1e-15` 量级，行业内均值也在浮点误差量级。

### F3-001 波动、MAX 和 Amihud 等量价因子继承了错误的 `daily_quote.ret` 口径

**严重度**：Blocker

**证据**：
- 阶段 2 的 F2-001 已确认 `daily_quote.ret` 与 `close_adj` 分股票计算的后复权收益不一致，最大绝对差约 `0.17656`，差异超过 1 bp 的行数为 `3650`。
- `src/factors/price_factors.py:293-316`：`factor_vol_60d()` 使用 `dq["ret"]`。
- `src/factors/price_factors.py:335-378`：`factor_ivol_60d()` 使用 `dq["ret"]` 和 `index_quote.index_ret`。
- `src/factors/price_factors.py:386-408`：`factor_max_ret()` 使用 `dq["ret"]`。
- `src/factors/price_factors.py:450-479`：`factor_amihud()` 使用 `abs(dq["ret"]) / amount`。
- 现有 `data/processed/factor_panels/vol_60d.parquet`、`ivol_60d.parquet`、`max_ret.parquet`、`amihud.parquet` 均是在该收益口径下生成的。

**影响**：
这是阶段 2 数据缺陷向阶段 3 的直接传导。波动率、特质波动率、最大单日涨幅和 Amihud 非流动性会被错误收益污染，后续单因子检验、因子筛选、合成信号和优化权重都可能被扭曲。即使这些因子后续表现很好，也不能先解释为 alpha，必须先修复收益口径并重建因子。

**修复建议**：
1. 先修复 F2-001：`daily_quote.ret` 应由 `close_adj.groupby(ts_code).pct_change(fill_method=None)` 生成。
2. 删除或标记依赖旧 `ret` 的因子面板、评估报告和组合结果为失效产物。
3. 重建至少以下面板：`vol_60d`、`ivol_60d`、`max_ret`、`amihud`；如果下游使用过这些因子，也必须重建单因子检验、因子合成、优化和归因。
4. 增加测试：构造除权样本，确保 `vol_60d`、`max_ret`、`amihud` 使用后复权收益，而不是原始 `pct_chg`。

**验证方式**：
- 数据层测试通过：`ret == close_adj.groupby(ts_code).pct_change()`。
- 对一个已知除权异常样例重新计算 `max_ret`，确认不会取到 `pct_chg` 的异常跳变。

### F3-002 因子构建脚本允许直接扩展到测试期，缺少测试集纪律防护

**严重度**：High

**证据**：
- `scripts/build_factor_panels.py:8-9` 顶部说明默认范围到 2022，测试集严格不处理。
- 但 `scripts/build_factor_panels.py:155-157` 又实现了 `end_date > VALID_END` 的“测试集扩展模式”。
- `scripts/build_factor_panels.py:185` 注释举例为 `end_date=2025-12` 补算 `2023-01 ~ 2025-12`。
- `scripts/build_factor_panels.py:375-379` CLI 暴露 `--end-date`，帮助文本明确“测试集扩展用 2025-12-31”。
- 当前落盘面板仍只到 2022-12-30，这是通过项；问题在于脚本缺少运行次数、run id、审批记录和防误用机制。

**影响**：
单独计算测试期因子不一定等同于读取测试期收益标签，但它是测试期流水线的一部分。当前 CLI 可以绕开 `[TEST_SET_RUN_N]` 纪律直接生成 2023-2025 因子面板，容易造成“先补测试期因子、再多次局部评估”的灰区操作。对产品级研究，测试期相关产物必须有统一入口、统一日志和运行计数。

**修复建议**：
1. 默认禁止 `--end-date > cfg.VALID_END`，除非显式传入受控参数，例如 `--allow-test-set --run-id N`。
2. 将测试期因子扩展纳入 `check/test_set_run_log.md` 或统一测试流水线日志。
3. 脚本启动时读取 git 历史和运行日志，若测试集运行次数已达 2，直接拒绝。
4. 训练/验证面板与测试面板分目录存放，例如 `factor_panels_train_valid/` 与 `factor_panels_test_run_N/`，避免覆盖。

**验证方式**：
- 普通运行 `python -m scripts.build_factor_panels --end-date 2025-12-31` 应失败并提示测试集纪律。
- 合规测试运行必须生成带 `[TEST_SET_RUN_N]` 的日志记录和不可覆盖产物目录。

### F3-003 缺少因子预处理和时间边界单元测试

**严重度**：High

**证据**：
- `tests/` 目录目前只有 `tests/__pycache__/`，没有 `tests/test_preprocess.py`、`tests/test_factor_time_boundary.py` 或等价测试文件。
- `src/factors/preprocess.py:46-88` 的 MAD 去极值没有单元测试覆盖 MAD=0、全 NaN、NaN 保持、边界只用当期横截面。
- `src/factors/preprocess.py:130-179` 的中性化没有单元测试覆盖行业缺失、自由流通市值缺失、样本不足和残差正交性。
- `src/factors/price_factors.py:80-112` 的窗口定位没有测试证明只使用 `<= T` 的交易日。
- `src/data/pit_loader.py` 的 `make_ttm()` 虽属数据层，但财务因子强依赖它；当前也没有针对 TTM 的边界测试。

**影响**：
阶段 3 的核心风险不是代码能否跑，而是是否在每个 T 日只用当期及之前信息、横截面处理是否泄漏全样本统计量、金融股过滤和中性化是否稳定。没有测试时，后续任何因子增删或重构都可能引入未来函数或横截面处理错误，并且难以及时发现。

**修复建议**：
1. 新增 `tests/test_preprocess.py`：覆盖 MAD、标准化、中性化、金融股过滤、NaN 保持。
2. 新增 `tests/test_factor_time_boundary.py`：用人工行情和 PIT 数据验证 `ret_1m`、`mom_6_1`、`holder_chg`、`make_ttm` 不使用 T 之后数据。
3. 新增除权样例测试：确保波动/MAX/Amihud 使用后复权收益。
4. 新增面板 schema 测试：因子面板最大日期不得超过 `VALID_END`，除非处于受控测试集运行。

**验证方式**：
- `python -m pytest tests/test_preprocess.py tests/test_factor_time_boundary.py -q` 通过。
- 测试中至少包含一个会因 `shift(-1)` 或全样本 winsor 边界而失败的负例。

### F3-004 因子面板缺少可审计的预处理诊断产物

**严重度**：Medium

**证据**：
- `scripts/build_factor_panels.py:291-324` 对每个因子做预处理，失败时写入全 NaN 截面，但只通过日志记录。
- `scripts/build_factor_panels.py:347-355` 落盘时只保存因子值和整体有效值比例，没有保存逐期诊断。
- 只读抽查显示各因子有效值比例大约在 `31.8%` 至 `45.7%` 之间；由于列是全期股票并集，这个比例本身不能解释每期可投资域覆盖情况。
- 当前没有 `factor_panel_metadata`、`preprocess_diagnostics` 或等价文件记录每期原始覆盖率、金融股过滤数量、MAD 截断数量、中性化有效样本数、OLS 失败次数。

**影响**：
当某个因子在某期突然变成全 NaN、覆盖率下降或中性化被跳过时，后续评估只能看到最终面板，很难判断是数据缺失、PIT 陈旧、金融股过滤、自由流通市值缺失、行业缺失还是代码异常。产品级研究需要把这些诊断落盘，否则无法复现和解释因子质量变化。

**修复建议**：
1. 在构建面板时同步输出 `data/processed/factor_panel_diagnostics.parquet`。
2. 每个 `(rebalance_date, factor)` 至少记录：原始非空数、金融股过滤数、winsor 截断数、标准化有效样本数、中性化样本数、是否跳过中性化、最终非空数、异常信息。
3. 对关键阈值设硬门槛，例如最终有效样本数低于 300 或中性化样本数低于 30 时记录为 ERROR/WARN。

**验证方式**：
- 任意一个因子面板都能追溯每期预处理路径。
- 构造一个行业全缺失或 log_mv 全缺失样例，诊断文件必须记录中性化跳过原因。

### F3-005 因子数量文档与实际实现不一致

**严重度**：Low

**证据**：
- `src/factors/financial_factors.py:5` 写“实现 14 个财务因子”，但 `_FACTOR_BUILDERS` 实际包含 15 个严格 PIT 财务因子；`scripts/build_factor_panels.py:60-81` 也按 15 个财务 + 12 个量价统计。
- `src/factors/price_factors.py:5` 写“实现 11 个量价类因子”，但 `_PRICE_FACTOR_BUILDERS` 实际包含 12 个量价因子。
- `scripts/build_factor_panels.py:16` 和实际落盘文件均显示默认全部 27 个因子。

**影响**：
这不是直接的领域错误，但会降低审查和复现效率。因子数量是过拟合防范的一部分，文档和实现必须一致，尤其是在后续解释“为何 27 个候选因子不过多”时。

**修复建议**：
统一更新模块 docstring、脚本说明和研究计划中的因子数量表，并把非严格 PIT 的 `dy_ttm` 明确列为排除项。

**验证方式**：
- `len(FINANCIAL_FACTORS) + len(PRICE_FACTORS) == len(data/processed/factor_panels/*.parquet) == 27`。
- 文档中不再出现 14/11 的旧数量。

### 阶段 3 通过项与保留意见

本阶段没有发现财务因子主路径使用 `end_date` 替代公告可用日的直接证据。财务因子通过 `get_financial_pit_raw()`、`get_indicator_pit_raw()`、`make_ttm()`、`get_pit_latest()` 和 `get_roe_delta()` 获取 PIT 数据，方向符合 `pit_date <= T` 和 `end_date < T` 规则。

动量类 `ret_1m`、`mom_6_1`、`mom_12_1` 使用 `close_adj` 两端比值且窗口末端不晚于 T；这一路径比使用错误的 `ret` 累乘更稳健。现有面板也没有测试期落盘污染。

但阶段 3 结论仍为不通过。原因是 F3-001 直接污染多个量价因子，F3-002 留下测试集纪律旁路，F3-003 使未来函数和横截面处理无法形成自动回归保护。修复阶段 2 的收益口径后，阶段 3 相关因子面板必须重建。

## 阶段 4：单因子评价与统计严谨性审查
> 审查日期：2026-05-19  
> 审查范围：`src/evaluation/ic_analysis.py`、`src/evaluation/quintile_backtest.py`、`src/evaluation/shift_test.py`、`scripts/run_factor_evaluation.py`、`reports/factor_evaluation/*`、`data/processed/fwd_ret_panel.parquet`。  
> 明确未做：未运行 `scripts/run_factor_evaluation.py`，未重写报告，未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未修改业务代码。

### 阶段 4 只读抽查摘要

- `compute_forward_returns()` 的核心对齐是 `T+1` 开盘到下一调仓周期 `T'+1` 开盘，使用 `daily_quote.open_adj`。
- `scripts/run_factor_evaluation.py` 只取 `cfg.TRAIN_START` 到 `cfg.VALID_END` 的调仓日；现有 `data/processed/fwd_ret_panel.parquet` 为 `83 x 1065`，日期范围 `2016-01-29` 到 `2022-11-30`，未发现 2023-2025 行。
- 训练集 IC 使用 2016-2021，验证集使用 2022；forward return 按 `exit_date` 切分，避免 `2021-12-31` 的未来收益跨入 2022 后仍计入训练集。
- `ic_result.csv` 包含 `ic_mean`、`ic_std`、`ic_ir`、`t_stat`、`p_value`、`p_value_bh`、`significant_bh`、`effective`，多重检验使用 BH 校正。
- 当前报告目录在 `2026-05-19 09:26` 左右生成了 CSV/Markdown/PNG，但 `final_factors.json` 的时间戳是 `2026-05-19 09:13:52`，与其他评估产物不一致。

### F4-001 单因子评价结果继承了阶段 2/3 的收益口径污染

**严重度**：Blocker

**证据**：
- 阶段 2 已确认 `daily_quote.ret` 与后复权收益不一致；阶段 3 已确认 `vol_60d`、`ivol_60d`、`max_ret`、`amihud` 依赖该错误收益口径。
- `reports/factor_evaluation/factor_evaluation_report.md:86-92` 将 `ivol_60d`、`vol_60d`、`max_ret`、`amihud` 列为训练集有效因子。
- `reports/factor_evaluation/factor_evaluation_report.md:288-294` 又把 `ivol_60d`、`vol_60d`、`max_ret`、`amihud` 放入“最终纳入合成”的报告段落。
- 当前 `reports/factor_evaluation/final_factors.json` 仍包含 `amihud`。

**影响**：单因子评价阶段已经把错误收益口径污染的量价因子评为有效，并至少部分进入下游候选集合。任何基于这些报告的“因子有效性”结论都不能作为产品研究结论，也不能进入因子合成、优化或归因解释。

**修复建议**：
1. 先修复 F2-001，并重建 F3-001 指定的量价因子面板。
2. 删除或标记当前 `reports/factor_evaluation/*`、`final_factors.json`、`fwd_ret_panel.parquet` 为失效产物。
3. 重跑训练/验证期单因子评价后，再重新判断 `vol_60d`、`ivol_60d`、`max_ret`、`amihud` 是否有效。

**验证方式**：
- 重建后，`ic_result.csv` 和 `factor_summary.csv` 中所有依赖收益口径的因子必须来自修复后的后复权收益。
- 抽取除权样本，确认 `amihud`、`vol_60d`、`ivol_60d`、`max_ret` 的输入收益不再等于原始 `pct_chg`。

### F4-002 单因子评价报告、`factor_summary.csv` 与下游 `final_factors.json` 不一致

**严重度**：Blocker

**证据**：
- 只读抽查显示 `factor_summary.csv` 中 `final_include=True` 的因子数量为 `18`，而 `final_factors.json` 中 `final_factors` 数量为 `12`。
- `factor_summary.csv` 的最终因子包含：`ivol_60d`、`vol_60d`、`max_ret`、`roa`、`roe`、`roe_delta`、`short_ratio`；但这些不在 `final_factors.json` 的 `final_factors` 中。
- `final_factors.json` 包含 `mom_12_1`，但 `factor_summary.csv` 中 `mom_12_1` 的 `final_include=False`。
- `reports/factor_evaluation/factor_evaluation_report.md:282` 写“最终纳入合成：18 个”，而 `final_factors.json` 是下游 pipeline 实际读取的 12 个。
- 文件时间戳也不一致：`factor_summary.csv` 和 `factor_evaluation_report.md` 为 `2026-05-19 09:26:30`，`final_factors.json` 为 `2026-05-19 09:13:52`。
- 当前源码 `scripts/run_factor_evaluation.py:73-74` 定义 `HIGH_CORR_THRESHOLD=0.70`、`SHARPE_LS_THRESHOLD=0.50`；`scripts/run_factor_evaluation.py:540-548` 要求去冗余且方向化 Sharpe 达标。但现有 `factor_summary.csv` 中 `max_ret` 的 `directional_sharpe_ls=0.3807`、`short_ratio=0.4100` 仍被标为 `final_include=True`，且 `ivol_60d` 与 `vol_60d` 的相关系数约 `0.9339` 也同时被标为入选。

**影响**：这是产物一致性和可复现性缺陷。研究报告告诉研究员最终纳入 18 个因子，下游 JSON 告诉合成脚本使用 12 个因子，且两者与当前源码规则也对不上。后续任何合成信号、组合优化或归因都无法明确追溯“到底使用了哪一组因子”。

**修复建议**：
1. 把单因子评价输出改为原子写入：同一次 run 生成 `run_id`，所有 CSV/MD/JSON 写入同一带时间戳目录。
2. `final_factors.json` 中增加 `run_id`、`generated_at`、`git_commit`、`source_script_hash`、`factor_summary_hash`、`config_hash`。
3. 在报告生成后增加一致性断言：`factor_summary.final_include` 必须与 `final_factors.json.final_factors` 完全一致，否则直接失败。
4. 当前不应继续使用 `reports/factor_evaluation/` 下任何最终因子产物，直到重新生成并通过一致性校验。

**验证方式**：
- 写入 `tests/test_evaluation_artifacts.py`：读取 `factor_summary.csv` 与 `final_factors.json`，断言最终因子集合一致。
- 重跑训练/验证单因子评价后，确认报告、CSV、JSON 时间戳和 `run_id` 一致。

### F4-003 Forward return 缓存没有配置、日期、股票池和源码版本校验

**严重度**：High

**证据**：
- `scripts/run_factor_evaluation.py:90-103` 中 `load_or_compute_fwd_ret()` 只判断 `data/processed/fwd_ret_panel.parquet` 是否存在；存在就直接 `pd.read_parquet()`，没有校验日期范围、股票池、调仓频率、是否使用基准、代码版本或数据版本。
- 当前 `fwd_ret_panel.parquet` 是未跟踪产物，`git status` 显示 `?? data/processed/fwd_ret_panel.parquet`。
- 只读抽查显示该缓存为 `83 x 1065`，日期到 `2022-11-30`。如果后续修复数据层、更新可投资域、修改 T+1 对齐、启用基准超额模式或扩展验证边界，该缓存仍可能被静默复用。

**影响**：单因子评价的标签面板是统计检验的根基。缓存若与当前配置或源码不匹配，IC、分组收益、时间错位测试和最终因子筛选都会基于旧标签运行，而且报告不会暴露这一事实。

**修复建议**：
1. 为 `fwd_ret_panel.parquet` 配套写入 metadata，例如 `start/end`、`rebalance_dates_hash`、`codes_by_date_hash`、`benchmark_mode`、`data_version_hash`、`source_commit`。
2. 加载缓存前强制校验 metadata；不匹配则拒绝运行或要求显式 `--recompute-fwd-ret`。
3. 修复 F2-001、F2-002 后必须重算 forward return 缓存。

**验证方式**：
- 构造一个变更 `VALID_END` 或股票池 hash 的测试，缓存加载必须失败。
- 构造一个 `benchmark_code` 变化的测试，缓存不得被复用。

### F4-004 时间错位测试过于宽松，报告对“无未来函数疑点”的表述过强

**严重度**：High

**证据**：
- `src/evaluation/shift_test.py:56-58` 只做 `factor_panel.shift(1)`，即用上一期因子预测当期 forward return。
- `src/evaluation/shift_test.py:73` 只有在 `ic_ir_drop < -0.10` 时才给出 warning；如果错位后 IC_IR 几乎不下降，也不会报警。
- `reports/factor_evaluation/shift_result.csv` 中有 10 个因子的 `ic_ir_drop < 0.05`，例如 `gross_margin` 为 `0.0034`、`short_ratio` 为 `0.0123`、`holder_chg` 为 `0.0245`、`vol_60d` 为 `0.0383`；`leverage` 的 drop 为 `-0.0165`，但 warning 仍为 False。
- `reports/factor_evaluation/factor_evaluation_report.md:157` 写“所有因子时间错位测试正常（无未来函数疑点）”。

**影响**：时间错位测试只能作为辅助证据，不能替代 PIT 单元测试。当前报告把一个低灵敏度测试解释为“无未来函数疑点”，会给研究结论过度背书。对于上市级研究，这种表述不合格。

**修复建议**：
1. 把 shift test 拆成多档诊断：`lag_1`、`lag_2`、必要时 `lead_1` 负例检测，并区分“强下降”“弱下降”“反向增强”。
2. 对低持久性因子要求错位后显著衰减；对高持久性基本面因子允许低衰减，但报告必须解释原因，不能统一写“无未来函数疑点”。
3. 报告中改成保守表述：时间错位测试未发现反向增强，但仍需 PIT 单元测试确认。

**验证方式**：
- 构造一个故意使用未来收益的伪因子，shift/lead 诊断必须触发红色告警。
- 构造一个持久性极强的基本面因子，报告应给出“低衰减但可解释”的黄色诊断，而不是绿色通过。

### F4-005 缺少单因子评价层的单元测试

**严重度**：High

**证据**：
- `rg --files tests` 没有返回任何测试文件。
- 当前没有 `tests/test_evaluation.py` 或等价测试覆盖 `compute_forward_returns()`、`build_exit_date_map()`、`batch_ic_test()`、`run_shift_test()`、`run_quintile_backtest()`、报告/JSON 一致性。
- 阶段 4 计划明确要求未来收益对齐、shift test、IC 极端样例等测试。

**影响**：评估层是因子入选和下游组合构建的入口。没有测试时，T+1 对齐、训练/验证边界、BH 校正、方向化多空收益、去冗余规则和最终产物一致性都无法形成回归保护。

**修复建议**：
1. 新增 `tests/test_evaluation.py`，覆盖：
   - T+1 开盘到 `T'+1` 开盘的 forward return 对齐；
   - `build_exit_date_map()` 不让训练收益跨越到验证期；
   - `batch_ic_test()` 的 BH 校正和正负向有效因子；
   - `run_shift_test()` 对未来泄露伪因子的告警；
   - `run_quintile_backtest()` 的 Q5-Q1 与 DirectionalLS 方向；
   - `factor_summary.csv` 与 `final_factors.json` 的最终因子集合一致。
2. 把这些测试纳入每次评估报告生成前的最小门禁。

**验证方式**：
- `python -m pytest tests/test_evaluation.py -q` 通过。

### F4-006 分组回测报告使用原始 forward return，未明确区分全收益基准超额口径

**严重度**：Medium

**证据**：
- `src/evaluation/ic_analysis.py:193-218` 支持 `benchmark_code`，传入时可用 `index_quote.parquet` 的 `nav` 生成超额收益。
- `src/evaluation/ic_analysis.py:215` 的示例仍写 `000905.SH` 为中证500全收益指数；阶段 2 已确认当前全收益基准产物来自 `H00905.CSI`，该文档示例容易误导后续调用。
- `scripts/run_factor_evaluation.py:101` 调用 `compute_forward_returns(all_dates, all_codes, codes_by_date)` 时没有传 `benchmark_code`，因此当前 `fwd_ret_panel` 是原始个股 forward return。
- `src/evaluation/quintile_backtest.py:10-12` 说明分组回测不含交易成本且只是方向验证，但报告中展示的 `q1_ann_ret`、`q5_ann_ret`、`ls_ann_ret` 仍容易被误读为可交易或相对基准收益。

**影响**：对于 Rank IC 和 Q5-Q1 多空收益，扣除同一期全收益基准的截面常数通常不改变排序和多空差。但 Q1/Q5 绝对年化收益、累计收益图以及任何“收益水平”解释都不是全收益基准超额口径。产品研究报告必须明确区分，否则会和项目“必须使用中证500全收益指数”的硬规则产生解释冲突。

**修复建议**：
1. 报告中明确写明：IC/DirectionalLS 用于截面方向筛选，当前分组绝对收益为原始收益、不含成本，不代表相对中证500全收益指数的可交易超额。
2. 如需展示组别相对表现，另行生成 `benchmark_code="H00905.CSI"` 或内部标准基准代码的超额 forward return 报表，并注明基准 `nav` 为全收益口径。
3. 不要把单因子等权分组年化收益作为实盘策略收益或指数增强超额承诺。

**验证方式**：
- 报告中同时列出 raw 与 excess 两套标签或明确只使用其中一种，并在文件名或 metadata 中记录 `benchmark_mode`。

### 阶段 4 通过项与保留意见

本阶段未发现 `compute_forward_returns()` 在主路径中使用 `shift(-n)` 或直接把未来收益变量输入因子值的证据；当前 forward return 对齐设计为 T 日信号、T+1 开盘成交、T'+1 开盘退出。训练/验证切分也按 `exit_date` 过滤，方向是正确的。

统计字段层面，IC 报告具备 `IC_IR`、`t_stat`、`p_value` 和 BH 多重检验校正；最终筛选主逻辑使用训练集 IC、训练集 shift test、训练集分组 Sharpe 和训练集相关性去冗余，验证集主要用于稳健性观察，未发现直接用 2022 验证集调高最终权重的代码证据。

但阶段 4 结论仍为不通过。根因是：单因子评价继承了阶段 2/3 的错误收益污染；当前报告、CSV、JSON 互相矛盾；forward return 缓存缺少版本校验；时间错位测试的证据强度被报告过度解释。修复前，`reports/factor_evaluation/` 下的结果不能作为因子有效性或最终入选因子的可信依据。

## 阶段 5：信号合成审查
> 审查日期：2026-05-19  
> 审查范围：`src/signal/combiner.py`、`notebooks/03_factor_combination.ipynb`、`reports/factor_combination/`、`data/processed/composite_signal_equal.parquet`、`data/processed/composite_signal_ic_ir.parquet`、`data/processed/ic_series_all_factors.parquet`、`scripts/run_test_pipeline.py` 中的合成信号入口。  
> 明确未做：未运行 `notebooks/03_factor_combination.ipynb`，未运行 `scripts/run_test_pipeline.py`，未重建合成信号，未运行 2023-2025 测试集，未修改业务代码。

### 阶段 5 只读抽查摘要

- 当前落盘 `composite_signal_equal.parquet` 和 `composite_signal_ic_ir.parquet` 均为 `84 x 1065`，日期范围 `2016-01-29` 到 `2022-12-30`，没有 2023-2025 行。
- 当前 `ic_series_all_factors.parquet` 为 `83 x 12`，日期范围 `2016-01-29` 到 `2022-11-30`，列与 `reports/factor_evaluation/final_factors.json` 的 12 个因子完全一致。
- 合成信号每期截面均值约为 0、标准差为 1；`composite_signal_ic_ir` 在 CSI500 成分股上的覆盖率中位数约 `95.4%`，在 `tradable=True` 成分上的覆盖率中位数约 `98.0%`。
- `src/signal/combiner.py` 的滚动 IC_IR 主逻辑先取 `ic_s.index < before_date`，再 `.iloc[:-1]` 丢弃最近一期 IC，时间对齐方向是保守的。
- 当前 `reports/factor_combination/` 只有两张 PNG：`01_composite_ic_timeseries.png` 和 `02_icir_weight_heatmap.png`，没有 Markdown/CSV/Parquet 级别的信号合成审计报告。

### F5-001 合成信号使用的最终因子集合继承了阶段 4 的不一致产物

**严重度**：Blocker

**证据**：
- `data/processed/ic_series_all_factors.parquet` 的列为 12 个，完全等于 `reports/factor_evaluation/final_factors.json` 的 `final_factors`。
- 阶段 4 已确认 `factor_summary.csv` 中 `final_include=True` 的因子是 18 个，而 `final_factors.json` 是 12 个。
- 只读抽查显示当前信号实际使用了 `mom_12_1`，但 `reports/factor_evaluation/factor_summary.csv` 中 `mom_12_1` 的 `final_include=False`。
- 当前信号未使用 `factor_summary.csv` 中标记为 `final_include=True` 的 `ivol_60d`、`vol_60d`、`max_ret`、`roa`、`roe`、`roe_delta`、`short_ratio`。
- 当前信号使用了 `amihud`；阶段 3 已确认 `amihud` 依赖错误的 `daily_quote.ret` 口径。

**影响**：组合优化阶段读取的 alpha 信号已经建立在一个不可解释的因子集合上。研究报告、CSV、JSON 和合成信号之间无法回答“最终到底用了哪些因子、为什么用了这些因子”。这会直接污染阶段 6 组合优化、阶段 7 回测和阶段 8 归因。

**修复建议**：
1. 先修复阶段 2-4 的上游问题，并重新生成一致的 `final_factors.json`。
2. 合成信号构建前加入硬断言：`ic_series_all_factors.columns == final_factors.json.final_factors == factor_summary[final_include].index`。
3. 如果研究决策允许 JSON 与 `factor_summary.csv` 不一致，必须在同一 run 的审计报告中记录差异和原因，不能静默进入下游。
4. 修复收益口径后，重新评估 `amihud` 等受污染因子，再决定是否进入合成。

**验证方式**：
- 重建后读取 `ic_series_all_factors.parquet`、`final_factors.json`、`factor_summary.csv`，三者最终因子集合必须完全一致。
- 下游优化报告中记录的因子列表必须与合成信号审计报告一致。

### F5-002 测试流水线会把测试期合成信号和 IC 序列覆盖写入通用产物路径

**严重度**：Blocker

**证据**：
- `scripts/run_test_pipeline.py:6-7` 顶部说明测试流程会“重建合成信号（全期 2016-2025，120 期）并覆盖写入”。
- `scripts/run_test_pipeline.py:54-56` 把输出路径固定为：
  - `data/processed/composite_signal_ic_ir.parquet`
  - `data/processed/composite_signal_equal.parquet`
  - `data/processed/ic_series_all_factors.parquet`
- `scripts/run_test_pipeline.py:156` 的函数说明写明“全期覆盖”。
- `scripts/run_test_pipeline.py:188-193` 直接对上述通用路径执行 `to_parquet()`。
- `scripts/run_test_pipeline.py:467-502` 从全期因子面板取 `all_dates`，再构建全期合成信号。

**影响**：一旦运行测试流水线，训练/验证信号产物会被 2016-2025 全期信号覆盖，`ic_series_all_factors.parquet` 也会包含测试期收益标签计算出来的 IC 序列。即使 `compute_rolling_ic_ir()` 对每个 T 是时间保守的，通用文件名仍会把训练/验证产物和测试产物混在一起，破坏测试集纪律和审计可复现性。

**修复建议**：
1. 测试集相关信号必须写入独立目录，例如 `data/processed/test_run_1/composite_signal_ic_ir.parquet`。
2. 通用路径只允许保存训练/验证期产物；测试流水线不得覆盖。
3. `ic_series_all_factors.parquet` 拆分为 `ic_series_train_valid.parquet` 和 `ic_series_test_run_N.parquet`，后者必须带 `[TEST_SET_RUN_N]` run id。
4. 在测试流水线启动前检查目标路径，若会覆盖训练/验证产物则直接失败。

**验证方式**：
- 运行 dry-run 或单元测试时，测试集输出路径不得等于训练/验证输出路径。
- 测试流水线结束后，训练/验证信号文件的最大日期仍不得超过 `VALID_END`。

### F5-003 冷启动和等权路径的因子方向硬编码与训练集评估方向不一致

**严重度**：High

**证据**：
- `src/signal/combiner.py:43-78` 用 `FACTOR_DIRECTIONS` 维护等权和冷启动回退时的手工方向。
- `src/signal/combiner.py:76` 把 `margin_ratio` 写为 `+1`，注释为“待测，暂设正向”。
- 只读抽查 `reports/factor_evaluation/quintile_summary.csv` 和 `factor_summary.csv` 显示 `margin_ratio` 的训练集方向为 `-1`。
- `src/signal/combiner.py:131-133` 的 `_equal_weight_map()` 会直接使用该硬编码表。
- 当前 `compute_rolling_ic_ir()` 权重抽查显示，`composite_signal_ic_ir` 前 13 个调仓日所有因子 IC_IR 均不足，全部回退到等权；前 24 个调仓日存在部分因子 IC_IR 不足。

**影响**：`margin_ratio` 在等权合成和 IC_IR 冷启动期被反向使用。这个错误至少影响训练初期 13 期的主信号，也影响 `composite_signal_equal.parquet` 的全周期基准对照。更深层的问题是，合成阶段没有把训练集评估出的因子方向作为唯一来源，而是维护了另一份可能过期的手工表。

**修复建议**：
1. `FACTOR_DIRECTIONS` 不应作为生产信号方向来源；应从同一 run 的 `factor_summary.csv` 或 `final_factors.json` 写入并读取 `factor_direction`。
2. 对冷启动期，若方向无法从训练期确定，应明确选择“不生成信号”或只使用已确认方向的因子，不能靠“暂设正向”。
3. 在合成前校验：所有入选因子的方向必须非空，且与评估报告一致。

**验证方式**：
- 新增测试：构造一个训练集方向为 `-1` 的因子，冷启动等权合成必须按 `-1` 使用。
- 当前 `margin_ratio` 修复后，等权信号与冷启动期 IC_IR 信号应发生可解释变化。

### F5-004 缺少可审计的 IC_IR 权重历史和信号合成元数据

**严重度**：High

**证据**：
- 当前 `reports/factor_combination/` 只有两张 PNG，没有 `factor_combination_report.md`、`icir_weight_history.csv/parquet`、`signal_metadata.json` 或等价审计文件。
- `src/signal/combiner.py:279-288` 每期内部计算并应用滚动 IC_IR 和 `stability_weights`，但 `build_composite_panel()` 只返回合成信号面板，不返回每期每因子的实际权重。
- `notebooks/03_factor_combination.ipynb:1451-1456` 为画热力图重新计算权重，并且注释承认这只是可视化。
- 当前产物没有记录 `window_months`、`MIN_IC_HISTORY`、`min_valid_factors`、冷启动期数量、入选因子版本、`stability_weights`、输入 IC 序列 hash 或输出信号 hash。

**影响**：合成信号是优化器的 alpha 输入。没有每期实际权重和元数据，后续无法追溯某个调仓日为何某只股票 alpha 高、为何某个因子权重变号、冷启动期是否回退到等权、是否应用了稳定性降权。产品级研究不能只保留 PNG。

**修复建议**：
1. `build_composite_panel()` 增加可选 `return_diagnostics=True`，返回或落盘每期每因子的实际权重、冷启动标记、有效因子数分布。
2. 落盘 `data/processed/composite_signal_metadata.json`，记录配置、run id、因子列表、输入文件 hash、输出文件 hash。
3. `reports/factor_combination/` 增加 Markdown 审计报告，至少包括权重历史表、信号覆盖率、冷启动期、验证集表现和异常清单。

**验证方式**：
- 任意调仓日 `T` 都能从审计文件还原 `composite_signal_ic_ir.loc[T]` 的因子权重来源。
- 修改 `window_months` 或入选因子列表后，metadata hash 必须变化。

### F5-005 合成参数散落且不一致，`min_valid_factors` 不是集中配置

**严重度**：Medium

**证据**：
- `src/signal/combiner.py:36-38` 定义 `MIN_IC_HISTORY=12`、`DEFAULT_WINDOW_MONTHS=24`、`DEFAULT_MIN_VALID_FACTORS=8`。
- `notebooks/03_factor_combination.ipynb:314` 对真实数据设置 `min_valid_count = 5`。
- `scripts/run_test_pipeline.py:80` 也设置 `MIN_VALID_COUNT = 5`。
- 这些参数没有集中在 `src/config.py`，也没有在产物 metadata 中记录。

**影响**：`min_valid_factors` 会直接改变信号覆盖率和可投资股票的 alpha 缺失率。当前源码默认是 8，但 notebook 和测试流水线实际用 5；如果后续有人直接调用模块默认值，会得到不同信号。该参数属于研究决策，不应散落在 notebook 和脚本中。

**修复建议**：
1. 将 `MIN_IC_HISTORY`、`DEFAULT_WINDOW_MONTHS`、`MIN_VALID_FACTORS` 纳入 `src/config.py` 或统一的 strategy config。
2. notebook 和脚本只能读取配置，不允许重新定义。
3. 输出 metadata 必须记录实际使用的参数。

**验证方式**：
- 全项目 `rg "MIN_VALID|WINDOW_MONTHS|MIN_IC_HISTORY"` 不应出现多份生产参数定义。
- 合成信号 metadata 与配置文件一致。

### F5-006 Notebook 自检门禁过弱，负向改进仍被标记为全部通过

**严重度**：Medium

**证据**：
- `notebooks/03_factor_combination.ipynb:1777-1790` 输出“自检报告 — 03_factor_combination”，并给出“全部通过，合成信号可交付组合优化模块”。
- 同一输出显示：`ic_ir=0.771`、`equal=0.921`、`提升=-0.150`，即 IC_IR 加权合成弱于等权合成。
- `notebooks/03_factor_combination.ipynb:1850-1852` 对“IC_IR 加权 vs 等权对比”硬编码 `True`，只作为信息项，不形成 FAIL 门控。
- 当前 `final_factors.json`、`factor_summary.csv` 不一致以及 `amihud` 污染，notebook 自检没有拦截。

**影响**：该自检不足以作为产品级交付门禁。IC_IR 加权不一定必须总是优于等权，但当它显著弱于等权时，至少应触发风险提示和原因分析，而不是“全部通过”。更重要的是，它没有检查最终因子集合一致性和上游污染。

**修复建议**：
1. 自检拆分为硬门禁和信息项；“可交付组合优化模块”只能在硬门禁全部通过后输出。
2. 硬门禁至少包含：最终因子集合一致、无受污染因子、信号日期不超过授权范围、每期标准化正常、CSI500 tradable 覆盖率达标、权重历史可追溯。
3. IC_IR 加权弱于等权时，输出黄色警告并要求记录原因；若弱化超过预设阈值，应阻止自动交付。

**验证方式**：
- 构造 `ic_ir < equal` 且差距较大的样例，自检应返回 WARN 或 FAIL，而不是 PASS。
- 构造 `final_factors.json` 与 `factor_summary.csv` 不一致样例，自检必须失败。

### F5-007 缺少信号合成层单元测试

**严重度**：High

**证据**：
- 当前 `tests/` 下没有信号合成测试文件。
- `src/signal/combiner.py` 的关键逻辑没有测试覆盖：滚动 IC_IR 是否丢弃不可得最近一期、冷启动是否正确回退、等权方向是否来自评估结果、`min_valid_factors` 是否按股票逐行生效、NaN 是否保持、合成后截面标准化是否稳定。

**影响**：信号合成是从研究因子进入组合优化的最后一道口径转换。没有测试时，任何未来修改都可能引入当前期 IC 泄露、方向反转、覆盖率异常或错误填充 NaN，并直接传导到权重和回测。

**修复建议**：
1. 新增 `tests/test_signal_combiner.py`。
2. 覆盖以下用例：
   - `compute_rolling_ic_ir()` 不使用当前期和最近不可得期 IC；
   - 冷启动等权方向来自评估产物而不是硬编码默认；
   - `combine_factors_cross_section()` 对每只股票的 `min_valid_factors` 生效；
   - 合成后均值接近 0、标准差接近 1；
   - 信号输出日期不得超过授权范围；
   - signal artifact 与 final factor artifact 一致。

**验证方式**：
- `python -m pytest tests/test_signal_combiner.py -q` 通过。

### 阶段 5 通过项与保留意见

当前落盘合成信号没有 2023-2025 测试期行，这是重要通过项。`src/signal/combiner.py` 的滚动 IC_IR 主路径没有发现直接使用 `shift(-n)` 或包含当期 IC 的证据；它还额外丢弃最近一期 IC，时间可得性处理偏保守。合成信号当前截面标准化也正常，CSI500 tradable 股票覆盖率在抽查中较高。

但阶段 5 结论仍为不通过。原因是当前信号使用了阶段 4 不一致的最终因子 JSON，并含有上游污染因子；测试流水线会把测试期信号和 IC 序列覆盖到通用路径；冷启动和等权方向存在硬编码错误；合成过程缺少可审计权重历史、元数据和单元测试。修复前，`data/processed/composite_signal_ic_ir.parquet` 不能作为产品级 alpha 输入。

## 阶段 9：复现、测试与发布前清单

> 审查日期：2026-05-20  
> 审查范围：`README.md`、`requirements.txt`、`.gitignore`、`scripts/run_pipeline.py`、`scripts/run_test_pipeline.py`、`scripts/build_factor_panels.py`、`scripts/run_factor_evaluation.py`、`tests/`、`src/config.py`、`check/test_set_run_log.md`、当前 git 状态。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未重建 raw/processed/factor/portfolio/backtest/attribution 产物，未修改业务代码。  
> 详细审查文件：`check/12_repro_test_release_checklist.md`。

### 阶段 9 只读抽查摘要

- `README.md`、`requirements.txt`、`.gitignore`、`check/test_set_run_log.md` 已存在。
- `git ls-files data` 和 `git ls-files "*.csv" "*.parquet"` 当前均为 0，数据/Parquet 产物不再作为 tracked 文件出现。
- `git log --oneline --grep='\[TEST_SET_RUN_' --all` 当前只有 1 条：`80708d4 [TEST_SET_RUN_1] 测试集评估完成 (2023-2025)`。
- `python -m pytest tests/ -q` 当前为 `35 passed`，但出现 `.pytest_cache` 权限 warning。
- 当前测试只覆盖 PIT、交易成本、可投资域；尚未覆盖评价、信号、优化、回测引擎、指标、归因。
- 一键 pipeline 只覆盖到因子评价，且下载入口参数与脚本 CLI 不匹配；信号/组合/回测/归因仍依赖 notebook。

### F9-001 一键复现入口不是有效的 raw → processed → evaluation 流水线

**严重度**：Blocker

**证据**：
- `scripts/run_pipeline.py:48-80` 的阶段只有 `download`、`convert`、`quality`、`factors`、`evaluate`。
- `scripts/run_pipeline.py:52-54` 调用 `scripts.download_tushare` 时 `extra=[]`。
- `scripts/download_tushare.py:607-622` 没有命令参数时只打印文档并 `sys.exit(0)`；真正下载全量数据需要 `all`。
- `scripts/run_pipeline.py` 没有调用 `scripts.download_supplement`；`scripts/download_supplement.py:375-389` 也要求传入 `all` 才下载补充数据。
- `README.md:34-40` 的下载和转换命令缺少脚本要求的 `all` 或模块参数；`csv_to_parquet.py:1359-1367` 要求 positional `module`。
- `scripts/run_pipeline.py:61-68` 的 `quality` 阶段是 `module=None`，会被跳过。

**影响**：按 README 或 `python -m scripts.run_pipeline` 执行时，下载阶段可能以退出码 0“成功”但没有下载数据，补充数据不会被下载，CSV 转 Parquet 命令也缺少必需参数。当前入口无法证明项目能从 raw 数据重建 processed 数据。

**修复建议**：
1. 将 `download` 阶段拆成 `download_core` 和 `download_supplement`，分别调用 `scripts.download_tushare all` 和 `scripts.download_supplement all`。
2. README 同步写成 `python -m scripts.csv_to_parquet all`。
3. 未实现的 `quality` 阶段不应静默跳过；要么实现，要么要求显式 `--skip quality`。
4. 每阶段增加关键产物存在性检查。

**验证方式**：
- 在不运行测试集的前提下，执行 dry-run 或小样本 fixture pipeline，确认每个阶段 CLI 参数正确。
- 缺失核心产物时 pipeline 必须失败。

### F9-002 processed → 信号/组合/回测/归因没有脚本化发布链路

**严重度**：Blocker

**证据**：
- `scripts/run_pipeline.py:48-80` 只覆盖到 `evaluate`。
- `README.md:52-57` 要求手工运行 `notebooks/02_factor_evaluation.ipynb` 到 `notebooks/06_attribution.ipynb`。
- `scripts/` 下没有训练/验证期专用的信号、组合优化、回测、归因脚本。
- 当前唯一覆盖信号、优化和回测的脚本是 `scripts/run_test_pipeline.py`，但它面向 2023-2025 测试集。

**影响**：阶段 9 要求从 processed 数据重建因子、信号、组合、回测和归因。当前只能稳定重建到因子评价，后半段依赖 notebook 单元格执行顺序和人工判断，不满足发布前最小复现要求。

**修复建议**：
1. 增加训练/验证期脚本化入口：`run_signal_combination.py`、`run_portfolio_optimization.py`、`run_backtest.py`、`run_attribution.py`。
2. `scripts/run_pipeline.py` 增加这些阶段，并默认只覆盖 `TRAIN_START` 到 `VALID_END`。
3. notebook 改为读取脚本产物展示，不再承担生产产物生成职责。

**验证方式**：
- 不运行测试集时，`python -m scripts.run_pipeline --from-stage signal` 能生成训练/验证期信号、组合、回测、归因产物。

### F9-003 测试流水线仍会覆盖通用训练/验证产物路径

**严重度**：Blocker

**证据**：
- `scripts/run_test_pipeline.py:55-60` 将 forward return、信号、IC 序列和组合权重路径指向 `data/processed/` 通用文件名。
- `scripts/run_test_pipeline.py:158` 注释仍写“全期覆盖”。
- `scripts/run_test_pipeline.py:190-195` 直接写入通用合成信号和 IC 序列。
- `scripts/run_test_pipeline.py:342-343` 直接写入通用组合权重。
- 只有回测 NAV、metrics、trades、actual weights 使用 `_test` 后缀：`scripts/run_test_pipeline.py:64-70`。

**影响**：第二次测试集运行会把通用信号、IC 序列、组合权重和协方差缓存扩展为 2016-2025 全期产物。后续报告如读取通用路径，会混用测试期构建的产物，破坏测试集纪律。

**修复建议**：
1. 测试集输出全部写入 `data/processed/test_runs/run_N/`。
2. 测试流水线启动时禁止写入训练/验证通用路径。
3. 通用路径最大日期不得超过 `VALID_END`。

**验证方式**：
- 增加路径隔离单元测试：任何测试输出路径不得等于通用训练/验证路径。

### F9-004 测试集运行次数门禁不具备本地防重复能力

**严重度**：High

**证据**：
- `scripts/run_test_pipeline.py:464-471` 仅通过 git 历史统计 `[TEST_SET_RUN_N]` 次数。
- `scripts/run_test_pipeline.py:493-498` 只要求 `--run-id == completed + 1`。
- `check/test_set_run_log.md:24-31` 和 `check/test_set_run_log.md:45-53` 的预登记与防误用清单不会被脚本读取或强制执行。

**影响**：如果运行 `--run-id 2` 后尚未 commit，git 历史仍只有 Run #1，脚本会允许再次运行 `--run-id 2`。这会让“最多 2 次”的纪律在本地被绕过。

**修复建议**：
1. 脚本读取本地 ledger，确认 Run #2 已预登记且状态为 `pending`。
2. 运行开始即创建不可覆盖的 `RUN_STARTED.json`。
3. 若存在未提交的 Run #2 记录，禁止再次运行相同 run id。

**验证方式**：
- 模拟 git 历史只有 Run #1，第一次 `--run-id 2 --dry-run` 创建 ledger；第二次相同命令必须失败。

### F9-005 测试覆盖不足以作为发布前门禁

**严重度**：High

**证据**：
- 当前测试文件只有 `tests/test_pit.py`、`tests/test_transaction.py`、`tests/test_universe.py`。
- `python -m pytest tests/ -q` 为 `35 passed`，但未覆盖阶段 4-8 的关键风险。
- 未发现 `tests/test_evaluation.py`、`tests/test_signal_combiner.py`、`tests/test_optimizer.py`、`tests/test_backtest_engine.py`、`tests/test_backtest_metrics.py`、`tests/test_attribution.py`。
- 当前没有 `.github/`、`pytest.ini`、`pyproject.toml` 或等价 CI/测试配置。

**影响**：当前 pytest 通过不能证明收益口径、信号方向、fallback、T+1 成交、缺失状态、归因口径等核心问题已被防住。

**修复建议**：
1. 补齐评价、信号、优化、回测、指标、归因测试。
2. 每个阶段 2-8 的 Blocker 至少转化为一个回归测试。
3. 增加 CI 或本地测试门禁配置。

**验证方式**：
- `python -m pytest tests/ -q` 覆盖全部关键模块；构造已知失败样例时测试能失败。

### F9-006 产物缺少统一 run manifest 和输入版本校验

**严重度**：High

**证据**：
- `scripts/run_factor_evaluation.py:94-102` 加载或写入 `fwd_ret_panel.parquet` 时没有 metadata 校验。
- `scripts/run_factor_evaluation.py:565-583` 输出 CSV 和 `final_factors.json`，JSON 没有 `run_id`、`generated_at`、`git_commit`、输入 hash、配置 hash。
- `scripts/build_factor_panels.py:348` 直接写入因子面板 parquet，没有配套记录因子版本、输入数据版本、处理参数和样本期。
- 阶段 5/6/8 已分别确认合成信号、协方差/权重、归因产物缺少可追踪 metadata。

**影响**：无法回答报告对应哪个代码版本、哪批输入数据、哪些参数和哪个测试集运行状态。当前已经出现报告、CSV、JSON、Parquet 不一致，没有 run manifest 会阻断发布审查。

**修复建议**：
1. 增加统一 `run_manifest.json`，每次训练/验证流水线生成不可覆盖 run 目录。
2. 每个阶段 metadata 记录 `run_id`、生成时间、git commit、config hash、输入/输出 hash、样本期、测试集运行计数。
3. 加载缓存前校验 metadata，不匹配则拒绝读取或要求显式 `--recompute`。

**验证方式**：
- 修改关键配置后，旧缓存必须被拒绝或标记过期。

### F9-007 发布工作区仍含 tracked pyc 与 pytest cache 权限问题

**严重度**：Medium

**证据**：
- `.gitignore:16-19` 已忽略 `__pycache__/`、`*.py[cod]`、`.pytest_cache/`。
- `git ls-files "*__pycache__*" "*.pyc"` 仍返回 27 个 tracked pyc 文件。
- `git status` 当前显示 `src/__pycache__/config.cpython-313.pyc` 和 `src/signal/__pycache__/combiner.cpython-313.pyc` 被修改。
- 本次 `pytest` 出现 `.pytest_cache` 无法创建 cache 文件的权限 warning。

**影响**：不会直接改变策略收益，但发布包仍混入本地解释器缓存，并让测试输出带噪音。

**修复建议**：
1. 用非破坏性方式将 tracked pyc 移出版本控制，例如 `git rm --cached`，不删除本地文件。
2. 修复或清理 `.pytest_cache` 权限。
3. 发布前确认 `git status --short` 不再出现 pyc 和 pytest cache。

**验证方式**：
- `git ls-files "*__pycache__*" "*.pyc"` 无输出。
- `python -m pytest tests/ -q` 不再出现 `.pytest_cache` 权限 warning。

### 阶段 9 通过项与保留意见

通过项：

- `README.md`、`requirements.txt`、`.gitignore`、`check/test_set_run_log.md` 已补齐。
- 数据和 Parquet 文件当前没有作为 tracked 文件出现。
- 测试集历史计数当前为 1 次，与运行记录一致。
- `src/config.py` 已集中样本期、交易成本、行业源、核心研究参数和 `RANDOM_SEED`。
- 新增 PIT、成本、可投资域基础测试，且当前 `35 passed`。

保留意见：

这些工程补丁改善了基础卫生，但尚未形成发布级复现闭环。阶段 9 发布门禁应把阶段 2-8 的 Blocker/High 作为先决条件，而不是只检查 README、requirements 和基础 pytest 是否存在。
