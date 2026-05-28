"""
tests/test_pipeline_release_gates.py — 阶段A专项：Pipeline 发布门禁测试

验证规则：
  F9-001: _check_artifacts() 失败时 _run_stage() 返回 False，manifest 写 success=false
  F9-002: run_attribution.py 在归因异常时退出非零码（无 --allow-partial）
  F9-006: manifest 包含 config_hash、skipped_stages、test_set_run_count 等字段

不依赖真实数据或网络，纯函数/行为测试。
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 项目根路径
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# F9-001: 关键产物缺失时 _run_stage 必须返回 False
# ---------------------------------------------------------------------------

class TestArtifactGates:
    """验证产物存在性检查是发布门禁，缺失时阶段必须失败。"""

    def test_check_artifacts_returns_false_when_file_missing(self, tmp_path: Path) -> None:
        """产物路径不存在时 _check_artifacts 必须返回 False。"""
        from scripts.run_pipeline import _ARTIFACT_CHECKS, _check_artifacts

        # 注入一个指向不存在文件的检查项
        _ARTIFACT_CHECKS["_test_stage_"] = [tmp_path / "nonexistent.parquet"]
        try:
            result = _check_artifacts("_test_stage_")
        finally:
            _ARTIFACT_CHECKS.pop("_test_stage_", None)

        assert result is False, "产物缺失时 _check_artifacts 应返回 False"

    def test_check_artifacts_returns_true_when_file_exists(self, tmp_path: Path) -> None:
        """产物存在时 _check_artifacts 应返回 True。"""
        from scripts.run_pipeline import _ARTIFACT_CHECKS, _check_artifacts

        existing = tmp_path / "exists.parquet"
        existing.touch()
        _ARTIFACT_CHECKS["_test_stage2_"] = [existing]
        try:
            result = _check_artifacts("_test_stage2_")
        finally:
            _ARTIFACT_CHECKS.pop("_test_stage2_", None)

        assert result is True

    def test_check_artifacts_returns_true_for_directory(self, tmp_path: Path) -> None:
        """产物是目录时，目录存在应返回 True。"""
        from scripts.run_pipeline import _ARTIFACT_CHECKS, _check_artifacts

        d = tmp_path / "factor_panels"
        d.mkdir()
        _ARTIFACT_CHECKS["_test_dir_stage_"] = [d]
        try:
            result = _check_artifacts("_test_dir_stage_")
        finally:
            _ARTIFACT_CHECKS.pop("_test_dir_stage_", None)

        assert result is True

    def test_run_stage_fails_when_artifacts_missing(self, tmp_path: Path) -> None:
        """脚本执行成功但产物缺失时，_run_stage 必须返回 False（F9-001 核心规则）。"""
        from scripts.run_pipeline import STAGE_META, _ARTIFACT_CHECKS, _run_stage

        # 伪造一个"执行成功"但不生成产物的阶段
        STAGE_META["_mock_stage_"] = {
            "desc": "mock",
            "module": "scripts._nonexistent_module_for_test",
            "extra": [],
        }
        _ARTIFACT_CHECKS["_mock_stage_"] = [tmp_path / "missing_output.parquet"]

        try:
            # subprocess.run 返回码 0，但产物文件不存在
            with patch("scripts.run_pipeline.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = _run_stage("_mock_stage_", [], set())

            assert result is False, (
                "脚本退出码为 0 但关键产物缺失时，_run_stage 必须返回 False"
            )
        finally:
            STAGE_META.pop("_mock_stage_", None)
            _ARTIFACT_CHECKS.pop("_mock_stage_", None)

    def test_run_stage_passes_when_artifacts_exist(self, tmp_path: Path) -> None:
        """脚本执行成功且产物存在时，_run_stage 应返回 True。"""
        from scripts.run_pipeline import STAGE_META, _ARTIFACT_CHECKS, _run_stage

        output = tmp_path / "output.parquet"

        STAGE_META["_mock_ok_stage_"] = {
            "desc": "mock ok",
            "module": "scripts._nonexistent_for_test",
            "extra": [],
        }
        _ARTIFACT_CHECKS["_mock_ok_stage_"] = [output]

        def _fake_run(*args, **kwargs):
            output.touch()  # 模拟脚本生成了产物
            return MagicMock(returncode=0)

        try:
            with patch("scripts.run_pipeline.subprocess.run", side_effect=_fake_run):
                result = _run_stage("_mock_ok_stage_", [], set())

            assert result is True
        finally:
            STAGE_META.pop("_mock_ok_stage_", None)
            _ARTIFACT_CHECKS.pop("_mock_ok_stage_", None)


# ---------------------------------------------------------------------------
# F9-006: manifest 必须包含可追溯字段
# ---------------------------------------------------------------------------

class TestManifestFields:
    """验证 run manifest 包含发布追溯所需的完整字段。"""

    def test_manifest_has_required_fields(self, tmp_path: Path) -> None:
        """manifest 必须包含 config_hash、skipped_stages、test_set_run_count 等字段。"""
        from scripts.run_pipeline import _write_run_manifest
        import src.config as cfg

        original_data_proc = cfg.DATA_PROC
        cfg.DATA_PROC = tmp_path

        try:
            with patch("scripts.run_pipeline.subprocess.check_output", return_value="abc1234\n"):
                _write_run_manifest(
                    run_id="test_run",
                    stages_executed=["factors", "evaluate"],
                    stages_skipped=["quality"],
                    start_time="2026-01-01T00:00:00Z",
                    elapsed_s=42.0,
                    success=True,
                    failed_stage=None,
                )
        finally:
            cfg.DATA_PROC = original_data_proc

        manifest_path = tmp_path / "run_manifests" / "run_test_run.json"
        assert manifest_path.exists(), "manifest 文件应当被写入"

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        required_fields = [
            "config_hash",
            "stages_skipped",
            "test_set_run_count",
            "input_hashes",
            "output_hashes",
            "stages_executed",
            "stages_skipped",
            "success",
            "git_commit",
            "train_end",
            "valid_end",
        ]
        for field in required_fields:
            assert field in manifest, f"manifest 缺少字段: {field}"

    def test_manifest_records_failure(self, tmp_path: Path) -> None:
        """失败时 manifest 应记录 success=false 和 failed_stage。"""
        from scripts.run_pipeline import _write_run_manifest
        import src.config as cfg

        original_data_proc = cfg.DATA_PROC
        cfg.DATA_PROC = tmp_path

        try:
            with patch("scripts.run_pipeline.subprocess.check_output", return_value="abc1234\n"):
                _write_run_manifest(
                    run_id="fail_run",
                    stages_executed=["factors"],
                    stages_skipped=[],
                    start_time="2026-01-01T00:00:00Z",
                    elapsed_s=10.0,
                    success=False,
                    failed_stage="evaluate",
                )
        finally:
            cfg.DATA_PROC = original_data_proc

        manifest_path = tmp_path / "run_manifests" / "run_fail_run.json"
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["success"] is False
        assert manifest["failed_stage"] == "evaluate"


# ---------------------------------------------------------------------------
# F9-002: run_attribution.py 归因失败时退出非零码
# ---------------------------------------------------------------------------

class TestAttributionExitCode:
    """验证 run_attribution.py 在归因失败时返回非零退出码（F9-002）。"""

    def _make_fake_weights(self) -> MagicMock:
        """构造满足 run_attribution.main() 最低要求的 fake weights DataFrame。"""
        fake_weights = MagicMock()
        fake_weights.shape = (12, 500)
        # 让 fake_weights.index[0].date() 可调用（用于 log.info 输出）
        fake_date = MagicMock()
        fake_date.date.return_value = "2022-01-31"
        fake_weights.index = MagicMock()
        fake_weights.index.__getitem__ = MagicMock(return_value=fake_date)
        return fake_weights

    def test_brinson_failure_exits_nonzero_without_allow_partial(self, tmp_path: Path) -> None:
        """Brinson 归因失败且未传 --allow-partial 时必须 sys.exit(1)。"""
        from scripts import run_attribution
        import src.config as cfg

        # 让 ACTUAL_WEIGHTS_PATH 指向临时文件
        fake_weights_file = tmp_path / "backtest_weights_v2.parquet"
        fake_weights_file.touch()

        original = run_attribution.ACTUAL_WEIGHTS_PATH
        run_attribution.ACTUAL_WEIGHTS_PATH = fake_weights_file

        def _fail_brinson(*args, **kwargs):
            raise RuntimeError("模拟 Brinson 归因异常")

        try:
            with patch("scripts.run_attribution.pd.read_parquet",
                       return_value=self._make_fake_weights()):
                with patch("scripts.run_attribution.compute_brinson_attribution",
                           side_effect=_fail_brinson):
                    with patch("sys.argv", ["run_attribution"]):
                        with pytest.raises(SystemExit) as exc_info:
                            run_attribution.main()
        finally:
            run_attribution.ACTUAL_WEIGHTS_PATH = original

        assert exc_info.value.code == 1, (
            "Brinson 归因失败时应 sys.exit(1)，"
            "当前退出码: %s" % exc_info.value.code
        )

    def test_allow_partial_continues_on_brinson_failure(self, tmp_path: Path) -> None:
        """--allow-partial 时 Brinson 失败不应立即 exit(1)，因子归因继续。"""
        from scripts import run_attribution

        fake_weights_file = tmp_path / "backtest_weights_v2.parquet"
        fake_weights_file.touch()

        original = run_attribution.ACTUAL_WEIGHTS_PATH
        run_attribution.ACTUAL_WEIGHTS_PATH = fake_weights_file

        def _fail_brinson(*args, **kwargs):
            raise RuntimeError("模拟 Brinson 失败")

        # 让因子归因也直接返回 MagicMock（避免 to_parquet 失败）
        fake_attr = MagicMock()
        fake_summary = MagicMock()
        # MagicMock.to_parquet() 默认可调用，不需要额外 patch

        try:
            with patch("scripts.run_attribution.pd.read_parquet",
                       return_value=self._make_fake_weights()):
                with patch("scripts.run_attribution.compute_brinson_attribution",
                           side_effect=_fail_brinson):
                    with patch("scripts.run_attribution.compute_factor_attribution",
                               return_value=(fake_attr, fake_summary)):
                        try:
                            run_attribution.main(
                                actual_weights_path=fake_weights_file,
                                allow_partial=True,
                            )
                        except SystemExit as e:
                            assert e.code != 1, (
                                "--allow-partial 模式下 Brinson 失败不应立即 exit(1)，"
                                "当前退出码: %s" % e.code
                            )
        finally:
            run_attribution.ACTUAL_WEIGHTS_PATH = original
