# 07 — 备选数据因子构建 `src/factors/alt_factors.py`

---

## 一、文件定位

```
所属 Part  : Layer 1（因子构建层）
在数据流中 : loader（备选数据接口）→ alt_factors → preprocess → combiner/ridge
被谁调用   : scripts/build_factor_panels.py
调用谁     : src/data/loader.py（load_hk_hold / load_analyst_rc_pit /
             load_share_float / load_holder_trade_pit / load_pledge_stat /
             load_cyq_perf / load_moneyflow_hsgt / load_daily_quote）
```

`alt_factors.py` 实现两类备选因子：
1. **技术因子**（5个）：基于日行情的技术分析指标，无外部数据依赖
2. **外部数据因子**（9+个）：依赖沪深港通、分析师、股东、质押等非标准行情数据

**已明确剔除的因子**（函数保留作历史参考，不进入因子池）：
- `chip_winner_rate` / `cost_deviation`：依赖 `cyq_perf`，训练期仅约 36/108 期有数据（~3年），IC_IR 估计不稳定
- `north_flow_5d`：全市场广播因子（所有股票同一值），横截面标准差=0，标准化后全 NaN，无截面选股价值

---

## 二、时间对齐与 PIT 假设

**技术因子**（macd_cross / rsi_6 / rsi_12 / boll_pct / obv_chg_20d）：

来自 `daily_quote`，T 日收盘后即可用，无 PIT 约束。所有窗口截止到 T 日（含）。

**外部数据因子**的 PIT 各有差异：

| 因子 | PIT 方式 | 关键字段 |
|---|---|---|
| hk_hold_ratio / hk_hold_chg | T 日及前 5/30 交易日 | trade_date |
| analyst_eps_revision / analyst_rating_chg / eps_dispersion / analyst_cnt_chg | pit_date（report_date）≤ T | analyst_rc_pit.pit_date |
| float_pct_30d | ann_date ≤ T < float_date ≤ T+30 | share_float.ann_date |
| insider_net_buy | pit_date（ann_date）≤ T，lookback 90 天 | holder_trade_pit.pit_date |
| pledge_ratio | end_date ≤ T（无 ann_date，保守 PIT）| pledge_stat.end_date |

**北向持仓数据的特殊限制**：2014-11-17（沪深港通开通日）前无数据，因此训练期前 2 年（2012-2014）`hk_hold_*` 全 NaN。这是**预期行为**，不是数据错误。

---

## 三、模块顶部

```python
@lru_cache(maxsize=1)
def _all_trading_dates() -> pd.DatetimeIndex:
    """与 price_factors.py 独立实现，不跨模块共享（避免隐式耦合）。"""
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    return iq.index.sort_values()

WINDOW_TECH     = 120   # 技术因子历史窗口（EMA 26+Signal 9 稳定需要约 60 天，120 保安全）
WINDOW_BOLL     = 20    # 布林带窗口
WINDOW_OBV      = 20    # OBV 变化窗口
WINDOW_HK_HOLD  = 30    # 北向持仓变化回看（交易日）
WINDOW_ANALYST  = 180   # 分析师回看天数
ANALYST_SPLIT   = 90    # 近期/远期分割点（天）
WINDOW_INSIDER  = 90    # 股东增减持回看天数
MIN_ANALYST_CNT    = 2  # 近期/远期各自至少需要的研报数
MIN_DISPERSION_CNT = 3  # eps_dispersion 所需最少机构数

RATING_MAP: dict[str, float] = {
    "买入": 5.0, "强烈推荐": 5.0, "强推": 5.0,
    "增持": 4.0, "推荐": 4.0,
    "中性": 3.0, "持有": 3.0, "观望": 3.0,
    "减持": 2.0, "回避": 2.0,
    "卖出": 1.0,
}
```

`RATING_MAP` 将中文评级字符串映射为 5 级数字（5=最看多）。未识别评级（如"跑赢大市"等机构自定义评级）视为 NaN。

---

## 四、核心函数逐一解析

### 辅助函数

**`_nan_series(codes, name)`**：历史不足或数据缺失时返回全 NaN 的占位 Series。保证函数在任何情况下都返回正确 shape 的 Series，避免调用方需要处理 None。

**`_agg_with_min(series, min_cnt, agg)`**：带最小有效值检查的聚合（mean/median）。有效值 < `min_cnt` 时返回 NaN，防止样本极少时统计量不稳定。

---

### 4.1 技术因子（5 个）

#### `factor_macd_cross` — MACD 柱状图因子（L124-161）

```python
macd   = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
signal = macd.ewm(span=9, adjust=False).mean()
hist_T = (macd - signal).iloc[-1]        # T 日柱状图高度
result = hist_T / close_T                # 按收盘价归一化消除量纲
```

