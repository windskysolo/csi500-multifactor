# 组合优化模块深度诊断与业界做法参考

> 生成时间：2026-05-23  
> 依据代码：`src/portfolio/optimizer.py`、`src/portfolio/covariance.py`、`src/config.py`  
> 当前结果：V2 IR=0.017，年化超额 +0.10%，跟踪误差 5.82%

---

## 一、你当前在做什么

### 1.1 优化框架概述

你用的是**均值-方差主动组合优化**，属于 Grinold & Kahn《主动投资组合管理》第 8 章的经典框架，求解一个带约束的二次规划（QP）：

```
max  α^T · w
s.t. (w - w_b)^T · Σ_daily · (w - w_b) ≤ TE_target² / 252    [跟踪误差约束，二次]
     |Σ_行业(w_i - w_b_i)| ≤ 2%   ∀ 行业                        [行业偏离，线性]
     |w_i - w_b_i|             ≤ 1%   ∀ 股票                     [单股偏离，线性]
     w_i ≥ 0                                                      [纯多头]
     Σ w_i = 1                                                    [全仓]
```

**求解工具**：cvxpy（Python 凸优化建模库），依次尝试 CLARABEL → SCS 求解器。

**协方差估计**：sklearn 的 LedoitWolf 收缩估计，252 个交易日滚动窗口，输出日频协方差矩阵。

**退化路线（Fallback）**：
- L1：完整 QP（含 TE 二次约束）
- L2：去掉 TE 约束，保留全部线性约束，求解线性规划
- L3：TopN 等权兜底（取 alpha 最高的前 N 只等权）

### 1.2 这个框架本身是否正确

**框架是对的**——这正是国内外量化增强基金的主流优化骨架。核心思路（最大化信号 × 约束主动风险）没有领域错误。问题在实施细节上，下面逐条展开。

---

## 二、发现的问题（按严重程度排序）

---

### 问题 1（严重）：TE 约束被单股偏离约束架空，二次规划形同虚设

**这是最核心的设计缺陷。**

#### 数学推导

中证 500 约 500 只股票，近似等权，每只基准权重约 0.2%。单股偏离上限 ±1%，则主动偏离向量 d = w - w_b 的每个分量满足：

```
|d_i| ≤ 1%
```

在零相关假设（上界估计）下，组合最大年化跟踪误差为：

```
TE_max = sqrt( Σ_i d_i² × σ_i² × 252 )
       ≤ sqrt( N × (1%)² × σ̄² × 252 )
       = sqrt( 500 × 0.0001 × 0.0003 × 252 )
       ≈ 3.5% 年化
```

其中 σ̄² ≈ 0.0003（A 股日收益率方差典型值，约对应年化波动率 27%）。

考虑到股票间正相关（A 股平均相关系数约 0.3），实际可达到的 TE 上限更低，约 2.5-3%。

**你的 TE 目标是 5%，但物理上单股偏离约束只允许 TE 最高约 3%。**

结论：TE 约束（二次约束）在大多数期根本无法触及，真正约束组合的是单股偏离（线性约束）。这意味着：

1. 协方差矩阵 Σ 的估计质量对结果影响极小
2. 求解 QP 的成本（LedoitWolf + CLARABEL）大部分是浪费的
3. 实际等价于一个纯线性规划（L2），但你却付出了 L1 的计算代价和工程复杂度

#### 如何验证

查看优化元数据中 L1 vs L2 的比例，以及 L1 成功时 TE 约束是否 binding：

```python
# 在 run_portfolio_optimization 完成后
meta = pd.read_parquet("data/processed/optimize_metadata.parquet")
print(meta["fallback_level"].value_counts())
# 如果 L2 占比很高（>20%），说明 TE 约束经常不可行
# 如果 L1 为主，但 IR 仍很低，说明 TE 约束不 binding（松弛）
```

#### 如何修复

要让 TE 约束真正 binding，需要满足：TE 目标 < 单股偏离约束能达到的最大 TE。调整方向：

