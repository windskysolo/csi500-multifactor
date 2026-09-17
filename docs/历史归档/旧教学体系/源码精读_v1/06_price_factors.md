# 06 — 量价因子构建 `src/factors/price_factors.py`

---

## 一、文件定位

```
流程位置：第 3 步（构建因子面板）的核心计算模块之二
读取：load_daily_quote / load_daily_basic / load_margin / load_moneyflow / get_holder_pit_raw
写入：无（返回 Series，由 build_factor_panels.py 汇总写盘）
被谁调用：scripts/build_factor_panels.py
调用谁：src/data/loader.py、src/config.py
```

本模块共实现 **12 个量价因子**（动量/反转 4 个 + 波动率 3 个 + 流动性 2 个 + 资金流向 3 个）。

**与财务因子的关键区别：**

| 维度 | 量价因子 | 财务因子 |
|------|---------|---------|
| PIT 风险 | **无**（行情数据 T 日收盘后即可用）| 有（需要公告日过滤）|
| 数据窗口 | 滚动窗口（20/60/120/240 日）| 单期快照或 TTM |
| 频率 | 使用日频行情计算 | 使用季报数据 |

---

## 二、模块顶部

### 窗口参数常量（L44-55）

```python
WINDOW_RET_1M  = 20    # 短期反转：20 个交易日 ≈ 1 个月
WINDOW_MOM_SKIP = 20   # 动量跳过最近 20 个交易日（消除反转干扰）
WINDOW_MOM_6   = 120   # 6 个月动量：120 交易日
WINDOW_MOM_12  = 240   # 12 个月动量：240 交易日
WINDOW_VOL     = 60    # 波动率：60 个交易日
WINDOW_TURN    = 20    # 换手率：20 个交易日
WINDOW_AMIHUD  = 20    # Amihud：20 个交易日
WINDOW_FLOW    = 20    # 资金流向：20 个交易日
MIN_VOL_PERIODS = 40   # 波动率至少需要 40 个有效日
MIN_IVOL_PERIODS = 30  # 特质波动率 OLS 至少需要 30 个观测
HOLDER_STALE_MONTHS = 18  # 股东人数数据陈旧度阈值
```

**为什么用交易日数而不是日历天数？** A 股每年约 252 个交易日，60 个交易日 ≈ 3 个月，240 个交易日 ≈ 1 年。用交易日避免了节假日造成的窗口不一致（如春节期间有一周没有交易日，用日历天数会漏掉这段时间）。

### `@lru_cache` 交易日序列（L62-71）

```python
@lru_cache(maxsize=1)
def _all_trading_dates() -> pd.DatetimeIndex:
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    return iq.index.sort_values()
```

`lru_cache(maxsize=1)` 让函数在进程生命周期内只读一次 `index_quote.parquet`（2430 行，极小），之后都从内存返回。与 `loader._CACHE` 的区别：`lru_cache` 是函数级缓存，`_CACHE` 是模块级字典缓存，两者机制不同但效果相似。

---

## 三、内部工具函数

### `_valid_dates_before(rebalance_date)` — 历史交易日序列（L74-77）

```python
def _valid_dates_before(rebalance_date: pd.Timestamp) -> pd.DatetimeIndex:
    all_dates = _all_trading_dates()
    return all_dates[all_dates <= rebalance_date]   # 含当日
```

返回 T 日及之前所有实际交易日的列表。**用于确定滚动窗口的起止日期。**

---

### `_window_dates(rebalance_date, n_window, skip_recent, extra_base)` — 窗口计算（L80-112）

这是整个量价因子计算的核心辅助函数，负责把"窗口大小"转换为"实际日期范围"。

```python
def _window_dates(
    rebalance_date: pd.Timestamp,
    n_window: int,       # 窗口内的交易日数
    skip_recent: int = 0,  # 从 T 往前跳过的交易日数
    extra_base: int = 1,   # 额外的基点日数（通常为1）
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
```

**逻辑图解（以 `ret_1m` 为例，n_window=20, skip_recent=0）：**

