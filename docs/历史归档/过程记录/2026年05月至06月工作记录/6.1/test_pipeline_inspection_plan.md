# 测试集 Pipeline 重跑前检查计划（v2）

> 生成日期：2026-06-01  
> 背景：test_run_1（IR=0.149）因 3 个 ALT_FACTORS 因子面板缺 2023-2025 数据导致全测试期零调仓，已废除。  
> 目标：逐层检查数据文件和代码逻辑，确认所有风险已消除后重跑测试集（消耗第 1 次有效次数）。

---

## 根因回顾

### 连锁失败路径

```
build_factor_panels --resume 只重建 ALL_FACTORS（不含 ALT_FACTORS）
   ↓
factor_panels_test_run_1/ 中 3 个因子（analyst_eps_revision / hk_hold_chg / hk_hold_ratio）
   无 2023+ 行
   ↓
RidgeCombiner._predict_cross_section(T)：任意因子缺 T 行 → return None（硬编码早返回）
   ↓
build_ridge_panel 对全部 36 个测试期 T 返回 None
   ↓
composite.parquet 无 2023-2025 任何行
   ↓
TopN EW 优化器：composite 全 NaN → 跳过全部调仓（36 个月 0 次调仓）
   ↓
持仓冻结在 2022-12-30，静态持有 36 个月 → IR=0.149 反映的是漂移而非策略
```

### 已执行的修复

```
python scripts/build_factor_panels.py \
  --factors analyst_eps_revision hk_hold_chg hk_hold_ratio \
  --allow-test-set --run-id 2 --end-date 2025-12-31
→ 生成 factor_panels_test_run_2/（3 个文件，168 期，至 2025-12-31）
→ 3 个文件已复制到 factor_panels_test_run_1/
```

---

## 运行口径统一（必须先定，所有检查以此为准）

| 参数 | 正确值 | 依据 |
|------|--------|------|
| `--run-id` | **1** | `run_test_pipeline.py:1003`：`args.run_id == count_test_set_runs() + 1`，当前 `active_runs=[]`，count=0，故期望 1 |
| commit tag | **`[TEST_SET_RUN_1]`** | `run_test_pipeline.py:1072`：`run_tag = f"[TEST_SET_RUN_{args.run_id}]"` |
| 因子面板目录 | **`factor_panels_test_run_1/`** | run_id=1 → pipeline 自动检测 `DATA_PROC/factor_panels_test_run_1` |
| run 目录名 | **`test_run_1__<mainline_run_id>`** | `run_test_pipeline.py:1014`：`f"test_run_{args.run_id}__{mainline_run_id}"` |
| mainline_run_id | 以 `registry/mainline.json` 为准 | 检查 L4-2 时核实 |

---

## 检查总览

| 阶段 | 检查组 | 项数 | 风险等级 |
|------|--------|------|---------|
| **Preflight** | 目录冲突 & 旧产物 | 3 项 | 🔴 高（不做直接 sys.exit） |
| **L1** | 因子面板文件 | 5 项 | 🔴 高（上次失败根因） |
| **L2** | 基础数据文件（覆盖+质量） | 5 项 | 🟠 中 |
| **L3** | 核心代码逻辑 | 6 项 | 🔴 高 |
| **L4** | 配置 & mainline 注册表 | 3 项 | 🟡 中 |
| **L5** | PIT 专项检查（修复因子） | 3 项 | 🟠 中 |
| **L6** | 端到端干跑 & 重跑后验证 | 4 项 | 🔴 高 |

---

## Preflight：目录冲突与旧产物

### P-1：旧无效 run 目录是否会拦截新跑

**风险**：`run_test_pipeline.py:1057` 检查 `test_run_dir / "signal" / "composite.parquet"` 是否存在。
若存在且未传 `--resume-from-lock` → `sys.exit(1)` 直接拒绝。

旧目录 `runs/test/test_run_1__20260530_111129__rolling48_topn150_ew/` 已有 `signal/composite.parquet`。
若 mainline 未变（active_run_id 仍为 `20260530_111129__rolling48_topn150_ew`），新 run_id=1 的目录名**与旧目录完全相同**，必定触发拦截。

**必须执行（重跑前）**：将旧目录归档（改名），绝对不要用 `--resume-from-lock` 复用旧产物。

```powershell
# 归档旧无效目录（改名，不删除，保留审计记录）
Rename-Item `
  "E:\Acoding\Project\500 improve\runs\test\test_run_1__20260530_111129__rolling48_topn150_ew" `
  "test_run_1__20260530_111129__rolling48_topn150_ew__VOIDED"
```

**通过标准**：`runs/test/test_run_1__<mainline_run_id>/` 不存在，或存在但无 `signal/composite.parquet`。

---

### P-2：RUN_STARTED 锁文件检查

**风险**：同上目录的 `RUN_STARTED.json` 若存在，会进入 `lock_started.exists()` 分支（`run_test_pipeline.py:1024`），触发额外守卫。归档目录后此风险消除。

