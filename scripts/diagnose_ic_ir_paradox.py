"""
scripts/diagnose_ic_ir_paradox.py
===================================
诊断 IC_IR 与组合 IR 反相关的根本原因。

四条归因线：
  Test-1  alpha 分布：z-score 后量级是否一致？（排除"量级压缩"假设）
  Test-2  信号-收益序列相关：IC(t) vs 月超额收益(t) 是否同向？（直接发现"信号被 QP 反转"）
  Test-3  Transfer Coefficient（传导系数）：alpha 与 (w* - w_b) 的相关性（QP 约束摩擦）
  Test-4  因子系数方向：expanding Ridge 的各因子系数在验证期是否有反转？（piotroski_f 假设）

全部基于已有 run 输出，不运行新实验。

用法：
    python -m scripts.diagnose_ic_ir_paradox --output-dir check/0529
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量：run 路径
# ---------------------------------------------------------------------------

BASE = Path(__file__).parents[1] / "runs" / "train_valid"

RUNS = {
    "expanding": "20260527_141545__baseline_expanding_ridge_te6_lam0050",
    "rolling48":  "20260527_142056__challenger_rolling48_te6_lam0050",
    "rolling36":  "20260527_141843__challenger_rolling36_te6_lam0050",
    "rolling60":  "20260527_142318__challenger_rolling60_te6_lam0050",
    "decay_hl24": "20260528_141041__challenger_decay_ridge_hl24_te6_lam0050",
    "decay_hl36": "20260528_141324__challenger_decay_ridge_hl36_te6_lam0050",
    "decay_hl48": "20260528_141729__challenger_decay_ridge_hl48_te6_lam0050",
}

VALID_START = pd.Timestamp("2021-01-01")
VALID_END   = pd.Timestamp("2022-12-31")

FACTOR_ORDER = [
    "turn_20d", "ivol_60d", "piotroski_f", "hk_hold_ratio", "roe_delta_3q",
    "hk_hold_chg", "amihud", "q_roe", "roe_delta", "ep_ttm", "rev_yoy",
    "cfp", "gross_margin_trend", "holder_chg", "analyst_eps_revision",
    "gross_margin", "high_52w", "rev_acceleration",
]


# ---------------------------------------------------------------------------
# 数据加载工具
# ---------------------------------------------------------------------------

def _load(run_key: str, subpath: str) -> pd.DataFrame | None:
    p = BASE / RUNS[run_key] / subpath
    if not p.exists():
        log.warning("文件不存在: %s", p)
        return None
    df = pd.read_parquet(p)
    return df


def _valid_dates(df: pd.DataFrame) -> pd.DataFrame:
    """保留验证期日期（index 为 DatetimeIndex）。"""
    idx = pd.to_datetime(df.index)
    return df.loc[(idx >= VALID_START) & (idx <= VALID_END)]


def _load_fwd_ret() -> pd.DataFrame:
    p = Path(__file__).parents[1] / "data" / "processed" / "fwd_ret_panel.parquet"
    return pd.read_parquet(p)


# ---------------------------------------------------------------------------
# Test-1: alpha 分布分析
# ---------------------------------------------------------------------------

def test1_alpha_distribution() -> pd.DataFrame:
    """
    比较各方法在验证期的 alpha（composite signal）截面分布。
    z-score 后 std≈1 是预期，若某方法 std 明显偏小说明量级被压缩。
    """
    rows = []
    for key in RUNS:
        sig = _load(key, "signal/composite.parquet")
        if sig is None:
            continue
        sig_v = _valid_dates(sig)
        if sig_v.empty:
            continue
        # 每期截面展平
        vals = sig_v.values.flatten()
        vals = vals[~np.isnan(vals)]
        rows.append({
            "run":      key,
            "n_obs":    len(vals),
            "mean":     np.mean(vals),
            "std":      np.std(vals),
            "p5":       np.percentile(vals, 5),
            "p25":      np.percentile(vals, 25),
            "p75":      np.percentile(vals, 75),
            "p95":      np.percentile(vals, 95),
            "skew":     stats.skew(vals),
            "kurt":     stats.kurtosis(vals),
            # 极端值：|alpha| > 2 的比例（高分散 → 更强仓位偏离）
            "pct_abs_gt2": np.mean(np.abs(vals) > 2),
        })
    return pd.DataFrame(rows).set_index("run")


# ---------------------------------------------------------------------------
# Test-2: IC(t) vs 月超额收益(t) 序列相关
# ---------------------------------------------------------------------------

def test2_ic_vs_excess_return() -> pd.DataFrame:
    """
    逐月计算：
      - IC(t)：composite alpha 与 fwd_ret 的 Spearman 秩相关
      - excess_ret(t)：portfolio 月超额收益（target_weights @ fwd_ret - benchmark）
    若 IC(t) 与 excess_ret(t) 负相关 → 信号在 QP 流程中被"反转"
    """
    fwd = _load_fwd_ret()
    rows = []

    for key in RUNS:
        sig = _load(key, "signal/composite.parquet")
        w   = _load(key, "portfolio/target_weights.parquet")
        wb  = _load(key, "portfolio/baseline_weights.parquet")
        if any(x is None for x in [sig, w, wb]):
            continue

        sig_v = _valid_dates(sig)
        w_v   = _valid_dates(w)
        wb_v  = _valid_dates(wb)
        if sig_v.empty:
            continue

        for dt in sig_v.index:
            dt_ts = pd.Timestamp(dt)
            if dt_ts not in fwd.index:
                continue

            alpha_row = sig_v.loc[dt].dropna()
            ret_row   = fwd.loc[dt_ts]

            # IC：alpha 与 fwd_ret 的 Spearman 相关
            common = alpha_row.index.intersection(ret_row.dropna().index)
            if len(common) < 30:
                continue
            ic_val, _ = stats.spearmanr(alpha_row[common], ret_row[common])

            # 月超额收益：portfolio - benchmark
            if dt_ts in w_v.index and dt_ts in wb_v.index:
                w_row  = w_v.loc[dt_ts].reindex(ret_row.index).fillna(0)
                wb_row = wb_v.loc[dt_ts].reindex(ret_row.index).fillna(0)
                # forward return 的 NaN 用 0 填（不持仓股票）
                ret_filled = ret_row.fillna(0)
                excess = float((w_row - wb_row) @ ret_filled)
            else:
                excess = np.nan

            rows.append({"run": key, "date": dt_ts, "ic": ic_val, "excess_ret": excess})

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)

    # 每个 run 的汇总
    summary = []
    for key, g in df.groupby("run"):
        g = g.dropna(subset=["ic", "excess_ret"])
        if len(g) < 5:
            continue
        rho, p = stats.spearmanr(g["ic"], g["excess_ret"])
        summary.append({
            "run":              key,
            "n_periods":        len(g),
            "ic_mean":          g["ic"].mean(),
            "ic_ir":            g["ic"].mean() / (g["ic"].std() + 1e-9),
            "excess_ret_mean":  g["excess_ret"].mean(),
            "ic_vs_ret_spearman_rho": rho,
            "ic_vs_ret_p":      p,
        })
    return pd.DataFrame(summary).set_index("run")


# ---------------------------------------------------------------------------
# Test-3: Transfer Coefficient（传导系数）
# ---------------------------------------------------------------------------

def test3_transfer_coefficient() -> pd.DataFrame:
    """
    TC(t) = Pearson corr(alpha_t, w_t* - w_b_t)
    衡量 QP 对信号的传导效率（1=完美传导，0=完全压制）。
    若 expanding 的 TC 系统低于 rolling → QP 约束"抵制"expanding 信号
    """
    rows = []
    for key in RUNS:
        sig = _load(key, "signal/composite.parquet")
        w   = _load(key, "portfolio/target_weights.parquet")
        wb  = _load(key, "portfolio/baseline_weights.parquet")
        if any(x is None for x in [sig, w, wb]):
            continue

        sig_v = _valid_dates(sig)
        tc_list = []

        for dt in sig_v.index:
            dt_ts = pd.Timestamp(dt)
            if dt_ts not in w.index or dt_ts not in wb.index:
                continue

            alpha = sig_v.loc[dt].dropna()
            w_row  = w.loc[dt_ts].reindex(alpha.index).fillna(0)
            wb_row = wb.loc[dt_ts].reindex(alpha.index).fillna(0)
            active = w_row - wb_row

            common = alpha.index
            a_vec = alpha[common].values
            d_vec = active[common].values

            if np.std(a_vec) < 1e-9 or np.std(d_vec) < 1e-9:
                continue

            tc_val, _ = stats.pearsonr(a_vec, d_vec)
            tc_list.append(tc_val)

        if not tc_list:
            continue
        rows.append({
            "run":    key,
            "n":      len(tc_list),
            "tc_mean": np.mean(tc_list),
            "tc_std":  np.std(tc_list),
            "tc_min":  np.min(tc_list),
            "tc_max":  np.max(tc_list),
        })

    return pd.DataFrame(rows).set_index("run") if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# Test-4: 因子系数方向分析（仅 Ridge 方法）
# ---------------------------------------------------------------------------

def test4_coef_direction(valid_period_only: bool = True) -> pd.DataFrame:
    """
    读取每个 Ridge run 的 coef_history.parquet，
    报告验证期内各因子系数的均值和符号稳定性。
    重点观察 piotroski_f 和 high_52w：
      - 若 expanding 的 piotroski_f 系数 > 0 而验证期 IC < 0 → 直接证据
    """
    all_rows = []
    for key in RUNS:
        coef = _load(key, "signal/coef_history.parquet")
        if coef is None:
            continue

        if valid_period_only:
            coef_v = _valid_dates(coef)
        else:
            coef_v = coef

        if coef_v.empty:
            continue

        for factor in coef_v.columns:
            vals = coef_v[factor].dropna()
            if vals.empty:
                continue
            pos_ratio = (vals > 0).mean()
            all_rows.append({
                "run":          key,
                "factor":       factor,
                "coef_mean":    vals.mean(),
                "coef_std":     vals.std(),
                "coef_abs_mean":vals.abs().mean(),
                "pos_ratio":    pos_ratio,  # 1=始终正，0=始终负
                "sign_stable":  max(pos_ratio, 1 - pos_ratio),  # 越高越稳定
            })

    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows)
    return df.pivot_table(
        index="factor", columns="run",
        values="coef_mean", aggfunc="mean"
    ).round(5)


def test4_coef_sign_stability() -> pd.DataFrame:
    """返回各方法各因子系数在验证期的方向稳定性（symbol_stable）。"""
    all_rows = []
    for key in RUNS:
        coef = _load(key, "signal/coef_history.parquet")
        if coef is None:
            continue
        coef_v = _valid_dates(coef)
        if coef_v.empty:
            continue
        for factor in coef_v.columns:
            vals = coef_v[factor].dropna()
            if vals.empty:
                continue
            pos_ratio = (vals > 0).mean()
            all_rows.append({
                "run":    key,
                "factor": factor,
                "coef_mean": vals.mean(),
                "pos_pct": pos_ratio,
            })

    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows)
    # pivot：行=因子，列=run，值=系数均值
    pivot = df.pivot_table(index="factor", columns="run", values="coef_mean").round(5)
    return pivot


# ---------------------------------------------------------------------------
# 计算已知的信号-IC 对比（来自 ic_history_research_all）
# ---------------------------------------------------------------------------

def load_factor_ic_in_validation() -> pd.Series | None:
    """
    从 ic_history_research_all.parquet 读取验证期各因子 IC 均值。
    用于与 expanding 的因子系数方向做交叉验证。
    """
    p = (Path(__file__).parents[1]
         / "reports" / "factor_evaluation" / "ic_history_research_all.parquet")
    if not p.exists():
        return None
    ic_hist = pd.read_parquet(p)
    ic_hist.index = pd.to_datetime(ic_hist.index)
    valid_ic = ic_hist[(ic_hist.index >= VALID_START) & (ic_hist.index <= VALID_END)]
    return valid_ic.mean()


# ---------------------------------------------------------------------------
# 生成报告
# ---------------------------------------------------------------------------

def _fmt_section(title: str, df: pd.DataFrame, note: str = "") -> str:
    lines = [f"\n## {title}\n"]
    if note:
        lines.append(f"> {note}\n")
    if df is None or df.empty:
        lines.append("_数据不可用_\n")
    else:
        lines.append(df.to_string())
    lines.append("")
    return "\n".join(lines)


def build_report(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("Test-1: alpha 分布分析...")
    t1 = test1_alpha_distribution()

    log.info("Test-2: IC(t) vs 月超额收益(t) 相关性...")
    t2 = test2_ic_vs_excess_return()

    log.info("Test-3: Transfer Coefficient...")
    t3 = test3_transfer_coefficient()

    log.info("Test-4: 因子系数方向（验证期）...")
    t4_coef = test4_coef_sign_stability()

    # 因子 IC 方向（真相参照）
    factor_valid_ic = load_factor_ic_in_validation()

    # ---- Test-4 交叉验证：expanding 系数方向 vs 因子 IC 方向 ----
    mismatch_rows = []
    if t4_coef is not None and not t4_coef.empty and factor_valid_ic is not None:
        if "expanding" in t4_coef.columns:
            for fac, coef_val in t4_coef["expanding"].items():
                ic_val = factor_valid_ic.get(fac, np.nan)
                if np.isnan(ic_val) or np.isnan(coef_val):
                    continue
                # 方向不匹配 = 系数为正但 IC 为负（或反之）
                mismatch = bool((coef_val > 0) != (ic_val > 0))
                mismatch_rows.append({
                    "factor":        fac,
                    "expanding_coef": round(coef_val, 5),
                    "valid_ic_mean":  round(ic_val, 5),
                    "direction_mismatch": mismatch,
                    "drag_severity": abs(coef_val) * abs(ic_val) if mismatch else 0,
                })
        mismatch_df = pd.DataFrame(mismatch_rows).sort_values("drag_severity", ascending=False)
    else:
        mismatch_df = pd.DataFrame()

    # ---- 写 parquet 产物 ----
    if not t1.empty:
        t1.to_csv(out_dir / "test1_alpha_distribution.csv")
    if not t2.empty:
        t2.to_csv(out_dir / "test2_ic_vs_excess_ret.csv")
    if not t3.empty:
        t3.to_csv(out_dir / "test3_transfer_coefficient.csv")
    if not t4_coef.empty:
        t4_coef.to_csv(out_dir / "test4_coef_mean.csv")
    if not mismatch_df.empty:
        mismatch_df.to_csv(out_dir / "test4_expanding_coef_vs_ic.csv", index=False)

    # ---- 写 Markdown 报告 ----
    report_lines = [
        "# IC-IR vs 组合 IR 反相关归因报告",
        "",
        f"> 生成日期：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 验证期：{VALID_START.date()} ~ {VALID_END.date()}",
        f"> 诊断对象：expanding / rolling36/48/60 / decay_hl24/36/48",
        "",
        "---",
        "",
        "## 核心问题",
        "",
        "验证期 IC_IR 排名：expanding(0.628) > decay_hl48(0.527) > decay_hl36(0.489) > ...",
        "验证期组合 IR 排名：rolling48(1.489) > rolling60(1.176) > ... > expanding(0.408)",
        "",
        "**两个排名完全反向。本报告通过四条归因线定位根本原因。**",
        "",
        "---",
    ]

    report_lines.append(_fmt_section(
        "Test-1：Alpha 分布（验证期截面展平）",
        t1,
        note="若 std≈1 且 pct_abs_gt2 各方法接近 → 量级压缩假设被否定；若差异显著 → 量级是原因之一",
    ))

    # 解读 Test-1
    if not t1.empty and "std" in t1.columns:
        expand_std = t1.loc["expanding", "std"] if "expanding" in t1.index else np.nan
        rolling_std = t1.loc["rolling48", "std"] if "rolling48" in t1.index else np.nan
        if not np.isnan(expand_std) and not np.isnan(rolling_std):
            ratio = expand_std / rolling_std
            conclusion = (
                f"expanding std={expand_std:.3f} vs rolling48 std={rolling_std:.3f}，"
                f"比值={ratio:.2f}。"
            )
            if 0.9 < ratio < 1.1:
                conclusion += " **量级基本相同，否定量级压缩假设。**"
            else:
                conclusion += " **量级存在差异，量级压缩假设成立。**"
            report_lines.append(f"\n> Test-1 结论：{conclusion}\n")

    report_lines.append(_fmt_section(
        "Test-2：IC(t) vs 月超额收益(t) 序列相关性",
        t2,
        note="ic_vs_ret_spearman_rho：负值 = 高 IC 月份反而亏损 = 信号被 QP 流程反转 / 约束压制",
    ))

    # 解读 Test-2
    if not t2.empty and "ic_vs_ret_spearman_rho" in t2.columns:
        for run in ["expanding", "rolling48"]:
            if run in t2.index:
                rho = t2.loc[run, "ic_vs_ret_spearman_rho"]
                p   = t2.loc[run, "ic_vs_ret_p"]
                sign = "正相关 ✅" if rho > 0 else "负相关 ⚠️"
                sig  = "显著" if p < 0.1 else "不显著"
                report_lines.append(
                    f"\n> Test-2 {run}: IC-ExcRet rho={rho:.3f} ({sign}, {sig} p={p:.3f})\n"
                )

    report_lines.append(_fmt_section(
        "Test-3：Transfer Coefficient（QP 传导系数，验证期逐月）",
        t3,
        note="TC_mean：1=alpha 完美传导到主动权重；越低=QP 约束对信号压制越强",
    ))

    # 解读 Test-3
    if not t3.empty and "tc_mean" in t3.columns:
        for run in ["expanding", "rolling48"]:
            if run in t3.index:
                tc = t3.loc[run, "tc_mean"]
                n  = t3.loc[run, "n"]
                report_lines.append(
                    f"\n> Test-3 {run}: TC={tc:.3f}（{n} 期均值）\n"
                )
        if "expanding" in t3.index and "rolling48" in t3.index:
            tc_exp = t3.loc["expanding", "tc_mean"]
            tc_r48 = t3.loc["rolling48", "tc_mean"]
            if tc_exp < tc_r48 - 0.05:
                report_lines.append(
                    f"\n> **expanding TC({tc_exp:.3f}) 显著低于 rolling48 TC({tc_r48:.3f})"
                    f"，QP 对 expanding 信号的约束摩擦更强。**\n"
                )

    report_lines.append(_fmt_section(
        "Test-4：因子系数方向（验证期各方法 coef_mean，行=因子 列=run）",
        t4_coef,
        note="正值=该因子被赋予正向权重；若 coef>0 但 IC<0 → 系数方向与实际收益方向相反 → 拖累组合",
    ))

    # Test-4 交叉验证表
    report_lines.append("\n### Test-4 子检验：expanding 系数方向 vs 验证期因子 IC 方向（已排序）\n")
    if not mismatch_df.empty:
        report_lines.append(mismatch_df.to_string(index=False))
        drag_factors = mismatch_df[mismatch_df["direction_mismatch"]]["factor"].tolist()
        if drag_factors:
            report_lines.append(
                f"\n\n**方向不匹配因子（expanding 赋予错误方向权重）：{drag_factors}**"
            )
            report_lines.append(
                "\n**这些因子在验证期的实际 IC 方向与 expanding Ridge 学到的系数方向相反，"
                "直接在预测层产生反向 alpha，是 IC_IR 高但组合 IR 低的直接证据。**\n"
            )
        else:
            report_lines.append("\n\n_未发现方向不匹配因子，因子系数方向与 IC 方向一致。_\n")
    else:
        report_lines.append("_缺少 ic_history_research_all.parquet，无法做交叉验证。_\n")

    # ---- 结论汇总 ----
    report_lines += [
        "",
        "---",
        "",
        "## 结论汇总与后续行动",
        "",
        "| 归因线 | 假设 | 结论 |",
        "|---|---|---|",
    ]

    # 动态结论
    for test_key, hyp, col, threshold in [
        ("Test-1", "量级压缩（expanding alpha 方差更小）",
         "pct_abs_gt2", None),
        ("Test-2", "信号被 QP 反转（IC 高但超额反而负）",
         "ic_vs_ret_spearman_rho", None),
        ("Test-3", "QP 约束摩擦（TC 低）",
         "tc_mean", None),
    ]:
        report_lines.append(f"| {test_key} | {hyp} | （见上方各节详细数字）|")

    report_lines += [
        "| Test-4 | 因子系数方向错误（expanding 因子权重与 IC 方向相反）| （见 expanding 系数 vs IC 交叉表）|",
        "",
        "### 后续行动（按优先级）",
        "",
        "1. **若 Test-4 确认 piotroski_f 是主因**（expanding coef>0 but IC<0）：",
        "   立即运行 `configs/pipelines/ablation_no_piotroski_f.py`，确认剔除后 IR 回升量",
        "",
        "2. **若 Test-3 显示 expanding TC 显著低于 rolling**：",
        "   说明 QP 约束与 expanding 信号存在系统性冲突，考虑：",
        "   - 降低 expanding 对旧数据的依赖（增加指数衰减权重，hl 缩短到 12-18m）",
        "   - 或直接以 rolling-48 为主基线（已注册为 challenger，待统计验证）",
        "",
        "3. **若 Test-2 显示 IC-ExcRet 负相关**：",
        "   信号本身无误但 QP 在高 IC 月份产生更大约束偏差",
        "   → 分析高 IC 月份的具体期数，看是否集中在某些市场状态（2021 熊市 vs 2022 反弹）",
        "",
        "4. **若 Test-1 显示 expanding alpha 量级偏小**：",
        "   对 expanding 信号做 rescaling（乘以常数）后重跑 QP，看 IR 是否回升",
        "",
    ]

    report_content = "\n".join(report_lines)
    report_path = out_dir / "ic_ir_paradox_attribution.md"
    report_path.write_text(report_content, encoding="utf-8")
    log.info("归因报告已写入: %s", report_path)
    print(f"\n[OK] Attribution report: {report_path}")
    print(f"     CSV files:  {out_dir}/*.csv")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="诊断 IC_IR vs 组合 IR 反相关根本原因")
    parser.add_argument(
        "--output-dir", default="check/0529",
        help="输出目录（默认：check/0529）",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    build_report(out_dir)


if __name__ == "__main__":
    main()
