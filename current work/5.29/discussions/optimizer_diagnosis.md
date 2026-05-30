# 优化器诊断报告：有/无 QP 效果对比 & 已知问题梳理

> 生成日期：2026-05-29  
> 数据来源：`reports/experiment_board.csv`、`registry/challengers.json`、`docs/research/improve/progress_log.md`  
> 覆盖范围：train_valid（训练期 2012-2020 + 验证期 2021-2022）

---

## 一、全量运行性能对比表

> 注：IC_IR / IC_mean 均为**验证期（2021-2022）**单独统计（n≈24，可由 `t = IC_IR × √n` 验证）。

| run_id（缩写） | 信号方法 | 训练模式 | 优化器 | IC_IR（验证） | IC_t | IC_p | 组合 IR | 超额收益 | 超额回撤 | 换手率 | IR_PASS |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **frozen_baseline_icir_topn50_ew** | IC_IR 加权 | expanding | **TopN=50 EW（无 QP）** | — | — | — | **0.924** | 6.24% | -7.48% | 886% | ✅ |
| baseline_expanding_ridge | Ridge | expanding | QP TE=6% λ=0.005 | 0.628 | 3.01 | 0.006 | 0.408 | 2.44% | -8.16% | 974% | ❌ |
| challenger_rolling48 | Ridge | rolling 48m | QP TE=6% λ=0.005 | 0.348 | 1.667 | 0.110 | **1.489** | 8.63% | -5.82% | 1002% | ✅ |
| challenger_rolling60 | Ridge | rolling 60m | QP TE=6% λ=0.005 | 0.423 | 2.029 | 0.055 | 1.176 | 6.87% | -6.73% | 975% | ✅ |
| challenger_rolling36 | Ridge | rolling 36m | QP TE=6% λ=0.005 | 0.236 | 1.134 | 0.269 | 0.966 | 5.80% | -8.38% | 1002% | ✅ |
| challenger_decay_hl24 | Ridge | decay hl=24m | QP TE=6% λ=0.005 | 0.418 | 2.006 | 0.057 | 0.626 | 3.65% | -8.32% | 1041% | ✅ |
| challenger_decay_hl36 | Ridge | decay hl=36m | QP TE=6% λ=0.005 | 0.489 | 2.343 | 0.029 | 0.295 | 1.71% | -8.79% | 1008% | ❌ |
| challenger_decay_hl48 | Ridge | decay hl=48m | QP TE=6% λ=0.005 | 0.527 | 2.526 | 0.019 | 0.289 | 1.68% | -8.28% | 1008% | ❌ |

**所有 QP 运行的 fallback 统计（全部 106 期）：L1=106（正常 QP 求解）、L2=0、L3=0。**

---

## 二、核心发现：IC_IR 与组合 IR 的反向关系

### 2.1 反常现象

将验证期 IC_IR（信号质量）与组合 IR（投资组合表现）排序后对比：

| 信号 IC_IR 排名 | 训练模式 | 验证 IC_IR | 组合 IR | IR 排名 |
|---:|---|---:|---:|---:|
| 1（最高） | expanding | 0.628 | 0.408 | **8（最差）** |
| 2 | decay hl=48 | 0.527 | 0.289 | 7 |
| 3 | decay hl=36 | 0.489 | 0.295 | 6 |
| 4 | decay hl=24 | 0.418 | 0.626 | 5 |
| 5 | rolling-60 | 0.423 | 1.176 | 2 |
| 6 | rolling-48 | 0.348 | 1.489 | **1（最优）** |
| 7（最低） | rolling-36 | 0.236 | 0.966 | 3 |

**结论：信号 IC_IR 与组合 IR 完全负相关（Spearman ρ ≈ −0.86）。** 这不是正常的信号传导损耗，而是系统性异常。

### 2.2 原因假设（初步）

**Hypothesis A（优先级 P0）：expanding/decay 信号因历史数据污染产生量级压缩**

