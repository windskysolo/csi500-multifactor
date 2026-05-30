# 2026-05-29 工作总结

## 一、本日核心发现（结论速报）

| 问题 | 结论 |
|------|------|
| QP 优化器是否有 bug？ | **否**，TC=+0.57，IC-超额相关 ρ=+0.90，运作正常 |
| IC_IR 高但组合 IR 低的根因 | **信号含53%噪声**，QP 按量级分配权重（基数），等于把噪声最大化进了权重；TopN50 只用排名（序数），对噪声免疫 |
| rolling-48 为何远优于 expanding | 底仓贡献差距 10 倍（+0.30% vs +0.03%/月），不是选股层差异 |
| QP 约束是阻碍还是保护？ | **保护机制**：松 TE 或行业约束后 IR 均变差，约束在抑制噪声押注 |
| piotroski_f 状态 | **REMOVE_CANDIDATE**：消融实验确认移除后 IR +0.116（0.408→0.524）|
| 本日全局最优 | rolling-48 Ridge + TopN50 EW，**IR=1.771**，三项硬指标全 PASS |

---

## 二、实验矩阵（信号 × 优化器，验证期 2021-2022）

| 信号 | 训练方式 | 优化器 | IR | 超额 | MDD | 换手 | PASS |
|------|---------|-------|---:|----:|----:|----:|------|
| IC_IR | expanding | **TopN50 EW** | **0.924** | +6.24% | -7.48% | 886% | ✅ 冻结基线 |
| Ridge | rolling-48 | **TopN50 EW** | **1.771** | +10.2% | -5.68% | 973% | ✅ 本日最优 |
| Ridge | decay hl=24 | TopN50 EW | 1.057 | +6.18% | -6.51% | 1005% | ✅ |
| Ridge | expanding | TopN50 EW | 0.574 | +3.58% | -7.24% | 959% | ✅ |
| Ridge | rolling-48 | QP TE=6% | 1.489 | +8.63% | -5.82% | 1002% | ✅ |
| Ridge | rolling-36 | QP TE=6% | 0.966 | +5.80% | -8.38% | 1002% | ✅ |
| Ridge | decay hl=24 | QP TE=6% | 0.626 | +3.65% | -8.32% | 1041% | ✅ |
| Ridge | expanding | QP TE=6% | 0.408 | +2.44% | -8.16% | 974% | ❌ |
| IC_IR | expanding | QP TE=6% | 0.402 | +2.45% | -7.75% | 918% | ❌ |

**QP 损耗汇总**：TopN50 EW 在所有信号类型上均优于 QP，损耗 -0.166 ~ -0.522。

---

## 三、三条主要工作线

### 线 1：TopN50 EW 系统对照实验（`results/experiment_board.md`）

建立 IC_IR / Ridge-expanding / Ridge-rolling48 / Ridge-decay24 四个 TopN50 EW 对照组，与对应 QP 版本横向比较，首次量化 QP 损耗全貌。

### 线 2：消融实验（`plans/ablation_factor_pool_plan.md`）

| 实验 | 移除 | IR | 变化 | 结论 |
|------|------|---:|----:|------|
| ablation_no_piotroski_f | piotroski_f | 0.524 | +0.116 | **确认 REMOVE** |
| ablation_no_high_52w_v2 | high_52w_v2 | 0.423 | +0.015 | 贡献微弱，可移除 |
| ablation_no_piotroski_high52w | 两者均移除 | 0.430 | +0.022 | 叠加效果不显著 |

### 线 3：QP 约束诊断实验（`results/qp_constraint_diagnosis_conclusion.md`）

针对 IC_IR 信号，固定信号（sha256=23024863），仅改变优化层，测试 5 个约束变体：

| 配置 | IR | 结论 |
|------|---:|------|
| QP TE=6% + 行业±3%（原始）| 0.402 | QP 最优点 |
| L2（去掉 TE 二次约束）| 0.356 | 变差 → TE 约束有正则化作用 |
| QP TE=10%/15% | 0.356 | 与 L2 相同 → TE 约束非绑定时无效果 |
| QP + 行业±5% | 0.146 | 大幅变差 → 行业约束在限制历史偏见 |
| QP + 无行业约束 | 0.240 | 变差 → 约束是保护机制 |

根因确认：**约束在保护 QP，真正瓶颈是序数 vs 基数问题，不可通过调参解决**。

---

## 四、后续行动优先级

```
P0（立即执行）：
  1. 从 final_factors.json 移除 piotroski_f
  2. 在 rolling-48 TopN50 框架下验证无 piotroski_f 的 IR 变化
  3. 检查 rolling-48 TopN50 训练期 IC_IR 稳定性（防验证期过拟合）

P1（中期执行）：
  4. alpha 校准：IC × vol × z-score，测试 QP 损耗是否缩小
  5. 若步骤 2 结果良好（IR > 1.8），评估是否用测试集最终验证

P2（暂时搁置）：
  6. Black-Litterman 框架（TopN50 已经够好，优先级低）
```

---

## 五、文件索引

| 目录 | 文件 | 用途 |
|------|------|------|
| `plans/` | `ablation_factor_pool_plan.md` | piotroski_f / high_52w_v2 消融实验方案 |
| `plans/` | `qp_vs_topn_diagnosis.md` | QP 损耗 7 大原因分析 + 诊断实验方案 |
| `plans/` | `signal_quality_integration_plan.md` | 信号质量集成改进计划 |
| `plans/` | `factor_signal_diagnosis_plan.md` | 因子信号层诊断计划 |
| `discussions/` | `optimizer_diagnosis.md` | QP vs TopN50 全量对比与诊断假设（初版）|
| `discussions/` | `ic_ir_paradox_root_cause.md` | IC_IR 与组合 IR 反相关的根本原因归因 |
| `discussions/` | `analysis_summary_0529.md` | 诊断总结（结论速报 + 全量 run 对比）|
| `discussions/` | `session_results_analysis.md` | 本日完整实验流水记录 |
| `discussions/` | `factor_library_panel.md` | 因子库总面板（含各因子状态）|
| `results/` | `5.29_research_summary.md` | **本日主入口**：全量结论 + 后续方向 |
| `results/` | `qp_constraint_diagnosis_conclusion.md` | QP 约束诊断最终结论（实验验证版）|
| `results/` | `experiment_board.md` / `.csv` | 本日 7 个诊断 run 横向比较板 |
