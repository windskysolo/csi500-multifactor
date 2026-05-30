# 01 — 全局配置中心 `src/config.py`

---

## 一、文件定位

```
所属 Part  : Layer 0（数据基础层）
在数据流中 : 最底层，无任何依赖
被谁调用   : src/ 下所有模块通过 from src import config as cfg 引用
调用谁     : 无
```

这是整个项目的"唯一参数来源"。所有时间区间、成本参数、阈值在此定义一次，禁止在其他地方硬编码数字。

> ⚠️ **与 v1 文档的重要差异**：v1 文档中几乎所有数值均已过时（训练区间、优化参数、信号参数均有变更），不可参照 v1 文档里的具体数字。

---

## 二、时间对齐与 PIT 假设

`config.py` 本身不做数据计算，不涉及 PIT。但它定义的时间边界直接控制所有模块的 PIT 安全性：

- `TRAIN_START/END`、`VALID_START/END`、`TEST_START/END` 是各类评估函数的过滤边界
- 一旦训练期确定并开始调参，这三组常量**不可修改**（修改等同于偷看答案）

---

## 三、模块顶部

```python
from pathlib import Path   # 跨平台路径对象，/运算符自动处理路径拼接
import pandas as pd        # 仅用于构造 pd.Timestamp（全模块统一日期类型）
```

只有两个 import，说明此文件不做任何计算，纯常量声明。

---

## 四、核心常量逐一解析

### 4.1 路径常量（L12-14）

```python
ROOT      = Path(__file__).parent.parent   # config.py 的上两级 = 项目根目录
DATA_RAW  = ROOT / "data" / "raw"
DATA_PROC = ROOT / "data" / "processed"
```

推导：`config.py` 位于 `src/`，`.parent` = `src/`，`.parent.parent` = 项目根目录。所有模块通过 `cfg.DATA_PROC / "xxx.parquet"` 构建路径，不硬编码绝对路径，项目可任意移动。

### 4.2 样本期切分（L19-29）

```python
MARKET_START = pd.Timestamp("2011-01-01")   # 行情数据起点（训练期需要1年行情缓冲）
MARKET_END   = pd.Timestamp("2025-12-31")

EARLY_START  = pd.Timestamp("2010-01-01")   # 财务/股东数据提前下载起点

TRAIN_START  = pd.Timestamp("2012-01-01")
TRAIN_END    = pd.Timestamp("2020-12-31")   # ← 9年训练期
VALID_START  = pd.Timestamp("2021-01-01")
VALID_END    = pd.Timestamp("2022-12-31")   # ← 2年验证期
TEST_START   = pd.Timestamp("2023-01-01")
TEST_END     = pd.Timestamp("2025-12-31")   # ← 3年测试期（≤2次运行）
```

**为何使用 `pd.Timestamp` 而不是字符串？**

`pd.Timestamp` 支持直接与 DataFrame 日期索引做比较运算（`df.loc[cfg.TRAIN_START:cfg.TRAIN_END]`），而字符串需要先转换。全系统统一使用 `pd.Timestamp` 消除隐式类型转换带来的 bug 风险。

**为何 `EARLY_START = 2010-01-01`，比训练期早 2 年？**

财务数据需要"提前量"：
1. PIT 使用公告日（`f_ann_date`），公告日通常比报告期晚 1-4 个月
2. TTM 计算需要上年同期数据（如 2012-01 的 Q3 TTM 需要 2011-09 的财报）
3. 因此 2012 年训练开始前，财务历史需追溯到 2010 年

**为何 `MARKET_START = 2011-01-01`，比训练期早 1 年？**

量价因子需要历史窗口，例如 `mom_12_1` 需要 252 个交易日（约 1 年）历史行情。训练期第一个调仓日（2012 年某月末）计算动量因子，需要回看 2011 年的行情数据。

**参数冻结原则（最重要）**：

`TRAIN_*`、`VALID_*`、`TEST_*` 切分点在项目开始时确定，此后**绝对不允许修改**。任何基于测试期结果反向调整参数的操作，都会让测试期实际上变成第三个训练集，彻底破坏样本外有效性验证。

### 4.3 交易成本（L35-40）

```python
STAMP_DUTY_CUT_DATE = pd.Timestamp("2023-08-28")
STAMP_DUTY_BEFORE   = 0.001     # 10 bps，卖出单边
STAMP_DUTY_AFTER    = 0.0005    # 5 bps，卖出单边

COMMISSION_RATE = 0.00025       # 双边各 2.5 bps
SLIPPAGE_RATE   = 0.0008        # 8 bps（保守估计）
```

**2023-08-28 印花税减半的背景**：证监会于 2023-08-27 宣布自次日（08-28）起将股票印花税税率由 0.1% 降至 0.05%（卖出单边）。这是自 2008 年以来首次下调。回测**必须**在此日期前后使用不同税率，否则 2023 年后的成本被高估 50%（相差 5bps）。

**印花税只对卖方收取**，佣金买卖双方各付。成本汇总：

