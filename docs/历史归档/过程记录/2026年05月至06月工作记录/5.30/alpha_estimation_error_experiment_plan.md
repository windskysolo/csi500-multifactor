# Alpha 估计误差改进：分阶段实验计划

> 撰写日期：2026-05-30
> 依据：`current work/5.29/results/alpha_estimation_error_guide.md`（理论背景与方法论）
> 目标：验证排名变换与 Alpha 校准能否提升 QP 组合的 IR，填补"QP 损耗"（0.402 → 理论最优 ~0.924）

---

## 一、问题背景（快速复盘）

| 实验 | 信号 | 优化器 | 验证期 IR |
|------|------|--------|-----------|
| `frozen_baseline_icir_topn50_ew` | ICIR | TopN50 EW | **0.924**（冻结基线） |
| `baseline_icir_te6_lam0050` | ICIR | QP (TE=6%) | 0.402 |
| `rolling48_ridge_topn50_ew` | Ridge-48m | TopN50 EW | **1.771**（最优） |
| `challenger_rolling48_te6_lam0050` | Ridge-48m | QP (TE=6%) | 1.489 |

**根因（2026-05-29 已诊断）**：QP 把合成 Alpha 当"基数"（cardinal）用，IC=0.047 环境下 Alpha ≈ 噪声，QP 在最大化噪声暴露；TopN50 只用"序数"（ordinal），对噪声鲁棒。

**解法路径**（本计划覆盖前两步）：
```
排名变换 QP → Alpha 校准 QP → [Black-Litterman（长期可选）]
```

---

## 二、阶段总览

| 阶段 | 目标 | 核心改动 | 预期耗时 |
|------|------|---------|---------|
| **阶段一** | 排名变换 + ICIR + QP | contracts + stages，新 Spec | 1-2 小时 |
| **阶段二** | 排名变换 + Ridge-48 + QP | 复用信号，新 Spec | 0.5 小时 |
| **阶段三** | Alpha 校准 + ICIR + QP | calibrate_alpha 函数，新 Spec | 2-4 小时 |

**阶段一是前置条件**：代码改动（contracts + stages）在阶段一完成后，阶段二/三直接复用。

---

## 三、阶段一：排名变换 QP（ICIR 信号）

### 3.1 假设与可证伪条件

**假设**：将截面 Alpha 转换为排名百分位（序数），QP 的 IR 应从 0.402 显著提升，接近 TopN50 的 0.924。

**可证伪**：
- 若 IR < 0.5（连门槛都过不了）→ 排名变换无效，问题不在"基数 vs 序数"，需重新诊断
- 若 0.5 ≤ IR < 0.7 → 有改善但效果有限，可能需要配合 Alpha 校准（阶段三）
- 若 IR ≥ 0.7 → 排名变换有效，继续阶段二验证在更强信号上的效果

### 3.2 代码改动（最小改动原则）

#### 改动 1：`src/pipeline/contracts.py` — 在 `OptimizerSpec` 中添加字段

在 `OptimizerSpec` dataclass 中添加一个字段，放在 `optimizer_mode` 之前：

```python
# 在 OptimizerSpec 中添加（放在 optimizer_mode 字段之前）：
use_rank_transform: bool = False   # 是否在进入 QP 前对截面 Alpha 做排名百分位变换
```

同时更新 `__post_init__` 的校验逻辑（保持不动，`use_rank_transform` 对 `topn_ew` 模式无意义但不会报错）。

#### 改动 2：`src/pipeline/stages.py` — 在 `run_portfolio_stage` 中注入变换

在 `run_portfolio_stage` 函数体内，读取信号之后、调用 `portfolio_main` 之前，添加排名变换逻辑：

```python
def run_portfolio_stage(spec, run_dir, signal_path, data_proc=None):
    ...
    # 新增：排名变换（在 portfolio_main 调用之前）
    effective_signal_path = signal_path
    if getattr(spec.optimizer, "use_rank_transform", False):
        effective_signal_path = _apply_rank_transform(signal_path, run_dir)

    portfolio_main(
        signal_path      = effective_signal_path,  # 改用变换后的路径
        ...
    )
```

新增辅助函数（放在 `stages.py` 的 Helpers 区域）：

