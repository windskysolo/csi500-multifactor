"""
experiments/te_grid/run_te_grid.py
===================================

TE 约束网格实验：固定 Ridge v2 信号，对比 TE=6%/7%/8% 对组合 IR 的影响。

唯一变量：te_target_annual。其余参数（IND_DEV=3%, SGL_DEV=1.5%）与主管线完全一致。
全部产物写入 experiments/te_grid/results/te_{pct}pct/，零修改主管线和 Ridge 实验任何文件。

用法：
    python -m experiments.te_grid.run_te_grid
"""

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from src.attribution.brinson import compute_brinson_attribution
from src.backtest.engine import BacktestConfig, run_backtest
from src.portfolio.covariance import validate_and_repair_covariance
from src.portfolio.optimizer import OptimizeConfig, optimize_single_period

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TE_CANDIDATES = [0.06, 0.07, 0.08]

RIDGE_SIGNAL_PATH = (
    Path(__file__).parent.parent / "ridge_signal" / "results" / "ridge_composite_panel.parquet"
)
COV_CACHE_DIR = cfg.DATA_PROC / "cov_cache"          # 只读，复用已有缓存
RESULTS_DIR   = Path(__file__).parent / "results"
COMPARISON_REPORT = RESULTS_DIR / "comparison_report.md"

IND_DEV = cfg.OPT_INDUSTRY_MAX_DEV   # 3%
SGL_DEV = cfg.OPT_SINGLE_MAX_DEV     # 1.5%
TOPN    = cfg.OPT_TOPN               # 50


def _te_label(te: float) -> str:
    return f"te_{int(round(te * 100))}pct"


def _te_dir(te: float) -> Path:
    d = RESULTS_DIR / _te_label(te)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Step 1: 数据加载（一次性）
# ---------------------------------------------------------------------------

def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """加载 Ridge v2 信号、index_member、industry。只读。"""
    if not RIDGE_SIGNAL_PATH.exists():
        raise FileNotFoundError(
            f"Ridge 信号不存在: {RIDGE_SIGNAL_PATH}\n"
            "请先运行: python -m experiments.ridge_signal.run_ridge_experiment"
        )
    ridge_signal = pd.read_parquet(RIDGE_SIGNAL_PATH)
    log.info("Ridge v2 信号: %s  %s ~ %s", ridge_signal.shape,
             ridge_signal.index[0].date(), ridge_signal.index[-1].date())

    index_member = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    industry_raw = pd.read_parquet(cfg.DATA_PROC / "industry.parquet")
    industry_pivot = (
        industry_raw["industry_code"]
        .unstack(level="ts_code")
        .sort_index()
        .ffill()
    )
    log.info("辅助数据加载完成: index_member=%d 期",
             index_member.index.get_level_values("rebalance_date").nunique())
    return ridge_signal, index_member, industry_pivot


def build_period_data(
    ridge_signal: pd.DataFrame,
    index_member: pd.DataFrame,
    industry_pivot: pd.DataFrame,
) -> tuple:
    """
    预处理每个调仓日的成分股快照、行业映射、停牌/涨跌停标记、协方差缓存。
    与 TE 无关，只需执行一次，三个 TE 共用。

    Returns:
        (available_dates, benchmark_weights_dict, halt_dict, limit_up_dict,
         limit_dn_dict, industry_dict, codes_map, cov_cache)
    """
    available_dates = sorted(
        d for d in index_member.index.get_level_values("rebalance_date").unique()
        if d <= cfg.VALID_END
    )

    benchmark_weights_dict: dict = {}
    halt_dict:     dict = {}
    limit_up_dict: dict = {}
    limit_dn_dict: dict = {}
    industry_dict: dict = {}
    codes_map:     dict = {}

    for T in available_dates:
        snap = index_member.loc[T]
        w_b  = snap["index_weight"] / 100.0
        w_b  = w_b / w_b.sum()
        benchmark_weights_dict[T] = w_b
        halt_dict[T]     = set(snap.index[snap["is_suspended"]])
        limit_up_dict[T] = set(snap.index[snap["is_limit_up_locked"]])
        limit_dn_dict[T] = set(snap.index[snap["is_limit_down_locked"]])
        codes_map[T]     = list(w_b.index)
        ind_dates = industry_pivot.index[industry_pivot.index <= T]
        industry_dict[T] = (
            industry_pivot.loc[ind_dates[-1]].dropna()
            if len(ind_dates) > 0
            else pd.Series(dtype=object)
        )

    cov_cache = _load_cov_cache(available_dates, codes_map)

    log.info("周期数据预处理完成: %d 个调仓日", len(available_dates))
    return (
        available_dates, benchmark_weights_dict, halt_dict, limit_up_dict,
        limit_dn_dict, industry_dict, codes_map, cov_cache,
    )