| 操作 | 印花税（≥2023-08-28）| 佣金 | 滑点 | 合计 |
|---|---|---|---|---|
| 卖出 100 万 | 500 元（5bps）| 250 元（2.5bps）| 800 元（8bps）| 1550 元 |
| 买入 100 万 | 0 | 250 元（2.5bps）| 800 元（8bps）| 1050 元 |

`SLIPPAGE_RATE = 0.0008`（8bps）是保守估计，中证500成分股实际滑点通常 3-5bps，保守估计留安全边际。

### 4.4 行业分类（L45-48）

```python
INDUSTRY_SRC = "SW2021"
INDUSTRY_UNCLASSIFIED_CODE = "999999.SI"
INDUSTRY_UNCLASSIFIED_NAME = "未分类"
```

申万 SW2021 一级行业共 31 个（代码格式 `801xxx.SI`）。中性化时以这 31 个行业哑变量 + `log(自由流通市值)` 作为控制变量。`999999.SI` 是极少数无法归类股票的占位符，这类股票做中性化时残差为 NaN。

### 4.5 股票过滤规则（L53）

```python
NEW_STOCK_DAYS = 180
```

上市不足 180 天（约 6 个月）的股票排除出可投资域，原因：
1. **IPO 效应**：新股前几个月价格异常波动，因子无预测力
2. **历史窗口不足**：动量因子（mom_12_1）需要 252 个交易日历史
3. **财务数据缺失**：上市不足一年的公司季报极少

### 4.6 因子预处理参数（L59-63）

```python
FINANCIAL_SECTOR_CODES: frozenset[str] = frozenset({
    "801780.SI",  # 银行
    "801790.SI",  # 非银金融（券商、保险）
})
MIN_NEUTRALIZE_STOCKS: int = 30
```

**银行/非银金融特殊处理**：财务因子假设"高负债 = 高风险"，但银行的负债就是存款（业务本质，不是风险信号）。银行资产负债率天然 >90%，若不排除会"污染"横截面因子分布。这两个行业的财务因子在 `preprocess_factor()` 中统一置 NaN。

**量价因子**传入 `fin_sector_codes=None`（不过滤），因为量价信号对金融股同样有效。

`MIN_NEUTRALIZE_STOCKS = 30`：OLS 回归的最少有效观测数。某些极端情景（市场恐慌期大量停牌）下有效股票可能不足 30 只，此时跳过中性化直接返回原始因子值，避免少样本 OLS 的不稳定性。

### 4.7 信号合成参数（L78-81）

```python
SIGNAL_MIN_IC_HISTORY: int = 12
SIGNAL_WINDOW_MONTHS: int = 24
SIGNAL_DEFAULT_MIN_VALID: int = 8
SIGNAL_MIN_VALID_FACTORS: int = 5
```

**为何 IC_IR 加权需要至少 12 期历史？**

IC_IR = IC 序列均值 / 标准差。样本数 < 12 时，标准差估计方差极大（由于 `var(s²) ∝ 1/n`），IC_IR 的置信区间非常宽，用它加权的效果不如直接等权。冷启动前 12 个月用等权合成，第 13 个月开始切换到 IC_IR 加权。

`SIGNAL_WINDOW_MONTHS = 24`：IC_IR 加权时只看最近 24 个月的 IC 历史（滚动窗口），更侧重近期因子有效性，而不用累积全训练期。

`SIGNAL_MIN_VALID_FACTORS = 5`：合成信号时每只股票至少需要 5 个因子有效值。如果某股票大量因子缺失（如新上市公司财务数据不足），该股票的合成信号可信度太低，置 NaN 不参与选股。

### 4.8 单因子评价参数（L86-93）

```python
EVAL_HIGH_CORR_THRESHOLD: float = 0.70
EVAL_SHARPE_LS_THRESHOLD: float = 0.50
EVAL_IC_EFFECTIVE_CRITERIA: dict = dict(
    min_ic_ir=0.30,     # Gate 1：IC_IR 下限
    min_ic_mean=0.02,   # Gate 1：|IC 均值| 下限
    max_p_bh=0.05,      # Gate 1：BH 校正后 p 值上限
    min_dir=0.55,       # Gate 1：正向期数比例下限
)
```

**四个 IC 有效性阈值的来源**：

| 参数 | 值 | 含义 |
|---|---|---|
| `min_ic_ir` | 0.30 | IC_IR 类似 Sharpe。0.30 对应 t 统计量 ≈ 0.30×√96 ≈ 2.94（训练期 96 个月），约 p=0.003，统计显著 |
| `min_ic_mean` | 0.02 | Spearman IC 均值 2% 是实践中"有使用价值"的最低门槛；低于此值信号弱到可能被交易成本淹没 |
| `max_p_bh` | 0.05 | BH（Benjamini-Hochberg）多重检验校正后的 p 值。同时检验 20+ 个因子，单个 p<0.05 可能是随机偶然，BH 校正控制整体假阳性率 |
| `min_dir` | 0.55 | IC 方向一致性。55% 意味着多数时候方向是对的；低于 50% 说明因子方向不稳定，在不同市场环境下会翻转 |

`EVAL_HIGH_CORR_THRESHOLD = 0.70`：因子横截面相关系数 >0.70 的两个因子信息高度重叠，保留 IC_IR 更高的那个，另一个标记为冗余。

