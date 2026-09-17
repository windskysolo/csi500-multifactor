# 项目文件全图

> 用途：按文件找功能，或按功能找文件。比 FILE_GUIDE.md 更细，比 deep_dive/ 更快。
> 最后更新：2026-05-29

---

## 快速定位（按需求查）

| 我想要… | 去哪找 |
|---------|--------|
| 查冻结基线的回测结果 | `runs/train_valid/20260529_034947__frozen_baseline_icir_topn50_ew/reports/self_check.md` |
| 知道项目现在进展到哪了 | `docs/FILE_GUIDE.md` Part 1 |
| 读懂项目设计决策 | `docs/PROJECT_PLAN_v1.1.md` |
| 运行一个新实验 | `docs/RESEARCH_GUIDE.md` → `scripts/run_experiment.py` |
| 开发一个新因子 | `src/factors/` 下任意 `.py` + `docs/guides/improvement_guide.md` |
| 看单因子的测试报告 | `reports/factor_evaluation/ic_summary.csv` |
| 看哪些因子进了最终池 | `reports/factor_evaluation/final_factors.json` |
| 看已有 run 的回测结果 | `runs/train_valid/<run_id>/backtest/metrics_valid.parquet` |
| 看已有 run 的 NAV 曲线 | `runs/train_valid/<run_id>/backtest/nav_valid.parquet` |
| 查当前主基线是哪个 run | `registry/mainline.json` |
| 查挑战者列表和指标 | `registry/challengers.json` |
| 横向比较所有 run | `reports/experiment_board.md` 或运行 `compare_runs.py` |
| 确认测试集还剩几次 | `docs/logs/test_set_runs.json` |
| 理解某个模块怎么写的 | `docs/deep_dive/NN_<模块名>.md` |
| 查 SignalSpec/OptimizerSpec 有哪些字段 | `src/pipeline/contracts.py` |
| 找数据在哪里 | `src/config.py`（DATA_PROC 路径常量）|
| 调试优化器回退（L1/L2/L3）| `src/portfolio/optimizer.py` |
| 查交易成本怎么算的 | `src/backtest/transaction.py` |
| 看测试用例在哪 | `tests/test_<模块名>.py` |

---

## 根目录

```
500 improve/
├── README.md                  项目介绍 + 快速上手
├── CLAUDE.md                  AI 协作规范（命名/注释/安全/Git 规则）
├── AGENTS.md                  Agent 设计模式
├── MIGRATION_STATUS.md        数据迁移进度记录
├── requirements.txt           Python 依赖包列表
├── pytest.ini                 pytest 配置（测试路径、标记等）
├── conftest.py                pytest 入口配置（2026-05-28 新增）
├── .gitignore                 排除 data/、logs/ 等大文件目录
├── current work/              实验过程中的临时分析笔记（不入 git 主线）
│   ├── 5.28/                  2026-05-28 滚动窗口实验过程笔记
│   └── 5.29/                  2026-05-29 冻结基线 + 消融实验过程笔记
├── docs_new/                  AI 参考文档集（持续维护，见 docs_new/FILE_MAP.md）
│
├── src/                       核心业务逻辑（不改这里，除非改算法）
├── scripts/                   入口脚本（运行实验从这里进）
├── configs/                   实验配置文件（写 Spec 在这里）
├── tests/                     单元测试
├── docs/                      文档（指南、计划、深度解析、存档）
├── experiments/               历史实验归档
├── runs/                      每次 run 的完整产物（自动生成）
├── reports/                   汇总报告和比较表
├── registry/                  主基线 + 挑战者注册表
├── data/                      原始数据 + Parquet 缓存（不入 git）
├── logs/                      下载运行日志
└── check/                     临时诊断输出（审计用）
```

---

## src/ — 核心算法库

框架的心脏，一般只在新增或修改算法时改这里。

### src/config.py

项目全局配置中心，所有路径和常量的唯一来源。

```
主要内容：
  TRAIN_START / TRAIN_END        训练期：2012-01-01 ~ 2020-12-31
  VALID_START / VALID_END        验证期：2021-01-01 ~ 2022-12-31
  TEST_START / TEST_END          测试期：2023-01-01 ~ 2025-12-31
  DATA_RAW / DATA_PROC           原始 CSV 路径 / Parquet 缓存路径
  FACTOR_PANEL_DIR               因子面板 parquet 输出路径
```

