# scripts/

本目录只放“可执行入口与编排”，核心金融逻辑必须位于 `src/`。默认正式入口是 `python -m scripts.run_experiment`；文件名相近不代表都属于当前主线。

## 入口分类

| 分类 | 文件 | 职责 |
|---|---|---|
| 实验治理 | `run_experiment.py`、`compare_runs.py`、`promote_run.py`、`test_set_ledger.py` | 创建训练/验证 run、比较、晋升和查询测试集次数 |
| 受控测试 | `run_test_pipeline.py` | 唯一测试集入口；不得作为普通研究命令运行 |
| 数据准备 | `download_*.py`、`csv_to_parquet.py`、`build_factor_panels.py` | 下载、转换和构建因子面板 |
| 研究评价 | `run_factor_evaluation.py`、`run_factor_health.py` | 单因子检验和健康监控 |
| 单阶段工具 | `run_signal_combination.py`、`run_portfolio_optimization.py`、`run_backtest.py`、`run_attribution.py` | 独立复核某一阶段，不替代正式 Spec run |
| 参数研究 | `run_optimizer_grid.py`、`run_turnover_lambda_grid.py`、`run_optimizer_o1_recheck.py` | 训练/验证期参数实验；结果不得直接覆盖主线 |
| 诊断工具 | `diagnose_*.py`、`probe_data_coverage.py`、`check_phase3_coverage.py`、`backfill_signal_quality.py` | 生成诊断证据，不定义生产流程 |
| 报告与图表 | `generate_backtest_report.py`、`gen_comparison_report.py`、`plot_validation_results.py`、`build_data_quality_notebook.py` | 从已有产物生成展示或检查材料 |
| 历史/兼容入口 | `run_pipeline.py`、`save_version.py` | 旧流程或版本归档辅助；新实验优先使用 `run_experiment.py` |
| 运维脚本 | `run_phase3_*.ps1`、`watch_phase3_fast.ps1` | 长任务启动、恢复和观察 |

## 约束

- 脚本只负责参数解析、编排、日志和产物写入，不复制 `src/` 中的算法。
- 新的一次性诊断优先放入 `experiments/`，确认可复用后再进入本目录。
- 输出必须进入 `runs/`、`reports/`、`check/` 或 `logs/`，不能散落在项目根目录。
- 不根据文件名判断当前主线；以 `registry/mainline.json` 为准。
- 不从普通脚本绕过 `scripts/run_test_pipeline.py` 读取或运行测试集。
