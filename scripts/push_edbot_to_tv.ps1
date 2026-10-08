# Push Ed Bot Analysis Pine to TradingView Desktop (UTF-8, no mangled Cyrillic).
# Requires: TV Desktop with --remote-debugging-port=9222 (see Launch-TradingView-Debug.bat)
param(
    [string]$PineFile = (Join-Path $PSScriptRoot "Ed_Bot_Analysis_v2.pine"),
    [string]$TvCliRoot = "D:\tradingview-mcp-main\tradingview-mcp-main"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $PineFile)) { throw "Pine file not found: $PineFile" }
$cli = Join-Path $TvCliRoot "src\cli\index.js"
if (-not (Test-Path $cli)) { throw "TradingView CLI not found: $cli" }

Push-Location $TvCliRoot
try {
    Write-Host "Checking Pine (server compile)..."
    node src/cli/index.js pine check --file $PineFile | Out-Host
    Write-Host "Setting editor source (UTF-8 from file)..."
    node src/cli/index.js pine set --file $PineFile | Out-Host
    Write-Host "Smart compile..."
    node src/cli/index.js pine compile | Out-Host
    Write-Host "Done. In TV: Save (Ctrl+S) and Add to chart if needed."
}
finally {
    Pop-Location
}
