# P4 — 实验横向比较 `src/pipeline/compare.py`

---

## 一、文件定位

```
所属 Part  : Layer 8（Pipeline 编排层）
在数据流中 : runs/**/run_config.json + backtest/metrics_valid.parquet → compare.py → experiment_board
被谁调用   : scripts/compare_runs.py（CLI 入口）
             scripts/run_test_pipeline.py（测试集结果对比）
调用谁     : src/pipeline/registry.py（load_mainline / load_challengers）
             src/config.py（VALID_START / VALID_END / DATA_PROC）
             scipy.stats（Spearman IC 计算 / 配对 t 检验）
```

**实际行数：381 行**（PLAN.md 快照为 300 行，已更新）。

`compare.py` 负责从多个 run 的产物目录中**读取并聚合指标**，生成横向比较表。核心功能：从磁盘文件还原出每个 run 的关键参数和性能指标，不需要重新运行任何计算（只读）。

---

## 二、时间对齐与 PIT 假设

`compare.py` 在读取 IC 统计时有明确的时间窗口控制：

```python
valid_start = pd.Timestamp(cfg.VALID_START)   # 2021-01-01
valid_end   = pd.Timestamp(cfg.VALID_END)     # 2022-12-31
common_dates = common_dates[(common_dates >= valid_start) & (common_dates <= valid_end)]
```

**比较板里的 IC/IR 是验证期（2021-2022）的统计，不是训练期**，确保与回测指标（同样是验证期）口径一致。

---

## 三、模块顶部

```python
# Hard threshold constants（阈值来自领域规范，不可随意修改）
IR_MIN = 0.5
MDD_MAX_ABS = 0.10
TO_MIN_PCT = 500.0
TO_MAX_PCT = 1500.0
```

这四个阈值决定了 `ir_pass / mdd_pass / to_pass` 三列的判断逻辑，是比较板最核心的输出。`compare_runs.py` 直接 import 这四个常量用于在 Markdown 表头显示阈值。

---

## 四、核心函数逐一解析

### 4.1 `load_run_metrics(run_id, scope, runs_root)` — 单个 run 指标加载（L24-114）

这是整个模块最核心的函数，从一个 run 目录还原出**全部可比较字段**：

```python
def load_run_metrics(run_id, scope="train_valid", runs_root=None) -> dict:
    """
    Returns dict with keys:
      run_id, experiment_id, signal_method, training_mode, window_months,
      te_target_pct, turnover_lambda, topn, optimizer_mode, is_test_set,
      composite_sha256,
      IC_mean, IC_IR, IC_t, IC_p,
      fallback_L0_cnt ~ fallback_L3_cnt,
      IR, excess_return_pct, excess_max_drawdown_pct,
      tracking_error_pct, monthly_win_rate_pct, annual_turnover_pct,
      ir_pass, mdd_pass, to_pass, status
    """
```

**读取链路**（按顺序依次尝试）：

1. `run_config.json` → spec 参数（experiment_id / signal_method / optimizer 等）
2. `RUN_FINISHED.json` 存在性 → status（"incomplete" / 继续）
3. `signal/composite.parquet` + `data/processed/fwd_ret_panel.parquet` → IC 统计（动态计算）
4. `portfolio/optimizer_meta.parquet` → fallback 次数统计
5. `backtest/metrics_valid.parquet` → 六项回测指标
6. `backtest/trades_valid.parquet` → 年化换手率（计算）

**status 字段**：
- `"no_config"` — run_config.json 不存在
- `"incomplete"` — RUN_FINISHED.json 不存在（run 未完成）
- `"no_metrics"` — metrics_valid.parquet 不存在
- `"finished"` — 正常完成

**任何一步失败都不抛异常，而是设置 status 并返回部分数据**，确保单个损坏的 run 不会破坏整个比较板。

---

### 4.2 `_compute_ic_stats(run_dir)` — 动态 IC 计算（L283-353）

**IC 是动态计算的，不是从缓存文件读取的**：

```python
composite = pd.read_parquet(composite_path)    # signal/composite.parquet
fwd_ret   = pd.read_parquet(fwd_path)          # data/processed/fwd_ret_panel.parquet

# 只取验证期
common_dates = composite.index.intersection(fwd_ret.index)
common_dates = common_dates[(common_dates >= valid_start) & (common_dates <= valid_end)]

# 每期计算 Spearman IC
for dt in common_dates:
    sig = comp.loc[dt].dropna()
    ret = fwd.loc[dt].dropna()
    ic_val, _ = sp_stats.spearmanr(sig[aligned].values, ret[aligned].values)
    ic_list.append(float(ic_val))

# IC 序列 → IC_mean / IC_IR / IC_t / IC_p
ic_ir = ic_mean / ic_std
ic_t  = ic_ir * math.sqrt(n)
ic_p  = 2 * (1 - sp_stats.t.cdf(abs(ic_t), df=n-1))
```

