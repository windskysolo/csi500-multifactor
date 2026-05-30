# 时变 Ridge 模型：数学框架与原理

> 目的：为中证500多因子策略寻找 expanding（全历史等权）和 rolling（硬截断）之间更合理的系数估计方案。  
> 本文覆盖4个可行框架，由简到难，每个框架都给出完整数学推导和直觉解释。

---

## 0. 问题定义

在每个调仓月 T，我们有一个截面数据集：

$$
\mathcal{D}_T = \{(X_t,\ y_t)\}_{t=1}^{T}
$$

- $X_t \in \mathbb{R}^{n_t \times p}$：第 $t$ 期的因子矩阵（$n_t$ 只股票，$p=18$ 个因子）
- $y_t \in \mathbb{R}^{n_t}$：第 $t$ 期的超额收益向量（预测目标）
- $\beta \in \mathbb{R}^p$：待估计的因子权重向量

**三种极端情形**：

| 方案 | 思路 | 问题 |
|------|------|------|
| Expanding Ridge | $\mathcal{D}_T$ 全部历史等权 | 早期旧数据与当前同等重要，适应市场变化慢 |
| Rolling Ridge | 只用最近 $W$ 期，其余硬截断 | 信息突然消失，结果对窗口长度高度敏感 |
| **时变 Ridge** | **近期权重高，远期权重低，平滑过渡** | **本文要实现的目标** |

---

## 框架一：指数衰减加权 Ridge（EW-Ridge）

### 核心思想

给不同时期的观测赋予不同的权重，越近期的数据权重越高，以指数速率衰减：

$$
w_{T,t} = \lambda^{T - t}, \quad \lambda \in (0, 1)
$$

例如 $\lambda = 0.97$：前1期权重为 1，前12期权重为 $0.97^{12} \approx 0.694$，前24期权重为 $0.97^{24} \approx 0.481$。

### 目标函数

$$
\hat{\beta}_T = \arg\min_{\beta} \underbrace{\sum_{t=1}^{T} \lambda^{T-t} \|y_t - X_t \beta\|^2}_{\text{加权重建误差}} + \underbrace{\alpha \|\beta\|^2}_{\text{Ridge 正则项}}
$$

这是**加权最小二乘 + L2 正则**，写成矩阵形式：

$$
\mathcal{L}(\beta) = (\mathbf{y} - \mathbf{X}\beta)^\top \mathbf{W} (\mathbf{y} - \mathbf{X}\beta) + \alpha \|\beta\|^2
$$

其中堆叠后：

$$
\mathbf{X} = \begin{pmatrix} X_1 \\ X_2 \\ \vdots \\ X_T \end{pmatrix} \in \mathbb{R}^{N \times p}, \quad
\mathbf{W} = \mathrm{diag}(\underbrace{\lambda^{T-1},\ldots,\lambda^{T-1}}_{n_1},\ \underbrace{\lambda^{T-2},\ldots,\lambda^{T-2}}_{n_2},\ \ldots,\ \underbrace{1,\ldots,1}_{n_T})
$$

（同一时期 $t$ 内的所有股票赋予相同的时间权重 $\lambda^{T-t}$）

### 闭式解

对 $\beta$ 求导令其为零：

$$
\nabla_\beta \mathcal{L} = -2\mathbf{X}^\top \mathbf{W} (\mathbf{y} - \mathbf{X}\beta) + 2\alpha\beta = 0
$$

$$
\boxed{\hat{\beta}_T = \left(\mathbf{X}^\top \mathbf{W} \mathbf{X} + \alpha \mathbf{I}\right)^{-1} \mathbf{X}^\top \mathbf{W} \mathbf{y}}
$$

与标准 Ridge 解 $(\mathbf{X}^\top\mathbf{X} + \alpha\mathbf{I})^{-1}\mathbf{X}^\top\mathbf{y}$ 完全一致，仅将 $\mathbf{X}^\top\mathbf{X}$ 换成了 $\mathbf{X}^\top\mathbf{W}\mathbf{X}$。

### 关键参数：$\lambda$ 的直觉解释

**半衰期**（权重衰减到 $0.5$ 所需的时间步数）：

$$
t_{1/2} = \frac{\ln 0.5}{\ln \lambda} = \frac{-\ln 2}{\ln \lambda}
$$

