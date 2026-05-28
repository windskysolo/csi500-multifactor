# 修复未闭环问题登记

> 创建日期：2026-05-20  
> 用途：集中记录已经声明修复、但经复核仍未完全闭环的问题。  
> 维护规则：后续每次复核或修复后，只在对应问题下追加“复核记录”或更新状态说明；新增未闭环问题继续追加到本文末尾。  
> 当前来源：`check/13_stage0_2_fix_verification.md` 对阶段 0-2 的修复核验、`check/15_stage3_4_fix_verification.md` 对阶段 3-4 的修复核验、`check/16_stage5_6_fix_verification.md` 对阶段 5-6 的修复核验、`check/17_stage6_7_fix_verification.md` 对阶段 6-7 的连续复核、`check/18_stage9_fix_verification.md` 对阶段 9 的修复复核、`check/20_stageA_B_completion_verification.md` 对最终路线图 A/B 部分的完成情况复核、`check/21_stageC_D_completion_verification.md` 对最终路线图 C/D 部分的完成情况复核、`check/22_stageEF_completion_verification.md` 对阶段 E/F 的完成情况复核，以及 `check/23_pre_run_readiness_verification.md` 对正式运行前状态的复核。

## 状态定义

| 状态 | 含义 |
|---|---|
| 未修复 | 原问题核心风险仍存在，或当前证据显示没有实质性修复 |
| 部分修复 | 源码/文档/产物中有一部分已修，但尚未形成完整闭环 |
| 待验证 | 已修复但缺少必要重建、测试或产物核验 |
| 已闭环 | 源码、文档、产物和测试均已验证；仅在复核后标记 |

## 当前未闭环清单

| 编号 | 原问题 | 当前状态 | 当前阻塞点 | 下一步 |
|---|---|---|---|---|
| F0-001 | 缺少 `.gitignore`，数据和生成产物进入 git 状态 | 部分修复 | 数据/Parquet 已清理出 tracked 列表，但仍有 27 个 tracked pyc | `git rm --cached` 移除 tracked pyc，不删除本地文件 |
| F0-002 | 工作区混合源码、notebook、报告、数据和缓存 | 未修复 | 当前仍混合 `src/`、`scripts/`、notebook、报告、测试、审查文件等变更 | 按源码、notebook、报告、审查文件、测试分批整理 |
| F0-004 | pytest cache 权限异常 | 未修复 | 已改到 `.codex_tmp_pytest_cache`，但该目录仍访问被拒绝，pytest 仍有 `WinError 5` warning | 修复或清理 `.codex_tmp_pytest_cache` ACL，或改到确认可写路径 |
| F0-003 | 测试集运行记录和 run-id 管理 | 部分修复 | 已去除硬编码 Run 1，但仍只靠 git 历史计数，commit 前可重复运行同一 run id | 增加本地 ledger/锁文件，阻止同一 run id 重复运行 |
| F1-002 | 行业体系口径冲突：中信一级 vs SW2021 | 部分修复 | 下载脚本已改为 SW2021 并输出 `industry_sw2021.csv`，但 `csv_to_parquet.py` 仍读取 `industry_citics.csv`，新 raw 重建链路会断 | 统一下载输出名和转换输入名，建议转换脚本改读 `industry_sw2021.csv` 并兼容旧文件 warning |
| F1-004 | 缺少 README 或复现入口文档 | 部分修复 | README 已存在，但下载/转换命令缺少脚本实际要求的 `all`/模块参数 | 修正 README 命令为 `download_tushare all`、`download_supplement all`、`csv_to_parquet all` |
| F2-003 | 成分股权重快照来源不可复现 | 基本闭环 | metadata 已加入 git index，SHA256、120 期、每期 500 股和权重合计范围均与 CSV 一致 | 提交前保留 metadata；如 CSV 重下导致 SHA 变化，更新并说明差异 |
| F2-004 | 明文 Token 和非标准 API 地址 | 部分修复 | 明文 Token 已去除；`download_supplement.py` 仍默认非官方代理 URL，README 未同步披露 `SUPPLEMENT_API_URL` 风险 | 移除默认代理并要求显式配置，或在 README/manifest 中披露代理风险 |
| F2-005 | 股票代码规则与实现不一致 | 已闭环 | `CLAUDE.md:66` 已统一为 Tushare `ts_code`，与 `AGENTS.md`/计划一致 | 发布前再做一次 `rg` 确认无 6 位裸代码规则残留 |
| F2-007 | 财务三表 PIT 宽表缺少源可用日追踪列 | 已闭环 | 当前 `financial_pit.parquet` 已含 `_pit_inc/_pit_bal/_pit_cf`，且三列非空值均不晚于索引 `pit_date` | 后续数据重建后保留同样核验 |
| F3-004 | 因子面板缺少可审计的预处理诊断产物 | 已闭环 | 当前 `factor_panel_diagnostics.parquet` 覆盖 84 个调仓日 × 27 个因子，共 2268 行 | 后续重建因子面板后重复核验覆盖率和字段 |
| F4-001 | 单因子评价报告失效标记与重建闭环 | 已闭环 | `INVALIDATED.md` 已删除，当前报告已重建并保守披露 shift test 异常信号 | 后续评价重跑若发现口径漂移，应重新加失效标记 |
| F4-002 | `factor_summary.csv` 与 `final_factors.json` 一致性和 metadata | 已闭环 | `final_factors.json` 含 `_metadata`，最终因子集合与 `factor_summary.csv.final_include` 一致，产物测试不再 skip | 后续重跑保持 `_metadata.factor_summary_md5` 与 CSV 一致 |
| F4-003 | forward return 缓存 sidecar metadata | 基本闭环 | `fwd_ret_panel.meta.json` 已存在并能阻止无 sidecar 的旧缓存；当前 `benchmark_mode=null` 语义不够直观 | 建议将 `benchmark_mode` 改为显式口径字符串，如 `raw_open_to_open_no_benchmark` |
| F4-004 | shift test 新诊断字段和保守报告措辞 | 已闭环 | `shift_result.csv` 含 `lag2_ic_ir`、`lead1_ic_ir`、`diagnosis`、`lead_reverse`；报告不再宣称“无未来函数疑点” | 对 `lead_reverse=True` 的因子继续按风险项跟踪 |
| F4-006 | 分组回测报告口径说明 | 已闭环 | 报告第 4 节已明确原始个股等权收益、不含成本、不扣基准、不代表可交易超额 | 后续报告重生成时保留该口径说明 |
| F5-001 | 合成信号使用的最终因子集合和 provenance | 已闭环 | 公共信号 metadata 已记录最终因子、IC hash、信号 hash、git commit；因子集合与 `final_factors.json` 一致 | 后续信号重建后核验 metadata hash |
| F5-002 | 测试集信号/IC 输出隔离 | 待验证 | 源码已改为写入 `test_run_dir`，但未运行授权测试集流水线，当前无 `data/processed/test_run_*` 产物 | 下次授权测试集运行时核验专用目录存在且公共信号路径未被覆盖 |
| F5-004 | IC_IR 权重历史和合成信号 metadata | 已闭环 | 公共路径已有 `composite_signal_metadata.json` 和 `icir_weight_history.parquet`，权重历史为 84 × 6 | 后续重建信号后核验日期范围不超过验证期 |
| F5-006 | Notebook 自检门禁过弱 | 已闭环 | `03_factor_combination.ipynb` 已改为 PASS/WARN/FAIL，IC_IR 加权弱于等权时不再无条件输出“可交付” | 后续报告需披露 WARN 项，不得写成硬性 PASS |
| F6-001 | L3 fallback 约束合规 | 已披露限制 | `_topn_equal_weight` 已引入 `effective_topn = max(topn, ceil(free_budget/single_max_dev))`；L3 非合规期从 30 降至 15；剩余 15 期为候选数不足 n_min 的真实无可行解，已正确标记 `constraint_compliant=False`，回测使用 `--allow-noncompliant-weights` 并在报告中披露 | 下次测试集运行后确认测试集权重合规情况 |
| F6-002 | 缺失协方差不得伪装 L1 | 已闭环 | 公共 meta 含 `cov_available`；3 个协方差缺失期均 `cov_available=False` 且 `fallback_level>=1` | 后续重建权重后重复核验 |
| F6-003 | 协方差缓存验证和来源审计 | 基本闭环 | 公共 `cov_cache` 有 81 个 `.meta.json`，记录 `min_eig_before/diag_delta/was_repaired/source_commit`；生产脚本调用验证函数 | `notebooks/04_portfolio_optimization.ipynb` 未同步新口径；sidecar 仍缺输入行情 hash |
| F6-004 | `w_prev_source` 与实际持仓闭环 | 部分修复 | 公共 meta 已含 `w_prev_source="target_weight"`；但真实执行后持仓仍未反馈优化器 | 阶段 E 报告披露目标权重近似；产品化前实现 actual weights 反馈 |
| F6-005 | 测试集权重输出隔离 | 待验证 | 源码已写入 `test_run_dir`，但未做授权测试集端到端验证，当前无 `test_run_*` 权重产物 | 下次授权测试集运行后核验公共 `portfolio_weights_*.parquet` 未被覆盖 |
| F7-001 | T+1 开盘调仓使用上一日收盘净值 | 已闭环 | 源码、单元测试、产物均已重建（2026-05-20 重跑 run_backtest）；backtest_nav/metrics/trades/weights 均由当前 engine.py 生成 | 已闭环 |
| F7-002 | 缺失执行日状态默认 FREE | 已闭环 | docstring 已正确描述缺失股票默认为 LOCKED；公开回测产物已重建 | 已闭环 |
| F7-003 | `n_no_price` 被全历史股票列污染 | 已闭环 | 公开 trade log 已由修复后 engine.py 重建，`n_no_price` 仅统计持仓或目标非零股票 | 已闭环 |
| F7-004 | 分析报告与落盘回测指标不一致 | 已闭环 | `reports/analysis_v2_results.md` 已由 run_attribution 从当前 backtest_metrics.parquet 自动生成（2026-05-20），不再含旧指标 | 已闭环 |
| F9-001 | 一键复现 pipeline 不具备严格发布门禁 | 已闭环 | `_check_artifacts()` 缺产物会使阶段失败；`backtest` 阶段显式传 `--allow-noncompliant-weights`；实测 `run_pipeline --from-stage backtest --skip quality` 成功并写 `success=true` manifest | 若要审计完整链路，仍需从更早阶段重跑生成完整 manifest |
| F9-002 | 信号/组合/回测/归因脚本化链路 | 已闭环 | 4 个生产脚本存在；`run_attribution.py` 失败会非零退出（除非显式 `--allow-partial`）；实测 backtest→attribution pipeline 成功 | 已闭环 |
| F9-003 | 测试集产物隔离 | 基本修复 | forward return 和新增协方差缓存已写入 `test_run_{N}`，专项测试通过；模块级 `PUBLIC_COV_CACHE_DIR.mkdir()` 已移除（2026-05-20），import 不再创建公共目录 | 真实 Run #2 后核验公共路径未被测试集写入 |
| F9-004 | 测试集 run-id 本地锁 | 部分修复 | `RUN_STARTED` 锁已加，但 `--resume-from-lock` 会从头重跑且缺单测/ledger 预登记 | 增加锁文件单测和结构化 ledger；明确或实现真正断点恢复 |
| F9-005 | 发布前测试覆盖不足 | 基本闭环 | 当前全量 `pytest` 为 `201 passed, 2 warnings`；阶段 9 流水线/隔离/锁测试已存在 | `.pytest_cache` 权限 warning 仍需处理；真实 Run #2 后再核验测试集隔离 |
| F9-006 | 统一 run manifest 和输入版本校验 | 基本闭环 | 源码已增加 `config_hash`、输入/输出 hash、跳过阶段和测试集运行计数；实测生成 `run_20260520_212027.json`，`git_commit=0f7a71f`、`test_set_run_count=1` | 当前 manifest 只覆盖 `backtest→attribution`，完整训练/验证链路仍可择机从更早阶段重跑 |
| F9-007 | tracked pyc 与 pytest cache 权限问题 | 部分修复 | tracked pyc 已清；`.codex_tmp_pytest_cache` 不再使用 | 当前全量 pytest 仍有 `.pytest_cache` 的 `WinError 5` PytestCacheWarning |

