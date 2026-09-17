# Experiments Index

本目录存放平行实验和历史版本归档。主管线生产代码在 `src/`，主管线入口在 `scripts/`。

## 当前主线

当前主线由 `registry/mainline.json` 确定，通过 `scripts/compare_runs.py` 查看横向比较板。

```bash
python -m scripts.compare_runs          # 从 registry 读取，生成 reports/experiment_board.md
```

本目录不硬编码当前主线名称；文件名和历史实验结论都不能替代 registry。
当前主线只以 `registry/mainline.json` 为准，指标以对应 `runs/train_valid/<run_id>/` 的不可变产物为准。

## 目录结构

| 目录 | 内容 |
|------|------|
| `legacy/` | 迁移前的旧实验脚本；Ridge 旧路径仅保留兼容转发，正式实现已迁入 `src/signal/` |
| `v1.0/` | 旧口径因子评估归档（历史对照） |
| `v1.1/` | 旧验证期最优配置归档（已被新 spec/run 体系替代） |
| `v1.2/` | 滚动窗口实验归档（已被新 run 体系替代） |
| `notebooks/` | 探索性分析 notebook |
| `notes/` | 研究笔记 |

> **注意**：`v1.0/`、`v1.1/`、`v1.2/` 是历史归档，不反映当前主线状态。
> `legacy/` 只保留历史脚本和兼容导入；新增生产代码不得从该目录导入实现。

## legacy/ 目录说明

`legacy/` 下的脚本是迁移前的独立实验入口（ridge_signal、ridge_rolling、te_grid、turnover_lambda_grid）。
这些目录已于 2026-05-27 从 `experiments/` 根目录迁入 `legacy/`。

- 这些脚本**不保证**可直接运行（上游数据路径可能已变化）
- 如需重现旧结论，以对应目录内的 `README.md` 或 `NOTES.md` 为参考

## 文件保留规则

- 入库：`README.md`、`NOTES.md`、`manifest.json`、`comparison_report.md`、小型 `csv/json/md` 摘要
- 不入库：`parquet/feather/pkl/png/pdf` 等大文件（已在 `.gitignore` 中排除）

## 测试集纪律

测试集运行次数只读取 `docs/logs/test_set_runs.json`；本文件不复制具体次数，避免状态漂移。
每次跑测试集，commit message 必须含 `[TEST_SET_RUN_N]` 标记。