```python
# 当前（config.py）
OPT_SINGLE_MAX_DEV: float = 0.01   # 1%
OPT_TE_TARGET_ANNUAL: float = 0.05  # 5%

# 建议方向 A（放宽线性约束）
OPT_SINGLE_MAX_DEV: float = 0.02   # 2%  → 允许更大单股偏离
OPT_INDUSTRY_MAX_DEV: float = 0.04 # 4%  → 允许行业观点
OPT_TE_TARGET_ANNUAL: float = 0.08  # 8%  → 仍然保守，但线性约束不再架空 TE

# 建议方向 B（收紧 TE 目标，与单股约束匹配）
OPT_SINGLE_MAX_DEV: float = 0.01   # 保持 1%
OPT_TE_TARGET_ANNUAL: float = 0.02  # 降到 2%，TE 约束才有可能 binding
```

方向 A 更合理——指数增强策略本来就需要给信号更多空间。

---

### 问题 2（严重）：alpha 是 z-score，TE 约束是收益单位，量纲不匹配

#### 问题描述

目标函数：

```
max  α^T · w
```

这里 α 是经过 MAD 去极值 + z-score 标准化的合成信号，值域约 [-3, +3]，**无任何收益单位**，是纯粹的排序信号。

TE 约束：

```
(w - w_b)^T · Σ · (w - w_b) ≤ TE² / 252
```

这里 Σ 是**日频收益率**的协方差矩阵，TE 是**年化超额收益率的标准差**，有收益单位（%/年）。

**两个量没有共同的量纲**，优化器看到的是一个无量纲目标函数在一个有量纲约束集内最大化，结果的解释取决于 α 的实际预测能力（IC），而这在 z-score 归一化后被抹掉了。

#### 为什么这会导致问题

假设某期两只股票 A（alpha=+2.5）和 B（alpha=+0.1），优化器会把 A 推到单股偏离上限。但 alpha=2.5 是否真的对应更高预期收益，取决于信号的 IC 均值和这只股票的波动率。若 IC=0.03、σ_A=30%，则预期月超额收益为 `0.03 × 0.3/sqrt(12) ≈ 0.26%`——是个很小的数字。优化器并不知道这个转换关系，只看到 2.5 > 0.1，就决定给 A 最大权重。

**结果：组合配置的依据是排序 rank，不是预期收益量级，TE 约束无法根据真实信号强弱动态调整主动头寸。**

#### 业界标准做法

将 alpha z-score 转换为预期月超额收益估计：

```
α_ret_i = IC_mean × σ_i × z_i

其中：
  IC_mean ≈ 滚动历史 IC 均值（约 0.03-0.05）
  σ_i     = 个股月化波动率（从协方差矩阵对角元素提取）
  z_i     = 当期 z-score
```

这样目标函数 α_ret^T · w 和 TE 约束都有收益单位，优化器的权衡有经济含义。

或者，更常见的方式是改用**风险厌恶系数形式**（无需量纲匹配）：

```
max  α^T · w  -  λ × (w - w_b)^T · Σ · (w - w_b)
```

通过调整 λ 来控制主动风险水平，λ 越大越保守。等价于对每个 λ 都有一个隐含的 TE 目标，但优化器不需要量纲匹配。

---

### 问题 3（中等）：没有显式换手成本惩罚，导致换手过高

#### 现状

当前优化目标：`max  α^T · w`

没有惩罚换手，每期优化器会将权重推到约束允许的极端位置（单股最大偏离）。

**实际换手**：745% 年化双边，成本估算约：

```
745% × (2.5 + 2.5 + 8) bps × 2（双边）≈ 1.1% 年化
```

当年化超额仅 +0.10% 时，成本本身已经是超额的 11 倍。

#### 业界标准做法

在目标函数中显式加入换手惩罚（TC-aware optimization）：

```
max  α^T · w  -  λ_TC × ||w - w_prev||_1
```

其中 λ_TC 是换手惩罚系数，单位是"每单位权重变动的成本"（如 0.0015 = 15 bps）。

这样优化器会自动权衡"追逐新信号带来的 alpha 提升"vs"实现这个提升需要付出的交易成本"，找到净收益最大的权重。

L1 范数（绝对值和）使问题保持凸性，可以和原 QP 一起求解：

```python
# 在 optimizer.py 中，L1 问题定义里加入换手惩罚项
turnover_cost = cfg.OPT_TURNOVER_LAMBDA * cp.norm1(w_l1 - w_prev_vec)
l1_problem = cp.Problem(
    cp.Maximize(alpha_vec @ w_l1 - turnover_cost),
    l1_constraints
)
```

