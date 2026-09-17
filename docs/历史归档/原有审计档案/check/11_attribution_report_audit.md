# 阶段 8：归因与报告审查

> 审查日期：2026-05-20  
> 审查依据：`check/00_review_requirements.md`、`check/02_full_project_review_plan.md`  
> 审查范围：`src/attribution/brinson.py`、`src/attribution/factor_attr.py`、`notebooks/06_attribution.ipynb`、`reports/analysis_v2_results.md`、`data/processed/*attr*.parquet`、`data/processed/brinson_*.parquet`、归因图表  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未重建归因产物，未修改业务代码。

## 阶段结论

不通过。

当前归因模块已经实现了 BHB 行业归因和 WLS 因子归因，且落盘结果内部会计恒等式可以通过。但归因收益口径与回测净值口径不一致，模块仍继承阶段 2 的 `daily_quote.ret` 收益口径问题，策略实际权重被重新归一化导致现金和成本拖累被抹掉，Brinson 模块还依赖 notebook 外部补齐 CSI500 成分股列才能避免基准收益被错算。因子归因使用的是 28 个默认候选因子组，而不是当前组合真实使用的 12 个最终因子，并且方向来源继承了 `FACTOR_DIRECTIONS` 的硬编码错误。

修复前，`brinson_summary_v2.parquet`、`factor_summary_v2.parquet` 和 `06_attribution.ipynb` 中的归因解释只能作为探索性草稿，不能作为产品级报告结论。

## 只读抽查摘要

- 当前归因区间：验证集 2022，`period_start` 从 `2021-12-31` 到 `2022-11-30`，共 12 期。
- `brinson_summary_v2.parquet`：12 行，V2 算术超额加总 `+1.92%`。
- `factor_summary_v2.parquet`：12 行，平均 WLS R² 约 `0.0627`，每期有效股票数 425-441。
- 当前 `backtest_nav.parquet` 计算的 V2 期末超额净值约 `+1.01%`；Brinson 几何链接超额约 `+2.74%`，算术加总超额 `+1.92%`，三者不一致。
- `backtest_weights_v2.parquet` 日度股票权重行和最小约 `0.975`，均值约 `0.997`；归因模块会把每期股票权重强制归一到 1。
- 当前 `reports/analysis_v2_results.md` 仍写 V2 IR `1.025`、年化超额 `+5.35%`，与最新回测产物不一致，且没有纳入本阶段归因产物。

## F8-001 归因收益口径没有按 T+1 开盘成交口径计算，且继承 `daily_quote.ret` 上游收益问题

**严重度**：Blocker

**证据**：
- `src/attribution/brinson.py:324-325` 使用 `dq["ret"].unstack("ts_code")` 作为股票日收益。
- `src/attribution/factor_attr.py:431-432` 同样使用 `dq["ret"].unstack("ts_code")`。
- `src/attribution/brinson.py:187-193` 和 `src/attribution/factor_attr.py:207-213` 用 `(T, T_next]` 的日收益几何复利计算持有期收益。
- 阶段 2 已确认 `daily_quote.ret` 口径存在污染风险，不能保证等于基于 `close_adj` 的后复权收益。
- 回测要求是 T 日盘后生成权重、T+1 开盘成交；当前归因的 `(T, T_next]` close-to-close 日收益包含了 `T close -> T+1 close`，没有剥离 `T close -> T+1 open` 的隔夜段。

**影响**：
归因收益与真实回测成交路径不一致。策略在 T+1 开盘才建仓，不应获得 T 收盘到 T+1 开盘的隔夜收益；而当前归因把该段混入持有期收益。再叠加 `daily_quote.ret` 上游收益口径问题，行业收益、因子收益、选股效应和残差都可能失真。

