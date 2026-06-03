"""
scripts/generate_backtest_report.py — 从 parquet 产物自动生成验证期报告

从 data/processed/ 下的当前 parquet 产物读取指标，生成 reports/analysis_v2_results.md。
每次调用均覆盖旧报告，报告头部注明生成时间和数据源路径，不保留任何硬编码旧数值。

设计原则：
  - 所有数字来自 parquet（无例外），不手写历史数值
  - 若 parquet 不存在，报告写入"产物缺失"说明，不崩溃
  - 归因与 NAV 的 reconciliation 表纳入报告，方便审计

用法：
  python -m scripts.generate_backtest_report
  （也可由 run_attribution.py 在归因完成后自动调用）
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from scripts.test_set_ledger import count_test_set_runs, remaining_test_set_runs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
NAV_PATH            = cfg.DATA_PROC / "backtest_nav.parquet"
METRICS_PATH        = cfg.DATA_PROC / "backtest_metrics.parquet"
TRADES_V2_PATH      = cfg.DATA_PROC / "backtest_trades_v2.parquet"
BRINSON_SUMMARY     = cfg.DATA_PROC / "brinson_period_summary.parquet"
FACTOR_SUMMARY      = cfg.DATA_PROC / "factor_attr_period_summary.parquet"
RECONCILIATION_PATH = cfg.DATA_PROC / "attribution_nav_reconciliation.parquet"
METADATA_PATH       = cfg.DATA_PROC / "attribution_metadata.json"
REPORT_PATH         = _ROOT / "reports" / "analysis_v2_results.md"

_MISSING = "*（产物缺失，请先运行 run_backtest.py 和 run_attribution.py）*"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _read_parquet(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        log.warning("读取 %s 失败: %s", path.name, exc)
        return None


def _pct(val: float, digits: int = 2) -> str:
    if val != val:   # NaN
        return "N/A"
    return f"{val * 100:+.{digits}f}%"


def _pos_pct(val: float, digits: int = 2) -> str:
    if val != val:
        return "N/A"
    return f"{val * 100:.{digits}f}%"


def _annual_turnover(trade_log: pd.DataFrame, n_months: int) -> float:
    """双边年化换手率（小数形式）。"""
    if trade_log.empty or n_months == 0:
        return float("nan")
    total_to = (
        (trade_log["buy_value"] + trade_log["sell_value"])
        / trade_log["portfolio_value_before"]
    ).sum()
    return total_to / n_months * 12


def _check_mark(passed: bool | None) -> str:
    if passed is None:
        return "—"
    return "✅" if passed else "❌"


# ---------------------------------------------------------------------------
# 报告各节生成函数
# ---------------------------------------------------------------------------

def _section_header(gen_time: str, metadata: dict | None = None) -> str:
    test_count = count_test_set_runs()
    test_remaining = remaining_test_set_runs()
    return f"""\
# 验证期回测与归因报告（自动生成）

> **本报告由 `scripts/generate_backtest_report.py` 从当前 parquet 产物自动生成。**
> 生成时间：{gen_time}
> 数据源目录：`{cfg.DATA_PROC.relative_to(_ROOT)}/`
> 覆盖期间：验证集 {cfg.VALID_START.date()} ~ {cfg.VALID_END.date()}
> 测试集状态：已运行 **{test_count} 次**（2023-2025），剩余可运行次数 **{test_remaining} 次**（上限 3；以 `docs/check/test_set_runs.json` 为准）。

