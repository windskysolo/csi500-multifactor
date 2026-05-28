# 阶段 9 修复复核：复现、测试与发布前清单

> 复核日期：2026-05-20  
> 复核范围：`check/04_fix_log.md` 中“修复批次 9”、`check/12_repro_test_release_checklist.md`、`scripts/run_pipeline.py`、`scripts/run_test_pipeline.py`、`scripts/run_signal_combination.py`、`scripts/run_portfolio_optimization.py`、`scripts/run_backtest.py`、`scripts/run_attribution.py`、`README.md`、`tests/`、当前 git 与产物状态。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未消耗第二次测试集机会；未重建 raw/processed/信号/组合/回测/归因产物。

## 总结论

阶段 9 尚未完全闭环。

已确认的进展：

- `README.md` 的下载和转换命令已改为带 `all`/模块参数。
- `scripts/run_pipeline.py` 已覆盖 `download_core`、`download_supplement`、`convert`、`quality`、`factors`、`evaluate`、`signal`、`portfolio`、`backtest`、`attribution`。
- 新增训练/验证期脚本：`run_signal_combination.py`、`run_portfolio_optimization.py`、`run_backtest.py`、`run_attribution.py`。
- 测试集脚本已把信号、权重、回测输出迁到 `data/processed/test_run_{N}/`。
- `git ls-files "*__pycache__*" "*.pyc"` 当前无输出。
- 全量单元测试通过：`178 passed, 2 skipped`。

仍未闭环的核心问题：

- `run_pipeline.py` 的产物检查只是 warning；关键产物缺失时仍可能写入 `success=true` 的 manifest。
- 测试集脚本仍会写公共 `data/processed/fwd_ret_panel.parquet` 和公共 `data/processed/cov_cache/`，F9-003 的“完全隔离”未达成。
- `run_attribution.py` 捕获归因异常后不退出非零码，流水线可能把失败的归因阶段当作成功。
- run manifest 只记录阶段和 commit，缺输入 hash、输出 hash、配置 hash、测试集运行计数；当前也尚未生成 `data/processed/run_manifests/`。
- `.pytest_cache` 权限 warning 仍存在，发布卫生未完全清理。

## 逐项状态

| 编号 | 修复声明 | 复核状态 | 关键证据 |
|---|---|---|---|
| F9-001 | 一键复现链路已修复 | 部分修复 | `run_pipeline.py` 阶段和参数已补，但产物缺失只 warning，不会使 pipeline 失败 |
| F9-002 | 信号/组合/回测/归因脚本化 | 部分修复 | 4 个脚本存在；`run_attribution.py` 异常只记录日志，可能返回 0 |
| F9-003 | 测试集产物完全隔离 | 部分修复，Blocker 未闭环 | `run_test_pipeline.py` 仍写公共 `fwd_ret_panel.parquet` 和 `cov_cache/` |
| F9-004 | 测试集 run-id 本地锁 | 部分修复 | `RUN_STARTED.json` 逻辑存在；缺少回归测试，`--resume-from-lock` 会从头重跑并可覆盖已有测试运行产物 |
| F9-005 | 测试覆盖不足 | 部分修复 | 业务模块测试已补到 178 passed；阶段 9 新流水线/锁/隔离逻辑无单测，2 个产物一致性测试仍 skipped |
| F9-006 | 统一 run manifest | 部分修复 | manifest 写入逻辑存在，但无输入/输出/config hash；当前 `run_manifests/` 不存在 |
| F9-007 | tracked pyc 和缓存问题 | 部分修复 | tracked pyc 已清；`.pytest_cache` WinError 5 warning 仍存在 |

## 主要证据

### F9-001 一键 pipeline 仍不是严格发布门禁

已修复部分：

- `scripts/run_pipeline.py` 已将核心下载命令设为 `scripts.download_tushare all`、补充下载设为 `scripts.download_supplement all`、转换设为 `scripts.csv_to_parquet all`。
- `quality` 未实现时必须显式 `--skip quality`，不再静默跳过。
- `README.md` 已同步为 `python -m scripts.run_pipeline --from-stage convert --skip quality`。

残留问题：

- `scripts/run_pipeline.py:172` 定义 `_check_artifacts()`，但 `scripts/run_pipeline.py:236-237` 在产物缺失时只打印“执行成功但关键产物缺失”，随后仍返回成功。
- `scripts/run_pipeline.py:349` 会在所有阶段返回成功后写 `success=true` 的 manifest。若某阶段脚本退出码为 0 但没有生成新产物，发布门禁会被绕过。
- `quality` 仍是占位阶段；显式跳过比静默跳过更好，但不能等同于数据质量检查已实现。

