# 一致预期因子实验计划

> 创建日期：2026-05-25  
> 基于版本：V2（16 因子池，`feature/expand-train-2012` 分支）  
> 文件用途：一致预期类因子的完整实验计划，含数据准备、构建规格、验证标准、优先级  
> 数据来源：`data/processed/analyst_rc_pit.parquet`（已处理）/ `data/raw/analyst_rc/`（原始）

---

## 一、现状盘点

### 1.1 已有因子

| 因子 | 状态 | 训练 IC_IR | 验证 IC_IR | 备注 |
|------|------|----------:|----------:|------|
| `analyst_eps_revision` | ✅ 入池 stable | +0.352 | +0.332 | 近90天 vs 远90天 EPS 中位数之比 |
| `analyst_rating_chg` | ❌ 未通过 | — | — | 评级粘性问题，截面分布过于集中 |

### 1.2 数据字段可用性（实测）

| 字段 | 来源 | 覆盖率 | 说明 |
|------|------|------:|------|
| `eps`（EPS 预测）| processed parquet | **99%+**（全年份）| 现有因子已用 |
| `rating`（评级）| processed parquet | 高 | 评级字符串，已用 |
| `max_price` / `min_price`（目标价）| processed parquet | **< 1%**（2017前为0）| **实际不可用** |
| `np`（净利润预测）| raw CSV only | **93-98%**（2010起）| 未进 parquet，需重处理 |
| `op_rt`（营收预测）| raw CSV only | 46%（2010）→ 85%（2023）| 未进 parquet，早期覆盖偏低 |
| `quarter`（预测期）| raw CSV only | 高，主要为年度预测 Q4 | 未进 parquet，盈利惊喜所需 |

**关键发现**：
- 目标价因子（`max_price`/`min_price`）覆盖率不足 1%，**直接放弃**，不列入计划
- `np` 和 `op_rt` 数据已下载，只需修改 `csv_to_parquet.py` 重跑一次即可使用
- `quarter` 字段几乎全为年度（Q4），季度预测很少

---

## 二、实验计划总览

| 编号 | 因子 | 数据准备 | 预期独立性 | 优先级 |
|------|------|---------|----------|-------|
| C1 | EPS 分散度 | ✅ 零成本，现有 parquet 直接算 | 高（与现有因子低相关）| **P1** |
| C2 | 分析师覆盖数变化 | ✅ 零成本，现有 parquet 直接算 | 中 | **P2** |
| C3 | 净利润预测增速 | ⚠️ 需重跑 parquet（原始数据已有）| 中（与 rev_yoy 不同维度）| **P2** |
| C4 | 营收预测增速 | ⚠️ 需重跑 parquet（原始数据已有）| 高（纯前瞻，历史成长不同）| **P3** |
| C5 | EPS 修正改进版 | ✅ 现有 parquet 即可改造 | — （改造现有因子）| **P2** |
| C6 | 盈利惊喜 | ❌ 需新下载数据 | 高 | **P4**（暂缓）|

---

## 三、C1：EPS 分散度（分析师分歧）

### 经济逻辑

当分析师对同一股票的 EPS 预测分歧越大，说明公司未来盈利的不确定性越高。不确定性高的股票被市场系统性高估（投资者对"彩票型"资产支付溢价），因此**高分歧 → 低未来超额收益**。这与行为金融学中的模糊厌恶理论一致。

### 因子公式

$$\text{eps\_dispersion}_{i,T} = -\frac{\text{std}(\text{EPS}_{i,[T-W, T]})}{\left|\text{median}(\text{EPS}_{i,[T-W, T]})\right|}$$

- 分子：过去 `W=180` 天内所有研报 EPS 预测的标准差
- 分母：同期 EPS 中位数的绝对值（规模标准化，避免大绝对值公司分散度虚高）
- 取**负号**：分散度低（分析师一致看好）→ 正暴露 → 正超额收益

### 数据准备

无需任何改动，直接从 `analyst_rc_pit.parquet` 的 `eps` 列计算。

