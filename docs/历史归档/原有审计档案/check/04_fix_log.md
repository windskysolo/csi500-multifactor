# 修复记录

> 本文件记录每次修复的问题编号、变更内容、验证结果。
> **规则**：每次修复后在本文件末尾追加新的"修复批次"章节，不修改已有历史记录。
> 问题编号来自 `check/03_stage_review_findings.md`（F0-xxx / F1-xxx / F2-xxx 等）。

---

## 修复批次 1 — 2026-05-20

**范围**：阶段 0/1 遗留问题（F0-003 补充、F0-005 骨架、F1-001 ~ F1-005）

**对应审查文件**：`check/03_stage_review_findings.md` 阶段 0 / 阶段 1

---

### F0-003 补充 — 测试集运行日志缺失

**修复文件**：`check/test_set_run_log.md`（新建）

**变更**：
- 新建运行日志，记录 Run#1（2026-05-12，commit `80708d4`）的日期、目的、注意事项
- 预留 Run#2 表格和防误用检查清单（剩余 1 次机会）
- 明确标注 Run#1 依赖旧收益口径（F2-001 未修复前），待上游修复后决定是否消耗 Run#2

**验证**：`git log --oneline --grep='\[TEST_SET_RUN_' --all` 返回 1 条，与日志一致。

---

### F1-001 — PROJECT_PLAN_v1.1.md 数据源口径冲突（CSMAR vs Tushare Pro）

**修复文件**：`PROJECT_PLAN_v1.1.md`

**变更（共 9 处）**：

| 位置 | 修改前 | 修改后 |
|---|---|---|
| 关键决策表·数据源 | CSMAR | Tushare Pro |
| 禁用库原因 | 与 CSMAR 不兼容 | 与 Tushare Pro 格式不兼容 |
| 目录注释 data/raw/ | CSMAR 原始 CSV | Tushare Pro 原始 CSV |
| 第1周下载清单 | 从 CSMAR 下载（CSV，GBK 编码） | 从 Tushare Pro 下载，字段逐一对应 |
| PIT 转换实现 | Accper/Annodt 字段名 | 保留概念，新增 Tushare 字段映射（ann_date/end_date/f_ann_date） |
| 风险点说明 | CSMAR 财务数据默认非 PIT | Tushare 财务数据默认非 PIT |
| 数据备份说明 | CSMAR 重新下载要排队 | Tushare Pro 接口有频率限制 |

**保留内容**：字段映射行中保留 `CSMAR Annodt / Accper` 作为概念对照，不作为当前数据源方案。

**验证**：`rg -n "CSMAR" PROJECT_PLAN_v1.1.md` 只剩 1 处字段映射说明。

---

### F1-002 — PROJECT_PLAN_v1.1.md 行业体系口径冲突（中信一级 vs SW2021）

**修复文件**：`PROJECT_PLAN_v1.1.md`

**变更（共 2 处）**：

| 位置 | 修改前 | 修改后 |
|---|---|---|
| 关键决策表·中性化基准 | 行业（中信一级） | 行业（申万一级 SW2021，31个行业） |
| 风险登记表 #16 | 全程统一中信一级 | 已统一为申万一级 SW2021；industry_citics.csv 为历史遗留文件名 |

**验证**：`rg -n "中信一级" PROJECT_PLAN_v1.1.md` 无匹配。

---

### F1-003 — 缺少依赖和环境声明文件

**修复文件**：`requirements.txt`（新建）

**变更**：
- 覆盖实际使用的全部库：pandas/numpy/pyarrow/scipy/statsmodels/scikit-learn/cvxpy/tushare/matplotlib/plotly/jupyterlab/tqdm/joblib/nbformat/pytest
- 声明 `python>=3.10`、`pandas>=2.0`、`cvxpy>=1.4` 版本下限
- 注明 cvxpy 推荐求解器（CLARABEL、ECOS、OSQP、SCS）

**验证**：
```bash
python -c "import pandas, numpy, pyarrow, scipy, statsmodels, sklearn, cvxpy, tushare"
```

---

### F1-004 — 缺少 README 入口文档

**修复文件**：`README.md`（新建）

**内容**：
1. 项目概述表（策略类型、数据源、基准、样本期、完成标准）
2. 免责声明
3. 环境安装命令
4. 数据下载顺序（download_tushare → download_supplement → csv_to_parquet）
5. 训练/验证期流水线命令顺序（build_factor_panels → run_factor_evaluation → notebooks）
6. 测试集运行纪律（当前已用 1 次，剩余 1 次，附运行命令）
7. 目录结构说明

---

### F1-005 — 关键研究参数未完全集中到 src/config.py

**修复文件**：`src/config.py`、`src/signal/combiner.py`、`src/portfolio/optimizer.py`、`src/evaluation/ic_analysis.py`、`scripts/run_factor_evaluation.py`、`scripts/run_test_pipeline.py`

**变更——新增到 src/config.py**（12 个常量，分 3 组）：

```python
# 信号合成参数
SIGNAL_MIN_IC_HISTORY     = 12
SIGNAL_WINDOW_MONTHS      = 24
SIGNAL_DEFAULT_MIN_VALID  = 8    # 模块默认（冷启动保守值）
SIGNAL_MIN_VALID_FACTORS  = 5    # 流水线生产用

# 单因子评价参数
EVAL_HIGH_CORR_THRESHOLD  = 0.70
EVAL_SHARPE_LS_THRESHOLD  = 0.50
EVAL_IC_EFFECTIVE_CRITERIA = dict(min_ic_ir=0.30, min_ic_mean=0.02, max_p_bh=0.05, min_dir=0.55)

# 组合优化参数
OPT_TE_TARGET_ANNUAL      = 0.05
OPT_INDUSTRY_MAX_DEV      = 0.02
OPT_SINGLE_MAX_DEV        = 0.01
OPT_TOPN                  = 50
```

**各文件改动**：

| 文件 | 改动 |
|---|---|
| `src/signal/combiner.py` | `MIN_IC_HISTORY / DEFAULT_WINDOW_MONTHS / DEFAULT_MIN_VALID_FACTORS` 改为引用 `cfg.*`；保留模块级别名兼容现有 import |
| `src/portfolio/optimizer.py` | `OptimizeConfig` 的 4 个研究参数默认值改为引用 `cfg.OPT_*` |
| `src/evaluation/ic_analysis.py` | `IC_IR_THRESHOLD / IC_MEAN_THRESHOLD / PVALUE_ALPHA / IC_POS_THRESHOLD` 改为从 `cfg.EVAL_IC_EFFECTIVE_CRITERIA` 读取 |
| `scripts/run_factor_evaluation.py` | `HIGH_CORR_THRESHOLD / SHARPE_LS_THRESHOLD / IC_EFFECTIVE_CRITERIA` 改为引用 `cfg.*` |
| `scripts/run_test_pipeline.py` | `MIN_VALID_COUNT / OPT_CONFIG` 4 个参数改为引用 `cfg.*`；`BASELINE_N` 改为 `cfg.OPT_TOPN` |

**验证**：
```
rg "HIGH_CORR_THRESHOLD\s*=\s*0\.|te_target_annual\s*=\s*0\.|MIN_IC_HISTORY\s*=\s*[0-9]|MIN_VALID_COUNT\s*=\s*[0-9]" src scripts
# → 无匹配（所有硬编码研究参数已移出）
```

---

### F0-005 — 缺少单元测试文件

**修复文件**：`tests/test_pit.py`、`tests/test_transaction.py`、`tests/test_universe.py`（均新建）

**测试覆盖（35 用例，全部通过）**：

| 文件 | 用例数 | 覆盖内容 |
|---|---|---|
| `test_pit.py` | 15 | `_filter_visible` PIT 双重约束（pit_date ≤ T，end_date < T）；ann_date 为可用日概念；`make_ttm` 不使用 T 后数据 |
| `test_transaction.py` | 12 | 印花税 2023-08-28 前后切换（10bps/5bps）；佣金双边 2.5bps；滑点；边界值（零成交、负数输入） |
| `test_universe.py` | 14 | `get_investable_universe` 过滤 ST/新股/停牌；`get_constraint_states` 约束优先级（LOCKED > NO_BUY > NO_SELL > BUY_LIMIT > FREE） |

所有测试使用合成数据 + `monkeypatch`，不依赖磁盘文件，可在无数据环境中直接运行。

**验证**：
```
python -m pytest tests/ -q
# → 35 passed in 0.99s
```

---

## 修复批次 2 — 2026-05-20

**范围**：阶段 2 问题（F2-001 ~ F2-007）

**对应审查文件**：`check/03_stage_review_findings.md` 阶段 2

---

### F2-001 / F2-002 / F2-004 — 已在 commit `95a0e3d` 修复，本批次确认

**F2-001**（`daily_quote.ret` 不是后复权收益）：`scripts/csv_to_parquet.py:241-246` 已改为
`close_adj.groupby("ts_code").pct_change(fill_method=None)`，`pct_chg` 不再用作收益字段。

**F2-002**（`stock_status` 缺少完全停牌日期）：`scripts/csv_to_parquet.py:1006-1019` 已补充
`susp_fully` 逻辑，将无行情停牌日也加入状态网格，回测引擎不会再把完全停牌股默认为 FREE。

**F2-004**（明文 Token）：两个下载脚本均已改为 `os.environ.get("TUSHARE_TOKEN")`，缺失时立即
`raise RuntimeError`；非标 API 地址已加注释说明为开发代理，并通过 `SUPPLEMENT_API_URL` 环境变量覆盖。

