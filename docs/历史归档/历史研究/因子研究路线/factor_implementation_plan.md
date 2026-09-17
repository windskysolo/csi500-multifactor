# 因子扩充实施计划

> 创建日期：2026-05-25  
> 修订日期：2026-05-25（执行版修订）  
> 作者：Claude Code  
> 分支：`feature/expand-train-2012`  
> 依据：`consensus_factor_plan.md` + `factor_research_guide.md`（阶段一 P1-P3）  
> 状态：**可执行计划（已按当前仓库接口修订）**

---

## 零、执行版修订要点（必须遵守）

本计划已按当前仓库接口修订，可以作为执行清单使用。以下约束优先级高于旧表述：

1. **不触碰测试集**：所有命令只覆盖 `TRAIN_START ~ VALID_END`；不得传入 `--allow-test-set`，不得把 `--end-date` 设到 `2023-01-01` 或之后。
2. **先构建因子面板，再评估**：当前 `scripts/run_factor_evaluation.py` 没有 `--factor_names` 参数；新因子必须先通过 `scripts.build_factor_panels --factors ...` 写入训练/验证期面板，再用隔离目录运行全量评估。
3. **首次评估写入隔离目录**：阶段一输出到 `reports/factor_evaluation_factor_impl_stage1/`，阶段二输出到 `reports/factor_evaluation_factor_impl_stage2/`，不得直接覆盖主 `reports/factor_evaluation/final_factors.json`。
4. **必须更新 `scripts/build_factor_panels.py` 注册列表**：除 `_FACTOR_BUILDERS` / `_ALT_FACTOR_BUILDERS` 外，还要把新因子名加入 `FINANCIAL_FACTORS` 或 `ALT_FACTORS`，否则构建脚本会报“未知因子名”。
5. **与 `momentum_sprint_plan.md` 不重合**：本计划不修改 `src/factors/price_factors.py`，不新增 `high_52w_v2`、`ind_adj_mom_6_1`、`mom_consistency_6`；报告目录和 `run-id` 也与 momentum sprint 分开。

## 一、总览

### 1.1 目标

在不影响任何已有因子和现有流水线结果的前提下，分两阶段新增 6 个因子：

| 阶段 | 因子 | 数据依赖 | 实现文件 |
|------|------|---------|---------|
| 阶段一 | C1 `eps_dispersion` | 现有 `analyst_rc_pit.parquet` | `alt_factors.py` |
| 阶段一 | C2 `analyst_cnt_chg` | 现有 `analyst_rc_pit.parquet` | `alt_factors.py` |
| 阶段一 | P2-C `roe_stability` | 现有 `indicator_pit.parquet` | `financial_factors.py` |
| 阶段一 | P3-B `rev_acceleration` | 现有 `indicator_pit.parquet` | `financial_factors.py` |
| 阶段二 | C3 `np_forecast_growth` | 重跑 M14 → 新增 `np`、`quarter` 列 | `alt_factors.py` |
| 阶段二 | C5 `eps_revision_v2` | 重跑 M14 → 新增 `quarter` 列 | `alt_factors.py` |

> C4 `op_rt_forecast_growth`：重跑 M14 后根据 Gate 0 覆盖率结果决定是否实现，不列入本计划。

### 1.2 变动文件汇总

| 文件 | 变动类型 | 说明 |
|------|---------|------|
| `src/factors/alt_factors.py` | 追加函数 + 追加 `_ALT_FACTOR_BUILDERS` 条目 | 不修改任何现有函数和 key |
| `src/factors/financial_factors.py` | 追加函数 + 追加 `_FACTOR_BUILDERS` 条目 | 不修改任何现有函数和 key |
| `scripts/build_factor_panels.py` | 追加 `FINANCIAL_FACTORS` / `ALT_FACTORS` 条目 | 只加入本计划新增因子，不碰 momentum 因子 |
| `scripts/csv_to_parquet.py` | 修改 M14 的两处列表 | 仅扩展 `keep` 列表和数值转换循环，不删除任何字段 |

---

## 二、安全隔离保证

### 2.1 三条不变性

1. **函数不变性**：所有现有 `factor_xxx` 函数一行不动，新函数只追加在文件末尾。  
2. **注册不变性**：`_ALT_FACTOR_BUILDERS` 和 `_FACTOR_BUILDERS` 字典只添加新 key，不修改或删除已有 key。已有因子的入池状态由 `final_factors.json` 控制，与注册字典无关。  
3. **parquet 不变性**：M14 重跑后 `analyst_rc_pit.parquet` 只新增 3 列（`np`、`op_rt`、`quarter`），原有字段（`eps`、`rating`、`org_name` 等）**完全不变**，`analyst_eps_revision` 因子不受任何影响。

