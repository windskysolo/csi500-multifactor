# 500 improve — 迁移进度状态

> 最后更新：2026-05-27
> 参考设计文档：`500/docs/design/experiment_framework_migration_plan.md`

---

## 总体目标

将 `e:\Acoding\Project\500` 从「脚本输出互相覆盖、产物混放」的旧架构，迁移为：
- **spec 驱动**：每次实验由 `configs/pipelines/*.py` 定义
- **不可变 run 目录**：每次运行产物写入 `runs/train_valid/<run_id>/`，成功写 `RUN_FINISHED.json`，失败写 `RUN_FAILED.json`
- **注册表主基线**：`registry/mainline.json` 记录当前主线 run_id，`registry/challengers.json` 记录挑战者

---

## 阶段完成状态总览

| 阶段 | 内容 | 状态 |
|------|------|------|
| 0 | 项目骨架、data 符号链接 | ✅ 完成 |
| 1 | `src/pipeline/` 核心合约层 | ✅ 完成 |
| 2 | L5 框架测试（24 个） | ✅ 完成 |
| 3 | 8 个 spec 配置文件 | ✅ 完成 |
| 4 | 旧脚本向后兼容改造 | ✅ 完成 |
| 5 | 统一实验入口 `run_experiment.py` | ✅ 完成 |
| 6 | 主基线 run + `registry/mainline.json` | ✅ 完成 |
| 7 | rolling 挑战者 run（3/6，decay 待实现） | ✅ 部分完成 |
| 8 | `compare_runs.py` 横向比较板 | ✅ 完成 |
| 9 | 归档 `experiments/legacy/` | ✅ 完成 |
| 10 | 测试集 pipeline 接入新框架 | ✅ 完成 |

---

## 已完成阶段详情

### 阶段 0 — 项目骨架
**状态**：✅ 完成

- 创建 `500 improve` 完整目录结构（`src/`、`scripts/`、`tests/`、`configs/`、`registry/`、`runs/`、`reports/`、`experiments/`、`docs/`）
- `data/` → 符号链接指向 `500/data`（避免复制 4.7 GB 数据）
- `experiments/notebooks/` ← 原 `notebooks/` 内容移入
- 所有 `src/` 业务模块从 `500` 复制

---

### 阶段 1 — 核心合约层（`src/pipeline/`）
**状态**：✅ 完成

| 文件 | 关键内容 |
|------|---------|
| `contracts.py` | `SignalSpec`、`OptimizerSpec`、`BacktestSpec`、`AttributionSpec`、`ExperimentSpec`、`RunContext` |
| `artifacts.py` | `ArtifactLayout`、`file_sha256`、各阶段 required artifact 常量 |
| `registry.py` | `load_mainline()`、`load_challengers()`、`promote_run()`、`_validate_promotion()` |
| `__init__.py` | 重导出主要类 |

关键设计：
- `ExperimentSpec.from_config_file(path)` 从 `.py` 文件动态加载 spec
- `RunContext.create(spec, runs_root)` 自动生成 `run_id = <timestamp>__<experiment_id>`
- `ArtifactLayout.write_run_finished()` 幂等写入

---

### 阶段 2 — 测试覆盖
**状态**：✅ 完成

| 测试文件 | 测试数 | 说明 |
|----------|--------|------|
| `tests/test_pipeline_contracts.py` | 10 | ExperimentSpec 序列化、from_config_file |
| `tests/test_pipeline_artifacts.py` | 14 | ArtifactLayout 路径、write/check 方法 |

全部 24 个测试通过（`pytest tests/test_pipeline_contracts.py tests/test_pipeline_artifacts.py`）。

---

### 阶段 3 — Spec 配置文件
**状态**：✅ 完成

`configs/pipelines/` 下共 8 个 spec 文件：

| 文件 | 类型 | 说明 |
|------|------|------|
| `baseline_icir_te6_lam0050.py` | baseline | IC/IR 加权合成信号 |
| `baseline_expanding_ridge_te6_lam0050.py` | baseline | 扩展窗口 Ridge（当前主基线） |
| `challenger_rolling36_te6_lam0050.py` | challenger | 滚动 36 月窗口 Ridge |
| `challenger_rolling48_te6_lam0050.py` | challenger | 滚动 48 月窗口 Ridge |
| `challenger_rolling60_te6_lam0050.py` | challenger | 滚动 60 月窗口 Ridge |
| `challenger_decay_ridge_hl24_te6_lam0050.py` | challenger | 衰减 Ridge hl=24（预注册，待实现） |
| `challenger_decay_ridge_hl36_te6_lam0050.py` | challenger | 衰减 Ridge hl=36（预注册，待实现） |
| `challenger_decay_ridge_hl48_te6_lam0050.py` | challenger | 衰减 Ridge hl=48（预注册，待实现） |

