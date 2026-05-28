# 阶段 0-2 修复核验

> 核验日期：2026-05-20  
> 核验依据：`check/03_stage_review_findings.md`、`check/04_fix_log.md`、当前工作区源码与本地 `data/processed` 产物。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未重建 raw/processed/factor/portfolio/backtest/attribution 产物，未修改业务代码。

## 总体结论

阶段 0-2 的修复尚未全部闭环。

`check/04_fix_log.md` 中记录的若干修复已经落实到源码或文档，特别是 `daily_quote.ret` 后复权收益、完全停牌状态补全、成分股权重来源元数据、依赖声明、测试集运行记录和基础单元测试。但仍有以下未闭环项：

- 阶段 0：工作区仍混合源码、notebook、报告、测试与审查文件；tracked pyc 仍存在；`.pytest_cache` 权限 warning 仍存在。
- 阶段 1：行业下载脚本仍写“优先 CITICS，失败降级 SW2021”，与当前 SW2021 事实口径不一致；README 入口命令存在 CLI 参数问题。
- 阶段 2：`financial_pit.parquet` 当前产物未包含 `_pit_inc/_pit_bal/_pit_cf`，说明 F2-007 只完成源码修复，产物尚未重建；`CLAUDE.md` 仍保留“股票代码 6 位字符串”的旧规则；补充数据 API 默认仍是非官方代理地址。

## 核验命令摘要

- `Get-Content -Encoding UTF8 check\04_fix_log.md`
- `Select-String -Path check\03_stage_review_findings.md -Pattern "^### F[0-2]-"`
- `git -c core.excludesfile= status --porcelain=v1 --untracked-files=all`
- `git -c core.excludesfile= ls-files data`
- `git -c core.excludesfile= ls-files "*.csv" "*.parquet" "*__pycache__*" "*.pyc"`
- `git log --oneline --grep='\[TEST_SET_RUN_' --all`
- `python -m pytest tests/ -q`
- 只读 Python parquet 抽查：`daily_quote.parquet`、`stock_status.parquet`、`daily_basic.parquet`、`financial_pit.parquet`

## 阶段 0 核验

| 问题 | 修复状态 | 核验证据 | 结论 |
|---|---|---|---|
| F0-001 缺少 `.gitignore`，数据和生成产物进入 git 状态 | 部分修复 | `.gitignore` 已覆盖 `data/raw/`、`data/processed/`、`*.parquet`、`__pycache__/`、`.pytest_cache/`；`git ls-files data` 为 0；但 `git ls-files "*__pycache__*" "*.pyc"` 仍有 27 个 tracked pyc | 数据/Parquet 已清掉，缓存 pyc 未清完 |
| F0-002 工作区混合源码、notebook、报告、数据和缓存 | 未修复 | 当前 `git status` 仍有 `M notebooks/*.ipynb`、`M scripts/*.py`、`M src/*.py`、未跟踪 `reports/factor_evaluation/*.csv/md/json`、`check/*.md`、`tests/` | 尚未建立干净审查/发布基线 |
| F0-003 测试集已运行 1 次且脚本硬编码 Run 1 | 基本修复 | `check/test_set_run_log.md` 已记录 Run #1；`git log --grep` 只有 `80708d4 [TEST_SET_RUN_1]`；`scripts/run_test_pipeline.py` 已要求 `--run-id` 并生成 `run_tag = f"[TEST_SET_RUN_{args.run_id}]"` | 原硬编码问题已修复；本地防重复 ledger 仍不足，见阶段 9 |
| F0-004 `.pytest_cache/` 权限异常 | 未修复 | `python -m pytest tests/ -q` 为 `35 passed`，但仍有 `PytestCacheWarning ... WinError 5 拒绝访问` | 权限 warning 仍存在 |
| F0-005 `tests/` 无源码测试文件 | 已修复 | `tests/test_pit.py`、`tests/test_transaction.py`、`tests/test_universe.py` 存在；`python -m pytest tests/ -q` 为 `35 passed` | 基础测试已补齐；覆盖面不足另见阶段 9 |
| F0-006 本地工具设置文件进入 git 状态 | 已修复 | `git ls-files .claude/settings.local.json` 无输出；`git status -- .claude/settings.local.json` 无输出 | 已移出当前 git 状态 |

## 阶段 1 核验

