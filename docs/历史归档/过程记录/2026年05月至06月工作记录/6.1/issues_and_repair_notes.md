# 测试集重跑前问题汇总与修复备忘

生成日期：2026-06-01  
适用目录：`E:\Acoding\Project\500 improve`  
当前目标：在正式消耗 `[TEST_SET_RUN_1]` 前，保证测试期实验条件尽量与 2021-2022 验证期一致，避免数据缺口导致测试结果不可解释。

---

## 1. 总体结论

当前所有自动审查没有 `FAIL`，但仍不建议直接正式跑测试集。

原因不是 pipeline 代码已经无法运行，而是两个最终入选因子 `hk_hold_ratio` / `hk_hold_chg` 在 2024-08 之后遭遇结构性数据披露变化，导致测试期后半段与 2021-2022 验证期条件不一致。

最关键判断：

- 2021-2022：`hk_hold_ratio` / `hk_hold_chg` 每月约 470-485 只有效股票，无全空月。
- 2023-2025：`hk_hold_ratio` 有 11 个测试月全空；`hk_hold_chg` 有 15 个测试月全空。
- 根因不是本地 Parquet 转换错误，也不是普通 API 故障；官方 A 股北向持股明细自 2024-08-19 后不再按日披露，改为季度披露上季度末数据。

因此，不应强行用 0 填充或简单前向填充来伪装成日频数据。更严谨的修复路线是修改 `hk_hold` 因子定义，使其适配季度披露后的 PIT 数据，并重新跑 2021-2022 验证期后再进入测试集。

---

## 2. 已完成检查产物

相关报告：

- `current work/6.1/l1_l2_inspection_report.md`
- `current work/6.1/l3_l4_inspection_report.md`
- `current work/6.1/remaining_inspection_report.md`
- `current work/6.1/l1_l2_inspection_results.json`
- `current work/6.1/l3_l4_inspection_results.json`
- `current work/6.1/remaining_inspection_results.json`

相关审查脚本：

- `current work/6.1/check_l1_l2.py`
- `current work/6.1/check_l3_l4.py`
- `current work/6.1/check_remaining.py`

审查摘要：

| 阶段 | 结果 | 说明 |
|---|---:|---|
| L1/L2 | PASS=9, WARN=1, FAIL=0 | WARN 来自 `hk_hold` 系列测试期部分月份全 NaN |
| L3/L4 | PASS=7, WARN=2, FAIL=0 | WARN 为 fwd label 边界与 `excess_return` 语义一致性 |
| Preflight/L5/L6 Gate | PASS=4, WARN=2, FAIL=0, PENDING=4 | L6 正式运行和运行后验证尚未执行 |

---

## 3. 问题清单

### P0 - `hk_hold_ratio` / `hk_hold_chg` 测试期覆盖断裂

风险等级：高  
状态：未修复  
是否阻止直接测试：建议阻止

现象：

| 因子 | 验证期 2021-2022 | 测试期 2023-2025 |
|---|---:|---:|
| `hk_hold_ratio` | 平均 481.6 只，最少 479，只数全空月 0 | 平均 343.9 只，最少 0，全空月 11 |
| `hk_hold_chg` | 平均 480.6 只，最少 473，只数全空月 0 | 平均 288.4 只，最少 0，全空月 15 |

全空月份：

`hk_hold_ratio`：

```text
2024-08-30
2024-10-31
2024-11-29
2025-01-27
2025-02-28
2025-04-30
2025-05-30
2025-07-31
2025-08-29
2025-10-31
2025-11-28
```

`hk_hold_chg`：

```text
2024-10-31
2024-11-29
2024-12-31
2025-01-27
2025-02-28
2025-03-31
2025-04-30
2025-05-30
2025-06-30
2025-07-31
2025-08-29
2025-09-30
2025-10-31
2025-11-28
2025-12-31
```

本地 raw 数据证据：

