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
# the spec's out-of-service removals: Sandy Creek S01, Big Cajun 2-1, Merrimack 2, Warrick 2, Biron Mill GEN5
REMOVALS = ["56611|S01", "6055|1", "2364|2", "6705|2", "10234|GEN5"]


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
    h = cs.load_holds()
    for opt in cs.RETIREMENT_OPTIONS:
        e = pd.read_csv(cs.SPEC / f"by_option/coal_spec_hold_by_stage.{opt}.csv", dtype={"generator_id": str})
        for scen in ("s0", "holds_persist"):
            held = cf.scenario_holds({"coal_holds": {"scenario": scen}})
            y = {k: (None if pd.isna(v) else cs.pushed_year(int(v), cs.BLOCK_RULE if opt == "block_all" else None))
                 for k, v in zip(held.kn, held.encoded_year)}
            hb = cf.holds_by_stage(y, h, cs.STAGES, scen, e)
            assert hb.ok.all() and len(hb) == 50
            n2030 = (hb[hb.stage == 2030].build == "held").sum()
            assert n2030 == (10 if scen == "holds_persist" else 8 if opt == "block_all" else 0)
            assert (hb[(hb.stage == 2035) & (hb.build == "held")].scenario == "holds_persist").all()
    # rev. 2 table = planned_only and unrestricted; a hold that ran one stage too long is caught
    rev2 = spec("coal_spec_hold_by_stage.csv", dtype={"generator_id": str})
    held = cf.scenario_holds({"coal_holds": {"scenario": "s0"}})
    assert cf.holds_by_stage(dict(zip(held.kn, [2029] * 8)), h, cs.STAGES, "s0", rev2).ok.all()
    assert not cf.holds_by_stage(dict(zip(held.kn, [2030] * 8)), h, cs.STAGES, "s0", rev2).ok.all()


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


def test_overrides_already_satisfied_and_unmapped():
    """VM 88e6b30 (Tom 2026-10-04): where PowerGenome's fleet already has what the spec's override wants, the build
    derives no override and the check passes as "already satisfied" (Brandon Shores 1 / 2 dated 2029, Stanton 1 with
    no date), not "only in spec"; a unit whose plant isn't in reeds_plant_map.csv passes as not in the model; any other
    spec row the build lacks still fails."""
    op, rt = cf.load_fleet860m()
    holds = cf.scenario_holds({"coal_holds": {"scenario": "s0"}})
    e = spec("coal_spec_overrides.csv", dtype={"generator_id": str})
    b = cs.load_model_basis()
    k = pd.Series(cs.keys(b.plant_id_eia, b.generator_id), index=b.index)
    b.loc[k.isin(["602|1", "602|2"]), "retirement_year"] = 2029                     # as PowerGenome's fleet has them
    b.loc[k == "564|1", "retirement_year"] = b.loc[k == "564|1", "operating_date"] + 500
    ov, _ = cf.derive_overrides(b, op, rt, holds)
    assert not set(ov.kn) & {"602|1", "602|2", "564|1"}                              # no-ops: nothing derived
    chk = cf.check_overrides(ov, e, "s0", absent={"10234|GEN5"}, units=b).set_index("kn")
    assert chk.ok.all(), chk[~chk.ok]
    assert chk.at["602|1", "status"] == "ok (already satisfied: model 2029)" == chk.at["602|2", "status"]
    assert chk.at["564|1", "status"] == "ok (already satisfied: model no date)"
    assert (chk.status == cs.NOT_IN_MODEL).sum() == 12 and chk.at["57953|ST", "status"] == cs.NOT_IN_MODEL
    assert (cf.check_overrides(ov, e, "s0", absent={"10234|GEN5"}).status == "only in spec").sum() == 3   # no units
    b.loc[k == "602|1", "retirement_year"] = 2030                                    # not what the spec wants: fails
    ov2, _ = cf.derive_overrides(b, op, rt, holds)
    c2 = cf.check_overrides(ov2, e, "s0", absent={"10234|GEN5"}, units=b).set_index("kn")
    assert not c2.at["602|1", "ok"] and c2.at["602|1", "status"] == "differs"


def test_apply_overrides_unit_edits():
    s0 = {"coal_holds": {"scenario": "s0"}}
    op, rt = cf.load_fleet860m()
    u = model_basis()
    ov, final = cf.derive_overrides(u, op, rt, cf.scenario_holds(s0))
    edited = cf.apply_overrides(u, final, ov)
    e = edited.assign(kn=lambda d: cs.keys(d.plant_id_eia, d.generator_id)).set_index("kn")
    assert e.at["628|ST4", "retirement_year"] == 2034 and "56611|S01" not in e.index        # Sandy Creek: deleted
    assert e.at["564|1", "retirement_year"] > cs.HORIZON                                   # keep online
    assert e.at["564|1", "retirement_year"] - 500 == e.at["564|1", "operating_date"]       # build year = operating year
    v = e.loc["2721|6"]
    assert v.technology_description == cf.GAS_STEAM and v.energy_source_code_1 == "NG" and v.winter_capacity_mw == 849
    assert e.at["6193|3", "retirement_year"] == 2040
    assert e.at["3845|2", "technology_description"] == cf.hold_tech(3845, "2") and e.at["3845|2", "retirement_year"] == 2029
    assert e.at["55000|CT1", "retirement_year"] == 2027                                    # other technologies untouched
    # model coal MW by stage: held and converted units out after; Brandon Shores (2029) in 2028, out in 2030
    b28, a28 = cf.model_mw(u, edited, 2028)
    b30, a30 = cf.model_mw(u, edited, 2030)
    assert a28["p123"] - a30.get("p123", 0) == pytest.approx(638 + 635)
    assert "p2" not in a28.index and a28.get("p97", 0) < b28.get("p97", 0) + 1e-9
    # block_all: coal and held units dated 2026-29 -> 2031 (through the 2030 stage); gas and later years untouched
    pb = cf.apply_overrides(u, final, ov, rule=cs.BLOCK_RULE).assign(
        kn=lambda d: cs.keys(d.plant_id_eia, d.generator_id)).set_index("kn")
    assert pb.at["602|1", "retirement_year"] == 2031 and pb.at["2364|1", "retirement_year"] == 2031   # Brandon, Merrimack 1
    assert not pb.index.isin(REMOVALS).any() and not e.index.isin(REMOVALS).any()          # the OS removals: deleted
    assert cf.removal_keys(ov) == set(REMOVALS)
    a28b = cf.model_mw(u, pb.reset_index(), 2028, rule=cs.BLOCK_RULE)[1]
    assert a28b.get("p130", 0) == pytest.approx(108.0)                                      # Merrimack 1 only
    assert pb.at["3845|2", "retirement_year"] == 2031 and cf.hold_years(pb.reset_index())["3845|2"] == 2031
    assert pb.at["628|ST4", "retirement_year"] == 2034 and pb.at["470|3", "retirement_year"] == 2030
    assert pb.at["55000|CT1", "retirement_year"] == 2027                                   # gas: fedpol's own push
    b30b, a30b = cf.model_mw(u, pb.reset_index(), 2030, rule=cs.BLOCK_RULE)
    assert a30b["p123"] == pytest.approx(638 + 635) and cf.model_mw(u, pb.reset_index(), 2035)[1].get("p123", 0) == 0


