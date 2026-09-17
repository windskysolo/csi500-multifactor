# 实验框架迁移详细计划

> 目标：让项目从当前“主线产物、平行实验产物、临时审查产物混放”的状态，迁移到
> “主线可替换、实验可并行、产物可冻结、入口可复用”的文件框架。
>
> 本计划是迁移说明书：先解释现在是什么样，再说明每一部分迁移后去哪里，最后给出分阶段移动计划。

---

## 1. 现在的结构是什么样

当前项目实际上已经有三类东西，但它们没有被严格分开。

### 1.1 可复用代码

当前位置：

```text
src/
├── data/
├── factors/
├── evaluation/
├── signal/
├── portfolio/
├── backtest/
└── attribution/
```

当前职责：

- 读取数据；
- 构建因子；
- 做 IC / 分组 / shift test；
- 合成信号；
- 组合优化；
- 回测；
- 归因。

评价：

- 这部分方向是对的，应该保留。
- 问题不在 `src/`，而在 `scripts/` 和 `experiments/` 调用这些模块时路径和产物规则不统一。

迁移原则：

- `src/` 继续作为核心业务模块。
- 新增 `src/pipeline/`，专门负责实验契约、产物路径、registry、run manifest。
- 不把具体实验参数硬塞进 `src/`。

---

### 1.2 主管线脚本

当前位置：

```text
scripts/
├── run_pipeline.py
├── run_signal_combination.py
├── run_portfolio_optimization.py
├── run_backtest.py
├── run_attribution.py
├── run_test_pipeline.py
└── ...
```

当前职责：

- `run_pipeline.py` 串起训练/验证公共流水线；
- `run_signal_combination.py` 默认生成 IC_IR / equal 信号；
- `run_portfolio_optimization.py` 默认读取 `data/processed/composite_signal_ic_ir.parquet`；
- `run_backtest.py` 默认读取 `data/processed/portfolio_weights_optimized.parquet`；
- `run_attribution.py` 默认读取 `data/processed/backtest_weights_v2.parquet`；
- `run_test_pipeline.py` 有单独测试集纪律。

当前问题：

- 脚本默认路径都指向 `data/processed/` 的公共产物。
- 替换主线时容易靠复制 parquet 或改硬编码路径完成。
- 平行实验不能天然并行，因为多个实验会争抢同一套公共输出文件。

迁移原则：

- 短期：旧脚本保留，作为兼容入口。
- 中期：给关键脚本增加 `--input-path` / `--output-dir` 参数。
- 长期：新增 `scripts/run_experiment.py` 作为统一实验入口；旧 `run_pipeline.py` 变成调用新入口的 wrapper。

---

### 1.3 平行实验目录

当前位置：

```text
experiments/
├── ridge_signal/
├── ridge_rolling/
├── turnover_lambda_grid/
├── te_grid/
├── v1.0/
├── v1.1/
└── v1.2/
```

当前职责：

- `ridge_signal/`：expanding Ridge 信号及回测；
- `ridge_rolling/`：rolling 36/48/60 窗口实验；
- `turnover_lambda_grid/`：换手惩罚参数网格；
- `te_grid/`：TE 参数网格；
- `v1.x/`：版本归档。

当前问题：

- `experiments/*/results/` 既像临时实验结果，又像候选主线结果。
- 不同实验的产物命名不一致，例如：
  - `ridge_composite_panel.parquet`
  - `rolling_48m_composite_panel.parquet`
  - `weights_optimized.parquet`
  - `backtest_nav_valid.parquet`
- 固定目录会被重跑覆盖，导致 artifact drift。
- 某些对比直接复用旧产物，某些候选用当前代码重跑，容易产生新旧混用。

迁移原则：

- `experiments/` 不再放稳定产物。
- 稳定运行产物统一迁入 `runs/`。
- `experiments/` 以后只放：
  - 探索 notebook；
  - 临时研究脚本；
  - 旧实验归档；
  - 人读的研究说明。

---

### 1.4 共享数据和当前公共产物

当前位置：

```text
data/processed/
├── daily_quote.parquet
├── daily_basic.parquet
├── index_quote.parquet
├── index_member.parquet
├── industry.parquet
├── financial_pit.parquet
├── indicator_pit.parquet
├── factor_panels/
├── fwd_ret_panel.parquet
├── cov_cache/
├── composite_signal_ic_ir.parquet
├── portfolio_weights_optimized.parquet
├── backtest_nav.parquet
├── backtest_metrics.parquet
├── brinson_attribution.parquet
└── ...
```

当前职责混杂：

- 一部分是共享输入数据；
- 一部分是主管线输出；
- 一部分是中间缓存；
- 一部分是报告/回测结果。

迁移原则：

`data/processed/` 以后只保留“共享输入层”和“可复用缓存”：

```text
data/processed/
├── daily_quote.parquet
├── daily_basic.parquet
├── index_quote.parquet
├── index_member.parquet
├── stock_status.parquet
├── industry.parquet
├── financial_pit.parquet
├── indicator_pit.parquet
├── factor_panels/
├── fwd_ret_panel.parquet
└── cov_cache/
```

