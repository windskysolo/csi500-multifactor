# 动量因子实施计划（月频版）

> 创建日期：2026-05-25
> 修订日期：2026-05-25（执行版修订）
> 基于：`factor_research_guide.md §8` + 已有评估结果（commit 7423f1e，ic_result.csv / shift_result.csv）
> 目标：为动量维度补充有效的量价因子，填补当前因子池的维度空缺
> 测试集剩余次数：2 次（严禁本计划触及测试集）

---

## 零、执行版修订要点（必须遵守）

本计划可以直接执行，但以下修订优先级高于正文中旧表述：

1. **不触碰测试集**：所有命令默认只覆盖 `TRAIN_START ~ VALID_END`；不得传入 `--allow-test-set`，不得把 `--end-date` 设到 `2023-01-01` 或之后。
2. **首次评估写入隔离目录**：新因子初评不直接覆盖主 `reports/factor_evaluation/final_factors.json`，先写入 `reports/factor_evaluation_momentum_sprint/`。确认通过 Gate 后，再决定是否刷新主评估结果。
3. **`mom_consistency_6` 禁止使用 `pct_change()` 默认行为**：pandas 会默认前向填充缺失价格，和停牌/月末缺失应保留 NaN 的设计冲突。必须用显式除法或 `pct_change(fill_method=None)`。
4. **`high_52w_v2` 的 52 周窗口要在跳过近 20 日之后仍保留 252 个交易日**：窗口起点不能简单用 `valid[-WINDOW_HIGH_52W]`，否则实际高点窗口少了 20 个交易日。
5. **lead 结果不能预设**：`ind_adj_mom_6_1` 理论上不应新增未来信息，但不能写成“继承 lead_ratio=0”；所有新因子是否干净以 Gate 2 实测为准。

## 一、现有评估结果复盘（不重复踩坑）

三个已实现的动量类因子全部被排除，原因各不同：

| 因子 | 训练期 IC_IR | lead_ratio | 排除原因分析 |
|------|----------:|----------:|-------------|
| `high_52w` | 0.054 | **37.6x ⛔** | 因子分子 = T 日收盘价；远期收益分母 = T 日收盘价；机械负相关抵消动量信号，lead 检验极端异常 |
| `ind_adj_mom` | 0.125 | **3.25x ⛔** | 底层用 `mom_12_1`（12 个月），而 `mom_12_1` 本身 lead_reverse=True；行业调整放大了问题 |
| `mom_risk_adj` | 0.063 | **5.80x ⛔** | 分子 = `mom_12_1`，同上；分母 `ivol_60d` 无帮助，lead 问题传导 |
| `mom_6_1` | 0.042 | **0.0 ✅** | **唯一 lead 干净的动量基底**；IC_IR 低但方向中性、无数据污染 |
| `mom_12_1` | 0.129 | lead_reject | IC_IR 看似更高，但 lead_reverse=True，信号是"滞后的"而非前瞻的 |

**核心结论**：
- `mom_6_1` 是唯一干净的动量底层，6 个月窗口 + 跳过 20 个交易日
- `ind_adj_mom_6_1` 以 `mom_6_1` 为底层，理论上不应新增未来信息；但行业去均值后的 lead_ratio 不可预设，必须以 Gate 2 实测为准
- `high_52w` 的机械拖累问题需要修改分子（用 T-20 价格代替 T 价格）才能消除
- `mom_risk_adj` 已有评估，不再重做；若要测风险调整动量，需等 `mom_6_1` 底层修复后再做

---

## 二、Sprint 1：立即实现（3 个因子）

### S1-1　`ind_adj_mom_6_1`　行业调整 6 个月动量

**优先级**：最高（`mom_6_1` 已通过 lead 测试，行业调整理论上提升 IC）

**公式**：

```
ind_adj_mom_6_1[i, T] = mom_6_1[i, T] − mean(mom_6_1[j, T] | j ∈ same_industry(i))
```

其中 `mom_6_1[i, T] = close_adj(T-20td) / close_adj(T-120td) - 1`

**与旧 `ind_adj_mom` 的唯一区别**：底层从 `factor_mom_12_1` 改为 `factor_mom_6_1`。

#### 实现规格

```
文件：src/factors/price_factors.py
函数名：factor_ind_adj_mom_6_1
位置：阶段 5 动量因子区块（factor_high_52w 之后）
```

