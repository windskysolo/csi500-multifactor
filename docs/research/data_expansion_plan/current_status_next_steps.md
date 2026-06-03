# 当前状态与后续执行计划

> 生成日期：2026-05-22  
> 适用范围：训练数据扩展到 2012 起点，并接入 12 类备选数据与 15 个备选因子。  
> 依据文件：`teach/data_expansion_plan/phase_plan.md`、`PROJECT_PLAN_v1.1.md`、当前代码与本地数据检查结果。

---

## 1. 当前结论

当前处于：

```text
阶段 3 已完成，准备进入阶段 4。
```

可以开始跑阶段 4：

```powershell
python -m scripts.csv_to_parquet all
```

但现在不要直接一口气跑完整流水线到因子评估、信号、优化、回测、归因。原因是当前 `data/processed`、`data/processed/factor_panels`、`data/processed/fwd_ret_panel.parquet` 仍然是旧口径产物，主要起点还是 2016，而本次计划目标是训练期扩展到 2012。

---

## 2. 已完成状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| 阶段 0：现状冻结 | 已完成 | `feature/expand-train-2012` 分支已建立，旧结果已记录 |
| 阶段 1：API 探针 | 已完成 | `scripts/probe_data_coverage.py` 和探针报告已完成，12 个新增接口通过 |
| 阶段 2：代码准备 | 已完成，待运行验证 | 配置、下载脚本、Parquet 转换、loader、因子面板 CLI 已改好 |
| 阶段 2.5：备选因子实现 | 已完成，待修测试口径 | `src/factors/alt_factors.py` 已实现 15 个备选因子 |
| 阶段 3：下载 | 已完成 | 数据覆盖检查为 15 通过 / 1 warning，`margin` 从 2013 起是已知限制 |
| 阶段 8：测试代码准备 | 已提前完成 | 测试文件已写好，但目前不能算阶段 8 完成，因为测试还没有全绿 |

---

## 3. 当前已知风险

### 3.1 processed 仍是旧口径

当前 `data/processed/index_member.parquet` 和 `data/processed/factor_panels/*.parquet` 仍主要覆盖：

```text
2016-01-29 ~ 2022-12-30
```

这说明 raw 数据虽然已经补好，但 processed 层尚未重建。必须先完成阶段 4。

### 3.2 不要单独跑 `csv_to_parquet index`

`scripts.csv_to_parquet index` 会用当前 `daily_quote.parquet` 的股票交易日历对齐指数。如果 `daily_quote.parquet` 还是旧的 2016 起点，单独重建 `index_quote.parquet` 也会被限制在 2016 起点。

正确做法是直接跑：

```powershell
python -m scripts.csv_to_parquet all
```

让 `daily_quote`、`daily_basic`、`index_quote`、`industry`、`financial_pit`、`stock_status`、`index_member` 和新增备选数据一起按新 raw 数据重建。

### 3.3 阶段 5 不能使用 `--resume`

`scripts/build_factor_panels.py` 的 `--resume` 逻辑适合向后追加日期，例如从 2022 追加到 2025；但本次是向前补 2012-2015。

如果使用：

```powershell
python -m scripts.build_factor_panels --factor-set all --resume
```

可能漏算 2012-2015，导致结果仍是旧口径。

阶段 5 必须全量重算：

```powershell
python -m scripts.build_factor_panels --factor-set all
```

### 3.4 forward return 缓存是旧口径

当前 `data/processed/fwd_ret_panel.meta.json` 仍记录旧样本：

```text
2016-01-29 ~ 2022-12-30
```

阶段 6 因子评估必须强制重算 forward return：

```powershell
python -m scripts.run_factor_evaluation --recompute-fwd-ret --run-id expand-train-2012-v1
```

### 3.5 技术因子测试存在 2 个口径问题

已知测试结果：

```text
64 passed, 3 failed
```

其中 1 个失败来自 processed 未重建，阶段 4 后应解决；另外 2 个来自技术因子口径：

