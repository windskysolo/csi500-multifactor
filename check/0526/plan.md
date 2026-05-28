# 2026-05-26 阶段审查计划

> 目标：针对 `check/0526/problems.md` 中两个异常现象，建立一套可复现、可审计的阶段排查路径，最终给出“正确原因”，而不是只给经验判断。
>
> 审查范围仅限训练期和验证期产物：训练期 `2012-01-01 ~ 2020-12-31`，验证期 `2021-01-01 ~ 2022-12-31`。正式测试集 `2023-2025` 不参与本次审查，不运行 `scripts/run_test_pipeline.py`。

---

## 1. 本次要回答的问题

### 问题一：Pipeline B 月度胜率 39.1% 是否真实

现象：

- Pipeline B 验证期 IR=0.483，年化超额收益=+2.77%。
- 同期月度胜率仅 39.1%，即 23 个月中 9 个月跑赢基准。
- 该组合层结果优于 Pipeline A 的 IC_IR 加权基线，但胜率异常偏低。

需要确认的根因类别：

| 根因类别 | 判断标准 | 结论含义 |
|---|---|---|
| 指标计算错误 | 独立复算的月度胜率与 `backtest_metrics_valid.parquet` 不一致 | `monthly_win_rate` 或输入 NAV 有 bug |
| NAV 口径或基准错误 | 策略 NAV / 基准 NAV 列、全收益基准、日期对齐存在不一致 | 回测结果不可直接解释 |
| 回测调用不一致 | 同一权重面板在不同入口得到不同 NAV 或指标 | Pipeline A/B 比较不是苹果对苹果 |
| 收益分布真实异常 | 月度复算一致，且 9 个正月贡献了主要超额 | 胜率低是真实特征，原因可能是 regime 或少数月份贡献 |

### 问题二：rolling-48m 信号 IC 更弱但组合 IR 更强是否真实

现象：

- expanding 验证期 IC_IR=0.715，组合 IR=0.483。
- rolling-48m 验证期 IC_IR=0.441，组合 IR=1.092。
- 信号层显著性更弱，但组合层表现翻倍，IC 与 IR 方向背离。

需要确认的根因类别：

| 根因类别 | 判断标准 | 结论含义 |
|---|---|---|
| 信号构建未来函数 | rolling 训练日期、CV 日期、`fwd_ret_panel` 标签对齐存在穿越 | rolling-48m 结果无效 |
| 产物新旧混用 | expanding 直接加载旧产物，rolling 用当前代码重跑，二者口径不同 | 当前对比无效，需统一重跑 |
| 回测阶段不对称 | 冷启动、优化器参数、协方差缓存、fallback、交易成本或状态约束不同 | IR 差异来自回测流程，不是信号质量 |
| 组合优化放大效应 | rolling 信号在持仓集中度、行业暴露、换手、约束触发上更适配优化器 | IR 提升可能真实，但不是单纯 IC 提升 |
| 验证期样本噪声 | paired test / bootstrap 无法拒绝两者 IR 差异为噪声 | 不能把 rolling-48m 定为稳定改进 |

---

## 2. 审查原则

1. 不触碰正式测试集。所有脚本必须显式限制在 `cfg.TRAIN_END` 和 `cfg.VALID_END` 以内。
2. 不在审查过程中调参。审查只复现、对齐、解释，不因为看到结果而改变 alpha、窗口、TE、lambda 或因子集合。
3. 先复算，再重跑。优先用现有 parquet 独立复算；只有当产物口径无法确认时，才做受控重跑。
4. 每个结论必须有三类证据：观测值、来源文件、反证检查。没有反证检查的结论只能写成假设。
5. 对异常优秀结果先怀疑未来函数或口径差异。rolling-48m 的 IR=1.092 不直接视为改进。
6. 基准必须是中证500全收益指数 `H00905.CSI` 的 `nav` 列。不得用价格指数 `close` 降级。
7. 成交口径按项目规则检查：T 日盘后生成权重，T+1 开盘成交，佣金双边各 2.5 bps，滑点双边各 8 bps，印花税按 `2023-08-28` 切换。