def _load_cov_cache(dates: list[pd.Timestamp], codes_map: dict) -> dict:
    """复用 cov_cache/（只读）。cache miss 用 eye*1e-4 代替，与主管线行为一致。"""
    cov_cache: dict = {}
    n_loaded = n_miss = 0

    for T in dates:
        codes = list(codes_map[T])
        n = len(codes)
        cache_path = COV_CACHE_DIR / f"{T.strftime('%Y%m%d')}.parquet"

        if cache_path.exists():
            try:
                cov_df = pd.read_parquet(cache_path).reindex(index=codes, columns=codes)
                if not cov_df.isna().any().any():
                    cov_arr = cov_df.values.astype(float)
                    cov_arr, _, _, _ = validate_and_repair_covariance(cov_arr)
                    cov_cache[T] = cov_arr
                    n_loaded += 1
                    continue
            except Exception as exc:
                log.debug("cov cache read error %s: %s", T.date(), exc)

        log.warning("cov cache miss %s — eye*1e-4", T.date())
        cov_cache[T] = np.eye(n) * 1e-4
        n_miss += 1

    log.info("协方差缓存: 命中=%d  miss=%d", n_loaded, n_miss)
    return cov_cache


# ---------------------------------------------------------------------------
# Step 2: 单个 TE 的优化主循环
# ---------------------------------------------------------------------------

