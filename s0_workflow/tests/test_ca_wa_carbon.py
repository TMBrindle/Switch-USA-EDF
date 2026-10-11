"""California-Washington linked carbon price and import cost for S0 cases (CHANGES §65).
Small hand-built inputs where noted: fixtures, not results."""
import copy
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_workflow import production as s0prod  # noqa: E402

PRICES = {"central": [48.6, 53.4, 67.8, 86.0, 109.2], "low": [29.1, 32.0, 40.6, 51.6, 65.4],
          "high": [52.8, 74.8, 149.1, 189.2, 240.0]}
YEARS = [2028, 2030, 2035, 2040, 2045]
CA, WA = ["p8", "p9", "p10", "p11"], ["p1", "p2", "p3", "p4"]


def test_prices_by_path_and_year():
    for path, vals in PRICES.items():
        r = s0prod.ca_wa_settings({"ca_wa_carbon": {"path": path}})
        assert [s0prod.ca_wa_price(y, r) for y in YEARS] == vals
    r = s0prod.ca_wa_settings({})
    assert r["path"] == "central" and r["dollar_year"] == 2024 and r["programs"] == {"CA": "ETS 2", "WA": "ETS 3"}
    assert r["import_tco2_per_mwh"] == {"CA": 0.428, "WA": 0.437}
    assert s0prod.ca_wa_price(2032, r) == pytest.approx(53.4 + (67.8 - 53.4) * 2 / 5)        # linear between keys
    assert s0prod.ca_wa_price(2050, r) == 109.2 and s0prod.ca_wa_price(2027, r) is None
    with pytest.raises(ValueError, match="path"):
        s0prod.ca_wa_settings({"ca_wa_carbon": {"path": "mid"}})
    s0 = yaml.safe_load(open(REPO / "pg/settings/model_definition.yml"))
    assert s0["target_usd_year"] == 2024                                                    # no dollar conversion


def _case(axis_value, year, extra_axis=None):
    sm = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    s = {"s0_production": copy.deepcopy(s0), "target_usd_year": 2024,
         "model_first_planning_year": {2028: 2026, 2030: 2029, 2035: 2031, 2040: 2036, 2045: 2041}[year]}
    s = s0prod.deep_merge(s, sm["all_years"]["policies"]["S0_uncapped"])
    if axis_value:
        s = s0prod.deep_merge(s, sm["all_years"]["s0_production"][axis_value])
    if extra_axis:
        s = s0prod.deep_merge(s, sm["all_years"]["ca_wa_price"][extra_axis] or {})
    return s


def test_settings_s0_regression_and_paths():
    for y, v in zip(YEARS, PRICES["central"]):
        s = _case("on", y)
        assert s["carbon_cost_by_program"] == {"ETS 1": ".", "ETS 2": 33.43, "ETS 3": 33.43}     # the preset before
        cs = {"c": {y: s}}
        s0prod.apply_settings(cs)
        assert cs["c"][y]["carbon_cost_by_program"] == {"ETS 1": ".", "ETS 2": v, "ETS 3": v}  # RGGI stays hard
    for path in ("low", "high"):
        cs = {"c": {2035: _case("on", 2035, path)}}
        s0prod.apply_settings(cs)
        assert cs["c"][2035]["carbon_cost_by_program"]["ETS 2"] == PRICES[path][2]
    # regression case (on_pgdays: legacy): the preset's $33.43
    cs = {"c": {2035: _case("on_pgdays", 2035)}}
    s0prod.apply_settings(cs)
    assert cs["c"][2035]["carbon_cost_by_program"] == {"ETS 1": ".", "ETS 2": 33.43, "ETS 3": 33.43}
    # non-S0: untouched
    s = _case(None, 2035)
    before = copy.deepcopy(s)
    s0prod.apply_settings({"c": {2035: s}})
    assert s == before
    s = _case("on", 2035)
    s["target_usd_year"] = 2023
    with pytest.raises(ValueError, match="target_usd_year"):
        s0prod.apply_settings({"c": {2035: s}})
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    assert "ca_wa_price" in si.columns and (si.ca_wa_price == "central").all()


