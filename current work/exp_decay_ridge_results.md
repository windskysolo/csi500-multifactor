# EW-Ridge 实验结论（阶段一）

> 完成日期：2026-05-28  
> 关联计划：`exp_time_varying_ridge_plan.md § 1`

---

## 结果汇总

验证期 2021-2022，V2 优化权重：

| 方案 | IR | 年化超额 | 超额MDD | TE | 月胜率 | 年化换手 | 硬指标 |
|---|---:|---:|---:|---:|---:|---:|---|
| expanding（基线） | 0.408 | +2.44% | -8.16% | 5.97% | 43.5% | 974% | IR FAIL |
| **decay hl=24m** | **0.626** | **+3.65%** | **-8.32%** | **5.82%** | **39.1%** | **1041%** | **全 PASS** |
| decay hl=36m | 0.295 | +1.71% | -8.79% | 5.79% | 43.5% | 1008% | IR FAIL |
| decay hl=48m | 0.289 | +1.68% | -8.28% | 5.81% | 39.1% | 1008% | IR FAIL |
| rolling-48m（当前最优）| 1.489 | +8.63% | -5.82% | 5.80% | 56.5% | 1002% | 全 PASS |

alpha 均由 walk-forward CV 选出，三个 hl 版本均选出 alpha=5000。

---

## 关键发现

**1. hl=24m 是唯一有效的配置，但幅度有限**

hl=24（半衰期 2 年）比 expanding 基线（IR=0.408）有明显提升（+0.218），  
但远弱于 rolling-48m（IR=1.489）。衰减窗口越短（hl=24）信号越好，  
越长（hl=36/48）越接近 expanding，效果反而更差。

**2. hl=36/48 表现反常：比 expanding 基线还差**

- expanding: IC_IR=0.628，验证 IR=0.408  
- hl=36: IC_IR=0.489，验证 IR=0.295  
- hl=48: IC_IR=0.527，验证 IR=0.289  

训练集 IC_IR（compare board 中的 `IC_IR` 列）hl=48 > hl=36 > expanding，  
但验证期 IR 排序完全反转。说明更长的半衰期导致模型在 2019-2020 年的训练信号  
过度拟合了该时期的风格，到 2021-2022 风格切换时适应更慢，反而不如等权 expanding。

**3. EW-Ridge 没有达到"介于 expanding 和 rolling 之间"的预期**

- 假设：衰减权重能平滑过渡，优于硬截断的 rolling
- 实际：hl=24 确实优于 expanding，但仍大幅弱于 rolling-48m
- 原因假说：rolling 的硬截断丢弃了 2012-2018 年的旧数据，  
  这些旧数据的因子逻辑与 2021-2022 存在结构性差异（质量因子翻转），  
  而 EW-Ridge 的指数衰减仍然保留了这些"有害"的历史信息，只是权重更低

---

## run_id 记录

| 实验 | run_id |
|---|---|
| decay_hl24 | 20260528_141041__challenger_decay_ridge_hl24_te6_lam0050 |
| decay_hl36 | 20260528_141324__challenger_decay_ridge_hl36_te6_lam0050 |
| decay_hl48 | 20260528_141729__challenger_decay_ridge_hl48_te6_lam0050 |

---

## 下一步决策

**是否进入阶段二（贝叶斯）**

按计划的前提条件：EW-Ridge 需要"至少优于 expanding 基线"。  
hl=24 满足（0.626 > 0.408），可以进入。

但进入贝叶斯阶段前，更值得优先确认的问题：

1. **为什么 rolling 远优于 decay？**  
   核心假说：旧历史数据（2012-2018）因风格切换对 2021-2022 有"负迁移"。  
   验证方法：对比 rolling-48m 和 decay-hl24 的系数历史（`coef_history.parquet`），  
   看 piotroski_f / q_roe 等质量因子的系数在 2021 前后是否走向相反。

2. **贝叶斯对 hl=24 的改善空间有限**  
   因为 hl=24 的 EW-Ridge 和 Bayes（λ-discount）数学等价，  
   信号改善只能来自置信度加权（confidence weighting），预期有限。

**建议**：先做系数稳定性诊断（读 coef_history），再决定是否跑贝叶斯实验。