## 详细记录

### F0-001 `.gitignore` 与 tracked pyc 未闭环

**当前状态**：部分修复  

**复核记录 — 2026-05-20**：

- `.gitignore` 已覆盖 `data/raw/`、`data/processed/`、`*.parquet`、`__pycache__/`、`.pytest_cache/`。
- `git ls-files data` 为 0。
- `git ls-files "*.csv" "*.parquet"` 为 0。
- `git ls-files "*__pycache__*" "*.pyc"` 仍返回 27 个 tracked pyc 文件。

**闭环标准**：

- `git ls-files "*__pycache__*" "*.pyc"` 无输出。
- `git status --short` 不再出现 pyc 修改。

### F0-002 工作区仍未形成干净审查基线

**当前状态**：未修复

**复核记录 — 2026-05-20**：

当前 `git status` 仍混合：

- `M notebooks/*.ipynb`
- `M scripts/*.py`
- `M src/*.py`
- 未跟踪 `reports/factor_evaluation/*.csv/md/json`
- 未跟踪 `check/*.md`
- 未跟踪 `tests/`

**闭环标准**：

- 源码、notebook、报告、测试、审查文件能按批次清楚归类。
- 生成产物和缓存不进入发布候选状态。

### F0-004 `.pytest_cache` 权限 warning 未修复

**当前状态**：未修复

**复核记录 — 2026-05-20**：

`python -m pytest tests/ -q` 结果：

- `35 passed`
- 仍有 `PytestCacheWarning ... WinError 5 拒绝访问`

**闭环标准**：

- `python -m pytest tests/ -q` 通过且无 `.pytest_cache` 权限 warning。

