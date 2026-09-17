# 08 — 单因子 IC 检验框架 `src/evaluation/ic_analysis.py`

---

## 一、文件定位

```
流程位置：第 4 步（因子评价）的核心
读取：factor_panels（由 build_factor_panels.py 生成）、daily_quote.open_adj（计算 forward return）
写入：无（返回统计量 dict/DataFrame，由 run_factor_evaluation.py 写盘）
被谁调用：scripts/run_factor_evaluation.py、evaluation/quintile_backtest.py、evaluation/shift_test.py
调用谁：src/data/loader.py、src/data/universe.py、src/factors/price_factors.py（共用交易日序列）
```

本模块回答的核心问题：**"这个因子值高的股票，下个月收益率是不是真的更好？"**

提供的能力：
- `compute_rank_ic`：单期横截面 IC（因子与下期收益的排名相关）
- `compute_ic_series`：跨所有调仓日的 IC 时序
- `ic_summary`：IC_IR、t 统计量、p 值等评估统计量
- `compute_forward_returns`：T+1 开盘买入 → T'+1 开盘卖出的月度收益面板
- `batch_ic_test`：批量检验 + BH 多重检验校正
- `compute_factor_correlation`：因子间相关矩阵
- `filter_redundant_factors`：贪心去冗余

---

## 二、模块顶部

```python
from scipy import stats                          # t 分布 CDF（计算 p 值）
from statsmodels.stats.multitest import multipletests  # BH 多重检验校正

from src.factors.price_factors import _all_trading_dates  # 复用交易日序列缓存

# 有效因子判断阈值（直接引用 config，不硬编码）
IC_IR_THRESHOLD   = cfg.EVAL_IC_EFFECTIVE_CRITERIA["min_ic_ir"]   # 0.30
IC_MEAN_THRESHOLD = cfg.EVAL_IC_EFFECTIVE_CRITERIA["min_ic_mean"]  # 0.02
PVALUE_ALPHA      = cfg.EVAL_IC_EFFECTIVE_CRITERIA["max_p_bh"]     # 0.05
IC_POS_THRESHOLD  = cfg.EVAL_IC_EFFECTIVE_CRITERIA["min_dir"]      # 0.55
```

---

## 三、核心函数逐一解析

### 3.1 `compute_rank_ic(factor, fwd_ret)` — 单期 Rank IC（L56-72）

```python
def compute_rank_ic(factor: pd.Series, fwd_ret: pd.Series) -> float:
```

**公式：** Rank IC = Spearman(`factor`, `fwd_ret`)

**执行逻辑：**

```python
common = factor.dropna().index.intersection(fwd_ret.dropna().index)
if len(common) < 10:
    return np.nan          # 有效样本 < 10 只，无统计意义
return float(factor[common].corr(fwd_ret[common], method="spearman"))
```

**为什么用 Spearman（排名相关）而不是 Pearson（线性相关）？**

Pearson 相关系数假设两个变量之间是线性关系，并且对极端值非常敏感。因子值与收益率往往不是线性关系（例如 EP 因子分布高度偏斜），一两只极端值可能主导 Pearson 相关系数的结果。

Spearman 相关系数先把两个变量都换成排名，再计算排名之间的 Pearson 相关。排名变换天然截断了极端值的影响，对离群点更鲁棒。因此量化研究中几乎都用 Rank IC（Spearman），而不是 Pearson IC。

**取值范围与含义：**

| IC 值 | 含义 |
|-------|------|
| +1 | 因子排名与下期收益率排名完全一致（正向完美预测）|
| +0.05 | 较弱但有价值的正向预测（实践中 IC≥0.02 已有意义）|
| 0 | 无预测能力（随机）|
| -0.05 | 负向因子（低 IC 预测高收益，如低波动溢价）|
| -1 | 完全反向预测 |

**有效样本数阈值 < 10（L70）：** 样本数不足时，Spearman 相关系数的标准误非常大，估计完全不可靠，直接返回 NaN，不纳入 IC 时序。

---

