# 信号实现质量诊断系统集成计划

> **目的**：将 `factor_signal_diagnosis_plan.md` 中的诊断体系接入现有实验流水线，使每次
> `run_experiment.py` 运行后自动输出 Transfer Coefficient（TC）等信号实现质量指标，并在
> `compare_runs.py` 的横向对比板中可见。
>
> **版本**：v1.0 ｜ **状态**：待执行  
> **依赖文档**：`current work/5.29/factor_signal_diagnosis_plan.md`

---

## 0. 变更总览

| # | 操作 | 文件 | 风险 |
|---|------|------|------|
| 1 | **新建** | `src/evaluation/signal_quality.py` | 无（纯新增） |
| 2 | **新增函数** | `src/pipeline/stages.py` | 低（末尾追加，不修改现有函数） |
| 3 | **追加调用** | `scripts/run_experiment.py` | 低（非阻断，不影响已有逻辑） |
| 4 | **新增列** | `src/pipeline/compare.py` | 低（已有字段不变，仅增列） |

所有修改均向后兼容：旧 run 目录没有 `reports/signal_quality_report.json` 时，新列填 NaN，不影响对比板其他数据。

---

## 1. 已有流水线结构（修改前）

```
run_experiment.py
  ├── signal 阶段  → run_signal_stage()
  │     产物: signal/composite.parquet
  │            signal/ic_detail.parquet
  │            signal/coef_history.parquet（Ridge 时）
  │            signal/weight_history.parquet（ICIR 时）
  │            signal/signal_metadata.json
  │
  ├── portfolio 阶段 → run_portfolio_stage()
  │     产物: portfolio/target_weights.parquet      ← 诊断器的权重输入
  │            portfolio/baseline_weights.parquet
  │            portfolio/optimizer_meta.parquet
  │
  ├── backtest 阶段 → run_backtest_stage()
  │     产物: backtest/nav_valid.parquet
  │            backtest/metrics_valid.parquet
  │            backtest/trades_valid.parquet
  │            backtest/actual_weights_valid.parquet
  │
  ├── manifest.json + RUN_FINISHED.json （不可变标记）
  │
  └── write_self_check_md()（非阻断）
        产物: reports/self_check.md
```

修改后在 `write_self_check_md` 之后追加一步：

```
  └── write_self_check_md()（已有，非阻断）
  └── run_diagnosis_stage()（新增，非阻断）← 追加在此
        产物: reports/signal_quality_report.json
              reports/self_check.md（追加一节，文件已存在）
```

---

## 2. Step 1 — 新建 `src/evaluation/signal_quality.py`

**位置**：`src/evaluation/signal_quality.py`（全新文件，不覆盖任何现有文件）

### 2.1 设计决策

| 决策点 | 选择 | 原因 |
|--------|------|------|
| TC 的最优持仓代理 | `h_optimal ∝ z_i`（无波动率调整） | 项目当前无每股日频波动率面板；等后续有 `sigma_panel.parquet` 再升级 |
| TC 相关性类型 | Spearman 秩相关 | 与 `diagnose_ic_ir_paradox.py` Test-2 保持一致；对权重量级不敏感 |
| 验证期过滤 | 使用 `cfg.VALID_START / VALID_END` | 与 `compare.py` 的 IC 统计口径统一 |
| 年化期数推断 | `n_valid_periods / n_years_in_validation` | 比 `_infer_period` 的日期差法更稳定 |
| 诊断失败处理 | 返回全 NaN 的 `SignalQualityReport` | 不抛异常，让上层非阻断逻辑处理 |

### 2.2 完整代码