```python
def _apply_rank_transform(signal_path: Path, run_dir: Path) -> Path:
    """
    对截面 Alpha 做排名百分位变换后写到 signal/ 目录，返回新路径。
    
    rank(pct=True, na_option='keep') 输出 (0,1]，减 0.5 后变为 (-0.5, 0.5]。
    保持 NaN（optimizer 视为无观点，置 0）。
    """
    import pandas as pd

    signal = pd.read_parquet(signal_path)
    ranked = signal.rank(axis=1, pct=True, na_option="keep") - 0.5
    out_path = run_dir / "signal" / "composite_rank_transformed.parquet"
    ranked.to_parquet(out_path)
    log.info("排名变换完成，写入 %s  shape=%s", out_path, ranked.shape)
    return out_path
```

> **为什么不改 `optimizer.py`**：`optimizer.py` 只负责给定 Alpha 向量后的权重求解，不应关心上游信号格式。排名变换属于"信号后处理"，放在 stages 层的信号→组合衔接处最干净。

### 3.3 新增实验配置

创建 `configs/pipelines/challenger_icir_te6_rank_qp.py`：

```python
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="challenger_icir_te6_rank_qp",
    description=(
        "ICIR 信号 + 排名变换 + QP（TE=6%）。"
        "验证假设：截面 Alpha 的排名百分位变换能否修复 QP 的噪声放大问题。"
        "与冻结基线(ICIR+TopN50 IR=0.924)和 QP 基线(ICIR+QP IR=0.402)对比。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="icir",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
    ),
    optimizer=OptimizerSpec(
        optimizer_mode="qp",
        te_target_annual=0.06,
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.005,
        topn=50,
        use_rank_transform=True,    # 核心改动
    ),
    backtest=BacktestSpec(),
)
```

### 3.4 运行命令

```bash
# 完整 pipeline（信号 + 组合 + 回测）
python -m scripts.run_experiment \
    --spec configs/pipelines/challenger_icir_te6_rank_qp.py

# 实验完成后，与基线对比（必须包含 frozen_baseline 的 run_id）
python -m scripts.compare_runs \
    --run-ids \
        20260529_034947__frozen_baseline_icir_topn50_ew \
        <baseline_icir_te6_run_id> \
        <新的 challenger_icir_te6_rank_qp run_id>
```

> **注意**：`frozen_baseline` 的完整 run_id 是 `20260529_034947__frozen_baseline_icir_topn50_ew`，每次 compare_runs 必须包含它。

### 3.5 成功标准（全六项指标）

| 指标 | 目标 | 门槛 |
|------|------|------|
| IR | ≥ 0.7（相比 0.402 有显著提升） | ≥ 0.5（硬门槛） |
| 年化超额收益 | 正值且显著 | > 0% |
| 超额最大回撤 | ≤ 10% | ≤ 10%（硬门槛） |
| 跟踪误差 | 4-8%（TE=6% 约束附近） | 参考值 |
| 月度胜率 | ≥ 55% | ≥ 50% |
| 年化双边换手 | 500-1500% | 500-1500%（硬门槛） |

---

## 四、阶段二：排名变换 QP（Rolling-48 Ridge 信号）

### 4.1 假设

Rolling-48 Ridge 信号质量明显优于 ICIR 信号（TopN50 IR=1.771 vs 0.924）。如果排名变换在 ICIR 上有效，那么在 Ridge-48 信号上：
- 当前 QP IR=1.489，TopN50 IR=1.771，差距 0.282
- 排名变换后，QP IR 应进一步收窄差距

**特别意义**：如果排名变换 QP 在 Ridge-48 信号上也能超越纯 TopN50（即 IR > 1.771），则证明"协方差管理 + 序数信号"的组合优于"无协方差管理 + 序数信号"——这将是最理想的结果。

### 4.2 配置（复用 Rolling-48 信号，跳过信号阶段）

代码改动**无需额外工作**（阶段一已完成 contracts + stages 改动）。

创建 `configs/pipelines/challenger_rolling48_rank_qp.py`：

