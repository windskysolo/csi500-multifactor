"""
current work/charts/plot_strategy_comparison.py
================================================
三种信号合成策略全周期对比图（2012-2022）：
  · IC_IR rolling-24（18因子）
  · Ridge v2 expanding + λ=0.005（18因子）
  · Ridge v2 rolling-48m（18因子）

三个子图：累计超额、逐月净超额、超额回撤
输出：current work/charts/strategy_comparison.png
"""

import sys
import logging
from pathlib import Path

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch
from matplotlib import font_manager

# Windows 中文字体支持（优先使用微软雅黑，备选黑体）
_CN_FONTS = ["Microsoft YaHei", "SimHei", "SimSun", "KaiTi", "FangSong"]
for _fn in _CN_FONTS:
    if any(_fn.lower() in f.name.lower() for f in font_manager.fontManager.ttflist):
        matplotlib.rcParams["font.family"] = [_fn, "DejaVu Sans"]
        break
matplotlib.rcParams["axes.unicode_minus"] = False

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from src.backtest.engine import BacktestConfig, run_backtest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

OUT_DIR = Path(__file__).parent
FULL_START = pd.Timestamp("2014-01-01")   # rolling-48m 冷启动需要48个月，2016前数据质量低
FULL_END   = cfg.VALID_END

WEIGHTS = {
    "IC_IR rolling-24":     _ROOT / "experiments/v1.2/results/ic_ir/ic_ir_portfolio_weights_optimized.parquet",
    "Ridge v2 expanding":   _ROOT / "experiments/v1.2/results/ridge_lam_0050/weights_optimized.parquet",
    "Ridge v2 rolling-48m": _ROOT / "experiments/v1.2/results/ridge_rolling_48m/weights_optimized.parquet",
}

COLORS = {
    "IC_IR rolling-24":     "#2196F3",   # 蓝
    "Ridge v2 expanding":   "#FF9800",   # 橙
    "Ridge v2 rolling-48m": "#4CAF50",   # 绿
}

VALID_START = cfg.VALID_START


# ---------------------------------------------------------------------------
# 数据加载与回测
# ---------------------------------------------------------------------------

