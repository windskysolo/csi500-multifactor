# 因子实现计划：高优先级前半段（P0+P1）

> **文档性质**：可执行实施手册（已按当前仓库接口修订）  
> **生成日期**：2026-05-29  
> **修订日期**：2026-05-29  
> **依据文档**：`current work/5.28/plans/factor_gap_analysis.md`  
> **执行范围**：4 项代码任务（3 个新因子 + 1 个公式修复）

---

## 0. 前置发现（执行前必读）

在分析 `factor_gap_analysis.md` 时，发现三个关键事实与文档预期不符：

| 因子 | gap_analysis 描述 | 实际状态 | 训练期 IC_IR | 结论 |
|------|------------------|---------|------------|------|
| `accrual` | 空缺，需构建 | 已实现，已评估 | −0.148 ❌ | 公式有缺陷，改进后重评 |
| `bp` | 空缺，需构建 | 已实现，已评估 | +0.217 ❌ | Gate 1 失败，本次不动 |
| `fcfp` | 空缺，需构建 | 已实现，已评估 | +0.236 ❌ | Gate 1 失败，本次不动 |

**`accrual` 的缺陷根因**：  
当前分母用 `latest["total_assets"]`（T 日最新快照），而 Sloan (1996) 原始定义用 `(期初+期末)/2` 的平均总资产。分子是 TTM（跨12个月的流量），分母是单点值，时序错位导致信号被噪音稀释。

**bp 和 fcfp 本次不重复评估**：训练期 IC_IR 明显低于门槛（0.30），没有明确的改进方向。本次只重算 `accrual` 和新增因子，避免扩大评估面和污染主评估产物。

---

## 1. 任务清单

| # | 任务 | 文件 | 类型 | 工作量估算 |
|---|------|------|------|----------|
| T1 | 修复 `factor_accrual` 公式（平均资产）| `financial_factors.py` | 改进 | 0.5h |
| T2 | 新增 `factor_asset_growth` | `financial_factors.py` | 新建 | 1h |
| T3 | 新增 `factor_share_issuance` | `price_factors.py` | 新建 | 1h |
| T4 | 新增 `factor_mf_flow_ratio` | `price_factors.py` | 新建 | 1.5h |
| T5 | 注册到构建脚本 | `build_factor_panels.py` | 配置 | 0.5h |
| T6 | 构建面板 + 运行评估 | CLI | 运行 | —（机器跑） |

**先决条件（T4 前必须完成）**：确认 margin 缓存是否包含 `rz_net`；若没有，则至少必须同时包含 `rzmre`/`rzche`（见下方 Step 0）。

**硬性运行约束**：

1. 本计划只覆盖训练/验证期，命令中不得加入 `--allow-test-set`，不得把 `--end-date` 设到 `2023-01-01` 或之后。
2. `scripts.run_factor_evaluation` 默认会写入主目录 `reports/factor_evaluation/`，因此本计划首次评估必须使用隔离输出目录：`reports/factor_evaluation_factor_impl_p0p1/`。
3. 当前 `run_factor_evaluation.py` 会扫描 `data/processed/factor_panels/` 下全部已有因子面板，不支持只评估指定因子。判读报告时只关注本计划的 4 个目标因子，主 `final_factors.json` 不在本计划中刷新。
4. 先跑 `build_factor_panels --dry-run` 和单元测试，再正式构建面板；不要跳过测试直接跑评估。

---

## 2. Step 0：数据字段探查

**执行命令**（在项目根目录）：

```bash
python -c "
import pandas as pd
from src.data.loader import load_margin, load_industry
import src.config as cfg

# 检查 margin 字段
mg = load_margin(pd.Timestamp('2021-01-29'), pd.Timestamp('2021-01-29'))
print('margin 字段:', mg.columns.tolist())
required_flow = {'rz_net'} if 'rz_net' in mg.columns else {'rzmre', 'rzche'}
print('融资净买入可用:', required_flow.issubset(set(mg.columns)), '使用字段:', sorted(required_flow))

# 检查 industry 行业列和值
ind = load_industry(pd.Timestamp('2021-01-29'), pd.Timestamp('2021-01-29'))
print('industry 字段:', ind.columns.tolist())
if ind.empty is False:
    t = pd.Timestamp('2021-01-29')
    if t in ind.index.get_level_values('trade_date'):
        print('行业代码样本:', ind.loc[t]['industry_code'].dropna().unique()[:10])
        print('行业名称样本:', ind.loc[t]['industry_name'].dropna().unique()[:10])
print('金融行业代码配置:', sorted(cfg.FINANCIAL_SECTOR_CODES))
"
```