---
"""


def _section_core_metrics(metrics: pd.DataFrame | None, trade_log: pd.DataFrame | None) -> str:
    if metrics is None:
        return f"## 1. 核心回测指标\n\n{_MISSING}\n\n"

    def _get(col: str, key: str, default: float = float("nan")) -> float:
        if col not in metrics.columns or key not in metrics.index:
            return default
        return float(metrics.loc[key, col])

    n_months = len(trade_log) if (trade_log is not None and not trade_log.empty) else 0
    to_v1 = _annual_turnover(trade_log, n_months) if trade_log is not None else float("nan")
    to_v2 = to_v1  # 同一 trade_log，两版本共享；若有分版本可分别传入

    rows = []
    for col_label, col_key in [("V1 Baseline（等权）", "v1"), ("V2 优化组合", "v2")]:
        er  = _get(col_key, "excess_return")
        ir  = _get(col_key, "information_ratio")
        mdd = _get(col_key, "excess_max_drawdown")
        te  = _get(col_key, "tracking_error")
        wr  = _get(col_key, "monthly_win_rate")
        ar  = _get(col_key, "annualized_return")
        br  = _get(col_key, "benchmark_return")

        ir_ok  = ir >= 0.5 if ir == ir else None
        mdd_ok = abs(mdd) <= 0.10 if mdd == mdd else None

        rows.append(
            f"| {col_label} | {_pct(er)} | {ir:.3f} {_check_mark(ir_ok)} "
            f"| {_pos_pct(abs(mdd))} {_check_mark(mdd_ok)} "
            f"| {_pos_pct(te)} | {_pos_pct(wr, 1)} "
            f"| {_pct(ar)} | {_pct(br)} |"
        )

    table = "\n".join(rows)
    return f"""\
## 1. 核心回测指标

| 版本 | 年化超额 | IR（目标≥0.5） | 超额最大回撤（目标≤10%） | 跟踪误差 | 月胜率 | 绝对收益 | 基准收益 |
|------|----------|---------------|------------------------|----------|--------|----------|----------|
{table}

> 换手率指标（V2）：{_pos_pct(to_v2, 0)} 年化双边（目标 500-1500%）

