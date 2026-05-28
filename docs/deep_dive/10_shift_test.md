# 10 — 时间错位测试：shift_test.py 逐行精讲

> 对应源文件：`src/evaluation/shift_test.py`（191 行）  
> 前置依赖：`08_ic_analysis.md`（IC 计算与汇总）  
> 核心任务：通过时间错位检测因子是否包含未来数据（未来函数）

---

## 一、模块定位：为什么需要时间错位测试

### 1.1 问题背景

在量化研究中，**未来函数（look-ahead bias）** 是最危险的错误。当你用 T 日的信息构造因子，但信息实际上在 T 日之后才能获得时，回测结果虚高，实盘表现则大幅低于预期。

### 1.2 时间错位测试的核心思想

```
原始 IC：因子[T] × 收益[T→T+1]
lag_1  ：因子[T-1] × 收益[T→T+1]（用上期因子预测当期收益）
lag_2  ：因子[T-2] × 收益[T→T+1]
lead_1 ：因子[T+1] × 收益[T→T+1]（用下期因子预测当期收益，未来探针）
```

**关键逻辑**：如果因子只用了 T 日及以前的信息（没有未来函数），那么：
- `lag_1` 的预测力应弱于 `原始`（因子信息变旧了）
- `lead_1` 的预测力不应强于 `原始`（下期信息没有理由比当期更好）

如果 `lead_1` 的预测力显著强于 `原始`，说明当期因子已经包含了未来信息。

### 1.3 测试的局限性

模块 docstring 明确指出：

```
此测试是辅助工具，不能替代 PIT 单元测试（tests/test_pit.py）。
```

时间错位测试是统计检验，存在假阳性和假阴性：
- **高持久性因子**（价值、质量）：lag_1 IC 本来就接近原始 IC，误判为"无衰减=有未来函数"
- **低持久性因子**（动量、波动率）：lag_1 IC 应显著低于原始，否则才是问题

所以测试结果需要结合因子经济含义解读，不能机械应用。

---

## 二、常量定义（第 41-44 行）

```python
_DIAGNOSIS_STRONG_DROP   = "strong_drop"   # drop > 0.5 — 正常
_DIAGNOSIS_WEAK_DROP     = "weak_drop"     # 0.1 < drop <= 0.5 — 可接受
_DIAGNOSIS_NO_DROP       = "no_drop"       # -0.1 <= drop <= 0.1 — 中性
_DIAGNOSIS_REVERSE       = "reverse"       # drop < -0.1 — 异常
```

四个等级对应 `ic_ir_drop = |orig_ic_ir| - |lag1_ic_ir|` 的不同区间。  
下划线前缀 `_` 表示模块内部常量，不对外暴露。

---

## 三、工具函数

### 3.1 `_safe_abs_ir`（第 47-48 行）

```python
def _safe_abs_ir(val) -> float:
    return 0.0 if pd.isna(val) else abs(float(val))
```

将 IC_IR 取绝对值，同时处理 NaN（返回 0.0 而不是 NaN）。

**为什么取绝对值？** `ic_ir_drop = |orig| - |lag1|` 计算的是绝对强度的下降量。如果因子是负向的（IC_IR 为负），原始 IC_IR 绝对值大才是"强"，不是因为 IC_IR 本身是负数就说明因子弱。

**为什么 NaN 转 0？** NaN 时认为 IC_IR = 0，即因子无预测力。这是保守处理：如果数据不足以计算 IC_IR，相当于假设它没有预测力。

### 3.2 `_compute_shift_stats`（第 51-59 行）

```python
def _compute_shift_stats(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    shift: int,
) -> dict:
    shifted = factor_panel.shift(shift)
    ic_s = compute_ic_series(shifted, fwd_ret_panel)
    return ic_summary(ic_s)
```

`factor_panel.shift(shift)` 对整个因子面板做时间平移：
- `shift=1`：第 T 行的因子值变成第 T+1 行（即用 T-1 期因子预测 T 期收益）
- `shift=-1`：第 T 行的因子值变成第 T-1 行（即用 T+1 期因子预测 T 期收益）

这里有个关键的坐标系理解：

```
原始面板：
  日期 T1 → 因子值 f(T1)
  日期 T2 → 因子值 f(T2)

shift(1) 后：
  日期 T1 → NaN（没有 T0 的数据）
  日期 T2 → f(T1)（用上期因子值）

与 fwd_ret 计算 IC：
  T2 的 IC = corr(f(T1), ret(T2→T3))
  → "上期因子" 预测 "当期收益"
```

`shift(-1)` 用于 lead_1 探针：

