"""
实验：超额收益衰减归因
- 实验A：IC 与市场涨跌相关性回归
- 实验B：时间趋势检验（控制市场后）
- 实验C：市场环境分层回测对比

判定：牛市效应(H1) vs 因子时间衰减(H2)
"""

import sys
import io
import warnings
from pathlib import Path

# 强制 UTF-8 输出，避免 Windows GBK 编码错误
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.ticker as mticker
import scipy.stats as stats
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent.parent
TS_DIR  = ROOT / "runs/test/test_run_1__20260602_104439__rolling48_topn150_ew_hk_quarterly"
TV_DIR  = ROOT / "runs/train_valid/20260602_104439__rolling48_topn150_ew_hk_quarterly"
OUT_DIR = Path(__file__).parent / "analysis_charts"
OUT_DIR.mkdir(exist_ok=True)

plt.rcParams["font.family"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# ══════════════════════════════════════════════════════════════════════════════
# 数据准备
# ══════════════════════════════════════════════════════════════════════════════
print("Loading data...")

# ── 全期日度基准 NAV（中证500全收益）────────────────────────────────────────
idx_q = pd.read_parquet(ROOT / "data/processed/index_quote.parquet")
bench_daily = idx_q["nav"]  # 2011-01-04 ~ 2025-12-31

# ── 月度基准收益率 ─────────────────────────────────────────────────────────
bench_monthly = bench_daily.resample("ME").last().pct_change().dropna()
bench_monthly.name = "bench_ret"

# ── 因子 IC（全期，2012-2025）───────────────────────────────────────────────
ic = pd.read_parquet(TS_DIR / "signal/ic_detail.parquet")
# ic.index 是月末日，与 bench_monthly 对齐
ic.index = ic.index.to_period("M").to_timestamp("M")

# ── 合成 IC（18 个因子等权均值）─────────────────────────────────────────────
ic_composite = ic.mean(axis=1)
ic_composite.name = "composite_ic"

# ── 月度超额收益（验证期 + 测试期）─────────────────────────────────────────
def monthly_excess(nav_s, nav_b):
    ms = nav_s.resample("ME").last().pct_change().dropna()
    mb = nav_b.resample("ME").last().pct_change().dropna()
    idx = ms.index.intersection(mb.index)
    return (ms.loc[idx] - mb.loc[idx])

nav_tv = pd.read_parquet(TV_DIR / "backtest/nav_valid.parquet")
nav_ts = pd.read_parquet(TS_DIR / "backtest/nav_valid.parquet")
me_tv  = monthly_excess(nav_tv["strategy_v1"], nav_tv["benchmark"])
me_ts  = monthly_excess(nav_ts["strategy_v1"], nav_ts["benchmark"])
monthly_excess_all = pd.concat([me_tv, me_ts])
monthly_excess_all = monthly_excess_all[~monthly_excess_all.index.duplicated(keep="first")]
monthly_excess_all.index = monthly_excess_all.index.to_period("M").to_timestamp("M")
monthly_excess_all.name = "excess_ret"

# ── 合并分析用 DataFrame ─────────────────────────────────────────────────
bench_monthly.index = bench_monthly.index.to_period("M").to_timestamp("M")
df_full = pd.concat([ic_composite, bench_monthly], axis=1).dropna()
df_full["time_index"] = np.arange(len(df_full))  # 月序号（用于时间趋势检验）
df_full["year"] = df_full.index.year

# 合并超额收益（只有 2021-2025 才有）
df_obs = pd.concat([monthly_excess_all, bench_monthly], axis=1).dropna()
df_obs["year"] = df_obs.index.year

print(f"IC 序列: {len(df_full)} 期  {str(df_full.index[0])[:7]} ~ {str(df_full.index[-1])[:7]}")
print(f"超额收益: {len(df_obs)} 期  {str(df_obs.index[0])[:7]} ~ {str(df_obs.index[-1])[:7]}")

# ══════════════════════════════════════════════════════════════════════════════
# 实验 A：IC 与市场涨跌的相关性回归
# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 实验A：IC ~ benchmark_ret 回归 ===")

from scipy.stats import pearsonr
from numpy.polynomial import polynomial as P

X_a = df_full["bench_ret"].values
Y_a = df_full["composite_ic"].values

slope_a, intercept_a, r_a, p_a, se_a = stats.linregress(X_a, Y_a)
print(f"  β (市场敏感度):  {slope_a:.4f}")
print(f"  t 统计量:       {slope_a/se_a:.3f}")
print(f"  p 值:           {p_a:.4f}")
print(f"  R²:             {r_a**2:.4f}")

if p_a < 0.05 and slope_a < 0:
    print("  => IC 在牛市中显著下降 → 支持 H1（牛市效应）")
elif p_a < 0.05 and slope_a > 0:
    print("  => IC 在牛市中显著上升（异常）")
else:
    print("  => IC 与市场涨跌无显著关系 → 不支持 H1（牛市效应）")

# 各因子分别做回归
print("\n  各因子 IC ~ bench_ret 回归：")
factor_market_betas = {}
for f in ic.columns:
    yf = ic[f].reindex(df_full.index).values
    mask = ~np.isnan(yf)
    if mask.sum() < 20: continue
    sl, ic_, r, p, se = stats.linregress(X_a[mask], yf[mask])
    factor_market_betas[f] = {"beta": sl, "t": sl/se, "p": p, "r2": r**2}
    sig = "*" if p < 0.05 else " "
    print(f"  {f:<28}  β={sl:+.4f}  t={sl/se:+.2f}  p={p:.3f}  R²={r**2:.3f} {sig}")

# ══════════════════════════════════════════════════════════════════════════════
# 实验 B：时间趋势检验（控制市场后）
# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 实验B：IC ~ bench_ret + time_index 多元回归 ===")

from numpy.linalg import lstsq

# 多元回归: IC = a + b1*bench_ret + b2*time_index
X_b = np.column_stack([
    np.ones(len(df_full)),
    df_full["bench_ret"].values,
    df_full["time_index"].values,
])
Y_b = df_full["composite_ic"].values
coef_b, _, _, _ = lstsq(X_b, Y_b, rcond=None)

# 计算统计量
Y_hat_b = X_b @ coef_b
resid_b = Y_b - Y_hat_b
n, k = len(Y_b), 3
s2 = np.sum(resid_b**2) / (n - k)
cov_coef = s2 * np.linalg.inv(X_b.T @ X_b)
se_b = np.sqrt(np.diag(cov_coef))
t_b  = coef_b / se_b
p_b  = 2 * (1 - stats.t.cdf(np.abs(t_b), df=n-k))
ss_tot = np.sum((Y_b - Y_b.mean())**2)
r2_b = 1 - np.sum(resid_b**2) / ss_tot

print(f"  截距 α:         {coef_b[0]:+.4f}  t={t_b[0]:+.2f}  p={p_b[0]:.4f}")
print(f"  β₁ (市场):      {coef_b[1]:+.4f}  t={t_b[1]:+.2f}  p={p_b[1]:.4f}")
print(f"  β₂ (时间趋势): {coef_b[2]:+.6f}  t={t_b[2]:+.2f}  p={p_b[2]:.4f}")
print(f"  R²:             {r2_b:.4f}")

time_decay_per_year = coef_b[2] * 12  # 每年 IC 变化
print(f"  时间趋势含义: 每年 IC 变化 {time_decay_per_year:+.4f}")

if p_b[2] < 0.05 and coef_b[2] < 0:
    print("  => 控制市场后时间趋势显著为负 → 支持 H2（因子衰减）")
else:
    print("  => 控制市场后时间趋势不显著 → 不支持 H2，主要为牛市效应")

# 各因子时间趋势检验
print("\n  各因子时间趋势（控制市场后）：")
factor_time_trends = {}
for f in ic.columns:
    yf = ic[f].reindex(df_full.index).values
    mask = ~np.isnan(yf)
    if mask.sum() < 30: continue
    X_f = np.column_stack([np.ones(mask.sum()), X_a[mask], df_full["time_index"].values[mask]])
    c, _, _, _ = lstsq(X_f, yf[mask], rcond=None)
    Y_hat_f = X_f @ c
    resid_f = yf[mask] - Y_hat_f
    nf, kf = mask.sum(), 3
    s2f = np.sum(resid_f**2) / (nf - kf)
    cov_f = s2f * np.linalg.inv(X_f.T @ X_f)
    se_f = np.sqrt(np.diag(cov_f))
    t_f = c / se_f
    p_f = 2 * (1 - stats.t.cdf(np.abs(t_f), df=nf-kf))
    factor_time_trends[f] = {"beta_time": c[2], "t_time": t_f[2], "p_time": p_f[2]}
    sig = "** DECAY **" if p_f[2] < 0.05 and c[2] < 0 else (
          "** GROWTH **" if p_f[2] < 0.05 and c[2] > 0 else "")
    print(f"  {f:<28}  β_time={c[2]:+.6f}  t={t_f[2]:+.2f}  p={p_f[2]:.3f} {sig}")

# ══════════════════════════════════════════════════════════════════════════════
# 实验 C：市场环境分层回测对比
# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 实验C：市场环境分层对比 ===")

# 分层：按基准月收益率
BEAR_THRESH  = -0.02   # < -2%
BULL_THRESH  =  0.02   # > +2%

def classify_regime(ret):
    if ret < BEAR_THRESH:  return "熊市月"
    elif ret > BULL_THRESH: return "牛市月"
    else:                   return "震荡月"

# ── IC 分层（全期）─────────────────────────────────────────────────────────
df_full["regime"] = df_full["bench_ret"].apply(classify_regime)
df_full["period"] = df_full["year"].apply(
    lambda y: "训练期(2014-2020)" if y <= 2020 else ("验证期(2021-2022)" if y <= 2022 else "测试期(2023-2025)")
)

# IC 按环境×时期统计
ic_by_regime_period = df_full.groupby(["regime", "period"])["composite_ic"].agg(["mean","std","count"])
print("\n  IC 均值（按市场环境×时期）：")
for (regime, period), row in ic_by_regime_period.iterrows():
    t_stat = row["mean"] / (row["std"] / np.sqrt(row["count"])) if row["count"] > 1 else 0
    print(f"  {regime:<8} | {period:<20} | "
          f"IC均值={row['mean']:+.4f}  n={int(row['count']):3d}  t={t_stat:+.2f}")

# ── 超额收益分层（仅 2021-2025）────────────────────────────────────────────
df_obs["regime"] = df_obs["bench_ret"].apply(classify_regime)
df_obs["period"] = df_obs["year"].apply(
    lambda y: "验证期(2021-2022)" if y <= 2022 else "测试期(2023-2025)"
)

exc_by_regime_period = df_obs.groupby(["regime", "period"])["excess_ret"].agg(["mean","std","count"])
print("\n  超额收益均值（按市场环境×时期）：")
for (regime, period), row in exc_by_regime_period.iterrows():
    t_stat = row["mean"] / (row["std"] / np.sqrt(row["count"])) if row["count"] > 1 else 0
    print(f"  {regime:<8} | {period:<20} | "
          f"超额月均={row['mean']*100:+.3f}%  n={int(row['count']):3d}  t={t_stat:+.2f}")

# ── 分层差值检验（验证期 vs 测试期，同一环境）──────────────────────────────
print("\n  分层差值检验（测试期 - 验证期）：")
regimes = ["熊市月", "震荡月", "牛市月"]
decay_by_regime = {}
for regime in regimes:
    tv_vals = df_obs[(df_obs["regime"]==regime) & (df_obs["period"]=="验证期(2021-2022)")]["excess_ret"].values
    ts_vals = df_obs[(df_obs["regime"]==regime) & (df_obs["period"]=="测试期(2023-2025)")]["excess_ret"].values
    if len(tv_vals) < 2 or len(ts_vals) < 2:
        print(f"  {regime:<8}: 样本量不足（验证期n={len(tv_vals)}, 测试期n={len(ts_vals)}）")
        decay_by_regime[regime] = None
        continue
    diff = ts_vals.mean() - tv_vals.mean()
    t_stat, p_val = stats.ttest_ind(ts_vals, tv_vals, equal_var=False)
    decay_by_regime[regime] = {"diff": diff, "t": t_stat, "p": p_val,
                                "tv_mean": tv_vals.mean(), "ts_mean": ts_vals.mean(),
                                "tv_n": len(tv_vals), "ts_n": len(ts_vals)}
    sig = "**" if p_val < 0.05 else ("*" if p_val < 0.10 else "")
    print(f"  {regime:<8}: 验证={tv_vals.mean()*100:+.3f}%(n={len(tv_vals)})  "
          f"测试={ts_vals.mean()*100:+.3f}%(n={len(ts_vals)})  "
          f"差值={diff*100:+.3f}%  t={t_stat:.2f}  p={p_val:.3f} {sig}")

# ══════════════════════════════════════════════════════════════════════════════
# 综合判定
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("综合判定")
print("="*60)

h1_evidence = []
h2_evidence = []

# A 实验证据
if p_a < 0.05 and slope_a < 0:
    h1_evidence.append(f"实验A: IC-市场负相关 β={slope_a:.3f}, p={p_a:.3f}")
else:
    h2_evidence.append(f"实验A: IC与市场无显著相关(p={p_a:.3f}), 反驳H1")

# B 实验证据
if p_b[2] < 0.05 and coef_b[2] < 0:
    h2_evidence.append(f"实验B: 时间趋势显著 β₂={coef_b[2]:.6f}, p={p_b[2]:.3f}, 每年IC降{time_decay_per_year:.4f}")
else:
    h1_evidence.append(f"实验B: 控制市场后时间趋势不显著(p={p_b[2]:.3f}), 支持H1")

# C 实验证据
all_regimes_decay = all(
    v is not None and v["diff"] < 0
    for v in decay_by_regime.values()
)
bear_not_significant = (
    decay_by_regime.get("熊市月") is not None and
    decay_by_regime["熊市月"]["p"] > 0.10
)
bull_significant = (
    decay_by_regime.get("牛市月") is not None and
    decay_by_regime["牛市月"]["p"] < 0.10 and
    decay_by_regime["牛市月"]["diff"] < 0
)

if all_regimes_decay:
    h2_evidence.append("实验C: 各市场环境下均有超额衰减 → 不仅是牛市效应")
elif bull_significant and bear_not_significant:
    h1_evidence.append("实验C: 衰减主要集中在牛市月 → 支持牛市效应H1")
else:
    h1_evidence.append("实验C: 分层差异样本量不足，结论待定")

print(f"\n支持 H1（牛市效应）的证据（{len(h1_evidence)}条）：")
for e in h1_evidence: print(f"  + {e}")
print(f"\n支持 H2（因子衰减）的证据（{len(h2_evidence)}条）：")
for e in h2_evidence: print(f"  + {e}")

if len(h2_evidence) >= len(h1_evidence):
    verdict = "混合效应（H1+H2 均有贡献）" if h1_evidence else "因子衰减（H2 为主）"
else:
    verdict = "牛市效应（H1 为主）"
print(f"\n最终判定：{verdict}")

# ══════════════════════════════════════════════════════════════════════════════
# 可视化（4 × 2 面板）
# ══════════════════════════════════════════════════════════════════════════════
print("\nGenerating visualization...")
C_VALID = "#fff3cd"
C_TEST  = "#e8f5e9"
C_TRAIN = "#e8f4f8"

fig = plt.figure(figsize=(20, 18))
gs = gridspec.GridSpec(4, 2, figure=fig, hspace=0.5, wspace=0.35)
fig.suptitle("超额收益衰减归因分析：牛市效应 vs 因子时间衰减",
             fontsize=15, fontweight="bold")

# ── Panel 1: 复合 IC 时间序列 + 市场环境色带 ─────────────────────────────
ax1 = fig.add_subplot(gs[0, :])
roll_ic = ic_composite.rolling(12).mean()
ax1.plot(df_full.index, df_full["composite_ic"], color="steelblue", lw=0.7, alpha=0.5, label="月度复合IC")
ax1.plot(roll_ic.index, roll_ic.values, color="navy", lw=1.8, label="12月滚动均值")
ax1.axhline(0, color="black", lw=0.8)

# 背景色带标注验证/测试期
ax1.axvspan(pd.Timestamp("2021-01-01"), pd.Timestamp("2022-12-31"), color=C_VALID, alpha=0.4)
ax1.axvspan(pd.Timestamp("2023-01-01"), pd.Timestamp("2025-12-31"), color=C_TEST, alpha=0.4)

# 市场牛市月标记
bull_dates = df_full[df_full["regime"]=="牛市月"].index
bear_dates = df_full[df_full["regime"]=="熊市月"].index
ax1.scatter(bull_dates, df_full.loc[bull_dates, "composite_ic"],
            color="tomato", s=18, zorder=5, alpha=0.7, label="牛市月(基准>+2%)")
ax1.scatter(bear_dates, df_full.loc[bear_dates, "composite_ic"],
            color="green", s=18, zorder=5, alpha=0.7, label="熊市月(基准<-2%)")

ax1.set_ylabel("复合 IC（18因子等权均值）", fontsize=11)
ax1.set_title("(A) 全期复合 IC 时间序列及市场环境标注", fontsize=11, loc="left")
ax1.legend(fontsize=9, ncol=4)
ax1.grid(axis="y", alpha=0.4)
ax1.text(pd.Timestamp("2021-07-01"), ax1.get_ylim()[1]*0.9, "验证期", fontsize=9, color="goldenrod", fontweight="bold")
ax1.text(pd.Timestamp("2024-01-01"), ax1.get_ylim()[1]*0.9, "测试期", fontsize=9, color="seagreen", fontweight="bold")

# ── Panel 2: IC ~ 市场涨跌 散点（实验A）──────────────────────────────────
ax2 = fig.add_subplot(gs[1, 0])
period_colors = {"训练期(2014-2020)": "steelblue", "验证期(2021-2022)": "goldenrod", "测试期(2023-2025)": "seagreen"}
for period, grp in df_full.groupby("period"):
    ax2.scatter(grp["bench_ret"]*100, grp["composite_ic"],
                color=period_colors[period], alpha=0.6, s=20, label=period)
# 回归线
x_line = np.linspace(df_full["bench_ret"].min(), df_full["bench_ret"].max(), 100)
y_line = intercept_a + slope_a * x_line
ax2.plot(x_line*100, y_line, color="red", lw=1.5, linestyle="--",
         label=f"回归线 β={slope_a:.3f} (p={p_a:.3f})")
ax2.axhline(0, color="black", lw=0.5); ax2.axvline(0, color="black", lw=0.5)
ax2.set_xlabel("月度基准收益率 (%)", fontsize=10)
ax2.set_ylabel("复合 IC", fontsize=10)
ax2.set_title(f"(B) 实验A：IC ~ 市场涨跌\nβ={slope_a:.3f}  t={slope_a/se_a:.2f}  R²={r_a**2:.3f}  p={p_a:.3f}",
              fontsize=10)
ax2.legend(fontsize=8)
ax2.grid(alpha=0.3)

# ── Panel 3: IC 时间趋势（实验B）- 年度IC均值 + 趋势线 ─────────────────
ax3 = fig.add_subplot(gs[1, 1])
ic_annual_mean = df_full.groupby("year")["composite_ic"].mean()
bar_colors = ["goldenrod" if y in [2021,2022] else ("seagreen" if y>=2023 else "steelblue")
              for y in ic_annual_mean.index]
ax3.bar(ic_annual_mean.index, ic_annual_mean.values, color=bar_colors, alpha=0.75)

# 控制市场后的时间趋势（残差趋势线）
resid_after_market = Y_b - (coef_b[0] + coef_b[1]*df_full["bench_ret"].values)
residual_annual = pd.Series(resid_after_market, index=df_full.index).groupby(df_full["year"]).mean()
ax3.plot(residual_annual.index, residual_annual.values, "o--",
         color="navy", lw=1.5, label=f"控制市场后残差IC\n时间趋势t={t_b[2]:.2f}, p={p_b[2]:.3f}")
ax3.axhline(0, color="black", lw=0.8)
ax3.set_xlabel("年份", fontsize=10)
ax3.set_ylabel("复合 IC 年均值", fontsize=10)
ax3.set_title(f"(C) 实验B：年度IC均值与时间趋势\n（控制市场后，时间趋势p={p_b[2]:.3f}）",
              fontsize=10)
ax3.legend(fontsize=8)
ax3.grid(axis="y", alpha=0.4)

# ── Panel 4: 各因子时间趋势系数（实验B 扩展）──────────────────────────────
ax4 = fig.add_subplot(gs[2, 0])
ft_sorted = sorted(factor_time_trends.items(), key=lambda x: x[1]["beta_time"])
f_names = [x[0] for x in ft_sorted]
f_betas = [x[1]["beta_time"]*12 for x in ft_sorted]  # 年化
f_sigs  = [x[1]["p_time"] < 0.05 for x in ft_sorted]
colors4 = ["tomato" if (b < 0 and s) else ("steelblue" if (b > 0 and s) else "lightgray")
           for b, s in zip(f_betas, f_sigs)]
y4 = np.arange(len(f_names))
ax4.barh(y4, f_betas, color=colors4, alpha=0.8)
ax4.axvline(0, color="black", lw=0.8)
ax4.set_yticks(y4); ax4.set_yticklabels(f_names, fontsize=8.5)
ax4.set_xlabel("每年 IC_IR 变化（控制市场后）", fontsize=10)
ax4.set_title("(D) 各因子时间衰减系数（红=显著衰减, 蓝=显著增强）", fontsize=10)
from matplotlib.patches import Patch
ax4.legend(handles=[Patch(facecolor="tomato",label="显著衰减(p<0.05)"),
                    Patch(facecolor="steelblue",label="显著增强"),
                    Patch(facecolor="lightgray",label="不显著")], fontsize=8)
ax4.grid(axis="x", alpha=0.4)

# ── Panel 5: 市场环境分层对比（实验C）─────────────────────────────────────
ax5 = fig.add_subplot(gs[2, 1])
regime_order = ["熊市月", "震荡月", "牛市月"]
x5 = np.arange(len(regime_order))
w5 = 0.35

tv_means, ts_means, tv_ns, ts_ns = [], [], [], []
for r in regime_order:
    tv_vals_ = df_obs[(df_obs["regime"]==r) & (df_obs["period"]=="验证期(2021-2022)")]["excess_ret"].values
    ts_vals_ = df_obs[(df_obs["regime"]==r) & (df_obs["period"]=="测试期(2023-2025)")]["excess_ret"].values
    tv_means.append(tv_vals_.mean() * 100 if len(tv_vals_) > 0 else 0)
    ts_means.append(ts_vals_.mean() * 100 if len(ts_vals_) > 0 else 0)
    tv_ns.append(len(tv_vals_))
    ts_ns.append(len(ts_vals_))

bars1 = ax5.bar(x5 - w5/2, tv_means, w5, label="验证期(2021-2022)", color="goldenrod", alpha=0.8)
bars2 = ax5.bar(x5 + w5/2, ts_means, w5, label="测试期(2023-2025)", color="seagreen", alpha=0.8)

for i, (tv_m, ts_m, tv_n, ts_n) in enumerate(zip(tv_means, ts_means, tv_ns, ts_ns)):
    ax5.text(i - w5/2, tv_m + (0.02 if tv_m >= 0 else -0.08), f"n={tv_n}", ha="center", fontsize=7)
    ax5.text(i + w5/2, ts_m + (0.02 if ts_m >= 0 else -0.08), f"n={ts_n}", ha="center", fontsize=7)
    # 显著性标注
    info = decay_by_regime.get(regime_order[i])
    if info and info["p"] < 0.10:
        ax5.text(i, max(tv_m, ts_m) + 0.15, f"p={info['p']:.2f}*", ha="center", fontsize=8, color="red")

ax5.axhline(0, color="black", lw=0.8)
ax5.set_xticks(x5); ax5.set_xticklabels(regime_order, fontsize=10)
ax5.set_ylabel("月均超额收益 (%)", fontsize=10)
ax5.set_title("(E) 实验C：同等市场环境下\n验证期 vs 测试期月均超额收益", fontsize=10)
ax5.legend(fontsize=9)
ax5.grid(axis="y", alpha=0.4)

# ── Panel 6: 综合判定结论框 ────────────────────────────────────────────────
ax6 = fig.add_subplot(gs[3, :])
ax6.axis("off")

# 左：判定矩阵表
table_data = [
    ["实验", "检验内容", "结论", "支持假说"],
    ["A", f"IC ~ 市场涨跌  β={slope_a:.3f}  p={p_a:.3f}",
     "IC与市场负相关" if (p_a<0.05 and slope_a<0) else "无显著相关",
     "H1" if (p_a<0.05 and slope_a<0) else "反驳H1"],
    ["B", f"时间趋势（控制市场后）  p={p_b[2]:.3f}",
     "时间衰减显著" if (p_b[2]<0.05 and coef_b[2]<0) else "时间趋势不显著",
     "H2" if (p_b[2]<0.05 and coef_b[2]<0) else "反驳H2"],
    ["C", "同环境验证期 vs 测试期差值",
     "各环境均有衰减" if all_regimes_decay else ("主要在牛市月" if bull_significant else "差异不显著"),
     "H2" if all_regimes_decay else ("H1" if bull_significant else "待定")],
]

table = ax6.table(
    cellText=table_data[1:],
    colLabels=table_data[0],
    cellLoc="center", loc="center",
    bbox=[0.0, 0.0, 0.72, 1.0]
)
table.auto_set_font_size(False); table.set_fontsize(10)
for (r, c), cell in table.get_celld().items():
    if r == 0:
        cell.set_facecolor("#2c3e50"); cell.set_text_props(color="white", fontweight="bold")
        cell.set_height(0.28)
    else:
        cell.set_height(0.24)
        if r % 2 == 0: cell.set_facecolor("#f8f9fa")
        if c == 3:
            txt = cell.get_text().get_text()
            if "H1" in txt and "反" not in txt: cell.set_facecolor("#fff3cd")
            elif "H2" in txt and "反" not in txt: cell.set_facecolor("#fce4e4")

# 右：最终判定
verdict_text = f"""
最终判定：{verdict}

H1（牛市效应）证据：
{"  无" if not h1_evidence else chr(10).join("  · " + e for e in h1_evidence)}

H2（因子衰减）证据：
{"  无" if not h2_evidence else chr(10).join("  · " + e for e in h2_evidence)}

建议：
{"  当前超额衰减主要是结构性的，" if len(h2_evidence) > len(h1_evidence) else "  当前超额衰减主要由市场环境驱动，"}
{"  因子池需要更新补充。" if len(h2_evidence) > len(h1_evidence) else "  等待熊市环境可验证策略有效性。"}
"""

ax6.text(0.74, 0.95, verdict_text.strip(), transform=ax6.transAxes,
         fontsize=9.5, va="top", ha="left",
         bbox=dict(boxstyle="round,pad=0.5", facecolor="#f0f8ff", alpha=0.9,
                   edgecolor="navy", linewidth=2))

fig.savefig(OUT_DIR / "09_alpha_decay_attribution.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print("  Saved: 09_alpha_decay_attribution.png")
print("Done.")