---

## 3. 产出物

本次审查建议在 `check/0526/` 下沉淀以下文件：

| 文件 | 内容 |
|---|---|
| `log.md` | 审查执行日志，记录每一步命令、输入文件、关键输出 |
| `artifact_manifest.csv` | 被审查产物的路径、mtime、shape、hash、日期范围 |
| `metric_recalc.csv` | 独立复算的 IR、年化超额、TE、月度胜率等指标 |
| `monthly_excess_review.csv` | Pipeline B、expanding、rolling-48m 的逐月收益、基准收益、超额收益 |
| `pipeline_equivalence.md` | 不同入口、同一权重或同一信号的回测等价性结论 |
| `signal_audit.csv` | 信号覆盖、NaN 率、横截面分布、IC 明细 |
| `alignment_audit.csv` | 每个预测日 T 的最大训练日期、purge 是否生效、标签日期对齐 |
| `optimizer_audit.csv` | 权重、fallback、约束、换手、行业/个股偏离审计 |
| `root_cause_report.md` | 最终根因报告，明确“已证实”“已排除”“仍不确定” |

---

## 4. 阶段 0：冻结审查对象和口径

### 目的

防止把“代码变了、旧产物没刷新”误判为策略机制差异。当前已看到一个高优先级风险：`experiments/ridge_rolling/run_rolling_experiment.py` 对 expanding 基准是直接加载既有 Pipeline B 产物，而 rolling 变体是当前脚本生成的新产物，因此必须先确认产物是否同一代码口径。

### 检查项

1. 记录 git 状态和当前 commit。
2. 记录以下文件的修改时间、文件大小、shape、日期范围：
   - `data/processed/composite_signal_ic_ir.parquet`
   - `experiments/ridge_signal/results/ridge_composite_panel.parquet`
   - `experiments/ridge_signal/results/ridge_coef_history.parquet`
   - `experiments/turnover_lambda_grid/results/lam_0050/backtest_nav_valid.parquet`
   - `experiments/turnover_lambda_grid/results/lam_0050/backtest_metrics_valid.parquet`
   - `experiments/turnover_lambda_grid/results/lam_0050/weights_optimized.parquet`
   - `experiments/ridge_rolling/results/rolling_48m_composite_panel.parquet`
   - `experiments/ridge_rolling/results/rolling_48m_coef_history.parquet`
   - `experiments/ridge_rolling/results/rolling_48m/backtest_nav_valid.parquet`
   - `experiments/ridge_rolling/results/rolling_48m/backtest_metrics_valid.parquet`
   - `data/processed/index_quote.parquet`
   - `data/processed/fwd_ret_panel.parquet`
3. 记录当前配置：
   - `cfg.TRAIN_START`, `cfg.TRAIN_END`
   - `cfg.VALID_START`, `cfg.VALID_END`
   - `cfg.TEST_START`, `cfg.TEST_END`
   - `cfg.OPT_TE_TARGET_ANNUAL`
   - `cfg.OPT_INDUSTRY_MAX_DEV`
   - `cfg.OPT_SINGLE_MAX_DEV`
   - `cfg.OPT_TOPN`
   - `cfg.COMMISSION_RATE`, `cfg.SLIPPAGE_RATE`
   - `cfg.STAMP_DUTY_BEFORE`, `cfg.STAMP_DUTY_AFTER`, `cfg.STAMP_DUTY_CUT_DATE`

### 判定标准

- 若产物日期范围不覆盖完整验证期，后续指标解释无效。
- 若 expanding 与 rolling 的产物生成时间明显来自不同代码阶段，先标记为“产物新旧混用风险”，在阶段 2 做统一回测复核。
- 若 `index_quote.parquet` 缺少 `nav` 列，本次所有超额收益和 IR 结论暂停。

---

## 5. 阶段 1：独立复算指标，确认异常是否存在