---

### src/data/ — 数据加载

| 文件 | 干什么 |
|------|--------|
| `loader.py` | 统一 Parquet 读取接口。读行情、财务、行业、股票状态等；大表走列过滤，小表全量缓存 |
| `pit_loader.py` | PIT（Point-In-Time）数据提取。`make_ttm()` 将季报转 TTM；`get_pit_latest()` 按 `ann_date ≤ rebalance_date` 取最新值，防未来数据 |
| `universe.py` | 定义 `TradeState` 枚举（正常/停牌/涨停/跌停）；负责 CSI500 成分股的 ST、次新股、停牌过滤 |

---

### src/factors/ — 因子计算

每个文件产出一组因子。每个因子函数返回 `pd.DataFrame`（index=日期, columns=股票代码）。

| 文件 | 因子组 | 典型因子 |
|------|--------|---------|
| `financial_factors.py` | 价值、质量、成长（15 个）| `pb_ttm`, `pe_ttm`, `roe_ttm`, `roa_ttm`, `gross_margin`, `rev_yoy`, `eps_yoy`, `rev_acceleration` |
| `price_factors.py` | 动量、波动、流动性、资金流（15 个）| `ret_1m`, `mom_6_1`, `mom_12_1`, `vol_60d`, `ivol`, `turn_20d`, `amihud`, `margin_ratio` |
| `alt_factors.py` | 另类/情绪（12 个）| `hk_hold_ratio`, `analyst_eps_revision`, `pledge_ratio`, `analyst_cnt_chg` |
| `preprocess.py` | 因子预处理（不产出新因子）| MAD 去极值 → Z-score 标准化 → 行业中性化（申万一级）|

---

### src/evaluation/ — 单因子评价

| 文件 | 干什么 |
|------|--------|
| `ic_analysis.py` | Rank IC 测试。计算 IC 时序、IC_IR、t 统计量、BH 多重检验校正；`build_ic_history()` 输出 date×factor IC 历史矩阵 |
| `quintile_backtest.py` | 五分组等权回测。验证因子是否有单调分层效应 |
| `shift_test.py` | 时间位移测试。因子滞后一期后 IC 应该显著下降，否则警告未来数据泄漏 |
| `factor_health.py` | **因子健康监控**。基于 IC 历史矩阵和因子方向，计算 12/24/36m 滚动 IC_IR，输出 STABLE/WEAK/WARN/REVERSE/UNKNOWN 状态和 DETERIORATING 趋势预警 |

---

### src/signal/ — 信号合成

| 文件 | 干什么 |
|------|--------|
| `combiner.py` | 多因子合成。支持 `method="icir"`（IC_IR 加权）和 `method="ridge"`（Ridge 回归）。合成窗口严格 < T，无信息泄漏 |
| `ridge_decay.py` | 指数衰减 Ridge 信号合成（2026-05-28 新增）。`RidgeDecayCombiner` 继承 `RidgeCombiner`，对历史样本按半衰期指数降权；支持 hl=24/36/48m 等配置 |

---

### src/portfolio/ — 组合构建

| 文件 | 干什么 |
|------|--------|
| `optimizer.py` | CVXPY 优化器。目标：最大化信号得分 − 换手惩罚。约束：年化 TE ≤ target、行业偏离 ≤ 3%、个股偏离 ≤ 1.5%。三级回退：L1 精确 → L2 线性 → L3 TopN 等权 |
| `covariance.py` | 协方差矩阵估计。LedoitWolf 收缩，500 维情况下防止矩阵病态；含正定性修复 |

---

### src/backtest/ — 回测引擎

| 文件 | 干什么 |
|------|--------|
| `engine.py` | 回测主循环。T 日权重 → T+1 开盘成交。日度盯市、扣除成本、处理停牌和涨跌停限制。返回 NavResult（NAV/交易记录/持仓权重）|
| `metrics.py` | 绩效指标计算。年化收益、波动率、Sharpe、最大回撤、换手率、超额 IR 等 |
| `transaction.py` | 交易成本模型。印花税：2023-08-28 前 10bps/后 5bps；佣金：2.5bps；冲击成本：5-10bps |

---

### src/attribution/ — 绩效归因

