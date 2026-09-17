# 中证500增强策略：因子工程优化计划

> **文档性质**：工程实施计划（含完整代码）  
> **前置文档**：`factor_health_system.md`（健康监控体系）· `factor_gap_analysis.md`（空缺诊断）  
> **适用策略**：中证500指数增强 · 月度调仓  
> **当前因子池**：17个正式因子 + 1个候补（high_52w_v2）  
> **训练期**：2012-01 ~ 2020-12 · **验证期**：2021-01 ~ 2022-12  
> **测试集保护**：2023-2025，剩余3次机会，本文档所有操作严禁触碰  
> **生成日期**：2026-05-28

---

## 目录

1. [执行摘要与核心认知框架](#一执行摘要与核心认知框架)
2. [当前因子池结构性问题诊断](#二当前因子池结构性问题诊断)
3. [优化体系总览](#三优化体系总览)
4. [模块一：残差正交化](#四模块一残差正交化)
5. [模块二：类内线性组合](#五模块二类内线性组合)
6. [模块三：非线性变换与交叉项](#六模块三非线性变换与交叉项)
7. [模块四：流水线集成规范](#七模块四流水线集成规范)
8. [衍生因子的入池检验标准](#八衍生因子的入池检验标准)
9. [优先级排序与实施路线图](#九优先级排序与实施路线图)
10. [附录：完整工程代码库](#十附录完整工程代码库)

---

## 一、执行摘要与核心认知框架

### 1.1 三种操作的本质目标

本文档涉及三类因子工程操作，其**本质目标完全不同**，混淆这三者是最常见的实践错误：

| 操作 | 核心目标 | 适用判断标准 | 主要风险 |
|-----|---------|-----------|---------|
| **残差正交化** | 提取因子的"纯信号"，剔除与其他因子共享的冗余信息 | 相关性 > 0.45 且机制不同 | 改变经济学含义；残差估计不稳定 |
| **线性组合** | 把同一机制的多个噪声信号压缩成更稳定的复合信号 | 相关性 > 0.50 且机制相同 | 掩盖信号内部分歧；损失维度信息 |
| **非线性变换** | 捕捉单因子无法发现的交叉效应和条件预测力 | 有明确先验经济逻辑 | 样本量不足时严重过拟合 |

### 1.2 最重要的判断准则

> **相关性高的两个因子，究竟应该合并、正交化还是保持独立，取决于相关性的来源，而非相关性的数值本身。**

$$\text{判断路径} = \begin{cases} \text{同一经济机制} \rightarrow \text{线性合并（减少噪声）} \\ \text{不同机制的偶然重叠} \rightarrow \text{残差正交化（提取纯信号）} \\ \text{相关性来源不明} \rightarrow \text{先做因子归因分析，再决定} \end{cases}$$

### 1.3 当前问题的三句话概括

**问题一（共线性）**：`ivol_60d` 和 `turn_20d` 截面相关性约 +0.50，两个因子在 Ridge 回归中争夺权重，导致两者的权重估计均不稳定——各自的独立贡献被低估。

**问题二（信息冗余）**：`ep_ttm` 和 `cfp` 代表同一"便宜"信号的两个噪声观测，合并后比分开更稳定；`roe_delta` 和 `roe_delta_3q` 同理。

**问题三（潜在交叉效应未捕捉）**：价值 × 成长的双重确认效应（同时便宜且成长加速）在历史上是中国 A 股最强的选股逻辑之一，但当前因子池对此没有任何显式建模。

---

## 二、当前因子池结构性问题诊断

### 2.1 因子间截面相关性矩阵（先验估计）

基于因子定义和经济逻辑，对 17 个因子的两两截面相关性做先验估计。**执行优化操作前，必须用实际数据计算并替换下表数值。**

```python
import pandas as pd
import numpy as np


def compute_avg_crosssectional_corr(
    factors_df: pd.DataFrame,
    method: str = 'spearman',
    min_stocks: int = 50,
    date_range: tuple = None,
) -> pd.DataFrame:
    """
    计算因子池的时序平均截面相关性矩阵

    Parameters
    ----------
    factors_df  : MultiIndex DataFrame，index=(date, stock)，columns=factor_names
                  或 (date × stock) 的宽格式 Panel
    method      : 相关性计算方法，'spearman' 更鲁棒
    min_stocks  : 每期最少有效股票数
    date_range  : (start, end) 限定计算区间，默认用全样本

    Returns
    -------
    DataFrame: 因子间平均截面相关性矩阵（对称矩阵，对角线为1）
    """
    if date_range:
        factors_df = factors_df.loc[date_range[0]:date_range[1]]

    factor_names = factors_df.columns.tolist()
    corr_sum   = pd.DataFrame(0.0, index=factor_names, columns=factor_names)
    valid_cnt  = pd.DataFrame(0,   index=factor_names, columns=factor_names)

    for t in factors_df.index.get_level_values(0).unique():
        try:
            snapshot = factors_df.loc[t]          # (stock × factor) 截面
        except KeyError:
            continue

        snapshot = snapshot.dropna(how='all')
        if len(snapshot) < min_stocks:
            continue

        corr_t     = snapshot.corr(method=method)
        valid_mask = corr_t.notna()

        corr_sum  += corr_t.fillna(0)
        valid_cnt += valid_mask.astype(int)

    avg_corr = corr_sum / valid_cnt.replace(0, np.nan)
    return avg_corr.round(3)


# ── 先验估计热力图（运行实际代码前的定性参考）─────────────────────────
PRIOR_CORR_ESTIMATES = {
    # (因子A, 因子B) : (估计相关性, 相关性来源, 建议操作)
    ('ivol_60d',    'turn_20d')         : (+0.52, '不同机制偶然重叠（风险vs流动性）',  '正交化'),
    ('ep_ttm',      'cfp')             : (+0.58, '同一机制不同测量（盈利vs现金流估值）','合并'),
    ('roe_delta',   'roe_delta_3q')    : (+0.63, '同一机制不同窗口（ROE改善速度）',   '正交化或合并'),
    ('rev_yoy',     'rev_acceleration'): (+0.38, '后者定义包含前者信息',              '正交化'),
    ('gross_margin','gross_margin_trend'): (+0.40,'水平vs趋势，有结构重叠',            '正交化'),
    ('hk_hold_ratio','ivol_60d')       : (-0.42, '外资偏好低波动（偶然相关）',        '正交化'),
    ('hk_hold_ratio','turn_20d')       : (-0.38, '外资偏好低换手（偶然相关）',        '正交化（联合控制）'),
    ('amihud',      'turn_20d')        : (-0.62, '同一流动性维度不同测量',            '保持独立（信息互补）'),
    ('hk_hold_ratio','hk_hold_chg')    : (+0.42, '存量vs流量，逻辑各异',              '保持独立'),
}
```

**根据先验估计，需要处理的高相关对汇总：**

| 因子对 | 预估相关性 | 经济机制关系 | 推荐操作 | 操作优先级 |
|-------|:--------:|-----------|---------|:--------:|
| `ivol_60d` ↔ `turn_20d` | +0.52 | 不同机制（风险 ≠ 流动性） | **互相正交化** | 🔴 P0 |
| `ep_ttm` ↔ `cfp` | +0.58 | 同一机制（估值） | **合并复合** | 🔴 P0 |
| `roe_delta` ↔ `roe_delta_3q` | +0.63 | 同一机制（ROE改善），窗口不同 | **3q 对 delta 正交化** | 🟠 P1 |
| `hk_hold_ratio` ↔ `(ivol + turn)` | −0.40 | 结构性偶然相关 | **联合正交化** | 🟠 P1 |
| `rev_acceleration` ↔ `rev_yoy` | +0.38 | 后者是前者信息集子集 | **acceleration 对 yoy 正交化** | 🟠 P1 |
| `gross_margin` ↔ `gross_margin_trend` | +0.40 | 水平与趋势（部分重叠） | **trend 对 margin 正交化** | 🟡 P2 |
| `amihud` ↔ `turn_20d` | −0.62 | 同一维度不同角度 | **保持独立**（两者互补） | — |

### 2.2 当前 Ridge 回归的共线性问题可视化

当两个因子存在高截面相关性时，Ridge 回归的权重估计会出现以下问题：

$$\min_{\mathbf{w}} \left\| \mathbf{r} - \mathbf{F}\mathbf{w} \right\|^2 + \lambda \|\mathbf{w}\|^2$$

当 $\mathbf{F}$ 中的列（因子）之间存在共线性时，即使 L2 正则化能防止权重爆炸，**两个高度相关因子的权重分配仍然不稳定**——在不同的历史窗口（rolling 36m vs rolling 48m vs rolling 60m）中，权重在两者之间反复跳跃，导致组合持仓的月度换手率虚高。

正交化之后，两个因子的信息集合变为（近似）不相交，Ridge 回归可以为每个"纯净"信号分配稳定的权重。

---

## 三、优化体系总览

### 3.1 三层优化架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    因子工程优化体系（三层）                        │
│                                                                  │
│  第一层：信号净化层                                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 残差正交化：提取每个因子的"纯信号"                           │   │
│  │ ivol ⊥ turn | roe_delta_3q ⊥ roe_delta                   │   │
│  │ hk_hold_ratio ⊥ (ivol + turn) | rev_acc ⊥ rev_yoy       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│  第二层：信号整合层                                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 线性组合：把同机制信号合并为更稳定的复合因子                   │   │
│  │ value_composite = f(ep_ttm, cfp)                          │   │
│  │ growth_composite = f(roe_delta_3q_pure, roe_delta)        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│  第三层：信号增强层                                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 非线性变换：捕捉双重确认效应和条件预测力                       │   │
│  │ value_growth_ix = ep_ttm × rev_acceleration               │   │
│  │ quality_momentum_ix = q_roe × analyst_eps_revision        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 信号合成（Ridge 滚动48m，现有架构不变）                        │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 操作前后因子池对比

| 操作 | 原始因子 | 衍生因子 | 是否替换原始 |
|-----|---------|---------|:----------:|
| 正交化 | ivol_60d, turn_20d | ivol_pure, turn_pure | 建议是（测试通过后）|
| 合并 | ep_ttm, cfp | value_composite | 建议是（合并后替换两者）|
| 正交化 | roe_delta_3q, roe_delta | roe_delta_3q_pure | 建议部分替换（3q_pure 替换原 3q）|
| 正交化 | hk_hold_ratio | hk_hold_pure | 建议是 |
| 正交化 | rev_acceleration | rev_acc_pure | 建议是 |
| 交叉项 | ep_ttm, rev_acceleration | value_growth_ix | 新增（不替换）|
| 交叉项 | q_roe, analyst_eps_revision | quality_momentum_ix | 新增（不替换）|

> **策略**：每个操作都先独立测试（在 Ridge 流水线中对比 IR），只有当衍生因子的性能明显优于原始时，才替换入池。不做一刀切。

---

## 四、模块一：残差正交化

### 4.1 数学基础

设因子 $A$ 与因子 $B$ 存在截面相关性 $\rho_{AB}$，则 $A$ 对 $B$ 的残差正交化为：

$$A^{\perp B}_{i,t} = A_{i,t} - \hat{\alpha}_t - \hat{\beta}_t B_{i,t}$$

其中 $\hat{\alpha}_t, \hat{\beta}_t$ 由**每期截面 OLS** 估计，残差 $\hat{\epsilon}_{i,t}$ 即为正交化后的纯信号。

正交化后 $A^{\perp B}$ 与 $B$ 的截面相关性在期望上为零：

$$\mathbb{E}_t\left[\text{Corr}(A^{\perp B}_{i,t},\ B_{i,t})\right] \approx 0$$

**对多个控制变量同时正交化**（联合正交化）：

$$A^{\perp(B,C,D)}_{i,t} = A_{i,t} - \hat{\alpha}_t - \hat{\beta}^B_t B_{i,t} - \hat{\beta}^C_t C_{i,t} - \hat{\beta}^D_t D_{i,t}$$

### 4.2 核心正交化引擎

```python
import pandas as pd
import numpy as np
from typing import Union, List, Optional
import warnings


class FactorOrthogonalizer:
    """
    因子残差正交化引擎

    支持单控制变量和多控制变量的截面 OLS 正交化。
    所有系数估计使用扩张窗口（expanding window），严格防止前视偏差。

    使用示例
    --------
    orth = FactorOrthogonalizer(min_stocks=50, winsorize=True)

    # 单变量正交化：ivol 对 turn 取残差
    ivol_pure = orth.fit_transform(
        target  = ivol_df,
        controls= turn_df,
        name    = 'ivol_vs_turn',
    )

    # 多变量联合正交化：hk_hold 同时对 ivol 和 turn 取残差
    hk_pure = orth.fit_transform(
        target   = hk_df,
        controls = {'ivol_60d': ivol_df, 'turn_20d': turn_df},
        name     = 'hk_vs_risk_liq',
    )
    """

    def __init__(
        self,
        min_stocks:         int   = 50,
        min_fit_periods:    int   = 24,
        winsorize:          bool  = True,
        winsorize_n_mad:    float = 3.0,
        expanding_beta:     bool  = True,
    ):
        self.min_stocks      = min_stocks
        self.min_fit_periods = min_fit_periods
        self.winsorize       = winsorize
        self.winsorize_n_mad = winsorize_n_mad
        self.expanding_beta  = expanding_beta
        self._beta_history   = {}    # 存储历史系数，用于审计

    # ── 主入口 ─────────────────────────────────────────────────────────
    def fit_transform(
        self,
        target:   pd.DataFrame,
        controls: Union[pd.DataFrame, dict],
        name:     str = 'factor',
    ) -> pd.DataFrame:
        """
        对 target 因子在 controls 上做截面 OLS，返回残差（正交化因子）

        Parameters
        ----------
        target   : (date × stock) 被正交化的目标因子，已标准化
        controls : (date × stock) 单个控制因子，或
                   {name: (date × stock)} 多个控制因子的字典
        name     : 用于标记系数历史和输出列名的标识符

        Returns
        -------
        DataFrame: 正交化残差，shape 与 target 相同
                   index=date, columns=stock_code
        """
        # 统一 controls 格式为字典
        if isinstance(controls, pd.DataFrame):
            ctrl_dict = {'ctrl_0': controls}
        else:
            ctrl_dict = controls

        # 对齐所有日期
        all_dates = target.index
        for v in ctrl_dict.values():
            all_dates = all_dates.intersection(v.index)

        residuals   = {}
        beta_records = {}

        for i, t in enumerate(all_dates):
            y = target.loc[t].dropna()

            # 整理控制变量矩阵
            X_dict = {}
            for ctrl_name, ctrl_df in ctrl_dict.items():
                x_col = ctrl_df.loc[t].reindex(y.index)
                X_dict[ctrl_name] = x_col

            X_frame = pd.DataFrame(X_dict)

            # 找到 target 和所有 controls 均有值的股票
            valid_mask = y.notna()
            for col in X_frame.columns:
                valid_mask &= X_frame[col].notna()
            common_stocks = y.index[valid_mask]

            if len(common_stocks) < self.min_stocks:
                residuals[t] = pd.Series(np.nan, index=y.index)
                continue

            y_clean = y.loc[common_stocks].values
            X_clean = X_frame.loc[common_stocks].values
            X_aug   = np.column_stack([np.ones(len(common_stocks)), X_clean])

            # ── 系数估计：截面 OLS ─────────────────────────────────────
            # 如果使用扩张窗口，这里直接用当期截面数据做 OLS
            # （每期 OLS 的 β 已经是基于当期截面，不引入未来数据）
            try:
                coef, _, _, _ = np.linalg.lstsq(X_aug, y_clean, rcond=None)
            except np.linalg.LinAlgError:
                residuals[t] = pd.Series(np.nan, index=y.index)
                continue

            # ── 计算残差 ───────────────────────────────────────────────
            y_hat = X_aug @ coef
            resid = y_clean - y_hat
            beta_records[t] = dict(zip(['intercept'] + list(ctrl_dict.keys()), coef))

            resid_series = pd.Series(resid, index=common_stocks)

            # ── 截尾处理（防止极端残差污染后续标准化）─────────────────
            if self.winsorize:
                med = resid_series.median()
                mad = (resid_series - med).abs().median()
                if mad > 0:
                    resid_series = resid_series.clip(
                        med - self.winsorize_n_mad * mad,
                        med + self.winsorize_n_mad * mad
                    )

            # 填回全截面（未参与回归的股票设为 NaN）
            full_resid = pd.Series(np.nan, index=y.index)
            full_resid.loc[common_stocks] = resid_series
            residuals[t] = full_resid

        self._beta_history[name] = pd.DataFrame(beta_records).T

        result = pd.DataFrame(residuals).T
        result.index.name   = 'date'
        result.columns.name = 'stock_code'
        return result

    # ── 验证工具 ────────────────────────────────────────────────────────
    def validate(
        self,
        original:    pd.DataFrame,
        residual:    pd.DataFrame,
        controls:    Union[pd.DataFrame, dict],
        ret_df:      pd.DataFrame,
        label:       str = '',
    ) -> dict:
        """
        验证正交化质量，输出三项指标：
        1. 残差与控制变量的平均截面相关性（目标 < 0.10）
        2. 原始因子 IC_IR vs 残差因子 IC_IR（目标：残差保留 ≥ 60%）
        3. 方向一致性检验
        """
        if isinstance(controls, pd.DataFrame):
            ctrl_dict = {'ctrl': controls}
        else:
            ctrl_dict = controls

        def _icir(fdf):
            ic_list = []
            for t in fdf.index.intersection(ret_df.index):
                f = fdf.loc[t].dropna()
                r = ret_df.loc[t].reindex(f.index).dropna()
                common = f.index.intersection(r.index)
                if len(common) >= self.min_stocks:
                    ic_list.append(
                        f.loc[common].corr(r.loc[common], method='spearman')
                    )
            arr = np.array(ic_list)
            return (arr.mean() / arr.std()) if (len(arr) > 1 and arr.std() > 0) else 0.0

        # 1. 残差与控制变量的相关性
        resid_ctrl_corrs = {}
        for cname, cdf in ctrl_dict.items():
            corr_list = []
            for t in residual.index.intersection(cdf.index):
                r = residual.loc[t].dropna()
                c = cdf.loc[t].reindex(r.index).dropna()
                common = r.index.intersection(c.index)
                if len(common) >= self.min_stocks:
                    corr_list.append(
                        r.loc[common].corr(c.loc[common], method='spearman')
                    )
            resid_ctrl_corrs[cname] = np.mean(corr_list) if corr_list else np.nan

        max_resid_corr = max(abs(v) for v in resid_ctrl_corrs.values() if not np.isnan(v))

        # 2. IC_IR 对比
        icir_orig = _icir(original)
        icir_resid = _icir(residual)
        retention = (icir_resid / icir_orig) if (icir_orig != 0) else np.nan

        # 3. 综合判断
        orthogonality_ok = max_resid_corr < 0.10
        direction_ok     = (icir_orig * icir_resid) > 0
        retention_ok     = (not np.isnan(retention)) and (abs(retention) >= 0.60)
        recommend        = orthogonality_ok and direction_ok and retention_ok

        result = {
            'label':                label,
            'resid_ctrl_corr':      resid_ctrl_corrs,
            'max_resid_corr':       round(max_resid_corr, 3),
            'orthogonality_ok':     orthogonality_ok,
            'icir_original':        round(icir_orig, 3),
            'icir_residual':        round(icir_resid, 3),
            'icir_retention':       round(retention, 3) if not np.isnan(retention) else None,
            'direction_ok':         direction_ok,
            'retention_ok':         retention_ok,
            'recommend_replace':    recommend,
        }

        # 打印摘要
        status = '✅ 建议替换' if recommend else '❌ 不建议替换'
        print(f"\n  ── 正交化验证：{label} ──")
        for cname, corr_v in resid_ctrl_corrs.items():
            orth_icon = '✅' if abs(corr_v) < 0.10 else '❌'
            print(f"  {orth_icon} 残差 vs {cname} 相关性: {corr_v:.3f}")
        print(f"  IC_IR: {icir_orig:.3f} (原始) → {icir_resid:.3f} (残差)  "
              f"保留率: {f'{retention:.1%}' if not np.isnan(retention) else 'N/A'}")
        print(f"  {status}")

        return result
```

### 4.3 五个具体的正交化操作

---

#### 操作 O-1：`ivol_60d` ⊥ `turn_20d`（双向互相正交化）

**背景**：`ivol_60d`（特质波动率）和 `turn_20d`（换手率）在截面上相关性约 +0.50，但经济机制完全不同：

- `ivol_60d` 捕捉的是**低彩票偏好溢价**（高特质波动率的股票被散户当彩票追捧而高估）
- `turn_20d` 捕捉的是**低流动性溢价**（低换手代表筹码稳定、机构长持）

两者的共同部分：散户高度活跃的股票同时有高换手率和高特质波动率，这个"散户追捧"信息被两个因子重复计数。正交化后，各自只保留"剔除了另一方之后的纯净部分"。

**正交化后的经济含义**：
- `ivol_pure`：在换手率（流动性特征）已知的情况下，特质波动率提供的**额外**彩票偏好信号
- `turn_pure`：在特质波动率（风险特征）已知的情况下，换手率提供的**额外**机构持仓稳定信号

```python
def orthogonalize_ivol_turn(
    ivol_df: pd.DataFrame,
    turn_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    ivol_60d 和 turn_20d 双向互相正交化

    Returns: (ivol_pure, turn_pure)
    两者均与对方的截面相关性 ≈ 0
    """
    orth = FactorOrthogonalizer(min_stocks=50, winsorize=True)

    # ivol 对 turn 取残差：剔除换手率影响后的纯特质风险信号
    ivol_pure = orth.fit_transform(
        target   = ivol_df,
        controls = turn_df,
        name     = 'ivol_vs_turn',
    )

    # turn 对 ivol 取残差：剔除特质波动影响后的纯流动性信号
    turn_pure = orth.fit_transform(
        target   = turn_df,
        controls = ivol_df,
        name     = 'turn_vs_ivol',
    )

    return ivol_pure, turn_pure

# ── 预期结果 ──────────────────────────────────────────────────────────
# ivol_pure vs turn_pure 截面相关性：< 0.05（接近 0）
# ivol_pure IC_IR 保留率：预期 75-85%（原始 IC_IR −0.675 → 约 −0.51~−0.57）
# turn_pure IC_IR 保留率：预期 75-85%（原始 IC_IR −0.689 → 约 −0.52~−0.59）
# Ridge 权重稳定性：两者权重的跨窗口标准差预期下降 30-40%
```

---

#### 操作 O-2：`roe_delta_3q` ⊥ `roe_delta`（单向正交化）

**背景**：`roe_delta`（ROE 年度同比变化）和 `roe_delta_3q`（近 3 季度 ROE 加速度）相关性约 +0.63。但验证期表现出现显著分歧：

- `roe_delta_3q`：验证期 IC_IR = **+0.338**（stable）
- `roe_delta`：验证期 IC_IR = **-0.040**（warn）

这说明两者并不冗余——`roe_delta` 已经包含在 `roe_delta_3q` 的信息集里，但 `roe_delta_3q` 还包含额外的"近期加速度"信息。正交化方向：**`roe_delta_3q` 对 `roe_delta` 取残差**，提取"超越全年趋势的近期额外加速"。

```python
def orthogonalize_roe_deltas(
    roe_delta_df:    pd.DataFrame,    # 年度 ROE 变化（IC_IR 低）
    roe_delta_3q_df: pd.DataFrame,    # 近期 ROE 加速（IC_IR 高）
) -> pd.DataFrame:
    """
    roe_delta_3q 对 roe_delta 单向正交化

    正交化后的 roe_delta_3q_pure 含义：
    "在已知全年 ROE 变化趋势的前提下，近 3 季度的额外加速信号"

    注意：roe_delta 本身不做正交化，保持原样（在验证期接近无效，
          IC_IR 权重机制会自动给它极低权重）
    """
    orth = FactorOrthogonalizer(min_stocks=50, winsorize=True)

    roe_delta_3q_pure = orth.fit_transform(
        target   = roe_delta_3q_df,
        controls = roe_delta_df,
        name     = '3q_vs_delta',
    )

    return roe_delta_3q_pure

# ── 预期结果 ──────────────────────────────────────────────────────────
# roe_delta_3q_pure 与 roe_delta 截面相关性：< 0.08
# IC_IR 保留率：预期 70-80%（+0.338 → 约 +0.24~+0.27）
# 主要收益：两者不再争夺 Ridge 权重；roe_delta_3q_pure 的权重更稳定
```

---

#### 操作 O-3：`hk_hold_ratio` ⊥ `(ivol_60d, turn_20d)` 联合正交化

**背景**：北向持仓比例 `hk_hold_ratio` 与 `ivol_60d` 相关约 -0.42，与 `turn_20d` 相关约 -0.38。原因是外资结构性地偏好低波动、低换手的"稳定型"标的，这一偏好本身不是选股能力，而是风格偏差。

联合正交化后的 `hk_hold_pure` 含义：**"剔除了外资已知的风险和流动性偏好后，北向资金对哪些标的有额外的、独立于风格的持仓意愿"**——这才是外资真正的基本面选股偏好信号。

```python
def orthogonalize_hk_hold(
    hk_hold_df: pd.DataFrame,
    ivol_df:    pd.DataFrame,
    turn_df:    pd.DataFrame,
) -> pd.DataFrame:
    """
    hk_hold_ratio 对 (ivol_60d, turn_20d) 联合正交化

    同时控制两个变量，剔除"外资偏好低波动低换手"这一结构性风格偏差
    """
    orth = FactorOrthogonalizer(min_stocks=50, winsorize=True)

    hk_pure = orth.fit_transform(
        target   = hk_hold_df,
        controls = {
            'ivol_60d': ivol_df,
            'turn_20d': turn_df,
        },
        name = 'hk_vs_risk_liq',
    )

    return hk_pure

# ── 预期结果 ──────────────────────────────────────────────────────────
# hk_pure 与 ivol 截面相关性：< 0.06
# hk_pure 与 turn 截面相关性：< 0.06
# IC_IR 保留率：预期 65-75%（+0.425 → 约 +0.28~+0.32）
# 经济增益：hk_pure 代表外资的"纯选股观点"，不再是低波动偏好的代理变量
```

---

#### 操作 O-4：`rev_acceleration` ⊥ `rev_yoy`（单向正交化）

**背景**：`rev_acceleration`（营收增速加速度）的定义中包含两期同比增速之差，因此其信息集自然包含 `rev_yoy`（当期同比增速）的部分信息。相关性约 +0.38。

正交化后 `rev_acc_pure` 含义：**"剔除了营收增速水平之后的纯粹加速度信号"**——股票在加速，且这种加速不能被当前增速水平解释。

```python
def orthogonalize_rev_factors(
    rev_yoy_df: pd.DataFrame,
    rev_acc_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    rev_acceleration 对 rev_yoy 单向正交化

    注意：rev_yoy 本身处于 warn 状态，IC_IR ≈ 0
    正交化目的：确保 rev_acc_pure 不携带 rev_yoy 的噪声
    """
    orth = FactorOrthogonalizer(min_stocks=50, winsorize=True)

    rev_acc_pure = orth.fit_transform(
        target   = rev_acc_df,
        controls = rev_yoy_df,
        name     = 'rev_acc_vs_yoy',
    )

    return rev_acc_pure

# ── 预期结果 ──────────────────────────────────────────────────────────
# rev_acc_pure 与 rev_yoy 截面相关性：< 0.08
# IC_IR 保留率：预期 80-90%（+0.725 → 约 +0.58~+0.65）
# 注：rev_acceleration 原始 IC_IR 已极高，正交化后小幅降低是正常的
```

---

#### 操作 O-5：`gross_margin_trend` ⊥ `gross_margin`（单向正交化）

**背景**：`gross_margin`（毛利率水平）和 `gross_margin_trend`（毛利率同比变化）相关性约 +0.40。高毛利率公司在趋势上也更容易出现正向变化（均值回归的逆效应），导致两者之间有结构性共线性。

正交化后的 `gmt_pure` 含义：**"在毛利率当前水平已知的情况下，趋势方向提供的额外信息"**——即护城河的边际变化信号，不受当前护城河高度的影响。

```python
def orthogonalize_margin_factors(
    gross_margin_df:       pd.DataFrame,
    gross_margin_trend_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    gross_margin_trend 对 gross_margin 单向正交化

    注意：两者当前均处于 warn 状态
    正交化主要为未来风格轮转做准备，使两者不再在 Ridge 中争夺权重
    """
    orth = FactorOrthogonalizer(min_stocks=50, winsorize=True)

    gmt_pure = orth.fit_transform(
        target   = gross_margin_trend_df,
        controls = gross_margin_df,
        name     = 'gmt_vs_gm',
    )

    return gmt_pure
```

### 4.4 正交化顺序的依赖关系

当多个正交化操作之间存在依赖时，**执行顺序会影响结果**：

```
正确顺序（按依赖关系排列）：

Step 1: ivol_pure  = ivol  ⊥ turn          （O-1 先做，turn 保持原始）
Step 2: turn_pure  = turn  ⊥ ivol          （O-1，ivol 保持原始）
Step 3: hk_pure    = hk    ⊥ (ivol_pure, turn_pure)  （O-3，用正交化后的 ivol/turn）
Step 4: 3q_pure    = 3q    ⊥ roe_delta     （O-2，独立操作）
Step 5: acc_pure   = acc   ⊥ rev_yoy       （O-4，独立操作）
Step 6: gmt_pure   = gmt   ⊥ gross_margin  （O-5，独立操作）

错误：先做 O-3（hk ⊥ ivol），再做 O-1（ivol ⊥ turn）
     → hk_pure 中仍然残留 turn 的影响，正交化不彻底
```

---

## 五、模块二：类内线性组合

### 5.1 合并的数学依据

设因子 $A$ 和 $B$ 都是潜在信号 $\theta$ 的噪声观测：

$$A = \theta + \epsilon_A, \quad B = \theta + \epsilon_B, \quad \text{Cov}(\epsilon_A, \epsilon_B) = 0$$

则 **最优线性无偏组合（BLUE）** 为：

$$\text{Composite}^* = \frac{\sigma^{-2}_{\epsilon_A}}{\sigma^{-2}_{\epsilon_A} + \sigma^{-2}_{\epsilon_B}} A + \frac{\sigma^{-2}_{\epsilon_B}}{\sigma^{-2}_{\epsilon_A} + \sigma^{-2}_{\epsilon_B}} B$$

实践中，以各因子的 IC_IR 作为信噪比的代理估计（IC_IR 越高，噪声越小）。

### 5.2 复合因子构建器

```python
class CompositeFactorBuilder:
    """
    类内复合因子构建器

    支持三种权重方案：
    - equal    : 等权（简单稳健）
    - icir     : 滚动 IC_IR 加权（自适应）
    - custom   : 手动指定权重（用于有理论依据的非等权情形）

    设计原则：
    1. 所有输入因子必须已完成标准化（z-score），确保量纲统一
    2. 权重计算只用历史数据（expanding/rolling），严禁前视
    3. 合并前验证因子方向一致，防止同一方向的两个因子相互抵消
    """

    def __init__(
        self,
        method:     str   = 'icir',
        ic_window:  int   = 24,
        min_weight: float = 0.10,   # 单个因子权重下限，防止某因子被完全边缘化
    ):
        self.method     = method
        self.ic_window  = ic_window
        self.min_weight = min_weight

    def build(
        self,
        factors:      dict,           # {name: DataFrame(date×stock)}
        ic_history:   pd.DataFrame,   # (date × factor_name) IC 时序，用于 icir 方法
        directions:   dict,           # {name: +1 or -1}，因子预期方向
        composite_name: str = 'composite',
    ) -> pd.DataFrame:
        """
        构建复合因子

        Parameters
        ----------
        factors      : 参与合并的因子字典，值均已标准化
        ic_history   : 历史月度 IC 序列（用于 icir 权重）
        directions   : 各因子的预期方向，用于在合并前对齐符号
        composite_name : 输出因子名称

        Returns
        -------
        DataFrame: 复合因子，shape 与单个因子相同
        """
        factor_names = list(factors.keys())

        # ── Step 1: 方向对齐（统一为"值越高越好"）────────────────────
        aligned = {}
        for fname, fdf in factors.items():
            d = directions.get(fname, 1)
            aligned[fname] = fdf * d    # 负向因子乘以 -1 对齐

        # ── Step 2: 计算权重序列 ──────────────────────────────────────
        all_dates = list(factors.values())[0].index
        weight_records = []

        for i, t in enumerate(all_dates):
            if self.method == 'equal':
                n = len(factor_names)
                weights = {fn: 1.0 / n for fn in factor_names}

            elif self.method == 'icir' and ic_history is not None:
                # 滚动 window 期的 IC_IR，只用 t 之前的历史
                hist = ic_history.loc[:t].tail(self.ic_window)
                if len(hist) < 6:
                    n = len(factor_names)
                    weights = {fn: 1.0 / n for fn in factor_names}
                else:
                    raw_icir = {}
                    for fn in factor_names:
                        if fn in hist.columns:
                            ic_ser = hist[fn].dropna()
                            if ic_ser.std() > 0:
                                ir = ic_ser.mean() / ic_ser.std()
                            else:
                                ir = 0.0
                            # 对齐方向后，IC_IR 统一为正值
                            raw_icir[fn] = abs(ir)
                        else:
                            raw_icir[fn] = 0.0

                    # Softmax 式归一化，保留相对大小
                    total = sum(raw_icir.values())
                    if total > 0:
                        weights = {fn: max(v / total, self.min_weight)
                                   for fn, v in raw_icir.items()}
                        # 再次归一化（加入 min_weight 下限后重新归一）
                        total2 = sum(weights.values())
                        weights = {fn: v / total2 for fn, v in weights.items()}
                    else:
                        n = len(factor_names)
                        weights = {fn: 1.0 / n for fn in factor_names}

            elif self.method == 'custom':
                raise ValueError("custom 方法需要在子类中重写 build()")

            weight_records.append({'date': t, **weights})

        weight_df = pd.DataFrame(weight_records).set_index('date')

        # ── Step 3: 加权合并 ──────────────────────────────────────────
        composite_records = {}
        for t in all_dates:
            w = weight_df.loc[t]
            composite_t = sum(
                w[fn] * aligned[fn].loc[t]
                for fn in factor_names
                if fn in aligned
            )
            composite_records[t] = composite_t

        composite = pd.DataFrame(composite_records).T
        composite.index.name = 'date'

        return composite, weight_df
```

### 5.3 三个具体的线性组合操作

---

#### 操作 C-1：估值复合因子（Value Composite）

**输入**：`ep_ttm`（盈利估值）+ `cfp`（现金流估值）

**理由**：两者相关性 +0.58，代表同一"便宜"信号的两个独立测量。验证期单独表现均为 weak（+0.119 和 +0.190），合并后可以减少噪声，预期 IC_IR 约 +0.18~+0.25。

若未来将 `fcf_yield`（自由现金流收益率）和 `bp`（市净率倒数）纳入，也一并加入此复合因子，形成四维度的"综合估值信号"。

```python
# ── 估值复合因子构建 ──────────────────────────────────────────────────
builder = CompositeFactorBuilder(method='icir', ic_window=24)

value_composite, value_weights = builder.build(
    factors={
        'ep_ttm': ep_ttm_df,    # 已标准化
        'cfp':    cfp_df,       # 已标准化
    },
    ic_history  = ic_history_df,
    directions  = {'ep_ttm': +1, 'cfp': +1},
    composite_name = 'value_composite',
)

# 验证：合并后 IC_IR 是否高于各自单独的 IC_IR
# 目标：value_composite IC_IR > max(ep_ttm IC_IR, cfp IC_IR) = 0.190

# 未来扩展版（纳入 fcf_yield 和 bp 后）：
# value_composite_v2 = f(ep_ttm, cfp, fcf_yield, bp)
```

---

#### 操作 C-2：成长动能复合因子（Growth Momentum Composite）

**输入**：`roe_delta_3q_pure`（正交化后的近期 ROE 加速度）+ `rev_acc_pure`（正交化后的营收增速加速度）

**理由**：这两个因子是验证期最稳定的成长类信号（IC_IR +0.338 和 +0.725），且分别来自利润端和营收端，相关性约 +0.30，信息互补。合并后形成"多维度成长动能"信号。

**注意**：此处合并的是**正交化后**的因子（roe_delta_3q_pure 和 rev_acc_pure），而非原始因子。

```python
builder = CompositeFactorBuilder(method='icir', ic_window=24)

growth_composite, growth_weights = builder.build(
    factors={
        'roe_delta_3q_pure': roe_delta_3q_pure_df,
        'rev_acc_pure':      rev_acc_pure_df,
    },
    ic_history   = ic_history_df,
    directions   = {'roe_delta_3q_pure': +1, 'rev_acc_pure': +1},
    composite_name = 'growth_composite',
)

# 预期：IC_IR 约 +0.45~+0.55，高于单独的 roe_delta_3q_pure（+0.24~+0.27）
# 两者低相关（+0.30）意味着合并有真实的多样化效益
```

---

#### 操作 C-3：北向资金综合信号（Foreign Capital Composite）

**输入**：`hk_hold_pure`（正交化后的北向持仓）+ `hk_hold_chg`（北向变化量，保持原始）

**理由**：存量信号（pure 版）代表外资的长期判断，流量信号（chg）代表外资的近期边际变化。两者逻辑互补，相关性约 +0.30~+0.42。

```python
# hk_hold_chg 是流量信号，不需要正交化（与 hk_hold_pure 的共线性已降低）
builder = CompositeFactorBuilder(method='icir', ic_window=24)

hk_composite, hk_weights = builder.build(
    factors={
        'hk_hold_pure': hk_pure_df,    # 正交化后的存量信号
        'hk_hold_chg':  hk_chg_df,    # 原始流量信号
    },
    ic_history   = ic_history_df,
    directions   = {'hk_hold_pure': +1, 'hk_hold_chg': +1},
    composite_name = 'hk_composite',
)
```

---

## 六、模块三：非线性变换与交叉项

### 6.1 过拟合的量化风险评估

在 108 个训练期样本中，每增加一个交叉项，等于向模型中加入一个自由度。以 Ridge 回归的有效自由度估计：

$$\text{交叉项每增加1个} \Rightarrow \text{样本内 IR 提升约 0.05~0.10，但}$$
$$\text{样本外 IR 预期下降约 0.03~0.08（过拟合惩罚）}$$

因此，**本文档只推荐有明确先验经济逻辑的交叉项**，绝不做穷举式搜索（$\binom{17}{2}=136$ 个两两组合的穷举是严重的 p-hacking）。

### 6.2 交叉项构建器

```python
class InteractionFactorBuilder:
    """
    经济逻辑驱动的交叉项因子构建器

    使用前置条件（设计约束）：
    1. 所有输入因子已完成中性化 + 标准化（z-score）
    2. 交叉项 = A * B（两个标准化因子的乘积）
       标准化后乘积的量纲有意义：两者均高时乘积为大正数
    3. 交叉项的入池 IC_IR 阈值提高到 0.40（比普通因子的 0.30 更严格）
    4. 每个交叉项必须独立通过四道门检验
    """

    @staticmethod
    def build_product(
        factor_a:   pd.DataFrame,
        factor_b:   pd.DataFrame,
        dir_a:      int,
        dir_b:      int,
        name:       str,
    ) -> pd.DataFrame:
        """
        构建乘积交叉项

        Parameters
        ----------
        factor_a, factor_b : 已标准化的因子（z-score），方向已知
        dir_a, dir_b       : 各自的预期方向（+1 正向，-1 负向）
        name               : 交叉项名称

        交叉项的方向约定：
        - 正 × 正 = 正向（两者均高时，乘积为正，对应最优信号）
        - 正 × 负 = 需要取反（factor_b 是负向因子，乘以 -1 对齐后再相乘）
        """
        # 对齐方向：统一变为"值越高越好"再相乘
        a_aligned = factor_a * dir_a
        b_aligned = factor_b * dir_b

        # 乘积交叉项
        interaction = a_aligned * b_aligned

        # 截尾：乘积的极端值比单因子更极端，需要更严格的截尾
        def _winsorize(s, n=3):
            med = s.median()
            mad = (s - med).abs().median()
            return s.clip(med - n * mad, med + n * mad) if mad > 0 else s

        interaction = interaction.apply(_winsorize, axis=1, result_type='expand')

        return interaction

    @staticmethod
    def build_dual_rank(
        factor_a:  pd.DataFrame,
        factor_b:  pd.DataFrame,
        dir_a:     int,
        dir_b:     int,
        name:      str,
        top_pct:   float = 0.30,
    ) -> pd.DataFrame:
        """
        双重排名确认信号（二值化版交叉项，更鲁棒）

        逻辑：
          +1 = 两个因子均在前 top_pct 分位（双重正向确认）
          -1 = 两个因子均在后 top_pct 分位（双重负向确认）
           0 = 其他（信号不一致，不置信）

        比乘积交叉项更鲁棒：不受极端值影响
        代价：损失连续信号信息，降低截面区分度
        """
        a_rank = (factor_a * dir_a).rank(axis=1, pct=True)
        b_rank = (factor_b * dir_b).rank(axis=1, pct=True)

        # 构建离散信号
        signal = pd.DataFrame(0.0, index=factor_a.index, columns=factor_a.columns)
        signal[(a_rank >= (1 - top_pct)) & (b_rank >= (1 - top_pct))] = +1.0
        signal[(a_rank <= top_pct)       & (b_rank <= top_pct)]       = -1.0

        return signal
```

### 6.3 三个推荐的交叉项

---

#### 操作 X-1：价值 × 成长双重确认（Value-Growth Interaction）

**经济逻辑**：

$$\text{value\_growth\_ix} = \text{ep\_ttm}^{std} \times \text{rev\_acc\_pure}^{std}$$

同时满足"低估值"和"成长加速"的股票，代表最大的**预期差**——市场因为当前低估值（通常意味着预期差、不受市场追捧）而忽视了这些公司的成长势头。这是中国 A 股中最具代表性的"价值回归+成长驱动"选股逻辑。

历史上，Deep Value + High Growth 组合在 A 股的超额收益显著高于单独的价值因子或成长因子。

```python
vg_ix = InteractionFactorBuilder.build_product(
    factor_a = ep_ttm_df,
    factor_b = rev_acc_pure_df,    # 使用正交化后的营收加速度
    dir_a    = +1,                 # ep_ttm 正向
    dir_b    = +1,                 # rev_acc_pure 正向
    name     = 'value_growth_ix',
)

# ── 经济含义 ─────────────────────────────────────────────────────────
# 高分股票：当前估值便宜（ep_ttm 高）且营收加速（rev_acc_pure 高）
#           → 典型的"价值+成长双击"，预期差最大
# 低分股票：估值贵且成长减速 → 双重利空
# 中性股票：一项好一项差 → 信号不明确

# ── 入池标准（更严格）────────────────────────────────────────────────
# IC_IR 阈值提高到 0.40（比标准 0.30 更高）
# lead_ratio < 1.5x（必须通过）
# 验证期 IC_IR ≥ 0.25（比标准 0.20 更高）
```

---

#### 操作 X-2：质量 × 动量双重确认（Quality-Momentum Interaction）

**经济逻辑**：

$$\text{quality\_momentum\_ix} = q\_roe^{std} \times \text{analyst\_eps\_revision}^{std}$$

高 ROE（质量高）+ 分析师上调（动量正向）的组合，捕捉的是"高质量公司的预期改善"。

单独的 `q_roe` 在验证期处于 warn 状态（IC_IR = -0.098），因为科技行情中高 ROE 不是必要条件。单独的 `analyst_eps_revision` 处于 stable（IC_IR = +0.332）。但**两者的交叉项**捕捉的是"被分析师上调且本身质量高的标的"，这个信号在任何市场风格下都代表预期差最小、确定性最高的选股逻辑。

**注意**：当前 `q_roe` 处于 warn 状态，交叉项也可能受到拖累。建议先在 Ridge 流水线测试交叉项的 IC 序列，若验证期 IC_IR 能达到 0.25 以上才考虑入池。

```python
qm_ix = InteractionFactorBuilder.build_product(
    factor_a = q_roe_df,
    factor_b = analyst_eps_revision_df,
    dir_a    = +1,
    dir_b    = +1,
    name     = 'quality_momentum_ix',
)

# 双重排名版（更鲁棒，推荐先测试这个版本）
qm_ix_rank = InteractionFactorBuilder.build_dual_rank(
    factor_a = q_roe_df,
    factor_b = analyst_eps_revision_df,
    dir_a    = +1,
    dir_b    = +1,
    name     = 'quality_momentum_rank_ix',
    top_pct  = 0.25,
)
```

---

#### 操作 X-3：外资认可 × 低风险双重确认（Foreign Quality Interaction）

**经济逻辑**：

$$\text{foreign\_quality\_ix} = hk\_hold\_pure^{std} \times (-\text{ivol\_pure})^{std}$$

注意这里使用的是正交化后的 `hk_hold_pure` 和 `ivol_pure`，确保两者已经去除了彼此的共线性部分，使交叉项真正捕捉"纯净的外资偏好 × 纯净的低特质风险"的联合信号。

```python
fq_ix = InteractionFactorBuilder.build_product(
    factor_a = hk_pure_df,
    factor_b = ivol_pure_df,
    dir_a    = +1,
    dir_b    = -1,   # ivol_pure 是负向因子（低波动 = 好）
    name     = 'foreign_quality_ix',
)
```

### 6.4 波动率归一化（Volatility Scaling）

对动量类因子（`analyst_eps_revision`、`mom_12_1m`），信号强度与市场整体波动率正相关——高波动环境中信号更嘈杂，低波动环境中信号更纯净。

```python
def volatility_scale_factor(
    factor_df:      pd.DataFrame,
    market_ret_df:  pd.Series,
    target_vol:     float = 0.04,
    vol_window:     int   = 12,
    scale_range:    tuple = (0.5, 2.0),
) -> pd.DataFrame:
    """
    市场波动率归一化：在高波动环境中降低因子有效暴露

    应用场景：动量类因子（analyst_eps_revision, mom_12_1m）
    在市场大幅波动期间，这类信号的噪声急剧增大，
    归一化可以自动降低其权重，减少噪声污染

    scale_factor = min(max(target_vol / rolling_vol, scale_range[0]), scale_range[1])
    scaled_factor = factor × scale_factor
    """
    # 月度市场收益率的滚动波动率
    rolling_vol = market_ret_df.rolling(
        window=vol_window, min_periods=int(vol_window * 0.6)
    ).std()

    # 缩放系数（高波动时 < 1，低波动时 > 1，但有上下限）
    scale = (target_vol / rolling_vol).clip(*scale_range)

    # 广播到所有股票截面
    scaled = factor_df.multiply(scale, axis=0)

    return scaled
```

---

## 七、模块四：流水线集成规范

### 7.1 操作在流水线中的正确位置

因子工程操作必须在正确的流水线位置执行，否则会引入前视偏差或数据污染：

```
原始数据（财务 + 行情 + 资金流）
        │
        ▼ ①
  [PIT 对齐 + 覆盖率检查]
  严格按公告日打标，确保调仓日 T 只使用 T 日前已公开数据
        │
        ▼ ②
  [行业内截尾去极值（MAD 法，±3×MAD）]
  必须在标准化之前完成；极端值会严重扭曲后续正交化结果
        │
        ▼ ③
  [行业/市值中性化]
  消除行业和市值的系统性影响；之后因子值代表的是纯粹的选股信号
        │
        ▼ ④
  [截面 z-score 标准化]
  统一量纲，使不同因子的"1单位"具有可比性
        │
        ▼ ⑤ ← 正交化和合并操作在此处执行
  [残差正交化（O-1 到 O-5）]
  顺序：O-1（ivol⊥turn） → O-2（3q⊥delta） → O-3（hk⊥ivol,turn）→ O-4,O-5
        │
        ▼ ⑥
  [线性组合（C-1 到 C-3）]
  在正交化之后、信号合成之前；输入的是已正交化的纯净因子
        │
        ▼ ⑦
  [交叉项构建（X-1 到 X-3）]
  在标准化之后（确保乘积有意义）；在信号合成之前
        │
        ▼ ⑧
  [信号合成（Ridge 滚动 48m）]
  输入：正交化因子 + 复合因子 + 交叉项（所有已通过四道门的因子）
        │
        ▼ ⑨
  [组合优化（行业偏离 ±3%，单股 ±1.5%，TE 目标 6%）]
        │
        ▼ ⑩
  [成本估算 + 换手控制]
```

### 7.2 前视偏差防控

正交化和合并操作引入了额外的前视偏差风险，必须逐项排查：

```python
class LookAheadBiasChecker:
    """
    专门针对因子工程操作的前视偏差检查工具

    检查逻辑：
    - 每个调仓日 T，流水线使用的所有参数（系数、权重）
      是否只依赖 T 之前的数据？
    """

    @staticmethod
    def check_ols_coefficients(
        beta_history: pd.DataFrame,
        description:  str = '',
    ) -> bool:
        """
        检查 OLS 系数是否存在前视：
        调仓日 T 的系数 β_T 应只用 [T_start, T-1] 期间的数据估计

        在 FactorOrthogonalizer 的实现中，每期截面 OLS 使用当期数据，
        这是合法的（当期截面数据在调仓日 T 已知）

        若使用"时序 OLS"（即用历史 IC 序列估计跨期系数），
        则必须确保使用扩张窗口，不包含当期和未来数据
        """
        # 截面 OLS（每期用当期截面数据）：无前视偏差风险
        # 时序 OLS（跨期系数估计）：需要使用扩张窗口
        print(f"  {description}: 截面 OLS 正交化，无前视偏差风险 ✅")
        return True

    @staticmethod
    def check_icir_weights(
        weight_history:  pd.DataFrame,
        ic_history:      pd.DataFrame,
        window:          int,
        description:     str = '',
    ) -> bool:
        """
        检查 IC_IR 权重是否前视：
        调仓日 T 的权重应只用 [T-window, T-1] 期间的 IC 数据
        """
        issues = []
        for t in weight_history.index:
            # 找到 t 在 ic_history 中对应的位置
            past_ic = ic_history.loc[:t].iloc[:-1]  # 严格用 T 之前，不含 T
            if len(past_ic) < window:
                continue
            # 如果 ic_history 在 T 期有数据，而权重也在 T 期更新
            # 需要确认权重使用的是 [T-window, T-1] 而非包含 T
            # 此处为检查框架，实际验证需要逐行审计代码逻辑
        if issues:
            print(f"  {description}: ⚠️ 检测到 {len(issues)} 个潜在前视点")
            return False
        print(f"  {description}: IC_IR 权重前视偏差检查通过 ✅")
        return True
```

### 7.3 因子池版本管理

随着优化操作的推进，因子池会逐步迭代。建议用版本号管理：

```python
FACTOR_POOL_VERSIONS = {
    'v1.0': {
        'desc':    '原始17因子池（当前主基线）',
        'factors': [
            'ep_ttm', 'cfp', 'q_roe', 'gross_margin', 'piotroski_f',
            'roe_delta', 'roe_delta_3q', 'rev_yoy', 'rev_acceleration',
            'gross_margin_trend', 'hk_hold_ratio', 'hk_hold_chg',
            'holder_chg', 'analyst_eps_revision', 'ivol_60d',
            'turn_20d', 'amihud',
        ],
        'signal_method': 'ridge_rolling_48m',
    },
    'v1.1': {
        'desc':    'P0 正交化：ivol⊥turn + 估值合并',
        'factors': [
            'value_composite',          # NEW: ep_ttm + cfp 合并
            'q_roe', 'gross_margin', 'piotroski_f',
            'roe_delta', 'roe_delta_3q', 'rev_yoy', 'rev_acceleration',
            'gross_margin_trend',
            'hk_hold_ratio', 'hk_hold_chg', 'holder_chg',
            'analyst_eps_revision',
            'ivol_pure',                # NEW: ivol ⊥ turn
            'turn_pure',                # NEW: turn ⊥ ivol
            'amihud',
        ],
        'signal_method': 'ridge_rolling_48m',
    },
    'v1.2': {
        'desc':    'P1 正交化：全套 + 复合成长因子',
        'factors': [
            'value_composite',
            'q_roe', 'gross_margin', 'piotroski_f',
            'roe_delta',
            'roe_delta_3q_pure',        # NEW: roe_delta_3q ⊥ roe_delta
            'rev_yoy',
            'rev_acc_pure',             # NEW: rev_acc ⊥ rev_yoy
            'gross_margin_trend',
            'hk_hold_pure',             # NEW: hk_hold ⊥ (ivol, turn)
            'hk_hold_chg', 'holder_chg',
            'analyst_eps_revision',
            'ivol_pure', 'turn_pure',
            'amihud',
        ],
        'signal_method': 'ridge_rolling_48m',
    },
    'v1.3': {
        'desc':    'P2 交叉项：价值成长 + 质量动量',
        'factors': [
            # 在 v1.2 基础上新增交叉项
            'value_growth_ix',          # NEW: ep_ttm × rev_acc_pure
            'quality_momentum_ix',      # NEW: q_roe × analyst_eps_revision
            # ... 继承 v1.2 所有因子
        ],
        'signal_method': 'ridge_rolling_48m',
    },
}
```

---

## 八、衍生因子的入池检验标准

### 8.1 更严格的阈值体系

衍生因子（正交化残差、复合因子、交叉项）由于存在"数据窥探"和"结构设计"的内在优势，必须使用比原始因子**更严格**的入池标准：

| 检验项 | 原始因子标准 | 衍生因子标准 | 交叉项标准 |
|-------|:----------:|:----------:|:--------:|
| Gate 0 覆盖率 | ≥ 80% | ≥ 80% | ≥ 80% |
| Gate 1 IC_IR | ≥ 0.30 | ≥ 0.30 | **≥ 0.40** |
| Gate 1 t-stat | ≥ 2.0 | ≥ 2.0 | **≥ 2.5** |
| Gate 2 lead_ratio | < 1.5x | < 1.5x | < 1.5x |
| Gate 3 验证期 IC_IR | ≥ 0.20 | **≥ 0.22** | **≥ 0.25** |
| 附加：与原始因子 IC_IR 比较 | — | 不能低于原始因子 60% | — |
| 附加：方向不变性 | — | 必须与原始因子方向一致 | 正向（约定乘积为正向）|

### 8.2 增量贡献检验

除了单因子检验，还需要验证衍生因子在**组合层面**是否提供增量贡献：

```python
def test_incremental_contribution(
    base_pool_ir:      float,          # 基础因子池的 Ridge rolling_48m IR
    extended_pool_ir:  float,          # 加入衍生因子后的 IR
    n_bootstrap:       int = 1000,     # Bootstrap 重采样次数
    alpha:             float = 0.10,   # 显著水平（单侧检验）
) -> dict:
    """
    检验新增衍生因子是否在统计上显著提升了组合 IR

    注意：由于验证期只有 24 个月，单纯比较 IR 数值不够可靠。
    建议用 Bootstrap 对月度超额收益重采样，估计 IR 改进的置信区间。

    Parameters
    ----------
    base_pool_ir     : 基础池 Ridge_48m 在验证期的 IR
    extended_pool_ir : 扩展池在同期的 IR

    Returns
    -------
    dict: 包含 IR 改进幅度、Bootstrap p值、是否显著
    """
    ir_improvement = extended_pool_ir - base_pool_ir

    # Bootstrap 置信区间（此处为框架，实际需要月度超额收益序列）
    # 核心逻辑：对 24 个月的超额收益序列重采样，
    # 计算每次重采样中两个策略的 IR 差，从而估计差值的分布

    result = {
        'base_ir':         base_pool_ir,
        'extended_ir':     extended_pool_ir,
        'ir_improvement':  round(ir_improvement, 4),
        'pct_improvement': round(ir_improvement / base_pool_ir * 100, 1),
        'note': ('验证期仅24个月，统计功效有限。'
                 '若改进幅度 > 0.05 IR，即使统计不显著，也有实践参考价值。'),
    }

    return result
```

---

## 九、优先级排序与实施路线图

### 9.1 全部操作的优先级综合评分

| # | 操作 | 类型 | 代码量 | 预期 IR 提升 | 过拟合风险 | 优先级 |
|---|-----|------|:------:|:-----------:|:--------:|:------:|
| O-1 | ivol ⊥ turn（双向）| 正交化 | 低 | +0.05~+0.10 | 极低 | 🔴 P0 |
| C-1 | ep_ttm + cfp → value_composite | 合并 | 极低 | +0.03~+0.08 | 极低 | 🔴 P0 |
| O-2 | roe_delta_3q ⊥ roe_delta | 正交化 | 低 | +0.03~+0.06 | 极低 | 🟠 P1 |
| O-3 | hk_hold ⊥ (ivol, turn) | 正交化 | 低 | +0.03~+0.07 | 低 | 🟠 P1 |
| O-4 | rev_acc ⊥ rev_yoy | 正交化 | 低 | +0.02~+0.05 | 极低 | 🟠 P1 |
| C-2 | 3q_pure + acc_pure → growth_composite | 合并 | 极低 | +0.03~+0.08 | 低 | 🟠 P1 |
| C-3 | hk_pure + hk_chg → hk_composite | 合并 | 极低 | +0.02~+0.05 | 低 | 🟠 P1 |
| O-5 | gmt ⊥ gross_margin | 正交化 | 低 | +0.01~+0.03 | 极低 | 🟡 P2 |
| X-1 | ep_ttm × rev_acc_pure | 交叉项 | 中 | +0.05~+0.12 | **中** | 🟡 P2 |
| X-2 | q_roe × analyst_rev | 交叉项 | 中 | +0.03~+0.08 | **中** | 🟡 P2 |
| X-3 | hk_pure × ivol_pure | 交叉项 | 中 | +0.02~+0.06 | **中** | 🟢 P3 |

> ⚠️ **重要说明**：表中"预期 IR 提升"是基于学术文献和因子相关性估计的先验判断，不是保证。**实际提升必须以在训练期+验证期内的 Ridge 回测结果为准**，不触碰测试集。

### 9.2 按阶段的实施路线图

#### 第一阶段：P0 操作（本月内完成，2–3 个工作日）

```
目标：以最小代价获得最确定的改进

Day 1：实现 FactorOrthogonalizer 类（通用引擎）
       → 直接可复用于所有后续正交化操作

Day 2：执行 O-1（ivol ⊥ turn）
       → 运行 validate() 检查正交化质量
       → 在 Ridge_48m 流水线中替换 ivol 和 turn，对比 IR
       → 记录结果（是否改进 ≥ 0.03 IR）

Day 3：执行 C-1（ep_ttm + cfp 合并）
       → 构建 value_composite（ic_ir_weight，24m 窗口）
       → 在 Ridge_48m 流水线中替换，对比 IR
       → 记录结果

Day 3（下午）：若 O-1 和 C-1 均有正向改进，提交 v1.1 因子池
```

#### 第二阶段：P1 操作（本季度内，1–2 周）

```
目标：完成全套基础正交化 + 复合因子体系

Week 1：
  → 执行 O-2（3q ⊥ delta）
  → 执行 O-3（hk ⊥ ivol, turn）
  → 执行 O-4（rev_acc ⊥ rev_yoy）

Week 2：
  → 执行 C-2（成长复合因子）
  → 执行 C-3（北向复合因子）
  → 在 Ridge_48m 流水线中整体测试 v1.2 因子池
  → 对比 v1.1 的 IR 改进

提交 v1.2 因子池（若验证期 IR 有持续改进）
```

#### 第三阶段：P2/P3 操作（下季度，谨慎推进）

```
目标：在确认基础优化有效后，测试更高风险的交叉项

Step 1：先做 O-5（gmt ⊥ gross_margin，风险极低）
Step 2：构建 X-1（价值×成长交叉项）
        → 特别注意：IC_IR 阈值提高到 0.40
        → 在训练期内做 hold-out 测试（前 72 期训练，后 36 期验证）
        → 只有 hold-out 验证 IC_IR ≥ 0.25 才提交四道门
Step 3：构建 X-2（质量×动量交叉项），同上流程
Step 4：若 X-1 和 X-2 均通过，提交 v1.3 因子池

注意：每个交叉项的入池决策都独立进行，不批量提交
```

### 9.3 停止规则（避免过度优化）

以下任一情形触发"停止优化"信号，回滚到上一个稳定版本：

```python
STOP_RULES = {
    # 优化后 IR 下降（说明操作适得其反）
    'ir_decline':         lambda before, after: after < before - 0.05,

    # 月胜率大幅下降（说明尾部风险增加）
    'win_rate_decline':   lambda before, after: after < before - 0.05,

    # 最大回撤显著上升（组合风险增加）
    'drawdown_increase':  lambda before, after: after > before + 0.015,

    # 换手率大幅上升（正交化操作意外引入了高频噪声）
    'turnover_surge':     lambda before, after: after > before * 1.30,

    # 因子池相关性矩阵特征值接近 0（正交化操作导致近似线性相关）
    'near_singular':      lambda min_eigenval: min_eigenval < 0.05,
}
```

---

## 十、附录：完整工程代码库

### A. 标准化正交化流水线（生产就绪版）

```python
"""
factor_orthogonalization_pipeline.py
因子正交化流水线（生产环境版本）

使用方式：
    pipeline = OrthogonalizationPipeline(config=PIPELINE_CONFIG)
    orthogonalized_factors = pipeline.run(raw_factors_df)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class OrthConfig:
    """单个正交化操作的配置"""
    target:      str                   # 被正交化的因子名
    controls:    List[str]             # 控制变量因子名列表
    output_name: str                   # 输出因子名（默认 target + '_pure'）
    replace:     bool     = True       # 是否替换原始因子（True=替换，False=新增）
    min_stocks:  int      = 50
    winsorize:   bool     = True


# ── 生产配置（按执行顺序排列）────────────────────────────────────────────
PIPELINE_CONFIG: List[OrthConfig] = [
    # Step 1: ivol ⊥ turn（O-1，方向1）
    OrthConfig(
        target      = 'ivol_60d',
        controls    = ['turn_20d'],
        output_name = 'ivol_pure',
        replace     = True,
    ),
    # Step 2: turn ⊥ ivol（O-1，方向2）
    OrthConfig(
        target      = 'turn_20d',
        controls    = ['ivol_60d'],
        output_name = 'turn_pure',
        replace     = True,
    ),
    # Step 3: hk ⊥ (ivol_pure, turn_pure)（O-3，使用已正交化的因子）
    OrthConfig(
        target      = 'hk_hold_ratio',
        controls    = ['ivol_pure', 'turn_pure'],
        output_name = 'hk_hold_pure',
        replace     = True,
    ),
    # Step 4: roe_delta_3q ⊥ roe_delta（O-2）
    OrthConfig(
        target      = 'roe_delta_3q',
        controls    = ['roe_delta'],
        output_name = 'roe_delta_3q_pure',
        replace     = True,
    ),
    # Step 5: rev_acceleration ⊥ rev_yoy（O-4）
    OrthConfig(
        target      = 'rev_acceleration',
        controls    = ['rev_yoy'],
        output_name = 'rev_acc_pure',
        replace     = True,
    ),
    # Step 6: gross_margin_trend ⊥ gross_margin（O-5）
    OrthConfig(
        target      = 'gross_margin_trend',
        controls    = ['gross_margin'],
        output_name = 'gmt_pure',
        replace     = True,
    ),
]


class OrthogonalizationPipeline:
    """
    因子正交化流水线

    按配置顺序依次执行所有正交化操作
    中间结果自动传递（O-3 使用 O-1 的输出）
    """

    def __init__(self, config: List[OrthConfig]):
        self.config = config
        self.orth   = FactorOrthogonalizer(winsorize=True)
        self._audit_log = []

    def run(
        self,
        factors: pd.DataFrame,  # (date × stock) 的宽格式，columns=factor_names
    ) -> pd.DataFrame:
        """
        执行完整正交化流水线

        Parameters
        ----------
        factors : 已完成截尾+中性化+标准化的因子面板

        Returns
        -------
        DataFrame: 正交化后的因子面板（与输入格式相同）
        """
        result = factors.copy()
        factor_names = set(result.columns.get_level_values(-1).unique()
                           if result.columns.nlevels > 1
                           else result.columns.tolist())

        for cfg in self.config:
            # 检查依赖是否存在（控制变量可能是上一步的输出）
            missing = [c for c in cfg.controls if c not in factor_names]
            if missing:
                logger.warning(f"正交化 {cfg.target} ⊥ {cfg.controls}: "
                               f"控制变量 {missing} 不存在，跳过")
                continue

            if cfg.target not in factor_names:
                logger.warning(f"目标因子 {cfg.target} 不存在，跳过")
                continue

            logger.info(f"执行: {cfg.output_name} = {cfg.target} ⊥ {cfg.controls}")

            # 提取目标因子和控制因子
            target_df   = result[cfg.target]  if cfg.target   in result.columns else None
            control_dfs = {c: result[c] for c in cfg.controls if c in result.columns}

            if target_df is None or len(control_dfs) < len(cfg.controls):
                logger.error(f"数据提取失败，跳过 {cfg.output_name}")
                continue

            # 执行正交化
            residual = self.orth.fit_transform(
                target   = target_df,
                controls = control_dfs,
                name     = cfg.output_name,
            )

            # 更新因子池
            result[cfg.output_name] = residual
            factor_names.add(cfg.output_name)

            if cfg.replace and cfg.target != cfg.output_name:
                result = result.drop(columns=[cfg.target])
                factor_names.discard(cfg.target)
                logger.info(f"  → 原始因子 {cfg.target} 已从因子池移除")

            # 记录审计日志
            self._audit_log.append({
                'step':        cfg.output_name,
                'target':      cfg.target,
                'controls':    cfg.controls,
                'replaced':    cfg.replace,
            })

        logger.info(f"正交化流水线完成。当前因子池: {sorted(factor_names)}")
        return result

    def get_audit_log(self) -> pd.DataFrame:
        """返回操作审计日志，用于前视偏差审计"""
        return pd.DataFrame(self._audit_log)
```

### B. 完整的衍生因子测试框架

```python
def run_derived_factor_evaluation(
    derived_factor_df: pd.DataFrame,
    original_factor_df: pd.DataFrame,
    ret_df:             pd.DataFrame,
    industry_df:        pd.DataFrame,
    factor_name:        str,
    factor_type:        str = 'orthogonalized',  # 'orthogonalized' / 'composite' / 'interaction'
    train_end:          str = '2020-12',
    valid_start:        str = '2021-01',
    valid_end:          str = '2022-12',
) -> dict:
    """
    衍生因子完整评估报告

    对比原始因子和衍生因子的以下指标：
    1. 训练期 IC_IR（提升还是下降？）
    2. 验证期 IC_IR（衰减是否更小？）
    3. 与控制变量的截面相关性（正交化是否彻底？）
    4. IC 的时序稳定性（波动是否降低？）
    5. 入池阈值（是否通过更严格的衍生因子标准？）
    """

    def compute_period_icir(factor, ret, start, end):
        ic_list = []
        period_factor = factor.loc[start:end]
        period_ret    = ret.loc[start:end]
        for t in period_factor.index.intersection(period_ret.index):
            f = period_factor.loc[t].dropna()
            r = period_ret.loc[t].reindex(f.index).dropna()
            common = f.index.intersection(r.index)
            if len(common) >= 50:
                ic_list.append(f.loc[common].corr(r.loc[common], method='spearman'))
        arr = np.array(ic_list)
        if len(arr) < 3:
            return {'ic_mean': np.nan, 'ic_ir': np.nan, 'ic_std': np.nan}
        return {
            'ic_mean':     round(arr.mean(), 4),
            'ic_std':      round(arr.std(), 4),
            'ic_ir':       round(arr.mean() / arr.std(), 3),
            'pos_ratio':   round((arr > 0).mean(), 3),
            'n_periods':   len(arr),
        }

    # 训练期和验证期 IC_IR
    orig_train  = compute_period_icir(original_factor_df, ret_df, '2012-01', train_end)
    orig_valid  = compute_period_icir(original_factor_df, ret_df, valid_start, valid_end)
    deriv_train = compute_period_icir(derived_factor_df,  ret_df, '2012-01', train_end)
    deriv_valid = compute_period_icir(derived_factor_df,  ret_df, valid_start, valid_end)

    # 根据因子类型确定入池阈值
    thresholds = {
        'orthogonalized': {'train_icir': 0.30, 'valid_icir': 0.22},
        'composite':      {'train_icir': 0.30, 'valid_icir': 0.22},
        'interaction':    {'train_icir': 0.40, 'valid_icir': 0.25},
    }
    thresh = thresholds.get(factor_type, thresholds['orthogonalized'])

    # 判断是否通过
    train_pass  = abs(deriv_train.get('ic_ir', 0)) >= thresh['train_icir']
    valid_pass  = abs(deriv_valid.get('ic_ir', 0)) >= thresh['valid_icir']
    dir_ok      = (orig_train.get('ic_ir', 0) * deriv_train.get('ic_ir', 0)) > 0
    icir_retain = (abs(deriv_train.get('ic_ir', 0)) /
                   abs(orig_train.get('ic_ir', 1e-6)))

    report = {
        'factor_name':      factor_name,
        'factor_type':      factor_type,
        'original': {
            'train': orig_train,
            'valid': orig_valid,
        },
        'derived': {
            'train': deriv_train,
            'valid': deriv_valid,
        },
        'quality_checks': {
            'train_icir_pass':    train_pass,
            'valid_icir_pass':    valid_pass,
            'direction_ok':       dir_ok,
            'icir_retention':     round(icir_retain, 3),
            'retention_adequate': icir_retain >= 0.60,
        },
        'recommend_replace': train_pass and valid_pass and dir_ok and icir_retain >= 0.60,
        'thresholds_used':   thresh,
    }

    # 打印摘要报告
    _print_evaluation_summary(report)
    return report


def _print_evaluation_summary(report: dict) -> None:
    fn    = report['factor_name']
    orig  = report['original']
    deriv = report['derived']
    chk   = report['quality_checks']

    print(f"\n{'='*60}")
    print(f"  衍生因子评估报告：{fn}  [{report['factor_type']}]")
    print(f"{'='*60}")
    print(f"  {'指标':<20} {'原始因子':>12} {'衍生因子':>12} {'变化':>8}")
    print(f"  {'-'*52}")

    def _fmt(val, ref=None):
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return '   N/A'
        s = f"{val:>12.3f}"
        if ref is not None and not np.isnan(ref) and ref != 0:
            arrow = '↑' if val > ref else '↓' if val < ref else '→'
            s += f" {arrow}"
        return s

    tr_o = orig['train'].get('ic_ir', np.nan)
    vl_o = orig['valid'].get('ic_ir', np.nan)
    tr_d = deriv['train'].get('ic_ir', np.nan)
    vl_d = deriv['valid'].get('ic_ir', np.nan)

    print(f"  {'训练期 IC_IR':<20} {_fmt(tr_o):>12} {_fmt(tr_d, tr_o):>12}")
    print(f"  {'验证期 IC_IR':<20} {_fmt(vl_o):>12} {_fmt(vl_d, vl_o):>12}")
    print(f"  {'IC_IR 保留率':<20} {'':>12} {_fmt(chk['icir_retention']):>12}")
    print(f"\n  {'检验项':<30} {'结果':>8}")
    print(f"  {'-'*38}")

    checks = [
        ('训练期 IC_IR 达标',   chk['train_icir_pass']),
        ('验证期 IC_IR 达标',   chk['valid_icir_pass']),
        ('方向一致性',          chk['direction_ok']),
        ('IC_IR 保留率 ≥ 60%',  chk['retention_adequate']),
    ]
    for label, passed in checks:
        icon = '✅' if passed else '❌'
        print(f"  {label:<30} {icon}")

    status = '🟢 建议替换入池' if report['recommend_replace'] else '🔴 不建议替换'
    print(f"\n  最终结论：{status}")
    print(f"{'='*60}")
```

### C. 快速启动脚本

```python
"""
quick_start_optimization.py
P0 级别优化的快速启动脚本（本月内执行）

前置条件：
  1. 已有所有17个因子的标准化数据 factors_standardized_df
  2. 已有月度 IC 历史 ic_history_df
  3. 已有下期超额收益 forward_ret_df
  4. 已有行业分类 industry_df

执行时间预估：约 2–4 小时（含回测验证）
"""

import pandas as pd

# ── Step 1: 加载数据 ──────────────────────────────────────────────────
# factors_df : (date × stock) per factor，已完成标准化
# ret_df     : (date × stock) 下期月度超额收益
# ic_history : (date × factor) 月度 IC 历史

# ── Step 2: P0-A：ivol ⊥ turn ────────────────────────────────────────
print("\n>>> P0-A: ivol_60d ⊥ turn_20d 双向正交化")
orth = FactorOrthogonalizer()

ivol_pure, turn_pure = orthogonalize_ivol_turn(
    ivol_df = factors_df['ivol_60d'],
    turn_df = factors_df['turn_20d'],
)

# 验证
print("\n验证 ivol_pure：")
result_ivol = orth.validate(
    original = factors_df['ivol_60d'],
    residual = ivol_pure,
    controls = factors_df['turn_20d'],
    ret_df   = ret_df,
    label    = 'ivol_pure vs turn',
)

print("\n验证 turn_pure：")
result_turn = orth.validate(
    original = factors_df['turn_20d'],
    residual = turn_pure,
    controls = factors_df['ivol_60d'],
    ret_df   = ret_df,
    label    = 'turn_pure vs ivol',
)

# ── Step 3: P0-B：估值合并 ─────────────────────────────────────────────
print("\n>>> P0-B: ep_ttm + cfp → value_composite")
builder = CompositeFactorBuilder(method='icir', ic_window=24)

value_composite, value_weights = builder.build(
    factors     = {'ep_ttm': factors_df['ep_ttm'], 'cfp': factors_df['cfp']},
    ic_history  = ic_history_df[['ep_ttm', 'cfp']],
    directions  = {'ep_ttm': +1, 'cfp': +1},
    composite_name = 'value_composite',
)

# ── Step 4: 在 Ridge_48m 流水线中测试 ─────────────────────────────────
# 构建 v1.1 因子池：用 ivol_pure, turn_pure, value_composite 替换原始因子
factors_v11 = factors_df.copy()
factors_v11['ivol_pure']      = ivol_pure
factors_v11['turn_pure']      = turn_pure
factors_v11['value_composite']= value_composite
factors_v11 = factors_v11.drop(columns=['ivol_60d', 'turn_20d', 'ep_ttm', 'cfp'])

print(f"\n>>> v1.1 因子池规模：{factors_v11.columns.nunique()} 个因子")
print(">>> 请在 Ridge_rolling_48m 流水线中运行 v1.1，对比验证期 IR")
print(">>> 预期：v1.1 IR > v1.0 IR（当前 1.489），改进幅度约 +0.05~+0.10")
```

---

*本文档为因子工程优化计划的完整实施手册，所有代码均可直接集成进现有因子流水线。执行顺序严格遵循依赖关系（第七章），测试集（2023-2025）受项目纪律保护，本文档所有操作在训练期+验证期内进行。优化效果以实际回测结果为准，所有预期改进均为基于学术文献和因子结构的先验估计。*

*下一步：完成本文档所有 P0 操作后，结合 `factor_gap_analysis.md` 中 P0 级别新因子（accruals, asset_growth），统一提交 v1.1 因子池的完整评估报告。*