```python
def factor_ind_adj_mom_6_1(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    行业调整 6 个月动量（ind_adj_mom 的 6 个月修正版）。

    公式：mom_6_1_i − equal_weight_mean(mom_6_1, same SW2021 industry)
    底层用 factor_mom_6_1（100 交易日，跳过近 20 日），消除 12 个月版本的 lead 问题。

    与旧 ind_adj_mom 的区别：底层从 mom_12_1 改为 mom_6_1。
    旧版 lead_ratio = 3.25x（Gate 2 拒绝）；本版预期显著低于旧版，但最终以 Gate 2 实测为准。

    行业均值：等权（不用市值加权），只在当期中证500宇宙内计算。
    行业分类：申万 2021 一级（31 个行业），来自 load_industry。

    若行业数据在调仓日不可用，返回全 NaN（不降级为 mom_6_1，避免两个因子信息重叠）。

    Args:
        rebalance_date: 调仓日 T
        codes:          当期可投资域股票代码（来自 get_investable_universe）
    Returns:
        ts_code → 行业调整 6 个月动量（小数形式）；数据不足时为 NaN
    Time alignment: 底层 mom_6_1 窗口 [T-120td, T-20td]，无未来数据
    Data deps: daily_quote.parquet, industry.parquet
    """
    raw_mom = factor_mom_6_1(rebalance_date, codes)
    if raw_mom.isna().all():
        return raw_mom.rename("ind_adj_mom_6_1")

    ind_df = load_industry(rebalance_date, rebalance_date, codes=codes)
    if ind_df.empty:
        return pd.Series(np.nan, index=raw_mom.index, name="ind_adj_mom_6_1")

    trade_dates = ind_df.index.get_level_values("trade_date")
    if rebalance_date not in trade_dates:
        return pd.Series(np.nan, index=raw_mom.index, name="ind_adj_mom_6_1")

    ind_series  = ind_df.loc[rebalance_date]["industry_code"]
    ind_aligned = ind_series.reindex(raw_mom.index)

    # 等权行业均值（transform 保持 index 对齐）
    ind_mean = raw_mom.groupby(ind_aligned).transform("mean")
    result   = raw_mom - ind_mean
    result.name = "ind_adj_mom_6_1"
    return result
```

#### 正确性保证

| 检查项 | 验证方式 |
|--------|---------|
| 行业均值只用当期宇宙 | 传入 `codes=codes` 限制 `load_industry` 范围 |
| 等权非市值加权 | `groupby.transform("mean")`，无权重参数 |
| 行业分类版本 | `load_industry` 已返回 SW2021 31 个行业（`loader.py` 注释确认） |
| 行业 NaN 处理 | `ind_aligned` 中 NaN 的股票在 `groupby` 中自动排除（不影响其余行业均值），返回 NaN |
| 底层窗口 | `factor_mom_6_1` 使用 [T-120td, T-20td]，跳过近 20 日，无未来数据 |

---

### S1-2　`mom_consistency_6`　月涨幅一致性动量

**优先级**：高（全新因子，与 M1/M2 方向互补）

**公式**：

```
mom_consistency_6[i, T] = (正收益月数 / 6) × sign(6 个月累计收益)

其中：6 个月 = T 前第 7 个月末 到 T 前第 1 个月末（跳过最近 1 个月，共 6 段月度收益）
每月收益 = close_adj(月末) / close_adj(前月末) - 1，截尾 [-0.4, +0.4]
```

**经济逻辑**：过去 6 个月中连续上涨（而非单月暴涨）的股票，趋势更真实、更可持续。

#### 需要新增的辅助函数 `_prev_month_end_dates`

这是整个 M4 最容易出错的地方。

