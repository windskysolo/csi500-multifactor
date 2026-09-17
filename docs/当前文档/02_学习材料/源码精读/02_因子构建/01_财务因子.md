# 05 — 财务因子构建 `src/factors/financial_factors.py`

---

## 一、文件定位

```
所属 Part  : Layer 1（因子构建层）
在数据流中 : pit_loader → financial_factors → preprocess → combiner/ridge
被谁调用   : scripts/build_factor_panels.py（批量构建面板）
调用谁     : src/data/loader.py（get_financial_pit_raw / get_indicator_pit_raw / load_daily_basic）
             src/data/pit_loader.py（make_ttm / get_pit_latest / get_quarterly_history 等）
```

> ⚠️ **与 v1 的重大差异**：v1 文档描述的是 15 个因子（价值5+质量6+成长4）；v2 实现了 **22 个**财务因子，新增了 fcfp（自由现金流收益率）和 6 个"阶段4低风险因子"（roe_smoothed_4q、roe_delta_3q、roe_stability、rev_acceleration、gross_margin_trend、ep_vs_history）。同时 accrual 因子的分母从单点总资产改为平均总资产（修复了时序错位 bug）。

---

## 二、时间对齐与 PIT 假设

**所有财务因子只使用 T 日及之前已公告的财务数据**，由 `pit_loader._filter_visible` 在底层执行。

特殊情况：
- `dy_ttm`（股息率）来自 `daily_basic.dv_ttm`，是 Tushare 预计算值，**非严格 PIT**（可能有 0-60 天前视偏差）。函数文档中已明确标注 ⚠️。
- 每只股票的"最新可用报告期"在同一调仓日可能不同（有的已有 Annual，有的只有 Q3），`pit_loader` 的逐股查找机制处理这种异质性。

---

## 三、模块顶部

```python
from src.data.loader import (
    get_financial_pit_raw,
    get_indicator_pit_raw,
    load_daily_basic,
)
from src.data.pit_loader import (
    get_field_yoy_delta, get_pit_latest,
    get_quarterly_history, get_roe_delta, make_ttm,
)

_WAN_TO_YUAN: float = 10_000.0          # daily_basic.total_mv 万元→元
MIN_QUARTERS_STABILITY: int = 4          # roe_stability 所需最少季度数
```

---

## 四、核心函数逐一解析

### 辅助函数：`_get_total_mv_yuan(rebalance_date, codes)` （L52-70）

从 `daily_basic` 取当天总市值，转换为元。`total_mv` 在 Tushare 中单位是**万元**，`financial_pit` 的货币字段是**元**，计算比率前必须统一单位：

```python
mv_yuan = mv_wan * _WAN_TO_YUAN   # ×10000
```

---

### 4.1 价值类（6 个）

#### `factor_ep_ttm` — E/P（TTM净利润/总市值）

预期方向：**+**（价值溢价，高 E/P 的股票预期超额收益）

```python
ttm_ni  = make_ttm(fp_raw, "n_income", rebalance_date, codes)   # 严格 PIT
mv_yuan = _get_total_mv_yuan(rebalance_date, codes)             # T 日收盘市值
ep      = ttm_ni / mv_yuan.reindex(ttm_ni.index)
```

为何用 E/P 而不用 P/E：E/P 对负利润股票有定义（E/P < 0 = 亏损），而 P/E 在负利润时没有经济意义。E/P 的横截面分布更接近正态，便于 MAD 去极值。

#### `factor_bp` — B/P（归母净资产/总市值）

净资产为资产负债表**存量指标**，直接取最新 PIT 快照（无需 TTM 化）：
```python
equity = latest["total_hldr_eqy_exc_min_int"]   # 归母净资产（元）
```
净资产可为负（高杠杆或 ST 股），winsorize 后仍有效。预期方向：**+**。

#### `factor_sp_ttm` — S/P（TTM营收/总市值）

TTM 营收需要 `make_ttm(fp_raw, "revenue", ...)`，原因同 ep_ttm。预期方向：**+**。

#### `factor_dy_ttm` — 股息率

直接来自 `daily_basic.dv_ttm`（Tushare 预计算），非严格 PIT，有 0-60 天前视偏差。单位：百分比（2.5 = 2.5%）。预期方向：**+**（高股息溢价）。

#### `factor_cfp` — 经营现金流收益率（TTM CFO/总市值）

`n_cashflow_act`（经营活动现金流净额）是累计流量，需 TTM 化。现金流比利润更难造假（需要实际资金出入），是衡量盈利质量的重要指标。预期方向：**+**。