### 目的

先回答“39.1% 和 1.092 是否真的来自 NAV 本身”，而不是来自报告或 metrics 文件。

### 方法

对以下 NAV 文件独立复算日频和月频指标：

- Pipeline B：`experiments/turnover_lambda_grid/results/lam_0050/backtest_nav_valid.parquet`
- rolling-48m：`experiments/ridge_rolling/results/rolling_48m/backtest_nav_valid.parquet`
- Pipeline A：`data/processed/backtest_nav.parquet`，如列名包含 `strategy_v2` / `benchmark`，需明确选择列

独立复算指标：

1. 日频策略收益、日频基准收益、日频超额收益。
2. 年化策略收益、年化基准收益、年化超额收益。
3. 年化跟踪误差和 IR。
4. 月末 NAV 复采样后的月度策略收益、月度基准收益、月度超额收益。
5. 月度胜率：`mean(monthly_excess > 0)`。
6. 正超额月份数量、负超额月份数量、最大正超额月份、最大负超额月份。
7. 超额收益贡献集中度：正超额月份贡献 / 全部净超额。

### 需要特别检查的细节

- 当前 `src/backtest/metrics.py` 的 `monthly_win_rate` 使用 `resample("ME").last().pct_change().dropna()`。该逻辑会丢掉首个自然月收益；验证期 2021-01 至 2022-12 有 24 个自然月，但问题文件中有效月数为 23，二者是一致的。不能把“23 个月”本身视作 bug。
- 需要确认 NAV 文件首日是否恰好为验证期第一个交易日，且策略和基准均归一化到 1.0。
- 月度胜率只反映方向，不反映幅度。9 个大幅正超额月加 14 个小幅负超额月，可以同时得到正年化超额和低胜率。

### 判定标准

| 复算结果 | 结论 |
|---|---|
| 复算月胜率不等于 metrics 文件 | `src/backtest/metrics.py` 或 metrics 文件来源有问题 |
| 复算 IR 不等于 metrics 文件 | 年化、TE、日期对齐或输入 NAV 有问题 |
| 复算完全一致 | 异常是真实进入 NAV 的结果，进入阶段 2 到 8 查原因 |
| Pipeline B 仅少数月份贡献全部正超额 | 优先检查 regime、行业暴露、交易约束和权重集中度 |

---

## 6. 阶段 2：验证回测入口和产物是否等价

### 目的

确认 Pipeline B、rolling-48m、Pipeline A 的比较是否口径一致。特别是 rolling 实验脚本中，expanding 基准直接复用 `turnover_lambda_grid/lam_0050` 的旧产物，而 rolling 组合重新跑优化器和回测；这会产生“信号方法差异”和“脚本产物差异”混在一起的风险。

### 方法

1. 读取 Pipeline B 的 `weights_optimized.parquet`，直接调用当前 `src.backtest.engine.run_backtest` 重跑验证期。
2. 将重跑 NAV 与 `lam_0050/backtest_nav_valid.parquet` 做逐日差异比较：
   - `max_abs_diff(strategy)`
   - `max_abs_diff(benchmark)`
   - `max_abs_diff(excess_nav)`
   - 复算 metrics 差异
3. 用 rolling 脚本中的 `run_optimizer_backtest` 逻辑对 expanding 信号重新生成一次权重和回测，输出到临时审查目录，不覆盖原产物。
4. 比较“旧 Pipeline B 产物”和“当前代码重跑 expanding”的差异：
   - 权重矩阵逐期差异
   - 交易日志差异
   - fallback 分布差异
   - NAV 差异
   - metrics 差异
5. 对 rolling-48m 也做一次“读取现有权重再当前引擎重跑”的复核，确认 NAV 与现有文件一致。

### 判定标准

| 检查结果 | 结论 |
|---|---|
| 同一权重当前引擎重跑与现有 NAV 不一致 | 回测引擎或输入数据版本发生变化，现有产物不可直接比较 |
| expanding 旧产物与当前重跑差异显著 | rolling vs expanding 的 IR 差异可能来自新旧产物混用 |
| 同一信号、同一优化配置仍复现原差异 | 进入信号和优化机制审查 |

