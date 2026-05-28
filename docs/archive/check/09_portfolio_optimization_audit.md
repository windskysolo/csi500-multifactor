# 阶段 6：协方差与组合优化审查

> 审查日期：2026-05-20  
> 审查依据：`check/00_review_requirements.md`、`check/02_full_project_review_plan.md`  
> 审查范围：`src/portfolio/covariance.py`、`src/portfolio/optimizer.py`、`notebooks/04_portfolio_optimization.ipynb`、`scripts/run_test_pipeline.py`、`data/processed/cov_cache/`、`data/processed/portfolio_weights_*.parquet`  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未重建协方差或组合权重。

## 阶段结论

不通过。

当前优化主路径具备 Ledoit-Wolf 协方差、TE 约束、行业/单股偏离和 L1/L2/L3 fallback 的基本框架；验证期落盘权重行和正常，`portfolio_weights_meta.parquet` 记录了每期 fallback level。  
但 L3 fallback 在真实产物中已经触发 14 期，并且违反停牌锁定和单股偏离约束；缺失协方差被静默替换为单位矩阵且仍可能记录为 L1；协方差缓存缺少正定性与来源审计；测试集流水线会覆盖通用组合权重产物。修复前，`portfolio_weights_optimized.parquet` 不能作为产品级可交易权重产物。

## 只读抽查摘要

- 当前 `portfolio_weights_meta.parquet`：84 期，日期 `2016-01-29` 到 `2022-12-30`。
- fallback 分布：L1=70 期，L3=14 期，L2=0 期。
- `cov_cache/`：81 个文件，日期 `2016-04-29` 到 `2022-12-30`；训练/验证期缺 `2016-01-29`、`2016-02-29`、`2016-03-31` 三期协方差。
- 当前优化权重行和约为 1，持仓数中位数 87；baseline 固定 50 只。
- 未运行测试集流水线。

## F6-001 L3 fallback 违反停牌锁定和单股偏离约束

**严重度**：Blocker

**证据**：
- `src/portfolio/optimizer.py:233-266` 的 `_topn_equal_weight()` 只按 alpha 选 TopN，并排除 `halt_indices` 与 `no_buy_indices`，没有接收或保留 `w_prev_vec`。
- `src/portfolio/optimizer.py:435` 在 L3 调用 `_topn_equal_weight(alpha_vec, n, config.topn, halt_indices, limit_up_idx)`，未处理跌停不可卖、停牌锁定和已有持仓延续。
- `portfolio_weights_meta.parquet` 显示 14 期 `fallback_level=2`。
- 对落盘权重抽查：14 个 L3 日期全部存在停牌锁定违约，停牌股上期权重被置为 0。例如：
  - `2016-03-31`：7 只停牌股未锁定，上期合计权重约 `8.66%`，本期变为 `0`。
  - `2016-07-29`：3 只停牌股未锁定，上期合计权重约 `3.53%`，本期变为 `0`。
- 对单股偏离约束抽查：14 个 L3 日期均有 50 只股票超过 `single_max_dev=1%`，最大偏离约 `1.9%`。

**影响**：
L3 产物不是约束可行权重。若这类权重进入回测或报告，会把无法成交的停牌减仓和超过风险预算的 TopN 权重当作合法目标权重。当前报告中将 L3 描述为“保守且合规”是不成立的。即使验证期没有 L3，训练期和未来测试期一旦触发 L3，组合权重口径都会失真。

**修复建议**：
1. L3 不应简单 TopN 等权。应以 `w_prev` 为基底，先锁定 `halt`、满足 `limit_up: w<=w_prev` 和 `limit_dn: w>=w_prev`，再用剩余可交易资金在可买股票中分配。
2. 如果 L3 被定义为“放弃风险约束的紧急产物”，必须在 metadata 中显式标记 `constraint_compliant=False`，且不得在报告中声称单股/行业/状态约束满足。
3. 对 L3 输出增加约束校验：停牌锁定、涨停不加仓、跌停不减仓、权重和、单股偏离、行业偏离。

**验证方式**：
- 新增 `tests/test_optimizer.py`：构造含停牌、涨停、跌停且 L1/L2 不可行的样例，要求 L3 至少满足状态约束。
- 对历史 14 个 L3 日期重算，确认停牌股本期权重等于上期权重，或 metadata 明确标记为非约束可行。

## F6-002 缺失协方差被单位矩阵替代，但 metadata 可能仍显示 L1 成功

**严重度**：High

**证据**：
- `notebooks/04_portfolio_optimization.ipynb` 的协方差逻辑提示缺失时使用 `1e-4·I`。
- `src/portfolio/optimizer.py:525-528` 在缺少 `cov_dict[T]` 时使用 `np.eye(len(period_codes)) * 1e-4`。
- 当前 `cov_cache/` 缺少 `2016-01-29`、`2016-02-29`、`2016-03-31` 三期。
- `portfolio_weights_meta.parquet` 中 `2016-01-29` 和 `2016-02-29` 仍记录为 `fallback_level=0, solver_status=optimal`。

**影响**：
单位矩阵不是实际风险模型。用它求解出的 L1 不能解释为“严格 TE 约束成功”，会污染 fallback 统计和风险控制结论。特别是在冷启动期，风险模型缺失和信号冷启动同时发生，应被单独标记，而不是混入正常 L1。

**修复建议**：
1. 协方差缺失时不要伪装成 L1；应显式降级为 L2，并在 `solver_status` 或 metadata 中记录 `cov_missing`。
2. `portfolio_weights_meta.parquet` 增加 `cov_available`、`cov_source`、`cov_min_eig`、`cov_repaired`、`n_cov_missing_codes` 字段。
3. 对前 60 个交易日样本不足期设置 warm-up：跳过优化、使用基准权重或明确使用非风险模型 fallback。

