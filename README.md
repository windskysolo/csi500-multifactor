# 中证500多因子指数增强

> **免责声明**：本项目仅用于学习和研究目的，不构成任何投资建议，不保证任何收益，不对任何投资损失负责。

---

## 项目概述

基于 TinyShare（Tushare Pro 兼容）公开数据，构建中证500多因子量化指数增强策略，月频调仓。

| 项目 | 说明 |
|---|---|
| 策略类型 | 截面多因子、月频调仓、组合优化 |
| 数据源 | TinyShare SDK（Tushare Pro 风格接口） |
| 基准指数 | 中证500全收益指数（H00905.CSI，含分红再投资） |
| 行业分类 | 申万一级 SW2021（31个行业） |
| 训练集 | 2012-01-01 ~ 2020-12-31 |
| 验证集 | 2021-01-01 ~ 2022-12-31 |
| 测试集 | 2023-01-01 ~ 2025-12-31（总运行 ≤ 3 次，**已用 1 次**） |
| 完成标准 | IR ≥ 0.5，超额最大回撤 ≤ 10%，年化双边换手 500%–1500% |
| 当前主线 IR | **2.150**（验证期，rolling48_topn150_ew_hk_quarterly） |

> 上表中的主线与测试集数字是截至 2026-09-17 的入口快照。操作前必须分别复核 `registry/mainline.json`、对应不可变 run 产物和 `docs/logs/test_set_runs.json`。

---

## 环境安装

```bash
# Python 3.10+
pip install -r requirements.txt
```

主要依赖（版本以 `requirements.txt` 为准）：

| 包 | 用途 |
|---|---|
| `pandas>=2.0` `numpy` `pyarrow` | 数据处理与 Parquet 存储 |
| `scipy` `statsmodels` | 统计检验、OLS 中性化 |
| `scikit-learn` | LedoitWolf 协方差收缩 |
| `cvxpy>=1.4` | 二次规划组合优化 |
| `tinyshare` | 数据源 SDK（兼容 Tushare Pro） |

**求解器**（cvxpy 后端，任选其一）：
```bash
pip install clarabel   # 推荐，cvxpy >= 1.4 内置
pip install ecos       # 备用
```

**配置数据 Token**（必须）：
```powershell
# PowerShell
$env:TINYSHARE_TOKEN = "your_token_here"
```
```bash
# bash
export TINYSHARE_TOKEN=your_token_here
```

---

## 数据获取（首次运行）

```bash
# 1. 验证所有 API 可用性（必须先跑）
python -m scripts.download_tushare test

# 2. 下载全量数据（行情、财务、成分股、行业等）
python -m scripts.download_tushare all

# 3. 下载补充数据（港股持仓、涨跌停、停牌等）
python -m scripts.download_supplement all

# 4. CSV → Parquet 转换（加速后续读取）
python -m scripts.csv_to_parquet all
```

`download_tushare.py` 支持断点续传，中断后重新执行同一命令即可。

数据处理与面试学习请按 [数据转换与质量控制](docs/当前文档/02_学习材料/阶段02_数据转换与质量控制.md) 阅读；该文档同时记录当前质量门禁尚未闭环等已知缺陷。

PIT、历史成分股与交易状态请按 [PIT、投资域与交易状态](docs/当前文档/02_学习材料/阶段03_PIT投资域与交易状态.md) 阅读。

因子构建与截面预处理请按 [因子构建与预处理](docs/当前文档/02_学习材料/阶段04_因子构建与预处理.md) 阅读；其中包含当前注册因子、五步预处理、审计缺陷和面试回答框架。

单因子检验与因子准入请按 [单因子检验与准入](docs/当前文档/02_学习材料/阶段05_单因子检验与准入.md) 阅读；其中解释 Rank IC、显著性与 BH、五分组、shift/decay及当前因子准入证据。

---

## 代码结构

