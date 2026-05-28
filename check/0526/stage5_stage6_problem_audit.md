# 阶段 5/6：problems 定向审查

- 生成时间：2026-05-27 09:25:24
- 正式测试集：未运行。
- 旧 Pipeline B 低胜率问题使用 `check/0526/tmp/stage2/pipeline_b_lam0050_replayed/` 中的冻结 replay 产物。
- rolling-48m 问题使用 `check/0526/tmp/stage2/rolling_48m_replayed/` 与当前 rolling 信号产物。
- 注意：当前正式实验产物已相对阶段 0 冻结记录漂移；本报告不混用当前 lam_0050 作为旧 Pipeline B。

## 问题 A：Pipeline B 低月胜率是否来自收益分布

- `pipeline_b_frozen_replay`: win_rate=0.391304, pos_mean=0.0175473, neg_mean=-0.00871491, payoff_ratio=2.01348, net_sum=0.0359171, best=2021-03-31/0.0288158, worst=2021-05-31/-0.0233605
- `rolling_48m_replay`: win_rate=0.608696, pos_mean=0.0139581, neg_mean=-0.0130122, payoff_ratio=1.07269, net_sum=0.078303, best=2021-06-30/0.0372484, worst=2022-10-31/-0.0238283

判定：Pipeline B 低胜率不是指标 bug；它的正超额月份数量少，但正月份平均幅度大于负月份平均绝对幅度，属于偏右尾收益结构。是否足够稳健仍需阶段 7/8 做归因和显著性检验。

## 问题 B：rolling-48m 弱 IC 但强 IR 是否有未来函数迹象

- 预测窗口违规行数：0 / 210。
- CV 窗口违规行数：0 / 10。
- `expanding_ridge_current_signal` original: valid IC_mean=0.0606056, IC_IR=0.637929, p=0.00574443
- `expanding_ridge_current_signal` signal_lag_1_shift_down: valid IC_mean=0.0415544, IC_IR=0.381075, p=0.0812104
- `expanding_ridge_current_signal` signal_lead_1_shift_up: valid IC_mean=-0.0280094, IC_IR=-0.253242, p=0.248169
- `expanding_ridge_current_signal` label_shuffle_by_date: valid IC_mean=0.0074046, IC_IR=0.157365, p=0.458434
- `rolling_48m_current_signal` original: valid IC_mean=0.0325321, IC_IR=0.441427, p=0.0458018
- `rolling_48m_current_signal` signal_lag_1_shift_down: valid IC_mean=0.010733, IC_IR=0.123626, p=0.559301
- `rolling_48m_current_signal` signal_lead_1_shift_up: valid IC_mean=-0.0176476, IC_IR=-0.183624, p=0.398816
- `rolling_48m_current_signal` label_shuffle_by_date: valid IC_mean=-0.00190546, IC_IR=-0.0442637, p=0.833842

判定：当前信号版本的训练窗口和 CV 窗口未发现直接穿越预测日/验证期的证据。shift test 未显示 rolling-48m 的 lead_1 明显优于 original；label shuffle 接近 0。后续仍需阶段 5 更深层审查实际训练样本矩阵和边界 label exit_date。

## 阶段 6：优化器与交易执行解释

### 权重暴露

- `pipeline_b_frozen_replay`: holdings_mean=85.75, active_share_mean=0.853933, max_single_dev_mean=0.0161815, max_industry_dev_mean=0.0310112, top10_sum_mean=0.19796, benchmark_corr_mean=0.150474
- `rolling_48m_replay`: holdings_mean=83.0417, active_share_mean=0.845673, max_single_dev_mean=0.0162917, max_industry_dev_mean=0.0309018, top10_sum_mean=0.201398, benchmark_corr_mean=0.182058

### 交易执行和成本

- `pipeline_b_frozen_replay`: annual_turnover=1003.53%, total_cost=0.0328897, avg_cost_bps=12.7544, stamp_rates=[0.001], avg_locked=0.0416667, avg_no_buy=0.208333, all_Tplus1=True
- `rolling_48m_replay`: annual_turnover=1009.23%, total_cost=0.0344598, avg_cost_bps=12.8278, stamp_rates=[0.001], avg_locked=0.0416667, avg_no_buy=0.375, all_Tplus1=True

### rolling 相对 Pipeline B 贡献最大的月份

- 2021-04-30: rolling - PipelineB = 0.0243696
- 2022-03-31: rolling - PipelineB = 0.014218
- 2021-06-30: rolling - PipelineB = 0.0137502
- 2021-08-31: rolling - PipelineB = 0.0119648
- 2022-08-31: rolling - PipelineB = 0.0101597

阶段 6 判定：两者均满足 T+1 执行和 2021-2022 印花税 10bps 口径；rolling 的换手和持仓暴露略高/不同，组合层优势更可能来自优化后持仓路径和月份收益分布，而不是信号 IC 整体更强。还不能证明 rolling 稳健优越。