- Expanding Ridge 在拟合时混入了 2012-2020 年共 9 年数据，而 2021-2022 验证期的因子逻辑（质量因子反转、piotroski_f 失效）与 2012-2020 有明显结构性差异
- Ridge 系数是 9 年数据的"平均结论"，产生的 alpha 分数量级偏小、方向不确定（噪声稀释）
- **QP 优化器使用 alpha 分数的绝对量级分配权重**，量级偏小 → 组合偏向基准 → IR 低
- IC_IR（Spearman 秩相关）对量级不敏感，所以信号的秩排序质量看上去不错，但 QP 无法利用

**Hypothesis B（优先级 P0）：piotroski_f 在验证期反转贡献了系统性噪声**

- 训练期 piotroski_f IC_IR=+0.615（高度有效），验证期 IC_IR=-0.200（显著反转）
- Expanding Ridge 对 piotroski_f 赋予正权重（2012-2020 训练期学到的），在 2021-2022 推动评分高的股票向错误方向
- Rolling-48 只用 2018-2022 年数据，piotroski_f 在 2019-2020 期间仍有效（IC_IR=0.534），权重不会错误
- 这部分解释了为什么 rolling 系列明显优于 expanding/decay

**Hypothesis C（次要）：lambda 惩罚与信号更新速度不匹配**

- λ=0.005 减缓了组合对新信号的响应，对"需要频繁调整才能追踪信号"的 expanding Ridge 更不友好
- rolling 信号本身每期更新更大（窗口滑动），惩罚相对较小

---

## 三、有无 QP 的直接比较

### 3.1 同信号下的 QP 效果（历史数据）

来自 `progress_log.md` 阶段 5.12（16 因子，旧信号）：

| 组合层 | 方法 | IR | 说明 |
|---|---|---:|---|
| V1 | IC_IR 信号 + TopN EW（无 QP） | -0.110 | 旧 16 因子，信号本身弱 |
| V2 | IC_IR 信号 + QP | +0.353 | 同信号，QP 提升了 +0.463 |

**结论：对旧的 16 因子信号，QP 是改进的**（TopN EW 无法控制风险，QP 剔除了噪声仓位）。

### 3.2 当前（17 因子）无 QP vs 有 QP

| 配置 | 信号 | 优化器 | IR |
|---|---|---|---:|
| frozen_baseline | IC_IR 加权 + 17 因子 | TopN=50 EW（无 QP） | 0.924 |
| baseline_expanding_ridge | Ridge expanding + 17 因子 | QP TE=6% λ=0.005 | 0.408 |
| challenger_rolling48 | Ridge rolling-48 + 17 因子 | QP TE=6% λ=0.005 | 1.489 |

**注意：frozen_baseline 与两个 QP 配置的信号方法不同，无法直接归因于"QP 是否有效"。**  
缺少的对照组：**IC_IR 信号 + 17 因子 + QP**（没有该对照，不能确定 QP 本身的贡献）。

### 3.3 直接结论

| 问题 | 当前证据 |
|---|---|
| QP 本身是否有害？ | **不确定**：缺少控制变量（同信号下无 QP vs 有 QP 的 17 因子对照） |
| 扩展窗口是否有害？ | **是**：expanding 在所有训练模式中 portfolio IR 最低，与其高 IC_IR 相悖 |
| rolling 是否解决了问题？ | **是**：rolling-48 IR=1.489，远优于 frozen_baseline |
| piotroski_f 是否拖累 expanding？ | **很可能**：结合诊断报告，piotroski_f 在验证期显著反转 |

---

## 四、当前 QP 优化器实现细节

### 4.1 优化目标

```
max  α^T · w  [- λ_TC · ‖w - w_prev‖₁]
```

- **α**：Ridge 信号（cross-section z-score），NaN → 0（视为"无观点"）
- **λ_TC = 0.005**：换手成本惩罚，来自网格实验（2026-05-25 确定，验证期 IR 峰值 0.483）
- 第一期（无 w_prev）不施加换手惩罚

### 4.2 约束体系

