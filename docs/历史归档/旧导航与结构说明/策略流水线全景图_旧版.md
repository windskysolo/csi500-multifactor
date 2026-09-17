# 策略流水线全景图

> 用途：告诉 AI 助手"我要用哪条路"，避免流水线混淆。
> 最后更新：2026-05-26

---

## 快速定位

| 我想做的事 | 用哪条流水线 | 一句话入口 |
|-----------|-------------|-----------|
| 完整跑训练+验证，IC_IR信号 | **Pipeline A** | `python -m scripts.run_pipeline` |
| 重现 v1.1 IR=0.483 最优结果 | **Pipeline B** | 先跑 B-1，再跑 B-2 |
| 跑测试集（≤3次，慎用） | **Pipeline C** | `python -m scripts.run_test_pipeline` |
| 评估新因子（单因子检验） | **Pipeline D** | `python -m scripts.run_factor_evaluation` |
| 修改因子池后重跑全流程 | 先跑 D → 更新 `final_factors.json` → 再跑 A 或 B |
| Rolling 窗口 vs Expanding 对比 | **Pipeline E** | `python -m experiments.ridge_rolling.run_rolling_experiment` |

---

## Pipeline A：IC_IR 主流水线

**当前验证期 IR ≈ 0.341（16因子）**  
**特点**：从头到尾一键跑通，信号用 IC_IR 加权（不是 Ridge）

```
scripts/run_pipeline.py  ← 一键入口（可 --from-stage 断点续跑）
│
├─► [Stage 1] 因子面板构建
│   脚本: scripts/build_factor_panels.py
│   读取: src/factors/price_factors.py
│         src/factors/financial_factors.py
│         src/factors/alt_factors.py
│         src/factors/preprocess.py
│         src/data/pit_loader.py
│         src/data/loader.py
│         data/processed/（行情/财务 Parquet）
│   输出: data/processed/factor_panels/{factor_name}.parquet  ← 每因子一个文件
│
├─► [Stage 2] 信号合成（IC_IR 加权）
│   脚本: scripts/run_signal_combination.py
│   读取: reports/factor_evaluation/final_factors.json  ← 因子列表 + stability_weights
│         data/processed/factor_panels/{name}.parquet
│         data/processed/fwd_ret_panel.parquet
│   核心: src/signal/combiner.py（method="ic_ir"，rolling 24个月）
│   输出: data/processed/composite_signal_ic_ir.parquet  ← 下游优化器读这个
│         data/processed/composite_signal_equal.parquet
│         data/processed/composite_signal_weight_history.parquet
│         data/processed/composite_signal_metadata.json
│
├─► [Stage 3] 组合优化
│   脚本: scripts/run_portfolio_optimization.py
│   读取: data/processed/composite_signal_ic_ir.parquet  ← A流水线信号
│         data/processed/index_member.parquet
│         data/processed/industry.parquet
│         data/processed/cov_cache/{YYYYMMDD}.parquet    ← 共享协方差缓存（264个）
│         src/config.py（TE=6%, λ=0.005, 单股偏离1.5%, 行业偏离3%）
│   核心: src/portfolio/covariance.py（LedoitWolf）
│         src/portfolio/optimizer.py（L1→L2→L3 三级 fallback）
│   输出: data/processed/portfolio_weights_optimized.parquet
│         data/processed/portfolio_weights_baseline.parquet
│         data/processed/portfolio_weights_meta.parquet
│
├─► [Stage 4] 回测
│   脚本: scripts/run_backtest.py
│   读取: data/processed/portfolio_weights_optimized.parquet
│         data/processed/（行情、基准）
│   核心: src/backtest/engine.py（T+1开盘，成本=佣金2.5bps+滑点5bps+印花税）
│         src/backtest/transaction.py
│         src/backtest/metrics.py
│   输出: data/processed/backtest_nav.parquet
│         data/processed/backtest_metrics.parquet
│
└─► [Stage 5] 归因
    脚本: scripts/run_attribution.py
          scripts/generate_backtest_report.py
    读取: data/processed/backtest_nav.parquet
          data/processed/portfolio_weights_optimized.parquet
    核心: src/attribution/brinson.py
          src/attribution/factor_attr.py
    输出: reports/analysis_v2_results.md  ← 最终验证期报告
```

**配置文件**：`src/config.py`  
**关键参数**（所有实验共享）：

| 参数 | 值 | 含义 |
|------|-----|------|
| `TRAIN_START/END` | 2012-01 / 2020-12 | 训练期 108期 |
| `VALID_START/END` | 2021-01 / 2022-12 | 验证期 24期 |
| `TEST_START/END` | 2023-01 / 2025-12 | 测试期（≤3次）|
| `OPT_TE_TARGET_ANNUAL` | 0.06 | 年化跟踪误差目标 |
| `OPT_TURNOVER_LAMBDA` | 0.005 | 换手惩罚系数（v1.1最优）|
| `OPT_SINGLE_MAX_DEV` | 0.015 | 单股最大偏离（绝对值）|
| `OPT_INDUSTRY_MAX_DEV` | 0.03 | 行业最大偏离（绝对值）|
| `SIGNAL_WINDOW_MONTHS` | 24 | IC_IR 滚动窗口 |