```python
def _prev_month_end_dates(
    rebalance_date: pd.Timestamp,
    n: int,
) -> list[pd.Timestamp]:
    """
    返回 rebalance_date 之前 n 个自然月的最后交易日列表（从旧到新）。

    rebalance_date 本身是月末最后交易日（来自 get_rebalance_dates）。
    例：rebalance_date = 2019-06-28，n=7
      → [last_td(Nov2018), last_td(Dec2018), ..., last_td(May2019)]
        （k=7 到 k=1 对应的月末交易日，共 7 个）

    实现：
      ref_k = rebalance_date - DateOffset(months=k)
      取 ref_k.year / ref_k.month，在交易日历中找该月最后一天。

    注意 DateOffset 对月末日期的处理：
      2013-03-29 - DateOffset(months=1) = 2013-02-28（pandas 自动截断）
      → 正确定位 2013 年 2 月，找到最后交易日 2013-02-28 ✓

    返回空列表表示历史不足（调用方应返回全 NaN）。
    """
    all_dates = _all_trading_dates()
    result = []
    for k in range(n, 0, -1):          # k 从大（最老）到小（最近），输出从旧到新
        ref = rebalance_date - pd.DateOffset(months=k)
        y, m = ref.year, ref.month
        month_dates = all_dates[(all_dates.year == y) & (all_dates.month == m)]
        if len(month_dates) == 0:
            return []                  # 该月无交易日，历史不足
        result.append(month_dates[-1]) # 取该月最后一个交易日
    return result
```

#### 实现规格

```
文件：src/factors/price_factors.py
函数名：factor_mom_consistency_6
位置：factor_ind_adj_mom_6_1 之后
窗口常量：WINDOW_CONSISTENCY_LOOKBACK = 6  （月数）
          WINDOW_CONSISTENCY_SKIP     = 1  （跳过最近月数）
          MIN_CONSISTENCY_MONTHS      = 4  （最少有效月数，否则返回 NaN）
          RET_CAP                     = 0.4（单月收益截尾上限）
```

```python
# 在常量区增加
WINDOW_CONSISTENCY_LOOKBACK = 6
WINDOW_CONSISTENCY_SKIP     = 1
MIN_CONSISTENCY_MONTHS      = 4   # 有效月数低于此阈值返回 NaN
MONTHLY_RET_CAP             = 0.4 # A股涨跌停上限，单月超此值通常是停牌复牌极端情形


def factor_mom_consistency_6(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    月涨幅一致性动量因子：过去 6 个月（跳过最近 1 个月）中正收益月数占比 × 总收益方向。

    公式：
      正收益月数比例 = count(monthly_ret > 0) / 6
      总收益方向    = sign(close_adj[T-1m] / close_adj[T-7m] - 1)
      因子值        = 正收益月数比例 × 总收益方向

    优势：抗单月爆涨干扰——5 个月平盘 + 1 个月暴涨 50% 的股票与
    6 个月稳定上涨的股票，前者累计收益可能更高，但一致性因子分更低。

    月度收益截尾 [-0.4, +0.4]：A 股月频策略中，单月超 40% 绝对收益几乎
    只出现在停牌复牌或极端行情，属异常值，截尾后计数更稳健。

    Args:
        rebalance_date: 调仓日 T
        codes:          当期可投资域股票代码
    Returns:
        ts_code → 一致性动量（[-1, +1] 区间）；有效月数 < 4 时为 NaN
    Time alignment: 最新用到 T-1m 月末价格，窗口 [T-7m末, T-1m末]，无未来数据
    Data deps: daily_quote.parquet
    """
    n_calendar_months = WINDOW_CONSISTENCY_LOOKBACK + WINDOW_CONSISTENCY_SKIP  # skip=1 时为 7

    # 获取 T 前 n_calendar_months 个自然月末实际交易日（最老 → 最新）。
    # 当 skip=1 时，使用 [T-7m末, T-1m末] 共 7 个价格点，形成 6 段月收益。
    # 如果未来把 skip 改成 2，则使用 [T-8m末, T-2m末]，自然剔除最近 1 个完整月。
    month_pool = _prev_month_end_dates(rebalance_date, n=n_calendar_months)
    if len(month_pool) < n_calendar_months:
        return pd.Series(
            np.nan,
            index=pd.Index(codes, name="ts_code"),
            name="mom_consistency_6",
        )
    month_ends = month_pool[: WINDOW_CONSISTENCY_LOOKBACK + 1]

    # 加载 [T-7m末, T-1m末] 区间的日行情（只取这 7 个月末日期的数据）
    dq = load_daily_quote(month_ends[0], month_ends[-1], codes=codes)
    if dq.empty:
        return pd.Series(
            np.nan,
            index=pd.Index(codes, name="ts_code"),
            name="mom_consistency_6",
        )

    close_adj = dq["close_adj"].unstack("ts_code")  # 日期 × ts_code
    # 筛选出 7 个月末价格（reindex 在缺失日期产生 NaN，停牌股自然处理）
    monthly_prices = close_adj.reindex(month_ends)   # 7 行 × n_codes 列

    # 计算 6 个月度收益（截尾）。
    # 不用 pct_change() 默认行为，因为它会前向填充缺失价格，掩盖停牌/月末缺失。
    monthly_rets = (monthly_prices / monthly_prices.shift(1) - 1).iloc[1:]
    monthly_rets = monthly_rets.clip(-MONTHLY_RET_CAP, MONTHLY_RET_CAP)

    # 有效月数检查（数据不足 4 个月则返回 NaN）
    valid_months = monthly_rets.notna().sum()
    positive_count = (monthly_rets > 0).sum()

    # 正收益月数比例（分母固定为 6，有效月数不足则比值偏低，已足够）
    positive_ratio = positive_count / WINDOW_CONSISTENCY_LOOKBACK

    # 6 个月累计收益方向（用 7 个月末中的首尾两个价格）
    cum_ret  = monthly_prices.iloc[-1] / monthly_prices.iloc[0] - 1
    cum_sign = np.sign(cum_ret)

    result = positive_ratio * cum_sign
    result[valid_months < MIN_CONSISTENCY_MONTHS] = np.nan
    result.name = "mom_consistency_6"
    return result.reindex(codes)
```

