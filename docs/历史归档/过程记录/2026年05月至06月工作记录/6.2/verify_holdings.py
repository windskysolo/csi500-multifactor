"""验证 hk_quarterly 实验每期实际持仓数量及候选池大小。"""
import pandas as pd
import numpy as np
from pathlib import Path

RUN_DIR = Path("runs/train_valid/20260602_104439__rolling48_topn150_ew_hk_quarterly")
REF_DIR = Path("runs/train_valid/20260530_111129__rolling48_topn150_ew")

# ── 1. 目标权重：每期持仓数 ──────────────────────────────────────────────────
weights = pd.read_parquet(RUN_DIR / "portfolio" / "target_weights.parquet")
holdings_per_period = (weights > 1e-8).sum(axis=1)
print("=== 每期持仓数（target_weights > 0）===")
print(f"总期数:       {len(holdings_per_period)}")
print(f"最小持仓数:   {holdings_per_period.min()}")
print(f"最大持仓数:   {holdings_per_period.max()}")
print(f"均值持仓数:   {holdings_per_period.mean():.1f}")
print(f"持仓=150 的期数: {(holdings_per_period == 150).sum()}/{len(holdings_per_period)}")
print(f"持仓<150 的期数: {(holdings_per_period < 150).sum()}")
print()
if (holdings_per_period < 150).any():
    short_periods = holdings_per_period[holdings_per_period < 150]
    print("持仓不足150 的期：")
    print(short_periods.to_string())
    print()

# ── 2. 复合信号：每期有效信号数（候选池大小）──────────────────────────────────
sig = pd.read_parquet(RUN_DIR / "signal" / "composite.parquet")
valid_signal_per_period = sig.notna().sum(axis=1)
print("=== 每期有效信号数（候选池大小）===")
print(f"最小有效信号数: {valid_signal_per_period.min()}")
print(f"最大有效信号数: {valid_signal_per_period.max()}")
print(f"均值有效信号数: {valid_signal_per_period.mean():.1f}")
print(f"有效信号数 < 150 的期数: {(valid_signal_per_period < 150).sum()}")
print()

# ── 3. 验证期持仓分布 ─────────────────────────────────────────────────────────
valid_weights = weights.loc["2021":"2022"]
valid_holdings = (valid_weights > 1e-8).sum(axis=1)
print("=== 验证期（2021-2022）每月持仓数 ===")
print(valid_holdings.to_string())
print()

# ── 4. 与参照 run 的候选池对比 ────────────────────────────────────────────────
print("=== 候选池大小对比（hk_quarterly vs reference）===")
ref_sig = pd.read_parquet(REF_DIR / "signal" / "composite.parquet")
ref_valid = ref_sig.notna().sum(axis=1)

# 取共同日期
common_dates = sig.index.intersection(ref_sig.index)
comparison = pd.DataFrame({
    "hk_quarterly": valid_signal_per_period.reindex(common_dates),
    "reference":    ref_valid.reindex(common_dates),
})
comparison["diff"] = comparison["hk_quarterly"] - comparison["reference"]
print(f"hk_quarterly 平均候选池: {comparison['hk_quarterly'].mean():.0f}")
print(f"reference    平均候选池: {comparison['reference'].mean():.0f}")
print(f"差值         均值:       {comparison['diff'].mean():.0f}")
print()

# ── 5. hk_hold 在候选池中的影响：有无 hk_hold 信号的股票分布 ──────────────────
# 取最近一个完整年份（2022）的信号
sig_2022 = sig.loc["2022"]
hk_ratio_panel = pd.read_parquet("data/processed/factor_panels/hk_hold_ratio.parquet")
hk_2022 = hk_ratio_panel.loc["2022"]

# 每期：有 hk_hold 信号 vs 无 hk_hold 信号的股票数
common_dates_2022 = sig_2022.index.intersection(hk_2022.index)
print("=== 2022 年：信号有效性拆解（以 hk_hold_ratio 为例）===")
for d in common_dates_2022:
    has_composite = sig_2022.loc[d].notna()
    has_hk = hk_2022.loc[d].notna()
    n_total   = has_composite.sum()
    n_with_hk = (has_composite & has_hk).sum()
    n_no_hk   = (has_composite & ~has_hk).sum()
    print(f"  {d.date()}: composite={n_total}, with_hk={n_with_hk}, no_hk={n_no_hk}")
