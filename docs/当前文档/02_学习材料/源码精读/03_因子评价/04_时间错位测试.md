# 12 — shift_test.py：时间错位测试（防未来函数辅助检验）

> 对应源文件：`src/evaluation/shift_test.py`
> 实际行数：**219 行**（计划快照为 155，Phase 3 新增 lead_warn/lead_reject 分级机制，撰写前已核实）
> 处理方式：**沿用 + 核查更新**（新增 Phase 3 分级 lead_ratio 逻辑）
> 所属 Part：Part 3 — 单因子评估层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 2  因子评估    ← 本文件（shift_test.py）
Layer 2  因子评估    ic_analysis.py（compute_ic_series / ic_summary）
Layer 1  因子构建    src/factors/
```

`shift_test.py` 是**辅助防御工具**，专门用于检测因子中是否混入了未来数据。它不直接产生因子筛选结论，而是提供警告信号，供研究员判断是否需要深入排查 PIT 对齐。

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游 | `ic_analysis.compute_ic_series` `ic_analysis.ic_summary` | 复用 IC 计算原语 |
| 下游 | `scripts/run_factor_evaluation.py` | 调用 `batch_shift_test`，结果写入 `factor_summary.csv` |

### 与 v1 的主要差异（Phase 3 升级）

| 变化 | 说明 |
|------|------|
| `lead_warn` 字段 | 新增：`lead_ratio` 在 `[1.5, 2.0)` 时标记待议区，不自动排除 |
| `lead_reject` 字段 | 新增：`lead_ratio >= 2.0` 时拒绝（原 `lead_reverse` 升级为此） |
| `lead_reverse` | 变为 `lead_reject` 的向后兼容别名 |
| `lag2` 统计量 | 新增：`lag_2` 错位检验，监控 2 期滞后的 IC_IR |
| 诊断等级扩展 | 沿用原有 4 级，但 `lead_reject` 的触发条件更精细 |

---

## 二、时间对齐与 PIT 假设

### 测试原理

```
原始 IC:  IC(factor_T,    fwd_ret_T)   → 当期因子预测当期收益
lag_1  IC:  IC(factor_{T-1}, fwd_ret_T) → 上期因子预测当期收益（错位后）
lag_2  IC:  IC(factor_{T-2}, fwd_ret_T) → 两期前因子预测当期收益
lead_1 IC:  IC(factor_{T+1}, fwd_ret_T) → 下期因子预测当期收益（前视探针）
```

`lead_1` 是**未来数据检测探针**：正常因子在 T 时刻不含 T+1 的信息，`lead_1 IC` 应接近 0。若 `lead_1 IC_IR` 明显强于 `original IC_IR`，说明因子 T 的值实际上已包含了 T+1 甚至更晚的信息。

### 实现细节

`factor_panel.shift(k)` 意味着：
- `shift(1)`：将因子向前错位 1 期（lag_1），第一期变为 NaN
- `shift(-1)`：将因子向后错位 1 期（lead_1），最后一期变为 NaN

```python
# shift_test.py L100–103
stats_lag1  = _compute_shift_stats(factor_panel, fwd_ret_panel, shift)       # shift(1)
stats_lag2  = _compute_shift_stats(factor_panel, fwd_ret_panel, shift + 1)   # shift(2)
stats_lead1 = _compute_shift_stats(factor_panel, fwd_ret_panel, -1)          # shift(-1)
```

注意：`shift` 操作是在调仓日序列维度上做，不是交易日维度。`factor_panel.shift(1)` 中的第 T 期因子值变为第 T-1 期的因子值，使其对应 fwd_ret[T]，即"用上期因子预测当期收益"。

---

## 三、模块顶部：导入与常量

```python
# L41–43
from src.evaluation.ic_analysis import compute_ic_series, ic_summary