```
500 improve/
│
├── src/                           # 核心金融逻辑（不直接执行）
│   ├── config.py                  # 全局参数中心（路径、日期、成本、约束均在此）
│   ├── data/
│   │   ├── loader.py              # 行情/财务数据加载接口
│   │   ├── pit_loader.py          # PIT 财务数据（以公告日为准的严格 PIT 实现）
│   │   └── universe.py            # 可投资域（成分股快照 + 新股/ST/停牌过滤）
│   ├── factors/
│   │   ├── financial_factors.py   # 财务因子（价值 5 + 质量 6 + 成长 4，共 15 个）
│   │   ├── price_factors.py       # 价格/技术因子（动量、波动率、流动性等）
│   │   ├── alt_factors.py         # 另类因子（港股持仓 hk_hold、资金流等）
│   │   └── preprocess.py          # 因子预处理（MAD 去极值、中性化、标准化）
│   ├── evaluation/
│   │   ├── ic_analysis.py         # IC/IC_IR 计算与 BH 多重检验
│   │   ├── quintile_backtest.py   # 五分组回测
│   │   ├── shift_test.py          # 移位测试（未来函数检测）
│   │   ├── factor_health.py       # 因子健康评分（stable/weak/warn/reverse）
│   │   └── signal_quality.py      # 信号质量报告（TC、N_eff、ir_loss）
│   ├── signal/
│   │   ├── combiner.py            # IC_IR 加权合成信号
│   │   ├── ridge_combiner.py      # expanding Ridge 基类
│   │   ├── ridge_rolling.py       # 固定窗口 Rolling Ridge
│   │   └── ridge_decay.py         # 指数衰减 Ridge
│   ├── portfolio/
│   │   ├── covariance.py          # LedoitWolf 协方差估计
│   │   └── optimizer.py           # 组合优化（QP / TopN EW / L2 Forced 三种模式）
│   ├── backtest/
│   │   ├── engine.py              # 回测引擎（T+1 开盘成交、印花税切换）
│   │   ├── metrics.py             # 绩效指标（IR、超额收益、MDD、换手）
│   │   └── transaction.py         # 交易成本计算（印花税 + 佣金 + 滑点）
│   ├── attribution/
│   │   ├── brinson.py             # Brinson 归因（配置效应 + 选股效应）
│   │   └── factor_attr.py         # 因子收益归因
│   └── pipeline/
│       ├── contracts.py           # 数据结构（ExperimentSpec、SignalSpec、OptimizerSpec 等）
│       ├── stages.py              # 各阶段编排（signal → portfolio → backtest）
│       ├── artifacts.py           # 产物路径契约与 SHA256 完整性校验
│       ├── registry.py            # 实验注册表（主基线晋升逻辑）
│       └── compare.py             # 多 run 横向比较逻辑
│
├── scripts/                       # 可直接执行的脚本
│   ├── run_experiment.py          # ★ 运行单个实验（最常用）
│   ├── compare_runs.py            # 生成横向比较板（experiment_board.md）
│   ├── promote_run.py             # 晋升 run 为主基线
│   ├── run_test_pipeline.py       # 测试集评估（受控，最多 3 次）
│   ├── build_factor_panels.py     # 构建所有因子面板 Parquet
│   ├── run_factor_evaluation.py   # 单因子评价（IC/IC_IR/五分组）
│   ├── run_signal_combination.py  # 独立运行信号合成（不走 Spec 框架）
│   ├── run_portfolio_optimization.py  # 独立运行组合优化
│   ├── run_backtest.py            # 独立运行回测
│   ├── run_attribution.py         # 运行 Brinson + 因子归因
│   ├── test_set_ledger.py         # 查询测试集已用/剩余次数
│   ├── download_tushare.py        # Tushare 数据下载
│   └── download_supplement.py    # 补充数据下载（港股持仓等）
│
├── configs/pipelines/             # ★ 实验配置文件（研究工作主要在此）
│   ├── frozen_baseline_icir_topn50_ew.py     # 冻结基线（不可修改）
│   ├── rolling48_topn150_ew_hk_quarterly.py  # 当前主基线
│   ├── baseline_*.py              # 主基线候选
│   ├── challenger_*.py            # 挑战者实验
│   └── ablation_*.py              # 消融实验
│
├── registry/
│   ├── mainline.json              # 当前主基线 run_id（由 promote_run.py 写入）
│   ├── challengers.json           # 挑战者注册表
│   └── archived_promotions.jsonl # 历史晋升日志
│
├── runs/                          # 实验产物（不进 git，本地自动生成）
│   ├── train_valid/<timestamp>__<experiment_id>/
│   └── test/test_run_N__<mainline_run_id>/
│
├── reports/                       # 可再生的跨 run 汇总与专项报告
│   ├── experiment_board.csv       # 最新横向比较数据
│   ├── experiment_board.md        # 最新横向比较 Markdown 表
│   ├── factor_evaluation/         # 单因子评价报告
│   └── archive/                   # 已被替代的历史报告快照
│
├── data/                          # 数据目录（不进 git，symlink 到本地存储）
│   ├── raw/                       # Tushare 原始 CSV
│   └── processed/                 # Parquet 缓存
│       └── factor_panels/         # 各因子面板（因子名.parquet）
│
├── tests/                         # 单元测试与架构边界测试
├── docs/
│   ├── 当前文档/                 # 当前治理、学习、面试和研究材料
│   ├── 历史归档/                 # 冻结的旧文档与迁移来源
│   ├── logs/                     # 测试集账本等治理日志
│   └── evidence/                 # 文档审计证据
├── check/                         # 按日期保存的阶段审计证据，不代表当前主线
├── logs/                          # 本地运行日志（不入 Git）
├── experiments/                   # 历史实验；legacy 仅保留历史脚本与兼容入口
├── paper/                         # 工程报告草稿；含个人信息版本不入 Git
├── AGENTS.md                      # AI 新会话入口、硬规则与信息导航
├── CLAUDE.md                      # Claude 兼容入口，仅转向 AGENTS.md
├── requirements.txt
└── pytest.ini
```

