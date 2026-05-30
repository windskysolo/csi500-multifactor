# 深度讲解文档集 v2 — 撰写计划

> 本文件是"先写计划，再逐篇生成"的执行路线图。
> 每完成一篇文档，在对应条目打 ✅ 并标注日期。
>
> **v2 说明**：原 `docs/deep_dive/` 写于 2026-05-20，覆盖项目搭建期代码。
> 当前代码结构已发生重大变化（新增整套 Pipeline 编排层 + 3 个业务模块 + 实验治理体系），
> 本目录全量覆盖现状，旧文档仅作历史参照。
>
> **权威来源原则**：所有行数、函数签名、实现状态以撰写时读取的源文件为准，
> 本计划里的行数是撰写计划时的快照，实际撰写前必须重新 `Read` 源文件核实。

---

## 整体策略

分四级处理：

| 处理级别 | 含义 | 篇数 |
|---|---|---|
| **新写** | v1 目录没有对应模块，必须新建 | 11 篇 |
| **重写** | 模块变化大或定位改变，旧文档已实质性失准 | 2 篇 |
| **核查更新** | 模块有局部变化，旧文档大体可用但需逐函数核实 | 5 篇 |
| **沿用** | 模块基本未变，核实编号/行号/交叉引用后可复用旧内容 | 12 篇 |

> ⚠️ **"沿用"不等于"直接复制"**：v2 编号与 v1 错位（例如旧 `07_preprocess` → v2 `08_preprocess`），
> 旧文档里的交叉引用、行号注释（`# L{N}`）、章节引用均需重新核查。
> 每篇"沿用"文档必须完成以下三项才可发布：
> 1. 更新文档编号和所有内部交叉引用
> 2. 逐一核查代码行号注释是否仍对应当前源文件
> 3. 核查所有函数签名是否有变化

---

## 文档清单（共 30 篇）

### Part 0：阅读导航

| 序号 | 文件名 | 说明 | 处理方式 | 状态 |
|------|--------|------|---------|------|
| 00 | `00_reading_guide.md` | 阅读路径、角色指引、与 v1 对照说明 | **重写**：新增 Pipeline / 研究治理阅读路径 | ✅ 2026-05-29 |

### Part A：架构与研究治理（全部新写）

| 序号 | 文件名 | 对应范围 | 状态 |
|------|--------|---------|------|
| A1 | `A1_architecture.md` | 整个项目系统架构 | ✅ 2026-05-29 |
| A2 | `A2_research_workflow.md` | 实验治理体系 | ✅ 2026-05-29 |

> **A1 是所有其他文档的前置定向，必须第一篇完成。**

### Part 1：基础设施层

| 序号 | 文件名 | 对应源文件 | 行数 | 处理方式 | 状态 |
|------|--------|-----------|:----:|---------|------|
| 01 | `01_config.md` | `src/config.py` | 98 | **核查更新**：确认新增 SIGNAL_*、TopN、Ridge 相关参数 | ✅ 2026-05-29 |
| 02 | `02_data_loader.md` | `src/data/loader.py` | 595 | 核查更新（新增大量备选数据接口）| ✅ 2026-05-29 |
| 03 | `03_pit_loader.md` | `src/data/pit_loader.py` | 329 | **重写**（由类式改为函数式，STALE_THRESHOLD 修复）| ✅ 2026-05-29 |
| 04 | `04_universe.md` | `src/data/universe.py` | 88 | 核查更新（新增 get_constraint_states / get_full_universe）| ✅ 2026-05-29 |

### Part 2：因子层

| 序号 | 文件名 | 对应源文件 | 行数 | 处理方式 | 状态 |
|------|--------|-----------|:----:|---------|------|
| 05 | `05_financial_factors.md` | `src/factors/financial_factors.py` | 663 | **核查更新**（22因子，accrual修复，6个阶段4因子新增）| ✅ 2026-05-29 |
| 06 | `06_price_factors.md` | `src/factors/price_factors.py` | 804 | **核查更新**（15因子，Sprint1新增6个动量因子）| ✅ 2026-05-29 |
| 07 | `07_alt_factors.md` | `src/factors/alt_factors.py` | 524 | **新写** | ✅ 2026-05-29 |
| 08 | `08_preprocess.md` | `src/factors/preprocess.py` | 166 | **重写**（由类式私有函数改为公开函数，MAD=0退化逻辑新增）| ✅ 2026-05-29 |