#### 正确性保证

| 检查项 | 处理方式 |
|--------|---------|
| 月末日期准确性 | `_prev_month_end_dates` 用交易日历找月末，不依赖 DateOffset 精确对齐 |
| 历史不足（早期调仓日）| `WINDOW_CONSISTENCY_LOOKBACK + WINDOW_CONSISTENCY_SKIP` 对应的月末价格点不满足时返回全 NaN |
| 停牌月 | 该月 `close_adj` 为 NaN → 显式除法得到 NaN → 不计入正收益计数，有效月数减少 |
| 缺失价格处理 | 禁止使用 `pct_change()` 默认前向填充；若使用 `pct_change` 必须写 `fill_method=None` |
| 极端收益（停牌复牌）| 截尾至 `[-0.4, +0.4]`，避免爆炸性月份扭曲一致性评分 |
| 有效月数门槛 | `< MIN_CONSISTENCY_MONTHS = 4` 时返回 NaN（4 个月数据可计算出可信比率） |
| 因子值域 | `positive_ratio ∈ [0,1]`，`cum_sign ∈ {-1, 0, +1}`，结果 `∈ [-1,+1]` |
| cum_sign = 0（收益恰好为零）| 结果为 0（极边界，不影响排名） |

---

### S1-3　`high_52w_v2`　修正版 52 周高点比率

**优先级**：中（修复已知 bug，成本低，结果未知）

**问题根源**：原版 `high_52w` 分子用 T 日收盘价，同一价格出现在远期收益的分母中（`fwd_ret[T] = close(T+1)/close(T) - 1`），产生机械负相关，导致 IC_IR 仅 0.054 且 lead_ratio = 37.6x。

**修正**：分子改用 `T-20 交易日`的收盘价（与分母窗口终点一致），彻底消除与远期收益的分母重叠：

```
high_52w_v2[i, T] = close_adj(T-20td) / max(high_adj[T-252td : T-20td])
```

两端共用同一终点 `T-20`，分子不出现在 `fwd_ret[T]` 的计算中。

#### 实现规格

```
文件：src/factors/price_factors.py
函数名：factor_high_52w_v2
位置：factor_high_52w 之后，注意保留原 factor_high_52w（历史对比用）
```

