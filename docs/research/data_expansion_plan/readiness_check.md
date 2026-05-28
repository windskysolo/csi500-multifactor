# 数据扩展计划可执行性检查

检查日期：2026-05-21

## 结论

`phase_plan.md` 的方向基本合理，但**当前不能直接运行**。原因不是 tinyshare 授权或基础接口问题，而是计划中的新增脚本、参数、字段名和现有代码尚未对齐。

当前 tinyshare 核心接口和补充接口已测试通过；新增接口抽样测试显示大部分接口可达，但多处字段名与计划不一致，必须先修订计划和实现代码。

## 已通过项

- `src/config.py` 日期切分已切到：
  - `MARKET_START = 2011-01-01`
  - `EARLY_START = 2010-01-01`
  - `TRAIN_START = 2012-01-01`
  - `TRAIN_END = 2020-12-31`
  - `VALID_START = 2021-01-01`
  - `VALID_END = 2022-12-31`
- `scripts/download_tushare.py` 和 `scripts/download_supplement.py` 已切到 `tinyshare`。
- `TINYSHARE_TOKEN` 已配置为用户级环境变量。
- 核心接口探针已通过：
  - trade_cal / daily / adj_factor / daily_basic / H00905.CSI / suspend / limit_list_d / income / fina_indicator / stock_basic
- 补充接口探针已通过：
  - margin_detail / moneyflow / stk_holdernumber / dividend

## 阻塞问题

### 1. 新增数据脚本尚不存在

计划要求：

```text
scripts/download_alternative_data.py
```

当前检查结果：文件不存在。

影响：

- `python scripts/download_alternative_data.py test` 无法运行。
- `python scripts/download_alternative_data.py all` 无法运行。
- 新增接口数据不会进入 `data/raw/`。

### 2. 现有下载脚本尚未实现向前补历史

计划要求新增：

- `_get_existing_min_date`
- `_backfill_needed`
- `_save_merged`

当前检查结果：

- `scripts/download_tushare.py` 仍使用 `_done()`：只要文件存在就跳过。
- `scripts/download_supplement.py` 仍使用 `_done()`：只要文件存在就跳过。

影响：

- 现有 2016-2025 文件不会自动补 2011-2015。
- 如果直接跑 `python scripts/download_tushare.py all`，大量已有股票会被错误跳过，历史扩展不完整。

### 3. 新成分权重文件路径已切换，但文件尚不存在

当前配置：

```python
CSI500_WEIGHT_FILE = data/csi500_index_weight_201201_202512.csv
```

当前检查结果：

- `data/csi500_index_weight_201201_202512.csv` 不存在。
- 旧文件 `data/csi500_index_weight_201601_202512.csv` 存在。

影响：

- `download_tushare.py all` 会先尝试下载新权重文件，这是合理的。
- 但在新权重文件生成前，依赖 `_stock_list()` 的补充下载脚本不能直接跑 `all`。

### 4. 计划中的 CLI 参数当前未实现

计划命令：

```text
python scripts/build_factor_panels.py --group original
python scripts/build_factor_panels.py --group alternative
python scripts/run_factor_evaluation.py --group original
python scripts/run_factor_evaluation.py --group alternative
python scripts/csv_to_parquet.py --all
```

当前实际支持：

```text
build_factor_panels.py: --factors / --resume / --end-date / --dry-run
run_factor_evaluation.py: --output-dir / --no-plots / --run-id / --recompute-fwd-ret
csv_to_parquet.py: positional group: daily/index/industry/financial/status/universe/all
```

影响：

- `--group original/alternative` 直接运行会报错。
- `csv_to_parquet.py --all` 应改为 `python scripts/csv_to_parquet.py all`，或者代码新增 `--all` 兼容。

### 5. 新增接口字段名与 tinyshare 实际返回不一致

抽样测试结果如下。

| 接口 | 计划字段问题 | tinyshare 实际返回 |
|---|---|---|
| `hk_hold` | `exchange_type` 不存在 | 返回 `exchange` |
| `report_rc` | `eps_y1` 不存在 | 返回 `eps` 等字段 |
| `share_float` | `float_type` 不存在 | 返回 `share_type` |
| `cyq_perf` | `winner` 不存在 | 返回 `winner_rate` |
| `pledge_stat` | `qd_pct` 不存在 | 返回 `pledge_ratio`，未见 `qd_pct` |
| `block_trade` | `premium` 不存在 | 返回 `price`, `vol`, `amount`, `buyer`, `seller` |
| `stk_surv` | `org_cnt` 不存在 | 返回 `rece_org`, `org_type` 等，需要自行聚合 |
| `fina_mainbz` | `ann_date`, `bz_type` 不存在 | 返回 `end_date`, `bz_item`, `bz_code`, `bz_sales` 等 |

