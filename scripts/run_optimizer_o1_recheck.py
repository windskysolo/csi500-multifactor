"""
scripts/run_optimizer_o1_recheck.py — 仅重跑 O1 配置（Bug 修复后基线更新）

用途：
  优化器 Bug-1/Bug-2 修复后，只重新跑 O1 来更新基线数字，避免重跑全部三个配置。
  O0/O2 的历史结果保留在 grid_summary.csv 的对应行中。

用法：
  python -m scripts.run_optimizer_o1_recheck
"""

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import scripts.run_optimizer_grid as grid_mod

# 仅保留 O1
grid_mod.GRID_CONFIGS = {"O1": grid_mod.GRID_CONFIGS["O1"]}

OUTPUT_DIR = _ROOT / "reports" / "optimizer_grid"
SUMMARY_PATH = OUTPUT_DIR / "grid_summary.csv"

# 保留现有 O0/O2 行（如果存在）
old_summary: pd.DataFrame | None = None
if SUMMARY_PATH.exists():
    old_summary = pd.read_csv(SUMMARY_PATH, index_col=0)

# 运行 O1（会覆盖 O1_meta.csv / O1_nav.csv，并写只含 O1 的 grid_summary.csv）
grid_mod.main()

# 合并：把旧的 O0/O2 行追加回 grid_summary.csv
if old_summary is not None:
    new_summary = pd.read_csv(SUMMARY_PATH, index_col=0)
    for config in ["O0", "O2"]:
        if config in old_summary.index and config not in new_summary.index:
            new_summary = pd.concat([new_summary, old_summary.loc[[config]]])
    new_summary = new_summary.loc[
        [c for c in ["O0", "O1", "O2"] if c in new_summary.index]
    ]
    new_summary.to_csv(SUMMARY_PATH)
    print(f"\ngrid_summary.csv 已合并（O0/O2 保留，O1 已更新）: {SUMMARY_PATH}")


if __name__ == "__main__":
    pass
