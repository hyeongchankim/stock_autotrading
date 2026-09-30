@echo off
REM Invoked by three separate Windows scheduled-task triggers (11:00/13:30
REM "scan", 15:20 "final") for the discovery ("발굴형 종가매매") sleeve - see
REM README's 발굴형 스케줄 등록 section. Takes the checkpoint as %1.
setlocal
if "%~1"=="" (
    echo usage: run_discovery_cycle.bat scan^|final
    exit /b 1
)
set SSL_CERT_FILE=C:\ca-certs\cacert.pem
set CURL_CA_BUNDLE=C:\ca-certs\cacert.pem
cd /d "%~dp0"
if not exist logs mkdir logs

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set LOGDATE=%%i

python main.py --mode discovery --checkpoint %1 >> logs\discovery_stdout_%LOGDATE%.log 2>&1
set PYEXIT=%ERRORLEVEL%

forfiles /p logs /m discovery_stdout_*.log /d -30 /c "cmd /c del @path" >nul 2>&1

exit /b %PYEXIT%
