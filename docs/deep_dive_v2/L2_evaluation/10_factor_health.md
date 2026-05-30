# 10 — factor_health.py：因子健康监控模块

> 对应源文件：`src/evaluation/factor_health.py`
> 实际行数：**504 行**（计划快照为 393，新增可视化函数后行数增加，撰写前已核实）
> 处理方式：**新写**（v1 目录无对应文档）
> 所属 Part：Part 3 — 单因子评估层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 2  因子评估    ← 本文件（factor_health.py）
Layer 2  因子评估    src/evaluation/ic_analysis.py  ← 依赖
Layer 1  因子构建    src/factors/
Layer 0  数据基础    src/data/ + src/config.py
```

`factor_health.py` 是 `ic_analysis.py` 的**上层封装**：
- `ic_analysis.py` 提供计算原语（IC 序列、IC_IR）
- `factor_health.py` 在此基础上构建**滚动健康状态**并生成结构化报告

本模块的设计目标是**持续监控**：不只看当前 IC_IR，更关注因子的时间趋势（是否在衰减）。

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游（IC 数据） | `src/evaluation/ic_analysis.py` | 读取 `build_ic_history` 的输出 |
| 上游（配置）| 无（阈值硬编码为模块常量）| 与 config.py 解耦，便于独立测试 |
| 下游 | `scripts/run_factor_evaluation.py` | 调用 `FactorHealthMonitor` 生成报告 |
| 下游 | `docs/research/factor_roadmap/factor_research_guide.md` | 报告结论更新到因子路线图 |

> **重要约束**（来自 L26）：  
> 本模块**不依赖** `src/pipeline/`、`src/signal/`、`src/backtest/` 或测试集目录。  
> 这保证了因子健康诊断可以独立运行，不受实验状态影响。

### matplotlib 隔离设计

`matplotlib` 仅在 `plot_health_panel()` 函数内部导入（L404），不作为顶层依赖。原因：CI 环境或无显示器的服务器不一定安装了 matplotlib，将其放入顶层会导致整个模块无法 import。只在绘图时按需导入，其他函数可正常使用。

---

## 二、时间对齐与 PIT 假设

### 关键声明：验证期数据不得回流筛选

```python
# L284–290（build_report 中的报告声明）
"> **重要声明**：本报告数据范围为**训练期 + 验证期**"
"（训练期：2012-2020，验证期：2021-2022）。\n"
"> 验证期（2021-2022）数据仅用于诊断参考，"
"**不允许回流至因子筛选逻辑**，不得直接作为调整 `final_factors.json` 的依据。"
```

**为什么验证期数据不能回流？**  
`FactorHealthMonitor` 的 IC 历史矩阵包含训练期 + 验证期（2012–2022）。验证期数据已被用于"观察因子在样本外的表现"，如果以验证期的 IC 趋势来决定因子筛选，相当于在验证集上过拟合，违背了"验证集仅用一次"的统计纪律。

**正确用法**：报告中的 `REVIEW_REMOVAL` 建议 → 另起训练集实验验证 → 在训练集内完成验证后才能修改因子池。

### as_of 参数的时间边界

`build_snapshot(as_of=...)` 只使用 `ic_history.index <= as_of` 的数据（L221）。调用方可传入任意截止日期，便于：
- 生成历史快照（如"2020 年末的因子健康状态"）
- 模拟在训练集结束时的因子健康评估

---

## 三、模块顶部：导入与常量

```python
# L43–56 — 状态和趋势标签常量
STATUS_STABLE   = "STABLE"
STATUS_WEAK     = "WEAK"
STATUS_WARN     = "WARN"
STATUS_REVERSE  = "REVERSE"
STATUS_UNKNOWN  = "UNKNOWN"

TREND_OK            = "OK"
TREND_DETERIORATING = "DETERIORATING"
TREND_INSUFFICIENT  = "INSUFFICIENT_HISTORY"
```

```python
# L53–56 — 健康判断阈值（模块级常量，不从 config.py 读取）
IC_IR_STABLE_THRESHOLD = 0.30
IC_IR_WEAK_THRESHOLD   = 0.20
DETERIORATING_GAP      = 0.15   # adj_12m < adj_36m - 0.15 → DETERIORATING
MIN_VALID_OBS          = 6      # 滚动窗口内最少有效观测数
```

**为什么阈值不从 config.py 读取？**  
因子健康监控是诊断工具，其阈值是关于"IC_IR 衰减到什么程度算需要关注"的判断标准，与因子筛选的准入门槛（`EVAL_IC_EFFECTIVE_CRITERIA`）性质不同。将两者分开维护避免耦合：调整筛选门槛不会误触健康状态分类。

---

## 四、核心函数逐一解析

### 4.1 `compute_rolling_ic_ir` — 滚动 IC_IR 计算

```python
# L89–120
def compute_rolling_ic_ir(
    ic_series: pd.Series,
    window: int,
    as_of: pd.Timestamp,
    min_obs: int = MIN_VALID_OBS,
) -> float:
```

**时间约束**：
```python
# L111–113 — 严格只使用 <= as_of 的数据
valid = ic_series.dropna()
valid = valid[valid.index <= as_of]
if len(valid) > window:
    valid = valid.iloc[-window:]   # 取最近 window 个有效期