def test_pushed_year_semantics():
    """block_all keeps a unit dated 2026-29 in the 2028 and 2030 stages and drops it from the 2035 stage: Switch
    (--retire early) runs a unit with retirement year Y in a period iff Y >= its end; PowerGenome counts it in model
    year M iff Y > M; the encoded 2031 satisfies both through 2030 and neither from 2035."""
    r = cs.BLOCK_RULE
    assert [cs.pushed_year(y, r) for y in (2025, 2026, 2029, 2030, 2034)] == [2025, 2031, 2031, 2030, 2034]
    assert cs.pushed_year(2027, None) == 2027 and cs.pushed_year(None, r) is None
    for y in (2026, 2027, 2028, 2029):
        Y = cs.pushed_year(y, r)
        assert [Y >= p for p in cs.STAGES] == [True, True, False, False, False]          # Switch
        assert [Y > m for m in cs.STAGES] == [True, True, False, False, False]           # PowerGenome
    sm = axis()["retirement_policy"]["blocked_2030_coal_gas"]["predetermined_retirement_override"]
    assert {k: sm[k] for k in ("technologies", "window", "target_year")} == cs.BLOCK_RULE   # fedpol's rule


def test_conversion_year_and_heat_rate():
    assert cs.conversion_year(pd.Series({2020: "BIT", 2022: "NG", 2023: "NG", 2024: "BIT", 2025: "BIT", 2026: "NG"})) == 2026
    assert cs.conversion_year(pd.Series({2023: "SUB", 2024: "SUB", 2025: "NG", 2026: "NG"})) == 2025
    st = cs.read_table(REPO / "s0_workflow/data/coal_plant_st_fuel.csv")
    g = spec("coal_spec_converted_gas_units.csv", dtype={"generator_id": str})
    rev2 = {8224: 13.978, 3149: 10.123, 6248: 12.969, 6193: 11.104, 2721: 9.798}     # rev. 2 (PUDL through 2025-05)
    latest = {8224: 11.416, 3149: 10.109, 6248: 10.962, 6193: 10.833, 2721: 10.552}  # rev. 2.1 (EIA-923 to 2026-07)
    for r in g.drop_duplicates("plant_id_eia").itertuples():
        coal = st[(st.plant == r.plant_id_eia) & (st.fuel == "COAL") & (st.year == 2024)]
        coal_hr = coal.mmbtu.sum() / coal.mwh.sum()
        hr, src, flag = cs.plant_gas_heat_rate(st, r.plant_id_eia, coal_hr)              # S0: latest EIA-923
        assert hr == pytest.approx(r.convert_heat_rate, abs=0.01) == latest[r.plant_id_eia], r.plant_name
        assert "2026" in src and not flag and r.convert_heat_rate_source == src         # the committed table (rev. 2.1)
        old = cs.plant_gas_heat_rate(st, r.plant_id_eia, coal_hr, through="2025-05")
        assert old[0] == pytest.approx(rev2[r.plant_id_eia], abs=0.001) and old[2] == (r.plant_id_eia == 8224)
    o = spec("coal_spec_overrides.csv", dtype={"generator_id": str})
    o = o[o.action == "convert to gas"]
    assert dict(zip(cs.keys(o.plant_id_eia, o.generator_id), o.convert_heat_rate)) == \
        dict(zip(cs.keys(g.plant_id_eia, g.generator_id), g.convert_heat_rate))
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
    assert list(si.columns[-10:]) == ["coal_holds", "retirements_pre2030", "forced_tx", "prm_design", "time_sample",
                                      "tx_bill", "tx_sens", "ca_wa_price", "retirement_sens", "fuel_prices"] and (si.coal_holds == "s0").all()
    assert set(si.loc[si.case_id == "s4x1_S0prod_2035", "retirements_pre2030"]) == {"legacy"}
    assert (si.loc[si.case_id != "s4x1_S0prod_2035", "retirements_pre2030"] == "block_all").all()
    assert ax["retirements_pre2030"] == {o: {"s0_production": {"retirements_pre2030": o}} for o in cs.RETIREMENT_OPTIONS} \
        | {"legacy": None}
    assert s0["retirements_pre2030"] == "block_all"
    assert ax["s0_production"]["on_pgdays"]["s0_production"]["retirements_pre2030"] == "legacy"
    # the legacy case: no hold technologies, no override exemption, legacy coal caps; new defaults: on
    leg_s = s0_case("on_pgdays")
    before = copy.deepcopy(leg_s)
    s0prod.apply_settings({"c": {2035: leg_s}})
    assert leg_s["num_clusters"] == before["num_clusters"]                              # legacy: no hold technologies
    assert "predetermined_retirement_override" not in leg_s                              # nor a push set by S0
    for ax_value in ("on", "on_windows", "on_pgdays_new"):
        s = s0_case(ax_value)
        s0prod.apply_settings({"c": {2028: s}})
        t = cf.hold_tech(3845, "2")
        assert s["num_clusters"][t] == 1 and s["tech_fuel_map"][t] == "coal" and s["eia_atb_tech_map"][t] == "Coal_newAvgCF"
        assert t not in s["tech_groups"] and "predetermined_retirement_override_exempt" not in s
        assert s["predetermined_retirement_override"] == cs.BLOCK_RULE                    # block_all (S0 default)
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