# L45–48 — 诊断等级常量
_DIAGNOSIS_STRONG_DROP   = "strong_drop"   # drop > 0.5 — 正常
_DIAGNOSIS_WEAK_DROP     = "weak_drop"     # 0.1 < drop <= 0.5 — 可接受
_DIAGNOSIS_NO_DROP       = "no_drop"       # -0.1 <= drop <= 0.1 — 中性
_DIAGNOSIS_REVERSE       = "reverse"       # drop < -0.1 — 异常
```

---

## 四、核心函数逐一解析

### 4.1 `run_shift_test` — 单因子多档时间错位测试

```python
# L66–178
def run_shift_test(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    shift: int = 1,
) -> dict:
```

**执行逻辑**（关键步骤）：

**Step 1**：计算 4 组 IC 统计量（L97–107）
```python
ic_orig     = compute_ic_series(factor_panel, fwd_ret_panel)   # 原始
stats_lag1  = _compute_shift_stats(factor_panel, fwd_ret_panel, shift)     # lag_1
stats_lag2  = _compute_shift_stats(factor_panel, fwd_ret_panel, shift+1)   # lag_2
stats_lead1 = _compute_shift_stats(factor_panel, fwd_ret_panel, -1)        # lead_1
```

**Step 2**：计算 ic_ir_drop（L108）
```python
ic_ir_drop = float(ir_orig - ir_lag1)   # 正数表示原始更强（正常）
```

**Step 3**：4 级诊断等级（L111–118）
```python
if ic_ir_drop > 0.5:   → "strong_drop"  （正常，因子有明显短期预测力）
elif ic_ir_drop > 0.1: → "weak_drop"    （正常，高质量/价值因子常见持久性）
elif ic_ir_drop >= -0.1:→ "no_drop"     （中性，需视因子类型判断）
else:                  → "reverse"      （异常，错位 IC 反而更强）
```

**Step 4（Phase 3 新增）**：三级 lead_ratio 检测（L120–143）

```python
# L120–125 — lead_ratio 计算与分级
# ir_orig <= 0.05 时因子本身过弱，lead_ratio 无统计意义，置 0 不触发警告
lead_ratio  = round(ir_lead1 / ir_orig, 4) if ir_orig > 0.05 else 0.0
lead_warn   = 1.5 <= lead_ratio < 2.0   # ⚠️ 待议区（1.5-2.0x），不自动排除
lead_reject = lead_ratio >= 2.0          # ❌ 拒绝（≥2.0x，Gate 2 排除）
lead_reverse = lead_reject               # 向后兼容别名
```

**三级分类的含义**：

| lead_ratio | 分级 | 含义 | 处理 |
|----------|------|------|------|
| < 1.5x | 正常 | lead_1 IC 不明显强于 orig | 不触发任何警告 |
| 1.5–2.0x | `lead_warn=True` | 待议区，可能有轻微持续性 | 记录 info 日志，需人工审查 |
| ≥ 2.0x | `lead_reject=True` | 高度怀疑未来数据 | 记录 warning，Gate 2 排除 |

**Step 5**：组装 warning 字符串（L127–143）

`warning` 非空的条件：
1. `diagnosis == "reverse"`（lag_1 IC 反而更强）
2. `lead_reject == True`（lead_ratio ≥ 2.0x）

两个条件可以同时满足，warning 字符串会合并。

**完整返回字段**：

| 字段 | 含义 | 类型 |
|------|------|------|
| `original` | 原始 IC 统计量（ic_summary 输出）| dict |
| `lagged` | lag_1 错位 IC 统计量 | dict |
| `shifted` | `lagged` 的向后兼容别名 | dict |
| `lag2` | lag_2 错位 IC 统计量 | dict |
| `lead1` | lead_1 超前 IC 统计量 | dict |
| `lead_ratio` | `\|lead1_ic_ir\| / \|orig_ic_ir\|` | float |
| `lead_warn` | 1.5–2.0x 待议区 | bool |
| `lead_reject` | ≥2.0x 拒绝 | bool |
| `ic_ir_drop` | `\|orig\| - \|lag1\|`（正数正常）| float |
| `diagnosis` | 4 级诊断字符串 | str |
| `lead_reverse` | `lead_reject` 的向后兼容别名 | bool |
| `warning` | 非空表示存在警告信息 | str |

---

### 4.2 `batch_shift_test` — 批量时间错位测试

```python
# L181–218
def batch_shift_test(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    shift: int = 1,
) -> pd.DataFrame:
```

**输出**：每行一个因子的 DataFrame，按 `ic_ir_drop` 升序排列（问题最大的——drop 最负——排最前），包含：
- `orig_ic_ir / lag1_ic_ir / lag2_ic_ir / lead1_ic_ir`
- `lead_ratio / lead_warn / lead_reject`（Phase 3 新增）
- `ic_ir_drop / diagnosis`
- `lead_reverse / warning`（向后兼容）
- `lagged_ic_ir`（`lag1_ic_ir` 的旧名，兼容 `run_factor_evaluation.py`）

> **重要注意**：`warning` 列在 `batch_shift_test` 中存储为 `bool`（L213），而在 `run_shift_test` 返回的 dict 中是字符串。这是为了便于 DataFrame 过滤（`result[result["warning"] == True]`）。

---

## 五、内部辅助函数

| 函数 | 位置 | 说明 |
|------|------|------|
| `_safe_abs_ir(val)` | L51–52 | NaN 返回 0.0（用于 lead_ratio 分母安全计算） |
| `_compute_shift_stats(factor_panel, fwd_ret_panel, shift)` | L55–63 | 对 factor_panel 做 shift，返回错位后的 ic_summary |

---

## 六、落盘产物（Artifacts）

`shift_test.py` **不直接写文件**。

典型产物（由 `run_factor_evaluation.py` 写入）：
- `reports/factor_evaluation/factor_summary.csv`：含 `diagnosis`、`lead_ratio`、`lead_warn`、`lead_reject` 列
- `reports/factor_evaluation/shift_test_detail.csv`（可选）：每个因子的详细错位统计

---

## 七、相关测试

**测试文件**：`tests/test_evaluation.py::TestShiftTest`

| 测试用例 | 覆盖内容 |
|---------|---------|
| `test_diagnosis_field_present` | 结果含 `diagnosis` 且在 4 个合法值中 |
| `test_lead_reverse_field_present` | 结果含 `lead_reverse` 字段 |
| `test_genuine_factor_no_lead_reverse` | 正常无未来函数因子不触发 `lead_reverse` |
| `test_future_leakage_triggers_lead_reverse` | 含未来数据的因子接口完整性（布尔类型验证）|
| `test_batch_shift_has_diagnosis_column` | `batch_shift_test` 含 `diagnosis`、`lead_reverse`、`warning` 列 |
| `test_reverse_diagnosis_on_anomalous_factor` | 构造"错位 IC 更强"场景，触发 reverse 诊断 |

**缺失测试（TODO）**：
- `lead_warn` 触发（1.5–2.0x 待议区的精确边界）
- `lead_reject` 触发（≥2.0x 的精确边界）
- `ir_orig <= 0.05` 时 `lead_ratio` 不应触发警告

---

## 八、失败与降级路径

| 失败场景 | 行为 |
|---------|------|
| 因子面板全为 NaN | `compute_ic_series` 返回空 Series，所有统计量为 NaN，`warning=""` |
| `ir_orig <= 0.05`（因子本身过弱）| `lead_ratio = 0.0`（不触发 lead_warn/lead_reject） |
| `shift` 参数大于调仓日数 | `shift` 后 factor_panel 大量 NaN，IC 样本极少，统计量多为 NaN |

---

## 九、数据流图

```
factor_panel (date × stock)     fwd_ret_panel (date × stock)
     │                                    │
     │    shift(0) ─────────────────────► │──→ ic_orig
     │    shift(+1) ────────────────────► │──→ stats_lag1
     │    shift(+2) ────────────────────► │──→ stats_lag2
     │    shift(-1) ────────────────────► │──→ stats_lead1
     │                                    │
     └────────────────────────────────────┘
                        │
                        ▼
         ic_ir_drop = |orig_ic_ir| - |lag1_ic_ir|
         diagnosis  = 4 级分类
         lead_ratio = |lead1_ic_ir| / |orig_ic_ir|
         lead_warn   = 1.5 ≤ ratio < 2.0
         lead_reject = ratio ≥ 2.0
         warning    = (diagnosis=="reverse") OR lead_reject
                        │
                        ▼
              run_shift_test 返回 dict
                        │
               batch_shift_test
                        │
                        ▼
           DataFrame（每因子一行）
           按 ic_ir_drop 升序（问题最大的在顶部）