### Part 3：单因子评估层

| 序号 | 文件名 | 对应源文件 | 行数 | 处理方式 | 状态 |
|------|--------|-----------|:----:|---------|------|
| 09 | `09_ic_analysis.md` | `src/evaluation/ic_analysis.py` | ~~453~~ **649** | **核查更新**：确认 BH 校正、Gate 接口等新增逻辑 | ✅ 2026-05-29 |
| 10 | `10_factor_health.md` | `src/evaluation/factor_health.py` | ~~393~~ **504** | **新写** | ✅ 2026-05-29 |
| 11 | `11_quintile_backtest.md` | `src/evaluation/quintile_backtest.py` | ~~187~~ **253** | 沿用+核查 | ✅ 2026-05-29 |
| 12 | `12_shift_test.md` | `src/evaluation/shift_test.py` | ~~155~~ **219** | 沿用+核查 | ✅ 2026-05-29 |

### Part 4：信号合成层

| 序号 | 文件名 | 对应源文件 | 行数 | 处理方式 | 状态 |
|------|--------|-----------|:----:|---------|------|
| 13 | `13_combiner.md` | `src/signal/combiner.py` | ~~307~~ **369** | **核查更新**：确认 Ridge 分支接口 | ✅ 2026-05-29 |
| 14 | `14_ridge_decay.md` | `src/signal/ridge_decay.py` | ~~250~~ **316** | **新写** | ✅ 2026-05-29 |

### Part 5：组合构建层

| 序号 | 文件名 | 对应源文件 | 行数 | 处理方式 | 状态 |
|------|--------|-----------|:----:|---------|------|
| 15 | `15_covariance.md` | `src/portfolio/covariance.py` | ~~160~~ **236** | 沿用+核查更新（新增 `validate_and_repair_covariance` F6-003）| ✅ 2026-05-29 |
| 16 | `16_optimizer.md` | `src/portfolio/optimizer.py` | ~~712~~ **914** | **重写**：新增 `optimizer_mode="topn_ew"` 分支，三层 Fallback 结构有变 | ✅ 2026-05-29 |

### Part 6：回测与归因层

| 序号 | 文件名 | 对应源文件 | 行数 | 处理方式 | 状态 |
|------|--------|-----------|:----:|---------|------|
| 17 | `17_backtest_engine.md` | `src/backtest/engine.py` | ~~472~~ **628** | 沿用+核查更新（F7-001~003 修复，全收益基准强制校验）| ✅ 2026-05-29 |
| 18 | `18_transaction.md` | `src/backtest/transaction.py` | ~~39~~ **60** | 沿用+核查更新（docstring 扩充）| ✅ 2026-05-29 |
| 19 | `19_metrics.md` | `src/backtest/metrics.py` | ~~152~~ **211** | 沿用+核查更新（新增 calmar_ratio / monthly_win_rate）| ✅ 2026-05-29 |
| 20 | `20_brinson.md` | `src/attribution/brinson.py` | ~~410~~ **553** | 沿用+核查更新（F8-001~003 修复，现金仓位、summarize_by_segment）| ✅ 2026-05-29 |
| 21 | `21_factor_attr.md` | `src/attribution/factor_attr.py` | ~~615~~ **800** | 沿用+核查更新（F8-004/005，DEFAULT_FACTOR_GROUPS 扩展）| ✅ 2026-05-29 |

### Part P：Pipeline 编排层（全部新写）

这是 v2 相比 v1 最大的新增部分，原目录完全没有覆盖。

