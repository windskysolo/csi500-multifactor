# Time-Varying Ridge 实验计划：EW-Ridge -> Bayesian Sequential Ridge

创建日期：2026-05-28  
适用项目：`E:\Acoding\Project\500 improve`  
目标：在现有 `run_experiment.py` / `ExperimentSpec` / `runs/train_valid/` 框架内，实现并验证时间变 Ridge 信号，先跑 EW-Ridge，再视结果进入 Bayesian Sequential Ridge。

重要约束：

- 本计划只使用训练/验证期 `train_valid`，不运行正式测试集 pipeline。
- 所有实验必须通过 `python -m scripts.run_experiment --spec ...` 进入，不直接写 `data/processed` 主产物。
- 所有产物写入 `runs/train_valid/<timestamp>__<experiment_id>/`。
- 跑完 challenger 后必须更新或显式指定 run_id，否则默认 `compare_runs` 不会自动纳入新 run。
- 阶段 10 测试集入口仍有历史风险，时间变 Ridge 研究阶段不要调用 `scripts.run_test_pipeline`。

---

## 0. 当前基线

验证期：2021-2022，V2 优化权重。

| 方案 | 当前状态 | IR | 备注 |
|---|---:|---:|---|
| expanding Ridge | mainline | 0.408 | `registry/mainline.json` 当前 active run |
| rolling-36m | challenger | 0.966 | 已完成 |
| rolling-48m | challenger | 1.489 | 当前验证期最优，尚未晋升 |
| rolling-60m | challenger | 1.176 | 已完成 |
| decay-hl24/36/48 | pre-registered | 待跑 | 本计划阶段 1 |

已有 decay specs：

```text
configs/pipelines/challenger_decay_ridge_hl24_te6_lam0050.py
configs/pipelines/challenger_decay_ridge_hl36_te6_lam0050.py
configs/pipelines/challenger_decay_ridge_hl48_te6_lam0050.py
```

当前缺口：

- `src/signal/ridge_decay.py` 不存在。
- `src/pipeline/stages.py` 对 `decay_weighted_expanding` 仍抛 `NotImplementedError`。
- `registry/challengers.json` 中 decay 三个条目仍是 `run_id: null`。

---

## 1. 阶段一：EW-Ridge

### 1.1 修改范围

新增：

```text
src/signal/ridge_decay.py
tests/test_ridge_decay.py
```

修改：

```text
src/pipeline/stages.py
registry/challengers.json    # 仅在完整 run 成功后填入 run_id 和指标
```

不修改：

```text
scripts/run_experiment.py
scripts/run_test_pipeline.py
configs/pipelines/challenger_decay_ridge_hl*.py
data/processed/*
runs/test/*
docs/logs/test_set_runs.json
```

### 1.2 `RidgeDecayCombiner` 实现规范

文件：`src/signal/ridge_decay.py`

类名：`RidgeDecayCombiner`，继承 `experiments.legacy.ridge_signal.ridge_combiner.RidgeCombiner`。

必须覆盖：

- `_process_cv_fold(...)`
- `_fit_and_predict(...)`

必须新增：

- `_build_sample_weights(train_dates, n_per_date)`
- `_build_training_matrix_with_counts(...)`

核心要求：

1. `_build_training_matrix_with_counts(...)` 必须复制父类 `_build_training_matrix(...)` 的筛选逻辑，返回 `(X, y, n_per_date)`。
2. `n_per_date` 必须是每个训练日期最终进入 `X` 的有效样本数，而不是原始股票数。
3. `sum(n_per_date) == len(X_train)` 必须成立。
4. sample weight 长度必须等于 `len(X_train)`。
5. sample weight 生成后建议归一化为均值 1，避免不同 half-life 改变 Ridge `alpha` 的有效强度。
6. 必须保留父类的 `use_excess_return` / `bench_ret_series` 语义。
7. 预测仍使用父类 `_predict_cross_section(model, factor_panels, T)`，确保 winsorize/z-score 口径一致。

权重定义：

```python
lam = 0.5 ** (1.0 / half_life_months)
w_k = lam ** (T - 1 - k)
```