```python
def factor_high_52w_v2(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    修正版 52 周高点比率（v2）：分子用 T-20 收盘价，消除机械拖累。

    原版问题：分子 = close_adj(T)，与 fwd_ret[T]=close(T+1)/close(T) 共享 close(T)，
    产生机械负相关，IC 被压低至 0.054，lead_ratio = 37.6x（Gate 2 拒绝）。

    修正：分子改为 close_adj(T-20td)，与分母窗口终点（T-20td）重合，
    但 T-20td 的价格不出现在 fwd_ret[T] 的计算中，消除机械拖累。

    窗口：[T-252td, T-20td]（约 1 年，跳过最近 20 个交易日）
    分子：close_adj(T-20td)
    分母：max(high × adj_factor, [T-252td, T-20td])

    Args:
        rebalance_date: 调仓日 T
        codes:          当期可投资域股票代码
    Returns:
        ts_code → close_adj(T-20) / max_52w_high（无量纲，≤ 1）；历史不足时为 NaN
    Time alignment: 窗口 [T-252td, T-20td]，不包含 T 日数据，无未来数据
    Data deps: daily_quote.parquet
    """
    valid = _valid_dates_before(rebalance_date)
    needed = WINDOW_HIGH_52W + WINDOW_MOM_SKIP
    if len(valid) < needed:
        return pd.Series(
            np.nan,
            index=pd.Index(codes, name="ts_code"),
            name="high_52w_v2",
        )

    window_end_pos   = len(valid) - 1 - WINDOW_MOM_SKIP
    window_start_pos = window_end_pos - WINDOW_HIGH_52W + 1
    window_end       = valid[window_end_pos]        # T 前第 21 个交易日（T-20td）
    window_start     = valid[window_start_pos]      # 跳过近 20 日后，仍保留 252 个交易日窗口

    dq = load_daily_quote(window_start, window_end, codes=codes)
    if dq.empty:
        return pd.Series(
            np.nan,
            index=pd.Index(codes, name="ts_code"),
            name="high_52w_v2",
        )

    # 分母：52 周最高价（后复权）
    high_adj = (dq["high"] * dq["adj_factor"]).unstack("ts_code")
    high_52w_max = high_adj.max().replace(0, np.nan)

    # 分子：T-20 当日后复权收盘价（与分母窗口终点相同，不引入 T 日价格）
    close_adj_panel = dq["close_adj"].unstack("ts_code")
    if window_end not in close_adj_panel.index:
        return pd.Series(
            np.nan,
            index=pd.Index(codes, name="ts_code"),
            name="high_52w_v2",
        )
    close_t_minus_20 = close_adj_panel.loc[window_end]

    result      = close_t_minus_20 / high_52w_max
    result.name = "high_52w_v2"
    return result.reindex(codes)
```

#### 与原版的对比

| 项目 | `high_52w`（原版）| `high_52w_v2`（修正版）|
|------|:---:|:---:|
| 分子 | `close_adj(T)` | `close_adj(T-20td)` |
| 分母 | `max_high(T-252, T-20)` | 跳过近 20 日后，取满 252 个交易日的 `max_high` |
| 与 fwd_ret 共享变量 | ✅ close(T) 共享 → 机械拖累 | ❌ 无共享 → 干净信号 |
| 预期 lead_ratio | ≈ 37.6x（Gate 2 拒绝） | 预期 < 2.0x |
| 信号新鲜度 | T 日价格 | T-20 日价格（稍旧） |

---

## 三、注册变更清单

### 3.1 `src/factors/price_factors.py`

**新增常量**（在常量区块末尾追加）：
```python
WINDOW_CONSISTENCY_LOOKBACK = 6
WINDOW_CONSISTENCY_SKIP     = 1
MIN_CONSISTENCY_MONTHS      = 4
MONTHLY_RET_CAP             = 0.4
```

**新增函数**（按此顺序追加在 factor_high_52w 之后）：
1. `_prev_month_end_dates(rebalance_date, n)` ← 辅助函数，前缀 `_` 表示内部使用
2. `factor_high_52w_v2(rebalance_date, codes)` ← 修正版 52 周高点
3. `factor_ind_adj_mom_6_1(rebalance_date, codes)` ← 6 个月行业调整动量
4. `factor_mom_consistency_6(rebalance_date, codes)` ← 月涨幅一致性

**`_PRICE_FACTOR_BUILDERS` 字典末尾追加**：
```python
"high_52w_v2":        factor_high_52w_v2,
"ind_adj_mom_6_1":    factor_ind_adj_mom_6_1,
"mom_consistency_6":  factor_mom_consistency_6,
```

### 3.2 `scripts/build_factor_panels.py`

在 `PRICE_FACTORS` 列表的 `# 阶段 5` 区块末尾追加：
```python
"high_52w_v2", "ind_adj_mom_6_1", "mom_consistency_6",
```