| 序号 | 文件名 | 对应源文件 | 行数 | 状态 |
|------|--------|-----------|:----:|------|
| P1 | `P1_contracts.md` | `src/pipeline/contracts.py` | ~~90~~ **110** | ✅ 2026-05-29 |
| P2 | `P2_stages.md` | `src/pipeline/stages.py` | ~~407~~ **501** | ✅ 2026-05-29 |
| P3 | `P3_artifacts_registry.md` | `src/pipeline/artifacts.py` + `src/pipeline/registry.py` | ~~78+87~~ **102+110** | ✅ 2026-05-29 |
| P4 | `P4_compare.md` | `src/pipeline/compare.py` | ~~300~~ **381** | ✅ 2026-05-29 |
| P5 | `P5_experiment_workflow.md` | `scripts/run_experiment.py` + `scripts/compare_runs.py` + `scripts/promote_run.py` | ~~206+118+76~~ **255+155+95** | ✅ 2026-05-29 |
| P6 | `P6_test_pipeline_guardrails.md` | `scripts/run_test_pipeline.py` + `scripts/test_set_ledger.py` | 1085 + ~~63~~ **82** | ✅ 2026-05-29 |

---

## 优先级与执行顺序

### 第一批（P0 — 架构定向，所有文档的前置）

| 文档 | 理由 |
|------|------|
| `A1_architecture.md` | 整个系统的分层图和数据流图，后续所有文档都会交叉引用 |
| `A2_research_workflow.md` | 实验治理体系，理解"为何有这些配置文件和注册表"的必要前置 |
| `P1_contracts.md` | ExperimentSpec 是 Pipeline 层的核心数据结构，P2/P5/P6 都依赖 |

### 第二批（P1 — Pipeline 核心，理解实验如何运行）

| 文档 | 理由 |
|------|------|
| `P2_stages.md` | Stage 编排是连接 Spec 和底层模块的关键 |
| `P4_compare.md` | 实验报告直接引用 compare board，阅读频率高，提前写 |
| `P5_experiment_workflow.md` | 端到端工作流，"用户视角"最常查阅的文档 |
| `P6_test_pipeline_guardrails.md` | 测试集保护机制，工程纪律的核心约束 |

### 第三批（P2 — 新业务模块，完全没有旧文档）

| 文档 | 理由 |
|------|------|
| `10_factor_health.md` | 四道 Gate 是因子评估的核心，run_factor_evaluation.py 依赖它 |
| `14_ridge_decay.md` | 验证期对照实验已跑出结果（hl24/36/48），需要文档说明设计 |
| `07_alt_factors.md` | 524 行新增因子代码，逻辑复杂 |
| `16_optimizer.md`（重写）| TopN EW 模式是当前验证期重要对照路径，旧文档不含此分支 |

### 第四批（P3 — 核查更新 + 工程支撑）

| 文档 | 理由 |
|------|------|
| `P3_artifacts_registry.md` | 产物管理与注册机制，阅读频率低于 P4/P5 |
| `00_reading_guide.md`（重写）| 依赖所有其他文档完成后才能写准阅读路径 |
| `01_config.md` | 核查新增参数 |
| `05_financial_factors.md` | 核查 accrual 修复后的逻辑 |
| `06_price_factors.md` | 行数增加 145 行，需确认新增内容 |
| `09_ic_analysis.md` | 核查 Gate 接口和 BH 校正 |
| `13_combiner.md` | 核查 Ridge 分支接口变化 |

### 第五批（P4 — 沿用验证，逐一核查编号/行号/交叉引用）

02、03、04、08、11、12、15、17、18、19、20、21，共 **12 篇**。
每篇完成：① 更新编号和交叉引用 → ② 核查行号注释 → ③ 核查函数签名 → ✅

---

## 每篇文档的固定结构（模板，共十节）

