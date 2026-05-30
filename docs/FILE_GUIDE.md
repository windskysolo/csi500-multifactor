# 项目进展快照

> 用途：快速了解项目当前位置、指标状态、已知问题。
> 详细文件功能索引见 `FILE_MAP.md`；完整决策依据见 `PROJECT_PLAN_v1.1.md`。
> 最后更新：2026-05-29

---

## Part 1 — 当前状态仪表盘

| 字段 | 当前值 |
|------|--------|
| 因子池规模 | 17 个（含 rev_acceleration）|
| **冻结基线 IR** | **0.924**（frozen_baseline_icir_topn50_ew，ICIR+TopN50 EW，永不晋升，不可与 Ridge+QP 实验直接比较）|
| 验证期最优 IR | **1.489**（challenger_rolling48，未晋升）|
| 已晋升主线 IR | 0.408（baseline_expanding_ridge，Ridge+QP，已在 mainline.json 注册）|
| 测试集已用 / 剩余 | 0 次 / **2 次剩余**（权威来源：`docs/logs/test_set_runs.json`）|
| 当前阶段 | 持续改进期（搭建完成，聚焦提升 IR）|

> 若此表与 `CLAUDE.md` §1.1 不一致，**以 CLAUDE.md §1.1 为准**（两者应同步维护）。

---

## Part 2 — 实验结果汇总

| run_id（简称）| 角色 | IR | 超额收益 | 最大回撤 | 换手 |
|-------------|------|----|---------|---------|------|
| frozen_baseline_icir_topn50_ew | **冻结基线**（ICIR+TopN50 EW，不变对照锚）| 0.924 | +6.24% | -7.48% | 886% |
| baseline_expanding_ridge | **已晋升主线**（Ridge+QP）— 方法与冻结基线不同，IR 不可直接比较 | 0.408 | +2.44% | -8.16% | ~1000% |
| challenger_rolling36 | 挑战者 | 0.966 | +5.80% | -8.38% | — |
| challenger_rolling48 | 挑战者 | **1.489** | +8.63% | -5.82% | — |
| challenger_rolling60 | 挑战者 | 1.176 | +6.87% | -6.73% | — |
| challenger_decay_ridge_hl24 | 挑战者 | 0.626 | +3.65% | -8.32% | — |
| challenger_decay_ridge_hl36 | 挑战者 | 0.295 | +1.71% | -8.79% | — |
| challenger_decay_ridge_hl48 | 挑战者 | 0.289 | +1.68% | -8.28% | — |

> 完整横向比较（含 IR、回撤、换手等所有指标）：`reports/experiment_board.md`

---

## Part 3 — 完成标准达成情况

| 指标 | 目标 | 当前最优（验证期）| 状态 |
|------|------|----------------|------|
| 信息比率 IR | ≥ 0.5 | 1.489（rolling48）| ✅ |
| 超额最大回撤 | ≤ 10% | -5.82%（rolling48）| ✅ |
| 年化双边换手 | 5-15 倍 | ~9x（rolling48）| ✅ |

> rolling48 仅在验证期达标，**尚未做测试集评估**（测试集剩余 2 次，请谨慎使用）。

---

## Part 4 — 已知问题与待跟进

| 问题 | 级别 | 状态 |
|------|------|------|
| piotroski_f 因子方向疑似反转 | 中 | 待诊断 |
| decay_ridge hl36/hl48 验证期 IR 低于主基线 | 中 | 待诊断（假说：结构性因子变迁，远期信息有害）|
| QP 优化器相比冻结基线存在 IR 衰减（0.408 vs 0.924）| 高 | 待诊断（约束截断 + 换手惩罚联合作用？）|

---

## Part 5 — 关键里程碑路径

| 里程碑 | 完成日期 | 参考文档 |
|--------|---------|---------|
| 优化器 Bug 修复（L2 约束冲突、L3 违约）| 2026-05-24 | `progress_log.md` §1.3 |
| 因子池扩展至 17 个（Phase 4-6）| 2026-05-24~26 | `progress_log.md` §3-6 |
| Ridge v2 + λ=0.005（IR=0.483）| 2026-05-25 | `progress_log.md` §6.12 |
| EW-Ridge 实验（hl24 IR=0.626，hl36/48 低于基线）| 2026-05-28 | `progress_log.md` 阶段 N |
| 滚动窗口挑战者（rolling48 IR=1.489，当前最优）| 2026-05-28 | `registry/challengers.json` |
| 冻结基线建立（IR=0.924，永不变更）| 2026-05-29 | `progress_log.md` 阶段 N+1 |

---

## Part 6 — 快速导航

| 我想… | 去哪查 |
|-------|--------|
| 了解项目完整设计决策 | `docs/PROJECT_PLAN_v1.1.md` |
| 找某个文件在哪、某功能在哪个模块 | `docs/FILE_MAP.md` |
| 运行一个新实验（Spec→Run→Compare→Promote）| `docs/RESEARCH_GUIDE.md` |
| 了解因子健康状态和新因子优先级 | `docs/research/factor_roadmap/factor_research_guide.md` |
| 回顾历史实验的诊断和结论 | `docs/research/improve/progress_log.md` |
| 确认测试集剩余次数（权威来源）| `docs/logs/test_set_runs.json` |
| 查某个 run 的详细指标 | `runs/train_valid/<run_id>/reports/self_check.md` |
