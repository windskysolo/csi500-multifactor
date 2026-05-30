"""L5 框架层测试：ExperimentSpec 加载、序列化、period_scope 校验。"""
import json
import pytest
from src.pipeline.contracts import (
    AttributionSpec,
    BacktestSpec,
    ExperimentSpec,
    OptimizerSpec,
    RunContext,
    SignalSpec,
)


def _make_spec(**overrides) -> ExperimentSpec:
    defaults = dict(
        experiment_id="test_exp",
        description="test experiment",
        period_scope="train_valid",
        signal=SignalSpec(method="ridge", target="excess_return", training_mode="expanding"),
        optimizer=OptimizerSpec(),
        backtest=BacktestSpec(),
    )
    defaults.update(overrides)
    return ExperimentSpec(**defaults)


def test_spec_creation_basic():
    spec = _make_spec()
    assert spec.experiment_id == "test_exp"
    assert spec.period_scope == "train_valid"
    assert spec.signal.method == "ridge"


def test_spec_json_roundtrip():
    spec = _make_spec()
    data = json.loads(spec.to_json())
    assert data["experiment_id"] == "test_exp"
    assert data["signal"]["method"] == "ridge"
    assert data["optimizer"]["te_target_annual"] == 0.06
    assert data["optimizer"]["optimizer_mode"] == "qp"


def test_spec_allow_test_set_false_by_default():
    spec = _make_spec()
    assert spec.allow_test_set is False


def test_spec_test_set_can_be_explicitly_allowed():
    spec = _make_spec(period_scope="test_run_1", allow_test_set=True)
    assert spec.allow_test_set is True


def test_spec_requires_period_scope():
    with pytest.raises(TypeError):
        ExperimentSpec(
            experiment_id="x",
            description="x",
            signal=SignalSpec(method="ridge", target="excess_return", training_mode="expanding"),
            optimizer=OptimizerSpec(),
            backtest=BacktestSpec(),
        )


def test_spec_from_config_file(tmp_path):
    config_content = """\
from src.pipeline.contracts import ExperimentSpec, SignalSpec, OptimizerSpec, BacktestSpec

SPEC = ExperimentSpec(
    experiment_id="from_file_exp",
    description="loaded from file",
    period_scope="train_valid",
    signal=SignalSpec(method="icir", target="excess_return", training_mode="expanding"),
    optimizer=OptimizerSpec(),
    backtest=BacktestSpec(),
)
"""
    config_file = tmp_path / "test_spec.py"
    config_file.write_text(config_content, encoding="utf-8")
    spec = ExperimentSpec.from_config_file(config_file)
    assert spec.experiment_id == "from_file_exp"
    assert spec.signal.method == "icir"
    assert spec.allow_test_set is False


def test_spec_from_config_file_missing_spec_raises(tmp_path):
    config_file = tmp_path / "bad_spec.py"
    config_file.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(AttributeError, match="SPEC"):
        ExperimentSpec.from_config_file(config_file)


def test_run_context_create_generates_run_id(tmp_path):
    spec = _make_spec()
    ctx = RunContext.create(spec, runs_root=tmp_path)
    assert "test_exp" in ctx.run_id
    assert ctx.scope == "train_valid"
    assert ctx.run_dir.parent == tmp_path / "train_valid"


def test_run_context_test_set_goes_to_test_scope(tmp_path):
    spec = _make_spec(period_scope="test_run_1", allow_test_set=True)
    ctx = RunContext.create(spec, runs_root=tmp_path)
    assert ctx.scope == "test"
    assert ctx.run_dir.parent == tmp_path / "test"


def test_signal_spec_optional_fields_default_none():
    sig = SignalSpec(method="ridge", target="excess_return", training_mode="expanding")
    assert sig.half_life_months is None
    assert sig.window_months is None


def test_optimizer_spec_default_mode_is_qp():
    opt = OptimizerSpec()
    assert opt.optimizer_mode == "qp"


def test_optimizer_spec_allows_topn_ew_mode():
    opt = OptimizerSpec(optimizer_mode="topn_ew", topn=50)
    assert opt.optimizer_mode == "topn_ew"


def test_optimizer_spec_rejects_unknown_mode():
    with pytest.raises(ValueError, match="optimizer_mode"):
        OptimizerSpec(optimizer_mode="bad_mode")


def test_frozen_baseline_spec_loads():
    spec = ExperimentSpec.from_config_file(
        "configs/pipelines/frozen_baseline_icir_topn50_ew.py"
    )
    assert spec.experiment_id == "frozen_baseline_icir_topn50_ew"
    assert spec.period_scope == "train_valid"
    assert spec.optimizer.optimizer_mode == "topn_ew"
    assert spec.optimizer.topn == 50


def test_run_portfolio_stage_passes_optimizer_mode(monkeypatch, tmp_path):
    from scripts import run_portfolio_optimization
    from src.pipeline.stages import run_portfolio_stage

    captured = {}

    def fake_portfolio_main(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(run_portfolio_optimization, "main", fake_portfolio_main)
    spec = _make_spec(optimizer=OptimizerSpec(optimizer_mode="topn_ew", topn=50))

    outputs = run_portfolio_stage(
        spec=spec,
        run_dir=tmp_path,
        signal_path=tmp_path / "signal" / "composite.parquet",
        data_proc=tmp_path,
    )

    assert captured["optimizer_mode"] == "topn_ew"
    assert captured["optimizer_config"].topn == 50
    assert outputs["target_weights"] == tmp_path / "portfolio" / "target_weights.parquet"
