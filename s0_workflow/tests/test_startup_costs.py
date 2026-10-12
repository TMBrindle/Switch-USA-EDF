"""V32-01 start-up costs (CHANGES §119; s0_workflow/startup_costs.py). The audit runs on the repo's own settings files;
the build check on the committed s4x1 fedpol inputs (switch/in/foresight/s4x1_fedpol_current) and hand-edited copies."""
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import production as s0prod, startup_costs as sc  # noqa: E402

FEDPOL = REPO / "switch/in/foresight/s4x1_fedpol_current"
CT = "NaturalGas_Combustion Turbine (F-Frame)_Moderate"
CC = "NaturalGas_1-on-1 Combined Cycle (H-Frame)_Moderate"


def _techs():
    res, sm = sc.load_yml("resources.yml"), sc.load_yml("scenario_management.yml")
    return sorted(set(sc.known_technologies(res)) | set(sc.scenario_technologies(sm)))


def test_audit_v31_keys_miss_the_atb_gas_names():
    v31 = sc.load_yml("startup_costs.yml")["new_build_startup_costs"]
    a = sc.audit(v31, [CT, CC]).set_index("technology")
    assert (a.n_keys == 0).all()                                  # the bug: new CT and CC get no start-up cost class
    a = sc.audit(sc.fixed_keys(v31), [CT, CC]).set_index("technology")
    assert a.at[CT, "class"] == "gas_large_ct" and a.at[CC, "class"] == "gas_cc"


def test_audit_every_buildable_fossil_and_nuclear_name():
    """Fixed keys: every NaturalGas_* technology the settings name, and every coal / nuclear technology an axis or
    atb_new_gen builds, matches exactly one key (no gap, nothing ambiguous; CCS variants included, no hydrogen
    technology exists to catch). The v3.1 keys that match nothing are listed (stale, harmless)."""
    keys = sc.fixed_keys(sc.load_yml("startup_costs.yml")["new_build_startup_costs"])
    techs = _techs()
    a = sc.audit(keys, techs).set_index("technology")
    gas = a[a.index.str.startswith("NaturalGas_")]
    assert len(gas) >= 9 and (gas.n_keys == 1).all(), gas[gas.n_keys != 1]
    built = {f"{e[0]}_{e[1]}" for e in sc.load_yml("resources.yml")["atb_new_gen"]} | set(
        sc.scenario_technologies(sc.load_yml("scenario_management.yml")))
    fossil_nuc = [t for t in built if t.split("_")[0] in ("NaturalGas", "Coal", "Nuclear")]
    assert all(a.at[t, "n_keys"] == 1 for t in fossil_nuc), {t: a.at[t, "keys"] for t in fossil_nuc}
    assert "Coal_95%-CCS" in fossil_nuc and a.at["Coal_95%-CCS", "class"] == "coal_supercritical"
    assert not any("hydrogen" in t.lower() or "h2" in t.lower() for t in techs)
    assert set(sc.unmatched_keys(keys, techs)) == {"Coal_CCS90", "Coal_IGCC", "NaturalGas_CCS100"}


def test_flag_off_by_default_and_on_adds_keys():
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["startup_cost_fix"] == {"enabled": True}                                       # v3.2 ON (§126)
    s = {"new_build_startup_costs": dict(sc.load_yml("startup_costs.yml")["new_build_startup_costs"])}
    before = dict(s["new_build_startup_costs"])
    assert sc.apply_settings(s, {"startup_cost_fix": {"enabled": False}}) == []
    assert s["new_build_startup_costs"] == before                                            # off: v3.1
    assert sc.apply_settings(s, {}) == []                                                    # code default: off
    added = sc.apply_settings(s, {"startup_cost_fix": {"enabled": True}})
    assert set(added) == set(sc.FIXED_KEYS) and {**before, **sc.FIXED_KEYS} == s["new_build_startup_costs"]


def test_build_check_on_the_fedpol_inputs(tmp_path):
    if not (FEDPOL / "gen_info.csv").exists():
        pytest.skip("committed fedpol inputs not present")
    d = tmp_path / "case"
    d.mkdir()
    shutil.copy(FEDPOL / "gen_info.csv", d / "gen_info.csv")
    bad = sc.check_new_gas(d)
    assert set(bad.gen_tech) == {CT, CC} and len(bad) == 268                    # 134 zones x CT, CC: all zero
    logs = []
    sc.write_check(d, {}, logs.append)                                         # off: reported, not fatal
    assert "268 new gas projects have no start-up cost" in logs[0]
    with pytest.raises(ValueError, match="still have gen_startup_om 0"):
        sc.write_check(d, {"startup_cost_fix": {"enabled": True}}, logs.append)
    gi = pd.read_csv(d / "gen_info.csv", na_values=".")
    gi.loc[gi.gen_tech == CT, "gen_startup_om"] = 145.0                        # as the fix would give
    gi.loc[gi.gen_tech == CC, "gen_startup_om"] = 112.0
    gi.to_csv(d / "gen_info.csv", index=False, na_rep=".")
    sc.write_check(d, {"startup_cost_fix": {"enabled": True}}, logs.append)
    assert sc.check_new_gas(d).empty and "NaturalGas_Combustion Turbine (F-Frame)_Moderate 145" in logs[-1]


def test_wired_into_s0_settings():
    import inspect
    src = inspect.getsource(s0prod.apply_settings) + inspect.getsource(s0prod.write_case_inputs)
    assert "startup_costs.apply_settings(s, s0)" in src and "startup_costs.write_check(out_folder, s0, log)" in src
