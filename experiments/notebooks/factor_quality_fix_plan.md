# 因子质量问题修复方案

> 基于 `factor_quality_issues.md` 分析  
> 撰写日期：2026-05-11  
> 执行顺序：严格按 Step 顺序，前置步骤未完成不进行后续步骤

---

## 总览

| 步骤 | 解决问题 | 涉及文件 | 是否需要重跑数据 |
|------|----------|----------|-----------------|
| Step 1 | A — 覆盖率偏低 | `src/data/pit_loader.py` | ✅ 是（因子面板） |
| Step 2 | B/C/G — 冗余未过滤 + 筛选不完整 | `src/evaluation/ic_analysis.py` + Notebook | ❌ 否（利用新面板重跑评估） |
| Step 3 | D/F — 验证集检验力 + 风格污染评估 | `src/config.py` + Notebook | ⚠️ 可选（若调整验证窗口） |
| Step 4 | E — 因子稳定性权重 | `src/signal/combiner.py` | ❌ 否（合成阶段处理） |
| Step 5 | H — 超额收益 | `src/evaluation/ic_analysis.py` | ✅ 是（需重算 forward return） |

---

## Step 1：修复覆盖率偏低（问题 A）

### 修改文件：`src/data/pit_loader.py`

**当前代码（第 35 行）：**

```python
STALE_THRESHOLD_MONTHS: int = 18
```

**修改为：**

```python
STALE_THRESHOLD_MONTHS: int = 9
```

**理由**：A 股季度财报（Q1→H1→Q3→Annual）最大间隔为 6 个月，18 个月阈值远超正常披露周期，大量合法数据被误过滤。改为 9 个月保留充足安全边际（覆盖迟报情形）的同时，预计可将覆盖率从 ~43% 提升至 ≥ 85%。

> **注意**：`get_pit_latest()` 的 `stale_months` 参数默认值引用了这个常量，无需单独修改。

### 验证方法

修改后在 notebook 中执行对比：

```python
# 运行 01_factor_pipeline.ipynb（或 pipeline 脚本）重新生成因子面板后
coverage_new = factor_panel.notna().mean(axis=1).mean()
print(f"修复后覆盖率：{coverage_new:.1%}")  # 预期 ≥ 85%
```

### 后续动作

修改完成后必须**重新运行因子生成流水线**（`01_factor_pipeline.ipynb` 或等效脚本），覆盖存量 Parquet 文件，再进行 Step 2。

---

## Step 2：修复高相关冗余过滤 + 完善 final_include（问题 B/C/G）

### 2a. 新增函数：`src/evaluation/ic_analysis.py`

在 `batch_ic_test` 函数之后新增 `filter_redundant_factors()`：

```python
def filter_redundant_factors(
    summary: pd.DataFrame,
    corr_matrix: pd.DataFrame,
    corr_threshold: float = 0.70,
) -> list[str]:
    """
    贪心过滤高相关冗余因子。

    按 |IC_IR| 降序遍历已通过 IC 检验的因子，若当前因子与已纳入因子
    的任意一个相关系数（绝对值）> corr_threshold，则丢弃（保留 IC_IR 更强的）。

    Args:
        summary:        batch_ic_test 输出的因子汇总 DataFrame（index=因子名）
        corr_matrix:    compute_factor_correlation 输出的截面平均相关矩阵
        corr_threshold: 高相关阈值，默认 0.70
    Returns:
        去冗余后保留的因子名列表（保持 |IC_IR| 降序）
    """
    candidates = (
        summary[summary["effective_ic"]]
        .sort_values("ic_ir", key=abs, ascending=False)
        .index.tolist()
    )

    selected: list[str] = []
    for factor in candidates:
        if not selected:
            selected.append(factor)
            continue
        # 只与 corr_matrix 中存在的已选因子比较
        available = [f for f in selected if f in corr_matrix.columns]
        if not available:
            selected.append(factor)
            continue
        max_corr = corr_matrix.loc[factor, available].abs().max()
        if max_corr <= corr_threshold:
            selected.append(factor)
        else:
            log.debug("过滤冗余因子 %s（与已选因子最高相关 %.2f > %.2f）", factor, max_corr, corr_threshold)

    return selected
```

### 2b. 修改 Notebook：`02_factor_evaluation.ipynb`

