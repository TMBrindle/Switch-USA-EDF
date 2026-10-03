"""Coal specification rev. 2 (s0_workflow/specs/coal/coal_spec.md): cap units, caps by stage, fleet overrides,
holds, the PowerGenome hooks, the case-build checks, settings, and the 2045 load entries.
The committed public-EIA tables (s0_workflow/data/coal_*.csv) are checked against the spec's validation tables;
PowerGenome's unit table is a fixture built from those tables (PowerGenome's data is VM-only)."""
import ast
import copy
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_workflow import coal_fleet as cf  # noqa: E402
from s0_workflow import coal_spec as cs  # noqa: E402
from s0_workflow import production as s0prod  # noqa: E402

SPEC = cs.SPEC


def spec(name, **kw):
    return pd.read_csv(SPEC / name, **kw)


def s0_yaml():
    return yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]


def axis():
    return yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]


def cap_units():
    cu = cs.load_cap_units()
    return cu[cu.status.isin(s0_yaml()["coal_spec"]["cap_statuses"])]


HELD = cs.held_keys(cs.load_holds())


# ------------------------------------------------------------------------------------------------ §1.2 keys
def test_generator_id_normalisation_and_collisions():
    assert [cs.norm_gen(g) for g in ("0001", "001", "01", " 1 ", "0", "000", "ST4", "GEN5", "S01")] == \
        ["1", "1", "1", "1", "0", "0", "ST4", "GEN5", "S01"]
    assert cs.unit_key(3845.0, "02") == "3845|2"
    coal = {"10|1"}
    # a collision on a coal-group unit stops; one elsewhere (Equus Freeport's gas CTs "0001"/"001") doesn't
    with pytest.raises(ValueError, match="collide"):
        cs.check_collisions("f", [10, 10], ["01", "001"], coal)
    cs.check_collisions("f", [56032, 56032], ["0001", "001"], coal)
    cs.check_collisions("f", [10, 10], ["1", "1"], coal)                 # the same raw ID twice is no collision
    assert cs.norm_county("Saint Louis County") == cs.norm_county("St. Louis") == "stlouis"
    assert cs.norm_county("Lafourche Parish") == "lafourche" and cs.norm_county("Juneau City and Borough") == "juneau"


# ------------------------------------------------------------------------------------------------ §1.1-1.4 cap units
def test_cap_units_match_spec():
    cu, a = cs.load_cap_units(), spec("coal_cap_units_all.csv")
    assert set(cu.status) == {"OP", "SB", "OA"}
    x = cu[cu.status.isin(["OP", "SB"])].merge(a, on="kn", how="outer", indicator=True)
    assert (x._merge == "both").all() and len(x) == 427                         # same set as the spec
    assert (x.cf_max - x["max annual CF 2021-24"]).abs().max() <= 0.0005        # unit max +-0.0005
    assert (x.cf_max.isna() == x["max annual CF 2021-24"].isna()).all()
    assert (x.zone_x.fillna("") == x.zone_final.fillna("")).all()               # plant map, then county
    assert (x.winter_mw - x["winter MW (860M)"]).abs().max() < 1e-9
    assert set(cu.loc[cu.status == "OA", "kn"]) == {"10234|GEN1", "57915|5"}   # only OA units (§1.1 vs tables)
    l48 = cu[~cu["Plant State"].isin(["AK", "HI"])]
    assert l48.zone.notna().all() and (cu[cu.zone.isna()]["Plant State"] == "AK").all()
    adj = spec("coal_cap_units_adjudicated.csv")                              # the 2035 cap unit set
    s35 = cs.stage_units(x[x._merge == "both"].rename(columns={"Planned Retirement Year_x": "Planned Retirement Year"}),
                         2035, set())
    assert set(s35.kn) == set(adj.kn)


def test_place_zones_order():
    units = pd.DataFrame({"Plant ID": [1, 2, 3, 4], "Plant State": ["wa", "WA", "WA", "XX"],
                          "County": ["Chelan", "Chelan County", "Nowhere", "Nowhere"],
                          "Latitude": [np.nan, np.nan, 47.0, np.nan], "Longitude": [np.nan, np.nan, -120.0, np.nan]})
    c2z = pd.DataFrame({"state": ["WA"], "county_name": ["chelan"], "ba": ["p1"]})
    z = cs.place_zones(units, {1: "p9"}, c2z, latlon=lambda lat, lon: "p5")
    assert list(z.zone.fillna("-")) == ["p9", "p1", "p5", "-"]
    assert list(z.zone_source.fillna("-")) == ["plant map", "county", "lat/lon", "-"]


