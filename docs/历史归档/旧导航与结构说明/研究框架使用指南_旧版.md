# 研究框架使用指南

> 适用项目：`e:\Acoding\Project\500 improve`
> 最后更新：2026-05-28

---

## 一、框架核心思想（先读这个）

框架只有一条核心规则：

**每一次研究 = 一个 Spec 文件 + 一次 run + 一个不可变目录**

旧做法是改代码、跑脚本、结果覆盖上一次。  
新框架下你永远不改运行代码，只写一个描述"这次实验想测什么"的配置文件，然后运行，结果自动存到独立目录，永远不会覆盖。

```
你写这个 ──→ configs/pipelines/my_experiment.py   （描述想法）
框架生成这个 ──→ runs/train_valid/<timestamp>__my_experiment/   （所有结果）
```

---

## 二、项目目录速查

```
500 improve/
│
├── configs/pipelines/          ← 所有实验的配置文件（你主要在这里工作）
│   ├── baseline_*.py           ← 主基线候选
│   ├── challenger_*.py         ← 挑战者
│   └── grid/                   ← 网格实验批量生成的 spec（建议放子目录）
├── data/
│   ├── raw/                    ← Tushare 原始 CSV（不进 git）
│   └── processed/              ← Parquet 缓存；因子面板输出到此处
│       └── factor_panels/      ← 单因子 parquet（因子研究输出目标）
├── runs/
│   ├── train_valid/            ← 所有训练+验证期 run 的结果
│   └── test/                   ← 测试集 run 的结果（最多 3 次）
│
├── registry/
│   ├── mainline.json           ← 当前"主基线"是哪个 run
│   ├── challengers.json        ← 挑战者名单
│   └── archived_promotions.jsonl  ← 历史晋升日志
│
├── reports/
│   ├── experiment_board.csv    ← 最新横向比较数据（可导入 Excel）
│   └── experiment_board.md     ← 最新横向比较 Markdown 表
│
├── src/pipeline/               ← 框架核心（一般不需要改）
│   ├── contracts.py            ← ExperimentSpec 等数据结构定义
│   ├── stages.py               ← 信号/组合/回测 阶段执行逻辑
│   ├── registry.py             ← 晋升/加载主基线
│   └── compare.py              ← 多 run 横向比较逻辑
│
└── scripts/
    ├── run_experiment.py       ← 运行单个实验（最常用）
    ├── compare_runs.py         ← 生成比较板
    ├── promote_run.py          ← 晋升某个 run 为主基线
    └── run_test_pipeline.py    ← 测试集评估（最多 3 次）
```

---

## 三、一个实验从头到尾的完整流程

### 第 1 步：写 Spec 文件

在 `configs/pipelines/` 新建一个 `.py` 文件，文件里只需要定义一个名为 `SPEC` 的变量。

**示例：测试换手率惩罚从 0.005 改成 0.010 会怎样**

```python
# configs/pipelines/challenger_te6_lam0100.py

from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="challenger_te6_lam0100",          # 必须唯一，会成为目录名的一部分
    description="测试 lambda=0.010 对换手率的影响",   # 给自己看的说明
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
    ),
    optimizer=OptimizerSpec(
        te_target_annual=0.06,
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.010,     # ← 只改这一行
        topn=50,
    ),
    backtest=BacktestSpec(),
)
```

### 第 2 步：运行实验

```bash
python -m scripts.run_experiment --spec configs/pipelines/challenger_te6_lam0100.py
```

