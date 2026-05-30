# P5 — 端到端实验工作流 `scripts/run_experiment.py` + `compare_runs.py` + `promote_run.py`

---

## 一、文件定位

```
所属 Part  : Layer 7（实验脚本层）
在数据流中 : configs/pipelines/*.py → run_experiment.py → run_dir → compare_runs.py → promote_run.py
被谁调用   : 用户从命令行调用（python -m scripts.xxx）
调用谁（run_experiment.py）:
  src/pipeline/contracts.py  （ExperimentSpec.from_config_file / RunContext.create）
  src/pipeline/artifacts.py  （ArtifactLayout / file_sha256）
  src/pipeline/stages.py     （run_signal_stage / run_portfolio_stage / run_backtest_stage / write_self_check_md）
调用谁（compare_runs.py）:
  src/pipeline/compare.py    （compare_runs / build_board_from_registry）
调用谁（promote_run.py）:
  src/pipeline/registry.py   （promote_run）
```

**实际行数**：`run_experiment.py` = 255 行，`compare_runs.py` = 155 行，`promote_run.py` = 95 行

---

## 二、时间对齐与 PIT 假设

- `run_experiment.py` 不直接处理时序数据，时间范围由被调用的 Stage 函数控制（训练/验证期 ≤ `cfg.VALID_END`）
- `compare_runs.py` 计算 IC 统计时限定在验证期（`cfg.VALID_START ~ cfg.VALID_END`）
- `promote_run.py` 不处理数据，仅操作注册表文件

---

## 三、一次完整实验的全流程

```
1. 研究员写 configs/pipelines/rolling48_ridge_topn50_ew.py（ExperimentSpec）
   ↓
2. python -m scripts.run_experiment \
       --spec configs/pipelines/rolling48_ridge_topn50_ew.py
   ├── 加载 spec（from_config_file → __post_init__ 校验）
   ├── 创建 RunContext（生成 UTC 时间戳 run_id）
   ├── 创建 ArtifactLayout（mkdir runs/train_valid/{run_id}/）
   ├── 检查 RUN_FINISHED.json（防止重跑）
   ├── 写 run_config.json + inputs.lock.json
   ├── 执行 signal 阶段（run_signal_stage）
   ├── 执行 portfolio 阶段（run_portfolio_stage）
   ├── 执行 backtest 阶段（run_backtest_stage）
   ├── 写 manifest.json（所有 .parquet sha256）
   ├── 写 RUN_FINISHED.json
   └── 写 reports/self_check.md（write_self_check_md）
   ↓
3. python -m scripts.compare_runs \
       --run-ids <frozen_baseline_run_id> <new_run_id>
   ├── load_run_metrics 逐个 run 读取指标
   ├── 输出 reports/experiment_board.csv + .md
   └── 终端打印比较表
   ↓
4. 看 self_check.md 和 experiment_board.md，判断是否 PASS
   ↓（三项硬指标全 PASS）
5. python -m scripts.promote_run \
       --run-id <run_id> \
       --reason "Rolling48 IR=1.771, all PASS"
   ├── _validate_promotion（检查 RUN_FINISHED / self_check.md / artifacts）
   ├── 写 registry/mainline.json（覆盖）
   └── 追加 registry/archived_promotions.jsonl
```

---

## 四、`run_experiment.py` — 详解

### 4.1 CLI 参数（L57-80）

| 参数 | 说明 | 示例 |
|---|---|---|
| `--spec` | ExperimentSpec .py 文件路径（必填）| `configs/pipelines/rolling48_ridge_topn50_ew.py` |
| `--from-stage` | 从哪个阶段开始（默认 signal）| `portfolio`（跳过信号阶段）|
| `--to-stage` | 执行到哪个阶段（含，默认 backtest）| `signal`（只跑信号）|
| `--input-signal-run` | 复用已有 run 的信号（`--from-stage portfolio` 时必填）| `20260527_081232__rolling48_...` |
| `--runs-root` | runs/ 根目录（默认 `runs/`）| `/data/runs` |

**`--from-stage portfolio --input-signal-run` 加速模式**：

对相同信号、不同组合参数的实验（如对比不同 TE 目标），可以复用已有 run 的 `signal/composite.parquet`，跳过耗时的信号计算阶段：

```bash
# 第一次：完整运行（生成信号）
python -m scripts.run_experiment \
    --spec configs/pipelines/ridge_te6_lambda5.py

# 第二次：复用信号，只跑 portfolio + backtest
python -m scripts.run_experiment \
    --spec configs/pipelines/ridge_te4_lambda5.py \
    --from-stage portfolio \
    --input-signal-run 20260529_081232__rolling48_ridge_topn50_ew
```

