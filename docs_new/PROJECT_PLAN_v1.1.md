# 中证500多因子指数增强项目蓝图 v1.1（含当前状态校正）

> **项目性质**：量化金融实验课作业 / 个人学习项目
> **周期**：约 2 个月（9 周缓冲版）
> **核心目标**：完整走通"数据 → 因子 → 信号 → 组合 → 风控 → 回测 → 归因"全流程，产出一个可复现、可审计的指数增强研究框架
> **版本历史**：v1.0 初版 → v1.1 自检修订 → 2026-05-20 当前状态校正（同步流水线、测试集纪律和实际产物）→ 2026-05-21 训练数据扩展决策（见第零节 0.4）

---

## 零、当前状态校正（2026-05-20）

本文件最初是项目蓝图，后续章节中的分周计划和部分复选框保留了历史计划语境。当前执行和审计应优先采用本节、`docs/check/test_set_run_log.md`、`docs/check/test_set_runs.json`、`docs/check/23_pre_run_readiness_verification.md`、`docs/check/24_training_validation_pipeline_refresh.md` 的状态记录。

### 0.1 当前已跑通的非测试集流水线

已在不触碰正式测试集的前提下，用当前代码刷新训练/验证公共产物：

```text
python -m scripts.run_pipeline --from-stage convert --skip quality
```

执行成功，最新 manifest：

```text
data/processed/run_manifests/run_20260520_212950.json
success=True
stages_executed=['convert', 'factors', 'evaluate', 'signal', 'portfolio', 'backtest', 'attribution']
train_end=2021-12-31
valid_end=2022-12-31
test_set_run_count=0
```

边界说明：本次从 `convert` 阶段开始，未重新联网下载 Tushare raw 缓存；已覆盖的是由现有 raw 缓存派生的训练/验证公共产物。`quality` 阶段仍是占位阶段，本次通过 `--skip quality` 显式跳过。

### 0.2 当前测试集纪律

测试集为 2023-2025。历史上已有一次提交：

```text
80708d4 [TEST_SET_RUN_1] 测试集评估完成 (2023-2025)
```

历史 `80708d4 [TEST_SET_RUN_1]` 已确认是错误记载。当前测试集实际未使用，已用 **0 次**，剩余 **2 次**。

正式 Run #1 前必须先在同一 run-id 纪律下生成测试期因子面板：

```text
python -m scripts.build_factor_panels --allow-test-set --run-id 1 --end-date 2025-12-31 --resume
```

随后才能运行：

```text
python -m scripts.run_test_pipeline --run-id 1 --factor-panel-dir data/processed/factor_panels_test_run_1
```

当前尚未运行 `scripts/run_test_pipeline.py`，尚未生成 `data/processed/test_run_*` 或 `data/processed/factor_panels_test_run_*`。

### 0.3 当前训练/验证产物状态

```text
factor_panels:                 27 个因子，2016-01-29 -> 2022-12-30，shape=(84, 1065)
composite_signal_ic_ir:         2016-01-29 -> 2022-12-30，shape=(84, 1065)
portfolio_weights_optimized:    2016-01-29 -> 2022-12-30，shape=(84, 1069)
backtest_nav:                   2022-01-04 -> 2022-12-30，shape=(242, 3)
pytest:                         201 passed, 2 warnings
```

最终入模因子为 6 个：

```text
amihud, rev_yoy, ep_ttm, cfp, gross_margin, mom_12_1
```

组合优化训练/验证期 fallback 分布：

```text
L1=67, L2=2, L3=15
constraint_compliant=False: 15/84 期
```

当前验证期 V2 结果不达成 IR 目标，但不代表程序失败：

```text
annualized excess return = -0.86%
information ratio        = -0.191
excess max drawdown      = -7.32%
annual turnover          = 863%
```

### 0.4 仍需披露的非阻塞问题

1. `quality` 阶段尚未实现，训练/验证流水线依赖 `--skip quality` 显式跳过。
2. 组合优化有 15/84 期 L3 fallback 权重不完全满足单股偏离约束；回测通过 `--allow-noncompliant-weights` 明示接受，报告必须披露。
3. Brinson 归因存在 V2 reconciliation warning，最大偏差约 `0.4247%`；因子归因会计恒等式通过。
4. 当前 raw 目录仍使用旧行业文件 `industry_citics.csv` 作为兼容输入；转换脚本已优先支持 `industry_sw2021.csv`，若从空 raw 重新下载，建议生成规范文件后再全量转换。
5. `pytest` 仍有 `.pytest_cache` 的 `WinError 5` 权限 warning，不影响核心测试通过，但不应写成已完全清理。

---

## 一、项目核心约定

### 1.1 完成标准