**修复建议**：
1. 修复并重建 `daily_quote.ret`：明确用 `close_adj.pct_change()` 生成后复权收盘收益，旧产物全部作废。
2. 归因持有期收益改为与回测一致的价格链：首日使用 `close_adj(T+1) / open_adj(T+1) - 1`，中间用 close-to-close，期末到 `T_next close`。
3. 对停牌无开盘价的股票按回测实际成交状态处理，不能简单用 0 收益替代所有缺口。

**验证方式**：
- 新增归因收益对齐测试：构造单股票 T+1 大幅跳空但开盘后不涨的样例，归因收益应为 0，而不是 close-to-close 的跳空收益。
- 修复后比较每期归因策略收益与回测净值月收益，差异必须能由现金、成本和几何链接解释。

## F8-002 归因强制把实际股票权重归一到 100%，抹掉现金和成本拖累

**严重度**：High

**证据**：
- `src/attribution/brinson.py:109-115`：取 `actual_weights` 中 T 后首个可用日期后，执行 `return w / w_sum`。
- `src/attribution/factor_attr.py:156-162`：同样把股票权重重新归一化。
- `src/backtest/engine.py:488` 记录的是“股票市值 / 组合总净值”，不含现金列。
- 当前 `backtest_weights_v2.parquet` 行和最小约 `0.975`，均值约 `0.997`，说明存在现金或未投满头寸。
- 当前 V2 归因超额与净值超额不一致：
  - Brinson 算术加总超额约 `+1.92%`。
  - Backtest 期末超额净值约 `+1.01%`。

**影响**：
归因解释的是“把实际股票持仓重新满仓后的毛收益”，不是回测净值收益。交易成本、现金拖累、无法成交造成的未投满、卖出后未买足等执行影响会被移入残差之外或直接消失。报告中说残差“含成本差异”也不准确，因为成本已经在权重归一化时大部分被抹掉。

**修复建议**：
1. `actual_weights` 保留原始行和，不要默认归一化；新增现金列或 `cash_weight = 1 - stock_weight_sum`。
2. Brinson 和因子归因分别输出 gross attribution 与 net attribution，并明确口径。
3. 若仍做满仓股票归因，应在字段名和报告中写成 `gross_stock_attribution`，不得与回测 NAV 指标直接比较。

**验证方式**：
- 构造持有 50% 股票、50% 现金的样例，归因策略收益必须为股票收益的一半，而不是股票满仓收益。
- 每期归因 `strategy_return` 与回测月度净值收益差异需要落盘为 reconciliation 表。

## F8-003 Brinson 模块自身未加载完整基准成分股收益，依赖 notebook 外部增广

**严重度**：High

**证据**：
- `src/attribution/brinson.py:320-324` 只从 `actual_weights.columns` 中收集 `strategy_codes` 并加载这些股票的日收益。
- `src/attribution/brinson.py:364-368` 后续归因时 `universe_codes = w_p ∪ w_b`，包含完整基准成分股。
- `src/attribution/brinson.py:188-194` 对不在 `daily_ret_panel.columns` 的股票收益填 0。
- `notebooks/06_attribution.ipynb:144-147` 明确注释：如果不把所有 CSI500 成分股补为 0 权重列，未持有的基准成分股收益会被错误补 0。

**影响**：
`compute_brinson_attribution()` 作为 `src/` 生产模块，单独调用时会把未持有的基准成分股收益当作 0，直接扭曲基准行业收益、配置效应、选股效应和总超额。notebook 的外部补救不能替代模块级正确性。

**修复建议**：
1. 在 `compute_brinson_attribution()` 内部根据所有归因期的 `index_member` 自动收集完整基准成分股，并加载其收益。
2. `_compute_period_cumulative_returns()` 对归因 universe 中缺失收益的股票不应静默填 0；应记录 `n_missing_return`，超过阈值时报错。
3. notebook 删除外部增广逻辑，避免两套口径。

**验证方式**：
- 新增测试：传入只持有 1 只股票的 `actual_weights`，Brinson 基准收益仍必须等于完整 CSI500 成分股收益，而不是接近 0。