### F0-003 测试集 run-id 防重复能力不足

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `check/test_set_run_log.md` 已记录 Run #1。
- `scripts/run_test_pipeline.py` 已要求 `--run-id`。
- 当前仍只通过 git 历史统计已运行次数；若 Run #2 执行后未 commit，本地可重复运行同一 `--run-id 2`。

**闭环标准**：

- 测试集脚本读取本地 ledger 或锁文件。
- 同一 run id 一旦开始运行，commit 前再次运行必须失败。

### F1-002 行业下载/转换链路仍未闭环

**当前状态**：部分修复

**复核补充 — 2026-05-20（阶段 A/B）**：

- `scripts/download_tushare.py` 已改为只下载 SW2021，并输出 `industry_sw2021.csv`，不再尝试 CITICS。
- 新残留：`scripts/csv_to_parquet.py` 仍读取 `raw/industry_citics.csv`；如果从空 raw 目录按新下载脚本重建，行业转换会找不到输入文件。
- 当前 raw 目录仍只有旧 `industry_citics.csv` 和 `industry_classify_SW2021.csv`，没有 `industry_sw2021.csv`。

**闭环标准更新**：

- 下载输出名与转换输入名统一，建议 `csv_to_parquet.py` 改读 `industry_sw2021.csv`。
- 如兼容旧 `industry_citics.csv`，必须明确 warning 并在 metadata 中说明。

**复核记录 — 2026-05-20**：

- `PROJECT_PLAN_v1.1.md`、`src/config.py`、`README.md` 已统一为 SW2021。
- `scripts/download_tushare.py` 仍有：
  - 文档字符串“industry 中信一级行业分类”
  - `src = "CITICS"` 后失败降级 `SW2021`

**闭环标准**：

- 下载脚本明确以 SW2021 为唯一当前行业源。
- 文件名、日志、metadata 都说明实际行业体系，避免 `industry_citics.csv` 误导。

### F1-004 README 复现命令与脚本 CLI 不一致

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

README 已存在，但当前命令写法缺少参数：

- `python -m scripts.download_tushare`
- `python -m scripts.download_supplement`
- `python -m scripts.csv_to_parquet`

这些脚本实际需要 `all` 或模块参数。

**闭环标准**：

- README 命令改为与真实 CLI 一致。
- 至少包括：
  - `python -m scripts.download_tushare all`
  - `python -m scripts.download_supplement all`
  - `python -m scripts.csv_to_parquet all`

### F2-003 成分股权重 metadata 未纳入版本管理

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `dl_index_weight()` 和 `indexweight` 已存在。
- `data/csi500_index_weight_201601_202512.meta.md` 已存在。
- 当前 CSV SHA256 与 metadata 一致。
- `data/csi500_index_weight_201601_202512.meta.md` 仍是未跟踪文件。

**闭环标准**：

- metadata 文件纳入版本管理。
- 复现时能核对 CSV SHA256、期数、每期成分数和权重合计范围。

### F2-004 补充数据默认 API 仍是非官方代理

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- 两个下载脚本均已改为读取 `TUSHARE_TOKEN` 环境变量。
- `scripts/download_supplement.py` 仍默认 `API_URL = "http://tsdata.siboer.xin/1wan"`，可通过 `SUPPLEMENT_API_URL` 覆盖。

**闭环标准**：

- 明确决定是否允许默认代理。
- 若按产品化审查更保守，应移除默认代理，要求显式配置 `SUPPLEMENT_API_URL`。

### F2-005 `CLAUDE.md` 股票代码规则仍冲突

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `AGENTS.md` 已改为 Tushare `ts_code`。
- `PROJECT_PLAN_v1.1.md` 已改为 Tushare `ts_code`。
- `CLAUDE.md:66` 仍写“统一为 6 位字符串（含前导零）”。

**闭环标准**：

- `CLAUDE.md` 同步更新为 Tushare `ts_code` 格式，禁止仅用 6 位裸代码。

### F2-007 `financial_pit.parquet` 源 PIT 列已重建

**当前状态**：已闭环

**复核补充 — 2026-05-20（阶段 A/B）**：

- 当前 `data/processed/financial_pit.parquet` 存在，shape 为 `(56744, 14)`。
- 索引为 `['pit_date', 'ts_code', 'end_date']`。
- 列中包含 `_pit_inc`、`_pit_bal`、`_pit_cf`。
- 三个 `_pit_*` 非空值均不晚于索引 `pit_date`。

**闭环标准更新**：

- 本项当前已闭环；后续每次重建 `financial_pit.parquet` 后重复上述核验。

**复核记录 — 2026-05-20**：

- 源码已保留 `_pit_inc`、`_pit_bal`、`_pit_cf`。
- 当前 `data/processed/financial_pit.parquet` 仍不含这三列。

**闭环标准**：

- 重建 `financial_pit.parquet`。
- 验证 `_pit_inc`、`_pit_bal`、`_pit_cf` 存在。
- 验证每个 `_pit_*` 非空值均不晚于合成 `pit_date`。

### F3-004 因子面板预处理诊断产物未生成

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `scripts/build_factor_panels.py` 已包含 `DIAGNOSTICS_PATH = cfg.DATA_PROC / "factor_panel_diagnostics.parquet"`。
- 构建流程末尾已在 `diag_rows` 非空时写出诊断 parquet。
- 当前 `data/processed/factor_panel_diagnostics.parquet` 不存在。

**闭环标准**：

- 重跑训练/验证期因子面板构建。
- `factor_panel_diagnostics.parquet` 存在。
- 诊断行覆盖 27 个因子 × 84 个训练/验证调仓日。
- 字段至少包含 `n_raw`、`n_fin_filtered`、`n_winsor_clipped`、`n_neutralize_valid`、`skipped_neutralize`、`n_final`、`error_msg`。

### F4-001 单因子评价报告仍处于失效状态

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `reports/factor_evaluation/INVALIDATED.md` 已存在，能阻止误用当前评价报告。
- 当前 `daily_quote.ret` 与 `close_adj` 后复权收益的最大差异为 `0.0`，但 `INVALIDATED.md` 仍写 `daily_quote.parquet` 尚未重建。
- 当前 `factor_summary.csv` 与 `final_factors.json` 最终因子集合同为 12 个，但 `INVALIDATED.md` 仍写 CSV 18 个、JSON 12 个。
- 当前评价报告仍未用修复后的评价脚本重跑，失效标记未解除。

**闭环标准**：

- 用新脚本重跑单因子评价。
- 重新核验报告、CSV、JSON、fwd_ret metadata 一致。
- 更新或删除 `INVALIDATED.md`；删除前当前报告不得作为可信因子筛选依据。

### F4-002 `final_factors.json` 缺少 metadata

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- 源码已在 `scripts/run_factor_evaluation.py` 中写入 `_metadata`。
- 当前 `reports/factor_evaluation/final_factors.json` 仅包含 `final_factors`、`excluded`、`stability_weights`。
- 当前最终因子集合与 `factor_summary.csv` 一致，但缺少 `run_id`、`generated_at`、`git_commit`、`factor_summary_md5` 等 metadata。
- 由于 `INVALIDATED.md` 存在，`tests/test_evaluation.py` 中两个产物一致性测试当前跳过。

