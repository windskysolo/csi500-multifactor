"""
src/portfolio/optimizer.py — 组合权重优化器
==========================================================

优化目标（L1 严格路线）：
  max  α^T · w  [- λ_TC · ‖w - w_prev‖₁]
                （λ_TC > 0 且有上期权重 w_prev 时生效；λ_TC = 0 时退化为无惩罚形式）
  s.t. (w - w_b)^T · Σ_daily · (w - w_b) ≤ TE_target^2 / 252   # 跟踪误差约束（日频方差形式）
       |Σ_ind(w_i - w_b_i)| ≤ industry_max_dev  ∀ industry       # 行业偏离
       |w_i - w_b_i| ≤ single_max_dev           ∀ stock           # 单股偏离
       w_i ≥ 0                                                     # 纯多头
       Σ w_i = 1                                                   # 全仓

三层 Fallback（按顺序尝试）：
  L1 → L2（去掉跟踪误差二次约束）→ L3（TopN 等权）

停牌 / 涨跌停处理：
  - 停牌：等式约束 w_i = w_prev_i（锁定上期权重）
  - 涨停：不等式约束 w_i ≤ w_prev_i（不能买入）
  - 跌停：不等式约束 w_i ≥ w_prev_i（不能卖出）
  第一期（w_prev=None）不加涨跌停约束。

NaN alpha 处理：
  NaN alpha 视为"无观点"，置 0（让优化器根据约束分配），不排除出可行域。
  排除会改变基准归一化，风险更高。

optimal_inaccurate 处理：
  接受解，执行 clip(w, 0) + 归一化后处理。
  若最大负权重绝对值 > MAX_NEG_WEIGHT_THRESHOLD，则认为解质量太差，降级到 L2。

时间对齐：
  本模块不访问外部数据，不做 PIT 检查。
  调用方须保证 alpha/w_b/cov 对齐到同一 codes 列表（相同长度和顺序）。
"""

import logging
import math
import time
from dataclasses import dataclass, field

import cvxpy as cp
import numpy as np
import pandas as pd

from src import config as cfg

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ACCEPTABLE_STATUSES: frozenset[str] = frozenset({"optimal", "optimal_inaccurate"})
MAX_NEG_WEIGHT_THRESHOLD: float = 0.005  # optimal_inaccurate 时最大负权重阈值，超过则降 L2
TRADING_DAYS_PER_YEAR: int = 252


# ---------------------------------------------------------------------------
# 配置与结果数据结构
# ---------------------------------------------------------------------------

@dataclass
class OptimizeConfig:
    """优化器参数（所有字段均有默认值，可按需覆盖）。研究决策参数默认值来自 src/config.py。"""
    te_target_annual: float = cfg.OPT_TE_TARGET_ANNUAL   # 年化跟踪误差目标
    industry_max_dev: float = cfg.OPT_INDUSTRY_MAX_DEV   # 行业权重最大偏离（绝对值）
    single_max_dev: float   = cfg.OPT_SINGLE_MAX_DEV     # 单股权重最大偏离（绝对值）
    topn: int               = cfg.OPT_TOPN               # L3 兜底取前 N 只
    turnover_lambda: float  = cfg.OPT_TURNOVER_LAMBDA    # 换手成本惩罚系数；0 = 不启用
    max_solve_seconds: float = 30.0                      # 超时触发 Fallback（工程参数）
    force_l2: bool = False                               # 跳过 L1（TE 二次约束），直接从 L2 开始
    solver_order: list[str] = field(
        default_factory=lambda: ["CLARABEL", "SCS"]
    )
    prefilter_topn: int = 0          # 0=不预筛; N>0=仅 top-N 股票参与主动配置
    prefilter_mode: str = "none"     # "none"|"zero_alpha"|"lock_benchmark"


@dataclass
class OptimizeResult:
    """单期优化结果。"""
    weights: pd.Series      # ts_code → weight，index 与输入 alpha.index 一致，和=1
    fallback_level: int     # 0=L1, 1=L2, 2=L3
    solver_status: str      # cvxpy 状态字符串；L3 时为 'topn_fallback'
    solve_time_s: float     # 求解耗时（秒）
    cov_available: bool = True          # F6-002: 当期是否有真实协方差矩阵；False 时 fallback_level 强制 ≥ 1
    constraint_compliant: bool = True   # F6-001: L3 路径是否满足停牌/涨停/单股偏离全部约束


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _build_industry_matrix(
    codes: list[str],
    industry_map: pd.Series,
) -> np.ndarray:
    """
    构建行业指示矩阵 A，形状 (n_industries × n_stocks)。

    A[i, j] = 1 当且仅当 codes[j] 属于第 i 个行业。
    不在 industry_map 中的股票对应全零列（不参与行业约束）。

    Args:
        codes:        股票代码列表，长度 n
        industry_map: ts_code → industry_code 的 Series
    Returns:
        A (n_ind × n) float ndarray
    """
    if industry_map.empty:
        return np.zeros((0, len(codes)), dtype=float)

    industries = sorted(industry_map.unique())
    ind_idx = {ind: i for i, ind in enumerate(industries)}
    n, n_ind = len(codes), len(industries)

    A = np.zeros((n_ind, n), dtype=float)
    for j, code in enumerate(codes):
        if code in industry_map.index:
            ind = industry_map[code]
            if ind in ind_idx:
                A[ind_idx[ind], j] = 1.0
    return A