**为何动态计算而不用 `signal/ic_detail.parquet`？**

`ic_detail.parquet` 只有 `icir` 信号路径生成（`_run_signal_icir` 写入），`ridge` 路径没有这个文件。为了给所有信号路径提供统一的 IC 统计，`compare.py` 统一从 `composite.parquet` 和 `fwd_ret_panel` 动态计算，不依赖特定信号方法的中间产物。

**计算使用验证期 IC**（`valid_start ~ valid_end`）：与回测结果同样来自验证期，口径一致。

---

### 4.3 `_load_fallback_counts(run_dir)` — Fallback 统计（L268-280）

```python
meta = pd.read_parquet(meta_path)   # portfolio/optimizer_meta.parquet
counts = meta["fallback_level"].value_counts()
return {f"fallback_L{i}_cnt": int(counts.get(i, 0)) for i in range(4)}
```

Fallback 统计来自 `optimizer_meta.parquet` 的 `fallback_level` 列，计数 L0/L1/L2/L3 各触发多少次。

典型诊断场景：
- `fallback_L0_cnt` 很低（L0=正常 QP 求解）→ 说明约束频繁违反，优化器质量不稳定
- `fallback_L2_cnt` / `fallback_L3_cnt` 高 → 信号可能噪声过大

---

### 4.4 `compare_runs(run_ids, scope, runs_root)` — 多 run 比较（L117-152）

```python
def compare_runs(run_ids, scope="train_valid", runs_root=None) -> pd.DataFrame:
    rows = [load_run_metrics(r, scope, runs_root) for r in run_ids]
    df = pd.DataFrame(rows)

    col_order = [
        "run_id", "experiment_id", "signal_method", "training_mode", "window_months",
        "te_target_pct", "turnover_lambda", "topn", "optimizer_mode", "is_test_set",
        "composite_sha256",
        "IC_mean", "IC_IR", "IC_t", "IC_p",
        "fallback_L0_cnt", "fallback_L1_cnt", "fallback_L2_cnt", "fallback_L3_cnt",
        "IR", "excess_return_pct", "excess_max_drawdown_pct",
        "tracking_error_pct", "monthly_win_rate_pct", "annual_turnover_pct",
        "ir_pass", "mdd_pass", "to_pass", "status",
    ]
    # 按 IR 降序排列（NaN 排末）
    df = df.sort_values("IR", ascending=False, na_position="last")
```

**`composite_sha256`**：manifest.json 中 `composite.parquet` 的 sha256 前 8 位。即使两个 run 的 experiment_id 相同，sha256 不同说明用了不同的信号，是快速审计的关键字段。

---

### 4.5 `build_board_from_registry(scope, runs_root, registry_dir)` — 从注册表自动构建比较板（L155-211）

```python
def build_board_from_registry(...) -> pd.DataFrame:
    mainline = load_mainline(reg_dir)
    mainline_run_id = mainline["active_run_id"]

    challengers = load_challengers(reg_dir)
    for c in challengers.get("challengers", []):
        run_entries.append((rid, f"challenger/{c.get('name', rid)}"))

    df = compare_runs(run_ids, scope=scope, runs_root=runs_root)
    df.insert(1, "role", df["run_id"].map(role_map).fillna("unknown"))

    # 配对 t 检验
    if mainline_run_id:
        df["paired_p_vs_mainline"] = df["run_id"].apply(_paired_p)
```

**`role` 列**：自动标注每个 run 的角色（`"mainline"` / `"challenger/rolling48_ridge_topn50_ew"` 等），便于比较板阅读。

**`paired_p_vs_mainline`**（新增字段，PLAN.md 未提及）：对每个 challenger，计算其月度超额收益与 mainline 月度超额收益的**配对 t 检验 p 值**。p < 0.05 表示 challenger 的表现与 mainline 有统计显著差异，是评估改进是否显著的关键指标。

---

### 4.6 `_monthly_excess_returns` + `_compute_paired_p` — 配对 t 检验（L356-380）

```python
def _monthly_excess_returns(nav_path: Path) -> pd.Series:
    nav = pd.read_parquet(nav_path)
    excess_daily = nav["strategy_v2"] / nav["benchmark"]
    monthly = excess_daily.resample("ME").last()    # 月末值
    return monthly.pct_change().dropna()            # 月度超额收益率

def _compute_paired_p(mainline_exc, challenger_exc) -> float:
    common = mainline_exc.index.intersection(challenger_exc.index)
    if len(common) < 8:     # 样本不足 8 个月，不计算
        return float("nan")
    _, p = sp_stats.ttest_rel(
        challenger_exc.loc[common].values,
        mainline_exc.loc[common].values,
    )
    return round(float(p), 4)
```

**用验证期月末 NAV 计算月度收益**（`resample("ME").last()`）。要求至少 8 个月的共同观测期，样本过少时返回 NaN 而非强行计算。

