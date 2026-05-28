# 阶段检验计划 — P0/P1 诊断与修正手册

> 基于 `risk_register.md` V1.2 编写  
> 创建日期：2026-05-27  
> 适用版本：V1.2（Ridge v2，18 因子，λ=0.005，验证期 IR=0.503）  
> 目的：在消耗任何测试集配额之前，系统性诊断 P0 和 P1 风险，对每种可能的根因给出对应的修正路径，并在修正后通过回归验证确认结果不倒退

---

## 总体流程

```
开始
  │
  ▼
P0 全量执行（约 20 分钟，3 项）
  │
  ├── 任一 P0 失败 → 执行对应修正 → 修正验证 → 重新确认 P0
  │
  └── P0 全部通过
         │
         ▼
       P1 全量执行（约 2 小时，3 项）
         │
         ├── 发现需要修正 → 执行修正 → 修正验证 → 继续 P1
         │
         └── P1 全部完成
                │
                ▼
             P1 决策门评估
                │
                ├── 通过 → 运行测试集（按 R-O1 规则登记）
                │
                └── 不通过 → 修正后再次评估
```

**强制约束**：P0 中任何一项未通过且未修正，禁止运行测试集，不论 P1 结果如何。

---

## 第一部分：P0 检验（必须全部通过）

### P0-1｜确认 Ridge 对 `piotroski_f` 的验证期权重方向

**对应风险**：R-S1  
**预计耗时**：5 分钟  
**为什么是 P0**：如果 Ridge 正在用正权重驱动一个方向反转的因子，这是主动做错方向，不排查直接跑测试集等于在已知错误上下注

#### 诊断步骤

```python
import pandas as pd
import numpy as np

# 加载 walk-forward Ridge 权重时序
# 路径需根据实际 combiner 输出调整
weights = pd.read_parquet(
    "experiments/v1.2/results/ridge_lam_0050/weights_meta.parquet"
)

# 提取验证期（2021-01 到 2022-12）
valid_w = weights.loc["2021":"2022", "piotroski_f"]

print("=== piotroski_f 验证期权重诊断 ===")
print(f"均值:       {valid_w.mean():.4f}")
print(f"中位数:     {valid_w.median():.4f}")
print(f"正值期数:   {(valid_w > 0).sum()} / {len(valid_w)}")
print(f"负值期数:   {(valid_w < 0).sum()} / {len(valid_w)}")
print(f"最近 6 期:\n{valid_w.tail(6)}")
```

#### 判读规则

| 诊断结果 | 含义 | 决策 |
|----------|------|------|
| 均值 ≤ −0.05，且负值期数 > 75% | Ridge 已主动反转方向，模型自我纠正成功 | ✅ P0-1 通过，无需修正 |
| 均值在 (−0.05, +0.05)，正负期数接近 | 权重被压近零，中性处理，影响极小 | ✅ P0-1 通过（接受），记录均值后继续 |
| 均值 ≥ +0.05，或正值期数 > 50% | **Ridge 正在用错误方向的信号** | ❌ P0-1 失败，进入修正路径（见下） |

#### 修正路径（P0-1 失败时）

**先执行 P1-1（piotroski_f 分年度 IC 分析）来判断根因**，再根据 P1-1 结论选择修正方案：

> 若 P0 阶段无法等待 P1-1，使用**临时修正方案（强制截断）**作为止血措施

**临时修正方案（5 分钟）**

在 `src/signal/combiner.py` 的 Ridge 权重输出后，添加符号约束：

```python
# combiner.py —— Ridge 系数输出后，信号合成前
# 将 piotroski_f 权重强制截断为非正值（允许模型将其降到负权重，但不允许正权重）
if "piotroski_f" in weights.index:
    weights["piotroski_f"] = min(float(weights["piotroski_f"]), 0.0)
```

临时修正后执行**回归验证**（见第三部分），确认 IR ≥ 0.50 且 MDD ≤ 7.96%。

**P1-1 完成后**，根据根因结论替换为对应的永久修正方案（见 P1-1 修正路径）。