**闭环标准**：

- 重跑评价脚本生成带 `_metadata` 的 `final_factors.json`。
- 移除失效标记后，产物一致性测试不再 skip 且通过。

### F4-003 `fwd_ret_panel.parquet` 缺少 sidecar metadata

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- 源码已支持 `fwd_ret_panel.meta.json`，记录 `start`、`end`、`n_dates`、`rebalance_dates_hash`、`benchmark_mode`、`source_commit`。
- 当前 `data/processed/fwd_ret_panel.parquet` 存在，日期范围 `2016-01-29` 至 `2022-11-30`。
- 当前 `data/processed/fwd_ret_panel.meta.json` 不存在。
- 源码在旧缓存无 sidecar 时只发出 warning 并继续加载，仍可能让未校验缓存进入评价。

**闭环标准**：

- 用 `--recompute-fwd-ret` 重算 forward return 并生成 sidecar。
- 旧缓存缺少 sidecar 时，生产运行应拒绝加载或要求显式确认重算。

### F4-004 shift test 产物仍是旧 schema

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `src/evaluation/shift_test.py` 已支持 `lag_2`、`lead_1`、`diagnosis`、`lead_reverse`。
- `tests/test_evaluation.py` 已覆盖这些字段。
- 当前 `reports/factor_evaluation/shift_result.csv` 仍只有 `orig_ic_ir`、`lagged_ic_ir`、`ic_ir_drop`、`warning`。
- 当前 `factor_evaluation_report.md` 仍写“所有因子时间错位测试正常（无未来函数疑点）”。

**闭环标准**：

- 重跑单因子评价。
- `shift_result.csv` 包含 `lag2_ic_ir`、`lead1_ic_ir`、`diagnosis`、`lead_reverse`。
- 报告不再使用“无未来函数疑点”这种过强结论。

### F4-006 分组回测口径说明未进入当前报告

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `scripts/run_factor_evaluation.py` 的报告模板已新增分组收益口径说明。
- 当前 `reports/factor_evaluation/factor_evaluation_report.md` 未重生成，第 4 节仍直接进入“5分组回测摘要（训练集，不含成本）”。
- 当前报告没有明确说明该表是原始个股等权收益、不含交易成本、不扣减基准、不代表相对中证500全收益指数的可交易超额收益。

**闭环标准**：

- 重跑单因子评价报告。
- 第 4 节头部明确展示上述口径说明。

### F5-001 合成信号 provenance 仍不完整

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- 当前 `reports/factor_evaluation/factor_summary.csv` 与 `final_factors.json` 的最终因子集合均为 12 个，集合一致。
- 当前 `final_factors.json` 仍缺 `_metadata`，无法追踪 `run_id`、生成时间、git commit 和 `factor_summary_md5`。
- `reports/factor_evaluation/INVALIDATED.md` 仍存在，阶段 4 评价链路尚未解除失效状态。
- 当前公共 `data/processed/composite_signal_ic_ir.parquet` 没有 sidecar metadata，无法说明使用了哪次评价结果和哪组最终因子。

**闭环标准**：

- 重跑阶段 4 评价，生成带 `_metadata` 的 `final_factors.json`。
- 重建阶段 5 信号，生成信号 metadata，记录最终因子列表、评价产物哈希和生成环境。
- 移除或更新 `INVALIDATED.md`，并让评价产物一致性测试不再 skip。

### F5-002 测试集信号输出隔离尚未端到端验证

**当前状态**：待验证

**复核记录 — 2026-05-20**：

- `scripts/run_test_pipeline.py` 的 `_build_composite_signals()` 已改为向 `output_dir` 写入 `composite_signal_equal.parquet`、`composite_signal_ic_ir.parquet` 和 `ic_series_all_factors.parquet`。
- 当前未运行测试集流水线，未消耗测试集次数。
- 当前 `data/processed/test_run_*` 不存在，尚无运行产物可验证公共路径未被覆盖。

**闭环标准**：

- 下次授权测试集运行后，确认 `data/processed/test_run_{N}/` 下存在信号和 IC 产物。
- 确认公共 `data/processed/composite_signal_*.parquet` 与 `ic_series_all_factors.parquet` 未被该次测试集运行覆盖。

### F5-004 公共合成信号缺少权重历史和 metadata

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `src/signal/combiner.py` 已支持 `return_diagnostics=True`，返回 `weight_history`、`cold_start_flags`、`cold_start_count` 和 `method`。
- `scripts/run_test_pipeline.py` 已在测试集专用目录写出 `icir_weight_history.parquet` 和 `composite_signal_metadata.json`。
- 当前公共路径仍只有：
  - `data/processed/composite_signal_equal.parquet`
  - `data/processed/composite_signal_ic_ir.parquet`
  - `data/processed/ic_series_all_factors.parquet`
- 当前公共路径缺少 `data/processed/composite_signal_metadata.json` 和 `data/processed/icir_weight_history.parquet`。
- `notebooks/03_factor_combination.ipynb` 仍只写上述 3 个 Parquet，未写同等审计元数据。

**闭环标准**：

- 训练/验证期公共信号产物也必须生成 `composite_signal_metadata.json` 和 `icir_weight_history.parquet`。
- metadata 至少记录 `run_id`、生成时间、最终因子列表、IC 序列哈希、信号哈希、冷启动期数、窗口和 `min_valid_factors`。

### F5-006 Notebook 自检仍会把负向提升标为通过

**当前状态**：未修复

**复核记录 — 2026-05-20**：

- `notebooks/03_factor_combination.ipynb` 中 “IC_IR 加权 vs 等权对比” 检查仍写为 `True`，仅作为信息项。
- 当前 notebook 输出显示 `ic_ir=0.772`、`equal=0.921`、`提升=-0.149`，但仍打印“全部通过，合成信号可交付组合优化模块”。

**闭环标准**：

- 当 IC_IR 加权弱于等权时，notebook 自检不得直接输出“可交付”。
- 若该项因 `stability_weights` 设计取舍而不作为失败，应输出明确风险声明，并要求在报告中披露，不得与硬性 PASS 混淆。

### F6-001 L3 fallback 仍未约束闭环

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `src/portfolio/optimizer.py` 的 L3 normal path 已支持 `w_prev_vec`、`limit_dn_indices`，可锁定停牌和跌停持仓。
- 当前公共 `portfolio_weights_optimized.parquet` 仍为旧产物，`portfolio_weights_meta.parquet` 显示 14 个 L3 日期。
- 抽查当前旧产物：
  - `2016-03-31`：7 只停牌股锁定违约，上期违约权重合计约 `8.66%`，本期为 `0`；50 只股票单股偏离超过 1%，最大约 `1.934%`。
  - `2016-07-29`：4 只停牌股锁定违约，上期违约权重合计约 `3.53%`，本期为 `0`；50 只股票单股偏离超过 1%，最大约 `1.928%`。