def test_block_all_pushes_gas_to_2031_in_the_hook():
    """S0 new-defaults cases with block_all: gas units whose cluster technology (after PowerGenome's grouping) matches
    fedpol's rule are encoded 2031 in the hook, like coal, so an all-pushed gas cluster stays in the 2030 stage; gas
    grouped into Other_peaker is left alone (as fedpol); OS removals aren't pushed; planned_only, unrestricted and
    the legacy case push nothing in the hook."""
    tech_groups = {cs.CSC: list(cs.COAL_GROUP),
                   "Other_peaker": ["Natural Gas Internal Combustion Engine", "Natural Gas Steam Turbine"]}

    def group_technologies(df, groups=None, *a, **k):                      # PowerGenome's grouping rule
        df["_t"] = df["technology_description"]
        for tech, members in (groups or {}).items():
            df.loc[df["technology_description"].isin(members), "_t"] = tech
        df["technology_description"] = df.pop("_t")
        return df

    fake = types.ModuleType("powergenome.generators")
    fake.group_technologies, fake.atb_fixed_var_om_existing = group_technologies, lambda u, *a, **k: u
    pkg = types.ModuleType("powergenome")
    pkg.generators = fake
    saved = {k: sys.modules.get(k) for k in ("powergenome", "powergenome.generators")}
    sys.modules["powergenome"], sys.modules["powergenome.generators"] = pkg, fake
    gas = pd.DataFrame({"plant_id_eia": [55001, 55002, 55003, 55004, 55005], "generator_id": ["CC1", "CT1", "ST1", "IC1", "CT2"],
                        "technology_description": ["Natural Gas Fired Combined Cycle", "Natural Gas Fired Combustion Turbine",
                                                   "Natural Gas Steam Turbine", "Natural Gas Internal Combustion Engine",
                                                   "Natural Gas Fired Combustion Turbine"],
                        "model_region": "p1", "winter_capacity_mw": 100.0, "retirement_year": [2027, 2029, 2027, 2028, 2033],
                        "operating_date": 1990, "retirement_age": 500, "heat_rate_mmbtu_mwh": 10.0, "energy_source_code_1": "NG"})
    try:
        out = {}
        for opt in ("block_all", "planned_only", "unrestricted"):
            s = s0_case("on")
            s["s0_production"]["retirements_pre2030"] = opt
            s0prod.apply_settings({"c": {2028: s}})
            with cf.unit_hooks(s) as rec:
                g = fake.group_technologies(pd.concat([model_basis(), gas], ignore_index=True), tech_groups)
            out[opt] = (g.assign(kn=lambda d: cs.keys(d.plant_id_eia, d.generator_id)).set_index("kn").retirement_year, rec)
        y, rec = out["block_all"]
        assert y["55001|CC1"] == 2031 and y["55002|CT1"] == 2031                         # gas CC / CT: 2031
        assert y["55003|ST1"] == 2027 and y["55004|IC1"] == 2028                         # Other_peaker: as fedpol
        assert y["55005|CT2"] == 2033 and y["55000|CT1"] == 2031                         # outside / inside the window
        assert y["602|1"] == 2031 and y["3845|2"] == 2031 and "56611|S01" not in y.index  # coal, hold, OS removal
        assert set(rec["pushed"].cluster_technology) >= {"Conventional Steam Coal", "Natural Gas Fired Combined Cycle"}
        assert not rec["pushed"].technology_description.isin(["Natural Gas Steam Turbine"]).any()
        # what PowerGenome then sees: in model year 2030 (Y > 2030) and gone in 2035; fedpol's function leaves it
        fn, _ = _pg_fn("apply_predetermined_retirement_override")
        assert y["55001|CC1"] > 2030 and not y["55001|CC1"] > 2035
        units = pd.DataFrame({"technology": ["Natural Gas Fired Combined Cycle"], "retirement_year": [y["55001|CC1"]]})
        assert fn(units, {"predetermined_retirement_override": cs.BLOCK_RULE}).retirement_year.iloc[0] == 2031
        for opt in ("planned_only", "unrestricted"):
            y2, rec2 = out[opt]
            assert y2["55001|CC1"] == 2027 and y2["602|1"] == 2029 and "pushed" not in rec2
        # the legacy regression case and non-S0 cases: no hook at all
        for s in (s0_case("on_pgdays"), {"s0_production": {"enabled": False}, "model_year": 2035}):
            s["predetermined_retirement_override"] = dict(cs.BLOCK_RULE)
            with cf.unit_hooks(s) as none:
                assert none is None and fake.group_technologies is group_technologies
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _pg_sequence(fake, tech_groups, basis, operating_860m):
    """PowerGenome's unit tables in create_all_generators' order, as far as the removals are concerned: its EIA-860
    units (gens_860_model: plants with a plant-map region; retirement years labelled, then grouped), then the 860M
    Operating units missing from that table (import_new_generators: placed by location, index plant/prime mover/fuel,
    grouped, then retirement years labelled by age), then all_units with the cluster technology, which fedpol's
    apply_predetermined_retirement_override gets (pg_to_switch.eia_build_info)."""
    g860 = fake.group_technologies(basis.copy(), tech_groups)
    have = set(cs.keys(g860.plant_id_eia, g860.generator_id))
    new = operating_860m[~pd.Series(cs.keys(operating_860m.plant_id_eia, operating_860m.generator_id)).isin(have).values]
    new = new.drop(columns="retirement_year").set_index(["plant_id_eia", "prime_mover_code", "energy_source_code_1"])
    g_new = fake.group_technologies(new, tech_groups).reset_index()
    g_new["retirement_year"] = g_new.operating_date + 500                        # label_retirement_year: by age
    allu = pd.concat([g860, g_new], ignore_index=True)
    return allu.rename(columns={"technology_description": "technology"})


