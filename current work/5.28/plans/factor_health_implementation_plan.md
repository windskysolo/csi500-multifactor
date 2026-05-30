# 因子健康监控体系 - 可执行实施计划 v1.1（修正版）

> **文档版本**：v1.1  
> **修订日期**：2026-05-29  
> **适用策略**：中证500指数增强，月度调仓  
> **依据文档**：`current work/5.28/plans/factor_health_system.md`、`docs/PROJECT_PLAN_v1.1.md`  
> **本版目标**：修正 v1.0 中不可直接运行、因子池口径过期、训练/验证边界不清的问题，保证按阶段实现后每一步都有可执行命令和验收标准。

---

## 0. 当前仓库事实（执行前必须以此为准）

本计划基于 2026-05-29 当前仓库状态：

| 项目 | 当前事实 |
|---|---|
| 训练期 | `2012-01-01` 至 `2020-12-31` |
| 验证期 | `2021-01-01` 至 `2022-12-31` |
| 测试期 | `2023-01-01` 至 `2025-12-31`，当前有效使用 0 次 |
| 因子面板 | `data/processed/factor_panels/` 当前约 58 个 parquet |
| 已评价因子 | `reports/factor_evaluation/ic_result.csv` 当前 55 个 |
| 当前最终入模因子 | `reports/factor_evaluation/final_factors.json` 当前 18 个 |
| `ic_history` | 当前不存在，必须先实现阶段一 |
| 旧计划错误点 | `ic_series_map` 是 `batch_ic_test()` 局部变量，`run_factor_evaluation.py` 不能直接读取 |

当前最终入模因子以 `final_factors.json` 为准，不在计划中写死。健康监控默认监控最终入模因子；全量 55 个因子只用于诊断和候选池观察。

---

## 1. 执行纪律

1. **先隔离输出，后覆盖主目录**  
   开发验证一律输出到 `reports/factor_evaluation_health_probe/`、`reports/factor_health_probe/`。确认无误后，才允许写入 `reports/factor_evaluation/` 和 `reports/factor_health/`。

2. **测试集保护**  
   本计划所有命令不得使用 `--allow-test-set`，不得读取 `data/processed/factor_panels_test_run_*` 或 `runs/test/`。诊断只允许用训练期和验证期。

3. **验证期只做诊断，不回流筛选**  
   2021-2022 的 IC 可以用于健康报告和根因诊断，但不能直接作为 `final_include` 的新筛选条件。是否调池必须另起实验，在训练/验证期内重新验证，并记录原因。

4. **一个阶段一个变量**  
   阶段一只做 IC 历史持久化；阶段二只做健康监控；阶段三以后才做 Piotroski、Chow、Fama-MacBeth 诊断。

5. **不改核心流水线**  
   本计划不修改 `src/pipeline/`、`src/backtest/`、`src/signal/`。改动集中在 `src/evaluation/`、`scripts/run_factor_evaluation.py`、新增诊断脚本和测试。

---

## 2. 预检命令（现在即可运行）

这些命令只读文件，不改任何产物：

```powershell
python -c "from scripts.test_set_ledger import count_test_set_runs, remaining_test_set_runs; print('used', count_test_set_runs(), 'remaining', remaining_test_set_runs())"
```

```powershell
python -c "import json; j=json.load(open(r'reports/factor_evaluation/final_factors.json', encoding='utf-8')); print(len(j['final_factors'])); print(j['final_factors'])"
```

```powershell
python -c "import pandas as pd; s=pd.read_csv(r'reports/factor_evaluation/ic_result.csv', index_col=0); print(s.shape); print(s.index[:10].tolist())"
```

预期：

- 测试集已用次数为 0，剩余 2。
- `final_factors.json` 为 18 个因子左右，以实际输出为准。
- `ic_result.csv` 为 55 个左右，以实际输出为准。

---

## 阶段一：IC 历史矩阵持久化（P0，必须先做）

### 目标

新增可复用的 IC 历史构建函数，并在 `run_factor_evaluation.py` 中输出清晰区分训练期、验证期、研究诊断期的 parquet。不得再假设脚本能读取 `batch_ic_test()` 内部的 `ic_series_map`。

### 改动文件

| 动作 | 文件 | 说明 |
|---|---|---|
| 修改 | `src/evaluation/ic_analysis.py` | 新增 `build_ic_history()`，复用 `compute_ic_series()` |
| 修改 | `scripts/run_factor_evaluation.py` | 调用 `build_ic_history()` 并落盘 |
| 修改 | `tests/test_evaluation.py` | 增加 IC 历史矩阵单元测试 |

