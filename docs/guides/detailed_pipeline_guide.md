# 中证500多因子增强项目：完整流程详解

> 本文档的目标：让你能完整追踪"一支股票的收益率数字是怎么从原始数据走到最终报告的"，以及每一步在哪个文件里、用什么方法做的。

---

## 总体地图

```
第0步  全局配置        src/config.py
         ↓
第1步  下载原始数据    scripts/download_tushare.py
                       scripts/download_supplement.py
         ↓
第2步  格式转换        scripts/csv_to_parquet.py
         ↓ 生成 data/processed/*.parquet
第3步  构建因子面板    scripts/build_factor_panels.py
         ↑ 调用
         src/data/loader.py          ← 读 parquet
         src/data/pit_loader.py      ← PIT安全的财务数据
         src/data/universe.py        ← 投资域和状态
         src/factors/financial_factors.py  ← 财务因子
         src/factors/price_factors.py      ← 量价因子
         src/factors/preprocess.py         ← 去极值/中性化
         ↓ 生成 data/processed/factor_panels/
第4步  因子评价        scripts/run_factor_evaluation.py
         ↑ 调用
         src/evaluation/ic_analysis.py      ← IC检验
         src/evaluation/quintile_backtest.py ← 5分组回测
         src/evaluation/shift_test.py       ← 时间错位测试
         ↓ 生成 reports/factor_evaluation/*.csv/json/md
第5步  合成信号        scripts/run_signal_combination.py
         ↑ 调用
         src/signal/combiner.py
         ↓ 生成 data/processed/composite_signal_*.parquet
第6步  组合优化        scripts/run_portfolio_optimization.py
         ↑ 调用
         src/portfolio/covariance.py   ← 协方差矩阵
         src/portfolio/optimizer.py    ← 二次规划
         ↓ 生成 data/processed/portfolio_weights_*.parquet
第7步  策略回测        scripts/run_backtest.py
         ↑ 调用
         src/backtest/engine.py        ← 主循环
         src/backtest/transaction.py   ← 交易成本
         src/backtest/metrics.py       ← 指标计算
         ↓ 生成 data/processed/backtest_*.parquet
第8步  业绩归因        scripts/run_attribution.py
         ↑ 调用
         src/attribution/brinson.py    ← 行业归因
         src/attribution/factor_attr.py ← 因子归因
         ↓ 生成 reports/analysis_v2_results.md
```

---

## 第0步：全局配置中心 `src/config.py`

**作用：** 整个项目所有参数的唯一来源。所有脚本都 `from src import config as cfg` 来引用，不允许在代码里散落硬编码数字。

### 核心参数一览

```
时间划分
  TRAIN_START = 2016-01-01    训练期开始
  TRAIN_END   = 2021-12-31    训练期结束
  VALID_START = 2022-01-01    验证期开始
  VALID_END   = 2022-12-31    验证期结束
  TEST_START  = 2023-01-01    测试期开始（不得提前看）
  TEST_END    = 2025-12-31    测试期结束

交易成本
  STAMP_DUTY_CUT_DATE = 2023-08-28  印花税率变化节点
  STAMP_DUTY_BEFORE   = 0.001       10bps（2023-08-28之前）
  STAMP_DUTY_AFTER    = 0.0005      5bps（2023-08-28之后）
  COMMISSION_RATE     = 0.00025     佣金 2.5bps 双边各收
  SLIPPAGE_RATE       = 0.0008      滑点 8bps 双边各计

因子筛选阈值（训练期冻结）
  EVAL_IC_EFFECTIVE_CRITERIA:
    min_ic_ir   = 0.30    IC_IR至少0.3
    min_ic_mean = 0.02    |IC均值|至少0.02
    max_p_bh    = 0.05    BH校正后p值<0.05
    min_dir     = 0.55    方向一致性>55%

组合优化约束（训练期冻结）
  OPT_TE_TARGET_ANNUAL = 0.05    年化跟踪误差上限 5%
  OPT_INDUSTRY_MAX_DEV = 0.02    行业权重偏离上限 2%
  OPT_SINGLE_MAX_DEV   = 0.01    单股权重偏离上限 1%
  OPT_TOPN             = 50      L3兜底选股数量

信号合成参数（训练期冻结）
  SIGNAL_MIN_IC_HISTORY   = 12   最少需要12期IC历史才用IC_IR加权
  SIGNAL_WINDOW_MONTHS    = 24   滚动窗口24个月
  SIGNAL_MIN_VALID_FACTORS = 5   每只股票至少5个因子有效

其他
  NEW_STOCK_DAYS         = 180   上市不足180天视为新股，排除
  EVAL_HIGH_CORR_THRESHOLD = 0.70 因子相关系数>0.70视为冗余
  RANDOM_SEED            = 42
```