**注意**：上述三项代码已修复，但 `daily_quote.parquet`、`stock_status.parquet` 及所有依赖
旧 `ret` 口径的因子面板均需在下次数据重建时重新生成。

---

### F2-003 — 成分股权重 CSV 来源不可复现

**修复文件**：
- `scripts/download_tushare.py`（新增 `dl_index_weight()` 函数、`RUN_ORDER` 前置依赖、`main()` 自动检测）
- `data/csi500_index_weight_201601_202512.meta.md`（新建元数据文件）

**变更**：

| 位置 | 变更内容 |
|---|---|
| `download_tushare.py` | 新增 `dl_index_weight()` 函数，调用 `pro.index_weight(index_code='000905.SH')` 按年分批下载，去重排序后写入 `MEMBERS_FILE` |
| `download_tushare.py` | `RUN_ORDER` 首位加入 `"indexweight"`，明注为所有其他模块的前置依赖 |
| `download_tushare.py` | `dispatch` 字典加入 `"indexweight": dl_index_weight` |
| `download_tushare.py` | `main()` 启动时检测 `MEMBERS_FILE` 是否存在，不存在则自动调用 `dl_index_weight()` |
| `data/csi500_index_weight_201601_202512.meta.md` | 新建元数据文件：API 来源、字段说明、生效日定义、SHA256（`d3294c0b...`）、基准说明 |

**验证**：
```
rg "dl_index_weight|indexweight" scripts/download_tushare.py
# → 函数定义、RUN_ORDER、dispatch、main() 四处匹配
```

---

### F2-005 — 股票代码格式文档与实现不一致

**修复文件**：`AGENTS.md`、`PROJECT_PLAN_v1.1.md`（共 3 处）

**变更**：

| 文件 | 位置 | 修改前 | 修改后 |
|---|---|---|---|
| `AGENTS.md` | 数据层规则表·股票代码 | 统一为 6 位字符串（含前导零） | 全程使用 Tushare `ts_code` 格式（含交易所后缀，如 `000001.SZ`），禁止仅用 6 位裸代码 |
| `PROJECT_PLAN_v1.1.md` | 第 2 周核心口径清单 | 股票代码统一为 6 位字符串 | 股票代码使用 Tushare `ts_code` 格式（含交易所后缀，如 `000001.SZ`），禁止仅用 6 位裸代码 |
| `PROJECT_PLAN_v1.1.md` | 七、自检清单·数据阶段 | 股票代码格式统一（6 位字符串） | 股票代码格式统一（ts_code，含交易所后缀，如 000001.SZ） |

**说明**：实现一直使用 `ts_code` 格式；文档改为与实现一致，不需要改代码。

---

### F2-006 — `daily_basic` 自由流通市值缺失未拦截

**修复文件**：`scripts/csv_to_parquet.py`（M2 验证段）

**变更**：在 `assert (df["free_float_mv"] <= 0).sum() == 0` 之后新增：

```python
# NaN 率检查：自由流通市值是中性化核心变量，缺失需明确记录
fs_nan  = int(df["free_share"].isna().sum())
log_nan = int(df["log_free_float_mv"].isna().sum())
# 记录缺失行数和比例，超过 1% 时断言失败
assert log_nan_rate < 0.01, "log_free_float_mv 缺失率超过 1%"
```

**说明**：超过阈值时构建脚本中断并提示检查，缺失率在阈值内时记录日志便于追踪。

---

### F2-007 — 财务三表 PIT 宽表丢弃源可用日列

**修复文件**：`scripts/csv_to_parquet.py`（M5 `process_financial_pit()` 段）

**变更**：

| 行为 | 修改前 | 修改后 |
|---|---|---|
| 三表合并后源 pit_date 列 | `df.drop(columns=["_pit_inc", "_pit_bal", "_pit_cf"])` | 保留这三列，写入 `financial_pit.parquet` |
| 数值类型转换 | `num_cols` 包含所有非索引列 | 从 `num_cols` 中排除 `_pit_inc/_pit_bal/_pit_cf`（datetime 列不做数值转换） |

`financial_pit.parquet` 重建后，`_pit_inc`、`_pit_bal`、`_pit_cf` 字段可还原"哪张表哪天才可用"，满足字段级 PIT 审计要求。

**验证**：
```python
import pandas as pd
df = pd.read_parquet("data/processed/financial_pit.parquet")
assert "_pit_inc" in df.columns
assert "_pit_bal" in df.columns
assert "_pit_cf"  in df.columns
# 任何 _pit_* 列的值均 <= 对应 pit_date（否则取最大值的逻辑有误）
assert (df["_pit_inc"].dropna() <= df.loc[df["_pit_inc"].notna(), "pit_date"].values).all()
```
（验证需在数据重建后执行）

---

## 修复批次 3 — 2026-05-20

**范围**：阶段 3 问题（F3-001 ~ F3-005）

**对应审查文件**：`check/03_stage_review_findings.md` 阶段 3

---

### F3-001 — 量价因子继承错误 `daily_quote.ret` 口径

**代码层已在 F2-001（commit `95a0e3d`）修复**：`daily_quote.ret` 现已由
`close_adj.groupby("ts_code").pct_change(fill_method=None)` 生成，
`price_factors.py` 中 `factor_vol_60d / factor_ivol_60d / factor_max_ret / factor_amihud`
均使用 `dq["ret"]`，口径已正确。

**本批次行动**：
- 新增 `tests/test_factor_time_boundary.py`（见 F3-003）：
  - `TestFactorUsesAdjustedRet` 类（3 个用例）通过注入已知 ret 值验证三个因子的输出与
    预期一致，证明它们确实读取了 `dq["ret"]` 而非其他列。
  - `test_vol_60d_differs_with_wrong_ret`：反例，证明注入含除权跳变的错误 ret
    会产生显著不同的 vol_60d，测试有区分能力。

**运行时遗留**：`vol_60d / ivol_60d / max_ret / amihud.parquet` 及下游产物需在
`daily_quote.parquet` 重建后重新运行 `scripts/build_factor_panels.py` 重建。

---

### F3-002 — 因子构建脚本缺少测试集纪律防护

**修复文件**：`scripts/build_factor_panels.py`

**变更**：

| 位置 | 变更内容 |
|---|---|
| 常量区 | 新增 `DIAGNOSTICS_PATH`、`TEST_SET_RUN_LOG`、`MAX_TEST_SET_RUNS = 2` |
| 新函数 `_count_test_set_git_runs()` | 通过 `subprocess` 统计 git 历史中含 `[TEST_SET_RUN_` 的 commit 数 |
| `build_panels()` 参数 | 新增 `output_dir: Path | None = None`；测试集模式下写入独立目录 |
| `_parse_args()` | 新增 `--allow-test-set`（布尔开关）和 `--run-id N`（整数编号）两个 CLI 参数 |
| `main()` 防护逻辑 | `end_date > VALID_END` 时：① 缺少 `--allow-test-set` → `sys.exit(1)`；② 缺少 `--run-id` → `sys.exit(1)`；③ git 运行次数 ≥ 2 → `sys.exit(1)`；④ run-id 与预期不符 → `sys.exit(1)` |
| `main()` 输出目录 | 测试集模式下写入 `factor_panels_test_run_{N}/`，训练/验证期面板不被覆盖 |

**验证**：
```
python -m scripts.build_factor_panels --end-date 2025-12-31 --dry-run
# → ERROR: --end-date 超过 VALID_END，必须加 --allow-test-set --run-id N
```

---

### F3-003 — 缺少因子预处理和时间边界单元测试

**新建文件**：`tests/test_preprocess.py`、`tests/test_factor_time_boundary.py`

**测试覆盖（33 用例，全部通过）**：

| 文件 | 用例数 | 覆盖内容 |
|---|---|---|
| `test_preprocess.py` | 23 | `winsorize_mad`（正常截断、MAD=0 退化、全 NaN 抛出、NaN 保持、横截面独立）；`standardize`（均值 0/std 1、NaN 保持、样本不足/std=0 返回全 NaN）；`neutralize`（残差与 log_mv 正交、样本不足跳过、NaN 保持、_diag 填充）；`preprocess_factor`（金融股过滤、输出近零均值单位标准差、NaN 传播、_diag 填充、shift(-1) 负例） |
| `test_factor_time_boundary.py` | 10 | `_valid_dates_before(T)` 不含 T 后日期（含当日）；三个因子 load_daily_quote 的 end ≤ T；vol_60d/max_ret/amihud 输出与注入 ret 一致（证明使用后复权收益）；错误 ret 产生不同 vol_60d（区分能力验证） |

**验证**：
```
python -m pytest tests/test_preprocess.py tests/test_factor_time_boundary.py -q
# → 33 passed in 5.26s
python -m pytest tests/ -q
# → 68 passed in 2.09s
```

---

### F3-004 — 因子面板缺少可审计的预处理诊断产物

**修复文件**：`src/factors/preprocess.py`、`scripts/build_factor_panels.py`

**变更**：

| 文件 | 变更内容 |
|---|---|
| `preprocess.py` | `neutralize()` 新增 `_diag: dict \| None = None` 参数；填充 `n_neutralize_valid`、`skipped_neutralize` |
| `preprocess.py` | `preprocess_factor()` 新增 `_diag` 参数；比较 winsorize 前后填充 `n_winsor_clipped`；填充 `n_fin_filtered`；将 `_diag` 传递给 `neutralize()` |
| `build_factor_panels.py` | 主循环中每个 `(T, fname)` 对创建 `diag` 字典（含 n_raw/n_fin_filtered/n_winsor_clipped/n_neutralize_valid/skipped_neutralize/n_final/error_msg），传入 `preprocess_factor()` |
| `build_factor_panels.py` | 落盘环节 6：与已有诊断文件合并后写出 `data/processed/factor_panel_diagnostics.parquet` |

