# 多因子项目：信号质量诊断体系与因子合成改进计划

> **文档目的**：建立一套可复用的"信号 → 持仓"质量诊断流程，使每一轮因子研究都能客观回答"我的信号实现质量如何、损耗在哪、下一步该改什么"。
>
> **核心问题**：当前出现 ICIR 高但策略 IR 低的悖论，初步定位为 Top-50 等权截断导致的 Transfer Coefficient（TC）泄漏。
>
> **版本**：v1.0 ｜ **状态**：待执行

---

## 目录

1. [问题诊断与根因分析](#一问题诊断与根因分析)
2. [理论锚点：基本定理](#二理论锚点grinold-kahn-基本定理)
3. [因子合成方法对比与选型](#三因子合成方法对比与选型)
4. [信号到持仓质量诊断体系（核心）](#四信号到持仓质量诊断体系核心)
5. [诊断指标清单与阈值](#五诊断指标清单与阈值)
6. [分阶段执行计划](#六分阶段执行计划)
7. [偏差自查清单](#七偏差自查清单)
8. [附录：代码模块说明](#八附录代码模块说明)

---

## 一、问题诊断与根因分析

### 1.1 现象描述

| 观测 | 数值方向 | 含义 |
|---|---|---|
| 信号 ICIR | 高 | 信号时间序列稳定、单期预测技能强 |
| 最终策略 IR | 反而低 | 实盘/回测收益的风险调整后表现差 |
| 持仓构建方式 | Top-50 等权 | 仅取信号最强的 50 只，等权配置 |

这是一个**信号质量优秀、但落地实现损耗严重**的典型割裂。

### 1.2 根因假设（按优先级）

```
ICIR 高 → IR 低
   │
   ├─【假设 A｜最可能】Transfer Coefficient 泄漏
   │     Top-N 硬截断同时丢弃了：
   │       (1) 幅度信息：Rank 1 与 Rank 50 等权，忽视信号强弱
   │       (2) 边际信息：Rank 50 与 Rank 51 信号几乎相同，却被一刀切
   │       (3) 符号信息：若为多空，截断破坏了空头腿的对称性
   │     → 实际持仓与最优持仓相关性 TC 估计仅 0.3~0.5
   │
   ├─【假设 B】有效广度 N_eff 被高估
   │     Top-50 内部个股可能高度相关（同行业、同风格暴露）
   │     真实独立 bet 数远小于 50，分散不足 → 组合风险偏高
   │
   ├─【假设 C】风险未做调整
   │     等权使高波动个股获得与低波动个股相同权重
   │     → 组合波动率被少数高波动票主导，IR 分母膨胀
   │
   └─【假设 D｜需排除】ICIR 本身存在隐性偏差
         前视偏差、IC 计算窗口滑动、换仓时点选择
         → 样本内 ICIR 虚高，样本外不成立
```

### 1.3 诊断结论的判定标准

| 若诊断出 | 则根因为 | 优先修复方向 |
|---|---|---|
| TC < 0.55 | 假设 A 成立 | 改连续权重 / PPP |
| N_eff << 持仓数 | 假设 B 成立 | 行业/风格中性化、相关性约束 |
| 高波动票主导组合方差 | 假设 C 成立 | 波动率调整权重 |
| 样本外 ICIR 大幅下降 | 假设 D 成立 | 回到信号层重做样本外验证 |

> **注**：A、B、C 可同时成立，但 D 必须先排除——若 ICIR 本身有前视偏差，后续所有持仓优化都是在错误信号上做文章。

---

## 二、理论锚点：Grinold-Kahn 基本定理

所有诊断都围绕这一个公式展开，它把"信号质量"与"信号实现"分离：

```
IR = IC × TC × √N
        │    │    │
        │    │    └── 广度：独立 bet 数量
        │    └─────── 转移系数：信号实现质量 = corr(实际持仓, 最优持仓)
        └──────────── 信息系数：单期预测技能
```

时间序列版本（你的语境）：

```
IR_策略 = ICIR × TC × √T
```

**关键推论**：
- ICIR 高、IR 低 ⟹ 几乎必然是 TC 在泄漏（其他项不会让关系反向）
- TC 从 0.5 提升到 0.75，IR 直接提升 50%，**无需任何信号改进**
- 用扩大广度来弥补 TC 损耗，需要把 N 扩大 `1/TC²` 倍（TC=0.5 时需 4 倍），代价极高
- **提升 TC 是当前性价比最高的杠杆**

---

## 三、因子合成方法对比与选型

> 在修复持仓构建之前，先审视信号本身是否合成得当。多个单因子如何合成为综合信号，直接影响 IC 和有效广度。

### 3.1 主流合成方法对比

| 方法 | 原理 | 优点 | 缺点 | 适用场景 |
|---|---|---|---|---|
| **等权合成** | 各因子标准化后简单平均 | 稳健、无过拟合、可解释 | 忽视因子有效性差异 | 因子质量相近、样本短 |
| **IC 加权** | 按历史 IC 加权 | 反映因子有效性 | IC 估计噪声大、易过拟合 | 因子有效性差异明显 |
| **ICIR 加权** | 按 IC/σ(IC) 加权 | 兼顾有效性与稳定性 | 仍依赖历史估计 | 中长周期因子 |
| **最大化 IC_IR 优化** | 求解使组合 ICIR 最大的权重 | 理论最优 | 需因子协方差，易过拟合 | 因子数适中、样本充足 |
| **半衰期加权** | 近期表现加权更高 | 适应因子衰减 | 增加参数 | 因子有效性时变 |
| **PCA / 正交化** | 主成分提取或施密特正交 | 消除因子共线性，提升 N_eff | 可解释性下降 | 因子高度相关 |
| **机器学习合成** | XGBoost/NN 非线性组合 | 捕捉非线性、交互项 | 黑箱、过拟合风险高、需大样本 | 数据充足、追求绝对性能 |

### 3.2 合成方法的有效广度修正

关键警示：因子相关性会侵蚀有效广度。

```
N_eff = K / (1 + (K-1) × ρ̄)
```

其中 K 是因子数，ρ̄ 是因子间平均相关系数。

| K（因子数） | ρ̄=0（独立） | ρ̄=0.3 | ρ̄=0.6 |
|---|---|---|---|
| 5 | 5.0 | 2.3 | 1.6 |
| 10 | 10.0 | 2.9 | 1.7 |
| 20 | 20.0 | 3.2 | 1.7 |

**结论**：当因子高度相关时，加再多因子对有效广度几乎没有帮助。**先正交化、降低 ρ̄，比堆砌因子数量更有价值。**

### 3.3 选型建议（针对当前项目）

```
第一优先：先确认问题在合成层还是持仓层
  └─ 若 TC 低 → 问题在持仓层，合成方法暂不动
  └─ 若 TC 高但 IR 仍低 → 回到合成层

合成层改进路径（保守 → 激进）：
  1. 等权 → ICIR 加权（最小改动，先验证收益）
  2. 加入因子正交化（提升 N_eff，降低共线性）
  3. 半衰期加权（若因子有效性明显时变）
  4. ML 合成（仅在前述都做完、样本充足、有严格样本外验证时）
```

> **反对过早上 ML**：在 TC 都没修好的情况下上机器学习合成，等于在漏水的桶上装更大的水龙头。先把转移损耗堵住，信号质量的改进才能真正传导到 IR。

---

## 四、信号到持仓质量诊断体系（核心）

> 这是本计划最重要的部分。目标：建立一个**每轮研究都能跑一遍**的标准化诊断流程，输出一份固定格式的"信号实现质量报告"。

### 4.1 诊断流程总览

```
输入：截面信号 z_it、实际持仓 h_it、前瞻收益 r_it、个股波动率 σ_it
  │
  ├─ Step 1：信号层诊断
  │     ├─ IC 时间序列（均值、std、ICIR、t-stat）
  │     ├─ IC 衰减曲线（信号半衰期）
  │     └─ 分层单调性检验（Rank 分组收益是否单调）
  │
  ├─ Step 2：转移诊断【核心】
  │     ├─ TC = corr(h_actual, h_optimal)
  │     ├─ 幅度保真度：实际权重 vs 信号幅度的相关
  │     └─ 边际损耗：被截断标的的信号分布
  │
  ├─ Step 3：广度诊断
  │     ├─ N_eff = 1 / Σ(w_i²)（Herfindahl 倒数）
  │     ├─ 持仓相关性矩阵的有效秩
  │     └─ 行业/风格暴露集中度
  │
  ├─ Step 4：归因分解
  │     └─ IR_实际 vs IR_理论 = ICIR × TC × √N
  │           量化每个环节的损耗百分比
  │
  └─ 输出：标准化诊断报告（固定格式，可跨轮次对比）
```

### 4.2 核心诊断代码

```python
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class SignalQualityReport:
    """信号实现质量诊断报告（标准化输出，可跨轮次对比）"""
    # 信号层
    ic_mean: float = np.nan
    ic_std: float = np.nan
    icir: float = np.nan
    ic_tstat: float = np.nan
    monotonicity: float = np.nan        # 分层收益单调性（Spearman）
    # 转移层【核心】
    tc: float = np.nan                  # Transfer Coefficient
    amplitude_fidelity: float = np.nan  # 权重对信号幅度的保真
    truncation_loss: float = np.nan     # 被截断标的的信号占比
    # 广度层
    n_holdings: int = 0
    n_eff: float = np.nan               # 有效 bet 数
    concentration: float = np.nan       # 持仓集中度（Herfindahl）
    # 归因层
    ir_theoretical: float = np.nan
    ir_actual: float = np.nan
    ir_loss_pct: float = np.nan

    def to_dict(self) -> Dict:
        return {k: round(v, 4) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


class SignalToPositionDiagnostics:
    """
    信号到持仓质量诊断器

    用法（每轮研究跑一遍）：
        diag = SignalToPositionDiagnostics()
        report = diag.run(signals_df, weights_df, returns_df, vol_df)
        diag.print_report(report)
        diag.export_report(report, "round_03.json")  # 存档用于跨轮对比
    """

    def __init__(self, annual_factor: float = 252, min_stocks: int = 20):
        self.annual_factor = annual_factor
        self.min_stocks = min_stocks

    # ───────────────────────── Step 1：信号层 ─────────────────────────
    def _diagnose_signal(self, signals, returns) -> Dict:
        """IC 时间序列与分层单调性"""
        ic_series = []
        layer_returns = {q: [] for q in range(5)}  # 五分层

        for t in signals.index:
            if t not in returns.index:
                continue
            z = signals.loc[t].dropna()
            r = returns.loc[t]
            common = z.index.intersection(r.index)
            if len(common) < self.min_stocks:
                continue

            z_t, r_t = z.loc[common], r.loc[common]
            ic, _ = spearmanr(z_t, r_t)
            ic_series.append(ic)

            # 分层收益（用于单调性检验）
            ranks = z_t.rank(pct=True)
            for q in range(5):
                mask = (ranks > q / 5) & (ranks <= (q + 1) / 5)
                if mask.sum() > 0:
                    layer_returns[q].append(r_t[mask].mean())

        ic_arr = np.array(ic_series)
        ic_mean = ic_arr.mean()
        ic_std = ic_arr.std()
        icir = ic_mean / ic_std if ic_std > 0 else np.nan
        ic_tstat = ic_mean / (ic_std / np.sqrt(len(ic_arr))) if ic_std > 0 else np.nan

        # 分层单调性：五个分层的平均收益是否随信号递增
        layer_means = [np.mean(layer_returns[q]) if layer_returns[q] else np.nan
                       for q in range(5)]
        monotonicity, _ = spearmanr(range(5), layer_means)

        return dict(ic_mean=ic_mean, ic_std=ic_std, icir=icir,
                    ic_tstat=ic_tstat, monotonicity=monotonicity,
                    _layer_means=layer_means)

    # ─────────────────────── Step 2：转移诊断【核心】 ───────────────────────
    def _diagnose_transfer(self, signals, weights, vol=None) -> Dict:
        """
        计算 Transfer Coefficient 及相关诊断

        最优持仓代理：h_optimal ∝ z_i / σ_i²（风险调整后的信号）
        若无波动率数据，退化为 h_optimal ∝ z_i
        """
        tc_list, amp_list, trunc_list = [], [], []

        for t in signals.index:
            if t not in weights.index:
                continue
            z = signals.loc[t].dropna()
            h = weights.loc[t].dropna()
            common = z.index.intersection(h.index)
            if len(common) < 10:
                continue

            z_c, h_c = z.loc[common], h.loc[common]

            # 构造最优持仓代理
            if vol is not None and t in vol.index:
                v = vol.loc[t].reindex(common)
                v_clip = v.clip(lower=v.quantile(0.05))
                h_opt = z_c / (v_clip ** 2)
            else:
                h_opt = z_c
            h_opt = h_opt / h_opt.abs().sum()

            # TC：实际持仓 vs 最优持仓的秩相关
            tc, _ = spearmanr(h_c, h_opt)
            tc_list.append(tc)

            # 幅度保真度：实际权重幅度 vs 信号幅度
            amp, _ = spearmanr(h_c.abs(), z_c.abs())
            amp_list.append(amp)

            # 截断损耗：未进入持仓的标的，其信号绝对值占总信号的比例
            held = set(h_c[h_c.abs() > 1e-8].index)
            not_held = z_c[~z_c.index.isin(held)]
            if z_c.abs().sum() > 0:
                trunc = not_held.abs().sum() / z_c.abs().sum()
                trunc_list.append(trunc)

        return dict(
            tc=np.nanmean(tc_list),
            amplitude_fidelity=np.nanmean(amp_list),
            truncation_loss=np.nanmean(trunc_list),
        )

    # ───────────────────────── Step 3：广度诊断 ─────────────────────────
    def _diagnose_breadth(self, weights) -> Dict:
        """有效 bet 数与持仓集中度"""
        n_eff_list, conc_list, n_hold_list = [], [], []

        for t in weights.index:
            h = weights.loc[t].dropna()
            h = h[h.abs() > 1e-8]
            if len(h) == 0:
                continue
            w_norm = h.abs() / h.abs().sum()
            herfindahl = (w_norm ** 2).sum()
            n_eff_list.append(1.0 / herfindahl)
            conc_list.append(herfindahl)
            n_hold_list.append(len(h))

        return dict(
            n_holdings=int(np.mean(n_hold_list)),
            n_eff=np.nanmean(n_eff_list),
            concentration=np.nanmean(conc_list),
        )

    # ───────────────────────── Step 4：归因分解 ─────────────────────────
    def _diagnose_attribution(self, weights, returns, signal_res, transfer_res, breadth_res) -> Dict:
        """IR 理论 vs 实际，量化损耗"""
        port_rets = []
        for t in weights.index:
            if t not in returns.index:
                continue
            h = weights.loc[t].dropna()
            r = returns.loc[t]
            common = h.index.intersection(r.index)
            port_rets.append((h.loc[common] * r.loc[common]).sum())

        port_rets = np.array(port_rets)
        ir_actual = (port_rets.mean() / port_rets.std()
                     * np.sqrt(self.annual_factor)) if port_rets.std() > 0 else np.nan

        # 理论 IR = ICIR × TC × √N_eff（年化）
        icir = signal_res['icir']
        tc = transfer_res['tc']
        n_eff = breadth_res['n_eff']
        # 年化：ICIR 已是单期，乘以 √(每年期数)
        periods_per_year = self.annual_factor / self._infer_period(weights)
        ir_theoretical = icir * tc * np.sqrt(periods_per_year) if not np.isnan(icir) else np.nan

        ir_loss_pct = ((1 - ir_actual / ir_theoretical) * 100
                       if ir_theoretical and not np.isnan(ir_theoretical) else np.nan)

        return dict(ir_theoretical=ir_theoretical, ir_actual=ir_actual,
                    ir_loss_pct=ir_loss_pct)

    @staticmethod
    def _infer_period(weights) -> float:
        """推断调仓周期（交易日）"""
        if len(weights.index) < 2:
            return 1.0
        try:
            deltas = pd.Series(weights.index).diff().dropna()
            return deltas.dt.days.median()
        except Exception:
            return 1.0

    # ───────────────────────── 主入口 ─────────────────────────
    def run(self, signals: pd.DataFrame, weights: pd.DataFrame,
            returns: pd.DataFrame, vol: Optional[pd.DataFrame] = None
            ) -> SignalQualityReport:
        sig = self._diagnose_signal(signals, returns)
        trans = self._diagnose_transfer(signals, weights, vol)
        breadth = self._diagnose_breadth(weights)
        attr = self._diagnose_attribution(weights, returns, sig, trans, breadth)

        return SignalQualityReport(
            ic_mean=sig['ic_mean'], ic_std=sig['ic_std'], icir=sig['icir'],
            ic_tstat=sig['ic_tstat'], monotonicity=sig['monotonicity'],
            tc=trans['tc'], amplitude_fidelity=trans['amplitude_fidelity'],
            truncation_loss=trans['truncation_loss'],
            n_holdings=breadth['n_holdings'], n_eff=breadth['n_eff'],
            concentration=breadth['concentration'],
            ir_theoretical=attr['ir_theoretical'], ir_actual=attr['ir_actual'],
            ir_loss_pct=attr['ir_loss_pct'],
        )

    # ───────────────────────── 报告输出 ─────────────────────────
    def print_report(self, r: SignalQualityReport):
        def flag(val, good, ok):
            if np.isnan(val):
                return "  ? "
            if val >= good:
                return " ✓✓ "
            if val >= ok:
                return "  ✓ "
            return " ⚠  "

        print("=" * 60)
        print("  信号到持仓质量诊断报告")
        print("=" * 60)
        print("\n【Step 1 · 信号层】")
        print(f"  IC 均值          : {r.ic_mean:+.4f}")
        print(f"  IC 标准差        : {r.ic_std:.4f}")
        print(f"  ICIR             : {r.icir:+.4f} {flag(r.icir, 0.5, 0.3)}")
        print(f"  IC t-stat        : {r.ic_tstat:+.2f}  (>2 显著)")
        print(f"  分层单调性       : {r.monotonicity:+.3f} {flag(r.monotonicity, 0.9, 0.7)}")

        print("\n【Step 2 · 转移层】★核心")
        print(f"  Transfer Coef TC : {r.tc:+.4f} {flag(r.tc, 0.75, 0.55)}")
        print(f"  幅度保真度       : {r.amplitude_fidelity:+.4f} {flag(r.amplitude_fidelity, 0.7, 0.5)}")
        print(f"  截断信号损耗     : {r.truncation_loss:.1%}  (越低越好)")

        print("\n【Step 3 · 广度层】")
        print(f"  持仓数量         : {r.n_holdings}")
        print(f"  有效 bet 数 N_eff: {r.n_eff:.1f}")
        print(f"  集中度 Herf      : {r.concentration:.4f}")
        eff_ratio = r.n_eff / r.n_holdings if r.n_holdings else np.nan
        print(f"  有效占比         : {eff_ratio:.1%}  (N_eff/持仓数)")

        print("\n【Step 4 · 归因】")
        print(f"  理论 IR          : {r.ir_theoretical:+.3f}")
        print(f"  实际 IR          : {r.ir_actual:+.3f}")
        print(f"  IR 损耗          : {r.ir_loss_pct:.1f}%")

        print("\n" + "─" * 60)
        print("  诊断结论：")
        self._verdict(r)
        print("=" * 60)

    def _verdict(self, r: SignalQualityReport):
        """自动给出诊断结论"""
        if not np.isnan(r.tc) and r.tc < 0.55:
            print(f"  ⚠ 根因 = 转移损耗。TC={r.tc:.2f} 过低，信号在持仓")
            print(f"    构建环节损耗严重。优先改连续权重 / PPP。")
        elif not np.isnan(r.n_eff) and r.n_holdings and r.n_eff / r.n_holdings < 0.5:
            print(f"  ⚠ 根因 = 广度虚高。N_eff/持仓={r.n_eff/r.n_holdings:.0%}，")
            print(f"    持仓内部高度相关。需行业/风格中性化。")
        elif not np.isnan(r.monotonicity) and r.monotonicity < 0.7:
            print(f"  ⚠ 根因 = 信号单调性差。可能存在非线性或反转。")
        else:
            print(f"  ✓ 转移与广度健康。主要杠杆在提升 IC（更好因子）")
            print(f"    或扩大 N（更多独立机会）。")

    def export_report(self, r: SignalQualityReport, path: str):
        """导出 JSON，用于跨轮次对比追踪"""
        import json
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(r.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"报告已存档：{path}")


# ───────────────────────── 跨轮次对比追踪 ─────────────────────────
class DiagnosisTracker:
    """
    追踪多轮研究的诊断指标演化，回答"这一轮比上一轮好在哪"
    """
    def __init__(self):
        self.history = []

    def add(self, round_name: str, report: SignalQualityReport):
        record = {'round': round_name}
        record.update(report.to_dict())
        self.history.append(record)

    def compare(self) -> pd.DataFrame:
        df = pd.DataFrame(self.history).set_index('round')
        key_metrics = ['icir', 'tc', 'amplitude_fidelity', 'n_eff',
                       'ir_theoretical', 'ir_actual', 'ir_loss_pct']
        return df[key_metrics].T  # 指标为行，轮次为列，方便横向对比
```

### 4.3 使用方式（每轮研究的标准动作）

```python
# 初始化追踪器（整个项目周期保持一个实例）
tracker = DiagnosisTracker()
diag = SignalToPositionDiagnostics(annual_factor=252)

# ── 第 1 轮：当前的 Top-50 等权 ──
report_v1 = diag.run(signals_df, weights_top50, returns_df, vol_df)
diag.print_report(report_v1)
diag.export_report(report_v1, "diag_v1_top50.json")
tracker.add("v1_top50等权", report_v1)

# ── 第 2 轮：改用波动率调整连续权重 ──
report_v2 = diag.run(signals_df, weights_voladj, returns_df, vol_df)
diag.print_report(report_v2)
tracker.add("v2_波动率调整", report_v2)

# ── 第 3 轮：改用 PPP ──
report_v3 = diag.run(signals_df, weights_ppp, returns_df, vol_df)
tracker.add("v3_PPP", report_v3)

# ── 横向对比所有轮次 ──
print(tracker.compare())
# 输出形如：
#                    v1_top50等权  v2_波动率调整  v3_PPP
# icir                      2.10        2.10      2.10   ← 信号没变
# tc                        0.45        0.78      0.85   ← TC 显著改善
# ir_actual                 0.62        1.15      1.34   ← IR 随 TC 提升
# ir_loss_pct              55.0        18.0      12.0   ← 损耗下降
```

> **这套流程的价值**：每一轮你都能看到 ICIR 是否保持（信号没被破坏）、TC 是否提升（持仓改进有效）、IR 损耗是否下降。**信号质量与实现质量被彻底分离，不再混为一谈。**

---

## 五、诊断指标清单与阈值

| 层级 | 指标 | 公式 | 优秀 | 可接受 | 警戒 |
|---|---|---|---|---|---|
| 信号 | ICIR | IC均值 / IC标准差 | > 0.5 | 0.3–0.5 | < 0.3 |
| 信号 | IC t-stat | IC均值 / (σ/√n) | > 3 | 2–3 | < 2 |
| 信号 | 分层单调性 | corr(分层序号, 分层收益) | > 0.9 | 0.7–0.9 | < 0.7 |
| **转移** | **TC** | **corr(实际持仓, 最优持仓)** | **> 0.75** | **0.55–0.75** | **< 0.55** |
| 转移 | 幅度保真度 | corr(\|权重\|, \|信号\|) | > 0.7 | 0.5–0.7 | < 0.5 |
| 转移 | 截断损耗 | 被弃信号 / 总信号 | < 20% | 20–40% | > 40% |
| 广度 | N_eff / 持仓数 | 1/Σw² ÷ 持仓数 | > 0.7 | 0.5–0.7 | < 0.5 |
| 归因 | IR 损耗 | 1 − IR实际/IR理论 | < 20% | 20–40% | > 40% |

> **使用原则**：转移层（加粗）是当前最关注的部分。每轮研究后填写此表，标红任何落入警戒区的指标。

---

## 六、分阶段执行计划

### 阶段 0：建立诊断基线（1–2 天）

- [ ] 部署 `SignalToPositionDiagnostics` 诊断器
- [ ] 对当前 Top-50 等权策略跑一遍完整诊断
- [ ] 确认 TC 实际值，验证假设 A
- [ ] 排除假设 D：检查 ICIR 是否有前视偏差（样本外重算 IC）
- [ ] **产出**：基线诊断报告 `diag_v1_top50.json`

### 阶段 1：修复转移损耗（3–5 天）

按改动从小到大依次测试，每步都跑诊断对比：

- [ ] **1a** 信号幅度比例权重（最小改动，立即可跑）
- [ ] **1b** 波动率调整权重 `w ∝ z/σ²`（需波动率估计）
- [ ] **1c** 软阈值 softmax 替代硬截断（单参数 β，网格搜索）
- [ ] 每步记录到 `DiagnosisTracker`，确认 TC 单调提升、ICIR 保持
- [ ] **产出**：TC 改善曲线，确定最优权重方案

### 阶段 2：参数化组合策略 PPP（1 周）

- [ ] 实现 PPP：直接优化 `w = 1/N·(1+θ'x)` 的 θ 最大化 Sharpe
- [ ] 滚动窗口估计（训练 252 天，月度重估），避免前视偏差
- [ ] L2 正则化校准（交叉验证选 λ）
- [ ] 检查 θ 时序稳定性（参数剧烈跳动 = 过拟合信号）
- [ ] **产出**：PPP vs 阶段 1 最优方案的诊断对比

### 阶段 3：审视因子合成（1 周，可与阶段 2 并行）

- [ ] 计算因子间相关矩阵，评估当前 N_eff
- [ ] 测试因子正交化（降低 ρ̄，提升有效广度）
- [ ] 等权 → ICIR 加权对比（验证是否真有提升）
- [ ] **产出**：合成方法对比报告

### 阶段 4：固化为标准流程（持续）

- [ ] 将诊断器封装为研究流水线的固定环节
- [ ] 每个新因子 / 新合成方法上线前必跑诊断
- [ ] 维护 `DiagnosisTracker` 历史，形成项目级指标演化档案

---

## 七、偏差自查清单

每轮研究务必逐项确认，防止诊断结论本身被偏差污染：

- [ ] **前视偏差**：IC 计算、权重生成是否只用了 t 时点之前的信息？PPP 的 θ 是否严格滚动估计？
- [ ] **数据窥探**：softmax 的 β、PPP 的 λ 等参数是否在样本外验证？还是反复在同一数据上调出来的？
- [ ] **幸存者偏差**：标的池是否包含已退市 / 已并购股票？
- [ ] **流动性假设**：Top 持仓是否包含无法以目标规模成交的标的？
- [ ] **交易成本**：诊断中的 IR 是否已扣除滑点、冲击成本、手续费？纸面 IR 与净 IR 可能差距巨大
- [ ] **容量限制**：当前 N_eff 在真实资金规模下是否会因容量约束而缩水？
- [ ] **IC 显著性**：ICIR 高是否伴随足够的 t-stat？样本期是否够长（建议 > 60 个独立观测）？

---

## 八、附录：代码模块说明

| 模块 | 职责 | 关键方法 |
|---|---|---|
| `SignalQualityReport` | 标准化报告数据结构 | `to_dict()` |
| `SignalToPositionDiagnostics` | 四步诊断主流程 | `run()` `print_report()` `export_report()` |
| `DiagnosisTracker` | 跨轮次指标追踪对比 | `add()` `compare()` |
| `SignalToWeightMapper` | 五种持仓映射方案 | `signal_proportional()` `vol_adjusted()` `softmax_threshold()` |
| `ParametricPortfolioPolicy` | PPP 优化器 | `fit()` `rolling_fit_predict()` `get_factor_attribution()` |

### 最优持仓代理的说明

诊断器中 TC 的计算依赖"最优持仓" `h_optimal` 的定义。本文采用风险调整信号：

```
h_optimal ∝ z_i / σ_i²
```

这是无约束均值-方差解的退化形式。**注意**：TC 的绝对值依赖于 `h_optimal` 的定义假设，因此：
- TC 应主要用于**同一定义下的跨轮次相对比较**，而非绝对水平的断言
- 若你的真实优化目标不是均值-方差（如风险平价、最小方差），应相应替换 `h_optimal` 的构造逻辑
- 建议在文档中固定 `h_optimal` 定义，保证跨轮可比

---

## 局限性与下一步

**本计划的局限**：
1. TC 的最优持仓代理基于均值-方差假设，若实际目标函数不同需调整
2. 归因分解中的"理论 IR"假设 bet 独立，实际相关性会使其偏乐观
3. ICIR 的稳定性假设在 regime 切换时失效，需配合滚动监控
4. 所有阈值（如 TC>0.75）是经验值，应根据你的资产类别、频率校准

**下一步可探索方向**：
1. 将 TC 纳入实时监控，作为策略健康度指标
2. regime-conditional 的 θ（让 PPP 参数随市场状态变化）
3. 在 PPP 框架内引入隐式风险惩罚，进一步解耦协方差估计
4. 因子有效性的时变检测（rolling ICIR + 结构突变检验）

---

*文档结束 ｜ 建议将本文与诊断代码一并纳入版本管理，每轮研究更新指标演化档案。*