- `data/raw/hk_hold/*.csv` 在 `2024-08-17` 后只剩季度点附近数据。
- 典型后续日期：

```text
2024-09-30
2024-12-31
2025-03-31
2025-06-30
2025-09-30
2025-12-31
```

API 小样本验证：

- `daily(000001.SZ, 20240101-20240131)` 可返回正常行情，说明代理 API 基本可用。
- `hk_hold(ts_code='000009.SZ', 20251001-20251031)` 返回空。
- `hk_hold(ts_code='000009.SZ', 20250901-20250930)` 只返回 `2025-09-30`。
- `hk_hold(trade_date='20251031')` 返回的是 `.HK` 港股记录，不是 `.SZ/.SH` A 股北向持股。
- 加 `exchange=SZ/SH` 也无法取到 `2025-10-31` 的 A 股 `hk_hold`。

外部规则依据：

- 上交所 2024-04-12 公告说明：沪深股通方面，调整后每季度第 5 个沪深股通交易日公布上季度末单只证券沪深股通投资者合计持有数量。  
  来源：https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20240412_10753188.shtml
- 港交所持股记录查询页说明：2024-08-19 起，北向交易持股记录查询服务调整为每季度第五个沪深股通交易日公布上季度末数据。  
  来源：https://www3.hkexnews.hk/sdw/search/mutualmarket_c.aspx?t=sz

推荐修复：

1. 不再尝试强行补官方不存在的 A 股日频 `hk_hold`。
2. 修改 `hk_hold_ratio` 为“最近可得季度披露快照”因子：
   - 只允许使用 `trade_date <= T` 的最近披露点。
   - 明确记录最大允许滞后，例如不超过 120 天；超过则置 NaN。
3. 修改 `hk_hold_chg` 为“最近两个季度披露点之间的变化”：
   - 例如 `ratio_latest - ratio_prev_quarter`。
   - 两个披露点都必须 `<= T`。
   - 不足两个披露点时置 NaN。
4. 用同一套新定义重建 2021-2025 面板。
5. 重新跑 2021-2022 验证期，重新 promote mainline。
6. 通过验证期后再正式运行测试集。

不推荐：

- 不要用 0 填充。0 表示“无北向持仓”，不是“未披露”。
- 不要简单前向填充后仍命名为日频因子。若使用最近披露快照，必须在因子定义和文档中显式说明。
- 不要只在测试期改口径。否则测试期和验证期条件不一致。
- 不要用 `.HK` 港股持股数据替代 `.SZ/.SH` A 股北向持股。

---

### P1 - `signal.target = excess_return` 与当前 RidgeCombiner 构造语义不完全一致

风险等级：中  
状态：未修复  
是否阻止直接测试：不单独阻止，但必须记录

现象：

- `runs/train_valid/20260530_111129__rolling48_topn150_ew/run_config.json` 中：

```json
"target": "excess_return"
```

- 当前 `scripts/run_test_pipeline.py` 构造 `RidgeCombiner` 时没有显式传入 `use_excess_return=True`。

判断：

- 这与当前已 promoted 的 train_valid 主线行为一致。
- 如果现在修改，会改变策略定义，必须重跑 train_valid 并重新 promote。

推荐修复：

- 短期：不在测试前单独修改。
- 中期：在重新处理 `hk_hold` 因子定义时，一并核对 Ridge 训练目标口径：
  - 如果验证期主线实际训练的是 raw forward return，则 `run_config` 应改为准确描述。
  - 如果目标确实应为 excess return，则代码构造必须显式传 `use_excess_return=True`，并重跑 train_valid。

---

### P2 - fwd return 扩展包含 `2022-12-30` 边界标签

风险等级：中低  
状态：已检查，无未来函数证据  
是否阻止直接测试：否

现象：

