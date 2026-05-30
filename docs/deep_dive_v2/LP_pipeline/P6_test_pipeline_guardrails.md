# P6 — 测试集流水线与防污染机制 `scripts/run_test_pipeline.py` + `scripts/test_set_ledger.py`

---

## 一、文件定位

```
所属 Part  : Layer 7（实验脚本层）
在数据流中 : registry/mainline.json → run_test_pipeline.py → runs/test/{run_id}/
被谁调用   : 用户从命令行调用（python -m scripts.run_test_pipeline --run-id 1）
调用谁（run_test_pipeline.py）:
  src/pipeline/registry.py  （load_mainline）
  src/pipeline/artifacts.py （file_sha256）
  src/pipeline/stages.py    （write_self_check_md）
  src/evaluation/ic_analysis.py（compute_ic_series / compute_forward_returns）
  src/signal/combiner.py    （build_composite_panel）
  src/portfolio/covariance.py / optimizer.py
  src/backtest/engine.py / metrics.py
  scripts/test_set_ledger.py（全部函数）
```

**实际行数**：`run_test_pipeline.py` = 1085 行，`test_set_ledger.py` = 82 行（PLAN.md 快照说 63 行，已更新）。

---

## 二、时间对齐与 PIT 假设

测试集时间范围：`cfg.TEST_START = 2023-01-01` ~ `cfg.TEST_END = 2025-12-31`

**关键隔离原则**（F9-003）：
- 公共 `data/processed/fwd_ret_panel.parquet` **只含训练/验证期数据，不被测试集运行修改**
- 测试集运行将扩展后的 `fwd_ret_panel` 写入 `runs/test/{run_id}/fwd_ret_panel.parquet`
- 测试期新增协方差矩阵缓存写入 `runs/test/{run_id}/cov_cache/`，不写公共 `data/processed/cov_cache/`
- 信号计算时，alpha 选择（walk-forward CV）严格在训练/验证期（≤ `VALID_END`）完成，不接触测试期信息

---

## 三、模块顶部

### test_set_ledger.py

```python
ROOT = Path(__file__).parent.parent
LEDGER_PATH = ROOT / "docs" / "logs" / "test_set_runs.json"   # 权威路径
MAX_TEST_SET_RUNS = 2
```

> ⚠️ **已知 docstring 漂移**：`test_set_ledger.py` 文件顶部的 docstring 写的是 `docs/check/test_set_runs.json`，
> 但代码里 `LEDGER_PATH = ROOT / "docs" / "logs" / "test_set_runs.json"`。
> **以代码为准**，正确路径是 `docs/logs/test_set_runs.json`，docstring 是过时描述，建议修复。

### run_test_pipeline.py

```python
PANEL_DIR            = cfg.DATA_PROC / "factor_panels"
PUBLIC_FWD_CACHE     = cfg.DATA_PROC / "fwd_ret_panel.parquet"
PUBLIC_COV_CACHE_DIR = cfg.DATA_PROC / "cov_cache"

_EVAL_DIR           = _ROOT / "reports" / "factor_evaluation"
_INVALIDATED_PATH   = _EVAL_DIR / "INVALIDATED.md"    # 评估失效哨兵文件
_EVAL_JSON_PATH     = _EVAL_DIR / "final_factors.json"
```

---

## 四、核心机制逐一解析

### 4.1 五道安全门（main 函数 L937-1028）

测试集流水线在任何实质性操作开始之前，按顺序执行五道守卫检查：

#### 门 1：Ledger 计数守卫

```python
completed = count_test_set_runs()
if completed >= MAX_TEST_RUNS:
    log.error("测试集已运行 %d 次（上限 %d 次），拒绝执行。", completed, MAX_TEST_RUNS)
    sys.exit(1)
```

检查 `docs/logs/test_set_runs.json` 中有效运行次数（status != "voided"）。达到上限（2次）立即拒绝，不允许第三次运行。

#### 门 2：Run-id 序列守卫

```python
if args.run_id != completed + 1:
    log.error(
        "--run-id %d 与 ledger 中已有次数 %d 不符（应为 %d）",
        args.run_id, completed, completed + 1,
    )
    sys.exit(1)
```

`--run-id` 必须比当前完成次数恰好多 1。用户无法跳过次数（如已完成 0 次但传 `--run-id 2`）。

#### 门 3：本地锁文件守卫（F9-004）

```python
lock_started = test_run_dir / "RUN_STARTED.json"

if lock_started.exists():
    if has_test_set_run(args.run_id):     # ledger 已有记录
        log.error("该次测试集运行已完成，拒绝重复执行。")
        sys.exit(1)
    elif not args.resume_from_lock:        # ledger 无记录，但锁文件存在
        log.error(
            "RUN_STARTED 锁文件已存在（可能是上次中断的运行）。\n"
            "若要重试，请显式传入 --resume-from-lock。"
        )
        sys.exit(1)
    else:
        log.warning("检测到未提交的 RUN_STARTED 锁，--resume-from-lock 已传入，继续运行。")
```