**决策分支**：

- 若 `margin` 有 `rz_net` → 正常实现 `factor_mf_flow_ratio`，公式使用 `sum(rz_net, 近20交易日) / circ_mv`
- 若没有 `rz_net` 但有 `rzmre`/`rzche` → 仍可实现 `factor_mf_flow_ratio`，在因子内计算 `rzmre - rzche`
- 若只有 `rzye` → T4 改为实现 `factor_mf_congestion_neg`（融资余额拥挤度，等同于 `-margin_ratio_60d_avg`）
- `industry` 当前应有 `industry_code` / `industry_name`。财务因子的银行/非银金融过滤由 `build_factor_panels.py` 传入 `cfg.FINANCIAL_SECTOR_CODES` 后在 `preprocess_factor` 中统一执行，**不要在 `factor_asset_growth` 内重复猜行业列名**

---

## 3. T1：修复 `factor_accrual`

**文件**：`src/factors/financial_factors.py`

**定位**：第 349–380 行，函数 `factor_accrual`

**修改内容**：分母从 `latest["total_assets"]` 改为平均总资产。

```python
def factor_accrual(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    应计项目因子（Accrual）：(TTM净利润 - TTM经营现金流) / 平均总资产。

    分母用平均总资产 = (本期总资产 + 4季前总资产) / 2，
    与 Sloan (1996) 原始定义一致，消除分子(12个月流量)与分母(单点值)的时序错位。

    guard：avg_assets ≤ 0 或 NaN 时返回 NaN。

    预期方向：-（高应计 = 盈利质量差 = 低未来收益）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → accrual（无量纲比率）；数据不足时为 NaN
    """
    fp_raw  = get_financial_pit_raw()
    ttm_ni  = make_ttm(fp_raw, "n_income",       rebalance_date, codes)
    ttm_cfo = make_ttm(fp_raw, "n_cashflow_act", rebalance_date, codes)

    # 平均总资产：取 q0（最新季）和 q4（4季前）的均值
    hist_assets = get_quarterly_history(
        fp_raw, "total_assets", rebalance_date, codes, n_quarters=5
    )
    avg_assets = ((hist_assets["q0"] + hist_assets["q4"]) / 2).replace(0, np.nan)
    avg_assets[avg_assets < 0] = np.nan   # 负资产（极罕见）无经济含义

    accrual      = (ttm_ni.reindex(avg_assets.index) - ttm_cfo.reindex(avg_assets.index)) / avg_assets
    accrual.name = "accrual"
    return accrual.reindex(codes)
```

**需要新增的 import**：`get_quarterly_history` 已在文件顶部 `from src.data.pit_loader import` 中导入，无需修改。

**验证**：修改后在 REPL 中跑一个调仓日，确认返回值分布合理（中位数约 -0.03 ~ +0.03）：

```python
from src.factors.financial_factors import factor_accrual
from src.data.universe import get_investable_universe
import pandas as pd
t = pd.Timestamp('2020-12-31')
codes = get_investable_universe(t).tolist()
result = factor_accrual(t, codes)
print(result.describe())
print("NaN 比例:", result.isna().mean())
```

---

## 4. T2：新增 `factor_asset_growth`

**文件**：`src/factors/financial_factors.py`

**位置**：添加到成长类因子末尾（`factor_gross_margin_trend` 之后，`_FACTOR_BUILDERS` 之前）

**需要新增的 import**：无。`get_quarterly_history` 已在文件顶部导入；金融行业过滤由构建脚本的统一预处理完成，不在本函数中导入 `load_industry`。

