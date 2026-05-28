# 阶段 5-6 修复核验报告

> 核验日期：2026-05-20  
> 核验对象：`check/04_fix_log.md` 中阶段 5（F5-001 ~ F5-007）与阶段 6（F6-001 ~ F6-006）修复声明  
> 对照文件：`check/03_stage_review_findings.md`、`check/09_portfolio_optimization_audit.md`  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行或重建 2023-2025 测试集，未重建生产合成信号、协方差或组合权重。

## 总结论

阶段 5-6 **尚未完全闭环**。

源码层面已有较多有效修复：信号合成增加方向校验、诊断返回和单元测试；优化器增加 L3 停牌/跌停锁定、协方差缺失标记、协方差缓存验证函数和测试集专用输出目录；相关单测全部通过。

但当前落盘产物仍大量停留在旧口径：公共合成信号缺少权重历史和元数据，公共组合权重 metadata 仍是旧 4 列 schema，14 个 L3 日期仍存在停牌锁定和单股偏离违约；`notebooks/03_factor_combination.ipynb` 与 `notebooks/04_portfolio_optimization.ipynb` 仍可按旧逻辑生成或宣布可交付。因此不能把阶段 5/6 视为产品级已修复。

## 核验命令与结果

### 相关单测

```text
python -m pytest tests\test_signal_combiner.py tests\test_optimizer.py -q
31 passed, 1 warning
```

warning 为 `.pytest_cache` 权限问题，属于阶段 0 遗留。

### 全量单元测试

```text
python -m pytest tests/ -q
162 passed, 2 skipped, 6 warnings
```

- 2 skipped 仍来自 `reports/factor_evaluation/INVALIDATED.md` 导致的评价产物一致性测试跳过。
- warnings 包括 backtest 中 pandas `FutureWarning`、shift test 的 `ConstantInputWarning` 和 `.pytest_cache` 权限 warning。

### 当前产物抽查

```text
data/processed/composite_signal_ic_ir.parquet: shape=(84, 1065), 2016-01-29 ~ 2022-12-30
data/processed/composite_signal_equal.parquet: shape=(84, 1065), 2016-01-29 ~ 2022-12-30
data/processed/ic_series_all_factors.parquet: shape=(83, 12), 2016-01-29 ~ 2022-11-30
data/processed/composite_signal_metadata.json: 不存在
data/processed/icir_weight_history.parquet: 不存在
```

```text
data/processed/portfolio_weights_meta.parquet:
shape=(84, 4)
columns=['fallback_level', 'solver_status', 'solve_time_s', 'n_holdings']
fallback={0: 70, 2: 14}
缺少 cov_available / w_prev_source
```

`data/processed/cov_cache/` 当前 81 个文件，首个为 `20160429.parquet`，缺少 `2016-01-29`、`2016-02-29`、`2016-03-31` 三期协方差缓存。

## 阶段 5：信号合成修复核验

| 编号 | 修复声明 | 核验结论 | 依据 |
|---|---|---|---|
| F5-001 | 合成信号使用不一致最终因子集合 | 部分修复 | 当前 `factor_summary.csv` 与 `final_factors.json` 最终因子集合均为 12 个且集合一致；但 `final_factors.json` 仍缺 `_metadata`，`reports/factor_evaluation/INVALIDATED.md` 仍存在，当前合成信号仍缺可追踪 provenance。 |
| F5-002 | 测试流水线覆盖训练/验证公共路径 | 基本修复，待端到端验证 | `scripts/run_test_pipeline.py` 的 `_build_composite_signals()` 已写入 `output_dir`，不再写公共 `composite_signal_*.parquet` 和 `ic_series_all_factors.parquet`；但未运行测试集流水线验证，且当前没有 `data/processed/test_run_*` 产物。 |
| F5-003 | 冷启动/等权方向硬编码不一致 | 已修复 | `FACTOR_DIRECTIONS["margin_ratio"] == -1`，当前 `factor_summary.csv` 中 `margin_ratio.ic_mean=-0.0500`，方向一致；`tests/test_signal_combiner.py` 覆盖方向校验。 |
| F5-004 | 缺少 IC_IR 权重历史和合成元数据 | 部分修复 | `build_composite_panel(return_diagnostics=True)` 与测试集路径已支持权重历史/metadata；但当前公共合成信号没有 `composite_signal_metadata.json` 和 `icir_weight_history.parquet`，`notebooks/03_factor_combination.ipynb` 仍只写 3 个 Parquet。 |
| F5-005 | 合成参数散落 | 已修复 | `src/config.py` 已集中 `SIGNAL_*` 参数，`combiner.py` 与 `run_test_pipeline.py` 读取配置常量。 |
| F5-006 | Notebook 自检门禁过弱 | 未修复/暂缓 | `notebooks/03_factor_combination.ipynb` 仍将 “IC_IR 加权 vs 等权” 写成 `True` 信息项；当前输出中 `ic_ir=0.772, equal=0.921, 提升=-0.149` 仍打印“全部通过”。 |
| F5-007 | 缺少信号合成单元测试 | 已修复 | `tests/test_signal_combiner.py` 存在，相关测试通过。 |

### 阶段 5 关键残留风险

