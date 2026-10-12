"""§105: constrained_pipe share path and the post-solve upstream CH4 tool on toy Switch outputs."""
import numpy as np
import pandas as pd
import pytest
from conftest import PKG

from gasnet import load_config, upstream

OUT = PKG / "outputs"
W = 4380.0     # two timepoints per typical year


def _toy(tmp_path, onsite=False):
    fu = [("g1", "1", "naturalgas", 10.0), ("g1", "2", "naturalgas", 10.0),        # VA, gas
          ("g2", "1", "naturalgas_ccs90", 5.0), ("g2", "2", "naturalgas_ccs90", 0.0),  # CA, CCS gas
          ("g3", "1", "coal", 7.0),                                               # VA, not gas
          ("g4", "1", "naturalgas", 3.0), ("g4", "1", "hydrogen", 1.0)]           # VA, gas + H2 blend
    disp = [("g1", "1", "p99", 2030, 1.0, 10.0), ("g1", "2", "p99", 2030, 1.0, 10.0),
            ("g2", "1", "p10", 2030, 0.5, 1.0), ("g2", "2", "p10", 2030, 0.0, 0.0),
            ("g3", "1", "p99", 2030, 0.7, 30.0), ("g4", "1", "p99", 2030, 0.4, 4.0)]
    if onsite:
        fu += [("td_gen", "1", "NaturalGas", 2.0)]                                # on-site gen, already in Switch
        disp += [("td_gen", "1", "p10", 2030, 0.2, 2.0)]
    d = tmp_path / "outputs"
    d.mkdir()
    pd.DataFrame(fu, columns=["GEN_TP_FUELS_1", "GEN_TP_FUELS_2", "GEN_TP_FUELS_3", "GenFuelUseRate"]).to_csv(
        d / "GenFuelUseRate.csv", index=False)
    pd.DataFrame([(g, t, z, p, W, gwh, tco2) for g, t, z, p, gwh, tco2 in disp],
                 columns=["generation_project", "timestamp", "gen_load_zone", "period", "tp_weight_in_year_hrs",
                          "Energy_GWh_typical_yr", "DispatchEmissions_tCO2_per_typical_yr"]).to_csv(d / "dispatch.csv",
                                                                                                    index=False)
    if onsite:
        pd.DataFrame([("dc1", "td_gen", "p10", 1, "1", 2030, "NaturalGas", 2.0, W, 0.106),
                      ("dc1", "td_gen2", "p10", 2, "2", 2030, "NaturalGas", 1.0, W, 0.053)],   # not in Switch files
                     columns=["tracked_demand", "generation_project", "load_zone", "timepoint", "timestamp", "period",
                              "fuel", "fuel_use_mmbtu_per_h", "tp_weight_in_year", "emissions_tco2_per_h"]).to_csv(
            d / upstream.ONSITE, index=False)
    return d


def _expected_kg(zone, year, path="current_rules", level="central", shares="zone_basin_shares.csv"):
    sh = pd.read_csv(OUT / shares)
    s = sh[(sh.zone == zone) & (sh.year == year)]
    d = pd.read_csv(PKG / "data/basin_methane_intensity.csv")
    k = d[(d.segment == "upstream_total") & (d.unit == "kg_ch4_per_mmbtu_delivered") & (d.year == year)
          & (d.path == path)].set_index("basin")[level]
    ts = d[(d.basin == "all") & (d.segment == "transmission_storage")
           & (d.unit == "kg_ch4_per_mmbtu_delivered")][level].iloc[0]
    return float((s.set_index("basin").share * k.reindex(s.basin).fillna(0).values).sum()) + ts


def test_upstream_known_answer(tmp_path):
    d = _toy(tmp_path)
    r = upstream.upstream(d, load_config(), write=True)
    z = r["by_zone"]
    va = z[(z.zone == "p99") & (z.share_path == "aeo") & (z.basis == "measured") & (z.methane_path == "current_rules")
           & (z.level == "central")].iloc[0]
    gas = 10 * W * 2 + 3 * W
    assert va.gas_mmbtu == pytest.approx(gas)
    assert va.gas_mwh == pytest.approx(2000 + 400 * 0.75)                     # g4: 3 of 4 MMBtu/h is gas
    assert va.stack_tco2 == pytest.approx(20 + 4 * 0.75)                      # coal g3 excluded
    assert va.total_tch4 * 1000 == pytest.approx(gas * _expected_kg("p99", 2030), rel=1e-9)
    assert va.kg_ch4_per_mwh == pytest.approx(1000 * va.total_tch4 / va.gas_mwh)
    assert va.tco2e_gwp20 == pytest.approx(va.total_tch4 * 82.5) and va.state == "VA"
    assert (r["out_dir"] / "upstream_ch4_by_state.csv").exists()
    # 24 rows per zone-period: 2 share paths x 2 bases x 2 methane paths x 3 levels
    assert len(z[z.zone == "p99"]) == 2 * 2 * 2 * 3
    inv = z[(z.zone == "p99") & (z.basis == "inventory") & (z.share_path == "aeo") & (z.methane_path == "current_rules")]
    assert inv.total_tch4.nunique() == 3                                      # T&S keeps its low/central/high
    lo, hi = (z[(z.zone == "p10") & (z.basis == "measured") & (z.level == lv) & (z.share_path == "aeo")
                & (z.methane_path == "current_rules")].total_tch4.iloc[0] for lv in ("low", "high"))
    assert lo < hi
    nfr = z[(z.zone == "p99") & (z.basis == "measured") & (z.level == "central") & (z.share_path == "aeo")
            & (z.methane_path == "no_further_reductions")].total_tch4.iloc[0]
    assert nfr > va.total_tch4                                                # 2030 current_rules is below 2024