### 2.2 新因子与现有 pipeline 的关系

- `run_pipeline.py` 读取 `final_factors.json` 中的因子列表，新因子不在列表里，**不会进入主流水线**。  
- 新因子评估不使用不存在的 `--factor_names` 参数；必须先运行 `build_factor_panels --factors ...`，再用 `run_factor_evaluation --output-dir ...` 写入隔离目录。  
- `run_factor_evaluation.py` 会扫描 `data/processed/factor_panels/` 下全部已有面板；若 momentum sprint 因子也已构建，它们会出现在同一份评估结果中。判读时只关注本计划目标因子，且隔离目录不会覆盖主线产物。  
- 只有在评估通过四道 Gate 并由用户确认后，才允许刷新主 `reports/factor_evaluation/final_factors.json`；刷新必须通过 `run_factor_evaluation.py` 生成 metadata，不手写 JSON。

### 2.3 与 momentum sprint 的边界

| 项目 | 本计划 | `momentum_sprint_plan.md` |
|------|--------|---------------------------|
| 因子名 | `eps_dispersion`、`analyst_cnt_chg`、`roe_stability`、`rev_acceleration`、`np_forecast_growth`、`eps_revision_v2` | `high_52w_v2`、`ind_adj_mom_6_1`、`mom_consistency_6` |
| 因子实现文件 | `alt_factors.py`、`financial_factors.py` | `price_factors.py` |
| 构建脚本列表 | `FINANCIAL_FACTORS`、`ALT_FACTORS` | `PRICE_FACTORS` |
| 初评报告目录 | `reports/factor_evaluation_factor_impl_stage1/`、`reports/factor_evaluation_factor_impl_stage2/` | `reports/factor_evaluation_momentum_sprint/` |
| 初评 run-id | `factor_impl_stage1`、`factor_impl_stage2` | `momentum_sprint_s1` |

两份计划唯一共同修改点是 `scripts/build_factor_panels.py`，但修改的是不同列表。若两份计划同时实施，合并时只追加各自因子名，不删除对方新增项。

重要：主 `reports/factor_evaluation/final_factors.json` 只能在所有已构建因子的入池决策合并后刷新。因为 `run_factor_evaluation.py` 会扫描全部 `factor_panels/*.parquet`，如果 momentum 因子已经构建，主刷新会同时评估并可能纳入它们。未完成合并决策前，只使用隔离目录评估。

---

## 三、阶段一：零成本因子（无需任何数据准备）

### 3.1 前置确认

运行以下命令确认数据文件存在且字段完整：

```powershell
python -c "
import pandas as pd
# 确认 analyst_rc_pit
rc = pd.read_parquet('data/processed/analyst_rc_pit.parquet')
print('analyst_rc_pit 字段:', rc.columns.tolist())
print('eps 非空率:', rc['eps'].notna().mean())
print('org_name 非空率:', rc['org_name'].notna().mean())

# 确认 indicator_pit
ip = pd.read_parquet('data/processed/indicator_pit.parquet')
print()
print('indicator_pit 字段:', ip.columns.tolist())
print('q_dt_roe 非空率:', ip['q_dt_roe'].notna().mean())
print('or_yoy 非空率:', ip['or_yoy'].notna().mean())
"
```

期望结果：`eps`、`org_name`、`q_dt_roe`、`or_yoy` 均存在且非空率 > 50%。

---

### 3.2 C1：EPS 分散度（`eps_dispersion`）

**实现位置**：`src/factors/alt_factors.py`，追加在 `factor_analyst_rating_chg` 函数之后。

**新增常量**（追加在文件顶部常量区，紧跟 `MIN_ANALYST_CNT` 之后）：

```python
MIN_DISPERSION_CNT = 3  # eps_dispersion 要求的最少不同机构研报数
```

**函数实现**：

