"""Run a command and record its wall time and peak memory (the process and all its children), for the
mode-B window test (s0_workflow/VM_RECIPES.md, recipe B).

Uses psutil when the env already has it (it is a PowerGenome / pyomo dependency on most envs); without
it, only wall time is recorded and the recipe's PowerShell fallback gives memory. Installs nothing.
usage: python s0_workflow/scripts/measure_run.py --log <file.json> -- <command> [args ...]
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    try:
        import psutil
    except ImportError:
        psutil = None
    t0 = time.time()
    p = subprocess.Popen(cmd)
    peak = 0
    while p.poll() is None:
        if psutil is not None:
            try:
                pr = psutil.Process(p.pid)
                rss = pr.memory_info().rss + sum(c.memory_info().rss for c in pr.children(recursive=True))
                peak = max(peak, rss)
            except Exception:  # noqa: BLE001  (process ended between calls)
                pass
        time.sleep(a.interval)
    rec = {"command": cmd, "start": datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
           "wall_s": round(time.time() - t0, 1), "exit_code": p.returncode,
           "peak_rss_gb": round(peak / 1e9, 2) if psutil is not None else None,
           "memory_source": "psutil (sum of process tree RSS, sampled)" if psutil is not None else "not measured"}
    json.dump(rec, open(a.log, "w"), indent=1)
    print(json.dumps(rec, indent=1))
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
