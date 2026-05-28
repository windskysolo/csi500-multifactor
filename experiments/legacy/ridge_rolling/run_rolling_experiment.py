"""
experiments/ridge_rolling/run_rolling_experiment.py
====================================================

Pipeline E — Rolling-Window Ridge 对比实验

对比扩展窗口（Pipeline B 基准，IR=0.483）与滚动窗口（36m/48m/60m）在：
  1. 信号层面：IC_IR（训练期 + 验证期）
  2. 组合层面：验证期 IR（λ=0.005，与 Pipeline B 设置完全一致）

扩展窗口基准直接复用 Pipeline B 的已有产物，不重新运行优化器。
新建文件全部写入 experiments/ridge_rolling/results/，不修改任何现有文件。

用法：
    python -m experiments.ridge_rolling.run_rolling_experiment
"""

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
from src.portfolio.covariance import validate_and_repair_covariance
from src.portfolio.optimizer import OptimizeConfig, optimize_single_period
from src.backtest.engine import BacktestConfig, run_backtest

from experiments.legacy.ridge_signal.run_ridge_experiment import (
    load_final_factors,
    load_factor_panels,
    load_fwd_ret,
    compute_bench_ret_series,
    compute_ic_series,
    compute_ic_stats,
    split_ic_by_period,
)
from experiments.legacy.ridge_rolling.rolling_combiner import RidgeRollingCombiner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

WINDOW_VARIANTS: list[int] = [36, 48, 60]
LAM_BEST: float = 0.005      # Pipeline B 最优 λ

TE_FIXED = 0.06
IND_DEV  = cfg.OPT_INDUSTRY_MAX_DEV
SGL_DEV  = cfg.OPT_SINGLE_MAX_DEV
TOPN     = cfg.OPT_TOPN

ALPHA_CANDIDATES: list[float] = [0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0]

RESULTS_DIR = Path(__file__).parent / "results"

# Pipeline B 现有产物（expanding 基准，直接加载，不重新跑优化器）
EXPANDING_PANEL_PATH  = Path(__file__).parent.parent / "ridge_signal" / "results" / "ridge_composite_panel.parquet"
EXPANDING_COEF_PATH   = Path(__file__).parent.parent / "ridge_signal" / "results" / "ridge_coef_history.parquet"
EXPANDING_METRICS_PATH = Path(__file__).parent.parent / "turnover_lambda_grid" / "results" / "lam_0050" / "backtest_metrics_valid.parquet"
EXPANDING_TO_PATH     = Path(__file__).parent.parent / "turnover_lambda_grid" / "results" / "lam_0050" / "turnover_summary.parquet"

COV_CACHE_DIR    = cfg.DATA_PROC / "cov_cache"
INDEX_MEMBER_PATH = cfg.DATA_PROC / "index_member.parquet"


def _label(window_months: int | None) -> str:
    return "expanding" if window_months is None else f"rolling_{window_months}m"


# ---------------------------------------------------------------------------
# 信号构建
# ---------------------------------------------------------------------------

def build_rolling_signal(
    window_months: int,
    factor_names: list[str],
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    bench_ret_series: pd.Series,
    all_dates: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, pd.DataFrame | None, float]:
    """
    构建单个滚动窗口变体的 Ridge 信号面板。

    Returns:
        (signal_panel, coef_history, selected_alpha)
    """
    label = _label(window_months)
    log.info("=" * 55)
    log.info("构建信号: %s", label)
    log.info("=" * 55)

    combiner = RidgeRollingCombiner(
        factor_names,
        window_months=window_months,
        alpha_candidates=ALPHA_CANDIDATES,
        use_excess_return=True,
    )
    selected_alpha = combiner.select_alpha_walk_forward(
        factor_panels, fwd_ret_panel, all_dates,
        bench_ret_series=bench_ret_series,
    )
    log.info("%s: 选定 alpha=%.1f", label, selected_alpha)

    panel = combiner.build_ridge_panel(
        factor_panels, fwd_ret_panel, all_dates,
        bench_ret_series=bench_ret_series,
    )
    coef_history = combiner.coef_history_

    panel.to_parquet(RESULTS_DIR / f"{label}_composite_panel.parquet")
    if coef_history is not None and not coef_history.empty:
        coef_history.to_parquet(RESULTS_DIR / f"{label}_coef_history.parquet")
    combiner.cv_results_.to_csv(RESULTS_DIR / f"{label}_cv_results.csv", index=False)

    first = panel.index[0].date() if not panel.empty else "N/A"
    log.info("%s: panel shape=%s  首个信号日期=%s", label, panel.shape, first)
    return panel, coef_history, selected_alpha


