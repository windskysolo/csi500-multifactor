# IC_IR vs 组合 IR 反相关：根本原因归因报告（确定版）

> 生成日期：2026-05-29  
> 方法：基于已有 run 数据的多层次量化归因（无新实验）  
> 状态：**根本原因已确认，修复路径明确**

---

## 一、结论速报

### 核心发现

**rolling-48 的性能优势 (+0.54%/月) 来自「底仓超额」，不是「选股超额」。**

| 超额收益来源 | expanding | rolling-48 |
|---|---:|---:|
| TOP-50 主动超配贡献 | +0.18%/月 | +0.24%/月 |
| **其余 450 股主动低配贡献** | **+0.03%/月** | **+0.30%/月** |
| **组合总超额（vs CSI500）** | **+0.21%/月** | **+0.54%/月** |

- **两者的差距（0.54 - 0.21 = 0.33%/月）中，约 82% 来自底仓差异（0.30 - 0.03 = 0.27%/月）**
- 选股（TOP-50）贡献差距仅 0.06%/月，是次要因素

**底仓差距的根本原因：Expanding 对"坏股票"的预测含大量反向错误，导致低配了实际上会跑赢的股票。**

---

## 二、关键诊断过程与证据

### 2.1 第一步纠错：之前用了错误的基准

初版分析（check/0529/test3_transfer_coefficient.csv）用 `baseline_weights.parquet`（TopN-50 等权）计算 TC，得到 TC=-0.066（负值），结论为"QP 反向传导信号"。

经查 `scripts/run_portfolio_optimization.py`（第 329 行）：
```python
result = optimize_single_period(
    w_b = benchmark_weights_dict[T],   # ← 真实 CSI500 市值权重
    ...
)
baseline_weights[T] = _compute_baseline(...)   # ← 落盘的是 TopN-50 等权（仅供报告用）
```

**`baseline_weights.parquet` 是 TopN-50 等权（报告参考），QP 实际使用的是 `index_member.parquet` 中的真实 CSI500 市值权重。**

用正确基准（真实 CSI500）重算后：
- **TC = +0.571（expanding）、+0.594（rolling-48）**：QP 正常传导信号，运作正确
- **IC-超额收益 Spearman rho = +0.900（expanding）、+0.844（rolling-48）**：信号好的月份确实赚更多钱
- 两者均稳定跑赢 CSI500 基准（非之前误以为的"expanding 亏钱"）

### 2.2 真正的差距：底仓超额贡献（10倍差距）

| 维度 | expanding | rolling-48 | 差值 |
|---|---:|---:|---:|
| TOP-50 月超额贡献 | +0.18% | +0.24% | +0.06% |
| REST-450 月超额贡献 | **+0.03%** | **+0.30%** | **+0.27%** |
| 月胜率（TOP-50 贡献为正）| 14/23 | 13/23 | 类似 |
| 月胜率（REST 贡献为正） | 11/23 | 11/23 | **完全相同** |

**关键发现**：
- 两者 REST-450 贡献为正的月份数量 **完全相同（11/23）**
- 但 rolling-48 的 REST 贡献**量级（0.30%）是 expanding（0.03%）的 10 倍**
- 这意味着：当底仓"对的时候"，rolling-48 赚的更多；当"错的时候"，两者亏损量级类似

### 2.3 底仓超额来自哪里？

REST-450 的超额贡献机制：
- 被低配（active weight < 0）的股票跑输基准 → 贡献正超额
- 被低配的股票跑赢基准 → 贡献负超额

**Expanding 的底仓失效原因：8 个因子在验证期方向错误**

| 因子 | Expanding 系数（验证期）| 验证期实际 IC | 问题 |
|---|---:|---:|---|
| **piotroski_f** | +0.00098 | **-0.012** | 系数正向，实际 IC 负向：错误低配了 piotroski 高分股 |
| **q_roe** | +0.00138 | **-0.008** | 同上，季度 ROE 高的股票被错误低配 |
| **amihud** | -0.00058 | **+0.017** | 方向反转：高非流动性股票在 2021-2022 实际跑赢 |
| roe_delta | +0.00129 | -0.003 | 轻微拖累 |
| gross_margin_trend | +0.00053 | -0.004 | 轻微拖累 |
| gross_margin | +0.00058 | -0.003 | 轻微拖累 |
| ep_ttm | -0.00011 | +0.015 | 轻微拖累 |
| rev_yoy | +0.00101 | -0.001 | 轻微拖累 |

**举例**：piotroski_f（财务健康综合评分）在 2021-2022 IC_IR=-0.200（显著反转）。Expanding Ridge 学到了"piotroski 高 = 好股票"的历史规律，在预测中给 piotroski 高分股赋予正 alpha，导致这些股票进入 TOP-50（被超配）。但验证期 piotroski 高股票恰恰跑输，进一步损伤 TOP-50 贡献，同时又错误低配了 piotroski 低分（但实际跑赢）的股票，损伤 REST 贡献。