# ------------------------------------------------------------------------------------------------ §1.5 caps by stage
@pytest.mark.parametrize("stage", cs.STAGES)
def test_caps_by_stage_match_spec(stage):
    """H, own, N from the committed cap units; with the spec's model MW, the rule gives the spec's caps and labels."""
    e = spec("coal_spec_expected_caps_by_stage.csv")
    x = e[e.stage == stage].set_index("zone")
    hist, nat = cs.stage_history(cap_units(), stage, HELD)
    assert nat == pytest.approx(x.national_N.iloc[0], abs=5e-5)
    assert {2028: 0.5806, 2030: 0.5926, 2035: 0.5998, 2040: 0.6001, 2045: 0.6001}[stage] == pytest.approx(nat, abs=5e-5)
    r = cs.apply_rule(hist, x.model_MW_before, x.model_MW_after_overrides, nat)
    j = x.join(r, rsuffix="_c")
    assert len(r) == len(x)
    assert (j.expected_cap - j.expected_cap_c).abs().max() <= 0.001
    assert (j.rule == j.rule_c).all()
    assert (j.hist_MW.fillna(0) - j.hist_MW_c.fillna(0)).abs().max() <= 1.0
    assert (j.n_units.fillna(0) == j.n_units_c.fillna(0)).all()
    blend = r[r.rule == cs.RULE_BLEND]
    assert list(blend.index) == ["p111"]                                         # the blend zone in every stage
    assert blend.expected_cap.iloc[0] == pytest.approx(
        {2028: 0.531, 2030: 0.542, 2035: 0.548, 2040: 0.549, 2045: 0.549}[stage], abs=0.001)
    if stage == 2035:                                                            # the rev. 1 table
        x35 = spec("coal_spec_expected_caps.csv").set_index("zone")
        assert (x35.expected_cap - j.expected_cap_c.reindex(x35.index)).abs().max() <= 0.001


def test_held_units_out_of_every_stage_and_oa_effect():
    for p in cs.STAGES:
        assert not (set(cs.stage_units(cap_units(), p, HELD).kn) & HELD)
    # including the OA units (§1.1 text) moves p21 by +0.002: why the default follows the tables
    h_op, _ = cs.stage_history(cap_units(), 2035, HELD)
    h_oa, _ = cs.stage_history(cs.load_cap_units(), 2035, HELD)
    assert h_oa.at["p21", "own_cap"] - h_op.at["p21", "own_cap"] == pytest.approx(0.002, abs=0.0005)


def test_rule_cases_and_aggregation():
    hist = pd.DataFrame({"hist_MW": [1000.0, 50.0, 400.0], "n_units": [2, 1, 1], "own_cap": [0.6, 0.002, 0.8]},
                        index=pd.Index(["a", "b", "d"], name="zone"))
    m = pd.Series({"a": 1500.0, "b": 190.0, "c": 300.0, "d": 0.0})
    r = cs.apply_rule(hist, m, m, 0.58)
    assert r.at["a", "rule"] == cs.RULE_OWN and r.at["a", "expected_cap"] == pytest.approx(0.6)
    assert r.at["b", "rule"] == cs.RULE_BLEND and r.at["b", "expected_cap"] == pytest.approx((50 * 0.002 + 500 * 0.58) / 550)
    assert r.at["c", "rule"] == cs.RULE_NAT and r.at["c", "expected_cap"] == pytest.approx(0.58)
    assert r.at["d", "rule"] == cs.RULE_OWN + cs.NO_COAL
    ag = cs.aggregate_rule(r, {"ab": ["a", "b"], "c": ["c"]}, 0.58)
    assert ag.at["ab", "hist_MW"] == 1050 and ag.at["ab", "model_MW_after_overrides"] == 1690
    assert ag.at["ab", "expected_cap"] == pytest.approx((1000 * 0.6 + 50 * 0.002) / 1050)   # coverage 0.62: own
    assert cs.availability(0.6, 0.1) == pytest.approx(0.6 / 0.9) and cs.availability(0.95, 0.1) == 1.0