**为什么重要：** 这些参数都是在训练期确定的，一旦确定就不能再根据测试集结果修改，否则就是对测试集的过拟合（偷看答案）。

---

## 第1步：下载原始数据

### 文件：`scripts/download_tushare.py`

从 **Tushare Pro**（专业金融数据服务）下载，保存到 `data/raw/`。

下载的核心数据表：

| 下载内容 | Tushare接口 | 用途 |
|---|---|---|
| 每日行情（开/高/低/收/量）| `daily_adj` | 计算收益率、买卖价格 |
| 后复权因子 | `adj_factor` | 做复权处理 |
| 每日基本面（流通市值等）| `daily_basic` | 市值中性化 |
| 利润表（季报）| `income` | 利润因子 |
| 资产负债表 | `balancesheet` | 账面价值因子 |
| 现金流量表 | `cashflow` | 现金流因子 |
| 财务指标汇总 | `fina_indicator` | ROE、ROA等 |
| 停牌状态 | `suspend_d` | 交易约束 |
| 涨跌停状态 | `stk_limit` | 交易约束 |
| 行业分类（SW2021）| `index_classify` | 行业中性化 |
| 指数成分股权重 | 自研聚合 | 基准权重 |

**PIT设计：** 财务数据下载时保留 `ann_date`（公告日期），这样后续可以按"T日之前公告的最新数据"来做PIT过滤，不会用到T日之后才公布的财报。

### 文件：`scripts/download_supplement.py`

补充数据（停牌记录、指数成分快照等），走另一个数据端点。

---

## 第2步：格式转换 `scripts/csv_to_parquet.py`

**做什么：** 把 `data/raw/*.csv` 转换成 `data/processed/*.parquet`。

**为什么用Parquet格式：** CSV是纯文本，读1GB文件要几秒；Parquet是列式存储压缩格式，读同样的数据快10倍以上，而且类型信息不会丢失（日期就是日期，不是字符串）。

转换过程中同时做一些清洗：

```
1. 日期列统一转为 pd.Timestamp
2. 收益率列（ret）= close_adj[t] / close_adj[t-1] - 1
   （后复权收盘价计算，这里修复了原来的F2-001问题）
3. 行业分类：优先读 industry_sw2021.csv，兼容旧 industry_citics.csv
4. stock_status 合并停牌/涨停/跌停/ST/新股状态成一张宽表
```

### 核心输出文件

| 文件名 | 内容 | 行数（约）|
|---|---|---|
| `daily_quote.parquet` | 每日行情，含 `open_adj/close_adj/ret` | 270万行（500股×10年×252天）|
| `financial_pit.parquet` | PIT安全的财务宽表，含 `_pit_inc/_pit_bal/_pit_cf` | 5.7万行 |
| `index_member.parquet` | 每月成分股快照（哪些股票在中证500里）| 约5000行 |
| `stock_status.parquet` | 每日每股的停牌/涨跌停状态 | 约200万行 |
| `industry.parquet` | 每日行业归属 | 约180万行 |

---

## 第3步：构建因子面板

### 什么是"因子面板"

一张表，行是 `(调仓日, 股票代码)`，列是各种因子值。

```
                   ep_ttm   bp   roe   momentum_1m  ...
(2016-01-29, 000001.SZ)  0.08  0.6  0.12   -0.03  ...
(2016-01-29, 000002.SZ)  0.05  0.8  0.08   +0.02  ...
(2016-02-29, 000001.SZ)  0.09  0.6  0.13   +0.01  ...
...
```

每月末（月频调仓）为所有中证500成分股计算一次因子值。

### 文件：`src/data/loader.py`

**统一读取接口**，其他模块统一通过它来读parquet数据，不直接用 `pd.read_parquet`。

主要函数：

- `load_daily_quote(codes, start, end)` → 返回日频行情数据
- `load_universe(date)` → 返回某个日期中证500的成分股列表
- `load_stock_status(date)` → 返回某个交易日的停牌/涨跌停状态
- `load_industry(date)` → 返回股票行业分类
- `get_rebalance_dates(start, end)` → 返回所有月末调仓日列表

### 文件：`src/data/pit_loader.py`

**PIT安全的财务数据加载器**。

核心逻辑：

```python
# 错误做法（会用到未来数据）：
data = financial_pit.query("end_date == '2021-09-30'")

# 正确做法（PIT）：
data = financial_pit.query("pit_date <= '2021-12-31'")  # 只取12月31日前已公告的
data = data.sort_values("pit_date").groupby("ts_code").last()  # 每只股票取最新
```

还提供 `compute_ttm()`（过去12个月滚动汇总）和 `compute_yoy()`（同比增速）。

