# 训练数据扩展 + 新增字段整合计划 v2.1（修正版）

## 0. 目标与边界

本计划同时完成两件事：

1. **时间扩展**：训练/验证链路从旧口径 `2016-2021 / 2022` 扩展为 `2012-2020 / 2021-2022`。
2. **字段与品类扩展**：在现有 Tushare/TinyShare 数据基础上，新增可用于后续因子研究的数据接口和字段。

样本切分：

```text
训练期：2012-01-01 至 2020-12-31
验证期：2021-01-01 至 2022-12-31
测试期：2023-01-01 至 2025-12-31，不在本计划中运行
```

下载范围：

```text
MARKET_START：2011-01-01  # 行情、持仓、交易、事件数据
EARLY_START ：2010-01-01  # 财务、股东、分析师数据的 PIT 缓冲
MARKET_END  ：2025-12-31
```

硬边界：

- 不运行 `scripts/run_test_pipeline.py`。
- 不用价格指数替代 `H00905.CSI` 全收益指数。
- 不把 `EARLY_START` 到 `TRAIN_START` 之间的数据作为训练标签，只作为历史窗口和 PIT 缓冲。
- 新字段必须进入 `raw -> processed -> loader -> factor` 的完整链路，不能只下载 raw 后就声称完成。

## 1. 当前状态

已完成：

- `src/config.py` 已切到新日期口径。
- `CSI500_WEIGHT_FILE` 已指向 `data/csi500_index_weight_201201_202512.csv`。
- `download_tushare.py` / `download_supplement.py` 已切到 `tinyshare`。
- `TINYSHARE_TOKEN` 已配置。
- 核心接口和补充接口 test 已通过。

尚未完成：

- 新权重文件 `data/csi500_index_weight_201201_202512.csv` 尚不存在。
- `scripts/download_alternative_data.py` 尚不存在。
- 现有下载脚本尚未实现向前补历史，仍是“文件存在即跳过”。
- 新增数据的 `csv_to_parquet` processor、loader、因子实现、测试尚未实现。

因此：**当前不能直接运行 all 下载或重建流水线**，必须先完成阶段 1-3 的修复。

## 2. 新增接口范围

本计划新增 **12 个 TinyShare/Tushare 风格接口**。技术指标不走外部预计算接口，直接从后复权行情自行计算。

### 2.1 新增外部接口

| 模块名 | API | 用途 | PIT/时间口径 | 优先级 |
|---|---|---|---|---|
| `hk_hold` | `pro.hk_hold` | 北向持仓 | `trade_date <= T` | 高 |
| `analyst_rc` | `pro.report_rc` | 分析师研报/预期 | `report_date <= T` | 高 |
| `share_float` | `pro.share_float` | 解禁压力 | `ann_date <= T` 且 `float_date > T` | 中 |
| `holder_trade` | `pro.stk_holdertrade` | 高管/股东增减持 | `ann_date <= T` | 中 |
| `cyq_perf` | `pro.cyq_perf` | 筹码/成本分布摘要 | `trade_date <= T` | 中 |
| `pledge_stat` | `pro.pledge_stat` | 股权质押风险 | `end_date <= T` | 中 |
| `hsgt_flow` | `pro.moneyflow_hsgt` | 北向/南向市场级资金流 | `trade_date <= T` | 中 |
| `top_list` | `pro.top_list` | 龙虎榜个股事件 | `trade_date <= T` | 低 |
| `top_inst` | `pro.top_inst` | 龙虎榜机构席位 | `trade_date <= T` | 低 |
| `block_trade` | `pro.block_trade` | 大宗交易 | `trade_date <= T` | 低 |
| `stk_surv` | `pro.stk_surv` | 机构调研 | `surv_date <= T` | 低 |
| `fina_mainbz` | `pro.fina_mainbz` | 主营业务构成 | 需派生 PIT 日期，默认仅下载研究 | 低 |

### 2.2 明确不下载的数据

- `stk_factor_pro`：技术指标从 `daily_quote` 的后复权 OHLCV 自行计算，避免黑盒口径。
- `cyq_chips`：每日筹码价格区间明细数据量过大，先只用 `cyq_perf` 摘要。
- `ccass_hold_detail`：机构席位明细历史覆盖和权限不稳定，暂不纳入。

## 3. 实测字段修正

以下字段来自 tinyshare 抽样测试，计划和实现必须以这些字段为准。

