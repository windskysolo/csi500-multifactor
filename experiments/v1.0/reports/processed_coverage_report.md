# processed 数据覆盖报告

生成时间：2026-05-23 00:28:30

| 模块 | 文件 | 行数 | 时间范围 | 关键列 NaN 率 | 状态 |
|------|------|------|----------|--------------|------|
| M1  daily_quote | daily_quote.parquet | 4,617,574 | 2011-01-04 ~ 2025-12-31 | ret=0.0%, close_adj=0.0% | ✅ |
| M2  daily_basic | daily_basic.parquet | 4,617,572 | 2011-01-04 ~ 2025-12-31 | free_share=0.0%, free_float_mv=0.0%, log_free_float_mv=0.0% | ✅ |
| M3  index_quote | index_quote.parquet | 3,644 | 2011-01-04 ~ 2025-12-31 | index_ret=0.0%, nav=0.0% | ✅ |
| M4  industry | industry.parquet | 4,617,574 | 2011-01-04 ~ 2025-12-31 | industry_code=0.0% | ✅ |
| M5  financial_pit | financial_pit.parquet | 87,289 | 2010-04-09 ~ 2026-05-01 | revenue=0.2%, total_assets=1.3% | ✅ |
| M6  indicator_pit | indicator_pit.parquet | 88,256 | 2010-04-09 ~ 2026-05-01 | roe=1.3%, debt_to_assets=1.1% | ✅ |
| M7  holder_pit | holder_pit.parquet | 119,622 | 2010-01-07 ~ 2025-12-31 | holder_num=0.5% | ✅ |
| M8  dividend_pit | dividend_pit.parquet | 11,611 | 2010-02-24 ~ 2026-05-16 | cash_div_tax=0.0% | ✅ |
| M9  margin | margin.parquet | 2,400,026 | 2011-01-04 ~ 2025-12-31 | rzye=0.0%, rqye=0.0% | ✅ |
| M10 moneyflow | moneyflow.parquet | 3,799,633 | 2011-01-04 ~ 2025-12-31 | net_mf_amount=0.0% | ✅ |
| M11 stock_status | stock_status.parquet | 4,762,022 | 2011-01-04 ~ 2025-12-31 | tradable=0.0% | ✅ |
| M12 index_member | index_member.parquet | 90,000 | 2011-08-23 ~ 2025-12-31 | index_weight=0.0%, tradable=0.0% | ✅ |
| M13 hk_hold | hk_hold.parquet | 2,764,551 | 2016-06-29 ~ 2025-12-31 | ratio=0.0% | ✅ |
| M14 analyst_rc_pit | analyst_rc_pit.parquet | 1,454,494 | 2010-01-01 ~ 2025-12-31 | eps=0.9% | ✅ |
| M15 share_float | share_float.parquet | 8,096 | 2010-01-05 ~ 2025-12-16 | float_ratio=0.0% | ✅ |
| M16 holder_trade_pit | holder_trade_pit.parquet | 52,916 | 2010-01-04 ~ 2025-12-31 | change_vol=0.0% | ✅ |
| M17 cyq_perf | cyq_perf.parquet | 2,567,756 | 2018-01-02 ~ 2025-12-31 | winner_rate=0.0%, weight_avg=0.0% | ✅ |
| M18 pledge_stat | pledge_stat.parquet | 820,507 | 2014-03-07 ~ 2026-05-15 | pledge_ratio=0.0% | ✅ |
| M19 moneyflow_hsgt | moneyflow_hsgt.parquet | 2,621 | 2014-11-17 ~ 2025-12-31 | north_money=0.2% | ✅ |
| M20 top_list | top_list.parquet | 187,791 | 2011-01-04 ~ 2025-12-31 | net_amount=0.0% | ✅ |
| M21 top_inst | top_inst.parquet | 1,586,800 | 2012-01-04 ~ 2025-12-31 | net_buy=0.0% | ✅ |
| M22 block_trade | block_trade.parquet | 295,898 | 2011-01-04 ~ 2025-12-31 | amount=0.0% | ✅ |
| M23 stk_surv | stk_surv.parquet | 157,723 | 2021-08-06 ~ 2025-12-31 | fund_visitors=0.0% | ✅ |
| M24 fina_mainbz_raw | fina_mainbz_raw.parquet | 602,592 | 2010-03-31 ~ 2025-12-31 | bz_sales=0.2% | ✅ |

## 阶段4验收检查

- ✅ daily_quote.parquet 覆盖 2011 起（最早 2011-01-04）
- ✅ daily_basic.parquet 含 free_share / free_float_mv / log_free_float_mv
- ✅ index_quote.parquet 行数 > 2000（3644 行）
- ✅ index_member.parquet 覆盖 2012 起（最早 2011-08-23）
- ✅ financial_pit.parquet 行数 > 50000（87,289 行）
- ✅ indicator_pit.parquet 行数 > 50000（88,256 行）
- ✅ 全部 12 个新增 Parquet 文件均已生成
