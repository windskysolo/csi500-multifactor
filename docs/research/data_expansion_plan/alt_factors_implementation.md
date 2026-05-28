# 备选因子实现纪要

> 对应阶段计划 `phase_plan.md` 第 10 节（阶段 5.2 新增候选因子）。
> 完成时间：2026-05-22。

## 1. 改动文件速览

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/factors/alt_factors.py` | **新建** | 15 个备选因子实现 + 注册字典 + 入口函数 |
| `scripts/build_factor_panels.py` | **修改** | 加 import；删旧 `unimplemented_alts` 警告块；加 `alt_targets` 调度 |

### build_factor_panels.py 改动点

1. **第 42 行**：新增 `from src.factors.alt_factors import build_all_alt_factors`
2. **第 283-295 行（原）**：删除 `unimplemented_alts` 检测块（因子已实现，不再需要）
3. **第 297 行后**：新增 `alt_targets = [f for f in pending_factors if f in ALT_FACTORS]`
4. **循环体内**：新增 `raw_alt = build_all_alt_factors(T, codes_list, factor_names=alt_targets)`
5. **预处理合并**：`{**raw_fin, **raw_price}` → `{**raw_fin, **raw_price, **raw_alt}`

---

## 2. 因子清单

### 2.1 技术因子（5 个，来源：`daily_quote.close_adj`）

| 因子名 | 定义 | 数据窗口 | 预期方向 |
|---|---|---|---|
| `macd_cross` | (EMA12-EMA26 - Signal9)_T / close_adj_T | 120 交易日 | + |
| `rsi_6` | 6 日简单 RSI | 120 交易日 | - |
| `rsi_12` | 12 日简单 RSI | 120 交易日 | - |
| `boll_pct` | (close_T - MA20) / (2 × std20) | 20 交易日 | - |
| `obv_chg_20d` | (OBV_T - OBV_{T-20}) / avg_vol_20d | 120 交易日 | + |

**RSI 说明**：使用简单均值（非 Wilder 平滑），已有足够窗口（120 日），无参数偏差，更稳健。

**MACD 归一化**：histogram 除以 T 日收盘价，消除股价量纲差异，使横截面可比。

**OBV 归一化**：变化量除以 20 日平均成交量，消除市值差异。

### 2.2 外部数据因子（10 个）

| 因子名 | 数据来源 | PIT 口径 | 预期方向 | 特殊说明 |
|---|---|---|---|---|
| `hk_hold_ratio` | `hk_hold.ratio` | trade_date <= T | + | 2014-11-17 前全 NaN |
| `hk_hold_chg` | `hk_hold.ratio` | trade_date <= T | + | 30 交易日变化量；2014-11-17 前全 NaN |
| `analyst_eps_revision` | `analyst_rc_pit.eps` | pit_date <= T | + | 近 90d / 远 90d median；两端需 ≥ 2 条且均为正 |
| `analyst_rating_chg` | `analyst_rc_pit.rating` | pit_date <= T | + | 近 90d - 远 90d 均值；评级映射见下表 |
| `float_pct_30d` | `share_float.float_ratio` | ann_date <= T < float_date <= T+30 | - | 无解禁填 0（非 NaN） |
| `insider_net_buy` | `holder_trade_pit.change_ratio` | pit_date <= T | + | 净增减持比率；无事件填 0 |
| `chip_winner_rate` | `cyq_perf.winner_rate` | trade_date <= T | 无明确预期 | 2018 年前数据稀疏 |
| `cost_deviation` | `cyq_perf.weight_avg / cost_50pct - 1` | trade_date <= T | 无明确预期 | 2018 年前数据稀疏 |
| `pledge_ratio` | `pledge_stat.pledge_ratio` | end_date <= T | - | 无数据返回 NaN（不填 0） |
| `north_flow_5d` | `moneyflow_hsgt.north_money` | trade_date <= T | 环境变量 | 全市场广播，IC 预期 ≈ 0 |

**评级数字映射**：

| 字符串 | 数值 |
|---|---|
| 买入 / 强烈推荐 / 强推 | 5 |
| 增持 / 推荐 | 4 |
| 中性 / 持有 / 观望 | 3 |
| 减持 / 回避 | 2 |
| 卖出 | 1 |

---

## 3. 已知覆盖限制

| 因子 | 覆盖限制 | 处理方式 |
|---|---|---|
| `hk_hold_ratio` / `hk_hold_chg` | 2014-11-17 前无数据 | 全 NaN 截面正常落盘，属于预期 |
| `chip_winner_rate` / `cost_deviation` | 2018 年前数据稀疏 | 全 NaN 截面正常落盘，诊断文件记录 NaN 率 |
| `analyst_eps_revision` / `analyst_rating_chg` | 覆盖率受研报数量限制 | 报告数不足时返回 NaN |
| `north_flow_5d` | 截面恒定值 | 中性化后 ≈ 0，因子评价 IC ≈ 0，评价阶段自动排除 |

---

## 4. 领域风险与处理

| 风险点 | 处理方式 |
|---|---|
| MACD 有量纲（与价格同单位） | 除以 close_adj_T 归一化 |
| RSI 历史不足冷启动 | `shape[0] < 8/14` 时返回全 NaN |
| `boll_pct` std=0（价格不动） | `std.replace(0, np.nan)` 避免除零 |
| `cost_50pct=0` | `replace(0, np.nan)` 避免除零 |
| `pledge_ratio` 无数据 vs 无质押 | 无法区分，统一返回 NaN（不填 0）|
| `float_pct_30d` 无解禁事件 | 填 0（语义清晰：无压力）|
| `insider_net_buy` 无事件 | 填 0（语义清晰：中性）|
| `analyst_eps_revision` EPS 有负值 | 两端 median 须均 > 0 才计算比值 |

---

## 5. 验收命令

数据下载完成、阶段 4（`csv_to_parquet all`）完成后运行：

```bash
# 干运行：确认 15 个因子名被识别，不实际计算
python -m scripts.build_factor_panels --factor-set alternative --dry-run