```python
def factor_eps_dispersion(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    分析师 EPS 预测分散度：-std(EPS) / |median(EPS)|，取负。

    分散度越高表示分析师分歧越大，盈利不确定性越强。
    根据 Miller(1977) 异质信念理论，A 股做空受限下高分歧股票被乐观者高估，
    后续跑输概率更高，故取负号使因子方向为正向（低分歧 → 正超额）。

    时间对齐：仅使用 pit_date ∈ (T-180d, T] 的研报，无未来数据。
    口径：同一机构多次发报只取窗口内最新 EPS，避免单一机构高频发报被重复加权。

    Args:
        rebalance_date: 调仓日 T
        codes:          可投资股票代码列表
    Returns:
        ts_code → eps_dispersion；有效机构数 < MIN_DISPERSION_CNT 时为 NaN
    """
    rc = load_analyst_rc_pit(rebalance_date, codes=codes, lookback_days=WINDOW_ANALYST)
    if rc.empty or "eps" not in rc.columns or "org_name" not in rc.columns:
        return _nan_series(codes, "eps_dispersion")

    rc = rc.copy()
    rc["eps"] = pd.to_numeric(rc["eps"], errors="coerce")
    rc = rc.dropna(subset=["eps", "org_name"])
    if rc.empty:
        return _nan_series(codes, "eps_dispersion")

    # 每只股票、每家机构只保留窗口内最新预测，确保 MIN_DISPERSION_CNT 是“机构数”而非研报条数。
    rc_latest = (
        rc.sort_values("pit_date")
        .drop_duplicates(subset=["ts_code", "org_name"], keep="last")
    )

    def _dispersion(grp: pd.Series) -> float:
        valid = grp.dropna()
        if len(valid) < MIN_DISPERSION_CNT:
            return np.nan
        std = float(valid.std(ddof=1))
        med = float(valid.median())
        if abs(med) < 1e-8:   # EPS 中位数接近 0，无法标准化
            return np.nan
        return std / abs(med)

    disp = rc_latest.groupby("ts_code")["eps"].apply(_dispersion)
    result = -disp          # 高分散度 → 负向信号 → 取负使其正向
    result.name = "eps_dispersion"
    return result.reindex(codes)
```

**注册到 `_ALT_FACTOR_BUILDERS`**（追加在 `"analyst_rating_chg"` 条目之后）：

```python
"eps_dispersion":       factor_eps_dispersion,
```

---

### 3.3 C2：分析师覆盖数变化（`analyst_cnt_chg`）

**实现位置**：`src/factors/alt_factors.py`，追加在 `factor_eps_dispersion` 之后。

**函数实现**：

```python
def factor_analyst_cnt_chg(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    分析师机构覆盖数变化：近 90 天覆盖机构数 - 前 90 天覆盖机构数。

    使用 org_name 去重计数（同一机构多次发报只算 1 次），
    衡量机构关注度的增减趋势。覆盖数增加 → 信息摩擦降低 → 估值折价收窄。

    时间对齐：
      近期 = (T-90d, T]；远期 = (T-180d, T-90d]
      无事件时该区间计数为 0（fill_value=0），不返回 NaN。

    Args:
        rebalance_date: 调仓日 T
        codes:          可投资股票代码列表
    Returns:
        ts_code → 覆盖机构数变化（整数差值，可为负）；
        两段均无覆盖的股票返回 NaN（区分"无覆盖"与"变化为 0"）
    """
    rc = load_analyst_rc_pit(rebalance_date, codes=codes, lookback_days=WINDOW_ANALYST)
    if rc.empty or "org_name" not in rc.columns:
        return _nan_series(codes, "analyst_cnt_chg")

    split_date = rebalance_date - pd.Timedelta(days=ANALYST_SPLIT)
    recent = rc[rc["pit_date"] >  split_date]
    prior  = rc[rc["pit_date"] <= split_date]

    recent_cnt = recent.groupby("ts_code")["org_name"].nunique()
    prior_cnt  = prior.groupby("ts_code")["org_name"].nunique()

    # 在宇宙内有覆盖的股票才做差值；完全无覆盖的保持 NaN
    has_coverage = recent_cnt.index.union(prior_cnt.index)
    chg = recent_cnt.reindex(has_coverage, fill_value=0).sub(
        prior_cnt.reindex(has_coverage, fill_value=0)
    ).astype(float)

    chg.name = "analyst_cnt_chg"
    return chg.reindex(codes)
```

**注册到 `_ALT_FACTOR_BUILDERS`**：

```python
"analyst_cnt_chg":      factor_analyst_cnt_chg,
```

---

### 3.4 P2-C：ROE 稳定性（`roe_stability`）

**实现位置**：`src/factors/financial_factors.py`，追加在 `factor_roe_delta_3q` 之后。

**新增常量**（追加在文件顶部，`_WAN_TO_YUAN` 旁边）：