找到第 3537–3538 行的 `final_include` 赋值，替换为：

```python
# ── Step 2b：完整的因子筛选逻辑 ──────────────────────────────────────────
# 2b-1：去冗余（解决问题 B/C）
deduplicated_factors = filter_redundant_factors(summary, corr_matrix, corr_threshold=0.70)

# 2b-2：综合筛选（解决问题 G）
summary["final_include"] = (
    # 条件 1：IC 显著有效（原有）
    summary["effective_ic"]
    # 条件 2：无未来函数警告（原有）
    & ~summary["shift_warning"].fillna(False)
    # 条件 3：去冗余后保留（新增）
    & summary.index.isin(deduplicated_factors)
    # 条件 4：方向性 Sharpe 合格（新增）
    & (summary["directional_sharpe_ls"].abs() >= 0.50)
)

# 2b-3：对验证集翻转因子降权（不硬排除，因验证集仅 11 期检验力不足）
summary["stability_weight"] = summary["dir_flip_valid"].fillna(False).map({True: 0.5, False: 1.0})

print(f"筛选结果：{summary['final_include'].sum()} 个因子入选（共 {len(summary)} 个）")
print("\n被过滤的因子：")
excluded = summary[~summary["final_include"]]
for idx, row in excluded.iterrows():
    reasons = []
    if not row["effective_ic"]:        reasons.append("IC 不显著")
    if row.get("shift_warning"):       reasons.append("时间错位警告")
    if idx not in deduplicated_factors: reasons.append("与高相关因子冗余")
    if abs(row.get("directional_sharpe_ls", 0)) < 0.50: reasons.append("Sharpe 不足")
    print(f"  {idx}: {' + '.join(reasons)}")
```

### 预期的冗余过滤结果

根据 issue 报告中的相关矩阵，以下因子预计在 `corr_threshold=0.70` 下被过滤：

| 被过滤因子 | 保留因子 | 平均相关 | 过滤原因 |
|------------|----------|----------|----------|
| `vol_60d` | `ivol_60d` | +0.93 | ivol_60d IC_IR 更高且剥离系统性风险 |
| `roe` | `q_roe` | +0.91 | q_roe IC_IR 更高（+0.587 vs +0.495） |
| `roa` | `q_roe` | +0.91 | 同上 |
| `roe_delta` | `np_yoy` | +0.81 | np_yoy 覆盖更广 |

> 若 `q_roe` 在 Step 3 分析后被判定为稳定性不足，可用 `roa` 替换，届时 `roe` 也随之被过滤。

---

## Step 3：评估验证集检验力与风格污染（问题 D/F）

此步骤**不修改代码**，仅在 notebook 中新增分析单元。

### 3a. 验证集检验力分析（问题 D）

```python
# 在 02_factor_evaluation.ipynb 中新增分析单元
import scipy.stats as sst
import warnings

n_valid = len(valid_ic_series)  # 验证集有效期数
ci_half = 1.96 / n_valid**0.5
print(f"验证集有效期数 n = {n_valid}")
print(f"IC_IR 95% CI 宽度 = ±{ci_half:.3f}")
if n_valid < 24:
    warnings.warn(
        f"验证集期数 n={n_valid} 不足 24，置信区间宽达 ±{ci_half:.2f}，"
        "方向翻转结论不可靠，仅作参考，不建议据此硬排除因子。"
    )
```

### 3b. 分段 IC_IR 评估（问题 F）

```python
# 在 02_factor_evaluation.ipynb 中新增分析单元
periods = {
    "2016-2018（去杠杆前）": ("2016-01-01", "2018-12-31"),
    "2019-2021（茅指数牛市）": ("2019-01-01", "2021-12-31"),
}

for factor_name in summary[summary["final_include"]].index:
    ic_s = ic_series_map[factor_name]
    print(f"\n── {factor_name} ──")
    for label, (start, end) in periods.items():
        sub = ic_s.loc[start:end].dropna()
        if len(sub) == 0:
            print(f"  {label}: 无数据")
            continue
        sub_ir = sub.mean() / sub.std(ddof=1) if sub.std(ddof=1) > 0 else float("nan")
        print(f"  {label}: IC_IR={sub_ir:.3f}  n={len(sub)}  IC均值={sub.mean():.3f}")
```

