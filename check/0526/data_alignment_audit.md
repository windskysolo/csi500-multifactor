# 阶段 3：数据口径和时间对齐审查

- 生成时间：2026-05-26 19:34:05
- 正式测试集：未运行；forward return 手工复算样本只选 exit_date <= VALID_END 的验证期标签。

## 3.1 基准口径

- `pipeline_b_lam0050`: index_quote has nav=True, benchmark vs normalized nav max diff=0, span=2021-01-04~2022-12-30
- `rolling_48m`: index_quote has nav=True, benchmark vs normalized nav max diff=0, span=2021-01-04~2022-12-30
- 代码扫描候选：`stage3_benchmark_code_scan.csv`，疑似 close 绕过候选 0 条；需要人工看上下文，脚本不把注释/数据质量检查当作违规。

## 3.2 Forward Return 标签

- `fwd_ret_shape`: 131x1329 [INFO] nan
- `fwd_ret_date_min`: 2012-01-31 [INFO] nan
- `fwd_ret_date_max`: 2022-11-30 [INFO] nan
- `fwd_index_subset_of_index_member_rebalance_dates`: True [PASS] nan
- `sample_factor_dates_cover_fwd_dates`: True [PASS] sample=data/processed/factor_panels/accrual.parquet
- `labels_exit_after_valid_end_count`: 1 [WARN] 这些标签跨过 VALID_END；本审计不读取对应区间价格手工复算。
- 手工复算样本：30 行，max_abs_diff=0。

## 3.3 PIT 和因子面板

- PIT loader 抽样：FAIL=0, WARN=0，详见 `stage3_pit_snapshot_audit.csv`。
- 因子面板测试期日期行数：0，详见 `stage3_factor_panel_audit.csv`。
- PIT/预处理源码关键语句未命中数量：0，详见 `stage3_pit_source_scan.csv`。

## 阶段 3 初步判定

- 基准 NAV 口径与现有验证期 NAV 文件一致，未发现用价格指数替代全收益基准的证据。
- 已抽样验证 fwd_ret_panel 的 T+1 开盘到 T'+1 开盘口径。
- 发现 1 个 fwd_ret 标签的 exit_date 超过 VALID_END；这不是正式测试集回测，但会影响验证期 IC/训练标签边界解释，应在阶段 5 继续检查训练窗口是否按 exit_date purge。
- PIT 抽样和因子面板日期覆盖未发现明显未来数据。