"""测试集运行前最终全面检查。"""
import json
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
from src import config as cfg

issues   = []
warnings = []
passes   = []

def ok(msg):  passes.append(f"[PASS] {msg}")
def warn(msg): warnings.append(f"[WARN] {msg}")
def fail(msg): issues.append(f"[FAIL] {msg}")

# ── 1. mainline.json ─────────────────────────────────────────────────────────
mainline = json.loads((ROOT / "registry" / "mainline.json").read_text())
mid = mainline["active_run_id"]
print(f"mainline run_id: {mid}")
if "hk_quarterly" in mid:
    ok(f"mainline 指向 hk_quarterly 版本: {mid}")
else:
    fail(f"mainline 未指向 hk_quarterly 版本: {mid}")
if not mainline.get("test_set_used"):
    ok("mainline.test_set_used = False")
else:
    fail("mainline.test_set_used = True，测试集已用过")

# ── 2. test_set_runs.json 有效次数 ────────────────────────────────────────────
ledger = json.loads((ROOT / "docs" / "logs" / "test_set_runs.json").read_text(encoding="utf-8"))
active = ledger.get("active_runs", [])
print(f"active_runs: {active}")
if len(active) == 0:
    ok(f"test_set_runs.json 有效次数=0，剩余3次")
else:
    fail(f"test_set_runs.json 有效次数={len(active)}，非0")

# ── 3. factor_panels_test_run_1 目录检查（关键！）────────────────────────────
test_panel_dir = cfg.DATA_PROC / "factor_panels_test_run_1"
default_panel_dir = cfg.DATA_PROC / "factor_panels"
print(f"\n测试集专用面板目录: {test_panel_dir}")
print(f"  exists: {test_panel_dir.exists()}")

if test_panel_dir.exists():
    warn("factor_panels_test_run_1/ 存在，run_test_pipeline 将自动使用此目录（非默认面板）")

    # 检查其中的 hk_hold 面板是否覆盖测试期
    for factor in ["hk_hold_ratio", "hk_hold_chg"]:
        fp = test_panel_dir / f"{factor}.parquet"
        if fp.exists():
            df = pd.read_parquet(fp)
            last_date = df.index.max()
            print(f"  {factor}: shape={df.shape}, last_date={last_date.date()}")

            # 检查测试期（2024-08 以后）是否有空月
            test_rows = df.loc["2023":]
            empty_months = test_rows.isna().all(axis=1).sum()
            total_test = len(test_rows)
            print(f"    test期全空月: {empty_months}/{total_test}")

            if last_date < pd.Timestamp("2025-01-01"):
                fail(f"{factor} 在 test_panel_dir 中最后日期={last_date.date()}，未覆盖测试期")
            elif empty_months > 0:
                fail(f"{factor} 在 test_panel_dir 中测试期有 {empty_months} 个全空月（旧日频定义残留！）")
            else:
                ok(f"{factor} 在 test_panel_dir 中测试期无全空月")
        else:
            fail(f"{factor} 在 test_panel_dir 中不存在")

    # 检查其他选用因子面板是否覆盖测试期（只检查 final_factors.json 中的选用因子）
    eval_path = ROOT / "reports" / "factor_evaluation" / "final_factors.json"
    if eval_path.exists():
        selected_set = set(json.loads(eval_path.read_text(encoding="utf-8"))["final_factors"])
        selected_set -= {"hk_hold_ratio", "hk_hold_chg"}  # 已单独检查
        too_early = []
        for fname in selected_set:
            fp = test_panel_dir / f"{fname}.parquet"
            if not fp.exists():
                too_early.append((fname, None))
                continue
            last_d = pd.read_parquet(fp, columns=[]).index.max()
            if last_d < pd.Timestamp("2024-01-01"):
                too_early.append((fname, last_d))
        if too_early:
            fail(f"选用因子中 {len(too_early)} 个未覆盖2024+: {[n for n,_ in too_early]}")
        else:
            ok(f"其余 {len(selected_set)} 个选用因子面板均覆盖至2025-12-31")
else:
    warn("factor_panels_test_run_1/ 不存在，将使用默认 factor_panels/（仅2012-2022）")
    last_default = pd.read_parquet(default_panel_dir / "hk_hold_ratio.parquet", columns=[]).index.max()
    if last_default < pd.Timestamp("2023-01-01"):
        fail(f"默认面板最后日期={last_default.date()}，F9-008 检查会拒绝运行（距TEST_END>2个月）")

