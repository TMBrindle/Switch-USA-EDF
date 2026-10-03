"""Run from the repo root: python s0_workflow/day_selection/pg_to_switch_netload.py pg/settings <out folder> --case-id <id> --year <year>
Run the repo's pg_to_switch.py unchanged, except that its kmeans_time_clustering is replaced by
netload_days.kmeans_time_clustering (net-load day selection). Run with cwd = repo root, same arguments as
pg_to_switch.py. Config via env NL_DAYS_CFG (JSON; see netload_days.py). No tracked file is modified."""
import sys
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.argv[0] = str(REPO / "pg_to_switch.py")
import typer
import pg_to_switch as P
import importlib, os
sel_name = os.environ.get("NL_SELECTOR", "netload_days_fi")  # fleet-independent selector (netload_days.py, the first fleet-based version, is not staged)
sel = importlib.import_module(sel_name)

P.kmeans_time_clustering = sel.kmeans_time_clustering
P.logger.info(f"pg_to_switch_netload: kmeans_time_clustering replaced by {sel_name}")
app = typer.Typer(pretty_exceptions_enable=False, pretty_exceptions_show_locals=False)
app.command()(P.main)
app()
