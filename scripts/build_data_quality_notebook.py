"""Build the processed data quality Jupyter notebook.

The notebook is generated with nbformat instead of hand-written JSON so that
cell boundaries and metadata stay valid.
"""

from pathlib import Path
from textwrap import dedent

import nbformat as nbf


OUT = Path("notebooks") / "01_processed_data_quality_report.ipynb"


def md(text: str):
    return nbf.v4.new_markdown_cell(dedent(text).strip())


def code(text: str):
    return nbf.v4.new_code_cell(dedent(text).strip())


def build_notebook():
    nb = nbf.v4.new_notebook()
    nb.metadata = {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {"name": "python", "pygments_lexer": "ipython3"},
    }

    nb.cells = [
        md(
            """
            # processed 数据质量检验与基础描述

            本 notebook 用于审计 `data/processed` 下已经聚合完成的 Parquet 数据。重点检查：

            - 文件是否齐全、schema 是否符合项目计划；
            - 主键是否重复、日期范围是否合理、股票代码格式是否统一；
            - 缺失值、无穷值、异常负值、极端值等基础质量问题；
            - 日行情、自由流通市值、指数净值、PIT 时间关系、成分股、状态、行业、两融、资金流等领域专项约束；
            - 各表与 `daily_quote` 的覆盖关系。

            注意：本 notebook 只做数据质量和基础统计描述，不计算因子收益、不做策略回测，不触发测试集绩效评估纪律。
            """
        ),
        code(
            r'''
            # ============================================================
            # 0. 环境初始化：定位项目根目录、导入依赖、设置显示参数
            # ============================================================
            from pathlib import Path
            import sys
            import re
            import gc
            import warnings

            import numpy as np
            import pandas as pd
            import pyarrow.parquet as pq
            import matplotlib.pyplot as plt
            from IPython.display import display, Markdown

            warnings.filterwarnings("ignore", category=FutureWarning)
            pd.set_option("display.max_columns", 120)
            pd.set_option("display.max_rows", 120)
            pd.set_option("display.width", 180)
            pd.set_option("display.float_format", lambda x: f"{x:,.6g}")


            def find_project_root() -> Path:
                """向上查找项目根目录，避免从 notebooks/ 或项目根目录启动时路径不一致。"""
                current = Path.cwd().resolve()
                for candidate in [current, *current.parents]:
                    if (candidate / "src" / "config.py").exists() and (candidate / "data" / "processed").exists():
                        return candidate
                raise FileNotFoundError("未找到项目根目录：需要同时存在 src/config.py 和 data/processed。")


            PROJECT_ROOT = find_project_root()
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))

            from src import config as cfg

            DATA_PROC = cfg.DATA_PROC
            CODE_PATTERN = re.compile(r"^\d{6}\.(SZ|SH|BJ)$")

            # 大表的详细数值描述默认抽样，避免一次 describe 过慢；质量检查本身仍按整表执行。
            FULL_NUMERIC_PROFILE = False
            PROFILE_SAMPLE_ROWS = 200_000
            RANDOM_SEED = cfg.RANDOM_SEED

            # 是否额外输出 CSV 报告。默认 False，避免在审计 notebook 中自动产生额外文件。
            SAVE_REPORTS = False
            REPORT_DIR = PROJECT_ROOT / "data" / "quality_reports"

            print(f"PROJECT_ROOT = {PROJECT_ROOT}")
            print(f"DATA_PROC    = {DATA_PROC}")
            print(f"样本区间     = {cfg.MARKET_START.date()} ~ {cfg.MARKET_END.date()}")
            '''
        ),
        md(
            """
            ## 1. 数据字典与预期 schema

            这一部分集中声明每张 processed 表的用途、主键、日期列和必须存在的字段。后续所有检查都从这里读取口径，避免 notebook 内散落硬编码。
            """
        ),
        code(
            r'''
            # ============================================================
            # 1. 数据字典：每张表的主键、日期列、必需字段与基础约束
            # ============================================================
            EXPECTED_TABLES = {
                "daily_quote.parquet": {
                    "desc": "日行情：后复权价、后复权收益率、成交量额",
                    "key_cols": ["trade_date", "ts_code"],
                    "date_cols": ["trade_date"],
                    "required_cols": [
                        "trade_date", "ts_code", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount",
                        "adj_factor", "close_adj", "open_adj", "ret",
                    ],
                },
                "daily_basic.parquet": {
                    "desc": "每日基础指标：估值、换手、自由流通市值",
                    "key_cols": ["trade_date", "ts_code"],
                    "date_cols": ["trade_date"],
                    "required_cols": [
                        "trade_date", "ts_code", "close", "turnover_rate", "turnover_rate_f", "volume_ratio", "pe", "pe_ttm", "pb",
                        "ps_ttm", "dv_ttm", "free_share", "total_mv", "circ_mv", "free_float_mv", "log_free_float_mv",
                    ],
                },
                "index_quote.parquet": {
                    "desc": "中证500全收益指数：指数收益与净值序列",
                    "key_cols": ["trade_date"],
                    "date_cols": ["trade_date"],
                    "required_cols": ["trade_date", "close", "pct_chg", "index_ret", "nav"],
                },
                "industry.parquet": {
                    "desc": "每日行业归属：SW2021 一级行业",
                    "key_cols": ["trade_date", "ts_code"],
                    "date_cols": ["trade_date"],
                    "required_cols": ["trade_date", "ts_code", "industry_code", "industry_name"],
                },
                "financial_pit.parquet": {
                    "desc": "财务三表 PIT 宽表",
                    "key_cols": ["pit_date", "ts_code", "end_date"],
                    "date_cols": ["pit_date", "end_date"],
                    "required_cols": [
                        "pit_date", "ts_code", "end_date", "revenue", "total_profit", "n_income", "basic_eps", "rd_exp",
                        "total_assets", "total_liab", "total_hldr_eqy_exc_min_int", "money_cap", "n_cashflow_act", "free_cashflow",
                    ],
                },
                "indicator_pit.parquet": {
                    "desc": "财务指标 PIT 宽表",
                    "key_cols": ["pit_date", "ts_code", "end_date"],
                    "date_cols": ["pit_date", "end_date"],
                    "required_cols": [
                        "pit_date", "ts_code", "end_date", "roe", "roa", "grossprofit_margin", "assets_turn", "netprofit_yoy",
                        "or_yoy", "bps", "debt_to_assets", "q_roe", "ocf_to_debt",
                    ],
                },
                "holder_pit.parquet": {
                    "desc": "股东人数 PIT 表",
                    "key_cols": ["pit_date", "ts_code", "end_date"],
                    "date_cols": ["pit_date", "end_date", "available_date"],
                    "required_cols": ["pit_date", "ts_code", "end_date", "available_date", "holder_num"],
                },
                "dividend_pit.parquet": {
                    "desc": "分红事件 PIT 表",
                    "key_cols": ["event_id"],
                    "date_cols": ["pit_date", "end_date", "ex_date"],
                    "required_cols": [
                        "pit_date", "ts_code", "event_id", "end_date", "cash_div_tax", "stk_div", "stk_bo_rate", "ex_date", "div_proc",
                    ],
                },
                "margin.parquet": {
                    "desc": "融资融券明细",
                    "key_cols": ["trade_date", "ts_code"],
                    "date_cols": ["trade_date"],
                    "required_cols": [
                        "trade_date", "ts_code", "rzye", "rqye", "rzmre", "rzche", "rqyl", "rqchl", "rqmcl", "rzrqye", "rz_net", "rq_net_vol",
                    ],
                },
                "moneyflow.parquet": {
                    "desc": "资金流向",
                    "key_cols": ["trade_date", "ts_code"],
                    "date_cols": ["trade_date"],
                    "required_cols": [
                        "trade_date", "ts_code", "buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount",
                        "buy_md_amount", "buy_sm_amount", "net_mf_amount", "net_mf_vol",
                    ],
                },
                "stock_status.parquet": {
                    "desc": "每日股票状态：停牌、涨跌停锁定、ST、新股、可交易标记",
                    "key_cols": ["trade_date", "ts_code"],
                    "date_cols": ["trade_date"],
                    "required_cols": [
                        "trade_date", "ts_code", "is_suspended", "limit_status", "is_limit_locked", "is_limit_up_locked",
                        "is_limit_down_locked", "is_st", "is_new_stock", "tradable",
                    ],
                },
                "index_member.parquet": {
                    "desc": "中证500月末成分股快照",
                    "key_cols": ["rebalance_date", "ts_code"],
                    "date_cols": ["rebalance_date"],
                    "required_cols": [
                        "rebalance_date", "ts_code", "index_weight", "tradable", "limit_status", "is_suspended", "is_limit_locked",
                        "is_limit_up_locked", "is_limit_down_locked", "is_st", "is_new_stock",
                    ],
                },
            }

            expected_df = pd.DataFrame([
                {
                    "table": name,
                    "description": spec["desc"],
                    "key_cols": ", ".join(spec["key_cols"]),
                    "date_cols": ", ".join(spec["date_cols"]),
                    "n_required_cols": len(spec["required_cols"]),
                }
                for name, spec in EXPECTED_TABLES.items()
            ])
            expected_df
            '''
        ),
        md(
            """
            ## 2. 文件与 Parquet 元数据总览

            这一部分只读取 Parquet metadata，不读取全表数据。它用于快速确认文件是否存在、行列规模、row group 数量和物理 schema。
            """
        ),
        code(
            r'''
            # ============================================================
            # 2. 文件元数据：存在性、行数、列数、row group、文件大小、schema
            # ============================================================
            def parquet_metadata(name: str) -> dict:
                """读取单个 Parquet 文件的元数据；不扫描全表。"""
                path = DATA_PROC / name
                if not path.exists():
                    return {
                        "table": name,
                        "exists": False,
                        "rows": np.nan,
                        "cols": np.nan,
                        "row_groups": np.nan,
                        "size_mb": np.nan,
                        "schema": None,
                    }
                pf = pq.ParquetFile(path)
                schema = {field.name: str(field.type) for field in pf.schema_arrow}
                return {
                    "table": name,
                    "exists": True,
                    "rows": pf.metadata.num_rows,
                    "cols": len(schema),
                    "row_groups": pf.metadata.num_row_groups,
                    "size_mb": path.stat().st_size / 1024 / 1024,
                    "schema": schema,
                }


            metadata = [parquet_metadata(name) for name in EXPECTED_TABLES]
            metadata_df = pd.DataFrame(metadata).drop(columns=["schema"])
            display(metadata_df.sort_values("table"))

            # schema 明细：列名与 pyarrow 类型。
            schema_rows = []
            for item in metadata:
                if not item["exists"]:
                    continue
                for col, dtype in item["schema"].items():
                    schema_rows.append({"table": item["table"], "column": col, "arrow_dtype": dtype})

            schema_df = pd.DataFrame(schema_rows)
            schema_df.sort_values(["table", "column"]).reset_index(drop=True)
            '''
        ),
        md(
            """
            ## 3. 通用质量检查

            本节按整表扫描每个 Parquet 文件，检查主键重复、必需字段缺失、日期范围、股票代码格式、缺失值占比和无穷值。大表会逐张读取并及时释放变量，避免同时占用过多内存。
            """
        ),
        code(
            r'''
            # ============================================================
            # 3. 通用工具函数：读取、问题登记、基础质量扫描
            # ============================================================
            ISSUES = []


            def record_issue(severity: str, table: str, check: str, value, detail: str) -> None:
                """登记质量问题，便于最后集中汇总。severity 建议用 ERROR/WARN/INFO。"""
                ISSUES.append({
                    "severity": severity,
                    "table": table,
                    "check": check,
                    "value": value,
                    "detail": detail,
                })


            def read_table(name: str, columns: list[str] | None = None) -> pd.DataFrame:
                """读取 processed Parquet 表。columns 用于只取必要列，降低内存。"""
                return pd.read_parquet(DATA_PROC / name, columns=columns)


            def invalid_code_mask(series: pd.Series) -> pd.Series:
                """检查 ts_code 是否为 6 位代码 + 交易所后缀，如 000001.SZ。"""
                return ~series.astype("string").str.match(CODE_PATTERN, na=False)


            def max_abs_diff(left: pd.Series, right: pd.Series) -> float:
                """计算两列的最大绝对误差；全部为空时返回 NaN。"""
                diff = (left - right).abs().replace([np.inf, -np.inf], np.nan)
                return float(diff.max()) if diff.notna().any() else np.nan


            def general_quality_scan(name: str, spec: dict) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
                """
                对单表做通用质量扫描。

                返回：表级摘要、各列缺失率、数值列无穷值计数。
                """
                path = DATA_PROC / name
                if not path.exists():
                    record_issue("ERROR", name, "file_exists", False, "processed 文件不存在。")
                    return {"table": name, "exists": False}, pd.DataFrame(), pd.DataFrame()

                df = read_table(name)
                required_cols = spec["required_cols"]
                key_cols = spec["key_cols"]
                date_cols = spec["date_cols"]
                missing_required = [col for col in required_cols if col not in df.columns]

                if missing_required:
                    record_issue("ERROR", name, "required_columns", len(missing_required), f"缺少字段: {missing_required}")

                duplicate_keys = np.nan
                if all(col in df.columns for col in key_cols):
                    duplicate_keys = int(df.duplicated(subset=key_cols).sum())
                    if duplicate_keys > 0:
                        record_issue("ERROR", name, "duplicate_keys", duplicate_keys, f"主键重复: {key_cols}")

                date_summary = {}
                for col in date_cols:
                    if col in df.columns:
                        date_summary[f"{col}_min"] = df[col].min()
                        date_summary[f"{col}_max"] = df[col].max()
                        date_summary[f"{col}_nunique"] = df[col].nunique(dropna=True)
                    else:
                        date_summary[f"{col}_min"] = pd.NaT
                        date_summary[f"{col}_max"] = pd.NaT
                        date_summary[f"{col}_nunique"] = np.nan

                invalid_codes = np.nan
                unique_codes = np.nan
                if "ts_code" in df.columns:
                    bad_mask = invalid_code_mask(df["ts_code"])
                    invalid_codes = int(bad_mask.sum())
                    unique_codes = int(df["ts_code"].nunique(dropna=True))
                    if invalid_codes > 0:
                        examples = df.loc[bad_mask, "ts_code"].dropna().astype(str).unique()[:10].tolist()
                        record_issue("ERROR", name, "ts_code_format", invalid_codes, f"示例: {examples}")

                null_counts = df.isna().sum()
                null_detail = (
                    pd.DataFrame({
                        "table": name,
                        "column": null_counts.index,
                        "null_count": null_counts.values,
                        "null_pct": null_counts.values / max(len(df), 1),
                    })
                    .sort_values(["null_pct", "null_count"], ascending=False)
                    .reset_index(drop=True)
                )

                numeric = df.select_dtypes(include=[np.number])
                if numeric.shape[1] > 0:
                    inf_counts = np.isinf(numeric).sum(axis=0)
                    inf_detail = pd.DataFrame({"table": name, "column": inf_counts.index, "inf_count": inf_counts.values})
                    bad_inf = inf_detail[inf_detail["inf_count"] > 0]
                    if not bad_inf.empty:
                        record_issue("ERROR", name, "infinite_values", int(bad_inf["inf_count"].sum()), "数值列存在 inf 或 -inf。")
                else:
                    inf_detail = pd.DataFrame(columns=["table", "column", "inf_count"])

                overview = {
                    "table": name,
                    "description": spec["desc"],
                    "exists": True,
                    "rows": len(df),
                    "cols": df.shape[1],
                    "memory_mb": df.memory_usage(deep=True).sum() / 1024 / 1024,
                    "duplicate_keys": duplicate_keys,
                    "unique_codes": unique_codes,
                    "invalid_code_rows": invalid_codes,
                    "missing_required_cols": len(missing_required),
                    "null_cells": int(null_counts.sum()),
                    "null_cell_pct": float(null_counts.sum() / max(df.shape[0] * df.shape[1], 1)),
                    **date_summary,
                }

                del df
                gc.collect()
                return overview, null_detail, inf_detail


            # 执行通用质量扫描。
            overview_rows = []
            null_details = []
            inf_details = []
            for name, spec in EXPECTED_TABLES.items():
                overview, null_detail, inf_detail = general_quality_scan(name, spec)
                overview_rows.append(overview)
                if not null_detail.empty:
                    null_details.append(null_detail)
                if not inf_detail.empty:
                    inf_details.append(inf_detail)

            quality_overview = pd.DataFrame(overview_rows)
            null_detail_df = pd.concat(null_details, ignore_index=True) if null_details else pd.DataFrame()
            inf_detail_df = pd.concat(inf_details, ignore_index=True) if inf_details else pd.DataFrame()

            display(quality_overview.sort_values("table").reset_index(drop=True))
            display(Markdown("### 缺失值字段明细（只显示缺失率 > 0）"))
            display(null_detail_df[null_detail_df["null_count"] > 0].sort_values(["table", "null_pct"], ascending=[True, False]).reset_index(drop=True))
            '''
        ),
        code(
            r'''
            # ============================================================
            # 3.1 可视化：各表行数和缺失率，便于快速定位异常规模
            # ============================================================
            fig, axes = plt.subplots(1, 2, figsize=(16, 5))

            plot_df = quality_overview.sort_values("rows", ascending=True)
            axes[0].barh(plot_df["table"], plot_df["rows"])
            axes[0].set_xscale("log")
            axes[0].set_title("Rows by table (log scale)")
            axes[0].set_xlabel("rows")

            plot_df = quality_overview.sort_values("null_cell_pct", ascending=True)
            axes[1].barh(plot_df["table"], plot_df["null_cell_pct"] * 100)
            axes[1].set_title("Null cell percentage")
            axes[1].set_xlabel("%")

            plt.tight_layout()
            plt.show()
            '''
        ),
        md(
            """
            ## 4. 每张表的基础描述

            这一部分按表输出：日期覆盖、股票覆盖、数值列分布、布尔列比例、字符/类别列的 top 值。大表数值描述默认使用固定 seed 抽样，缺失率仍来自整表。
            """
        ),
        code(
            r'''
            # ============================================================
            # 4. 基础描述工具：数值列、布尔列、类别列、日期列
            # ============================================================
            def describe_table(name: str) -> dict[str, pd.DataFrame]:
                """返回单表的基础描述，包含日期、数值、布尔和类别字段。"""
                df = read_table(name)
                spec = EXPECTED_TABLES[name]

                # 日期覆盖：每个日期列的最小值、最大值和唯一日期数量。
                date_desc = pd.DataFrame([
                    {
                        "column": col,
                        "min": df[col].min(),
                        "max": df[col].max(),
                        "nunique": df[col].nunique(dropna=True),
                        "null_pct": df[col].isna().mean(),
                    }
                    for col in spec["date_cols"]
                    if col in df.columns
                ])

                # 数值描述：大表默认抽样做 describe，同时补充整表缺失率、零值率和负值率。
                numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
                if numeric_cols:
                    if (not FULL_NUMERIC_PROFILE) and len(df) > PROFILE_SAMPLE_ROWS:
                        sample = df.sample(PROFILE_SAMPLE_ROWS, random_state=RANDOM_SEED)
                        sample_note = f"sample_{PROFILE_SAMPLE_ROWS}"
                    else:
                        sample = df
                        sample_note = "full"
                    numeric_desc = sample[numeric_cols].describe(percentiles=[0.01, 0.05, 0.5, 0.95, 0.99]).T
                    numeric_desc["profile_scope"] = sample_note
                    numeric_desc["full_null_pct"] = df[numeric_cols].isna().mean()
                    numeric_desc["full_zero_pct"] = (df[numeric_cols] == 0).mean()
                    numeric_desc["full_negative_pct"] = (df[numeric_cols] < 0).mean()
                else:
                    numeric_desc = pd.DataFrame()

                # 布尔列描述：True 占比可直接理解为状态发生率。
                bool_cols = df.select_dtypes(include=["bool"]).columns.tolist()
                bool_desc = pd.DataFrame({
                    "column": bool_cols,
                    "true_count": [int(df[col].sum()) for col in bool_cols],
                    "true_pct": [float(df[col].mean()) for col in bool_cols],
                    "null_pct": [float(df[col].isna().mean()) for col in bool_cols],
                }) if bool_cols else pd.DataFrame()

                # 类别/字符列描述：展示唯一值数量与最常见取值。
                cat_rows = []
                cat_cols = df.select_dtypes(include=["object", "string", "category"]).columns.tolist()
                for col in cat_cols:
                    vc = df[col].astype("string").value_counts(dropna=False).head(10)
                    cat_rows.append({
                        "column": col,
                        "nunique": df[col].nunique(dropna=True),
                        "null_pct": df[col].isna().mean(),
                        "top_values": "; ".join([f"{idx}: {val}" for idx, val in vc.items()]),
                    })
                cat_desc = pd.DataFrame(cat_rows)

                # 代码覆盖：如果有 ts_code，输出唯一股票数。
                if "ts_code" in df.columns:
                    code_desc = pd.DataFrame([{
                        "unique_codes": df["ts_code"].nunique(dropna=True),
                        "invalid_code_rows": int(invalid_code_mask(df["ts_code"]).sum()),
                        "first_codes": ", ".join(sorted(df["ts_code"].dropna().astype(str).unique())[:10]),
                    }])
                else:
                    code_desc = pd.DataFrame()

                del df
                gc.collect()
                return {"date": date_desc, "code": code_desc, "numeric": numeric_desc, "boolean": bool_desc, "categorical": cat_desc}


            TABLE_PROFILES = {}
            for name in EXPECTED_TABLES:
                display(Markdown(f"### {name} — {EXPECTED_TABLES[name]['desc']}"))
                profile = describe_table(name)
                TABLE_PROFILES[name] = profile
                display(Markdown("**日期覆盖**"))
                display(profile["date"] if not profile["date"].empty else pd.DataFrame({"note": ["无日期列"]}))
                display(Markdown("**股票代码覆盖**"))
                display(profile["code"] if not profile["code"].empty else pd.DataFrame({"note": ["无 ts_code 列"]}))
                display(Markdown("**数值列描述**"))
                display(profile["numeric"] if not profile["numeric"].empty else pd.DataFrame({"note": ["无数值列"]}))
                display(Markdown("**布尔列描述**"))
                display(profile["boolean"] if not profile["boolean"].empty else pd.DataFrame({"note": ["无布尔列"]}))
                display(Markdown("**字符/类别列描述**"))
                display(profile["categorical"] if not profile["categorical"].empty else pd.DataFrame({"note": ["无字符/类别列"]}))
            '''
        ),
        md(
            """
            ## 5. 领域专项检查

            这一部分检查最容易影响后续因子和回测的金融口径：后复权收益、自由流通市值、全收益指数内部一致性、PIT 时间关系、成分股、交易状态、行业、两融和资金流。
            """
        ),
        code(
            r'''
            # ============================================================
            # 5.1 日行情：收益率、后复权价格、OHLC、成交量额
            # ============================================================
            quote_cols = [
                "trade_date", "ts_code", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount",
                "adj_factor", "close_adj", "open_adj", "ret",
            ]
            quote = read_table("daily_quote.parquet", columns=quote_cols)

            quote_checks = pd.DataFrame([
                {"check": "ret_equals_pct_chg_div_100", "value": max_abs_diff(quote["ret"], quote["pct_chg"] / 100), "threshold": "<= 1e-12 expected"},
                {"check": "close_adj_equals_close_times_adj_factor", "value": max_abs_diff(quote["close_adj"], quote["close"] * quote["adj_factor"]), "threshold": "small floating tolerance"},
                {"check": "open_adj_equals_open_times_adj_factor", "value": max_abs_diff(quote["open_adj"], quote["open"] * quote["adj_factor"]), "threshold": "small floating tolerance"},
                {"check": "non_positive_price_rows", "value": int((quote[["open", "high", "low", "close", "pre_close"]] <= 0).any(axis=1).sum()), "threshold": "0 expected"},
                {"check": "negative_vol_or_amount_rows", "value": int(((quote["vol"] < 0) | (quote["amount"] < 0)).sum()), "threshold": "0 expected"},
                {"check": "ohlc_inconsistent_rows", "value": int(((quote["high"] < quote[["open", "close", "low"]].max(axis=1)) | (quote["low"] > quote[["open", "close", "high"]].min(axis=1))).sum()), "threshold": "0 expected"},
                {"check": "abs_ret_gt_12pct_rows_flag", "value": int((quote["ret"].abs() > 0.12).sum()), "threshold": "flag only"},
                {"check": "zero_volume_or_amount_rows_flag", "value": int(((quote["vol"] == 0) | (quote["amount"] == 0)).sum()), "threshold": "flag only; should align with status"},
            ])
            display(quote_checks)

            if quote_checks.loc[quote_checks["check"] == "ret_equals_pct_chg_div_100", "value"].iloc[0] > 1e-10:
                record_issue("ERROR", "daily_quote.parquet", "ret口径", quote_checks.loc[0, "value"], "ret 与 pct_chg/100 不一致。")
            if quote_checks.loc[quote_checks["check"] == "non_positive_price_rows", "value"].iloc[0] > 0:
                record_issue("ERROR", "daily_quote.parquet", "价格非正", quote_checks.loc[3, "value"], "open/high/low/close/pre_close 出现 <=0。")
            if quote_checks.loc[quote_checks["check"] == "ohlc_inconsistent_rows", "value"].iloc[0] > 0:
                record_issue("ERROR", "daily_quote.parquet", "OHLC关系", quote_checks.loc[5, "value"], "high/low 与 open/close 的大小关系异常。")
            '''
        ),
        code(
            r'''
            # ============================================================
            # 5.2 每日基础指标：自由流通市值、对数市值、与行情 close 一致性
            # ============================================================
            basic_cols = [
                "trade_date", "ts_code", "close", "free_share", "free_float_mv", "log_free_float_mv", "total_mv", "circ_mv",
                "turnover_rate", "turnover_rate_f", "pe", "pe_ttm", "pb", "ps_ttm", "dv_ttm",
            ]
            basic = read_table("daily_basic.parquet", columns=basic_cols)

            expected_ffmv = basic["free_share"] * basic["close"] * 100
            positive_ffmv = basic["free_float_mv"] > 0
            expected_log_ffmv = np.log(basic.loc[positive_ffmv, "free_float_mv"])
            quote_close = quote[["trade_date", "ts_code", "close"]].rename(columns={"close": "quote_close"})
            merged_close = basic[["trade_date", "ts_code", "close"]].merge(quote_close, on=["trade_date", "ts_code"], how="left")

            basic_checks = pd.DataFrame([
                {"check": "free_float_mv_equals_free_share_times_close_times_100", "value": max_abs_diff(basic["free_float_mv"], expected_ffmv), "threshold": "small tolerance; unit from plan.md"},
                {"check": "log_free_float_mv_equals_log_free_float_mv", "value": max_abs_diff(basic.loc[positive_ffmv, "log_free_float_mv"], expected_log_ffmv), "threshold": "small tolerance"},
                {"check": "non_positive_free_float_mv_rows", "value": int((basic["free_float_mv"] <= 0).sum()), "threshold": "0 expected"},
                {"check": "negative_turnover_rows_flag", "value": int(((basic["turnover_rate"] < 0) | (basic["turnover_rate_f"] < 0)).sum()), "threshold": "0 expected"},
                {"check": "daily_basic_close_vs_daily_quote_close_max_abs_diff", "value": max_abs_diff(merged_close["close"], merged_close["quote_close"]), "threshold": "0 or tiny tolerance"},
                {"check": "daily_basic_rows_without_quote_match", "value": int(merged_close["quote_close"].isna().sum()), "threshold": "0 expected"},
            ])
            display(basic_checks)

            if basic_checks.loc[basic_checks["check"] == "non_positive_free_float_mv_rows", "value"].iloc[0] > 0:
                record_issue("ERROR", "daily_basic.parquet", "自由流通市值非正", basic_checks.loc[2, "value"], "中性化需要正的 free_float_mv。")
            if basic_checks.loc[basic_checks["check"] == "daily_basic_rows_without_quote_match", "value"].iloc[0] > 0:
                record_issue("ERROR", "daily_basic.parquet", "行情键缺失", basic_checks.loc[5, "value"], "daily_basic 中存在无法匹配 daily_quote 的键。")

            del basic, quote_close, merged_close, quote
            gc.collect()
            '''
        ),
        code(
            r'''
            # ============================================================
            # 5.3 指数基准：index_ret 和 nav 的内部一致性
            # ============================================================
            index_quote = read_table("index_quote.parquet").sort_values("trade_date").reset_index(drop=True)
            nav_recalc = (1 + index_quote["index_ret"]).cumprod()
            index_checks = pd.DataFrame([
                {"check": "index_ret_equals_pct_chg_div_100", "value": max_abs_diff(index_quote["index_ret"], index_quote["pct_chg"] / 100), "threshold": "<= 1e-12 expected"},
                {"check": "nav_equals_cumprod_1_plus_index_ret", "value": max_abs_diff(index_quote["nav"], nav_recalc), "threshold": "small tolerance"},
                {"check": "date_min", "value": index_quote["trade_date"].min(), "threshold": f">= {cfg.MARKET_START.date()}"},
                {"check": "date_max", "value": index_quote["trade_date"].max(), "threshold": f"<= {cfg.MARKET_END.date()}"},
                {"check": "non_positive_nav_rows", "value": int((index_quote["nav"] <= 0).sum()), "threshold": "0 expected"},
            ])
            display(index_checks)
            display(Markdown("提示：processed 表无法单独证明原始源一定是中证500全收益指数，需要结合下载脚本或原始字段复核。"))
            '''
        ),
        code(
            r'''
            # ============================================================
            # 5.4 PIT 表：日期关系和 as-of 快照审计
            # ============================================================
            def pit_table_checks(name: str) -> pd.DataFrame:
                """检查 PIT 表日期关系，特别是 pit_date/end_date/available_date。"""
                df = read_table(name)
                rows = [
                    {"check": "rows", "value": len(df), "threshold": ">0"},
                    {"check": "pit_date_null_rows", "value": int(df["pit_date"].isna().sum()), "threshold": "0 expected"},
                ]
                if "end_date" in df.columns:
                    rows += [
                        {"check": "end_date_null_rows", "value": int(df["end_date"].isna().sum()), "threshold": "0 expected except special event tables"},
                        {"check": "pit_date_before_end_date_rows", "value": int((df["pit_date"] < df["end_date"]).sum()), "threshold": "0 expected for reporting PIT"},
                    ]
                if "available_date" in df.columns:
                    rows += [
                        {"check": "available_date_before_pit_date_rows", "value": int((df["available_date"] < df["pit_date"]).sum()), "threshold": "0 expected"},
                        {"check": "available_date_before_end_date_rows", "value": int((df["available_date"] < df["end_date"]).sum()), "threshold": "0 expected"},
                    ]
                if "ex_date" in df.columns:
                    rows += [
                        {"check": "ex_date_null_rows", "value": int(df["ex_date"].isna().sum()), "threshold": "check source meaning"},
                        {"check": "ex_date_before_pit_date_rows_flag", "value": int((df["ex_date"] < df["pit_date"]).sum()), "threshold": "flag only"},
                    ]

                result = pd.DataFrame(rows)
                if name in {"financial_pit.parquet", "indicator_pit.parquet"}:
                    bad_pit = result.loc[result["check"].isin(["pit_date_null_rows", "pit_date_before_end_date_rows"]), "value"].sum()
                    if bad_pit > 0:
                        record_issue("ERROR", name, "PIT日期关系", int(bad_pit), "pit_date 为空或早于 end_date，可能引入时间口径问题。")
                elif name == "holder_pit.parquet":
                    bad_available = result.loc[
                        result["check"].isin([
                            "pit_date_null_rows",
                            "available_date_before_pit_date_rows",
                            "available_date_before_end_date_rows",
                        ]),
                        "value",
                    ].sum()
                    if bad_available > 0:
                        record_issue("ERROR", name, "available_date日期关系", int(bad_available), "股东人数 PIT 的保守可用日早于 pit_date 或 end_date。")
                    pit_before_end = result.loc[result["check"] == "pit_date_before_end_date_rows", "value"].sum()
                    if pit_before_end > 0:
                        record_issue("INFO", name, "pit_date早于end_date", int(pit_before_end), "股东人数表使用 available_date 保守处理，此项作为信息提示。")

                del df
                gc.collect()
                return result


            for pit_name in ["financial_pit.parquet", "indicator_pit.parquet", "holder_pit.parquet", "dividend_pit.parquet"]:
                display(Markdown(f"### {pit_name}"))
                display(pit_table_checks(pit_name))


            def latest_pit_snapshot(df: pd.DataFrame, asof_date: pd.Timestamp, use_available_date: bool = False) -> pd.DataFrame:
                """构造 T 日 PIT 快照，保证 pit_date <= T 且 end_date < T。"""
                mask = (df["pit_date"] <= asof_date) & (df["end_date"] < asof_date)
                if use_available_date and "available_date" in df.columns:
                    mask &= df["available_date"] <= asof_date
                snap = df.loc[mask].sort_values(["ts_code", "end_date", "pit_date"])
                if snap.empty:
                    return snap
                return snap.groupby("ts_code", as_index=False).last()


            AUDIT_DATES = [cfg.TRAIN_END, cfg.VALID_END, cfg.TEST_END]
            audit_rows = []
            for name, use_available in [("financial_pit.parquet", False), ("indicator_pit.parquet", False), ("holder_pit.parquet", True)]:
                df = read_table(name)
                for asof_date in AUDIT_DATES:
                    snap = latest_pit_snapshot(df, asof_date, use_available_date=use_available)
                    audit_rows.append({
                        "table": name,
                        "asof_date": asof_date,
                        "snapshot_rows": len(snap),
                        "max_pit_date": snap["pit_date"].max() if not snap.empty else pd.NaT,
                        "max_end_date": snap["end_date"].max() if not snap.empty else pd.NaT,
                        "future_pit_rows": int((snap["pit_date"] > asof_date).sum()) if not snap.empty else 0,
                        "future_end_rows": int((snap["end_date"] >= asof_date).sum()) if not snap.empty else 0,
                        "future_available_rows": int((snap["available_date"] > asof_date).sum()) if use_available and not snap.empty else 0,
                    })
                del df
                gc.collect()

            pit_audit_df = pd.DataFrame(audit_rows)
            display(Markdown("### PIT as-of 快照审计"))
            display(pit_audit_df)

            bad_future = pit_audit_df[["future_pit_rows", "future_end_rows", "future_available_rows"]].sum().sum()
            if bad_future > 0:
                record_issue("ERROR", "PIT快照", "future_data", int(bad_future), "as-of 快照中存在未来日期。")
            '''
        ),
        code(
            r'''
            # ============================================================
            # 5.5 成分股、股票状态、行业覆盖
            # ============================================================
            members = read_table("index_member.parquet")
            member_daily = members.groupby("rebalance_date").agg(
                n_members=("ts_code", "nunique"),
                rows=("ts_code", "size"),
                weight_sum=("index_weight", "sum"),
                tradable_rate=("tradable", "mean"),
                suspended_rate=("is_suspended", "mean"),
                st_rate=("is_st", "mean"),
                new_stock_rate=("is_new_stock", "mean"),
                limit_locked_rate=("is_limit_locked", "mean"),
            ).reset_index()
            member_anomalies = member_daily[
                (member_daily["n_members"] != 500)
                | (member_daily["rows"] != 500)
                | ((member_daily["weight_sum"] - 100).abs() > 1)
            ]
            if not member_anomalies.empty:
                record_issue("WARN", "index_member.parquet", "成分股数量或权重", len(member_anomalies), "部分调仓日不是 500 只或权重和偏离 100 超过 1。")

            status = read_table("stock_status.parquet")
            status_daily = status.groupby("trade_date").agg(
                n_stocks=("ts_code", "nunique"),
                tradable_rate=("tradable", "mean"),
                suspended_rate=("is_suspended", "mean"),
                st_rate=("is_st", "mean"),
                new_stock_rate=("is_new_stock", "mean"),
                limit_locked_rate=("is_limit_locked", "mean"),
                limit_up_locked_rate=("is_limit_up_locked", "mean"),
                limit_down_locked_rate=("is_limit_down_locked", "mean"),
            ).reset_index()
            limit_status_counts = status["limit_status"].value_counts(dropna=False).rename_axis("limit_status").reset_index(name="rows")

            industry = read_table("industry.parquet")
            industry_daily = industry.groupby("trade_date").agg(
                rows=("ts_code", "size"),
                n_stocks=("ts_code", "nunique"),
                n_industries=("industry_name", "nunique"),
                unclassified_rate=("industry_name", lambda s: (s == cfg.INDUSTRY_UNCLASSIFIED_NAME).mean()),
            ).reset_index()
            industry_distribution = industry["industry_name"].value_counts(dropna=False).rename_axis("industry_name").reset_index(name="rows")
            high_unclassified = industry_daily[industry_daily["unclassified_rate"] > 0.05]
            if not high_unclassified.empty:
                record_issue("WARN", "industry.parquet", "未分类行业比例", len(high_unclassified), "存在未分类比例超过 5% 的交易日。")

            display(Markdown("### index_member 每日摘要"))
            display(member_daily.describe(include="all"))
            display(Markdown("### index_member 异常调仓日"))
            display(member_anomalies.head(30))
            display(Markdown("### stock_status 每日摘要"))
            display(status_daily.describe(include="all"))
            display(Markdown("### limit_status 分布"))
            display(limit_status_counts)
            display(Markdown("### industry 每日摘要"))
            display(industry_daily.describe(include="all"))
            display(Markdown("### 行业分布"))
            display(industry_distribution)
            display(Markdown("### 未分类比例超过 5% 的日期"))
            display(high_unclassified.head(30))

            del status, industry
            gc.collect()
            '''
        ),
        code(
            r'''
            # ============================================================
            # 5.6 两融与资金流：非负字段和基础分布
            # ============================================================
            margin = read_table("margin.parquet")
            margin_nonnegative_cols = ["rzye", "rqye", "rzmre", "rzche", "rqyl", "rqchl", "rqmcl", "rzrqye"]
            margin_checks = []
            for col in margin_nonnegative_cols:
                margin_checks.append({"check": f"negative_{col}_rows", "value": int((margin[col] < 0).sum()), "threshold": "0 expected"})
            margin_checks += [
                {"check": "rzrqye_equals_rzye_plus_rqye_max_abs_diff", "value": max_abs_diff(margin["rzrqye"], margin["rzye"] + margin["rqye"]), "threshold": "small tolerance if same unit"},
                {"check": "unique_codes", "value": margin["ts_code"].nunique(), "threshold": "两融标的子集，通常少于全市场"},
            ]
            margin_check_df = pd.DataFrame(margin_checks)
            display(Markdown("### margin 检查"))
            display(margin_check_df)
            bad_margin_negative = margin_check_df[margin_check_df["check"].str.startswith("negative_")]["value"].sum()
            if bad_margin_negative > 0:
                record_issue("ERROR", "margin.parquet", "两融非负字段", int(bad_margin_negative), "余额或买卖/偿还字段出现负值。")
            del margin
            gc.collect()

            moneyflow = read_table("moneyflow.parquet")
            moneyflow_nonnegative_cols = ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount", "buy_md_amount", "buy_sm_amount"]
            moneyflow_checks = []
            for col in moneyflow_nonnegative_cols:
                moneyflow_checks.append({"check": f"negative_{col}_rows", "value": int((moneyflow[col] < 0).sum()), "threshold": "0 expected"})
            moneyflow_checks += [
                {"check": "unique_codes", "value": moneyflow["ts_code"].nunique(), "threshold": "should be close to daily_quote universe if source complete"},
                {"check": "net_mf_amount_abs_p99", "value": float(moneyflow["net_mf_amount"].abs().quantile(0.99)), "threshold": "distribution flag only"},
            ]
            moneyflow_check_df = pd.DataFrame(moneyflow_checks)
            display(Markdown("### moneyflow 检查"))
            display(moneyflow_check_df)
            bad_moneyflow_negative = moneyflow_check_df[moneyflow_check_df["check"].str.startswith("negative_")]["value"].sum()
            if bad_moneyflow_negative > 0:
                record_issue("ERROR", "moneyflow.parquet", "资金流非负字段", int(bad_moneyflow_negative), "买卖额字段出现负值。")
            del moneyflow
            gc.collect()
            '''
        ),
        md(
            """
            ## 6. 跨表覆盖关系

            这一部分按交易日统计各表相对 `daily_quote` 的覆盖率。`daily_basic`、`stock_status`、`industry` 理论上应与 `daily_quote` 高度一致；`margin` 是两融标的子集，覆盖率低于全市场是正常现象；`moneyflow` 应接近全市场，但仍取决于源数据覆盖。
            """
        ),
        code(
            r'''
            # ============================================================
            # 6. 跨表覆盖：与 daily_quote 的每日行数和股票数对齐
            # ============================================================
            def daily_key_count(name: str, date_col: str = "trade_date") -> pd.DataFrame:
                """返回某表按日期的行数和股票数，用于跨表覆盖比较。"""
                has_code = "ts_code" in EXPECTED_TABLES[name]["required_cols"]
                cols = [date_col, "ts_code"] if has_code else [date_col]
                df = read_table(name, columns=cols)
                if "ts_code" in df.columns:
                    out = df.groupby(date_col).agg(rows=("ts_code", "size"), n_codes=("ts_code", "nunique")).reset_index()
                else:
                    out = df.groupby(date_col).size().reset_index(name="rows")
                    out["n_codes"] = np.nan
                out = out.rename(columns={date_col: "trade_date", "rows": f"{name}_rows", "n_codes": f"{name}_n_codes"})
                del df
                gc.collect()
                return out


            quote_counts = daily_key_count("daily_quote.parquet")
            coverage_tables = ["daily_basic.parquet", "stock_status.parquet", "industry.parquet", "moneyflow.parquet", "margin.parquet"]
            coverage = quote_counts.copy()
            for name in coverage_tables:
                coverage = coverage.merge(daily_key_count(name), on="trade_date", how="left")
                coverage[f"{name}_row_coverage"] = coverage[f"{name}_rows"] / coverage["daily_quote.parquet_rows"]
                coverage[f"{name}_code_coverage"] = coverage[f"{name}_n_codes"] / coverage["daily_quote.parquet_n_codes"]

            coverage_summary_cols = [col for col in coverage.columns if col.endswith("_coverage")]
            coverage_summary = coverage[coverage_summary_cols].describe(percentiles=[0.01, 0.05, 0.5, 0.95, 0.99]).T
            display(coverage_summary)

            # 成分股调仓日必须能在日行情交易日中找到，否则 T 日优化无法取得行情和状态。
            quote_dates = set(quote_counts["trade_date"])
            member_dates = set(members["rebalance_date"])
            missing_member_trade_dates = sorted(member_dates - quote_dates)
            member_date_check = pd.DataFrame([{
                "n_rebalance_dates": len(member_dates),
                "n_missing_in_daily_quote": len(missing_member_trade_dates),
                "missing_examples": missing_member_trade_dates[:10],
            }])
            display(member_date_check)

            if missing_member_trade_dates:
                record_issue("ERROR", "index_member.parquet", "调仓日行情缺失", len(missing_member_trade_dates), "部分 rebalance_date 不在 daily_quote.trade_date 中。")

            del members, quote_counts
            gc.collect()
            '''
        ),
        md(
            """
            ## 7. 问题汇总与可选导出

            最后集中展示本 notebook 记录的 `ERROR/WARN/INFO`。建议优先处理 `ERROR`，再判断 `WARN` 是否符合数据源语义。若需要留痕，可将 `SAVE_REPORTS` 改为 `True` 后重新运行本单元。
            """
        ),
        code(
            r'''
            # ============================================================
            # 7. 问题汇总：按严重程度排序输出
            # ============================================================
            severity_order = {"ERROR": 0, "WARN": 1, "INFO": 2}
            issues_df = pd.DataFrame(ISSUES)
            if issues_df.empty:
                issues_df = pd.DataFrame(columns=["severity", "table", "check", "value", "detail"])
            else:
                issues_df = (
                    issues_df.assign(_severity_order=issues_df["severity"].map(severity_order).fillna(99))
                    .sort_values(["_severity_order", "table", "check"])
                    .drop(columns="_severity_order")
                    .reset_index(drop=True)
                )
            display(issues_df)

            # 可选导出：保存主要质量报告到 data/quality_reports。
            if SAVE_REPORTS:
                REPORT_DIR.mkdir(parents=True, exist_ok=True)
                metadata_df.to_csv(REPORT_DIR / "processed_metadata.csv", index=False, encoding="utf-8-sig")
                quality_overview.to_csv(REPORT_DIR / "processed_quality_overview.csv", index=False, encoding="utf-8-sig")
                null_detail_df.to_csv(REPORT_DIR / "processed_null_detail.csv", index=False, encoding="utf-8-sig")
                inf_detail_df.to_csv(REPORT_DIR / "processed_inf_detail.csv", index=False, encoding="utf-8-sig")
                coverage.to_csv(REPORT_DIR / "processed_daily_coverage.csv", index=False, encoding="utf-8-sig")
                issues_df.to_csv(REPORT_DIR / "processed_quality_issues.csv", index=False, encoding="utf-8-sig")
                print(f"已导出到: {REPORT_DIR}")
            else:
                print("SAVE_REPORTS=False：未导出 CSV。需要留痕时，将 SAVE_REPORTS 改为 True 后重新运行本单元。")
            '''
        ),
        md(
            """
            ## 8. 人工复核建议

            自动检查不能替代字段语义复核。建议重点人工核对：

            - `index_quote.parquet` 的源数据确认为中证500全收益指数，而不是价格指数；
            - `daily_basic.parquet` 中 `free_share` 的含义确认为自由流通股本，`free_float_mv` 单位与计划一致；
            - `financial_pit.parquet` / `indicator_pit.parquet` 的 `pit_date` 来自公告可用日期，而不是报告期 `end_date`；
            - `index_member.parquet` 的 `rebalance_date` 是成分股生效快照日期，并与月末交易日一致；
            - `stock_status.parquet` 的停牌、涨跌停、一字板、ST、新股标记和后续优化约束保持一致。
            """
        ),
    ]
    return nb


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    nbf.write(build_notebook(), OUT)
    print(OUT.resolve())