### 新增函数

在 `src/evaluation/ic_analysis.py` 增加：

```python
def build_ic_history(
    factor_panels: dict[str, pd.DataFrame],
    fwd_ret_panel: pd.DataFrame,
    factor_names: list[str] | None = None,
    min_coverage: float = 0.0,
) -> pd.DataFrame:
    """
    构建 date x factor 的月度 Rank IC 历史矩阵。

    输入：
        factor_panels: 因子面板字典，行=调仓日，列=ts_code。
        fwd_ret_panel: 下期收益面板，必须已按训练/验证边界切分。
        factor_names: 可选因子列表；None 表示使用全部 factor_panels。
        min_coverage: 覆盖率下限，传给 compute_ic_series。
    输出：
        DataFrame，index=rebalance_date，columns=factor_name，dtype=float64。
    时间对齐假设：
        本函数不自行切分样本期；调用方必须传入已按 exit_date 过滤后的 fwd_ret_panel。
    数据依赖：
        仅依赖传入的因子面板和 forward return 面板，不读取磁盘。
    """
```

实现要点：

- `factor_names is None` 时使用 `list(factor_panels)`。
- 如果 `factor_names` 中有缺失因子，抛 `KeyError`，不要静默忽略。
- 每个因子调用 `compute_ic_series(panel, fwd_ret_panel, min_coverage)`。
- 返回值 `index.name = "rebalance_date"`，并 `sort_index()`。

### 输出文件

在 `run_factor_evaluation.py` 里，完成 `train_fwd`、`valid_fwd` 切分后生成：

| 文件 | 范围 | 因子范围 | 用途 |
|---|---|---|---|
| `ic_history_train_all.parquet` | 训练期，`exit_date <= TRAIN_END` | 全部已评价因子 | 训练期诊断，允许参与筛选逻辑审计 |
| `ic_history_valid_all.parquet` | 验证期，`TRAIN_END < exit_date <= VALID_END` | 全部已评价因子 | 验证期诊断，不允许回流筛选 |
| `ic_history_research_all.parquet` | 训练+验证 | 全部已评价因子 | 健康监控/根因诊断 |
| `ic_history_final_research.parquet` | 训练+验证 | `final_factors.json` 对应因子 | 健康监控默认输入 |
| `ic_history_metadata.json` | 元信息 | - | 记录样本边界、因子数量、是否含验证期 |

注意：不使用含义模糊的 `ic_history.parquet` 作为主输出名，避免未来把含验证期的诊断数据误用于训练筛选。

### 单元测试

在 `tests/test_evaluation.py` 增加测试：

- `build_ic_history()` 返回 `date x factor` 矩阵。
- `factor_names` 能限制输出列和列顺序。
- 缺失因子会抛 `KeyError`。
- 输出 index 名为 `rebalance_date`。

### 验证命令

先跑单测：

```powershell
python -m pytest tests\test_evaluation.py -q
```

再用隔离目录跑评价脚本：

```powershell
python -m scripts.run_factor_evaluation --output-dir reports/factor_evaluation_health_probe --no-plots
```

检查输出：

```powershell
python -c "import pandas as pd; base=r'reports/factor_evaluation_health_probe'; files=['ic_history_train_all.parquet','ic_history_valid_all.parquet','ic_history_research_all.parquet','ic_history_final_research.parquet']; [print(f, pd.read_parquet(base+'/'+f).shape) for f in files]"
```

验收标准：

- `ic_history_train_all.parquet` 约为 `106 x 55`。
- `ic_history_valid_all.parquet` 约为 `22 x 55`。
- `ic_history_final_research.parquet` 约为 `128 x 18`。
- 每列有效值比例建议不低于 80%；覆盖不足因子要在 metadata 中披露，不要直接删除。

确认后才允许刷新主目录：

```powershell
python -m scripts.run_factor_evaluation --output-dir reports/factor_evaluation --no-plots
```

---

## 阶段二：滚动健康监控器（P1）

### 目标

建立独立健康监控模块，读取阶段一的 IC 历史矩阵和 `factor_summary.csv` 中的 `factor_direction`，输出健康快照、面板图和 Markdown 报告。

### 改动文件

| 动作 | 文件 | 说明 |
|---|---|---|
| 新增 | `src/evaluation/factor_health.py` | `FactorHealthMonitor` 和健康计算函数 |
| 新增 | `scripts/run_factor_health.py` | 命令行入口 |
| 新增 | `tests/test_factor_health.py` | 健康状态和滚动窗口测试 |
| 修改 | `docs/FILE_MAP.md` | 完成后补充脚本说明 |