### 3.2 `compute_ic_series(factor_panel, fwd_ret_panel)` — IC 时序（L75-93）

```python
def compute_ic_series(
    factor_panel:  pd.DataFrame,   # 行=调仓日, 列=ts_code
    fwd_ret_panel: pd.DataFrame,   # 行=调仓日, 列=ts_code
) -> pd.Series:
```

**执行逻辑：**

```python
dates = factor_panel.index.intersection(fwd_ret_panel.index)  # 两个面板的共同日期
ic_vals = [
    compute_rank_ic(factor_panel.loc[d], fwd_ret_panel.loc[d])
    for d in dates
]
return pd.Series(ic_vals, index=dates, name="ic").dropna()  # 过滤 NaN 期
```

对每个调仓日 T，用 T 日的因子横截面（因子值，预测变量）和 T 日对应的下期收益率横截面（标签）计算一个 IC 值，得到 IC 随时间的序列。

**`.dropna()` 的作用：** 某些调仓日可能因有效股票数 < 10 而返回 NaN，这些日期直接从 IC 序列中剔除，不参与后续的均值/标准差计算，避免影响统计量。

---

### 3.3 `ic_summary(ic_series)` — IC 统计量汇总（L96-146）

```python
def ic_summary(ic_series: pd.Series) -> dict:
```

**返回字段含义与计算方式：**

```python
n         = len(valid)                         # 有效 IC 观测数
ic_mean   = valid.mean()                       # IC 均值（预测能力）
ic_std    = valid.std(ddof=1)                  # IC 标准差（稳定性，越小越好）
ic_ir     = ic_mean / ic_std                   # IC_IR（类 Sharpe ratio）
se        = ic_std / sqrt(n)                   # IC 均值的标准误
t_stat    = ic_mean / se                       # t 统计量（= IC_IR × sqrt(n)）
p_value   = 2 * (1 - t.cdf(|t_stat|, df=n-1)) # 双尾 p 值（t 分布）
pct_positive = (valid > 0).mean()             # IC > 0 的月份比例
```

**IC_IR 的直觉（类比 Sharpe Ratio）：**

```
Sharpe = (年化收益 - 无风险收益) / 波动率
IC_IR  = IC均值 / IC标准差
```

两者的逻辑完全类似：IC_IR 衡量"平均预测能力"相对于"预测能力的波动性"。

- IC_IR = 0.3：意味着平均 IC 是 IC 序列波动性的 30%，信号相对稳定
- IC_IR = 0.1：平均 IC 只有波动性的 10%，时好时坏，不稳定
- IC_IR 的 t 统计量 = IC_IR × √n，所以 IC_IR=0.3 且 n=72 时，t≈2.55（p≈0.013，显著）

**`p_value` 的计算（L135）：**

```python
p_val = float(2 * (1 - stats.t.cdf(abs(t_stat), df=n - 1)))
```

- 使用 t 分布（而非正态分布），因为 n 有限（通常 60-72 个月），t 分布更保守（比正态分布尾部更厚）
- `df=n-1`：自由度 = 样本数 - 1
- 双尾检验：`2 * (1 - CDF(|t|))`，既检验 IC > 0 又检验 IC < 0

**退化保护（L114-130）：**

```python
if n < 2:
    return {"n": n, **_nan_dict}   # 样本不足，所有统计量为 NaN

if ic_std < 1e-12:
    # IC 序列几乎无波动（极罕见），IC_IR 无意义
    return {..., "ic_ir": np.nan, ...}
```

---

### 3.4 `build_exit_date_map(rebalance_dates)` — 卖出日映射（L153-182）

```python
def build_exit_date_map(rebalance_dates: list[pd.Timestamp]) -> pd.Series:
```

**用途：** 构建 T → T'+1（卖出执行日）的映射，用于样本切分时防止 forward return 跨越训练/测试边界。

**问题背景：** 2021 年最后一个调仓日（如 2021-12-31）的 forward return 计算用的是 2022 年初的开盘价（exit_date ≈ 2022-02-07）。如果按 label index（2021-12-31）来切分，这条数据会被纳入训练集，但它实际上包含了 2022 年的信息——隐性的数据泄露。

