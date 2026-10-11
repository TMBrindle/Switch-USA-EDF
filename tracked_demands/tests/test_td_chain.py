"""Carry-forward between chain stages (§108 (c)): a two-stage myopic toy chain (2020, then 2030).

prepare_next_stage hands on, as <file>.chained.case.csv in the 2030 inputs: on-site MW, battery MW/MWh and H2 tank
(existing, expandable capacity), the siting of a location-flexible TD (fixed after the first stage), and the cap
coverage of linked generators.
"""
from __future__ import annotations

import subprocess
import sys

import pandas as pd
import pytest

from td_toy import add_onsite_gen, carbon_cap, csv, set_td, soft_cap, td_chain

TOL = 1e-5


def chained(run, f):
    return pd.read_csv(run / "in/2030/case" / f"{f}.chained.case.csv")


def built(out):
    return pd.read_csv(out / "tracked_demand_onsite_build.csv").set_index("tech")["built_MW"]


def test_onsite_and_battery_carried_as_existing_capacity(tmp_path):
    """Stage 2030 makes on-site solar and batteries unattractive; the 2020 build stays (lower bound)."""
    def dear(stage, inp):
        if stage == "2030":
            t = pd.read_csv(inp / "tracked_demand_onsite_techs.csv", na_values=".")
            t["td_onsite_tech_capital_cost"] *= 50
            csv(inp, "tracked_demand_onsite_techs.csv", t)
            (inp / "tracked_demand_cfe_targets.csv").unlink()
    run, stages, outs = td_chain(tmp_path, "cap", matching="hourly", stage_edit=dear)
    b1, b2 = built(outs[0]), built(outs[1])
    assert b1["solar_onsite"] > 0.5
    ex = chained(run, "tracked_demand_onsite_existing").set_index("td_onsite_tech")["td_onsite_existing_mw"]
    assert ex["solar_onsite"] == pytest.approx(b1["solar_onsite"], rel=1e-5)
    assert b2["solar_onsite"] == pytest.approx(b1["solar_onsite"], rel=1e-5)
    s1 = pd.read_csv(outs[0] / "tracked_demand_storage_build.csv")
    s2 = pd.read_csv(outs[1] / "tracked_demand_storage_build.csv")
    sx = chained(run, "tracked_demand_storage_existing")
    assert sx["td_storage_existing_mw"].iat[0] == pytest.approx(s1["built_power_MW"].iat[0], rel=1e-5)
    assert sx["td_storage_existing_mwh"].iat[0] == pytest.approx(s1["built_energy_MWh"].iat[0], rel=1e-5)
    assert s2["built_power_MW"].iat[0] >= s1["built_power_MW"].iat[0] - TOL
    assert s2["built_energy_MWh"].iat[0] >= s1["built_energy_MWh"].iat[0] - TOL


def test_existing_capacity_is_expandable(tmp_path):
    """A bigger data centre in 2030 builds more on-site solar than 2020 left it."""
    def bigger(stage, inp):
        if stage == "2030":
            set_td(inp, td_default_min_power_mw=4.0, td_default_max_power_mw=4.0, td_grid_interconnect_mw=4.0,
                   td_energy_requirement_mwh_per_year=0.9 * 4 * 8760)
    run, stages, outs = td_chain(tmp_path, "grow", matching="hourly", stage_edit=bigger)
    assert built(outs[1])["solar_onsite"] > built(outs[0])["solar_onsite"] + 0.1


def test_h2_tank_carried(tmp_path):
    def el(inp):
        csv(inp, "tracked_demands.csv", [{"TRACKED_DEMAND": "DC1", "td_type": "electrolyzer",
                                          "td_energy_requirement_mwh_per_year": 0.5 * 3 * 8760,
                                          "td_default_min_power_mw": 0, "td_default_max_power_mw": 3}])
        csv(inp, "tracked_demand_h2_storage.csv", [{"TRACKED_DEMAND": "DC1", "td_h2_storage_cost_per_kg_yr": 1.0,
                                                    "td_h2_min_delivery_kg_per_hr": 0.9 * 0.5 * 3 / 0.05}])

    def relax(stage, inp):
        if stage == "2030":
            csv(inp, "tracked_demand_h2_storage.csv", [{"TRACKED_DEMAND": "DC1", "td_h2_storage_cost_per_kg_yr": 1.0}])
    run, stages, outs = td_chain(tmp_path, "h2", onsite=False, storage=False, after=el, stage_edit=relax)
    k1 = pd.read_csv(outs[0] / "tracked_demand_h2_build.csv")["h2_tank_capacity_kg"].iat[0]
    k2 = pd.read_csv(outs[1] / "tracked_demand_h2_build.csv")["h2_tank_capacity_kg"].iat[0]
    assert k1 > 1.0
    assert chained(run, "tracked_demand_h2_existing")["td_h2_existing_kg"].iat[0] == pytest.approx(k1, rel=1e-5)
    assert k2 >= k1 - TOL


