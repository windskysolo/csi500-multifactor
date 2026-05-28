"""
scripts/run_optimizer_grid.py — 阶段1：优化器约束参数网格实验
============================================================

实验目标：
  量化三种约束配置（O0/O1/O2）在训练期的关键指标，选定最优参数组合。

  评估指标：
  - 事前 TE（年化，来自协方差矩阵），均值
  - 实现 TE（年化，来自回测引擎）
  - TE 约束 binding 比例（L1期中事前TE ≥ 0.95 × 目标TE的占比）
  - L1/L2/L3 Fallback 分布（%）
  - 单股偏离实际利用率（最大值、均值）
  - 行业偏离实际利用率（最大值、均值）
  - 训练期年化超额收益
  - 训练期 IR
  - 训练期超额最大回撤
  - 年化双边换手率

仅在训练期（TRAIN_START ~ TRAIN_END）运行，不触碰验证/测试集。
协方差直接读现有 cov_cache，不重新估计。

配置：
  O0（基线，当前生产参数）: TE=5%, single=±1%, industry=±2%
  O1（温和放松）:           TE=6%, single=±1.5%, industry=±3%
  O2（行业参考水准）:       TE=8%, single=±2%, industry=±4%

输出：
  reports/optimizer_grid/grid_summary.csv    — 配置对比表（主要结论）
  reports/optimizer_grid/{O0,O1,O2}_meta.csv — 逐期优化元数据
  reports/optimizer_grid/{O0,O1,O2}_nav.csv  — 训练期 NAV

用法：
  python -m scripts.run_optimizer_grid
"""

import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg
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
# 路径
# ---------------------------------------------------------------------------
SIGNAL_IC_IR_PATH = cfg.DATA_PROC / "composite_signal_ic_ir.parquet"
COV_CACHE_DIR = cfg.DATA_PROC / "cov_cache"
OUTPUT_DIR = _ROOT / "reports" / "optimizer_grid"

TRADING_DAYS_PER_YEAR = 252

# ---------------------------------------------------------------------------
# 网格配置定义（研究决策，实验结束后选定获胜者更新 config.py）
# ---------------------------------------------------------------------------
GRID_CONFIGS: dict[str, OptimizeConfig] = {
    "O0": OptimizeConfig(
        te_target_annual=0.05,
        single_max_dev=0.01,
        industry_max_dev=0.02,
        topn=cfg.OPT_TOPN,
    ),
    "O1": OptimizeConfig(
        te_target_annual=0.06,
        single_max_dev=0.015,
        industry_max_dev=0.03,
        topn=cfg.OPT_TOPN,
    ),
    "O2": OptimizeConfig(
        te_target_annual=0.08,
        single_max_dev=0.02,
        industry_max_dev=0.04,
        topn=cfg.OPT_TOPN,
    ),
}

# TE constraint 判定为 binding 的事前TE占目标TE的比例下限
TE_BINDING_RATIO = 0.95


# ---------------------------------------------------------------------------
# 辅助函数
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


def _compute_max_ind_dev(
    w_series: pd.Series,
    wb_series: pd.Series,
    industry_map: pd.Series,
) -> float:
    """计算单期最大行业偏离（绝对值）。industry_map: ts_code → 行业代码。"""
    dev = (w_series - wb_series).reindex(w_series.index).fillna(0.0)
    ind = industry_map.reindex(dev.index)
    valid_ind = ind.dropna()
    if valid_ind.empty:
        return 0.0
    ind_dev = dev.loc[valid_ind.index].groupby(valid_ind).sum()
    return float(np.max(np.abs(ind_dev)))


def _load_cov_cache(
    dates: list[pd.Timestamp],
    codes_per_date: dict[pd.Timestamp, list[str]],
) -> dict[pd.Timestamp, np.ndarray]:
    """从磁盘读取已有协方差缓存，不触发重新估计。"""
    cov_cache: dict = {}
    n_loaded = n_missing = 0

    for T in dates:
        codes = codes_per_date[T]
        cache_path = COV_CACHE_DIR / f"{T.strftime('%Y%m%d')}.parquet"
        if not cache_path.exists():
            n_missing += 1
            continue
        try:
            cov_df = pd.read_parquet(cache_path).reindex(index=codes, columns=codes)
            if cov_df.isna().any().any():
                n_missing += 1
                continue
            cov_arr = cov_df.values.astype(float)
            cov_arr, _, _, _ = validate_and_repair_covariance(cov_arr)
            cov_cache[T] = cov_arr
            n_loaded += 1
        except Exception as e:
            log.warning("%s 协方差读取失败: %s", T.date(), e)
            n_missing += 1

    log.info("协方差缓存：读取=%d，缺失=%d（缺失期降级 L2）", n_loaded, n_missing)
    return cov_cache


