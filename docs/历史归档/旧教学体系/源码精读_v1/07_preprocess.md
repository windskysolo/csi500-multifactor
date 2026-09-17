# 07 — 因子横截面预处理流水线 `src/factors/preprocess.py`

---

## 一、文件定位

```
流程位置：第 3 步（构建因子面板）的最后一环，衔接原始因子值与 IC 检验/信号合成
读取：无（接受调用方传入的 pd.Series，不自己读数据）
写入：无（返回处理后的 pd.Series）
被谁调用：scripts/build_factor_panels.py（在每个调仓日对每个因子调用一次）
调用谁：statsmodels（OLS）、src/config.py
```

**5 步流水线概览：**

```
Step 1  金融股过滤      → 银行/非银金融的财务因子置 NaN（量价因子跳过）
Step 2  MAD 去极值      → 截断横截面极端值
Step 3  Z-score 标准化  → 转化为均值0、标准差1
Step 4  行业+市值中性化 → OLS回归取残差，去掉行业和市值效应
Step 5  残差再标准化    → 再做一次 Z-score，保证最终因子服从标准正态
```

**最重要的不变式（全文档通用）：** NaN 在 5 步中保持 NaN，本模块不做任何填充。只有在组合优化器入口处才统一将 NaN 填充为 0（横截面均值），与预处理解耦。

---

## 二、模块顶部

```python
import statsmodels.formula.api as smf      # OLS 中性化
from src import config as cfg              # cfg.FINANCIAL_SECTOR_CODES / cfg.MIN_NEUTRALIZE_STOCKS

_MAD_CONSISTENCY_FACTOR: float = 1.4826   # 正态分布一致性系数
```

其他参数（金融板块代码、最少中性化股票数）已移至 `config.py`，通过 `cfg` 引用，保证单一来源。

---

## 三、核心函数逐一解析

### 3.1 `winsorize_mad(series, n=3.0)` — MAD 去极值（L46-88）

```python
def winsorize_mad(series: pd.Series, n: float = 3.0) -> pd.Series:
```

**用途**：截断横截面极端因子值，防止少数极端股票扭曲整个分布。

**MAD 的数学定义：**

```
median    = 中位数（排名中间的值）
MAD       = median(|x - median|)  所有值与中位数偏差的绝对值的中位数
bound     = median ± n × 1.4826 × MAD
截断规则  = clip(下界, 上界)
```

**为什么用 MAD 而不是 3σ（均值 ± 3倍标准差）？**

均值和标准差对极端值非常敏感：一只股票 EP = 100（其余都在 0-0.2 范围），这一个极端值会把均值拉高、标准差放大，导致 3σ 边界也跟着偏移，无法有效截断。

中位数和 MAD 是"鲁棒统计量"——不受极端值影响。即使 10% 的样本是极端值，中位数和 MAD 也几乎不变。

**`1.4826` 常数的来源（L34 注释）：**

对于正态分布，`MAD = 0.6745 × σ`，因此 `σ ≈ 1/0.6745 × MAD = 1.4826 × MAD`。乘以 1.4826 让 MAD 成为正态分布标准差的"一致估计量"（consistent estimator）。这样 `n × 1.4826 × MAD` 在正态分布假设下就等同于 `n × σ`。

**正常执行路径（L70-88）：**

```python
valid = series.dropna()                      # 只用非 NaN 值计算
median = valid.median()
mad = (valid - median).abs().median()

bound = n * _MAD_CONSISTENCY_FACTOR * mad    # = 3 × 1.4826 × MAD
lower = median - bound
upper = median + bound
return series.clip(lower=lower, upper=upper)  # NaN 保持 NaN
```

**退化处理：MAD = 0（L73-83）：**

如果超过一半的样本值完全相同，则 MAD = 0（中位数两侧无偏差）。此时退化为 3σ 截断：

```python
if mad == 0:
    std = valid.std(ddof=1)
    if std < 1e-10:
        return series.copy()    # 所有值几乎相同，无需截断
    bound = 3.0 * std           # 退化为 3σ
```

**关键约束（对应 CLAUDE.md §4.2 的高频犯错点）：**

MAD 的 `median` 和 `mad` 只用 **T 日横截面**的值（`series` 就是 T 日截面），绝对不能用历史全样本。如果用全样本，2020 年的分布特征会"泄漏"到 2016 年的处理中，引入前视偏差。

---

### 3.2 `standardize(series)` — Z-score 标准化（L95-123）

```python
def standardize(series: pd.Series) -> pd.Series:
```

**公式：**

```
z = (x - mean) / std
mean、std 只用非 NaN 值计算
NaN 位置保持 NaN
```

**两种退化情况（L108-121）：**

