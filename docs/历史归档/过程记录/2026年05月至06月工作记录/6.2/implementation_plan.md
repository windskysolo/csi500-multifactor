# hk_hold 季度化修复 — 实验新线实施计划

生成日期：2026-06-02  
适用目录：`E:\Acoding\Project\500 improve`  
关联问题：`current work/6.1/issues_and_repair_notes.md` — P0 问题

---

## 0. 总体目标与约束

### 目标

将 `hk_hold_ratio` / `hk_hold_chg` 两个因子从"日频北向持股"改造成"季度披露 PIT 因子"，使验证期和测试期实验条件一致，然后重跑 train+valid 主线并进入测试集。

### 硬约束

| 约束 | 说明 |
|------|------|
| **不动现有 runs** | `runs/train_valid/*` 所有目录保持不变，历史实验可溯源 |
| **不动其他因子面板** | `data/processed/factor_panels/` 中除 `hk_hold_ratio.parquet` / `hk_hold_chg.parquet` 外的所有文件不修改 |
| **不动测试集审计产物** | `factor_panels_test_run_1/`、`factor_panels_test_run_2/` 目录及 `runs/test/test_run_1__...__VOIDED/` 保持原样 |
| **新线独立命名** | 新 pipeline spec / 新 run_id 包含 `hk_quarterly` 标识，与历史 run 清晰区分 |
| **测试集纪律不变** | 只有新线通过 2021-2022 验证并 promote 后，才允许正式 `[TEST_SET_RUN_1]` |

---

## 1. 背景诊断

### 1.1 问题根因

2024-08-19 起，港交所/上交所调整北向持股披露频率：
- 旧规则：每日收盘后披露各股北向持仓
- 新规则：每季度第 5 个沪深股通交易日公布**上季度末**持仓数据

导致 `hk_hold.parquet`（原始数据）在 2024-08-17 后只有季度末快照：
```
2024-09-30 / 2024-12-31 / 2025-03-31 / 2025-06-30 / 2025-09-30 / 2025-12-31
```

旧因子定义（`start = T - 5日历天`）在季末月月末之外的月份取不到数据 → 全空月。

### 1.2 受影响文件

| 文件 | 状态 | 处置 |
|------|------|------|
| `src/factors/alt_factors.py` | 需修改（两个函数 + 两个常量） | Step 1 |
| `data/processed/factor_panels/hk_hold_ratio.parquet` | 需删除并重建（2012-2022，旧日频定义） | Step 3 |
| `data/processed/factor_panels/hk_hold_chg.parquet` | 需删除并重建（2012-2022，旧日频定义） | Step 3 |
| `configs/pipelines/rolling48_topn150_ew_hk_quarterly.py` | 新建 | Step 4 |
| 其余 58 个因子面板、所有历史 runs | **不动** | — |

---

## 2. 分步实施计划

### Step 1：修改因子定义（`src/factors/alt_factors.py`）

**改动范围**：约 40 行，只改 `factor_hk_hold_ratio` 和 `factor_hk_hold_chg` 两个函数及其相关常量。

#### 1.1 新增常量（替换旧的 `WINDOW_HK_HOLD = 30`）

```python
# 旧常量 WINDOW_HK_HOLD = 30（交易日）保留但仅用于兼容旧函数签名
# 新增季度定义相关常量
WINDOW_HK_HOLD_QUARTERLY = 270  # 约3个自然季度，保证能取到最近2个季度披露点
MAX_HK_HOLD_STALENESS_DAYS = 120  # 最大允许滞后天数（> 1季约92天，留缓冲）
HK_HOLD_QUARTERLY_OFFSET_DAYS = 10  # 季末后约5个交易日 ≈ 7-10日历天，保守估计披露滞后
```

#### 1.2 修改 `factor_hk_hold_ratio`

**新定义**：取 T 日之前最近一个季度披露快照的 ratio（最大滞后 120 天，超过置 NaN）。