# ------------------------------------------------------------------------------------------------ §3 holds
def test_hold_table_and_cap_rule():
    h, s = cs.load_holds(), spec("coal_spec_hold_online.csv", dtype={"generator_id": str})
    s = s[s.in_S0 | s.in_sensitivity_holds_persist]
    m = h.merge(s, on=["plant_id_eia", "generator_id"])
    assert len(m) == len(h) == 10
    assert (m.hold_cap == m.hold_max_annual_CF).all()                           # cap exact
    assert (m.cf_since_order - m.CF_since_order).abs().max() <= 0.0005
    assert (m.in_S0_x == m.in_S0_y).all() and (m.in_holds_persist == m.in_sensitivity_holds_persist).all()
    s0 = h[h.in_S0]
    assert len(s0) == 8 and s0.winter_mw.sum() == pytest.approx(3238.3)
    assert (s0.S0_encoded_retirement_year == 2029).all() and (s0.S0_last_stage == 2028).all()
    assert h[h.in_holds_persist].winter_mw.sum() == pytest.approx(5038.3)
    assert set(h.loc[~h.in_S0, "kn"]) == {"6481|1", "6481|2"} and (h.loc[~h.in_S0, "hold_cap"] == 0.001).all()
    # the rule on monthly data: floor, no data, window extension
    p4 = pd.DataFrame({"kn": "1|1", "year": 2026, "month": range(1, 8), "mwh": [100.0, -500, 0, 0, 0, 0, 0]})
    assert cs.hold_cap(p4, "1|1", 100.0, "2026-01", "2026-07")["hold_cap"] == 0.001        # negative -> 0 -> floor
    assert cs.hold_cap(p4, "1|1", 100.0, "2026-06", "2026-07")["hold_cap"] == 0.01         # < 3 months
    r = cs.hold_cap(p4.assign(mwh=7200.0), "1|1", 100.0, "2026-06", "2026-07", fallback_start="2026-01")
    assert r["window"].startswith("2026-01") and r["months_with_data"] == 7


def test_holds_by_stage_check():
    e = spec("coal_spec_hold_by_stage.csv", dtype={"generator_id": str})
    h = cs.load_holds()
    for scen, enc in (("s0", 2029), ("holds_persist", None)):
        held = cf.scenario_holds({"coal_holds": {"scenario": scen}})
        final = {k: ("held", y if pd.notna(y) else None) for k, y in zip(held.kn, held.encoded_year)}
        hb = cf.holds_by_stage(final, h, cs.STAGES, scen, e)
        assert hb.ok.all() and len(hb) == 50
        assert set(hb[(hb.stage == 2030) & (hb.build == "held")].kn if "kn" in hb else []) == set()
        n2030 = (hb[hb.stage == 2030].build == "held").sum()
        assert n2030 == (0 if scen == "s0" else 10)
    # a hold that ran one stage too long is caught
    held = cf.scenario_holds({"coal_holds": {"scenario": "s0"}})
    final = {k: ("held", 2030) for k in held.kn}
    assert not cf.holds_by_stage(final, h, cs.STAGES, "s0", e).ok.all()


# ------------------------------------------------------------------------------------------------ §2 overrides
def model_basis():
    """PowerGenome's coal-group unit table before the overrides, as a fixture: the spec's model rows (model
    technology, zone, winter MW, model-basis retirement) and every other cap unit at its 860M planned date."""
    ov = spec("coal_spec_overrides.csv", dtype={"generator_id": str})
    op, _ = cf.load_fleet860m()
    rows = []
    for r in ov.itertuples():
        k = cs.unit_key(r.plant_id_eia, r.generator_id)
        opy = op["Operating Year"].get(k, 1980) if k in op.index else 1980
        rows.append(dict(plant_id_eia=r.plant_id_eia, generator_id=r.generator_id,
                         technology_description=r.model_technology if pd.notna(r.model_technology) else cs.CSC,
                         model_region=r.zone, winter_capacity_mw=r.model_winter_MW,
                         retirement_year=r.model_ret_year if pd.notna(r.model_ret_year) else opy + 500,
                         operating_date=opy, heat_rate_mmbtu_mwh=10.5, energy_source_code_1="BIT"))
    seen = set(cs.keys(ov.plant_id_eia, ov.generator_id))
    for _, r in cap_units().iterrows():
        if r.kn in seen or pd.isna(r.zone):
            continue
        pry, opy = r["Planned Retirement Year"], r["Operating Year"]
        rows.append(dict(plant_id_eia=r["Plant ID"], generator_id=r["Generator ID"], technology_description=cs.CSC,
                         model_region=r.zone, winter_capacity_mw=r.winter_mw,
                         retirement_year=pry if pd.notna(pry) else opy + 500, operating_date=opy,
                         heat_rate_mmbtu_mwh=10.5, energy_source_code_1="BIT"))
    gas = dict(plant_id_eia=55000, generator_id="CT1", technology_description="Natural Gas Fired Combustion Turbine",
               model_region="p1", winter_capacity_mw=100.0, retirement_year=2027, operating_date=2000,
               heat_rate_mmbtu_mwh=11.0, energy_source_code_1="NG")
    return pd.DataFrame(rows + [gas]).assign(retirement_age=500)