**通过标准**：目录归档后，`test_run_1__<mainline_run_id>/RUN_STARTED.json` 不存在。

---

### P-3：禁止 `--resume-from-lock` 参数

**原则**：`--resume-from-lock` 设计用于中断重试，不是用于修复后重建。若传入此参数，pipeline 会复用旧的 `composite.parquet`（仍是只有 2014-2022 数据的破损版本），修复完全失效。

**通过标准**：重跑命令中不含 `--resume-from-lock`。

---

## L1：因子面板文件检查

### L1-1：factor_panels_test_run_1/ 文件数量与选中因子对齐

**检查命令**：
```python
import os, json

base = r"E:\Acoding\Project\500 improve\data\processed\factor_panels_test_run_1"
with open(r"E:\Acoding\Project\500 improve\reports\factor_evaluation\final_factors.json") as f:
    selected = json.load(f)["final_factors"]

files = {f[:-8] for f in os.listdir(base) if f.endswith('.parquet')}  # 去掉 .parquet
print(f"目录文件数: {len(files)}")
print(f"选中因子数: {len(selected)}")

missing_in_dir = [f for f in selected if f not in files]
if missing_in_dir:
    print(f"[FAIL] 以下选中因子在目录中缺失: {missing_in_dir}")
else:
    print("✅ 所有选中因子均有对应文件")
```

**通过标准**：`missing_in_dir` 为空。

---

### L1-2：获取实际调仓日（不用日历月末）

**说明**：实际调仓日来自 `get_rebalance_dates()`（基于 `index_member.parquet`），与日历月末可能有 1-2 天偏差，必须用实际日期作为检查依据。

**检查命令**：
```python
import sys
sys.path.insert(0, r"E:\Acoding\Project\500 improve")
from src.data.universe import get_rebalance_dates
import src.config as cfg

test_dates = get_rebalance_dates(start=cfg.TEST_START, end=cfg.TEST_END)
print(f"实际测试期调仓日: {len(test_dates)} 期")
print(f"  起止: {test_dates[0].date()} ~ {test_dates[-1].date()}")
print(f"  首5个: {[str(d.date()) for d in test_dates[:5]]}")
```

**通过标准**：2023-2025 调仓日数 = 36（月频，每月一期）。

---

### L1-3：3 个修复文件的日期和质量

**检查命令**：
```python
import pandas as pd, sys
sys.path.insert(0, r"E:\Acoding\Project\500 improve")
from src.data.universe import get_rebalance_dates
import src.config as cfg

base = r"E:\Acoding\Project\500 improve\data\processed\factor_panels_test_run_1"
test_dates = set(get_rebalance_dates(start=cfg.TEST_START, end=cfg.TEST_END))

for factor in ["analyst_eps_revision", "hk_hold_chg", "hk_hold_ratio"]:
    df = pd.read_parquet(f"{base}/{factor}.parquet")
    missing_in_panel = test_dates - set(df.index)
    avg_valid = df[df.index.isin(test_dates)].notna().sum(axis=1).mean()
    print(f"{factor}:")
    print(f"  范围: {df.index.min().date()} ~ {df.index.max().date()}")
    print(f"  测试期缺失调仓日: {sorted([str(d.date()) for d in missing_in_panel])}")
    print(f"  测试期平均有效股票数: {avg_valid:.0f}")
```

**通过标准**：`missing_in_panel` 全为空；每期平均有效股票 > 50。

---

### L1-4：所有选中因子的测试期覆盖

**检查命令**：
```python
import pandas as pd, json, os, sys
sys.path.insert(0, r"E:\Acoding\Project\500 improve")
from src.data.universe import get_rebalance_dates
import src.config as cfg

base = r"E:\Acoding\Project\500 improve\data\processed\factor_panels_test_run_1"
with open(r"E:\Acoding\Project\500 improve\reports\factor_evaluation\final_factors.json") as f:
    selected = json.load(f)["final_factors"]

test_dates = set(get_rebalance_dates(start=cfg.TEST_START, end=cfg.TEST_END))
issues = []

for factor in selected:
    fp = os.path.join(base, f"{factor}.parquet")
    if not os.path.exists(fp):
        issues.append((factor, "FILE_MISSING", 0))
        continue
    df = pd.read_parquet(fp)
    missing = test_dates - set(df.index)
    if missing:
        issues.append((factor, f"缺 {len(missing)} 个测试期", sorted(missing)))
    else:
        pass  # OK

if not issues:
    print("✅ 所有选中因子均覆盖全部测试期调仓日")
else:
    for factor, reason, detail in issues:
        print(f"[FAIL] {factor}: {reason}")
```

**通过标准**：`issues` 为空。

---

### L1-5：_predict_cross_section 兼容性模拟（关键验证）