```python
MIN_QUARTERS_STABILITY = 4   # roe_stability 要求最少有效季度数
```

**函数实现**：

```python
def factor_roe_stability(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    ROE 稳定性因子：过去 8 季度扣非季度 ROE 的标准差，取负。

    稳定的高 ROE 说明公司具备持久竞争优势（护城河），
    波动的 ROE 往往来自周期性峰值或会计操纵，预期未来回归均值。
    使用 q_dt_roe（扣非季度 ROE）排除非经常性损益干扰。

    时间对齐：
      get_quarterly_history 内部已做 pit_date <= T 过滤（严格 PIT）。
      n_quarters=8 取最近 8 个报告期，陈旧度由 STALE_THRESHOLD_MONTHS 控制。

    Args:
        rebalance_date: 调仓日 T
        codes:          可投资股票代码列表
    Returns:
        ts_code → roe_stability（越高越稳定）；
        有效期数 < MIN_QUARTERS_STABILITY 时为 NaN
    """
    ip_raw = get_indicator_pit_raw()
    hist   = get_quarterly_history(
        ip_raw, "q_dt_roe", rebalance_date, codes, n_quarters=8
    )

    def _row_std(row: pd.Series) -> float:
        valid = row.dropna()
        if len(valid) < MIN_QUARTERS_STABILITY:
            return np.nan
        return float(valid.std(ddof=1))

    std_series  = hist.apply(_row_std, axis=1)
    result      = -std_series   # 取负：低波动 → 高因子值 → 正超额
    result.name = "roe_stability"
    return result.reindex(codes)
```

**注册到 `_FACTOR_BUILDERS`**（追加在 `"roe_delta_3q"` 条目之后）：

```python
"roe_stability":    factor_roe_stability,
```

---

### 3.5 P3-B：营收加速度（`rev_acceleration`）

**实现位置**：`src/factors/financial_factors.py`，追加在 `factor_roe_stability` 之后。

**函数实现**：

```python
def factor_rev_acceleration(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    营收增速加速度：当期同比增速 - 4 个报告期前同比增速。

    规避现有 rev_yoy 的高基数效应缺陷：
      rev_yoy 在 2021 年因对比 2020 年疫情低基数而虚高，导致验证期失效。
      加速度指标的双差法抵消了绝大部分基数干扰，关注增速趋势而非水平。

    时间对齐：
      get_quarterly_history 内部做 pit_date <= T 过滤（严格 PIT）。
      or_yoy 为 Tushare indicator 预计算的营收同比增速（百分比）。
      q0 = 最新报告期，q4 = 4 个报告期前（约 1 年前）。

    Args:
        rebalance_date: 调仓日 T
        codes:          可投资股票代码列表
    Returns:
        ts_code → rev_acceleration（百分点差值）；
        q0 或 q4 任一为 NaN 时返回 NaN
    """
    ip_raw = get_indicator_pit_raw()
    hist   = get_quarterly_history(
        ip_raw, "or_yoy", rebalance_date, codes, n_quarters=5
    )

    result      = hist["q0"] - hist["q4"]   # 当期增速 - 4期前增速
    result.name = "rev_acceleration"
    return result.reindex(codes)
```

**注册到 `_FACTOR_BUILDERS`**（追加在 `"roe_stability"` 条目之后）：

```python
"rev_acceleration": factor_rev_acceleration,
```

---

### 3.6 构建脚本注册（必须做）

修改 `scripts/build_factor_panels.py`：

**`FINANCIAL_FACTORS` 追加**（放在阶段 4 或阶段 5 附近均可，保持顺序稳定）：

```python
"roe_stability", "rev_acceleration",
```

**`ALT_FACTORS` 追加**（放在 `"analyst_rating_chg"` 之后）：

```python
"eps_dispersion", "analyst_cnt_chg",
```

完成后先执行 dry-run，确认脚本识别新因子且不触碰测试集：

```powershell
python -m scripts.build_factor_panels --dry-run --factors eps_dispersion analyst_cnt_chg roe_stability rev_acceleration
```

期望：输出调仓日数量为当前 `TRAIN_START ~ VALID_END` 的训练/验证期日期（当前配置约 132 期），目标因子列表只包含上述 4 个因子，无“未知因子名”错误。

---

### 3.7 阶段一验证 Checklist

完成代码追加后，按顺序执行：

**Step 1：导入冒烟测试（30 秒内完成）**