### 文件：`src/data/universe.py`

**投资域管理** — "哪些股票可以买"

```
TradeState（交易状态枚举）：
  FREE      - 可自由交易
  BUY_LIMIT - 只能卖出（ST股票、新股）
  NO_SELL   - 只能买入（跌停股）
  NO_BUY    - 只能卖出（涨停股）
  LOCKED    - 完全不能动（停牌股，或状态数据缺失）
```

核心函数 `get_investable_universe(date)` 返回该日期可投资的中证500股票列表，已排除：
- 上市不足180天的新股
- ST/ST*股票
- 完全停牌的股票

### 文件：`src/factors/financial_factors.py`

**财务因子计算**，共15个因子：

```
价值类（5个）
  ep_ttm   = TTM净利润 / 总市值         （市盈率的倒数，越高越便宜）
  bp       = 账面净资产 / 总市值         （市净率的倒数）
  sp_ttm   = TTM营收 / 总市值            （市销率的倒数）
  cfp      = TTM经营现金流 / 总市值      （现金流版市盈率）
  dy_ttm   = TTM现金分红 / 总市值        （股息率）

质量类（6个）
  roe_ttm  = TTM净利润 / 净资产          （权益回报率）
  roa_ttm  = TTM净利润 / 总资产
  gross_margin = 毛利润 / 营收
  asset_turn   = 营收 / 总资产           （资产周转率，衡量效率）
  leverage     = 总负债 / 总资产         （负向因子，杠杆越高越差）
  accrual      = (净利润 - 经营现金流) / 总资产  （负向因子，应计越高质量越差）

成长类（4个）
  np_yoy      = 净利润同比增速
  np_ttm_yoy  = TTM净利润同比增速
  rev_yoy     = 营收同比增速
  roe_chg     = ROE环比变化
```

**金融股特殊处理（修复后）：** 银行（801780.SI）和非银金融（801790.SI）的财务指标含义与其他行业完全不同（比如银行的"负债"是正常的业务，不是风险），所以这两个行业的财务因子全部置NaN，不参与选股。

### 文件：`src/factors/price_factors.py`

**量价因子计算**，共12个因子：

```
动量/反转（3个）
  momentum_1m    = 过去1个月区间收益率（反转，短期过涨会回调，负向）
  momentum_3m    = 过去3个月区间收益率（中期动量，正向）
  momentum_12m   = 过去12个月区间收益率（长期动量，正向）

波动率（2个）
  vol_20d  = 过去20日日收益率标准差（负向，低波动更好）
  vol_60d  = 过去60日日收益率标准差（负向）

流动性（2个）
  turnover_1m      = 过去1个月平均换手率（高换手可能是散户炒作）
  illiquidity_amihud = 价格冲击系数（|日收益率| / 日成交量）（低流动性有溢价）

资金流（2个）
  large_net_inflow     = 大单净流入额（机构资金流向信号）
  large_inflow_ratio   = 大单净流入占比
```

**注意：** 量价因子不需要PIT处理，因为这些数据在T日收盘后就全部可知，不存在公告延迟问题。

### 文件：`src/factors/preprocess.py`

**因子横截面预处理**，5步流水线，每个因子在每个调仓日独立执行：

```
Step 1：行业/状态过滤
  - 金融股的财务因子 → 置NaN
  - 新股、ST、完全停牌的股票 → 置NaN
  （这些股票不可投资，让它们在选股中没有因子值）

Step 2：MAD去极值（Winsorization）
  MAD = Median Absolute Deviation（中位数绝对偏差）
  
  边界 = 中位数 ± 3 × 1.4826 × MAD
  
  例：因子值 = [0.1, 0.2, 0.15, 0.18, 0.9（极端值）]
  中位数 = 0.18，MAD ≈ 0.03，边界上限 ≈ 0.18 + 3×1.4826×0.03 ≈ 0.31
  0.9 被截断为 0.31
  
  关键：边界只用当期横截面的值计算，不用历史全样本
  （否则未来的数据会影响边界，造成前视偏差）

Step 3：Z-score标准化
  z = (x - 均值) / 标准差
  
  每只股票的因子值变成"它比平均水平高几个标准差"
  结果：均值≈0，标准差≈1
  NaN保持NaN，不参与计算

Step 4：行业+市值中性化（OLS回归）
  目的：去掉因行业差异和市值大小带来的"伪因子"
  
  方法：把因子值对31个行业虚拟变量 + log(自由流通市值) 做回归，取残差
  
  残差 = 因子值 - 行业效应 - 市值效应
  
  例：某股票ROE高，可能只是因为它是银行（行业平均ROE就高），
  中性化后的残差才代表"在银行行业内，这只股票ROE比同行还高多少"

Step 5：残差再标准化
  再做一次Z-score，让残差也服从均值0、标准差1的分布
```