```python
# 伪代码
rc = load_analyst_rc_pit(rebalance_date, codes=codes, lookback_days=180)
eps_std  = rc.groupby("ts_code")["eps"].std()
eps_med  = rc.groupby("ts_code")["eps"].median().abs()
dispersion = -(eps_std / eps_med.replace(0, np.nan))
```

### 关键约束

| 约束 | 值 | 原因 |
|------|---|------|
| 最少研报数 | ≥ 3 条（不同机构）| 少于 3 条时 std 无统计意义 |
| 时间窗口 | 180 天 | 与 `analyst_eps_revision` 保持一致 |
| 分母为 0 处理 | 返回 NaN | EPS 预测中位数为 0 时无法标准化 |
| EPS 全为负处理 | 分母取绝对值，正常计算 | 亏损公司分散度同样有意义 |

### 与现有因子的相关性预判

- 与 `analyst_eps_revision`：理论上低相关（修正方向 vs 分歧程度是不同维度）
- 与 `ivol_60d`：可能存在 0.2-0.4 的正相关（高分歧公司往往高波动），**需要实测**
- 若 `|r| > 0.5`，重新评估是否进池

### 验证标准（四道门）

| Gate | 要求 | 备注 |
|------|------|------|
| Gate 0 覆盖率 | ≥ 80% | 预期较高，EPS 数据覆盖率 99% |
| Gate 1 IC_IR | ≥ 0.3，t ≥ 2 | 文献支撑中等，A 股预期 0.25-0.40 |
| Gate 2 lead_ratio | < 2.0x | 分散度是截面信号，预期 lead_ratio 较低 |
| Gate 3 验证期 | 方向不变，衰减 < 50% | — |

---

## 四、C2：分析师覆盖数变化

### 经济逻辑

新分析师开始跟踪某只股票，说明该公司的信息摩擦在降低、机构关注度提升，往往伴随估值修复和交易量上升。从"无人关注"到"被发现"是一个渐进过程，覆盖数**增加**是正向信号。

### 因子公式

$$\text{analyst\_cnt\_chg}_{i,T} = N_{\text{recent}} - N_{\text{prior}}$$

- $N_{\text{recent}}$：过去 90 天内发布研报的**不同机构**数量（去重 `org_name`）
- $N_{\text{prior}}$：前 90 天（T-180 到 T-90）内的不同机构数量

### 数据准备

无需改动，直接从现有 parquet 计算。

```python
rc = load_analyst_rc_pit(rebalance_date, codes=codes, lookback_days=180)
split = rebalance_date - pd.Timedelta(days=90)
recent_cnt = rc[rc["pit_date"] > split].groupby("ts_code")["org_name"].nunique()
prior_cnt  = rc[rc["pit_date"] <= split].groupby("ts_code")["org_name"].nunique()
chg = recent_cnt.sub(prior_cnt, fill_value=0)
```

### 关键约束

- 用**机构数**（`org_name` 去重），不用研报总条数（同一机构多次发报不应重复计算）
- 新股上市初期覆盖数天然增加，需过滤上市 < 6 个月的股票（与标准宇宙过滤一致）
- 早期（2012-2014）分析师池较小，截面相对排名更重要，绝对数差值会偏小

### 预期效果

此因子是 **信息摩擦** 类信号，与成长/动量类因子相关度低，可提供独立的维度。但在 A 股，研报更多跟随大市值，覆盖数变化对中小盘更有效。中证 500 处于中大盘区间，预期 IC_IR 约 0.15-0.25，属于辅助信号。

---

## 五、C3：净利润预测增速

### 经济逻辑

分析师预测的下一年净利润增速，是市场对公司基本面最直接的前瞻性判断。相比已实现的历史成长（`rev_yoy`），预期增速更能捕捉市场尚未完全定价的盈利改善。

### 因子公式

$$\text{np\_forecast\_growth}_{i,T} = \frac{\text{median}(\text{NP\_forecast}_{i,[T-90,T]})}{\text{median}(\text{NP\_forecast}_{i,[T-180,T-90]})} - 1$$

- 近期 90 天 vs 远期 90 天的净利润预测中位数之比
- 逻辑与现有 `analyst_eps_revision` 完全一致，只是把 `eps` 换成 `np`