- `_extend_fwd_ret_panel` 会新增 `2022-12-30` 到 `2025-11-28` 的 forward return 标签。
- `2022-12-30` 标签使用 2023-01 的退出价格，容易被误判为测试期泄漏。

检查结论：

- rolling48 + purge=2 的训练模拟中，所有训练标签的 `exit_date < prediction T`。
- 当前未发现未来标签进入信号预测的证据。

建议：

- 保持现有实现。
- 在正式测试报告中保留该边界说明，避免后续审计误解。

---

### P3 - 旧无效 test run 目录曾经会阻塞新 `run-id=1`

风险等级：高  
状态：已处理  
是否阻止直接测试：已不阻止

原问题：

- 旧目录存在：

```text
runs/test/test_run_1__20260530_111129__rolling48_topn150_ew/
```

- 其中有 `signal/composite.parquet` 和 `RUN_STARTED.json`。
- 新的 `python scripts/run_test_pipeline.py --run-id 1` 会生成同名目录，触发冲突或误复用旧产物风险。

处理结果：

已非删除式归档为：

```text
runs/test/test_run_1__20260530_111129__rolling48_topn150_ew__VOIDED/
```

当前检查：

- 预期新目录不存在。
- stale `RUN_STARTED.json` 不存在。
- stale `signal/composite.parquet` 不存在。

建议：

- 保留 `__VOIDED` 目录作为审计证据。
- 正式重跑时不要使用 `--resume-from-lock`。

---

### P4 - 正式测试前 worktree 需要整理并提交

风险等级：高  
状态：未完成  
是否阻止直接测试：建议阻止

原因：

- 当前工作区存在大量修改和新增文件。
- 测试集纪律要求正式测试结果能对应明确代码和数据状态。
- 正式测试 commit message 必须含 `[TEST_SET_RUN_1]`。

建议流程：

1. 先完成 `hk_hold` 因子定义修复和验证期重跑。
2. 检查待提交文件：

```powershell
git status --short
```

3. 不要盲目 `git add .`。
4. 如果 Parquet 大文件已经被 Git 跟踪，则按项目现有规则处理；如果未跟踪且被忽略，不要强行提交大文件。
5. 提交信息必须包含：

```text
[TEST_SET_RUN_1]
```

---

### P5 - `docs/logs/test_set_runs.json` 说明文本有乱码

风险等级：低  
状态：未修复  
是否阻止直接测试：否

现象：

- `docs/logs/test_set_runs.json` 中 `corrections` 的中文说明存在编码乱码。
- 但结构字段仍可读：
  - `max_runs = 3`
  - `active_runs = []`
  - 当前有效测试集运行次数为 0

建议：

- 可在正式测试前用 UTF-8 重新写清楚 `corrections` 文本。
- 不要改变 `active_runs` 计数，除非正式测试完成并由 pipeline 自动记录。

---

### P6 - L6 正式测试与运行后验证仍未执行

风险等级：流程项  
状态：PENDING

未执行项：

- `L6-2` 正式测试 pipeline：

```powershell
python scripts/run_test_pipeline.py --run-id 1
```

- `L6-3` 运行后产物验证：
  - `signal/composite.parquet`
  - `portfolio/optimizer_meta.parquet`
  - `backtest/trades_valid.parquet`
  - `backtest/actual_weights_valid.parquet`

- `L6-4` 更新项目状态：
  - `CLAUDE.md` 测试集次数
  - IR 和硬指标结果
  - `docs/logs/test_set_runs.json` 验证
  - `current work/6.1` 分析报告刷新

建议：

- 在 `hk_hold` 问题修复和验证期重新确认前，不运行正式测试集。
- 正式运行后立刻重跑：

```powershell
python "current work\6.1\check_remaining.py"
```

---

## 4. 当前不缺的数据

最终入选 18 个因子中，除 `hk_hold_ratio` / `hk_hold_chg` 外，其余因子测试期没有全空月份。

覆盖摘要：