```python
if len(valid) < 2:              # 有效样本不足2个
    return pd.Series(np.nan, ...)

if std < 1e-10:                 # std ≈ 0（所有值几乎相同）
    return pd.Series(np.nan, ...)
```

如果某个调仓日某个因子几乎所有股票的值都相同（例如某些成长因子在特殊时期），标准化失去意义，返回全 NaN。在评估报告中这会体现为该因子在该期 IC = NaN，不影响后续统计（IC 计算时 dropna）。

**`ddof=1`（样本标准差）：** 与财务统计惯例一致，用无偏的样本标准差。

---

### 3.3 `neutralize(factor, industry, log_mv, _diag)` — 行业+市值中性化（L130-190）

```python
def neutralize(
    factor:   pd.Series,     # 已去极值+标准化的因子值
    industry: pd.Series,     # 申万一级行业代码（字符串）
    log_mv:   pd.Series,     # log(自由流通市值) — daily_basic.log_free_float_mv
    _diag:    dict | None = None,   # 诊断信息收集（可选）
) -> pd.Series:
```

**目标：** 去掉因行业差异和市值差异带来的"伪因子信号"，让预处理后的因子值只反映在同行业、同市值规模内的相对优劣。

**OLS 回归模型（L182）：**

```python
model = smf.ols("y ~ C(industry) + log_mv", data=df).fit()
```

- `y`：当期已标准化的因子值（横截面）
- `C(industry)`：31 个行业的哑变量（`statsmodels` 自动处理，自动丢弃一个基准行业避免完全共线）
- `log_mv`：log(自由流通市值)（连续变量）

回归的含义：估计"行业效应"和"市值效应"对因子值的贡献，然后用残差（`resid`）表示排除这两个效应后的纯净信号。

**示例：** 假设某只银行股的 ROE = 15%（高于市场平均 12%），但银行行业平均 ROE = 14%，银行业高 ROE 是行业属性，不代表该股票特别优秀。中性化后，这只银行股的残差可能接近 0（只比银行同行高 1 个百分点），而不是市场截面意义上的高分。

**有效样本过滤（L156-163）：**

```python
# 关键：先建 valid_mask（基于原始 Series），再 astype(str)
valid_mask = factor.notna() & industry.notna() & log_mv.notna()
df = pd.DataFrame({
    "y":        factor[valid_mask],
    "industry": industry[valid_mask].astype(str),   # ← 转字符串在过滤后
    "log_mv":   log_mv[valid_mask],
})
```

**为什么不能先 `astype(str)` 再 `dropna()`（L156-158 注释）：**

```python
# 错误做法：
industry_str = industry.astype(str)     # NaN → 字符串 "nan"
industry_str.dropna()                   # "nan" 不是 NaN，dropna 无法识别
# 结果：缺失行业的股票带着 "nan" 哑变量进入 OLS，引入错误变量
```

正确做法：先在原始 Series 上建 `valid_mask`（NaN 仍然是真正的 NaN），再转字符串。这是代码中一个重要的正确性细节。

**`model.resid` 的 index 绑定（L184）：**

```python
resid = pd.Series(np.array(model.resid), index=df.index)
return resid.reindex(factor.index)
```

`statsmodels` 的 `model.resid` 是 numpy array（丢失了 index），需要显式绑定到 `df.index`（`ts_code`），然后 `reindex` 回原始 `factor.index`（包含 NaN 位置）。这样：
- 参与回归的股票：有残差值
- NaN 股票（不参与回归）：`reindex` 后自动填 NaN

**样本不足时跳过（L169-176）：**

```python
if len(df) < cfg.MIN_NEUTRALIZE_STOCKS:   # < 30 只
    return factor   # 直接返回原因子值，不做中性化
```

如果某调仓日有效股票数不足 30 只（极端情况），OLS 回归可能不稳定（参数多、样本少）。此时跳过中性化，直接返回 Step 3 标准化后的值。

**`_diag` 参数（诊断信息收集）：**

```python
if _diag is not None:
    _diag["n_neutralize_valid"] = len(df)
    _diag["skipped_neutralize"] = False / True
```

这是一个可选的诊断接口：如果调用方传入一个字典，函数会把中间状态信息写入其中，用于生成"预处理诊断报告"（`factor_panel_diagnostics.parquet`）。不传入时完全忽略。

---

### 3.4 `preprocess_factor(raw, industry, log_mv, fin_sector_codes, _diag)` — 完整流水线（L197-248）

```python
def preprocess_factor(
    raw:              pd.Series,                   # 原始因子值（T 日截面）
    industry:         pd.Series,                   # 行业代码（与 raw 共享 index）
    log_mv:           pd.Series,                   # log(自由流通市值)
    fin_sector_codes: Optional[frozenset[str]] = None,  # 财务因子传 FINANCIAL_SECTOR_CODES
    _diag:            dict | None = None,
) -> pd.Series:
```