```
时间轴（交易日）：
...  [T-21]  [T-20]  [T-19]  ...  [T-1]  [T]
      ↑                              ↑      ↑
   start（基点日）              end（窗口末）= T

valid = [..., T-21, T-20, ..., T-1, T]
n = len(valid)

end_pos   = n - 1 - 0       = n - 1  → valid[n-1] = T
start_pos = end_pos - 20    = n - 21 → valid[n-21] = T-20（第21个交易日前）

返回：(valid[n-21], valid[n-1]) = (T-20, T)

收益率计算：close_adj[T] / close_adj[T-20] - 1
= 恰好 20 个交易日的区间收益
```

**以 `mom_6_1` 为例（n_window=100, skip_recent=20）：**

```
...  [T-120] ... [T-20]  [T-19]  ...  [T]
      ↑             ↑
   start（基点）  end（跳过最近20天后的末端）

返回：(T-120, T-20)

收益率：close_adj[T-20] / close_adj[T-120] - 1
= 跳过最近 1 个月的 6 个月动量
```

**历史不足时返回 `(None, None)`（L107-108）：**

```python
total_needed = n_window + skip_recent + extra_base
if n < total_needed:
    return None, None
```

调用方检查到 `start is None` 时返回空 Series（而非崩溃），保证 2016 年初的冷启动期能正常运行。

---

## 四、核心函数逐一解析

### 4.1 动量/反转类（4 个）

---

#### `factor_ret_1m` — 短期反转（L119-146）

```
公式：ret_1m = close_adj[T] / close_adj[T-20] - 1
预期方向：-（近期涨幅大的股票下期倾向回调）
```

```python
start, end = _window_dates(rebalance_date, WINDOW_RET_1M, skip_recent=0)
dq = load_daily_quote(start, end, codes=codes)
adj = dq["close_adj"].unstack("ts_code")
result = adj.iloc[-1] / adj.iloc[0] - 1    # 末端/起点 - 1
```

**为什么用两端价格之比，而不是 `ret.prod()`？**（L128-130 注释）

`ret.prod()` 计算累计收益需要每一天的 `ret` 都不缺失。如果某天停牌（`ret = NaN`），`prod()` 会产生错误（缺失行被跳过，相当于多乘了一个 1）。两端价格之比只需要首尾两天有价格数据，中间停牌的天数不影响结果。

**短期反转的金融解释：** A 股散户比例高，存在"追涨杀跌"行为，导致短期过度反应——近期涨幅过大的股票，在机构的"均值回归"操作下，下期倾向于下跌。

---

#### `factor_mom_6_1` — 6 个月动量（L149-177）

```
公式：mom_6_1 = close_adj[T-20] / close_adj[T-120] - 1
窗口：T-120 到 T-20，恰好 100 个交易日（约 5 个月）
预期方向：+（中期动量效应）
```

```python
n_window = WINDOW_MOM_6 - WINDOW_MOM_SKIP   # 120 - 20 = 100
start, end = _window_dates(rebalance_date, n_window, skip_recent=WINDOW_MOM_SKIP)
```

**"跳过最近 20 天"的目的：** 动量因子（正向）和反转因子（负向）在时间上有部分重叠。如果动量窗口包含最近 1 个月，反转效应会抵消动量效应，使因子信号减弱。跳过最近 20 天（约 1 个月），让动量因子专注于捕捉 1-6 个月的"中期趋势"。

---

#### `factor_mom_12_1` — 12 个月动量（L180-208）

```
公式：mom_12_1 = close_adj[T-20] / close_adj[T-240] - 1
窗口：T-240 到 T-20，恰好 220 个交易日（约 11 个月）
预期方向：+（长期动量效应）
```

早期调仓日（2016 年初）可能历史不足 240 天，此时返回全 NaN 是正常的冷启动行为（L191 注释）。

---

#### `factor_holder_chg` — 股东人数变化率（L211-279）

```
公式：holder_chg = -(holder_num_latest - holder_num_prev) / holder_num_prev
        = -(Δholder_num) / prev
预期方向：+（股东人数减少 = 筹码集中 = 看好信号）
```

