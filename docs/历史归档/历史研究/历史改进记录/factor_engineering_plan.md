# 因子库扩展与因子工程方案

> 背景：当前候选因子池共 39 个，通过四道门后仅剩 6 个，动量和质量维度存在结构性空缺。
> 本文档解决两个问题：① 哪些因子变种被漏检；② 如何通过因子工程产出更稳定的信号。
> 更新时间：2026-05-23

---

## 一、当前因子库的结构性问题

### 1.1 现有候选池全景

| 模块 | 因子数 | 实际候选 | 通过四道门 |
|---|---|---|---|
| `price_factors.py` | 12 | ret_1m, mom_6_1, mom_12_1, holder_chg, vol_60d, ivol_60d, max_ret, turn_20d, amihud, margin_ratio, short_ratio, large_net_inflow | ivol_60d, amihud |
| `financial_factors.py` | 15 | ep_ttm, bp, sp_ttm, cfp, fcfp, roe, roa, gross_margin, asset_turn, leverage, accrual, np_yoy, rev_yoy, roe_delta, q_roe | ep_ttm, cfp, rev_yoy |
| `alt_factors.py` | 12 | macd_cross, rsi_6/12, boll_pct, obv_chg_20d, hk_hold_ratio/chg, analyst_eps_revision, analyst_rating_chg, float_pct_30d, insider_net_buy, pledge_ratio | hk_hold_ratio |

**核心问题**：动量类的 5 个候选（mom_6_1, mom_12_1, ret_1m, macd_cross, rsi_6/12, boll_pct, obv_chg_20d）**全部失败**，不是因为动量效应在 A 股不存在，而是你测试的变种在月频 A 股下本就表现最差。

### 1.2 动量维度的根本问题

当前动量候选池的根本缺陷：

```
当前测试的动量变种：
  ❌ mom_6_1    → 传统价格动量，A股月频最不稳定
  ❌ mom_12_1   → 同上，2012-2015 几乎无效
  ❌ ret_1m     → 短期反转（20日），月内信号
  ❌ macd_cross → 日内/周频信号，月频 lead_ratio 14x
  ❌ rsi_6/12   → 同上，月频 lead_ratio 24-27x
  ❌ boll_pct   → 同上，月频 lead_ratio 17x

完全未测试的动量变种：
  ❓ 52周高点比率          → A股最鲁棒动量信号
  ❓ 行业调整动量          → 剔除板块效应的纯个股动量
  ❓ 风险调整动量          → Sharpe式动量，高波动股票权重下调
  ❓ 分析师预期修正（动量角度）→ 基本面动量的最前瞻信号
```

**为什么 52 周高点比纯价格动量在 A 股更有效**：  
纯价格动量（12-1 月收益）在 A 股容易被逆转事件（股灾、政策冲击）破坏；而 52 周高点因子衡量"当前价格距离历史高点的距离"，本质上是一个相对稀缺性指标——接近 52 周高点的股票往往有持续性资金流入支撑，散户和机构对"创新高"有心理锚点效应。

---

## 二、应新增的因子候选

### 2.1 动量维度（最优先，填补空缺）

#### 新因子 1：52 周高点比率（`high_52w`）

```python
def factor_high_52w(rebalance_date, codes):
    """
    52 周高点动量因子。
    
    公式：close_T / max(high, T-252交易日 到 T-20交易日)
    跳过最近 20 个交易日（消除短期反转干扰，与 mom_12_1 保持一致）。
    
    经济含义：接近 52 周高点的股票有强势动能支撑；散户心理锚点效应。
    预期方向：+（高比率预期正超额收益）
    
    A 股适用性：比纯价格动量更鲁棒，因为：
      1. 衡量相对稀缺性而非绝对涨跌幅
      2. 在股灾后市场中，高点比率高的股票是真正抗跌的，而非只是"涨了很多"
    """
    # 取 T 前 252 个交易日到 T 前 20 个交易日的最高价
    valid = _valid_dates_before(rebalance_date)
    if len(valid) < 252:
        return _nan_series(codes, "high_52w")
    
    window_end = valid[-20]        # T 前第 20 个交易日
    window_start = valid[-252]     # T 前第 252 个交易日（约 1 年）
    
    dq_window = load_daily_quote(window_start, window_end, codes=codes)
    dq_today = load_daily_quote(valid[-1], valid[-1], codes=codes)
    
    high_52w = dq_window["high_adj"].unstack("ts_code").max()
    close_t = dq_today["close_adj"].unstack("ts_code").iloc[-1]
    
    result = close_t / high_52w.replace(0, np.nan)
    result.name = "high_52w"
    return result.reindex(codes)
```

