# 阶段 2：回测入口和产物等价性审查

- 生成时间：2026-05-26 19:34:05
- 正式测试集：未运行。
- 写入目录：`check/0526/tmp/stage2/`；未覆盖 `experiments/` 产物。

## 2.1 同一权重 + 当前回测引擎

- `pipeline_b_lam0050` / existing_weights_current_engine_vs_existing_nav: strategy max diff=8.881784197e-16, benchmark max diff=0, excess max diff=8.881784197e-16, rows=485
- `rolling_48m` / existing_weights_current_engine_vs_existing_nav: strategy max diff=1.11022302463e-15, benchmark max diff=0, excess max diff=1.33226762955e-15, rows=485
- `pipeline_b_lam0050` / rebuilt_expanding_current_optimizer_vs_existing_nav: strategy max diff=0.045070126319, benchmark max diff=0, excess max diff=0.0415916616867, rows=485

## 2.2 Metrics 差异

- `pipeline_b_lam0050` / existing_weights_current_engine_vs_existing_metrics / `excess_return`: old=0.0277112698958, new=0.0277112698958, diff=-1.11022302463e-16
- `pipeline_b_lam0050` / existing_weights_current_engine_vs_existing_metrics / `information_ratio`: old=0.483493043277, new=0.483493043277, diff=-2.44249065418e-15
- `pipeline_b_lam0050` / existing_weights_current_engine_vs_existing_metrics / `monthly_win_rate`: old=0.391304347826, new=0.391304347826, diff=0
- `pipeline_b_lam0050` / existing_weights_current_engine_vs_existing_metrics / `tracking_error`: old=0.0573147230992, new=0.0573147230992, diff=5.55111512313e-17
- `rolling_48m` / existing_weights_current_engine_vs_existing_metrics / `excess_return`: old=0.0616107307877, new=0.0616107307877, diff=-2.22044604925e-16
- `rolling_48m` / existing_weights_current_engine_vs_existing_metrics / `information_ratio`: old=1.09173279598, new=1.09173279598, diff=-3.77475828373e-15
- `rolling_48m` / existing_weights_current_engine_vs_existing_metrics / `monthly_win_rate`: old=0.608695652174, new=0.608695652174, diff=0
- `rolling_48m` / existing_weights_current_engine_vs_existing_metrics / `tracking_error`: old=0.0564338920791, new=0.0564338920791, diff=-6.93889390391e-18
- `pipeline_b_lam0050` / rebuilt_expanding_current_optimizer_vs_existing_metrics / `excess_return`: old=0.0277112698958, new=0.0299196623367, diff=0.00220839244085
- `pipeline_b_lam0050` / rebuilt_expanding_current_optimizer_vs_existing_metrics / `information_ratio`: old=0.483493043277, new=0.502676630515, diff=0.0191835872375
- `pipeline_b_lam0050` / rebuilt_expanding_current_optimizer_vs_existing_metrics / `monthly_win_rate`: old=0.391304347826, new=0.478260869565, diff=0.0869565217391
- `pipeline_b_lam0050` / rebuilt_expanding_current_optimizer_vs_existing_metrics / `tracking_error`: old=0.0573147230992, new=0.0595206948571, diff=0.00220597175792

## 2.3 权重、交易日志和 fallback

- `pipeline_b_lam0050` / existing_weights_current_engine_actual_weights_vs_existing_actual_weights: max_abs_diff=2.08166817117e-17, nonzero_cells>1e-10=0, dates_with_diff=0
- `pipeline_b_lam0050` / rebuilt_expanding_current_optimizer_vs_existing_target_weights: max_abs_diff=0.0285721713613, nonzero_cells>1e-10=8769, dates_with_diff=106
- `pipeline_b_lam0050` / rebuilt_expanding_current_optimizer_meta_numeric_vs_existing_meta: max_abs_diff=0.84650829999, nonzero_cells>1e-10=144, dates_with_diff=144
- `pipeline_b_lam0050` / existing_weights_current_engine_vs_existing_trades: old_exists=True, max_trade_numeric_diff=6.66133814775e-16, nonzero_cells=0, note=nan
- `rolling_48m` / existing_weights_current_engine_vs_existing_trades: old_exists=False, max_trade_numeric_diff=N/A, nonzero_cells=0, note=no old trade log
- `pipeline_b_lam0050` / rebuilt_expanding_current_optimizer_vs_existing_trades: old_exists=True, max_trade_numeric_diff=2, nonzero_cells=120, note=nan
- fallback L0: old=144 new=144 diff=0

## 阶段 2 初步判定

- 同一旧权重用当前回测引擎重跑等价，回测入口可复现；但当前优化器重建 expanding 与旧 Pipeline B 产物存在显著差异，rolling 与 expanding 的机制解释需先排查优化器输入、求解器稳定性或产物冻结口径。