这是本模块中唯一的 **PIT 因子**（其他因子都是纯市场数据，无 PIT 问题）。

**执行逻辑（L230-279）：**

**Step1：PIT 过滤（L232-238）**

```python
vis = hp[
    (hp["available_date"] <= rebalance_date)   # 可用日 ≤ T
    & (hp["end_date"] < rebalance_date)         # 报告期 < T
    & (hp["pit_date"] <= rebalance_date)        # 公告日 ≤ T
]
```

用 `available_date`（`max(pit_date, end_date)`）而非单纯的 `pit_date`，处理少量 `pit_date ≤ end_date` 的异常记录（详见 `02_data_loader.md` 3.8节）。

**Step2：对每只股票取最新两期（L248-252）**

```python
vis_dedup["_rank"] = vis_dedup.groupby("ts_code").cumcount(ascending=False)
latest = vis_dedup[vis_dedup["_rank"] == 0]   # rank=0 是最新期
prev   = vis_dedup[vis_dedup["_rank"] == 1]   # rank=1 是次新期
```

`cumcount(ascending=False)` 从最新期开始计数（最新的 rank=0，次新的 rank=1），取这两期做差。

**Step3：双重陈旧度检查（L261-268）**

```python
# 检查1：最新期距 T 不超过 18 个月
stale_cutoff = rebalance_date - pd.DateOffset(months=HOLDER_STALE_MONTHS)
fresh = latest.loc[common, "end_date"] >= stale_cutoff

# 检查2：两期时间跨度不超过 18 个月（防止用跨度过大的数据）
gap_days = (latest["end_date"] - prev["end_date"]).dt.days
reasonable_gap = gap_days <= HOLDER_STALE_MONTHS * 31
```

任一条件不满足，该股票的因子值置 NaN。

---

### 4.2 波动率类（3 个）

---

#### `factor_vol_60d` — 历史波动率（L286-316）

```
公式：vol_60d = std(daily_ret, window=60)
预期方向：-（低波动溢价：低波动率的股票预期超额收益更高）
```

```python
ret_pivot = dq["ret"].unstack("ts_code")      # (60日, N只股票) 矩阵
result = ret_pivot.std(ddof=1, skipna=True)   # 每列的标准差
valid_counts = ret_pivot.notna().sum()
result[valid_counts < MIN_VOL_PERIODS] = np.nan   # 有效天数 < 40 → NaN
```

**`ddof=1`（样本标准差）：** 分母用 `n-1`（贝塞尔校正），而非总体标准差的 `n`。对于 60 个观测，样本标准差和总体标准差差别约 1%，影响很小，但沿用统计惯例。

**停牌日的处理：** `daily_quote.ret` 在停牌日为 0（股价不变 = 零收益），而非 NaN。这些零值**参与**标准差计算（压低了波动率估计）。这是有意为之：如果排除停牌日，会高估波动率；包含零值后，停牌多的股票波动率被系统性低估，但这反而在某种程度上反映了其流动性差（停牌期间无法套利的风险）。

**低波动溢价的解释：** 低波动股票之所以有超额收益，主要归因于行为金融（投资者偏好"彩票股"，愿意为高波动支付溢价，导致高波动股票估值偏高）。

---

#### `factor_ivol_60d` — 特质波动率（L319-378）

```
方法：CAPM 残差标准差
模型：ret_stock = α + β × ret_market + ε
ivol = std(ε, window=60)
预期方向：-（低特质波动率溢价）
```

特质波动率（Idiosyncratic Volatility）是剔除系统性风险（市场 Beta）之后的个股特有风险。

**向量化 OLS 实现（L366-373）：**

```python
Y = ret_pivot.fillna(0).values           # (T, N) — N只股票同时处理
X = np.column_stack([np.ones(len(mkt)), mkt.values])  # (T, 2) — 截距 + 市场收益

# 向量化 OLS：一次性对所有 N 只股票求解
beta, *_ = np.linalg.lstsq(X, Y, rcond=None)   # 返回 (2, N)

residuals = Y - X @ beta                         # (T, N) 残差矩阵
ivol = residuals.std(axis=0, ddof=1)             # (N,) 每只股票的残差标准差
```