## F8-004 因子归因使用 28 个默认候选因子，而非组合真实使用的最终因子

**严重度**：High

**证据**：
- `src/attribution/factor_attr.py:53-89` 的 `DEFAULT_FACTOR_GROUPS` 覆盖 28 个候选因子。
- 当前 `reports/factor_evaluation/final_factors.json` 的 `final_factors` 为 12 个。
- 只读抽查显示 `DEFAULT_FACTOR_GROUPS` 相比最终因子额外包含 16 个因子：`accrual`、`bp`、`dy_ttm`、`fcfp`、`ivol_60d`、`large_net_inflow`、`leverage`、`max_ret`、`mom_6_1`、`ret_1m`、`roa`、`roe`、`roe_delta`、`short_ratio`、`sp_ttm`、`vol_60d`。
- `src/attribution/factor_attr.py:412-414` 会加载 `factor_group_map` 中所有可用因子面板，而不是读取本次组合真实使用的因子清单。

**影响**：
当前因子归因解释的是“候选风格因子对组合超额的事后解释”，不是“本策略真实 alpha 输入的贡献”。这两者可以都做，但必须分开命名。把 28 个候选因子的贡献解释为 V2 策略因子贡献，会误导读者，尤其是其中一些因子在前序审查中已被标记为受收益口径污染或未入选。

**修复建议**：
1. 因子归因默认读取 `final_factors.json` 或组合信号 metadata，使用真实入模因子。
2. 如果保留 28 因子风格解释，应命名为 `style_factor_exposure_attribution`，并明确它不是 alpha 因子贡献。
3. 输出 metadata：`factor_list_source`、`factor_names`、`group_map_hash`。

**验证方式**：
- 单元测试：当 `final_factors.json` 仅含两个因子时，默认归因不得加载其他候选因子。

## F8-005 因子方向使用硬编码 `FACTOR_DIRECTIONS`，与评估产物方向不一致

**严重度**：High

**证据**：
- `src/attribution/factor_attr.py:44` 从 `src.signal.combiner` 导入 `FACTOR_DIRECTIONS`。
- `src/attribution/factor_attr.py:272-273` 用 `FACTOR_DIRECTIONS.get(factor_name, 1)` 调整因子方向。
- `src/signal/combiner.py:77` 将 `margin_ratio` 硬编码为 `+1`。
- 当前 `reports/factor_evaluation/factor_summary.csv` 中 `margin_ratio` 的 `factor_direction=-1.0`。

**影响**：
资金流向因子组暴露和贡献会被方向错误污染。更一般地，归因方向应来自同一次因子评价/信号构建产物，而不是一个可能过期的手工表；否则报告无法追溯“为什么某个因子组是正暴露或负暴露”。

**修复建议**：
1. 从 `factor_summary.csv` 或 `final_factors.json` 读取 `factor_direction`，作为唯一方向来源。
2. 对方向缺失的因子直接报错或从归因中排除，不要默认 `+1`。
3. 将方向表写入归因 metadata。

**验证方式**：
- 构造 `margin_ratio` 方向为 `-1` 的样例，确认归因暴露符号与评估产物一致。

## F8-006 归因 notebook 自检把“超额为正”当作 PASS，且引用过期回测结果

**严重度**：Medium

**证据**：
- `notebooks/06_attribution.ipynb:405` 注释写“已知 backtest 结果 5.35% 超额，Brinson 应一致”。
- `notebooks/06_attribution.ipynb:407` 只检查 `v2_excess_sum > 0`，并输出 PASS。
- 当前 `backtest_metrics.parquet` 的 V2 年化超额约 `+1.47%`，不是报告中旧的 `+5.35%`。
- 当前 Brinson 加总超额 `+1.92%` 与 backtest 期末超额净值 `+1.01%` 并不一致。

**影响**：
自检门槛方向错了。归因自检不应奖励“收益为正”，而应检查口径一致性、输入版本一致、与回测净值可解释地对齐。当前 PASS 会掩盖 F8-001 和 F8-002 这类核心口径问题。

