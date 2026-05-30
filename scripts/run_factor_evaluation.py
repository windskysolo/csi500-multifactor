"""
scripts/run_factor_evaluation.py — 因子有效性检验（从 02_factor_evaluation.ipynb 提取）
======================================================================================

输入：
    data/processed/factor_panels/*.parquet   （已预处理因子面板）
    data/processed/fwd_ret_panel.parquet     （如已缓存则直接加载）

输出：
    reports/factor_evaluation/
        factor_overview.csv
        ic_result.csv
        shift_result.csv
        quintile_summary.csv
        factor_correlation.csv
        valid_comparison.csv
        factor_summary.csv
        final_factors.json          ← pipeline 下游使用
        factor_evaluation_report.md
        01_ic_timeseries_top6.png
        02_quintile_cumret_top3.png
        03_factor_correlation.png

使用方法：
    python -m scripts.run_factor_evaluation [--output-dir PATH] [--no-plots]
"""

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # 非交互后端，避免在无 GUI 环境报错
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import config as cfg
from src.data.universe import get_trade_dates, get_investable_universe
from src.evaluation.ic_analysis import (
    batch_ic_test,
    batch_ic_decay,
    build_exit_date_map,
    build_ic_history,
    compute_ic_series,
    compute_forward_returns,
    compute_factor_correlation,
    filter_redundant_factors,
)
from src.evaluation.shift_test import batch_shift_test
from src.evaluation.quintile_backtest import batch_quintile_summary, run_quintile_backtest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

plt.rcParams.update({
    "figure.dpi":         110,
    "font.size":          11,
    "axes.unicode_minus": False,
    "font.family":        ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
})

COLORS = {"Q1": "#d62728", "Q5": "#2ca02c", "Q5-Q1": "#1f77b4", "DirectionalLS": "#1f77b4"}
HIGH_CORR_THRESHOLD   = cfg.EVAL_HIGH_CORR_THRESHOLD
SHARPE_LS_THRESHOLD   = cfg.EVAL_SHARPE_LS_THRESHOLD
IC_EFFECTIVE_CRITERIA = cfg.EVAL_IC_EFFECTIVE_CRITERIA

# Phase 3 新增常量
# P1 覆盖率校正：当期因子非 NaN 股票数 / 总列数 < 此值时跳过该期 IC 计算
IC_COVERAGE_MIN: float = 0.50
# IC Decay 最大检验 lag 期数
IC_DECAY_MAX_LAG: int = 3

# 已从因子池显式剔除的因子：parquet 文件仍保留（便于复现），但不参与评估
# north_flow_5d：广播因子，截面标准差=0，预处理后全 NaN
# chip_winner_rate / cost_deviation：cyq_perf 2018 前无数据，训练期仅 ~30 期有效
EXCLUDED_FROM_FACTOR_POOL: frozenset[str] = frozenset({
    "north_flow_5d",
    "chip_winner_rate",
    "cost_deviation",
})

_FWD_RET_META_SUFFIX = ".meta.json"


def _get_git_commit() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(_PROJECT_ROOT),
        )
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _hash_series(s: "pd.Series") -> str:
    return hashlib.md5("|".join(str(v) for v in s).encode()).hexdigest()[:12]