> **注意**：需要在 `daily_quote` 中有 `high_adj`（最高价后复权）字段，如果 Tushare 未提供后复权最高价，可用 `high / close * close_adj` 近似。

---

#### 新因子 2：行业调整动量（`ind_adj_mom`）

```python
def factor_ind_adj_mom(rebalance_date, codes, industry_map):
    """
    行业中性化后的个股动量（12-1 月）。
    
    公式：mom_12_1_i - mean(mom_12_1, 同申万一级行业)
    
    经济含义：剔除行业轮动效应后的纯个股超强/超弱动量。
    A 股 2016-2021 的行业轮动极强（消费→科技→周期的快速切换），
    纯价格动量大量被行业动量"噪音"污染。
    行业调整后的个股内部动量更稳定。
    
    预期方向：+（行业内跑赢的股票下期继续跑赢）
    industry_map：dict{ts_code → 申万一级行业代码}，在调用层提供
    """
    # 先取 mom_12_1 原始值
    raw_mom = factor_mom_12_1(rebalance_date, codes)
    if raw_mom.isna().all():
        return raw_mom.rename("ind_adj_mom")
    
    # 行业去均值
    ind = pd.Series(industry_map).reindex(raw_mom.index)
    ind_mean = raw_mom.groupby(ind).transform("mean")
    result = raw_mom - ind_mean
    result.name = "ind_adj_mom"
    return result
```

---

#### 新因子 3：风险调整动量（`mom_risk_adj`）

```python
def factor_mom_risk_adj(rebalance_date, codes):
    """
    Sharpe 式风险调整动量。
    
    公式：mom_12_1 / ivol_60d
    
    经济含义：相同涨幅下，低波动率的动量信号质量更高——
    高波动股的涨幅往往是随机游走，不具有持续性；
    低波动股的涨幅更可能是基本面驱动的持续性信号。
    
    A 股特殊价值：2015 年股灾中高波动+高动量股票跌幅最惨，
    就是因为它们的动量是杠杆驱动而非基本面驱动。
    风险调整后能过滤掉这类"虚假动量"。
    
    预期方向：+（高风险调整动量预期正超额收益）
    """
    mom = factor_mom_12_1(rebalance_date, codes)
    ivol = factor_ivol_60d(rebalance_date, codes)
    
    # 两者均有效才计算
    result = mom / ivol.replace(0, np.nan)
    result.name = "mom_risk_adj"
    return result
```

---

### 2.2 质量维度（补充稳定性指标）

当前质量因子的问题：只有"水平值"（ROE、ROA），缺少"稳定性"指标。水平值在周期顶部失效（高 ROE 但马上要回落），稳定性指标跨周期更鲁棒。

#### 新因子 4：ROE 稳定性（`roe_stability`，负向因子）

```python
def factor_roe_stability(rebalance_date, codes):
    """
    ROE 波动率（8 个季度滚动标准差的倒数）。
    
    低 ROE 波动率 = 盈利稳定 = 护城河更宽 = 预期正超额收益。
    取倒数后因子方向变为正向（值越大越好）。
    
    数据要求：需要 PIT 的至少 8 期季报 ROE 序列（即约 2 年历史）。
    2014 年之前的早期股票可能数据不足，返回 NaN。
    
    实现思路：
      从 indicator_pit 提取过去 8 个报告期的 roe，
      计算 std，取倒数（std=0 时用大值 999 代替，不填 NaN）。
    
    预期方向：+（ROE 越稳定，倒数越大，预期收益越高）
    """
    ip_raw = get_indicator_pit_raw()
    
    # 取 T 及之前的所有 PIT 快照，按 ts_code 分组取最新 8 期
    vis = ip_raw[ip_raw["ann_date"] <= rebalance_date]
    if codes:
        vis = vis[vis["ts_code"].isin(codes)]
    
    # 每只股票取最新 8 期 roe
    vis_sorted = vis.sort_values(["ts_code", "end_date"])
    
    def _roe_std(grp):
        vals = grp["roe"].dropna().tail(8)
        if len(vals) < 4:  # 至少 4 期才有意义
            return np.nan
        return vals.std(ddof=1)
    
    std_series = vis_sorted.groupby("ts_code").apply(_roe_std)
    # 取倒数，std=0（完全稳定）用 999
    result = 1.0 / std_series.replace(0, 999.0)
    result.name = "roe_stability"
    return result.reindex(codes)
```

