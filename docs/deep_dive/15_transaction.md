# 15 — 交易成本：transaction.py 逐行精讲

> 对应源文件：`src/backtest/transaction.py`（60 行）  
> 前置依赖：`01_config.md`（STAMP_DUTY_CUT_DATE、COMMISSION_RATE、SLIPPAGE_RATE）  
> 核心任务：计算单次调仓的三类交易成本之和

---

## 一、三类成本的组成

本模块计算的成本结构：

| 成本类型 | 方向 | 费率 | 来源 |
|---------|------|------|------|
| 印花税 | 卖出单边 | 2023-08-28 前 10 bps，之后 5 bps | `cfg.STAMP_DUTY_*` |
| 佣金 | 买卖双边各 2.5 bps | 2.5 bps | `cfg.COMMISSION_RATE` |
| 滑点 | 买卖双边各 8 bps | 8 bps | `cfg.SLIPPAGE_RATE` |

注意：印花税只收卖出方，佣金和滑点买卖双方都计。

---

## 二、印花税税率函数（第 18-29 行）

```python
def stamp_duty_rate(trade_date: pd.Timestamp) -> float:
    if trade_date >= cfg.STAMP_DUTY_CUT_DATE:
        return cfg.STAMP_DUTY_AFTER   # 0.0005（5 bps）
    return cfg.STAMP_DUTY_BEFORE      # 0.001（10 bps）
```

**参数是 `trade_date`**（T+1 实际执行日），不是 `rebalance_date`（T 日）。  
2023-08-27 日是最后一个按 10 bps 征收印花税的交易日，2023-08-28（含）起改为 5 bps。

> 背景：2023 年 8 月 27 日收盘后，证监会公告调降印花税。所以 8 月 28 日开盘成交即适用新税率。

`cfg.STAMP_DUTY_CUT_DATE = pd.Timestamp("2023-08-28")`，使用 `>=` 判断（含当天）。

---

## 三、总成本计算（第 32-59 行）

```python
def compute_trade_cost(
    sell_value: float,
    buy_value: float,
    trade_date: pd.Timestamp,
) -> float:
    assert sell_value >= 0 and buy_value >= 0

    stamp_duty = sell_value * stamp_duty_rate(trade_date)
    commission = (sell_value + buy_value) * cfg.COMMISSION_RATE
    slippage   = (sell_value + buy_value) * cfg.SLIPPAGE_RATE

    return stamp_duty + commission + slippage
```

### 3.1 `assert` 的使用

```python
assert sell_value >= 0 and buy_value >= 0
```

这里用 `assert` 而不是 `if/raise`。在本项目语境中，`sell_value` 和 `buy_value` 是由 `engine.py` 内部计算并传入的，调用方保证非负（边界校验在调用方）。用 `assert` 明确声明这是内部前提条件，不是用户输入校验。

### 3.2 滑点的业务含义

滑点 = 预期成交价和实际成交价之间的差。月末调仓涉及较多股票的同向交易，市场冲击不可忽略。8 bps 双边（买入 8 bps + 卖出 8 bps = 合计 16 bps）是 A 股中小市值股票的常用估计值。

### 3.3 成本单位

`sell_value` 和 `buy_value` 是**绝对金额**，与 `portfolio_value` 同单位（本项目中初始净值=1.0）。返回值也是金额，engine 从净值中直接扣除。

---

## 四、成本在 engine 中的扣除方式

engine 拿到 `cost` 后，不是简单减去现金，而是**等比缩减全部头寸**：

```python
scale = max(0.0, 1.0 - cost / portfolio_value)
new_shares = {c: s * scale for c, s in new_shares.items()}
cash_after = max(0.0, raw_cash) * scale
```

**为什么不直接扣现金？** 满仓策略中持股比例接近 100%，扣完股票后几乎无现金，直接扣现金会导致现金变负（隐含杠杆）。等比缩减头寸保证成本被均摊到整个组合，且无论满仓还是空仓都数值安全。

---

## 五、数字示例

假设：
- `portfolio_value = 1.0`（归一化净值）
- 调仓时 `sell_value = 0.3`，`buy_value = 0.3`
- `trade_date = 2024-01-31`（2023-08-28 之后）

```
stamp_duty = 0.3 × 0.0005 = 0.00015
commission = (0.3 + 0.3) × 0.000025 = 0.000015（2.5 bps，双边各算）
slippage   = (0.3 + 0.3) × 0.0008 = 0.00048
total_cost = 0.00015 + 0.000015 + 0.00048 = 0.000645
```

单次调仓成本约 6.45 bps（相对净值）。月调仓、年换手约 5 倍时，年化成本约 32 bps，与行业惯例相符。
