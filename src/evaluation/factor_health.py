"""
src/evaluation/factor_health.py — 因子健康监控模块
==========================================================

核心功能：
  compute_rolling_ic_ir    计算因子在 as_of 前 N 期内的滚动 IC_IR
  compute_health_status    按方向调整后的 24m IC_IR 判断健康状态
  compute_trend_flag       判断趋势预警（DETERIORATING / INSUFFICIENT_HISTORY / OK）
  FactorHealthMonitor      健康监控器（构建快照、生成报告）
  plot_health_panel        健康面板图（按因子数自动计算布局；matplotlib 仅内部导入）

健康状态规则（使用方向调整后的 24 期 IC_IR）：
  adjusted_ic_ir_24m = ic_ir_24m * factor_direction
  STABLE  : >= 0.30
  WEAK    : 0.20 <= < 0.30
  WARN    : 0 <= < 0.20
  REVERSE : < 0
  UNKNOWN : 有效样本不足（< MIN_VALID_OBS）

趋势预警：
  DETERIORATING        : adj_ic_ir_12m < adj_ic_ir_36m - 0.15
  INSUFFICIENT_HISTORY : 12m 或 36m 窗口样本不足
  OK                   : 无预警

重要约束：
  - 本模块不依赖 src/pipeline/、src/signal/、src/backtest/ 或测试集目录
  - matplotlib 仅在 plot_health_panel 函数内部导入，不作为顶层依赖
"""

import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
STATUS_STABLE   = "STABLE"
STATUS_WEAK     = "WEAK"
STATUS_WARN     = "WARN"
STATUS_REVERSE  = "REVERSE"
STATUS_UNKNOWN  = "UNKNOWN"

TREND_OK            = "OK"
TREND_DETERIORATING = "DETERIORATING"
TREND_INSUFFICIENT  = "INSUFFICIENT_HISTORY"

IC_IR_STABLE_THRESHOLD = 0.30
IC_IR_WEAK_THRESHOLD   = 0.20
DETERIORATING_GAP      = 0.15  # adj_ic_ir_12m < adj_ic_ir_36m - 此值 → DETERIORATING
MIN_VALID_OBS          = 6     # 滚动窗口内最少有效观测数


# ---------------------------------------------------------------------------
# 私有辅助
# ---------------------------------------------------------------------------

def _round_or_nan(val: float, decimals: int = 4) -> float:
    """四舍五入；NaN 原样返回。"""
    if pd.isna(val):
        return np.nan
    return round(float(val), decimals)


def _suggest_action(status: str, trend: str) -> str:
    """根据健康状态和趋势生成建议操作字符串。"""
    _action_map = {
        STATUS_STABLE:  "HOLD",
        STATUS_WEAK:    "MONITOR",
        STATUS_WARN:    "WATCH_CLOSELY",
        STATUS_REVERSE: "REVIEW_REMOVAL",
        STATUS_UNKNOWN: "INSUFFICIENT_DATA",
    }
    action = _action_map.get(status, "UNKNOWN")
    if trend == TREND_DETERIORATING:
        action = f"{action}|DETERIORATING"
    return action


# ---------------------------------------------------------------------------
# 核心计算函数
# ---------------------------------------------------------------------------

def compute_rolling_ic_ir(
    ic_series: pd.Series,
    window: int,
    as_of: pd.Timestamp,
    min_obs: int = MIN_VALID_OBS,
) -> float:
    """
    计算因子在 as_of 日期前 window 期内的滚动 IC_IR。

    Args:
        ic_series: 因子月度 IC 时序（index=rebalance_date），NaN 行视为无效期
        window:    滚动窗口期数（如 12 / 24 / 36），取 <= as_of 中最后 window 个有效期
        as_of:     评估截止日期（含）
        min_obs:   有效观测下限；不足时返回 NaN
    Returns:
        IC_IR（float）；有效样本不足或 IC 标准差为零时返回 NaN
    时间对齐假设：
        只使用 index <= as_of 的观测，取其中最后 window 个有效（非 NaN）期。
    数据依赖：
        仅依赖传入的 ic_series，不读取磁盘。
    """
    valid = ic_series.dropna()
    valid = valid[valid.index <= as_of]
    if len(valid) > window:
        valid = valid.iloc[-window:]
    if len(valid) < min_obs:
        return np.nan
    mean = float(valid.mean())
    std  = float(valid.std(ddof=1))
    if std < 1e-12:
        return np.nan
    return mean / std