def test_removals_deleted_before_fedpol_push(caplog):
    """The five out-of-service removals are deleted from PowerGenome's unit tables before clustering in every pre-2030
    option: from its EIA-860 units and from the 860M generators PowerGenome would otherwise add back for them (all five
    are OP/OA in the July 2025 860M Operating sheet; Biron Mill, plant 10234, has no plant-map region, so it only ever
    enters that way: why the VM's hook derived 4 remove overrides). fedpol's function receives none of them, so it
    has nothing to push into 2030; the build log says "5 removed"; another count fails the build."""
    tech_groups = {cs.CSC: list(cs.COAL_GROUP)}

    def group_technologies(df, groups=None, *a, **k):
        df = df.copy()
        for tech, members in (groups or {}).items():
            df.loc[df["technology_description"].isin(members), "technology_description"] = tech
        return df

    fake = types.ModuleType("powergenome.generators")
    fake.group_technologies, fake.atb_fixed_var_om_existing = group_technologies, lambda u, *a, **k: u
    pkg = types.ModuleType("powergenome")
    pkg.generators = fake
    saved = {k: sys.modules.get(k) for k in ("powergenome", "powergenome.generators")}
    sys.modules["powergenome"], sys.modules["powergenome.generators"] = pkg, fake
    basis = model_basis().assign(prime_mover_code="ST")
    kn = pd.Series(cs.keys(basis.plant_id_eia, basis.generator_id))
    biron = kn == "10234|GEN5"
    operating = basis[kn.isin(REMOVALS).values].copy()                       # 860M July 2025 Operating: OP / OA
    adm = basis.iloc[[0]].assign(plant_id_eia=10860, generator_id="9Z", technology_description=cs.CSC,
                                 model_region="p70", winter_capacity_mw=75.0)   # an unmapped plant PowerGenome re-adds
    operating = pd.concat([operating, adm], ignore_index=True)
    eia860 = basis[~biron.values]                                            # plant 10234: no plant-map region
    fn, _ = _pg_fn("apply_predetermined_retirement_override")
    try:
        # without the hook (as at c4a19f8: removals encoded 2026), fedpol's function pushes them to 2030
        old = basis[kn.isin(REMOVALS).values].assign(technology=cs.CSC, retirement_year=2026)
        assert (fn(old.copy(), {"predetermined_retirement_override": cs.BLOCK_RULE}).retirement_year == 2030).all()
        for opt in ("block_all", "planned_only", "unrestricted"):
            s = s0_case("on")
            s["s0_production"]["retirements_pre2030"] = opt
            s0prod.apply_settings({"c": {2028: s}})
            caplog.clear()
            with caplog.at_level("INFO", logger=cf.logger.name):
                with cf.unit_hooks(s) as rec:
                    allu = _pg_sequence(fake, tech_groups, eia860, operating)
            got = set(cs.keys(allu.plant_id_eia, allu.generator_id))
            assert not got & set(REMOVALS), (opt, got & set(REMOVALS))     # fedpol's function receives none of them
            out = fn(allu.copy(), s)                                          # with the option's own rule (if any)
            assert not set(cs.keys(out.plant_id_eia, out.generator_id)) & set(REMOVALS)
            r = rec["removals"].set_index("kn")
            assert len(r) == 5 and r.in_spec.all()
            both = f"deleted from {cf.T860} and {cf.T860M}"
            assert (r.status.drop("10234|GEN5") == both).all() and r.at["10234|GEN5", "status"] == f"deleted from {cf.T860M}"
            assert list(r.derived_override) == [k != "10234|GEN5" for k in r.index]
            assert len(rec["overrides"]) == 70 and set(cf.removal_keys(rec["overrides"])) == set(REMOVALS) - {"10234|GEN5"}
            assert "coal removals: 5 removed before clustering (4 deleted from EIA-860 units and 860M new generators: " \
                   "6705|2, 2364|2, 6055|1, 56611|S01; 1 deleted from 860M new generators: 10234|GEN5)" in caplog.text
            assert "10234|GEN5" not in set(cs.keys(rec["edited"].plant_id_eia, rec["edited"].generator_id))
            assert rec["new_860m_coal"] == [f"10860|9Z {cs.CSC} 75.0 MW"]                 # reported, not edited
            # the build's override check: Biron Mill has no override row (not in the EIA-860 units), and is accepted
            chk = cf.check_overrides(rec["overrides"], spec("coal_spec_overrides.csv", dtype={"generator_id": str}), "s0",
                                     absent=set(r.index[~r.derived_override]))
            assert chk.ok.all() and chk.set_index("kn").at["10234|GEN5", "status"].startswith("ok (removal")
            # without the removal record it still passes, as an unmapped plant (plant 10234 isn't in reeds_plant_map.csv)
            c2 = cf.check_overrides(rec["overrides"], spec("coal_spec_overrides.csv", dtype={"generator_id": str}), "s0")
            assert c2.set_index("kn").at["10234|GEN5", "status"] == cs.NOT_IN_MODEL
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_removal_count_fails_the_build(tmp_path):
    """A removal count other than 5 (here: a spec table with one remove row less, so the build derives one the spec
    doesn't list) stops the build, in the hook and in the case-input check."""
    e = spec("coal_spec_overrides.csv", dtype={"generator_id": str})
    e = e[np.array(cs.keys(e.plant_id_eia, e.generator_id)) != "6705|2"]
    e.to_csv(tmp_path / "ov.csv", index=False)
    r = cf.removals_record(cf.spec_removals(tmp_path / "ov.csv"), set(REMOVALS), {k: [cf.T860] for k in REMOVALS})
    assert len(r) == 5 and cf.removals_problems(r) == ["removals not in coal_spec_overrides.csv: 6705|2"]
    r4 = cf.removals_record(cf.spec_removals(tmp_path / "ov.csv"), set(), {})
    assert cf.removals_problems(r4) == ["4 out-of-service removals, expected 5 (coal_spec.md §2.2)"]
    fake = types.ModuleType("powergenome.generators")
    fake.group_technologies, fake.atb_fixed_var_om_existing = (lambda df, *a, **k: df), (lambda u, *a, **k: u)
    pkg = types.ModuleType("powergenome")
    pkg.generators = fake
    saved = {k: sys.modules.get(k) for k in ("powergenome", "powergenome.generators")}
    sys.modules["powergenome"], sys.modules["powergenome.generators"] = pkg, fake
    try:
        s = s0_case("on")
        s["s0_production"]["coal_spec"]["expected_overrides"] = str(tmp_path / "ov.csv")
        s0prod.apply_settings({"c": {2028: s}})
        with pytest.raises(ValueError, match="6705"):
            with cf.unit_hooks(s):
                fake.group_technologies(model_basis())
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    # the case-input check: a record with 4 removals fails
    years = [2028]
    s = s0_case("on_windows")
    s0prod.apply_settings({"c": {2028: s}})
    rec = _hook_record("block_all")
    rec["removals"] = rec["removals"][rec["removals"].kn != "6705|2"]
    cf._State.store[("c", 2028)] = {"case": "c", "year": 2028, **rec}
    _case_folder(tmp_path, ["p101"])
    with pytest.raises(ValueError, match="4 out-of-service removals, expected 5"):
        cf.write_case_inputs(tmp_path, s["s0_production"], {y: dict(s, model_year=y) for y in years}, s0prod.Log(tmp_path))


