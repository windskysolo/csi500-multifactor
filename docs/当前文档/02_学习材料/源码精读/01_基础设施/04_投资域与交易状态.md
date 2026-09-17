# 04 — 投资域与交易约束状态 `src/data/universe.py`

---

## 一、文件定位

```
所属 Part  : Layer 0（数据基础层）
在数据流中 : loader.py → universe.py → factors（因子计算用 investable） + optimizer（用 constraint_states）
被谁调用   : financial_factors.py / price_factors.py / alt_factors.py（用 get_investable_universe）
             portfolio/optimizer.py / pipeline/stages.py（用 get_constraint_states / get_full_universe）
调用谁     : src/data/loader.py（load_universe + get_rebalance_dates）
```

`universe.py` 是 `loader.load_universe()` 的**薄包装层**，职责是：
1. 将 `index_member` 的布尔标志翻译成**因子层可直接使用**的可投资域（`pd.Index`）
2. 将布尔标志翻译成**优化器可直接使用**的约束状态枚举（`TradeState`）

> ⚠️ **与 v1 的差异**：v1 中 `get_investable_universe(date, loader)` 接受 loader 作为参数；v2 不再传 loader，直接调用 `loader.py` 的模块级函数。新增了 `get_constraint_states`、`get_full_universe` 两个函数，覆盖优化器需要的全量快照场景。

---

## 二、时间对齐与 PIT 假设

`index_member.parquet` 是**月末调仓日快照**，数据来源是中证500成分股的历史生效日期（不是公告日）。T 日调仓使用 T 日的成分股快照，这是中证500成分股调整的"实际生效"口径，不含前视偏差。

- `is_new_stock`、`is_suspended`、`is_st` 等字段由 `csv_to_parquet.py` 在 T 日基于已知信息计算，不涉及未来数据
- 涨跌停标志（`is_limit_up_locked`、`is_limit_down_locked`）来自 T 日盘后数据，无前视问题

---

## 三、模块顶部

```python
import logging
from enum import Enum
from typing import Optional

import pandas as pd

from src.data.loader import get_rebalance_dates, load_universe

log = logging.getLogger(__name__)
```

---

## 四、核心函数逐一解析

### 4.1 `TradeState` 枚举（L30-41）

```python
class TradeState(str, Enum):
    LOCKED    = "locked"      # 停牌
    NO_BUY    = "no_buy"      # 涨停一字板
    NO_SELL   = "no_sell"     # 跌停一字板
    BUY_LIMIT = "buy_limit"   # ST 或新股
    FREE      = "free"        # 正常可交易
```

**继承 `str`** 的作用：`TradeState.LOCKED == "locked"` 为 True，便于 DataFrame 存储（不需要额外转换）和日志输出（直接显示字符串值而非 `<TradeState.LOCKED: 'locked'>`）。

**优先级（高 → 低）**：

```
LOCKED（停牌）> NO_BUY（涨停）> NO_SELL（跌停）> BUY_LIMIT（ST/新股）> FREE
```

优先级意味着：一只股票同时满足停牌和涨停条件时，取 LOCKED（更严格约束）。这通过 `get_constraint_states` 里的**赋值顺序**实现（低优先级先赋值，高优先级后赋值覆盖）。

**五种状态对优化器权重的约束含义**：

| 状态 | 约束 | 经济逻辑 |
|---|---|---|
| LOCKED | `w = w_prev`（权重锁定）| 完全无法交易，保持原仓位 |
| NO_BUY | `w ≤ w_prev`（只减不增）| 涨停一字板无买盘，只能减仓或持仓 |
| NO_SELL | `w ≥ w_prev`（只增不减）| 跌停一字板无卖盘，只能加仓或持仓 |
| BUY_LIMIT | `w ≤ w_prev`（同 NO_BUY）| ST/新股风险高，控制加仓；上期为0则禁止建仓 |
| FREE | 无额外约束 | 正常可交易 |

---

### 4.2 `get_investable_universe(rebalance_date)` — 可投资域（L44-66）

```python
def get_investable_universe(rebalance_date: pd.Timestamp) -> pd.Index:
```

**返回值**：`pd.Index`（ts_code 字符串序列）

```python
snap = load_universe(rebalance_date)       # 全部 500 只快照
investable = snap[snap["tradable"]].index  # tradable=True 的子集
```

`tradable` 字段由 `csv_to_parquet.py` 综合三个条件预计算：
- `is_suspended == False`（未停牌）
- `is_st == False`（非 ST）
- `is_new_stock == False`（非新股）

三个条件都满足才是 `tradable=True`。

**注意涨跌停不在此处过滤**：涨跌停不影响因子计算（仍有正常的价格和财务数据），只影响优化器的权重约束，通过 `get_constraint_states` 单独处理。

**典型使用场景**：

