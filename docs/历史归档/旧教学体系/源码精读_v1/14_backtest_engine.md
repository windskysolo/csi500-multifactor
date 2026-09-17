# 14 — 回测主循环：engine.py 逐行精讲

> 对应源文件：`src/backtest/engine.py`（628 行）  
> 前置依赖：`13_optimizer.md`（权重输入），`15_transaction.md`（成本计算），`04_universe.md`（TradeState）  
> 核心任务：按日模拟"收到权重 → T+1 开盘执行 → 每日估值 → 输出 NAV"

---

## 一、时间轴设计

```
月末 T：优化器生成目标权重（基于 ≤T 日信息）
T+1 开盘：按开盘价执行交易（含涨跌停 / 停牌约束）
T+1 ~ T'+1：每日按收盘价估值（close_adj，ffill 填充）
T_next（下个月末）：重复上述过程
```

关键设计：T 日信号，T+1 才执行。不用 T 日收盘价执行，避免"知道今日收盘价才下单"的未来函数。

---

## 二、数据结构

### 2.1 `TradeRecord`（第 54-66 行）

```python
@dataclass
class TradeRecord:
    exec_date:              pd.Timestamp   # T+1（实际执行日）
    rebalance_date:         pd.Timestamp   # T（信号生成日）
    sell_value:             float          # 本次卖出总金额
    buy_value:              float          # 本次买入总金额
    cost:                   float          # 三类交易成本之和
    portfolio_value_before: float          # 执行前净值
    portfolio_value_after:  float          # 执行后净值（= before - cost）
    n_locked:               int            # 停牌锁定数
    n_no_buy:               int            # 涨停无法加仓数
    n_no_sell:              int            # 跌停无法减仓数
    n_no_price:             int            # 价格缺失数（停牌开盘）
```

`n_no_price` 是开盘价为 NaN（停牌未开盘）的股票数，与 `n_locked`（优化器层已锁定）不同。

### 2.2 `BacktestResult`（第 68-75 行）

```python
@dataclass
class BacktestResult:
    nav:             pd.Series      # 日频策略净值
    benchmark_nav:   pd.Series      # 基准净值（全收益指数）
    excess_nav:      pd.Series      # nav / benchmark_nav
    actual_weights:  pd.DataFrame   # 每日实际持仓权重
    trade_log:       pd.DataFrame   # 每次调仓记录
    metrics:         dict           # summarize() 输出的指标
```

---

## 三、辅助函数

### 3.1 `_next_trading_day`（第 82-96 行）

```python
def _next_trading_day(date, all_dates) -> Optional[pd.Timestamp]:
    idx = bisect.bisect_right(all_dates, date)
    return all_dates[idx] if idx < len(all_dates) else None
```

`bisect.bisect_right`：二分查找，O(log N)。返回 `date` 右侧（严格大于）第一个元素的位置，即下一个交易日的 index。

### 3.2 `_build_execution_map`（第 99-139 行）

构建 `{T: T+1}` 字典，有一个特殊处理：

```python
# 把回测前最后一次调仓的信号在回测第一天执行，消除首月空仓偏差
pre_start = [T for T in rebalance_dates if T < backtest_start]
if pre_start and all_trading_dates:
    T_last = max(pre_start)
    T1 = _next_trading_day(T_last, all_trading_dates)
    if T1 is not None and T1 <= backtest_end:
        result[T_last] = T1
```

**为什么需要这个特殊处理？**

验证集从 2022-01-01 开始。最近一次调仓可能是 2021-12-31。如果不处理，验证期第一个月策略是空仓，但基准是满仓，会产生一个月的虚假跑输。

这段代码把回测起始前最后一个调仓日（2021-12-31）映射到验证期第一个交易日（2022-01-04），保证第一天就建仓，消除首月偏差。

### 3.3 价格面板加载（第 160-182 行）

```python
close_adj = close_adj.ffill()   # 停牌日用上一收盘价估值，open_adj 不 ffill
```

**为什么 close_adj 需要 ffill 而 open_adj 不需要？**

- `close_adj`：用于**每日估值**。停牌日无收盘价时，用上一交易日收盘价估值（价格不变，持仓锁定）。这是正确的持有期间估值方式。
- `open_adj`：用于**执行交易**。停牌日开盘价为 NaN 表示"无法在此价格成交"，不能填充（填充会造成用错误价格下单）。