| API | 实测可用字段 | 原计划修正 |
|---|---|---|
| `hk_hold` | `code`, `trade_date`, `ts_code`, `name`, `vol`, `ratio`, `exchange` | 用 `exchange`，不是 `exchange_type` |
| `report_rc` | `ts_code`, `name`, `report_date`, `report_title`, `org_name`, `author_name`, `eps`, `pe`, `rating`, `max_price`, `min_price` 等 | 用 `eps`，不是 `eps_y1` |
| `share_float` | `ts_code`, `ann_date`, `float_date`, `float_share`, `float_ratio`, `holder_name`, `share_type` | 用 `share_type`，不是 `float_type` |
| `stk_holdertrade` | `ts_code`, `ann_date`, `holder_name`, `holder_type`, `in_de`, `change_vol`, `change_ratio`, `after_share`, `after_ratio`, `avg_price` | 与计划一致 |
| `cyq_perf` | `ts_code`, `trade_date`, `cost_5pct`, `cost_15pct`, `cost_50pct`, `cost_85pct`, `cost_95pct`, `weight_avg`, `winner_rate` | 用 `winner_rate`，不是 `winner` |
| `pledge_stat` | `ts_code`, `end_date`, `pledge_count`, `unrest_pledge`, `rest_pledge`, `total_share`, `pledge_ratio` | 删除 `qd_pct` 要求 |
| `moneyflow_hsgt` | `trade_date`, `ggt_ss`, `ggt_sz`, `hgt`, `sgt`, `north_money`, `south_money` | 与计划一致 |
| `top_list` | `trade_date`, `ts_code`, `name`, `close`, `pct_change`, `turnover_rate`, `amount`, `net_amount`, `net_rate`, `reason` | 与计划一致 |
| `top_inst` | `trade_date`, `ts_code`, `exalter`, `buy`, `sell`, `net_buy`, `side`, `reason` | 与计划一致 |
| `block_trade` | `ts_code`, `trade_date`, `price`, `vol`, `amount`, `buyer`, `seller` | `premium` 需后续自行计算 |
| `stk_surv` | `ts_code`, `name`, `surv_date`, `fund_visitors`, `rece_place`, `rece_mode`, `rece_org`, `org_type`, `comp_rece` | `org_cnt` 需后续聚合 |
| `fina_mainbz` | `ts_code`, `end_date`, `bz_item`, `bz_code`, `bz_sales`, `bz_profit`, `bz_cost`, `curr_type` | 无 `ann_date` / `bz_type`，不能直接进 PIT 因子 |

## 4. 现有表字段扩展

新增品类之外，现有表也需要明确“更多字段”的保留策略。

原则：

- 不全量保留所有 raw 字段，避免列口径不明和磁盘膨胀。
- 每个新增字段必须说明用途、单位和 PIT 口径。
- `csv_to_parquet.py` 使用字段白名单，但字段不存在时只记录 warning，不应静默生成全空列。

建议扩展字段：

| 表 | 现有用途 | 必须额外保留 |
|---|---|---|
| `daily_basic` | 估值、市值、中性化、换手 | `total_share`, `float_share`。两者用于 PMO/ABN TURN 的月度成交量/流通股本口径；`circ_mv` 已保留，可用于流通市值版 EP |
| `financial_pit` | TTM 收益、现金流、资产负债、投资/资产类因子 | `total_revenue`, `operate_profit`, `oper_cost`, `sell_exp`, `admin_exp`, `fin_exp`, `income_tax`, `ebit`, `ebitda`, `total_hldr_eqy_exc_min_int`, `money_cap`, `inventories`, `accounts_receiv`, `prepayment`, `total_cur_assets`, `fix_assets`, `cip`, `intan_assets`, `goodwill`, `total_assets`, `total_liab`, `st_borr`, `lt_borr`, `notes_payable`, `bond_payable`, `n_cashflow_inv_act`, `n_cash_flows_fnc_act` |
| `indicator_pit` | ROE、ROA、毛利率、成长、债务和现金流质量 | `netprofit_margin`, `grossprofit_margin`, `debt_to_assets`, `current_ratio`, `quick_ratio`, `ocf_to_debt`, `ocf_to_shortdebt`, `roe_yearly`, `roa_yearly`, `roa2_yearly`, `assets_yoy`, `equity_yoy`, `q_roe`, `q_dt_roe`, `q_ocf_to_sales`, `q_sales_yoy` |
| `moneyflow` | 主力资金流 | 当前大单/超大单字段可先保留；如 raw 存在中单/小单字段，可作为研究字段保留但不进入默认因子 |
| `dividend_pit` | 分红事件 | `cash_div`, `cash_div_tax`, `stk_div`, `stk_bo_rate`, `ex_date`, `imp_ann_date`, `div_proc` |