**不修改** `ALL_FACTORS` 以外的内容；`run_factor_evaluation.py` 会自动扫描
`factor_panels/` 目录中的新 parquet 文件，无需在评估脚本中注册。
首次评估必须写入隔离目录，避免尚未确认的新因子直接覆盖主 `final_factors.json`。

---

## 四、单点验证（写完代码先验证，再大批量构建）

在提交代码之前，用一个固定的调仓日手工验算每个因子的输出是否合理。

### 验证日期：`T = 2019-06-28`

```python
# 在项目根目录的 Python 交互环境中运行
import pandas as pd
from src.data.loader import get_investable_universe
from src.factors.price_factors import (
    factor_ind_adj_mom_6_1, factor_mom_consistency_6, factor_high_52w_v2,
    _prev_month_end_dates,
)

T = pd.Timestamp("2019-06-28")
codes = get_investable_universe(T).tolist()

# === 验证辅助函数 ===
month_ends = _prev_month_end_dates(T, n=7)
assert len(month_ends) == 7, "应返回 7 个月末日期"
assert month_ends[-1].year == 2019 and month_ends[-1].month == 5, \
    f"最近月末应为 2019-05，实际 {month_ends[-1]}"
assert month_ends[0].year == 2018 and month_ends[0].month == 11, \
    f"最远月末应为 2018-11，实际 {month_ends[0]}"
print("辅助函数验证通过:", [str(d.date()) for d in month_ends])

# === 验证 ind_adj_mom_6_1 ===
s1 = factor_ind_adj_mom_6_1(T, codes)
assert s1.name == "ind_adj_mom_6_1"
assert s1.notna().sum() > 400, f"有效值太少：{s1.notna().sum()}"
assert abs(s1.mean()) < 0.01, f"行业均值去除后均值应接近 0，实际 {s1.mean():.4f}"
assert -1.5 < s1.min() < 0 < s1.max() < 1.5, "值域不在合理范围"
print(f"ind_adj_mom_6_1: n={s1.notna().sum()}, mean={s1.mean():.4f}, std={s1.std():.4f}")

# === 验证 mom_consistency_6 ===
s2 = factor_mom_consistency_6(T, codes)
assert s2.name == "mom_consistency_6"
assert s2.notna().sum() > 350, f"有效值太少：{s2.notna().sum()}"
assert s2.min() >= -1 and s2.max() <= 1, f"值域超出 [-1,+1]：{s2.min():.2f} ~ {s2.max():.2f}"
# 值域应主要分布在 1/6 间隔的离散值上；负侧不一定出现 -1，取决于累计收益方向
unique_vals = sorted(s2.dropna().round(2).unique())
print(f"mom_consistency_6: n={s2.notna().sum()}, unique_count={len(unique_vals)}")
print(f"  前 10 个唯一值: {unique_vals[:10]}")

# === 验证 high_52w_v2 ===
s3 = factor_high_52w_v2(T, codes)
assert s3.name == "high_52w_v2"
assert s3.notna().sum() > 400
assert (s3.dropna() <= 1.0).all(), "比率不应超过 1（分子 ≤ 分母中的最大值）"
assert (s3.dropna() > 0).all(), "比率应为正"
print(f"high_52w_v2: n={s3.notna().sum()}, mean={s3.mean():.3f}, range=[{s3.min():.3f},{s3.max():.3f}]")
```

**预期结果**：
- `ind_adj_mom_6_1` 均值接近 0（行业去均值后的正常特性），有效值 > 400
- `mom_consistency_6` 值在 [-1, +1]，分布在 1/6 间隔的离散值上；负侧极值是否出现取决于累计收益方向
- `high_52w_v2` 全部在 (0, 1]，均值约 0.7-0.85

---

## 五、实验执行步骤（按顺序执行）