```python
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="challenger_rolling48_rank_qp",
    description=(
        "Rolling 48m Ridge 信号 + 排名变换 + QP（TE=6%）。"
        "验证最优信号下，排名变换能否弥合 QP(1.489) vs TopN50(1.771) 的差距。"
        "复用 rolling48_ridge_topn50_ew 的信号，仅改变优化器。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="rolling",
        purge_months=2,
        window_months=48,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
    ),
    optimizer=OptimizerSpec(
        optimizer_mode="qp",
        te_target_annual=0.06,
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.005,
        topn=50,
        use_rank_transform=True,
    ),
    backtest=BacktestSpec(),
)
```

### 4.3 运行命令

```bash
# ⚠️ 复用信号前必须先核实 SignalSpec 一致性，防止用错信号版本
# 运行下面命令对比两个 spec 的 signal 字段：
# cat runs/train_valid/20260529_081232__rolling48_ridge_topn50_ew/run_config.json
# 确认 method/training_mode/window_months/purge_months/alpha_grid/selected_alpha_policy
# 与本 spec 完全一致后，再用 --input-signal-run 参数

# 方式A：复用已有 rolling-48 信号（推荐，跳过耗时的信号阶段）
python -m scripts.run_experiment \
    --spec configs/pipelines/challenger_rolling48_rank_qp.py \
    --from-stage portfolio \
    --input-signal-run 20260529_081232__rolling48_ridge_topn50_ew

# 方式B：如果不确定 run_id 或信号需要重建
python -m scripts.run_experiment \
    --spec configs/pipelines/challenger_rolling48_rank_qp.py

# 对比（必须包含 frozen_baseline）
python -m scripts.compare_runs \
    --run-ids \
        20260529_034947__frozen_baseline_icir_topn50_ew \
        20260529_081232__rolling48_ridge_topn50_ew \
        <challenger_rolling48 的 run_id> \
        <新的 challenger_rolling48_rank_qp run_id>
```

### 4.4 决策矩阵

| 排名变换 Ridge-48 QP 结果 | 结论 | 下一步 |
|--------------------------|------|--------|
| IR > 1.771（超过 TopN50）| 协方差管理 + 序数信号 = 最优组合 | **优先注册为新 challenger；考虑晋升主线** |
| 1.489 < IR ≤ 1.771 | QP 改善但仍不及 TopN50 | 继续阶段三（Alpha 校准） |
| IR ≤ 1.489 | 排名变换在强信号上反而无效 | 深度诊断（换手率是否大幅增加？） |

---

## 五、阶段三：Alpha 校准 QP（中期）

> **前提条件**：阶段一 IR ≥ 0.7 且 阶段二有改善，才进行阶段三。

### 5.1 核心思路

排名变换去除了量级信息，但实际上 IC 高的时期信号更强、波动率高的股票同等信号意味着更大潜在收益——这些信息在排名变换中被丢弃。

Alpha 校准：
```
α_cal[t, i] = α_raw[t, i] × IC_rolling[t] × σ_stock[t, i]
```

让校准后的 Alpha 量级接近"预期月度超额收益 %"，使 QP 的比例分配对应真实收益差异。

### 5.2 代码改动

#### 改动 1：`src/pipeline/contracts.py` — 在 `OptimizerSpec` 添加字段

```python
use_alpha_calibration: bool = False             # 是否做 IC×波动率 校准
alpha_calibration_ic_lookback: int = 12         # 滚动 IC 回看月数
alpha_calibration_vol_lookback_months: int = 24 # 历史月收益率滚动标准差回看月数
                                                # （fwd_ret_panel 为月频面板，单位为月）
```

#### 改动 2：`src/pipeline/stages.py` — 在 `run_portfolio_stage` 中注入校准

新增 `_apply_alpha_calibration` 辅助函数：