```python
"""
src/evaluation/signal_quality.py — 信号到持仓实现质量诊断
=============================================================

诊断信号质量 (ICIR) 与策略最终 IR 的差距来自何处，通过
Transfer Coefficient、有效广度、IR 损耗三个维度量化。

每次 run_experiment.py 完成后自动调用，产物写入
  reports/signal_quality_report.json

用法（通常不直接调用，由 stages.run_diagnosis_stage 调用）：
    from src.evaluation.signal_quality import SignalToPositionDiagnostics
    diag = SignalToPositionDiagnostics()
    report = diag.run(signals_df, weights_df, returns_df)
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

log = logging.getLogger(__name__)

# TC 阈值（用于自动诊断结论）
TC_EXCELLENT = 0.75
TC_OK = 0.55
MONO_EXCELLENT = 0.9
MONO_OK = 0.7
EFF_RATIO_OK = 0.5


@dataclass
class SignalQualityReport:
    """信号实现质量诊断报告（可跨轮次 JSON 对比）"""
    # ── 信号层 ──
    ic_mean: float = float("nan")
    ic_std: float = float("nan")
    icir: float = float("nan")
    ic_tstat: float = float("nan")
    monotonicity: float = float("nan")
    # ── 转移层（核心）──
    tc: float = float("nan")          # Transfer Coefficient
    amplitude_fidelity: float = float("nan")
    truncation_loss: float = float("nan")
    # ── 广度层 ──
    n_holdings: int = 0
    n_eff: float = float("nan")
    concentration: float = float("nan")
    # ── 归因层 ──
    ir_theoretical: float = float("nan")
    ir_actual: float = float("nan")
    ir_loss_pct: float = float("nan")
    # ── 元信息 ──
    n_valid_periods: int = 0

    def to_dict(self) -> Dict:
        d = asdict(self)
        return {
            k: (round(v, 4) if isinstance(v, float) and not math.isnan(v) else v)
            for k, v in d.items()
        }


class SignalToPositionDiagnostics:
    """
    信号到持仓实现质量四步诊断器。

    Args:
        min_stocks: 单期最少有效股票数，不足时跳过该期
    """

    def __init__(self, min_stocks: int = 10):
        self.min_stocks = min_stocks

    # ──────────────────────────── Step 1 信号层 ────────────────────────────

    def _diagnose_signal(
        self,
        signals: pd.DataFrame,
        returns: pd.DataFrame,
    ) -> dict:
        ic_list: list[float] = []
        layer_returns: dict[int, list[float]] = {q: [] for q in range(5)}

        for t in signals.index:
            if t not in returns.index:
                continue
            z = signals.loc[t].dropna()
            r = returns.loc[t].dropna()
            common = z.index.intersection(r.index)
            if len(common) < self.min_stocks:
                continue

            z_t, r_t = z.loc[common], r.loc[common]
            ic_val, _ = spearmanr(z_t.values, r_t.values)
            if not math.isnan(float(ic_val)):
                ic_list.append(float(ic_val))

            ranks = z_t.rank(pct=True)
            for q in range(5):
                mask = (ranks > q / 5) & (ranks <= (q + 1) / 5)
                if mask.sum() > 0:
                    layer_returns[q].append(float(r_t[mask].mean()))

        if not ic_list:
            return dict(ic_mean=float("nan"), ic_std=float("nan"),
                        icir=float("nan"), ic_tstat=float("nan"),
                        monotonicity=float("nan"))

        ic_arr = np.array(ic_list)
        ic_mean = float(ic_arr.mean())
        ic_std = float(ic_arr.std(ddof=1)) if len(ic_arr) > 1 else float("nan")
        icir = ic_mean / ic_std if ic_std and ic_std > 0 else float("nan")
        n = len(ic_arr)
        ic_tstat = icir * math.sqrt(n) if not math.isnan(icir) else float("nan")

        layer_means = [
            float(np.mean(layer_returns[q])) if layer_returns[q] else float("nan")
            for q in range(5)
        ]
        valid_pairs = [(i, v) for i, v in enumerate(layer_means) if not math.isnan(v)]
        if len(valid_pairs) >= 3:
            xs = [p[0] for p in valid_pairs]
            ys = [p[1] for p in valid_pairs]
            monotonicity, _ = spearmanr(xs, ys)
            monotonicity = float(monotonicity)
        else:
            monotonicity = float("nan")

        return dict(ic_mean=ic_mean, ic_std=ic_std, icir=icir,
                    ic_tstat=ic_tstat, monotonicity=monotonicity)

    # ──────────────────────── Step 2 转移层（核心）────────────────────────

    def _diagnose_transfer(
        self,
        signals: pd.DataFrame,
        weights: pd.DataFrame,
    ) -> dict:
        """
        TC = Spearman corr(h_actual, h_optimal)
        h_optimal ∝ z_i（信号幅度比例，无波动率调整）

        注：此 TC 衡量原始信号到持仓的转移保真度，适用于 TopN EW 和 QP 两种模式。
        与 diagnose_ic_ir_paradox.py Test-3 的 corr(alpha, active_weight) 为不同视角。

        ⚠ 跨策略比较注意：TC 的绝对值范围受持仓权重形式影响。
        - TopN EW（N=50/500）：权重为二值向量（0 或 1/N），TC 结构上限约 0.4–0.6，
          即使选股完美也无法趋近 1.0。
        - QP：权重连续，TC 可达 0.7–0.9。
        因此 TC_QP > TC_TopN EW 不等同于"QP 实现质量更好"，仅限同策略类型横向比较。
        """
        tc_list: list[float] = []
        amp_list: list[float] = []
        trunc_list: list[float] = []

        for t in signals.index:
            if t not in weights.index:
                continue
            z = signals.loc[t].dropna()
            h = weights.loc[t].dropna()

            # 只取信号有值的股票作为全域（TC 计算在信号宇宙内）
            if len(z) < 10:
                continue

            # 实际持仓：目标权重不为 0 的股票（TopN EW 只有 ~50 个非零值）
            h_nonzero = h[h.abs() > 1e-9]
            if len(h_nonzero) < 5:
                continue

            # 最优持仓：信号值直接作为代理（仅用 z 中与 h 有交集的范围计算）
            common = z.index.intersection(h.index)
            if len(common) < 10:
                continue
            z_c = z.loc[common]
            h_c = h.loc[common]

            # ── TC ──
            tc_val, _ = spearmanr(h_c.values, z_c.values)
            if not math.isnan(float(tc_val)):
                tc_list.append(float(tc_val))

            # ── 幅度保真度：|权重| vs |信号| 相关 ──
            amp_val, _ = spearmanr(h_c.abs().values, z_c.abs().values)
            if not math.isnan(float(amp_val)):
                amp_list.append(float(amp_val))

            # ── 截断损耗：未进入持仓的信号占总信号的比例 ──
            held_stocks = set(h_c[h_c.abs() > 1e-9].index)
            not_held = z_c[~z_c.index.isin(held_stocks)]
            total_signal_abs = z_c.abs().sum()
            if total_signal_abs > 0:
                trunc = float(not_held.abs().sum() / total_signal_abs)
                trunc_list.append(trunc)

        return dict(
            tc=float(np.mean(tc_list)) if tc_list else float("nan"),
            amplitude_fidelity=(float(np.mean(amp_list)) if amp_list
                                else float("nan")),
            truncation_loss=(float(np.mean(trunc_list)) if trunc_list
                             else float("nan")),
        )

    # ──────────────────────────── Step 3 广度层 ────────────────────────────

    def _diagnose_breadth(self, weights: pd.DataFrame) -> dict:
        """N_eff = 1 / Σw² （Herfindahl 指数倒数）"""
        n_eff_list: list[float] = []
        conc_list: list[float] = []
        n_hold_list: list[int] = []

        for t in weights.index:
            h = weights.loc[t]
            h_held = h[h.abs() > 1e-9].dropna()
            if len(h_held) == 0:
                continue
            w_norm = h_held.abs() / h_held.abs().sum()
            herf = float((w_norm ** 2).sum())
            n_eff_list.append(1.0 / herf if herf > 0 else float("nan"))
            conc_list.append(herf)
            n_hold_list.append(len(h_held))

        return dict(
            n_holdings=int(round(np.mean(n_hold_list))) if n_hold_list else 0,
            n_eff=(float(np.nanmean(n_eff_list)) if n_eff_list else float("nan")),
            concentration=(float(np.nanmean(conc_list)) if conc_list
                           else float("nan")),
        )

    # ──────────────────────────── Step 4 归因层 ────────────────────────────

    def _diagnose_attribution(
        self,
        weights: pd.DataFrame,
        returns: pd.DataFrame,
        signal_res: dict,
        transfer_res: dict,
        n_valid_periods: int,
        n_years: float,
    ) -> dict:
        """
        IR 理论 = ICIR × TC × √(periods_per_year)
        IR 实际 = 组合收益序列的均值/标准差 × √(periods_per_year)
        IR 损耗 = 1 - IR实际 / IR理论

        注：
        - IR_theoretical 基于 GK 定理的时间序列形式，假设各期 bet 独立（实际相关性
          使其偏乐观），仅用于跨轮次相对比较，不对应任何绝对标准。
        - ir_actual 使用原始组合收益（weights × fwd_ret，未扣除基准），与
          metrics_valid.parquet 里的超额 IR 口径不同，不可直接对比。
          两者均基于 fwd_ret_panel 原始收益，内部一致性满足，但绝对值偏高。
        - ir_loss_pct 理论上可为负（当原始组合 IR 超过简化理论值时），属正常现象，
          不代表"信号到持仓有增益"，而是 GK 简化公式低估理论值所致。
        """
        port_ret_list: list[float] = []
        for t in weights.index:
            if t not in returns.index:
                continue
            h = weights.loc[t].dropna()
            r = returns.loc[t].dropna()
            common = h.index.intersection(r.index)
            if len(common) == 0:
                continue
            port_ret_list.append(float((h.loc[common] * r.loc[common]).sum()))

        if not port_ret_list:
            return dict(ir_theoretical=float("nan"), ir_actual=float("nan"),
                        ir_loss_pct=float("nan"))

        port_arr = np.array(port_ret_list)
        port_std = float(port_arr.std(ddof=1))

        periods_per_year = n_valid_periods / n_years if n_years > 0 else 12.0
        sqrt_freq = math.sqrt(periods_per_year)

        if port_std > 0:
            ir_actual = float(port_arr.mean() / port_std * sqrt_freq)
        else:
            ir_actual = float("nan")

        icir = signal_res.get("icir", float("nan"))
        tc = transfer_res.get("tc", float("nan"))

        if not math.isnan(icir) and not math.isnan(tc):
            ir_theoretical = icir * tc * sqrt_freq
        else:
            ir_theoretical = float("nan")

        if (not math.isnan(ir_theoretical) and ir_theoretical != 0
                and not math.isnan(ir_actual)):
            ir_loss_pct = (1 - ir_actual / ir_theoretical) * 100
        else:
            ir_loss_pct = float("nan")

        return dict(ir_theoretical=ir_theoretical, ir_actual=ir_actual,
                    ir_loss_pct=ir_loss_pct)

    # ──────────────────────────── 主入口 ────────────────────────────

    def run(
        self,
        signals: pd.DataFrame,
        weights: pd.DataFrame,
        returns: pd.DataFrame,
        valid_start: pd.Timestamp = None,
        valid_end: pd.Timestamp = None,
    ) -> SignalQualityReport:
        """
        Args:
            signals: 截面 composite signal，index=rebalance_date，columns=ts_code
            weights: 目标持仓权重，index=rebalance_date，columns=ts_code
            returns: 前瞻收益面板，index=rebalance_date，columns=ts_code
            valid_start: 验证期起始日（None = 不过滤）
            valid_end:   验证期结束日（None = 不过滤）

        Returns:
            SignalQualityReport（计算失败时字段为 NaN，不抛异常）
        """
        try:
            # 过滤验证期
            if valid_start is not None:
                signals = signals.loc[signals.index >= valid_start]
                weights = weights.loc[weights.index >= valid_start]
            if valid_end is not None:
                signals = signals.loc[signals.index <= valid_end]
                weights = weights.loc[weights.index <= valid_end]

            n_valid = len(signals.index)
            if n_valid == 0:
                log.warning("signal_quality: 验证期内无数据，返回空报告")
                return SignalQualityReport()

            # 推断验证年数（用于年化）
            if valid_start and valid_end:
                n_years = (valid_end - valid_start).days / 365.25
            elif len(signals.index) >= 2:
                span = (signals.index[-1] - signals.index[0]).days
                n_years = max(span / 365.25, 0.1)
            else:
                n_years = 1.0

            sig_res = self._diagnose_signal(signals, returns)
            trans_res = self._diagnose_transfer(signals, weights)
            breadth_res = self._diagnose_breadth(weights)
            attr_res = self._diagnose_attribution(
                weights, returns, sig_res, trans_res, n_valid, n_years
            )

            return SignalQualityReport(
                ic_mean=sig_res["ic_mean"],
                ic_std=sig_res["ic_std"],
                icir=sig_res["icir"],
                ic_tstat=sig_res["ic_tstat"],
                monotonicity=sig_res["monotonicity"],
                tc=trans_res["tc"],
                amplitude_fidelity=trans_res["amplitude_fidelity"],
                truncation_loss=trans_res["truncation_loss"],
                n_holdings=breadth_res["n_holdings"],
                n_eff=breadth_res["n_eff"],
                concentration=breadth_res["concentration"],
                ir_theoretical=attr_res["ir_theoretical"],
                ir_actual=attr_res["ir_actual"],
                ir_loss_pct=attr_res["ir_loss_pct"],
                n_valid_periods=n_valid,
            )
        except Exception as e:
            log.error("signal_quality.run 失败: %s", e, exc_info=True)
            return SignalQualityReport()

    # ──────────────────────────── 报告输出 ────────────────────────────

    def format_report_section(self, r: SignalQualityReport) -> str:
        """生成 Markdown 格式的诊断报告节（追加到 self_check.md 用）"""

        def _f(v: float, dp: int = 3) -> str:
            return f"{v:.{dp}f}" if not math.isnan(v) else "N/A"

        def _flag(v: float, good: float, ok: float) -> str:
            if math.isnan(v):
                return "?"
            return "✓✓" if v >= good else ("✓" if v >= ok else "⚠")

        lines = [
            "",
            "## 信号实现质量诊断（Signal Quality Diagnostics）",
            "",
            f"验证期期数：{r.n_valid_periods}",
            "",
            "### Step 1 · 信号层",
            "",
            f"| 指标 | 值 | 评级 |",
            f"|------|----|------|",
            f"| IC 均值 | {_f(r.ic_mean, 4)} | — |",
            f"| IC 标准差 | {_f(r.ic_std, 4)} | — |",
            f"| ICIR | {_f(r.icir)} | {_flag(r.icir, 0.5, 0.3)} |",
            f"| IC t-stat | {_f(r.ic_tstat, 2)} | — |",
            f"| 分层单调性 | {_f(r.monotonicity)} | {_flag(r.monotonicity, MONO_EXCELLENT, MONO_OK)} |",
            "",
            "### Step 2 · 转移层 ★核心",
            "",
            f"| 指标 | 值 | 评级 |",
            f"|------|----|------|",
            f"| Transfer Coef (TC) | {_f(r.tc)} | {_flag(r.tc, TC_EXCELLENT, TC_OK)} |",
            f"| 幅度保真度 | {_f(r.amplitude_fidelity)} | — |",
            (f"| 截断信号损耗 | {r.truncation_loss:.1%} | — |"
             if not math.isnan(r.truncation_loss) else "| 截断信号损耗 | N/A | — |"),
            "",
            "### Step 3 · 广度层",
            "",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| 平均持仓数 | {r.n_holdings} |",
            f"| N_eff (Herfindahl⁻¹) | {_f(r.n_eff, 1)} |",
            (f"| N_eff / 持仓数 | {r.n_eff / r.n_holdings:.1%} |"
             if r.n_holdings > 0 and not math.isnan(r.n_eff)
             else "| N_eff / 持仓数 | N/A |"),
            "",
            "### Step 4 · IR 归因",
            "",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| IR 理论（ICIR×TC×√freq） | {_f(r.ir_theoretical)} |",
            f"| IR 实际（原始组合，非超额） | {_f(r.ir_actual)} |",
            (f"| IR 损耗 | {r.ir_loss_pct:.1f}% |"
             if not math.isnan(r.ir_loss_pct) else "| IR 损耗 | N/A |"),
            "| ⚠ 注 | ir_actual ≠ 超额IR；负损耗属正常（GK简化公式所致） |",
            "",
            "### 自动诊断结论",
            "",
        ]

        # 诊断结论逻辑
        if not math.isnan(r.tc) and r.tc < TC_OK:
            lines.append(
                f"> ⚠ **根因 = 转移损耗**。TC={r.tc:.3f} < {TC_OK}，"
                "信号在持仓构建环节损耗严重。优先改连续权重或 PPP。"
            )
        elif (r.n_holdings > 0 and not math.isnan(r.n_eff)
              and r.n_eff / r.n_holdings < EFF_RATIO_OK):
            ratio = r.n_eff / r.n_holdings
            lines.append(
                f"> ⚠ **根因 = 广度虚高**。N_eff/持仓={ratio:.0%}，"
                "持仓内部高度相关，需行业/风格中性化。"
            )
        elif not math.isnan(r.monotonicity) and r.monotonicity < MONO_OK:
            lines.append(
                f"> ⚠ **根因 = 信号单调性差**（{r.monotonicity:.3f}），"
                "可能存在非线性或区域反转。"
            )
        elif not math.isnan(r.tc):
            lines.append(
                f"> ✓ TC={r.tc:.3f}，转移与广度健康。主要杠杆在提升 IC 或扩大独立 bet 数。\n"
                "> ℹ TC 阈值适用于同类型策略横向比较：TopN EW 因二值权重结构上限约 0.4–0.6，"
                "QP 连续权重上限约 0.7–0.9，两者不宜直接对比。"
            )
        else:
            lines.append("> ? 数据不足，无法给出诊断结论。")

        return "\n".join(lines)
```

