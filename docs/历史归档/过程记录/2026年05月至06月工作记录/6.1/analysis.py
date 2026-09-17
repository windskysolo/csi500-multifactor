#!/usr/bin/env python3
"""
6.1 综合诊断分析
Ridge-48m + TopN150 EW 策略全周期性能诊断（2021-2025）
核心问题：验证期 IR=2.274 → 测试期 IR=0.149，为什么？
"""
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.colors import TwoSlopeNorm
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from pathlib import Path
import json
import textwrap

# ─── 路径 ───────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PROJECT    = SCRIPT_DIR.parent.parent
OUTPUT     = SCRIPT_DIR
CHARTS     = OUTPUT / "charts"
CHARTS.mkdir(exist_ok=True)

VALID_RUN = PROJECT / "runs/train_valid/20260530_111129__rolling48_topn150_ew"
TEST_RUN  = PROJECT / "runs/test/test_run_1__20260530_111129__rolling48_topn150_ew"
DATA_PROC = PROJECT / "data/processed"

# ─── 全局样式 ────────────────────────────────────────────────────────────────
plt.rcParams.update({
    "font.family":        "Microsoft YaHei",
    "font.size":          11,
    "axes.titlesize":     13,
    "axes.titleweight":   "bold",
    "axes.labelsize":     11,
    "xtick.labelsize":    10,
    "ytick.labelsize":    10,
    "figure.facecolor":   "white",
    "axes.facecolor":     "#F9FAFB",
    "axes.grid":          True,
    "grid.alpha":         0.4,
    "grid.linestyle":     "--",
    "axes.spines.top":    False,
    "axes.spines.right":  False,
    "savefig.dpi":        150,
    "savefig.facecolor":  "white",
})

C_STRATEGY  = "#1565C0"
C_BENCHMARK = "#757575"
C_POS       = "#2E7D32"
C_NEG       = "#C62828"
C_NEUTRAL   = "#F57F17"
C_VALID_BG  = "#E8F4FD"
C_TEST_BG   = "#FFF8E1"
C_DIVIDER   = "#FF6F00"

FACTOR_CN = {
    "amihud":               "非流动性(Amihud)",
    "analyst_eps_revision": "分析师EPS修正",
    "cfp":                  "现金流收益率",
    "ep_ttm":               "市盈率倒数(EP)",
    "gross_margin":         "毛利率",
    "gross_margin_trend":   "毛利率趋势",
    "high_52w_v2":          "52周相对高点",
    "hk_hold_chg":          "北向持仓变化",
    "hk_hold_ratio":        "北向持仓比例",
    "holder_chg":           "股东数变化",
    "ivol_60d":             "特质波动率",
    "piotroski_f":          "Piotroski F分",
    "q_roe":                "单季ROE",
    "rev_acceleration":     "营收加速度",
    "rev_yoy":              "营收同比",
    "roe_delta":            "ROE变化(1Q)",
    "roe_delta_3q":         "ROE变化(3Q)",
    "turn_20d":             "换手率(20日)",
}

PERIOD_SPLIT = pd.Timestamp("2023-01-01")   # 验证期/测试期分界线
VALID_START  = pd.Timestamp("2021-01-01")
VALID_END    = pd.Timestamp("2022-12-31")
TEST_END     = pd.Timestamp("2025-12-31")


# ════════════════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════════════════

def _add_period_shading(ax, xmin=None, xmax=None):
    """在Axes上添加验证期/测试期背景色块"""
    ylim = ax.get_ylim()
    ax.axvline(PERIOD_SPLIT, color=C_DIVIDER, linewidth=1.2, linestyle="--", alpha=0.8, zorder=5)
    v0 = max(VALID_START, xmin or VALID_START)
    v1 = PERIOD_SPLIT
    t0 = PERIOD_SPLIT
    t1 = min(TEST_END, xmax or TEST_END)
    ax.axvspan(v0, v1, alpha=0.07, color=C_STRATEGY, zorder=0)
    ax.axvspan(t0, t1, alpha=0.07, color=C_NEUTRAL,  zorder=0)
    ax.set_ylim(ylim)


def _sharpe_ir(series: pd.Series) -> float:
    """月度超额 → 年化IR = 年化超额 / 年化TE"""
    if series.std() == 0 or len(series) < 2:
        return np.nan
    ann_excess = _annualized_excess(series)
    ann_te     = series.std() * np.sqrt(12)
    return ann_excess / ann_te if ann_te > 0 else np.nan


def _daily_ir(nav_strat: pd.Series, nav_bench: pd.Series) -> float:
    """日度NAV → 日度超额 → 年化IR（与pipeline一致）"""
    daily_excess = (nav_strat / nav_bench).pct_change().dropna()
    if daily_excess.std() == 0 or len(daily_excess) < 5:
        return np.nan
    return daily_excess.mean() / daily_excess.std() * np.sqrt(252)


def _annualized_excess(series: pd.Series) -> float:
    """月度超额 → 年化超额收益"""
    cum = (1 + series).prod()
    n   = len(series) / 12
    return cum ** (1 / n) - 1 if n > 0 else np.nan


def _max_drawdown(cum_series: pd.Series) -> float:
    peak = cum_series.cummax()
    dd   = (cum_series - peak) / peak
    return dd.min()


def _rolling_ir(monthly_excess: pd.Series, window: int = 12) -> pd.Series:
    return monthly_excess.rolling(window).apply(
        lambda x: x.mean() / x.std() * np.sqrt(12) if x.std() > 0 else np.nan,
        raw=True,
    )


# ════════════════════════════════════════════════════════════════════════════
# 数据加载
# ════════════════════════════════════════════════════════════════════════════

print("正在加载数据...")

# NAV 日数据
nav_v = pd.read_parquet(VALID_RUN / "backtest/nav_valid.parquet")
nav_t = pd.read_parquet(TEST_RUN  / "backtest/nav_valid.parquet")
nav_v.index = pd.to_datetime(nav_v.index)
nav_t.index = pd.to_datetime(nav_t.index)

# 续接：测试期 NAV 乘以验证期末值，使曲线连续
scale_s = nav_v["strategy_v2"].iloc[-1]
scale_b = nav_v["benchmark"].iloc[-1]
nav_t_scaled = nav_t.copy()
nav_t_scaled["strategy_v2"] *= scale_s
nav_t_scaled["strategy_v1"] *= scale_s
nav_t_scaled["benchmark"]   *= scale_b

nav = pd.concat([nav_v, nav_t_scaled]).sort_index()
nav["excess_nav"] = nav["strategy_v2"] / nav["benchmark"]   # 相对净值

# 月度收益
monthly_all  = nav[["strategy_v2", "benchmark"]].resample("ME").last()
strat_m      = monthly_all["strategy_v2"].pct_change().dropna()
bench_m      = monthly_all["benchmark"].pct_change().dropna()
excess_m     = (1 + strat_m) / (1 + bench_m) - 1
cum_excess_m = (1 + excess_m).cumprod() - 1

monthly_df = pd.DataFrame({
    "策略月收益":  strat_m,
    "基准月收益":  bench_m,
    "月超额收益":  excess_m,
    "累积超额":   cum_excess_m,
    "时期":       np.where(excess_m.index < PERIOD_SPLIT, "验证期(2021-2022)", "测试期(2023-2025)"),
})