```powershell
python -c "
from src.factors.alt_factors import factor_eps_dispersion, factor_analyst_cnt_chg
from src.factors.financial_factors import factor_roe_stability, factor_rev_acceleration
import pandas as pd

t = pd.Timestamp('2020-01-31')
codes = ['000001.SZ', '000063.SZ', '600036.SH', '601318.SH', '002352.SZ']

for name, fn in [
    ('eps_dispersion',  factor_eps_dispersion),
    ('analyst_cnt_chg', factor_analyst_cnt_chg),
    ('roe_stability',   factor_roe_stability),
    ('rev_acceleration',factor_rev_acceleration),
]:
    s = fn(t, codes)
    nan_n = s.isna().sum()
    print(f'{name:25s}  非NaN: {len(codes)-nan_n}/{len(codes)}  值域: [{s.min():.3f}, {s.max():.3f}]')
"
```

期望输出：每个因子至少 1 个非 NaN 值，无 Python 报错。

**Step 2：回归确认现有因子不受影响**

```powershell
python -c "
from src.factors.alt_factors import factor_analyst_eps_revision
import pandas as pd
t = pd.Timestamp('2020-01-31')
codes = ['000001.SZ', '600036.SH']
s = factor_analyst_eps_revision(t, codes)
print('analyst_eps_revision OK:', s.to_dict())
"
```

期望：与修改前输出完全一致（无变化）。

**Step 3：构建阶段一新因子面板（训练集 + 验证集）**

```powershell
python -m scripts.build_factor_panels --factors eps_dispersion analyst_cnt_chg roe_stability rev_acceleration
```

**Step 4：运行隔离目录评估（训练集 + 验证集，约 10-20 分钟）**

```powershell
python -m scripts.run_factor_evaluation --recompute-fwd-ret --run-id factor_impl_stage1 --output-dir reports/factor_evaluation_factor_impl_stage1
```

评估完成后，对照四道 Gate 标准：

| Gate | 指标 | 要求 | 备注 |
|------|------|------|------|
| Gate 0 | 覆盖率 | ≥ 80% | 每期有效股票数 / 宇宙规模 |
| Gate 1 | IC_IR（训练集）| ≥ 0.30，t ≥ 2.0 | 训练期 2012-2020 |
| Gate 2 | lead_ratio | < 1.5x 直接通过；1.5-2.0x 待议；≥ 2.0x 拒绝 | 未来函数核心检测 |
| Gate 3 | 验证期 IC_IR | ≥ 0.20，方向不变 | 验证期 2021-2022 |

检查文件：

```text
reports/factor_evaluation_factor_impl_stage1/ic_result.csv
reports/factor_evaluation_factor_impl_stage1/shift_result.csv
reports/factor_evaluation_factor_impl_stage1/seg_ic_ir.csv
reports/factor_evaluation_factor_impl_stage1/factor_summary.csv
```

注意：如果 momentum sprint 的因子面板已经存在，评估结果会同时包含这些因子。阶段一判读只看 `eps_dispersion`、`analyst_cnt_chg`、`roe_stability`、`rev_acceleration`。

---

## 四、阶段二：parquet 重跑 + 一致预期因子

> **前提条件**：阶段一的 4 个因子完成评估，确认流水线无异常后再执行。

### 4.1 修改 `scripts/csv_to_parquet.py`（M14 模块）

**定位行号**：L1455-L1460（`process_analyst_rc_pit` 函数内）

**修改一：扩展 keep 列表（L1455-L1456）**

原始代码：
```python
keep = ["pit_date", "ts_code", "org_name", "author_name", "eps", "pe", "rating",
        "report_title", "max_price", "min_price"]
```

修改为：
```python
keep = ["pit_date", "ts_code", "org_name", "author_name", "eps", "pe", "rating",
        "report_title", "max_price", "min_price", "np", "op_rt", "quarter"]
```

**修改二：扩展数值类型转换循环（L1458）**

原始代码：
```python
for col in ["eps", "pe"]:
```

修改为：
```python
for col in ["eps", "pe", "np", "op_rt"]:
```

> `quarter` 是字符串型（如 `"2023Q4"`），不做数值转换。

**重跑命令**：

```powershell
python -c "from scripts.csv_to_parquet import process_analyst_rc_pit; process_analyst_rc_pit()"
```

预计耗时：2-5 分钟（取决于 analyst_rc 目录的 CSV 体量）。

说明：当前 `scripts/csv_to_parquet.py` 的命令行接口只支持 `daily/index/industry/financial/status/universe/alternative/all`，没有 `--modules M14`。这里用定向函数调用，只重建 `analyst_rc_pit.parquet`，避免重跑全部 alternative 模块。

