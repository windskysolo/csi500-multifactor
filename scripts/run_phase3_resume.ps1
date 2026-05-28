$ROOT = "e:\Acoding\Project\500"
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
    if ($ec -ne 0) { Log "ABORT: $label failed"; exit $ec }
}

Set-Location $ROOT
$env:TINYSHARE_MAX_WORKERS = "4"
$env:TINYSHARE_CALL_INTERVAL = "0.40"

Log "=== 续跑：跳过 margin，从 moneyflow 开始 ==="
RunStep "download_supplement moneyflow" "python -m scripts.download_supplement moneyflow"
RunStep "download_supplement holder"    "python -m scripts.download_supplement holder"
RunStep "download_supplement dividend"  "python -m scripts.download_supplement dividend"
RunStep "download_alternative_data all" "python -m scripts.download_alternative_data all"
Log "=== Phase 3 fast download completed ==="
