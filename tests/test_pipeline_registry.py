"""
tests/test_pipeline_registry.py — registry 单元测试

覆盖：
  - promote_run() 测试集 scope 拦截（scope="test", allow_test_set=False）
  - promote_run() 缺少 run 目录 / RUN_FINISHED / self_check / artifacts 的拦截
  - promote_run() 成功写入 mainline.json 的正确字段
  - promote_run() 成功追加 archived_promotions.jsonl
  - load_mainline() / load_challengers() 基础读取
"""

import json
from pathlib import Path

import pytest

from src.pipeline.registry import (
    _DEFAULT_REGISTRY_DIR,
    load_challengers,
    load_mainline,
    promote_run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_complete_run(runs_root: Path, scope: str, run_id: str) -> Path:
    """在 tmp 目录下创建一个满足所有 _validate_promotion 检查的完整 run。"""
    run_dir = runs_root / scope / run_id
    for rel in [
        "signal/composite.parquet",
        "portfolio/target_weights.parquet",
        "backtest/nav_valid.parquet",
        "backtest/metrics_valid.parquet",
        "reports/self_check.md",
    ]:
        p = run_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    (run_dir / "RUN_FINISHED.json").write_text("{}", encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------------------
# 测试集 scope 拦截
# ---------------------------------------------------------------------------

class TestTestSetGuard:
    def test_promote_test_scope_without_flag_raises(self, tmp_path: Path) -> None:
        """scope='test' 且 allow_test_set=False 必须立即 ValueError，不写任何文件。"""
        runs_root = tmp_path / "runs"
        _make_complete_run(runs_root, "test", "run_test_001")
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        with pytest.raises(ValueError, match="allow_test_set"):
            promote_run(
                run_id="run_test_001",
                slot="mainline",
                reason="trying to sneak test scope in",
                runs_root=runs_root,
                registry_dir=reg_dir,
                allow_test_set=False,
                scope="test",
            )

        assert not (reg_dir / "mainline.json").exists(), \
            "mainline.json 不应在被拒绝后创建"

    def test_promote_train_valid_scope_passes_guard(self, tmp_path: Path) -> None:
        """scope='train_valid' 不触发测试集 guard。"""
        runs_root = tmp_path / "runs"
        _make_complete_run(runs_root, "train_valid", "run_tv_001")
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        promote_run(
            run_id="run_tv_001",
            slot="mainline",
            reason="normal promotion",
            runs_root=runs_root,
            registry_dir=reg_dir,
            allow_test_set=False,
            scope="train_valid",
        )
        assert (reg_dir / "mainline.json").exists()

    def test_promote_test_scope_with_explicit_flag_passes(self, tmp_path: Path) -> None:
        """scope='test' 且 allow_test_set=True 允许通过。"""
        runs_root = tmp_path / "runs"
        _make_complete_run(runs_root, "test", "run_test_002")
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        promote_run(
            run_id="run_test_002",
            slot="mainline",
            reason="intentional test-set promotion",
            runs_root=runs_root,
            registry_dir=reg_dir,
            allow_test_set=True,
            scope="test",
        )
        assert (reg_dir / "mainline.json").exists()


# ---------------------------------------------------------------------------
# _validate_promotion 拦截：结构缺失
# ---------------------------------------------------------------------------

class TestValidatePromotion:
    def test_missing_run_dir_raises(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        with pytest.raises(RuntimeError, match="not found"):
            promote_run(
                run_id="nonexistent_run",
                slot="mainline",
                reason="test",
                runs_root=runs_root,
                registry_dir=reg_dir,
            )

    def test_missing_run_finished_raises(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        run_dir = _make_complete_run(runs_root, "train_valid", "run_no_finished")
        (run_dir / "RUN_FINISHED.json").unlink()
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        with pytest.raises(RuntimeError, match="RUN_FINISHED"):
            promote_run(
                run_id="run_no_finished",
                slot="mainline",
                reason="test",
                runs_root=runs_root,
                registry_dir=reg_dir,
            )

    def test_missing_self_check_raises(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        run_dir = _make_complete_run(runs_root, "train_valid", "run_no_check")
        (run_dir / "reports" / "self_check.md").unlink()
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        with pytest.raises(RuntimeError, match="self_check"):
            promote_run(
                run_id="run_no_check",
                slot="mainline",
                reason="test",
                runs_root=runs_root,
                registry_dir=reg_dir,
            )

    def test_missing_artifact_raises(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        run_dir = _make_complete_run(runs_root, "train_valid", "run_missing_art")
        (run_dir / "signal" / "composite.parquet").unlink()
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        with pytest.raises(RuntimeError, match="Missing required artifacts"):
            promote_run(
                run_id="run_missing_art",
                slot="mainline",
                reason="test",
                runs_root=runs_root,
                registry_dir=reg_dir,
            )

    def test_unknown_slot_raises(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        _make_complete_run(runs_root, "train_valid", "run_bad_slot")
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        with pytest.raises(ValueError, match="slot"):
            promote_run(
                run_id="run_bad_slot",
                slot="challenger",
                reason="test",
                runs_root=runs_root,
                registry_dir=reg_dir,
            )


# ---------------------------------------------------------------------------
# 成功晋升后的产物校验
# ---------------------------------------------------------------------------

class TestSuccessfulPromotion:
    def test_mainline_json_fields(self, tmp_path: Path) -> None:
        """成功晋升后 mainline.json 必须包含所有必要字段且值正确。"""
        runs_root = tmp_path / "runs"
        _make_complete_run(runs_root, "train_valid", "run_good_001")
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        promote_run(
            run_id="run_good_001",
            slot="mainline",
            reason="baseline passed all gates",
            runs_root=runs_root,
            registry_dir=reg_dir,
            allow_test_set=False,
            scope="train_valid",
        )

        entry = json.loads((reg_dir / "mainline.json").read_text(encoding="utf-8"))
        assert entry["slot"] == "mainline"
        assert entry["active_run_id"] == "run_good_001"
        assert entry["scope"] == "train_valid"
        assert entry["test_set_used"] is False
        assert entry["reason"] == "baseline passed all gates"
        assert "promoted_at" in entry

    def test_archived_promotions_appended(self, tmp_path: Path) -> None:
        """每次晋升都追加一行到 archived_promotions.jsonl。"""
        runs_root = tmp_path / "runs"
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        for i in range(3):
            _make_complete_run(runs_root, "train_valid", f"run_{i:03d}")
            promote_run(
                run_id=f"run_{i:03d}",
                slot="mainline",
                reason=f"promotion {i}",
                runs_root=runs_root,
                registry_dir=reg_dir,
            )

        lines = (reg_dir / "archived_promotions.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            record = json.loads(line)
            assert record["slot"] == "mainline"

    def test_repeated_promotion_overwrites_mainline(self, tmp_path: Path) -> None:
        """第二次晋升应覆盖 mainline.json，指向新 run_id。"""
        runs_root = tmp_path / "runs"
        reg_dir = tmp_path / "registry"
        reg_dir.mkdir()

        for run_id in ("run_v1", "run_v2"):
            _make_complete_run(runs_root, "train_valid", run_id)
            promote_run(run_id=run_id, slot="mainline", reason="update",
                        runs_root=runs_root, registry_dir=reg_dir)

        entry = json.loads((reg_dir / "mainline.json").read_text(encoding="utf-8"))
        assert entry["active_run_id"] == "run_v2"


# ---------------------------------------------------------------------------
# load_mainline / load_challengers
# ---------------------------------------------------------------------------

class TestLoadFunctions:
    def test_load_mainline_returns_dict(self) -> None:
        """load_mainline() 从项目 registry/ 读取，返回 dict。"""
        entry = load_mainline()
        assert isinstance(entry, dict)
        assert "active_run_id" in entry

    def test_load_mainline_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_mainline(registry_dir=tmp_path)

    def test_load_challengers_returns_dict_with_list(self) -> None:
        """load_challengers() 返回含 challengers 键的 dict。"""
        data = load_challengers()
        assert isinstance(data, dict)
        assert "challengers" in data
        assert isinstance(data["challengers"], list)

    def test_load_challengers_missing_returns_empty(self, tmp_path: Path) -> None:
        """registry 目录没有 challengers.json 时返回空列表而非抛异常。"""
        data = load_challengers(registry_dir=tmp_path)
        assert data == {"challengers": []}
