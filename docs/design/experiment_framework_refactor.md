# 实验框架与主线替换文件逻辑设计

> 目的：把当前项目从“脚本产物互相覆盖 + experiments 目录各写各的”整理为
> “可并行实验、可冻结产物、可统一替换主线、可审计复现”的研究框架。
>
> 本文是结构设计，不要求一次性迁移全部旧代码。建议先并行搭建新框架，再逐步把旧脚本接入。

---

## 1. 当前结构的问题

当前项目有完整模块，但实验组织有几个明显风险：

1. `src/` 是可复用模块，`scripts/` 和 `experiments/` 里却混合了路径、参数、信号生成、优化、回测、报告逻辑。
2. 主线产物写在 `data/processed/`，平行实验写在 `experiments/*/results/`，二者没有统一 artifact contract。
3. `experiments/ridge_signal/`、`experiments/ridge_rolling/`、`experiments/te_grid/`、`experiments/turnover_lambda_grid/` 以“实验主题”分目录，但输入输出文件名和路径规则不完全一致。
4. 主线替换依赖手动改路径或复制 parquet，容易出现新旧产物混用。
5. 一个实验到底改了信号、优化器、回测，还是同时改了多层，不容易从目录名和产物中直接判断。

核心改法：**把 authored spec、immutable run artifacts、active mainline registry 三者拆开。**

---

## 2. 目标原则

### 2.1 三层分离

| 层 | 作用 | 是否可覆盖 |
|---|---|---|
| `src/` | 纯业务模块：因子、信号、优化、回测、归因 | 不写实验产物 |
| `configs/` | 人写的实验定义：信号方法、优化器参数、回测口径 | 可编辑、入库 |
| `runs/` | 每次运行的不可变产物：信号、权重、NAV、报告、manifest | 不覆盖，默认不入库 |
| `registry/` | 当前主线/候选线指针 | 可编辑、入库 |

### 2.2 产物不可变

同一个 `run_id` 一旦生成 `RUN_FINISHED.json`，就不再覆盖。重跑必须生成新 `run_id`。

这比 `experiments/ridge_rolling/results/rolling_48m/` 这种固定目录安全，因为固定目录天然容易漂移。

### 2.3 主线替换只改 registry

不要再通过复制 `composite_signal_ic_ir.parquet` 或手动改脚本路径来替换主线。

主线替换应变成：

```text
registry/mainline.json
  active_pipeline_run_id = "20260527_143000__expanding_ridge_te6_lam0050"
```

所有默认入口先读 registry，再找到对应 run 的 artifact。

### 2.4 实验只改一层

研究时尽量遵守：

- 因子/信号实验：固定优化器、固定回测。
- 优化器实验：固定信号、固定回测。
- 回测/成本实验：固定信号、固定权重。

如果一个 pipeline 同时改了信号和优化器，必须在 spec 中标为 `full_pipeline_experiment`，不能和单层实验混排。

---