```python
def factor_asset_growth(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    总资产增速因子（取反后为正向因子）。

    经济逻辑：资产扩张过快 → 低效投资 / 市场过度乐观 → 未来超额收益偏低。
    Cooper, Gulen & Schill (2008, JoF) 在美股发现强负向效应；中证500中大量
    并购驱动型公司，使该因子在 A 股有独特区分度。

    asset_growth_raw = (total_assets_q0 - total_assets_q4) / |total_assets_q4|
    Factor = -asset_growth_raw（资产增速越低 → 因子值越高 → 预期收益越高）

    金融行业（银行/非银金融）的资产增速是核心业务指标而非过度投资信号；
    本函数只返回原始值，金融股置 NaN 由 build_factor_panels.py →
    preprocess_factor(fin_sector_codes=cfg.FINANCIAL_SECTOR_CODES) 统一执行。
    极端值截尾至 [-5, 5]（重组/剥离事件）。

    预期方向：+（低资产增速预期超额收益）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → -yoy_asset_growth（小数）；数据不足时为 NaN
    Time alignment: q0/q4 均来自 pit_date <= T 的已披露数据，无未来函数
    Data deps: financial_pit.parquet
    """
    fp_raw = get_financial_pit_raw()
    hist   = get_quarterly_history(fp_raw, "total_assets", rebalance_date, codes, n_quarters=5)

    q0    = hist["q0"]
    q4    = hist["q4"]
    denom = q4.abs().replace(0, np.nan)

    asset_growth_raw = ((q0 - q4) / denom).clip(-5, 5)
    result           = -asset_growth_raw

    result.name = "asset_growth"
    return result.reindex(codes)
```

**注册到 `_FACTOR_BUILDERS`**（`financial_factors.py` 末尾）：

```python
_FACTOR_BUILDERS = {
    ...
    # 阶段 7：资本效率
    "asset_growth": factor_asset_growth,
}
```

**验证**：

```python
from src.factors.financial_factors import factor_asset_growth
from src.data.universe import get_investable_universe
import pandas as pd

t = pd.Timestamp('2020-12-31')
codes = get_investable_universe(t).tolist()
result = factor_asset_growth(t, codes)
print(result.describe())
print("原始值 NaN 比例:", result.isna().mean())
```

**金融行业过滤验证**：正式构建面板后查看 `data/processed/factor_panel_diagnostics.parquet` 中 `asset_growth` 的 `n_fin_filtered`，应大于 0；不要用原始函数的 NaN 比例判断金融过滤是否生效。

---

## 5. T3：新增 `factor_share_issuance`

**文件**：`src/factors/price_factors.py`

**位置**：添加到 `factor_short_ratio` 之后（资金流向类末尾）

**已有依赖**：`_valid_dates_before`、`load_daily_basic`、`WINDOW_HIGH_52W` 均已在文件中定义，直接复用。

```python
def factor_share_issuance(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    股本稀释因子（取反后为正向因子）。

    经济逻辑：管理层在股价高估时倾向增发（负向信号）；在低估时回购（正向信号）。
    Loughran & Ritter (1995) 发现增发后 5 年内股票系统性跑输；
    A 股创业板/科创板定向增发极为频繁，使该因子具有持续差异化价值。

    share_change = (total_share_T - total_share_{T-252td}) / total_share_{T-252td}
    Factor = -share_change（增发 = 负向，回购/注销 = 正向，取反后正向因子）

    PIT 合规：daily_basic.total_share 是交易所实时数据，无前视偏差。
    极端值截尾至 [-1, 1]（100% 增发/回购视为异常重组事件）。

    预期方向：+（股本缩减的公司预期超额收益更高）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → -yoy_share_change（小数）；历史不足时为 NaN
    Time alignment: 两端均为已发生的市场数据，无未来函数
    Data deps: daily_basic.parquet
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) <= WINDOW_HIGH_52W:   # T 当日 + 向前 252 个交易日
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="share_issuance")

    one_year_ago = valid[-(WINDOW_HIGH_52W + 1)]   # T 前第 252 个交易日（不含 T）

    basic_now  = load_daily_basic(rebalance_date, rebalance_date,  codes=codes)
    basic_prev = load_daily_basic(one_year_ago,   one_year_ago,    codes=codes)

    now_dates  = basic_now.index.get_level_values("trade_date")
    prev_dates = basic_prev.index.get_level_values("trade_date")
    if rebalance_date not in now_dates or one_year_ago not in prev_dates:
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="share_issuance")

    shares_now  = basic_now.loc[rebalance_date]["total_share"]
    shares_prev = basic_prev.loc[one_year_ago]["total_share"]

    common = shares_now.index.intersection(shares_prev.index)
    denom  = shares_prev[common].replace(0, np.nan)

    share_change = ((shares_now[common] - shares_prev[common]) / denom).clip(-1, 1)

    result      = -share_change
    result.name = "share_issuance"
    return result.reindex(codes)
```

