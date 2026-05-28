# configs/

人工编写的实验定义。可入库，可 code review，不运行任何计算。

```
configs/
├── signals/         # 单独信号方法定义（可被多个 pipeline 复用）
├── optimizers/      # 单独优化器参数定义
└── pipelines/       # 完整 pipeline spec（ExperimentSpec）
```

## 使用方式

```python
from src.pipeline.contracts import ExperimentSpec

spec = ExperimentSpec.from_config_file("configs/pipelines/baseline_expanding_ridge_te6_lam0050.py")
```

## 命名约定

| 前缀 | 含义 |
|---|---|
| `baseline_` | 当前主线或主对照基线 |
| `challenger_` | 候选实验线，需与主线对比才能晋升 |

## 禁止事项

- 不在 spec 文件内写计算逻辑
- 不把测试集期间写入 `period_scope` 除非显式 `allow_test_set=True`
- 不在看到结果后补注册候选线（应预注册）
