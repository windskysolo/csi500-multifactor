# 11 — quintile_backtest.py：5 分组等权回测

> 对应源文件：`src/evaluation/quintile_backtest.py`
> 实际行数：**253 行**（计划快照为 187，新增 `direction` 参数、`DirectionalLS`、`batch_quintile_summary`，撰写前已核实）
> 处理方式：**沿用 + 核查更新**（行数从 187→253，新增内容需补充）
> 所属 Part：Part 3 — 单因子评估层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 2  因子评估    ← 本文件（quintile_backtest.py）
Layer 2  因子评估    ic_analysis.py（fwd_ret 复用）
Layer 1  因子构建    src/factors/
Layer 0  数据基础    src/data/
```

`quintile_backtest.py` 提供**5 分组收益分析**，是 IC 检验的互补工具：
- IC 检验衡量**排名相关性**（线性预测能力）
- 分组回测衡量**分位数单调性**（实际选股有效性）

两者一起使用可以区分"因子有 IC 但分组单调性差（噪声多）"和"因子 IC 中等但组间收益分离清晰"的不同情况。

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游（数据）| `ic_analysis.compute_forward_returns` | 分组回测需要与 IC 检验相同的 fwd_ret |
| 上游（可选）| `ic_analysis.compute_ic_series` | `batch_quintile_summary` 不提供 `direction` 时用 IC 均值推断方向 |
| 下游 | `scripts/run_factor_evaluation.py` | 调用 `batch_quintile_summary` |

### 与 v1 的主要差异

| 变化 | 说明 |
|------|------|
| 新增 `direction` 参数 | `run_quintile_backtest` 支持方向调整，正确处理负向因子 |
| 新增 `DirectionalLS` 列 | Q5-Q1 多空组合按方向翻转，使"高 IC 因子"的多空始终为正 |
| 新增 `directional_sharpe_ls` | 方向调整后的多空 Sharpe（用于与正向因子比较） |
| 新增 `batch_quintile_summary` | 批量汇总接口，对多个因子返回汇总 DataFrame |

---

## 二、时间对齐与 PIT 假设

分组回测使用与 IC 检验**完全相同**的 `fwd_ret_panel`（`compute_forward_returns` 输出），因此继承相同的时间对齐约定：
- Forward Return = T+1 开盘买入、T'+1 开盘卖出
- 最后一个调仓日无 T'，`fwd_ret` 全为 NaN，不参与分组

分组本身（`pd.qcut`）只依赖截面数据，无跨期计算，无 PIT 问题。

---

## 三、模块顶部：导入与常量

```python
# L30–32
log = logging.getLogger(__name__)

MIN_STOCKS_PER_GROUP = 5   # 每组最少股票数，低于此置 NaN
DIRECTIONAL_LS_LABEL = "DirectionalLS"
```

`MIN_STOCKS_PER_GROUP = 5` 是保守下限。每组少于 5 只股票时，组合收益的统计意义极低（任意 1 只异常股票能主导全组）。

---

## 四、核心函数逐一解析

### 4.1 `compute_quintile_returns` — 单截面分组收益

```python
# L77–113
def compute_quintile_returns(
    factor: pd.Series,
    fwd_ret: pd.Series,
    n_groups: int = 5,
) -> pd.Series:
```

**执行逻辑**：
1. 调用 `_assign_quintiles` 按因子值等频分为 5 组（Q1 最低，Q5 最高）
2. 取 labels 和 fwd_ret 均有效的股票交集
3. 按组计算等权平均 forward return
4. 每组实际样本数 < `MIN_STOCKS_PER_GROUP` 时置 NaN

**`pd.qcut` 的细节**（`_assign_quintiles` 中，L64–66）：
```python
labels = pd.qcut(valid, q=n_groups, labels=False, duplicates="drop") + 1
```

`duplicates="drop"` 处理大量相同值（如 PE 值大量为 0 或相同整数）。若重复值导致实际分组数 < n_groups，整期返回空 Series（L70–72）。

**交集后的逐组检查**（L104–110）：注意总量达标不代表每组都达标。极端情况下（如某因子某期覆盖率很低），组内与 fwd_ret 取交集后可能某组只剩 1-2 只股票，这时该组置 NaN。

---

### 4.2 `run_quintile_backtest` — 全周期分组回测

```python
# L116–201
def run_quintile_backtest(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    n_groups: int = 5,
    direction: Optional[float] = None,   # 新增：因子方向
) -> dict:
```

**`direction` 参数的作用**（L141）：
```python
factor_direction = _normalize_direction(direction)   # 归一化为 +1/-1/NaN
```

方向的用途：
- `direction = +1`（正向因子）：Q5 是高分组，高分组预期收益高，Q5-Q1 > 0 是好信号
- `direction = -1`（负向因子，如 `vol_60d`）：Q5 是高波动组，**Q1** 才是"好"组，应翻转

**`DirectionalLS` 列的计算**（L151–153）：
```python
if np.isfinite(factor_direction):
    row[DIRECTIONAL_LS_LABEL] = factor_direction * row[ls_label]
