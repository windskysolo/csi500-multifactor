# 冻结基线引入计划

> 创建日期：2026-05-28  
> 目标：新增一条永不变化的冻结基线（ICIR加权 + TopN排序种子 + L3约束感知等权，无 QP），并统一所有指导文档中的口径。

---

## 背景与目的

当前研究框架中，"主基线"会随实验推进而晋升更新，导致每次实验没有稳定的对照物，无法严格控制变量。

引入冻结基线解决这个问题：

| 角色 | 定义 | 是否可变 |
|------|------|---------|
| **冻结基线** | ICIR加权信号 + TopN=50排序种子 + L3约束感知等权，无 QP | **永远不变** |
| 主基线 | 当前最优信号策略 | 可通过实验晋升 |
| 挑战者 | 正在测试的实验 | 随实验进展变化 |

冻结基线的价值：最简单、最可解释（无超参可调），任何改进都必须先超过它才算有意义。

---

## 现状确认

执行前核对以下事实（已确认）：

- `baseline_icir_te6_lam0050.py` 已存在（IR=0.402），但它**仍走完整 QP 优化器**，不是冻结基线
- `src/signal/combiner.py` 已支持 `method="icir"`，无需改动
- `src/portfolio/optimizer.py` 的 L3 已实现 TopN 等权逻辑，必须复用其停牌/涨跌停/单股偏离处理，禁止另写简化版 TopN
- `src/pipeline/contracts.py` 的 `OptimizerSpec` 需要 `optimizer_mode` 字段并校验取值
- `src/pipeline/stages.py` 只负责将 `OptimizerSpec` 映射到组合脚本调用，**不应承载选股/约束金融逻辑**

---

## 执行步骤（按顺序）

### Step 1：`src/pipeline/contracts.py` — 新增字段

在 `OptimizerSpec` 末尾加一个字段：

```python
@dataclass
class OptimizerSpec:
    te_target_annual: float = 0.06
    industry_max_dev: float = 0.03
    single_max_dev: float = 0.015
    turnover_lambda: float = 0.005
    topn: int = 50
    optimizer_mode: str = "qp"      # ← 新增："qp" = 走 L1/L2/L3；"topn_ew" = 跳过 QP，复用 L3 约束感知等权
```

**注意**：
- 默认值 `"qp"` 保证所有已有 Spec 文件无需改动，向下兼容。
- `OptimizerSpec.__post_init__` 必须拒绝未知 `optimizer_mode`，防止拼写错误静默走错路径。

---

### Step 2：组合层新增 topn_ew 模式（不在 stage 内写金融逻辑）

实现边界：
- `src/pipeline/stages.py` 只读取 `spec.optimizer.optimizer_mode`，并原样传给 `scripts.run_portfolio_optimization.main(...)`。
- `scripts/run_portfolio_optimization.py` 负责在 `optimizer_mode == "topn_ew"` 时跳过协方差估计和 QP 主循环。
- `src/portfolio/optimizer.py` 新增批量函数，内部必须复用现有 `_topn_equal_weight(...)`，不得用 `nlargest(topn)` 后简单等权替代。

核心调用形态：

```python
weights_panel, meta_df = optimize_topn_equal_weight_all_periods(
    composite_panel=composite_signal,
    benchmark_weights=benchmark_weights_dict,
    rebalance_dates=available_dates,
    config=optimizer_config,
    halt_dict=halt_dict,
    limit_up_dict=limit_up_dict,
    limit_dn_dict=limit_dn_dict,
)
```

**要点**：
- 停牌股必须锁定上期目标权重；不能简单过滤后置 0。
- 涨停股不得新增买入；跌停股不得减仓。
- 如果单股偏离约束要求有效持仓数超过 `topn=50`，允许 L3 逻辑动态扩容，并在 `n_holdings` 中记录；因此该模式含义是“Top50 作为排序种子，约束感知等权”，不是裸 Top50。
- `target_weights.parquet` 与 `baseline_weights.parquet` 在该模式下写成相同权重，避免回测额外运行一个未审计的 V1 权重路径。
- `optimizer_meta.parquet` 中 `fallback_level` 填 `2`（当前项目 L3 语义为 0/1/2，不存在 3），`solver_status="topn_ew_forced"`，`optimizer_mode="topn_ew"`，并必须包含 `constraint_compliant`。

---

