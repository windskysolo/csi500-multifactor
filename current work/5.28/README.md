# 2026-05-28 工作总结

## 一、本日核心工作

### 主线：时变 Ridge 研究与 Decay 实验

5.28 的核心任务是探索在 expanding（全历史等权）和 rolling（硬截断）之间的更优信号估计方案，主要推进了两条线：

1. **时变 Ridge 数学框架梳理**（`plans/time_varying_ridge_frameworks.md`）
   - 系统整理 EW-Ridge、RLS、卡尔曼滤波、贝叶斯序列 Ridge 四种框架
   - 核心结论：四者在线性高斯假设下数学等价，均可用 λ（半衰期）控制遗忘速度
   - 推荐实验路径：先做 EW-Ridge（sklearn 一行），验证效果后再考虑 Bayes/Kalman

2. **Decay Ridge 实验执行与结果**（`discussions/open_questions.md` § 2.3）
   - 实现了 `RidgeDecayCombiner`（`src/signal/ridge_decay.py`）
   - 执行了 hl24 / hl36 / hl48 三个 decay 配置的完整 train_valid run
   - **关键结果**：rolling 硬截断远优于指数衰减

   | 方案 | IR | 对比 expanding(0.408) |
   |------|---:|---:|
   | decay hl=24 | 0.626 | +0.218 ✅ |
   | decay hl=36 | 0.295 | -0.113 ❌ |
   | decay hl=48 | 0.289 | -0.119 ❌ |
   | rolling-36m | 0.966 | +0.558 ✅ |
   | rolling-48m | **1.489** | **+1.081 ✅** |

3. **时变 Ridge 后续实验计划**（`plans/exp_time_varying_ridge_plan.md`）
   - 阶段一：EW-Ridge decay（已完成）
   - 阶段二：Bayesian Sequential Ridge（待执行，入口规范已写好）

---

### 配线：因子健康梳理与评估体系建设

- **因子详解手册**（`discussions/factor_guide.md`）：整理 17+1 个因子的定义、数据来源、IC 表现、状态（stable/weak/warn/reverse），包含策略对比表
- **多份因子实现计划**（`plans/`）：factor_health_system、factor_implementation_plan、factor_optimization_plan 等，规划因子池扩充路径
- **关键事实确认**：`high_52w_v2` 单因子层面通过四道门（IC_IR=0.328），但加入 IC_IR 流水线后 IR 暴跌（IC_IR 给了它过高权重）

---

### 待决策问题登记（`discussions/open_questions.md`）

| 层面 | 问题 | 状态 |
|------|------|------|
| 因子层 | 价格动量三变种全部失效，下步如何 | ❓ 待决策 |
| 因子层 | piotroski_f 方向反转，诊断两假说（风格周期 vs A 股会计失配）| ❓ 待决策 |
| 信号层 | rolling-48m 是否晋升为主基线（IR=1.489，远超目标 0.5）| ❓ 待决策 |
| 信号层 | Bayesian Ridge 是否有增量价值 | 🔬 待实验 |

---

### 可视化输出（`results/`）

生成了全周期策略对比图（strategy_comparison.png）和完整历史回测图（excess_nav_curve、fullperiod_cum_excess、drawdown 等），用于支撑决策讨论。

---

## 二、文件索引

| 目录 | 文件 | 用途 |
|------|------|------|
| `plans/` | `exp_time_varying_ridge_plan.md` | Decay/Bayes Ridge 实验全规范（阶段 1+2）|
| `plans/` | `time_varying_ridge_frameworks.md` | 四种时变 Ridge 数学框架推导 |
| `plans/` | `frozen_baseline_plan.md` | 冻结基线建立计划 |
| `plans/` | `factor_health_*.md` / `factor_*.md` | 因子体系建设与优化方向规划 |
| `discussions/` | `factor_guide.md` | 17+1 因子详解与策略表现参考手册 |
| `discussions/` | `open_questions.md` | 全面待决策/待实验问题清单 |
| `results/` | `strategy_comparison.png` 等 | 策略对比与回测可视化 |
| `results/` | `gen_fullperiod_charts.py` / `plot_strategy_comparison.py` | 图表生成脚本 |

---

## 三、后续衔接（5.29 接手的问题）

- piotroski_f 状态诊断
- rolling-48 TopN50 EW 与 QP 的系统对比（发现 QP 全面损耗 IR）
- QP 约束诊断实验（TE / 行业约束是否为瓶颈）
- 消融实验：量化剔除 piotroski_f 的 IR 影响