def test_siting_fixed_after_first_stage(tmp_path):
    """A location-flexible TD sites in 2020; in 2030 a cheap zone appears, but the siting stays."""
    def flex(inp):
        set_td(inp, td_location_fixed=0)
        csv(inp, "tracked_demand_candidate_zones.csv",
            [{"TRACKED_DEMAND": "DC1", "LOAD_ZONE": z} for z in ("North", "Central", "South")])
    run, stages, outs = td_chain(tmp_path, "site", onsite=False, storage=False, clean_fraction=None, after=flex)
    a1 = pd.read_csv(outs[0] / "tracked_demand_zone_allocation.csv").set_index("zone")["allocation_fraction"]
    a2 = pd.read_csv(outs[1] / "tracked_demand_zone_allocation.csv").set_index("zone")["allocation_fraction"]
    fx = chained(run, "tracked_demand_siting_fixed").set_index("LOAD_ZONE")["td_fixed_allocation"]
    for z in a1.index:
        assert fx.get(z, 0.0) == pytest.approx(a1[z], abs=1e-6)
        assert a2[z] == pytest.approx(a1[z], abs=1e-6)


def test_siting_fixed_binds(tmp_path):
    """The fixed siting is enforced: force 2020 into Central only, then offer South in 2030; it stays in Central."""
    def flex(inp):
        set_td(inp, td_location_fixed=0)
        csv(inp, "tracked_demand_candidate_zones.csv",
            [{"TRACKED_DEMAND": "DC1", "LOAD_ZONE": z} for z in ("Central", "South")])

    def stage(name, inp):
        if name == "2020":
            csv(inp, "tracked_demand_siting_fixed.csv",
                [{"TRACKED_DEMAND": "DC1", "LOAD_ZONE": "Central", "td_fixed_allocation": 1.0}])
    run, stages, outs = td_chain(tmp_path, "bind", onsite=False, storage=False, clean_fraction=None, after=flex,
                                 stage_edit=stage)
    a2 = pd.read_csv(outs[1] / "tracked_demand_zone_allocation.csv").set_index("zone")["allocation_fraction"]
    assert a2["Central"] == pytest.approx(1.0, abs=1e-6) and a2["South"] == pytest.approx(0.0, abs=1e-6)


def test_cap_coverage_carried(tmp_path):
    """Covered in 2020 (no threshold); 2030's settings would exempt it (25 MW, unit 2 MW), but coverage carries."""
    def gen(inp):
        add_onsite_gen(inp, unit_mw=2.0)
        soft_cap(inp)
        carbon_cap(inp)

    def stage(name, inp):
        if name == "2030":
            csv(inp, "tracked_demand_cap_coverage.csv", [{"CO2_PROGRAM": "RGGI", "td_cap_threshold_mw": 25}])
    run, stages, outs = td_chain(tmp_path, "cov", modules=("carbon_policies_regional", "tracked_demands"),
                                 onsite=False, storage=False, after=gen, stage_edit=stage)
    e1 = pd.read_csv(outs[0] / "tracked_demand_onsite_emissions.csv")
    e2 = pd.read_csv(outs[1] / "tracked_demand_onsite_emissions.csv")
    assert e1["covered_by_cap"].iat[0] == 1
    cov = chained(run, "tracked_demand_cap_coverage_fixed")
    assert list(cov["td_cap_covered"]) == [1] and list(cov["CO2_PROGRAM"]) == ["RGGI"]
    assert e2["covered_by_cap"].iat[0] == 1 and e2["coverage_reason"].iat[0] == "carried_from_earlier_stage"


def test_stage_reuse_handover_writes_the_same_files(tmp_path):
    """chain_reuse's handover runs prepare_next_stage as a standalone file on copied outputs; it writes the same
    tracked-demand files as the solve did."""
    from s0_workflow.chain_reuse import HANDOVER
    run, stages, outs = td_chain(tmp_path, "reuse", matching="hourly")
    names = ["tracked_demand_onsite_existing", "tracked_demand_storage_existing"]
    before = {n: (run / "in/2030/case" / f"{n}.chained.case.csv").read_bytes() for n in names}
    for n in names:
        (run / "in/2030/case" / f"{n}.chained.case.csv").unlink()
    r = subprocess.run([sys.executable, "-c", HANDOVER, str(run / "mods/prepare_next_stage.py"),
                        str(run / "in/2020/case"), str(outs[0])], capture_output=True, text=True, cwd=run)
    assert r.returncode == 0, r.stderr[-2000:]
    for n in names:
        assert (run / "in/2030/case" / f"{n}.chained.case.csv").read_bytes() == before[n]
