# 因子质量问题分析报告

> 生成日期：2026-05-09  
> 范围：`02_factor_evaluation.ipynb` 及相关 `src/evaluation/` 模块  
> 状态标注：✅ 代码已确认 / ⚠️ 运行结果观察 / ❌ 经核实不属实

---

## 概述

本报告仅收录**已核实属实**的问题，按严重程度排序。Bug.md 中的 Bug 1（高相关检测失效）、Bug 2（Sharpe 方向硬编码）、Bug 3（错位方向写反）经代码核实**不属实**，不在本文中讨论。

| 编号 | 问题 | 严重程度 | 核实状态 |
|------|------|----------|----------|
| A | 因子有效覆盖率系统性偏低（~43%） | P1 严重 | ✅ 代码确认 |
| B/C | 高相关因子冗余未被过滤 | P1 严重 | ✅ 代码确认 |
| D | 验证集翻转率 37%，成因需区分 | P2 重要 | ⚠️ 结果观察 |
| E | q_roe 在熊市年完全失效 | P2 重要 | ⚠️ 结果观察 |
| F | 2020-2021 茅指数风格污染 | P2 重要 | ⚠️ 结果观察 |
| G | `final_include` 筛选标准不完整 | P2 重要 | ✅ 代码确认 |
| H | Forward Return 使用原始收益而非超额收益 | P3 方法论 | ✅ 代码确认 |

---

## 问题 A — 因子有效覆盖率系统性偏低（~43%）✅

### 现象

所有 27 个因子的有效率均集中在 42%–46% 之间，即每个调仓期约有超过一半的中证 500 成分股没有有效因子值。  
财务因子对中证 500 Universe 的正常覆盖率应 ≥ 85%。

### 根本原因

**代码层面已确认**：`src/data/pit_loader.py` 第 35 行存在一个全局陈旧度过滤阈值：

```python
# src/data/pit_loader.py:35
STALE_THRESHOLD_MONTHS: int = 18
```

第 198–199 行的 Step F 会将所有 `latest_end < rebalance_date - 18 months` 的数据置为 NaN：

```python
# src/data/pit_loader.py:197-199
# ---- Step F: 陈旧度过滤 ----
stale_mask = df["latest_end"] < stale_cutoff
result[stale_mask] = np.nan
```

**问题所在**：18 个月的阈值对 A 股市场的财报披露周期过于严苛。

A 股上市公司财报发布规律：
- 一季报：4 月 30 日前
- 半年报：8 月 31 日前
- 三季报：10 月 31 日前
- 年报：4 月 30 日前

正常情况下，最新可用财报距调仓日不应超过 **6 个月**（三季报→年报→一季报之间的最大间隔）。当设置 18 个月阈值时，大量本应可用的财报被过滤，导致覆盖率大幅下降。

> 注：PIT 对齐本身（使用 `pit_date <= rebalance_date`）是**正确的**，陈旧度阈值才是覆盖率低的真实原因。Bug.md 猜测使用报告期而非公告日导致的说法**不成立**。

### 影响

- 每期实际参与 IC 计算的股票约 210–230 只，而非完整的 500 只
- 所有 IC 均值、IC_IR、Sharpe 数值存在**系统性样本选择偏差**
- 覆盖率低的月份可能偏向流动性好、信息披露及时的大盘股，因子效果被高估

### 解决方案

**方案 1（推荐）：分因子类型设置阈值**

不同因子类型的数据更新频率不同，应区分处理：

```python
# src/data/pit_loader.py
STALE_THRESHOLD_BY_TYPE = {
    "quarterly": 6,   # 季频财务数据：6 个月（两个季度内必有更新）
    "annual":    15,  # 年频数据（如某些行业指标）：15 个月
    "default":   9,   # 通用默认：9 个月
}
```

**方案 2（保守）：统一放宽至 9 个月**

```python
STALE_THRESHOLD_MONTHS: int = 9  # 原为 18，放宽但仍保留安全边际
```

**验证方法**：修改后重新运行 `make_ttm()` 和 `get_pit_latest()`，比较覆盖率变化：

```python
# 在 notebook 中加入覆盖率对比
coverage_before = factor_panel.notna().mean(axis=1).mean()
# 修改阈值后重跑
coverage_after = factor_panel_new.notna().mean(axis=1).mean()
print(f"覆盖率变化：{coverage_before:.1%} → {coverage_after:.1%}")
```