---

#### 新因子 5：Piotroski F-Score（`piotroski_f`，综合质量评分）

```python
def factor_piotroski_f(rebalance_date, codes):
    """
    Piotroski F-Score：9 个二值财务健康指标的综合评分。
    
    原始论文：Piotroski (2000)，基于 A 股改版（剔除现金股息条件）。
    
    盈利能力（4 分）：
      F1 = ROA > 0                → 1
      F2 = CFO > 0                → 1
      F3 = ΔROA > 0 (同比)        → 1
      F4 = CFO > ROE（应计低）    → 1
    
    杠杆/流动性（3 分）：
      F5 = 资产负债率同比下降      → 1
      F6 = 流动比率同比上升        → 1
      F7 = 未增发新股              → 1（无大规模股权稀释）
    
    经营效率（2 分）：
      F8 = 毛利率同比上升          → 1
      F9 = 资产周转率同比上升      → 1
    
    总分 0-9，高分 = 财务健康。
    
    相比单一 ROE，F-Score 是多维度综合，验证期更稳定（不依赖单一指标）。
    预期方向：+（高 F-Score 预期正超额收益）
    
    注：需要同比数据，因此新上市 < 1 年的股票返回 NaN。
    """
    # 这里给出计算框架，完整实现需要调用 pit_loader 工具
    ip_raw = get_indicator_pit_raw()
    fp_raw = get_financial_pit_raw()
    
    latest = get_pit_latest(ip_raw, rebalance_date, codes)
    prev_year_date = rebalance_date - pd.DateOffset(years=1)
    prev_year = get_pit_latest(ip_raw, prev_year_date, codes)
    
    if latest.empty or prev_year.empty:
        return pd.Series(dtype=float, name="piotroski_f")
    
    scores = pd.DataFrame(index=pd.Index(codes, name="ts_code"))
    
    # F1: ROA > 0
    scores["f1"] = (latest["roa"].reindex(codes) > 0).astype(float)
    # F3: ΔROA > 0
    scores["f3"] = (
        latest["roa"].reindex(codes) > prev_year["roa"].reindex(codes)
    ).astype(float)
    # F5: 资产负债率下降
    scores["f5"] = (
        latest["debt_to_assets"].reindex(codes) < prev_year["debt_to_assets"].reindex(codes)
    ).astype(float)
    # F8: 毛利率上升
    scores["f8"] = (
        latest["grossprofit_margin"].reindex(codes) > prev_year["grossprofit_margin"].reindex(codes)
    ).astype(float)
    # F9: 资产周转率上升
    scores["f9"] = (
        latest["assets_turn"].reindex(codes) > prev_year["assets_turn"].reindex(codes)
    ).astype(float)
    
    # CFO 相关（来自 financial_pit）
    ttm_cfo = make_ttm(fp_raw, "n_cashflow_act", rebalance_date, codes)
    ttm_ni  = make_ttm(fp_raw, "n_income",       rebalance_date, codes)
    
    # F2: CFO > 0
    scores["f2"] = (ttm_cfo.reindex(codes) > 0).astype(float)
    # F4: CFO > 净利润（应计低）
    scores["f4"] = (ttm_cfo.reindex(codes) > ttm_ni.reindex(codes)).astype(float)
    
    # 汇总（NaN 的子分项不贡献得分，用 sum 跳过 NaN）
    f_score = scores.sum(axis=1, skipna=False)  # 若任一分项 NaN，总分也 NaN
    f_score.name = "piotroski_f"
    return f_score
```

---

### 2.3 成长维度（增加稳定性，降低对 rev_yoy 单一依赖）

#### 新因子 6：两年复合收入增速（`rev_cagr_2y`）