这是本文件的核心公开接口，调用方（`build_factor_panels.py`）对每个因子在每个调仓日调用一次。

**5 步的代码对应：**

```python
processed = raw.copy()

# Step 1：金融股过滤（L221-230）
if fin_sector_codes:
    fin_mask = industry.isin(fin_sector_codes)
    processed[fin_mask] = np.nan              # 银行/非银金融 → NaN

# Step 2：MAD 去极值（L232-239）
processed = winsorize_mad(processed)          # 截断横截面极端值

# Step 3：Z-score 标准化（L241-242）
processed = standardize(processed)            # 均值0，std1

# Step 4：行业+市值中性化（L244-245）
residuals = neutralize(processed, industry, log_mv, _diag=_diag)

# Step 5：残差再标准化（L247-248）
return standardize(residuals)                 # 再次均值0，std1
```

**为什么需要 Step 5（残差再标准化）？**

OLS 残差理论上均值为 0（OLS 的一阶条件），但标准差不一定是 1。如果直接用残差做 IC 检验，不同因子的残差量纲不同，无法直接比较。再标准化后，所有因子都服从均值 0、std 1 的分布，可以直接做加权合成。

**`fin_sector_codes` 参数的设计意图：**

- 财务因子调用时：`fin_sector_codes=cfg.FINANCIAL_SECTOR_CODES`（银行/非银金融置 NaN）
- 量价因子调用时：`fin_sector_codes=None`（不过滤，金融股的量价因子有效）

这避免了在函数内部硬编码"哪些因子需要过滤金融股"，让调用方决定，更灵活。

---

## 四、内部辅助函数

本模块没有私有（下划线开头）的辅助函数，三个公开函数就是全部逻辑。

---

## 五、数据流图

```
build_factor_panels.py（外部调用者）
        │
        │  对每个调仓日 T，对每个因子 f：
        │
        ├─→ raw       = factor_f.compute(T, codes)       → pd.Series（原始值）
        ├─→ industry  = load_industry(T, T).loc[T]        → pd.Series（行业代码）
        └─→ log_mv    = load_daily_basic(T, T).loc[T]["log_free_float_mv"]

                        preprocess_factor(raw, industry, log_mv, fin_sector_codes)
                                │
                       Step 1: 金融股过滤（财务因子）
                                │
                       Step 2: winsorize_mad(processed)
                                │
                       Step 3: standardize(processed)
                                │
                       Step 4: neutralize(processed, industry, log_mv)
                                │
                       Step 5: standardize(residuals)
                                │
                               ↓
                        预处理后因子值（均值0，std1，已中性化）
                                │
                 汇总为 factor_panel_train.parquet / factor_panel_valid.parquet
```

---

## 六、量化领域知识补充

**为什么先去极值，再标准化，再中性化，而不是其他顺序？**

这个顺序是量化实践中的标准流程，逻辑如下：

1. **先去极值**：如果先标准化，极端值会拉大分母（std），使非极端值都被压缩到很小的范围，失去区分度。先去极值，让后续的 Z-score 基于更合理的分布。

2. **先标准化，后中性化**：OLS 回归（中性化）的残差在统计意义上是"在控制行业和市值效应后的因子偏差"。如果先做中性化（用原始值）、再标准化，残差的分布会受到原始值量纲的影响（ROE 是百分比，EP 是无量纲比率，两者量纲不同）。先标准化让所有因子都在同一量纲（std=1）上中性化，回归系数更有意义。

3. **最后再标准化**：中性化产生的残差 std 不一定是 1，再做一次标准化确保输出的因子值都在相同的分布下，可以直接加权合成。

**中性化中的"基准行业"问题：**

31 个行业哑变量放入线性回归时，会出现"完全共线"（31 个哑变量之和恒等于 1，与截距项线性相关）。`statsmodels` 的 `C(industry)` 自动处理这个问题：它会丢弃第一个行业（按字母序），以该行业作为基准类。回归结果中其他行业的系数是"相对于基准行业的差异"，但这不影响残差（残差不随基准行业的选择而改变）。

**"横截面"的含义：**

每一步的统计量（均值、标准差、中位数、MAD）都只用**同一调仓日**的所有股票来计算，不跨时间。这是最核心的约束：如果跨时间计算（如用过去 5 年的所有数据），2022 年的分布特征会影响 2016 年的处理，引入前视偏差。

**行业中性化后的因子含义变化：**

中性化之前：ROE 高的股票得高分（可能只是因为该股是金融股，金融行业 ROE 高）

中性化之后：ROE 高于**同行业同市值**平均水平的股票得高分（真正的"在同类中脱颖而出"）

这是量化因子"去伪存真"的核心操作：让因子捕捉的是个股的相对优势，而不是行业或规模效应。