以下产物迁出到 `runs/<scope>/<run_id>/`：

```text
composite_signal_*.parquet
ic_series_all_factors.parquet
icir_weight_history.parquet
portfolio_weights_*.parquet
backtest_nav.parquet
backtest_metrics.parquet
backtest_trades_*.parquet
backtest_weights_*.parquet
brinson_*.parquet
factor_attr_*.parquet
attribution_*.json
```

---

### 1.5 报告和审查产物

当前位置：

```text
reports/
check/
docs/research/
docs/archive/
```

当前职责：

- `reports/`：因子评估、回测、rolling 实验报告；
- `check/`：阶段审查、问题排查；
- `docs/research/`：研究计划和改进思路；
- `docs/archive/`：历史审查材料。

迁移原则：

- 每个 run 自己的报告放到 `runs/.../<run_id>/reports/`。
- 横向比较报告放到 `reports/experiment_board.*`。
- 临时审查继续放 `check/YYYYMM/`，结束后可归档到 `docs/archive/check/`。
- 方法论、设计文档继续放 `docs/design/`。

---

### 1.6 tests/ — 分层单元测试

当前位置：

```text
tests/
├── test_pit.py
├── test_alt_data_pit.py
├── test_universe.py
├── test_index_quote_code.py
├── test_data_expansion_config.py
├── test_factor_time_boundary.py
├── test_preprocess.py
├── test_phase4_factors.py
├── test_phase5_factors.py
├── test_technical_factors.py
├── test_hk_hold_coverage.py
├── test_evaluation.py
├── test_signal_combiner.py
├── test_optimizer.py
├── test_backtest_engine.py
├── test_backtest_metrics.py
├── test_transaction.py
├── test_attribution.py
├── test_pipeline_release_gates.py      ← 已有 L5 框架层测试
└── test_test_pipeline_isolation.py     ← 已有 L5 框架层测试
```

测试层分类（按 `src/` 模块层次）：

| 层 | 内容 | 现有文件 |
|---|---|---|
| L0 配置 | config 常量一致性（样本期、印花税、ALT_DATA_SUBDIRS） | `test_data_expansion_config.py` |
| L1 数据 | PIT 约束、Universe 过滤、全收益指数代码校验 | `test_pit.py` `test_alt_data_pit.py` `test_universe.py` `test_index_quote_code.py` |
| L2 因子 | 时间边界（未来数据防护）、横截面预处理、财务/技术/北向因子 | `test_factor_time_boundary.py` `test_preprocess.py` `test_phase4_factors.py` `test_phase5_factors.py` `test_technical_factors.py` `test_hk_hold_coverage.py` |
| L3 评价/信号 | IC 分析、shift test、BH 校正、IC_IR 合成、冷启动 | `test_evaluation.py` `test_signal_combiner.py` |
| L4 组合/回测 | 优化器（L1/L2/L3 fallback）、T+1 引擎、绩效指标、印花税切换、Brinson | `test_optimizer.py` `test_backtest_engine.py` `test_backtest_metrics.py` `test_transaction.py` `test_attribution.py` |
| L5 框架 | pipeline 契约、artifact 锁、registry promote 校验、测试集路径隔离 | `test_pipeline_release_gates.py` `test_test_pipeline_isolation.py` + **新增 3 个** |

迁移原则：

- **L0–L4 全部保留**：这 17 个测试文件无需移动，只要 `src/` 接口不变就继续有效。
- **L5 扩展**：阶段 2 完成 `src/pipeline/` 后，新增：
  - `tests/test_pipeline_contracts.py`：`ExperimentSpec` 加载/序列化、`period_scope` 边界、禁止测试集标志；
  - `tests/test_pipeline_artifacts.py`：`ArtifactLayout` 路径生成、`manifest.json` 结构、`RUN_FINISHED.json` 幂等性；
  - `tests/test_pipeline_registry.py`：`promote_run` 前置检查（必备 artifact、self_check.md、不误用测试集）。
- **测试不产生 `runs/` 产物**：临时文件一律用 `tmp_path` fixture 或 `tempfile`，遵循现有 `test_test_pipeline_isolation.py` 的模式。

完成标准（阶段 2）：L5 新增 3 个测试文件通过 `pytest` 无报错。

---

### 1.7 logs/ — 运维日志

当前位置：

```text
logs/
├── download.log
├── download_main.log
├── download_supplement.log
├── download_alt.log
└── phase3_*.log
```

当前职责：数据下载脚本的运行日志，与研究产物无关。

迁移原则：

- **保留在 `logs/`**，不进入 `runs/`，不入 git（已在 `.gitignore`）。
- 下载脚本（`scripts/download_tushare.py` 等）继续写这里。
- 与 `runs/<run_id>/` 的 manifest 无关，不需要 hash。

---

### 1.8 reports/factor_evaluation/ — 因子库评估产物

当前位置：

