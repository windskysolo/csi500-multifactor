# 05 — 财务因子构建 `src/factors/financial_factors.py`

---

## 一、文件定位

```
流程位置：第 3 步（构建因子面板）的核心计算模块之一
读取：通过 loader 的 get_financial_pit_raw() / get_indicator_pit_raw() / load_daily_basic()
写入：无（每个函数返回 Series，由 build_factor_panels.py 汇总后写盘）
被谁调用：scripts/build_factor_panels.py
调用谁：src/data/loader.py、src/data/pit_loader.py
```

本模块共实现 **15 个财务因子**（价值 6 个 + 质量 5 个 + 成长 4 个）。所有函数都只返回"原始值"，不做去极值/标准化/中性化——这些由 `preprocess.py` 负责。

**全局返回约定：**
- 每个函数返回以 `ts_code` 为 index 的 `pd.Series`
- NaN 表示数据缺失，整个流水线不做填充（填充在优化器入口统一处理）
- 数值单位以 docstring 中的"无量纲比率"、"百分比"等标注为准

---

## 二、模块顶部

```python
from src.data.loader import get_financial_pit_raw, get_indicator_pit_raw, load_daily_basic
from src.data.pit_loader import get_pit_latest, get_roe_delta, make_ttm

_WAN_TO_YUAN: float = 10_000.0   # 万元 → 元
```

`get_financial_pit_raw()`：返回完整的 financial_pit 历史表（reset_index 后），用于 TTM 计算（需要多期数据）。

`get_pit_latest()`：从完整历史表中取最新 PIT 快照（用于存量指标，如总资产、净资产）。

`make_ttm()`：把累计季报数据转换为 TTM（过去 12 个月合计），用于流量指标（净利润、营收等）。

---

## 三、内部辅助函数

### `_get_total_mv_yuan(rebalance_date, codes)` — 总市值（元）（L42-60）

```python
def _get_total_mv_yuan(rebalance_date, codes) -> pd.Series:
```

所有价值类因子（EP/BP/SP/CFP）都以总市值为分母，这个辅助函数统一处理单位转换：

```python
basic = load_daily_basic(rebalance_date, rebalance_date, codes=codes)
mv_wan  = basic.loc[rebalance_date]["total_mv"]   # daily_basic 单位：万元
mv_yuan = mv_wan * _WAN_TO_YUAN                    # 转为元（与财务字段同量纲）
```

**为什么不直接在每个因子函数里转换？** 避免重复代码，且单位转换逻辑集中在一处，减少出错的可能（曾出现过漏乘 10000 的 bug）。

---

## 四、核心函数逐一解析

### 4.1 价值类因子（6 个）

价值类因子的通用逻辑：**某种盈利/资产 / 总市值**。"便宜的股票"（相对其基本面而言市值偏低）理论上有超额收益。

---

#### `factor_ep_ttm` — 市盈率倒数（L67-90）

```
公式：EP_TTM = TTM净利润 / 总市值
预期方向：+ （值越大 = 股票越"便宜"，预期超额收益越高）
```

```python
fp_raw  = get_financial_pit_raw()
ttm_ni  = make_ttm(fp_raw, "n_income", rebalance_date, codes)   # TTM 净利润（元）
mv_yuan = _get_total_mv_yuan(rebalance_date, codes)               # 总市值（元）
ep      = ttm_ni / mv_yuan.reindex(ttm_ni.index)
```

**为什么用 TTM 而不是最新一期季报？**

如果在 2024 年 3 月末用 2024 Q1 的净利润（3个月）直接除市值，得到的 EP 只有年化的 1/4，系统性偏低。TTM 把过去 12 个月累计净利润拼出来，消除了报告期的季节性差异（详见 `03_pit_loader.md`）。

**负 EP（亏损公司）的处理：**

不做特殊处理，直接返回负值。去极值（Step 2）会截断极端负值，中性化后的残差仍然有意义（"比同行业同市值更亏"）。

---

#### `factor_bp` — 账面市值比（L93-117）

```
公式：BP = 归母净资产（PIT）/ 总市值
来源：financial_pit.total_hldr_eqy_exc_min_int
预期方向：+ （高 BP = 市净率倒数高 = 股票相对账面价值便宜）
```

```python
latest = get_pit_latest(fp_raw, rebalance_date, codes)    # 最新 PIT 快照
equity = latest["total_hldr_eqy_exc_min_int"]             # 归母净资产（元）
```

**为什么不需要 TTM？** 净资产是资产负债表的存量指标（截止报告期末的余额），不是累计流量，取最新一期的值即可，不需要加减拼接。

