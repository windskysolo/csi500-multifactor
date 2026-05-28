# 04 — 投资域与交易约束状态 `src/data/universe.py`

---

## 一、文件定位

```
流程位置：第 3 步（因子构建）和第 6 步（组合优化）的共同依赖
读取：通过 loader.load_universe() 读 index_member.parquet
写入：无（纯查询与转换）
被谁调用：
  - src/factors/*.py（因子计算只在可投资域内进行）
  - src/portfolio/optimizer.py（优化器需要全量 500 只 + 四类约束）
  - src/backtest/engine.py（回测引擎每期判断成交约束）
调用谁：src/data/loader.py（load_universe / get_rebalance_dates）
```

**本模块的职责分工：**

| 使用方 | 需要的信息 | 对应函数 |
|--------|-----------|---------|
| 因子计算 | 哪些股票可以计算（tradable=True）| `get_investable_universe` |
| 优化器 | 所有500只 + 各自的四类约束状态 | `get_full_universe` + `get_constraint_states` |
| 主循环 | 调仓日列表 | `get_trade_dates` |

---

## 二、模块顶部

```python
from enum import Enum
from typing import Optional
import pandas as pd
from src.data.loader import get_rebalance_dates, load_universe
import logging
log = logging.getLogger(__name__)
```

`Enum` 用于定义 `TradeState`：使用枚举而非字符串常量的好处是类型安全（IDE 可检查、不会拼错）和继承 `str` 后可直接存入 DataFrame 并做字符串比较。

---

## 三、核心函数逐一解析

### 3.1 `TradeState` 枚举（L30-41）

```python
class TradeState(str, Enum):
    LOCKED    = "locked"      # 停牌
    NO_BUY    = "no_buy"      # 涨停一字板
    NO_SELL   = "no_sell"     # 跌停一字板
    BUY_LIMIT = "buy_limit"   # ST 或新股
    FREE      = "free"        # 正常可交易
```

**继承 `str` 的实际意义：**

```python
# 存入 DataFrame 时，值是字符串，不是枚举对象
state["000001.SZ"] = TradeState.FREE.value    # = "free"

# 可以直接比较
if state["000001.SZ"] == TradeState.FREE.value:   # 有效
if state["000001.SZ"] == "free":                   # 也有效
```

**五个状态的含义与回测约束：**

| 状态 | 触发条件 | 优化器约束 | 回测约束 |
|------|---------|-----------|---------|
| `LOCKED` | 停牌 | 权重固定为上期值 `w_i = w_prev_i`（退出优化变量）| 无法成交，持仓不变 |
| `NO_BUY` | 一字涨停 | `w_i ≤ w_prev_i`（不加仓）| 只能卖出，不能买入 |
| `NO_SELL` | 一字跌停 | `w_i ≥ w_prev_i`（不减仓）| 只能买入，不能卖出 |
| `BUY_LIMIT` | ST 或新股 | `w_i ≤ w_prev_i`（只减不增）| 已持有的可继续持有，但不能建仓 |
| `FREE` | 无限制 | 无额外约束 | 正常成交 |

**"一字板"的含义：**

涨停一字板（NO_BUY）：开盘即涨停，当日全天以涨停价成交，买单全部排队无法成交（只能卖出）。

跌停一字板（NO_SELL）：开盘即跌停，全天以跌停价成交，卖单排队无法成交（只能买入）。

普通涨跌停（非一字板）：开盘有一段时间可以正常交易，只是收盘价到达涨停/跌停价位。这种情况不一定完全无法成交。

项目中的 `is_limit_up_locked` / `is_limit_down_locked` 专指一字板，是比一般涨跌停更严格的限制。

---

### 3.2 `get_investable_universe(rebalance_date)` — 可投资域（L44-66）

```python
def get_investable_universe(rebalance_date: pd.Timestamp) -> pd.Index:
```

**功能**：返回调仓日 T 可以参与因子计算和选股的股票代码列表（已过滤不可交易股票）。

**执行逻辑（只有两行）：**

```python
snap = load_universe(rebalance_date)       # 读取月末500只成分股快照
investable = snap[snap["tradable"]].index  # 取 tradable=True 的股票
```

**`tradable` 字段是在哪里计算的？**

`tradable` 字段由 `csv_to_parquet.py`（数据格式转换阶段）在构建 `index_member.parquet` 时计算，综合了停牌、ST、新股三种状态：

```python
# csv_to_parquet 阶段的计算逻辑（伪代码）
tradable = ~is_suspended & ~is_st & ~is_new_stock
```

**涨跌停为什么不纳入 `tradable`？**

涨停/跌停不影响"这只股票能否被计算因子"，只影响"当期能否成交"。因子计算仍然可以给涨停股计算因子值，优化器通过 `get_constraint_states` 处理涨跌停的交易约束。

**用途示例（因子层调用）：**

```python
# financial_factors.py 中的典型调用
investable = get_investable_universe(rebalance_date)
pit_data = load_financial_pit(rebalance_date, codes=list(investable))
# 只对可投资域的股票计算因子，节省计算量
```

---

### 3.3 `get_constraint_states(rebalance_date)` — 优化器约束状态（L69-98）

```python
def get_constraint_states(rebalance_date: pd.Timestamp) -> pd.Series:
```

**功能**：为每只成分股分配一个 `TradeState`，供优化器构建约束时使用。