**目的**：直接模拟 `_predict_cross_section` 在每个测试期 T 的行为，确认不再提前返回 None。

```python
import pandas as pd, json, os, sys
sys.path.insert(0, r"E:\Acoding\Project\500 improve")
from src.data.universe import get_rebalance_dates
import src.config as cfg

base = r"E:\Acoding\Project\500 improve\data\processed\factor_panels_test_run_1"
with open(r"E:\Acoding\Project\500 improve\reports\factor_evaluation\final_factors.json") as f:
    selected = json.load(f)["final_factors"]

# 只读 index（轻量），检查每个 T 是否在每个 panel 的 index 中
panels_idx = {}
for f in selected:
    fp = os.path.join(base, f"{f}.parquet")
    if os.path.exists(fp):
        panels_idx[f] = set(pd.read_parquet(fp, columns=[]).index)

test_dates = get_rebalance_dates(start=cfg.TEST_START, end=cfg.TEST_END)
failures = []
for T in test_dates:
    for factor, idx_set in panels_idx.items():
        if T not in idx_set:
            failures.append((T.date(), factor))

if not failures:
    print(f"✅ 所有 {len(test_dates)} 个测试期、所有 {len(selected)} 个因子均有数据")
    print("   _predict_cross_section 不会在测试期提前返回 None")
else:
    for T, f in failures[:20]:
        print(f"[FAIL] T={T}, factor={f} 缺失")
```

**通过标准**：`failures` 为空。

---

## L2：基础数据文件（覆盖 + 质量）

### L2-1：fwd_ret_panel 公共缓存（扩展起点）

**检查命令**：
```python
import pandas as pd
fwd = pd.read_parquet(r"E:\Acoding\Project\500 improve\data\processed\fwd_ret_panel.parquet")
print(f"公共 fwd_ret: {fwd.index.min().date()} ~ {fwd.index.max().date()}, {len(fwd)} 期")
# 期望：到 2022-12-31（测试集运行时需扩展）
nan_rate = fwd.isna().mean().mean()
print(f"整体 NaN 率: {nan_rate:.2%}")
```

**通过标准**：最大日期 = 2022-12-31；NaN 率 < 30%（正常，很多股票不是每期都在成分股内）。

---

### L2-2：index_member — 成分股快照覆盖与字段质量

**检查命令**：
```python
import pandas as pd, sys
sys.path.insert(0, r"E:\Acoding\Project\500 improve")
from src.data.universe import get_rebalance_dates
import src.config as cfg

idx = pd.read_parquet(r"E:\Acoding\Project\500 improve\data\processed\index_member.parquet")
print(f"index: {type(idx.index)}")
print(f"列名: {idx.columns.tolist()}")

if isinstance(idx.index, pd.MultiIndex):
    dates = idx.index.get_level_values(0).unique()
else:
    dates = idx.index.unique()
dates = sorted(dates)
print(f"日期范围: {dates[0].date()} ~ {dates[-1].date()}, 共 {len(dates)} 期")

# 2023-2025 覆盖
test_dates = set(get_rebalance_dates(start=cfg.TEST_START, end=cfg.TEST_END))
missing = test_dates - set(dates)
print(f"2023-2025 缺失调仓日: {sorted([str(d.date()) for d in missing])}")

# 每期成分数应 ~500
if isinstance(idx.index, pd.MultiIndex):
    n_per_date = idx.groupby(level=0).size()
    test_n = n_per_date[n_per_date.index.isin(test_dates)]
    print(f"测试期每期成分数: min={test_n.min()}, max={test_n.max()}, mean={test_n.mean():.0f}")

# 权重和应 ~1
if "weight" in idx.columns and isinstance(idx.index, pd.MultiIndex):
    wt_sum = idx["weight"].groupby(level=0).sum()
    print(f"测试期权重和: min={wt_sum[wt_sum.index.isin(test_dates)].min():.3f}, "
          f"max={wt_sum[wt_sum.index.isin(test_dates)].max():.3f}")

# 状态列非空检查
status_cols = [c for c in idx.columns 
               if any(k in c for k in ["suspended","limit_up","limit_down","is_st","is_new"])]
print(f"状态列: {status_cols}")
for col in status_cols:
    null_rate = idx[col].isna().mean()
    if null_rate > 0.1:
        print(f"[WARN] {col} NaN率过高: {null_rate:.1%}")
    else:
        print(f"[OK]   {col} NaN率: {null_rate:.1%}")
```

**通过标准**：
- 2023-2025 缺失调仓日为空
- 每期成分数 450-550（允许调整期有偏差）
- 权重和 0.99-1.01
- 状态列 NaN 率 < 5%
- `ts_code` 格式为 `000001.SZ`（带交易所后缀，无裸 6 位代码）

---

### L2-3：daily_quote — 价格口径与时间覆盖

