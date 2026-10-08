# QA прогон EdBot через tradingview-mcp CLI (CDP 9222 + TV debug)
$ErrorActionPreference = "Continue"
$tvRoot = "D:\tradingview-mcp-main\tradingview-mcp-main"
$outDir = "d:\PROJECTS\Bybit_bot\docs\tv_qa_v35"
$syms = @(
    "BYBIT:BTCUSDT.P",
    "BYBIT:ETHUSDT.P",
    "BYBIT:SOLUSDT.P",
    "BYBIT:STRKUSDT.P",
    "BYBIT:DOGEUSDT.P",
    "BYBIT:WIFUSDT.P",
    "BYBIT:AVAXUSDT.P",
    "BYBIT:LINKUSDT.P",
    "VANTAGE:UKOUSD"
)
Set-Location $tvRoot
$status = node src/cli/index.js status 2>&1 | Out-String
if ($status -notmatch '"success": true') {
    Write-Error "CDP not connected. Run Launch-TradingView-Debug.bat first."
    exit 2
}
node src/cli/index.js timeframe 15 2>&1 | Out-Null
$report = @()
foreach ($s in $syms) {
    $safe = ($s -replace "[:.]", "_")
    Write-Host "=== $s ==="
    node src/cli/index.js symbol $s 2>&1 | Out-Null
    Start-Sleep -Seconds 5
    $stJson = node src/cli/index.js state 2>&1 | Out-String
    $stJson | Set-Content -Path (Join-Path $outDir "$safe.state.json") -Encoding UTF8
    $edVer = "missing"
    if ($stJson -match '"name": "Ed Bot Analysis v([^"]+)"') { $edVer = $matches[1] }
    $studyN = 0
    if ($stJson -match '"studies":\s*\[') {
        $studyN = ([regex]::Matches($stJson, '"name":')).Count
    }
    $shot = node src/cli/index.js screenshot -r chart -o "qa_$safe" 2>&1 | Out-String
    $shotOk = $shot -match '"success": true'
    $report += [pscustomobject]@{ Symbol = $s; EdBot = $edVer; Studies = $studyN; Screenshot = $shotOk }
}
$report | Format-Table -AutoSize
$report | ConvertTo-Json | Set-Content (Join-Path $outDir "summary.json") -Encoding UTF8
Write-Host "Done -> $outDir"