def test_ca_wa_zones_are_the_priced_programs():
    """The CA and WA zones are the ETS 2 / ETS 3 zones of the S0 policy file, and every one is in CA / WA."""
    e = pd.read_csv(REPO / "pg/extra_inputs/rggi_carbon/emission_policies_reeds_2026.09.21.csv")
    assert sorted(e[e.CO_2_Cap_Zone_2 == 1].region.unique()) == sorted(CA)
    assert sorted(e[e.CO_2_Cap_Zone_3 == 1].region.unique()) == sorted(WA)
    assert e[e.CO_2_Cap_Zone_2 == 1].CO_2_Max_Mtons_2.max() == 0 and e[e.CO_2_Cap_Zone_3 == 1].CO_2_Max_Mtons_3.max() == 0
    st = dict(pd.read_csv(REPO / "hierarchy.csv")[["ba", "st"]].values)
    assert {st[z] for z in CA} == {"CA"} and {st[z] for z in WA} == {"WA"}


def test_import_cost_file_on_real_transmission(tmp_path):
    from test_forced_tx import build
    (tmp_path / "b").mkdir()
    out = build(tmp_path / "b", years=[2035], source=REPO / "pg_to_switch.py")
    pd.DataFrame([{"CO2_PROGRAM": prog, "PERIOD": 2035, "LOAD_ZONE": z, "carbon_cap_tco2_per_yr": 0.0,
                   "carbon_cost_dollar_per_tco2": 67.8, "carbon_floor_price_dollar_per_tco2": 0.0}
                  for prog, zs in (("ETS 2", CA), ("ETS 3", WA)) for z in zs]).to_csv(out / "carbon_policies_regional.csv",
                                                                                    index=False)
    logs = []
    s0prod.write_ca_wa_import_cost(out, {"ca_wa_carbon": {"import_charge": "all_default"}}, {2035: {}}, logs.append)
    ic = pd.read_csv(out / "trans_import_cost.csv")                  # v3's charge (§65): every MWh at the default
    inside = set(CA) | set(WA)
    assert len(ic) > 0 and set(ic.trans_lz_to) <= inside and not (set(ic.trans_lz_from) & inside)  # into CA/WA only
    tl = pd.read_csv(out / "transmission_lines.csv")
    boundary = {(a, b) if b in inside else (b, a) for a, b in zip(tl.trans_lz1, tl.trans_lz2)
                if (a in inside) != (b in inside)}
    assert set(zip(ic.trans_lz_from, ic.trans_lz_to)) == boundary                          # every boundary line, once
    for r in ic.itertuples():
        f = 0.428 if r.trans_lz_to in CA else 0.437
        assert r.trans_import_cost_per_mwh == pytest.approx(67.8 * f, abs=1e-4)
    assert ic.PERIOD.unique().tolist() == [2035] and any("import directions" in x for x in logs)
    # legacy: no file
    out2 = tmp_path / "legacy"
    out2.mkdir()
    for f in ("carbon_policies_regional.csv", "transmission_lines.csv"):
        (out2 / f).write_bytes((out / f).read_bytes())
    s0prod.write_ca_wa_import_cost(out2, {"ca_wa_carbon": {"mode": "legacy"}}, {2035: {}}, logs.append)
    assert not (out2 / "trans_import_cost.csv").exists()


def test_toy_import_cost_charges_delivered_imports_one_way(tmp_path):
    """Switch toy: an import cost on Central -> North charges delivered MWh in that direction only, inside the existing
    TxHurdleCostPerTP component (no new component, so other cases report as before)."""
    from toyutil import toy_inputs

    def edit(inp):
        pd.DataFrame([{"trans_lz_from": "Central", "trans_lz_to": "North", "PERIOD": p, "trans_import_cost_per_mwh": 25.0}
                      for p in (2020, 2030)]).to_csv(inp / "trans_import_cost.csv", index=False)
    run = toy_inputs(tmp_path, "imp", ["trans_hurdle_cost"], edit)
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--include-module", "mods.trans_hurdle_cost"],
                       cwd=run, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(run)})
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    res = pd.read_csv(run / "outputs/trans_import_cost_results.csv")
    assert res.annual_cost.tolist() == pytest.approx((res.delivered_mwh_per_yr * 25.0).tolist()) and res.delivered_mwh_per_yr.sum() > 0
    ci = pd.read_csv(run / "outputs/costs_itemized.csv")
    assert "TxImportCostPerTP" not in set(ci.Component)
    hc = ci[ci.Component == "TxHurdleCostPerTP"].set_index("PERIOD").AnnualCost_Real
    for p, g in res.groupby("PERIOD"):
        assert hc[p] == pytest.approx(g.annual_cost.sum(), rel=1e-6)
    # without the file: no results file, and the hurdle component is as before (0 here)
    run2 = toy_inputs(tmp_path, "noimp", ["trans_hurdle_cost"])
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--include-module", "mods.trans_hurdle_cost"],
                       cwd=run2, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(run2)})
    assert r.returncode == 0, r.stderr[-2000:]
    assert not (run2 / "outputs/trans_import_cost_results.csv").exists()