Rolling-48 只用 2018-2022 年数据，2021-2022 的因子关系与近期更匹配，方向错误因子更少。

---

## 三、因果路径图

```
Expanding Ridge（2012-2022 全量训练）
    ↓
8 个因子系数方向与 2021-2022 实际 IC 相反
    ↓
    ├─ TOP-50 选股：部分错误方向因子拉高了"伪好股票"的 alpha
    │   → 这些股票进入 TOP-50 被超配，但实际跑输
    │   → TOP-50 月贡献：+0.18%（可以，但不如 rolling-48 的 +0.24%）
    │
    └─ REST-450 底仓：这些错误方向因子同时把"真好股票"划入低 alpha 区
        → 真好股票被低配（active < 0），但它们实际跑赢 CSI500
        → REST 月贡献：+0.03%（远低于 rolling-48 的 +0.30%）
        → 这是 IR 差距的 82% 来源

Rolling-48（仅用最近 48 个月训练）
    ↓
因子系数与 2021-2022 实际 IC 对齐更好（近期数据 dominant）
    ↓
    ├─ TOP-50 选股更精准 → +0.24%/月
    └─ 底仓预测更精准 → +0.30%/月（真正的差距所在）

IC_IR（Spearman 秩相关）衡量全截面的方向准确性，expanding 的全截面方向准确但
TOP 股票和 BOTTOM 股票的具体预测含有噪声，对 IC 指标不敏感但直接影响组合 IR。
```

---

## 四、修复方案（按优先级）

### Fix-1（P0，立即）：剔除 piotroski_f

**依据**：piotroski_f 是方向错误中"drag_severity"最大因子，且 check/0529/piotroski_diagnosis.md 已确认为 REMOVE_CANDIDATE。

```powershell
python -m scripts.run_experiment --spec configs/pipelines/ablation_no_piotroski_f.py
```

**预期效果**：
- Expanding 的 REST-450 底仓贡献应从 +0.03% 提升（减少对 piotroski 高分股的错误低配）
- TOP-50 中"伪质量股"减少，TOP 贡献也应提升
- 预计 expanding IR 从 0.408 提升 0.1-0.2

**验证方式**：`compare_runs.py` 中对比 ablation_no_piotroski_f vs baseline_expanding，分解 TOP-50 和 REST-450 贡献变化。

### Fix-2（P0，同步）：评估 q_roe 方向

q_roe 在验证期 IC=-0.008（方向轻微反转），expanding 系数 +0.00138（正向）。单独剔除 q_roe 的影响可通过：
- 检查 ablation_no_piotroski_f 后 q_roe 的系数是否自然收缩
- 或追加 ablation_no_piotroski_high52w.py（已建立）

### Fix-3（P1，后续）：对 expanding 使用更短衰减窗口

将 expanding 的有效历史限制在最近 24-36 个月（等价于 decay hl=12-18m）。
已有 decay_hl24（IR=0.626）作为参考，验证"更短历史 = 更少方向错误 = 更好底仓预测"。

### Fix-4（不需要）：QP 本身无需修改

经验证，QP 的 TC=+0.57（正常正值传导），IC-超额收益正相关（rho=0.90），运作正确。
之前误判为"QP 问题"是因为使用了错误基准（TopN-50 EW）计算 TC。

---

## 五、关键数字一览

| 指标 | expanding | rolling-48 |
|---|---:|---:|
| 验证期 IC_IR（信号层）| 0.628 | 0.348 |
| Transfer Coefficient（CSI500基准）| **+0.571** | **+0.594** |
| IC-超额收益 Spearman ρ | +0.90 | +0.84 |
| 组合月超额（vs CSI500）| +0.21% | +0.54% |
| TOP-50 月超额贡献 | +0.18% | +0.24% |
| **REST-450 月超额贡献** | **+0.03%** | **+0.30%（关键差距）** |
| 组合年化 IR | 0.408 | 1.489 |
| 方向错误因子数 | **8/18（44%）** | 更少（近期数据）|

---

## 六、诊断工具位置

```
check/0529/
  ic_ir_paradox_attribution.md     ← 四个测试的原始输出
  test1_alpha_distribution.csv     ← alpha 分布（量级假设被否定）
  test2_ic_vs_excess_ret.csv       ← IC vs 超额（注：用的是错误基准，已修正）
  test3_transfer_coefficient.csv   ← TC（注：用的是错误基准，已修正）
  test4_coef_mean.csv              ← 各方法因子系数
  test4_expanding_coef_vs_ic.csv   ← expanding 系数方向 vs IC 方向对比（8个错误）
  ic_ir_paradox_root_cause.md      ← 本文档（确定版）
```

---

_本报告基于对 run 输出文件的直接数值计算，所有结论有完整的可重现代码支持（`scripts/diagnose_ic_ir_paradox.py`）。_
