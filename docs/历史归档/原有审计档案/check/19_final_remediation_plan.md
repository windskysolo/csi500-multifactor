# 最终修复路线图

> 生成日期：2026-05-20  
> 依据文件：`check/13_stage0_2_fix_verification.md`、`check/15_stage3_4_fix_verification.md`、`check/16_stage5_6_fix_verification.md`、`check/17_stage6_7_fix_verification.md`、`check/18_stage9_fix_verification.md`、`check/11_attribution_report_audit.md`、`check/14_incomplete_fix_register.md`、`check/04_fix_log.md`。  
> 目的：把阶段 0-9 已审查出的未闭环问题收敛为后续最终修改顺序。  
> 测试集纪律：在本文第 A-E 阶段全部完成前，不运行 `scripts/run_test_pipeline.py`，不消耗 2023-2025 剩余测试集次数。

## 总体判断

当前项目还不能发布，也不应运行第二次测试集。

最主要的问题不是“缺少代码”，而是“源码已改、当前产物未重建、发布门禁不够硬、测试集隔离仍有漏洞”。后续修复必须按依赖顺序推进，否则很容易出现旧产物覆盖新逻辑、报告引用过期指标、测试集缓存污染训练/验证路径的问题。

优先级最高的阻断项：

1. 测试集脚本仍写公共 `fwd_ret_panel.parquet` 和公共 `cov_cache/`，必须先隔离。
2. `run_pipeline.py` 对关键产物缺失只 warning，发布门禁不硬。
3. 阶段 4 评价报告仍处于 `INVALIDATED` 状态，`final_factors.json`、`fwd_ret_panel` 缺 metadata。
4. 阶段 6 L3 fallback 仍有交易约束和单股偏离风险，当前公开权重仍违规。
5. 阶段 7 回测和阶段 8 归因产物未用当前源码重建，报告正文仍含旧指标。

## 阶段 A：发布门禁与测试集隔离先修

**目标**：先防止后续重跑产生新的污染或假阳性成功。

必须修复：

| 问题 | 文件/模块 | 动作 |
|---|---|---|
| F9-003 测试集缓存污染公共路径 | `scripts/run_test_pipeline.py` | 将测试集 `fwd_ret_panel`、协方差缓存、信号、权重、回测、归因全部写入 `data/processed/test_run_{N}/` 或 `data/processed/test_runs/run_N/` |
| F9-001 pipeline 产物缺失只 warning | `scripts/run_pipeline.py` | `_check_artifacts()` 失败时必须让阶段失败，manifest 写 `success=false` |
| F9-002 归因失败不返回非零码 | `scripts/run_attribution.py` | Brinson 或因子归因失败时退出非零码，除非显式 `--allow-partial` |
| F9-004 run-id 锁恢复语义弱 | `scripts/run_test_pipeline.py`、`check/test_set_run_log.md` | 增加结构化 ledger 或锁文件单测；`--resume-from-lock` 不得悄悄覆盖已有测试产物 |
| F9-006 manifest 不完整 | `scripts/run_pipeline.py` | manifest 增加 `config_hash`、输入 hash、输出 hash、跳过阶段、测试集运行计数 |
| F0-004 / F9-007 pytest cache 权限 | `.pytest_cache/` 或 pytest 配置 | 修复权限 warning；发布前 pytest 不应有 cache 权限噪音 |

建议新增测试：

- `tests/test_pipeline_release_gates.py`
- `tests/test_test_pipeline_isolation.py`

最低验证：

```powershell
python -m py_compile scripts\run_pipeline.py scripts\run_test_pipeline.py scripts\run_attribution.py
python -m pytest tests\test_pipeline_release_gates.py tests\test_test_pipeline_isolation.py -q --basetemp=.codex_tmp_release_gates
```

退出标准：

- 测试集脚本静态/单测确认不会写公共 `fwd_ret_panel.parquet` 和公共 `cov_cache/`。
- pipeline 关键产物缺失时返回失败。
- manifest 可追溯输入、输出、配置和测试集运行状态。

## 阶段 B：基础文档、数据与 PIT 产物闭环

**目标**：把仍冲突的项目口径和基础数据产物先统一，避免后续因子/回测继承旧口径。

必须修复：