**注册到 `_PRICE_FACTOR_BUILDERS`**（`price_factors.py` 末尾）：

```python
_PRICE_FACTOR_BUILDERS = {
    ...
    "share_issuance": factor_share_issuance,
}
```

**验证**：

```python
from src.factors.price_factors import factor_share_issuance
from src.data.universe import get_investable_universe
import pandas as pd

t = pd.Timestamp('2020-12-31')
codes = get_investable_universe(t).tolist()
result = factor_share_issuance(t, codes)
print(result.describe())
# 预期：大部分公司年度股本变化在 [-0.3, 0.3] 之间；NaN 应 < 5%
```

---

## 6. T4：新增 `factor_mf_flow_ratio`

**文件**：`src/factors/price_factors.py`

**位置**：添加到 `factor_share_issuance` 之后

**前提**：Step 0 确认 `rz_net` 字段可用；若没有 `rz_net`，但有 `rzmre`/`rzche`，则在因子内计算 `rzmre - rzche`。若两者都不可用，实现 `factor_mf_congestion_neg`（见 §6.1）。

**新增常量**（添加到文件顶部常量区，紧接现有常量）：

```python
WINDOW_MF_FLOW  = 20   # 融资净买入流量窗口（交易日）
WINDOW_MF_STOCK = 60   # 融资余额拥挤度窗口（交易日，备用）
_MF_WAN_TO_YUAN = 10_000.0  # daily_basic.circ_mv 万元→元
```

```python
def factor_mf_flow_ratio(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    近期融资净买入流量比（正向动量信号）。

    mf_flow_ratio = sum(rz_net, 近20交易日) / 当日流通市值

    经济逻辑：近期本土杠杆资金净流入 → 短期需求支撑 → 正向动量信号。
    与 hk_hold_chg（北向资金）互补，覆盖 A 股本土散户/杠杆资金视角；
    中证500成分股两融覆盖率 > 95%，覆盖率充足。

    与现有因子的相关性预估：
      turn_20d：r ≈ 0.25-0.40（高融资买入伴随高换手，Ridge 可自然处理）
      hk_hold_chg：r < 0.20（不同资金来源，独立性强）

    注意：优先使用 csv_to_parquet.py 已生成的 rz_net = rzmre - rzche。
    若缓存中既无 rz_net，也无法由 rzmre/rzche 计算，函数返回全 NaN（不回退到 rzye）。

    预期方向：+（融资净流入越多，短期价格支撑越强）

    Args:
        rebalance_date: 调仓日
        codes:          可投资股票代码列表
    Returns:
        ts_code → 20日融资净买入 / 流通市值（无量纲）；无数据时为 NaN
    Time alignment: 使用 [T-20td, T] 的融资流量数据，不包含未来
    Data deps: margin.parquet, daily_basic.parquet
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < WINDOW_MF_FLOW:
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_flow_ratio")

    start = valid[-WINDOW_MF_FLOW]
    mg    = load_margin(start, rebalance_date, codes=codes)

    if mg.empty:
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_flow_ratio")

    if "rz_net" in mg.columns:
        flow = mg["rz_net"]
    elif {"rzmre", "rzche"}.issubset(mg.columns):
        flow = mg["rzmre"] - mg["rzche"]
    else:
        log.warning("mf_flow_ratio: margin 缓存缺少 rz_net 或 rzmre/rzche，返回全 NaN")
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_flow_ratio")

    net_buy_20d = flow.unstack("ts_code").fillna(0.0).sum(axis=0)   # 20日累计净买入（元）

    # 流通市值（daily_basic.circ_mv 万元 → 元，与 margin 数据元单位对齐）
    basic = load_daily_basic(rebalance_date, rebalance_date, codes=codes)
    if rebalance_date not in basic.index.get_level_values("trade_date"):
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_flow_ratio")
    circ_mv_yuan = basic.loc[rebalance_date]["circ_mv"] * _MF_WAN_TO_YUAN

    common = net_buy_20d.index.intersection(circ_mv_yuan.index)
    result = net_buy_20d[common] / circ_mv_yuan[common].replace(0, np.nan)
    result.name = "mf_flow_ratio"
    return result.reindex(codes)
```