# ---------------------------------------------------------------------------
# 单配置优化 + 逐期指标
# ---------------------------------------------------------------------------

def _run_single_config(
    config_name: str,
    opt_config: OptimizeConfig,
    train_dates: list[pd.Timestamp],
    composite_signal: pd.DataFrame,
    benchmark_weights_dict: dict[pd.Timestamp, pd.Series],
    cov_cache: dict[pd.Timestamp, np.ndarray],
    industry_dict: dict[pd.Timestamp, pd.Series],
    halt_dict: dict[pd.Timestamp, set],
    limit_up_dict: dict[pd.Timestamp, set],
    limit_dn_dict: dict[pd.Timestamp, set],
) -> tuple[pd.DataFrame, pd.DataFrame, dict, pd.Series]:
    """
    运行单配置优化实验（仅训练期），含逐期指标计算。

    Args:
        config_name:            配置名（O0/O1/O2）
        opt_config:             OptimizeConfig
        train_dates:            训练期调仓日列表
        composite_signal:       合成信号面板（已过滤到训练期）
        benchmark_weights_dict: {T: Series(ts_code → weight)}
        cov_cache:              {T: ndarray(N×N)} 日频协方差矩阵
        industry_dict:          {T: Series(ts_code → industry_code)}
        halt/limit_up/limit_dn_dict: 停牌/涨跌停股集合

    Returns:
        (weights_panel, meta_df, summary_metrics, nav_series)
        weights_panel:   行=调仓日，列=ts_code
        meta_df:         逐期元数据
        summary_metrics: 汇总指标 dict
        nav_series:      训练期 NAV
    """
    opt_weights: dict = {}
    meta_rows: list[dict] = []
    w_prev: pd.Series | None = None
    fb_counts: dict[int, int] = {0: 0, 1: 0, 2: 0}

    # 逐期收集的指标
    exante_te_list: list[float] = []
    te_binding_l1_count = 0
    l1_success_count = 0
    single_dev_max_list: list[float] = []
    ind_dev_max_list: list[float] = []

    t0 = time.perf_counter()

    for i, T in enumerate(train_dates):
        if T not in benchmark_weights_dict:
            continue

        codes = list(benchmark_weights_dict[T].index)
        n = len(codes)

        alpha = (
            composite_signal.loc[T].reindex(codes).fillna(0.0)
            if T in composite_signal.index
            else pd.Series(0.0, index=codes)
        )

        cov_available = T in cov_cache
        cov = cov_cache[T] if cov_available else np.eye(n) * 1e-4

        w_prev_aligned = w_prev.reindex(codes) if w_prev is not None else None

        result = optimize_single_period(
            alpha=alpha,
            w_b=benchmark_weights_dict[T],
            cov=cov,
            industry_map=industry_dict.get(T, pd.Series(dtype=object)),
            w_prev=w_prev_aligned,
            halt_codes=halt_dict.get(T, set()),
            limit_up_codes=limit_up_dict.get(T, set()),
            limit_dn_codes=limit_dn_dict.get(T, set()),
            config=opt_config,
        )

        effective_fb = result.fallback_level
        effective_status = result.solver_status
        if not cov_available and result.fallback_level == 0:
            effective_fb = 1
            effective_status = result.solver_status + "+cov_missing"

        fb_counts[effective_fb] = fb_counts.get(effective_fb, 0) + 1

        # 事前 TE（仅在有协方差时有意义）
        w_vec = result.weights.reindex(codes).fillna(0.0).values.astype(float)
        wb_vec = benchmark_weights_dict[T].reindex(codes).fillna(0.0).values.astype(float)
        dev = w_vec - wb_vec

        if cov_available:
            variance_daily = float(dev @ cov @ dev)
            exante_te = float(np.sqrt(max(variance_daily, 0.0) * TRADING_DAYS_PER_YEAR))
            exante_te_list.append(exante_te)

            if effective_fb == 0:  # L1 成功
                l1_success_count += 1
                if exante_te >= TE_BINDING_RATIO * opt_config.te_target_annual:
                    te_binding_l1_count += 1
        else:
            exante_te = float("nan")

        # 单股最大偏离
        single_dev = float(np.max(np.abs(dev)))
        single_dev_max_list.append(single_dev)

        # 行业最大偏离
        ind_dev = _compute_max_ind_dev(
            result.weights,
            benchmark_weights_dict[T],
            industry_dict.get(T, pd.Series(dtype=object)),
        )
        ind_dev_max_list.append(ind_dev)

        opt_weights[T] = result.weights
        w_prev = result.weights

        meta_rows.append({
            "rebalance_date": T,
            "fallback_level": effective_fb,
            "solver_status": effective_status,
            "solve_time_s": result.solve_time_s,
            "cov_available": cov_available,
            "exante_te": exante_te,
            "single_dev_max": single_dev,
            "ind_dev_max": ind_dev,
        })

        if (i + 1) % 20 == 0 or (i + 1) == len(train_dates):
            log.info(
                "[%s] 优化进度: %d/%d  L1=%d  L2=%d  L3=%d  用时=%.1fs",
                config_name, i + 1, len(train_dates),
                fb_counts.get(0, 0), fb_counts.get(1, 0), fb_counts.get(2, 0),
                time.perf_counter() - t0,
            )

    n_periods = len(opt_weights)
    log.info(
        "[%s] 优化完成: %d 期  L1=%d  L2=%d  L3=%d  总用时=%.1fs",
        config_name, n_periods,
        fb_counts.get(0, 0), fb_counts.get(1, 0), fb_counts.get(2, 0),
        time.perf_counter() - t0,
    )

    # 组装权重面板
    weights_panel = pd.DataFrame(opt_weights).T
    weights_panel.index.name = "rebalance_date"
    weights_panel.columns = weights_panel.columns.astype(str)

    meta_df = pd.DataFrame(meta_rows).set_index("rebalance_date")

    # 回测（训练期）
    bt_config = BacktestConfig(initial_value=1.0)
    bt_result = run_backtest(weights_panel, cfg.TRAIN_START, cfg.TRAIN_END, bt_config)
    n_months = len(bt_result.trade_log) if not bt_result.trade_log.empty else n_periods
    m = bt_result.metrics

    # 汇总指标
    total = len(train_dates)
    te_binding_pct = (
        f"{te_binding_l1_count / l1_success_count * 100:.1f}%"
        if l1_success_count > 0
        else "N/A"
    )

    summary_metrics = {
        "config":            config_name,
        "te_target":         f"{opt_config.te_target_annual:.0%}",
        "single_max_dev":    f"±{opt_config.single_max_dev:.1%}",
        "industry_max_dev":  f"±{opt_config.industry_max_dev:.1%}",
        "fb_l1_pct":         f"{fb_counts.get(0, 0) / total * 100:.1f}%",
        "fb_l2_pct":         f"{fb_counts.get(1, 0) / total * 100:.1f}%",
        "fb_l3_pct":         f"{fb_counts.get(2, 0) / total * 100:.1f}%",
        "te_binding_pct_of_l1": te_binding_pct,
        "exante_te_mean":    f"{np.mean(exante_te_list):.2%}" if exante_te_list else "N/A",
        "realized_te":       f"{m.get('tracking_error', float('nan')):.2%}",
        "single_dev_max":    f"{max(single_dev_max_list):.2%}" if single_dev_max_list else "N/A",
        "single_dev_mean":   f"{np.mean(single_dev_max_list):.2%}" if single_dev_max_list else "N/A",
        "ind_dev_max":       f"{max(ind_dev_max_list):.2%}" if ind_dev_max_list else "N/A",
        "ind_dev_mean":      f"{np.mean(ind_dev_max_list):.2%}" if ind_dev_max_list else "N/A",
        "excess_return":     f"{m.get('excess_return', float('nan')):.2%}",
        "ir":                f"{m.get('information_ratio', float('nan')):.3f}",
        "excess_max_dd":     f"{abs(m.get('excess_max_drawdown', float('nan'))):.2%}",
        "turnover_pct":      f"{_annual_turnover_pct(bt_result.trade_log, n_months):.0f}%",
    }

    return weights_panel, meta_df, summary_metrics, bt_result.nav


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=" * 60)
    log.info("阶段 1：优化器约束参数网格实验")
    log.info("训练期：%s ~ %s", cfg.TRAIN_START.date(), cfg.TRAIN_END.date())
    log.info("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 读取合成信号（过滤到训练期）
    if not SIGNAL_IC_IR_PATH.exists():
        log.error("合成信号不存在：%s\n请先运行 run_signal_combination.py", SIGNAL_IC_IR_PATH)
        sys.exit(1)

    composite_signal = pd.read_parquet(SIGNAL_IC_IR_PATH)
    train_mask = (
        (composite_signal.index >= cfg.TRAIN_START)
        & (composite_signal.index <= cfg.TRAIN_END)
    )
    composite_signal = composite_signal.loc[train_mask]
    log.info("训练期调仓日：%d 期", len(composite_signal))

    # 读取 index_member 和 industry
    log.info("读取 index_member 及行业数据...")
    index_member_raw = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    industry_raw = pd.read_parquet(cfg.DATA_PROC / "industry.parquet")

    industry_pivot = (
        industry_raw["industry_code"]
        .unstack(level="ts_code")
        .sort_index()
        .ffill()
    )

    rebalance_dates = list(composite_signal.index)
    dates_in_member = index_member_raw.index.get_level_values("rebalance_date").unique()
    available_dates = [d for d in rebalance_dates if d in dates_in_member]

    missing_cnt = len(rebalance_dates) - len(available_dates)
    if missing_cnt > 0:
        log.warning("index_member 缺少 %d 个调仓日，将跳过", missing_cnt)
    log.info("可用调仓日：%d 期", len(available_dates))

    benchmark_weights_dict: dict = {}
    halt_dict: dict = {}
    limit_up_dict: dict = {}
    limit_dn_dict: dict = {}
    industry_dict: dict = {}
    codes_per_date: dict = {}

    for T in available_dates:
        snap = index_member_raw.loc[T]
        w_b = snap["index_weight"] / 100.0
        w_b = w_b / w_b.sum()
        benchmark_weights_dict[T] = w_b
        halt_dict[T] = set(snap.index[snap["is_suspended"]])
        limit_up_dict[T] = set(snap.index[snap["is_limit_up_locked"]])
        limit_dn_dict[T] = set(snap.index[snap["is_limit_down_locked"]])
        ind_dates = industry_pivot.index[industry_pivot.index <= T]
        industry_dict[T] = (
            industry_pivot.loc[ind_dates[-1]].dropna()
            if len(ind_dates) > 0
            else pd.Series(dtype=object)
        )
        codes_per_date[T] = list(w_b.index)

    # 读取协方差缓存（不重估）
    cov_cache = _load_cov_cache(available_dates, codes_per_date)

    # -----------------------------------------------------------------------
    # 网格实验主循环
    # -----------------------------------------------------------------------
    all_summaries: list[dict] = []
    nav_dict: dict[str, pd.Series] = {}

    for config_name, opt_config in GRID_CONFIGS.items():
        log.info(
            "\n%s\n[%s] TE=%.0f%%  single=±%.1f%%  industry=±%.1f%%",
            "─" * 60, config_name,
            opt_config.te_target_annual * 100,
            opt_config.single_max_dev * 100,
            opt_config.industry_max_dev * 100,
        )

        weights_panel, meta_df, summary, nav = _run_single_config(
            config_name=config_name,
            opt_config=opt_config,
            train_dates=available_dates,
            composite_signal=composite_signal,
            benchmark_weights_dict=benchmark_weights_dict,
            cov_cache=cov_cache,
            industry_dict=industry_dict,
            halt_dict=halt_dict,
            limit_up_dict=limit_up_dict,
            limit_dn_dict=limit_dn_dict,
        )

        meta_df.to_csv(OUTPUT_DIR / f"{config_name}_meta.csv")
        nav.to_frame(name=config_name).to_csv(OUTPUT_DIR / f"{config_name}_nav.csv")
        all_summaries.append(summary)
        nav_dict[config_name] = nav
        log.info("[%s] 结果已保存", config_name)

    # -----------------------------------------------------------------------
    # 汇总表
    # -----------------------------------------------------------------------
    summary_df = pd.DataFrame(all_summaries).set_index("config")
    summary_df.to_csv(OUTPUT_DIR / "grid_summary.csv")

    # 控制台打印
    _print_summary(summary_df, nav_dict)

    log.info("=" * 60)
    log.info("阶段 1 完成，完整结果：%s", OUTPUT_DIR)
    log.info("=" * 60)


def _print_summary(
    summary_df: pd.DataFrame,
    nav_dict: dict[str, pd.Series],
) -> None:
    """格式化打印网格实验结果。"""
    print()
    print("=" * 80)
    print(
        f"优化器约束网格实验 — 训练期结果"
        f"（{cfg.TRAIN_START.date()} ~ {cfg.TRAIN_END.date()}）"
    )
    print("=" * 80)

    # 对比表（按行分组展示）
    groups = [
        ("约束参数",     ["te_target", "single_max_dev", "industry_max_dev"]),
        ("Fallback分布", ["fb_l1_pct", "fb_l2_pct", "fb_l3_pct"]),
        ("TE诊断",       ["te_binding_pct_of_l1", "exante_te_mean", "realized_te"]),
        ("偏离利用率",   ["single_dev_max", "single_dev_mean", "ind_dev_max", "ind_dev_mean"]),
        ("回测指标",     ["excess_return", "ir", "excess_max_dd", "turnover_pct"]),
    ]

    configs = list(summary_df.index)
    col_w = 18

    for group_name, cols in groups:
        print(f"\n  【{group_name}】")
        header = f"  {'指标':<28}" + "".join(f"{c:>{col_w}}" for c in configs)
        print(header)
        print("  " + "-" * (28 + col_w * len(configs)))
        for col in cols:
            if col not in summary_df.columns:
                continue
            row = f"  {col:<28}" + "".join(
                f"{str(summary_df.loc[c, col]):>{col_w}}" for c in configs
            )
            print(row)

    print()
    print(f"  完整对比表：{OUTPUT_DIR / 'grid_summary.csv'}")
    print("=" * 80)
    print()


if __name__ == "__main__":
    main()
