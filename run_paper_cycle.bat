@echo off
REM Invoked by the "StockAutoTradingPaper" Windows scheduled task every 15
REM minutes during KRX market hours. Sets the SSL cert env vars this PC
REM needs (see README troubleshooting - Korean username breaks the default
REM cert lookup for yfinance/pykrx) since Task Scheduler does not inherit a
REM logged-in shell's temporary exports, only persistent env vars.
setlocal
set SSL_CERT_FILE=C:\ca-certs\cacert.pem
set CURL_CA_BUNDLE=C:\ca-certs\cacert.pem
cd /d "%~dp0"
if not exist logs mkdir logs

REM %date%'s format is locale-dependent, so get an unambiguous YYYY-MM-DD
REM from PowerShell instead. This captures stdout the Python logging setup
REM can't (e.g. pykrx's own print()-based KRX login messages) into one file
REM per day (logs\scheduler_stdout_YYYY-MM-DD.log) - daily files rotate
REM themselves, then get pruned by the forfiles cleanup below (30-day
REM retention, matching utils/logger.py's Python-side log rotation).
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set LOGDATE=%%i

python main.py --mode paper >> logs\scheduler_stdout_%LOGDATE%.log 2>&1
set PYEXIT=%ERRORLEVEL%

REM forfiles exits non-zero when nothing matches its age filter (the normal
REM case on most days) - must not let that clobber the script's own exit
REM code, or Task Scheduler's "Last Result" would misreport a failed cycle
REM as failed just because there was nothing to prune that day.
forfiles /p logs /m scheduler_stdout_*.log /d -30 /c "cmd /c del @path" >nul 2>&1

exit /b %PYEXIT%
