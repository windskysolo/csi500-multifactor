"""验证重建后 hk_hold 季度化因子面板覆盖率。"""
import pandas as pd

ratio = pd.read_parquet("data/processed/factor_panels/hk_hold_ratio.parquet")
chg   = pd.read_parquet("data/processed/factor_panels/hk_hold_chg.parquet")

print("=== hk_hold_ratio ===")
print(f"Shape: {ratio.shape}")
print(f"Date range: {ratio.index.min().date()} ~ {ratio.index.max().date()}")
print(f"2015+ coverage: {ratio.loc['2015':].notna().mean().mean():.1%}")

empty_ratio_2015 = ratio.loc["2015":].isna().all(axis=1)
print(f"Empty months (2015+): {empty_ratio_2015.sum()}")

empty_ratio_valid = ratio.loc["2021":"2022"].isna().all(axis=1)
print(f"Empty months (2021-2022 valid): {empty_ratio_valid.sum()}")

print()
print("=== hk_hold_chg ===")
print(f"Shape: {chg.shape}")
print(f"2016+ coverage: {chg.loc['2016':].notna().mean().mean():.1%}")

empty_chg_2016 = chg.loc["2016":].isna().all(axis=1)
print(f"Empty months (2016+): {empty_chg_2016.sum()}")

empty_chg_valid = chg.loc["2021":"2022"].isna().all(axis=1)
print(f"Empty months (2021-2022 valid): {empty_chg_valid.sum()}")

print()
print("=== 2021-2022 valid period monthly valid stock count (ratio) ===")
valid_counts = ratio.loc["2021":"2022"].notna().sum(axis=1)
print(valid_counts.to_string())
