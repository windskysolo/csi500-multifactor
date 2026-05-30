# 14 — ridge_decay.py：指数衰减加权 Ridge 信号合成

> 对应源文件：`src/signal/ridge_decay.py`
> 实际行数：**316 行**（计划快照为 250，撰写前已核实）
> 处理方式：**新写**（v1 目录无对应文档）
> 所属 Part：Part 4 — 信号合成层

---

## 一、文件定位

### 在系统分层中的位置

```
Layer 3  信号合成    ← 本文件（ridge_decay.py）
Layer 3  信号合成    src/signal/combiner.py（IC_IR 加权路径，见 13）
Layer 3  信号合成    experiments/legacy/ridge_signal/ridge_combiner.py（父类）
Layer 2  因子评估    src/evaluation/ic_analysis.py（验证期评分时用）
Layer 1  因子构建    src/factors/
```

### 继承关系与职责分工

```
RidgeCombiner（experiments/legacy/ridge_signal/ridge_combiner.py）
   │  提供：Walk-forward CV 骨架、_build_training_matrix、
   │        _predict_cross_section、_compute_ic_ir
   │
   └── RidgeDecayCombiner（src/signal/ridge_decay.py）← 本文件
          扩展：_build_sample_weights（指数衰减权重构造）
          扩展：_build_training_matrix_with_counts（返回每期样本数）
          覆写：_process_cv_fold（CV 折中传 sample_weight）
          覆写：_fit_and_predict（最终拟合时传 sample_weight）
```

`RidgeDecayCombiner` 与标准 expanding Ridge（`RidgeCombiner`）的**唯一区别**：在调用 `Ridge.fit()` 时传入 `sample_weight`，使近期数据的损失贡献高于远期。所有 CV 结构、IC_IR 评分、预测截面、winsorize/z-score 完全复用父类。

### 与其他 Ridge 变体的对比

| 变体 | 训练窗口 | 时间权重 | 特点 |
|------|---------|---------|------|
| `RidgeCombiner` | expanding | 均等 | 简单稳健，数据量随时间增加 |
| `RidgeRollingCombiner` | rolling（固定 N 月）| 均等 | 更新快，但早期数据量少 |
| `RidgeDecayCombiner` | expanding | **指数衰减** ← 本文件 | 近期数据更重要，同时利用全部历史 |

### stages.py 中的接入点

```python
# src/pipeline/stages.py（L175-184）
# training_mode="decay_weighted_expanding" 分支
elif training_mode == "decay_weighted_expanding":
    from src.signal.ridge_decay import RidgeDecayCombiner
    combiner = RidgeDecayCombiner(
        factor_names,
        half_life_months=spec.signal.half_life_months,
        ...
    )
```

调用链：`run_experiment.py` → `stages.py::run_signal_stage()` → `RidgeDecayCombiner`。

### 上游 / 下游依赖

| 方向 | 模块 | 关系 |
|------|------|------|
| 上游（父类）| `experiments/legacy/ridge_signal/ridge_combiner.py` | 继承 Walk-forward 骨架 |
| 上游（数据）| 因子面板 + fwd_ret_panel | 标准化后的 Parquet |
| 上游（ML）| `sklearn.linear_model.Ridge` | 核心估计器 |
| 下游 | `src/pipeline/stages.py` | Stage 层调用接口 |

---

## 二、时间对齐与 PIT 假设

### Walk-forward CV 的时间边界（继承自父类）

`_process_cv_fold` 的 purge 逻辑（L217–221）：

```python
# L217–221 — purge 隔离训练集和验证集
cutoff    = val_start - pd.DateOffset(months=self.purge_months)
train_dates = all_dates[all_dates <= cutoff]   # 训练集截止 val_start 前 purge_months
val_dates   = all_dates[(all_dates >= val_start) & (all_dates <= val_end)]
```

`purge_months`（默认 2）的作用：在训练集最后一期和验证集第一期之间插入 2 个月的缓冲，防止训练集末期的 forward return 的 exit_date 落入验证期，造成隐式数据泄露。

