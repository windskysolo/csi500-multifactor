# 阶段 9：复现、测试与发布前清单审查

> 审查日期：2026-05-20  
> 审查范围：`README.md`、`requirements.txt`、`.gitignore`、`scripts/run_pipeline.py`、`scripts/run_test_pipeline.py`、`scripts/build_factor_panels.py`、`scripts/run_factor_evaluation.py`、`tests/`、`src/config.py`、`check/test_set_run_log.md`、当前 git 状态。  
> 明确未做：未运行 `scripts/run_test_pipeline.py`，未运行 2023-2025 测试集，未重建 raw/processed/factor/portfolio/backtest/attribution 产物，未修改业务代码。

## 阶段结论

阶段 9 不通过。

当前项目已经补上了 `README.md`、`requirements.txt`、`.gitignore`、测试集运行记录和 3 个基础测试文件，这是阶段 0/1 相比上一轮的明确进展；`python -m pytest tests/ -q` 当前结果为 `35 passed`。但发布前复现闭环仍不成立：一键 pipeline 不是从 raw 到最终报告的全流程，README 的下载/转换命令与脚本 CLI 不匹配，信号/优化/回测/归因仍依赖 notebook 手工执行，测试流水线仍会覆盖通用产物路径，且测试集运行次数只靠 git 历史计数，不能阻止同一 `--run-id` 在 commit 前被本地重复运行。

因此，在前 8 个阶段的 Blocker/High 修复前，当前项目不应进入发布或第二次测试集评估。

## 只读审查摘要

- `README.md` 和 `requirements.txt` 已存在，且 README 明确了 Tushare Pro、H00905.CSI、SW2021、训练/验证/测试期和测试集纪律。
- `.gitignore` 已覆盖 `data/raw/`、`data/processed/`、`*.parquet`、`__pycache__/`、`.pytest_cache/` 等路径。
- `git ls-files data` 和 `git ls-files "*.csv" "*.parquet"` 当前均为 0，说明数据/Parquet 产物已经不再作为 tracked 文件出现。
- `git ls-files "*__pycache__*" "*.pyc"` 仍返回 27 个 tracked pyc 文件；`git status` 也显示 `src/__pycache__/config...pyc`、`src/signal/__pycache__/combiner...pyc` 被修改。
- `git log --oneline --grep='\[TEST_SET_RUN_' --all` 当前只有 1 条：`80708d4 [TEST_SET_RUN_1] 测试集评估完成 (2023-2025)`。
- `python -m pytest tests/ -q` 结果为 `35 passed`，但伴随 `.pytest_cache` 权限 warning：`could not create cache path ... WinError 5 拒绝访问`。
- 当前测试文件只有 `tests/test_pit.py`、`tests/test_transaction.py`、`tests/test_universe.py`，尚无评价、信号、优化、回测引擎、指标、归因测试。

## F9-001 一键复现入口不是有效的 raw → processed → evaluation 流水线

**严重度**：Blocker

**证据**：

- `scripts/run_pipeline.py:48-80` 的阶段只有 `download`、`convert`、`quality`、`factors`、`evaluate`。
- `scripts/run_pipeline.py:52-54` 调用 `scripts.download_tushare` 时 `extra=[]`。
- `scripts/download_tushare.py:607-622` 在没有命令参数时只打印文档并 `sys.exit(0)`；真正下载全量数据需要 `all`。
- `scripts/run_pipeline.py` 没有调用 `scripts.download_supplement`；而 `scripts/download_supplement.py:375-389` 也要求传入 `all` 才会下载补充数据。
- `README.md:34-40` 写的是 `python -m scripts.download_tushare`、`python -m scripts.download_supplement`、`python -m scripts.csv_to_parquet`，但三个脚本当前都需要显式命令参数；`csv_to_parquet.py:1359-1367` 要求 positional `module`。
- `scripts/run_pipeline.py:61-68` 的 `quality` 阶段是 `module=None`，会打印“尚未实现”后跳过。

**影响**：

用户或审查人按 README 或 `python -m scripts.run_pipeline` 执行时，下载阶段可能以退出码 0“成功”但没有下载任何数据，补充数据不会被下载，CSV 转 Parquet 命令也缺少必需参数。这个入口会制造“流水线跑通”的假象，无法证明项目能从 raw 数据重建 processed 数据。

**修复建议**：

