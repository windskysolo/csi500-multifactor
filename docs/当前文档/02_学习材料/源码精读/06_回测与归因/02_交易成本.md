# 18 — transaction.py：交易成本计算

> 对应源文件：`src/backtest/transaction.py`
> 实际行数：**60 行**（计划快照为 39，新增完整 docstring，撰写前已核实）
> 处理方式：**沿用 + 核查更新**
> 所属 Part：Part 6 — 回测与归因层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 5  回测    ← 本文件（transaction.py）
Layer 5  回测    engine.py（调用 compute_trade_cost）
Layer 0  配置    src/config.py（印花税切换日期、佣金率、滑点率）
```

`transaction.py` 是**最简单的业务模块之一**，只有两个函数，职责单一：给定卖出金额、买入金额、交易日期，返回总交易成本。

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游 | `src/config.py` | `STAMP_DUTY_CUT_DATE`、`STAMP_DUTY_BEFORE/AFTER`、`COMMISSION_RATE`、`SLIPPAGE_RATE` |
| 下游 | `engine._execute_rebalance` | 每次调仓后调用 `compute_trade_cost` 计算总成本 |

---

## 二、时间对齐与 PIT 假设

`transaction.py` 不访问外部数据，无 PIT 风险。

唯一与时间相关的逻辑是印花税率的切换：

```
trade_date < 2023-08-28：卖出印花税 10 bps（0.1%）
trade_date ≥ 2023-08-28：卖出印花税  5 bps（0.05%）
```

**关键**：`trade_date` 是**实际执行日**（T+1 开盘日），不是调仓日（T）。月末调仓日 T 与执行日 T+1 可能跨过 2023-08-28 的边界，必须用 T+1 的日期计算印花税。

---

## 三、模块顶部：导入与常量

```python
import pandas as pd
from src import config as cfg
```

所有成本参数均来自 `src/config.py`，**不在本文件写死**。成本参数的权威来源是 `config.py`，本文档不列具体数值（遵守 CLAUDE.md §0 原则）。

---

## 四、核心函数逐一解析

### 4.1 `stamp_duty_rate` — 印花税率

```python
# L18–29
def stamp_duty_rate(trade_date: pd.Timestamp) -> float:
    """
    返回 trade_date 对应的印花税率（小数形式）。

    Args:
        trade_date: 实际执行交易的日期（T+1 开盘日），不是调仓日 T
    Returns:
        cfg.STAMP_DUTY_BEFORE（2023-08-28 前）或 cfg.STAMP_DUTY_AFTER（之后）
    """
    if trade_date >= cfg.STAMP_DUTY_CUT_DATE:
        return cfg.STAMP_DUTY_AFTER
    return cfg.STAMP_DUTY_BEFORE
```

**设计意图**：独立成函数，便于测试（CLAUDE.md §5.5 要求此函数必须有单元测试，覆盖切换前后两个时点）。

**印花税特性**：
- 仅对**卖出**单边收取（不对买入收取）
- 2023-08-28 是调整日（含），当天及之后按新税率
- 查阅 `src/config.py` 获取实际 bps 值

---

### 4.2 `compute_trade_cost` — 总交易成本

```python
# L32–59
def compute_trade_cost(
    sell_value: float,
    buy_value: float,
    trade_date: pd.Timestamp,
) -> float:
```

**三类成本的计算**：
```python
# 印花税：仅卖出单边收取
stamp_duty = sell_value * stamp_duty_rate(trade_date)

# 佣金：买卖双边均计
commission = (sell_value + buy_value) * cfg.COMMISSION_RATE

# 滑点：买卖双边均计（模拟市场冲击成本）
slippage = (sell_value + buy_value) * cfg.SLIPPAGE_RATE