- RSI：`_compute_rsi` 对最近窗口无下跌日时返回 `NaN`，测试希望混合序列存在有效值。
- OBV：`obv_chg_20d` 当前实现返回 19，测试期望 20，属于 20 日窗口定义的 off-by-one 口径问题。

这两个问题建议在阶段 4 后、阶段 5 前修复，避免后续因子面板重算两次。

---

## 4. 后续执行计划

### 第 0 步：冻结当前工作状态

目的：后续阶段会覆盖 processed、factor panels 和报告文件，先保留当前状态，避免新旧结果混在一起。

先查看：

```powershell
git status --short
```

如果确认当前改动都要保留，建议提交：

```powershell
git add .
git commit -m "phase3 data expansion ready before processed rebuild"
```

如果暂时不想提交，至少把 `git status --short` 输出保存到检查记录中。

通过标准：

- 当前分支是 `feature/expand-train-2012`。
- 已知道哪些文件是本轮扩展产生的改动。
- 没有误跑测试集流水线。

失败或疑问处理：

- 如果工作树里有你不确定来源的改动，不要继续跑阶段 4，先人工确认这些改动是否要保留。

---

### 第 1 步：阶段 4，重建 processed 层

运行：

```powershell
python -m scripts.csv_to_parquet all
```

该步骤会重建核心 processed 文件，包括：

- `daily_quote.parquet`
- `daily_basic.parquet`
- `index_quote.parquet`
- `industry.parquet`
- `financial_pit.parquet`
- `indicator_pit.parquet`
- `holder_pit.parquet`
- `dividend_pit.parquet`
- `margin.parquet`
- `moneyflow.parquet`
- `stock_status.parquet`
- `index_member.parquet`
- 12 个新增备选 processed parquet

运行结束后检查：

```powershell
python -m pytest tests\test_index_quote_code.py tests\test_data_expansion_config.py tests\test_hk_hold_coverage.py tests\test_alt_data_pit.py -q
```

通过标准：

- `reports/processed_coverage_report.md` 已生成。
- `index_quote.parquet` 使用 `H00905.CSI`，不是价格指数。
- `index_quote.parquet` 覆盖 2011 起点附近。
- `index_member.parquet` 覆盖 2012 起的调仓日。
- PIT 相关测试通过。

失败处理：

- 如果 `index_quote` 仍从 2016 起，检查 `daily_quote.parquet` 是否真正重建到 2011 起。
- 如果 `index_quote` 混入非 `H00905.CSI`，必须停止，修 `data/raw/index_daily.csv` 或 `process_index_quote`。
- 如果 `index_member` 仍从 2016 起，检查 `data/csi500_index_weight_201201_202512.csv` 是否被 `process_index_member` 正确读取。
- 如果新增备选 parquet 缺失，先修对应的 `csv_to_parquet` processor 或 raw 文件路径。

---

### 第 2 步：修复技术因子测试口径

阶段 4 通过后，修复两个技术因子测试问题。

#### 2.1 RSI 口径

相关位置：

```text
src/factors/alt_factors.py
tests/test_technical_factors.py
```

需要二选一并写清楚：

- 方案 A：常见 RSI 口径，无下跌日时 RSI = 100。
- 方案 B：保守口径，无下跌日时 RSI = NaN，并同步修改测试说明。

建议采用方案 A，因为 RSI 的常见定义更符合直觉，也能避免强趋势窗口被错误置空。

> **注意**：修改代码时必须同步修改 `tests/test_technical_factors.py` 中的 `TestRSIComputation`。
> 当前测试锁定了「全正收益 RSI=NaN」的保守行为（见 `alt_factors_implementation.md` 第 7.5 节），
> 改为方案 A 后测试必须一起改，否则仍会失败。

#### 2.2 OBV 20 日窗口口径

相关位置：

```text
src/factors/alt_factors.py
tests/test_technical_factors.py
```

需要统一：

- `20d` 是 20 个收益区间，还是 20 个交易日内的 19 个收益变化。

建议明确为 20 个收益区间，因此需要 21 个交易日数据。

修完后运行：

