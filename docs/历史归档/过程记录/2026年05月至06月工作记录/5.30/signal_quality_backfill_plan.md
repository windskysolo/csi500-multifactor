# 信号转化持仓方式对比：TC 诊断回填计划

> **目的**：通过 Transfer Coefficient（TC）等指标，归因"信号 ICIR 更高的策略实际 IR 反而更低"的悖论。  
> **方法**：对所有已完成的 run 补跑 `SignalToPositionDiagnostics`，在 compare_runs 中对比 TC/truncation_loss/ir_loss_pct。  
> **日期**：2026-05-30  
> **状态**：待执行

---

## 0. 核心问题

实验板中存在以下悖论：

| 信号类型 | 持仓方式 | IC_IR（信号层）| 实际超额 IR |
|----------|---------|--------------|---------|
| ICIR expanding | TopN50 EW | 0.461 | **0.924** |
| ICIR expanding | QP TE=6% | 0.461 | 0.402 |
| Expanding Ridge | QP TE=6% | **0.628** | 0.408 |
| Rolling48 Ridge | QP TE=6% | 0.348 | **1.489** |

同一信号、不同持仓方式，IR 差距 2 倍以上；信号层 ICIR 更高的 expanding ridge QP，IR 反而输给 ICIR QP。  
TC 诊断体系已实现（`src/evaluation/signal_quality.py` + `run_diagnosis_stage`），需回填现有 **12 个** run。

> ⚠ **口径说明**：`signal_quality_report.json` 中的 `ir_actual` 是原始组合收益的 Sharpe（未扣基准），
> 与 `metrics_valid.parquet` 中的**超额 IR** 不可直接对比。
> `ir_loss_pct` 衡量"原始组合 Sharpe 与 GK 理论值的差距"，不直接等于超额 IR 的损耗来源。
> 本次分析用 `ir_loss_pct` 做跨 run 的**相对比较**，绝对值不做跨策略类型（QP vs TopN）的直接解读。

---

## 1. 信号-权重对应关系（已确认）

所有 TopN50 EW runs 和 ICIR 变体 runs 均通过 `--from-stage portfolio` 跑出，
`signal/` 目录为空，信号文件来自各自 `inputs.lock.json` 指定的源 run。

### Group A：ICIR expanding 信号（sha256=`23024863`）

信号权威文件：`runs/train_valid/20260528_133249__baseline_icir_te6_lam0050/signal/composite.parquet`

| run_id | 持仓方式 | IR | 已有 signal_quality.json |
|--------|---------|-----|--------------------------|
| `20260529_034947__frozen_baseline_icir_topn50_ew` | TopN50 EW | 0.924 | ✅（已有，回填跳过）|
| `20260528_133249__baseline_icir_te6_lam0050` | QP TE=6% | 0.402 | ❌ |
| `20260529_145209__icir_l2_forced` | L2 forced | 0.356 | ❌ |
| `20260529_145838__icir_te10_lam0050` | QP TE=10% | 0.356 | ❌ |
| `20260529_150439__icir_te15_lam0050` | QP TE=15% | 0.356 | ❌ |
| `20260529_151545__icir_te6_noind` | QP 无行业约束 | 0.240 | ❌ |
| `20260529_150951__icir_te6_ind5pct` | QP 行业±5% | 0.146 | ❌ |

### Group B：Expanding Ridge 信号（sha256=`40cb47bc`）

信号权威文件：`runs/train_valid/20260527_141545__baseline_expanding_ridge_te6_lam0050/signal/composite.parquet`

| run_id | 持仓方式 | IR | 已有 signal_quality.json |
|--------|---------|-----|--------------------------|
| `20260527_141545__baseline_expanding_ridge_te6_lam0050` | QP TE=6% | 0.408 | ❌ |
| `20260529_081253__expanding_ridge_topn50_ew` | TopN50 EW | 0.574 | ❌ |

### Group C：Rolling48 Ridge 信号（sha256=`a1e6893b`）

信号权威文件：`runs/train_valid/20260527_142056__challenger_rolling48_te6_lam0050/signal/composite.parquet`

