#!/bin/bash
# Paper sleeve, every 15 min in KRX hours (mac port of run_paper_cycle.bat).
cd "$(dirname "$0")" || exit 1
mkdir -p logs
set -a; [ -f .env ] && source .env; set +a
[ -f .venv/bin/activate ] && source .venv/bin/activate
LOGDATE=$(date +%F)

python main.py --mode paper >> logs/scheduler_stdout_$LOGDATE.log 2>&1
PYEXIT=$?
find logs -name 'scheduler_stdout_*.log' -mtime +30 -delete 2>/dev/null
exit $PYEXIT