---

### P0-2｜确认测试集脚本使用 Ridge v2 信号（R-A2）

**对应风险**：R-A2  
**预计耗时**：5 分钟  
**为什么是 P0**：IC_IR 流水线验证期 IR=0.402，不满足完成标准。跑错流水线等于直接失败并浪费配额

#### 诊断步骤

```bash
# 在项目根目录执行
grep -n "ridge\|ic_ir\|combiner\|method\|signal_type" scripts/run_test_pipeline.py
```

同时检查 combiner 调用参数：

```python
# 在脚本中找到 combiner 调用点，确认传入的方法参数
# 预期：method="ridge" 或 combiner_type="ridge_v2" 或类似标识
# 非预期：method="ic_ir" 或 method="weighted" 等
```

如果脚本中没有显式的方法参数，追踪 `src/signal/combiner.py` 的默认值：

```bash
grep -n "def combine\|default\|method=" src/signal/combiner.py | head -20
```

#### 判读规则

| 诊断结果 | 决策 |
|----------|------|
| 脚本明确传入 Ridge v2 标识，或 combiner 默认方法为 Ridge | ✅ P0-2 通过 |
| 脚本传入 IC_IR 加权标识 | ❌ P0-2 失败，执行修正 C |
| 无法从脚本判断（无显式参数） | 在脚本中加临时打印语句，在验证期空跑一次确认 IR 输出值 ≈ 0.503 |

#### 修正 C（P0-2 失败时）

1. 定位 `scripts/run_test_pipeline.py` 中的 combiner 调用
2. 将方法参数修改为 Ridge v2 对应的枚举值
3. **修正后验证**：在验证期（2021-2022）空跑一次，确认 IR 输出 ≈ 0.503（±0.003 容差）
4. 验证通过后，方可认为 P0-2 通过

---

### P0-3｜确认 Walk-forward 窗口类型并记录（R-A3）

**对应风险**：R-A3  
**预计耗时**：10 分钟  
**为什么是 P0**：窗口类型决定 R-F1/R-F2/R-B1 的判断逻辑，未记录会导致 P1 分析结论基于错误假设

#### 诊断步骤

```bash
# 定位 Ridge fit 的窗口构建代码段
grep -n "X_train\|expanding\|rolling\|window\|iloc\[:t\]\|loc\[:t\]" src/signal/combiner.py
```

阅读对应代码段，判断 `X_train` 的切片方式：

```python
# 扩展窗口（expanding window）的特征
# 每期训练数据从起始点到当前期，窗口随时间增大
X_train = data.loc[:t]                  # 形如这种

# 滚动窗口（rolling window）的特征
# 每期只用最近固定 N 期的数据
X_train = data.iloc[t - window_size:t]  # 形如这种
```

#### 判读与记录规则

| 诊断结果 | 对 P1 的影响（需记录） |
|----------|----------------------|
| **扩展窗口** | piotroski_f 的历史正向信号（2012-2020）会稀释 2021-2022 反转，P0-1 中均值偏正的可能性更高；R-F2 的 5 个 warn 因子降权速度较慢；R-B1 回撤期 Ridge 对新风格的适应更滞后 |
| **滚动窗口**（窗口长度 N 月） | piotroski_f 的方向反转能在 N 个月内被学到为负权重，P0-1 通过概率更高；但极端行情窗口（如 2022 年全年熊市）可能导致过度降权 |
| **两种混用（不同因子组用不同窗口）** | ❌ 记录为设计缺陷，需要统一——推荐统一使用扩展窗口（历史数据越多模型越稳定，适合 A 股长期因子） |

**本检验不要求修正**，只要记录清楚即可，但必须将结论更新至 `risk_register.md` R-A3 的"当前状态"字段。

---

### P0 决策门

以下三项全部满足，才允许进入 P1：

- [ ] P0-1：piotroski_f 验证期权重均值 ≤ +0.05，或已执行临时修正并通过回归验证
- [ ] P0-2：测试集脚本已确认使用 Ridge v2，或已执行修正 C 并空跑验证 IR ≈ 0.503
- [ ] P0-3：walk-forward 窗口类型已明确，已记录到 R-A3

