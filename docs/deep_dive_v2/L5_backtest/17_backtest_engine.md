# 17 — engine.py：回测主循环

> 对应源文件：`src/backtest/engine.py`
> 实际行数：**628 行**（计划快照为 472，新增 F7-001~003 多项修复，撰写前已核实）
> 处理方式：**沿用 + 核查更新**
> 所属 Part：Part 6 — 回测与归因层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 6  归因       src/attribution/（brinson / factor_attr）
Layer 5  回测       ← 本文件（engine.py）
Layer 5  回测       transaction.py / metrics.py
Layer 4  组合构建   optimizer.py（提供 weights_panel）
Layer 0  数据基础   src/data/loader.py（load_daily_quote / load_stock_status）
```

`engine.py` 是**回测层的唯一入口**，接受目标权重面板（调仓日 × 股票），输出净值曲线、实际权重、交易记录和绩效指标。

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游 | `optimizer.py` | 提供 `weights_panel`（调仓日 × 股票目标权重）|
| 上游 | `src/data/loader.load_daily_quote` | 加载后复权开盘价和收盘价 |
| 上游 | `src/data/loader.load_stock_status` | 加载每日股票状态（停牌/涨跌停）|
| 下游 | `metrics.summarize` | 计算绩效指标 |
| 下游 | `transaction.compute_trade_cost` | 计算交易成本 |
| 下游 | `attribution/brinson.py` | 消费 `BacktestResult.actual_weights` |

### 计划快照 vs 实际变化

| 变化 | 说明 |
|------|------|
| **F7-001**：执行前净值用 T+1 开盘价 | 避免用 T 日收盘价估算净值、T+1 开盘前价格波动导致目标金额偏差 |
| **F7-002**：状态快照缺失 → LOCKED | 保守处理：状态未知不得交易，防止异常数据导致买入停牌股 |
| **F7-003**：`n_no_price` 统计修复 | 只统计当前持仓或目标为正的股票，避免被历史全量股票污染 |
| `_load_benchmark_nav` 拒绝价格指数 | 明确校验 `nav` 列存在，若仅有 `close` 列则 `raise ValueError`（全收益指数强制要求）|
| 行数从 472 → 628 | 新增函数、完整 docstring、防御性检查 |

---

## 二、时间对齐与 PIT 假设

这是整个项目中**时间对齐逻辑最复杂的模块**，核心规则：

```
T 日（月末）：
  - 收盘后生成目标权重（来自 weights_panel[T]）
  - 目标权重基于 ≤T 日的信号、基准权重、协方差

T+1 日（执行日）：
  - 开盘价买卖（open_adj）
  - 执行前净值用 T+1 开盘价估算（F7-001）
  - 扣除交易成本（印花税 + 佣金 + 滑点）

T+1 ~ T_next-1：
  - 每日收盘价估值（close_adj）
  - close_adj 已做 ffill（停牌日用上一收盘价）

T_next：下一个调仓日，重复上述流程
```

**首月处理**（`_build_execution_map` L123–129）：
若 `backtest_start` 之前存在调仓日，将最近的调仓日映射到 `backtest_start`（回测第一个交易日），保证策略从第一天开始有持仓，不因"首月空仓"产生基准相对偏差。

**基准必须是全收益指数**（L274–286）：
```python
if "nav" in iq.columns:
    bench_raw = iq["nav"]
elif "close" in iq.columns:
    raise ValueError("index_quote.parquet 缺少 nav 列（全收益指数）...")
```
若 `index_quote.parquet` 只有 `close` 列（价格指数），直接抛异常，不允许降级——因为价格指数会虚增 2-3%/年的超额收益。

---

## 三、模块顶部：导入与常量

```python
# L48–76
@dataclass
class BacktestConfig:
    initial_value: float = 1.0    # 组合起始净值

@dataclass
class TradeRecord:
    exec_date / rebalance_date / sell_value / buy_value / cost /
    portfolio_value_before / portfolio_value_after /
    n_locked / n_no_buy / n_no_sell / n_no_price

@dataclass
class BacktestResult:
    nav: pd.Series               # 日频策略净值（起点=1.0）
    benchmark_nav: pd.Series     # 日频基准净值（起点=1.0）
    excess_nav: pd.Series        # nav / benchmark_nav
    actual_weights: pd.DataFrame  # 每日实际权重（date × ts_code）
    trade_log: pd.DataFrame      # 每次调仓记录
    metrics: dict                # summarize() 输出