| 问题 | 文件/模块 | 动作 |
|---|---|---|
| F1-002 行业体系脚本仍保留 CITICS 优先 | `scripts/download_tushare.py` | 明确以 SW2021 为当前行业源，修正文档字符串、日志、文件命名说明 |
| F2-004 补充数据默认非官方代理 | `scripts/download_supplement.py` | 产品化路径要求显式 `SUPPLEMENT_API_URL`，或在 README/metadata 中明确代理风险 |
| F2-005 股票代码规则残留冲突 | `CLAUDE.md` | 改为 Tushare `ts_code`，禁止仅 6 位裸代码 |
| F2-003 成分股权重 metadata 未纳入版本管理 | `data/csi500_index_weight_201601_202512.meta.md` | 纳入审查/版本管理，并增加 SHA 校验说明 |
| F2-007 PIT 三表源可用日列未进入产物 | `scripts/csv_to_parquet.py` 输出 | 重建 `financial_pit.parquet`，验证 `_pit_inc/_pit_bal/_pit_cf` |
| F0-002 工作区混杂 | git 工作区 | 按源码、测试、报告、审查文件、产物分批整理，避免把数据/cache 当源码提交 |

最低验证：

```powershell
python -m pytest tests\test_pit.py tests\test_universe.py tests\test_transaction.py -q --basetemp=.codex_tmp_base
python -m pytest tests/ -q --basetemp=.codex_tmp_all
```

数据核验：

- `financial_pit.parquet` 包含 `_pit_inc`、`_pit_bal`、`_pit_cf`。
- 任一 `_pit_*` 非空值不晚于合成 `pit_date`。
- `daily_quote.ret` 与 `close_adj` 分组 pct_change 一致。
- 完全停牌日期在 `stock_status` 中存在且 `is_suspended=True`。

退出标准：

- 基础文档口径不再互相冲突。
- PIT 源可用日列进入当前产物。
- 工作区能区分源码变更和生成产物。

## 阶段 C：因子面板与单因子评价重建

**目标**：解除阶段 4 `INVALIDATED` 状态，生成可信的最终因子清单和 forward return metadata。

必须修复：

| 问题 | 文件/模块 | 动作 |
|---|---|---|
| F3-004 预处理诊断产物缺失 | `scripts/build_factor_panels.py` | 重跑训练/验证期因子面板，生成 `factor_panel_diagnostics.parquet` |
| F4-001 评价报告失效标记未闭环 | `reports/factor_evaluation/INVALIDATED.md` | 重跑评价后更新或删除，不得保留过期事实 |
| F4-002 `final_factors.json` 缺 metadata | `scripts/run_factor_evaluation.py` 输出 | 重跑生成带 `_metadata` 的 JSON |
| F4-003 `fwd_ret_panel` 缺 sidecar | `data/processed/fwd_ret_panel.meta.json` | 用 `--recompute-fwd-ret` 重算，并严格校验 metadata |
| F4-004 shift test 当前产物旧 schema | `shift_result.csv`、报告 | 重跑生成 `lag2_ic_ir`、`lead1_ic_ir`、`diagnosis`、`lead_reverse` |
| F4-006 分组回测口径说明未进报告 | `factor_evaluation_report.md` | 报告明确原始个股等权收益、不含成本、不扣基准 |

建议命令：

```powershell
python -m scripts.build_factor_panels
python -m scripts.run_factor_evaluation --run-id valid_rebuild_YYYYMMDD --recompute-fwd-ret --no-plots
```

最低验证：

```powershell
python -m pytest tests\test_preprocess.py tests\test_factor_time_boundary.py tests\test_evaluation.py -q --basetemp=.codex_tmp_eval
python -m pytest tests/ -q --basetemp=.codex_tmp_all
```

退出标准：

- `factor_panel_diagnostics.parquet` 覆盖 27 个因子 × 84 个训练/验证调仓日。
- `final_factors.json` 有 `_metadata`，且与 `factor_summary.csv` 一致。
- `fwd_ret_panel.meta.json` 存在且校验通过。
- `INVALIDATED.md` 被删除或改为准确的历史说明。
- `tests/test_evaluation.py` 中产物一致性测试不再 skip。

## 阶段 D：信号合成与组合优化闭环

**目标**：让训练/验证期公共信号、权重、协方差和 metadata 都由当前源码生成，并满足交易约束。

必须修复：