---

## 3. Step 2 — `src/pipeline/stages.py` 新增 `run_diagnosis_stage()`

**位置**：在文件末尾的 `_write_signal_metadata` 函数之前追加，不修改任何现有函数。

**插入点**（以下注释块之前）：

```python
def _write_signal_metadata(signal_dir: Path, spec, extra: dict) -> None:
```

**追加代码**：

```python
# ---------------------------------------------------------------------------
# Diagnosis stage（信号实现质量诊断）
# ---------------------------------------------------------------------------

def run_diagnosis_stage(
    spec,
    run_dir: Path,
    data_proc=None,
) -> dict:
    """
    运行信号实现质量诊断，产物写到 run_dir/reports/。

    输入（从已完成的 signal / portfolio 阶段读取）：
        signal/composite.parquet          → 截面信号
        portfolio/target_weights.parquet  → 实际持仓权重
        data_proc/fwd_ret_panel.parquet   → 前瞻收益

    输出：
        reports/signal_quality_report.json  ← 结构化 JSON，供 compare_runs 读取
        reports/self_check.md               ← 追加诊断节（文件已存在时）

    本函数为非阻断设计：调用方应自行 try/except，失败不影响 run 状态。

    Returns:
        dict with key "signal_quality_report" → Path（成功时），或空 dict（失败时）
    """
    import json as _json

    import pandas as pd
    from src import config as cfg
    from src.evaluation.signal_quality import SignalToPositionDiagnostics

    _data_proc = data_proc or cfg.DATA_PROC

    signal_path  = run_dir / "signal" / "composite.parquet"
    weights_path = run_dir / "portfolio" / "target_weights.parquet"
    fwd_path     = _data_proc / "fwd_ret_panel.parquet"
    reports_dir  = run_dir / "reports"
    out_json     = reports_dir / "signal_quality_report.json"
    out_md       = reports_dir / "self_check.md"

    # 检查必须的输入文件
    missing = [p for p in [signal_path, weights_path, fwd_path] if not p.exists()]
    if missing:
        log.warning("diagnosis: 缺少输入文件，跳过诊断: %s",
                    [str(m) for m in missing])
        return {}

    signals = pd.read_parquet(signal_path)
    weights = pd.read_parquet(weights_path)
    fwd_ret = pd.read_parquet(fwd_path)

    # 确保 index 为 Timestamp
    signals.index = pd.DatetimeIndex(signals.index)
    weights.index = pd.DatetimeIndex(weights.index)
    fwd_ret.index = pd.DatetimeIndex(fwd_ret.index)

    diag = SignalToPositionDiagnostics(min_stocks=10)
    report = diag.run(
        signals=signals,
        weights=weights,
        returns=fwd_ret,
        valid_start=pd.Timestamp(cfg.VALID_START),
        valid_end=pd.Timestamp(cfg.VALID_END),
    )

    # 写 JSON
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        _json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("signal_quality_report.json 已写入: %s", out_json)

    # 追加到 self_check.md（若存在）
    if out_md.exists():
        section = diag.format_report_section(report)
        existing = out_md.read_text(encoding="utf-8")
        # 避免重复追加（幂等）
        if "Signal Quality Diagnostics" not in existing:
            out_md.write_text(existing + section, encoding="utf-8")
            log.info("诊断节已追加到 self_check.md")

    return {"signal_quality_report": out_json}
```

