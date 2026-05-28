# 回测框架详细设计文档

> 本文档是 `src/backtest/` 模块与 `notebooks/05_backtest.ipynb` 的实现蓝图。
> 阅读顺序：整体架构 → 各文件详细设计 → 每步最易犯错误。
> 实现时严格按照本文档，每完成一个模块对照"易错点"自检。

---

## 一、整体架构

### 1.1 数据流

```
nb04 输出
  portfolio_weights_optimized.parquet   (月末 T × stocks，目标权重)
  portfolio_weights_baseline.parquet    (TopN 等权对照)
        ↓
engine.py  BacktestEngine.run()
  ├─ 预加载  open_adj_panel / close_adj_panel  ← daily_quote.parquet
  ├─ 预加载  exec_status_dict[T+1]             ← stock_status.parquet
  ├─ 预加载  benchmark_nav                     ← index_quote.parquet
  │
  ├─ 逐日循环
  │    ├─ 调仓日 T+1：_execute_rebalance()
  │    │      └─ transaction.compute_trade_cost()
  │    └─ 每日收盘：更新 portfolio_value = Σ(shares × close_adj)
  │
  └─ 输出  BacktestResult
        ├─ nav / benchmark_nav / excess_nav
        ├─ actual_weights（每日实际权重）
        ├─ trade_log（每次调仓记录）
        └─ metrics ← metrics.summarize()

05_backtest.ipynb
  ├─ V0  基准
  ├─ V1  Baseline TopN
  ├─ V2  QP 优化
  └─ 可视化 + 落盘
```

### 1.2 文件清单

```
src/backtest/
├── __init__.py          空文件（3 行）
├── transaction.py       交易成本（~60 行）
├── metrics.py           绩效指标（~120 行）
└── engine.py            回测主循环（~280 行）

notebooks/
└── 05_backtest.ipynb    展示层（~10 cells）
```

### 1.3 依赖关系（严格单向）

```
engine.py
  ├── 依赖  transaction.py
  ├── 依赖  metrics.py
  ├── 依赖  src.data.loader  (load_daily_quote, load_stock_status)
  ├── 依赖  src.data.universe (TradeState, _all_trading_dates)
  └── 依赖  src.config

transaction.py / metrics.py
  └── 依赖  src.config（读常量，不导入其他 src 模块）
```

---

## 二、`transaction.py` 详细设计

### 2.1 函数清单

```python
stamp_duty_rate(trade_date: pd.Timestamp) -> float
compute_trade_cost(
    sell_value: float,
    buy_value:  float,
    trade_date: pd.Timestamp,
) -> float
```

### 2.2 `stamp_duty_rate`

**逻辑：**

```python
if trade_date >= cfg.STAMP_DUTY_CUT_DATE:   # 2023-08-28（含）
    return cfg.STAMP_DUTY_AFTER             # 0.0005（5 bps）
else:
    return cfg.STAMP_DUTY_BEFORE            # 0.001（10 bps）
```

**前提条件：**
- `trade_date` 是实际执行交易的日期（T+1 开盘日），不是调仓日 T

**返回值：** 小数形式（0.001 而非 1.0），直接乘以金额

### 2.3 `compute_trade_cost`

**逻辑：**

```python
stamp_duty = sell_value * stamp_duty_rate(trade_date)     # 卖出单边
commission  = (sell_value + buy_value) * cfg.COMMISSION_RATE   # 双边各 2.5 bps
slippage    = (sell_value + buy_value) * cfg.SLIPPAGE_RATE     # 双边各 8 bps
return stamp_duty + commission + slippage
```

**参数约定：**
- `sell_value`：本次调仓卖出的总金额（已按执行价格计算，非目标减少量）
- `buy_value`：本次调仓买入的总金额
- 两者均为非负数，函数内不取绝对值（调用方保证）

**返回值：** 总成本（货币单位，与 portfolio_value 同单位），engine 负责从净值中扣除

### 2.4 单元测试要点

| 测试用例 | 预期 |
|---|---|
| `stamp_duty_rate(Timestamp("2023-08-27"))` | 0.001 |
| `stamp_duty_rate(Timestamp("2023-08-28"))` | 0.0005 |
| `stamp_duty_rate(Timestamp("2023-08-29"))` | 0.0005 |
| `compute_trade_cost(0, 0, any_date)` | 0.0 |
| `compute_trade_cost(100, 100, "2023-08-27")` | 验算：10×0.001 + 200×0.00025 + 200×0.0008 = 0.1 + 0.05 + 0.16 = 0.31 |