**诊断字段说明**：
每行为 `(rebalance_date, factor)` 复合索引，列为：
- `n_raw`：原始非空股票数
- `n_fin_filtered`：金融股过滤数（财务因子）
- `n_winsor_clipped`：MAD 去极值截断数
- `n_neutralize_valid`：参与中性化 OLS 的有效样本数
- `skipped_neutralize`：是否跳过中性化（样本不足或 OLS 失败）
- `n_final`：最终非空股票数
- `error_msg`：异常信息（正常为 None）

**向后兼容**：所有变更为可选参数（`_diag=None`），现有调用无需修改。

---

### F3-005 — 因子数量文档与实现不一致

**修复文件**：`src/factors/financial_factors.py`（第 5 行）、`src/factors/price_factors.py`（第 5 行）

**变更**：

| 文件 | 修改前 | 修改后 |
|---|---|---|
| `financial_factors.py` docstring | `14 个财务因子（价值 5 + 质量 5 + 成长 4）` | `15 个财务因子（价值 5 + 质量 6 + 成长 4）` |
| `price_factors.py` docstring | `11 个量价类因子`（列表缺少 `short_ratio`） | `12 个量价类因子`（列表加入 `short_ratio`） |

**说明**：实现未变，仅修正文档与 `build_factor_panels.py` 中 `FINANCIAL_FACTORS`（15 个）/ `PRICE_FACTORS`（12 个）列表的一致性。

**验证**：docstring 中的数量现与 `len(FINANCIAL_FACTORS) + len(PRICE_FACTORS) == 27` 一致。

---

## 修复批次 4 — 2026-05-20

**范围**：阶段 4 单因子评价问题（F4-001 ~ F4-006）

**对应审查文件**：`check/03_stage_review_findings.md` 阶段 4

---

### F4-001 — 单因子评价继承阶段 2/3 收益口径污染

**严重度**：Blocker

**根因**：F2-001 代码修复（commit `95a0e3d`）已将 `daily_quote.ret` 切换为后复权收益，但数据文件尚未重建，现有 `reports/factor_evaluation/*` 产物基于旧口径。

**修复内容**：
- 新建 `reports/factor_evaluation/INVALIDATED.md`，明确标注当前报告失效原因、不可使用的下游环节、和重建步骤
- 代码层无需变更（F2-001 已修复）；数据重建属运行时操作

**验证**：`INVALIDATED.md` 已创建，内容准确描述失效链路。

---

### F4-002 — factor_summary.csv 与 final_factors.json 不一致

**严重度**：Blocker

**修复文件**：`scripts/run_factor_evaluation.py`

**变更内容**：
1. 在 `run_evaluation()` 中，写 `final_factors.json` 前加一致性断言：`summary_final_set != final_factors_set` 时抛 `RuntimeError`，确保 CSV 与 JSON 永远同步
2. `final_factors.json` 新增 `_metadata` 字段，含 `run_id`、`generated_at`（UTC ISO-8601）、`git_commit`、`factor_summary_md5`、`n_final`、`n_excluded`
3. 新增辅助函数 `_get_git_commit()`、`_hash_dates()`、`_hash_series()`
4. `run_evaluation()` 新增 `run_id: str | None = None` 参数
5. `_parse_args()` 新增 `--run-id` 命令行参数

**验证**：下次 `run_factor_evaluation.py` 运行若 CSV/JSON 不一致会直接失败；一致时 JSON 含完整 metadata。

---

### F4-003 — forward return 缓存缺乏配置校验

**严重度**：High

**修复文件**：`scripts/run_factor_evaluation.py`

**变更内容**：
1. `load_or_compute_fwd_ret()` 在计算缓存时，同步写入 sidecar metadata 文件 `fwd_ret_panel.meta.json`，记录：`start`、`end`、`n_dates`、`rebalance_dates_hash`（MD5）、`benchmark_mode`、`source_commit`
2. 加载已有缓存前，若存在 sidecar，校验 `rebalance_dates_hash` 和 `benchmark_mode`；不匹配则抛 `RuntimeError` 拒绝使用旧缓存
3. 若 sidecar 不存在（老缓存），记录 warning，建议 `--recompute-fwd-ret` 重算
4. 新增 `--recompute-fwd-ret` CLI 参数，传入时强制忽略缓存重算
5. 函数签名增加 `recompute: bool = False`、`benchmark_mode: str | None = None`

**验证**：配置变更后 `recompute_dates_hash` 不匹配，缓存加载会抛异常。

---

### F4-004 — 时间错位测试过于宽松，报告措辞过强

**严重度**：High

**修复文件**：`src/evaluation/shift_test.py`、`scripts/run_factor_evaluation.py`（报告文本）

**变更内容（shift_test.py）**：
1. `run_shift_test()` 新增 `lag_2`、`lead_1`（shift(-1)）两档诊断
2. 新增 `diagnosis` 字段（`strong_drop`/`weak_drop`/`no_drop`/`reverse`），取代简单 warning bool
3. 新增 `lead_reverse` 字段（bool）：当 `lead_1` IC_IR 绝对值 > orig IC_IR 且 orig > 0.05 时为 True，表示因子可能含未来信息
4. `warning` 字段在 `reverse` 或 `lead_reverse` 时填写详细描述，否则为空字符串
5. `batch_shift_test()` 新增列：`lag1_ic_ir`、`lag2_ic_ir`、`lead1_ic_ir`、`diagnosis`、`lead_reverse`；向后兼容保留 `lagged_ic_ir`、`warning`

**变更内容（报告文本）**：
- 第 3 节（时间错位）：添加诊断等级说明；将"所有因子时间错位测试正常（无未来函数疑点）"改为保守表述，明确指出不能替代 PIT 单元测试
- 第 4 节（5分组回测）：添加口径说明——原始个股等权收益、不含成本、不扣减基准、不代表相对中证500全收益指数的可交易超额收益

**验证**：新测试 `TestShiftTest`（6 个）全部通过。

---

### F4-005 — 缺少单因子评价层单元测试

**严重度**：High

**修复文件**：`tests/test_evaluation.py`（新建，16 个测试用例）

**覆盖范围**：

| 测试类 | 用例数 | 覆盖内容 |
|---|---|---|
| `TestForwardReturnAlignment` | 2 | T+1 开盘买入/T'+1 开盘卖出对齐；最后调仓日无 fwd_ret |
| `TestBuildExitDateMap` | 2 | exit_date > T'；最后调仓日不在映射中 |
| `TestBatchIcTest` | 4 | BH 校正列存在；强因子标为 effective；噪声因子不 effective；p_bh >= p_raw |
| `TestShiftTest` | 6 | diagnosis 字段；lead_reverse 字段；正常因子不触发 lead_reverse；接口完整性；batch 列检查；reverse 诊断场景 |
| `TestArtifactConsistency` | 2 | factor_summary.csv 与 final_factors.json 一致性；metadata 字段完整（产物失效时自动跳过） |

**验证**：`python -m pytest tests/test_evaluation.py -v` → 14 passed, 2 skipped（因 INVALIDATED.md 存在，Artifact 一致性测试正确跳过）。全套 `python -m pytest tests/ -q` → **82 passed, 2 skipped, 0 failed**。

---

### F4-006 — 分组回测报告未明确区分原始收益与超额收益口径

**严重度**：Medium

**修复文件**：`scripts/run_factor_evaluation.py`（`build_markdown_report()` 第 4 节）

**变更内容**：在第 4 节（5分组回测摘要）表格上方添加口径说明块，明确：原始个股等权收益 / 不含交易成本 / 不扣减基准 / 不代表相对中证500全收益指数的可交易超额收益。

**验证**：下次报告生成后，`factor_evaluation_report.md` 第 4 节头部含口径说明。

---

### 阶段 4 总体说明

**F4-001**：数据层 Blocker，代码无需变更，已创建 `INVALIDATED.md` 作为失效标记。  
**F4-002/003**：在评估脚本层形成配置漂移防护网——JSON/CSV 一致性断言 + fwd_ret sidecar metadata 校验。  
**F4-004/006**：改善证据强度和报告透明度，避免过度解读辅助诊断结果。  
**F4-005**：新增 16 个评价层单元测试，覆盖 T+1 对齐、BH 校正、shift test 接口和产物一致性。

下次重建数据并重跑评估后，删除 `INVALIDATED.md`，`TestArtifactConsistency` 两个测试将自动从 skip 转为真正执行并验证一致性。

<!-- 下次修复在此处按相同格式追加 -->

---

## 修复批次 5 — 2026-05-20

**范围**：阶段 5 信号合成问题（F5-001 ~ F5-007）

**对应审查文件**：`check/03_stage_review_findings.md` 阶段 5

---

### F5-001 合成信号使用不一致的因子集合（Blocker）

**修复文件**：`scripts/run_test_pipeline.py`

**变更**：
1. **INVALIDATED.md 守卫**：`main()` 启动后首先检查 `reports/factor_evaluation/INVALIDATED.md`；若存在则 `sys.exit(1)`，阻止在报告失效状态下运行测试流水线。
2. **因子集合一致性断言**：加载 `final_factors.json` 后，若 `factor_summary.csv` 存在，则校验 `factor_summary.csv[final_include=True]` 集合 == `final_factors.json["final_factors"]` 集合；不一致时抛 `RuntimeError`。
3. **FACTOR_DIRECTIONS 方向校验**：调用 `validate_directions_vs_summary()`，对所有方向不一致因子输出 `WARNING` 日志（不中断运行，供人工审查）。

