"""State policy buyout output (item 3) and the pinned ReEDS state-policy inputs (item 4)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
PIN = REPO / "pg/extra_inputs/reeds_state_policies"


def rps_module():
    spec = importlib.util.spec_from_file_location("rps_regional", REPO / "switch/study_modules/rps_regional.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_buyouts_by_state_year():
    rows = pd.DataFrame({"RPS_PROGRAM": ["ESR_NY_rps", "ESR_NY_ces", "ESR_MD_rps_solar", "OTHER"],
                         "PERIOD": [2030, 2030, 2035, 2030], "rps_acp_per_mwh": [100.0] * 4,
                         "target_mwh_per_yr": [10.0, 20.0, 5.0, 1.0], "shortfall_mwh_per_yr": [1.0, 2.0, 0.5, 0.0],
                         "shortfall_cost_per_yr": [100.0, 200.0, 50.0, 0.0]})
    out = rps_module().buyouts_by_state_year(rows, {2030: (2029, 2030), 2035: (2031, 2035)})
    ny = out[out.state == "NY"]
    assert list(ny.year) == [2029, 2030] and (ny.buyout_mwh_per_yr == 3.0).all() and (ny.buyout_cost_per_yr == 300.0).all()
    assert (ny.programs == 2).all()
    md = out[out.state == "MD"]
    assert list(md.year) == list(range(2031, 2036)) and (md.buyout_cost_per_yr == 50.0).all()
    assert set(out.state) == {"NY", "MD", "OTHER"}
    assert list(out.columns) == ["state", "year", "PERIOD", "period_start", "period_end", "programs",
                                 "target_mwh_per_yr", "buyout_mwh_per_yr", "buyout_cost_per_yr"]
    assert rps_module().buyouts_by_state_year(rows.iloc[0:0], {}).empty


def test_reeds_inputs_pinned():
    rel = yaml.safe_load(open(PIN / "REEDS_RELEASE.yml"))
    assert rel["release"] == "2026.09.21" and rel["commit"] == "8a1572331da89b15ad3c0f74db448911f715266c"
    assert rel["repository"] == "https://github.com/ReEDS-Model/ReEDS"
    for f in rel["files"]:
        p = PIN / f
        assert p.exists() and p.stat().st_size > 0, f
        assert "git-lfs" not in p.read_text()[:200], f
    src = (REPO / "make_emission_policies.py").read_text()
    assert "refs/heads/main" not in src and "reeds_input(" in src
    # every ReEDS file the script reads goes through reeds_input() and is pinned here
    used = set(__import__("re").findall(r'reeds_input\(f?"([^"]+)"\)', src))
    used = {u.replace("{prog}", p) for u in used for p in ("rps", "ces")} if any("{prog}" in u for u in used) else used
    assert used <= set(rel["files"]) | {"state_policies/rps_fraction.csv", "state_policies/ces_fraction.csv"}


def test_reeds_policy_diff_report(tmp_path):
    out = tmp_path / "diff.csv"
    r = subprocess.run([sys.executable, str(REPO / "s0_workflow/scripts/compare_reeds_state_policies.py"),
                        "--out", str(out)], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    d = pd.read_csv(out, comment="#")
    committed = pd.read_csv(REPO / "s0_workflow/data/reeds_state_policy_diff_2026.09.21.csv", comment="#")
    pd.testing.assert_frame_equal(d, committed)                                   # the committed report is current
    assert not d.program.isin(["ESR_NY_rps", "ESR_NY_ces"]).any()                 # NY targets unchanged
    nc = d[(d.program == "ESR_NC_ces") & (d.year == 2035)].iloc[0]
    assert nc.target_current == pytest.approx(0.5738, abs=1e-4) and nc.target_pinned == pytest.approx(0.3915, abs=1e-4)
    assert set(d[d.status == "dropped in release"].program.str.replace("UREC_Limit_", "")) == {"ESR_AZ_rps"}