```

**执行逻辑**：
1. 过滤到 `index <= as_of` 的有效（非 NaN）观测
2. 取最后 `window` 个（不足时用全部有效观测）
3. 若有效数 < `min_obs`（默认 6），返回 NaN
4. 计算 `ic_mean / ic_std(ddof=1)`

**min_obs = 6 的设计意图**：  
6 期约半年数据。少于 6 期时，IC_IR 的统计稳定性太差（标准差估计方差很大），不如返回 NaN 明确表示"数据不足"，避免由少量极端值主导的 IC_IR 产生误导性结论。

---

### 4.2 `compute_health_status` — 健康状态分类

```python
# L123–140
def compute_health_status(adjusted_ic_ir_24m: float) -> str:
```

**输入**：`adjusted_ic_ir_24m = ic_ir_24m * factor_direction`

**分类规则**：

```
adj_IC_IR_24m >= 0.30  →  STABLE  （信号稳定，可放心使用）
0.20 <= adj < 0.30     →  WEAK    （信号偏弱，持续监控）
0 <= adj < 0.20        →  WARN    （信号衰减，关注是否需要调整）
adj < 0                →  REVERSE （方向反转，需要审查）
NaN                    →  UNKNOWN （样本不足，无法判断）
```

**为什么使用 adj_IC_IR（方向调整后）而非原始 IC_IR？**  
`factor_direction` 决定了"高 IC_IR 是好事还是坏事"：
- 正向因子（`ep_ttm`，+1）：原始 IC_IR > 0 说明因子有效
- 负向因子（`vol_60d`，-1）：原始 IC_IR < 0 说明因子有效（高波动率预期负超额）

`adj_IC_IR = IC_IR × direction` 把所有因子的"有效方向"统一到正数，使同一套阈值可以无差别地应用于正向和负向因子。

---

### 4.3 `compute_trend_flag` — 趋势预警

```python
# L143–160
def compute_trend_flag(adj_ic_ir_12m: float, adj_ic_ir_36m: float) -> str:
```

**规则**：
```
任一输入为 NaN                               →  INSUFFICIENT_HISTORY
adj_ic_ir_12m < adj_ic_ir_36m - 0.15        →  DETERIORATING
否则                                         →  OK
```

**DETERIORATING 的经济含义**：  
近 12 期（约 1 年）的因子预测力显著低于近 36 期（约 3 年）的长期水平，且差距超过 0.15。这意味着因子的信号质量在近期**明显下滑**，而不只是短期波动。

**0.15 阈值的选择**：对于 IC_IR，0.15 的差值大约等于"稳定级别（0.30）vs. 弱信号级别（0.20）"之间的差距，是一个有实际意义的分界点。

---

### 4.4 `FactorHealthMonitor` — 因子健康监控器

```python
# L167–370
class FactorHealthMonitor:
    def __init__(
        self,
        ic_history: pd.DataFrame,
        factor_directions: pd.Series,
        windows: tuple[int, int, int] = (12, 24, 36),
        min_obs: int = MIN_VALID_OBS,
    ) -> None:
```

**构造函数的严格校验**（L192–198）：

```python
# L192–198 — 拒绝默认正向
missing = [
    f for f in ic_history.columns
    if pd.isna(factor_directions.get(f))
]
if missing:
    raise ValueError(
        f"factor_directions 缺少以下因子的方向定义（不允许默认正向）：{missing}"
    )