### 设计边界

`factor_health.py` 只依赖：

- `pandas`
- `numpy`
- `matplotlib`（只在绘图函数中使用）

不得依赖：

- `src/pipeline/`
- `src/signal/`
- `src/backtest/`
- 测试集目录

### 健康状态规则

使用方向调整后的 24 个月 IC_IR：

```text
adjusted_ic_ir_24m = ic_ir_24m * factor_direction
```

| 状态 | 判定 |
|---|---|
| `STABLE` | `adjusted_ic_ir_24m >= 0.30` |
| `WEAK` | `0.20 <= adjusted_ic_ir_24m < 0.30` |
| `WARN` | `0 <= adjusted_ic_ir_24m < 0.20` |
| `REVERSE` | `adjusted_ic_ir_24m < 0` |
| `UNKNOWN` | 有效样本不足 |

趋势预警：

- `adjusted_ic_ir_12m < adjusted_ic_ir_36m - 0.15` 时标记 `DETERIORATING`。
- 样本不足时标记 `INSUFFICIENT_HISTORY`。

### 脚本参数

`scripts/run_factor_health.py` 支持：

```text
--ic-history      默认 reports/factor_evaluation/ic_history_final_research.parquet
--factor-summary  默认 reports/factor_evaluation/factor_summary.csv
--output-dir      默认 reports/factor_health
--as-of           可选，YYYY-MM-DD；默认 IC 历史最后一期
--no-plot         可选，只输出 csv/md
--chow            阶段四实现后启用
```

如果 `factor_summary.csv` 缺少某个因子的 `factor_direction`，脚本应报错并列出缺失因子，不能默认全部正向。

### 输出文件

| 文件 | 内容 |
|---|---|
| `health_snapshot.csv` | 每个因子的 12/24/36m IC_IR、方向调整后 IC_IR、状态、趋势预警、建议动作 |
| `health_report.md` | 人类可读报告，注明包含验证期、仅用于诊断 |
| `health_panel.png` | 动态布局面板图；不要写死 6x3，按因子数自动计算 |

### 验证命令

先跑单测：

```powershell
python -m pytest tests\test_factor_health.py -q
```

用阶段一隔离产物跑健康监控：

```powershell
python -m scripts.run_factor_health --ic-history reports/factor_evaluation_health_probe/ic_history_final_research.parquet --factor-summary reports/factor_evaluation_health_probe/factor_summary.csv --output-dir reports/factor_health_probe
```

检查输出：

```powershell
python -c "import pandas as pd; s=pd.read_csv(r'reports/factor_health_probe/health_snapshot.csv'); print(s.shape); print(s['status'].value_counts(dropna=False).to_string())"
```

验收标准：

- `health_snapshot.csv` 行数等于当前 `final_factors.json` 的因子数。
- `health_report.md` 明确写出：数据范围为训练+验证，验证期仅诊断。
- `health_panel.png` 存在且图中因子数与快照一致。

主目录已刷新阶段一输出后，才运行：

```powershell
python -m scripts.run_factor_health
```

---

## 阶段三：`piotroski_f` 专项诊断（P1）

### 目标

对 `piotroski_f` 的验证期反转做根因诊断。诊断可以用训练+验证 IC 历史，但结论不得直接改 `final_factors.json`。

### 改动文件

| 动作 | 文件 | 说明 |
|---|---|---|
| 新增 | `scripts/diagnose_piotroski.py` | 只读诊断脚本 |
| 新增输出 | `check/0529/piotroski_diagnosis.md` | 诊断结论 |
| 新增输出 | `check/0529/piotroski_component_ic.csv` | F1/F2/F3/F4/F5/F8/F9 分项 IC |
| 新增输出 | `check/0529/piotroski_industry_ic.csv` | 行业分层 IC |

### 数据依赖

- `reports/factor_evaluation/ic_history_research_all.parquet`
- `data/processed/factor_panels/piotroski_f.parquet`
- `data/processed/fwd_ret_panel.parquet`
- `data/processed/indicator_pit.parquet`
- `data/processed/financial_pit.parquet`
- `data/processed/industry.parquet`

Piotroski 分项必须与正式因子实现一致：