### 脚本：`scripts/build_factor_panels.py`

把上面所有步骤串起来，对每个调仓日（月末）、每只成分股，算出27个预处理后的因子值，保存成：

```
data/processed/factor_panels/
  factor_panel_train.parquet    训练期 (2016-2021)
  factor_panel_valid.parquet    验证期 (2022)
  factor_panel_diagnostics.parquet  预处理诊断（每步过滤了多少股票）
```

---

## 第4步：因子评价

### 文件：`src/evaluation/ic_analysis.py`

**核心概念：IC（Information Coefficient，信息系数）**

IC = 当期因子值排名 与 下期股票收益率排名 的Spearman相关系数

取值范围 -1 到 1：
- IC > 0：因子值高的股票，下个月收益率倾向于更高（正向因子）
- IC < 0：因子值高的股票，下个月收益率倾向于更低（负向因子）
- IC ≈ 0：没有预测能力

**评价一个因子的标准：**

```
IC均值    ：|平均IC| ≥ 0.02   （至少有一定预测能力）
IC_IR     ：|IC均值/IC标准差| ≥ 0.30  （稳定性，类似Sharpe ratio）
t统计量   ：对应p值 < 0.05（统计显著）
BH校正p值 ：同时检验27个因子时需要Benjamini-Hochberg校正
方向一致性：同向期数 > 55%（不能时正时负）
```

**Forward Return的计算方式（关键）：**

```
fwd_ret[T, stock] = open_adj[T'+1] / open_adj[T+1] - 1

T    = 调仓日（月末）
T+1  = T之后第一个交易日（执行买入）
T'   = 下一个调仓日
T'+1 = T'之后第一个交易日（执行卖出）
```

为什么用开盘价而不是收盘价？
因为策略是T日盘后生成权重，T+1日开盘成交，所以真实的"买入价格"是T+1开盘价。

**批量IC检验（27个因子同时检验）：**

多重检验问题：如果你同时检验27个随机因子，偶然也会有1-2个p<0.05（纯属运气）。BH（Benjamini-Hochberg）校正解决这个问题，把误报率控制在5%。

### 文件：`src/evaluation/shift_test.py`

**时间错位测试：检测未来函数**

如果一个因子的IC在时间错位后仍然很高，说明它可能用到了未来的信息。

```
正常情况：
  原始因子（T日 → T+1期收益）的IC_IR = 0.6  ✓正常
  滞后1期（T-1日因子 → T+1期收益）的IC_IR = 0.2  ✓正常下降

疑似未来函数：
  原始IC_IR = 0.8
  超前1期（T+1日因子 → T+1期收益）的IC_IR = 0.9  ！比原始还高！
  这说明"未来的因子值"预测能力更强，即当前T日已经含有了T+1的信息 → 未来函数！
```

### 文件：`src/evaluation/quintile_backtest.py`

**5分组等权回测**

每个调仓日，把所有股票按因子值从低到高分成5组（各约100只），每组内等权，看每组的平均收益。

预期结果（正向因子）：第5组 > 第4组 > ... > 第1组

注意：这是"不含成本、不扣基准"的原始评价，不代表可交易的超额收益。

### 脚本：`scripts/run_factor_evaluation.py`

对所有27个因子运行上述评价，输出：

```
reports/factor_evaluation/
  ic_result.csv          每个因子的IC均值/IC_IR/p值等
  shift_result.csv       每个因子的时间错位测试结果
  quintile_summary.csv   5分组回测摘要
  factor_correlation.csv 因子间相关矩阵
  factor_summary.csv     综合评价表，含是否入选最终组合
  final_factors.json     最终入选因子清单（含metadata）
  factor_evaluation_report.md  人类可读的评价报告
```

**最终入选6个因子：** value、quality、growth、momentum、volatility、liquidity

为什么只保留6个？
- 去掉IC_IR低于0.30的无效因子
- 去掉与其他因子相关性>0.70的冗余因子（保留IC_IR更高的那个）
- 目标：少而精，降低过拟合风险

---

## 第5步：合成信号

### 文件：`src/signal/combiner.py`

**把6个因子合并成1个综合打分**

两种合成方式：

```
方式1：等权（equal）
  composite = (f1 + f2 + f3 + f4 + f5 + f6) / 6
  简单，不需要历史数据，冷启动时使用

方式2：IC_IR加权（ic_ir，推荐）
  composite = (w1×f1 + w2×f2 + ... + w6×f6) / (|w1|+|w2|+...+|w6|)
  其中 wi = 过去24个月的IC_IR（负值的因子自动反向）
  
  例：如果momentum在过去24个月IC_IR = +0.5，value的IC_IR = +0.8，
  则value的权重更高（它最近表现更稳定）。
  
  如果vol_60d的IC_IR = -0.6（负向因子），
  w = -0.6，composite += (-0.6) × vol_60d_score，
  等效于把vol低的股票打高分（无需手工翻转）。
```