```
shift(-1) 后：
  日期 T1 → f(T2)（用下期因子值）
  日期 T2 → f(T3)
  最后一行 → NaN

与 fwd_ret 计算 IC：
  T1 的 IC = corr(f(T2), ret(T1→T2))
  → "下期因子" 预测 "当期收益"
```

---

## 四、核心函数：`run_shift_test`

```python
def run_shift_test(
    factor_panel: pd.DataFrame,
    fwd_ret_panel: pd.DataFrame,
    shift: int = 1,
) -> dict:
```

### 4.1 计算四组 IC 统计量

```python
ic_orig  = compute_ic_series(factor_panel, fwd_ret_panel)
stats_orig = ic_summary(ic_orig)

stats_lag1  = _compute_shift_stats(factor_panel, fwd_ret_panel, shift)      # shift=1
stats_lag2  = _compute_shift_stats(factor_panel, fwd_ret_panel, shift + 1)  # shift=2
stats_lead1 = _compute_shift_stats(factor_panel, fwd_ret_panel, -1)         # shift=-1
```

注意 `shift + 1`：如果调用方传 `shift=1`（默认值），则 lag2 用 `shift=2`；如果传 `shift=2`，lag2 用 `shift=3`。这个参数设计允许测试不同起始错位期数，但实践中几乎总用默认值 1。

### 4.2 计算 ic_ir_drop

```python
ir_orig  = _safe_abs_ir(stats_orig.get("ic_ir"))
ir_lag1  = _safe_abs_ir(stats_lag1.get("ic_ir"))
ir_lead1 = _safe_abs_ir(stats_lead1.get("ic_ir"))
ic_ir_drop = float(ir_orig - ir_lag1)
```

`ic_ir_drop = |原始 IC_IR| - |lag1 IC_IR|`

| 结果 | 含义 |
|------|------|
| `drop > 0` | 原始比错位更强，正常（因子有时效性） |
| `drop ≈ 0` | 原始和错位强度相当，高持久性因子正常 |
| `drop < 0` | 错位反而更强，强烈怀疑未来函数 |

### 4.3 诊断等级分配

```python
if ic_ir_drop > 0.5:
    diagnosis = _DIAGNOSIS_STRONG_DROP
elif ic_ir_drop > 0.1:
    diagnosis = _DIAGNOSIS_WEAK_DROP
elif ic_ir_drop >= -0.1:
    diagnosis = _DIAGNOSIS_NO_DROP
else:
    diagnosis = _DIAGNOSIS_REVERSE
```

四个阈值的直觉解释：

| 等级 | drop 范围 | 解读 |
|------|-----------|------|
| `strong_drop` | > 0.5 | 上期因子比当期弱很多，因子有明显短期预测力，完全正常 |
| `weak_drop` | 0.1 ~ 0.5 | 有所下降但不多，价值/质量类高持久性因子常见 |
| `no_drop` | -0.1 ~ 0.1 | 基本无变化，高持久性因子可接受，低持久性因子需关注 |
| `reverse` | < -0.1 | 上期因子预测力更强，不合理，高度怀疑数据问题 |

**重要**：`no_drop` 不是"好"，也不是"坏"，需要结合因子类型判断。财务因子（ROE、负债率）季度更新，持久性高，`no_drop` 是正常的。动量因子日频更新，持久性低，`no_drop` 就是异常。

### 4.4 lead_1 未来泄露检测

```python
lead_reverse = ir_lead1 > ir_orig and ir_orig > 0.05
```

双重条件：
1. `ir_lead1 > ir_orig`：下期因子的预测力比当期更强
2. `ir_orig > 0.05`：原始 IC_IR 有一定意义（排除全部 IC_IR 接近 0 的噪声情况）

**为什么需要第二个条件？** 如果原始 IC_IR = 0.01，lead1 IC_IR = 0.02，数值上 lead1 > orig，但两个都接近 0，差异可能只是噪声。加 `ir_orig > 0.05` 要求原始因子本身有一定预测力，才认定 lead_reverse 是真正的警告。

`lead_reverse = True` 是**最高危警告**，含义是：下期因子比当期因子更能预测当期收益，这在逻辑上是不可能的（除非因子已经用了未来数据）。

### 4.5 警告信息生成