# 冒烟测试：仅计算 3 个因子验证链路通畅
python -m scripts.build_factor_panels \
    --factor-set alternative \
    --factors macd_cross hk_hold_ratio pledge_ratio

# 全量
python -m scripts.build_factor_panels --factor-set alternative --resume
```

检查输出：
- `data/processed/factor_panels/macd_cross.parquet` 等 15 个文件存在
- `data/processed/factor_panel_diagnostics.parquet` 记录每期每因子的 NaN 率
- 2012-2014 期间 `hk_hold_ratio` 的 `n_raw` 应为 0（全 NaN，属于预期）
- 2012-2018 期间 `chip_winner_rate` 的 `n_raw` 应接近 0

---

## 6. 后续因子评价注意事项

运行 `run_factor_evaluation --factor-set alternative` 时：

- `north_flow_5d` IC 预期接近 0（截面恒定），评价后直接排除
- `hk_hold_*` 在 2014-11 前样本内 IC 统计不稳定，评价时需注意样本期
- `cyq_perf` 类因子：在 `seg_ic_ir.csv` 中 `2012-2014` 段的有效样本量极少，不参考该段结论
- 最终候选因子总数（原有 + 新增）不超过 30 个（约 27 + 5 = 32，需再筛选 2-3 个）

---

## 7. 单元测试实现纪要

> 对应 `phase_plan.md` 第 13 节（阶段 8 测试门禁）。
> 完成时间：2026-05-22。

### 7.1 新增测试文件

| 文件 | 测试类别 | 测试数 |
|---|---|---|
| `tests/test_alt_data_pit.py` | PIT 时间约束 | 14 个 |
| `tests/test_technical_factors.py` | 技术因子数值 + 时间边界 | 18 个 |
| `tests/test_hk_hold_coverage.py` | 北向持仓覆盖范围 | 7 个 |
| `tests/test_index_quote_code.py` | index_quote 代码校验 | 7 个 |
| `tests/test_data_expansion_config.py` | 配置一致性 | 17 个 |

**合计**：5 个文件，63 个测试用例。

### 7.2 测试策略

所有测试**不依赖磁盘数据**，通过 `monkeypatch` 注入合成 DataFrame：

| 测试模式 | 具体做法 |
|---|---|
| Loader PIT 过滤 | `monkeypatch.setattr(loader, "_read_cached", lambda f: 合成DF)` |
| 技术因子时间边界 | `monkeypatch.setattr(af, "_all_trading_dates", lambda: dates)` + `load_daily_quote` |
| 北向因子覆盖 | `monkeypatch.setattr(af, "load_hk_hold", lambda ...: 合成DF)` |
| 配置/常量 | 直接断言 `cfg.*` 常量值（无 mock 需求） |

集成测试（`TestIndexQuoteFileContent`）使用 `pytest.skip` 在数据文件未生成时自动跳过。

### 7.3 核心测试场景说明

#### test_alt_data_pit.py

| 类 | 核心断言 |
|---|---|
| `TestAnalystRcPIT` | `pit_date > T` 排除；`pit_date == T` 可见；超出 `lookback_days` 排除；`codes` 过滤生效 |
| `TestHolderTradePIT` | `pit_date > T` 排除；`in_de`（增/减）字段保留；超窗口排除 |
| `TestShareFloatPIT` | 三重过滤缺一不可：`ann_date <= T < float_date <= T+30` |

#### test_technical_factors.py

| 类 | 核心断言 |
|---|---|
| `TestTechnicalFactorTimeBoundary` | 5 个因子各自确认 `load_daily_quote` 的 `end <= T` |
| `TestTechnicalFactorInsufficientHistory` | 历史不足返回全 NaN 而非抛异常 |
| `TestRSIComputation` | 全负收益 RSI=0；全正收益 RSI=NaN（除零保守处理）；混合 RSI∈[0,100] |
| `TestBollPctNumerics` | 价格恒定→NaN；上涨→正；下跌→负 |
| `TestOBVChgDirection` | 全上涨窗口 OBV 变化=+20.0（已知精确值）；全下跌→负 |
| `TestMACDNormalization` | 同收益率序列、价差 10 倍→归一化后 MACD 相等 |

#### test_hk_hold_coverage.py

`factor_hk_hold_ratio`：2014-11-17 前全 NaN；有数据时值正确；取最近交易日快照。
`factor_hk_hold_chg`：2014-11-17 前全 NaN；变化量方向正确；精确值可验证。

#### test_index_quote_code.py

单元测试：`_window_start` 计算正确性；重复日期对窗口计算的干扰（说明单一指数的必要性）。
集成测试（可跳过）：实际文件无重复日期；覆盖 MARKET_START；覆盖 TRAIN_END。

#### test_data_expansion_config.py

- 8 个日期常量与 `phase_plan.md` 逐一比对
- 单调性 + 段间连续性（差值 1-2 天）
- `ALT_DATA_SUBDIRS` 12 个子目录完整、无多余、无重复
- 印花税切换日期 `2023-08-28` 及数值（10 bps / 5 bps）

### 7.4 运行命令

```bash
# 运行全部新增测试（约 63 个，纯 mock，秒级完成）
python -m pytest tests/test_alt_data_pit.py \
                 tests/test_technical_factors.py \
                 tests/test_hk_hold_coverage.py \
                 tests/test_index_quote_code.py \
                 tests/test_data_expansion_config.py -v

# 数据到位后（阶段 4 完成后）集成测试也会通过
python -m pytest tests/test_index_quote_code.py::TestIndexQuoteFileContent -v
```

### 7.5 设计决策记录

| 决策 | 理由 |
|---|---|
| `_compute_rsi` 全正收益时返回 NaN（而非 100） | 代码对 `losses=0` 做 `replace(0, np.nan)`；测试锁定此行为防止未来无意修改 |
| `obv_chg_20d` 已知精确值断言（=20.0） | 全上涨、恒定成交量的场景下公式结果确定，可精确验证而非只测方向 |
| `TestIndexQuoteFileContent` 用 `autouse fixture + pytest.skip` | 数据文件在阶段 4 前不存在；skipif 比条件注释更干净，CI 会记录为 skipped |
| 不 mock `pd.read_parquet` 测试 `_all_trading_dates` 本身 | 改为替换模块属性 `af._all_trading_dates` 可绕过 `@lru_cache`，更直接且符合项目既有模式 |
