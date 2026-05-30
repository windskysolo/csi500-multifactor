# 信号实现质量诊断系统 — 实施记录

> **实施日期**：2026-05-29 / 2026-05-30  
> **依赖计划**：`current work/5.29/plans/signal_quality_integration_plan.md`  
> **状态**：全部 4 步实施完成，50 项单元测试 PASS

---

## 一、实施前计划审查与纠错

在正式实施前，对计划文件进行了严格审查，发现并修正以下错误：

| # | 错误位置 | 错误内容 | 修正 |
|---|---------|---------|------|
| 1 | Step 3 描述 | 称 import 块在 `try` 块**内部** | 实际在 try 块**外部**（L205-208），修正描述 |
| 2 | Step 3 代码示例 | 新 try 块内有冗余的本地 `from src.pipeline.stages import run_diagnosis_stage` | 删除（已在顶层 import，重复会覆盖） |
| 3 | 全文 `min_stocks` | 值为 `20` | 改为 `10`（与 `compare.py._compute_ic_stats` 的 `< 10` 阈值保持一致） |
| 4 | `run()` 代码逻辑 | 先做日期过滤再判断空值 | 修正为**先判断空值**再过滤（空 DataFrame 的 RangeIndex 无法与 Timestamp 比较，会抛 TypeError） |
| 5 | `ir_actual` 文档 | 未说明与超额 IR 的区别 | 补充：`ir_actual` 是原始组合收益 IR（未扣基准），不等于 `metrics_valid` 的超额 IR |
| 6 | TC 跨策略比较 | 无注意事项 | 补充：TopN EW 二值权重 TC 结构上限 0.4–0.6，QP 连续权重上限 0.7–0.9，两者不宜直接比较 |

---

## 二、四步实施详情

### Step 1 — 新建 `src/evaluation/signal_quality.py`

**核心内容：**

- `SignalQualityReport` dataclass：15 个字段（信号层 / 转移层 / 广度层 / 归因层 / 元信息），`to_dict()` 保留 NaN、float 精度 4 位
- `SignalToPositionDiagnostics` 类，`min_stocks=10`，包含四个诊断方法：

| 方法 | 计算内容 |
|------|---------|
| `_diagnose_signal` | IC 均值/方差/ICIR/t统计量、五分位分层单调性 |
| `_diagnose_transfer` | TC（Spearman 秩相关）、幅度保真度、截断信号损耗 |
| `_diagnose_breadth` | N_eff（Herfindahl 倒数）、平均持仓数、集中度 |
| `_diagnose_attribution` | GK 理论 IR（ICIR×TC×√freq）、实际组合 IR、IR 损耗百分比 |

- `run()` 主入口：验证期过滤 → 四步诊断 → 返回 `SignalQualityReport`；内部 try/except，任何异常返回全 NaN 报告，不向外传播
- `format_report_section()`：生成 Markdown 格式诊断节，含自动结论判断（低 TC / 广度虚高 / 信号单调性差 / 健康）

**相较计划的微调：** 删除计划中的 `import json`（文件内无 JSON 操作，序列化在 `stages.py` 完成）

---

### Step 2 — 修改 `src/pipeline/stages.py`

**位置：** 在 `_write_signal_metadata` 函数之前插入（原 L492，现 L496）

**新增函数 `run_diagnosis_stage(spec, run_dir, data_proc=None)`：**

- 输入：`signal/composite.parquet`、`portfolio/target_weights.parquet`、`data_proc/fwd_ret_panel.parquet`
- 输出：`reports/signal_quality_report.json`（结构化 JSON）、追加到 `reports/self_check.md`（幂等：检查"Signal Quality Diagnostics"防重复）
- 任意输入文件缺失时记录 warning 并返回 `{}`，不抛异常
- 验证期使用 `cfg.VALID_START / cfg.VALID_END`（与 `compare.py` IC 统计口径统一）

---

### Step 3 — 修改 `scripts/run_experiment.py`

**改动 1**：顶层 import 块（L205-208）加入 `run_diagnosis_stage`：
```python
from src.pipeline.stages import (
    run_signal_stage, run_portfolio_stage, run_backtest_stage,
    write_self_check_md, run_diagnosis_stage,   # ← 新增
)
```

**改动 2**：在 `write_self_check_md` 块之后、`log.info("="×60)` 之前追加非阻断诊断调用：
```python
# ── 信号实现质量诊断（非阻断，依赖 self_check.md 已存在）─────────────
if "backtest" in active_stages:
    try:
        log.info("── 生成 reports/signal_quality_report.json ──")
        run_diagnosis_stage(spec, ctx.run_dir, data_proc=cfg.DATA_PROC)
    except Exception as e:
        log.warning("signal quality diagnosis 失败（不阻断）: %s", e)
```

**执行顺序保证**：`write_self_check_md` → `run_diagnosis_stage`（诊断追加依赖 self_check.md 已存在，顺序正确）

---

### Step 4 — 修改 `src/pipeline/compare.py`

**改动 1**：新增 `_load_signal_quality(run_dir)` helper（在 `_compute_paired_p` 之后）：
- 读取 `reports/signal_quality_report.json`
- 返回 `{"tc_mean": ..., "n_eff": ..., "ir_loss_pct": ...}`
- 文件缺失 / 字段缺失 / JSON 损坏均返回全 NaN（旧 run 向后兼容）

**改动 2**：`load_run_metrics()` 中在 `_compute_ic_stats` 调用后加入：
```python
row.update(_load_signal_quality(run_dir))
```

