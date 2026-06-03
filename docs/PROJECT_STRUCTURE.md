# Project Structure

## 当前目录职责

| 目录 | 职责 |
|---|---|
| `src/` | 生产代码：数据、因子、评估、信号、组合、回测、归因 |
| `tests/` | 单元测试和回归测试 |
| `scripts/` | 主管线入口和稳定工具脚本 |
| `experiments/` | 平行实验、参数网格、版本归档 |
| `reports/` | 当前主管线报告和小型结果表 |
| `data/` | raw/processed 数据缓存，不入库 |
| `notebooks/` | 探索性分析，不作为生产逻辑来源 |
| `docs/` | 计划、设计、指南、研究路线、测试集 ledger、历史审计归档 |

## 建议边界

1. 主管线稳定逻辑放 `src/` 和 `scripts/`。
2. 一次性实验逻辑放 `experiments/<experiment_name>/`，不要再复制到 `scripts/`，除非准备进入主管线。
3. 当前事实只保留一份源头：
   - 测试集次数：`docs/logs/test_set_runs.json` 与 `docs/logs/test_set_run_log.md`
   - 实验版本：`experiments/VERSIONS.md`
   - 阶段纪要：`docs/research/improve/progress_log.md`
4. 大文件产物只留本地，不入 git；摘要报告和 manifest 入库。

## docs/ 内部结构

```
docs/
├── PROJECT_PLAN_v1.1.md          # 主计划文档（日期切分、完成标准、风险登记表）
├── FILE_GUIDE.md                 # 当前进展快照 + 全文件索引（最常查阅的文件）
├── PROJECT_STRUCTURE.md          # 本文件：目录职责与边界说明
│
├── design/                       # 系统设计文档（随代码演进更新）
│   └── backtest_design.md        # 回测引擎设计说明
│
├── guides/                       # 操作与学习指南（稳定，查阅用）
│   ├── detailed_pipeline_guide.md    # 全流程操作手册
│   ├── factor_evaluation_guide.md    # 因子评估方法指南
│   ├── improvement_guide.md          # 改进思路总结
│   ├── results_interpretation.md     # 结果解读指南
│   ├── project_explainer.md          # 项目整体说明（适合新人）
│   └── china_ashare_factor_library.md  # A 股因子库参考
│
├── deep_dive/                    # 模块源码精读系列（00-18，教学用）
│   ├── 00_reading_guide.md       # 阅读路线图
│   └── 01~18_*.md                # 逐模块深度解析
│
├── research/                     # 研究规划（仍在推进）
│   ├── factor_roadmap/           # 因子路线图与研究计划
│   ├── data_expansion_plan/      # 数据扩展计划与执行记录
│   └── improve/                  # IR 诊断、优化器诊断、改进阶段计划
│
├── logs/                         # 运行记录（只追加）
│   ├── test_set_runs.json        # 测试集次数计数（权威来源，≤ 3 次）
│   └── test_set_run_log.md       # 每次测试集运行前的登记日志
│
└── archive/                      # 历史记录（只读，留存溯源）
    ├── check/                    # 阶段验证记录 00-24（已完成的审查）
    ├── audit_plan_20260521/      # 2026-05-21 性能诊断审计快照
    └── DONE.md                   # 历史完成事项记录
```

## 后续可选整理

等当前 v1.1 是否进入测试集明确后，再考虑：

- 将 `reports/optimizer_grid/` 归到 `experiments/optimizer_grid_o1/`
- 将旧 `reports/turnover_lambda_grid/` 标记为 superseded 或移入归档
- 将 `docs/research/improve/loader.py` 迁入对应实验目录或归档
- 将 `reports/current/` 与 `reports/archive/` 分层