**窗口约束（关键）：**

计算T日权重时，只用 `before_date < T` 的IC历史，严格不含当期。

合成后再做一次MAD去极值 + Z-score标准化。

### 脚本：`scripts/run_signal_combination.py`

输出：

```
data/processed/
  composite_signal_ic_ir.parquet    IC_IR加权合成信号（主要用）
  composite_signal_equal.parquet    等权合成信号（对比用）
  icir_weight_history.parquet       每个调仓日各因子权重历史
  composite_signal_metadata.json    记录使用的因子、IC序列哈希等
```

---

## 第6步：组合优化

**核心问题：** 合成信号给出了每只股票的"分数"，但怎么把分数转成"应该持有多少百分比"？

不能简单地"打分前10%的股票各持2%"——这样会导致行业集中、个股集中等风险。

需要用数学优化：在满足各种约束的条件下，最大化预期收益。

### 文件：`src/portfolio/covariance.py`

**协方差矩阵估计**

协方差矩阵描述"哪两只股票一起涨跌"的程度，用于计算组合的风险（跟踪误差）。

问题：中证500有500只股票，需要估计 500×500 = 25万个参数，但只有有限的历史数据 → 样本协方差矩阵严重不稳定（"病态"）。

解决方案：**LedoitWolf收缩估计**
```
Σ_估计 = (1 - α) × Σ_样本 + α × Σ_目标
         |              |           |
         |              |           └─ 简单的对角矩阵（假设股票互不相关）
         |              └─────────── 原始样本协方差
         └───────────────────────── 收缩系数由数据自动确定
```
结果是一个介于"全相关"和"独立"之间的稳健矩阵，不容易过拟合噪声。

修复：每个协方差矩阵计算后都验证是否正定（最小特征值>0），不正定时加微小对角项修复，并记录在 `.meta.json` sidecar文件中。

### 文件：`src/portfolio/optimizer.py`

**组合优化器（三层Fallback）**

**目标函数：**
```
最大化：alpha^T × w        （让高分股票权重更大）
约束：
  (w - w_b)^T × Σ × (w - w_b) ≤ TE_target²/252   跟踪误差约束
  |Σ_行业(w_i - w_b_i)| ≤ 2%  ∀ 行业              行业偏离约束
  |w_i - w_b_i| ≤ 1%           ∀ 股票              单股偏离约束
  w_i ≥ 0                                          纯多头（不能做空）
  Σ w_i = 1                                        全仓（现金比例=0）
  停牌股：w_i = w_i_prev                           停牌锁定
  涨停股：w_i ≤ w_i_prev                           不可加仓
  跌停股：w_i ≥ w_i_prev                           不可减仓
```

**三层Fallback（L1→L2→L3）：**

```
L1：严格约束（用CLARABEL/SCS求解器）
  全部约束生效
  → 求解成功：使用最优权重，fallback_level=0

L2：去掉跟踪误差二次约束（问题变成线性规划）
  → 当L1无可行解或超时时触发，fallback_level=1

L3：TopN等权兜底
  → 当L2也失败时触发，fallback_level=2
  
  具体做法（本次修复后）：
  1. 停牌股锁定上期权重
  2. 跌停股锁定上期权重下限
  3. 排除涨停股
  4. 计算最少需要多少只股票才能满足单股偏离约束：
     n_min = ceil(free_budget / single_max_dev)
     例：free_budget=0.9，single_max_dev=0.01 → n_min=90只
  5. effective_topn = max(配置的topn=50, n_min=90) = 90
  6. 按综合信号排名选前90只，等权分配
```

**每个调仓日的Meta记录（`portfolio_weights_meta.parquet`）：**

```
fallback_level         0/1/2（用了哪层退路）
solver_status          "optimal"/"topn_fallback"等
cov_available          True/False（当期是否有协方差矩阵）
constraint_compliant   True/False（是否满足所有交易约束）
w_prev_source          "target_weight"（上期目标权重，而非实际成交权重）
```

### 脚本：`scripts/run_portfolio_optimization.py`

训练期+验证期全部84个月末调仓日，每个都跑一次优化。

输出结果（验证期）：
- L1（严格最优）：67/84期 = 80%
- L2（线性规划退路）：2/84期
- L3（等权兜底）：15/84期（其中3期因协方差缺失被迫L2/L3）

L3的15期中：有些是候选可买股票数量不足n_min（比如市场极端行情时大量涨停），属于真实无可行解，标记 `constraint_compliant=False` 并在报告中披露。

