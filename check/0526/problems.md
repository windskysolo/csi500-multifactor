# 2026-05-26 研究问题说明

> 本文件用于理解问题背景、定位相关文件，便于后续规划和排查。

---

## 背景数据速览

| 流水线 | 信号方法 | 验证期 IC_IR | 验证期 IR | 月度胜率 | 超额收益 |
|--------|---------|:-----------:|:--------:|:-------:|:-------:|
| Pipeline A | IC_IR 加权 | — | 0.341 | — | — |
| Pipeline B | Ridge expanding | 0.715 | 0.483 | **39.1%** | +2.77% |
| Pipeline E rolling-36m | Ridge rolling | 0.247 | 0.443 | 60.9% | +2.54% |
| Pipeline E rolling-48m | Ridge rolling | 0.441 | **1.092** | 60.9% | +6.16% |
| Pipeline E rolling-60m | Ridge rolling | 0.511 | 0.619 | 60.9% | +3.51% |

验证期：2021-01-01 ~ 2022-12-31（23 个月）

---

## 问题一：Pipeline B（Ridge expanding）的月度胜率 39.1% 是真实的吗？

### 现象

Pipeline B 验证期 IR=0.483，年化超额=+2.77%，但月度胜率只有 **39.1%**（23 个月里只有 9 个月跑赢基准）。

同时 Pipeline B 比 Pipeline A（IC_IR 加权，IR=0.341）表现更好。

### 两个子问题

**1a — 内部一致性**：IR=0.483 与月度胜率=39.1% 能同时成立吗？

数学上可以：9 个月大幅领先 + 14 个月小幅落后，净超额仍为正。但这对于指数增强策略来说非常异常（正常应在 55-65% 左右），需要验证是真实的收益分布还是计算 bug。

**1b — Pipeline B vs A 的机制**：Ridge 的真实改善体现在哪里？两套流水线在数据口径、回测参数、基准设定上是否完全一致？

### 排查假设清单

| 假设 | 检查位置 |
|------|---------|
| `monthly_win_rate` 计算逻辑有误 | `src/backtest/metrics.py` |
| 两套流水线基准不一致（全收益 vs 价格指数）| 两者 `BacktestConfig` 对比 |
| 信号覆盖股票数不同（IC_IR vs Ridge） | 两个信号面板的 NaN 率对比 |
| 回测引擎调用参数有差异 | `scripts/run_backtest.py` vs `run_turnover_lambda_grid.py` |
| expanding Ridge 在 2021-2022 系统性偏向错误方向 | 月度超额收益时序图 |

### 相关文件路径

```
# 信号
data/processed/composite_signal_ic_ir.parquet
experiments/ridge_signal/results/ridge_composite_panel.parquet
experiments/ridge_signal/results/ridge_coef_history.parquet

# Pipeline A 回测产物
data/processed/backtest_nav.parquet
data/processed/backtest_metrics.parquet

# Pipeline B 回测产物
experiments/turnover_lambda_grid/results/lam_0050/backtest_nav_valid.parquet
experiments/turnover_lambda_grid/results/lam_0050/backtest_nav_train.parquet
experiments/turnover_lambda_grid/results/lam_0050/backtest_metrics_valid.parquet
experiments/turnover_lambda_grid/results/lam_0050/trades_v2.parquet
experiments/turnover_lambda_grid/results/lam_0050/actual_weights_v2.parquet

# 核心逻辑
src/signal/combiner.py                   ← IC_IR 合成
experiments/ridge_signal/ridge_combiner.py   ← Ridge expanding 合成
src/backtest/engine.py                   ← 回测引擎（两条流水线共用）
src/backtest/metrics.py                  ← monthly_win_rate 定义
src/backtest/transaction.py              ← T+1 成交、成本计算
scripts/run_backtest.py                  ← Pipeline A 回测入口
experiments/turnover_lambda_grid/run_turnover_lambda_grid.py  ← Pipeline B 回测入口
```

---

## 问题二：rolling-48m 信号质量更差但 IR 翻倍，真实吗？

### 现象

| 对比维度 | expanding | rolling-48m |
|---------|:---------:|:-----------:|
| 验证期 IC_IR（信号质量）| **0.715** | 0.441 |
| 验证期 IR（组合绩效）| 0.483 | **1.092** |
| 验证期月度胜率 | 39.1% | **60.9%** |
| 验证期年化超额 | +2.77% | **+6.16%** |
| 验证期 IC p 值 | 0.002 | 0.046 |

信号统计显著性更弱，但组合绩效翻倍，IC 与 IR 方向完全相反。

### 两类排查方向

**类型 A — 信号构建阶段的 bug（rolling 是否引入了未来信息）**