`adjust=False`：使用递推公式（`EWM_{t} = α × x_t + (1-α) × EWM_{t-1}`），而非权重修正公式，与 tradingsoftware 中 EMA 的标准实现一致。

预期方向：**+**（MACD 柱状图由负转正是多头信号）。历史 < 35 天时返回全 NaN（EMA 26 + Signal 9 最低需求）。

#### `factor_rsi_6` / `factor_rsi_12` — RSI（6日/12日）（L185-232）

`_compute_rsi` 内部函数：用简单均值（非 Wilder 平滑），更稳健无参数偏差：

```python
gains  = recent.clip(lower=0).mean()   # 正收益日均值
losses = (-recent.clip(upper=0)).mean() # 负收益绝对值均值
rsi    = 100 - 100 / (1 + gains / losses)
# 全涨日（losses=0, gains>0）→ RSI=100（正确处理，不产生 inf）
```

预期方向：**−**（RSI 超买时短期反转）。

#### `factor_boll_pct` — 布林带位置（L235-262）

```python
result = (close.iloc[-1] - close.mean()) / (2 * close.std(ddof=1))
```

含义：T 日价格在布林带内的相对位置（0 = 中轨，+0.5 = 上轨，-0.5 = 下轨）。预期方向：**−**（价格偏上轨时短期反转）。

#### `factor_obv_chg_20d` — OBV 20 日变化率（L265-296）

```python
obv_cum  = (np.sign(ret) * vol).cumsum()   # 日 OBV 累计
avg_vol  = vol.iloc[-WINDOW_OBV:].mean()   # 20 日均成交量（归一化基准）
result   = (obv_cum.iloc[-1] - obv_cum.iloc[-(WINDOW_OBV+1)]) / avg_vol
```

`np.sign(ret)`：+1（上涨日）、-1（下跌日）、0（平盘日）。OBV 按均成交量归一化消除量纲（大盘股绝对成交量大，直接比较无意义）。预期方向：**+**（OBV 上升 = 筹码积累 = 看多）。

---

### 4.2 北向持仓因子（2 个）

#### `factor_hk_hold_ratio` — 北向持仓比例（L303-322）

```python
start = rebalance_date - pd.Timedelta(days=5)   # 容忍最多 5 日数据缺口
hk = load_hk_hold(start, rebalance_date, codes=codes)
snap = hk["ratio"].groupby(level="ts_code").last()  # 取最近一条记录
```

**覆盖注意**：2014-11-17 前全 NaN；非两融标的股票也可能无数据。`reindex(codes)` 后 NaN 作为正常缺失处理。

#### `factor_hk_hold_chg` — 北向持仓变化（L325-351）

30 交易日内北向持仓比例变化量（百分点）：

```python
ratio = hk["ratio"].unstack("ts_code")
result = ratio.iloc[-1] - ratio.iloc[0]   # 最新 - 30 日前
```

预期方向：**+**（北向持续加仓 = 外资流入 = 质量信号）。

---

### 4.3 分析师因子（4 个）

所有分析师因子共享相同的数据加载和时间分割逻辑：
- `load_analyst_rc_pit(T, lookback_days=180)` 取过去 180 天研报
- `split_date = T - 90 天`：180 天内的研报分为"近期（最近90天）"和"远期（90-180天前）"

#### `factor_analyst_eps_revision` — EPS 修正幅度（L354-385）

`median(近期EPS) / median(远期EPS) - 1`。分析师最近 90 天预测相比此前 90 天的变化方向，近期两端均 ≤ 0 时返回 NaN（分子/分母为负时比率无经济含义）。预期方向：**+**（EPS 上调 = 基本面改善预期）。

#### `factor_analyst_rating_chg` — 评级变化（L388-421）

```python
chg = avg_recent_rating - avg_prior_rating   # 均值差
```

使用 `RATING_MAP` 将文字评级转为 5 级数字，未识别评级视为 NaN（不影响其他研报的均值计算）。预期方向：**+**（评级上调 = 分析师看多）。

#### `factor_eps_dispersion` — EPS 预测分歧度（L424-475）

```python
# 同一机构取最新一条研报（去重，避免同机构多期虚高分歧）
rc = rc.groupby(["ts_code", "org_name"]).last().reset_index()

# 变异系数 = std / |mean|，取负号（分歧小 → 高分）
cv = eps_vals.std() / abs(eps_vals.mean())
result = -cv
```

**Miller(1977) 异质信念理论**：意见分歧越大，悲观者因卖空约束被排斥，价格偏向乐观预期，后续向下修正概率更高。因子方向：**+**（分歧小 = 高共识 = 更稳定预期 = 超额收益）。

#### `factor_analyst_cnt_chg` — 覆盖机构数变化（L478-514）

```python
chg = cnt_recent.subtract(cnt_prior, fill_value=0)
```

`fill_value=0` 的含义：某段无研报等价于 0 家机构。但两段均无数据的股票不出现在任一 index，`reindex(codes)` 后自然为 NaN（与"0 家机构" vs "0 家机构"的变化量 0 不同）。