def test_coal_spec_steps_on_installed_pandas(tmp_path):
    """The coal-spec steps run on the installed pandas (the case build runs in switch-pg-reeds-fedpol, pandas 1.4.4;
    the tests also run on pandas 2/3) and reproduce the committed tables byte for byte: per-option tables
    (option_tables -> stage_history, model_mw, apply_rule), the zone caps of coal_cf and the csv writer."""
    for o in cs.RETIREMENT_OPTIONS:
        caps, holds, summ = cf.option_tables(o)
        for name, df in (("coal_spec_expected_caps_by_stage", caps.round(4)), ("coal_spec_hold_by_stage", holds),
                         ("coal_spec_stage_summary", summ)):
            assert cs.csv_text(df, index=False) == (SPEC / f"by_option/{name}.{o}.csv").read_text(), (pd.__version__, o, name)
    cu = cap_units()
    cu = cu[cu.status.isin(["OP", "SB"])]
    h, nat = cs.stage_history(cu, 2035, cs.held_keys(cs.load_holds()))
    e = spec("coal_spec_expected_caps_by_stage.csv").query("stage == 2035").set_index("zone")
    j = h.join(e[["hist_MW", "national_N"]], rsuffix="_spec", how="inner")
    assert (j.hist_MW - j.hist_MW_spec).abs().max() <= 1.0 and nat == pytest.approx(e.national_N.iloc[0], abs=1e-4)
    w = cs.weighted_by(pd.DataFrame({"z": ["a", "a", "b"], "v": [1.0, 0.0, 0.5], "w": [1.0, 3.0, 2.0]}), "z", "v", "w")
    assert list(w.weight) == [4.0, 2.0] and list(w.n_units) == [2, 1] and list(w["mean"]) == [0.25, 0.5]
    assert cs.csv_text(pd.DataFrame({"a": [1]}), index=False) == "a\n1\n"


def _pg_fn(name):
    src = (REPO / "pg_to_switch.py").read_text()
    tree = ast.parse(src)
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == name)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "PREDETERMINED_RETIREMENT_OVERRIDE_KEYS"
                                                    for t in n.targets))]
    g = {"pd": pd, "logger": __import__("logging").getLogger()}
    exec(compile(ast.Module(nodes, []), "pg_to_switch", "exec"), g)
    return g[name], src


def test_pg_to_switch_hook_and_fedpol_override_unchanged():
    """fedpol's apply_predetermined_retirement_override is as it was (no coal exemption): block_all relies on it for
    gas, and the S0 coal push (coal_fleet) encodes 2031, outside its window."""
    fn, src = _pg_fn("apply_predetermined_retirement_override")
    import subprocess
    fn_src = lambda s: s[s.index("def apply_predetermined_retirement_override"):s.index("def eia_build_info")]  # noqa: E731
    before = subprocess.run(["git", "show", "79c5f35:pg_to_switch.py"], cwd=REPO, capture_output=True, text=True,
                            check=True).stdout
    assert fn_src(src) == fn_src(before)                                    # as before the coal spec (fedpol's)
    units = pd.DataFrame({"technology": ["Conventional Steam Coal", f"{cf.HOLD_TECH} 3845 2",
                                         "Natural Gas Fired Combined Cycle", "Conventional Steam Coal"],
                          "retirement_year": [2027, 2031, 2028, 2031]})
    a = fn(units.copy(), {"predetermined_retirement_override": cs.BLOCK_RULE})
    assert list(a.retirement_year) == [2030, 2031, 2030, 2031]
    assert list(fn(units.copy(), {}).retirement_year) == [2027, 2031, 2028, 2031]          # planned_only / unrestricted
    i = src.index("with s0coal.unit_hooks(year_settings):")
    assert "gc.create_all_generators()" in src[i:i + 120]