@pytest.mark.parametrize("scen,n", [("s0", 71), ("holds_persist", 73)])
def test_overrides_derived_from_latest_860m_match_spec(scen, n):
    s0 = {"coal_holds": {"scenario": scen}}
    op, rt = cf.load_fleet860m()
    ov, final = cf.derive_overrides(model_basis(), op, rt, cf.scenario_holds(s0))
    chk = cf.check_overrides(ov, spec("coal_spec_overrides.csv", dtype={"generator_id": str}), scen)
    assert len(ov) == n and (chk.status == "ok").all(), chk[chk.status != "ok"]
    acts = ov.action.map(cf._hold_class).value_counts()
    assert acts[cf.A_RETIRE] == 15 and acts[cf.A_LATER] == 9 and acts[cf.A_CONVERT] == 8 and acts[cf.A_REMOVE] == 5
    assert acts[cf.A_KEEP] == 5 and acts["hold online"] == (8 if scen == "s0" else 10)
    assert final["628|ST4"] == ("coal", 2034) and final["602|1"] == ("coal", 2029)       # Crystal River 4, Brandon Shores 1
    assert final["56611|S01"] == ("coal", 2026) and final["564|1"] == ("coal", None)      # Sandy Creek out; Stanton kept
    assert final["6193|2"] == ("gas", 2038) and final["8224|1"] == ("gas", None)          # Harrington 2, North Valmy 1
    assert final["1004|ST"] == ("coal", None) and final["1004|CT1"] == ("coal", None)     # Edwardsport unchanged
    assert final["3845|2"] == ("held", 2029 if scen == "s0" else None)
    assert ("6481|1" in final and final["6481|1"][0] == "held") == (scen == "holds_persist")
    # an extra disagreement is caught
    bad = ov.copy()
    bad.loc[bad.kn == "628|ST4", "effective_year"] = 2033
    assert (cf.check_overrides(bad, spec("coal_spec_overrides.csv", dtype={"generator_id": str}), scen).status == "differs").sum() == 1


def test_apply_overrides_unit_edits():
    s0 = {"coal_holds": {"scenario": "s0"}}
    op, rt = cf.load_fleet860m()
    u = model_basis()
    ov, final = cf.derive_overrides(u, op, rt, cf.scenario_holds(s0))
    e = cf.apply_overrides(u, final, ov).assign(kn=lambda d: cs.keys(d.plant_id_eia, d.generator_id)).set_index("kn")
    assert e.at["628|ST4", "retirement_year"] == 2034 and e.at["56611|S01", "retirement_year"] == 2026
    assert e.at["564|1", "retirement_year"] > cs.HORIZON                                   # keep online
    assert e.at["564|1", "retirement_year"] - 500 == e.at["564|1", "operating_date"]       # build year = operating year
    v = e.loc["2721|6"]
    assert v.technology_description == cf.GAS_STEAM and v.energy_source_code_1 == "NG" and v.winter_capacity_mw == 849
    assert e.at["6193|3", "retirement_year"] == 2040
    assert e.at["3845|2", "technology_description"] == cf.hold_tech(3845, "2") and e.at["3845|2", "retirement_year"] == 2029
    assert e.at["55000|CT1", "retirement_year"] == 2027                                    # other technologies untouched
    # model coal MW by stage: held and converted units out after; Brandon Shores (2029) in 2028, out in 2030
    b28, a28 = cf.model_mw(u, final, 2028)
    b30, a30 = cf.model_mw(u, final, 2030)
    assert a28["p123"] - a30.get("p123", 0) == pytest.approx(638 + 635)
    assert "p2" not in a28.index and a28.get("p97", 0) < b28.get("p97", 0) + 1e-9


