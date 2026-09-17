# 阶段 3-4 修复核验记录

> 核验日期：2026-05-20  
> 核验对象：`check/04_fix_log.md` 中“修复批次 3 / 修复批次 4”。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未重建因子面板，未重跑单因子评价生产脚本。  
> 后续未闭环问题同步登记在 `check/14_incomplete_fix_register.md`。

## 核验结论

阶段 3-4 **尚未完全闭环**。

源码层和测试层已有明显修复：阶段 3 的测试集扩展防护、预处理/时间边界测试、阶段 4 的 forward return 对齐测试、shift test 接口和报告生成逻辑均已加入。当前 `python -m pytest tests/ -q` 为 `82 passed, 2 skipped`。

但当前落盘产物仍不是可发布状态：

- `data/processed/factor_panel_diagnostics.parquet` 不存在，F3-004 的审计产物未生成。
- `reports/factor_evaluation/INVALIDATED.md` 仍存在，当前评价报告仍被标记为失效。
- `reports/factor_evaluation/final_factors.json` 缺少 `_metadata`。
- `data/processed/fwd_ret_panel.meta.json` 不存在。
- 当前 `shift_result.csv` 仍是旧 schema，仅含 `orig_ic_ir / lagged_ic_ir / ic_ir_drop / warning`，没有 `lag2_ic_ir / lead1_ic_ir / diagnosis / lead_reverse`。
- 当前 `factor_evaluation_report.md` 仍保留旧表述“所有因子时间错位测试正常（无未来函数疑点）”，也没有新增的分组收益口径说明。

## 核验命令摘要

```powershell
python -m pytest tests\test_preprocess.py tests\test_factor_time_boundary.py tests\test_evaluation.py -q
python -m pytest tests/ -q
python -m scripts.build_factor_panels --end-date 2025-12-31 --dry-run
rg -n "DIAGNOSTICS_PATH|allow-test-set|run-id|factor_panel_diagnostics" scripts\build_factor_panels.py
rg -n "run_id|final_factors|fwd_ret_panel.meta|diagnosis|lead_reverse" scripts\run_factor_evaluation.py src\evaluation\shift_test.py tests\test_evaluation.py
```

## 阶段 3 逐项核验

| 编号 | 修复日志声明 | 当前核验状态 | 证据与说明 |
|---|---|---|---|
| F3-001 | 量价因子继承错误 `daily_quote.ret` 口径 | 基本修复，保留审计意见 | 当前 `daily_quote.ret` 与 `close_adj` 分组 pct_change 的最大差异为 `0.0`。`vol_60d / ivol_60d / max_ret / amihud` 面板覆盖 `2016-01-29` 至 `2022-12-30`，未扩展到测试期。但缺少本次重建的 run metadata 和预处理诊断产物，审计闭环依赖 F3-004。 |
| F3-002 | 因子构建脚本增加测试集纪律防护 | 基本修复，继承 run-id 本地防重复弱点 | `scripts/build_factor_panels.py` 已有 `--allow-test-set`、`--run-id`、`factor_panels_test_run_{N}`。执行 `python -m scripts.build_factor_panels --end-date 2025-12-31 --dry-run` 返回错误并拒绝越过 `VALID_END=2022-12-31`。但 run-id 仍主要依赖 git 历史计数，本地防重复问题已在 F0-003/F9-004 登记。 |
| F3-003 | 新增预处理和时间边界单元测试 | 已修复 | `tests/test_preprocess.py`、`tests/test_factor_time_boundary.py` 存在；与评价测试合并运行结果为 `47 passed, 2 skipped`。 |
| F3-004 | 输出因子面板预处理诊断产物 | 部分修复 | 源码已加入 `DIAGNOSTICS_PATH` 和诊断字段落盘逻辑，但当前 `data/processed/factor_panel_diagnostics.parquet` 不存在。说明修复后尚未重跑因子面板，或当前产物没有被诊断文件覆盖。 |
| F3-005 | 因子数量文档与实现一致 | 已修复 | `financial_factors.py` 已写 15 个财务因子，`price_factors.py` 已写 12 个量价因子并包含 `short_ratio`；`build_factor_panels.py` 也标注全部 27 个因子。 |

