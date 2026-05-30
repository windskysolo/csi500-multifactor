# 13 — combiner.py：多因子合成信号（IC_IR 加权路径）

> 对应源文件：`src/signal/combiner.py`
> 实际行数：**369 行**（计划快照为 307，新增 `stability_weights`、`return_diagnostics`、`validate_directions_vs_summary`，撰写前已核实）
> 处理方式：**核查更新**
> 所属 Part：Part 4 — 信号合成层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 3  信号合成    ← 本文件（combiner.py，IC_IR 加权路径）
Layer 3  信号合成    src/signal/ridge_decay.py（Ridge Decay 路径，详见 14）
Layer 2  因子评估    src/evaluation/ic_analysis.py（提供 IC 历史）
Layer 1  因子构建    src/factors/（提供因子面板）
Layer 0  数据基础    src/data/ + src/config.py
```

### 三条信号路径的分工

```
stages.py::run_signal_stage()
    │
    ├── method="icir"                 → combiner.py::build_composite_panel(method="ic_ir")
    │
    ├── method="ridge"
    │     ├── training_mode="expanding"    → RidgeCombiner（experiments/legacy）
    │     ├── training_mode="rolling"      → RidgeRollingCombiner（experiments/legacy）
    │     └── training_mode="decay_weighted_expanding" → RidgeDecayCombiner（ridge_decay.py）
    │
    └── method="equal"                → combiner.py::build_composite_panel(method="equal")
```

**`combiner.py` 只负责 `ic_ir` 和 `equal` 两种方法**。Ridge 路径完全绕过本模块，由 `stages.py` 直接调用对应的 RidgeCombiner 类。

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游（数据） | 因子面板 dict（来自 `build_factor_panels.py`）| 标准化后的 date×stock 矩阵 |
| 上游（IC 权重）| IC 历史（来自 `ic_analysis.build_ic_history`）| 按期计算滚动 IC_IR 权重 |
| 上游（配置）| `src/config.py` | `SIGNAL_MIN_IC_HISTORY`、`SIGNAL_WINDOW_MONTHS`、`SIGNAL_DEFAULT_MIN_VALID` |
| 下游（直接）| `src/pipeline/stages.py::run_signal_stage` | Stage 层按 method 分发 |
| 上游（预处理）| `src/factors/preprocess.py` | `winsorize_mad`、`standardize` |

### 与 v1 的主要差异（核查更新重点）

| 变化 | 位置 | 说明 |
|------|------|------|
| `stability_weights` 参数 | L282、L332 | 可降权或排除验证集方向翻转的因子 |
| `return_diagnostics` 参数 | L264、L338–368 | 返回逐期权重历史和冷启动标记 |
| `validate_directions_vs_summary` | L83–115 | 校验 `FACTOR_DIRECTIONS` 与训练集 IC 方向的一致性 |
| 方向表更新 | L41–80 | 新增 `fcfp`、`holder_chg`、`margin_ratio`、`short_ratio`、`large_net_inflow` |

---

## 二、时间对齐与 PIT 假设

### IC_IR 权重的时间约束（关键）

```python
# combiner.py::compute_rolling_ic_ir（L122–163）
# 关键：先取 index < before_date 的历史，再丢弃最近一期
past = ic_s[ic_s.index < before_date].iloc[:-1]   # L146
```

**为什么丢弃最近一期？**  
IC(T-1) = corr(factor[T-1], fwd_ret[T-1])。  
fwd_ret[T-1] 的 exit_date ≈ T 之后第一个交易日（T+1），在 T 盘后生成信号时，T+1 价格尚未确定，因此 IC(T-1) 在 T 时刻**不可得**。

若不丢弃，则 IC_IR 权重中隐含了"T 盘后已知 T+1 价格"的前视偏差，等价于微弱的未来函数。

**时间约束链**：

```
调仓日 T（因子观测日）
  ↓
IC 权重只用 index < T 的 IC 历史
  ↓
再丢弃最近一期（IC(T-1) 的 exit 在 T+1）
  ↓
最终使用 index <= T-2 的 IC 记录
  ↓