| 文件 | 干什么 |
|------|--------|
| `brinson.py` | Brinson-Hood-Beebower 归因。按申万一级行业分解：配置效应 + 选股效应 + 交互效应 |
| `factor_attr.py` | 因子贡献归因。将超额收益回归到各因子暴露上，量化每个因子的贡献 |

---

### src/pipeline/ — 实验框架

| 文件 | 干什么 |
|------|--------|
| `contracts.py` | 数据类定义。`ExperimentSpec`（实验总描述）、`SignalSpec`（信号参数）、`OptimizerSpec`（优化器参数）、`BacktestSpec`（回测参数）、`RunContext`（run 运行时上下文）|
| `stages.py` | 阶段执行封装。`run_signal_stage`、`run_portfolio_stage`、`run_backtest_stage`、`write_self_check_md` 各自负责一个阶段的文件 I/O 和日志 |
| `artifacts.py` | 产物路径约定。`ArtifactLayout` 定义每个阶段的标准输出路径；`REQUIRED_SIGNAL_ARTIFACTS` 等校验清单 |
| `registry.py` | 主基线管理。`promote_run()` 执行晋升前检查（RUN_FINISHED + self_check + 产物完整）并写入 `registry/mainline.json` |
| `compare.py` | 多 run 横向比较。`build_board_from_registry()` 从 registry 读取并生成比较板；`IR_MIN`、`MDD_MAX_ABS`、`TO_MIN_PCT`/`TO_MAX_PCT` 硬阈值常量 |

---

## scripts/ — 入口脚本

### 数据下载与预处理

| 脚本 | 干什么 |
|------|--------|
| `download_tushare.py` | 从 Tushare Pro 下载：日行情、财务报告（PIT）、指标数据、成分股权重、行业映射 |
| `download_supplement.py` | 补充数据：停牌状态、涨跌停、融资融券余额、指数权重 |
| `download_alternative_data.py` | 另类数据：北向资金、分析师预期、股权质押、大股东减持 |
| `csv_to_parquet.py` | 将下载的 CSV 批量转为 Parquet（压缩 + 类型优化）|
| `build_factor_panels.py` | 批量计算所有因子并输出到 `data/processed/factor_panels/`；支持 `--allow-test-set` 选项 |

### 核心流水线

| 脚本 | 干什么 | 常用度 |
|------|--------|--------|
| `run_experiment.py` | **最常用**。按 Spec 文件驱动完整实验（signal→portfolio→backtest）。每次运行生成新的不可变 run 目录 | ★★★★★ |
| `run_pipeline.py` | 旧式全流程入口（训练/验证期，不含测试集）。运行前无需写 Spec | ★★★ |
| `run_test_pipeline.py` | 测试集评估（≤3次总计）。读取当前主基线 Spec 自动运行 | ★★★★ |
| `compare_runs.py` | 生成横向比较板（`experiment_board.csv` + `.md`）。支持 `--run-ids` 指定特定 run | ★★★★ |
| `promote_run.py` | 将某个 run 晋升为主基线。晋升前检查产物完整性 | ★★★ |

### 单阶段工具

| 脚本 | 干什么 |
|------|--------|
| `run_factor_evaluation.py` | 批量单因子评价（IC 测试 + 五分组 + 移位测试）；同时输出 4 个 IC 历史矩阵 parquet 供健康监控使用 |
| `run_factor_health.py` | **因子健康监控**。读取 `ic_history_final_research.parquet` 和 `factor_summary.csv`，生成 `health_snapshot.csv`、`health_report.md`、`health_panel.png` |
| `diagnose_piotroski.py` | **piotroski_f 专项诊断**。输出分期 IC_IR、7 个分项 IC、行业分层 IC；结论写入 `check/0529/piotroski_diagnosis.md` |
| `run_signal_combination.py` | 单独测试信号合成（不跑完整流水线）|
| `run_portfolio_optimization.py` | 单独测试组合优化器 |
| `run_backtest.py` | 单独回放回测（可复用已有权重）|
| `run_attribution.py` | 单独做绩效归因（Brinson + 因子归因）|

### 参数搜索（网格实验）

| 脚本 | 干什么 |
|------|--------|
| `run_optimizer_grid.py` | TE 目标 / 行业偏离 / 个股偏离网格搜索 |
| `run_turnover_lambda_grid.py` | 换手惩罚系数 λ 网格搜索（最终定参 λ=0.005）|

