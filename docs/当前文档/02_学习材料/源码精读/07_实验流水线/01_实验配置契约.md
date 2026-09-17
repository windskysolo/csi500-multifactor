# P1 — 实验数据契约 `src/pipeline/contracts.py`

---

## 一、文件定位

```
所属 Part  : Layer 8（Pipeline 编排层）
在数据流中 : configs/pipelines/*.py → ExperimentSpec → run_experiment.py → stages.py
被谁调用   : scripts/run_experiment.py（主实验入口）
             scripts/run_test_pipeline.py（测试集流水线，部分复用）
             src/pipeline/stages.py（读取 spec 决定调用哪条路径）
             src/pipeline/artifacts.py / registry.py（依赖 RunContext）
调用谁     : Python 标准库（dataclasses / importlib.util / json）
```

`contracts.py` 是 Pipeline 层的**核心数据契约文件**，定义了整个实验的参数结构。所有实验参数在这里集中声明、校验，Stage 函数只需接收 `spec` 即可，不再需要散落的关键字参数。

---

## 二、时间对齐与 PIT 假设

`contracts.py` 本身不处理数据，没有直接的 PIT 约束。但它定义的字段影响下游的时间边界：

- `period_scope = "train_valid"` → Stage 函数只处理 `≤ VALID_END` 的数据
- `period_scope = "test_run_N"` → Stage 函数（测试集专用路径）处理到 `TEST_END`
- `allow_test_set` 的 False/True 区分了两种时间范围，在 `RunContext` 层物理隔离存储路径

---

## 三、模块顶部

```python
import importlib.util   # 动态加载 .py 格式 Spec 文件
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional
```

**为何用 `.py` 格式的 Spec 而非 JSON/YAML**（见 `from_config_file`）：

Python 文件可以引用其他模块（如 `cfg.OPT_TE_TARGET_ANNUAL`），避免在 JSON 中重复硬编码配置中央值。同时 IDE 提供类型提示，拼错字段名时 Python 会报错，而 JSON 只在运行时发现。

---

## 四、核心 Dataclass 逐一解析

### 4.1 `SignalSpec` — 信号阶段参数（L11-22）

```python
@dataclass
class SignalSpec:
    method: str                        # "icir" | "ridge"
    target: str                        # 信号目标变量（通常 "excess_return"）
    training_mode: str                 # "expanding" | "rolling" | "decay_weighted_expanding"
    purge_months: int = 2              # 训练/验证集之间的纯化间隔（防信息泄露）
    alpha_grid: List[float] = [0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0]
    selected_alpha_policy: str = "cv_train_only"   # Ridge alpha 选择策略
    half_life_months: Optional[int] = None          # 仅 decay 模式使用
    window_months: Optional[int] = None             # 仅 rolling 模式使用
    exclude_factors: List[str] = field(default_factory=list)   # 消融实验用
```

**字段说明**：

| 字段 | 含义 | 注意事项 |
|---|---|---|
| `method` | 信号合成方式 | "icir"：IC_IR 加权；"ridge"：Ridge 回归 |
| `training_mode` | Ridge 训练窗口类型 | icir 方法时此字段被忽略 |
| `purge_months=2` | 纯化间隔 | 防止训练期末月和验证期首月的未来信息泄露，默认 2 个月 |
| `alpha_grid` | Ridge 正则化候选值 | walk-forward CV 从中选最优 |
| `selected_alpha_policy` | alpha 选择策略 | "cv_train_only"=只用训练集 CV（默认，防过拟合） |
| `half_life_months` | 衰减半衰期（月） | decay 模式必填；expanding/rolling 时应为 None |
| `window_months` | 滚动窗口长度（月） | rolling 模式必填；其他模式应为 None |
| `exclude_factors` | 消融实验排除列表 | 如 `["piotroski_f"]`，在加载因子面板后过滤 |

### 4.2 `OptimizerSpec` — 组合优化参数（L25-40）

```python
@dataclass
class OptimizerSpec:
    te_target_annual: float = 0.06      # 跟踪误差目标（年化，如 6%）
    industry_max_dev: float = 0.03      # 行业权重相对基准偏离上限（如 ±3%）
    single_max_dev: float = 0.015       # 单股偏离上限（相对基准，如 ±1.5%）
    turnover_lambda: float = 0.005      # 换手惩罚项系数
    topn: int = 50                      # TopN 等权时选取的股票数
    optimizer_mode: str = "qp"          # "qp" | "topn_ew" | "l2_forced"

    def __post_init__(self) -> None:
        valid_modes = {"qp", "topn_ew", "l2_forced"}
        if self.optimizer_mode not in valid_modes:
            raise ValueError(...)
```