| $\lambda$ | 半衰期（月） | 等效历史 $N_{\text{eff}} \approx \frac{1}{1-\lambda}$（月） | 对应含义 |
|-----------|------------|----------------------------------------------|---------|
| 0.99 | 69 个月 | 100 期 | 接近 expanding（慢衰减） |
| 0.97 | 23 个月 | 33 期 | 近 2 年有效记忆 |
| 0.95 | 14 个月 | 20 期 | 近 1.5 年 |
| 0.90 | 7 个月  | 10 期 | 接近 rolling-12m（快衰减）|

直觉：**$\lambda = 0.97$ 表示你认为 2 年前的市场规律对今天的预测力约等于当前的一半**。

### 实现

```python
import numpy as np
from sklearn.linear_model import Ridge

def build_sample_weights(n_per_period: list[int], lam: float) -> np.ndarray:
    """n_per_period[t] = 第 t 期截面股票数，t=0 是最远期"""
    T = len(n_per_period)
    weights = []
    for k, n in enumerate(n_per_period):
        # 最近一期 k=T-1，权重 λ^0=1；最远期 k=0，权重 λ^{T-1}
        w = lam ** (T - 1 - k)
        weights.extend([w] * n)
    return np.array(weights)

ridge = Ridge(alpha=5000)
ridge.fit(X_stack, y_stack, sample_weight=sample_weights)
beta_T = ridge.coef_
```

**超参数调优**：$\lambda$ 和 $\alpha$ 均通过 walk-forward CV 选择，与现有管线一致。

### 优缺点

| 优点 | 缺点 |
|------|------|
| 实现极简，sklearn 一行 | λ 固定，无法根据市场波动自适应 |
| 不丢弃任何历史信息 | 仍需手动调 λ |
| 与现有 walk-forward CV 完全兼容 | 当市场结构突变时衰减速度仍可能不够快 |

---

## 框架二：递归最小二乘（RLS）

### 核心思想

EW-Ridge 每期都要重新拟合全部历史数据，计算代价是 $O(T \cdot N \cdot p)$。  
RLS 给出了一个**等价的在线更新公式**：只需保存当前的 $(\hat{\beta}_t,\ P_t)$，每来一期新数据 $(X_{t+1},\ y_{t+1})$ 执行一次矩阵更新即可。

### 推导：为什么 RLS = EW-Ridge

记精度矩阵（Hessian 的一半）为：

$$
\Phi_t = \mathbf{X}_{1:t}^\top \mathbf{W}_t \mathbf{X}_{1:t} + \alpha\mathbf{I} = \sum_{s=1}^t \lambda^{t-s} X_s^\top X_s + \alpha\mathbf{I}
$$

递推关系：

$$
\Phi_t = \lambda \Phi_{t-1} + X_t^\top X_t
\quad \Rightarrow \quad
P_t \triangleq \Phi_t^{-1} = \frac{1}{\lambda}\left(P_{t-1} - \frac{P_{t-1} X_t^\top X_t P_{t-1}}{\lambda + X_t P_{t-1} X_t^\top}\right)
$$

（最后一步用了 Sherman-Morrison-Woodbury 矩阵求逆引理）

### 完整更新公式

已知上一期估计 $(\hat\beta_{t-1},\ P_{t-1})$，新数据 $(X_t \in \mathbb{R}^{n_t \times p},\ y_t \in \mathbb{R}^{n_t})$：

**步骤 1 — 计算增益矩阵**：

$$
K_t = P_{t-1} X_t^\top \left(\lambda \mathbf{I} + X_t P_{t-1} X_t^\top\right)^{-1} \in \mathbb{R}^{p \times n_t}
$$

**步骤 2 — 更新系数**：

$$
\hat\beta_t = \hat\beta_{t-1} + K_t \underbrace{(y_t - X_t \hat\beta_{t-1})}_{\text{预测误差（innovation）}}
$$

**步骤 3 — 更新精度矩阵逆**：

$$
P_t = \frac{1}{\lambda}\left(\mathbf{I} - K_t X_t\right) P_{t-1}
$$

**直觉解读**：
- $y_t - X_t\hat\beta_{t-1}$ 是**预测误差**（你的模型有多错）
- $K_t$ 是**增益矩阵**（应该把多少预测误差纳入修正），$P_{t-1}$ 越大说明系数越不确定，增益越大
- $1/\lambda > 1$ 在 Step 3 放大了 $P_t$，意思是**时间过去后我们对系数的不确定性增加了**，为下一次更新留出更大的修正空间

### 等价关系

> **定理**：无论初始值如何，足够多步迭代后，RLS($\lambda$) 的输出 $\hat\beta_t$ 与 EW-Ridge($\lambda$) 的批量解完全相同。

