# 数据层完成说明

> 记录截止：2026-04-29
> 数据来源：Tushare Pro（通过 OpenClaw 代理 `http://101.35.233.113:8020/`）
> 覆盖范围：2016-01-01 ～ 2025-12-31（财务类数据从 2014-01-01 起）
> 股票池：中证500历史成分股（去重后共 **1264 只**）

---

## 一、目录结构

```
data/
├── csi500_index_weight_201601_202512.csv   # 成分股月频权重快照
└── raw/
    ├── daily_quote/        # 日行情（1264 个 CSV，按股票）
    ├── adj_factor/         # 复权因子（1264 个 CSV，按股票）
    ├── daily_basic/        # 每日基础指标（1264 个 CSV，按股票）
    ├── financial_income/   # 利润表（1264 个 CSV，按股票）
    ├── financial_balance/  # 资产负债表（1264 个 CSV，按股票）
    ├── financial_cashflow/ # 现金流量表（1264 个 CSV，按股票）
    ├── financial_indicator/# 财务衍生指标（1264 个 CSV，按股票）
    ├── suspend/            # 停牌记录（1264 个 CSV，按股票）
    ├── limit_list/         # 涨跌停列表（2430 个 CSV，按交易日）
    ├── margin/             # 融资融券明细（1264 个 CSV，按股票）
    ├── moneyflow/          # 个股资金流向（1264 个 CSV，按股票）
    ├── holder_number/      # 股东人数（1264 个 CSV，按股票）
    ├── dividend/           # 分红派息（1264 个 CSV，按股票）
    ├── index_daily.csv     # 中证500全收益指数日行情
    ├── stock_basic.csv     # 股票基本信息
    ├── namechange.csv      # 股票曾用名（ST标记来源）
    ├── industry_citics.csv # 行业分类历史成员（实为SW2021，见注意）
    └── industry_classify_SW2021.csv  # 申万2021行业体系层级表
```

---

## 二、各数据详情

### 2.1 成分股权重快照

**文件**：`data/csi500_index_weight_201601_202512.csv`
**规模**：60,000 行 × 4 列，覆盖 120 个月份
**字段**：

| 字段 | 说明 |
|------|------|
| `index_code` | 指数代码（000905.SH） |
| `con_code` | 成分股代码 |
| `trade_date` | 权重生效日期（每月最后一个交易日） |
| `weight` | 权重（%） |

**用途**：构造每月调仓日的可投资域（投资域 = 当月实际生效的成分股）。

---

### 2.2 日行情（daily_quote）

**组织**：每股一个 CSV，文件名为 `{ts_code}.csv`
**规模**：每只股票约 2313 行 × 11 列
**字段**：

| 字段 | 说明 |
|------|------|
| `ts_code` | 股票代码 |
| `trade_date` | 交易日 |
| `open/high/low/close` | 开高低收（**未复权**） |
| `pre_close` | 昨收（未复权） |
| `change` | 涨跌额 |
| `pct_chg` | 涨跌幅（%，已为后复权） |
| `vol` | 成交量（手） |
| `amount` | 成交额（千元） |

**注意**：`open/high/low/close` 为未复权原始价格，需结合 `adj_factor` 计算后复权价：
`后复权价 = close × adj_factor`。`pct_chg` 已经是后复权涨跌幅，可直接使用。

---

### 2.3 复权因子（adj_factor）

**组织**：每股一个 CSV
**规模**：每只股票约 2430 行 × 3 列
**字段**：

| 字段 | 说明 |
|------|------|
| `ts_code` | 股票代码 |
| `trade_date` | 日期 |
| `adj_factor` | 后复权因子（最新日 = 1.0，越往前越小） |

---

### 2.4 每日基础指标（daily_basic）

**组织**：每股一个 CSV
**规模**：每只股票约 2313 行 × 18 列
**字段**：