### 4.9 组合优化参数（L98-103）

```python
OPT_TE_TARGET_ANNUAL: float = 0.06   # 年化跟踪误差目标 6%（O1 网格实验结果，2026-05-24）
OPT_INDUSTRY_MAX_DEV: float = 0.03   # 行业权重偏离上限（绝对值）
OPT_SINGLE_MAX_DEV: float = 0.015    # 单股权重偏离上限（绝对值）
OPT_TOPN: int = 50                   # L3/TopN EW 选股数量
OPT_TURNOVER_LAMBDA: float = 0.005   # 换手成本惩罚系数（λ_TC）（2026-05-25 冻结为 v1.1）
```

**`OPT_TE_TARGET_ANNUAL = 6%`**（注意：v1 文档写的是 5%，已变更）：年化跟踪误差（TE）是策略超额收益的年化标准差。6% 意味着：
- 68% 置信区间：超额年收益落在 [-6%, +6%] 之间（1σ）
- 月频等效：`6% / √12 ≈ 1.73%`

这个参数是 2026-05-24 通过网格实验（O1 检验）确定的，比早期版本的 5% 略宽松，给信号更多表达空间。

**`OPT_INDUSTRY_MAX_DEV = 3%`**（v1 是 2%）：任一申万一级行业的组合权重与基准权重之差的绝对值 ≤ 3%。3% 相对于中证500中大多数行业 3-8% 的权重来说是适中约束。

**`OPT_SINGLE_MAX_DEV = 1.5%`**（v1 是 1%）：单股权重偏离基准权重的上限。中证500中单股基准权重约 0.2%，1.5% 偏离意味着单股最多持有约 1.7%（基准的 8.5 倍）。

**`OPT_TURNOVER_LAMBDA = 0.005`**（v1 无此参数，为新增）：在优化目标函数中加入换手成本惩罚项。`lambda × ||w_t - w_{t-1}||₁` 是对高换手的惩罚，平衡"追随信号"和"减少摩擦成本"。0.005 是 2026-05-25 换手率网格实验的最优值，已冻结为 v1.1 版本参数。

### 4.10 工程参数（L107-108）

```python
RANDOM_SEED   = 42
MAX_READ_WORKERS = 8
```

`RANDOM_SEED = 42`：固定所有随机过程的种子，使结果可复现。用到的地方：numpy 随机操作、sklearn 的 `LedoitWolf`（内部有随机化步骤）。

`MAX_READ_WORKERS = 8`：并行读取 CSV 文件的线程数，用于 `scripts/csv_to_parquet.py` 批量转换阶段。

### 4.11 备选数据子目录（L113-126）

```python
ALT_DATA_SUBDIRS: list[str] = [
    "hk_hold", "analyst_rc", "share_float", "holder_trade",
    "cyq_perf", "pledge_stat", "hsgt_flow", "top_list",
    "top_inst", "block_trade", "stk_surv", "fina_mainbz",
]
```

12 类备选数据来源，对应 `data/raw/` 下的子目录，也对应 `loader.py` 中的新增加载函数（`load_hk_hold`、`load_analyst_rc_pit` 等）。`fina_mainbz`（主营业务收入）目前无对应 loader（注释"暂不提供"），其余 11 类均有 loader 实现。

---

## 五、内部辅助函数

`config.py` 无任何函数，全部是模块级常量。

---

## 六、落盘产物

无。`config.py` 不写入任何文件。

---

## 七、相关测试

无直接测试。配置值的正确性通过下游模块的测试间接验证（如 `tests/test_pit.py` 依赖时间常量）。

---

## 八、失败与降级路径

不适用（纯常量定义，无运行时逻辑）。

---

## 九、数据流图

```
src/config.py
    ↓ from src import config as cfg
src/data/loader.py
src/data/pit_loader.py
src/data/universe.py
src/factors/financial_factors.py  price_factors.py  alt_factors.py
src/factors/preprocess.py
src/evaluation/*.py
src/signal/*.py
src/portfolio/*.py
src/backtest/*.py
src/attribution/*.py
src/pipeline/stages.py
```

---

## 十、领域知识补充

**为什么不用 YAML 或 JSON 存配置？**

Python 常量的优势：
1. 可以用 `pd.Timestamp("2012-01-01")` 直接构建日期对象，JSON 需要额外转换
2. 可以用 `frozenset` 保证集合不可变（`FINANCIAL_SECTOR_CODES`）
3. 类型注解（`int`、`float`、`frozenset[str]`）为 IDE 提供静态检查
4. 注释可以解释参数来源（如"O1 网格实验结果"）

**参数冻结与机器学习的类比**：

量化策略的时间切分与机器学习的训练集/验证集/测试集完全类比。训练期参数调好后：
- 验证期：允许微调（相当于超参数搜索的 dev set）
- 测试期：不允许任何调整（真实未来的代理）

违反这一原则的后果是"测试集过拟合"——策略在历史回测中表现出色，但在真实未来中失效，因为你已经（隐式地）用测试期结果调整了参数。