```

`TradeRecord` 中的 `n_*` 字段用于诊断：
- `n_locked`：当日停牌（状态 LOCKED），现有持仓保持不变
- `n_no_buy`：涨停（NO_BUY），想加仓但无法买入
- `n_no_sell`：跌停（NO_SELL），想减仓但无法卖出
- `n_no_price`：停牌无开盘价（F7-003 修复后仅统计相关股票）

---

## 四、核心函数逐一解析

### 4.1 `run_backtest` — 公开接口

```python
# L543–627
def run_backtest(
    weights_panel: pd.DataFrame,
    backtest_start: pd.Timestamp,
    backtest_end: pd.Timestamp,
    config: BacktestConfig = BacktestConfig(),
) -> BacktestResult:
```

**四阶段执行**：
```
阶段 0：_build_execution_map(rebalance_dates, ...)   → {T: T+1}
阶段 1：_load_price_panels(all_codes, ...)          → (open_adj, close_adj)
阶段 2：_load_exec_status(exec_dates, all_codes)    → {T+1: TradeState Series}
阶段 3：_run_main_loop(...)                         → (nav_dict, weight_rows, trade_records)
```

**E13 对齐**（L599）：
```python
nav_aligned, bench_aligned = nav_series.align(benchmark_nav, join="inner")
```
`inner` 对齐防止日期不一致时产生 NaN 传播（某些极端情况下基准数据与行情数据日期不完全重叠）。

---

### 4.2 `_execute_rebalance` — 单次调仓执行

```python
# L297–435
def _execute_rebalance(
    shares, cash, target_w, open_prices, status,
    portfolio_value, exec_date, rebalance_date,
) -> tuple[dict, float, TradeRecord]:
```

**两阶段执行设计（保证资金守恒）**：

**Phase 1：卖出**（L346–391）：
```python
for code in all_codes:
    # 确认价格有效
    if not (price > 0): n_no_price += 1; continue
    # 应用状态约束
    if state == LOCKED:  n_locked += 1; continue
    if state == NO_BUY:
        actual_value = target_value if target < current else continue  # 减仓可，加仓不可
    ...
    if delta < -1e-6:  # 卖出
        sell_value += abs(delta)
        new_shares[code] = actual_value / price
    elif delta > 1e-6:  # 买入 → 推迟到 Phase 2
        buy_orders[code] = (actual_value, price, current_value)
```

**Phase 2：买入（资金不足时等比缩减）**（L393–410）：
```python
available_cash = cash + sell_value   # 原有现金 + 卖出所得
total_buy_need = sum(av - cv for av, _p, cv in buy_orders.values())

if total_buy_need <= available_cash + 1e-6:
    # 资金充足：全额买入
    ...
elif total_buy_need > 1e-10 and available_cash > 1e-10:
    # 资金不足：等比缩减（保持相对权重不变，不允许隐含融资）
    buy_scale = available_cash / total_buy_need
    for code, (actual_value, price, current_value) in buy_orders.items():
        actual_buy = (actual_value - current_value) * buy_scale
        new_shares[code] = (current_value + actual_buy) / price
```

**成本扣除**（L414–420）：
```python
cost = compute_trade_cost(sell_value, buy_value, exec_date)
# 满仓时现金≈0，不能直接从现金扣除，否则成本被静默吸收。
# 改为等比缩减全部头寸（含现金）：
scale = max(0.0, 1.0 - cost / portfolio_value)
new_shares = {c: s * scale for c, s in new_shares.items() if s * scale > 1e-10}
cash_after = max(0.0, raw_cash) * scale
```
这保证：`cash_after + Σ new_shares[c] × open_price[c] = portfolio_value - cost`，即成本从净值扣除，而不是从现金账户扣除（满仓时现金为零）。

**F7-001：执行前净值用 T+1 开盘价**（L487–499）：
```python
if shares:
    op = open_prices.reindex(s_series.index)
    cl = close_adj_panel.loc[date].reindex(s_series.index)
    prices_pretrade = op.where(op > 0, cl).fillna(0.0)  # 停牌用收盘价
    pretrade_value = cash + float((s_series * prices_pretrade).sum())