def _build_portfolio_constraints(
    w: cp.Variable,
    w_b_vec: np.ndarray,
    A_ind: np.ndarray,
    config: OptimizeConfig,
    halt_indices: set[int],
    limit_up_idx: set[int],
    limit_dn_idx: set[int],
    w_prev_vec: np.ndarray | None,
) -> list:
    """
    构建 L1/L2 共用的线性约束集合。

    包括：全仓、纯多头、单股偏离、行业偏离、停牌锁定、涨跌停单边约束。
    """
    dev = w - w_b_vec
    constraints: list = [
        cp.sum(w) == 1.0,
        w >= 0.0,
    ]
    # 停牌锁定生效时停牌股的偏离量外生（= w_prev - w_b），若叠加单股约束则 L2 真实不可行（Bug-1 修复）
    if w_prev_vec is not None and halt_indices:
        n_stocks = w_b_vec.shape[0]
        free_mask = np.array([i for i in range(n_stocks) if i not in halt_indices])
        if len(free_mask) > 0:
            constraints += [
                dev[free_mask] <= config.single_max_dev,
                dev[free_mask] >= -config.single_max_dev,
            ]
    else:
        constraints += [
            dev <= config.single_max_dev,
            dev >= -config.single_max_dev,
        ]

    # 行业约束（A_ind 为空时自动跳过）
    if A_ind.shape[0] > 0:
        ind_dev = A_ind @ dev
        constraints += [
            ind_dev <= config.industry_max_dev,
            ind_dev >= -config.industry_max_dev,
        ]

    if w_prev_vec is None:
        return constraints

    # 停牌：锁定上期权重
    for i in halt_indices:
        constraints.append(w[i] == w_prev_vec[i])

    # 涨停：不能买入；跌停：不能卖出（停牌优先，不重复添加）
    for i in limit_up_idx:
        if i not in halt_indices:
            constraints.append(w[i] <= w_prev_vec[i])
    for i in limit_dn_idx:
        if i not in halt_indices:
            constraints.append(w[i] >= w_prev_vec[i])

    return constraints


def _postprocess_weights(
    w_vals: np.ndarray,
    status: str,
) -> tuple[np.ndarray, bool]:
    """
    裁剪数值误差产生的微小负权重并重新归一化。

    Returns:
        (processed_weights, trigger_l2)
        trigger_l2=True 表示 optimal_inaccurate 且负权重超阈值，建议降级 L2
    """
    min_val = float(np.min(w_vals))
    trigger_l2 = False

    if status == "optimal_inaccurate":
        if min_val < -MAX_NEG_WEIGHT_THRESHOLD:
            log.warning(
                "optimal_inaccurate: 最大负权重 %.4f 超阈值 %.4f，触发 L2",
                min_val, -MAX_NEG_WEIGHT_THRESHOLD,
            )
            trigger_l2 = True
        else:
            log.warning(
                "optimal_inaccurate: 数值误差较小（min_w=%.5f），后处理修复", min_val
            )

    w = np.clip(w_vals, 0.0, None)
    total = float(w.sum())
    w = w / total if total > 1e-10 else np.ones(len(w)) / len(w)
    return w, trigger_l2


def _try_solve(
    problem: cp.Problem,
    config: OptimizeConfig,
) -> tuple[bool, str, float]:
    """
    按 config.solver_order 顺序尝试求解，返回第一个可接受的结果。

    Returns:
        (success, status, elapsed_seconds)
    """
    status = "not_solved"
    elapsed = 0.0

    for solver_name in config.solver_order:
        solver_const = getattr(cp, solver_name, None)
        if solver_const is None:
            log.warning("求解器 %s 在当前 cvxpy 版本不可用，跳过", solver_name)
            continue
        try:
            t0 = time.perf_counter()
            problem.solve(solver=solver_const, warm_start=True)
            elapsed = time.perf_counter() - t0
            status = problem.status or "unknown"

            if status in ACCEPTABLE_STATUSES and elapsed <= config.max_solve_seconds:
                return True, status, elapsed

            log.debug("求解器 %s: status=%s, time=%.2fs", solver_name, status, elapsed)

        except Exception as exc:
            log.warning("求解器 %s 异常: %s", solver_name, exc)
            status = f"solver_error({solver_name})"

    return False, status, elapsed


