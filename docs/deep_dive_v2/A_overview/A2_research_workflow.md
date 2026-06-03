# A2 — 实验治理体系与研究工作流

> 本文档回答"这些配置文件和注册表是怎么被组织起来的，为什么这么设计"。
> 覆盖 `configs/pipelines/`、`registry/` 和 `reports/experiment_board.*`。

---

## 一、文件定位

```
读取：configs/pipelines/*.py（实验定义）、registry/challengers.json、registry/mainline.json
写入：registry/challengers.json（手动更新）、registry/mainline.json（promote_run.py 写入）
         reports/experiment_board.md/csv（compare_runs.py 生成）
         docs/logs/test_set_runs.json（run_test_pipeline.py 写入）
被谁调用：run_experiment.py 读取 spec 文件；promote_run.py 写入 mainline.json
```

---

## 二、五类实验角色

每个 `configs/pipelines/` 下的 `.py` 文件都对应一次实验，按命名前缀分为五类，职责严格隔离：

### 2.1 frozen_baseline（冻结基线）

**命名模式**：`frozen_baseline_*.py`

**唯一实例**：`frozen_baseline_icir_topn50_ew`（IR=0.924，run_id=`20260529_034947__frozen_baseline_icir_topn50_ew`）

**作用**：所有实验结果的**绝对下限参照**。任何声称"改进"的实验，IR 必须高于冻结基线，否则无意义。

**规则（硬约束）**：
- 永不重跑（run_id 不变）
- 永不晋升（不进 mainline.json）
- `compare_runs.py` 每次调用必须包含此 run_id

**设计原因**：消除"数据区间扩展"对 IR 的正向影响。任何新方案的对比必须用同样的时间段来跑，而 frozen_baseline 就是这个固定锚点。

```python
# frozen_baseline_icir_topn50_ew.py 核心配置
SPEC = ExperimentSpec(
    experiment_id="frozen_baseline_icir_topn50_ew",
    period_scope="train_valid",
    signal=SignalSpec(method="icir", ...),
    optimizer=OptimizerSpec(optimizer_mode="topn_ew", topn=50),
)
```

### 2.2 baseline（主线基线）

**命名模式**：`baseline_*.py`

**代表实例**：`baseline_expanding_ridge_te6_lam0050`（IR=0.408，已晋升 mainline.json）

**作用**：当前被认可的生产方案。是 challenger 的比较对象（challenger 需要超越 baseline 才有晋升意义）。

**规则**：
- 通过三项硬指标后可晋升 mainline.json
- mainline.json 里有且只有一个当前最优方案

### 2.3 challenger（挑战者）

**命名模式**：`challenger_*.py`

**当前状态**（来自 `registry/challengers.json`，共 6 个 + 1 个冻结基线）：

| 名称 | IR | 状态 |
|---|---|---|
| frozen_baseline_icir_topn50_ew | 0.924 | frozen_baseline |
| rolling36 | 0.966 | researching |
| rolling48 | 1.489 | researching |
| rolling60 | 1.176 | researching |
| decay_ridge_hl24 | 0.626 | researching（IR PASS）|
| decay_ridge_hl36 | 0.295 | researching（IR FAIL）|
| decay_ridge_hl48 | 0.289 | researching（IR FAIL）|

**注意**：上表数据来自 `registry/challengers.json`，使用前以文件为准（本文档只是记录时的快照）。

**规则**：challenger 不会自动晋升；需要人工确认后运行 `promote_run.py`。

### 2.4 ablation（消融实验）

**命名模式**：`ablation_*.py`

**作用**：剔除单个因子或模块，测量其对 IR 的净贡献。**一次消融只改一个变量**，否则无法归因。

**当前已完成的消融**：
- `ablation_no_piotroski_f`：剔除 piotroski_f 后 IR=0.524（升）
- `ablation_no_high_52w_v2`：剔除 high_52w_v2 后结果见 experiment_board
- `ablation_no_piotroski_high52w`：两者同剔 IR=0.430

**结论示例**：piotroski_f IR 升 → 该因子为 REMOVE_CANDIDATE（弱化信号，移除后改善）。

### 2.5 test_run（测试集运行）

**命名规则**：`period_scope="test_run_N"`，且 `allow_test_set=True`

**最严格的纪律约束**：
- 整个项目生命周期内最多运行 **2 次**
- 每次运行后 git commit message 必须含 `[TEST_SET_RUN_N]` 标记
- 权威计数来自 `docs/logs/test_set_runs.json`（详见 P6 文档）
- 当前已用：**0 次**，剩余 3 次

---

## 三、完整治理流程

```
┌────────────────────────────────────────────────────────────────┐
│                    研究想法形成                                 │
└──────────────────────────┬─────────────────────────────────────┘
                           ↓
         docs/research/improve/progress_log.md
         （确认历史诊断结论，避免重复排查）
                           ↓
         reports/experiment_board.md
         （了解当前各 run 的指标分布，确认问题量级）
                           ↓
┌────────────────────────────────────────────────────────────────┐
│  写实验 Spec（configs/pipelines/*.py）                          │
│  单一变量原则：一次 Spec 只改一个维度                            │
└──────────────────────────┬─────────────────────────────────────┘
                           ↓
         python -m scripts.run_experiment --spec <path>
         （Layer 3→4→5 依次执行，产物写入 runs/train_valid/<run_id>/）
                           ↓
         python -m scripts.compare_runs \
             --runs <run_id_1> <run_id_2> ... \
             --baseline 20260529_034947__frozen_baseline_icir_topn50_ew
         （生成 reports/experiment_board.md，含 PASS/FAIL 标注）
                           ↓
┌────────────────────────────────────────────────────────────────┐
│  判断：三项硬指标全 PASS？                                       │
│  IR ≥ 0.5  AND  超额 MDD ≤ 10%  AND  换手率 500%-1500%          │
└──────────────────────────┬──────────────────┬──────────────────┘
                    PASS ↓                  FAIL ↓
         python -m scripts.promote_run  记录结论
         （写入 registry/mainline.json）  更新 challengers.json
         更新 CLAUDE.md §1.1             更新 progress_log.md
```