```

`DirectionalLS = direction × (Q5-Q1)`：
- 正向因子：= Q5-Q1（不变）
- 负向因子：= Q1-Q5（翻转）→ 使"正确方向的多空"始终为正收益

**返回字段汇总**：

| 字段 | 含义 | 类型 |
|------|------|------|
| `group_rets` | 每期分组收益 DataFrame | DataFrame（行=日期，列=Q1~Q5/Q5-Q1/DirectionalLS）|
| `mean_rets` | 各组期均收益 | Series |
| `ann_rets` | 各组年化收益（× 12，简单近似）| Series |
| `cum_rets` | 累计收益（以 1 为基准）| DataFrame |
| `sharpe_ls` | Q5-Q1 年化 Sharpe（rf=0）| float |
| `directional_sharpe_ls` | 方向调整后多空 Sharpe | float |
| `factor_direction` | 实际使用的方向（归一化后）| float |
| `n_periods` | 有效期数 | int |

**注意**：`ann_rets = mean_rets × 12` 是**简单近似**，假设月频不复利。正确的年化应使用复利公式 `(1+mean)^12 - 1`，但对于量级 < 1% 的月均收益差异不大。

---

### 4.3 `batch_quintile_summary` — 批量分组回测汇总

```python
# L204–252
def batch_quintile_summary(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    n_groups: int = 5,
    factor_directions: Optional[dict[str, float] | pd.Series] = None,
) -> pd.DataFrame:
```

**方向推断逻辑**（`_direction_for` 内部函数，L220–231）：
- 若提供 `factor_directions`：从字典或 Series 查找
- 否则：实时计算 IC 时序，用 `IC_mean` 的符号作为方向

后者是"懒加载"但正确性较弱的方案：IC 均值符号仅反映训练集整体方向，可能掩盖样本内/外方向不一致的问题。**推荐**在调用前显式计算并传入 `factor_directions`。

**输出**：按 `directional_sharpe_ls` 降序排列的汇总 DataFrame，包含：
`n_periods / factor_direction / q1_ann_ret / q5_ann_ret / ls_ann_ret / directional_ls_ann_ret / sharpe_ls / directional_sharpe_ls`

---

## 五、内部辅助函数

| 函数 | 位置 | 说明 |
|------|------|------|
| `_normalize_direction(direction)` | L34–39 | 将任意输入规范化为 +1/-1/NaN；`0.0` 也返回 NaN |
| `_annualized_sharpe(ret)` | L42–47 | 年化 Sharpe：月均收益 / 月标准差 × √12，rf=0 |
| `_assign_quintiles(factor, n_groups)` | L50–74 | 等频分组，处理重复值，不足时返回空 Series |

---

## 六、落盘产物（Artifacts）

`quintile_backtest.py` **不直接写文件**，所有产物由调用方（`run_factor_evaluation.py`）写入。

典型产物（由上层脚本写入）：
- `reports/factor_evaluation/quintile_summary.csv`：批量分组回测汇总
- `reports/factor_evaluation/quintile_charts/`：累计收益图（若上层脚本生成）

---

## 七、相关测试

**当前状态**：`tests/test_evaluation.py` 中**没有 `quintile_backtest.py` 的专项测试**（TODO）。

**高优先级待补充测试**：

| 测试场景 | 要点 |
|---------|------|
| 单调性验证 | 构造完美单调因子（Q1 < Q2 < ... < Q5），验证 `sharpe_ls > 0` |
| 负向因子方向翻转 | `direction=-1` 时 `DirectionalLS = Q1-Q5`（而非 Q5-Q1） |
| 每组 < 5 只股票 | 该组 forward return 应为 NaN |
| 重复值过多分组失败 | `qcut` 失败时返回空 Series，`run_quintile_backtest` 返回空结果 |
| 最后一期 fwd_ret 全 NaN | 应自动被过滤，不影响其他期计算 |

---

## 八、失败与降级路径

| 失败场景 | 行为 |
|---------|------|
| 全截面因子值相同（无法分组）| `_assign_quintiles` 返回空 Series，该期跳过 |
| 某组 < 5 只股票 | 该组收益置 NaN，其他组正常计算 |
| 所有期无有效分组 | 返回空 DataFrame + `n_periods=0`，记录 warning |
| 因子与 fwd_ret 无有效交集 | `group_rets` 为空，所有指标为 NaN 或 0 |

---

## 九、数据流图

```
factor_panel (date × stock)                fwd_ret_panel (date × stock)
    │                                              │
    └────────────────────┬─────────────────────────┘
                         │（按日期取交集）
                         ▼
  for each date T:
    _assign_quintiles(factor[T])  →  labels (stock → Q1~Q5)
         │
         ▼
    compute_quintile_returns(factor[T], fwd_ret[T])
      → group_ret (Q1~Q5 均值)
         │
         ▼
    [若有 direction] row[DirectionalLS] = direction × row[Q5-Q1]
         │
         ▼
  group_rets DataFrame (date × {Q1,Q2,Q3,Q4,Q5,Q5-Q1,DirectionalLS})
         │
    ┌────┴─────────┬──────────────────────┐
    ▼              ▼                      ▼
  mean_rets    cum_rets = (1+group_rets).cumprod()   sharpe_ls / directional_sharpe_ls
```

---

## 十、领域知识补充

### 分组回测的局限性

1. **不含交易成本**：分组回测的 Q5-Q1 多空是理论值，实际执行需扣除佣金、滑点和印花税。
2. **等权假设**：每组内等权持有，忽略了市值约束（A 股小市值股票流动性差）。
3. **分位数不等价于实际组合**：Q5 中可能包含 ST 股、次新股，实际策略有更严格的筛选。
4. **历史数据偏差**：使用存活的股票，退市股票被排除，可能高估历史收益。

### 单调性与 IC 的互补

```
IC 高 + 分组单调性好   → 因子对整个收益分布都有预测力，最理想
IC 高 + 分组单调性差   → 因子值只对头尾有区分力，中间组噪声大（可能线性预测力强但非线性关系）
IC 低 + 分组单调性好   → 因子有区分力但绝对幅度小，IC_IR 统计显著性低
IC 低 + 分组单调性差   → 因子无效
```

Gate 2（单调性检验）正是用 `batch_quintile_summary` 的结果来区分前两种情况。