def _hash_dates(dates: list) -> str:
    return hashlib.md5("|".join(str(d.date()) for d in dates).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------

def load_factor_panels(panel_dir: Path) -> dict[str, pd.DataFrame]:
    found = sorted([f.stem for f in panel_dir.glob("*.parquet")])
    if not found:
        raise FileNotFoundError(f"未找到因子面板，请先运行 build_factor_panels：{panel_dir}")
    active = [name for name in found if name not in EXCLUDED_FROM_FACTOR_POOL]
    if skipped := set(found) - set(active):
        log.info("跳过已从因子池剔除的因子: %s", sorted(skipped))
    log.info("加载 %d 个因子面板（共 %d 个，排除 %d 个）...", len(active), len(found), len(skipped))
    return {name: pd.read_parquet(panel_dir / f"{name}.parquet") for name in active}


def load_or_compute_fwd_ret(
    all_dates: list[pd.Timestamp],
    fwd_cache: Path,
    *,
    recompute: bool = False,
    benchmark_mode: str | None = None,
) -> pd.DataFrame:
    """
    加载或计算 forward return 面板，并通过 sidecar metadata 防止配置漂移。

    Args:
        all_dates:       调仓日列表（来自 TRAIN_START ~ VALID_END）
        fwd_cache:       缓存 Parquet 路径
        recompute:       True 则忽略缓存强制重算（对应 --recompute-fwd-ret 参数）
        benchmark_mode:  "excess" 或 None；写入 metadata 便于后续校验
    """
    meta_path = fwd_cache.parent / (fwd_cache.stem + _FWD_RET_META_SUFFIX)
    expected_dates_hash = _hash_dates(all_dates)
    expected_meta = {
        "start": str(all_dates[0].date()),
        "end":   str(all_dates[-1].date()),
        "n_dates": len(all_dates),
        "rebalance_dates_hash": expected_dates_hash,
        "benchmark_mode": benchmark_mode,
    }

    if fwd_cache.exists() and not recompute:
        # 校验 sidecar metadata；不匹配则拒绝使用旧缓存
        if meta_path.exists():
            cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            mismatch_keys = [
                k for k in ("rebalance_dates_hash", "benchmark_mode")
                if cached_meta.get(k) != expected_meta[k]
            ]
            if mismatch_keys:
                log.error(
                    "fwd_ret_panel 缓存 metadata 不匹配（%s），请用 --recompute-fwd-ret 重算。"
                    "差异字段：%s",
                    fwd_cache, mismatch_keys,
                )
                raise RuntimeError(
                    f"fwd_ret_panel 缓存与当前配置不匹配（{mismatch_keys}）。"
                    "请传入 --recompute-fwd-ret 参数强制重算。"
                )
        else:
            # F4-003: sidecar 缺失时拒绝加载，避免不可追溯的旧缓存进入生产流程
            raise RuntimeError(
                f"fwd_ret_panel 缓存存在但无 metadata sidecar（{meta_path}），"
                "无法校验配置一致性。请使用 --recompute-fwd-ret 参数强制重算并生成 metadata。"
            )
        log.info("从缓存加载 forward return：%s", fwd_cache)
        return pd.read_parquet(fwd_cache)

    log.info("计算 forward return（约 1-3 分钟）...")
    codes_by_date = {T: get_investable_universe(T).tolist() for T in all_dates}
    all_codes = sorted(set().union(*codes_by_date.values()))
    fwd_ret = compute_forward_returns(all_dates, all_codes, codes_by_date)
    fwd_ret.to_parquet(fwd_cache)
    meta_path.write_text(
        json.dumps({**expected_meta, "source_commit": _get_git_commit()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("已计算并缓存：%s  shape=%s  metadata=%s", fwd_cache, fwd_ret.shape, meta_path)
    return fwd_ret


# ---------------------------------------------------------------------------
# 可视化
# ---------------------------------------------------------------------------

def plot_ic_timeseries(
    plot_factors: list[str],
    train_panels: dict[str, pd.DataFrame],
    train_fwd: pd.DataFrame,
    ic_result: pd.DataFrame,
    output_dir: Path,
) -> None:
    n_plot = len(plot_factors)
    fig, axes = plt.subplots(n_plot, 1, figsize=(14, 3.2 * n_plot), sharex=True)
    if n_plot == 1:
        axes = [axes]

    for ax, fname in zip(axes, plot_factors):
        ic_s = compute_ic_series(train_panels[fname], train_fwd)
        ic_cum = ic_s.cumsum()
        ic_mean = ic_result.loc[fname, "ic_mean"]
        ic_ir = ic_result.loc[fname, "ic_ir"]
        is_eff = ic_result.loc[fname, "effective"]

        bar_color = "#2ca02c" if ic_mean >= 0 else "#d62728"
        ax.bar(ic_s.index, ic_s.values, color=bar_color, alpha=0.45, width=20, label="月度 IC")
        ax.axhline(0, color="black", linewidth=0.6)
        ax.set_ylabel("IC", fontsize=9)
        ax.set_ylim(-0.25, 0.25)

        ax2 = ax.twinx()
        ax2.plot(ic_cum.index, ic_cum.values, color=bar_color, linewidth=2, label="IC 累计")
        ax2.set_ylabel("IC 累计", fontsize=9)

        eff_mark = "✅" if is_eff else "❌"
        ax.set_title(
            f"{eff_mark} {fname}    IC_IR={ic_ir:+.3f}    "
            f"|IC均值|={abs(ic_mean):.4f}    n={int(ic_result.loc[fname, 'n'])}",
            fontsize=10, loc="left",
        )
        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8)

    fig.suptitle(
        f"IC 时序（训练集 {cfg.TRAIN_START.year}-{cfg.TRAIN_END.year}，按 |IC_IR| 降序前 6）",
        fontsize=13, y=1.01,
    )
    plt.tight_layout()
    out = output_dir / "01_ic_timeseries_top6.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info("图表已保存：%s", out)


def plot_quintile_cumret(
    top_factors: list[str],
    train_panels: dict[str, pd.DataFrame],
    train_fwd: pd.DataFrame,
    quintile_summary: pd.DataFrame,
    output_dir: Path,
) -> None:
    n = len(top_factors)
    if n == 0:
        return
    fig, axes = plt.subplots(1, n, figsize=(6.5 * n, 5), sharey=False)
    if n == 1:
        axes = [axes]

    for ax, fname in zip(axes, top_factors):
        direction = quintile_summary.loc[fname, "factor_direction"]
        res = run_quintile_backtest(train_panels[fname], train_fwd, direction=direction)
        cum = res.get("cum_rets", pd.DataFrame())
        if cum.empty:
            ax.set_title(f"{fname} — 无有效数据")
            continue

        for col in ["Q1", "Q5", "DirectionalLS"]:
            if col in cum.columns:
                ax.plot(
                    cum.index, cum[col],
                    label=col,
                    color=COLORS.get(col, "gray"),
                    linewidth=2.0 if col == "DirectionalLS" else 1.4,
                    linestyle="--" if col == "DirectionalLS" else "-",
                )

        ax.axhline(1.0, color="black", linewidth=0.7, linestyle=":")
        ax.set_title(
            f"{fname}\n方向化 Sharpe={quintile_summary.loc[fname, 'directional_sharpe_ls']:.2f}  "
            f"方向化年化={quintile_summary.loc[fname, 'directional_ls_ann_ret']:+.1%}",
            fontsize=10,
        )
        ax.legend(fontsize=9)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{y:.1f}x"))

    fig.suptitle(
        f"5 分组累计收益（Q1 / Q5 / DirectionalLS，训练集 {cfg.TRAIN_START.year}-{cfg.TRAIN_END.year}）",
        fontsize=13,
    )
    plt.tight_layout()
    out = output_dir / "02_quintile_cumret_top3.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info("图表已保存：%s", out)


def plot_correlation_heatmap(
    corr_matrix: pd.DataFrame,
    output_dir: Path,
) -> None:
    factor_labels = corr_matrix.columns.tolist()
    n_f = len(factor_labels)
    fig, ax = plt.subplots(figsize=(max(12, n_f * 0.7), max(10, n_f * 0.65)))
    im = ax.imshow(corr_matrix.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    plt.colorbar(im, ax=ax, shrink=0.75, label="Spearman r")

    ax.set_xticks(range(n_f))
    ax.set_yticks(range(n_f))
    ax.set_xticklabels(factor_labels, rotation=50, ha="right", fontsize=9)
    ax.set_yticklabels(factor_labels, fontsize=9)

    for i in range(n_f):
        for j in range(n_f):
            val = corr_matrix.iloc[i, j]
            if pd.notna(val):
                txt_color = "white" if abs(val) > 0.65 else "black"
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        fontsize=6.5, color=txt_color)

    ax.set_title(
        f"因子平均截面 Rank 相关矩阵（训练集 {cfg.TRAIN_START.year}-{cfg.TRAIN_END.year}）",
        fontsize=13, pad=15,
    )
    plt.tight_layout()
    out = output_dir / "03_factor_correlation.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info("图表已保存：%s", out)


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def build_markdown_report(
    overview: pd.DataFrame,
    ic_result: pd.DataFrame,
    shift_result: pd.DataFrame,
    quintile_summary: pd.DataFrame,
    corr_matrix: pd.DataFrame,
    comparison: pd.DataFrame,
    summary: pd.DataFrame,
    train_dates: list[pd.Timestamp],
    valid_dates: list[pd.Timestamp],
    valid_fwd: pd.DataFrame,
    effective_factors: list[str],
    invalid_factors: list[str],
    warned_factors: list[str],
    high_corr_pairs: list[tuple],
    flipped: list[str],
    final_factors: list[str],
    excluded: list[str],
    display_cols: list[str],
    *,
    dedup_excluded: list[str] | None = None,
    seg_ic_df: pd.DataFrame | None = None,
    valid_n: int = 0,
    ci_half: float = float("nan"),
    power_low: bool = False,
    lead_warn_factors: list[str] | None = None,
    ic_decay_df: pd.DataFrame | None = None,
) -> str:
    lines = []
    lines.append("# 因子有效性检验报告（run_factor_evaluation）")
    lines.append(f"\n生成时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"\n训练集：{train_dates[0].date()} ~ {train_dates[-1].date()}（{len(train_dates)} 期）")
    lines.append(f"\n验证集：{valid_dates[0].date()} ~ {valid_dates[-1].date()}（{len(valid_dates)} 期）")

    lines.append("\n\n---\n\n## 1. 因子面板概览\n")
    lines.append(overview.to_markdown())

    lines.append("\n\n---\n\n## 2. IC 检验结果（训练集）\n")
    lines.append(ic_result[display_cols].round(4).to_markdown(floatfmt=".4f"))
    lines.append(f"\n\n✅ **有效因子（{len(effective_factors)} 个）**：")
    for f in effective_factors:
        row = ic_result.loc[f]
        lines.append(f"\n- `{f}` IC_IR={row['ic_ir']:+.3f}  |IC均值|={abs(row['ic_mean']):.4f}  p_bh={row['p_value_bh']:.4f}")
    lines.append(f"\n\n❌ **未通过（{len(invalid_factors)} 个）**：`{'`、`'.join(invalid_factors)}`")

    lines.append("\n\n---\n\n## 3. 时间错位测试（Gate 2 分级 lead_ratio）\n")
    lines.append(
        "\n> **说明**：时间错位测试（lag/lead）是辅助诊断工具，不能替代 PIT 单元测试（`tests/test_pit.py`）。\n"
        "> `diagnosis` 等级：`strong_drop`=正常 / `weak_drop`=可接受 / `no_drop`=中性 / `reverse`=异常。\n"
        "> **Gate 2 分级阈值（Phase 3）**：`lead_ratio` < 1.5x → ✅ 通过；1.5–2.0x → ⚠️ 待议不排除；≥ 2.0x → ❌ 拒绝。\n"
    )
    lines.append(shift_result.round(3).to_markdown(floatfmt=".3f"))
    if warned_factors:
        lines.append(f"\n\n❌ **Gate 2 拒绝因子（lead_ratio ≥ 2.0x 或 lag_reverse）**：`{'`、`'.join(warned_factors)}`")
        lines.append(
            '\n\n> 时间错位测试发现异常信号，但此测试为辅助诊断，**不能作为"无未来函数"的充分证据**。'
            "必须配合 `tests/test_pit.py` 单元测试确认 PIT 约束。"
        )
    else:
        lines.append(
            "\n\n✅ Gate 2 检验通过：无 lead_ratio ≥ 2.0x 拒绝或 lag_reverse 异常。"
            "**注意**：此结果不能替代 PIT 单元测试，仍需通过 `tests/test_pit.py` 确认 PIT 约束。"
        )
    if lead_warn_factors:
        lines.append(
            f"\n\n⚠️ **lead_ratio 待议区（1.5–2.0x，不自动排除）**：`{'`、`'.join(lead_warn_factors)}`\n"
            "> 这些因子通过 Gate 2，但 lead_ratio 略高，建议结合 IC Decay 和子区间稳定性综合判断是否保留。"
        )

    lines.append("\n\n---\n\n## 4. 5分组回测摘要（训练集，不含成本）\n")
    lines.append(
        "\n> **重要口径说明**：以下分组收益为**原始个股等权收益**，不含交易成本，不扣减任何基准，"
        "不代表相对中证500全收益指数（H00905.CSI）的可交易超额收益。"
        "本节数据仅用于验证因子截面方向性，不可直接与实盘策略收益对比。\n"
    )
    lines.append(quintile_summary.round(4).to_markdown(floatfmt=".4f"))

    lines.append("\n\n---\n\n## 5. 因子相关性与去冗余\n")
    if high_corr_pairs:
        lines.append(f"**高相关对**（|r| > {HIGH_CORR_THRESHOLD}）：\n")
        lines.append("| 因子A | 因子B | Spearman r | 建议去掉 |")
        lines.append("|------|------|-----------|---------|")
        for a, b, r, weaker in sorted(high_corr_pairs, key=lambda x: abs(x[2]), reverse=True):
            lines.append(f"| `{a}` | `{b}` | {r:+.3f} | `{weaker}` |")
    else:
        lines.append(f"✅ 无高相关因子对（所有 |r| ≤ {HIGH_CORR_THRESHOLD}）")
    if dedup_excluded:
        lines.append(f"\n\n**去冗余过滤（{len(dedup_excluded)} 个）**：`{'`、`'.join(dedup_excluded)}`")
        lines.append("\n（保留规则：各冗余组内 |IC_IR| 最高者入选）")
    else:
        lines.append("\n\n✅ 去冗余：所有有效因子均不冗余，无因子被过滤")

    lines.append("\n\n---\n\n## 6. 验证集稳健性与检验力分析（Gate 4 量化评分）\n")
    lines.append(
        "\n> **Gate 4 量化分级**（仅用于报告，不参与因子筛选—筛选依据为训练集内部子区间稳定性）：\n"
        "> `stable`=跨期稳定（同向且 |IC_IR| ≥ 0.2）；`weak`=衰减（同向但 < 0.2）；"
        "`warn`=轻微扰动（反向但 < 0.2）；`reverse`=明确逆转（反向且 ≥ 0.2）\n"
    )
    lines.append(comparison.round(3).to_markdown(floatfmt=".3f"))
    if flipped:
        lines.append(f"\n\n⚠️ **方向翻转因子（需关注）**：`{'`、`'.join(flipped)}`")
    else:
        lines.append("\n\n✅ 所有因子在验证集方向一致（无翻转）")
    lines.append(f"\n\n**检验力说明**：验证集有效期 n={valid_n}，IC_IR 95% CI 宽度 ≈ ±{ci_half:.2f}。")
    if power_low:
        lines.append(
            f"⚠️ n < 24，置信区间过宽，方向翻转结论不可靠（翻转因子采用降权 0.5 而非硬排除）。"
        )
    else:
        lines.append("期数充足，检验力可接受。")

    if seg_ic_df is not None and not seg_ic_df.empty:
        meta_cols = {"dir_consistent", "seg_min_ratio", "seg_stable_quant"}
        seg_labels = " vs ".join(c for c in seg_ic_df.columns if c not in meta_cols)
        lines.append(f"\n\n**训练集分段 IC_IR（{seg_labels}）**\n")
        lines.append(seg_ic_df.round(3).to_markdown(floatfmt=".3f"))
        lines.append(
            "\n\n`dir_consistent=False` 表示各段方向不一致，因子效果可能主要来自某一特定市场风格。\n"
            "`seg_min_ratio` = 最差子区间 |IC_IR| / 整体 |IC_IR|；< 0.5 表示量化稳定性不足（降权 0.5）。"
        )

    # IC Decay 节（Phase 3 新增）
    if ic_decay_df is not None and not ic_decay_df.empty:
        lines.append("\n\n---\n\n## 6.5 IC Decay 检验（信号持续性）\n")
        lines.append(
            "\n> `icir_lag1`=原始 IC_IR；`icir_lag2`=因子能否预测第 2 个月收益；"
            "`icir_lag3`=因子能否预测第 3 个月收益。\n"
            "> `ic_decay_pass=True` 表示 lag3 IC_IR 与 lag1 方向一致（信号寿命 ≥ 3 个月），适合月频使用。\n"
            "> `ic_decay_pass=False` 表示 lag3 方向已反转，信号寿命不足，该因子被排除出 final_include。\n"
        )
        lines.append(ic_decay_df.round(3).to_markdown(floatfmt=".3f"))
        decay_fail = ic_decay_df[~ic_decay_df["ic_decay_pass"]].index.tolist()
        if decay_fail:
            lines.append(f"\n\n❌ **IC Decay 未通过（lag3 方向反转）**：`{'`、`'.join(decay_fail)}`")
        else:
            lines.append("\n\n✅ 所有因子 IC Decay 通过（lag3 方向一致）")

    lines.append("\n\n---\n\n## 7. 最终因子汇总\n")
    lines.append(summary.sort_values("ic_ir", key=abs, ascending=False).round(3).to_markdown(floatfmt=".3f"))
    lines.append(f"\n\n### 最终纳入合成：{len(final_factors)} 个")
    for f in final_factors:
        row = summary.loc[f]
        flip = " ⚠️方向翻转" if row.get("dir_flip_valid") else ""
        sharpe = f"{row['directional_sharpe_ls']:.2f}" if pd.notna(row.get("directional_sharpe_ls", float("nan"))) else "N/A"
        lines.append(f"\n- `{f}` IC_IR={row['ic_ir']:+.3f}  Directional_Sharpe_LS={sharpe}{flip}")
    lines.append(f"\n\n### 排除：{len(excluded)} 个\n`{'`、`'.join(excluded)}`")

    lines.append("\n\n---\n\n## 图表文件")
    lines.append("\n- `01_ic_timeseries_top6.png`：前6因子月度IC时序 + 累计IC")
    lines.append("\n- `02_quintile_cumret_top3.png`：方向化多空Sharpe最高前3因子5分组累计收益")
    lines.append("\n- `03_factor_correlation.png`：因子截面Rank相关性热力图")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def run_evaluation(
    output_dir: Path,
    no_plots: bool = False,
    run_id: str | None = None,
    recompute_fwd: bool = False,
) -> list[str]:
    """
    执行完整因子有效性检验流程。

    Args:
        output_dir:    报告与图表输出目录
        no_plots:      True 则跳过可视化（加快速度）
        run_id:        运行标识（如 "20260520-1"），写入 final_factors.json metadata
        recompute_fwd: True 则忽略 fwd_ret_panel 缓存强制重算
    Returns:
        final_factors: 最终通过检验的因子名列表
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    panel_dir = cfg.DATA_PROC / "factor_panels"
    fwd_cache = cfg.DATA_PROC / "fwd_ret_panel.parquet"

    # 1. 加载因子面板
    factor_panels = load_factor_panels(panel_dir)
    found = list(factor_panels.keys())
    log.info("共 %d 个因子面板", len(found))

    # 概览表
    rows = []
    for name, panel in factor_panels.items():
        valid_per_date = panel.notna().sum(axis=1)
        rows.append({
            "因子": name,
            "期数": len(panel),
            "起始": panel.index.min().date() if len(panel) else None,
            "截止": panel.index.max().date() if len(panel) else None,
            "均有效股票数": round(valid_per_date.mean()),
            "最少股票数": int(valid_per_date.min()),
            "有效率%": round(100 * panel.notna().mean().mean(), 1),
        })
    overview = pd.DataFrame(rows).set_index("因子")

    # 2. Forward Return
    all_dates = get_trade_dates(start=cfg.TRAIN_START, end=cfg.VALID_END)
    train_dates = [d for d in all_dates if d <= cfg.TRAIN_END]
    valid_dates = [d for d in all_dates if d >= cfg.VALID_START]
    log.info("总调仓日：%d 期  训练：%d 期  验证：%d 期",
             len(all_dates), len(train_dates), len(valid_dates))

    fwd_ret_panel = load_or_compute_fwd_ret(all_dates, fwd_cache, recompute=recompute_fwd)

    # 3. 训练/验证分割
    # 因子面板按 T 切分（因子值本身不含未来信息）
    train_dates_set = set(train_dates)
    valid_dates_set = set(valid_dates)

    train_panels = {n: p.loc[p.index.isin(train_dates_set)] for n, p in factor_panels.items()}
    valid_panels = {n: p.loc[p.index.isin(valid_dates_set)] for n, p in factor_panels.items()}

    # fwd_ret_panel 按 exit_date 切分：防止最后几期 forward return 跨越边界
    # 例：T=2020-12-31 的 exit_date ≈ 2021-02-07，不应纳入训练集 IC 计算
    exit_date_map = build_exit_date_map(all_dates)
    train_fwd_idx = exit_date_map[exit_date_map <= cfg.TRAIN_END].index
    valid_fwd_idx = exit_date_map[
        (exit_date_map > cfg.TRAIN_END) & (exit_date_map <= cfg.VALID_END)
    ].index
    train_fwd = fwd_ret_panel.loc[fwd_ret_panel.index.isin(train_fwd_idx)]
    valid_fwd  = fwd_ret_panel.loc[fwd_ret_panel.index.isin(valid_fwd_idx)]
    log.info(
        "exit_date 切分完成：train_fwd %d 期（原 %d 期），valid_fwd %d 期（原 %d 期）",
        len(train_fwd), len(train_dates), len(valid_fwd), len(valid_dates),
    )

    # 4. IC 检验（训练集，含 P1 覆盖率校正）
    log.info("IC 检验中（覆盖率下限 %.0f%%）...", IC_COVERAGE_MIN * 100)
    ic_result = batch_ic_test(train_panels, train_fwd, min_coverage=IC_COVERAGE_MIN)
    effective_factors = ic_result[ic_result["effective"]].index.tolist()
    invalid_factors = ic_result[~ic_result["effective"]].index.tolist()
    log.info("有效因子 %d 个，未通过 %d 个", len(effective_factors), len(invalid_factors))

    # 4.5 IC Decay 检验（训练集，P1）
    log.info("IC Decay 检验中（lag 1–%d）...", IC_DECAY_MAX_LAG)
    ic_decay_df = batch_ic_decay(
        train_panels, train_fwd,
        max_lag=IC_DECAY_MAX_LAG,
        min_coverage=IC_COVERAGE_MIN,
    )

    # 5. 时间错位测试
    log.info("时间错位测试中...")
    shift_result = batch_shift_test(train_panels, train_fwd)
    # Gate 2 分级：warning=True 仅在 lead_ratio≥2.0x 或 diagnosis=reverse 时触发
    warned_factors   = shift_result[shift_result["warning"]].index.tolist()
    # 1.5-2.0x 待议区：不自动排除，但在报告中单独列出
    lead_warn_factors = shift_result[shift_result["lead_warn"].fillna(False)].index.tolist()
    if warned_factors:
        log.warning("时间错位测试有警告（需排查 PIT 对齐）：%s", warned_factors)
    else:
        log.info("✅ 所有因子时间错位测试：无 lead_ratio≥2.0x 拒绝或 lag_reverse")
    if lead_warn_factors:
        log.info("时间错位 lead_warn（1.5–2.0x 待议，不自动排除）：%s", lead_warn_factors)

    # 6. IC 时序图（前 6）
    if not no_plots:
        n_plot = min(6, len(ic_result))
        plot_factors = ic_result.head(n_plot).index.tolist()
        log.info("绘制 IC 时序图（%d 个因子）...", n_plot)
        plot_ic_timeseries(plot_factors, train_panels, train_fwd, ic_result, output_dir)

    # 7. 分组回测
    log.info("5 分组回测中（约 30-60 秒）...")
    quintile_summary = batch_quintile_summary(train_panels, train_fwd)

    # 8. 分组累计收益图（前 3）
    if not no_plots:
        top3 = quintile_summary.dropna(subset=["directional_sharpe_ls"]).head(3).index.tolist()
        log.info("绘制分组累计收益图（%d 个因子）...", len(top3))
        plot_quintile_cumret(top3, train_panels, train_fwd, quintile_summary, output_dir)

    # 9. 因子相关性
    log.info("计算因子相关性矩阵...")
    corr_matrix = compute_factor_correlation(train_panels)

    if not no_plots:
        plot_correlation_heatmap(corr_matrix, output_dir)

    factor_labels = corr_matrix.columns.tolist()
    high_corr_pairs = []
    for i, row_f in enumerate(factor_labels):
        for j, col_f in enumerate(factor_labels):
            if j <= i:
                continue
            v = corr_matrix.loc[row_f, col_f]
            if pd.notna(v) and abs(v) > HIGH_CORR_THRESHOLD:
                ir_row = ic_result.loc[row_f, "ic_ir"] if row_f in ic_result.index else float("nan")
                ir_col = ic_result.loc[col_f, "ic_ir"] if col_f in ic_result.index else float("nan")
                weaker = col_f if abs(ir_row) >= abs(ir_col) else row_f
                high_corr_pairs.append((row_f, col_f, v, weaker))

    # 9.5 去冗余（贪心法，按 |IC_IR| 降序保留，高相关因子丢弃较弱的）
    deduplicated_factors = filter_redundant_factors(ic_result, corr_matrix, HIGH_CORR_THRESHOLD)
    dedup_excluded = [f for f in ic_result[ic_result["effective"]].index
                      if f not in deduplicated_factors]
    if dedup_excluded:
        log.info("去冗余过滤（|r| > %.2f）：%s", HIGH_CORR_THRESHOLD, dedup_excluded)
    else:
        log.info("去冗余：无高相关冗余因子被过滤")

    # 10. 验证集 IC（含覆盖率校正）
    log.info("验证集 IC 检验中（覆盖率下限 %.0f%%）...", IC_COVERAGE_MIN * 100)
    valid_ic = batch_ic_test(valid_panels, valid_fwd, min_coverage=IC_COVERAGE_MIN)
    comparison = pd.DataFrame({
        "train_n": ic_result["n"],
        "train_ic_ir": ic_result["ic_ir"],
        "valid_n": valid_ic["n"],
        "valid_ic_ir": valid_ic["ic_ir"],
        "train_effective": ic_result["effective"],
    }).sort_values("train_ic_ir", key=abs, ascending=False)
    comparison["direction_flip"] = (comparison["train_ic_ir"] * comparison["valid_ic_ir"]) < 0
    flipped = comparison[comparison["direction_flip"]].index.tolist()

    # Gate 4 升级：量化 IC_IR 分级评分（仅用于报告，不参与 final_include 决策）
    def _gate4_stability(train_ir: float, valid_ir: float) -> str:
        """
        验证期稳定性量化分级（Gate 4）。
        此函数结果只用于最终报告，不循环反馈进因子筛选。
        筛选依据是训练期内部子区间稳定性（见分段 IC_IR 逻辑）。
        """
        if pd.isna(train_ir) or pd.isna(valid_ir):
            return "unknown"
        same_dir  = (train_ir * valid_ir) >= 0
        abs_valid = abs(valid_ir)
        if same_dir and abs_valid >= 0.2:
            return "stable"    # ✅ 跨期稳定，正常权重
        elif same_dir and abs_valid < 0.2:
            return "weak"      # ⚠️ 方向一致但信号衰减，降权候选
        elif not same_dir and abs_valid < 0.2:
            return "warn"      # ⚠️ 轻微扰动，持续观察
        else:
            return "reverse"   # ❌ 方向明确逆转

    comparison["valid_stability"] = comparison.apply(
        lambda row: _gate4_stability(row["train_ic_ir"], row["valid_ic_ir"]),
        axis=1,
    )

    if flipped:
        log.warning("验证集 IC_IR 方向翻转（需关注）：%s", flipped)

    # 10.5 验证集检验力分析（n < 24 时置信区间过宽，翻转结论不可靠）
    valid_n   = int(comparison["valid_n"].dropna().max()) if not comparison["valid_n"].dropna().empty else len(valid_dates)
    ci_half   = 1.96 / (valid_n ** 0.5) if valid_n > 0 else float("nan")
    power_low = valid_n < 24
    if power_low:
        log.warning(
            "验证集期数 n=%d < 24，IC_IR 置信区间宽达 ±%.2f；"
            "方向翻转结论可信度有限，不建议据此硬排除因子",
            valid_n, ci_half,
        )

    # 10.6 分段 IC_IR 评估，识别风格污染
    # 将训练期等分为 3 段；段数可调，dir_consistent 判断逻辑对任意 N 段均有效。
    def _make_seg_boundaries(
        train_start: pd.Timestamp, train_end: pd.Timestamp, n_segs: int = 3
    ) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
        total_months = (
            (train_end.year - train_start.year) * 12
            + (train_end.month - train_start.month)
            + 1
        )
        seg_months = total_months // n_segs
        result = []
        for i in range(n_segs):
            s = train_start + pd.DateOffset(months=i * seg_months)
            if i < n_segs - 1:
                e = (train_start + pd.DateOffset(months=(i + 1) * seg_months - 1)
                     + pd.offsets.MonthEnd(0))
            else:
                e = train_end
            result.append((f"{s.year}-{e.year}", s, e))
        return result

    seg_boundaries = _make_seg_boundaries(cfg.TRAIN_START, cfg.TRAIN_END, n_segs=3)
    seg_ic_ir: dict[str, dict] = {}
    for label, seg_start, seg_end in seg_boundaries:
        seg_set    = {d for d in train_dates if seg_start <= d <= seg_end}
        seg_p      = {n: p.loc[p.index.isin(seg_set)] for n, p in train_panels.items()}
        seg_f      = train_fwd.loc[train_fwd.index.isin(seg_set)]
        seg_res    = batch_ic_test(seg_p, seg_f, min_coverage=IC_COVERAGE_MIN)
        seg_ic_ir[label] = seg_res["ic_ir"].to_dict()

    seg_ic_df = pd.DataFrame(seg_ic_ir)
    seg_ic_df.index.name = "factor"
    if len(seg_ic_ir) >= 2:
        labels = list(seg_ic_ir.keys())
        # 方向一致性检验：所有段 IC_IR 同号为稳定，任意段符号不同则不稳定。
        sign_mat = seg_ic_df[labels].fillna(0.0).apply(np.sign)
        seg_ic_df["dir_consistent"] = (
            (sign_mat > 0).all(axis=1) | (sign_mat < 0).all(axis=1)
        )

        # P1 量化稳定性：最差子区间 |IC_IR| ≥ 整体 |IC_IR| × 50%
        # 使用训练集整体 IC_IR 作为分母（来自覆盖率校正后的 ic_result）
        overall_abs_ir = ic_result["ic_ir"].abs()

        def _seg_min_ratio(row: pd.Series) -> float:
            fname = row.name
            overall = overall_abs_ir.get(fname, float("nan"))
            if pd.isna(overall) or overall < 1e-6:
                return float("nan")
            return float(row[labels].abs().min() / overall)

        seg_ic_df["seg_min_ratio"]  = seg_ic_df.apply(_seg_min_ratio, axis=1)
        # 量化稳定：最差段 IC_IR 至少是整体的 50%
        seg_ic_df["seg_stable_quant"] = seg_ic_df["seg_min_ratio"].fillna(0.0) >= 0.5

    # 11. 汇总
    summary = pd.DataFrame(index=ic_result.index)
    summary["ic_ir"] = ic_result["ic_ir"]
    summary["ic_mean"] = ic_result["ic_mean"]
    summary["ic_n"] = ic_result["n"].astype(int)
    summary["p_value_bh"] = ic_result["p_value_bh"]
    summary["effective_ic"] = ic_result["effective"]
    summary["shift_drop"] = shift_result.reindex(summary.index)["ic_ir_drop"]
    summary["shift_warning"] = shift_result.reindex(summary.index)["warning"]
    # Gate 2 分级：lead_warn（1.5–2.0x 待议区）单独标记，不计入 shift_warning
    summary["shift_lead_warn"] = shift_result.reindex(summary.index)["lead_warn"].fillna(False)
    summary["shift_lead_ratio"] = shift_result.reindex(summary.index)["lead_ratio"].fillna(0.0)
    summary["factor_direction"] = quintile_summary.reindex(summary.index)["factor_direction"]
    summary["sharpe_ls"] = quintile_summary.reindex(summary.index)["sharpe_ls"]
    summary["directional_sharpe_ls"] = quintile_summary.reindex(summary.index)["directional_sharpe_ls"]
    summary["dir_flip_valid"] = comparison.reindex(summary.index)["direction_flip"]
    summary["valid_stability"] = comparison.reindex(summary.index)["valid_stability"]

    # IC Decay 字段（P1：lag3 同向检验）
    for lag in range(1, IC_DECAY_MAX_LAG + 1):
        col = f"icir_lag{lag}"
        summary[col] = ic_decay_df.reindex(summary.index).get(col, pd.Series(dtype=float))
    summary["ic_decay_pass"] = ic_decay_df.reindex(summary.index)["ic_decay_pass"].fillna(True)

    summary["final_include"] = (
        # 条件 1：IC 显著有效
        summary["effective_ic"]
        # 条件 2：Gate 2 通过（lead_ratio < 2.0x 且 diagnosis ≠ reverse）
        & ~summary["shift_warning"].fillna(False)
        # 条件 3：去冗余后保留
        & summary.index.isin(deduplicated_factors)
        # 条件 4：方向化 Sharpe 合格
        & (summary["directional_sharpe_ls"].abs() >= SHARPE_LS_THRESHOLD)
        # 条件 5：IC Decay 通过（P1：lag3 IC 方向与 lag1 一致）
        & summary["ic_decay_pass"].fillna(True)
    )

    # 稳定性权重：融合方向一致性（P0）和量化最差段比例（P1）
    # 使用训练内部分段，不使用验证集信息，避免信息泄露。
    if "dir_consistent" in seg_ic_df.columns:
        dir_unstable   = ~seg_ic_df["dir_consistent"].reindex(summary.index).fillna(True)
        quant_unstable = ~seg_ic_df.get(
            "seg_stable_quant", pd.Series(True, index=seg_ic_df.index)
        ).reindex(summary.index).fillna(True)
        # 方向不一致 OR 量化最差段比例不足 → 降权 0.5
        train_inconsistent = dir_unstable | quant_unstable
    else:
        # seg_ic_df 未含 dir_consistent（样本期不足两段时的退化路径），全部权重设为 1.0
        train_inconsistent = pd.Series(False, index=summary.index)
    summary["stability_weight"] = np.where(train_inconsistent, 0.5, 1.0)

    final_factors = summary[summary["final_include"]].index.tolist()
    excluded      = summary[~summary["final_include"]].index.tolist()

    # 11.5 IC 历史矩阵持久化
    # 分别输出训练期、验证期、训练+验证研究诊断期，以及最终入模因子子集
    # 注意：验证期 IC 仅供诊断，不允许回流至因子筛选逻辑
    log.info("构建 IC 历史矩阵并持久化...")
    all_factor_names = list(factor_panels.keys())
    ic_history_train_all = build_ic_history(
        train_panels, train_fwd,
        factor_names=all_factor_names,
        min_coverage=IC_COVERAGE_MIN,
    )
    ic_history_valid_all = build_ic_history(
        valid_panels, valid_fwd,
        factor_names=all_factor_names,
        min_coverage=IC_COVERAGE_MIN,
    )
    ic_history_research_all = pd.concat(
        [ic_history_train_all, ic_history_valid_all]
    ).sort_index()
    ic_history_research_all.index.name = "rebalance_date"

    # final_factors 子集（健康监控默认输入）
    if final_factors:
        ic_history_final_research = ic_history_research_all[final_factors].copy()
    else:
        ic_history_final_research = pd.DataFrame(
            index=ic_history_research_all.index, dtype="float64"
        )
        ic_history_final_research.index.name = "rebalance_date"

    # 写出 parquet
    ic_history_train_all.to_parquet(output_dir / "ic_history_train_all.parquet")
    ic_history_valid_all.to_parquet(output_dir / "ic_history_valid_all.parquet")
    ic_history_research_all.to_parquet(output_dir / "ic_history_research_all.parquet")
    ic_history_final_research.to_parquet(output_dir / "ic_history_final_research.parquet")

    # 覆盖率不足因子（有效 IC 占比 < 80%）披露在 metadata 中
    low_coverage = [
        name for name in all_factor_names
        if ic_history_research_all[name].notna().mean() < 0.8
    ]
    ic_history_metadata = {
        "train_start": str(cfg.TRAIN_START.date()),
        "train_end":   str(cfg.TRAIN_END.date()),
        "valid_start": str(cfg.VALID_START.date()),
        "valid_end":   str(cfg.VALID_END.date()),
        "n_dates_train":    len(ic_history_train_all),
        "n_dates_valid":    len(ic_history_valid_all),
        "n_dates_research": len(ic_history_research_all),
        "n_factors_all":    len(all_factor_names),
        "n_factors_final":  len(final_factors),
        "includes_validation": True,
        "coverage_note": "验证期数据仅用于诊断，不允许回流至筛选逻辑",
        "low_coverage_factors": low_coverage,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(output_dir / "ic_history_metadata.json", "w", encoding="utf-8") as fh:
        json.dump(ic_history_metadata, fh, ensure_ascii=False, indent=2)

    log.info(
        "IC 历史矩阵已保存：train=%s  valid=%s  research=%s  final=%s",
        ic_history_train_all.shape, ic_history_valid_all.shape,
        ic_history_research_all.shape, ic_history_final_research.shape,
    )
    if low_coverage:
        log.warning("IC 历史覆盖率 < 80%% 的因子（%d 个）：%s", len(low_coverage), low_coverage)

    # 12. 导出
    display_cols = ["n", "ic_mean", "ic_std", "ic_ir", "t_stat", "p_value_bh",
                    "pct_consistent_dir", "effective"]
    overview.to_csv(output_dir / "factor_overview.csv")
    ic_result.to_csv(output_dir / "ic_result.csv")
    shift_result.to_csv(output_dir / "shift_result.csv")
    quintile_summary.to_csv(output_dir / "quintile_summary.csv")
    corr_matrix.to_csv(output_dir / "factor_correlation.csv")
    comparison.to_csv(output_dir / "valid_comparison.csv")
    seg_ic_df.to_csv(output_dir / "seg_ic_ir.csv")
    ic_decay_df.to_csv(output_dir / "ic_decay_result.csv")
    summary.sort_values("ic_ir", key=abs, ascending=False).to_csv(output_dir / "factor_summary.csv")

    # ---- 一致性断言（F4-002）：factor_summary.final_include 必须与 final_factors 完全一致 ----
    summary_final_set   = set(summary[summary["final_include"]].index.tolist())
    final_factors_set   = set(final_factors)
    if summary_final_set != final_factors_set:
        diff = summary_final_set.symmetric_difference(final_factors_set)
        raise RuntimeError(
            f"一致性断言失败：factor_summary.final_include 与 final_factors 不一致，"
            f"差异因子：{diff}。请检查 final_include 的计算逻辑。"
        )

    # final_factors 以 JSON 存储，供 pipeline 下游读取；stability_weights 供合成阶段使用
    final_factors_path = output_dir / "final_factors.json"
    summary_csv_path   = output_dir / "factor_summary.csv"
    summary_hash = hashlib.md5(
        summary.sort_index().to_csv().encode()
    ).hexdigest()[:12]
    with open(final_factors_path, "w", encoding="utf-8") as f:
        json.dump({
            "final_factors": final_factors,
            "excluded": excluded,
            "stability_weights": summary.loc[
                summary["final_include"], "stability_weight"
            ].to_dict(),
            "_metadata": {
                "run_id":             run_id or "unset",
                "generated_at":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "git_commit":         _get_git_commit(),
                "factor_summary_md5": summary_hash,
                "n_final":            len(final_factors),
                "n_excluded":         len(excluded),
            },
        }, f, ensure_ascii=False, indent=2)
    log.info(
        "final_factors.json 已写入（run_id=%s, n=%d, summary_md5=%s）",
        run_id, len(final_factors), summary_hash,
    )

    report_text = build_markdown_report(
        overview, ic_result, shift_result, quintile_summary, corr_matrix,
        comparison, summary, train_dates, valid_dates, valid_fwd,
        effective_factors, invalid_factors, warned_factors, high_corr_pairs,
        flipped, final_factors, excluded, display_cols,
        dedup_excluded=dedup_excluded,
        seg_ic_df=seg_ic_df,
        valid_n=valid_n, ci_half=ci_half, power_low=power_low,
        lead_warn_factors=lead_warn_factors,
        ic_decay_df=ic_decay_df,
    )
    (output_dir / "factor_evaluation_report.md").write_text(report_text, encoding="utf-8")

    # 控制台汇总
    print("\n" + "=" * 65)
    print(f"因子有效性检验汇总（训练集 {cfg.TRAIN_START.year}-{cfg.TRAIN_END.year}）")
    print("=" * 65)
    print(f"\n[最终纳入合成] {len(final_factors)} 个：")
    for f in final_factors:
        row    = summary.loc[f]
        sw     = row.get("stability_weight", 1.0)
        flip   = " (稳定性降权0.5)" if sw < 1.0 else ""
        sharpe = f"{row['directional_sharpe_ls']:.2f}" if pd.notna(row.get("directional_sharpe_ls", float("nan"))) else "N/A"
        print(f"   {f:20s}  IC_IR={row['ic_ir']:+.3f}  DirSharpe={sharpe}  SW={sw:.1f}{flip}")
    print(f"\n[排除] {len(excluded)} 个：{excluded}")
    if dedup_excluded:
        print(f"\n[去冗余过滤] {len(dedup_excluded)} 个：{dedup_excluded}")
    if lead_warn_factors:
        print(f"\n[Gate 2 待议（1.5-2.0x）] {len(lead_warn_factors)} 个：{lead_warn_factors}（通过Gate2，需人工复核）")
    if high_corr_pairs:
        print(f"\n[高相关对] |r| > {HIGH_CORR_THRESHOLD}：")
        for a, b, r, weaker in sorted(high_corr_pairs, key=lambda x: abs(x[2]), reverse=True):
            print(f"   {a} x {b}  r={r:+.3f}  建议去掉：{weaker}")
    decay_fail = ic_decay_df[~ic_decay_df["ic_decay_pass"]].index.tolist() if not ic_decay_df.empty else []
    if decay_fail:
        print(f"\n[IC Decay 未通过（lag3方向反转）] {len(decay_fail)} 个：{decay_fail}")
    else:
        print("\n[IC Decay] 所有因子 lag3 方向一致 ✅")
    if power_low:
        print(f"\n[检验力] 验证集 n={valid_n} < 24，IC_IR 95%CI 宽达 +-{ci_half:.2f}，翻转结论仅供参考")
    print(f"\n[报告目录] {output_dir}")
    print("=" * 65)

    return final_factors


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="因子有效性检验（提取自 02_factor_evaluation.ipynb）")
    parser.add_argument(
        "--output-dir",
        default=str(_PROJECT_ROOT / "reports" / "factor_evaluation"),
        help="报告与图表输出目录（默认：reports/factor_evaluation）",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="跳过可视化（加快速度，仍输出 CSV 和 Markdown 报告）",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="运行标识（如 '20260520-1'），写入 final_factors.json 的 _metadata 字段",
    )
    parser.add_argument(
        "--recompute-fwd-ret",
        action="store_true",
        help="忽略 fwd_ret_panel.parquet 缓存，强制重新计算 forward return",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    run_evaluation(
        output_dir=output_dir,
        no_plots=args.no_plots,
        run_id=args.run_id,
        recompute_fwd=args.recompute_fwd_ret,
    )


if __name__ == "__main__":
    main()
