"""
experiments/turnover_lambda_grid/run_turnover_lambda_grid.py
=============================================================

换手成本惩罚系数（λ_TC）网格实验：固定 Ridge v2 信号和 TE=6%，
对比不同 λ_TC 对换手率、净超额收益、IR 的影响。

唯一变量：turnover_lambda（λ_TC）。
其余参数（TE=6%, IND_DEV=3%, SGL_DEV=1.5%）均与 TE 网格获胜配置一致。
全部产物写入 experiments/turnover_lambda_grid/results/lam_{xxxx}/，
零修改主管线、Ridge 实验、TE 网格任何文件（只读访问）。

用法：
    python -m experiments.turnover_lambda_grid.run_turnover_lambda_grid
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

# 固定 TE=6%（来自 TE 网格获胜配置）
TE_FIXED        = 0.06
IND_DEV         = cfg.OPT_INDUSTRY_MAX_DEV   # 3%
SGL_DEV         = cfg.OPT_SINGLE_MAX_DEV     # 1.5%
TOPN            = cfg.OPT_TOPN               # 50

# λ_TC 候选：0 = 基线（无惩罚）；值越大换手惩罚越重
LAMBDA_CANDIDATES: list[float] = [0.000, 0.002, 0.005, 0.010, 0.020]

RIDGE_SIGNAL_PATH = (
    Path(__file__).parent.parent / "ridge_signal" / "results" / "ridge_composite_panel.parquet"
)
COV_CACHE_DIR = cfg.DATA_PROC / "cov_cache"          # 只读，复用已有缓存
RESULTS_DIR   = Path(__file__).parent / "results"
COMPARISON_REPORT = RESULTS_DIR / "comparison_report.md"


def _lam_label(lam: float) -> str:
    """λ 值转文件名标签，如 0.005 → 'lam_0050'。"""
    return f"lam_{int(round(lam * 10000)):04d}"


def _lam_dir(lam: float) -> Path:
    d = RESULTS_DIR / _lam_label(lam)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lam_display(lam: float) -> str:
    """用于报告表头，如 'λ=0.000'。"""
    return f"λ={lam:.3f}"


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
    log.info(
        "辅助数据加载完成: index_member=%d 期",
        index_member.index.get_level_values("rebalance_date").nunique(),
    )
    return ridge_signal, index_member, industry_pivot


# ---------------------------------------------------------------------------
# Step 2: 一次性预处理（与 λ 无关）
# ---------------------------------------------------------------------------

def build_period_data(
    ridge_signal: pd.DataFrame,
    index_member: pd.DataFrame,
    industry_pivot: pd.DataFrame,
) -> tuple:
    """
    预处理每个调仓日的成分股快照、行业映射、停牌/涨跌停标记、协方差缓存。
    与 λ_TC 无关，只需执行一次，所有 λ 共用。

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
    """复用 cov_cache/（只读）。cache miss 用 eye*1e-4，与主管线行为一致。"""
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
# Step 3: 单个 λ 的优化主循环
# ---------------------------------------------------------------------------

