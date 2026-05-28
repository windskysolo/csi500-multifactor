# v1.0 — 基线因子评估结果（旧口径，训练期 2016-2022）

> **归档日期**：2026-05-23  
> **Git Commit**：`20f3641` (feature/expand-train-2012)  
> **注意**：本版本**不含 config 快照**，因为归档时 config.py 已更新为 2012 起点新口径。  
> **下一步**：阶段 4-8 重跑后，将新口径结果归档为 v1.1。

---

## 本版快照的含义

这是训练期扩展到 2012 之前，**旧口径 pipeline 的因子评估结果存档**。

- 因子评估实际使用的数据窗口：**2016-01-29 ～ 2022-12-30**（84 个月度截面）
- 涵盖 28 个候选因子，最终筛选出 **6 个入选因子**
- **不含**信号合成、组合优化、回测、归因结果（这些阶段当时尚未完成）
- config 已切换到新口径（2012 起点），因此本版**故意不保存 config 快照**

---

## 文件结构

```
v1.0/
├── README.md                          ← 本文件
├── NOTES.md                           ← 版本说明模板（关键指标待填）
├── manifest.json                      ← 自动记录的元信息
└── reports/
    ├── factor_evaluation/             ← 核心结果（28个因子评估）
    │   ├── factor_overview.csv        ← 每个因子的覆盖统计
    │   ├── ic_result.csv              ← IC 时序统计（训练期）
    │   ├── factor_summary.csv         ← 汇总：IC_IR、入选标志、加权等
    │   ├── seg_ic_ir.csv              ← 分段 IC_IR（前后各一半训练期）
    │   ├── valid_comparison.csv       ← 训练期 vs 验证期 IC_IR 对比
    │   ├── quintile_summary.csv       ← 五分组年化收益 & 多空 Sharpe
    │   ├── shift_result.csv           ← 时间错位检验（防未来函数）
    │   ├── factor_correlation.csv     ← 因子间相关系数矩阵
    │   ├── final_factors.json         ← 最终入选因子列表
    │   └── factor_evaluation_report.md ← 完整评估报告（含图表说明）
    ├── alt_data_coverage_report.md    ← 备选数据覆盖率检查报告
    ├── analysis_v2_results.md         ← v2 分析摘要
    ├── data_expansion_probe_2010_2025.md ← API 探针报告（12类接口）
    └── processed_coverage_report.md   ← processed 层数据覆盖报告
```

---

## 各结果文件说明

### factor_overview.csv — 因子覆盖统计

| 列名 | 含义 |
|------|------|
| 期数 | 有数据的月度截面数（满值为 84） |
| 起始 / 截止 | 实际覆盖的日期范围 |
| 均有效股票数 | 每期有有效因子值的平均股票数 |
| 最少股票数 | 所有期中最少的有效股票数（0 表示某些期全 NaN） |
| 有效率% | 均有效股票数 / 中证 500 成分股数（≈1065）× 100 |

**关注点**：`margin_ratio`、`short_ratio`（融资融券）有效率约 32%，且 margin 从 2013 年起才有数据（已知限制）。

---

### ic_result.csv — IC 时序统计（训练期）

每行是一个因子在训练期的截面 IC 统计量。

| 列名 | 含义 |
|------|------|
| n | 参与统计的月度期数（部分因子 < 70，因早期 NaN） |
| ic_mean | IC 均值 |
| ic_std | IC 标准差 |
| ic_ir | IC 信息比率（ic_mean / ic_std） |
| t_stat | t 统计量（ic_mean / ic_std × √n） |
| p_value | 原始 p 值 |
| p_value_bh | BH 多重检验校正后 p 值 |
| significant_bh | 是否通过 BH 校正（|IC_IR| 显著） |
| pct_positive | IC > 0 的月份占比 |
| pct_consistent_dir | 与因子方向一致的月份占比 |
| effective | 是否通过有效性筛选 |

**本版训练期结果摘要（IC_IR 排序）**：

| 因子 | IC_IR | IC均值 | 方向 | BH显著 |
|------|-------|--------|------|--------|
| turn_20d | -0.636 | -0.070 | -1 | ✓ |
| q_roe | 0.611 | 0.063 | +1 | ✓ |
| holder_chg | 0.538 | 0.034 | +1 | ✓ |
| roa | 0.524 | 0.050 | +1 | ✓ |
| roe | 0.519 | 0.053 | +1 | ✓ |
| ivol_60d | -0.519 | -0.056 | -1 | ✓ |
| amihud | 0.508 | 0.035 | +1 | ✓ |
| rev_yoy | 0.508 | 0.033 | +1 | ✓ |
| ... | | | | |
| fcfp | 0.290 | 0.013 | +1 | ✗（未通过） |
| mom_6_1 | 0.276 | 0.031 | +1 | ✗ |
| ret_1m | -0.241 | -0.024 | -1 | ✗ |
| large_net_inflow | 0.132 | 0.012 | +1 | ✗ |
| sp_ttm | 0.113 | 0.010 | +1 | ✗ |
| bp | 0.105 | 0.012 | +1 | ✗ |
| accrual | -0.095 | -0.006 | -1 | ✗ |
| leverage | -0.093 | -0.006 | -1 | ✗ |