**为什么不逐股回归？** 如果对 500 只股票逐一做 OLS，需要调用 500 次 `statsmodels.OLS`，性能极差。向量化 OLS 利用矩阵乘法一次性处理所有股票，速度提升约 100 倍。

**填充缺失为 0（L363）：** IVOL 计算前用 0 填充停牌日的 NaN（停牌 = 零收益假设），让 OLS 能处理完整矩阵。最后再根据有效天数过滤（少于 30 天有效数据的股票置 NaN）。

---

#### `factor_max_ret` — 最大日收益率（L381-408）

```
公式：max_ret = max(|daily_ret|, window=20)
预期方向：-（Bali et al. 2011 MAX效应：高极端涨幅 → 下期回调）
```

这个因子捕捉"彩票效应"：投资者偏爱极端上涨的股票（类似彩票），愿意溢价购买，导致这类股票短期过热，下期回报为负。

---

### 4.3 流动性类（2 个）

---

#### `factor_turn_20d` — 换手率（L415-442）

```
公式：turn_20d = mean(daily_turnover_f, window=20)
来源：daily_basic.turnover_rate_f（自由流通换手率，百分比）
预期方向：-（低换手溢价：换手率低的股票预期超额收益）
```

**自由流通换手率 vs 全流通换手率：** `turnover_rate_f` 用自由流通股本（而非全部流通股）作为分母，更能反映活跃交易者的行为。如果一家公司 80% 的股份由大股东锁定，只有 20% 可自由流通，实际交易活跃度应该用 20% 来衡量，而不是 100%。

**低换手溢价的解释：** 换手率高通常意味着散户频繁追涨杀跌，机构通常倾向于持有低换手率（低热度）的股票，这类股票往往被低估。

---

#### `factor_amihud` — Amihud 非流动性（L445-479）

```
公式：amihud = mean(|daily_ret| / daily_amount, window=20)
       daily_amount 单位：千元
预期方向：+（非流动性越高，收益越高——流动性溢价）
```

**Amihud (2002) 非流动性指标的直觉：**

`|ret| / amount` = 每元成交额引起的价格变动幅度。这个比率越大，说明用同样的钱能更大幅度地推动股价，即股票流动性越差。流动性差的股票有"流动性溢价"——长期持有者要求更高的回报来补偿他们在需要急售时可能承受的价格冲击。

**停牌日处理（L474-475）：**

```python
amount = dq["amount"].replace(0, np.nan)   # 停牌日成交额为 0 → NaN，不参与均值
daily_ratio = ret_abs / amount              # 停牌日比率 = NaN（自动跳过）
result = daily_ratio.unstack("ts_code").mean(skipna=True)
```

这里 `replace(0, np.nan)` 专门处理停牌日的零成交额，避免分母为零导致 `inf`。`skipna=True` 让停牌日不参与均值计算（相当于只统计有实际成交的天数）。

---

### 4.4 资金流向类（3 个）

---

#### `factor_margin_ratio` — 融资余额占比（L486-519）

```
公式：margin_ratio = 融资余额(rzye) / 总市值（T日截面，非窗口均值）
来源：margin.rzye / daily_basic.total_mv
预期方向：待测（L498 注释：可能反映看多，也可能是拥挤信号）
```

**单位转换细节（L512-513）：**

```python
mv_yuan = basic.loc[rebalance_date]["total_mv"] * 10_000.0  # 万元 → 元
result = rzye_t[common] / mv_yuan[common]                    # 元/元 = 无量纲
```

**非两融标的股票（L498）：** 许多成分股不是两融标的，其 `rzye=NaN`，因子值保持 NaN。`preprocess_factor` 调用时会留作 NaN，不填充 0。这些股票在组合优化前才填充为 0（横截面均值），表示"在资金流向维度没有信号"。

---

#### `factor_short_ratio` — 融券余额占比（L522-553）

```
公式：short_ratio = 融券余额(rqye) / 总市值
预期方向：-（融券余额高 = 机构看空信号）
```

