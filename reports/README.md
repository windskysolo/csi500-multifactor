# reports/

本目录存放从数据、因子评价和实验产物生成的“可再生汇总报告”。它方便阅读，但不是当前主线、输入版本或测试集次数的权威来源。

## 读取顺序

1. 当前主线：`registry/mainline.json`
2. 某次运行事实：`runs/<scope>/<run_id>/run_config.json`、`inputs.lock.json`、`manifest.json`
3. 横向汇总：`experiment_board.csv` / `experiment_board.md`
4. 专项报告：本目录下的因子、优化器、回测等子目录

## 目录约定

| 位置 | 内容 |
|---|---|
| 根目录 | 跨 run 的汇总板、覆盖率摘要和少量项目级报告 |
| `factor_evaluation*/` | 不同批次的因子评价结果；目录名必须体现研究批次 |
| `factor_health*/` | 因子健康监控结果 |
| `optimizer_grid/`、`turnover_lambda_grid/` | 参数研究结果，不代表当前主线 |
| `backtest/`、`factor_combination/` | 独立阶段输出或历史兼容输出 |
| `figures/` | 可再生图表 |
| `archive/` | 已被替代但需保留追溯的历史报告 |
| `analysis_v2_results.md` | 固定报告生成器的输出位置；当前文件已标注为 2026-05 历史快照 |

## 新产物规则

- run 级结果优先写入对应 `runs/.../<run_id>/reports/`。
- 跨 run 汇总才写入本目录根部。
- 新建专项目录时使用稳定的研究名或日期批次，禁止覆盖不同输入生成的旧结果。
- 没有输入锁、run id 或生成命令的报告只能视为历史参考。
- 图片和 PDF 属于生成物，不作为唯一证据保存。