## 阶段 4 逐项核验

| 编号 | 修复日志声明 | 当前核验状态 | 证据与说明 |
|---|---|---|---|
| F4-001 | 标记当前单因子评价报告失效 | 部分修复 | `reports/factor_evaluation/INVALIDATED.md` 存在，能阻止误用。但该文件仍写“daily_quote.parquet 尚未重建”和“CSV 18 个 vs JSON 12 个”，与当前核验事实不完全一致：当前 `daily_quote.ret` 已修复，且 CSV/JSON 最终因子集合同为 12 个。当前评价报告仍未用新评价脚本重跑，失效标记未解除。 |
| F4-002 | `factor_summary.csv` 与 `final_factors.json` 一致性断言和 metadata | 部分修复 | 源码已在写 JSON 前加入一致性断言并写 `_metadata`。当前落盘 `factor_summary.csv` 与 `final_factors.json` 的最终因子集合同为 12 个，但 JSON 仍只有 `final_factors / excluded / stability_weights`，没有 `_metadata`。由于 `INVALIDATED.md` 存在，产物一致性测试当前跳过。 |
| F4-003 | forward return 缓存增加 sidecar metadata | 部分修复 | 源码已支持写 `fwd_ret_panel.meta.json` 并校验 `rebalance_dates_hash / benchmark_mode`。当前 `data/processed/fwd_ret_panel.parquet` 存在，但 `data/processed/fwd_ret_panel.meta.json` 不存在；现有缓存仍无法校验。源码在 sidecar 缺失时只是 warning 后继续加载旧缓存，产品级闭环仍偏弱。 |
| F4-004 | 强化 shift test，报告措辞更保守 | 部分修复 | `src/evaluation/shift_test.py` 与 `tests/test_evaluation.py` 已有 `diagnosis / lead_reverse`。但当前 `reports/factor_evaluation/shift_result.csv` 仍是旧列，当前报告仍写“无未来函数疑点”，说明报告未用新逻辑重跑。 |
| F4-005 | 新增单因子评价层单元测试 | 基本修复 | `tests/test_evaluation.py` 存在，全量测试为 `82 passed, 2 skipped`。两个跳过项来自 `INVALIDATED.md`，需在评价产物重建并解除失效后转为真实执行。 |
| F4-006 | 分组回测报告补充原始收益口径说明 | 部分修复 | 源码报告模板已有口径说明，但当前 `factor_evaluation_report.md` 未重新生成；第 4 节仍直接进入“5分组回测摘要（训练集，不含成本）”，未显示新增的“原始个股等权收益 / 不含交易成本 / 不扣减基准”说明。 |

## 当前需要继续闭环的问题

1. 重跑阶段 3 因子面板构建，生成 `data/processed/factor_panel_diagnostics.parquet`，并确认诊断行覆盖 27 个因子 × 84 个训练/验证调仓日。
2. 用新脚本重跑阶段 4 单因子评价，建议显式传入 `--run-id` 和 `--recompute-fwd-ret`，生成新的 `fwd_ret_panel.meta.json` 与带 `_metadata` 的 `final_factors.json`。
3. 重跑后确认 `shift_result.csv` 含 `lag2_ic_ir / lead1_ic_ir / diagnosis / lead_reverse`，报告中不再出现“无未来函数疑点”这种过强表述。
4. 重跑后确认报告第 4 节包含分组收益口径说明。
5. 核验通过后再删除或更新 `reports/factor_evaluation/INVALIDATED.md`；删除前不应将当前 `reports/factor_evaluation/*` 作为可信入选因子依据。

## 自检

- 未运行测试集管线，未消耗 2023-2025 测试集次数。
- 本次只读核验数据和产物；未重建 raw/processed/factor/evaluation 产物。
- 已运行单元测试：`82 passed, 2 skipped`；仍有 `.pytest_cache` 权限 warning，该问题已在阶段 0 未闭环登记中记录。