# ── 4. final_factors.json 与训练实验因子集合一致性 ────────────────────────────
eval_path = ROOT / "reports" / "factor_evaluation" / "final_factors.json"
train_coef = ROOT / "runs" / "train_valid" / mid / "signal" / "coef_history.parquet"

print(f"\nfinal_factors.json: {eval_path.exists()}")
if eval_path.exists() and train_coef.exists():
    final_factors = set(json.loads(eval_path.read_text())["final_factors"])
    coef_factors  = set(pd.read_parquet(train_coef).columns.tolist())

    only_in_eval  = final_factors - coef_factors
    only_in_coef  = coef_factors - final_factors

    print(f"  final_factors.json: {len(final_factors)} 个")
    print(f"  coef_history 列: {len(coef_factors)} 个")

    if not only_in_eval and not only_in_coef:
        ok("final_factors.json 与训练 run 因子集合完全一致")
    else:
        if only_in_eval:
            fail(f"final_factors.json 有但 coef_history 没有: {only_in_eval}")
        if only_in_coef:
            warn(f"coef_history 有但 final_factors.json 没有: {only_in_coef}（测试集不会使用）")
else:
    warn("无法比较（文件缺失）")

# ── 5. 测试集日期配置 ─────────────────────────────────────────────────────────
print(f"\ncfg.TEST_START = {cfg.TEST_START.date()}")
print(f"cfg.TEST_END   = {cfg.TEST_END.date()}")
if cfg.TEST_START >= pd.Timestamp("2023-01-01") and cfg.TEST_END <= pd.Timestamp("2026-01-01"):
    ok(f"TEST_START/END 范围合理: {cfg.TEST_START.date()} ~ {cfg.TEST_END.date()}")
else:
    fail(f"TEST_START/END 异常: {cfg.TEST_START.date()} ~ {cfg.TEST_END.date()}")

# ── 6. mainline run_config.json 中的优化器参数 ────────────────────────────────
run_config_path = ROOT / "runs" / "train_valid" / mid / "run_config.json"
if run_config_path.exists():
    rc = json.loads(run_config_path.read_text(encoding="utf-8"))
    spec = rc.get("spec", {})
    opt  = spec.get("optimizer", {})
    sig  = spec.get("signal", {})
    print(f"\nmainline run_config: topn={opt.get('topn')}, mode={opt.get('optimizer_mode')}")
    print(f"  signal: method={sig.get('method')}, training_mode={sig.get('training_mode')}, window={sig.get('window_months')}")

    if opt.get("optimizer_mode") == "topn_ew" and opt.get("topn") == 150:
        ok("优化器参数正确: topn_ew, topn=150")
    else:
        warn(f"优化器参数: topn={opt.get('topn')}, mode={opt.get('optimizer_mode')}")

    if sig.get("method") == "ridge" and sig.get("training_mode") == "rolling" and sig.get("window_months") == 48:
        ok("信号参数正确: ridge, rolling, window=48")
    else:
        fail(f"信号参数异常: method={sig.get('method')}, mode={sig.get('training_mode')}, window={sig.get('window_months')}")
else:
    fail(f"mainline run_config.json 不存在: {run_config_path}")

# ── 7. 公共 fwd_ret_panel 覆盖检查 ────────────────────────────────────────────
fwd_path = cfg.DATA_PROC / "fwd_ret_panel.parquet"
if fwd_path.exists():
    fwd = pd.read_parquet(fwd_path, columns=[])
    print(f"\nfwd_ret_panel: {fwd.shape[0]} 期, last={fwd.index.max().date()}")
    if fwd.index.max() >= pd.Timestamp("2022-11-01"):
        ok(f"fwd_ret_panel 覆盖到 {fwd.index.max().date()}（测试流程会自动扩展）")
    else:
        warn(f"fwd_ret_panel 末日期={fwd.index.max().date()}，较早")
else:
    fail("fwd_ret_panel.parquet 不存在")

# ── 结论 ─────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print(f"PASS: {len(passes)}  WARN: {len(warnings)}  FAIL: {len(issues)}")
print("="*60)
for m in passes:   print(m)
for m in warnings: print(m)
for m in issues:   print(m)

if issues:
    print(f"\n[X] 发现 {len(issues)} 个 FAIL，测试集运行前必须修复。")
    sys.exit(1)
else:
    print("\n[OK] 无 FAIL，可以继续（注意处理 WARN）。")