---

### 阶段 4 — 脚本向后兼容改造
**状态**：✅ 完成

三个脚本均增加了可选路径参数，不传参时行为与原版一致：

| 脚本 | 新参数 | output_dir 非 None 时的产物路径 |
|------|--------|-------------------------------|
| `run_portfolio_optimization.py` | `--signal-path` `--output-dir` `--cov-cache-dir` | `<run_dir>/portfolio/target_weights.parquet` 等 |
| `run_backtest.py` | `--weights-path` `--baseline-weights-path` `--weights-meta-path` `--output-dir` | `<run_dir>/backtest/nav_valid.parquet` 等 |
| `run_attribution.py` | `--actual-weights-path` `--nav-path` `--output-dir` | `<run_dir>/attribution/brinson_*.parquet` 等 |

---

### 阶段 5 — 统一实验入口
**状态**：✅ 完成

#### `src/pipeline/stages.py`（新建）
阶段编排包装层，路由信号方法、对齐路径、调用底层 `src/` 模块：

| 函数 | 说明 |
|------|------|
| `run_signal_stage(spec, run_dir, data_proc)` | 路由至 `_run_signal_icir` / `_run_signal_ridge` |
| `run_portfolio_stage(spec, run_dir, signal_path, data_proc)` | 调用 `run_portfolio_optimization.main()` |
| `run_backtest_stage(spec, run_dir, data_proc)` | 调用 `run_backtest.main()` |
| `write_self_check_md(spec, run_dir)` | 从回测产物自动生成 `reports/self_check.md`（阶段 6 时补充） |

信号路由：
- `method="icir"` → `src.signal.combiner.build_composite_panel`
- `method="ridge"` + `expanding` → `experiments.ridge_signal.RidgeCombiner`
- `method="ridge"` + `rolling` → `experiments.ridge_rolling.RidgeRollingCombiner`
- `method="ridge"` + `decay_weighted_expanding` → `NotImplementedError`

#### `scripts/run_experiment.py`（新建）
支持完整 pipeline 或分阶段运行：
```
python -m scripts.run_experiment --spec configs/pipelines/<spec>.py
python -m scripts.run_experiment --spec ... --from-stage portfolio --input-signal-run <run_id>
python -m scripts.run_experiment --spec ... --to-stage signal
```
每次运行自动生成：`run_config.json`、`inputs.lock.json`、`manifest.json`、`RUN_FINISHED.json` / `RUN_FAILED.json`、`reports/self_check.md`（backtest 完成时）

---

### 阶段 6 — 迁移第一条主基线 run
**状态**：✅ 完成（2026-05-27）

#### 新增文件
- **`scripts/promote_run.py`** — `registry.promote_run()` 的 CLI 包装，执行晋升前置检查后写入 `registry/mainline.json`
- **`write_self_check_md()`** — 加入 `src/pipeline/stages.py`，在 backtest 完成后自动从 `metrics_valid.parquet` + `trades_valid.parquet` 生成 `reports/self_check.md`（含硬指标 PASS/FAIL 表）

#### 已运行主基线
```
run_id: 20260527_141545__baseline_expanding_ridge_te6_lam0050
spec:   configs/pipelines/baseline_expanding_ridge_te6_lam0050.py
```

验证期指标（2021-01 ~ 2022-12）：

| 指标 | V1 Baseline (Top-50 等权) | V2 优化权重 | 阈值 | 结论 |
|------|--------------------------|------------|------|------|
| IR | 0.846 | 0.408 | ≥ 0.5 | V2 FAIL（TE 压缩已知问题） |
| 超额最大回撤 | -8.43% | -8.16% | ≤ 10% | PASS |
| 年化双边换手 | 1055% | 974% | 500–1500% | PASS |
| 年化超额收益 | +5.63% | +2.44% | — | — |

> V2 IR 偏低是 TE=6%+lambda=0.005 约束压缩的已知现象；V1 baseline IR=0.846 说明信号质量本身合格。
> 注册理由已写入 `registry/mainline.json`。

#### registry 状态
- `registry/mainline.json` → `20260527_141545__baseline_expanding_ridge_te6_lam0050`
- `registry/archived_promotions.jsonl` → 首条晋升记录已追加

