# 阶段 A/B 完成情况详细核验

> 核验日期：2026-05-20  
> 核验对象：`check/19_final_remediation_plan.md` 中阶段 A（发布门禁与测试集隔离）和阶段 B（基础文档、数据与 PIT 产物闭环）。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未消耗剩余测试集次数；未重建全流程训练/验证产物。  

## 总结论

阶段 A/B **尚未全部正确完成**。

阶段 A 的核心修复有明显进展：测试集 forward return 和新增协方差缓存已经改为写入 `test_run_{N}`，pipeline 产物缺失会失败，归因脚本失败会返回非零码，且新增的阶段 A 专项测试通过。但 A 仍未完全闭环：pytest cache 目录 `.codex_tmp_pytest_cache` 当前不可访问，导致每次 pytest 仍有 `PytestCacheWarning`；同时 run manifest 逻辑虽已增强，但当前尚未生成实际 manifest 产物。

阶段 B 的数据产物部分已明显改善：`financial_pit.parquet` 已包含 `_pit_inc/_pit_bal/_pit_cf`，且三个源 PIT 日期均不晚于合成 `pit_date`；`CLAUDE.md` 股票代码规则已改为 Tushare `ts_code`；成分股 metadata 已进入 git index。但 B 仍有两个关键未闭环：`download_tushare.py` 现在输出 `industry_sw2021.csv`，而 `csv_to_parquet.py` 仍读取 `industry_citics.csv`，原始数据重建链路会断；`download_supplement.py` 仍默认非官方代理地址，README 没有同步披露或要求显式配置。

## 阶段 A 逐项核验

| 项目 | 结论 | 证据 |
|---|---|---|
| F9-003 测试集 forward return 隔离 | 基本完成 | `run_test_pipeline.py:66` 只读公共 `PUBLIC_FWD_CACHE`；`run_test_pipeline.py:171-172` 将扩展结果写入 `output_dir/fwd_ret_panel.parquet`；专项测试通过 |
| F9-003 测试集协方差缓存隔离 | 基本完成，有小残留 | `run_test_pipeline.py:341-354` 使用 `output_dir/cov_cache` 写测试期新缓存，`run_test_pipeline.py:382` 写入 `test_cache`；但 `run_test_pipeline.py:69` 仍会在 import 时 `mkdir` 公共 `cov_cache` 目录 |
| F9-001 pipeline 产物缺失硬失败 | 完成 | `run_pipeline.py:237-238` 关键产物缺失时返回 False；`tests/test_pipeline_release_gates.py` 覆盖该行为 |
| F9-002 归因失败非零退出 | 完成 | `run_attribution.py:157-158`、`run_attribution.py:181-182` 在未传 `--allow-partial` 时 `sys.exit(1)` |
| F9-004 run-id 锁与 resume | 基本完成，待真实运行验证 | `run_test_pipeline.py:703-753` 有锁文件和重复运行拦截；`run_test_pipeline.py:810-855` resume 时按哨兵文件跳过步骤；专项测试通过 |
| F9-006 manifest 字段增强 | 源码完成，产物待生成 | `run_pipeline.py:305-340` 已写 `config_hash`、`input_hashes`、`output_hashes`、`test_set_run_count` 等字段；但本次未运行 pipeline，`run_manifests/` 尚未实际验证 |
| F0-004/F9-007 pytest cache 权限 | 未完成 | `pytest.ini:3` 指向 `.codex_tmp_pytest_cache`，但该目录当前访问被拒绝；pytest 仍出现 `PytestCacheWarning` |

### 阶段 A 验证命令

```text
python -m py_compile scripts\run_pipeline.py scripts\run_test_pipeline.py scripts\run_attribution.py scripts\download_tushare.py scripts\download_supplement.py scripts\csv_to_parquet.py
# 通过

python -m pytest tests\test_pipeline_release_gates.py tests\test_test_pipeline_isolation.py -q --basetemp=.codex_tmp_stage_ab_a
# 15 passed, 1 warning

python -m pytest tests/ -q --basetemp=.codex_tmp_stage_ab_all
# 200 passed, 6 warnings
```

warning 关键内容：

```text
PytestCacheWarning: could not create cache path
E:\Acoding\Project\500\.codex_tmp_pytest_cache\v\cache\nodeids:
[WinError 5] 拒绝访问。
```

并且：

```text
Get-Acl .codex_tmp_pytest_cache
# UnauthorizedAccessException

Get-ChildItem -Force .codex_tmp_pytest_cache
# 访问被拒绝
```

### 阶段 A 仍需修复

1. 处理 `.codex_tmp_pytest_cache` 权限，或把 pytest cache 指向一个确实可写且可清理的路径；退出标准是 pytest 不再出现 cache warning。
2. 可选收紧：`run_test_pipeline.py` 不应在 import 时创建公共 `cov_cache` 目录，公共路径应完全只读。
3. 至少运行一次不触碰测试集的训练/验证 pipeline，生成并检查 `run_manifests/run_*.json`，确认 manifest 字段真实落盘。

## 阶段 B 逐项核验