| 字段 | 说明 | 用途 |
|------|------|------|
| `pe_ttm` | 滚动市盈率 | 价值因子 EP = 1/pe_ttm |
| `pb` | 市净率 | 价值因子 BP = 1/pb |
| `ps_ttm` | 滚动市销率 | 价值因子 SP = 1/ps_ttm |
| `dv_ttm` | 滚动股息率（%） | 价值因子（近似值） |
| `turnover_rate` | 换手率（基于流通股） | 流动性因子 |
| `turnover_rate_f` | 换手率（基于自由流通股） | 流动性因子（更精确） |
| `volume_ratio` | 量比 | 技术因子 |
| `free_share` | 自由流通股本（万股） | 自由流通市值原材料 |
| `circ_mv` | 流通市值（千元） | 参考用 |
| `total_mv` | 总市值（千元） | 参考用 |

**自由流通市值**：`free_float_mv = free_share × close × 100`（单位：元），用于因子中性化。

---

### 2.5 财务三表

**文件**：`financial_income/`、`financial_balance/`、`financial_cashflow/`，每股一个 CSV
**覆盖**：2014-01-01 起（早于回测起点，确保 2016 年初有历史报表）
**下载口径**：`report_type=1`（合并报表）

**PIT 关键字段**（三表均有）：

| 字段 | 说明 | 规则 |
|------|------|------|
| `ann_date` | 公告日期 | T 日只能用 `ann_date ≤ T` 的数据 |
| `f_ann_date` | 首次披露日 | 比 `ann_date` 更保守的 PIT 时间戳 |
| `end_date` | 报告期（如 20230930） | **禁止作为 PIT 时间戳**，早于 ann_date 1-3 个月 |
| `report_type` | 报告类型（均为 1） | 合并报表 |

**利润表关键字段**：`revenue`、`total_profit`、`n_income`（净利润）、`basic_eps`、`rd_exp`（研发费用）

**资产负债表关键字段**：`total_assets`、`total_liab`、`total_hldr_eqy_exc_min_int`（归母权益）、`money_cap`（货币资金）

**现金流量表关键字段**：`n_cashflow_act`（经营现金流净额）、`free_cashflow`、`c_paid_goods_s`（购买商品付款）

---

### 2.6 财务衍生指标（financial_indicator）

**组织**：每股一个 CSV，约 86 行 × 108 列
**覆盖**：2014-01-01 起

**注意**：此表**没有 `f_ann_date` 字段**（只有 `ann_date`），PIT 处理时用 `ann_date`，与三表略有差异，使用时需备注。

**关键字段**：

| 字段 | 说明 |
|------|------|
| `roe` / `roa` | 净资产收益率 / 总资产收益率 |
| `grossprofit_margin` | 毛利率 |
| `assets_turn` | 资产周转率 |
| `netprofit_yoy` | 净利润同比增长率（%） |
| `or_yoy` | 营收同比增长率（%） |
| `bps` | 每股净资产 |
| `debt_to_assets` | 资产负债率（%） |
| `ocf_to_debt` | 经营现金流/总负债 |
| `q_roe` / `q_dt_roe` | 单季度 ROE / 扣非单季 ROE |

---

### 2.7 停牌记录（suspend）

**组织**：每股一个 CSV（无停牌记录的股票文件为空行）
**字段**：`ts_code`、`trade_date`、`suspend_timing`、`suspend_type`

**用途**：组合优化时停牌股权重锁定为上期值，不参与再平衡。

---

### 2.8 涨跌停列表（limit_list）

**组织**：每个交易日一个 CSV，文件名 `{trade_date}.csv`，共 **2430** 个文件（2016-2025 全量）
**规模**：每日约 0-200 行 × 18 列

**关键字段**：

| 字段 | 说明 |
|------|------|
| `ts_code` | 股票代码 |
| `trade_date` | 交易日 |
| `limit` | `U`=涨停，`D`=跌停 |
| `open_times` | 当日打开涨跌停次数（`0` = 全天封板，完全无法成交） |
| `first_time` | 首次封板时间 |
| `last_time` | 最后封板时间 |
| `close` | 收盘价 |
| `pct_chg` | 涨跌幅（%） |