```python
def factor_rev_cagr_2y(rebalance_date, codes):
    """
    营收两年复合年化增速（CAGR）。
    
    公式：(rev_latest / rev_two_years_ago)^(1/2) - 1
    
    解决问题：rev_yoy（单年同比）在 2021 年后疫情恢复期出现严重的基数效应波动，
    两年 CAGR 通过穿越高低基数年份，得到更平滑的真实成长判断。
    
    2021 年高基数效应的影响：
      假设 2019→2020→2021 收入：100→90（疫情）→120（恢复）
      单年同比：2021 yoy = +33%（虚高）
      两年CAGR：(120/100)^0.5 - 1 = +9.5%（真实成长）
    
    预期方向：+（高两年 CAGR 预期正超额收益）
    """
    ip_raw = get_indicator_pit_raw()
    latest = get_pit_latest(ip_raw, rebalance_date, codes)
    
    two_years_ago = rebalance_date - pd.DateOffset(years=2)
    prev = get_pit_latest(ip_raw, two_years_ago, codes)
    
    if latest.empty or prev.empty:
        return pd.Series(dtype=float, name="rev_cagr_2y")
    
    rev_now  = latest["or_yoy"].reindex(codes)     # 先用 or_yoy 近似，理想做法见注
    rev_prev = prev["or_yoy"].reindex(codes)
    
    # TODO: 理想实现应从 financial_pit 取两年前的营收绝对值，用比率计算 CAGR
    # 当前近似：(1 + yoy_now/100) / (1 + yoy_prev/100) - 1（简化版）
    cagr = ((1 + rev_now / 100) * (1 + rev_prev / 100)) ** 0.5 - 1
    cagr.name = "rev_cagr_2y"
    return cagr
```

> **注意**：理想的 `rev_cagr_2y` 应从 `financial_pit` 取绝对收入数值后计算比率，上面用同比近似只是临时方案，精度较差，建议实现时直接取收入字段。

---

## 三、因子工程方法：让现有因子更稳定

**核心思想**：新建原子因子的边际回报递减（已有 39 个），因子工程的回报更高——它让现有信号更稳定，而不是叠加更多噪音。

以下三类方法按实施难度排序。

---

### 3.1 方法一：时间序列平滑（最容易，立即可行）

**问题**：月频因子的月间跳跃较大，导致 IC 序列方差高，IC_IR 偏低。应计利润、ROE 变化等因子的季报频率更新导致 IC 序列产生季节性噪音。

**解决方案**：在因子计算后、IC 计算前，对因子截面值做指数加权平均（EWM）。

```python
def smooth_factor_ewm(
    factor_panel: pd.DataFrame,   # 行=调仓日，列=ts_code
    halflife: int = 2,            # 半衰期（期数），2 = 约 2 个月
) -> pd.DataFrame:
    """
    对因子时间序列做 EWM 平滑。
    
    对每只股票的时间序列做指数加权平均，半衰期 halflife 期。
    
    参数选择建议：
      halflife=1 → 快速衰减，接近原始因子（适合高频更新因子如 hk_hold）
      halflife=2 → 中等平滑（适合月度技术/情绪因子）
      halflife=3 → 慢速衰减（适合季频财务因子如 ROE）
    
    注意：平滑不改变截面排序的方向，只减小极值和噪音。
    必须确保 EWM 只使用历史数据（pandas ewm 默认 min_periods=0，是因果的）。
    
    Returns:
        平滑后的因子 panel，index/columns 与输入相同
    """
    return factor_panel.ewm(halflife=halflife, min_periods=1).mean()
```

**预期效果**：对财务类因子（rev_yoy、roe_delta），平滑后 IC_IR 通常提升 0.05-0.15，因为季报发布产生的跳跃噪音被抑制。

---

### 3.2 方法二：维度内合成因子（中等难度，效果显著）

**核心理论**：若同一维度内有多个相关因子，不把它们分别送入四道门检验，而是先合成一个"维度综合因子"再检验。

合成后的综合因子：
- IC_IR ≈ √n × IC_IR_avg（理论值，独立因子）
- 即使每个子因子 IC_IR 偏低，综合因子的 IC_IR 会更高
- 减少多重检验的 BH 校正惩罚

#### 动量合成因子（`momentum_composite`）