运行完成后，结果自动存到：
```
runs/train_valid/20260528_153000__challenger_te6_lam0100/
  ├── run_config.json           ← 本次 spec 的完整快照
  ├── inputs.lock.json          ← 输入信号的 sha256（复现追溯用）
  ├── manifest.json             ← 所有 parquet 产物的哈希校验清单
  ├── RUN_FINISHED.json         ← 成功标志
  ├── signal/
  │   ├── composite.parquet         ← 合成信号面板
  │   ├── coef_history.parquet      ← Ridge 每期系数历史（ridge 模式专有）
  │   └── signal_metadata.json      ← 信号元信息（alpha、方法等）
  ├── portfolio/
  │   ├── target_weights.parquet    ← 目标持仓权重
  │   ├── baseline_weights.parquet  ← 基准权重（用于 Brinson 归因）
  │   └── optimizer_meta.parquet    ← 优化器元信息（L1/L2/L3 回退统计）
  ├── backtest/
  │   ├── metrics_valid.parquet     ← 验证期绩效指标汇总
  │   ├── nav_valid.parquet         ← 每日 NAV 曲线（超额 + 绝对）
  │   ├── trades_valid.parquet      ← 每期交易记录
  │   ├── actual_weights_valid.parquet  ← 每日实际持仓权重
  │   ├── trades_baseline.parquet       ← 基准交易记录
  │   └── actual_weights_baseline.parquet
  └── reports/
      └── self_check.md         ← 自动生成的指标摘要 + PASS/FAIL
```

### 第 3 步：查看结果

打开 `runs/train_valid/<run_id>/reports/self_check.md` 即可看到关键指标。

### 第 4 步：横向比较

```bash
python -m scripts.compare_runs
```

自动读取 `registry/` 里所有已注册的 run，生成：
- `reports/experiment_board.csv`
- `reports/experiment_board.md`

### 第 5 步：注册挑战者

如果结果不错，手动把它加到 `registry/challengers.json`：

```json
{
  "challengers": [
    {
      "name": "lam0100",
      "run_id": "20260528_153000__challenger_te6_lam0100",
      "scope": "train_valid",
      "status": "researching",
      "registered_at": "2026-05-28",
      "metrics_v2": {
        "information_ratio": 1.2,
        "excess_return": 0.07,
        "excess_max_drawdown": -0.06,
        "tracking_error": 0.058,
        "monthly_win_rate": 0.54,
        "annual_turnover_pct": 680
      },
      "notes": "换手降低但 IR 无损"
    }
  ]
}
```

### 第 6 步（可选）：晋升为主基线

```bash
python -m scripts.promote_run \
    --run-id 20260528_153000__challenger_te6_lam0100 \
    --reason "lambda 加大后换手降至 680%，IR 无损，晋升为新主基线"
```

---

## 四、各类研究的具体做法

---

### 4.1 因子研究

因子研究的目标是：哪些因子真的有效？加进来是否提升信号质量？

**你需要改的地方：`src/` 里的因子代码，然后按以下步骤操作**

因子研究不走 Spec 框架（因子还没进 pipeline），而是先在训练/验证期做单因子评价，确认有效后把新因子并入主池并重跑全量评价，再跑一个包含新因子的信号实验。

**步骤：**

```
1. 开发新因子 → 在 src/factors/ 下的对应文件（financial_factors.py /
                  price_factors.py / alt_factors.py）中添加因子函数

2. 重建因子面板 → python -m scripts.build_factor_panels
                   输出：data/processed/factor_panels/<factor_name>.parquet
                   ⚠️ 不跑这步，评价脚本找不到新因子

3. 隔离评价新因子 → python -m scripts.run_factor_evaluation \
                         --output-dir reports/factor_evaluation_<name>
                   ⚠️ 必须指定 --output-dir，否则覆盖主池结果
                   主要看：ic_result.csv（IC 检验）、factor_evaluation_report.md（完整报告）
                   结论：IC_IR、移位测试、五分组 Sharpe ── 全过才纳入

4. 更新主因子池 → 确认新因子通过所有门后，重跑全量评价：
                   python -m scripts.run_factor_evaluation \
                       --output-dir reports/factor_evaluation
                   ⚠️ 不能手动编辑 final_factors.json：
                      该文件由脚本自动生成，含哈希校验，手动改会触发断言报错
                   脚本自动更新 final_factors.json（含稳定性权重和元数据）

5. 跑信号实验 → 新建 configs/pipelines/challenger_new_factor_<name>.py
                  与当前主基线相比，仅因子池不同
                  python -m scripts.run_experiment --spec ...

6. 对比结果   → python -m scripts.compare_runs
```