---

## 第7步：策略回测

### 文件：`src/backtest/transaction.py`

**交易成本计算**（非常精确，按实际规则实现）

```python
def compute_trade_cost(sell_value, buy_value, trade_date):
    # 印花税（只卖出方向收）
    if trade_date >= 2023-08-28:
        stamp_duty = sell_value × 0.0005   # 5bps
    else:
        stamp_duty = sell_value × 0.001    # 10bps
    
    # 佣金（买卖双向各收）
    commission = (sell_value + buy_value) × 0.00025  # 2.5bps
    
    # 滑点（买卖双向各计）
    slippage = (sell_value + buy_value) × 0.0008  # 8bps
    
    return stamp_duty + commission + slippage
```

### 文件：`src/backtest/engine.py`

**回测主循环** — 这是整个回测的核心

**完整的一个调仓周期是怎么执行的：**

```
T日（月末，比如2022-01-28）
  1. 从 portfolio_weights.parquet 读取 T 日的目标权重 w_target
  2. 记录当前持仓 w_current

T+1日（下一个交易日，比如2022-01-31）
  3. 从 stock_status 读取 T+1 日的停牌/涨跌停状态
     状态缺失的股票 → LOCKED（保守处理，修复后）
  4. 计算 pretrade_value = 当前持仓 × T+1 开盘价之和
     （注意：用T+1开盘价，不是T日收盘价，这是修复后的正确做法）
  5. 对每只股票决定是否成交：
     - LOCKED/停牌：保持现有持仓，目标权重被忽略
     - NO_BUY/涨停：w_actual_i = min(w_target_i, w_current_i)（不能加仓）
     - NO_SELL/跌停：w_actual_i = max(w_target_i, w_current_i)（不能减仓）
     - 价格缺失（停牌但未标LOCKED）：保持现有持仓
     - FREE：按目标权重成交
  6. 计算买入/卖出金额
  7. 计算交易成本（调用 transaction.py）
  8. 更新持仓 = 目标权重（除了上述受限股票）
  9. 从净值中扣除交易成本

T+1 ~ T'（持仓期，每个交易日）
  10. 用当日收盘价估值：NAV[d] = Σ 持仓[i] × close_adj[d, i]
      （期间不交易，NAV随市场涨跌波动）

T'日（下一个调仓日）
  → 重复上述流程
```

**n_no_price 统计（修复后）：**
只统计"当期持仓不为零或目标权重不为零"的股票中，因价格缺失无法成交的数量。不再把历史上曾经持仓过（但现在权重已为0）的股票算进去。

### 文件：`src/backtest/metrics.py`

**绩效指标计算**

```
从 NAV 序列计算：
  annualized_return     = (nav[-1] / nav[0]) ^ (252/n_days) - 1
  annualized_vol        = daily_return.std() × sqrt(252)
  sharpe_ratio          = (年化收益 - 无风险利率) / 年化波动率
  max_drawdown          = 最大峰谷回撤
  
  excess_return         = 策略年化收益 - 基准年化收益
  tracking_error        = (策略日收益 - 基准日收益).std() × sqrt(252)
  information_ratio     = excess_return / tracking_error
  excess_max_drawdown   = 超额NAV的最大峰谷回撤
  monthly_win_rate      = 月度超额为正的比例
```

### 脚本：`scripts/run_backtest.py`

输出（验证期2022年）：

```
data/processed/
  backtest_nav.parquet          日频NAV（策略/基准/超额）
  backtest_metrics.parquet      绩效指标汇总
  backtest_trades_v1.parquet    V1 Baseline的调仓记录
  backtest_trades_v2.parquet    V2 优化权重的调仓记录
  backtest_weights_v1.parquet   V1日频实际权重
  backtest_weights_v2.parquet   V2日频实际权重
```

---

## 第8步：业绩归因

**目的：** 搞清楚超额收益（或亏损）从哪里来——是因为行业配置对了还是个股选得好？

### 文件：`src/attribution/brinson.py`

**BHB（Brinson-Hood-Beebower）行业归因**

把超额收益分解成三部分：

```
配置效应 = (策略行业权重 - 基准行业权重) × (基准行业收益 - 基准总收益)
          "你多配了消费行业，消费行业涨得比平均好，+超额"

选股效应 = 基准行业权重 × (策略行业内收益 - 基准行业内收益)
          "在消费行业里，你选的股票比消费行业平均涨得好，+超额"

交叉效应 = (策略权重 - 基准权重) × (策略行业内收益 - 基准行业内收益)
          "既多配了消费行业，又选了好股，额外贡献"

总超额 = 配置效应 + 选股效应 + 交叉效应
```