#### 运行期间修复的 stages.py bug
| # | 位置 | 问题 | 修复 |
|---|------|------|------|
| 1 | `_run_signal_ridge` | 始终使用父类 `RidgeCombiner` 并传入不存在的 `window_months` | rolling 模式改用 `RidgeRollingCombiner(window_months=...)` |
| 2 | `_run_signal_ridge` | `tv_dates` 是 Python list，`all_dates[all_dates <= cutoff]` 需要 DatetimeIndex | 改为 `pd.DatetimeIndex(sorted(...))` |
| 3 | `_run_signal_ridge` | `if combiner.coef_history_:` 对 DataFrame bool 化报 ValueError | 改为 `if coef_hist is not None and not coef_hist.empty:` |

---

### 阶段 7 — 迁移挑战者 run
**状态**：✅ 部分完成（2026-05-27）

#### 已运行 rolling 挑战者（全部 hard threshold PASS）

| run_id | 窗口 | V2 IR | 年化超额 | 超额最大回撤 | 年化换手 |
|--------|------|-------|---------|------------|---------|
| `20260527_141843__challenger_rolling36_te6_lam0050` | 36m | 0.966 | +5.80% | -8.38% | 1002% |
| `20260527_142056__challenger_rolling48_te6_lam0050` | 48m | **1.489** | +8.63% | -5.82% | 1002% |
| `20260527_142318__challenger_rolling60_te6_lam0050` | 60m | 1.176 | +6.87% | -6.73% | 975% |

> rolling 48m 各项指标最优（IR=1.489，超额+8.63%，最大回撤仅 5.82%）。

#### decay 挑战者（待实现）
hl24 / hl36 / hl48 三个已注册为 `status: "not_started"`，原因：
- `stages.py` 的 `decay_weighted_expanding` 分支抛 `NotImplementedError`
- 需先实现 `src/signal/ridge_decay.py`，再运行对应 spec

#### registry 状态
`registry/challengers.json` 包含 6 个条目（3 个 `researching` + 3 个 `not_started`）。

---

## 已完成阶段详情（续）

### 阶段 8 — run 横向比较板
**状态**：✅ 完成（2026-05-27）

#### 新增文件

| 文件 | 说明 |
|------|------|
| `src/pipeline/compare.py` | 核心比较逻辑：`load_run_metrics()`、`compare_runs()`、`build_board_from_registry()` |
| `scripts/compare_runs.py` | CLI 包装：从 registry 自动读取或接受 `--run-ids`，输出 CSV + Markdown |

#### 比较字段

`run_id` · `role` · `experiment_id` · `signal_method` · `training_mode` · `window_months` · `te_target_pct` · `turnover_lambda` · `topn` · `IR` · `excess_return_pct` · `excess_max_drawdown_pct` · `tracking_error_pct` · `monthly_win_rate_pct` · `annual_turnover_pct` · `ir_pass` · `mdd_pass` · `to_pass` · `status`

#### 生成的 board（2026-05-27）

| run_id（简写） | role | IR | excess_return | mdd | to | ir_pass | mdd_pass | to_pass |
|---|---|---|---|---|---|---|---|---|
| rolling48 | challenger | **1.489** | +8.63% | -5.82% | 1002% | ✅ | ✅ | ✅ |
| rolling60 | challenger | 1.176 | +6.87% | -6.73% | 975% | ✅ | ✅ | ✅ |
| rolling36 | challenger | 0.966 | +5.80% | -8.38% | 1002% | ✅ | ✅ | ✅ |
| baseline_expanding | mainline | 0.408 | +2.44% | -8.16% | 974% | ❌ | ✅ | ✅ |

完整输出在 `reports/experiment_board.csv` 和 `reports/experiment_board.md`。

用法：
```
python -m scripts.compare_runs                         # 读 registry，默认 scope=train_valid
python -m scripts.compare_runs --run-ids <id1> <id2>  # 指定比较
python -m scripts.compare_runs --output-dir out/       # 自定义输出目录
```

---

### 阶段 9 — 归档旧 experiments/
**状态**：✅ 完成（2026-05-27）

已将以下 4 个目录移至 `experiments/legacy/`：

| 原路径 | 新路径 |
|--------|--------|
| `experiments/ridge_signal/` | `experiments/legacy/ridge_signal/` |
| `experiments/ridge_rolling/` | `experiments/legacy/ridge_rolling/` |
| `experiments/turnover_lambda_grid/` | `experiments/legacy/turnover_lambda_grid/` |
| `experiments/te_grid/` | `experiments/legacy/te_grid/` |