**检查命令**：
```python
import pandas as pd

dq = pd.read_parquet(r"E:\Acoding\Project\500 improve\data\processed\daily_quote.parquet")
print(f"列名: {dq.columns.tolist()}")

# 确认日期列
if "trade_date" in dq.columns:
    dates = dq["trade_date"]
elif "trade_date" == dq.index.name:
    dates = dq.index.to_series()
else:
    dates = pd.Series(dq.index)

print(f"日期范围: {dates.min()} ~ {dates.max()}")
print(f"2023-2025 交易日数: {(dates >= '2023-01-01').sum()}")

# 必须有开盘复权价（T+1 执行用）和收盘复权价（估值用）
required_cols = ["open_adj", "close_adj"]
alt_cols = [["open_hfq", "close_hfq"]]
for col in required_cols:
    if col not in dq.columns:
        print(f"[WARN] 缺少列 {col}，检查替代字段名")
    else:
        # 2023+ NaN 率
        test_slice = dq[dq["trade_date"] >= "2023-01-01"] if "trade_date" in dq.columns \
                     else dq[dq.index >= "2023-01-01"]
        print(f"[OK] {col} — 2023-2025 NaN 率: {test_slice[col].isna().mean():.2%}")

# ts_code 格式
if "ts_code" in dq.columns:
    sample = dq["ts_code"].iloc[:5].tolist()
    print(f"ts_code 样本: {sample}")
    has_suffix = all("." in str(c) for c in sample)
    print(f"ts_code 含交易所后缀: {'✅' if has_suffix else '❌ 裸 6 位代码'}")
```

**通过标准**：最后日期 ≥ 2025-12-31；`open_adj` 和 `close_adj` 存在且 2023-2025 NaN 率 < 5%；ts_code 含 `.SZ`/`.SH` 后缀。

---

### L2-4：index_quote — 基准口径确认（全收益）

**检查命令**：
```python
import pandas as pd

iq = pd.read_parquet(r"E:\Acoding\Project\500 improve\data\processed\index_quote.parquet")
print(f"列名: {iq.columns.tolist()}")
print(f"ts_code 唯一值: {iq['ts_code'].unique() if 'ts_code' in iq.columns else 'N/A'}")

# 关键：必须是 H00905.CSI（全收益），不是 000905.SH（价格指数）
# 全收益 ~ 2-3% 年化差距，错用会虚增超额收益
if "ts_code" in iq.columns:
    codes = iq["ts_code"].unique()
    if "H00905.CSI" in codes:
        print("✅ 全收益指数 H00905.CSI 存在")
    elif "000905.SH" in codes and "H00905.CSI" not in codes:
        print("❌ 仅有价格指数 000905.SH，缺全收益指数 H00905.CSI！")
    else:
        print(f"[INFO] 指数代码: {codes}")

# 日期覆盖
date_col = "trade_date" if "trade_date" in iq.columns else iq.index.name
if date_col and date_col in iq.columns:
    print(f"日期范围: {iq[date_col].min()} ~ {iq[date_col].max()}")
    # 验证 NAV 连续性（无异常跳跃）
    if "close" in iq.columns or "nav" in iq.columns:
        nav_col = "close" if "close" in iq.columns else "nav"
        nav = iq[iq[date_col] >= "2023-01-01"][nav_col].dropna()
        daily_ret = nav.pct_change().abs()
        print(f"2023-2025 单日最大涨跌: {daily_ret.max():.2%}")
        assert daily_ret.max() < 0.15, "指数日收益异常（>15%），可能口径错误"
```

**通过标准**：指数代码含 `H00905.CSI`（全收益）；最后日期 ≥ 2025-12-31；日收益无 >15% 的异常跳跃。

---

### L2-5：industry.parquet — 行业分类覆盖

**检查命令**：
```python
import pandas as pd

ind = pd.read_parquet(r"E:\Acoding\Project\500 improve\data\processed\industry.parquet")
print(f"形状: {ind.shape}, 列名: {ind.columns.tolist()}")

if isinstance(ind.index, pd.MultiIndex):
    dates = ind.index.get_level_values(0).unique()
else:
    date_col = "trade_date" if "trade_date" in ind.columns else ind.index.name
    dates = ind[date_col].unique() if date_col in ind.columns else ind.index.unique()

dates = sorted(dates)
print(f"日期范围: {dates[0]} ~ {dates[-1]}")
print(f"2023-2025 有数据: {any(d >= pd.Timestamp('2023-01-01') for d in dates)}")

# 行业数应 ~31（申万一级）
if "industry" in ind.columns or "sw_l1" in ind.columns:
    ind_col = "industry" if "industry" in ind.columns else "sw_l1"
    n_ind = ind[ind_col].nunique()
    print(f"行业分类数: {n_ind}（期望约 31 个申万一级）")
```

**通过标准**：有 2023+ 数据；行业分类数 ~31。

> **注意**：若因子面板在 `build_factor_panels` 阶段已中性化，`run_test_pipeline.py` 在信号生成时不会再用此文件。需确认流程中 industry 的实际使用时机。

