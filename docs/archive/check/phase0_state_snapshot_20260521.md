# 阶段 0 现状冻结快照

**创建时间**：2026-05-21
**创建目的**：训练数据扩展计划（v2.0）启动前，完整记录当前可回滚的基准状态。

---

## 1. Git 状态

| 项目 | 值 |
|---|---|
| 当前分支 | `feature/expand-train-2012`（从 `master` 切出） |
| 基准提交 | `0f7a71f` — docs: 提交因子评价报告产物和gitignore更新 |
| 扩展分支 | `feature/expand-train-2012` |

---

## 2. 当前 src/config.py 日期切分

> 注意：`config.py` 已在本轮扩展前被更新为新切分，但最后一次 `run_pipeline` 的产物仍对应旧切分（见第4节）。

```python
MARKET_START = pd.Timestamp("2011-01-01")   # 已更新
EARLY_START  = pd.Timestamp("2010-01-01")   # 已更新
TRAIN_START  = pd.Timestamp("2012-01-01")   # 已更新（旧：2016-01-01）
TRAIN_END    = pd.Timestamp("2020-12-31")   # 已更新（旧：2021-12-31）
VALID_START  = pd.Timestamp("2021-01-01")   # 已更新（旧：2022-01-01）
VALID_END    = pd.Timestamp("2022-12-31")   # 不变
TEST_START   = pd.Timestamp("2023-01-01")   # 不变
TEST_END     = pd.Timestamp("2025-12-31")   # 不变
```

**旧切分（config 更新前 / 最后一次流水线所用）**：

```
训练期：2016-01-01 至 2021-12-31
验证期：2022-01-01 至 2022-12-31
```

---

## 3. 当前训练/验证产物状态

来源：`data/processed/run_manifests/run_20260520_212950.json`

| 项目 | 值 |
|---|---|
| run_id | 20260520_212950 |
| success | True |
| git_commit | 0f7a71f |
| train_end（产物口径） | **2021-12-31**（旧切分，与当前 config 不一致） |
| valid_end（产物口径） | 2022-12-31 |
| test_set_run_count | **1**（历史已消耗一次，仅余 1 次机会） |
| stages_executed | convert / factors / evaluate / signal / portfolio / backtest / attribution |
| quality 阶段 | 跳过（--skip quality） |

当前因子面板产物：

```
factor_panels:              27 个因子，2016-01-29 → 2022-12-30，shape=(84, 1065)
composite_signal_ic_ir:     2016-01-29 → 2022-12-30，shape=(84, 1065)
portfolio_weights_optimized:2016-01-29 → 2022-12-30，shape=(84, 1069)
backtest_nav:               2022-01-04 → 2022-12-30，shape=(242, 3)
```

最终入模因子（6 个）：

```
amihud, rev_yoy, ep_ttm, cfp, gross_margin, mom_12_1
```

---

## 4. 当前 raw 数据文件状态

| 文件/目录 | 状态 |
|---|---|
| `data/csi500_index_weight_201601_202512.csv` | **仅覆盖 2016 起**，是扩展计划的核心障碍之一 |
| `data/raw/daily_quote/` | 已有，日期范围待探针确认 |
| `data/raw/adj_factor/` | 已有 |
| `data/raw/daily_basic/` | 已有（含 free_share 字段待确认） |
| `data/raw/financial_income/` 等 4 张财务表 | 已有 |
| `data/raw/margin/`, `moneyflow/`, `holder_number/`, `dividend/` | 已有 |
| `data/raw/limit_list/`, `suspend/` | 已有 |
| 新增数据目录（hk_hold 等 12 个） | **尚不存在** |

---

## 5. 扩展计划变更原因记录

| 变更项 | 旧值 | 新值 | 原因 |
|---|---|---|---|
| TRAIN_START | 2016-01-01 | 2012-01-01 | 扩大样本量，覆盖 2015 股灾、2013 创业板等行情区间 |
| TRAIN_END | 2021-12-31 | 2020-12-31 | 将 2021-2022 整体作为验证集，避免验证期只有 1 年 |
| VALID_START | 2022-01-01 | 2021-01-01 | 同上，验证集从 1 年扩展为 2 年 |
| MARKET_START | 2016-01-01（原 raw 数据实际起点） | 2011-01-01 | 为 2012 训练期提供 1 年行情缓冲 |
| EARLY_START | 2014-01-01（原 financial 起点） | 2010-01-01 | 为 2012 训练期的财务 PIT 数据提供足够历史缓冲 |
| 新增数据品类 | 无 | 13 个新 API | 扩充因子候选池，见 phase_plan.md 阶段 3B |

---

## 6. 恢复旧状态的方法

```bash
git checkout master
```

master 分支上的所有代码和产物均未被本次分支操作修改。

---

## 7. 本快照的验收确认

- [x] feature/expand-train-2012 分支已创建，基于 master 最新提交
- [x] 旧切分（2016-2021/2022）和新切分（2012-2020/2021-2022）变更原因已记录
- [x] test_set_run_count=1 已记录，扩展计划全程不运行测试集
- [x] 当前产物与 config.py 日期不一致的情况已明确记录（config 已更新但产物未重建）
