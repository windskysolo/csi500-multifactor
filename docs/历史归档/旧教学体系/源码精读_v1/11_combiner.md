# 11 — 多因子合成：combiner.py 逐行精讲

> 对应源文件：`src/signal/combiner.py`（370 行）  
> 前置依赖：`07_preprocess.md`（MAD/标准化），`08_ic_analysis.md`（IC 序列）  
> 核心任务：用 IC_IR 加权把 20+ 个因子合成为一个复合信号

---

## 一、模块定位

因子评估阶段（08-10）告诉你每个因子单独的预测能力；合成阶段把所有因子混合成一个信号交给优化器。

这里有个关键设计问题：**权重怎么定？**
- 等权：最简单，但忽略了因子之间能力强弱的差异
- IC_IR 加权：按历史 IC_IR（信息比率）分配权重，能力强的因子权重高

本模块默认用 IC_IR 加权（`method='ic_ir'`），等权（`method='equal'`）作为 baseline 对照。

---

## 二、常量与 FACTOR_DIRECTIONS 表

### 2.1 三个配置常量（第 37-39 行）

```python
MIN_IC_HISTORY: int = cfg.SIGNAL_MIN_IC_HISTORY        # 最少历史 IC 期数（才能计算 IC_IR）
DEFAULT_WINDOW_MONTHS: int = cfg.SIGNAL_WINDOW_MONTHS  # 滚动窗口月数（默认 24）
DEFAULT_MIN_VALID_FACTORS: int = cfg.SIGNAL_DEFAULT_MIN_VALID  # 每只股票最少有效因子数
```

这三个值都来自 `config.py`，保证全局统一。

### 2.2 FACTOR_DIRECTIONS 方向表（第 44-80 行）

```python
FACTOR_DIRECTIONS: dict[str, int] = {
    "ep_ttm": +1,   # 估值高=便宜=好
    "leverage": -1,  # 杠杆高=风险高=差
    "vol_60d": -1,   # 波动高=差
    "amihud": +1,    # 非流动性高=有溢价=好
    ...
}
```

这张表有两个用途：
1. **等权/冷启动回退时**：用来决定每个因子的正负号（方向）
2. **方向校验**：`validate_directions_vs_summary` 用它和训练集实测 IC 方向对比

**IC_IR 加权时不需要这张表**：IC_IR 本身带方向（IC 均值正 → IC_IR 正 → 加正权 → 因子值高=预测好），方向自动包含在权重符号里。

注意 `"holder_chg": +1` 的注释：`# 公式已含负号，值越高 = 股东减少 = 利好`。这是因子在构建时已做了方向处理，表中才标 +1。

---

## 三、方向校验函数（第 83-115 行）

```python
def validate_directions_vs_summary(factor_summary: "pd.DataFrame") -> list[str]:
    for name, row in factor_summary.iterrows():
        ic_mean = row.get("ic_mean", np.nan)
        if pd.isna(ic_mean) or name not in FACTOR_DIRECTIONS:
            continue
        eval_direction = 1 if float(ic_mean) > 0 else -1
        if eval_direction != FACTOR_DIRECTIONS[name]:
            mismatches.append(str(name))
            log.warning(...)
    return mismatches
```

此函数**只做检测不做修改**。发现方向不一致时输出 warning，但不阻止合成继续进行。调用方决定是否信任 IC 实测方向。

这体现了"诚实优于讨好"原则：发现问题就汇报，不静默吸收。

---

## 四、滚动 IC_IR 计算（第 122-164 行）

这是整个合成模块最关键的函数。

```python
def compute_rolling_ic_ir(
    ic_series_map: dict[str, pd.Series],
    before_date: pd.Timestamp,
    window_months: int = DEFAULT_WINDOW_MONTHS,
) -> dict[str, float]:
```

### 4.1 为什么要"丢掉最近一期"

```python
past = ic_s[ic_s.index < before_date].iloc[:-1]  # ← 丢掉 index[-1]
```

这行是最容易被忽略的时间对齐细节。

IC 序列中，`IC[T-1] = corr(factor[T-1], fwd_ret[T-1])`。

而 `fwd_ret[T-1]` 的计算是 `open_adj[T'] / open_adj[T] - 1`（T'+1 是下下期开盘）。  
在 T 日盘后，`fwd_ret[T-1]` 的"出场价"（T'+1 开盘价）**还没到来**，不可得。

因此，计算 T 日权重时，必须再丢弃 index < T 中最近的那一期（即 IC[T-1]），保证所有历史 IC 的 exit_date < T。

不丢弃这一期，就是轻微的未来函数泄露。

### 4.2 冷启动保护

```python
recent = past.iloc[-window_months:]
if len(recent) < MIN_IC_HISTORY:
    result[name] = np.nan
    continue
```

如果回看窗口内 IC 历史不足 `MIN_IC_HISTORY` 期，认为数据太少，IC_IR 不可靠，该因子权重置 NaN。

### 4.3 标准差保护

```python
if ic_std < 1e-10:
    result[name] = np.nan
    continue
```

IC 序列方差极小（近似恒定）时，IC_IR 会趋向无穷，置 NaN 避免数值不稳定。

---

## 五、等权映射（第 167-169 行）

```python
def _equal_weight_map(factor_names: list[str]) -> dict[str, float]:
    return {name: float(FACTOR_DIRECTIONS.get(name, 1)) for name in factor_names}
```

等权模式下，权重 = 因子方向（+1 或 -1），不是 1.0。  
这样等权合成时，负向因子（如 `vol_60d`）的贡献会自动取反，不需要事先手工翻转。

未在 `FACTOR_DIRECTIONS` 表中的因子默认 +1（保守，不翻转）。

---