---

## 4. Step 3 — `scripts/run_experiment.py` 追加调用

**位置**：在 `write_self_check_md` 的 try/except 块之后追加，共 8 行。

**找到这段代码作为插入点**（现有最后一段 try/except）：

```python
    if "backtest" in active_stages:
        try:
            log.info("── 生成 reports/self_check.md ───────────────")
            write_self_check_md(spec, ctx.run_dir)
        except Exception as e:
            log.warning("self_check.md 写入失败（不阻断）: %s", e)
```

**在其后追加**：

```python
    # ── 信号实现质量诊断（非阻断，依赖 self_check.md 已存在）──────────────
    if "backtest" in active_stages:
        try:
            log.info("── 生成 reports/signal_quality_report.json ──")
            run_diagnosis_stage(spec, ctx.run_dir, data_proc=cfg.DATA_PROC)
        except Exception as e:
            log.warning("signal quality diagnosis 失败（不阻断）: %s", e)
```

`run_diagnosis_stage` 已在下方顶部 import 块中导入，此处不再重复局部 import。

**同时**：在文件顶部已有的 import 块中，将 `run_diagnosis_stage` 加入现有的 stages 导入语句。  
找到：

```python
    from src.pipeline.stages import (
        run_signal_stage, run_portfolio_stage, run_backtest_stage,
        write_self_check_md,
    )
```

