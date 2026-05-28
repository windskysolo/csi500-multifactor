# 阶段 7：回测、成本与指标审查

> 审查日期：2026-05-20  
> 审查依据：`check/00_review_requirements.md`、`check/02_full_project_review_plan.md`  
> 审查范围：`src/backtest/engine.py`、`src/backtest/transaction.py`、`src/backtest/metrics.py`、`notebooks/05_backtest.ipynb`、`scripts/run_test_pipeline.py`、`data/processed/backtest_*.parquet`、`reports/analysis_v2_results.md`  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未重建回测产物。

## 阶段结论

不通过。

当前回测框架已经体现 T+1 开盘成交、`open_adj` 不前填、`close_adj` 估值前填、全收益基准 NAV、印花税切换、佣金和滑点成本等关键设计。  
但执行层用上一日收盘组合净值计算 T+1 开盘目标金额，会在隔夜价格变化时错误地产生交易；缺失状态默认 `FREE` 会放行无法确认可交易的股票；当前报告指标与落盘指标不一致；交易约束审计字段被全历史股票池噪声污染。修复前，不应解释当前 `backtest_nav.parquet`、`backtest_metrics.parquet` 或 `analysis_v2_results.md` 的表现结论。

## 只读抽查摘要

- 当前通用回测产物覆盖验证集：`2022-01-04` 到 `2022-12-30`，未在本次审查中运行测试集。
- 当前 `backtest_metrics.parquet` 中 V2 指标：IR `0.340`，超额最大回撤约 `-4.66%`，TE 约 `4.33%`。
- `reports/analysis_v2_results.md` 中仍写 V2 IR `1.025`、年化超额 `+5.35%`，与当前落盘产物不一致。
- 手工检查 `compute_trade_cost(100,100)`：
  - `2023-08-27` 成本 `0.31`，对应 10 bps 印花税。
  - `2023-08-28` 成本 `0.26`，对应 5 bps 印花税。

## F7-001 T+1 开盘调仓使用上一日收盘净值计算目标金额

**严重度**：Blocker

**证据**：
- `src/backtest/engine.py:459-461` 调用 `_execute_rebalance()` 时传入的 `portfolio_value` 是主循环上一日收盘估值。
- `src/backtest/engine.py:330` 用 `target_value = target_w * portfolio_value` 计算目标金额。
- `src/backtest/engine.py:321-329` 同时用 T+1 `open_prices` 计算当前持仓市值。
- 最小复现实验：若当前持有 `A` 100%，上一收盘组合净值为 100，T+1 开盘价从 100 跳到 200，目标仍是 `A` 100%，当前逻辑会卖出约 100 金额，把持仓从 1 股降到约 0.499 股并留下现金；正确行为应是不交易。

**影响**：
这会系统性扭曲隔夜跳空后的成交、换手、成本、现金和收益路径。月频回测中每次调仓都在 T+1 开盘执行，该错误直接影响回测 NAV、交易成本、换手率和所有绩效指标。

**修复建议**：
1. 在执行日前先计算 T+1 开盘前组合净值：`cash + Σ shares_i * exec_price_i`。
2. 对停牌无开盘价的既有持仓，使用上一可得收盘价估值并锁定，不参与成交。
3. 用开盘前组合净值计算目标金额，再执行卖出和买入。
4. `TradeRecord.portfolio_value_before` 应记录开盘前净值，而不是上一日收盘净值。

**验证方式**：
- 新增 `tests/test_backtest_engine.py::test_rebalance_uses_open_pretrade_value`：构造目标权重不变但开盘价翻倍的场景，要求无交易、无成本。
- 构造多股票隔夜涨跌分化场景，验证目标权重以开盘前净值为基准。

## F7-002 缺失执行日状态默认 `FREE`，会放行无法确认可交易的股票

**严重度**：Blocker

**证据**：
- `src/backtest/engine.py:187` docstring 写明“不在 status_snap 中的股票默认为 FREE”。
- `src/backtest/engine.py:195-203` 初始化全部股票为 `TradeState.FREE`，再用状态快照覆盖。
- `src/backtest/engine.py:224-226` 在某执行日状态表缺失时，将全部股票设为 `FREE`。
- 当前 `stock_status.parquet` 每日行数在 940 到 1218 之间，无法覆盖权重面板的历史 union 股票列。

**影响**：
完全停牌、缺行情、数据缺口或状态表构建不完整时，回测可能把无法成交的股票视为可自由交易。该问题会低估执行约束、换手失败和现金拖累，直接扭曲回测收益。

