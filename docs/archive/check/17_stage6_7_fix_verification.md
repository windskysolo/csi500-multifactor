# 阶段 6-7 修复复核

> 复核日期：2026-05-20  
> 复核依据：`check/04_fix_log.md` 修复批次 6-7、`check/09_portfolio_optimization_audit.md`、`check/10_backtest_audit.md`、`check/14_incomplete_fix_register.md`  
> 复核范围：`src/portfolio/optimizer.py`、`src/portfolio/covariance.py`、`src/backtest/engine.py`、`notebooks/04_portfolio_optimization.ipynb`、`notebooks/05_backtest.ipynb`、`scripts/run_test_pipeline.py`、`tests/test_optimizer.py`、`tests/test_backtest_engine.py`、`tests/test_backtest_metrics.py`、当前 `data/processed/portfolio_weights_*.parquet`、`data/processed/backtest_*.parquet`、`reports/analysis_v2_results.md`。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行或消耗 2023-2025 测试集，未重建组合权重和回测产物。

## 总结结论

阶段 6-7 未完全闭环。

阶段 6 的源码和单元测试已有明显修复，但当前公开组合权重仍是旧产物，`portfolio_weights_meta.parquet` 仍缺 `cov_available`、`w_prev_source` 等字段，14 个 L3 日期仍存在停牌锁定和单股偏离违约；生产 notebook 仍保留旧的协方差读取和缺失协方差处理逻辑。阶段 6 不应标记为完成。

阶段 7 的回测引擎源码已修复核心执行逻辑，相关单元测试通过；但当前 `backtest_*.parquet` 仍是修复前旧产物，报告只是加了过期声明，正文旧指标和旧结论仍保留。阶段 7 可以视为“源码和测试基本完成，产物与报告未重建闭环”。

## 复核运行

```text
python -m pytest tests\test_optimizer.py tests\test_backtest_engine.py tests\test_backtest_metrics.py -q
# 65 passed, 5 warnings

python -m pytest tests/ -q
# 首次失败：pytest tmp_path 访问 C:\Users\lengyanjun\AppData\Local\Temp\pytest-of-lengyanjun 被拒绝

python -m pytest tests/ -q --basetemp=.codex_tmp_stage67
# 178 passed, 2 skipped, 6 warnings
```

说明：
- 相关阶段单元测试全部通过。
- 全量测试需指定项目内 `--basetemp` 才能避开本机临时目录权限问题。
- `.pytest_cache` 仍有 `WinError 5` warning，属于阶段 0 遗留工程环境问题。

## 阶段 6 逐项复核

| 编号 | 修复日志状态 | 复核状态 | 证据 |
|---|---|---|---|
| F6-001 | 已修复 | 部分修复，Blocker 仍在产物层存在 | 源码 L3 已接收 `w_prev_vec`、`limit_dn_indices`；但当前公开权重 14 个 L3 日期仍全部有单股偏离违约，且均存在停牌锁定违约；合成无候选边界仍会给涨停不可买股票新增权重。 |
| F6-002 | 已修复 | 部分修复 | 源码缺协方差时会记录 `cov_available=False` 并覆盖 `fallback_level>=1`；但当前公开 meta 仍只有 4 列，2016-01-29、2016-02-29 缺协方差仍显示 `fallback_level=0`。 |
| F6-003 | 已修复 | 部分修复 | `validate_and_repair_covariance()` 与测试存在；但 `notebooks/04_portfolio_optimization.ipynb` 仍只查 NaN，协方差缓存仍无 source/min_eig/repair metadata。 |
| F6-004 | 已修复（文档化） | 部分修复 | 源码 meta 写 `w_prev_source="target_weight"`；当前公开 meta 缺该字段，且仍未实现实际执行持仓反馈优化器。 |
| F6-005 | 已修复 | 待验证 | `scripts/run_test_pipeline.py` 已写入 `test_run_dir`；本次未授权运行测试集，当前无 `data/processed/test_run_*` 可验证。 |
| F6-006 | 已修复 | 已闭环 | `tests/test_optimizer.py` 存在；相关测试通过。 |

### 关键证据