### 2.5 易错点

**E1（高概率）：印花税切换日用 `>` 而非 `>=`**
```python
# 错误：2023-08-28 当天仍按旧税率
if trade_date > cfg.STAMP_DUTY_CUT_DATE:

# 正确：切换日当天起适用新税率
if trade_date >= cfg.STAMP_DUTY_CUT_DATE:
```

**E2（中概率）：印花税双边计算**
```python
# 错误：买卖双边都收
stamp_duty = (sell_value + buy_value) * rate

# 正确：仅卖出单边
stamp_duty = sell_value * rate
```

**E3（低概率）：sell_value / buy_value 含负数**
- 调用方传入差值而非绝对金额，导致印花税为负
- 防御：函数开头 `assert sell_value >= 0 and buy_value >= 0`

---

## 三、`metrics.py` 详细设计

### 3.1 函数清单

```python
daily_returns(nav: pd.Series) -> pd.Series
annualized_return(nav: pd.Series) -> float
annualized_vol(nav: pd.Series) -> float
sharpe(nav: pd.Series, rf_annual: float = 0.02) -> float
max_drawdown(nav: pd.Series) -> float
tracking_error(nav: pd.Series, bench_nav: pd.Series) -> float
information_ratio(nav: pd.Series, bench_nav: pd.Series) -> float
excess_nav(nav: pd.Series, bench_nav: pd.Series) -> pd.Series
excess_max_drawdown(nav: pd.Series, bench_nav: pd.Series) -> float
monthly_win_rate(nav: pd.Series, bench_nav: pd.Series) -> float
calmar_ratio(nav: pd.Series) -> float
summarize(nav: pd.Series, bench_nav: pd.Series, rf_annual: float = 0.02) -> dict
```

### 3.2 各函数实现细节

#### `daily_returns`
```python
# 日收益率 = NAV 一阶差分，第一个值为 NaN（正常，下游 dropna）
return nav.pct_change()
```

#### `annualized_return`
```python
# 从复利公式推导，不用几何均值近似
n_years = len(nav) / 252.0
return (nav.iloc[-1] / nav.iloc[0]) ** (1 / n_years) - 1
```

#### `annualized_vol`
```python
# 日收益率标准差 × √252（日历年惯例）
return daily_returns(nav).std() * np.sqrt(252)
```

#### `sharpe`
```python
# 日无风险利率 = rf_annual / 252，超额日收益的年化
excess_daily = daily_returns(nav) - rf_annual / 252
return excess_daily.mean() / excess_daily.std() * np.sqrt(252)
```

#### `max_drawdown`
```python
# 滚动最大净值 → 回撤 = (NAV - 滚动最大) / 滚动最大
rolling_max = nav.cummax()
drawdown = (nav - rolling_max) / rolling_max
return float(drawdown.min())   # 负数，如 -0.15 表示最大回撤 15%
```

#### `tracking_error`
```python
# 超额日收益的年化标准差（事后实测 TE）
excess = daily_returns(nav) - daily_returns(bench_nav)
return excess.std() * np.sqrt(252)
```

#### `information_ratio`
```python
# 年化超额收益 / 跟踪误差
excess_ann = annualized_return(nav) - annualized_return(bench_nav)
te = tracking_error(nav, bench_nav)
return excess_ann / te if te > 1e-10 else 0.0
```

#### `excess_nav`
```python
# 超额净值曲线（两者均从 1.0 出发）
nav_aligned, bench_aligned = nav.align(bench_nav, join='inner')
return nav_aligned / bench_aligned
```

#### `monthly_win_rate`
```python
# 月末对齐，按月计算超额收益，统计正超额的比例
monthly_nav   = nav.resample('ME').last()
monthly_bench = bench_nav.resample('ME').last()
monthly_ret   = monthly_nav.pct_change().dropna()
monthly_bench_ret = monthly_bench.pct_change().dropna()
excess = monthly_ret - monthly_bench_ret
return float((excess > 0).mean())
```

#### `summarize`
```python
# 返回 dict，key 为指标名（英文），value 为 float
# 所有指标一次计算，下游可直接转 pd.Series 或 DataFrame
```

### 3.3 易错点