**注册到 `_PRICE_FACTOR_BUILDERS`**：

```python
_PRICE_FACTOR_BUILDERS = {
    ...
    "mf_flow_ratio": factor_mf_flow_ratio,
}
```

**验证**：

```python
from src.factors.price_factors import factor_mf_flow_ratio
from src.data.universe import get_investable_universe
import pandas as pd

t = pd.Timestamp('2021-06-30')
codes = get_investable_universe(t).tolist()
result = factor_mf_flow_ratio(t, codes)
print(result.describe())
# 预期：多数值在 [-0.05, 0.05] 附近；非两融标的为 NaN
```

### 6.1 备用方案：若融资净买入字段不可用

若 Step 0 确认既没有 `rz_net`，也没有可用于计算净买入的 `rzmre`/`rzche`，实现 `factor_mf_congestion_neg` 替代：

```python
def factor_mf_congestion_neg(
    rebalance_date: pd.Timestamp,
    codes: list[str],
) -> pd.Series:
    """
    融资余额拥挤度因子（取反后为正向因子）。

    mf_congestion = mean(rzye, 近60交易日) / 当日流通市值
    Factor = -mf_congestion（高余额 = 多头拥挤 = 未来负向风险 → 取反为正向因子）

    与现有 margin_ratio（单日余额/总市值）的区别：
    1. 用 60 日滚动均值而非单日值（更稳定，减少月末融资突变）
    2. 分母用流通市值而非总市值（对流通盘更敏感）
    """
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < WINDOW_MF_STOCK:
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_congestion_neg")

    start = valid[-WINDOW_MF_STOCK]
    mg    = load_margin(start, rebalance_date, codes=codes)
    if mg.empty or "rzye" not in mg.columns:
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_congestion_neg")

    rzye_avg = mg["rzye"].unstack("ts_code").mean(skipna=True)  # 60日均值（元）

    basic = load_daily_basic(rebalance_date, rebalance_date, codes=codes)
    if rebalance_date not in basic.index.get_level_values("trade_date"):
        return pd.Series(dtype=float,
                         index=pd.Index(codes, name="ts_code"),
                         name="mf_congestion_neg")
    circ_mv_yuan = basic.loc[rebalance_date]["circ_mv"] * _MF_WAN_TO_YUAN

    common  = rzye_avg.index.intersection(circ_mv_yuan.index)
    balance = rzye_avg[common] / circ_mv_yuan[common].replace(0, np.nan)

    result      = -balance
    result.name = "mf_congestion_neg"
    return result.reindex(codes)
```

---

## 7. T5：注册到构建脚本

**文件**：`scripts/build_factor_panels.py`

**修改 `FINANCIAL_FACTORS` 列表**（第 65 行附近）：

```python
FINANCIAL_FACTORS: list[str] = [
    # 价值
    "ep_ttm", "bp", "sp_ttm", "cfp", "fcfp",
    # 质量
    "roe", "roa", "gross_margin", "asset_turn", "leverage", "accrual",
    # 成长
    "np_yoy", "rev_yoy", "roe_delta", "q_roe",
    # 阶段 4：低风险因子扩展
    "roe_smoothed_4q", "roe_delta_3q", "gross_margin_trend", "ep_vs_history",
    # 阶段 5：质量综合因子
    "piotroski_f", "garp",
    # 阶段 6：新增财务质量/成长因子
    "roe_stability", "rev_acceleration",
    # 阶段 7：资本效率（新增）
    "asset_growth",
]
```