| 分项 | 来源 | 现有正式逻辑 |
|---|---|---|
| F1 | `indicator_pit.roa > 0` | `financial_factors.factor_piotroski_f()` |
| F2 | `TTM CFO > 0` | `make_ttm(financial_pit, n_cashflow_act)` |
| F3 | `delta_roa > 0` | `get_field_yoy_delta(indicator_pit, roa)` |
| F4 | `TTM CFO > TTM NI` | `make_ttm(financial_pit, n_income)` |
| F5 | `delta_debt_to_assets < 0` | `get_field_yoy_delta(indicator_pit, debt_to_assets)` |
| F8 | `delta_gross_margin > 0` | `get_field_yoy_delta(indicator_pit, grossprofit_margin)` |
| F9 | `delta_assets_turn > 0` | `get_field_yoy_delta(indicator_pit, assets_turn)` |

不要重新发明不同口径。

### 诊断步骤

1. 分期 IC_IR：`2012-2015`、`2016-2018`、`2019-2020`、`2021-2022`。
2. 行业分层 IC：重点看 2021-2022，行业分类使用 `industry.parquet` 的 SW2021 一级行业。
3. 分项 IC：分别计算 F1/F2/F3/F4/F5/F8/F9 与 forward return 的 Rank IC。

### 运行命令

```powershell
python -m scripts.diagnose_piotroski --ic-history reports/factor_evaluation/ic_history_research_all.parquet --output-dir check/0529
```

验收标准：

- 报告中只能给出以下之一：`KEEP_MONITOR`、`REMOVE_CANDIDATE`、`REBUILD_COMPONENTS`、`REVERSE_USE_CANDIDATE`。
- 如果建议调池，必须写明“需要另起训练/验证实验验证，不能直接改主线因子池”。
- 若分项数据不足，报告中明确降级路径和缺失字段。

---

## 阶段四：Chow 结构突变检验（P2）

### 目标

对健康状态为 `WARN` 或 `REVERSE` 的因子做结构断点扫描，定位失效时间点。该结果只做诊断，不作为自动剔除条件。

### 改动文件

| 动作 | 文件 | 说明 |
|---|---|---|
| 修改 | `src/evaluation/factor_health.py` | 新增 `scan_structural_break()` |
| 修改 | `scripts/run_factor_health.py` | 增加 `--chow` 参数 |
| 新增输出 | `reports/factor_health/chow_result.csv` | 断点扫描结果 |

### 统计要求

- 候选断点建议按季度扫描。
- 断点前后各至少 12 个有效 IC 观测。
- 输出原始 `p_value` 和 BH 校正后的 `p_value_bh`。
- 只有 `p_value_bh < 0.05` 才能写作“显著结构突变”；否则写“未发现显著断点”。

### 运行命令

隔离验证：

```powershell
python -m scripts.run_factor_health --ic-history reports/factor_evaluation_health_probe/ic_history_final_research.parquet --factor-summary reports/factor_evaluation_health_probe/factor_summary.csv --output-dir reports/factor_health_probe --chow
```

主目录：

```powershell
python -m scripts.run_factor_health --chow
```

验收标准：

- `chow_result.csv` 至少包含 `factor`、`breakpoint`、`F_stat`、`p_value`、`p_value_bh`、`ic_ir_before`、`ic_ir_after`、`sign_change`。
- `health_report.md` 中引用 Chow 结论时必须注明“多重检验校正后”。

---

## 阶段五：Fama-MacBeth 增强检验（P3）

### 目标

为单因子检验增加 Fama-MacBeth 单因子截面回归和 Newey-West 标准误，提升统计严谨性。该阶段独立，不阻塞阶段二到四。

### 改动文件

| 动作 | 文件 | 说明 |
|---|---|---|
| 修改 | `src/evaluation/ic_analysis.py` | 新增 `fama_macbeth_single()` 和批量封装 |
| 修改 | `scripts/run_factor_evaluation.py` | 增加 `--fama-macbeth` 参数 |
| 修改 | `tests/test_evaluation.py` | 增加 FM 合成数据测试 |

### 统计口径

- 默认只用训练期 `train_panels + train_fwd`。
- Newey-West lag 使用 `floor(T ** (1/3))`，也允许 CLI 覆盖。
- 输出方向调整字段：`factor_direction`、`directional_mean_lambda`、`directional_positive_ratio`。
- 单因子 FM 不控制其他因子共线性，报告中必须注明局限。

### 输出文件

`reports/factor_evaluation/fama_macbeth.csv`，至少包含：

```text
factor, n_periods, mean_lambda, nw_t_stat, positive_ratio,
factor_direction, directional_mean_lambda, directional_positive_ratio,
significant_nw
```

### 验证命令

```powershell
python -m pytest tests\test_evaluation.py -q
```