---

## 第二部分：P1 检验（P0 全部通过后执行）

### P1-1｜`piotroski_f` 分年度 IC 分析与根因判断（R-F1）

**对应风险**：R-F1  
**预计耗时**：30 分钟  
**目标**：区分两种假说，为 P0-1 的临时修正选择永久替代方案

#### 诊断步骤

**步骤 1：逐年 IC 统计**

```python
import pandas as pd
import numpy as np
from scipy import stats

# 加载 piotroski_f 月度 IC 序列
# 根据实际存储位置调整路径
ic_df = pd.read_csv(
    "experiments/v1.2/results/ridge_lam_0050/ic_series.csv",
    index_col=0, parse_dates=True
)
piotroski_ic = ic_df["piotroski_f"]

# 分年度统计
yearly = piotroski_ic.groupby(piotroski_ic.index.year).agg(
    mean_ic=("mean"),
    ic_ir=lambda x: x.mean() / x.std() if x.std() > 1e-8 else 0,
    n_months="count",
    neg_pct=lambda x: (x < 0).mean()
)
print("=== piotroski_f 分年度 IC ===")
print(yearly.to_string())
```

**步骤 2：重点对比 2021 vs 2022**

```python
ic_2021 = piotroski_ic.loc["2021"]
ic_2022 = piotroski_ic.loc["2022"]

t21, p21 = stats.ttest_1samp(ic_2021.dropna(), 0)
t22, p22 = stats.ttest_1samp(ic_2022.dropna(), 0)

print(f"\n2021: 均值={ic_2021.mean():.4f}, IC_IR={ic_2021.mean()/ic_2021.std():.3f}, "
      f"t={t21:.3f}, p={p21:.3f}")
print(f"2022: 均值={ic_2022.mean():.4f}, IC_IR={ic_2022.mean()/ic_2022.std():.3f}, "
      f"t={t22:.3f}, p={p22:.3f}")
```

**步骤 3（条件执行，如有子指标数据）：9 个子指标验证期 IC 方向**

```python
# 仅当 src/factors/piotroski.py 计算了子指标并存入了中间文件时执行
subcomponents = [
    "F_ROA", "F_dROA", "F_CFO", "F_ACCRUAL",
    "F_dLEVER", "F_dLIQUID", "F_EQ_OFFER",
    "F_dMARGIN", "F_dTURN"
]
# 找到子指标 IC 文件路径后替换下方路径
sub_ic = pd.read_csv("experiments/v1.2/results/sub_ic_piotroski.csv",
                     index_col=0, parse_dates=True)

print("\n=== piotroski_f 子指标验证期（2021-2022）IC 均值 ===")
for comp in subcomponents:
    if comp in sub_ic.columns:
        ic_val = sub_ic.loc["2021":"2022", comp].mean()
        flag = " ← 方向可疑" if ic_val < -0.01 else ""
        print(f"  {comp}: {ic_val:.4f}{flag}")
```

#### 根因判读决策树

```
┌─────────────────────────────────────────────────────────────────┐
│  2021 IC 均值 < 0  AND  2022 IC 均值 < 0                       │
│                                                                 │
│  ├── 两年 t 检验均显著（p < 0.05）                              │
│  │     → 强支持假说 B（A 股规则错误）                           │
│  │       执行 [修正 D：修正子指标方向]                          │
│  │                                                             │
│  └── 任一年 p ≥ 0.05（信号较弱）                               │
│        → 假说 A/B 均可能，不确定                               │
│          执行 [修正 E：保守降权上限]                            │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  2021 IC 均值 < 0  AND  2022 IC 均值 ≥ 0（或接近 0）           │
│  → 支持假说 A（2021 极端行情触发，2022 减弱）                   │
│    执行 [修正 F：滚动 IC_IR 触发器]                             │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  两年 IC 均值均在 (−0.01, +0.01)                                │
│  → 失效（信噪比低）而非方向反转                                 │
│    Ridge 自然降权，配合 P0-1 权重均值 < 0.05 则无需额外修正     │
└─────────────────────────────────────────────────────────────────┘
```