# ---- import charge options (CHANGES §86; §91 unspecified_share was 96b4780's default; §93: two_tranche is the S0
# default, Tom's S0 v3.1 launch setting) ----
SHARE = {"CA": 0.136, "WA": 1.0}                                         # CA: CEC 2023 PLACEHOLDER; WA: as v3


def test_import_charge_settings():
    r = s0prod.ca_wa_settings({})
    assert r["import_charge"] == "two_tranche" and r["two_tranche"]["free_mwh_per_yr"] == 49.399e6
    assert r["unspecified_share"] == SHARE and r["specified_factor"] == 0                   # the option's values
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["ca_wa_carbon"]["import_charge"] == "two_tranche"    # explicit in the yml (S0 v3.1 launch setting)
    assert s0prod.CA_WA_DEFAULTS["import_charge"] == "two_tranche"     # and the code default
    assert s0prod.ca_wa_settings(s0)["import_charge"] == "two_tranche"
    sh = s0prod.ca_wa_settings({"ca_wa_carbon": {"import_charge": "unspecified_share"}})
    assert s0prod.ca_wa_import_factor(sh, "CA", "p5", 2035) == pytest.approx(0.136 * 0.428)
    assert s0prod.ca_wa_import_factor(sh, "WA", "p5", 2035) == 0.437                        # WA as v3
    assert s0prod.ca_wa_import_factor(s0prod.ca_wa_settings(s0), "WA", "p5", 2035) == 0.437  # two_tranche: WA as v3
    r = s0prod.ca_wa_settings({"ca_wa_carbon": {"import_charge": "unspecified_share", "unspecified_share": {"WA": 0.5}}})
    assert r["unspecified_share"] == {"CA": 0.136, "WA": 0.5}             # merged per state
    assert s0prod.ca_wa_import_factor(r, "CA", "p5", 2035) == pytest.approx(0.136 * 0.428)
    assert s0prod.ca_wa_import_factor(r, "WA", "p5", 2035) == pytest.approx(0.5 * 0.437)
    r = s0prod.ca_wa_settings({"ca_wa_carbon": {"import_charge": "unspecified_share", "specified_factor": 0.05,
                                                "unspecified_share": {"CA": {2028: 0.2, 2038: 0.1}}}})
    assert s0prod.ca_wa_unspecified_share(r, "CA", 2033) == pytest.approx(0.15)       # linear between keys
    assert s0prod.ca_wa_unspecified_share(r, "CA", 2045) == 0.1                       # held after the last
    assert s0prod.ca_wa_import_factor(r, "CA", "p5", 2033) == pytest.approx(0.15 * 0.428 + 0.85 * 0.05)
    assert s0prod.ca_wa_import_factor(s0prod.ca_wa_settings({"ca_wa_carbon": {"import_charge": "all_default"}}),
                                      "CA", "p5", 2035) == 0.428
    for bad, msg in (({"import_charge": "marginal"}, "import_charge"),
                     ({"import_charge": "source_table"}, "source_table"),
                     ({"import_charge": "unspecified_share", "specified_factor": "source_table"}, "source_table"),
                     ({"import_charge": "unspecified_share", "specified_factor": -0.1}, "specified_factor")):
        with pytest.raises(ValueError, match=msg):
            s0prod.ca_wa_settings({"ca_wa_carbon": bad})
    r = s0prod.ca_wa_settings({"ca_wa_carbon": {"import_charge": "unspecified_share", "unspecified_share": {"CA": 1.2}}})
    with pytest.raises(ValueError, match="unspecified_share"):
        s0prod.ca_wa_import_factor(r, "CA", "p5", 2035)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from test_forced_tx import build
    tmp = tmp_path_factory.mktemp("icopt")
    (tmp / "b").mkdir()
    out = build(tmp / "b", years=[2035], source=REPO / "pg_to_switch.py")
    pd.DataFrame([{"CO2_PROGRAM": prog, "PERIOD": 2035, "LOAD_ZONE": z, "carbon_cap_tco2_per_yr": 0.0,
                   "carbon_cost_dollar_per_tco2": 67.8, "carbon_floor_price_dollar_per_tco2": 0.0}
                  for prog, zs in (("ETS 2", CA), ("ETS 3", WA)) for z in zs]).to_csv(out / "carbon_policies_regional.csv",
                                                                                    index=False)
    return out