```python
codes = get_investable_universe(rebalance_date).tolist()
ep_factor = factor_ep_ttm(rebalance_date, codes)  # 因子计算只在可投资域上
```

---

### 4.3 `get_constraint_states(rebalance_date)` — 优化器约束状态（L69-99）

```python
def get_constraint_states(rebalance_date: pd.Timestamp) -> pd.Series:
```

**返回值**：`pd.Series`，index=ts_code，values=TradeState 字符串值（如 `"locked"`）

**关键实现：低优先级先赋值，高优先级后赋值覆盖**（L87-95）：

```python
state = pd.Series(TradeState.FREE.value, index=snap.index, ...)   # 初始全部 FREE

# 依次覆盖，后写的优先级更高：
state[snap["is_st"] | snap["is_new_stock"]] = TradeState.BUY_LIMIT.value  # L91
state[snap["is_limit_down_locked"]]         = TradeState.NO_SELL.value    # L92
state[snap["is_limit_up_locked"]]           = TradeState.NO_BUY.value     # L93
state[snap["is_suspended"]]                 = TradeState.LOCKED.value     # L94（最高优先级最后写）
```

这种"后写覆盖"的模式比用嵌套 if-else 更清晰，也更 pandas 原生。

---

### 4.4 `get_full_universe(rebalance_date)` — 全量快照（L102-118）

```python
def get_full_universe(rebalance_date: pd.Timestamp) -> pd.DataFrame:
```

**返回值**：500行 DataFrame，包含 `index_member` 原始列 + `trade_state` 列

**为何优化器需要全量 500 只？**

停牌股（`LOCKED`）的权重不参与优化变量（固定在 `w_prev`），但它们的权重**仍然占用总权重预算**。如果只传 tradable 股票给优化器，会导致权重总和错误（tradable 权重合计 < 1 是正常的，差额是锁仓股占用的部分）。

典型使用场景（`stages.py` 的 portfolio 阶段）：

```python
universe_full = get_full_universe(rebalance_date)   # 500 只
investable = universe_full[universe_full["tradable"]].index  # 因子子集
states = pd.Series({code: row["trade_state"] for code, row in universe_full.iterrows()})
weights = optimizer.optimize(alpha, sigma, w_benchmark, w_prev, states)
```

---

### 4.5 `get_trade_dates(start, end)` — 调仓日列表（L121-137）

```python
def get_trade_dates(start=None, end=None) -> list[pd.Timestamp]:
```

包装 `loader.get_rebalance_dates()`，提供统一的调仓日获取入口。因子计算和回测的主循环应通过本函数获取调仓日序列，不要自行生成月末日期（避免生成非交易日）。

---

## 五、内部辅助函数

本模块无私有辅助函数（`_` 前缀的函数）。全部逻辑在 4 个公开函数中内联实现。

---

## 六、落盘产物

无。`universe.py` 不写任何文件，纯查询模块。

---

## 七、相关测试

- `tests/test_pipeline_contracts.py`：间接覆盖 universe 函数
- 建议补充：`test_constraint_states` — 验证优先级覆盖顺序（一只股票同时停牌+涨停时应取 LOCKED）

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| `rebalance_date` 不在 `index_member` | 传播 `loader.load_universe` 的 `KeyError` |
| `tradable` 列全为 False（极端情况）| 返回空 Index，因子计算返回空 Series |

---

## 九、数据流图

```
loader.load_universe(rebalance_date)   ← 整表缓存 _CACHE
    ↓ snap（500 行 DataFrame）
    │
    ├─ snap[snap["tradable"]].index
    │       ↓ get_investable_universe()
    │       用于因子层：codes 参数
    │
    ├─ 逐字段覆盖 TradeState
    │       ↓ get_constraint_states()
    │       用于优化器：约束条件
    │
    └─ snap + trade_state 列
            ↓ get_full_universe()
            用于优化器完整快照
```

---

## 十、领域知识补充

**涨跌停一字板的交易逻辑**：

A 股的涨停（+10% 或科创板+20%）和跌停（-10%）有两种情形：
1. **普通涨跌停**：到达涨跌停价位但有对手盘成交，仍可部分交易
2. **一字板（locked）**：开盘就封死涨停或跌停，全天几乎无对手盘

`is_limit_up_locked` 对应的是"一字板涨停"，意味着当日完全买不到；`is_limit_down_locked` 对应"一字板跌停"，意味着当日完全卖不出。普通涨跌停（未封死）则视为 FREE，可以正常交易（尽管有滑点）。

**为何 BUY_LIMIT 不叫 SELL_LIMIT？**

ST 和新股的逻辑是"控制加仓，不禁止减仓"：
- 上期持有 ST 股票，本期应该允许减仓（卖出）以降低风险
- 上期未持有 ST 股票，本期禁止新建仓（BUY = 受限）

所以约束是 `w ≤ w_prev`（不超过上期权重），买入受限但卖出自由 → 命名 `BUY_LIMIT`。