**E4（高概率）：annualized_return 用算术均值而非几何均值**
```python
# 错误：算术均值（低估长期复利收益）
return daily_returns(nav).mean() * 252

# 正确：从累计净值反推
n_years = len(nav) / 252
return (nav.iloc[-1] / nav.iloc[0]) ** (1 / n_years) - 1
```

**E5（高概率）：max_drawdown 返回正数还是负数**
- 统一规范：`max_drawdown` 返回**负数**（-0.15 = 最大回撤 15%）
- `summarize` 中字段名 `max_drawdown`，值为负；展示时取绝对值

**E6（中概率）：tracking_error 用超额收益算而非用各自收益差**
```python
# 错误：直接用两条 NAV 曲线的收益率标准差之差
te = nav.std() - bench.std()

# 正确：超额日收益的标准差
excess = daily_returns(nav) - daily_returns(bench_nav)
te = excess.std() * np.sqrt(252)
```

**E7（中概率）：monthly_win_rate 的对齐问题**
- nav 和 bench_nav 日期范围可能不完全一致（基准可能多几天）
- 必须先 `align(join='inner')` 再计算，防止 NaN 污染

**E8（低概率）：`excess_nav` 的起点不一致**
- 如果 nav 从 1.0 出发但 bench_nav 从实际点位出发，相除无意义
- 规范：engine 输出的 nav 和 bench_nav 均归一化到起点 = 1.0

---

## 四、`engine.py` 详细设计

### 4.1 数据结构

```python
@dataclass
class BacktestConfig:
    initial_value:    float = 1.0       # 组合起始净值
    benchmark_col:    str   = "close"   # index_quote 中的基准价格列

@dataclass
class TradeRecord:
    exec_date:     pd.Timestamp   # 实际执行日（T+1）
    rebalance_date: pd.Timestamp  # 对应的调仓日（T）
    sell_value:    float           # 本次卖出金额
    buy_value:     float           # 本次买入金额
    cost:          float           # 总交易成本
    portfolio_value_before: float  # 执行前净值
    portfolio_value_after:  float  # 执行后净值
    n_locked:      int             # 停牌无法成交的股数
    n_no_buy:      int             # 涨停无法买入的股数
    n_no_sell:     int             # 跌停无法卖出的股数
    n_no_price:    int             # 价格缺失无法成交的股数

@dataclass
class BacktestResult:
    nav:             pd.Series      # 日频 NAV（起点=1.0，index=trade_date）
    benchmark_nav:   pd.Series      # 基准 NAV（起点=1.0，同索引）
    excess_nav:      pd.Series      # nav / benchmark_nav
    actual_weights:  pd.DataFrame   # 每日实际权重（date × ts_code）
    trade_log:       pd.DataFrame   # TradeRecord 列表转成的 DataFrame
    metrics:         dict           # summarize() 输出
```

### 4.2 主函数 `run_backtest`

```python
def run_backtest(
    weights_panel:  pd.DataFrame,    # (rebalance_date × ts_code) 目标权重，行和=1
    backtest_start: pd.Timestamp,    # 回测开始日（含，须为交易日）
    backtest_end:   pd.Timestamp,    # 回测结束日（含，须为交易日）
    config:         BacktestConfig = BacktestConfig(),
) -> BacktestResult
```

**内部分 5 个阶段（均有独立函数，engine 主函数只做编排）：**

```
阶段 0：_build_execution_map       调仓日 T → T+1 实际交易日
阶段 1：_load_price_panels         预加载价格面板
阶段 2：_load_exec_status          预加载所有 T+1 状态
阶段 3：_run_main_loop             主循环（按日迭代）
阶段 4：_build_result              汇总结果
```

### 4.3 阶段 0：`_build_execution_map`

```python
def _build_execution_map(
    rebalance_dates: list[pd.Timestamp],
    backtest_start:  pd.Timestamp,
    backtest_end:    pd.Timestamp,
    all_trading_dates: list[pd.Timestamp],
) -> dict[pd.Timestamp, pd.Timestamp]:
    """
    返回 {T: T+1} 字典，T+1 是 T 之后的第一个实际交易日。
    过滤掉 T+1 > backtest_end 的调仓日（末期无法执行）。
    """
    date_set = set(all_trading_dates)
    result = {}
    for T in rebalance_dates:
        if T < backtest_start or T > backtest_end:
            continue
        # 找 T 之后的第一个交易日
        T1 = _next_trading_day(T, all_trading_dates)
        if T1 is None or T1 > backtest_end:
            continue   # 最后一期无执行日，跳过
        result[T] = T1
    return result
```

