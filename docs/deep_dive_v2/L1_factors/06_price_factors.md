# 06 — 量价因子构建 `src/factors/price_factors.py`

---

## 一、文件定位

```
所属 Part  : Layer 1（因子构建层）
在数据流中 : loader → price_factors → preprocess → combiner/ridge
被谁调用   : scripts/build_factor_panels.py
调用谁     : src/data/loader.py（load_daily_quote / load_daily_basic / load_margin /
             load_moneyflow / load_industry / get_holder_pit_raw）
```

> ⚠️ **与 v1 的重大差异**：v1 文档记录了约 12 个量价因子（659 行）；v2 实现 **15 个**因子（804 行），新增了 Sprint 1 的 6 个动量因子：`high_52w`、`ind_adj_mom`、`mom_risk_adj`、`high_52w_v2`、`ind_adj_mom_6_1`、`mom_consistency_6`。`holder_chg` 改为严格 PIT 实现（使用 `available_date` 三重过滤）。

---

## 二、时间对齐与 PIT 假设

量价因子全部来自日频市场数据，**无 PIT 约束**（日行情在当天收盘后即可用）。

唯一例外：`holder_chg`（股东人数变化率）使用 PIT 财务数据，需要 `available_date <= T`。

**窗口端对齐**：所有量价因子的数据截止到 `T` 日（含 `T` 日收盘价），不含 T+1。`_window_dates()` / `_valid_dates_before()` 中 `end=rebalance_date` 保证这一点。

---

## 三、模块顶部

```python
@lru_cache(maxsize=1)
def _all_trading_dates() -> pd.DatetimeIndex:
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    return iq.index.sort_values()
```

**为何用 `lru_cache(maxsize=1)` 而不是模块级变量**：第一次被调用时才读盘（懒加载），而不是 import 时立即读。`index_quote.parquet` 仅约 2500 行，读取极快，`lru_cache` 保证全程只读一次。

**为何不用 `pandas.bdate_range`**：`bdate_range` 按工作日（周一至周五）生成，会包含节假日（春节、五一等），而实际 A 股交易日序列来自 `index_quote`（真实交易记录），两者不一致。所有量价窗口必须基于实际交易日序列计算。

**窗口常量**（L48-68）：

| 常量 | 值 | 用途 |
|---|---|---|
| `WINDOW_RET_1M` | 20 | 短期反转窗口 |
| `WINDOW_MOM_SKIP` | 20 | 动量跳过最近交易日数 |
| `WINDOW_MOM_6` | 120 | 6M 动量总窗口（含跳过）|
| `WINDOW_MOM_12` | 240 | 12M 动量总窗口（含跳过）|
| `WINDOW_HIGH_52W` | 252 | 52 周高点窗口 |
| `WINDOW_VOL` | 60 | 波动率窗口 |
| `WINDOW_TURN` | 20 | 换手率均值窗口 |
| `WINDOW_AMIHUD` | 20 | Amihud 非流动性窗口 |

---

## 四、核心函数逐一解析

### 辅助函数

**`_window_dates(rebalance_date, n_window, skip_recent, extra_base)`**（L93-125）

计算动量因子的起止日期（含一个"基点日"用于收益率两端比较）：
```
end_pos   = valid[-1 - skip_recent]   # 窗口末端（跳过最近 skip_recent 个）
start_pos = end_pos - n_window        # 基点日（extra_base=1 时多取一个前基点）
```

例：`factor_mom_6_1` 使用 `skip_recent=20, n_window=100`：
- 末端 = T 前第 21 个交易日（跳过最近 20 天）
- 基点 = T 前第 121 个交易日
- 收益 = `close_adj[T-20] / close_adj[T-120] - 1`

**`_prev_month_end_dates(rebalance_date, n)`**（L128-160）

返回过去 n 个自然月的最后交易日，用于 `mom_consistency_6` 等需要月末日期的因子。`DateOffset(months=k)` 对月末日期自动截断（如 2013-03-29 - 1月 = 2013-02-28），正确定位到目标月份。

---

### 4.1 动量/反转类（4 个）

**`factor_ret_1m`** — 20 日短期反转

```python
result = adj.iloc[-1] / adj.iloc[0] - 1   # close_adj 两端比率（不是 ret.prod()）
```

用两端比率而非 `ret.cumprod()`：后者在缺失行时会因参与乘法的项数减少而低估收益。预期方向：**−**（短期反转）。

**`factor_mom_6_1`** — 6M中期动量（跳过1M）