def _write(out, s0, name):
    d = out.parent / name
    d.mkdir()
    for f in ("carbon_policies_regional.csv", "transmission_lines.csv"):
        (d / f).write_bytes((out / f).read_bytes())
    logs = []
    s0prod.write_ca_wa_import_cost(d, s0, {2035: {}}, logs.append)
    return d / "trans_import_cost.csv", logs


def test_import_charge_options_on_real_transmission(built):
    base, _ = _write(built, {"ca_wa_carbon": {"import_charge": "all_default"}}, "v3")
    b = pd.read_csv(base)
    f = lambda z: 0.428 if z in CA else 0.437                              # noqa: E731
    sh = lambda z: SHARE["CA"] if z in CA else SHARE["WA"]                # noqa: E731
    for r in b.itertuples():                                               # v3: every MWh at the default factor
        assert r.trans_import_cost_per_mwh == pytest.approx(67.8 * f(r.trans_lz_to), abs=1e-4)
    # unspecified share (96b4780's default): same directions, CA at 0.136 of v3's, WA as v3
    dflt, logs = _write(built, {"ca_wa_carbon": {"import_charge": "unspecified_share"}}, "share")
    u = pd.read_csv(dflt)
    assert u[["trans_lz_from", "trans_lz_to", "PERIOD"]].equals(b[["trans_lz_from", "trans_lz_to", "PERIOD"]])
    for r in u.itertuples():
        assert r.trans_import_cost_per_mwh == pytest.approx(67.8 * sh(r.trans_lz_to) * f(r.trans_lz_to), abs=1e-4)
    assert (u[u.trans_lz_to.isin(WA)].trans_import_cost_per_mwh.values
            == b[b.trans_lz_to.isin(WA)].trans_import_cost_per_mwh.values).all()                 # WA: as v3
    ca = u[u.trans_lz_to.isin(CA)].trans_import_cost_per_mwh
    assert len(ca) and ca.iloc[0] == pytest.approx(3.9465, abs=1e-4)                              # $3.95/MWh in 2035
    assert any("(unspecified_share)" in x for x in logs)
    p, _ = _write(built, {"ca_wa_carbon": {"import_charge": "unspecified_share", "specified_factor": 0.02}}, "share2")
    for r in pd.read_csv(p).itertuples():
        want = sh(r.trans_lz_to) * f(r.trans_lz_to) + (1 - sh(r.trans_lz_to)) * 0.02
        assert r.trans_import_cost_per_mwh == pytest.approx(67.8 * want, abs=1e-4)
    # source table: the exporting zone's factor (hand-built fixture factors, not results); a period row beats a
    # period-less one
    srcs = sorted(set(b.trans_lz_from))
    fac = {z: round(0.05 * (i + 1), 3) for i, z in enumerate(srcs)}
    rows = [{"zone": z, "tco2_per_mwh": v, "period": None} for z, v in fac.items()]
    rows.append({"zone": srcs[0], "tco2_per_mwh": 0.9, "period": 2035})
    tab = built.parent / "factors.csv"
    pd.DataFrame(rows).to_csv(tab, index=False)
    fac[srcs[0]] = 0.9
    p, _ = _write(built, {"ca_wa_carbon": {"import_charge": "source_table", "source_table": str(tab)}}, "table")
    for r in pd.read_csv(p).itertuples():
        assert r.trans_import_cost_per_mwh == pytest.approx(67.8 * fac[r.trans_lz_from], abs=1e-4)
    p, _ = _write(built, {"ca_wa_carbon": {"import_charge": "unspecified_share", "specified_factor": "source_table",
                                           "source_table": str(tab)}}, "hybrid")
    for r in pd.read_csv(p).itertuples():
        want = sh(r.trans_lz_to) * f(r.trans_lz_to) + (1 - sh(r.trans_lz_to)) * fac[r.trans_lz_from]
        assert r.trans_import_cost_per_mwh == pytest.approx(67.8 * want, abs=1e-4)
    # a missing exporting zone stops the build
    pd.DataFrame(rows[1:-1]).to_csv(tab, index=False)
    with pytest.raises(ValueError, match=f"zone {srcs[0]}"):
        _write(built, {"ca_wa_carbon": {"import_charge": "source_table", "source_table": str(tab)}}, "missing")