- 当前 `portfolio_weights_optimized.parquet`：`(84, 1069)`，日期 `2016-01-29` 至 `2022-12-30`，行和约为 1。
- 当前 `portfolio_weights_meta.parquet`：`(84, 4)`，列仅为 `fallback_level`、`solver_status`、`solve_time_s`、`n_holdings`。
- fallback 分布：`{0: 70, 2: 14}`。
- 当前 meta 缺少：`cov_available`、`w_prev_source`、`constraint_compliant`、`cov_min_eig`、`cov_repaired`。
- `data/processed/cov_cache/` 共有 81 个文件，首个为 `20160429.parquet`；meta 覆盖期缺 `2016-01-29`、`2016-02-29`、`2016-03-31` 三期协方差。
- 14 个 L3 日期均有停牌锁定违约和 50 只股票单股偏离超过 1%，最大偏离约 `1.914%` 到 `1.970%`。
- 合成边界样例：`w_prev=[0.0, 0.5]`，股票 0 涨停不可买、股票 1 停牌锁定且无可买候选时，`_topn_equal_weight()` 输出 `[0.5, 0.5]`，即涨停股票从 0 增至 0.5，仍违反不可买约束。

## 阶段 7 逐项复核

| 编号 | 修复日志状态 | 复核状态 | 证据 |
|---|---|---|---|
| F7-001 | 已修复 | 源码/测试已修，产物待重建 | `src/backtest/engine.py` 已在执行前用 T+1 开盘价和停牌收盘价估算 `pretrade_value`；测试覆盖价格翻倍不交易。但当前 `backtest_*.parquet` 时间戳为 2026-05-19，早于本批修复记录，不能证明 NAV 已按新逻辑重算。 |
| F7-002 | 已修复 | 源码/测试已修，文档与产物待闭环 | 缺失状态股票和整日状态缺失已保守设为 `LOCKED`，测试通过；但 `_status_to_trade_state()` docstring 仍写“不在 status_snap 中的股票默认为 FREE”，当前回测产物也未重建。 |
| F7-003 | 已修复 | 源码/测试已修，产物待重建 | `_execute_rebalance()` 已只遍历持仓或目标权重非零股票，测试覆盖 500 只非活跃股票不计入；但当前 `backtest_trades_v1/v2.parquet` 的 `n_no_price` 仍为旧口径，均值 `26.92`、最大 `38`。 |
| F7-004 | 已修复（标注） | 部分修复 | `reports/analysis_v2_results.md` 顶部已有过期声明；但正文仍保留旧表格、旧达标判断和“总体结论”。报告不可作为当前结论。 |
| F7-005 | 已修复 | 已闭环 | `tests/test_backtest_engine.py` 和 `tests/test_backtest_metrics.py` 存在；相关测试通过。 |

### 关键证据

- 当前 `backtest_metrics.parquet`：V2 `information_ratio=0.340449`，`excess_return=0.014725`，`excess_max_drawdown=-0.046553`。
- 当前 `backtest_nav.parquet`：验证期 `2022-01-04` 至 `2022-12-30`。
- 当前 `backtest_trades_v1.parquet` 与 `backtest_trades_v2.parquet`：各 12 行，`n_no_price` 最小 `23`、均值 `26.9167`、最大 `38`，与 F7-003 修复后的预期口径不匹配。
- `reports/analysis_v2_results.md` 顶部声明“本报告已过期，不可用于当前结论”，但正文仍写 V2 IR `1.025`、年化超额 `+5.35%`，与当前 `backtest_metrics.parquet` 不一致。
- `notebooks/05_backtest.ipynb` 调用 `src.backtest.engine.run_backtest`，因此源码修复后重新运行 notebook 应可使用新逻辑；但当前落盘结果尚未重建。

## 需要继续登记的未闭环项

新增或继续保留：

- F6-001：L3 fallback 约束合规未闭环。
- F6-002：公共权重仍把缺失协方差期记录为 L1。
- F6-003：协方差缓存验证未覆盖生产 notebook，缺少缓存 metadata。
- F6-004：`w_prev_source` 未进入公共 metadata，实际持仓反馈未实现。
- F6-005：测试集权重输出隔离尚未端到端验证。
- F7-001：回测 T+1 开盘前净值源码已修，但公开 NAV/交易日志未重建。
- F7-002：缺失状态 LOCKED 源码已修，但 docstring 和公开产物未闭环。
- F7-003：`n_no_price` 源码已修，但公开交易日志仍是旧口径。
- F7-004：报告仅标注过期，未重生成可信报告。

## 自检

- A. 高频犯错点：未运行测试集；未消耗剩余测试集次数；复核了 T+1 开盘成交、缺失状态 LOCKED、停牌/涨跌停约束、协方差缺失、L3 fallback、公开产物是否重建。
- B. 工程质量：相关单元测试通过；全量测试在指定 `--basetemp` 后通过；仍有 `.pytest_cache` 权限 warning 和 backtest pandas FutureWarning。
- C. 统计严谨性：本次未重新计算绩效，不声称当前 IR/超额回撤达标；报告旧指标明确不可用。
- D. 待优化事项：优先修 F6-001 的 L3 无候选边界和单股偏离校验；随后重建训练/验证期权重与回测产物，再重生成报告。