1. 将 `scripts/run_pipeline.py` 的下载阶段拆成 `download_core` 和 `download_supplement`，分别调用 `scripts.download_tushare all` 和 `scripts.download_supplement all`。
2. `convert` 明确调用 `scripts.csv_to_parquet all`；README 同步写成带参数命令。
3. 未实现的 `quality` 阶段不应静默跳过；要么实现 `scripts/run_data_quality.py`，要么要求用户用 `--skip quality` 显式跳过。
4. 每个阶段结束后增加关键产物存在性检查，例如 `daily_quote.parquet`、`index_quote.parquet`、`stock_status.parquet`、`index_member.parquet`。

**验证方式**：

- 在不运行测试集的前提下，执行 dry-run 或小样本 fixture pipeline，确认每个阶段调用的 CLI 参数正确。
- 构造缺失 `data/processed/daily_quote.parquet` 的场景，pipeline 应失败而不是继续进入因子阶段。

## F9-002 processed → 信号/组合/回测/归因没有脚本化发布链路

**严重度**：Blocker

**证据**：

- `scripts/run_pipeline.py:48-80` 只覆盖到 `evaluate`，没有 `signal`、`covariance`、`portfolio`、`backtest`、`attribution`、`report` 阶段。
- `README.md:52-57` 要求按顺序手工运行 `notebooks/02_factor_evaluation.ipynb` 到 `notebooks/06_attribution.ipynb`。
- `scripts/` 下没有训练/验证期专用的 `run_signal_combination.py`、`run_portfolio_optimization.py`、`run_backtest.py`、`run_attribution.py`。
- 当前唯一覆盖信号、优化和回测的脚本是 `scripts/run_test_pipeline.py`，但它面向 2023-2025 测试集，不应作为常规复现入口。

**影响**：

阶段 9 要求“能从 processed 数据重建因子、信号、组合、回测和归因”。当前只能稳定重建到因子评价，后半段仍依赖 notebook 单元格执行顺序、全局变量和人工判断。notebook 可以保留为探索与展示，但不能作为发布前唯一复现入口。

**修复建议**：

1. 增加训练/验证期脚本化入口：
   - `scripts/run_signal_combination.py`
   - `scripts/run_portfolio_optimization.py`
   - `scripts/run_backtest.py`
   - `scripts/run_attribution.py`
2. `scripts/run_pipeline.py` 增加上述阶段，并默认只覆盖 `TRAIN_START` 到 `VALID_END`，不得触碰测试集。
3. notebook 改为读取脚本产物展示，不再承担生产产物生成职责。
4. 每个脚本输出 metadata，记录输入文件、样本期、配置、git commit、生成时间和 run id。

**验证方式**：

- 在不运行测试集的前提下，执行 `python -m scripts.run_pipeline --from-stage signal` 能生成训练/验证期信号、组合、回测、归因产物。
- 删除 notebook 输出后，仅通过脚本仍能重建 `reports/analysis_v2_results.md` 所需全部数据表。

## F9-003 测试流水线仍会覆盖通用训练/验证产物路径

**严重度**：Blocker

**证据**：

- `scripts/run_test_pipeline.py:55-60` 将 `FWD_CACHE`、`SIGNAL_IC_IR_PATH`、`SIGNAL_EQUAL_PATH`、`IC_SERIES_PATH`、`WEIGHTS_OPT_PATH`、`WEIGHTS_BL_PATH` 指向 `data/processed/` 下通用文件名。
- `scripts/run_test_pipeline.py:158` 注释仍写“全期覆盖”。
- `scripts/run_test_pipeline.py:190-195` 直接写入通用合成信号和 IC 序列。
- `scripts/run_test_pipeline.py:342-343` 直接写入通用组合权重。
- 只有回测 NAV、metrics、trades、actual weights 使用了 `_test` 后缀：`scripts/run_test_pipeline.py:64-70`。

**影响**：

第二次测试集运行会把训练/验证期通用信号、IC 序列、组合权重和协方差缓存扩展为 2016-2025 全期产物。之后任何 notebook 或报告如果读取通用路径，都可能混用测试期构建的产物，破坏测试集纪律和发布复现性。

**修复建议**：

1. 所有测试集输出写入独立目录，例如 `data/processed/test_runs/run_2/`。
2. 测试流水线启动时检查目标路径，禁止写入训练/验证通用文件。
3. 通用路径只保存训练/验证期产物，最大日期不得超过 `VALID_END`。
4. 输出 `test_run_metadata.json`，记录 `run_id`、输入训练/验证产物 hash、测试输出 hash、代码 commit 和参数快照。