**修改后必须重跑**：因子面板、IC 检验、分组回测全部需要重新计算。

---

## 问题 B/C — 高相关因子冗余未被过滤 ✅

### 现象

因子相关矩阵检测到多组高度冗余的因子对，但这些因子全部进入了最终合成：

| 因子对 | 平均截面 Spearman r | 含义 |
|--------|---------------------|------|
| `ivol_60d` & `vol_60d` | **+0.93** | 特质波动率 ≈ 总波动率 |
| `roa` & `roe` | **+0.91** | 两者均衡量盈利能力 |
| `q_roe` & `roe` | **+0.88** | q_roe 是 roe 的季度版 |
| `q_roe` & `roa` | **+0.80** | 同上 |
| `np_yoy` & `roe_delta` | **+0.81** | 净利润同比 ≈ ROE 变化 |

### 根本原因

**代码层面已确认**：`compute_factor_correlation()` 函数（`src/evaluation/ic_analysis.py:312`）工作**正常**，能够正确检测高相关因子对。

但 notebook（`02_factor_evaluation.ipynb:3538`）的 `final_include` 筛选逻辑**完全没有使用相关性检测结果**：

```python
# notebooks/02_factor_evaluation.ipynb（第 3538 行）
# 现状：只看 IC 有效性和错位警告
summary["final_include"] = summary["effective_ic"] & ~summary["shift_warning"].fillna(False)
```

相关性矩阵只是被展示（可视化），没有被接入因子筛选流程。

### 影响

- 盈利因子（`roa` + `roe` + `q_roe`）在 IC_IR 加权合成中实际权重为其他类别的 **3 倍**
- 波动率因子（`ivol_60d` + `vol_60d`）被双重计权
- 破坏多因子分散化的初衷，合成因子实质上是"盈利因子 + 加权波动率因子"

### 解决方案

**Step 1：建立去冗余函数**

推荐使用**贪心法**：按 `|IC_IR|` 降序保留因子，若某因子与已保留因子的相关系数 > 阈值则丢弃。

```python
def filter_redundant_factors(
    summary: pd.DataFrame,
    corr_matrix: pd.DataFrame,
    corr_threshold: float = 0.70,
) -> list[str]:
    """
    贪心过滤高相关冗余因子。

    按 |IC_IR| 降序遍历已通过 IC 检验的因子，若当前因子与已纳入因子
    的相关系数 > corr_threshold，则丢弃（保留 IC_IR 更强的那个）。

    Args:
        summary:        batch_ic_test 输出的因子汇总 DataFrame
        corr_matrix:    compute_factor_correlation 输出的相关矩阵
        corr_threshold: 高相关阈值，默认 0.70
    Returns:
        去冗余后保留的因子名列表
    """
    candidates = summary[summary["effective_ic"]].sort_values(
        "ic_ir", key=abs, ascending=False
    ).index.tolist()

    selected = []
    for factor in candidates:
        if not selected:
            selected.append(factor)
            continue
        corr_with_selected = corr_matrix.loc[factor, selected].abs()
        if corr_with_selected.max() <= corr_threshold:
            selected.append(factor)
    return selected
```

**Step 2：将去冗余结果接入筛选逻辑**

```python
# 在 notebook 中替换原有的 final_include 逻辑
deduplicated_factors = filter_redundant_factors(summary, corr_matrix, corr_threshold=0.70)

summary["final_include"] = (
    summary["effective_ic"]
    & ~summary["shift_warning"].fillna(False)
    & summary.index.isin(deduplicated_factors)
)
```

**立即可执行的手动处理**（等待代码重构前的临时方案）：

从以下冗余对中各保留一个：
- `roa` / `roe` / `q_roe` → 保留 **`q_roe`**（IC_IR 最高，+0.587）
- `ivol_60d` / `vol_60d` → 保留 **`ivol_60d`**（学术文献支持更强，已剥离系统性风险）
- `np_yoy` / `roe_delta` → 保留 **`np_yoy`**（覆盖更广，可用于盈利增长方向）

---

## 问题 D — 验证集方向翻转率 37%，成因需区分 ⚠️

### 现象

10 个因子（共 27 个，占 37%）在验证集（2022 年）出现 IC_IR 方向翻转，尤以核心质量因子为重：

