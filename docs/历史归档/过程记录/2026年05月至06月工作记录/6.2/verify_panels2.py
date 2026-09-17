"""进一步调查 hk_hold 面板的全空月分布。"""
import pandas as pd

ratio = pd.read_parquet("data/processed/factor_panels/hk_hold_ratio.parquet")
chg   = pd.read_parquet("data/processed/factor_panels/hk_hold_chg.parquet")

print("=== ratio 全空月列表 (2015+) ===")
empty_months = ratio.loc["2015":].isna().all(axis=1)
print(empty_months[empty_months].index.tolist())

print()
print("=== chg 全空月列表 (2016+) ===")
empty_chg = chg.loc["2016":].isna().all(axis=1)
print(empty_chg[empty_chg].index.tolist())

print()
print("=== 2015-2016 每月有效股票数 (ratio) ===")
counts_early = ratio.loc["2015":"2016"].notna().sum(axis=1)
print(counts_early.to_string())

print()
print("=== 训练期各年平均有效股票数 (ratio) ===")
for year in range(2014, 2023):
    yr = ratio.loc[str(year)]
    mean_valid = yr.notna().sum(axis=1).mean()
    n_empty = yr.isna().all(axis=1).sum()
    print(f"  {year}: avg_valid={mean_valid:.0f}, empty_months={n_empty}")