## 3. 建议目标目录

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
│   └── pipeline/                         # 新增：统一运行契约与 artifact IO
│       ├── contracts.py                   # ExperimentSpec / ArtifactRef / RunContext
│       ├── registry.py                    # 读取 active baseline / challenger
│       ├── artifacts.py                   # 标准产物路径、hash、manifest
│       ├── stages.py                      # signal / portfolio / backtest / attribution stage wrapper
│       └── compare.py                     # run 间指标比较
│
├── configs/                               # 新增：人写实验定义
│   ├── README.md
│   ├── signals/
│   │   ├── icir_base.py
│   │   ├── ridge_expanding.py
│   │   ├── ridge_rolling_48m.py
│   │   └── ridge_decay_hl36.py
│   ├── optimizers/
│   │   ├── te6_lam0050.py
│   │   ├── te6_lam_grid.py
│   │   └── te_grid_6_7_8.py
│   └── pipelines/
│       ├── baseline_expanding_ridge_te6_lam0050.py
│       ├── challenger_decay_ridge_hl36_te6_lam0050.py
│       └── challenger_rolling48_te6_lam0050.py
│
├── registry/                              # 新增：当前主线与候选线指针
│   ├── mainline.json
│   ├── challengers.json
│   └── archived_promotions.jsonl
│
├── runs/                                  # 新增：不可变运行产物，默认不入库
│   ├── train_valid/
│   │   └── 20260527_143000__expanding_ridge_te6_lam0050/
│   │       ├── run_config.json
│   │       ├── inputs.lock.json
│   │       ├── manifest.json
│   │       ├── RUN_FINISHED.json
│   │       ├── signal/
│   │       │   ├── composite.parquet
│   │       │   ├── coef_history.parquet
│   │       │   ├── ic_detail.csv
│   │       │   └── signal_metadata.json
│   │       ├── portfolio/
│   │       │   ├── target_weights.parquet
│   │       │   ├── baseline_weights.parquet
│   │       │   ├── optimizer_meta.parquet
│   │       │   └── cov_metadata.csv
│   │       ├── backtest/
│   │       │   ├── nav_train.parquet
│   │       │   ├── nav_valid.parquet
│   │       │   ├── metrics_train.parquet
│   │       │   ├── metrics_valid.parquet
│   │       │   ├── trades_valid.parquet
│   │       │   └── actual_weights_valid.parquet
│   │       ├── attribution/
│   │       │   ├── brinson_industry.parquet
│   │       │   ├── brinson_period.parquet
│   │       │   └── reconciliation.parquet
│   │       └── reports/
│   │           ├── summary.md
│   │           └── self_check.md
│   └── test/
│       └── test_run_1__<run_id>/           # 正式测试集仍受 run-id 和 ledger 纪律约束
│
├── experiments/                            # 保留：研究笔记、旧实验归档、临时探索
│   ├── README.md
│   └── legacy/
│
├── reports/
│   ├── factor_evaluation/                  # 因子库评估快照（非 run 产物，按快照子目录区分）
│   │   └── <YYYYMMDD_snapshot_name>/       # 每次评估一个子目录，不直接覆盖
│   └── experiment_board.csv / .md          # 横向比较汇总（compare_runs.py 生成）
│
├── data/
│   ├── raw/
│   └── processed/                          # 只保留共享输入层，不再放主线回测结果
│       ├── daily_quote.parquet
│       ├── index_quote.parquet
│       ├── index_member.parquet
│       ├── factor_panels/
│       ├── fwd_ret_panel.parquet
│       └── cov_cache/
│
├── scripts/
│   ├── run_experiment.py                   # 新主入口：按 spec 运行
│   ├── compare_runs.py                     # 横向比较 runs
│   ├── promote_run.py                      # 更新 registry/mainline.json
│   ├── run_pipeline.py                     # 旧入口，逐步改为兼容 wrapper
│   ├── run_test_pipeline.py                # 测试集入口，后续接入同一 artifact contract
│   ├── run_factor_evaluation.py            # 因子库评估（产物归 reports/factor_evaluation/<snapshot>/）
│   ├── download_tushare.py / download_*.py # 数据下载（运维脚本，产物不入 runs/）
│   └── test_set_ledger.py                  # 测试集计数器（纪律工具）
│
├── tests/                                  # 分层单元测试，与 src/ 各层一一对应
│   ├── test_pit.py                         # L1 数据层：PIT 可见性
│   ├── test_universe.py                    # L1 数据层：可投资域
│   ├── test_preprocess.py                  # L2 因子层：横截面预处理
│   ├── test_evaluation.py                  # L3 评价层：IC / shift test
│   ├── test_signal_combiner.py             # L3 信号层：IC_IR 加权合成
│   ├── test_optimizer.py                   # L4 组合层：优化器与 fallback
│   ├── test_backtest_engine.py             # L4 回测层：T+1 执行
│   ├── test_transaction.py                 # L4 回测层：印花税切换
│   ├── test_attribution.py                 # L4 归因层：Brinson 恒等式
│   ├── test_pipeline_release_gates.py      # L5 框架层（现有）
│   ├── test_test_pipeline_isolation.py     # L5 框架层（现有）
│   ├── test_pipeline_contracts.py          # L5 框架层（阶段 2 后新增）
│   ├── test_pipeline_artifacts.py          # L5 框架层（阶段 2 后新增）
│   └── test_pipeline_registry.py          # L5 框架层（阶段 5 后新增）
│
└── logs/                                   # 运维日志（下载日志等），不入库，不入 runs/
```

---

## 4. Config 设计

不建议立刻引入 YAML，因为项目依赖里没有 PyYAML。为了可编辑和类型安全，建议先用 Python spec 文件，运行时生成 JSON lock。

### 4.1 authored spec：人写，可加注释

示例：`configs/pipelines/baseline_expanding_ridge_te6_lam0050.py`

```python
from src.pipeline.contracts import ExperimentSpec, SignalSpec, OptimizerSpec, BacktestSpec