| 因子 | 训练集 IC_IR | 验证集 IC_IR | 变化幅度 |
|------|-------------|-------------|---------|
| `q_roe` | +0.587 | -0.159 | -0.746 |
| `roa` | +0.495 | -0.288 | -0.783 |
| `roe` | +0.495 | -0.200 | -0.695 |
| `asset_turn` | +0.385 | -0.326 | -0.711 |
| `gross_margin` | +0.342 | -0.170 | -0.512 |

### 根本原因分析

必须区分**两类成因**，它们的处置方式完全不同：

**成因 1：市场制度性失效（非因子本身问题）**

2022 年 A 股经历 -20% 的单边下跌，叠加俄乌冲突、美联储加息、新冠封控等系统性冲击。全球范围内质量因子在 2022 年普遍失效：高 ROE 龙头股因高估值遭受更大压力，低质量高杠杆股因"困境反弹"短暂跑赢。这是**市场状态切换**，不代表因子模型有缺陷。

**成因 2：统计检验力严重不足（更关键）**

验证集有效期仅 **11 期**，对应的统计参数：
- t 检验的 95% 置信区间宽度 ≈ ±`2/√11` ≈ ±0.60
- 即 IC_IR = -0.159 与 IC_IR = 0 在统计上**无法区分**
- 10 个"翻转"因子中，部分可能只是统计噪声，不是真实的方向改变

```
# 验证检验力不足
n = 11
se = 1 / np.sqrt(n)        # ≈ 0.302
ci_95 = 1.96 * se          # ≈ ±0.591
# → IC_IR 必须超过 ±0.59 才能在 11 期内显著，大多数翻转因子不满足此条件
```

### 解决方案

**方案 1：延伸验证窗口**

将验证集从 2022 年单年延伸至 2022–2024（约 35 期），统计检验力从几乎为零提升至可接受水平：

```python
# src/config.py 或 notebook 中
TRAIN_END   = pd.Timestamp("2021-12-31")
VALID_START = pd.Timestamp("2022-01-01")
VALID_END   = pd.Timestamp("2024-12-31")  # 原为 2022-12-31
TEST_START  = pd.Timestamp("2025-01-01")  # 相应调整
```

> **注意**：延伸验证集会压缩测试集。需确认测试集仍有足够期数（≥ 6 期），或接受更短的样本外评估窗口。

**方案 2：在评估报告中显式标注检验力**

如果不调整时间区间，至少在报告中加入检验力警告：

```python
# 在 ic_summary 或 batch_ic_test 中增加
if n < 24:
    warnings.warn(
        f"有效期 n={n} 不足 24，t 检验置信度有限（95% CI 宽度约 ±{1.96/n**0.5:.2f}），"
        "验证集翻转结论仅供参考，不应作为排除因子的依据。"
    )
```

**方案 3：对翻转因子降权而非硬排除**

将验证集表现纳入合成权重，而非二元化的 include/exclude：

```python
# 对方向翻转因子施加 0.5 的权重惩罚
stability_weight = summary["dir_flip_valid"].map({True: 0.5, False: 1.0})
```

---

## 问题 E — q_roe 在熊市年（2018年）完全失效 ⚠️

### 现象

从 IC 时序图可见，`q_roe` 的累计 IC 曲线在 2018 年全年几乎水平（无斜率），月度 IC 均值接近 0，因子在该年完全丧失预测能力。结合 2022 年验证集翻转，可确认 `q_roe` 对市场状态高度敏感：

| 市场环境 | q_roe 表现 |
|---------|-----------|
| 2016–2017（白马行情） | IC_IR 强正，因子高效 |
| 2018（去杠杆熊市） | IC ≈ 0，因子完全失效 |
| 2019–2021（茅指数牛市） | IC_IR 极强，但含风格污染（见问题 F）|
| 2022（杀估值熊市） | IC 翻负，方向反转 |

### 根本原因

`q_roe` 本质上是一个"高质量溢价"因子，在市场偏好高质量股票时有效（牛市/慢牛），在市场陷入流动性危机（2018）或系统性去杠杆（2022）时，投资者被迫抛售优质资产，因子方向失效甚至翻转。

### 解决方案

**方案 1：在合成权重中引入时序稳定性惩罚**

计算 IC 时序的变异系数或"平台期占比"，对稳定性差的因子降权：