## 六、单截面合成（第 176-248 行）

```python
def combine_factors_cross_section(
    factor_dict: dict[str, pd.Series],
    ic_ir_weights: dict[str, float],
    min_valid_factors: int = DEFAULT_MIN_VALID_FACTORS,
) -> pd.Series:
```

### 6.1 加权合成公式

```
composite[i] = Σ_k(w_k × f_k[i]) / Σ_k(|w_k|)
```

其中 k 只遍历 `w_k 非 NaN` 且 `f_k[i] 非 NaN` 的因子。  
分母是有效权重的**绝对值之和**，这样正负向因子混合时归一化仍然正确。

### 6.2 向量化实现

```python
arr        = factor_matrix.values.astype(float)  # (n_stocks, n_factors)
valid_mask = ~np.isnan(arr)                        # True = 有值
valid_count = valid_mask.sum(axis=1)               # 每只股票的有效因子数

arr_filled  = np.where(valid_mask, arr, 0.0)
numerator   = arr_filled  @ weights               # (n_stocks,)
denominator = valid_mask.astype(float) @ abs_weights  # (n_stocks,)

composite_vals = np.where(denominator > 0, numerator / denominator, np.nan)
composite_vals = np.where(valid_count >= min_valid_factors, composite_vals, np.nan)
```

关键设计：
- NaN 位置填 0（`arr_filled`），但分母里对应的 `|w_k|` 也不计（通过 `valid_mask`）
- 这保证了 NaN 不参与加权，不是当 0 处理（当 0 会偏低）
- `valid_count >= min_valid_factors`：有效因子数不足的股票整体置 NaN

### 6.3 合成后再次标准化

```python
if n_valid >= 2:
    result = winsorize_mad(result)
    result = standardize(result)
```

各因子标准化后再合成，合成值还是有量纲，必须重新标准化才能交给优化器（优化器 alpha 需要是 z-score 形式）。

---

## 七、全周期合成面板（第 255-369 行）

```python
def build_composite_panel(
    factor_panels: dict[str, pd.DataFrame],
    ic_series_map: dict[str, pd.Series],
    rebalance_dates: list[pd.Timestamp],
    method: str = "ic_ir",
    window_months: int = DEFAULT_WINDOW_MONTHS,
    min_valid_factors: int = DEFAULT_MIN_VALID_FACTORS,
    stability_weights: dict[str, float] | None = None,
    return_diagnostics: bool = False,
) -> ...:
```

### 7.1 冷启动检测与回退

```python
weights = compute_rolling_ic_ir(ic_series_map, before_date=T, ...)
if all(not pd.notna(v) for v in weights.values()):
    is_cold_start = True
    weights = _equal_weight_map(list(factor_dict.keys()))
```

冷启动判断：**所有因子的 IC_IR 都是 NaN**。  
只要还有一个因子 IC_IR 不是 NaN，就不算冷启动（该因子正常加权，其余 NaN 的因子被排除）。

### 7.2 稳定性权重（stability_weights）

```python
elif stability_weights is not None:
    weights = {
        name: (w * stability_weights.get(name, 1.0) if pd.notna(w) else w)
        for name, w in weights.items()
    }
```

`stability_weights` 是从因子评估报告导入的系数（∈ [0, 1]）：
- 验证集方向与训练集一致的因子：系数 = 1.0（不调整）
- 方向在验证集翻转的因子：系数 = 0.5（降权）
- 完全排除的因子：系数 = 0.0（乘以 0 = 不参与合成）

**注意**：稳定性权重只在 IC_IR 加权且非冷启动时生效，冷启动回退的等权路径不应用。

### 7.3 诊断信息（return_diagnostics）

```python
if return_diagnostics:
    weight_history[T] = dict(weights)    # 每期实际用了哪些权重
    cold_start_flags[T] = is_cold_start  # 是否冷启动
```

开启后，可以追溯每个调仓日的因子权重，用于排查异常期。

---

## 八、完整数据流

```
ic_series_map                      factor_panels
{factor: IC time series}           {factor: T × code panel}
        │                                  │
        ▼ for each date T:                 │
compute_rolling_ic_ir(before_date=T)       │ factor_dict[factor] = panel.loc[T]
        │                                  │
        ├── 冷启动 → _equal_weight_map     │
        └── 非冷启动 → IC_IR weights       │
                │ × stability_weights      │
                ▼                          ▼
        combine_factors_cross_section(factor_dict, weights)
                │ Σ(w_k × f_k) / Σ|w_k|
                │ valid_count >= min_valid
                │ winsorize_mad + standardize
                ▼
        composite[T] = pd.Series(code → z-score)

rows → pd.DataFrame(T × code) = composite_panel
```

---

## 九、关键参数的经济含义

| 参数 | 典型值 | 含义 |
|------|-------|------|
| `window_months` | 24 | 用最近 24 个月 IC 计算 IC_IR（约 2 年） |
| `MIN_IC_HISTORY` | 12 | 至少需要 12 个月 IC 才能可靠计算 IC_IR（冷启动保护） |
| `min_valid_factors` | 3 | 某只股票至少有 3 个有效因子才合成（太少则不可信） |

---

## 十、等权 vs IC_IR 加权的对比

| 维度 | 等权 | IC_IR 加权 |
|------|------|-----------|
| 冷启动期 | 立即可用 | 需等待 MIN_IC_HISTORY 个月 |
| 历史依赖 | 无 | 依赖因子评估结果 |
| 因子方向 | 必须手动在 FACTOR_DIRECTIONS 中配置 | IC 符号自动决定 |
| 自适应性 | 固定 | 每期动态调整 |
| 适合用于 | baseline / 验证组 / 评估方法稳健性 | 生产信号 |