### Step 3：新建 Spec 文件

路径：`configs/pipelines/frozen_baseline_icir_topn50_ew.py`

```python
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="frozen_baseline_icir_topn50_ew",
    description=(
        "Frozen baseline: ICIR signal plus forced TopN equal-weight portfolio. "
        "No QP optimizer is used; the portfolio path reuses L3 halt/limit handling."
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="icir",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
    ),
    optimizer=OptimizerSpec(
        optimizer_mode="topn_ew",  # 跳过 QP，复用 L3 约束感知等权
        topn=50,
    ),
    backtest=BacktestSpec(),
)
```

---

### Step 4：运行实验

```bash
python -m scripts.run_experiment --spec configs/pipelines/frozen_baseline_icir_topn50_ew.py
```

运行完成后：
1. 记录 `run_id`（格式：`YYYYMMDD_HHMMSS__frozen_baseline_icir_topn50_ew`）
2. 打开 `runs/train_valid/<run_id>/reports/self_check.md`，记录以下指标：
   - IR
   - 超额收益
   - 超额最大回撤
   - 年化双边换手率

---

### Step 5：注册冻结基线

**不走** `promote_run.py`（那是主基线晋升用的）。直接手动编辑 `registry/challengers.json`，在 `challengers` 数组**最前面**插入：

```json
{
  "name": "frozen_baseline_icir_topn50_ew",
  "run_id": "<填入实际 run_id>",
  "scope": "train_valid",
  "status": "frozen_baseline",
  "registered_at": "2026-05-28",
  "metrics_v2": {
    "information_ratio": "<填入>",
    "excess_return": "<填入>",
    "excess_max_drawdown": "<填入>",
    "tracking_error": "<填入>",
    "monthly_win_rate": "<填入>",
    "annual_turnover_pct": "<填入>"
  },
  "notes": "冻结基线：ICIR加权 + TopN=50排序种子 + L3约束感知等权，无 QP。永不晋升、永不重跑。所有实验的不变对照锚。"
}
```

`"frozen_baseline"` 是新增的 `status` 值，与 `"researching"` 并列但语义不同：前者永久存在，后者是进行中的研究。

---

### Step 6：文档修改（5 处，按顺序执行）

---

#### 6.1 `CLAUDE.md` — §1.1 当前状态表

在表格中新增一行（放在"当前主基线 IR"行之前）：

```markdown
| 冻结基线 IR | <填入实际值>（frozen_baseline_icir_topn50_ew，永不晋升） |
```

完整表格变为：

| 字段 | 当前值 |
|------|--------|
| 因子池规模 | 17 个（含 rev_acceleration） |
| **冻结基线 IR** | **（填入）**（frozen_baseline_icir_topn50_ew，永不晋升） |
| 验证期最优 IR | 1.489（challenger_rolling48，未晋升） |
| 当前主基线 IR | 0.408（baseline_expanding_ridge） |
| ... | ... |

---

#### 6.2 `CLAUDE.md` — §6.0 实验纪律

在"实验纪律"列表末尾新增一条：

```markdown
- 每次运行 `compare_runs.py` 时，**必须**将 `frozen_baseline_icir_topn50_ew` 的 run_id 包含在比较列表中，作为所有实验结果的下限参照
```

---

#### 6.3 `docs/RESEARCH_GUIDE.md` — §六 当前实验状态

在实验状态表格中新增首行（置于所有其他行之前）：

```markdown
| frozen_baseline_icir_topn50_ew | **冻结基线**（永不晋升） | （填入） | （填入） | （填入） |
```

表格注释行之后新增说明：

```markdown
> **冻结基线说明**：`frozen_baseline_icir_topn50_ew` 是所有实验的不变对照锚，任何新实验的 IR 必须超过它才具有研究价值。它使用最简单可解释的方法（ICIR加权 + TopN排序种子 + L3约束感知等权，无 QP），永不重跑、永不修改。
```

---

#### 6.4 `docs/RESEARCH_GUIDE.md` — §八 experiment_id 命名规范

在"类型"说明块中新增一行：

```
类型：
  baseline        — 主基线候选
  frozen_baseline — 冻结基线，最简可解释方法（ICIR加权 + TopN等权），永不变更，不进晋升流程
  challenger      — 挑战者（需与主基线对比）
  grid            — 网格实验
  ablation        — 消融实验（去掉某个因子/模块）
  debug           — 调试用，不进注册表
```