这些字段已在当前 raw 样例中出现；实现时仍应以字段存在性探针为准，缺失字段只记录 warning，不得静默生成全空列。

## 5. 阶段 0：冻结现状

目标：确保当前工作可回滚。

动作：

- 新建或确认分支：`feature/expand-train-2012`
- 记录当前 `git status --short`
- 不清理用户已有改动，不 reset
- 记录当前 `reports/` 和 `data/processed/run_manifests/` 摘要
- 不运行测试集流水线

验收：

- 旧结果和新计划的差异有记录。
- `PROJECT_PLAN_v1.1.md` 说明当前准备进入数据扩展，不再把旧切分当作当前目标。

## 6. 阶段 1：可得性探针

目标：只抽样测试，不下载全量。

### 6.1 历史覆盖探针

必须检查：

- `index_weight(000905.SH)`：2012-2015 每个调仓月权重合计约 100。
- `index_daily(H00905.CSI)`：2011 起必须有全收益指数。
- `daily` / `adj_factor` / `daily_basic`：2011 起可取，`free_share` 存在。
- `income` / `balancesheet` / `cashflow` / `fina_indicator`：`ann_date` 可取，2010 起有 PIT 缓冲。

停止条件：

- `H00905.CSI` 缺失严重。
- `index_weight` 2012-2015 缺失严重或权重合计明显不接近 100。
- `daily_basic.free_share` 不可用。

### 6.2 新增接口探针

必须用第 3 节的实测字段做校验。

停止条件：

- `hk_hold` 完全不可用。
- `report_rc` 完全不可用。
- `share_float.ann_date` 不可用。

记录但不停止：

- `hk_hold` 在 2014-11-17 前为空。
- `cyq_perf` 早期无数据。
- `fina_mainbz` 无 `ann_date`，只能先下载研究，不能直接生成 PIT 因子。

输出：

```text
reports/data_expansion_probe_2010_2025.md
```

## 7. 阶段 2：代码准备

本阶段只改代码，不跑全量下载。

### 7.1 `src/config.py`

确认日期口径已正确。

新增：

```python
ALT_DATA_SUBDIRS: list[str] = [
    "hk_hold",
    "analyst_rc",
    "share_float",
    "holder_trade",
    "cyq_perf",
    "pledge_stat",
    "hsgt_flow",
    "top_list",
    "top_inst",
    "block_trade",
    "stk_surv",
    "fina_mainbz",
]
```

### 7.2 现有下载脚本补历史

`download_tushare.py` 和 `download_supplement.py` 必须新增“向前补历史”能力，不能继续用文件存在即跳过。

新增工具函数：

```python
def _get_existing_min_date(subdir: str, name: str, date_col: str) -> str | None:
    ...

def _backfill_needed(subdir: str, name: str, target_start: str, date_col: str) -> bool:
    ...

def _save_merged(subdir: str, name: str, new_df: pd.DataFrame, pk_cols: list[str]) -> None:
    ...
```

主键规则：

| 模块 | 日期列 | 主键 |
|---|---|---|
| `daily_quote` | `trade_date` | `["trade_date"]` |
| `adj_factor` | `trade_date` | `["trade_date"]` |
| `daily_basic` | `trade_date` | `["trade_date"]` |
| `suspend` | `trade_date` | `["trade_date"]` |
| `financial_income` | `ann_date` | `["ann_date", "end_date"]` |
| `financial_balance` | `ann_date` | `["ann_date", "end_date"]` |
| `financial_cashflow` | `ann_date` | `["ann_date", "end_date"]` |
| `financial_indicator` | `ann_date` | `["ann_date", "end_date"]` |
| `holder_number` | `ann_date` | `["ann_date", "end_date"]` |
| `margin` | `trade_date` | `["trade_date"]` |
| `moneyflow` | `trade_date` | `["trade_date"]` |

### 7.3 新建 `scripts/download_alternative_data.py`

要求：