20 个因子 BH 显著，8 个不显著。

---

### seg_ic_ir.csv — 分段 IC_IR（稳定性检验）

将训练期均分为两段（2016-2018 / 2019-2021），分别计算 IC_IR，检验因子效力是否跨期稳定。

**关注点**：大多数因子在 2016-2018 段 IC_IR 明显高于 2019-2021 段，说明近期整体因子效力有所衰减。例如：
- `turn_20d`：-0.948 → -0.380（衰减明显）
- `cfp`：0.739 → 0.063（衰减明显）
- 相对稳定：`q_roe`（0.711 → 0.501）、`np_yoy`（0.472 → 0.500）、`rev_yoy`（0.469 → 0.542）

---

### valid_comparison.csv — 训练期 vs 验证期 IC_IR 对比

⚠️ **重要警告**：本版多个训练期强因子在验证期出现方向翻转：

| 因子 | 训练期 IC_IR | 验证期 IC_IR | 翻转 |
|------|------------|------------|------|
| q_roe | +0.611 | **-0.217** | ✓ 翻转 |
| roa | +0.524 | **-0.323** | ✓ 翻转 |
| roe | +0.519 | **-0.224** | ✓ 翻转 |
| np_yoy | +0.487 | **-0.112** | ✓ 翻转 |
| ep_ttm | +0.467 | **-0.055** | ✓ 翻转 |
| asset_turn | +0.385 | **-0.269** | ✓ 翻转 |
| gross_margin | +0.377 | **-0.149** | ✓ 翻转 |

相对稳健（未翻转）：turn_20d、ivol_60d、amihud、vol_60d、max_ret、roe_delta、margin_ratio

---

### quintile_summary.csv — 五分组收益

每个因子按截面分五组，统计各组年化收益和多空组合 Sharpe。

| 列名 | 含义 |
|------|------|
| q1_ann_ret | 最低分组年化收益 |
| q5_ann_ret | 最高分组年化收益 |
| ls_ann_ret | 多空年化收益（原始方向） |
| directional_ls_ann_ret | 多空年化收益（按因子方向调整） |
| sharpe_ls | 多空 Sharpe（原始方向） |
| directional_sharpe_ls | 多空 Sharpe（按方向调整） |

**Sharpe 最高（已调方向）**：q_roe (1.62)、roe_delta (1.48)、np_yoy (1.48)、rev_yoy (1.36)

---

### shift_result.csv — 时间错位检验

对每个因子进行 ±1/±2 期错位，检验是否存在未来数据泄露。

| 列名 | 含义 |
|------|------|
| orig_ic_ir | 原始 IC_IR |
| lag1_ic_ir | 因子滞后 1 期的 IC_IR |
| lead1_ic_ir | 因子超前 1 期的 IC_IR（> orig 说明有未来泄露嫌疑） |
| ic_ir_drop | 滞后 1 期后 IC_IR 的下降量 |
| diagnosis | no_drop / weak_drop / strong_drop |
| lead_reverse | 超前 1 期后方向是否翻转 |
| warning | 是否触发时间错位警告 |

**结论**：所有因子诊断均为 `no_drop` 或 `weak_drop`，无 `strong_drop`，未发现明显未来函数。但以下因子触发了 `warning`（lead1 异常高）：leverage、accrual、margin_ratio、asset_turn、holder_chg、sp_ttm、turn_20d、bp、np_yoy、large_net_inflow、max_ret、q_roe、ret_1m、roe_delta。

---

### final_factors.json — 最终入选因子

本版最终选入 **6 个因子**：

```
amihud, rev_yoy, ep_ttm, cfp, gross_margin, mom_12_1
```

排除了 21 个因子（含训练期表现强但验证期翻转的 q_roe、roa、roe 等）。

---

### factor_correlation.csv — 因子相关系数矩阵

28×28 的截面因子相关系数，用于识别冗余因子对。（具体数值见文件，PNG 图在 reports/ 原目录）

---

## 本版存在的主要问题

1. **训练期仅从 2016 年起**，样本量偏少（70 个月度截面），是启动数扩展的直接原因
2. **多个盈利类因子（q_roe、roa、roe、np_yoy）在验证期翻转**，入选的 ep_ttm 也翻转，需要关注
3. **因子效力分段衰减明显**，2019-2021 段普遍弱于 2016-2018 段
4. **最终只选了 6 个因子**，低于计划的 10-15 个，因子多样性不足
5. **无回测、无信号、无组合结果**，pipeline 在因子评估后尚未完成

---

## 后续对比方向（v1.1 重跑后关注）

- 训练期扩展到 2012 后，IC_IR 是否更稳定（更多样本）
- 翻转的盈利类因子在更长训练期下是否恢复稳定
- 备选因子（15 个）加入后入选数量能否达到 10-15 个
- 分段 IC_IR 在 2012-2015 段的表现如何
