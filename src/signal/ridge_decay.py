"""
src/signal/ridge_decay.py — 指数衰减加权 Ridge 信号合成
==========================================================

EW-Ridge：expanding 训练窗口 + 指数衰减 sample_weight。

与标准 expanding Ridge 的唯一区别：调用 Ridge.fit() 时传入 sample_weight，
使近期截面数据的损失贡献高于远期。

数学等价关系（线性高斯假设下）：
  EW-Ridge(λ) ≡ RLS(λ) ≡ Bayes(λ-discount)
  见 docs/历史归档/过程记录/2026年05月至06月工作记录/5.28/plans/time_varying_ridge_frameworks.md。

使用方式（与 RidgeRollingCombiner 完全一致）：
  combiner = RidgeDecayCombiner(factor_names, half_life_months=24, ...)
  combiner.select_alpha_walk_forward(factor_panels, fwd_ret_panel, all_dates)
  panel = combiner.build_ridge_panel(factor_panels, fwd_ret_panel, all_dates)
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from src.signal.ridge_combiner import RidgeCombiner

log = logging.getLogger(__name__)


class RidgeDecayCombiner(RidgeCombiner):
    """
    Walk-forward Ridge with exponential decay sample weights.

    与父类 RidgeCombiner 的区别：
      - 覆写 _process_cv_fold 和 _fit_and_predict，在 Ridge.fit() 时传 sample_weight
      - 新增 _build_sample_weights 和 _build_training_matrix_with_counts

    所有其他逻辑（CV fold 结构、IC_IR 评分、预测截面、winsorize/z-score）
    完全复用父类，保证口径一致。

    Args:
        factor_names:       因子名列表（顺序与 factor_panels 键一致）
        half_life_months:   权重衰减到 0.5 所需月数，必须为正整数
                            决定衰减速率 λ = 0.5^(1/half_life_months)
                            hl=24 → λ≈0.971；hl=36 → λ≈0.981；hl=48 → λ≈0.986
        **kwargs:           传给 RidgeCombiner（alpha_candidates, purge_months 等）
    """

    def __init__(
        self,
        factor_names: list[str],
        half_life_months: int = 24,
        **kwargs,
    ) -> None:
        super().__init__(factor_names, **kwargs)
        if half_life_months <= 0:
            raise ValueError(f"half_life_months 必须为正整数，收到: {half_life_months!r}")
        self.half_life_months: int = int(half_life_months)
        self.lam: float = 0.5 ** (1.0 / self.half_life_months)

    # ------------------------------------------------------------------
    # Sample weight construction
    # ------------------------------------------------------------------

    def _build_sample_weights(
        self,
        train_dates: pd.DatetimeIndex,
        n_per_date: list[int],
    ) -> np.ndarray:
        """
        构造与 X_train 行数等长的 sample_weight 数组。

        权重定义：第 k 个日期（k=0 最旧，k=T-1 最新）的原始权重为
          w_k = λ^(T-1-k)
        最终归一化为均值 1，避免不同 half_life 改变 Ridge alpha 的有效正则强度。

        要求：
          len(train_dates) == len(n_per_date)
          sum(n_per_date) == 返回数组长度 == X_train 行数

        Args:
            train_dates: 与 n_per_date 等长的训练日期序列（升序，最旧在前）
            n_per_date:  每个日期实际进入 X_train 的有效样本数（跳过日期填 0）
        Returns:
            float64 ndarray，长度 = sum(n_per_date)，归一化后均值 ≈ 1
        Raises:
            ValueError: train_dates 与 n_per_date 长度不匹配
        """
        if len(train_dates) != len(n_per_date):
            raise ValueError(
                f"train_dates 长度 {len(train_dates)} 与 "
                f"n_per_date 长度 {len(n_per_date)} 不匹配"
            )

        n_total = len(train_dates)
        weights: list[float] = []
        for k, n_obs in enumerate(n_per_date):
            w = self.lam ** (n_total - 1 - k)
            weights.extend([w] * int(n_obs))

        sw = np.asarray(weights, dtype=np.float64)
        if sw.size > 0 and sw.mean() > 0:
            sw = sw / sw.mean()
        return sw

    # ------------------------------------------------------------------
    # Training matrix (extends parent, additionally returns n_per_date)
    # ------------------------------------------------------------------

    def _build_training_matrix_with_counts(
        self,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        training_dates: pd.DatetimeIndex,
        bench_ret_series: pd.Series | None = None,
    ) -> tuple[np.ndarray, np.ndarray, list[int]]:
        """
        复制父类 _build_training_matrix 的完整过滤逻辑，额外返回 n_per_date。

        n_per_date[k] = 第 k 个训练日期（按 training_dates 顺序）实际进入 X 的
        有效股票数；因日期缺失/股票不足而跳过的日期对应值为 0。

        不变量：
          len(n_per_date) == len(training_dates)
          sum(n_per_date) == len(X_train)

        过滤规则与父类完全一致（0-fill NaN，min_valid_factors 阈值）：
          见 RidgeCombiner._build_training_matrix 的 docstring。

        Args:
            factor_panels:    dict name→(date×stock) 预处理后因子 DataFrame
            fwd_ret_panel:    (date×stock) 月频远期收益 DataFrame
            training_dates:   纳入训练的日期序列（调用方已做 purge 过滤）
            bench_ret_series: 可选的基准收益序列；use_excess_return=True 时用于 y 去均值
        Returns:
            X:          float64 (N_obs, n_factors)，缺失因子填 0
            y:          float64 (N_obs,)，绝对收益或超额收益
            n_per_date: list[int]，len == len(training_dates)
        """
        x_chunks: list[np.ndarray] = []
        y_chunks: list[np.ndarray] = []
        n_per_date: list[int] = []

        for T in training_dates:
            if T not in fwd_ret_panel.index:
                n_per_date.append(0)
                continue

            cs_cols: dict[str, pd.Series] = {}
            skip = False
            for name in self.factor_names:
                if T not in factor_panels[name].index:
                    skip = True
                    break
                cs_cols[name] = factor_panels[name].loc[T]
            if skip:
                n_per_date.append(0)
                continue

            cs = pd.DataFrame(cs_cols)
            fwd_row = fwd_ret_panel.loc[T]

            n_valid = cs.notna().sum(axis=1)
            keep_mask = (
                (n_valid >= self.min_valid_factors)
                & fwd_row.reindex(cs.index).notna()
            )

            if keep_mask.sum() < 10:
                n_per_date.append(0)
                continue

            cs_train = cs[keep_mask].fillna(0.0)
            y_abs = fwd_row.reindex(cs_train.index)

            if self.use_excess_return and bench_ret_series is not None:
                bench_ret_T = bench_ret_series.get(T, float("nan"))
                if not np.isnan(bench_ret_T):
                    y_vals = (y_abs - bench_ret_T).values.astype(np.float64)
                else:
                    y_vals = y_abs.values.astype(np.float64)
            else:
                y_vals = y_abs.values.astype(np.float64)

            n_obs = len(cs_train)
            x_chunks.append(cs_train[self.factor_names].values.astype(np.float64))
            y_chunks.append(y_vals)
            n_per_date.append(n_obs)

        if not x_chunks:
            return (
                np.empty((0, len(self.factor_names)), dtype=np.float64),
                np.empty(0, dtype=np.float64),
                n_per_date,
            )

        return np.vstack(x_chunks), np.concatenate(y_chunks), n_per_date

    # ------------------------------------------------------------------
    # Overrides: CV fold + final prediction
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
        """覆写：expanding 训练集 + 指数衰减 sample_weight，其余与父类一致。"""
        val_start = pd.Timestamp(val_start_str)
        val_end   = pd.Timestamp(val_end_str) + pd.offsets.MonthEnd(0)
        cutoff    = val_start - pd.DateOffset(months=self.purge_months)

        train_dates = all_dates[all_dates <= cutoff]
        val_dates   = all_dates[(all_dates >= val_start) & (all_dates <= val_end)]

        if len(train_dates) < self.min_train_months:
            log.warning(
                "CV fold %d: 训练日期数 %d < min=%d，跳过",
                fold_idx, len(train_dates), self.min_train_months,
            )
            return []

        X_train, y_train, n_per_date = self._build_training_matrix_with_counts(
            factor_panels, fwd_ret_panel, train_dates,
            bench_ret_series=bench_ret_series,
        )

        if len(X_train) < self.min_train_obs:
            log.warning(
                "CV fold %d: 训练样本数 %d < min=%d，跳过",
                fold_idx, len(X_train), self.min_train_obs,
            )
            return []

        sample_weight = self._build_sample_weights(train_dates, n_per_date)
        if len(sample_weight) != len(X_train):
            raise RuntimeError(
                f"CV fold {fold_idx}: sample_weight 长度 {len(sample_weight)} "
                f"≠ X_train 行数 {len(X_train)}"
            )

        log.info(
            "CV fold %d [%s~%s]: decay hl=%dm  %d 训练日  %d obs  %d 验证日",
            fold_idx, val_start_str, val_end_str,
            self.half_life_months, len(train_dates), len(X_train), len(val_dates),
        )

        records: list[dict] = []
        for alpha in self.alpha_candidates:
            model = Ridge(alpha=alpha, fit_intercept=True)
            model.fit(X_train, y_train, sample_weight=sample_weight)
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
        """覆写：expanding 训练集 + 指数衰减 sample_weight 拟合，预测复用父类。"""
        cutoff = T - pd.DateOffset(months=self.purge_months)
        train_dates = all_dates[all_dates <= cutoff]

        if len(train_dates) < self.min_train_months:
            return None, None

        X_train, y_train, n_per_date = self._build_training_matrix_with_counts(
            factor_panels, fwd_ret_panel, train_dates,
            bench_ret_series=bench_ret_series,
        )

        if len(X_train) < self.min_train_obs:
            return None, None

        sample_weight = self._build_sample_weights(train_dates, n_per_date)
        if len(sample_weight) != len(X_train):
            raise RuntimeError(
                f"_fit_and_predict T={T.date()}: sample_weight 长度 {len(sample_weight)} "
                f"≠ X_train 行数 {len(X_train)}"
            )

        model = Ridge(alpha=self.alpha_, fit_intercept=True)
        model.fit(X_train, y_train, sample_weight=sample_weight)

        signal_t = self._predict_cross_section(model, factor_panels, T)
        coef_t = (
            dict(zip(self.factor_names, model.coef_))
            if signal_t is not None
            else None
        )
        return signal_t, coef_t