```
优先使用 T+1 开盘价，停牌无开盘价时退回到 T 日收盘价（已 ffill）。避免隔夜价格波动导致目标金额和实际能买的量不一致。

**F7-002：状态快照缺失 → LOCKED**（`_status_to_trade_state` L219–224）：
```python
missing_mask = snap.isna().all(axis=1)   # 完全不在状态快照中的股票
state[missing_mask] = TradeState.LOCKED.value   # 保守：状态未知不得交易
```

**F7-003：`all_codes` 范围限定**（L341–344）：
```python
all_codes = set(shares.keys()) | {
    c for c in target_w.index if float(target_w.get(c, 0.0)) > 1e-10
}
```
只遍历**当前有持仓**或**目标权重为正**的股票，防止 `n_no_price` 被历史上所有出现过的股票（可能数千只）的停牌状态污染。

---

### 4.3 `_run_main_loop` — 主循环

```python
# L438–536
def _run_main_loop(
    weights_panel, execution_map, open_adj_panel, close_adj_panel,
    exec_status, trading_dates, config,
) -> tuple[dict, list, list]:
```

**主循环结构**：
```python
for date in trading_dates:
    if date in exec_date_to_T:           # 调仓执行日
        T = exec_date_to_T[date]
        target_w = weights_panel.loc[T]
        pretrade_value = ...              # F7-001：T+1 开盘价估值
        new_shares, new_cash, record = _execute_rebalance(...)
        shares = new_shares
        cash   = new_cash

    # 每日收盘估值（无论是否调仓）
    close_prices = close_adj_panel.loc[date]
    portfolio_value = cash + stock_value
    nav_dict[date] = portfolio_value / config.initial_value

    # 记录每日权重（股票市值 / 总净值）
    if shares and portfolio_value > 1e-10:
        weight_rows.append({...})
```

状态：`shares`（{ts_code: 持股数}）和 `cash`（现金）在循环中持续更新。

---

### 4.4 `_status_to_trade_state` — 状态转换

**TradeState 优先级**（高覆盖低）：

```
LOCKED > NO_BUY > NO_SELL > BUY_LIMIT > FREE
```

| 状态 | 来源 | 含义 |
|------|------|------|
| `LOCKED` | `is_suspended` 或快照缺失（F7-002）| 不得交易，保持原有持仓 |
| `NO_BUY` | `is_limit_up_locked` | 涨停，只能减仓，不能加仓 |
| `NO_SELL` | `is_limit_down_locked` | 跌停，只能加仓，不能减仓 |
| `BUY_LIMIT` | `is_st` 或 `is_new_stock` | ST/次新股，只能减仓 |
| `FREE` | 默认 | 无约束 |

注意：`BUY_LIMIT` 的处理与 `NO_BUY` 相同（只能减仓），但来源不同。这保证回测层在优化器预过滤之外仍有独立的安全检查。

---

## 五、内部辅助函数

| 函数 | 位置 | 说明 |
|------|------|------|
| `_next_trading_day` | L82–96 | 二分查找 date 之后的第一个交易日（`bisect.bisect_right`）|
| `_build_execution_map` | L99–139 | 构建 {T: T+1} 映射，特殊处理首月 pre-start 调仓 |
| `_load_all_trading_dates` | L142–157 | 从 `index_quote.parquet` 获取回测区间内所有交易日 |
| `_load_price_panels` | L160–182 | 加载 open_adj/close_adj，close_adj 做 ffill，open_adj 不 ffill |
| `_status_to_trade_state` | L185–226 | 状态快照 → TradeState Series（F7-002 保守 LOCKED）|
| `_load_exec_status` | L229–251 | 预加载所有执行日状态，避免主循环内重复 I/O |
| `_load_benchmark_nav` | L254–294 | 加载基准 NAV，强制要求 `nav` 列（全收益），拒绝价格指数 |

---

## 六、落盘产物（Artifacts）

`engine.py` **不直接写文件**，由 `run_backtest_stage` 写入：

| 产物 | 路径 | 内容 |
|------|------|------|
| `nav.parquet` | `runs/.../backtest/nav.parquet` | 日频策略 NAV（index=date）|
| `benchmark_nav.parquet` | `runs/.../backtest/benchmark_nav.parquet` | 日频基准 NAV |
| `excess_nav.parquet` | `runs/.../backtest/excess_nav.parquet` | 超额净值 |
| `actual_weights.parquet` | `runs/.../backtest/actual_weights.parquet` | 每日实际持仓权重 |
| `trade_log.parquet` | `runs/.../backtest/trade_log.parquet` | 逐笔调仓记录 |
| `metrics.json` | `runs/.../backtest/metrics.json` | summarize() 输出的绩效指标字典 |

---

## 七、相关测试

**当前状态**：`tests/` 中没有专门的 `test_backtest_engine.py`（TODO）。

**高优先级待补充测试**：

| 测试场景 | 要点 |
|---------|------|
| T+1 成交时间对齐 | 构造 T 日权重变化，验证 T+1 开盘成交，T 收盘净值不变 |
| 停牌股保持原仓 | `is_suspended=True` 的股票在 T+1 调仓时权重不变 |
| 成本从净值等比扣除 | 满仓时（cash≈0）成本仍从净值扣除，验证 `scale = 1 - cost/NAV` |
| 基准必须用全收益 | `index_quote` 只有 `close` 列时应抛 ValueError |
| 首月 pre-start 处理 | backtest_start 之前有调仓日时策略从第一天有持仓 |
| F7-002 保守 LOCKED | 状态快照缺失的股票被设为 LOCKED，不参与调仓 |
| 2023-08-28 前后印花税 | trade_log 中两个时点的成本有差异（通过 `transaction.py`）|

---

## 八、失败与降级路径

| 失败场景 | 行为 |
|---------|------|
| `index_quote.parquet` 只有 `close` 无 `nav` | `run_backtest` 抛 `ValueError`（全收益基准强制要求）|
| `load_stock_status(d)` 抛 `KeyError` | 该执行日全部股票设为 LOCKED（F7-002 保守路径）|
| `weights_panel` 为空 | `_run_main_loop` 返回空，`BacktestResult.nav` 为空 Series |
| 权重和 ≠ 1（数值误差）| 主循环入口处强制归一化（偏差 > 1e-4 时 warning）|
| 买入资金不足 | Phase 2 等比缩减买入量，不允许融资，不抛异常 |
| `close_adj_panel` 缺某些交易日 | 用 `portfolio_value` 前一期值延续，记录 warning |

---

## 九、数据流图

```
weights_panel (rebalance_date × ts_code)
    │
    ▼
