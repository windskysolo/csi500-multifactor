# 17 — BHB 行业归因：brinson.py 逐行精讲

> 对应源文件：`src/attribution/brinson.py`（553 行）  
> 前置依赖：`14_backtest_engine.md`（BacktestResult.actual_weights）  
> 核心任务：把每月超额收益分解为"配置效应"+"选股效应"+"交叉效应"

---

## 一、BHB 模型基础

BHB（Brinson-Hood-Beebower）是行业归因的经典框架，把超额收益归因到三种主动决策：

对每个行业 i：

| 符号 | 含义 |
|------|------|
| W_p_i | 策略在行业 i 的权重合计 |
| W_b_i | 基准在行业 i 的权重合计 |
| R_p_i | 策略在行业 i 内的加权平均收益 |
| R_b_i | 基准在行业 i 内的加权平均收益 |
| R_b   | 基准总收益 = Σ W_b_i × R_b_i |

三个效应：

```
配置效应  = (W_p_i - W_b_i) × (R_b_i - R_b)   ← 超配/低配行业是否正确
选股效应  =  W_b_i          × (R_p_i - R_b_i)  ← 行业内选股是否比基准好
交叉效应  = (W_p_i - W_b_i) × (R_p_i - R_b_i)  ← 配置和选股联合效应
总超额    =  W_p_i × R_p_i  -  W_b_i × R_b_i
```

**加总恒等式**（汇总层成立，单行业不一定）：
```
Σ_i (alloc + select + interact) = Σ_i total = R_p_total - R_b_total
```

---

## 二、时间对齐关键约定

### 2.1 三个时间点

| 时间点 | 用于什么 |
|--------|---------|
| T（月末调仓日） | 基准权重、行业映射快照 |
| T+1 之后第一个 actual_weights 日期 | 策略权重（T+1 执行后实际持仓） |
| T+1 开盘 → T_next 收盘 | 期间收益 |

### 2.2 期间收益的计算（F8-001）

```
第一日（T+1）：close_adj / open_adj - 1（开盘入场）
后续日：close_adj(t) / close_adj(t-1) - 1（收盘到收盘）
```

这与回测引擎完全一致：T+1 开盘买，T_next 收盘卖。不含 T 收盘到 T+1 开盘的隔夜段。

### 2.3 策略权重不归一化（F8-002）

```python
return w   # F8-002: 不归一化，保留原始权重和
```

实际持仓中，部分股票停牌导致无法全额买入，权重和 < 1，差值是现金仓位。  
归一化会把现金仓位分配给持仓股，夸大了股票部分的收益贡献，产生误差。

---

## 三、期间列表构建（第 58-88 行）

```python
def _get_period_list(rebalance_dates, backtest_start, backtest_end):
    pre_start = [d for d in rebalance_dates if d < backtest_start]
    in_range  = [d for d in rebalance_dates if backtest_start <= d <= backtest_end]

    anchors = []
    if pre_start:
        anchors.append(max(pre_start))   # 回测前最近一次调仓日
    anchors.extend(in_range)

    return [(anchors[i], anchors[i+1]) for i in range(len(anchors)-1)]
```

包含 `pre_start` 的最近调仓日（同 engine 的 `_build_execution_map`），保证第一个月能被归因。

---

## 四、期间收益计算（第 172-229 行）

```python
def _compute_period_returns_t1_open(T, T_next, codes,
                                     close_adj_panel, open_adj_panel):
    in_period = close_adj_panel.index[
        (close_adj_panel.index > T) & (close_adj_panel.index <= T_next)
    ]
    first_date = in_period[0]

    # 第一日：从开盘入场
    first_close = close_adj_panel.loc[first_date, valid_codes]
    first_open  = open_adj_panel.loc[first_date, valid_codes]
    first_ret   = (first_close / first_open - 1.0).fillna(0.0)
    cum_prod    = 1.0 + first_ret

    if len(in_period) > 1:
        # 后续日：收盘到收盘
        close_slice    = close_adj_panel.loc[first_date:in_period[-1], valid_codes]
        remaining_rets = close_slice.pct_change(fill_method=None).fillna(0.0)
        remaining_dates = in_period[1:]
        cum_prod = cum_prod * (1.0 + remaining_rets.loc[remaining_dates]).prod()

    result = cum_prod - 1.0
    return result.reindex(codes).fillna(0.0), n_missing
```

关键设计：
- `close_slice.pct_change().loc[remaining_dates]`：只取 `in_period[1:]` 日的收益，不含 `first_date`（第一日的"收益"已经通过 close/open 计算了）
- 停牌 NaN → `fillna(0.0)` → 零收益（与回测引擎一致）

---

## 五、BHB 分解核心（第 232-300 行）