**判断标准（训练期）：**
- IC_IR ≥ 0.30
- |IC 均值| ≥ 0.02
- 方向稳定性 ≥ 55%
- 移位测试：`lead_ratio < 2.0x`（否则疑似未来函数）
- IC Decay：lag3 方向与 lag1 一致（信号寿命 ≥ 3 个月）

---

### 4.2 信号合成方法研究

信号合成是：给定一组因子，用什么方式把它们合成为最终打分？

**你需要改的地方：`SignalSpec`**

| 参数 | 当前主基线 | 可以测试的方向 |
|------|-----------|---------------|
| `method` | `"ridge"` | `"icir"`（IC_IR 加权） |
| `training_mode` | `"expanding"` | `"rolling"`（固定回看窗口） |
| `window_months` | `None`（expanding） | `36` / `48` / `60` |
| `purge_months` | `2` | `1` / `3`（泄露控制） |
| `alpha_grid` | 7 个候选 | 加密或扩大范围 |

**示例：测试 IC_IR 合成 vs Ridge 合成哪个更好**

```python
# configs/pipelines/challenger_icir_te6_lam0050.py
SPEC = ExperimentSpec(
    experiment_id="challenger_icir_te6_lam0050",
    description="用 IC_IR 加权替代 Ridge 信号合成，其余参数不变",
    period_scope="train_valid",
    signal=SignalSpec(
        method="icir",           # ← 只改这一个
        target="excess_return",
        training_mode="expanding",
    ),
    optimizer=OptimizerSpec(...),  # 与主基线相同
    backtest=BacktestSpec(),
)
```

**注意：每次只改一个变量。** 同时改信号方法又改优化参数，就无法判断是哪个变量起作用。

---

### 4.3 优化器参数研究

优化器控制：怎么把信号转换为持仓权重？

**你需要改的地方：`OptimizerSpec`**

| 参数 | 含义 | 当前值 | 方向 |
|------|------|--------|------|
| `te_target_annual` | 年化跟踪误差目标 | `0.06`（6%） | 更激进：0.08，更保守：0.04 |
| `turnover_lambda` | 换手惩罚系数 | `0.005` | 更大 → 换手更少但 IR 可能下降 |
| `topn` | L3 兜底选股数 | `50` | `30` / `80` |
| `industry_max_dev` | 行业偏离上限 | `0.03` | 更宽松：0.05 |
| `single_max_dev` | 个股偏离上限 | `0.015` | 适当放宽 |

**示例：跑一个 TE=8% 的实验**

```python
# configs/pipelines/challenger_te8_lam0050.py
SPEC = ExperimentSpec(
    experiment_id="challenger_te8_lam0050",
    description="放宽 TE 目标到 8%，观察 IR 是否有改善",
    period_scope="train_valid",
    signal=SignalSpec(method="ridge", target="excess_return", training_mode="expanding"),
    optimizer=OptimizerSpec(
        te_target_annual=0.08,    # ← 只改这一行
        turnover_lambda=0.005,
        topn=50,
    ),
    backtest=BacktestSpec(),
)
```

---

### 4.4 多变量网格实验

同时测试多个参数组合（网格搜索）。

**做法：写一个循环生成多个 Spec 文件，批量运行。**