---

### 4.2 阶段二前置验证

```powershell
python -c "
import pandas as pd
df = pd.read_parquet('data/processed/analyst_rc_pit.parquet')
print('analyst_rc_pit 列:', df.columns.tolist())
print()
# 覆盖率检查
for col in ['eps', 'np', 'op_rt', 'quarter']:
    r = df[col].notna().mean()
    flag = '✅' if r > 0.40 else '⚠️'
    print(f'{flag} {col:12s}  非空率: {r:.1%}')
print()
# 原有因子安全确认：eps 覆盖率应与重跑前一致
print('eps 抽样:', df['eps'].dropna().head(3).tolist())
"
```

**覆盖率决策规则**：

| 字段 | 覆盖率阈值 | 决策 |
|------|----------|------|
| `np` + `quarter` | np ≥ 50% 且 quarter ≥ 80% | 实现 C3 `np_forecast_growth` |
| `np` + `quarter` | np 为 30-50% 且 quarter ≥ 80% | 实现 C3，但在 docstring 注明早期覆盖受限，Gate 0 按年份分段检查 |
| `np` | < 30% | 不实现 C3，记录原因 |
| `op_rt` | ≥ 40% | 实现 C4（不在本计划内，独立评估） |
| `quarter` | ≥ 80% | 实现 C5 `eps_revision_v2` |

---

### 4.3 C3：净利润预测增速（`np_forecast_growth`）

**实现位置**：`src/factors/alt_factors.py`，追加在 `factor_analyst_cnt_chg` 之后。

**函数实现**：

```python
def factor_np_forecast_growth(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    分析师净利润预测修正：同一预测期的 median(近期NP预测) / median(远期NP预测) - 1。

    与 analyst_eps_revision 逻辑一致，区别在于：
      - analyst_eps_revision 用 EPS（每股），受股本变化（增发/回购）影响
      - np_forecast_growth 用 NP（净利润总量，万元），反映公司整体盈利修正
    两者互补，若截面相关 |r| < 0.6 则同时保留。

    数据依赖：需 analyst_rc_pit.parquet 含 np 和 quarter 列（重跑 M14 后可用）。

    时间对齐：
      近期 = (T-90d, T]；远期 = (T-180d, T-90d]（与 eps_revision 完全一致）
      在 (ts_code, quarter) 上做 inner join，避免预测财年切换被误当作盈利修正。
      同一股票、同一预测期两端各需 ≥ MIN_ANALYST_CNT 条有效研报。

    Args:
        rebalance_date: 调仓日 T
        codes:          可投资股票代码列表
    Returns:
        ts_code → NP 预测修正（小数）；覆盖不足或两端 NP ≤ 0 时为 NaN
    """
    rc = load_analyst_rc_pit(rebalance_date, codes=codes, lookback_days=WINDOW_ANALYST)
    if rc.empty or "np" not in rc.columns or "quarter" not in rc.columns:
        return _nan_series(codes, "np_forecast_growth")

    rc = rc.copy()
    rc["np"] = pd.to_numeric(rc["np"], errors="coerce")
    rc = rc.dropna(subset=["np", "quarter"])
    if rc.empty:
        return _nan_series(codes, "np_forecast_growth")

    split_date = rebalance_date - pd.Timedelta(days=ANALYST_SPLIT)
    recent = rc[rc["pit_date"] >  split_date]
    prior  = rc[rc["pit_date"] <= split_date]

    med_recent = recent.groupby(["ts_code", "quarter"])["np"].apply(
        lambda x: _agg_with_min(x, MIN_ANALYST_CNT, "median")
    ).rename("np_recent")
    med_prior = prior.groupby(["ts_code", "quarter"])["np"].apply(
        lambda x: _agg_with_min(x, MIN_ANALYST_CNT, "median")
    ).rename("np_prior")

    paired = med_recent.to_frame().join(med_prior, how="inner")
    valid = (paired["np_recent"] > 0) & (paired["np_prior"] > 0)
    paired["revision"] = (paired["np_recent"] / paired["np_prior"] - 1).where(valid)

    growth = paired["revision"].groupby(level="ts_code").median()
    growth.name = "np_forecast_growth"
    return growth.reindex(codes)
```

**注册到 `_ALT_FACTOR_BUILDERS`**：

```python
"np_forecast_growth":   factor_np_forecast_growth,
```

---

### 4.4 C5：EPS 修正改进版（`eps_revision_v2`）

