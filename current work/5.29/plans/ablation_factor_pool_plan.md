# 因子池消融实验计划

> 编写日期：2026-05-29  
> 目标：量化 `piotroski_f`（质量综合）和 `high_52w_v2`（52 周高动量）对基线 IR 的边际贡献  
> 方法：固定 expanding Ridge + TE 6% + λ 0.005 配置，只改变因子池组成

---

## 一、实验目标

当前主基线（18 因子，IR=0.408）包含了两个有问题的因子：
- `piotroski_f`：已诊断为 `REMOVE_CANDIDATE`（验证期 IC_IR=-0.200，质量溢价反转）
- `high_52w_v2`：健康状态为 `WARN`（验证期 adj_ic_ir_24m 较低）

**三个消融实验**：

| 实验编号 | 实验 ID | 因子数 | 移除的因子 |
|---------|---------|-------|-----------|
| E-01 | `ablation_no_piotroski_f` | 17 | `piotroski_f` |
| E-02 | `ablation_no_high_52w_v2` | 17 | `high_52w_v2` |
| E-03 | `ablation_no_piotroski_high52w` | 16 | `piotroski_f` + `high_52w_v2` |

**可证伪假设**：

- E-01：若 IR 不下降，说明 piotroski_f 在当前组合中无贡献（验证其 REMOVE_CANDIDATE 结论）
- E-02：若 IR 不下降，说明 high_52w_v2 也可以考虑移除
- E-03：E-01 + E-02 的叠加效果，了解两者共同移除后的 IR 下界

---

## 二、需要做的基础设施改动（约 20 行代码）

**问题**：当前 `SignalSpec` 没有指定因子子集的参数，`stages.py` 总是从 `final_factors.json` 读取全部 18 个因子。

**方案**：在 `SignalSpec` 增加 `exclude_factors` 字段，在 `_load_factor_panels` 中过滤掉不需要的因子。

### 2.1 改动 `src/pipeline/contracts.py`

在 `SignalSpec` 数据类中加一个可选字段：

```python
# 在 SignalSpec 的现有字段末尾添加：
exclude_factors: Optional[List[str]] = field(default_factory=list)
```

完整改动位置：`src/pipeline/contracts.py` 第 11-20 行，`SignalSpec` 定义内。

### 2.2 改动 `src/pipeline/stages.py`

**改动点一**：修改 `_load_factor_panels` 函数签名，增加过滤逻辑：

```python
def _load_factor_panels(
    panel_dir: Path,
    eval_dir: Path,
    exclude_factors: list[str] | None = None,  # ← 新增
) -> dict:
    ...
    # 原有逻辑：从 final_factors.json 筛选
    result = {k: v for k, v in all_panels.items() if k in selected}

    # 新增：排除 exclude_factors（在 selected 之后再过滤，保证不能绕过 final_factors.json）
    if exclude_factors:
        excluded_found = [f for f in exclude_factors if f in result]
        result = {k: v for k, v in result.items() if k not in exclude_factors}
        log.info("已排除因子：%s  剩余 %d 个", excluded_found, len(result))
    return result
```

**改动点二**：在 `_run_signal_icir` 和 `_run_signal_ridge` 两处调用 `_load_factor_panels` 的地方，
从 `spec.signal.exclude_factors` 传入参数：

```python
# 原来：
factor_panels = _load_factor_panels(panel_dir, eval_dir)

# 改为：
factor_panels = _load_factor_panels(
    panel_dir, eval_dir,
    exclude_factors=spec.signal.exclude_factors or [],
)
```

两处调用分别在约第 76 行（`_run_signal_icir`）和第 144 行（`_run_signal_ridge`）。

### 2.3 改动范围说明

| 文件 | 改动类型 | 行数 | 风险 |
|------|---------|------|------|
| `src/pipeline/contracts.py` | 新增可选字段 | +2 行 | 零风险（有默认值，旧 Spec 不受影响） |
| `src/pipeline/stages.py` | 修改函数签名 + 两处调用 | +8 行 | 低风险（只有当 exclude_factors 非空时生效） |

> ⚠️ `exclude_factors=[]`（默认）时行为与现在完全一致，不影响任何已有实验复现。

---

## 三、Spec 文件设计

全部放在 `configs/pipelines/` 目录，命名前缀 `ablation_`，与 baseline/challenger 明显区分。

### E-01：`configs/pipelines/ablation_no_piotroski_f.py`

```python
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="ablation_no_piotroski_f",
    description=(
        "消融实验：移除 piotroski_f（REMOVE_CANDIDATE，验证期 IC_IR=-0.200）。"
        "其余配置与主基线 baseline_expanding_ridge_te6_lam0050 完全一致。"
        "目标：量化 piotroski_f 对基线 IR 的边际贡献。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
        exclude_factors=["piotroski_f"],          # ← 唯一变量
    ),
    optimizer=OptimizerSpec(
        te_target_annual=0.06,
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.005,
        topn=50,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
```

### E-02：`configs/pipelines/ablation_no_high_52w_v2.py`

```python
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="ablation_no_high_52w_v2",
    description=(
        "消融实验：移除 high_52w_v2（健康状态 WARN，验证期 adj_ic_ir_24m 偏低）。"
        "其余配置与主基线完全一致。"
        "目标：量化 52 周高动量因子对基线 IR 的边际贡献。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
        exclude_factors=["high_52w_v2"],          # ← 唯一变量
    ),
    optimizer=OptimizerSpec(
        te_target_annual=0.06,
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.005,
        topn=50,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
```

### E-03：`configs/pipelines/ablation_no_piotroski_high52w.py`