- 源码 L3 仍未检查 `single_max_dev`。
- `_topn_equal_weight()` 的“无可买候选”分支会把自由预算分配给非停牌股票，可能给涨停不可买股票新增权重；合成样例输出 `w=[0.5, 0.5]`，其中涨停股票从 0 增至 0.5。

**闭环标准**：

- L3 输出必须显式校验停牌锁定、涨停不加仓、跌停不减仓、权重和、单股偏离。
- 无可买候选时不得悄悄违反交易状态约束；若无可行权益权重，应写入 `constraint_compliant=False` 并由回测/报告拒绝当作合规目标。
- 重建公共权重后，14 个历史 L3 日期不再出现停牌锁定和单股偏离违约，或 metadata 明确标记不可交付。

### F6-002 公共权重仍把缺失协方差期记录为 L1

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- 源码 `optimize_all_periods()` 和测试集路径已增加 `cov_available` 并在协方差缺失时强制 `fallback_level >= 1`。
- 当前 `data/processed/cov_cache/` 共有 81 个文件，首个为 `20160429.parquet`，缺少 2016-01、2016-02、2016-03 三期。
- 当前公共 `portfolio_weights_meta.parquet` 仍只有 `fallback_level`、`solver_status`、`solve_time_s`、`n_holdings` 四列。
- 当前 `2016-01-29` 与 `2016-02-29` 在缺失协方差时仍记录 `fallback_level=0, solver_status=optimal`。
- `notebooks/04_portfolio_optimization.ipynb` 仍按旧 schema 写 metadata。

**闭环标准**：

- 重建公共组合权重。
- `portfolio_weights_meta.parquet` 必须包含 `cov_available`。
- 所有协方差缺失期必须记录 `cov_available=False` 且 `fallback_level >= 1` 或明确 warm-up/跳过逻辑。

### F6-003 协方差缓存验证未覆盖生产 notebook

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `src/portfolio/covariance.py` 已新增 `validate_and_repair_covariance()`，可检查对称性和最小特征值。
- `scripts/run_test_pipeline.py` 读取缓存时已调用该函数。
- `tests/test_optimizer.py` 已覆盖 PSD、非 PSD、不对称矩阵和单位矩阵。
- `notebooks/04_portfolio_optimization.ipynb` 仍只在缓存读取后检查 NaN，没有调用 `validate_and_repair_covariance()`。
- 当前协方差缓存没有 source、min_eig_before、diag_delta、was_repaired 等审计 metadata。

**闭环标准**：

- 所有生产/研究路径读取协方差缓存时统一调用 `validate_and_repair_covariance()`。
- 协方差 metadata 至少记录日期、代码顺序、来源、最小特征值、是否对称化、对角扰动量和生成 commit/hash。

### F6-004 `w_prev_source` 未进入公共 metadata

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- 源码和测试集路径已写入 `w_prev_source="target_weight"`。
- 当前公共 `portfolio_weights_meta.parquet` 缺少 `w_prev_source`。
- 真实执行后的 actual weights 尚未反馈给下一期优化器；目前仍是目标权重近似。

**闭环标准**：

- 重建公共权重 metadata，包含 `w_prev_source`。
- 报告明确披露当前为目标权重近似。
- 若进入产品化闭环，应由回测引擎输出的实际持仓权重反馈下一期优化器。

### F6-005 测试集权重输出隔离尚未端到端验证

**当前状态**：待验证

**复核记录 — 2026-05-20**：

- `scripts/run_test_pipeline.py` 的 `_run_optimization()` 已将测试集权重写入 `output_dir / "portfolio_weights_*.parquet"`。
- 当前未运行测试集流水线，未消耗测试集次数。
- 当前无 `data/processed/test_run_*` 权重产物可验证。

**闭环标准**：

- 下次授权测试集运行后，确认 `data/processed/test_run_{N}/portfolio_weights_optimized.parquet`、`portfolio_weights_baseline.parquet` 和 `portfolio_weights_meta.parquet` 存在。
- 确认公共 `data/processed/portfolio_weights_*.parquet` 未被该次测试集运行覆盖。

### F7-001 回测 T+1 开盘前净值产物未重建

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `src/backtest/engine.py` 已在调仓执行前计算 `pretrade_value = cash + Σ shares_i * T+1 执行价_i`。
- 停牌无开盘价的既有持仓使用当日可得 `close_adj` 估值后再传入 `_execute_rebalance()`。
- `tests/test_backtest_engine.py` 已覆盖“目标权重不变但开盘价翻倍时不应交易”的合成用例。
- 当前公开 `data/processed/backtest_*.parquet` 文件最后修改时间为 2026-05-19，早于修复记录；尚不能证明 NAV、成本、换手已经按新逻辑重算。

**闭环标准**：

- 用修复后的 `run_backtest()` 重跑验证期回测。
- 随机抽查至少 2 个调仓日，确认 `TradeRecord.portfolio_value_before` 等于 T+1 开盘前组合净值，而非上一日收盘净值。
- 重新生成 `backtest_nav.parquet`、`backtest_metrics.parquet`、`backtest_trades_*.parquet` 和 `backtest_weights_*.parquet`。

### F7-002 缺失状态 LOCKED 修复未完全闭环

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `src/backtest/engine.py` 已将状态快照中完全缺失的股票设为 `TradeState.LOCKED`。
- `load_stock_status(d)` 整日缺失并抛出 `KeyError` 时，已保守将当日所有股票设为 `LOCKED`。
- `tests/test_backtest_engine.py` 已覆盖缺失股票、整日缺失和 LOCKED 不成交用例。
- `_status_to_trade_state()` docstring 仍写“不在 status_snap 中的股票默认为 FREE”，与实现和项目硬规则冲突。
- 当前公开回测产物未重建，无法确认历史交易日志已经按 LOCKED 口径更新。

**闭环标准**：

- 修正 docstring，明确缺失状态为 LOCKED。
- 重跑回测后核验目标非零或既有持仓股票的缺失状态不会产生买入/卖出成交。
- 若报告披露交易受限统计，应增加或说明缺失状态处理口径。

### F7-003 公开交易日志仍是旧 `n_no_price` 口径

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `src/backtest/engine.py` 中 `_execute_rebalance()` 已将 active universe 限制为“当前持仓 ∪ 目标权重非零股票”。
- `tests/test_backtest_engine.py` 已覆盖 500 只目标为 0 的历史股票不计入 `n_no_price`。
- 当前公开 `backtest_trades_v1.parquet` 与 `backtest_trades_v2.parquet` 仍各 12 行，`n_no_price` 最小 23、均值约 26.92、最大 38，仍是阶段 7 审查时观察到的旧口径特征。

**闭环标准**：

- 重跑验证期回测。
- `n_no_price` 只统计持仓或目标交易需求股票，不再被权重面板历史 union 列污染。
- 报告中的交易约束统计使用重跑后的 trade log。

