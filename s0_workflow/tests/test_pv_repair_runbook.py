"""§117: the VM runbook (s0_workflow/scripts/README_pv_repair.md) matches the script and the dashboards' data: every
option it names exists, and its expected counts are the ones zeroed_pv_sites.csv gives."""
import re
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
RUNBOOK = REPO / "s0_workflow/scripts/README_pv_repair.md"


def test_runbook_options_exist_in_the_script():
    from s0_workflow.scripts import repair_pv_profiles as cli
    src = (REPO / "s0_workflow/scripts/repair_pv_profiles.py").read_text()
    for opt in sorted(set(re.findall(r"--[a-z][a-z-]+", RUNBOOK.read_text()))):
        if opt in ("--cpa-col/--site-col/--lat-col/--lon-col/--cap-col",):
            continue
        assert f'"{opt}"' in src or f"--{{k}}-col" in src and opt.endswith("-col"), opt
    assert callable(cli.main)


def test_runbook_counts_match_the_zeroed_list():
    z = pd.read_csv(REPO / "s0_workflow/data/pv_profile_repair/zeroed_pv_sites.csv", dtype={"site": str})
    u = z.groupby("site").agg(mw=("all_cpa_mw", "sum"), n_cpa=("n_cpa", "sum"), years=("years_zero", "first"))
    u["n"] = u.years.str.count(";") + 1
    with_cap = u[u.mw > 0]
    no_cpa = u[u.n_cpa == 0]
    text = RUNBOOK.read_text()
    for value in (len(u), u.n.sum(), len(with_cap), with_cap.n.sum(), len(no_cpa), no_cpa.n.sum()):
        assert f"{value:,}" in text, value
    assert (len(u), u.n.sum(), len(with_cap), with_cap.n.sum(), len(no_cpa), no_cpa.n.sum()) == \
        (5390, 14553, 5330, 14350, 60, 203)
    for field in ("zero_site_years_with_capacity_after", "site_years_zero_before", "site_years_filled",
                  "zeroed_list_check", "fill_summary", "listed_not_found", "found_not_listed", "clipped_hours"):
        assert field in text, field
    for field in ("zero_site_years_with_capacity_after", "zeroed_list_check", "fill_summary", "site_years_filled"):
        assert field in (REPO / "s0_workflow/pv_profile_repair.py").read_text(), field
