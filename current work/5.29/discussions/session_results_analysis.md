# 2026-05-29 研究会话成果记录

> 编写日期：2026-05-29  
> 覆盖范围：本日全量实验与诊断工作  
> 目的：为后续会话提供完整的「做了什么 → 发现了什么 → 下一步」快照

---

## 一、关键数字速览

| 指标 | 值 | 说明 |
|------|----|----|
| **冻结基线 IR（下限参照）** | **0.924** | frozen_baseline_icir_topn50_ew，永不晋升 |
| **本日最优 IR（验证期）** | **1.771** | rolling48_ridge_topn50_ew，PASS 全部三项硬指标，未晋升 |
| 已晋升主线 IR | 0.408 | baseline_expanding_ridge + QP，已注册 mainline.json |
| 测试集剩余 | **2 次** | 权威来源：`docs/logs/test_set_runs.json` |
| 本日新发现的最大问题 | **QP 对所有 Ridge 信号均造成 IR 损耗（-0.17 ~ -0.43）** | TopN50 EW 在全部配置中均优于 QP |

---

## 二、本日完成的实验（按时间顺序）

### 2.1 TopN50 EW 系列实验（核心发现日）

**背景**：此前所有 Ridge 实验均使用 QP 优化器，但发现 IC_IR 与组合 IR 完全负相关。本日建立 TopN50 EW 对照组，直接测量信号质量（无 QP 噪音）。

**运行的 4 个 TopN 实验**：

| run_id（缩写） | 信号 | 训练模式 | IR | 超额收益 | 超额回撤 | 换手 | 全部 PASS？ |
|---|---|---|---:|---:|---:|---:|---|
| frozen_baseline_icir_topn50_ew | IC_IR | expanding | 0.924 | 6.24% | -7.48% | 886% | ✅ |
| **rolling48_ridge_topn50_ew** | Ridge | rolling-48 | **1.771** | **10.2%** | -5.68% | 973% | **✅** |
| decay_hl24_ridge_topn50_ew | Ridge | decay hl=24m | 1.057 | 6.18% | -6.51% | 1005% | ✅ |
| expanding_ridge_topn50_ew | Ridge | expanding | 0.574 | 3.58% | -7.24% | 959% | ✅ |

**关键发现**：
- `rolling48_ridge_topn50_ew` IR=1.771，是迄今全局最优（超过此前 QP 版 1.489）
- 全部 4 个配置均通过三项硬指标（IR≥0.5 / 超额MDD≤10% / 换手500-1500%）
- TopN50 EW 的实际 TE 普遍 **≥** QP 版的实际 TE（IC_IR 信号：6.75% vs 6.10%），说明 QP 的 TE 约束对 TopN 的自然偏离是压缩而非保护

---

### 2.2 QP vs TopN50 IR 损耗量化

通过 `experiment_board` 横向比较，确认 QP 对所有 Ridge 信号的 IR 损耗：

| 信号 | 训练方式 | TopN50 EW IR | QP IR | QP 损耗 |
|------|---------|---:|---:|---:|
| IC_IR | expanding | **0.924** | 0.402 | **-0.522** |
| Ridge | expanding | 0.574 | 0.408 | -0.166 |
| Ridge | rolling-48 | **1.771** | 1.489 | -0.282 |
| Ridge | decay-hl24 | 1.057 | 0.626 | **-0.431** |

**结论**：QP 在所有配置中均造成 IR 损耗，损耗幅度 -0.17 ~ -0.52，IC_IR 信号损耗最大。这是一个系统性问题，不是个别异常。

---

### 2.3 消融实验（因子池诊断）

**目标**：量化 `piotroski_f`（已知 REMOVE_CANDIDATE）和 `high_52w_v2`（WARN 状态）的边际 IR 贡献。