### 辅助工具

| 脚本 | 干什么 |
|------|--------|
| `test_set_ledger.py` | 测试集次数管理。`count_test_set_runs()`、`remaining_test_set_runs()`、`record_test_set_run()` |
| `probe_data_coverage.py` | 检查各数据表的时间覆盖范围 |
| `check_phase3_coverage.py` | 验证 Phase 3 另类数据是否完整 |
| `diagnose_optimizer.py` | 优化器回退统计（L1/L2/L3 各触发多少次）|
| `generate_backtest_report.py` | 生成回测报告 Markdown |
| `plot_validation_results.py` | 画验证期结果图表 |
| `save_version.py` | 保存当前版本快照（git commit + 产物哈希）|
| `build_data_quality_notebook.py` | 生成数据质量检查 Jupyter Notebook |

---

## configs/ — 实验配置

### configs/pipelines/ — Spec 文件（每个对应一次实验设计）

每个文件定义一个顶层 `SPEC = ExperimentSpec(...)` 变量，用 `run_experiment.py` 直接读取运行。

| 文件 | 实验内容 | 状态 |
|------|---------|------|
| `frozen_baseline_icir_topn50_ew.py` | ICIR加权 + TopN=50等权，**无 QP** | **冻结基线（永不修改；方法与下方 Ridge+QP 实验不同，IR 不可直接比较）** |
| `baseline_expanding_ridge_te6_lam0050.py` | 扩展窗口 Ridge + **QP**，TE=6%，λ=0.005 | **已晋升主线**（mainline.json，IR=0.408；消融/挑战者的对比基准）|
| `baseline_icir_te6_lam0050.py` | IC_IR 加权信号 + QP，TE=6%，λ=0.005 | 已完成（IR=0.402）|
| `challenger_rolling36_te6_lam0050.py` | 滚动 36 个月窗口 Ridge + QP | 已完成（IR=0.966）|
| `challenger_rolling48_te6_lam0050.py` | 滚动 48 个月窗口 Ridge + QP | 已完成（IR=1.489，当前最优）|
| `challenger_rolling60_te6_lam0050.py` | 滚动 60 个月窗口 Ridge + QP | 已完成（IR=1.176）|
| `challenger_decay_ridge_hl24_te6_lam0050.py` | 指数衰减 Ridge + QP，半衰期 24m | 已完成（IR=0.626，优于已晋升主线）|
| `challenger_decay_ridge_hl36_te6_lam0050.py` | 指数衰减 Ridge + QP，半衰期 36m | 已完成（IR=0.295，低于已晋升主线，待诊断）|
| `challenger_decay_ridge_hl48_te6_lam0050.py` | 指数衰减 Ridge + QP，半衰期 48m | 已完成（IR=0.289，低于已晋升主线，待诊断）|
| `ablation_no_piotroski_f.py` | 消融：剔除 piotroski_f | 已完成（IR=0.524，**优于主线 0.408**；确认 piotroski_f 为 REMOVE_CANDIDATE；run_id=20260529_054632）|
| `ablation_no_high_52w_v2.py` | 消融：剔除 high_52w_v2 | 已完成（IR=0.423，持平主线；high_52w_v2 保留）|
| `ablation_no_piotroski_high52w.py` | 消融：同时剔除 piotroski_f + high_52w_v2 | 已完成（IR=0.430，中间值；两者同剔无额外收益）|

**命名规范**：`<类型>_<核心变量>_te<TE档位>_lam<lambda×1000>`

---

## tests/ — 单元测试

按模块对应。