| run_id | 持仓方式 | IR | 已有 signal_quality.json |
|--------|---------|-----|--------------------------|
| `20260527_142056__challenger_rolling48_te6_lam0050` | QP TE=6% | 1.489 | ❌ |
| `20260529_081232__rolling48_ridge_topn50_ew` | TopN50 EW | 1.771 | ❌ |

### Group D：Decay HL24 Ridge 信号（sha256=`84abd1c3`）

信号权威文件：`runs/train_valid/20260528_141041__challenger_decay_ridge_hl24_te6_lam0050/signal/composite.parquet`

| run_id | 持仓方式 | IR | 已有 signal_quality.json |
|--------|---------|-----|--------------------------|
| `20260528_141041__challenger_decay_ridge_hl24_te6_lam0050` | QP TE=6% | 0.626 | ❌ |
| `20260529_081310__decay_hl24_ridge_topn50_ew` | TopN50 EW | 1.057 | ❌ |

---

## 2. 实现步骤

### Step 1：新建 `scripts/backfill_signal_quality.py`

> ✅ **脚本已创建**，直接执行 Step 2 即可。

改进说明（相比初版）：
- 绝对路径存在性校验：lock 文件中存储绝对路径，若路径失效会打印清晰错误而非静默失败
- 统一计数为 12 个 run

### Step 2：运行回填

```powershell
cd "e:\Acoding\Project\500 improve"
python scripts/backfill_signal_quality.py
```

预计耗时：每个 run 约 5–15 秒，共 12 个（1 个已有跳过），总计约 2–3 分钟。

### Step 3：生成分组对比表

**Group A（最核心）**：同一信号（ICIR expanding），不同持仓方式对比

```powershell
python -m scripts.compare_runs `
  20260529_034947__frozen_baseline_icir_topn50_ew `
  20260528_133249__baseline_icir_te6_lam0050 `
  20260529_145209__icir_l2_forced `
  20260529_145838__icir_te10_lam0050 `
  20260529_150439__icir_te15_lam0050 `
  20260529_151545__icir_te6_noind `
  20260529_150951__icir_te6_ind5pct
```

**Group B/C/D**：跨信号类型对比（含冻结基线作为下限参照）

```powershell
python -m scripts.compare_runs `
  20260529_034947__frozen_baseline_icir_topn50_ew `
  20260527_141545__baseline_expanding_ridge_te6_lam0050 `
  20260529_081253__expanding_ridge_topn50_ew `
  20260527_142056__challenger_rolling48_te6_lam0050 `
  20260529_081232__rolling48_ridge_topn50_ew `
  20260528_141041__challenger_decay_ridge_hl24_te6_lam0050 `
  20260529_081310__decay_hl24_ridge_topn50_ew
```

---

## 3. 关键分析维度

### 3.1 TC 的跨类型比较注意事项

TC 的绝对范围受持仓权重形式影响：

| 持仓方式 | TC 结构上限 | 权重类型 | TC 含义 |
|---------|-----------|---------|---------|
| TopN50 EW | ~0.4–0.6 | 二值权重（0 或 1/N）| 信号排序 → 是否入选 top-50 |
| QP/L2 | ~0.7–0.9 | 连续权重（总持仓）| 信号值 → 总持仓权重 |

**重要局限**：QP 的 `target_weights` 是总持仓权重（≈ 基准权重 + 主动偏离）。TC 度量的是信号与**总权重**的秩相关，而非信号与**主动权重（总权重 - 基准权重）**的相关。由于基准权重（CSI 500 成分权重）的截面方差不由信号驱动，这会使 QP 的 TC 低估真实的信号转化能力。因此：

- **同类型内部**（7 个 Group A ICIR 变体，均为 QP/L2）：TC 越高 → 信号实现越好，比较有效
- **跨 QP vs TopN**：TC 绝对值不可直接比较，用 `ir_loss_pct` 配合超额 IR 综合判断

**结论**：TC_QP > TC_TopN 不等于"QP 实现更好"。

### 3.2 核心假设与验证