```powershell
python -m pytest tests\test_technical_factors.py -q
```

通过标准：

- `tests/test_technical_factors.py` 全绿。
- 技术因子仍满足 `load_daily_quote` 的 `end <= rebalance_date`，不使用未来数据。

失败处理：

- 如果 RSI 仍失败，检查最后一个窗口是否只有正收益或只有负收益。
- 如果 OBV 仍失败，检查测试数据长度和实现窗口是否一致。

---

### 第 3 步：阶段 5 dry-run，确认调仓日起点

运行：

```powershell
python -m scripts.build_factor_panels --factor-set all --dry-run
```

通过标准：

输出应显示类似：

```text
调仓日数量：约 132 期
起止：2012-xx-xx ~ 2022-12-30
目标因子（42）
```

失败处理：

- 如果起点还是 `2016-01-29`，说明阶段 4 的 `index_member.parquet` 没有重建到 2012 起，回到第 1 步检查。
- 如果目标因子不是 42 个，检查 `ALL_FACTORS` 和 `ALT_FACTORS` 注册。
- 如果报未知因子，检查 `scripts/build_factor_panels.py` 中的因子列表和 `src/factors/alt_factors.py` 注册字典是否一致。

---

### 第 4 步：阶段 5，全量重建因子面板

确认 dry-run 正确后运行：

```powershell
python -m scripts.build_factor_panels --factor-set all
```

禁止使用：

```powershell
python -m scripts.build_factor_panels --factor-set all --resume
```

通过标准：

- `data/processed/factor_panels/` 下生成 42 个因子面板。
- 每个面板起点应覆盖 2012 起的调仓日。
- `data/processed/factor_panel_diagnostics.parquet` 已更新。

失败处理：

- 如果某些备选因子早期全 NaN，先判断是否为已知覆盖限制，例如 `hk_hold` 早期、`cyq_perf` 2018 前。
- 如果 loader 报 parquet 不存在，回到第 1 步检查对应 processor 是否运行成功。
- 如果中性化失败，检查 `industry.parquet` 和 `daily_basic.log_free_float_mv` 覆盖情况。
- 如果全量运行中断，不要马上用 `--resume` 补，先确认是否已经补足 2012-2015；必要时删除不完整因子面板后重跑。

---

### 第 5 步：阶段 6，单因子评估

> **运行前备份**：此步骤会覆盖 `reports/factor_evaluation/` 下的全部旧报告（旧口径 2016 起点）。
> 如需对比新旧结论，先执行：
> ```powershell
> Copy-Item -Recurse reports\factor_evaluation reports\factor_evaluation_2016_backup
> ```

运行：

```powershell
python -m scripts.run_factor_evaluation --recompute-fwd-ret --run-id expand-train-2012-v1
```

必须带：

```text
--recompute-fwd-ret
```

原因：旧 forward return 缓存是 2016 起点，不能复用。

通过标准：

- `data/processed/fwd_ret_panel.parquet` 和 `.meta.json` 更新为新日期口径。
- `reports/factor_evaluation/` 下报告和 CSV 更新。
- 训练期使用 2012-2020。
- 验证期使用 2021-2022。
- 未进入 2023-2025 测试集。

重点检查文件：

```text
reports/factor_evaluation/ic_result.csv
reports/factor_evaluation/quintile_summary.csv
reports/factor_evaluation/shift_result.csv
reports/factor_evaluation/valid_comparison.csv
```

失败处理：

- 如果提示 forward return metadata 不匹配，确认已加 `--recompute-fwd-ret`。
- 如果评估结果异常好，不要进入下一阶段，先排查未来函数和时间错位测试。
- 如果新因子覆盖率极低，先看是否是已知数据起点问题，不要直接删除因子。

---

### 第 6 步：阶段 8，跑测试门禁

阶段 4-6 通过后，跑已有测试门禁：

```powershell
python -m pytest tests\test_data_expansion_config.py tests\test_index_quote_code.py tests\test_hk_hold_coverage.py tests\test_alt_data_pit.py tests\test_technical_factors.py -q
```