# ---------------------------------------------------------------------------
# 协方差缓存加载
# ---------------------------------------------------------------------------

def _load_cov_cache(dates: list[pd.Timestamp], codes_map: dict) -> dict:
    cov_cache: dict = {}
    n_loaded = n_miss = 0
    for T in dates:
        codes = list(codes_map[T])
        n = len(codes)
        path = COV_CACHE_DIR / f"{T.strftime('%Y%m%d')}.parquet"
        if path.exists():
            try:
                cov_df = pd.read_parquet(path).reindex(index=codes, columns=codes)
                if not cov_df.isna().any().any():
                    arr, _, _, _ = validate_and_repair_covariance(
                        cov_df.values.astype(float)
                    )
                    cov_cache[T] = arr
                    n_loaded += 1
                    continue
            except Exception as exc:
                log.debug("协方差读取失败 %s: %s", T.date(), exc)
        log.warning("协方差缓存 miss %s — 使用 eye*1e-4", T.date())
        cov_cache[T] = np.eye(n) * 1e-4
        n_miss += 1
    log.info("协方差缓存: 命中=%d  miss=%d", n_loaded, n_miss)
    return cov_cache


# ---------------------------------------------------------------------------
# 调仓日预处理（与协方差无关，一次性）
# ---------------------------------------------------------------------------

def build_period_data(
    index_member: pd.DataFrame,
    industry_pivot: pd.DataFrame,
) -> tuple:
    available_dates = sorted(
        d for d in index_member.index.get_level_values("rebalance_date").unique()
        if d <= cfg.VALID_END
    )
    bw_dict: dict = {}
    halt_dict: dict = {}
    limit_up_dict: dict = {}
    limit_dn_dict: dict = {}
    industry_dict: dict = {}
    codes_map: dict = {}

    for T in available_dates:
        snap = index_member.loc[T]
        w_b  = snap["index_weight"] / 100.0
        w_b  = w_b / w_b.sum()
        bw_dict[T]        = w_b
        halt_dict[T]      = set(snap.index[snap["is_suspended"]])
        limit_up_dict[T]  = set(snap.index[snap["is_limit_up_locked"]])
        limit_dn_dict[T]  = set(snap.index[snap["is_limit_down_locked"]])
        codes_map[T]      = list(w_b.index)
        ind_dates = industry_pivot.index[industry_pivot.index <= T]
        industry_dict[T]  = (
            industry_pivot.loc[ind_dates[-1]].dropna()
            if len(ind_dates) > 0 else pd.Series(dtype=object)
        )

    cov_cache = _load_cov_cache(available_dates, codes_map)
    log.info("调仓日预处理完成: %d 期", len(available_dates))
    return available_dates, bw_dict, halt_dict, limit_up_dict, limit_dn_dict, industry_dict, codes_map, cov_cache


# ---------------------------------------------------------------------------
# 优化器 + 回测
# ---------------------------------------------------------------------------

def _annual_turnover_pct(trade_log: pd.DataFrame, n_months: int) -> float:
    if trade_log.empty or n_months == 0:
        return float("nan")
    total_to = (
        (trade_log["buy_value"] + trade_log["sell_value"])
        / trade_log["portfolio_value_before"]
    ).sum()
    return total_to / n_months * 12 * 100