| 问题 | 修复状态 | 核验证据 | 结论 |
|---|---|---|---|
| F1-001 数据源口径冲突：Tushare Pro 与 CSMAR 混用 | 基本修复 | `PROJECT_PLAN_v1.1.md`、`AGENTS.md`、`README.md` 均以 Tushare Pro 为当前数据源；残留 CSMAR 只用于 `ann_date/Annodt`、`end_date/Accper` 概念映射 | 当前事实口径基本统一 |
| F1-002 行业体系冲突：中信一级 vs SW2021 | 部分修复 | `PROJECT_PLAN_v1.1.md`、`src/config.py`、`README.md` 已统一为 SW2021；但 `scripts/download_tushare.py:13` 仍写“中信一级行业分类”，`scripts/download_tushare.py:354-372` 仍实现“优先 CITICS，失败降级 SW2021” | 文档/配置已修，下载脚本未完全修 |
| F1-003 缺少依赖和环境声明 | 已修复 | `requirements.txt` 已存在；导入冒烟测试 `import pandas, numpy, pyarrow, scipy, statsmodels, sklearn, cvxpy, tushare` 通过 | 已修复 |
| F1-004 缺少 README 或复现入口文档 | 部分修复 | `README.md` 已存在；但其中 `python -m scripts.download_tushare`、`python -m scripts.download_supplement`、`python -m scripts.csv_to_parquet` 缺少脚本要求的 `all`/模块参数 | README 文件已补，但复现命令仍不可靠 |
| F1-005 关键研究参数未集中到 `src/config.py` | 已修复 | `src/config.py` 已包含信号、评价、优化核心参数；硬编码检索未发现旧式阈值定义 | 已修复 |
| F1-006 禁用依赖门禁 | 已通过 | `rg "vectorbt|backtrader|zipline|streamlit|dash|rqalpha|jqdatasdk|joinquant|tqsdk"` 在 `src/scripts/requirements/README` 无匹配 | 未发现禁用依赖 |

## 阶段 2 核验

| 问题 | 修复状态 | 核验证据 | 结论 |
|---|---|---|---|
| F2-001 `daily_quote.ret` 不是后复权收益 | 已修复 | `scripts/csv_to_parquet.py:236-246` 用 `close_adj.groupby("ts_code").pct_change(fill_method=None)`；当前 `daily_quote.parquet` 中 `ret` 与 `close_adj` pct_change 最大差异为 0 | 源码和当前产物均已修复 |
| F2-002 `stock_status` 未覆盖完全停牌无行情日期 | 已修复 | `scripts/csv_to_parquet.py` 已有 `susp_fully` 补全逻辑；本地抽查 raw suspend 中 41,561 条不在 `daily_quote` 的完全停牌记录，全部在 `stock_status` 且 `is_suspended=True` | 源码和当前产物均已修复 |
| F2-003 成分股权重快照来源不可复现 | 基本修复 | `scripts/download_tushare.py` 已新增 `dl_index_weight()` 和 `indexweight`；`data/csi500_index_weight_201601_202512.meta.md` 存在；CSV SHA256 与元数据一致；120 期、每期 500 股、权重合计 99.984~100.019 | 本地已修复，metadata 仍是未跟踪文件，需纳入版本管理 |
| F2-004 明文 Token 和非标准 API 地址 | 部分修复 | 两个下载脚本均改为读取 `TUSHARE_TOKEN` 环境变量；`scripts/download_supplement.py` 仍默认 `SUPPLEMENT_API_URL` 为 `http://tsdata.siboer.xin/1wan`，虽有注释和环境变量覆盖 | 明文 Token 已修；非官方默认代理仍是残留风险 |
| F2-005 股票代码规则与实现不一致 | 部分修复 | `AGENTS.md` 和 `PROJECT_PLAN_v1.1.md` 已改为 Tushare `ts_code`；但 `CLAUDE.md:66` 仍写“统一为 6 位字符串（含前导零）” | 主要文档已修，`CLAUDE.md` 仍冲突 |
| F2-006 自由流通市值 NaN 未拦截 | 已修复 | `scripts/csv_to_parquet.py:342-356` 已记录 `free_share`/`log_free_float_mv` 缺失率并在超过 1% 时断言失败；当前 `daily_basic.parquet` `log_free_float_mv` 缺失 320 行，缺失率 0.0119% | 已有显式日志和阈值门禁；当前少量缺失仍存在但低于阈值 |
| F2-007 财务三表 PIT 宽表缺少源可用日追踪列 | 部分修复 | 源码已保留 `_pit_inc/_pit_bal/_pit_cf`；但当前 `data/processed/financial_pit.parquet` 读取后不含这三列 | 源码已修，当前产物未重建，不能视为完全闭环 |

## 必须继续处理

1. 先处理 F2-007：重建 `financial_pit.parquet`，确认 `_pit_inc/_pit_bal/_pit_cf` 出现在产物中。
2. 更新 `CLAUDE.md` 的股票代码规则，避免与 `AGENTS.md` 和 `PROJECT_PLAN_v1.1.md` 冲突。
3. 修改 `scripts/download_tushare.py` 的行业下载逻辑和注释，改为明确 SW2021，不再“优先 CITICS”。
4. 修正 README 的复现命令，至少使用 `download_tushare all`、`download_supplement all`、`csv_to_parquet all`。
5. 清理 tracked pyc，修复 `.pytest_cache` 权限 warning。
6. 整理工作区，将源码、notebook、报告、审查记录和测试分批提交或分批审查。

## 本次自检

- A. 高频犯错点：未运行测试集；确认测试集计数仍为 1；抽查后复权收益、完全停牌状态、PIT 源可用日列、股票代码口径。
- B. 工程质量：运行 `pytest`，35 个基础测试通过；但 `.pytest_cache` 权限 warning 未修复，测试覆盖仍不足。
- C. 统计严谨性：本次未评估收益和因子有效性，不解释任何测试集结果。
- D. 待优化事项：阶段 0-2 仍有 2 个未修复项（F0-002、F0-004）和多个部分修复项，详见上表。
