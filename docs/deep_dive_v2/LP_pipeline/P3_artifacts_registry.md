# P3 — 产物管理与注册机制 `src/pipeline/artifacts.py` + `src/pipeline/registry.py`

---

## 一、文件定位

```
所属 Part  : Layer 8（Pipeline 编排层）
在数据流中 : run_experiment.py → ArtifactLayout（写产物）→ registry.promote_run（注册）
被谁调用   : scripts/run_experiment.py（ArtifactLayout + file_sha256）
             scripts/promote_run.py（promote_run）
             scripts/run_test_pipeline.py（file_sha256 + ArtifactLayout 部分逻辑）
             src/pipeline/stages.py（write_self_check_md 间接使用）
             src/pipeline/compare.py（_read_composite_hash 用 file_sha256 逻辑）
调用谁     : Python 标准库（hashlib / json / pathlib / datetime）
             src/pipeline/artifacts.py（registry.py 内部导入）
```

两个文件各司其职：
- `artifacts.py`（102 行）：管理单次 run 的**目录结构和产物生命周期**
- `registry.py`（110 行）：管理**跨 run 的注册表**（mainline / challengers）

---

## 二、时间对齐与 PIT 假设

两个文件都不处理时序数据，不涉及 PIT 约束。它们管理的是文件系统路径和 JSON 注册表。

---

## 三、模块顶部

### artifacts.py

```python
import hashlib
from dataclasses import dataclass

REQUIRED_SIGNAL_ARTIFACTS = ["signal/composite.parquet"]
REQUIRED_PORTFOLIO_ARTIFACTS = ["portfolio/target_weights.parquet"]
REQUIRED_BACKTEST_ARTIFACTS = [
    "backtest/nav_valid.parquet",
    "backtest/metrics_valid.parquet",
]
REQUIRED_FULL_RUN_ARTIFACTS = (
    REQUIRED_SIGNAL_ARTIFACTS
    + REQUIRED_PORTFOLIO_ARTIFACTS
    + REQUIRED_BACKTEST_ARTIFACTS
    + ["reports/self_check.md"]
)
```

这四个常量定义了各阶段的**最小必要产物列表**。`REQUIRED_FULL_RUN_ARTIFACTS` 是晋升前的完整检查清单（共 5 个文件）。

### registry.py

```python
_PROJECT_ROOT = Path(__file__).parents[2]
_DEFAULT_REGISTRY_DIR = _PROJECT_ROOT / "registry"
_DEFAULT_RUNS_ROOT = _PROJECT_ROOT / "runs"
```

默认路径定位到项目根目录下的 `registry/` 和 `runs/`，通过参数可覆盖（方便测试）。

---

## 四、核心类/函数逐一解析

### 4.1 `ArtifactLayout` — run 目录布局（artifacts.py L24-91）

```python
@dataclass
class ArtifactLayout:
    run_dir: Path

    def signal_dir(self)     -> Path: return self.run_dir / "signal"
    def portfolio_dir(self)  -> Path: return self.run_dir / "portfolio"
    def backtest_dir(self)   -> Path: return self.run_dir / "backtest"
    def attribution_dir(self)-> Path: return self.run_dir / "attribution"
    def reports_dir(self)    -> Path: return self.run_dir / "reports"
    def manifest_path(self)  -> Path: return self.run_dir / "manifest.json"
    def run_finished_path(self) -> Path: return self.run_dir / "RUN_FINISHED.json"
    def run_failed_path(self)   -> Path: return self.run_dir / "RUN_FAILED.json"
    def run_config_path(self)   -> Path: return self.run_dir / "run_config.json"
    def inputs_lock_path(self)  -> Path: return self.run_dir / "inputs.lock.json"
```

`ArtifactLayout` 是 run 目录的**统一访问接口**，所有路径都通过它访问，不直接拼接字符串。这使路径规范集中在一处，修改目录结构时只改 `ArtifactLayout` 即可。

**关键方法**：

**`create_dirs()`（L61-63）**：

```python
def create_dirs(self) -> None:
    for subdir in ["signal", "portfolio", "backtest", "attribution", "reports"]:
        (self.run_dir / subdir).mkdir(parents=True, exist_ok=True)
```

一次性创建所有子目录。在 run 开始前调用，确保任何阶段都可以写文件。

**`write_run_finished()`（L65-73）**：

```python
def write_run_finished(self) -> None:
    if self.run_finished_path().exists():
        return   # Idempotent: 已存在则跳过（不覆盖时间戳）
    payload = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(self.run_dir),
    }
    self.run_finished_path().write_text(json.dumps(payload, indent=2))
```

**幂等设计**：如果 `RUN_FINISHED.json` 已存在，方法直接返回，不覆盖原有时间戳。这确保即使意外调用两次，也不会修改第一次的完成时间。