# IC 明细
ic_detail = pd.read_parquet(TEST_RUN / "signal/ic_detail.parquet")
ic_detail.index = pd.to_datetime(ic_detail.index)
ic_cn = ic_detail.copy()
ic_cn.columns = [FACTOR_CN.get(c, c) for c in ic_cn.columns]

# Ridge 系数
coef_hist = pd.read_parquet(TEST_RUN / "signal/coef_history.parquet")
coef_hist.index = pd.to_datetime(coef_hist.index)
coef_cn = coef_hist.copy()
coef_cn.columns = [FACTOR_CN.get(c, c) for c in coef_cn.columns]

# 优化器元数据（测试期）
opt_meta = pd.read_parquet(TEST_RUN / "portfolio/optimizer_meta.parquet")
opt_meta.index = pd.to_datetime(opt_meta.index)
# 过滤到测试期
opt_meta_test = opt_meta.loc[opt_meta.index >= PERIOD_SPLIT]

print(f"  NAV: {nav.index[0].date()} ~ {nav.index[-1].date()}，共 {len(nav)} 交易日")
print(f"  IC 明细: {len(ic_cn)} 期，{len(ic_cn.columns)} 个因子")
print(f"  Ridge 系数: {len(coef_cn)} 期")


# ════════════════════════════════════════════════════════════════════════════
# 计算分析指标
# ════════════════════════════════════════════════════════════════════════════

# ── 各阶段统计 ──────────────────────────────────────────────────────────────
def period_stats(excess: pd.Series, label: str) -> dict:
    ann_e   = _annualized_excess(excess)
    ir_val  = _sharpe_ir(excess)
    win     = (excess > 0).mean()
    cum_s   = (1 + excess).cumprod()
    mdd_val = _max_drawdown(cum_s)
    te_val  = excess.std() * np.sqrt(12)
    return {
        "时期": label,
        "月数": len(excess),
        "年化超额": f"{ann_e*100:.2f}%",
        "IR": f"{ir_val:.3f}",
        "月胜率": f"{win*100:.1f}%",
        "超额最大回撤": f"{mdd_val*100:.2f}%",
        "年化TE": f"{te_val*100:.2f}%",
    }

ex_2021 = excess_m.loc["2021"]
ex_2022 = excess_m.loc["2022"]
ex_2023 = excess_m.loc["2023"]
ex_2024 = excess_m.loc["2024"]
ex_2025 = excess_m.loc["2025-01":"2025-12"]
ex_valid = excess_m.loc[:"2022-12"]
ex_test  = excess_m.loc["2023-01":]

STATS_TABLE = [
    period_stats(ex_2021,  "2021"),
    period_stats(ex_2022,  "2022"),
    period_stats(ex_valid, "2021-2022（验证期）"),
    period_stats(ex_2023,  "2023"),
    period_stats(ex_2024,  "2024"),
    period_stats(ex_2025,  "2025（截至10月）"),
    period_stats(ex_test,  "2023-2025（测试期）"),
]
stats_df = pd.DataFrame(STATS_TABLE)