RLS 只是把 EW-Ridge 的矩阵求逆分拆成了增量更新，**数学含义完全一致**，区别仅在计算效率。

### 优缺点

| 优点 | 缺点 |
|------|------|
| 在线更新，无需存储全历史数据 | 仍是固定 $\lambda$，不自适应 |
| 与 EW-Ridge 数学等价，可互相验证 | 实现比 EW-Ridge 复杂 |
| 本项目样本不大，速度优势不明显 | 数值稳定性需处理（P 矩阵可能病态）|

**本项目建议**：直接用 EW-Ridge（批量解），RLS 理解即可，无需单独实现。

---

## 框架三：卡尔曼滤波 / 动态线性模型（DLM）

### 核心思想：系数本身是时变的随机过程

前两个框架都假设存在一个"真实但未知"的固定 $\beta^*$，只是用不同的权重方案去估计它。  
DLM 的出发点更根本：**因子权重本身就会随市场环境变化而漂移**，用一个随机过程来建模这种漂移。

### 模型设定（状态空间形式）

**状态方程**（系数如何演化）：

$$
\beta_t = \beta_{t-1} + \eta_t, \quad \eta_t \sim \mathcal{N}(0,\ Q)
$$

- $\beta_t \in \mathbb{R}^p$ 是第 $t$ 期的"真实"因子权重（不可观测的隐变量）
- $Q \in \mathbb{R}^{p \times p}$ 是**过程噪声协方差**，控制系数漂移的快慢
  - $Q$ 小 → 系数几乎不变（接近 Expanding Ridge）
  - $Q$ 大 → 系数快速变化（接近 Rolling Ridge）

**观测方程**（数据如何生成）：

$$
y_t = X_t \beta_t + \varepsilon_t, \quad \varepsilon_t \sim \mathcal{N}(0,\ R_t)
$$

- $R_t = \sigma^2 \mathbf{I}_{n_t}$ 是**观测噪声协方差**（收益率预测误差）

### 卡尔曼滤波递推

给定前一期后验 $\beta_{t-1} \sim \mathcal{N}(\mu_{t-1},\ \Sigma_{t-1})$，更新步骤：

---

**预测步（Prediction）**：将系数向前推一期

$$
\beta_{t|t-1} \sim \mathcal{N}(\underbrace{\mu_{t-1}}_{\text{均值不变}},\ \underbrace{\Sigma_{t-1} + Q}_{\text{不确定性增加}})
$$

$$
\mu_{t|t-1} = \mu_{t-1}, \quad \Sigma_{t|t-1} = \Sigma_{t-1} + Q
$$

预测步把协方差加上了 $Q$，意思是：**时间流逝本身会增加我们对系数的不确定性**，$Q$ 越大不确定性增加越快。

---

**更新步（Update）**：用新观测数据修正预测

$$
\text{Innovation:}\ e_t = y_t - X_t \mu_{t|t-1}
$$

$$
\text{Innovation 协方差:}\ S_t = X_t \Sigma_{t|t-1} X_t^\top + R_t
$$

$$
\text{卡尔曼增益:}\ K_t = \Sigma_{t|t-1} X_t^\top S_t^{-1} \in \mathbb{R}^{p \times n_t}
$$

$$
\boxed{\mu_{t|t} = \mu_{t|t-1} + K_t e_t}, \quad \boxed{\Sigma_{t|t} = (\mathbf{I} - K_t X_t) \Sigma_{t|t-1}}
$$

---

### 直觉解读

卡尔曼增益 $K_t$ 的意义：

$$
K_t = \frac{\text{当前系数的不确定性} \times \text{数据信息量}}{\text{当前系数的不确定性} \times \text{数据信息量} + \text{观测噪声}}
$$

- $\Sigma_{t|t-1}$ 大（我对系数很不确定）→ $K_t$ 大 → **新数据权重高**
- $R_t$ 大（观测噪声很大，数据不可信）→ $K_t$ 小 → **新数据权重低**

这就是自适应：**当市场发生结构性变化（之前的估计变得不确定）时，增益自动放大，模型更快响应新数据。**

### 与 EW-Ridge 的关系

**特殊情形：当 $Q = \frac{1-\lambda}{\lambda} \Sigma_{t-1}$，DLM 等价于 EW-Ridge($\lambda$)。**

DLM 比 EW-Ridge 多了一层自由度：$Q$ 可以是**非对角矩阵**，允许不同因子的系数以不同速率漂移，甚至建模不同因子系数之间的联动变化。

### 超参数设定

实践中 $Q$ 和 $R$ 的设定方式：

