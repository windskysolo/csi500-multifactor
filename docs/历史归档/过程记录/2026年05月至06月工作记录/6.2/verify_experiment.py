"""验证 hk_quarterly 实验是否正确运行。"""
import pandas as pd
import numpy as np
from pathlib import Path

RUN_DIR = Path("runs/train_valid/20260602_104439__rolling48_topn150_ew_hk_quarterly")

# 1. manifest.json - 确认 spec 参数
import json
with open(RUN_DIR / "manifest.json") as f:
    manifest = json.load(f)
print("=== manifest.json ===")
print(f"experiment_id: {manifest.get('experiment_id')}")
sig = manifest.get("signal", {})
print(f"signal.method: {sig.get('method')}")
print(f"signal.training_mode: {sig.get('training_mode')}")
print(f"signal.window_months: {sig.get('window_months')}")
print(f"optimizer.topn: {manifest.get('optimizer', {}).get('topn')}")
print(f"optimizer.mode: {manifest.get('optimizer', {}).get('optimizer_mode')}")

# 2. coef_history.parquet - Ridge 系数（确认 hk_hold 有非零权重）
print()
print("=== Ridge 系数（coef_history.parquet）===")
coef = pd.read_parquet(RUN_DIR / "signal" / "coef_history.parquet")
print(f"Shape: {coef.shape}  (dates x factors)")
print(f"Factors ({len(coef.columns)}): {list(coef.columns)}")
print()
# hk_hold 系数统计
hk_cols = [c for c in coef.columns if "hk_hold" in c]
print(f"hk_hold 因子列: {hk_cols}")
for col in hk_cols:
    series = coef[col].dropna()
    nonzero = (series.abs() > 1e-8).sum()
    print(f"  {col}: mean={series.mean():.4f}, std={series.std():.4f}, nonzero={nonzero}/{len(series)}")

# 3. composite.parquet - 信号形状与日期范围
print()
print("=== composite signal ===")
sig_df = pd.read_parquet(RUN_DIR / "signal" / "composite.parquet")
print(f"Shape: {sig_df.shape}")
print(f"Date range: {sig_df.index.min().date()} ~ {sig_df.index.max().date()}")
valid_pct = sig_df.notna().mean().mean()
print(f"Valid cell pct: {valid_pct:.1%}")

# 4. backtest metrics - 验证期指标
print()
print("=== backtest metrics ===")
metrics = pd.read_parquet(RUN_DIR / "backtest" / "metrics_valid.parquet")
print(metrics.to_string())

# 5. 与参照实验 rolling48_topn150_ew 的 coef 对比
print()
print("=== 与参照 run coef 均值对比（前10个因子）===")
ref_coef = pd.read_parquet(
    "runs/train_valid/20260530_111129__rolling48_topn150_ew/signal/coef_history.parquet"
)
comparison = pd.DataFrame({
    "hk_quarterly": coef.mean(),
    "reference": ref_coef.mean()
}).dropna()
print(comparison.head(15).to_string())