---

## L3：核心代码逻辑检查

### L3-1：`_predict_cross_section` 的 None 返回路径（上次 bug 根因，代码无需改）

**位置**：`experiments/legacy/ridge_signal/ridge_combiner.py:658-664`

**代码路径**：
```python
for name in self.factor_names:
    if T not in factor_panels[name].index:
        return None   # ← 任意因子缺 T 行，整期预测为 None
```

此代码无需修改，L1-5 检查通过后此路径不再触发。确认要点：`self.factor_names` 来自 `final_factors.json` 中的选中因子，L1-1 已验证所有选中因子均有面板文件。

---

### L3-2：`_build_composite_signals_ridge` 信号生成范围（代码逻辑确认）

**位置**：`scripts/run_test_pipeline.py:324-352`

**关键逻辑**：
```python
valid_end = pd.Timestamp(cfg.VALID_END)                   # 2022-12-31
tv_panels = {k: v.loc[v.index <= valid_end] for k, v in factor_panels.items()}  # alpha 选择用 TV 数据
tv_fwd    = fwd_ret_panel.loc[fwd_ret_panel.index <= valid_end]

# alpha 选择只用 TV 数据 ← 正确隔离
combiner.select_alpha_walk_forward(tv_panels, tv_fwd, tv_dates, ...)

# 信号生成用全量数据 ← 正确，含 2023-2025
composite_ridge = combiner.build_ridge_panel(
    factor_panels,              # 全量（含 2023-2025）
    fwd_ret_panel,              # 扩展后全量
    pd.DatetimeIndex(all_dates) # 全部日期
)
```

此处代码无 bug，根因是数据。确认后无需修改。

**验证方式（重跑后）**：`signal/signal_metadata.json` 的 `date_range` 应为 `["2012-01-31", "2025-12-31"]`，`n_rebalance_dates = 168`。

---

### L3-3：`_extend_fwd_ret_panel` 扩展逻辑

**位置**：`scripts/run_test_pipeline.py:211-287`

**检查要点**：验证该函数能将 fwd_ret 从 2022-12 扩展至 2025-11（最后一期月频收益的截止）。

**重跑后验证命令**：
```python
import pandas as pd
fwd_ext = pd.read_parquet(r"runs/test/<new_run_id>/fwd_ret_panel.parquet")
print(f"扩展后 fwd_ret: {fwd_ext.index.min().date()} ~ {fwd_ext.index.max().date()}")
test_rows = fwd_ext[fwd_ext.index >= "2023-01-01"]
print(f"2023-2025 期数: {len(test_rows)}")
print(f"  非空行占比: {test_rows.notna().any(axis=1).mean():.1%}")
```

**通过标准**：扩展后最后日期 ≥ 2025-10-31（最后调仓日的 1 个月后）；2023-2025 非空行占比 > 90%。

---

### L3-4：TopN EW 优化器的元数据预期（修正错误）

**位置**：`src/portfolio/optimizer.py:750-764`

**硬编码输出**（来自代码，不可更改）：
```python
"fallback_level":  2,               # TopN EW 强制路径固定为 2
"solver_status":   "topn_ew_forced",
"optimizer_mode":  "topn_ew",
```

**重跑后验证命令**：
```python
import pandas as pd
meta = pd.read_parquet(r"runs/test/<new_run_id>/portfolio/optimizer_meta.parquet")
test_meta = meta[meta.index >= "2023-01-01"]
print(f"测试期 optimizer_meta 行数: {len(test_meta)}（期望 36）")
print(f"fallback_level 分布: {test_meta['fallback_level'].value_counts().to_dict()}")
print(f"solver_status 分布: {test_meta['solver_status'].value_counts().to_dict()}")
print(f"constraint_compliant 非 True 比例: {(~test_meta['constraint_compliant']).mean():.1%}")
print(f"n_holdings 范围: {test_meta['n_holdings'].min()} ~ {test_meta['n_holdings'].max()}")
```

**通过标准**：
- 行数 = 36
- `fallback_level` 全为 2（不是 "L1"）
- `solver_status` 全为 `"topn_ew_forced"`
- `n_holdings` 范围 ~140-155（目标 150，停牌等情况有偏差）
- `constraint_compliant=False` 的期数 < 20%（偶有单股偏离超限属正常）

---

### L3-5：test_set_runs.json 写入时机确认

**检查命令**：
```bash
grep -n "record_test_set_run\|test_set_runs" scripts/run_test_pipeline.py | head -20
```

**预期**：重跑完成后，pipeline 自动调用 `record_test_set_run(args.run_id, ...)` 写入 `docs/logs/test_set_runs.json`，无需手动更新。重跑后需手动更新 `CLAUDE.md §1.1` 的测试集计数。

---

### L3-6：测试 2023-08-28 印花税切换（成本计算）