λ_TC 的校准方式：从历史回测中找到让换手率落在目标区间（如月单边 20-30%）的 λ_TC 值。

---

### 问题 4（中等）：LedoitWolf 与业界主流因子风险模型的差距

#### LedoitWolf 的局限

LedoitWolf 是将样本协方差矩阵收缩到**缩放单位矩阵**方向（假设所有股票方差相同、协方差为零），这在 p/n 接近 2（500 只股票、252 天样本）的情况下是有效的正则化，但有以下局限：

| 问题 | 具体表现 |
|------|---------|
| 收缩目标太简单 | 假设所有股票方差相同，忽视了大市值 vs 小市值波动率的系统差异 |
| 无法区分系统风险与特异风险 | 无法告诉你 TE 里有多少来自行业暴露、多少来自风格暴露 |
| 历史样本不稳定 | 252 天窗口里包含了大量市场制度转换（牛市/熊市/震荡市），协方差矩阵估计不稳定 |
| 计算成本高 | 500×500 矩阵每期特征值分解，约 O(N³) |

#### 业界主流：结构化因子风险模型

以 BARRA CNE6（A 股市场标准）为代表，将协方差矩阵分解为：

```
Σ = B · F · B^T + D

B (N×K)：因子暴露矩阵（市场、31 个申万行业、10 个风格因子 = 约 42 个因子）
F (K×K)：因子协方差矩阵（K≈42，小矩阵，估计稳定）
D (N×N)：对角矩阵，每只股票的特异方差
```

优点：
1. **可解释**：可以计算"行业配置贡献 TE 多少 bps、风格暴露贡献多少 bps"
2. **稳定**：K×K 小矩阵估计远比 N×N 稳定
3. **高效**：利用 Woodbury 公式可以在 O(K²N) 而非 O(N³) 内求解优化
4. **更准确**：可以对不同类型风险分别估计（行业用行业收益率协方差，风格用横截面回归）

#### 你的场景下的简化替代方案

完整 BARRA 模型工程量很大，你可以用**简化行业因子模型**：

```python
# 行业哑变量 + 市场因子作为结构
# Σ ≈ β_market × Var(market) × β_market^T + D_industry + ε·I
# 用申万行业虚拟变量估计行业方差，剩余为特异方差
```

这比 LedoitWolf 对行业集中度的估计更准确，同时工程量可控。

---

### 问题 5（中等）：NaN alpha 填充为 0 导致无信号股票摊薄有效 alpha

#### 问题描述

```python
# src/portfolio/optimizer.py 第 450 行
alpha_vec = alpha.reindex(codes).fillna(0.0).values.astype(float)
```

对于当期没有 alpha 信号的股票（因子缺失或数据不足），赋值 alpha=0。

在 z-score 体系中，alpha=0 意味着"与宇宙均值完全持平"，不是"无观点"。优化器会：

1. 对 alpha=0 的股票，给予接近基准权重的配置（不超配也不低配）
2. 如果这些股票有 100 只（约 20%），它们会占据 20% 的权重预算，这 20% 贡献零额外 alpha
3. 有效 alpha 被稀释了 20%

#### 业界标准做法

两种方案：

**方案 A（推荐）：排除无信号股票，将权重预算只分配给有信号的股票**

```python
# 无信号股票不参与优化，锁定在基准权重
# 问题：会改变 Σ 的维度，需要相应调整协方差矩阵
valid_mask = ~np.isnan(alpha_vec_raw)
# 对 valid_mask=False 的股票：w_i = w_b_i（锁定），从优化变量中移除
```

**方案 B：改用 0 作为"无信号"标记，但在目标函数里区分**

将目标函数改为只对有信号股票求和：

```python
objective = (alpha_vec * valid_mask_float) @ w_l1
# 对无信号股票，目标函数梯度为 0，优化器自然不会主动配置
```

这比直接 fillna(0) 更干净，虽然数学等价，但语义上更明确。

---

### 问题 6（轻微）：w_prev 使用目标权重，不是实际持仓

#### 问题描述

```python
# src/portfolio/optimizer.py 第 683 行
"w_prev_source": "target_weight",  # F6-004 标记：已知问题
```

每期优化结束后，把目标权重（优化器输出的理论权重）传给下一期作为 w_prev，用于涨跌停约束。