```python
warning = ""
if diagnosis == _DIAGNOSIS_REVERSE:
    warning = (
        f"[lag_reverse] 错位后 IC_IR（{ir_lag1:.3f}）显著高于原始（{ir_orig:.3f}），"
        f"drop={ic_ir_drop:.3f} < -0.1。排查：① PIT 时间对齐 ② forward return 口径 "
        f"③ 数据拼接错误。"
    )
    log.warning("时间错位测试警告：%s", warning)
if lead_reverse:
    lead_warn = (
        f"[lead_reverse] 超前因子 IC_IR（{ir_lead1:.3f}）> 原始（{ir_orig:.3f}），"
        f"高度怀疑因子已包含未来数据，请立即排查 PIT 对齐。"
    )
    warning = f"{warning} {lead_warn}".strip() if warning else lead_warn
    log.warning("time_shift lead_reverse 警告：%s", lead_warn)
```

警告文字内嵌了排查思路：
- `lag_reverse`：先查 PIT（公告日是否正确），再查 forward return（时间对齐），最后查数据拼接（多个 Parquet 文件合并是否有日期混淆）
- `lead_reverse`：直接指向 PIT 对齐问题

两个警告可以同时触发（`warning` 字符串拼接），也可以只触发其中一个。

### 4.6 日志记录

```python
log.info(
    "shift_test: orig=%.3f lag1=%.3f lag2=%.3f lead1=%.3f drop=%.3f [%s]%s",
    ir_orig, ir_lag1,
    _safe_abs_ir(stats_lag2.get("ic_ir")),
    ir_lead1, ic_ir_drop, diagnosis,
    " ⚠️" if warning else "",
)
```

一行日志包含所有关键信息，`⚠️` emoji 让警告在日志中视觉突出（Python logging 在终端不显示颜色，用字符代替）。

### 4.7 返回值结构

```python
return {
    "original":     stats_orig,    # dict: ic_mean, ic_std, ic_ir, t_stat, p_value, ...
    "lagged":       stats_lag1,    # dict: 同上，lag_1 错位
    "shifted":      stats_lag1,    # dict: 与 lagged 相同（向后兼容别名）
    "lag2":         stats_lag2,    # dict: lag_2 错位
    "lead1":        stats_lead1,   # dict: lead_1 超前
    "ic_ir_drop":   ic_ir_drop,    # float: |orig| - |lag1|
    "diagnosis":    diagnosis,     # str: 四个等级之一
    "lead_reverse": lead_reverse,  # bool: 高危标志
    "warning":      warning,       # str: 空=正常，非空=有问题
}
```

注意 `"shifted"` 是 `"lagged"` 的别名（`stats_lag1`），保留是为了兼容旧的调用代码。这是技术债的典型标记方式：内部知道是重复，但为了兼容性保留，通过注释标注 `# 向后兼容别名`。

---

## 五、批量测试：`batch_shift_test`

```python
def batch_shift_test(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    shift: int = 1,
) -> pd.DataFrame:
    rows = []
    for name, panel in factor_panels.items():
        res = run_shift_test(panel, fwd_ret_panel, shift)
        rows.append({
            "factor":       name,
            "orig_ic_ir":   res["original"].get("ic_ir"),
            "lag1_ic_ir":   res["lagged"].get("ic_ir"),
            "lag2_ic_ir":   res["lag2"].get("ic_ir"),
            "lead1_ic_ir":  res["lead1"].get("ic_ir"),
            "ic_ir_drop":   res["ic_ir_drop"],
            "diagnosis":    res["diagnosis"],
            "lead_reverse": res["lead_reverse"],
            "lagged_ic_ir": res["lagged"].get("ic_ir"),   # 兼容旧列名
            "warning":      bool(res["warning"]),
        })

    return pd.DataFrame(rows).set_index("factor").sort_values(
        "ic_ir_drop", ascending=True  # 问题最大的排最前
    )
```

### 5.1 排序设计

```python
.sort_values("ic_ir_drop", ascending=True)
```

`ascending=True` 即从小到大，`ic_ir_drop` 最小（最负）的排最前——正好是**问题最严重的因子排在最前面**。

这与 IC 分析的 `ic_summary_df` 按 `|IC_IR|` 降序（最强因子排前）相反，体现了两个函数的目的不同：
- IC 汇总：找最好的因子
- 时间错位：找问题最大的因子

### 5.2 `warning` 列的类型转换

```python
"warning": bool(res["warning"]),
```

`res["warning"]` 是字符串，`bool("")` = `False`，`bool("某些文字")` = `True`。转换为布尔值后，汇总表格更易读（True/False vs 长文本），详细的警告文字可以通过 `run_shift_test` 单独查看。

### 5.3 `lagged_ic_ir` 兼容列

```python
"lagged_ic_ir": res["lagged"].get("ic_ir"),   # 保留旧列名
```