def build_weights_for_te(
    te: float,
    ridge_signal: pd.DataFrame,
    available_dates: list,
    benchmark_weights_dict: dict,
    halt_dict: dict,
    limit_up_dict: dict,
    limit_dn_dict: dict,
    industry_dict: dict,
    codes_map: dict,
    cov_cache: dict,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    用指定 TE 运行组合优化（训练期 + 验证期，共 ~144 期）。

    Args:
        te: 年化跟踪误差目标（小数，如 0.07）
    Returns:
        (weights_panel, baseline_panel, meta_df)
    """
    log.info("=== TE=%.0f%% 优化开始 ===", te * 100)

    opt_config = OptimizeConfig(
        te_target_annual  = te,
        industry_max_dev  = IND_DEV,
        single_max_dev    = SGL_DEV,
        topn              = TOPN,
        max_solve_seconds = 30.0,
    )

    tv_signal = ridge_signal.loc[ridge_signal.index <= cfg.VALID_END]

    opt_weights:      dict = {}
    baseline_weights: dict = {}
    meta_rows: list[dict] = []
    w_prev: pd.Series | None = None
    fb_counts: dict = {0: 0, 1: 0, 2: 0}

    t0 = time.perf_counter()
    for i, T in enumerate(available_dates):
        codes = codes_map[T]
        n     = len(codes)

        # 冷启动期（2012-01 ~ 2014-02）无 Ridge 信号，用 0 向量（回退等权）
        alpha = (
            tv_signal.loc[T].reindex(codes).fillna(0.0)
            if T in tv_signal.index
            else pd.Series(0.0, index=codes)
        )

        cov = cov_cache.get(T, np.eye(n) * 1e-4)
        cov_available = T in cov_cache
        w_prev_aligned = w_prev.reindex(codes) if w_prev is not None else None

        result = optimize_single_period(
            alpha          = alpha,
            w_b            = benchmark_weights_dict[T],
            cov            = cov,
            industry_map   = industry_dict[T],
            w_prev         = w_prev_aligned,
            halt_codes     = halt_dict[T],
            limit_up_codes = limit_up_dict[T],
            limit_dn_codes = limit_dn_dict[T],
            config         = opt_config,
        )

        effective_fb = result.fallback_level
        if not cov_available and result.fallback_level == 0:
            effective_fb = 1  # cov 缺失时，记为 L2 即使 QP 求解成功

        opt_weights[T] = result.weights
        w_prev         = result.weights

        # TopN 等权基准（V1，TE 无关，每次循环顺带构造）
        avail = alpha.drop(index=list(halt_dict[T] & set(alpha.index)), errors="ignore")
        top_n = avail.dropna().nlargest(TOPN).index
        w_bl  = pd.Series(0.0, index=codes)
        if len(top_n) > 0:
            w_bl[top_n] = 1.0 / len(top_n)
        baseline_weights[T] = w_bl

        fb_counts[effective_fb] = fb_counts.get(effective_fb, 0) + 1
        meta_rows.append({
            "rebalance_date":       T,
            "fallback_level":       effective_fb,
            "solver_status":        result.solver_status,
            "solve_time_s":         result.solve_time_s,
            "cov_available":        cov_available,
            "constraint_compliant": result.constraint_compliant,
        })

        if (i + 1) % 24 == 0 or (i + 1) == len(available_dates):
            log.info("TE=%.0f%%  进度 %d/%d  用时=%.1fs", te * 100, i + 1,
                     len(available_dates), time.perf_counter() - t0)

    log.info(
        "TE=%.0f%% 完成: %d 期  用时=%.1fs  Fallback: L1=%d L2=%d L3=%d",
        te * 100, len(available_dates), time.perf_counter() - t0,
        fb_counts.get(0, 0), fb_counts.get(1, 0), fb_counts.get(2, 0),
    )

    weights_panel  = pd.DataFrame(opt_weights).T.fillna(0.0)
    baseline_panel = pd.DataFrame(baseline_weights).T.fillna(0.0)
    for df in (weights_panel, baseline_panel):
        df.index.name = "rebalance_date"
        df.columns    = df.columns.astype(str)

    meta_df = pd.DataFrame(meta_rows).set_index("rebalance_date")
    meta_df.index = pd.DatetimeIndex(meta_df.index)

    return weights_panel, baseline_panel, meta_df


# ---------------------------------------------------------------------------
# Step 3: 回测 + 归因
# ---------------------------------------------------------------------------

def _annual_turnover_pct(trade_log: pd.DataFrame, n_months: int) -> float:
    if trade_log.empty or n_months == 0:
        return float("nan")
    total_to = (
        (trade_log["buy_value"] + trade_log["sell_value"])
        / trade_log["portfolio_value_before"]
    ).sum()
    return total_to / n_months * 12 * 100


def run_backtest_for_te(
    te: float,
    weights_panel: pd.DataFrame,
    baseline_panel: pd.DataFrame,
) -> tuple:
    """
    验证期 V1（TopN 等权）+ V2（优化权重）回测，以及 V2 的 Brinson 归因。

    Returns:
        (result_v1, result_v2, to_v1, to_v2, brinson_summary)
    """
    log.info("=== TE=%.0f%% 回测 ===", te * 100)
    bt_config = BacktestConfig(initial_value=1.0)

    result_v1 = run_backtest(baseline_panel, cfg.VALID_START, cfg.VALID_END, bt_config)
    result_v2 = run_backtest(weights_panel,  cfg.VALID_START, cfg.VALID_END, bt_config)

    n_months = len(result_v2.trade_log) if not result_v2.trade_log.empty else 0
    to_v1    = _annual_turnover_pct(result_v1.trade_log, n_months)
    to_v2    = _annual_turnover_pct(result_v2.trade_log, n_months)

    m = result_v2.metrics
    log.info(
        "TE=%.0f%%  IR=%.3f  超额=%.2f%%  MDD=%.2f%%  换手=%.0f%%",
        te * 100, m["information_ratio"],
        m["excess_return"] * 100,
        abs(m["excess_max_drawdown"]) * 100,
        to_v2,
    )

    brinson_summary = pd.DataFrame()
    try:
        _, brinson_summary = compute_brinson_attribution(
            result_v2.actual_weights, cfg.VALID_START, cfg.VALID_END
        )
        log.info("TE=%.0f%% Brinson 完成: %d 期", te * 100, len(brinson_summary))
    except Exception as exc:
        log.error("TE=%.0f%% Brinson 归因失败: %s", te * 100, exc)

    return result_v1, result_v2, to_v1, to_v2, brinson_summary


# ---------------------------------------------------------------------------
# Step 4: 保存单个 TE 的产物
# ---------------------------------------------------------------------------

def save_te_results(
    te: float,
    weights_panel: pd.DataFrame,
    baseline_panel: pd.DataFrame,
    meta_df: pd.DataFrame,
    result_v2,
    to_v2: float,
    brinson_summary: pd.DataFrame,
) -> None:
    out = _te_dir(te)

    weights_panel.to_parquet(out / "weights_optimized.parquet")
    baseline_panel.to_parquet(out / "weights_baseline.parquet")
    meta_df.to_parquet(out / "weights_meta.parquet")

    pd.Series(result_v2.metrics, name="value").to_frame().to_parquet(
        out / "backtest_metrics.parquet"
    )

    nav_df = pd.DataFrame({
        "strategy":  result_v2.nav,
        "benchmark": result_v2.benchmark_nav,
    }).dropna()
    nav_df.index.name = "trade_date"
    nav_df.to_parquet(out / "backtest_nav.parquet")

    if not result_v2.actual_weights.empty:
        result_v2.actual_weights.to_parquet(out / "actual_weights_v2.parquet")
    if not result_v2.trade_log.empty:
        result_v2.trade_log.to_parquet(out / "trades_v2.parquet")
    if not brinson_summary.empty:
        brinson_summary.to_parquet(out / "brinson_period_summary.parquet")

    log.info("TE=%.0f%% 产物写入: %s", te * 100, out)


# ---------------------------------------------------------------------------
# Step 5: 对比报告
# ---------------------------------------------------------------------------

def _pct(v: float, sign: bool = False, digits: int = 2) -> str:
    if isinstance(v, float) and np.isnan(v):
        return "N/A"
    spec = f"{'+' if sign else ''}.{digits}f"
    return f"{v * 100:{spec}}%"


def _fmt(v: float, digits: int = 3) -> str:
    if isinstance(v, float) and np.isnan(v):
        return "N/A"
    return f"{v:.{digits}f}"


def _check(cond: bool | None) -> str:
    return "—" if cond is None else ("✅" if cond else "❌")


def generate_comparison_report(
    all_results: dict,
    gen_time: str,
) -> str:
    """三列（TE=6/7/8%）Markdown 对比报告。"""

    def _m(te: float, key: str) -> float:
        return float(all_results[te]["metrics"].get(key, float("nan")))

    def _b(te: float, col: str) -> float:
        bs = all_results[te]["brinson"]
        if bs.empty or col not in bs.columns:
            return float("nan")
        return float(bs[col].sum())

    def _fb(te: float) -> str:
        meta  = all_results[te]["meta"]
        v_meta = meta.loc[meta.index >= cfg.VALID_START]
        if v_meta.empty:
            return "N/A"
        l1    = int((v_meta["fallback_level"] == 0).sum())
        total = len(v_meta)
        return f"{l1}/{total}"

    cols   = [_te_label(te).replace("_", "=").replace("pct", "%") for te in TE_CANDIDATES]
    header = "| 指标 | " + " | ".join(cols) + " | 目标 |"
    sep    = "|------|" + "---:|" * len(TE_CANDIDATES) + "------|"

    def table_row(label: str, vals: list[str], target: str = "—") -> str:
        return "| " + " | ".join([label] + vals + [target]) + " |"

    lines = [
        "# TE 约束网格实验对比报告",
        "",
        f"> 生成时间：{gen_time}",
        f"> 信号：Ridge v2（超额收益目标，alpha=5000）",
        f"> 固定参数：IND\\_DEV={IND_DEV:.0%}，SGL\\_DEV={SGL_DEV:.1%}，TOPN={TOPN}",
        f"> 实验目录：`experiments/te_grid/results/`",
        f"> 回测区间：{cfg.VALID_START.date()} ~ {cfg.VALID_END.date()}（验证期）",
        "",
        "---",
        "",
        "## 1. 验证期核心指标（V2 优化组合）",
        "",
        header, sep,
    ]

    metric_rows = [
        (
            "年化超额收益",
            [_pct(_m(te, "excess_return"), sign=True) for te in TE_CANDIDATES],
            "—",
        ),
        (
            "信息比率 IR",
            [
                f"{_fmt(_m(te, 'information_ratio'))} "
                f"{_check(not np.isnan(_m(te, 'information_ratio')) and _m(te, 'information_ratio') >= 0.5)}"
                for te in TE_CANDIDATES
            ],
            "≥ 0.5",
        ),
        (
            "超额最大回撤",
            [
                f"{_pct(abs(_m(te, 'excess_max_drawdown')))} "
                f"{_check(not np.isnan(_m(te, 'excess_max_drawdown')) and abs(_m(te, 'excess_max_drawdown')) <= 0.10)}"
                for te in TE_CANDIDATES
            ],
            "≤ 10%",
        ),
        (
            "跟踪误差（实现值）",
            [_pct(_m(te, "tracking_error")) for te in TE_CANDIDATES],
            "—",
        ),
        (
            "月度胜率",
            [_pct(_m(te, "monthly_win_rate")) for te in TE_CANDIDATES],
            "—",
        ),
        (
            "年化双边换手",
            [
                f"{all_results[te]['to_v2']:.0f}% "
                f"{_check(not np.isnan(all_results[te]['to_v2']) and 500 <= all_results[te]['to_v2'] <= 1500)}"
                for te in TE_CANDIDATES
            ],
            "500-1500%",
        ),
        (
            "绝对收益（策略）",
            [_pct(_m(te, "annualized_return"), sign=True) for te in TE_CANDIDATES],
            "—",
        ),
    ]

    for label, vals, target in metric_rows:
        lines.append(table_row(label, vals, target))

    # Brinson 归因
    lines += [
        "",
        "---",
        "",
        "## 2. Brinson BHB 归因对比（V2，验证期累计）",
        "",
        "| 效应 | " + " | ".join(cols) + " |",
        "|------|" + "---:|" * len(TE_CANDIDATES),
    ]
    for label, col in [
        ("总超额收益", "excess_return"),
        ("配置效应",   "allocation_effect"),
        ("选股效应",   "selection_effect"),
        ("交叉效应",   "interaction_effect"),
    ]:
        vals = [_pct(_b(te, col), sign=True) for te in TE_CANDIDATES]
        lines.append("| " + " | ".join([label] + vals) + " |")

    # Fallback
    lines += [
        "",
        "---",
        "",
        "## 3. 优化器 Fallback（验证期 L1 严格解 / 总期数）",
        "",
        "| | " + " | ".join(cols) + " |",
        "|---|" + "---:|" * len(TE_CANDIDATES),
        "| L1（严格 QP 解）| " + " | ".join(_fb(te) for te in TE_CANDIDATES) + " |",
    ]

    # 结论
    ir_list  = [_m(te, "information_ratio") for te in TE_CANDIDATES]
    mdd_list = [abs(_m(te, "excess_max_drawdown")) for te in TE_CANDIDATES]
    to_list  = [all_results[te]["to_v2"] for te in TE_CANDIDATES]

    best_idx = int(np.nanargmax(ir_list))
    best_te  = TE_CANDIDATES[best_idx]
    best_ir  = ir_list[best_idx]
    best_mdd = mdd_list[best_idx]
    base_ir  = ir_list[0]  # TE=6%

    lines += [
        "",
        "---",
        "",
        "## 4. 结论与决策",
        "",
        f"- **最优 TE**：{best_te:.0%}（IR={best_ir:.3f}，MDD={best_mdd:.1%}）",
        f"- IR 相比 TE=6% 基线变化：{best_ir - base_ir:+.3f}",
    ]

    if best_ir >= 0.5:
        lines.append(f"- IR={best_ir:.3f} ≥ 0.5 ✅ 通过验证期目标")
    else:
        lines.append(f"- IR={best_ir:.3f} < 0.5 ❌ 仍未达标，差距 {0.5 - best_ir:.3f}")

    if best_mdd <= 0.10:
        lines.append(f"- 超额最大回撤 {best_mdd:.1%} ≤ 10% ✅")
    else:
        lines.append(f"- ⚠️ 超额最大回撤 {best_mdd:.1%} > 10% ❌，风险偏高")

    ir_trend = " → ".join(f"{ir:.3f}" for ir in ir_list)
    lines.append(f"- IR 随 TE 变化趋势（6%→7%→8%）：{ir_trend}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    gen_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    log.info("=" * 60)
    log.info("TE 网格实验  候选: %s", [f"{t:.0%}" for t in TE_CANDIDATES])
    log.info("=" * 60)

    # --- 一次性数据加载 ---
    ridge_signal, index_member, industry_pivot = load_data()
    (
        available_dates, benchmark_weights_dict, halt_dict,
        limit_up_dict, limit_dn_dict, industry_dict, codes_map, cov_cache,
    ) = build_period_data(ridge_signal, index_member, industry_pivot)

    all_results: dict = {}

    for te in TE_CANDIDATES:
        # 优化
        weights_panel, baseline_panel, meta_df = build_weights_for_te(
            te, ridge_signal, available_dates,
            benchmark_weights_dict, halt_dict, limit_up_dict, limit_dn_dict,
            industry_dict, codes_map, cov_cache,
        )

        # 回测 + 归因
        result_v1, result_v2, to_v1, to_v2, brinson_summary = run_backtest_for_te(
            te, weights_panel, baseline_panel
        )

        # 保存
        save_te_results(te, weights_panel, baseline_panel, meta_df,
                        result_v2, to_v2, brinson_summary)

        all_results[te] = {
            "metrics": result_v2.metrics,
            "to_v2":   to_v2,
            "brinson": brinson_summary,
            "meta":    meta_df,
        }

    # --- 对比报告 ---
    log.info("=== 生成对比报告 ===")
    report = generate_comparison_report(all_results, gen_time)
    COMPARISON_REPORT.write_text(report, encoding="utf-8")
    log.info("对比报告: %s", COMPARISON_REPORT)

    # 终端打印摘要
    log.info("=" * 60)
    log.info("TE 网格实验完成  结果摘要：")
    for te in TE_CANDIDATES:
        m = all_results[te]["metrics"]
        log.info(
            "  TE=%.0f%%  IR=%.3f  超额=%.2f%%  MDD=%.2f%%  换手=%.0f%%",
            te * 100,
            m["information_ratio"],
            m["excess_return"] * 100,
            abs(m["excess_max_drawdown"]) * 100,
            all_results[te]["to_v2"],
        )
    log.info("=" * 60)


if __name__ == "__main__":
    main()