**方案 A — 简化为标量控制**：令 $Q = q \cdot \mathbf{I}$，$R = r \cdot \mathbf{I}$，调信噪比 $q/r$
- $q/r$ 小 → 慢适应（接近 expanding）
- $q/r$ 大 → 快适应（接近 rolling）

**方案 B — 用 MLE 估计**：最大化观测序列的边际似然

$$
\hat{Q}, \hat{R} = \arg\max_{Q,R} \sum_{t=1}^T \log p(y_t | y_{1:t-1},\ Q,\ R)
$$

每一步的预测误差 $e_t$ 服从 $\mathcal{N}(0, S_t)$，所以对数似然为：

$$
\log \mathcal{L} = -\frac{1}{2}\sum_t \left[\log|S_t| + e_t^\top S_t^{-1} e_t\right]
$$

可以用 `scipy.optimize` 或 `statsmodels.tsa.statespace` 优化。

**方案 C — 先验约束**：通过先验知识设定 $Q$。例如，我们知道 `q_roe` 在 2021-2022 出现方向翻转，可以给该维度设置更大的 $Q$ 值，让模型更快适应。

### 优缺点

| 优点 | 缺点 |
|------|------|
| 自动平衡历史信息与近期数据 | $Q$ 矩阵有 $p(p+1)/2 = 171$ 个参数，过拟合风险高 |
| 系数不确定性可量化（$\Sigma_t$） | 实现较复杂 |
| 不需要手动设 $\lambda$ | MLE 估计 $Q$ 需要大样本 |
| 理论上最优（在线性高斯假设下）| 实践中对 $Q$ 形式敏感 |

---

## 框架四：贝叶斯序列 Ridge

### 核心思想：先验 = 上期后验

贝叶斯方法提供了一个与 DLM 等价但更易理解的视角：  
**把上一期对 $\beta$ 的后验估计作为本期的先验，每期用新数据更新信念。**

### 静态 Bayesian Ridge（复习标准 Ridge 的贝叶斯解释）

设先验 $\beta \sim \mathcal{N}(0,\ \frac{1}{\alpha}\mathbf{I})$，似然 $y | X, \beta \sim \mathcal{N}(X\beta,\ \sigma^2\mathbf{I})$。

由贝叶斯公式（共轭正态更新）：

$$
\beta | \mathcal{D} \sim \mathcal{N}(\mu^*,\ \Sigma^*)
$$

$$
(\Sigma^*)^{-1} = \frac{1}{\sigma^2} X^\top X + \alpha \mathbf{I}, \quad \mu^* = \Sigma^* \cdot \frac{1}{\sigma^2} X^\top y
$$

注意：$\mu^* = (X^\top X + \alpha\sigma^2 \mathbf{I})^{-1} X^\top y$ — **这正是标准 Ridge 的 MAP 解！**

$$
\text{Ridge} \equiv \text{零均值球形高斯先验下的贝叶斯后验均值}
$$

### 序列贝叶斯 Ridge（时变版本）

初始化：$\beta_0 \sim \mathcal{N}(0,\ \frac{1}{\alpha}\mathbf{I})$（等同于 Ridge 先验）

每期更新（共轭正态闭式解）：

$$
\Sigma_t^{-1} = \Sigma_{t-1}^{-1} + \frac{1}{\sigma^2} X_t^\top X_t
$$

$$
\mu_t = \Sigma_t \left(\Sigma_{t-1}^{-1} \mu_{t-1} + \frac{1}{\sigma^2} X_t^\top y_t\right)
$$

**解读**：
- 精度矩阵 $\Sigma_t^{-1}$ 随每期数据**累加增大**（越来越确定）
- 均值 $\mu_t$ 是先验均值 $\mu_{t-1}$ 与新数据信息的加权融合
- 权重由精度决定：信息量越大的来源，权重越高

### 引入遗忘：打折先验（Discounted Prior）

纯序列更新等价于 Expanding Ridge（不遗忘任何历史）。  
为了引入遗忘，每期更新之前**主动增大先验不确定性**（折扣精度矩阵）：

$$
\Sigma_{t|t-1}^{-1} = \lambda \cdot \Sigma_{t-1}^{-1}
$$

这等价于：对上期的估计"打折"，减少它对当期的约束力。此时序列贝叶斯 Ridge = EW-Ridge($\lambda$) = RLS($\lambda$)，**四者等价**。

### 直觉对比