**`write_run_failed(reason: str)`（L75-81）**：

写入 `RUN_FAILED.json`，记录失败时间和原因。注意：**此方法不是幂等的**（失败可覆盖），因为同一 run 可能在不同阶段失败，需要更新原因。

**`check_artifacts_present(artifact_list)`（L86-88）**：

```python
def check_artifacts_present(self, artifact_list: List[str]) -> List[str]:
    return [a for a in artifact_list if not (self.run_dir / a).exists()]
```

返回**缺失**的产物列表（空列表=全部存在）。供 `_validate_promotion` 调用，确保晋升前所有产物都已生成。

**`is_finished()`（L90-91）**：只检查 `RUN_FINISHED.json` 是否存在，`run_experiment.py` 用此方法防止重复运行已完成的 run。

---

### 4.2 `file_sha256(path)` — 文件哈希（artifacts.py L94-101）

```python
def file_sha256(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
```

**用途**：
1. `run_experiment.py` 写 `manifest.json` 时记录所有 `.parquet` 文件的哈希，用于产物审计
2. `run_test_pipeline.py` 写 `inputs.lock.json` 时记录因子面板的哈希，锁定输入快照
3. `compare.py` 读 `manifest.json` 中 `composite.parquet` 的哈希前 8 位，供比较板快速确认两个 run 是否用了相同的信号

分块读取（65536 字节/块）处理大型 Parquet 文件时不会导致内存问题。

---

### 4.3 `load_mainline(registry_dir=None)` / `load_challengers(registry_dir=None)` — 读取注册表（registry.py L14-27）

```python
def load_mainline(registry_dir=None) -> dict:
    path = (registry_dir or _DEFAULT_REGISTRY_DIR) / "mainline.json"
    if not path.exists():
        raise FileNotFoundError(...)
    return json.loads(path.read_text(encoding="utf-8"))

def load_challengers(registry_dir=None) -> dict:
    path = (registry_dir or _DEFAULT_REGISTRY_DIR) / "challengers.json"
    if not path.exists():
        return {"challengers": []}   # 文件不存在时返回空列表，不报错
    return json.loads(path.read_text(encoding="utf-8"))
```

**两种错误处理的差异**：`mainline.json` 不存在时**抛异常**（主线是必需的，缺失是错误状态）；`challengers.json` 不存在时**返回空结构**（没有挑战者是正常初始状态）。

---

### 4.4 `promote_run(run_id, slot, reason, ...)` — 晋升操作（registry.py L30-79）

```python
def promote_run(
    run_id: str,
    slot: str,              # 必须是 "mainline"
    reason: str,            # 晋升理由（写入注册表，供审计）
    runs_root: Path = None,
    registry_dir: Path = None,
    allow_test_set: bool = False,
    scope: str = "train_valid",
) -> None:
```

**晋升流程**：

1. **测试集安全门**（L55-59）：如果 `scope="test"` 但 `allow_test_set=False`，拒绝晋升
2. **产物完整性检查** `_validate_promotion(run_dir, allow_test_set)`（L82-103）：
   - run_dir 存在
   - `RUN_FINISHED.json` 存在（run 已完成）
   - `reports/self_check.md` 存在（自检报告已生成）
   - 所有 `REQUIRED_FULL_RUN_ARTIFACTS` 存在
3. **写入 mainline.json**（L78）：覆盖写（每次晋升替换主线）
4. **追加 archived_promotions.jsonl**（L79）：追加写（不可删除的历史记录）

`mainline.json` 写入内容（L68-76）：

```json
{
  "slot": "mainline",
  "active_run_id": "20260529_081232__rolling48_ridge_topn50_ew",
  "scope": "train_valid",
  "promoted_at": "2026-05-29T08:20:00Z",
  "reason": "Rolling48 IR=1.771, all three hard metrics PASS",
  "promoted_by": "manual",
  "test_set_used": false
}
```

**`slot` 只允许 `"mainline"`**（L63-66）：挑战者的管理不通过此接口，直接手动编辑 `challengers.json`，保持 `promote_run` 的职责单一。

---

### 4.5 `_validate_promotion(run_dir, allow_test_set)` — 晋升前验证（registry.py L82-103）

晋升的防守性检查，失败时抛 `RuntimeError`（不是 ValueError），确保不会进入错误的主线状态：

```python
if not run_dir.exists():
    raise RuntimeError(f"Run directory not found: {run_dir}")

if not (run_dir / "RUN_FINISHED.json").exists():
    raise RuntimeError("Run is not complete.")

if not (run_dir / "reports" / "self_check.md").exists():
    raise RuntimeError("Cannot promote without self-check.")

missing = layout.check_artifacts_present(REQUIRED_FULL_RUN_ARTIFACTS)
if missing:
    raise RuntimeError(f"Missing required artifacts: {missing}")
```