```
### 一、文件定位
  - 所属 Part（见 A1_architecture.md 分层图）
  - 在数据流中的位置（上游是谁，下游是谁）
  - 被哪些模块/脚本调用，调用哪些模块

### 二、时间对齐与 PIT 假设
  - 本模块使用的数据截止到哪一天（含 T 还是 <T）
  - 是否存在 PIT 约束（财务数据必须用 ann_date）
  - 横截面计算是否只用当期数据（去极值、中性化的常见错误点）

### 三、模块顶部：导入与常量
  - 关键 import 的意图
  - 模块级常量的含义与来源

### 四、核心函数逐一解析
  对该文件中每个 public 函数展开：
  - 函数签名与参数含义
  - 执行逻辑（逐段说明，配关键代码片段，格式：# L{行号}）
  - 返回值：列名/类型/单位
  - 注意事项 / 常见坑

### 五、内部辅助函数
  private（_开头）函数简要说明，不展开逐行

### 六、落盘产物（Artifacts）
  - 本模块写入哪些文件（路径模式 + 格式 + 列名）
  - 写入时机（每次调用都写 / 仅在特定条件下写）
  - 读取约定（下游模块如何读取这些文件）

### 七、相关测试
  - tests/ 中对应的测试文件和测试函数
  - 关键边界用例（如时间错位测试、印花税切换点）
  - 缺少测试的高风险逻辑（标注 TODO）

### 八、失败与降级路径
  - 本模块可能失败的条件
  - 失败时的行为（抛异常 / 返回 None / 触发 Fallback）
  - 上游如何感知到失败（Stage 层的异常处理）

### 九、数据流图
  ASCII 图展示：数据输入 → 处理 → 输出

### 十、领域知识补充
  代码背后的数学原理或金融逻辑
  （只讲本文件直接用到的，不重复其他文档已讲内容）
```

---

## 新写文档内容预告

### `A1_architecture.md` — 系统架构全景

- 系统八层分层图：
  ```
  Layer 8  Pipeline 编排   src/pipeline/（contracts / stages / artifacts / registry / compare）
  Layer 7  实验脚本        scripts/（run_experiment / compare_runs / promote_run / run_test_pipeline）
  Layer 6  归因            src/attribution/（brinson / factor_attr）
  Layer 5  回测            src/backtest/（engine / transaction / metrics）
  Layer 4  组合构建        src/portfolio/（covariance / optimizer）
  Layer 3  信号合成        src/signal/（combiner / ridge_decay）
  Layer 2  因子评估        src/evaluation/（ic_analysis / factor_health / quintile_backtest / shift_test）
  Layer 1  因子构建        src/factors/（financial / price / alt / preprocess）
  Layer 0  数据基础        src/data/（loader / pit_loader / universe） + src/config.py
  ```
- 完整数据流图：从原始 Parquet 缓存到 NAV 曲线 + 归因报告
- 三条信号路径对比：
  - 路径 A：IC_IR 加权合成（`combiner.py` method="icir"）
  - 路径 B：Ridge expanding/rolling（`combiner.py` method="ridge"）
  - 路径 C：Ridge 指数衰减（`ridge_decay.py` + `combiner.py` mode="decay_weighted_expanding"）
- 两种组合构建路径对比：
  - 路径 QP：二次规划优化（L1→L2→L3 三层 Fallback）
  - 路径 EW：TopN 等权（隔离优化器影响的对照实验工具）
- `runs/` 目录结构说明：`train_valid/<run_id>/` vs `test/<run_id>/` 的隔离
- 关键设计决策：为何将 Stage 编排与业务逻辑分离、为何用 dataclass 而非 dict 传参

### `A2_research_workflow.md` — 实验治理体系

- 五类实验角色及对应的 `configs/pipelines/` 命名惯例：
  - `frozen_baseline_*`：冻结基线，永不重跑，所有实验的下限参照
  - `baseline_*`：主线基线，已晋升 mainline，当前生产方案
  - `challenger_*`：挑战者，正在验证期对比的候选
  - `ablation_*`：消融实验，剔除单个因子/模块测量贡献
  - `test_run_N`：测试集运行（≤2次，有严格纪律）
- 注册表文件体系：
  - `registry/mainline.json`：已晋升方案的 run_id 和关键指标
  - `registry/challengers.json`：当前挑战者状态（含 hl24/36/48 结果）
  - `reports/experiment_board.md`：所有 run 的横向比较板