| 测试文件 | 覆盖内容 |
|---------|---------|
| `test_pit.py` | PIT 对齐：ann_date ≤ 再平衡日，确保财务数据无未来泄漏 |
| `test_preprocess.py` | MAD 去极值、Z-score、行业中性化的数值正确性 |
| `test_signal_combiner.py` | 多因子合成：IC 窗口验证，NaN 处理 |
| `test_evaluation.py` | IC 计算：Rank IC 与 pandas rankdata 结果一致 |
| `test_technical_factors.py` | 价格因子数值正确性（动量、波动、流动性）|
| `test_optimizer.py` | 优化器约束满足、L1→L2→L3 回退逻辑 |
| `test_backtest_engine.py` | T+1 成交逻辑、停牌处理、成本扣除、NAV 计算 |
| `test_backtest_metrics.py` | Sharpe、最大回撤、换手率等指标数值 |
| `test_transaction.py` | 印花税切换（2023-08-28）、佣金计算 |
| `test_attribution.py` | Brinson 分解、因子贡献归因 |
| `test_universe.py` | ST 过滤、次新股过滤、停牌状态判断 |
| `test_alt_data_pit.py` | 另类数据（北向、分析师等）PIT 对齐 |
| `test_pipeline_contracts.py` | ExperimentSpec 解析、period_scope 校验、allow_test_set 规则 |
| `test_pipeline_artifacts.py` | 产物路径约定、sha256 校验 |
| `test_pipeline_registry.py` | promote_run 流程、mainline.json 写入 |
| `test_pipeline_release_gates.py` | 发布门：产物完整性检查 |
| `test_test_pipeline_isolation.py` | 测试集与训练集隔离（不共享路径）|
| `test_factor_time_boundary.py` | 因子时间边界（月底数据，不超前）|
| `test_phase4_factors.py` | Phase 4 新增因子的入门验证 |
| `test_phase5_factors.py` | Phase 5 新增因子的入门验证 |
| `test_hk_hold_coverage.py` | 北向持股数据覆盖范围 |
| `test_index_quote_code.py` | 指数行情代码格式校验 |
| `test_data_expansion_config.py` | 另类数据配置覆盖 |
| `test_research_workflow.py` | 实验工作流端到端测试（Spec→Run→产物校验）|
| `test_ridge_decay.py` | `RidgeDecayCombiner` 核心逻辑（14 个测试，含指数衰减权重正确性）|
| `test_factor_health.py` | 因子健康监控：滚动 IC_IR、状态判定、趋势预警 |
| `test_new_factors_p0p1.py` | 阶段7 P0+P1 新因子（accrual、asset_growth、share_issuance、mf_flow_ratio）入门验证 |

---

## docs/ — 文档库

### 核心文档（最常用）

| 文件 | 干什么 |
|------|--------|
| `FILE_GUIDE.md` | **进展快照**：当前最优 IR、已用测试次数、已知问题、里程碑路径 + 文件索引 |
| `FILE_MAP.md` | **本文件**：完整的文件结构图，按功能找文件用 |
| `RESEARCH_GUIDE.md` | **研究工作流**：从写 Spec 到晋升主基线的完整操作手册 |
| `PROJECT_PLAN_v1.1.md` | **权威决策文档**：日期切分、指标阈值、风险登记表（18 项）、硬编码约定 |
| `PROJECT_STRUCTURE.md` | 目录职责边界说明（比本文更高层）|
| `PIPELINE_MAP.md` | 数据流图：各阶段的输入/输出依赖关系 |
| `MIGRATION_STATUS.md`（根目录）| 数据迁移进度 |

### docs/deep_dive/ — 模块精读（18 篇）

按编号顺序阅读，每篇对应一个模块，含代码走读和关键设计说明。

```
00_reading_guide.md     阅读路线图（从哪篇开始）
01_config.md            配置系统
02_data_loader.md       数据加载与缓存
03_pit_loader.md        PIT 数据处理原理
04_universe.md          股票池与状态管理
05_financial_factors.md 财务因子实现
06_price_factors.md     价格因子实现
07_preprocess.md        因子预处理流程
08_ic_analysis.md       IC 测试框架
09_quintile_backtest.md 五分组回测
10_shift_test.md        移位测试（防未来泄漏）
11_combiner.md          多因子信号合成
12_covariance.md        协方差估计
13_optimizer.md         组合优化器
14_backtest_engine.md   回测引擎
15_transaction.md       交易成本
16_metrics.md           绩效指标
17_brinson.md           Brinson 归因
18_factor_attr.md       因子贡献归因
```

### docs/guides/ — 操作指南

| 文件 | 干什么 |
|------|--------|
| `project_explainer.md` | 新人入门：整体策略说明（非技术性）|
| `detailed_pipeline_guide.md` | 全流程操作手册（步骤级）|
| `improvement_guide.md` | 如何提升 IR：因子/信号/优化器方向 |
| `factor_evaluation_guide.md` | 因子测试最佳实践 |
| `results_interpretation.md` | 如何读回测报告和归因报告 |
| `china_ashare_factor_library.md` | A 股因子库参考（行业中性化、PIT、成本等特殊处理）|

