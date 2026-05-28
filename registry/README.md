# registry/

当前主线与候选线的指针文件。可入库，是唯一的主线替换入口。

```
registry/
├── mainline.json              # 当前激活的主线 run_id
├── challengers.json           # 当前候选线列表
└── archived_promotions.jsonl  # 历史 promote 记录（append-only）
```

## 替换主线

```bash
python -m scripts.promote_run \
  --run-id 20260527_143000__baseline_expanding_ridge_te6_lam0050 \
  --slot mainline \
  --reason "clean baseline after artifact drift audit"
```

`promote_run.py` 会自动检查：
1. `RUN_FINISHED.json` 存在
2. `reports/self_check.md` 存在
3. 所有必备 artifact 在位
4. 非测试集 run（除非 `--allow-test-set`）

## 禁止事项

- 不手动编辑 `mainline.json` 绕过 promote 检查
- 不把 `runs/` 中的 parquet 复制回 `data/processed/` 作为主线