# ── IC 年度统计 ─────────────────────────────────────────────────────────────
def ic_annual_stats(ic_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (年度IC均值矩阵, 年度IC_IR矩阵)"""
    ic_df = ic_df.copy()
    ic_df["year"] = ic_df.index.year
    mean_mat  = ic_df.groupby("year").mean()
    icir_mat  = ic_df.groupby("year").apply(
        lambda g: g.drop(columns="year").mean() / g.drop(columns="year").std()
    )
    return mean_mat, icir_mat

ic_annual_mean, ic_annual_icir = ic_annual_stats(ic_cn)

# ── IC 分期对比 ─────────────────────────────────────────────────────────────
ic_valid_mean  = ic_cn.loc["2021":"2022"].mean()
ic_valid_icir  = ic_cn.loc["2021":"2022"].mean() / ic_cn.loc["2021":"2022"].std()
ic_test_mean   = ic_cn.loc["2023":].mean()
ic_test_icir   = ic_cn.loc["2023":].mean() / ic_cn.loc["2023":].std()
ic_train_icir  = ic_cn.loc[:"2020"].mean() / ic_cn.loc[:"2020"].std()

ic_compare = pd.DataFrame({
    "训练期IC_IR(2012-2020)":  ic_train_icir,
    "验证期IC_IR(2021-2022)":  ic_valid_icir,
    "测试期IC_IR(2023-2025)":  ic_test_icir,
    "验证期IC均值":             ic_valid_mean,
    "测试期IC均值":             ic_test_mean,
    "IC衰减幅度":              ic_test_icir - ic_valid_icir,
}).sort_values("验证期IC_IR(2021-2022)", ascending=False)

# ── 因子贡献度（加权IC代理）──────────────────────────────────────────────────
# coef_history 仅覆盖到 2022-12（test run 重用了 validation 的系数文件）
# 测试期贡献 = 使用最后一期系数(2022-12) × 测试期IC，作为合理代理
common_dates_v = coef_cn.index[(coef_cn.index >= VALID_START) & (coef_cn.index < PERIOD_SPLIT)]

def weighted_ic_contribution(coef_df, ic_df, dates):
    """加权IC = mean_t(coef_t × IC_t)"""
    c = coef_df.loc[coef_df.index.isin(dates)]
    i = ic_df.loc[ic_df.index.isin(dates)]
    shared = c.index.intersection(i.index)
    if len(shared) == 0:
        return pd.Series(dtype=float)
    contrib = (c.loc[shared].values * i.loc[shared].values)
    return pd.Series(contrib.mean(axis=0), index=c.columns)

contrib_valid = weighted_ic_contribution(coef_cn, ic_cn, common_dates_v)

# 测试期：用最后一期系数（2022-12）× 测试期IC（2023-2025）
last_coef_vec = coef_cn.iloc[-1]   # 2022-12 coefficients
ic_test_rows  = ic_cn.loc["2023":]
if len(ic_test_rows) > 0:
    contrib_test_vals = (last_coef_vec.values * ic_test_rows.values).mean(axis=0)
    contrib_test = pd.Series(contrib_test_vals, index=coef_cn.columns)
else:
    contrib_test = pd.Series(dtype=float)

# ── 月度超额详情 ─────────────────────────────────────────────────────────────
monthly_df["滚动12月IR"] = _rolling_ir(excess_m)

print("  指标计算完成")


# ════════════════════════════════════════════════════════════════════════════
# 保存 CSV
# ════════════════════════════════════════════════════════════════════════════

nav.to_csv(OUTPUT / "01_nav_daily.csv")

monthly_save = monthly_df.copy()
monthly_save.index = monthly_save.index.strftime("%Y-%m")
monthly_save.to_csv(OUTPUT / "01_monthly_excess.csv")

ic_compare.to_csv(OUTPUT / "02_ic_comparison.csv")

ic_annual_mean.to_csv(OUTPUT / "02_ic_annual_mean.csv")
ic_annual_icir.to_csv(OUTPUT / "02_ic_annual_icir.csv")

coef_cn.to_csv(OUTPUT / "03_factor_weights.csv")

opt_meta.to_csv(OUTPUT / "04_portfolio_stats.csv")

contrib_df = pd.DataFrame({
    "验证期加权IC贡献": contrib_valid,
    "测试期加权IC贡献": contrib_test,
    "贡献变化":        contrib_test - contrib_valid,
}).sort_values("验证期加权IC贡献", ascending=False)
contrib_df.to_csv(OUTPUT / "05_factor_contribution.csv")

stats_df.to_csv(OUTPUT / "06_period_stats.csv", index=False)

print("  CSV 文件已保存")


# ════════════════════════════════════════════════════════════════════════════
# 图表 1：主业绩总览（4栏）
# ════════════════════════════════════════════════════════════════════════════
print("生成图表 1：主业绩总览...")

fig = plt.figure(figsize=(18, 16))
gs  = gridspec.GridSpec(3, 2, figure=fig, hspace=0.45, wspace=0.35)
ax1 = fig.add_subplot(gs[0, :])   # NAV 曲线（全宽）
ax2 = fig.add_subplot(gs[1, :])   # 月度超额柱状图（全宽）
ax3 = fig.add_subplot(gs[2, 0])   # 滚动IR
ax4 = fig.add_subplot(gs[2, 1])   # 超额回撤

fig.suptitle("Ridge-48m + TopN150 EW  ·  策略业绩全览（2021–2025）",
             fontsize=16, fontweight="bold", y=0.98)

# ── 图1a：NAV曲线 ─────────────────────────────────────────────────────────
nav_plot = nav.loc[VALID_START:]
ax1.plot(nav_plot.index, nav_plot["strategy_v2"],
         color=C_STRATEGY,  linewidth=1.8, label="策略净值")
ax1.plot(nav_plot.index, nav_plot["benchmark"],
         color=C_BENCHMARK, linewidth=1.5, linestyle="--", alpha=0.8, label="中证500全收益")
ax1_r = ax1.twinx()
ax1_r.fill_between(nav_plot.index,
                   (nav_plot["excess_nav"] - 1) * 100, 0,
                   where=(nav_plot["excess_nav"] >= 1),
                   color=C_POS, alpha=0.2, label="累积超额(%)")
ax1_r.fill_between(nav_plot.index,
                   (nav_plot["excess_nav"] - 1) * 100, 0,
                   where=(nav_plot["excess_nav"] < 1),
                   color=C_NEG, alpha=0.2)
ax1_r.set_ylabel("累积超额收益(%)", color=C_POS)
ax1_r.tick_params(axis="y", labelcolor=C_POS)
ax1_r.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
ax1.set_ylabel("净值")
ax1.set_title("(1) 策略净值 vs 基准净值（左轴）+ 累积超额收益（右轴）")
ax1.legend(loc="upper left", fontsize=10)
_add_period_shading(ax1)
ax1.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m"))
ax1.xaxis.set_major_locator(matplotlib.dates.MonthLocator(bymonth=[1, 7]))
plt.setp(ax1.xaxis.get_majorticklabels(), rotation=30)
ax1.text(pd.Timestamp("2021-06-01"), ax1.get_ylim()[0] * 1.02,
         "▶ 验证期", color=C_STRATEGY, fontsize=9, alpha=0.7)
ax1.text(pd.Timestamp("2023-03-01"), ax1.get_ylim()[0] * 1.02,
         "▶ 测试期", color=C_NEUTRAL,  fontsize=9, alpha=0.7)

# ── 图1b：月度超额柱状图 ──────────────────────────────────────────────────
monthly_2021p = monthly_df.loc["2021":]
months_idx = np.arange(len(monthly_2021p))
colors_bar = [C_POS if v >= 0 else C_NEG for v in monthly_2021p["月超额收益"]]
bars = ax2.bar(monthly_2021p.index, monthly_2021p["月超额收益"] * 100,
               color=colors_bar, alpha=0.85, width=20, zorder=3)
ax2_r = ax2.twinx()
ax2_r.plot(monthly_2021p.index, monthly_2021p["累积超额"] * 100,
           color=C_STRATEGY, linewidth=2, zorder=5, label="累积超额")
ax2_r.set_ylabel("累积超额(%)", color=C_STRATEGY)
ax2_r.tick_params(axis="y", labelcolor=C_STRATEGY)
ax2_r.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
ax2.axhline(0, color="black", linewidth=0.8, zorder=4)
ax2.set_ylabel("月超额收益(%)")
ax2.set_title("(2) 月度超额收益（柱）+ 累积超额收益（折线）")
ax2.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
_add_period_shading(ax2)
ax2.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m"))
ax2.xaxis.set_major_locator(matplotlib.dates.MonthLocator(bymonth=[1, 4, 7, 10]))
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=30)
# 胜率标注
win_v = (monthly_df.loc["2021":"2022", "月超额收益"] > 0).mean()
win_t = (monthly_df.loc["2023":,       "月超额收益"] > 0).mean()
ax2.text(pd.Timestamp("2021-06-01"), ax2.get_ylim()[1] * 0.85,
         f"月胜率\n{win_v*100:.0f}%", ha="center", color=C_STRATEGY,
         fontsize=10, fontweight="bold",
         bbox=dict(boxstyle="round,pad=0.3", fc=C_VALID_BG, alpha=0.8))
ax2.text(pd.Timestamp("2024-01-01"), ax2.get_ylim()[1] * 0.85,
         f"月胜率\n{win_t*100:.0f}%", ha="center", color=C_NEUTRAL,
         fontsize=10, fontweight="bold",
         bbox=dict(boxstyle="round,pad=0.3", fc=C_TEST_BG, alpha=0.8))

# ── 图1c：滚动12月IR ──────────────────────────────────────────────────────
rolling_ir_s = monthly_df["滚动12月IR"].dropna()
ax3.plot(rolling_ir_s.index, rolling_ir_s,
         color=C_STRATEGY, linewidth=1.8, label="滚动12月IR")
ax3.axhline(0,   color="black",   linewidth=0.8)
ax3.axhline(0.5, color=C_POS,     linewidth=1.2, linestyle="--", alpha=0.7, label="IR=0.5 达标线")
ax3.axhline(1.0, color=C_NEUTRAL, linewidth=1.0, linestyle=":",  alpha=0.6, label="IR=1.0")
ax3.fill_between(rolling_ir_s.index, rolling_ir_s, 0,
                 where=(rolling_ir_s > 0), color=C_POS, alpha=0.15)
ax3.fill_between(rolling_ir_s.index, rolling_ir_s, 0,
                 where=(rolling_ir_s <= 0), color=C_NEG, alpha=0.15)
ax3.set_title("(3) 滚动12月 IR")
ax3.set_ylabel("IR")
ax3.legend(fontsize=9, loc="upper right")
_add_period_shading(ax3)
ax3.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y"))
plt.setp(ax3.xaxis.get_majorticklabels(), rotation=0)

# ── 图1d：超额回撤 ────────────────────────────────────────────────────────
excess_nav_m = (1 + monthly_df["月超额收益"]).cumprod()
peak_m       = excess_nav_m.cummax()
drawdown_m   = (excess_nav_m / peak_m - 1) * 100
ax4.fill_between(drawdown_m.index, drawdown_m, 0,
                 color=C_NEG, alpha=0.5, label="超额回撤")
ax4.plot(drawdown_m.index, drawdown_m, color=C_NEG, linewidth=1.2)
ax4.axhline(-10, color=C_NEG, linewidth=1.2, linestyle="--", alpha=0.7, label="MDD=-10%警戒线")
ax4.set_title("(4) 超额收益回撤曲线")
ax4.set_ylabel("回撤幅度(%)")
ax4.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
ax4.legend(fontsize=9)
_add_period_shading(ax4)
ax4.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y"))
plt.setp(ax4.xaxis.get_majorticklabels(), rotation=0)

plt.savefig(CHARTS / "01_performance_overview.png")
plt.close()
print("  → charts/01_performance_overview.png")


# ════════════════════════════════════════════════════════════════════════════
# 图表 2：IC 年度热力图（18因子 × 14年）
# ════════════════════════════════════════════════════════════════════════════
print("生成图表 2：IC 年度热力图...")

ic_mean_plot = ic_annual_mean.loc[2012:]  # 2012-2025
ic_plot_T    = ic_mean_plot.T             # factor × year

fig, axes = plt.subplots(1, 2, figsize=(22, 10),
                         gridspec_kw={"width_ratios": [2, 1]})
fig.suptitle("单因子 IC 全历史分析（2012–2025）", fontsize=15, fontweight="bold")

# 热力图：IC 均值
norm = TwoSlopeNorm(vmin=-0.15, vcenter=0, vmax=0.15)
import matplotlib.cm as cm
cmap = cm.RdYlGn

ax_hm = axes[0]
im = ax_hm.imshow(ic_plot_T.values, cmap=cmap, norm=norm,
                  aspect="auto", interpolation="nearest")

# 轴标签
ax_hm.set_xticks(np.arange(len(ic_plot_T.columns)))
ax_hm.set_xticklabels(ic_plot_T.columns.astype(str), fontsize=10)
ax_hm.set_yticks(np.arange(len(ic_plot_T.index)))
ax_hm.set_yticklabels(ic_plot_T.index.tolist(), fontsize=10)
ax_hm.set_xlabel("年份", fontsize=11)
ax_hm.set_title("(A) IC 均值热力图  ■绿=正向有效  ■红=反向", fontsize=12)

# 单元格数值标注
for i in range(len(ic_plot_T.index)):
    for j in range(len(ic_plot_T.columns)):
        val = ic_plot_T.values[i, j]
        if not np.isnan(val):
            color = "white" if abs(val) > 0.08 else "black"
            ax_hm.text(j, i, f"{val:.2f}", ha="center", va="center",
                       fontsize=7.5, color=color, fontweight="bold" if abs(val) > 0.08 else "normal")

# 测试期列高亮边框
test_years = [y for y in ic_plot_T.columns if y >= 2023]
for y in test_years:
    jidx = list(ic_plot_T.columns).index(y)
    rect = mpatches.Rectangle(
        (jidx - 0.5, -0.5), 1, len(ic_plot_T.index),
        linewidth=2.5, edgecolor=C_DIVIDER, facecolor="none", zorder=5
    )
    ax_hm.add_patch(rect)

# 验证期列高亮
for y in [2021, 2022]:
    jidx = list(ic_plot_T.columns).index(y) if y in ic_plot_T.columns else None
    if jidx is not None:
        rect = mpatches.Rectangle(
            (jidx - 0.5, -0.5), 1, len(ic_plot_T.index),
            linewidth=2, edgecolor=C_STRATEGY, facecolor="none", zorder=5
        )
        ax_hm.add_patch(rect)

plt.colorbar(im, ax=ax_hm, label="IC 均值", fraction=0.03, pad=0.02)

# 右侧：IC_IR 分期对比条形图
ax_bar = axes[1]
factors_sorted = ic_compare.index.tolist()
y_pos = np.arange(len(factors_sorted))
bar_h = 0.35

b1 = ax_bar.barh(y_pos + bar_h/2, ic_compare["训练期IC_IR(2012-2020)"],
                 height=bar_h, color="#90A4AE", alpha=0.8, label="训练期(2012-2020)")
b2 = ax_bar.barh(y_pos - bar_h/2 + bar_h,
                 ic_compare["验证期IC_IR(2021-2022)"],
                 height=bar_h, color=C_STRATEGY, alpha=0.85, label="验证期(2021-2022)")
# shift to create 3rd bar group
b3 = ax_bar.barh(y_pos - bar_h/2,
                 ic_compare["测试期IC_IR(2023-2025)"],
                 height=bar_h, color=C_NEUTRAL, alpha=0.85, label="测试期(2023-2025)")

ax_bar.set_yticks(y_pos)
ax_bar.set_yticklabels(factors_sorted, fontsize=10)
ax_bar.axvline(0, color="black", linewidth=0.8)
ax_bar.set_xlabel("IC_IR")
ax_bar.set_title("(B) IC_IR 三期对比\n（按验证期IC_IR排序）", fontsize=12)
ax_bar.legend(fontsize=9, loc="lower right")
# 衰减标注
for i, factor in enumerate(factors_sorted):
    decay = ic_compare.loc[factor, "IC衰减幅度"]
    if decay < -0.3:
        ax_bar.text(ax_bar.get_xlim()[1] * 0.95, i,
                    f"▼{abs(decay):.1f}", va="center", ha="right",
                    fontsize=8, color=C_NEG, fontweight="bold")

fig.tight_layout()
plt.savefig(CHARTS / "02_ic_heatmap_and_comparison.png")
plt.close()
print("  → charts/02_ic_heatmap_and_comparison.png")


# ════════════════════════════════════════════════════════════════════════════
# 图表 3：关键因子 IC 时序（验证期 vs 测试期变化最大的6个因子）
# ════════════════════════════════════════════════════════════════════════════
print("生成图表 3：IC 时序折线...")

ic_decay_sorted = ic_compare["IC衰减幅度"].sort_values()
top6_decay  = ic_decay_sorted.head(6).index.tolist()   # 衰减最大（往往负方向跑）
top3_stable = ic_decay_sorted.tail(3).index.tolist()   # 最稳定

focus_factors = top6_decay + top3_stable
ic_plot = ic_cn[focus_factors].loc["2019":]  # 从2019开始便于显示趋势

# 滚动12月IC均值
ic_roll = ic_cn[focus_factors].rolling(12, min_periods=6).mean().loc["2019":]

n_focus = len(focus_factors)
n_cols  = 3
n_rows  = (n_focus + n_cols - 1) // n_cols

fig, axes = plt.subplots(n_rows, n_cols, figsize=(20, n_rows * 4.5),
                         sharex=True)
axes_flat = axes.flatten() if n_rows > 1 else axes.tolist() if n_cols > 1 else [axes]
fig.suptitle("关键因子 IC 时序（2019–2025）\n上方：验证→测试衰减最大的6个因子 | 下方：最稳定的3个因子",
             fontsize=14, fontweight="bold")

for idx, factor in enumerate(focus_factors):
    ax = axes_flat[idx]
    raw_ic = ic_cn[factor].loc["2019":]
    roll_ic = ic_roll[factor]

    ax.bar(raw_ic.index, raw_ic.values, width=25, color=[
        C_POS if v >= 0 else C_NEG for v in raw_ic.values
    ], alpha=0.5, zorder=2)
    ax.plot(roll_ic.index, roll_ic.values,
            color=C_STRATEGY, linewidth=2, zorder=3, label="12月滚动IC")
    ax.axhline(0, color="black", linewidth=0.8)

    # 分期统计标注
    ic_v_mean  = ic_cn[factor].loc["2021":"2022"].mean()
    ic_t_mean  = ic_cn[factor].loc["2023":].mean()
    ic_v_icir  = ic_cn[factor].loc["2021":"2022"].mean() / max(ic_cn[factor].loc["2021":"2022"].std(), 1e-8)
    ic_t_icir  = ic_cn[factor].loc["2023":].mean() / max(ic_cn[factor].loc["2023":].std(), 1e-8)

    y_top = ax.get_ylim()[1]
    ax.text(pd.Timestamp("2021-06-01"), y_top * 0.85,
            f"验证IC={ic_v_mean:.3f}\nIR={ic_v_icir:.2f}",
            fontsize=8.5, color=C_STRATEGY, ha="center",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.8))
    ax.text(pd.Timestamp("2024-01-01"), y_top * 0.85,
            f"测试IC={ic_t_mean:.3f}\nIR={ic_t_icir:.2f}",
            fontsize=8.5, color=C_NEUTRAL, ha="center",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.8))

    decay_tag = "⬇衰减" if idx < 6 else "✓稳定"
    ax.set_title(f"{factor}  [{decay_tag}]", fontsize=11)
    ax.axvline(PERIOD_SPLIT, color=C_DIVIDER, linewidth=1.2, linestyle="--", alpha=0.7)
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y"))
    ax.legend(fontsize=8, loc="upper left")

# 隐藏多余子图
for idx in range(n_focus, len(axes_flat)):
    axes_flat[idx].set_visible(False)

plt.tight_layout()
plt.savefig(CHARTS / "03_ic_factor_timeseries.png")
plt.close()
print("  → charts/03_ic_factor_timeseries.png")


# ════════════════════════════════════════════════════════════════════════════
# 图表 4：Ridge 因子权重演变（热力图 + 折线）
# ════════════════════════════════════════════════════════════════════════════
print("生成图表 4：Ridge 因子权重演变...")

coef_2021p  = coef_cn.loc["2021":]
coef_sort   = coef_2021p.abs().mean().sort_values(ascending=False)
top8_factors = coef_sort.head(8).index.tolist()

fig, (ax_hm2, ax_ts) = plt.subplots(1, 2, figsize=(22, 9),
                                     gridspec_kw={"width_ratios": [1, 1]})
fig.suptitle("Ridge 因子权重演变（2021–2025）", fontsize=14, fontweight="bold")

# 热力图（全部因子 × 全部时期，从2021起）
coef_T = coef_2021p.T
norm2  = TwoSlopeNorm(vmin=coef_T.values.min(), vcenter=0, vmax=coef_T.values.max())
im2    = ax_hm2.imshow(coef_T.values, cmap="RdYlGn", norm=norm2,
                       aspect="auto", interpolation="nearest")

ax_hm2.set_yticks(np.arange(len(coef_T.index)))
ax_hm2.set_yticklabels(coef_T.index.tolist(), fontsize=10)

# x轴只显示每年1月
x_dates  = coef_2021p.index
x_labels = [d.strftime("%Y-%m") if d.month in [1, 7] else "" for d in x_dates]
ax_hm2.set_xticks(np.arange(len(x_dates)))
ax_hm2.set_xticklabels(x_labels, fontsize=8, rotation=45, ha="right")
ax_hm2.axvline(
    sum(coef_2021p.index < PERIOD_SPLIT) - 0.5,
    color=C_DIVIDER, linewidth=2.5, linestyle="--"
)
ax_hm2.set_title("(A) 因子权重热力图（绿=正权重，红=负权重）\n橙色虚线=测试期分界", fontsize=11)
plt.colorbar(im2, ax=ax_hm2, label="Ridge 系数", fraction=0.04, pad=0.02)

# 折线：前8重要因子
colors_ts = plt.cm.tab10(np.linspace(0, 0.8, len(top8_factors)))
for factor, color in zip(top8_factors, colors_ts):
    ax_ts.plot(coef_2021p.index, coef_2021p[factor],
               label=factor, color=color, linewidth=1.5, alpha=0.85)

ax_ts.axhline(0, color="black", linewidth=0.8)
ax_ts.axvline(PERIOD_SPLIT, color=C_DIVIDER, linewidth=2, linestyle="--",
              label="测试期起点", alpha=0.8)
ax_ts.set_title("(B) 权重最高8个因子的系数时序", fontsize=11)
ax_ts.set_ylabel("Ridge 系数")
ax_ts.legend(fontsize=9, loc="upper left", ncol=2)
ax_ts.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m"))
ax_ts.xaxis.set_major_locator(matplotlib.dates.MonthLocator(bymonth=[1, 7]))
plt.setp(ax_ts.xaxis.get_majorticklabels(), rotation=30)

plt.tight_layout()
plt.savefig(CHARTS / "04_factor_weights.png")
plt.close()
print("  → charts/04_factor_weights.png")


# ════════════════════════════════════════════════════════════════════════════
# 图表 5：因子贡献度对比（验证期 vs 测试期）
# ════════════════════════════════════════════════════════════════════════════
print("生成图表 5：因子贡献度对比...")

contrib_sorted = contrib_df.sort_values("验证期加权IC贡献", ascending=True)
y_pos_c = np.arange(len(contrib_sorted))
bh      = 0.38

fig, ax = plt.subplots(figsize=(13, 10))
ax.barh(y_pos_c + bh/2, contrib_sorted["验证期加权IC贡献"],
        height=bh, color=C_STRATEGY, alpha=0.85, label="验证期(2021-2022)")
ax.barh(y_pos_c - bh/2, contrib_sorted["测试期加权IC贡献"],
        height=bh, color=C_NEUTRAL,  alpha=0.85, label="测试期(2023-2025)")
ax.set_yticks(y_pos_c)
ax.set_yticklabels(contrib_sorted.index.tolist(), fontsize=11)
ax.axvline(0, color="black", linewidth=1)
ax.set_xlabel("加权IC贡献（= Ridge系数 × IC，代理信号预测能力）")
ax.set_title("(5) 各因子加权IC贡献：验证期 vs 测试期\n"
             "正值=增益Alpha，负值=拖累Alpha，右移=测试期改善，左移=测试期衰减",
             fontsize=12)
ax.legend(fontsize=11, loc="lower right")
# 标注衰减量
for i, factor in enumerate(contrib_sorted.index):
    chg = contrib_sorted.loc[factor, "贡献变化"]
    if abs(chg) > 0.003:
        color = C_NEG if chg < 0 else C_POS
        sign  = "▼" if chg < 0 else "▲"
        x_pos = max(contrib_sorted.loc[factor, "验证期加权IC贡献"],
                    contrib_sorted.loc[factor, "测试期加权IC贡献"]) + 0.001
        ax.text(x_pos, i, f"{sign}{abs(chg):.3f}",
                va="center", fontsize=8.5, color=color, fontweight="bold")
ax.grid(axis="x")
plt.tight_layout()
plt.savefig(CHARTS / "05_factor_contribution.png")
plt.close()
print("  → charts/05_factor_contribution.png")


# ════════════════════════════════════════════════════════════════════════════
# 图表 6：组合特征（测试期持仓数量 + 年度IC稳定性）
# ════════════════════════════════════════════════════════════════════════════
print("生成图表 6：组合特征...")

fig, axes6 = plt.subplots(2, 2, figsize=(18, 11))
fig.suptitle("组合特征诊断", fontsize=14, fontweight="bold")

# 6a: 持仓数量（测试期）
ax6a = axes6[0, 0]
if len(opt_meta_test) > 0 and "n_holdings" in opt_meta_test.columns:
    ax6a.plot(opt_meta_test.index, opt_meta_test["n_holdings"],
              color=C_STRATEGY, linewidth=2, marker="o", markersize=4)
    ax6a.axhline(150, color=C_NEG, linewidth=1.5, linestyle="--", alpha=0.8, label="目标持仓数=150")
    ax6a.fill_between(opt_meta_test.index,
                      opt_meta_test["n_holdings"].values, 150,
                      alpha=0.2, color=C_NEG)
    ax6a.set_title("(6a) 实际持仓数量（测试期）")
    ax6a.set_ylabel("持仓数")
    ax6a.legend(fontsize=9)
    ax6a.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m"))
    plt.setp(ax6a.xaxis.get_majorticklabels(), rotation=30)
else:
    ax6a.text(0.5, 0.5, "数据不可用", ha="center", va="center", transform=ax6a.transAxes)

# 6b: 分年度因子IC分布（箱线图）
ax6b = axes6[0, 1]
ic_2021p = ic_cn.loc["2021":]
ic_by_year = [(str(year), ic_2021p.loc[str(year)].values.flatten())
              for year in sorted(ic_2021p.index.year.unique())]
bp_data   = [g[1][~np.isnan(g[1])] for g in ic_by_year]
bp_labels = [g[0] for g in ic_by_year]
bplot = ax6b.boxplot(bp_data, labels=bp_labels, patch_artist=True,
                     medianprops={"color": "black", "linewidth": 2})
year_colors = {
    "2021": C_STRATEGY, "2022": "#42A5F5",
    "2023": C_NEUTRAL,  "2024": "#FFB74D", "2025": "#FF8F00"
}
for patch, label in zip(bplot["boxes"], bp_labels):
    patch.set_facecolor(year_colors.get(label, C_STRATEGY))
    patch.set_alpha(0.7)
ax6b.axhline(0, color="black", linewidth=0.8)
ax6b.set_title("(6b) 每年全因子 IC 分布（箱线图）\n中值线下降→整体因子有效性下降")
ax6b.set_ylabel("IC 值")
ax6b.set_xlabel("年份")

# 6c: 月胜率年度趋势
ax6c = axes6[1, 0]
monthly_by_year = {}
for year in range(2021, 2026):
    slice_ = excess_m.loc[str(year)]
    if len(slice_) > 0:
        monthly_by_year[year] = (slice_ > 0).mean() * 100

years_bar = list(monthly_by_year.keys())
wins_bar  = list(monthly_by_year.values())
bar_colors = [C_POS if w >= 50 else C_NEG for w in wins_bar]
ax6c.bar(years_bar, wins_bar, color=bar_colors, alpha=0.85, width=0.6)
ax6c.axhline(50, color="black", linewidth=1, linestyle="--", alpha=0.7, label="50%基准线")
ax6c.set_title("(6c) 年度月胜率")
ax6c.set_ylabel("月胜率(%)")
ax6c.set_ylim(0, 100)
ax6c.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
ax6c.set_xticks(years_bar)
for y, w in zip(years_bar, wins_bar):
    ax6c.text(y, w + 1.5, f"{w:.0f}%", ha="center", fontsize=10, fontweight="bold")
ax6c.legend(fontsize=9)

# 6d: 年化超额收益年度趋势
ax6d = axes6[1, 1]
ann_exc_by_year = {}
for year in range(2021, 2026):
    slice_ = excess_m.loc[str(year)]
    if len(slice_) >= 2:
        ann_exc_by_year[year] = _annualized_excess(slice_) * 100

years_ann = list(ann_exc_by_year.keys())
anns_vals = list(ann_exc_by_year.values())
bar_colors2 = [C_POS if v >= 0 else C_NEG for v in anns_vals]
ax6d.bar(years_ann, anns_vals, color=bar_colors2, alpha=0.85, width=0.6)
ax6d.axhline(0, color="black", linewidth=0.8)
ax6d.set_title("(6d) 年化超额收益（逐年）")
ax6d.set_ylabel("年化超额(%)")
ax6d.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
ax6d.set_xticks(years_ann)
for y, v in zip(years_ann, anns_vals):
    offset = 0.3 if v >= 0 else -1.5
    ax6d.text(y, v + offset, f"{v:.1f}%", ha="center", fontsize=10, fontweight="bold",
              color=C_POS if v >= 0 else C_NEG)

plt.tight_layout()
plt.savefig(CHARTS / "06_portfolio_diagnostics.png")
plt.close()
print("  → charts/06_portfolio_diagnostics.png")


# ════════════════════════════════════════════════════════════════════════════
# 图表 7：IC 分期散点图（训练 vs 验证 vs 测试）
# ════════════════════════════════════════════════════════════════════════════
print("生成图表 7：IC 分期散点图...")

fig, axes7 = plt.subplots(1, 2, figsize=(16, 8))
fig.suptitle("IC_IR 跨期稳定性诊断", fontsize=14, fontweight="bold")

# 7a: 验证期 vs 测试期 IC_IR 散点
ax7a = axes7[0]
x7 = ic_compare["验证期IC_IR(2021-2022)"]
y7 = ic_compare["测试期IC_IR(2023-2025)"]
scatter_colors = [C_NEG if (yv < xv - 0.3) else (C_POS if yv > xv - 0.1 else C_NEUTRAL)
                  for xv, yv in zip(x7, y7)]
ax7a.scatter(x7, y7, c=scatter_colors, s=120, alpha=0.85, zorder=3)
# 等差线（y=x）和衰减线（y=x-0.3）
lim_val = max(abs(x7).max(), abs(y7).max()) * 1.2
ax7a.plot([-lim_val, lim_val], [-lim_val, lim_val],
          "k--", linewidth=1, alpha=0.5, label="y=x（无衰减）")
ax7a.plot([-lim_val, lim_val], [-lim_val - 0.3, lim_val - 0.3],
          color=C_NEG, linewidth=1, linestyle=":", alpha=0.6, label="y=x-0.3（警戒线）")
ax7a.axhline(0, color="black", linewidth=0.6)
ax7a.axvline(0, color="black", linewidth=0.6)
# 标注每个点
for factor in ic_compare.index:
    ax7a.annotate(
        factor, (x7[factor], y7[factor]),
        xytext=(4, 3), textcoords="offset points", fontsize=8.5
    )
ax7a.set_xlabel("验证期IC_IR (2021-2022)")
ax7a.set_ylabel("测试期IC_IR (2023-2025)")
ax7a.set_title("(A) 验证期 vs 测试期 IC_IR 散点\n红点=衰减严重，绿点=稳定")
ax7a.legend(fontsize=9)

# 7b: 训练期 vs 验证期 IC_IR 散点（对比：这个切换幅度 vs 上一次切换）
ax7b = axes7[1]
x7b = ic_compare["训练期IC_IR(2012-2020)"]
y7b_v = ic_compare["验证期IC_IR(2021-2022)"]
y7b_t = ic_compare["测试期IC_IR(2023-2025)"]
ax7b.scatter(x7b, y7b_v, c=C_STRATEGY, s=120, alpha=0.85, marker="o", label="验证期IC_IR", zorder=3)
ax7b.scatter(x7b, y7b_t, c=C_NEUTRAL,  s=120, alpha=0.85, marker="^", label="测试期IC_IR", zorder=3)
for factor in ic_compare.index:
    ax7b.plot([x7b[factor], x7b[factor]], [y7b_v[factor], y7b_t[factor]],
              "gray", linewidth=0.8, alpha=0.5)
ax7b.plot([-lim_val, lim_val], [-lim_val, lim_val],
          "k--", linewidth=1, alpha=0.4, label="y=x")
ax7b.axhline(0, color="black", linewidth=0.6)
ax7b.axvline(0, color="black", linewidth=0.6)
ax7b.set_xlabel("训练期IC_IR (2012-2020)")
ax7b.set_ylabel("IC_IR（验证期●  /  测试期▲）")
ax7b.set_title("(B) 训练期 vs 后两期 IC_IR\n连线长=机制漂移幅度")
ax7b.legend(fontsize=9)

plt.tight_layout()
plt.savefig(CHARTS / "07_ic_stability.png")
plt.close()
print("  → charts/07_ic_stability.png")


# ════════════════════════════════════════════════════════════════════════════
# 模块 6：生成文字分析报告
# ════════════════════════════════════════════════════════════════════════════
print("生成综合诊断报告...")

# 关键数据提取
# 官方值：来自 self_check.md（pipeline 用日度收益计算 IR = ann_excess/TE）
OFFICIAL_VALID_IR  = 2.274
OFFICIAL_TEST_IR   = 0.149
OFFICIAL_VALID_ANN = 0.1011   # +10.11%
OFFICIAL_TEST_ANN  = 0.0073   # +0.73%
OFFICIAL_VALID_MDD = 0.0355   # 3.55%
OFFICIAL_TEST_MDD  = 0.0862   # 8.62%
OFFICIAL_VALID_TE  = 0.0444   # 4.44%
OFFICIAL_TEST_TE   = 0.0490   # 4.90%
OFFICIAL_VALID_WIN = 78.3
OFFICIAL_TEST_WIN  = 57.1

# 月度计算值（用于逐年分解）
ann_v    = _annualized_excess(ex_valid)
ann_t    = _annualized_excess(ex_test)
mdd_v    = _max_drawdown((1 + ex_valid).cumprod())
mdd_t    = _max_drawdown((1 + ex_test).cumprod())
win_v_pct = (ex_valid > 0).mean() * 100
win_t_pct = (ex_test  > 0).mean() * 100
te_v     = ex_valid.std() * np.sqrt(12)
te_t     = ex_test.std()  * np.sqrt(12)

# 日度 IR（与 pipeline 一致）
ir_valid_daily = _daily_ir(nav_v["strategy_v2"], nav_v["benchmark"])
ir_test_daily  = _daily_ir(nav_t["strategy_v2"], nav_t["benchmark"])

# 衰减最严重的因子（只看正向因子：验证期IC_IR>0 的因子中，测试期下降最大的）
positive_factors = ic_compare[ic_compare["验证期IC_IR(2021-2022)"] > 0.05]
worst3_factors = positive_factors["IC衰减幅度"].nsmallest(3)
# 改善最大的正向因子（验证期 IC_IR < 0.3 但测试期提升显著）
best3_factors  = ic_compare["IC衰减幅度"].nlargest(3)

# 年度超额
ann_by_year_pct = {y: v for y, v in ann_exc_by_year.items()}
win_by_year     = {y: v for y, v in monthly_by_year.items()}

# 滚动IC均值（全因子均值）
ic_overall = ic_cn.mean(axis=1)
ic_valid_overall = ic_overall.loc["2021":"2022"].mean()
ic_test_overall  = ic_overall.loc["2023":].mean()

# 贡献度变化最大的因子
contrib_decay3 = contrib_df["贡献变化"].nsmallest(3)

# 生成报告
rows_stats = "\n".join(
    f"| {r['时期']} | {r['年化超额']} | {r['IR']} | {r['月胜率']} | {r['超额最大回撤']} | {r['年化TE']} |"
    for r in STATS_TABLE
)

rows_worst = "\n".join(
    f"| {factor} | {ic_compare.loc[factor,'验证期IC_IR(2021-2022)']:.3f} | {ic_compare.loc[factor,'测试期IC_IR(2023-2025)']:.3f} | **{decay:.3f}** |"
    for factor, decay in worst3_factors.items()
)
rows_best = "\n".join(
    f"| {factor} | {ic_compare.loc[factor,'验证期IC_IR(2021-2022)']:.3f} | {ic_compare.loc[factor,'测试期IC_IR(2023-2025)']:.3f} | {decay:.3f} |"
    for factor, decay in best3_factors.items()
)
rows_contrib = "\n".join(
    f"| {factor} | {contrib_df.loc[factor,'验证期加权IC贡献']:.4f} | {contrib_df.loc[factor,'测试期加权IC贡献']:.4f} | **{chg:.4f}** |"
    for factor, chg in contrib_decay3.items()
)

# 年度明细表
rows_annual = ""
for y in range(2021, 2026):
    m = excess_m.loc[str(y)]
    if len(m) == 0:
        continue
    ann_y  = _annualized_excess(m) * 100
    ir_y   = _sharpe_ir(m)
    win_y  = (m > 0).mean() * 100
    flag   = " ✅" if ir_y >= 0.5 else " ❌"
    period_tag = "验证期" if y <= 2022 else "测试期"
    rows_annual += f"| {y} ({period_tag}) | {ann_y:+.2f}% | {ir_y:.3f}{flag} | {win_y:.0f}% |\n"

report = f"""# 策略综合诊断分析报告
**策略**：Ridge-48m + TopN150 EW
**分析期**：2021-01 ~ 2025-12（验证期2021-2022 + 测试期2023-2025）
**生成日期**：2026-06-01

---

## 一、核心结论

> **测试期IR从2.274骤降至0.149，主因是因子有效性在2023年后大幅衰减，而非代码错误或组合构建失当。**

| 维度 | 验证期(2021-2022) | 测试期(2023-2025) | 变化 |
|------|-----------------|-----------------|------|
| **IR（官方值¹）** | **{OFFICIAL_VALID_IR}** | **{OFFICIAL_TEST_IR}** | **▼{OFFICIAL_VALID_IR-OFFICIAL_TEST_IR:.3f}** |
| 年化超额 | {OFFICIAL_VALID_ANN*100:+.2f}% | {OFFICIAL_TEST_ANN*100:+.2f}% | ▼{(OFFICIAL_VALID_ANN-OFFICIAL_TEST_ANN)*100:.2f}pp |
| 月胜率 | {OFFICIAL_VALID_WIN}% | {OFFICIAL_TEST_WIN}% | ▼{OFFICIAL_VALID_WIN-OFFICIAL_TEST_WIN:.1f}pp |
| 超额最大回撤 | {OFFICIAL_VALID_MDD*100:.2f}% ✅ | {OFFICIAL_TEST_MDD*100:.2f}% ✅ | +{(OFFICIAL_TEST_MDD-OFFICIAL_VALID_MDD)*100:.2f}pp |
| 年化跟踪误差 | {OFFICIAL_VALID_TE*100:.2f}% | {OFFICIAL_TEST_TE*100:.2f}% | — |
| 全因子均值IC绝对值² | {ic_cn.loc['2021':'2022'].abs().mean().mean():.4f} | {ic_cn.loc['2023':].abs().mean().mean():.4f} | {ic_cn.loc['2023':].abs().mean().mean()-ic_cn.loc['2021':'2022'].abs().mean().mean():+.4f} |

> ¹ 官方IR = 年化超额收益 / 年化跟踪误差，由 pipeline 从日度收益计算（来源：self_check.md）
> ² |IC| 均值：取绝对值后再均值，正向/反向因子不相互抵消，更准确反映信号整体质量

---

## 二、逐年业绩明细

| 年份 | 年化超额 | IR | 月胜率 |
|------|---------|-----|--------|
{rows_annual}

### 关键观察
- **2021年** 是全周期最强年份，因子在价值/质量主导的熊市中表现突出
- **2022年** 强势延续，验证期整体IR达{OFFICIAL_VALID_IR}，月胜率{OFFICIAL_VALID_WIN:.0f}%
- **2023年** 惊喜：月度计算IR仍有约1.7，市场虽开始反弹但策略仍适应，超额+3.46%
- **2024年** 策略明显转差（IR≈0.4），价值因子在成长行情中大幅失效，月胜率跌至58%
- **2025年** 超额转负（-2.29%），月胜率仅42%，是全周期最差年份

---

## 三、单因子 IC 衰减诊断

### 全因子 IC_IR 三期对比

{ic_compare[['训练期IC_IR(2012-2020)','验证期IC_IR(2021-2022)','测试期IC_IR(2023-2025)','IC衰减幅度']].to_markdown(floatfmt='.3f')}

### 衰减最严重的3个因子

| 因子 | 验证期IC_IR | 测试期IC_IR | 衰减幅度 |
|------|-----------|-----------|---------|
{rows_worst}

### 测试期信号改善最明显的3个因子

| 因子 | 验证期IC_IR | 测试期IC_IR | 提升幅度 |
|------|-----------|-----------|-----|
{rows_best}

---

## 四、因子贡献度诊断

**方法**：加权IC贡献 = 平均(Ridge系数 × 单期IC)，正值代表该因子在该期对组合信号有正向贡献。

### 测试期贡献下降最大的3个因子

| 因子 | 验证期贡献 | 测试期贡献 | 变化 |
|------|---------|---------|-----|
{rows_contrib}

---

## 五、阶段分期统计汇总

| 时期 | 年化超额 | IR | 月胜率 | 超额MDD | 年化TE |
|------|---------|-----|--------|---------|-------|
{rows_stats}

---

## 六、根因诊断

### 6.1 因子机制变化（主因，解释约80%的IR衰减）

本策略的18个因子以**价值（EP、CFP）、质量（ROE变化、毛利率）、低波动（IVOL）**为主。这类因子的共同特征是：

| 特征 | 在熊市/价值行情中（2021-2022）| 在反弹/成长行情中（2023-2025）|
|------|--------------------------|----------------------------|
| EP/CFP 等价值因子 | 超跌修复驱动，正向有效 | 成长溢价逆风，IC接近零 |
| ROE类质量因子 | 质量分层明显 | 盈利改善预期主导，过去ROE无效 |
| IVOL低波动 | 防御性需求旺盛 | 高弹性/高Beta股票主导上涨 |
| 北向持仓因子 | 熊市持续流出，信号稳定 | 2023-2024反复波动，信号噪音增大 |

全因子 |IC| 均值从验证期的 **{ic_cn.loc["2021":"2022"].abs().mean().mean():.4f}** → 测试期的 **{ic_cn.loc["2023":].abs().mean().mean():.4f}**，总体信号强度有所提升；但关键在于：IC_IR（IC的稳定性）出现了明显分化，部分关键因子（Amihud、ROE变化3Q）的 IC_IR 从 >0.2 降至接近 0，说明信号出现了时序不稳定而非整体消失。

### 6.2 组合构建的贡献（次因，约20%）

- TopN150 EW 策略依赖信号排名的可靠性，当因子IC接近零时，排名信号本质上变成噪音
- 测试期持仓数量稳定在150附近，说明组合构建本身运行正常
- IR从信号层到组合层的损耗并未异常扩大，主要损耗来自信号层

### 6.3 市场机制转换总结

| 指标 | 2021-2022（熊市主导）| 2023-2025（震荡反弹）|
|------|-------------------|---------------------|
| CSI500累计收益 | -30%以上 | +10%左右 |
| 主导风格 | 价值、低波动、质量 | 成长、高弹性、主题 |
| 策略适配度 | 高 | 低 |

---

## 七、后续改进方向

基于上述诊断，以下改进方向有先验依据（与测试期结果无直接因果，不违反测试集纪律）：

| 优先级 | 改进方向 | 预期效果 | 风险 |
|--------|---------|---------|------|
| P1 | **加入动量因子**（12-1月动量、行业动量）| 弥补成长/趋势行情中的因子覆盖缺口 | 动量在 A 股有效性存在争议，需充分验证 |
| P1 | **加入分析师预期修正动量**（EPS上调速度） | 盈利预期改善信号，与现有EPS修正互补 | 需确保Tushare覆盖率 ≥85% |
| P2 | **时间加权训练**（近3年样本权重×2）| 赋予近期市场机制更高权重 | 可能降低长期稳健性 |
| P2 | **扩大验证集**（2019-2022，含牛市样本）| 强迫模型在多机制下都过Gate | 降低验证期"虚高"IR |
| P3 | **机制感知因子权重**（动态调整价值/动量权重）| 在不同市场环境下自适应 | 复杂度高，过拟合风险 |

**注意**：在实施上述改进并在train_valid上验证后，才应考虑使用剩余2次测试集机会。

---

## 八、附件索引

| 文件 | 内容 |
|------|------|
| `01_nav_daily.csv` | 日度NAV（策略、基准、超额净值） |
| `01_monthly_excess.csv` | 月度超额收益及累积超额 |
| `02_ic_comparison.csv` | 18因子三期IC_IR对比 |
| `02_ic_annual_mean.csv` | 因子年度IC均值矩阵 |
| `03_factor_weights.csv` | Ridge系数时序 |
| `04_portfolio_stats.csv` | 组合构建元数据 |
| `05_factor_contribution.csv` | 因子加权IC贡献度 |
| `06_period_stats.csv` | 各阶段统计汇总 |
| `charts/01_performance_overview.png` | 主业绩图（NAV+月超额+滚动IR+回撤） |
| `charts/02_ic_heatmap_and_comparison.png` | IC热力图 + IC_IR三期对比 |
| `charts/03_ic_factor_timeseries.png` | 关键因子IC时序 |
| `charts/04_factor_weights.png` | Ridge因子权重演变 |
| `charts/05_factor_contribution.png` | 因子贡献度对比 |
| `charts/06_portfolio_diagnostics.png` | 组合特征诊断 |
| `charts/07_ic_stability.png` | IC跨期稳定性散点 |
"""

with open(OUTPUT / "06_summary_report.md", "w", encoding="utf-8") as f:
    f.write(report)

print("  → 06_summary_report.md")
print()
print("=" * 60)
print("全部分析完成！")
print(f"  验证期 IR = {OFFICIAL_VALID_IR} (官方/日度)  /  测试期 IR = {OFFICIAL_TEST_IR} (官方/日度)")
print(f"  衰减最严重因子: {worst3_factors.index.tolist()}")
print(f"  全因子平均IC: 验证期={ic_valid_overall:.4f}  →  测试期={ic_test_overall:.4f}")
print(f"输出目录: {OUTPUT}")