时间对齐（F8-001修复后）：
- 策略权重用T+1日执行后的实际权重（不是T日盘后的目标权重）
- 期间收益从T+1开盘到T'+1开盘（与回测对齐）
- 保留现金仓位（不强制归一化到1，这样成本拖累体现在现金项中）

### 文件：`src/attribution/factor_attr.py`

**因子收益归因**

把策略收益用6个因子来"解释"：

```
对每个调仓期内的每个股票，做WLS回归（以流通市值为权重）：
  stock_return = β_value × value_score + β_quality × quality_score + ... + ε

β_value 就是"价值因子在这一期贡献了多少收益"
ε（残差）是模型无法解释的部分（选股残差）
```

从 `final_factors.json` 读取6个最终因子，从 `factor_summary.csv` 读取各因子预期方向（正向/负向）。

### 脚本：`scripts/run_attribution.py`

同时运行Brinson行业归因 + 因子归因，还做一个"reconciliation"验证：

```
reconciliation检验：
  归因加总的月度超额收益 ≈ 回测NAV的月度超额收益
  允许偏差 ≈ 0.4%（来自T+1开盘/收盘价差异和L3权重近似）
```

### 脚本：`scripts/generate_backtest_report.py`

自动读取 `backtest_metrics.parquet` 生成 `reports/analysis_v2_results.md`，不再手动填写指标。

---

## 验证期（2022年）结果解读

当前已完成的验证期回测结果：

| 指标 | V1 Baseline | V2 优化权重 | 含义 |
|---|---|---|---|
| 年化绝对收益 | -18.25% | -20.35% | 2022全年熊市，跌的 |
| 基准收益（中证500）| -19.55% | -19.55% | 基准也跌 |
| **年化超额收益** | **+1.30%** | **-0.80%** | 策略比基准多赚/少赚 |
| **信息比率(IR)** | **0.177** | **-0.178** | 超额/跟踪误差之比 |
| 超额最大回撤 | -11.36% | -7.30% | 超额NAV的最大回撤 |
| 月度胜率 | 45.5% | 54.5% | 跑赢基准的月份比例 |
| 年化换手率 | ~895% | ~861% | 年化双边换手（正常范围500-1500%）|

**如何解读：**

- V1（等权合成信号）IR=0.177，是正的但不显著（目标是≥0.5）
- V2（IC_IR加权）IR=-0.178，是负的——在2022年，因子选股反而帮了倒忙
- 2022年是特殊年份：A股全面下跌、价值/动量因子集体失效、市场逻辑被疫情政策主导
- **这不等于策略失效，需要看2016-2022全样本的平均IR才能下结论**

---

## 测试集还剩什么要做

现有状态（2026-05-20）：

```
Run #1（已作废）：2023-05-12 运行，使用了错误的收益率口径（F2-001），产物已丢失
剩余有效次数：2次
```

运行测试集前的检查清单：

```
✓ 因子面板最新期需 ≥ 2025-12-30（需重建测试期因子）
✓ 所有A-F阶段Blocker已关闭（当前已满足）
✓ test_set_run_log.md填写预登记（日期/commit/目的）
✓ data/processed/test_run_2/ 不存在
```

运行命令：
```powershell
python -m scripts.run_test_pipeline --run-id 2
```

---

## 附录A：目录结构完整说明

