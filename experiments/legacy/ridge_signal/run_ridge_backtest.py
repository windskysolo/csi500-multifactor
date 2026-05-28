"""
experiments/ridge_signal/run_ridge_backtest.py
==============================================

平行回测：用 Ridge 信号替换 IC_IR 信号，跑完整优化 → 回测 → Brinson 归因流水线，
与主管线结果进行对比。

完全不修改任何主管线文件：
  - 读取：data/processed/（只读：cov_cache、index_member、industry、backtest_metrics）
  - 写入：experiments/ridge_signal/results/backtest/（隔离目录）

流程：
  1. 加载 Ridge 信号（experiments/ridge_signal/results/ridge_composite_panel.parquet）
  2. 读取协方差缓存（data/processed/cov_cache/，复用，不重新估计）
  3. 运行组合优化（同主管线参数，仅信号源不同）
  4. 运行验证期回测（VALID_START ~ VALID_END）
  5. 运行 Brinson BHB 归因
  6. 加载主管线指标进行 A/B 对比
  7. 生成 comparison_backtest_report.md

用法：
    python -m experiments.ridge_signal.run_ridge_backtest
"""

import json
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
from src.attribution.brinson import compute_brinson_attribution
from src.backtest.engine import BacktestConfig, run_backtest
from src.backtest.metrics import summarize
from src.portfolio.covariance import validate_and_repair_covariance
from src.portfolio.optimizer import OptimizeConfig, optimize_single_period

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 路径（只读：主管线产物；读写：实验隔离目录）
# ---------------------------------------------------------------------------

RIDGE_SIGNAL_PATH    = Path(__file__).parent / "results" / "ridge_composite_panel.parquet"
COV_CACHE_DIR        = cfg.DATA_PROC / "cov_cache"             # 复用已有缓存，只读
MAIN_METRICS_PATH    = cfg.DATA_PROC / "backtest_metrics.parquet"
MAIN_BRINSON_PATH    = cfg.DATA_PROC / "brinson_period_summary.parquet"

BACKTEST_DIR = Path(__file__).parent / "results" / "backtest"

# 实验产物（写入隔离目录）
RIDGE_WEIGHTS_OPT  = BACKTEST_DIR / "ridge_weights_optimized.parquet"
RIDGE_WEIGHTS_BL   = BACKTEST_DIR / "ridge_weights_baseline.parquet"
RIDGE_WEIGHTS_META = BACKTEST_DIR / "ridge_weights_meta.parquet"
RIDGE_NAV_PATH     = BACKTEST_DIR / "ridge_backtest_nav.parquet"
RIDGE_METRICS_PATH = BACKTEST_DIR / "ridge_backtest_metrics.parquet"
RIDGE_TRADES_V1    = BACKTEST_DIR / "ridge_trades_v1.parquet"
RIDGE_TRADES_V2    = BACKTEST_DIR / "ridge_trades_v2.parquet"
RIDGE_WEIGHTS_V1   = BACKTEST_DIR / "ridge_actual_weights_v1.parquet"
RIDGE_WEIGHTS_V2   = BACKTEST_DIR / "ridge_actual_weights_v2.parquet"
RIDGE_BRINSON      = BACKTEST_DIR / "ridge_brinson_attribution.parquet"
RIDGE_BRINSON_SUM  = BACKTEST_DIR / "ridge_brinson_period_summary.parquet"
COMPARISON_REPORT  = BACKTEST_DIR / "comparison_backtest_report.md"

# ---------------------------------------------------------------------------
# 优化参数（与主管线完全一致）
# ---------------------------------------------------------------------------

OPT_CONFIG = OptimizeConfig(
    te_target_annual  = cfg.OPT_TE_TARGET_ANNUAL,
    industry_max_dev  = cfg.OPT_INDUSTRY_MAX_DEV,
    single_max_dev    = cfg.OPT_SINGLE_MAX_DEV,
    topn              = cfg.OPT_TOPN,
    max_solve_seconds = 30.0,
)
BASELINE_N = cfg.OPT_TOPN


# ---------------------------------------------------------------------------
# Step 1: 数据加载
# ---------------------------------------------------------------------------