### docs/design/ — 系统设计文档

| 文件 | 干什么 |
|------|--------|
| `backtest_design.md` | 回测引擎架构设计 |
| `experiment_framework_migration_plan.md` | 实验框架重构路线图 |
| `experiment_framework_refactor.md` | 重构实施说明 |

### docs/research/ — 研究规划

```
factor_roadmap/
  factor_research_guide.md     ← 核心维护文档：18 因子健康状态 + 缺口 + 优先级
  factor_catalog.md            因子完整目录（含未实现的）
  factor_implementation_plan.md 实现计划
  consensus_factor_plan.md     共识因子研究计划
  momentum_sprint_plan.md      动量因子冲刺计划

data_expansion_plan/
  readiness_check.md           另类数据就绪检查
  phase_plan.md                分阶段扩展计划
  execution_log.md             执行记录
  current_status_next_steps.md 当前状态与后续步骤
  alt_factors_implementation.md 另类因子实现说明

improve/
  ir_diagnosis_report.md       IR 诊断报告（为什么 IR 低）
  ir_improvement_phase_plan.md  IR 改善分阶段计划
  factor_engineering_plan.md   因子工程计划
  optimizer_diagnosis.md       优化器问题诊断
  rolling_window_analysis.md   滚动窗口分析
  progress_log.md              阶段进展日志（最新动态）
```

### docs/logs/ — 测试集记录

| 文件 | 干什么 |
|------|--------|
| `test_set_runs.json` | **权威计数器**：机器可读，`test_set_ledger.py` 读写此文件 |
| `test_set_run_log.md` | 人类可读的测试集使用记录 |

### docs/archive/ — 历史存档

```
audit_plan_20260521/    2026-05-21 五日全面审计（18 项问题）的计划和报告
check/                  各阶段逐步修复记录（阶段 0 ~ 9）
```

---

## experiments/ — 历史实验归档

### 版本里程碑

> **注意**：`experiments/v1.x` 是早期版本归档，**不反映当前最优状态**。
> 当前实验通过 `runs/` 框架管理，最优 run 见 `CLAUDE.md §1.1` 或 `registry/challengers.json`。
> 当前因子池 **17 个**（非下表中的 18 个）；最优验证期 IR=**1.489**（challenger_rolling48）。

| 目录 | IR | 关键变化 |
|------|----|---------|
| `v1.0/` | −0.11 | 初始版本，6 因子，优化器有 Bug |
| `v1.1/` | 0.483 | Ridge v2 + λ=0.005，16 因子 |
| `v1.2/` | 0.503 | + `rev_acceleration` + `high_52w_v2`，18 因子（历史存档）|

每个版本目录内：`manifest.json`（参数快照）、`results/`（Parquet 结果）、`reports/`（评估报告）、`README.md`（说明）

### experiments/legacy/ — 参数探索实验（已结束）

| 目录 | 内容 |
|------|------|
| `ridge_signal/` | Ridge 信号合成初始实现 |
| `ridge_rolling/` | 滚动窗口 vs 扩展窗口对比 |
| `te_grid/` | TE 目标从 4% 到 8% 的网格实验 |
| `turnover_lambda_grid/` | λ 从 0.001 到 0.010 的网格（最终选 λ=0.005）|

### experiments/notebooks/ — 探索性 Notebook

6 个阶段性 Jupyter Notebook（数据质量→因子评价→信号合成→组合优化→回测→归因）

---

## runs/ — Run 产物目录

每次 `run_experiment.py` 运行后自动生成，目录名格式：`YYYYMMDD_HHMMSS__<experiment_id>`

### 单个 run 目录的完整结构

