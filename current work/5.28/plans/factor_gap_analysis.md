# 中证500增强策略：因子池全面空缺诊断与修复方案

> **文档性质**：研究分析文档（非操作手册）  
> **策略标的**：中证500指数增强 · 月度调仓  
> **当前因子池**：17个正式因子 + 1个候补（high_52w_v2）  
> **训练期**：2012-01 ~ 2020-12 · **验证期**：2021-01 ~ 2022-12  
> **测试集**：2023-2025（受保护，剩余3次机会，本文档所有分析不触碰）  
> **生成日期**：2026-05-28

---

## 目录

1. [执行摘要](#一执行摘要)
2. [当前因子池全景评估](#二当前因子池全景评估)
3. [系统性空缺诊断矩阵](#三系统性空缺诊断矩阵)
4. [空缺一：应计项目因子（Accruals）](#四空缺一应计项目因子)
5. [空缺二：资产增速因子（Asset Growth）](#五空缺二资产增速因子)
6. [空缺三：自由现金流收益率（FCF Yield）](#六空缺三自由现金流收益率)
7. [空缺四：投入资本回报率（ROIC）](#七空缺四投入资本回报率)
8. [空缺五：市净率倒数（Book-to-Price）](#八空缺五市净率倒数)
9. [空缺六：盈利意外（SUE）](#九空缺六盈利意外)
10. [空缺七：分析师预期分散度（Forecast Dispersion）](#十空缺七分析师预期分散度)
11. [空缺八：股本稀释因子（Share Issuance）](#十一空缺八股本稀释因子)
12. [空缺九：融资净买入因子（Margin Financing）](#十二空缺九融资净买入因子)
13. [空缺十：价格动量因子（Price Momentum 12-1m）](#十三空缺十价格动量因子)
14. [空缺十一：短期价格反转（Short-term Reversal）](#十四空缺十一短期价格反转)
15. [空缺十二：大股东行为因子（Insider Behavior）](#十五空缺十二大股东行为因子)
16. [明确不建议纳入的因子](#十六明确不建议纳入的因子)
17. [综合优先级排序与实施路线图](#十七综合优先级排序与实施路线图)
18. [附录：标准入池检验流程模板](#十八附录标准入池检验流程模板)

---

## 一、执行摘要

### 核心结论

当前因子池的**维度覆盖存在三个系统性空白**，导致策略在市场风格快速切换时的防御能力不足：

**空白一：盈利质量的深度维度不足**  
现有质量因子（q_roe、gross_margin、piotroski_f）均衡量盈利能力的**水平与趋势**，但没有任何因子衡量"这份利润是否为真实可持续的现金利润"。应计项目（Accruals）和自由现金流收益率（FCF Yield）正是填补这个空白的核心工具。

**空白二：资本配置效率完全缺失**  
你有营收成长信号（rev_acceleration、rev_yoy）、盈利效率信号（q_roe），但完全没有"公司是否在低效率地消耗资本"的信号。资产增速（Asset Growth）和股本稀释（Share Issuance）是两个独立机制的资本行为信号，与现有因子的相关性极低，能提供真正的增量信息。

**空白三：A 股本土杠杆资金行为缺失**  
你覆盖了外资行为（北向资金），覆盖了股东数量变化（holder_chg），却完全没有 A 股最重要的本土杠杆资金信号——融资融券。中证500宇宙标的的融资融券覆盖率极高，这是一个专属 A 股结构的、有持续预测力的信号。

### 12 个空缺的优先级概览

| 优先级 | 空缺因子 | 类别 | 数据获取 | 建议行动 |
|--------|---------|------|---------|---------|
| 🔴 P0 | 应计项目（accruals） | 盈利质量 | Tushare 财报 | 立即构建入池测试 |
| 🔴 P0 | 盈利意外（SUE） | 行为偏差 | Wind 预期数据 | 立即构建入池测试 |
| 🔴 P0 | 资产增速（asset_growth） | 资本配置 | Tushare 财报 | 立即构建入池测试 |
| 🟠 P1 | 自由现金流收益率（fcf_yield） | 估值 | Tushare 财报 | 本季度 |
| 🟠 P1 | 融资净买入（margin_flow） | A股行为 | Tushare 融资融券 | 本季度 |
| 🟠 P1 | 股本稀释（share_issuance） | 公司行为 | Tushare 股本 | 本季度 |
| 🟡 P2 | ROIC | 盈利能力 | Tushare 财报 | 下季度 |
| 🟡 P2 | 市净率倒数（bp） | 估值 | Tushare 行情 | 下季度 |
| 🟡 P2 | 分析师预期分散度 | 信息不确定性 | Wind 预期 | 下季度 |
| 🟢 P3 | 价格动量 12-1m | 价格动量 | Tushare 行情 | 半年内（Ridge 专用）|
| 🟢 P3 | 短期价格反转 1m | 微结构 | Tushare 行情 | 半年内 |
| 🟢 P3 | 大股东增减持 | 内部人行为 | Tushare 公告 | 半年内（数据质量待评估）|

---

## 二、当前因子池全景评估

### 2.1 17+1 因子完整状态表

| # | 因子 | 维度 | 方向 | 训练 IC_IR | 验证 IC_IR | 状态 | 衰减率 |
|---|------|------|------|:---------:|:---------:|------|------:|
| 1 | turn_20d | 流动性 | − | −0.689 | −0.707 | 🟢 stable | +3% (增强) |
| 2 | ivol_60d | 风险特征 | − | −0.675 | −0.641 | 🟢 stable | −5% |
| 3 | hk_hold_ratio | 资金/情绪 | + | +0.579 | +0.425 | 🟢 stable | −27% |
| 4 | rev_acceleration | 成长 | + | +0.574 | +0.725 | 🟢 stable | +26% (增强) |
| 5 | roe_delta_3q | 成长/质量 | + | +0.518 | +0.338 | 🟢 stable | −35% |
| 6 | amihud | 流动性 | + | +0.492 | +0.211 | 🟢 stable | −57% |
| 7 | hk_hold_chg | 资金/情绪 | + | +0.497 | +0.154 | 🟡 weak | −69% |
| 8 | ep_ttm | 估值 | + | +0.473 | +0.119 | 🟡 weak | −75% |
| 9 | cfp | 估值 | + | +0.441 | +0.190 | 🟡 weak | −57% |
| 10 | holder_chg | 资金/情绪 | + | +0.379 | +0.319 | 🟢 stable | −16% |
| 11 | analyst_eps_revision | 基本面动量 | + | +0.352 | +0.332 | 🟢 stable | −6% |
| 12 | gross_margin | 财务质量 | + | +0.333 | −0.040 | 🟠 warn | >100% |
| 13 | q_roe | 财务质量 | + | +0.486 | −0.098 | 🟠 warn | >100% |
| 14 | roe_delta | 成长/质量 | + | +0.483 | −0.040 | 🟠 warn | >100% |
| 15 | rev_yoy | 成长 | + | +0.448 | −0.020 | 🟠 warn | >100% |
| 16 | gross_margin_trend | 质量/成长 | + | +0.429 | −0.069 | 🟠 warn | >100% |
| 17 | piotroski_f | 财务质量 | + | +0.615 | −0.200 | 🔴 reverse | — |
| 18* | high_52w_v2 | 价格动量 | + | +0.328 | 稳定 | ⚪ 候补 | — |

**健康状态汇总**：稳定 7 个 · 偏弱 3 个 · 警告 5 个 · 反转 1 个 · 候补 1 个

### 2.2 当前五种策略性能对比

| 策略 | 信号方法 | IR | 年化超额 | 最大回撤 | 月胜率 |
|------|---------|---:|-------:|--------:|------:|
| IC_IR 加权扩张 | IC_IR 加权 | 0.402 | +2.45% | −7.75% | 56.5% |
| Ridge 扩张 (主基线) | Ridge 扩张窗口 | 0.408 | +2.44% | −8.16% | 43.5% |
| Ridge 滚动 36m | Ridge 滚动 | 0.966 | +5.80% | −8.38% | 52.2% |
| **Ridge 滚动 48m** | **Ridge 滚动** | **1.489** | **+8.63%** | **−5.82%** | **56.5%** |
| Ridge 滚动 60m | Ridge 滚动 | 1.176 | +6.87% | −6.73% | 47.8% |

**当前最优策略**（rolling_48m）已通过全部合规标准，但尚未正式晋升为主基线。

### 2.3 验证期维度归因（Ridge 扩张基线）

| 因子维度 | 累计超额贡献 | 主要来源 |
|---------|----------:|---------|
| 成长 | **+5.50%** | rev_yoy（warn 但仍是主贡献）|
| 波动率 | **+3.98%** | ivol_60d（跨周期最稳定）|
| 资金流向 | **+3.27%** | hk_hold_ratio |
| 动量 | **+2.00%** | analyst_eps_revision |
| 估值 | **−1.57%** | ep_ttm/cfp 验证期偏弱 |
| 质量 | **−1.46%** | piotroski_f 反转是主要拖累 |
| 流动性 | **−1.42%** | amihud 验证期衰减 |
| 残差 | **−4.17%** | 交易成本 + 模型残差 |

**净超额（内部口径）**：约 +6.18%

### 2.4 当前因子池的核心优势

**优势一：数据质量基础扎实**  
PIT 处理、四道门控、时间错位检验（lead_ratio），这些基础设施的质量在国内量化团队中处于较高水准。`rev_acceleration` 通过 `high_52w_v2` 的数据污染事件（lead_ratio=37.6x → 修复后 0.09）证明了这套体系的有效性。

**优势二：信号合成方法多元**  
五种策略同时运行，且 Ridge 滚动 48m 的验证期 IR 达到 1.489，已有一个性能优异的候选主策略。

**优势三：低相关稳定因子较强**  
`turn_20d`（IC_IR −0.689/−0.707）和 `ivol_60d`（IC_IR −0.675/−0.641）在跨风格周期中表现出极强的稳定性，这两个因子是当前组合的"压舱石"。

### 2.5 当前因子池的结构性局限

**局限一：质量维度只测"有多好"，不测"是否真实"**  
q_roe、gross_margin 等衡量盈利能力的水平，但没有任何因子质问"这份盈利中有多少是应计项目（非现金成分）"。一旦市场开始对盈利质量定价差异（通常在利率上行、信用收紧时期），这个空白会成为组合的脆弱点。

**局限二：公司行为维度几乎空白**  
股本变化（增发/回购）、大股东增减持、资产购置/出售——这些管理层用真实资金投票的行为信号，预测力往往强于财务报表的结果指标，但当前因子池对此没有覆盖。

**局限三：A 股结构性信号覆盖不对称**  
覆盖了外资（北向 2 个因子）、股东结构（holder_chg），但完全没有本土杠杆资金（融资融券）。这造成了信号体系对"外资定价"敏感，对"本土散户/杠杆资金定价"盲目。

**局限四：估值维度过于依赖盈利基础的估值**  
ep_ttm 和 cfp 都是"利润/现金流 ÷ 市值"，缺少基于资产负债表（PB）和企业价值（EV/EBITDA）视角的估值，在利率变化或负债结构重要的行业中会产生估值盲区。

---

## 三、系统性空缺诊断矩阵

下表从两个维度交叉映射：**信息来源**（数据从哪里来）× **经济机制**（为什么能预测收益）。

```
                  估值锚定   盈利质量   资本效率   风险溢价   行为偏差   A股结构
─────────────────────────────────────────────────────────────────────────────
财务报表          ep_ttm ✅  q_roe  ✅  ────❌────  ─────────  ─────────  ─────
                  cfp    ✅  gross_m✅  资产增速❌             应计项目❌
                  ──────❌── piotroski  FCF收益率❌            股本稀释❌
                  PB/BP  ❌  ✅         ROIC   ❌
                  EV/EBITDA❌
─────────────────────────────────────────────────────────────────────────────
财报+预期数据      ────────── ─────────  ─────────  ─────────  SUE    ❌  ─────
                                                              预期分散❌
─────────────────────────────────────────────────────────────────────────────
价格/行情         high_52w_v2 ─────────  ─────────  ivol_60d✅ 动量12-1m  ─────
                  ⚪候补                             turn_20d✅ ❌
                                                    amihud  ✅ 短期反转❌
─────────────────────────────────────────────────────────────────────────────
机构/资金行为      analyst_   ─────────  ─────────  ─────────  ─────────  hk_hold
                  revision✅                                              ratio  ✅
                  holder_chg✅                                             hk_hold
                                                                          chg ✅
                                                                          融资融券❌
                                                                          大股东❌
─────────────────────────────────────────────────────────────────────────────

✅ 已覆盖   ❌ 空缺   ⚪ 候补
```

**空缺计数**：

| 类别 | 已覆盖 | 空缺 |
|-----|-------|-----|
| 估值锚定 | 2（ep_ttm, cfp）| 3（PB/BP, EV/EBITDA, FCF Yield）|
| 盈利质量 | 3（q_roe, gross_m, piotroski_f）| 2（应计项目, ROIC）|
| 资本效率 | 0 | 3（资产增速, FCF Yield, ROIC 重叠计）|
| 风险溢价 | 3（ivol_60d, turn_20d, amihud）| 1（beta，不建议）|
| 行为偏差 | 2（analyst_rev, holder_chg）| 4（SUE, 预期分散度, 动量, 反转）|
| A股结构 | 2（hk_hold_ratio, hk_hold_chg）| 2（融资融券, 大股东增减持）|

---

## 四、空缺一：应计项目因子（Accruals）

### 4.1 当前状态

**当前覆盖**：`ep_ttm`（利润/市值）和 `cfp`（经营现金流/市值）  
**空缺内容**：两者之差的信息——"利润中有多少是非现金的应计成分"  
**影响评估**：盈利质量维度的核心空白，在信用收紧、财务造假高发时期策略脆弱

### 4.2 为什么是空缺

你已有 `ep_ttm` 和 `cfp`，看似已经覆盖了盈利。但这里有一个关键洞察：

$$\text{净利润} = \underbrace{\text{经营现金流}}_{\text{cfp 已捕捉}} + \underbrace{\text{应计项目}}_{\text{未被任何因子单独捕捉}}$$

应计项目是净利润中**非现金的会计调整部分**（如应收账款增加、存货增加、折旧调整等）。**Richard Sloan（1996）** 的经典发现：

- 应计成分的**持续性** < 现金流成分的**持续性**
- 市场错误定价：高应计公司的股价中嵌入了对不可持续利润的过度乐观
- 后续修正：高应计公司股票的未来收益系统性偏低

你的 `piotroski_f` 子项 F4（"现金流 > 净利润"）有局部重叠，但那是 0/1 二元变量，损失了大量连续信息。应计因子是**连续且独立**的信号。

**A 股特殊性**：A 股上市公司的应计异象强于美股，因为：
1. 盈余管理更普遍（保配送、保上市、避免退市）
2. 会计师事务所的审计独立性平均低于美股
3. 中证500中有大量中小盘公司，信息透明度低

### 4.3 修复方案

**推荐使用现金流量表法**（而非资产负债表法），原因：数据更稳定，对会计重分类不敏感。

**因子定义**：

$$\text{accrual\_quality} = \frac{\text{经营活动现金流}_{TTM} - \text{净利润}_{TTM}}{\text{平均总资产}} \times (-1)$$

乘以 $-1$ 后调整方向：**值越高 = 现金流质量越好 = 预期超额收益越高（正向因子）**

```python
import pandas as pd
import numpy as np


def compute_accrual_quality(
    cashflow_df: pd.DataFrame,  # 现金流量表 PIT 数据
    income_df:   pd.DataFrame,  # 利润表 PIT 数据
    balance_df:  pd.DataFrame,  # 资产负债表 PIT 数据
) -> pd.DataFrame:
    """
    应计质量因子（正向：值越高，现金流质量越好）

    所需字段：
    - cashflow_df : n_cashflow_act（经营活动现金流净额，TTM）
    - income_df   : n_income（净利润，TTM）
    - balance_df  : total_assets（总资产，期末值）

    Returns
    -------
    DataFrame: index=date, columns=stock_code, 值为应计质量因子
    """
    # ── Step 1: TTM 经营现金流 ─────────────────────────────────────────
    # Tushare 的 cashflow_pit 表中 n_cashflow_act 通常是单季度值
    # 需要滚动求和得到 TTM（过去4个季度累计）
    oper_cf_ttm = (cashflow_df['n_cashflow_act']
                   .unstack('stock_code')
                   .rolling(window=4, min_periods=4)
                   .sum())

    # ── Step 2: TTM 净利润 ────────────────────────────────────────────
    net_income_ttm = (income_df['n_income']
                      .unstack('stock_code')
                      .rolling(window=4, min_periods=4)
                      .sum())

    # ── Step 3: 平均总资产（期初 + 期末）/ 2 ───────────────────────────
    total_assets = balance_df['total_assets'].unstack('stock_code')
    avg_assets   = (total_assets + total_assets.shift(4)) / 2

    # ── Step 4: 应计原始值（现金流 − 净利润，除以资产标准化）──────────
    accruals_raw = (oper_cf_ttm - net_income_ttm) / avg_assets

    # ── Step 5: 方向取反（使因子为正向）并截尾去极值 ────────────────
    accrual_quality = -accruals_raw

    # 行业内 MAD 截尾（±3×MAD），防止极端财务数据污染
    def mad_winsorize(s, n=3):
        med = s.median()
        mad = (s - med).abs().median()
        return s.clip(med - n * mad, med + n * mad)

    accrual_quality = accrual_quality.apply(
        lambda row: mad_winsorize(row.dropna()), axis=1, result_type='expand'
    )

    return accrual_quality


# ── 数据说明 ─────────────────────────────────────────────────────────────
# Tushare 接口：
#   cashflow_pit: pro.cashflow_vip(ann_date=...) 或 cashflow_vip
#   income_pit  : pro.income_vip(ann_date=...)
#   balance_pit : pro.balancesheet_vip(ann_date=...)
# 关键：必须用 ann_date（公告日）而非 end_date（报告期截止日）过滤，保证 PIT

# ── 与现有因子的相关性预估 ──────────────────────────────────────────────
# accrual_quality vs cfp    : ~0.40–0.55（中等相关，有增量信息）
# accrual_quality vs ep_ttm : ~0.25–0.40（弱相关，独立信息更多）
# accrual_quality vs q_roe  : ~0.20–0.35（弱相关）
```

### 4.4 入池测试要求

在提交四道门检验前，需额外验证：

- **行业分层检验**：金融行业（银行/保险）的现金流定义特殊，建议设置 NaN 或单独处理，避免拉偏截面回归
- **与 cfp 相关性检验**：若截面相关性超过 0.75，考虑取残差正交化（见问题三）
- **极端值处理**：亏损年度 `avg_assets` 为负时，因子值需设为 NaN

### 4.5 数据来源与可行性

| 项目 | 详情 |
|-----|------|
| 数据接口 | Tushare Pro: `cashflow_vip`、`income_vip`、`balancesheet_vip` |
| PIT 字段 | `ann_date`（公告日），严格按公告日过滤 |
| 覆盖率预估 | ≥ 90%（财报覆盖齐全） |
| 构建复杂度 | 低（所有原始数据已在现有管道中）|
| 开发工作量 | 1–2 天 |

**优先级：🔴 P0 — 立即构建**

---

## 五、空缺二：资产增速因子（Asset Growth）

### 5.1 当前状态

**当前覆盖**：成长维度有 rev_acceleration（营收增速加速度）、rev_yoy（营收同比）  
**空缺内容**：公司是否在低效率地**扩张资产规模**（区分内生成长 vs 外延扩张）  
**影响评估**：中证500中有大量靠并购扩张的公司，这是一个高度差异化的信号

### 5.2 为什么是空缺

营收增速高的公司不等于资产配置效率高的公司。**Cooper、Gulen 和 Schill（2008，Journal of Finance）** 发现：

> **总资产年增速是横截面股票收益的强负向预测变量**——即增速越快，未来超额收益越低

背后有三种互补机制：
1. **过度投资假说**：管理层帝国建造倾向，低回报率项目不断被投入
2. **错误定价假说**：投资者对高增长公司过度乐观，后续均值回归
3. **风险降低假说**：实物期权执行后系统性风险降低，预期收益随之下降

对中证500特别重要：**成分股中有大量处于扩张期的制造业、科技公司**，其中靠并购堆砌资产的公司和靠内生盈利增长的公司，在资产增速上的特征截然不同，但在营收增速上的差异较小。

### 5.3 修复方案

**因子定义**：

$$\text{asset\_growth} = \frac{\text{总资产}_t - \text{总资产}_{t-4}}{\text{总资产}_{t-4}}$$

取反后为正向因子（增速越低 = 因子值越高 = 预期收益越高）

```python
def compute_asset_growth(
    balance_df: pd.DataFrame,  # 资产负债表 PIT 数据，total_assets 字段
    neutralize_industry: bool = True,
) -> pd.DataFrame:
    """
    资产增速因子（负向原始值，取反后为正向因子）

    经济逻辑：总资产扩张过快 → 低效投资 → 未来收益偏低
    负向因子：资产增速越低的公司，预期超额收益越高

    Parameters
    ----------
    balance_df         : PIT 资产负债表，需字段 total_assets，index=(date, stock)
    neutralize_industry: 是否在行业内计算（默认 True，金融行业总资产定义不同）
    """
    total_assets = balance_df['total_assets'].unstack('stock_code')

    # 同比增速：当季 vs 4个季度前（消除季节性）
    asset_growth_raw = total_assets.pct_change(periods=4)

    # ── 行业处理 ─────────────────────────────────────────────────────
    # 金融行业（银行/保险/证券）的总资产是其核心业务规模指标，
    # 与制造业/科技的"过度投资"逻辑不同，需单独处理
    # 建议：金融行业股票的该因子设为 NaN，在预处理阶段跳过

    # ── 方向取反（高增速 = 负向信号 → 取反变正向因子）──────────────
    asset_growth_factor = -asset_growth_raw

    # ── 截尾处理（±5，消除重组/剥离等极端事件）─────────────────────
    asset_growth_factor = asset_growth_factor.clip(-5, 5)

    return asset_growth_factor


def compute_capex_intensity(
    cashflow_df: pd.DataFrame,
    income_df:   pd.DataFrame,
) -> pd.DataFrame:
    """
    资本支出强度（可选补充因子，与 asset_growth 配合使用）

    capex_intensity = TTM资本支出 / TTM营业收入（取反，正向因子）
    
    经济逻辑：相对于营收规模的资本支出越高，
              占用资本越多，未来自由现金流越少 → 负向信号
    """
    # capex = 购建固定资产、无形资产支出 + 取得子公司支出
    capex_ttm = (cashflow_df.get('c_pay_acq_const_fiolta', 0) +
                 cashflow_df.get('c_pay_acq_staff', 0))
    capex_ttm = capex_ttm.unstack('stock_code').rolling(4, min_periods=3).sum()

    revenue_ttm = (income_df['revenue']
                   .unstack('stock_code')
                   .rolling(4, min_periods=3)
                   .sum())

    capex_ratio = capex_ttm / revenue_ttm.replace(0, np.nan)

    return (-capex_ratio).rename('capex_intensity_neg')
```

### 5.4 进阶版：低效扩张复合信号

单纯的资产增速因子可能错误惩罚了"高盈利能力支撑下的高质量扩张"。建议同时构建一个复合版本：

```python
def compute_inefficient_growth(
    asset_growth_df: pd.DataFrame,   # 已计算的资产增速因子（取反前原始值）
    roe_df:          pd.DataFrame,   # q_roe 因子
    quantile_high:   float = 0.75,
    quantile_low:    float = 0.25,
) -> pd.DataFrame:
    """
    低效扩张信号：高资产增速 + 低 ROE 的交叉条件

    逻辑：扩张且低回报 = 明确的资本浪费信号（更纯粹的负向信号）
    
    返回二元信号：
      −1 = 低效扩张（高增速 + 低ROE），强负向
       0 = 其他
      +1 = 高效成长（高增速 + 高ROE），正向
    """
    dates = asset_growth_df.index.intersection(roe_df.index)
    results = {}
    for t in dates:
        ag  = -asset_growth_df.loc[t]   # 恢复为原始正值增速（高增速 = 大）
        roe = roe_df.loc[t]
        common = ag.dropna().index.intersection(roe.dropna().index)

        ag_high  = ag.loc[common] > ag.loc[common].quantile(quantile_high)
        ag_low   = ag.loc[common] < ag.loc[common].quantile(1 - quantile_high)
        roe_high = roe.loc[common] > roe.loc[common].quantile(quantile_high)
        roe_low  = roe.loc[common] < roe.loc[common].quantile(quantile_low)

        signal = pd.Series(0, index=common)
        signal[ag_high & roe_low]  = -1  # 低效扩张
        signal[ag_high & roe_high] = +1  # 高效成长
        results[t] = signal

    return pd.DataFrame(results).T
```

### 5.5 数据来源与可行性

| 项目 | 详情 |
|-----|------|
| 数据接口 | Tushare Pro: `balancesheet_vip`，字段 `total_assets` |
| PIT 字段 | `ann_date` |
| 覆盖率预估 | ≥ 92%（资产负债表覆盖最全）|
| 金融行业处理 | 设为 NaN（预处理阶段屏蔽）|
| 开发工作量 | 0.5 天 |

**优先级：🔴 P0 — 立即构建**

---

## 六、空缺三：自由现金流收益率（FCF Yield）

### 6.1 当前状态

**当前覆盖**：`cfp` = 经营现金流/市值  
**空缺内容**：扣除维持性资本支出后的"真实"现金流收益率  
**关系**：FCF Yield 是 cfp 的升级版，信息集包含资本密集度信息

### 6.2 为什么是空缺

`cfp` = 经营现金流/市值，捕捉了"公司从主营业务赚了多少现金"。但它忽略了**维持业务所需的资本支出（Capex）**。

对于资本密集型行业（制造业、电力、钢铁），经营现金流可能很高，但同时需要持续的大规模资本支出才能维持现有产能。扣除 Capex 后的自由现金流（FCF）才是可以分配给股东的"真实"现金：

$$\text{FCF\_yield} = \frac{\text{经营活动现金流}_{TTM} - \text{资本支出}_{TTM}}{\text{总市值}}$$

`fcf_yield` 与 `cfp` 的相关性约 0.60–0.70，相关但不冗余——两者的差值正是资本密集度信息。

### 6.3 修复方案

```python
def compute_fcf_yield(
    cashflow_df: pd.DataFrame,
    market_cap_df: pd.DataFrame,  # 日度总市值数据
) -> pd.DataFrame:
    """
    自由现金流收益率（正向因子：值越高越好）

    FCF_yield = (经营现金流_TTM − 资本支出_TTM) / 总市值

    资本支出定义（取 Tushare 中对应字段）：
      c_pay_acq_const_fiolta：购建固定资产/无形资产/长期资产支付的现金
    """
    # TTM 经营现金流
    oper_cf_ttm = (cashflow_df['n_cashflow_act']
                   .unstack('stock_code')
                   .rolling(4, min_periods=3)
                   .sum())

    # TTM 资本支出（用绝对值，因为现金流量表中支出为正）
    capex_ttm = (cashflow_df['c_pay_acq_const_fiolta']
                 .unstack('stock_code')
                 .rolling(4, min_periods=3)
                 .sum()
                 .abs())

    # TTM FCF
    fcf_ttm = oper_cf_ttm - capex_ttm

    # 当月最后一个交易日总市值（与 cfp 的市值对齐）
    mktcap = market_cap_df  # (date × stock) 总市值，单位与财报一致

    fcf_yield = fcf_ttm / mktcap.replace(0, np.nan)

    # 极端值截尾：FCF 为极端负值时（重大资本支出年份）截尾到 −0.5
    fcf_yield = fcf_yield.clip(-0.5, 1.0)

    return fcf_yield.rename('fcf_yield')
```

**注意**：FCF Yield 为负值的公司不应简单过滤。负 FCF 可能意味着：
- 高成长型公司大规模投资未来（正当负 FCF）
- 陷入资本陷阱、经营不善（负向负 FCF）

建议对 FCF Yield 极端负值（< −0.2）的股票做额外分析，而非直接截尾处理。

**优先级：🟠 P1 — 本季度构建**

---

## 七、空缺四：投入资本回报率（ROIC）

### 7.1 当前状态

**当前覆盖**：`q_roe` 衡量净资产收益率  
**空缺内容**：剔除财务杠杆影响后，公司核心经营资本的回报效率  
**关键区别**：ROE 混淆了"经营能力"和"杠杆水平"；ROIC 只看前者

### 7.2 为什么是空缺

以一个极端例子说明 ROE 的局限：

| 公司 | EBIT | 净利润 | 净资产 | 有息负债 | ROE | ROIC |
|-----|-----|------|------|---------|-----|-----|
| A（低杠杆优质）| 1亿 | 8000万 | 10亿 | 2亿 | 8% | 8.3% |
| B（高杠杆劣质）| 1亿 | 8000万 | 2亿 | 10亿 | 40% | 8.3% |

ROE 显示 B 公司远优于 A，但两者的实际经营能力（ROIC）完全相同。B 的高 ROE 完全来自高杠杆，一旦利率上升或信用收紧，B 的 ROE 会快速恶化。

在中证500中，行业之间的资本结构差异极大（重资产制造业 vs 轻资产互联网），ROIC 的行业可比性远优于 ROE。

**ROIC 定义**：

$$\text{ROIC} = \frac{\text{NOPAT（税后净营业利润）}}{\text{投入资本（净债务 + 净资产）}}$$

其中：$\text{NOPAT} = \text{EBIT} \times (1 - \text{税率})$

### 7.3 修复方案

```python
def compute_roic(
    income_df:  pd.DataFrame,
    balance_df: pd.DataFrame,
    tax_rate_default: float = 0.25,  # A股标准企业所得税率
) -> pd.DataFrame:
    """
    投入资本回报率（正向因子）

    ROIC = NOPAT / Invested Capital
    NOPAT = EBIT × (1 - 有效税率)
    Invested Capital = 净资产 + 有息负债 (= 总资产 - 无息流动负债)
    """
    # ── EBIT（息税前利润）─────────────────────────────────────────────
    # EBIT = 营业利润 + 财务费用（利息支出部分）
    # Tushare income_vip 中：operate_profit（营业利润），fin_expenses（财务费用）
    ebit = (income_df['operate_profit'].unstack('stock_code') +
            income_df['fin_expenses'].unstack('stock_code').fillna(0))

    # TTM EBIT
    ebit_ttm = ebit.rolling(4, min_periods=3).sum()

    # ── 有效税率估计 ──────────────────────────────────────────────────
    # 用近4季度：所得税费用 / 税前利润，取平均
    tax_expense = income_df.get('income_tax', pd.Series()).unstack('stock_code')
    pretax_income = income_df.get('total_profit', pd.Series()).unstack('stock_code')

    effective_tax_rate = (
        (tax_expense.rolling(4, min_periods=2).sum() /
         pretax_income.rolling(4, min_periods=2).sum())
        .clip(0, 0.50)
        .fillna(tax_rate_default)   # 缺失时用默认税率
    )

    # ── NOPAT ────────────────────────────────────────────────────────
    nopat_ttm = ebit_ttm * (1 - effective_tax_rate)

    # ── 投入资本 ──────────────────────────────────────────────────────
    # 投入资本 = 净资产 + 有息负债
    # 有息负债 = 短期借款 + 一年内到期的长期负债 + 长期借款 + 应付债券
    equity     = balance_df['total_hldr_eqy_inc_min_int'].unstack('stock_code')
    short_debt = balance_df.get('short_term_loan', 0).unstack('stock_code') if \
                 'short_term_loan' in balance_df.columns else 0
    long_debt  = balance_df.get('long_term_loan', 0).unstack('stock_code') if \
                 'long_term_loan' in balance_df.columns else 0
    bonds_pay  = balance_df.get('bond_payable', 0).unstack('stock_code') if \
                 'bond_payable' in balance_df.columns else 0
    curr_ltd   = balance_df.get('non_cur_liab_due_1y', 0).unstack('stock_code') if \
                 'non_cur_liab_due_1y' in balance_df.columns else 0

    invested_capital = (equity + short_debt + long_debt + bonds_pay + curr_ltd)

    # 期初期末平均（平均投入资本）
    avg_ic = (invested_capital + invested_capital.shift(4)) / 2

    # ── ROIC ─────────────────────────────────────────────────────────
    roic = nopat_ttm / avg_ic.replace(0, np.nan)
    roic = roic.clip(-0.5, 1.0)

    return roic.rename('roic')
```

**优先级：🟡 P2 — 下季度构建**

---

## 八、空缺五：市净率倒数（Book-to-Price）

### 8.1 当前状态

**当前覆盖**：`ep_ttm`（PE 倒数）、`cfp`（现金流估值）  
**空缺内容**：基于资产负债表的估值视角（净资产/市值）  
**重要性**：Fama-French 三因子的核心构件之一，是学术实证最充分的因子

### 8.2 为什么是空缺

`ep_ttm` 是盈利基础的估值（分子是净利润），`bp` 是资产基础的估值（分子是净资产）。两者捕捉不同的"便宜"维度：

- `ep_ttm` 在利润较为稳定的行业有效
- `bp` 对**周期性行业**（利润波动大但资产稳定）、**金融行业**（资产是核心指标）更有效
- 两者相关性约 0.40–0.60，均有独立预测力

历史上，`bp`（Fama-French HML 因子的构建基础）在 A 股的实证研究中持续显著，且在你的因子池因验证期价值因子失效的背景下，`bp` 作为不同视角的估值补充尤为重要（`bp` 与 `ep_ttm` 在成长/科技行情中的行为略有不同）。

### 8.3 修复方案

```python
def compute_book_to_price(
    balance_df:    pd.DataFrame,
    market_cap_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    市净率倒数 BP（正向因子：BP越高 = 越便宜 = 预期收益越高）

    BP = 净资产（账面价值）/ 总市值
    """
    # 净资产 = 股东权益合计（含少数股东权益）
    book_value = balance_df['total_hldr_eqy_inc_min_int'].unstack('stock_code')

    # 市值对齐：使用调仓日收盘后市值
    mktcap = market_cap_df

    bp = book_value / mktcap.replace(0, np.nan)

    # ── 特殊处理 ─────────────────────────────────────────────────────
    # 净资产为负的股票（资不抵债）：BP 为负，信号方向混乱
    # 建议：净资产 < 0 的股票 BP 设为 NaN，预处理阶段过滤
    bp = bp.where(book_value > 0, np.nan)

    # 截尾：BP > 10 通常是数据异常或极端小盘股
    bp = bp.clip(0, 10)

    return bp.rename('bp')
```

**与 ep_ttm 的关系**：建议将两者同时保留，因为它们对不同行业的估值信号来源不同。不需要正交化，两者 0.40–0.60 的相关性在 Ridge 框架下可以自然处理。

**优先级：🟡 P2 — 下季度构建**

---

## 九、空缺六：盈利意外（SUE）

### 9.1 当前状态

**当前覆盖**：`analyst_eps_revision`（分析师事前调整方向）  
**空缺内容**：实际盈利 vs 市场预期的偏差（事后事实与预期的差距）  
**关系**：两个完全不同的信号——一个是"分析师提前调整"，一个是"调整后仍然不够"

### 9.2 为什么是空缺

**盈利公告后漂移（PEAD，Post-Earnings Announcement Drift）** 是金融学中最持久的市场异象之一，由 Ball & Brown（1968）首次记录，此后在全球多个市场均有确认：

$$\text{SUE}_{i,t} = \frac{\text{EPS}_{i,t}^{actual} - \overline{\text{EPS}}_{i,t}^{forecast}}{\sigma(\text{EPS forecast error})_{i}}$$

核心机制：**市场对好消息的反应不足**。在公告日之后，好消息（高 SUE）的股票持续跑赢，坏消息（低 SUE）的股票持续跑输，这个漂移可以持续 1–3 个季度。

与 `analyst_eps_revision` 的互补关系：

| | analyst_eps_revision | SUE |
|--|---------------------|-----|
| 时间节点 | 公告前（分析师预判调整）| 公告后（实际 vs 预期） |
| 信息来源 | 分析师预期的方向变化 | 分析师整体预期与现实的偏差 |
| 捕捉的市场行为 | 分析师信息优势 | 市场对信息的反应不足 |

两者相关性约 0.15–0.25，**实质上独立**。

### 9.3 修复方案

```python
def compute_sue(
    actual_eps_df:   pd.DataFrame,  # 实际公告 EPS（PIT，按公告日打标）
    consensus_df:    pd.DataFrame,  # 公告前最新分析师共识 EPS
    n_lag_periods:   int = 8,       # 用过去多少期预测误差计算标准差
) -> pd.DataFrame:
    """
    标准化未预期盈利（SUE）—— 正向因子（超预期越多，收益越高）

    SUE = (实际EPS − 共识预期EPS) / 历史预测误差标准差

    数据来源：
    - actual_eps_df : Wind/Tushare 实际 EPS（按公告日 PIT）
    - consensus_df  : Wind 一致预期 EPS（公告日前最近一次共识）

    注意：共识预期数据质量对 SUE 因子影响极大
         需要确保公告日前取预期（不能用公告后修正的预期）
    """
    # ── Step 1: 盈利意外（原始值）───────────────────────────────────
    surprise_raw = actual_eps_df - consensus_df

    # ── Step 2: 标准化（除以历史预测误差的标准差）──────────────────
    forecast_error_std = surprise_raw.rolling(
        window=n_lag_periods, min_periods=4
    ).std()

    sue = surprise_raw / forecast_error_std.replace(0, np.nan)

    # ── Step 3: 极端值处理 ─────────────────────────────────────────
    # SUE 超过 ±10 通常是分析师预期数据质量问题（如覆盖极少）
    sue = sue.clip(-10, 10)

    # ── Step 4: 覆盖率检查 ─────────────────────────────────────────
    # 分析师覆盖少于 2 名的股票，共识预期噪音大，建议设为 NaN
    # 需要外部传入 analyst_coverage_df 并在此过滤

    return sue.rename('sue')
```

**实施注意**：SUE 的最大难点是**数据质量**，需要确保：
1. 使用公告日之前的最新共识预期（Wind 的 `est_eps` 按最新日期取，公告日前截断）
2. 覆盖分析师不足 2 名的股票，共识 EPS 噪音极大，建议过滤
3. 实际 EPS 用 PIT 数据（公告日打标），不用报告期截止日

**数据来源**：Wind 金融终端（`consensusdata` 接口）或通联数据；Tushare 不提供一致预期数据

**优先级：🔴 P0（理论价值极高）— 但受制于 Wind 数据权限**

---

## 十、空缺七：分析师预期分散度（Forecast Dispersion）

### 10.1 当前状态

**当前覆盖**：`analyst_eps_revision`（EPS 预期调整方向，正向信号）  
**空缺内容**：不同分析师之间 EPS 预期的分歧程度（**负向**信号）

### 10.2 为什么是空缺

Miller（1977）的经典论文提出：当投资者意见分歧（预期分散度高）时，由于空头约束（A 股的限制更严格），乐观投资者推高价格，股票被高估。后续收益因此偏低。

$$\text{Dispersion} = \frac{\sigma(\text{各分析师EPS预测}_{i,t})}{\left|\bar{\text{EPS}}_{i,t}\right|}$$

这是**负向因子**：预期分散度越高 → 不确定性溢价 / 乐观偏差定价 → 未来超额收益越低。

与 `analyst_eps_revision` 的互补性极强：
- `analyst_eps_revision` 回答"预期是否在变好"（方向信号）
- `dispersion` 回答"这份预期有多可信"（置信度信号）

两者相关性约 −0.10 至 −0.20，基本独立。

### 10.3 修复方案

```python
def compute_analyst_dispersion(
    analyst_forecast_df: pd.DataFrame,  # 各分析师个体 EPS 预测明细
    min_analysts: int = 3,              # 最少分析师数量（少于此则 NaN）
) -> pd.DataFrame:
    """
    分析师预期分散度（负向因子：取反后为正向）

    Dispersion = std(各分析师EPS预测) / |mean(EPS预测)|

    负向因子：分歧越大 → 不确定性越高 → 预期收益越低
    取反后：一致度越高 → 因子值越高 → 预期收益越高
    """
    # 按股票、调仓日分组，计算当前有效预测的离散度
    def monthly_dispersion(group):
        if len(group) < min_analysts:
            return np.nan
        eps_preds = group['eps_forecast'].dropna()
        if eps_preds.std() == 0 or abs(eps_preds.mean()) < 1e-6:
            return np.nan
        return eps_preds.std() / abs(eps_preds.mean())

    dispersion = (analyst_forecast_df
                  .groupby(['date', 'stock_code'])
                  .apply(monthly_dispersion)
                  .unstack('stock_code'))

    # 取反：高分散度 → 负向 → 取反后为正向因子
    return (-dispersion).rename('analyst_agreement')
```

**优先级：🟡 P2 — 数据来源同 SUE，需 Wind**

---

## 十一、空缺八：股本稀释因子（Share Issuance）

### 11.1 当前状态

**当前覆盖**：无任何公司股本行为信号  
**空缺内容**：公司是否在增发股票（负向信号）或回购股票（正向信号）

### 11.2 为什么是空缺

管理层对自家公司价值的看法，最终体现在他们的**真实资金行为**上，而非公告中。

**股本扩张（增发、配股）**：
- 在股价高估时，管理层更倾向于向外融资
- 大量新股涌入市场，供给增加压低价格
- 学术证据（Loughran & Ritter, 1995）：增发后 5 年内股票显著跑输

**股本收缩（回购）**：
- 管理层认为股价低估时才回购（信号理论）
- 每股盈利提升，价值重估

A 股特殊背景：
- **再融资高发**：创业板、科创板上市公司的定向增发极为频繁
- **回购注销增加**：2018 年后 A 股回购制度完善，回购信号质量提升
- **限售解禁**：大股东减持形成供给压力（特殊情境下的股本信号）

### 11.3 修复方案

```python
def compute_share_issuance(
    share_df: pd.DataFrame,  # 总股本历史数据（PIT）
    cap_op_df: pd.DataFrame = None,  # 可选：资本运作公告数据
) -> pd.DataFrame:
    """
    股本变化因子（正向因子：股本减少=正向，股本增加=负向，取反后正向）

    ShareIssue_neg = −(总股本_t / 总股本_{t-4} − 1)

    取反后，股本减少（回购）得到正值，股本增加（增发）得到负值
    """
    total_shares = share_df['total_share'].unstack('stock_code')

    # 年化股本变化率（同比，对应季度数据用 shift(4)）
    share_change = total_shares.pct_change(periods=4)

    # 取反（股本减少=正向信号）
    share_issuance_neg = -share_change

    # 截尾：±1（100% 以上的增发/回购属于极端重组事件）
    share_issuance_neg = share_issuance_neg.clip(-1, 1)

    return share_issuance_neg.rename('share_issuance_neg')


def compute_buyback_indicator(
    repurchase_df: pd.DataFrame,     # 回购公告数据
    market_cap_df: pd.DataFrame,
    window_days: int = 90,
) -> pd.DataFrame:
    """
    回购规模占市值比（精细化回购信号，优先级低于 share_issuance_neg）

    需要 Tushare repurchase 接口数据
    """
    # 过去 window_days 内的回购金额 / 当前总市值
    repurchase_amount = (repurchase_df['amount']
                         .rolling(window=window_days)
                         .sum()
                         .unstack('stock_code'))

    buyback_ratio = repurchase_amount / market_cap_df.replace(0, np.nan)

    return buyback_ratio.fillna(0).rename('buyback_ratio')
```

### 11.4 数据来源

| 项目 | 详情 |
|-----|------|
| 数据接口 | Tushare Pro: `daily_basic` 中 `total_share`；或 `equity` 接口 |
| PIT 处理 | 总股本变化一般在公告日后生效，需按公告日打标 |
| 覆盖率预估 | ≥ 95% |
| 开发工作量 | 0.5 天 |

**优先级：🟠 P1 — 本季度构建**

---

## 十二、空缺九：融资净买入因子（Margin Financing）

### 12.1 当前状态

**当前覆盖**：北向资金（外资）的持仓占比和变化  
**空缺内容**：A 股本土杠杆资金（融资融券）的流向信号  
**重要性**：中证500成分股的融资融券标的覆盖率 > 95%，这是完全可用的数据

### 12.2 为什么是空缺

你的因子池对"资金流向"的覆盖是**单一外资视角**（hk_hold_ratio + hk_hold_chg）。但中证500的日常定价更多由 A 股本土机构和散户主导，其中融资买入是**散户/激进投资者的杠杆多头行为**，是一个非常有特色的本土信号。

融资融券信号有两种截然不同的预测逻辑：

**短期融资净买入（流量，正向动量信号）**：
- 近期资金加速涌入 → 短期价格支撑
- 反映市场对该股的积极情绪

**高融资余额（存量，反向拥挤度信号）**：
- 长期高融资余额 → 多头仓位过度拥挤
- 去杠杆风险：一旦下跌，强制平仓触发踩踏
- 这与 `turn_20d` 的逻辑互补：高换手可以是散户追捧，而高融资余额是杠杆追捧

### 12.3 修复方案

```python
def compute_margin_signals(
    margin_df:    pd.DataFrame,  # 融资融券日度数据
    float_cap_df: pd.DataFrame,  # 自由流通市值
    window_flow:  int = 20,      # 融资净买入流量窗口（交易日）
    window_stock: int = 60,      # 融资余额存量窗口（交易日）
) -> pd.DataFrame:
    """
    融资融券双信号：
    1. mf_flow_ratio   : 近期融资净买入/流通市值（正向动量信号）
    2. mf_stock_neg    : 高融资余额/流通市值取反（负向拥挤度信号）

    Tushare 接口：pro.margin_detail(ts_code=...)
    字段：
      rzye    : 融资余额（元）
      rzmre   : 融资买入额（元）
      rqye    : 融券余额（元，备用）
    """
    rzye  = margin_df['rzye'].unstack('stock_code')    # 融资余额
    rzmre = margin_df['rzmre'].unstack('stock_code')   # 融资买入额（日度）
    rzsye = margin_df.get('rzsye', rzye * 0).unstack('stock_code')  # 融资卖出额

    float_cap = float_cap_df

    # ── 信号1：短期融资净买入流量比（正向动量信号）─────────────────
    mf_net_buy    = rzmre - rzsye    # 净买入 = 买入 - 卖出
    mf_net_20d    = mf_net_buy.rolling(window=window_flow, min_periods=10).sum()
    mf_flow_ratio = mf_net_20d / float_cap.replace(0, np.nan)
    mf_flow_ratio.name = 'mf_flow_ratio_20d'

    # ── 信号2：融资余额拥挤度（负向拥挤度信号，取反后正向）─────────
    mf_balance_avg = rzye.rolling(window=window_stock, min_periods=30).mean()
    mf_balance_pct = mf_balance_avg / float_cap.replace(0, np.nan)
    mf_stock_neg   = -mf_balance_pct   # 取反：高余额 = 拥挤 = 负向 → 取反为正向
    mf_stock_neg.name = 'mf_congestion_neg_60d'

    return pd.concat([mf_flow_ratio, mf_stock_neg], axis=1)


# ── 重要说明 ──────────────────────────────────────────────────────────────
# 两个信号方向截然不同，不建议直接合并为单一因子
# 建议在四道门检验中分别测试，选择 IC_IR 更高的一个入池
# 预期：
#   mf_flow_ratio (正向动量) → IC_IR 约 +0.25~+0.35，但衰减快
#   mf_stock_neg  (负向拥挤) → IC_IR 约 +0.20~+0.30，较稳定
```

### 12.4 数据来源与可行性

| 项目 | 详情 |
|-----|------|
| 数据接口 | Tushare Pro: `margin_detail`，字段 rzye/rzmre/rzsye/rqye |
| 历史数据 | 上交所从 2010 年起，深交所从 2010 年底，覆盖你的 2012 训练期起点 |
| 频率对齐 | 日度数据，月末汇总时取当月累计流量 + 月末存量 |
| 覆盖率预估 | 中证500成分股覆盖率 > 95%（两融标的覆盖全） |
| 开发工作量 | 1 天 |

**优先级：🟠 P1 — 本季度构建**

---

## 十三、空缺十：价格动量因子（Price Momentum 12-1m）

### 13.1 当前状态

**当前覆盖**：`high_52w_v2`（候补，52 周价格位置）  
**空缺内容**：标准 Jegadeesh-Titman 12-1m 累积收益动量  
**关键区别**：`high_52w_v2` 是"价格与历史高点的距离"，动量是"过去 12 个月的实际累积收益"

### 13.2 为什么是空缺

`high_52w_v2` 与标准动量的相关性约 0.55–0.65，是不同的价格信号。两者在失效时间上可能不同步。

**标准动量** = 过去 12 个月累积对数收益，**跳过最近 1 个月**（skip-1m，剔除短期反转污染）：

$$\text{MOM}_{12-1} = \sum_{k=2}^{12} \ln\left(\frac{P_{t-k+1}}{P_{t-k}}\right)$$

**A 股动量的特殊风险**：

> ⚠️ A 股动量存在显著的"动量崩溃"风险（Momentum Crash），在市场急速反转时（如 2015 年 6 月、2022 年 11 月），持有过去强势股会产生极端单期损失。你的验证期（2021-2022）恰好是动量因子的低谷期，这是 `high_52w_v2` 在 IC_IR 加权流水线中失败的根本原因。

### 13.3 修复方案

```python
def compute_price_momentum(
    adj_close_df: pd.DataFrame,     # 月末后复权收盘价
    lookback_months: int = 12,
    skip_months:     int = 1,
) -> pd.DataFrame:
    """
    标准 Jegadeesh-Titman 价格动量（12-1m）

    MOM_12-1 = 对数累积收益 [t-12 月, t-2 月]
    跳过最近 1 个月（skip-1m），剔除短期反转干扰

    方向：正向（过去表现好的股票未来倾向于继续表现好）
    A 股注意：动量效应在 A 股不稳定，建议仅在 Ridge 流水线测试，
              不适合直接用 IC_IR 加权纳入（会获得过高权重）
    """
    log_ret = np.log(adj_close_df / adj_close_df.shift(1))  # 月度对数收益

    # 滚动累积过去 lookback_months 个月的收益，然后减去最近 skip_months
    cum_lookback = log_ret.rolling(window=lookback_months, min_periods=int(lookback_months * 0.8)).sum()
    cum_skip     = log_ret.rolling(window=skip_months,     min_periods=skip_months).sum()

    # skip-1m 动量：shift(skip) 确保使用的是 t-1 月之前的数据
    momentum = (cum_lookback - cum_skip).shift(skip_months)

    return momentum.rename('mom_12_1m')


def compute_momentum_with_crash_control(
    adj_close_df:  pd.DataFrame,
    market_ret_df: pd.Series,     # 市场指数月度收益率
    vol_threshold: float = 0.06,  # 市场月度波动率阈值（超过则降权动量）
) -> pd.DataFrame:
    """
    带动量崩溃控制的动量因子

    当市场波动率上升（动量崩溃风险增加时），
    自动降低动量因子的有效权重

    实现方式：
    - 计算动量原始信号
    - 根据近期市场波动率调整缩放系数
    - 高波动环境下因子值向 0 压缩
    """
    raw_mom = compute_price_momentum(adj_close_df)

    # 近 3 个月市场波动率
    market_vol = market_ret_df.rolling(window=3).std()

    # 缩放系数：波动率越高，动量信号越被压缩
    scale = (vol_threshold / market_vol.clip(lower=vol_threshold)).clip(0, 1)

    # 将缩放系数广播到所有股票
    scaled_mom = raw_mom.multiply(scale, axis=0)

    return scaled_mom.rename('mom_12_1m_controlled')
```

**入池建议**：**仅在 Ridge 流水线中测试**，不适合 IC_IR 加权流水线（会获得与验证期表现不相符的过高权重）。建议与 `high_52w_v2` 分别测试，保留表现更好的一个（或两者均保留，相关性 0.55–0.65 在 Ridge 框架下可以共存）。

**优先级：🟢 P3 — 在 Ridge 专用，半年内测试**

---

## 十四、空缺十一：短期价格反转（Short-term Reversal）

### 14.1 当前状态

**当前覆盖**：无任何短期反转信号  
**空缺内容**：1 个月内价格过度波动后的均值回归

### 14.2 为什么是空缺

短期反转（De Bondt & Thaler, 1985；Jegadeesh, 1990）是与中期动量方向相反的现象：

**上个月涨幅最大的股票，下个月倾向于下跌；上个月跌幅最大的，倾向于反弹。**

机制：流动性提供者的库存管理、过度交易后的均值回归。

**A 股特殊性**：由于散户主导，A 股的短期反转效应比美股更强且更显著（追涨杀跌的散户在月度频率下是强烈的反向信号）。

与动量的关系：这正是动量构建中需要 skip-1m 的原因。但作为独立因子，它提供了与动量互补的信息。

### 14.3 修复方案

```python
def compute_short_term_reversal(
    adj_close_df: pd.DataFrame,
    lookback_months: int = 1,
) -> pd.DataFrame:
    """
    短期价格反转因子（正向因子：上期下跌 → 正值 → 预期本期反弹）

    Reversal = −月度对数收益率（取反）

    方向：正向（上月跌的股票，本月倾向于涨）
    
    注意：
    1. 与 turn_20d 的相关性约 0.3~0.5（高换手的股票反转效应更强）
    2. 与 ivol_60d 的相关性约 0.4~0.6（高特质波动的股票反转效应更强）
    3. 在月度调仓中会与 turn_20d 存在一定信息重叠，需通过 Gate 1 验证独立性
    """
    log_ret = np.log(adj_close_df / adj_close_df.shift(1))

    # 上个月的收益率，取反后为反转因子
    reversal = -log_ret.shift(1)   # shift(1) 确保用上期收益预测本期

    return reversal.rename('reversal_1m')
```

**与现有因子的相关性**：短期反转与 `ivol_60d`（r ≈ 0.40–0.55）和 `turn_20d`（r ≈ 0.30–0.45）有中等相关，因为高波动、高换手的股票反转效应更强。**在 Ridge 框架下这个相关性可以自动处理**，但建议在入池前先检验与两者的截面相关性，若超过 0.6，考虑取残差后再测试。

**优先级：🟢 P3 — 半年内测试**

---

## 十五、空缺十二：大股东行为因子（Insider Behavior）

### 15.1 当前状态

**当前覆盖**：`holder_chg`（股东数量变化，间接反映机构集中度变化）  
**空缺内容**：控股股东 / 高管的直接增减持行为

### 15.2 为什么是空缺

大股东/高管的增减持是**内部人对自家公司价值的"用钱投票"**，信息含量理论上极高：

- **大股东增持**：认为股价低估，强烈正向信号
- **大股东减持**：可能认为股价高估，或资金需求，偏负向信号（但噪音大）
- **高管期权行权后减持**：性质特殊，需要过滤

### 15.3 修复方案

```python
def compute_insider_net_buying(
    disclosure_df: pd.DataFrame,  # 大股东/高管增减持公告数据
    market_cap_df: pd.DataFrame,
    window_days:   int = 90,       # 统计窗口（交易日）
    min_ratio:     float = 0.001,  # 最小占流通市值比例（过滤微小交易）
) -> pd.DataFrame:
    """
    大股东净增持因子（正向因子：净增持占市值比越高越好）

    Tushare 接口：
    - pro.stk_holdertrade(ts_code=...): 大股东增减持
      字段：in_de（增持/减持），change_vol（变动股数），avg_price

    数据质量注意事项：
    1. 必须过滤"被动减持"（如质押强平、大宗交易）
    2. 限售解禁后减持与主动减持需要区分
    3. 覆盖率问题：仅有持股 5% 以上的股东须强制披露，
       5% 以下的高管/董事减持也须披露，但可能存在延迟
    """
    # 增持和减持分别统计
    buys  = disclosure_df[disclosure_df['in_de'] == '增持']
    sells = disclosure_df[disclosure_df['in_de'] == '减持']

    def net_volume(df, stock_code, as_of_date):
        mask = (
            (df['ts_code'] == stock_code) &
            (df['ann_date'] > (as_of_date - pd.Timedelta(days=window_days))) &
            (df['ann_date'] <= as_of_date)
        )
        return df.loc[mask, 'change_vol'].sum()

    # 注意：这个实现仅为示意，实际需要向量化处理提升速度
    # 建议预先按股票分组，构建滚动窗口累加的 pivot 表

    return pd.DataFrame()  # 占位，实际实现需向量化


# ── 数据质量评估（入池前必做）──────────────────────────────────────────
# 1. 运行 Gate 2（lead_ratio 检验）：
#    增减持公告可能在行动后有延迟，需要检验是否存在前视偏差
# 2. 分类过滤：
#    被动减持（质押强平）≠ 主动减持（看空），需要通过交易类型字段过滤
# 3. 覆盖率检验：
#    Gate 0 要求覆盖率 ≥ 80%；仅 5% 以上股东须披露，
#    覆盖率可能不足，需要实测
```

> ⚠️ **数据质量警告**：大股东行为数据在 A 股存在以下系统性噪音：
> 1. **被动减持**（质押平仓、司法拍卖）与主动减持混在一起，需要类型过滤
> 2. **信息延迟**：部分减持在实施前 3 个交易日预披露，时序处理复杂
> 3. **覆盖率可能不足**：Gate 0（覆盖率 ≥ 80%）可能无法通过，需实测
>
> 建议在构建后先运行 Gate 0 + Gate 2，若覆盖率或 lead_ratio 不达标，暂不入池。

**优先级：🟢 P3 — 半年内，数据质量需先评估**

---

## 十六、明确不建议纳入的因子

以下因子表面上看合理，但对你的策略框架有害，明确说明原因：

| 因子 | 拒绝原因 |
|-----|---------|
| **市场 Beta** | 与 `ivol_60d` 高度相关（r ≈ 0.65–0.75），提供极少增量信息；月度中性化后 Beta 信号很弱 |
| **新闻情绪 NLP** | 月度调仓频率下情绪信号衰减太快（半衰期约 2–5 天）；A 股新闻 NLP 数据质量差 |
| **RSI / MACD 等技术指标** | 缺乏经济机制，与动量/反转高度重叠，纯粹是信息集的子集 |
| **行业景气度指数** | 与行业中性化冲突——你的框架已做行业中性化，行业层面信号会被抵消 |
| **融券余额** | A 股融券数量极少（相对美股），规模太小，噪音信噪比低；覆盖率不足 |
| **EV/EBITDA** | 与 `ep_ttm` + `cfp` 的信息集高度重叠（相关性 > 0.70），在 Ridge 框架下会争抢权重而不提供增量信息 |
| **管理层措辞分析** | 大量 NLP 工程工作，信号质量不稳定，ROI 极低 |
| **股价绝对水平（低价股效应）** | A 股的低价股效应主要是市值效应的影射，中性化市值后该效应消失 |

---

## 十七、综合优先级排序与实施路线图

### 17.1 优先级综合评分矩阵

对每个空缺因子，从五个维度评分（1–5分），总分越高优先级越高：

| 因子 | 学术证据 | A股适用性 | 独立信息量 | 数据可得性 | 开发难度 | **综合** |
|-----|:------:|:-------:|:--------:|:--------:|:------:|:------:|
| 应计项目（accruals）| 5 | 5 | 4 | 5 | 5 | **24** |
| 资产增速（asset_growth）| 5 | 4 | 5 | 5 | 5 | **24** |
| 盈利意外（SUE）| 5 | 4 | 5 | 3 | 3 | **20** |
| 融资净买入（margin_flow）| 3 | 5 | 4 | 4 | 4 | **20** |
| FCF 收益率（fcf_yield）| 4 | 4 | 3 | 5 | 4 | **20** |
| 股本稀释（share_issuance）| 4 | 4 | 4 | 5 | 5 | **22** |
| ROIC | 4 | 4 | 3 | 4 | 3 | **18** |
| 市净率倒数（BP）| 5 | 4 | 3 | 5 | 5 | **22** |
| 分析师预期分散度 | 4 | 4 | 4 | 3 | 3 | **18** |
| 价格动量（12-1m）| 5 | 3 | 3 | 5 | 4 | **20** |
| 短期反转（reversal）| 4 | 4 | 3 | 5 | 5 | **21** |
| 大股东增减持 | 3 | 4 | 4 | 3 | 2 | **16** |

### 17.2 按季度实施路线图

#### 立即行动（本月内）

这三个因子完全基于你已有的财务报表数据管道，只需增加计算逻辑：

```
本月工作（3–5个工作日）：
├── 构建 accrual_quality（0.5天代码 + 1天测试）
├── 构建 asset_growth_neg（0.5天代码 + 0.5天测试）
└── 构建 bp（0.3天代码 + 0.5天测试）

上述三个因子全部提交四道门检验
目标：在下次月度调仓前完成入池评估
```

#### 第一季度工作（本季度内）

```
Q1 工作（约 2–3 周）：
├── 构建 fcf_yield（1天）
├── 构建 share_issuance_neg（0.5天）
├── 构建 margin_flow 双信号（1天 + 数据管道接入）
└── 对以上所有因子运行四道门检验，提交合格者入池候选

同期进行（不影响上线时间线）：
└── 评估 Wind 数据权限：
    确认是否有 consensus EPS 数据 → 决定 SUE 和 analyst_dispersion 的可行性
```

#### 第二季度工作

```
Q2 工作：
├── 构建 ROIC（需仔细处理 NOPAT 计算，约 2 天）
├── 构建 analyst_dispersion（如 Wind 数据可用）
├── 构建 SUE（如 Wind 数据可用）
└── 对 price_momentum 和 reversal 在 Ridge 流水线中独立测试
    （不通过 IC_IR 加权流水线，直接进 Ridge 测试集）
```

#### 半年内

```
H2 工作：
├── 构建 insider_net_buying（大股东增减持）
│   先评估数据质量和覆盖率，再决定是否提交四道门
├── 根据 Q1/Q2 入池结果，重新评估全因子池相关性矩阵
└── 对新入池因子运行"组合增量测试"：
    新因子池 vs 旧因子池 的 rolling_48m Ridge 性能对比
    （在训练期+验证期内，不触碰测试集）
```

### 17.3 入池后的预期改进

基于学术文献和 A 股实证研究的保守估计：

| 阶段 | 新增因子 | 预期 IR 提升 | 预期超额提升 |
|-----|---------|----------:|----------:|
| 立即 (P0) | accruals + asset_growth + bp | +0.10–0.20 | +0.5–1.0% |
| Q1 (P1) | fcf_yield + share_issuance + margin_flow | +0.15–0.25 | +0.8–1.5% |
| Q2 (P2) | ROIC + SUE + analyst_dispersion | +0.10–0.20 | +0.5–1.0% |

> ⚠️ **注意**：上述预期改进是乐观估计，基于以下假设：
> 1. 新因子通过四道门（不保证）
> 2. 未来市场风格与验证期类似
> 3. Ridge 流水线能合理分配因子权重
>
> **实际改进幅度必须以样本外验证为准，不能以验证期结果直接估计**

---

## 十八、附录：标准入池检验流程模板

每个新候选因子提交入池前，必须完整执行以下检验清单：

```python
class FactorOnboardingChecklist:
    """
    新因子入池标准检验流程（四道门 + 附加检验）
    
    使用方式：
        checklist = FactorOnboardingChecklist(factor_name='accrual_quality')
        checklist.run_all(factor_df, ret_df, industry_df, existing_factors_df)
    """

    def __init__(self, factor_name: str):
        self.factor_name = factor_name
        self.results = {}

    # ── Gate 0：数据覆盖率检验 ──────────────────────────────────────
    def gate0_coverage(self, factor_df: pd.DataFrame,
                       universe_df: pd.DataFrame,
                       threshold: float = 0.80) -> bool:
        """
        每期有效股票数 / 宇宙总股票数 ≥ threshold
        """
        coverage_ratio = (factor_df.notna().sum(axis=1) /
                          universe_df.notna().sum(axis=1))
        avg_coverage = coverage_ratio.mean()
        passed = avg_coverage >= threshold
        self.results['gate0'] = {
            'avg_coverage': round(avg_coverage, 3),
            'passed': passed,
            'threshold': threshold,
        }
        print(f"  Gate 0 覆盖率: {avg_coverage:.1%} ({'✅ 通过' if passed else '❌ 未通过'})")
        return passed

    # ── Gate 1：IC 统计显著性 ────────────────────────────────────────
    def gate1_ic_significance(self, factor_df: pd.DataFrame,
                               ret_df: pd.DataFrame,
                               ic_ir_threshold: float = 0.30,
                               t_stat_threshold: float = 2.0) -> bool:
        """
        训练期 IC_IR ≥ 0.30 且 IC 的 t 统计量 ≥ 2.0
        """
        ic_list = []
        dates = factor_df.index.intersection(ret_df.index)
        for t in dates:
            f = factor_df.loc[t].dropna()
            r = ret_df.loc[t].reindex(f.index).dropna()
            common = f.index.intersection(r.index)
            if len(common) >= 50:
                ic_list.append(f.loc[common].corr(r.loc[common], method='spearman'))

        ic_arr = np.array(ic_list)
        ic_mean = ic_arr.mean()
        ic_std  = ic_arr.std()
        ic_ir   = ic_mean / ic_std if ic_std > 0 else 0
        t_stat  = ic_mean / (ic_std / np.sqrt(len(ic_arr))) if ic_std > 0 else 0

        passed = abs(ic_ir) >= ic_ir_threshold and abs(t_stat) >= t_stat_threshold
        self.results['gate1'] = {
            'ic_mean': round(ic_mean, 4),
            'ic_ir':   round(ic_ir, 3),
            't_stat':  round(t_stat, 3),
            'passed':  passed,
        }
        print(f"  Gate 1 IC_IR: {ic_ir:.3f}, t-stat: {t_stat:.3f} "
              f"({'✅ 通过' if passed else '❌ 未通过'})")
        return passed

    # ── Gate 2：时间错位检验（前视偏差检测）──────────────────────────
    def gate2_lead_ratio(self, factor_df: pd.DataFrame,
                          ret_df: pd.DataFrame,
                          threshold: float = 1.5) -> bool:
        """
        future_ic / current_ic < threshold
        lead_ratio 接近 1 说明因子用了未来数据
        """
        def mean_abs_ic(lag):
            ic_list = []
            dates = factor_df.index
            for i, t in enumerate(dates[:-lag]):
                future_t = dates[i + lag]
                f = factor_df.loc[t].dropna()
                r = ret_df.loc[future_t].reindex(f.index).dropna()
                common = f.index.intersection(r.index)
                if len(common) >= 50:
                    ic_list.append(abs(f.loc[common].corr(r.loc[common], method='spearman')))
            return np.mean(ic_list) if ic_list else 0

        ic_current  = mean_abs_ic(1)   # 预测下期
        ic_lead     = mean_abs_ic(0)   # 预测当期（如果高，说明用了未来数据）

        lead_ratio = ic_lead / ic_current if ic_current > 0 else np.inf
        passed = lead_ratio < threshold

        self.results['gate2'] = {
            'ic_current':  round(ic_current, 4),
            'ic_lead':     round(ic_lead, 4),
            'lead_ratio':  round(lead_ratio, 3),
            'passed':      passed,
        }
        print(f"  Gate 2 lead_ratio: {lead_ratio:.3f} ({'✅ 通过' if passed else '❌ 未通过 — 前视偏差风险！'})")
        return passed

    # ── Gate 3：验证期稳定性 ─────────────────────────────────────────
    def gate3_validation_stability(self,
                                    train_ic_ir: float,
                                    valid_ic_ir: float,
                                    ic_ir_threshold: float = 0.20) -> bool:
        """
        验证期 IC_IR ≥ 0.20 且方向与训练期一致
        """
        direction_ok = (train_ic_ir * valid_ic_ir) > 0
        passed = abs(valid_ic_ir) >= ic_ir_threshold and direction_ok

        self.results['gate3'] = {
            'train_ic_ir': round(train_ic_ir, 3),
            'valid_ic_ir': round(valid_ic_ir, 3),
            'direction_ok': direction_ok,
            'passed': passed,
        }
        print(f"  Gate 3 验证期 IC_IR: {valid_ic_ir:.3f} (训练期: {train_ic_ir:.3f}) "
              f"({'✅ 通过' if passed else '❌ 未通过'})")
        return passed

    # ── 附加检验一：与现有因子相关性 ─────────────────────────────────
    def addon_correlation_check(self,
                                 new_factor_df: pd.DataFrame,
                                 existing_factors_df: pd.DataFrame,
                                 high_corr_threshold: float = 0.70) -> dict:
        """
        检验新因子与所有现有因子的截面平均相关性
        若与某因子相关性 > 0.70，建议考虑取残差正交化
        """
        corr_results = {}
        for fname in existing_factors_df.columns:
            corr_list = []
            dates = new_factor_df.index.intersection(existing_factors_df.index)
            for t in dates:
                new_f   = new_factor_df.loc[t].dropna()
                exist_f = existing_factors_df[fname].loc[t].reindex(new_f.index).dropna()
                common  = new_f.index.intersection(exist_f.index)
                if len(common) >= 50:
                    corr_list.append(new_f.loc[common].corr(exist_f.loc[common]))
            avg_corr = np.mean(corr_list) if corr_list else np.nan
            corr_results[fname] = round(avg_corr, 3)
            if abs(avg_corr) > high_corr_threshold:
                print(f"  ⚠️  高相关警告：与 {fname} 的平均截面相关性 = {avg_corr:.3f}，"
                      f"考虑正交化处理")

        self.results['correlation'] = corr_results
        return corr_results

    # ── 附加检验二：行业分布均衡性 ───────────────────────────────────
    def addon_industry_distribution(self,
                                     factor_df: pd.DataFrame,
                                     industry_df: pd.DataFrame) -> None:
        """
        检验因子值是否在某个行业高度集中（行业偏差风险）
        若某行业的平均因子分位数 > 0.75 或 < 0.25，可能存在行业偏差
        """
        print(f"  行业分布检验：（详见完整报告）")
        # 此处省略实现，实际运行时输出每个行业的平均分位数

    def run_all(self, factor_df, ret_df, industry_df,
                existing_factors_df, train_period, valid_period):
        """
        运行完整入池检验流程
        """
        print(f"\n{'='*60}")
        print(f"  因子入池检验：{self.factor_name}")
        print(f"{'='*60}")

        # 分别在训练期和验证期计算 IC_IR（Gate 3 需要）
        # 此处简化，实际实现需按期间切片
        g0 = self.gate0_coverage(factor_df, ret_df)
        g1 = self.gate1_ic_significance(
            factor_df.loc[train_period[0]:train_period[1]], ret_df)
        g2 = self.gate2_lead_ratio(
            factor_df.loc[train_period[0]:train_period[1]], ret_df)

        # Gate 3 需要分别计算训练期/验证期 IC_IR（假设已计算）
        # g3 = self.gate3_validation_stability(train_ic_ir, valid_ic_ir)

        self.addon_correlation_check(factor_df, existing_factors_df)
        self.addon_industry_distribution(factor_df, industry_df)

        all_passed = g0 and g1 and g2  # and g3
        status = "🟢 建议入池" if all_passed else "🔴 未通过，暂不入池"
        print(f"\n  最终结论：{status}")
        print(f"{'='*60}\n")

        return all_passed, self.results
```

---

## 附录 B：数据接口汇总

| 因子 | Tushare 接口 | 关键字段 | 是否需要 Wind |
|-----|------------|---------|:----------:|
| accrual_quality | cashflow_vip, income_vip, balancesheet_vip | n_cashflow_act, n_income, total_assets | 否 |
| asset_growth | balancesheet_vip | total_assets | 否 |
| fcf_yield | cashflow_vip, daily_basic | n_cashflow_act, c_pay_acq_const_fiolta, total_mv | 否 |
| share_issuance | equity 或 daily_basic | total_share | 否 |
| bp | balancesheet_vip, daily_basic | total_hldr_eqy_inc_min_int, total_mv | 否 |
| margin_flow | margin_detail | rzye, rzmre, rzsye | 否 |
| ROIC | income_vip, balancesheet_vip | operate_profit, fin_expenses | 否 |
| SUE | — | 分析师共识 EPS | **是** |
| analyst_dispersion | — | 各分析师个体 EPS 预测 | **是** |
| price_momentum | daily（adj_close）| close, adj_factor | 否 |
| short_term_reversal | daily（adj_close）| close, adj_factor | 否 |
| insider_behavior | stk_holdertrade | in_de, change_vol, avg_price | 否 |

---

*本文档基于 2026-05-28 时点的因子池状态编写。所有分析均在训练期（2012-2020）和验证期（2021-2022）范围内进行，严格遵守测试集保护原则（2023-2025，剩余 3 次机会）。新因子的实际性能以入池测试结果为准，本文档中的预期 IC_IR 和改进幅度均为基于学术文献的先验估计。*