**修改 `PRICE_FACTORS` 列表**（第 80 行附近）：

```python
PRICE_FACTORS: list[str] = [
    # 动量/反转
    "ret_1m", "mom_6_1", "mom_12_1", "holder_chg",
    # 波动率
    "vol_60d", "ivol_60d", "max_ret",
    # 流动性
    "turn_20d", "amihud",
    # 资金流向
    "margin_ratio", "short_ratio", "large_net_inflow",
    # 阶段 5：动量类因子（原有）
    "high_52w", "ind_adj_mom", "mom_risk_adj",
    # Sprint 1：修正版动量因子
    "high_52w_v2", "ind_adj_mom_6_1", "mom_consistency_6",
    # 阶段 7：股本行为 + 融资资金（新增）
    "share_issuance",
    "mf_flow_ratio",   # 或 "mf_congestion_neg"，取决于 Step 0 结果
]
```

---

## 8. T6：构建面板 + 四道门评估

### 8.1 构建因子面板

```powershell
# 1) 先确认构建脚本能识别目标因子，且日期范围仍为 TRAIN_START ~ VALID_END
python -m scripts.build_factor_panels --dry-run --factors accrual asset_growth share_issuance mf_flow_ratio

# 若 Step 0 确认用备用方案，则 dry-run 改为：
# python -m scripts.build_factor_panels --dry-run --factors accrual asset_growth share_issuance mf_congestion_neg

# 2) 先跑新增单测，不通过则不要构建面板
python -m pytest tests/test_new_factors_p0p1.py -q

# 3) 重建 accrual（公式已改）和所有新因子
python -m scripts.build_factor_panels --factors accrual asset_growth share_issuance mf_flow_ratio

# 若 Step 0 确认用备用方案
# python -m scripts.build_factor_panels --factors accrual asset_growth share_issuance mf_congestion_neg
```

### 8.2 运行四道门评估

```powershell
# 必须写入隔离目录，避免覆盖主 reports/factor_evaluation/final_factors.json
python -m scripts.run_factor_evaluation --output-dir reports/factor_evaluation_factor_impl_p0p1 --no-plots
```

注意：`run_factor_evaluation.py` 会扫描 `data/processed/factor_panels/` 下全部已有面板。报告中只判读本计划的 `accrual`、`asset_growth`、`share_issuance`、`mf_flow_ratio`（或备用 `mf_congestion_neg`）；是否刷新主 `final_factors.json` 需另行决策。

### 8.3 评估结果解读标准

| Gate | 通过标准 | 说明 |
|------|---------|------|
| Gate 0 | 覆盖率 ≥ 80% | 财务因子经 `preprocess_factor` 金融行业过滤后仍应满足覆盖率；同时检查 diagnostics 的 `n_fin_filtered` |
| Gate 1 | 训练期 `\|IC_IR\|` ≥ 0.30 且 t ≥ 2 | 预期最有希望的是 `asset_growth`（文献 0.25-0.45） |
| Gate 2 | `lead_ratio < 2.0x` | 财务类因子通常 1.0-1.3x；`mf_flow_ratio` 需重点检查 |
| Gate 3 | 验证期 `\|IC_IR\|` ≥ 0.20 且方向不反转 | 验证期 2021-2022 是反质量环境，财务因子需注意 |

### 8.4 入池附加检验

Gate 1-3 全部通过后，还需检验：

```bash
# 检查与现有因子的截面相关性（目标 |r| < 0.70）
# 评估脚本会自动生成 factor_correlation.csv
# 重点关注：
#   asset_growth vs rev_yoy        (预期 r ≈ -0.10 ~ -0.25)
#   asset_growth vs rev_acceleration (预期 r ≈ -0.05 ~ -0.15)
#   mf_flow_ratio vs turn_20d       (预期 r ≈ 0.25-0.40)
#   share_issuance vs holder_chg    (预期 r < 0.30)
```

---

## 9. 单元测试