def test_zone_import_factors_script(tmp_path):
    """Hand-built dispatch summary (a fixture, not results): average and fossil rates, storage left out, a zone with
    no generation filled with the system value."""
    from s0_workflow.scripts import zone_import_factors as zif
    s = pd.DataFrame([
        ("gas", "z1", "gas", 2035, 100.0, 40_000.0, 0.0), ("solar", "z1", "sun", 2035, 300.0, 0.0, 0.0),
        ("battery", "z1", "elec", 2035, -5.0, 0.0, 25.0), ("hydro", "z2", "water", 2035, 50.0, 0.0, 0.0),
        ("coal", "z3", "coal", 2035, 10.0, 10_000.0, 0.0), ("gas", "z4", "gas", 2035, 0.0, 0.0, 0.0)],
        columns=["gen_tech", "gen_load_zone", "gen_energy_source", "period", "Energy_GWh_typical_yr",
                 "DispatchEmissions_tCO2_per_typical_yr", "Store_GWh_typical_yr"])
    a = zif.factors(s, "average").set_index("zone")
    assert a.tco2_per_mwh.to_dict() == pytest.approx({"z1": 0.1, "z2": 0.0, "z3": 1.0, "z4": 50_000 / 460e3})
    assert a.loc["z4", "filled"] == "system" and a.loc["z1", "filled"] == ""
    fo = zif.factors(s, "fossil").set_index("zone")
    assert fo.loc["z1", "tco2_per_mwh"] == pytest.approx(0.4) and fo.loc["z2", "filled"] == "system"
    d = tmp_path / "out"
    d.mkdir()
    s.to_csv(d / "dispatch_zonal_annual_summary.csv", index=False)
    zif.main([str(d), "--out", str(tmp_path / "f.csv")])
    t = pd.read_csv(tmp_path / "f.csv")
    assert set(t.columns) >= {"zone", "period", "tco2_per_mwh"} and len(t) == 4
    r = s0prod.ca_wa_settings({"ca_wa_carbon": {"import_charge": "source_table", "source_table": str(tmp_path / "f.csv")}})
    assert s0prod.ca_wa_import_factor(r, "CA", "z3", 2035, s0prod.ca_wa_source_table(r)) == pytest.approx(1.0)