**修复建议**：
1. 对目标权重或既有持仓中的股票，若状态缺失，不应默认 `FREE`；保守路径应设为 `LOCKED` 并记录，或直接抛错中止回测。
2. 在 `stock_status.parquet` 构建层补齐全市场交易日状态，明确区分“无行情但仍上市停牌”和“未上市/已退市/不在股票池”。
3. trade log 增加 `n_missing_status`，并在报告中披露。

**验证方式**：
- 新增单元测试：构造执行日状态缺失但目标有买入的股票，要求不得成交。
- 数据审计：对每个执行日检查 `target_positive ∪ current_holding` 是否全部有状态记录。

## F7-003 交易日志的 `n_no_price` 被全历史股票列污染

**严重度**：Medium

**证据**：
- `src/backtest/engine.py:536` 将 `all_codes` 设置为 `weights_panel.columns.tolist()`，即全历史 union 股票列。
- `src/backtest/engine.py:317` 在每次调仓时遍历 `set(shares.keys()) | set(target_w.index)`。
- 当前权重面板有 1069 列，但每期实际成分约 500 只、实际持仓约 80-90 只。
- 当前 V2 `trade_log` 中 `n_no_price` 每期 23 到 38，但其中包含大量目标权重为 0 且无持仓的历史股票。

**影响**：
交易日志无法准确回答“多少目标交易因为无开盘价无法执行”。这会污染执行约束审计和报告中的状态触发频率分析。

**修复建议**：
1. `_execute_rebalance()` 中的 active universe 应限制为 `current_holding ∪ target_nonzero`，另行保留全列覆盖率审计。
2. `n_no_price` 只统计有持仓或目标交易需求的股票。
3. 增加 `n_no_price_active` 与 `n_no_price_inactive` 两个字段，避免混淆。

**验证方式**：
- 构造一个目标权重含大量 0 列的样例，要求 `n_no_price` 不统计这些 inactive 股票。

## F7-004 当前分析报告与落盘回测指标不一致

**严重度**：High

**证据**：
- `reports/analysis_v2_results.md:13-17` 写 V2 年化超额 `+5.35%`、IR `1.025`、TE `5.22%`、超额最大回撤 `-4.33%`。
- 当前 `data/processed/backtest_metrics.parquet` 显示 V2 年化超额约 `+1.47%`、IR `0.340`、TE `4.33%`、超额最大回撤约 `-4.66%`。
- `reports/analysis_v2_results.md` 生成日期为 2026-05-11，当前回测产物最后修改时间为 2026-05-19。

**影响**：
报告结论已经与当前产物脱节。继续引用该报告会误导对优化器、回测和阶段目标是否达标的判断。

**修复建议**：
1. 在回测修复前，给旧报告加“已过期/不可用于当前结论”的醒目标记。
2. 修复阶段 2-7 的阻断问题后，从同一 commit、同一数据产物重新生成报告。
3. 报告中记录输入文件 hash、生成时间、代码 commit 和测试集运行计数。

**验证方式**：
- 报告生成脚本读取 `backtest_metrics.parquet` 后自动校验文档中的关键指标，或直接由模板生成，禁止手工维护数值。

## F7-005 回测成本、指标和执行约束缺少单元测试

**严重度**：High

**证据**：
- 当前 `tests/` 下只有 `__pycache__/`，没有 `test_transaction.py`、`test_backtest_metrics.py`、`test_backtest_engine.py`。
- 印花税切换、T+1 成交、停牌/涨跌停、收益和回撤极端场景均为项目硬性测试要求。

**影响**：
阶段 7 的核心金融逻辑没有回归保护。尤其是 F7-001 这类执行基准错误，如果没有合成样例测试，很容易长期隐藏在“结果看起来合理”的回测表中。

**修复建议**：
优先补以下测试：
1. `test_stamp_duty_cut_date_20230828`
2. `test_rebalance_uses_open_pretrade_value`
3. `test_missing_status_is_locked_or_error`
4. `test_limit_up_no_buy_and_limit_down_no_sell`
5. `test_metrics_extreme_nav_paths`

## 阶段 7 通过项与保留意见

- `src/backtest/transaction.py` 的印花税切换逻辑与项目规则一致：`2023-08-28` 起卖出单边 5 bps。
- 成本函数包含佣金双边 2.5 bps 和滑点双边 8 bps。
- `src/backtest/engine.py` 使用 `open_adj` 执行，且不前填 `open_adj`；`close_adj` 用于连续估值。
- 基准读取强制要求 `index_quote.parquet` 的 `nav` 列，不允许降级到价格指数 `close`。
- 但由于执行基准净值和状态缺失默认值两个 Blocker，阶段 7 整体不通过。

