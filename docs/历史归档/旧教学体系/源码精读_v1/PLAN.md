# 深度讲解文档 — 生成计划

> 本文件是"先写计划，再逐个生成"的执行路线图。
> 每完成一篇文档，在对应条目打 ✅，标注生成日期。

---

## 目标

现有的 `detailed_pipeline_guide.md` 解释了"每步做什么"，但没有展开"具体哪个函数、哪行代码、为什么这么写"。
本目录的文档补足这一层：**逐文件、逐函数，把代码逻辑翻译成人类语言**。

读者定位：具备基础 Python 能力，了解量化策略概念，希望能独立审查或修改本项目任何一个模块的人。

---

## 文档清单（共19篇）

| 序号 | 文件名 | 对应源文件 | 代码行数 | 状态 |
|------|--------|-----------|---------|------|
| 00 | `00_reading_guide.md` | — | — | ✅ 2026-05-20 |
| 01 | `01_config.md` | `src/config.py` | 107 | ✅ 2026-05-20 |
| 02 | `02_data_loader.md` | `src/data/loader.py` | 535 | ✅ 2026-05-20 |
| 03 | `03_pit_loader.md` | `src/data/pit_loader.py` | 328 | ✅ 2026-05-20 |
| 04 | `04_universe.md` | `src/data/universe.py` | 137 | ✅ 2026-05-20 |
| 05 | `05_financial_factors.md` | `src/factors/financial_factors.py` | 546 | ✅ 2026-05-20 |
| 06 | `06_price_factors.md` | `src/factors/price_factors.py` | 659 | ✅ 2026-05-20 |
| 07 | `07_preprocess.md` | `src/factors/preprocess.py` | 248 | ✅ 2026-05-20 |
| 08 | `08_ic_analysis.md` | `src/evaluation/ic_analysis.py` | 486 | ✅ 2026-05-20 |
| 09 | `09_quintile_backtest.md` | `src/evaluation/quintile_backtest.py` | 252 | ✅ 2026-05-20 |
| 10 | `10_shift_test.md` | `src/evaluation/shift_test.py` | 190 | ✅ 2026-05-20 |
| 11 | `11_combiner.md` | `src/signal/combiner.py` | 369 | ✅ 2026-05-20 |
| 12 | `12_covariance.md` | `src/portfolio/covariance.py` | 235 | ✅ 2026-05-20 |
| 13 | `13_optimizer.md` | `src/portfolio/optimizer.py` | 697 | ✅ 2026-05-20 |
| 14 | `14_backtest_engine.md` | `src/backtest/engine.py` | 627 | ✅ 2026-05-20 |
| 15 | `15_transaction.md` | `src/backtest/transaction.py` | 59 | ✅ 2026-05-20 |
| 16 | `16_metrics.md` | `src/backtest/metrics.py` | 210 | ✅ 2026-05-20 |
| 17 | `17_brinson.md` | `src/attribution/brinson.py` | 552 | ✅ 2026-05-20 |
| 18 | `18_factor_attr.md` | `src/attribution/factor_attr.py` | 782 | ✅ 2026-05-20 |

**总源码：7,019 行 / 18 个文件**

---

## 每篇文档的固定结构（模板）

每篇文档都按以下六节组织，保证格式统一、便于对照代码核查：

```
### 一、文件定位
  - 在流程中的位置（第几步）
  - 读什么 / 写什么（输入输出文件）
  - 被谁调用 / 调用谁

### 二、模块顶部：导入与常量
  - 逐行说明 import 的意图
  - 模块级常量的含义

### 三、核心函数逐一解析
  对该文件中每个 public 函数，展开：
  - 函数签名与参数含义
  - 执行逻辑（逐段说明，配关键代码片段）
  - 返回值：列名/类型/单位
  - 注意事项 / 常见坑

### 四、内部辅助函数
  private（_开头）函数，简要说明，不展开逐行

### 五、数据流图
  用 ASCII 图展示本文件的数据输入 → 处理 → 输出

### 六、量化领域知识补充
  代码背后的数学原理或金融逻辑（面向不熟悉该领域的读者）
```

---

## 分批生成顺序

按"依赖关系从小到大"排序，每批完成后用户可以验证，再进入下一批。

### 第一批：基础层（优先，其他文档都依赖它们）