**解决方案：** 按 `exit_date` 切分而非 T，确保训练集中的每条 forward return 的卖出点都在 `TRAIN_END` 之前。

---

### 3.5 `compute_forward_returns(...)` — 月度前向收益面板（L189-295）

这是整个评价框架最基础的数据准备函数。

```python
def compute_forward_returns(
    rebalance_dates: list[pd.Timestamp],
    codes: list[str],
    codes_by_date: Optional[dict[pd.Timestamp, list[str]]] = None,
    benchmark_code: Optional[str] = None,
) -> pd.DataFrame:
```

**Forward Return 的精确定义（L14-18 模块注释）：**

```
fwd_ret[T, stock] = open_adj[T'+1] / open_adj[T+1] - 1

T    = 调仓日（月末，比如 2021-01-29）
T+1  = T 之后第一个交易日（买入点，比如 2021-02-01）
T'   = 下一个调仓日（比如 2021-02-26）
T'+1 = T' 之后第一个交易日（卖出点，比如 2021-03-01）
```

**为什么用开盘价而不是收盘价（L20 模块注释）：**

策略在 T 日收盘后生成权重（T 日收盘价是已知信息），但执行买入最早在 T+1 日开盘。用 T 日收盘价作为买入价会引入"隔夜价格"的前视偏差——实际上无法以 T 日收盘价成交（市场已经收盘）。T+1 开盘价是策略能真正成交的最早价格。

**一次性加载所有交易日数据（L249-253）：**

```python
# 批量确定所有需要的日期（entry 和 exit 日期的并集）
all_load_dates = sorted({d for _, e, x in entry_exit_pairs for d in (e, x)})
start_load = all_load_dates[0]
end_load   = all_load_dates[-1]
# 一次 I/O 加载覆盖所有日期的 open_adj
dq = load_daily_quote(start_load, end_load, codes=all_codes)
open_pivot = dq["open_adj"].unstack("ts_code")
```

如果对每个调仓日单独调用 `load_daily_quote`，60 个调仓日就要发起 60 次 I/O，非常慢。一次性确定所有需要的日期范围，只读一次磁盘，性能提升约 60 倍。

**`codes_by_date` 参数（动态成分股处理，L211-228）：**

```python
if codes_by_date is not None:
    all_codes = sorted(set().union(*codes_by_date.values()))
    # 取所有调仓日可投资股票的并集
```

中证 500 成分股会随时间变化（每年 6 月/12 月调整）。如果只用某一期的成分股列表，后期新进入的股票就没有 forward return 数据，IC 计算会漏掉这些股票。`codes_by_date` 传入每个调仓日的实际可投资股票，取并集加载数据，保证动态成分股也被覆盖。

**`benchmark_code`（超额收益计算）：**

```python
if active_benchmark is not None:
    fwd = fwd - bm_ret.get(T, 0.0)   # 个股收益 - 基准区间收益 = 超额收益
```

当传入 `benchmark_code="000905.SH"`（中证500全收益指数）时，forward return 变为"超额收益"（individual return - benchmark return）。IC 检验用超额收益更准确：我们想知道因子能不能选出"跑赢基准"的股票，而不仅仅是"绝对上涨"的股票。

---

### 3.6 `batch_ic_test(factor_panels, fwd_ret_panel, alpha)` — 批量 IC 检验（L302-368）

```python
def batch_ic_test(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    alpha: float = 0.05,
) -> pd.DataFrame:
```

**核心流程：**

1. 对每个因子调用 `compute_ic_series` + `ic_summary`，得到原始 p 值
2. BH 多重检验校正（仅对 p 值非 NaN 的因子）
3. 计算 `pct_consistent_dir`（方向一致性）
4. 综合评定 `effective`（四个条件全部满足）

**BH 多重检验校正（L344-349）：**