- **正式测试集（2023-2025）目标**：相对中证500全收益指数有正的年化超额收益（这是目标，不是当前已验证结论）
- **信息比率（IR）≥ 0.5**（完成线）；**≥ 0.7** 为优秀。当前验证期 V2 IR 为 -0.191，尚未达标
- **超额最大回撤 ≤ 10%**
- **年化双边换手 5-15 倍**（月频）
- **跟踪误差** 4-8%（事后测算，作为结果而非约束目标）
- **完整可复现**：训练/验证链路当前可由 `scripts.run_pipeline --from-stage convert --skip quality` 重跑；raw 重新下载和 `quality` 阶段仍需单独披露

### 1.2 边界声明

- 不追求工业级 alpha
- 不做高频策略、不做日内交易
- 不引入另类数据（仅用 Tushare Pro 公开数据）
- 不做实盘对接、不做模拟盘交易
- 不做 Streamlit 等交互式 Dashboard；当前交付以脚本生成的 Markdown 报告、审查记录和必要 notebook 为主

---

## 二、关键决策记录

| 决策项 | 方案 |
|---|---|
| 数据源 | Tushare Pro（行情、财务三表、行业分类、指数成分股）|
| 数据中转格式 | Tushare CSV/raw 缓存 → Parquet 工作表；编码以下载脚本和原始文件为准 |
| 时间区间 | 2016-01-01 ~ 2025-12-31 |
| 训练集 | 2016-2021（6 年） |
| 验证集 | 2022（1 年，含熊市压力测试） |
| 测试集 | 2023-2025（3 年）：尚未有效运行；当前剩余 Run #1 / Run #2 两次正式机会 |
| 调仓频率 | 当前主流程为月频；周频仅为可选扩展，未作为当前主交付 |
| 基准指数 | **中证500全收益指数（含分红再投资）** |
| 价格口径 | **全程使用后复权价 + 后复权计算的收益率** |
| 成交假设 | **T+1 开盘价 + 5-10 bps 滑点** |
| 印花税 | **按日期切换：2023-08-28 之前 10 bps，之后 5 bps（卖出单边）** |
| 佣金 | 双边各 2.5 bps |
| 中性化基准 | 行业（申万一级 SW2021，31个行业）+ log(自由流通市值) |
| 跟踪误差控制 | **L1：Ledoit-Wolf 协方差 + cvxpy 二次约束；L2：线性偏离约束；L3：TopN 等权**。当前训练/验证期 L1=67、L2=2、L3=15 |
| 代码组织 | `src/` 模块 + `scripts/` 流水线；Notebook 主要用于探索和展示 |
| 可视化 | Jupyter + matplotlib/plotly；当前主报告由脚本读取 parquet 自动生成 |
| 回测引擎 | 自研轻量回测（pandas + numpy + cvxpy） |
| 投资域 | **池内策略**：调仓日的可投资股票 = 当日实际属于中证500成分股的股票 |

---

## 三、技术栈

### 3.1 核心依赖

```
python >= 3.10
pandas >= 2.0
numpy
pyarrow              # Parquet 读写
scipy                # 统计检验
statsmodels          # 因子回归、中性化
scikit-learn         # 部分预处理 / 因子合成
cvxpy >= 1.4         # 组合优化
tushare              # Tushare Pro API
matplotlib, plotly   # 可视化
jupyterlab           # 探索环境
tqdm                 # 进度条
joblib               # 并行计算（可选）
nbformat             # notebook 程序化构建
pytest               # 单元测试
```

可选求解器（cvxpy 后端）：CLARABEL（推荐）、ECOS、OSQP、SCS

### 3.2 不使用的库（及原因）

- **backtrader / zipline**：事件驱动框架，不适合截面策略
- **vectorbt**：对组合优化和约束的支持不如自研
- **天勤 / 米筐 / 聚宽 SDK**：与 Tushare Pro 格式不兼容，封装过深

---

## 四、目录结构