```python
def _apply_alpha_calibration(
    signal_path: Path,
    run_dir: Path,
    spec,
    data_proc: Path,
) -> Path:
    """
    IC-波动率 Alpha 校准：α_cal = α_raw × IC_rolling[t] × σ_stock[t]
    
    依赖：
      - run_dir/signal/ic_detail.parquet（由 _run_signal_icir 写入）
      - data_proc/fwd_ret_panel.parquet（月频已实现收益，shift(1) 后估计历史波动率）
    
    校准后 Alpha 单位约等于"月度预期超额收益"，QP 的权重比例更有意义。
    
    未来函数防范：
      - IC_rolling[t] 通过 ic_detail.shift(1) 实现：T 期只用 T-1 及之前的 IC
        （ic_detail[T] 含 fwd_ret[T] 即 T→T+1 未来收益，不能直接用）
      - σ_stock[t] 通过 fwd_ret.shift(1) 实现：T 期只用 T-1 及之前的已实现收益
    """
    import pandas as pd

    signal = pd.read_parquet(signal_path)

    # IC 明细（仅 ICIR 信号阶段产生）
    ic_detail_path = run_dir / "signal" / "ic_detail.parquet"
    if not ic_detail_path.exists():
        log.warning("ic_detail.parquet 不存在，跳过 Alpha 校准，返回原始信号")
        return signal_path

    ic_detail = pd.read_parquet(ic_detail_path)
    opt = spec.optimizer
    ic_lookback         = getattr(opt, "alpha_calibration_ic_lookback", 12)
    vol_lookback_months = getattr(opt, "alpha_calibration_vol_lookback_months", 24)

    # 滚动平均 IC：shift(1) 确保 T 期只用 T-1 及之前的 IC，避免未来函数
    rolling_ic = ic_detail.shift(1).rolling(ic_lookback).mean().mean(axis=1)

    # 月化历史波动率：fwd_ret.shift(1) 后取已实现历史收益，rolling 单位为月
    # vol_lookback_months=24 表示用过去 24 个月的已实现收益估计当期波动率
    fwd_path = data_proc / "fwd_ret_panel.parquet"
    fwd_ret  = pd.read_parquet(fwd_path)
    stock_vol = fwd_ret.shift(1).rolling(vol_lookback_months).std() * (12 ** 0.5)

    calibrated = signal.copy()
    for t in signal.index:
        if t not in rolling_ic.index or pd.isna(rolling_ic.loc[t]):
            continue
        ic_t  = rolling_ic.loc[t]
        vol_t = stock_vol.loc[t] if t in stock_vol.index else None
        if vol_t is None:
            continue
        calibrated.loc[t] = signal.loc[t] * ic_t * vol_t

    out_path = run_dir / "signal" / "composite_calibrated.parquet"
    calibrated.to_parquet(out_path)
    log.info("Alpha 校准完成，写入 %s", out_path)
    return out_path
```

在 `run_portfolio_stage` 中：
```python
# 先做校准（若需要），再做排名变换（若需要）
effective_signal_path = signal_path
if getattr(spec.optimizer, "use_alpha_calibration", False):
    effective_signal_path = _apply_alpha_calibration(
        effective_signal_path, run_dir, spec, _data_proc
    )
if getattr(spec.optimizer, "use_rank_transform", False):
    effective_signal_path = _apply_rank_transform(effective_signal_path, run_dir)
```

> **顺序说明**：先校准（调整量级），再排名（去除量级）——两者同时使用时实际上等价于只做排名变换。所以实验中它们是互斥的：单独测校准、单独测排名、然后看是否需要组合。

### 5.3 阶段三实验配置

```
configs/pipelines/challenger_icir_calibrated_qp.py        # ICIR + 纯校准 + QP（无排名变换）
configs/pipelines/challenger_rolling48_calibrated_qp.py   # Ridge-48 + 纯校准 + QP（无排名变换）
```

---

## 六、代码改动总清单

```
需要修改的文件（按顺序）：
─────────────────────────────────────────────────────
阶段一代码改动：
  src/pipeline/contracts.py
    └── OptimizerSpec: 添加 use_rank_transform: bool = False

  src/pipeline/stages.py
    └── run_portfolio_stage: 注入 _apply_rank_transform 逻辑
    └── 新增 _apply_rank_transform 辅助函数

需要新增的配置文件：
  configs/pipelines/challenger_icir_te6_rank_qp.py
  configs/pipelines/challenger_rolling48_rank_qp.py

─────────────────────────────────────────────────────
阶段三代码改动（阶段一完成后再做）：
  src/pipeline/contracts.py
    └── OptimizerSpec: 添加 use_alpha_calibration + 两个回看参数

  src/pipeline/stages.py
    └── run_portfolio_stage: 注入 _apply_alpha_calibration 逻辑
    └── 新增 _apply_alpha_calibration 辅助函数

需要新增的配置文件：
  configs/pipelines/challenger_icir_calibrated_qp.py
  configs/pipelines/challenger_rolling48_calibrated_qp.py
```

---