| 序号 | 文件 | 理由 |
|------|------|------|
| 00 | `00_reading_guide.md` | 使用说明，先给读者定向 |
| 01 | `01_config.md` | 全局参数，所有文件都引用 |
| 02 | `02_data_loader.md` | 数据读取接口，第3-8步都依赖 |
| 03 | `03_pit_loader.md` | PIT是项目最核心的正确性约束 |
| 04 | `04_universe.md` | TradeState枚举，优化/回测都用 |

### 第二批：因子层

| 序号 | 文件 | 理由 |
|------|------|------|
| 05 | `05_financial_factors.md` | 15个财务因子，最多业务逻辑 |
| 06 | `06_price_factors.md` | 12个量价因子 |
| 07 | `07_preprocess.md` | 5步横截面流水线，是高频犯错点 |

### 第三批：评价层

| 序号 | 文件 | 理由 |
|------|------|------|
| 08 | `08_ic_analysis.md` | IC是核心评价指标，需展开数学 |
| 09 | `09_quintile_backtest.md` | 分组回测逻辑 |
| 10 | `10_shift_test.md` | 时间错位测试，未来函数防护 |

### 第四批：信号与优化层

| 序号 | 文件 | 理由 |
|------|------|------|
| 11 | `11_combiner.md` | 多因子合成，IC_IR加权核心逻辑 |
| 12 | `12_covariance.md` | LedoitWolf数学原理 |
| 13 | `13_optimizer.md` | 最复杂：三层Fallback + cvxpy |

### 第五批：回测层

| 序号 | 文件 | 理由 |
|------|------|------|
| 15 | `15_transaction.md` | 最短，先做热身 |
| 16 | `16_metrics.md` | 绩效指标定义 |
| 14 | `14_backtest_engine.md` | 最核心的回测主循环 |

### 第六批：归因层

| 序号 | 文件 | 理由 |
|------|------|------|
| 17 | `17_brinson.md` | BHB归因数学分解 |
| 18 | `18_factor_attr.md` | WLS因子归因，最长的文件 |

---

## 每篇文档的细化大纲

以下是19篇文档各自要覆盖的具体函数和知识点，生成前可直接作为写作提示。

---

### 00 — 阅读指南 `00_reading_guide.md`

- 本目录的定位：代码审查地图，而非入门教程
- 如何用这些文档做代码审查：找函数 → 读解析 → 对照源码 → 验证测试
- 阅读路径推荐（三种读者角色）：
  - 想整体了解架构的：1→2→5→8→11→13→14→17
  - 想审查数据正确性的：1→2→3→4→7→8→10
  - 想审查回测正确性的：1→2→4→13→15→14→16
- 文档与源码的对应关系（链接表）
- 如何发现文档与代码不一致（说明文档是按截止日期的代码写的）

---

### 01 — 全局配置 `01_config.md`

覆盖 `src/config.py` 全部 ~107 行。

**关键内容：**
- `TRAIN_START/END`、`VALID_START/END`、`TEST_START/END`：为何这样切分？训练6年、验证1年、测试2年的依据
- `STAMP_DUTY_CUT_DATE`：2023-08-28的历史背景（证监会减税政策）
- `EVAL_IC_EFFECTIVE_CRITERIA`：4个阈值各自的量化意义（IC_IR≥0.30从哪里来）
- `OPT_TE_TARGET_ANNUAL = 0.05`：年化跟踪误差5%的含义（≈每日约0.31%）
- `OPT_SINGLE_MAX_DEV = 0.01`：为何上限1%（中证500基准权重约0.2%，1%已经是5倍偏离）
- `NEW_STOCK_DAYS = 180`：新股6个月排除期的量化逻辑
- `RANDOM_SEED = 42`：在哪些地方被用到（numpy、sklearn、cvxpy）
- 参数冻结原则：这些值一旦在训练期确定就不能改，修改等同于偷看答案

---

### 02 — 数据加载器 `02_data_loader.md`

覆盖 `src/data/loader.py` 全部 ~535 行。

