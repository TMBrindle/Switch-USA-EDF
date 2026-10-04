"""RGGI Third Program Review floor and CCR for every S0 case (CHANGES §62)."""
import copy
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import production as s0prod  # noqa: E402

EXPECT = {   # model year: (cap t, floor, CCR1, CCR2, volume) in $ and t per metric tonne
    2028: (55411816, 10.62, 23.01, 34.50, 10656120.0),
    2030: (39579868, 12.15, 26.34, 39.50, 10656120.0),
    2035: (12520768, 17.04, 36.93, 55.39, 10656120.0),
    2040: (8191313, 23.90, 51.80, 77.69, 10656120.0),
    2045: (8191313, 33.52, 72.65, 108.96, 10656120.0),
}


def test_files_tracked_and_model_rule_rate():
    tracked = subprocess.run(["git", "ls-files", "pg/extra_inputs/rggi_carbon"], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.split()
    assert {"pg/extra_inputs/rggi_carbon/rggicon_3pr.csv", "pg/extra_inputs/rggi_carbon/rggi_3pr_parameters.csv"} <= set(tracked)
    t = pd.read_csv(REPO / "pg/extra_inputs/rggi_carbon/rggi_3pr_parameters.csv")
    assert list(t.year) == list(range(2027, 2038))
    for c in ("min_reserve_price_per_short_ton", "ccr_t1_trigger_per_short_ton", "ccr_t2_trigger_per_short_ton"):
        # the Model Rule's 7%/yr, to the cent
        assert all(abs(b - round(a * 1.07, 2)) <= 0.011 for a, b in zip(t[c], t[c][1:])), c
    assert t.ccr_t1_volume_metric_tonnes.nunique() == 1 and t.ccr_t2_volume_metric_tonnes.nunique() == 1


def test_values_by_model_year():
    cap = pd.read_csv(REPO / "pg/extra_inputs/rggi_carbon/rggicon_3pr.csv", header=None, names=["y", "t"]).set_index("y").t
    for y, (c, floor, t1, t2, vol) in EXPECT.items():
        v = s0prod.rggi_3pr_values(y)
        assert cap[y] == c and v["floor"] == pytest.approx(floor) and v["ccr_prices"] == pytest.approx([t1, t2])
        assert v["ccr_volumes"] == [vol, vol]
    # after 2037: cap held, prices +7%/yr from 2037 (short tons, converted as the file does)
    assert (cap.loc[2037:2050] == cap[2037]).all()
    assert s0prod.rggi_3pr_values(2038)["floor"] == round(round(17.70 * 1.07, 2) / 0.907185, 2)
    assert s0prod.rggi_3pr_values(2037)["floor"] == 19.51 and s0prod.rggi_3pr_values(2020)["floor"] == 9.92


def test_s0_policy_cap_is_rggicon_3pr():
    """The S0 policy file (pinned ReEDS release, built by build_reeds_state_policies.py) carries the 3PR cap in every
    model year; the ReEDS copy of rggicon.csv is not used."""
    e = pd.read_csv(REPO / "pg/extra_inputs/rggi_carbon/emission_policies_reeds_2026.09.21.csv")
    for y, (c, *_) in EXPECT.items():
        sub = e[e.year == y]
        assert (sub.CO_2_Max_Mtons_1 * sub.CO_2_Cap_Zone_1).sum() * 1e6 == pytest.approx(c, abs=1)
    rel = (REPO / "pg/extra_inputs/reeds_state_policies/REEDS_RELEASE.yml").read_text()
    assert "SUPERSEDED" in rel


def _case(axis_value, policies="S0_uncapped", year=2035):
    sm = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    sw = yaml.safe_load(open(REPO / "pg/settings/switch.yml"))
    s = {"carbon_ccr": copy.deepcopy(sw["carbon_ccr"]), "s0_production": copy.deepcopy(s0),
         "model_first_planning_year": {2028: 2026, 2030: 2029, 2035: 2031, 2040: 2036, 2045: 2041}[year]}
    s = s0prod.deep_merge(s, sm["all_years"]["policies"][policies] or {})
    s = s0prod.deep_merge(s, ((sm.get(year) or {}).get("policies") or {}).get(policies) or {})
    if axis_value:
        s = s0prod.deep_merge(s, sm["all_years"]["s0_production"][axis_value])
    return s


def test_every_s0_case_gets_floor_and_ccr_and_legacy_keeps_its_inputs():
    for y, (_, floor, t1, t2, vol) in EXPECT.items():
        s = _case("on", year=y)
        assert "carbon_floor_price_by_program" not in s and "carbon_ccr_prices" not in s   # S0_uncapped: none before
        cs = {"c": {y: s}}
        s0prod.apply_settings(cs)
        s = cs["c"][y]
        assert s["carbon_floor_price_by_program"] == {"ETS 1": floor}
        assert s["carbon_ccr_prices"] == {"ETS 1": [t1, t2]}
        assert s["carbon_ccr"]["ETS 1"]["ccr_pools_tco2_per_yr"] == [vol, vol]
        assert s["carbon_cost_by_program"]["ETS 1"] == "."                                 # the cap stays hard
    # every preset, not only current (on_single, the bill and 2035 test cases, use the same S0 layer)
    for pol in ("S0_uncapped", "S0", "current_uncapped", "current"):
        cs = {"c": {2040: _case("on", pol, 2040)}}
        s0prod.apply_settings(cs)
        assert cs["c"][2040]["carbon_floor_price_by_program"]["ETS 1"] == 23.90, pol
    # the regression case (on_pgdays: rggi legacy): its preset's values, i.e. none for S0_uncapped
    for y in (2028, 2035):
        cs = {"c": {y: _case("on_pgdays", year=y)}}
        before = copy.deepcopy(cs["c"][y])
        s0prod.apply_settings(cs)
        assert "carbon_floor_price_by_program" not in cs["c"][y] and "carbon_ccr_prices" not in cs["c"][y]
        assert cs["c"][y]["carbon_ccr"] == before["carbon_ccr"]
    # non-S0 cases: untouched
    s = _case(None, "current", 2035)
    s["s0_production"]["enabled"] = False
    before = copy.deepcopy(s)
    s0prod.apply_settings({"c": {2035: s}})
    assert s == before
    with pytest.raises(ValueError, match="rggi.mode"):
        s0prod.rggi_settings({"rggi": {"mode": "ecr"}})


def test_writer_reads_these_settings():
    """pg_to_switch writes carbon_policies_regional.csv's floor from carbon_floor_price_by_program and
    carbon_policies_ccr.csv from carbon_ccr_prices x carbon_ccr pools (the existing mechanism)."""
    src = (REPO / "pg_to_switch.py").read_text(encoding="utf-8")
    assert 'scen_settings.get("carbon_floor_price_by_program")' in src
    assert 'scen_settings.get("carbon_ccr_prices", {})' in src and 'first_year_settings.get("carbon_ccr", {})' in src
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    s0rows = si[si.s0_production != "off"]
    assert set(s0rows.policies) == {"S0_uncapped"} and set(s0rows.rggi) == {"RGGI10"}