### 注意

重跑只允许写入 `check/0526/tmp/` 或新的审查目录。不得覆盖 `experiments/` 下的正式实验产物。

---

## 7. 阶段 3：数据口径和时间对齐审查

### 目的

排除最严重的领域错误：全收益基准错误、前向收益标签错位、T 日信号使用 T 日之后数据。

### 检查项

#### 3.1 基准口径

检查 `data/processed/index_quote.parquet`：

- 是否存在 `nav` 列。
- `nav` 是否用于 `_load_benchmark_nav`。
- 验证期基准收益是否与 NAV 文件中的 `benchmark` 收益一致。
- 任何脚本是否绕过 `run_backtest` 自行加载 `close` 作为基准。

判定：

- 若使用 `close` 或价格指数口径，所有超额收益和 IR 结论暂停。
- 若全部使用 `nav`，基准口径排除。

#### 3.2 前向收益标签

检查 `data/processed/fwd_ret_panel.parquet`：

- index 是否为调仓日 T。
- 每个 T 的标签是否表示 T 后下一持有期收益，而不是包含 T 之前或 T 当日不可得信息。
- 与 `index_member`、`factor_panels` 的 rebalance_date 是否一致。
- 抽样 5 个 T 和 10 只股票，用后复权价格手工复算 `fwd_ret_panel.loc[T, code]`。

判定：

- 若 `fwd_ret_panel.loc[T]` 对应的是 T 之前收益，Ridge 训练标签错误。
- 若 `fwd_ret_panel.loc[T]` 包含 T+1 之后收益，这是训练标签可以使用的历史已实现收益，但必须只在未来预测日 T' 的训练窗口中出现，不能用于同一 T 的信号。

#### 3.3 因子 PIT 口径

抽查 `src/data/pit_loader.py` 和 `data/processed/factor_panels/`：

- 财务数据是否用公告可用日期，而不是报告期直接前填。
- 因子面板日期是否只到验证期，且未混入测试期刷新产物。
- 因子预处理是否只用 T 日横截面边界，不使用全样本边界。

判定：

- 若因子层存在 PIT 错误，expanding 和 rolling 都可能被污染。此时不应解释 rolling 优于 expanding，而应先修复数据。

---

## 8. 阶段 4：信号面板审查

### 目的

确认 IC 与 IR 背离是否来自信号覆盖、横截面分布、极端值或股票池差异，而不是 Ridge 模型本身。

### 比较对象

- IC_IR 加权信号：`data/processed/composite_signal_ic_ir.parquet`
- expanding Ridge：`experiments/ridge_signal/results/ridge_composite_panel.parquet`
- rolling-48m：`experiments/ridge_rolling/results/rolling_48m_composite_panel.parquet`

### 检查项

1. 日期范围、调仓日数量是否一致。
2. 每期非 NaN 股票数、NaN 率、有效覆盖股票数。
3. 与当期中证500成分股的交集比例。
4. 信号横截面均值、标准差、偏度、峰度、最大值、最小值。
5. 每期 top 50 股票与 benchmark 成分权重的交集、行业分布和市值分布。
6. expanding 与 rolling-48m 的信号 rank correlation。
7. rolling-48m 相比 expanding 的换手来源：信号变化导致，还是优化器约束导致。
8. 每月 IC 明细，而不仅是 IC_IR 汇总：
   - IC 均值
   - IC 标准差
   - 正 IC 月份数
   - IC t 值和 p 值
   - 2021 与 2022 分年度 IC

### 判定标准

| 现象 | 可能原因 |
|---|---|
| rolling 覆盖股票显著少于 expanding | IR 改善可能来自隐式筛选股票池 |
| rolling 与 expanding rank correlation 很低 | 二者不是小幅窗口差异，而是暴露结构完全变化 |
| rolling IC 均值低但 IC 波动更低 | IC_IR 下降未必代表组合收益必然差 |
| rolling IC 差但 top 持仓收益贡献高 | 可能是组合优化选中了少数有效段，需做归因确认 |