```python
# tools/gen_te_lambda_grid.py（一次性辅助脚本，不进框架）
from pathlib import Path

te_grid     = [0.04, 0.06, 0.08]
lambda_grid = [0.002, 0.005, 0.010]
out_dir = Path("configs/pipelines/grid")   # ← 放子目录，与 baseline/challenger 分开
out_dir.mkdir(parents=True, exist_ok=True)

TEMPLATE = """\
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="{exp_id}",
    description="网格实验 te={te_pct}% lambda={lam}",
    period_scope="train_valid",
    signal=SignalSpec(method="ridge", target="excess_return", training_mode="expanding"),
    optimizer=OptimizerSpec(te_target_annual={te}, turnover_lambda={lam}, topn=50),
    backtest=BacktestSpec(),
)
"""

for te in te_grid:
    for lam in lambda_grid:
        exp_id = f"grid_te{int(te*100):02d}_lam{int(lam*1000):04d}"
        content = TEMPLATE.format(
            exp_id=exp_id,
            te_pct=int(te * 100),
            te=te,
            lam=lam,
        )
        (out_dir / f"{exp_id}.py").write_text(content, encoding="utf-8")
        print(f"已生成 {exp_id}.py")
```

**然后批量运行（PowerShell）：**
```powershell
Get-ChildItem configs/pipelines/grid/*.py | ForEach-Object {
    python -m scripts.run_experiment --spec $_.FullName
}
```

**然后一次性看所有结果：**
```bash
python -m scripts.compare_runs --run-ids grid_te04_lam0020 grid_te04_lam0050 ...
```

---

### 4.5 分阶段运行（信号复用）

信号计算是最慢的步骤（几十分钟）。如果你只想测试不同的优化器参数，可以复用已有的信号。

```bash
# 只跑 portfolio + backtest，信号从已有 run 读取
python -m scripts.run_experiment \
    --spec configs/pipelines/challenger_te8_lam0050.py \
    --from-stage portfolio \
    --input-signal-run 20260527_141545__baseline_expanding_ridge_te6_lam0050
```

这样不重新计算信号，只跑组合优化和回测，速度很快。

---

### 4.6 测试集评估（最终出结论，最多 3 次）

**测试集是一次性资源，用完就失去无偏性。请只在研究定型之后使用。**

流程：
1. 在训练/验证期跑完所有实验，确定最终主基线
2. 把主基线晋升（`test_set_used` 必须为 false）：
   ```bash
   python -m scripts.promote_run --run-id <id> --reason "..."
   ```
3. 跑测试集评估：`python -m scripts.run_test_pipeline --run-id 1`
4. 立即 git commit（锁文件机制要求）

> **注意**：`promote_run` 默认只允许晋升 `train_valid` scope 的 run。若需晋升 test scope 的 run，需加 `--scope test --allow-test-set`，但这种情况极少见。

---

## 五、硬指标阈值（判断一个 run 是否合格）

| 指标 | 阈值 | 说明 |
|------|------|------|
| IR（信息比率） | ≥ 0.5 | 超额收益 / 跟踪误差 |
| 超额最大回撤 | ≤ 10% | 验证期最大相对回撤 |
| 年化双边换手 | 500%–1500% | 太低说明持仓僵化，太高则成本侵蚀收益 |

这三个阈值是自动检查的——每个 run 目录下的 `runs/train_valid/<run_id>/reports/self_check.md` 里会显示 `✅ PASS` 或 `❌ FAIL`。

---

## 六、当前实验状态（参考）

| run | 角色 | 信号/优化器 | IR | 超额收益 | 最大回撤 |
|-----|------|-----------|-----|---------|---------|
| frozen_baseline_icir_topn50_ew | **冻结基线**（永不晋升）| ICIR + TopN50 EW（无 QP） | 0.924 | +6.24% | -7.48% |
| baseline_expanding_ridge | **已晋升主线** | Ridge + QP TE=6% | 0.408 | +2.44% | -8.16% |
| challenger_rolling36 | 挑战者 | Ridge Rolling-36 + QP | 0.966 | +5.80% | -8.38% |
| challenger_rolling48 | 挑战者 | Ridge Rolling-48 + QP | **1.489** | +8.63% | -5.82% |
| challenger_rolling60 | 挑战者 | Ridge Rolling-60 + QP | 1.176 | +6.87% | -6.73% |
| challenger_decay_ridge_hl24 | 挑战者 | Ridge Decay hl=24m + QP | 0.626 | +3.65% | -8.32% |
| challenger_decay_ridge_hl36 | 挑战者 | Ridge Decay hl=36m + QP | 0.295 | +1.71% | -8.79% |
| challenger_decay_ridge_hl48 | 挑战者 | Ridge Decay hl=48m + QP | 0.289 | +1.68% | -8.28% |