```
Step 1  实现代码
        修改 src/factors/price_factors.py（新增 4 个函数 + 常量 + 注册）
        修改 scripts/build_factor_panels.py（PRICE_FACTORS 追加 3 个名称）

Step 2  单点验证
        运行 §四 中的验证脚本，全部 assert 通过后再进入 Step 3

Step 3  构建新因子面板
        python -m scripts.build_factor_panels --factors high_52w_v2 ind_adj_mom_6_1 mom_consistency_6
        （当前配置约 132 个调仓日 × 3 个因子；耗时以本机 I/O 为准）

Step 4  运行全量因子评估
        python -m scripts.run_factor_evaluation --recompute-fwd-ret --run-id momentum_sprint_s1 --output-dir reports/factor_evaluation_momentum_sprint
        （会重新评估所有因子，新因子面板会被自动扫描；结果写入隔离目录）

Step 5  检查评估结果
        查看 reports/factor_evaluation_momentum_sprint/ic_result.csv：
          重点看 3 个新因子的 ic_ir 和 significant_bh
        查看 reports/factor_evaluation_momentum_sprint/shift_result.csv：
          重点看 lead_ratio 和 lead_reject
          lead_ratio < 1.5 且 lead_reject = False → Gate 2 直接通过
          1.5 <= lead_ratio < 2.0 → 待议区，只能结合 IC Decay、验证期稳定性和经济解释人工决定

Step 6  基于结果决策（见 §六）
```

---

## 六、Gate 评估标准与决策矩阵

### 6.1 四道 Gate 标准

| Gate | 指标 | 通过标准 | 文件 |
|------|------|---------|------|
| Gate 0 | 覆盖率 | 全样本期平均 ≥ 80%（价格因子应 > 90%） | `ic_result.csv` n 列 |
| Gate 1 | IC_IR | ≥ 0.3，t_stat ≥ 2，significant_bh = True | `ic_result.csv` |
| Gate 2 | lead_ratio | < 1.5 直接通过；1.5-2.0 为待议区；≥ 2.0 或 lead_reject=True 拒绝 | `shift_result.csv` |
| Gate 3 | 验证期稳定性 | 需单独计算 2021-2022 期间 IC_IR ≥ 0.2，方向不变 | 手动计算或看 seg_ic_ir.csv |

### 6.2 动量因子的补充注意事项

- **Gate 2 是重中之重**：动量因子最容易在这里暴露数据问题。lead_ratio > 2.0 等同于宣告实现有问题，无论 IC 多高都不可信
- **分期检验**：动量因子在牛市（2014-15, 2019, 2020）IC 通常为正，熊市（2016, 2018, 2022）为负，属正常现象；验证期 2021-2022 是反动量环境，Gate 3 标准宽松（≥ 0.15 且方向不变即可）
- **与现有因子相关性**：若通过 Gate 1-3，还需检查 `factor_correlation.csv`：
  - `|r(new, analyst_eps_revision)| < 0.5`（与基本面动量不高度重叠）
  - `|r(ind_adj_mom_6_1, mom_consistency_6)| < 0.5`（若均通过，相关性低才同时入池）

### 6.3 决策矩阵

```
ind_adj_mom_6_1 Gate 1+2 通过？
  ├── 是 → 进 Gate 3 + 相关性检查 → 通过则入 final_factors.json
  └── 否 → 记录原因；若 Gate 2 失败（lead_reject=True）→ 说明 mom_6_1 底层
            虽然底层 mom_6_1 较干净，但经行业调整后产生了新的 lead 问题；
            接受动量维度只有 analyst_eps_revision 的现状

mom_consistency_6 Gate 1+2 通过？
  ├── 是 → 进 Gate 3 + 与 ind_adj_mom_6_1 相关性（若后者也通过）
  └── 否 → 舍弃

high_52w_v2 Gate 1+2 通过？
  ├── 是 → 进 Gate 3（lead_ratio 应显著低于原版的 37.6x）
  └── Gate 2 仍失败 → 说明 52 周高点设计本身在月频 A 股不适用；放弃此方向

全部未通过 → 不更新 final_factors.json，不重跑 pipeline，IR=0.483 保持不变
```

---

## 七、Sprint 2：更新 pipeline（若有因子通过 Gate）

若有任何因子通过 Gate 1-3，执行以下步骤：

```
Step A  更新 final_factors.json
        先用隔离目录结果确认通过因子；若决定进入主线，再运行：
        python -m scripts.run_factor_evaluation --recompute-fwd-ret --run-id momentum_sprint_s1_main
        由脚本重写主 reports/factor_evaluation/final_factors.json 及 metadata。
        若需要人工排除某个“待议区”因子，必须在提交说明中记录理由。

Step B  重跑信号合成
        python -m scripts.run_signal_combination
        （Ridge v2 会自动利用新加入的因子面板）

Step C  重跑组合优化
        python -m scripts.run_portfolio_optimization

Step D  重跑回测
        python -m scripts.run_backtest

Step E  检查是否改善 IR
        对比 experiments/v1.1/ 的 IR=0.483
        若新版本 IR 更低 → 新因子无益，不归档为 v1.2；按 git diff 精确恢复本次改动，不使用粗粒度回滚覆盖无关修改
        若新版本 IR 更高 → 归档为 experiments/v1.2/
```