**实际情况**：
- T-1 期可能触发了 L3 fallback（TopN 等权），实际持仓与目标权重可能差异达到 1-3%
- T-1 期有涨跌停股票实际未成交，实际持仓偏离目标

**后果**：
- 本期涨停约束（`w_i ≤ w_prev_i`）基于错误的 w_prev，可能错误地允许或禁止某些操作
- 换手计算（`|w_now - w_prev|`）偏低（目标→目标比实际→目标的差更小）
- IR 计算准确，但换手率可能被低估约 10-20%

#### 修复方向

在回测引擎（`backtest/engine.py`）执行 T+1 成交后，将实际持仓（而非目标权重）传回给下期优化：

```python
# 伪代码
actual_holdings_t = backtest_engine.execute_and_get_holdings(target_weights_t)
w_prev_for_optimizer = actual_holdings_t  # 而非 target_weights_t
```

---

## 三、与业界做法的系统对比

### 3.1 目标函数对比

| 维度 | 你的实现 | 业界主流（国内量化增强） |
|------|---------|----------------------|
| 形式 | `max α^T·w`（纯 alpha 最大化） | `max α_ret^T·w - λ·TE²` 或 `max α^T·w - λ_TC·||Δw||_1` |
| alpha 单位 | z-score（无量纲） | 预期超额收益（bp/月）= IC × σ × z |
| 换手惩罚 | 无 | 显式 λ_TC 或换手上限约束 |
| 风险惩罚 | TE 硬约束 | 风险厌恶系数 λ（软约束）或 TE 硬约束 |

### 3.2 风险模型对比

| 维度 | 你的实现 | 业界主流 |
|------|---------|---------|
| 方法 | LedoitWolf 收缩（全矩阵） | 结构化因子风险模型（BARRA 风格） |
| 更新频率 | 每期月频（252 天回看） | 因子暴露日更新，因子协方差月更新 |
| 计算规模 | 500×500，O(N³) | K×K（K≈42），O(K²N) |
| 可解释性 | 低（黑盒） | 高（行业/风格/特异贡献可分解） |
| 样本稳定性 | 中（252 天刚好够） | 高（因子历史远比个股稳定） |

### 3.3 约束体系对比

| 约束 | 你的实现 | 业界参考值（国内量化增强） |
|------|---------|--------------------------|
| TE 目标 | 5% | 5-10%（宽松端） |
| 单股偏离 | ±1% | ±1.5-3%，或基准权重的 2-5 倍 |
| 行业偏离 | ±2% | ±3-5% |
| 换手约束 | 无 | 月单边换手 ≤ 20-30% |
| 净多头约束 | w_i ≥ 0 | 同，纯多头增强 |
| Beta 约束 | 无 | 通常不加（指数成分股 Beta 接近 1） |

### 3.4 整体流程对比

```
业界典型量化增强流程（以国内主流为例）：

因子打分（20-40 个因子）
   ↓
IC_IR 加权合成 alpha（与你相同）
   ↓
alpha → 预期月超额收益（乘以 IC_mean × σ）← 你缺少这一步
   ↓
因子风险模型估计 Σ（BARRA 风格）← 你用 LedoitWolf
   ↓
QP 优化（含换手惩罚）← 你缺少换手惩罚
   ↓
约束：TE ≤ 8%，单股 ±2%，行业 ±4%，换手 ≤ 25% 月单边 ← 你的约束偏紧
   ↓
T+1 开盘执行，实际持仓反馈回下期优化 ← 你用目标权重替代实际持仓
```

---

## 四、修复优先级与操作步骤

### P0（立即做，收益最大）

#### 放宽约束参数

修改 `src/config.py`：

```python
# 当前值 → 修改后
OPT_TE_TARGET_ANNUAL: float = 0.05   →  0.08   # 5% → 8%
OPT_SINGLE_MAX_DEV:   float = 0.01   →  0.02   # 1% → 2%
OPT_INDUSTRY_MAX_DEV: float = 0.02   →  0.04   # 2% → 4%
```

**预期效果**：TE 约束开始 binding，优化器有更多自由度追逐 alpha，IR 预计从 0.017 提升到 0.1-0.3。

### P1（本轮迭代完成后做）

#### 加入换手惩罚

在 `src/portfolio/optimizer.py` 的 `OptimizeConfig` 中加入：