def compute_health_status(adjusted_ic_ir_24m: float) -> str:
    """
    根据方向调整后的 24 期 IC_IR 判断健康状态。

    Args:
        adjusted_ic_ir_24m: ic_ir_24m * factor_direction；NaN 时返回 UNKNOWN
    Returns:
        STABLE / WEAK / WARN / REVERSE / UNKNOWN
    """
    if pd.isna(adjusted_ic_ir_24m):
        return STATUS_UNKNOWN
    if adjusted_ic_ir_24m >= IC_IR_STABLE_THRESHOLD:
        return STATUS_STABLE
    if adjusted_ic_ir_24m >= IC_IR_WEAK_THRESHOLD:
        return STATUS_WEAK
    if adjusted_ic_ir_24m >= 0:
        return STATUS_WARN
    return STATUS_REVERSE


def compute_trend_flag(adj_ic_ir_12m: float, adj_ic_ir_36m: float) -> str:
    """
    判断趋势预警标志。

    规则：adj_ic_ir_12m < adj_ic_ir_36m - DETERIORATING_GAP（0.15）时触发 DETERIORATING。
    任一输入为 NaN 时返回 INSUFFICIENT_HISTORY（样本不足，无法判断趋势）。

    Args:
        adj_ic_ir_12m: 方向调整后的 12 期 IC_IR
        adj_ic_ir_36m: 方向调整后的 36 期 IC_IR
    Returns:
        OK / DETERIORATING / INSUFFICIENT_HISTORY
    """
    if pd.isna(adj_ic_ir_12m) or pd.isna(adj_ic_ir_36m):
        return TREND_INSUFFICIENT
    if adj_ic_ir_12m < adj_ic_ir_36m - DETERIORATING_GAP:
        return TREND_DETERIORATING
    return TREND_OK


# ---------------------------------------------------------------------------
# FactorHealthMonitor
# ---------------------------------------------------------------------------

