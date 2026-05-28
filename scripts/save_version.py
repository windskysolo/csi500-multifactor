"""
归档当前 pipeline 运行结果到 experiments/vX.X/。

用法：
    python -m scripts.save_version --version 1.0 --notes "基线：训练期2016-2021，10个核心因子"
    python -m scripts.save_version --version 1.1 --notes "训练期扩展到2012，新增15个备选因子"

归档内容：
    experiments/vX.X/
    ├── NOTES.md            — 版本说明（含你传入的 --notes）
    ├── config_snapshot.py  — 当时 src/config.py 的冻结副本
    ├── manifest.json       — 日期、git commit、数据区间等元信息
    └── reports/            — reports/ 下所有 CSV / JSON / MD 文件
"""

import argparse
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
EXPERIMENTS_DIR = ROOT / "experiments"
REPORTS_DIR = ROOT / "reports"
CONFIG_SRC = ROOT / "src" / "config.py"
VERSIONS_MD = EXPERIMENTS_DIR / "VERSIONS.md"

REPORT_SUFFIXES = {".csv", ".json", ".md"}


def _git_info() -> dict:
    def run(cmd):
        try:
            return subprocess.check_output(cmd, cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            return "unknown"

    return {
        "commit": run(["git", "rev-parse", "--short", "HEAD"]),
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "commit_message": run(["git", "log", "-1", "--format=%s"]),
    }


def _collect_report_files() -> list[Path]:
    return [p for p in REPORTS_DIR.rglob("*") if p.is_file() and p.suffix in REPORT_SUFFIXES]


def _extract_config_summary() -> dict:
    """从 config.py 提取关键参数，不 import 避免副作用。"""
    summary = {}
    try:
        text = CONFIG_SRC.read_text(encoding="utf-8")
        for key in ("TRAIN_START", "TRAIN_END", "VALID_START", "VALID_END",
                    "TEST_START", "TEST_END", "MARKET_START"):
            for line in text.splitlines():
                if line.strip().startswith(key) and "Timestamp" in line:
                    date_str = line.split('"')[1] if '"' in line else line.split("'")[1]
                    summary[key] = date_str
                    break
    except Exception:
        pass
    return summary


def _update_versions_md(version: str, notes: str, date_str: str, git_commit: str, config_summary: dict) -> None:
    text = VERSIONS_MD.read_text(encoding="utf-8")
    train_period = f"{config_summary.get('TRAIN_START','?')} ~ {config_summary.get('TRAIN_END','?')}"
    new_row = f"| v{version} | {date_str} | `{git_commit}` | {train_period} | {notes} |"

    marker = "| 版本 | 日期 | Git Commit | 训练期 | 说明 |"
    separator = "|------|------|-----------|--------|------|"
    if separator in text:
        text = text.replace(separator, separator + "\n" + new_row)
    else:
        text += "\n" + new_row

    VERSIONS_MD.write_text(text, encoding="utf-8")


def save_version(version: str, notes: str, skip_config: bool = False) -> None:
    version_dir = EXPERIMENTS_DIR / f"v{version}"
    if version_dir.exists():
        raise FileExistsError(
            f"版本 v{version} 已存在：{version_dir}\n"
            "如需覆盖，请手动删除该目录后重试。"
        )

    version_dir.mkdir(parents=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    git = _git_info()
    config_summary = _extract_config_summary()

    # 1. config 快照
    if not skip_config:
        shutil.copy2(CONFIG_SRC, version_dir / "config_snapshot.py")

    # 2. reports 关键文件（保留子目录结构）
    report_files = _collect_report_files()
    for src in report_files:
        rel = src.relative_to(REPORTS_DIR)
        dst = version_dir / "reports" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # 3. manifest.json
    manifest = {
        "version": version,
        "date": date_str,
        "git": git,
        "config": config_summary,
        "notes": notes,
        "files_saved": [str(f.relative_to(REPORTS_DIR)) for f in report_files],
    }
    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 4. NOTES.md（给用户手动补充细节的模板）
    notes_content = f"""# v{version} — {notes}

> 归档日期：{date_str}
> Git Commit：`{git['commit']}` ({git['branch']})

## 本版变更

{notes}

## 数据区间

| 阶段 | 起止 |
|------|------|
| 训练 | {config_summary.get('TRAIN_START', '?')} ~ {config_summary.get('TRAIN_END', '?')} |
| 验证 | {config_summary.get('VALID_START', '?')} ~ {config_summary.get('VALID_END', '?')} |
| 测试 | {config_summary.get('TEST_START', '?')} ~ {config_summary.get('TEST_END', '?')} |

## 关键指标（手动填写）

| 指标 | 训练期 | 验证期 |
|------|--------|--------|
| IC 均值 | | |
| IC_IR | | |
| 年化超额收益 | | |
| 最大超额回撤 | | |
| 年化双边换手 | | |

## 待改进

- （下一版计划改什么）
"""
    (version_dir / "NOTES.md").write_text(notes_content, encoding="utf-8")

    # 5. 更新 VERSIONS.md 索引
    _update_versions_md(version, notes, date_str, git["commit"], config_summary)

    config_note = "（已跳过）" if skip_config else "config_snapshot.py"
    print(f"已归档 v{version} → {version_dir}")
    print(f"  config 快照：{config_note}")
    print(f"  reports 文件：{len(report_files)} 个")
    print(f"  git commit：{git['commit']} ({git['branch']})")
    print(f"\n请在 {version_dir / 'NOTES.md'} 中补充关键指标。")


def main() -> None:
    parser = argparse.ArgumentParser(description="归档当前 pipeline 结果到 experiments/vX.X/")
    parser.add_argument("--version", required=True, help="版本号，如 1.0 或 1.1")
    parser.add_argument("--notes", required=True, help="本版变更说明（一句话）")
    parser.add_argument("--skip-config", action="store_true", help="不保存 config 快照（config 正在变更时使用）")
    args = parser.parse_args()
    save_version(args.version, args.notes, skip_config=args.skip_config)


if __name__ == "__main__":
    main()