# ------------------------------------------------------------------------------------------------ case build
def _case_folder(folder, zones):
    rows = [dict(GENERATION_PROJECT=f"{z}_conventional_steam_coal_1", gen_tech=cs.CSC, gen_load_zone=z,
                 gen_energy_source="coal", gen_forced_outage_rate="0.1", gen_can_retire_early="1") for z in zones]
    rows += [dict(GENERATION_PROJECT="p103_conventional_steam_coal_hold_1710_3_1", gen_tech=cf.hold_tech(1710, "3"),
                  gen_load_zone="p103", gen_energy_source="coal", gen_forced_outage_rate="0.1", gen_can_retire_early="1"),
             dict(GENERATION_PROJECT="p97_other_peaker_1", gen_tech="Other_peaker", gen_load_zone="p97",
                  gen_energy_source="naturalgas", gen_forced_outage_rate="0.05", gen_can_retire_early="1")]
    pd.DataFrame(rows).to_csv(folder / "gen_info.csv", index=False)


def _hook_record(option, scen="s0", basis=None):
    """What the unit hook records for a case on the public model basis (as during the real build)."""
    op, rt = cf.load_fleet860m()
    basis = cs.load_model_basis() if basis is None else basis
    ov, final = cf.derive_overrides(basis, op, rt, cf.scenario_holds({"coal_holds": {"scenario": scen}}))
    rule = cs.BLOCK_RULE if option == "block_all" else None
    derived = cf.removal_keys(ov)
    return {"units": basis, "final": final, "overrides": ov, "rule": rule,
            "edited": cf.apply_overrides(basis, final, ov, rule=rule),
            "removals": cf.removals_record(cf.spec_removals(), derived,
                                           {**{k: [cf.T860] for k in derived}, "10234|GEN5": [cf.T860M]})}


@pytest.mark.parametrize("option", cs.RETIREMENT_OPTIONS)
def test_case_build_caps_holds_and_checks(tmp_path, option):
    """The case build on the public model basis passes every check against its option's tables; caps go on the coal
    clusters (per period with two periods) and hold projects; a missed cap stops the build."""
    years = [2028, 2030]
    s = s0_case("on_windows")
    s["s0_production"]["retirements_pre2030"] = option
    s0prod.apply_settings({"c": {2028: s}})
    assert cf.pre2030_rule(s) == (cs.BLOCK_RULE if option == "block_all" else None)
    s0 = s["s0_production"]
    for y in years:
        cf._State.store[("c", y)] = {"case": "c", "year": y, **_hook_record(option)}
    zones = ["p101", "p111", "p21", "p53", "p130"]
    _case_folder(tmp_path, zones)
    log = s0prod.Log(tmp_path)
    cf.write_case_inputs(tmp_path, s0, {y: dict(s, model_year=y) for y in years}, log)
    t = pd.read_csv(tmp_path / "coal_caps_by_stage.csv")
    assert t.ok.all() and set(t.stage) == set(years) and t.M_diff.abs().max() < 1e-6
    gi = pd.read_csv(tmp_path / "gen_info.csv", na_values=".").set_index("GENERATION_PROJECT")
    bp = pd.read_csv(tmp_path / "gen_max_annual_availability_by_period.csv").set_index(["GENERATION_PROJECT", "PERIOD"])
    e = pd.read_csv(cs.SPEC / f"by_option/coal_spec_expected_caps_by_stage.{option}.csv").set_index(["stage", "zone"]).expected_cap
    for z in zones:
        for y in years:
            assert bp.loc[(f"{z}_conventional_steam_coal_1", y)].iloc[0] == pytest.approx(min(1, e[(y, z)] / 0.9), abs=1e-3)
    h = "p103_conventional_steam_coal_hold_1710_3_1"
    assert gi.at[h, "gen_max_annual_availability"] == pytest.approx(0.5539 / 0.9, abs=1e-6)  # its own unit cap
    assert gi.at[h, "gen_can_retire_early"] == 0                                     # held: no economic retirement
    assert pd.isna(gi.at["p97_other_peaker_1", "gen_max_annual_availability"])      # converted gas: no coal cap
    ap = pd.read_csv(tmp_path / "coal_overrides_applied.csv").set_index("kn")
    assert ap.ok.all() and len(ap) == 71                                         # the spec table's S0 rows
    assert set(ap.index[ap.status == cs.NOT_IN_MODEL]) == {"10167|GEN1", "50305|GE10", "50305|GEN6", "50305|GEN7",
                                                            "50305|GEN8", "50305|GEN9", "50965|GEN1", "50121|TG1",
                                                            "50121|TG2", "57953|ST", "10361|GEN3", "10361|GEN4"}
    assert ap.at["10234|GEN5", "status"].startswith("ok (removal") and ap.at["1004|CT1", "status"] == "ok"
    assert len(pd.read_csv(tmp_path / "coal_not_in_model.csv")) == len(cs.not_in_model())
    assert pd.read_csv(tmp_path / "coal_converted_heat_rates.csv").ok.all() if (tmp_path / "coal_converted_heat_rates.csv").exists() else True
    hb = pd.read_csv(tmp_path / "coal_holds_by_stage.csv")
    assert hb.ok.all() and set(hb[hb.build == "held"].stage) == ({2028, 2030} if option == "block_all" else {2028})
    txt = "\n".join(log.lines)
    n = {"block_all": "0.5791", "planned_only": "0.5812", "unrestricted": "0.5812"}[option]
    assert f"coal caps 2028: N {n}" in txt and "71/71 as coal_spec_overrides.csv" in txt
    assert "coal removals: 5 removed before clustering (4 deleted from EIA-860 units: " in txt
    assert "1 deleted from 860M new generators: 10234|GEN5" in txt and cs.NOT_IN_MODEL in txt
    assert "model MW differs" not in txt                                             # zone MW: no differences
    assert len(pd.read_csv(tmp_path / "coal_removals.csv")) == 5
    # the wrong option's table is caught
    if option != "block_all":
        _case_folder(tmp_path, zones)
        s0b = dict(s0, retirements_pre2030="block_all")
        with pytest.raises(ValueError, match="coal spec check failed"):
            cf.write_case_inputs(tmp_path, s0b, {y: dict(s, model_year=y) for y in years}, s0prod.Log(tmp_path))
    # a cap that misses the spec stops the build, after writing the check files
    rec = _hook_record(option)
    m = rec["edited"].model_region == "p111"
    rec["edited"].loc[m, "winter_capacity_mw"] *= 0.1                               # p111: blend -> own history
    for y in years:
        cf._State.store[("c", y)] = {"case": "c", "year": y, **rec}
    _case_folder(tmp_path, zones)
    with pytest.raises(ValueError, match="p111"):
        cf.write_case_inputs(tmp_path, s0, {y: dict(s, model_year=y) for y in years}, s0prod.Log(tmp_path))
    with pytest.raises(RuntimeError, match="unit hook"):
        cf.write_case_inputs(tmp_path, s0, {2035: dict(s, model_year=2035, case_id="other")}, s0prod.Log(tmp_path))