---

## 五、内部辅助函数

| 函数 | 说明 |
|---|---|
| `_safe_float(v)` | 安全类型转换，失败返回 NaN |
| `_round(v, scale, dp)` | 缩放和四舍五入（如 0.12 × 100 = 12.0%）|
| `_compute_annual_turnover(trades_path)` | 从 trades_valid.parquet 计算年化换手率 |
| `_read_composite_hash(run_dir)` | 从 manifest.json 读 composite.parquet sha256 前 8 位 |
| `_load_fallback_counts(run_dir)` | 从 optimizer_meta.parquet 统计 Fallback 次数 |
| `_compute_ic_stats(run_dir)` | 动态计算验证期 IC 序列和统计量 |
| `_monthly_excess_returns(nav_path)` | 从 nav_valid.parquet 计算月度超额收益 |
| `_compute_paired_p(mainline_exc, challenger_exc)` | 配对 t 检验 p 值 |

---

## 六、落盘产物

`compare.py` 本身不写文件，由 `compare_runs.py` 脚本调用后写入：
- `reports/experiment_board.csv`（UTF-8 BOM 编码，兼容 Excel）
- `reports/experiment_board.md`（含阈值说明的 Markdown 表格）

---

## 七、相关测试

- 当前无专项测试，建议补充：
  - `load_run_metrics` 在 run 目录损坏时的容错行为
  - IC 统计的正确性（用已知信号验证 Spearman IC 计算）

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| `run_config.json` 不存在 | 返回 `{"run_id": id, "status": "no_config"}` |
| `RUN_FINISHED.json` 不存在 | 返回部分数据，`status="incomplete"` |
| `composite.parquet` 不存在 | `_compute_ic_stats` 返回全 NaN IC 字段 |
| 验证期日期数 < 4 | `_compute_ic_stats` 返回全 NaN |
| `metrics_valid.parquet` 不存在 | 返回部分数据，`status="no_metrics"` |
| `trades_valid.parquet` 不存在 | `annual_turnover_pct=NaN`，`to_pass=False` |
| 配对 t 检验样本 < 8 | `paired_p_vs_mainline=NaN` |
| 任何内部计算异常 | 各 helper 函数 `except Exception: return NaN`，不传播 |

---

## 九、数据流图

```
registry/mainline.json + registry/challengers.json
    ↓ build_board_from_registry()
    ↓ 收集 run_id 列表

for each run_id:
  runs/{scope}/{run_id}/run_config.json       → spec 参数
  runs/{scope}/{run_id}/RUN_FINISHED.json     → status 检查
  runs/{scope}/{run_id}/signal/composite.parquet
  data/processed/fwd_ret_panel.parquet        → _compute_ic_stats（动态 IC 计算）
  runs/{scope}/{run_id}/portfolio/optimizer_meta.parquet → _load_fallback_counts
  runs/{scope}/{run_id}/backtest/metrics_valid.parquet   → 六项回测指标
  runs/{scope}/{run_id}/backtest/trades_valid.parquet    → 年化换手率
  runs/{scope}/{run_id}/backtest/nav_valid.parquet       → 月度超额收益（配对 t 检验）
    ↓ compare_runs() → DataFrame（IR 降序）
    ↓ 插入 role 列 + paired_p_vs_mainline 列

scripts/compare_runs.py
    ↓ _df_to_markdown(df) → reports/experiment_board.md
    ↓ df.to_csv(...)      → reports/experiment_board.csv
```

---

## 十、领域知识补充

**为何必须在比较板中包含 `frozen_baseline_icir_topn50_ew`？**

项目治理规范要求，每次运行 `compare_runs.py` 时必须将冻结基线（IR=0.924）包含在比较列表中。原因：冻结基线是在固定数据、固定因子池下的"最坏情况下限"，任何声称有效的改进都必须在其之上。如果新方法的 IR 低于冻结基线，说明改动是负向的，无论绝对值多高都不应晋升。冻结基线一旦确定永不重跑（不可改变的参照点）。

**`composite_sha256` 的审计价值**：

两个 run 如果使用相同的 `experiment_id` 但 `composite_sha256` 不同，说明两次运行之间因子面板或数据发生了变化。这在排查"为什么重跑结果不一致"时是关键线索。在比较板中显示哈希前 8 位，可以快速判断两个 run 是否基于完全相同的信号。

**年化双边换手率的计算公式**：

```
annual_to_pct = (sum(buy_value + sell_value) / portfolio_value_before) / n_months * 12 * 100
```

每期换手率 = (买入 + 卖出) / 期初总市值，年化 = 月均换手率 × 12。注意分子是双边（买 + 卖），分母是期初总市值，这是量化行业的标准计算方式。500-1500% 的阈值对应月均双边换手约 0.4-1.25 倍组合。