```
E:/Acoding/Project/500/
├── AGENTS.md / CLAUDE.md
├── PROJECT_PLAN_v1.1.md
├── README.md
├── requirements.txt
│
├── data/
│   ├── raw/                         # Tushare Pro 原始缓存，不入库
│   ├── processed/                   # 当前训练/验证公共 parquet 产物，不入库
│   │   ├── daily_quote.parquet
│   │   ├── daily_basic.parquet
│   │   ├── index_quote.parquet      # H00905.CSI 全收益基准
│   │   ├── index_member.parquet
│   │   ├── stock_status.parquet
│   │   ├── industry.parquet         # SW2021 一级行业
│   │   ├── financial_pit.parquet / indicator_pit.parquet / dividend_pit.parquet
│   │   ├── factor_panels/           # 训练/验证因子面板，当前到 2022-12-30
│   │   ├── cov_cache/
│   │   ├── run_manifests/
│   │   ├── factor_panels_test_run_N/ # 正式测试集扩展面板，独立目录
│   │   └── test_run_N/              # 正式测试集产物，独立目录
│   └── csi500_index_weight_201601_202512.meta.md
│
├── src/
│   ├── config.py
│   ├── data/                        # loader / pit_loader / universe
│   ├── evaluation/                  # IC、分组回测、shift test
│   ├── factors/                     # financial_factors / price_factors / preprocess
│   ├── signal/                      # combiner
│   ├── portfolio/                   # covariance / optimizer
│   ├── backtest/                    # engine / transaction / metrics
│   └── attribution/                 # brinson / factor_attr
│
├── scripts/
│   ├── download_tushare.py
│   ├── download_supplement.py
│   ├── csv_to_parquet.py
│   ├── build_factor_panels.py
│   ├── run_factor_evaluation.py
│   ├── run_signal_combination.py
│   ├── run_portfolio_optimization.py
│   ├── run_backtest.py
│   ├── run_attribution.py
│   ├── generate_backtest_report.py
│   ├── run_pipeline.py              # 训练/验证流水线
│   └── run_test_pipeline.py         # 正式测试集流水线，受 run-id 限制
│
├── notebooks/
│   ├── 01_processed_data_quality_report.ipynb
│   ├── 02_factor_evaluation.ipynb
│   ├── 03_factor_combination.ipynb
│   ├── 04_portfolio_optimization.ipynb
│   ├── 05_backtest.ipynb
│   └── 06_attribution.ipynb
│
├── tests/
│   ├── test_universe.py
│   ├── test_pit.py
│   ├── test_preprocess.py
│   ├── test_factor_time_boundary.py
│   ├── test_evaluation.py
│   ├── test_signal_combiner.py
│   ├── test_optimizer.py
│   ├── test_transaction.py
│   ├── test_backtest_engine.py
│   ├── test_backtest_metrics.py
│   ├── test_attribution.py
│   ├── test_pipeline_release_gates.py
│   └── test_test_pipeline_isolation.py
│
├── reports/
│   ├── analysis_v2_results.md
│   ├── factor_evaluation/
│   └── factor_combination/
│
└── check/                           # 审查、修复记录、测试集纪律和刷新记录
```

---

## 五、分周计划（历史蓝图，当前状态见第零节）

以下内容保留为方法论和阶段拆解，不再作为当前完成状态的唯一事实源。当前可运行状态、测试集次数和最新产物以第零节及 `check/` 下的复核记录为准。

### 第 1-2 周：数据基础设施

**目标**：拿到干净、对齐、PIT 严格的数据。**这是整个项目最重要的两周**。

#### 第 1 周：行情、成分股、行业

- [x] 从 Tushare Pro 下载（CSV → Parquet）：
  - 股票日行情：`daily`（含复权因子 `adj_factor`）
  - 中证500历史成分股及权重：`index_weight`（000905.SH）
  - 中证500全收益指数日频：`index_daily`（**H00905.CSI，全收益口径**）
  - 申万一级行业分类（SW2021，31个行业）：`index_classify`
  - 退市/ST 标记：`namechange`；停牌：`suspend_d`；每日基本面：`daily_basic`
  - 自由流通股本：`daily_basic.float_share`（注意与 `circ_share` 的区别）
- [x] 编写 `csv_to_parquet.py`：批量转换 + 类型规范
- [x] 编写 `src/data/universe.py`：构造调仓日可投资域和交易状态
- [x] 停牌、涨跌停、ST 标记已进入 `stock_status.parquet` 和回测/优化逻辑
- [ ] 自动化 `quality` 阶段尚未实现；当前只有数据质量 notebook/脚本辅助检查

#### 第 2 周：财务 PIT、协方差准备数据

- [x] 下载并转换财务三表、财务指标、股东人数、分红等 PIT 数据
- [ ] 分析师预期数据未进入当前主流程
- [x] **PIT 转换实现**：
  - Tushare 字段映射：`ann_date`（公告日，等价于 CSMAR `Annodt`）、`f_ann_date`（首次公告日）、`end_date`（报告期，等价于 CSMAR `Accper`）
  - 规则：在 T 日只能使用 `ann_date <= T` 的最新数据（以 `ann_date` 为可用日，不用 `end_date`）
  - 同一报告期多次修正：保留最新版本但记录修正历史
- [x] **PIT 验证测试 `tests/test_pit.py`**
- [x] 准备协方差估计需要的日频后复权收益率序列