---

#### 6.5 `docs/RESEARCH_GUIDE.md` — §九 不应该做的事

在禁止事项表格末尾新增一行：

```markdown
| 修改或重跑 `frozen_baseline_icir_topn50_ew` spec | 冻结基线是不可变对照锚，修改后所有历史对比失去意义 |
```

---

#### 6.6 `docs/FILE_MAP.md` — `configs/pipelines/` 文件表

在文件列表末尾新增一行：

```markdown
| `frozen_baseline_icir_topn50_ew.py` | ICIR加权 + TopN=50排序种子 + L3约束感知等权，无 QP | **冻结基线（永不修改）** |
```

---

#### 6.7 `docs/FILE_MAP.md` — 顶部快速定位表

新增一行：

```markdown
| 查冻结基线的回测结果 | `runs/train_valid/<frozen_baseline_run_id>/reports/self_check.md` |
```

---

#### 6.8 `docs/guides/improvement_guide.md` — 执行路线图顶部

在"执行路线图总览"的代码块**之前**插入如下说明框：

```markdown
> **研究前提：冻结基线**
>
> 所有阶段的 IR 改善，都以 **`frozen_baseline_icir_topn50_ew`**（ICIR加权 + Top50排序种子 + L3约束感知等权，无 QP）作为参照下限。  
> 冻结基线永不改变。若某个改进方向的最终 IR 低于冻结基线，说明引入的复杂度没有带来实质收益。
```

---

## 修改范围汇总

| 类别 | 文件 | 操作 | 影响范围 |
|------|------|------|---------|
| 代码 | `src/pipeline/contracts.py` | 新增字段 `optimizer_mode` 和取值校验 | 向下兼容，已有 Spec 不受影响 |
| 代码 | `src/pipeline/stages.py` | 将 `optimizer_mode` 传给组合脚本 | 不承载金融逻辑 |
| 代码 | `scripts/run_portfolio_optimization.py` | 新增 `topn_ew` 模式，跳过协方差和 QP | 仅 `optimizer_mode="topn_ew"` 时生效 |
| 代码 | `src/portfolio/optimizer.py` | 新增批量 TopN 等权函数，复用 L3 `_topn_equal_weight` | 停牌/涨跌停/合规 metadata 口径统一 |
| 配置 | `configs/pipelines/frozen_baseline_icir_topn50_ew.py` | 新建 | 一次运行后不再改动 |
| 测试 | `tests/test_pipeline_contracts.py`, `tests/test_optimizer.py` | 增加模式校验、传参、TopN 状态约束测试 | 防止回归 |
| 注册表 | `registry/challengers.json` | 插入新条目 | 追加，不修改已有条目 |
| 文档 | `CLAUDE.md` | §1.1 加行，§6.0 加一条实验纪律 | 追加 |
| 文档 | `docs/RESEARCH_GUIDE.md` | §六加行+说明，§八加命名类型，§九加禁止项 | 追加 |
| 文档 | `docs/FILE_MAP.md` | 配置表加行，快速定位表加行 | 追加 |
| 文档 | `docs/guides/improvement_guide.md` | 顶部加说明框 | 追加 |

代码改动为向后兼容增量修改；文档和注册表在跑出实际结果后再追加，不覆盖已有条目。

---

## 执行顺序

```
Step 1  修改 contracts.py（加字段和取值校验）
Step 2  修改 src/portfolio/optimizer.py（新增复用 L3 的批量 topn_ew 函数）
Step 3  修改 scripts/run_portfolio_optimization.py（新增 topn_ew 模式，写完整 artifacts/meta）
Step 4  修改 stages.py（只传递 optimizer_mode）
Step 5  新建 frozen_baseline_icir_topn50_ew.py
Step 6  跑单元测试和语法检查
Step 7  跑实验，记录 run_id 和指标
Step 8  更新 registry/challengers.json（填入 run_id 和指标）
Step 9  更新 CLAUDE.md（填入实际 IR 值）
Step 10 更新 docs/RESEARCH_GUIDE.md
Step 11 更新 docs/FILE_MAP.md
Step 12 更新 docs/guides/improvement_guide.md
```

Step 1-6 需要通过代码正确性验证后才执行 Step 7。  
Step 8-12 在 Step 7 出结果后填入实际数值再执行。