**验证方式**：
- 删除某期协方差缓存后运行单元测试，要求输出 metadata 显示 `cov_available=False` 且不允许 `fallback_level=0`。

## F6-003 协方差缓存读取只检查 NaN，未重新验证正定性、对称性和来源

**严重度**：High

**证据**：
- `src/portfolio/covariance.py:55-76` 有 `_ensure_positive_definite()`，但只在重新估计时调用。
- `notebooks/04_portfolio_optimization.ipynb` 和 `scripts/run_test_pipeline.py:258-264` 读取缓存时只检查 `not cov_df.isna().any().any()`。
- `src/portfolio/optimizer.py:387-389` 使用 `cp.psd_wrap(cov)`，等于告诉 CVXPY 信任该矩阵为 PSD。

**影响**：
一旦缓存文件来自旧逻辑、手工修改、列顺序错配或数值损坏，优化器会跳过 CVXPY 的 PSD 检查并直接求解，可能得到不可解释的权重或错误 fallback。当前缓存也没有记录 lookback、收益口径、股票列表 hash、最小特征值和修复量，无法审计风险模型版本。

**修复建议**：
1. 每次读取缓存后检查：shape、index/columns 与 codes 完全一致、对称性、最小特征值、对角线非负。
2. 若 `min_eig < EPSILON_DIAG`，重新执行正定修复并记录。
3. 为每期协方差写入 metadata，至少包含 `lookback_days`、`return_col`、`codes_hash`、`min_eig_before`、`diag_delta`、`shrinkage`。

**验证方式**：
- 构造一个非 PSD 缓存文件，测试读取层必须拒绝或修复，并在 metadata 留痕。

## F6-004 优化器使用上一期目标权重作为 `w_prev`，不是实际执行后持仓权重

**严重度**：High

**证据**：
- `scripts/run_test_pipeline.py:283-310` 和 notebook 优化主循环中，`w_prev` 直接取上一期 `result.weights`。
- `backtest_design.md:778` 已承认该问题：“nb04 传给优化器的 w_prev 是目标权重而非实际持仓（因价格漂移）”。

**影响**：
停牌锁定、涨停不加仓、跌停不减仓约束理论上应基于上一期实际持仓权重，而不是上一期目标权重。实际回测中 T+1 执行失败、价格漂移、现金残留都会让实际持仓偏离目标。用目标权重做约束会低估交易约束对组合的影响，并让优化器输出与真实可成交路径不一致。

**修复建议**：
1. 回测和优化需要形成一致状态：每期优化前使用上一执行周期的实际持仓权重作为 `w_prev`。
2. 如果工程上暂不做闭环优化，应在报告中明确“优化约束使用目标权重近似，实际执行层另行修正”，并降低对 TE/换手约束的结论强度。
3. metadata 记录 `w_prev_source=target_weight|actual_weight`。

**验证方式**：
- 构造价格大幅漂移且存在涨跌停的两期样例，验证目标权重近似与实际持仓闭环会产生不同约束结果。

## F6-005 测试集流水线会覆盖通用组合权重产物

**严重度**：Blocker

**证据**：
- `scripts/run_test_pipeline.py:7-10` 明确说明会重建全期信号、补充优化权重并全期写入。
- `scripts/run_test_pipeline.py:59-60` 将组合权重路径固定为：
  - `data/processed/portfolio_weights_optimized.parquet`
  - `data/processed/portfolio_weights_baseline.parquet`
- `scripts/run_test_pipeline.py:342-343` 直接写入上述通用路径。
- `scripts/run_test_pipeline.py:546-548` 对全期 `all_dates` 执行协方差估计和组合优化。

**影响**：
一旦运行测试集流水线，训练/验证期通用组合权重产物会被包含 2023-2025 的全期结果覆盖。即使 backtest 输出文件已拆成 `_test`，组合权重层仍会污染测试集纪律和复现链路。

**修复建议**：
1. 测试集权重写入独立目录，例如 `data/processed/test_run_2/portfolio_weights_optimized.parquet`。
2. 通用 `portfolio_weights_*.parquet` 只允许保存训练+验证期产物。
3. 测试流水线启动前检查目标路径，若会覆盖通用产物则直接失败。

**验证方式**：
- 用 dry-run 或单元测试确认测试集 run 的所有输出路径都包含 `test_run_N` 或 `_test` 标识，不触碰训练/验证通用产物。

## F6-006 缺少组合优化与协方差单元测试

**严重度**：High

**证据**：
- 当前 `tests/` 下只有 `__pycache__/`，没有 `tests/test_optimizer.py` 或 `tests/test_covariance.py`。
- 阶段 6 涉及的硬规则包括正定性修复、fallback、停牌/涨跌停约束、缺失协方差防御，均无自动回归保护。

**影响**：
后续任何改动都可能再次引入状态约束失效、fallback 误标、协方差缓存失真或权重不可行，且无法被 CI 或本地测试拦截。

**修复建议**：
优先补以下测试：
1. `test_l3_preserves_locked_and_limit_constraints`
2. `test_missing_covariance_not_reported_as_l1`
3. `test_cov_cache_validation_rejects_non_psd`
4. `test_postprocess_does_not_break_constraints_or_triggers_fallback`

## 阶段 6 通过项与保留意见

- Ledoit-Wolf 收缩估计已经在 `estimate_covariance_lw()` 中使用。
- 当前缓存抽样检查的协方差矩阵为正定，最近和最早样本最小特征值均为正。
- 优化器主路径包含 L1/L2/L3 三层结构，并记录了 `fallback_level`、`solver_status`、`solve_time_s`。
- 但由于上述 Blocker，当前阶段整体不通过。