```python
def ic_stability_score(ic_series: pd.Series, window: int = 12) -> float:
    """
    衡量 IC 时序在滚动窗口内的方向一致性。
    返回值 ∈ [0, 1]，越高越稳定。
    """
    rolling_sign = ic_series.rolling(window).mean().apply(np.sign)
    # 方向一致的滚动窗口占比
    consistency = (rolling_sign == np.sign(ic_series.mean())).mean()
    return float(consistency)
```

**方案 2：采用 ICIR 加权时叠加时序稳定性调整**

```python
# 在合成阶段
weights = ic_ir_weights * stability_scores
weights = weights / weights.sum()   # 归一化
```

**方案 3（短期）：将 `q_roe` 与 `roa` 合并为单一盈利质量因子**

`q_roe`、`roa`、`roe` 三者高度冗余（见问题 B），去冗余后只保留一个，本身就降低了对单一盈利指标的依赖。

---

## 问题 F — 2020-2021 茅指数风格污染 ⚠️

### 现象

`q_roe`、`roe_delta`、`np_yoy` 等质量/成长因子的 Q5–Q1 累计收益曲线在 2020 年底至 2021 年出现急剧拉升，贡献了训练集内大量的 Sharpe 值。这与 A 股"茅指数"行情（高 ROE 龙头股极致溢价）高度重合：

- 贵州茅台等高 ROE 龙头在 2020–2021 年溢价创历史峰值
- Q5 组（高 ROE）不是因因子预测能力强，而是因风格集中而跑赢
- 2022 年风格切换后，累计收益曲线急剧回落，与训练集末期拉升直接对应

### 影响

- 三个因子的训练集 IC_IR 被末期特殊行情虚抬，代表性不足
- 跨样本期的预测能力很可能被高估

### 解决方案

**方案 1：在训练集内做时序分段评估**

不只看训练集整体 IC_IR，分段观察：

```python
# 分段 IC_IR 评估
periods = {
    "2016-2018": ("2016-01-01", "2018-12-31"),
    "2019-2021": ("2019-01-01", "2021-12-31"),
}
for label, (start, end) in periods.items():
    sub_ic = ic_series.loc[start:end]
    sub_ir = sub_ic.mean() / sub_ic.std(ddof=1)
    print(f"{label}: IC_IR = {sub_ir:.3f}")
```

若某因子在 2016–2018 和 2019–2021 两段内 IC_IR 方向一致且量级相近，说明效果稳定；若仅靠 2019–2021 贡献，则应降低权重。

**方案 2：在 IC_IR 加权时使用"衰减加权"**

给近期数据更低权重，避免末期牛市的异常效果主导合成权重：

```python
# 时间衰减 IC 加权（衰减系数 λ=0.95/月）
decay = np.exp(-np.arange(len(ic_series))[::-1] * np.log(1 / 0.95))
weighted_ic_mean = (ic_series * decay).sum() / decay.sum()
weighted_ic_std = np.sqrt(((ic_series - weighted_ic_mean) ** 2 * decay).sum() / decay.sum())
weighted_ic_ir = weighted_ic_mean / weighted_ic_std
```

**方案 3：加入市场风格中性化**

在因子中性化阶段额外对"市值 × 估值"联合因子做中性化，抑制风格驱动的截面收益。

---

## 问题 G — `final_include` 筛选标准不完整 ✅

### 现象

`notebooks/02_factor_evaluation.ipynb`（第 3538 行）当前的筛选逻辑：

```python
# 现状：仅两个条件
summary["final_include"] = summary["effective_ic"] & ~summary["shift_warning"].fillna(False)
```

汇总表中虽然已经计算了 `directional_sharpe_ls`、`dir_flip_valid` 等字段（第 3532–3535 行），但它们**只被展示，未参与筛选**。

### 影响

- `dir_flip_valid = True` 的因子（验证集方向翻转）无差别纳入合成，不予任何惩罚
- `directional_sharpe_ls` 极低的因子（如修正前的 `margin_ratio`）同样可以入选
- 高相关冗余因子无法被自动过滤（见问题 B/C）

### 解决方案

**推荐的完整筛选逻辑**：