```

> **为什么不允许默认正向？**  
> 默认所有因子为正向会导致负向因子（`vol_60d`、`accrual` 等）的健康状态被错误评估：原始 IC_IR = -0.35 会被误判为 REVERSE，而实际上这是正常的有效信号。方向信息必须显式提供，强制调用方思考每个因子的预期方向。

**`build_snapshot` 的输出结构**（L205–263）：

| 列名 | 含义 | 类型 |
|------|------|------|
| `factor_direction` | +1 或 -1 | float |
| `n_valid_obs` | 截止 as_of 的有效 IC 观测总数 | int |
| `ic_ir_12m` | 原始滚动 12m IC_IR | float |
| `ic_ir_24m` | 原始滚动 24m IC_IR | float |
| `ic_ir_36m` | 原始滚动 36m IC_IR | float |
| `adj_ic_ir_12m` | 方向调整后 12m IC_IR | float |
| `adj_ic_ir_24m` | 方向调整后 24m IC_IR（用于状态分类）| float |
| `adj_ic_ir_36m` | 方向调整后 36m IC_IR | float |
| `status` | STABLE/WEAK/WARN/REVERSE/UNKNOWN | str |
| `trend_flag` | OK/DETERIORATING/INSUFFICIENT_HISTORY | str |
| `suggested_action` | 建议操作 | str |

**排序**：按 `adj_ic_ir_24m` 降序（NaN 在末尾），即健康状态最好的因子排最前。

**`build_report` 输出结构**（L265–370）：

```
# 因子健康监控报告

[重要声明：验证期数据不得回流]

## 1. 健康状态分布
  STABLE：N 个
  WEAK：N 个
  ...

## 2. 趋势预警
  DETERIORATING 因子列表（含 adj_12m、adj_36m 数值）

## 3. 完整健康快照
  Markdown 表格

## 4. 因子分组详情
  按状态分组列出每个因子及数值

## 5. 建议操作汇总
  REVIEW_REMOVAL 和 WATCH_CLOSELY 列表
```

---

### 4.5 `plot_health_panel` — 因子健康面板图

```python
# L377–503
def plot_health_panel(
    ic_history: pd.DataFrame,
    snapshot: pd.DataFrame,
    as_of: pd.Timestamp,
    output_path: Path | str,
    windows: tuple[int, int, int] = (12, 24, 36),
) -> None:
```

**自动布局算法**（L428–429）：

```python
# L428–429 — 动态计算布局（支持任意因子数）
n_cols = max(1, math.ceil(math.sqrt(n)))
n_rows = math.ceil(n / n_cols)
```

对于 N 个因子，布局为近似正方形：
- 16 个因子 → 4×4
- 20 个因子 → 5×4
- 25 个因子 → 5×5

**子图内容**：
1. 月度 IC 柱状图（IC 均值为正时绿色，为负时红色）
2. 滚动 adj_IC_IR_24m 折线（第二纵轴，颜色编码健康状态）
3. 子图标题：因子名 + 状态 + adj_IC_IR_24m 数值

**颜色方案**：
```python
_STATUS_COLOR = {
    STATUS_STABLE:  "#2ca02c",   # 绿
    STATUS_WEAK:    "#ff7f0e",   # 橙
    STATUS_WARN:    "#d62728",   # 红
    STATUS_REVERSE: "#9467bd",   # 紫
    STATUS_UNKNOWN: "#7f7f7f",   # 灰
}
```

---

## 五、内部辅助函数

| 函数 | 位置 | 说明 |
|------|------|------|
| `_round_or_nan(val, decimals)` | L63–67 | NaN 原样返回；否则四舍五入 |
| `_suggest_action(status, trend)` | L70–82 | 生成建议操作字符串（HOLD/MONITOR/WATCH_CLOSELY/REVIEW_REMOVAL）；DETERIORATING 时追加 `\|DETERIORATING` 后缀 |

---

## 六、落盘产物（Artifacts）

`factor_health.py` 本身**不直接写文件**。产物由调用方（`run_factor_evaluation.py`、`scripts/build_factor_panels.py`）写入。

| 产物 | 典型路径 | 写入时机 |
|------|---------|---------|
| Markdown 报告 | `reports/factor_health/health_report_<date>.md` | 评估脚本调用 `build_report` 后写入 |
| 健康面板图 | `reports/factor_health/health_panel_<date>.png` | 评估脚本调用 `plot_health_panel` 后写入 |
| 快照 CSV | `reports/factor_health/health_snapshot_<date>.csv` | 可选，评估脚本写入 |

> **权威来源**：实际产物路径以 `run_factor_evaluation.py` 的实现为准，本文件不包含路径逻辑。

---

## 七、相关测试

**当前状态**：`tests/test_evaluation.py` 中**没有 `factor_health.py` 的专项测试**（TODO）。

**高优先级待补充测试**：

| 测试场景 | 测试要点 |
|---------|---------|
| `compute_rolling_ic_ir` 窗口边界 | `len(valid) < min_obs` 返回 NaN；正好 min_obs 期时正常计算 |
| `compute_health_status` 阈值边界 | adj_IC_IR = 0.30（恰好 STABLE）、0.29（WEAK）、0.0（WARN）、-0.01（REVERSE）、NaN（UNKNOWN） |
| `compute_trend_flag` DETERIORATING 触发 | adj_12m = 0.10，adj_36m = 0.26 → `0.10 < 0.26 - 0.15=0.11` → DETERIORATING |
| `FactorHealthMonitor.__init__` 缺少方向定义 | 应抛 ValueError |
| `build_snapshot` 验证 as_of 时间截断 | 传入过去的 as_of，快照不应包含之后的数据 |
| 负向因子的健康状态 | IC_IR = -0.35，direction = -1 → adj = +0.35 → STABLE |

---

## 八、失败与降级路径

| 失败场景 | 行为 | 影响 |
|---------|------|------|
| 因子方向定义缺失（NaN） | `FactorHealthMonitor.__init__` 抛 ValueError | 构造失败，需检查 `factor_directions` 输入 |
| 有效观测数 < min_obs | `compute_rolling_ic_ir` 返回 NaN | 快照中该因子 `status = UNKNOWN` |
| 两个时间窗口之一为 NaN | `compute_trend_flag` 返回 INSUFFICIENT_HISTORY | 无法判断趋势，不触发 DETERIORATING |
| 快照为空（无因子）| `plot_health_panel` 输出 warning 并直接返回 | 不报错，不生成图文件 |

---

## 九、数据流图

```
IC 历史矩阵（build_ic_history 输出）            因子方向（factor_directions Series）
  date × factor                                    factor → +1/-1
       │                                                │
       └───────────────────┬───────────────────────────┘
                           │
                           ▼
              FactorHealthMonitor(ic_history, factor_directions)
                           │
                           ▼
              build_snapshot(as_of=T)
                ┌──────────────────────────────────────────┐
                │  for each factor:                         │
                │    ic_ir_12m = compute_rolling_ic_ir(w12) │
                │    ic_ir_24m = compute_rolling_ic_ir(w24) │
                │    ic_ir_36m = compute_rolling_ic_ir(w36) │
                │    adj_24m = ic_ir_24m * direction        │
                │    status = compute_health_status(adj_24m)│
                │    trend = compute_trend_flag(adj_12m, adj_36m) │
                └──────────────────────────────────────────┘
                           │
              ┌────────────┴────────────────────┐
              ▼                                  ▼
        build_report()                    plot_health_panel()
        → Markdown 报告字符串              → PNG 面板图（.png）
              │
              ▼
        调用方写入 reports/factor_health/