**文件**：新建 `tests/test_new_factors_p0p1.py`

每个新因子必须覆盖以下最小测试集。下面是测试清单，落地时必须写成真实断言，不能保留 `...`；其中需要隔离数据读取的用 `monkeypatch` 替换 `get_financial_pit_raw`、`get_quarterly_history`、`load_daily_basic`、`load_margin` 等依赖。

建议测试清单：

| 类 | 测试名 | 必须断言 |
|---|---|---|
| `TestAccrualImproved` | `test_returns_series_with_ts_code_index` | 返回 `pd.Series`，index 与传入 `codes` 完全一致，`name == "accrual"` |
| `TestAccrualImproved` | `test_denominator_uses_average_not_latest` | monkeypatch `make_ttm` 与 `get_quarterly_history`：当 `ttm_ni=30`、`ttm_cfo=0`、`q0=200`、`q4=100` 时，结果为 `30 / 150`，不是 `30 / 200` |
| `TestAccrualImproved` | `test_nan_when_assets_insufficient_history` | `q4` 缺失或平均资产 <= 0 时结果为 NaN |
| `TestAssetGrowth` | `test_negative_for_fast_growing_company` | `q0=200`、`q4=100` 时结果为 `-1.0` |
| `TestAssetGrowth` | `test_clip_extreme_values` | `q0/q4` 导致原始增长超过 500% 时，结果被截尾到 `-5.0` 或 `+5.0` 对应边界 |
| `TestAssetGrowth` | `test_returns_series_reindexed_to_codes` | 返回结果必须按传入 `codes` 对齐，缺失股票为 NaN |
| `TestAssetGrowth` | `test_financial_sector_filtered_by_preprocess_not_raw_factor` | 用 `preprocess_factor` 构造 `industry_code in cfg.FINANCIAL_SECTOR_CODES` 的样本，确认金融股置 NaN；原始 `factor_asset_growth` 不直接查行业 |
| `TestShareIssuance` | `test_nan_when_history_insufficient` | 交易日历史不足 `WINDOW_HIGH_52W + 1` 时返回与 `codes` 对齐的全 NaN |
| `TestShareIssuance` | `test_positive_for_buyback_company` | `total_share_T < total_share_T_minus_252` 时因子值为正 |
| `TestShareIssuance` | `test_negative_for_dilution_company` | `total_share_T > total_share_T_minus_252` 时因子值为负 |
| `TestMfFlowRatio` | `test_uses_rz_net_not_buy_amount` | 合成 `rzmre=100`、`rzche=80`、`rz_net=20` 时，结果按 `20 / circ_mv_yuan` 计算，不能按 `100 / circ_mv_yuan` |
| `TestMfFlowRatio` | `test_falls_back_to_rzmre_minus_rzche` | 无 `rz_net` 但有 `rzmre/rzche` 时，结果按 `sum(rzmre-rzche) / circ_mv_yuan` |
| `TestMfFlowRatio` | `test_returns_nan_when_flow_fields_missing` | 既无 `rz_net` 也无 `rzmre/rzche` 时返回与 `codes` 对齐的全 NaN |
| `TestMfFlowRatio` | `test_result_range_is_reasonable` | 用真实小截面 smoke test 验证绝大多数值在合理范围；超出时不直接失败，先输出诊断分位数 |

**PIT / 时间错位测试归属**：`lead_ratio` 不是单元测试内手工实现，统一由 `python -m scripts.run_factor_evaluation --output-dir reports/factor_evaluation_factor_impl_p0p1 --no-plots` 生成的 `shift_result.csv` 判读；单元测试只验证公式、字段、index 对齐和异常路径。

---

## 10. 文档更新

### 评估完成后必须更新

**更新 `docs/research/factor_roadmap/factor_research_guide.md`**：

在第 3 节（需要研究的新因子方向）的对应章节，添加评估结果记录，格式参照已有 Sprint 记录：

```markdown
#### asset_growth — Sprint X 完成（YYYY-MM-DD）

| Gate | 要求 | 实际结果 | 结论 |
|------|------|---------|------|
| Gate 0 覆盖率 | ≥ 80% | ?% | ? |
| Gate 1 IC_IR | ≥ 0.30 | ? | ? |
| Gate 2 lead_ratio | < 2.0x | ? | ? |
| Gate 3 验证期 | ≥ 0.20 | ? | ? |
```

