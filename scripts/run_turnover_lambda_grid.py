"""
scripts/run_turnover_lambda_grid.py — 阶段 2：换手成本惩罚系数（λ_TC）网格实验
=================================================================================

目标：
  在训练期对 λ_TC 做网格搜索，找到使年化双边换手落在目标区间（5-10 倍）的值，
  同时观察对 IR、超额收益的影响，以最小 IR 损失为代价换取成本节约。

前提：
  阶段 1（run_optimizer_grid.py）已完成，通过 --winning-config 指定获胜约束配置。
  λ_TC 校准只基于训练期；验证期只做一次对照，不反向调整参数。

选择标准（见 improvement_guide.md 阶段 2）：
  主目标：年化双边换手落在 5-10 倍目标区间
  次要：在满足主目标的候选中，选 IR 最高者；IR 相同时选最小 lambda（最保守）
  确定后将 λ_TC 填入 src/config.py 的 OPT_TURNOVER_LAMBDA

用法：
  python -m scripts.run_turnover_lambda_grid --winning-config O1

输出：
  reports/turnover_lambda_grid/lambda_summary.csv   — 主对比表（含推荐值）
  reports/turnover_lambda_grid/lam_{xxxx}_meta.csv  — 逐期元数据
  reports/turnover_lambda_grid/lam_{xxxx}_nav.csv   — 训练期 NAV
"""

import argparse
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
# 常量
# ---------------------------------------------------------------------------
SIGNAL_IC_IR_PATH = cfg.DATA_PROC / "composite_signal_ic_ir.parquet"
COV_CACHE_DIR     = cfg.DATA_PROC / "cov_cache"
OUTPUT_DIR        = _ROOT / "reports" / "turnover_lambda_grid"
TRADING_DAYS_PER_YEAR = 252

# λ_TC 候选值；包含 0.0 作为无惩罚基线对照
LAMBDA_GRID: list[float] = [0.0, 0.0005, 0.001, 0.002, 0.004]

# 换手倍数目标区间（年化双边）
# 阶段1修复后实际换手约3.80x（低于原诊断7.45x），目标区间下调至[2,5]x
# 目的：观察lambda对IR/换手的权衡曲线，不强制"达到5x以上"
TARGET_TURNOVER_MIN: float = 2.0
TARGET_TURNOVER_MAX: float = 5.0

