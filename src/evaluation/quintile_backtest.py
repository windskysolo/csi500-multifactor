"""
src/evaluation/quintile_backtest.py — 5 分组等权回测
==========================================================

方法：
  每个调仓日 T，将可投资域按因子值从小到大分为 5 组（Q1 最低、Q5 最高）。
  各组等权持有，下期（T+1 开盘 → T'+1 开盘）的组合收益率为等权平均。
  Q5-Q1 多空组合捕捉因子的截面预测能力。

局限性与注意事项：
  - 不含交易成本（仅用于验证因子方向性，非精确回测）
  - 等权假设忽略了实际组合的市值约束和流动性限制
  - 若某组内样本数过少（< 5），该组收益置 NaN

输出说明：
  group_rets       DataFrame，行=调仓日, 列=Q1~Q5 及 Q5-Q1
  mean_rets        各组期均收益（含年化近似）
  cum_rets         累计收益率曲线（以 1 为基准）
  sharpe_ls        Q5-Q1 多空组合 Sharpe（月度 Sharpe × sqrt(12)）
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

MIN_STOCKS_PER_GROUP = 5   # 每组最少股票数，低于此返回 NaN
DIRECTIONAL_LS_LABEL = "DirectionalLS"


def _normalize_direction(direction: Optional[float]) -> float:
    """Return +1/-1 for a usable factor direction; NaN means unknown."""
    if direction is None or pd.isna(direction):
        return np.nan
    signed = float(np.sign(direction))
    return signed if signed != 0.0 else np.nan


def _annualized_sharpe(ret: pd.Series) -> float:
    """Annualized Sharpe for monthly returns, assuming rf=0."""
    valid = ret.dropna()
    if len(valid) > 1 and valid.std(ddof=1) > 0:
        return float(valid.mean() / valid.std(ddof=1) * np.sqrt(12))
    return np.nan


def _assign_quintiles(factor: pd.Series, n_groups: int = 5) -> pd.Series:
    """
    将截面因子值按从小到大分为 n_groups 组，返回分组标签（1 到 n_groups）。

    使用 pd.qcut 等频分组，`duplicates='drop'` 处理大量相同值的情况。
    若最终分组数 < n_groups（因重复值过多），返回空 Series。

    Returns:
        以 ts_code 为 index 的整数组标签 Series（1~n_groups）；分组失败时为空 Series
    """
    valid = factor.dropna()
    if len(valid) < n_groups * MIN_STOCKS_PER_GROUP:
        return pd.Series(dtype=int)

    try:
        labels = pd.qcut(valid, q=n_groups, labels=False, duplicates="drop") + 1
    except ValueError:
        return pd.Series(dtype=int)

    # 若重复值导致实际分组数少于预期，返回空
    if labels.nunique() < n_groups:
        log.debug("因重复值过多，实际分组数 %d < %d", labels.nunique(), n_groups)
        return pd.Series(dtype=int)

    return labels.astype(int)


def compute_quintile_returns(
    factor: pd.Series,
    fwd_ret: pd.Series,
    n_groups: int = 5,
) -> pd.Series:
    """
    计算单截面的分组平均 forward return。

    Args:
        factor:   截面因子值（ts_code 为 index）
        fwd_ret:  对应的下期收益率（ts_code 为 index）
        n_groups: 分组数（默认 5）
    Returns:
        以组号（1~n_groups）为 index 的各组平均收益率 Series；分组失败时为空 Series
    """
    labels = _assign_quintiles(factor, n_groups)
    if labels.empty:
        return pd.Series(dtype=float)

    common = labels.index.intersection(fwd_ret.dropna().index)
    if len(common) < n_groups * MIN_STOCKS_PER_GROUP:
        return pd.Series(dtype=float)

    group_ret = fwd_ret[common].groupby(labels[common]).mean()

    # 逐组检查：forward return 交集后每组实际样本数可能低于阈值
    # （总量达标不代表每组都达标，极端情况下某组 fwd_ret 全为 NaN 仅剩 1 只股票）
    group_sizes = labels[common].value_counts()
    small_groups = group_sizes[group_sizes < MIN_STOCKS_PER_GROUP].index
    if not small_groups.empty:
        group_ret[small_groups] = np.nan
        log.debug(
            "compute_quintile_returns: 组 %s 与 fwd_ret 交集后样本数不足 %d，置 NaN",
            sorted(small_groups.tolist()), MIN_STOCKS_PER_GROUP,
        )

    return group_ret


def run_quintile_backtest(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    n_groups: int = 5,
    direction: Optional[float] = None,
) -> dict:
    """
    对单个因子执行全周期 5 分组等权回测。

    Args:
        factor_panel:  行=调仓日, 列=ts_code 的因子面板（原始值或预处理后均可）
        fwd_ret_panel: forward return 面板（来自 compute_forward_returns）
        n_groups:      分组数（默认 5）
    Returns:
        dict，包含：
          group_rets    DataFrame，行=调仓日, 列=Q1~Qn 及 Q5-Q1
          mean_rets     各组期均收益率（Series）
          ann_rets      各组年化收益率（Series，月频 × 12 近似）
          cum_rets      累计收益率（以 1 为基准的 DataFrame）
          sharpe_ls     Q5-Q1 多空 Sharpe（年化，假设月度无风险利率≈0）
          n_periods     有效期数
    """
    dates = factor_panel.index.intersection(fwd_ret_panel.index)
    group_labels = [f"Q{i}" for i in range(1, n_groups + 1)]
    ls_label = f"Q{n_groups}-Q1"
    factor_direction = _normalize_direction(direction)

    rows: list[pd.Series] = []
    for d in dates:
        qrets = compute_quintile_returns(factor_panel.loc[d], fwd_ret_panel.loc[d], n_groups)
        if qrets.empty or len(qrets) < n_groups:
            continue
        row = qrets.rename(lambda x: f"Q{x}")
        row[ls_label] = row[f"Q{n_groups}"] - row["Q1"]
        if np.isfinite(factor_direction):
            row[DIRECTIONAL_LS_LABEL] = factor_direction * row[ls_label]
        else:
            row[DIRECTIONAL_LS_LABEL] = np.nan
        row.name = d
        rows.append(row)

    if not rows:
        log.warning("run_quintile_backtest: 没有有效分组数据")
        return {
            "group_rets": pd.DataFrame(),
            "cum_rets":   pd.DataFrame(),
            "mean_rets":  pd.Series(dtype=float),
            "ann_rets":   pd.Series(dtype=float),
            "sharpe_ls":  np.nan,
            "directional_sharpe_ls": np.nan,
            "factor_direction": factor_direction,
            "n_periods":  0,
        }

    group_rets = pd.DataFrame(rows)
    group_rets.index.name = "rebalance_date"
    # 确保列顺序一致
    ordered_cols = group_labels + [ls_label, DIRECTIONAL_LS_LABEL]
    group_rets = group_rets.reindex(columns=ordered_cols)

    mean_rets = group_rets.mean()
    ann_rets  = mean_rets * 12   # 月频近似年化（不复利）
    cum_rets  = (1 + group_rets).cumprod()

    # Keep raw Qn-Q1 and a direction-adjusted long-short series separate.
    sharpe_ls = _annualized_sharpe(group_rets[ls_label])
    directional_sharpe_ls = _annualized_sharpe(group_rets[DIRECTIONAL_LS_LABEL])

    log.info(
        "quintile_backtest: %d 期，Q5-Q1 均收益=%.2f%%，Sharpe=%.2f，DirectionalLS Sharpe=%.2f",
        len(group_rets),
        float(mean_rets.get(ls_label, 0)) * 100,
        sharpe_ls if np.isfinite(sharpe_ls) else 0,
        directional_sharpe_ls if np.isfinite(directional_sharpe_ls) else 0,
    )

    return {
        "group_rets": group_rets,
        "mean_rets":  mean_rets,
        "ann_rets":   ann_rets,
        "cum_rets":   cum_rets,
        "sharpe_ls":  sharpe_ls,
        "directional_sharpe_ls": directional_sharpe_ls,
        "factor_direction": factor_direction,
        "n_periods":  len(group_rets),
    }


def batch_quintile_summary(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    n_groups: int = 5,
    factor_directions: Optional[dict[str, float] | pd.Series] = None,
) -> pd.DataFrame:
    """
    对多个因子批量执行分组回测，汇总多空 Sharpe 等核心指标。

    Args:
        factor_panels: dict，key=因子名，value=因子面板
        fwd_ret_panel: forward return 面板
        n_groups:      分组数
    Returns:
        每行一个因子的汇总 DataFrame，按 sharpe_ls 降序排列
    """
    def _direction_for(name: str, panel: pd.DataFrame) -> float:
        if factor_directions is not None:
            if isinstance(factor_directions, pd.Series):
                raw_direction = factor_directions.get(name, np.nan)
            else:
                raw_direction = factor_directions.get(name, np.nan)
            return _normalize_direction(raw_direction)

        from src.evaluation.ic_analysis import compute_ic_series

        ic_series = compute_ic_series(panel, fwd_ret_panel)
        return _normalize_direction(ic_series.mean() if len(ic_series) else np.nan)

    rows = []
    for name, panel in factor_panels.items():
        direction = _direction_for(name, panel)
        res = run_quintile_backtest(panel, fwd_ret_panel, n_groups, direction=direction)
        ls_label = f"Q{n_groups}-Q1"
        rows.append({
            "factor":       name,
            "n_periods":    res.get("n_periods", 0),
            "factor_direction": res.get("factor_direction"),
            "q1_ann_ret":   res.get("ann_rets", pd.Series(dtype=float)).get("Q1"),
            "q5_ann_ret":   res.get("ann_rets", pd.Series(dtype=float)).get(f"Q{n_groups}"),
            "ls_ann_ret":   res.get("ann_rets", pd.Series(dtype=float)).get(ls_label),
            "directional_ls_ann_ret": res.get("ann_rets", pd.Series(dtype=float)).get(DIRECTIONAL_LS_LABEL),
            "sharpe_ls":    res.get("sharpe_ls"),
            "directional_sharpe_ls": res.get("directional_sharpe_ls"),
        })

    return pd.DataFrame(rows).set_index("factor").sort_values(
        "directional_sharpe_ls", ascending=False, na_position="last"
    )
