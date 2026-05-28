"""
experiments/ridge_rolling/rolling_combiner.py
=============================================

Rolling-window variant of RidgeCombiner (Pipeline E).

Difference from parent: training data at each prediction date T is clipped
to the last `window_months` calendar months before the purge cutoff, instead
of using all available history (expanding window).

All other logic — CV fold scoring, prediction, winsorize/z-score — is
inherited unchanged from RidgeCombiner.
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_ROOT))

from experiments.legacy.ridge_signal.ridge_combiner import RidgeCombiner

log = logging.getLogger(__name__)


class RidgeRollingCombiner(RidgeCombiner):
    """
    Walk-forward Ridge with a fixed rolling training window.

    At each prediction date T the training set is restricted to rebalance
    dates in the half-open interval:

        ( T - window_months,  T - purge_months ]

    For window_months=48 and purge_months=2 this yields at most 46 months
    of pooled stock-date observations, vs. the unbounded expanding window
    used by the parent class.

    Args:
        factor_names:  ordered list of factor names (must match panel keys)
        window_months: rolling window size in calendar months
        **kwargs:      forwarded to RidgeCombiner (alpha_candidates,
                       purge_months, min_train_months, use_excess_return, …)
    """

    def __init__(
        self,
        factor_names: list[str],
        window_months: int = 48,
        **kwargs,
    ) -> None:
        super().__init__(factor_names, **kwargs)
        self.window_months = window_months

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _rolling_train_dates(
        self,
        cutoff: pd.Timestamp,
        all_dates: pd.DatetimeIndex,
    ) -> pd.DatetimeIndex:
        """Dates in (cutoff - window_months, cutoff]."""
        window_start = cutoff - pd.DateOffset(months=self.window_months)
        return all_dates[(all_dates <= cutoff) & (all_dates > window_start)]

    # ------------------------------------------------------------------
    # Overrides: swap expanding filter for rolling filter
    # ------------------------------------------------------------------

    def _process_cv_fold(
        self,
        fold_idx: int,
        val_start_str: str,
        val_end_str: str,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        all_dates: pd.DatetimeIndex,
        bench_ret_series: pd.Series | None = None,
    ) -> list[dict]:
        val_start = pd.Timestamp(val_start_str)
        val_end   = pd.Timestamp(val_end_str) + pd.offsets.MonthEnd(0)
        cutoff    = val_start - pd.DateOffset(months=self.purge_months)

        train_dates = self._rolling_train_dates(cutoff, all_dates)
        val_dates   = all_dates[(all_dates >= val_start) & (all_dates <= val_end)]

        if len(train_dates) < self.min_train_months:
            log.warning(
                "CV fold %d [%s-%s]: rolling=%dm → only %d train dates (min=%d), skipping",
                fold_idx, val_start_str, val_end_str,
                self.window_months, len(train_dates), self.min_train_months,
            )
            return []

        X_train, y_train = self._build_training_matrix(
            factor_panels, fwd_ret_panel, train_dates,
            bench_ret_series=bench_ret_series,
        )

        if len(X_train) < self.min_train_obs:
            log.warning(
                "CV fold %d: rolling=%dm → only %d obs (min=%d), skipping",
                fold_idx, self.window_months, len(X_train), self.min_train_obs,
            )
            return []

        log.info(
            "CV fold %d [%s~%s]: rolling=%dm  %d train dates  %d obs  %d val dates",
            fold_idx, val_start_str, val_end_str,
            self.window_months, len(train_dates), len(X_train), len(val_dates),
        )

        records: list[dict] = []
        for alpha in self.alpha_candidates:
            model = Ridge(alpha=alpha, fit_intercept=True)
            model.fit(X_train, y_train)
            ic_ir = self._compute_ic_ir(model, factor_panels, fwd_ret_panel, val_dates)
            records.append({
                "fold":        fold_idx,
                "val_start":   val_start_str,
                "val_end":     val_end_str,
                "n_train_obs": len(X_train),
                "alpha":       alpha,
                "ic_ir":       ic_ir,
            })
            log.info(
                "  alpha=%6.1f → val IC_IR=%s",
                alpha,
                f"{ic_ir:.4f}" if not np.isnan(ic_ir) else "  NaN",
            )

        return records

    def _fit_and_predict(
        self,
        T: pd.Timestamp,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        all_dates: pd.DatetimeIndex,
        bench_ret_series: pd.Series | None = None,
    ) -> tuple[pd.Series | None, dict[str, float] | None]:
        cutoff = T - pd.DateOffset(months=self.purge_months)
        train_dates = self._rolling_train_dates(cutoff, all_dates)

        if len(train_dates) < self.min_train_months:
            return None, None

        X_train, y_train = self._build_training_matrix(
            factor_panels, fwd_ret_panel, train_dates,
            bench_ret_series=bench_ret_series,
        )

        if len(X_train) < self.min_train_obs:
            return None, None

        model = Ridge(alpha=self.alpha_, fit_intercept=True)
        model.fit(X_train, y_train)

        signal_t = self._predict_cross_section(model, factor_panels, T)
        coef_t = dict(zip(self.factor_names, model.coef_)) if signal_t is not None else None

        return signal_t, coef_t
