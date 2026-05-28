"""L5 框架层测试：ArtifactLayout 路径生成、manifest 写入、RUN_FINISHED 幂等性。"""
import json
import pytest
from src.pipeline.artifacts import (
    ArtifactLayout,
    REQUIRED_BACKTEST_ARTIFACTS,
    REQUIRED_FULL_RUN_ARTIFACTS,
    REQUIRED_SIGNAL_ARTIFACTS,
    file_sha256,
)


def test_artifact_layout_path_helpers(tmp_path):
    layout = ArtifactLayout(tmp_path / "run1")
    assert layout.signal_dir() == tmp_path / "run1" / "signal"
    assert layout.portfolio_dir() == tmp_path / "run1" / "portfolio"
    assert layout.backtest_dir() == tmp_path / "run1" / "backtest"
    assert layout.attribution_dir() == tmp_path / "run1" / "attribution"
    assert layout.reports_dir() == tmp_path / "run1" / "reports"
    assert layout.run_finished_path() == tmp_path / "run1" / "RUN_FINISHED.json"
    assert layout.manifest_path() == tmp_path / "run1" / "manifest.json"
    assert layout.run_config_path() == tmp_path / "run1" / "run_config.json"
    assert layout.inputs_lock_path() == tmp_path / "run1" / "inputs.lock.json"


def test_create_dirs_makes_all_subdirs(tmp_path):
    layout = ArtifactLayout(tmp_path / "run1")
    layout.create_dirs()
    for subdir in ["signal", "portfolio", "backtest", "attribution", "reports"]:
        assert (tmp_path / "run1" / subdir).is_dir()


def test_write_run_finished_creates_valid_json(tmp_path):
    layout = ArtifactLayout(tmp_path / "run1")
    layout.create_dirs()
    layout.write_run_finished()
    assert layout.run_finished_path().exists()
    data = json.loads(layout.run_finished_path().read_text())
    assert "finished_at" in data
    assert "run_dir" in data


def test_write_run_finished_is_idempotent(tmp_path):
    layout = ArtifactLayout(tmp_path / "run1")
    layout.create_dirs()
    layout.write_run_finished()
    original = layout.run_finished_path().read_text()
    layout.write_run_finished()  # second call must not overwrite
    assert layout.run_finished_path().read_text() == original


def test_write_run_failed_creates_valid_json(tmp_path):
    layout = ArtifactLayout(tmp_path / "run1")
    layout.create_dirs()
    layout.write_run_failed("something went wrong")
    data = json.loads(layout.run_failed_path().read_text())
    assert data["reason"] == "something went wrong"
    assert "failed_at" in data


def test_is_finished_false_before_write(tmp_path):
    layout = ArtifactLayout(tmp_path / "run1")
    assert not layout.is_finished()


def test_is_finished_true_after_write(tmp_path):
    layout = ArtifactLayout(tmp_path / "run1")
    layout.create_dirs()
    layout.write_run_finished()
    assert layout.is_finished()


def test_check_artifacts_present_reports_missing(tmp_path):
    layout = ArtifactLayout(tmp_path / "run1")
    missing = layout.check_artifacts_present(["signal/composite.parquet", "manifest.json"])
    assert "signal/composite.parquet" in missing
    assert "manifest.json" in missing


def test_check_artifacts_present_none_missing(tmp_path):
    layout = ArtifactLayout(tmp_path / "run1")
    layout.create_dirs()
    (layout.signal_dir() / "composite.parquet").write_bytes(b"fake_parquet")
    missing = layout.check_artifacts_present(REQUIRED_SIGNAL_ARTIFACTS)
    assert missing == []


def test_write_manifest_writes_valid_json(tmp_path):
    layout = ArtifactLayout(tmp_path / "run1")
    layout.create_dirs()
    entries = {"signal/composite.parquet": {"sha256": "abc", "rows": 100}}
    layout.write_manifest(entries)
    data = json.loads(layout.manifest_path().read_text())
    assert data["signal/composite.parquet"]["sha256"] == "abc"


def test_file_sha256_returns_none_for_missing_file(tmp_path):
    assert file_sha256(tmp_path / "nonexistent.parquet") is None


def test_file_sha256_is_deterministic(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"hello world 12345")
    h1 = file_sha256(f)
    h2 = file_sha256(f)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest length


def test_file_sha256_changes_with_content(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"version1")
    h1 = file_sha256(f)
    f.write_bytes(b"version2")
    h2 = file_sha256(f)
    assert h1 != h2


def test_required_artifact_constants_not_empty():
    assert len(REQUIRED_SIGNAL_ARTIFACTS) > 0
    assert len(REQUIRED_BACKTEST_ARTIFACTS) > 0
    assert len(REQUIRED_FULL_RUN_ARTIFACTS) >= len(REQUIRED_SIGNAL_ARTIFACTS) + len(REQUIRED_BACKTEST_ARTIFACTS)