实际窗口 = 100 个交易日（`WINDOW_MOM_6=120 - WINDOW_MOM_SKIP=20`）。预期方向：**+**（中期动量延续）。

**`factor_mom_12_1`** — 12M长期动量（跳过1M）

实际窗口 = 220 个交易日（`WINDOW_MOM_12=240 - WINDOW_MOM_SKIP=20`）。早期调仓日因历史不足返回 NaN 是正常现象。预期方向：**+**。

**`factor_holder_chg`** — 股东人数变化率（PIT 因子）

```python
# 三重 PIT 过滤（L281-284）：
vis = hp[
    (hp["available_date"] <= rebalance_date)   # 实际可用日（max(pit_date, end_date)）
    & (hp["end_date"] < rebalance_date)         # 报告期已结束
    & (hp["pit_date"] <= rebalance_date)         # 公告日已过
]
```

取最新两期，计算变化率，负号：`chg = -(latest - prev) / prev`（减少 = 正值 = 看多）。

双重陈旧度检查（L309-316）：
1. 最新期距 T ≤ `HOLDER_STALE_MONTHS=18` 个月
2. 两期间隔 ≤ 18 个月（防止跨度过大的两期缺乏可比性）

预期方向：**+**（股东人数减少 = 筹码集中 = 机构入场 = 看多）。

---

### 4.2 波动率类（3 个）

**`factor_vol_60d`** — 60日历史波动率

```python
result = ret_pivot.std(ddof=1, skipna=True)
result[valid_counts < MIN_VOL_PERIODS] = np.nan   # 有效天数<40时置NaN
```

停牌日 `ret=0` 保留（股价不变 = 零方差贡献）。预期方向：**−**（低波动溢价）。

**`factor_ivol_60d`** — 60日特质波动率（CAPM残差标准差）

向量化 OLS：对 N 只股票同时回归（X 形状 T×2，Y 形状 T×N），避免逐股回归的性能瓶颈。回归：`ret_stock = α + β × ret_market + ε`，`ivol = std(ε)`。

缺失天填充 0（停牌=零收益假设），填充后做 OLS，完成后把有效天数 < `MIN_IVOL_PERIODS=30` 的股票置 NaN。预期方向：**−**。

**`factor_max_ret`** — 20日最大单日收益率

用过去 20 个交易日的最大 `ret`。高最大收益率（通常来自游资拉板）预示后续反转。预期方向：**−**（彩票因子的反面，高偏度股票预期低收益）。

---

### 4.3 流动性类（2 个）

**`factor_turn_20d`** — 20日平均换手率

```python
turn = dq["vol"] / mv_basic["free_float_shares"]   # 成交量 / 自由流通股数
result = turn.groupby("ts_code").mean()
```

高换手率常反映散户短期投机，预期方向：**−**（高换手 = 博弈属性 = 低收益）。

**`factor_amihud`** — Amihud 非流动性指标

```python
amihud = (abs_ret / amount_yuan).mean(skipna=True)  # |ret| / (成交额（元）)
```

注意：`daily_quote.amount` 单位是**千元**，需 ×1000 转元（或直接用千元，标准化后量纲消除）。

含义：每单位成交额对股价的冲击；值越高 = 流动性越差。预期方向：**+**（流动性溢价，流动性差的股票有补偿）。

---

### 4.4 资金流向类（3 个）

**`factor_margin_ratio`** — 融资余额占自由流通市值比例

```python
rzye = margin["rzye"]          # 融资余额（元）
free_mv = basic["free_float_mv"] * 1e4   # 万元→元
result = rzye_20d / free_mv_daily
```

融资比例高 = 杠杆资金多 = 拥挤 = 后续有爆仓压力。预期方向：**−**（融资拥挤 = 低收益）。

**`factor_short_ratio`** — 融券余量占成交量比例

融券做空信号。预期方向：**−**。

**`factor_large_net_inflow`** — 大单净流入占总成交比例

```python
net_mf_yuan = mf["net_mf_amount"] * 10   # 万元→千元（与 amount 同量纲）
ratio = net_mf_yuan / dq["amount"]
```

大单净流入 = 机构资金净买入信号。预期方向：**+**。

---

### 4.5 Sprint 1 动量类（6 个，v2 新增）

#### `factor_high_52w` — 52周高点比率

```python
high_52w = close_adj.rolling(WINDOW_HIGH_52W).max()
result = close_adj_T / high_52w_T
```