`RUN_STARTED.json` 在进入主流程前写入，是进程级别的锁。三种状态：
- 锁存在 + ledger 有记录 → 已完成，拒绝重跑
- 锁存在 + ledger 无记录 + 无 `--resume-from-lock` → 疑似中断，要求显式确认
- 锁存在 + ledger 无记录 + 有 `--resume-from-lock` → 合法重试，继续执行

**`--resume-from-lock` 的使用场景**：上次运行中途被 Ctrl+C 中断，进程死亡，但 ledger 尚未写入（因为 ledger 写入是最后一步）。此时 `RUN_STARTED.json` 存在但 ledger 无记录，允许用 `--resume-from-lock` 重试，已生成的产物（如信号文件）自动跳过重新计算。

#### 门 4：评估报告失效守卫（F5-001）

```python
if _INVALIDATED_PATH.exists():
    log.error(
        "评估报告已标记失效（%s 存在）。"
        "请先按 INVALIDATED.md 中的重建步骤修复上游数据后再运行测试流水线。",
        _INVALIDATED_PATH,
    )
    sys.exit(1)
```

如果 `reports/factor_evaluation/INVALIDATED.md` 存在，说明上游数据（因子面板或财务数据）发生了变更，之前的因子评估结果不再有效。在清除 `INVALIDATED.md`（即完成重新评估）之前，禁止运行测试集。

#### 门 5：信号文件存在性守卫（F5-002）

```python
if _test_signal_sentinel.exists() and not args.resume_from_lock:
    log.error(
        "test_run_%d 信号文件已存在。"
        "拒绝覆盖；若需重建，请手动删除 %s 后重试，或传入 --resume-from-lock。",
        args.run_id, test_run_dir,
    )
    sys.exit(1)
```

防止意外覆盖测试集信号文件（信号一旦生成就代表"用过了测试期数据"）。

---

### 4.2 主基线 Context 读取（L97-141）

```python
mainline = load_mainline()                    # registry/mainline.json
mainline_run_id = mainline["active_run_id"]

# 读取主基线的 run_config.json
mainline_run_config_path = runs/train_valid/{mainline_run_id}/run_config.json
opt_spec    = run_config.spec.optimizer
signal_spec = run_config.spec.signal
```

测试集运行**复用主基线的 optimizer 参数**（te_target / turnover_lambda / topn）和**信号方法**（method / training_mode）。不允许在测试集运行时更改这些参数（防止测试集"调参"）。

---

### 4.3 产物目录结构（F9-003 隔离）

```
runs/test/
  test_run_1__{mainline_run_id}/       ← 测试集专用目录（不与 train_valid 混合）
    run_config.json
    inputs.lock.json                   ← 因子面板 sha256 快照
    TEST_SET_RUN_LOCK.json             ← 含 run_id/mainline_run_id/status/git_commit
    RUN_STARTED.json                   ← 进程锁（早于 RUN_FINISHED）
    RUN_FINISHED.json
    manifest.json
    fwd_ret_panel.parquet              ← 扩展到测试期（不覆盖公共文件）
    cov_cache/                         ← 测试期新增协方差缓存（不写公共 cov_cache）
    signal/
      composite.parquet
    portfolio/
      target_weights.parquet
      baseline_weights.parquet
      optimizer_meta.parquet
    backtest/
      nav_valid.parquet               ← 名称为 nav_valid 但实际是测试期（历史原因）
      metrics_valid.parquet
      trades_valid.parquet
    reports/
      self_check.md
```

**路径命名**：测试 run 的 `run_id = f"test_run_{N}__{mainline_run_id}"`，如 `test_run_1__20260527_143000__baseline_expanding_ridge`，使产物目录名自带"这是第几次测试集运行 + 基于哪个主线"的信息。

---

### 4.4 fwd_ret_panel 扩展（`_extend_fwd_ret_panel`，L210-285）

```python
existing = pd.read_parquet(PUBLIC_FWD_CACHE)   # 读取公共文件（只读）
new_t_dates = [d for d in all_dates[:-1] if d not in existing_dates]

new_fwd = compute_forward_returns(
    rebalance_dates = new_t_dates + [all_dates[-1]],   # 最后一期是"下一期"日期
    codes           = all_new_codes,
)

merged = pd.concat([existing, new_fwd]).sort_index()
merged.to_parquet(test_run_dir / "fwd_ret_panel.parquet")   # 写测试专用路径
# 公共文件 PUBLIC_FWD_CACHE 未被修改（F9-003 隔离）
```

**增量计算**：只计算不在现有面板中的新调仓日（测试期），与历史数据 concat 后写入测试专用目录，公共文件只读不写。

