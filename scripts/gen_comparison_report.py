"""生成 ICIR-48m vs Ridge-48m + TopN150 验证期对比报告（含图表）。"""
import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

matplotlib.rcParams["font.family"] = ["Microsoft YaHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

RUNS = {
    "ICIR-48m+TopN150": "runs/train_valid/20260530_114709__challenger_icir_rolling48_topn150_ew",
    "Ridge-48m+TopN150": "runs/train_valid/20260530_111129__rolling48_topn150_ew",
    "ICIR-24m+TopN50(冻结基线)": "runs/train_valid/20260529_034947__frozen_baseline_icir_topn50_ew",
}
COLORS = {
    "ICIR-48m+TopN150": "#E67E22",
    "Ridge-48m+TopN150": "#2ECC71",
    "ICIR-24m+TopN50(冻结基线)": "#95A5A6",
}
PANEL_BG = "#1A1D27"
TEXT_COL = "#E0E0E0"
GRID_COL = "#2A2D3A"


def load_data():
    navs, metrics = {}, {}
    for label, path in RUNS.items():
        navs[label] = pd.read_parquet(f"{path}/backtest/nav_valid.parquet")
        m = pd.read_parquet(f"{path}/backtest/metrics_valid.parquet")
        metrics[label] = m["v2"]
    return navs, metrics


def _excess_ret(nav):
    """每日超额收益率（strategy_v2 - benchmark 的日收益差）。"""
    strat = nav["strategy_v2"].pct_change().fillna(0)
    bench = nav["benchmark"].pct_change().fillna(0)
    return strat - bench


def excess_cum(nav):
    return (1 + _excess_ret(nav)).cumprod() - 1


def monthly_excess(nav):
    cum = (1 + _excess_ret(nav)).cumprod()
    return cum.resample("ME").last().pct_change().dropna()


def rolling_ir(mnth, window=12):
    return mnth.rolling(window).mean() / mnth.rolling(window).std() * np.sqrt(12)


def drawdown(nav):
    cum = (1 + _excess_ret(nav)).cumprod()
    return (cum / cum.cummax() - 1) * 100


def style_ax(ax, title):
    ax.set_facecolor(PANEL_BG)
    ax.set_title(title, color=TEXT_COL, fontsize=11, pad=8, fontweight="bold")
    ax.tick_params(colors=TEXT_COL, labelsize=9)
    for sp in ax.spines.values():
        sp.set_color(GRID_COL)
    ax.yaxis.grid(True, color=GRID_COL, linewidth=0.5, alpha=0.7)
    ax.xaxis.grid(True, color=GRID_COL, linewidth=0.5, alpha=0.7)
    ax.set_axisbelow(True)


def main():
    navs, metrics = load_data()
    monthly = {k: monthly_excess(v) for k, v in navs.items()}
    keys_main = ["ICIR-48m+TopN150", "Ridge-48m+TopN150"]

    fig = plt.figure(figsize=(16, 22))
    fig.patch.set_facecolor("#0F1117")
    gs = gridspec.GridSpec(4, 2, figure=fig, hspace=0.48, wspace=0.35)

    # ── 1. 累计超额收益曲线（全宽）────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0, :])
    for label, nav in navs.items():
        cum = excess_cum(nav) * 100
        ax1.plot(cum.index, cum, label=label, color=COLORS[label], linewidth=2)
    ax1.axhline(0, color="#555", linewidth=0.8, linestyle="--")
    ax1.set_ylabel("累计超额收益 (%)", color=TEXT_COL, fontsize=10)
    ax1.legend(facecolor=PANEL_BG, edgecolor=GRID_COL, labelcolor=TEXT_COL, fontsize=10)
    style_ax(ax1, "验证期累计超额收益（2021-01 ~ 2022-12）")

    # ── 2. 月度超额收益柱状图 ──────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1, 0])
    dates = monthly[keys_main[0]].index
    x = np.arange(len(dates))
    w = 0.38
    for i, k in enumerate(keys_main):
        vals = monthly[k].reindex(dates).fillna(0) * 100
        colors_bar = [COLORS[k] if v >= 0 else "#C0392B" for v in vals]
        ax2.bar(x + (i - 0.5) * w, vals, w, label=k, color=colors_bar, alpha=0.85)
    ax2.axhline(0, color="#555", linewidth=0.8)
    ax2.set_xticks(x[::3])
    ax2.set_xticklabels([str(d)[:7] for d in dates[::3]],
                        rotation=35, ha="right", fontsize=8, color=TEXT_COL)
    ax2.set_ylabel("月超额收益 (%)", color=TEXT_COL, fontsize=10)
    ax2.legend(facecolor=PANEL_BG, edgecolor=GRID_COL, labelcolor=TEXT_COL, fontsize=9)
    style_ax(ax2, "月度超额收益对比")

    # ── 3. 滚动 IR（12个月）────────────────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 1])
    for k in keys_main:
        rir = rolling_ir(monthly[k])
        ax3.plot(rir.index, rir, label=k, color=COLORS[k], linewidth=1.8)
    ax3.axhline(0, color="#555", linewidth=0.8, linestyle="--")
    ax3.axhline(0.5, color="#F39C12", linewidth=0.8, linestyle=":", alpha=0.7, label="IR=0.5 门槛")
    ax3.set_ylabel("滚动12月 IR", color=TEXT_COL, fontsize=10)
    ax3.legend(facecolor=PANEL_BG, edgecolor=GRID_COL, labelcolor=TEXT_COL, fontsize=9)
    style_ax(ax3, "滚动 IR（12个月窗口）")

    # ── 4. IR / 月胜率 / 超额收益 对比条形图 ─────────────────────────────
    ax4 = fig.add_subplot(gs[2, 0])
    bar_labels = list(RUNS.keys())
    short = ["ICIR-48m\nTopN150", "Ridge-48m\nTopN150", "ICIR-24m\nTopN50\n(冻结基线)"]
    irs = [metrics[k]["information_ratio"] for k in bar_labels]
    wrs = [metrics[k]["monthly_win_rate"] * 100 for k in bar_labels]
    ers = [metrics[k]["excess_return"] * 100 for k in bar_labels]
    x3 = np.arange(len(bar_labels))
    w3 = 0.25
    ax4.bar(x3 - w3, irs, w3, label="IR", color=[COLORS[k] for k in bar_labels], alpha=0.9)
    ax4b = ax4.twinx()
    ax4b.bar(x3, wrs, w3, label="月胜率(%)", color=[COLORS[k] for k in bar_labels], alpha=0.5)
    ax4b.bar(x3 + w3, ers, w3, label="超额收益(%)", color=[COLORS[k] for k in bar_labels], alpha=0.35)
    ax4.set_xticks(x3)
    ax4.set_xticklabels(short, fontsize=8, color=TEXT_COL)
    ax4.set_ylabel("信息比率 (IR)", color=TEXT_COL, fontsize=9)
    ax4b.set_ylabel("月胜率% / 超额收益%", color=TEXT_COL, fontsize=9)
    ax4.tick_params(colors=TEXT_COL)
    ax4b.tick_params(colors=TEXT_COL)
    ax4.set_facecolor(PANEL_BG)
    ax4b.set_facecolor(PANEL_BG)
    for sp in ax4.spines.values():
        sp.set_color(GRID_COL)
    ax4.yaxis.grid(True, color=GRID_COL, linewidth=0.5, alpha=0.7)
    lines1, lbl1 = ax4.get_legend_handles_labels()
    lines2, lbl2 = ax4b.get_legend_handles_labels()
    ax4.legend(lines1 + lines2, lbl1 + lbl2, facecolor=PANEL_BG, edgecolor=GRID_COL,
               labelcolor=TEXT_COL, fontsize=9)
    ax4.set_title("关键指标三组对比", color=TEXT_COL, fontsize=11, pad=8, fontweight="bold")

    # ── 5. 超额回撤水位 ────────────────────────────────────────────────────
    ax5 = fig.add_subplot(gs[2, 1])
    for label, nav in navs.items():
        dd = drawdown(nav)
        ax5.fill_between(dd.index, dd, 0, alpha=0.3, color=COLORS[label])
        ax5.plot(dd.index, dd, color=COLORS[label], linewidth=1.2, label=label)
    ax5.set_ylabel("超额回撤 (%)", color=TEXT_COL, fontsize=10)
    ax5.legend(facecolor=PANEL_BG, edgecolor=GRID_COL, labelcolor=TEXT_COL, fontsize=9)
    style_ax(ax5, "超额回撤水位")

    # ── 6. 月收益分布直方图 ───────────────────────────────────────────────
    ax6 = fig.add_subplot(gs[3, 0])
    for k in keys_main:
        vals = monthly[k] * 100
        ax6.hist(vals, bins=10, alpha=0.55, label=k, color=COLORS[k],
                 edgecolor="white", linewidth=0.3)
        ax6.axvline(vals.mean(), color=COLORS[k], linewidth=1.8,
                    linestyle="--", alpha=0.9, label=f"{k} 均值={vals.mean():.2f}%")
    ax6.axvline(0, color="white", linewidth=0.8, linestyle=":")
    ax6.set_xlabel("月超额收益 (%)", color=TEXT_COL, fontsize=10)
    ax6.set_ylabel("频次", color=TEXT_COL, fontsize=10)
    ax6.legend(facecolor=PANEL_BG, edgecolor=GRID_COL, labelcolor=TEXT_COL, fontsize=8)
    style_ax(ax6, "月超额收益分布")

    # ── 7. 雷达图 ──────────────────────────────────────────────────────────
    ax7 = fig.add_subplot(gs[3, 1], polar=True)
    cats = ["IR", "月胜率", "超额收益", "低换手\n(反转)", "低超额MDD\n(反转)"]
    N = len(cats)
    angles = [n / float(N) * 2 * np.pi for n in range(N)] + [0]
    data_raw = {
        "ICIR-48m+TopN150":      [1.901, 0.696, 0.1059, 1 - 680 / 1500, 1 - 0.0418 / 0.10],
        "Ridge-48m+TopN150":     [2.274, 0.783, 0.1011, 1 - 763 / 1500, 1 - 0.0355 / 0.10],
        "ICIR-24m+TopN50(冻结基线)": [0.924, 0.609, 0.0624, 1 - 886 / 1500, 1 - 0.0748 / 0.10],
    }
    maxv = [max(data_raw[k][i] for k in data_raw) for i in range(N)]
    for label, vals in data_raw.items():
        norm = [v / maxv[i] if maxv[i] > 0 else 0 for i, v in enumerate(vals)] + [0]
        norm[-1] = norm[0]
        ax7.plot(angles, norm, color=COLORS[label], linewidth=2,
                 label=label.replace("(冻结基线)", "\n(冻结基线)"))
        ax7.fill(angles, norm, alpha=0.12, color=COLORS[label])
    ax7.set_xticks(angles[:-1])
    ax7.set_xticklabels(cats, color=TEXT_COL, fontsize=9)
    ax7.set_facecolor(PANEL_BG)
    ax7.spines["polar"].set_color(GRID_COL)
    ax7.tick_params(colors=TEXT_COL)
    ax7.set_yticklabels([])
    ax7.set_title("多维综合雷达图", color=TEXT_COL, fontsize=11, pad=20, fontweight="bold")
    ax7.legend(facecolor=PANEL_BG, edgecolor=GRID_COL, labelcolor=TEXT_COL, fontsize=8,
               loc="upper right", bbox_to_anchor=(1.45, 1.2))

    fig.suptitle(
        "验证期对比报告：ICIR-48m vs Ridge-48m（TopN150 EW）",
        color=TEXT_COL, fontsize=14, fontweight="bold", y=0.998,
    )

    out = "reports/comparison_icir48_vs_ridge48_topn150.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"图表已保存: {out}")

    # ── 文字汇总 ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("验证期六项指标汇总")
    print("=" * 60)
    rows = []
    for k in RUNS:
        m = metrics[k]
        rows.append({
            "配置": k,
            "IR": f"{m['information_ratio']:.3f}",
            "年化超额": f"{m['excess_return']*100:.2f}%",
            "超额MDD": f"{abs(m['excess_max_drawdown'])*100:.2f}%",
            "跟踪误差": f"{m['tracking_error']*100:.2f}%",
            "月胜率": f"{m['monthly_win_rate']*100:.1f}%",
        })
    df = pd.DataFrame(rows).set_index("配置")
    print(df.to_string())
    print()

    # 换手率从 self_check 里读
    turnovers = {
        "ICIR-48m+TopN150": 680,
        "Ridge-48m+TopN150": 763,
        "ICIR-24m+TopN50(冻结基线)": 886,
    }
    print("年化双边换手率：")
    for k, v in turnovers.items():
        print(f"  {k}: {v}%")

    print("\nPASS/FAIL (IR>=0.5 / MDD<=10% / turnover 500-1500%):")
    for k in RUNS:
        m = metrics[k]
        ir_pass = "PASS" if m["information_ratio"] >= 0.5 else "FAIL"
        mdd_pass = "PASS" if abs(m["excess_max_drawdown"]) <= 0.10 else "FAIL"
        to_pass = "PASS" if 500 <= turnovers[k] <= 1500 else "FAIL"
        print(f"  {k}: IR={ir_pass}  MDD={mdd_pass}  TO={to_pass}")


if __name__ == "__main__":
    main()