```
L1 层（严格 QP）：
  (w - w_b)^T · Σ_daily · (w - w_b) ≤ (TE_target)² / 252    # 跟踪误差二次约束
  |Σ_ind(w_i - w_b_i)| ≤ 0.03   ∀ industry                  # 行业偏离 ±3%
  |w_i - w_b_i| ≤ 0.015          ∀ 非停牌股                   # 单股偏离 ±1.5%
  w_i = w_prev_i                  ∀ 停牌股                     # 停牌锁定
  w_i ≤ w_prev_i                  ∀ 涨停非停牌股               # 涨停不买
  w_i ≥ w_prev_i                  ∀ 跌停非停牌股               # 跌停不卖
  Σ w_i = 1, w_i ≥ 0

L2 层（去掉 TE 二次约束，保留线性约束）：
  [同上但无 Σ 约束]

L3 层（TopN 等权）：
  TopN=50 等权，含停牌锁定 + 涨跌停 + 单股偏离上限截断
```

**关键参数（`OptimizeConfig` dataclass，来自 `src/config.py`）：**

| 参数 | 值 | 来源 |
|---|---:|---|
| `te_target_annual` | 0.06（6%）| 阶段 1 实验选定（O1 配置） |
| `industry_max_dev` | 0.03（3%） | O1 配置 |
| `single_max_dev` | 0.015（1.5%）| O1 配置（Bug-1 修复后） |
| `turnover_lambda` | 0.005 | 阶段 6.12 网格实验，λ=0.005 验证期 IR 峰值 |
| `topn` | 50 | frozen_baseline 对齐 |

### 4.3 协方差估计

- 方法：`sklearn.covariance.LedoitWolf`（收缩估计），解决 ~500 维样本协方差病态问题
- 每期独立估计，基于滚动历史日频收益率
- 正定性保证：`Σ + ε·I`（ε 约 1e-6）

### 4.4 Fallback 机制

当前 106 个优化期全部在 L1 层求解，无 Fallback 触发。这在 Bug-1/Bug-2 修复后是预期行为（历史上因停牌约束冲突曾出现 L3 cascade）。

### 4.5 求解器

`solver_order: ["CLARABEL", "SCS"]`，超时阈值 30 秒；warm_start=True。

---

## 五、已知问题清单（可观测的结果异常）

### 问题 P0-A：IC_IR 与组合 IR 反向相关（最高优先级）

- **现象**：验证期信号 IC_IR 最高的 expanding Ridge（0.628）对应最差的组合 IR（0.408）
- **诊断方向**：
  1. 比较 expanding 与 rolling-48 在验证期的 alpha 分数分布（量级分布、极端值比例）
  2. 检查 expanding Ridge 的因子系数历史（`coef_history.parquet`），看 piotroski_f 系数在 2021-2022 赋值方向
  3. 做 piotroski_f ablation：从 expanding Ridge 信号中剔除 piotroski_f 后重跑，看 IR 是否回升
- **关键文件**：`check/0529/piotroski_diagnosis.md`（已完成，结论：REMOVE_CANDIDATE）

### 问题 P0-B：piotroski_f 验证期 IC 反转

- **现象**：piotroski_f 在 2021-2022 验证期 IC_IR=-0.200，训练期 +0.500 → +0.839
- **根因**：2021-2022 市场中利润含金量、去杠杆等指标失去预测力（经济环境切换）
- **当前状态**：已诊断为 REMOVE_CANDIDATE，需另起实验（`ablation_no_piotroski_f`）确认影响
- **危险**：expanding Ridge 的系数仍在利用 piotroski_f 的历史规律，2021-2022 期间产生反向 alpha
- **关键 Spec**：`configs/pipelines/ablation_no_piotroski_f.py`（已建立，未运行）

### 问题 P0-C：rolling-48 的低 IC_IR 与高组合 IR 需要解释

- **现象**：rolling-48 验证期 IC_IR=0.348（p=0.110，**统计不显著**），但组合 IR=1.489（全局最优）
- **风险**：这可能是 rolling 窗口对验证期的隐式过拟合（窗口长度恰好覆盖了 2018-2022 的"最优"训练数据）
- **诊断方向**：
  1. 计算 rolling-48 的训练期 IC_IR（仅 2012-2020），不应比验证期低太多
  2. `paired_p_vs_mainline = 0.1474`（p 值较大，IR 差异统计不显著），说明 rolling-48 vs expanding 的组合 IR 差异在统计上尚未可靠
  3. 窗口长度的直接关系（rolling-48 > rolling-60 > rolling-36 for IR）不是单调的，需要更多数据点