**风险**：2023-08-28 印花税从卖出 0.1% 调降为 0.05%。回测需在此节点前后切换，否则 2023-08 后的交易成本偏高，IR 偏低约 0.1-0.2。

**检查命令**：
```python
# 确认 config 中印花税切换逻辑
import src.config as cfg
print(dir(cfg))
# 或直接搜代码
```

```bash
grep -n "2023-08-28\|stamp_duty\|印花税" src/backtest/transaction.py src/config.py
```

**通过标准**：代码中存在 `2023-08-28` 切换逻辑，而非常数印花税率。

---

## L4：配置 & mainline 注册表

### L4-1：mainline.json 确认主基线 run_id

**位置**：`registry/mainline.json`

**检查命令**：
```python
import json
with open(r"E:\Acoding\Project\500 improve\registry\mainline.json") as f:
    mainline = json.load(f)
print(f"active_run_id: {mainline['active_run_id']}")
# 期望：20260530_111129__rolling48_topn150_ew
```

**通过标准**：`active_run_id` 与 `runs/train_valid/` 中存在的目录名一致。

---

### L4-2：主基线 run_config.json — 参数未漂移

**检查命令**：
```python
import json

with open(r"E:\Acoding\Project\500 improve\registry\mainline.json") as f:
    mainline_id = json.load(f)["active_run_id"]

rc_path = fr"E:\Acoding\Project\500 improve\runs\train_valid\{mainline_id}\run_config.json"
with open(rc_path) as f:
    rc = json.load(f)

spec = rc.get("spec", {})
signal = spec.get("signal", {})
opt    = spec.get("optimizer", {})

print("=== Signal Spec ===")
print(f"  method:        {signal.get('method')}         期望: ridge")
print(f"  training_mode: {signal.get('training_mode')}  期望: rolling")
print(f"  window_months: {signal.get('window_months')}  期望: 48")

print("=== Optimizer Spec ===")
print(f"  optimizer_mode: {opt.get('optimizer_mode')}   期望: topn_ew")
print(f"  topn:           {opt.get('topn')}             期望: 150")
print(f"  te_target_annual: {opt.get('te_target_annual')} 期望: 0.06")
```

**通过标准**：signal = ridge/rolling/48；optimizer = topn_ew/150。如与期望不符，必须在重跑前找原因。

---

### L4-3：src/config.py 时间区间

**检查命令**：
```python
import src.config as cfg
print(f"VALID_END:  {cfg.VALID_END}")
print(f"TEST_START: {cfg.TEST_START}")
print(f"TEST_END:   {cfg.TEST_END}")
```

**通过标准**：`VALID_END = 2022-12-31`；`TEST_START = 2023-01-01`；`TEST_END = 2025-12-31`。

---

## L5：PIT 专项检查（3 个修复因子）

### L5-1：hk_hold_ratio / hk_hold_chg 的 PIT 口径验证

**位置**：`src/factors/alt_factors.py:303-322`

**代码逻辑**：
```python
def factor_hk_hold_ratio(rebalance_date, codes):
    start = rebalance_date - pd.Timedelta(days=5)
    hk = load_hk_hold(start, rebalance_date, codes=codes)  # 只用 <= rebalance_date 的数据
    snap = hk["ratio"].groupby(level="ts_code").last()     # 取截止当日最新值
```

此逻辑满足 PIT：T 日只用 ≤ T 日数据，无未来函数风险。

**抽样验证命令**（抽 5 个日期 × 3 只股票）：
```python
import pandas as pd, sys
sys.path.insert(0, r"E:\Acoding\Project\500 improve")
from src.data.loader import load_hk_hold

# 抽样 2023-03-31 的北向持仓
T = pd.Timestamp("2023-03-31")
raw = load_hk_hold(T - pd.Timedelta(days=5), T)
print(f"2023-03-31 北向持仓记录数: {len(raw)}")
print(f"最大日期: {raw.index.get_level_values('trade_date').max()}")
assert raw.index.get_level_values("trade_date").max() <= T, "存在未来数据！"
print("✅ PIT 验证通过：所有记录 trade_date <= 2023-03-31")
```

**通过标准**：原始数据最大日期 ≤ T，无未来函数。

---

### L5-2：analyst_eps_revision 的 PIT 口径验证

**检查要点**：分析师 EPS 修正因子使用 `analyst_rc_pit.parquet`（含 pit_date 字段），应只用 `pit_date <= T` 的记录。

**检查命令**：
```bash
grep -n "analyst_eps_revision\|analyst_rc_pit\|Annodt\|pit_date" src/factors/alt_factors.py | head -20
```

然后手动确认该因子函数中没有 `shift(-1)` 或使用 `>T` 的日期过滤。

**通过标准**：确认 analyst_eps_revision 使用 PIT 口径（pit_date/Annodt ≤ T），无未来数据。

---

### L5-3：修复后因子面板的 NaN 率合理性