**交付物**：清洗后的所有 Parquet 文件 + `notebooks/01_data_exploration.ipynb`

**风险点（v1.1 强调）**：

- ⚠️ **基准必须用全收益指数**（含分红再投资），用价格指数会导致 2-3% 的虚假超额
- ⚠️ **股票收益必须用后复权价计算**，全程统一口径
- ⚠️ **Tushare 财务数据默认非 PIT**，必须用 `ann_date`（公告日）而非 `end_date`（报告期）作为可用日期
- ⚠️ **成分股快照对齐到生效日**，不是公告日；调仓日的投资域 = 调仓日实际生效的成分股
- ⚠️ 股票代码使用 Tushare `ts_code` 格式（含交易所后缀，如 `000001.SZ`），禁止仅用 6 位裸代码
- ⚠️ 自由流通市值 ≠ 流通市值，确认下载的是哪个

---

### 第 3-4 周：因子构建与单因子检验

**目标**：构建 20-30 个候选因子，每个完成单因子检验

#### 因子库（精选，不求多）

| 大类 | 当前实现因子 |
|---|---|
| 价值 | `ep_ttm`, `bp`, `sp_ttm`, `cfp`, `fcfp` |
| 质量 | `roe`, `roa`, `gross_margin`, `asset_turn`, `leverage`, `accrual` |
| 成长 | `np_yoy`, `rev_yoy`, `roe_delta`, `q_roe` |
| 动量/反转 | `ret_1m`, `mom_6_1`, `mom_12_1`, `holder_chg` |
| 波动率 | `vol_60d`, `ivol_60d`, `max_ret` |
| 流动性 | `turn_20d`, `amihud` |
| 资金流向 | `margin_ratio`, `short_ratio`, `large_net_inflow` |

当前共 27 个候选因子。最新训练集筛选后进入合成的 6 个因子为：`amihud`, `rev_yoy`, `ep_ttm`, `cfp`, `gross_margin`, `mom_12_1`。分析师预期因子未进入当前主流程。

#### 因子预处理流水线（v1.1 严格化）

每个因子在每个调仓日 T 上独立处理：

1. **行业适用性过滤**：金融股（银行/保险/券商）的财务因子单独处理或剔除
2. **去极值**：MAD 法或分位数截断（1%/99%）
   - **关键**：边界**仅用 T 日横截面**计算，不能用全样本
3. **标准化**：z-score（横截面 mean=0, std=1）
4. **行业 + 市值中性化**：
   - 横截面回归：`factor ~ industry_dummies + log(free_float_mcap)`
   - 取残差作为最终因子值

#### 单因子检验

- [x] **月度 Rank IC**：每月调仓日因子 vs 下期收益（与月频回测一致）
- [x] IC_IR = mean(IC) / std(IC)
- [x] t 统计量、p 值、BH 多重检验校正
- [x] 分组回测（5 组）和方向化多空指标
- [x] 换手率、稳定性、验证期对比、shift test
- [ ] 日度 IC 和深度 IC 衰减不是当前主流程产物；如后续研究需要再扩展

**交付物**：因子库 + 每个因子一份测试报告

**自检清单（关键）**：

- [ ] T 日因子只用了 ≤ T 日的数据（**做"时间错位测试"**：把因子整体后移一期，IC 应剧烈下降；如果几乎不变，说明有未来函数）
- [ ] 未来收益是 T+1 → T+N（不含 T 日盘前信息）
- [ ] 涨停/跌停/停牌的股票被排除或标记
- [ ] 新股（上市 < 6 个月）被排除
- [ ] 中性化的市值是 PIT 的 log(自由流通市值)
- [ ] **去极值边界仅用 T 日横截面计算**（v1.1 新增）
- [ ] 财务因子的可用日期用 `Annodt`

---

### 第 5 周：因子合成与协方差估计

**目标**：合成 alpha 信号 + 准备协方差矩阵

#### 5.1 因子合成

- [x] 因子相关性分析（剔除相关性 > 0.7 的冗余因子）
- [x] 因子稳定性筛选（IC 在子区间内是否一致）
- [x] 合成方法对比：
  - **等权**（baseline）
  - **IC 加权**（按历史 IC 均值，IC 计算窗口必须是 T 日之前的）
  - **IC_IR 加权**（推荐）
  - **滚动回归**（容易过拟合，谨慎使用）
- [x] 在**训练集（2016-2021）**确定方法
- [x] 在**验证集（2022）**确认稳健性
- [x] **测试集暂不使用**：正式测试集信号只允许由 `run_test_pipeline.py --run-id N` 在独立目录生成

#### 5.2 协方差矩阵估计（v1.1 新增）

为支持严格的跟踪误差约束，需要估算 500 只股票的协方差矩阵：