| 框架 | 遗忘机制 | 本质操作 |
|------|---------|---------|
| EW-Ridge | $w_t = \lambda^{T-t}$（直接折扣数据权重）| 旧数据的"票权"随时间缩小 |
| RLS | $P_t \leftarrow \frac{1}{\lambda} P_t$（放大不确定性）| 允许系数随时间有更大的修正空间 |
| Kalman | $\Sigma_{t\|t-1} = \Sigma_{t-1} + Q$（加过程噪声）| 系数会自然漂移，新数据纠正漂移 |
| Bayes | $\Sigma^{-1} \leftarrow \lambda \Sigma^{-1}$（折扣精度）| 对先验的信心随时间减弱 |

**它们在线性高斯假设下本质相同，是同一个数学对象的四种等价表述。**

---

## 框架总结与选择指南

### 完整对比

| | EW-Ridge | RLS | Kalman / DLM | 序列贝叶斯 |
|---|---|---|---|---|
| **遗忘机制** | 固定 $\lambda$ | 固定 $\lambda$（等价） | 自适应（$Q/R$） | 固定 $\lambda$（等价）|
| **超参数** | $\lambda,\ \alpha$ | $\lambda,\ \alpha$ | $Q,\ R,\ \alpha$ | $\lambda,\ \sigma^2,\ \alpha$ |
| **自适应能力** | 无（固定衰减速率）| 无 | 有（可学习 $Q$）| 无（除非设置时变 $\lambda$）|
| **实现难度** | ★☆☆☆ | ★★☆☆ | ★★★★ | ★★★☆ |
| **解释性** | 直观 | 直观 | 中等 | 高（先验 = 信念）|
| **是否需要全历史** | 是（批量）| 否（在线）| 否（在线）| 否（在线）|
| **本项目可行性** | ✅ 立即可用 | ✅ 可作验证 | ⚠️ $Q$ 需简化 | ✅ 可用于实验 |

### 扩展方向：混合专家模型（Mixture of Experts）

上述框架都假设系数平滑演化。但我们已经观察到，2021 年和 2022 年的市场环境截然不同（周期牛市 vs 杀估值熊市）。

更直接的方案：**识别市场 regime，每个 regime 用独立 Ridge 模型**。

$$
\hat{y}_{t+1} = \sum_{k=1}^K \underbrace{P(z_t = k \mid \text{market state})}_{\text{regime 概率}} \cdot X_{t+1} \hat\beta^{(k)}
$$

- $z_t \in \{1,\ldots,K\}$：市场状态（牛/熊/震荡）
- $\hat\beta^{(k)}$：第 $k$ 个 regime 下用 EW-Ridge 估计的系数

**regime 判断规则（简单版）**：
```
z_t = "bull"  if 12M_market_return > +15%
z_t = "bear"  if 12M_market_return < -10%
z_t = "neutral" otherwise
```
或用波动率：`z_t = "high_vol"` if realized_vol > 历史75分位。

**主要风险**：regime 标签本身依赖未来数据，需要用 t-1 月的信号来预测 t 月的 regime，避免前视偏差。

---

## 推荐的实验顺序

```
Step 1  EW-Ridge (λ decay)
  → 实现: sklearn Ridge + sample_weight=λ^k
  → 调参: λ ∈ {0.99, 0.97, 0.95, 0.90} × α ∈ {500, 2000, 5000}
  → 对比: vs expanding, vs rolling-48m（固定同一 optimizer）
  → 评估: 逐年超额、MDD、IC_IR 是否比两端更稳定

Step 2  Kalman（简化版）
  → 先用标量 Q = q·I，调信噪比 q/σ²
  → 对比 EW-Ridge：是否能在2021/2022切换时更快响应
  → 若有效，再尝试 MLE 估计 Q

Step 3（可选）Mixture of Experts
  → 需要先验证 regime 标签本身的有效性
  → 用 2012-2020 估计 regime 条件均值，2021-2022 验证
```

---

## 附：关键数学结论速查

$$
\text{EW-Ridge}(\lambda) \equiv \text{RLS}(\lambda) \equiv \text{Bayes}(\lambda\text{-discount}) \equiv \text{Kalman}\left(Q = \tfrac{1-\lambda}{\lambda}\Sigma\right)
$$

在选择框架时，选择你**最容易调参和理解的那一个**；数学上它们等价。

**对于本项目，第一步建议选 EW-Ridge**：一行代码实现，$\lambda$ 的含义（半衰期）直觉清晰，walk-forward CV 调参逻辑和现有 Ridge 完全一致。

---

*文件创建日期：2026-05-28*  
*相关实验规划：`current work/open_questions.md` § 2.3 S-03*