| 项目 | 结论 | 证据 |
|---|---|---|
| F1-002 行业体系统一 SW2021 | 部分完成，存在新断链 | `download_tushare.py` 已改为 SW2021 并输出 `industry_sw2021.csv`；但 `csv_to_parquet.py:458` 仍读取 `raw/industry_citics.csv` |
| F2-004 补充数据非官方代理 | 部分完成 | `download_supplement.py:55-61` 仍默认 `http://tsdata.siboer.xin/1wan`，仅在脚本注释和日志中警告；README 未披露 `SUPPLEMENT_API_URL` |
| F2-005 股票代码规则 | 完成 | `CLAUDE.md:66` 已改为 Tushare `ts_code`，与 `AGENTS.md`/计划一致 |
| F2-003 成分股权重 metadata | 基本完成 | `data/csi500_index_weight_201601_202512.meta.md` 已加入 git index；SHA256、期数、每期 500 股、权重合计范围与 CSV 一致 |
| F2-007 PIT 三表源可用日列 | 完成 | 当前 `financial_pit.parquet` 包含 `_pit_inc/_pit_bal/_pit_cf`；三列非空值均不晚于索引 `pit_date` |
| F0-002 工作区混杂 | 未完成 | `git status --short` 仍混合文档、源码、notebook、报告、测试、审查文件和 pyc 删除记录 |

### 阶段 B 数据核验

`financial_pit.parquet`：

```text
exists True
shape (56744, 14)
index_names ['pit_date', 'ts_code', 'end_date']
pit_cols ['_pit_inc', '_pit_bal', '_pit_cf']
pit_checks {'_pit_inc': True, '_pit_bal': True, '_pit_cf': True}
```

`daily_quote.parquet` 后复权收益：

```text
daily_quote_exists True
daily_shape (2692934, 2)
ret_max_abs_diff 0.0
ret_nan_rate 0.0004693765
```

成分股权重 metadata：

```text
sha256 d3294c0b70cd3c9fbc1b6658ef6a8e60930c407fe2b1628453d4c4b0fe9382b4
shape (60000, 4)
dates 120
count_minmax (500, 500)
weight_sum_minmax (99.984, 100.019)
```

基础测试：

```text
python -m pytest tests\test_pit.py tests\test_universe.py tests\test_transaction.py -q --basetemp=.codex_tmp_stage_ab_b
# 35 passed, 1 warning
```

## 关键残留问题

### 1. 行业下载与转换脚本文件名不一致

这是阶段 B 当前最重要的问题。

当前下载脚本：

```text
scripts/download_tushare.py:363 输出文件：industry_sw2021.csv（旧文件 industry_citics.csv 不再生成）
scripts/download_tushare.py:370 out = RAW_DIR / "industry_sw2021.csv"
```

当前转换脚本：

```text
scripts/csv_to_parquet.py:446 将 raw/industry_citics.csv（实为 SW2021）展开为每日行业归属
scripts/csv_to_parquet.py:458 ind = pd.read_csv(cfg.DATA_RAW / "industry_citics.csv", encoding="utf-8")
```

当前 raw 目录实际只有旧文件：

```text
data/raw/industry_citics.csv
data/raw/industry_classify_SW2021.csv
```

影响：

- 如果从空 raw 目录按新下载脚本重建，只会生成 `industry_sw2021.csv`，`csv_to_parquet.py industry` 会找不到 `industry_citics.csv`。
- 如果当前目录继续保留旧 `industry_citics.csv`，转换会继续使用旧文件，不能证明新下载链路已统一。

建议修复：

- 优先方案：`csv_to_parquet.py` 改为读取 `industry_sw2021.csv`，并兼容旧 `industry_citics.csv` 时明确 warning。
- 同步更新 `src/config.py` 中仍写 `industry_citics.csv` 的历史注释。
- 增加一个小测试或静态检查，确保下载输出名与转换输入名一致。

### 2. 补充数据代理仍未产品化闭环

当前脚本有风险说明，但默认值仍是非官方代理，README 没有同步说明。按阶段 B 退出标准，这还不能算完全完成。

建议二选一：

- 保守产品化：移除默认代理，缺少 `SUPPLEMENT_API_URL` 时直接失败。
- 学习/本地模式：保留默认代理，但 README 和 run manifest 必须显式记录“使用非官方代理，结果依赖该代理可用性和数据完整性”。

### 3. pytest cache 权限未解决

`.codex_tmp_pytest_cache` 当前目录存在但访问被拒绝，导致 pytest warning。仅新增 `pytest.ini` 没有解决权限问题。

建议：

- 删除或修复 `.codex_tmp_pytest_cache` ACL。
- 或改用一个确定可写的 cache 目录。
- 复跑 `python -m pytest tests/ -q --basetemp=...`，确认无 `PytestCacheWarning`。

## 最终判断

阶段 A：**部分完成**。核心测试集隔离和 pipeline 硬门禁已基本完成，但 pytest cache 权限与实际 manifest 产物仍未闭环。

阶段 B：**部分完成**。PIT 数据产物、股票代码文档、成分股 metadata 已完成；但行业下载/转换文件名不一致、补充数据默认代理、工作区混杂仍未完成。

## 自检

- 未运行测试集流水线，未消耗测试集次数。
- 已区分“源码已修”“当前产物已修”“测试已覆盖”“发布环境仍有 warning”四类状态。
- 本次没有修改业务源码，只新增本核验报告并将未闭合点继续登记。