- 使用 `tinyshare`。
- 从 `src.config` 读取日期，不写死 `20110101`、`20140101`。
- 支持命令：

```text
python -m scripts.download_alternative_data test
python -m scripts.download_alternative_data all
python -m scripts.download_alternative_data hk_hold
```

下载方式：

| 模块 | 存储方式 |
|---|---|
| `hk_hold` | 按股票 CSV |
| `analyst_rc` | 按股票 CSV |
| `share_float` | 按股票 CSV |
| `holder_trade` | 按股票 CSV |
| `cyq_perf` | 按股票 CSV |
| `pledge_stat` | 按股票 CSV |
| `hsgt_flow` | 单 CSV |
| `top_list` | 按交易日 CSV |
| `top_inst` | 按交易日 CSV |
| `block_trade` | 按交易日 CSV |
| `stk_surv` | 按股票 CSV |
| `fina_mainbz` | 按股票 CSV |

验收：

- `python -m scripts.download_alternative_data test` 通过。
- 字段校验使用第 3 节修正后的字段。

### 7.4 `csv_to_parquet.py`

新增 processor：

| Parquet | 来源 | 关键处理 |
|---|---|---|
| `hk_hold.parquet` | `raw/hk_hold/` | `(trade_date, ts_code)` 去重，保留 `vol`, `ratio`, `exchange` |
| `analyst_rc_pit.parquet` | `raw/analyst_rc/` | `report_date` 作 PIT 时间戳 |
| `share_float.parquet` | `raw/share_float/` | 保留 `ann_date`, `float_date`, `float_ratio`, `share_type` |
| `holder_trade_pit.parquet` | `raw/holder_trade/` | `ann_date` 作 PIT 时间戳 |
| `cyq_perf.parquet` | `raw/cyq_perf/` | 保留 `winner_rate`, `weight_avg`, `cost_5pct` |
| `pledge_stat.parquet` | `raw/pledge_stat/` | `end_date` 作统计日期 |
| `moneyflow_hsgt.parquet` | `raw/hsgt_flow.csv` | 按 `trade_date` |
| `top_list.parquet` | `raw/top_list/` | 合并交易日文件 |
| `top_inst.parquet` | `raw/top_inst/` | 合并交易日文件 |
| `block_trade.parquet` | `raw/block_trade/` | `premium` 不从 raw 读取，后续如需自行计算 |
| `stk_surv.parquet` | `raw/stk_surv/` | 后续聚合 `org_cnt` |
| `fina_mainbz_raw.parquet` | `raw/fina_mainbz/` | 只作为 raw-like processed，暂不直接进 PIT 因子 |

命令口径：

```text
python -m scripts.csv_to_parquet all
```

不要写成 `--all`，除非额外实现兼容参数。

### 7.5 loader 与因子分组

新增 loader：

- `load_hk_hold`
- `load_analyst_rc_pit`
- `load_share_float`
- `load_holder_trade_pit`
- `load_cyq_perf`
- `load_pledge_stat`
- `load_moneyflow_hsgt`
- `load_top_list`
- `load_top_inst`
- `load_block_trade`
- `load_stk_surv`

`fina_mainbz` 暂不提供默认因子 loader，除非已实现保守 PIT 日期派生。

因子构建命令不要直接使用当前不存在的 `--group`。两种可选方案：

方案 A：新增 CLI：

```text
python -m scripts.build_factor_panels --factor-set original
python -m scripts.build_factor_panels --factor-set alternative
```

方案 B：沿用当前 CLI：

```text
python -m scripts.build_factor_panels --factors <因子名列表>
```

推荐方案 A，但必须先实现后再写入执行日历。

## 8. 阶段 3：下载

### 8.1 现有数据向前补齐

前置：阶段 2.2 已实现。

运行：

```text
python -m scripts.download_tushare all
python -m scripts.download_supplement all
```

验收：

- 新权重文件 `data/csi500_index_weight_201201_202512.csv` 存在。
- `index_daily.csv` 覆盖 2011 起，且为 `H00905.CSI`。
- `daily_quote` / `daily_basic` / `adj_factor` 每只股票覆盖向前延伸。
- 财务表 `ann_date` 覆盖 2010 起。
- 原 2016-2025 数据去重后不得异常减少。

### 8.2 新增接口下载

前置：`download_alternative_data.py test` 已通过。

运行：

```text
python -m scripts.download_alternative_data all
```