### 3.4 状态映射（第 185-226 行）

```python
def _status_to_trade_state(status_snap, all_codes) -> pd.Series:
    state = pd.Series(TradeState.FREE.value, index=all_codes)

    buy_limit = _to_bool("is_st") | _to_bool("is_new_stock")
    state[buy_limit]                        = TradeState.BUY_LIMIT.value
    state[_to_bool("is_limit_down_locked")] = TradeState.NO_SELL.value
    state[_to_bool("is_limit_up_locked")]   = TradeState.NO_BUY.value
    state[_to_bool("is_suspended")]         = TradeState.LOCKED.value

    # F7-002: 状态快照中完全缺失的股票 → LOCKED
    missing_mask = snap.isna().all(axis=1)
    state[missing_mask] = TradeState.LOCKED.value
```

**优先级由赋值顺序决定（后赋值覆盖前）**：
1. BUY_LIMIT（ST/次新）
2. NO_SELL（跌停）
3. NO_BUY（涨停）
4. LOCKED（停牌）← 最高优先级，最后赋值

这样停牌股即使同时被标记为 ST，也会被 LOCKED 覆盖（停牌优先）。

**F7-002**：状态快照中完全没有记录的股票保守设为 LOCKED（状态未知不得交易），这是"宁可错过机会，不做错误交易"的保守策略。

`_to_bool` 内部用 `numpy` 路径避免 pandas 的 FutureWarning（object dtype 的 fillna 自动降级问题）。

### 3.5 基准净值加载（第 254-294 行）

```python
def _load_benchmark_nav(...) -> pd.Series:
    iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
    if "nav" in iq.columns:
        bench_raw = iq["nav"]
    elif "close" in iq.columns:
        raise ValueError(
            "index_quote.parquet 缺少 nav 列（全收益指数），不允许降级使用 close（价格指数）。"
            ...
        )
```

**这里是硬性拦截**：如果 `nav` 列不存在但 `close` 列存在，代码**直接抛异常而不是降级**。

原因（来自 CLAUDE.md）：使用价格指数会虚增 2-3% 年化超额收益（分红部分被忽略），结论完全失真。这是领域规则的强制执行，不是工程选择。

---

## 四、单次调仓执行（第 297-435 行）

```python
def _execute_rebalance(
    shares, cash, target_w, open_prices, status,
    portfolio_value, exec_date, rebalance_date,
) -> tuple[dict, float, TradeRecord]:
```

### 4.1 两阶段执行保证资金守恒

**Phase 1：先卖后挂买单**

```python
for code in all_codes:
    # 确认价格可用
    if not (price > 0):
        n_no_price += 1
        continue

    # 根据状态决定操作
    if state == TradeState.LOCKED:
        continue
    elif state == TradeState.NO_BUY:
        if target_value < current_value: actual_value = target_value  # 允许减仓
        else: n_no_buy += 1; continue                                 # 想加仓但涨停
    elif state == TradeState.NO_SELL:
        if target_value > current_value: actual_value = target_value  # 允许加仓
        else: n_no_sell += 1; continue                                # 想减仓但跌停

    if delta < -1e-6:    # 卖出：立即执行
        sell_value += abs(delta)
        new_shares[code] = actual_value / price
    elif delta > 1e-6:   # 买入：推迟到 Phase 2
        buy_orders[code] = (actual_value, price, current_value)
```

**Phase 2：按可用资金等比缩减买入**

```python
available_cash  = cash + sell_value  # 原有现金 + 卖出所得
total_buy_need  = sum(av - cv for av, _p, cv in buy_orders.values())

if total_buy_need <= available_cash + 1e-6:
    # 资金充足，全额买入
    ...
elif total_buy_need > 1e-10 and available_cash > 1e-10:
    # 资金不足，等比缩减（保持相对权重不变）
    buy_scale = available_cash / total_buy_need
    ...
```

**为什么需要两阶段？** 如果先买后卖，可能在买入时资金不足（需要等卖出所得）。先卖汇资金，再按可用资金买入，保证不出现隐含融资。

### 4.2 只处理持仓和目标股票（F7-003）

```python
all_codes = set(shares.keys()) | {
    c for c in target_w.index if float(target_w.get(c, 0.0)) > 1e-10
}
```