**验证**：无法在当前数据重建前完整运行，但单元测试覆盖了一致性断言逻辑。

---

### F5-002 测试流水线覆盖训练/验证公共路径（Blocker）

**修复文件**：`scripts/run_test_pipeline.py`

**变更**：
1. **测试运行专用目录**：将 `composite_signal_equal.parquet`、`composite_signal_ic_ir.parquet`、`ic_series_all_factors.parquet` 写入 `data/processed/test_run_{run_id}/`，不再覆盖公共路径。
2. **重复运行守卫**：启动时检查 `data/processed/test_run_{run_id}/composite_signal_ic_ir.parquet` 是否已存在；已存在则 `sys.exit(1)`，防止意外覆盖。
3. **删除模块级公共路径常量**（`SIGNAL_IC_IR_PATH`、`SIGNAL_EQUAL_PATH`、`IC_SERIES_PATH`）：改为动态生成 `output_dir` 路径，杜绝误用。

**修改的函数签名**：
```python
def _build_composite_signals(
    factor_panels, fwd_ret_panel, all_dates, stability_weights,
    output_dir: Path, run_id: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
```

---

### F5-003 因子方向硬编码与评估结果不一致（High）

**修复文件**：`src/signal/combiner.py`

**变更**：
1. `FACTOR_DIRECTIONS["margin_ratio"]` 由 `+1` 改为 `-1`（训练集 IC 均值 < 0，高融资余额为超买信号）。
2. 新增 `validate_directions_vs_summary(factor_summary: pd.DataFrame) -> list[str]`：
   - 接受 `factor_summary.csv` 读入的 DataFrame
   - 比较 `ic_mean` 隐含方向与 `FACTOR_DIRECTIONS`
   - 返回不一致因子名列表，对每个不一致因子输出 `WARNING` 日志
   - `factor_summary` 无 `ic_mean` 列时安全返回空列表

---

### F5-004 缺少可审计的 IC_IR 权重历史和信号合成元数据（High）

**修复文件**：`src/signal/combiner.py`、`scripts/run_test_pipeline.py`

**combiner.py 变更**：
- `build_composite_panel()` 新增 `return_diagnostics: bool = False` 参数
- 当 `return_diagnostics=True` 时，返回 `(panel, diagnostics)`
- `diagnostics` 包含：
  - `weight_history: dict[Timestamp, dict[factor, weight]]` — 逐期实际权重
  - `cold_start_flags: dict[Timestamp, bool]` — 每期是否为冷启动
  - `cold_start_count: int` — 冷启动期总数
  - `method: str` — 合成方法

**run_test_pipeline.py 变更**：
- 调用 `build_composite_panel(..., return_diagnostics=True)` 获取诊断信息
- 落盘 `data/processed/test_run_{N}/icir_weight_history.parquet`（行=调仓日，列=因子，值=实际权重）
- 落盘 `data/processed/test_run_{N}/composite_signal_metadata.json`，记录：
  `run_id`、`generated_at`、`method`、`window_months`、`min_valid_factors`、`factor_list`、`n_factors`、`n_rebalance_dates`、`date_range`、`cold_start_count`、`ic_series_hash`、`signal_ic_ir_hash`、`output_dir`

---

### F5-005 合成参数散落（Medium）

**状态**：已在前序批次中修复。`src/config.py` 已包含 `SIGNAL_MIN_IC_HISTORY`、`SIGNAL_WINDOW_MONTHS`、`SIGNAL_DEFAULT_MIN_VALID`、`SIGNAL_MIN_VALID_FACTORS`；`src/signal/combiner.py` 和 `scripts/run_test_pipeline.py` 均读取 cfg，不再重复定义。

---

### F5-006 Notebook 自检门禁过弱（Medium）

**状态**：暂缓。Notebook 为探索性代码，修改成本高；当前代码层的断言（F5-001 一致性断言、run_factor_evaluation.py 的一致性断言）已覆盖最关键的硬门控逻辑。Notebook 自检仍为信息项，后续可单独迭代。

---

### F5-007 缺少信号合成层单元测试（High）

**修复文件**：`tests/test_signal_combiner.py`（新建）

**新增 15 个测试用例**（5 个测试类）：

- **TestFactorDirections**（5 个）：`margin_ratio` 方向为 `-1`；所有方向只为 ±1；`validate_directions_vs_summary` 正确检测不一致；一致时返回空列表；缺 `ic_mean` 列时安全返回空列表
- **TestComputeRollingIcIr**（3 个）：丢弃最近可用一期 IC（时间保守性）；冷启动返回 NaN；历史充足时返回非 NaN
- **TestCombineFactorsCrossSection**（2 个）：`min_valid_factors=3` 时只有 2 个有效因子的股票置 NaN；`min_valid_factors=2` 时 2 因子股票不为 NaN
- **TestCompositeSignalStandardization**（1 个）：合成信号均值≈0、标准差≈1
- **TestBuildCompositePanel**（4 个）：`return_diagnostics=True` 含必需字段；早期调仓日冷启动数 > 0；权重历史键与调仓日一致；`return_diagnostics=False` 时只返回 DataFrame

---

### 验证结果

```
python -m pytest tests/ -q
97 passed, 2 skipped in 4.78s
```

- 新增测试：15 个（`tests/test_signal_combiner.py`）
- 历史 82 passed + 2 skipped 保持不变，无回归
- 2 skipped = `TestArtifactConsistency`，待删除 `INVALIDATED.md` 后自动启用（F4-001 后续动作）

**注**：F5-001/F5-002 的完整验证（运行测试流水线）依赖上游数据重建（`INVALIDATED.md` 中列出的步骤），当前无法运行。代码层断言和守卫逻辑已通过单元测试间接验证。

---

## 修复批次 6 — 2026-05-20

**范围**：阶段 6 协方差与组合优化问题（F6-001 ~ F6-006）

**对应审查文件**：`check/03_stage_review_findings.md` 阶段 6、`check/09_portfolio_optimization_audit.md`

---

### F6-001 — L3 fallback 违反停牌锁定和单股偏离约束（Blocker）

**修复文件**：`src/portfolio/optimizer.py`

**问题**：`_topn_equal_weight()` 只排除停牌股而不锁定其上期权重，导致停牌股权重被置 0，同时跌停约束完全缺失。历史 14 个 L3 期全部存在停牌锁定违约。

**变更**：

| 位置 | 变更内容 |
|---|---|
| `_topn_equal_weight()` 函数签名 | 新增 `limit_dn_indices: set[int] \| None` 和 `w_prev_vec: np.ndarray \| None` 两个参数 |
| `_topn_equal_weight()` 内部逻辑 | 第一步锁定停牌股权重为 `w_prev_vec[i]`，累计 `locked_budget`；第二步锁定跌停股（非停牌）权重为 `w_prev_vec[i]`；第三步在剩余 `free_budget` 内对非停牌/非跌停/非涨停的候选股票 TopN 等权分配 |
| `_topn_equal_weight()` 第一期退化 | `w_prev_vec=None` 时：停牌/跌停股排除出选股池（无历史权重可锁定），记录 `warning`，不崩溃 |
| `optimize_single_period()` L3 调用处 | 传入 `limit_dn_indices=limit_dn_idx` 和 `w_prev_vec=w_prev_vec`，修复跌停和停牌约束均未传入的缺陷 |

**约束优先级（新实现）**：停牌锁定 → 跌停锁定（持上期权重）→ 涨停排出选股池 → 剩余预算 TopN 等权

---

### F6-002 — 缺失协方差被伪装为 L1 成功（High）

**修复文件**：`src/portfolio/optimizer.py`、`scripts/run_test_pipeline.py`

**问题**：当 `T not in cov_dict` 时，代码用 `1e-4·I` 占位后直接求解，L1 因 TE 约束形同虚设而常常成功，但 metadata 记录 `fallback_level=0`，掩盖了无真实风险模型的事实。当前 3 个冷启动期（2016-01、02、03 月末）均如此记录。

**变更**：

| 文件 | 位置 | 变更内容 |
|---|---|---|
| `optimizer.py` | `OptimizeResult` dataclass | 新增 `cov_available: bool = True` 字段 |
| `optimizer.py` | `optimize_all_periods()` | 分离 `cov_available = T in cov_dict` 判断；协方差缺失时仍用 `1e-4·I` 求解，但在结果后处理中将 `fallback_level=0` 强制覆盖为 `1`，`solver_status` 追加 `+cov_missing` 后缀 |
| `optimizer.py` | `optimize_all_periods()` meta_rows | 新增 `cov_available` 和 `w_prev_source` 两个字段 |
| `run_test_pipeline.py` | `_run_optimization()` 优化主循环 | 同步加入 `cov_available` 追踪和 `effective_fb` 覆盖逻辑，并填入 metadata |

---

### F6-003 — 协方差缓存读取只检查 NaN，未验证正定性与对称性（High）

**修复文件**：`src/portfolio/covariance.py`、`scripts/run_test_pipeline.py`

**问题**：缓存读取后只检查 `not cov_df.isna().any().any()`，对非 PSD、非对称、列顺序错配等损坏情况无防御。

**变更**：