**说明**：hk_hold 系列在港股通开通（2014-11-17）前全为 NaN 属正常。2023-2025 期应有合理覆盖。

**检查命令**：
```python
import pandas as pd

base = r"E:\Acoding\Project\500 improve\data\processed\factor_panels_test_run_1"
for factor in ["analyst_eps_revision", "hk_hold_chg", "hk_hold_ratio"]:
    df = pd.read_parquet(f"{base}/{factor}.parquet")
    test_slice = df[df.index >= "2023-01-01"]
    nan_rate = test_slice.isna().mean().mean()
    avg_valid = test_slice.notna().sum(axis=1).mean()
    print(f"{factor}: 测试期 NaN率={nan_rate:.1%}, 平均有效股={avg_valid:.0f}")
    if avg_valid < 50:
        print(f"  [WARN] 有效股票数过少，信号质量可能受影响")
```

**通过标准**：
- `hk_hold_chg` / `hk_hold_ratio`：测试期平均有效股 > 50（港股通持股约覆盖 200-400 只 CSI500 成份股）
- `analyst_eps_revision`：测试期平均有效股 > 100

---

## L6：端到端验证

### L6-1：正式重跑前的 git commit

**必须执行**，确保代码和数据状态有版本记录。

```bash
# 提交修复文件
git add data/processed/factor_panels_test_run_1/analyst_eps_revision.parquet
git add data/processed/factor_panels_test_run_1/hk_hold_chg.parquet
git add data/processed/factor_panels_test_run_1/hk_hold_ratio.parquet
git add docs/logs/test_set_runs.json
git add CLAUDE.md
git commit -m "fix: 补全 3 个 ALT_FACTORS 因子面板（2023-2025），为 [TEST_SET_RUN_1] 准备
..."
```

> **注意**：`git add` 大文件前确认 `.gitignore` 是否排除 parquet，避免意外提交超大文件。

---

### L6-2：正式重跑命令

所有 Preflight 和 L1-L5 检查通过后，执行：

```bash
python scripts/run_test_pipeline.py --run-id 1
```

**不传任何额外参数**：
- 不传 `--factor-panel-dir`（pipeline 自动检测 `factor_panels_test_run_1/`）
- 不传 `--resume-from-lock`（旧目录已归档，不需要恢复）

---

### L6-3：重跑后立即验证关键产物

**在 pipeline 完成后立即执行**：

```python
import pandas as pd, json, sys
sys.path.insert(0, r"E:\Acoding\Project\500 improve")
from src.data.universe import get_rebalance_dates
import src.config as cfg

# 读取新 run_id（从 docs/logs/test_set_runs.json）
with open(r"docs/logs/test_set_runs.json") as f:
    run_id = json.load(f)["active_runs"][-1]["run_id"]
with open(r"registry/mainline.json") as f:
    mainline_id = json.load(f)["active_run_id"]
run_dir = f"runs/test/test_run_{run_id}__{mainline_id}/"

test_dates = get_rebalance_dates(start=cfg.TEST_START, end=cfg.TEST_END)

# 1. composite 覆盖范围
comp = pd.read_parquet(run_dir + "signal/composite.parquet")
print(f"composite: {comp.index.min().date()} ~ {comp.index.max().date()}, {len(comp)} 期")
assert comp.index.max() >= pd.Timestamp("2025-12-01"), "❌ composite 未覆盖 2025-12"

# 2. 优化器运行记录
meta = pd.read_parquet(run_dir + "portfolio/optimizer_meta.parquet")
test_meta = meta[meta.index >= "2023-01-01"]
print(f"测试期 optimizer_meta 行数: {len(test_meta)}（期望 36）")
assert len(test_meta) >= 36, "❌ 优化器未为所有测试期运行"
assert (test_meta["solver_status"] == "topn_ew_forced").all(), "❌ solver_status 异常"

# 3. 换手记录（每次调仓约 1 条记录，共约 36 条）
trades = pd.read_parquet(run_dir + "backtest/trades_valid.parquet")
print(f"调仓记录行数: {len(trades)}（期望约 36 行）")
assert len(trades) > 10, "❌ 换手次数异常少，可能仍未调仓"
assert (trades["buy_value"] > 0).any() or (trades["sell_value"] > 0).any(), \
    "❌ 无实际成交"

# 4. 持仓是否动态变化（不同月份持仓股票应有差异）
aw = pd.read_parquet(run_dir + "backtest/actual_weights_valid.parquet")
# 抽比较 2023-01 和 2023-12 的持仓
dates_aw = aw.index.unique() if not isinstance(aw.index, pd.MultiIndex) else aw.index.get_level_values(0).unique()
if len(dates_aw) > 10:
    w1 = aw.loc[dates_aw[0]]
    w2 = aw.loc[dates_aw[30]]
    diff = (w1.fillna(0) - w2.fillna(0)).abs().sum()
    print(f"2023-01-03 vs ~2023-12 持仓差异（L1 范数）: {diff:.4f}")
    assert diff > 0.01, "❌ 持仓似乎没有变化，可能仍是静态持有"

print("\n✅ 所有关键产物验证通过")
```

