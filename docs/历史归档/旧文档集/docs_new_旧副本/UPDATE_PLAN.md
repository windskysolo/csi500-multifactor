# docs_new 更新计划

> 本文件记录 docs_new/ 中各文档的滞后点，待逐项修复。
> 修复完成后在对应条目打勾（✅）并注明日期。
> 所有改动以代码 + 实验产物为准，不凭记忆补写。

---

## 文件一：FILE_MAP.md

### ✅ 问题 1 — `src/signal/` 节缺 `ridge_decay.py`（2026-05-29 修复）

**当前**：只列了 `combiner.py`  
**事实**：2026-05-28 新增 `src/signal/ridge_decay.py`，实现 `RidgeDecayCombiner`（继承 RidgeCombiner，支持指数衰减加权）  
**修复**：在 `src/signal/` 节补一行：
```
| `ridge_decay.py` | 指数衰减 Ridge 信号合成。`RidgeDecayCombiner` 继承 `RidgeCombiner`，对历史样本按半衰期指数降权；支持 hl=24/36/48m 等配置 |
```

---

### ✅ 问题 2 — `src/evaluation/` 节缺 `factor_health.py`（已存在于 docs_new 副本，无需修复）

**当前**：该节列了 ic_analysis / quintile_backtest / shift_test，未列 factor_health  
**事实**：`src/evaluation/factor_health.py` 已实现，功能是基于 IC 历史矩阵计算 12/24/36m 滚动 IC_IR，输出 STABLE/WEAK/WARN/REVERSE/UNKNOWN 状态  
**修复**：在 `src/evaluation/` 表格新增一行：
```
| `factor_health.py` | 因子健康监控。读取 ic_history 矩阵，计算滚动 IC_IR，输出 STABLE/WEAK/WARN/REVERSE/UNKNOWN 状态和 DETERIORATING 趋势预警 |
```

---

### ✅ 问题 3 — `tests/` 节缺测试文件（2026-05-29 修复）

**当前**：列了约 25 个测试文件  
**事实**：实际存在 26 个，多出以下三个：
- `tests/test_ridge_decay.py`（14 个测试，覆盖 RidgeDecayCombiner 核心逻辑）
- `tests/test_factor_health.py`（因子健康监控测试）
- `tests/test_research_workflow.py`（实验工作流端到端测试）

**修复**：在 tests/ 表格末尾追加这三行。

---

### ✅ 问题 4 — `configs/pipelines/` 表缺 3 个消融实验 Spec（2026-05-29 修复）

**当前**：列了 9 个 Spec，从 frozen_baseline 到 decay_ridge_hl48  
**事实**：目录下还有：
- `ablation_no_piotroski_f.py`（剔除 piotroski_f，IR=0.524，PASS）
- `ablation_no_high_52w_v2.py`（剔除 high_52w_v2，IR=0.423，持平）
- `ablation_no_piotroski_high52w.py`（两者同剔，IR=0.430）

**修复**：表格末尾追加这三行，状态列写"已完成（消融实验）"，并标注 run_id。

---

### ✅ 问题 5 — `experiments/` 版本里程碑表信息过时（2026-05-29 修复）

**当前**：表格写 v1.2 是"当前最优，18 因子"  
**事实**：
- 当前因子池 17 个（非 18）
- 最优 run 是 `challenger_rolling48`（IR=1.489），通过 `runs/` 框架管理，不在 experiments/v1.x 目录
- experiments/ 目录已成为历史版本归档，不再反映当前最优

**修复**：在该节前加一段说明：
> experiments/v1.x 是早期版本归档，不反映当前状态。当前实验通过 runs/ 框架管理，最优 run 见 CLAUDE.md §1.1 或 registry/challengers.json。

---

### ✅ 问题 6 — `check/` 节缺 `0529/`（2026-05-29 修复）

**当前**：只提到 `0526/`、`0527/`  
**事实**：`check/0529/` 存在，内含 piotroski_f 专项诊断输出（`piotroski_diagnosis.md`）  
**修复**：在 check/ 节补充 `0529/` 及其内容说明。

---

### ✅ 问题 7 — 根目录结构图缺新文件（2026-05-29 修复）

**当前**：列了 README.md / CLAUDE.md / AGENTS.md / MIGRATION_STATUS.md / requirements.txt / pytest.ini / .gitignore  
**事实**：根目录还有：
- `conftest.py`（2026-05-28 新增，pytest 入口配置）
- `current work/`（目录，含 5.28/、5.29/ 子目录和实验中间笔记）
- `docs_new/`（本次新建，AI 参考文档）

**修复**：根目录结构图追加这三项，并说明 `current work/` 的用途（实验过程中的临时分析笔记）。

---

## 文件二：FILE_GUIDE.md

### ✅ 问题 1 — Part 4 已知问题表 piotroski_f 状态未更新（2026-05-29 修复）

**当前**：piotroski_f 一行写"方向疑似反转，状态：待诊断"  
**事实**：2026-05-29 诊断已完成，脚本 `scripts/diagnose_piotroski.py`，报告 `check/0529/piotroski_diagnosis.md`  
**结论**：REMOVE_CANDIDATE — 2021-2022 出现方向反转，前 9 年有效，风格周期假说成立，消融实验确认剔除后 IR=0.524（提升）  
**修复**：将该行状态列改为"已确认 REMOVE_CANDIDATE（2026-05-29），消融 IR=0.524"

---

## 其余文件：无需更新

| 文件 | 状态 |
|------|------|
| `RESEARCH_GUIDE.md` | 最新（2026-05-28）|
| `PROJECT_PLAN_v1.1.md` | 权威决策文档，按需查阅 |
| `PROJECT_STRUCTURE.md` | 结构未变化 |
| `PIPELINE_MAP.md` | 数据流未变化 |
| `factor_research_guide.md` | 最新（2026-05-29，含消融结论）|
| `progress_log.md` | 最新（含阶段 N+1）|
| `factor_system_dimensions.md` | 理论框架，无时效性 |
| `factor_catalog.md` | 参考目录，无时效性 |
| `test_set_runs.json` | 权威计数，当前正确 |
| `test_set_run_log.md` | 当前正确 |
| `deep_dive/` 18 篇 | 代码未重构，仍有效 |
| `guides/` 6 篇 | 方法指南，无时效性 |
| `design/` 3 篇 | 设计文档，无时效性 |

---

## 执行顺序建议

1. 先修 `FILE_MAP.md`（7 处，影响文件导航）
2. 再修 `FILE_GUIDE.md`（1 处，影响问题追踪）