---

## 核心参数（`src/config.py`）

所有参数集中在 `src/config.py`，不得散落在代码中。关键参数：

| 参数 | 值 | 含义 |
|---|---|---|
| `TRAIN_START/END` | 2012-01-01 / 2020-12-31 | 训练集 |
| `VALID_START/END` | 2021-01-01 / 2022-12-31 | 验证集 |
| `TEST_START/END` | 2023-01-01 / 2025-12-31 | 测试集 |
| `STAMP_DUTY_CUT_DATE` | 2023-08-28 | 印花税切换日 |
| `STAMP_DUTY_BEFORE` | 0.001（10 bps） | 2023-08-28 前卖出单边 |
| `STAMP_DUTY_AFTER` | 0.0005（5 bps） | 2023-08-28 后卖出单边 |
| `COMMISSION_RATE` | 0.00025（2.5 bps） | 双边佣金 |
| `SLIPPAGE_RATE` | 0.0008（8 bps） | 滑点（保守估计） |
| `OPT_TE_TARGET_ANNUAL` | 0.06（6%） | QP 优化年化 TE 目标 |
| `OPT_TOPN` | 50 | L3 兜底 TopN 数 |
| `OPT_TURNOVER_LAMBDA` | 0.005 | 换手惩罚系数 |
| `EVAL_HIGH_CORR_THRESHOLD` | 0.70 | 因子高相关去冗余阈值 |
| `RANDOM_SEED` | 42 | 全局随机种子 |

---

## 实验框架

本项目采用 **Spec 驱动** 的不可变实验框架：

```
你写配置 → configs/pipelines/my_experiment.py
框架生成 → runs/train_valid/<timestamp>__my_experiment/（产物永不覆盖）
```

### ExperimentSpec 结构