SPEC = ExperimentSpec(
    experiment_id="baseline_expanding_ridge_te6_lam0050",
    description="保守主基线：expanding Ridge + TE 6% + lambda 0.005",
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="expanding",
        purge_months=2,
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
    ),
    optimizer=OptimizerSpec(
        te_target_annual=0.06,
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.005,
        topn=50,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
```

示例：`configs/pipelines/challenger_decay_ridge_hl36_te6_lam0050.py`

```python
SPEC.signal.training_mode = "decay_weighted_expanding"
SPEC.signal.half_life_months = 36
SPEC.experiment_id = "challenger_decay_ridge_hl36_te6_lam0050"
```

### 4.2 run lock：机器生成，不手改

每次运行生成：

```text
runs/train_valid/<run_id>/run_config.json
runs/train_valid/<run_id>/inputs.lock.json
```

`run_config.json` 记录最终展开后的参数。  
`inputs.lock.json` 记录输入文件 hash、git commit、config hash、测试集运行次数。

---

## 5. 标准 artifact contract

每个完整 run 必须尽量产出同名文件：

| 阶段 | 标准产物 | 说明 |
|---|---|---|
| signal | `signal/composite.parquet` | 行=调仓日，列=ts_code |
| signal | `signal/ic_detail.csv` | IC 明细、p 值、多重检验信息 |
| signal | `signal/coef_history.parquet` | Ridge/模型类信号需要 |
| portfolio | `portfolio/target_weights.parquet` | 调仓目标权重 |
| portfolio | `portfolio/baseline_weights.parquet` | TopN/基准对照权重 |
| portfolio | `portfolio/optimizer_meta.parquet` | fallback、约束、TE、求解状态 |
| backtest | `backtest/nav_valid.parquet` | 验证期策略/基准 NAV |
| backtest | `backtest/metrics_valid.parquet` | 验证期指标 |
| backtest | `backtest/trades_valid.parquet` | 交易日志 |
| backtest | `backtest/actual_weights_valid.parquet` | 实际日频持仓 |
| attribution | `attribution/brinson_period.parquet` | 月度归因 |
| reports | `reports/summary.md` | 自动报告 |
| root | `manifest.json` | 文件 hash、shape、日期范围 |
| root | `RUN_FINISHED.json` | 完成锁 |

平行实验可以只跑某一层，但也要遵守对应子目录命名。例如只做信号实验，也要写到：

```text
runs/train_valid/<run_id>/signal/composite.parquet
```

不要再出现同类产物有的叫 `ridge_composite_panel.parquet`、有的叫 `rolling_48m_composite_panel.parquet`、有的叫 `composite_signal_ic_ir.parquet`。这些可以放入 metadata，不放进 contract 文件名。

---

## 6. 新入口设计

### 6.1 运行实验

```text
python -m scripts.run_experiment ^
  --spec configs/pipelines/baseline_expanding_ridge_te6_lam0050.py ^
  --period train_valid
```

支持阶段截断：

```text
python -m scripts.run_experiment --spec ... --from-stage signal --to-stage backtest
python -m scripts.run_experiment --spec ... --from-stage portfolio --input-signal-run <run_id>
```

第二条用于“固定信号，只比较优化器”。

### 6.2 比较实验

```text
python -m scripts.compare_runs ^
  --base registry/mainline.json ^
  --candidates <run_id_1> <run_id_2> <run_id_3>
```

输出：

```text
reports/experiment_board.csv
reports/experiment_board.md
```

比较指标至少包括：

- signal IC_mean / IC_IR / t / p / 正 IC 比例
- annualized excess return
- IR
- monthly win rate
- excess max drawdown
- annual turnover
- fallback 分布
- active share
- 成本
- paired monthly diff p value

### 6.3 替换主线

```text
python -m scripts.promote_run ^
  --run-id 20260527_143000__expanding_ridge_te6_lam0050 ^
  --slot mainline ^
  --reason "clean baseline after artifact drift audit"
```

`promote_run.py` 必须检查：

1. `RUN_FINISHED.json` 存在。
2. 正式测试集未被误用，除非显式 `--allow-test-set`。
3. 必备 artifact 存在。
4. `reports/self_check.md` 存在。
5. 若是 mainline，必须写入 `registry/archived_promotions.jsonl`。

---

## 7. experiments 目录的新定位

未来建议：

```text
experiments/
├── README.md
├── notebooks/                 # 探索 notebook
├── legacy/                    # 旧 ridge_signal / rolling / te_grid 迁入后归档
└── notes/                     # 某条研究线的说明文档
```

`experiments/` 不再承担稳定产物路径职责。  
稳定产物只认 `runs/`。  
可入库的实验定义只认 `configs/`。

---

## 8. 推荐的基线和实验线

### 8.1 当前主线建议

先建立一个干净主线：

```text
configs/pipelines/baseline_expanding_ridge_te6_lam0050.py
```

它不是因为一定最好，而是因为：

- 解释性和稳定性更强；
- 没有 rolling-48m 的事后窗口选择问题；
- 适合作为所有 challenger 的共同对照。

### 8.2 新改进线

优先建立：

```text
configs/pipelines/challenger_decay_ridge_hl24_te6_lam0050.py
configs/pipelines/challenger_decay_ridge_hl36_te6_lam0050.py
configs/pipelines/challenger_decay_ridge_hl48_te6_lam0050.py
```

注意：半衰期候选必须预注册，不要看到结果后继续补候选。

### 8.3 rolling 线

保留但降级为 challenger：

```text
configs/pipelines/challenger_rolling36_te6_lam0050.py
configs/pipelines/challenger_rolling48_te6_lam0050.py
configs/pipelines/challenger_rolling60_te6_lam0050.py
```

比较时必须一起披露 36/48/60，而不是只报告 48。

---

## 9. 迁移计划

### 阶段 A：不动旧代码，先建立规范

新增：

```text
src/pipeline/
configs/
registry/
runs/
```

只写 contracts、artifact path、registry reader，不改变旧脚本。

### 阶段 B：让旧脚本支持路径参数

优先改这些入口：

```text
scripts/run_portfolio_optimization.py
scripts/run_backtest.py
scripts/run_attribution.py
experiments/ridge_signal/run_ridge_experiment.py
experiments/ridge_rolling/run_rolling_experiment.py
```

目标是把硬编码路径改为可传：

```text
--signal-path
--weights-path
--output-dir
--cov-cache-dir
--period train_valid
```

但默认值仍保持旧路径，避免一次性破坏兼容性。

### 阶段 C：实现 `scripts.run_experiment`

`run_experiment` 不重写金融逻辑，只作为 orchestration：

1. 读取 spec。
2. 建 run directory。
3. 调用对应 stage wrapper。
4. 保存 artifact。
5. 写 manifest 和 self-check。

### 阶段 D：迁移已有结果

把现有关键产物复制或重跑到标准 run：

```text
runs/train_valid/<run_id>__baseline_expanding_ridge_te6_lam0050/
runs/train_valid/<run_id>__rolling48_te6_lam0050/
```

旧目录不删除，先标为 legacy。

### 阶段 E：主线 promotion

生成：

```text
registry/mainline.json
registry/challengers.json
```

以后研究只通过 registry 读取主线。

---

## 10. 不建议做的事

1. 不要直接把 `experiments/ridge_rolling/results/rolling_48m/` 当新主线。
2. 不要继续把回测结果写回 `data/processed/` 的公共路径。
3. 不要用复制 parquet 的方式替换主线。
4. 不要一个实验目录同时包含多个不同层级的产物，除非它是完整 pipeline run。
5. 不要在迁移期删除旧目录；先兼容，后清理。

---

## 11. 最小可行落地版本

第一版只需要做到：

```text
configs/pipelines/baseline_expanding_ridge_te6_lam0050.py
configs/pipelines/challenger_decay_ridge_hl36_te6_lam0050.py
registry/mainline.json
runs/train_valid/<run_id>/
scripts/run_experiment.py
scripts/compare_runs.py
```

其中 `run_experiment.py` 第一版可以只支持：

```text
signal -> portfolio -> backtest
```

归因和报告可以第二版补上。

这样就能先解决你当前最大的痛点：**主线和 challenger 不再互相覆盖，每条实验线的来源、参数、产物、指标都能被固定住。**

---

## 12. 测试策略

### 12.1 测试层分类

测试与 `src/` 层次严格对应，共 6 层：

| 层 | 覆盖范围 | 现有文件 | 新框架后新增 |
|---|---|---|---|
| **L0 配置** | config 常量一致性 | `test_data_expansion_config.py` | 无 |
| **L1 数据** | PIT 约束、Universe 过滤、指数代码 | `test_pit.py` `test_alt_data_pit.py` `test_universe.py` `test_index_quote_code.py` | 无 |
| **L2 因子** | 时间边界、预处理、财务因子、技术因子、北向数据 | `test_factor_time_boundary.py` `test_preprocess.py` `test_phase4_factors.py` `test_phase5_factors.py` `test_technical_factors.py` `test_hk_hold_coverage.py` | 无 |
| **L3 评价/信号** | IC 分析、shift test、BH 校正、IC_IR 合成 | `test_evaluation.py` `test_signal_combiner.py` | 无 |
| **L4 组合/回测** | 优化器、引擎、绩效、成本、归因 | `test_optimizer.py` `test_backtest_engine.py` `test_backtest_metrics.py` `test_transaction.py` `test_attribution.py` | 无 |
| **L5 框架** | pipeline 契约、artifact、registry、stage 隔离 | `test_pipeline_release_gates.py` `test_test_pipeline_isolation.py` | `test_pipeline_contracts.py` `test_pipeline_artifacts.py` `test_pipeline_registry.py` |

### 12.2 各层测试原则

**L0–L4（业务逻辑层）**
- 全部使用合成数据 + monkeypatch，不依赖磁盘数据；
- 迁移前后无需变动，只要 `src/` 内部模块接口不改，测试直接继续有效；
- 现有 17 个测试文件在新框架下不需要移动或重写。

**L5（框架层）**
- 阶段 2 实现 `src/pipeline/` 后，须同步新增：
  - `test_pipeline_contracts.py`：`ExperimentSpec` 加载、JSON 序列化、`period_scope` 校验、不允许 `test` 期在 train_valid run 中出现；
  - `test_pipeline_artifacts.py`：`ArtifactLayout` 路径生成、`manifest.json` 写入结构、`RUN_FINISHED.json` 写入与幂等性（已存在时不覆盖）；
  - `test_pipeline_registry.py`：`mainline.json` 读取、`promote_run` 前置检查（`RUN_FINISHED` 存在、必备 artifact 在位、self_check.md 存在）。
- 阶段 2 完成标准：L5 新增测试全通过。

### 12.3 测试不产生 run 产物

- `tests/` 下的任何测试不写 `runs/` 目录、不写 `data/processed/`；
- 如果测试需要临时文件，使用 `tmp_path`（pytest fixture）或 `tempfile.TemporaryDirectory`；
- 已有两个 L5 测试（`test_test_pipeline_isolation.py`）已遵守此原则，作为参考实现。

### 12.4 CI 建议运行顺序

```text
pytest tests/test_data_expansion_config.py           # L0：最快，任何改动都先过
pytest tests/test_pit.py tests/test_universe.py      # L1
pytest tests/test_preprocess.py tests/test_evaluation.py  # L2-L3
pytest tests/test_optimizer.py tests/test_backtest_engine.py tests/test_transaction.py  # L4
pytest tests/test_pipeline_contracts.py tests/test_pipeline_artifacts.py tests/test_pipeline_registry.py  # L5（阶段 2 后）
```
