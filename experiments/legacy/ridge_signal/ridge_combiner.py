"""Compatibility import for the historical Ridge experiment path.

The maintained implementation lives in :mod:`src.signal.ridge_combiner`.
Historical experiment scripts may continue importing this module, but new
production code must import from ``src.signal``.
"""

from src.signal.ridge_combiner import RidgeCombiner, verify_data_interfaces

__all__ = ["RidgeCombiner", "verify_data_interfaces"]