**实现位置**：`src/factors/alt_factors.py`，追加在 `factor_np_forecast_growth` 之后。

**新增常量**：

```python
MIN_ORG_REVISION_CNT = 2   # eps_revision_v2 要求至少 2 家机构完成同季前后修正
```

**函数实现**：

```python
def factor_eps_revision_v2(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    分析师 EPS 修正改进版：追踪同一机构对同一财年的前后修正幅度。

    现有 analyst_eps_revision 的隐患：近远两期的分析师群体可能不同，
    导致比率混入「分析师团队更替」的噪音。本函数仅匹配在两个时间窗口内
    均有发报、且 quarter 相同的机构，计算其前后修正中位数，信号更纯粹。

    数据依赖：需 analyst_rc_pit.parquet 含 quarter 列（重跑 M14 后可用）。

    时间对齐：
      recent = (T-90d, T]；prior = (T-180d, T-90d]
      对每只股票，在 (ts_code, org_name, quarter) 三元组上做 inner join，
      只保留在两段内均有研报的机构-财年对。

    Args:
        rebalance_date: 调仓日 T
        codes:          可投资股票代码列表
    Returns:
        ts_code → EPS 修正中位数（小数）；有效机构对 < MIN_ORG_REVISION_CNT 时为 NaN
    """
    rc = load_analyst_rc_pit(rebalance_date, codes=codes, lookback_days=WINDOW_ANALYST)
    if rc.empty or "eps" not in rc.columns or "quarter" not in rc.columns:
        return _nan_series(codes, "eps_revision_v2")

    rc = rc.copy()
    rc["eps"] = pd.to_numeric(rc["eps"], errors="coerce")
    rc = rc.dropna(subset=["eps", "quarter", "org_name"])
    if rc.empty:
        return _nan_series(codes, "eps_revision_v2")

    split_date = rebalance_date - pd.Timedelta(days=ANALYST_SPLIT)
    keys       = ["ts_code", "org_name", "quarter"]

    # 每段内：同一 (ts_code, org_name, quarter) 只保留最新一条
    recent_df = (
        rc[rc["pit_date"] >  split_date]
        .sort_values("pit_date")
        .drop_duplicates(subset=keys, keep="last")
        .set_index(keys)["eps"]
        .rename("eps_recent")
    )
    prior_df = (
        rc[rc["pit_date"] <= split_date]
        .sort_values("pit_date")
        .drop_duplicates(subset=keys, keep="last")
        .set_index(keys)["eps"]
        .rename("eps_prior")
    )

    paired = recent_df.to_frame().join(prior_df, how="inner")
    # 两端均须为正（亏损公司 EPS 为负时，比率无选股含义）
    paired = paired[(paired["eps_recent"] > 0) & (paired["eps_prior"] > 0)].copy()
    paired["revision"] = paired["eps_recent"] / paired["eps_prior"] - 1

    def _stock_median(grp: pd.DataFrame) -> float:
        # 门槛按“机构数”计算，而不是 org-quarter 配对条数。
        if grp["org_name"].nunique() < MIN_ORG_REVISION_CNT:
            return np.nan
        return float(grp["revision"].median())

    result = (
        paired.reset_index()
        .groupby("ts_code")
        .apply(_stock_median)
    )
    result.name = "eps_revision_v2"
    return result.reindex(codes)
```

**注册到 `_ALT_FACTOR_BUILDERS`**：

```python
"eps_revision_v2":      factor_eps_revision_v2,
```

---

### 4.5 构建脚本注册（必须做）

修改 `scripts/build_factor_panels.py`，在 `ALT_FACTORS` 中追加：

```python
"np_forecast_growth", "eps_revision_v2",
```

完成后先执行 dry-run：

```powershell
python -m scripts.build_factor_panels --dry-run --factors np_forecast_growth eps_revision_v2
```

期望：脚本识别 2 个因子，无“未知因子名”错误。

---

### 4.6 阶段二验证 Checklist

**Step 1：导入冒烟测试**

```powershell
python -c "
from src.factors.alt_factors import factor_np_forecast_growth, factor_eps_revision_v2
import pandas as pd

t = pd.Timestamp('2020-01-31')
codes = ['000001.SZ', '000063.SZ', '600036.SH', '601318.SH']

for name, fn in [
    ('np_forecast_growth', factor_np_forecast_growth),
    ('eps_revision_v2',    factor_eps_revision_v2),
]:
    s = fn(t, codes)
    nan_n = s.isna().sum()
    print(f'{name:25s}  非NaN: {len(codes)-nan_n}/{len(codes)}')
"
```