- 完整实验治理流程图：
  ```
  写 Spec → run_experiment → compare_runs（含 frozen_baseline 下限）
    → 通过门槛? → promote_run → 更新 mainline
    → 不通过   → 记录结论 → 更新 challengers.json
  ```
- 冻结基线存在的意义：隔离"算法改进"与"数据/时间区间扩展"的混淆
- 消融实验的设计原则：一次只剔一个变量，结论才可归因
- 测试集纪律概述（详细机制在 P6）

### `07_alt_factors.md` — 另类因子

- 覆盖 `src/factors/alt_factors.py` 全部 524 行
- 因子分类（当前已实现的类别）与对应的 Tushare 数据接口
- 每个因子的 PIT 安全保证：哪些字段依赖 `ann_date`，哪些是日频行情可直接用
- 与 `financial_factors.py`（财务因子）和 `price_factors.py`（量价因子）的职责边界
- 金融股特殊处理逻辑、新股排除条件
- `compute_all_alt_factors()` 入口函数的调用约定

### `10_factor_health.md` — 因子健康评估

- 覆盖 `src/evaluation/factor_health.py` 全部 393 行
- 四道 Gate 的完整计算逻辑：
  - Gate 0：截面覆盖率（有效值 / 可投资域 ≥ 阈值）
  - Gate 1：IC_IR 显著性（`|ic_ir| ≥ 0.30` 且 `p_value < 0.05`）
  - Gate 2：单调性（5分组收益的 Spearman 秩相关 ≥ 阈值）
  - Gate 3：方向一致性（训练期与验证期 IC 均值同号）
- `FactorHealthReport` 数据结构的每个字段
- 与 `ic_analysis.py` 的分工：`factor_health` 是 `ic_analysis` 的上层封装，负责 Gate 判断和报告聚合
- 报告输出格式：`reports/factor_evaluation/` 下的文件结构

### `14_ridge_decay.md` — 指数衰减加权 Ridge

- 覆盖 `src/signal/ridge_decay.py` 全部 250 行
- `RidgeDecayCombiner` 的设计：时间衰减权重的构造
  - 权重公式：`w_t = exp(-ln2 / half_life × (T - t))`，`half_life` 单位为月
  - 含义：距当前 `half_life` 个月的样本，其权重为最新样本的 1/2
- Walk-forward CV 的实现：训练窗口内按时间切分，防止用验证集数据选 alpha
- 与 `combiner.py` 中三种训练模式（expanding / rolling / decay）的差异对比
- `stages.py` 的接入点：`mode="decay_weighted_expanding"` 分支（L175-184）
- 验证期实验结论（训练集事实，非测试集结论）：hl24/36/48 在验证期的表现
  - 注意：该结论仅限于验证集，用于指导下一步研究方向，不代表最终策略优劣

### `P1_contracts.md` — 实验数据契约

- 覆盖 `src/pipeline/contracts.py` 全部 90 行
- 五个 dataclass 的字段含义与默认值：
  - `SignalSpec`：`method`（icir/ridge）、`training_mode`（expanding/rolling/decay_weighted_expanding）、`purge_months`、`alpha_grid`、`half_life_months`、`window_months`、`exclude_factors`
  - `OptimizerSpec`：约束参数（te_target_annual、industry_max_dev、single_max_dev、turnover_lambda、topn）、`optimizer_mode`（qp/topn_ew）
  - `BacktestSpec`：`execution`（tplus1_open）、`cost_model`、`benchmark`
  - `AttributionSpec`：`method`（brinson）
  - `ExperimentSpec`：组合以上所有 Spec，`allow_test_set` 测试集保护锁，`experiment_type`
- `ExperimentSpec.__post_init__()` 的校验逻辑：测试集保护锁如何在代码层面防止误运行
- `ExperimentSpec.from_config_file()` 的动态加载机制（`importlib.util`）：为何不用 JSON/YAML
- `RunContext`：run_id 生成规则（UTC 时间戳 + experiment_id）、run_dir 路径约定

### `P2_stages.md` — Stage 编排逻辑