**`total_hldr_eqy_exc_min_int` 字段名解读：**

全称 "Total Holder Equity Excluding Minority Interest" = 归属于母公司股东的净资产（排除少数股东权益）。在合并报表中，部分子公司由少数股东参股，BP 因子分子应该用"属于我们（上市公司股东）的净资产"，而不是整个合并报表的净资产。

---

#### `factor_sp_ttm` — 销售收益率（L120-137）

```
公式：SP_TTM = TTM营业收入 / 总市值
来源：make_ttm(fp_raw, "revenue", ...)
预期方向：+
```

营收比净利润更难造假（利润可以通过关联交易等手段操纵，但营收对应实实在在的销售合同）。SP 在某些行业（如周期行业、利润波动大的行业）比 EP 更稳定。

---

#### `factor_dy_ttm` — 股息率（L140-161）

```
公式：DY_TTM = dv_ttm（daily_basic 字段，百分比）
预期方向：+（高股息率公司通常财务稳健，且有股息再投资收益）
```

**特殊标注：非严格 PIT（L147）**

```python
# ⚠️ 非严格 PIT：Tushare 的 dv_ttm 基于最新财报计算，可能有 0-60 天前视偏差。
```

`daily_basic.dv_ttm` 是 Tushare 预计算的字段，其更新时间不明确，可能用了尚未正式公告的分红数据。因此：

- `dy_ttm` **不在** `_FACTOR_BUILDERS` 默认集合中（L479-498）
- 放在单独的 `_FACTOR_BUILDERS_NONSTRICT_PIT` 字典（L502-504）
- 使用者需要显式传入 `factor_names=["dy_ttm", ...]` 才能启用，并须自行标注偏差风险

---

#### `factor_cfp` — 经营现金流收益率（L164-184）

```
公式：CFP = TTM经营现金流 / 总市值
来源：make_ttm(fp_raw, "n_cashflow_act", ...)
预期方向：+
```

**为什么 CFP 比 EP 更难造假：** 利润可以通过应收账款、存货计价、折旧政策等会计手段调节，但经营现金流（"真金白银流入"）更难伪造。应计项目因子（`accrual`）正是利用这一差异来衡量盈利质量。

---

#### `factor_fcfp` — 自由现金流收益率（L187-210）

```
公式：FCFP = TTM自由现金流 / 总市值
来源：make_ttm(fp_raw, "free_cashflow", ...)
       free_cashflow = 经营现金流 - 资本支出（Tushare预计算）
预期方向：+
```

FCFP 与 CFP 的区别：CFP 是"赚了多少钱"，FCFP 是"花完维持业务所需的资本开支之后，还剩多少可以分给股东的钱"。对重资产行业（钢铁、房地产），FCFP 远低于 CFP；对轻资产行业（软件、消费），两者差距小。

---

### 4.2 质量类因子（5 个）

质量因子衡量公司经营效率和财务健康程度。大多数来自 `indicator_pit`（Tushare 预计算的衍生指标），直接取最新 PIT 快照，不需要 TTM。

---

#### `factor_roe` — 净资产收益率（L217-239）

```
来源：indicator_pit.roe（百分比）
公式：ROE = 净利润 / 平均净资产（Tushare 用加权平均净资产，非期末净资产）
预期方向：+
```

**为什么来自 indicator_pit 而非自己计算？** Tushare 的 ROE 使用加权平均净资产作为分母（在年报期间按月加权），与主流数据库口径一致。如果自己用 `n_income / total_hldr_eqy_exc_min_int` 计算，分母只用期末净资产，精度略低。

---

#### `factor_roa` — 总资产收益率（L242-263）

```
来源：indicator_pit.roa（百分比）
公式：ROA = 净利润 / 平均总资产
预期方向：+
```

ROA 比 ROE 更"公平"：ROE 可以通过加杠杆来提高（杠杆越高，ROE 越虚高），ROA 排除了杠杆效应，更纯粹地反映资产利用效率。

---

#### `factor_gross_margin` — 毛利率（L266-287）

```
来源：indicator_pit.grossprofit_margin（百分比）
公式：毛利率 = (营收 - 营业成本) / 营收
预期方向：+ （高毛利率 = 护城河强 = 定价权强）
```

毛利率是"竞争优势"的直接体现：高毛利行业（茅台 80%+）往往有品牌护城河；低毛利行业（贸易、制造）竞争激烈。在行业中性化之后，高毛利的公司相对同行有超额收益。

---

