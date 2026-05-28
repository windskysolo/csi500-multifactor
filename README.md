# 中证500多因子指数增强

> **免责声明**：本项目仅用于学习和研究目的，不构成任何投资建议，不保证任何收益，不对任何投资损失负责。

## 项目概述

基于 Tushare Pro 公开数据，构建中证500多因子量化指数增强策略，评估区间为 2016-2025 年，月频调仓。

> 找文件先看：[docs/FILE_GUIDE.md](docs/FILE_GUIDE.md)

| 项目 | 说明 |
|---|---|
| 策略类型 | 截面多因子、月频调仓、组合优化 |
| 数据源 | Tushare Pro（行情、财务、行业、成分股） |
| 基准指数 | 中证500全收益指数（H00905.CSI，含分红再投资） |
| 行业分类 | 申万一级 SW2021（31个行业） |
| 训练集 | 2016-01-01 ~ 2021-12-31 |
| 验证集 | 2022-01-01 ~ 2022-12-31 |
| 测试集 | 2023-01-01 ~ 2025-12-31（总运行 ≤ 2 次） |
| 完成标准 | IR ≥ 0.5，超额最大回撤 ≤ 10%，年化双边换手 5-15 倍 |

## 环境安装

```bash
# Python 3.10+
pip install -r requirements.txt

# 配置 Tushare Token（必须）
export TUSHARE_TOKEN=your_token_here

# 补充数据 API 端点（可选）
# 默认使用非官方代理 http://tsdata.siboer.xin/1wan
# 该地址可用性和数据完整性无法保证，生产化时请替换为官方端点
# export SUPPLEMENT_API_URL=http://your-official-endpoint
```

## 数据下载（首次运行）

```bash
# 1. 下载行情、成分股、行业数据
python -m scripts.download_tushare all

# 2. 下载补充数据（停牌、指数成分权重等）
python -m scripts.download_supplement all

# 3. CSV → Parquet 转换
python -m scripts.csv_to_parquet all
```

## 训练/验证期流水线

可一键运行（跳过数据下载，显式跳过未实现的 quality 检查阶段）：

```bash
python -m scripts.run_pipeline --from-stage convert --skip quality
```

或分阶段运行：

```bash
# 4. 构建因子面板（2016-2022）
python -m scripts.build_factor_panels

# 5. 单因子评价（训练集 2012-2020，验证集 2021-2022）
python -m scripts.run_factor_evaluation

# 6. 合成信号（IC_IR 加权 + 等权，训练/验证期）
python -m scripts.run_signal_combination

# 7. 组合优化（训练/验证期权重）
python -m scripts.run_portfolio_optimization

# 8. 验证期回测（NAV、成本、换手）
python -m scripts.run_backtest

# 9. 验证期业绩归因（Brinson + 因子归因）
python -m scripts.run_attribution
```

Notebook（`notebooks/02_factor_evaluation.ipynb` 至 `06_attribution.ipynb`）
用于探索和可视化，读取上述脚本产物展示，不承担生产产物生成职责。

## 测试集运行（受控，最多 2 次）

```bash
# 查看当前已使用次数
python -c "from scripts.test_set_ledger import count_test_set_runs, remaining_test_set_runs; print(count_test_set_runs(), remaining_test_set_runs())"

# 运行第 1 次（当前已用 0 次，剩余 2 次）
# 运行前必须在 docs/check/test_set_run_log.md 预登记
python -m scripts.run_test_pipeline --run-id 1
```

详见 [docs/check/test_set_run_log.md](docs/check/test_set_run_log.md)。

## 运行测试

```bash
python -m pytest tests/ -q
```

## 目录结构

```
├── src/               # 核心模块（因子、信号、优化、回测、归因）
│   └── config.py      # 全局参数（所有路径、日期、研究参数集中于此）
├── scripts/           # 数据下载、因子构建、流水线脚本
├── notebooks/         # 探索性分析（结果复现需先运行上述流水线）
├── data/
│   ├── raw/           # Tushare Pro 原始 CSV（不入 git）
│   └── processed/     # 清洗后 Parquet（不入 git）
├── reports/           # 评估报告（CSV/MD，PNG 不入 git）
├── tests/             # 单元测试（PIT、成本、可投资域）
├── requirements.txt
├── docs/              # 计划、审查、教学、结构说明
└── CLAUDE.md          # AI 协作规范
```

## 重要注意事项

- **测试集纪律**：测试集 2023-2025 总运行次数 ≤ 2 次，当前已运行 0 次。
- **数据口径**：全程使用后复权价格和后复权收益率，基准为全收益指数（H00905.CSI）。
- **PIT 严格**：财务数据以 `ann_date`（公告日）为可用日，不以报告期 `end_date` 为准。
- **参数冻结**：训练期确定的研究参数（IC 阈值、优化约束）在测试集运行前不得修改。
