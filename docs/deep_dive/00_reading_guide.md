# 00 — 深度讲解文档阅读指南

> **本目录定位：代码审查地图，而非入门教程。**
> 每篇文档帮你把一个源文件的函数逻辑翻译成人类语言，让你在读代码时知道"这里为什么这么写"。

---

## 这套文档能帮你做什么

| 场景 | 用法 |
|------|------|
| 审查某个模块的逻辑 | 找到对应文档 → 读"核心函数逐一解析" → 对照源码验证 |
| 理解一个陌生函数 | 用文档的"函数签名与参数"直接查 |
| 怀疑某处有未来函数 | 读 03（PIT） + 07（预处理） + 10（时间错位测试） |
| 想改优化器约束 | 读 04（Universe状态）+ 13（优化器） |
| 想理解回测为何亏损 | 读 15（成本）+ 14（回测引擎）+ 17（Brinson归因） |

---

## 文档与源文件对应表

| 文档 | 源文件 | 行数 |
|------|--------|------|
| [01_config](01_config.md) | `src/config.py` | 107 |
| [02_data_loader](02_data_loader.md) | `src/data/loader.py` | 535 |
| [03_pit_loader](03_pit_loader.md) | `src/data/pit_loader.py` | 328 |
| [04_universe](04_universe.md) | `src/data/universe.py` | 137 |
| [05_financial_factors](05_financial_factors.md) | `src/factors/financial_factors.py` | 546 |
| [06_price_factors](06_price_factors.md) | `src/factors/price_factors.py` | 659 |
| [07_preprocess](07_preprocess.md) | `src/factors/preprocess.py` | 248 |
| [08_ic_analysis](08_ic_analysis.md) | `src/evaluation/ic_analysis.py` | 486 |
| [09_quintile_backtest](09_quintile_backtest.md) | `src/evaluation/quintile_backtest.py` | 252 |
| [10_shift_test](10_shift_test.md) | `src/evaluation/shift_test.py` | 190 |
| [11_combiner](11_combiner.md) | `src/signal/combiner.py` | 369 |
| [12_covariance](12_covariance.md) | `src/portfolio/covariance.py` | 235 |
| [13_optimizer](13_optimizer.md) | `src/portfolio/optimizer.py` | 697 |
| [14_backtest_engine](14_backtest_engine.md) | `src/backtest/engine.py` | 627 |
| [15_transaction](15_transaction.md) | `src/backtest/transaction.py` | 59 |
| [16_metrics](16_metrics.md) | `src/backtest/metrics.py` | 210 |
| [17_brinson](17_brinson.md) | `src/attribution/brinson.py` | 552 |
| [18_factor_attr](18_factor_attr.md) | `src/attribution/factor_attr.py` | 782 |

---

## 每篇文档的结构（固定六节）

```
一、文件定位        — 它在流程中处于哪一步，读/写什么文件
二、模块顶部        — import 意图 + 模块级常量
三、核心函数解析    — 每个公开函数逐段分析
四、内部辅助函数    — private 函数简要说明
五、数据流图        — ASCII 图：输入 → 处理 → 输出
六、领域知识补充    — 代码背后的数学原理或金融逻辑
```

---

## 推荐阅读路径（三种角色）

### 路径 A：想理解整体架构
```
01_config → 02_data_loader → 05_financial_factors → 08_ic_analysis
         → 11_combiner → 13_optimizer → 14_backtest_engine → 17_brinson
```

### 路径 B：审查数据正确性（PIT / 未来函数）
```
01_config → 02_data_loader → 03_pit_loader → 04_universe
         → 07_preprocess → 08_ic_analysis → 10_shift_test
```

### 路径 C：审查回测正确性（成本 / 成交逻辑）
```
01_config → 02_data_loader → 04_universe
         → 12_covariance → 13_optimizer
         → 15_transaction → 14_backtest_engine → 16_metrics
```

---

## 使用注意事项

**文档是代码在特定时间点的快照。** 如果源码被修改但文档未同步，以源码为准。发现不一致时可以要求更新该篇文档。

**代码片段保留了行号注释**，格式为 `# L{行号}`，方便直接跳转到源文件对应位置。

**量化领域知识**（第六节）只讲当前模块用到的部分，不重复其他文档已经讲过的内容。