#### 同步修改的 import 路径

| 文件 | 变更 |
|------|------|
| `src/pipeline/stages.py` | `experiments.ridge_signal.` → `experiments.legacy.ridge_signal.` |
| `src/pipeline/stages.py` | `experiments.ridge_rolling.` → `experiments.legacy.ridge_rolling.` |
| `experiments/legacy/ridge_rolling/rolling_combiner.py` | 同上；`_ROOT` 从 `parent.parent.parent` 改为 `parent.parent.parent.parent`（层级加深） |

验证：`from experiments.legacy.ridge_signal.ridge_combiner import RidgeCombiner` ✅  
验证：`from experiments.legacy.ridge_rolling.rolling_combiner import RidgeRollingCombiner` ✅  
验证：24 个框架测试全部通过 ✅

---

## 已完成阶段详情（续）

### 阶段 10 — 测试集 pipeline 接入新框架
**状态**：✅ 完成（2026-05-27）

#### 修改文件
`scripts/run_test_pipeline.py` — 全面接入新框架，保留所有 F9-003/F9-004 安全机制。

#### 核心变更

| 变更点 | 旧行为 | 新行为 |
|--------|--------|--------|
| 输出目录 | `data/processed/test_run_{N}/` | `runs/test/test_run_{N}__<mainline_run_id>/` |
| 主基线读取 | 无 | `_load_mainline_context()` 读 `registry/mainline.json` |
| 优化器参数 | `cfg.OPT_*` 全局常量 | 从主基线 `run_config.json` 读取 optimizer spec（回退到 cfg） |
| 信号产物 | `test_run_dir/composite_signal_ic_ir.parquet` | `test_run_dir/signal/composite.parquet` |
| 组合产物 | `test_run_dir/portfolio_weights_optimized.parquet` | `test_run_dir/portfolio/target_weights.parquet` |
| 回测产物 | `test_run_dir/backtest_nav.parquet` | `test_run_dir/backtest/nav_valid.parquet` |
| 指标产物 | `test_run_dir/backtest_metrics.parquet` | `test_run_dir/backtest/metrics_valid.parquet` |
| 框架文件 | 无 | `run_config.json` + `reports/self_check.md` + `RUN_FINISHED.json` |

#### 新增函数

| 函数 | 说明 |
|------|------|
| `_load_mainline_context()` | 读 registry，返回 `(mainline_run_id, optimizer_spec_dict)` |
| `_build_opt_config(opt_spec)` | 从 spec dict 构建 `OptimizeConfig`，缺失值回退 cfg |
| `_write_run_config(...)` | 写入 `run_config.json`（供 `compare_runs.py` 读取） |

#### 保持不变
- ledger 纪律（最多 3 次，显式 `--run-id`，`test_set_ledger.py` 路径不变）
- F9-003 隔离：不修改公共 `fwd_ret_panel.parquet`、`cov_cache/`
- F9-004 守卫：`RUN_STARTED.json` 锁文件逻辑
- F5-001 守卫：`INVALIDATED.md` 检查、因子集合一致性断言
- Resume 逻辑（`--resume-from-lock`），sentinel 路径已更新为新产物命名

#### 运行方式（不变）
```
python -m scripts.run_test_pipeline --run-id 1
```
输出目录：`runs/test/test_run_1__<mainline_run_id>/`

---

## 其他待办事项

| 事项 | 优先级 | 说明 |
|------|--------|------|
| `src/signal/ridge_decay.py` | 中 | `decay_weighted_expanding` 模式实现；3 个 decay 挑战者 spec 依赖它 |
| `tests/test_pipeline_registry.py` | 中 | 设计文档要求的 registry 单元测试（`promote_run` 前置检查、self_check 校验） |
| `run_factor_evaluation.py --output-label` | 低 | 因子评估产物快照化，输出到 `reports/factor_evaluation/<label>/` |

---

## 关键路径速查