**辅助：`_next_trading_day`**

```python
def _next_trading_day(
    date: pd.Timestamp,
    all_dates: list[pd.Timestamp],   # 已排序
) -> pd.Timestamp | None:
    # 二分查找 date 在列表中的位置，取下一个元素
    import bisect
    idx = bisect.bisect_right(all_dates, date)
    return all_dates[idx] if idx < len(all_dates) else None
```

### 4.4 阶段 1：`_load_price_panels`

```python
def _load_price_panels(
    all_codes:      list[str],
    backtest_start: pd.Timestamp,
    backtest_end:   pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    返回 (open_adj_panel, close_adj_panel)，shape=(trading_days × stocks)。
    停牌 NaN → close_adj 用 ffill 填充，open_adj 保持 NaN（执行时识别为无法成交）。
    """
    dq = load_daily_quote(backtest_start, backtest_end, codes=all_codes)

    open_adj_panel  = dq["open_adj"].unstack("ts_code").reindex(columns=all_codes)
    close_adj_panel = dq["close_adj"].unstack("ts_code").reindex(columns=all_codes)

    # close_adj 停牌 NaN → ffill（保持估值连续，不影响真实 PnL）
    close_adj_panel = close_adj_panel.ffill()

    # open_adj 不 ffill（停牌开盘价 NaN = 无法成交，engine 主循环据此识别）
    return open_adj_panel, close_adj_panel
```

### 4.5 阶段 2：`_load_exec_status`

```python
def _load_exec_status(
    exec_dates: list[pd.Timestamp],   # 所有 T+1 执行日
    all_codes:  list[str],
) -> dict[pd.Timestamp, pd.Series]:  # {T+1: Series(ts_code → TradeState)}
    """
    预加载每个执行日的股票状态，避免主循环内重复 I/O。
    load_stock_status 返回当日所有股票状态，包含 is_suspended / is_limit_up_locked 等。
    此处转换为 TradeState 枚举（与 universe.get_constraint_states 逻辑一致）。
    """
```

**注意：** `load_stock_status` 已存在且可接受任意交易日，无需额外封装。

### 4.6 阶段 3：主循环 `_run_main_loop`

这是最核心的部分，逐行描述每一步。

```python
# ── 初始化 ────────────────────────────────────────────────
shares: dict[str, float] = {}       # ts_code → 持仓股数（浮点数）
portfolio_value = config.initial_value
nav_dict:    dict[pd.Timestamp, float] = {}
weight_rows: list[dict] = []
trade_records: list[TradeRecord] = []

# ── 主循环（按交易日遍历） ──────────────────────────────────
exec_date_to_T = {v: k for k, v in execution_map.items()}   # 反查：T+1 → T

for date in all_trading_dates_in_range:

    # ── 调仓执行（仅在 T+1 日触发） ─────────────────────────
    if date in exec_date_to_T:
        T = exec_date_to_T[date]
        target_w   = weights_panel.loc[T]           # Series: ts_code → weight
        open_prices = open_adj_panel.loc[date]       # Series: ts_code → open_adj
        status      = exec_status[date]              # Series: ts_code → TradeState

        shares, record = _execute_rebalance(
            shares, target_w, open_prices, status,
            portfolio_value, date, T,
        )
        portfolio_value -= record.cost
        trade_records.append(record)

    # ── 每日收盘估值 ─────────────────────────────────────────
    close_prices = close_adj_panel.loc[date]        # 停牌已 ffill，无 NaN
    portfolio_value = sum(
        shares.get(code, 0.0) * close_prices.get(code, 0.0)
        for code in shares
    )
    nav_dict[date] = portfolio_value / config.initial_value

    # ── 记录实际权重（可选，内存较大时可降采样到月频） ─────────
    if portfolio_value > 1e-10:
        w_actual = {
            code: shares[code] * close_prices.get(code, 0.0) / portfolio_value
            for code in shares if shares[code] > 1e-10
        }
        weight_rows.append({"date": date, **w_actual})
```

### 4.7 `_execute_rebalance` 详细逻辑

