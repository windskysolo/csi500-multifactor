# 阶段 4：信号面板审查

- 生成时间：2026-05-26 20:21:44
- 正式测试集：未运行。
- 重要警示：`artifact_drift_after_stage2b.md` 显示 `composite_signal_ic_ir.parquet`、`ridge_composite_panel.parquet` 等信号产物已相对阶段 0 冻结记录发生漂移。本报告审查的是当前文件系统中的信号版本。

## 覆盖和横截面分布

- `ic_ir`: dates=132, valid avg_non_nan=498, valid overlap=0.996, top50 in universe=1, avg cs_std=1
- `expanding_ridge`: dates=105, valid avg_non_nan=468.522, valid overlap=0.937043, top50 in universe=1, avg cs_std=1
- `rolling_48m`: dates=105, valid avg_non_nan=460.696, valid overlap=0.921391, top50 in universe=1, avg cs_std=1

## 信号 Rank Correlation

- `expanding_ridge` vs `rolling_48m` validation rank corr: mean=0.846149, min=0.802206, n=23
- `ic_ir` vs `expanding_ridge` validation rank corr: mean=0.876684, min=0.796818, n=23
- `ic_ir` vs `rolling_48m` validation rank corr: mean=0.759465, min=0.505932, n=23

## 信号换手和目标权重换手

- `ic_ir` signal top50 turnover valid mean=0.370833, rank_corr_vs_prev mean=0.829494
- `expanding_ridge` signal top50 turnover valid mean=0.406087, rank_corr_vs_prev mean=0.796646
- `rolling_48m` signal top50 turnover valid mean=0.397391, rank_corr_vs_prev mean=0.750855
- `expanding_ridge` target weight L1 turnover valid mean=0.756491, active_names mean=108.958
- `rolling_48m` target weight L1 turnover valid mean=0.783014, active_names mean=108.958

## IC 明细汇总

- `ic_ir` valid: n=23, IC_mean=0.0470835, IC_std=0.102191, IC_IR=0.460742, positive=0.608696, t=2.20964, p=0.0378411
- `ic_ir` 2021: n=12, IC_mean=0.0342491, IC_std=0.0814212, IC_IR=0.420641, positive=0.583333, t=1.45714, p=0.173025
- `ic_ir` 2022: n=11, IC_mean=0.0610848, IC_std=0.123567, IC_IR=0.494345, positive=0.636364, t=1.63956, p=0.132134
- `expanding_ridge` valid: n=23, IC_mean=0.0606056, IC_std=0.0950037, IC_IR=0.637929, positive=0.652174, t=3.0594, p=0.00574443
- `expanding_ridge` 2021: n=12, IC_mean=0.0559512, IC_std=0.0819105, IC_IR=0.683077, positive=0.666667, t=2.36625, p=0.0373988
- `expanding_ridge` 2022: n=11, IC_mean=0.0656831, IC_std=0.111454, IC_IR=0.589331, positive=0.636364, t=1.95459, p=0.0791436
- `rolling_48m` valid: n=23, IC_mean=0.0325321, IC_std=0.0736977, IC_IR=0.441427, positive=0.652174, t=2.11701, p=0.0458018
- `rolling_48m` 2021: n=12, IC_mean=0.0228225, IC_std=0.0758257, IC_IR=0.300986, positive=0.5, t=1.04265, p=0.319485
- `rolling_48m` 2022: n=11, IC_mean=0.0431245, IC_std=0.0734026, IC_IR=0.587506, positive=0.818182, t=1.94854, p=0.079942

## 阶段 4 判定

- 验证期三个信号与成分股交集覆盖率未见显著低覆盖。
- expanding 与 rolling-48m 验证期 rank correlation 均值为 0.846149。
- 阶段 4 只说明信号层和权重层的差异特征，不单独证明 rolling-48m 的 IR 改善真实稳健；仍需结合阶段 5 的训练窗口/未来函数审查和阶段 6 的优化器归因。
- 由于信号产物已经相对阶段 0 冻结记录发生漂移，本阶段结果必须标注为“当前产物版本”的审查结果；不能直接替代阶段 0/1 旧产物版本的 root cause。