_build_execution_map(rebalance_dates, ...)
    → {T: T+1}
    │
    ▼
_load_price_panels(all_codes, start, end)
    → open_adj (date × code)
    → close_adj (date × code, ffill)
    │
    ▼
_load_exec_status(exec_dates, all_codes)
    → {T+1: Series(ts_code → TradeState)}
    │
    ▼
_run_main_loop
┌─────────────────────────────────────┐
│ for date in trading_dates:          │
│   if date == T+1:                   │
│     pretrade_value = ∑ shares × op  │  ← F7-001
│     _execute_rebalance(...)         │
│       Phase1: 卖出（立即执行）       │
│       Phase2: 买入（等比分配）       │
│       扣成本：scale = 1 - cost/NAV  │
│   portfolio_value = cash + Σ s×cp  │  ← 每日估值
│   nav[date] = portfolio_value       │
└─────────────────────────────────────┘
    │
    ▼
BacktestResult
    .nav                ← 日频策略净值
    .benchmark_nav      ← 日频基准净值（全收益）
    .excess_nav         ← nav / benchmark_nav
    .actual_weights     ← 每日实际持仓权重
    .trade_log          ← 逐笔调仓记录
    .metrics            ← summarize() 绩效字典
```

---

## 十、领域知识补充

### T+1 成交假设的重要性

截面策略中，T 日（月末）收盘后我们才知道最终的 CSI500 成分股快照和信号值，此时 T 日市场已收盘，无法成交。因此必须等到 T+1 开盘执行。

若使用 T 日收盘价成交（常见错误），等价于假设策略能在信号生成的**同一时刻**完成交易，这在现实中不可能——月末调仓是在收盘后才能确认的。T+1 成交假设是**正确的、保守的**设计。

### 成本扣除方式的选择

本模块使用**等比缩减全部持仓**来扣除成本：
```
scale = 1 - cost / portfolio_value
new_shares[c] = old_shares[c] × scale
cash_after    = old_cash × scale
```
这等价于"成本从组合净值按比例扣减"，是一种简化但在月频换手率不极端时误差极小的处理。替代方案是"先出售足够多的股票获得现金，再扣除成本"，但对月频策略而言两者结果几乎相同，且后者实现更复杂。

### 停牌估值的 ffill 假设

`close_adj.ffill()` 的语义是：停牌期间，该股票的市场价值被冻结在最后一个交易收盘价。这与实际情况接近（停牌股持有者的名义持仓市值确实没有日内变化），但复牌时可能产生跳空（price gap），`open_adj` 保持 NaN（不 ffill）正是反映这一点——复牌首日的开盘价反映了停牌期间的信息变化。