其中最后使用的 IC = IC(T-2)，对应 exit_date ≈ T-1 后第一交易日 < T
```

### 合成阶段的 PIT 保证

合成公式 `composite[i] = Σ(w_k × f_k[i]) / Σ(|w_k|)` 中：
- `f_k[i]`：T 日截面因子值，已经过 PIT 对齐（`build_factor_panels.py` 负责）
- `w_k`：IC_IR 权重，严格 < T（以上分析保证）
- 合成结果不含任何 T 日之后的信息 ✓

---

## 三、模块顶部：导入与常量

```python
# L37–39 — 从 config 读取常量
MIN_IC_HISTORY: int = cfg.SIGNAL_MIN_IC_HISTORY        # 12（不足此期不做 IC_IR 加权）
DEFAULT_WINDOW_MONTHS: int = cfg.SIGNAL_WINDOW_MONTHS  # 24（滚动回看期数）
DEFAULT_MIN_VALID_FACTORS: int = cfg.SIGNAL_DEFAULT_MIN_VALID  # 8（每只股票最少有效因子数）
```

```python
# L44–80 — FACTOR_DIRECTIONS：因子预期方向表
FACTOR_DIRECTIONS: dict[str, int] = {
    # 价值
    "ep_ttm":  +1, "bp": +1, "sp_ttm": +1, "dy_ttm": +1,
    "cfp":     +1, "fcfp": +1,
    # 质量
    "roe":     +1, "roa": +1, "gross_margin": +1, "asset_turn": +1,
    "leverage": -1,            # 高杠杆 = 高风险
    "accrual":  -1,            # 高应计 = 低质量盈余
    # ... 更多因子
}
```

`FACTOR_DIRECTIONS` 的用途：
1. `method="equal"` 时确定各因子的符号（否则等权会把正负因子相互抵消）
2. 冷启动期回退到等权时使用
3. `validate_directions_vs_summary` 用于一致性校验

**重要**：IC_IR 加权模式（非冷启动）下，因子方向由 IC_IR 的符号自动决定，不依赖此表。IC_IR > 0 的因子正权，IC_IR < 0 的因子负权（自动反向），**无需手工翻转**。

---

## 四、核心函数逐一解析

### 4.1 `validate_directions_vs_summary` — 方向一致性校验

```python
# L83–115
def validate_directions_vs_summary(factor_summary: pd.DataFrame) -> list[str]:
```

**用途**：对比 `FACTOR_DIRECTIONS`（硬编码先验）与训练集 IC 均值（数据驱动后验），发现不一致的因子。

**调用时机**：在合成前，由 `run_factor_evaluation.py` 或 `stages.py` 调用。不一致的因子输出 warning 日志，但**不影响合成逻辑**（调用方根据 warning 判断是否需要更新 `FACTOR_DIRECTIONS`）。

**典型不一致来源**：
- 市场 regime 变化导致某因子方向逆转（如动量因子在特殊年份）
- 行业中性化后因子方向偏移

**返回**：不一致的因子名列表（空列表表示全部一致）。

---

### 4.2 `compute_rolling_ic_ir` — 滚动 IC_IR 权重计算

```python
# L122–163
def compute_rolling_ic_ir(
    ic_series_map: dict[str, pd.Series],
    before_date: pd.Timestamp,
    window_months: int = DEFAULT_WINDOW_MONTHS,
) -> dict[str, float]:
```

**执行逻辑**（L143–162）：

```python
for name, ic_s in ic_series_map.items():
    # 步骤1: 取 index < before_date 的历史
    past = ic_s[ic_s.index < before_date].iloc[:-1]  # 丢弃最近一期
    if len(past) == 0:
        result[name] = np.nan; continue

    # 步骤2: 取最近 window_months 期
    recent = past.iloc[-window_months:]

    # 步骤3: 不足 MIN_IC_HISTORY 期 → NaN（冷启动）
    if len(recent) < MIN_IC_HISTORY:
        result[name] = np.nan; continue

    # 步骤4: 计算 IC_IR
    ic_mean = recent.mean()
    ic_std  = recent.std(ddof=1)
    if ic_std < 1e-10: result[name] = np.nan; continue
    result[name] = ic_mean / ic_std