def test_conversion_year_and_heat_rate():
    assert cf.cs.conversion_year(pd.Series({2020: "BIT", 2022: "NG", 2023: "NG", 2024: "BIT", 2025: "BIT", 2026: "NG"})) == 2026
    assert cs.conversion_year(pd.Series({2023: "SUB", 2024: "SUB", 2025: "NG", 2026: "NG"})) == 2025
    st = cs.read_table(REPO / "s0_workflow/data/coal_plant_st_fuel.csv")
    g = spec("coal_spec_converted_gas_units.csv", dtype={"generator_id": str})
    for r in g.drop_duplicates("plant_id_eia").itertuples():
        coal = st[(st.plant == r.plant_id_eia) & (st.fuel == "COAL") & (st.year == 2024)]
        coal_hr = coal.mmbtu.sum() / coal.mwh.sum()
        hr, src, flag = cs.plant_gas_heat_rate(st, r.plant_id_eia, coal_hr, through="2025-05")
        assert hr == pytest.approx(r.convert_heat_rate, abs=0.01), r.plant_name          # the spec table's vintage
        assert flag == r.convert_heat_rate_source.startswith("no gas")
        latest = cs.plant_gas_heat_rate(st, r.plant_id_eia, coal_hr)                     # S0 default: latest EIA-923
        assert "2026" in latest[1] and not latest[2]
    f, _ = cf.load_fleet860m()
    g["kn"] = cs.keys(g.plant_id_eia, g.generator_id)
    assert (g.kn.map(f.conversion_year) == g.conversion_year).all()


# ------------------------------------------------------------------------------------------------ settings and hooks
def s0_case(axis_value="on", holds="s0"):
    s = {"s0_production": copy.deepcopy(s0_yaml()), "case_id": "c", "model_year": 2028, "capacity_col": "winter_capacity_mw",
         "num_clusters": {cs.CSC: 2, "Other_peaker": 1}, "tech_fuel_map": {cs.CSC: "coal"},
         "eia_atb_tech_map": {cs.CSC: "Coal_newAvgCF"}, "tech_groups": {cs.CSC: [cs.CSC, cs.IGCC, cs.PETCOKE]},
         "existing_startup_costs_tech_map": {cs.CSC: "coal_large_sub"}}
    s = s0prod.deep_merge(s, axis()["s0_production"][axis_value])
    return s0prod.deep_merge(s, axis()["coal_holds"][holds])


def test_settings_axis_column_and_legacy_off():
    s0 = s0_yaml()
    assert s0["coal_spec"]["enabled"] and s0["coal_spec"]["cap_statuses"] == ["OP", "SB"]
    assert s0["coal_holds"] == {"enabled": True, "scenario": "s0", "table": "s0_workflow/data/coal_holds.csv"}
    ax = axis()
    assert ax["coal_holds"] == {"s0": {"s0_production": {"coal_holds": {"scenario": "s0"}}},
                                "holds_persist": {"s0_production": {"coal_holds": {"scenario": "holds_persist"}}}}
    leg = ax["s0_production"]["on_pgdays"]["s0_production"]
    assert leg["coal_spec"] == {"enabled": False} and leg["coal_holds"] == {"enabled": False}
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    assert si.columns[-1] == "coal_holds" and (si.coal_holds == "s0").all()
    # the legacy case: no hold technologies, no override exemption, legacy coal caps; new defaults: on
    leg_s = s0_case("on_pgdays")
    before = copy.deepcopy(leg_s)
    s0prod.apply_settings({"c": {2035: leg_s}})
    assert "predetermined_retirement_override_exempt" not in leg_s and leg_s["num_clusters"] == before["num_clusters"]
    for ax_value in ("on", "on_windows", "on_pgdays_new"):
        s = s0_case(ax_value)
        s0prod.apply_settings({"c": {2028: s}})
        t = cf.hold_tech(3845, "2")
        assert s["num_clusters"][t] == 1 and s["tech_fuel_map"][t] == "coal" and s["eia_atb_tech_map"][t] == "Coal_newAvgCF"
        assert t not in s["tech_groups"] and s["predetermined_retirement_override_exempt"] == ["coal"]
        assert sum(k.startswith(cf.HOLD_TECH) for k in s["num_clusters"]) == 8
    p = s0_case("on", "holds_persist")
    s0prod.apply_settings({"c": {2028: p}})
    assert sum(k.startswith(cf.HOLD_TECH) for k in p["num_clusters"]) == 10
    with pytest.raises(ValueError, match="holds_persist"):
        cf.hold_scenario({"coal_holds": {"scenario": "forever"}})