| 实验 | 移除因子 | IR | vs 主基线(0.408) | 结论 |
|------|---------|---:|---:|---|
| ablation_no_piotroski_f | piotroski_f | **0.524** | **+0.116（提升）** | **piotroski_f 为净负贡献，确认 REMOVE_CANDIDATE** |
| ablation_no_high_52w_v2 | high_52w_v2 | 0.423 | +0.015（轻微提升）| high_52w_v2 贡献微弱，可考虑移除 |
| ablation_no_piotroski_high52w | 两者均移除 | 0.430 | +0.022（轻微提升）| 两因子均无正贡献，叠加效果不显著 |

**核心确认**：移除 piotroski_f 后 IR 从 0.408 → 0.524（+28%），明确验证其为有害因子。

---

### 2.4 阶段7新因子评估（全部失败）

| 因子 | IC_IR | 失败原因 | 处置 |
|------|---:|---|---|
| accrual（修复版） | -0.144 | Gate 1 失败（IC_IR < 0.30）+ 与 cfp 高相关（r=-0.741）| 排除 |
| asset_growth | -0.198 | Gate 1 失败（IC_IR < 0.30）+ Gate 3 方向反转 | 排除 |
| share_issuance | -0.113 | Gate 1 + Gate 2 + Gate 3 三重失败 | 排除 |
| mf_flow_ratio | -0.175 | Gate 0（覆盖率 72%）+ Gate 1 + Gate 2 失败；但两期方向一致，逆向信号待研究 | 存档观察 |

**结论**：阶段7 P0+P1 全部候选因子本日评估完毕，均未通过；当前因子池维持 18 个（已注册）。

---

## 三、核心诊断发现

### 3.1 QP vs TopN 损耗根本原因（已确认）

详见 `current work/5.29/plan/qp_vs_topn_diagnosis.md`，确认了 7 个原因，按优先级排序：

**原因 A（最核心）：TE 约束截断 alpha 的自然表达空间**
- TopN50 的"自然 TE" ≈ 6-7%，恰好在 TE=6% 约束线上，QP 被迫持有大量"无观点"底仓来满足约束
- 当 TE 约束处于 binding 状态时，最优解由约束的几何形状决定，而非 alpha 信息的质量
- 验证证据：IC_IR 信号的 TopN TE=6.75% > QP TE=6.10%，约束紧绑定确认

**原因 B：alpha 量级与 MVO 框架不匹配**
- 当前 α 为横截面 z-score（量级 1-2），MVO 期望的是预期超额收益率（量级 0.3-1%）
- z-score 量级比实际预期收益大 10-20 倍，导致 TE 约束成为唯一制约，约束内权重分配由协方差几何决定

**原因 C：行业约束截断 alpha**
- ±3% 行业约束在所有 QP 运行中产生负配置效应（-3.5% ~ -5.2%）
- TopN 无行业约束，自然跟随 alpha 集中的行业，贡献额外 IR

**原因 D：NaN 填 0 稀释 alpha 浓度**
- QP 将 NaN alpha 股票填 0（视为"平均股"），这些股票占用 15-25% 的组合权重但无 alpha 贡献
- TopN 将 NaN 股票排到候选末尾，实际不入选，50 只全部来自有信号的股票

---

### 3.2 IC_IR 与组合 IR 反相关的根本原因（已确认）

详见 `current work/5.29/ic_ir_paradox_root_cause.md`，确认了真正的 IR 差距来源：

**核心发现：rolling-48 优势的 82% 来自底仓（REST-450），不是选股（TOP-50）**

| 超额收益来源 | expanding | rolling-48 |
|---|---:|---:|
| TOP-50 主动超配贡献 | +0.18%/月 | +0.24%/月 |
| REST-450 主动低配贡献 | **+0.03%/月** | **+0.30%/月** |
| **组合总超额** | **+0.21%/月** | **+0.54%/月** |

**底仓差距根本原因**：expanding Ridge 有 8/18 个因子在验证期方向错误（训练学到"正相关"，验证期实际"负相关"），导致这些因子将好股票误划为低 alpha，造成错误低配。rolling-48 只用近 48 个月数据，方向错误因子更少。