```python
# notebooks/02_factor_evaluation.ipynb — 替换第 3537-3538 行

# 先做去冗余（解决问题 B/C）
deduplicated_factors = filter_redundant_factors(summary, corr_matrix, corr_threshold=0.70)

# 综合筛选
summary["final_include"] = (
    # 条件 1：IC 显著有效（原有）
    summary["effective_ic"]
    # 条件 2：无未来函数警告（原有）
    & ~summary["shift_warning"].fillna(False)
    # 条件 3：去冗余后保留（新增，解决 B/C）
    & summary.index.isin(deduplicated_factors)
    # 条件 4：方向性 Sharpe 合格（新增）
    & (summary["directional_sharpe_ls"].abs() >= 0.50)
)

# 对验证集翻转因子标记警告（不硬排除，降权处理）
summary["stability_weight"] = np.where(
    summary["dir_flip_valid"].fillna(False), 0.5, 1.0
)
```

> 注：验证集翻转选择"降权而非硬排除"，原因是验证集仅 11 期、统计检验力不足（见问题 D），翻转结论可信度有限，硬排除风险较大。

---

## 问题 H — Forward Return 使用原始收益而非超额收益 ✅

### 现象

`src/evaluation/ic_analysis.py`（第 218 行）计算方式：

```python
# 当前实现：原始股票收益
fwd = open_pivot.loc[exit_date] / open_pivot.loc[entry_date] - 1
```

使用的是**个股原始月收益**，未剔除中证 500 指数的系统性收益（Beta 部分）。

### 影响

- IC 中混入了 Beta 信息：若某因子与市场 Beta 相关（如低波动因子在熊市跑赢），IC 会被高估
- 对于中证 500 增强策略，因子的核心价值在于**截面选股能力**，应剔除指数影响
- 影响量级：在趋势明显的年份（2020–2021 上涨、2022 下跌），偏差可能达到 0.01–0.03 IC 单位

### 解决方案

**修改 `compute_forward_returns()` 增加超额收益选项**：

```python
def compute_forward_returns(
    rebalance_dates: list[pd.Timestamp],
    codes: list[str],
    codes_by_date: Optional[dict[pd.Timestamp, list[str]]] = None,
    benchmark_code: Optional[str] = "000905.SH",   # 新增参数：中证500全收益指数
) -> pd.DataFrame:
    """
    ...
    Args:
        benchmark_code: 基准指数代码（使用全收益指数）。提供时返回超额收益；
                        None 则返回原始收益（向后兼容）。
    """
    # ... 原有逻辑不变 ...

    # 计算超额收益
    if benchmark_code is not None:
        bm_dq = load_index_daily(benchmark_code, start_load, end_load)
        bm_pivot = bm_dq["close_adj"]   # 全收益指数直接用收盘价计算涨幅
        for T, entry_date, exit_date in entry_exit_pairs:
            fwd_raw = open_pivot.loc[exit_date] / open_pivot.loc[entry_date] - 1
            bm_ret = bm_pivot.loc[exit_date] / bm_pivot.loc[entry_date] - 1
            result[T] = fwd_raw - bm_ret   # 超额收益
    else:
        # 原有逻辑
        for T, entry_date, exit_date in entry_exit_pairs:
            result[T] = open_pivot.loc[exit_date] / open_pivot.loc[entry_date] - 1
```

> **重要**：基准必须使用**中证500全收益指数**（含分红再投资），不能用价格指数，否则会系统性高估 2–3% 的年化超额收益。

**优先级说明**：此问题在 P3 级别，建议在问题 A、G 修复并重跑因子检验后再处理，避免重复计算。

---

## 行动计划

按依赖顺序排列，前置任务完成后才能进行后续任务：

```
[Step 1] 修复问题 A — 调整陈旧度阈值
         ↓ 重新生成因子面板（Parquet 覆盖）
[Step 2] 修复问题 G — 完善 final_include 筛选逻辑
         ├── 实现 filter_redundant_factors()（解决 B/C）
         └── 加入 Sharpe 阈值和稳定性权重
         ↓ 重新运行 02_factor_evaluation.ipynb
[Step 3] 评估问题 D/E/F — 分段 IC_IR 对比
         确认调整后各因子在不同市场状态下的表现
[Step 4] 修复问题 H — 切换为超额收益（可选，视 Step 3 结论决定必要性）
```

> **测试集纪律提示**：以上所有调整均在训练集（2016–2021）和验证集（2022）范围内进行。测试集（2023–2025）在完成 Step 3 评估并确认因子质量稳健后，方可运行，且运行次数不超过 2 次（`[TEST_SET_RUN_N]` 标记）。
