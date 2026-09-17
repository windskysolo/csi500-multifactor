# 正式运行前状态复核

> 复核日期：2026-05-20  
> 复核目标：判断当前是否已经“全部没有问题，可以开始正式运行”。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未消耗剩余测试集次数。

---

## 总结论

二次复核更新（2026-05-20 21:20）：当前不建议直接开始正式测试集运行，但上次列出的 4 个阻断项中，前 3 个已经完成或基本完成；第 4 个“测试期因子面板尚未生成”仍是正式 Run #2 前的必要步骤。

当前业务模块测试整体通过，因子评价、信号、组合优化、验证期回测和归因的主要产物也已存在。测试集 run-id 计数、Run #1 消耗口径、一键 pipeline 回测门禁已经修正；测试期因子面板入口已通过 dry-run 校验，但实际 `factor_panels_test_run_2/` 尚未生成。

---

## 二次复核更新

| 原阻断项 | 当前结论 | 复核证据 |
|---|---|---|
| 一键 pipeline 回测阶段会失败 | 已修复 | `scripts/run_pipeline.py` 的 `backtest.extra` 已显式传入 `--allow-noncompliant-weights`；实测 `python -m scripts.run_pipeline --from-stage backtest --skip quality` 成功，写出 `run_20260520_212027.json` |
| 测试集 run-id 计数函数失效 | 已修复 | `scripts.run_pipeline._count_test_set_runs_git()`、`scripts.run_test_pipeline._count_test_set_runs()`、`scripts.build_factor_panels._count_test_set_git_runs()` 均返回 1；`git log --fixed-strings --grep=[TEST_SET_RUN_` 也返回 1 条 |
| 测试集运行记录与硬规则冲突 | 已修复 | `check/test_set_run_log.md` 已改为 Run #1 结果作废但运行次数已消耗，剩余有效运行次数为 1 次，仅 Run #2 |
| 测试期因子面板尚未生成 | 未完成，流程已就绪 | 当前 `data/processed/factor_panels/` 最新仍为 `2022-12-30`，且不存在 `factor_panels_test_run_2/`；但 dry-run 通过，会输出到独立目录 |

二次复核命令摘要：

```text
python -m scripts.run_pipeline --from-stage backtest --skip quality
# 成功；backtest 阶段命令包含 --allow-noncompliant-weights；manifest test_set_run_count=1

python -m scripts.build_factor_panels --allow-test-set --run-id 2 --end-date 2025-12-31 --dry-run
# 成功；120 个调仓日，2016-01-29 ~ 2025-12-31；输出目录 factor_panels_test_run_2

python -m pytest tests/ -q --basetemp=.codex_tmp_recheck_all
# 201 passed, 2 warnings
```

本次仍未运行 `scripts/run_test_pipeline.py`，未生成测试集绩效结果。

---

## 已通过的检查

| 检查项 | 当前结果 |
|---|---|
| `reports/factor_evaluation/INVALIDATED.md` | 不存在 |
| `final_factors.json` metadata | 存在，含 `run_id/generated_at/git_commit/factor_summary_md5/n_final/n_excluded` |
| 公共组合 metadata | `portfolio_weights_meta.parquet` 为 84 × 6，含 `cov_available/w_prev_source/constraint_compliant` |
| Fallback 分布 | L1=67，L2=2，L3=15 |
| 非合规权重 | 15/84 期 `constraint_compliant=False` |
| 验证期回测产物 | `backtest_nav.parquet`、`backtest_metrics.parquet` 存在 |
| run manifest | 已生成当前 HEAD 的 `run_20260520_212027.json`，覆盖 `backtest → attribution`，`test_set_run_count=1` |
| git 测试集运行历史 | `git log --fixed-strings --grep=[TEST_SET_RUN_` 实际有 1 条：`80708d4 [TEST_SET_RUN_1]` |
| 单元测试 | `201 passed, 2 warnings` |

---

## 首次复核原始问题记录

以下内容是首次复核时的原始阻断记录。二次复核后的当前状态以上方“二次复核更新”为准：一键 pipeline、run-id 计数、测试集运行记录口径已修复；测试期因子面板仍待生成。

### 1. 一键 pipeline 的回测阶段会失败

实测命令：

```text
python -m scripts.run_backtest
```

结果：退出码 1。原因是当前公共权重仍有 15/84 期 `constraint_compliant=False`：

```text
15/84 期权重 constraint_compliant=False
```

`scripts/run_backtest.py` 默认拒绝这些权重，这是正确的保守门禁。但 `scripts/run_pipeline.py` 的 `backtest` 阶段没有传入 `--allow-noncompliant-weights`，所以从 `backtest` 或更早阶段运行训练/验证 pipeline 会中止。

