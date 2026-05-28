"""
测试集运行次数 ledger。

测试集是否已使用以 `docs/check/test_set_runs.json` 的 active_runs 为准。
历史 git 中的错误 `[TEST_SET_RUN_N]` 标签不再作为运行次数来源。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
LEDGER_PATH = ROOT / "docs" / "logs" / "test_set_runs.json"
MAX_TEST_SET_RUNS = 2


def load_test_set_ledger() -> dict[str, Any]:
    """读取测试集 ledger；文件缺失时按 0 次处理。"""
    if not LEDGER_PATH.exists():
        return {"max_runs": MAX_TEST_SET_RUNS, "active_runs": []}
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"max_runs": MAX_TEST_SET_RUNS, "active_runs": []}


def count_test_set_runs() -> int:
    """返回已登记的有效测试集运行次数。"""
    ledger = load_test_set_ledger()
    runs = ledger.get("active_runs", [])
    if not isinstance(runs, list):
        return 0
    return len([r for r in runs if isinstance(r, dict) and r.get("status") != "voided"])


def has_test_set_run(run_id: int) -> bool:
    """检查某个 run_id 是否已经在有效运行记录中。"""
    ledger = load_test_set_ledger()
    runs = ledger.get("active_runs", [])
    if not isinstance(runs, list):
        return False
    return any(
        isinstance(r, dict)
        and int(r.get("run_id", -1)) == run_id
        and r.get("status") != "voided"
        for r in runs
    )


def remaining_test_set_runs() -> int:
    """返回剩余测试集运行次数。"""
    return max(MAX_TEST_SET_RUNS - count_test_set_runs(), 0)


def record_test_set_run(run_id: int, status: str = "finished", git_commit: str = "unknown") -> None:
    """在 ledger 中登记一次有效测试集运行。"""
    ledger = load_test_set_ledger()
    ledger.setdefault("max_runs", MAX_TEST_SET_RUNS)
    runs = ledger.setdefault("active_runs", [])
    if not isinstance(runs, list):
        runs = []
        ledger["active_runs"] = runs

    entry = {
        "run_id": run_id,
        "status": status,
        "git_commit": git_commit,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    for idx, run in enumerate(runs):
        if isinstance(run, dict) and int(run.get("run_id", -1)) == run_id:
            runs[idx] = entry
            break
    else:
        runs.append(entry)

    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