**回测约束**：
- 涨停（`limit=U` 且 `open_times=0`）：T+1 无法买入，权重上限 ≤ 前期权重
- 跌停（`limit=D` 且 `open_times=0`）：T+1 无法卖出，权重下限 ≥ 前期权重

---

### 2.9 融资融券明细（margin）

**组织**：每股一个 CSV（非两融标的文件为空）
**规模**：每只股票约 2430 行 × 10 列
**PIT**：纯市场数据，`trade_date` 即可用日期，无延迟。

**字段**：

| 字段 | 说明 | 因子用途 |
|------|------|---------|
| `rzye` | 融资余额（元） | 融资余额变化率 |
| `rqye` | 融券余额（元） | 空头压力指标 |
| `rzmre` | 融资买入额（元） | 当日融资需求强度 |
| `rzche` | 融资偿还额（元） | 融资净增量 = rzmre - rzche |
| `rqyl` | 融券卖出量（股） | 融券净卖出 = rqyl - rqchl |
| `rqchl` | 融券偿还量（股） | |
| `rqmcl` | 融券买入量（股） | |
| `rzrqye` | 融资融券余额合计 | |

---

### 2.10 个股资金流向（moneyflow）

**组织**：每股一个 CSV
**规模**：每只股票约 2313 行 × 20 列
**PIT**：纯市场数据，`trade_date` 即可用日期，无延迟。

**关键字段**：

| 字段 | 说明 |
|------|------|
| `buy_elg_amount` | 超大单买入额（万元，单笔 ≥500万 或 ≥100手） |
| `sell_elg_amount` | 超大单卖出额（万元） |
| `buy_lg_amount` | 大单买入额（单笔 100-500万） |
| `sell_lg_amount` | 大单卖出额 |
| `buy_md_amount` | 中单买入额（单笔 2-100万） |
| `buy_sm_amount` | 小单买入额（单笔 <2万） |
| `net_mf_amount` | 主力净流入 = (超大单+大单买) − (超大单+大单卖)，万元 |
| `net_mf_vol` | 主力净流入量（手） |

**因子构建**：`主力净流入比 = net_mf_amount / (daily_quote.amount / 10)`（统一万元单位）

---

### 2.11 股东人数（holder_number）

**组织**：每股一个 CSV
**规模**：每只股票约 40-80 行（季频）× 4 列
**覆盖**：2014-01-01 起

**字段**：

| 字段 | 说明 |
|------|------|
| `ts_code` | 股票代码 |
| `ann_date` | 公告日期（**PIT 时间戳**，必须用此字段） |
| `end_date` | 统计截止日（报告期，**禁止作为 PIT 时间戳**） |
| `holder_num` | 股东人数 |

**PIT 规则**：T 日可用 = `ann_date ≤ T` 的最新记录（同一 `end_date` 取最大 `ann_date`）。
`end_date` 早于 `ann_date` 约 1-3 个月。

**因子**：股东人数变化率（减少 → 持仓集中 → 正向 alpha，A 股实证显著）

---

### 2.12 分红派息（dividend）

**组织**：每股一个 CSV（下载时已过滤"取消"状态）
**规模**：每只股票约 10-40 行 × 14 列

**关键字段**：

| 字段 | 说明 |
|------|------|
| `ann_date` | 分红方案首次公告日 |
| `imp_ann_date` | 实施公告日（**推荐 PIT 时间戳**） |
| `ex_date` | 除权除息日（股价已调整，**禁止作为 PIT 时间戳**） |
| `div_proc` | 状态：预案 / 股东大会预案 / 实施 |
| `cash_div` | 每股分红（元，含税） |
| `cash_div_tax` | 每股分红（元，税后，**推荐用于股息率因子**） |
| `stk_div` | 每股送股数 |
| `stk_bo_rate` | 每股转增数 |

**PIT 规则**：只用 `div_proc='实施'` 的记录；PIT 时间戳用 `imp_ann_date`（缺失退回 `ann_date`）。

---

