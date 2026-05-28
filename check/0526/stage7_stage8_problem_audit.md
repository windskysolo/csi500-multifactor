# 阶段 7/8：收益归因与显著性审查

- 生成时间：2026-05-27 09:38:49
- 正式测试集：未运行。
- Pipeline B 使用冻结 replay：`check/0526/tmp/stage2/pipeline_b_lam0050_replayed/`。
- rolling-48m 使用冻结 replay：`check/0526/tmp/stage2/rolling_48m_replayed/`。
- rolling-36m/60m 仅有 `experiments/ridge_rolling/results/` 当前实验产物，因此只用于多窗口披露，不作为冻结证据。

## 问题 A：Pipeline B 39.1% 月胜率的原因

- `pipeline_b_frozen_replay`: n=23, win=39.13%, pos=9, neg=14, pos_mean=1.75%, neg_mean=-0.87%, payoff=2.01348, net_sum=3.59%
- `rolling_48m_replay`: n=23, win=60.87%, pos=14, neg=9, pos_mean=1.40%, neg_mean=-1.30%, payoff=1.07269, net_sum=7.83%

直接解释：Pipeline B 的 23 个有效月中只有 9 个正超额月，但正超额月平均幅度约为负超额月绝对幅度的 2 倍；因此低胜率与正净超额可以同时成立。这不是月胜率公式错误。

年度拆解：

- `pipeline_b_frozen_replay` 2021: n=11, excess_sum=0.61%, win=27.27%, monthly_IR=0.114774
- `pipeline_b_frozen_replay` 2022: n=12, excess_sum=2.98%, win=50.00%, monthly_IR=0.622752
- `rolling_48m_replay` 2021: n=11, excess_sum=2.14%, win=54.55%, monthly_IR=0.367099
- `rolling_48m_replay` 2022: n=12, excess_sum=5.69%, win=66.67%, monthly_IR=1.14203

市场状态拆解：

- `pipeline_b_frozen_replay` benchmark_down: n=11, excess_sum=9.18%, win=63.64%
- `pipeline_b_frozen_replay` benchmark_up: n=12, excess_sum=-5.59%, win=16.67%
- `rolling_48m_replay` benchmark_down: n=11, excess_sum=8.34%, win=72.73%
- `rolling_48m_replay` benchmark_up: n=12, excess_sum=-0.51%, win=50.00%

## 问题 B：rolling-48m 弱 IC 但组合 IR 更高的解释

rolling 相对 Pipeline B 贡献最大的月份：

- month_end=2021-04-30: rolling_minus_pipeline_b=0.0243696
- month_end=2022-03-31: rolling_minus_pipeline_b=0.014218
- month_end=2021-06-30: rolling_minus_pipeline_b=0.0137502
- month_end=2021-08-31: rolling_minus_pipeline_b=0.0119648
- month_end=2022-08-31: rolling_minus_pipeline_b=0.0101597

rolling 相对 Pipeline B 总效应改善最大的行业：

- industry_name=食品饮料, industry_code=801120.SI: rolling_minus_pipeline_b_total_effect=0.0293699
- industry_name=电力设备, industry_code=801730.SI: rolling_minus_pipeline_b_total_effect=0.0283538
- industry_name=煤炭, industry_code=801950.SI: rolling_minus_pipeline_b_total_effect=0.0193568
- industry_name=石油石化, industry_code=801960.SI: rolling_minus_pipeline_b_total_effect=0.015296
- industry_name=基础化工, industry_code=801030.SI: rolling_minus_pipeline_b_total_effect=0.012212

rolling 相对 Pipeline B 主动贡献改善最大的股票：

- ts_code=300274.SZ, industry_name=电力设备, industry_code=801730.SI: rolling_minus_pipeline_b_active_contribution=0.0131736
- ts_code=300763.SZ, industry_name=电力设备, industry_code=801730.SI: rolling_minus_pipeline_b_active_contribution=0.0111117
- ts_code=002382.SZ, industry_name=医药生物, industry_code=801150.SI: rolling_minus_pipeline_b_active_contribution=0.0110245
- ts_code=600256.SH, industry_name=石油石化, industry_code=801960.SI: rolling_minus_pipeline_b_active_contribution=0.0109925
- ts_code=002709.SZ, industry_name=电力设备, industry_code=801030.SI: rolling_minus_pipeline_b_active_contribution=0.00993796

归因口径校验（Brinson/个股贡献为 T+1 开盘到下期收盘的股票端 gross 近似，NAV 为扣成本 net）：

- `pipeline_b_frozen_replay`: reconciled_periods=23, avg_gross_minus_nav_net=0.13%, max_abs=0.89%
- `rolling_48m_replay`: reconciled_periods=23, avg_gross_minus_nav_net=0.15%, max_abs=0.90%

Brinson 分项残差提示：行业 `total_effect` 与组合超额收益闭合；但组合存在少量现金/锁定权重，`allocation + selection + interaction` 与 `total_effect` 有小残差，因此本报告只用 `total_effect` 做行业贡献主证据。

- `pipeline_b_frozen_replay`: max_abs_effect_residual=0.17%, avg_abs_effect_residual=0.01%
- `rolling_48m_replay`: max_abs_effect_residual=0.16%, avg_abs_effect_residual=0.02%

阶段 7 判定：rolling 的优势集中在少数月份和若干行业/个股贡献上；阶段 5/6 未发现明显未来函数、成本更低或约束更松的证据，因此更合理的解释是验证期路径下的持仓差异被组合优化放大，但稳健性仍需按阶段 8 处理。

## 阶段 8：显著性

- 配对月度差值 rolling - PipelineB：mean=0.18%, sum=4.24%, rolling_better_months=14, t=0.883573, p=0.386481, Wilcoxon p=0.481952。
- 普通 bootstrap 月度 IR 差 95% CI：[-0.481298, 1.4369]，P(diff>0)=80.45%。
- 3 个月 block bootstrap 月均差 95% CI：[-0.16%, 0.55%]，P(diff>0)=84.56%。
- 普通 bootstrap 月均差 95% CI：[-0.20%, 0.59%]，P(diff>0)=81.61%。
- 3 个月 block bootstrap 月度 IR 差 95% CI：[-0.377573, 1.25225]，P(diff>0)=84.84%。

多窗口披露：

- `rolling_36m_live_artifact`: daily_IR=0.443704, monthly_win=60.87%, paired_p=0.804527, BH_p=0.95063, note=live_experiments_artifact
- `rolling_48m_replay`: daily_IR=1.09398, monthly_win=60.87%, paired_p=0.386481, BH_p=0.95063, note=frozen_replay
- `rolling_60m_live_artifact`: daily_IR=0.620653, monthly_win=60.87%, paired_p=0.95063, BH_p=0.95063, note=live_experiments_artifact

阶段 8 判定：验证期只有 23 个有效月，rolling-48m 的组合 IR 点估计更高，但配对月度差异与 bootstrap 区间不足以支持“稳定显著优于 Pipeline B”的强结论；同时 48m 是多个窗口中事后最好者，必须披露多重比较风险。