### 4.2 阶段范围控制（L152-165）

```python
_STAGE_ORDER = ["signal", "portfolio", "backtest"]

from_idx = _STAGE_ORDER.index(args.from_stage)
to_idx   = _STAGE_ORDER.index(args.to_stage)
active_stages = _STAGE_ORDER[from_idx: to_idx + 1]
```

从 `_STAGE_ORDER` 切片出需要执行的阶段列表，按顺序执行。

> **注意**：`attribution` 阶段在 `_STAGE_ORDER` 中**不存在**。当前版本（v2）的 `run_experiment.py` 只执行 signal → portfolio → backtest 三个阶段，归因（Brinson + 因子归因）不在统一入口中执行。

### 4.3 RUN_FINISHED 防重复运行检查（L178-184）

```python
if layout.is_finished():
    log.error(
        "run_dir 已存在且有 RUN_FINISHED.json: %s\n"
        "重跑请等待新的时间戳自动生成新 run_id。",
        ctx.run_dir,
    )
    sys.exit(1)
```

每次运行生成新的 `run_id`（UTC 时间戳不同），即使参数完全相同。防止误重跑已完成的 run 并覆盖产物。

### 4.4 信号路径解析（L83-109）

```python
def _resolve_signal_path(from_stage, input_signal_run, run_dir, runs_root, scope):
    if from_stage == "signal":
        return run_dir / "signal" / "composite.parquet"   # 本 run 的信号目录
    
    # 从已有 run 读取信号
    signal_path = runs_root / scope / input_signal_run / "signal" / "composite.parquet"
    if not signal_path.exists():
        raise FileNotFoundError(...)
    return signal_path
```

复用已有信号时，`signal_path` 指向另一个 run 的目录；这个路径会写入 `inputs.lock.json` 并记录其 sha256，用于审计"用了哪个信号跑的这次 portfolio"。

### 4.5 异常处理（L225-230）

```python
try:
    if "signal" in active_stages:
        run_signal_stage(spec, ctx.run_dir, ...)
    if "portfolio" in active_stages:
        run_portfolio_stage(...)
    if "backtest" in active_stages:
        run_backtest_stage(...)
except Exception as exc:
    tb = traceback.format_exc()
    log.error("阶段执行失败: %s\n%s", exc, tb)
    layout.write_run_failed(str(exc))
    sys.exit(1)
```

任何阶段失败：写 `RUN_FAILED.json`（记录原因），退出码 1，**已完成阶段的产物保留**（便于从中断点重试）。

---

## 五、`compare_runs.py` — 详解

### 5.1 CLI 参数（L47-67）

| 参数 | 说明 | 默认 |
|---|---|---|
| `--run-ids` | 指定 run_id 列表 | 从 registry 自动读取 |
| `--scope` | run 所在 scope | `train_valid` |
| `--output-dir` | 输出目录 | `reports/` |
| `--runs-root` | runs/ 根目录 | 自动推断 |

**两种调用模式**：

```bash
# 模式 A：指定 run_id 列表（必须包含 frozen_baseline 作为下限参照）
python -m scripts.compare_runs \
    --run-ids <frozen_baseline_run_id> <challenger_run_id_1> <challenger_run_id_2>

# 模式 B：从 registry 自动读取 mainline + challengers
python -m scripts.compare_runs
```

> ⚠️ **规范要求**：使用模式 A 时，**必须**将 `frozen_baseline_icir_topn50_ew` 的 run_id 包含在列表中。若不包含，比较表缺少下限参照，可能错误地将低效改进判断为有效。

### 5.2 Markdown 输出格式（L79-97）

```python
def _df_to_markdown(df) -> str:
    # 列宽 = max(表头宽度, 最长单元格宽度)
    # bool 列：True → ✅, False → ❌
    # NaN：— 
```

输出示例（`reports/experiment_board.md` 片段）：

```markdown
# Experiment Board
Generated : 2026-05-29 08:30 UTC
Thresholds: IR ≥ 0.5 · Excess MDD ≤ 10% · Annual Turnover 500–1500%

| run_id  | role    | signal_method | ... | IR    | ir_pass | mdd_pass | to_pass |
|---------|---------|---------------|-----|-------|---------|----------|---------|
| 202605… | mainline| ridge         | ... | 1.771 | ✅      | ✅       | ✅      |
```

---

## 六、`promote_run.py` — 详解

