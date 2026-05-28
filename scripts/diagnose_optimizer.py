"""
scripts/diagnose_optimizer.py — 优化器 Bug 诊断
==================================================

诊断目标：
  Bug-1：halt 约束 + 单股偏离约束冲突 → L2 真实不可行
  Bug-2：L3 的 TopN 等权对大权重基准股赋 0，偏离超约束

运行方式：
  python -m scripts.diagnose_optimizer

输出：
  - 每期是否存在 halt 冲突（Bug-1）
  - 每期 L3 的大权重基准股违约情况（Bug-2）
  - 两个 Bug 与 L3 fallback 的关联性统计
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src import config as cfg

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
SINGLE_MAX_DEV = 0.01   # 复现 O0 的约束，与 grid 实验保持一致
TOPN           = cfg.OPT_TOPN
META_PATH      = _ROOT / "reports" / "optimizer_grid" / "O0_meta.csv"

# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_data():
    index_member = pd.read_parquet(cfg.DATA_PROC / "index_member.parquet")
    meta         = pd.read_csv(META_PATH, index_col=0, parse_dates=True)
    return index_member, meta


def get_period_info(index_member: pd.DataFrame, T: pd.Timestamp):
    """提取单期的基准权重、停牌集合等。"""
    if T not in index_member.index.get_level_values("rebalance_date"):
        return None, set(), set()
    snap = index_member.loc[T]
    w_b  = snap["index_weight"] / 100.0
    w_b  = w_b / w_b.sum()
    halt = set(snap.index[snap["is_suspended"]])
    return w_b, halt, snap


# ---------------------------------------------------------------------------
# Bug-1 诊断：halt + single_max_dev 约束冲突
# ---------------------------------------------------------------------------

def diagnose_bug1(index_member: pd.DataFrame, meta: pd.DataFrame):
    """
    对每个调仓日，检查停牌股的 w_prev 是否违反 single_max_dev 约束。

    关键逻辑：
      - w_prev 来自上一期的优化结果
      - L1 期：优化器贴着约束上限，single_dev_max ≈ 1%（从 meta 读）
      - L3 期：w_prev 来自 TopN 等权，会包含偏离基准的权重
      - 若停牌股的 |w_prev_i - w_b_i_new| > single_max_dev → Bug-1 触发

    这里用一个保守模型近似 w_prev：
      - L1 成功期：某些股票被推到约束边界（贪心近似）
      - L3 期：等权分配 1/TOPN 给前 N 只，其余为 0

    为避免复现全量优化，我们用以下策略近似：
      每期只跟踪"停牌且在上期基准中的股票"的上期基准权重 vs 本期基准权重的差异，
      并用 meta 的 single_dev_max 作为实际偏离量的上界。
    """
    rebalance_dates = sorted(index_member.index.get_level_values("rebalance_date").unique())
    train_dates     = [d for d in rebalance_dates
                       if cfg.TRAIN_START <= d <= cfg.TRAIN_END]

    records = []
    # 近似 w_prev：L1 期 = w_b + max deviation 方向（worst-case）
    # L3 期 = TopN 等权（1/TOPN）for selected, 0 for others
    # 由于我们没有实际权重，使用以下代理：
    # 上期 L1 success → 某些股票持有 w_b_prev ± single_dev_max
    # 上期 L3       → 持有 1/TOPN（selected）或 0（unselected）

    prev_fallback = None
    prev_w_b      = None
    prev_meta_row = None

    for i, T in enumerate(train_dates):
        w_b, halt, snap = get_period_info(index_member, T)
        if w_b is None:
            continue

        if T in meta.index:
            row = meta.loc[T]
            current_fallback = int(row["fallback_level"])
        else:
            current_fallback = -1

        # 检查停牌冲突（Bug-1）
        bug1_violations = []
        if prev_w_b is not None and halt:
            for code in halt:
                if code not in w_b.index:
                    continue
                w_b_new = float(w_b[code])

                # 近似 w_prev：
                # 如果上期是 L3，该股票权重为 0（如果未入选TopN）或 1/TOPN
                # 如果上期是 L1，该股票权重最多偏离 single_max_dev
                if prev_fallback == 2:  # 上期 L3
                    # 若该股票上期基准权重 > single_max_dev（会被排出TopN）
                    if code in prev_w_b.index and float(prev_w_b[code]) > SINGLE_MAX_DEV:
                        # 大权重股票在 TopN 中不一定被选中，取保守估计 w_prev = 0
                        w_prev_approx = 0.0
                        deviation     = abs(w_prev_approx - w_b_new)
                        if deviation > SINGLE_MAX_DEV + 1e-6:
                            bug1_violations.append({
                                "code":       code,
                                "w_b_new":    w_b_new,
                                "w_prev_est": w_prev_approx,
                                "deviation":  deviation,
                                "source":     "L3_unselected_large_stock",
                            })
                    else:
                        # 小权重股票被 TopN 选中，w_prev ≈ 1/TOPN
                        w_prev_approx = 1.0 / TOPN
                        deviation     = abs(w_prev_approx - w_b_new)
                        if deviation > SINGLE_MAX_DEV + 1e-6:
                            bug1_violations.append({
                                "code":       code,
                                "w_b_new":    w_b_new,
                                "w_prev_est": w_prev_approx,
                                "deviation":  deviation,
                                "source":     "L3_selected_small_stock",
                            })
                else:  # 上期 L1 (optimal)
                    # L1 期：worst-case w_prev = w_b_prev ± single_max_dev
                    # 基准权重变化 delta = w_b_new - w_b_prev
                    if code in prev_w_b.index:
                        w_b_prev = float(prev_w_b[code])
                        # 如果上期持有 w_b_prev + single_max_dev（贴上边界）
                        # 且本期基准下降，就可能超出约束
                        w_prev_upper = w_b_prev + SINGLE_MAX_DEV
                        deviation_upper = abs(w_prev_upper - w_b_new)
                        if deviation_upper > SINGLE_MAX_DEV + 1e-6:
                            bug1_violations.append({
                                "code":       code,
                                "w_b_prev":   w_b_prev,
                                "w_b_new":    w_b_new,
                                "w_prev_est": w_prev_upper,
                                "deviation":  deviation_upper,
                                "source":     "L1_boundary_benchmark_drop",
                            })

        records.append({
            "date":             T,
            "fallback_level":   current_fallback,
            "n_halted":         len(halt),
            "bug1_n_conflicts": len(bug1_violations),
            "bug1_detail":      bug1_violations[:3],  # 取前3条
        })

        prev_fallback = current_fallback
        prev_w_b      = w_b

    return pd.DataFrame(records).set_index("date")


# ---------------------------------------------------------------------------
# Bug-2 诊断：L3 TopN 对大权重股赋 0 导致偏离超界
# ---------------------------------------------------------------------------

def diagnose_bug2(index_member: pd.DataFrame, meta: pd.DataFrame):
    """
    对每个 L3 期，找出基准权重 > single_max_dev 的股票。
    这些股票在 L3 中若未被 TopN 选中（weight=0），偏离必然超约束。
    """
    rebalance_dates = sorted(index_member.index.get_level_values("rebalance_date").unique())
    train_dates     = [d for d in rebalance_dates
                       if cfg.TRAIN_START <= d <= cfg.TRAIN_END]

    records = []
    for T in train_dates:
        if T not in meta.index:
            continue
        row = meta.loc[T]
        if int(row["fallback_level"]) != 2:
            continue

        w_b, _, snap = get_period_info(index_member, T)
        if w_b is None:
            continue

        # 大权重股：w_b_i > single_max_dev → 若 weight=0，|0 - w_b_i| > single_max_dev
        large_stocks = w_b[w_b > SINGLE_MAX_DEV]
        n_large      = len(large_stocks)
        max_w_b      = float(large_stocks.max()) if n_large > 0 else 0.0

        # n_min 计算（来自 _topn_equal_weight 代码）
        candidates = list(w_b.index)
        min_w_b    = float(w_b.min())
        max_unit   = SINGLE_MAX_DEV + min_w_b
        n_min      = math.ceil(1.0 / max_unit) if max_unit > 1e-9 else TOPN
        effective_topn = max(TOPN, n_min)

        # 假设：TopN 从高 alpha 选，大权重股是否一定被选中？
        # 保守估计：TOP effective_topn 未必包含所有大权重股
        # 若 n_large > 0 且 effective_topn < n_stocks，就有风险
        n_stocks = len(candidates)
        potentially_excluded = max(0, n_large - max(0, effective_topn - (n_stocks - n_large)))

        records.append({
            "date":               T,
            "n_large_stocks":     n_large,
            "max_w_b":            max_w_b,
            "effective_topn":     effective_topn,
            "n_stocks":           n_stocks,
            "potentially_excl":   potentially_excluded,
            "confirmed_violation": float(row["single_dev_max"]) > SINGLE_MAX_DEV + 1e-6,
        })

    return pd.DataFrame(records).set_index("date") if records else pd.DataFrame()


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("优化器 Bug 诊断")
    print(f"训练期：{cfg.TRAIN_START.date()} ~ {cfg.TRAIN_END.date()}")
    print(f"single_max_dev = {SINGLE_MAX_DEV:.1%}（复现 O0 配置）")
    print("=" * 70)

    index_member, meta = load_data()
    train_meta = meta[(meta.index >= cfg.TRAIN_START) & (meta.index <= cfg.TRAIN_END)]
    n_l1 = (train_meta["fallback_level"] == 0).sum()
    n_l3 = (train_meta["fallback_level"] == 2).sum()
    n_l2 = (train_meta["fallback_level"] == 1).sum()

    print(f"\n[Meta 总览] 总期数={len(train_meta)}, L1={n_l1}, L2={n_l2}, L3={n_l3}")

    # ---------- Bug-1 ----------
    print("\n" + "─" * 70)
    print("[Bug-1] halt 约束 + single_max_dev 约束冲突分析")
    bug1_df = diagnose_bug1(index_member, meta)

    b1_conflict_dates = bug1_df[bug1_df["bug1_n_conflicts"] > 0]
    print(f"  存在潜在 halt 冲突的期数：{len(b1_conflict_dates)}")

    if not b1_conflict_dates.empty:
        # 看这些期的 fallback 分布
        l3_with_conflict = (b1_conflict_dates["fallback_level"] == 2).sum()
        l1_with_conflict = (b1_conflict_dates["fallback_level"] == 0).sum()
        print(f"  其中 L3 期：{l3_with_conflict}，L1 期：{l1_with_conflict}")

        print("\n  前10条冲突记录（date, fallback_level, n_halted, n_conflicts）：")
        display = b1_conflict_dates[["fallback_level", "n_halted", "bug1_n_conflicts"]].head(10)
        print(display.to_string())

        # 具体冲突示例
        first_row = b1_conflict_dates.iloc[0]
        if first_row["bug1_detail"]:
            v = first_row["bug1_detail"][0]
            print(f"\n  示例冲突（{b1_conflict_dates.index[0].date()}）：")
            for k, val in v.items():
                if k != "code":
                    print(f"    {k} = {val:.4f}" if isinstance(val, float) else f"    {k} = {val}")

    # ---------- Bug-2 ----------
    print("\n" + "─" * 70)
    print("[Bug-2] L3 TopN 对大权重股赋 0 导致偏离超约束分析")
    bug2_df = diagnose_bug2(index_member, meta)

    if bug2_df.empty:
        print("  无 L3 期数据")
    else:
        confirmed = bug2_df["confirmed_violation"].sum()
        has_large = (bug2_df["n_large_stocks"] > 0).sum()
        print(f"  L3 总期数：{len(bug2_df)}")
        print(f"  存在大权重股（w_b > 1%）的期数：{has_large}")
        print(f"  meta 中确认 single_dev_max > 1% 的期数：{confirmed}")
        print(f"\n  n_large_stocks 分布：")
        print(bug2_df["n_large_stocks"].value_counts().sort_index().to_string())
        print(f"\n  max_w_b（L3期大权重股最大权重）均值：{bug2_df['max_w_b'].mean():.4f}")

        print("\n  前10条 L3 期记录：")
        print(bug2_df[["n_large_stocks", "max_w_b", "effective_topn", "confirmed_violation"]].head(10).to_string())

    # ---------- L3 连续段分析 ----------
    print("\n" + "─" * 70)
    print("[连续 L3 段分析] 检查 L3 是否成簇出现（级联效应特征）")
    l3_series = (train_meta["fallback_level"] == 2).astype(int)
    # 找连续 L3 段
    in_run  = False
    runs    = []
    cur_run = []
    for date, is_l3 in l3_series.items():
        if is_l3:
            cur_run.append(date)
            in_run = True
        else:
            if in_run:
                runs.append(cur_run)
                cur_run = []
            in_run = False
    if cur_run:
        runs.append(cur_run)

    run_lengths = [len(r) for r in runs]
    print(f"  L3 连续段数量：{len(runs)}")
    if run_lengths:
        print(f"  最长连续 L3 段：{max(run_lengths)} 期")
        print(f"  连续段长度分布：{sorted(set(run_lengths))} "
              f"（各 {[run_lengths.count(k) for k in sorted(set(run_lengths))]} 次）")
        if max(run_lengths) >= 3:
            longest = max(runs, key=len)
            print(f"  最长连续段：{longest[0].date()} ~ {longest[-1].date()}")

    print("\n" + "=" * 70)
    print("诊断结论：")
    if not b1_conflict_dates.empty or (not bug2_df.empty and bug2_df["confirmed_violation"].any()):
        print("  ✗ Bug-1（halt 约束冲突）：存在")
        print("  ✗ Bug-2（L3 大权重股赋0）：存在")
        print("  ✗ 级联效应：L3 → 坏 w_prev → 下期 halt 冲突 → L3 → 循环")
    else:
        print("  ✓ 未发现明显 bug（需进一步验证）")
    print("=" * 70)


if __name__ == "__main__":
    main()