#### `factor_fcfp` — 自由现金流收益率（TTM FCF/总市值）（v2 新增）

`free_cashflow = n_cashflow_act - 资本支出`（Tushare 预计算）。相比 `cfp` 扣除了维持性资本支出，对重资产行业（制造业、地产）区分度更高。预期方向：**+**。

---

### 4.2 质量类（6 个）

#### `factor_roe` / `factor_roa` / `factor_gross_margin` / `factor_asset_turn`

均来自 `indicator_pit`，Tushare 预计算值：
```python
ip_raw = get_indicator_pit_raw()
latest = get_pit_latest(ip_raw, rebalance_date, codes)
```

| 函数 | 字段 | 单位 | 方向 |
|---|---|---|---|
| factor_roe | `roe` | % | + |
| factor_roa | `roa` | % | + |
| factor_gross_margin | `grossprofit_margin` | % | + |
| factor_asset_turn | `assets_turn` | 倍数 | + |

金融股提醒：`gross_margin` 和 `asset_turn` 在金融行业的财务意义与非金融行业不同。`preprocess_factor` 调用时需传入 `fin_sector_codes=FINANCIAL_SECTOR_CODES`。

#### `factor_leverage` — 资产负债率

来自 `indicator_pit.debt_to_assets`（百分比，如 60.5 = 60.5%）。预期方向：**−**（低杠杆溢价）。金融股天然高杠杆（>90%），`preprocess` 中已过滤。

#### `factor_accrual` — 应计项目（Sloan 异象，已修复）

**公式**：`(TTM净利润 - TTM经营现金流) / 平均总资产`

**分母修复**（v2 相比 v1 的关键 bug 修复）：

```python
hist_assets = get_quarterly_history(fp_raw, "total_assets", ..., n_quarters=5)
avg_assets = ((hist_assets["q0"] + hist_assets["q4"]) / 2)   # 当期与4季前的均值
```

v1 版本用的是单点 `total_assets`（同一时点），与分子的"12个月累计"产生了时序错位。正确做法是 Sloan(1996) 原始定义：分母用**平均**资产（首尾均值）。`q0` = 最新季，`q4` = 4个季报之前，均值近似"过去12个月的平均总资产"。

预期方向：**−**（高应计 = 盈利依赖非现金项目 = 盈利质量差 = 后续收益更低）。

---

### 4.3 成长类（4 个）

#### `factor_np_yoy` / `factor_rev_yoy`

直接来自 `indicator_pit.netprofit_yoy` / `or_yoy`（百分比，Tushare 预计算同比增速）。预期方向：**+**。

极端值常见（分母接近 0 的小公司增速可能 >1000%），`winsorize_mad` 会截断。

#### `factor_roe_delta` — ROE 同比变化

来自 `pit_loader.get_roe_delta`（当期ROE - 上年同期ROE）。预期方向：**+**（ROE 改善趋势的预测力）。

#### `factor_q_roe` — 单季 ROE

来自 `indicator_pit.q_roe`（当季净利润/平均净资产，百分比）。单季值对近期变化更敏感，方差大，winsorize 尤为重要。预期方向：**+**。

---

### 4.4 阶段4扩展类（6 个，v2 新增）

#### `factor_roe_smoothed_4q` — 4季单季ROE均值

```python
hist = get_quarterly_history(ip_raw, "q_roe", ..., n_quarters=4)
result = hist.mean(axis=1, skipna=True)
```

去除单季一次性事件干扰（如资产处置收益），表达"持续盈利能力"。与 `q_roe`（灵敏快照）互补：`roe_smoothed_4q` 更稳健。预期方向：**+**。

#### `factor_roe_delta_3q` — ROE 3季改善速度

```python
hist = get_quarterly_history(ip_raw, "q_roe", ..., n_quarters=4)
result = hist["q0"] - hist["q3"]   # 最新季 - 3季前
```

与 `roe_delta`（同比变化）的区别：`roe_delta` 反映跨年趋势；`roe_delta_3q` 反映近期加速/减速，时间窗口更短。预期方向：**+**。

#### `factor_roe_stability` — ROE稳定性（标准差取负）

```python
hist = get_quarterly_history(ip_raw, "q_dt_roe", ..., n_quarters=MIN_QUARTERS_STABILITY)
std_vals = hist.std(axis=1, ddof=1)
result = -std_vals   # 负号：std 越低 → 稳定性越高 → 因子值越高
```

使用**扣非单季ROE**（`q_dt_roe`）而非普通 ROE，避免非经常性损益（如资产出售）干扰稳定性评估。有效季度数 < 4 时返回 NaN。预期方向：**+**（稳定=高质量）。