```python
def _compute_period_brinson(w_p, w_b, r, industry_map) -> pd.DataFrame:
    df = pd.DataFrame({
        "industry_code": industry_map,
        "w_p": w_p.reindex(all_codes).fillna(0.0),
        "w_b": w_b.reindex(all_codes).fillna(0.0),
        "r":   r.reindex(all_codes).fillna(0.0),
    })
    df["w_p_r"] = df["w_p"] * df["r"]
    df["w_b_r"] = df["w_b"] * df["r"]

    grp  = df.groupby("industry_code")
    W_p  = grp["w_p"].sum()
    W_b  = grp["w_b"].sum()
    wp_r = grp["w_p_r"].sum()
    wb_r = grp["w_b_r"].sum()

    R_p = (wp_r / W_p.where(W_p > 1e-10)).fillna(0.0)
    R_b = (wb_r / W_b.where(W_b > 1e-10)).fillna(0.0)
    R_b_total = float((W_b * R_b).sum())

    allocation_effect  = (W_p - W_b) * (R_b - R_b_total)
    selection_effect   =  W_b        * (R_p - R_b)
    interaction_effect = (W_p - W_b) * (R_p - R_b)
    total_effect       =  W_p * R_p  -  W_b * R_b
```

### 5.1 向量化而非 groupby.apply

预计算 `w_p_r = w_p × r` 和 `w_b_r = w_b × r`，再 groupby.sum()，比 `groupby.apply` 快 5-10 倍，且兼容 pandas 2.0。

### 5.2 `R_b_total` 的计算

```python
R_b_total = float((W_b * R_b).sum())
```

基准总收益 = 各行业权重 × 行业内收益的加权求和。这是 BHB 公式中配置效应的参考点。

### 5.3 F8-002 的影响：不归一化时 ΔW 不为零

正常情况下，`Σ(W_p_i - W_b_i) = Σ W_p_i - Σ W_b_i = 1 - 1 = 0`，配置效应之和为 0。

但当策略权重和 < 1（有现金仓位）时，`Σ W_p_i < 1`，`Σ(W_p_i - W_b_i) = -cash_weight`，配置效应之和不为零，体现了"持有现金 = 低配全部行业"的隐含决策。这才是真实的归因，不应归一化掩盖。

---

## 六、两个恒等式验证（第 474-492 行）

```python
# V1: strategy_return - benchmark_return ≈ total_effect（汇总层）
v1_discrepancy = (
    (period_summary["strategy_return"] - period_summary["benchmark_return"])
    - period_summary["total_effect"]
).abs().max()

# V2: alloc + select + interact ≈ total_effect（BHB 可加性）
v2_discrepancy = (
    period_summary[["allocation_effect", "selection_effect", "interaction_effect"]].sum(axis=1)
    - period_summary["total_effect"]
).abs().max()

if v1_discrepancy > 1e-8 or v2_discrepancy > 1e-8:
    log.warning("BHB 验证失败: ...")
```

**V1 验证**：归因的总超额 = 实际超额，数学上必然成立（由 total_effect 的定义保证）。若失败，说明收益计算或权重处理有 bug。

**V2 验证**：BHB 三效应之和 = 总超额。注意代码注释说"汇总层成立，单行业不成立"：  
`Σ_i (alloc_i + select_i + interact_i) = Σ_i total_i`  
单行业差值 = `ΔW_i × R_b_total`，该项加总后因 `Σ ΔW_i = 0`（或接近 0）而消失。

---

## 七、按时间段汇总（第 503-552 行）

```python
def summarize_by_segment(period_summary, segments=None) -> pd.DataFrame:
    if segments is None:
        groups = period_summary.groupby(period_summary.index.year)
        # 按年汇总
    else:
        # 按自定义时间段汇总
```

Brinson 模型假设算术收益可加（线性），跨期加总是标准做法。若需几何链接（更精确），在 notebook 中另行处理。

`win_rate = (excess_return > 0).mean()`：超额正收益的月份比例，不是简单收益正的月份。

---

## 八、归因输出结构

**`industry_attr`**：MultiIndex `(period_start, industry_code)`

| 列 | 含义 |
|----|------|
| W_p, W_b | 策略/基准行业权重 |
| R_p, R_b | 策略/基准行业内收益 |
| allocation_effect | 配置效应 |
| selection_effect | 选股效应 |
| interaction_effect | 交叉效应 |
| total_effect | 总超额 |
| industry_name | 申万行业名称 |
| is_financial | 是否金融板块 |

**`period_summary`**：index = `period_start`（月末调仓日）

| 列 | 含义 |
|----|------|
| strategy_return | 股票部分净值贡献（不含现金） |
| benchmark_return | 基准收益 |
| excess_return | 超额 = 策略 - 基准 |
| cash_weight | 现金仓位（F8-002） |
| weight_sum | 股票权重和（F8-002） |

---

## 九、归因结果的解读

- **选股效应 > 0，配置效应 ≈ 0**：策略超额来自行业内选股，行业暴露与基准接近（指数增强的理想状态）
- **配置效应 > 0，选股效应 ≈ 0**：策略超额来自行业轮动，不是单股选择
- **交叉效应显著**：超配的行业里选股更好（或相反），配置和选股决策相互强化
- `is_financial=True` 的行业（银行/证券/保险）需要特别关注，因为财务因子在金融股上失真