可按优先级分批：

第一批：

```text
hk_hold
analyst_rc
share_float
holder_trade
```

第二批：

```text
cyq_perf
pledge_stat
hsgt_flow
```

第三批：

```text
top_list
top_inst
block_trade
stk_surv
fina_mainbz
```

验收：

- 生成 `reports/alt_data_coverage_report.md`。
- 失败文件列表写入 `logs/alt_data_failed.txt`。
- 高优先级模块完成前，不进入阶段 4。

## 9. 阶段 4：processed 重建

前置：

- 阶段 3.1 完成。
- 阶段 3.2 第一批和第二批完成。

运行：

```text
python -m scripts.csv_to_parquet all
```

验收：

- `daily_quote.parquet` 覆盖 2011 起。
- `daily_basic.parquet` 含 `free_share`, `free_float_mv`, `log_free_float_mv`。
- `index_quote.parquet` 硬校验为 `H00905.CSI`。
- `index_member.parquet` 覆盖 2012 起。
- `financial_pit.parquet` / `indicator_pit.parquet` 无空 `pit_date`。
- 新增 Parquet 文件按第 7.4 节生成。
- 输出 `reports/processed_coverage_report.md`。

## 10. 阶段 5：因子面板

### 10.1 原有因子

运行方式取决于阶段 7.5 的实现。

如果实现 `--factor-set`：

```text
python -m scripts.build_factor_panels --factor-set original --resume
```

如果未实现：

```text
python -m scripts.build_factor_panels --factors <原有因子列表> --resume
```

验收：

- 原有因子覆盖 `2012-2022` 调仓日。
- 早期 NaN 率记录在 `factor_panel_diagnostics.parquet`。

### 10.2 新增候选因子

技术因子：用 pandas/numpy 自行实现，不引入 `pandas_ta`。

| 因子名 | 来源 | 核心口径 |
|---|---|---|
| `macd_cross` | `daily_quote.close_adj` | 12/26/9 EMA，T 日只用 T 及以前 |
| `rsi_6` | `daily_quote.close_adj` | 6 日 RSI |
| `rsi_12` | `daily_quote.close_adj` | 12 日 RSI |
| `boll_pct` | `daily_quote.close_adj` | 20 日均线和 2 倍标准差 |
| `obv_chg_20d` | `daily_quote.close_adj + vol` | OBV 20 日变化 |

外部数据因子：

| 因子名 | 来源 | 说明 |
|---|---|---|
| `hk_hold_ratio` | `hk_hold.ratio` | 北向持仓比例 |
| `hk_hold_chg` | `hk_hold.ratio` / `vol` | 北向持仓变化 |
| `analyst_eps_revision` | `analyst_rc_pit.eps` | 研报 EPS 变化，覆盖不足则不纳入 |
| `analyst_rating_chg` | `analyst_rc_pit.rating` | 评级变化，需先做评级映射 |
| `float_pct_30d` | `share_float.float_ratio` | 已公告未来 30 日解禁比例，要求 `ann_date <= T < float_date` |
| `insider_net_buy` | `holder_trade_pit.change_vol/change_ratio` | 股东增减持净额 |
| `chip_winner_rate` | `cyq_perf.winner_rate` | 筹码获利比例 |
| `cost_deviation` | `cyq_perf.weight_avg/cost_50pct` | 成本偏离 |
| `pledge_ratio` | `pledge_stat.pledge_ratio` | 股权质押比例 |
| `north_flow_5d` | `moneyflow_hsgt.north_money` | 市场级北向资金流，需明确是全市场因子或环境变量 |

暂不默认纳入：

- `fina_mainbz` 因子：除非已从财务 PIT 表派生保守可用日。
- `stk_surv` 因子：需先证明覆盖率和事件含义稳定。
- `top_list` / `top_inst` / `block_trade` 因子：事件稀疏，先做覆盖报告，不直接进入默认因子池。

验收：

- 新增因子有效样本起点和 NaN 率写入诊断。
- `cyq_perf` 类因子标注“2018 起才有较完整样本”。
- `hk_hold` 类因子标注“2014-11 前无数据”。
- 所有新增因子仍走横截面去极值、标准化、行业+自由流通市值中性化。

## 11. 阶段 6：因子评价

原有因子：

```text
python -m scripts.run_factor_evaluation --output-dir reports/factor_evaluation
```

新增因子：