```python
def factor_hk_hold_ratio(rebalance_date, codes):
    """
    北向持仓比例（沪深港通持股占流通股比例）— 季度 PIT 快照版。

    2024-08-19 起港交所改为每季度第5个沪深股通交易日公布上季度末数据，
    因此此因子从"日频"改为"最近可得季度披露快照"。

    PIT 规则：
      - 只使用 trade_date <= T - HK_HOLD_QUARTERLY_OFFSET_DAYS 的记录
        （保守偏移10个日历天，覆盖披露公告滞后）
      - 在 [T - WINDOW_HK_HOLD_QUARTERLY, T - offset] 窗口内取最新快照
      - 若最新快照与 T 距离超过 MAX_HK_HOLD_STALENESS_DAYS → 置 NaN

    Returns:
        ts_code → 北向持仓比例（%）；无有效快照或超滞后时为 NaN
    """
    pit_cutoff = rebalance_date - pd.Timedelta(days=HK_HOLD_QUARTERLY_OFFSET_DAYS)
    window_start = rebalance_date - pd.Timedelta(days=WINDOW_HK_HOLD_QUARTERLY)

    hk = load_hk_hold(window_start, pit_cutoff, codes=codes)
    if hk.empty:
        return _nan_series(codes, "hk_hold_ratio")

    snap = hk["ratio"].groupby(level="ts_code").last()

    # 取最新快照对应的日期，超过最大滞后则置 NaN
    latest_dates = hk.index.get_level_values("trade_date").groupby(
        hk.index.get_level_values("ts_code")
    )
    # 用 staleness filter：若最近一次披露日距 T 超过阈值则置 NaN
    staleness = (rebalance_date - hk.groupby(level="ts_code").apply(
        lambda g: g.index.get_level_values("trade_date").max()
    )).dt.days
    snap = snap.where(staleness <= MAX_HK_HOLD_STALENESS_DAYS)

    snap.name = "hk_hold_ratio"
    return snap.reindex(codes)
```

> **注意**：上述伪代码展示逻辑，实际 staleness 计算需用 `groupby + transform` 处理 MultiIndex，实现时补全细节。核心不变：只用 `<= pit_cutoff` 的数据，取最近披露值，超 120 天置 NaN。

#### 1.3 修改 `factor_hk_hold_chg`

**新定义**：最近两个季度披露点的 ratio 差值（均须 `<= pit_cutoff`，不足两个点则置 NaN）。

```python
def factor_hk_hold_chg(rebalance_date, codes):
    """
    北向持仓比例变化（最近两个季度披露快照之差）— 季度 PIT 版。

    Δratio = ratio_latest_quarter - ratio_prev_quarter
    两个披露点都必须 <= T - HK_HOLD_QUARTERLY_OFFSET_DAYS。
    若最新点距 T 超过 MAX_HK_HOLD_STALENESS_DAYS，或只有一个披露点 → 置 NaN。

    Returns:
        ts_code → ratio 变化量（百分点）；数据不足时为 NaN
    """
    pit_cutoff = rebalance_date - pd.Timedelta(days=HK_HOLD_QUARTERLY_OFFSET_DAYS)
    window_start = rebalance_date - pd.Timedelta(days=WINDOW_HK_HOLD_QUARTERLY)

    hk = load_hk_hold(window_start, pit_cutoff, codes=codes)
    if hk.empty:
        return _nan_series(codes, "hk_hold_chg")

    ratio = hk["ratio"].unstack(level="ts_code")
    if ratio.shape[0] < 2:
        return _nan_series(codes, "hk_hold_chg")

    # 取最后两个有效行（非全NaN行）
    valid_rows = ratio.dropna(how="all")
    if valid_rows.shape[0] < 2:
        return _nan_series(codes, "hk_hold_chg")

    result = valid_rows.iloc[-1] - valid_rows.iloc[-2]
    result.name = "hk_hold_chg"
    return result.reindex(codes)
```

> **精细化说明**：`valid_rows.dropna(how="all")` 会去掉全空的日期行，因为季度披露下各股票披露日期可能不同步；每只股票的 `latest - prev` 取自该股票自身有数据的最近两个日期，逐列处理比 `iloc[-1] - iloc[-2]` 更准确。实现时应改为逐股按 `groupby` 取最后两个非 NaN 点。

#### 1.4 更新 docstring 及模块顶部说明

- 在 `alt_factors.py` 顶部的 docstring 中，将 `hk_hold*` 的 "已知覆盖限制" 更新为：
  ```
  hk_hold*：
    - 2014-11-17 前无数据（沪深港通开通前）→ 全 NaN 属于预期
    - 2024-08-19 后改为季度披露快照，因子定义已同步调整（见实施计划 current work/6.2）
  ```

---

### Step 2：编写单元测试（`tests/test_hk_hold_quarterly.py`）

新增测试文件，覆盖以下场景：

| 测试用例 | 预期结果 |
|---------|---------|
| `T` 在季末第5交易日之前（无最新季度数据）→ 用上季度数据 | 返回上季度 ratio，非 NaN |
| 最近披露点距 T 超过 120 天 | 返回 NaN（staleness 保护生效） |
| 只有一个披露点（chg 场景） | `hk_hold_chg` 返回 NaN |
| 两个连续季度均有数据 | `hk_hold_chg` = ratio_q2 - ratio_q1 |
| 沪深港通开通前（< 2014-11-17） | 全 NaN（沿用原有逻辑） |
| `load_hk_hold` 返回空 | 全 NaN，不抛异常 |