```python
def factor_momentum_composite(rebalance_date, codes, industry_map):
    """
    动量维度综合因子（三个动量变种等权合成）。
    
    合成逻辑：
      Step 1: 分别计算 high_52w、ind_adj_mom、mom_risk_adj
      Step 2: 每个子因子独立做截面 MAD 去极值 + 标准化（z-score）
      Step 3: 等权平均（若某子因子覆盖率 < 30%，该因子不参与当期合成）
    
    设计理念：等权合成而非 IC_IR 加权的原因是
    训练期内这三个子因子都没有单独通过检验，无法可靠地估计各自的 IC_IR。
    等权合成是最保守、最不容易过拟合的方案。
    """
    sub_factors = {}
    sub_factors["high_52w"] = factor_high_52w(rebalance_date, codes)
    sub_factors["ind_adj_mom"] = factor_ind_adj_mom(rebalance_date, codes, industry_map)
    sub_factors["mom_risk_adj"] = factor_mom_risk_adj(rebalance_date, codes)
    
    # 每个子因子截面标准化
    z_scores = []
    for name, sf in sub_factors.items():
        valid_ratio = sf.notna().mean()
        if valid_ratio < 0.30:  # 覆盖率不足时跳过
            continue
        # 截面 MAD 去极值
        med = sf.median()
        mad = (sf - med).abs().median()
        sf_clipped = sf.clip(lower=med - 3*1.4826*mad, upper=med + 3*1.4826*mad)
        # z-score
        z = (sf_clipped - sf_clipped.mean()) / (sf_clipped.std() + 1e-8)
        z_scores.append(z)
    
    if not z_scores:
        return pd.Series(np.nan, index=pd.Index(codes, name="ts_code"),
                        name="momentum_composite")
    
    composite = pd.concat(z_scores, axis=1).mean(axis=1)
    composite.name = "momentum_composite"
    return composite
```

#### 质量合成因子（`quality_composite`）

```python
def factor_quality_composite(rebalance_date, codes):
    """
    质量维度综合因子（水平 + 稳定性 + 盈利质量三个角度合成）。
    
    子因子：
      - roe（盈利能力水平，已有）
      - roe_stability（盈利稳定性，新建）
      - accrual（盈利质量，已有，取负值使方向一致）
    
    roe 和 roe_stability 的组合逻辑：
      高 ROE 且 ROE 稳定 = 真正的护城河
      高 ROE 但 ROE 不稳 = 周期顶部的伪高质量（正是 2021 年失效的原因）
    
    这三个子因子的相关性：
      roe vs roe_stability：弱正相关（高质量公司往往也稳定）
      roe vs accrual：弱负相关（真实盈利的公司应计少）
      → 合成后能有效过滤"纸面利润"公司
    """
    sub_factors = {
        "roe": factor_roe(rebalance_date, codes),
        "roe_stability": factor_roe_stability(rebalance_date, codes),
        "accrual_neg": -factor_accrual(rebalance_date, codes),   # 反向，低应计=好
    }
    
    # 标准化后等权合成（同上）
    ...
```

---

### 3.3 方法三：因子正交化（高级，消除维度间相关性）

**问题**：价值因子（ep_ttm）和盈利质量因子（ROE）之间天然有相关性，合成信号时这部分共同方差被重复计数。

**解决方案**：构建"正交化因子"——去掉 A 因子中 B 因子已能解释的部分，只保留 A 的独立信息。

这比两个因子的简单叠加有两个优势：
1. 独立信息的 IC_IR 实际上比原始因子更高（因为减少了被其他因子解释的噪音）
2. 因子相关性接近 0，合成时无信息重叠

#### 质量调整价值因子（`quality_adj_value`）

```python
def factor_quality_adj_value(ep_ttm: pd.Series, roe: pd.Series) -> pd.Series:
    """
    质量调整后的价值因子（横截面回归残差法）。
    
    做法：在截面上用 roe（质量）回归 ep_ttm（价值），取残差。
    残差含义：在控制了 ROE 水平之后，ep_ttm 的剩余"便宜程度"。
    
    即：找到那些"按 ROE 调整后依然便宜"的股票，而不是"高 ROE 且低 P/E"
    两者方向往往一致，但前者更精准——它排除了"因为质量差所以便宜"的情况。
    
    实现：
      roe_z = (roe - roe.mean()) / roe.std()
      ep_z  = (ep_ttm - ep_ttm.mean()) / ep_ttm.std()
      residual = ep_z - beta * roe_z  (简单 OLS 截面回归)
    """
    from scipy import stats
    
    common = ep_ttm.dropna().index.intersection(roe.dropna().index)
    if len(common) < 30:
        return pd.Series(np.nan, index=ep_ttm.index, name="quality_adj_value")
    
    ep_z  = (ep_ttm[common] - ep_ttm[common].mean()) / ep_ttm[common].std()
    roe_z = (roe[common] - roe[common].mean()) / roe[common].std()
    
    slope, intercept, *_ = stats.linregress(roe_z, ep_z)
    residual = ep_z - (slope * roe_z + intercept)
    residual.name = "quality_adj_value"
    return residual.reindex(ep_ttm.index)
```