- [x] 数据准备：调仓日 T 之前 N 天的日频收益率（当前要求至少 60 个有效日；主路径使用 Ledoit-Wolf）
- [x] **样本协方差不可直接用**：500 维 × 252 样本的协方差矩阵严重病态
- [x] 推荐方法（按复杂度递增）：
  1. **Ledoit-Wolf 收缩估计**（首选）：sklearn 的 `LedoitWolf` 一行代码搞定，对作业级项目最实用
  2. **EWMA 协方差**：考虑时变性，但实现略复杂
  3. **因子模型协方差**（Barra-style）：用风格因子做降维，最理想但工作量大
- [x] 输出：每个调仓日 T 对应一个协方差矩阵缓存；训练/验证期当前 81 期可用，前 3 期因历史样本不足降级
- [x] **数值检查**：每个 Σ 必须是正定矩阵；不正定时加微小对角项 `Σ + ε·I` 修复并写 sidecar metadata

**风险提示**：
- 协方差估计本身就是研究课题，对作业级项目用 Ledoit-Wolf 即可
- 如果 cvxpy 因为协方差矩阵问题不收敛，**立刻退化为简化路线**（线性权重偏离约束 + 行业中性约束），不在这一步死磕

**交付物**：合成因子 + 协方差估计模块 + `notebooks/04_factor_combination.ipynb`、`05_covariance_estimation.ipynb`

---

### 第 6 周：组合优化与风控

**目标**：从 alpha 信号到可交易的组合权重

#### 优化目标（严格路线）

```
max  α^T · w
s.t. (w - w_b)^T · Σ · (w - w_b) ≤ TE_target^2 / 252   # 跟踪误差约束（日频方差形式）
     行业偏离: |sum(w_industry_i) - sum(w_b_industry_i)| ≤ 2%   ∀ i
     单股偏离: |w_i - w_b_i| ≤ 1%   ∀ i
     w_i ≥ 0   ∀ i   （只做多）
     sum(w) = 1
     # 可选风格中性约束（市值、波动率等）
```

其中：
- α 是合成因子（横截面 z-score）
- w_b 是基准（中证500）的权重
- Σ 是协方差矩阵（来自第 5 周）
- TE_target = 5%（年化）

#### 实现细节

- [x] 用 cvxpy 实现优化器，求解器优先 CLARABEL，当前 fallback 求解器为 SCS
- [x] **Fallback 机制**：
  - 优化器失败时退化为简化版（去掉协方差约束，只保留线性约束）
  - 简化版仍失败时退化为 TopN 等权（取因子前 50 只等权）
- [x] 处理边界情况：
  - 停牌：当期权重锁定为上期值（不参与优化）
  - 涨停：单边约束 `w_i ≤ w_i_prev`（不能买入）
  - 跌停：单边约束 `w_i ≥ w_i_prev`（不能卖出）
  - 一字板（开盘即涨跌停）：完全无法成交，调仓延后到下一交易日
- [x] 调仓日期生成（当前主流程月频）
- [x] **Baseline 版本**：直接 TopN 等权（用于对比）

当前训练/验证期优化结果：L1=67、L2=2、L3=15；其中 15/84 期 `constraint_compliant=False`，属于必须披露的约束风险，不能写成完全合规组合。

#### 性能预估

- 月频 × 10 年 ≈ 120 次优化
- 单次优化 1-5 秒（500 维带二次约束）
- 总时长 2-10 分钟，可接受
- 周频会变成 500 次优化，10-50 分钟，可接受但略慢

**交付物**：组合权重序列（baseline + 优化版）+ 优化器测试报告

**风险点**：
- ⚠️ 协方差矩阵病态导致优化失败 → Fallback 到简化版
- ⚠️ 约束过严导致无解 → 放松行业/单股偏离约束
- ⚠️ 求解器选择影响数值稳定性

---

### 第 7 周：回测引擎与交易成本

**目标**：在权重序列上跑出真实可信的 PnL 曲线

#### 回测主循环（伪代码）

```python
for t in trading_days:
    if t in rebalance_dates:
        # T 日盘后：基于 ≤ T 日的所有信息计算因子和目标权重
        alpha = compute_factor(data_up_to=t)
        target_w = optimize(alpha, sigma=cov[t], w_prev=current_w)

    if t-1 in rebalance_dates:
        # T+1 日开盘：执行交易
        execute_at_open(target_w, current_w, slippage=8bps)

    # 每日按后复权收益率更新 PnL
    daily_return = sum(current_w * stock_return[t])
    nav[t] = nav[t-1] * (1 + daily_return)
```

#### 交易成本模型（v1.1 修订）