---

### Step 3：删除旧 hk_hold 因子面板并重建

> **操作前确认：此步骤会删除两个 parquet 文件，执行前向用户展示文件路径并等待确认。**

#### 3.1 删除旧训练+验证期面板（仅这两个文件）

```powershell
# 确认路径存在后再删除
Remove-Item "e:\Acoding\Project\500 improve\data\processed\factor_panels\hk_hold_ratio.parquet" -Confirm
Remove-Item "e:\Acoding\Project\500 improve\data\processed\factor_panels\hk_hold_chg.parquet" -Confirm
```

#### 3.2 重建这两个因子面板（2012-01 ~ 2022-12）

使用 `build_factor_panels.py` 的增量参数，**只重建这两个因子**，其余 58 个面板文件不触碰：

```powershell
cd "e:\Acoding\Project\500 improve"
python -m scripts.build_factor_panels --factors hk_hold_ratio hk_hold_chg
```

预计运行时间：约 20-40 分钟（~132 个调仓日 × 2 个因子，每期调用 `load_hk_hold` 一次）。

#### 3.3 验证重建结果

```python
import pandas as pd
ratio = pd.read_parquet("data/processed/factor_panels/hk_hold_ratio.parquet")
chg   = pd.read_parquet("data/processed/factor_panels/hk_hold_chg.parquet")

# 训练期覆盖检查（2014-11-17 后应有有效数据）
print("ratio shape:", ratio.shape)
print("ratio 有效占比（2015以后）:", ratio.loc["2015":].notna().mean().mean())

# 全空月检查（训练+验证期内不应有全空月）
empty_months_ratio = ratio.loc["2015":].isna().all(axis=1)
print("全空月（ratio）:", empty_months_ratio.sum())

empty_months_chg = chg.loc["2016":].isna().all(axis=1)
print("全空月（chg）:", empty_months_chg.sum())
```

期望：
- 2015年后 ratio 有效覆盖率 > 60%
- 2021-2022 验证期内全空月 = 0

---

### Step 4：新建 Pipeline Spec

创建 `configs/pipelines/rolling48_topn150_ew_hk_quarterly.py`：

```python
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="rolling48_topn150_ew_hk_quarterly",
    description=(
        "hk_hold 季度化修复后的主线重跑（2026-06 新线）。"
        "hk_hold_ratio / hk_hold_chg 已改为季度 PIT 快照因子。"
        "其余 58 个因子面板直接复用，仅信号+下游重跑。"
        "对照基准：rolling48_topn150_ew(run_id=20260530_111129, IR=2.274)。"
    ),
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="rolling",
        purge_months=2,
        window_months=48,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
    ),
    optimizer=OptimizerSpec(
        optimizer_mode="topn_ew",
        topn=150,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
```

> 此 spec 与 `rolling48_topn150_ew` **完全相同的超参**，唯一区别是 hk_hold 因子定义已在 Step 1 修改。新 `experiment_id` 带 `_hk_quarterly` 后缀，确保 run_id 可区分。

---

### Step 5：运行 train+valid 实验

```powershell
cd "e:\Acoding\Project\500 improve"
python -m scripts.run_experiment --spec configs/pipelines/rolling48_topn150_ew_hk_quarterly.py
```

**必须从 signal 阶段完整运行**（不能用 `--from-stage portfolio`），因为因子面板变更会影响 Ridge 训练的系数。

预计运行时间：约 2-4 小时（完整信号生成 + portfolio + backtest）。

运行完成后记录新 run_id（格式：`2026XXXX_XXXXXX__rolling48_topn150_ew_hk_quarterly`）。

---

### Step 6：对比验证

```powershell
python -m scripts.compare_runs \
    <new_run_id> \
    20260530_111129__rolling48_topn150_ew \
    20260529_034947__frozen_baseline_icir_topn50_ew
```

**评判标准（必须同时满足三项硬指标才允许 promote）**：

| 指标 | 门槛 | 目标 |
|------|------|------|
| IR（验证期 2021-2022）| ≥ 0.5 | 接近或优于 2.274（历史主线） |
| 超额最大回撤 | ≤ 10% | — |
| 年化双边换手率 | 500% ~ 1500% | — |

同时查看以下对比维度：
1. IR 是否与历史主线接近（hk_hold 是否仍贡献正向信号）
2. hk_hold 系列因子的 IC_IR（新定义下是否仍有效）
3. Ridge 系数中 hk_hold 的权重变化

---

### Step 7：Promote 新主线

若 Step 6 通过三项硬指标：