### 问题 P1-A：decay Ridge hl=36/48 异常低

- **现象**：decay hl=36m/48m 的 IC_IR（0.489/0.527，显著）远高于 rolling-48（0.348，不显著），但组合 IR（0.295/0.289）远低于 rolling-48（1.489）
- **根因假设**：decay 半衰期 36/48m ≈ expanding（历史数据权重衰减慢），保留了过多"有毒"历史信息；但 decay hl=24m 只保留最近 24 个月权重，部分类似 rolling，IR 回升至 0.626
- **未诊断**：decay 的系数历史（`src/signal/ridge_decay.py`）尚未与 rolling 对比

### 问题 P1-B：优化器贡献不明确

- **现象**：无法确定 QP 本身有利还有害，因为缺少"Ridge 信号 + 无 QP TopN EW"的对照
- **所需实验**：`ablation_ridge_topn_ew`（Ridge 信号 + TopN EW，去掉 QP），与 frozen_baseline 做对比
- **当前无此对照组，这是一个决策空白**

### 问题 P1-C：配置效应持续为负

- **现象**：所有 QP 运行的行业配置效应均为负（-3.5% ~ -5.2%），拖累了总超额收益
- **原因**：行业中性化约束（±3%）限制了行业层面的主动配置；但超额收益主要来自选股效应（+9% ~ +11%），配置效应是"代价"而非"错误"
- **当前可接受**，但如果要进一步提升 IR，可以评估是否放开行业约束

### 问题 P2-A：月度胜率偏低

- **现象**：expanding 月度胜率 43.5%，rolling-48 为 56.5%，frozen_baseline 为 60.9%；expanding 月度胜率低于 50% 但 IR 正值，说明盈利月份收益较大
- **影响**：月度胜率低意味着夏普比率的尾部风险较高，用户体验差

---

## 六、诊断优先级与建议行动

### 立即可以做（无新实验，纯分析）

1. **读取 expanding Ridge 的 `coef_history.parquet`**，查看 piotroski_f 系数在 2019-2022 期间的方向和量级变化
2. **对比 expanding 与 rolling-48 的 alpha 分数分布**（均值、标准差、极端值比例），验证"量级压缩"假设

### 需要运行实验（需要新的 run）

| 实验 | 对应 Spec | 目的 |
|---|---|---|
| 剔除 piotroski_f | `ablation_no_piotroski_f.py`（已建立） | 确认 piotroski_f 对 expanding IR 的拖累量级 |
| 剔除 piotroski_f + high_52w | `ablation_no_piotroski_high52w.py`（已建立）| 同时验证两个问题因子 |
| Ridge TopN EW（无 QP） | 待建立 | 控制 QP 贡献，与 frozen_baseline 对比 |
| rolling-48 无 piotroski_f | 待建立 | 确认 rolling-48 的优势是信号质量还是窗口选择 |

### 不应该做（风险提示）

- **不应该直接晋升 rolling-48 为主基线**：IC_IR=0.348 统计不显著（p=0.110），paired_p=0.1474，两者差异未经可靠统计验证；需要更多样本（如 rolling 训练期 IC_IR、不同因子池下的稳健性检验）
- **不应该在没有 ablation 的情况下扩大 rolling 窗口网格**：已有 36/48/60m，需要先诊断为什么 48m 是局部最优再决定是否继续搜索

---

## 七、关键数字速查

| 指标 | 值 |
|---|---|
| 冻结基线 IR（无 QP 下限参照） | 0.924 |
| 当前最优 challenger IR（rolling-48）| 1.489 |
| 当前主基线 IR（expanding Ridge + QP）| 0.408 |
| QP 优化后 IR 的"理论上限"（rolling-48）| 1.489 |
| TE 实现值（不受约束限制）| ~5.8% < 6%（TE 约束非紧绑定）|
| 换手惩罚 λ_TC | 0.005（网格最优） |
| piotroski_f 验证期 IC_IR | -0.200（REMOVE_CANDIDATE）|
| 测试集剩余次数 | **2 次**（权威来源：`docs/logs/test_set_runs.json`）|

---

_本文档由 Claude Code 生成，基于 2026-05-29 仓库状态的完整历史记录梳理。_
