"""
experiments/ridge_signal/ridge_combiner.py
==========================================

Walk-forward Ridge regression signal combiner (Experiment: Plan A).

This module is a parallel implementation of src/signal/combiner.py. It reads
the same preprocessed factor panels and forward return panel but replaces
IC_IR weighting with Ridge regression for signal synthesis.

No main-pipeline files are modified.

Algorithm (per prediction date T):
  1. Pool all (stock, date) observations from [train_start, T - purge_months]:
       X shape: (n_obs, n_factors) — preprocessed factor values, all non-NaN rows
       y shape: (n_obs,)           — realized 1M forward returns, non-NaN
  2. Fit Ridge(alpha=self.alpha_) on (X, y)
  3. Predict signal at T:
       For each stock with >= min_valid_factors non-NaN factors:
         predicted_return = X_stock @ β   (NaN factors filled with 0)
  4. Winsorize (MAD) + Z-score → composite signal

Walk-forward CV for alpha selection:
  5 expanding folds, each validating on 1 year within 2012-2020.
  Score per fold: IC_IR (Spearman rank IC mean / std) on validation dates.
  Best alpha = argmax of mean IC_IR across all folds.

Data interface (Step 1 verification):
  factor_panels : dict[name → pd.DataFrame]  shape=(rebalance_date × ts_code)
  fwd_ret_panel : pd.DataFrame               shape=(rebalance_date × ts_code)
  Both indexed by pd.Timestamp (month-end rebalance dates).
  Factor panels are already fully preprocessed (winsorize → neutralize → z-score).
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from src.factors.preprocess import standardize, winsorize_mad

log = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CV_FOLDS: list[tuple[str, str]] = [
    ("2015-01", "2015-12"),
    ("2016-01", "2016-12"),
    ("2017-01", "2017-12"),
    ("2018-01", "2018-12"),
    ("2019-01", "2019-12"),
]

_DEFAULT_ALPHA_CANDIDATES: list[float] = [0.1, 1.0, 10.0, 100.0, 500.0]

_MIN_TRAIN_MONTHS: int = 24   # minimum distinct rebalance dates before fitting
_MIN_TRAIN_OBS: int = 500     # minimum pooled stock-date observations before fitting
_MIN_VALID_FACTORS: int = 8   # stocks with fewer non-NaN factors → NaN in signal
_PURGE_MONTHS: int = 2        # calendar months between training cutoff and validation start
_MIN_IC_OBS: int = 3          # minimum valid IC values to compute IC_IR


# ---------------------------------------------------------------------------
# Step 1: data interface verification
# ---------------------------------------------------------------------------

def verify_data_interfaces(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    factor_names: list[str],
) -> dict:
    """
    Verify that factor panels and fwd_ret_panel are compatible for Ridge training.

    Checks presence of all required factors, date alignment, and NaN rates.
    Call this before running the experiment to surface problems early.

    Args:
        factor_panels: dict name→(rebalance_date × ts_code) preprocessed DataFrame
        fwd_ret_panel: (rebalance_date × ts_code) realized 1M forward return DataFrame
        factor_names:  list of 16 factor names expected to be in factor_panels
    Returns:
        dict with keys:
          missing_factors  list[str]  — factors not found in factor_panels
          n_fwd_dates      int        — number of dates in fwd_ret_panel
          n_common_dates   int        — dates present in both panels and fwd_ret
          date_range       tuple      — (first_common, last_common) as date strings
          nan_rates        dict       — factor→NaN fraction over all dates × stocks
          avg_stocks_per_date dict    — factor→mean non-NaN stocks per rebalance date
          valid            bool       — True if no missing factors and n_common_dates ≥ 24
    """
    missing = [n for n in factor_names if n not in factor_panels]

    # Compute common dates across all present panels and fwd_ret
    common_dates = fwd_ret_panel.index
    for name in factor_names:
        if name in factor_panels:
            common_dates = common_dates.intersection(factor_panels[name].index)

    date_range = (
        (str(common_dates[0].date()), str(common_dates[-1].date()))
        if len(common_dates) > 0
        else ("N/A", "N/A")
    )

    nan_rates: dict[str, float] = {}
    avg_stocks: dict[str, float] = {}
    for name in factor_names:
        if name not in factor_panels:
            continue
        panel = factor_panels[name]
        total = panel.size
        nan_rates[name] = float(panel.isna().sum().sum() / total) if total > 0 else 1.0
        avg_stocks[name] = float(panel.notna().sum(axis=1).mean())

    result = {
        "missing_factors":    missing,
        "n_fwd_dates":        len(fwd_ret_panel.index),
        "n_common_dates":     len(common_dates),
        "date_range":         date_range,
        "nan_rates":          nan_rates,
        "avg_stocks_per_date": avg_stocks,
        "valid":              len(missing) == 0 and len(common_dates) >= _MIN_TRAIN_MONTHS,
    }

    if missing:
        log.error("verify_data_interfaces: missing factors: %s", missing)
    else:
        log.info(
            "verify_data_interfaces: all %d factors present, %d common dates (%s ~ %s)",
            len(factor_names), len(common_dates), *date_range,
        )

    return result


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class RidgeCombiner:
    """
    Walk-forward Ridge regression signal combiner.

    Usage:
        combiner = RidgeCombiner(factor_names)
        combiner.select_alpha_walk_forward(factor_panels, fwd_ret_panel, all_dates)
        panel = combiner.build_ridge_panel(factor_panels, fwd_ret_panel, all_dates)
        # combiner.coef_history_ holds per-date factor coefficients for inspection

    Args:
        factor_names:       ordered list of factor names (must match panel dict keys)
        alpha_candidates:   Ridge regularization strengths to evaluate in CV
        purge_months:       calendar months to drop between training and validation start
        min_train_months:   minimum distinct rebalance dates required to fit
        min_train_obs:      minimum pooled stock-date observations required to fit
        min_valid_factors:  stocks with fewer non-NaN factors get NaN in output signal
    """

    def __init__(
        self,
        factor_names: list[str],
        alpha_candidates: list[float] | None = None,
        purge_months: int = _PURGE_MONTHS,
        min_train_months: int = _MIN_TRAIN_MONTHS,
        min_train_obs: int = _MIN_TRAIN_OBS,
        min_valid_factors: int = _MIN_VALID_FACTORS,
        use_excess_return: bool = False,
    ) -> None:
        self.factor_names = list(factor_names)
        self.alpha_candidates = alpha_candidates or list(_DEFAULT_ALPHA_CANDIDATES)
        self.purge_months = purge_months
        self.min_train_months = min_train_months
        self.min_train_obs = min_train_obs
        self.min_valid_factors = min_valid_factors
        self.use_excess_return = use_excess_return

        # Set by select_alpha_walk_forward()
        self.alpha_: float | None = None
        self.cv_results_: pd.DataFrame | None = None
        # Set by build_ridge_panel()
        self.coef_history_: pd.DataFrame | None = None

        if use_excess_return:
            log.info("RidgeCombiner: use_excess_return=True — training on fwd_ret minus benchmark return")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_alpha_walk_forward(
        self,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        all_dates: pd.DatetimeIndex,
        cv_folds: list[tuple[str, str]] | None = None,
        bench_ret_series: pd.Series | None = None,
    ) -> float:
        """
        Select Ridge regularization alpha via walk-forward cross-validation.

        For each fold and each alpha candidate:
          - Training data: all rebalance dates ≤ val_start - purge_months
          - Validation: IC_IR of Ridge predictions on dates within the fold year
          - Score: Spearman rank IC mean / std on validation dates

        Sets self.alpha_ to the alpha with highest mean IC_IR across folds.
        Saves full fold table to self.cv_results_.

        Args:
            factor_panels:    dict name→(date×stock) preprocessed factor DataFrame
            fwd_ret_panel:    (date×stock) realized 1M forward return DataFrame
            all_dates:        DatetimeIndex of all rebalance dates (train + validation)
            cv_folds:         list of (val_start_str, val_end_str) in "YYYY-MM" format
            bench_ret_series: (date,) Series of benchmark-weighted return per date.
                              Required when use_excess_return=True; ignored otherwise.
        Returns:
            selected alpha (float); also sets self.alpha_
        """
        if cv_folds is None:
            cv_folds = _DEFAULT_CV_FOLDS

        records: list[dict] = []
        for fold_idx, (val_start_str, val_end_str) in enumerate(cv_folds, start=1):
            fold_records = self._process_cv_fold(
                fold_idx, val_start_str, val_end_str,
                factor_panels, fwd_ret_panel, all_dates,
                bench_ret_series=bench_ret_series,
            )
            records.extend(fold_records)

        self.cv_results_ = pd.DataFrame(records)
        self.alpha_ = self._select_best_alpha(self.cv_results_)
        return self.alpha_

    def build_ridge_panel(
        self,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        all_dates: pd.DatetimeIndex,
        bench_ret_series: pd.Series | None = None,
    ) -> pd.DataFrame:
        """
        Build Ridge composite signal panel using an expanding training window.

        At each date T: fit Ridge on all data before T - purge_months, then
        predict the cross-sectional signal at T (winsorized + z-scored).
        Cold-start dates (insufficient training data) are omitted from output.

        Output format is identical to build_composite_panel():
          index=rebalance_date, columns=ts_code.

        Also stores per-date factor coefficients in self.coef_history_ for
        interpretability (index=rebalance_date, columns=factor_names).

        Args:
            factor_panels:    dict name→(date×stock) preprocessed factor DataFrame
            fwd_ret_panel:    (date×stock) realized 1M forward return DataFrame
            all_dates:        DatetimeIndex of all rebalance dates to generate signals for
            bench_ret_series: (date,) Series of benchmark-weighted return per date.
                              Required when use_excess_return=True; ignored otherwise.
        Returns:
            (rebalance_date × ts_code) DataFrame of Ridge composite signals
        Raises:
            RuntimeError: if select_alpha_walk_forward() has not been called first
        """
        if self.alpha_ is None:
            raise RuntimeError(
                "alpha_ is None — call select_alpha_walk_forward() before build_ridge_panel()"
            )

        rows: dict[pd.Timestamp, pd.Series] = {}
        coef_rows: dict[pd.Timestamp, dict[str, float]] = {}
        cold_start_count = 0

        for T in all_dates:
            signal_t, coef_t = self._fit_and_predict(
                T, factor_panels, fwd_ret_panel, all_dates,
                bench_ret_series=bench_ret_series,
            )
            if signal_t is None:
                cold_start_count += 1
            else:
                rows[T] = signal_t
                if coef_t is not None:
                    coef_rows[T] = coef_t

        if cold_start_count:
            log.info("build_ridge_panel: %d cold-start dates skipped", cold_start_count)

        if not rows:
            log.warning("build_ridge_panel: no valid dates — returning empty DataFrame")
            return pd.DataFrame()

        panel = pd.DataFrame(rows).T
        panel.index.name = "rebalance_date"

        self.coef_history_ = pd.DataFrame(coef_rows).T
        self.coef_history_.index.name = "rebalance_date"

        log.info(
            "build_ridge_panel: %d dates, ~%d stocks/date, alpha=%.1f",
            len(panel), int(panel.shape[1]), self.alpha_,
        )
        return panel

    # ------------------------------------------------------------------
    # Private: CV fold processing
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
        """
        Process one walk-forward CV fold across all alpha candidates.

        Returns list of record dicts (one per alpha candidate, or empty on skip).
        """
        val_start = pd.Timestamp(val_start_str)
        val_end   = pd.Timestamp(val_end_str) + pd.offsets.MonthEnd(0)
        cutoff    = val_start - pd.DateOffset(months=self.purge_months)

        train_dates = all_dates[all_dates <= cutoff]
        val_dates   = all_dates[(all_dates >= val_start) & (all_dates <= val_end)]

        if len(train_dates) < self.min_train_months:
            log.warning(
                "CV fold %d: only %d training dates (< min=%d), skipping",
                fold_idx, len(train_dates), self.min_train_months,
            )
            return []

        X_train, y_train = self._build_training_matrix(
            factor_panels, fwd_ret_panel, train_dates,
            bench_ret_series=bench_ret_series,
        )

        if len(X_train) < self.min_train_obs:
            log.warning(
                "CV fold %d: only %d training obs (< min=%d), skipping",
                fold_idx, len(X_train), self.min_train_obs,
            )
            return []

        log.info(
            "CV fold %d [%s~%s]: %d train dates, %d obs, %d val dates",
            fold_idx, val_start_str, val_end_str,
            len(train_dates), len(X_train), len(val_dates),
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

    def _select_best_alpha(self, cv_df: pd.DataFrame) -> float:
        """
        Select alpha with highest mean IC_IR across folds.

        Falls back to 10.0 if all CV results are missing or NaN.
        Logs a summary table showing mean IC_IR per alpha.
        """
        fallback = 10.0

        if cv_df.empty or cv_df["ic_ir"].isna().all():
            log.error("All CV folds failed — defaulting alpha to %.1f", fallback)
            return fallback

        mean_ic_ir = cv_df.groupby("alpha")["ic_ir"].mean().sort_index()
        best = float(mean_ic_ir.idxmax())

        log.info("--- Alpha selection summary ---")
        for alpha_val, val in mean_ic_ir.items():
            marker = " ← SELECTED" if alpha_val == best else ""
            log.info(
                "  alpha=%6.1f  mean IC_IR=%s%s",
                alpha_val,
                f"{val:.4f}" if not np.isnan(val) else "  NaN",
                marker,
            )

        return best

    # ------------------------------------------------------------------
    # Private: per-date fit + predict
    # ------------------------------------------------------------------

    def _fit_and_predict(
        self,
        T: pd.Timestamp,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        all_dates: pd.DatetimeIndex,
        bench_ret_series: pd.Series | None = None,
    ) -> tuple[pd.Series | None, dict[str, float] | None]:
        """
        Fit Ridge on expanding window before T and predict cross-section at T.

        Returns (signal_series, coef_dict); both None if cold-start or no valid data.
        """
        cutoff = T - pd.DateOffset(months=self.purge_months)
        train_dates = all_dates[all_dates <= cutoff]

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

    # ------------------------------------------------------------------
    # Private: matrix construction and IC computation
    # ------------------------------------------------------------------

    def _build_training_matrix(
        self,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        training_dates: pd.DatetimeIndex,
        bench_ret_series: pd.Series | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Stack stock-date observations from training_dates into (X, y).

        Inclusion rule (mirrors _predict_cross_section for consistency):
          - Stock has >= self.min_valid_factors non-NaN factor values
          - Stock's fwd_ret is non-NaN
          - Missing factor values are filled with 0 (z-score neutral value)

        When use_excess_return=True and bench_ret_series is provided:
          y = fwd_ret_i - bench_ret_T  (cross-sectional demeaning by benchmark return)
          This removes the common market factor from the training target so Ridge
          coefficients focus on cross-sectional stock selection rather than market
          direction. Benchmark return is the index-weighted average of fwd_ret across
          constituent stocks at date T.

          Why this improves on absolute-return training:
            - |bench_ret| averages ~6%, cs_std averages ~11%: without demeaning,
              >50% of MSE loss is driven by common market moves Ridge cannot explain
              via stock-level factors, pushing coefficients toward market-timing noise.
            - Spearman IC is rank-invariant to subtracting a constant, so IC metrics
              are unchanged; but Ridge MSE now optimizes the quantity IC measures.

        Why 0-fill rather than strict dropna:
          Some factors (e.g. hk_hold_ratio) have zero coverage before their
          data source launched (HK Stock Connect opened Nov 2014). Strict dropna
          would yield zero training observations for 2012-2014, eliminating 3 years
          of valid training data. Filling with 0 is economically sensible (0 = no HK
          holding, which is accurate for that period) and statistically conservative.

        Time-alignment guarantee:
          training_dates are already purge-filtered by the caller.

        Args:
            factor_panels:    dict name→(date×stock) preprocessed factor DataFrame
            fwd_ret_panel:    (date×stock) realized 1M forward return DataFrame
            training_dates:   DatetimeIndex of dates to pool
            bench_ret_series: optional (date,) Series of benchmark-weighted returns;
                              used for y-demeaning when use_excess_return=True
        Returns:
            X: float64 array (N_obs, n_factors), NaN-filled with 0
            y: float64 array (N_obs,)  — absolute or excess return per configuration
        """
        x_chunks: list[np.ndarray] = []
        y_chunks: list[np.ndarray] = []

        for T in training_dates:
            if T not in fwd_ret_panel.index:
                continue

            cs_cols: dict[str, pd.Series] = {}
            skip = False
            for name in self.factor_names:
                if T not in factor_panels[name].index:
                    skip = True
                    break
                cs_cols[name] = factor_panels[name].loc[T]
            if skip:
                continue

            cs = pd.DataFrame(cs_cols)   # index=ts_code, cols=factors
            fwd_row = fwd_ret_panel.loc[T]

            # Keep stocks with enough factor coverage and a valid forward return
            n_valid = cs.notna().sum(axis=1)
            keep_mask = (n_valid >= self.min_valid_factors) & fwd_row.reindex(cs.index).notna()

            if keep_mask.sum() < 10:
                continue

            cs_train = cs[keep_mask].fillna(0.0)
            y_abs = fwd_row.reindex(cs_train.index)

            # Demean by benchmark return when use_excess_return=True
            if self.use_excess_return and bench_ret_series is not None:
                bench_ret_T = bench_ret_series.get(T, float("nan"))
                if not np.isnan(bench_ret_T):
                    y_vals = (y_abs - bench_ret_T).values.astype(np.float64)
                else:
                    y_vals = y_abs.values.astype(np.float64)
            else:
                y_vals = y_abs.values.astype(np.float64)

            x_chunks.append(cs_train[self.factor_names].values.astype(np.float64))
            y_chunks.append(y_vals)

        if not x_chunks:
            return (
                np.empty((0, len(self.factor_names)), dtype=np.float64),
                np.empty(0, dtype=np.float64),
            )

        return np.vstack(x_chunks), np.concatenate(y_chunks)

    def _compute_ic_ir(
        self,
        model: Ridge,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        val_dates: pd.DatetimeIndex,
    ) -> float:
        """
        Compute Spearman rank IC_IR of Ridge predictions on val_dates.

        IC per date = Spearman corr(raw_predicted_return, realized_fwd_ret).
        IC_IR = mean(IC) / std(IC, ddof=1).

        Uses the same NaN-fill=0 + min_valid_factors inclusion rule as
        _build_training_matrix and _predict_cross_section, so the evaluation
        universe matches both training and prediction universes.

        Raw Ridge predictions (before winsorize/z-score) are used for IC to
        keep the CV score independent of post-processing choices. For Spearman
        (rank-based), this is equivalent to using the final signal since
        monotone transforms preserve rank order.

        Args:
            model:         fitted Ridge model
            factor_panels: same format as build_ridge_panel input
            fwd_ret_panel: same format as build_ridge_panel input
            val_dates:     dates to evaluate
        Returns:
            IC_IR (float); NaN if fewer than _MIN_IC_OBS valid IC values
        """
        ic_list: list[float] = []

        for T in val_dates:
            if T not in fwd_ret_panel.index:
                continue

            cs_cols: dict[str, pd.Series] = {}
            skip = False
            for name in self.factor_names:
                if T not in factor_panels[name].index:
                    skip = True
                    break
                cs_cols[name] = factor_panels[name].loc[T]
            if skip:
                continue

            cs = pd.DataFrame(cs_cols)
            fwd = fwd_ret_panel.loc[T]

            n_valid = cs.notna().sum(axis=1)
            keep_mask = (n_valid >= self.min_valid_factors) & fwd.reindex(cs.index).notna()

            if keep_mask.sum() < 10:
                continue

            cs_pred = cs[keep_mask].fillna(0.0)
            raw_preds = pd.Series(
                model.predict(cs_pred[self.factor_names].values),
                index=cs_pred.index,
            )
            fwd_valid = fwd.reindex(cs_pred.index)

            ic = float(raw_preds.corr(fwd_valid, method="spearman"))
            if not np.isnan(ic):
                ic_list.append(ic)

        if len(ic_list) < _MIN_IC_OBS:
            return np.nan

        ic_arr = np.array(ic_list)
        std = float(ic_arr.std(ddof=1))
        if std < 1e-10:
            return np.nan
        return float(ic_arr.mean() / std)

    def _predict_cross_section(
        self,
        model: Ridge,
        factor_panels: dict[str, pd.DataFrame],
        T: pd.Timestamp,
    ) -> pd.Series | None:
        """
        Apply fitted Ridge to the factor cross-section at T.

        Stocks with >= self.min_valid_factors non-NaN factors receive a prediction
        (NaN factors are filled with 0, the neutral value for a z-scored factor).
        Stocks below the threshold receive NaN.
        Output is winsorized (MAD) then z-scored.

        Using 0-fill rather than dropping stocks:
          - 0 is the cross-sectional mean for a z-scored factor
          - This means "no opinion from the missing factor", which is more
            conservative than excluding the stock entirely from the signal
          - Stocks with very few valid factors are still excluded via min_valid_factors

        Args:
            model:         fitted Ridge model
            factor_panels: dict name→(date×stock) preprocessed factor DataFrame
            T:             prediction date
        Returns:
            normalized composite signal Series (ts_code index); None if too few stocks
        """
        cs_cols: dict[str, pd.Series] = {}
        for name in self.factor_names:
            if T not in factor_panels[name].index:
                log.debug("_predict_cross_section: factor %s missing at %s", name, T.date())
                return None
            cs_cols[name] = factor_panels[name].loc[T]

        cs = pd.DataFrame(cs_cols)   # index=ts_code, cols=factors

        n_valid = cs.notna().sum(axis=1)
        predict_mask = n_valid >= self.min_valid_factors

        if predict_mask.sum() < 10:
            log.debug("_predict_cross_section: fewer than 10 qualifying stocks at %s", T.date())
            return None

        cs_pred = cs[predict_mask].fillna(0.0)
        raw_preds = model.predict(cs_pred[self.factor_names].values)

        signal = pd.Series(raw_preds, index=cs_pred.index, name="composite")
        signal = signal.reindex(cs.index)   # non-qualifying stocks → NaN

        valid_count = signal.notna().sum()
        if valid_count >= 2:
            signal = winsorize_mad(signal)
            signal = standardize(signal)

        signal.name = "composite"
        return signal