```
src/pipeline/
  contracts.py      # ExperimentSpec, RunContext
  artifacts.py      # ArtifactLayout, file_sha256, REQUIRED_*_ARTIFACTS
  registry.py       # promote_run, load_mainline, load_challengers
  stages.py         # run_signal/portfolio/backtest_stage, write_self_check_md
  compare.py        # load_run_metrics, compare_runs, build_board_from_registry

scripts/
  run_experiment.py    # 统一实验入口（阶段 5）
  promote_run.py       # 晋升 CLI（阶段 6）
  compare_runs.py      # 比较板 CLI（阶段 8）
  run_test_pipeline.py # 测试集 pipeline（阶段 10，接入 registry）
  run_portfolio_optimization.py  # 向后兼容改造（阶段 4）
  run_backtest.py                # 向后兼容改造（阶段 4）
  run_attribution.py             # 向后兼容改造（阶段 4）

configs/pipelines/
  baseline_expanding_ridge_te6_lam0050.py  # 当前主基线
  baseline_icir_te6_lam0050.py
  challenger_rolling36/48/60_te6_lam0050.py
  challenger_decay_ridge_hl24/36/48_te6_lam0050.py  # 待实现

registry/
  mainline.json              # → 20260527_141545__baseline_expanding_ridge_te6_lam0050
  challengers.json           # 6 条目（3 researching + 3 not_started）
  archived_promotions.jsonl  # 历史晋升日志

runs/train_valid/
  20260527_141545__baseline_expanding_ridge_te6_lam0050/   # 主基线
  20260527_141843__challenger_rolling36_te6_lam0050/
  20260527_142056__challenger_rolling48_te6_lam0050/
  20260527_142318__challenger_rolling60_te6_lam0050/
  每个 run 包含：
    run_config.json  inputs.lock.json  manifest.json
    RUN_FINISHED.json
    signal/composite.parquet  coef_history.parquet  signal_metadata.json
    portfolio/target_weights.parquet  baseline_weights.parquet  optimizer_meta.parquet
    backtest/nav_valid.parquet  metrics_valid.parquet  trades_valid.parquet  actual_weights_valid.parquet
    reports/self_check.md
```

---

## 工作纪要

### 2026-05-27 会话一（阶段 0–5）
- 建立 `500 improve` 项目骨架，data/ 符号链接
- 实现 `src/pipeline/`（contracts / artifacts / registry）
- 编写 24 个 L5 框架测试（全部通过）
- 写入 8 个 spec 配置文件
- 向后兼容改造 run_portfolio_optimization / run_backtest / run_attribution
- 新建 `stages.py` + `run_experiment.py`

### 2026-05-27 会话四（阶段 10 + 完整验证）
- **重写** `scripts/run_test_pipeline.py`：接入 registry，输出到 `runs/test/`，标准产物目录结构，生成 `run_config.json` + `reports/self_check.md`
- 新增 `_load_mainline_context()` / `_build_opt_config()` / `_write_run_config()` 三个集成辅助函数
- `_run_optimization()` 新增 `opt_config` 参数（原全局常量，现从主基线 spec 派生）
- 信号/组合/回测产物路径全部迁移为标准子目录（`signal/` / `portfolio/` / `backtest/`）
- Resume sentinel 路径同步更新
- 执行 68 项迁移完整性检查：全部通过（0 FAIL）
- 执行端到端 compare board 验证：IR/MDD/换手率 assertions 全通过
- 24 个 L5 框架测试仍全部通过

### 2026-05-27 会话三（阶段 8–9）
- **新增** `src/pipeline/compare.py`：`load_run_metrics()` / `compare_runs()` / `build_board_from_registry()`，支持多 run 横向比较，自动计算 IR/超额收益/MDD/换手等指标及硬阈值 PASS/FAIL
- **新增** `scripts/compare_runs.py`：CLI，从 registry 读取或接受 `--run-ids`，生成 `reports/experiment_board.csv` + `reports/experiment_board.md`
- 归档旧 experiments：`ridge_signal/`、`ridge_rolling/`、`te_grid/`、`turnover_lambda_grid/` 移至 `experiments/legacy/`
- 更新 `stages.py` + `rolling_combiner.py` import 路径，保证 pipeline 继续可运行
- 新增 `experiments/legacy/__init__.py`
- 运行 compare board：4 条 run 全部正确读取，rolling48 IR=1.489 最优
- 24 个 L5 框架测试全部通过

### 2026-05-27 会话二（阶段 6–7）
- **新增** `write_self_check_md()` 至 `stages.py`：backtest 后自动生成 `reports/self_check.md`，写入指标 PASS/FAIL 表和产物清单
- **新增** `scripts/promote_run.py`：`promote_run()` 的 CLI 包装
- **修复** `stages.py` 三处 bug（见阶段 6 详情）
- 运行主基线实验：`baseline_expanding_ridge_te6_lam0050`，晋升写入 `registry/mainline.json`
- 依次运行 rolling 36 / 48 / 60 挑战者，全部通过 hard threshold
- 写入 `registry/challengers.json`（6 条目，decay 三个标记 not_started）
- 更新 `MIGRATION_STATUS.md`