```text
reports/factor_evaluation/         ← 当前主线评估（直接覆盖）
reports/factor_evaluation_factor_impl_stage1/
reports/factor_evaluation_momentum_sprint/
```

当前问题：

- 因子评估产物不属于任何 pipeline run，但仍在 `reports/` 根目录下裸放，多次评估靠目录名区分（名字不规范）。
- `scripts/run_factor_evaluation.py` 的输出路径硬编码到 `reports/factor_evaluation/`，会被直接覆盖。

迁移原则：

- `reports/factor_evaluation/` 改为**按快照子目录**组织，每次评估写到独立子目录：
  ```text
  reports/factor_evaluation/
  ├── 20260527_baseline_16factors/    ← 命名格式：YYYYMMDD_<label>
  │   ├── ic_result.csv
  │   ├── quintile_summary.csv
  │   ├── shift_result.csv
  │   ├── factor_evaluation_report.md
  │   └── final_factors.json
  └── 20260610_after_piotroski_fix/
  ```
- 旧的 `reports/factor_evaluation_factor_impl_stage1/` 等非标准命名，在阶段 9 后重命名归档。
- 这层产物**不进入 `runs/`**，是因子库级别（早于信号合成），由 `scripts/run_factor_evaluation.py` 直接生成。
- `scripts/run_factor_evaluation.py` 需增加 `--output-label` 参数以指定快照名。

---

## 2. 迁移后的结构长什么样

目标结构：

```text
E:/Acoding/Project/500/
├── src/
│   ├── data/
│   ├── factors/
│   ├── evaluation/
│   ├── signal/
│   ├── portfolio/
│   ├── backtest/
│   ├── attribution/
│   └── pipeline/
│       ├── contracts.py
│       ├── artifacts.py
│       ├── registry.py
│       ├── stages.py
│       └── compare.py
│
├── configs/
│   ├── signals/
│   ├── optimizers/
│   └── pipelines/
│
├── registry/
│   ├── mainline.json
│   ├── challengers.json
│   └── archived_promotions.jsonl
│
├── runs/
│   ├── train_valid/
│   └── test/
│
├── data/
│   ├── raw/
│   └── processed/
│
├── scripts/
│   ├── run_experiment.py
│   ├── compare_runs.py
│   ├── promote_run.py
│   ├── run_pipeline.py
│   ├── run_test_pipeline.py
│   ├── run_factor_evaluation.py    # 因子库评估（独立于 runs/，输出到 reports/factor_evaluation/<snapshot>/）
│   ├── download_tushare.py / download_*.py
│   └── test_set_ledger.py
│
├── tests/
│   ├── test_pit.py                 # L1–L4 业务层（17 个，迁移后原地保留）
│   ├── ...
│   ├── test_pipeline_contracts.py  # L5 框架层（阶段 2 后新增）
│   ├── test_pipeline_artifacts.py
│   └── test_pipeline_registry.py
│
├── experiments/
│   ├── legacy/
│   ├── notebooks/
│   └── notes/
│
├── reports/
│   ├── factor_evaluation/
│   │   └── <YYYYMMDD_label>/       # 每次评估写独立子目录，不覆盖
│   └── experiment_board.csv / .md  # 跨 run 比较汇总
│
├── logs/                           # 运维日志，不入库
├── check/
└── docs/
```

---

## 3. 每一部分迁移到哪里

### 3.1 旧路径到新路径总表

