# 因子健康监控报告

生成时间：2026-05-29 12:01

评估截止日期（as_of）：2022-10-31


> **重要声明**：本报告数据范围为**训练期 + 验证期**（训练期：2012-2020，验证期：2021-2022）。
> 验证期（2021-2022）数据仅用于诊断参考，**不允许回流至因子筛选逻辑**，不得直接作为调整 `final_factors.json` 的依据。
> 如需调整因子池，必须另起训练/验证实验，在训练期内完成验证后方可决策。


---

## 1. 健康状态分布

| 状态 | 因子数 | 说明 |
|------|--------|------|
| STABLE | 7 | adj_IC_IR_24m ≥ 0.30，信号稳定 |
| WEAK | 2 | 0.20 ≤ adj_IC_IR_24m < 0.30，信号偏弱 |
| WARN | 3 | 0 ≤ adj_IC_IR_24m < 0.20，信号衰减 |
| REVERSE | 6 | adj_IC_IR_24m < 0，方向反转 |
| UNKNOWN | 0 | 有效样本不足，无法判断 |


---

## 2. 趋势预警

⚠️ **DETERIORATING**（近 12 期 adj_IC_IR 显著低于近 36 期，差值 > 0.15）：

  - `holder_chg`  adj_12m=-0.0068  adj_36m=+0.2622
  - `high_52w_v2`  adj_12m=+0.0956  adj_36m=+0.2600
  - `rev_yoy`  adj_12m=-0.0183  adj_36m=+0.2003
  - `gross_margin`  adj_12m=-0.2376  adj_36m=+0.1418
  - `q_roe`  adj_12m=-0.2013  adj_36m=+0.2571
  - `roe_delta`  adj_12m=-0.0439  adj_36m=+0.1931
  - `gross_margin_trend`  adj_12m=-0.1155  adj_36m=+0.2519


---

## 3. 完整健康快照

| factor               |   factor_direction |   n_valid_obs |   adj_ic_ir_12m |   adj_ic_ir_24m |   adj_ic_ir_36m | status   | trend_flag    | suggested_action             |
|:---------------------|-------------------:|--------------:|----------------:|----------------:|----------------:|:---------|:--------------|:-----------------------------|
| rev_acceleration     |             1.0000 |           128 |          0.6432 |          0.7652 |          0.6782 | STABLE   | OK            | HOLD                         |
| turn_20d             |            -1.0000 |           128 |          0.8243 |          0.7532 |          0.5971 | STABLE   | OK            | HOLD                         |
| ivol_60d             |            -1.0000 |           128 |          0.8040 |          0.7030 |          0.5593 | STABLE   | OK            | HOLD                         |
| holder_chg           |             1.0000 |           128 |         -0.0068 |          0.3525 |          0.2622 | STABLE   | DETERIORATING | HOLD|DETERIORATING           |
| hk_hold_ratio        |             1.0000 |            69 |          0.6301 |          0.3294 |          0.5059 | STABLE   | OK            | HOLD                         |
| roe_delta_3q         |             1.0000 |           128 |          0.3683 |          0.3257 |          0.4439 | STABLE   | OK            | HOLD                         |
| analyst_eps_revision |             1.0000 |           128 |          0.3117 |          0.3098 |          0.2939 | STABLE   | OK            | HOLD                         |
| amihud               |             1.0000 |           128 |          0.3152 |          0.2616 |          0.2331 | WEAK     | OK            | MONITOR                      |
| cfp                  |             1.0000 |           128 |          0.3761 |          0.2278 |          0.2050 | WEAK     | OK            | MONITOR                      |
| ep_ttm               |             1.0000 |           128 |          0.1675 |          0.1731 |          0.2311 | WARN     | OK            | WATCH_CLOSELY                |
| high_52w_v2          |             1.0000 |           127 |          0.0956 |          0.1230 |          0.2600 | WARN     | DETERIORATING | WATCH_CLOSELY|DETERIORATING  |
| hk_hold_chg          |             1.0000 |            68 |          0.2565 |          0.1225 |          0.2535 | WARN     | OK            | WATCH_CLOSELY                |
| rev_yoy              |             1.0000 |           128 |         -0.0183 |         -0.0709 |          0.2003 | REVERSE  | DETERIORATING | REVIEW_REMOVAL|DETERIORATING |
| gross_margin         |             1.0000 |           128 |         -0.2376 |         -0.0889 |          0.1418 | REVERSE  | DETERIORATING | REVIEW_REMOVAL|DETERIORATING |
| q_roe                |             1.0000 |           128 |         -0.2013 |         -0.0974 |          0.2571 | REVERSE  | DETERIORATING | REVIEW_REMOVAL|DETERIORATING |
| roe_delta            |             1.0000 |           128 |         -0.0439 |         -0.1099 |          0.1931 | REVERSE  | DETERIORATING | REVIEW_REMOVAL|DETERIORATING |
| gross_margin_trend   |             1.0000 |           128 |         -0.1155 |         -0.1160 |          0.2519 | REVERSE  | DETERIORATING | REVIEW_REMOVAL|DETERIORATING |
| piotroski_f          |             1.0000 |           128 |         -0.0028 |         -0.2673 |          0.0675 | REVERSE  | OK            | REVIEW_REMOVAL               |