**Transfer Coefficient 澄清**：QP 本身传导正确（TC=+0.57），不是 QP 有问题，是信号层方向有误。

---

### 3.3 因子池健康状态更新

| 因子 | 新状态 | 依据 |
|------|--------|------|
| piotroski_f | **REMOVE_CANDIDATE（确认）** | 消融实验：移除后 IR+28%；验证期 IC_IR=-0.200 |
| high_52w_v2 | **WARN（维持）** | 消融实验：移除后 IR+1.5%，贡献微弱但非负贡献 |
| accrual（修复版）| **EXCLUDED（新）** | Gate 1 失败；与 cfp 高相关 r=-0.741 |
| asset_growth | **EXCLUDED（新）** | Gate 1+3 失败；A 股成长溢价方向反转 |
| share_issuance | **EXCLUDED（新）** | Gate 1+2+3 三重失败 |
| mf_flow_ratio | **EXCLUDED（存档观察）** | 多重失败，但逆向信号方向一致 |

---

## 四、本日产出文档清单

| 文档 | 路径 | 内容 |
|------|------|------|
| 因子库全景面板 | `current work/5.29/factor_library_panel.md` | 18入选 + 37未入选 + 2盲区 + 5待实现，完整构造方式 |
| 优化器诊断报告 | `current work/5.29/optimizer_diagnosis.md` | QP vs TopN 对比、已知问题清单、优化器实现精读 |
| IC_IR 悖论根因分析 | `current work/5.29/ic_ir_paradox_root_cause.md` | 确认版根因：底仓差距 82% 来自因子方向错误 |
| QP 损耗深度分析 | `current work/5.29/plan/qp_vs_topn_diagnosis.md` | 7 个原因分析 + D-01~D-07 诊断实验设计 |
| 消融实验计划 | `current work/5.29/plan/ablation_factor_pool_plan.md` | E-01~E-03 实验设计与结果解读框架 |
| 信号质量集成计划 | `current work/5.29/plan/signal_quality_integration_plan.md` | 后续路线图 |
| 信号诊断计划 | `current work/5.29/factor_signal_diagnosis_plan.md` | 因子诊断思路 |

---

## 五、决策结论与遗留问题

### 5.1 已确认的结论

| 结论 | 置信度 | 依据 |
|------|--------|------|
| rolling-48 + TopN50 EW 是当前最优配置（IR=1.771）| 高 | 实验直接证明，PASS 三项硬指标 |
| QP 对所有 Ridge 信号均造成 IR 损耗 | 高 | 4 组配置均确认，损耗 -0.17 ~ -0.52 |
| piotroski_f 在当前验证期为净负贡献 | 高 | 消融实验：移除后 IR +0.116 |
| IC_IR 悖论根因为因子方向错误（非 QP 问题）| 高 | TC 分析 + 底仓贡献分析 |
| 阶段7全部候选新因子失败 | 高 | Gate 评估完成 |

### 5.2 未解决的核心问题

#### 问题 P0-A（最高优先级）：为何 QP 造成系统性损耗？是否应切换为 TopN50？

**关键数据**：

| 情形 | IR |
|---|---:|
| IC_IR + TopN50（当前冻结基线）| 0.924 |
| Ridge rolling-48 + TopN50（当前最优）| 1.771 |
| Ridge rolling-48 + QP（当前挑战者）| 1.489 |
| IC_IR + QP（主线注册版）| 0.408 |

TopN50 EW 全面优于 QP，但 TopN50 存在**无行业约束**的风险敞口（实际 TE 更高，行业集中度可能更高）。  
在切换前，需确认 TopN50 的行业集中度是否超出可接受范围（是否会在单行业大幅回撤时造成毁灭性损失）。