### 数据准备步骤

**第一步：修改 `scripts/csv_to_parquet.py`，在 `keep` 列表中加入 `np` 和 `quarter`**

定位到 `process_analyst_rc_pit()` 函数的第 1455 行：

```python
# 原代码
keep = ["pit_date", "ts_code", "org_name", "author_name", "eps", "pe", "rating",
        "report_title", "max_price", "min_price"]

# 修改为
keep = ["pit_date", "ts_code", "org_name", "author_name", "eps", "pe", "rating",
        "report_title", "max_price", "min_price", "np", "op_rt", "quarter"]
```

同时在数值类型转换处添加 `np` 和 `op_rt`：

```python
# 原代码
for col in ["eps", "pe"]:

# 修改为
for col in ["eps", "pe", "np", "op_rt"]:
```

**第二步：重跑 parquet 生成**

```bash
python scripts/csv_to_parquet.py --modules M14
```

重跑后 `analyst_rc_pit.parquet` 新增 `np`、`op_rt`、`quarter` 三列，不影响现有因子。

**第三步：验证**

```python
df = pd.read_parquet("data/processed/analyst_rc_pit.parquet")
print(df[["np", "op_rt", "quarter"]].notna().mean())  # np ~0.96, op_rt ~0.70
```

### 与 C4（营收预测增速）的区别

| 维度 | C3 净利润预测增速 | C4 营收预测增速 |
|------|----------------|---------------|
| 易被操控 | 较易（应计利润）| 较难（营收造假成本高）|
| 早期覆盖率 | ~94% | ~50%（2010-2014 偏低）|
| 先测哪个 | **优先测 C3**（覆盖率更高）| 在 C3 通过后再测 |

### 验证标准

与 C1 相同的四道门。特别关注与 `analyst_eps_revision` 的相关性：两者用相同的时间窗口逻辑，但 EPS 是每股、np 是总量，若 `|r| > 0.6`，优先保留 IC_IR 更高的一个。

---

## 六、C4：营收预测增速

### 经济逻辑

分析师对未来营收的预测比净利润更难"按摩"，代表公司真实业务扩张前景的市场共识。当分析师集体上调营收预期时，往往领先于股价的正向反应。

### 因子公式

$$\text{op\_rt\_forecast\_growth}_{i,T} = \frac{\text{median}(\text{OpRt\_forecast}_{i,[T-90,T]})}{\text{median}(\text{OpRt\_forecast}_{i,[T-180,T-90]})} - 1$$

### 数据准备

与 C3 共享同一次 parquet 重跑（`op_rt` 字段同时加入），**无额外数据准备成本**。

### 注意事项

- 早期（2012-2015）覆盖率约 50%，低于 80% 基准线，需检验 Gate 0
- 若 Gate 0 在早期年份不通过，可缩短覆盖要求的样本起始点至 2015 年
- 与 `rev_yoy`（已实现的历史营收增速）形成前瞻 vs 后视的互补对，两者相关度约 0.2-0.4

---

## 七、C5：analyst_eps_revision 改进版

### 现有实现的局限

当前 `analyst_eps_revision` 比较的是"近 90 天所有研报"vs"90-180 天所有研报"的 EPS 中位数。隐含的问题：近期研报来自的分析师群体，可能与远期研报来自的群体不同，导致比率捕捉到的是"分析师群体更替"而非"同一分析师修正预期"。

### 改进方向（可选实验）

追踪**同一机构**对同一预测期（如 FY1）的前后两次预测变化：

$$\text{eps\_revision\_v2}_{i,T} = \frac{1}{N} \sum_{\text{org}} \frac{\text{eps\_latest}_{i,\text{org}}}{\text{eps\_prior}_{i,\text{org}}} - 1$$

其中 "latest" 和 "prior" 分别是同一机构最近一次和前一次研报的 EPS 预测。

### 实施条件

- 需要能追踪同一机构的前后两次预测，要求 `org_name` 在同一股票下有多次记录
- 需要 `quarter` 字段确认两次预测指向同一财年（否则比较没有意义）
- **建议在 C3 parquet 重跑后再做这个实验**，因为届时 `quarter` 已经入库