**判断标准**：若某因子在 2016–2018 和 2019–2021 两段 IC_IR 方向一致，说明效果稳定；若仅靠 2019–2021 贡献，应在 Step 4 的 `stability_weight` 中进一步降权（由 0.5 降至 0.3）。

### 3c. 是否调整验证集窗口（问题 D 的根本解法）

当前配置（`src/config.py`）：

```
VALID_END  = 2022-12-31   # 仅 11 期
TEST_START = 2023-01-01
```

**推荐方案**（若评估后认为验证集统计力严重不足）：

```python
# src/config.py
VALID_END  = pd.Timestamp("2024-12-31")  # 扩展至 35 期
TEST_START = pd.Timestamp("2025-01-01")  # 测试集缩减至约 12 期
```

> **执行前须确认**：测试集期数 ≥ 6 期才有统计意义。当前调整后测试集约 12 期（2025 全年），满足条件。此调整需要**重跑评估流水线**。

---

## Step 4：合成阶段加入稳定性权重（问题 E）

此步骤在 **Step 2** 已通过 `stability_weight` 列做了标记，在合成器中落地：

### 修改文件：`src/signal/combiner.py`

找到 IC_IR 加权合成逻辑，加入稳定性权重：

```python
def combine_factors(
    factor_panels: dict[str, pd.DataFrame],
    ic_ir_weights: dict[str, float],
    stability_weights: dict[str, float] | None = None,   # 新增参数
) -> pd.DataFrame:
    """
    IC_IR 加权合成，支持额外的稳定性权重调整。

    Args:
        factor_panels:     dict[因子名, 因子面板]
        ic_ir_weights:     dict[因子名, IC_IR 绝对值]
        stability_weights: dict[因子名, 稳定性系数 ∈ (0, 1]]；None 表示不做额外调整
    Returns:
        合成因子面板（行=调仓日, 列=ts_code）
    """
    effective_weights = {}
    for name, ic_ir in ic_ir_weights.items():
        stab = stability_weights.get(name, 1.0) if stability_weights else 1.0
        effective_weights[name] = abs(ic_ir) * stab

    # 归一化
    total = sum(effective_weights.values())
    if total == 0:
        raise ValueError("所有因子权重为零，无法合成")
    norm_weights = {k: v / total for k, v in effective_weights.items()}

    # 加权合成
    result = None
    for name, w in norm_weights.items():
        signed_panel = factor_panels[name] * (1 if ic_ir_weights[name] >= 0 else -1)
        if result is None:
            result = signed_panel * w
        else:
            result = result.add(signed_panel * w, fill_value=0)

    return result
```

**在 notebook 中调用方式**：

```python
# 从 Step 2b 的 summary 提取稳定性权重
stability_weights = summary[summary["final_include"]]["stability_weight"].to_dict()

# 合成（dir_flip_valid=True 的因子自动获得 0.5 权重）
composite_factor = combine_factors(
    factor_panels={k: v for k, v in factor_panels_train.items() if k in final_factors},
    ic_ir_weights=ic_ir_weights,
    stability_weights=stability_weights,
)
```

---

## Step 5：切换 Forward Return 为超额收益（问题 H）

> **执行条件**：Step 1–4 完成并确认因子质量后再执行，避免重复计算。

### 修改文件：`src/evaluation/ic_analysis.py`

修改 `compute_forward_returns()` 签名和实现，增加超额收益选项：