---

### 4.5 Ledger 写入（`record_test_set_run`，test_set_ledger.py L58-81）

```python
def record_test_set_run(run_id, status="finished", git_commit="unknown") -> None:
    ledger = load_test_set_ledger()
    entry = {
        "run_id":     run_id,
        "status":     status,
        "git_commit": git_commit,
        "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # 幂等：若 run_id 已存在，更新而非追加
    for idx, run in enumerate(runs):
        if int(run.get("run_id", -1)) == run_id:
            runs[idx] = entry
            break
    else:
        runs.append(entry)
    LEDGER_PATH.write_text(json.dumps(ledger, ...), encoding="utf-8")
```

`count_test_set_runs()` 统计 `status != "voided"` 的条目数。这意味着：
- 如果一次运行被确认为无效（手动将 status 改为 `"voided"`），不计入次数
- `"voided"` 只能手动操作，不能通过正常流程撤销

---

### 4.6 `TEST_SET_RUN_LOCK.json` — 测试运行追溯文件

```json
{
  "run_id": 1,
  "test_run_id": "test_run_1__20260527_...",
  "mainline_run_id": "20260527_143000__baseline_expanding_ridge",
  "status": "finished",
  "started_at": "2026-05-29T10:00:00Z",
  "finished_at": "2026-05-29T10:45:00Z",
  "ledger_path": ".../docs/logs/test_set_runs.json",
  "factor_panel_dir": ".../data/processed/factor_panels",
  "git_commit": "a1b2c3d"
}
```

记录了这次测试集运行的完整上下文，供事后审计（"当时用的哪个主基线、哪些因子面板、哪个 git commit"）。

---

### 4.7 git commit 规范

测试集运行完成后（成功且 `record_test_set_run` 已写入），应立即 commit：

```bash
git add docs/logs/test_set_runs.json runs/test/test_run_1__*/
git commit -m "[TEST_SET_RUN_1] test set evaluation using mainline=baseline_expanding_ridge"
```

`[TEST_SET_RUN_N]` 标签是 CLAUDE.md 要求的纪律约束，用于事后追溯"哪些 commit 消耗了测试集次数"。但 **ledger 本身的计数以 `docs/logs/test_set_runs.json` 为权威**，不以 git 历史中的标签数量为准（防止标签被 squash 或 amend 抹除）。

---

## 五、内部辅助函数

| 函数 | 说明 |
|---|---|
| `_load_mainline_context()` | 读取主基线 run_config.json，返回 optimizer / signal spec |
| `_build_opt_config(opt_spec)` | 从 spec dict 构建 OptimizeConfig（缺失值回退 cfg 默认）|
| `_extend_fwd_ret_panel()` | 增量扩展 fwd_ret_panel 到测试期（不污染公共文件）|
| `_build_composite_signals_ridge()` | Ridge 信号路径（method=ridge 时使用）|
| `_run_test_backtest()` | 运行 V1/V2 回测，写标准产物 |
| `_write_run_config()` | 写 run_config.json（与 compare_runs.py 兼容的格式）|
| `_write_inputs_lock()` | 写 inputs.lock.json（因子面板哈希快照）|
| `_write_test_set_run_lock()` | 写 TEST_SET_RUN_LOCK.json（测试运行追溯）|
| `_write_manifest()` | 写 manifest.json（所有产物文件哈希）|
| `_write_lock_file()` | 写 RUN_STARTED.json（进程锁）|
| `_current_git_commit()` | 获取当前 git commit short hash |
| `count_test_set_runs()` | 统计有效测试集运行次数（from ledger）|
| `has_test_set_run(run_id)` | 检查 run_id 是否在 ledger 中（用于锁文件守卫）|
| `remaining_test_set_runs()` | 返回剩余可用次数（=MAX - count）|
| `record_test_set_run()` | 在 ledger 中登记一次有效运行 |

---

## 六、落盘产物

### test_set_ledger.py

| 文件 | 写入时机 | 说明 |
|---|---|---|
| `docs/logs/test_set_runs.json` | 测试集运行成功完成后 | 计数权威来源 |

### run_test_pipeline.py（写入 `runs/test/{run_id}/`）

| 文件 | 说明 |
|---|---|
| `RUN_STARTED.json` | 进程锁，运行开始时写入 |
| `run_config.json` | spec + mainline 信息（与 compare_runs 兼容）|
| `inputs.lock.json` | 因子面板 sha256 快照 |
| `TEST_SET_RUN_LOCK.json` | 完整运行上下文（审计用）|
| `fwd_ret_panel.parquet` | 扩展到测试期的 forward return（只在本 run 目录）|
| `cov_cache/*.npz` | 测试期协方差缓存（只在本 run 目录）|
| `signal/composite.parquet` | 全期合成信号 |
| `portfolio/target_weights.parquet` + 其他 | 优化权重 |
| `backtest/nav_valid.parquet` + 其他 | 测试期回测结果 |
| `manifest.json` | 所有产物文件哈希 |
| `RUN_FINISHED.json` | 成功完成标志 |
| `reports/self_check.md` | 测试期指标 + PASS/FAIL 自检 |
| `docs/logs/test_set_runs.json` | ledger 更新（最后一步）|