### 6.1 CLI 参数（L38-63）

| 参数 | 说明 |
|---|---|
| `--run-id` | 要晋升的 run_id（必填）|
| `--reason` | 晋升理由（必填，写入 registry）|
| `--scope` | run 所在 scope（默认 train_valid）|
| `--allow-test-set` | 允许晋升测试集 run（默认禁止）|
| `--runs-root` | runs/ 根目录（默认自动推断）|

### 6.2 晋升流程（L66-91）

```bash
python -m scripts.promote_run \
    --run-id 20260529_081232__rolling48_ridge_topn50_ew \
    --reason "Rolling48 IR=1.771, all hard metrics PASS, replacing baseline_expanding_ridge"
```

执行后：
1. `registry.promote_run` 执行 `_validate_promotion`（检查完整性）
2. 写入 `registry/mainline.json`（覆盖当前主线）
3. 追加 `registry/archived_promotions.jsonl`

**晋升失败时**：`log.error` + `sys.exit(1)`，注册表文件不修改。

---

## 七、相关测试

- `tests/test_pipeline_contracts.py`：间接覆盖 spec 加载和校验
- 建议补充：
  - `run_experiment.py` 的集成测试（用 mock stage 函数，验证文件写入）
  - 重跑已完成 run 时的 exit 行为

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| spec 文件不存在 | `run_experiment.py` 打 error + exit(1)（加载前检查）|
| `from_stage > to_stage` | exit(1) |
| `--from-stage portfolio` 但无 `--input-signal-run` | `_resolve_signal_path` 抛 ValueError + exit(1) |
| signal/composite.parquet 不存在（复用模式）| exit(1) |
| run 已完成（RUN_FINISHED.json 存在）| exit(1)，提示"等待新时间戳" |
| 某阶段抛异常 | 写 RUN_FAILED.json + exit(1) |
| promote_run 前检查失败 | RuntimeError + exit(1) |

---

## 九、数据流图

```
configs/pipelines/rolling48_ridge_topn50_ew.py
    ↓ python -m scripts.run_experiment --spec ...
    │
    ├─ ExperimentSpec.from_config_file() → spec 校验
    ├─ RunContext.create() → run_id + run_dir = runs/train_valid/{run_id}/
    ├─ ArtifactLayout.create_dirs()
    ├─ _write_run_config() → run_config.json
    ├─ _write_inputs_lock() → inputs.lock.json
    │
    ├─ run_signal_stage()      → signal/composite.parquet + metadata
    ├─ run_portfolio_stage()   → portfolio/target_weights.parquet + meta
    ├─ run_backtest_stage()    → backtest/nav_valid.parquet + metrics
    │
    ├─ _write_manifest()       → manifest.json（所有 .parquet sha256）
    ├─ write_run_finished()    → RUN_FINISHED.json
    └─ write_self_check_md()   → reports/self_check.md（PASS/FAIL）
    │
    ↓ python -m scripts.compare_runs --run-ids <frozen_baseline> <new_run>
    ├─ load_run_metrics() × N  → 读取各 run 指标
    ├─ _compute_ic_stats()     → 动态计算验证期 IC
    └─ _write_outputs()        → reports/experiment_board.md + .csv
    │
    ↓（三项全 PASS）python -m scripts.promote_run --run-id <id> --reason ...
    ├─ _validate_promotion()   → 完整性检查
    ├─ 写 registry/mainline.json（覆盖）
    └─ 追加 archived_promotions.jsonl
```

---

## 十、领域知识补充

**为何 attribution 阶段不在 `_STAGE_ORDER` 里？**

Brinson 归因和因子归因（WLS）是**事后分析工具**，不影响策略参数和产物，且运行时间较长（WLS 需要跨日截面回归）。当前设计将归因从实验主流程中解耦：研究员在确认某个 run 值得深度分析时再单独运行归因脚本，不增加每次实验的默认运行时间。未来如果归因成为标准报告的一部分，可以将 `"attribution"` 加入 `_STAGE_ORDER`。

**单变量实验原则（来自 `experiment_type` 字段）**：

每次实验只改一个变量（信号方法、组合参数、因子池之一），其他保持不变。违反这一原则的常见后果：假设 A（新信号）和 B（新约束）同时改变，结果比基线好，但无法判断是 A 还是 B 的贡献。后来才发现是 B 的负向效果被 A 的正向效果掩盖，下次实验用了错误的"改进后的 B"。`--from-stage portfolio --input-signal-run` 加速模式是执行单变量原则的工程支撑：固定信号，只改组合参数。
