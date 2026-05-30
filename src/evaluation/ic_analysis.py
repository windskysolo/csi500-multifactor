"""
src/evaluation/ic_analysis.py — 单因子 IC 检验框架
==========================================================

核心功能：
  compute_rank_ic         单截面 Rank IC（Spearman 相关系数）
  compute_ic_series       跨所有调仓日的 IC 时序
  build_ic_history        构建 date × factor 的月度 Rank IC 历史矩阵
  ic_summary              IC 统计量（IC_IR / t 统计量 / p 值 / IC>0 占比）
  compute_forward_returns T+1 开盘买入 → T'+1 开盘卖出的月度收益率面板
  batch_ic_test           批量因子 IC 检验，含 BH 多重检验校正
  compute_factor_correlation 因子间平均截面 Rank 相关矩阵
  filter_redundant_factors   贪心去冗余：按 |IC_IR| 降序保留，高相关重复因子丢弃

Forward Return 对齐约定（严格 T+1 开盘成交）：
  fwd_ret[T, stock] = open_adj[T'+1] / open_adj[T+1] - 1
  T+1  = T 之后的第一个实际交易日（买入）
  T'   = T 之后的下一个调仓日
  T'+1 = T' 之后的第一个实际交易日（卖出）

此对齐方式比 T 收盘→T' 收盘更准确，消除了隔夜因子建仓的前视偏差。

有效因子标准（训练集，方向性建议）：
  |IC_IR| ≥ 0.3  &  |IC 均值| ≥ 0.02  &  p < 0.05 (BH 校正后)
  & pct_consistent_dir ≥ 55%（方向一致性 = max(pct_positive, 1-pct_positive)）

注意：有效性判断用方向一致性（不区分正负向因子），负向因子（vol_60d、accrual 等）
IC 稳定为负时，pct_consistent_dir = 1 - pct_positive，仍可被正确识别为有效。
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

from src import config as cfg
from src.data.loader import load_daily_quote
from src.data.universe import get_trade_dates
from src.factors.price_factors import _all_trading_dates

log = logging.getLogger(__name__)

# 有效因子判断阈值（来自 src/config.py，不作为硬断言）
IC_IR_THRESHOLD   = cfg.EVAL_IC_EFFECTIVE_CRITERIA["min_ic_ir"]
IC_MEAN_THRESHOLD = cfg.EVAL_IC_EFFECTIVE_CRITERIA["min_ic_mean"]
PVALUE_ALPHA      = cfg.EVAL_IC_EFFECTIVE_CRITERIA["max_p_bh"]
IC_POS_THRESHOLD  = cfg.EVAL_IC_EFFECTIVE_CRITERIA["min_dir"]


# ---------------------------------------------------------------------------
# 核心 IC 计算
# ---------------------------------------------------------------------------

def compute_rank_ic(factor: pd.Series, fwd_ret: pd.Series) -> float:
    """
    计算单截面 Rank IC（Spearman 相关系数）。

    在 factor 和 fwd_ret 均非 NaN 的股票交集上计算，NaN 不参与排名。
    若有效样本数 < 10，返回 NaN（样本太少，统计意义不可靠）。

    Args:
        factor:  当期截面因子值（ts_code 为 index）
        fwd_ret: 对应的下期收益率（ts_code 为 index）
    Returns:
        Rank IC 值（−1 到 1）；有效样本不足时为 NaN
    """
    common = factor.dropna().index.intersection(fwd_ret.dropna().index)
    if len(common) < 10:
        return np.nan
    return float(factor[common].corr(fwd_ret[common], method="spearman"))


def compute_ic_series(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    min_coverage: float = 0.0,
) -> pd.Series:
    """
    计算跨所有调仓日的月度 Rank IC 时序。

    Args:
        factor_panel:  行=调仓日, 列=ts_code 的因子值 DataFrame
        fwd_ret_panel: 行=调仓日, 列=ts_code 的下期收益 DataFrame（同 compute_forward_returns 输出）
        min_coverage:  覆盖率下限（0.0–1.0）。当期非 NaN 因子数 / 总列数 < 此值时跳过该期。
                       0.0 表示不过滤（默认，向后兼容）。P1 覆盖率校正建议传 0.50。
    Returns:
        以调仓日为 index 的 IC Series；某期有效样本不足或覆盖率不足的日期被过滤
    """
    dates = factor_panel.index.intersection(fwd_ret_panel.index)
    if min_coverage > 0.0:
        # 覆盖率 = 当期有效股票数 / 当期"典型最大有效股票数"
        # 用 max(notna_per_date) 而非 len(columns)：factor_panel 的列是所有期合并宇宙
        # （可能有 2000+ 列），而每期实际可投资只有 ~500 只。直接用列数做分母会使
        # 覆盖率永远 < 50%，导致所有期被过滤。用最大有效数作分母，仅排除
        # 当期有效股票数显著少于典型水平的日期（如 hk_hold_ratio 2014 年前）。
        notna_per_date = factor_panel.notna().sum(axis=1)
        max_notna = notna_per_date.max()
        if max_notna > 0:
            dates = [d for d in dates
                     if notna_per_date.get(d, 0) / max_notna >= min_coverage]
    ic_vals = [
        compute_rank_ic(factor_panel.loc[d], fwd_ret_panel.loc[d])
        for d in dates
    ]
    return pd.Series(ic_vals, index=dates, name="ic").dropna()


def build_ic_history(
    factor_panels: dict[str, "pd.DataFrame"],
    fwd_ret_panel: "pd.DataFrame",
    factor_names: "list[str] | None" = None,
    min_coverage: float = 0.0,
) -> "pd.DataFrame":
    """
    构建 date × factor 的月度 Rank IC 历史矩阵。

    对每个因子调用 compute_ic_series()，汇聚为一张宽表。
    不同因子的有效日期可能不同（如数据起点不一致），合并时会产生 NaN；
    这是正常现象，调用方应在 metadata 中披露覆盖率不足的因子。

    Args:
        factor_panels: 因子面板字典，key=因子名，value=行=调仓日、列=ts_code 的 DataFrame。
        fwd_ret_panel: 下期收益面板，调用方必须已按 exit_date 过滤至所需样本区间。
        factor_names:  可选因子名列表；None 表示使用全部 factor_panels。
                       列表中若含 factor_panels 中不存在的因子，抛 KeyError。
        min_coverage:  覆盖率下限，透传给 compute_ic_series（0.0 = 不过滤）。
    Returns:
        DataFrame，index=rebalance_date（已 sort_index），columns=factor_name，dtype=float64。
        某日某因子 IC 计算失败（有效样本 < 10 或覆盖不足）时对应单元格为 NaN。
    时间对齐假设：
        本函数不自行切分训练/验证边界；调用方负责传入已按 exit_date 切分好的 fwd_ret_panel。
    数据依赖：
        仅依赖传入参数，不读取磁盘。
    Raises:
        KeyError: factor_names 中包含 factor_panels 里不存在的因子名。
    """
    if factor_names is None:
        factor_names = list(factor_panels)

    missing = [n for n in factor_names if n not in factor_panels]
    if missing:
        raise KeyError(f"以下因子在 factor_panels 中不存在：{missing}")

    series_dict: dict[str, pd.Series] = {}
    for name in factor_names:
        ic_s = compute_ic_series(factor_panels[name], fwd_ret_panel, min_coverage=min_coverage)
        series_dict[name] = ic_s

    if not series_dict:
        empty = pd.DataFrame(columns=factor_names, dtype="float64")
        empty.index.name = "rebalance_date"
        return empty

    result = pd.DataFrame(series_dict).sort_index()
    result.index.name = "rebalance_date"
    return result


def ic_summary(ic_series: pd.Series) -> dict:
    """
    从 IC 时序计算评估统计量。

    Returns 的 dict 字段：
      n           有效 IC 观测数
      ic_mean     IC 均值
      ic_std      IC 标准差（ddof=1）
      ic_ir       IC_IR = IC_mean / IC_std
      t_stat      t 统计量 = IC_mean / (IC_std / sqrt(n))
      p_value     双尾 p 值（t 分布，df=n-1）
      pct_positive IC > 0 的月份占比
    """
    valid = ic_series.dropna()
    n = len(valid)
    _nan_dict = {k: np.nan for k in
                 ["ic_mean", "ic_std", "ic_ir", "t_stat", "p_value", "pct_positive"]}

    if n < 2:
        return {"n": n, **_nan_dict}

    ic_mean = float(valid.mean())
    ic_std  = float(valid.std(ddof=1))

    if ic_std < 1e-12:
        # 不使用 _nan_dict（其中含 ic_mean/ic_std 键会覆盖实际值），逐字段显式返回
        return {
            "n":            n,
            "ic_mean":      round(ic_mean, 4),
            "ic_std":       round(ic_std, 4),
            "ic_ir":        np.nan,
            "t_stat":       np.nan,
            "p_value":      np.nan,
            "pct_positive": np.nan,
        }

    ic_ir  = ic_mean / ic_std
    se     = ic_std / np.sqrt(n)
    t_stat = ic_mean / se
    p_val  = float(2 * (1 - stats.t.cdf(abs(t_stat), df=n - 1)))
    pct_pos = float((valid > 0).mean())

    return {
        "n":            n,
        "ic_mean":      round(ic_mean,  4),
        "ic_std":       round(ic_std,   4),
        "ic_ir":        round(ic_ir,    4),
        "t_stat":       round(t_stat,   4),
        "p_value":      round(p_val,    6),
        "pct_positive": round(pct_pos,  4),
    }


# ---------------------------------------------------------------------------
# Exit Date 映射（用于按 exit_date 切分样本，防止 forward return 跨边界）
# ---------------------------------------------------------------------------

def build_exit_date_map(
    rebalance_dates: list[pd.Timestamp],
) -> pd.Series:
    """
    构建调仓日 T → 卖出执行日 (T'+1) 的映射。

    与 compute_forward_returns 使用完全相同的对齐逻辑：
      exit_date = 下一个调仓日 T' 之后的第一个实际交易日
    最后一个调仓日没有对应 T'，不纳入结果（其 fwd_ret 全为 NaN）。

    用途：将 fwd_ret_panel 按 exit_date 而非 T（label index）做样本切分，
    防止最后若干期的 forward return 跨越训练/验证/测试边界。
    例：T=2020-12-31 的 exit_date ≈ 2021-02-07 → 不应纳入训练集。

    Args:
        rebalance_dates: 有序调仓日列表
    Returns:
        pd.Series，index=T，values=exit_date；最后一期无 T'，不含
    """
    all_dates = _all_trading_dates()
    result: dict[pd.Timestamp, pd.Timestamp] = {}
    for i, T in enumerate(rebalance_dates[:-1]):
        T_prime = rebalance_dates[i + 1]
        after_T_prime = all_dates[all_dates > T_prime]
        if len(after_T_prime) == 0:
            continue
        result[T] = after_T_prime[0]
    if not result:
        return pd.Series(dtype="datetime64[ns]", name="exit_date")
    return pd.Series(result, name="exit_date")


# ---------------------------------------------------------------------------
# Forward Return 计算（T+1 开盘 → T'+1 开盘）
# ---------------------------------------------------------------------------

def compute_forward_returns(
    rebalance_dates: list[pd.Timestamp],
    codes: list[str],
    codes_by_date: Optional[dict[pd.Timestamp, list[str]]] = None,
    benchmark_code: Optional[str] = None,
) -> pd.DataFrame:
    """
    计算 T+1 开盘买入、T'+1 开盘卖出的月度收益率面板。

    对齐约定：
      fwd_ret[T, stock] = open_adj[T'+1] / open_adj[T+1] - 1
      T+1：T 之后的第一个实际交易日（买入点）
      T'：下一个调仓日
      T'+1：T' 之后的第一个实际交易日（卖出点）

    最后一个调仓日无下一期，其 forward return 全为 NaN（不计入 IC 计算）。

    数据依赖：daily_quote.open_adj；超额模式另需 index_quote.parquet 的 nav 列。

    Args:
        rebalance_dates: 调仓日列表（有序，通常来自 get_trade_dates）
        codes:           股票代码列表（全区间静态并集，兜底参数）
        codes_by_date:   每个调仓日的可投资股票代码 dict（推荐）。
                         提供时取所有 date 的 codes 并集加载数据，使每期新进
                         成分股（动态变动）也能获得 forward return，避免样本
                         被锚定在首期成分股上。未提供时退化为静态 codes 列表。
        benchmark_code:  基准指数代码（如 "000905.SH"，中证500全收益指数）。
                         提供时返回超额收益（个股收益 - 基准区间收益）；
                         None 返回原始收益（向后兼容）。
                         基准数据来自 index_quote.parquet 的 nav 列（全收益）。
    Returns:
        行=调仓日（不含最后一期）, 列=ts_code 的（超额）收益率 DataFrame
    """
    all_dates = _all_trading_dates()

    # 确定实际加载的股票代码集合
    if codes_by_date is not None:
        # 取所有调仓日可投资股票的并集，确保动态新进成分股也有 forward return
        all_codes: list[str] = sorted(set().union(*codes_by_date.values()))
    else:
        all_codes = codes

    # 为每个调仓日确定 (entry_date=T+1, exit_date=T'+1) 对
    entry_exit_pairs: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
    for i, T in enumerate(rebalance_dates[:-1]):   # 最后一期无下一调仓日，跳过
        T_prime = rebalance_dates[i + 1]

        after_T      = all_dates[all_dates > T]
        after_T_prime = all_dates[all_dates > T_prime]

        if len(after_T) == 0 or len(after_T_prime) == 0:
            log.warning("找不到 %s 或 %s 之后的交易日，跳过", T.date(), T_prime.date())
            continue

        entry_exit_pairs.append((T, after_T[0], after_T_prime[0]))

    if not entry_exit_pairs:
        return pd.DataFrame(index=pd.DatetimeIndex([]), columns=all_codes)

    # 一次性加载所有需要的交易日的 open_adj（避免 N 次 I/O）
    all_load_dates = sorted({d for _, e, x in entry_exit_pairs for d in (e, x)})
    start_load = all_load_dates[0]
    end_load   = all_load_dates[-1]

    dq = load_daily_quote(start_load, end_load, codes=all_codes)
    open_pivot = dq["open_adj"].unstack("ts_code")  # (trade_dates × ts_codes)

    # 加载基准区间收益（仅在 benchmark_code 非 None 时）
    bm_ret: dict[pd.Timestamp, float] = {}
    active_benchmark = benchmark_code
    if active_benchmark is not None:
        iq = pd.read_parquet(cfg.DATA_PROC / "index_quote.parquet")
        if "nav" not in iq.columns:
            log.warning("index_quote.parquet 缺少 nav 列，benchmark_code=%s 已忽略", benchmark_code)
            active_benchmark = None
        else:
            nav = iq["nav"]
            for T, entry_date, exit_date in entry_exit_pairs:
                if entry_date in nav.index and exit_date in nav.index:
                    bm_ret[T] = float(nav.loc[exit_date] / nav.loc[entry_date] - 1)
                else:
                    log.warning("基准 nav 在 %s 或 %s 无数据，该期不扣减", entry_date.date(), exit_date.date())
                    bm_ret[T] = 0.0

    # 计算每个调仓日的截面 forward return
    result: dict[pd.Timestamp, pd.Series] = {}
    for T, entry_date, exit_date in entry_exit_pairs:
        if entry_date not in open_pivot.index or exit_date not in open_pivot.index:
            log.warning("%s 或 %s 不在 daily_quote 中", entry_date.date(), exit_date.date())
            continue
        fwd = open_pivot.loc[exit_date] / open_pivot.loc[entry_date] - 1
        if active_benchmark is not None:
            fwd = fwd - bm_ret.get(T, 0.0)
        result[T] = fwd

    if not result:
        return pd.DataFrame(index=pd.DatetimeIndex([]), columns=all_codes)

    fwd_panel = pd.DataFrame(result).T
    fwd_panel.index.name = "rebalance_date"
    log.info(
        "compute_forward_returns: %d 期收益计算完成（%s ~ %s）",
        len(fwd_panel),
        fwd_panel.index[0].date() if len(fwd_panel) else "N/A",
        fwd_panel.index[-1].date() if len(fwd_panel) else "N/A",
    )
    return fwd_panel


# ---------------------------------------------------------------------------
# 批量因子 IC 检验（含 BH 多重检验校正）
# ---------------------------------------------------------------------------

def batch_ic_test(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    alpha: float = PVALUE_ALPHA,
    min_coverage: float = 0.0,
) -> pd.DataFrame:
    """
    对多个因子同时做 IC 检验，并用 BH 法校正多重检验。

    BH（Benjamini-Hochberg）校正：
      对所有因子的原始 p_value 统一校正，控制 FDR（假阳性率）。
      比 Bonferroni 更宽松，适合因子筛选场景（不追求 FWER）。

    Args:
        factor_panels: dict，key=因子名，value=行×列=调仓日×ts_code 的因子面板
        fwd_ret_panel: 前向收益率面板（来自 compute_forward_returns）
        alpha:         BH 校正的显著性水平（默认 0.05）
        min_coverage:  覆盖率下限，传给 compute_ic_series（0.0 = 不过滤，向后兼容）
    Returns:
        每行一个因子的汇总 DataFrame，列包含：
          n / ic_mean / ic_std / ic_ir / t_stat / p_value /
          p_value_bh（BH 校正后）/ significant_bh（是否通过 BH 校正）/
          pct_positive / effective（综合评价：全部有效标准通过时为 True）
    """
    rows: list[dict] = []
    ic_series_map: dict[str, pd.Series] = {}

    for name, panel in factor_panels.items():
        ic_s = compute_ic_series(panel, fwd_ret_panel, min_coverage=min_coverage)
        ic_series_map[name] = ic_s
        summary = ic_summary(ic_s)
        summary["factor"] = name
        rows.append(summary)

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows).set_index("factor")

    # BH 多重检验校正（仅对 p_value 非 NaN 的因子做校正）
    # 先初始化为安全默认值，防止 NaN 在后续 astype(bool) 时被误判为 True
    result["p_value_bh"]    = np.nan
    result["significant_bh"] = False

    valid_mask = result["p_value"].notna()
    if valid_mask.any():
        p_raw = result.loc[valid_mask, "p_value"].values
        _, p_corrected, _, _ = multipletests(p_raw, method="fdr_bh", alpha=alpha)
        result.loc[valid_mask, "p_value_bh"]    = p_corrected
        result.loc[valid_mask, "significant_bh"] = p_corrected < alpha

    # 方向一致性：max(pct_positive, 1 - pct_positive)
    # 正向因子 IC>0 时等于 pct_positive；负向因子 IC<0 时等于 1 - pct_positive。
    # 解决"负向有效因子因 pct_positive<0.55 被错误标为无效"的问题。
    result["pct_consistent_dir"] = result["pct_positive"].apply(
        lambda p: max(p, 1.0 - p) if pd.notna(p) else np.nan
    )

    # 综合有效性判断：每个比较表达式用括号隔开（& 优先级高于 >=，必须括号）
    result["effective"] = (
        (result["ic_ir"].abs()              >= IC_IR_THRESHOLD)
        & (result["ic_mean"].abs()          >= IC_MEAN_THRESHOLD)
        & result["significant_bh"]
        & (result["pct_consistent_dir"]     >= IC_POS_THRESHOLD)
    )

    # 排序：IC_IR 绝对值降序
    result = result.sort_values("ic_ir", key=lambda x: x.abs(), ascending=False)
    return result


# ---------------------------------------------------------------------------
# 因子相关性矩阵
# ---------------------------------------------------------------------------

def compute_factor_correlation(
    factor_panels: dict[str, pd.DataFrame],
    min_stocks: int = 20,
) -> pd.DataFrame:
    """
    计算因子间的平均截面 Rank 相关系数矩阵。

    方法：对每个调仓日 T 独立计算各因子的横截面 Spearman 相关矩阵，
    再在时间轴上取均值。每对因子独立统计有效期数，避免某些配对日期
    不一致导致的平均偏差。

    用途：识别 |r| > 0.7 的冗余因子对——高相关因子在 IC_IR 加权合成时
    会重复计数同一风险敞口，IC 离散度也会被低估。

    Args:
        factor_panels: dict，key=因子名，value=行=调仓日、列=ts_code 的因子面板
        min_stocks:    每个截面有效股票数下限（低于此的截面不纳入均值）
    Returns:
        factor × factor 的平均截面 Rank 相关系数矩阵（对角线为 1.0）；
        若所有截面均低于 min_stocks，对应配对返回 NaN
    """
    factor_names = list(factor_panels.keys())
    if len(factor_names) < 2:
        log.warning("compute_factor_correlation: 因子数 < 2，无法计算相关矩阵")
        return pd.DataFrame(index=factor_names, columns=factor_names, dtype=float)

    # 取所有因子面板调仓日的交集
    dates = sorted(set.intersection(*[set(p.index) for p in factor_panels.values()]))
    if not dates:
        log.warning("compute_factor_correlation: 因子面板无公共调仓日")
        return pd.DataFrame(np.nan, index=factor_names, columns=factor_names)

    corr_sum = pd.DataFrame(0.0, index=factor_names, columns=factor_names)
    count    = pd.DataFrame(0,   index=factor_names, columns=factor_names)

    for T in dates:
        cross = pd.DataFrame({name: factor_panels[name].loc[T] for name in factor_names})
        cross = cross.dropna(how="all")   # 剔除全因子均缺失的股票
        if len(cross) < min_stocks:
            continue
        corr_mat  = cross.corr(method="spearman")
        valid_mask = corr_mat.notna()
        corr_sum  += corr_mat.fillna(0.0)
        count     += valid_mask.astype(int)

    # 每对因子独立除以其有效期数（count=0 的配对返回 NaN）
    avg_corr = corr_sum.where(count > 0, np.nan) / count.where(count > 0, np.nan)
    log.info(
        "compute_factor_correlation: 基于 %d 期截面，%d×%d 相关矩阵",
        len(dates), len(factor_names), len(factor_names),
    )
    return avg_corr


# ---------------------------------------------------------------------------
# 冗余因子过滤
# ---------------------------------------------------------------------------

def filter_redundant_factors(
    ic_result: pd.DataFrame,
    corr_matrix: pd.DataFrame,
    corr_threshold: float = 0.70,
) -> list[str]:
    """
    贪心过滤高相关冗余因子。

    按 |IC_IR| 降序遍历已通过 IC 检验的因子（ic_result["effective"]=True），
    若当前因子与已纳入因子的任意一个平均截面相关系数（绝对值）> corr_threshold，
    则丢弃（保留 IC_IR 更强的）。

    不修改任何传入对象；保持 |IC_IR| 降序。

    Args:
        ic_result:      batch_ic_test 输出的汇总 DataFrame（index=因子名，含 "effective" 列）
        corr_matrix:    compute_factor_correlation 输出的截面平均相关矩阵
        corr_threshold: 高相关阈值，默认 0.70
    Returns:
        去冗余后保留的因子名列表（按 |IC_IR| 降序）
    Raises:
        ValueError: ic_result 缺少 "effective" 列
    """
    if "effective" not in ic_result.columns:
        raise ValueError("ic_result 缺少 'effective' 列，请传入 batch_ic_test 的输出")

    candidates = (
        ic_result[ic_result["effective"]]
        .sort_values("ic_ir", key=abs, ascending=False)
        .index.tolist()
    )

    selected: list[str] = []
    for factor in candidates:
        if not selected:
            selected.append(factor)
            continue
        comparable = [
            f for f in selected
            if f in corr_matrix.columns and factor in corr_matrix.index
        ]
        if not comparable:
            selected.append(factor)
            continue
        max_corr = corr_matrix.loc[factor, comparable].abs().max()
        if max_corr <= corr_threshold:
            selected.append(factor)
        else:
            log.debug(
                "去冗余：过滤 %s（与已选因子最高相关 %.2f > 阈值 %.2f）",
                factor, max_corr, corr_threshold,
            )

    return selected


# ---------------------------------------------------------------------------
# IC Decay 检验（Phase 3 新增）
# ---------------------------------------------------------------------------

def compute_ic_decay(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    max_lag: int = 3,
    min_coverage: float = 0.0,
) -> dict[str, dict]:
    """
    计算因子在 lag 1, 2, ..., max_lag 的 IC 统计量，用于信号衰减分析。

    方法：
      lag=1: IC(factor_T, fwd_ret[T])          原始 IC（与 batch_ic_test 一致）
      lag=2: IC(factor_T, fwd_ret[T+1 period]) 即 fwd_ret_panel.shift(-1) 对齐到 T
      lag=3: IC(factor_T, fwd_ret[T+2 period]) 即 fwd_ret_panel.shift(-2) 对齐到 T

    注意：这里测量"当期因子能否预测 k 期后的单期收益"，而非累积收益。
    好的月频因子应在 lag2–3 仍保持与 lag1 相同的符号方向。

    月频可用性标准（参见 improvement_guide.md 阶段 3.5）：
      - lag3 IC_IR 与 lag1 IC_IR 同号 → 信号持续性合格，可月频使用
      - lag2 方向已反转 → 信号寿命不足 2 个月，月频换仓难以捕捉

    Args:
        factor_panel:  行=调仓日, 列=ts_code 的因子值 DataFrame
        fwd_ret_panel: forward return 面板（lag=1 的原始 1 期收益）
        max_lag:       最大检验 lag 期数（默认 3）
        min_coverage:  覆盖率下限，传给 compute_ic_series（0.0 = 不过滤）
    Returns:
        dict，key = "lag1" / "lag2" / ...，value = ic_summary 统计量 dict
    """
    result: dict[str, dict] = {}
    for lag in range(1, max_lag + 1):
        # lag=1: 不做 shift；lag=k: 将 fwd_ret 向前移动 k-1 期
        shifted_fwd = fwd_ret_panel if lag == 1 else fwd_ret_panel.shift(-(lag - 1))
        ic_s = compute_ic_series(factor_panel, shifted_fwd, min_coverage=min_coverage)
        result[f"lag{lag}"] = ic_summary(ic_s)
    return result


def batch_ic_decay(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    max_lag: int = 3,
    min_coverage: float = 0.0,
) -> pd.DataFrame:
    """
    批量计算所有因子的 IC Decay 统计量。

    输出 DataFrame 包含 icir_lag1 / icir_lag2 / icir_lag3（最多到 max_lag），
    以及 ic_decay_pass 列（lag3 IC_IR 与 lag1 IC_IR 同号则 True）。

    ic_decay_pass=False 的因子信号在第 3 个月已反转，月频持仓期间无法有效捕捉，
    视为不适合月频使用（final_include 排除候选）。

    Args:
        factor_panels: dict，key=因子名，value=因子面板
        fwd_ret_panel: forward return 面板
        max_lag:       最大 lag 期数（默认 3）
        min_coverage:  覆盖率下限（0.0 = 不过滤）
    Returns:
        DataFrame，index=因子名，列包含 icir_lag1/lag2/lag3 及 ic_decay_pass
    """
    rows: list[dict] = []
    for name, panel in factor_panels.items():
        decay = compute_ic_decay(panel, fwd_ret_panel, max_lag, min_coverage)
        row: dict = {"factor": name}
        for lag_key, stats in decay.items():
            row[f"icir_{lag_key}"] = stats.get("ic_ir")
        rows.append(row)

    df = pd.DataFrame(rows).set_index("factor") if rows else pd.DataFrame()

    # ic_decay_pass：lag3 IC_IR 与 lag1 IC_IR 同号
    lag1_col = "icir_lag1"
    lag3_col = f"icir_lag{max_lag}" if max_lag >= 3 else f"icir_lag{max_lag}"
    if lag1_col in df.columns and lag3_col in df.columns:
        lag1 = df[lag1_col]
        lag3 = df[lag3_col]
        df["ic_decay_pass"] = (
            lag1.notna() & lag3.notna() & ((lag1 * lag3) > 0)
        )
    else:
        df["ic_decay_pass"] = True

    log.info(
        "batch_ic_decay: %d 个因子，ic_decay_pass=%d / %d",
        len(df), int(df["ic_decay_pass"].sum()), len(df),
    )
    return df
