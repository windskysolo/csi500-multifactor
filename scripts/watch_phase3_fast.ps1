# Live monitor for Phase 3 fast download.
# This script only reads logs/raw files. It does not call TinyShare or mutate data.

param(
    [int]$RefreshSeconds = 5
)

$ROOT = Split-Path -Parent $PSScriptRoot
$LogPath = Join-Path $ROOT "logs\phase3_fast_download.log"
$WeightPath = Join-Path $ROOT "data\csi500_index_weight_201201_202512.csv"

function Get-StockCount {
    if (-not (Test-Path $WeightPath)) { return 0 }
    try {
        return @((Import-Csv $WeightPath | Select-Object -ExpandProperty con_code -Unique)).Count
    } catch {
        return 0
    }
}

function Get-LastStep {
    if (-not (Test-Path $LogPath)) { return $null }
    $lines = Get-Content -Path $LogPath -ErrorAction SilentlyContinue
    $stepLines = $lines | Where-Object { $_ -match '^\[(?<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] --- (?<step>.+) ---$' }
    if (-not $stepLines) { return $null }
    $last = $stepLines[-1]
    $null = $last -match '^\[(?<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] --- (?<step>.+) ---$'
    return [pscustomobject]@{
        Time = [datetime]::ParseExact($Matches.ts, 'yyyy-MM-dd HH:mm:ss', $null)
        Step = $Matches.step
    }
}

function Count-Files($relativeDir, [datetime]$since) {
    $dir = Join-Path $ROOT $relativeDir
    if (-not (Test-Path $dir)) {
        return [pscustomobject]@{ Total = 0; Updated = 0; Latest = $null }
    }
    $files = @(Get-ChildItem $dir -Filter *.csv -ErrorAction SilentlyContinue)
    $updated = @($files | Where-Object { $_.LastWriteTime -ge $since })
    $latest = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    return [pscustomobject]@{
        Total = $files.Count
        Updated = $updated.Count
        Latest = $latest
    }
}

function Show-ProgressLine($name, $relativeDir, [datetime]$since, [int]$expected) {
    $c = Count-Files $relativeDir $since
    $done = if ($expected -gt 0) { [math]::Min($c.Updated, $expected) } else { $c.Updated }
    $pct = if ($expected -gt 0) { [math]::Round($done * 100.0 / $expected, 1) } else { 0 }
    $latest = if ($c.Latest) { "$($c.Latest.Name) @ $($c.Latest.LastWriteTime.ToString('HH:mm:ss'))" } else { "-" }
    Write-Host ("{0,-18} updated {1,5}/{2,-5} ({3,5}%) total_files={4,5} latest={5}" -f $name, $done, $expected, $pct, $c.Total, $latest)
    if ($expected -gt 0) {
        Write-Progress -Activity "Phase 3 fast download" -Status "$name $done/$expected" -PercentComplete $pct
    }
}

function Show-RecentWarnings {
    if (-not (Test-Path $LogPath)) { return }
    $recent = Get-Content $LogPath -Tail 300 -ErrorAction SilentlyContinue
    $bad = @($recent | Where-Object { $_ -match '限流|频率|rate|ERROR|失败|ABORT|failed' } | Select-Object -Last 8)
    if ($bad.Count -gt 0) {
        Write-Host ""
        Write-Host "Recent warnings/errors:" -ForegroundColor Yellow
        $bad | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    }
}

$stockCount = Get-StockCount
if ($stockCount -le 0) { $stockCount = 1539 }
$tradingDaysApprox = 3644

while ($true) {
    Clear-Host
    $now = Get-Date
    Write-Host "Phase 3 Fast Download Monitor" -ForegroundColor Cyan
    Write-Host "Time: $($now.ToString('yyyy-MM-dd HH:mm:ss'))    Refresh: ${RefreshSeconds}s"
    Write-Host "Log : $LogPath"
    Write-Host ""

    if (-not (Test-Path $LogPath)) {
        Write-Host "Waiting for log file..."
        Start-Sleep -Seconds $RefreshSeconds
        continue
    }

    $completed = Select-String -Path $LogPath -Pattern 'Phase 3 fast download completed' -SimpleMatch -Quiet
    $step = Get-LastStep
    if (-not $step) {
        Write-Host "No step found yet."
        Start-Sleep -Seconds $RefreshSeconds
        continue
    }

    $elapsed = [math]::Round((New-TimeSpan -Start $step.Time -End $now).TotalMinutes, 1)
    Write-Host "Current step: $($step.Step)"
    Write-Host "Step start  : $($step.Time.ToString('yyyy-MM-dd HH:mm:ss'))  elapsed=${elapsed}m"
    Write-Host ""

    switch -Regex ($step.Step) {
        'adjfactor' {
            Show-ProgressLine "adj_factor" "data\raw\adj_factor" $step.Time $stockCount
        }
        'dailybasic' {
            Show-ProgressLine "daily_basic" "data\raw\daily_basic" $step.Time $stockCount
        }
        'suspend' {
            Show-ProgressLine "suspend" "data\raw\suspend" $step.Time $stockCount
        }
        'limitlist' {
            Show-ProgressLine "limit_list" "data\raw\limit_list" $step.Time $tradingDaysApprox
        }
        'financial' {
            Show-ProgressLine "financial_income" "data\raw\financial_income" $step.Time $stockCount
            Show-ProgressLine "financial_balance" "data\raw\financial_balance" $step.Time $stockCount
            Show-ProgressLine "financial_cashflow" "data\raw\financial_cashflow" $step.Time $stockCount
            Show-ProgressLine "financial_indicator" "data\raw\financial_indicator" $step.Time $stockCount
        }
        'supplement' {
            Show-ProgressLine "margin" "data\raw\margin" $step.Time $stockCount
            Show-ProgressLine "moneyflow" "data\raw\moneyflow" $step.Time $stockCount
            Show-ProgressLine "holder_number" "data\raw\holder_number" $step.Time $stockCount
            Show-ProgressLine "dividend" "data\raw\dividend" $step.Time $stockCount
        }
        'alternative' {
            foreach ($d in @('hk_hold','analyst_rc','share_float','holder_trade','cyq_perf','pledge_stat','top_list','top_inst','block_trade','stk_surv','fina_mainbz')) {
                Show-ProgressLine $d "data\raw\$d" $step.Time $stockCount
            }
            $hsgt = Join-Path $ROOT "data\raw\hsgt_flow.csv"
            if (Test-Path $hsgt) {
                $item = Get-Item $hsgt
                Write-Host ("{0,-18} file={1} updated={2}" -f "hsgt_flow", $item.Name, $item.LastWriteTime.ToString('HH:mm:ss'))
            }
        }
        default {
            Write-Host "No module-specific counter for this step yet."
        }
    }

    Show-RecentWarnings
    if ($completed) {
        Write-Host ""
        Write-Host "Phase 3 fast download completed." -ForegroundColor Green
        Write-Progress -Activity "Phase 3 fast download" -Completed
        break
    }

    Start-Sleep -Seconds $RefreshSeconds
}