每个实验配置文件定义一个 `SPEC` 变量，由三个子 Spec 组成：

```python
# configs/pipelines/my_experiment.py
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="my_experiment",       # 唯一 ID，成为目录名的一部分
    description="实验说明",
    period_scope="train_valid",          # "train_valid" 或 "test_run_N"

    signal=SignalSpec(
        method="ridge",                  # "icir" 或 "ridge"
        target="excess_return",
        training_mode="rolling",         # "expanding" / "rolling" / "decay_weighted_expanding"
        purge_months=2,                  # 防泄露的 purge 期数
        window_months=48,                # rolling 模式专用，单位：月
        alpha_grid=[0.1, 1.0, 10.0, 100.0, 500.0, 2000.0, 5000.0],
        selected_alpha_policy="cv_train_only",
    ),

    optimizer=OptimizerSpec(
        optimizer_mode="topn_ew",        # "qp" / "topn_ew" / "l2_forced"
        topn=150,                        # TopN EW 模式选股数
        te_target_annual=0.06,           # QP 模式专用
        turnover_lambda=0.005,           # QP 模式换手惩罚
        industry_max_dev=0.03,
        single_max_dev=0.015,
    ),

    backtest=BacktestSpec(
        execution="tplus1_open",         # T+1 开盘成交（唯一支持的模式）
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
```

### 运行产物目录结构

```
runs/train_valid/20260602_104439__rolling48_topn150_ew_hk_quarterly/
├── run_config.json              # 本次 Spec 快照
├── inputs.lock.json             # 输入信号 SHA256（复现追溯）
├── manifest.json                # 所有产物哈希校验清单
├── RUN_FINISHED.json            # 成功标志
├── signal/
│   ├── composite.parquet        # 合成信号面板
│   ├── coef_history.parquet     # Ridge 系数历史（ridge 模式）
│   └── signal_metadata.json
├── portfolio/
│   ├── target_weights.parquet   # 目标持仓权重
│   ├── baseline_weights.parquet # 基准权重（用于 Brinson 归因）
│   └── optimizer_meta.parquet   # 优化器元信息（L1/L2/L3 回退统计）
├── backtest/
│   ├── metrics_valid.parquet    # 验证期绩效指标汇总
│   ├── nav_valid.parquet        # 每日 NAV 曲线
│   ├── trades_valid.parquet     # 每期交易记录
│   └── actual_weights_valid.parquet
└── reports/
    └── self_check.md            # 自动生成的指标摘要 + PASS/FAIL
```

---

## 复现主线实验

以下步骤复现当前主线（验证期 IR=2.150）。

### 前置：构建因子面板

```bash
python -m scripts.build_factor_panels
# 输出：data/processed/factor_panels/<factor_name>.parquet
# 耗时约 30-60 分钟（视机器性能）
```

### 运行主线实验

```bash
python -m scripts.run_experiment \
    --spec configs/pipelines/rolling48_topn150_ew_hk_quarterly.py
```

主线 Spec 参数：Ridge Rolling-48 + TopN-150 EW，含 hk_hold 季度化修复。

运行结束后查看结果：

```bash
# 找到 run_id（格式：<timestamp>__rolling48_topn150_ew_hk_quarterly）
# 打开对应目录下的 self_check.md
cat runs/train_valid/<run_id>/reports/self_check.md
```

### 运行冻结基线（对照锚）

```bash
python -m scripts.run_experiment \
    --spec configs/pipelines/frozen_baseline_icir_topn50_ew.py
```

冻结基线（IR=0.924）是最简可解释方法（ICIR加权 + TopN50等权），作为所有实验的下限参照，永不修改。

### 生成横向比较板

```bash
python -m scripts.compare_runs
# 输出：reports/experiment_board.csv 和 reports/experiment_board.md
```

---

## 研究操作指南

### 1. 新增因子