与 `margin_ratio` 方向相反。融资（margin）表示投资者借钱做多，融券（short）表示借股票做空。A 股做空难度高、融券成本高，所以融券行为主要来自机构投资者，通常是有信息优势的信号。

---

#### `factor_large_net_inflow` — 大单净流入占比（L556-595）

```
公式：large_net_inflow = mean(net_mf_amount × 10 / amount, window=20)
       net_mf_amount：万元；amount：千元；×10 换算统一量纲
预期方向：+（大单净流入反映机构看多）
```

**单位转换（L591）：**

```python
daily_ratio = (net_mf * 10) / amount
# net_mf_amount 单位：万元 → × 10 → 千元（与 amount 同量纲）
# ratio = 千元/千元 = 无量纲
```

`net_mf_amount` 是 Tushare `moneyflow` 接口提供的"大单净流入"（大单定义：成交金额 ≥ 20 万元，通常被认为是机构或大户行为）。20 日均值消除了单日噪声，反映近期持续的资金流向趋势。

---

## 五、批量构建入口

### `build_all_price_factors(rebalance_date, codes, factor_names)` — 批量入口（L622-659）

与 `build_all_financial_factors` 结构完全相同：

- 默认构建全部 12 个因子
- 支持 `factor_names` 指定子集
- 单个因子失败用全 NaN 填充，不中断其他因子

---

## 六、数据流图

```
_all_trading_dates()（lru_cache）
        ↓
_valid_dates_before(T)
        ↓
_window_dates(T, n_window, skip_recent)
        ↓
(start, end) 日期范围

load_daily_quote(start, end, codes)
        ├─→ close_adj → factor_ret_1m / factor_mom_6_1 / factor_mom_12_1
        ├─→ ret       → factor_vol_60d / factor_ivol_60d / factor_max_ret / factor_amihud
        └─→ amount    → factor_amihud / factor_large_net_inflow

load_daily_basic(start, end, codes)
        ├─→ turnover_rate_f → factor_turn_20d
        └─→ total_mv        → factor_margin_ratio / factor_short_ratio

load_margin(T, T, codes)
        ├─→ rzye → factor_margin_ratio
        └─→ rqye → factor_short_ratio

load_moneyflow(start, end, codes)
        └─→ net_mf_amount → factor_large_net_inflow

get_holder_pit_raw()
        └─→ holder_num → factor_holder_chg（PIT过滤 + 最新两期 + 陈旧度检查）

index_quote.parquet（lru_cache）
        └─→ index_ret → factor_ivol_60d（CAPM市场收益率）
```

---

## 七、领域知识补充

**动量效应与反转效应的时间关系：**

| 时间跨度 | 效应 | 因子 |
|---------|------|------|
| 过去 1 个月 | 短期反转（均值回归）| `ret_1m`（负向）|
| 过去 2-12 个月（跳过最近 1 个月）| 中/长期动量 | `mom_6_1`、`mom_12_1`（正向）|
| 过去 3-5 年 | 长期反转 | （本项目未实现）|

动量效应（Jegadeesh & Titman 1993）在美股非常显著，但 A 股的动量效应比美股弱，且方向有时相反（尤其在牛市快速上涨阶段）。这是因为 A 股散户主导，追涨行为反而在短期形成动量，但在一定周期后出现更强烈的反转。

**低波动溢价（Low Volatility Anomaly）：**

理论上，高风险应对应高回报（资本资产定价模型 CAPM 的核心假设）。但实证上，低波动率股票的长期收益反而更好（"低波动溢价"）。主要解释：
- **杠杆约束**：机构受杠杆限制，只能买高 Beta 股票来提高收益，推高了高波动股票的估值
- **彩票需求**：散户偏好"彩票型"高波动股票，支付溢价，导致估值偏高
- **代理问题**：基金经理追求跑赢基准而非绝对收益，偏好高 Beta 股票

在 A 股，低波动溢价存在但稳定性不如美股，在大盘快速上涨阶段（2019、2020年初）低波动因子明显跑输。