- 佣金：双边各 2.5 bps
- **印花税：按日期切换**
  - 2023-08-28 前：卖出 10 bps
  - 2023-08-28 后：卖出 5 bps
- 滑点 + 冲击：当前配置为买卖双边各 8 bps（`cfg.SLIPPAGE_RATE = 0.0008`）
- **当前配置下完整买卖双边约 26-31 bps**（佣金 5 bps + 滑点 16 bps + 卖出印花税 5/10 bps；具体取决于交易日期和买卖金额）

#### 业绩指标

- 年化收益、年化波动、Sharpe（无风险利率取 2%）
- **超额收益、跟踪误差（事后实测）、信息比率（IR）**
- 最大回撤、超额最大回撤
- 月度胜率（月度战胜基准的比例）
- 换手率、持仓集中度
- Calmar 比率

#### 多版本对比

| 版本 | 描述 | 用途 |
|---|---|---|
| V0 | 中证500全收益基准 | 对照 |
| V1 | Baseline TopN 等权 | 当前已输出 |
| V2 | 优化权重（内部按期走 L1/L2/L3 fallback） | 当前主交付版本 |
| 成本敏感性 | 不同交易成本假设 | 可选扩展，当前非主流水线产物 |

**交付物**：完整回测结果 + 业绩报告

**自检清单**：
- [x] 权重在 T 日盘后基于 ≤ T 日信息计算
- [x] 实际成交在 T+1 开盘
- [x] 交易成本扣除：佣金双边各 2.5 bps，滑点双边各 8 bps，印花税卖出单边按日期切换
- [x] 涨跌停/停牌/一字板限制生效
- [x] 印花税按日期切换
- [x] 用全收益基准对比

---

### 第 8 周：归因分析

**目标**：解释"为什么这个策略赚钱（或亏钱）"

#### 8.1 测试集最终运行纪律

- [ ] **测试集（2023-2025）尚未有效运行**；当前剩余 Run #1 / Run #2 两次正式机会
- [ ] Run #1 前先生成 `data/processed/factor_panels_test_run_1/`，且该步骤纳入同一 run-id 纪律
- [ ] Run #1 使用 `python -m scripts.run_test_pipeline --run-id 1 --factor-panel-dir data/processed/factor_panels_test_run_1`
- [ ] 运行完成后必须立即提交，commit message 含 `[TEST_SET_RUN_2]`
- [ ] **重要事件标注**：
  - 2023-08：印花税下调
  - 2023-04：注册制全面落地
  - 2024-初：小微盘股流动性危机
- [ ] 如果测试集结果远超预期，**警惕未来函数**
- [ ] 如果测试集结果远低于预期，**这是正常现象**（参见风险登记表 #11）

#### 8.2 归因分析

- [ ] **Brinson 行业归因**：超额拆解为行业配置 + 个股选择
- [ ] **因子归因**：用风格因子拆解超额来源
- [ ] **金融股贡献单独看一眼**：如果持续负贡献，记录在报告
- [ ] **分时段归因**：2023 / 2024 / 2025 分别看
- [ ] 持仓换手分析：是哪些股票贡献了超额

**交付物**：归因报告 + `notebooks/08_attribution.ipynb`

当前状态：验证期 Brinson 归因、因子归因、reconciliation 表和 `attribution_metadata.json` 已生成；正式测试集 2023-2025 的归因要等 Run #1 完成后才能生成。

---

### 第 9 周：报告与收尾

**目标**：产出最终交付物

- [ ] 撰写最终报告。当前验证期报告为 `reports/analysis_v2_results.md`，正式测试集后再决定是否沉淀为 `reports/final_report.md`：
  - 项目背景与目标
  - 数据与方法（含关键决策、PIT 处理、口径说明）
  - 因子库说明
  - 主要结果（含图表）
  - 归因分析
  - **局限性与改进方向**（这一节非常重要，体现批判性思维）
  - **2024-2025 期间市场结构变化的影响讨论**
- [ ] `notebooks/09_final_report.ipynb`：核心结果可视化
  - 净值曲线（策略 vs 基准）
  - 超额曲线
  - 滚动 IR、滚动跟踪误差
  - 关键指标表（按年度）
  - 持仓行业分布变化
  - 归因瀑布图
- [ ] 代码清理、注释补充
- [ ] README.md 编写：项目说明、复现步骤
- [ ] **整理项目仓库**，确保 `scripts/run_pipeline.py` 能从现有 raw 缓存重建训练/验证公共产物；若要求从空 raw 重建，还需先修复并验证下载链路

**交付物**：最终报告 + 完整代码仓库

---

## 六、关键风险登记表（v1.1）