注释说明这是为了兼容 `run_factor_evaluation.py`，该脚本可能引用了旧列名 `lagged_ic_ir`（而不是新列名 `lag1_ic_ir`）。两列值相同，都是 lag_1 的 IC_IR，不是错误。

---

## 六、诊断结果解读指南

### 6.1 按因子类型解读

| 因子类型 | 预期持久性 | 正常诊断 | 需关注诊断 |
|---------|-----------|---------|-----------|
| 动量（mom_12m） | 低（月频更新） | `strong_drop` | `no_drop`、`reverse` |
| 波动率（vol_60d） | 低-中 | `strong_drop` 或 `weak_drop` | `no_drop`、`reverse` |
| 价值（bp、ep） | 高（季频更新） | `weak_drop` 或 `no_drop` | `reverse` |
| 质量（roe、accrual） | 高（季频更新） | `weak_drop` 或 `no_drop` | `reverse` |
| 成长（roe_delta） | 中（季频变化快） | `weak_drop` | `no_drop`、`reverse` |

### 6.2 lead_reverse 的严重程度

`lead_reverse = True` 时需要立即停下排查，不能继续评估因子：

**排查步骤**：
1. 检查 `src/data/pit_loader.py` 中 `_pit_snapshot` 的时间条件（`pit_date <= T AND end_date < T`）
2. 检查财务数据 Parquet 文件是否按 `ann_date`（公告日）正确过滤，而非 `end_date`（报告期）
3. 检查因子计算中是否意外使用了 `df.shift(-n)` 将未来数据前移
4. 检查数据拼接（年报 + 季报）时是否有日期错乱

### 6.3 `reverse` 诊断的排查思路

当 `diagnosis = "reverse"` 时，错位后的因子反而预测力更强，可能原因：

| 可能原因 | 排查方法 |
|---------|---------|
| PIT 时间对齐错误 | 检查 `ann_date` vs `end_date` 的使用 |
| forward return 计算用了当日收盘价（而非 T+1 开盘） | 检查 `compute_forward_returns` 的 shift 逻辑 |
| 多个数据源拼接时日期偏移 | 检查 Parquet 合并代码 |
| 纯属噪声（样本量小时） | 查看 `n_periods`，样本少于 20 期时结果不稳定 |

---

## 七、与其他模块的关系

```
ic_analysis.py
    ├── compute_ic_series()   ← shift_test 内部直接调用
    └── ic_summary()          ← shift_test 内部直接调用

shift_test.py
    └── run_shift_test()
        ├── 计算 4 组 IC 统计量（orig, lag1, lag2, lead1）
        ├── 比较 ic_ir_drop
        ├── 发出 diagnosis + warning
        └── 返回结果 dict

scripts/run_factor_evaluation.py
    └── batch_shift_test()    ← 写出 reports/factor_evaluation/shift_result.csv
```

---

## 八、完整诊断示意图

```
因子面板 factor_panel (T × N)
fwd_ret_panel (T × N)
        │
        ├─ 原始 IC：compute_ic_series(factor, fwd_ret)
        │         → ic_ir = orig_ic_ir
        │
        ├─ lag_1：factor.shift(1) → compute_ic_series → ic_ir = lag1_ic_ir
        │
        ├─ lag_2：factor.shift(2) → compute_ic_series → ic_ir = lag2_ic_ir
        │
        └─ lead_1：factor.shift(-1) → compute_ic_series → ic_ir = lead1_ic_ir

计算 ic_ir_drop = |orig_ic_ir| - |lag1_ic_ir|

判断诊断等级：
  drop > 0.5  → strong_drop（正常）
  drop > 0.1  → weak_drop（正常）
  drop ≥ -0.1 → no_drop（中性，需结合因子类型判断）
  drop < -0.1 → reverse（异常，怀疑未来函数）

额外检测：
  |lead1_ic_ir| > |orig_ic_ir| AND |orig_ic_ir| > 0.05
      → lead_reverse = True（高危，因子包含未来数据）

输出警告：
  reverse     → "[lag_reverse] 错位后比原始更强，排查 PIT 对齐..."
  lead_reverse → "[lead_reverse] 超前因子比当期更强，立即排查 PIT..."
```

---

## 九、测试集纪律中的应用

时间错位测试应在**训练集**或**验证集**上运行，用于筛选因子，不应在测试集上单独运行（测试集运行次数有限制）。

当你在 `run_factor_evaluation.py` 中批量评估因子时，`batch_shift_test` 的输出 `shift_result.csv` 是因子筛选的必要依据之一。`lead_reverse = True` 的因子**不得进入后续的因子合成和优化环节**，无论其 IC_IR 多高。
