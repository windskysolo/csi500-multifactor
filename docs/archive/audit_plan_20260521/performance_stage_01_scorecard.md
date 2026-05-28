# 0-1 阶段审查记录：可信度复核与总体效果判定

生成日期：2026-05-21

审查范围：只审查阶段 0 和阶段 1。未运行正式测试集，未使用 2023-2025 结果，未修改策略参数。

## 结论摘要

阶段 0 结论：当前训练/验证产物没有发现需要立刻停止效果诊断的 P0 问题。测试集 Run #2 未运行，最新公共流水线是 `training_validation`，验证期产物范围截止 2022-12-31。

阶段 1 结论：当前 V2 不是工程链路失败，而是“风控有效、收益失败”。V2 的 TE、超额回撤、换手都在目标或合理区间，但年化超额为负，IR 为负，未达到产品发布目标。后续诊断应重点查信号衰减、组合优化稀释 alpha、行业内选股失败，而不是优先查交易成本或 fallback。

## 阶段 0：轻量安全复核

| 检查项 | 结果 | 审查判断 |
|---|---:|---|
| 最新 manifest | `run_20260520_212950.json` | 通过 |
| pipeline 类型 | `training_validation` | 通过 |
| manifest success | `True` | 通过 |
| 执行阶段 | convert, factors, evaluate, signal, portfolio, backtest, attribution | 通过 |
| train_end / valid_end | 2021-12-31 / 2022-12-31 | 通过 |
| manifest 测试集计数 | `test_set_run_count=1` | 通过 |
| git 测试集记录 | 仅 `80708d4 [TEST_SET_RUN_1]` | 通过 |
| `data/processed/test_run_*` | 不存在 | 通过 |
| `data/processed/factor_panels_test_run_*` | 不存在 | 通过 |
| 测试集隔离/发布门禁测试 | 15 passed, 1 pytest cache warning | 通过但有工程卫生 warning |

关键产物范围：

| 产物 | 范围 | 形状 | 判断 |
|---|---|---:|---|
| `factor_panels/*.parquet` | 2016-01-29 到 2022-12-30 | 27 个文件, 每个 84 x 1065 | 未覆盖测试期 |
| `composite_signal_ic_ir.parquet` | 2016-01-29 到 2022-12-30 | 84 x 1065 | 未覆盖测试期 |
| `portfolio_weights_optimized.parquet` | 2016-01-29 到 2022-12-30 | 84 x 1069 | 未覆盖测试期 |
| `backtest_nav.parquet` | 2022-01-04 到 2022-12-30 | 242 x 3 | 仅验证期 |

需要披露但不阻塞阶段 1 的问题：

- `quality` 阶段仍是显式跳过，不等于自动化数据质量检查已经完成。
- `portfolio_weights_meta.parquet` 全样本有 15/84 期 `constraint_compliant=False`，但 2022 验证期 12 期全部为 L1 且 `constraint_compliant=True`，所以这不是 2022 V2 表现差的直接原因。
- `attribution_metadata.json` 中 `reconciliation_max_excess_discrepancy` 写成 `NaN`，严格 JSON 语义不佳；`backtest_weights_v2` hash 为 `e3b0c442`，与非空 parquet 文件不匹配，疑似 hash 生成逻辑问题。
- `reports/analysis_v2_results.md` 的“已知未修复风险”可能含过期描述，需要在最终发布报告前清理。
- `reports/factor_combination` 下图表时间戳早于最新流水线，若最终报告引用这些图，需要重生成或标注非最新。

阶段 0 判定：可以进入效果诊断。上述问题属于披露和报告质量问题，当前没有证据表明 2022 验证期绩效被测试集污染。

## 阶段 1：总体效果判定

核心指标来自 `data/processed/backtest_metrics.parquet`，并与 `reports/analysis_v2_results.md` 一致。

| 指标 | V1 Baseline | V2 优化组合 | V2 判定 |
|---|---:|---:|---|
| 年化绝对收益 | -18.28% | -20.41% | 差 |
| 基准年化收益 | -19.55% | -19.55% | 参照 |
| 年化超额收益 | +1.26% | -0.86% | 差 |
| IR | +0.173 | -0.191 | 未达标 |
| 跟踪误差 | 7.32% | 4.50% | 风控有效 |
| 超额最大回撤 | 11.40% | 7.32% | 达标 |
| 月胜率 | 45.45% | 54.55% | 表面尚可但不足 |
| 年化双边换手 | 899% | 863% | 达标 |
| 成本合计 | 1.16% | 1.09% | V2 成本略低 |

目标对照：

| 产品目标 | V2 结果 | 是否通过 |
|---|---:|---|
| IR >= 0.5 | -0.191 | 否 |
| 超额最大回撤 <= 10% | 7.32% | 是 |
| 年化双边换手 5-15 倍 | 8.63 倍 | 是 |
| 相对中证500全收益有正超额 | -0.86% | 否 |

月度结构：

| 观察 | 结果 |
|---|---|
| 官方月胜率口径 | V2 有 6/11 个月跑赢基准 |
| V2 相对 V1 | V2 仅 5/11 个月优于 V1 |
| V2 最差超额月份 | 2022-06: -3.81%, 2022-10: -1.69%, 2022-05: -0.98%, 2022-02: -0.89% |
| V2 相对 V1 最弱月份 | 2022-03: -1.59%, 2022-11: -1.37%, 2022-08: -1.19%, 2022-02: -0.87% |

审查判断：

1. V2 收益端明确失败。年化超额为 -0.86%，IR 为 -0.191，离 IR >= 0.5 的发布线很远。
2. V2 风控端有效。TE 从 V1 的 7.32% 降到 4.50%，超额最大回撤从 11.40% 降到 7.32%。
3. V2 不是被交易成本拖垮。V2 年化双边换手 863%，低于 V1 的 899%；成本合计也略低于 V1。
4. V1 也不能作为可发布方案。V1 虽然年化超额为正，但 IR 只有 0.173，且超额最大回撤 11.40% 超过 10% 目标。
5. 月胜率不能掩盖失败。V2 有超过一半月份跑赢基准，但坏月份损失幅度更大，最终导致负 IR 和负年化超额。

阶段 1 判定：当前策略没有达到可发布效果标准。更准确的产品化描述是：风控层把组合压稳了，但 alpha 没有留下来，收益端没有证明有效。

## 下一阶段审查目标

阶段 2 应优先回答：6 个入模因子中哪些在 2022 仍有效，哪些衰减或方向翻转。

阶段 3 应优先回答：IC_IR 加权是否比等权更差，以及是否把权重过多给了训练期强、验证期弱的因子。

阶段 4 应优先回答：V2 优化是否把 alpha exposure 稀释到不足以覆盖选股噪声。

暂不作为主线的问题：

- 交易成本不是 V2 弱于 V1 的主因。
- 2022 验证期没有 L3 fallback，所以 fallback 不是当年直接原因。
- 安全性和测试集隔离只保留轻量复核，除非后续发现 P0 口径问题。

## 本次执行记录

- 未运行 `scripts/run_test_pipeline.py`。
- 未生成 `data/processed/test_run_*`。
- 未生成 `data/processed/factor_panels_test_run_*`。
- 执行了轻量测试：`python -m pytest tests\test_test_pipeline_isolation.py tests\test_pipeline_release_gates.py -q --basetemp=.codex_tmp_stage01`，结果 `15 passed, 1 warning`。