```

**冷启动的定义**：有效观测数 < `MIN_IC_HISTORY = 12`。约 12 个月（从项目启动后约 1 年才能进入非冷启动期）。冷启动期的因子权重为 NaN，由 `build_composite_panel` 统一降级到等权。

---

### 4.3 `combine_factors_cross_section` — 单截面合成

```python
# L176–248
def combine_factors_cross_section(
    factor_dict: dict[str, pd.Series],
    ic_ir_weights: dict[str, float],
    min_valid_factors: int = DEFAULT_MIN_VALID_FACTORS,
) -> pd.Series:
```

**合成公式**：

```
composite[i] = Σ_k(w_k × f_k[i]) / Σ_k(|w_k|)
```

其中 k 只遍历 `w_k` 非 NaN 且 `f_k[i]` 非 NaN 的因子。

**向量化实现细节**（L222–238）：

```python
# L222–238 — 向量化加权合成（避免逐股循环）
arr         = factor_matrix.values.astype(float)
valid_mask  = ~np.isnan(arr)
valid_count = valid_mask.sum(axis=1)

arr_filled  = np.where(valid_mask, arr, 0.0)        # NaN 临时填 0 用于矩阵乘
numerator   = arr_filled  @ weights                  # (n_stocks,)：加权总和
denominator = valid_mask.astype(float) @ abs_weights # (n_stocks,)：有效权重总和

composite_vals = np.where(denominator > 0, numerator / denominator, np.nan)
composite_vals = np.where(valid_count >= min_valid_factors, composite_vals, np.nan)
```

**为什么先填 0 再矩阵乘，最后除 denominator？**  
不能直接用 `nansum`：不同因子的权重不同，缺失因子的贡献不能简单地用总权重归一化（会低估有效因子的影响）。  
正确做法：numerator 只包含有效因子的加权贡献，denominator 只包含有效因子的权重绝对值之和。两者相除得到"在当前有效因子子集上"的归一化合成值。

**合成后再预处理**（L242–247）：
```python
if n_valid >= 2:
    result = winsorize_mad(result)    # MAD 去极值
    result = standardize(result)      # Z-score 标准化
```

合成信号会重新标准化，原因：各因子已经标准化，但组合后的量纲仍可能因权重不均导致分布偏移（特别是权重差异很大时）。重新标准化确保下游优化器收到统一量纲的信号。

---

### 4.4 `build_composite_panel` — 全周期合成信号面板

```python
# L255–368
def build_composite_panel(
    factor_panels: dict[str, pd.DataFrame],
    ic_series_map: dict[str, pd.Series],
    rebalance_dates: list[pd.Timestamp],
    method: str = "ic_ir",
    window_months: int = DEFAULT_WINDOW_MONTHS,
    min_valid_factors: int = DEFAULT_MIN_VALID_FACTORS,
    stability_weights: dict[str, float] | None = None,    # 新增
    return_diagnostics: bool = False,                      # 新增
) -> pd.DataFrame | tuple[pd.DataFrame, dict]:
```

**方法路由逻辑**（L323–336）：

```python
if method == "equal":
    weights = _equal_weight_map(...)          # 等权，不依赖 IC 历史
else:  # method == "ic_ir"
    weights = compute_rolling_ic_ir(...)
    if all(nan for all factors):              # 冷启动
        is_cold_start = True
        weights = _equal_weight_map(...)      # 等权回退
    elif stability_weights is not None:        # 应用稳定性系数
        weights = {name: w * stability_weights.get(name, 1.0) ...}
```

**`stability_weights` 的设计意图**（L275–282）：

```
来源：run_factor_evaluation.py 的 summary["stability_weight"]
  - 验证集方向翻转因子（训练集 IC_mean > 0 但验证集 IC_mean < 0）：stability_weight = 0.5
  - 完全排除因子：stability_weight = 0.0
  - 正常因子：stability_weight = 1.0

用途：降低"在训练集有效但验证集方向已反转"因子的权重，
      不是直接剔除（保留少量贡献），而是将其影响减半。