| 因子类型 | 测试期覆盖 |
|---|---|
| 技术/流动性因子 | 大多每月 493-498 只有效股票 |
| 财务/质量/成长因子 | 大多每月 440-456 只有效股票 |
| `analyst_eps_revision` | 平均约 380 只，最少 329，无全空月 |
| `holder_chg` | 平均约 371 只，最少 366，无全空月 |

因此，当前不建议扩大数据补齐范围。后续重点只放在 `hk_hold` 口径修复。

---

## 5. 推荐后续修复路线

### 路线 A：保守推荐，修改因子定义并重跑验证期

适用条件：

- 承认 2024-08 后 A 股北向日频持股官方不可得。
- 目标是保证验证期和测试期同一套因子定义。

步骤：

1. 修改 `src/factors/alt_factors.py`：
   - `factor_hk_hold_ratio` 改为最近可得披露快照。
   - `factor_hk_hold_chg` 改为最近两个披露点变化。
2. 补充/修改 `tests/test_hk_hold_coverage.py`：
   - 验证只使用 `trade_date <= T`。
   - 验证非季末月份取最近披露快照。
   - 验证超过最大滞后时置 NaN。
   - 验证 `chg` 需要两个披露点。
3. 重建 2021-2025 因子面板。
4. 重新跑 2021-2022 train_valid 主线实验。
5. 重新 promote mainline。
6. 重新执行 L1-L6 检查。
7. 全部通过后，正式运行测试集。

优点：

- 符合 PIT。
- 不伪造日频数据。
- 验证期和测试期条件一致。

缺点：

- 会改变策略定义，需要重新验证和 promote。

### 路线 B：剔除 `hk_hold` 系列因子并重跑验证期

适用条件：

- 不希望在当前阶段引入季度披露因子定义。
- 接受资金流/外资行为维度减少。

步骤：

1. 从最终因子列表中剔除：

```text
hk_hold_ratio
hk_hold_chg
```

2. 重新选择/确认最终因子。
3. 重跑 2021-2022 train_valid。
4. 重新 promote。
5. 再进入测试集。

优点：

- 逻辑简单。
- 避免披露机制变化带来的结构性不可比。

缺点：

- 删除了此前验证期有效的 A 股特色因子。
- 可能影响 IR。

### 路线 C：继续寻找第三方日频数据

适用条件：

- 能获得明确来源、字段定义、PIT 可用时间的数据。

必须确认：

- 是否真的是 A 股 `.SZ/.SH` 北向持股，不是 `.HK` 港股持股。
- 2024-08 后日频数据来源是什么。
- 是官方披露、估算、券商加工，还是其他非公开数据。
- 是否能合法用于本项目。
- 字段是否能映射到当前 `vol` / `ratio`。

如果数据商只是季度披露，则不能解决当前问题。  
如果数据商提供日频估算，则应作为新数据源/新因子，而不是无声替换原 `hk_hold`。

---

## 6. 明确禁止的修复方式

以下方式不建议使用：

1. **用 0 填充 `hk_hold` 缺失值**  
   0 表示无北向持仓，不等于未披露。

2. **简单前向填充并仍称为日频因子**  
   这会掩盖披露频率变化。若使用最近披露值，必须显式改因子定义。

3. **只修测试期，不重跑验证期**  
   会造成验证期和测试期实验条件不一致。

4. **用 `.HK` 港股持股替代 `.SZ/.SH` A 股北向持股**  
   经济含义完全不同。

5. **临时修改主线参数后直接跑测试集**  
   任何影响策略定义的修改都必须先经过 2021-2022 验证。

---

## 7. 当前建议决策

推荐优先采用路线 A：

> 将 `hk_hold_ratio` / `hk_hold_chg` 从“日频北向持股因子”改造成“季度披露 PIT 因子”，然后重建面板、重跑验证期、重新 promote mainline，再正式测试。

这是当前最符合工业级回测纪律的处理方式。

