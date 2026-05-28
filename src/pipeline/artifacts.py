from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

REQUIRED_SIGNAL_ARTIFACTS = ["signal/composite.parquet"]
REQUIRED_PORTFOLIO_ARTIFACTS = ["portfolio/target_weights.parquet"]
REQUIRED_BACKTEST_ARTIFACTS = [
    "backtest/nav_valid.parquet",
    "backtest/metrics_valid.parquet",
]
REQUIRED_FULL_RUN_ARTIFACTS = (
    REQUIRED_SIGNAL_ARTIFACTS
    + REQUIRED_PORTFOLIO_ARTIFACTS
    + REQUIRED_BACKTEST_ARTIFACTS
    + ["reports/self_check.md"]
)


@dataclass
class ArtifactLayout:
    run_dir: Path

    def path(self, relative: str) -> Path:
        return self.run_dir / relative

    def signal_dir(self) -> Path:
        return self.run_dir / "signal"

    def portfolio_dir(self) -> Path:
        return self.run_dir / "portfolio"

    def backtest_dir(self) -> Path:
        return self.run_dir / "backtest"

    def attribution_dir(self) -> Path:
        return self.run_dir / "attribution"

    def reports_dir(self) -> Path:
        return self.run_dir / "reports"

    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.json"

    def run_finished_path(self) -> Path:
        return self.run_dir / "RUN_FINISHED.json"

    def run_failed_path(self) -> Path:
        return self.run_dir / "RUN_FAILED.json"

    def run_config_path(self) -> Path:
        return self.run_dir / "run_config.json"

    def inputs_lock_path(self) -> Path:
        return self.run_dir / "inputs.lock.json"

    def create_dirs(self) -> None:
        for subdir in ["signal", "portfolio", "backtest", "attribution", "reports"]:
            (self.run_dir / subdir).mkdir(parents=True, exist_ok=True)

    def write_run_finished(self) -> None:
        # Idempotent: never overwrite an existing finished marker
        if self.run_finished_path().exists():
            return
        payload = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "run_dir": str(self.run_dir),
        }
        self.run_finished_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def write_run_failed(self, reason: str) -> None:
        payload = {
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "run_dir": str(self.run_dir),
        }
        self.run_failed_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def write_manifest(self, entries: dict) -> None:
        self.manifest_path().write_text(json.dumps(entries, indent=2), encoding="utf-8")

    def check_artifacts_present(self, artifact_list: List[str]) -> List[str]:
        """Return the subset of artifact_list that does not exist under run_dir."""
        return [a for a in artifact_list if not (self.run_dir / a).exists()]

    def is_finished(self) -> bool:
        return self.run_finished_path().exists()


def file_sha256(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
