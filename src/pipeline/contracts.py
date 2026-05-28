from __future__ import annotations

import importlib.util
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class SignalSpec:
    method: str
    target: str
    training_mode: str
    purge_months: int = 2
    alpha_grid: List[float] = field(
        default_factory=lambda: [0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0]
    )
    selected_alpha_policy: str = "cv_train_only"
    half_life_months: Optional[int] = None
    window_months: Optional[int] = None


@dataclass
class OptimizerSpec:
    te_target_annual: float = 0.06
    industry_max_dev: float = 0.03
    single_max_dev: float = 0.015
    turnover_lambda: float = 0.005
    topn: int = 50


@dataclass
class BacktestSpec:
    execution: str = "tplus1_open"
    cost_model: str = "china_a_share_v1"
    benchmark: str = "CSI500_TOTAL_RETURN"


@dataclass
class AttributionSpec:
    method: str = "brinson"


@dataclass
class ExperimentSpec:
    experiment_id: str
    description: str
    period_scope: str  # "train_valid" | "test_run_N"
    signal: SignalSpec
    optimizer: OptimizerSpec
    backtest: BacktestSpec
    attribution: AttributionSpec = field(default_factory=AttributionSpec)
    allow_test_set: bool = False
    # "single_layer" keeps only one pipeline layer variable; "full_pipeline" changes multiple
    experiment_type: str = "single_layer"

    def __post_init__(self) -> None:
        valid_train_only = self.period_scope == "train_valid"
        test_scope = self.period_scope.startswith("test_run_")
        if not valid_train_only and not test_scope:
            raise ValueError(
                f"period_scope must be 'train_valid' or 'test_run_N', got: {self.period_scope!r}"
            )
        if test_scope and not self.allow_test_set:
            raise ValueError(
                f"period_scope={self.period_scope!r} implies test-set use, "
                "but allow_test_set=False. Set allow_test_set=True in the spec."
            )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_config_file(cls, path: str | Path) -> "ExperimentSpec":
        spec_module = importlib.util.spec_from_file_location("_spec_module", path)
        module = importlib.util.module_from_spec(spec_module)
        spec_module.loader.exec_module(module)
        if not hasattr(module, "SPEC"):
            raise AttributeError(f"Config file {path} must define a top-level SPEC variable.")
        return module.SPEC


@dataclass
class RunContext:
    spec: ExperimentSpec
    run_id: str
    run_dir: Path
    scope: str  # "train_valid" | "test"

    @classmethod
    def create(cls, spec: ExperimentSpec, runs_root: Path) -> "RunContext":
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_id = f"{timestamp}__{spec.experiment_id}"
        scope = "test" if spec.allow_test_set else "train_valid"
        run_dir = runs_root / scope / run_id
        return cls(spec=spec, run_id=run_id, run_dir=run_dir, scope=scope)