def test_option_tables_committed_and_figures():
    """The committed per-option tables are what option_tables() builds; planned_only (= unrestricted) reproduces the
    rev. 2 caps and rules; its model MW differs from rev. 2's only by the plants not in reeds_plant_map.csv (left out,
    Tom 2026-10-04) and Edwardsport CT1 / CT2 (+481.2 MW in p107, as the build counts them); block_all changes only
    2028 and 2030."""
    import subprocess
    r = subprocess.run([sys.executable, str(REPO / "s0_workflow/scripts/build_coal_option_tables.py"), "--check"],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    D = cs.SPEC / "by_option"
    caps = {o: pd.read_csv(D / f"coal_spec_expected_caps_by_stage.{o}.csv").set_index(["stage", "zone"]) for o in cs.RETIREMENT_OPTIONS}
    summ = {o: pd.read_csv(D / f"coal_spec_stage_summary.{o}.csv").set_index("stage") for o in cs.RETIREMENT_OPTIONS}
    holds = {o: pd.read_csv(D / f"coal_spec_hold_by_stage.{o}.csv", dtype={"generator_id": str}) for o in cs.RETIREMENT_OPTIONS}
    pd.testing.assert_frame_equal(caps["planned_only"], caps["unrestricted"])
    pd.testing.assert_frame_equal(holds["planned_only"], holds["unrestricted"])
    rev2 = spec("coal_spec_expected_caps_by_stage.csv").set_index(["stage", "zone"])
    j = rev2.join(caps["planned_only"], rsuffix="_o", how="outer")
    strip = lambda r: r.fillna("").str.replace(cs.NO_COAL, "", regex=False)  # noqa: E731
    # zones whose model coal and history were all unmapped plants drop out (A4, A5)
    gone = j.rule_o.isna()
    assert len(j) == len(rev2)
    assert set(j.index[gone].get_level_values("zone")) == {"p10", "p42", "p44", "p74", "p76", "p83", "p92"}
    assert (strip(j.rule[~gone]) == strip(j.rule_o[~gone])).all()                 # no rule changes
    # H differs from rev. 2's exactly by the unmapped cap units (A5); caps move only through H and N
    cu = cap_units()
    cu = cu[cu.status.isin(["OP", "SB"])]
    un = cu[~cs.in_plant_map(cu["Plant ID"]) & cu.cf_max.notna() & cu.zone.notna()]       # units with history
    held = cs.held_keys(cs.load_holds())
    for st in cs.STAGES:
        dh = (j.hist_MW.fillna(0) - j.hist_MW_o.fillna(0)).loc[st]
        want = cs.stage_units(un, st, held).groupby("zone").winter_mw.sum()
        want = want[want.index.isin(j.loc[st].index)]
        pd.testing.assert_series_equal(dh[dh.abs() > 0.05].sort_index(), want[want > 0.05].sort_index(),
                                       check_names=False, atol=0.05)
        same = j.loc[st][~gone.loc[st] & (dh.abs() <= 0.05) & (j.loc[st].rule == cs.RULE_OWN)]
        assert (same.expected_cap - same.expected_cap_o).abs().max() <= 1e-4
    nim = cs.not_in_model()
    assert set(nim.status) == {cs.NOT_IN_MODEL} and not nim.plant_id_eia.isin(cs.read_plant_map()).any()
    unmapped = nim[nim.county_zone.notna() & (nim.kn != "10234|GEN5")].groupby("county_zone").winter_capacity_mw.sum()
    # GEN5: an OS removal, out in rev. 2 too; Covington (p99, 43 MW): not in rev. 2's model MW either
    want = (-unmapped.drop("p99")).add(pd.Series({"p107": 481.2}), fill_value=0)
    for st in cs.STAGES:
        dm = (j.model_MW_after_overrides_o.fillna(0) - j.model_MW_after_overrides.fillna(0)).loc[st]
        pd.testing.assert_series_equal(dm[dm.abs() > 0.05].sort_index(), want.sort_index(), check_names=False, atol=0.05)
    assert unmapped.sum() == pytest.approx(1450.4) and unmapped["p70"] == pytest.approx(472.0)
    pd.testing.assert_frame_equal(holds["planned_only"].drop(columns="cap_while_held"),
                                  spec("coal_spec_hold_by_stage.csv", dtype={"generator_id": str}).drop(columns="cap_while_held"))
    s, b = summ["planned_only"], summ["block_all"]
    assert list(s.national_N) == [0.5812, 0.5934, 0.6007, 0.6011, 0.6011]            # A5 (was 0.5806 ... 0.6001)
    assert list(s.model_GW_after_overrides) == [156.35, 141.14, 125.48, 123.38, 123.38]
    assert list(b.national_N[[2028, 2030]]) == [0.5791, 0.5791]
    assert list(b.model_GW_after_overrides[[2028, 2030]]) == [163.59, 163.59]             # OS removals out
    ba = caps["block_all"]
    assert ba.loc[(2028, "p130")].rule == cs.RULE_OWN and ba.loc[(2028, "p130")].expected_cap == pytest.approx(0.128)
    # the OS removals are out of both options in 2028 and 2030 (zones p63, p58, p130, p107, p76: 1,969 MW)
    for st in (2028, 2030):
        for z in ("p63", "p58", "p107", "p76"):
            assert ba.loc[(st, z)].model_MW_after_overrides <= ba.loc[(2028, z)].model_MW_before + 1e-6
    assert (b.model_GW_after_overrides[2028] - s.model_GW_after_overrides[2028]) == pytest.approx(7.24, abs=0.01)
    assert list(b.held_GW_S0) == [3.24, 3.24, 0, 0, 0] and list(s.held_GW_S0) == [3.24, 0, 0, 0, 0]
    later = [2035, 2040, 2045]
    pd.testing.assert_frame_equal(caps["block_all"].loc[later], caps["planned_only"].loc[later])
    assert (b.loc[later].drop(columns="option") == s.loc[later].drop(columns="option")).all().all()


def test_retirement_option_settings_and_rules(tmp_path):
    """block_all sets fedpol's rule and the per-period rule; planned_only removes the dated push but keeps the rule;
    unrestricted removes both and allows economic retirement from the first stage; legacy leaves the settings."""
    # the module is on in every option with the retirement friction (S0 default 0.5, §66); unrestricted adds it only
    # for the friction (no retirement_rules.csv)
    for opt, rule, module in (("block_all", cs.BLOCK_RULE, True), ("planned_only", None, True), ("unrestricted", None, True)):
        s = s0_case("on")
        s["predetermined_retirement_override"] = {"technologies": ["coal"], "window": [2026, 2029], "target_year": 2030}
        s["clean_power_regs_retirement_override"] = {"technologies": ["coal"], "mode": "x"}
        s["s0_production"]["retirements_pre2030"] = opt
        s0prod.apply_settings({"c": {2028: s}})
        assert s.get("predetermined_retirement_override") == rule
        assert ("clean_power_regs_retirement_override" in s) == (opt == "block_all")
        assert ("study_modules.retirement_rules" in s0prod.scenario_options(s)) == module
        d = tmp_path / opt
        d.mkdir()
        pd.DataFrame({"GENERATION_PROJECT": ["coal_old", "gas_old", "gas_new"], "gen_energy_source": ["coal", "naturalgas", "naturalgas"],
                      "gen_can_retire_early": [0, 0, 0]}).to_csv(d / "gen_info.csv", index=False)
        pd.DataFrame({"GENERATION_PROJECT": ["coal_old", "gas_old"], "build_year": [1990, 2000],
                      "build_gen_predetermined": [1, 1]}).to_csv(d / "gen_build_predetermined.csv", index=False)
        s0prod.write_retirement_rules(d, s["s0_production"], s0prod.Log(d))
        gi = pd.read_csv(d / "gen_info.csv").set_index("GENERATION_PROJECT").gen_can_retire_early
        assert list(gi) == [1, 1, 0] and (d / "retirement_rules.csv").exists() == (opt != "unrestricted")
    leg = s0_case("on_pgdays")
    leg["predetermined_retirement_override"] = dict(cs.BLOCK_RULE)
    before = copy.deepcopy(leg)
    s0prod.apply_settings({"c": {2035: leg}})
    assert leg["predetermined_retirement_override"] == before["predetermined_retirement_override"]
    assert cf.retirement_option(leg["s0_production"]) == "legacy"
    assert "study_modules.retirement_rules" in s0prod.scenario_options(leg)          # legacy: retirement_rule.enabled
    with pytest.raises(ValueError, match="retirements_pre2030"):
        cf.retirement_option({"retirements_pre2030": "sometimes"})


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


def test_cap_unit_set_mapped_plants_only():
    """A5 (Tom 2026-10-04): H, own and N come from OP / SB cap units of plants in reeds_plant_map.csv only, in the
    case build (write_case_inputs) and the expected tables (option_tables) alike."""
    cu = cs.load_cap_units()
    c = cs.cap_unit_set(cu)
    assert set(c.status) == {"OP", "SB"} and cs.in_plant_map(c["Plant ID"]).all()
    gone = cu[cu.status.isin(["OP", "SB"]) & ~cs.in_plant_map(cu["Plant ID"])]
    assert len(c) + len(gone) == cu.status.isin(["OP", "SB"]).sum()
    assert {10860, 10864, 10865, 50481, 50900} <= set(gone["Plant ID"])            # ADM, Tennessee Eastman, Covington
    src = (REPO / "s0_workflow/coal_fleet.py").read_text()
    assert src.count("cs.cap_unit_set(") == 2                                      # build and option tables