## 七、比较基准（每次 compare_runs 必须包含）

| run_id | 角色 | IR |
|--------|------|-----|
| `20260529_034947__frozen_baseline_icir_topn50_ew` | 冻结基线（不可晋升） | 0.924 |
| `20260529_081232__rolling48_ridge_topn50_ew` | 当前最优 | 1.771 |
| `<baseline_icir_te6_lam0050 run_id>` | QP 基线（ICIR 信号） | 0.402 |
| `<challenger_rolling48_te6_lam0050 run_id>` | QP 基线（Ridge-48 信号） | 1.489 |

---

## 八、风险点与注意事项

### 8.1 排名变换的局限性

- **换手率可能上升**：相邻排名的 Alpha 差异更均匀，优化器持仓变化可能更频繁
  - 检查换手率是否超 1500%
  - 若超出，可调大 `turnover_lambda`（从 0.005 → 0.010）

- **TE 约束仍然必要**：排名变换不能替代 TE 约束（TE 约束是鲁棒优化的隐式实现）
  - 保持 `te_target_annual=0.06`，不要因为"信号已序数化"就去掉约束

### 8.2 Alpha 校准的 Cold Start 问题

- 前 `ic_lookback=12` 个月（加上 shift(1) 共 13 期）无法计算滚动 IC → 这些期 Alpha 保持不变（不校准）
- 前 `vol_lookback_months=24` 个月（加上 shift(1) 共 25 期）历史收益数据不足 → 同上
- 实现中已通过 `if pd.isna(rolling_ic.loc[t]): continue` 处理（vol_t 为 NaN 时同样跳过）

### 8.3 Ridge 信号没有 `ic_detail.parquet`

- `ic_detail.parquet` 只由 `_run_signal_icir` 写入，Ridge 信号阶段不写
- 阶段三的 Alpha 校准暂只适用于 ICIR 信号
- Ridge 信号上的校准需要单独计算合成信号 IC（较复杂，暂不做）

### 8.4 测试集纪律

本计划所有实验均为 `period_scope="train_valid"`，**不消耗测试集配额**。
当前测试集剩余 3 次，只有在某个实验晋升为主线候选后才考虑测试集评估。

### 8.5 中间产物不在 manifest 追踪范围内（已知局限）

`_apply_rank_transform` 写出 `composite_rank_transformed.parquet`，`_apply_alpha_calibration` 写出 `composite_calibrated.parquet`，两者均不在 `REQUIRED_SIGNAL_ARTIFACTS` 定义的 manifest 追踪清单中。manifest 哈希的是原始 `composite.parquet`，实际进入组合阶段的是变换后文件——两者不同，manifest 完整性校验在此处形同虚设。

**缓解措施**：`self_check.md` 的规格摘要会记录 `use_rank_transform=True`，可通过 `run_config.json` 追溯变换类型。完整 manifest 支持留作后续框架改进，不阻塞当前实验。

---

## 九、决策树

```
阶段一完成（challenger_icir_te6_rank_qp）
  │
  ├─ IR < 0.5 → 停止，重新诊断"序数 vs 基数"假设是否成立
  │
  ├─ 0.5 ≤ IR < 0.7 → 轻微改善
  │    └─ 继续阶段三（Alpha 校准），观察两者结合效果
  │
  └─ IR ≥ 0.7 → 排名变换有效 ✓
       │
       └─ 继续阶段二（challenger_rolling48_rank_qp）
            │
            ├─ IR > 1.771 → 协方差管理有价值，考虑注册 challenger
            │
            ├─ 1.5 ≤ IR ≤ 1.771 → 有改善，可选择注册 challenger 或继续阶段三
            │
            └─ IR ≤ 1.5 → 效果有限，检查换手率、行业偏离，或继续阶段三
```

---

_计划版本：v1.1（2026-05-30）_
_v1.1 修复：experiment_id 加 challenger_ 前缀；compare_runs --run-ids 参数名；Alpha 校准 rolling_ic 加 shift(1) 消除未来函数；vol_lookback 单位从"日"改为"月"并更名 vol_lookback_months（默认24）；补充信号复用核实步骤；新增 §8.5 manifest 追踪局限说明_
_下次更新触发条件：阶段一/二实验结果出来后，在此文档追加实验结果摘要_
