"""§115 (Tom's decision C): the S-set settings and tests from tom/s0-v3.1-credits are on tom/s0-v3.2, so the v3.2 S-set
builds from one branch. Every S-set row resolves on v3.2 exactly as on the credits branch, except the v3.2 flags,
which sit at their v3.1 settings (inert). Settings files from git; no build."""
import subprocess
import tempfile
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CREDITS = "f8f12bc"           # the S-set launch commit on tom/s0-v3.1-credits
S_SET = ["S0prod_A", "S1", "S2", "S3", "S4", "S5", "L", "P"]
FILES = ("pg/extra_inputs/scenario_inputs.csv", "pg/settings/scenario_management.yml", "pg/settings/s0_production.yml")
V32_FLAGS = {"s0_production.storage_credit", "s0_production.pv_profile_repair",
             "s0_production.ca_wa_carbon.two_tranche", "s0_production.time_sampling.cf_fidelity",
             "s0_production.prm.thermal_derate.prm_temp_tz_fix", "s0_production.startup_cost_fix",
             "s0_production.offshore_filter_fix",
             # §126: the wind interconnection price factor (v3.2 ON), in the S0 settings block and merged into the case
             "s0_production.settings.interconnection_headroom.price_factors", "interconnection_headroom.price_factors"}


def _resolve(paths, case, year):
    import sys
    sys.path.insert(0, str(REPO))
    from s0_workflow import chain_reuse as cr
    si = pd.read_csv(paths[0])
    sm = yaml.safe_load(open(paths[1]))["settings_management"]
    base = yaml.safe_load(open(paths[2]))["s0_production"]
    rows = si[(si.case_id == case) & (si.year == year)]
    if rows.empty:
        return None
    cols = [c for c in si.columns if c not in ("case_id", "year")]
    return cr._at_year(cr._merged(sm["all_years"], sm.get(year, {}), rows.iloc[0], cols, base, year), year)


def test_s_set_rows_resolve_as_on_the_credits_branch():
    from s0_workflow import chain_reuse as cr
    with tempfile.TemporaryDirectory() as d:
        old = []
        for f in FILES:
            r = subprocess.run(["git", "show", f"{CREDITS}:{f}"], cwd=REPO, capture_output=True, text=True)
            if r.returncode:
                pytest.skip(f"{CREDITS} not available")
            p = Path(d) / Path(f).name
            p.write_text(r.stdout)
            old.append(p)
        si = pd.read_csv(REPO / FILES[0])
        n = 0
        for case in S_SET:
            for year in sorted(si[si.case_id == case].year.unique()):
                a, b = _resolve([REPO / f for f in FILES], case, int(year)), _resolve(old, case, int(year))
                assert (a is None) == (b is None), (case, year)
                if a is None:
                    continue
                diffs = cr._diff_paths(a, b)
                other = [x for x in diffs if not any(x.startswith(k + ":") or x.startswith(k + ".") for k in V32_FLAGS)]
                assert not other, (case, year, other)
                n += 1
    assert n >= len(S_SET) * 3


# §126: the regression row's axis value (on_pgdays) sets the v3.2 defaults back to v3.1; the only change from the
# credits branch's scenario_management.yml
ON_PGDAYS_V31 = {"settings": {"interconnection_headroom": {"price_factors": {"wind": None}}},
                 "storage_credit": {"mode": "flat"}, "startup_cost_fix": {"enabled": False},
                 "prm": {"thermal_derate": {"prm_temp_tz_fix": False}},
                 "offshore_filter_fix": {"mode": "off", "resource_groups_dir": None}}           # §129


def test_scenario_management_differs_only_by_the_regression_row_v31_pins():
    r = subprocess.run(["git", "show", f"{CREDITS}:pg/settings/scenario_management.yml"], cwd=REPO, capture_output=True,
                       text=True)
    if r.returncode:
        pytest.skip(f"{CREDITS} not available")
    old, now = yaml.safe_load(r.stdout), yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))
    pg = now["settings_management"]["all_years"]["s0_production"]["on_pgdays"]["s0_production"]
    for k, v in ON_PGDAYS_V31.items():
        assert pg.pop(k) == v
    assert pg["time_sampling"].pop("cf_fidelity") == {"mode": "off"}
    assert pg["ca_wa_carbon"].pop("two_tranche") == {"preset": "v31"}
    assert now == old


def test_s_set_files_are_the_credits_branch_files():
    for f in ("pg/extra_inputs/scenario_inputs.csv",
              "Guides and documentation/s_set_scenarios.md", "s0_workflow/tests/test_tax_credits.py"):
        r = subprocess.run(["git", "show", f"{CREDITS}:{f}"], cwd=REPO, capture_output=True)
        if r.returncode:
            pytest.skip(f"{CREDITS} not available")
        assert (REPO / f).read_bytes().replace(b"\r\n", b"\n") == r.stdout.replace(b"\r\n", b"\n"), f