def test_toy_unspecified_share_charge(tmp_path):
    """§91 on a Switch toy: South (the toy zone that imports most) stands in for a CA zone (ETS 2). The case writer's charge (the S0 default: CA share
    0.136, specified 0) puts 53.4 x 0.428 x 0.136 $/MWh on power delivered into South in 2030 (no charge before 2028),
    in the existing hurdle component. Imports into South are at least as large as under v3's charge on every MWh,
    and the total cost is no higher."""
    from toyutil import toy_inputs

    def run(name, s0):
        def edit(inp):
            pd.DataFrame([{"CO2_PROGRAM": "ETS 2", "PERIOD": p, "LOAD_ZONE": "South", "carbon_cap_tco2_per_yr": 0.0,
                           "carbon_cost_dollar_per_tco2": 53.4, "carbon_floor_price_dollar_per_tco2": 0.0}
                          for p in (2020, 2030)]).to_csv(inp / "carbon_policies_regional.csv", index=False)
            s0prod.write_ca_wa_import_cost(inp, s0, {2020: {}, 2030: {}}, lambda x: None)
        r = toy_inputs(tmp_path, name, ["trans_hurdle_cost"], edit)
        res = subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--include-module", "mods.trans_hurdle_cost"],
                             cwd=r, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(r)})
        assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
        return r

    new = run("share", {"ca_wa_carbon": {"import_charge": "unspecified_share"}})
    ic = pd.read_csv(new / "inputs/trans_import_cost.csv")
    assert set(ic.trans_lz_to) == {"South"} and set(ic.PERIOD) == {2030}
    assert ic.trans_import_cost_per_mwh.unique().tolist() == [pytest.approx(53.4 * 0.428 * 0.136, abs=1e-4)]
    res = pd.read_csv(new / "outputs/trans_import_cost_results.csv")
    assert res.annual_cost.tolist() == pytest.approx((res.delivered_mwh_per_yr * 53.4 * 0.428 * 0.136).tolist(), rel=1e-5)
    ci = pd.read_csv(new / "outputs/costs_itemized.csv")
    assert ci[(ci.Component == "TxHurdleCostPerTP") & (ci.PERIOD == 2030)].AnnualCost_Real.iat[0] == \
        pytest.approx(res.annual_cost.sum(), rel=1e-6)
    old = run("v3", {"ca_wa_carbon": {"import_charge": "all_default"}})
    assert pd.read_csv(old / "inputs/trans_import_cost.csv").trans_import_cost_per_mwh.unique().tolist() == \
        [pytest.approx(53.4 * 0.428, abs=1e-4)]
    r_old = pd.read_csv(old / "outputs/trans_import_cost_results.csv")
    assert res.delivered_mwh_per_yr.sum() > 0
    assert res.delivered_mwh_per_yr.sum() > r_old.delivered_mwh_per_yr.sum()      # cheaper imports: more of them
    tot = lambda r: float(open(r / "outputs/total_cost.txt").read())              # noqa: E731
    assert tot(new) <= tot(old) + 1e-6
    print("delivered into South 2030, MWh/yr: share", res.delivered_mwh_per_yr.sum(), "v3", r_old.delivered_mwh_per_yr.sum())


# ---- §92 two-tranche CA import charge (option; not the S0 default) ----

def test_two_tranche_writer_on_real_transmission(built):
    """two_tranche: CA border imports leave trans_import_cost.csv and go into one tranche per period (free 49.4 TWh
    PLACEHOLDER, then price x 0.428); WA directions keep v3's per-MWh charge; the info file records mode, size and
    source. It is the S0 default (§93): the default writes the same files byte for byte; the legacy (regression)
    case and the other options write no tranche files."""
    v3, _ = _write(built, {"ca_wa_carbon": {"import_charge": "all_default"}}, "tt_v3")
    p, logs = _write(built, {"ca_wa_carbon": {"import_charge": "two_tranche"}}, "tt")
    d = p.parent
    ic, b = pd.read_csv(p), pd.read_csv(v3)
    assert set(ic.trans_lz_to) == set(b.trans_lz_to) & set(WA)                          # CA left the per-MWh file
    assert ic.reset_index(drop=True).equals(b[b.trans_lz_to.isin(WA)].reset_index(drop=True))   # WA as v3
    t = pd.read_csv(d / "trans_import_tranche.csv")
    assert t.PERIOD.tolist() == [2035] and t.tranche_free_mwh_per_yr.iat[0] == 49.399e6
    assert t.tranche_cost_per_mwh.iat[0] == pytest.approx(67.8 * 0.428, abs=1e-4)
    dirs = pd.read_csv(d / "trans_import_tranche_dirs.csv")
    ca_v3 = b[b.trans_lz_to.isin(CA)]
    assert set(zip(dirs.trans_lz_from, dirs.trans_lz_to)) == set(zip(ca_v3.trans_lz_from, ca_v3.trans_lz_to))
    info = pd.read_csv(d / "trans_import_tranche_info.csv").iloc[0]
    assert info["mode"] == "two_tranche" and "PLACEHOLDER" in info.source and info.free_mwh_per_yr == 49.399e6
    assert any("(two_tranche)" in x for x in logs)
    q, _ = _write(built, {}, "tt_default")                                               # the S0 default
    for f in ("trans_import_cost.csv", "trans_import_tranche.csv", "trans_import_tranche_dirs.csv",
              "trans_import_tranche_info.csv"):
        assert (q.parent / f).read_bytes() == (d / f).read_bytes(), f
    for name, s0 in (("tt_legacy", {"ca_wa_carbon": {"mode": "legacy"}}),
                     ("tt_share", {"ca_wa_carbon": {"import_charge": "unspecified_share"}})):
        q, _ = _write(built, s0, name)
        assert not (q.parent / "trans_import_tranche.csv").exists() and not (q.parent / "trans_import_tranche_dirs.csv").exists()
    assert s0prod.ca_wa_settings({})["import_charge"] == "two_tranche"
    with pytest.raises(ValueError, match="free_mwh_per_yr"):
        s0prod.ca_wa_settings({"ca_wa_carbon": {"import_charge": "two_tranche", "two_tranche": {"free_mwh_per_yr": -1}}})


