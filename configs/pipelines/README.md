# configs/pipelines/

每个 Python 文件定义一个 `ExperimentSpec`，描述一次可审查、可复现的实验设计。这里存放“实验意图”，不存放计算逻辑或运行结果。

## 当前入口

- 当前注册主线：`rolling48_topn150_ew_hk_quarterly.py`
- 当前主线 run：以 `registry/mainline.json` 的 `active_run_id` 为准
- 冻结对照：`frozen_baseline_icir_topn50_ew.py`

文件名中的 `baseline` 或 `challenger` 只是实验设计分类，不能替代 registry 的当前状态。

## 命名分类

| 命名 | 含义 | 使用规则 |
|---|---|---|
| `frozen_*` | 冻结对照锚 | 不原地修改；变更时新建 Spec |
| `baseline_*` | 历史或候选基线 | 必须通过 run/registry 判断是否仍有效 |
| `challenger_*` | 单变量挑战实验 | 与主线比较后才可晋升 |
| `ablation_*` | 消融实验 | 只移除或替换一个明确变量 |
| `rolling48_topn*` | TopN 数量网格 | 属于参数研究，不因文件存在而成为主线 |
| `icir_te*`、`*_qp`、`*_prefilter_*` | QP/约束实验 | 与 TopN 主线分开解释，不混用指标口径 |
| 其他无前缀 Spec | 已完成的定向研究 | 新增前先确认是否可归入上述分类 |

## 变更规则

- Spec 内只声明参数，禁止写数据读取、训练或回测逻辑。
- 已产生正式 run 的 Spec 视为不可变；参数变化应新建文件和新的 `experiment_id`。
- 一次实验原则上只改变一个研究变量，多变量变更必须在描述中显式说明。
- 测试期不得写入普通 Spec；测试集只能经受控入口运行。
- 新 Spec 应在描述中写清基准 Spec、变化项、预期验证问题和 period scope。