| 现在的位置 | 现在的内容 | 迁移后的位置 | 处理方式 |
|---|---|---|---|
| `src/data/` | loader / PIT / universe | `src/data/` | 保留 |
| `src/factors/` | 因子构建 | `src/factors/` | 保留 |
| `src/signal/combiner.py` | IC_IR 合成 | `src/signal/` | 保留，后续加 decay 版本 |
| `src/portfolio/` | 协方差和优化器 | `src/portfolio/` | 保留 |
| `src/backtest/` | 回测和成本 | `src/backtest/` | 保留 |
| `src/attribution/` | Brinson / 因子归因 | `src/attribution/` | 保留 |
| 无 | 实验契约/manifest/registry | `src/pipeline/` | 新增 |
| `scripts/run_signal_combination.py` | 默认信号入口 | `scripts/run_experiment.py` 调用 stage | 先兼容，后 wrapper |
| `scripts/run_portfolio_optimization.py` | 默认优化入口 | `scripts/run_experiment.py` 调用 stage | 先加参数 |
| `scripts/run_backtest.py` | 默认回测入口 | `scripts/run_experiment.py` 调用 stage | 先加参数 |
| `scripts/run_attribution.py` | 默认归因入口 | `scripts/run_experiment.py` 调用 stage | 先加参数 |
| `experiments/ridge_signal/` | Ridge expanding 实验 | `configs/pipelines/baseline_expanding_ridge_*.py` + `runs/` | 逻辑抽出，旧目录 legacy |
| `experiments/ridge_rolling/` | rolling 实验 | `configs/pipelines/challenger_rolling*.py` + `runs/` | 逻辑抽出，旧目录 legacy |
| `experiments/turnover_lambda_grid/` | lambda 网格 | `configs/optimizers/` + `runs/` | 逻辑抽出，旧目录 legacy |
| `experiments/te_grid/` | TE 网格 | `configs/optimizers/` + `runs/` | 逻辑抽出，旧目录 legacy |
| `experiments/v1.x/` | 历史版本归档 | `runs/` 或 `experiments/legacy/v1.x/` | 不急着动 |
| `data/processed/factor_panels/` | 共享因子面板 | `data/processed/factor_panels/` | 保留 |
| `data/processed/cov_cache/` | 共享协方差缓存 | `data/processed/cov_cache/` | 保留，run 内记录引用 |
| `data/processed/composite_signal_*.parquet` | 当前信号产物 | `runs/.../<run_id>/signal/` | 新 run 产出，不建议手动搬 |
| `data/processed/portfolio_weights_*.parquet` | 当前组合权重 | `runs/.../<run_id>/portfolio/` | 新 run 产出，不建议手动搬 |
| `data/processed/backtest_*.parquet` | 当前回测产物 | `runs/.../<run_id>/backtest/` | 新 run 产出，不建议手动搬 |
| `data/processed/brinson_*.parquet` | 当前归因产物 | `runs/.../<run_id>/attribution/` | 新 run 产出 |
| `reports/factor_evaluation/` | 因子库评估（IC、quintile、shift test）| `reports/factor_evaluation/<YYYYMMDD_label>/` | 改为快照子目录，不覆盖；不进入 `runs/` |
| `reports/factor_evaluation_factor_impl_stage1/` | 旧命名评估产物 | `reports/factor_evaluation/20260527_factor_impl_stage1/` | 阶段 9 重命名归档 |
| `reports/factor_evaluation_momentum_sprint/` | 旧命名评估产物 | `reports/factor_evaluation/20260527_momentum_sprint/` | 阶段 9 重命名归档 |
| `reports/ridge_rolling_experiment.md` | 某次实验报告 | `runs/.../reports/summary.md` | 新 run 生成 |
| `reports/optimizer_grid/` | 优化器网格报告 | `runs/.../reports/` 或 grid run 子目录 | 阶段 7 重组 |
| `reports/turnover_lambda_grid/` | lambda 网格报告 | `runs/.../reports/` 或 grid run 子目录 | 阶段 7 重组 |
| `scripts/run_factor_evaluation.py` | 因子库评估主入口 | 保留，增加 `--output-label` 参数 | 输出路径改为快照子目录 |
| `scripts/run_optimizer_grid.py` | 优化器网格入口 | 改为 `run_experiment.py --spec grid_spec` | 先兼容，后接入 |
| `scripts/run_optimizer_o1_recheck.py` | O1 参数复核 | `experiments/legacy/` | 已完成，归档 |
| `scripts/save_version.py` | 版本归档 | 由 `scripts/promote_run.py` 替代 | 阶段 5 后废弃 |
| `scripts/test_set_ledger.py` | 测试集计数 | 保留，阶段 10 接入新框架 | `runs/test/` 的 ledger 仍有效 |
| `scripts/plot_validation_results.py` | 回测结果画图 | 由 run 内 `reports/` 自动生成替代 | 阶段 5-6 后废弃 |
| `scripts/diagnose_optimizer.py` | 优化器问题诊断 | `experiments/legacy/` | 已完成，归档 |
| `scripts/download_tushare.py` 等 | 数据下载 | 保留，日志写 `logs/` | 不纳入 `runs/` |
| `tests/` (L0-L4, 17个) | 业务逻辑单元测试 | `tests/`（原地保留） | 不移动，不重写 |
| `tests/test_pipeline_*.py` (L5, 新增3个) | 新框架层测试 | `tests/` | 阶段 2 后新增 |
| `logs/` | 运维下载日志 | `logs/`（保留） | 不入 `runs/`，不入库 |
| `check/0526/` | 审查记录 | `check/0526/`，完成后可归档 | 保留 |

---

### 3.2 当前重点实验线如何映射