当前公共信号产物不能回答“本次合成到底用了哪次评价结果、哪些 IC_IR 权重、哪些期为冷启动”。即使源码支持诊断，现有生产/公共产物仍不可审计。阶段 4 评价报告尚未解除失效标记前，阶段 5 的信号只能作为旧产物参考，不能作为可交付 alpha 输入。

## 阶段 6：协方差与组合优化修复核验

| 编号 | 修复声明 | 核验结论 | 依据 |
|---|---|---|---|
| F6-001 | L3 fallback 修复停牌锁定和跌停锁定 | 部分修复，Blocker 仍在 | 源码 normal path 已传入 `w_prev_vec` 和 `limit_dn_indices`；但当前公共权重仍是旧产物，14 个 L3 日期仍有停牌锁定违约和单股偏离违约。源码 L3 仍没有 `single_max_dev` 校验；极端“无可买候选”分支会给涨停股新增权重。 |
| F6-002 | 缺失协方差不得伪装为 L1 | 部分修复 | `optimize_all_periods()` 与测试集路径源码已记录 `cov_available` 并覆盖 fallback；但当前 `portfolio_weights_meta.parquet` 仍缺 `cov_available`，且 `2016-01-29`、`2016-02-29` 在缺失协方差情况下仍记录 `fallback_level=0, solver_status=optimal`。 |
| F6-003 | 协方差缓存读取验证 PSD/对称性 | 部分修复 | 新增 `validate_and_repair_covariance()` 且测试集路径调用；但 `notebooks/04_portfolio_optimization.ipynb` 仍只检查 NaN 后加载缓存，生产权重重建路径仍可绕过验证；缓存也没有 source/min_eig/repair metadata。 |
| F6-004 | 元数据声明 `w_prev_source="target_weight"` | 部分修复 | 源码和测试集路径新增字段；当前公共 `portfolio_weights_meta.parquet` 缺少 `w_prev_source`，notebook 仍写旧 schema。真实“回测实际持仓反馈优化器”的闭环仍未实现。 |
| F6-005 | 测试集流水线不覆盖通用组合权重 | 基本修复，待端到端验证 | `scripts/run_test_pipeline.py` 的 `_run_optimization()` 已写入 `test_run_dir / portfolio_weights_*.parquet`；未运行测试集流水线验证，当前也没有 `data/processed/test_run_*` 权重产物。 |
| F6-006 | 缺少组合优化/协方差测试 | 已修复 | `tests/test_optimizer.py` 存在，相关测试通过。 |

### F6-001 当前旧权重仍违规

对当前 `data/processed/portfolio_weights_optimized.parquet` 和 `portfolio_weights_meta.parquet` 抽查：

```text
L3 日期 2016-03-31:
  停牌股权重锁定违约 7 只
  上期违约停牌股权重合计约 8.66%，本期变为 0
  单股偏离超过 1% 的股票 50 只，最大偏离约 1.934%

L3 日期 2016-07-29:
  停牌股权重锁定违约 4 只
  上期违约停牌股权重合计约 3.53%，本期变为 0
  单股偏离超过 1% 的股票 50 只，最大偏离约 1.928%
```

此外，用当前 `_topn_equal_weight()` 构造极端样例：

```text
w_prev=[0.0, 0.5], 股票0涨停不可买，股票1停牌锁定，free_budget=0.5
输出 w=[0.5, 0.5]
```

股票0从 0 增至 0.5，违反涨停不可加仓。该边界未被当前 `tests/test_optimizer.py` 覆盖。

### Notebook 生产路径仍旧

`notebooks/04_portfolio_optimization.ipynb` 仍包含以下旧逻辑：

- 协方差缓存读取后只做 `not cov_df.isna().any().any()`，未调用 `validate_and_repair_covariance()`。
- 缺失协方差时仍使用 `np.eye(n) * 1e-4`。
- metadata 写入仍只有 `fallback_level`、`solver_status`、`solve_time_s`、`n_holdings`。
- 最终仍写公共 `portfolio_weights_optimized.parquet`、`portfolio_weights_baseline.parquet`、`portfolio_weights_meta.parquet`。

因此即使模块源码修了，重新运行 notebook 仍可能生成旧 schema/旧口径产物。

## 需要继续登记的未闭环项

- F5-001：受阶段 4 失效标记与缺 metadata 影响，当前信号 provenance 不完整。
- F5-002：测试集专用输出目录源码已修，但未做授权端到端验证。
- F5-004：公共合成信号仍缺 IC_IR 权重历史和 metadata。
- F5-006：notebook 自检仍能在 IC_IR 弱于等权时输出“全部通过”。
- F6-001：L3 当前产物仍违规，源码仍缺单股偏离校验和无可买候选边界处理。
- F6-002：公共权重 metadata 未重建，缺失协方差期仍显示 L1。
- F6-003：协方差缓存验证未覆盖 notebook/生产路径，缺来源与修复元数据。
- F6-004：公共 metadata 缺 `w_prev_source`，实际持仓闭环仍未实现。
- F6-005：测试集权重输出隔离未做授权端到端验证。

## 自检

- 未运行 `scripts/run_test_pipeline.py`。
- 未运行 2023-2025 测试集，未消耗剩余测试集次数。
- 所有核验均基于源码、notebook、当前训练/验证期产物和单元测试。
- 结论区分了“源码已改”“当前产物已重建”“测试集端到端已验证”三类状态。