```powershell
python -m scripts.promote_run --run-id <new_run_id> --reason "hk_hold季度化修复后主线，验证期IR=X.XXX，三硬指标PASS"
```

更新 `registry/mainline.json`（由脚本自动完成）。

同步更新：
- `CLAUDE.md` §1.1 当前状态：验证期最优 IR、已晋升主线 IR、最后一次有效实验
- `docs/research/factor_roadmap/factor_research_guide.md`：hk_hold 因子状态更新

---

### Step 8：重新执行 L1-L6 检查

基于新主线重新运行 `current work/6.1/` 的检查脚本：

```powershell
cd "e:\Acoding\Project\500 improve"
python "current work\6.1\check_l1_l2.py"
python "current work\6.1\check_l3_l4.py"
python "current work\6.1\check_remaining.py"
```

重点关注：
- P0（hk_hold 覆盖）：全空月应归零
- P2（fwd return 边界）：保持 PASS 即可

产出新版检查报告到 `current work/6.2/` 目录：
- `l1_l2_inspection_report_hk_quarterly.md`
- `l3_l4_inspection_report_hk_quarterly.md`
- `remaining_inspection_report_hk_quarterly.md`

---

### Step 9：正式测试集运行（`[TEST_SET_RUN_1]`）

**前置条件检查清单（全部满足才执行）**：

- [ ] Step 7 完成，mainline 已更新为 `hk_quarterly` 版本
- [ ] Step 8 L1-L6 全部 PASS（无 FAIL，WARN 已记录且可接受）
- [ ] `docs/logs/test_set_runs.json` 确认 `active_runs = []`，有效次数 = 0
- [ ] git status 干净（或已 commit 当前修改）

执行命令：

```powershell
python scripts/run_test_pipeline.py --run-id 1
```

提交必须含 `[TEST_SET_RUN_1]` 标记。

---

## 3. 关键风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| 季度定义下 hk_hold IC_IR 大幅下降，新线 IR 低于历史主线 | 中 | 高 | 先跑验证看 IC_IR；若 hk_hold 贡献近零，转路线 B（剔除因子） |
| 季末偏移量不准确导致 PIT 泄漏 | 低 | 极高 | 单元测试逐案核对；时间错位测试（因子整体后移一期 IC 应显著下降） |
| `build_factor_panels` 意外覆盖其他因子文件 | 低 | 高 | 严格使用 `--factors hk_hold_ratio hk_hold_chg`，完成后 diff 文件时间戳 |
| 新线与旧线因子面板版本混用 | 中 | 中 | 新 run 的 factor_panels 仍来自同一目录，用 manifest 记录构建时间戳 |
| staleness 阈值（120天）太宽导致用了过时数据 | 低 | 中 | 诊断报告中输出各调仓日实际使用的披露日，确认无 >120天 情况 |

---

## 4. 不在本次计划范围内的变更

以下内容**不做**，避免范围蔓延：

- 修改 `load_hk_hold`（底层 loader 不变，因子逻辑层解决 PIT 问题）
- 修改其他任何因子定义
- 修改优化器超参（topn=150, rolling48 等）
- 处理 P1（excess_return 语义）、P2（fwd return 边界）问题——这些不阻止测试
- 清理 `factor_panels_test_run_1/` 或 `factor_panels_test_run_2/` 目录（保留审计证据）
- 修改 `factor_panels_test_run_*`（测试集面板将在 Step 9 的 `run_test_pipeline.py` 中自动重建到新目录）

---

## 5. 实施顺序总结

```
Step 1  修改 alt_factors.py（factor_hk_hold_ratio / factor_hk_hold_chg）
Step 2  新增单元测试 tests/test_hk_hold_quarterly.py
Step 3  删除 + 重建训练验证期两个 hk_hold 面板
Step 4  新建 pipeline spec: rolling48_topn150_ew_hk_quarterly.py
Step 5  运行完整 train+valid 实验（完整 signal → portfolio → backtest）
Step 6  compare_runs 对比（含 frozen_baseline 作下限）
Step 7  通过三硬指标则 promote 新主线
Step 8  重新执行 L1-L6 检查，产出新版报告
Step 9  全通过后正式运行 [TEST_SET_RUN_1]
```

**总预估工时**：  
- 代码修改（Step 1-2）：1-2 小时  
- 面板重建（Step 3）：0.5-1 小时（机器跑）  
- 实验运行（Step 5）：2-4 小时（机器跑）  
- 验证与 promote（Step 6-8）：0.5-1 小时  
- 测试集（Step 9）：1-2 小时（机器跑）

**总计约 0.5-1 工作日**（人工参与部分）+ **3-7 小时计算**。