```python
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="ablation_no_piotroski_high52w",
    description=(
        "消融实验：同时移除 piotroski_f 和 high_52w_v2。"
        "其余配置与主基线完全一致。"
        "目标：了解两者共同移除后的 IR 下界，以及协同效应。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
        exclude_factors=["piotroski_f", "high_52w_v2"],   # ← 唯一变量
    ),
    optimizer=OptimizerSpec(
        te_target_annual=0.06,
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.005,
        topn=50,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
```

---

## 四、执行顺序

### 第 0 步：代码改动（只做一次）

```powershell
# 先运行现有测试确认改动前后基线一致
python -m pytest tests\ -q --tb=short
```

确认测试通过后，按照第二节的说明修改 `contracts.py` 和 `stages.py`。

再跑一次测试确认改动没有破坏已有功能：

```powershell
python -m pytest tests\ -q --tb=short
```

### 第 1 步：创建 3 个 Spec 文件

将上面三个 Spec 文件按路径创建：
- `configs/pipelines/ablation_no_piotroski_f.py`
- `configs/pipelines/ablation_no_high_52w_v2.py`
- `configs/pipelines/ablation_no_piotroski_high52w.py`

### 第 2 步：依次运行（无依赖关系，可并行，但建议顺序运行便于监控）

```powershell
# E-01：移除 piotroski_f
python -m scripts.run_experiment --spec configs/pipelines/ablation_no_piotroski_f.py

# E-02：移除 high_52w_v2
python -m scripts.run_experiment --spec configs/pipelines/ablation_no_high_52w_v2.py

# E-03：移除两者
python -m scripts.run_experiment --spec configs/pipelines/ablation_no_piotroski_high52w.py
```

每次运行约 5-10 分钟（expanding Ridge 需要逐期拟合）。

### 第 3 步：横向比较

运行结束后，找到三个 run 的 ID（格式 `YYYYMMDD_HHMMSS__ablation_*`），
用以下命令生成比较板：

```powershell
python -m scripts.compare_runs `
    --runs `
        20260527_141545__baseline_expanding_ridge_te6_lam0050 `
        <E-01 run_id> `
        <E-02 run_id> `
        <E-03 run_id> `
    --include-frozen
```

> 注：`--include-frozen` 确保 frozen_baseline_icir_topn50_ew（IR=0.924）始终出现在比较板中，作为下限参照。

输出：`reports/experiment_board.md`

---

## 五、输出文件结构

运行结束后，目录结构如下：

```
runs/train_valid/
├── 20260527_141545__baseline_expanding_ridge_te6_lam0050/   ← 已有主基线
│   └── reports/self_check.md                               ← IR=0.408
│
├── YYYYMMDD_HHMMSS__ablation_no_piotroski_f/               ← E-01
│   ├── run_config.json
│   ├── signal/composite.parquet
│   ├── portfolio/target_weights.parquet
│   ├── backtest/metrics_valid.parquet
│   └── reports/self_check.md                               ← 关键：看 IR 有没有下降
│
├── YYYYMMDD_HHMMSS__ablation_no_high_52w_v2/               ← E-02
│   └── reports/self_check.md
│
└── YYYYMMDD_HHMMSS__ablation_no_piotroski_high52w/         ← E-03
    └── reports/self_check.md

reports/
├── experiment_board.md         ← 横向比较（4 runs + frozen baseline）
└── experiment_board.csv
```

---

## 六、结果解读标准

| 场景 | 含义 | 建议动作 |
|------|------|---------|
| E-01 IR ≥ 主基线 IR（0.408）| piotroski_f 无贡献或有害 | 继续推进正式移除实验 |
| E-01 IR < 主基线 IR，差距 ≤ 0.05 | piotroski_f 贡献微弱 | 可接受移除，在 challenger_rolling48 基础上再验证 |
| E-01 IR 明显低于主基线 IR（差距 > 0.10）| piotroski_f 仍有贡献 | 不急于移除，转为降权而非剔除 |
| E-02 IR ≥ 主基线 IR | high_52w_v2 无贡献 | 标记为 REMOVE_CANDIDATE，下次实验移除 |
| E-03 IR 与 E-01/E-02 预期一致 | 两者独立，无交叉作用 | 正常 |
| E-03 IR 明显高于 E-01 和 E-02 | 两者存在负协同（互相抵消）| 有趣，记录到诊断日志 |

---

## 七、注意事项

1. **结论纪律**：即使 E-01 IR ≥ 主基线，也**不能直接修改 `final_factors.json`**。
   消融实验只提供证据，正式调池需要通过 `run_factor_evaluation.py` 更新 json，再推进一个 challenger 实验晋升。

2. **对比基准**：比较板中必须包含 `frozen_baseline_icir_topn50_ew`（IR=0.924）
   作为下限参照，防止消融实验意外把 IR 降到不可接受水平。

3. **不触碰测试集**：以上所有实验均为 `period_scope="train_valid"`，不消耗测试集次数。

4. **新因子实验**：消融实验与新因子引入是**并行路径**，不需要等消融实验结论才能开始研究新因子。

---

## 八、快速实施检查清单

- [ ] 1. 修改 `src/pipeline/contracts.py`（SignalSpec 加 exclude_factors 字段）
- [ ] 2. 修改 `src/pipeline/stages.py`（_load_factor_panels + 两处调用）
- [ ] 3. 运行 `pytest tests\ -q` 确认测试通过
- [ ] 4. 创建 3 个 Spec 文件
- [ ] 5. 运行 E-01
- [ ] 6. 运行 E-02
- [ ] 7. 运行 E-03
- [ ] 8. 运行 compare_runs 生成比较板
- [ ] 9. 更新 CLAUDE.md §1.1（待解决已知问题字段）
