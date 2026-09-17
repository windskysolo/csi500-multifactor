# 训练/验证流水线刷新记录

> 记录时间：2026-05-20 21:50  
> 目的：在不运行正式测试集的前提下，使用当前代码重新生成并覆盖训练/验证阶段公共产物。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未生成 `data/processed/test_run_*`，未生成 `data/processed/factor_panels_test_run_*`。
> 边界说明：本次从 `convert` 阶段开始，未重新联网下载 Tushare raw 缓存；已覆盖的是由现有 raw 缓存派生的训练/验证公共产物。

---

## 本次执行命令

```text
python -m scripts.run_pipeline --from-stage convert --skip quality
```

执行范围：

```text
convert -> quality -> factors -> evaluate -> signal -> portfolio -> backtest -> attribution
```

其中 `quality` 阶段因当前项目尚未实现 `scripts/run_data_quality.py`，按 pipeline 设计通过 `--skip quality` 显式跳过。

---

## 执行结论

本次训练/验证流水线执行成功，退出码为 0。

最新 run manifest：

```text
data/processed/run_manifests/run_20260520_212950.json
```

manifest 关键字段：

```text
success=True
pipeline=training_validation
stages_executed=['convert', 'factors', 'evaluate', 'signal', 'portfolio', 'backtest', 'attribution']
train_end=2021-12-31
valid_end=2022-12-31
test_set_run_count=1
```

这说明本次刷新没有新增正式测试集运行次数；git 历史仍只有：

```text
80708d4 [TEST_SET_RUN_1] 测试集评估完成 (2023-2025)
```

---

## 覆盖产物确认

本次已刷新并覆盖的主要训练/验证公共产物包括：

```text
data/processed/daily_quote.parquet                  2026-05-20 21:30
data/processed/daily_basic.parquet                  2026-05-20 21:30
data/processed/index_quote.parquet                  2026-05-20 21:30
data/processed/index_member.parquet                 2026-05-20 21:31
data/processed/stock_status.parquet                 2026-05-20 21:31
data/processed/factor_panels/*.parquet              2026-05-20 21:35
data/processed/factor_panel_diagnostics.parquet     2026-05-20 21:35
reports/factor_evaluation/*.csv/json/md             2026-05-20 21:35-21:36
data/processed/composite_signal_*.parquet           2026-05-20 21:36
data/processed/portfolio_weights_*.parquet          2026-05-20 21:42
data/processed/backtest_*.parquet                   2026-05-20 21:42
data/processed/brinson_attribution.parquet          2026-05-20 21:42
data/processed/factor_attribution.parquet           2026-05-20 21:42
data/processed/attribution_metadata.json            2026-05-20 21:42
reports/analysis_v2_results.md                      2026-05-20 21:42
```

范围检查：

```text
factor_file_count=27
factor_panels: 2016-01-29 -> 2022-12-30, shape=(84, 1065)
composite_signal_ic_ir: 2016-01-29 -> 2022-12-30, shape=(84, 1065)
portfolio_weights_optimized: 2016-01-29 -> 2022-12-30, shape=(84, 1069)
backtest_nav: 2022-01-04 -> 2022-12-30, shape=(242, 3)
```

未发现测试集专用目录：

```text
data/processed/test_run_*
data/processed/factor_panels_test_run_*
```

---

## 当前验证期结果

验证期为 2022-01-01 至 2022-12-31。最新回测指标：

```text
V1 Baseline:
annualized excess return = +1.26%
information ratio        = +0.173
excess max drawdown      = -11.40%
annual turnover          = 899%

V2 Optimized:
annualized excess return = -0.86%
information ratio        = -0.191
excess max drawdown      = -7.32%
annual turnover          = 863%
```

硬指标检查中，V2 当前验证期 IR 未达到 0.5；这属于策略表现问题，不是程序运行失败。

---

## 测试结果

```text
python -m pytest tests/ -q --basetemp=.codex_tmp_post_pipeline
```

结果：

```text
201 passed, 2 warnings
```

两个 warning：

```text
ConstantInputWarning: 测试中构造的常量输入导致 Spearman 相关系数未定义
PytestCacheWarning: .pytest_cache 写入仍有 WinError 5 权限警告
```

这两个 warning 未导致测试失败，但 `.pytest_cache` 权限问题仍未完全清理。

---

## 本次仍需披露的风险

1. `quality` 阶段仍是占位阶段，本次是显式跳过，不等于已完成自动化数据质量检查。
2. 组合优化仍有 15/84 期 `constraint_compliant=False`，pipeline 通过 `--allow-noncompliant-weights` 明示接受并继续回测，报告中必须披露该限制。
3. 归因阶段有 Brinson reconciliation warning：V2 月度归因最大偏差约 `0.4247%`，因子归因会计恒等式通过。
4. 测试期因子面板仍未生成；正式 Run #2 前仍需在 run-id 纪律下生成 `factor_panels_test_run_2/`，然后才能运行 `scripts/run_test_pipeline.py --run-id 2`。