def _topn_equal_weight(
    alpha_vec: np.ndarray,
    n: int,
    topn: int,
    halt_indices: set[int],
    no_buy_indices: set[int] | None = None,
    limit_dn_indices: set[int] | None = None,
    w_prev_vec: np.ndarray | None = None,
    single_max_dev: float = 0.01,
    w_b_vec: np.ndarray | None = None,
) -> tuple[np.ndarray, bool]:  # F6-001: 返回 (weights, constraint_compliant)
    """
    L3 兜底：保留停牌锁定和跌停不可减仓约束，在可交易预算内 TopN 等权。

    约束优先级（F6-001 修复）：
      1. 停牌股：锁定上期权重（必须提供 w_prev_vec，否则置 0 并记录 warning）
      2. 跌停股（非停牌）：锁定上期权重下限（持有上期权重，不减仓）
      3. 涨停股（非停牌、非跌停）：排除出选股池（不可买入）
      4. 剩余可交易预算：在非停牌/非跌停/非涨停的股票中选取有效 topn 等权分配

    有效 topn（effective_topn）= max(topn, ceil(free_budget / single_max_dev))。
    这保证每股权重 ≤ single_max_dev（最坏情形 w_b_i=0）；无法满足时仍取全部候选。

    Args:
        alpha_vec:        长度 n 的因子值数组（NaN 合法）
        n:                股票总数
        topn:             期望选取数量（config.topn）
        halt_indices:     停牌股索引集合（锁定 w_prev）
        no_buy_indices:   涨停等不可新买入股票的索引集合；None 等价于空集
        limit_dn_indices: 跌停不可卖出股票的索引集合；None 等价于空集
        w_prev_vec:       上期权重向量；None=第一期
        single_max_dev:   单股权重最大偏离（相对基准），用于计算最少选股数
        w_b_vec:          基准权重向量；用于精确计算 n_min，None 时保守取 w_b=0
    Returns:
        (weights, constraint_compliant)
        weights:              长度 n 的权重数组，和=1
        constraint_compliant: False 表示"无可买候选"时回退方案违反了涨停/偏离约束，
                              调用方应在 metadata 中标记并拒绝将此权重作为合规目标。
    """
    no_buy  = no_buy_indices   or set()
    lim_dn  = limit_dn_indices or set()

    w = np.zeros(n)
    locked_budget = 0.0

    if w_prev_vec is not None:
        # 停牌：锁定上期权重
        for i in halt_indices:
            w[i] = w_prev_vec[i]
            locked_budget += w_prev_vec[i]

        # 跌停（非停牌）：不得减仓，锁定上期权重
        for i in lim_dn - halt_indices:
            w[i] = w_prev_vec[i]
            locked_budget += w_prev_vec[i]
    else:
        # 第一期无历史权重：停牌/跌停视为不可买入
        if halt_indices:
            log.warning(
                "_topn_equal_weight: 第一期无 w_prev，%d 只停牌股权重置 0",
                len(halt_indices),
            )

    # 可交易预算
    free_budget = max(1.0 - locked_budget, 0.0)

    if free_budget < 1e-10:
        # 极端情况：所有预算被锁定，只做归一化
        w_sum = w.sum()
        w = w / w_sum if w_sum > 1e-10 else (np.ones(n) / n)
        return w, True

    # 可选股票：排除停牌、跌停（已锁定）、涨停（不可买入）
    excluded_free = halt_indices | lim_dn | no_buy

    # Bug-2A 修复：基准权重 > single_max_dev 的非排除股若未被 TopN 选中权重为 0，
    # 偏离 = w_b_i > single_max_dev，必然违约 → 强制锁定为基准权重
    must_hold_set: set[int] = set()
    if w_b_vec is not None:
        for i in range(n):
            if i not in excluded_free and w_b_vec[i] > single_max_dev + 1e-9:
                must_hold_set.add(i)
                w[i] = float(w_b_vec[i])
                free_budget -= w[i]
    free_budget = max(free_budget, 0.0)

    candidates = [i for i in range(n) if i not in excluded_free and i not in must_hold_set]
    ranked = sorted(
        candidates,
        key=lambda i: (np.isnan(alpha_vec[i]), -alpha_vec[i] if not np.isnan(alpha_vec[i]) else 0.0),
    )

    # 动态计算最少选股数（保证 unit_w - w_b_i ≤ single_max_dev）
    # w_b_vec 已知时用候选股的最小基准权重收紧 n_min；否则保守取 w_b=0
    if w_b_vec is not None and candidates:
        min_w_b = float(min(w_b_vec[i] for i in candidates))
    else:
        min_w_b = 0.0
    max_unit_w = single_max_dev + min_w_b
    n_min = math.ceil(free_budget / max_unit_w) if max_unit_w > 1e-9 else topn
    effective_topn = max(topn, n_min)
    selected = ranked[:effective_topn]

    if selected:
        # Bug-2B 修复：上限截断 + 重分配，防止 unit_w > w_b_i + single_max_dev
        remaining = free_budget
        to_assign = list(selected)
        while to_assign:
            unit_w = remaining / len(to_assign)
            next_round: list[int] = []
            for i in to_assign:
                cap = (float(w_b_vec[i]) + single_max_dev) if w_b_vec is not None else unit_w
                if unit_w > cap + 1e-9:
                    w[i] = cap
                    remaining -= cap
                else:
                    next_round.append(i)
            if len(next_round) == len(to_assign):
                for i in next_round:
                    w[i] = unit_w
                break
            to_assign = next_round
        return w, True

    # F6-001: 无可用可买入股票（停牌/涨停/跌停全覆盖）
    # 紧急回退：排除涨停（no_buy）和停牌，仅给非停牌/非涨停股分配预算。
    # 涨停股 w_prev=0 时若分配到正权重即违反"涨停不加仓"约束 → constraint_compliant=False
    emergency_excluded = halt_indices | no_buy
    emergency_stocks = [i for i in range(n) if i not in emergency_excluded]
    if emergency_stocks:
        log.error(
            "_topn_equal_weight: 无可买入候选（停牌/涨停/跌停全覆盖），"
            "自由预算 %.4f 等权分配给 %d 只非停牌/非涨停股（constraint_compliant=False）",
            free_budget, len(emergency_stocks),
        )
        unit_w = free_budget / len(emergency_stocks)
        for i in emergency_stocks:
            w[i] += unit_w  # 已锁定权重（跌停）+= 追加预算，保持权重和=1
    else:
        # 极端：全部停牌或全部涨停
        log.error(
            "_topn_equal_weight: 全部股票停牌或涨停，等权退化（constraint_compliant=False）"
        )
        w[:] = 1.0 / n

    return w, False  # 无合规可买候选，标记不可交付