def run_optimizer_backtest(
    label: str,
    signal_panel: pd.DataFrame,
    available_dates: list[pd.Timestamp],
    bw_dict: dict,
    halt_dict: dict,
    limit_up_dict: dict,
    limit_dn_dict: dict,
    industry_dict: dict,
    codes_map: dict,
    cov_cache: dict,
) -> dict:
    """
    用 λ=0.005 运行优化器 + 验证期回测，产物写入 results/{label}/。

    Returns:
        dict with keys: metrics_valid, metrics_train, to_train, to_valid
    """
    log.info("=== 优化器+回测: %s (λ=%.3f) ===", label, LAM_BEST)

    opt_cfg = OptimizeConfig(
        te_target_annual  = TE_FIXED,
        industry_max_dev  = IND_DEV,
        single_max_dev    = SGL_DEV,
        topn              = TOPN,
        turnover_lambda   = LAM_BEST,
        max_solve_seconds = 30.0,
    )

    tv_signal = signal_panel.loc[signal_panel.index <= cfg.VALID_END]
    opt_weights: dict      = {}
    baseline_weights: dict = {}
    w_prev: pd.Series | None = None
    t0 = time.perf_counter()

    for i, T in enumerate(available_dates):
        codes = codes_map[T]
        n     = len(codes)
        alpha = (
            tv_signal.loc[T].reindex(codes).fillna(0.0)
            if T in tv_signal.index
            else pd.Series(0.0, index=codes)
        )
        cov            = cov_cache.get(T, np.eye(n) * 1e-4)
        w_prev_aligned = w_prev.reindex(codes) if w_prev is not None else None

        result = optimize_single_period(
            alpha          = alpha,
            w_b            = bw_dict[T],
            cov            = cov,
            industry_map   = industry_dict[T],
            w_prev         = w_prev_aligned,
            halt_codes     = halt_dict[T],
            limit_up_codes = limit_up_dict[T],
            limit_dn_codes = limit_dn_dict[T],
            config         = opt_cfg,
        )
        opt_weights[T] = result.weights
        w_prev         = result.weights

        avail = alpha.drop(index=list(halt_dict[T] & set(alpha.index)), errors="ignore")
        top_n = avail.dropna().nlargest(TOPN).index
        w_bl  = pd.Series(0.0, index=codes)
        if len(top_n) > 0:
            w_bl[top_n] = 1.0 / len(top_n)
        baseline_weights[T] = w_bl

        if (i + 1) % 24 == 0 or (i + 1) == len(available_dates):
            log.info(
                "  %s  进度 %d/%d  %.1fs",
                label, i + 1, len(available_dates), time.perf_counter() - t0,
            )

    weights_panel  = pd.DataFrame(opt_weights).T.fillna(0.0)
    baseline_panel = pd.DataFrame(baseline_weights).T.fillna(0.0)
    for df in (weights_panel, baseline_panel):
        df.index.name = "rebalance_date"
        df.columns    = df.columns.astype(str)

    bt_cfg       = BacktestConfig(initial_value=1.0)
    result_train = run_backtest(weights_panel, cfg.TRAIN_START, cfg.TRAIN_END, bt_cfg)
    result_valid = run_backtest(weights_panel, cfg.VALID_START, cfg.VALID_END, bt_cfg)

    n_train  = len(result_train.trade_log) if not result_train.trade_log.empty else 0
    n_valid  = len(result_valid.trade_log) if not result_valid.trade_log.empty else 0
    to_train = _annual_turnover_pct(result_train.trade_log, n_train)
    to_valid = _annual_turnover_pct(result_valid.trade_log, n_valid)

    # 保存产物
    out = RESULTS_DIR / label
    out.mkdir(parents=True, exist_ok=True)
    weights_panel.to_parquet(out / "weights_optimized.parquet")
    pd.Series(result_valid.metrics, name="value").to_frame().to_parquet(
        out / "backtest_metrics_valid.parquet"
    )
    pd.Series(result_train.metrics, name="value").to_frame().to_parquet(
        out / "backtest_metrics_train.parquet"
    )
    nav_valid = pd.DataFrame({
        "strategy":  result_valid.nav,
        "benchmark": result_valid.benchmark_nav,
    }).dropna()
    nav_valid.index.name = "trade_date"
    nav_valid.to_parquet(out / "backtest_nav_valid.parquet")

    m = result_valid.metrics
    log.info(
        "%s: IR=%.3f  超额=%.2f%%  MDD=%.2f%%  换手(验证)=%.0f%%",
        label,
        m["information_ratio"],
        m["excess_return"] * 100,
        abs(m["excess_max_drawdown"]) * 100,
        to_valid,
    )

    return {
        "metrics_train": result_train.metrics,
        "metrics_valid": result_valid.metrics,
        "to_train":      to_train,
        "to_valid":      to_valid,
    }