正式运行前需要二选一：

1. 修到 0 期 `constraint_compliant=False`。
2. 明确接受 L3 非合规限制，并把 `--allow-noncompliant-weights` 做成 pipeline/test pipeline 的显式参数，同时在报告和 manifest 中记录。

### 2. 测试集 run-id 计数函数当前失效

实测结果：

```text
git log --oneline --all --grep="\[TEST_SET_RUN_" -> 1 条
scripts.run_pipeline._count_test_set_runs_git() -> -1
scripts.run_test_pipeline._count_test_set_runs() -> 0
```

主要原因：

- 脚本使用 `--grep=[TEST_SET_RUN_]`，未转义 `[`，会把普通中文提交也匹配进来。
- `run_test_pipeline.py` 使用 `text=True`，在 Windows 上会按本地编码读取 git 输出，遇到 UTF-8 中文提交可能触发解码异常并返回 0。

影响：

- `run_test_pipeline --run-id 2` 的防护逻辑不可信。
- manifest 中 `test_set_run_count=-1` 不可信。
- `build_factor_panels.py --allow-test-set` 的运行次数防护也可能误判。

修复建议：

- 统一使用 `git log --oneline --all --fixed-strings --grep=[TEST_SET_RUN_`。
- 使用 bytes 输出后 `decode("utf-8", errors="replace")`，或显式 `encoding="utf-8", errors="replace"`。
- 为三个入口补同一个 helper 和回归测试：`build_factor_panels.py`、`run_pipeline.py`、`run_test_pipeline.py`。

### 3. 测试集运行记录与硬规则冲突

`check/test_set_run_log.md` 当前写：

```text
Run #1 已作废，不计入有效次数
后续有效运行次数上限仍为 2 次（Run #2 和 Run #3）
```

这与项目硬规则和 README 当前口径冲突。保守纪律应按 git 历史计数：已有 `[TEST_SET_RUN_1]`，因此只剩 1 次正式测试集运行机会。

修复建议：

- 将 `check/test_set_run_log.md` 改为“Run #1 结果作废，但运行次数已消耗”。
- 删除 Run #3 作为常规计划，只保留“除非项目负责人书面批准并修改纪律，否则不可运行第三次”。

### 4. 测试期因子面板尚未生成

当前因子面板检查结果：

```text
data/processed/factor_panels/*.parquet
n_files = 27
earliest = 2016-01-29
latest = 2022-12-30
```

`check/test_set_run_log.md` 自身要求测试集运行前确认：

```text
因子面板最新期 >= 2025-12-30
```

因此当前尚不能直接运行 2023-2025 测试集评估。需要先在受控 run-id 纪律下生成测试期因子面板，并确保这一步也计入同一次正式测试集流程或被同一 run-id 记录。

---

## 次要问题

| 问题 | 影响 |
|---|---|
| `run_20260520_212027.json` 只覆盖 `backtest → attribution`，不是从 `convert/factors/evaluate` 开始的完整链路 | 可作为当前 HEAD 的回测/归因追溯记录，但不是完整训练/验证全流程复现记录 |
| `fwd_ret_panel.meta.json` 中 `benchmark_mode=null` | 审计语义不够明确，建议改为 `raw_open_to_open_no_benchmark` |
| `attribution_metadata.json` 的 `known_unfixed_risks` 含过期描述 | 报告 metadata 与当前修复状态不一致 |
| `pytest` 仍有 `.pytest_cache` 的 `WinError 5` 警告 | 工程卫生问题；不影响核心测试通过，但不应再写“无缓存权限 warning” |

---

## 本次验证命令摘要

```text
python -m scripts.run_backtest
# 退出码 1，因 15/84 期 constraint_compliant=False

python -m pytest tests/ -q --basetemp=.codex_tmp_pre_run_all
# 201 passed, 2 warnings
```

未运行 `scripts/run_test_pipeline.py`。

---

## 建议的下一步

1. 先修复测试集运行计数 helper，并更新 `check/test_set_run_log.md` 为“剩余 1 次”。
2. 决定如何处理 15 期非合规 L3 权重：完全修到合规，或显式参数化并披露。
3. 生成 2023-2025 因子面板时必须纳入测试集 run-id 纪律，不允许作为灰区预处理。
4. 修复后先跑训练/验证 pipeline 到 manifest，确认当前 HEAD 的 manifest 为 `success=true` 且 `test_set_run_count=1`。
5. 上述完成后，再预登记并运行唯一剩余的正式测试集 run-id。
