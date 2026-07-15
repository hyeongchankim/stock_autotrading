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
python main.py --mode paper >> logs\scheduler_stdout.log 2>&1