```bash
# 第 1 步：在对应文件中实现因子函数
# src/factors/financial_factors.py  → 财务因子
# src/factors/price_factors.py       → 价格/技术因子
# src/factors/alt_factors.py         → 另类因子

# 第 2 步：重建因子面板（必须，否则评价脚本找不到新因子）
python -m scripts.build_factor_panels

# 第 3 步：隔离评价新因子（--output-dir 必须指定，否则覆盖主池结果）
python -m scripts.run_factor_evaluation \
    --output-dir reports/factor_evaluation_<name>
# 主要看：ic_result.csv（IC_IR、t 统计量）、factor_evaluation_report.md

# 第 4 步：确认通过所有门后，更新主因子池（脚本自动写 final_factors.json）
python -m scripts.run_factor_evaluation \
    --output-dir reports/factor_evaluation

# 第 5 步：写 Spec 对比实验（仅新增因子，其余参数与主基线一致）
python -m scripts.run_experiment \
    --spec configs/pipelines/challenger_new_factor_<name>.py
```

**因子准入标准（训练集）**：

| 指标 | 门槛 |
|---|---|
| IC_IR | ≥ 0.30 |
| \|IC 均值\| | ≥ 0.02 |
| BH 校正 p 值 | ≤ 0.05 |
| 方向稳定性 | ≥ 55% |
| 移位测试 lead_ratio | < 2.0x（否则疑似未来函数） |

### 2. 测试不同信号合成方法

只改 `SignalSpec`，其余与主基线保持一致：

```python
# 示例：IC_IR 加权 vs Ridge Rolling-48
signal=SignalSpec(
    method="icir",           # 改这里（"icir" 或 "ridge"）
    target="excess_return",
    training_mode="expanding",
)
```

`training_mode` 可选值：

| 值 | 说明 |
|---|---|
| `"expanding"` | 扩展窗口（用全部历史数据训练） |
| `"rolling"` | 滚动窗口（需指定 `window_months`，如 36/48/60） |
| `"decay_weighted_expanding"` | 指数衰减加权（需指定 `half_life_months`） |

### 3. 测试不同优化器参数

只改 `OptimizerSpec`，其余不变：

```python
# 示例：TopN EW 选 100 只
optimizer=OptimizerSpec(
    optimizer_mode="topn_ew",
    topn=100,
)

# 示例：QP 模式放宽 TE
optimizer=OptimizerSpec(
    optimizer_mode="qp",
    te_target_annual=0.08,    # 只改这一个
    turnover_lambda=0.005,
    industry_max_dev=0.03,
    single_max_dev=0.015,
    topn=50,
)
```

`optimizer_mode` 可选值：

| 值 | 说明 |
|---|---|
| `"topn_ew"` | 选 Alpha 最高的前 N 只，等权持有（**当前主线**） |
| `"qp"` | 二次规划（含 TE/行业/个股约束） |
| `"l2_forced"` | 强制 L2（线性约束，QP 失败时的退化路径） |

### 4. 复用已有信号（跳过慢步骤）

信号计算是最慢的阶段。只测试优化器时，可复用已有信号：

```bash
python -m scripts.run_experiment \
    --spec configs/pipelines/challenger_te8_lam0050.py \
    --from-stage portfolio \
    --input-signal-run <已有run的run_id>
```

### 5. 只跑信号阶段

```bash
python -m scripts.run_experiment \
    --spec configs/pipelines/my_experiment.py \
    --to-stage signal
```

### 6. 晋升主基线

```bash
# 晋升前提：run_dir 存在且有 RUN_FINISHED.json + reports/self_check.md
python -m scripts.promote_run \
    --run-id <run_id> \
    --reason "改进原因说明"
# 写入 registry/mainline.json，追加 registry/archived_promotions.jsonl
```

### 7. 查询测试集次数

```bash
python -c "
from scripts.test_set_ledger import count_test_set_runs, remaining_test_set_runs
print('已用:', count_test_set_runs(), '剩余:', remaining_test_set_runs())
"
```