| 假设 | 验证指标 | 预期结果 | 备注 |
|------|---------|---------|------|
| H1：TopN50 EW 截断信号多，但截断的是低信号区噪声 | `truncation_loss`（预期 ~74%）| TopN 截断比 QP 多 ~50pp | 可与冻结基线已知值对比 |
| H2：Group A 内部，QP 各变体的 `ir_actual` 偏差来源 | `ir_loss_pct`（同一信号内部比较）| TE 宽松→ TC 不变，行业约束放松 → IR 下降 | ⚠ `ir_actual` 是原始组合 Sharpe 非超额 IR，仅做组内相对比较 |
| H3：QP TE 约束松紧不影响 TC | `tc_mean`（icir_te6/10/15 对比）| TC 相近（约束是保护机制，不改变信号跟踪）| 已有诊断结论作为预期 |
| H4：无行业约束（noind）的 TC 最高但 IR 最低 | `tc_mean` 和超额 `IR` | TC 更高但行业暴露使回撤增大 | 需用超额 IR 而非 ir_actual |

### 3.3 已知基准（frozen_baseline TopN50 EW）

```
TC              = 0.585   ← TopN50 EW 的结构上限附近
truncation_loss = 74.1%   ← 74% 信号未进入持仓（仅保留 top 50/500）
ir_theoretical  = 0.935   ← GK 理论值（ICIR × TC × √freq）
ir_actual       = 0.361   ← 原始组合 Sharpe（≠ 超额 IR 0.924，未扣基准）
ir_loss_pct     = 61.4%   ← GK 简化公式已知低估，负值属正常
```

---

## 4. 验证 compare.py 是否已更新

运行前先确认 TC 列存在：

```powershell
python -c "
from src.pipeline.compare import load_run_metrics
r = load_run_metrics('20260529_034947__frozen_baseline_icir_topn50_ew')
print({k: r.get(k) for k in ['tc_mean', 'n_eff', 'ir_loss_pct']})
"
# 预期: {'tc_mean': 0.585, 'n_eff': 66.0, 'ir_loss_pct': 61.4}
```

若 `tc_mean` 为 `KeyError` 或 `NaN`，说明 compare.py 尚未更新 `_load_signal_quality` 和 `col_order`，需先按 `signal_quality_integration_plan.md` §5 实施。

---

## 5. 执行顺序检查清单

```
[ ] 1. 确认 compare.py 已有 tc_mean/n_eff/ir_loss_pct 列（验证命令见 §4）
[ ] 2. 运行：python scripts/backfill_signal_quality.py
[ ] 3. 确认日志输出 "完成 12/12"（1 个已有跳过，11 个新生成）
[ ] 4. 确认 12 个 run 的 reports/signal_quality_report.json 全部存在
[ ] 5. 运行 Group A compare_runs（含冻结基线，共 7 个 run）
[ ] 6. 运行 Group B/C/D compare_runs（含冻结基线，共 7 个 run）
[ ] 7. 分析结论（注意 ir_loss_pct 口径限制，见 §0 和 §3.2 备注）
[ ] 8. 记录结论到 docs/research/improve/progress_log.md
```

---

## 6. 预期输出样式

Group A 对比表关键列（预期数值方向，具体值需跑出）：

```
持仓方式       | 超额IR | TC     | truncation | ir_loss | N_eff
              |（来自metrics）|（signal_quality）| | |
TopN50 EW     | 0.924 | ~0.585 |   ~74%     |  ~61%   |  66
QP TE=6%      | 0.402 |  ?高   |   ~20%     |  ?      |  ?
L2 forced     | 0.356 |  ?     |   ?        |  ?      |  ?
QP TE=10%     | 0.356 |  ?     |   ?        |  ?      |  ?
QP TE=15%     | 0.356 |  ?     |   ?        |  ?      |  ?
QP 无行业     | 0.240 |  ?最高 |   ?        |  ?      |  ?
QP 行业±5%    | 0.146 |  ?     |   ?        |  ?      |  ?
```

**解读框架**：
- Group A 内部 QP 变体的 TC 均来自连续权重，可直接互比
- `ir_loss_pct` 组内排序可说明"哪种 QP 设置信号转化损耗更大"
- 要解释"TopN 超额 IR 为何远高于 QP"，需结合超额 IR、换手率、TE 三个指标综合判断，不能单靠 `ir_loss_pct`