- 覆盖 `src/pipeline/stages.py` 全部 407 行
- 四个 stage 函数的分发逻辑：
  - `run_signal_stage()`：按 `method` + `training_mode` 分发到三条信号路径
  - `run_portfolio_stage()`：按 `optimizer_mode` 分发到 QP 或 TopN EW
  - `run_backtest_stage()`
  - `run_attribution_stage()`
- 路径约定：每个 stage 写到 `run_dir/{stage_name}/` 下的哪些文件（对应 P3 artifacts）
- 异常处理策略：stage 失败时已完成的 artifact 如何保留、如何从断点恢复
- 已知文档漂移：`stages.py` 模块 docstring（约 L38）仍写"decay_weighted_expanding 尚未实现"，
  但实际 L175-184 已接入 `RidgeDecayCombiner`。撰写此文档时需标注并建议修复该 docstring

### `P4_compare.md` — 实验横向比较

- 覆盖 `src/pipeline/compare.py` 全部 300 行
- `compare_runs()` 的核心逻辑：从各 run_dir 读取绩效指标并组装比较表
- 为何必须包含 `frozen_baseline_icir_topn50_ew`：作为下限参照，防止"新方案虽然比基线好但绝对值很低"的误判
- 六项必报指标的计算来源（IR / 年化超额 / 超额 MDD / TE / 月度胜率 / 换手率）
- 三项硬指标 PASS/FAIL 的判断逻辑
- 输出格式：`reports/experiment_board.md` + `reports/experiment_board.csv`

### `P5_experiment_workflow.md` — 端到端实验工作流（训练/验证集）

- 一次完整实验的全链路（含脚本行数）：
  ```
  写 Spec (.py)
    → scripts/run_experiment.py（206 行）
      → Stage 1 Signal  → Stage 2 Portfolio → Stage 3 Backtest → Stage 4 Attribution
    → scripts/compare_runs.py（118 行）
    → （通过门槛）scripts/promote_run.py（76 行）
  ```
- `run_experiment.py` 的 CLI 参数（`--spec`、`--from-stage`、`--input-signal-run`）
- `--from-stage` 的加速机制：复用已有信号，跳过耗时的信号阶段
- `compare_runs.py`：比较表生成，`frozen_baseline` 作为下限参照的强制要求
- `promote_run.py`：晋升条件（三项硬指标全 PASS）、写入 `registry/mainline.json` 的内容
- 一次 run 结束后 `runs/train_valid/<run_id>/` 的完整目录结构

### `P6_test_pipeline_guardrails.md` — 测试集流水线与防污染机制

- 覆盖 `scripts/run_test_pipeline.py`（1085 行）+ `scripts/test_set_ledger.py`（63 行）
- 测试集与训练/验证集的物理隔离：`runs/test/` vs `runs/train_valid/`，公共产物不覆盖
- `RUN_STARTED.json` 锁文件机制：防止测试集 run 中途中断后被重复计数
- `--resume-from-lock` 参数：合法恢复 vs 非法绕过的判断逻辑
- `test_set_ledger.py`：测试集计数的权威来源（`docs/logs/test_set_runs.json`），与 CLAUDE.md §1.1 的关系
- `allow_test_set=True` 在 `ExperimentSpec` 中的作用（契约层保护）
- git commit message 中 `[TEST_SET_RUN_N]` 标记的规范和意义

---

## 生成指引（撰写时强制遵守）

1. **读取源文件全文**（`Read` 工具），不凭记忆写；本计划中的行数是快照，撰写前必须重新核实
2. **读取相关测试文件**（`tests/test_*.py`），了解预期行为
3. **按十节模板展开**，代码片段引用实际代码，加行号注释（格式：`# L{行号}`）
4. **时间对齐（第二节）优先**：每篇文档先确认 PIT 约束和横截面计算的时间边界
5. **领域知识（第十节）**：只讲本文件直接相关的部分，不重复其他文档内容
6. **完成后**：在本 PLAN.md 的状态列改为 ✅ 并注明日期
7. **发现代码与文档不一致**（如 stages.py docstring 漂移问题）：在文档中明确标注，不静默忽略