影响：

- 如果按计划中的字段做强校验，新增接口探针会失败。
- 如果按计划中的字段写 Parquet 转换，转换阶段会 KeyError 或得到全空字段。

### 6. 新增 Parquet 处理尚未实现

计划要求新增：

- `hk_hold.parquet`
- `analyst_rc_pit.parquet`
- `share_float.parquet`
- `holder_trade_pit.parquet`
- `cyq_perf.parquet`
- `pledge_stat.parquet`
- `moneyflow_hsgt.parquet`
- `top_list.parquet`
- `top_inst.parquet`
- `block_trade.parquet`

当前检查结果：

- `scripts/csv_to_parquet.py` 只处理现有 M1-M12。
- 新增表没有 processor。
- `src/data/loader.py` 没有新增 loader。

影响：

- 即使 raw 下载成功，也不会进入 processed 层。
- 因子层无法读取新增数据。

### 7. 技术因子依赖 `pandas_ta`，但项目未批准该新依赖

计划写法：

```python
import pandas_ta as ta
```

当前状态：

- `requirements.txt` 未包含 `pandas_ta`。
- 项目规范要求引入新依赖前必须说明理由并获得确认。

建议：

- 优先用 pandas/numpy 自行实现 MACD、RSI、BOLL、OBV，避免新增依赖。
- 若一定使用 `pandas_ta`，需要单独确认并更新依赖。

### 8. 计划中的数量表述不一致

当前 `phase_plan.md` 同时出现：

- “新增 13 个 Tushare Pro 接口”
- “新增 14 个候选因子”
- 实际新增接口表列出 12 个 API
- 技术因子 5 个，外部数据因子 10 个，合计 15 个候选因子

影响：

- 文档、实现、验收口径不一致。
- 后续 final_factors 数量控制容易混乱。

## 接口抽样测试摘要

新增接口抽样测试结果：

```text
hk_hold: 可达，但 exchange_type 应改为 exchange
report_rc: 可达，但 eps_y1 应改为 eps 或重新确认字段口径
share_float: 可达，但 float_type 应改为 share_type
stk_holdertrade: 可达，字段符合计划
cyq_perf: 可达，但 winner 应改为 winner_rate
pledge_stat: 可达，但 qd_pct 未返回
moneyflow_hsgt: 可达，字段符合计划
top_list: 可达，字段符合计划
top_inst: 可达，字段符合计划
block_trade: 可达，但 premium 未返回，需要自行计算
stk_surv: 可达，但 org_cnt 未返回，需要自行按期聚合
fina_mainbz: 可达，但 ann_date/bz_type 未返回，PIT 口径需重审
```

## 修改建议

执行前应先完成以下修正：

1. 修订 `phase_plan.md` 中新增接口数量、候选因子数量和字段名。
2. 新建 `scripts/download_alternative_data.py`，先实现 `test` 命令，不急于全量下载。
3. 给 `download_tushare.py` / `download_supplement.py` 增加向前补历史和合并去重逻辑。
4. 在 `csv_to_parquet.py` 增加新增数据 processor。
5. 在 `src/data/loader.py` 增加新增数据 loader。
6. 技术指标优先自行实现，暂不引入 `pandas_ta`。
7. `build_factor_panels.py` 和 `run_factor_evaluation.py` 若要支持 `--group`，需要先实现 CLI 和分组逻辑。
8. 重新运行新增接口探针，全部通过后才进入大规模下载。

## 当前可直接运行的安全命令

这些命令可以运行，用于继续检查，不会触碰正式测试集：

```text
python -m scripts.download_tushare test
python -m scripts.download_supplement test
python scripts/build_factor_panels.py --dry-run
python scripts/csv_to_parquet.py --help
python scripts/build_factor_panels.py --help
python scripts/run_factor_evaluation.py --help
```

以下命令当前不建议直接运行：

```text
python scripts/download_tushare.py all
python scripts/download_supplement.py all
python scripts/download_alternative_data.py all
python scripts/csv_to_parquet.py --all
python scripts/build_factor_panels.py --group original
python scripts/run_factor_evaluation.py --group original
```

原因：下载补历史逻辑、新增脚本、新增 processed 转换和 CLI 参数尚未实现。