其中 `k=0` 是最早训练日期，`k=T-1` 是最近训练日期。

实现骨架：

```python
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from experiments.legacy.ridge_signal.ridge_combiner import RidgeCombiner


class RidgeDecayCombiner(RidgeCombiner):
    def __init__(self, factor_names: list[str], half_life_months: int = 24, **kwargs) -> None:
        super().__init__(factor_names, **kwargs)
        if half_life_months <= 0:
            raise ValueError("half_life_months must be positive")
        self.half_life_months = int(half_life_months)
        self.lam = 0.5 ** (1.0 / self.half_life_months)

    def _build_sample_weights(
        self,
        train_dates: pd.DatetimeIndex,
        n_per_date: list[int],
    ) -> np.ndarray:
        if len(train_dates) != len(n_per_date):
            raise ValueError("train_dates and n_per_date length mismatch")

        n_dates = len(train_dates)
        weights: list[float] = []
        for k, n_obs in enumerate(n_per_date):
            w = self.lam ** (n_dates - 1 - k)
            weights.extend([w] * int(n_obs))

        sw = np.asarray(weights, dtype=np.float64)
        if len(sw) and sw.mean() > 0:
            sw = sw / sw.mean()
        return sw

    def _build_training_matrix_with_counts(
        self,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        training_dates: pd.DatetimeIndex,
        bench_ret_series: pd.Series | None = None,
    ) -> tuple[np.ndarray, np.ndarray, list[int]]:
        # Copy the parent _build_training_matrix filtering logic.
        # Return n_per_date for the final kept observations per date.
        raise NotImplementedError

    def _process_cv_fold(
        self,
        fold_idx: int,
        val_start_str: str,
        val_end_str: str,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        all_dates: pd.DatetimeIndex,
        bench_ret_series: pd.Series | None = None,
    ) -> list[dict]:
        val_start = pd.Timestamp(val_start_str)
        val_end = pd.Timestamp(val_end_str) + pd.offsets.MonthEnd(0)
        cutoff = val_start - pd.DateOffset(months=self.purge_months)

        train_dates = all_dates[all_dates <= cutoff]
        val_dates = all_dates[(all_dates >= val_start) & (all_dates <= val_end)]

        if len(train_dates) < self.min_train_months:
            return []

        X_train, y_train, n_per_date = self._build_training_matrix_with_counts(
            factor_panels, fwd_ret_panel, train_dates, bench_ret_series=bench_ret_series
        )
        if len(X_train) < self.min_train_obs:
            return []

        sample_weight = self._build_sample_weights(train_dates, n_per_date)
        if len(sample_weight) != len(X_train):
            raise RuntimeError("sample_weight length does not match X_train")

        records: list[dict] = []
        for alpha in self.alpha_candidates:
            model = Ridge(alpha=alpha, fit_intercept=True)
            model.fit(X_train, y_train, sample_weight=sample_weight)
            ic_ir = self._compute_ic_ir(model, factor_panels, fwd_ret_panel, val_dates)
            records.append({
                "fold": fold_idx,
                "val_start": val_start_str,
                "val_end": val_end_str,
                "n_train_obs": len(X_train),
                "alpha": alpha,
                "ic_ir": ic_ir,
            })
        return records

    def _fit_and_predict(
        self,
        T: pd.Timestamp,
        factor_panels: dict[str, pd.DataFrame],
        fwd_ret_panel: pd.DataFrame,
        all_dates: pd.DatetimeIndex,
        bench_ret_series: pd.Series | None = None,
    ):
        cutoff = T - pd.DateOffset(months=self.purge_months)
        train_dates = all_dates[all_dates <= cutoff]

        if len(train_dates) < self.min_train_months:
            return None, None

        X_train, y_train, n_per_date = self._build_training_matrix_with_counts(
            factor_panels, fwd_ret_panel, train_dates, bench_ret_series=bench_ret_series
        )
        if len(X_train) < self.min_train_obs:
            return None, None

        sample_weight = self._build_sample_weights(train_dates, n_per_date)
        if len(sample_weight) != len(X_train):
            raise RuntimeError("sample_weight length does not match X_train")

        model = Ridge(alpha=self.alpha_, fit_intercept=True)
        model.fit(X_train, y_train, sample_weight=sample_weight)

        signal_t = self._predict_cross_section(model, factor_panels, T)
        coef_t = dict(zip(self.factor_names, model.coef_)) if signal_t is not None else None
        return signal_t, coef_t
```