### 优先级说明

此实验是改造现有 stable 因子，不是填补空缺。若 `analyst_eps_revision` 本身已经 stable（IC_IR 0.332），改进版的边际价值取决于两者 IC_IR 的差值是否值得工程投入。**建议先做 C1-C4，再回来做 C5**。

---

## 八、C6：盈利惊喜（暂缓）

### 经济逻辑

公司实际公布的 EPS 超过市场预期（consensus EPS before announcement），会触发 PEAD（Post-Earnings Announcement Drift）效应：市场对好消息的定价滞后，导致超预期公司在随后 1-3 个月持续跑赢。

### 为什么暂缓

| 问题 | 描述 |
|------|------|
| 需要新下载数据 | Tushare `express`（业绩快报）接口，含实际公告 EPS，当前未下载 |
| 预测期匹配复杂 | 需要将每份研报的预测期（`quarter` 字段）与实际财报公告期精确对齐 |
| A股 PEAD 的月频效果存疑 | PEAD 在 A 股主要集中在公告后 5-20 个交易日，月频调仓可能只抓到尾部效应 |
| 工程复杂度高 | 相对上述因子，这是最复杂的，放到其他因子跑完后再考虑 |

### 未来执行路径（备忘）

1. 下载 `tushare.pro.express`（业绩快报），获取实际公告 EPS
2. 以 `ann_date`（公告日）为 PIT 时间戳，匹配 `end_date`（报告期）
3. 从 `analyst_rc_pit` 中取公告日前 90 天内、针对同一报告期的分析师共识 EPS
4. 计算 `surprise = (actual_eps - consensus_eps) / |consensus_eps|`
5. 按 PIT 对齐到每月调仓日

---

## 九、执行顺序与检查点

```
阶段 A（零成本，立刻可做）
├── 实现 C1：EPS 分散度
│   ├── 在 alt_factors.py 新增 factor_eps_dispersion()
│   ├── 在 FACTOR_MAP 注册
│   └── 运行评估，记录 IC_IR / lead_ratio / 覆盖率
└── 实现 C2：分析师覆盖数变化
    ├── 在 alt_factors.py 新增 factor_analyst_cnt_chg()
    └── 同上

检查点 A：若 C1/C2 均未通过 Gate 1（IC_IR < 0.3），重新评估 C3-C4 的优先级

阶段 B（需重跑 parquet）
├── 修改 csv_to_parquet.py：keep 列表加 np / op_rt / quarter
├── 重跑 M14 模块
├── 验证新字段覆盖率
├── 实现 C3：净利润预测增速
└── 实现 C4：营收预测增速（Gate 0 覆盖率通过则继续）

检查点 B：汇总 C1-C4 通过门控的因子，与现有16因子做相关矩阵，确认增量价值

阶段 C（可选，改造现有因子）
└── C5：analyst_eps_revision 改进版（若 C3 结果提示有改进空间）

阶段 D（暂缓）
└── C6：盈利惊喜（待其他因子稳定后再下载新数据）
```

---

## 十、成功标准与进池决策规则

新一致预期因子通过所有四道门后，进池前还需满足：

1. **与现有 stable 因子相关度**：与 `analyst_eps_revision` 的截面相关 `|r| < 0.6`（两者逻辑相近，若高度相关则只保留 IC_IR 更高的一个）
2. **维度独立性**：加入合成信号后，组合 IC_IR 相比单独去掉该因子时有提升
3. **覆盖率不拖累信号**：若 Gate 0 覆盖率 < 80% 的年份超过全样本期的 30%，不进池

预期结果：C1（EPS 分散度）进池概率最高，C3（净利润预测增速）次之，C4 视早期覆盖率而定。

---

*相关文档：`teach/factor_roadmap/factor_research_guide.md`（整体因子研究方向）*  
*数据脚本：`scripts/csv_to_parquet.py` L1427–L1468（M14 模块，需修改 `keep` 列表）*  
*实现位置：`src/factors/alt_factors.py`（新因子在此模块追加）*