return stamp_duty + commission + slippage
```

**参数约束**（L49）：
```python
assert sell_value >= 0 and buy_value >= 0
```
使用 `assert` 而非 `raise ValueError`，因为 sell/buy 为负的情况是调用方的 bug（内部契约），而非用户输入错误。

**与 engine.py 的接口约定**：
- `sell_value`、`buy_value` 均为**交易前价格 × 股数**的估算金额，不含成本自身
- 返回值与 `portfolio_value` 单位一致（不是百分比）
- `engine._execute_rebalance` 调用后用 `scale = 1 - cost/portfolio_value` 等比扣除

---

## 五、内部辅助函数

无私有辅助函数，模块极简。

---

## 六、落盘产物（Artifacts）

`transaction.py` 不写任何文件。成本数据通过 `TradeRecord.cost` 字段在 `engine.py` 中记录，最终落盘到 `trade_log.parquet`。

---

## 七、相关测试

CLAUDE.md §5.5 要求有印花税切换点的对比测试。

**当前状态**：无专门的 `test_transaction.py`（TODO）。

**必须补充的测试**：

| 测试场景 | 要点 |
|---------|------|
| 2023-08-28 之前的印花税率 | `stamp_duty_rate(pd.Timestamp("2023-08-27"))` 返回 `STAMP_DUTY_BEFORE` |
| 2023-08-28 当天的印花税率 | `stamp_duty_rate(pd.Timestamp("2023-08-28"))` 返回 `STAMP_DUTY_AFTER` |
| 仅卖出时印花税为卖出量 × 税率 | 买入=0 时，`compute_trade_cost` = `sell × stamp_duty + sell × commission + sell × slippage` |
| 纯买入无印花税 | 卖出=0 时，`stamp_duty = 0.0` |
| 买卖相同金额时成本对称（佣金+滑点双边）| 双边对称验证 |

---

## 八、失败与降级路径

| 失败场景 | 行为 |
|---------|------|
| `sell_value < 0` 或 `buy_value < 0` | `AssertionError`（调用方 bug，不捕获）|
| `config.py` 中成本参数为 0 | 函数正常返回 0（允许零成本模拟）|
| `trade_date` 为 `NaT` | `pd.Timestamp >= cfg.STAMP_DUTY_CUT_DATE` 会抛 `TypeError`（需要调用方保证有效日期）|

---

## 九、数据流图

```
(sell_value, buy_value, trade_date)
    │
    ▼
stamp_duty_rate(trade_date)
    → STAMP_DUTY_BEFORE  (< 2023-08-28)
    → STAMP_DUTY_AFTER   (≥ 2023-08-28)
    │
    ▼
stamp_duty  = sell_value × rate
commission  = (sell_value + buy_value) × COMMISSION_RATE
slippage    = (sell_value + buy_value) × SLIPPAGE_RATE
    │
    ▼
total_cost = stamp_duty + commission + slippage
    │
    ▼
engine._execute_rebalance
    scale = 1.0 - total_cost / portfolio_value
    new_shares = {c: s × scale ...}
    cash_after = old_cash × scale
```

---

## 十、领域知识补充

### 三类成本的经济含义

**印花税**：政府税收，卖出单边（A 股政策）。2023 年 8 月降低印花税是政府刺激市场的政策操作，降幅 50%（10bps → 5bps）。

**佣金**：券商收费，买卖双边。现实中机构有议价能力，通常远低于散户（散户通常 10~30bps，机构可低至 1~3bps）。`config.py` 中的设定值应反映机构投资者的实际成本水平。

**滑点**：市场冲击成本，买入时推高价格、卖出时压低价格。本模块用固定比例近似（简化处理），实际滑点与订单规模、标的流动性、市场状态（当日换手率）密切相关。对于 CSI500 策略（中盘股，相对流动性充足），固定滑点是合理的一阶近似。

### 成本对换手率的制约关系

总双边交易成本（年化）= 年化双边换手率 × (佣金 + 滑点) × 2 + 年化单边卖出量 × 印花税

这建立了一个隐含约束：换手率不能无限高，否则交易成本会侵蚀 alpha。项目的完成标准"年化双边换手 5-15 倍"正是在此约束下设定的上限——超过 15 倍时，即使 IC_IR 优异，净值表现也可能显著下降。