| 研究线 | 当前位置 | 迁移后的 spec | 迁移后的产物 |
|---|---|---|---|
| IC_IR 加权 | `scripts/run_signal_combination.py` / `data/processed/composite_signal_ic_ir.parquet` | `configs/pipelines/baseline_icir_te6_lam0050.py` | `runs/train_valid/<run_id>__baseline_icir_te6_lam0050/` |
| expanding Ridge | `experiments/ridge_signal/` | `configs/pipelines/baseline_expanding_ridge_te6_lam0050.py` | `runs/train_valid/<run_id>__baseline_expanding_ridge_te6_lam0050/` |
| rolling 36m | `experiments/ridge_rolling/results/rolling_36m/` | `configs/pipelines/challenger_rolling36_te6_lam0050.py` | `runs/train_valid/<run_id>__challenger_rolling36_te6_lam0050/` |
| rolling 48m | `experiments/ridge_rolling/results/rolling_48m/` | `configs/pipelines/challenger_rolling48_te6_lam0050.py` | `runs/train_valid/<run_id>__challenger_rolling48_te6_lam0050/` |
| rolling 60m | `experiments/ridge_rolling/results/rolling_60m/` | `configs/pipelines/challenger_rolling60_te6_lam0050.py` | `runs/train_valid/<run_id>__challenger_rolling60_te6_lam0050/` |
| decay Ridge 24m | 暂无 | `configs/pipelines/challenger_decay_ridge_hl24_te6_lam0050.py` | `runs/train_valid/<run_id>__challenger_decay_ridge_hl24_te6_lam0050/` |
| decay Ridge 36m | 暂无 | `configs/pipelines/challenger_decay_ridge_hl36_te6_lam0050.py` | `runs/train_valid/<run_id>__challenger_decay_ridge_hl36_te6_lam0050/` |
| decay Ridge 48m | 暂无 | `configs/pipelines/challenger_decay_ridge_hl48_te6_lam0050.py` | `runs/train_valid/<run_id>__challenger_decay_ridge_hl48_te6_lam0050/` |
| lambda 网格 | `experiments/turnover_lambda_grid/` | `configs/optimizers/turnover_lambda_grid.py` | 每个 lambda 一个 run，或一个 grid run 下多个 child run |
| TE 网格 | `experiments/te_grid/` | `configs/optimizers/te_grid_6_7_8.py` | 每个 TE 一个 run，或一个 grid run 下多个 child run |

---

## 4. 新 run 目录内部结构

每个完整 run 固定成这样：

```text
runs/train_valid/20260527_143000__baseline_expanding_ridge_te6_lam0050/
├── run_config.json
├── inputs.lock.json
├── manifest.json
├── RUN_FINISHED.json
├── signal/
│   ├── composite.parquet
│   ├── ic_detail.csv
│   ├── coef_history.parquet
│   └── signal_metadata.json
├── portfolio/
│   ├── target_weights.parquet
│   ├── baseline_weights.parquet
│   ├── optimizer_meta.parquet
│   └── cov_metadata.csv
├── backtest/
│   ├── nav_train.parquet
│   ├── nav_valid.parquet
│   ├── metrics_train.parquet
│   ├── metrics_valid.parquet
│   ├── trades_valid.parquet
│   └── actual_weights_valid.parquet
├── attribution/
│   ├── brinson_period.parquet
│   ├── brinson_industry.parquet
│   └── reconciliation.parquet
└── reports/
    ├── summary.md
    └── self_check.md
```

如果某个 run 只做信号实验，也至少写：

```text
signal/composite.parquet
manifest.json
RUN_FINISHED.json
```

如果某个 run 只做优化器实验，则必须记录它引用哪个信号 run：

```json
{
  "input_signal_run_id": "20260527_143000__baseline_expanding_ridge_te6_lam0050"
}
```

---

### 4.2 Grid / Sweep 实验的子结构

网格实验（`turnover_lambda_grid`、`te_grid`）有两种组织方式，按规模选择：

**方式 A：每个参数点独立顶层 run（推荐，参数点 ≤ 10 个时）**

```text
runs/train_valid/
├── 20260601_grid_lam0000__baseline_ridge_lam0000/
│   ├── run_config.json          # lambda = 0.000
│   ├── RUN_FINISHED.json
│   ├── backtest/metrics_valid.parquet
│   └── ...
├── 20260601_grid_lam0025__baseline_ridge_lam0025/
├── 20260601_grid_lam0050__baseline_ridge_lam0050/
└── 20260601_grid_lam0100__baseline_ridge_lam0100/
```

比较时用 `compare_runs.py` 传入多个 `--candidates` 即可横向比较。

**方式 B：grid run 下挂 child run（参数点 > 10 或批量扫描时）**

```text
runs/train_valid/
└── 20260601_sweep_lambda/
    ├── sweep_config.json         # grid_type: lambda_sweep, 参数范围声明
    ├── SWEEP_FINISHED.json
    ├── grid_summary.csv          # 所有 child 的关键指标汇总
    └── children/
        ├── lam_0000/             # 每个 child 遵守同样的 artifact contract
        │   ├── run_config.json
        │   ├── RUN_FINISHED.json
        │   ├── backtest/metrics_valid.parquet
        │   └── ...
        ├── lam_0025/
        └── lam_0050/
```

规则：

- `sweep_config.json` 记录扫描参数空间和固定信号 run 的引用；
- 每个 child 必须遵守同样的 artifact contract（必有 `RUN_FINISHED.json`）；
- `SWEEP_FINISHED.json` 仅在所有 child 成功后生成；
- 网格结果汇总写 `grid_summary.csv`，不重复散落进各 child；
- **不把网格最优点直接 promote 为主线**：必须先单独跑一个完整 full run 确认，再 promote。

---

## 5. 主线替换逻辑

### 5.1 旧方式

旧方式通常是：

1. 某个实验跑出更好的 parquet；
2. 手动复制或改路径；
3. 后续脚本默认读 `data/processed/...`；
4. 时间久了不知道现在主线到底来自哪次实验。

风险：