**关键函数：**
- `DataLoader.__init__(data_dir)` — 初始化，懒加载（第一次读时才加载parquet）
- `load_daily_quote(codes, start, end)` — 参数解析、列过滤、日期对齐；返回DataFrame的列含义（`open_adj`/`close_adj`/`ret` 都是后复权）
- `load_universe(date)` — 按月末日期返回成分股列表；如何处理"没有刚好这一天的快照"的情况（取最近一期）
- `load_stock_status(date)` — 返回每只股票在该日期的停牌/涨跌停状态；默认值处理（缺失→LOCKED）
- `load_industry(date)` — 行业数据的有效期处理（行业分类不每天变，取有效区间内最近一期）
- `get_rebalance_dates(start, end)` — 月末交易日的计算方式（`pd.tseries.offsets.BMonthEnd`）
- `get_next_trading_day(date)` — 用于确定T+1成交日
- `_validate_date_range()` — 内部校验，防止 start > end

**数据模式（Schema）说明：**
- `daily_quote.parquet`：行索引=(`date`, `ts_code`)，必有列=(`open_adj`, `close_adj`, `ret`)
- `index_member.parquet`：行=(`date`, `ts_code`)，列=(`weight`, `in_index`)
- `stock_status.parquet`：行=(`date`, `ts_code`)，列=(`is_suspended`, `limit_up`, `limit_down`, `is_st`)

---

### 03 — PIT安全财务加载器 `03_pit_loader.md`

覆盖 `src/data/pit_loader.py` 全部 ~328 行。

**核心概念先讲：什么是PIT（Point-In-Time）？**
- 财务报告有"报告期"（如2021Q3 = 2021-09-30）和"公告日"（如2021-10-28）
- T=2021-10-15时，2021Q3的报告还没公告，不能用！
- 必须用`ann_date`（公告日）做过滤，而不是`end_date`（报告期）

**关键函数：**
- `PITLoader.__init__()` — 加载 `financial_pit.parquet`，建立`ann_date`索引
- `get_financial_data(ts_code, query_date)` — 单股查询：`ann_date <= query_date` 的所有记录里取最新一条；返回值中各字段含义
- `get_cross_section(query_date, fields)` — 批量查询（调仓日用）：对500只股票同时做PIT过滤，返回横截面DataFrame
- `compute_ttm(ts_code, query_date, field)` — TTM（Trailing Twelve Months）计算：如何用季报数据拼出过去12个月合计
  - 公式：`TTM = Q4_{y-1} + (Q{n}_{y} - Q{n-1}_{y-1})`（追加法）
  - 实现细节：需要同时找到y年的最新季报和y-1年同期的季报
- `compute_yoy(ts_code, query_date, field)` — 同比增速：如何处理分母为负/为零的情况

**陷阱讲解：**
- 用`end_date`代替`ann_date`会引入多少天的前视偏差（历史上平均约45天）
- 季报和年报的公告时间规律（年报4月底、中报8月底、季报10月底/次年4月底）

---

### 04 — 投资域与交易状态 `04_universe.md`

覆盖 `src/data/universe.py` 全部 ~137 行。

**关键内容：**
- `TradeState` 枚举：5个状态（FREE/BUY_LIMIT/NO_SELL/NO_BUY/LOCKED）的含义和优先级
- `get_investable_universe(date, loader)` — 主函数：取中证500成分股 → 过滤新股（上市<180天）→ 过滤ST → 过滤完全停牌；返回 `(股票列表, 状态字典)`
- `_get_trade_state(row)` — 根据停牌/涨跌停/ST标志推断TradeState；优先级：停牌 > 涨停 > 跌停 > ST > FREE
- `_is_new_stock(ts_code, query_date, loader)` — 用上市日期判断是否是新股
- 为什么要用枚举而不是字符串：类型安全，避免拼写错误，支持 `match/case`

---

### 05 — 财务因子 `05_financial_factors.md`

覆盖 `src/factors/financial_factors.py` 全部 ~546 行。

**整体结构：**
- `FinancialFactorCalculator` 类（或函数集合）
- 公共入口：`compute_all_financial_factors(date, universe, pit_loader)`

**按因子类别逐一展开：**

价值类（5个）：
- `compute_ep_ttm()` — 分子=TTM净利润（`n_income_attr_p`），分母=总市值（`total_mv`）；为何不用市盈率（E/P是PE倒数，正值表示"便宜"）
- `compute_bp()` — 分子=账面净资产（`total_hldr_eqy_exc_min_int`），分母=总市值；账面净资产的字段选取原因
- `compute_sp_ttm()` — TTM营收（`revenue`）/总市值
- `compute_cfp()` — TTM经营现金流（`n_cashflow_act`）/总市值；为何现金流因子比利润因子更难造假
- `compute_dy_ttm()` — TTM现金分红（`cash_div`）/总市值；股息率与分红政策的关系