修改为：

```python
    from src.pipeline.stages import (
        run_signal_stage, run_portfolio_stage, run_backtest_stage,
        write_self_check_md, run_diagnosis_stage,
    )
```

> **注意**：这个 import 在 `try` 块**外**（第 205 行区域，`try` 从第 211 行才开始）。
> 直接将 `run_diagnosis_stage` 追加到此 import 语句末尾即可，无需任何特殊处理。

---

## 5. Step 4 — `src/pipeline/compare.py` 新增 TC 相关列

### 5.1 新增 helper 函数

在文件末尾（`_compute_paired_p` 函数之后）追加：

```python
def _load_signal_quality(run_dir: Path) -> dict:
    """
    从 reports/signal_quality_report.json 读取信号实现质量指标。
    旧 run 目录没有此文件时返回全 NaN，不影响其他列。
    """
    nan = float("nan")
    empty = {"tc_mean": nan, "n_eff": nan, "ir_loss_pct": nan}
    report_path = run_dir / "reports" / "signal_quality_report.json"
    if not report_path.exists():
        return empty
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        tc_raw = data.get("tc", nan)
        n_eff_raw = data.get("n_eff", nan)
        ir_loss_raw = data.get("ir_loss_pct", nan)

        def _safe(v):
            try:
                f = float(v)
                return f if not math.isnan(f) else nan
            except (TypeError, ValueError):
                return nan

        return {
            "tc_mean": round(_safe(tc_raw), 3),
            "n_eff": round(_safe(n_eff_raw), 1),
            "ir_loss_pct": round(_safe(ir_loss_raw), 1),
        }
    except Exception:
        return empty
```

