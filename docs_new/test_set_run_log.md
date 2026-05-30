# 测试集运行记录

> 项目纪律：测试集（2023-2025）有效运行次数不超过 2 次。  
> 当前确认：测试集尚未有效运行，已用 0 次，剩余 2 次。  
> 结构化计数文件：`docs/check/test_set_runs.json`。

---

## 记录修正说明

历史中曾出现 `80708d4 [TEST_SET_RUN_1]` 记录。用户于 2026-05-25 确认该记录为错误记载：测试集实际未使用。

处理决定：

- 删除原 Run #1 的有效运行记录。
- 不再以 git 历史中的 `[TEST_SET_RUN_N]` 标签作为测试集次数来源。
- 脚本改为读取 `docs/check/test_set_runs.json` 的 `active_runs`。

---

## Run #1（待使用）

> 剩余有效次数：2 次。使用前必须完成预登记。

| 字段 | 值 |
|---|---|
| Run ID | 1 |
| 日期 | （待填） |
| Git commit | （待填） |
| 目的 | 第一次正式测试集评估（2023-2025） |
| 命令 | `python -m scripts.run_test_pipeline --run-id 1 --factor-panel-dir data/processed/factor_panels_test_run_1` |
| 输出文件 | （待填） |
| 结论 | （运行后填写） |

---

## Run #2（待使用）

> Run #1 完成并记录后方可使用。

| 字段 | 值 |
|---|---|
| Run ID | 2 |
| 日期 | （待填） |
| Git commit | （待填） |
| 目的 | 第二次且最后一次测试集评估（如 Run #1 后仍需确认） |
| 命令 | `python -m scripts.run_test_pipeline --run-id 2 --factor-panel-dir data/processed/factor_panels_test_run_2` |
| 输出文件 | （待填） |
| 结论 | （运行后填写） |

---

## 防误用检查清单

运行测试集前必须核对：

- [ ] `docs/check/test_set_runs.json` 中 `active_runs` 数量与本文件一致
- [ ] `src/config.py` 中所有研究参数已冻结（不在测试集运行期间修改）
- [ ] 因子面板最新期 ≥ `2025-12-30`
- [ ] 本文件已完成预登记（日期、commit、目的）
- [ ] 对应的 `data/processed/test_run_N/` 不存在，或已有 `RUN_STARTED.json` 且有明确恢复方案