质量类（6个）：
- `compute_roe_ttm()` — TTM净利润/平均净资产（分母取首尾平均还是期末？代码里怎么做的）
- `compute_roa_ttm()` — TTM净利润/平均总资产
- `compute_gross_margin()` — 毛利润/营收；TTM口径还是最新季报口径？
- `compute_asset_turnover()` — TTM营收/平均总资产（效率因子）
- `compute_leverage()` — 总负债/总资产（负向因子：杠杆高→风险高→差）
- `compute_accrual()` — (净利润 - 经营现金流)/总资产；为何应计高→质量差（Sloan accrual anomaly）

成长类（4个）：
- `compute_np_yoy()` — 净利润同比增速；分母为负时如何处理
- `compute_np_ttm_yoy()` — TTM净利润同比增速：需要两个时点的TTM值
- `compute_rev_yoy()` — 营收同比增速
- `compute_roe_chg()` — ROE季度环比变化（最新季 vs 上一季，而非去年同期）

**金融股特殊处理：**
- 哪两个申万行业代码被标记为金融股（`801780.SI`银行、`801790.SI`非银金融）
- 处理方式：`factor_values[is_financial] = np.nan`
- 为何保险公司的`leverage`因子无效（保险公司天然高杠杆，不代表风险）

---

### 06 — 量价因子 `06_price_factors.md`

覆盖 `src/factors/price_factors.py` 全部 ~659 行。

**整体结构：**
- `PriceFactorCalculator` 类（或函数集合）
- 公共入口：`compute_all_price_factors(date, universe, loader)`

**逐因子展开：**

动量/反转（3个）：
- `compute_momentum_1m()` — `close_adj[T] / close_adj[T-21交易日] - 1`；21交易日≈1个月；为何1m是反转（短期过涨会回调）
- `compute_momentum_3m()` — 用63交易日；中期动量效应的实证依据
- `compute_momentum_12m()` — 用252交易日，但排除最近1个月（`[T-252:T-21]`）；跳过最近1个月是为什么（避免与1m反转效应叠加）

波动率（2个）：
- `compute_vol_20d()` — `daily_ret.std()` 用20个交易日；用std还是对数收益率的std
- `compute_vol_60d()` — 60日版本；为何波动率是负向因子（低波动异象：低波动股票长期超额）

流动性（2个）：
- `compute_turnover_1m()` — 平均换手率 = 成交量/流通股数；高换手可能代表散户炒作（负向）
- `compute_illiquidity_amihud()` — Amihud非流动性指标：`|ret| / (volume × price)`；数学意义：每元交易对价格的冲击；正向因子（流动性差的股票有溢价）

资金流（2个）：
- `compute_large_net_inflow()` — 大单净流入额（大单定义：≥20万元的成交）；从`moneyflow`接口获取
- `compute_large_inflow_ratio()` — 大单净流入占总成交的比例；机构资金流向信号

**计算窗口对齐的关键：**
- 每个量价因子的历史数据截止到T日收盘（包含T日）
- 为何不含T+1日：策略在T日盘后生成权重，T+1日才成交，所以T+1的价格不可用
- `loader.load_daily_quote()` 的 `end=date` 参数如何保证这一点

---

### 07 — 因子横截面预处理 `07_preprocess.md`

覆盖 `src/factors/preprocess.py` 全部 ~248 行。

**关键函数：**
- `preprocess_pipeline(factor_df, date, universe_status, industry_df, mcap_df)` — 主入口，按顺序执行5步
- `_filter_by_status(factor_df, universe_status)` — Step 1：把不可投资股票的因子置NaN
- `_mad_winsorize(series)` — Step 2：MAD去极值
  - `MAD = median(|x - median(x)|)`
  - `1.4826` 常数的来源：使MAD成为正态分布标准差的一致估计
  - 边界 = `[m - 3*1.4826*MAD, m + 3*1.4826*MAD]`；超出的截断到边界
  - 为何不用均值±3σ（样本均值对极端值敏感，中位数更鲁棒）