---

## 七、相关测试

- `tests/test_pipeline_contracts.py`：间接覆盖 `allow_test_set` 校验
- 建议补充：
  - `count_test_set_runs` 的单元测试（空文件/非法格式/voided 条目）
  - `--resume-from-lock` 逻辑的集成测试
  - 门 1（MAX_RUNS 超限）的拒绝行为测试

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| 测试集已用 2 次 | 门 1 拒绝，exit(1) |
| --run-id 与 ledger 不符 | 门 2 拒绝，exit(1) |
| RUN_STARTED 存在 + ledger 有记录 | 门 3 拒绝，exit(1) |
| RUN_STARTED 存在 + 无 --resume-from-lock | 门 3 拒绝，exit(1) |
| INVALIDATED.md 存在 | 门 4 拒绝，exit(1) |
| 信号文件存在 + 无 --resume-from-lock | 门 5 拒绝，exit(1) |
| mainline.json 不存在 | 警告（使用 cfg 默认参数），不阻断 |
| 某步骤计算失败 | 写 RUN_FAILED.json，exit(1)，ledger 不更新 |
| 协方差估计单期失败 | 警告，该期计数 n_failed，不阻断整体流程 |

---

## 九、数据流图

```
用户：python -m scripts.run_test_pipeline --run-id 1
  ↓
  门 1: count_test_set_runs() < 2？
  门 2: --run-id == completed + 1？
  门 3: RUN_STARTED.json 存在？→ 检查 ledger + --resume-from-lock
  门 4: INVALIDATED.md 存在？
  门 5: signal/composite.parquet 存在？
  ↓（全部通过）
  _load_mainline_context()        ← registry/mainline.json
  ↓
  写 RUN_STARTED.json             ← 进程锁
  写 run_config.json
  ↓
  _extend_fwd_ret_panel()         ← 公共 fwd_ret_panel（只读）→ test_run_dir/fwd_ret_panel
  ↓
  build_composite_panel() 或      ← factor_panels/ + final_factors.json
  RidgeCombiner.build_panel()     → test_run_dir/signal/composite.parquet
  ↓
  estimate_covariance_lw()        ← 公共 cov_cache（只读新期） → test_run_dir/cov_cache/
  ↓
  optimize_single_period()        → test_run_dir/portfolio/
  ↓
  run_backtest(TEST_START~TEST_END)→ test_run_dir/backtest/
  ↓
  write_self_check_md()           → test_run_dir/reports/self_check.md
  write_manifest()                → test_run_dir/manifest.json
  write_run_finished()            → test_run_dir/RUN_FINISHED.json
  write_test_set_run_lock()       → test_run_dir/TEST_SET_RUN_LOCK.json
  record_test_set_run()           → docs/logs/test_set_runs.json（最后一步）
```

---

## 十、领域知识补充

**测试集上限为何是 2 次？**

统计学视角：每次测试集运行后，研究员都可能根据结果调整策略，使下一次测试集结果"被污染"。如果允许无限次测试集运行，实质上是在测试集上调参，样本外结果失去意义。2 次上限是工程化实现的"保留一次容错"原则：第 1 次发现问题（如实现 bug）可以修复后用第 2 次验证，但第 2 次必须是最终决策，不能再改。

**为何 ledger 写入是"最后一步"？**

若 ledger 在运行开始前写入，中途失败时 ledger 已消耗一次机会但未产生有效结果。若在运行成功后写入，失败的运行不消耗计数。代价是：若进程在"写 RUN_FINISHED.json"之后、"写 ledger"之前崩溃，会出现"run 已完成但 ledger 未记录"的状态，此时 `RUN_STARTED.json` 已存在，通过 `--resume-from-lock` 可以重试（会检测到 RUN_FINISHED.json 存在，跳过所有步骤，直接写 ledger）。

**`fwd_ret_panel` 的公共/测试隔离为何重要？**

公共 `data/processed/fwd_ret_panel.parquet` 是训练集评估的数据依赖（`ic_analysis.py`、`combiner.py` 等都读它）。若测试集运行修改了这个文件（写入了 2023+ 的数据），后续的训练集 IC 计算就能"看到"测试期的 forward return，引入测试集污染。即使研究员不主动使用这些数据，只要文件被修改，就存在意外污染的风险。物理隔离（写到独立目录）是杜绝此类风险的最彻底方案。
