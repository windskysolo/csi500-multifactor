# 08 — 因子横截面预处理流水线 `src/factors/preprocess.py`

---

## 一、文件定位

```
所属 Part  : Layer 1（因子构建层）
在数据流中 : financial/price/alt_factors → preprocess → combiner/ridge
被谁调用   : scripts/build_factor_panels.py（批量构建面板时逐因子调用）
调用谁     : src/config.py（FINANCIAL_SECTOR_CODES / MIN_NEUTRALIZE_STOCKS）
             statsmodels.formula.api（OLS 中性化）
```

`preprocess.py` 对每个因子在每个调仓日的横截面执行 5 步标准化流水线，消除量纲差异，剥离行业和市值暴露，使因子值可以直接比较和合成。

> ⚠️ **与 v1 的重大差异**：v1 的 `preprocess.py` 有约 248 行，以 `preprocess_pipeline(factor_df, date, ...)` 为主入口，内部辅助函数全为 private（`_mad_winsorize`、`_zscore_standardize`、`_neutralize`）。**v2 重构为 166 行**，将三个步骤提升为**公开函数**（`winsorize_mad`、`standardize`、`neutralize`），可以被独立测试和调用。主入口改为 `preprocess_factor(raw, industry, log_mv, fin_sector_codes, _diag)`，接受横截面 Series 而非 DataFrame。

---

## 二、时间对齐与 PIT 假设

**最重要的约束**：去极值边界和标准化统计量**只用 T 日横截面**的非 NaN 值计算，**禁止用历史全样本**。

这是量化因子预处理中最高频的犯错点。如果用全样本计算 MAD 边界（相当于"未来"样本影响了"过去"时点的因子值），会引入前视偏差。代码中通过函数签名强制：`winsorize_mad(series)` 只接收单期横截面 Series，不接收历史面板 DataFrame，从设计上避免误用。

中性化的 OLS 回归也只用 T 日横截面（`factor ~ C(industry) + log_mv` 用当期数据拟合），残差只来自当期横截面，无历史信息泄露。

---

## 三、模块顶部

```python
import statsmodels.formula.api as smf
from src import config as cfg

_MAD_CONSISTENCY_FACTOR: float = 1.4826
# 使 MAD 成为正态分布标准差的一致估计（sigma ≈ 1.4826 × MAD）
# 来源：正态分布下 E[|X - median(X)|] = sigma × (2/π)^(1/2)，
# 修正因子 1.4826 = 1 / (2 × Φ^{-1}(0.75)) ≈ 1/(2 × 0.6745)
```

注释中已说明 `_MAD_CONSISTENCY_FACTOR` 的数学来源，不需要查阅外部文献。

---

## 四、核心函数逐一解析

### 4.1 `winsorize_mad(series, n=3.0)` — MAD 去极值（L46-88）

```python
median = valid.median()
mad    = (valid - median).abs().median()

if mad == 0:
    # MAD=0 退化处理：超过 50% 的值完全相同时 MAD 为 0
    std = valid.std(ddof=1)
    if std < 1e-10:
        return series.copy()   # 所有值几乎完全相同，无需截断
    bound = 3.0 * std          # 退化为 3σ 截断
    log.warning(...)           # 记录退化发生
else:
    bound = n * _MAD_CONSISTENCY_FACTOR * mad   # = 3 × 1.4826 × MAD

lower = median - bound
upper = median + bound
return series.clip(lower=lower, upper=upper)
```

**为何用 MAD 而不是均值 ± 3σ（3 倍标准差）？**

均值和标准差对极端值高度敏感：一个极端值就能大幅拉偏均值，进而影响边界。A 股因子中极端值极常见（如 PE 极高的"僵尸股"），如果边界本身被极端值拉偏，就会截断不该截断的正常值。中位数对极端值完全不敏感（鲁棒），MAD 是基于中位数的散布度量，因此也鲁棒。

**MAD=0 退化逻辑**（v2 新增）：当中证500某行业整体因子值完全相同时（如某季度所有银行股 ROE 来自同一份分析师报告），MAD=0。此时改用 3σ 截断，并记录警告。这个退化路径在 v1 中不存在，可能导致零除错误。

