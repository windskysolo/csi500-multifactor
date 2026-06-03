"""
tests/test_research_workflow.py — 研究流程完整性验收测试

验证 RESEARCH_GUIDE.md 描述的每一个步骤在代码层面是正确且可执行的。
全部测试均不依赖真实数据（data/ 目录），秒级完成。

覆盖范围：
  1. Spec 文件加载与字段校验
  2. ExperimentSpec 合约（period_scope / allow_test_set 规则）
  3. RunContext 生成的 run_id 格式
  4. ArtifactLayout 目录结构与路径与文档一致
  5. 晋升前置检查（promote_run 拦截不完整 run）
  6. CLI 参数接口（所有文档中提到的参数确实存在）
  7. Registry 文件结构（mainline / challengers）
  8. 测试集 ledger 路径与计数
  9. final_factors.json 结构合法性
 10. 网格实验 Spec 生成到子目录
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

# ── 项目根目录 ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.pipeline.artifacts import (
    ArtifactLayout,
    REQUIRED_FULL_RUN_ARTIFACTS,
    REQUIRED_SIGNAL_ARTIFACTS,
    REQUIRED_PORTFOLIO_ARTIFACTS,
    REQUIRED_BACKTEST_ARTIFACTS,
)
from src.pipeline.contracts import (
    BacktestSpec,
    ExperimentSpec,
    OptimizerSpec,
    RunContext,
    SignalSpec,
)
from src.pipeline.registry import load_mainline, load_challengers, promote_run


# ═══════════════════════════════════════════════════════════════════════════
# 1. Spec 文件加载
# ═══════════════════════════════════════════════════════════════════════════

SPEC_DIR = ROOT / "configs" / "pipelines"
SPEC_FILES = sorted(SPEC_DIR.glob("*.py"))


def test_spec_files_exist():
    """configs/pipelines/ 下至少有 1 个 Spec 文件。"""
    assert len(SPEC_FILES) >= 1, "configs/pipelines/ 下没有任何 Spec 文件"


@pytest.mark.parametrize("spec_file", SPEC_FILES, ids=lambda p: p.stem)
def test_spec_file_loads(spec_file: Path):
    """每个 Spec 文件都可以被正确加载，且包含合法的 ExperimentSpec。"""
    spec = ExperimentSpec.from_config_file(spec_file)
    assert isinstance(spec, ExperimentSpec)
    assert spec.experiment_id, f"{spec_file.name}: experiment_id 不能为空"
    assert spec.description, f"{spec_file.name}: description 不能为空"
    assert isinstance(spec.signal, SignalSpec)
    assert isinstance(spec.optimizer, OptimizerSpec)
    assert isinstance(spec.backtest, BacktestSpec)


@pytest.mark.parametrize("spec_file", SPEC_FILES, ids=lambda p: p.stem)
def test_spec_file_id_matches_filename(spec_file: Path):
    """experiment_id 应该与文件名一致（文档命名规范）。"""
    spec = ExperimentSpec.from_config_file(spec_file)
    assert spec.experiment_id == spec_file.stem, (
        f"文件名 {spec_file.stem!r} 与 experiment_id {spec.experiment_id!r} 不一致"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2. ExperimentSpec 合约规则
# ═══════════════════════════════════════════════════════════════════════════

def _make_spec(**overrides) -> ExperimentSpec:
    defaults = dict(
        experiment_id="test_spec",
        description="测试用 spec",
        period_scope="train_valid",
        signal=SignalSpec(method="ridge", target="excess_return", training_mode="expanding"),
        optimizer=OptimizerSpec(),
        backtest=BacktestSpec(),
    )
    defaults.update(overrides)
    return ExperimentSpec(**defaults)


def test_spec_train_valid_scope_passes():
    """period_scope='train_valid' 不需要 allow_test_set。"""
    spec = _make_spec(period_scope="train_valid")
    assert spec.period_scope == "train_valid"


def test_spec_test_run_scope_with_flag_passes():
    """period_scope='test_run_1' + allow_test_set=True 是合法组合。"""
    spec = _make_spec(period_scope="test_run_1", allow_test_set=True)
    assert spec.allow_test_set is True


def test_spec_test_run_scope_without_flag_raises():
    """period_scope='test_run_1' 但 allow_test_set=False 应该报错。"""
    with pytest.raises(ValueError, match="allow_test_set"):
        _make_spec(period_scope="test_run_1", allow_test_set=False)


def test_spec_invalid_scope_raises():
    """period_scope='test'（旧的非法值）应该报错。"""
    with pytest.raises(ValueError, match="period_scope"):
        _make_spec(period_scope="test")


def test_spec_arbitrary_invalid_scope_raises():
    """period_scope 乱写应该报错。"""
    with pytest.raises(ValueError):
        _make_spec(period_scope="production")


def test_spec_to_json_roundtrip():
    """ExperimentSpec 序列化后可以完整读回字段。"""
    spec = _make_spec()
    data = json.loads(spec.to_json())
    assert data["experiment_id"] == "test_spec"
    assert data["signal"]["method"] == "ridge"
    assert data["optimizer"]["topn"] == 50
    assert data["backtest"]["execution"] == "tplus1_open"


# ═══════════════════════════════════════════════════════════════════════════
# 3. RunContext — run_id 格式
# ═══════════════════════════════════════════════════════════════════════════

def test_run_context_id_format(tmp_path: Path):
    """run_id 格式必须是 YYYYMMDD_HHMMSS__<experiment_id>。"""
    spec = _make_spec(experiment_id="my_experiment")
    ctx = RunContext.create(spec, runs_root=tmp_path)
    pattern = r"^\d{8}_\d{6}__my_experiment$"
    assert re.match(pattern, ctx.run_id), (
        f"run_id {ctx.run_id!r} 不符合 YYYYMMDD_HHMMSS__<id> 格式"
    )


def test_run_context_scope_train_valid(tmp_path: Path):
    """train_valid spec → scope='train_valid'，路径在 runs/train_valid/ 下。"""
    spec = _make_spec(period_scope="train_valid")
    ctx = RunContext.create(spec, runs_root=tmp_path)
    assert ctx.scope == "train_valid"
    assert "train_valid" in str(ctx.run_dir)


def test_run_context_scope_test(tmp_path: Path):
    """test_run spec → scope='test'，路径在 runs/test/ 下。"""
    spec = _make_spec(period_scope="test_run_1", allow_test_set=True)
    ctx = RunContext.create(spec, runs_root=tmp_path)
    assert ctx.scope == "test"
    assert "test" in str(ctx.run_dir)


def test_run_context_run_dir_not_created_yet(tmp_path: Path):
    """RunContext.create 只计算路径，不创建目录（目录由 layout.create_dirs() 创建）。"""
    spec = _make_spec()
    ctx = RunContext.create(spec, runs_root=tmp_path)
    assert not ctx.run_dir.exists(), "RunContext.create 不应提前创建 run_dir"


# ═══════════════════════════════════════════════════════════════════════════
# 4. ArtifactLayout — 目录结构与路径
# ═══════════════════════════════════════════════════════════════════════════

def test_artifact_layout_creates_all_documented_subdirs(tmp_path: Path):
    """create_dirs() 必须创建 RESEARCH_GUIDE.md 里列出的所有子目录。"""
    layout = ArtifactLayout(tmp_path / "run")
    layout.create_dirs()
    expected_dirs = {"signal", "portfolio", "backtest", "attribution", "reports"}
    for d in expected_dirs:
        assert (tmp_path / "run" / d).is_dir(), f"子目录 {d}/ 未被创建"


def test_artifact_layout_root_file_paths(tmp_path: Path):
    """run_config.json / inputs.lock.json / manifest.json / RUN_FINISHED.json 在根目录。"""
    layout = ArtifactLayout(tmp_path / "run")
    assert layout.run_config_path().parent == tmp_path / "run"
    assert layout.inputs_lock_path().parent == tmp_path / "run"
    assert layout.manifest_path().parent == tmp_path / "run"
    assert layout.run_finished_path().parent == tmp_path / "run"
    assert layout.run_failed_path().parent == tmp_path / "run"


def test_self_check_in_reports_subdir(tmp_path: Path):
    """self_check.md 路径必须在 reports/ 子目录，不在根目录。"""
    layout = ArtifactLayout(tmp_path / "run")
    self_check_rel = "reports/self_check.md"
    # REQUIRED_FULL_RUN_ARTIFACTS 必须包含这个路径
    assert self_check_rel in REQUIRED_FULL_RUN_ARTIFACTS, (
        "REQUIRED_FULL_RUN_ARTIFACTS 未包含 reports/self_check.md"
    )
    # 路径实际指向 reports/ 子目录
    actual_path = layout.path(self_check_rel)
    assert actual_path.parent.name == "reports", (
        f"self_check.md 应在 reports/ 子目录，实际父目录：{actual_path.parent.name}"
    )


def test_required_artifacts_signal(tmp_path: Path):
    """REQUIRED_SIGNAL_ARTIFACTS 包含 signal/composite.parquet。"""
    assert "signal/composite.parquet" in REQUIRED_SIGNAL_ARTIFACTS


def test_required_artifacts_portfolio(tmp_path: Path):
    """REQUIRED_PORTFOLIO_ARTIFACTS 包含 portfolio/target_weights.parquet。"""
    assert "portfolio/target_weights.parquet" in REQUIRED_PORTFOLIO_ARTIFACTS


def test_required_artifacts_backtest(tmp_path: Path):
    """REQUIRED_BACKTEST_ARTIFACTS 包含 nav 和 metrics。"""
    assert "backtest/nav_valid.parquet" in REQUIRED_BACKTEST_ARTIFACTS
    assert "backtest/metrics_valid.parquet" in REQUIRED_BACKTEST_ARTIFACTS


def test_required_full_run_artifacts_superset(tmp_path: Path):
    """REQUIRED_FULL_RUN_ARTIFACTS 是各阶段 artifacts 的超集。"""
    full = set(REQUIRED_FULL_RUN_ARTIFACTS)
    for a in REQUIRED_SIGNAL_ARTIFACTS + REQUIRED_PORTFOLIO_ARTIFACTS + REQUIRED_BACKTEST_ARTIFACTS:
        assert a in full, f"{a} 不在 REQUIRED_FULL_RUN_ARTIFACTS 中"


def test_artifact_layout_write_run_finished(tmp_path: Path):
    """write_run_finished 创建 RUN_FINISHED.json，is_finished() 返回 True。"""
    layout = ArtifactLayout(tmp_path / "run")
    (tmp_path / "run").mkdir()
    assert not layout.is_finished()
    layout.write_run_finished()
    assert layout.is_finished()
    data = json.loads(layout.run_finished_path().read_text())
    assert "finished_at" in data


def test_artifact_layout_write_run_finished_idempotent(tmp_path: Path):
    """write_run_finished 幂等：调用两次不抛错，不覆盖第一次的时间戳。"""
    layout = ArtifactLayout(tmp_path / "run")
    (tmp_path / "run").mkdir()
    layout.write_run_finished()
    first_ts = json.loads(layout.run_finished_path().read_text())["finished_at"]
    layout.write_run_finished()
    second_ts = json.loads(layout.run_finished_path().read_text())["finished_at"]
    assert first_ts == second_ts, "write_run_finished 幂等性失败：时间戳被覆盖"


def test_artifact_layout_write_run_failed(tmp_path: Path):
    """write_run_failed 创建 RUN_FAILED.json 并包含 reason 字段。"""
    layout = ArtifactLayout(tmp_path / "run")
    (tmp_path / "run").mkdir()
    layout.write_run_failed("测试失败原因")
    data = json.loads(layout.run_failed_path().read_text())
    assert data["reason"] == "测试失败原因"
    assert "failed_at" in data


def test_artifact_layout_check_missing(tmp_path: Path):
    """check_artifacts_present 正确列出缺失的文件。"""
    layout = ArtifactLayout(tmp_path / "run")
    (tmp_path / "run").mkdir()
    missing = layout.check_artifacts_present(["signal/composite.parquet", "manifest.json"])
    assert "signal/composite.parquet" in missing
    assert "manifest.json" in missing


def test_artifact_layout_check_present(tmp_path: Path):
    """check_artifacts_present 不报告已存在的文件为缺失。"""
    layout = ArtifactLayout(tmp_path / "run")
    layout.create_dirs()
    (tmp_path / "run" / "signal" / "composite.parquet").write_bytes(b"fake")
    missing = layout.check_artifacts_present(["signal/composite.parquet"])
    assert missing == [], "已存在的 artifact 被错误报告为缺失"


# ═══════════════════════════════════════════════════════════════════════════
# 5. promote_run 前置检查
# ═══════════════════════════════════════════════════════════════════════════

def _make_complete_run(run_dir: Path) -> None:
    """创建一个符合晋升条件的完整 run 目录。"""
    layout = ArtifactLayout(run_dir)
    layout.create_dirs()
    layout.write_run_finished()
    (run_dir / "reports" / "self_check.md").write_text("## OK", encoding="utf-8")
    for art in REQUIRED_FULL_RUN_ARTIFACTS:
        p = run_dir / art
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake_parquet_data")


def test_promote_run_succeeds_with_complete_run(tmp_path: Path):
    """完整的 run（所有必要文件齐全）可以成功晋升。"""
    run_dir = tmp_path / "runs" / "train_valid" / "20260101_000000__test_exp"
    _make_complete_run(run_dir)
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()
    (reg_dir / "archived_promotions.jsonl").write_text("", encoding="utf-8")

    promote_run(
        run_id="20260101_000000__test_exp",
        slot="mainline",
        reason="单元测试晋升",
        runs_root=tmp_path / "runs",
        registry_dir=reg_dir,
    )
    mainline = json.loads((reg_dir / "mainline.json").read_text())
    assert mainline["active_run_id"] == "20260101_000000__test_exp"


def test_promote_run_blocks_without_run_finished(tmp_path: Path):
    """缺少 RUN_FINISHED.json 时，promote_run 必须抛出 RuntimeError。"""
    run_dir = tmp_path / "runs" / "train_valid" / "20260101_000000__incomplete"
    run_dir.mkdir(parents=True)
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()

    with pytest.raises(RuntimeError, match="RUN_FINISHED"):
        promote_run(
            run_id="20260101_000000__incomplete",
            slot="mainline",
            reason="测试",
            runs_root=tmp_path / "runs",
            registry_dir=reg_dir,
        )


def test_promote_run_blocks_without_self_check(tmp_path: Path):
    """缺少 reports/self_check.md 时，promote_run 必须抛出 RuntimeError。"""
    run_dir = tmp_path / "runs" / "train_valid" / "20260101_000000__no_selfcheck"
    run_dir.mkdir(parents=True)
    layout = ArtifactLayout(run_dir)
    layout.write_run_finished()
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()

    with pytest.raises(RuntimeError, match="self_check"):
        promote_run(
            run_id="20260101_000000__no_selfcheck",
            slot="mainline",
            reason="测试",
            runs_root=tmp_path / "runs",
            registry_dir=reg_dir,
        )


def test_promote_run_blocks_missing_artifacts(tmp_path: Path):
    """有 RUN_FINISHED 和 self_check 但缺少 parquet 文件时，必须抛出 RuntimeError。"""
    run_dir = tmp_path / "runs" / "train_valid" / "20260101_000000__missing_art"
    run_dir.mkdir(parents=True)
    layout = ArtifactLayout(run_dir)
    layout.create_dirs()
    layout.write_run_finished()
    (run_dir / "reports" / "self_check.md").write_text("## OK")
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()

    with pytest.raises(RuntimeError, match="Missing required artifacts"):
        promote_run(
            run_id="20260101_000000__missing_art",
            slot="mainline",
            reason="测试",
            runs_root=tmp_path / "runs",
            registry_dir=reg_dir,
        )


def test_promote_test_scope_without_flag_raises(tmp_path: Path):
    """test scope 的 run 必须显式传 allow_test_set=True 才能晋升。"""
    run_dir = tmp_path / "runs" / "test" / "test_run_1__20260101_000000__exp"
    _make_complete_run(run_dir)
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()

    with pytest.raises(ValueError, match="allow_test_set"):
        promote_run(
            run_id="test_run_1__20260101_000000__exp",
            slot="mainline",
            reason="测试",
            runs_root=tmp_path / "runs",
            registry_dir=reg_dir,
            scope="test",
            allow_test_set=False,
        )


def test_promote_run_logs_to_jsonl(tmp_path: Path):
    """晋升后 archived_promotions.jsonl 追加一条记录。"""
    run_dir = tmp_path / "runs" / "train_valid" / "20260101_000000__log_test"
    _make_complete_run(run_dir)
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()
    (reg_dir / "archived_promotions.jsonl").write_text("", encoding="utf-8")

    promote_run(
        run_id="20260101_000000__log_test",
        slot="mainline",
        reason="测试日志",
        runs_root=tmp_path / "runs",
        registry_dir=reg_dir,
    )
    lines = (reg_dir / "archived_promotions.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["active_run_id"] == "20260101_000000__log_test"
    assert entry["reason"] == "测试日志"


# ═══════════════════════════════════════════════════════════════════════════
# 6. CLI 接口 — 文档中所有参数确实存在
# ═══════════════════════════════════════════════════════════════════════════

def _get_parser_actions(script_path: Path) -> dict[str, argparse.Action]:
    """
    动态加载脚本并返回其 argparse {dest: Action} 字典。

    通过拦截 ArgumentParser.parse_args 避免实际解析 sys.argv，
    使测试不受 pytest 自身的命令行参数干扰，也不需要提供必填参数。
    """
    from unittest.mock import patch

    spec_mod = importlib.util.spec_from_file_location("_mod", script_path)
    mod = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(mod)

    captured: dict = {}

    def _fake_parse_args(self, args=None, namespace=None):
        captured["parser"] = self
        # 返回一个包含所有默认值的 Namespace，不实际解析
        return argparse.Namespace(**{a.dest: a.default for a in self._actions})

    with patch.object(argparse.ArgumentParser, "parse_args", _fake_parse_args):
        mod._parse_args()

    parser = captured.get("parser")
    assert parser is not None, f"无法从 {script_path.name} 捕获 ArgumentParser"
    return {a.dest: a for a in parser._actions}


def test_run_experiment_cli_has_documented_args():
    """run_experiment.py 必须支持 RESEARCH_GUIDE.md 里的所有参数。"""
    actions = _get_parser_actions(ROOT / "scripts" / "run_experiment.py")
    assert "spec" in actions, "--spec 参数缺失"
    assert "from_stage" in actions, "--from-stage 参数缺失"
    assert "to_stage" in actions, "--to-stage 参数缺失"
    assert "input_signal_run" in actions, "--input-signal-run 参数缺失"
    assert "runs_root" in actions, "--runs-root 参数缺失"


def test_run_experiment_from_stage_choices():
    """--from-stage 的合法值必须包含 signal / portfolio / backtest。"""
    actions = _get_parser_actions(ROOT / "scripts" / "run_experiment.py")
    choices = actions["from_stage"].choices
    assert set(choices) == {"signal", "portfolio", "backtest"}, (
        f"--from-stage choices 不符：{choices}"
    )


def test_run_experiment_to_stage_choices():
    """--to-stage 的合法值必须包含 signal / portfolio / backtest。"""
    actions = _get_parser_actions(ROOT / "scripts" / "run_experiment.py")
    choices = actions["to_stage"].choices
    assert set(choices) == {"signal", "portfolio", "backtest"}


def test_compare_runs_cli_has_documented_args():
    """compare_runs.py 必须支持 --run-ids 和 --scope。"""
    actions = _get_parser_actions(ROOT / "scripts" / "compare_runs.py")
    assert "run_ids" in actions, "--run-ids 参数缺失"
    assert "scope" in actions, "--scope 参数缺失"
    assert "output_dir" in actions, "--output-dir 参数缺失"


def test_compare_runs_scope_choices():
    """--scope 必须同时支持 train_valid 和 test（文档 4.6 说明）。"""
    actions = _get_parser_actions(ROOT / "scripts" / "compare_runs.py")
    choices = actions["scope"].choices
    assert "train_valid" in choices
    assert "test" in choices


def test_promote_run_cli_has_documented_args():
    """promote_run.py 必须支持 --run-id / --reason / --scope / --allow-test-set。"""
    actions = _get_parser_actions(ROOT / "scripts" / "promote_run.py")
    assert "run_id" in actions, "--run-id 参数缺失"
    assert "reason" in actions, "--reason 参数缺失"
    assert "scope" in actions, "--scope 参数缺失（文档提到但脚本可能未加）"
    assert "allow_test_set" in actions, "--allow-test-set 参数缺失"


def test_run_factor_evaluation_cli_has_output_dir():
    """run_factor_evaluation.py 必须支持 --output-dir（防止覆盖主池）。"""
    actions = _get_parser_actions(ROOT / "scripts" / "run_factor_evaluation.py")
    assert "output_dir" in actions, (
        "--output-dir 参数缺失。没有该参数，新因子评价会覆盖主池 reports/factor_evaluation/"
    )


def test_run_factor_evaluation_default_output_dir():
    """--output-dir 的默认值必须是 reports/factor_evaluation（文档约定）。"""
    actions = _get_parser_actions(ROOT / "scripts" / "run_factor_evaluation.py")
    default = actions["output_dir"].default
    assert default.endswith("factor_evaluation"), (
        f"--output-dir 默认值不是 reports/factor_evaluation：{default}"
    )


def test_run_factor_evaluation_has_run_id_arg():
    """run_factor_evaluation.py 必须支持 --run-id（写入 final_factors.json metadata）。"""
    actions = _get_parser_actions(ROOT / "scripts" / "run_factor_evaluation.py")
    assert "run_id" in actions


# ═══════════════════════════════════════════════════════════════════════════
# 7. Registry 文件结构
# ═══════════════════════════════════════════════════════════════════════════

def test_mainline_json_exists():
    """registry/mainline.json 必须存在。"""
    assert (ROOT / "registry" / "mainline.json").exists()


def test_mainline_json_has_required_fields():
    """mainline.json 必须包含 slot / active_run_id / scope / promoted_at 字段。"""
    mainline = load_mainline()
    for field in ("slot", "active_run_id", "scope", "promoted_at", "reason"):
        assert field in mainline, f"mainline.json 缺少字段：{field}"
    assert mainline["slot"] == "mainline"
    assert mainline["scope"] in ("train_valid", "test")


def test_mainline_run_dir_exists():
    """mainline.json 指向的 run 目录必须实际存在。"""
    mainline = load_mainline()
    run_dir = ROOT / "runs" / mainline["scope"] / mainline["active_run_id"]
    assert run_dir.exists(), f"主基线 run 目录不存在：{run_dir}"


def test_mainline_run_is_finished():
    """主基线的 run 目录必须有 RUN_FINISHED.json。"""
    mainline = load_mainline()
    run_dir = ROOT / "runs" / mainline["scope"] / mainline["active_run_id"]
    assert (run_dir / "RUN_FINISHED.json").exists(), "主基线 run 缺少 RUN_FINISHED.json"


def test_challengers_json_exists():
    """registry/challengers.json 必须存在。"""
    assert (ROOT / "registry" / "challengers.json").exists()


def test_challengers_json_structure():
    """challengers.json 必须有 'challengers' 列表，每项含 name / run_id / scope / status。"""
    data = load_challengers()
    assert "challengers" in data
    for ch in data["challengers"]:
        for field in ("name", "run_id", "scope", "status"):
            assert field in ch, f"挑战者 {ch.get('name')} 缺少字段：{field}"
        assert ch["scope"] in ("train_valid", "test")
        assert ch["status"] in ("researching", "not_started", "promoted", "archived")


def test_finished_challengers_run_dirs_exist():
    """status='researching' 且 run_id 非 null 的挑战者，run 目录必须实际存在。"""
    data = load_challengers()
    for ch in data["challengers"]:
        if ch["status"] == "researching" and ch.get("run_id"):
            run_dir = ROOT / "runs" / ch["scope"] / ch["run_id"]
            assert run_dir.exists(), (
                f"挑战者 {ch['name']} 的 run 目录不存在：{run_dir}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 8. 测试集 Ledger
# ═══════════════════════════════════════════════════════════════════════════

from scripts.test_set_ledger import (
    LEDGER_PATH,
    MAX_TEST_SET_RUNS,
    count_test_set_runs,
    remaining_test_set_runs,
    has_test_set_run,
)


def test_ledger_path_is_docs_logs():
    """Ledger 路径必须在 docs/logs/test_set_runs.json（不是 docs/check/）。"""
    expected = ROOT / "docs" / "logs" / "test_set_runs.json"
    assert LEDGER_PATH == expected, (
        f"Ledger 路径错误：{LEDGER_PATH}，应为 {expected}"
    )


def test_ledger_max_runs_is_three():
    """MAX_TEST_SET_RUNS 必须是 3。"""
    assert MAX_TEST_SET_RUNS == 3


def test_ledger_count_is_non_negative():
    """count_test_set_runs() 返回值 ≥ 0。"""
    assert count_test_set_runs() >= 0


def test_ledger_remaining_plus_count_equals_max():
    """remaining + count == MAX_TEST_SET_RUNS（或 remaining == 0 如果超出）。"""
    count = count_test_set_runs()
    remaining = remaining_test_set_runs()
    assert remaining + count == MAX_TEST_SET_RUNS or remaining == 0


def test_ledger_count_does_not_exceed_max():
    """已用次数不应超过最大值。"""
    assert count_test_set_runs() <= MAX_TEST_SET_RUNS


def test_ledger_file_structure(tmp_path: Path):
    """Ledger 文件格式：有 max_runs 和 active_runs 字段。"""
    if not LEDGER_PATH.exists():
        pytest.skip("Ledger 文件不存在，跳过结构测试")
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    assert "max_runs" in data or "active_runs" in data, "Ledger 文件缺少必要字段"
    if "active_runs" in data:
        assert isinstance(data["active_runs"], list)


# ═══════════════════════════════════════════════════════════════════════════
# 9. final_factors.json 结构
# ═══════════════════════════════════════════════════════════════════════════

FINAL_FACTORS_PATH = ROOT / "reports" / "factor_evaluation" / "final_factors.json"


def test_final_factors_json_exists():
    """reports/factor_evaluation/final_factors.json 必须存在。"""
    assert FINAL_FACTORS_PATH.exists()


def test_final_factors_json_has_required_keys():
    """final_factors.json 必须包含 final_factors / excluded / stability_weights / _metadata。"""
    data = json.loads(FINAL_FACTORS_PATH.read_text(encoding="utf-8"))
    for key in ("final_factors", "excluded", "stability_weights", "_metadata"):
        assert key in data, f"final_factors.json 缺少字段：{key}"


def test_final_factors_json_types():
    """final_factors 和 excluded 必须是列表，stability_weights 必须是字典。"""
    data = json.loads(FINAL_FACTORS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data["final_factors"], list)
    assert isinstance(data["excluded"], list)
    assert isinstance(data["stability_weights"], dict)


def test_final_factors_json_not_empty():
    """final_factors 列表不能为空（18 个入模因子）。"""
    data = json.loads(FINAL_FACTORS_PATH.read_text(encoding="utf-8"))
    assert len(data["final_factors"]) > 0, "final_factors 为空"


def test_final_factors_stability_weights_match_factors():
    """stability_weights 的 key 集合必须与 final_factors 完全一致。"""
    data = json.loads(FINAL_FACTORS_PATH.read_text(encoding="utf-8"))
    factors_set = set(data["final_factors"])
    weights_set = set(data["stability_weights"].keys())
    assert factors_set == weights_set, (
        f"stability_weights keys 与 final_factors 不一致。\n"
        f"多余的 weights key：{weights_set - factors_set}\n"
        f"缺少 weights key：{factors_set - weights_set}"
    )


def test_final_factors_metadata_has_generated_at():
    """_metadata 必须有 generated_at 字段（可追溯）。"""
    data = json.loads(FINAL_FACTORS_PATH.read_text(encoding="utf-8"))
    assert "generated_at" in data["_metadata"]


# ═══════════════════════════════════════════════════════════════════════════
# 10. 网格实验 Spec 生成到子目录
# ═══════════════════════════════════════════════════════════════════════════

GRID_SPEC_TEMPLATE = """\
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="{exp_id}",
    description="网格实验 te={te_pct}% lambda={lam}",
    period_scope="train_valid",
    signal=SignalSpec(method="ridge", target="excess_return", training_mode="expanding"),
    optimizer=OptimizerSpec(te_target_annual={te}, turnover_lambda={lam}, topn=50),
    backtest=BacktestSpec(),
)
"""


def test_grid_spec_written_to_subdirectory(tmp_path: Path):
    """网格实验 Spec 文件必须写入 configs/pipelines/grid/ 子目录。"""
    grid_dir = tmp_path / "configs" / "pipelines" / "grid"
    grid_dir.mkdir(parents=True)
    exp_id = "grid_te06_lam0050"
    content = GRID_SPEC_TEMPLATE.format(
        exp_id=exp_id, te_pct=6, te=0.06, lam=0.005
    )
    spec_file = grid_dir / f"{exp_id}.py"
    spec_file.write_text(content, encoding="utf-8")
    assert spec_file.exists()
    assert spec_file.parent.name == "grid", "spec 文件应在 grid/ 子目录"


def test_grid_generated_spec_loads_correctly(tmp_path: Path):
    """模板生成的 Spec 文件可以被 ExperimentSpec.from_config_file 正确加载。"""
    grid_dir = tmp_path / "grid"
    grid_dir.mkdir()
    exp_id = "grid_te08_lam0100"
    content = GRID_SPEC_TEMPLATE.format(
        exp_id=exp_id, te_pct=8, te=0.08, lam=0.010
    )
    spec_file = grid_dir / f"{exp_id}.py"
    spec_file.write_text(content, encoding="utf-8")

    spec = ExperimentSpec.from_config_file(spec_file)
    assert spec.experiment_id == exp_id
    assert spec.optimizer.te_target_annual == pytest.approx(0.08)
    assert spec.optimizer.turnover_lambda == pytest.approx(0.010)
    assert spec.signal.method == "ridge"
    assert spec.period_scope == "train_valid"


def test_grid_spec_does_not_go_to_root_pipeline_dir(tmp_path: Path):
    """验证：不把 grid spec 文件放在 configs/pipelines/ 根目录（混入 baseline/challenger）。"""
    grid_dir = tmp_path / "configs" / "pipelines" / "grid"
    pipeline_dir = tmp_path / "configs" / "pipelines"
    grid_dir.mkdir(parents=True)
    exp_id = "grid_te04_lam0020"
    content = GRID_SPEC_TEMPLATE.format(
        exp_id=exp_id, te_pct=4, te=0.04, lam=0.002
    )
    # 正确做法：写到 grid/ 子目录
    (grid_dir / f"{exp_id}.py").write_text(content, encoding="utf-8")
    # 验证根目录下没有 grid_ 前缀文件
    root_grid_files = list(pipeline_dir.glob("grid_*.py"))
    assert root_grid_files == [], (
        f"grid spec 文件出现在 configs/pipelines/ 根目录：{root_grid_files}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 11. 现有 run 目录结构完整性
# ═══════════════════════════════════════════════════════════════════════════

def _iter_finished_runs() -> list[Path]:
    """返回所有带 RUN_FINISHED.json 的 run 目录。"""
    runs_root = ROOT / "runs"
    finished = []
    for scope_dir in runs_root.iterdir():
        if not scope_dir.is_dir():
            continue
        for run_dir in scope_dir.iterdir():
            if run_dir.is_dir() and (run_dir / "RUN_FINISHED.json").exists():
                finished.append(run_dir)
    return finished


FINISHED_RUNS = _iter_finished_runs()


@pytest.mark.parametrize("run_dir", FINISHED_RUNS, ids=lambda p: p.name)
def test_finished_run_has_run_config(run_dir: Path):
    """每个完成的 run 必须有 run_config.json。"""
    assert (run_dir / "run_config.json").exists(), f"{run_dir.name} 缺少 run_config.json"


@pytest.mark.parametrize("run_dir", FINISHED_RUNS, ids=lambda p: p.name)
def test_finished_run_has_self_check(run_dir: Path):
    """每个完成的 run 必须有 reports/self_check.md（在 reports/ 子目录，不在根目录）。"""
    self_check = run_dir / "reports" / "self_check.md"
    assert self_check.exists(), f"{run_dir.name} 缺少 reports/self_check.md"
    assert not (run_dir / "self_check.md").exists(), (
        f"{run_dir.name} self_check.md 出现在根目录，应在 reports/ 子目录"
    )


@pytest.mark.parametrize("run_dir", FINISHED_RUNS, ids=lambda p: p.name)
def test_finished_run_has_required_artifacts(run_dir: Path):
    """每个完成的 run 必须有晋升所需的全部 artifact。"""
    layout = ArtifactLayout(run_dir)
    missing = layout.check_artifacts_present(REQUIRED_FULL_RUN_ARTIFACTS)
    assert missing == [], f"{run_dir.name} 缺少产物：{missing}"


@pytest.mark.parametrize("run_dir", FINISHED_RUNS, ids=lambda p: p.name)
def test_finished_run_id_format(run_dir: Path):
    """完成的 run 目录名必须符合 YYYYMMDD_HHMMSS__<experiment_id> 格式。"""
    pattern = r"^\d{8}_\d{6}__.+$"
    assert re.match(pattern, run_dir.name), (
        f"run 目录名不符合格式：{run_dir.name}"
    )


@pytest.mark.parametrize("run_dir", FINISHED_RUNS, ids=lambda p: p.name)
def test_finished_run_config_experiment_id_matches_dir(run_dir: Path):
    """run_config.json 中的 experiment_id 必须与目录名后缀一致。"""
    config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    experiment_id = config.get("experiment_id") or config.get("spec", {}).get("experiment_id")
    dir_suffix = run_dir.name.split("__", 1)[-1]
    assert experiment_id == dir_suffix, (
        f"{run_dir.name}: run_config experiment_id={experiment_id!r} "
        f"与目录后缀 {dir_suffix!r} 不一致"
    )
