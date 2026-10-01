@echo off
REM Invoked by the "StockAutoTradingMacd" Windows scheduled task every 15
REM minutes during KRX market hours - same cadence as StockAutoTradingPaper,
REM but a completely separate seed/state (macd_state.json) and strategy
REM (MACD cross alone, no regime/volume filter - see config.yaml's
REM macd_sleeve section for why).
setlocal
set SSL_CERT_FILE=C:\ca-certs\cacert.pem
set CURL_CA_BUNDLE=C:\ca-certs\cacert.pem
cd /d "%~dp0"
if not exist logs mkdir logs

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set LOGDATE=%%i

python main.py --mode macd >> logs\macd_stdout_%LOGDATE%.log 2>&1
set PYEXIT=%ERRORLEVEL%

forfiles /p logs /m macd_stdout_*.log /d -30 /c "cmd /c del @path" >nul 2>&1

exit /b %PYEXIT%
