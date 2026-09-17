"""Architecture checks for canonical and legacy Ridge import paths."""

from experiments.legacy.ridge_rolling.rolling_combiner import (
    RidgeRollingCombiner as LegacyRidgeRollingCombiner,
)
from experiments.legacy.ridge_signal.ridge_combiner import (
    RidgeCombiner as LegacyRidgeCombiner,
)
from src.signal.ridge_combiner import RidgeCombiner
from src.signal.ridge_decay import RidgeDecayCombiner
from src.signal.ridge_rolling import RidgeRollingCombiner


def test_canonical_ridge_class_hierarchy() -> None:
    """Rolling and decay variants must extend the canonical production base."""
    assert issubclass(RidgeRollingCombiner, RidgeCombiner)
    assert issubclass(RidgeDecayCombiner, RidgeCombiner)


def test_legacy_ridge_paths_are_compatibility_aliases() -> None:
    """Historical imports must resolve to the maintained production classes."""
    assert LegacyRidgeCombiner is RidgeCombiner
    assert LegacyRidgeRollingCombiner is RidgeRollingCombiner