# ---------------------------------------------------------------------------
# 因子系数时变稳定性
# ---------------------------------------------------------------------------

def coef_stability(coef_history: pd.DataFrame | None) -> dict[str, float]:
    """每个因子系数的时序标准差（越大 = β 随时间变化越剧烈）。"""
    if coef_history is None or coef_history.empty:
        return {}
    return coef_history.std(ddof=1).to_dict()


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def _fmt(v: float | None, spec: str = ".4f") -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    return format(v, spec)


def _pct(v: float | None, sign: bool = False) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    spec = f"{'+' if sign else ''}.2f"
    return f"{v * 100:{spec}}%"


def _check(cond: bool | None) -> str:
    if cond is None:
        return "—"
    return "✅" if cond else "❌"


def generate_report(
    ic_stats_all: dict[str, dict],   # label → {train: dict, valid: dict}
    bt_results: dict[str, dict],     # label → {metrics_valid, to_valid, …}
    coef_stds: dict[str, dict],      # label → {factor: std}
    factor_names: list[str],
    gen_time: str,
) -> str:
    labels_rolling = [_label(w) for w in WINDOW_VARIANTS]
    labels_all     = ["expanding"] + labels_rolling

    def _m(label: str, key: str) -> float:
        return float(bt_results[label]["metrics_valid"].get(key, float("nan")))

    # ------------------------------------------------------------------
    # 1. IC 信号层对比表
    # ------------------------------------------------------------------
    ic_header = "| 指标 | expanding | " + " | ".join(labels_rolling) + " |"
    ic_sep    = "|------|" + "---:|" * len(labels_all)

    def ic_row(period: str, stat: str, label_text: str) -> str:
        vals = []
        for lbl in labels_all:
            v = ic_stats_all[lbl][period].get(stat, float("nan"))
            vals.append(_fmt(v))
        return "| " + " | ".join([label_text] + vals) + " |"

    # ------------------------------------------------------------------
    # 2. 组合层对比表
    # ------------------------------------------------------------------
    bt_header = "| 指标 | expanding (B v1.1) | " + " | ".join(labels_rolling) + " | 目标 |"
    bt_sep    = "|------|" + "---:|" * len(labels_all) + "------|"

    def bt_row(label_text: str, vals: list[str], target: str = "—") -> str:
        return "| " + " | ".join([label_text] + vals + [target]) + " |"

    # ------------------------------------------------------------------
    # 3. 因子系数稳定性（验证期 β 变化）
    # ------------------------------------------------------------------
    # 用所有变体的平均系数标准差衡量"响应灵敏度"
    mean_stds: dict[str, float] = {}
    for lbl in labels_all:
        stds = coef_stds.get(lbl, {})
        mean_stds[lbl] = float(np.mean(list(stds.values()))) if stds else float("nan")

    # ------------------------------------------------------------------
    # 结论
    # ------------------------------------------------------------------
    best_ir_label = max(labels_all, key=lambda l: bt_results[l]["metrics_valid"].get("information_ratio", float("-inf")))
    best_ir_val   = _m(best_ir_label, "information_ratio")
    expand_ir     = _m("expanding", "information_ratio")

    conclusions = []
    for lbl in labels_rolling:
        ir   = _m(lbl, "information_ratio")
        diff = ir - expand_ir
        if np.isnan(ir):
            conclusions.append(f"- {lbl}: 回测结果缺失")
        elif diff > 0.02:
            conclusions.append(
                f"- ✅ {lbl}: IR={ir:.3f}，相比 expanding 提升 {diff:+.3f}，"
                "建议作为新基准"
            )
        elif diff > 0:
            conclusions.append(
                f"- ⚠️ {lbl}: IR={ir:.3f}，相比 expanding 提升 {diff:+.3f}（幅度有限）"
            )
        else:
            conclusions.append(
                f"- ❌ {lbl}: IR={ir:.3f}，相比 expanding 下降 {diff:.3f}"
            )

    if best_ir_val >= 0.5:
        conclusions.append(
            f"- **最优变体 {best_ir_label}**: IR={best_ir_val:.3f} ≥ 0.5 ✅ 通过目标"
        )
    else:
        conclusions.append(
            f"- **最优变体 {best_ir_label}**: IR={best_ir_val:.3f} < 0.5 ❌ "
            f"仍需提升 {0.5 - best_ir_val:.3f}"
        )

    report = f"""\
# Pipeline E — Rolling-Window Ridge 对比实验报告

> 生成时间：{gen_time}
> 分支：feature/expand-train-2012
> 实验目录：`experiments/ridge_rolling/`
> Pipeline B 基准（expanding）：IR=0.483，直接复用已有产物

---

## 1. 实验设计

| 参数 | 值 |
|------|---|
| 信号方法 | Walk-forward Ridge，超额收益目标（与 Pipeline B 完全一致）|
| α 候选 | {', '.join(str(a) for a in ALPHA_CANDIDATES)} |
| CV 折叠 | 5 折（2015-2019），每折 1 年 |
| Purge 月数 | 2 个月 |
| 对比变体 | expanding（全历史）/ rolling-36m / rolling-48m / rolling-60m |
| 优化器参数 | TE=6%，λ={LAM_BEST:.3f}，IND_DEV=3%，SGL_DEV=1.5% |
| 训练期 | {cfg.TRAIN_START.date()} ~ {cfg.TRAIN_END.date()} |
| 验证期 | {cfg.VALID_START.date()} ~ {cfg.VALID_END.date()} |

---

## 2. 信号层对比（IC / IC_IR）

> IC = Spearman rank corr(signal, fwd_ret)；IC_IR = IC均值 / IC标准差

### 2.1 训练期

{ic_header}
{ic_sep}
{ic_row("train", "ic_mean",  "IC 均值")}
{ic_row("train", "ic_std",   "IC 标准差")}
{ic_row("train", "ic_ir",    "IC_IR")}
{ic_row("train", "win_rate", "IC 胜率")}
{ic_row("train", "t_stat",   "t 统计量")}
{ic_row("train", "n",        "有效日期数")}

### 2.2 验证期（样本外）

{ic_header}
{ic_sep}
{ic_row("valid", "ic_mean",  "IC 均值")}
{ic_row("valid", "ic_std",   "IC 标准差")}
{ic_row("valid", "ic_ir",    "IC_IR")}
{ic_row("valid", "win_rate", "IC 胜率")}
{ic_row("valid", "t_stat",   "t 统计量")}
{ic_row("valid", "n",        "有效日期数")}

---

## 3. 组合层对比（IR / 超额收益 / 回撤）

> expanding 基准直接加载 Pipeline B（λ=0.005）的已有产物
> rolling 变体均用 λ={LAM_BEST:.3f} 重新跑优化器

{bt_header}
{bt_sep}
""" + bt_row(
        "信息比率 IR",
        [f"{_fmt(_m(l, 'information_ratio'))} "
         f"{_check(not np.isnan(_m(l, 'information_ratio')) and _m(l, 'information_ratio') > expand_ir)}"
         for l in labels_all],
        "越高越好",
    ) + "\n" + bt_row(
        "年化超额收益",
        [_pct(_m(l, "excess_return"), sign=True) for l in labels_all],
        "—",
    ) + "\n" + bt_row(
        "超额最大回撤",
        [f"{_pct(abs(_m(l, 'excess_max_drawdown')))} "
         f"{_check(not np.isnan(_m(l, 'excess_max_drawdown')) and abs(_m(l, 'excess_max_drawdown')) <= 0.10)}"
         for l in labels_all],
        "≤ 10%",
    ) + "\n" + bt_row(
        "跟踪误差（实现值）",
        [_pct(_m(l, "tracking_error")) for l in labels_all],
        "—",
    ) + "\n" + bt_row(
        "月度胜率",
        [_pct(_m(l, "monthly_win_rate")) for l in labels_all],
        "—",
    ) + "\n" + bt_row(
        "验证期年化换手",
        [f"{bt_results[l]['to_valid']:.0f}%" if not np.isnan(bt_results[l]["to_valid"]) else "N/A"
         for l in labels_all],
        "500-1500%",
    ) + f"""

---

## 4. 因子系数时变灵敏度

> β 标准差越大 = 权重对市场 regime 变化越敏感；expanding 窗口越大越稳定

| 变体 | 平均 β 标准差 | 相对 expanding |
|------|------------|--------------|
""" + "\n".join(
        f"| {lbl} | {_fmt(mean_stds.get(lbl, float('nan')))} | "
        f"{_fmt(mean_stds.get(lbl, float('nan')) - mean_stds.get('expanding', float('nan')), '+.4f')} |"
        for lbl in labels_all
    ) + f"""

---

## 5. 结论与建议

{chr(10).join(conclusions)}

- **下一步**：{"若最优 rolling 变体 IR > expanding，可将其信号文件替换 Pipeline B 的 B-1 输出，重新跑完整 lambda 网格实验确认最优 λ。" if best_ir_val > expand_ir else "rolling 窗口在当前设置下未能超越 expanding 基准，可考虑：(1) 指数衰减加权替代硬截断；(2) 扩大 α 候选范围；(3) 检查 β 时变图确认 regime 响应是否如预期改善。"}

---

*本报告由 `run_rolling_experiment.py` 自动生成，对比基于验证期 2021-01 ~ 2022-12。*
"""
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    gen_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── 1. 加载公共数据 ─────────────────────────────────────────────────
    log.info("=== 加载公共数据 ===")
    factor_names  = load_final_factors()
    factor_panels = load_factor_panels(factor_names)
    fwd_ret_panel = load_fwd_ret()
    all_dates     = fwd_ret_panel.index

    index_member  = pd.read_parquet(INDEX_MEMBER_PATH)
    bench_ret_series = compute_bench_ret_series(fwd_ret_panel, index_member)

    # ── 2. 加载 expanding 基准信号（IC 计算用）───────────────────────────
    log.info("=== 加载 expanding 基准信号 ===")
    if not EXPANDING_PANEL_PATH.exists():
        raise FileNotFoundError(
            f"Pipeline B 信号不存在: {EXPANDING_PANEL_PATH}\n"
            "请先运行: python -m experiments.ridge_signal.run_ridge_experiment"
        )
    expanding_panel = pd.read_parquet(EXPANDING_PANEL_PATH)
    expanding_coef  = (
        pd.read_parquet(EXPANDING_COEF_PATH)
        if EXPANDING_COEF_PATH.exists() else None
    )
    log.info("expanding 信号: %s  %s ~ %s",
             expanding_panel.shape,
             expanding_panel.index[0].date(),
             expanding_panel.index[-1].date())

    # ── 3. 构建滚动窗口信号 ──────────────────────────────────────────────
    rolling_panels: dict[int, pd.DataFrame]          = {}
    rolling_coefs:  dict[int, pd.DataFrame | None]   = {}
    rolling_alphas: dict[int, float]                 = {}

    for window in WINDOW_VARIANTS:
        panel, coef, alpha = build_rolling_signal(
            window, factor_names, factor_panels,
            fwd_ret_panel, bench_ret_series, all_dates,
        )
        rolling_panels[window] = panel
        rolling_coefs[window]  = coef
        rolling_alphas[window] = alpha

    # ── 4. 计算所有变体的 IC 序列与统计 ─────────────────────────────────
    log.info("=== 计算 IC 序列 ===")

    def ic_stats_pair(panel: pd.DataFrame) -> dict[str, dict]:
        ic_all = compute_ic_series(panel, fwd_ret_panel)
        tr, vl = split_ic_by_period(ic_all)
        return {
            "train": compute_ic_stats(tr, "train"),
            "valid": compute_ic_stats(vl, "valid"),
        }

    ic_stats_all: dict[str, dict] = {
        "expanding": ic_stats_pair(expanding_panel),
        **{_label(w): ic_stats_pair(rolling_panels[w]) for w in WINDOW_VARIANTS},
    }

    # 保存 IC 对比 CSV
    ic_rows = []
    for lbl, stats in ic_stats_all.items():
        for period, s in stats.items():
            ic_rows.append({"variant": lbl, "period": period, **s})
    pd.DataFrame(ic_rows).to_csv(RESULTS_DIR / "ic_stats_summary.csv", index=False)

    # 打印 IC 摘要
    log.info("--- IC 统计摘要 ---")
    for lbl in ["expanding"] + [_label(w) for w in WINDOW_VARIANTS]:
        for period in ("train", "valid"):
            s = ic_stats_all[lbl][period]
            log.info(
                "  %-20s [%s]  IC=%.4f  IC_IR=%.4f  t=%.2f  p=%.3f",
                lbl, period,
                s.get("ic_mean", float("nan")),
                s.get("ic_ir",   float("nan")),
                s.get("t_stat",  float("nan")),
                s.get("p_value", float("nan")),
            )

    # ── 5. 调仓日预处理（一次性，所有变体共用）─────────────────────────
    log.info("=== 调仓日预处理 ===")
    industry_raw   = pd.read_parquet(cfg.DATA_PROC / "industry.parquet")
    industry_pivot = (
        industry_raw["industry_code"]
        .unstack(level="ts_code")
        .sort_index()
        .ffill()
    )
    (
        available_dates, bw_dict, halt_dict, limit_up_dict,
        limit_dn_dict, industry_dict, codes_map, cov_cache,
    ) = build_period_data(index_member, industry_pivot)

    # ── 6. 加载 expanding 基准的回测指标（直接复用 Pipeline B）───────────
    log.info("=== 加载 expanding 基准回测指标 ===")
    if not EXPANDING_METRICS_PATH.exists():
        raise FileNotFoundError(
            f"Pipeline B 回测指标不存在: {EXPANDING_METRICS_PATH}\n"
            "请先完成 Pipeline B（run_turnover_lambda_grid --lam 0.005）"
        )
    expanding_metrics = pd.read_parquet(EXPANDING_METRICS_PATH)["value"].to_dict()
    expanding_to_summary = (
        pd.read_parquet(EXPANDING_TO_PATH)["value"].to_dict()
        if EXPANDING_TO_PATH.exists() else {}
    )
    expanding_to_valid = expanding_to_summary.get("to_valid_pct", float("nan"))

    bt_results: dict[str, dict] = {
        "expanding": {
            "metrics_valid": expanding_metrics,
            "metrics_train": {},
            "to_train":      expanding_to_summary.get("to_train_pct", float("nan")),
            "to_valid":      expanding_to_valid,
        }
    }

    # ── 7. Rolling 变体：优化器 + 回测 ──────────────────────────────────
    for window in WINDOW_VARIANTS:
        lbl = _label(window)
        bt_results[lbl] = run_optimizer_backtest(
            lbl, rolling_panels[window],
            available_dates, bw_dict, halt_dict, limit_up_dict,
            limit_dn_dict, industry_dict, codes_map, cov_cache,
        )

    # ── 8. 因子系数稳定性 ────────────────────────────────────────────────
    coef_stds: dict[str, dict] = {
        "expanding": coef_stability(expanding_coef),
        **{_label(w): coef_stability(rolling_coefs[w]) for w in WINDOW_VARIANTS},
    }

    # ── 9. 生成对比报告 ──────────────────────────────────────────────────
    log.info("=== 生成对比报告 ===")
    report = generate_report(
        ic_stats_all, bt_results, coef_stds, factor_names, gen_time,
    )
    report_path = RESULTS_DIR / "comparison_report.md"
    report_path.write_text(report, encoding="utf-8")
    log.info("报告写入: %s", report_path)

    # 最终摘要打印
    log.info("=" * 60)
    log.info("实验完成。各变体验证期 IR：")
    for lbl in ["expanding"] + [_label(w) for w in WINDOW_VARIANTS]:
        ir = bt_results[lbl]["metrics_valid"].get("information_ratio", float("nan"))
        log.info("  %-20s  IR=%.3f", lbl, ir)
    log.info("结果目录: %s", RESULTS_DIR)
    log.info("=" * 60)


def coef_stability(coef_history: pd.DataFrame | None) -> dict[str, float]:
    if coef_history is None or coef_history.empty:
        return {}
    return coef_history.std(ddof=1).to_dict()


if __name__ == "__main__":
    main()