### 5.2 在 `load_run_metrics()` 中调用

找到现有的 IC stats 加载行：

```python
    # IC stats（验证期，从 composite.parquet + fwd_ret_panel 动态计算）
    row.update(_compute_ic_stats(run_dir))
```

在其后追加一行：

```python
    # 信号实现质量诊断（TC、N_eff 等，从 reports/signal_quality_report.json 读取）
    row.update(_load_signal_quality(run_dir))
```

### 5.3 在 `compare_runs()` 的 `col_order` 中加入新列

找到：

```python
        "IC_mean", "IC_IR", "IC_t", "IC_p",
        "fallback_L0_cnt", "fallback_L1_cnt", "fallback_L2_cnt", "fallback_L3_cnt",
```

修改为：

```python
        "IC_mean", "IC_IR", "IC_t", "IC_p",
        "tc_mean", "n_eff", "ir_loss_pct",
        "fallback_L0_cnt", "fallback_L1_cnt", "fallback_L2_cnt", "fallback_L3_cnt",
```

---

## 6. 数据流与接口确认

```
run_experiment.py 执行顺序（修改后）
  │
  ├─ signal 阶段  →  signal/composite.parquet
  │                  (shape: n_dates × n_stocks, float64, index=Timestamp)
  │
  ├─ portfolio 阶段  →  portfolio/target_weights.parquet
  │                     (shape: n_dates × n_stocks, float64, index=Timestamp)
  │                     非零值数量 ≈ topn（TopN EW 约 50，QP 约 300~500）
  │
  ├─ backtest 阶段  →  backtest/metrics_valid.parquet
  │
  ├─ manifest + RUN_FINISHED.json
  │
  ├─ write_self_check_md()  →  reports/self_check.md（已有）
  │
  └─ run_diagnosis_stage()（新增）
       输入: signal/composite.parquet
             portfolio/target_weights.parquet
             data/processed/fwd_ret_panel.parquet
             cfg.VALID_START / cfg.VALID_END
       输出: reports/signal_quality_report.json
             reports/self_check.md（追加节，幂等）

compare_runs.py 读取链
  load_run_metrics(run_id)
    ├─ _compute_ic_stats()        → IC_mean, IC_IR, IC_t, IC_p
    ├─ _load_signal_quality()（新）→ tc_mean, n_eff, ir_loss_pct
    └─ ...（其他现有字段不变）
```

