# 00 — 阅读指南

> **本目录定位：代码审查地图，而非入门教程。**
> 每篇文档帮你把一个源文件的函数逻辑翻译成人类语言，让你在读代码时知道"这里为什么这么写"。
> v2 相比 v1 新增了 Pipeline 编排层（Part P）和研究治理层（Part A）的完整文档。

---

## 文档与源文件对应表

### Part A：架构与研究治理

| 文档 | 覆盖范围 | 处理方式 |
|------|---------|---------|
| [A1_architecture](A1_architecture.md) | 整个系统分层、数据流图、路径对比 | 新写 |
| [A2_research_workflow](A2_research_workflow.md) | 实验治理体系、五类实验角色、注册表、比较板 | 新写 |

### Part 1：基础设施层

| 文档 | 源文件 | 行数 |
|------|--------|:----:|
| [01_config](01_config.md) | `src/config.py` | 98 |
| [02_data_loader](02_data_loader.md) | `src/data/loader.py` | 595 |
| [03_pit_loader](03_pit_loader.md) | `src/data/pit_loader.py` | 329 |
| [04_universe](04_universe.md) | `src/data/universe.py` | 88 |

### Part 2：因子层

| 文档 | 源文件 | 行数 |
|------|--------|:----:|
| [05_financial_factors](05_financial_factors.md) | `src/factors/financial_factors.py` | 663 |
| [06_price_factors](06_price_factors.md) | `src/factors/price_factors.py` | 804 |
| [07_alt_factors](07_alt_factors.md) | `src/factors/alt_factors.py` | 524 |
| [08_preprocess](08_preprocess.md) | `src/factors/preprocess.py` | 166 |

### Part 3：单因子评估层

| 文档 | 源文件 | 行数 |
|------|--------|:----:|
| [09_ic_analysis](09_ic_analysis.md) | `src/evaluation/ic_analysis.py` | 453 |
| [10_factor_health](10_factor_health.md) | `src/evaluation/factor_health.py` | 393 |
| [11_quintile_backtest](11_quintile_backtest.md) | `src/evaluation/quintile_backtest.py` | 187 |
| [12_shift_test](12_shift_test.md) | `src/evaluation/shift_test.py` | 155 |

### Part 4：信号合成层

| 文档 | 源文件 | 行数 |
|------|--------|:----:|
| [13_combiner](13_combiner.md) | `src/signal/combiner.py` | 307 |
| [14_ridge_decay](14_ridge_decay.md) | `src/signal/ridge_decay.py` | 250 |

### Part 5：组合构建层

| 文档 | 源文件 | 行数 |
|------|--------|:----:|
| [15_covariance](15_covariance.md) | `src/portfolio/covariance.py` | 160 |
| [16_optimizer](16_optimizer.md) | `src/portfolio/optimizer.py` | 712 |

### Part 6：回测与归因层

| 文档 | 源文件 | 行数 |
|------|--------|:----:|
| [17_backtest_engine](17_backtest_engine.md) | `src/backtest/engine.py` | 472 |
| [18_transaction](18_transaction.md) | `src/backtest/transaction.py` | 39 |
| [19_metrics](19_metrics.md) | `src/backtest/metrics.py` | 152 |
| [20_brinson](20_brinson.md) | `src/attribution/brinson.py` | 410 |
| [21_factor_attr](21_factor_attr.md) | `src/attribution/factor_attr.py` | 615 |

### Part P：Pipeline 编排层

| 文档 | 源文件 | 行数 |
|------|--------|:----:|
| [P1_contracts](P1_contracts.md) | `src/pipeline/contracts.py` | 90 |
| [P2_stages](P2_stages.md) | `src/pipeline/stages.py` | 407 |
| [P3_artifacts_registry](P3_artifacts_registry.md) | `src/pipeline/artifacts.py` + `registry.py` | 78 + 87 |
| [P4_compare](P4_compare.md) | `src/pipeline/compare.py` | 300 |
| [P5_experiment_workflow](P5_experiment_workflow.md) | `scripts/run_experiment.py` + `compare_runs.py` + `promote_run.py` | 206+118+76 |
| [P6_test_pipeline_guardrails](P6_test_pipeline_guardrails.md) | `scripts/run_test_pipeline.py` + `test_set_ledger.py` | 1085+63 |

---

## 每篇文档的结构（固定十节）

```
一、文件定位        — 所属 Part，上游/下游，被谁调用
二、时间对齐与 PIT  — 数据截止时间，PIT 约束，横截面边界
三、模块顶部        — 关键 import，模块级常量
四、核心函数解析    — 每个 public 函数逐段分析（含行号注释 # L{N}）
五、内部辅助函数    — private 函数简要说明
六、落盘产物        — 写入哪些文件，格式，列名
七、相关测试        — tests/ 中的测试文件，关键边界用例
八、失败与降级路径  — 模块失败时的行为
九、数据流图        — ASCII 图
十、领域知识补充    — 本文件相关的数学原理或金融逻辑
```

---

## 推荐阅读路径（四种角色）

### 路径 A：想理解整体架构
```
A1_architecture → A2_research_workflow → P1_contracts → P2_stages
             → P5_experiment_workflow → 13_combiner → 16_optimizer → 17_backtest_engine
```

### 路径 B：审查数据正确性（PIT / 未来函数）
```
A1_architecture → 01_config → 02_data_loader → 03_pit_loader → 04_universe
             → 08_preprocess → 09_ic_analysis → 12_shift_test
```

### 路径 C：审查回测正确性（成本 / 成交逻辑）
```
01_config → 04_universe → 15_covariance → 16_optimizer
         → 18_transaction → 17_backtest_engine → 19_metrics
```

### 路径 D：理解实验治理（怎么设计和运行实验）
```
A2_research_workflow → P1_contracts → P2_stages → P3_artifacts_registry
             → P4_compare → P5_experiment_workflow → P6_test_pipeline_guardrails
```

---

## 使用注意事项

**文档是代码在特定时间点的快照。** 如果源码被修改但文档未同步，以源码为准。发现不一致时可要求更新该篇文档。

**代码片段保留了行号注释**，格式为 `# L{行号}`，方便直接跳转到源文件对应位置。行号在文档撰写时核实，后续代码改动后可能漂移，以 `Read` 工具读取源文件为准。

**v1 与 v2 文档编号不同**：v1 中的 `07_preprocess.md` 在 v2 中是 `08_preprocess.md`。两套文档独立，不互相引用。

**量化领域知识**（第十节）只讲当前模块用到的部分，不重复其他文档已经讲过的内容。