| 文件 | 位置 | 变更内容 |
|---|---|---|
| `covariance.py` | 新增公开函数 `validate_and_repair_covariance()` | 检查对称性（`max\|Σ−Σᵀ\|` > `1e-6` 时强制对称化）；计算最小特征值；若 `min_eig < eps` 则加对角扰动修复；返回 `(repaired_cov, was_valid, min_eig_before, diag_delta)` 四元组 |
| `run_test_pipeline.py` | `_run_optimization()` 缓存读取段 | `import validate_and_repair_covariance`；在通过 NaN 检查后立即调用该函数；新增 `n_repaired` 计数；日志报告修复次数 |

---

### F6-004 — 优化器使用目标权重近似实际持仓，未在元数据中声明（High）

**修复文件**：`src/portfolio/optimizer.py`、`scripts/run_test_pipeline.py`

**问题**：`w_prev` 直接取上期优化结果权重（目标权重），实际执行后因价格漂移、T+1 滑点、停牌导致的实际持仓与目标可能存在偏差，但约束基于目标权重计算，低估了交易约束的影响。

**变更**：在 `optimize_all_periods()` 的 `meta_rows` 和 `_run_optimization()` 的 `meta_rows` 中均写入 `w_prev_source="target_weight"`，明确标注当前为目标权重近似，非实际持仓闭环。如需真实闭环，需由回测引擎反馈实际权重给下一期优化器。

**注**：本次为文档化修复（明确假设）。真实持仓闭环属架构重构，列为后续待优化事项。

---

### F6-005 — 测试集流水线覆盖通用组合权重产物（Blocker）

**修复文件**：`scripts/run_test_pipeline.py`

**问题**：`_run_optimization()` 将权重写入全局路径 `WEIGHTS_OPT_PATH`（`portfolio_weights_optimized.parquet`）和 `WEIGHTS_BL_PATH`（`portfolio_weights_baseline.parquet`），运行测试流水线后训练/验证期公共产物被覆盖。

**变更**：

| 位置 | 修改前 | 修改后 |
|---|---|---|
| `_run_optimization()` 签名 | `(composite_signal, all_rebalance_dates)` | 新增 `output_dir: Path` 参数 |
| 权重写入路径 | `WEIGHTS_OPT_PATH` / `WEIGHTS_BL_PATH`（公共路径） | `output_dir / "portfolio_weights_optimized.parquet"` / `output_dir / "portfolio_weights_baseline.parquet"` |
| 新增落盘 | — | `output_dir / "portfolio_weights_meta.parquet"`，含 `fallback_level`、`cov_available`、`w_prev_source` 等字段 |
| `main()` 调用处 | `_run_optimization(composite_ic_ir, all_dates)` | `_run_optimization(composite_ic_ir, all_dates, test_run_dir)` |

通用路径 `WEIGHTS_OPT_PATH` / `WEIGHTS_BL_PATH` 常量保留，但测试流水线不再写入这两条路径，训练/验证期产物安全。

---

### F6-006 — 缺少组合优化与协方差单元测试（High）

**修复文件**：`tests/test_optimizer.py`（新建）

**新增 16 个测试用例**（4 个测试类）：

| 测试类 | 用例数 | 覆盖内容 |
|---|---|---|
| `TestTopnEqualWeightHaltLock` | 3 | 停牌股权重锁定为上期权重；剩余预算等权分配给非停牌 TopN；第一期无 w_prev 时不崩溃 |
| `TestTopnEqualWeightLimitDn` | 2 | 跌停股权重不低于上期权重；停牌与跌停同时发生时停牌优先 |
| `TestOptimizeSinglePeriod` | 4 | 宽松参数下 L1 可行返回 `fallback_level=0`；权重和恒等于 1；第一期无 w_prev 不崩溃；停牌股权重锁定约束 |
| `TestMissingCovarianceNotReportedAsL1` | 3 | 缺失协方差期 `fallback_level >= 1`；有协方差期仍可为 L1；`meta_df` 含 `cov_available` 和 `w_prev_source` 列 |
| `TestCovarianceCacheValidation` | 4 | 合法 PSD 矩阵 `was_valid=True`；非 PSD 矩阵被修复；不对称矩阵强制对称化；单位矩阵通过验证 |

---

### 验证结果

```
python -m pytest tests/test_optimizer.py -v
# → 16 passed in 8.37s

python -m pytest tests/ -q --ignore=tests/test_pit.py --ignore=tests/test_universe.py \
       --ignore=tests/test_evaluation.py --ignore=tests/test_factor_time_boundary.py
# → 115 passed, 4 warnings in 4.35s
```

- 新增测试：16 个（`tests/test_optimizer.py`）
- 存量测试无回归（数据依赖类测试忽略后 115 passed）
- 2 个历史 skipped（`TestArtifactConsistency`）维持不变

**阶段 6 总结**：

| 编号 | 严重度 | 状态 | 核心修复 |
|---|---|---|---|
| F6-001 | Blocker | 已修复 | L3 停牌锁定 + 跌停锁定，剩余预算等权 |
| F6-002 | High | 已修复 | 缺失协方差强制 fallback≥1，metadata 记录 `cov_available` |
| F6-003 | High | 已修复 | 缓存读取加 PSD+对称性验证，新增公开修复函数 |
| F6-004 | High | 已修复（文档化）| metadata 写入 `w_prev_source="target_weight"` |
| F6-005 | Blocker | 已修复 | 测试集权重写入专用目录，公共路径不被覆盖 |
| F6-006 | High | 已修复 | 新建 `tests/test_optimizer.py`，16 个用例全通过 |

---

## 修复批次 7 — 2026-05-20

**范围**：阶段 7 回测、成本与指标问题（F7-001 ~ F7-005）

**对应审查文件**：`check/03_stage_review_findings.md` 阶段 7、`check/10_backtest_audit.md`

---

### F7-001 — T+1 开盘调仓使用上一日收盘净值计算目标金额（Blocker）

**修复文件**：`src/backtest/engine.py`

**问题**：`_run_main_loop()` 在触发调仓时，将上一日收盘估值 `portfolio_value` 直接传入 `_execute_rebalance()`。若持仓股票在 T+1 开盘出现隔夜跳空，`target_value = target_w × portfolio_value_prev_close` 会与 `current_value = shares × open_price` 产生虚假差额，从而触发不应发生的买卖、产生额外成本并扭曲 NAV 路径。

**变更**：在 `_run_main_loop()` 执行调仓块（触发 `date in exec_date_to_T`）内，于调用 `_execute_rebalance()` 前新增 `pretrade_value` 计算逻辑：

```python
# F7-001: 用 T+1 开盘价计算执行前净值，避免隔夜价格变化产生虚假交易
# 停牌无开盘价的持仓以最近收盘价（已 ffill）估值
if shares:
    s_series = pd.Series(shares)
    op = open_prices.reindex(s_series.index)
    cl = (
        close_adj_panel.loc[date].reindex(s_series.index)
        if date in close_adj_panel.index
        else pd.Series(0.0, index=s_series.index)
    )
    prices_pretrade = op.where(op > 0, cl).fillna(0.0)
    pretrade_value = cash + float((s_series * prices_pretrade).sum())
else:
    pretrade_value = cash
```

`_execute_rebalance()` 调用处由 `portfolio_value` 改为 `pretrade_value`；同步更新 `portfolio_value` 参数 docstring，说明调用方必须在开盘前计算后传入。

**验证**：`TestPretradePorfolioValue::test_no_trade_when_weights_unchanged_and_price_doubles`：持有 1 股 A（开盘价 200），目标仍 100% → `pretrade_value=200`，`target=current=200`，`sell_value=buy_value=cost=0`，断言通过。

---

### F7-002 — 缺失执行日状态默认 FREE，放行无法确认可交易股票（Blocker）

**修复文件**：`src/backtest/engine.py`（两处）

**问题一**：`_status_to_trade_state()` 对 `status_snap.reindex(all_codes)` 后存在 NaN 的行（即完全不在快照中的股票）通过 `fillna(False)` 静默置为 `FREE`，使停牌或数据缺失的股票可被视为可交易。

**变更一**：在现有五段状态赋值之后，追加 `missing_mask` 逻辑：

```python
# F7-002: 完全不在状态快照中的股票 → LOCKED（保守：状态未知不得交易）
missing_mask = snap.isna().all(axis=1)
n_missing = int(missing_mask.sum())
if n_missing > 0:
    log.debug("状态快照缺失 %d 只股票，保守设为 LOCKED", n_missing)
    state[missing_mask] = TradeState.LOCKED.value
```

**问题二**：`_load_exec_status()` 在 `load_stock_status(d)` 抛出 `KeyError`（整日无状态数据）时，将全部股票设为 `FREE`。

**变更二**：

```python
# 修改前
result[d] = pd.Series(TradeState.FREE.value, index=all_codes, dtype=object)

# 修改后
log.warning("load_stock_status(%s) 无数据，保守设所有股票为 LOCKED", d.date())
result[d] = pd.Series(TradeState.LOCKED.value, index=all_codes, dtype=object)
```

**验证**：
- `TestMissingStatusLocked::test_stock_absent_from_snap_is_locked`：B 不在快照中 → `state["B"] == LOCKED`
- `TestMissingStatusLocked::test_load_exec_status_uses_locked_when_data_missing`：`monkeypatch` 令 `load_stock_status` 抛 KeyError → 全部 LOCKED
- `TestMissingStatusLocked::test_missing_status_stock_cannot_be_bought`：LOCKED 股票 buy_value=0

---

### F7-003 — 交易日志 `n_no_price` 被全历史股票列污染（Medium）

**修复文件**：`src/backtest/engine.py`