class FactorHealthMonitor:
    """
    因子健康监控器。

    读取 IC 历史矩阵和因子方向，构建健康快照和 Markdown 报告。
    不依赖任何 pipeline / signal / backtest 模块。
    matplotlib 仅在外部 plot_health_panel() 函数中导入。

    Args:
        ic_history:        DataFrame，index=rebalance_date，columns=factor_name，值为月度 Rank IC
        factor_directions: Series，index=factor_name，values=+1.0（正向因子）或 -1.0（负向因子）
        windows:           滚动窗口三元组 (w12, w24, w36)，单位：期数（月频约等于月）
        min_obs:           每个滚动窗口所需最少有效观测数
    Raises:
        ValueError: factor_directions 缺少 ic_history 中某因子的方向定义（或为 NaN），
                    拒绝默认全部正向。
    """

    def __init__(
        self,
        ic_history: pd.DataFrame,
        factor_directions: pd.Series,
        windows: tuple[int, int, int] = (12, 24, 36),
        min_obs: int = MIN_VALID_OBS,
    ) -> None:
        missing = [
            f for f in ic_history.columns
            if pd.isna(factor_directions.get(f))
        ]
        if missing:
            raise ValueError(
                f"factor_directions 缺少以下因子的方向定义（不允许默认正向）：{missing}"
            )
        self.ic_history        = ic_history.sort_index()
        self.factor_directions = factor_directions
        self.windows           = windows
        self.min_obs           = min_obs

    def build_snapshot(self, as_of: Optional[pd.Timestamp] = None) -> pd.DataFrame:
        """
        构建因子健康快照 DataFrame。

        Args:
            as_of: 评估截止日期；None 时使用 IC 历史最后一期
        Returns:
            DataFrame，index=factor，列包含：
              factor_direction / n_valid_obs /
              ic_ir_12m / ic_ir_24m / ic_ir_36m /
              adj_ic_ir_12m / adj_ic_ir_24m / adj_ic_ir_36m /
              status / trend_flag / suggested_action
            按 adj_ic_ir_24m 降序排列（NaN 在末尾）。
        时间对齐假设：
            只使用 ic_history.index <= as_of 的数据。
        """
        if as_of is None:
            as_of = self.ic_history.index.max()

        w12, w24, w36 = self.windows
        rows: list[dict] = []

        for factor in self.ic_history.columns:
            direction = float(self.factor_directions.get(factor, np.nan))
            ic_s = self.ic_history[factor]

            ic_ir_12m = compute_rolling_ic_ir(ic_s, w12, as_of, self.min_obs)
            ic_ir_24m = compute_rolling_ic_ir(ic_s, w24, as_of, self.min_obs)
            ic_ir_36m = compute_rolling_ic_ir(ic_s, w36, as_of, self.min_obs)

            adj_12m = ic_ir_12m * direction if not pd.isna(ic_ir_12m) else np.nan
            adj_24m = ic_ir_24m * direction if not pd.isna(ic_ir_24m) else np.nan
            adj_36m = ic_ir_36m * direction if not pd.isna(ic_ir_36m) else np.nan

            status = compute_health_status(adj_24m)
            trend  = compute_trend_flag(adj_12m, adj_36m)
            action = _suggest_action(status, trend)

            # 有效观测总数（截止 as_of）
            n_valid = int(ic_s.dropna()[ic_s.dropna().index <= as_of].count())

            rows.append({
                "factor":           factor,
                "factor_direction": direction,
                "n_valid_obs":      n_valid,
                "ic_ir_12m":        _round_or_nan(ic_ir_12m),
                "ic_ir_24m":        _round_or_nan(ic_ir_24m),
                "ic_ir_36m":        _round_or_nan(ic_ir_36m),
                "adj_ic_ir_12m":    _round_or_nan(adj_12m),
                "adj_ic_ir_24m":    _round_or_nan(adj_24m),
                "adj_ic_ir_36m":    _round_or_nan(adj_36m),
                "status":           status,
                "trend_flag":       trend,
                "suggested_action": action,
            })

        df = pd.DataFrame(rows).set_index("factor")
        df.index.name = "factor"
        return df.sort_values("adj_ic_ir_24m", ascending=False, na_position="last")

    def build_report(
        self,
        snapshot: pd.DataFrame,
        as_of: pd.Timestamp,
    ) -> str:
        """
        生成 Markdown 健康报告。

        Args:
            snapshot: build_snapshot() 的输出
            as_of:    评估截止日期
        Returns:
            Markdown 字符串，包含验证期声明、状态分布、完整快照、分组列表。
        """
        lines: list[str] = []

        lines.append("# 因子健康监控报告")
        lines.append(f"\n生成时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"\n评估截止日期（as_of）：{as_of.date()}")
        lines.append(
            "\n\n> **重要声明**：本报告数据范围为**训练期 + 验证期**"
            "（训练期：2012-2020，验证期：2021-2022）。\n"
            "> 验证期（2021-2022）数据仅用于诊断参考，"
            "**不允许回流至因子筛选逻辑**，不得直接作为调整 `final_factors.json` 的依据。\n"
            "> 如需调整因子池，必须另起训练/验证实验，在训练期内完成验证后方可决策。"
        )

        # 1. 状态分布
        status_counts = snapshot["status"].value_counts()
        lines.append("\n\n---\n\n## 1. 健康状态分布\n")
        lines.append("| 状态 | 因子数 | 说明 |")
        lines.append("|------|--------|------|")
        _desc = {
            STATUS_STABLE:  "adj_IC_IR_24m ≥ 0.30，信号稳定",
            STATUS_WEAK:    "0.20 ≤ adj_IC_IR_24m < 0.30，信号偏弱",
            STATUS_WARN:    "0 ≤ adj_IC_IR_24m < 0.20，信号衰减",
            STATUS_REVERSE: "adj_IC_IR_24m < 0，方向反转",
            STATUS_UNKNOWN: "有效样本不足，无法判断",
        }
        for s in [STATUS_STABLE, STATUS_WEAK, STATUS_WARN, STATUS_REVERSE, STATUS_UNKNOWN]:
            n = status_counts.get(s, 0)
            lines.append(f"| {s} | {n} | {_desc[s]} |")

        # 2. 趋势预警
        trend_counts = snapshot["trend_flag"].value_counts()
        lines.append("\n\n---\n\n## 2. 趋势预警\n")
        deteriorating_list = snapshot[snapshot["trend_flag"] == TREND_DETERIORATING].index.tolist()
        insufficient_list  = snapshot[snapshot["trend_flag"] == TREND_INSUFFICIENT].index.tolist()

        if deteriorating_list:
            lines.append(
                f"⚠️ **DETERIORATING**（近 12 期 adj_IC_IR 显著低于近 36 期，差值 > {DETERIORATING_GAP}）：\n"
            )
            for f in deteriorating_list:
                r = snapshot.loc[f]
                a12 = f"{r['adj_ic_ir_12m']:+.4f}" if pd.notna(r["adj_ic_ir_12m"]) else "N/A"
                a36 = f"{r['adj_ic_ir_36m']:+.4f}" if pd.notna(r["adj_ic_ir_36m"]) else "N/A"
                lines.append(f"  - `{f}`  adj_12m={a12}  adj_36m={a36}")
        else:
            lines.append("✅ 无因子触发 DETERIORATING 趋势预警。")

        if insufficient_list:
            lines.append(
                f"\n\n⚠️ **INSUFFICIENT_HISTORY**（样本不足，无法判断趋势）："
                f"`{'`、`'.join(insufficient_list)}`"
            )

        # 3. 完整快照
        lines.append("\n\n---\n\n## 3. 完整健康快照\n")
        display_cols = [
            "factor_direction", "n_valid_obs",
            "adj_ic_ir_12m", "adj_ic_ir_24m", "adj_ic_ir_36m",
            "status", "trend_flag", "suggested_action",
        ]
        lines.append(snapshot[display_cols].to_markdown(floatfmt=".4f"))

        # 4. 分组列表
        lines.append("\n\n---\n\n## 4. 因子分组详情\n")
        for s in [STATUS_STABLE, STATUS_WEAK, STATUS_WARN, STATUS_REVERSE, STATUS_UNKNOWN]:
            factors_in_s = snapshot[snapshot["status"] == s].index.tolist()
            if not factors_in_s:
                continue
            lines.append(f"\n### {s}（{len(factors_in_s)} 个）\n")
            for f in factors_in_s:
                row = snapshot.loc[f]
                adj = row["adj_ic_ir_24m"]
                adj_str = f"{adj:+.4f}" if pd.notna(adj) else "N/A"
                trend_mark = " ⚠️DETERIORATING" if row["trend_flag"] == TREND_DETERIORATING else ""
                lines.append(f"- `{f}` adj_IC_IR_24m={adj_str}  [{row['trend_flag']}]{trend_mark}")

        # 5. 建议操作
        lines.append("\n\n---\n\n## 5. 建议操作汇总\n")
        lines.append(
            "> 以下建议仅基于 IC 历史统计，不替代人工判断。\n"
            "> `REVIEW_REMOVAL` 表示需要另起实验验证后才能决定是否调池，**不可直接修改主线因子池**。\n"
        )
        review = snapshot[snapshot["suggested_action"].str.startswith("REVIEW_REMOVAL")].index.tolist()
        watch  = snapshot[snapshot["suggested_action"].str.startswith("WATCH_CLOSELY")].index.tolist()
        if review:
            lines.append(f"\n❌ **需要复核（REVIEW_REMOVAL）**：`{'`、`'.join(review)}`")
        if watch:
            lines.append(f"\n⚠️ **密切关注（WATCH_CLOSELY）**：`{'`、`'.join(watch)}`")
        if not review and not watch:
            lines.append("\n✅ 当前无需复核或密切关注的因子。")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 面板图（matplotlib 仅在此函数内部导入）
# ---------------------------------------------------------------------------

def plot_health_panel(
    ic_history: pd.DataFrame,
    snapshot: pd.DataFrame,
    as_of: pd.Timestamp,
    output_path: "Path | str",
    windows: tuple[int, int, int] = (12, 24, 36),
) -> None:
    """
    绘制因子健康面板图，每个因子一个子图，按因子数自动计算布局。

    布局：n_cols = ceil(sqrt(n_factors))，n_rows = ceil(n_factors / n_cols)，
    不写死 6×3，支持任意因子数。

    每个子图内容：
      - 月度 IC 柱状图（颜色：IC 均值正向绿色，负向红色）
      - 滚动 adj_IC_IR_24m 折线（第二纵轴，颜色按健康状态）
      - 子图标题标注因子名、状态、adj_IC_IR_24m 值

    Args:
        ic_history:   IC 历史矩阵（index=rebalance_date，columns=factor）
        snapshot:     build_snapshot() 输出（决定因子列表和状态颜色）
        as_of:        评估截止日期（只展示 <= as_of 的数据）
        output_path:  输出图片路径（.png）
        windows:      与 FactorHealthMonitor 使用相同的滚动窗口
    数据依赖：
        仅依赖传入参数；matplotlib 在函数内部导入（不作为模块级依赖）。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    _STATUS_COLOR = {
        STATUS_STABLE:  "#2ca02c",   # 绿
        STATUS_WEAK:    "#ff7f0e",   # 橙
        STATUS_WARN:    "#d62728",   # 红
        STATUS_REVERSE: "#9467bd",   # 紫
        STATUS_UNKNOWN: "#7f7f7f",   # 灰
    }

    plt.rcParams.update({
        "axes.unicode_minus": False,
        "font.family":        ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
    })

    factors = snapshot.index.tolist()
    n = len(factors)
    if n == 0:
        log.warning("plot_health_panel: 快照中无因子，跳过绘图")
        return

    n_cols = max(1, math.ceil(math.sqrt(n)))
    n_rows = math.ceil(n / n_cols)

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(5.5 * n_cols, 3.2 * n_rows),
        squeeze=False,
    )

    ic_filtered = ic_history[ic_history.index <= as_of].copy()
    w24 = windows[1]

    for idx, factor in enumerate(factors):
        row_i, col_i = divmod(idx, n_cols)
        ax = axes[row_i][col_i]

        ic_s = (
            ic_filtered[factor].dropna()
            if factor in ic_filtered.columns
            else pd.Series(dtype=float)
        )
        status    = snapshot.loc[factor, "status"]
        adj_24m   = snapshot.loc[factor, "adj_ic_ir_24m"]
        direction = float(snapshot.loc[factor, "factor_direction"])
        sc        = _STATUS_COLOR.get(status, "#7f7f7f")

        # 月度 IC 柱状图
        if not ic_s.empty:
            bar_color = "#2ca02c" if ic_s.mean() >= 0 else "#d62728"
            ax.bar(ic_s.index, ic_s.values, color=bar_color, alpha=0.40, width=20, label="月度IC")

        ax.axhline(0, color="black", linewidth=0.6)
        ax.set_ylim(-0.30, 0.30)
        ax.tick_params(axis="x", labelsize=6, rotation=30)
        ax.tick_params(axis="y", labelsize=7)
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))

        # 滚动 adj_IC_IR_24m 折线（第二纵轴）
        if not ic_s.empty and len(ic_s) >= MIN_VALID_OBS:
            rolling_vals, rolling_idx = [], []
            for i in range(len(ic_s)):
                window_data = ic_s.iloc[max(0, i - w24 + 1): i + 1]
                if len(window_data) >= MIN_VALID_OBS:
                    std_v = window_data.std(ddof=1)
                    if std_v > 1e-12:
                        rolling_vals.append(window_data.mean() / std_v * direction)
                        rolling_idx.append(ic_s.index[i])
            if rolling_vals:
                ax2 = ax.twinx()
                ax2.plot(rolling_idx, rolling_vals, color=sc, linewidth=1.5,
                         label="滚动adj_IC_IR", alpha=0.9)
                ax2.axhline(0, color="gray", linewidth=0.4, linestyle="--")
                ax2.tick_params(axis="y", labelsize=6)
                ax2.set_ylabel("adj_IC_IR", fontsize=6, color=sc)

        # 子图标题（颜色编码健康状态）
        adj_str = f"{adj_24m:+.3f}" if pd.notna(adj_24m) else "N/A"
        ax.set_title(
            f"{factor}\n[{status}]  adj_IC_IR_24m={adj_str}",
            fontsize=8, color=sc, pad=3,
        )

    # 隐藏多余子图
    for idx in range(n, n_rows * n_cols):
        row_i, col_i = divmod(idx, n_cols)
        axes[row_i][col_i].set_visible(False)

    fig.suptitle(
        f"因子健康面板（as_of={as_of.date()}）\n"
        f"数据范围：训练期+验证期  ·  仅供诊断，不得回流筛选",
        fontsize=11, y=1.02,
    )
    plt.tight_layout()
    fig.savefig(str(output_path), dpi=120, bbox_inches="tight")
    plt.close(fig)
    log.info("健康面板图已保存：%s", output_path)