---

## 4. 因子分组详情


### STABLE（7 个）

- `rev_acceleration` adj_IC_IR_24m=+0.7652  [OK]
- `turn_20d` adj_IC_IR_24m=+0.7532  [OK]
- `ivol_60d` adj_IC_IR_24m=+0.7030  [OK]
- `holder_chg` adj_IC_IR_24m=+0.3525  [DETERIORATING] ⚠️DETERIORATING
- `hk_hold_ratio` adj_IC_IR_24m=+0.3294  [OK]
- `roe_delta_3q` adj_IC_IR_24m=+0.3257  [OK]
- `analyst_eps_revision` adj_IC_IR_24m=+0.3098  [OK]

### WEAK（2 个）

- `amihud` adj_IC_IR_24m=+0.2616  [OK]
- `cfp` adj_IC_IR_24m=+0.2278  [OK]

### WARN（3 个）

- `ep_ttm` adj_IC_IR_24m=+0.1731  [OK]
- `high_52w_v2` adj_IC_IR_24m=+0.1230  [DETERIORATING] ⚠️DETERIORATING
- `hk_hold_chg` adj_IC_IR_24m=+0.1225  [OK]

### REVERSE（6 个）

- `rev_yoy` adj_IC_IR_24m=-0.0709  [DETERIORATING] ⚠️DETERIORATING
- `gross_margin` adj_IC_IR_24m=-0.0889  [DETERIORATING] ⚠️DETERIORATING
- `q_roe` adj_IC_IR_24m=-0.0974  [DETERIORATING] ⚠️DETERIORATING
- `roe_delta` adj_IC_IR_24m=-0.1099  [DETERIORATING] ⚠️DETERIORATING
- `gross_margin_trend` adj_IC_IR_24m=-0.1160  [DETERIORATING] ⚠️DETERIORATING
- `piotroski_f` adj_IC_IR_24m=-0.2673  [OK]


---

## 5. 建议操作汇总

> 以下建议仅基于 IC 历史统计，不替代人工判断。
> `REVIEW_REMOVAL` 表示需要另起实验验证后才能决定是否调池，**不可直接修改主线因子池**。


❌ **需要复核（REVIEW_REMOVAL）**：`rev_yoy`、`gross_margin`、`q_roe`、`roe_delta`、`gross_margin_trend`、`piotroski_f`

⚠️ **密切关注（WATCH_CLOSELY）**：`ep_ttm`、`high_52w_v2`、`hk_hold_chg`