闭环标准：

- 关键产物缺失必须让 pipeline 失败并写入 `success=false`。
- `attribution` 等阶段应校验所有必要产物，而不是只检查一个文件。
- 若 `quality` 继续允许跳过，manifest 和报告必须显式记录该事实。

### F9-002 脚本化链路已建立，但错误传播不足

已修复部分：

- `scripts/run_signal_combination.py`、`scripts/run_portfolio_optimization.py`、`scripts/run_backtest.py`、`scripts/run_attribution.py` 均存在。
- `scripts/run_pipeline.py` 已纳入 `signal`、`portfolio`、`backtest`、`attribution` 阶段。

残留问题：

- `scripts/run_attribution.py:130-132` 捕获 Brinson 失败后继续；`scripts/run_attribution.py:149-150` 捕获因子归因失败后只记录日志，不 `sys.exit(1)`。
- 配合 F9-001 的“产物缺失只 warning”，归因阶段可能失败但 pipeline 仍被记录为成功。
- 当前公共路径未发现新版 `brinson_attribution.parquet`、`factor_attribution.parquet` 等产物；说明脚本化链路尚未实际重建验证期产物。

闭环标准：

- 生产/发布脚本中，核心归因失败应返回非零退出码。
- pipeline 必须在归因产物缺失或过期时失败。
- 用脚本重建验证期产物，并核验日期范围不超过 `VALID_END`。

### F9-003 测试集输出仍会污染公共缓存

已修复部分：

- `scripts/run_test_pipeline.py` 的信号、权重、回测输出已写入 `test_run_dir`。
- `RUN_STARTED.json` / `RUN_FINISHED.json` 也放在 `data/processed/test_run_{N}/`。

残留问题：

- `scripts/run_test_pipeline.py:63` 仍定义 `FWD_CACHE = cfg.DATA_PROC / "fwd_ret_panel.parquet"`。
- `scripts/run_test_pipeline.py:146` 会把扩展到测试期后的 forward return 写回公共 `fwd_ret_panel.parquet`。
- `scripts/run_test_pipeline.py:64` 仍定义公共 `COV_CACHE_DIR = cfg.DATA_PROC / "cov_cache"`。
- `scripts/run_test_pipeline.py:318` 和 `scripts/run_test_pipeline.py:340` 仍会把测试期协方差写入公共 `cov_cache/`。

影响：

第二次测试集运行后，即使信号/权重/NAV 不覆盖公共路径，公共 forward return 和协方差缓存仍会包含测试期扩展结果。后续训练/验证脚本或 notebook 若读取这些缓存，会破坏测试集隔离和发布可追溯性。

闭环标准：

- 测试集 forward return、协方差缓存、信号、权重、回测、归因全部写入同一个 `test_run_{N}/` 或 `test_runs/run_N/` 目录。
- 公共 `data/processed/fwd_ret_panel.parquet` 和 `data/processed/cov_cache/` 不得被测试集流水线写入。
- 增加路径隔离单元测试，断言测试集脚本所有写路径均位于 test run 目录。

### F9-004 本地锁已有，但恢复语义仍偏弱

已修复部分：

- `scripts/run_test_pipeline.py:660-663` 创建 `test_run_{N}` 目录和 `RUN_STARTED.json` / `RUN_FINISHED.json`。
- `scripts/run_test_pipeline.py:669-686` 对已存在 `RUN_STARTED.json` 的重复运行做了拦截，除非显式传入 `--resume-from-lock`。
- git 历史当前只有 1 条 `[TEST_SET_RUN_1]`。

残留问题：

- `--resume-from-lock` 不是从中断点恢复，而是允许后续步骤从头执行；已有信号、权重、回测测试集产物可能被覆盖。
- 未读取 `check/test_set_run_log.md` 或结构化 ledger 来确认 Run #2 是否预登记。
- 未发现针对 `RUN_STARTED`、`RUN_FINISHED`、`--resume-from-lock` 的单元测试或 dry-run 测试。

闭环标准：

- 增加不消耗测试集的路径/锁文件单测。
- `--resume-from-lock` 必须明确哪些步骤可覆盖，或实现真正的断点恢复。
- Run #2 应有结构化预登记状态，脚本启动前强制校验。

### F9-005 测试覆盖明显改善，但阶段 9 新门禁未覆盖

验证结果：