**三种 optimizer_mode**：

| Mode | 含义 | 适用场景 |
|---|---|---|
| `"qp"` | 完整二次规划（L1→L2→L3 Fallback）| 生产方案，约束完整 |
| `"topn_ew"` | 直接取 alpha 最高的 TopN 只等权 | 隔离优化器影响的对照实验；当前验证期最优 |
| `"l2_forced"` | 强制使用 L2（线性约束，跳过 L1）| 诊断用，对比 L1 vs L2 的影响 |

> ⚠️ PLAN.md 原来描述的是 "qp" 和 "topn_ew" 两种，实际代码有第三种 `"l2_forced"`（L35）。

**`te_target_annual`、`industry_max_dev`、`single_max_dev` 与 `src/config.py` 的关系**：

`contracts.py` 提供的是**实验级覆盖值**，直接写入 `ExperimentSpec`。`config.py` 中的同名常量是**系统默认值**，供未明确指定时使用。Spec 中的参数优先级高于 `config.py`。

### 4.3 `BacktestSpec` — 回测参数（L43-48）

```python
@dataclass
class BacktestSpec:
    execution: str = "tplus1_open"              # T+1 开盘价成交
    cost_model: str = "china_a_share_v1"        # A 股成本模型（含印花税日期切换）
    benchmark: str = "CSI500_TOTAL_RETURN"      # 全收益指数基准
```

这三个值在项目中基本固定。`benchmark` 必须是全收益（含分红再投资），用价格指数会虚增超额约 2-3%/年。

### 4.4 `AttributionSpec` — 归因参数（L51-53）

```python
@dataclass
class AttributionSpec:
    method: str = "brinson"
```

目前只支持 Brinson-Hood-Beebower 归因。WLS 因子归因功能存在于 `src/attribution/factor_attr.py` 但尚未集成到 Pipeline spec。

### 4.5 `ExperimentSpec` — 实验总规格（L55-91）

```python
@dataclass
class ExperimentSpec:
    experiment_id: str         # 唯一标识，也是 run_id 的后缀部分
    description: str           # 人类可读描述（写入 run_config.json）
    period_scope: str          # "train_valid" | "test_run_1" | "test_run_2"
    signal: SignalSpec
    optimizer: OptimizerSpec
    backtest: BacktestSpec
    attribution: AttributionSpec = field(default_factory=AttributionSpec)
    allow_test_set: bool = False
    experiment_type: str = "single_layer"  # "single_layer" | "full_pipeline"
```

**`__post_init__` 的测试集保护锁（L68-79）**：

```python
def __post_init__(self) -> None:
    valid_train_only = self.period_scope == "train_valid"
    test_scope = self.period_scope.startswith("test_run_")
    if not valid_train_only and not test_scope:
        raise ValueError(...)       # period_scope 值非法
    if test_scope and not self.allow_test_set:
        raise ValueError(...)       # 测试集 scope 但未显式解锁
```

这是契约层的双重检查：
1. `period_scope` 只允许 `"train_valid"` 或 `"test_run_N"` 格式
2. 如果 scope 是测试集，必须显式设置 `allow_test_set=True`

这意味着：**如果 Spec 文件里没有 `allow_test_set=True`，即使指定了 `period_scope="test_run_1"`，也会在加载 Spec 时立即报错**，在任何数据处理开始之前就阻断。

**`experiment_type` 字段**：
- `"single_layer"` = 单次只改一个 Pipeline 层的变量（推荐，消融实验纪律）
- `"full_pipeline"` = 同时修改多个层（明确标注为全链路实验）

**`to_json()` 和 `from_config_file()`（L81-91）**：

```python
def to_json(self) -> str:
    return json.dumps(asdict(self), indent=2)

@classmethod
def from_config_file(cls, path) -> "ExperimentSpec":
    spec_module = importlib.util.spec_from_file_location("_spec_module", path)
    module = importlib.util.module_from_spec(spec_module)
    spec_module.loader.exec_module(module)
    if not hasattr(module, "SPEC"):
        raise AttributeError(f"Config file {path} must define a top-level SPEC variable.")
    return module.SPEC
```

`from_config_file` 通过 `importlib.util` 动态执行 `.py` 文件，要求文件顶层定义一个名为 `SPEC` 的 `ExperimentSpec` 实例。文件执行失败（语法错误、import 失败等）会直接抛出异常。

### 4.6 `RunContext` — 运行上下文（L94-109）