- `_zscore_standardize(series)` — Step 3：标准化；`(x - mean) / std`，NaN忽略不参与
- `_neutralize(factor_df, industry_df, mcap_df)` — Step 4：行业+市值中性化
  - 构建设计矩阵：31个行业哑变量 + `log(free_float_mcap)`
  - OLS回归：`factor ~ X`（用 `statsmodels.OLS`）
  - 取残差作为中性化后的因子值
  - 为何用OLS而不是WLS（WLS会给大市值股票更高权重，引入偏差）
  - 基准类别处理（31个行业哑变量放入OLS需要去掉一个基准类）
- `_zscore_standardize_residual(series)` — Step 5：残差再标准化（与Step 3相同逻辑）

**最容易犯错的点：**
- MAD边界必须只用当期横截面（不能用全样本历史）
- 中性化的OLS不加截距项（行业哑变量已经充当截距）
- 如果某只股票行业信息缺失，残差为NaN（不能填0）

---

### 08 — IC分析 `08_ic_analysis.md`

覆盖 `src/evaluation/ic_analysis.py` 全部 ~486 行。

**核心概念：Spearman相关系数 vs Pearson相关系数**
- IC 用 Spearman（基于排名）而非 Pearson（基于原始值）
- 原因：因子值与收益率往往不是线性关系，排名相关系数更鲁棒

**关键函数：**
- `compute_ic_series(factor_df, fwd_ret_df)` — 主函数
  - 对每个调仓日t，计算 `spearmanr(factor[t], fwd_ret[t])`
  - 返回 Series，index=调仓日，value=IC值
  - 处理：有效样本 < 10只时该期IC置NaN
- `compute_ic_stats(ic_series)` — 统计汇总
  - `ic_mean = ic.mean()`
  - `ic_std = ic.std()`
  - `ic_ir = ic_mean / ic_std`（类Sharpe ratio）
  - `t_stat = ic_ir * sqrt(n_obs)`（假设IC序列独立）
  - `p_value = 2 * (1 - norm.cdf(abs(t_stat)))`
  - `positive_ratio = (ic > 0).mean()`（方向一致性）
- `benjamini_hochberg_correction(p_values)` — BH多重检验校正
  - 27个因子同时检验时，单个p<0.05可能是偶然
  - BH步骤：对p值排序 → 计算调整阈值 `p_k ≤ k/m × α` → 找最大k
  - 返回每个因子的 `reject_H0`（True/False）和校正后p值
- `compute_forward_return(daily_quote, rebalance_dates)` — 计算月度前向收益
  - 公式：`fwd_ret[T] = open_adj[T'+1] / open_adj[T+1] - 1`
  - T+1 = T之后第一个交易日（成交日），T' = 下一个调仓日
  - 边界处理：最后一个调仓日没有下一期，该期fwd_ret置NaN

---

### 09 — 5分组回测 `09_quintile_backtest.md`

覆盖 `src/evaluation/quintile_backtest.py` 全部 ~252 行。

**关键函数：**
- `run_quintile_backtest(factor_df, fwd_ret_df, n_groups=5)` — 主函数
  - 每期按因子值分成5组（用`pd.qcut`），每组内等权
  - 返回每组的时间序列收益率
- `compute_group_return(group_stocks, fwd_ret_df, date)` — 计算单期单组收益
- `compute_spread_return(quintile_returns)` — Q5 - Q1（多空价差）
- `compute_cumulative_nav(period_returns)` — `(1 + r).cumprod()`
- `plot_quintile_returns()` — 可视化（5组累积净值）

**注意事项：**
- 分组用`pd.qcut`而不是`pd.cut`（等频分组而非等距分组）
- 边界股票（排名完全相同）如何处理（随机分配）
- 这是不含成本的评价：不扣佣金/印花税/滑点

---

### 10 — 时间错位测试 `10_shift_test.md`

覆盖 `src/evaluation/shift_test.py` 全部 ~190 行。

**关键函数：**
- `run_shift_test(factor_df, fwd_ret_df, shifts=[-1, 0, 1, 2, 3])` — 主函数
  - shift<0：用未来因子值（检测：若IC比shift=0还高，说明含未来信息）
  - shift=0：原始IC（基准）
  - shift>0：用历史因子值（IC应该随shift增大而下降）