| # | 风险点 | 严重度 | 防范措施 |
|---|---|---|---|
| 1 | **未来函数** | 极高 | 每个因子做"时间错位测试"；坚持 PIT 数据；T+1 开盘成交 |
| 2 | **幸存者偏差** | 高 | 用每日成分股快照（按生效日，不是公告日） |
| 3 | **财务数据非 PIT** | 高 | 用 `Annodt` 而非 `Accper`；写专门的 `test_pit.py` |
| 4 | **基准用错（价格 vs 全收益）** | 高 | 全程使用全收益指数 |
| 5 | **复权口径混用** | 高 | 全程后复权价 + 后复权收益率 |
| 6 | **去极值用全样本统计量** | 高 | 仅用 T 日横截面计算边界 |
| 7 | **测试集污染** | 高 | 每次跑测试集 git commit `[TEST_SET_RUN_N]`；不超过 2 次。当前尚未有效运行测试集，剩余 Run #1 / Run #2 |
| 8 | **过拟合** | 高 | 因子数量适度（20-30）；优先简单合成方法；参数尽量少 |
| 9 | **交易成本低估** | 中 | 当前配置下完整买卖双边约 26-31 bps；按日期切换印花税；做敏感性测试 |
| 10 | **涨跌停/一字板可成交假设** | 中 | 实现完整的成交约束逻辑 |
| 11 | **样本外退化的诱惑** | 高 | 退化是正常现象，写进报告 ≠ 回去调参 |
| 12 | **协方差矩阵病态** | 中 | Ledoit-Wolf 收缩 + 失败 fallback 到简化路线 |
| 13 | **cvxpy 不收敛** | 中 | 三层 fallback：严格 → 简化 → TopN 等权 |
| 14 | **金融股财务因子失真** | 中 | 行业内中性化 + 归因时单独检查 |
| 15 | **数据缺失/错误** | 中 | 每周做 sanity check |
| 16 | **行业分类口径** | 中 | 目标口径为申万一级 SW2021（31个行业）；转换脚本优先读 `industry_sw2021.csv` 并兼容旧 `industry_citics.csv`。当前 raw 仍是旧文件名，需在报告中说明或重新下载规范文件 |
| 17 | **2024 年小微盘危机影响测试集** | 中 | 在归因报告中专门讨论 |
| 18 | **第 1-2 周和第 9 周超时** | 中 | 内置 1 周缓冲；超时则砍因子数量而非流程 |

---

## 七、自检清单（每个阶段结束前过一遍）

### 数据阶段
- [ ] 股票代码格式统一（ts_code，含交易所后缀，如 000001.SZ）
- [ ] 日期均为 datetime64
- [ ] **复权方向明确，全程后复权**
- [ ] 成分股快照按**生效日**对齐
- [ ] 财务数据按 `Annodt` 对齐
- [ ] 自由流通市值字段确认无误
- [ ] **基准是全收益指数**

### 因子阶段
- [ ] T 日因子只用 ≤ T 日数据
- [ ] **去极值边界用 T 日横截面**
- [ ] 中性化的市值是 PIT 的 log(自由流通市值)
- [ ] 单因子 IC 方向符合经济逻辑
- [ ] 时间错位测试通过

### 信号阶段
- [ ] 合成权重在训练期确定
- [ ] 验证期不再调整
- [ ] **测试期 0 次调整**
- [ ] IC 加权时 IC 计算窗口严格 < T

### 协方差与组合阶段
- [ ] 协方差矩阵正定
- [ ] 优化器约束都用 PIT 数据
- [ ] 停牌/涨跌停/一字板正确处理
- [ ] Fallback 机制能跑通

### 回测阶段
- [ ] 权重 T 日盘后生成，T+1 开盘交易
- [ ] **印花税按日期切换**
- [ ] 涨跌停限制生效
- [ ] 全收益基准对比
- [x] 当前主版本 V0/V1/V2 齐备；V3/V4 不再作为当前主交付要求

### 报告阶段
- [x] 测试集运行次数已记录：旧 Run #1 记载已确认错误并删除，当前已用 0 次、剩余 2 次
- [ ] 局限性章节诚实
- [ ] 2024 小微盘危机有讨论
- [ ] 复现步骤清晰

---

## 八、严格 vs 简化路线的退化判断

当前实现已采用以下退化路线；表中 L1/L2/L3 对应 `portfolio_weights_meta.parquet` 中的 `fallback_level=0/1/2`。

| 层级 | 描述 | 触发条件 |
|---|---|---|
| L1（理想） | 严格协方差约束 + 完整线性约束 | 默认方案 |
| L2（简化） | 移除协方差约束，保留线性偏离约束 | cvxpy 严重不收敛或求解时间 > 30 秒 |
| L3（兜底） | TopN 等权（不做优化） | L2 仍失败 |