**更新 `CLAUDE.md §1.1 当前状态`**：

- 若任何因子通过全部四道门：`因子池规模` +1
- 若有因子方向确认或问题解决：更新 `待解决已知问题`

---

## 11. 执行顺序总结

```
Step 0：数据探查（必须先做）
  └─ 确认 margin 是否有 rz_net，或是否可由 rzmre/rzche 计算；确认 industry_code/name 存在

T1：修复 accrual 分母（改 2-3 行，验证通过）
  └─ 依赖：get_quarterly_history 已可用

T2：实现 asset_growth（新函数 + 注册）
  └─ 金融股过滤不写在原始函数内，由 preprocess_factor 统一处理

T3：实现 share_issuance（新函数 + 注册）
  └─ 无外部依赖，可与 T2 并行

T4：实现 mf_flow_ratio 或 mf_congestion_neg（取决于 Step 0）
  └─ 依赖：Step 0 确认 rz_net 或 rzmre/rzche 字段可用性

T5：注册到 build_factor_panels.py
  └─ 依赖：T1-T4 完成

T6a：python -m scripts.build_factor_panels --dry-run --factors accrual asset_growth share_issuance mf_flow_ratio
T6b：python -m pytest tests/test_new_factors_p0p1.py -q
T6c：python -m scripts.build_factor_panels --factors accrual asset_growth share_issuance mf_flow_ratio
T6d：python -m scripts.run_factor_evaluation --output-dir reports/factor_evaluation_factor_impl_p0p1 --no-plots
T6e：读取隔离目录报告，记录 Gate 结果；不刷新主 final_factors.json

更新文档（factor_research_guide.md + CLAUDE.md §1.1）
```

---

## 12. 风险备忘

| 风险 | 说明 | 预案 |
|------|------|------|
| `get_quarterly_history` 兼容 `total_assets` | 该函数按最近报告期取 q0/q1/...，理论上适合存量字段，但新增用途需验证 | T1/T2 单测覆盖 q0/q4 分母和资产增速公式 |
| 财务因子金融过滤遗漏 | `asset_growth` 若未加入 `FINANCIAL_FACTORS`，预处理不会传入金融行业过滤参数 | T5 必须同时注册 `_FACTOR_BUILDERS` 与 `FINANCIAL_FACTORS`；构建后检查 diagnostics 的 `n_fin_filtered` |
| `accrual` 改进后仍可能不过 Gate 1 | 历史 IC_IR -0.148 极弱，均值资产未必能大幅改善 | 实测结果为准；若仍 < 0.30，在 research_guide 记录结论停止 |
| A 股实际 IC_IR 可能低于文献预期 | 学术估计基于美股；A 股因子实测结果差异可能较大 | 以实测为准，不以文献预期下结论 |
| `mf_flow_ratio` 错用融资买入额 | 若误用 `rzmre` 而不是 `rz_net`，会把买入总额当净买入，严重放大信号 | 单测必须覆盖 `rzmre=100, rzche=80, rz_net=20` 的公式断言 |
| 评估覆盖主产物 | 默认 `run_factor_evaluation` 会覆盖 `reports/factor_evaluation/final_factors.json` | 本计划只允许输出到 `reports/factor_evaluation_factor_impl_p0p1/`，主刷新需另行决策 |
| `mf_flow_ratio` 与 `turn_20d` 高相关 | 若 r > 0.70，Ridge 框架内两者会抢权重 | 评估时检查 factor_correlation.csv；若 r > 0.70 考虑只保留 IC_IR 更高的一个 |
| SUE（盈利意外）P0 但无法实现 | 需要 Wind 一致预期 EPS 数据，Tushare 不提供 | 本计划不包含；待确认 Wind 权限后单独处理 |

---

*本计划的所有评估均在训练集（2012-2020）和验证集（2021-2022）范围内进行，不触碰测试集（2023-2025，剩余 3 次）。*