### 1.3 `stages.py` 接入位置

文件：`src/pipeline/stages.py`

不要在 `run_signal_stage()` 里实例化 combiner。那里没有 `factor_names` 等上下文。

正确改法：

1. `run_signal_stage()` 中删除/替换 `decay_weighted_expanding` 的早期 `NotImplementedError`。
2. 在 `_run_signal_ridge(...)` 内和 `rolling` / `expanding` 同级分支处理。
3. 增加未知 mode 的显式报错，避免拼错后静默跑成 expanding。

目标结构：

```python
from experiments.legacy.ridge_signal.ridge_combiner import RidgeCombiner
from experiments.legacy.ridge_rolling.rolling_combiner import RidgeRollingCombiner

if mode == "rolling":
    if not sig.window_months:
        raise ValueError("rolling 模式需要 spec.signal.window_months")
    combiner = RidgeRollingCombiner(
        factor_names=factor_names,
        window_months=sig.window_months,
        alpha_candidates=sig.alpha_grid,
        purge_months=sig.purge_months,
    )
elif mode == "decay_weighted_expanding":
    if not sig.half_life_months:
        raise ValueError("decay_weighted_expanding 模式需要 spec.signal.half_life_months")
    from src.signal.ridge_decay import RidgeDecayCombiner

    combiner = RidgeDecayCombiner(
        factor_names=factor_names,
        half_life_months=sig.half_life_months,
        alpha_candidates=sig.alpha_grid,
        purge_months=sig.purge_months,
    )
elif mode == "expanding":
    combiner = RidgeCombiner(
        factor_names=factor_names,
        alpha_candidates=sig.alpha_grid,
        purge_months=sig.purge_months,
    )
else:
    raise ValueError(
        f"未知 ridge training_mode: {mode!r}. "
        "支持: expanding, rolling, decay_weighted_expanding"
    )
```

metadata 也要补 half-life：

```python
_write_signal_metadata(signal_dir, spec, {
    "method": "ridge",
    "training_mode": mode,
    "window_months": sig.window_months if mode == "rolling" else None,
    "half_life_months": sig.half_life_months if mode == "decay_weighted_expanding" else None,
    "selected_alpha": getattr(combiner, "alpha_", None),
    "n_factors": len(factor_names),
    "n_rebalance_dates": len(tv_dates),
})
```

### 1.4 必加测试

新增文件：`tests/test_ridge_decay.py`

最低测试集：

```python
def test_decay_sample_weights_length_and_order():
    # older date weight < newer date weight
    # len(sample_weight) == sum(n_per_date)
    # mean(sample_weight) approximately 1


def test_decay_requires_positive_half_life():
    # half_life_months <= 0 raises ValueError


def test_decay_training_matrix_counts_match_rows():
    # small synthetic factor_panels/fwd_ret_panel
    # assert sum(n_per_date) == len(X)


def test_unknown_ridge_training_mode_raises():
    # protect against typo silently becoming expanding
```

现有完整测试仍必须通过：

```powershell
python -m pytest tests -q --basetemp=.codex_tmp_pytest_timevarying -p no:cacheprovider
```

### 1.5 运行顺序

先只跑一个信号阶段，确认实现没有结构错误：

```powershell
python -m scripts.run_experiment --spec configs/pipelines/challenger_decay_ridge_hl24_te6_lam0050.py --to-stage signal
```

检查该 run：

```text
runs/train_valid/<run_id>/
├── run_config.json
├── inputs.lock.json
├── manifest.json
├── RUN_FINISHED.json
└── signal/
    ├── composite.parquet
    ├── coef_history.parquet
    └── signal_metadata.json
```

信号阶段通过后，再完整跑 hl24：

```powershell
python -m scripts.run_experiment --spec configs/pipelines/challenger_decay_ridge_hl24_te6_lam0050.py
```