```python
def compute_forward_returns(
    rebalance_dates: list[pd.Timestamp],
    codes: list[str],
    codes_by_date: Optional[dict[pd.Timestamp, list[str]]] = None,
    benchmark_code: Optional[str] = "000905.SH",  # 新增：默认中证500全收益指数
) -> pd.DataFrame:
    """
    计算 T+1 开盘买入、T'+1 开盘卖出的月度收益率面板。

    Args:
        ...（原有参数不变）...
        benchmark_code: 基准指数代码（必须使用全收益指数）。
                        提供时返回超额收益（个股收益 - 基准收益）；
                        None 返回原始收益（向后兼容）。
    Returns:
        行=调仓日, 列=ts_code 的月度（超额）收益面板
    """
    # ── 原有逻辑：构造 entry_exit_pairs、加载 open_pivot ── （不变）

    # ── 新增：加载基准收益 ──
    bm_ret_series: dict[pd.Timestamp, float] = {}
    if benchmark_code is not None:
        from src.data.loader import load_index_daily
        bm_dq = load_index_daily(benchmark_code, start_load, end_load)
        # 全收益指数用收盘价计算涨幅（含分红再投资）
        bm_close = bm_dq["close"].sort_index()
        for T, entry_date, exit_date in entry_exit_pairs:
            if entry_date in bm_close.index and exit_date in bm_close.index:
                bm_ret_series[T] = bm_close.loc[exit_date] / bm_close.loc[entry_date] - 1
            else:
                bm_ret_series[T] = 0.0  # 降级为原始收益
                log.warning("基准指数在 %s 或 %s 无数据，该期不扣除基准", entry_date.date(), exit_date.date())

    # ── 计算截面收益 ──
    result: dict[pd.Timestamp, pd.Series] = {}
    for T, entry_date, exit_date in entry_exit_pairs:
        if entry_date not in open_pivot.index or exit_date not in open_pivot.index:
            log.warning("%s 或 %s 不在 daily_quote 中", entry_date.date(), exit_date.date())
            continue
        fwd = open_pivot.loc[exit_date] / open_pivot.loc[entry_date] - 1
        if benchmark_code is not None:
            fwd = fwd - bm_ret_series.get(T, 0.0)  # 转换为超额收益
        result[T] = fwd

    # ── 后续不变 ──
    ...
```

**注意事项**：

- 基准代码 `000905.SH` 是中证 500 **全收益指数**（含分红），不是 `000905` 价格指数。
- 使用原始收益的历史结果会在超额收益模式下系统性下移，IC 均值会略降（正常现象，因 Beta 被剔除）。
- `benchmark_code=None` 保持向后兼容，现有测试不受影响。

### 验证方法

```python
# 对比两种模式的 IC 均值
fwd_raw    = compute_forward_returns(dates, codes, benchmark_code=None)
fwd_excess = compute_forward_returns(dates, codes, benchmark_code="000905.SH")

ic_raw    = compute_ic_series(q_roe_panel, fwd_raw)
ic_excess = compute_ic_series(q_roe_panel, fwd_excess)

print(f"原始收益 IC_IR = {ic_raw.mean()/ic_raw.std():.3f}")
print(f"超额收益 IC_IR = {ic_excess.mean()/ic_excess.std():.3f}")
# 预期：IC_IR 略降（0.01–0.03 IC 单位），方向不变
```

---

## 执行检查清单

按顺序勾选，每步完成后打 ✅ 再进行下一步：

```
[ ] Step 1：修改 STALE_THRESHOLD_MONTHS = 9
[ ] Step 1：重跑因子面板生成流水线，验证覆盖率 ≥ 85%
[ ] Step 2a：在 ic_analysis.py 新增 filter_redundant_factors()
[ ] Step 2b：更新 notebook final_include 逻辑
[ ] Step 2：确认最终入选因子数量（预期 16–22 个）
[ ] Step 3a：在 notebook 新增验证集检验力分析单元
[ ] Step 3b：在 notebook 新增分段 IC_IR 评估单元
[ ] Step 3c：根据分析结论决定是否调整验证集窗口
[ ] Step 4：在 combiner.py 加入 stability_weights 参数
[ ] Step 4：在 notebook 用稳定性权重重新合成因子
[ ] Step 5（可选）：修改 compute_forward_returns 支持超额收益
[ ] Step 5（可选）：重跑全部 IC 检验，对比前后差异
```

---

## 风险提示

1. **测试集纪律**：以上所有修改均在训练集（2016–2021）和验证集（2022 或调整后的 2022–2024）范围内进行。测试集（2023–2025 或调整后的 2025）**严禁在此阶段运行**。

2. **覆盖率提升的双重效应**：Step 1 修复后，覆盖率提升会改变 IC 的计算样本。预期 IC 均值略有变化（扩大了中小盘/迟报股的参与），因子排名可能微调，需重新执行完整筛选流程。

3. **验证集窗口调整（Step 3c）是一次性决策**：一旦调整后跑过评估，再改回去会引入数据窥探嫌疑。决定前应充分分析。

4. **冗余阈值 0.70 的敏感性**：若入选因子数量偏少（< 12 个），可适当放宽至 0.75；若冗余明显未被清理（> 25 个），收紧至 0.65。修改阈值视为超参，需在训练集评估中确认后固定。