#### 修正 D｜修正子指标方向（假说 B：A 股规则错误）

**适用条件**：2021 和 2022 IC 均值均显著为负（两年 p < 0.05）

**步骤**：
1. 用步骤 3 的结果找出验证期 IC 均值 < −0.01 的子指标（如 `F_EQ_OFFER`、`F_dLEVER`）
2. 在 `src/factors/piotroski.py` 中将对应子指标的评分方向取反

```python
# 示例：若 F_EQ_OFFER 在 A 股方向相反（A 股融资行为逻辑不同）
# 原始：融资 = 0分，未融资 = 1分（美股逻辑：融资摊薄是负面信号）
# 修正：融资 = 1分，未融资 = 0分（A 股逻辑：主动融资表示成长信心）
df["F_EQ_OFFER"] = 1 - df["F_EQ_OFFER"]  # 方向取反

# piotroski_f 总分重新汇总
df["piotroski_f"] = df[subcomponents].sum(axis=1)
```

3. **修正后验证**：重新运行验证期因子 IC，确认 piotroski_f 2021-2022 IC 均值转正（或至少不显著为负）
4. 重新运行验证期回测，执行回归验证（见第三部分）

**预期结果**：修正后验证期 piotroski_f IC_IR > 0，Ridge 权重自然转为正向，IR 应有所提升

#### 修正 E｜保守权重上限（假说不明确，p ≥ 0.05 的情况）

**适用条件**：两年 IC 均为负但统计不显著，无法确定根因

```python
# src/signal/combiner.py —— Ridge 系数输出后
# 对 piotroski_f 设置权重上限为 0.02（允许模型微量使用，但不让它主导）
import numpy as np

MAX_PIOTROSKI_WEIGHT = 0.02
if "piotroski_f" in weights.index:
    weights["piotroski_f"] = np.clip(
        float(weights["piotroski_f"]), -np.inf, MAX_PIOTROSKI_WEIGHT
    )
```

**修正后验证**：IR 下降不超过 0.02，MDD 不恶化。若 IR 下降 > 0.02，说明修正过强，将上限改为 0.05 重试。

#### 修正 F｜滚动 IC_IR 触发器（假说 A：风格周期）

**适用条件**：2021 IC 负、2022 IC 接近 0 或为正，验证为风格周期而非规则错误

```python
# src/signal/combiner.py —— 每期信号合成时执行
# 用 12 个月滚动 IC_IR 动态决定是否使用 piotroski_f

def apply_style_trigger(weights, ic_series, factor_name, window=12):
    """当因子近期 IC_IR < 0 时，将其权重清零"""
    recent_ic = ic_series[factor_name].iloc[-window:]
    ic_ir = recent_ic.mean() / recent_ic.std() if recent_ic.std() > 1e-8 else 0
    if ic_ir < 0:
        weights[factor_name] = 0.0
    return weights

weights = apply_style_trigger(weights, ic_series, "piotroski_f", window=12)
```

**修正后验证**：在验证期中找到触发器应激活的月份（2021 年），确认模型在那些期间确实将权重清零。

---

### P1-2｜V1.2 超额最大回撤跳升根因 + Bootstrap（R-B1）

**对应风险**：R-B1  
**预计耗时**：1 小时  
**目标**：定位回撤的具体时段和因子归因，用 Bootstrap 量化测试期突破 10% 上限的概率，决定是否需要修正

#### 诊断步骤

**步骤 1：定位回撤最深时段**