---

## Pipeline B：Ridge v2 实验流水线（v1.1 最优）

**验证期 IR = 0.483（16因子，λ=0.005）**  
**特点**：信号用 Walk-forward Ridge（超额收益目标，α=5000），比 IC_IR 高约 0.14 IR  
**注意**：两步骤必须按顺序执行；B-1 结果是 B-2 的输入

```
┌─────────────────────────────────────────────────────┐
│ Step B-1：构建 Ridge 合成信号                        │
│                                                     │
│ 脚本: experiments/ridge_signal/run_ridge_experiment.py │
│ 命令: python -m experiments.ridge_signal.run_ridge_experiment │
│                                                     │
│ 读取:                                               │
│   reports/factor_evaluation/final_factors.json      │
│   data/processed/factor_panels/{name}.parquet       │
│   data/processed/fwd_ret_panel.parquet              │
│   data/processed/index_member.parquet               │
│                                                     │
│ 核心逻辑:                                           │
│   Walk-forward Pooled Ridge（超额收益目标）          │
│   CV折叠=5，α候选=[0.1,1,10,100,500,2000,5000]     │
│   冷启动期24个月 → 首个信号日期2014-03-31           │
│   选定α=5000                                       │
│                                                     │
│ 输出（隔离目录）:                                   │
│   experiments/ridge_signal/results/                 │
│     ridge_composite_panel.parquet  ← B-2 读这个    │
│     ridge_coef_history.parquet                     │
│     comparison_report.md                           │
│     cv_alpha_selection.csv                         │
└─────────────────────────────────────────────────────┘
                        │
                        ▼ (B-1 完成后)
┌─────────────────────────────────────────────────────┐
│ Step B-2：优化+回测（λ=0.005 为最优）               │
│                                                     │
│ 脚本: experiments/turnover_lambda_grid/             │
│       run_turnover_lambda_grid.py                   │
│ 命令: python -m experiments.turnover_lambda_grid.   │
│       run_turnover_lambda_grid --lam 0.005          │
│                                                     │
│ 读取:                                               │
│   experiments/ridge_signal/results/                 │
│     ridge_composite_panel.parquet  ← 来自 B-1      │
│   data/processed/cov_cache/*.parquet  ← 共享       │
│   data/processed/index_member.parquet               │
│   data/processed/industry.parquet                  │
│   src/config.py（TE/约束参数）                      │
│                                                     │
│ 输出（隔离目录，按λ分开）:                          │
│   experiments/turnover_lambda_grid/results/lam_0050/│
│     weights_optimized.parquet                      │
│     backtest_nav_train.parquet                     │
│     backtest_nav_valid.parquet                     │
│     backtest_metrics_valid.parquet  ← IR=0.483在这 │
│     brinson_period_summary.parquet                 │
└─────────────────────────────────────────────────────┘
```

**当前 B-1 信号状态**（截至 2026-05-26）：
- `ridge_composite_panel.parquet` 时间戳：2026-05-25 00:09，**基于 16 因子，完好**
- 如果修改了 `final_factors.json`（新增/删除因子），必须重新跑 B-1，再跑 B-2

---

## Pipeline E：Rolling-Window Ridge 对比实验

**目的**：验证滚动窗口（36m/48m/60m）能否突破 Pipeline B 的 IR=0.483 上限
**状态**：待运行
**命令**：`python -m experiments.ridge_rolling.run_rolling_experiment`

```
experiments/ridge_rolling/
  rolling_combiner.py          ← RidgeRollingCombiner（继承 B，加 window_months 参数）
  run_rolling_experiment.py    ← 实验入口

产物（隔离目录）:
  experiments/ridge_rolling/results/
    rolling_{36/48/60}m_composite_panel.parquet
    rolling_{36/48/60}m_coef_history.parquet
    rolling_{36/48/60}m_cv_results.csv
    rolling_{36/48/60}m/
      weights_optimized.parquet
      backtest_metrics_valid.parquet
      backtest_nav_valid.parquet
    ic_stats_summary.csv        ← 所有变体 IC 对比
    comparison_report.md        ← 最终 IR 对比报告
```

**设计要点**：
- expanding 基准直接复用 `experiments/turnover_lambda_grid/results/lam_0050/`，不重新跑优化器
- rolling 变体 CV 独立选 α（候选范围与 B 相同）
- 优化器固定 λ=0.005（Pipeline B 最优），仅对比窗口策略的影响
- 不修改任何已有文件

---

## Pipeline C：测试集专用流水线