**NaN 处理**：`valid = series.dropna()` 先提取非 NaN，边界基于非 NaN 计算。NaN 位置不参与计算，`clip()` 不改变 NaN 位置。

**注意**：函数签名有 `n: float = 3.0`（截断系数），但项目中一律使用默认 3.0，不建议修改（该值是实践惯例，来自于"正态分布 3σ 内覆盖 99.7%"的经验）。

---

### 4.2 `standardize(series)` — Z-score 标准化（L95-123）

```python
mu  = valid.mean()
std = valid.std(ddof=1)

if std < 1e-10:
    return pd.Series(np.nan, index=series.index)   # 全部相同，无法标准化

return (series - mu) / std
```

**NaN 保留**：`(series - mu) / std` 中 NaN 参与运算结果仍为 NaN，自动保留。

**`ddof=1`（样本标准差）**：横截面通常约 400-480 只股票（可投资域），远大于 2，使用 `ddof=1`（无偏）和 `ddof=0` 差异极小，但从统计严谨性角度使用 `ddof=1`。

**非 NaN 样本 < 2 的处理**：直接返回全 NaN（L108-113），不抛异常。这种情形极罕见，但发生时强行标准化会产生 0/0 或无意义的结果。

---

### 4.3 `neutralize(factor, industry, log_mv, _diag=None)` — 行业+市值中性化（L130-190）

```python
valid_mask = factor.notna() & industry.notna() & log_mv.notna()
df = pd.DataFrame({
    "y":        factor[valid_mask],
    "industry": industry[valid_mask].astype(str),   # 注意 astype 在 valid_mask 后
    "log_mv":   log_mv[valid_mask],
})
```

**`astype(str)` 在 `valid_mask` 之后的关键性**（L156-158，有行内注释）：

如果先 `astype(str)` 再 `dropna()`，则 `NaN → "nan"` 字符串，后续 `dropna()` 无法识别。结果是"缺少 industry 的股票"带着 `"nan"` 哑变量进入 OLS，违反"任一输入为 NaN 则输出 NaN"的契约。正确顺序：先用原始 Series 计算 `valid_mask`（检测到 NaN），再对子集做 `astype(str)`。

```python
model = smf.ols("y ~ C(industry) + log_mv", data=df).fit()
resid = pd.Series(np.array(model.resid), index=df.index)
return resid.reindex(factor.index)   # 未参与回归的股票返回 NaN
```

**回归设计**：
- `C(industry)` = 31 个申万一级行业哑变量（statsmodels 的 patsy 语法自动处理基准类别）
- `log_mv` = log(自由流通市值)（已由 `daily_basic.log_free_float_mv` 提供，无需重算）
- 不加截距项：`C(industry)` 哑变量已充当截距（31 个行业 dummy 覆盖了常数项的自由度）

**样本不足退化**（L169-176）：有效股票 < `cfg.MIN_NEUTRALIZE_STOCKS=30` 时，跳过中性化，直接返回原因子值，并记录警告。不抛异常，确保流水线不中断。

**`_diag` 诊断字典**（v2 新增）：可选参数，传入时记录各步骤的统计信息（如实际有效样本数、是否跳过中性化），用于调试和监控，不影响正常计算路径。

---

### 4.4 `preprocess_factor(raw, industry, log_mv, fin_sector_codes=None, _diag=None)` — 完整流水线（L197-248）

```python
# Step 1：金融股过滤（仅财务因子传 fin_sector_codes）
if fin_sector_codes:
    fin_mask = industry.isin(fin_sector_codes)
    processed[fin_mask] = np.nan

# Step 2：MAD 去极值（只用 T 日横截面）
processed = winsorize_mad(processed)

# Step 3：Z-score 标准化
processed = standardize(processed)

# Step 4：行业 + log市值中性化
residuals = neutralize(processed, industry, log_mv, _diag=_diag)

# Step 5：残差再标准化
return standardize(residuals)
```

