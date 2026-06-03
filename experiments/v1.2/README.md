# V1.2 实验档案

## 核心结论

**Ridge v2 验证期 IR = 0.503**，首次突破 IR ≥ 0.5 目标。

| 指标 | V1.1 | **V1.2** | 变化 |
|------|-----:|--------:|-----:|
| Ridge 验证 IR | 0.483 | **0.503** | +0.020 |
| Ridge 超额收益 | +2.77% | +2.99% | +0.22% |
| Ridge 超额最大回撤 | 3.98% | 7.96% | +3.98% |
| Ridge 换手 | 1004% | 981% | −23% |
| IC_IR 主流水线 IR | 0.341 | 0.402 | +0.061 |

## 变更内容

### 相对于 V1.1

- **新增因子**: `rev_acceleration`（营收增速加速度）
  - 阶段6评估：训练 IC_IR=0.574，验证 IC_IR=0.725，四道 Gate 全通过
  - 经济逻辑：成长动能二阶导，对高基数效应免疫
- **evaluate 阶段自动纳入**: `high_52w_v2`（52周高点比率）
  - 此前因 IC_IR 主流水线 IR 跌至 0.091 而暂缓；v1.2 配合 rev_acceleration 后 IR 升至 0.402
  - 验证期 IC_IR = 0.328，lead_ratio = 0.09（Gate 全通过）

### 因子池

共 **18 个**因子（v1.1 为 16 个）：

| 类别 | 因子 |
|------|------|
| 流动性 | turn_20d, amihud |
| 风险特征 | ivol_60d |
| 资金/情绪 | hk_hold_ratio, hk_hold_chg, holder_chg |
| 估值 | ep_ttm, cfp |
| 财务质量 | piotroski_f, q_roe, gross_margin, gross_margin_trend |
| 成长 | roe_delta, roe_delta_3q, rev_yoy, **rev_acceleration（新）** |
| 动量（基本面）| analyst_eps_revision |
| 动量（价格）| **high_52w_v2（新）** |

## 目标达成状态

| 目标 | 要求 | 实际 | 状态 |
|------|------|------|------|
| 信息比率 IR | ≥ 0.5 | **0.503** | ✅ 达成 |
| 超额最大回撤 | ≤ 10% | 7.96% | ✅ 达成 |
| 年化双边换手 | 5-15x | 9.81x | ✅ 达成 |

## 已知非阻塞问题

- `rev_acceleration` 未注册到 `factor_attr.py` 的 `DEFAULT_FACTOR_GROUPS`，归因中归入 residual
- Brinson BHB 验证偏差 1.65e-03（> 1e-8，但 < 0.5% 警戒线量级）
- 超额最大回撤从 3.98%（v1.1）升至 7.96%（v1.2），仍在 10% 以内，但上升幅度需注意

## 文件结构

```
experiments/v1.2/
├── README.md         # 本文件
├── manifest.json     # 关键指标存档
└── results/
    ├── ic_ir/        # IC_IR 主流水线结果（IR=0.402）
    │   ├── ic_ir_backtest_nav.parquet
    │   ├── ic_ir_backtest_metrics.parquet
    │   ├── ic_ir_portfolio_weights_optimized.parquet
    │   ├── ic_ir_composite_signal_ic_ir.parquet
    │   └── ic_ir_brinson_attribution.parquet
    └── ridge_lam_0050/   # Ridge v2 + λ=0.005（IR=0.503）
        ├── backtest_metrics_valid.parquet
        ├── backtest_nav_valid.parquet
        └── ...（共11个文件）
```

## 下一步

IR 已达标。剩余 ≤3 次测试集配额用于最终验证。测试集运行需要：

1. git commit message 含 `[TEST_SET_RUN_N]`
2. 使用 `scripts/run_test_pipeline.py`
3. 运行前先在 `docs/logs/test_set_run_log.md` 登记