---
"""


def _section_brinson(summary: pd.DataFrame | None) -> str:
    if summary is None:
        return f"## 2. Brinson BHB 行业归因\n\n{_MISSING}\n\n"

    effect_cols = ["allocation_effect", "selection_effect", "interaction_effect", "total_effect"]
    present_cols = [c for c in effect_cols if c in summary.columns]
    totals = summary[present_cols].sum() if present_cols else pd.Series(dtype=float)

    total_excess = float(summary["excess_return"].sum()) if "excess_return" in summary.columns else float("nan")
    avg_cash     = float(summary["cash_weight"].mean())  if "cash_weight" in summary.columns else float("nan")

    lines = [
        "## 2. Brinson BHB 行业归因",
        "",
        f"| 效应 | 全期累计 |",
        f"|------|---------|",
        f"| 总超额收益 | {_pct(total_excess)} |",
    ]
    effect_labels = {
        "allocation_effect": "配置效应",
        "selection_effect":  "选股效应",
        "interaction_effect": "交叉效应",
        "total_effect":      "总效应（汇总验证）",
    }
    for col in present_cols:
        lines.append(f"| {effect_labels.get(col, col)} | {_pct(float(totals[col]))} |")
    lines.append(f"| 平均现金仓位 | {_pos_pct(avg_cash)} |")
    lines.append("")
    lines.append(f"> 期数：{len(summary)} 个月")
    lines.append(f"> 月度胜率（超额 > 0）：{_pos_pct((summary['excess_return'] > 0).mean(), 1)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _section_factor_attr(summary: pd.DataFrame | None) -> str:
    if summary is None:
        return f"## 3. 因子收益归因\n\n{_MISSING}\n\n"

    group_cols = [c for c in ["value", "quality", "growth", "momentum",
                               "volatility", "liquidity", "fund_flow", "residual"]
                  if c in summary.columns]
    sums = summary[group_cols].sum() if group_cols else pd.Series(dtype=float)

    avg_r2    = float(summary["regression_r2"].mean())  if "regression_r2" in summary.columns else float("nan")
    factor_src = summary["factor_list_source"].iloc[0] if "factor_list_source" in summary.columns else "未知"

    lines = [
        "## 3. 因子收益归因（WLS 横截面回归）",
        "",
        f"因子列表来源：`{factor_src}`　　平均加权 R²：{avg_r2:.3f}",
        "",
        "| 因子组 | 全期累计贡献 |",
        "|--------|------------|",
    ]
    label_map = {
        "value": "价值", "quality": "质量", "growth": "成长",
        "momentum": "动量", "volatility": "波动率",
        "liquidity": "流动性", "fund_flow": "资金流向",
        "residual": "残差（特异 + 成本 + 拟合误差）",
    }
    for col in group_cols:
        lines.append(f"| {label_map.get(col, col)} | {_pct(float(sums[col]))} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _section_reconciliation(recon: pd.DataFrame | None) -> str:
    if recon is None or recon.empty:
        return f"## 4. 归因收益与 NAV 对账（Reconciliation）\n\n{_MISSING}\n\n"

    max_disc = float(recon["excess_discrepancy"].abs().max()) if "excess_discrepancy" in recon.columns else float("nan")
    avg_disc = float(recon["excess_discrepancy"].mean())      if "excess_discrepancy" in recon.columns else float("nan")

    lines = [
        "## 4. 归因收益与 NAV 对账（Reconciliation）",
        "",
        "对账说明：",
        "- `nav_strategy_return`：回测 NAV 在该月的实际收益（含成本、现金拖累）",
        "- `brinson_strategy_return`：Brinson 归因中策略股票部分的加权收益（不含成本）",
        "- `strategy_discrepancy = nav - brinson`：预期 ≤ 0，主要为成本与现金拖累",
        "",
        f"| 指标 | 值 |",
        f"|------|---|",
        f"| 全期最大超额偏差（绝对值） | {_pos_pct(max_disc)} |",
        f"| 全期平均超额偏差 | {_pct(avg_disc)} |",
        f"| 对账月数 | {len(recon)} |",
        "",
    ]

    if max_disc > 0.005:
        lines.append(
            "> ⚠️ 超额偏差 > 0.5%，可能存在口径不一致，请检查归因收益口径与回测 T+1 "
            "开盘执行假设是否对齐。"
        )
    else:
        lines.append("> 超额偏差在合理范围（≤ 0.5%），主要由成本/现金拖累解释，口径一致。")

    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _section_risks(metadata: dict | None) -> str:
    risks = []
    if metadata and "known_unfixed_risks" in metadata:
        risks = metadata["known_unfixed_risks"]

    lines = [
        "## 5. 已知未修复风险",
        "",
    ]
    if risks:
        for r in risks:
            lines.append(f"- {r}")
    elif metadata:
        lines.append("*（当前 metadata 未记录已知未修复风险。）*")
    else:
        lines.append("*（元数据文件缺失，请运行 run_attribution.py 后再生成报告）*")

    lines.append("")
    lines.append(
        "> 完整风险清单见 `docs/check/14_incomplete_fix_register.md`。"
        "发布前请确认所有 Blocker/High 已关闭或明确降级。"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def generate() -> None:
    """读取当前 parquet 产物，生成 reports/analysis_v2_results.md。"""
    gen_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log.info("生成验证期报告（%s）...", gen_time)

    metrics     = _read_parquet(METRICS_PATH)
    trade_log   = _read_parquet(TRADES_V2_PATH)
    brinson_sum = _read_parquet(BRINSON_SUMMARY)
    factor_sum  = _read_parquet(FACTOR_SUMMARY)
    recon       = _read_parquet(RECONCILIATION_PATH)

    metadata: dict | None = None
    if METADATA_PATH.exists():
        try:
            metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("attribution_metadata.json 读取失败: %s", exc)

    report = (
        _section_header(gen_time, metadata)
        + _section_core_metrics(metrics, trade_log)
        + _section_brinson(brinson_sum)
        + _section_factor_attr(factor_sum)
        + _section_reconciliation(recon)
        + _section_risks(metadata)
    )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    log.info("报告已写入: %s", REPORT_PATH)


def main() -> None:
    generate()


if __name__ == "__main__":
    main()