**Step 2：确认现有 analyst_eps_revision 不受影响**

```powershell
python -c "
from src.factors.alt_factors import factor_analyst_eps_revision
import pandas as pd
t = pd.Timestamp('2020-01-31')
codes = ['000001.SZ', '600036.SH']
print(factor_analyst_eps_revision(t, codes))
"
```

**Step 3：构建阶段二新因子面板**

```powershell
python -m scripts.build_factor_panels --factors np_forecast_growth eps_revision_v2
```

**Step 4：运行隔离目录评估**

```powershell
python -m scripts.run_factor_evaluation --recompute-fwd-ret --run-id factor_impl_stage2 --output-dir reports/factor_evaluation_factor_impl_stage2
```

---

## 五、评估后的入池决策规则

新因子通过四道 Gate 后，入池前还需满足：

1. **与 `analyst_eps_revision` 的截面相关**：`|r| < 0.60`（若相关过高，只保留 IC_IR 更高的一个）
2. **加入后组合 IC 提升**：在合成信号评估中，加入该因子后整体 IC_IR 相比去掉时有提升
3. **覆盖率稳定**：Gate 0 覆盖率不合格的年份占比 < 30%

**入池操作**：

只在用户确认后执行。不要手写 `final_factors.json`；用主评估脚本生成带 metadata 的正式结果。

执行前必须确认：若 momentum sprint 因子面板已经构建，则本次主刷新是一次**合并入池决策**，同时接受或排除 momentum 因子的评估结果；否则继续停留在隔离目录评估，不刷新主线。

```powershell
python -m scripts.run_factor_evaluation --recompute-fwd-ret --run-id factor_impl_main_refresh
```

随后再按主流水线顺序运行：

```powershell
python -m scripts.run_signal_combination
python -m scripts.run_portfolio_optimization
python -m scripts.run_backtest
```

以上命令仍只覆盖训练/验证期，不触碰 2023-2025 测试集。

---

## 六、快速回滚方案

如果阶段一代码引入问题，先检查 diff，确认这些文件中没有用户的无关改动：

```powershell
# 只查看，不修改
git diff -- src/factors/alt_factors.py src/factors/financial_factors.py scripts/build_factor_panels.py
```

确认只有本计划改动后，再用精确 patch 撤销新增函数和注册项。只有在明确没有无关改动时，才允许使用：

```powershell
git restore src/factors/alt_factors.py src/factors/financial_factors.py scripts/build_factor_panels.py

# 验证现有因子恢复正常
python -c "
from src.factors.alt_factors import factor_analyst_eps_revision
import pandas as pd
t = pd.Timestamp('2020-01-31')
print(factor_analyst_eps_revision(t, ['600036.SH']))
"
```

如果阶段二 parquet 重跑出问题（M14 结果异常），回滚步骤：

```powershell
# 先查看差异
git diff -- scripts/csv_to_parquet.py

# 确认无无关改动后再恢复脚本
git restore scripts/csv_to_parquet.py

# 重新生成 analyst_rc_pit.parquet（只重跑 M14）
python -c "from scripts.csv_to_parquet import process_analyst_rc_pit; process_analyst_rc_pit()"
```

---

## 七、进度追踪

| 任务 | 状态 | 完成日期 |
|------|------|---------|
| 阶段一：代码实现（4 因子）| ⬜ 待开始 | — |
| 阶段一：冒烟测试通过 | ⬜ 待开始 | — |
| 阶段一：因子评估（四道 Gate）| ⬜ 待开始 | — |
| 阶段一：git commit | ⬜ 待开始 | — |
| 阶段二：`csv_to_parquet.py` 修改 | ⬜ 待开始 | — |
| 阶段二：M14 重跑 + 覆盖率验证 | ⬜ 待开始 | — |
| 阶段二：C3/C5 代码实现 | ⬜ 待开始 | — |
| 阶段二：因子评估 | ⬜ 待开始 | — |
| 阶段二：git commit | ⬜ 待开始 | — |
| 入池决策（用户确认后）| ⬜ 待开始 | — |

---

*相关文档：*  
*`docs/research/factor_roadmap/consensus_factor_plan.md`（一致预期因子原始计划）*  
*`docs/research/factor_roadmap/factor_research_guide.md`（因子研究方向总纲）*  
*`reports/factor_evaluation/final_factors.json`（当前入模因子权威来源）*