---

## 9. 阶段 5：Ridge 训练窗口和未来函数审查

### 目的

rolling-48m 的 IR 明显高于 expanding，必须优先排除未来函数。重点不是看代码“感觉正确”，而是导出每个 T 实际用到的训练日期证据。

### 检查项

#### 5.1 预测日训练窗口

对每个预测日 T 导出：

- `prediction_date`
- `cutoff = T - purge_months`
- `window_start = cutoff - window_months`
- `min_train_date`
- `max_train_date`
- `n_train_dates`
- `max_train_date <= cutoff`
- `T not in train_dates`
- `train_dates` 是否均小于 T

预期：

- expanding：`train_dates <= T - 2 months`
- rolling-48m：`T - 50 months < train_dates <= T - 2 months`，因为代码是 `window_start = cutoff - window_months`

#### 5.2 CV fold 训练窗口

对每个 fold 导出：

- `val_start`
- `val_end`
- `cutoff = val_start - 2 months`
- `max_train_date`
- `min_val_date`
- `max_train_date < min_val_date`
- `max_train_date <= cutoff`

预期：

- train 和 validation 严格不重叠。
- validation 的 IC 只用于 alpha 选择，不应影响验证期 2021-2022 的训练标签。

#### 5.3 标签错位压力测试

建议做三个只读审查测试，不改正式产物：

1. 信号后移一期测试：用 `signal.shift(1)` 对同一 `fwd_ret_panel` 计算 IC，IC 应显著下降。
2. 信号前移一期测试：用 `signal.shift(-1)` 计算 IC。如果 IC 异常上升，说明原始信号可能靠近未来标签。
3. 标签打乱测试：按日期内随机打乱股票标签，IC 应接近 0。

判定：

- 若 rolling-48m 在未来敏感测试中表现异常，先按未来函数处理，不进入策略改进讨论。
- 若所有对齐测试通过，再把 rolling IR 改善视为可能真实或样本噪声。

---

## 10. 阶段 6：优化器和交易执行审查

### 目的

IC 是横截面信号质量，IR 是组合净值结果。两者背离可能来自优化器、约束、成本、冷启动和实际成交约束，而不是信号预测能力。

### 检查项

#### 6.1 优化配置一致性

比较 expanding 和 rolling-48m：

- `TE_FIXED = 0.06`
- `IND_DEV = cfg.OPT_INDUSTRY_MAX_DEV`
- `SGL_DEV = cfg.OPT_SINGLE_MAX_DEV`
- `TOPN = cfg.OPT_TOPN`
- `turnover_lambda = 0.005`
- `max_solve_seconds = 30.0`
- 协方差缓存路径和 miss 数量

判定：

- 任一配置不一致，不能把 IR 差异归因于 rolling 窗口。

#### 6.2 权重和约束

对每期导出：

- fallback level 分布。
- `constraint_compliant` 分布。
- 单股偏离最大值。
- 行业偏离最大值。
- ex-ante TE，如果 meta 中可用。
- active share。
- 持仓数量。
- top 10 权重集中度。
- 与 benchmark 权重相关性。

判定：

- 若 rolling 的 L3 fallback 更少，IR 改善可能来自优化成功率，而非信号本身。
- 若 rolling 行业或个股偏离更集中，IR 可能来自未被充分约束的暴露。
- 若 rolling active share 显著更高，需确认超额收益不是来自隐含风格暴露。

#### 6.3 交易执行和成本

检查：

- 执行映射是否为 T 到 T+1。
- trade log 中 `exec_date > rebalance_date`。
- 交易成本是否使用 `exec_date` 判断印花税。
- 验证期 2021-2022 全部处于印花税 10 bps 区间，不能出现 5 bps。
- `n_locked`、`n_no_buy`、`n_no_sell`、`n_no_price` 是否在两者间显著不同。
- 年化双边换手是否在目标 5-15 倍内。