def build_weights_for_lambda(
    lam: float,
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
    用指定 λ_TC 运行组合优化（训练期 + 验证期，共 ~144 期）。

    Args:
        lam: 换手成本惩罚系数（小数，如 0.005）
    Returns:
        (weights_panel, baseline_panel, meta_df)
    """
    log.info("=== λ=%.3f 优化开始 ===", lam)

    opt_config = OptimizeConfig(
        te_target_annual  = TE_FIXED,
        industry_max_dev  = IND_DEV,
        single_max_dev    = SGL_DEV,
        topn              = TOPN,
        turnover_lambda   = lam,
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
        cov_available  = T in cov_cache
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
            effective_fb = 1

        opt_weights[T] = result.weights
        w_prev         = result.weights

        # TopN 等权基线（V1，λ 无关，顺带构造）
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
            log.info(
                "λ=%.3f  进度 %d/%d  用时=%.1fs",
                lam, i + 1, len(available_dates), time.perf_counter() - t0,
            )

    log.info(
        "λ=%.3f 完成: %d 期  用时=%.1fs  Fallback: L1=%d L2=%d L3=%d",
        lam, len(available_dates), time.perf_counter() - t0,
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
# Step 4: 回测 + 归因
# ---------------------------------------------------------------------------

def _annual_turnover_pct(trade_log: pd.DataFrame, n_months: int) -> float:
    if trade_log.empty or n_months == 0:
        return float("nan")
    total_to = (
        (trade_log["buy_value"] + trade_log["sell_value"])
        / trade_log["portfolio_value_before"]
    ).sum()
    return total_to / n_months * 12 * 100


def run_backtest_for_lambda(
    lam: float,
    weights_panel: pd.DataFrame,
    baseline_panel: pd.DataFrame,
) -> tuple:
    """
    训练期 + 验证期回测，以及验证期 Brinson 归因。

    Returns:
        (result_train, result_v1, result_v2, to_train, to_v1, to_v2, brinson_summary)
        result_train: 训练期 V2 回测（用于观察训练期换手和 IR）
        result_v1:    验证期 TopN 等权
        result_v2:    验证期优化权重
    """
    log.info("=== λ=%.3f 回测 ===", lam)
    bt_config = BacktestConfig(initial_value=1.0)

    # 训练期（用于观察 λ 对换手的实际压缩效果）
    result_train = run_backtest(weights_panel, cfg.TRAIN_START, cfg.TRAIN_END, bt_config)
    n_train = len(result_train.trade_log) if not result_train.trade_log.empty else 0
    to_train = _annual_turnover_pct(result_train.trade_log, n_train)

    # 验证期 V1 / V2
    result_v1 = run_backtest(baseline_panel, cfg.VALID_START, cfg.VALID_END, bt_config)
    result_v2 = run_backtest(weights_panel,  cfg.VALID_START, cfg.VALID_END, bt_config)
    n_valid   = len(result_v2.trade_log) if not result_v2.trade_log.empty else 0
    to_v1     = _annual_turnover_pct(result_v1.trade_log, n_valid)
    to_v2     = _annual_turnover_pct(result_v2.trade_log, n_valid)

    m = result_v2.metrics
    log.info(
        "λ=%.3f  验证期 IR=%.3f  超额=%.2f%%  MDD=%.2f%%  换手(验证)=%.0f%%  换手(训练)=%.0f%%",
        lam,
        m["information_ratio"],
        m["excess_return"] * 100,
        abs(m["excess_max_drawdown"]) * 100,
        to_v2,
        to_train,
    )

    brinson_summary = pd.DataFrame()
    try:
        _, brinson_summary = compute_brinson_attribution(
            result_v2.actual_weights, cfg.VALID_START, cfg.VALID_END
        )
        log.info("λ=%.3f Brinson 完成: %d 期", lam, len(brinson_summary))
    except Exception as exc:
        log.error("λ=%.3f Brinson 归因失败: %s", lam, exc)

    return result_train, result_v1, result_v2, to_train, to_v1, to_v2, brinson_summary


# ---------------------------------------------------------------------------
# Step 5: 保存单个 λ 的产物
# ---------------------------------------------------------------------------

def save_lambda_results(
    lam: float,
    weights_panel: pd.DataFrame,
    baseline_panel: pd.DataFrame,
    meta_df: pd.DataFrame,
    result_train,
    result_v2,
    to_train: float,
    to_v2: float,
    brinson_summary: pd.DataFrame,
) -> None:
    out = _lam_dir(lam)

    weights_panel.to_parquet(out / "weights_optimized.parquet")
    baseline_panel.to_parquet(out / "weights_baseline.parquet")
    meta_df.to_parquet(out / "weights_meta.parquet")

    # 验证期指标
    pd.Series(result_v2.metrics, name="value").to_frame().to_parquet(
        out / "backtest_metrics_valid.parquet"
    )
    # 训练期指标
    pd.Series(result_train.metrics, name="value").to_frame().to_parquet(
        out / "backtest_metrics_train.parquet"
    )

    nav_df = pd.DataFrame({
        "strategy":  result_v2.nav,
        "benchmark": result_v2.benchmark_nav,
    }).dropna()
    nav_df.index.name = "trade_date"
    nav_df.to_parquet(out / "backtest_nav_valid.parquet")

    nav_train_df = pd.DataFrame({
        "strategy":  result_train.nav,
        "benchmark": result_train.benchmark_nav,
    }).dropna()
    nav_train_df.index.name = "trade_date"
    nav_train_df.to_parquet(out / "backtest_nav_train.parquet")

    if not result_v2.actual_weights.empty:
        result_v2.actual_weights.to_parquet(out / "actual_weights_v2.parquet")
    if not result_v2.trade_log.empty:
        result_v2.trade_log.to_parquet(out / "trades_v2.parquet")
    if not brinson_summary.empty:
        brinson_summary.to_parquet(out / "brinson_period_summary.parquet")

    # 附加换手元数据
    pd.Series({"to_train_pct": to_train, "to_valid_pct": to_v2}).to_frame(
        name="value"
    ).to_parquet(out / "turnover_summary.parquet")

    log.info("λ=%.3f 产物写入: %s", lam, out)


# ---------------------------------------------------------------------------
# Step 6: 对比报告
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


def generate_comparison_report(all_results: dict, gen_time: str) -> str:
    """多列（各 λ 值）Markdown 对比报告。"""

    def _m(lam: float, key: str, period: str = "valid") -> float:
        metrics_key = f"metrics_{period}"
        return float(all_results[lam][metrics_key].get(key, float("nan")))

    def _b(lam: float, col: str) -> float:
        bs = all_results[lam]["brinson"]
        if bs.empty or col not in bs.columns:
            return float("nan")
        return float(bs[col].sum())

    def _fb(lam: float) -> str:
        meta   = all_results[lam]["meta"]
        v_meta = meta.loc[meta.index >= cfg.VALID_START]
        if v_meta.empty:
            return "N/A"
        l1    = int((v_meta["fallback_level"] == 0).sum())
        total = len(v_meta)
        return f"{l1}/{total}"

    cols   = [_lam_display(lam) for lam in LAMBDA_CANDIDATES]
    header = "| 指标 | " + " | ".join(cols) + " | 目标 |"
    sep    = "|------|" + "---:|" * len(LAMBDA_CANDIDATES) + "------|"

    def table_row(label: str, vals: list[str], target: str = "—") -> str:
        return "| " + " | ".join([label] + vals + [target]) + " |"

    lines = [
        "# λ_TC 换手成本惩罚系数网格实验对比报告",
        "",
        f"> 生成时间：{gen_time}",
        f"> 信号：Ridge v2（超额收益目标，alpha=5000）",
        f"> 固定参数：TE={TE_FIXED:.0%}，IND\\_DEV={IND_DEV:.0%}，SGL\\_DEV={SGL_DEV:.1%}，TOPN={TOPN}",
        f"> 实验目录：`experiments/turnover_lambda_grid/results/`",
        f"> 训练期：{cfg.TRAIN_START.date()} ~ {cfg.TRAIN_END.date()}",
        f"> 验证期：{cfg.VALID_START.date()} ~ {cfg.VALID_END.date()}",
        "",
        "---",
        "",
        "## 1. 训练期换手对比（λ 压缩效果校验）",
        "",
        "| λ_TC | 训练期年化双边换手 | 训练期 IR | 目标换手 |",
        "|------|---:|---:|------|",
    ]

    for lam in LAMBDA_CANDIDATES:
        to_tr = all_results[lam]["to_train"]
        ir_tr = _m(lam, "information_ratio", "train")
        in_t  = "✅" if not np.isnan(to_tr) and 500 <= to_tr <= 1500 else "❌"
        lines.append(
            f"| {_lam_display(lam)} | {to_tr:.0f}% {in_t} | {_fmt(ir_tr)} | 500-1500% |"
        )

    lines += [
        "",
        "---",
        "",
        "## 2. 验证期核心指标（V2 优化组合）",
        "",
        header, sep,
    ]

    metric_rows = [
        (
            "年化超额收益",
            [_pct(_m(lam, "excess_return"), sign=True) for lam in LAMBDA_CANDIDATES],
            "—",
        ),
        (
            "信息比率 IR",
            [
                f"{_fmt(_m(lam, 'information_ratio'))} "
                f"{_check(not np.isnan(_m(lam, 'information_ratio')) and _m(lam, 'information_ratio') >= 0.5)}"
                for lam in LAMBDA_CANDIDATES
            ],
            "≥ 0.5",
        ),
        (
            "超额最大回撤",
            [
                f"{_pct(abs(_m(lam, 'excess_max_drawdown')))} "
                f"{_check(not np.isnan(_m(lam, 'excess_max_drawdown')) and abs(_m(lam, 'excess_max_drawdown')) <= 0.10)}"
                for lam in LAMBDA_CANDIDATES
            ],
            "≤ 10%",
        ),
        (
            "跟踪误差（实现值）",
            [_pct(_m(lam, "tracking_error")) for lam in LAMBDA_CANDIDATES],
            "—",
        ),
        (
            "月度胜率",
            [_pct(_m(lam, "monthly_win_rate")) for lam in LAMBDA_CANDIDATES],
            "—",
        ),
        (
            "验证期年化双边换手",
            [
                f"{all_results[lam]['to_valid']:.0f}% "
                f"{_check(not np.isnan(all_results[lam]['to_valid']) and 500 <= all_results[lam]['to_valid'] <= 1500)}"
                for lam in LAMBDA_CANDIDATES
            ],
            "500-1500%",
        ),
        (
            "绝对收益（策略）",
            [_pct(_m(lam, "annualized_return"), sign=True) for lam in LAMBDA_CANDIDATES],
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
        "## 3. Brinson BHB 归因对比（V2，验证期累计）",
        "",
        "| 效应 | " + " | ".join(cols) + " |",
        "|------|" + "---:|" * len(LAMBDA_CANDIDATES),
    ]
    for label, col in [
        ("总超额收益", "excess_return"),
        ("配置效应",   "allocation_effect"),
        ("选股效应",   "selection_effect"),
        ("交叉效应",   "interaction_effect"),
    ]:
        vals = [_pct(_b(lam, col), sign=True) for lam in LAMBDA_CANDIDATES]
        lines.append("| " + " | ".join([label] + vals) + " |")

    # Fallback
    lines += [
        "",
        "---",
        "",
        "## 4. 优化器 Fallback（验证期 L1 严格解 / 总期数）",
        "",
        "| | " + " | ".join(cols) + " |",
        "|---|" + "---:|" * len(LAMBDA_CANDIDATES),
        "| L1（严格 QP 解）| " + " | ".join(_fb(lam) for lam in LAMBDA_CANDIDATES) + " |",
    ]

    # 结论
    ir_list  = [_m(lam, "information_ratio") for lam in LAMBDA_CANDIDATES]
    to_list  = [all_results[lam]["to_valid"] for lam in LAMBDA_CANDIDATES]
    mdd_list = [abs(_m(lam, "excess_max_drawdown")) for lam in LAMBDA_CANDIDATES]

    # 推荐：在 500-1500% 换手目标区间内 IR 最高的 λ；都不在区间则选最接近的
    in_target = [
        (i, lam) for i, lam in enumerate(LAMBDA_CANDIDATES)
        if not np.isnan(to_list[i]) and 500 <= to_list[i] <= 1500
    ]
    if in_target:
        best_idx, best_lam = max(in_target, key=lambda x: ir_list[x[0]])
    else:
        best_idx = int(np.nanargmax(ir_list))
        best_lam = LAMBDA_CANDIDATES[best_idx]

    best_ir  = ir_list[best_idx]
    base_ir  = ir_list[0]
    best_to  = to_list[best_idx]
    best_mdd = mdd_list[best_idx]

    ir_trend = " → ".join(f"{ir:.3f}" for ir in ir_list)

    lines += [
        "",
        "---",
        "",
        "## 5. 结论与决策",
        "",
        f"- **推荐 λ_TC**：{best_lam:.3f}（IR={best_ir:.3f}，换手={best_to:.0f}%，MDD={best_mdd:.1%}）",
        f"- IR 相比 λ=0.000 基线变化：{best_ir - base_ir:+.3f}",
    ]

    if best_ir >= 0.5:
        lines.append(f"- IR={best_ir:.3f} ≥ 0.5 ✅ 通过验证期目标")
    else:
        lines.append(f"- IR={best_ir:.3f} < 0.5 ❌ 仍未达标，差距 {0.5 - best_ir:.3f}")

    if best_mdd <= 0.10:
        lines.append(f"- 超额最大回撤 {best_mdd:.1%} ≤ 10% ✅")
    else:
        lines.append(f"- ⚠️ 超额最大回撤 {best_mdd:.1%} > 10% ❌")

    ir_lambda_pairs = " → ".join(
        f"λ={lam:.3f}:{ir:.3f}" for lam, ir in zip(LAMBDA_CANDIDATES, ir_list)
    )
    lines.append(f"- IR 随 λ 变化趋势：{ir_lambda_pairs}")
    lines.append(f"- 换手随 λ 变化趋势（验证期）：" +
                 " → ".join(f"{to:.0f}%" for to in to_list))

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    gen_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    log.info("=" * 60)
    log.info("λ_TC 换手惩罚网格实验  候选: %s", LAMBDA_CANDIDATES)
    log.info("固定: TE=%.0f%%  IND_DEV=%.0f%%  SGL_DEV=%.1f%%",
             TE_FIXED * 100, IND_DEV * 100, SGL_DEV * 100)
    log.info("=" * 60)

    # 一次性加载所有数据
    ridge_signal, index_member, industry_pivot = load_data()
    (
        available_dates, benchmark_weights_dict, halt_dict,
        limit_up_dict, limit_dn_dict, industry_dict, codes_map, cov_cache,
    ) = build_period_data(ridge_signal, index_member, industry_pivot)

    all_results: dict = {}

    for lam in LAMBDA_CANDIDATES:
        # 优化
        weights_panel, baseline_panel, meta_df = build_weights_for_lambda(
            lam, ridge_signal, available_dates,
            benchmark_weights_dict, halt_dict, limit_up_dict, limit_dn_dict,
            industry_dict, codes_map, cov_cache,
        )

        # 回测 + 归因
        result_train, result_v1, result_v2, to_train, to_v1, to_v2, brinson_summary = (
            run_backtest_for_lambda(lam, weights_panel, baseline_panel)
        )

        # 保存产物
        save_lambda_results(
            lam, weights_panel, baseline_panel, meta_df,
            result_train, result_v2, to_train, to_v2, brinson_summary,
        )

        all_results[lam] = {
            "meta":          meta_df,
            "metrics_valid": result_v2.metrics,
            "metrics_train": result_train.metrics,
            "brinson":       brinson_summary,
            "to_train":      to_train,
            "to_valid":      to_v2,
        }

        log.info("λ=%.3f 完成 ✓\n", lam)

    # 生成对比报告
    report = generate_comparison_report(all_results, gen_time)
    COMPARISON_REPORT.write_text(report, encoding="utf-8")
    log.info("对比报告写入: %s", COMPARISON_REPORT)

    log.info("=" * 60)
    log.info("全部 λ 实验完成！结果目录: %s", RESULTS_DIR)
    log.info("=" * 60)

    print(report)


if __name__ == "__main__":
    main()