def test_unit_hooks_wrap_powergenome_and_restore():
    """The hooks act through PowerGenome's module attributes, as create_region_technology_clusters calls them,
    and restore them afterwards (a stand-in module: PowerGenome's data is VM-only)."""
    calls = {}

    def group_technologies(df, *a, **k):
        calls["group"] = df.copy()
        return df

    def atb_fixed_var_om_existing(units, *a, **k):
        calls["om"] = units.copy()
        return units

    fake = types.ModuleType("powergenome.generators")
    fake.group_technologies, fake.atb_fixed_var_om_existing = group_technologies, atb_fixed_var_om_existing
    pkg = types.ModuleType("powergenome")
    pkg.generators = fake
    saved = {k: sys.modules.get(k) for k in ("powergenome", "powergenome.generators")}
    sys.modules["powergenome"], sys.modules["powergenome.generators"] = pkg, fake
    try:
        s = s0_case("on")
        s0prod.apply_settings({"c": {2028: s}})
        with cf.unit_hooks(s) as rec:
            u = model_basis()
            fake.group_technologies(u, {})                                     # as PowerGenome calls it
            um = calls["group"].rename(columns={"technology_description": "technology"}).set_index(
                ["plant_id_eia", "energy_source_code_1"])
            fake.atb_fixed_var_om_existing(um)
        assert fake.group_technologies is group_technologies and fake.atb_fixed_var_om_existing is atb_fixed_var_om_existing
        g = calls["group"].assign(kn=lambda d: cs.keys(d.plant_id_eia, d.generator_id)).set_index("kn")
        assert g.at["2721|6", "technology_description"] == cf.GAS_STEAM
        assert g.at["1710|3", "technology_description"] == cf.hold_tech(1710, "3")
        o = calls["om"].reset_index().assign(kn=lambda d: cs.keys(d.plant_id_eia, d.generator_id)).set_index("kn")
        assert o.at["2721|6", "heat_rate_mmbtu_mwh"] != 10.5 and o.at["628|ST4", "heat_rate_mmbtu_mwh"] == 10.5
        assert rec is cf.state("c", 2028) and len(rec["overrides"]) == 71
        hr = rec["heat_rates"]
        assert set(hr) == {"8224|1", "8224|2", "3149|1", "6248|1", "6193|2", "6193|3", "2721|5", "2721|6"}
        # other cases: no hooks
        with cf.unit_hooks(s0_case("on_pgdays")) as none:
            assert none is None and fake.group_technologies is group_technologies
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _pg_fn(name):
    src = (REPO / "pg_to_switch.py").read_text()
    tree = ast.parse(src)
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == name)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "PREDETERMINED_RETIREMENT_OVERRIDE_KEYS"
                                                    for t in n.targets))]
    g = {"pd": pd, "logger": __import__("logging").getLogger()}
    exec(compile(ast.Module(nodes, []), "pg_to_switch", "exec"), g)
    return g[name], src


def test_pg_to_switch_hooks_and_override_exemption():
    fn, src = _pg_fn("apply_predetermined_retirement_override")
    units = pd.DataFrame({"technology": ["Conventional Steam Coal", f"{cf.HOLD_TECH} 3845 2",
                                         "Natural Gas Fired Combined Cycle", "Conventional Steam Coal"],
                          "retirement_year": [2027, 2029, 2028, 2031]})
    rule = {"technologies": ["coal", "natural gas"], "window": [2026, 2029], "target_year": 2030}
    a = fn(units.copy(), {"predetermined_retirement_override": rule})
    assert list(a.retirement_year) == [2030, 2030, 2030, 2031]                      # legacy behaviour
    b = fn(units.copy(), {"predetermined_retirement_override": rule, "predetermined_retirement_override_exempt": ["coal"]})
    assert list(b.retirement_year) == [2027, 2029, 2030, 2031]                      # coal keeps the spec's years
    i = src.index("with s0coal.unit_hooks(year_settings):")
    assert "gc.create_all_generators()" in src[i:i + 120]


