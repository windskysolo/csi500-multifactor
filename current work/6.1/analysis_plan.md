# 6.1 综合诊断分析计划
> 生成日期：2026-06-01  
> 分析目标：Ridge-48m + TopN150 EW 策略，全周期性能诊断（2021-2025）  
> 核心问题：为什么测试期 IR=0.149（大幅低于验证期 IR=2.274）

---

## 一、数据来源

| 分析内容 | 数据来源文件 | 时间区间 |
|---------|------------|---------|
| 验证期 NAV | `runs/train_valid/20260530_111129__rolling48_topn150_ew/backtest/nav_valid.parquet` | 2021-01 ~ 2022-12 |
| 测试期 NAV | `runs/test/test_run_1__20260530_111129__rolling48_topn150_ew/backtest/nav_valid.parquet` | 2023-01 ~ 2025-12 |
| 单因子 IC 全序列 | `runs/test/test_run_1__20260530_111129__rolling48_topn150_ew/signal/ic_detail.parquet` | 2012-01 ~ 2025-10（18因子 × 166期）|
| Ridge 因子权重 | `runs/test/test_run_1__20260530_111129__rolling48_topn150_ew/signal/coef_history.parquet` | 2014-03 ~ 2025-10（48m burn-in 后）|
| 组合构建元数据 | `runs/test/test_run_1__20260530_111129__rolling48_topn150_ew/portfolio/optimizer_meta.parquet` | 全期持仓数量、fallback级别 |
| 日度持仓权重 | `runs/test/test_run_1__20260530_111129__rolling48_topn150_ew/backtest/actual_weights_valid.parquet` | 2023-01 ~ 2025-12 |
| 换手明细 | `runs/test/test_run_1__20260530_111129__rolling48_topn150_ew/backtest/trades_valid.parquet` | 每次调仓 |
| 冻结基线 IC | `runs/train_valid/20260529_034947__frozen_baseline_icir_topn50_ew/signal/ic_detail.parquet` | 2012-01 ~ 2022-12（独立验证）|
| 行业分类 | `data/processed/industry.parquet` | 用于持仓行业暴露 |
| 基准成分 | `data/processed/index_member.parquet` | 行业基准权重对比 |
| 前向收益 | `runs/test/test_run_1__20260530_111129__rolling48_topn150_ew/fwd_ret_panel.parquet` | 各股票实际月收益（用于因子贡献计算）|

---

## 二、六大分析模块

### 模块 1：业绩总览（NAV & 超额收益）

**产出文件：**
- `01_nav_daily.csv` — 日度：策略NAV、基准NAV、超额（2021-2025）
- `01_monthly_excess.csv` — 月度：超额收益、累积超额、当月基准收益
- `charts/01a_cumulative_excess.png` — 累积超额曲线（含验证期/测试期分割线）
- `charts/01b_monthly_bar.png` — 月度超额柱状图（红绿条，36个月2021-2022 + 36个月2023-2025）
- `charts/01c_rolling_ir.png` — 滚动12月 IR 曲线
- `charts/01d_drawdown.png` — 超额收益回撤曲线

**关键计算：**
```
月超额 = (1+策略月收益) / (1+基准月收益) - 1
累积超额 = cumprod(1 + 月超额) - 1
滚动IR = mean(月超额[t-11:t+1]) / std(月超额[t-11:t+1]) * sqrt(12)
超额回撤 = cummax(累积超额) - 累积超额
```

**分期汇总表（年度 + 全期）：**

| 时期 | 年化超额 | IR | 月胜率 | 超额MDD | 跟踪误差 |
|------|---------|-----|--------|---------|---------|
| 2021 | ? | ? | ? | ? | ? |
| 2022 | ? | ? | ? | ? | ? |
| 2023 | ? | ? | ? | ? | ? |
| 2024 | ? | ? | ? | ? | ? |
| 2025 | ? | ? | ? | ? | ? |
| 验证期（2021-2022）| ? | ? | ? | ? | ? |
| 测试期（2023-2025）| ? | ? | ? | ? | ? |

---

### 模块 2：单因子 IC 深度分析

**产出文件：**
- `02_ic_annual_by_factor.csv` — 每因子每年 IC均值 和 IC_IR（18因子 × 14年 2012-2025）
- `02_ic_monthly.csv` — 每因子每月 IC（精确到期）
- `charts/02a_ic_heatmap_annual.png` — 热力图：因子（行）× 年份（列），色块=IC均值
- `charts/02b_ic_ir_comparison.png` — 柱状对比图：18因子 × 2（2021-22 vs 2023-25）的IC_IR
- `charts/02c_ic_decay_ts.png` — 每个因子的IC时序折线（2021-2025），识别哪些因子 2023 后 IC 翻转