```python
turnover_lambda: float = cfg.OPT_TURNOVER_LAMBDA  # 默认约 0.001
```

在 L1 问题的目标函数中加入：

```python
if w_prev_vec is not None and config.turnover_lambda > 0:
    turnover_penalty = config.turnover_lambda * cp.norm1(w_l1 - w_prev_vec)
    l1_objective = cp.Maximize(alpha_vec @ w_l1 - turnover_penalty)
else:
    l1_objective = cp.Maximize(alpha_vec @ w_l1)
l1_problem = cp.Problem(l1_objective, l1_constraints)
```

同时在 `src/config.py` 中加入：

```python
OPT_TURNOVER_LAMBDA: float = 0.001  # 初始值，通过 grid search 校准到目标换手
```

**校准方法**：对训练集用 [0.0005, 0.001, 0.002, 0.004] 做网格搜索，找到让月单边换手落在 20-30% 的 λ_TC 值。

### P2（中长期优化）

#### 将 alpha z-score 转换为预期超额收益

在 `src/signal/combiner.py` 输出 composite alpha 后，在 `run_portfolio_optimization` 中加一步转换：

```python
# 预期月超额收益估计
# ic_mean：当期滚动 IC 均值（来自 compute_rolling_ic_ir 的均值部分）
# vol_monthly：个股月化波动率（从协方差矩阵对角元素 sqrt(diag(Σ) × 21)）
alpha_pred = ic_mean_composite * vol_monthly * alpha_zscore
```

这使目标函数和 TE 约束量纲统一，优化器的配置决策有明确的经济含义。

---

## 五、不建议现在做的事

| 操作 | 原因 |
|------|------|
| 替换为 BARRA 风格因子模型 | 工程量很大（需实现因子暴露估计、因子协方差、特异方差），当前阶段性价比低 |
| 引入 Long-Short 或杠杆 | 指数增强框架不适合，偏离项目定位 |
| 更换优化求解器（如 Gurobi） | CLARABEL + SCS 对当前问题规模足够，没有瓶颈 |
| 加入 Black-Litterman 观点调整 | 增加了参数，当前信号质量是瓶颈，不是观点混合 |
| 删除 LedoitWolf，改用样本协方差 | 反向操作，LedoitWolf 比样本协方差稳定，应该保留 |

---

## 六、当前问题对 IR 的量化影响估算

| 问题 | 对 IR 的影响方向 | 粗略影响量级 |
|------|----------------|------------|
| TE 约束被架空（约束过紧） | 信号无法转化为权重偏离，直接压低主动收益 | −0.10 到 −0.15 IR |
| alpha 量纲不匹配 | 优化器配置行为不稳定，噪声偏大 | −0.05 IR |
| 无换手惩罚 → 换手成本 1.1% | 直接侵蚀超额收益 | −0.10 IR（从超额角度） |
| LedoitWolf vs 因子模型 | 风险估计偏差，约束可能过松或过紧 | ±0.03 IR |
| NaN alpha 填 0 | 稀释有效 alpha 约 15-20% | −0.03 IR |
| w_prev 用目标权重 | 换手计算轻微偏差，约束轻微违反 | <−0.01 IR |

**总计**：修复 P0（约束参数）+ P1（换手惩罚）预计可以将 IR 从 0.017 提升到 0.15-0.35，再结合因子筛选的改进，有望接近 0.4-0.5 的目标。

---

## 七、参考文献

1. Grinold, R. & Kahn, R. (2000). *Active Portfolio Management* (2nd ed.). McGraw-Hill. Ch. 8: Portfolio Construction.
2. Ledoit, O. & Wolf, M. (2004). A well-conditioned estimator for large-dimensional covariance matrices. *Journal of Multivariate Analysis*.
3. Barra (MSCI). CNE6 Risk Model Handbook. — A 股标准结构化风险模型文档（需 MSCI 订阅）。
4. Grinold, R. (1994). Alpha is voltage. *Journal of Portfolio Management*. — alpha 单位转换的经典文章。
5. 国泰君安证券研究 (2019). 中证500指数增强策略研究 — 国内量化增强约束参数参考。

---

*诊断完。建议优先执行第四节 P0 操作（约束参数放宽），重跑回测，观察 IR 变化后再决定是否继续 P1 操作。*