**验证方式**：

- 对 `scripts/run_test_pipeline.py` 增加路径隔离单元测试：任何测试输出路径不得等于通用训练/验证路径。
- 测试流水线 dry-run 后，`composite_signal_ic_ir.parquet`、`portfolio_weights_optimized.parquet` 的最大日期仍不得超过 `VALID_END`。

## F9-004 测试集运行次数门禁不具备本地防重复能力

**严重度**：High

**证据**：

- `scripts/run_test_pipeline.py:464-471` 仅通过 `git log --grep=[TEST_SET_RUN_` 统计已完成次数。
- `scripts/run_test_pipeline.py:493-498` 只要求 `--run-id == completed + 1`。
- `check/test_set_run_log.md:24-31` 要求 Run #2 运行前必须确认上游修复并预登记。
- `check/test_set_run_log.md:45-53` 的防误用清单只是文档，不会被脚本读取或强制执行。

**影响**：

如果用户运行 `--run-id 2` 后尚未 commit，则 git 历史仍只有 Run #1，脚本会允许再次运行 `--run-id 2`。这会让“最多 2 次”的纪律在本地被绕过，尤其是在调试脚本或修复路径时最容易发生。

**修复建议**：

1. 脚本启动时读取 `check/test_set_run_log.md` 或独立 JSON ledger，确认 Run #2 已预登记且状态为 `pending`。
2. 运行开始即创建不可覆盖的本地锁文件，例如 `data/processed/test_runs/run_2/RUN_STARTED.json`。
3. 若 `RUN_STARTED` 存在但没有对应 git commit 标记，脚本必须拒绝再次运行，除非用户显式传入受审计的恢复参数。
4. 运行结束后自动写入 `RUN_FINISHED.json`，并打印必须提交的 commit message。

**验证方式**：

- 在临时目录模拟 git 历史只有 Run #1，第一次 `--run-id 2 --dry-run` 创建 ledger；第二次相同命令必须失败。
- 缺少预登记字段时脚本必须失败，而不是只依赖文档提醒。

## F9-005 测试覆盖不足以作为发布前门禁

**严重度**：High

**证据**：

- 当前测试文件只有：
  - `tests/test_pit.py`
  - `tests/test_transaction.py`
  - `tests/test_universe.py`
- `python -m pytest tests/ -q` 结果为 `35 passed`，但这些测试未覆盖阶段 4-8 已确认的关键风险。
- 未发现以下测试文件：`tests/test_evaluation.py`、`tests/test_signal_combiner.py`、`tests/test_optimizer.py`、`tests/test_backtest_engine.py`、`tests/test_backtest_metrics.py`、`tests/test_attribution.py`。
- 当前没有 `.github/`、`pytest.ini`、`pyproject.toml` 或等价 CI/测试配置。

**影响**：

现有 35 个测试是有价值的基础门禁，但不能防止已发现的主要结论失真问题再次出现：收益口径污染、最终因子集合不一致、冷启动方向错误、L3 fallback 违反约束、T+1 开盘调仓估值错误、缺失状态默认 FREE、归因口径与净值不一致。发布前如果只看当前 pytest 通过，会给出错误安全感。

**修复建议**：

1. 按前 8 阶段发现补齐测试：
   - `tests/test_evaluation.py`
   - `tests/test_signal_combiner.py`
   - `tests/test_optimizer.py`
   - `tests/test_backtest_engine.py`
   - `tests/test_backtest_metrics.py`
   - `tests/test_attribution.py`
2. 每个测试使用小型合成数据或 fixture，不依赖完整真实数据。
3. 增加 CI 或本地 `make test`/`python -m pytest` 门禁，至少覆盖上述单元测试。
4. 将阶段 2-8 的每个 Blocker 至少转化为一个回归测试。

**验证方式**：

- `python -m pytest tests/ -q` 通过且覆盖全部关键模块。
- 构造已知失败样例，例如 T+1 开盘价翻倍、缺失 stock_status、L3 fallback 遇停牌股，测试必须能失败。

## F9-006 产物缺少统一 run manifest 和输入版本校验

**严重度**：High

**证据**：

