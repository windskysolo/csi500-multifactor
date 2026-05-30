"""
scripts/build_factor_panels.py — 构建训练+验证期全因子面板
==========================================================

输出：data/processed/factor_panels/{factor_name}.parquet
      每个文件是 rebalance_date × ts_code 的 DataFrame（已预处理）

运行范围：TRAIN_START 到 VALID_END（由 src/config.py 控制），不含测试集。
测试集（2023-2025）严格不处理，保留其样本外性。

使用方法：
    python -m scripts.build_factor_panels [--resume] [--factors ep_ttm roe ...]

参数：
    --resume         跳过已完整写入的因子文件（增量更新）
    --factors        只处理指定因子子集（默认处理全部 27 个因子）
    --dry-run        只打印调仓日列表，不执行因子计算
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import config as cfg
from src.data.loader import (
    get_rebalance_dates,
    load_daily_basic,
    load_industry,
)
from src.data.universe import get_investable_universe
from src.factors.alt_factors import build_all_alt_factors
from src.factors.financial_factors import build_all_financial_factors
from src.factors.preprocess import preprocess_factor
from src.factors.price_factors import build_all_price_factors
from scripts.test_set_ledger import MAX_TEST_SET_RUNS, count_test_set_runs

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
OUTPUT_DIR          = cfg.DATA_PROC / "factor_panels"
DIAGNOSTICS_PATH    = cfg.DATA_PROC / "factor_panel_diagnostics.parquet"
TEST_SET_RUN_LOG    = cfg.ROOT / "docs" / "check" / "test_set_run_log.md"

# 全部 36 个因子：21 财务（含阶段 4/5 新增 6 个）+ 15 量价（含阶段 5 新增 3 个）
FINANCIAL_FACTORS: list[str] = [
    # 价值
    "ep_ttm", "bp", "sp_ttm", "cfp", "fcfp",
    # 质量
    "roe", "roa", "gross_margin", "asset_turn", "leverage", "accrual",
    # 成长
    "np_yoy", "rev_yoy", "roe_delta", "q_roe",
    # 阶段 4：低风险因子扩展
    "roe_smoothed_4q", "roe_delta_3q", "gross_margin_trend", "ep_vs_history",
    # 阶段 5：质量综合因子
    "piotroski_f", "garp",
    # 阶段 6：新增财务质量/成长因子
    "roe_stability", "rev_acceleration",
    # 阶段 7：资本效率（新增）
    "asset_growth",
]

PRICE_FACTORS: list[str] = [
    # 动量/反转
    "ret_1m", "mom_6_1", "mom_12_1", "holder_chg",
    # 波动率
    "vol_60d", "ivol_60d", "max_ret",
    # 流动性
    "turn_20d", "amihud",
    # 资金流向
    "margin_ratio", "short_ratio", "large_net_inflow",
    # 阶段 5：动量类因子（原有）
    "high_52w", "ind_adj_mom", "mom_risk_adj",
    # Sprint 1：修正版动量因子
    "high_52w_v2", "ind_adj_mom_6_1", "mom_consistency_6",
    # 阶段 7：股本行为 + 融资资金（新增）
    "share_issuance",
    "mf_flow_ratio",
]

ALL_FACTORS: list[str] = FINANCIAL_FACTORS + PRICE_FACTORS

# 备选数据因子（来自 docs/teach/data_expansion_plan/phase_plan.md 阶段5；实现在 Phase 5）
# 名称已在此定义，--factor-set alternative --dry-run 可列出计划因子；
# 实际计算实现在 src/factors/alt_factors.py（阶段5 建立）。
ALT_FACTORS: list[str] = [
    # 技术因子（自行从后复权行情计算，不引入 pandas_ta）
    "macd_cross", "rsi_6", "rsi_12", "boll_pct", "obv_chg_20d",
    # 外部数据因子
    "hk_hold_ratio", "hk_hold_chg",
    "analyst_eps_revision", "analyst_rating_chg",
    "eps_dispersion", "analyst_cnt_chg",
    "float_pct_30d", "insider_net_buy",
    "pledge_ratio",
    # 已剔除（训练期覆盖不足，见 execution_log.md 第 4.5 步）：
    #   chip_winner_rate / cost_deviation：cyq_perf 2018 前无数据，训练期仅 36 期
    #   north_flow_5d：广播因子，截面标准差=0，预处理后全 NaN
]


# ---------------------------------------------------------------------------
# 辅助：测试集运行次数校验
# ---------------------------------------------------------------------------

def _count_test_set_ledger_runs() -> int:
    """
    统计已登记的测试集运行次数。

    权威来源为 docs/check/test_set_runs.json；历史 git 中的错误
    [TEST_SET_RUN_N] 标签不作为运行次数来源。
    """
    return count_test_set_runs()


# ---------------------------------------------------------------------------
# 辅助：提取单日横截面行业 / log_mv
# ---------------------------------------------------------------------------

def _extract_cross_section(
    multiindex_df: pd.DataFrame,
    date: pd.Timestamp,
    col: str,
    codes: pd.Index,
) -> pd.Series:
    """
    从 (trade_date, ts_code) MultiIndex 的 DataFrame 中提取单日横截面列。

    若该日期不存在（节假日等极端情形），返回全 NaN Series。
    结果以 codes 为 index（reindex 保证对齐），未覆盖的股票填 NaN。

    Args:
        multiindex_df:  (trade_date, ts_code) MultiIndex 的 DataFrame
        date:           目标日期
        col:            列名
        codes:          目标股票代码 Index
    Returns:
        ts_code → 该列值的 pd.Series
    """
    try:
        cross = multiindex_df.xs(date, level="trade_date")[col]
    except KeyError:
        log.warning("_extract_cross_section: %s 无 %s 数据，返回全 NaN", date.date(), col)
        return pd.Series(np.nan, index=codes, name=col)

    return cross.reindex(codes)


# ---------------------------------------------------------------------------
# 辅助：增量检查
# ---------------------------------------------------------------------------

def _is_complete(path: Path, expected_dates: list[pd.Timestamp]) -> bool:
    """
    判断已有 parquet 文件是否包含所有期望日期（增量跳过判断）。

    仅检查行 index，不读取数据内容。

    Args:
        path:           parquet 文件路径
        expected_dates: 期望出现在 index 中的所有调仓日
    Returns:
        True 表示文件已完整，可跳过
    """
    if not path.exists():
        return False
    try:
        existing = pd.read_parquet(path, columns=[]).index
        return set(expected_dates).issubset(set(existing))
    except Exception as exc:
        log.warning("读取 %s 失败（%s），将重新计算", path.name, exc)
        return False


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def build_panels(
    target_factors: list[str],
    resume: bool = False,
    end_date: pd.Timestamp | None = None,
    output_dir: Path | None = None,
) -> None:
    """
    构建指定因子的全周期面板并写出 Parquet。

    时间范围：TRAIN_START ~ end_date（默认 VALID_END）。
    当 end_date > VALID_END 时进入"测试集扩展模式"，须经 main() 的防护门才能到达此处。
    测试集扩展模式的产物写入独立目录（output_dir），不覆盖训练/验证期面板。

    同步输出 factor_panel_diagnostics.parquet，记录每期每个因子的预处理统计：
      n_raw、n_fin_filtered、n_winsor_clipped、n_neutralize_valid、skipped_neutralize、
      n_final、error_msg。

    每个因子独立写出一个 parquet：
        {output_dir}/{factor_name}.parquet
    行 index = rebalance_date，列 = ts_code。

    Args:
        target_factors:  需要处理的因子名列表
        resume:          True 则跳过已完整写入的因子 / 仅补全缺失日期
        end_date:        覆盖终止日期（默认 cfg.VALID_END = 2022-12-31）
        output_dir:      因子面板输出目录（默认 OUTPUT_DIR；测试集运行时传入独立目录）
    """
    out_dir = output_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    end = end_date if end_date is not None else cfg.VALID_END
    full_dates = get_rebalance_dates(start=cfg.TRAIN_START, end=end)

    if not full_dates:
        log.error("未找到任何调仓日（%s ~ %s），请检查 index_member.parquet",
                  cfg.TRAIN_START.date(), end.date())
        return

    log.info(
        "目标调仓日范围：%s ~ %s，共 %d 期",
        full_dates[0].date(), full_dates[-1].date(), len(full_dates),
    )

    # ── 确定每个因子需要计算哪些日期 ──────────────────────────────────────────
    # resume 模式：加载已有面板，只计算缺失日期；已完整的因子直接跳过。
    # 典型场景：现有面板到 2022-12，end_date=2025-12，只补算 2023-01 ~ 2025-12。
    existing_panels: dict[str, pd.DataFrame] = {}
    pending_factors: list[str] = []

    for fname in target_factors:
        path = out_dir / f"{fname}.parquet"
        if resume and path.exists():
            if _is_complete(path, full_dates):
                log.info("resume: %s 已完整（%d 期），跳过", fname, len(full_dates))
                continue
            try:
                existing_panels[fname] = pd.read_parquet(path)
                existing_count = len(existing_panels[fname])
                missing_count  = len([d for d in full_dates if d not in set(existing_panels[fname].index)])
                log.info("resume: %s 已有 %d 期，缺 %d 期，将增量补全",
                         fname, existing_count, missing_count)
            except Exception as exc:
                log.warning("读取 %s 失败（%s），全量重算", fname, exc)
        pending_factors.append(fname)

    if not pending_factors:
        log.info("所有目标因子已完整，无需重新计算")
        return

    # ── 只计算有缺失因子涉及的日期 ────────────────────────────────────────────
    # 取所有待补全因子中"最早缺失日期"之后的全部日期，保证无遗漏。
    if existing_panels:
        latest_in_existing = {
            fname: existing_panels[fname].index.max()
            for fname in pending_factors
            if fname in existing_panels
        }
        if latest_in_existing:
            min_latest = min(latest_in_existing.values())
            dates_to_compute = [d for d in full_dates if d > min_latest]
            log.info(
                "增量模式：从 %s 之后的 %d 个新日期开始计算",
                min_latest.date(), len(dates_to_compute),
            )
        else:
            dates_to_compute = full_dates
    else:
        dates_to_compute = full_dates

    if not dates_to_compute:
        log.info("无新日期需要计算，结束")
        return

    fin_targets   = [f for f in pending_factors if f in FINANCIAL_FACTORS]
    price_targets = [f for f in pending_factors if f in PRICE_FACTORS]
    alt_targets   = [f for f in pending_factors if f in ALT_FACTORS]
    log.info("待计算财务因子 %d 个：%s", len(fin_targets),   fin_targets)
    log.info("待计算量价因子 %d 个：%s", len(price_targets), price_targets)
    log.info("待计算备选因子 %d 个：%s", len(alt_targets),   alt_targets)

    # 按因子名积累截面：{factor_name: {T: cross_section_series}}
    panels: dict[str, dict[pd.Timestamp, pd.Series]] = {f: {} for f in pending_factors}

    # 预处理诊断：每期每因子记录一行
    diag_rows: list[dict] = []

    # ---------------------------------------------------------------------------
    # 批量预加载行业 / log_mv（仅 dates_to_compute 覆盖的范围，节省内存）
    # ---------------------------------------------------------------------------
    period_start = dates_to_compute[0]
    period_end   = dates_to_compute[-1]

    log.info("预加载行业数据（%s ~ %s）...", period_start.date(), period_end.date())
    industry_df = load_industry(period_start, period_end, codes=None)
    log.info("行业数据加载完成，shape=%s", industry_df.shape)

    log.info("预加载 daily_basic（%s ~ %s）...", period_start.date(), period_end.date())
    basic_df = load_daily_basic(period_start, period_end, codes=None)
    log.info("daily_basic 加载完成，shape=%s", basic_df.shape)

    # ---------------------------------------------------------------------------
    # 逐调仓日循环（仅新日期）
    # ---------------------------------------------------------------------------
    for T in tqdm(dates_to_compute, desc="构建因子面板", unit="月"):
        # 1. 可投资域
        try:
            codes = get_investable_universe(T)
        except KeyError:
            log.warning("%s 不在 index_member 中，跳过", T.date())
            continue

        if codes.empty:
            log.warning("%s 可投资域为空，跳过", T.date())
            continue

        codes_list = codes.tolist()

        # 2. 提取当期行业 / log_mv
        industry_day = _extract_cross_section(industry_df, T, "industry_code", codes)
        log_mv_day   = _extract_cross_section(basic_df,    T, "log_free_float_mv", codes)

        # 行业全 NaN → 中性化失效，仍可进行去极值+标准化；记录一个警告
        if industry_day.isna().all():
            log.warning("%s 行业数据全缺，中性化将跳过", T.date())

        # 3. 构建原始因子
        raw_fin: dict[str, pd.Series] = {}
        if fin_targets:
            raw_fin = build_all_financial_factors(T, codes_list,
                                                   factor_names=fin_targets)

        raw_price: dict[str, pd.Series] = {}
        if price_targets:
            raw_price = build_all_price_factors(T, codes_list,
                                                 factor_names=price_targets)

        raw_alt: dict[str, pd.Series] = {}
        if alt_targets:
            raw_alt = build_all_alt_factors(T, codes_list,
                                             factor_names=alt_targets)

        # 4. 预处理每个因子并写入 panels
        for fname, raw_series in {**raw_fin, **raw_price, **raw_alt}.items():
            is_financial = fname in FINANCIAL_FACTORS
            fin_sector_codes = cfg.FINANCIAL_SECTOR_CODES if is_financial else None

            raw_aligned = raw_series.reindex(codes)
            diag: dict = {
                "rebalance_date":    T,
                "factor":            fname,
                "n_raw":             int(raw_aligned.notna().sum()),
                "n_fin_filtered":    0,
                "n_winsor_clipped":  0,
                "n_neutralize_valid": 0,
                "skipped_neutralize": False,
                "n_final":           0,
                "error_msg":         None,
            }

            # 若原始值全 NaN（冷启动或数据缺失），直接存 NaN 截面，跳过 preprocess
            if raw_aligned.isna().all():
                log.debug("%s %s 全 NaN，跳过 preprocess", T.date(), fname)
                panels[fname][T] = pd.Series(
                    np.nan, index=codes, name=fname, dtype=float
                )
                diag_rows.append(diag)
                continue

            try:
                processed = preprocess_factor(
                    raw=raw_aligned,
                    industry=industry_day,
                    log_mv=log_mv_day,
                    fin_sector_codes=fin_sector_codes,
                    _diag=diag,
                )
                processed.name = fname
                panels[fname][T] = processed
                diag["n_final"] = int(processed.notna().sum())
            except ValueError as exc:
                # preprocess_factor 在全 NaN 时抛 ValueError（已由全 NaN 检查拦截，
                # 此处捕获预料外的边界情形，不让整个脚本崩溃）
                log.warning("%s %s preprocess 失败（%s），存全 NaN", T.date(), fname, exc)
                panels[fname][T] = pd.Series(
                    np.nan, index=codes, name=fname, dtype=float
                )
                diag["error_msg"] = str(exc)
            except Exception as exc:
                log.error("%s %s preprocess 意外错误（%s），存全 NaN", T.date(), fname, exc)
                panels[fname][T] = pd.Series(
                    np.nan, index=codes, name=fname, dtype=float
                )
                diag["error_msg"] = str(exc)

            diag_rows.append(diag)

    # ---------------------------------------------------------------------------
    # 5. 落盘：与已有面板合并后写出 Parquet
    # ---------------------------------------------------------------------------
    log.info("因子计算完成，开始写出 Parquet ...")
    for fname in pending_factors:
        rows = panels[fname]
        if not rows:
            log.warning("%s 无有效截面数据，跳过写出", fname)
            continue

        new_df = pd.DataFrame(rows).T
        new_df.index.name = "rebalance_date"

        # 与已有面板合并（增量扩展场景：新日期追加到旧面板）
        if fname in existing_panels:
            merged = pd.concat([existing_panels[fname], new_df])
            # 去重（保留新计算值），再排序
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        else:
            merged = new_df.sort_index()

        out_path = out_dir / f"{fname}.parquet"
        merged.to_parquet(out_path)
        n_valid = merged.notna().sum().sum()
        n_total = merged.size
        log.info(
            "已写出 %s：%d 期 × %d 股，有效值 %.1f%%，路径 %s",
            fname, len(merged), merged.shape[1],
            100 * n_valid / n_total if n_total > 0 else 0.0,
            out_path,
        )

    # ---------------------------------------------------------------------------
    # 6. 落盘诊断文件：factor_panel_diagnostics.parquet
    # ---------------------------------------------------------------------------
    if diag_rows:
        new_diag = pd.DataFrame(diag_rows).set_index(["rebalance_date", "factor"])
        if DIAGNOSTICS_PATH.exists():
            try:
                old_diag = pd.read_parquet(DIAGNOSTICS_PATH)
                merged_diag = pd.concat([old_diag, new_diag])
                merged_diag = merged_diag[
                    ~merged_diag.index.duplicated(keep="last")
                ].sort_index()
            except Exception as exc:
                log.warning("读取旧诊断文件失败（%s），仅写出新条目", exc)
                merged_diag = new_diag.sort_index()
        else:
            merged_diag = new_diag.sort_index()
        merged_diag.to_parquet(DIAGNOSTICS_PATH)
        log.info("预处理诊断文件已写出: %s（%d 条）", DIAGNOSTICS_PATH, len(merged_diag))

    log.info("build_panels 完成，共 %d 个因子", len(pending_factors))


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="构建因子面板（默认 TRAIN_START~VALID_END；加 --end-date 可扩展到测试集）"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="增量模式：跳过已完整因子，仅补算缺失日期并合并",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="覆盖终止日期（默认 VALID_END=2022-12-31）",
    )
    parser.add_argument(
        "--allow-test-set",
        action="store_true",
        help=(
            "允许 end-date 超过 VALID_END 进入测试期（必须同时传 --run-id）。"
            "测试集产物写入独立目录，受运行次数上限保护（≤ 2 次）。"
        ),
    )
    parser.add_argument(
        "--run-id",
        type=int,
        default=None,
        metavar="N",
        help="测试集运行编号（仅与 --allow-test-set 配合使用；必须与 git 历史中的下一次编号一致）",
    )
    parser.add_argument(
        "--factors",
        nargs="+",
        default=None,
        metavar="FACTOR",
        help=f"只处理指定因子（与 --factor-set 互斥；默认全部 {len(ALL_FACTORS)} 个财务+量价因子）",
    )
    parser.add_argument(
        "--factor-set",
        choices=["original", "alternative", "all"],
        default=None,
        metavar="SET",
        help=(
            "使用预定义因子集合（与 --factors 互斥）："
            " original=原有27个因子，"
            " alternative=备选数据因子（阶段5实现后生效），"
            " all=两者合并"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印调仓日和因子列表，不执行计算",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # 解析 end_date
    end_date: pd.Timestamp | None = None
    if args.end_date:
        try:
            end_date = pd.Timestamp(args.end_date)
        except Exception:
            log.error("--end-date 格式错误：%s（应为 YYYY-MM-DD）", args.end_date)
            sys.exit(1)

    effective_end = end_date if end_date is not None else cfg.VALID_END

    # ── 测试集纪律防护门 ──────────────────────────────────────────────────────
    output_dir: Path | None = None
    if end_date is not None and end_date > cfg.VALID_END:
        if not args.allow_test_set:
            log.error(
                "--end-date %s 超过验证期边界 VALID_END=%s。"
                "扩展到测试期必须显式传入 --allow-test-set --run-id N。"
                "详见 docs/check/test_set_run_log.md 中的运行纪律。",
                end_date.date(), cfg.VALID_END.date(),
            )
            sys.exit(1)

        if args.run_id is None:
            log.error("测试集模式必须传入 --run-id N（整数运行编号）。")
            sys.exit(1)

        prior_runs = _count_test_set_ledger_runs()
        if prior_runs >= MAX_TEST_SET_RUNS:
            log.error(
                "测试集运行次数已达上限（ledger 中已有 %d 次有效运行，"
                "上限为 %d 次）。不允许继续运行。",
                prior_runs, MAX_TEST_SET_RUNS,
            )
            sys.exit(1)

        expected_run_id = prior_runs + 1
        if args.run_id != expected_run_id:
            log.error(
                "--run-id %d 与预期不符：ledger 中已有 %d 次运行，"
                "下一次应为 run-id=%d。",
                args.run_id, prior_runs, expected_run_id,
            )
            sys.exit(1)

        # 测试集产物写入独立目录，不覆盖训练/验证期面板
        output_dir = cfg.DATA_PROC / f"factor_panels_test_run_{args.run_id}"
        log.warning(
            "=== 测试集扩展模式 run-id=%d（已用 %d/%d 次） ==="
            "  产物目录: %s",
            args.run_id, prior_runs, MAX_TEST_SET_RUNS, output_dir,
        )

    # 因子列表解析（--factor-set 与 --factors 互斥）
    if args.factor_set and args.factors:
        log.error("--factor-set 与 --factors 互斥，不能同时使用")
        sys.exit(1)

    if args.factor_set:
        if args.factor_set == "original":
            target = list(ALL_FACTORS)
        elif args.factor_set == "alternative":
            target = list(ALT_FACTORS)
            if not target:
                log.warning(
                    "--factor-set alternative 当前因子列表为空（阶段5实现后生效）。"
                    "如需运行原有因子请用 --factor-set original。"
                )
        else:  # "all"
            target = list(ALL_FACTORS) + [f for f in ALT_FACTORS if f not in set(ALL_FACTORS)]
    else:
        target = args.factors if args.factors else list(ALL_FACTORS)

    all_known = set(ALL_FACTORS) | set(ALT_FACTORS)
    unknown = set(target) - all_known
    if unknown:
        log.error("未知因子名：%s", sorted(unknown))
        log.error("合法原有因子：%s", ALL_FACTORS)
        if ALT_FACTORS:
            log.error("合法备选因子：%s", ALT_FACTORS)
        sys.exit(1)

    if args.dry_run:
        dates = get_rebalance_dates(start=cfg.TRAIN_START, end=effective_end)
        print(f"调仓日数量：{len(dates)}")
        print(f"起止：{dates[0].date()} ~ {dates[-1].date()}")
        print(f"目标因子（{len(target)}）：{target}")
        if output_dir:
            print(f"输出目录（测试集）：{output_dir}")
        return

    build_panels(
        target_factors=target,
        resume=args.resume,
        end_date=end_date,
        output_dir=output_dir,
    )


if __name__ == "__main__":
    main()
