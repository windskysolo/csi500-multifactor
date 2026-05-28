# 2026-05-26 阶段 0/1 审查日志

- 生成时间：2026-05-26 19:01:15
- 工作目录：`E:\Acoding\Project\500`
- 当前 commit：`7423f1e59bdd23a11b7c38474801def9ec0aeed1`
- git dirty 项数量：107
- 正式测试集：未运行；本脚本不读取 `scripts/run_test_pipeline.py` 产物。

## 阶段 0：产物与口径冻结

- manifest 输出：`check/0526/artifact_manifest.csv`，共 14 个对象。
- config 输出：`check/0526/config_snapshot.csv`，共 16 个参数。
- `index_quote.parquet` 是否包含全收益 `nav` 列：True
- 缺失对象数量：0

### 覆盖说明
- `pipeline_b_ridge_expanding_signal`: 2014-03-31 ~ 2022-11-30；截至验证期最后一个实际持有期所需调仓日附近；VALID_END 月末调仓的执行日落在验证期之后
- `pipeline_b_ridge_expanding_coef`: 2014-03-31 ~ 2022-11-30；截至验证期最后一个实际持有期所需调仓日附近；VALID_END 月末调仓的执行日落在验证期之后
- `pipeline_b_metrics_valid`: N/A ~ N/A；未检测到日期索引或日期列
- `rolling_48m_signal`: 2014-03-31 ~ 2022-11-30；截至验证期最后一个实际持有期所需调仓日附近；VALID_END 月末调仓的执行日落在验证期之后
- `rolling_48m_coef`: 2014-03-31 ~ 2022-11-30；截至验证期最后一个实际持有期所需调仓日附近；VALID_END 月末调仓的执行日落在验证期之后
- `rolling_48m_metrics_valid`: N/A ~ N/A；未检测到日期索引或日期列
- `fwd_ret_panel`: 2012-01-31 ~ 2022-11-30；截至验证期最后一个实际持有期所需调仓日附近；VALID_END 月末调仓的执行日落在验证期之后
- `pipeline_a_metrics`: N/A ~ N/A；未检测到日期索引或日期列

## 阶段 1：独立指标复算

- 指标输出：`check/0526/metric_recalc.csv`，共 4 个 NAV/策略组合。
- 逐月输出：`check/0526/monthly_excess_review.csv`。

- `pipeline_b_ridge_expanding_lam0050` (primary): IR=0.483493, 年化超额=+2.77%, 月胜率=39.13% (9/23), 月度净超额和=+3.59%
- `rolling_48m` (primary): IR=1.091733, 年化超额=+6.16%, 月胜率=60.87% (14/23), 月度净超额和=+7.83%
- `pipeline_a_strategy_v1` (aux): IR=-0.110137, 年化超额=-0.78%, 月胜率=52.17% (12/23), 月度净超额和=-2.39%
- `pipeline_a_strategy_v2` (primary): IR=0.341396, 年化超额=+2.03%, 月胜率=56.52% (13/23), 月度净超额和=+2.11%

## 与已存 metrics 文件差异

- 未发现 `information_ratio`、`monthly_win_rate`、`excess_return` 的复算差异超过 `1e-8`。

## 当前阶段结论

- 阶段 0/1 只确认“产物状态”和“NAV 指标是否能独立复算”，不对 rolling-48m 是否真实改进下最终结论。
- 若某一异常值已经能从 NAV 独立复算出来，下一步应进入阶段 2：同一权重使用当前回测引擎重跑，排除产物新旧混用。