**⚠️ 剩余次数：3次（已用0次）。每次跑之前必须登记**

```
scripts/run_test_pipeline.py  ← 唯一入口，内置计数保护

依赖（与 Pipeline A 相同，但时间范围切换到 TEST_START~TEST_END）:
  data/processed/composite_signal_ic_ir.parquet
  data/processed/cov_cache/*.parquet
  data/processed/portfolio_weights_optimized.parquet（若需要初始持仓）

输出:
  data/processed/run_manifests/{run_id}_manifest.json
  （其他输出与 Pipeline A 相同）

计数权威来源: docs/logs/test_set_runs.json
git commit 规则: message 必须含 [TEST_SET_RUN_N]
```

---

## Pipeline D：单因子评估流水线

**用途**：评估新候选因子是否通过四道 Gate（不影响主线）

```
scripts/run_factor_evaluation.py
│
├─► 读取: data/processed/factor_panels/{name}.parquet（待评估因子）
│         data/processed/factor_panels/{已有因子}.parquet（用于相关性检验）
│         data/processed/fwd_ret_panel.parquet
│
├─► 计算:
│   Gate 0: 覆盖率 ≥ 80%
│   Gate 1: IC_IR ≥ 0.3，t ≥ 2（仅训练期）
│   Gate 2: lead_ratio < 1.5x（未来函数检测）
│   Gate 3: 训练期分段稳定性（跨2012-14/15-17/18-20三段）
│
└─► 输出（隔离目录，避免污染主线）:
    reports/factor_evaluation_{实验名}/
      ic_result.csv
      shift_result.csv
      seg_ic_ir.csv
      factor_summary.csv
      final_factors.json  ← 建议手动比对后再覆盖主线版本
      factor_correlation.csv
      valid_comparison.csv

主线因子池（当前16个）:
  reports/factor_evaluation/final_factors.json  ← 唯一权威来源
```

---

## 共享资源（所有流水线复用）

```
data/processed/
├── factor_panels/          ← 因子面板（每个因子一个 parquet）
│   ├── turn_20d.parquet
│   ├── ivol_60d.parquet
│   └── ...（共 54 个，其中 16 个在主线因子池）
├── cov_cache/              ← LedoitWolf 协方差矩阵缓存（264个，按调仓日）
│   └── {YYYYMMDD}.parquet
├── index_member.parquet    ← 中证500成分股（月末快照）
├── industry.parquet        ← 申万一级行业（31个）
├── fwd_ret_panel.parquet   ← 月频远期收益（T+1至T+2的超额收益）
└── adj_factor.parquet      ← 后复权因子

reports/factor_evaluation/
├── final_factors.json      ← 因子池权威来源（当前16个因子+stability_weights）
└── factor_summary.csv      ← 因子评估汇总（final_include 字段控制是否入池）
```

---

## 两条主流水线的核心差异

| 维度 | Pipeline A（IC_IR）| Pipeline B（Ridge v2）|
|------|-------------------|----------------------|
| **信号方法** | IC_IR 加权，rolling 24m | Walk-forward Ridge，α=5000 |
| **信号文件** | `composite_signal_ic_ir.parquet` | `ridge_composite_panel.parquet` |
| **冷启动** | 12个月 | 24个月（首个信号2014-03）|
| **验证期 IR** | ~0.341 | **0.483（v1.1最优）** |
| **一键入口** | `run_pipeline.py` | 需两步：`run_ridge_experiment` → `run_turnover_lambda_grid` |
| **隔离性** | 产物在 `data/processed/` | 产物在 `experiments/` 子目录 |
| **修改因子池后** | 只需重跑 A | **必须先重跑 B-1，再跑 B-2** |
| **适用场景** | 快速迭代/日常调试 | 正式对比/版本归档 |

**重要规则**：
- 修改 `final_factors.json` 后，**A 和 B 用的是同一个文件，但 B 还额外需要重跑 Ridge 信号**
- `data/processed/cov_cache/` 被 A 和 B 共享，不要轻易删除
- `reports/factor_evaluation/factor_summary.csv` 里的 `final_include` 字段必须与 `final_factors.json` 保持一致，否则 Pipeline A 的 `run_signal_combination.py` 会报错

---

## 版本快照

| 版本 | IR | 信号 | 因子数 | 存档位置 |
|------|---:|------|-------:|---------|
| V0 基线 | -0.11 | IC_IR（6因子）| 6 | `experiments/v1.0/` |
| 数据扩展+修Bug | ~0.35 | IC_IR（16因子）| 16 | — |
| Ridge v2 引入 | ~0.42 | Ridge（16因子）| 16 | `experiments/ridge_signal/` |
| **v1.1（当前最优）** | **0.483** | Ridge+λ=0.005 | 16 | `experiments/v1.1/` |

---

*本文件记录截至 2026-05-26 的项目状态。修改流水线后请同步更新此文件。*
