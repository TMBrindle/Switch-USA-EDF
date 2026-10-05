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
    assert list(si.columns)[-1] == "ca_wa_price" and (si.ca_wa_price == "central").all()


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
    s0prod.write_ca_wa_import_cost(out, {}, {2035: {}}, logs.append)
    ic = pd.read_csv(out / "trans_import_cost.csv")
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