# ------------------------------------------------------------------------------------------------ case build
def _expected_frame(stages):
    """A coal unit table whose model MW after the overrides equals the spec's in every stage and zone."""
    e = spec("coal_spec_expected_caps_by_stage.csv")
    m = e.pivot_table(index="zone", columns="stage", values="model_MW_after_overrides").fillna(0)
    rows, n = [], 0
    for z, r in m.iterrows():
        prev = None
        for i, p in enumerate(cs.STAGES):
            nxt = r[cs.STAGES[i + 1]] if i + 1 < len(cs.STAGES) else 0.0
            mw = r[p] - nxt
            if mw > 1e-6:
                n += 1
                ret = cs.STAGES[i + 1] - 1 if i + 1 < len(cs.STAGES) else 2070
                rows.append(dict(plant_id_eia=900000 + n, generator_id="1", technology_description=cs.CSC,
                                 model_region=z, winter_capacity_mw=mw, retirement_year=ret, operating_date=1980))
    u = pd.DataFrame(rows).assign(retirement_age=500)
    final = {cs.unit_key(p, g): ("coal", cf._eff(y)) for p, g, y in zip(u.plant_id_eia, u.generator_id, u.retirement_year)}
    return u, final


def _case_folder(folder, zones):
    rows = [dict(GENERATION_PROJECT=f"{z}_conventional_steam_coal_1", gen_tech=cs.CSC, gen_load_zone=z,
                 gen_energy_source="coal", gen_forced_outage_rate="0.1", gen_can_retire_early="1") for z in zones]
    rows += [dict(GENERATION_PROJECT="p103_conventional_steam_coal_hold_1710_3_1", gen_tech=cf.hold_tech(1710, "3"),
                  gen_load_zone="p103", gen_energy_source="coal", gen_forced_outage_rate="0.1", gen_can_retire_early="1"),
             dict(GENERATION_PROJECT="p97_other_peaker_1", gen_tech="Other_peaker", gen_load_zone="p97",
                  gen_energy_source="naturalgas", gen_forced_outage_rate="0.05", gen_can_retire_early="1")]
    pd.DataFrame(rows).to_csv(folder / "gen_info.csv", index=False)


def test_case_build_caps_holds_and_checks(tmp_path):
    years = [2028, 2030]
    u, final = _expected_frame(years)
    op, rt = cf.load_fleet860m()
    s = s0_case("on_windows")
    s0prod.apply_settings({"c": {2028: s}})
    s0 = s["s0_production"]
    held = cf.scenario_holds(s0)
    basis = model_basis()
    ov, f2 = cf.derive_overrides(basis, op, rt, held)
    final.update({k: v for k, v in f2.items() if v[0] == "held"})
    for y in years:
        cf._State.store[("c", y)] = {"case": "c", "year": y, "units": u, "final": final, "overrides": ov}
    zones = ["p101", "p111", "p21", "p53"]
    _case_folder(tmp_path, zones)
    log = s0prod.Log(tmp_path)
    cf.write_case_inputs(tmp_path, s0, {y: dict(s, model_year=y) for y in years}, log)
    t = pd.read_csv(tmp_path / "coal_caps_by_stage.csv")
    assert t.ok.all() and set(t.stage) == set(years)
    gi = pd.read_csv(tmp_path / "gen_info.csv", na_values=".").set_index("GENERATION_PROJECT")
    bp = pd.read_csv(tmp_path / "gen_max_annual_availability_by_period.csv").set_index(["GENERATION_PROJECT", "PERIOD"])
    e = spec("coal_spec_expected_caps_by_stage.csv").set_index(["stage", "zone"]).expected_cap
    for z in zones:
        for y in years:
            assert bp.loc[(f"{z}_conventional_steam_coal_1", y)].iloc[0] == pytest.approx(min(1, e[(y, z)] / 0.9), abs=1e-3)
        assert gi.at[f"{z}_conventional_steam_coal_1", "gen_max_annual_availability"] == pytest.approx(
            min(1, e[(2028, z)] / 0.9), abs=1e-3)
    h = "p103_conventional_steam_coal_hold_1710_3_1"
    assert gi.at[h, "gen_max_annual_availability"] == pytest.approx(0.5539 / 0.9, abs=1e-6)
    assert gi.at[h, "gen_can_retire_early"] == 0                                     # held: no economic retirement
    assert pd.isna(gi.at["p97_other_peaker_1", "gen_max_annual_availability"])      # converted gas: no coal cap
    assert (pd.read_csv(tmp_path / "coal_overrides_applied.csv").status == "ok").all()
    hb = pd.read_csv(tmp_path / "coal_holds_by_stage.csv")
    assert hb.ok.all() and set(hb[hb.build == "held"].stage) == {2028}
    txt = (tmp_path / "s0_production_log.txt").read_text() if (tmp_path / "s0_production_log.txt").exists() else "\n".join(log.lines)
    assert "coal caps 2028: N 0.5806" in txt and "71/71 as coal_spec_overrides.csv" in txt
    # a cap that misses the spec stops the build, after writing the check files
    u2 = u.copy()
    u2.loc[u2.model_region == "p111", "winter_capacity_mw"] *= 0.1                  # p111: blend -> own history
    for y in years:
        cf._State.store[("c", y)]["units"] = u2
    _case_folder(tmp_path, zones)
    with pytest.raises(ValueError, match="p111"):
        cf.write_case_inputs(tmp_path, s0, {y: dict(s, model_year=y) for y in years}, s0prod.Log(tmp_path))
    with pytest.raises(RuntimeError, match="unit hook"):
        cf.write_case_inputs(tmp_path, s0, {2035: dict(s, model_year=2035, case_id="other")}, s0prod.Log(tmp_path))