**purge 的必要性**：

```
训练集末期 T_last 的 forward return：
  fwd_ret = open[T_last'+1] / open[T_last+1] - 1
  T_last' ≈ T_last 后 1 个月
  T_last'+1 ≈ T_last 后约 1.5 个月

若 val_start = T_last + 1 个月（无 purge），
  T_last'+1 落入验证期 → 训练样本中包含验证期信息 → 数据泄露
```

`purge_months=2` 确保 `T_last'+1 < val_start`，从而消除这一风险。

### 指数衰减的 PIT 安全性

样本权重 `w_k = λ^(T-1-k)` 只是改变各历史期的**损失贡献比例**，不改变哪些日期可以进入训练集的判断。PIT 约束（只用 `<= cutoff` 的日期）由父类保证，衰减权重不影响这一约束。

---

## 三、模块顶部：导入与常量

```python
# L19–28
from __future__ import annotations
import logging
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from experiments.legacy.ridge_signal.ridge_combiner import RidgeCombiner
```

**父类路径**：`experiments/legacy/ridge_signal/ridge_combiner.py`  
这是一个从实验代码升级到生产的模式：父类保留在 `experiments/legacy`（历史兼容），子类在 `src/signal`（主线使用）。如果将来父类需要重构，应考虑将稳定的部分迁移到 `src/signal` 目录。

---

## 四、核心函数逐一解析

### 4.1 `__init__` — 构造函数

```python
# L32–61
class RidgeDecayCombiner(RidgeCombiner):
    def __init__(
        self,
        factor_names: list[str],
        half_life_months: int = 24,
        **kwargs,                  # 传给 RidgeCombiner（alpha_candidates, purge_months 等）
    ) -> None:
```

**核心参数**：

| 参数 | 含义 | 典型值 |
|------|------|--------|
| `half_life_months` | 衰减半衰期（单位：月） | 24 / 36 / 48 |
| `**kwargs` | 传递给父类的参数 | `alpha_candidates`, `purge_months`, `min_train_months` 等 |

**λ 的计算**（L61）：
```python
self.lam: float = 0.5 ** (1.0 / self.half_life_months)
```

| half_life | λ | 含义 |
|-----------|---|------|
| 24 | ≈ 0.9716 | 24 个月前的样本权重 = 最新样本的 50% |
| 36 | ≈ 0.9807 | 36 个月前的样本权重 = 最新样本的 50% |
| 48 | ≈ 0.9856 | 48 个月前的样本权重 = 最新样本的 50% |

`half_life_months <= 0` 时抛 `ValueError`（L58-59），快速失败。

---

### 4.2 `_build_sample_weights` — 指数衰减权重构造

```python
# L67–106
def _build_sample_weights(
    self,
    train_dates: pd.DatetimeIndex,
    n_per_date: list[int],
) -> np.ndarray:
```

**权重公式**：

```
第 k 个日期（k=0 最旧，k=T-1 最新）的原始权重：
  w_k = λ^(T-1-k)

其中 T = len(train_dates)（训练日期总数）
```

**示例**（hl=24，λ≈0.972，T=36 个训练月）：

```
k=0  （最旧，36 个月前）: w = 0.972^35 ≈ 0.37
k=17 （中间，18 个月前）: w = 0.972^18 ≈ 0.61
k=35 （最新）           : w = 0.972^0  = 1.00
```

**归一化到均值 1**（L103–105）：
```python
if sw.size > 0 and sw.mean() > 0:
    sw = sw / sw.mean()
```

**为什么归一化到均值 1 而非总和 1？**  
sklearn 的 Ridge 中，`sample_weight` 的作用是对每个样本的**平方损失**乘以权重。若权重总和差异很大（不同 hl 时总和不同），等效于改变了正则化强度 α 的相对大小，导致不同 hl 的最优 α 不可比较，CV 选出的 α 无法泛化。  
归一化到均值 1 确保无论 hl 为多少，样本总权重始终等于样本数，Ridge 的 α 在不同 hl 间具有相同的正则化语义。