```

---

## 十、领域知识补充

### 为什么用 24 期（约 2 年）作为健康状态的主判据？

因子的 IC 存在**均值回归**特性：短期（3-6 个月）波动很大，以月为单位的 IC_IR 统计噪声极高。研究表明：
- 12 期（1 年）：噪声仍较大，适合趋势监控（检测衰减方向）
- 24 期（2 年）：在噪声和时效性之间取得较好的平衡
- 36 期（3 年）：长期水平，适合识别结构性变化

24 期 IC_IR 比 12 期更稳定，比 36 期对近期变化更敏感，是健康状态分类的最佳单一参考。

### DETERIORATING 设计的博弈

`DETERIORATING` 的触发条件（`adj_12m < adj_36m - 0.15`）设计了一个**有意义的滞后**：只有近期显著低于长期水平时才触发，避免单期波动被误判为趋势。

这个设计的取舍：
- **过于灵敏**（阈值过小如 0.05）：频繁触发伪警告，运营成本高
- **过于迟钝**（阈值过大如 0.30）：因子已严重衰减才触发，损失期 alpha

0.15 对应"稳定级别"和"弱信号级别"的中间档位，是一个合理的操作化定义。

### 健康监控的使用纪律

```
                 ┌─── STABLE/WEAK ────→ 正常使用，不干预
健康监控结果      │
（仅限诊断）     ├─── WARN ──────────→ 关注，监控未来趋势
                 │
                 └─── REVERSE/DETERIORATING ─→ 另起消融实验验证
                                               ↓
                                            训练集内验证通过
                                               ↓
                                            修改 final_factors.json
```

**禁止的操作**：看到 REVIEW_REMOVAL 后直接修改主线因子池。必须经过"另起实验→训练集验证→审批"的完整流程。