### F7-004 回测报告仅标注过期，尚未重生成

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `reports/analysis_v2_results.md` 顶部已有“本报告已过期，不可用于当前结论”的声明，并列明阶段 7/8 关键问题。
- 但正文仍保留旧指标和旧结论，例如 V2 IR `1.025`、年化超额 `+5.35%`、三项硬指标达标等。
- 当前 `backtest_metrics.parquet` 中 V2 IR 为 `0.340449`、年化超额为 `+1.4725%`，与正文旧值不一致。

**闭环标准**：

- 在阶段 6 权重和阶段 7 回测产物重建后，用同一批 `backtest_metrics.parquet` 自动生成或替换报告正文。
- 报告中记录输入文件 hash、生成时间、代码 commit、测试集运行计数。
- 旧的达标判断不得与过期声明同时保留在可交付报告正文中。

### F9-001 一键 pipeline 仍不是严格发布门禁

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `scripts/run_pipeline.py` 已补齐 `download_core`、`download_supplement`、`convert`、`signal`、`portfolio`、`backtest`、`attribution` 等阶段。
- 下载/转换阶段已经使用 `all` 参数，README 也同步为带参数命令。
- `quality` 未实现时要求显式 `--skip quality`，不再静默跳过。
- 但 `_check_artifacts()` 发现关键产物缺失时只打印 warning；`_run_stage()` 仍返回成功，pipeline 最终可写 `success=true` manifest。

**闭环标准**：

- 关键产物缺失必须让 pipeline 退出非零码。
- manifest 必须记录失败阶段、缺失产物、显式跳过阶段。
- `quality` 若继续跳过，应在 manifest 和报告中披露。

### F9-002 脚本化链路错误传播不足

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `scripts/run_signal_combination.py`、`scripts/run_portfolio_optimization.py`、`scripts/run_backtest.py`、`scripts/run_attribution.py` 已存在。
- `scripts/run_pipeline.py` 已调用上述脚本。
- `scripts/run_attribution.py` 捕获 Brinson 或因子归因异常后只记录日志，不 `sys.exit(1)`。
- 当前公共目录尚未生成新版 `brinson_attribution.parquet`、`factor_attribution.parquet` 等脚本产物，不能证明链路已端到端重建。

**闭环标准**：

- 发布路径中的归因失败必须返回非零退出码。
- pipeline 对信号、权重、回测、归因的全部关键产物做强校验。
- 用脚本重建验证期产物，并核验日期范围不超过 `VALID_END`。

### F9-003 测试集输出隔离基本修复，待端到端验证

**当前状态**：基本修复

**复核补充 — 2026-05-20（阶段 A/B）**：

- `scripts/run_test_pipeline.py` 已将扩展后的 `fwd_ret_panel.parquet` 写入 `test_run_dir`，不再写公共 `data/processed/fwd_ret_panel.parquet`。
- 测试期新增协方差缓存写入 `test_run_dir/cov_cache/`，公共 `cov_cache/` 只作为读取来源。
- `tests/test_test_pipeline_isolation.py` 已覆盖 forward return 隔离、协方差缓存隔离和 run-id 锁行为；专项测试通过。
- 小残留：`run_test_pipeline.py` import 时仍会对公共 `cov_cache/` 执行 `mkdir(exist_ok=True)`；这不是测试期协方差数据写入，但仍不是完全只读。

**闭环标准更新**：

- 真实 Run #2 授权后，核验公共 `fwd_ret_panel.parquet`、公共 `cov_cache/`、公共信号、公共权重、公共回测产物均未被修改。
- 可选收紧：去除 import 时创建公共 `cov_cache/` 的行为。

**复核记录 — 2026-05-20**：

- `scripts/run_test_pipeline.py` 已将测试集信号、权重、回测输出写入 `data/processed/test_run_{N}/`。
- 但 `FWD_CACHE` 仍指向公共 `data/processed/fwd_ret_panel.parquet`，`_extend_fwd_ret_panel()` 会把测试期 forward return 写回公共缓存。
- `COV_CACHE_DIR` 仍指向公共 `data/processed/cov_cache/`，测试期协方差估计会写入公共缓存。
- 因此 F9-003 的“测试集产物完全隔离”仍未达成。

**闭环标准**：

- 测试集 forward return、协方差缓存、信号、权重、回测、归因全部写入同一个 `test_run_{N}` 或 `test_runs/run_N` 目录。
- 测试集流水线不得写公共 `fwd_ret_panel.parquet` 和公共 `cov_cache/`。
- 增加路径隔离单元测试，静态或 monkeypatch 检查所有写路径。

### F9-004 run-id 锁文件已有但恢复语义仍需收紧

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `scripts/run_test_pipeline.py` 会在 `test_run_{N}` 下使用 `RUN_STARTED.json` 和 `RUN_FINISHED.json`。
- 已存在 `RUN_STARTED.json` 且未传 `--resume-from-lock` 时会拒绝重复运行。
- 但 `--resume-from-lock` 实际会从头执行后续流程，已有测试集信号/权重/回测产物可被覆盖，不是真正断点恢复。
- 未发现针对锁文件、重复 run-id、resume 语义的单元测试。

**闭环标准**：

- 增加不消耗测试集数据的锁文件单测。
- `--resume-from-lock` 要么实现明确断点恢复，要么要求人工删除并重新登记。
- Run #2 应读取结构化 ledger 或预登记状态后才能启动。

### F9-005 发布前测试覆盖仍缺阶段 9 门禁测试

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- 全量测试结果为 `178 passed, 2 skipped, 6 warnings`。
- PIT、交易成本、可投资域、因子预处理、评价、信号、优化、回测、指标、归因等业务模块测试已补齐。
- 2 个产物一致性测试仍因 `reports/factor_evaluation/INVALIDATED.md` 存在而跳过。
- 未发现 `run_pipeline`、`run_test_pipeline`、manifest、路径隔离、锁文件重复运行等阶段 9 逻辑的专项测试。

**闭环标准**：

- 增加阶段 9 专项测试，不依赖真实测试集。
- 移除或更新失效标记后，产物一致性测试不再 skip。
- pytest 运行无缓存权限 warning。

### F9-006 run manifest 和缓存 metadata 未达到发布追溯要求

**当前状态**：部分修复

**复核记录 — 2026-05-20**：

- `scripts/run_pipeline.py` 已具备写 `data/processed/run_manifests/run_{run_id}.json` 的逻辑。
- 当前 `data/processed/run_manifests/` 不存在，尚无实际 manifest 可审计。
- manifest 只记录运行时间、成功状态、git commit、阶段列表、训练/验证边界；缺输入 hash、输出 hash、配置 hash、测试集运行计数、跳过阶段。
- `scripts/run_signal_combination.py` 遇到旧 `fwd_ret_panel.parquet` 缺 sidecar 时只 warning 后加载；且 `rebalance_dates_hash` 字段写的是 forward return 内容 hash，字段语义不准确。

**闭环标准**：

- manifest 记录 config hash、输入/输出文件 hash、测试集运行计数、跳过阶段和关键参数。
- 生产路径加载旧缓存缺 metadata 时拒绝运行或要求显式 `--recompute`。
- metadata 字段名与内容一致，调仓日 hash 与数据内容 hash 分开记录。