如果实现了独立输入目录或 factor-set：

```text
python -m scripts.run_factor_evaluation --factor-set alternative --output-dir reports/factor_evaluation_alt
```

否则先不要用命令行硬跑，需先实现评价脚本的因子集选择逻辑。

训练期分段必须改为：

```text
2012-2014
2015-2017
2018-2020
```

验收：

- `seg_ic_ir.csv` 不再出现旧分段 `2016-2018 / 2019-2021`。
- `valid_comparison.csv` 使用 2021-2022。
- `factor_summary.csv.final_include` 与 `final_factors.json` 完全一致。
- 合并后的候选因子总数不超过 30 个。

## 12. 阶段 7：信号、优化、回测、归因

前置：

- 原有因子评价完成。
- 新增因子评价完成或明确暂不纳入。
- `final_factors.json` 已冻结。

运行：

```text
python -m scripts.run_signal_combination
python -m scripts.run_portfolio_optimization
python -m scripts.run_backtest --allow-noncompliant-weights
python -m scripts.run_attribution
```

验收：

- 合成信号覆盖 2012-2022。
- 协方差缓存覆盖 2012-2022。
- 验证期回测只覆盖 2021-2022。
- L1/L2/L3 fallback 分布写入报告。
- 不触碰测试集。

## 13. 阶段 8：测试门禁

必须运行：

```text
python -m pytest tests/test_pit.py -q
python -m pytest tests/test_preprocess.py -q
python -m pytest tests/test_factor_time_boundary.py -q
python -m pytest tests/test_evaluation.py -q
python -m pytest tests/test_signal_combiner.py -q
python -m pytest tests/test_optimizer.py -q
python -m pytest tests/test_transaction.py -q
python -m pytest tests/test_backtest_engine.py -q
python -m pytest tests/test_backtest_metrics.py -q
python -m pytest tests/test_pipeline_release_gates.py -q
```

新增测试：

- `tests/test_alt_data_pit.py`
  - `analyst_rc.report_date <= T`
  - `holder_trade.ann_date <= T`
  - `share_float.ann_date <= T < float_date`
- `tests/test_technical_factors.py`
  - 技术指标只用后复权价和 T 日以前窗口
  - 后移一期 IC 应明显下降
- `tests/test_hk_hold_coverage.py`
  - 2014-11 前空值为预期
  - 北向标的 `ratio` 覆盖率达标
- `tests/test_index_quote_code.py`
  - `index_quote.parquet` 只能是 `H00905.CSI`
- `tests/test_data_expansion_config.py`
  - 配置日期、manifest、报告区间一致

## 14. 阶段 9：文档同步

必改：

- `PROJECT_PLAN_v1.1.md`
- `README.md`
- notebook 标题和说明
- 回测报告模板
- run manifest 元数据

必须记录：

- 新切分：`2012-2020 / 2021-2022`
- `EARLY_START=2010` 只作历史缓冲
- 新增 12 个外部接口
- 技术指标自行计算，不引入 `pandas_ta`
- `fina_mainbz` 当前无直接 PIT 日期，不默认用于策略
- 测试集 2023-2025 尚未运行

## 15. 推荐执行顺序

1. 阶段 0：冻结现状。
2. 阶段 1：历史覆盖和新增接口探针。
3. 阶段 2：实现补历史、新增下载脚本、Parquet processor、loader、CLI。
4. 阶段 3.1：补齐现有数据历史。
5. 阶段 3.2：下载新增接口，高优先级先完成。
6. 阶段 4：processed 全量重建。
7. 阶段 5：因子面板。
8. 阶段 6：因子评价。
9. 阶段 7：训练/验证回测和归因。
10. 阶段 8：测试门禁。
11. 阶段 9：文档同步。

任何阶段验收失败，停在当前阶段修复，不跳步继续。

## 16. 当前不建议直接运行的命令

在阶段 2 完成前，不建议运行：

```text
python -m scripts.download_tushare all
python -m scripts.download_supplement all
python -m scripts.download_alternative_data all
python -m scripts.csv_to_parquet all
python -m scripts.build_factor_panels --factor-set original
python -m scripts.run_factor_evaluation --factor-set alternative
```

原因：

- 补历史逻辑尚未实现。
- 新增下载脚本尚未实现。
- 新增 Parquet processor 和 loader 尚未实现。
- `--factor-set` 尚未实现。