---

## 八、Sprint 3：残差动量 `residual_mom_6_2`（待评估后决定）

**前提条件**：Sprint 1 中至少有 1 个因子通过 Gate，且判断动量维度仍有提升空间。

**工程量**：Sprint 1 的 3 倍，需要预建月度参考因子收益序列。

**概要设计**：

```
数据准备：
  - monthly_ret_panel: 每只股票的月度收益（来自 daily_quote，取月末收盘价计算）
  - market_ret: 中证500月度收益（来自 index_quote）
  - smb_ret: 小市值 minus 大市值等权月度收益（来自 daily_basic，按月末市值分组）
  - hml_ret: 低EP minus 高EP等权月度收益（来自 indicator_pit）

每个调仓日 T：
  for 每月 t ∈ [T-6m, T-2m]（共 5 个月，跳过最近 2 个月）：
    横截面 OLS: r_i,t ~ alpha + beta1*market_ret + beta2*smb_ret + beta3*hml_ret
    提取残差 eps_i,t
  residual_mom_i,T = sum(eps_i,t for t in [T-6m, T-2m])  # 5 个月残差累加

关键风险：
  - 因子载荷每月滚动估计（不用全样本固定载荷），否则未来函数
  - Gate 2（lead_ratio）是最重要检验：残差应比原始收益更干净
  - 残差截尾 [-0.4, +0.4]（2015 年股灾月残差极端）
```

**本计划不实现 Sprint 3**，等 Sprint 1 评估结果出来后再决定是否需要。

---

## 九、常见错误预防清单

在提交代码前，逐项确认：

### A. 数据对齐
- [ ] `ind_adj_mom_6_1` 底层用 `factor_mom_6_1`，**不是** `factor_mom_12_1`
- [ ] `factor_mom_6_1` 窗口：[T-120td, T-20td]，跳过最近 20 个交易日
- [ ] `mom_consistency_6` 的 7 个月末价格都是真实交易日（来自 `_prev_month_end_dates`）
- [ ] `high_52w_v2` 分子使用 `window_end`（T-20）的 `close_adj`，**不是** T 日的

### B. 未来函数防范
- [ ] `ind_adj_mom_6_1`：最新用到 T-20 日数据，无 T 日或更新数据
- [ ] `mom_consistency_6`：最新用到 T-1m 月末价格，无 T 日或更新数据
- [ ] `high_52w_v2`：最新用到 T-20 日数据，无 T 日或更新数据
- [ ] `_prev_month_end_dates` 返回的所有日期都 < T

### C. 覆盖率与 NaN 处理
- [ ] `mom_consistency_6` 中月末价格缺失（停牌股）不会 crash，自动变 NaN 月度收益
- [ ] `mom_consistency_6` 不使用 `pct_change()` 默认前向填充；缺失价格必须保持 NaN
- [ ] `ind_adj_mom_6_1` 中行业 NaN 的股票不影响其他行业的均值计算
- [ ] 历史不足（T 在训练集早期）时所有函数返回全 NaN Series，不抛异常

### D. 注册完整性
- [ ] `_PRICE_FACTOR_BUILDERS` 中已注册新因子名
- [ ] `build_factor_panels.py::PRICE_FACTORS` 已追加新因子名
- [ ] 因子名与 `_PRICE_FACTOR_BUILDERS` 中的 key 拼写完全一致

### E. 单点验证
- [ ] §四 中的所有 `assert` 全部通过
- [ ] `mom_consistency_6` 唯一值约 13 个（0/6 到 6/6 共 7 种 × 方向 ±1，minus 0 重叠）

---

*本计划相关文件路径*
- 实现：`src/factors/price_factors.py`
- 注册：`scripts/build_factor_panels.py`
- 评估输入：`data/processed/factor_panels/`
- 评估输出：`reports/factor_evaluation/`
- 参考：`factor_research_guide.md §8`（构建规格）