### F9-007 pyc 已清但 pytest cache 权限仍未闭环

**当前状态**：部分修复

**复核补充 — 2026-05-20（阶段 A/B）**：

- `git ls-files "*__pycache__*" "*.pyc"` 当前无输出，tracked pyc 已清理。
- `pytest.ini` 已将 cache_dir 改为 `.codex_tmp_pytest_cache`。
- 但 `.codex_tmp_pytest_cache` 目录当前存在且访问被拒绝，`Get-Acl` 与 `Get-ChildItem` 均失败。
- 全量 pytest 仍出现 `PytestCacheWarning`，只是路径从 `.pytest_cache` 变为 `.codex_tmp_pytest_cache`。

**闭环标准更新**：

- 修复或删除 `.codex_tmp_pytest_cache` 的异常 ACL，或把 `cache_dir` 改到确认可写目录。
- `python -m pytest tests/ -q --basetemp=...` 不再出现 cache warning。

**复核记录 — 2026-05-20**：

- `git ls-files "*__pycache__*" "*.pyc"` 当前无输出，tracked pyc 已清理出版本管理。
- `python -m pytest tests/ -q --basetemp=.codex_tmp_stage9_full` 仍出现 `.pytest_cache` 的 `WinError 5 拒绝访问` warning。
- `git status` 仍提示无法访问用户级 git ignore 文件，这是环境权限噪音，但不属于策略业务逻辑。

**闭环标准**：

- 修复或清理 `.pytest_cache` 权限后复跑 pytest。
- 发布前确认 pyc/cache 不出现在 tracked 文件和 `git status --short` 中。

### 阶段 A/B 复核补充记录

**复核记录 — 2026-05-20（对应 `check/20_stageA_B_completion_verification.md`）**：

阶段 A：

- `scripts/run_test_pipeline.py` 已将测试集扩展后的 `fwd_ret_panel.parquet` 写入 `test_run_dir`，不再覆盖公共 `data/processed/fwd_ret_panel.parquet`。
- 测试期新增协方差缓存已写入 `test_run_dir/cov_cache/`；公共 `cov_cache/` 用作只读缓存来源。
- `scripts/run_pipeline.py` 已在关键产物缺失时返回失败，并在 manifest 中记录 `success=false`。
- `scripts/run_attribution.py` 已在 Brinson 或因子归因失败时退出非零码，除非显式传入 `--allow-partial`。
- 新增阶段 A 专项测试，运行结果 `15 passed`。
- 残留：`.codex_tmp_pytest_cache` 当前目录访问被拒绝，导致全量 pytest 仍有 `PytestCacheWarning`；`run_test_pipeline.py` import 时仍会创建公共 `cov_cache` 目录。

阶段 B：

- `CLAUDE.md` 股票代码规则已统一为 Tushare `ts_code`。
- `financial_pit.parquet` 已含 `_pit_inc/_pit_bal/_pit_cf`，且非空源 PIT 日期均不晚于索引 `pit_date`。
- 成分股权重 metadata 已加入 git index，SHA256 与 CSV 一致。
- 残留：`scripts/download_tushare.py` 现在输出 `industry_sw2021.csv`，但 `scripts/csv_to_parquet.py` 仍读取 `industry_citics.csv`，新 raw 重建会断链。
- 残留：`scripts/download_supplement.py` 仍默认非官方代理 URL，README 未同步披露该风险。

**本次验证摘要**：

- `python -m py_compile scripts\run_pipeline.py scripts\run_test_pipeline.py scripts\run_attribution.py scripts\download_tushare.py scripts\download_supplement.py scripts\csv_to_parquet.py` 通过。
- `python -m pytest tests\test_pipeline_release_gates.py tests\test_test_pipeline_isolation.py -q --basetemp=.codex_tmp_stage_ab_a`：`15 passed, 1 warning`。
- `python -m pytest tests\test_pit.py tests\test_universe.py tests\test_transaction.py -q --basetemp=.codex_tmp_stage_ab_b`：`35 passed, 1 warning`。
- `python -m pytest tests/ -q --basetemp=.codex_tmp_stage_ab_all`：`200 passed, 6 warnings`。
- 未运行测试集流水线，未消耗测试集次数。

### 阶段 C/D 复核补充记录

**复核记录 — 2026-05-20（对应 `check/21_stageC_D_completion_verification.md`）**：

阶段 C：

- `reports/factor_evaluation/INVALIDATED.md` 当前不存在，评价链路已解除失效标记。
- `data/processed/factor_panel_diagnostics.parquet` 当前为 2268 行，覆盖 84 个调仓日 × 27 个因子。
- `reports/factor_evaluation/final_factors.json` 已含 `_metadata`，当前最终因子数为 6，集合与 `factor_summary.csv.final_include` 一致。
- `data/processed/fwd_ret_panel.meta.json` 已存在；小残留是 `benchmark_mode=null` 语义不够显式，建议后续改为明确原始收益口径字符串。
- `shift_result.csv` 已含 `lag2_ic_ir`、`lead1_ic_ir`、`diagnosis`、`lead_reverse`；当前 `lead_reverse=True` 为 14 个，报告已作为异常信号披露。
- `factor_evaluation_report.md` 第 4 节已声明分组收益为原始个股等权收益、不含成本、不扣基准、不代表可交易超额。

阶段 D：

- `composite_signal_metadata.json`、`icir_weight_history.parquet` 已进入公共训练/验证产物，信号因子集合与 `final_factors.json` 一致。
- `portfolio_weights_meta.parquet` 当前含 `cov_available`、`w_prev_source`、`constraint_compliant`；Fallback 分布为 L1=52、L2=2、L3=30。
- 3 个协方差缺失期均 `cov_available=False` 且 `fallback_level>=1`，不再伪装为 L1。
- `data/processed/cov_cache/` 当前 81 个 parquet 均有 `.meta.json` sidecar，记录 `min_eig_before`、`diag_delta`、`was_repaired` 等审计字段。
- 当前 30 个 L3 日期停牌锁定违约 0、涨停加仓违约 0、跌停减仓违约 0。
- 当前 30 个 L3 日期全部存在单股偏离超限，合计 1578 个股票-日期，最大单股偏离约 1.954%，因此全部标记 `constraint_compliant=False`。这说明“违规被识别”，但不说明“权重已满足交易约束”。
- `notebooks/04_portfolio_optimization.ipynb` 未检索到 `validate_and_repair_covariance`、`cov_available`、`w_prev_source`、`constraint_compliant` 等新口径字段；若 notebook 作为交付物，需要同步。

**本次验证摘要**：

- `python -m pytest tests\test_preprocess.py tests\test_factor_time_boundary.py tests\test_evaluation.py tests\test_signal_combiner.py tests\test_optimizer.py -q --basetemp=.codex_tmp_stage_cd`：`85 passed, 2 warnings`。
- `python -m pytest tests/ -q --basetemp=.codex_tmp_stage_cd_all`：`200 passed, 7 warnings`。
- 未运行测试集流水线，未消耗测试集次数。