```python
def _execute_rebalance(
    shares:          dict[str, float],    # 当前持仓股数
    target_w:        pd.Series,           # 目标权重（和=1）
    open_prices:     pd.Series,           # T+1 开盘价（open_adj）
    status:          pd.Series,           # T+1 状态（TradeState）
    portfolio_value: float,               # 执行前净值（按 T 日收盘价估算）
    exec_date:       pd.Timestamp,
    rebalance_date:  pd.Timestamp,
) -> tuple[dict[str, float], TradeRecord]:

    new_shares = dict(shares)   # 复制，不改原始
    sell_value = 0.0
    buy_value  = 0.0
    n_locked = n_no_buy = n_no_sell = n_no_price = 0

    # 合并当前持仓和目标持仓的全量 codes
    all_codes = set(shares.keys()) | set(target_w.index)

    for code in all_codes:
        target_weight = float(target_w.get(code, 0.0))
        current_shares = shares.get(code, 0.0)
        price = open_prices.get(code)
        state = status.get(code, TradeState.FREE)

        # ── 价格缺失（停牌未开盘）────────────────────────────
        if pd.isna(price) or price <= 0:
            n_no_price += 1
            continue   # 保留原有股数

        current_value = current_shares * price
        target_value  = target_weight * portfolio_value

        # ── 按状态决定执行方向 ──────────────────────────────
        if state == TradeState.LOCKED:
            # 完全锁定，不能买也不能卖
            n_locked += 1
            continue

        elif state == TradeState.NO_BUY:
            # 只能减仓，不能加仓
            if target_value < current_value:
                actual_value = target_value   # 减仓到目标
            else:
                n_no_buy += 1
                continue   # 想买但买不了，保持原有
            actual_value = max(actual_value, 0.0)

        elif state == TradeState.NO_SELL:
            # 只能加仓，不能减仓
            if target_value > current_value:
                actual_value = target_value   # 加仓到目标
            else:
                n_no_sell += 1
                continue   # 想卖但卖不了，保持原有
        
        else:
            # FREE 或 BUY_LIMIT（BUY_LIMIT 在优化器层已处理，此处同 FREE）
            actual_value = target_value

        # ── 计算成交金额 ──────────────────────────────────────
        trade_value = actual_value - current_value
        if trade_value > 1e-6:           # 买入
            buy_value += trade_value
        elif trade_value < -1e-6:        # 卖出
            sell_value += abs(trade_value)

        new_shares[code] = actual_value / price   # 更新股数

    cost = compute_trade_cost(sell_value, buy_value, exec_date)

    record = TradeRecord(
        exec_date=exec_date,
        rebalance_date=rebalance_date,
        sell_value=sell_value,
        buy_value=buy_value,
        cost=cost,
        portfolio_value_before=portfolio_value,
        portfolio_value_after=portfolio_value - cost,
        n_locked=n_locked,
        n_no_buy=n_no_buy,
        n_no_sell=n_no_sell,
        n_no_price=n_no_price,
    )
    return new_shares, record
```

### 4.8 阶段 4：`_build_result`

```python
nav_series = pd.Series(nav_dict)

# 基准：从 index_quote 读取，归一化到 backtest_start = 1.0
bench_raw = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")["close"]
bench_raw = bench_raw[
    (bench_raw.index >= backtest_start) & (bench_raw.index <= backtest_end)
]
benchmark_nav = bench_raw / bench_raw.iloc[0]

# 对齐（内连接）
nav_aligned, bench_aligned = nav_series.align(benchmark_nav, join="inner")

return BacktestResult(
    nav           = nav_aligned,
    benchmark_nav = bench_aligned,
    excess_nav    = nav_aligned / bench_aligned,
    actual_weights = pd.DataFrame(weight_rows).set_index("date").fillna(0.0),
    trade_log      = pd.DataFrame([vars(r) for r in trade_records]),
    metrics        = summarize(nav_aligned, bench_aligned),
)
```

### 4.9 engine.py 易错点

**E9（P0，最高概率）：用 close 价格代替 open 价格执行 T+1 交易**
```python
# 错误：当日收盘已知，产生前视偏差
price = close_adj_panel.loc[date, code]

# 正确：T+1 开盘价（交易发生时尚未确定收盘价）
price = open_adj_panel.loc[date, code]
```