**Merton(1987) 信息摩擦**：更多分析师覆盖 = 更多投资者了解 = 持有者基础扩大 = 预期价格重估。预期方向：**+**（覆盖增加 = 看多信号）。

---

### 4.4 其他外部因子（3 个）

#### `factor_float_pct_30d` — 30 日内即将解禁比例

```python
# PIT：ann_date <= T（已公告）AND T < float_date <= T+30（尚未解禁）
upcoming = load_share_float(T, horizon_days=30)
float_ratio = upcoming["float_ratio"].sum(level="ts_code")  # 同一股票多笔解禁加总
```

无解禁的股票填 0（不是 NaN），因为"没有即将解禁"本身是信息。预期方向：**−**（近期解禁比例高 = 供给冲击 = 看空）。

#### `factor_insider_net_buy` — 股东/高管净增持

```python
# 过去 90 天内：增持 in_de='增持'，减持 in_de='减持'
buy_vol  = trades[trades["in_de"] == "增持"]["change_vol"].sum()
sell_vol = trades[trades["in_de"] == "减持"]["change_vol"].sum()
result = (buy_vol - sell_vol).fillna(0)  # 无动作的股票填 0
```

无增减持记录的股票填 0（不是 NaN）。预期方向：**+**（内部人净增持 = 内部人对股票有信心）。

#### `factor_pledge_ratio` — 股权质押比例

```python
snap = load_pledge_stat(T, codes)   # end_date <= T 最新记录
result = snap["pledge_ratio"]
```

使用 `end_date` 作为 PIT 基准（无 `ann_date`）：保守处理，可能有少量前视偏差（通常 0-30 天），可接受。预期方向：**−**（高质押比例 = 大股东流动性压力 = 风险信号）。

---

## 五、内部辅助函数

| 函数 | 说明 |
|---|---|
| `_all_trading_dates()` | 全市场交易日，lru_cache |
| `_valid_dates_before(T)` | T 及之前的交易日序列 |
| `_window_start(T, n)` | T 前 n 个交易日的第一天 |
| `_nan_series(codes, name)` | 全 NaN 占位 Series |
| `_agg_with_min(series, min_cnt, agg)` | 带最小样本检查的聚合 |
| `_compute_rsi(close, window)` | RSI 计算（被 rsi_6 / rsi_12 共用）|

---

## 六、落盘产物

本模块不直接落盘。由 `build_factor_panels.py` 写入 `data/processed/factor_panels/<name>.parquet`。

---

## 七、相关测试

**建议优先补充的测试**：
- `test_hk_hold_prelaunch`：2014-11-17 之前的调仓日调用 `hk_hold_*` 应返回全 NaN
- `test_north_flow_broadcast`：`north_flow_5d`（已剔除）的截面标准差为 0 的逻辑

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| 数据文件不存在（如 hk_hold.parquet 未下载）| `FileNotFoundError`（由 loader 抛出）|
| 窗口内无数据（如 2012 年无北向持仓）| 返回全 NaN Series |
| `analyst_rc_pit` 无 `eps` 列 | 立即返回全 NaN（L368 防御）|
| `pledge_ratio` 无近期记录 | 该股票为 NaN（非 0）|

---

## 九、数据流图

```
load_daily_quote(start, T)              → 技术因子（macd / rsi / boll / obv）
load_hk_hold(start, T)                 → hk_hold_ratio / hk_hold_chg
load_analyst_rc_pit(T, lookback=180)   → analyst_eps_revision / analyst_rating_chg
                                           eps_dispersion / analyst_cnt_chg
load_share_float(T, horizon=30)        → float_pct_30d
load_holder_trade_pit(T, lookback=90)  → insider_net_buy
load_pledge_stat(T)                    → pledge_ratio

每个 factor_xxx() → ts_code 为 index 的 Series（原始值）
    ↓ preprocess_factor（fin_sector_codes=None，量价/外部数据不过滤金融股）
    ↓ data/processed/factor_panels/<name>.parquet
```

---

## 十、领域知识补充

**北向资金的信息含义**：

沪深港通北向资金（A 股的外资机构投资者）通常被认为是"聪明钱"：具有更完善的研究能力和估值框架，对 A 股的基本面信息反应更理性。北向持仓增加被视为正向信号，但需注意两点：
1. 2014-11-17 前无数据，训练期前 2 年无此信号
2. 北向持仓受汇率、地缘政治因素影响，信号噪声较大

**分析师预期修正效应**：

分析师调高 EPS 预测通常是因为公司业绩超出原有预期（"盈余上调效应"）。由于市场对盈余修正的反应往往是渐进的（信息传播有延迟），调高预测后的 1-3 个月内，股票常常继续跑赢市场。这是 `analyst_eps_revision` 的理论依据。