# 阶段 1 约束配置（与 run_optimizer_grid.py 保持同步）
PHASE1_CONSTRAINT_CONFIGS: dict[str, dict] = {
    "O0": {"te_target_annual": 0.05, "single_max_dev": 0.01,  "industry_max_dev": 0.02},
    "O1": {"te_target_annual": 0.06, "single_max_dev": 0.015, "industry_max_dev": 0.03},
    "O2": {"te_target_annual": 0.08, "single_max_dev": 0.02,  "industry_max_dev": 0.04},
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _lam_label(lam: float) -> str:
    """将 λ 值转为文件名安全标签，如 0.001 → 'lam_0010'。"""
    return f"lam_{int(round(lam * 10000)):04d}"


def _annual_turnover_x(trade_log: pd.DataFrame, n_months: int) -> float:
    """计算年化双边换手倍数（1.0 = 1 倍 = 100%，非百分比）。"""
    if trade_log.empty or n_months == 0:
        return float("nan")
    total_to = (
        (trade_log["buy_value"] + trade_log["sell_value"])
        / trade_log["portfolio_value_before"]
    ).sum()
    return total_to / n_months * 12


def _load_cov_cache(
    dates: list[pd.Timestamp],
    codes_per_date: dict[pd.Timestamp, list[str]],
) -> dict[pd.Timestamp, np.ndarray]:
    """从磁盘读取已有协方差缓存，不重新估计。"""
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


def _run_single_lambda(
    label: str,
    opt_config: OptimizeConfig,
    train_dates: list[pd.Timestamp],
    composite_signal: pd.DataFrame,
    benchmark_weights_dict: dict[pd.Timestamp, pd.Series],
    cov_cache: dict[pd.Timestamp, np.ndarray],
    industry_dict: dict[pd.Timestamp, pd.Series],
    halt_dict: dict[pd.Timestamp, set],
    limit_up_dict: dict[pd.Timestamp, set],
    limit_dn_dict: dict[pd.Timestamp, set],
) -> tuple[pd.DataFrame, dict, pd.Series]:
    """
    以固定约束参数 + 指定 λ_TC 运行训练期全量优化，返回逐期元数据、汇总指标和 NAV。

    Args:
        label:                  日志标签（如 "lambda=0.0010"）
        opt_config:             OptimizeConfig，含 turnover_lambda
        train_dates:            训练期调仓日列表
        composite_signal:       合成信号面板（已过滤到训练期）
        benchmark_weights_dict: {T: Series(ts_code → weight)}
        cov_cache:              {T: ndarray(N×N)} 日频协方差矩阵
        industry_dict:          {T: Series(ts_code → industry_code)}
        halt/limit_up/limit_dn_dict: 停牌/涨跌停股集合

    Returns:
        (meta_df, summary_dict, nav_series)
        summary_dict 字段：lambda, turnover_x, in_target, ir,
                          excess_return_pct, excess_max_dd_pct,
                          fb_l1_pct, fb_l2_pct, fb_l3_pct
    """
    opt_weights: dict = {}
    meta_rows: list[dict] = []
    w_prev: pd.Series | None = None
    fb_counts: dict[int, int] = {0: 0, 1: 0, 2: 0}
    t0 = time.perf_counter()
    total = len(train_dates)

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
        opt_weights[T] = result.weights
        w_prev = result.weights

        meta_rows.append({
            "rebalance_date": T,
            "fallback_level": effective_fb,
            "solver_status":  effective_status,
            "solve_time_s":   result.solve_time_s,
            "cov_available":  cov_available,
        })

        if (i + 1) % 20 == 0 or (i + 1) == total:
            log.info(
                "[%s] 优化进度: %d/%d  L1=%d  L2=%d  L3=%d  用时=%.1fs",
                label, i + 1, total,
                fb_counts.get(0, 0), fb_counts.get(1, 0), fb_counts.get(2, 0),
                time.perf_counter() - t0,
            )

    weights_panel = pd.DataFrame(opt_weights).T
    weights_panel.index.name = "rebalance_date"
    weights_panel.columns = weights_panel.columns.astype(str)

    meta_df = pd.DataFrame(meta_rows).set_index("rebalance_date")

    bt_config = BacktestConfig(initial_value=1.0)
    bt_result = run_backtest(weights_panel, cfg.TRAIN_START, cfg.TRAIN_END, bt_config)
    n_months = len(bt_result.trade_log) if not bt_result.trade_log.empty else len(opt_weights)
    m = bt_result.metrics

    turnover_x = _annual_turnover_x(bt_result.trade_log, n_months)
    er = m.get("excess_return", float("nan"))
    dd = m.get("excess_max_drawdown", float("nan"))
    ir = m.get("information_ratio", float("nan"))

    summary = {
        "lambda":           opt_config.turnover_lambda,
        "turnover_x":       round(turnover_x, 2) if not np.isnan(turnover_x) else float("nan"),
        "in_target":        (
            TARGET_TURNOVER_MIN <= turnover_x <= TARGET_TURNOVER_MAX
            if not np.isnan(turnover_x) else False
        ),
        "ir":               round(ir, 4) if not np.isnan(ir) else float("nan"),
        # 存为百分比（乘以 100）方便 CSV 阅读
        "excess_return_pct": round(er * 100, 3) if not np.isnan(er) else float("nan"),
        "excess_max_dd_pct": round(abs(dd) * 100, 3) if not np.isnan(dd) else float("nan"),
        "fb_l1_pct": round(fb_counts.get(0, 0) / total * 100, 1),
        "fb_l2_pct": round(fb_counts.get(1, 0) / total * 100, 1),
        "fb_l3_pct": round(fb_counts.get(2, 0) / total * 100, 1),
    }

    log.info(
        "[%s] 完成: 换手=%.2fx  IR=%.3f  超额=%.2f%%  L1=%.0f%%",
        label,
        turnover_x if not np.isnan(turnover_x) else -1,
        ir if not np.isnan(ir) else float("nan"),
        er * 100 if not np.isnan(er) else float("nan"),
        fb_counts.get(0, 0) / total * 100,
    )

    return meta_df, summary, bt_result.nav


# ---------------------------------------------------------------------------
# 推荐逻辑
# ---------------------------------------------------------------------------

def _recommend_lambda(summary_df: pd.DataFrame) -> float | None:
    """
    从汇总表中推荐 λ_TC：
    - 优先选换手在目标区间 [5, 10]x 内且 IR 最高者
    - 多个 IR 相同时取最小 lambda（最保守）
    - 无满足条件时，返回换手最接近目标上界的候选（暗示需更大惩罚）
    """
    in_target = summary_df[summary_df["in_target"]]
    if not in_target.empty:
        best = in_target.sort_values(["ir", "lambda"], ascending=[False, True]).iloc[0]
        return float(best["lambda"])

    # 无满足目标的 lambda：换手均超出上界，返回最接近上界的（最低换手中最大 lambda）
    above = summary_df[
        summary_df["turnover_x"].notna() & (summary_df["turnover_x"] > TARGET_TURNOVER_MAX)
    ]
    if not above.empty:
        return float(above.sort_values("turnover_x").iloc[0]["lambda"])

    return None


# ---------------------------------------------------------------------------
# 结果打印
# ---------------------------------------------------------------------------

def _print_summary(
    summary_df: pd.DataFrame,
    winning_config: str,
    recommended_lambda: float | None,
) -> None:
    """格式化打印对比表和推荐结论。"""
    print()
    print("=" * 88)
    print(
        f"换手惩罚系数网格实验 — 训练期 "
        f"（{cfg.TRAIN_START.date()} ~ {cfg.TRAIN_END.date()}）"
    )
    print(
        f"  Phase 1 获胜配置：{winning_config}    "
        f"目标换手区间：{TARGET_TURNOVER_MIN:.0f} ~ {TARGET_TURNOVER_MAX:.0f} 倍/年"
    )
    print("=" * 88)
    print()

    hdr = (
        f"  {'lambda':>8}  {'换手(x倍)':>10}  {'达标':>4}  "
        f"{'IR':>7}  {'超额收益%':>10}  {'超额回撤%':>10}  "
        f"{'L1%':>5}  {'L2%':>5}  {'L3%':>5}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for _, row in summary_df.iterrows():
        lam  = row["lambda"]
        in_t = "YES" if row["in_target"] else "no "
        to_s = f"{row['turnover_x']:.2f}x" if not np.isnan(row["turnover_x"]) else "  N/A"
        ir_s = f"{row['ir']:.3f}"           if not np.isnan(row["ir"])          else "  N/A"
        er_s = f"{row['excess_return_pct']:.2f}" if not np.isnan(row["excess_return_pct"]) else "  N/A"
        dd_s = f"{row['excess_max_dd_pct']:.2f}" if not np.isnan(row["excess_max_dd_pct"]) else "  N/A"
        rec  = "  <- RECOMMENDED" if (
            recommended_lambda is not None and abs(lam - recommended_lambda) < 1e-8
        ) else ""
        print(
            f"  {lam:>8.4f}  {to_s:>10}  {in_t:>4}  "
            f"{ir_s:>7}  {er_s:>10}  {dd_s:>10}  "
            f"{row['fb_l1_pct']:>4.1f}%  {row['fb_l2_pct']:>4.1f}%  {row['fb_l3_pct']:>4.1f}%"
            f"{rec}"
        )

    print()

    if recommended_lambda is not None:
        in_target_rows = summary_df[summary_df["in_target"]]
        rec_row = summary_df[abs(summary_df["lambda"] - recommended_lambda) < 1e-8].iloc[0]

        if not in_target_rows.empty:
            print(f"  推荐 OPT_TURNOVER_LAMBDA = {recommended_lambda:.4f}")
            print(
                f"  （年化换手 {rec_row['turnover_x']:.2f}x，在目标区间内；"
                f"IR = {rec_row['ir']:.3f}）"
            )
            print()
            print(
                f"  下一步：将 src/config.py 中 OPT_TURNOVER_LAMBDA 设为 {recommended_lambda:.4f}，"
                f"然后在验证期做一次对照。"
            )
        else:
            print(
                f"  注意：无 λ_TC 使换手完全落入目标区间 "
                f"[{TARGET_TURNOVER_MIN:.0f}, {TARGET_TURNOVER_MAX:.0f}]x。"
            )
            print(
                f"  最接近上界的候选：lambda={recommended_lambda:.4f}，"
                f"换手={rec_row['turnover_x']:.2f}x"
            )
            print(f"  建议扩大 LAMBDA_GRID 范围后重试。")
    else:
        print(f"  无法确定推荐 λ_TC，请人工审查上表。")

    print()
    print(f"  完整对比表：{OUTPUT_DIR / 'lambda_summary.csv'}")
    print("=" * 88)
    print()


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="阶段 2：λ_TC 换手成本惩罚网格实验（仅训练期）"
    )
    parser.add_argument(
        "--winning-config",
        required=True,
        choices=["O0", "O1", "O2"],
        metavar="CONFIG",
        help="阶段 1 选定的获胜约束配置，取值 O0 / O1 / O2",
    )
    args = parser.parse_args()

    winning_config    = args.winning_config
    constraint_params = PHASE1_CONSTRAINT_CONFIGS[winning_config]

    log.info("=" * 60)
    log.info("阶段 2：换手成本惩罚系数 λ_TC 网格实验")
    log.info(
        "Phase 1 获胜配置：%s  TE=%.0f%%  single=±%.1f%%  industry=±%.1f%%",
        winning_config,
        constraint_params["te_target_annual"] * 100,
        constraint_params["single_max_dev"] * 100,
        constraint_params["industry_max_dev"] * 100,
    )
    log.info("λ 网格：%s", LAMBDA_GRID)
    log.info("训练期：%s ~ %s", cfg.TRAIN_START.date(), cfg.TRAIN_END.date())
    log.info("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 数据加载（与 run_optimizer_grid.py 相同逻辑）
    # ------------------------------------------------------------------
    if not SIGNAL_IC_IR_PATH.exists():
        log.error(
            "合成信号不存在：%s\n请先运行 run_signal_combination.py",
            SIGNAL_IC_IR_PATH,
        )
        sys.exit(1)

    composite_signal = pd.read_parquet(SIGNAL_IC_IR_PATH)
    train_mask = (
        (composite_signal.index >= cfg.TRAIN_START)
        & (composite_signal.index <= cfg.TRAIN_END)
    )
    composite_signal = composite_signal.loc[train_mask]
    log.info("训练期调仓日：%d 期", len(composite_signal))

    log.info("读取 index_member 及行业数据...")
    index_member_raw = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    industry_raw     = pd.read_parquet(cfg.DATA_PROC / "industry.parquet")

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
    halt_dict:              dict = {}
    limit_up_dict:          dict = {}
    limit_dn_dict:          dict = {}
    industry_dict:          dict = {}
    codes_per_date:         dict = {}

    for T in available_dates:
        snap = index_member_raw.loc[T]
        w_b  = snap["index_weight"] / 100.0
        w_b  = w_b / w_b.sum()
        benchmark_weights_dict[T] = w_b
        halt_dict[T]      = set(snap.index[snap["is_suspended"]])
        limit_up_dict[T]  = set(snap.index[snap["is_limit_up_locked"]])
        limit_dn_dict[T]  = set(snap.index[snap["is_limit_down_locked"]])
        ind_dates         = industry_pivot.index[industry_pivot.index <= T]
        industry_dict[T]  = (
            industry_pivot.loc[ind_dates[-1]].dropna()
            if len(ind_dates) > 0
            else pd.Series(dtype=object)
        )
        codes_per_date[T] = list(w_b.index)

    cov_cache = _load_cov_cache(available_dates, codes_per_date)

    # ------------------------------------------------------------------
    # λ_TC 网格主循环
    # ------------------------------------------------------------------
    all_summaries: list[dict] = []

    for lam in LAMBDA_GRID:
        label = f"lambda={lam:.4f}"
        opt_config = OptimizeConfig(
            te_target_annual = constraint_params["te_target_annual"],
            single_max_dev   = constraint_params["single_max_dev"],
            industry_max_dev = constraint_params["industry_max_dev"],
            topn             = cfg.OPT_TOPN,
            turnover_lambda  = lam,
        )

        log.info("\n%s\n[%s] λ_TC = %.4f", "─" * 60, label, lam)

        meta_df, summary, nav = _run_single_lambda(
            label=label,
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

        file_label = _lam_label(lam)
        meta_df.to_csv(OUTPUT_DIR / f"{file_label}_meta.csv")
        nav.to_frame(name=label).to_csv(OUTPUT_DIR / f"{file_label}_nav.csv")
        all_summaries.append(summary)
        log.info("[%s] 结果已保存至 %s", label, OUTPUT_DIR)

    # ------------------------------------------------------------------
    # 汇总与推荐
    # ------------------------------------------------------------------
    summary_df = pd.DataFrame(all_summaries)
    summary_df.to_csv(OUTPUT_DIR / "lambda_summary.csv", index=False)

    recommended_lambda = _recommend_lambda(summary_df)
    _print_summary(summary_df, winning_config, recommended_lambda)

    log.info("=" * 60)
    log.info("阶段 2 完成，完整结果目录：%s", OUTPUT_DIR)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