```
e:/Acoding/Project/500/
│
├── src/                          核心业务代码（不可直接运行）
│   ├── config.py                 全局参数中心
│   ├── data/
│   │   ├── loader.py             Parquet统一读取接口
│   │   ├── pit_loader.py         PIT安全财务数据加载
│   │   └── universe.py           投资域和TradeState枚举
│   ├── factors/
│   │   ├── financial_factors.py  15个财务因子
│   │   ├── price_factors.py      12个量价因子
│   │   └── preprocess.py         5步横截面预处理
│   ├── evaluation/
│   │   ├── ic_analysis.py        IC检验框架
│   │   ├── quintile_backtest.py  5分组回测
│   │   └── shift_test.py         时间错位测试
│   ├── signal/
│   │   └── combiner.py           多因子合成
│   ├── portfolio/
│   │   ├── covariance.py         LedoitWolf协方差估计
│   │   └── optimizer.py          三层Fallback优化器
│   ├── backtest/
│   │   ├── engine.py             回测主循环（T+1开盘成交）
│   │   ├── transaction.py        交易成本（含印花税切换）
│   │   └── metrics.py            绩效指标
│   └── attribution/
│       ├── brinson.py            BHB行业归因
│       └── factor_attr.py        WLS因子归因
│
├── scripts/                      可执行脚本（按顺序运行）
│   ├── download_tushare.py       第1步：下载核心数据
│   ├── download_supplement.py    第1步：下载补充数据
│   ├── csv_to_parquet.py         第2步：格式转换+清洗
│   ├── build_factor_panels.py    第3步：构建因子面板
│   ├── run_factor_evaluation.py  第4步：因子评价
│   ├── run_signal_combination.py 第5步：合成信号
│   ├── run_portfolio_optimization.py 第6步：组合优化
│   ├── run_backtest.py           第7步：策略回测
│   ├── run_attribution.py        第8步：业绩归因
│   ├── generate_backtest_report.py 自动生成报告
│   ├── run_pipeline.py           一键运行全流程（调用上述脚本）
│   └── run_test_pipeline.py      测试期专用（隔离产物）
│
├── tests/                        自动化测试（201个）
│   ├── test_pit.py               PIT日期正确性验证
│   ├── test_transaction.py       交易成本计算
│   ├── test_universe.py          投资域过滤
│   ├── test_preprocess.py        因子预处理
│   ├── test_factor_time_boundary.py 防未来函数
│   ├── test_evaluation.py        IC分析
│   ├── test_signal_combiner.py   信号合成
│   ├── test_optimizer.py         组合优化器
│   ├── test_backtest_engine.py   回测引擎
│   ├── test_backtest_metrics.py  绩效指标
│   ├── test_attribution.py       归因分析
│   ├── test_pipeline_release_gates.py 发布门禁
│   └── test_test_pipeline_isolation.py 测试集隔离
│
├── data/
│   ├── raw/                      原始CSV（不入git）
│   └── processed/                处理后Parquet（不入git）
│       ├── daily_quote.parquet
│       ├── financial_pit.parquet
│       ├── factor_panels/
│       ├── composite_signal_*.parquet
│       ├── portfolio_weights_*.parquet
│       ├── backtest_*.parquet
│       ├── run_manifests/        运行追溯记录
│       └── test_run_{N}/         测试集专用隔离目录
│
├── reports/
│   ├── factor_evaluation/        因子评价报告（CSV+MD+JSON）
│   └── analysis_v2_results.md    验证期回测报告（自动生成）
│
├── check/                        人工审查记录（全部入git）
│   ├── test_set_run_log.md       测试集运行日志（有效次数：2次）
│   ├── 14_incomplete_fix_register.md 未闭环问题登记
│   └── 20~22_*.md               各阶段核验报告
│
├── teach/                        本教学文档目录
├── notebooks/                    探索分析用Jupyter笔记本
└── src/config.py                 全局参数（所有常量集中在这里）
```

---

## 附录B：数据在各步骤之间是怎么流动的

```
download_tushare.py
    ↓ 写
data/raw/daily_*.csv, financial_*.csv, industry_*.csv
    ↓ 读
csv_to_parquet.py
    ↓ 写
data/processed/daily_quote.parquet  ← open_adj, close_adj, ret（后复权）
data/processed/financial_pit.parquet ← PIT安全财务数据
data/processed/stock_status.parquet  ← 停牌/涨跌停状态
data/processed/industry.parquet      ← 行业归属
data/processed/index_member.parquet  ← 成分股权重
    ↓ 读
build_factor_panels.py
    ↓ 写
data/processed/factor_panels/factor_panel_train.parquet
data/processed/factor_panels/factor_panel_valid.parquet
    ↓ 读
run_factor_evaluation.py
    ↓ 写
reports/factor_evaluation/final_factors.json  ← 6个入选因子
reports/factor_evaluation/ic_result.csv       ← IC统计量
data/processed/fwd_ret_panel.parquet          ← 月度前向收益
    ↓ 读
run_signal_combination.py
    ↓ 写
data/processed/composite_signal_ic_ir.parquet ← 综合打分
data/processed/icir_weight_history.parquet    ← 因子权重历史
    ↓ 读
run_portfolio_optimization.py
    ↓ 也读 data/processed/cov_cache/*.parquet（协方差矩阵）
    ↓ 写
data/processed/portfolio_weights_optimized.parquet ← V2权重
data/processed/portfolio_weights_baseline.parquet  ← V1等权
data/processed/portfolio_weights_meta.parquet      ← fallback/合规记录
    ↓ 读
run_backtest.py
    ↓ 也读 data/processed/daily_quote.parquet（成交价格）
    ↓ 也读 data/processed/stock_status.parquet（交易约束）
    ↓ 写
data/processed/backtest_nav.parquet    ← 日频NAV
data/processed/backtest_metrics.parquet ← 绩效指标
data/processed/backtest_trades_*.parquet ← 调仓记录
    ↓ 读
run_attribution.py
    ↓ 写
data/processed/brinson_attribution.parquet  ← 行业归因
data/processed/factor_attribution.parquet   ← 因子归因
reports/analysis_v2_results.md             ← 最终报告（自动生成）
```