def load_ridge_signal() -> pd.DataFrame:
    """加载 Ridge 合成信号面板（date × ts_code）。"""
    if not RIDGE_SIGNAL_PATH.exists():
        raise FileNotFoundError(
            f"Ridge 信号文件不存在: {RIDGE_SIGNAL_PATH}\n"
            "请先运行 run_ridge_experiment.py 生成信号。"
        )
    sig = pd.read_parquet(RIDGE_SIGNAL_PATH)
    log.info("Ridge 信号: %s  %s ~ %s", sig.shape,
             sig.index[0].date(), sig.index[-1].date())
    return sig


def load_aux_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """加载 index_member 和 industry 数据（只读）。"""
    index_member = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    industry_raw = pd.read_parquet(cfg.DATA_PROC / "industry.parquet")
    industry_pivot = (
        industry_raw["industry_code"]
        .unstack(level="ts_code")
        .sort_index()
        .ffill()
    )
    log.info("index_member: %d rebalance dates", index_member.index.get_level_values(0).nunique())
    return index_member, industry_pivot


def load_cov_cache(dates: list[pd.Timestamp], codes_map: dict) -> dict:
    """
    从已有 cov_cache/ 目录加载协方差矩阵（只读，不重新估计）。

    对 cache miss 的日期用 np.eye(n)*1e-4 代替并记录警告，与主管线 fallback 行为一致。
    """
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
                log.debug("cov cache read error at %s: %s", T.date(), exc)

        log.warning("cov cache miss at %s — 用 eye*1e-4 代替（L2 fallback）", T.date())
        cov_cache[T] = np.eye(n) * 1e-4
        n_miss += 1

    log.info("协方差缓存: 命中=%d  miss=%d", n_loaded, n_miss)
    return cov_cache


# ---------------------------------------------------------------------------
# Step 2: 组合优化
# ---------------------------------------------------------------------------

def _compute_baseline_weights(
    alpha: pd.Series,
    halt_codes: set,
    codes: list[str],
) -> pd.Series:
    """TopN 等权基准权重（排除停牌股）。"""
    avail = alpha.drop(index=list(halt_codes & set(alpha.index)), errors="ignore")
    top_sel = avail.dropna().nlargest(BASELINE_N).index
    w_bl = pd.Series(0.0, index=codes)
    if len(top_sel) > 0:
        w_bl[top_sel] = 1.0 / len(top_sel)
    return w_bl