def _expand_cov_for_missing(
    cov_base: np.ndarray,
    base_codes: list[str],
    target_codes: list[str],
) -> np.ndarray:
    """
    将 base_codes 的协方差矩阵扩展到 target_codes。

    target_codes 中不在 base_codes 里的股票：
      - 对角元素用 base 矩阵对角均值填充（保守）
      - 与其他股票的协方差置 0（假设零相关）

    仅在成分股调仓日新增股票等边界情况触发；正常运行应保证 base_codes ⊇ target_codes。

    Args:
        cov_base:    (M×M) 基础协方差矩阵
        base_codes:  与 cov_base 行/列对应的代码列表
        target_codes: 目标代码列表
    Returns:
        (len(target_codes) × len(target_codes)) 扩展后的协方差矩阵
    """
    base_idx = {c: i for i, c in enumerate(base_codes)}
    avg_var = float(np.diag(cov_base).mean())
    n_t = len(target_codes)
    cov_new = np.zeros((n_t, n_t), dtype=float)

    for i, ci in enumerate(target_codes):
        for j, cj in enumerate(target_codes):
            if ci in base_idx and cj in base_idx:
                cov_new[i, j] = cov_base[base_idx[ci], base_idx[cj]]
            elif i == j:
                cov_new[i, j] = avg_var  # 缺失股票用平均方差

    return cov_new


# ---------------------------------------------------------------------------
# 单期优化主函数
# ---------------------------------------------------------------------------

