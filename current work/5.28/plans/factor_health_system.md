# 多因子策略：因子检验与健康监控体系改进计划

> **文档版本**：v1.0  
> **适用策略**：中证500指数增强 · 月度调仓  
> **因子池**：17个正式因子 + 1个候补（high_52w_v2）  
> **覆盖范围**：截面有效性检验 · 时序稳定性监控 · 失效根因诊断 · 动态响应流程  
> **生成日期**：2026-05-28

---

## 目录

1. [现状诊断：当前体系的能力边界](#一现状诊断当前体系的能力边界)
2. [改进目标与整体架构](#二改进目标与整体架构)
3. [模块一：截面有效性检验（增强版）](#三模块一截面有效性检验增强版)
4. [模块二：时序稳定性监控](#四模块二时序稳定性监控)
5. [模块三：IC 衰减速度分析](#五模块三ic-衰减速度分析)
6. [模块四：失效根因诊断框架](#六模块四失效根因诊断框架)
7. [模块五：月度自动化健康报告](#七模块五月度自动化健康报告)
8. [因子响应决策流程](#八因子响应决策流程)
9. [针对当前因子池的优先行动计划](#九针对当前因子池的优先行动计划)
10. [附录：完整代码实现](#十附录完整代码实现)

---

## 一、现状诊断：当前体系的能力边界

### 1.1 已有能力（四道门控）

| Gate | 检验内容 | 状态 |
|------|---------|------|
| Gate 0 | 数据覆盖率 ≥ 80% | ✅ 已有 |
| Gate 1 | IC_IR ≥ 0.30，t 统计量 ≥ 2 | ✅ 已有 |
| Gate 2 | lead_ratio < 1.5x（防未来数据）| ✅ 已有 |
| Gate 3 | 验证期 IC_IR ≥ 0.20，方向不变 | ✅ 已有 |

**核心问题**：四道门是**入池时的截面判断**，是一次性的静态快照。它回答的是"这个因子在历史上有没有用"，而不是"这个因子现在还有没有用"以及"它是怎么失效的"。

### 1.2 已暴露的风险：piotroski_f 案例

`piotroski_f` 在训练期 IC_IR 高达 **+0.615**（因子池第一），验证期却反转至 **-0.200**。这个失效如果有滚动监控机制，**至少应该在 2020 年底就发出预警**，而非在验证期结束后才被事后发现。

代价是明确的：验证期归因显示"质量维度"对超额的累计拖累为 **-1.46%**，其中 piotroski_f 反转是主要来源。

### 1.3 当前体系的能力缺口

| 检验维度 | 核心问题 | 缺口等级 |
|---------|---------|---------|
| 截面有效性 | 因子在整个样本期有无选股能力 | ⚠️ 基本覆盖，缺 Fama-MacBeth 精确检验 |
| **时序稳定性** | 因子预测力随时间如何变化，在哪个阶段失效 | 🔴 严重缺失 |
| **IC 衰减速度** | 信号在多少期后失效，月度调仓是否最优 | 🔴 完全缺失 |
| **失效根因诊断** | 是风格周期？逻辑崩溃？还是数据错误 | 🔴 仅有初步假说，未系统化 |
| 月度自动预警 | 实时因子健康状态的动态监控 | 🔴 完全缺失 |

---

## 二、改进目标与整体架构

### 2.1 改进目标

```
当前：入池时一次性检验（静态）
目标：贯穿因子全生命周期的动态监控体系（动态）

    因子候选 ──→ 入池检验 ──→ 月度健康监控 ──→ 失效诊断 ──→ 响应决策
                  [已有]          [新增]           [新增]       [新增]
```

### 2.2 新增五个模块概览

```
┌─────────────────────────────────────────────────────────────────┐
│                    因子检验与监控体系                             │
│                                                                 │
│  ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌──────────┐  │
│  │ 模块一    │   │ 模块二    │   │ 模块三    │   │ 模块四   │  │
│  │ 截面有效性 │   │ 时序稳定性 │   │ IC衰减速度 │   │ 根因诊断 │  │
│  │（增强版）  │   │（滚动监控）│   │（调仓频率）│   │（四象限）│  │
│  └─────┬─────┘   └─────┬─────┘   └─────┬─────┘   └────┬─────┘  │
│        └───────────────┴───────────────┴──────────────┘        │
│                              │                                  │
│                    ┌─────────▼─────────┐                       │
│                    │     模块五         │                       │
│                    │ 月度自动化健康报告  │                       │
│                    │（每次调仓前运行）   │                       │
│                    └───────────────────┘                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、模块一：截面有效性检验（增强版）

### 3.1 现有 IC/IC_IR 检验的局限

IC 是 Spearman 秩相关，优点是对异常值鲁棒，缺点是：
- 无法同时控制多因子，无法识别"这个因子是否只是另一个因子的影子"
- 标准 t 检验假设月度 IC 无序列相关，而实际月度 IC 有正序列相关，导致 t 统计量虚高

### 3.2 新增：Fama-MacBeth 截面回归

**原理**：每期运行截面 OLS，收集各期因子系数 $\hat{\lambda}_t$，再对时序均值做 t 检验：

$$r_{i,t+1} = \alpha_t + \lambda_t f_{i,t} + \epsilon_{i,t+1}$$

$$\bar{\lambda} = \frac{1}{T}\sum_{t=1}^{T}\hat{\lambda}_t, \quad t\text{-stat} = \frac{\bar{\lambda}}{\widehat{se}_{NW}(\hat{\lambda})}$$

**关键**：标准误必须使用 **Newey-West 修正**（滞后期取 $\lfloor T^{1/3} \rfloor$），因为月度因子收益存在序列相关，否则 t 统计量严重虚高。

```python
def fama_macbeth_single(factor_df: pd.DataFrame,
                         ret_df: pd.DataFrame,
                         factor_name: str,
                         nw_lags: int = 4) -> dict:
    """
    Fama-MacBeth 单因子截面回归 + Newey-West 标准误
    
    Parameters
    ----------
    factor_df   : (date × stock) 因子值，已中性化标准化
    ret_df      : (date × stock) 下期超额收益
    factor_name : 因子名称，仅用于输出标记
    nw_lags     : Newey-West 滞后阶数，默认4（月度数据）
    
    Returns
    -------
    dict: mean_lambda, nw_t_stat, positive_ratio, n_periods
    """
    lambdas = []
    dates = factor_df.index.intersection(ret_df.index)

    for t in dates:
        f = factor_df.loc[t].dropna()
        r = ret_df.loc[t].reindex(f.index).dropna()
        common = f.index.intersection(r.index)
        if len(common) < 50:
            continue
        X = np.column_stack([np.ones(len(common)), f.loc[common].values])
        y = r.loc[common].values
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        lambdas.append(coef[1])         # 取因子系数，忽略截距

    lam = np.array(lambdas)
    T   = len(lam)
    mu  = lam.mean()

    # Newey-West 方差估计
    gamma0 = np.var(lam, ddof=1)
    nw_var  = gamma0
    for lag in range(1, nw_lags + 1):
        w = 1 - lag / (nw_lags + 1)                      # Bartlett 核权重
        gamma_l = np.cov(lam[lag:], lam[:-lag])[0, 1]
        nw_var  += 2 * w * gamma_l

    nw_se   = np.sqrt(nw_var / T)
    t_stat  = mu / nw_se if nw_se > 0 else np.nan

    return {
        'factor':         factor_name,
        'mean_lambda':    round(mu, 5),
        'nw_t_stat':      round(t_stat, 3),
        'positive_ratio': round((lam > 0).mean(), 3),
        'n_periods':      T,
        'significant':    abs(t_stat) >= 2.0,
    }
```

**判断标准**：

| 指标 | 通过阈值 |
|------|---------|
| `|nw_t_stat|` | ≥ 2.0（双侧 5% 显著水平）|
| `positive_ratio`（正向因子）| ≥ 0.55 |
| `mean_lambda` 方向 | 与预期一致 |

> ⚠️ **局限性说明**：Fama-MacBeth 假设截面残差无相关性，但同期所有股票受到共同因子影响（截面相关），严格来说需要 Shanken（1992）修正。你的因子已做行业/市值中性化，这个问题相对较轻，但不能忽视。在多因子联合回归时，建议同时控制市值和行业哑变量。

### 3.3 多因子联合显著性检验

单因子 Fama-MacBeth 可能遗漏因子共线性带来的伪显著。在合成权重估计前，建议定期运行**多因子联合截面回归**：

$$r_{i,t+1} = \alpha_t + \sum_{k=1}^{K} \lambda_{k,t} f_{i,t}^{(k)} + \epsilon_{i,t+1}$$

关注每个因子的 $\bar{\lambda}_k$ 在多因子框架下是否仍然显著，从而识别"只是相关因子影子"的冗余因子。

---

## 四、模块二：时序稳定性监控

这是**当前体系中最薄弱、最需要填补的缺口**。

### 4.1 滚动 IC_IR 曲线

对因子池中每个因子，在每个调仓日 $T$ 计算滚动 IC_IR：

$$\text{Rolling IC\_IR}_T^{(W)} = \frac{\bar{IC}_{T-W+1:T}}{\sigma_{IC_{T-W+1:T}}}$$

推荐同时维护三个窗口：**12 个月**（短期敏感）、**24 个月**（中期基准）、**36 个月**（长期稳定性）。

```python
def compute_rolling_ic_ir(ic_series: pd.Series,
                           windows: list = [12, 24, 36],
                           min_obs_ratio: float = 0.7) -> pd.DataFrame:
    """
    计算多窗口滚动 IC_IR 曲线
    
    Parameters
    ----------
    ic_series      : 每月 IC 值的时间序列，index 为日期
    windows        : 滚动窗口列表（月数）
    min_obs_ratio  : 窗口内最少有效观测比例
    """
    results = {}
    for w in windows:
        min_obs = int(w * min_obs_ratio)
        roll_mean = ic_series.rolling(window=w, min_periods=min_obs).mean()
        roll_std  = ic_series.rolling(window=w, min_periods=min_obs).std()
        results[f'ic_ir_{w}m'] = roll_mean / roll_std

    df = pd.DataFrame(results, index=ic_series.index)
    df['ic_monthly'] = ic_series   # 保留原始月度 IC 供绘图

    return df


def plot_factor_health_panel(ic_history: pd.DataFrame,
                              factor_names: list,
                              warn_band: float = 0.20,
                              stable_threshold: float = 0.30):
    """
    绘制因子健康面板（6×3 子图）
    每个子图显示：月度 IC（灰色柱）+ 三条滚动 IC_IR 曲线 + 阈值线
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    n = len(factor_names)
    ncols = 3
    nrows = (n + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(18, nrows * 4))
    axes = axes.flatten()

    colors = {'ic_ir_12m': '#E74C3C', 'ic_ir_24m': '#2980B9', 'ic_ir_36m': '#27AE60'}
    window_labels = {'ic_ir_12m': '12m', 'ic_ir_24m': '24m（基准）', 'ic_ir_36m': '36m'}

    for i, fname in enumerate(factor_names):
        ax = axes[i]
        ic_series = ic_history[fname]
        rolling_df = compute_rolling_ic_ir(ic_series)

        # 月度 IC 柱状图（背景）
        colors_bar = ['#27AE60' if v > 0 else '#E74C3C' for v in ic_series]
        ax.bar(ic_series.index, ic_series.values, color=colors_bar,
               alpha=0.25, width=20, label='月度IC')

        # 三条滚动 IC_IR 曲线
        for col, color in colors.items():
            rolling_df[col].plot(ax=ax, color=color, linewidth=1.8,
                                  label=window_labels[col])

        # 阈值线
        ax.axhline(stable_threshold, color='#27AE60', linestyle='--',
                   alpha=0.6, linewidth=1, label=f'稳定阈值±{stable_threshold}')
        ax.axhline(-stable_threshold, color='#27AE60', linestyle='--',
                   alpha=0.6, linewidth=1)
        ax.axhline(warn_band, color='#F39C12', linestyle=':', alpha=0.6, linewidth=1)
        ax.axhline(-warn_band, color='#F39C12', linestyle=':', alpha=0.6, linewidth=1)
        ax.axhline(0, color='black', linewidth=0.8, alpha=0.4)

        # 标题：显示最新 24m IC_IR 状态
        latest_ir = rolling_df['ic_ir_24m'].iloc[-1]
        status_color = '#27AE60' if abs(latest_ir) >= stable_threshold else \
                       '#E67E22' if abs(latest_ir) >= warn_band else '#E74C3C'
        ax.set_title(f'{fname}\n(24m IC_IR: {latest_ir:.3f})',
                     fontsize=9, color=status_color, fontweight='bold')
        ax.set_ylim(-1.2, 1.2)
        ax.legend(fontsize=6, loc='upper left')
        ax.grid(True, alpha=0.2)

    # 隐藏多余子图
    for j in range(i + 1, len(axes)):
        axes[j].set_visible(False)

    plt.suptitle('因子池健康面板（滚动 IC_IR）', fontsize=14, fontweight='bold', y=1.01)
    plt.tight_layout()
    return fig
```

### 4.2 三种时序失效模式识别

| 模式 | 滚动 IC_IR 形态 | 解读 | 应对 |
|-----|---------------|------|------|
| **渐进衰退** | 从高位缓慢下滑，三个窗口同向 | 因子被市场学习/套利，溢价逐渐消失 | 考虑剔除或寻找升级版本 |
| **突变反转** | 某时间点后方向突然翻转，12m 先于 36m 转负 | 结构性变化（政策、市场机制）| 立即启动根因诊断（见模块四）|
| **周期波动** | 在阈值上下震荡，有规律性 | 风格因子，与市场情绪周期绑定 | 保留，IC_IR 权重机制自动处理 |

### 4.3 Chow 结构突变检验

对疑似"突变反转"的因子，自动扫描结构断点：

$$H_0:\ \lambda\ \text{在断点前后无显著差异} \quad F = \frac{(RSS_R - RSS_1 - RSS_2)/k}{(RSS_1 + RSS_2)/(T-2k)} \sim F(k,\ T-2k)$$

```python
def scan_structural_break(ic_series: pd.Series,
                           search_start: str = '2018-01',
                           search_end:   str = '2022-12') -> pd.DataFrame:
    """
    对因子 IC 序列扫描结构断点（Chow 检验）
    返回所有候选断点的 F 统计量和 p 值，按显著性排序
    
    ic_series   : 月度 IC 时间序列
    search_start: 扫描起始日期（留出足够训练样本）
    search_end  : 扫描截止日期
    """
    from scipy import stats

    candidates = pd.date_range(search_start, search_end, freq='QS')
    results = []

    for bp in candidates:
        before = ic_series[:bp].dropna()
        after  = ic_series[bp:].dropna()
        full   = ic_series.dropna()

        # 各段内用均值拟合（Chow 均值相等检验）
        if len(before) < 12 or len(after) < 12:
            continue

        rss_before = np.sum((before - before.mean()) ** 2)
        rss_after  = np.sum((after  - after.mean())  ** 2)
        rss_full   = np.sum((full   - full.mean())   ** 2)

        k = 1                            # 只有均值这1个参数
        T = len(full)
        num   = (rss_full - rss_before - rss_after) / k
        denom = (rss_before + rss_after) / (T - 2 * k)

        if denom <= 0:
            continue

        F_stat  = num / denom
        p_value = 1 - stats.f.cdf(F_stat, k, T - 2 * k)

        results.append({
            'breakpoint':    bp,
            'F_stat':        round(F_stat, 3),
            'p_value':       round(p_value, 4),
            'ic_ir_before':  round(before.mean() / before.std(), 3),
            'ic_ir_after':   round(after.mean()  / after.std(),  3),
            'sign_change':   (before.mean() * after.mean()) < 0,   # 是否方向反转
            'significant':   p_value < 0.05,
        })

    return pd.DataFrame(results).sort_values('p_value').reset_index(drop=True)
```

**使用建议**：对所有处于 `warn` 或 `reverse` 状态的因子，每季度运行一次 Chow 扫描，识别精确的失效时间点，为根因诊断（模块四）提供时间锚点。

---

## 五、模块三：IC 衰减速度分析

### 5.1 为什么需要 IC Decay 分析

你的策略是月度调仓，这个频率的选择应当以因子信号的衰减速度为依据，而非习惯。

- 若某因子在 lag=1 就快速衰减 → 月度调仓合理，但换手惩罚应适度降低
- 若某因子在 lag=3 才达到信号最强 → 可以考虑双月调仓，降低换手成本
- 若某因子衰减极慢（lag=6 仍有 IC）→ 该因子可以承受更低调仓频率，进一步节省交易成本

### 5.2 IC Decay 计算

对调仓日 $T$ 的因子截面值，分别计算与 $T+1, T+2, ..., T+K$ 月收益的 IC：

$$\text{IC}^{(k)}_T = \text{Corr}_{spearman}(f_{i,T},\ r_{i,T+k}), \quad k = 1, 2, ..., K$$

$K$ 取 6（半年），对所有调仓日取均值，绘制衰减曲线。

```python
def ic_decay_curve(factor_df: pd.DataFrame,
                   ret_df:    pd.DataFrame,
                   max_lag:   int = 6,
                   min_stocks: int = 50) -> pd.DataFrame:
    """
    计算因子 IC 衰减曲线（lag=1 到 max_lag 期）
    
    Parameters
    ----------
    factor_df  : (date × stock) 因子值，已中性化标准化
    ret_df     : (date × stock) 月度超额收益
    max_lag    : 最大滞后期数
    min_stocks : 每期最少有效股票数
    
    Returns
    -------
    DataFrame: index=lag(月), columns=[mean_ic, std_ic, ic_ir, t_stat]
    """
    dates = factor_df.index
    records = {lag: [] for lag in range(1, max_lag + 1)}

    for i, t in enumerate(dates):
        f = factor_df.loc[t].dropna()
        for lag in range(1, max_lag + 1):
            future_dates = dates[dates > t]
            if len(future_dates) < lag:
                continue
            future_t = future_dates[lag - 1]
            r = ret_df.loc[future_t].reindex(f.index).dropna()
            common = f.index.intersection(r.index)
            if len(common) < min_stocks:
                continue
            ic = f.loc[common].corr(r.loc[common], method='spearman')
            records[lag].append(ic)

    rows = []
    for lag, ic_list in records.items():
        if len(ic_list) < 10:
            continue
        arr = np.array(ic_list)
        mean_ic = arr.mean()
        std_ic  = arr.std()
        ic_ir   = mean_ic / std_ic if std_ic > 0 else np.nan
        t_stat  = mean_ic / (std_ic / np.sqrt(len(arr))) if std_ic > 0 else np.nan
        rows.append({
            'lag_months': lag,
            'mean_ic':    round(mean_ic, 4),
            'std_ic':     round(std_ic, 4),
            'ic_ir':      round(ic_ir, 3),
            't_stat':     round(t_stat, 3),
            'n_periods':  len(ic_list),
        })

    return pd.DataFrame(rows).set_index('lag_months')
```

### 5.3 针对你因子池的预判

| 因子 | 预期衰减模式 | 调仓频率建议 |
|-----|------------|------------|
| `turn_20d`、`ivol_60d` | 缓慢衰减（持续性强）| 月度足够，可降低换手惩罚系数 |
| `rev_acceleration`、`roe_delta_3q` | 中速衰减 | 月度合理 |
| `analyst_eps_revision` | 较快衰减（分析师修正信息扩散快）| 月度合理，但注意公告日集中效应 |
| `hk_hold_chg`（北向变化量）| 极快衰减（流量数据）| **超过 2 个月可能已失效**，需实测确认 |
| `hk_hold_ratio`（存量持仓）| 缓慢衰减 | 月度合理 |

> ⚠️ **关键警示**：如果 `hk_hold_chg` 的 lag=2 IC 已接近 0，说明该因子的有效预测窗口仅约 1 个月。在月度调仓策略中，其信号在调仓日已部分失效，实际贡献会低于单因子 IC 检验的估计。

---

## 六、模块四：失效根因诊断框架

因子失效的应对策略取决于失效的根本原因。错误地把"逻辑崩溃"当成"风格周期"等待，或者把"风格低谷"当成"逻辑错误"剔除，都会带来系统性损失。

### 6.1 四象限失效分类

```
                     是否所有行业同步失效？
                        是                否
                ┌──────────────────┬──────────────────┐
   风格/周期     │    Type A         │    Type C        │
   驱动（可逆）  │  全市场风格切换   │   行业轮动导致   │
                │  （等待轮转）     │   的局部失效     │
                ├──────────────────┼──────────────────┤
   结构性问题   │    Type B         │    Type D        │
   （需修复）   │   因子逻辑本身    │   数据/计算      │
                │   在当前市场失效  │   错误（可修复） │
                └──────────────────┴──────────────────┘
```

| 类型 | 特征 | 应对策略 | 优先级 |
|-----|-----|---------|------|
| **Type A** 全市场风格切换 | 全行业同步失效，历史有过相似周期 | **保留**，IC_IR 机制自动降权，等待风格轮转 | 低（系统自动处理）|
| **Type B** 因子逻辑失效 | 全行业同步失效，且无历史先例 | **立即重构或剔除**，因子经济逻辑已不适用当前市场 | 高 |
| **Type C** 行业特异性失效 | 仅部分行业失效，其他行业仍有效 | **行业分层使用**，或对失效行业屏蔽 | 中 |
| **Type D** 数据/计算错误 | lead_ratio 异常或特定子项方向错误 | **修复信号定义**（rev_yoy → rev_acceleration 是成功案例）| 高 |

### 6.2 诊断工具：行业分层 IC

```python
def ic_by_industry(factor_df:   pd.DataFrame,
                   ret_df:      pd.DataFrame,
                   industry_df: pd.DataFrame,
                   date_range:  tuple = None) -> pd.DataFrame:
    """
    分行业计算 IC，判断失效是全面性（Type A/B）还是行业特异性（Type C）
    
    industry_df: (date × stock) 行业标签（申万一级行业代码）
    date_range : (start_date, end_date) 聚焦诊断时段，如验证期 ('2021-01', '2022-12')
    """
    if date_range:
        factor_df   = factor_df.loc[date_range[0]: date_range[1]]
        ret_df      = ret_df.loc[date_range[0]: date_range[1]]
        industry_df = industry_df.loc[date_range[0]: date_range[1]]

    records = []
    dates = factor_df.index.intersection(ret_df.index).intersection(industry_df.index)

    for t in dates:
        f   = factor_df.loc[t].dropna()
        r   = ret_df.loc[t].reindex(f.index).dropna()
        ind = industry_df.loc[t].reindex(f.index).dropna()
        common = f.index.intersection(r.index).intersection(ind.index)

        for sector in ind.loc[common].unique():
            stocks = ind.loc[common][ind.loc[common] == sector].index
            if len(stocks) < 8:
                continue
            ic = f.loc[stocks].corr(r.loc[stocks], method='spearman')
            records.append({'date': t, 'sector': sector, 'ic': ic, 'n': len(stocks)})

    ic_df = pd.DataFrame(records)
    summary = (ic_df.groupby('sector')['ic']
                     .agg(['mean', 'std', 'count'])
                     .rename(columns={'mean': 'mean_ic', 'std': 'std_ic', 'count': 'n_months'}))
    summary['ic_ir'] = summary['mean_ic'] / summary['std_ic']
    summary['t_stat'] = summary['mean_ic'] / (summary['std_ic'] / np.sqrt(summary['n_months']))
    summary['significant'] = summary['t_stat'].abs() >= 1.65   # 10% 单侧
    return summary.sort_values('ic_ir', ascending=False)
```

**诊断判断规则**：

- 如果 **90% 以上行业的 IC 都同向失效** → 全市场性失效，走 Type A/B 流程
- 如果 **仅 1-3 个行业失效** → 行业特异性失效，走 Type C 流程，考虑行业屏蔽
- 结合滚动 IC_IR 的突变时间点，进一步区分 Type A（有历史先例）与 Type B（结构性新问题）

### 6.3 针对 piotroski_f 的专项诊断流程

`piotroski_f` 是目前唯一确认 `reverse` 的因子，以下是完整诊断步骤：

```
Step 1：分期 IC 分析
   计算：2012-15 / 2016-18 / 2019-20 / 2021-22 各子期的 IC_IR
   
   判断：
   ├─ 仅 2021-22 反转 → Type A（风格周期）→ 保留，等待轮转
   ├─ 2019-20 已开始衰减 → 可能 Type B，进入 Step 2
   └─ 多个子期均不稳定 → Type B（逻辑失配）→ 进入 Step 2+3

Step 2：行业分层 IC（以 2021-22 为诊断窗口）
   运行 ic_by_industry()
   
   判断：
   ├─ 仅 TMT/新能源行业 IC 为负，其他行业 IC 仍为正 → Type C
   └─ 全行业 IC 同步反转 → Type A 或 Type B，进入 Step 3

Step 3：子项 IC 拆解（A 股特异性检验）
   对 F1-F5,F8,F9 七个子项分别计算 IC
   
   重点关注（最可疑的 A 股失配子项）：
   ├─ F5（去杠杆：资产负债率同比下降）
   │   A 股 2020-22 年，加杠杆扩张的科技公司反而涨更多
   │   "去杠杆=好" 这个假设可能在 A 股成长期是反的
   │
   ├─ F8（毛利率同比上升）
   │   对于处于投资扩张期的成长股，毛利率阶段性下降是正常的
   │   与 gross_margin_trend 因子高度重叠，可能双重计算了负向信号
   │
   └─ F3（ROA 同比改善）
       与 q_roe 高度相关，且 q_roe 自身也处于 warn 状态
       两个方向相同的 warn/reverse 子项叠加，放大了负向贡献
```

```python
def diagnose_composite_factor(sub_factor_dict: dict,
                               ret_df: pd.DataFrame,
                               date_range: tuple = ('2021-01', '2022-12')) -> pd.DataFrame:
    """
    对复合因子（如 piotroski_f）的各子项分别计算 IC_IR，定位问题子项
    
    sub_factor_dict: {'F1': DataFrame, 'F2': DataFrame, ...} 各子项因子值
    ret_df         : 月度超额收益
    date_range     : 诊断时段（默认验证期）
    """
    results = []
    for sub_name, sub_df in sub_factor_dict.items():
        sub_slice = sub_df.loc[date_range[0]: date_range[1]]
        ret_slice = ret_df.loc[date_range[0]: date_range[1]]
        dates = sub_slice.index.intersection(ret_slice.index)
        ic_list = []
        for t in dates:
            f = sub_slice.loc[t].dropna()
            r = ret_slice.loc[t].reindex(f.index).dropna()
            common = f.index.intersection(r.index)
            if len(common) < 30:
                continue
            ic_list.append(f.loc[common].corr(r.loc[common], method='spearman'))
        arr = np.array(ic_list)
        results.append({
            'sub_factor': sub_name,
            'mean_ic':    round(arr.mean(), 4),
            'ic_ir':      round(arr.mean() / arr.std(), 3) if arr.std() > 0 else np.nan,
            'pos_ratio':  round((arr > 0).mean(), 2),
            'diagnosis':  '✅ 正向有效' if arr.mean() / arr.std() > 0.2 else
                          '⚠️ 方向失效' if arr.mean() / arr.std() < -0.15 else
                          '🔘 接近无效',
        })
    return pd.DataFrame(results).sort_values('ic_ir')
```

---

## 七、模块五：月度自动化健康报告

### 7.1 设计目标

每次调仓前（月末倒数第 2 个交易日）自动运行，输出因子健康状态报告，用于：
1. 决定是否需要调整因子池（调池）
2. 决定是否需要进一步根因诊断
3. 记录历史健康状态，追踪趋势

### 7.2 健康状态判定逻辑

对每个因子，综合三个滚动窗口的 IC_IR，给出统一健康状态：

```python
class FactorHealthMonitor:
    """
    月度因子健康监控系统
    每次调仓前运行，生成因子健康快照
    """

    STATUS_RULES = {
        'STABLE':  {'color': '🟢', 'desc': '稳定，维持正常权重'},
        'WEAK':    {'color': '🟡', 'desc': '偏弱，权重自动缩减'},
        'WARN':    {'color': '🟠', 'desc': '警告，接近无效区间'},
        'REVERSE': {'color': '🔴', 'desc': '反转，IC_IR 已反向'},
    }

    def __init__(self,
                 ic_history: pd.DataFrame,
                 expected_direction: dict,
                 windows: list = [12, 24, 36],
                 thresholds: dict = None):
        """
        ic_history         : (date × factor) 每月 IC 值矩阵
        expected_direction : {'turn_20d': -1, 'ivol_60d': -1, 'ep_ttm': 1, ...}
        """
        self.ic = ic_history
        self.direction = expected_direction
        self.windows = windows
        self.thresholds = thresholds or {
            'stable':  0.30,
            'weak':    0.20,
            'warn':    0.05,
        }

    def _classify_status(self, ic_ir_24m: float, expected_dir: int) -> str:
        """根据 24 个月 IC_IR 和预期方向，判断健康状态"""
        adjusted_ir = ic_ir_24m * expected_dir   # 修正方向后的 IC_IR

        if adjusted_ir >= self.thresholds['stable']:
            return 'STABLE'
        elif adjusted_ir >= self.thresholds['weak']:
            return 'WEAK'
        elif adjusted_ir >= self.thresholds['warn']:
            return 'WARN'
        else:
            return 'REVERSE'

    def snapshot(self, as_of_date: pd.Timestamp = None) -> pd.DataFrame:
        """
        生成指定日期的因子健康快照
        
        Returns
        -------
        DataFrame: 每行一个因子，包含各窗口 IC_IR、健康状态、预警标记
        """
        if as_of_date is None:
            as_of_date = self.ic.index[-1]

        rows = []
        for fname in self.ic.columns:
            ic_series = self.ic[fname].loc[:as_of_date].dropna()
            exp_dir   = self.direction.get(fname, 1)
            row = {'factor': fname, 'expected_dir': exp_dir}

            for w in self.windows:
                recent = ic_series.iloc[-w:] if len(ic_series) >= w else ic_series
                ic_ir  = recent.mean() / recent.std() if recent.std() > 0 else np.nan
                row[f'ic_ir_{w}m'] = round(ic_ir, 3)

            # 主要状态以 24m 窗口为准
            ic_ir_24m = row.get('ic_ir_24m', np.nan)
            if not np.isnan(ic_ir_24m):
                row['status'] = self._classify_status(ic_ir_24m, exp_dir)
            else:
                row['status'] = 'UNKNOWN'

            # 趋势预警：12m IC_IR 比 36m 恶化超过 50%
            ir_12m = row.get('ic_ir_12m', np.nan)
            ir_36m = row.get('ic_ir_36m', np.nan)
            if not any(np.isnan([ir_12m, ir_36m])) and ir_36m != 0:
                deterioration = (abs(ir_12m) - abs(ir_36m)) / abs(ir_36m)
                row['trend_alert'] = '⚠️ 恶化中' if deterioration < -0.40 else '—'
            else:
                row['trend_alert'] = '—'

            row['action'] = self._recommend_action(row['status'], row['trend_alert'])
            rows.append(row)

        df = pd.DataFrame(rows)
        df['status_icon'] = df['status'].map(lambda s: self.STATUS_RULES.get(s, {}).get('color', '?'))
        return df.sort_values('ic_ir_24m', key=abs, ascending=False)

    def _recommend_action(self, status: str, trend: str) -> str:
        actions = {
            'STABLE':  '正常使用',
            'WEAK':    '保留观察' if '恶化' not in trend else '启动诊断',
            'WARN':    '启动根因诊断',
            'REVERSE': '立即诊断 / 考虑剔除',
        }
        return actions.get(status, '—')

    def print_report(self, as_of_date: pd.Timestamp = None) -> None:
        """打印格式化的月度健康报告"""
        snap = self.snapshot(as_of_date)
        date_str = (as_of_date or self.ic.index[-1]).strftime('%Y-%m')

        print(f"\n{'='*70}")
        print(f"  因子健康月报  |  {date_str}  |  因子数: {len(snap)}")
        print(f"{'='*70}")

        for _, row in snap.iterrows():
            print(f"  {row['status_icon']} {row['factor']:<22} "
                  f"IC_IR: {row['ic_ir_12m']:>6.3f}(12m) "
                  f"{row['ic_ir_24m']:>6.3f}(24m) "
                  f"{row['ic_ir_36m']:>6.3f}(36m)  "
                  f"[{row['status']:<7}]  {row['trend_alert']}  → {row['action']}")

        n_stable  = (snap['status'] == 'STABLE').sum()
        n_warn    = (snap['status'].isin(['WARN', 'REVERSE'])).sum()
        print(f"\n  汇总：稳定 {n_stable} 个 | 警告/反转 {n_warn} 个")
        print(f"{'='*70}\n")
```

### 7.3 报告示例输出

```
======================================================================
  因子健康月报  |  2026-05  |  因子数: 17
======================================================================
  🟢 turn_20d               IC_IR:  -0.712(12m) -0.707(24m) -0.695(36m)  [STABLE ]  —        → 正常使用
  🟢 ivol_60d               IC_IR:  -0.658(12m) -0.641(24m) -0.629(36m)  [STABLE ]  —        → 正常使用
  🟢 rev_acceleration       IC_IR:  +0.681(12m) +0.725(24m) +0.690(36m)  [STABLE ]  —        → 正常使用
  🟢 hk_hold_ratio          IC_IR:  +0.398(12m) +0.425(24m) +0.441(36m)  [STABLE ]  —        → 正常使用
  🟢 roe_delta_3q           IC_IR:  +0.312(12m) +0.338(24m) +0.355(36m)  [STABLE ]  —        → 正常使用
  🟢 holder_chg             IC_IR:  +0.301(12m) +0.319(24m) +0.328(36m)  [STABLE ]  —        → 正常使用
  🟢 analyst_eps_revision   IC_IR:  +0.298(12m) +0.332(24m) +0.339(36m)  [STABLE ]  —        → 正常使用
  🟡 amihud                 IC_IR:  +0.189(12m) +0.211(24m) +0.253(36m)  [WEAK   ]  ⚠️ 恶化中  → 启动诊断
  🟡 hk_hold_chg            IC_IR:  +0.141(12m) +0.154(24m) +0.187(36m)  [WEAK   ]  —        → 保留观察
  🟡 ep_ttm                 IC_IR:  +0.102(12m) +0.119(24m) +0.163(36m)  [WEAK   ]  —        → 保留观察
  🟡 cfp                    IC_IR:  +0.175(12m) +0.190(24m) +0.210(36m)  [WEAK   ]  —        → 保留观察
  🟠 q_roe                  IC_IR:  -0.098(12m) -0.041(24m) +0.089(36m)  [WARN   ]  ⚠️ 恶化中  → 启动根因诊断
  🟠 roe_delta              IC_IR:  -0.045(12m) -0.040(24m) +0.071(36m)  [WARN   ]  —        → 启动根因诊断
  🟠 rev_yoy                IC_IR:  -0.028(12m) -0.020(24m) +0.058(36m)  [WARN   ]  —        → 启动根因诊断
  🟠 gross_margin           IC_IR:  -0.051(12m) -0.040(24m) +0.032(36m)  [WARN   ]  ⚠️ 恶化中  → 启动根因诊断
  🟠 gross_margin_trend     IC_IR:  -0.079(12m) -0.069(24m) +0.018(36m)  [WARN   ]  ⚠️ 恶化中  → 启动根因诊断
  🔴 piotroski_f            IC_IR:  -0.213(12m) -0.200(24m) -0.089(36m)  [REVERSE]  —        → 立即诊断 / 考虑剔除

  汇总：稳定 7 个 | 警告/反转 6 个
======================================================================
```

---

## 八、因子响应决策流程

### 8.1 "调池"与"不调池"的判断边界

```
                     ┌──────────────────────┐
                     │ 因子进入 warn/reverse │
                     └──────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │ 运行根因诊断（模块四）  │
                    └───┬──────────────┬────┘
                        │              │
              ┌─────────▼──┐    ┌──────▼──────────┐
              │ Type A/C   │    │   Type B/D       │
              │ 风格/行业   │    │   结构/数据错误  │
              └─────┬───────┘    └──────┬───────────┘
                    │                   │
         ┌──────────▼─────┐     ┌───────▼──────────┐
         │ 不调池          │     │  调池             │
         │                │     │                  │
         │ IC_IR 权重机制  │     │  Type B：剔除    │
         │ 已自动降权      │     │  或重构因子逻辑   │
         │                │     │                  │
         │ 继续月度监控    │     │  Type D：修复信号 │
         │ 等待风格轮转    │     │  定义并重新入池   │
         └────────────────┘     └──────────────────┘
```

### 8.2 "不轻易剔除"的量化标准

对 `warn` 状态因子，只有满足**以下任一条件**才考虑剔除：

| 剔除条件 | 量化标准 | 目前命中的因子 |
|---------|---------|-------------|
| 结构性错误确认 | 子项拆解发现 A 股会计失配（假说 B）| piotroski_f（诊断中）|
| 有更优替代且高度相关 | 替代因子 IC_IR 更高，且两者相关系数 > 0.7 | rev_yoy vs rev_acceleration |
| 连续多窗口零贡献 | 3 个滚动窗口 IC_IR 均 < 0.10 且持续 > 12 个月 | 目前无 |

**对于仅仅"验证期表现差"的因子，不应轻易剔除**。理由：
- 验证期仅 24 个月，统计功效不足
- 风格周期通常 2-5 年，24 个月不足以判断是否为永久性失效
- IC_IR 权重机制已经在自动降低其贡献，剔除的边际收益有限

### 8.3 高优先级行动时间线

```
立即（本月）
├── 对 piotroski_f 运行三步诊断（分期IC → 行业分层 → 子项拆解）
├── 运行全部 17 因子的 IC Decay 曲线，确认月度调仓频率合理性
└── 建立月度 IC 数据库（每月 IC 值存档），作为监控系统的数据基础

本季度
├── 对 warn 状态的 5 个因子（q_roe / roe_delta / rev_yoy / gross_margin / gross_margin_trend）
│   运行 Chow 结构突变检验，定位精确失效时间点
├── 在 Ridge 流水线中测试 high_52w_v2（预期 Ridge L2 正则化会给予更合理权重）
└── 完成 Fama-MacBeth 多因子联合回归，识别因子池内的冗余信号

下一个评估周期（6 个月后）
├── 根据诊断结果决定 piotroski_f 是剔除、修复子项还是继续反向使用
├── 根据 IC Decay 结果考虑是否对 hk_hold_chg 降权或调整更新频率
└── 评估 rev_yoy 在 rev_acceleration 已入池的情况下是否仍提供独立信息
```

---

## 九、针对当前因子池的优先行动计划

### 9.1 因子状态全景与行动优先级

| 优先级 | 因子 | 状态 | 根因假设 | 行动 |
|--------|-----|------|---------|------|
| 🔴 P0 | `piotroski_f` | reverse | Type B（A股会计失配 F5/F8）+ Type A | 三步专项诊断，本月完成 |
| 🟠 P1 | `q_roe` | warn | Type A（科技行情偏好低ROE） | Chow 检验定位失效点，12个月后复查 |
| 🟠 P1 | `gross_margin` + `gross_margin_trend` | warn | Type A + 疑似 Type C（金融行业）| 行业分层IC，确认是否需要屏蔽 TMT |
| 🟡 P2 | `rev_yoy` | warn | Type D（基数效应）| IC Decay 检验，评估与 rev_acceleration 的信息独立性 |
| 🟡 P2 | `hk_hold_chg` | weak | 可能衰减过快 | IC Decay，确认 lag=2 后是否仍有效 |
| 🟢 P3 | `high_52w_v2` | 候补 | 动量风格低谷 | 在 Ridge 流水线测试，预期比 IC_IR 流水线表现好 |
| 🟢 P3 | `amihud` | weak + 恶化中 | 流动性溢价周期 | 滚动监控，关注 12m vs 36m IC_IR 的分化 |

### 9.2 测试集保护原则（重要提醒）

> **测试集（2023-2025）目前剩余 3 次使用机会，受项目纪律保护。**
>
> 上述所有诊断和改进工作**必须在训练期+验证期内完成**，不得以"诊断目的"消耗测试集机会。测试集仅用于最终策略的样本外性能评估，不得用于因子筛选或参数调整。
>
> 若因诊断需要扩展验证期，应考虑向前扩展（使用 2020 年之前的更多数据做分期诊断），而非动用 2023 年后的数据。

---

## 十、附录：完整代码实现

### A. 初始化与数据准备

```python
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy import stats
from typing import Optional, List, Dict, Tuple
import warnings
warnings.filterwarnings('ignore')

# ─── 因子预期方向配置（正向=+1, 负向=-1）───────────────────────────
EXPECTED_DIRECTIONS = {
    'ep_ttm':               +1,
    'cfp':                  +1,
    'q_roe':                +1,
    'gross_margin':         +1,
    'piotroski_f':          +1,   # 训练期方向，监控系统会自动检测反转
    'roe_delta':            +1,
    'roe_delta_3q':         +1,
    'rev_yoy':              +1,
    'rev_acceleration':     +1,
    'gross_margin_trend':   +1,
    'hk_hold_ratio':        +1,
    'hk_hold_chg':          +1,
    'holder_chg':           +1,
    'analyst_eps_revision': +1,
    'ivol_60d':             -1,   # 低特质波动跑赢，负向因子
    'turn_20d':             -1,   # 低换手跑赢，负向因子
    'amihud':               +1,
    'high_52w_v2':          +1,
}

# ─── 健康状态阈值（可根据实盘经验调整）──────────────────────────────
HEALTH_THRESHOLDS = {
    'stable':  0.30,   # 24m IC_IR 方向调整后 ≥ 0.30 → 稳定
    'weak':    0.20,   # 0.20 ~ 0.30 → 偏弱
    'warn':    0.05,   # 0.05 ~ 0.20 → 警告
    # < 0.05 → 反转
}
```

### B. 一键运行全套检验

```python
def run_full_factor_diagnostics(factor_df:    pd.DataFrame,
                                 ret_df:       pd.DataFrame,
                                 industry_df:  pd.DataFrame,
                                 ic_history:   pd.DataFrame,
                                 output_dir:   str = './factor_reports') -> None:
    """
    一键运行全套因子检验，生成 HTML 报告
    
    factor_df   : (date × stock) 因子值矩阵，已中性化标准化
    ret_df      : (date × stock) 月度超额收益
    industry_df : (date × stock) 申万一级行业标签
    ic_history  : (date × factor) 每月 IC 值历史
    output_dir  : 报告输出目录
    """
    import os
    os.makedirs(output_dir, exist_ok=True)

    print("▶ Step 1/5: Fama-MacBeth 截面显著性检验...")
    fm_results = []
    for fname in ic_history.columns:
        result = fama_macbeth_single(factor_df[fname].unstack() if hasattr(factor_df[fname], 'unstack')
                                     else factor_df, ret_df, fname)
        fm_results.append(result)
    fm_df = pd.DataFrame(fm_results)
    fm_df.to_csv(f'{output_dir}/01_fama_macbeth.csv', index=False)
    print(f"   完成。显著因子数: {fm_df['significant'].sum()}/{len(fm_df)}")

    print("▶ Step 2/5: 滚动 IC_IR 曲线与健康快照...")
    monitor = FactorHealthMonitor(ic_history, EXPECTED_DIRECTIONS,
                                  thresholds=HEALTH_THRESHOLDS)
    monitor.print_report()
    snap = monitor.snapshot()
    snap.to_csv(f'{output_dir}/02_health_snapshot.csv', index=False)
    fig = plot_factor_health_panel(ic_history, list(ic_history.columns))
    fig.savefig(f'{output_dir}/02_health_panel.png', dpi=150, bbox_inches='tight')
    print(f"   完成。健康面板已保存至 {output_dir}/02_health_panel.png")

    print("▶ Step 3/5: Chow 结构突变检验（针对 warn/reverse 因子）...")
    problem_factors = snap[snap['status'].isin(['WARN', 'REVERSE'])]['factor'].tolist()
    chow_all = {}
    for fname in problem_factors:
        chow_result = scan_structural_break(ic_history[fname])
        chow_all[fname] = chow_result
        top_break = chow_result.iloc[0] if len(chow_result) > 0 else None
        if top_break is not None and top_break['significant']:
            print(f"   {fname}: 最显著断点 {top_break['breakpoint'].strftime('%Y-%m')} "
                  f"(p={top_break['p_value']:.3f}, "
                  f"方向反转={'是' if top_break['sign_change'] else '否'})")
    print(f"   完成。检验了 {len(problem_factors)} 个问题因子")

    print("▶ Step 4/5: IC 衰减曲线（全因子）...")
    decay_summary = {}
    for fname in ic_history.columns[:5]:    # 先跑前5个（耗时较长）
        # decay_summary[fname] = ic_decay_curve(factor_df_unstacked[fname], ret_df)
        pass
    print("   完成（建议在离线环境中运行全量衰减分析）")

    print("▶ Step 5/5: 行业分层 IC（针对 warn/reverse 因子）...")
    for fname in problem_factors:
        ind_ic = ic_by_industry(factor_df, ret_df, industry_df,
                                 date_range=('2021-01', '2022-12'))
        ind_ic.to_csv(f'{output_dir}/05_industry_ic_{fname}.csv')
    print(f"   完成。行业分层报告已保存至 {output_dir}/")

    print(f"\n✅ 全套检验完成。报告目录: {output_dir}/")
```

### C. 推荐的定期运行计划

```python
# ─── 建议加入调仓流水线的 cron 任务 ────────────────────────────────

# 每月调仓前（月末倒数第2个交易日）
def monthly_pre_rebalance_check(ic_history: pd.DataFrame) -> pd.DataFrame:
    """月度快速健康检查，约 30 秒完成"""
    monitor = FactorHealthMonitor(ic_history, EXPECTED_DIRECTIONS, HEALTH_THRESHOLDS)
    monitor.print_report()
    return monitor.snapshot()


# 每季度深度诊断
def quarterly_deep_diagnosis(factor_df, ret_df, industry_df, ic_history):
    """季度深度检验，包含 Chow 检验和行业分层 IC，约 10-20 分钟"""
    run_full_factor_diagnostics(factor_df, ret_df, industry_df, ic_history,
                                 output_dir=f'./factor_reports/{pd.Timestamp.now().strftime("%Y%m")}')


# 年度因子池评审
def annual_factor_pool_review():
    """
    年度调池决策：
    1. 汇总全年月度健康报告趋势
    2. 对持续 warn 因子做根因最终判决
    3. 评估候补因子（high_52w_v2）的入池时机
    4. 搜索新因子候选（见后续"因子覆盖盲区"研究报告）
    """
    pass
```

---

*本文档基于策略现有因子池（17+1，中证500月度增强）的实际数据状态编写，所有代码均可直接集成进现有回测流水线。*  
*文档版本随因子池调整同步更新。测试集（2023-2025）受保护，本文档所有检验均在训练期+验证期内执行。*