def run_optimization(
    ridge_signal: pd.DataFrame,
    index_member: pd.DataFrame,
    industry_pivot: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    用 Ridge 信号运行组合优化（训练期 + 验证期），逻辑与 run_portfolio_optimization.py 完全一致。

    只替换信号源（ridge_signal 替代 composite_signal_ic_ir），输出写到隔离目录。
    协方差缓存复用 data/processed/cov_cache/，不重新估计。

    Returns:
        (weights_panel, baseline_panel, meta_df) 均以 rebalance_date 为 index
    """
    log.info("=== Step 2: 组合优化（训练+验证期，信号=Ridge）===")

    # 限制到训练/验证期
    tv_mask = ridge_signal.index <= cfg.VALID_END
    signal = ridge_signal.loc[tv_mask].copy()

    # 提取每个调仓日的成分股信息
    dates_in_member = index_member.index.get_level_values("rebalance_date").unique()
    all_rebalance_dates = sorted(signal.index.union(dates_in_member))

    # 只处理 index_member 中存在的日期
    available_dates = [
        d for d in sorted(
            index_member.index.get_level_values("rebalance_date").unique()
        )
        if d <= cfg.VALID_END
    ]

    # 构建逐期辅助字典
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

    # 加载协方差缓存（复用，不重新估计）
    cov_cache = load_cov_cache(available_dates, codes_map)

    # 优化主循环
    opt_weights:      dict = {}
    baseline_weights: dict = {}
    meta_rows: list[dict] = []
    w_prev: pd.Series | None = None
    fb_counts: dict = {0: 0, 1: 0, 2: 0}

    t_opt = time.perf_counter()
    for i, T in enumerate(available_dates):
        codes = codes_map[T]
        n     = len(codes)

        # Ridge 信号：未覆盖日期（冷启动 2012-01~2014-02）用 0（neutral，回退等权）
        if T in signal.index:
            alpha = signal.loc[T].reindex(codes).fillna(0.0)
        else:
            alpha = pd.Series(0.0, index=codes)

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
            config         = OPT_CONFIG,
        )

        effective_fb = result.fallback_level
        if not cov_available and result.fallback_level == 0:
            effective_fb = 1

        opt_weights[T]      = result.weights
        w_prev              = result.weights
        baseline_weights[T] = _compute_baseline_weights(alpha, halt_dict[T], codes)
        fb_counts[effective_fb] = fb_counts.get(effective_fb, 0) + 1

        meta_rows.append({
            "rebalance_date":       T,
            "fallback_level":       effective_fb,
            "solver_status":        result.solver_status,
            "solve_time_s":         result.solve_time_s,
            "cov_available":        cov_available,
            "constraint_compliant": result.constraint_compliant,
        })

        if (i + 1) % 20 == 0 or (i + 1) == len(available_dates):
            log.info("优化进度: %d/%d  用时=%.1fs", i + 1, len(available_dates),
                     time.perf_counter() - t_opt)

    log.info(
        "优化完成: %d 期  用时=%.1fs  Fallback: L1=%d  L2=%d  L3=%d",
        len(available_dates), time.perf_counter() - t_opt,
        fb_counts.get(0, 0), fb_counts.get(1, 0), fb_counts.get(2, 0),
    )

    weights_panel  = pd.DataFrame(opt_weights).T.fillna(0.0)
    baseline_panel = pd.DataFrame(baseline_weights).T.fillna(0.0)
    weights_panel.index.name  = "rebalance_date"
    baseline_panel.index.name = "rebalance_date"
    weights_panel.columns  = weights_panel.columns.astype(str)
    baseline_panel.columns = baseline_panel.columns.astype(str)

    meta_df = pd.DataFrame(meta_rows).set_index("rebalance_date")
    meta_df.index = pd.DatetimeIndex(meta_df.index)

    return weights_panel, baseline_panel, meta_df


# ---------------------------------------------------------------------------
# Step 3: 回测
# ---------------------------------------------------------------------------

def _annual_turnover_pct(trade_log: pd.DataFrame, n_months: int) -> float:
    """双边年化换手率（%）。"""
    if trade_log.empty or n_months == 0:
        return float("nan")
    total_to = (
        (trade_log["buy_value"] + trade_log["sell_value"])
        / trade_log["portfolio_value_before"]
    ).sum()
    return total_to / n_months * 12 * 100


def run_validation_backtest(
    weights_panel: pd.DataFrame,
    baseline_panel: pd.DataFrame,
) -> tuple:
    """运行验证期（VALID_START ~ VALID_END）V1 和 V2 回测，返回 (result_v1, result_v2)。"""
    log.info("=== Step 3: 验证期回测 ===")
    config = BacktestConfig(initial_value=1.0)

    log.info("运行 Ridge V1 Baseline 回测...")
    result_v1 = run_backtest(baseline_panel, cfg.VALID_START, cfg.VALID_END, config)
    log.info("运行 Ridge V2 优化权重回测...")
    result_v2 = run_backtest(weights_panel, cfg.VALID_START, cfg.VALID_END, config)

    n_months = len(result_v2.trade_log) if not result_v2.trade_log.empty else 0
    to_v1 = _annual_turnover_pct(result_v1.trade_log, n_months)
    to_v2 = _annual_turnover_pct(result_v2.trade_log, n_months)

    log.info("Ridge V2 验证期指标:")
    m = result_v2.metrics
    log.info(
        "  年化超额=%.2f%%  IR=%.3f  超额最大回撤=%.2f%%  TE=%.2f%%  月胜率=%.1f%%  换手=%.0f%%",
        m["excess_return"] * 100,
        m["information_ratio"],
        abs(m["excess_max_drawdown"]) * 100,
        m["tracking_error"] * 100,
        m["monthly_win_rate"] * 100,
        to_v2,
    )

    return result_v1, result_v2, to_v1, to_v2


# ---------------------------------------------------------------------------
# Step 4: Brinson 归因
# ---------------------------------------------------------------------------

def run_attribution(actual_weights_v2: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """运行 Brinson BHB 归因（验证期，仅 V2 优化组合）。"""
    log.info("=== Step 4: Brinson 归因 ===")
    try:
        brinson_detail, brinson_summary = compute_brinson_attribution(
            actual_weights_v2, cfg.VALID_START, cfg.VALID_END
        )
        log.info("Brinson 归因完成: %d 期", len(brinson_summary))
        return brinson_detail, brinson_summary
    except Exception as exc:
        log.error("Brinson 归因失败: %s", exc)
        return pd.DataFrame(), pd.DataFrame()


# ---------------------------------------------------------------------------
# Step 5: 对比报告生成
# ---------------------------------------------------------------------------

def _fmt(val: float, fmt: str = ".4f") -> str:
    if isinstance(val, float) and np.isnan(val):
        return "N/A"
    return format(val, fmt)


def _pct(val: float, digits: int = 2) -> str:
    if isinstance(val, float) and np.isnan(val):
        return "N/A"
    return f"{val * 100:+.{digits}f}%"


def _pos_pct(val: float, digits: int = 2) -> str:
    if isinstance(val, float) and np.isnan(val):
        return "N/A"
    return f"{val * 100:.{digits}f}%"


def _check(passed: bool | None) -> str:
    if passed is None:
        return "—"
    return "✅" if passed else "❌"


def generate_report(
    ridge_result_v1,
    ridge_result_v2,
    ridge_to_v1: float,
    ridge_to_v2: float,
    ridge_brinson_summary: pd.DataFrame,
    ridge_meta: pd.DataFrame,
    gen_time: str,
) -> str:
    """生成 Ridge vs IC_IR 完整对比报告（markdown）。"""

    # 加载主管线指标
    main_metrics = pd.read_parquet(MAIN_METRICS_PATH) if MAIN_METRICS_PATH.exists() else None
    main_brinson = pd.read_parquet(MAIN_BRINSON_PATH) if MAIN_BRINSON_PATH.exists() else None

    # 读取主管线换手率（从 backtest_trades_v2）
    main_trades_v2_path = cfg.DATA_PROC / "backtest_trades_v2.parquet"
    main_to_v2 = float("nan")
    if main_trades_v2_path.exists():
        main_trades = pd.read_parquet(main_trades_v2_path)
        n_months = len(main_trades)
        if n_months > 0:
            main_to_v2 = (
                (main_trades["buy_value"] + main_trades["sell_value"])
                / main_trades["portfolio_value_before"]
            ).sum() / n_months * 12 * 100

    # Ridge 指标
    r2 = ridge_result_v2.metrics

    def _get_main(col: str, key: str) -> float:
        if main_metrics is None or col not in main_metrics.columns or key not in main_metrics.index:
            return float("nan")
        return float(main_metrics.loc[key, col])

    # 归因汇总
    def _brinson_row(df: pd.DataFrame | None, col: str) -> str:
        if df is None or df.empty or col not in df.columns:
            return "N/A"
        return _pct(float(df[col].sum()))

    # Fallback 统计
    fb_v = ridge_meta["fallback_level"].value_counts().sort_index() if "fallback_level" in ridge_meta.columns else pd.Series()
    total_periods = len(ridge_meta)
    valid_dates = ridge_meta.index[ridge_meta.index >= cfg.VALID_START]
    valid_meta = ridge_meta.loc[valid_dates] if len(valid_dates) > 0 else pd.DataFrame()
    fb_valid = valid_meta["fallback_level"].value_counts().sort_index() if not valid_meta.empty else pd.Series()

    report = f"""\
# Ridge 信号回测对比报告

> 生成时间：{gen_time}
> 分支：feature/expand-train-2012
> 实验目录：`experiments/ridge_signal/results/backtest/`
>
> **平行实验设计**：Ridge 信号替换 IC_IR 信号，优化器参数、回测引擎、归因方法均与主管线完全一致。
> 主管线产物未被修改（只读）。

---

## 1. 实验对比概览

| 维度 | 主管线（IC_IR 信号）| 本实验（Ridge 信号）|
|------|---:|---:|
| 信号文件 | `composite_signal_ic_ir.parquet` | `ridge_composite_panel.parquet` |
| 信号起始日期 | 2012-01-31 | 2014-03-31（冷启动 26 期使用 0 信号）|
| 优化参数 | TE={cfg.OPT_TE_TARGET_ANNUAL:.0%}, IND_DEV={cfg.OPT_INDUSTRY_MAX_DEV:.0%}, SGL_DEV={cfg.OPT_SINGLE_MAX_DEV:.1%} | 同左（完全一致）|
| 回测区间 | {cfg.VALID_START.date()} ~ {cfg.VALID_END.date()} | 同左（完全一致）|

---

## 2. 验证期核心指标对比（V2 优化组合）

| 指标 | IC_IR 基线 | Ridge | 变化 | 目标 |
|------|---:|---:|---:|------|
| 年化超额收益 | {_pct(_get_main('v2','excess_return'))} | {_pct(r2['excess_return'])} | {_pct(r2['excess_return'] - _get_main('v2','excess_return'))} | — |
| 信息比率 IR | {_fmt(_get_main('v2','information_ratio'))} | {_fmt(r2['information_ratio'])} | {_fmt(r2['information_ratio'] - _get_main('v2','information_ratio'), '+.3f')} | ≥ 0.5 {_check(r2['information_ratio'] >= 0.5)} |
| 超额最大回撤 | {_pos_pct(abs(_get_main('v2','excess_max_drawdown')))} | {_pos_pct(abs(r2['excess_max_drawdown']))} | {_pct(abs(r2['excess_max_drawdown']) - abs(_get_main('v2','excess_max_drawdown')))} | ≤ 10% {_check(abs(r2['excess_max_drawdown']) <= 0.10)} |
| 跟踪误差（年化）| {_pos_pct(_get_main('v2','tracking_error'))} | {_pos_pct(r2['tracking_error'])} | {_pct(r2['tracking_error'] - _get_main('v2','tracking_error'))} | — |
| 月度胜率 | {_pos_pct(_get_main('v2','monthly_win_rate'), 1)} | {_pos_pct(r2['monthly_win_rate'], 1)} | — | — |
| 年化双边换手 | {_pos_pct(main_to_v2 / 100, 0)} | {_pos_pct(ridge_to_v2 / 100, 0)} | — | 500%-1500% {_check(500 <= ridge_to_v2 <= 1500)} |
| 绝对收益（策略）| {_pct(_get_main('v2','annualized_return'))} | {_pct(r2['annualized_return'])} | — | — |
| 基准收益 | {_pct(_get_main('v2','benchmark_return'))} | {_pct(r2['benchmark_return'])} | — | — |

---

## 3. V1 Baseline 对比（TopN 等权）

| 指标 | IC_IR 基线 V1 | Ridge V1 |
|------|---:|---:|
| 年化超额收益 | {_pct(_get_main('v1','excess_return'))} | {_pct(ridge_result_v1.metrics['excess_return'])} |
| 信息比率 IR | {_fmt(_get_main('v1','information_ratio'))} | {_fmt(ridge_result_v1.metrics['information_ratio'])} |
| 超额最大回撤 | {_pos_pct(abs(_get_main('v1','excess_max_drawdown')))} | {_pos_pct(abs(ridge_result_v1.metrics['excess_max_drawdown']))} |
| 月度胜率 | {_pos_pct(_get_main('v1','monthly_win_rate'), 1)} | {_pos_pct(ridge_result_v1.metrics['monthly_win_rate'], 1)} |

> V1 TopN 等权选股依赖信号排名。Ridge V1 的超额收益差异反映信号选股能力的直接对比（无优化器影响）。

---

## 4. Brinson BHB 归因对比（V2 优化组合）

| 效应 | IC_IR 基线 | Ridge |
|------|---:|---:|
| 总超额收益 | {_brinson_row(main_brinson, 'excess_return')} | {_brinson_row(ridge_brinson_summary, 'excess_return')} |
| 配置效应 | {_brinson_row(main_brinson, 'allocation_effect')} | {_brinson_row(ridge_brinson_summary, 'allocation_effect')} |
| 选股效应 | {_brinson_row(main_brinson, 'selection_effect')} | {_brinson_row(ridge_brinson_summary, 'selection_effect')} |
| 交叉效应 | {_brinson_row(main_brinson, 'interaction_effect')} | {_brinson_row(ridge_brinson_summary, 'interaction_effect')} |

---

## 5. 优化器 Fallback 统计

### 全期（训练+验证，共 {total_periods} 期）

| Fallback 等级 | 期数 |
|--------------|-----|
| L1（严格 QP 约束）| {int(fb_v.get(0, 0))} |
| L2（去掉二次 TE 约束）| {int(fb_v.get(1, 0))} |
| L3（TopN 等权）| {int(fb_v.get(2, 0))} |

### 验证期（{cfg.VALID_START.date()} ~ {cfg.VALID_END.date()}，共 {len(valid_meta)} 期）

| Fallback 等级 | 期数 |
|--------------|-----|
| L1（严格 QP 约束）| {int(fb_valid.get(0, 0))} |
| L2（去掉二次 TE 约束）| {int(fb_valid.get(1, 0))} |
| L3（TopN 等权）| {int(fb_valid.get(2, 0))} |

---

## 6. 结论

"""

    # Add conclusions
    ir_ridge = r2['information_ratio']
    ir_main  = _get_main('v2', 'information_ratio')
    er_ridge = r2['excess_return']
    er_main  = _get_main('v2', 'excess_return')
    mdd_ridge = abs(r2['excess_max_drawdown'])
    mdd_main  = abs(_get_main('v2', 'excess_max_drawdown'))

    conclusions = []

    if not np.isnan(ir_ridge) and not np.isnan(ir_main):
        ir_diff = ir_ridge - ir_main
        if ir_diff > 0.1:
            conclusions.append(
                f"- ✅ Ridge V2 IR={ir_ridge:.3f}，较 IC_IR 基线（{ir_main:.3f}）提升 {ir_diff:+.3f}，"
                f"{'通过 IR≥0.5 目标 ✅' if ir_ridge >= 0.5 else '仍未达到 IR≥0.5 ❌'}。"
            )
        elif ir_diff > 0:
            conclusions.append(
                f"- ⚠️ Ridge V2 IR={ir_ridge:.3f}，较 IC_IR 基线小幅提升 {ir_diff:+.3f}，"
                f"{'通过 IR≥0.5 ✅' if ir_ridge >= 0.5 else '仍未达到 IR≥0.5 ❌'}。"
            )
        else:
            conclusions.append(
                f"- ❌ Ridge V2 IR={ir_ridge:.3f}，较 IC_IR 基线下降 {ir_diff:+.3f}，"
                "IC 层面的提升未能传导至回测 IR。需分析原因。"
            )

    if not np.isnan(er_ridge) and not np.isnan(er_main):
        conclusions.append(
            f"- 年化超额：Ridge {_pct(er_ridge)} vs IC_IR {_pct(er_main)}，"
            f"差值 {_pct(er_ridge - er_main)}。"
        )

    if not np.isnan(mdd_ridge):
        conclusions.append(
            f"- 超额最大回撤：Ridge {_pos_pct(mdd_ridge)}，"
            f"{'通过 ≤10% ✅' if mdd_ridge <= 0.10 else '超出 10% ❌'}。"
        )

    if not np.isnan(ridge_to_v2):
        conclusions.append(
            f"- 年化双边换手：Ridge {ridge_to_v2:.0f}%，"
            f"{'在目标区间 500-1500% ✅' if 500 <= ridge_to_v2 <= 1500 else '超出目标区间 ❌'}。"
        )

    conclusions.append(
        "\n**IC vs IR 传导分析**："
    )
    conclusions.append(
        f"- 信号层：Ridge 验证期 IC_IR=0.729 vs IC_IR 基线 IC_IR=0.466（提升 56%）。"
    )
    conclusions.append(
        f"- 回测层：Ridge IR={ir_ridge:.3f} vs IC_IR 基线 IR={ir_main:.3f}。"
    )

    ic_diff = 0.729 - 0.466
    ir_diff_actual = ir_ridge - ir_main if not (np.isnan(ir_ridge) or np.isnan(ir_main)) else float("nan")
    if not np.isnan(ir_diff_actual):
        if ir_diff_actual > 0:
            conclusions.append(
                f"- ✅ IC 提升有效传导至回测 IR，但回测 IR 提升幅度（{ir_diff_actual:+.3f}）"
                f"远小于 IC_IR 提升幅度（{ic_diff:+.3f}）。差距由换手成本、组合约束、"
                "市场冲击等因素消耗。"
            )
        else:
            conclusions.append(
                f"- ❌ IC 层面提升未传导至回测 IR（IR 下降 {ir_diff_actual:+.3f}）。"
                "可能原因：(1) 信号过拟合至 IC 优化而损失组合层稳健性；"
                "(2) Ridge 信号的截面分布变化导致换手率上升抵消收益；"
                "(3) 验证期样本量小（23期），随机噪声影响大。"
            )

    report += "\n".join(conclusions)
    report += "\n"
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    gen_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    log.info("=" * 60)
    log.info("Ridge 平行回测实验")
    log.info("=" * 60)

    # --- 1. 加载数据 ---
    log.info("=== Step 1: 加载数据 ===")
    ridge_signal = load_ridge_signal()
    index_member, industry_pivot = load_aux_data()

    # --- 2. 组合优化 ---
    weights_panel, baseline_panel, meta_df = run_optimization(
        ridge_signal, index_member, industry_pivot
    )

    # 保存权重
    weights_panel.to_parquet(RIDGE_WEIGHTS_OPT)
    baseline_panel.to_parquet(RIDGE_WEIGHTS_BL)
    meta_df.to_parquet(RIDGE_WEIGHTS_META)
    log.info("权重已保存: opt=%s  baseline=%s", weights_panel.shape, baseline_panel.shape)

    # --- 3. 验证期回测 ---
    result_v1, result_v2, to_v1, to_v2 = run_validation_backtest(
        weights_panel, baseline_panel
    )

    # 保存回测产物
    nav_df = pd.DataFrame({
        "ridge_v1": result_v1.nav,
        "ridge_v2": result_v2.nav,
        "benchmark": result_v1.benchmark_nav,
    }).dropna()
    nav_df.index.name = "trade_date"
    nav_df.to_parquet(RIDGE_NAV_PATH)

    metrics_df = pd.DataFrame({
        "ridge_v1": pd.Series(result_v1.metrics),
        "ridge_v2": pd.Series(result_v2.metrics),
    })
    metrics_df.index.name = "metric"
    metrics_df.to_parquet(RIDGE_METRICS_PATH)

    for ver, res, path_t, path_w in [
        ("v1", result_v1, RIDGE_TRADES_V1, RIDGE_WEIGHTS_V1),
        ("v2", result_v2, RIDGE_TRADES_V2, RIDGE_WEIGHTS_V2),
    ]:
        if not res.trade_log.empty:
            res.trade_log.to_parquet(path_t)
        if not res.actual_weights.empty:
            res.actual_weights.to_parquet(path_w)

    log.info("回测产物已保存到 %s", BACKTEST_DIR)

    # --- 4. Brinson 归因 ---
    brinson_detail, brinson_summary = run_attribution(result_v2.actual_weights)
    if not brinson_detail.empty:
        brinson_detail.to_parquet(RIDGE_BRINSON)
    if not brinson_summary.empty:
        brinson_summary.to_parquet(RIDGE_BRINSON_SUM)

    # --- 5. 生成对比报告 ---
    log.info("=== Step 5: 生成对比报告 ===")
    report = generate_report(
        ridge_result_v1=result_v1,
        ridge_result_v2=result_v2,
        ridge_to_v1=to_v1,
        ridge_to_v2=to_v2,
        ridge_brinson_summary=brinson_summary,
        ridge_meta=meta_df,
        gen_time=gen_time,
    )

    COMPARISON_REPORT.write_text(report, encoding="utf-8")
    log.info("对比报告已写入: %s", COMPARISON_REPORT)
    log.info("=" * 60)
    log.info("平行回测完成")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