### 8. 运行测试集（慎用，最多 3 次，当前已用 1 次）

```bash
# 运行前必须确认：研究已定型，主基线已晋升，test_set_used=false
python -m scripts.run_test_pipeline --run-id 2
# commit message 必须含 [TEST_SET_RUN_2] 标记
```

---

## 当前实验状态

| run | 角色 | 信号方法 | 优化器 | 验证期 IR |
|---|---|---|---|---|
| `frozen_baseline_icir_topn50_ew` | **冻结基线**（永不修改） | ICIR + TopN50 EW | 无 QP | 0.924 |
| `rolling48_topn150_ew_hk_quarterly` | **当前主基线** | Ridge Rolling-48 | TopN-150 EW | **2.150** |

测试集结果（第 1 次，2026-06-02）：IR=0.645，超额MDD=5.70%，换手728%，三硬指标全 PASS。

**两个基线不可直接比较**：冻结基线无 QP 约束，主基线使用 TopN EW 模式，IR 差距反映方法差异，不代表主线退步。新实验应与**主基线**（2.150）对比，不与冻结基线比较。

---

## 硬性约束

| 类别 | 约束 |
|---|---|
| 数据 | 全程后复权价格；基准为全收益指数（含分红）；财务数据以 `ann_date`（公告日）为准，不以报告期 `end_date` 为准 |
| 实验 | 每次只改一个变量；所有实验通过 Spec + `run_experiment.py` 运行，禁止手动跑裸脚本 |
| 测试集 | 总运行 ≤ 3 次；研究定型前不得使用；每次必须 git commit 含 `[TEST_SET_RUN_N]` 标记 |
| 注册表 | 不可直接编辑 `final_factors.json`（脚本自动写入，含哈希校验） |
| 冻结基线 | 不可修改或重跑 `frozen_baseline_icir_topn50_ew` Spec |
| 禁用库 | vectorbt / backtrader / zipline / 天勤 / 米筐 / 聚宽 SDK / Streamlit |

---

## 单元测试

```bash
python -m pytest tests/ -q
```

关键测试文件：

| 文件 | 覆盖内容 |
|---|---|
| `test_pit.py` | PIT 数据时间边界（随机抽样 5-10 只股票手动核对） |
| `test_factor_time_boundary.py` | 因子移位测试（未来函数检测） |
| `test_transaction.py` | 交易成本计算（含 2023-08-28 印花税切换） |
| `test_optimizer.py` | 优化器约束与 L1/L2/L3 退化逻辑 |
| `test_pipeline_artifacts.py` | 产物路径契约与 SHA256 完整性 |
| `test_hk_hold_quarterly.py` | hk_hold 季度 PIT 快照因子 |

---

## 文档导航

| 需求 | 文档 |
|---|---|
| AI/开发新会话入口 | `AGENTS.md`（先执行第 0 节） |
| 项目介绍、运行入口与真实目录结构 | `README.md`（本文件） |
| 当前全部文档入口 | `docs/当前文档/00_文档总览.md` |
| **从头学习并逐阶段检查项目（唯一执行口径）** | `docs/当前文档/01_项目治理/学习与审计主线.md` |
| 数据检验、处理、PIT与投资域专项学习 | `docs/当前文档/02_学习材料/` |
| 当前源码精读 | `docs/当前文档/02_学习材料/源码精读/00_源码精读使用说明.md` |
| 当前研究计划 | `docs/当前文档/04_研究与改进/最终业绩改进计划.md` |
| 历史文档和旧版本 | `docs/历史归档/README.md` |
| 参数决策依据 | `docs/当前文档/01_项目治理/项目规则与决策.md` |
| 当前因子检验与准入 | `docs/当前文档/02_学习材料/阶段05_单因子检验与准入.md` |
| 历史诊断结论 | `docs/历史归档/历史研究/历史改进记录/progress_log.md` |
| 测试集计数权威来源 | `docs/logs/test_set_runs.json` |
