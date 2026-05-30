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

import logging
import math
from dataclasses import asdict, dataclass
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
        min_stocks: 单期最少有效股票数，不足时跳过该期（与 compare.py 阈值一致）
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
        valid_start: Optional[pd.Timestamp] = None,
        valid_end: Optional[pd.Timestamp] = None,
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
            # 数据为空时提前返回，避免 RangeIndex 与 Timestamp 比较报错
            if signals.empty:
                log.warning("signal_quality: 输入信号为空，返回空报告")
                return SignalQualityReport()

            # 过滤验证期（此时 index 必须为 DatetimeIndex）
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
