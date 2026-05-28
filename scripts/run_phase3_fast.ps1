# Phase 3 fast download driver
# Usage:
#   .\scripts\run_phase3_fast.ps1
#   .\scripts\run_phase3_fast.ps1 -StartAt financial
#
# This driver resumes Phase 3 with higher-throughput settings while keeping one
# account-level rate limiter per Python process. It intentionally runs modules
# sequentially to avoid multiple processes competing for the same API quota.

param(
    [ValidateSet("adjfactor", "dailybasic", "suspend", "limitlist", "financial", "supplement", "alt")]
    [string]$StartAt = "adjfactor",

    [string]$Token = "",

    [int]$MarketWorkers = 8,
    [int]$FinancialWorkers = 4,
    [double]$TushareInterval = 0.20,

    [int]$OtherWorkers = 8,
    [double]$OtherInterval = 0.10
)

$ROOT = Split-Path -Parent $PSScriptRoot
$LOG  = "$ROOT\logs\phase3_fast_download.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Add-Content -Path $LOG -Value $line
    Write-Output $line
}

function RunStep($label, $cmd) {
    Log "--- $label ---"
    $t0 = Get-Date
    Invoke-Expression $cmd 2>&1 | Tee-Object -Append -FilePath $LOG
    $ec = $LASTEXITCODE
    $mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
    Log "$label finished, exit_code=$ec, minutes=$mins"
    if ($ec -ne 0) {
        Log "ABORT: $label failed"
        exit $ec
    }
}

Set-Location $ROOT

if ($Token) {
    $env:TINYSHARE_TOKEN = $Token
}
if (-not $env:TINYSHARE_TOKEN) {
    $env:TINYSHARE_TOKEN = [Environment]::GetEnvironmentVariable("TINYSHARE_TOKEN", "User")
}
if (-not $env:TINYSHARE_TOKEN) {
    Log "ERROR: TINYSHARE_TOKEN is not set"
    exit 1
}

$env:TINYSHARE_MARKET_WORKERS = [string]$MarketWorkers
$env:TINYSHARE_FINANCIAL_WORKERS = [string]$FinancialWorkers
$env:TINYSHARE_TUSHARE_CALL_INTERVAL = [string]$TushareInterval
$env:TINYSHARE_MAX_WORKERS = [string]$OtherWorkers
$env:TINYSHARE_CALL_INTERVAL = [string]$OtherInterval

Log "=== Phase 3 fast download started (StartAt=$StartAt) ==="
Log "settings: MARKET_WORKERS=$MarketWorkers FINANCIAL_WORKERS=$FinancialWorkers TUSHARE_INTERVAL=$TushareInterval OTHER_WORKERS=$OtherWorkers OTHER_INTERVAL=$OtherInterval"

$tushareModules = @("adjfactor", "dailybasic", "suspend", "limitlist", "financial")
$startIndex = [Array]::IndexOf($tushareModules, $StartAt)
if ($startIndex -ge 0) {
    for ($i = $startIndex; $i -lt $tushareModules.Count; $i++) {
        $m = $tushareModules[$i]
        RunStep "download_tushare $m" "python -m scripts.download_tushare $m"
    }
    RunStep "download_supplement all" "python -m scripts.download_supplement all"
    RunStep "download_alternative_data all" "python -m scripts.download_alternative_data all"
} elseif ($StartAt -eq "supplement") {
    RunStep "download_supplement all" "python -m scripts.download_supplement all"
    RunStep "download_alternative_data all" "python -m scripts.download_alternative_data all"
} elseif ($StartAt -eq "alt") {
    RunStep "download_alternative_data all" "python -m scripts.download_alternative_data all"
}

Log "=== Phase 3 fast download completed ==="