- 容易覆盖；
- 容易新旧产物混用；
- 不知道主线升级原因；
- 很难回滚。

### 5.2 新方式

主线替换只更新：

```text
registry/mainline.json
```

示例：

```json
{
  "slot": "mainline",
  "active_run_id": "20260527_143000__baseline_expanding_ridge_te6_lam0050",
  "scope": "train_valid",
  "promoted_at": "2026-05-27T14:30:00Z",
  "reason": "clean baseline after artifact drift audit",
  "promoted_by": "manual",
  "test_set_used": false
}
```

候选线写：

```text
registry/challengers.json
```

示例：

```json
{
  "challengers": [
    {
      "name": "decay_ridge_hl36",
      "run_id": "20260527_160000__challenger_decay_ridge_hl36_te6_lam0050",
      "status": "researching"
    },
    {
      "name": "rolling48",
      "run_id": "20260527_170000__challenger_rolling48_te6_lam0050",
      "status": "not_promoted_sample_risk"
    }
  ]
}
```

以后默认回测、报告、比较都先读 registry，而不是读 `data/processed/backtest_nav.parquet`。

---

## 6. 移动阶段计划

迁移必须小步走，不能一次大搬家。每一步都要能回滚。

### 阶段 0：冻结当前状态

目标：

- 记录当前目录、git 状态、关键产物 hash；
- 不移动、不删除。

动作：

1. 运行 `git status --short`，保存到 `check/<date>/pre_migration_git_status.txt`。
2. 生成当前关键产物 manifest：
   - `data/processed/composite_signal_ic_ir.parquet`
   - `data/processed/portfolio_weights_optimized.parquet`
   - `data/processed/backtest_nav.parquet`
   - `experiments/ridge_signal/results/*`
   - `experiments/ridge_rolling/results/*`
   - `experiments/turnover_lambda_grid/results/*`
   - `experiments/te_grid/results/*`
3. 写 `docs/design/experiment_framework_migration_plan.md`。

完成标准：

- 有冻结记录；
- 旧入口仍可运行；
- 没有移动任何旧产物。

---

### 阶段 1：新增目标目录骨架

目标：

- 建立新框架，但不接管旧逻辑。

新增目录：

```text
src/pipeline/
configs/signals/
configs/optimizers/
configs/pipelines/
registry/
runs/train_valid/
runs/test/
experiments/legacy/
```

新增文件：

```text
configs/README.md
registry/README.md
runs/README.md
src/pipeline/__init__.py
```

完成标准：

- 只是新增文件，不改旧脚本；
- `pytest` 至少能导入现有模块；
- `.gitignore` 明确忽略 `runs/**` 下的大型 parquet，但保留 `.gitkeep` / README。

---

### 阶段 2：定义 experiment contract

目标：

- 先把“实验是什么”说清楚。

新增：

```text
src/pipeline/contracts.py
src/pipeline/artifacts.py
src/pipeline/registry.py
```

核心对象：

```text
ExperimentSpec
SignalSpec
OptimizerSpec
BacktestSpec
AttributionSpec
RunContext
ArtifactLayout
```

要求：

- dataclass 即可，不引入新依赖；
- 支持转 JSON；
- 支持从 Python config 文件加载 `SPEC`；
- 记录 period scope：`train_valid` / `test_run_N`；
- 记录是否允许测试集。

完成标准：

- 能加载一个 spec；
- 能生成 run_id；
- 能创建标准 run 目录；
- 能写 `run_config.json` 和 `inputs.lock.json`。

---

### 阶段 3：建立第一批 spec

目标：

- 把当前主线和候选线先以配置形式固定下来。

新增：

```text
configs/pipelines/baseline_icir_te6_lam0050.py
configs/pipelines/baseline_expanding_ridge_te6_lam0050.py
configs/pipelines/challenger_rolling36_te6_lam0050.py
configs/pipelines/challenger_rolling48_te6_lam0050.py
configs/pipelines/challenger_rolling60_te6_lam0050.py
configs/pipelines/challenger_decay_ridge_hl24_te6_lam0050.py
configs/pipelines/challenger_decay_ridge_hl36_te6_lam0050.py
configs/pipelines/challenger_decay_ridge_hl48_te6_lam0050.py
```

注意：

- decay Ridge 先只写 spec，不一定马上实现。
- rolling 36/48/60 必须同时存在，避免只保留最优 48m。

完成标准：

- 每个 spec 都能被 contract loader 解析；
- 参数清楚写在 spec 里；
- 不运行测试集。

---

### 阶段 4：改造旧脚本为可指定输入输出

目标：

- 旧脚本不再只能写 `data/processed/`。

优先改：

```text
scripts/run_portfolio_optimization.py
scripts/run_backtest.py
scripts/run_attribution.py
```

新增参数：

```text
--signal-path
--weights-path
--actual-weights-path
--nav-path
--output-dir
--cov-cache-dir
--period train_valid
```

兼容性要求：

- 不传参数时，行为和旧版本一致；
- 传 `--output-dir runs/.../` 时，所有输出写到 run 目录；
- 不允许在 `period=train_valid` 时读取测试期信号或权重。