#### `factor_asset_turn` — 总资产周转率（L290-311）

```
来源：indicator_pit.assets_turn（倍数，非百分比）
公式：周转率 = 营收 / 平均总资产
预期方向：+（高周转 = 单位资产带来更多营收 = 效率高）
```

周转率与毛利率往往呈负相关（沃尔玛低毛利高周转 vs 奢侈品高毛利低周转），两者结合（即 ROA = 毛利率 × 周转率）才能反映综合效率。

---

#### `factor_leverage` — 资产负债率（L314-336）

```
来源：indicator_pit.debt_to_assets（百分比）
公式：负债率 = 总负债 / 总资产
预期方向：-（低负债率 = 财务更健康 = 低违约风险 = 预期超额收益）
```

**金融股特殊处理（已在 preprocess_factor 中处理）：** 银行的负债率通常 >90%（存款是负债），保险公司也类似，但这是其业务特性，不代表风险。`preprocess_factor` 调用时会传入 `fin_sector_codes=FINANCIAL_SECTOR_CODES`，金融股的 leverage 因子值会被置 NaN，不参与排序。

---

#### `factor_accrual` — 应计项目（L339-370）

```
公式：Accrual = (TTM净利润 - TTM经营现金流) / 最新总资产
预期方向：-（高应计 = 盈利中非现金部分多 = 质量差 = 负超额）
```

这是 Sloan (1996) 应计异象的量化实现。

**为什么需要三个数据源（L356-359）？**

```python
fp_raw       = get_financial_pit_raw()
ttm_ni       = make_ttm(fp_raw, "n_income",       rebalance_date, codes)  # 流量→TTM
ttm_cfo      = make_ttm(fp_raw, "n_cashflow_act", rebalance_date, codes)  # 流量→TTM
latest       = get_pit_latest(fp_raw, rebalance_date, codes)               # 存量→最新快照
total_assets = latest["total_assets"]
```

- 分子（净利润 - 经营现金流）：两者都是流量指标，需要 TTM 化确保口径一致（不能一个用年报一个用季报）
- 分母（总资产）：存量指标，直接取最新 PIT 快照即可

**防除零保护（L365-366）：**

```python
total_assets = total_assets.replace(0, np.nan)   # 极罕见，但防止 inf 结果
```

---

### 4.3 成长类因子（4 个）

成长因子衡量公司的盈利/收入增速，多数直接来自 `indicator_pit` 的预计算值。

---

#### `factor_np_yoy` — 净利润同比增速（L377-399）

```
来源：indicator_pit.netprofit_yoy（百分比）
预期方向：+（高增速预期超额）
```

**极端值注意（L385）：** 净利润同比增速极端值频繁出现（如公司上年亏损 1 元、今年盈利 100 元，增速 10000%）。这类极端值在 Step 2（MAD 去极值）会被截断，不会影响因子的横截面排序。

---

#### `factor_rev_yoy` — 营收同比增速（L402-423）

```
来源：indicator_pit.or_yoy（百分比）
预期方向：+
```

营收增速与利润增速的区别：利润可以通过成本控制、一次性收益等因素短期提升，营收增速更能反映业务扩张的可持续性。两个因子结合使用可以区分"真增长"和"利润虚增"。

---

#### `factor_roe_delta` — ROE 同比变化（L426-447）

```
公式：roe_delta = 当期 ROE - 上年同期 ROE（均来自 indicator_pit）
预期方向：+（ROE 改善的公司预期超额收益）
```

这个因子捕捉的是"盈利趋势"，而非盈利水平。一家 ROE 从 5% 提升到 8% 的公司，可能比 ROE 一直维持在 12% 但开始下滑的公司更有吸引力。

实现委托给 `pit_loader.get_roe_delta()`，后者处理了"上年同期"的 PIT 对齐和缺失值处理（详见 `03_pit_loader.md`）。

---

#### `factor_q_roe` — 单季 ROE（L450-472）

```
来源：indicator_pit.q_roe（百分比，单季净利润/平均净资产）
预期方向：+
```

**单季 ROE vs 全年 ROE：** 全年 ROE 是"过去 12 个月的累计表现"，单季 ROE 是"最近这个季度的表现"，对近期边际变化更敏感。两者配合 `roe_delta` 可以从不同时间维度刻画盈利质量和趋势。

**波动率注意（L459）：** 单季 ROE 方差远大于年化 ROE，因为只有 3 个月的数据，季节性波动明显（部分公司 Q4 大结算，Q1 很少）。MAD 去极值在这里特别重要。

---

## 五、批量构建入口