**改动 3**：`compare_runs()` 的 `col_order` 列表中，在 `"IC_p"` 之后插入三列：
```python
"tc_mean", "n_eff", "ir_loss_pct",
```

---

## 三、验证结果

### 3.1 计划 §8.2 快速验证（四步 import / 语法检查）

```
Step1 OK  — from src.evaluation.signal_quality import ...
Step2 OK  — from src.pipeline.stages import run_diagnosis_stage
Step3 OK  — python -m py_compile scripts/run_experiment.py
Step4 OK  — python -m py_compile src/pipeline/compare.py
```

### 3.2 对已有 run 补跑诊断验证

对 `runs/train_valid/20260529_034947__frozen_baseline_icir_topn50_ew` 运行 `run_diagnosis_stage`：

| 字段 | 值 | 备注 |
|------|-----|------|
| `ic_mean` | 0.0471 | |
| `icir` | 0.461 | 接近 CLAUDE.md 记录的 IC_IR |
| `monotonicity` | 0.700 | 信号层健康 |
| **`tc`** | **0.585** | **TopN EW 二值权重结构上限内（0.4–0.6），正常** |
| `amplitude_fidelity` | 0.473 | |
| `truncation_loss` | 74.1% | TopN50/500 ≈ 90% 信号被截断，符合预期 |
| `n_holdings` | 66 | |
| `n_eff` | 66.0 | ≈ n_holdings，等权持仓 N_eff = N，公式验证通过 |
| `ir_theoretical` | 0.935 | ICIR(0.461) × TC(0.585) × √12 |
| `ir_actual` | 0.361 | 原始组合 IR（非超额，偏低正常） |
| `ir_loss_pct` | 61.4% | 理论到实际有损耗，主要来自截断 |
| `n_valid_periods` | 24 | 验证期 2021-2022，24 个月，正确 |

产物位置：
- `runs/train_valid/20260529_034947__frozen_baseline_icir_topn50_ew/reports/signal_quality_report.json` ✅
- `runs/train_valid/20260529_034947__frozen_baseline_icir_topn50_ew/reports/self_check.md`（诊断节已追加）✅

### 3.3 compare_runs 列验证

```python
compare_runs([
    "20260529_034947__frozen_baseline_icir_topn50_ew",
    "20260527_141545__baseline_expanding_ridge_te6_lam0050",
])
```

结果：32 列，`tc_mean / n_eff / ir_loss_pct` 位于 `IC_p` 与 `fallback_L0_cnt` 之间，顺序正确。
旧 run（无 JSON 文件）三列自动为 NaN，向后兼容 ✅

### 3.4 单元测试

新建 `tests/test_signal_quality.py`，50 项测试全部 PASS：

| 测试组（编号） | 覆盖内容 | 用例数 |
|--------------|---------|--------|
| SQ-001 | `to_dict()` NaN/精度/int/JSON可序列化/字段完整 | 5 |
| SQ-002 | `_diagnose_signal` 强因子/噪声/不足股票/t统计公式/单调性 | 5 |
| SQ-003 | `_diagnose_transfer` TC范围/随机vs TopN/完美对齐/截断损耗/无日期交集 | 5 |
| SQ-004 | `_diagnose_breadth` 等权N_eff=N/单只=1/集中vs分散/空权重 | 4 |
| SQ-005 | `_diagnose_attribution` GK公式/NaN传播/正实际IR/无日期交集/损耗方向 | 5 |
| SQ-006 | `run()` 空输入/不足股票/无数据/日期过滤/异常不传播 | 5 |
| SQ-007 | `run()` 正常路径 n_valid/IC字段/TC范围/持仓数/N_eff/理论IR/截断 | 7 |
| SQ-008 | `format_report_section` 节标题/四步/NaN→N/A/低TC警告/健康结论/无数据 | 6 |
| SQ-009 | `_load_signal_quality` 缺失/正常/字段缺失/损坏JSON/null值 | 5 |
| SQ-010 | `run_diagnosis_stage` 缺文件跳过/JSON写入/self_check追加不重复 | 3 |

---

## 四、新增/修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/evaluation/signal_quality.py` | **新建** | 信号质量诊断核心模块 |
| `src/pipeline/stages.py` | **修改** | 在 `_write_signal_metadata` 前插入 `run_diagnosis_stage` |
| `scripts/run_experiment.py` | **修改** | 顶层 import 加 `run_diagnosis_stage`；backtest 后追加诊断调用 |
| `src/pipeline/compare.py` | **修改** | 新增 `_load_signal_quality`；`load_run_metrics` 加一行；`col_order` 加三列 |
| `tests/test_signal_quality.py` | **新建** | 50 项单元测试，全覆盖四个诊断层 + 集成路径 |

---

## 五、向后兼容性说明

- 旧 run 目录没有 `reports/signal_quality_report.json` 时，`compare_runs` 三个新列自动显示 NaN，不影响其他列
- `run_diagnosis_stage` 失败（文件缺失或运行时异常）不影响 run 的 `RUN_FINISHED.json` 状态
- `self_check.md` 的诊断节追加有幂等保护，重复运行不会产生重复内容

---

## 六、已知限制（暂不实现）

| 限制 | 原因 | 条件 |
|------|------|------|
| TC 无波动率调整（`h∝z/σ²`） | 无 `sigma_panel.parquet` | 构建日频波动率面板后升级 |
| 旧 run 批量补生成 JSON | 当前无需求 | 可新增 `scripts/backfill_diagnosis.py` |
| 行业/风格集中度诊断 | 需要行业映射和风格载荷 | 作为广度层后续增强 |
