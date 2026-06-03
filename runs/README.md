# runs/

每次实验运行的不可变产物目录。默认不入库（parquet 文件体积大）。

```
runs/
├── train_valid/   # 训练/验证期 run
│   └── YYYYMMDD_HHMMSS__<experiment_id>/
│       ├── run_config.json
│       ├── inputs.lock.json
│       ├── manifest.json
│       ├── RUN_FINISHED.json   ← 成功完成标志（幂等）
│       ├── RUN_FAILED.json     ← 失败标志（如存在）
│       ├── signal/
│       ├── portfolio/
│       ├── backtest/
│       ├── attribution/
│       └── reports/
└── test/          # 正式测试集 run（最多 3 次，受 ledger 纪律约束）
    └── test_run_N__<mainline_run_id>/
```

## 规则

- 同一个 `run_id` 一旦生成 `RUN_FINISHED.json`，不再覆盖
- 重跑必须生成新 `run_id`（时间戳自动区分）
- 不把 `runs/` 产物复制回 `data/processed/`
- 测试集 run 必须通过 `scripts/run_test_pipeline.py`，不能绕过 ledger