```text
python -m pytest tests\test_attribution.py tests\test_backtest_engine.py tests\test_backtest_metrics.py tests\test_evaluation.py tests\test_signal_combiner.py tests\test_optimizer.py -q --basetemp=.codex_tmp_stage9_targeted
# 110 passed, 2 skipped, 6 warnings

python -m pytest tests/ -q --basetemp=.codex_tmp_stage9_full
# 178 passed, 2 skipped, 6 warnings
```

残留问题：

- `tests/test_evaluation.py` 中 2 个产物一致性测试仍因 `reports/factor_evaluation/INVALIDATED.md` 存在而跳过。
- 未发现 `run_pipeline`、`run_test_pipeline` 路径隔离、manifest 内容、锁文件重复运行等阶段 9 关键逻辑的测试。
- `.pytest_cache` 权限 warning 仍然出现。

闭环标准：

- 删除或更新 `INVALIDATED.md` 后，产物一致性测试必须真实执行并通过。
- 增加阶段 9 专项测试，不依赖真实测试集数据。
- pytest 运行不应有 `.pytest_cache` 权限 warning。

### F9-006 manifest 不是完整版本追踪

已修复部分：

- `scripts/run_pipeline.py:257-284` 会写 `data/processed/run_manifests/run_{run_id}.json`。
- manifest 记录 `run_id`、`pipeline`、起止时间、`success`、`git_commit`、`stages_executed`、`train_end`、`valid_end`。

残留问题：

- 当前 `data/processed/run_manifests/` 不存在，说明尚未用新 pipeline 生成实际 manifest。
- manifest 缺少输入文件 hash、输出文件 hash、配置 hash、测试集运行计数、是否跳过 quality、各阶段产物路径。
- `scripts/run_signal_combination.py:151-153` 在旧 `fwd_ret_panel.parquet` 缺 sidecar 时只 warning 后继续加载；若 sidecar 存在，也没有校验 `rebalance_dates_hash` / `benchmark_mode`。
- `scripts/run_signal_combination.py:186-191` 把 forward return 内容 hash 写入 `rebalance_dates_hash` 字段，字段含义不准确。

闭环标准：

- run manifest 至少记录 config hash、输入/输出路径和 hash、测试集运行计数、跳过阶段。
- 缓存缺少 metadata 时，生产路径应拒绝加载或要求显式 `--recompute`。
- metadata 字段名与内容一致，`rebalance_dates_hash` 应由调仓日期序列计算。

### F9-007 tracked pyc 已清，但 pytest cache 权限未清

已修复部分：

- `git ls-files "*__pycache__*" "*.pyc"` 当前无输出。

残留问题：

- 全量 pytest 仍出现：
  `PytestCacheWarning: could not create cache path E:\Acoding\Project\500\.pytest_cache\v\cache\nodeids: [WinError 5] 拒绝访问。`
- `git status` 仍提示无法访问 `C:\Users\lengyanjun/.config/git/ignore`，这是用户环境权限问题，不直接影响策略逻辑，但会污染命令输出。

闭环标准：

- 修复或清理 `.pytest_cache` 权限，使 `python -m pytest tests/ -q` 无缓存权限 warning。
- 发布前确认 `git status --short` 不出现 pyc/cache 变更。

## 本次验证命令

```text
python -m py_compile scripts\run_pipeline.py scripts\run_signal_combination.py scripts\run_portfolio_optimization.py scripts\run_backtest.py scripts\run_attribution.py scripts\run_test_pipeline.py
# 通过

python -m pytest tests\test_attribution.py tests\test_backtest_engine.py tests\test_backtest_metrics.py tests\test_evaluation.py tests\test_signal_combiner.py tests\test_optimizer.py -q --basetemp=.codex_tmp_stage9_targeted
# 110 passed, 2 skipped, 6 warnings

python -m pytest tests/ -q --basetemp=.codex_tmp_stage9_full
# 178 passed, 2 skipped, 6 warnings

git ls-files "*__pycache__*" "*.pyc"
# 无输出

git log --oneline --grep="\[TEST_SET_RUN_" --all
# 80708d4 [TEST_SET_RUN_1] 测试集评估完成 (2023-2025)
```

## 自检

- 测试集纪律：未运行 `scripts/run_test_pipeline.py`，未读取/生成 2023-2025 测试结果，未消耗第二次测试机会。
- 复现性：只做脚本、路径、metadata 和测试门禁复核；未声称当前公开产物已经由新 pipeline 重建。
- 交易/回测口径：本次未重算回测结果，未给出任何新的收益、IR 或超额回撤结论。
- 工程质量：新增复核结果已写入 `check/18_stage9_fix_verification.md`，未修改业务源码。