```python
_, p_corrected, _, _ = multipletests(p_raw, method="fdr_bh", alpha=alpha)
result.loc[valid_mask, "p_value_bh"]    = p_corrected
result.loc[valid_mask, "significant_bh"] = p_corrected < alpha
```

**为什么需要多重检验校正？**

如果同时检验 27 个因子，每个都用 p < 0.05 作为显著性标准，即使所有因子都是无效的随机因子，平均也会有 27 × 0.05 = 1.35 个因子"显著"（纯属偶然）。

BH（Benjamini-Hochberg）方法控制 FDR（False Discovery Rate，假阳性率）：在所有"显著"的因子中，假阳性比例 ≤ α（5%）。

BH 步骤：
1. 把所有因子的 p 值从小到大排序：p_(1) ≤ p_(2) ≤ ... ≤ p_(m)
2. 找到最大的 k，使得 p_(k) ≤ k/m × α
3. 把排名 1 到 k 的因子都判定为显著

这比 Bonferroni 校正（p < α/m = 0.05/27 ≈ 0.0019）更宽松，适合因子筛选场景（我们接受一定比例的假阳性，但要控制总体错误率）。

**`pct_consistent_dir`（方向一致性）的设计（L354-356）：**

```python
result["pct_consistent_dir"] = result["pct_positive"].apply(
    lambda p: max(p, 1.0 - p) if pd.notna(p) else np.nan
)
```

**为什么不直接用 `pct_positive ≥ 0.55`？**

负向因子（如 `vol_60d`：低波动率预期高收益）的 IC 稳定为负，因此 `pct_positive`（IC > 0 的比例）很低（如 0.15）。如果用 `pct_positive ≥ 0.55`，这个有效的负向因子会被误判为无效。

`pct_consistent_dir = max(pct_positive, 1 - pct_positive)` 取两者的较大值：
- 正向因子（IC 多数为正）：`pct_consistent_dir ≈ pct_positive`
- 负向因子（IC 多数为负）：`pct_consistent_dir ≈ 1 - pct_positive`

两类因子都能通过 `≥ 0.55` 的阈值。

**综合评定 `effective`（L359-364）：**

```python
result["effective"] = (
    (result["ic_ir"].abs()           >= 0.30)   # IC_IR
    & (result["ic_mean"].abs()       >= 0.02)   # IC 均值
    & result["significant_bh"]                   # BH 校正后显著
    & (result["pct_consistent_dir"] >= 0.55)    # 方向一致性
)
```

四个条件必须全部满足。注意每个比较表达式都需要括号，因为 Python 中 `&` 优先级高于 `>=`，不加括号会报错或得到错误结果。

---

### 3.7 `compute_factor_correlation(factor_panels, min_stocks)` — 因子相关矩阵（L375-426）

```python
def compute_factor_correlation(
    factor_panels: dict[str, pd.DataFrame],
    min_stocks: int = 20,
) -> pd.DataFrame:
```

**方法：** 对每个调仓日 T 独立计算各因子的横截面 Spearman 相关矩阵，再在时间轴上取均值。

```python
for T in dates:
    cross = pd.DataFrame({name: factor_panels[name].loc[T] for name in factor_names})
    cross = cross.dropna(how="all")
    if len(cross) < min_stocks:
        continue
    corr_mat = cross.corr(method="spearman")    # 单期相关矩阵
    corr_sum += corr_mat.fillna(0.0)
    count    += valid_mask.astype(int)

avg_corr = corr_sum / count   # 时间均值
```

**为什么每对因子独立计数（L420-421）：**

```python
avg_corr = corr_sum.where(count > 0, np.nan) / count.where(count > 0, np.nan)
```

不同因子对可能在不同的调仓日有缺失。如果用一个全局的 count（所有日期数），某些配对的平均值会被拉偏（分母比有效期数大）。每对因子独立记录有效期数，确保平均准确。

---

### 3.8 `filter_redundant_factors(ic_result, corr_matrix, corr_threshold)` — 贪心去冗余（L433-486）