| 问题 | 文件/模块 | 动作 |
|---|---|---|
| F5-001 合成信号 provenance 不完整 | `run_signal_combination.py` 输出 | 信号 metadata 记录最终因子、评价产物 hash、IC 序列 hash |
| F5-004 公共信号缺权重历史 | `data/processed/icir_weight_history.parquet` | 重建公共 `icir_weight_history` 和 `composite_signal_metadata.json` |
| F5-006 notebook 自检过弱 | `notebooks/03_factor_combination.ipynb` | IC_IR 弱于等权时不得输出“可交付”，改为 WARN/FAIL |
| F6-001 L3 fallback 约束未闭环 | `src/portfolio/optimizer.py` | 增加停牌锁定、涨停不加仓、跌停不减仓、单股偏离、无可买候选校验 |
| F6-002 缺失协方差伪装 L1 | `run_portfolio_optimization.py` 输出 | 重建 meta，缺协方差期 `cov_available=False` 且 `fallback_level>=1` |
| F6-003 协方差缓存缺审计 metadata | `src/portfolio/covariance.py`、`cov_cache` | 记录 `min_eig_before`、`diag_delta`、`was_repaired`、source/hash |
| F6-004 `w_prev_source` 和实际持仓闭环 | `portfolio_weights_meta.parquet` | 至少记录 `w_prev_source="target_weight"`；报告披露限制 |

必须新增或强化的测试：

- L3 无可买候选时不能给涨停不可买股票加仓。
- L3 输出必须校验单股偏离；无法满足时标记 `constraint_compliant=False`。
- 协方差缺失期不得显示 L1 `optimal`。

建议命令：

```powershell
python -m scripts.run_signal_combination --recompute-fwd-ret
python -m scripts.run_portfolio_optimization
python -m pytest tests\test_signal_combiner.py tests\test_optimizer.py -q --basetemp=.codex_tmp_signal_opt
```

退出标准：

- 公共信号有 metadata 和 IC_IR 权重历史。
- `portfolio_weights_meta.parquet` 至少包含 `cov_available`、`w_prev_source`、`constraint_compliant`。
- 历史 14 个 L3 日期不再出现停牌锁定违约、涨停加仓、跌停减仓、单股偏离违约；若无可行解，必须明确不可交付。

## 阶段 E：验证期回测、归因与报告重建

**目标**：用阶段 D 的当前权重重跑验证期回测和归因，生成不含过期指标的报告。

必须修复：

| 问题 | 文件/模块 | 动作 |
|---|---|---|
| F7-001 回测公开产物未重建 | `scripts/run_backtest.py` 输出 | 重跑验证期 NAV、metrics、trades、actual weights |
| F7-002 缺失状态 docstring 旧口径 | `src/backtest/engine.py` | docstring 改为缺失状态保守 LOCKED |
| F7-003 `n_no_price` 当前交易日志旧口径 | `backtest_trades_*.parquet` | 重跑并验证只统计持仓或目标非零股票 |
| F7-004 报告正文旧指标 | `reports/analysis_v2_results.md` | 由当前 metrics/attribution 自动生成或替换正文 |
| F8-001 归因收益 T+1 开盘口径 | `src/attribution/*` | 确认源码和测试均按 open/close 价格链计算 |
| F8-002 现金/成本拖累 | `src/attribution/*` | 保留 `weight_sum`、`cash_weight`，不得强制满仓归一化 |
| F8-003 完整基准收益 | `src/attribution/brinson.py` | 模块内部加载完整基准成分收益，不能依赖 notebook 补列 |
| F8-004/F8-005 因子列表和方向来源 | `src/attribution/factor_attr.py` | 默认读取最终因子和 `factor_summary.csv` 方向 |
| F8-007/F8-008 报告披露和 metadata | `run_attribution.py`、报告 | 写 `attribution_metadata.json`，报告列明口径、测试集次数、未修复风险 |

注意：

- 阶段 8 虽在 `check/04_fix_log.md` 中声明已修复，但当前没有独立的阶段 8 修复复核文件。进入本阶段时应补做一次 F8-001 至 F8-008 的源码、测试、产物复核。

建议命令：

```powershell
python -m scripts.run_backtest
python -m scripts.run_attribution
python -m pytest tests\test_backtest_engine.py tests\test_backtest_metrics.py tests\test_attribution.py -q --basetemp=.codex_tmp_backtest_attr
```