- `_compute_shifted_ic(factor_df, fwd_ret_df, shift)` — 把因子整体时移shift期后重算IC
- `interpret_shift_pattern(shift_ic_dict)` — 自动判断是否疑似未来函数
  - 判断条件：`IC(shift=-1) / IC(shift=0) > 1.1`（超前1期IC高10%以上）

**误报处理：**
- 某些因子天然有"前瞻性"（如分析师预期），shift=-1的IC会更高，不一定是bug
- 报告中用 `⚠️ 疑似` 标记，需人工确认

---

### 11 — 信号合成 `11_combiner.md`

覆盖 `src/signal/combiner.py` 全部 ~369 行。

**关键函数：**
- `SignalCombiner.__init__(final_factors, method='ic_ir')` — 初始化，加载入选因子列表
- `compute_weights(ic_history_df, before_date)` — 计算IC_IR加权权重
  - 过滤：只用 `ic_date < before_date`（严格不含当期）
  - 过滤：IC序列长度 >= `SIGNAL_MIN_IC_HISTORY`（至少12期）
  - `ic_ir_i = ic_series.mean() / ic_series.std()`
  - 归一化：`w_i = ic_ir_i / sum(|ic_ir_j|)`
  - 如果某因子IC_IR为负：权重为负（等效于自动翻转方向）
- `combine(factor_panel_df, ic_history_df, date)` — 主函数
  - 调用`compute_weights`得到权重向量
  - 合成：`composite = factor_matrix @ weights`
  - 对合成信号再做MAD + Z-score
- `_fallback_equal_weight(factor_matrix)` — IC历史不足时的等权兜底

**冷启动问题：**
- 前12个月没有足够IC历史 → 等权合成
- 第13个月开始切换到IC_IR加权
- 切换点前后不应有明显跳变（因为等权只是IC_IR加权的特殊情况）

---

### 12 — 协方差估计 `12_covariance.md`

覆盖 `src/portfolio/covariance.py` 全部 ~235 行。

**核心问题：为什么不能用样本协方差矩阵？**
- 500只股票需要估计 500×500/2 ≈ 12.5万个参数
- 但每个月只有约252个收益率观测（1年日频数据）
- 参数比观测多 → 样本协方差矩阵奇异、病态、特征值噪声大

**LedoitWolf收缩估计：**
- 公式：`Σ_LW = (1-α) × Σ_sample + α × Σ_target`
- `Σ_target` = 对角矩阵（假设股票相互独立）
- `α` 由Oracle Approximating Shrinkage公式自动估计（不需要人工调参）
- `sklearn.covariance.LedoitWolf` 实现了这个估计量

**关键函数：**
- `CovarianceEstimator.__init__(window_months=24)` — 用过去24个月的日频收益率估计
- `estimate(date, universe, loader)` — 主函数
  - 读取过去24个月日频行情
  - 调用`LedoitWolf().fit(returns_matrix)` 
  - 验证正定性：`np.linalg.eigvalsh(Σ).min() > 0`
  - 不正定时：`Σ += ε × I`（`ε = 1e-6`），记录到meta
  - 保存到 `data/processed/cov_cache/{date}.parquet`
- `load_cached(date)` — 读取缓存（每次跑优化不需要重算协方差）

---

### 13 — 组合优化器 `13_optimizer.md`

覆盖 `src/portfolio/optimizer.py` 全部 ~697 行，最复杂的模块。

**关键函数：**
- `PortfolioOptimizer.__init__(config)` — 加载约束参数
- `optimize(alpha, Sigma, w_benchmark, w_prev, universe_status, date)` — 主入口
  - 调用 L1 → 失败时调 L2 → 失败时调 L3
  - 返回 `(weights, meta_dict)`
- `_solve_l1(alpha, Sigma, w_b, w_prev, universe_status)` — L1严格优化
  - 用 `cvxpy` 构建问题
  - 目标：`maximize alpha.T @ w`
  - 约束：二次跟踪误差 + 行业偏离线性 + 单股偏离线性 + 停牌/涨跌停
  - 求解器：先试 `CLARABEL`，失败再试 `SCS`
  - 超时：60秒
- `_solve_l2(alpha, w_b, w_prev, universe_status)` — L2线性规划
  - 去掉跟踪误差二次约束（因为它是L1失败的最常见原因）
  - 保留行业偏离、单股偏离约束
  - 用 `ECOS` 求解器（线性规划更快）