- `scripts/run_factor_evaluation.py:94-102` 加载或写入 `fwd_ret_panel.parquet` 时没有 metadata 校验。
- `scripts/run_factor_evaluation.py:565-583` 输出 CSV 和 `final_factors.json`，但 JSON 只包含 `final_factors`、`excluded`、`stability_weights`，没有 `run_id`、`generated_at`、`git_commit`、输入 hash、配置 hash。
- `scripts/build_factor_panels.py:348` 直接写入因子面板 parquet，没有配套记录因子版本、输入数据版本、处理参数和样本期。
- 阶段 5/6/8 已分别确认合成信号、协方差/权重、归因产物缺少可追踪 metadata。

**影响**：

当前报告、CSV、JSON、Parquet 之间已经出现过不一致。没有统一 run manifest 时，无法回答“这个报告对应哪个代码版本、哪批输入数据、哪些参数和哪个测试集运行状态”。这会直接阻断产品化发布审查。

**修复建议**：

1. 增加统一 `run_manifest.json`，训练/验证流水线每次生成一个不可覆盖 run 目录。
2. 每个阶段输出 metadata，至少包括：
   - `run_id`
   - `generated_at`
   - `git_commit`
   - `config_hash`
   - 输入文件路径和 hash
   - 输出文件路径和 hash
   - 样本期
   - 测试集运行计数
3. 加载缓存前校验 metadata；不匹配时拒绝读取或要求显式 `--recompute`。

**验证方式**：

- 修改 `src/config.py` 的关键参数后，旧缓存必须被拒绝或标记为过期。
- 任意报告表格都能追溯到同一个 `run_manifest.json`。

## F9-007 发布工作区仍含 tracked pyc 与 pytest cache 权限问题

**严重度**：Medium

**证据**：

- `.gitignore:16-19` 已忽略 `__pycache__/`、`*.py[cod]`、`.pytest_cache/`。
- 但 `git ls-files "*__pycache__*" "*.pyc"` 仍返回 27 个 tracked pyc 文件。
- `git status` 当前显示 `src/__pycache__/config.cpython-313.pyc` 和 `src/signal/__pycache__/combiner.cpython-313.pyc` 发生修改。
- 本次 `pytest` 运行出现 warning：`.pytest_cache` 下无法创建 cache 文件，`WinError 5 拒绝访问`。

**影响**：

这不会直接改变策略收益，但会让发布包混入本地解释器版本产物，并让测试输出带噪音。发布前应保证源码变更和缓存产物分离。

**修复建议**：

1. 用非破坏性方式将 tracked pyc 移出版本控制，例如 `git rm --cached`，不删除本地文件。
2. 修复或清理 `.pytest_cache` 权限。
3. 发布前执行 `git status --short`，确认 pyc 和 pytest cache 不再出现。

**验证方式**：

- `git ls-files "*__pycache__*" "*.pyc"` 无输出。
- `python -m pytest tests/ -q` 不再出现 `.pytest_cache` 权限 warning。

## 发布前阻断清单

以下清单全部满足前，不建议发布，也不建议消耗第二次测试集：

- [ ] 阶段 2-8 已确认的所有 Blocker 修复并有回归测试。
- [ ] `scripts/run_pipeline.py` 能从 raw/processed 复现到训练/验证期报告，不触碰测试集。
- [ ] 测试集流水线所有输出隔离到 `test_runs/run_N/`，不覆盖通用产物。
- [ ] 测试集运行 ledger 具备本地锁，能阻止同一 run id 在 commit 前重复运行。
- [ ] `tests/` 覆盖 PIT、评价、信号、优化、回测、指标、归因。
- [ ] 所有关键产物都有 metadata/run manifest，缓存读取前校验输入版本。
- [ ] tracked pyc、缓存、数据产物已移出版本控制。
- [ ] README 中的复现命令与脚本真实 CLI 一致。

## 通过项与保留意见

通过项：

- `README.md`、`requirements.txt`、`.gitignore`、`check/test_set_run_log.md` 已补齐。
- 数据和 Parquet 文件当前没有作为 tracked 文件出现。
- 测试集历史计数当前为 1 次，与 `check/test_set_run_log.md` 记录一致。
- `src/config.py` 已集中样本期、交易成本、行业源、核心研究参数和 `RANDOM_SEED`。
- 新增的 PIT、成本、可投资域基础测试能在不依赖完整数据的情况下运行通过。

保留意见：

这些改动改善了项目工程卫生，但尚未解决“结论可信”的核心问题。阶段 9 的发布门禁应把阶段 2-8 的 Blocker/High 作为先决条件，而不是只检查 README、requirements 和基础 pytest 是否存在。
