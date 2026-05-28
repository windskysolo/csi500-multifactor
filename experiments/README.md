# Experiments Index

本目录存放平行实验和历史版本归档。主管线生产代码在 `src/`，主管线入口在 `scripts/`。

## 当前主线

当前主线由 `registry/mainline.json` 确定，通过 `scripts/compare_runs.py` 查看横向比较板。

```bash
python -m scripts.compare_runs          # 从 registry 读取，生成 reports/experiment_board.md
```

截至 2026-05-27，主线为 `baseline_expanding_ridge_te6_lam0050`，challenger 已有 rolling36/48/60 三个完整 run。
详见 `reports/experiment_board.md`。

## 目录结构

| 目录 | 内容 |
|------|------|
| `legacy/` | 迁移前的旧实验脚本，仅供历史对照，不再维护 |
| `v1.0/` | 旧口径因子评估归档（历史对照） |
| `v1.1/` | 旧验证期最优配置归档（已被新 spec/run 体系替代） |
| `v1.2/` | 滚动窗口实验归档（已被新 run 体系替代） |
| `notebooks/` | 探索性分析 notebook |
| `notes/` | 研究笔记 |

> **注意**：`v1.0/`、`v1.1/`、`v1.2/` 和 `legacy/` 均为历史归档，不反映当前主线状态。
> 当前最优配置以 `registry/mainline.json` 为准，当前最优指标以 `reports/experiment_board.md` 为准。

## legacy/ 目录说明

`legacy/` 下的脚本是迁移前的独立实验入口（ridge_signal、ridge_rolling、te_grid、turnover_lambda_grid）。
这些目录已于 2026-05-27 从 `experiments/` 根目录迁入 `legacy/`。

- 这些脚本**不保证**可直接运行（上游数据路径可能已变化）
- 如需重现旧结论，以对应目录内的 `README.md` 或 `NOTES.md` 为参考

## 文件保留规则

- 入库：`README.md`、`NOTES.md`、`manifest.json`、`comparison_report.md`、小型 `csv/json/md` 摘要
- 不入库：`parquet/feather/pkl/png/pdf` 等大文件（已在 `.gitignore` 中排除）

## 测试集纪律

测试集运行次数以 `docs/logs/test_set_runs.json` 为权威来源。当前口径：已消耗 0 次，剩余 2 次。
每次跑测试集，commit message 必须含 `[TEST_SET_RUN_N]` 标记。
