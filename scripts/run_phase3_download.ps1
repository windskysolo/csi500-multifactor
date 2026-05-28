# Phase 3 全量下载 — 串行执行三个脚本
# 用法: .\scripts\run_phase3_download.ps1
# 日志: logs/phase3_download_driver.log

param(
    [string]$StartFrom = "tushare",   # tushare | supplement | alt
    [string]$Token = ""               # 若不传则从环境变量读
)

$ROOT = Split-Path -Parent $PSScriptRoot
$LOG  = "$ROOT\logs\phase3_download_driver.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Add-Content -Path $LOG -Value $line
    Write-Output $line
}

Set-Location $ROOT

# 优先用参数传入的 token，否则从环境变量读
if ($Token) { $env:TINYSHARE_TOKEN = $Token }
if (-not $env:TINYSHARE_TOKEN) {
    Log "ERROR: TINYSHARE_TOKEN 未设置，请先执行: `$env:TINYSHARE_TOKEN='<token>'"
    exit 1
}

Log "=== Phase 3 下载启动 (StartFrom=$StartFrom) ==="

# ──────────────────────────────────────────────
# 3.1  现有数据向前补历史
# ──────────────────────────────────────────────
if ($StartFrom -eq "tushare") {
    Log "--- 3.1a download_tushare all ---"
    $t0 = Get-Date
    python -m scripts.download_tushare all 2>&1 | Tee-Object -Append -FilePath $LOG
    $ec = $LASTEXITCODE
    Log "download_tushare all 结束，退出码=$ec，耗时 $([math]::Round(((Get-Date)-$t0).TotalMinutes,1)) 分钟"
    if ($ec -ne 0) { Log "ABORT: download_tushare 失败"; exit 1 }

}

if ($StartFrom -in @("tushare","supplement")) {
    Log "--- 3.1b download_supplement all ---"
    $t0 = Get-Date
    python -m scripts.download_supplement all 2>&1 | Tee-Object -Append -FilePath $LOG
    $ec = $LASTEXITCODE
    Log "download_supplement all 结束，退出码=$ec，耗时 $([math]::Round(((Get-Date)-$t0).TotalMinutes,1)) 分钟"
    if ($ec -ne 0) { Log "ABORT: download_supplement 失败"; exit 1 }
}

# ──────────────────────────────────────────────
# 3.2  新增备选数据（按优先级分批）
# ──────────────────────────────────────────────
if ($StartFrom -in @("tushare","supplement","alt")) {
    Log "--- 3.2 download_alternative_data all ---"
    $t0 = Get-Date
    python -m scripts.download_alternative_data all 2>&1 | Tee-Object -Append -FilePath $LOG
    $ec = $LASTEXITCODE
    Log "download_alternative_data all 结束，退出码=$ec，耗时 $([math]::Round(((Get-Date)-$t0).TotalMinutes,1)) 分钟"
    if ($ec -ne 0) { Log "ABORT: download_alternative_data 失败"; exit 1 }
}

Log "=== Phase 3 全量下载完成 ==="