**不变量验证**（L90–94）：
```python
if len(train_dates) != len(n_per_date):
    raise ValueError(...)
```

`len(n_per_date)` 必须等于 `len(train_dates)`（每个训练日期对应一个样本数，即使该期样本数为 0）。

**构造逻辑**（L99–101）：
```python
for k, n_obs in enumerate(n_per_date):
    w = self.lam ** (n_total - 1 - k)
    weights.extend([w] * int(n_obs))    # 该日期所有股票使用相同权重
```

同一调仓日的所有股票获得**相同权重**（时间维度衰减，非股票维度）。

---

### 4.3 `_build_training_matrix_with_counts` — 训练矩阵构建（返回每期样本数）

```python
# L112–199
def _build_training_matrix_with_counts(
    self,
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    training_dates: pd.DatetimeIndex,
    bench_ret_series: pd.Series | None = None,
) -> tuple[np.ndarray, np.ndarray, list[int]]:
```

**为什么需要这个方法？**  
父类的 `_build_training_matrix` 只返回 `(X, y)`，不返回每期实际的样本数。而 `_build_sample_weights` 需要知道每个训练日期对应多少个有效股票（`n_per_date[k]`），才能构造正确长度的权重数组。

本方法**完全复制**父类的过滤逻辑，额外返回 `n_per_date`：

**不变量**：
```
len(n_per_date) == len(training_dates)
sum(n_per_date) == len(X_train)         ← X_train 行数等于总样本数
```

**过滤规则**（与父类一致，逐期遍历）：

```python
# 每个训练日期 T：
1. T 不在 fwd_ret_panel.index → n_per_date.append(0); continue
2. 某因子在 T 不在 factor_panels → n_per_date.append(0); continue
3. 有效股票数（n_valid >= min_valid_factors 且 fwd_ret 非 NaN）< 10 → append(0)
4. 通过 → cs_train.fillna(0.0)（缺失因子填 0，Ridge 对 0 特征有正则惩罚效果）
         → n_per_date.append(n_obs)
```

**`use_excess_return` 分支**（L178–185）：若父类参数 `use_excess_return=True` 且提供 `bench_ret_series`，y 为超额收益（个股收益 - 基准当期收益），否则为绝对收益。

**返回**：`(X: float64 (N, n_factors), y: float64 (N,), n_per_date: list[int])`

---

### 4.4 `_process_cv_fold` — CV 折训练（覆写父类）

```python
# L205–274
def _process_cv_fold(
    self,
    fold_idx: int,
    val_start_str: str,
    val_end_str: str,
    factor_panels, fwd_ret_panel, all_dates, bench_ret_series,
) -> list[dict]:
```

**与父类 `_process_cv_fold` 的唯一区别**（L242–243）：

```python
# 父类：model.fit(X_train, y_train)
# 子类：model.fit(X_train, y_train, sample_weight=sample_weight)

sample_weight = self._build_sample_weights(train_dates, n_per_date)
model.fit(X_train, y_train, sample_weight=sample_weight)
```

其余逻辑完全一致：expanding 训练集、IC_IR 验证评分、每个 alpha 候选的结果记录。

**安全检查**（L243–247）：
```python
if len(sample_weight) != len(X_train):
    raise RuntimeError(
        f"CV fold {fold_idx}: sample_weight 长度 {len(sample_weight)} ≠ X_train 行数 {len(X_train)}"
    )
```

权重数组长度必须精确等于训练样本数，否则抛 `RuntimeError`（而非 `ValueError`，因为这是逻辑异常，不是参数错误）。

**日志输出**（L249–253）：
```python
log.info(
    "CV fold %d [%s~%s]: decay hl=%dm  %d 训练日  %d obs  %d 验证日",
    fold_idx, val_start_str, val_end_str,
    self.half_life_months, len(train_dates), len(X_train), len(val_dates),
)
```