通过标准：

- 上述测试全部通过。
- PIT 时间约束测试通过。
- 技术因子时间边界测试通过。
- 全收益指数测试通过。
- 配置一致性测试通过。

失败处理：

- PIT 失败：必须先修 loader 或 processor，不能进入回测。
- 全收益指数失败：必须停止，不能用价格指数替代。
- 技术因子失败：先修实现或测试口径，再重建受影响因子面板。

---

### 第 7 步：阶段 7，信号、优化、回测、归因

只有阶段 6 和阶段 8 门禁通过后，才运行：

```powershell
python -m scripts.run_signal_combination
python -m scripts.run_portfolio_optimization
python -m scripts.run_backtest
python -m scripts.run_attribution
```

通过标准：

- 信号合成只使用训练期冻结参数和验证期确认。
- 优化约束、成本、T+1 成交、涨跌停和停牌处理不被绕过。
- 回测仍然不进入 2023-2025 测试集。
- 若结果明显优于旧结果，先怀疑未来函数，而不是直接收尾。

失败处理：

- 优化失败：检查 L1/L2/L3 fallback 是否正确触发并记录。
- 回测指标异常好：检查 `shift_result.csv`、交易日期对齐、T+1 开盘成交。
- 回撤或换手异常：先检查成本、权重约束和调仓频率。

---

### 第 8 步：阶段 9，文档同步

最后同步：

- `PROJECT_PLAN_v1.1.md`
- `README.md`
- `teach/data_expansion_plan/phase_plan.md`
- 阶段执行记录
- 覆盖率报告
- 因子评估报告摘要

通过标准：

- 文档中的训练/验证/测试切分与 `src/config.py` 一致。
- 文档明确记录 `margin` 2013 起的限制。
- 文档明确记录新增 12 类数据和 15 个备选因子。
- 文档明确记录本次未运行 2023-2025 测试集。

---

## 5. 禁止事项

在完成上述门禁前，禁止执行：

```powershell
python -m scripts.run_test_pipeline
```

禁止把阶段 5 或阶段 6 扩展到 2023-2025 测试期，除非进入项目末期测试集评估，并且严格遵守：

- 测试集运行次数不超过 3 次。
- git commit message 必须包含 `[TEST_SET_RUN_N]`。
- 测试集产物必须与训练/验证产物隔离。

---

## 6. 最短可执行命令清单

如果只看命令，顺序如下：

```powershell
# 0. 冻结
git status --short

# 1. 阶段 4：重建 processed
python -m scripts.csv_to_parquet all

# 2. 阶段 4 后门禁
python -m pytest tests\test_index_quote_code.py tests\test_data_expansion_config.py tests\test_hk_hold_coverage.py tests\test_alt_data_pit.py -q

# 3. 修复技术因子口径后门禁
python -m pytest tests\test_technical_factors.py -q

# 4. 阶段 5 dry-run
python -m scripts.build_factor_panels --factor-set all --dry-run

# 5. 阶段 5：全量重建因子面板，禁止 --resume
python -m scripts.build_factor_panels --factor-set all

# 6. 阶段 6：因子评估，强制重算 forward return
python -m scripts.run_factor_evaluation --recompute-fwd-ret --run-id expand-train-2012-v1

# 7. 全部门禁测试
python -m pytest tests\test_data_expansion_config.py tests\test_index_quote_code.py tests\test_hk_hold_coverage.py tests\test_alt_data_pit.py tests\test_technical_factors.py -q

# 8. 阶段 7：信号、优化、回测、归因
python -m scripts.run_signal_combination
python -m scripts.run_portfolio_optimization
python -m scripts.run_backtest
python -m scripts.run_attribution
```

---

## 7. 当前下一步

当前应执行：

```powershell
python -m scripts.csv_to_parquet all
```

执行完成后，优先查看：

```text
reports/processed_coverage_report.md
```

并运行阶段 4 门禁测试。只有确认 processed 已经切到 2012 训练口径后，才能继续阶段 5。