---

## 7. 边界条件与错误处理

| 场景 | 处理方式 |
|------|---------|
| `signal/composite.parquet` 不存在（`--from-stage portfolio` 且外部信号 run 路径不同） | `run_diagnosis_stage` 检测到缺文件后 `log.warning` 并返回 `{}`，上层 try/except 不感知 |
| 验证期内信号/权重日期无交集 | `SignalToPositionDiagnostics.run` 返回全 NaN 的 `SignalQualityReport` |
| 单期有效股票数 < `min_stocks`（默认 10，与 `compare.py` 保持一致） | 跳过该期，不贡献到均值；若所有期均跳过则返回 NaN |
| `fwd_ret_panel.parquet` 不存在 | 与上方第一条相同处理逻辑 |
| `signal_quality_report.json` 已存在（重跑诊断） | `out_json.write_text(...)` 直接覆盖（符合期望） |
| `self_check.md` 中已有诊断节 | `"Signal Quality Diagnostics" not in existing` 检查防重复追加 |
| 旧 run 目录（无 `signal_quality_report.json`） | `_load_signal_quality` 返回全 NaN，`compare_runs` 对应列显示 `—` |

---

## 8. 执行顺序与验证方法

### 8.1 实施顺序（严格按顺序，避免引用未存在的函数）