| 检查点 | 代码位置 | 当前状态 |
|--------|---------|---------|
| `_rolling_train_dates` 边界 | `rolling_combiner.py` | `all_dates > window_start`（严格大于，看起来正确）|
| purge 2 个月是否生效 | `rolling_combiner.py` `_fit_and_predict` | `cutoff = T - DateOffset(months=2)` |
| CV fold 的 train/val 是否严格不重叠 | `rolling_combiner.py` `_process_cv_fold` | cutoff 在 val_start 前 2 月，需确认 |
| `fwd_ret_panel` 时间对齐（T 日信号对应 T+1 收益）| `data/processed/fwd_ret_panel.parquet` | 需验证 index 含义 |
| 因子面板的 PIT 对齐（不含未来财务数据）| `data/processed/factor_panels/` + `src/data/pit_loader.py` | 两个实验共用同一份因子，若有问题两者都受影响 |

**类型 B — 回测阶段的不对称性**

| 检查点 | 说明 |
|--------|------|
| expanding 的指标从旧文件加载，rolling 是重新跑的 | 两个 `run_backtest` 调用方式是否完全等价？ |
| 优化器冷启动 `w_prev=None` 的处理 | 两者训练期第一期的初始权重是否相同？ |
| `BacktestConfig` 参数 | 新脚本 vs 旧脚本是否一致？ |

**类型 C — 统计噪声（样本量问题）**

- 验证期只有 23 个月，IR 标准误 ≈ `1/sqrt(23) ≈ 0.21`
- rolling-48m IR=1.092 的 95% CI ≈ [0.66, 1.52]
- expanding IR=0.483 的 95% CI ≈ [0.07, 0.90]
- 两者置信区间有重叠，差值 +0.609 在统计上未必显著
- rolling-48m 的 IC p 值仅 0.046，信号本身边际显著

### 相关文件路径

```
# Rolling-48m 产物
experiments/ridge_rolling/results/rolling_48m_composite_panel.parquet
experiments/ridge_rolling/results/rolling_48m_coef_history.parquet
experiments/ridge_rolling/results/rolling_48m_cv_results.csv
experiments/ridge_rolling/results/rolling_48m/backtest_nav_valid.parquet
experiments/ridge_rolling/results/rolling_48m/backtest_metrics_valid.parquet
experiments/ridge_rolling/results/ic_stats_summary.csv

# Expanding 对比产物
experiments/ridge_signal/results/ridge_composite_panel.parquet
experiments/ridge_signal/results/ridge_coef_history.parquet
experiments/turnover_lambda_grid/results/lam_0050/backtest_nav_valid.parquet
experiments/turnover_lambda_grid/results/lam_0050/backtest_metrics_valid.parquet

# 核心代码
experiments/ridge_rolling/rolling_combiner.py        ← 重点检查 bug
experiments/ridge_rolling/run_rolling_experiment.py  ← 回测调用方式
experiments/turnover_lambda_grid/run_turnover_lambda_grid.py  ← 对比
src/backtest/engine.py
src/data/pit_loader.py                               ← PIT 对齐逻辑
data/processed/fwd_ret_panel.parquet                 ← 前向收益（检查时间对齐）
```

---

## 排查的优先起点（新对话建议先做）

**步骤 1 — 5 分钟内排除最大疑虑：看月度超额收益时序**

```python
import pandas as pd

nav_b = pd.read_parquet("experiments/turnover_lambda_grid/results/lam_0050/backtest_nav_valid.parquet")
nav_r = pd.read_parquet("experiments/ridge_rolling/results/rolling_48m/backtest_nav_valid.parquet")

# 月度超额
excess_b = nav_b["strategy"].pct_change() - nav_b["benchmark"].pct_change()
excess_r = nav_r["strategy"].pct_change() - nav_r["benchmark"].pct_change()

print("Pipeline B 月度超额（验证期）:")
print(excess_b.dropna().describe())
print(f"正月数: {(excess_b.dropna() > 0).sum()} / {excess_b.dropna().count()}")

print("\nRolling-48m 月度超额（验证期）:")
print(excess_r.dropna().describe())
print(f"正月数: {(excess_r.dropna() > 0).sum()} / {excess_r.dropna().count()}")
```

- 若 expanding 真的 9/23 月为正且分布合理 → 胜率真实，问题是 regime
- 若数字对不上 → metrics 计算 bug

**步骤 2 — 验证两次 run_backtest 的等价性**

把 expanding 信号面板传入 `run_rolling_experiment.py` 的同一套回测逻辑重跑一次，对比结果与 `lam_0050` 是否一致。若一致，两套回测等价；若不同，说明回测逻辑有差异。
