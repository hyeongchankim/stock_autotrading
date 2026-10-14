"""Install the macOS launchd jobs that replace the Windows scheduled tasks (see README / CONTEXT_HANDOFF.md).

  paper      watchlist sleeve  run_paper_cycle.sh               weekdays 09:00-15:30 every 15 min   (StockAutoTradingPaper)
  macd       MACD sleeve      run_macd_cycle.sh                weekdays 09:00-15:30 every 15 min   (StockAutoTradingMacd)
  scan1      discovery        run_discovery_cycle.sh scan      weekdays 11:00                      (StockDiscoveryScan1)
  scan2      discovery        run_discovery_cycle.sh scan      weekdays 13:30                      (StockDiscoveryScan2)
  final      discovery        run_discovery_cycle.sh final     weekdays 15:20                      (StockDiscoveryFinal)

Run: python3 scripts/install_launchd.py          (writes ~/Library/LaunchAgents/com.stockautotrading.*.plist and loads
                                                  the ones not loaded yet; already-loaded jobs are left alone)
Remove one: launchctl bootout gui/$(id -u)/com.stockautotrading.<name>
Holiday handling stays in the Python entry points (market_calendar guard), so the weekday trigger is enough.
"""
import os
import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = Path.home() / "Library" / "LaunchAgents"
WEEKDAYS = range(1, 6)  # launchd: 1=Mon ... 5=Fri
QUARTERS = [(h, m) for h in range(9, 16) for m in (0, 15, 30, 45) if (9, 0) <= (h, m) <= (15, 30)]

JOBS = {
    "paper": ("run_paper_cycle.sh", [], QUARTERS),
    "macd": ("run_macd_cycle.sh", [], QUARTERS),
    "scan1": ("run_discovery_cycle.sh", ["scan"], [(11, 0)]),
    "scan2": ("run_discovery_cycle.sh", ["scan"], [(13, 30)]),
    "final": ("run_discovery_cycle.sh", ["final"], [(15, 20)]),
}


def plist_for(name: str) -> dict:
    script, args, times = JOBS[name]
    return {
        "Label": f"com.stockautotrading.{name}",
        "ProgramArguments": ["/bin/bash", str(ROOT / script), *args],
        "WorkingDirectory": str(ROOT),
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(ROOT / "logs" / f"launchd_{name}.err"),
        "StartCalendarInterval": [{"Weekday": d, "Hour": h, "Minute": m} for d in WEEKDAYS for h, m in times],
    }


def main() -> None:
    AGENTS.mkdir(parents=True, exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    uid = os.getuid()
    for name in JOBS:
        label = f"com.stockautotrading.{name}"
        path = AGENTS / f"{label}.plist"
        loaded = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"], capture_output=True).returncode == 0
        if loaded:
            print(f"{label}: already loaded - left as is")
            continue
        path.write_bytes(plistlib.dumps(plist_for(name)))
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=True)
        print(f"{label}: installed ({len(plist_for(name)['StartCalendarInterval'])} triggers)")


if __name__ == "__main__":
    main()