**E10（P0）：close_adj ffill 污染 open_adj**
```python
# 错误：两个 panel 都 ffill
close_adj_panel = dq["close_adj"].unstack().ffill()
open_adj_panel  = dq["open_adj"].unstack().ffill()   # ← 错误！

# 正确：只 ffill close_adj，open_adj 保持 NaN（NaN = 停牌无法成交）
close_adj_panel = dq["close_adj"].unstack().ffill()
open_adj_panel  = dq["open_adj"].unstack()           # 不 ffill
```

**E11（P1）：第一期 portfolio_value 用 initial_value 计算 target_shares，但 shares 为空**
- 第一期 `current_value = 0`，`target_value = target_w * initial_value`
- 全部为买入，`sell_value = 0`，`buy_value = sum(target_w) * initial_value`
- 印花税 = 0（无卖出），成本 = 买入成本，这是正确的
- 注意：第一期换手率 = 100%（从空组合到满仓），应在报告中单独标注

**E12（P1）：weights_panel 权重和不严格等于 1，导致 portfolio_value 持续漂移**
```python
# 防御：每期目标权重归一化
target_w = weights_panel.loc[T]
w_sum = target_w.sum()
if abs(w_sum - 1.0) > 1e-4:
    log.warning("T=%s 权重和 %.6f ≠ 1，强制归一化", T.date(), w_sum)
    target_w = target_w / w_sum
```

**E13（P1）：基准 NAV 与策略 NAV 日期不对齐**
- `index_quote.parquet` 可能有策略期间缺失的日期（如某些节假日记录不一致）
- 必须用 `align(join="inner")` 而非直接除法，否则 NaN 传播

**E14（P1）：`portfolio_value` 在执行成本后未更新，导致当日收盘估值偏高**
```python
# 错误顺序：先估值，再扣成本（成本丢失）
portfolio_value = sum(shares * close_prices)
portfolio_value -= cost   # ← 这行没用，estimate 已经完成

# 正确顺序：
shares, record = _execute_rebalance(...)
portfolio_value -= record.cost          # 先扣成本
# 然后用 close_price 重算（成本已反映为现金减少）
portfolio_value = sum(shares * close_prices)
```

**E15（P2）：`_execute_rebalance` 直接修改传入的 `shares` 字典**
```python
# 错误：修改原始引用
shares[code] = new_value

# 正确：先复制
new_shares = dict(shares)
new_shares[code] = new_value
return new_shares, record   # 返回新字典，原字典不变
```

**E16（P2）：`_next_trading_day` 用线性搜索代替二分查找**
- 调用次数 ≈ 120 期（月频 10 年），差异不大，但若改为日频会有问题
- 推荐用 `bisect.bisect_right`，O(log n)

**E17（P2）：实际权重按月末计算但 close_adj 用错了日期**
- actual_weights 每日记录，date 应为 `close_adj_panel` 的索引日期
- 不要把调仓日 T 和执行日 T+1 混用

**E18（P3）：`index_quote.parquet` 的列名不确定**
- 可能是 `close` 也可能是 `close_adj` 或其他名字
- 加载时先检查 `.columns`，不要硬编码列名

---

## 五、`05_backtest.ipynb` 设计

### 5.1 Cell 结构

```
Cell 1  USE_MOCK_DATA 开关 + imports + 路径常量
Cell 2  Mock 数据生成 / Real 数据加载（读取 nb04 输出）
Cell 3  run_backtest → result_v1（Baseline TopN）
        run_backtest → result_v2（QP 优化）
        V0 基准从 BacktestResult 中提取
Cell 4  自检验证
Cell 5  可视化（5 张图）
Cell 6  指标汇总表（DataFrame 格式）
Cell 7  落盘
```

### 5.2 可视化（5 张图）

```
图 1：净值曲线对比      V0 / V1 / V2 三线
图 2：超额净值曲线      V1 超额 / V2 超额
图 3：月度超额收益柱状  V2 vs V0，红绿分色
图 4：月度换手率        V1 / V2 对比
图 5：关键指标汇总表    年化收益 / 波动 / Sharpe / IR / 最大回撤 / TE / 月胜率
```

### 5.3 落盘

```
backtest_nav.parquet    columns: [strategy_v1, strategy_v2, benchmark]，index=trade_date
backtest_metrics.parquet  columns: [v1, v2]，index=指标名
```

### 5.4 易错点

**E19（P0）：Mock 模式下不加载真实数据，但基准 index_quote 没有 mock 替代**
- Mock 模式需要同时 mock 价格面板和基准 NAV
- 不能一半 mock 一半读真实文件