```powershell
python -m scripts.run_factor_evaluation --output-dir reports/factor_evaluation_fm_probe --no-plots --fama-macbeth
```

```powershell
python -c "import pandas as pd; fm=pd.read_csv(r'reports/factor_evaluation_fm_probe/fama_macbeth.csv'); print(fm.shape); print(fm.head().to_string(index=False))"
```

验收标准：

- `fama_macbeth.csv` 行数与训练期已评价因子数一致。
- `nw_t_stat` 存在且不是全 NaN。
- 报告不使用验证期 FM 结果做筛选。

---

## 推荐执行顺序

```text
预检
  ↓
阶段一：IC 历史矩阵持久化
  ↓
阶段二：健康监控器
  ├─ 阶段三：piotroski_f 专项诊断
  └─ 阶段四：Chow 结构突变检验

阶段五：Fama-MacBeth 可在阶段一后任意时间插入
```

---

## 一键执行清单（按顺序复制运行）

完成对应代码改动后，按下面顺序跑：

```powershell
python -m pytest tests\test_evaluation.py -q
```

```powershell
python -m scripts.run_factor_evaluation --output-dir reports/factor_evaluation_health_probe --no-plots
```

```powershell
python -c "import pandas as pd; base=r'reports/factor_evaluation_health_probe'; files=['ic_history_train_all.parquet','ic_history_valid_all.parquet','ic_history_research_all.parquet','ic_history_final_research.parquet']; [print(f, pd.read_parquet(base+'/'+f).shape) for f in files]"
```

```powershell
python -m pytest tests\test_factor_health.py -q
```

```powershell
python -m scripts.run_factor_health --ic-history reports/factor_evaluation_health_probe/ic_history_final_research.parquet --factor-summary reports/factor_evaluation_health_probe/factor_summary.csv --output-dir reports/factor_health_probe
```

如果以上都通过，再刷新主目录：

```powershell
python -m scripts.run_factor_evaluation --output-dir reports/factor_evaluation --no-plots
```

```powershell
python -m scripts.run_factor_health
```

可选诊断：

```powershell
python -m scripts.diagnose_piotroski --ic-history reports/factor_evaluation/ic_history_research_all.parquet --output-dir check/0529
```

```powershell
python -m scripts.run_factor_health --chow
```

```powershell
python -m scripts.run_factor_evaluation --output-dir reports/factor_evaluation_fm_probe --no-plots --fama-macbeth
```

---

## 失败处理

| 失败点 | 处理 |
|---|---|
| `ic_history_*` 文件不存在 | 阶段一代码未接入 `run_factor_evaluation.py`，先检查导入和落盘位置 |
| `ic_history_final_research` 行数明显少于训练+验证期 | 检查是否误用 `train_fwd`，应由 train/valid history concat 得到 |
| 健康脚本报缺失方向 | 检查 `factor_summary.csv` 是否包含对应因子和 `factor_direction` |
| 图表生成失败 | 先加 `--no-plot` 验证 CSV/Markdown，绘图问题单独修 |
| Chow 全部无结果 | 样本不足或断点前后不足 12 期，报告写“样本不足”，不要硬判定 |
| FM t 值异常大 | 先检查 Newey-West 标准误、收益尺度、是否把验证期混入训练期 |

---

## 自检报告要求

完成阶段一或之后任一涉及因子诊断的阶段后，最终汇报必须包含：

- **A. 高频犯错点**：T 日因子只用 <=T 数据、forward return 按 T+1 开盘、训练/验证按 `exit_date` 切分、未触碰测试集、验证期未回流筛选。
- **B. 工程质量**：新增函数 docstring、单元测试、输出 metadata、异常路径。
- **C. 统计严谨性**：IC_IR/t/p 值或 FM Newey-West t 值、Chow 多重检验校正、样本期说明。
- **D. 待优化事项**：未处理的诊断脚本、样本不足、需要另起实验验证的调池建议。

---

## 本版修正摘要

相对 v1.0，本版修正：

1. 不再假设 `ic_series_map` 可在脚本层直接读取。
2. 因子池口径从“17 个”改为“以当前 `final_factors.json` 和 `ic_result.csv` 为准”。
3. 明确区分训练期 IC、验证期 IC、训练+验证研究诊断 IC。
4. 所有开发命令先写 probe 目录，避免覆盖主线报告。
5. 健康状态按 `factor_direction` 做方向调整，负向因子不会被误判。
6. Chow 检验加入 BH 多重检验校正要求。
7. Piotroski 分项诊断要求复用正式因子口径，避免诊断和生产逻辑不一致。