`self_check.md` 的存在性检查确保了 IR/MDD/换手率指标被人眼看过一次（即使 FAIL 也允许晋升，但必须有记录）。

---

### 4.6 `_append_promotion_log(reg_dir, entry)` — 追加晋升日志（registry.py L106-109）

```python
def _append_promotion_log(reg_dir: Path, entry: dict) -> None:
    log_path = reg_dir / "archived_promotions.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
```

`archived_promotions.jsonl` 是**只追加不删除**的 JSON Lines 格式日志，记录完整晋升历史。每行是一个 JSON 对象，便于 `grep` 和 `jq` 查询。

---

## 五、内部辅助函数

| 函数 | 说明 |
|---|---|
| `_validate_promotion` | 晋升前完整性校验（registry.py）|
| `_append_promotion_log` | 追加写晋升日志（registry.py）|

---

## 六、落盘产物

**run 目录层**（`ArtifactLayout` 管理）：

| 文件 | 写入时机 | 说明 |
|---|---|---|
| `run_config.json` | run 开始时 | 完整 spec + 元数据 |
| `inputs.lock.json` | run 开始时 | 信号输入文件哈希（从已有 run 复用时）|
| `manifest.json` | run 结束后 | 所有 .parquet 文件的 sha256 |
| `RUN_FINISHED.json` | 成功完成后 | 完成时间戳（幂等）|
| `RUN_FAILED.json` | 失败时 | 失败原因（可覆盖）|

**注册表层**（`registry.py` 管理）：

| 文件 | 内容 | 写入方式 |
|---|---|---|
| `registry/mainline.json` | 当前主线 run_id | 覆盖写（每次晋升替换）|
| `registry/archived_promotions.jsonl` | 所有晋升历史 | 追加写（不可删除）|
| `registry/challengers.json` | 挑战者列表 | 手动管理（不通过此模块写入）|

---

## 七、相关测试

- `tests/test_pipeline_contracts.py` 间接覆盖 `ArtifactLayout` 路径生成
- 建议补充：
  - `write_run_finished` 幂等性测试（调用两次，文件时间戳不变）
  - `_validate_promotion` 各失败条件的测试（缺少 self_check.md / 缺少 parquet）

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| `promote_run` 时 run 未完成 | `_validate_promotion` 抛 `RuntimeError` |
| `promote_run` 时缺少 self_check.md | 抛 `RuntimeError` |
| `promote_run` 时 scope=test 但未授权 | 抛 `ValueError` |
| `load_mainline` 文件不存在 | 抛 `FileNotFoundError` |
| `load_challengers` 文件不存在 | 返回 `{"challengers": []}` |
| `file_sha256` 文件不存在 | 返回 `None` |

---

## 九、数据流图

```
run_experiment.py 启动
  ↓ RunContext.create()         → run_id / run_dir / scope
  ↓ ArtifactLayout(run_dir)
  ↓ create_dirs()               → run_dir/signal/ portfolio/ backtest/ attribution/ reports/
  ↓ [写 run_config.json]
  ↓ [写 inputs.lock.json]
  ↓ stages 执行（信号 → 组合 → 回测）
  ↓ _write_manifest()           → manifest.json（所有 .parquet 的 sha256）
  ↓ write_run_finished()        → RUN_FINISHED.json
  ↓ write_self_check_md()       → reports/self_check.md

scripts/promote_run.py --run-id <id> --reason "..."
  ↓ registry.promote_run()
  ↓ _validate_promotion()       → 检查 RUN_FINISHED / self_check.md / artifacts
  ↓ 写 registry/mainline.json  （覆盖）
  ↓ 追加 archived_promotions.jsonl
```

---

## 十、领域知识补充

**为何用 `.jsonl` 格式记录晋升历史？**

`mainline.json` 只保存当前主线，历史晋升记录在 `archived_promotions.jsonl`（JSON Lines 格式）。JSONL 的优势：每行是独立 JSON，`tail -n 10` 可查看最近晋升，`grep "run_id"` 可快速搜索，`git diff` 只显示新增行而不是整个文件变更。追加写（`"a"` 模式）确保历史记录无法被意外覆盖，是轻量级的操作日志方案。

**为何 `challengers.json` 手动管理而不通过接口？**

挑战者（challengers）的状态变化频繁（实验跑完 → 更新 IR → PASS/FAIL → 可能降级），而晋升（mainline）是一个较正式的操作，应该有明确的触发点和检查。分开管理的好处：`promote_run.py` 的接口简单、防错；`challengers.json` 的维护灵活、不需要额外代码支持每次小更新。