确认完整 run 有：

```text
portfolio/target_weights.parquet
portfolio/baseline_weights.parquet
backtest/nav_valid.parquet
backtest/metrics_valid.parquet
reports/self_check.md
RUN_FINISHED.json
```

hl24 完整通过后，再顺序跑 hl36 / hl48。不要并行跑，避免日志和重型计算资源互相干扰。

```powershell
python -m scripts.run_experiment --spec configs/pipelines/challenger_decay_ridge_hl36_te6_lam0050.py
python -m scripts.run_experiment --spec configs/pipelines/challenger_decay_ridge_hl48_te6_lam0050.py
```

### 1.6 registry 与 compare

完整 run 成功后有两种比较方式。

临时比较，不改 registry：

```powershell
python -m scripts.compare_runs --run-ids `
  20260527_141545__baseline_expanding_ridge_te6_lam0050 `
  20260527_142056__challenger_rolling48_te6_lam0050 `
  <decay_hl24_run_id> `
  <decay_hl36_run_id> `
  <decay_hl48_run_id> `
  --output-dir reports
```

长期规范比较：

1. 更新 `registry/challengers.json` 中对应 decay 条目：
   - `run_id`: 新 run_id
   - `status`: `researching`
   - `metrics_v2`: 从 `backtest/metrics_valid.parquet` 或 compare board 填入
   - `notes`: 记录 half-life、alpha、是否通过 hard threshold
2. 运行：

```powershell
python -m scripts.compare_runs --output-dir reports
```

不要因为 decay 暂时表现好就直接改 `registry/mainline.json`。是否晋升必须单独审查，并通过 `scripts/promote_run.py`。

### 1.7 EW-Ridge 验收标准

- `python -m pytest tests -q ...` 通过。
- hl24/36/48 三个完整 run 都有 `RUN_FINISHED.json`。
- 每个 decay run 都有非空 `signal/coef_history.parquet`。
- 每个 decay run 的 `signal/signal_metadata.json` 包含 `training_mode=decay_weighted_expanding`、`half_life_months`、`selected_alpha`。
- 每个 decay run 都有 `reports/self_check.md`。
- `reports/experiment_board.md` 纳入 mainline、rolling36/48/60、decay24/36/48。
- registry 中 decay 三个 challenger 不再是 `run_id: null`。

---

## 2. 阶段二：Bayesian Sequential Ridge

只有在 EW-Ridge 结果至少优于 expanding baseline，或诊断上显示值得继续时，才进入本阶段。

### 2.1 修改范围

新增：

```text
src/signal/ridge_bayes.py
configs/pipelines/challenger_bayes_ridge_hl24_te6_lam0050.py
configs/pipelines/challenger_bayes_ridge_hl24_confwt_te6_lam0050.py
tests/test_ridge_bayes.py
```

修改：

```text
src/pipeline/stages.py
registry/challengers.json    # 先预注册 bayes 两个 challenger，run_id=null
```

### 2.2 设计口径

Bayesian Sequential Ridge 与 EW-Ridge 不应写成“必然完全等价”。更稳妥的实验口径：

- `bayesian_sequential`：验证与 EW-Ridge 的近似一致性。
- `bayesian_sequential_confwt`：使用后验不确定性做 confidence weighting，观察是否提升 IR。

等价性指标建议：

- `bayes_hl24` vs `decay_hl24` 的 `composite.parquet` 截面相关均值 >= 0.98 可接受。
- 若低于 0.98，先检查 intercept、sample weight 归一化、alpha 定义、sigma2 和折扣公式，不直接做结论。

### 2.3 `RidgeBayesianCombiner` 实现注意事项

文件：`src/signal/ridge_bayes.py`

必须实现：

- `__init__()` 中初始化 `_processed_dates_: set[pd.Timestamp]`
- `_init_prior(p)`
- `_estimate_sigma2(X, y)`
- `_get_period_data(...)`
- `_update(X_t, y_t)`
- `_effective_coef()`
- `_predict_cross_section_from_coef(...)`
- `build_ridge_panel(...)`