---

### L6-4：重跑后更新项目状态

验证通过后执行以下更新：

1. **CLAUDE.md §1.1** — 更新测试集计数（0 次有效 → 1 次有效，2 次剩余）
2. **CLAUDE.md §1.1** — 更新 IR 和硬指标检查结果
3. **docs/logs/test_set_runs.json** — 由 pipeline 自动写入，检查确认
4. **current work/6.1/ 分析文件** — 若 IR 显著不同于 0.149，重新生成分析报告

---

## 执行清单（按序，任何 FAIL 先修复）

### Phase 0：口径对齐（重跑前）

| # | 检查项 | 状态 |
|---|--------|------|
| P-1 | 归档旧 test_run_1__... 目录，防止目录名冲突 | ⬜ |
| P-2 | 确认归档后 RUN_STARTED 锁文件不存在 | ⬜ |
| P-3 | 确认重跑命令不含 --resume-from-lock | ⬜ |

### Phase 1：数据层

| # | 检查项 | 状态 |
|---|--------|------|
| L1-1 | factor_panels_test_run_1 含所有选中因子 | ⬜ |
| L1-2 | 获取实际 36 个测试期调仓日 | ⬜ |
| L1-3 | 3 个修复文件覆盖所有 36 个调仓日 | ⬜ |
| L1-4 | 所有选中因子均覆盖 36 个调仓日 | ⬜ |
| **L1-5** | **_predict_cross_section 兼容性模拟（最关键）** | ⬜ |
| L2-1 | fwd_ret_panel 公共缓存截止 2022-12 | ⬜ |
| L2-2 | index_member 覆盖 36 个测试期，成分数 ~500，状态列完整 | ⬜ |
| L2-3 | daily_quote 至 2025-12，有 open_adj/close_adj | ⬜ |
| L2-4 | index_quote 为 H00905.CSI 全收益口径 | ⬜ |
| L2-5 | industry.parquet 有 2023+ 数据 | ⬜ |

### Phase 2：代码层

| # | 检查项 | 状态 |
|---|--------|------|
| L3-1 | _predict_cross_section None 路径已被数据修复封堵 | ⬜ |
| L3-2 | _build_composite_signals_ridge 信号范围含 2023-2025 | ⬜ |
| L3-3 | _extend_fwd_ret_panel 函数扩展逻辑确认 | ⬜ |
| L3-4 | optimizer_meta 期望为 fallback_level=2，solver_status=topn_ew_forced | ⬜ |
| L3-5 | record_test_set_run 写入时机确认 | ⬜ |
| L3-6 | 2023-08-28 印花税切换逻辑存在 | ⬜ |

### Phase 3：配置层

| # | 检查项 | 状态 |
|---|--------|------|
| L4-1 | registry/mainline.json active_run_id 确认 | ⬜ |
| L4-2 | 主基线 run_config.json 参数（ridge/rolling/48/topn_ew/150）未漂移 | ⬜ |
| L4-3 | config.py 时间区间正确 | ⬜ |

### Phase 4：PIT 层

| # | 检查项 | 状态 |
|---|--------|------|
| L5-1 | hk_hold 系列 PIT 口径验证 | ⬜ |
| L5-2 | analyst_eps_revision PIT 口径确认 | ⬜ |
| L5-3 | 3 个修复因子测试期 NaN 率合理 | ⬜ |

### Phase 5：执行层

| # | 检查项 | 状态 |
|---|--------|------|
| L6-1 | git commit（含修复文件，commit message 含 [TEST_SET_RUN_1] 准备标注）| ⬜ |
| L6-2 | 正式重跑 `python scripts/run_test_pipeline.py --run-id 1` | ⬜ |
| L6-3 | 重跑后验证：composite 168 期 / optimizer_meta 36 行 / trades 36 行 / 持仓动态变化 | ⬜ |
| L6-4 | 更新 CLAUDE.md §1.1 测试集计数和 IR 结果 | ⬜ |

---

## 附：上次运行 vs 本次预期

| 产物 | test_run_1（已废除）| 修复后预期 |
|------|-------------------|-----------|
| `composite.parquet` 行数 | 106（2014-2022）| 168（2014-2025）|
| `optimizer_meta` 2023+ 行数 | **0** | **36** |
| `trades_valid` 行数 | **1** | **~36**（每次调仓 1 条）|
| `actual_weights` 类型 | 静态 147 只不变 | 动态，每月持仓调整 |
| `fallback_level` | N/A | 全为 **2**（topn_ew 强制路径）|
| `solver_status` | N/A | 全为 **"topn_ew_forced"** |
| IR 来源 | 2022-12 持仓漂移 | 真实 rolling48 + TopN150 EW |