def test_annual_availability_by_period_on_toy(tmp_path):
    """gen_max_annual_availability_by_period.csv caps a generator's annual output per period."""
    from toyutil import solve, toy_inputs
    gen = "N-Coal_ST"

    def edit(inp):
        pd.DataFrame({"GENERATION_PROJECT": [gen, gen], "PERIOD": [2020, 2030],
                      "gen_max_annual_availability_by_period": [0.05, 0.3]}).to_csv(
            inp / "gen_max_annual_availability_by_period.csv", index=False)
    run = toy_inputs(tmp_path, "cap", ["gen_annual_availability_limits"], edit)
    d = pd.read_csv(solve(run) / "dispatch.csv")
    free = pd.read_csv(solve(toy_inputs(tmp_path, "free", ["gen_annual_availability_limits"])) / "dispatch.csv")
    cf_ = lambda df, p: (lambda y: (y.DispatchGen_MW * y.tp_weight_in_year_hrs).sum()  # noqa: E731
                         / (y.GenCapacity_MW * y.tp_weight_in_year_hrs).sum())(
        df[(df.generation_project == gen) & (df.period == p)])
    assert cf_(free, 2020) > 0.06                                   # the cap binds in 2020 ...
    assert cf_(d, 2020) <= 0.05 + 1e-6 and cf_(d, 2030) <= 0.3 + 1e-6
    assert cf_(d, 2030) > 0.05 + 1e-3                               # ... and 2030 has its own, looser value


# ------------------------------------------------------------------------------------------------ 2045 load, notes
def test_2045_load_entries_and_notes_removed():
    md = yaml.safe_load(open(REPO / "pg/settings/model_definition.yml"))
    sm = axis()["load_growth"]
    fl = yaml.safe_load(open(REPO / "pg/settings/flexible_load.yml"))["flexible_demand_resources"]
    for y in (2028, 2030, 2035, 2040, 2045):
        assert y in md["model_year"]
        assert y in fl and fl[y]["us_exports"]["fraction_shiftable"] == 0.0
        for v in ("edf_epri_med", "epri_high"):
            assert sm[v]["flexible_demand_resources"][y]["load_growth"] == {
                "fraction_shiftable": 1.0, "parameter_values": {"New_Build": -1}}
    assert sm["epri_high"]["demand_response_fn"] == "load_adjustments_epri_high.csv.zip"
    txt = (REPO / "pg/settings/scenario_management.yml").read_text()
    for gone in ("STATE CHECK", "THIS WORKTREE", "per repo-owner confirmation", "outside this worktree"):
        assert gone not in txt
    doc = (REPO / "Guides and documentation/load_growth.md").read_text()
    assert "load_adjustments_epri_high.csv.zip" in doc and "Follow-up" in doc
