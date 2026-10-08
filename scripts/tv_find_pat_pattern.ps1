# Scan symbols until EdBot chart shows a graphic pattern label (2x / GiP / 1-2-3)
param(
    [string]$TvCliRoot = "D:\tradingview-mcp-main\tradingview-mcp-main",
    [string]$OutDir = "d:\PROJECTS\Bybit_bot\docs\tv_pat_scan"
)
$ErrorActionPreference = "Continue"
$syms = @(
    "BYBIT:BTCUSDT.P", "BYBIT:ETHUSDT.P", "BYBIT:SOLUSDT.P", "BYBIT:DOGEUSDT.P",
    "BYBIT:XRPUSDT.P", "BYBIT:LINKUSDT.P", "BYBIT:AVAXUSDT.P", "BYBIT:ADAUSDT.P",
    "BYBIT:WIFUSDT.P", "BYBIT:PEPEUSDT.P", "BYBIT:STRKUSDT.P", "BYBIT:ALGOUSDT.P",
    "BYBIT:NEARUSDT.P", "BYBIT:APTUSDT.P", "BYBIT:OPUSDT.P", "BYBIT:ARBUSDT.P",
    "BYBIT:INJUSDT.P", "BYBIT:SUIUSDT.P", "BYBIT:SEIUSDT.P", "BYBIT:TONUSDT.P",
    "BYBIT:1000BONKUSDT.P", "BYBIT:FETUSDT.P", "BYBIT:RENDERUSDT.P", "BYBIT:ENAUSDT.P",
    "VANTAGE:UKOUSD", "NASDAQ:TSLA", "NYSE:NVDA"
)
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Push-Location $TvCliRoot
$status = node src/cli/index.js status 2>&1 | Out-String
if ($status -notmatch '"success":\s*true') {
    Write-Error "CDP not connected. Run Launch-TradingView-Debug.bat"
    exit 2
}
node src/cli/index.js timeframe 15 2>&1 | Out-Null
$found = $null
foreach ($s in $syms) {
    Write-Host "=== $s ==="
    node src/cli/index.js symbol $s 2>&1 | Out-Null
    Start-Sleep -Seconds 4
    $safe = ($s -replace "[:.]", "_")
    $shotPath = Join-Path $OutDir "pat_$safe.png"
    node src/cli/index.js screenshot -r chart -o $shotPath 2>&1 | Out-Null
    if (Test-Path ($shotPath -replace '\.png$','') ) { } # CLI may omit ext
    $png = if (Test-Path $shotPath) { $shotPath } elseif (Test-Path "$shotPath.png") { "$shotPath.png" } else { Get-ChildItem $OutDir -Filter "pat_$safe*" | Select-Object -First 1 -ExpandProperty FullName }
    $st = node src/cli/index.js state 2>&1 | Out-String
    $edId = ""
    if ($st -match '"name":\s*"Ed Bot Analysis[^"]*"[^}]*"id":\s*"([^"]+)"') { $edId = $matches[1] }
    elseif ($st -match '"id":\s*"([^"]+)"[^}]*"name":\s*"Ed Bot Analysis') { $edId = $matches[1] }
    $obj = [pscustomobject]@{ Symbol = $s; Screenshot = $png; EdBotId = $edId }
    $obj | ConvertTo-Json | Set-Content (Join-Path $OutDir "$safe.json") -Encoding UTF8
    Write-Host "  shot: $png  edbot: $edId"
    $found = @{ Symbol = $s; Shot = $png; State = $st }
}
Pop-Location
Write-Host "Scan complete -> $OutDir (review screenshots for pattern labels)"
