"""
Generate validation-period backtest charts from current parquet outputs.

Inputs:
    data/processed/backtest_nav.parquet

Outputs:
    reports/backtest/validation_nav_comparison.png
    reports/backtest/validation_cumulative_excess.png
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
NAV_PATH = ROOT / "data" / "processed" / "backtest_nav.parquet"
OUT_DIR = ROOT / "reports" / "backtest"


def _style_axis(ax: plt.Axes) -> None:
    ax.grid(True, which="major", axis="both", alpha=0.25, linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def main() -> None:
    if not NAV_PATH.exists():
        raise FileNotFoundError(f"Missing backtest NAV file: {NAV_PATH}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    nav = pd.read_parquet(NAV_PATH).sort_index()
    required = {"strategy_v1", "strategy_v2", "benchmark"}
    missing = required - set(nav.columns)
    if missing:
        raise ValueError(f"Missing required NAV columns: {sorted(missing)}")

    labels = {
        "strategy_v1": "V1 equal-weight signal",
        "strategy_v2": "V2 optimized portfolio",
        "benchmark": "CSI500 total-return benchmark",
    }
    colors = {
        "strategy_v1": "#8c8c8c",
        "strategy_v2": "#1f77b4",
        "benchmark": "#d62728",
    }

    # Chart 1: strategy vs benchmark NAV.
    fig, ax = plt.subplots(figsize=(11, 6))
    for col in ["strategy_v1", "strategy_v2", "benchmark"]:
        ax.plot(nav.index, nav[col], label=labels[col], color=colors[col], linewidth=2.0)
    ax.set_title("Validation NAV Comparison (2021-2022)")
    ax.set_ylabel("NAV, start = 1.0")
    ax.legend(loc="best", frameon=False)
    _style_axis(ax)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "validation_nav_comparison.png", dpi=160)
    plt.close(fig)

    # Chart 2: cumulative excess return vs benchmark.
    excess = pd.DataFrame(index=nav.index)
    excess["strategy_v1_excess"] = nav["strategy_v1"] / nav["benchmark"] - 1.0
    excess["strategy_v2_excess"] = nav["strategy_v2"] / nav["benchmark"] - 1.0

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(
        excess.index,
        excess["strategy_v1_excess"],
        label="V1 excess vs benchmark",
        color=colors["strategy_v1"],
        linewidth=2.0,
    )
    ax.plot(
        excess.index,
        excess["strategy_v2_excess"],
        label="V2 excess vs benchmark",
        color=colors["strategy_v2"],
        linewidth=2.2,
    )
    ax.axhline(0, color="#333333", linewidth=1.0, alpha=0.7)
    ax.set_title("Validation Cumulative Excess Return (2021-2022)")
    ax.set_ylabel("Cumulative excess return")
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    ax.legend(loc="best", frameon=False)
    _style_axis(ax)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "validation_cumulative_excess.png", dpi=160)
    plt.close(fig)

    print(f"Wrote {OUT_DIR / 'validation_nav_comparison.png'}")
    print(f"Wrote {OUT_DIR / 'validation_cumulative_excess.png'}")


if __name__ == "__main__":
    main()