```python
@dataclass
class RunContext:
    spec: ExperimentSpec
    run_id: str      # "{UTC时间戳}__{experiment_id}"，如 "20260529_081232__rolling48_ridge_topn50_ew"
    run_dir: Path    # runs/{scope}/{run_id}/
    scope: str       # "train_valid" | "test"

    @classmethod
    def create(cls, spec, runs_root) -> "RunContext":
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_id = f"{timestamp}__{spec.experiment_id}"
        scope = "test" if spec.allow_test_set else "train_valid"
        run_dir = runs_root / scope / run_id
        return cls(...)
```

**run_id 生成规则**：UTC 时间戳（格式 `YYYYMMDD_HHMMSS`）+ 双下划线 + `experiment_id`。UTC 时间戳保证在同一机器上自然排序，双下划线分隔使时间戳和名称都可解析。

**`scope` 的物理隔离**：
- `allow_test_set=False` → `scope="train_valid"` → `run_dir = runs/train_valid/<run_id>/`
- `allow_test_set=True` → `scope="test"` → `run_dir = runs/test/<run_id>/`

测试集和训练验证集的产物**物理上位于不同目录**，不会互相覆盖。

---

## 五、内部辅助函数

`contracts.py` 无 `_` 前缀的私有函数。所有逻辑都在 `__post_init__` 或公开方法中。

---

## 六、落盘产物

`contracts.py` 本身不落盘。但通过 `ExperimentSpec.to_json()` 生成的内容被写入：
- `run_dir/run_config.json`（由 `run_experiment.py` 的 `_write_run_config` 写入）

`run_config.json` 格式（示例）：
```json
{
  "run_id": "20260529_081232__rolling48_ridge_topn50_ew",
  "experiment_id": "rolling48_ridge_topn50_ew",
  "scope": "train_valid",
  "started_at": "2026-05-29T08:12:32Z",
  "spec": {
    "experiment_id": "rolling48_ridge_topn50_ew",
    "signal": { "method": "ridge", "training_mode": "rolling", "window_months": 48, ... },
    "optimizer": { "optimizer_mode": "topn_ew", "topn": 50, ... },
    ...
  }
}
```

`compare_runs.py` 后续读取此文件重建 spec 信息，用于比较板的规格列。

---

## 七、相关测试

- `tests/test_pipeline_contracts.py`：覆盖 `__post_init__` 校验逻辑（非法 period_scope、测试集未解锁）
- 建议补充：`from_config_file` 文件缺失时的异常路径测试

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| `period_scope` 值非法 | `__post_init__` 抛 `ValueError`（加载 Spec 时立即失败）|
| `allow_test_set=False` 但 `period_scope="test_run_N"` | `__post_init__` 抛 `ValueError` |
| `optimizer_mode` 非法 | `OptimizerSpec.__post_init__` 抛 `ValueError` |
| Spec 文件不存在 | `from_config_file` 抛 `FileNotFoundError` |
| Spec 文件没有 `SPEC` 变量 | `from_config_file` 抛 `AttributeError` |

---

## 九、数据流图

```
configs/pipelines/rolling48_ridge_topn50_ew.py
  ↓ ExperimentSpec.from_config_file()   # importlib 动态执行
  ↓ __post_init__ 校验
ExperimentSpec 对象
  ↓ RunContext.create()
RunContext（run_id + run_dir + scope）
  ↓ 传入 run_experiment.py
  ↓ ArtifactLayout(run_dir) 管理目录
  ↓ stages.py 各函数读 spec.signal / spec.optimizer / spec.backtest
  ↓ spec.to_json() 序列化写入 run_config.json
```

---

## 十、领域知识补充

**为何需要 `purge_months=2`？**

在时序 CV 中，训练集最后一期和验证集第一期之间的数据存在"信息泄露风险"：月度调仓策略里，T 期的 forward return（T+1 月的收益）与 T+1 期的因子值之间可能存在重叠（因为 T+1 的财务因子公告期往往在 T+1 期初，而 T 期的 forward return 恰好是 T+1 期初到 T+2 期初的收益）。`purge_months=2` 在 CV 切割时跳过训练集末尾的 2 个月，确保训练集的最新信号和验证集的起始 forward return 之间没有重叠。

**`experiment_type` 字段的设计意图**：

量化研究中一个常见陷阱是"同时改了多个变量但把收益全归因到一个改动"。`experiment_type="single_layer"` 是一个**软性纪律约束**：强制研究者明确声明本次实验是否遵循"单变量原则"。若标注为 `"full_pipeline"` 但后续分析时发现结果难以解释，可以回溯检查是哪个变量造成的。