def test_toy_two_tranche_charge(tmp_path):
    """Switch toy (South = the CA zone; it imports most): zero charge below the tranche, full rate on every MWh above
    it, and the total cost continuous in the tranche size (no jump at the kink)."""
    from toyutil import toy_inputs
    rate = round(53.4 * 0.428, 4)

    def run(name, free):
        def edit(inp):
            pd.DataFrame([{"CO2_PROGRAM": "ETS 2", "PERIOD": p, "LOAD_ZONE": "South", "carbon_cap_tco2_per_yr": 0.0,
                           "carbon_cost_dollar_per_tco2": 53.4, "carbon_floor_price_dollar_per_tco2": 0.0}
                          for p in (2020, 2030)]).to_csv(inp / "carbon_policies_regional.csv", index=False)
            s0prod.write_ca_wa_import_cost(inp, {"ca_wa_carbon": {"import_charge": "two_tranche",
                                                                  "two_tranche": {"free_mwh_per_yr": free}}},
                                           {2020: {}, 2030: {}}, lambda x: None)
        r = toy_inputs(tmp_path, name, ["trans_hurdle_cost"], edit)
        res = subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--include-module", "mods.trans_hurdle_cost"],
                             cwd=r, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(r)})
        assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
        t = pd.read_csv(r / "outputs/trans_import_tranche_results.csv").set_index("PERIOD").loc[2030]
        ci = pd.read_csv(r / "outputs/costs_itemized.csv")
        comp = ci[(ci.Component == "TxImportTrancheCost") & (ci.PERIOD == 2030)].AnnualCost_Real.iat[0]
        assert (r / "outputs/trans_import_tranche_info.csv").exists()
        assert not (r / "inputs/trans_import_cost.csv").exists()                      # no WA zone: no per-MWh file
        return t, comp, float(open(r / "outputs/total_cost.txt").read())

    big, comp_big, tot_big = run("free_big", 1e12)                                       # never binding
    assert big.excess_mwh_per_yr == pytest.approx(0, abs=1e-6) and big.annual_cost == pytest.approx(0, abs=1e-6)
    assert comp_big == pytest.approx(0, abs=1e-6) and big.delivered_mwh_per_yr > 0
    zero, comp0, tot0 = run("free_zero", 0.0)                                            # every MWh at the full rate
    assert zero.cost_per_mwh == pytest.approx(rate)
    assert zero.excess_mwh_per_yr == pytest.approx(zero.delivered_mwh_per_yr, rel=1e-6)
    assert zero.annual_cost == pytest.approx(rate * zero.delivered_mwh_per_yr, rel=1e-6) and comp0 == pytest.approx(zero.annual_cost, rel=1e-6)
    d_free = big.delivered_mwh_per_yr                                                   # imports when they are free
    half, _, tot_half = run("free_half", d_free / 2)                                     # above the tranche: full rate
    assert half.annual_cost == pytest.approx(rate * max(0.0, half.delivered_mwh_per_yr - d_free / 2), rel=1e-6)
    assert half.delivered_mwh_per_yr >= d_free / 2 - 1e-6
    at, _, tot_at = run("free_at", d_free)                                               # at the kink: no charge
    assert at.annual_cost == pytest.approx(0, abs=1e-3) and tot_at == pytest.approx(tot_big, rel=1e-9)
    eps = 1000.0
    below, _, tot_below = run("free_below", d_free - eps)                                 # continuity across the kink
    assert 0 <= tot_below - tot_at <= rate * eps * 30 + 1e-6                             # (x discounting over the period)
    assert tot_big <= tot_half <= tot0                                                   # monotone in the tranche