def optimize_single_period(
    alpha: pd.Series,
    w_b: pd.Series,
    cov: np.ndarray,
    industry_map: pd.Series,
    w_prev: pd.Series | None = None,
    halt_codes: set[str] | None = None,
    limit_up_codes: set[str] | None = None,
    limit_dn_codes: set[str] | None = None,
    config: OptimizeConfig | None = None,
) -> OptimizeResult:
    """
    对单个调仓日求解最优组合权重，内置三层 Fallback（L1 → L2 → L3）。

    调用方职责：
      - alpha、w_b 的 index 须为相同的 ts_code 列表
      - cov 形状须为 (len(alpha), len(alpha))，行/列顺序与 alpha.index 一致
      - w_b 权重和应为 1（本函数做防御性归一化，但不应依赖此行为）
      - cov 为日频协方差矩阵（不年化）；TE 约束内部除以 252

    Args:
        alpha:           合成因子信号（index=ts_code），已 z-score；NaN=无观点
        w_b:             基准权重（index=ts_code），和=1
        cov:             日频协方差矩阵（N×N），与 alpha.index 排列顺序一致
        industry_map:    ts_code → 行业代码（申万一级），多余条目不影响结果
        w_prev:          上期权重（index=ts_code）；None=第一期，不加涨跌停约束
        halt_codes:      当期停牌股代码集合
        limit_up_codes:  当期涨停股代码集合
        limit_dn_codes:  当期跌停股代码集合
        config:          优化参数；None 时使用 OptimizeConfig 默认值

    Returns:
        OptimizeResult（weights.index = alpha.index，权重和=1）
    """
    if config is None:
        config = OptimizeConfig()
    halt_codes      = halt_codes      or set()
    limit_up_codes  = limit_up_codes  or set()
    limit_dn_codes  = limit_dn_codes  or set()

    codes = list(alpha.index)
    n = len(codes)

    if n == 0:
        raise ValueError("optimize_single_period: alpha 为空，无可投资股票")

    code_idx = {c: i for i, c in enumerate(codes)}

    # ------------------------------------------------------------------
    # 对齐所有输入到 codes 顺序
    # ------------------------------------------------------------------
    alpha_vec = alpha.reindex(codes).fillna(0.0).values.astype(float)

    w_b_vec = w_b.reindex(codes).fillna(0.0).values.astype(float)
    w_b_sum = w_b_vec.sum()
    if w_b_sum > 1e-10:
        w_b_vec /= w_b_sum  # 防御性归一化

    w_prev_vec = (
        w_prev.reindex(codes).fillna(0.0).values.astype(float)
        if w_prev is not None else None
    )

    halt_indices  = {code_idx[c] for c in halt_codes   if c in code_idx}
    limit_up_idx  = {code_idx[c] for c in limit_up_codes if c in code_idx}
    limit_dn_idx  = {code_idx[c] for c in limit_dn_codes if c in code_idx}

    # ------------------------------------------------------------------
    # 预筛 Top-N（仅 QP 路径；L3 有自己的 topn 逻辑）
    # prefilter_mode="zero_alpha"    : 非 top-N 股票的 alpha 清零，仍可被约束分配权重
    # prefilter_mode="lock_benchmark": 非 top-N 股票等式锁定到基准权重，完全移出主动管理
    # ------------------------------------------------------------------
    lock_benchmark_indices: set[int] = set()
    if config.prefilter_topn > 0 and config.prefilter_mode != "none":
        alpha_orig = alpha.reindex(codes).values.astype(float)
        candidates = sorted(
            [(i, float(alpha_orig[i])) for i in range(n)
             if i not in halt_indices and not np.isnan(alpha_orig[i])],
            key=lambda x: x[1], reverse=True,
        )
        top_n_set = {i for i, _ in candidates[:config.prefilter_topn]}
        non_top_free = {i for i in range(n)
                        if i not in top_n_set and i not in halt_indices}

        if config.prefilter_mode == "zero_alpha":
            for i in non_top_free:
                alpha_vec[i] = 0.0
        elif config.prefilter_mode == "lock_benchmark":
            lock_benchmark_indices = non_top_free

        log.debug(
            "prefilter_%s: top-%d 激活 / %d 只受限",
            config.prefilter_mode, len(top_n_set), len(non_top_free),
        )

    A_ind = _build_industry_matrix(codes, industry_map)

    te_limit = (config.te_target_annual ** 2) / TRADING_DAYS_PER_YEAR

    # ------------------------------------------------------------------
    # L1：完整 QP（含跟踪误差二次约束）；force_l2=True 时直接跳过
    # ------------------------------------------------------------------
    status = "skipped_force_l2"
    elapsed = 0.0
    if not config.force_l2:
        w_l1 = cp.Variable(n, name="w_l1")
        l1_constraints = _build_portfolio_constraints(
            w_l1, w_b_vec, A_ind, config, halt_indices, limit_up_idx, limit_dn_idx, w_prev_vec
        )
        for i in lock_benchmark_indices:
            l1_constraints.append(w_l1[i] == w_b_vec[i])
        # psd_wrap：告知 CVXPY 矩阵已由 _ensure_positive_definite 保证正定，
        # 跳过 ARPACK 特征值验证（500×500 矩阵上 ARPACK 常迭代不收敛）。
        l1_constraints.append(cp.quad_form(w_l1 - w_b_vec, cp.psd_wrap(cov)) <= te_limit)

        if w_prev_vec is not None and config.turnover_lambda > 0:
            l1_obj = cp.Maximize(
                alpha_vec @ w_l1 - config.turnover_lambda * cp.norm1(w_l1 - w_prev_vec)
            )
        else:
            l1_obj = cp.Maximize(alpha_vec @ w_l1)
        l1_problem = cp.Problem(l1_obj, l1_constraints)
        success, status, elapsed = _try_solve(l1_problem, config)

        if success and w_l1.value is not None:
            w_vals, trigger_l2 = _postprocess_weights(w_l1.value, status)
            if not trigger_l2:
                log.debug("L1 成功: status=%s, %.2fs", status, elapsed)
                return OptimizeResult(
                    weights=pd.Series(w_vals, index=codes, name="weight"),
                    fallback_level=0,
                    solver_status=status,
                    solve_time_s=elapsed,
                )

        log.info("L1 失败或降级 (status=%s, %.2fs)，尝试 L2", status, elapsed)
    else:
        log.debug("force_l2=True：跳过 L1，直接进入 L2")

    # ------------------------------------------------------------------
    # L2：去掉跟踪误差约束，保留全部线性约束
    # ------------------------------------------------------------------
    w_l2 = cp.Variable(n, name="w_l2")
    l2_constraints = _build_portfolio_constraints(
        w_l2, w_b_vec, A_ind, config, halt_indices, limit_up_idx, limit_dn_idx, w_prev_vec
    )
    for i in lock_benchmark_indices:
        l2_constraints.append(w_l2[i] == w_b_vec[i])
    if w_prev_vec is not None and config.turnover_lambda > 0:
        l2_obj = cp.Maximize(
            alpha_vec @ w_l2 - config.turnover_lambda * cp.norm1(w_l2 - w_prev_vec)
        )
    else:
        l2_obj = cp.Maximize(alpha_vec @ w_l2)
    l2_problem = cp.Problem(l2_obj, l2_constraints)
    success_l2, status_l2, elapsed_l2 = _try_solve(l2_problem, config)

    if success_l2 and w_l2.value is not None:
        w_vals, trigger_l3 = _postprocess_weights(w_l2.value, status_l2)
        if not trigger_l3:
            log.info("L2 成功: status=%s, %.2fs", status_l2, elapsed_l2)
            return OptimizeResult(
                weights=pd.Series(w_vals, index=codes, name="weight"),
                fallback_level=1,
                solver_status=status_l2,
                solve_time_s=elapsed_l2,
            )
        log.warning("L2 optimal_inaccurate 且负权重超阈值，降级到 L3")

    log.warning("L2 失败或降级 (status=%s)，降级到 L3 TopN 等权", status_l2)

    # ------------------------------------------------------------------
    # L3：TopN 等权兜底（保留停牌锁定和跌停约束，F6-001）
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    w_vals, l3_compliant = _topn_equal_weight(
        alpha_vec, n, config.topn,
        halt_indices=halt_indices,
        no_buy_indices=limit_up_idx,
        limit_dn_indices=limit_dn_idx,
        w_prev_vec=w_prev_vec,
        single_max_dev=config.single_max_dev,
        w_b_vec=w_b_vec,
    )
    elapsed_l3 = time.perf_counter() - t0

    # F6-001: 验证单股偏离约束（停牌股偏差外生，不计入合规判定）
    if l3_compliant:
        free_idx = np.array([i for i in range(n) if i not in halt_indices])
        max_dev = float(np.max(np.abs(w_vals[free_idx] - w_b_vec[free_idx]))) if len(free_idx) > 0 else 0.0
        if max_dev > config.single_max_dev + 1e-6:
            log.warning(
                "L3: 单股最大偏离（非停牌）%.4f 超出约束 %.4f，constraint_compliant=False",
                max_dev, config.single_max_dev,
            )
            l3_compliant = False

    return OptimizeResult(
        weights=pd.Series(w_vals, index=codes, name="weight"),
        fallback_level=2,
        solver_status="topn_fallback",
        solve_time_s=elapsed_l3,
        constraint_compliant=l3_compliant,
    )