> **⚠️ 不可直接比较的两个 baseline**：
> - **冻结基线**（IR=0.924）：ICIR加权 + TopN50等权（无 QP 约束），是方法最简单的不变对照锚，永不重跑。
> - **已晋升主线**（IR=0.408）：Ridge + QP（含 TE=6% 约束），是当前注册在 `mainline.json` 的实验基准。
>
> 两者信号方法和优化器均不同，IR 差距（0.924 vs 0.408）**不代表主线"退步"**，而是 QP 约束压缩了组合暴露所带来的正常差异。消融/挑战者实验应与**已晋升主线**（0.408）对比，不与冻结基线比较。

rolling48 在验证期指标最优（IR=1.489），但还未晋升（需人工审查后决定）。

---

## 七、常见操作速查

```bash
# 运行一个新实验（全流程）
python -m scripts.run_experiment --spec configs/pipelines/<spec>.py

# 只跑信号阶段
python -m scripts.run_experiment --spec ... --to-stage signal

# 复用已有信号，只跑组合+回测
python -m scripts.run_experiment \
    --spec ... \
    --from-stage portfolio \
    --input-signal-run <run_id>

# 生成最新比较板（mainline + 已注册挑战者）
python -m scripts.compare_runs

# 比较指定的几个 run
python -m scripts.compare_runs --run-ids <id1> <id2> <id3>

# 比较测试集 run（默认 scope=train_valid，test scope 需显式指定）
python -m scripts.compare_runs --scope test --run-ids <test_run_id>

# 晋升 run 为主基线（train_valid scope，常规用法）
python -m scripts.promote_run --run-id <run_id> --reason "原因"

# 晋升 test scope 的 run（需显式声明，极少用）
python -m scripts.promote_run --run-id <run_id> --scope test --allow-test-set --reason "原因"

# 测试集评估（第 1 次）
python -m scripts.run_test_pipeline --run-id 1
```

---

## 八、experiment_id 命名规范

```
<类型>_<核心变量描述>_<te档位>_<lambda档位>

类型：
  baseline        — 主基线候选
  frozen_baseline — 冻结基线，最简可解释方法（ICIR加权 + TopN等权），永不变更，不进晋升流程
  challenger      — 挑战者（需与主基线对比）
  grid            — 网格实验
  ablation        — 消融实验（去掉某个因子/模块）
  debug           — 调试用，不进注册表

示例：
  baseline_expanding_ridge_te6_lam0050
  challenger_rolling48_te6_lam0050
  challenger_icir_te8_lam0050
  grid_te06_lam0100
  ablation_no_momentum_te6_lam0050
```

---

## 九、不应该做的事

| 操作 | 原因 |
|------|------|
| 直接修改 `runs/` 下的任何文件 | run 目录是不可变的，修改会破坏审计追溯 |
| 在没有 Spec 的情况下手动运行信号/组合/回测脚本 | 产物不会进 registry，无法比较 |
| 同时改多个变量跑一个实验 | 无法判断哪个变量贡献了改善 |
| 在训练/验证期没定型前用测试集 | 测试集只有 3 次机会，用完失去无偏性 |
| 修改或重跑 `frozen_baseline_icir_topn50_ew` spec | 冻结基线是不可变对照锚，修改后所有历史对比失去意义 |