---

## 四、注册表文件详解

### 4.1 registry/challengers.json

记录所有已注册挑战者的状态和指标。**唯一权威来源**用于判断"当前哪些实验是活跃的"。

数据结构（每个 challenger 的字段）：

```json
{
  "name": "rolling48",                          // 实验名（对应 spec experiment_id）
  "run_id": "20260527_142056__...",             // 完整 run_id（含时间戳）
  "scope": "train_valid",                       // 数据范围
  "status": "researching",                      // frozen_baseline / promoted / researching / rejected
  "registered_at": "2026-05-27",               // 注册日期
  "metrics_v2": {                               // 六项核心指标
    "information_ratio": 1.489,
    "excess_return": 0.0863,
    "excess_max_drawdown": -0.0582,
    "tracking_error": 0.058,
    "monthly_win_rate": 0.565,
    "annual_turnover_pct": 1002
  },
  "notes": "..."                                // 结论备注
}
```

**六项指标的来源**：`runs/<scope>/<run_id>/backtest/performance.json`（由 `metrics.py` 计算）。

### 4.2 registry/mainline.json

记录已晋升的主线方案（`promote_run.py` 写入）。当前已晋升：

```json
// 实际内容以文件为准，以下仅示意结构
{
  "current_mainline": {
    "run_id": "...",
    "experiment_id": "baseline_expanding_ridge",
    "information_ratio": 0.408,
    "promoted_at": "...",
    "notes": "..."
  }
}
```

---

## 五、experiment_board（比较板）

`reports/experiment_board.md` 和 `.csv` 是 `compare_runs.py` 的输出，是多实验横向比较的主要界面。

**必须包含 frozen_baseline**：`compare_runs.py` 要求传入 frozen_baseline 的 run_id 作为下限参照，防止出现"新方案比之前的方案好但绝对值很低"的误判。

**六列指标**：IR / 年化超额 / 超额 MDD / 跟踪误差 / 月度胜率 / 年化换手率

**三项 PASS/FAIL 标注**：IR ≥ 0.5 / 超额 MDD ≤ 10% / 换手率 500%-1500%

---

## 六、单一变量实验原则

这是最容易被违反的设计原则，对研究结论有决定性影响：

**错误做法**：在一次实验里同时换信号方法（IC_IR → Ridge）+ 换优化器（QP → TopN EW）+ 调整 TE 目标。IR 变化了，但不知道是哪个因素贡献的。

**正确做法**：
```
实验 1：只换信号（固定 QP 优化器）
实验 2：只换优化器（固定相同信号）→ 对比 1 和 2 可分离"信号贡献"和"优化器贡献"
实验 3：两者都换（如果 1+2 都有提升，再整合验证无交叉效应）
```

这就是为什么 `configs/pipelines/` 里有 `rolling48_ridge_topn50_ew` 和 `rolling48_ridge_te6_lam0050` 两个实验：前者 TopN EW，后者 QP，信号相同，优化器不同，专门用来隔离优化器影响。

---

## 七、为何两个"基线"概念不同

| 术语 | 对应 | 含义 |
|---|---|---|
| **冻结基线**（frozen_baseline）| `frozen_baseline_icir_topn50_ew` | 永不改变的对照锚，用于验证"是否真的有改进" |
| **主线基线**（mainline）| `registry/mainline.json` 中的当前最优 | 当前被认可的生产方案，可以被更好的 challenger 替换 |

冻结基线从不晋升，主线基线可以迭代更新。比较 IR 时：
- `新方案 IR > 冻结基线 IR`：说明相比固定参照有绝对改进
- `新方案 IR > 主线基线 IR`：说明相比当前生产方案有改进
- 两个条件都需要满足才考虑晋升

---

## 八、时间区间隔离与测试集纪律

### 8.1 三段式时间切分（来自 src/config.py）

| 时段 | 起止 | 用途 |
|---|---|---|
| 训练期 | 2012-01-01 ~ 2020-12-31 | 因子评估、超参数调优 |
| 验证期 | 2021-01-01 ~ 2022-12-31 | 稳健性确认（allow_test_set=False 的 run 覆盖此段）|
| 测试期 | 2023-01-01 ~ 2025-12-31 | 最终评估，≤ 3 次 |

`period_scope="train_valid"` 的 run 覆盖训练期+验证期，是日常实验的标准设置。

### 8.2 测试集运行计数

测试集剩余运行次数的权威来源是 `docs/logs/test_set_runs.json`，不是 CLAUDE.md（CLAUDE.md 只是副本，容易漂移）。每次测试集运行后必须更新此文件，详见 `P6_test_pipeline_guardrails.md`。