判定：

- 若 rolling 成本明显低、约束阻塞明显少，IR 改善可能来自交易可执行性差异。
- 若两者成本和交易约束一致，再进入收益归因。

---

## 11. 阶段 7：收益分布和归因解释

### 目的

如果阶段 1 到 6 没有发现 bug，则解释“为什么 Pipeline B 低胜率仍有正 IR”和“为什么 rolling-48m 弱 IC 仍强 IR”。

### 检查项

1. 逐月收益贡献：
   - 策略收益
   - 基准收益
   - 超额收益
   - 累计超额收益
   - 该月是否跑赢
2. 正负月份幅度：
   - 正超额月份均值和中位数
   - 负超额月份均值和中位数
   - win/loss payoff ratio
3. 分年度拆解：
   - 2021 年 IR、胜率、超额收益
   - 2022 年 IR、胜率、超额收益
4. 市场状态拆解：
   - 基准上涨月和下跌月
   - 高波动月和低波动月
   - 超额回撤期
5. 权重归因：
   - 行业贡献
   - 个股集中贡献
   - 是否由少数月份或少数股票贡献主要收益
6. 如果已有 Brinson 产物，检查 rolling 与 expanding 的行业 allocation / selection 差异。

### 判定标准

| 现象 | 解释 |
|---|---|
| Pipeline B 正超额月份少但平均正超额明显大于负超额 | 39.1% 胜率真实，收益结构偏右尾 |
| rolling-48m 主要在少数行业或少数月份贡献 IR | 结果可能真实但稳健性弱 |
| rolling-48m 在 2021 和 2022 都优于 expanding | 改进更可信 |
| rolling-48m 只在一个短窗口显著优于 expanding | 更可能是验证期噪声 |

---

## 12. 阶段 8：统计显著性审查

### 目的

验证期只有 23 个有效月，不能只看 IR 点估计。rolling-48m 的 IR=1.092 需要证明不是样本噪声或多窗口挑选的结果。

### 检查项

1. 对 monthly excess return 计算 IR 的近似置信区间。
2. 对 expanding 与 rolling-48m 的月度超额收益做 paired difference：
   - 均值
   - 标准误
   - t 统计量
   - p 值
3. 做 block bootstrap 或普通 bootstrap，估计 IR 差值分布。
4. 对 rolling-36m、48m、60m 三个窗口做多重比较提示：
   - 不能只报告 48m 最好。
   - 至少披露三者均已试验。
   - 如果声明 48m 显著更优，需要考虑 BH 或 Bonferroni 校正。
5. IC 层面也做同样处理：
   - IC 均值
   - IC_IR
   - t 统计量
   - p 值
   - 正 IC 月份数

### 判定标准

| 结果 | 结论 |
|---|---|
| rolling 与 expanding 月度超额差值 p 值不显著 | 不得声称 rolling 稳定优于 expanding，只能写成验证期样本内观察 |
| 48m 在多重比较校正后不显著 | 不能把 48m 定为确定最优窗口 |
| 48m 在 IC 层不强但组合层强 | 必须结合阶段 6、7 的权重和归因解释 |

---

## 13. 阶段 9：根因判定框架

最终报告不要只写“可能因为”。每个问题按以下格式给出结论。

### 问题一结论格式

1. `monthly_win_rate=39.1%` 是否复现：
   - 是 / 否
   - 证据文件：
   - 独立复算值：
2. 如果复现，低胜率的直接数学原因：
   - 正超额月份数量：
   - 负超额月份数量：
   - 正超额月份平均幅度：
   - 负超额月份平均幅度：
   - 最大贡献月份：
3. 是否存在口径或回测 bug：
   - 已排除项：
   - 未排除项：
4. 最终原因分类：
   - 指标 bug / 口径不一致 / 真实收益分布 / 样本噪声 / 其他

### 问题二结论格式