完成标准：

- 旧命令仍能跑；
- 新命令能把产物写到临时 run 目录；
- 不覆盖 `data/processed/`。

---

### 阶段 5：实现统一入口 `run_experiment.py`

目标：

- 让实验运行由 spec 驱动。

新增：

```text
scripts/run_experiment.py
```

第一版支持：

```text
python -m scripts.run_experiment --spec configs/pipelines/baseline_expanding_ridge_te6_lam0050.py
python -m scripts.run_experiment --spec ... --from-stage portfolio
python -m scripts.run_experiment --spec ... --to-stage backtest
```

第一版阶段：

```text
signal -> portfolio -> backtest
```

第二版再加：

```text
attribution -> report
```

完成标准：

- 能生成标准 run 目录；
- 能写 signal/portfolio/backtest 标准产物；
- 能写 `manifest.json`；
- 能写 `RUN_FINISHED.json`；
- 失败时写 `RUN_FAILED.json`，不写 `RUN_FINISHED.json`。

---

### 阶段 6：迁移一条干净主基线

目标：

- 先把 expanding Ridge 主基线跑成一个标准 run。

建议 run：

```text
configs/pipelines/baseline_expanding_ridge_te6_lam0050.py
```

输出：

```text
runs/train_valid/<run_id>__baseline_expanding_ridge_te6_lam0050/
```

完成后写：

```text
registry/mainline.json
registry/archived_promotions.jsonl
```

完成标准：

- 新 run 的验证期指标能独立复算；
- manifest 有输入 hash；
- 主线 registry 指向该 run；
- 旧 `experiments/ridge_signal/` 仍保留，不删除。

---

### 阶段 7：迁移 challenger

目标：

- 把候选实验并行化。

优先迁移：

```text
challenger_rolling36_te6_lam0050
challenger_rolling48_te6_lam0050
challenger_rolling60_te6_lam0050
challenger_decay_ridge_hl24_te6_lam0050
challenger_decay_ridge_hl36_te6_lam0050
challenger_decay_ridge_hl48_te6_lam0050
```

要求：

- rolling 36/48/60 一起比较；
- decay 24/36/48 一起比较；
- 不新增更多半衰期，除非先写研究计划；
- 所有 challenger 写入 `registry/challengers.json`。

完成标准：

- 每个 challenger 都有独立 run_id；
- 能用统一 compare 工具横向比较；
- 不覆盖主线。

---

### 阶段 8：实现横向比较板

目标：

- 不再手动翻多个 CSV / parquet。

新增：

```text
scripts/compare_runs.py
src/pipeline/compare.py
```

输出：

```text
reports/experiment_board.csv
reports/experiment_board.md
```

比较字段：

```text
run_id
signal_method
optimizer_config
IC_mean
IC_IR
IC_t
IC_p
annualized_excess_return
IR
monthly_win_rate
excess_max_drawdown
annual_turnover
tracking_error
fallback_L1_L2_L3
paired_p_vs_mainline
artifact_hash
```

完成标准：

- 能读取 `registry/mainline.json`；
- 能读取 `registry/challengers.json`；
- 能自动生成比较表；
- 能标注当前是否使用测试集。

---

### 阶段 9：归档旧 experiments

目标：

- 等新框架稳定后，再整理旧目录。

动作：

```text
experiments/ridge_signal/            -> experiments/legacy/ridge_signal/
experiments/ridge_rolling/           -> experiments/legacy/ridge_rolling/
experiments/turnover_lambda_grid/    -> experiments/legacy/turnover_lambda_grid/
experiments/te_grid/                 -> experiments/legacy/te_grid/
```

注意：

- 先移动小型代码和 markdown；
- 大型 parquet 如果本来不入库，可以不移动或只保留 manifest；
- 任何迁移都要先确认新 run 已经能复现关键指标。

完成标准：

- README 明确旧实验已归档；
- 新入口是默认推荐入口；
- 旧入口还能通过 legacy 文档找到，但不再作为主线。

---

### 阶段 10：测试集入口接入新框架

目标：

- 测试集纪律不变，但产物结构和 train_valid run 一致。

新结构：

```text
runs/test/test_run_1__<mainline_run_id>/
├── run_config.json
├── inputs.lock.json
├── TEST_SET_RUN_LOCK.json
├── backtest/
├── attribution/
└── reports/
```

要求：

- 仍然最多 3 次；
- 仍然必须显式 `--run-id 1/2`；
- 仍然写 ledger；
- 测试集产物绝不覆盖训练/验证 run。

完成标准：

- `scripts/run_test_pipeline.py` 能读取 `registry/mainline.json`；
- 测试集产物写入 `runs/test/`；
- ledger 仍然有效。

---

## 7. 建议执行顺序

### 短期（零破坏，立即可做）