- `_solve_l3(alpha, w_b, w_prev, universe_status)` — L3 TopN等权
  - 计算 `n_min = ceil(free_budget / single_max_dev)`
  - `effective_topn = max(config.OPT_TOPN, n_min)`
  - 排除涨停股、锁定停牌股、保留跌停股下限
  - 按alpha排名取前effective_topn，等权

**cvxpy变量构建详解：**
- `w = cp.Variable(n)` — 权重向量
- `tracking_error_sq = cp.quad_form(w - w_b, Sigma)` — 二次型
- 为何不能直接写 `w @ Sigma @ w`：cvxpy需要识别DCP（凸规划）结构
- 停牌股约束：`w[locked_mask] == w_prev[locked_mask]`
- 涨停股约束：`w[limit_up_mask] <= w_prev[limit_up_mask]`

---

### 14 — 回测引擎 `14_backtest_engine.md`

覆盖 `src/backtest/engine.py` 全部 ~627 行。

**完整调仓周期逐步解析：**

```
T日（月末）
  read_target_weights(T)
  compute_pretrade_value(w_current, prices[T+1_open])

T+1日（执行日）
  get_trade_states(T+1)
  apply_trade_constraints(w_target, w_current, states)
  compute_trade_amounts(w_actual, w_current, pretrade_value)
  deduct_costs(nav, trade_amounts, date=T+1)
  update_holdings(w_actual)

T+1 ~ T'（持仓期）
  mark_to_market(holdings, close_adj[d])
  nav[d] = sum(holdings_value)

T'日（下一调仓日）
  ... 循环
```

**关键函数：**
- `BacktestEngine.__init__(config, loader)` — 初始化NAV序列、持仓字典
- `run(weights_df, start, end)` — 主循环，调用`_run_period()`遍历每个调仓日
- `_run_period(T, T_next, w_target)` — 单个调仓周期
- `_apply_trade_constraints(w_target, w_current, states)` — 约束应用
  - 停牌（LOCKED）：`w_actual = w_current`
  - 涨停（NO_BUY）：`w_actual = min(w_target, w_current)`
  - 跌停（NO_SELL）：`w_actual = max(w_target, w_current)`
  - 价格缺失：保守处理，视为LOCKED
- `_mark_to_market(date)` — 用收盘价估值；停牌股用上一个有效收盘价
- `_count_no_price(date)` — 只统计"有持仓或有目标权重"的股票中价格缺失数

**为何用T+1开盘价成交（不是T日收盘价）：**
- 策略在T日收盘后才能生成权重（因为用了T日的数据）
- 最早能执行交易是T+1日开盘
- 用收盘价会高估执行质量（实际无法在收盘价成交全部委托量）

---

### 15 — 交易成本 `15_transaction.md`

覆盖 `src/backtest/transaction.py` 全部 ~59 行。

**关键函数：**
- `compute_trade_cost(sell_value, buy_value, trade_date)` — 主函数
- `_get_stamp_duty_rate(trade_date)` — 印花税率切换逻辑
  - `trade_date >= pd.Timestamp('2023-08-28')` → `0.0005`
  - 否则 → `0.001`
  - 为何不写成常数：不同时期测试数据的成本会不一样，时间函数才正确

**成本结构逐行：**
- `stamp_duty = sell_value * rate`（卖出单边）
- `commission = (sell_value + buy_value) * 0.00025`（买卖双边，每边2.5bps）
- `slippage = (sell_value + buy_value) * 0.0008`（买卖双边，每边8bps）
- 合计：卖出成本约12.5bps（印花税10bps+佣金2.5bps），买入约10.5bps（佣金2.5bps+滑点8bps）

---

### 16 — 绩效指标 `16_metrics.md`

覆盖 `src/backtest/metrics.py` 全部 ~210 行。

**关键函数：**
- `compute_performance_metrics(nav_series, benchmark_nav, risk_free_rate=0.02)` — 主函数
- `_compute_max_drawdown(nav)` — 最大峰谷回撤：`max((peak - trough) / peak)`
  - 用rolling max实现：`peak = nav.cummax()`，`drawdown = (nav - peak) / peak`