退出标准：

- `backtest_nav.parquet`、`backtest_metrics.parquet`、`backtest_trades_*.parquet`、`backtest_weights_*.parquet` 均由当前源码重建。
- `TradeRecord.portfolio_value_before` 随机抽查等于 T+1 开盘前组合估值。
- `n_no_price` 不再被全历史股票列污染。
- 归因每期收益与回测月度净值有 reconciliation 表。
- 报告不再保留旧 V2 IR、旧年化超额、旧达标结论。

## 阶段 F：全流程复现门禁和发布前检查

**目标**：在不触碰测试集的前提下，证明训练/验证期可复现、可追溯、可审计。

必须完成：

1. 跑训练/验证期一键 pipeline，显式记录是否跳过 quality。
2. 检查 `run_manifests/` 中 manifest 的输入/输出/config hash。
3. 全量 pytest 通过，且没有 `.pytest_cache` 权限 warning。
4. 所有报告指标都能追溯到同一批 parquet 和 manifest。
5. `check/14_incomplete_fix_register.md` 中阶段 A-E 涉及的问题更新状态。

建议命令：

```powershell
python -m scripts.run_pipeline --from-stage factors --skip quality --eval-no-plots
python -m pytest tests/ -q --basetemp=.codex_tmp_release
```

退出标准：

- 除需要真实测试集运行才能验证的 F5-002、F6-005 外，所有 Blocker/High 均关闭或明确降级为已披露限制。
- `reports/factor_evaluation/INVALIDATED.md` 不再导致产物一致性测试 skip。
- 发布报告没有过期正文。
- 当前 git 工作区能按源码、测试、报告、审查文件、产物清楚分组。

## 阶段 G：第二次测试集运行，仅在最后执行

**触发条件**：只有 A-F 全部满足后，才可以请求授权运行测试集。

运行前清单：

- `git log --oneline --grep="\[TEST_SET_RUN_" --all` 仍只有 1 条。
- `check/test_set_run_log.md` 或结构化 ledger 已预登记 Run #2。
- `scripts/run_test_pipeline.py` 已确认不会写公共 `fwd_ret_panel.parquet`、公共 `cov_cache/`、公共信号、公共权重、公共回测产物。
- `data/processed/test_run_2/` 不存在，或存在受审计的 `RUN_STARTED.json` 并有明确恢复方案。
- 当前训练/验证报告和 manifest 已归档。

授权后命令：

```powershell
python -m scripts.run_test_pipeline --run-id 2
```

运行后必须立即做：

- 保存 `data/processed/test_run_2/RUN_STARTED.json`、`RUN_FINISHED.json` 和所有测试集产物。
- 更新 `check/test_set_run_log.md`。
- 生成测试集专项报告，单独标注这是第 2 次且最后一次测试集运行。
- commit message 必须包含 `[TEST_SET_RUN_2]`。

## 最终关闭清单

发布或提交最终报告前，逐项确认：

- [ ] 测试集运行次数仍符合上限，未发生无记录重复运行。
- [ ] 训练/验证期所有产物最大日期不超过 `VALID_END=2022-12-31`。
- [ ] 测试集产物只存在于 test run 专用目录。
- [ ] 因子评价 JSON、CSV、forward return metadata、信号 metadata、权重 metadata、回测指标、归因 metadata 可相互追溯。
- [ ] L3 fallback 的交易状态约束和单股偏离约束有测试覆盖。
- [ ] T+1 开盘成交、成本、印花税切换、缺失状态 LOCKED 有测试覆盖。
- [ ] 报告指标全部来自当前 parquet，不再手写旧数值。
- [ ] `python -m pytest tests/ -q` 通过且无权限 warning。
- [ ] `git ls-files "*__pycache__*" "*.pyc"` 无输出。
- [ ] `git status --short` 中无意外数据、cache、pyc 产物。

## 自检

- 未运行测试集，也未建议在 A-F 完成前运行测试集。
- 本文件只给出最终修复路线和验收标准，未修改业务源码。
- 阶段 8 因缺少独立修复复核，按“修复日志声明已改但仍需复核和重建产物”处理。
- 所有收益、IR、回撤相关内容均只作为旧报告不可信的证据，不作为新结论。