### 2.13 中证500全收益指数日行情（index_daily.csv）

**规模**：2428 行 × 11 列（2016-2025 全量交易日）
**指数代码**：`H00905.CSI`（**全收益指数**，含分红再投资）
**有效字段**：`trade_date`、`close`、`pre_close`、`pct_chg`（`open/high/low/vol/amount` 为空）

**重要**：必须使用全收益指数（`H00905.CSI`）作为基准。使用价格指数（`000905.SH`）
会产生约 2-3% 的虚假年化超额，导致策略效果被系统性高估。

---

### 2.14 参考数据（独立文件）

**stock_basic.csv**（5510 行 × 10 列）：全市场股票基本信息
字段：`ts_code`、`name`、`area`、`market`、`list_date`（上市日期）、`act_ent_type`（企业性质）
用途：过滤上市不足 6 个月的新股（`list_date` + 180 天内排除）。

**namechange.csv**（10000 行 × 6 列）：股票曾用名变更历史
字段：`ts_code`、`name`、`start_date`、`end_date`、`ann_date`、`change_reason`
用途：识别 ST / \*ST 标记历史，构造逐日 ST 状态序列（`change_reason` 含 "ST" 的区间内排除）。

**industry_citics.csv**（7652 行 × 8 列）：行业历史成员表
字段：`index_code`、`con_code`、`in_date`、`out_date`、`is_new`、`industry_code`、`industry_name`、`industry_src`
⚠️ **注意**：文件名含"citics"但实际数据为 **SW2021（申万2021）**，共 31 个一级行业，
含历史进出记录（`in_date`/`out_date`）。计划原定中信一级，但 OpenClaw 代理不提供中信数据，
申万 SW2021 作为替代，因子中性化效果相当，使用时需在代码注释中备注口径。

**industry_classify_SW2021.csv**（31 行 × 7 列）：申万2021一级行业层级说明
字段：`index_code`、`industry_name`、`level`（均为 L1）、`industry_code`

---

## 三、数据使用关键约定

| 约定 | 规则 |
|------|------|
| **价格口径** | 全程后复权：`close_adj = close × adj_factor` |
| **收益率** | `daily_quote.pct_chg` 已是后复权涨跌幅，可直接使用 |
| **基准** | `H00905.CSI` 全收益指数，禁止替换为价格指数 |
| **财务 PIT** | 三表用 `f_ann_date`，财务指标表用 `ann_date`，禁止用 `end_date` |
| **股东人数 PIT** | 用 `ann_date`，规则与财务数据一致 |
| **分红 PIT** | 用 `imp_ann_date`（缺失退回 `ann_date`），禁止用 `ex_date` |
| **自由流通市值** | `free_share（万股）× close × 100`，单位：元 |
| **行业中性化** | SW2021 申万一级（`industry_citics.csv`），非中信一级 |
| **成分股口径** | 调仓日投资域 = `csi500_index_weight` 当月生效日的成分股 |
| **涨跌停约束** | `limit_list` 中 `open_times=0` 为全天封板，次日不可交易 |
| **停牌约束** | `suspend` 中有记录的日期，当日权重锁定，不参与优化 |
| **ST 过滤** | `namechange.change_reason` 含 ST 的区间内股票排除 |
| **新股过滤** | `stock_basic.list_date` + 180 天内的股票排除 |

---

## 四、下一步：数据清洗（第2周任务）

原始数据全部就绪，下一阶段：

1. **`scripts/csv_to_parquet.py`**：批量清洗 + 转 Parquet
   - 计算后复权价、后复权收益率
   - 财务数据 PIT 转换（同一 `end_date` 去重保留最新 `ann_date`）
   - 合并 suspend / limit_list / namechange 为 `stock_status.parquet`
   - 融资融券、资金流向对齐到交易日历

2. **`src/data/universe.py`**：构造每日成分股快照
   - 输入：`csi500_index_weight` + `stock_status`
   - 输出：每个调仓日的可投资股票集合（排除停牌/ST/新股/涨跌停）