**赋值顺序（低优先级 → 高优先级，后者覆盖前者）：**

```python
state = pd.Series(TradeState.FREE.value, index=snap.index)   # 初始全为 FREE

# 按优先级从低到高依次覆盖
restricted = snap["is_st"] | snap["is_new_stock"]
state[restricted]                   = TradeState.BUY_LIMIT.value  # 第1层
state[snap["is_limit_down_locked"]] = TradeState.NO_SELL.value    # 第2层
state[snap["is_limit_up_locked"]]   = TradeState.NO_BUY.value     # 第3层
state[snap["is_suspended"]]         = TradeState.LOCKED.value     # 第4层（最高）
```

**为什么赋值顺序是低优先级在前？**

Python 列表赋值是原地覆盖。如果高优先级先赋值，后续的低优先级赋值会把它覆盖掉——结果错误。按优先级从低到高赋值，确保高优先级的状态在最后生效，不会被覆盖。

**举例：一只股票同时是 ST 且处于停牌状态：**

1. 初始：`FREE`
2. 是 ST → 覆盖为 `BUY_LIMIT`
3. 没有涨跌停 → 不覆盖
4. 停牌 → 覆盖为 `LOCKED`

最终状态：`LOCKED`（正确，停牌比 ST 限制更严格）

测试文件 `test_universe.py:180-198` 验证了这个覆盖逻辑：

```python
def test_suspended_overrides_st(self, monkeypatch):
    """同时停牌且 ST 时，LOCKED 优先级高于 BUY_LIMIT。"""
    ...
    assert states["000001.SZ"] == TradeState.LOCKED.value
```

---

### 3.4 `get_full_universe(rebalance_date)` — 完整宇宙（含约束状态）（L102-118）

```python
def get_full_universe(rebalance_date: pd.Timestamp) -> pd.DataFrame:
```

**功能**：返回全部 500 只成分股的快照，并附加 `trade_state` 列。

```python
snap = load_universe(rebalance_date).copy()       # 500 只成分股
snap["trade_state"] = get_constraint_states(rebalance_date)  # 附加状态列
return snap
```

**为什么优化器需要全部 500 只（包括 `tradable=False` 的）？**

优化器在构建权重向量时，需要对 500 只股票都有一个权重：
- 可交易股票：权重由优化求解
- LOCKED（停牌）股票：权重固定为上期值，写入约束 `w_i = w_prev_i`，不是"忽略"
- 如果只传入 400 只可交易股票，停牌股的权重在优化结果中会不明确（到底是 0 还是保持上期？）

---

### 3.5 `get_trade_dates(start, end)` — 调仓日序列（L121-137）

```python
def get_trade_dates(
    start: Optional[pd.Timestamp] = None,
    end:   Optional[pd.Timestamp] = None,
) -> list[pd.Timestamp]:
```

这是对 `loader.get_rebalance_dates` 的简单转发，提供给上层模块一个语义更清晰的接口名。

```python
return get_rebalance_dates(start=start, end=end)
```

**调用方（因子构建、回测引擎）的典型用法：**

```python
# 训练期的因子构建循环
rebalance_dates = get_trade_dates(start=cfg.TRAIN_START, end=cfg.TRAIN_END)
for date in rebalance_dates:
    factors = compute_all_factors(date)
    ...
```

---

## 四、内部辅助函数

本模块无私有（下划线开头）辅助函数，逻辑全部在公开函数中。

---

## 五、数据流图

```
index_member.parquet
        │
        ↓ loader.load_universe(rebalance_date)
        │
        ├──→  snap[snap["tradable"]].index
        │               │
        │               ↓
        │     get_investable_universe()
        │     → pd.Index（可投资股票代码）
        │     → 因子计算使用
        │
        └──→  逐行赋值 TradeState
                        │
                        ↓
              get_constraint_states()
              → pd.Series（500行，每行一个 TradeState）
              → 优化器约束构建 / 回测成交约束
```

---

## 六、领域知识补充

**为什么中证500成分股不总是刚好500只？**

中证指数公司每年 6 月和 12 月定期调整成分股（调整期间进出不超过 10%）。在两次调整之间，由于退市、暂停上市等情况，实际成分股数量可能略少于 500 只。因此 `load_universe` 返回的行数可能是 498 或 497，不一定精确为 500。

**投资域过滤的实际影响（典型数据）：**

在一个月末调仓日，约 500 只成分股中通常：
- 停牌：5-15 只（市场平静时少，极端行情时多，如 2015 年股灾期间曾超 100 只）
- ST：10-30 只（质量较差的公司）
- 新股（上市<180天）：1-5 只
- 一字涨停：0-20 只（正常 A 股涨停股少，但也有极端情况）
- 一字跌停：类似

最终可投资域通常在 450-490 只之间。

**`BUY_LIMIT` 状态的含义细节：**

`BUY_LIMIT` 约束（只减不增）针对 ST 和新股，具体规则是：
- 如果上期权重为 0（从未持有过）：禁止建仓，`w_i = 0`
- 如果上期权重 > 0（已经持有）：允许维持或减仓，但不能加仓，`w_i ≤ w_prev_i`

这模拟了实际监管要求：部分 ST 股票有"其他风险"提示，公募基金受限于内部风控规定，通常不能在 ST 期间买入，但持有中的仓位可以逐步清出。
