# 项目结构初读清单

> 生成日期：2026-05-19。
> 本文件只记录结构层面的初读结果，不等同于代码语义审计结论。

## 1. 已读取的结构来源

- 根目录文件：`AGENTS.md`、`CLAUDE.md`、`PROJECT_PLAN_v1.1.md`、`DONE.md`、`backtest_design.md`。
- 代码目录：`src/`、`scripts/`。
- 实验与报告目录：`notebooks/`、`reports/`。
- 数据目录：`data/raw/`、`data/processed/`。
- 测试目录：`tests/`。

## 2. 顶层目录概览

| 路径 | 文件数 | 初读说明 |
|---|---:|---|
| `.claude/` | 1 | 本地会话/工具设置。 |
| `data/` | 17746 | 原始 CSV、处理后 Parquet、因子面板、协方差缓存等，规模很大。 |
| `logs/` | 3 | 下载日志。 |
| `notebooks/` | 12 | 质量报告、单因子、合成、优化、回测、归因等 notebook 与问题记录。 |
| `process/` | 0 | 当前为空。 |
| `reports/` | 16 | 因子评估、合成结果、分析结果与图片。 |
| `scripts/` | 15 | 下载、CSV 转 Parquet、因子面板构建、评估与测试期流水线。 |
| `src/` | 53 | 核心研究与工程模块，含 `__pycache__`。 |
| `teach/` | 0 | 当前为空。 |
| `tests/` | 0 | 当前未发现测试文件。 |

## 3. 核心源码结构

`src/config.py` 已集中定义路径、样本期、交易成本、行业体系、金融行业代码、新股过滤、随机种子等关键参数。

源码模块当前按以下结构组织：

- `src/data/`
  - `loader.py`：Parquet 加载、PIT snapshot、调仓日和各类数据读取。
  - `pit_loader.py`：PIT 可见性过滤、TTM、最新可用财务数据、ROE delta。
  - `universe.py`：可投资域、停牌/涨跌停状态、交易日。
- `src/factors/`
  - `preprocess.py`：MAD 去极值、标准化、行业和自由流通市值中性化、金融股处理。
  - `financial_factors.py`：价值、质量、成长、杠杆、应计等财务因子。
  - `price_factors.py`：动量、波动、残差波动、换手、Amihud、两融、资金流等价量因子。
- `src/evaluation/`
  - `ic_analysis.py`：Rank IC、未来收益、批量 IC、相关性、冗余过滤。
  - `quintile_backtest.py`：分组回测。
  - `shift_test.py`：时间错位测试。
- `src/signal/`
  - `combiner.py`：滚动 IC_IR、等权、横截面合成、合成信号面板。
- `src/portfolio/`
  - `covariance.py`：Ledoit-Wolf 协方差估计、正定修复、批量估计。
  - `optimizer.py`：cvxpy 优化、行业/个股/停牌/涨跌停约束、L1/L2/L3 fallback。
- `src/backtest/`
  - `transaction.py`：印花税和交易成本。
  - `metrics.py`：收益、波动、Sharpe、IR、TE、最大回撤、超额回撤、胜率。
  - `engine.py`：T+1 执行、价格面板、交易状态、调仓执行、主循环。
- `src/attribution/`
  - `brinson.py`：行业 Brinson 归因。
  - `factor_attr.py`：因子归因。

## 4. 脚本结构

- `scripts/download_tushare.py`：Tushare 主数据下载。
- `scripts/download_supplement.py`：股东、分红、两融、资金流等补充数据下载。
- `scripts/csv_to_parquet.py`：原始 CSV 转处理后 Parquet，含后复权价、自由流通市值、PIT、行业、状态、成分股。
- `scripts/build_factor_panels.py`：批量构建因子面板。
- `scripts/run_factor_evaluation.py`：单因子评估、IC、分组、相关性、报告。
- `scripts/run_pipeline.py`：主流水线。
- `scripts/run_test_pipeline.py`：测试期流水线。此脚本涉及 2023-2025 测试集，审查期间默认禁止运行，除非用户明确授权并记录测试集运行次数。
- `scripts/build_data_quality_notebook.py`：生成数据质量 notebook。

## 5. Notebook 与报告结构

Notebook 当前包括：

- `01_processed_data_quality_report.ipynb`
- `02_factor_evaluation.ipynb`
- `03_factor_combination.ipynb`
- `04_portfolio_optimization.ipynb`
- `05_backtest.ipynb`
- `06_attribution.ipynb`
- `bug.md`
- `factor_quality_issues.md`
- `factor_quality_fix_plan.md`

报告当前包括：

- `reports/factor_evaluation/`：IC、分组、相关性、最终因子、Markdown 报告和图片。
- `reports/factor_combination/`：合成信号相关图片。
- `reports/analysis_v2_results.md`：阶段结果说明。

## 6. 数据资产结构

`data/raw/` 当前按股票或交易日组织：

| 子目录 | 文件数 |
|---|---:|
| `adj_factor/` | 1264 |
| `daily_basic/` | 1264 |
| `daily_quote/` | 1264 |
| `dividend/` | 1264 |
| `financial_balance/` | 1264 |
| `financial_cashflow/` | 1264 |
| `financial_income/` | 1264 |
| `financial_indicator/` | 1264 |
| `holder_number/` | 1264 |
| `limit_list/` | 2430 |
| `margin/` | 1264 |
| `moneyflow/` | 1264 |
| `suspend/` | 1264 |

`data/processed/` 当前包含核心 Parquet、回测/归因产物，以及：

- `factor_panels/`：27 个因子面板。
- `cov_cache/`：81 个协方差缓存文件。

## 7. 初步结构风险

1. `tests/` 为空。项目规范要求 `test_pit.py`、信号时间错位测试、成本测试、涨跌停/停牌边界测试、收益与回撤极端场景测试；当前结构层面未满足。
2. 根目录初读未发现 `.gitignore`。`PROJECT_PLAN_v1.1.md` 明确要求排除 `data/raw/`、`data/processed/`、`*.parquet`、`*.csv`、`__pycache__/` 等，但 `git status` 显示大量数据文件、缓存和 notebook 处于新增或修改状态。这是复现和版本管理的高风险点。
3. 数据源描述存在文档不一致：会话规范和代码实际使用 Tushare Pro；`PROJECT_PLAN_v1.1.md` 的部分旧章节仍写 CSMAR。后续审查必须先统一项目事实口径，并提示更新计划文档。
4. 实际目录与 `PROJECT_PLAN_v1.1.md` 的理想结构不完全一致：缺少 `README.md`、`requirements.txt`、`src/viz/`、`src/data/cleaner.py`、若干计划中的分拆因子模块；但现有代码以合并模块形式实现，是否接受需要按可维护性和复现性审查。
5. `scripts/run_test_pipeline.py` 明确存在测试期流水线。全面审查时必须防止无意运行测试集，避免污染测试集纪律。
6. Notebook 有修改状态，需审查 notebook 结果是否与 `src/` 模块和落盘产物一致，避免“notebook 能跑但模块不可复现”。
7. `__pycache__`、`.pytest_cache`、处理后数据和报告图片混在工作区状态中，说明版本库卫生需要作为第一阶段审查项。