```
runs/train_valid/<timestamp>__<experiment_id>/
  ├── run_config.json               本次实验的完整 Spec 快照（JSON）
  ├── inputs.lock.json              输入信号的 sha256（用于追溯复现）
  ├── manifest.json                 所有 parquet 产物的哈希校验清单
  ├── RUN_FINISHED.json             成功完成标志（不存在 = 未完成或失败）
  ├── RUN_FAILED.json               失败标志（包含错误信息）
  ├── signal/
  │   ├── composite.parquet         合成信号面板（index=日期, columns=股票）
  │   ├── coef_history.parquet      Ridge 每期系数历史（ridge 模式专有）
  │   └── signal_metadata.json      信号元信息（alpha、窗口、方法）
  ├── portfolio/
  │   ├── target_weights.parquet    优化后目标权重
  │   ├── baseline_weights.parquet  基准权重（Brinson 归因用）
  │   └── optimizer_meta.parquet    L1/L2/L3 回退次数统计
  ├── backtest/
  │   ├── metrics_valid.parquet     验证期绩效指标汇总（IR、回撤、换手等）
  │   ├── nav_valid.parquet         每日 NAV 曲线（超额 + 绝对净值）
  │   ├── trades_valid.parquet      每期交易记录（买入/卖出/成本）
  │   ├── actual_weights_valid.parquet   每日实际持仓权重
  │   ├── trades_baseline.parquet        基准交易记录
  │   └── actual_weights_baseline.parquet
  └── reports/
      └── self_check.md             自动生成：指标摘要 + PASS/FAIL 标记
```

### runs/test/ — 测试集 run

结构与 `train_valid/` 相同，但物理隔离在不同目录，且 `run_id` 由 `run_test_pipeline.py` 统一管理（`test_run_N__<timestamp>__<experiment_id>`）。

---

## reports/ — 汇总报告

### 因子评价报告

| 目录 | 内容 |
|------|------|
| `factor_evaluation/` | **主线因子评价**（18 个最终入模因子）。`ic_summary.csv`（IC 指标）、`final_factors.json`（最终因子池）、`factor_correlation.csv`（相关性矩阵）|
| `factor_evaluation_factor_impl_stage1/` | 阶段 6 新因子独立评价结果（不覆盖主线）|
| `factor_evaluation_momentum_sprint/` | 动量因子冲刺评价结果 |

### 参数搜索结果

| 目录 | 内容 |
|------|------|
| `optimizer_grid/` | TE/行业/个股约束网格实验结果 |
| `turnover_lambda_grid/` | λ 网格实验结果（`results/lam_0050/` 对应 IR=0.503 的最优参数）|

### 顶层报告文件

| 文件 | 内容 |
|------|------|
| `experiment_board.csv` | 所有注册 run 的横向比较数据（由 `compare_runs.py` 生成）|
| `experiment_board.md` | 同上，Markdown 格式 |
| `processed_coverage_report.md` | 数据完整性检查报告 |
| `analysis_v2_results.md` | V2 结果分析摘要 |

---

## registry/ — 注册表

| 文件 | 干什么 | 谁写入 |
|------|--------|--------|
| `mainline.json` | 当前主基线：`active_run_id`、晋升时间、晋升理由 | `promote_run.py` |
| `challengers.json` | 挑战者列表：`name`、`run_id`、`status`、`metrics_v2`、`notes` | 手动编辑 |
| `archived_promotions.jsonl` | 历史晋升追加日志（每行一条，不覆盖）| `promote_run.py` |

---

## 其他目录

### data/ — 数据（不入 git）

```
data/raw/           Tushare 下载的原始 CSV（按表名分文件）
data/processed/     Parquet 缓存
  daily_quote.parquet         日行情
  financial_pit.parquet       财务报告（PIT 对齐）
  indicator_pit.parquet       财务指标（PIT 对齐）
  factor_panels/              各因子的面板数据
    <factor_name>.parquet
  factor_panels_test_run_N/   测试集专用因子面板（run_test_pipeline 用）
```

### logs/ — 运行日志

```
download.log            主下载日志
download_main.log       行情/财务主表下载
download_supplement.log 补充数据下载
download_alt.log        另类数据下载
phase3_*.log            Phase 3 另类数据分阶段日志
```

### check/ — 临时诊断输出

```
0526/     2026-05-26 的审计输出（产物漂移报告、manifest 对比）
0527/     2026-05-27 的诊断输出（迁移前快照）
0529/     2026-05-29 的专项诊断输出（piotroski_f 分期 IC_IR、7 分项 IC、行业分层；结论文件 piotroski_diagnosis.md）
```

存放临时对比文件，不作为长期参考，定期清理。
