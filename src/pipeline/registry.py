from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Project root is two levels above src/pipeline/
_PROJECT_ROOT = Path(__file__).parents[2]
_DEFAULT_REGISTRY_DIR = _PROJECT_ROOT / "registry"
_DEFAULT_RUNS_ROOT = _PROJECT_ROOT / "runs"


def load_mainline(registry_dir: Optional[Path] = None) -> dict:
    """Load the current mainline registry entry."""
    path = (registry_dir or _DEFAULT_REGISTRY_DIR) / "mainline.json"
    if not path.exists():
        raise FileNotFoundError(f"mainline.json not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_challengers(registry_dir: Optional[Path] = None) -> dict:
    """Load the challenger registry. Returns empty list if file not found."""
    path = (registry_dir or _DEFAULT_REGISTRY_DIR) / "challengers.json"
    if not path.exists():
        return {"challengers": []}
    return json.loads(path.read_text(encoding="utf-8"))


def promote_run(
    run_id: str,
    slot: str,
    reason: str,
    runs_root: Optional[Path] = None,
    registry_dir: Optional[Path] = None,
    allow_test_set: bool = False,
    scope: str = "train_valid",
) -> None:
    """
    Promote a finished run to the mainline registry.

    Args:
        run_id: The run ID to promote.
        slot: Must be "mainline".
        reason: Human-readable reason for promotion.
        runs_root: Root of runs/ directory. Defaults to project runs/.
        registry_dir: Registry directory. Defaults to project registry/.
        allow_test_set: Allow promoting a run that used the test set.
        scope: Either "train_valid" or "test".
    """
    reg_dir = registry_dir or _DEFAULT_REGISTRY_DIR
    root = runs_root or _DEFAULT_RUNS_ROOT
    run_dir = root / scope / run_id

    if scope == "test" and not allow_test_set:
        raise ValueError(
            f"Run '{run_id}' is in scope='test' but allow_test_set=False. "
            "Pass allow_test_set=True explicitly to promote a test-set run."
        )

    _validate_promotion(run_dir, allow_test_set)

    if slot != "mainline":
        raise ValueError(
            f"Unknown slot '{slot}'. Use 'mainline'; manage challengers.json directly."
        )

    entry = {
        "slot": slot,
        "active_run_id": run_id,
        "scope": scope,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "promoted_by": "manual",
        "test_set_used": allow_test_set,
    }

    (reg_dir / "mainline.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")
    _append_promotion_log(reg_dir, entry)


def _validate_promotion(run_dir: Path, allow_test_set: bool) -> None:
    from src.pipeline.artifacts import REQUIRED_FULL_RUN_ARTIFACTS, ArtifactLayout

    if not run_dir.exists():
        raise RuntimeError(f"Run directory not found: {run_dir}")

    run_finished = run_dir / "RUN_FINISHED.json"
    if not run_finished.exists():
        raise RuntimeError(
            f"RUN_FINISHED.json not found in {run_dir}. Run is not complete."
        )

    self_check = run_dir / "reports" / "self_check.md"
    if not self_check.exists():
        raise RuntimeError(
            f"reports/self_check.md not found in {run_dir}. Cannot promote without self-check."
        )

    layout = ArtifactLayout(run_dir)
    missing = layout.check_artifacts_present(REQUIRED_FULL_RUN_ARTIFACTS)
    if missing:
        raise RuntimeError(f"Missing required artifacts for promotion: {missing}")


def _append_promotion_log(reg_dir: Path, entry: dict) -> None:
    log_path = reg_dir / "archived_promotions.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