```
1. 新建 src/evaluation/signal_quality.py
2. 修改 src/pipeline/stages.py（新增 run_diagnosis_stage）
3. 修改 scripts/run_experiment.py（追加调用 + 更新 import）
4. 修改 src/pipeline/compare.py（新增 _load_signal_quality + 更新两处）
```

### 8.2 快速验证（不跑完整实验）

**验证 Step 1**（新模块独立可 import）：
```powershell
python -c "from src.evaluation.signal_quality import SignalToPositionDiagnostics, SignalQualityReport; print('OK')"
```

**验证 Step 2**（新函数可 import）：
```powershell
python -c "from src.pipeline.stages import run_diagnosis_stage; print('OK')"
```

**验证 Step 3**（run_experiment.py 语法无误）：
```powershell
python -m py_compile scripts/run_experiment.py && echo "syntax OK"
```

**验证 Step 4**（对现有 run 补跑诊断并检查列）：
```powershell
# 对最近的 rolling48_topn50_ew run 单独跑诊断
python -c "
from pathlib import Path
from src.pipeline.stages import run_diagnosis_stage
from src.pipeline.contracts import ExperimentSpec
import sys

run_dir = Path('runs/train_valid/20260529_081232__rolling48_ridge_topn50_ew')
# 用最简单的方式验证：直接读 spec 然后调用
spec_path = Path('configs/pipelines/rolling48_ridge_topn50_ew.py')
if spec_path.exists():
    spec = ExperimentSpec.from_config_file(spec_path)
    result = run_diagnosis_stage(spec, run_dir)
    print('产物:', result)
else:
    print('spec 不存在，跳过')
"
```

**验证 Step 4 的 compare_runs 列**：
```powershell
python -m scripts.compare_runs --run-ids 20260529_081232__rolling48_ridge_topn50_ew 20260529_034947__frozen_baseline_icir_topn50_ew
# 输出中应出现 tc_mean / n_eff / ir_loss_pct 列
```

### 8.3 完整流水线验证（下次 run_experiment.py 后）

新实验完成后检查：
```powershell
# 1. reports/signal_quality_report.json 是否生成
Get-Content "runs/train_valid/<new_run_id>/reports/signal_quality_report.json"

# 2. self_check.md 是否追加了诊断节
Select-String "Signal Quality" "runs/train_valid/<new_run_id>/reports/self_check.md"

# 3. compare_runs 输出是否包含三列
python -m scripts.compare_runs | Select-String "tc_mean|rolling48"
```

---

## 9. 暂不实现的部分（超出当前范围）

| 特性 | 原因 | 待实现条件 |
|------|------|-----------|
| 波动率调整的最优持仓（`h∝z/σ²`） | 无每股日频波动率面板 | 构建 `sigma_panel.parquet` 后升级 `_diagnose_transfer` |
| `DiagnosisTracker` 跨轮次追踪 | `compare_runs.py` 已覆盖核心需求 | 若需要可视化演化曲线时添加 |
| 行业/风格暴露集中度诊断 | 需要行业映射表和风格因子载荷 | 作为后续广度层增强 |
| 对旧 run 批量补生成 `signal_quality_report.json` | 当前无需求 | 新增 `scripts/backfill_diagnosis.py` 时实现 |

---

## 10. 影响范围确认

**不受影响的文件**：
- `src/factors/` — 因子构建逻辑，完全不涉及
- `src/portfolio/optimizer.py` — 优化器，完全不涉及
- `src/backtest/` — 回测引擎，完全不涉及
- `registry/*.json` — 注册表，完全不涉及
- `tests/` — 现有测试不受影响（新模块无现有测试，需后续补充）
- `configs/pipelines/*.py` — spec 文件，完全不涉及

**受影响的文件**（4 个）：
- `src/evaluation/signal_quality.py`（新建）
- `src/pipeline/stages.py`（末尾追加约 60 行）
- `scripts/run_experiment.py`（追加约 8 行 + 更新 1 处 import）
- `src/pipeline/compare.py`（追加约 25 行 + 更新 2 处）

---

*计划结束 ｜ 实施前请确认各验证命令的期望输出，完成后更新 CLAUDE.md §1.1 的"待解决已知问题"以反映 TC 诊断已上线。*