**E20（P1）：V0 / V1 / V2 的起点日期不一致导致对比失效**
- 三条曲线必须从同一天出发，NAV 起点均归一化为 1.0
- 起点取三者共有的第一个日期（inner align）

**E21（P1）：指标汇总表中 max_drawdown 展示为负数**
- 定义统一：`max_drawdown` 返回负数（-0.15），展示时取绝对值并格式化为百分比
- 在 `summarize` 函数中统一处理，不在 notebook 里临时取反

---

## 六、测试计划

### 6.1 `tests/test_backtest.py` 必须覆盖的用例

```python
# transaction.py
test_stamp_duty_before_cutoff()       # 2023-08-27 → 10 bps
test_stamp_duty_on_cutoff()           # 2023-08-28 → 5 bps（切换日当天）
test_stamp_duty_after_cutoff()        # 2023-08-29 → 5 bps
test_trade_cost_zero_trade()          # sell=0 buy=0 → cost=0
test_trade_cost_sell_only()           # 只卖，无买

# metrics.py
test_annualized_return_flat_nav()     # 全程 1.0 → 0%
test_max_drawdown_monotone_up()       # 只涨 → drawdown = 0
test_sharpe_positive()                # 稳定正收益 → Sharpe > 0
test_tracking_error_identical()       # nav = bench → TE = 0

# engine.py
test_engine_mock_data_runs()          # mock 数据跑通，不报错
test_nav_starts_at_one()              # nav.iloc[0] == 1.0
test_locked_stock_holds()             # 停牌股保留原有股数
test_cost_deducted_from_nav()         # 调仓后 NAV 比无成本版本低
test_first_period_no_prev_shares()    # 第一期从空组合买入，NAV 正常
test_all_locked_no_change()           # 全部停牌 → 持仓不变，成本=0
```

---

## 七、实现顺序与验收标准

| 步骤 | 内容 | 验收标准 |
|---|---|---|
| 1 | `transaction.py` + 单元测试 | 6 个测试全通过 |
| 2 | `metrics.py` + 单元测试 | 6 个测试全通过 |
| 3 | `engine.py` mock 模式 | `test_engine_mock_data_runs` 通过 |
| 4 | `engine.py` 全量测试 | 所有 `test_backtest.py` 通过 |
| 5 | `05_backtest.ipynb` mock 模式 | 所有 self-check cell 输出 PASS |
| 6 | `05_backtest.ipynb` real 模式 | 指标合理（非 NaN，无负 TE，IR 在 -2~3 范围） |

---

## 八、自检报告模板（notebook 完成后填写）

### A. 高频犯错点

| 检查项 | 状态 |
|---|---|
| T+1 执行用 open_adj，不用 close_adj | ☐ |
| open_adj 停牌 NaN = 不成交，不 ffill | ☐ |
| close_adj 停牌 NaN → ffill（估值连续）| ☐ |
| 印花税切换日 2023-08-28 当天适用 5 bps | ☐ |
| 印花税仅卖出单边，非双边 | ☐ |
| 基准用全收益指数（index_quote），不用价格指数 | ☐ |
| nav 与 benchmark_nav 起点均为 1.0 | ☐ |
| 第一期换手率 ~100% 属正常，已在报告注明 | ☐ |
| max_drawdown 负数，展示时取绝对值 | ☐ |
| 测试集未运行（OPT_END = VALID_END = 2022-12-31）| ☐ |

### B. 工程质量

| 检查项 | 状态 |
|---|---|
| `_execute_rebalance` 返回新字典，不修改原字典 | ☐ |
| 所有易错点（E9-E21）均已验证无问题 | ☐ |
| mock 模式不读取 / 覆盖真实数据文件 | ☐ |
| `tests/test_backtest.py` 全部通过 | ☐ |

### C. 待优化事项（本次不处理）

- **w_prev 精度问题**：nb04 传给优化器的 w_prev 是目标权重而非实际持仓（因价格漂移），导致约束计算有偏差。影响：换手率和 TE 约束在验证集轻微失准。处理建议：在 06_attribution 中单独讨论。
- **一字板的精确处理**：当前对涨跌停的处理是"当天不调"，真实场景下若持续多天一字板，应在后续交易日补充执行。本项目按月频，影响较小，注明即可。
- **退市股处理**：当前假设持仓股不退市。如有退市，close_adj ffill 会保留最后价格，实际上应记为归零。