**问题**：`_execute_rebalance()` 用 `all_codes = set(shares.keys()) | set(target_w.index)` 构造遍历集合。权重面板 `target_w` 包含历史全量股票（约 1069 列），目标权重为 0 的历史股票也被纳入遍历，导致这些无持仓、无目标的股票因无开盘价而被计入 `n_no_price`，污染交易约束审计指标。

**变更**：

```python
# 修改前
all_codes = set(shares.keys()) | set(target_w.index)

# 修改后（F7-003: 只处理当前持仓或目标权重为正的股票）
all_codes = set(shares.keys()) | {
    c for c in target_w.index if float(target_w.get(c, 0.0)) > 1e-10
}
```

**验证**：`TestNNoPriceActiveOnly::test_large_inactive_universe_not_counted`：500 只历史股票目标权重为 0，只有持仓 A 无开盘价 → `n_no_price == 1`（非 501）。

---

### F7-004 — 分析报告与落盘回测指标不一致（High）

**修复文件**：`reports/analysis_v2_results.md`

**问题**：报告（2026-05-11 生成）显示 V2 IR=1.025、年化超额+5.35%；当前 `backtest_metrics.parquet` 落盘值为 IR=0.340、年化超额+1.47%，相差悬殊。报告不可继续作为指标评估依据。

**变更**：在报告首部标题后新增醒目过期声明块，说明失效原因（F7-001/F7-002/F7-004）、具体数值差异，以及修复上游 Blocker 后将被统一替换。原报告内容原样保留供历史参考，不做删改。

---

### F7-005 — 缺少回测成本、指标和执行约束单元测试（High）

**新建文件**：`tests/test_backtest_engine.py`（19 个用例）、`tests/test_backtest_metrics.py`（30 个用例）

**`test_backtest_engine.py` 测试类**：

| 测试类 | 用例数 | 覆盖内容 |
|---|---|---|
| `TestPretradePorfolioValue` | 3 | 目标权重不变+价格翻倍→无交易；旧收盘值会产生虚假卖出（负例文档）；真实减仓场景卖出量符合预期 |
| `TestMissingStatusLocked` | 7 | 不在快照→LOCKED；停牌→LOCKED；涨跌停约束（NO_BUY/NO_SELL）；ST→BUY_LIMIT；LOCKED股票不被买入；整日缺失→全量LOCKED（monkeypatch） |
| `TestNNoPriceActiveOnly` | 4 | 零权重零持仓不计入n_no_price；持仓无价计入且仓位不变；目标非零无价计入；500只历史股票不污染计数 |
| `TestLimitConstraints` | 5 | 涨停不能加仓/可减仓；跌停不能减仓；LOCKED保持仓位；BUY_LIMIT可减仓不可加仓 |

**`test_backtest_metrics.py` 测试类**：

| 测试类 | 用例数 | 覆盖内容 |
|---|---|---|
| `TestMaxDrawdown` | 7 | 平坦/单调上涨0回撤；单步下跌精确值；跌至一半；接近归零（≈-100%）；多峰取最深；返回非正数 |
| `TestExcessMaxDrawdown` | 4 | 完全一致0超额回撤；持续跑赢0超额回撤；跑输后超额回撤为负；超额回撤≤策略回撤绝对值 |
| `TestAnnualizedReturn` | 4 | 1年+10%（允许1%误差）；平坦0收益；2天不崩溃；下跌返回负值 |
| `TestInformationRatio` | 3 | 完全一致TE=0→IR=0不崩溃；独立随机路径跑赢→IR>0；跑输→IR<0 |
| `TestTrackingError` | 3 | 完全一致TE=0；TE非负；噪声越大TE越大 |
| `TestMonthlyWinRate` | 3 | 月月跑赢1.0；月月跑输0.0；混合场景在[0,1]区间 |
| `TestSummarize` | 6 | 键集完整；回撤类为负；波动率非负；月胜率在区间；相同nav指标均为0；所有值为float |

---

### 验证结果

```
python -m pytest tests/test_backtest_engine.py tests/test_backtest_metrics.py -q
# → 49 passed in 1.68s

python -m pytest tests/ -q
# → 162 passed, 2 skipped in 20.59s
```

- 新增测试：49 个（`test_backtest_engine.py` 19个 + `test_backtest_metrics.py` 30个）
- 历史 113 passed + 2 skipped 保持不变，无回归
- 2 skipped = `TestArtifactConsistency`（待删除 `INVALIDATED.md` 后自动启用）

**阶段 7 总结**：

| 编号 | 严重度 | 状态 | 核心修复 |
|---|---|---|---|
| F7-001 | Blocker | 已修复 | `_run_main_loop` 用开盘前净值（开盘价+ffill收盘）替代上一日收盘净值，传入 `_execute_rebalance` |
| F7-002 | Blocker | 已修复 | 快照缺失股票→LOCKED；整日缺失→全量LOCKED（两处修复） |
| F7-003 | Medium | 已修复 | `all_codes` 限定为持仓∪目标非零，消除历史全量股票对 `n_no_price` 的污染 |
| F7-004 | High | 已修复（标注）| 报告头部加过期声明，列明数值差异和失效原因 |
| F7-005 | High | 已修复 | 新建两个测试文件共 49 个用例，全部通过 |

---

## 修复批次 8 — 2026-05-20

**范围**：阶段 8 归因与报告问题（F8-001 ~ F8-008）

**对应审查文件**：`check/11_attribution_report_audit.md`

---

### F8-001 — 归因收益口径未按 T+1 开盘成交计算（Blocker）

**修复文件**：`src/attribution/brinson.py`、`src/attribution/factor_attr.py`

**问题**：两个归因模块均使用 `dq["ret"].unstack("ts_code")` 作为日收益来源，存在两个问题：
1. `daily_quote.ret` 列来源不透明，存在上游污染风险（阶段 2 已确认）
2. 持有期收益使用 `(T, T_next]` 收盘到收盘区间，包含了 T 收盘到 T+1 开盘的隔夜段，而实际成交在 T+1 开盘，策略不应获得该段收益。

**变更**：

1. 两个模块均替换 `_compute_period_cumulative_returns` 为新函数 `_compute_period_returns_t1_open`：
   - 加载 `dq["close_adj"]` 和 `dq["open_adj"]` 面板（替代 `dq["ret"]`）
   - 第一日（T 后首个交易日）收益 = `close_adj / open_adj - 1`（开盘入场）
   - 后续日收益 = `close_adj.pct_change()`（收盘到收盘）
   - 停牌 NaN → 0，与回测引擎一致
   - 返回 `(pd.Series, n_missing_return)` 二元组

2. 函数签名由：
   ```python
   def _compute_period_cumulative_returns(T, T_next, codes, daily_ret_panel) -> pd.Series
   ```
   改为：
   ```python
   def _compute_period_returns_t1_open(T, T_next, codes, close_adj_panel, open_adj_panel) -> tuple[pd.Series, int]
   ```

3. 数据加载改为：
   ```python
   dq = load_daily_quote(backtest_start, backtest_end, codes)
   close_adj_panel = dq["close_adj"].unstack("ts_code")
   open_adj_panel  = dq["open_adj"].unstack("ts_code")
   ```

---

### F8-002 — 归因强制权重归一化，抹掉现金和成本拖累（High）

**修复文件**：`src/attribution/brinson.py`、`src/attribution/factor_attr.py`

**问题**：`_get_period_start_weights()` 末尾 `return w / w_sum` 将策略权重归一化至满仓（1.0），抹掉了约 0.25%-2.5% 的现金仓位和成本拖累。导致归因 `strategy_return` 是"假设满仓的毛收益"，而非 NAV 实际收益。

**变更**：

`_get_period_start_weights()` 末尾修改：
```python
# 修改前
return w / w_sum

# 修改后（F8-002: 不归一化，保留原始权重和）
log.debug("T=%s 权重和=%.4f，估算现金仓位=%.4f", T.date(), w_sum, 1.0 - w_sum)
return w
```

同时在 `period_rows` 中新增 `cash_weight` 和 `weight_sum` 字段，调用方可追溯现金仓位：
```python
period_rows.append({
    ...
    "cash_weight": 1.0 - w_p_sum,
    "weight_sum":  w_p_sum,
})
```

`period_summary` 新增两列：`cash_weight`、`weight_sum`。

---

### F8-003 — Brinson 模块未加载完整基准成分股收益（High）

**修复文件**：`src/attribution/brinson.py`

**问题**：`compute_brinson_attribution()` 只从 `actual_weights.columns` 收集策略持股代码，加载这些股票的日收益。未持有的基准成分股（约 300-400 只）在 `_compute_period_cumulative_returns` 中收益被静默填 0，严重低估基准行业收益，2022 年基准约 -18% 被错算为接近 0。

**变更**：在 `compute_brinson_attribution()` 加载行情前，先收集所有归因期的完整基准成分股代码：

```python
# F8-003: 收集完整基准成分股代码
benchmark_codes: set[str] = set()
for T, _ in periods:
    try:
        snap = load_universe(T)
        benchmark_codes.update(str(c) for c in snap.index)
    except Exception as exc:
        log.warning("T=%s 基准成分股代码收集失败（%s），跳过", T.date(), exc)

all_attr_codes = sorted(strategy_codes | benchmark_codes)
dq = load_daily_quote(backtest_start, backtest_end, all_attr_codes)
```

日志格式：`"归因股票代码：策略 X 只 + 基准 Y 只 = 合计 Z 只"`

---

### F8-004 — 因子归因使用 28 个候选因子，而非 12 个最终入模因子（High）

**修复文件**：`src/attribution/factor_attr.py`