### `_FACTOR_BUILDERS` 字典（L479-498）

```python
_FACTOR_BUILDERS = {
    "ep_ttm":       factor_ep_ttm,
    "bp":           factor_bp,
    "sp_ttm":       factor_sp_ttm,
    "cfp":          factor_cfp,
    "fcfp":         factor_fcfp,
    "roe":          factor_roe,
    "roa":          factor_roa,
    "gross_margin": factor_gross_margin,
    "asset_turn":   factor_asset_turn,
    "leverage":     factor_leverage,
    "accrual":      factor_accrual,
    "np_yoy":       factor_np_yoy,
    "rev_yoy":      factor_rev_yoy,
    "roe_delta":    factor_roe_delta,
    "q_roe":        factor_q_roe,
}
```

`dy_ttm` 放在单独的 `_FACTOR_BUILDERS_NONSTRICT_PIT` 字典，默认不构建。

### `build_all_financial_factors(rebalance_date, codes, factor_names)` — 批量入口（L507-546）

```python
def build_all_financial_factors(
    rebalance_date: pd.Timestamp,
    codes: list[str],
    factor_names: Optional[list[str]] = None,
) -> dict[str, pd.Series]:
```

**关键设计：错误隔离（L535-540）**

```python
for name in names:
    try:
        result[name] = _all_builders[name](rebalance_date, codes)
    except Exception as exc:
        log.error("因子 %s 在 %s 构建失败：%s", name, rebalance_date.date(), exc)
        result[name] = pd.Series(np.nan, index=pd.Index(codes, name="ts_code"), name=name)
```

单个因子构建失败不会阻断整个批量构建。失败的因子用全 NaN Series 填充，记录错误日志，其他因子继续执行。这确保了一个因子的数据问题不会导致整个月的因子面板缺失。

---

## 六、数据流图

```
get_financial_pit_raw()
        │
        ├─→ make_ttm(fp_raw, "n_income", T)        → factor_ep_ttm
        ├─→ make_ttm(fp_raw, "revenue", T)          → factor_sp_ttm
        ├─→ make_ttm(fp_raw, "n_cashflow_act", T)   → factor_cfp / factor_accrual
        ├─→ make_ttm(fp_raw, "free_cashflow", T)    → factor_fcfp
        └─→ get_pit_latest(fp_raw, T)
                │
                ├─→ total_hldr_eqy_exc_min_int      → factor_bp
                └─→ total_assets                    → factor_accrual（分母）

get_indicator_pit_raw()
        │
        └─→ get_pit_latest(ip_raw, T)
                │
                ├─→ roe              → factor_roe
                ├─→ roa              → factor_roa
                ├─→ grossprofit_margin → factor_gross_margin
                ├─→ assets_turn      → factor_asset_turn
                ├─→ debt_to_assets   → factor_leverage
                ├─→ netprofit_yoy    → factor_np_yoy
                ├─→ or_yoy           → factor_rev_yoy
                └─→ q_roe            → factor_q_roe

get_indicator_pit_raw() + get_roe_delta()
                         → factor_roe_delta

load_daily_basic(T, T)
        └─→ total_mv（万元 × 10000 = 元）→ _get_total_mv_yuan
                → 所有价值类因子的分母

全部 15 个因子（raw Series，ts_code → 原始值）
        ↓
build_all_financial_factors() → dict[str, pd.Series]
        ↓
preprocess_factor()（preprocess.py 中统一预处理）
```

---

## 七、领域知识补充

**价值溢价（Value Premium）的来源争议：**

价值因子（EP/BP/SP）是所有量化因子中最"古老"的，Fama-French (1992) 的三因子模型已经将其系统化。但其来源至今有争议：
- **风险补偿说**：价值股通常是陷入困境的公司，持有它们有更高的风险，高收益是对这种风险的补偿
- **行为金融说**：投资者系统性地对"价值股"过度悲观、对"成长股"过度乐观，价值溢价来自市场定价错误的修正

在中国 A 股市场，价值溢价的稳定性不如美股，在成长风格行情中（如 2019-2020）价值因子往往显著跑输。

**应计异象（Accrual Anomaly）— Sloan (1996)：**

Sloan 发现：净利润中应计成分（非现金部分）越高的公司，未来股票收益率越低。直觉解释：应计利润（如应收账款增加）更难以持续，市场对其估值偏高，最终会回归；而现金利润更有保障，市场低估了其价值。

在 A 股，这一异象同样存在，但强度不如美股（国内财报质量参差不齐，应计项目的信息含量受噪声干扰）。
