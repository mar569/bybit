@echo off
REM TradingView с портом отладки (нужен для Cursor / tradingview-mcp)
taskkill /F /IM TradingView.exe >nul 2>&1
ping -n 3 127.0.0.1 >nul
start "" "%LOCALAPPDATA%\tradingview-mcp\TradingView.Desktop_3.4.1.8194_x64__n534cwy3pjxzj\TradingView.exe" --remote-debugging-port=9222