| 步骤 | 阶段 | 关键产物 | 测试要求 |
|---|---|---|---|
| 1 | 阶段 0：冻结当前状态 | `check/<date>/pre_migration_git_status.txt` | 无 |
| 2 | 阶段 1：新增目录骨架 | `src/pipeline/__init__.py` `configs/README.md` `runs/README.md` | `pytest` 现有测试全通过 |
| 3 | 阶段 2：定义 contract | `contracts.py` `artifacts.py` `registry.py` | 新增 `test_pipeline_contracts.py` `test_pipeline_artifacts.py` |
| 4 | 阶段 3：写第一批 spec | 8 个 `configs/pipelines/*.py` | spec 文件能被 contract loader 解析 |

这四步不会破坏现有代码，是低风险。

### 中期（改造旧脚本，开始出 run）

| 步骤 | 阶段 | 关键产物 | 测试要求 |
|---|---|---|---|
| 5 | 阶段 4：旧脚本加路径参数 | `run_portfolio_optimization.py` 等支持 `--output-dir` | 旧命令无变化（回归） |
| 6 | 阶段 5：实现 `run_experiment.py` | 统一实验入口 | `test_pipeline_stages.py` smoke test |
| 7 | 阶段 6：迁移 expanding Ridge 主基线 | 第一个标准 run + `registry/mainline.json` | `test_pipeline_registry.py` |
| 8 | 因子评估快照化 | `run_factor_evaluation.py --output-label` | `reports/factor_evaluation/<snapshot>/` |

### 长期（challengers、比较板、清理）

| 步骤 | 阶段 | 关键产物 | 说明 |
|---|---|---|---|
| 9 | 阶段 7：迁移 challenger | rolling 36/48/60 + decay 24/36/48 标准 run | 全部进 `registry/challengers.json` |
| 10 | 阶段 8：实现比较板 | `reports/experiment_board.csv/md` | `compare_runs.py` 自动生成 |
| 11 | 阶段 9：归档旧 experiments | `experiments/legacy/` | 先复现后归档 |
| 12 | 阶段 10：测试集接入 | `runs/test/` 结构 | ledger 纪律不变 |

---

## 8. 迁移期间的禁止事项

1. 不删除旧实验目录，直到新 run 完整复现关键指标。
2. 不把 `runs/` 中的 parquet 复制回 `data/processed/` 作为主线。
3. 不手动改 `data/processed/composite_signal_ic_ir.parquet` 来替换信号。
4. 不在同一个实验里同时改信号和优化器，除非 spec 明确标记为 full pipeline。
5. 不在迁移过程中运行正式测试集。
6. 不把 rolling-48m 直接提升为主线；它仍是 challenger。
7. 不新增 decay 半衰期候选，除非先写入 spec 和研究计划。

---

## 9. 最小可行版本

如果只想先解决”结构乱、主线替换困难”，最小版本只需要：

```text
src/pipeline/contracts.py
src/pipeline/artifacts.py
configs/pipelines/baseline_expanding_ridge_te6_lam0050.py
registry/mainline.json
runs/train_valid/
scripts/run_experiment.py
scripts/promote_run.py
```

第一版不需要马上迁移所有实验。  
只要能把 expanding Ridge 跑成一个不可变 run，并通过 registry 指向它，主线替换问题就已经解决一半。

---

## 10. 当前可直接执行的内容（Ready-to-Start 清单）

以下内容**不依赖任何旧代码改动**，可以立即开始：

### ✅ 立即可执行（纯新增，零破坏）

| 内容 | 描述 |
|---|---|
| **阶段 0 冻结记录** | 运行 `git status --short` + 生成关键产物 hash 快照，写 `check/0527/pre_migration_manifest.txt` |
| **阶段 1 目录骨架** | 创建 `src/pipeline/` `configs/signals/` `configs/optimizers/` `configs/pipelines/` `registry/` `runs/train_valid/` `runs/test/` `experiments/legacy/`，写各 README |
| **阶段 2 contract 定义** | 实现 `src/pipeline/contracts.py` `artifacts.py` `registry.py`（dataclass，无新依赖） + 对应测试 |
| **阶段 3 第一批 spec** | 写 8 个 `configs/pipelines/*.py`（纯 Python dataclass 赋值，不运行任何计算） |
| **`run_factor_evaluation.py` 快照化** | 增加 `--output-label` 参数，输出到 `reports/factor_evaluation/<label>/`，旧行为保持不变 |

### ⚠️ 需要小改动（改旧脚本接口，兼容旧行为）

| 内容 | 描述 | 风险 |
|---|---|---|
| **阶段 4 脚本路径参数** | 给 `run_portfolio_optimization.py` `run_backtest.py` `run_attribution.py` 加 `--output-dir`，不传时行为不变 | 低 |

### 🔴 不建议现在做（依赖阶段 2-4 完成）

| 内容 | 原因 |
|---|---|
| 实现 `run_experiment.py` | 需要 contract 和 artifact 已定义 |
| 迁移 expanding Ridge 主基线 | 需要统一入口可用 |
| 归档 `experiments/legacy/` | 需要新 run 能复现指标才能归档 |
| 测试集接入新框架 | 必须在主线稳定后进行 |