价格接近 52 周高点 = 动量强势。预期方向：**+**（52周高点效应，接近高点则趋势延续）。

#### `factor_high_52w_v2` — 52周高点比率（修正版）

对 `high_52w` 的改进：分母用过去 252 日内实际最高价（排除停牌期间的填充），覆盖率更高，噪声更低。预期方向：**+**。

#### `factor_ind_adj_mom` — 行业调整动量（12M-1M）

`mom_12_1 - industry_mean_mom_12_1`：消除行业轮动对动量的干扰，保留个股的"相对行业"动量。预期方向：**+**。

#### `factor_ind_adj_mom_6_1` — 行业调整动量（6M-1M）

类似 `factor_ind_adj_mom`，但用 6 个月窗口。预期方向：**+**。

#### `factor_mom_risk_adj` — 风险调整动量

`mom_12_1 / vol_60d`：Sharpe 比率风格的动量因子，在相同收益下偏好波动率低的股票。预期方向：**+**。

#### `factor_mom_consistency_6` — 6月动量一致性

过去 6 个自然月中，月收益率为正的月数比例（去掉最近1个月）：

```python
month_ends = _prev_month_end_dates(rebalance_date, WINDOW_CONSISTENCY_LOOKBACK + 1)
# 计算各月月度收益率，统计 ≥ 0 的比例
```

`MONTHLY_RET_CAP = 0.4`：月收益率截尾上限（防止涨停板导致极端值），A 股月度最高收益率通常远超普通市场，截尾降低噪声。预期方向：**+**（一致性高 = 动量稳定 = 趋势可延续）。

---

## 五、内部辅助函数

| 函数 | 说明 |
|---|---|
| `_all_trading_dates()` | 全市场实际交易日序列，lru_cache 缓存 |
| `_valid_dates_before(T)` | T 及之前的交易日序列 |
| `_window_dates(T, n_window, skip, extra)` | 计算动量窗口起止日期 |
| `_prev_month_end_dates(T, n)` | 过去 n 个自然月的最后交易日列表 |

---

## 六、落盘产物

本模块不直接落盘。由 `build_factor_panels.py` 调用并将结果写入 `data/processed/factor_panels/<name>.parquet`。

---

## 七、相关测试

- `tests/test_evaluation.py`：间接覆盖
- 建议补充：时间错位测试（`shift_test.py`），将因子整体后移一期，IC 应显著下降。这是防未来函数的关键验证。

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| 历史数据不足 n 个交易日 | `_window_dates` / `_window_start` 返回 None，函数返回全 NaN Series |
| `load_daily_quote` 返回空 DataFrame | 返回全 NaN Series |
| 有效天数 < `MIN_VOL_PERIODS`（波动率）| 该股票置 NaN |
| holder_chg 数据陈旧 | 该股票置 NaN |

---

## 九、数据流图

```
load_daily_quote(start, T)     → close_adj / ret / vol / amount
load_daily_basic(start, T)     → total_mv / free_float_mv
load_margin(start, T)          → rzye（融资余额）
load_moneyflow(start, T)       → net_mf_amount（大单净流入）
load_industry(T, T)            → industry_code（行业调整动量用）
get_holder_pit_raw()           → holder_chg 的多期历史
index_quote.parquet            → _all_trading_dates()（实际交易日序列）

每个 factor_xxx() → ts_code 为 index 的 Series（原始值）
    ↓ preprocess_factor（MAD + Z-score + 中性化，量价因子 fin_sector_codes=None）
    ↓ data/processed/factor_panels/<name>.parquet
```

---

## 十、领域知识补充

**动量效应为何跳过最近 1 个月（`WINDOW_MOM_SKIP=20`）？**

实证研究发现：短期（1个月内）存在**反转效应**（近期过度涨跌会回调），而中长期（1-12个月）存在**动量效应**（趋势延续）。如果直接用 12 个月收益率（包含最近 1 个月），反转效应会部分抵消动量效应，降低因子 IC。跳过最近 20 个交易日（约 1 个月），净化后的动量信号 IC_IR 更高。

**Amihud 非流动性指标（2002）**：

Amihud 提出 `ILLIQ = mean(|r_t| / VOLD_t)`（日收益率绝对值除以日成交额）作为流动性的低成本代理指标。其含义是"每单位成交额引起的价格变动"，流动性越差，价格对交易量越敏感，ILLIQ 越高。实证表明 ILLIQ 能较好预测横截面收益（流动性溢价）。