```python
import pandas as pd
import numpy as np

# 加载 V1.2 验证期 NAV（超额收益净值曲线）
nav = pd.read_parquet(
    "experiments/v1.2/results/ridge_lam_0050/backtest_nav_valid.parquet"
)
# 根据实际列名调整，可能是 "excess_nav"、"alpha_nav" 等
excess_nav = nav["excess_nav"]

# 计算每个时点的最大回撤（从前期高点的跌幅）
rolling_peak = excess_nav.cummax()
drawdown = (excess_nav - rolling_peak) / rolling_peak

print(f"验证期超额最大回撤: {drawdown.min():.4f}")
print(f"\n回撤最深的 5 个月份:")
print(drawdown.nsmallest(5))

# 找出最大回撤时段（从峰值到谷底）
trough_date = drawdown.idxmin()
peak_date = excess_nav.loc[:trough_date].idxmax()
print(f"\n最大回撤时段: {peak_date} → {trough_date}")
```

**步骤 2：回撤时段因子贡献分析**

```python
# 加载验证期信号合成权重
weights_ts = pd.read_parquet(
    "experiments/v1.2/results/ridge_lam_0050/actual_weights_v2.parquet"
)

# 聚焦回撤最深的 3 个月（从 trough_date 往前 3 个月）
key_months = drawdown.nsmallest(3).index

print("=== 回撤关键月份因子权重 ===")
key_factors = ["rev_acceleration", "high_52w_v2", "piotroski_f", "q_roe", "roe_delta"]
for month in key_months:
    if month in weights_ts.index:
        print(f"\n{month}:")
        for f in key_factors:
            if f in weights_ts.columns:
                print(f"  {f}: {weights_ts.loc[month, f]:.4f}")
```

**步骤 3：V1.1 vs V1.2 同期对比**

```python
# 加载 V1.1 验证期 NAV（用于对比）
nav_v11 = pd.read_parquet(
    "experiments/v1.1/results/backtest_nav_valid.parquet"  # 路径按实际调整
)
excess_nav_v11 = nav_v11["excess_nav"]

# 对比回撤最深月份的月度超额收益
for month in key_months:
    month_str = str(month)[:7]
    v12_ret = excess_nav.loc[month_str].iloc[-1] / excess_nav.loc[month_str].iloc[0] - 1 \
              if len(excess_nav.loc[month_str]) > 0 else None
    v11_ret = excess_nav_v11.loc[month_str].iloc[-1] / excess_nav_v11.loc[month_str].iloc[0] - 1 \
              if len(excess_nav_v11.loc[month_str]) > 0 else None
    print(f"{month_str}: V1.1={v11_ret:.4f}, V1.2={v12_ret:.4f}, "
          f"差异={v12_ret - v11_ret:.4f}" if v11_ret and v12_ret else f"{month_str}: 数据不完整")
```

**步骤 4：Bootstrap 蒙特卡洛估算 P(MDD > 10%)**

```python
np.random.seed(42)

# 获取月度超额收益序列
monthly_excess_nav = excess_nav.resample("ME").last()
monthly_excess_returns = monthly_excess_nav.pct_change().dropna().values

n_months = len(monthly_excess_returns)
test_period_months = 30  # 测试期 2023-2025 约 30 个月（保守估计）
n_simulations = 10_000

def max_drawdown(returns):
    cum = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    return dd.min()

bootstrap_mdd = [
    max_drawdown(np.random.choice(monthly_excess_returns, size=test_period_months, replace=True))
    for _ in range(n_simulations)
]
bootstrap_mdd = np.array(bootstrap_mdd)

prob_exceed = (bootstrap_mdd < -0.10).mean()
print("=== Bootstrap 蒙特卡洛结果 ===")
print(f"模拟次数:               {n_simulations}")
print(f"模拟测试期长度:         {test_period_months} 个月")
print(f"P(MDD > 10%):          {prob_exceed:.2%}")
print(f"MDD 中位数:            {np.median(bootstrap_mdd):.2%}")
print(f"MDD 90th 分位数:       {np.percentile(bootstrap_mdd, 90):.2%}")
print(f"MDD 95th 分位数:       {np.percentile(bootstrap_mdd, 95):.2%}")
```

#### 根因判读决策树