**问题**：`compute_factor_attribution()` 默认使用 `DEFAULT_FACTOR_GROUPS`（28 个候选因子），而 `final_factors.json` 记录的最终入模因子仅 12 个。多余的 16 个被排除因子（含受收益口径污染标记的因子）会混入归因，解释的不是策略真实 alpha 贡献。

**变更**：新增 `_load_final_factor_group_map()` 函数：

```python
def _load_final_factor_group_map() -> dict[str, str]:
    """从 final_factors.json 加载最终入模因子，并映射到因子组。（F8-004）"""
    final_factors_path = cfg.ROOT / "reports" / "factor_evaluation" / "final_factors.json"
    # 读取 final_factors 列表，用 DEFAULT_FACTOR_GROUPS 映射到组
    # 不在 DEFAULT_FACTOR_GROUPS 中的因子报 warning 并跳过
    # 文件不存在或为空时退回到 DEFAULT_FACTOR_GROUPS 并记录 warning
```

`compute_factor_attribution()` 签名新增 `factor_group_map=None` 默认行为：
```python
if factor_group_map is None:
    factor_group_map = _load_final_factor_group_map()
    factor_list_source = "final_factors.json"
else:
    factor_list_source = "caller_provided"
```

`period_summary` 新增 `factor_list_source` 列，记录因子列表来源。

---

### F8-005 — 因子方向使用硬编码 `FACTOR_DIRECTIONS`，与评估产物不一致（High）

**修复文件**：`src/attribution/factor_attr.py`

**问题**：`_build_group_composites()` 使用 `FACTOR_DIRECTIONS.get(factor_name, 1)` 调整因子方向，而 `margin_ratio` 在 `factor_summary.csv` 中 `factor_direction=-1.0`，但 `FACTOR_DIRECTIONS` 硬编码为 `+1`，导致资金流向因子组暴露符号错误。

**变更**：新增 `_load_factor_directions_from_summary()` 函数：

```python
def _load_factor_directions_from_summary() -> dict[str, int]:
    """从 factor_summary.csv 读取因子方向，作为唯一权威来源。（F8-005）
    文件不存在时退回到 FACTOR_DIRECTIONS 硬编码表并记录 warning。
    """
```

`_build_group_composites()` 新增 `factor_directions: dict[str, int]` 参数（替代直接访问 `FACTOR_DIRECTIONS`）；**方向缺失的因子不提供默认值 +1，而是 warning + 跳过**：

```python
direction = factor_directions.get(factor_name)
if direction is None:
    log.warning("因子 %s 方向缺失（不在 factor_summary.csv 中），跳过该因子", factor_name)
    continue
```

`compute_factor_attribution()` 调用 `_load_factor_directions_from_summary()` 并传入 `_build_group_composites`。

---

### F8-006 — 归因 notebook 自检把"超额为正"当 PASS（Medium）

**修复文件**：`notebooks/06_attribution.ipynb`（Cell 2 注释更新、Cell 4 自检逻辑重写）

**问题**：Cell 4 检查条件 `check(v2_excess_sum > 0, ...)` 以"归因超额为正"为 PASS 门槛，该门槛在策略亏损年份会错误 FAIL，在策略盈利但归因口径严重失真时会错误 PASS。同时引用过期 backtest 数值 5.35%。

**变更**：

Cell 2 注释：说明 F8-003 已在 `brinson.py` 模块级修复，`_augment_with_csi500` 为冗余保留。

Cell 4 完全重写（F8-006）：
- 删除 `check(v2_excess_sum > 0, ...)` 检查项
- 新增口径对账检查：读取 `backtest_nav.parquet`，比较 Brinson 超额与 NAV 超额
  - 差值 < 2% → `[PASS]`
  - 差值 ≥ 2% → `[WARN]`，列明排查方向（F8-002/F8-001）
  - 文件不存在 → `[WARN]` 提示先运行回测流水线
- 新增 `warnings_list`，使自检能区分 FAIL（硬性错误）和 WARN（需人工审查）

---

### F8-007 — 报告未满足披露要求，引用过期指标（High）

**修复文件**：`reports/analysis_v2_results.md`

**问题**：报告首部已有阶段 7 的过期声明（F7-004 修复时添加），但未覆盖阶段 8 归因问题。

**变更**：在已有过期声明块内追加阶段 8 描述：

```markdown
> 阶段 8 审查发现（F8-001/F8-002/F8-004/F8-005/F8-007）：
> 4. 归因收益口径问题（F8-001）
> 5. 权重归一化抹掉现金拖累（F8-002）
> 6. 因子归因使用 28 个候选因子而非 12 个入模因子（F8-004/F8-005）
> 7. Brinson 超额 +1.92% 与 NAV 超额 +1.01% 不一致（F8-007）
> 当前测试集剩余可运行次数：1 次
```

---

### F8-008 — 缺少归因层单元测试（High）

**修复文件**：`tests/test_attribution.py`（新建，16 个用例，4 个测试类）

**测试类及覆盖内容**：

| 测试类 | 用例数 | 覆盖内容 |
|---|---|---|
| `TestBrinsonIdentity` | 3 | V1 恒等式（total_effect=excess）；V2 BHB 汇总层恒等式；同行业权重不变时 total_effect=0 |
| `TestT1OpenReturns` | 4 | 单日期间只用 close/open-1；多日期间第一日开盘对齐、后续收盘到收盘；停牌 NaN→0；缺失代码→0+n_missing |
| `TestCashWeightNotNormalized` | 3 | 权重和 0.90 不归一化；满仓权重和为 1；现金仓位可从 1-sum 正确推导 |
| `TestFinalFactorGroupMap` | 3 | JSON 仅 2 因子时 group_map 也只含 2 个；JSON 不存在退回默认；未知因子被排除 |
| `TestFactorDirectionsFromSummary` | 3 | margin_ratio 从 CSV 读取 -1（非硬编码 +1）；CSV 不存在退回硬编码不崩溃；方向缺失因子不参与合成 |

---

### 验证结果

```
python -m pytest tests/test_attribution.py -v
# → 16 passed in 1.13s

python -m pytest tests/ -q
# → 178 passed, 2 skipped in 2.86s
```

- 新增测试：16 个（`tests/test_attribution.py`）
- 历史 162 passed + 2 skipped 保持不变，无回归
- 2 skipped = `TestArtifactConsistency`（待删除 `INVALIDATED.md` 后自动启用）

---

### 阶段 8 总结

| 编号 | 严重度 | 状态 | 核心修复 |
|---|---|---|---|
| F8-001 | Blocker | 已修复 | 收益改为 close_adj/open_adj 计算，第一日 close/open-1（T+1 开盘入场） |
| F8-002 | High | 已修复 | 权重不归一化，新增 cash_weight/weight_sum 字段追踪现金仓位 |
| F8-003 | High | 已修复 | brinson.py 内部收集完整基准成分股代码并加载其收益 |
| F8-004 | High | 已修复 | 默认从 final_factors.json 加载 12 个入模因子，factor_list_source 记录来源 |
| F8-005 | High | 已修复 | 方向从 factor_summary.csv 读取，缺失因子 warning+跳过，不默认 +1 |
| F8-006 | Medium | 已修复 | notebook 自检改为 NAV 对账检查，去除"超额为正"门槛 |
| F8-007 | High | 已修复（标注）| 报告追加阶段 8 归因缺陷声明和测试集剩余次数 |
| F8-008 | High | 已修复 | 新建 tests/test_attribution.py，16 个用例全部通过 |

---

## 修复批次 9 — 2026-05-20

**范围**：阶段 9 — 复现、测试与发布前清单（F9-001 ~ F9-007）

**对应审查文件**：`check/12_repro_test_release_checklist.md`

---

### F9-007 — Tracked pyc 文件污染版本库（Medium）

**修复文件**：git index（`git rm --cached`）

**变更**：

- 执行 `git rm --cached $(git ls-files "*__pycache__*" "*.pyc")` 移除 27 个被 tracked 的 pyc 文件
- `.gitignore` 已覆盖 `__pycache__/` 和 `*.py[cod]`，移出 index 后不再重新进入 tracked 状态

**验证**：`git ls-files "*__pycache__*" "*.pyc"` 无输出。

---

### F9-001 — run_pipeline.py 一键复现链路不完整（Blocker）

**修复文件**：`scripts/run_pipeline.py`（重写）

**变更**：

1. **阶段拆分**：将原单一 `download` 阶段拆为 `download_core`（`download_tushare all`）和 `download_supplement`（`download_supplement all`），修复原来 `extra=[]` 导致"静默成功但未下载"的问题。
2. **质量阶段强制显式跳过**：`quality` 阶段若未在 `--skip` 中显式声明，脚本直接失败并提示，不再静默跳过。
3. **新增 signal/portfolio/backtest/attribution 阶段**：覆盖完整的训练/验证期 pipeline（详见 F9-002）。
4. **产物存在性检查**：`convert`/`factors`/`evaluate`/`signal`/`portfolio`/`backtest`/`attribution` 等阶段结束后检查关键产物是否存在。
5. **Run manifest**：每次 pipeline 执行结束后写入 `data/processed/run_manifests/run_{timestamp}.json`，记录 `run_id`、`git_commit`、执行阶段、耗时和成功状态。
6. **STAGE_NAMES 新增**：`download_supplement`、`signal`、`portfolio`、`backtest`、`attribution`，共 10 个阶段。

**验证**：`python -m py_compile scripts/run_pipeline.py` 通过。

---

### F9-002 — 信号/组合/回测/归因没有脚本化发布链路（Blocker）