**Step 1 的设计哲学**：`fin_sector_codes=None` 跳过此步，量价因子和备选数据因子传 `None`（金融股的量价信号同样有效）；财务因子传 `cfg.FINANCIAL_SECTOR_CODES`（银行/非银金融的财务比率失真）。

**为何 Step 5 还要再标准化？**

OLS 残差的均值理论上为 0，但方差不为 1（残差的 std 小于原始因子 std，因为回归解释了部分方差）。再标准化确保最终输出满足 `mean≈0, std≈1`，使不同因子之间的权重比较有意义。

**NaN 的全程保持原则**：整个 5 步流水线中，NaN 始终保持 NaN，不做任何填充。将 NaN 填充为 0（横截面均值）的操作在**信号合成层**（`combiner.py`）执行，与预处理解耦。

---

## 五、内部辅助函数

v2 版本无任何 `_` 前缀的私有函数，三个核心步骤都已提升为公开函数（`winsorize_mad`、`standardize`、`neutralize`）。这是相比 v1 的主要变化：独立函数可以被单独测试，不依赖 `preprocess_factor` 的完整调用链。

---

## 六、落盘产物

`preprocess.py` 不直接写文件。由 `build_factor_panels.py` 在调用 `preprocess_factor` 后将结果写入 `data/processed/factor_panels/<name>.parquet`。

---

## 七、相关测试

关键边界用例（应在 `tests/test_evaluation.py` 或专项测试中覆盖）：

1. **全横截面相同值**：`standardize` 应返回全 NaN（而非 0/0）
2. **MAD=0 退化**：同一因子超过 50% 股票值相同时，`winsorize_mad` 应降级为 3σ 截断
3. **中性化 NaN 传染**：industry 为 NaN 的股票，其残差必须为 NaN（不能是 0 或其他填充值）
4. **时序隔离**：预处理不能使用 T+1 及之后的数据（通过 `shift_test.py` 间接验证）

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| `winsorize_mad` 收到全 NaN series | 抛出 `ValueError`（L65-68）|
| `neutralize` 有效样本 < 30 | 跳过中性化，返回原因子值，记录警告 |
| OLS 拟合失败（数值问题）| 返回原因子值，记录警告（L186-190）|
| `standardize` 样本 < 2 或 std≈0 | 返回全 NaN，记录警告 |

---

## 九、数据流图

```
factor_xxx() 输出（原始横截面 Series）
         ↓ fin_sector_codes 参数控制是否执行 Step 1
Step 1: 金融股置 NaN（仅财务因子）
         ↓
Step 2: winsorize_mad()  → median ± 3×1.4826×MAD 截断
         ↓
Step 3: standardize()    → (x - mean) / std，NaN 不变
         ↓
Step 4: neutralize()     → OLS 残差（factor ~ C(industry) + log_mv）
         ↓
Step 5: standardize()    → 残差再标准化
         ↓
预处理后的因子值 Series（mean≈0, std≈1，NaN 位置不变）
         ↓ build_factor_panels.py
data/processed/factor_panels/<name>.parquet
```

---

## 十、领域知识补充

**为什么要做行业+市值中性化？**

如果不中性化，因子 IC 中会混入行业轮动和大/小盘效应的贡献：
- 某月"成长行业（科技）> 价值行业（银行）"，高 EP 因子在科技股低，在银行股高，EP 的 IC 会受行业效应污染
- 大市值股票 ROE 通常更稳定，如果不中性化，ROE 因子实质上是个"买大市值"信号

中性化后的残差因子去掉了行业和市值的系统性解释，剩下的才是"纯粹"的个股 alpha 信号。

**OLS vs WLS 的选择**：

中性化用 OLS（等权）而非 WLS（流通市值加权）。原因：WLS 会给大市值股票更高权重，而大市值股票在许多财务因子上有特殊分布（如 ROE 更稳定），用 WLS 实际上是在按市值调整因子，而这里想要一个与市值无关的残差（残差中性化已控制 `log_mv`，WLS 会在控制的基础上再过度放大大市值的影响）。