```
步骤 1-3 结果
  │
  ├── 回撤集中在 rev_acceleration / high_52w_v2 权重高的月份
  │     │
  │     ├── P(MDD > 10%) < 20%
  │     │     → ✅ R-B1 风险可接受，记录归因结论，P1-2 通过，无需修正
  │     │
  │     └── P(MDD > 10%) ≥ 20%
  │           → ⚠️ 风险超过容忍阈值，执行 [修正 G：动态权重上限]
  │
  ├── 回撤与上述因子权重无明显相关
  │     → 调查行业集中度或换手成本是否是主因
  │       若是行业集中度 → 执行 [修正 H：行业中性化约束]
  │       若是换手成本  → 检查滑点参数设置是否偏保守，调整后重测
  │
  └── V1.1 和 V1.2 同期回撤差异 < 1% 的月份
        → 说明回撤主要来自市场环境而非新增因子，属于系统性风险
          记录在 R-A1，无需修正
```

#### 修正 G｜动态权重上限（P(MDD > 10%) ≥ 20% 时）

```python
# src/signal/combiner.py —— Ridge 权重输出后，归一化前

# 对高波动因子设置权重上限
WEIGHT_CAPS = {
    "rev_acceleration": 0.25,   # 验证期 IC_IR=0.725，Ridge 可能过度集中
    "high_52w_v2":      0.20,   # 动量因子，熊市放大回撤
}

for factor, cap in WEIGHT_CAPS.items():
    if factor in weights.index:
        weights[factor] = np.clip(float(weights[factor]), 0, cap)

# 重新归一化（保持权重之和不变）
total = weights.abs().sum()
if total > 0:
    weights = weights / total
```

**修正后验证标准**：
- 验证期 IR ≥ 0.49（允许因权重截断损失 ≤ 0.013）
- 验证期超额最大回撤 ≤ 7.00%（从 7.96% 降低，确认有改善）
- 重新运行 Bootstrap：P(MDD > 10%) 下降至 < 20%

若修正 G 导致 IR 下降 > 0.03，说明对 rev_acceleration 的截断过强（该因子是 V1.2 IR 提升的主要来源），将 `rev_acceleration` 上限放宽至 0.30 重试。

#### 修正 H｜行业中性化约束（回撤由行业集中度引起时）

若 V1.2 在回撤时段相比 V1.1 明显更集中于某一行业（集中度差异 > 20pp），在持仓构建时添加行业偏离约束：

```python
# portfolio_constructor.py 中添加行业偏离约束
# 每个行业的持仓权重偏离基准不超过 ±5%
industry_deviation_limit = 0.05
```

---

### P1-3｜18 因子截面相关矩阵计算（R-F7）

**对应风险**：R-F7  
**预计耗时**：30 分钟  
**目标**：找出 |r| > 0.7 的因子对，评估多重共线性是否影响 Ridge 系数稳定性

#### 诊断步骤

**步骤 1：计算训练期截面均值相关矩阵**

```python
import pandas as pd
import numpy as np
import warnings

# 加载因子面板（训练期）
factor_panel = pd.read_parquet("data/processed/factor_panel.parquet")
train_panel = factor_panel.loc[:"2020"]  # 截取训练期

# 对每个截面计算 Spearman 相关系数（对异常值更稳健）
def cross_section_corr(df):
    return df.rank().corr(method="pearson")

print("计算中（训练期共 ~106 个月截面，约 1-2 分钟）...")
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    monthly_corr = train_panel.groupby(level="date").apply(cross_section_corr)

# 取训练期均值
mean_corr = monthly_corr.groupby(level=1).mean()

# 找出高相关因子对
print("\n=== 高相关因子对（|r| > 0.7）===")
factors = mean_corr.columns.tolist()
high_pairs = []
for i in range(len(factors)):
    for j in range(i + 1, len(factors)):
        r = mean_corr.iloc[i, j]
        if abs(r) > 0.7:
            high_pairs.append((factors[i], factors[j], r))

if not high_pairs:
    print("无高相关因子对，R-F7 风险不实")
else:
    for f1, f2, r in sorted(high_pairs, key=lambda x: abs(x[2]), reverse=True):
        print(f"  {f1} vs {f2}:  r = {r:.3f}")
```