**修复建议**：
1. 删除“超额为正”门槛，改为 reconciliation 检查。
2. 自检读取最新 `backtest_nav.parquet` 和 `backtest_metrics.parquet`，比较同一期间的策略、基准、超额收益。
3. 如果口径不同，必须输出 WARN/FAIL，并记录差异来源。

**验证方式**：
- 构造归因为正但回测净值为负的样例，自检应失败。

## F8-007 报告未满足阶段 8 的风险披露和样本说明要求

**严重度**：High

**证据**：
- `reports/analysis_v2_results.md` 生成日期为 2026-05-11，当前回测和归因产物更新时间为 2026-05-19。
- 该报告仍写 V2 IR `1.025`、年化超额 `+5.35%`，与当前 `backtest_metrics.parquet` 不一致。
- 报告没有纳入 `brinson_summary_v2.parquet`、`factor_summary_v2.parquet` 的归因结论。
- 阶段 8 要求检查 `2024` 小微盘危机，但当前归因 notebook 和通用报告只覆盖验证集 2022，没有 2024-2025 分析。
- 报告没有列出测试集运行次数记录，也没有说明当前测试集只剩 1 次机会。

**影响**：
报告无法作为当前研究状态的可信出口。它既没有反映最新产物，也没有暴露阶段 2-7 已确认的阻断问题，还缺少测试集纪律、归因局限性和 2024-2025 专项讨论。

**修复建议**：
1. 在修复阶段 2-7 Blocker 前，将该报告标记为“历史报告/不可用于当前结论”。
2. 新报告必须由脚本或 notebook 从当前产物自动生成关键指标，避免手工数值漂移。
3. 报告必须列明样本期、基准、成本、滑点、测试集运行次数、未修复风险、统计显著性和归因口径。
4. 仅在获得授权并消耗第 2 次测试集运行后，才能加入 2023-2025 和 2024 小微盘危机归因讨论。

**验证方式**：
- 报告生成后自动比对 `backtest_metrics.parquet`、归因 parquet 和 `check/test_set_run_log.md`。
- 若指标不一致或缺少测试集运行记录，报告构建失败。

## F8-008 缺少归因层单元测试与复现 metadata

**严重度**：High

**证据**：
- 当前 `tests/` 下没有归因测试文件。
- 归因产物只落盘 parquet 和 png，没有记录输入 `backtest_weights`、`daily_quote`、`factor_panels`、`final_factors`、代码 commit 或文件 hash。

**影响**：
归因很容易被输入版本漂移污染。当前已出现报告和产物不一致、归因和回测净值口径不一致的问题，缺少测试和 metadata 会使后续难以定位差异来源。

**修复建议**：
1. 新增 `tests/test_attribution.py`，至少覆盖 Brinson 恒等式、完整基准收益加载、T+1 开盘收益对齐、现金权重处理、因子列表来源。
2. 归因落盘时写 `attribution_metadata.json`，记录输入文件、样本期、收益口径、因子列表、方向来源、是否 net/gross。

**验证方式**：
- `python -m pytest tests/test_attribution.py -q` 通过。
- 任意归因产物都能通过 metadata 追溯到同一批输入文件。

## 阶段 8 通过项与保留意见

- Brinson 与因子归因的内部会计恒等式当前可以通过。
- 归因 notebook 明确只使用验证集 2022，未触碰 2023-2025 测试期。
- Notebook 已识别 Brinson 需要完整 CSI500 成分股收益，并做了外部补列补救；但该修复应下沉到 `src/attribution/brinson.py`。
- 因子归因使用 T 日因子暴露解释后续收益，未发现同期间未来因子暴露进入信号的证据；但它是事后归因，不应被解释为预测有效性。
- 阶段 8 整体仍不通过，主要原因是归因与回测口径不一致、因子归因对象不等于真实入模因子、报告 stale 且风险披露不足。