不要把 numpy 系数直接传给父类 `_predict_cross_section()`，父类需要 sklearn model 且调用 `model.predict(...)`。Bayes 版应实现自己的 `_predict_cross_section_from_coef(coef, factor_panels, T)`，但筛选、0-fill、winsorize、standardize 规则要和父类一致。

必须输出：

```text
signal/composite.parquet
signal/coef_history.parquet
signal/sigma_diag_history.parquet
signal/signal_metadata.json
```

### 2.4 Bayes configs 必须完整

不要用省略号配置。每个 config 必须完整定义：

```python
from src.pipeline.contracts import BacktestSpec, ExperimentSpec, OptimizerSpec, SignalSpec

SPEC = ExperimentSpec(
    experiment_id="challenger_bayes_ridge_hl24_te6_lam0050",
    description="候选线：Bayesian sequential Ridge，discount half-life=24，无 confidence weighting",
    period_scope="train_valid",
    signal=SignalSpec(
        method="ridge",
        target="excess_return",
        training_mode="bayesian_sequential",
        purge_months=2,
        half_life_months=24,
        alpha_grid=[2000.0],
        selected_alpha_policy="fixed",
    ),
    optimizer=OptimizerSpec(
        te_target_annual=0.06,
        industry_max_dev=0.03,
        single_max_dev=0.015,
        turnover_lambda=0.005,
        topn=50,
    ),
    backtest=BacktestSpec(
        execution="tplus1_open",
        cost_model="china_a_share_v1",
        benchmark="CSI500_TOTAL_RETURN",
    ),
)
```

confidence weighting 版本只改：

```python
experiment_id="challenger_bayes_ridge_hl24_confwt_te6_lam0050"
training_mode="bayesian_sequential_confwt"
```

### 2.5 `stages.py` Bayes 接入

在 `_run_signal_ridge(...)` 中新增分支，仍要保留 explicit unknown mode error：

```python
elif mode in ("bayesian_sequential", "bayesian_sequential_confwt"):
    if not sig.half_life_months:
        raise ValueError(f"{mode} 模式需要 spec.signal.half_life_months")
    from src.signal.ridge_bayes import RidgeBayesianCombiner

    alpha = sig.alpha_grid[0] if sig.alpha_grid else 2000.0
    combiner = RidgeBayesianCombiner(
        factor_names=factor_names,
        half_life_months=sig.half_life_months,
        alpha=alpha,
        confidence_weighting=(mode == "bayesian_sequential_confwt"),
        purge_months=sig.purge_months,
    )
```

Bayes 版不需要调用 `select_alpha_walk_forward()`，但必须让后续代码能统一：

- 生成 `composite`
- 保存 `coef_history_`
- 保存 `sigma_diag_history_`
- 写 metadata

如果 `_run_signal_ridge(...)` 现有代码假设所有 combiner 都有 `select_alpha_walk_forward()`，需要把 signal 构建拆成：

- Ridge/rolling/decay：CV 选择 alpha 后 build
- Bayes：固定 alpha 后 build

### 2.6 Bayes 运行顺序

先只跑 signal：

```powershell
python -m scripts.run_experiment --spec configs/pipelines/challenger_bayes_ridge_hl24_te6_lam0050.py --to-stage signal
```

signal 验收后完整跑：

```powershell
python -m scripts.run_experiment --spec configs/pipelines/challenger_bayes_ridge_hl24_te6_lam0050.py
python -m scripts.run_experiment --spec configs/pipelines/challenger_bayes_ridge_hl24_confwt_te6_lam0050.py
```

跑完后更新 `registry/challengers.json` 或用 `compare_runs --run-ids` 显式比较。

### 2.7 Bayes 验收标准

- `sigma_diag_history.parquet` 存在且非空。
- `coef_history.parquet` 存在且非空。
- `bayes_hl24` 与 `decay_hl24` 的 composite 截面相关均值记录在报告中。
- `bayes_hl24_confwt` 与 `bayes_hl24` 的 IR、MDD、turnover、月胜率差异记录在 `current work/` 报告中。
- `reports/experiment_board.md` 纳入 Bayes 两个 run。

