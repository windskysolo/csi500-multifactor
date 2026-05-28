# v1.1 — 当前验证期最优配置归档

> 归档日期：2026-05-25  
> Git Commit：`7423f1e`（`feature/expand-train-2012`，归档时工作区仍有未提交改动）  
> 样本：训练 2012-01-01 ~ 2020-12-31；验证 2021-01-01 ~ 2022-12-31；测试 2023-01-01 ~ 2025-12-31 未重新运行  
> 测试集纪律：按 `docs/check/test_set_runs.json` 和 `docs/check/test_set_run_log.md` 记录，已消耗 0 次，剩余 2 次

## 本版定义

v1.1 不是一次新的测试集结果，而是把当前验证集内最优研究配置冻结下来，作为后续是否进入最后一次测试集评估的候选版本。

当前最优组合：

| 层级 | 配置 |
|---|---|
| 因子池 | 16 个最终入池因子，来源 `reports/factor_evaluation/final_factors.json` |
| 信号合成 | Ridge v2，训练目标为超额收益 `fwd_ret - benchmark_ret`，alpha = 5000 |
| 优化约束 | TE=6%，单股偏离±1.5%，行业偏离±3%，L3 TopN=50 |
| 换手惩罚 | `OPT_TURNOVER_LAMBDA = 0.005` |
| 回测区间 | 验证期 2021-2022 |

## 核心指标

验证期 V2 优化组合：

| 指标 | 数值 | 目标 |
|---|---:|---:|
| 年化超额收益 | +2.77% | — |
| 信息比率 IR | 0.483 | >= 0.5 |
| 超额最大回撤 | 3.98% | <= 10% |
| 实现跟踪误差 | 5.73% | — |
| 月度胜率 | 39.13% | — |
| 年化双边换手 | 1004% | 500%-1500% |
| 优化器 L1 严格解 | 24/24 | — |

训练期换手惩罚网格中，`lambda=0.005` 的训练期 IR 为 1.425，年化双边换手为 733%。

## 关键证据文件

| 内容 | 文件 |
|---|---|
| Ridge 信号 IC 与 alpha 选择 | `experiments/ridge_signal/results/comparison_report.md` |
| Ridge 替换主管线信号后的回测 | `experiments/ridge_signal/results/backtest/comparison_backtest_report.md` |
| TE 网格实验 | `experiments/te_grid/results/comparison_report.md` |
| 换手惩罚网格实验 | `experiments/turnover_lambda_grid/results/comparison_report.md` |
| 当前配置快照 | `experiments/v1.1/config_snapshot.py` |
| 本归档元数据 | `experiments/v1.1/manifest.json` |

## 结论

`Ridge v2 + lambda=0.005` 是当前验证期内最优配置。IR 仍未达到 0.5，差距为 0.017；回撤和换手均达标。下一步应在 `lambda=0.005` 附近做细化搜索或推进 Elastic Net 信号实验。若直接进入测试集，应先完成预登记；当前仍有 2 次测试集机会。
