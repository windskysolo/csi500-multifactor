"""
src/evaluation/shift_test.py — 时间错位测试（防未来函数的辅助检验）
==========================================================

原理：
  若因子没有使用未来信息，用「上期因子值」预测「当期收益」的能力（错位 IC）
  应显著弱于用「当期因子值」预测「当期收益」（原始 IC）。

  对高持久性因子（价值、质量）：错位 IC 可能仍有效，不能单凭此判断未来函数。
  对低持久性因子（动量、波动率）：错位 IC 应接近 0，否则强烈怀疑数据错误。

此测试是辅助工具，不能替代 PIT 单元测试（tests/test_pit.py）。

诊断等级说明：
  strong_drop   drop > 0.5   — 正常，因子有明显短期预测力
  weak_drop     0.1 ~ 0.5   — 正常，但持久性较高（高质量/价值因子常见）
  no_drop       -0.1 ~ 0.1  — 中性，高持久性因子可接受，低持久性因子需关注
  reverse       drop < -0.1  — 异常：错位反而更强，强烈怀疑未来函数或数据错误

lead_1 检测（future leakage probe，Phase 3 升级为分级阈值）：
  将因子向前移动 1 期（shift(-1)），即"下一期因子预测当期收益"。
  计算 lead_ratio = |lead1_ic_ir| / |orig_ic_ir|，分三级处理：
    < 1.5x   → 通过（轻微持续性，正常）
    1.5–2.0x → lead_warn=True（待议，不自动排除）
    ≥ 2.0x   → lead_reject=True & warning（拒绝，Gate 2 排除）

结果解读指引：
  ic_ir_drop  > 0  → 原始比错位更强（正常，因子本身有预测力）
  ic_ir_drop ≈ 0   → 原始与错位强度相当（高持久性因子正常，低持久性要警惕）
  ic_ir_drop < 0   → 错位反而更强（高度怀疑存在未来函数或数据对齐错误）
  lead_ratio ≥ 2.0x → lead_reverse/lead_reject=True（数据已含未来信息，报告为高危警告）
  lead_ratio 1.5–2.0x → lead_warn=True（待议区，结合其他指标人工决定）
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from src.evaluation.ic_analysis import compute_ic_series, ic_summary

log = logging.getLogger(__name__)

_DIAGNOSIS_STRONG_DROP   = "strong_drop"   # drop > 0.5 — 正常
_DIAGNOSIS_WEAK_DROP     = "weak_drop"     # 0.1 < drop <= 0.5 — 可接受
_DIAGNOSIS_NO_DROP       = "no_drop"       # -0.1 <= drop <= 0.1 — 中性（需关注）
_DIAGNOSIS_REVERSE       = "reverse"       # drop < -0.1 — 异常


def _safe_abs_ir(val) -> float:
    return 0.0 if pd.isna(val) else abs(float(val))


def _compute_shift_stats(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    shift: int,
) -> dict:
    """对 factor_panel 做 shift，返回错位后的 IC 统计量。"""
    shifted = factor_panel.shift(shift)
    ic_s = compute_ic_series(shifted, fwd_ret_panel)
    return ic_summary(ic_s)


def run_shift_test(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    shift: int = 1,
) -> dict:
    """
    对单个因子执行多档时间错位测试。

    诊断包含：
    - lag_1 / lag_2：因子滞后 1/2 期预测当期收益（检测因子持久性）
    - lead_1：因子超前 1 期（shift(-1)）预测当期收益（检测未来数据泄露）

    Args:
        factor_panel:  行=调仓日, 列=ts_code 的因子面板
        fwd_ret_panel: forward return 面板（来自 compute_forward_returns）
        shift:         主错位期数（默认 1，即 lag_1）
    Returns:
        dict，包含：
          original     原始 IC 统计量（dict，来自 ic_summary）
          lagged       lag_1 错位后 IC 统计量（dict）
          shifted      与 lagged 相同（向后兼容别名）
          lag2         lag_2 错位后 IC 统计量（dict）
          lead1        lead_1 超前 1 期 IC 统计量（dict）
          lead_ratio   |lead1_ic_ir| / |orig_ic_ir|（orig 过弱时为 0.0）
          lead_warn    布尔，True 表示 lead_ratio 落入 [1.5, 2.0) 待议区
          lead_reject  布尔，True 表示 lead_ratio ≥ 2.0 → Gate 2 排除
          ic_ir_drop   |orig_ic_ir| - |lag1_ic_ir|（正数表示正常，负数警示）
          diagnosis    诊断等级字符串（strong_drop/weak_drop/no_drop/reverse）
          lead_reverse 布尔，等同于 lead_reject（向后兼容别名）
          warning      若 ic_ir_drop < -0.1 或 lead_reject=True，非空警告字符串
    """
    ic_orig = compute_ic_series(factor_panel, fwd_ret_panel)
    stats_orig = ic_summary(ic_orig)

    stats_lag1 = _compute_shift_stats(factor_panel, fwd_ret_panel, shift)
    stats_lag2 = _compute_shift_stats(factor_panel, fwd_ret_panel, shift + 1)
    # lead_1: shift(-1) 用"下一期因子"预测"当期收益"，正常因子应无预测力
    stats_lead1 = _compute_shift_stats(factor_panel, fwd_ret_panel, -1)

    ir_orig  = _safe_abs_ir(stats_orig.get("ic_ir"))
    ir_lag1  = _safe_abs_ir(stats_lag1.get("ic_ir"))
    ir_lead1 = _safe_abs_ir(stats_lead1.get("ic_ir"))
    ic_ir_drop = float(ir_orig - ir_lag1)

    # 诊断等级
    if ic_ir_drop > 0.5:
        diagnosis = _DIAGNOSIS_STRONG_DROP
    elif ic_ir_drop > 0.1:
        diagnosis = _DIAGNOSIS_WEAK_DROP
    elif ic_ir_drop >= -0.1:
        diagnosis = _DIAGNOSIS_NO_DROP
    else:
        diagnosis = _DIAGNOSIS_REVERSE

    # Gate 2 分级 lead_ratio 检测（Phase 3 修正）
    # ir_orig ≤ 0.05 时因子本身过弱，lead_ratio 无统计意义，置 0 不触发警告。
    lead_ratio  = round(ir_lead1 / ir_orig, 4) if ir_orig > 0.05 else 0.0
    lead_warn   = 1.5 <= lead_ratio < 2.0   # ⚠️ 待议区，不自动排除
    lead_reject = lead_ratio >= 2.0          # ❌ 拒绝（原 lead_reverse 升级为此）
    lead_reverse = lead_reject               # 向后兼容别名

    warning = ""
    if diagnosis == _DIAGNOSIS_REVERSE:
        warning = (
            f"[lag_reverse] 错位后 IC_IR（{ir_lag1:.3f}）显著高于原始（{ir_orig:.3f}），"
            f"drop={ic_ir_drop:.3f} < -0.1。排查：① PIT 时间对齐 ② forward return 口径 "
            f"③ 数据拼接错误。"
        )
        log.warning("时间错位测试警告：%s", warning)
    if lead_reject:
        # lead_ratio ≥ 2.0x：高度怀疑未来数据，触发 warning（会被 Gate 2 排除）
        lead_msg = (
            f"[lead_ratio={lead_ratio:.2f}x ≥ 2.0] 超前因子 IC_IR（{ir_lead1:.3f}）"
            f"是原始（{ir_orig:.3f}）的 {lead_ratio:.2f}x，"
            "高度怀疑因子已包含未来数据，请立即排查 PIT 对齐。"
        )
        warning = f"{warning} {lead_msg}".strip() if warning else lead_msg
        log.warning("shift_test lead_ratio≥2.0x 拒绝：%s", lead_msg)
    elif lead_warn:
        # lead_ratio 1.5–2.0x：标记为待议，不触发 warning，不自动排除
        log.info(
            "shift_test lead_warn: lead_ratio=%.2fx（1.5–2.0x 待议区，不自动排除）"
            " orig_ic_ir=%.3f lead1_ic_ir=%.3f",
            lead_ratio, ir_orig, ir_lead1,
        )

    log.info(
        "shift_test: orig=%.3f lag1=%.3f lag2=%.3f lead1=%.3f "
        "lead_ratio=%.2fx drop=%.3f [%s]%s",
        ir_orig,
        ir_lag1,
        _safe_abs_ir(stats_lag2.get("ic_ir")),
        ir_lead1,
        lead_ratio,
        ic_ir_drop,
        diagnosis,
        " ⚠️" if warning else (" ℹ️lead_warn" if lead_warn else ""),
    )

    return {
        "original":     stats_orig,
        "lagged":       stats_lag1,
        "shifted":      stats_lag1,   # 向后兼容别名
        "lag2":         stats_lag2,
        "lead1":        stats_lead1,
        "lead_ratio":   lead_ratio,
        "lead_warn":    lead_warn,
        "lead_reject":  lead_reject,
        "ic_ir_drop":   ic_ir_drop,
        "diagnosis":    diagnosis,
        "lead_reverse": lead_reverse,  # 向后兼容别名，等同于 lead_reject
        "warning":      warning,
    }


def batch_shift_test(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    shift: int = 1,
) -> pd.DataFrame:
    """
    对多个因子批量执行时间错位测试，汇总结果。

    Args:
        factor_panels: dict，key=因子名，value=因子面板
        fwd_ret_panel: forward return 面板
        shift:         主错位期数
    Returns:
        DataFrame，每行一个因子，列包含原始和错位的 IC_IR、drop 值及诊断等级
    """
    rows = []
    for name, panel in factor_panels.items():
        res = run_shift_test(panel, fwd_ret_panel, shift)
        rows.append({
            "factor":           name,
            "orig_ic_ir":       res["original"].get("ic_ir"),
            "lag1_ic_ir":       res["lagged"].get("ic_ir"),
            "lag2_ic_ir":       res["lag2"].get("ic_ir"),
            "lead1_ic_ir":      res["lead1"].get("ic_ir"),
            "lead_ratio":       res["lead_ratio"],
            "lead_warn":        res["lead_warn"],    # 1.5–2.0x 待议，不自动排除
            "lead_reject":      res["lead_reject"],  # ≥2.0x 拒绝
            "ic_ir_drop":       res["ic_ir_drop"],
            "diagnosis":        res["diagnosis"],
            "lead_reverse":     res["lead_reverse"],  # 向后兼容别名
            # 保留旧列名 warning/lagged_ic_ir 以兼容 run_factor_evaluation.py
            "lagged_ic_ir":     res["lagged"].get("ic_ir"),
            "warning":          bool(res["warning"]),
        })

    return pd.DataFrame(rows).set_index("factor").sort_values(
        "ic_ir_drop", ascending=True  # 问题最大的（drop 最小/最负）排最前
    )