# ---------------------------------------------------------------------------
# 强制 TopN 等权路径（无 QP，但复用 L3 状态约束）
# ---------------------------------------------------------------------------

def optimize_topn_equal_weight_all_periods(
    composite_panel: pd.DataFrame,
    benchmark_weights: dict[pd.Timestamp, pd.Series],
    rebalance_dates: list[pd.Timestamp],
    config: OptimizeConfig | None = None,
    halt_dict: dict[pd.Timestamp, set[str]] | None = None,
    limit_up_dict: dict[pd.Timestamp, set[str]] | None = None,
    limit_dn_dict: dict[pd.Timestamp, set[str]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build a forced TopN equal-weight target panel without running QP.

    This is intended for frozen/no-optimizer baselines. It deliberately reuses
    the L3 helper so suspended positions stay locked, limit-up names are not
    bought, limit-down holdings are not sold, and single-name deviation checks
    are recorded in metadata.

    Time alignment:
      - composite_panel[T] is the signal known at rebalance date T.
      - benchmark_weights[T] defines the CSI500 investable snapshot for T.
      - halt/limit dictionaries must also be keyed by T.
      - The returned target weights are T close decisions for downstream T+1
        open execution in the backtest layer.

    Returns:
        (weights_panel, metadata_df)
        weights_panel: rows are rebalance dates, columns are ts_code.
        metadata_df: rows are rebalance dates with optimizer audit fields.
    """
    if config is None:
        config = OptimizeConfig()

    weights_rows: dict[pd.Timestamp, pd.Series] = {}
    meta_rows: list[dict] = []
    w_prev: pd.Series | None = None

    for T in rebalance_dates:
        if T not in benchmark_weights:
            log.warning(
                "optimize_topn_equal_weight_all_periods: %s 无基准权重数据，跳过",
                T.date(),
            )
            continue
        if T not in composite_panel.index:
            log.warning(
                "optimize_topn_equal_weight_all_periods: %s 无合成信号，跳过",
                T.date(),
            )
            continue

        t0 = time.perf_counter()
        w_b = benchmark_weights[T]
        period_codes = list(w_b.index)
        code_idx = {c: i for i, c in enumerate(period_codes)}

        alpha_vec = (
            composite_panel.loc[T]
            .reindex(period_codes)
            .values
            .astype(float)
        )
        w_b_vec = w_b.reindex(period_codes).fillna(0.0).values.astype(float)
        w_b_sum = float(w_b_vec.sum())
        if w_b_sum > 1e-10:
            w_b_vec = w_b_vec / w_b_sum

        w_prev_vec = (
            w_prev.reindex(period_codes).fillna(0.0).values.astype(float)
            if w_prev is not None
            else None
        )

        halt = (halt_dict or {}).get(T, set())
        lim_up = (limit_up_dict or {}).get(T, set())
        lim_dn = (limit_dn_dict or {}).get(T, set())
        halt_idx = {code_idx[c] for c in halt if c in code_idx}
        lim_up_idx = {code_idx[c] for c in lim_up if c in code_idx}
        lim_dn_idx = {code_idx[c] for c in lim_dn if c in code_idx}

        # Reuse the same L3 helper as the optimizer fallback. This keeps the
        # frozen baseline auditable under the same halt/limit rules.
        w_vals, compliant = _topn_equal_weight(
            alpha_vec=alpha_vec,
            n=len(period_codes),
            topn=config.topn,
            halt_indices=halt_idx,
            no_buy_indices=lim_up_idx,
            limit_dn_indices=lim_dn_idx,
            w_prev_vec=w_prev_vec,
            single_max_dev=config.single_max_dev,
            w_b_vec=w_b_vec,
        )
        elapsed = time.perf_counter() - t0

        weights = pd.Series(w_vals, index=period_codes, name="weight")
        weights_rows[T] = weights
        n_holdings = int((weights > 1e-12).sum())
        meta_rows.append({
            "rebalance_date":       T,
            "fallback_level":       2,
            "solver_status":        "topn_ew_forced",
            "solve_time_s":         elapsed,
            "cov_available":        False,
            "w_prev_source":        "target_weight",
            "constraint_compliant": bool(compliant),
            "optimizer_mode":       "topn_ew",
            "requested_topn":       int(config.topn),
            "n_holdings":           n_holdings,
            "n_halt":               len(halt_idx),
            "n_limit_up":           len(lim_up_idx),
            "n_limit_down":         len(lim_dn_idx),
        })
        w_prev = weights

        log.debug(
            "optimize_topn_equal_weight_all_periods: %s -> topn_ew holdings=%d compliant=%s",
            T.date(),
            n_holdings,
            compliant,
        )

    if not weights_rows:
        log.warning(
            "optimize_topn_equal_weight_all_periods: 所有调仓日均跳过，返回空 DataFrame"
        )
        return pd.DataFrame(), pd.DataFrame()

    weights_panel = pd.DataFrame(weights_rows).T.fillna(0.0)
    weights_panel.index.name = "rebalance_date"
    weights_panel.columns = weights_panel.columns.astype(str)
    metadata_df = pd.DataFrame(meta_rows).set_index("rebalance_date")
    metadata_df.index = pd.DatetimeIndex(metadata_df.index)

    n_bad = int((~metadata_df["constraint_compliant"]).sum())
    log.info(
        "optimize_topn_equal_weight_all_periods: 完成 %d 期 | requested_topn=%d | noncompliant=%d",
        len(weights_panel),
        config.topn,
        n_bad,
    )
    return weights_panel, metadata_df


# ---------------------------------------------------------------------------
# 批量优化（全周期）
# ---------------------------------------------------------------------------

def optimize_all_periods(
    composite_panel: pd.DataFrame,
    benchmark_weights: dict[pd.Timestamp, pd.Series],
    cov_dict: dict[pd.Timestamp, np.ndarray],
    cov_codes: list[str],
    industry_map: pd.Series,
    rebalance_dates: list[pd.Timestamp],
    config: OptimizeConfig | None = None,
    halt_dict: dict[pd.Timestamp, set[str]] | None = None,
    limit_up_dict: dict[pd.Timestamp, set[str]] | None = None,
    limit_dn_dict: dict[pd.Timestamp, set[str]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    对所有调仓日批量求解最优权重。

    可投资域定义：每期 benchmark_weights[T] 的 index（当期 CSI500 成分股）。
    composite_panel 中不在基准的股票自动忽略，基准中无信号的股票 alpha 置 0。

    协方差对齐：
      cov_dict[T] 的行/列与 cov_codes 对应。若当期 period_codes ⊈ cov_codes，
      调用 _expand_cov_for_missing 补全（边界情况，正常运行不应频繁触发）。
      若 T 不在 cov_dict 中，用 1e-4 * I 代替（L1 约束形同虚设，实际走 L2）。

    Args:
        composite_panel:   行=调仓日，列=ts_code 的合成信号面板
        benchmark_weights: {T: Series(ts_code → weight)}，每期 CSI500 权重
        cov_dict:          {T: ndarray(M×M)}，日频协方差矩阵
        cov_codes:         cov_dict 中矩阵行/列对应的代码列表
        industry_map:      ts_code → 行业代码（全量映射，多余条目无影响）
        rebalance_dates:   调仓日列表（有序升序）
        config:            优化参数；None=默认
        halt_dict:         {T: set(ts_code)}，停牌股
        limit_up_dict:     {T: set(ts_code)}，涨停股
        limit_dn_dict:     {T: set(ts_code)}，跌停股

    Returns:
        (weights_panel, metadata_df)
        weights_panel: 行=调仓日，列=ts_code，权重（NaN=未持有）
        metadata_df:   列=[fallback_level, solver_status, solve_time_s]
    """
    if config is None:
        config = OptimizeConfig()

    cov_code_idx = {c: i for i, c in enumerate(cov_codes)}
    weights_rows: dict[pd.Timestamp, pd.Series] = {}
    meta_rows: list[dict] = []
    w_prev: pd.Series | None = None

    for T in rebalance_dates:
        if T not in benchmark_weights:
            log.warning("optimize_all_periods: %s 无基准权重数据，跳过", T.date())
            continue
        if T not in composite_panel.index:
            log.warning("optimize_all_periods: %s 无合成信号，跳过", T.date())
            continue

        w_b = benchmark_weights[T]
        period_codes = list(w_b.index)
        alpha = composite_panel.loc[T].reindex(period_codes)

        # ------------------------------------------------------------------
        # 协方差矩阵：子集化或扩展到 period_codes
        # ------------------------------------------------------------------
        cov_available = T in cov_dict
        if cov_available:
            all_in_base = all(c in cov_code_idx for c in period_codes)
            if all_in_base:
                idx = [cov_code_idx[c] for c in period_codes]
                cov_period = cov_dict[T][np.ix_(idx, idx)]
            else:
                missing_n = sum(1 for c in period_codes if c not in cov_code_idx)
                log.warning(
                    "optimize_all_periods: %s 有 %d 只成分股不在 cov_codes，扩展填充",
                    T.date(), missing_n,
                )
                cov_period = _expand_cov_for_missing(cov_dict[T], cov_codes, period_codes)
        else:
            # F6-002: 协方差缺失时降级为 L2（不伪装成 L1）
            # 用微小单位矩阵仅作占位，optimize_single_period 的 TE 约束近似无效，
            # 需在后处理中强制 fallback_level >= 1，并在 metadata 中标记 cov_available=False
            log.warning(
                "optimize_all_periods: %s 无协方差矩阵，降级为 L2（F6-002）",
                T.date(),
            )
            cov_period = np.eye(len(period_codes)) * 1e-4

        w_prev_aligned = w_prev.reindex(period_codes) if w_prev is not None else None

        halt   = (halt_dict   or {}).get(T, set())
        lim_up = (limit_up_dict or {}).get(T, set())
        lim_dn = (limit_dn_dict or {}).get(T, set())

        result = optimize_single_period(
            alpha=alpha,
            w_b=w_b,
            cov=cov_period,
            industry_map=industry_map,
            w_prev=w_prev_aligned,
            halt_codes=halt,
            limit_up_codes=lim_up,
            limit_dn_codes=lim_dn,
            config=config,
        )

        # F6-002: 协方差缺失时强制 fallback_level >= 1，避免伪装成"正常 L1"
        effective_fallback = result.fallback_level
        effective_status   = result.solver_status
        if not cov_available and result.fallback_level == 0:
            effective_fallback = 1
            effective_status   = result.solver_status + "+cov_missing"
            log.info(
                "optimize_all_periods: %s 协方差缺失，fallback_level 从 0 覆盖为 1，"
                "status=%s",
                T.date(), effective_status,
            )

        weights_rows[T] = result.weights
        meta_rows.append({
            "rebalance_date":      T,
            "fallback_level":      effective_fallback,
            "solver_status":       effective_status,
            "solve_time_s":        result.solve_time_s,
            "cov_available":       cov_available,           # F6-002
            "w_prev_source":       "target_weight",         # F6-004: 目标权重近似，非实际持仓
            "constraint_compliant": result.constraint_compliant,  # F6-001
        })
        w_prev = result.weights

        log.debug(
            "optimize_all_periods: %s → L%d (%s) %.2fs",
            T.date(), result.fallback_level, result.solver_status, result.solve_time_s,
        )

    if not weights_rows:
        log.warning("optimize_all_periods: 所有调仓日均跳过，返回空 DataFrame")
        return pd.DataFrame(), pd.DataFrame()

    weights_panel = pd.DataFrame(weights_rows).T
    weights_panel.index.name = "rebalance_date"
    metadata_df = pd.DataFrame(meta_rows).set_index("rebalance_date")

    counts = metadata_df["fallback_level"].value_counts().sort_index()
    log.info(
        "optimize_all_periods: 完成 %d 期 | L1=%d  L2=%d  L3=%d",
        len(weights_panel),
        counts.get(0, 0), counts.get(1, 0), counts.get(2, 0),
    )
    return weights_panel, metadata_df
