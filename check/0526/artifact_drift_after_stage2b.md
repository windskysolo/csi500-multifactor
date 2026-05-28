# 阶段 2b 后产物漂移审计

- 对比基准：`check/0526/artifact_manifest.csv` 阶段 0 冻结记录。
- 正式测试集：未运行。

## 发生变化的对象

- `pipeline_a_signal_ic_ir`: `data/processed/composite_signal_ic_ir.parquet`; mtime `2026-05-25T22:25:43` -> `2026-05-26T19:16:47`; sha `37a58f5e6454e00f` -> `23024863b43dbc99`; sha_changed=True
- `pipeline_b_ridge_expanding_signal`: `experiments/ridge_signal/results/ridge_composite_panel.parquet`; mtime `2026-05-25T00:09:35` -> `2026-05-26T19:22:29`; sha `ff2dd7bcc90a5e6c` -> `bc0404931c1abe13`; sha_changed=True
- `pipeline_b_ridge_expanding_coef`: `experiments/ridge_signal/results/ridge_coef_history.parquet`; mtime `2026-05-25T00:09:35` -> `2026-05-26T19:22:29`; sha `c009c3186aea5176` -> `cdc03a13b549f749`; sha_changed=True
- `pipeline_b_nav_valid`: `experiments/turnover_lambda_grid/results/lam_0050/backtest_nav_valid.parquet`; mtime `2026-05-25T09:15:57` -> `2026-05-26T19:28:13`; sha `70f6d6e75958f7b0` -> `d6891ca38f2924ba`; sha_changed=True
- `pipeline_b_metrics_valid`: `experiments/turnover_lambda_grid/results/lam_0050/backtest_metrics_valid.parquet`; mtime `2026-05-25T09:15:57` -> `2026-05-26T19:28:13`; sha `3abdc6f18a984f43` -> `81acfeedabb5313a`; sha_changed=True
- `pipeline_b_weights_optimized`: `experiments/turnover_lambda_grid/results/lam_0050/weights_optimized.parquet`; mtime `2026-05-25T09:15:57` -> `2026-05-26T19:28:13`; sha `b8269eca82330720` -> `a2d311c178452c04`; sha_changed=True
- `pipeline_a_nav`: `data/processed/backtest_nav.parquet`; mtime `2026-05-25T22:30:31` -> `2026-05-26T19:20:03`; sha `408ca2cdbc10cc01` -> `31573bfc83703108`; sha_changed=True
- `pipeline_a_metrics`: `data/processed/backtest_metrics.parquet`; mtime `2026-05-25T22:30:31` -> `2026-05-26T19:20:03`; sha `58784af50fbe3c07` -> `a4b0df8ed7353b35`; sha_changed=True

## 解释

- 若 `experiments/turnover_lambda_grid/results/lam_0050` 发生变化，则阶段 0/1 中记录的 Pipeline B IR=0.483493 与当前文件中的 IR=0.502677 属于两个不同产物版本。
- 后续比较必须明确使用“阶段 0 冻结旧产物”还是“当前刷新后产物”；不能混用。