**建议的下一步诊断实验（D-01、D-07）**：
- D-07（极低成本）：强制所有 run 走 L2（无 TE 约束，仅单股偏离），对比 L1 IR
- D-01（低成本）：扫描 TE=8% / 10% / 15%，确认 IR 是否随 TE 放松单调上升

#### 问题 P0-B：rolling-48 的 IR=1.771 是否过拟合？

IC_IR（验证期）=0.348 且 p=0.110（**统计不显著**），但组合 IR=1.771（全局最优）。  
这种"IC 不显著但 IR 极高"的组合值得警惕：可能是 48m 窗口恰好覆盖了"最优"的历史区间（2018-2022）。  
**在正式晋升前，需要做稳健性检验**（如不同因子池、不同窗口长度的对比）。

#### 问题 P1-A：piotroski_f 移除后，是否需要用替代因子补充质量维度？

piotroski_f 已是 REMOVE_CANDIDATE，但质量维度仍需覆盖。  
候选替代：`roe_stability`（已实现，在 excluded 列表）、阶段7新研究方向中的质量因子。

#### 问题 P1-B：high_52w_v2 是否需要移除？

消融实验显示移除后 IR +0.015（微弱提升），但贡献接近零，不是净负贡献。  
保留或移除的边际差异不大，可优先处理 piotroski_f。

---

## 六、下一步行动优先级

> 按照"不触碰测试集，优先在 train_valid 上诊断"的原则排序

### P0（立即可做）

1. **运行 D-07 实验**（纯 L2 强制，无 TE 约束）  
   预期成本：改一行代码 + 10 分钟  
   目的：最快验证"TE 约束"是否是 QP 损耗的主因

2. **分析 rolling-48 的行业集中度**  
   从 `runs/.../portfolio/target_weights.parquet` 计算各期行业权重分布，确认是否有行业超配超过 10%

3. **正式移除 piotroski_f**  
   更新 `final_factors.json`，将 piotroski_f 从 `final_factors` 移入 `excluded`  
   然后以"17 因子 + rolling-48 + TopN50"为新基础运行实验

### P1（P0 之后）

4. **运行 D-01 系列**（TE=8%/10%/15% 扫描）  
   复用已有 IC_IR 信号，仅改优化器参数，每次约 5 分钟

5. **调研 rolling-48 的稳健性**  
   对比 rolling-36 / rolling-48 / rolling-60 的 TopN50 版本 IR（需要 3 个新实验）

6. **研究 asset_growth 因子的 A 股特殊性**  
   从学术文献确认"A 股中资产增长与收益的关系"是否确实与美股相反

### P2（P1 之后，时间充裕时）

7. **alpha 校准实验（Grinold-Kahn）**：IC × vol 将 z-score 映射到收益量级
8. **mf_flow_ratio 逆向信号深挖**：两期方向一致但 IC_IR 为负，逆向因子值得研究
9. **补评 asset_growth 的逆向版本**：若 A 股成长惩罚溢价是真实的，`-asset_growth` 可能有效

---

## 七、注意事项（后续会话）

1. **测试集次数**：全部实验均为 `train_valid` 范围，0 次消耗，剩余 2 次
2. **晋升条件**：rolling-48 + TopN50（IR=1.771）虽然 PASS 三项硬指标，但 **IC_IR 验证期统计不显著（p=0.110）**，晋升前需要完成稳健性检验
3. **piotroski_f**：消融实验已确认 REMOVE_CANDIDATE，但正式移除需先更新 `final_factors.json` 再运行实验，不可直接修改现有已完成的 run
4. **frozen_baseline**：`frozen_baseline_icir_topn50_ew`（IR=0.924）为永久参照，所有比较必须包含它
5. **因子池状态**：当前有效入选因子 18 个（piotroski_f 虽为 REMOVE_CANDIDATE，在正式更新 final_factors.json 前仍在列表中）

---

_本文档基于 2026-05-29 会话的完整实验结果和诊断报告编写，所有数字均有 experiment_board.csv 和各 run 的 self_check.md 支撑。_