**F7-003**：不遍历整个宇宙（500 只股票），只遍历当前持仓 + 目标权重 > 0 的股票。这避免了 `n_no_price` 被大量历史曾持有但现在不持有的股票污染。

### 4.3 成本扣除（等比缩减）

```python
cost = compute_trade_cost(sell_value, buy_value, exec_date)
scale = max(0.0, 1.0 - cost / portfolio_value)
new_shares = {c: s * scale for c, s in new_shares.items() if s * scale > 1e-10}
cash_after = max(0.0, raw_cash) * scale
```

满仓时 `raw_cash ≈ 0`，直接从现金扣除成本会使现金变负。改为等比缩减全部头寸，`(cash + stocks) × scale = portfolio_value - cost`，精确保证净值守恒。

---

## 五、主循环（第 438-536 行）

```python
def _run_main_loop(weights_panel, execution_map, open_adj_panel,
                    close_adj_panel, exec_status, trading_dates, config):
    shares = {}            # {ts_code: 持仓股数}
    cash   = config.initial_value  # 初始全仓现金（= 1.0）
    ...
    for date in trading_dates:
        # 若今天是执行日（T+1），触发调仓
        if date in exec_date_to_T:
            T        = exec_date_to_T[date]
            target_w = weights_panel.loc[T]
            ...
            # F7-001: 用 T+1 开盘价计算执行前净值
            pretrade_value = cash + Σ(shares × max(open_price, close_price))
            new_shares, new_cash, record = _execute_rebalance(...)
            shares = new_shares

        # 每日收盘估值
        close_prices = close_adj_panel.loc[date]
        stock_value  = Σ(shares × close_prices)
        portfolio_value = cash + stock_value
        nav_dict[date] = portfolio_value / config.initial_value
```

### 5.1 F7-001：执行前净值用开盘价计算

```python
prices_pretrade = op.where(op > 0, cl).fillna(0.0)
pretrade_value = cash + float((s_series * prices_pretrade).sum())
```

`op.where(op > 0, cl)`：有开盘价用开盘价，无开盘价（停牌）用最近收盘价（cl 经过 ffill）。

**为什么要专门计算执行前净值？** 如果用昨日收盘价计算"可用资金"来决定今日买入量，而实际执行用今日开盘价，两者价格不同会产生"虚假"买入或卖出规模。F7-001 要求用执行日开盘价计算执行前净值，保证 `target_value = target_w × pretrade_value` 的单位与 `open_price × shares` 一致。

---

## 六、公开接口：`run_backtest`（第 543-627 行）

```python
def run_backtest(weights_panel, backtest_start, backtest_end,
                  config=BacktestConfig()) -> BacktestResult:
    # 阶段 0：构建执行映射
    trading_dates = _load_all_trading_dates(...)
    execution_map = _build_execution_map(...)

    # 阶段 1：加载价格面板（一次性 I/O）
    open_adj_panel, close_adj_panel = _load_price_panels(all_codes, ...)

    # 阶段 2：预加载执行日状态（避免主循环内重复 I/O）
    exec_status = _load_exec_status(exec_dates, all_codes)

    # 阶段 3：主循环
    nav_dict, weight_rows, trade_records = _run_main_loop(...)

    # 阶段 4：汇总结果
    nav_aligned, bench_aligned = nav_series.align(benchmark_nav, join="inner")
    metrics_dict = summarize(nav_aligned, bench_aligned)
    return BacktestResult(...)
```

**预加载设计**：价格面板和状态数据都在主循环外一次性加载，主循环只做内存查询，避免 I/O 成为性能瓶颈。

---

## 七、完整数据流

```
weights_panel (T × code)
        │
        ▼
_build_execution_map → {T: T+1}
        │
        ├── _load_price_panels → open_adj, close_adj
        ├── _load_exec_status → {T+1: TradeState Series}
        │
        ▼ for each trading_date:
        if T+1 → _execute_rebalance
              Phase1: 卖出（立即）
              Phase2: 按可用资金买入
              扣除 compute_trade_cost
        every day → 收盘估值 → nav_dict[date]
        │
        ▼
nav_series = pd.Series(nav_dict)
benchmark_nav = _load_benchmark_nav(...)   ← nav 列（全收益），不用 close 列
metrics = summarize(nav_aligned, bench_aligned)
return BacktestResult
```