**核心诊断逻辑：**
- 找出在 2021-2022 IC_IR > 0.3 但 2023-2025 IC_IR < 0.1 的因子（衰减最严重）
- 找出在 2023-2025 IC_IR 方向反转的因子（从正向变负向）
- 计算"IC 稳定性"：跨两期的 IC 相关系数

---

### 模块 3：Ridge 因子权重演变

**产出文件：**
- `03_factor_weights.csv` — 每期每因子 Ridge 系数（2014-2025）
- `charts/03a_factor_weight_heatmap.png` — 热力图：因子（行）× 时期（列），值=Ridge系数
- `charts/03b_weight_ts_top8.png` — 权重最高的8个因子时序折线图

**核心诊断逻辑：**
- 哪些因子权重在测试期（2023+）发生显著变化
- 权重与 IC 的相关性（高权重因子是否也有高 IC？）

---

### 模块 4：组合特征分析

**产出文件：**
- `04_portfolio_stats.csv` — 每期：持仓数量、有效N（1/Σwi²）、换手率、fallback级别
- `charts/04a_n_holdings.png` — 持仓数量时序（测试期）
- `charts/04b_concentration.png` — 持仓集中度（有效N）时序

**关键计算：**
```
有效N = 1 / sum(w_i^2)
月换手 = sum(|w_t - w_{t-1}|) / 2
```

---

### 模块 5：因子贡献度（简化归因）

**产出文件：**
- `05_factor_contribution.csv` — 每期每因子的"预测收益 vs 实际收益"对比
- `charts/05a_factor_contribution_bar.png` — 每个因子在验证期 vs 测试期的贡献累积

**计算方法：**
```
期望贡献_i = weight_i × IC_i  （信号质量代理）
实际贡献_i = weight_i × corr(signal_i, fwd_ret)  （实际信号-收益对应）
```
注：这是信号层面的简化归因，不是完整的 Brinson 分解。

---

### 模块 6：综合诊断摘要

**产出文件：**
- `06_summary_report.md` — 自动生成的文字报告，含：
  - 关键数字汇总表
  - IC 衰减最严重的前3因子列表
  - 验证期 vs 测试期对比表（IR、超额收益、因子有效性）
  - 下一步改进方向建议（基于数据）

---

## 三、执行顺序

```
Step 1: 运行 analysis.py
  → 读取所有parquet文件
  → 计算所有中间量
  → 生成 6 个 CSV + charts/ 目录中的图表

Step 2: 阅读 06_summary_report.md
  → 自动包含所有关键数字
  → 指出最值得关注的因子和时期
```

---

## 四、输出目录结构

```
current work/6.1/
├── analysis_plan.md        ← 本文件
├── analysis.py             ← 分析脚本
├── 01_nav_daily.csv
├── 01_monthly_excess.csv
├── 02_ic_annual_by_factor.csv
├── 02_ic_monthly.csv
├── 03_factor_weights.csv
├── 04_portfolio_stats.csv
├── 05_factor_contribution.csv
├── 06_summary_report.md    ← 自动生成的结论
└── charts/
    ├── 01a_cumulative_excess.png
    ├── 01b_monthly_bar.png
    ├── 01c_rolling_ir.png
    ├── 01d_drawdown.png
    ├── 02a_ic_heatmap_annual.png
    ├── 02b_ic_ir_comparison.png
    ├── 02c_ic_decay_ts.png
    ├── 03a_factor_weight_heatmap.png
    ├── 03b_weight_ts_top8.png
    ├── 04a_n_holdings.png
    ├── 04b_concentration.png
    └── 05a_factor_contribution_bar.png
```

---

## 五、分析边界说明

- **时间起点**：2021-01（观察期），2012-2020 的 IC 数据作为历史参考一并展示
- **策略对象**：仅分析 `rolling48_topn150_ew`（测试集用的主线策略）
- **不包含**：完整 Brinson 行业归因（需要历史持仓与行业数据对齐，复杂度高）；因子间交叉相关分析（留作后续）
- **行业分析**：用 `industry.parquet` + 测试期日度持仓，计算各期行业暴露变化

---

*计划确认后，执行 `analysis.py` 生成所有输出。*