- `_compute_information_ratio(excess_returns)` — `mean / std * sqrt(252)`
- `_compute_monthly_win_rate(nav, benchmark_nav)` — 按月重采样，超额为正的月数比例
- `_compute_turnover(weights_df)` — 年化双边换手：`sum(|w_t - w_{t-1}|) / n_periods * 12`（月频×12=年化）

**绩效指标的统计解释：**
- IR = 0.5 意味着什么：如果IC_IR = 0.5且每月调仓，约需2年才能在95%置信水平上判断策略有效
- 超额最大回撤 ≤ 10%：以中证500为基准，10%意味着策略相对基准在最差时期落后10%

---

### 17 — Brinson行业归因 `17_brinson.md`

覆盖 `src/attribution/brinson.py` 全部 ~552 行。

**BHB模型三个效应的数学定义：**
- 配置效应：`A_j = (w_p_j - w_b_j) × (r_b_j - r_b)`
- 选股效应：`S_j = w_b_j × (r_p_j - r_b_j)`
- 交叉效应：`I_j = (w_p_j - w_b_j) × (r_p_j - r_b_j)`
- 验证：`sum_j(A_j + S_j + I_j) = r_p - r_b`

**关键函数：**
- `BrinsonAttributor.__init__()` — 加载行业分类
- `attribute_period(T, T_next, w_portfolio, w_benchmark, daily_quote)` — 单期归因
  - 获取T+1执行后的实际权重（不是T日目标权重）
  - 计算行业内收益：用T+1开盘到T'+1开盘的区间收益，与回测引擎对齐
  - 计算每个行业的 A/S/I 效应
- `run(periods, ...)` — 遍历所有调仓期
- `reconcile(brinson_total, backtest_excess)` — 归因误差检验：允许偏差约0.4%

**为何要保留现金仓位（不强制归一化）：**
- 交易成本扣除后，组合总权重 < 1（有"现金"）
- 成本拖累需要在归因中体现，强制归一化会掩盖这个拖累

---

### 18 — 因子归因 `18_factor_attr.md`

覆盖 `src/attribution/factor_attr.py` 全部 ~782 行，最长的文件。

**方法论：Barra-style因子收益分解**
- 截面WLS回归：`r_i = Σ_k β_k × f_ki + ε_i`
  - `r_i` = 股票i在该调仓期的区间收益
  - `f_ki` = 股票i在因子k上的暴露（已经过中性化的Z-score值）
  - `β_k` = 因子k的截面收益（待估参数）
  - `ε_i` = 残差（纯选股alpha）
  - 权重 = 流通市值的平方根（大市值股票给更高权重）

**关键函数：**
- `FactorAttributor.__init__(final_factors, factor_directions)` — 加载6个因子及其预期方向
- `attribute_period(T, T_next, w_portfolio, factor_panel, daily_quote)` — 单期因子归因
  - 构建WLS设计矩阵：行=股票，列=6个因子暴露
  - 调用`statsmodels.WLS`
  - 提取回归系数（因子收益）和残差
  - 计算策略的因子暴露贡献：`w_portfolio @ factor_exposure_matrix`
- `compute_factor_contribution(beta, portfolio_exposure)` — 因子k对策略的贡献
  - `contribution_k = portfolio_exposure_k × beta_k`
- `run(periods, ...)` — 遍历所有期，汇总因子收益时间序列
- `compute_t_stats(factor_return_series)` — 对因子收益序列做t检验（是否显著不为零）

**WLS vs OLS：**
- 为什么用WLS而不是OLS：大市值股票的收益率更有代表性（流动性好、定价效率高），给更高权重
- 权重用`sqrt(mcap)`而非`mcap`：防止极端大市值股票主导回归

---

## 生成指引（给Claude）

每次用户请求生成某篇文档时，执行以下步骤：

1. **读取源文件全文**（`Read` 工具，读对应 `.py` 文件）
2. **读取相关测试文件**（`src/tests/test_*.py`）了解预期行为
3. **按上方大纲逐节展开**，对照实际代码而不是凭记忆写
4. **代码片段**：引用实际代码（不改变），加行号注释说明
5. **领域知识**：只讲与该模块直接相关的部分，不重复其他文档已讲的内容
6. **完成后**：在本 PLAN.md 的清单表格中将该条目改为 ✅ 并注明日期

每篇文档完成后，在本文件清单中更新状态。