def test_upstream_onsite_not_double_counted(tmp_path):
    d = _toy(tmp_path, onsite=True)
    r = upstream.upstream(d, load_config(), share_paths=["aeo"], bases=["measured"], methane_paths=["current_rules"],
                          write=False)
    ca = r["by_zone"][(r["by_zone"].zone == "p10") & (r["by_zone"].level == "central")].iloc[0]
    # g2 5 MMBtu/h + td_gen 2 (in Switch files, labelled on-site) + td_gen2 1 (only in the on-site file, added)
    assert ca.gas_mmbtu == pytest.approx((5 + 2 + 1) * W)
    assert ca.onsite_gas_mmbtu == pytest.approx((2 + 1) * W)
    assert ca.gas_mwh == pytest.approx(500 + 200)                             # the added row has no MWh
    assert ca.stack_tco2 == pytest.approx(1 + 2 + 0.053 * W)
    assert any("not in GenFuelUseRate" in f for f in r["flags"])


def test_unlisted_gas_fuel_stops(tmp_path):
    d = _toy(tmp_path)
    f = pd.read_csv(d / "GenFuelUseRate.csv")
    f.loc[len(f)] = ["g9", "1", "landfill_gas", 1.0]
    f.to_csv(d / "GenFuelUseRate.csv", index=False)
    with pytest.raises(ValueError, match="landfill_gas"):
        upstream.upstream(d, load_config(), write=False)


def test_constrained_pipe_path():
    a = pd.read_csv(OUT / "zone_basin_shares.csv")
    c = pd.read_csv(OUT / "zone_basin_shares_constrained_pipe.csv")
    assert list(c.columns) == list(a.columns) and len(c) == len(a)
    m = c.merge(a, on=["zone", "year", "basin"], suffixes=("", "_a"))
    np.testing.assert_allclose(m[m.year <= 2030].share, m[m.year <= 2030].share_a)
    h = a[a.year == 2030].set_index(["zone", "basin"]).share
    late = c[c.year > 2030]
    np.testing.assert_allclose(late.share.values, h.loc[list(zip(late.zone, late.basin))].values)
    assert set(late.method) == {"constrained_pipe_hold_2030"}
    np.testing.assert_allclose(c.groupby(["zone", "year"]).share.sum().values, 1.0, atol=1e-5)


def test_intensity_inventory_follows_measured_path():
    it, ts = upstream.intensities(PKG / "data/basin_methane_intensity.csv")
    t = it[(it.level == "central") & (it.basin == "permian")].set_index(["basis", "methane_path", "year"]).kg_per_mmbtu
    ratio_m = t[("measured", "current_rules", 2035)] / t[("measured", "current_rules", 2024)]
    ratio_i = t[("inventory", "current_rules", 2035)] / t[("inventory", "current_rules", 2024)]
    assert ratio_i == pytest.approx(ratio_m)
    assert t[("inventory", "current_rules", 2024)] == pytest.approx(upstream.kg_from_pct(0.242))
    assert set(it.basin) == {"appalachia", "haynesville", "permian", "eagle_ford", "barnett_other_tx", "midcon_anadarko",
                             "san_juan", "rockies_other", "bakken", "fed_gom", "w_canada", "e_canada", "lng_imports",
                             "other"}
    assert ts["central"] == pytest.approx(0.0376, abs=1e-4)