**步骤 2（条件执行）：检查 rev_yoy vs rev_acceleration 的 Ridge 系数时序稳定性**

仅当步骤 1 发现 `rev_yoy` 与 `rev_acceleration` |r| > 0.7 时执行：

```python
weights_ts = pd.read_parquet(
    "experiments/v1.2/results/ridge_lam_0050/weights_meta.parquet"
)

rev_yoy_w = weights_ts["rev_yoy"]
rev_acc_w = weights_ts["rev_acceleration"]

print("=== 成长组系数时序稳定性 ===")
print(f"rev_yoy:          均值={rev_yoy_w.mean():.4f}, 标准差={rev_yoy_w.std():.4f}")
print(f"rev_acceleration: 均值={rev_acc_w.mean():.4f}, 标准差={rev_acc_w.std():.4f}")
print(f"权重之和均值:      {(rev_yoy_w + rev_acc_w).mean():.4f}")
print(f"权重之和标准差:    {(rev_yoy_w + rev_acc_w).std():.4f}")

# 关键判断：若两者之和的标准差 << 各自标准差，说明是多重共线性导致的权重"随机分配"
# 而非真实的信号强度差异
```

#### 判读规则

| 诊断结果 | 含义 | 决策 |
|----------|------|------|
| 无因子对 \|r\| > 0.7 | ✅ R-F7 风险不实，更新 risk_register 状态 | 无需修正 |
| rev_yoy vs rev_acceleration \|r\| > 0.8，但权重之和标准差 < 各自标准差 | 多重共线性导致权重随机摆动，但组合效果稳定 | 执行 [修正 I 评估] |
| 质量组（q_roe/roe_delta/gross_margin）|r| > 0.7，权重时序大幅波动 | 降权存在惯性延迟 | 观察 P0-1/P1-1 中质量因子的实际权重，若权重已接近 0 则接受 |

#### 修正 I｜剔除 rev_yoy 评估（rev_yoy vs rev_acceleration |r| > 0.8 时）

**先评估，再决策**：

```python
# 对比两因子在验证期的单因子表现
# rev_acceleration 验证期 IC_IR = 0.725（已知）
# rev_yoy 验证期 IC_IR 应在 risk_register R-F2 中有记录（-0.020）

# 评估剔除 rev_yoy 对 IR 的影响：
# 方案：在因子注册表中临时禁用 rev_yoy，重跑验证期回测
```

**决策规则**：
- 剔除 rev_yoy 后 IR 不下降（或 ≤ 0.01 下降）→ 确认剔除，从因子池移除
- 剔除后 IR 下降 0.01~0.02 → 权衡后决定，优先保留（rev_yoy 本身验证期 IC_IR 接近 0，贡献有限）
- 剔除后 IR 下降 > 0.02 → 保留两者，接受多重共线性，在 R-F7 记录

---

### P1 决策门

**P1 完成后，综合评估是否允许运行测试集：**

| 条件 | 决策 |
|------|------|
| P1-1 无需修正（或修正后验证期 IR ≥ 0.50）+ P1-2 P(MDD>10%) < 20% + P1-3 无严重共线性 | ✅ 允许运行测试集 |
| P1-1 已修正且验证期 IR ≥ 0.50 + P1-2 P(MDD>10%) < 20% | ✅ 允许运行测试集，附修正说明 |
| P1-2 P(MDD>10%) ≥ 20% 且已执行修正 G，修正后 P(MDD>10%) < 20% | ✅ 允许运行测试集 |
| P1-2 P(MDD>10%) ≥ 20% 且修正 G 后 IR 下降 > 0.03（修正代价过高） | ⚠️ 需要权衡：IR 可达标，但回撤风险已知。由用户决策是否接受已知风险后运行 |
| 任何修正导致验证期 IR < 0.49 | ❌ 回滚修正，重新分析根因，不允许运行测试集 |

---

## 第三部分：修正后回归验证标准

**每次执行任何修正后**，必须在验证集上重跑回测，确认以下三项均不倒退：