```

**注意**：`stability_weights` 只在 `method="ic_ir"` 且非冷启动时生效（L332–336）。冷启动等权回退时不应用，因为等权的贡献由 `FACTOR_DIRECTIONS` 决定，不是 IC_IR。

**`return_diagnostics` 的内容**（L361–368）：

```python
diagnostics = {
    "weight_history":   {Timestamp: {factor: weight}},   # 逐期实际权重
    "cold_start_flags": {Timestamp: bool},                # 是否冷启动
    "cold_start_count": int,                              # 冷启动总期数
    "method":           str,                              # 合成方法
}
```

用途：调用方可以用 `weight_history` 生成因子权重随时间变化的可视化，或诊断"某期为什么信号质量差"。

---

## 五、内部辅助函数

| 函数 | 位置 | 说明 |
|------|------|------|
| `_equal_weight_map(factor_names)` | L167–169 | 按 FACTOR_DIRECTIONS 确定符号（±1），未在表中的默认 +1 |

---

## 六、落盘产物（Artifacts）

`combiner.py` **不直接写文件**。

典型产物（由 `stages.py` 写入）：

| 产物 | 路径 | 格式 | 内容 |
|------|------|------|------|
| 合成信号面板 | `runs/train_valid/<run_id>/signal/signal.parquet` | Parquet | date × stock 的 composite 信号 |
| 权重历史 | `runs/train_valid/<run_id>/signal/weight_history.parquet` | Parquet | date × factor 的实际权重（return_diagnostics=True 时） |

---

## 七、相关测试

**当前状态**：`tests/test_pipeline_contracts.py` 中有少量 Stage 集成测试，但 **`combiner.py` 没有专项单元测试**（TODO）。

**高优先级待补充测试**：

| 测试场景 | 要点 |
|---------|------|
| `compute_rolling_ic_ir` 时间约束 | 将因子整体后移一期后 `before_date` 不变，权重应改变 |
| 冷启动等权回退 | 不足 12 期 IC 历史时，所有因子权重按 FACTOR_DIRECTIONS 等权 |
| `stability_weights=0.0` 排除因子 | 该因子在合成中完全不参与（等价于从 factor_dict 中删除） |
| 负权重因子（IC_IR < 0）| 合成值应是原始因子的**反向**贡献 |
| `min_valid_factors` 阈值 | 股票有效因子数 = min_valid_factors-1 时置 NaN |

---

## 八、失败与降级路径

| 失败场景 | 行为 |
|---------|------|
| 所有因子冷启动（IC 历史不足）| 回退到等权，记录 info 日志；`cold_start_flags[T] = True` |
| 某期 `factor_dict` 为空（所有因子该日期缺失）| 该期跳过，不在 panel 中生成行 |
| `method` 不在支持列表中 | 抛 `ValueError`（L301） |
| 全期均无有效数据 | 返回空 DataFrame，记录 warning |

---

## 九、数据流图

```
因子面板 dict[name → (date×stock)]         IC 历史 dict[name → IC Series]
           │                                           │
           │            调仓日列表                     │
           └──────────────────┬────────────────────────┘
                              │
                              ▼
              for each T in rebalance_dates:

                compute_rolling_ic_ir(ic_map, before_date=T)
                  → weights {factor: IC_IR}（< T，丢弃最近一期）

                [冷启动] → _equal_weight_map(factor_names)
                [stability_weights] → weights × stability_weights

                combine_factors_cross_section(factor_dict, weights)
                  → composite Series（ts_code → 合成值）

                [MAD 去极值 + Z-score 标准化]

              ▼
        panel DataFrame (rebalance_date × ts_code)
              │
              ▼
        stages.py → 写入 signal.parquet
```

---

## 十、领域知识补充

### IC_IR 加权的信息论解释

IC_IR 加权等价于**最大化预测信息利用效率**：将有限的权重预算分配给"信号稳定性最高"的因子。从信息比率（IR = 预测均值 / 预测标准差）的视角，IC_IR 加权的组合信号满足：

```
组合 IC_IR² ≈ Σ IC_IR_k²
```

（各因子 IC 不相关时成立，这也是为什么在合成前需要去冗余相关因子）

高 IC_IR 的因子贡献更多信号，低 IC_IR 的因子（甚至 NaN 期）不稀释组合信号质量，这是 IC_IR 加权相对等权的核心优势。

### 冷启动的合理性

冷启动回退到等权并非完全无意义——等权合成只需要因子方向正确（`FACTOR_DIRECTIONS` 保证）。对于有充分文献支持的成熟因子，等权是一个合理的 Baseline。随着 IC 历史积累，系统自动从等权过渡到 IC_IR 加权，无需手工切换。

### stability_weights 与因子排除的区别

```
stability_weights = 0.0  →  该因子权重 = IC_IR × 0 = 0，等价于排除
stability_weights = 0.5  →  该因子权重 = IC_IR × 0.5，保留 50% 贡献
stability_weights = 1.0  →  正常使用（默认）

排除（weight=0）适用于：验证集 IC 方向完全反转，或 Gate 明确失败
降权（weight=0.5）适用于：验证集 IC 较弱但方向未反转，或弱因子
```