```

---

## 十、领域知识补充

### 未来函数的常见来源（PIT 排查清单）

若 `lead_1 IC_IR` 异常高（`lead_reject=True`），排查优先级：

1. **财务数据未用 ann_date（Annodt）**：用报告期（Accper）替代公告日，导致未来公告信息提前可用
2. **滚动均线数据泄露**：计算滚动 20 日均量时，用了包含 T 日的 21 日区间（应截止 T-1）
3. **数据合并对齐错误**：merge 时日期索引错位（如月末 vs 下月初），左侧 T 期因子实际含右侧 T+1 期数据
4. **复权处理不当**：前复权比例在 T+1 公布，倒算 T 日价格时用了 T+1 才知道的复权因子

### lag_1 IC 高的两种解释

```
  lag_1 IC_IR 仍较高
  ├── 因子持久性高（价值/质量因子常见）
  │     → no_drop / weak_drop，属正常现象
  │     → 说明因子信号在 1-2 个月内仍有效，月频使用合适
  │
  └── 数据对齐错误（低持久性因子出现 no_drop）
        → 应配合 lead_1 检验区分
        → 低持久性因子（动量、波动率）正常 lag_1 drop 应 > 0.5
        → 若 drop < 0.1 且 lead_ratio > 1.5 → 强烈怀疑数据问题
```

### lead_ratio 阈值的设定依据

`lead_reject` 阈值 2.0x 的含义：超前因子的 IC_IR 是原始的 2 倍以上。考虑到 IC 本身有统计噪声，1.5x 以上可能是偶然，2.0x 以上则几乎不可能是巧合——正常因子中"下期因子比当期因子更能预测当期收益"的概率极低，只有数据本身含有未来信息才会出现这种情况。

Phase 3 引入 `lead_warn` 的原因：1.5–2.0x 区间存在不确定性，自动排除可能误伤某些确实存在短期持续性（但并非未来泄露）的因子。将其标记为"待议"，由研究员结合其他证据（PIT 测试结果、数据来源审计）做人工判断。