当前训练/验证期实测分布为 L1=67、L2=2、L3=15；L3 触发期必须在报告中披露，不能被描述成完全满足所有优化约束。

---

## 九、Git 与数据管理

### .gitignore 必须排除

```
data/raw/
data/processed/
data/factor/
*.parquet
*.csv
.ipynb_checkpoints/
__pycache__/
```

### 进 git 的内容

- 全部源代码（`src/`、`notebooks/`、`tests/`、`scripts/`）
- 全部文档（`PROJECT_PLAN_v1.1.md`、`README.md`、`reports/`、`check/`）
- `requirements.txt`
- 报告里用的图（`reports/figures/`）

### 数据备份

- `data/raw/` 本地多备份一份（Tushare Pro 接口有频率限制，重新下载耗时较长）
- 当前使用 `scripts/run_pipeline.py` 重建训练/验证 processed 数据；如需从空 raw 目录完整重建，必须先运行并验证下载脚本

### 测试集运行记录

每次跑测试集，git commit message 必须含 `[TEST_SET_RUN_N]` 标记，N 是次数。**总次数 ≤ 2**。截至 2026-05-25，旧 `[TEST_SET_RUN_1]` 记载已确认错误并删除，当前已用 0 次、剩余 2 次。

---

## 十、可选扩展（时间富余时考虑）

按性价比排序：

1. **周频策略对比**（可选扩展；当前主流程仍为月频）
2. **因子 IC 衰减深度分析**（~1 天）
3. **不同协方差估计方法对比**（~2 天）
4. **滚动训练 vs 扩展窗口**（~2 天）
5. **多因子 ML 合成**（XGBoost）（~3 天，过拟合风险高）
6. **行业轮动叠加**（~3 天）
7. **T-cost 优化目标**（~3 天）

---

## 十一、参考资源

- 《主动投资组合管理》Grinold & Kahn
- 《量化投资策略与技术》丁鹏
- Barra 中国权益模型 CNE5/CNE6 文档
- 《Asset Management》Andrew Ang
- WorldQuant 101 Alphas（量价因子参考）
- 各券商研究所量化年度报告（兴业、广发、海通、华泰金工等）

---

## 版本历史

- **v1.0**（项目启动）：初版蓝图
- **v1.1**（自检修订）：
  - 修正跟踪误差约束的数学形式（线性 → 二次，配合协方差矩阵）
  - 新增第 5 周协方差矩阵估计模块
  - 明确基准为全收益指数
  - 明确价格口径为后复权
  - 明确成交价为 T+1 开盘 + 滑点
  - 印花税按日期切换
  - 中性化基准明确为 log(自由流通市值)
  - 明确成分股按生效日对齐
  - 去极值边界仅用横截面
  - 8 周计划扩展为 9 周（数据周和报告周各加缓冲）
  - 删除 Streamlit Dashboard
  - 新增三层 fallback 机制（L1/L2/L3）
  - 风险登记表从 10 项扩展到 18 项
  - 新增金融股、2024 小微盘危机等具体风险点
- **2026-05-20 当前状态校正**：
  - 新增第零节，明确训练/验证流水线已用当前代码刷新，正式测试集尚未运行
  - 修正测试集纪律：旧 Run #1 记载已确认错误并删除，当前已用 0 次、剩余 2 次
  - 更新实际目录结构、脚本入口、测试文件、候选因子和最终入模因子
  - 更新当前验证期结果、fallback 分布、非合规权重披露要求
  - 修正交易成本双边估算、行业文件名兼容状态、`make_data.py` 旧表述

- **2026-05-21 训练数据扩展决策**：
  - 启动 `feature/expand-train-2012` 分支，执行 `docs/teach/data_expansion_plan/phase_plan.md` v2.0 计划
  - 训练期从 2016-2021 扩展为 2012-2020，验证期从 2022 扩展为 2021-2022
  - 新增 13 个 Tushare Pro 数据接口（北向持仓、分析师预期、事件类等），技术因子改为从行情数据自行计算
  - raw 数据权重文件当前仅覆盖 2016 起，扩展后将重建为 2012 起
  - 测试集 2023-2025 仍冻结，run_count=0，扩展流程全程不触碰
  - 详细执行计划见 `docs/teach/data_expansion_plan/phase_plan.md`，阶段 0 冻结快照见 `docs/check/phase0_state_snapshot_20260521.md`

**下一步**：不要再按"进入第 1 周"理解本文件。当前下一步是：完成训练数据扩展（`docs/teach/data_expansion_plan/phase_plan.md` 阶段 1-9），重建 2012-2022 训练/验证产物后，再由用户明确授权运行正式测试集 Run #1。