打印 `hl=%dm`，方便在日志中区分不同 half_life 的实验。

---

### 4.5 `_fit_and_predict` — 最终预测拟合（覆写父类）

```python
# L276–315
def _fit_and_predict(
    self,
    T: pd.Timestamp,
    factor_panels, fwd_ret_panel, all_dates, bench_ret_series,
) -> tuple[pd.Series | None, dict[str, float] | None]:
```

**用途**：在生产预测阶段（不是 CV），对每个调仓日 T 用全部历史（`cutoff = T - purge_months`）拟合 Ridge，预测 T 期截面信号。

**主要流程**：
1. `train_dates = all_dates[all_dates <= cutoff]`（expanding，截止 purge 前）
2. `_build_training_matrix_with_counts()` 构建 `(X_train, y_train, n_per_date)`
3. `_build_sample_weights(train_dates, n_per_date)` 构建衰减权重
4. `Ridge(alpha=self.alpha_).fit(X_train, y_train, sample_weight=sample_weight)`
5. `_predict_cross_section(model, factor_panels, T)` 预测 T 期截面（复用父类）
6. 返回 `(signal_t, coef_t)`

**`self.alpha_` 的来源**：在调用 `_fit_and_predict` 前，必须先调用 `select_alpha_walk_forward()`（父类方法）确定最优 alpha，其结果存储在 `self.alpha_` 中。

---

## 五、内部辅助函数

本模块无额外私有辅助函数。关键辅助逻辑（`_predict_cross_section`、`_compute_ic_ir`）来自父类 `RidgeCombiner`，此处不展开。

---

## 六、落盘产物（Artifacts）

`ridge_decay.py` 本身**不直接写文件**。产物由 `stages.py` 写入：

| 产物 | 路径 | 内容 |
|------|------|------|
| 合成信号面板 | `runs/train_valid/<run_id>/signal/signal.parquet` | date × stock Ridge 预测值 |
| 系数历史 | `runs/train_valid/<run_id>/signal/coef_history.parquet` | date × factor 的 Ridge 系数（β） |
| CV 结果 | `runs/train_valid/<run_id>/signal/cv_results.parquet` | 每个 fold × alpha 的 IC_IR 分数 |
| 最优 alpha | `runs/train_valid/<run_id>/signal/best_alpha.json` | 选定的 alpha 值 |

---

## 七、相关测试

**当前状态**：`ridge_decay.py` **没有专项单元测试**（TODO）。

**高优先级待补充测试**：

| 测试场景 | 要点 |
|---------|------|
| `_build_sample_weights` 归一化 | 权重均值应≈1；最新日期权重最大 |
| `_build_sample_weights` 长度一致性 | `sum(n_per_date) == len(返回数组)` |
| `half_life=24` vs `24=48` | hl=48 的权重更"平坦"（近远期差距更小） |
| `_build_training_matrix_with_counts` 不变量 | `len(n_per_date) == len(training_dates)` 且 `sum == len(X)` |
| `half_life_months <= 0` | 构造函数应抛 `ValueError` |
| sample_weight 长度不匹配 | `_process_cv_fold` 应抛 `RuntimeError` |

---

## 八、失败与降级路径

| 失败场景 | 行为 |
|---------|------|
| 训练日期数 < `min_train_months` | `_process_cv_fold` 记录 warning 并返回空列表（跳过该 fold）|
| 训练样本数 < `min_train_obs` | 同上（跳过该 fold 或该期预测） |
| `sample_weight` 长度不匹配 | 抛 `RuntimeError`（中断，不静默处理）|
| 所有 CV fold 均跳过 | 父类 `select_alpha_walk_forward` 返回默认 alpha（通常 alpha_candidates[0]）|
| `_predict_cross_section` 失败 | `_fit_and_predict` 返回 `(None, None)`，该期无信号 |

---

## 九、数据流图