### 阶段 E/F 复核补充记录

**复核记录 — 2026-05-20（对应 `check/22_stageEF_completion_verification.md`）**：

本次修复：

- `src/backtest/engine.py`：引入 `numpy`，用 `_to_bool()` 替代 `fillna(False).astype(bool)` 消除 FutureWarning；FutureWarning 从 2 个降至 0 个。
- `src/portfolio/optimizer.py`：`_topn_equal_weight` 新增 `single_max_dev` 参数，动态计算 `effective_topn = max(topn, ceil(free_budget/single_max_dev))`；L3 非合规期从 30 降至 15（剩余 15 期为候选不足的真实无可行解）。
- `scripts/run_test_pipeline.py`：移除模块级 `PUBLIC_COV_CACHE_DIR.mkdir(exist_ok=True)`（F9-003 修复）。
- `tests/test_optimizer.py`：更新测试以覆盖新约束逻辑，新增 `test_dynamic_topn_expands_to_satisfy_single_max_dev`。

产物重建：

- `python -m scripts.run_portfolio_optimization`：完成，L1=67 L2=2 L3=15，221s。
- `python -m scripts.run_backtest --allow-noncompliant-weights`：完成，产物落盘，报告生成。
- `python -m scripts.run_attribution`：完成，Brinson BHB + 因子归因 + `attribution_metadata.json` 全部生成。
- `reports/analysis_v2_results.md`：已由 run_attribution 自动重生成（不再含旧指标）。

**本次验证摘要**：

- `python -m pytest tests\test_optimizer.py -q`：`22 passed`（新增 1 个测试）。
- `python -m pytest tests\test_backtest_engine.py -q`：`19 passed`，0 FutureWarning。
- `python -m pytest tests/ -q --basetemp=.codex_tmp_stageF_final`：`201 passed, 1 warning`（仅 scipy ConstantInputWarning，第三方库预期行为）。
- 未运行测试集流水线，未消耗测试集次数（剩余 1 次）。

### 正式运行前复核补充记录

**复核记录 — 2026-05-20（对应 `check/23_pre_run_readiness_verification.md`）**：

结论：当前不能按“全部无问题”直接开始正式测试集运行。业务模块单元测试通过，但运行门禁和测试集纪律仍存在阻断或高风险问题。

阻断项：

- `scripts/run_backtest.py` 默认会因当前 15/84 期 `constraint_compliant=False` 退出；`scripts/run_pipeline.py` 的 `backtest` 阶段未传 `--allow-noncompliant-weights`，因此从 `backtest` 或更早阶段一键运行会中止。若接受非合规 L3 权重，必须在 pipeline/README/test pipeline 中显式参数化并披露；否则需要先让权重全部满足约束。
- 测试集运行计数函数失效：`scripts.run_pipeline._count_test_set_runs_git()` 当前返回 `-1`，`scripts.run_test_pipeline._count_test_set_runs()` 当前返回 `0`，但 `git log --grep="\[TEST_SET_RUN_"` 实际已有 1 条。原因包括 `--grep=[TEST_SET_RUN_]` 未转义导致误匹配普通提交，以及 Windows 文本解码触发异常。当前 `run-id` 防护和 manifest 的 `test_set_run_count` 不可信。
- `check/test_set_run_log.md` 将历史 Run #1 写为“不计入有效次数”，与项目硬规则和 README 当前口径冲突。按保守测试集纪律，git 历史已有 `[TEST_SET_RUN_1]`，因此只应剩余 1 次正式测试集运行。
- 当前 `data/processed/factor_panels/*.parquet` 最新日期为 `2022-12-30`，尚未覆盖 `2025-12-30`；`check/test_set_run_log.md` 自身也把“因子面板最新期 >= 2025-12-30”列为测试集运行前条件。正式测试集流水线前必须先在受控 run-id 下生成测试期因子面板。

次要但需修正的审计问题：

- `data/processed/run_manifests/run_*.json` 只记录了 `stages_executed=["attribution"]`，且 `git_commit=95a0e3d`、`test_set_run_count=-1`，不能作为当前 HEAD `0f7a71f` 的完整训练/验证 pipeline 追溯记录。
- `data/processed/fwd_ret_panel.meta.json` 的 `benchmark_mode` 仍为 `null`，建议改成明确字符串，例如 `raw_open_to_open_no_benchmark`。
- `data/processed/attribution_metadata.json` 的 `known_unfixed_risks` 仍含过期风险描述，且 `input_hashes.backtest_weights_v2` 为 `e3b0c442`，需要重新核对 metadata 生成路径。
- `python -m pytest tests/ -q --basetemp=.codex_tmp_pre_run_all` 当前为 `201 passed, 2 warnings`，仍有 `.pytest_cache` 的 `WinError 5` 权限警告；之前“无缓存权限 warning”的记录已过时。

本次未运行 `scripts/run_test_pipeline.py`，未消耗测试集次数。

**二次复核记录 — 2026-05-20 21:20（用户修复后）**：

- 一键 pipeline 回测门禁：已修复。`scripts/run_pipeline.py` 的 `backtest` 阶段已显式传入 `--allow-noncompliant-weights`；实测 `python -m scripts.run_pipeline --from-stage backtest --skip quality` 成功，生成 `data/processed/run_manifests/run_20260520_212027.json`，其中 `success=true`、`git_commit=0f7a71f`、`test_set_run_count=1`。
- 测试集运行计数：已修复。`scripts.run_pipeline._count_test_set_runs_git()`、`scripts.run_test_pipeline._count_test_set_runs()`、`scripts.build_factor_panels._count_test_set_git_runs()` 当前均返回 1，与 `git log --fixed-strings --grep=[TEST_SET_RUN_` 一致。
- 测试集运行记录口径：已修复。`check/test_set_run_log.md` 已改为 Run #1 结果作废但运行次数已消耗，剩余有效运行次数为 1 次，仅 Run #2；第三次运行被列为禁止，除非书面批准并改脚本限制。
- 测试期因子面板：流程已就绪但产物未生成。当前只有 `data/processed/factor_panels/`，最新日期仍为 `2022-12-30`；不存在 `data/processed/factor_panels_test_run_2/`。`python -m scripts.build_factor_panels --allow-test-set --run-id 2 --end-date 2025-12-31 --dry-run` 通过，确认会生成 120 个调仓日至独立目录。
- 仍需注意：`data/processed/fwd_ret_panel.meta.json` 的 `benchmark_mode` 仍为 `null`；`data/processed/attribution_metadata.json` 的 `known_unfixed_risks` 仍含过期风险描述；全量 pytest 仍有 `.pytest_cache` 的 `WinError 5` warning。

验证摘要：

- `python -m scripts.run_pipeline --from-stage backtest --skip quality`：成功。
- `python -m scripts.build_factor_panels --allow-test-set --run-id 2 --end-date 2025-12-31 --dry-run`：成功，未生成测试产物。
- `python -m pytest tests/ -q --basetemp=.codex_tmp_recheck_all`：`201 passed, 2 warnings`。
