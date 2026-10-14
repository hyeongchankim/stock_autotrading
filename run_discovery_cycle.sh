#!/bin/bash
# Discovery sleeve; usage: run_discovery_cycle.sh scan|final (mac port of run_discovery_cycle.bat).
cd "$(dirname "$0")" || exit 1
mkdir -p logs
set -a; [ -f .env ] && source .env; set +a
[ -f .venv/bin/activate ] && source .venv/bin/activate
LOGDATE=$(date +%F)
[ -z "$1" ] && { echo "usage: $0 scan|final"; exit 1; }
python main.py --mode discovery --checkpoint "$1" >> logs/discovery_stdout_$LOGDATE.log 2>&1
PYEXIT=$?
find logs -name 'discovery_stdout_*.log' -mtime +30 -delete 2>/dev/null
exit $PYEXIT