def test_hold_year_option(tmp_path):
    from gasnet import limitations
    a = pd.read_csv(OUT / "zone_basin_shares.csv", keep_default_na=False)
    with pytest.raises(ValueError, match="hold year"):
        limitations.constrained_pipe(a, 2035)
    c = limitations.constrained_pipe(a, 2028)
    h = a[a.year == 2028].set_index(["zone", "basin"]).share
    late = c[c.year > 2028]
    np.testing.assert_allclose(late.share.values, h.loc[list(zip(late.zone, late.basin))].values)
    assert set(late.method) == {"constrained_pipe_hold_2028"}
    # the tool builds the path on the fly; default (None) reads the committed 2030 file
    d = _toy(tmp_path)
    run = lambda hy: upstream.upstream(d, load_config(), share_paths=["constrained_pipe"], bases=["measured"],
                                       methane_paths=["current_rules"], write=False, hold_year=hy)["by_zone"]
    z30, z24 = run(None), run(2024)
    assert set(z24.share_method) == {"constrained_pipe_hold_2024"} and set(z30.share_method) == {"trace_aeo_additive_balanced"}
    assert not np.allclose(z24.total_tch4.values, z30.total_tch4.values)
    assert z30.total_tch4.values == pytest.approx(run(2030).total_tch4.values)


def _realistic(tmp_path, name, permuted):
    """Plausible plant data (7 MMBtu/MWh, 0.05306 tCO2/MMBtu). permuted: S0 v3.1 dispatch.csv order (weight before
    period), extra columns, and GenFuelUseRate.csv with the value column first."""
    d = tmp_path / name
    d.mkdir()
    rows = []
    for g, z, mw in (("g1", "p99", 100.0), ("g2", "p10", 50.0)):
        for t in ("1", "2"):
            rows.append(dict(generation_project=g, gen_dbid=g, gen_tech="CC", gen_load_zone=z,
                             gen_energy_source="naturalgas", timestamp=t, tp_weight_in_year_hrs=W, period=2030,
                             DispatchGen_MW=mw, Energy_GWh_typical_yr=mw * W / 1000, VariableCost_per_yr=1.0,
                             DispatchEmissions_tCO2_per_typical_yr=7 * mw * W * 0.05306, GenCapacity_MW=2 * mw))
    disp = pd.DataFrame(rows)
    fu = pd.DataFrame({"GEN_TP_FUELS_1": disp.generation_project, "GEN_TP_FUELS_2": disp.timestamp,
                       "GEN_TP_FUELS_3": "naturalgas", "GenFuelUseRate": 7 * disp.DispatchGen_MW})
    if not permuted:
        disp = disp[["generation_project", "timestamp", "gen_load_zone", "period", "tp_weight_in_year_hrs",
                     "Energy_GWh_typical_yr", "DispatchEmissions_tCO2_per_typical_yr"]]
    else:   # Switch writes dispatch.csv with index (generation_project, timestamp) first, then the record order
        disp = disp[["generation_project", "timestamp"] + [c for c in disp if c not in ("generation_project", "timestamp")]]
        fu = fu[["GenFuelUseRate", "GEN_TP_FUELS_3", "GEN_TP_FUELS_1", "GEN_TP_FUELS_2"]].assign(extra=1)
    disp.to_csv(d / "dispatch.csv", index=False)
    fu.to_csv(d / "GenFuelUseRate.csv", index=False)
    return d


def test_column_order_and_extra_columns_do_not_matter(tmp_path):
    kw = dict(share_paths=["aeo"], bases=["measured"], methane_paths=["current_rules"], write=False)
    a = upstream.upstream(_realistic(tmp_path, "std", False), load_config(), **kw)
    b = upstream.upstream(_realistic(tmp_path, "s0", True), load_config(), **kw)
    cols = ["gas_mmbtu", "gas_mwh", "stack_tco2", "total_tch4", "kg_ch4_per_mwh"]
    pd.testing.assert_frame_equal(a["by_zone"][cols], b["by_zone"][cols])
    va = b["by_zone"][(b["by_zone"].zone == "p99") & (b["by_zone"].level == "central")].iloc[0]
    assert va.period == 2030 and va.gas_mmbtu == pytest.approx(7 * 100 * W * 2)
    assert va.gas_mmbtu / va.gas_mwh == pytest.approx(7.0)
    assert a["flags"] == [] and b["flags"] == []                               # plausible: no sanity warning


def test_missing_column_and_sanity_warning(tmp_path):
    d = _realistic(tmp_path, "x", True)
    disp = pd.read_csv(d / "dispatch.csv").drop(columns=["period"])
    disp.to_csv(d / "dispatch.csv", index=False)
    with pytest.raises(ValueError, match=r"expected column\(s\) \['period'\]"):
        upstream.upstream(d, load_config(), write=False)
    with pytest.warns(UserWarning, match="heat rate"):                        # the toy case: 43.8 MMBtu/MWh
        r = upstream.upstream(_toy(tmp_path), load_config(), share_paths=["aeo"], bases=["measured"],
                              methane_paths=["current_rules"], write=False)
    assert any("heat rate" in f for f in r["flags"]) and any("CO2 intensity" in f for f in r["flags"])