| 指标 | 原始基准 | 通过阈值 | 失败时操作 |
|------|---------|---------|----------|
| 验证期 IR | 0.503 | ≥ 0.490 | 回滚修正，重新分析 |
| 验证期超额最大回撤 | 7.96% | ≤ 7.96%（不得因修正而恶化） | 回滚修正 |
| 验证期换手率 | 9.81x | 5x ~ 15x | 若超 15x，检查修正是否引入额外换手 |

**同时更新 risk_register.md 对应条目的"当前状态"字段**，记录：已排查/根因/修正方案/修正后 IR 变化。

---

## 附录 A：工具与文件路径速查

| 任务 | 预期文件路径 |
|------|------------|
| Ridge 系数时序 | `experiments/v1.2/results/ridge_lam_0050/weights_meta.parquet` |
| 验证期 NAV | `experiments/v1.2/results/ridge_lam_0050/backtest_nav_valid.parquet` |
| 月度信号权重 | `experiments/v1.2/results/ridge_lam_0050/actual_weights_v2.parquet` |
| 月度 IC 序列 | `experiments/v1.2/results/ridge_lam_0050/ic_series.csv` |
| 因子面板 | `data/processed/factor_panel.parquet` |
| 测试集脚本 | `scripts/run_test_pipeline.py` |
| 信号合成 | `src/signal/combiner.py` |
| 因子计算 | `src/factors/financial_factors.py` |
| Piotroski 因子 | `src/factors/piotroski.py` |
| PIT 测试 | `tests/test_pit.py` |
| 风险登记表 | `docs/risk_register.md` |
| 测试集运行日志 | `docs/logs/test_set_run_log.md` |

> 以上路径为推断，运行前请用 `ls` 或 `find` 确认实际路径

---

## 附录 B：检验执行顺序一览

```
时间轴（总计 ~2.5 小时）
│
├── T+0:00  开始 P0-1（5 min）→ 读取 piotroski_f 权重均值
├── T+0:05  开始 P0-2（5 min）→ 检查测试集脚本信号方法
├── T+0:10  开始 P0-3（10 min）→ 读取 combiner.py 窗口构建逻辑，记录结论
│
├── T+0:20  P0 决策门评估
│            └── 若有失败 → 执行对应修正 → 修正验证（约 +20~30 min）
│
├── T+0:20  开始 P1-1（30 min）→ 分年度 IC，判断假说 A/B
│            └── 若需修正 → 执行对应修正方案 → 回归验证（约 +20~40 min）
│
├── T+0:50  开始 P1-2（60 min）→ 回撤归因 + Bootstrap
│            └── 若 P(MDD>10%) ≥ 20% → 执行修正 G → 回归验证（约 +20 min）
│
├── T+1:50  开始 P1-3（30 min）→ 相关矩阵计算
│            └── 若发现高相关对 → 执行修正 I 评估（约 +20 min）
│
└── T+2:20  P1 决策门评估 → 允许/不允许运行测试集
```

---

## 附录 C：修正方案快速索引

| 修正 | 对应场景 | 文件 | 耗时 |
|------|---------|------|------|
| 临时修正（P0 止血） | piotroski_f 权重为正 | `src/signal/combiner.py` | 5 min |
| 修正 C | 测试集脚本用错流水线 | `scripts/run_test_pipeline.py` | 10 min |
| 修正 D | 假说 B（A 股子指标方向错误） | `src/factors/piotroski.py` | 30~60 min |
| 修正 E | 假说不明确，保守降权 | `src/signal/combiner.py` | 10 min |
| 修正 F | 假说 A（风格周期触发器） | `src/signal/combiner.py` | 15 min |
| 修正 G | P(MDD>10%) ≥ 20%，动态权重上限 | `src/signal/combiner.py` | 15 min |
| 修正 H | 行业集中度引起回撤 | `src/portfolio/constructor.py` | 20 min |
| 修正 I | rev_yoy vs rev_acceleration 高相关 | 因子注册表 | 10~30 min |
