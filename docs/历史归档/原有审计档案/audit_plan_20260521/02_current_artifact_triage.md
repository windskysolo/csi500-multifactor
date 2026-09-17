# 当前产物第一轮分诊

本文件记录基于现有训练/验证产物的第一轮观察。它不是最终审计结论，只用于决定后续核查顺序。

## 1. 当前基线

最新完整训练/验证流水线：

- manifest：`data/processed/run_manifests/run_20260520_212950.json`
- pipeline：`convert → factors → evaluate → signal → portfolio → backtest → attribution`
- git commit：`0f7a71f`
- 运行状态：`success=true`
- 覆盖边界：训练 2016-2021，验证 2022；未运行正式 Run #2。
- 测试集纪律：Run #1 已消耗且作废，Run #2 是唯一剩余正式机会。

## 2. 因子层第一轮观察

候选因子：

- 当前因子面板共 27 个，路径为 `data/processed/factor_panels/*.parquet`。
- 诊断表为 `data/processed/factor_panel_diagnostics.parquet`，形状约为 2268 行 × 7 列。

最终入模因子：

- `amihud`
- `rev_yoy`
- `ep_ttm`
- `cfp`
- `gross_margin`
- `mom_12_1`

验证期方向与稳定性疑点：

| 因子 | 训练 IC_IR | 2022 验证 IC_IR | 初步判断 |
|---|---:|---:|---|
| `amihud` | 0.5083 | 0.2818 | 入模后仍有正贡献信号，优先保留核查 |
| `rev_yoy` | 0.5080 | 0.0405 | 验证期明显衰减，需查是否只在训练期有效 |
| `ep_ttm` | 0.4671 | -0.0552 | 方向翻转疑点，需复核入模规则 |
| `cfp` | 0.3893 | 0.1821 | 相对稳健，但强度一般 |
| `gross_margin` | 0.3769 | -0.1486 | 方向翻转疑点，需复核 |
| `mom_12_1` | 0.3353 | 0.3576 | 验证期保持正向，优先作为稳定候选 |

被排除但需复核的因子：

- `turn_20d`、`ivol_60d`、`max_ret`、`short_ratio` 在验证期仍有较强同向表现，但不能因为 2022 好就直接加入。
- `q_roe`、`roa`、`roe`、`np_yoy` 训练期强，但验证期方向翻转，可能代表 2022 风格切换或财务质量因子失效。
- 这些因子应通过训练期内部切分和经济逻辑复核，而不是用验证期单年调参。

需要立即澄清：

- `shift_warning=True` 的定义、阈值和阻断级别。
- `gross_margin` 的 `shift_drop` 很低但未被 warning 阻断的原因。
- 分组回测与可交易组合收益之间的口径差异是否在所有报告中说明。

## 3. 信号合成第一轮观察

当前 metadata：

- 路径：`data/processed/composite_signal_metadata.json`
- 方法：`ic_ir`
- 窗口：24 个月
- 最少有效因子：5
- 冷启动期数：13
- 日期范围：2016-01-29 至 2022-12-30

优先核查点：

- 冷启动 13 期使用等权，是否对早期训练表现产生结构性影响。
- `icir_weight_history.parquet` 中 2017-02 起权重开始变化，需确认每期权重只用 T 之前 IC。
- IC_IR 加权是否放大了不稳定财务因子，导致 V2 在验证期弱于 V1。
- 需要把 `composite_signal_equal.parquet` 与 `composite_signal_ic_ir.parquet` 的训练内、验证期表现并列表。

## 4. 组合优化第一轮观察

当前 `portfolio_weights_meta.parquet`：

| 项目 | 结果 |
|---|---:|
| L1 | 67 |
| L2 | 2 |
| L3 | 15 |
| `constraint_compliant=True` | 69 |
| `constraint_compliant=False` | 15 |

优先核查点：

- 15 期 L3 且非合规，是发布前必须解释或修复的问题。
- 当前 `w_prev_source="target_weight"`，真实执行后的 actual weights 尚未反馈优化器；这是产品化弱点。
- 需要检查 V2 相比 V1 的 alpha 暴露、行业偏离、成本、换手和 fallback 日期收益差异。
- 若 V2 的 TE 降低但 alpha 同时被压低，需要明确优化目标是否过度保守。

## 5. 回测第一轮观察

当前 `backtest_metrics.parquet`：

| 指标 | V1 Baseline | V2 优化组合 |
|---|---:|---:|
| 年化收益 | -18.28% | -20.41% |
| 基准收益 | -19.55% | -19.55% |
| 年化超额 | +1.26% | -0.86% |
| Tracking Error | 7.32% | 4.50% |
| IR | 0.173 | -0.191 |
| 超额最大回撤 | -11.40% | -7.32% |
| 月胜率 | 45.5% | 54.5% |

初步判断：

- V2 风险控制确实降低了 TE 和超额回撤，但收益端弱于 V1。
- 这不一定是 bug，也可能是 2022 单年风格不利或优化压低了 alpha 暴露。
- 需要用训练期内部滚动回测和收益拆解判断 V2 是否长期有必要。

交易日志观察：

- V2 2022 年月度调仓日志存在少量 `n_no_buy`、`n_locked`、`n_no_price`。
- 年化双边换手约 863%，落在 5-15 倍目标范围。
- 下一步应复算成本、现金权重、NAV 月收益，确认指标可对账。

## 6. 归因第一轮观察

当前报告摘要：

- Brinson 总超额约 -0.83%。
- 行业配置约 +2.66%。
- 个股选择约 -3.48%。
- 交互项约 +0.01%。
- 平均现金仓位约 0.28%。

因子归因疑点：

- 平均加权 R2 约 0.048，说明当前因子归因解释力较弱。
- 残差项约 +3.14%，需要解释它包含特异收益、成本、口径差异还是模型遗漏。

metadata 质量疑点：

- `data/processed/attribution_metadata.json` 中 `reconciliation_max_excess_discrepancy` 为 `NaN`，严格 JSON 产品化应避免。
- `input_hashes.backtest_weights_v2` 为 `e3b0c442`，疑似空内容 hash，需要核查 hash 生成逻辑。
- `attribution_nav_reconciliation.parquet` 首期 NAV 收益为 NaN，应标记为 warm-up 或从统计中排除。

## 7. 当前不建议做的事

- 不建议为了 2022 验证期把因子集合直接改成当年表现最好的因子。
- 不建议引入 XGBoost、深度学习或新依赖；当前主要问题是口径、稳定性和优化解释。
- 不建议在 L3 非合规、metadata 异常和入模稳定性未核查前运行 Run #2。
- 不建议把分组回测高 Sharpe 当作组合可交易超额的证据。

## 8. 第一轮审查任务清单

| 优先级 | 任务 | 产出 |
|---|---|---|
| P0 | 核查公共产物未混入测试期 | 产物隔离记录 |
| P0 | 核查 PIT、基准、T+1 成交三条生命线 | 数据/回测可信度结论 |
| P1 | 修复或解释 attribution metadata 的 `NaN` 和 hash 异常 | 严格 metadata 记录 |
| P1 | 分解 V2 弱于 V1 的来源 | 优化层诊断表 |
| P1 | 复核最终 6 因子的训练内稳定性和 2022 方向翻转 | 因子入模复核表 |
| P1 | 复核 L3 fallback 非合规期 | fallback 日期、原因、影响 |
| P2 | 增加报告编码/渲染检查 | 可发布报告检查记录 |