**经济直觉**：`quality_adj_value` 选出的股票是"ROE 不特别高，但估值极低"的低估值股票，这类股票不会在"高 ROE 周期顶部翻转"时崩溃。

---

## 四、实施路线图

### 阶段一：新因子构建（P0，填补动量维度）

| 任务 | 新增因子 | 实现难度 | 在哪个文件 |
|---|---|---|---|
| 52 周高点比率 | `high_52w` | 低（复用现有数据） | `price_factors.py` |
| 行业调整动量 | `ind_adj_mom` | 低（需传入 industry_map） | `price_factors.py` |
| 风险调整动量 | `mom_risk_adj` | 极低（两个现有因子相除） | `price_factors.py` |
| 动量合成因子 | `momentum_composite` | 中 | `price_factors.py` 或新建 `composite_factors.py` |

**关键决策**：建议先将三个动量子因子加入候选池，**分别**跑一次四道门检验，再决定是否需要合成。如果子因子中至少有一个通过，则不必合成（单因子已足够）；如果全部边界触发（IC_IR 0.3-0.4 附近），再考虑合成。

### 阶段二：质量维度补强（P1）

| 任务 | 新增因子 | 实现难度 | 备注 |
|---|---|---|---|
| ROE 稳定性 | `roe_stability` | 中（需历史 PIT 序列） | 替代"水平值 ROE"的过拟合风险 |
| Piotroski F-Score | `piotroski_f` | 中（多指标组合） | 综合质量，跨周期最稳定 |

### 阶段三：因子工程（P2，在完成阶段一/二后评估）

| 任务 | 操作 | 触发条件 |
|---|---|---|
| 时间序列平滑（EWM） | 对财务因子应用 halflife=2-3 | rev_yoy 或其他因子验证期 IC 波动过大时 |
| 质量合成因子 | roe + roe_stability + accrual 等权合成 | 三个子因子均未单独通过，但维度仍有缺失时 |
| 正交化因子 | ep_ttm 对 roe 的残差 | 价值+质量双因子合成后仍有高相关时 |

---

## 五、关于因子数量的上限问题

当前候选池 39 个，`CLAUDE.md` 中约束最终因子池 20-30 个。随着新增 5-6 个因子，候选池达到 44-45 个，仍在合理范围内。

**不应该无限扩充候选池**的三个原因：

1. **BH 校正惩罚**：候选池越大，BH 校正后的有效 p 值阈值越低，边界因子更难通过。
2. **过拟合风险**：候选池越大，"偶然发现有效因子"的概率越高（即使所有因子都是噪音，也会有几个碰巧通过）。
3. **计算成本**：每期多跑 5 个因子，10 年训练期 × 106 期，额外时间成本非零。

**实际建议**：阶段一新增 3-4 个动量变种（含合成因子），阶段二新增 2 个质量因子，候选池控制在 45 个以内，然后重跑四道门（修改后的版本）。目标最终因子池 8-10 个，覆盖 6-7 个维度。

---

## 六、对应代码修改位置

| 修改内容 | 文件 | 修改性质 |
|---|---|---|
| 新增 high_52w, ind_adj_mom, mom_risk_adj | `src/factors/price_factors.py` | 新增函数 + 注册到 `_PRICE_FACTOR_BUILDERS` |
| 新增 roe_stability, piotroski_f | `src/factors/financial_factors.py` | 新增函数 + 注册到 `_FACTOR_BUILDERS` |
| 新增合成因子（可选） | `src/factors/composite_factors.py`（新建） | 新建模块 |
| EWM 平滑（可选） | `src/factors/preprocess.py` | 新增 `smooth_factor_ewm` 函数 |
| 正交化（可选） | `src/factors/preprocess.py` | 新增 `orthogonalize` 函数 |
| 候选池配置更新 | `src/config.py` | 更新因子名称列表 |
| 评估脚本 | `scripts/run_factor_evaluation.py` | 添加新因子到评估批次 |

---

*参考文档：`teach/factor_evaluation_guide.md`，`teach/factor_system_dimensions.md`，`teach/factor_evaluation_improvement_plan.md`*