```python
def filter_redundant_factors(
    ic_result: pd.DataFrame,
    corr_matrix: pd.DataFrame,
    corr_threshold: float = 0.70,
) -> list[str]:
```

**贪心算法逻辑：**

```
candidates = 按 |IC_IR| 降序排列的"有效"因子列表

selected = []
for factor in candidates:
    if selected 为空:
        selected.append(factor)
        continue
    max_corr = max(|corr(factor, s)| for s in selected)
    if max_corr <= 0.70:
        selected.append(factor)   # 与已选因子相关性低，纳入
    else:
        丢弃（已有相关性更高且 IC_IR 更强的因子覆盖了这个信号）
```

**为什么用贪心而不是精确优化？**

精确优化（如在所有因子子集中找到相关性最低且 IC_IR 最高的组合）是 NP 难问题（组合爆炸）。贪心算法给出"先选最强的，再选与已选不相关的"这个近似解，在实践中效果接近最优，计算复杂度是 O(n²)。

**为什么相关性阈值是 0.70（来自 `cfg.EVAL_HIGH_CORR_THRESHOLD`）？**

两个因子相关系数 >0.70，说明它们共享约 49%（0.7² ≈ 0.49）的信息（R² ≈ 49%），冗余程度高。在 IC_IR 加权合成时，两个高相关因子会"重复计数"同一风险敞口，使该维度的权重被双倍放大，实际上是变相的集中风险。

---

## 四、数据流图

```
factor_panels（factor_panel_train.parquet 切出的各因子面板）
        │
        └─→ compute_ic_series(factor_panel, fwd_ret_panel)
                    │
                    └─→ compute_rank_ic(factor[T], fwd_ret[T])  （每期）
                                │ Spearman 相关
                                ↓
                          IC 时序 pd.Series
                                │
                         ic_summary()
                                │
                    ┌───────────┴──────────────┐
                    ↓                          ↓
              原始 p_value              pct_consistent_dir
                    │
        multipletests(method="fdr_bh")
                    │
              p_value_bh / significant_bh
                    │
               batch_ic_test() 输出 DataFrame（每行一个因子）
                    │
                    ├─→ effective=True 的因子 → filter_redundant_factors()
                    │                                   │
                    │                            去冗余后的最终因子列表
                    │                            → final_factors.json
                    │
                    └─→ compute_factor_correlation() → factor_correlation.csv

daily_quote.open_adj
        │
        └─→ compute_forward_returns()
                    │
              fwd_ret_panel（行=调仓日, 列=ts_code）
```

---

## 五、领域知识补充

**IC vs IR 的区别：**

- **IC（Information Coefficient）**：单期横截面中因子的预测能力，一个时间点的值
- **IC_IR（Information Ratio）**：IC 序列的均值/标准差，衡量跨时间的稳定性

一个好因子需要两者兼备：IC 均值大（平均预测能力强）且 IC_IR 高（不同时间段都稳定）。只有 IC 均值大但 IC_IR 低的因子，意味着它在某些时期非常有效，在另一些时期完全失效，实用性差。

**IC 随样本的统计显著性：**

IC_IR 的 t 统计量 = IC_IR × √n（在 IC 序列独立的假设下）。

| IC_IR | n=36（3年）| n=72（6年）|
|-------|-----------|-----------|
| 0.20 | t=1.20（不显著）| t=1.70（边界）|
| 0.30 | t=1.80（边界）| t=2.55（显著）|
| 0.50 | t=3.00（显著）| t=4.24（高度显著）|

这说明：
1. IC_IR ≥ 0.30 的准入标准需要至少 3-6 年的训练期数据才能被统计确认
2. 短暂测试（如 1 年数据）的因子筛选结果非常不可靠

**Spearman 相关系数的秩变换：**

Spearman 相关 = 先将原始值换成排名，再计算 Pearson 相关。排名变换的效果等价于：
1. 截断极端值（排名在 1-n 之间，不会有"市值是平均值 100 倍"的极端情况）
2. 非线性单调变换不影响结果（因子值的单调函数不改变排名，因此 IC 对非线性变换不敏感）