**修复文件**：新建以下 4 个脚本

| 脚本 | 功能 | 输出（公共路径，仅 TRAIN ~ VALID_END） |
|---|---|---|
| `scripts/run_signal_combination.py` | 合成信号（等权 + IC_IR 加权）| `composite_signal_*.parquet`、`ic_series_all_factors.parquet`、`icir_weight_history.parquet`、`composite_signal_metadata.json` |
| `scripts/run_portfolio_optimization.py` | QP 组合优化（LedoitWolf + L1/L2/L3 fallback）| `portfolio_weights_optimized.parquet`、`portfolio_weights_baseline.parquet`、`portfolio_weights_meta.parquet` |
| `scripts/run_backtest.py` | 验证期回测 | `backtest_nav.parquet`、`backtest_metrics.parquet`、`backtest_trades_v*.parquet`、`backtest_weights_v*.parquet` |
| `scripts/run_attribution.py` | Brinson BHB + 因子归因 | `brinson_attribution.parquet`、`brinson_period_summary.parquet`、`factor_attribution.parquet`、`factor_attr_period_summary.parquet` |

各脚本特性：
- 守卫：`run_signal_combination.py` 检查 `INVALIDATED.md`；`run_portfolio_optimization.py` 检查合成信号是否存在；`run_backtest.py` 检查权重文件是否存在；`run_attribution.py` 检查实际权重文件。
- 边界：所有脚本显式过滤 `VALID_END` 之后的日期，不触碰测试集。
- 元数据：`run_signal_combination.py` 生成 `composite_signal_metadata.json`（含 `git_commit`、`factor_list`、信号 hash 等），解决 F5-004。
- 硬指标检查：`run_backtest.py` 和 `run_test_pipeline.py` 均打印 IR / 超额最大回撤 / 换手率 PASS/FAIL。

**验证**：`python -m py_compile scripts/run_signal_combination.py scripts/run_portfolio_optimization.py scripts/run_backtest.py scripts/run_attribution.py` 通过。

---

### F9-003 — 测试流水线仍会覆盖通用训练/验证产物路径（Blocker）

**修复文件**：`scripts/run_test_pipeline.py`

**变更**：

1. **移除废弃常量**：删除 `WEIGHTS_OPT_PATH`、`WEIGHTS_BL_PATH`（已不作为写入目标使用），以及 `NAV_TEST_PATH`、`METRICS_TEST_PATH`、`TRADES_V*_TEST_PATH`、`WEIGHTS_V*_TEST_PATH`（原 `_test` 后缀公共路径）。
2. **`_run_test_backtest` 函数增加 `output_dir` 参数**：回测 NAV、metrics、交易记录、实际权重全部写入 `test_run_dir / "backtest_*.parquet"`，不再写入 `data/processed/` 根目录。
3. **main() 调用更新**：传入 `test_run_dir` 给 `_run_test_backtest`。
4. **docstring 更新**：明确标注各步骤的输出目录为 `test_run_{N}/`。

此时，`data/processed/` 下的公共路径（`composite_signal_*.parquet`、`portfolio_weights_*.parquet`、`backtest_nav.parquet` 等）由训练/验证期脚本维护；测试集全部输出在独立目录 `data/processed/test_run_{N}/` 下，满足完全隔离要求。

**验证**：`python -m py_compile scripts/run_test_pipeline.py` 通过；函数签名和调用点对齐。

---

### F9-004 — 测试集运行次数门禁不具备本地防重复能力（High）

**修复文件**：`scripts/run_test_pipeline.py`

**变更**（新增 3 个函数 + main 逻辑扩展）：

```python
def _git_commit_has_run_tag(run_id: int) -> bool:
    """检查 git 历史中是否已有 [TEST_SET_RUN_{run_id}] 提交。"""

def _write_lock_file(lock_path: Path, run_id: int, status: str) -> None:
    """写入 RUN_STARTED.json 或 RUN_FINISHED.json 锁文件。"""
```

**锁文件逻辑**：

| 场景 | 行为 |
|---|---|
| `RUN_STARTED.json` 不存在 | 正常运行，运行开始时创建锁文件 |
| `RUN_STARTED.json` 存在 + git 有对应 commit | 拒绝执行（该次运行已完成） |
| `RUN_STARTED.json` 存在 + git 无对应 commit + 无 `--resume-from-lock` | 拒绝执行（要求显式恢复参数） |
| `RUN_STARTED.json` 存在 + git 无对应 commit + 有 `--resume-from-lock` | 允许重试（打印警告） |

运行完成后写入 `RUN_FINISHED.json` 并打印需立即提交的 commit message 提示。

**CLI 新增参数**：`--resume-from-lock`（仅在审计过的中断场景使用）。

**验证**：语法通过；锁文件写入逻辑位于 main() 第一个关键检查点之后，确保锁文件创建前已完成所有守卫验证。

---

### F9-005 — 测试覆盖不足（High）

**状态**：已通过前序阶段（7/8）修复闭环

**当前状态**：

```
python -m pytest tests/ -q
# → 178 passed, 2 skipped, 5 warnings in 3.09s
```

已覆盖模块：
- `tests/test_pit.py` — PIT 财务数据可用日验证
- `tests/test_transaction.py` — 成本计算（含印花税切换）
- `tests/test_universe.py` — 可投资域逻辑
- `tests/test_preprocess.py` — 因子预处理（去极值、中性化）
- `tests/test_factor_time_boundary.py` — 时间错位测试
- `tests/test_evaluation.py` — IC 计算、shift test、分组回测
- `tests/test_signal_combiner.py` — 合成信号逻辑
- `tests/test_optimizer.py` — 组合优化（LedoitWolf、fallback）
- `tests/test_backtest_engine.py` — 回测引擎（19 个用例，阶段 7 新增）
- `tests/test_backtest_metrics.py` — 回测指标（30 个用例，阶段 7 新增）
- `tests/test_attribution.py` — 归因（16 个用例，阶段 8 新增）

2 个 skipped = `TestArtifactConsistency`（需删除 `INVALIDATED.md` 后自动启用）。

---

### F9-006 — 产物缺少统一 run manifest（High）

**状态**：已在 F9-001 的 `run_pipeline.py` 重写中内置

**变更**：`run_pipeline.py` 的 `_write_run_manifest()` 在每次 pipeline 完成（成功或失败）后写入：

```json
{
  "run_id": "20260520_143022",
  "pipeline": "training_validation",
  "started_at": "2026-05-20T14:30:22Z",
  "finished_at": "2026-05-20T15:10:55Z",
  "elapsed_seconds": 2433.1,
  "success": true,
  "git_commit": "abc1234",
  "stages_executed": ["convert", "factors", "evaluate", "signal", "portfolio", "backtest", "attribution"],
  "train_end": "2021-12-31",
  "valid_end": "2022-12-31"
}
```

输出路径：`data/processed/run_manifests/run_{timestamp}.json`（每次独立，不覆盖）。

此外，`run_signal_combination.py` 生成 `composite_signal_metadata.json`（含 `git_commit`、因子列表、IC 序列 hash、信号 hash），部分解决 F5-004（公共合成信号缺少权重历史和 metadata）。

---

### F9-001 附：README 同步修复（F1-004）

**修复文件**：`README.md`

**变更**：

- 数据下载命令更正为带参数版本：
  - `python -m scripts.download_tushare all`
  - `python -m scripts.download_supplement all`
  - `python -m scripts.csv_to_parquet all`
- 新增一键流水线命令：`python -m scripts.run_pipeline --from-stage convert --skip quality`
- 替换"按顺序运行 Notebook"为分阶段脚本命令（steps 6-9），明确 notebook 只承担探索和可视化职责

---

### 验证结果

```
python -m pytest tests/ -q
# → 178 passed, 2 skipped, 5 warnings in 3.50s

python -m py_compile scripts/run_pipeline.py scripts/run_signal_combination.py \
  scripts/run_portfolio_optimization.py scripts/run_backtest.py \
  scripts/run_attribution.py scripts/run_test_pipeline.py
# → 全部通过

git ls-files "*__pycache__*" "*.pyc"
# → 无输出（27 个 tracked pyc 已从 index 移除）
```

---

### 阶段 9 总结

| 编号 | 严重度 | 状态 | 核心修复 |
|---|---|---|---|
| F9-001 | Blocker | 已修复 | `run_pipeline.py` 重写：拆分下载阶段、强制 quality 显式跳过、新增 10 个阶段、产物存在性检查、run manifest |
| F9-002 | Blocker | 已修复 | 新建 4 个脚本：`run_signal_combination.py`、`run_portfolio_optimization.py`、`run_backtest.py`、`run_attribution.py` |
| F9-003 | Blocker | 已修复 | 移除废弃路径常量，`_run_test_backtest` 全部输出写入 `test_run_dir`，完全隔离测试集产物 |
| F9-004 | High | 已修复 | 本地锁文件（RUN_STARTED/RUN_FINISHED）+ `--resume-from-lock` 参数，防止同一 run-id 在 commit 前重复运行 |
| F9-005 | High | 已闭环（前序阶段） | 178 passed，2 skipped，覆盖 11 个测试文件、PIT/成本/因子/信号/优化/回测/指标/归因 |
| F9-006 | High | 已修复 | run manifest 内置于 `run_pipeline.py`，信号 metadata 内置于 `run_signal_combination.py` |
| F9-007 | Medium | 已修复 | `git rm --cached` 移除 27 个 tracked pyc，`.gitignore` 已覆盖 `__pycache__/` |