1. rolling-48m IR=1.092 是否复现：
   - 是 / 否
   - 证据文件：
   - 当前代码重跑值：
2. 信号构建是否存在未来函数：
   - 训练窗口审计：
   - CV 审计：
   - fwd_ret 对齐审计：
   - shift test：
3. 回测是否等价：
   - expanding 旧产物 vs 当前重跑：
   - rolling 旧产物 vs 当前重跑：
4. IR 提升来自哪里：
   - 信号覆盖：
   - 权重差异：
   - fallback 差异：
   - 行业/个股暴露：
   - 交易成本和换手：
   - 月度收益贡献：
5. 统计结论：
   - IR 差值置信区间：
   - paired test p 值：
   - 多窗口比较后的解释：
6. 最终原因分类：
   - 未来函数 / 产物混用 / 回测不对称 / 优化器放大 / 样本噪声 / 真实但待稳健性验证

---

## 14. 推荐执行顺序

### P0：当天必须完成

1. 阶段 0：冻结产物和配置。
2. 阶段 1：独立复算 NAV 指标和逐月超额收益。
3. 阶段 2：同一权重用当前回测引擎重跑，确认 NAV 是否可复现。

完成 P0 后应能判断：

- 39.1% 是否是 metrics bug。
- rolling-48m 的 1.092 是否至少能从现有 NAV 复算出来。
- expanding 与 rolling 对比是否存在明显产物新旧混用。

### P1：若 P0 未发现明确 bug，继续完成

4. 阶段 3：基准、fwd_ret、PIT 时间对齐审查。
5. 阶段 4：信号面板覆盖和 IC 明细审查。
6. 阶段 5：Ridge 训练窗口和未来函数审查。
7. 阶段 6：优化器和交易执行审查。

完成 P1 后应能判断：

- rolling-48m 是否因未来函数或回测不对称导致异常优秀。
- IC 与 IR 背离主要发生在信号层、优化层还是交易层。

### P2：用于写最终解释

8. 阶段 7：收益分布和归因解释。
9. 阶段 8：统计显著性审查。
10. 阶段 9：写 `root_cause_report.md`。

完成 P2 后应能给出：

- 对问题一的明确原因。
- 对问题二的明确原因或审慎结论。
- 哪些结果可以继续作为验证期研究依据，哪些必须废弃或重跑。

---

## 15. 优先级最高的可疑点

当前从文件阅读中已经发现以下优先级较高的审查点：

1. `monthly_win_rate` 的 23 个有效月不必然是 bug。`resample("ME").last().pct_change()` 会自然丢掉首月，因此 2021-2022 的 24 个自然月对应 23 个月度收益。
2. `src/backtest/engine.py` 已明确禁止从 `close` 降级为价格指数，基准层面看起来有防线，但仍需检查历史产物是否由旧代码生成。
3. `experiments/ridge_rolling/run_rolling_experiment.py` 对 expanding 基准直接加载旧 `lam_0050` 产物，而 rolling 变体由当前脚本重跑。这是解释 rolling-48m IR 翻倍前必须排除的产物口径风险。
4. `rolling_combiner.py` 的训练窗口代码表面上满足 `train_dates <= T - 2 months`，但必须导出每个 T 的实际训练日期审计表；只读代码不足以证明没有未来函数。
5. rolling-48m 是 36m、48m、60m 三个窗口中事后最优的一个。即使没有 bug，也必须在最终报告中披露多窗口比较和样本量不足风险。

---

## 16. 不应做的事

1. 不因为 rolling-48m 验证期 IR 高就替换主管线信号。
2. 不用测试集验证本次疑问。
3. 不在审查过程中新增窗口、调 alpha、调 TE 或调 lambda。
4. 不把“胜率正常应为 55-65%”当作证据。它只是先验经验，真正证据来自逐月收益分布和复算结果。
5. 不把 IC_IR 和组合 IR 简单等同。组合 IR 还受优化器、约束、成本、行业暴露、交易状态和样本路径影响。