def load_and_backtest(weights_path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    weights = pd.read_parquet(weights_path)
    bt_cfg = BacktestConfig(initial_value=1.0)
    result = run_backtest(weights, start, end, bt_cfg)
    nav_df = pd.DataFrame({
        "strategy":  result.nav,
        "benchmark": result.benchmark_nav,
        "excess":    result.excess_nav,
    })
    return nav_df


def compute_monthly_excess(nav_df: pd.DataFrame) -> pd.Series:
    """月末超额 NAV → 月度超额收益率（相对基准）"""
    monthly = nav_df[["strategy", "benchmark"]].resample("ME").last()
    strat_ret   = monthly["strategy"].pct_change()
    bench_ret   = monthly["benchmark"].pct_change()
    monthly_exc = (1 + strat_ret) / (1 + bench_ret) - 1
    return monthly_exc.dropna()


def compute_excess_drawdown(nav_df: pd.DataFrame) -> pd.Series:
    """超额 NAV 从历史高点的回撤序列"""
    excess_nav = nav_df["strategy"] / nav_df["benchmark"]
    peak = excess_nav.cummax()
    drawdown = (excess_nav - peak) / peak
    return drawdown


# ---------------------------------------------------------------------------
# 绘图
# ---------------------------------------------------------------------------

def plot_all(navs: dict[str, pd.DataFrame]) -> None:
    fig, axes = plt.subplots(4, 1, figsize=(14, 17), dpi=150,
                              gridspec_kw={"height_ratios": [2.0, 2.2, 1.5, 1.5]})
    fig.suptitle("三种信号合成策略对比（2014-2022，验证期 2021-2022）",
                 fontsize=14, fontweight="bold", y=0.99)

    train_shade_kw = dict(alpha=0.06, color="gray", label="_train_bg")
    valid_shade_kw = dict(alpha=0.10, color="steelblue", label="_valid_bg")

    def add_period_shading(ax):
        ax.axvspan(FULL_START, VALID_START, **train_shade_kw)
        ax.axvspan(VALID_START, FULL_END + pd.offsets.MonthEnd(1), **valid_shade_kw)
        ax.axvline(VALID_START, color="steelblue", linewidth=1.0, linestyle="--", alpha=0.6)

    # ── 0. 原始 NAV 绝对走势 ────────────────────────────────────────────────
    ax0 = axes[0]
    # 中证500全收益基准（取任意一个策略的 benchmark 序列）
    first_nav_df = next(iter(navs.values()))
    bench = first_nav_df["benchmark"]
    bench_norm = bench / bench.iloc[0]
    ax0.plot(bench_norm.index, bench_norm.values,
             color="black", linewidth=2.0, linestyle="--", label="中证500全收益指数", zorder=5)
    for name, nav_df in navs.items():
        strat = nav_df["strategy"]
        strat_norm = strat / strat.iloc[0]
        ax0.plot(strat_norm.index, strat_norm.values,
                 color=COLORS[name], linewidth=1.8, label=name)
    add_period_shading(ax0)
    ax0.axhline(1.0, color="black", linewidth=0.6, linestyle="-", alpha=0.3)
    ax0.set_ylabel("净值（起点=1）", fontsize=10)
    ax0.set_title("① 原始净值走势（中证500全收益 + 三策略）", fontsize=11, loc="left")
    ax0.legend(loc="upper left", fontsize=9)
    ax0.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.2f}"))
    ax0.grid(axis="y", linewidth=0.4, alpha=0.5)

    # ── 1. 累计超额 NAV ─────────────────────────────────────────────────────
    ax1 = axes[1]
    for name, nav_df in navs.items():
        excess_nav = nav_df["strategy"] / nav_df["benchmark"]
        # 归一化到 FULL_START 附近第一个可用点
        first_valid = excess_nav.first_valid_index()
        excess_nav = excess_nav / excess_nav.loc[first_valid]
        ax1.plot(excess_nav.index, (excess_nav - 1) * 100,
                 color=COLORS[name], linewidth=1.8, label=name)
    add_period_shading(ax1)
    ax1.axhline(0, color="black", linewidth=0.8, linestyle="-", alpha=0.4)
    ax1.set_ylabel("累计超额收益 (%)", fontsize=10)
    ax1.set_title("② 累计超额收益（超额 NAV 相对起点）", fontsize=11, loc="left")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.1f}%"))
    ax1.grid(axis="y", linewidth=0.4, alpha=0.5)

    # 期间标注（transform=ax 坐标系，y=0.02 = 底部 2%）
    ax1.text(FULL_START + pd.Timedelta(days=180), 0.02,
             "Training", fontsize=8, color="gray", va="bottom",
             transform=ax1.get_xaxis_transform())
    ax1.text(VALID_START + pd.Timedelta(days=30), 0.02,
             "Validation", fontsize=8, color="steelblue", va="bottom",
             transform=ax1.get_xaxis_transform())

    # ── 2. 逐月净超额 ───────────────────────────────────────────────────────
    ax2 = axes[2]
    bar_width = 18   # days
    offsets = {"IC_IR rolling-24": -bar_width, "Ridge v2 expanding": 0, "Ridge v2 rolling-48m": bar_width}

    for name, nav_df in navs.items():
        monthly_exc = compute_monthly_excess(nav_df)
        dates_offset = [d + pd.Timedelta(days=offsets[name]) for d in monthly_exc.index]
        colors_bar = [COLORS[name] if v >= 0 else COLORS[name] for v in monthly_exc.values]
        ax2.bar(dates_offset, monthly_exc.values * 100,
                width=bar_width * 0.85,
                color=COLORS[name], alpha=0.75, label=name)

    add_period_shading(ax2)
    ax2.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    ax2.set_ylabel("月度净超额 (%)", fontsize=10)
    ax2.set_title("③ 逐月净超额收益", fontsize=11, loc="left")
    ax2.legend(loc="upper left", fontsize=9)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.1f}%"))
    ax2.grid(axis="y", linewidth=0.4, alpha=0.5)

    # ── 3. 超额回撤 ─────────────────────────────────────────────────────────
    ax3 = axes[3]
    for name, nav_df in navs.items():
        dd = compute_excess_drawdown(nav_df)
        ax3.fill_between(dd.index, dd.values * 100, 0,
                         color=COLORS[name], alpha=0.30, label=name)
        ax3.plot(dd.index, dd.values * 100,
                 color=COLORS[name], linewidth=1.2)

    add_period_shading(ax3)
    ax3.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    ax3.axhline(-10.0, color="red", linewidth=1.0, linestyle="--", alpha=0.7, label="目标上限 -10%")
    ax3.set_ylabel("超额回撤 (%)", fontsize=10)
    ax3.set_title("④ 超额回撤（超额 NAV 从历史高点）", fontsize=11, loc="left")
    ax3.legend(loc="lower left", fontsize=9)
    ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.1f}%"))
    ax3.grid(axis="y", linewidth=0.4, alpha=0.5)

    # ── 共同 x 轴格式 ────────────────────────────────────────────────────────
    for ax in axes:
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.xaxis.set_minor_locator(mdates.MonthLocator(bymonth=[4, 7, 10]))
        ax.set_xlim(FULL_START, FULL_END + pd.offsets.MonthEnd(1))
        ax.tick_params(axis="x", labelsize=9)
        ax.tick_params(axis="y", labelsize=9)

    # 图例：添加期间说明补丁
    legend_patches = [
        Patch(color="gray",      alpha=0.15, label="训练期（2014-2020）"),
        Patch(color="steelblue", alpha=0.20, label="验证期（2021-2022）"),
    ]
    fig.legend(handles=legend_patches, loc="lower center", ncol=2,
               fontsize=9, bbox_to_anchor=(0.5, 0.01))

    plt.tight_layout(rect=[0, 0.03, 1, 0.97])
    out_path = OUT_DIR / "strategy_comparison.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    log.info("图表已保存: %s", out_path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== 加载权重并运行全周期回测 ===")
    navs: dict[str, pd.DataFrame] = {}
    for name, w_path in WEIGHTS.items():
        log.info("  %s ...", name)
        navs[name] = load_and_backtest(w_path, FULL_START, FULL_END)
        log.info("    NAV: %s ~ %s",
                 navs[name].index[0].date(), navs[name].index[-1].date())

    log.info("=== 生成对比图 ===")
    plot_all(navs)
    log.info("完成")


if __name__ == "__main__":
    main()