---

## 3. 诊断与报告

### 3.1 指标比较

最终比较表至少包含：

| 实验 | run_id | mode | hl/window | selected_alpha | IR | 年化超额 | 超额MDD | TE | 月胜率 | 年化换手 | 结论 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| mainline | 20260527_141545 | expanding | - | 5000 | 0.408 | +2.44% | -8.16% | 5.97% | 43.5% | 974% | baseline，IR FAIL |
| rolling48 | 20260527_142056 | rolling | 48 | 5000 | 1.489 | +8.63% | -5.82% | 5.80% | 56.5% | 1002% | current best ✅ |
| decay_hl24 | 20260528_141041 | decay | 24 | 5000 | 0.626 | +3.65% | -8.32% | 5.82% | 39.1% | 1041% | 优于 expanding，PASS ✅ |
| decay_hl36 | 20260528_141324 | decay | 36 | 5000 | 0.295 | +1.71% | -8.79% | 5.79% | 43.5% | 1008% | 比 expanding 还差，FAIL ❌ |
| decay_hl48 | 20260528_141729 | decay | 48 | 5000 | 0.289 | +1.68% | -8.28% | 5.81% | 39.1% | 1008% | 比 expanding 还差，FAIL ❌ |
| bayes_hl24 | 待跑 | bayes | 24 | 2000 | 待填 | | | | | | |
| bayes_hl24_confwt | 待跑 | bayes_confwt | 24 | 2000 | 待填 | | | | | | |

### 3.2 年度超额

用 `backtest/nav_valid.parquet` 计算 2021、2022 分年度超额，重点看：

- EW-Ridge 是否比 rolling48 更平滑。
- 是否只是某一年贡献了全部 IR。
- 是否提高 IR 但显著增加换手或回撤。

输出建议：

```text
current work/charts/time_varying_ridge_yearly_excess.png
current work/charts/time_varying_ridge_excess_nav.png
```

### 3.3 系数稳定性

读取 `signal/coef_history.parquet`：

```python
coef_df = pd.read_parquet("runs/train_valid/<run_id>/signal/coef_history.parquet")
coef_change = coef_df.diff().abs()
print(coef_change.mean().sort_values(ascending=False))
```

比较：

- rolling48 vs decay_hl24/36/48
- decay_hl24 vs bayes_hl24
- bayes_hl24 vs bayes_hl24_confwt

---

## 4. 最终验证命令

实现后，至少运行：

```powershell
python -m pytest tests -q --basetemp=.codex_tmp_pytest_timevarying -p no:cacheprovider
python -m scripts.compare_runs --output-dir .codex_tmp_compare_timevarying
python -m scripts.run_experiment --help
python -m scripts.compare_runs --help
```

清理临时目录：

```powershell
$root=(Resolve-Path '.').Path
foreach ($name in @('.codex_tmp_pytest_timevarying','.codex_tmp_compare_timevarying')) {
  $p=Resolve-Path -LiteralPath $name -ErrorAction SilentlyContinue
  if ($p -and $p.Path.StartsWith($root)) {
    Remove-Item -LiteralPath $p.Path -Recurse -Force -ErrorAction SilentlyContinue
  }
}
```

禁止在本研究阶段运行：

```powershell
python -m scripts.run_test_pipeline --run-id 1
python -m scripts.run_test_pipeline --run-id 2
```

---

## 5. 完成定义

阶段一完成定义：

- EW-Ridge 代码和测试完成。
- hl24/36/48 三个 train_valid run 完成。
- registry 中 decay 三个 challenger 已填 run_id。
- `reports/experiment_board.md` 可复现 decay 对比。
- `current work/` 下有一份简短实验结论记录。

阶段二完成定义：

- Bayes 两个 config 预注册并完成 train_valid run。
- `sigma_diag_history.parquet` 和 Bayes 诊断通过。
- Bayes 与 EW-Ridge 近似一致性有量化记录。
- confidence weighting 是否有效有明确结论。

任何阶段如果出现 `RUN_FAILED.json`，先修失败原因，不要手动删除 run 目录；失败 run 保留用于审计。
