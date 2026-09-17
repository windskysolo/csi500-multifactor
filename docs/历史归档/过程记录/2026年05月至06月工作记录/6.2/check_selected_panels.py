"""检查 test_panel_dir 中 18 个选用因子的覆盖情况。"""
import json, sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
from src import config as cfg

test_panel_dir = cfg.DATA_PROC / "factor_panels_test_run_1"
eval_path = ROOT / "reports" / "factor_evaluation" / "final_factors.json"
selected = json.loads(eval_path.read_text(encoding="utf-8"))["final_factors"]

print(f"选用因子({len(selected)}):\n  {selected}\n")
print(f"{'因子':<30} {'行数':>6} {'最早':>12} {'最晚':>12} {'测试期全空月':>12} {'状态'}")
print("-"*80)

issues = []
for f in sorted(selected):
    fp = test_panel_dir / f"{f}.parquet"
    if not fp.exists():
        print(f"  {f:<28} {'NOT FOUND':>6}")
        issues.append(f"{f}: 文件不存在")
        continue
    df = pd.read_parquet(fp)
    last = df.index.max()
    test_rows = df.loc["2023":]
    empty = test_rows.isna().all(axis=1).sum()
    status = "OK" if last >= pd.Timestamp("2025-01-01") and empty == 0 else "FAIL"
    if status == "FAIL":
        issues.append(f"{f}: last={last.date()}, empty_months={empty}")
    print(f"  {f:<28} {len(df):>6} {df.index.min().date()!s:>12} {last.date()!s:>12} {empty:>12} {status}")

print()
if issues:
    print(f"FAIL items ({len(issues)}):")
    for x in issues: print(f"  {x}")
else:
    print("所有 18 个选用因子面板在 test_panel_dir 中均完整覆盖测试期。")