#### `factor_rev_acceleration` — 营收增速加速度（二阶导数）

```python
hist = get_quarterly_history(fp_raw, "revenue", ..., n_quarters=6)
yoy_curr = (hist["q0"] - hist["q4"]) / denom_curr   # 本期同比
yoy_prev = (hist["q1"] - hist["q5"]) / denom_prev   # 上期同比
result = yoy_curr - yoy_prev                          # 加速度
```

分母用绝对值（`denom = hist["q4"].abs()`），避免去年亏损时方向颠倒。预期方向：**+**（成长加速是看多信号）。需要 6 期季报历史。

#### `factor_gross_margin_trend` — 毛利率同比趋势

```python
delta = get_field_yoy_delta(ip_raw, "grossprofit_margin", ...)
```

用同比（year-over-year）而非环比，自然消除季节性偏差。与 `gross_margin`（水平）互补：一个衡量护城河宽度，一个衡量是否在扩大。预期方向：**+**。

#### `factor_ep_vs_history` — 相对历史估值

当前 EP_TTM / 过去 3 年 EP_TTM 均值。经济含义：> 1 表示当前盈利收益率高于历史水平（相对自身历史便宜）。与横截面 `ep_ttm` 互补：`ep_ttm` 衡量"横截面中哪些股票便宜"，`ep_vs_history` 衡量"相对该股票自身历史是否便宜"。

---

## 五、内部辅助函数

| 函数 | 说明 |
|---|---|
| `_get_total_mv_yuan(date, codes)` | 从 daily_basic 获取总市值，转换为元 |

---

## 六、落盘产物

`financial_factors.py` 本身不落盘。`scripts/build_factor_panels.py` 调用这些函数并将结果按因子分别写入 `data/processed/factor_panels/<factor_name>.parquet`。

---

## 七、相关测试

- `tests/test_evaluation.py`：间接覆盖（通过 IC 评估测试因子输出的有效性）
- 建议补充：`test_accrual_avg_assets` — 验证分母是平均总资产而非单点值

---

## 八、失败与降级路径

| 场景 | 行为 |
|---|---|
| 某只股票财务数据完全缺失 | 该股票该因子值为 NaN（不中断） |
| `daily_basic` 无该调仓日数据 | 价值类因子（分母=总市值）返回全 NaN |
| `make_ttm` 年报年份不匹配 | 该股票 TTM 为 NaN（已在 pit_loader 处理）|
| 历史季度数不足 `n_quarters` | `get_quarterly_history` 补 NaN |

---

## 九、数据流图

```
get_financial_pit_raw()  → fp_raw（完整 financial_pit）
get_indicator_pit_raw()  → ip_raw（完整 indicator_pit）
load_daily_basic(T, T)   → mv_wan（总市值，万元）

  make_ttm(fp_raw, "n_income", T)     → 价值类（ep_ttm / sp_ttm / cfp / fcfp）
  get_pit_latest(fp_raw, T)           → 价值类（bp）
  get_pit_latest(ip_raw, T)           → 质量类（roe / roa / gross_margin / asset_turn / leverage）
  get_quarterly_history(fp_raw, ...) → accrual（avg_assets）
  get_quarterly_history(ip_raw, ...) → roe_smoothed_4q / roe_delta_3q / roe_stability
  get_roe_delta(ip_raw, T)            → roe_delta
  get_field_yoy_delta(ip_raw, ...)    → gross_margin_trend

每个 factor_xxx() 函数 → ts_code 为 index 的 Series（原始未处理值）
    ↓ preprocess_factor（MAD + Z-score + 中性化）
    ↓ 写入 data/processed/factor_panels/<name>.parquet
```

---

## 十、领域知识补充

**为什么用 E/P、B/P 而不是 P/E、P/B？**

在量化选股中，"高 P/E = 贵 = 坏信号"。如果用 P/E 作为因子，"因子值越高越差"，与通常预期相反，容易混淆方向。改用 E/P、B/P 后，"因子值越高越好"，方向统一。另外，E/P 对负值有定义（亏损公司 E/P < 0），P/E 无法处理负利润。

**应计异象（Sloan Accrual Anomaly）**：

Richard Sloan（1996）发现：高应计项目（accrual = 净利润 - 经营现金流）的公司未来收益更低。原因：高应计意味着净利润中有大量"纸面利润"（应收账款、存货评估增值等非现金项目），这类利润不可持续，市场会在下一期修正定价。因子方向为**负**（高应计 = 低质量 = 低收益）。