```
factor_panels (dict[name → (date×stock)])      fwd_ret_panel (date×stock)
        │                                              │
        │     all_dates (全量调仓日历)                 │
        └──────────────────┬───────────────────────────┘
                           │
                           ▼
         select_alpha_walk_forward()（继承自 RidgeCombiner）
           │
           └── for each CV fold:
                 _process_cv_fold()
                   │
                   ├── _build_training_matrix_with_counts()
                   │     → (X_train, y_train, n_per_date)
                   │
                   ├── _build_sample_weights(train_dates, n_per_date)
                   │     → sw = λ^(T-1-k) × n_obs_k，均值归一化
                   │
                   └── Ridge.fit(X_train, y_train, sample_weight=sw)
                         → val IC_IR for each alpha
           │
           → self.alpha_ = best alpha
                           │
                           ▼
         build_ridge_panel()（继承自 RidgeCombiner）
           │
           └── for each prediction date T:
                 _fit_and_predict(T)
                   │
                   ├── _build_training_matrix_with_counts(cutoff=T-purge)
                   ├── _build_sample_weights(...)
                   ├── Ridge(alpha=self.alpha_).fit(..., sample_weight=sw)
                   └── _predict_cross_section(model, factor_panels, T)
                         → signal_t (ts_code → float)
                           │
                           ▼
                        signal.parquet（由 stages.py 写入）
```

---

## 十、领域知识补充

### 指数衰减的数学等价

**EW-Ridge**（指数衰减加权 Ridge）在线性高斯假设下等价于以下三种框架（参见 `current work/time_varying_ridge_frameworks.md`）：

1. **RLS（Recursive Least Squares）**：λ-discount 版本，每期更新 β 而非重新拟合
2. **贝叶斯线性回归**：时间变化 prior，λ-discount 对先验施加遗忘
3. **本模块实现（批量 EW-Ridge）**：对历史数据加权后一次性拟合，与 RLS 在线性高斯下结果相同

批量实现的优势：
- 可使用 walk-forward CV 选择 α（RLS 难以进行 CV）
- 与父类共享 CV 和预测框架，代码复用最大化

### 半衰期的选择逻辑

| half_life | 适用场景 | 原理 |
|-----------|---------|------|
| 短（24 月）| 市场 regime 变化快，因子有效性随时间漂移 | 近期数据权重高，旧数据快速被遗忘 |
| 中（36 月）| 平衡近期适应性和长期稳定性 | 默认推荐值 |
| 长（48 月）| 因子稳定性高，市场结构变化慢 | 接近 uniform，退化为标准 expanding Ridge |

验证期实验结论（训练集数据，非测试集结论）：
- hl24 验证期 IR 低于基线，根因待诊断（见 CLAUDE.md §1.1 待解决问题）
- hl36、hl48 待进一步诊断

> **重要声明**：以上结论仅基于验证集（2021-2022）观察，仅用于指导下一步研究方向。测试集尚未使用（剩余 2 次机会）。

### Ridge 的 α 与正则化

Ridge 的目标函数：
```
argmin_β  Σ_i [w_i × (y_i - x_i^T β)^2] + α × ||β||²
```

- **α 越大**：β 被压缩越强，减少过拟合，但信号减弱
- **α 越小**：接近 OLS，可能过拟合（特别是因子数接近样本数时）
- **α 的 CV 候选**：`[0.1, 1.0, 10.0, 100.0, 500.0]`（来自父类 `_DEFAULT_ALPHA_CANDIDATES`）

Ridge 相对 OLS 在多因子截面回归中的优势：
- 因子间高相关（如同一类别的多个因子）时，OLS 系数不稳定；Ridge 对相关特征施加等比例收缩，系数更稳健
- N（截面股票数）≈ 500，p（因子数）≈ 20：问题不是 "p > N" 的严格意义上的 Ridge 必要条件，但因子间相关性导致的条件数问题使 Ridge 仍优于 OLS
