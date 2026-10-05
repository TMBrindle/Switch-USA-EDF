"""S0 fuel prices, STEO -> AEO2026 (CHANGES §67), on the pinned EIA tables (s0_workflow/data/fuel/)."""
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from s0_workflow import fuel_prices as fp  # noqa: E402
from s0_workflow import production as s0prod  # noqa: E402

PERIODS = [(2028, 2026, 2028), (2030, 2029, 2030), (2035, 2031, 2035), (2040, 2036, 2040), (2045, 2041, 2045)]
MODES = ("steo_aeo", "steo_aeo_low_supply", "steo_aeo_high_supply")


def test_pinned_sources():
    src = yaml.safe_load(open(REPO / "s0_workflow/data/fuel/SOURCES.yml"))
    assert src["steo"]["edition"] == "September 2026" and src["steo"]["months"] == "2022-01..2027-12"
    labels = {k: (v["scenario"], v["scenario_label"], v["release"]) for k, v in src["aeo2026"].items()}
    assert labels == {"aeo_ref_tab3": ("cb2026", "Counterfactual Baseline", "April 2026"),
                      "aeo_lowogs_tab3": ("lowogs", "Low Oil and Gas Supply", "April 2026"),
                      "aeo_highogs_tab3": ("highogs", "High Oil and Gas Supply", "April 2026")}
    e = pd.read_csv(REPO / "s0_workflow/data/fuel/aeo2026_power_fuel_emm.csv", dtype={"emm": str})
    assert e.emm.nunique() == 25 and e.year.min() == 2025 and e.year.max() == 2050
    # the EMM table's consumption-weighted national price is close to AEO's national Table 3 value (within 3%; 2.5% in
    # 2030 for gas). The regional scaling makes the weighted regional prices follow the path exactly.
    n = pd.read_csv(REPO / "s0_workflow/data/fuel/aeo2026_power_fuel_national.csv").query("case == 'reference'")
    for fuel in ("naturalgas", "coal"):
        for y in (2030, 2035, 2045):
            g = e[(e.fuel == fuel) & (e.year == y) & (e.price_2025usd_per_mmbtu > 0)]
            w = (g.price_2025usd_per_mmbtu * g.consumption_quads).sum() / g.consumption_quads.sum()
            assert w == pytest.approx(n[(n.fuel == fuel) & (n.year == y)].price_2025usd_per_mmbtu.iloc[0], rel=0.03)


def test_fetch_check_offline():
    cache = REPO / "s0_workflow/data/raw/fuel"
    if not (cache / "STEO_m.steo_m.xlsx").exists():
        pytest.skip("EIA downloads not cached (python s0_workflow/scripts/fetch_fuel_prices_eia.py --check)")
    sys.path.insert(0, str(REPO / "s0_workflow/scripts"))
    import fetch_fuel_prices_eia as f
    assert f.main(["--check", "--offline"]) == 0


def test_national_path_rules():
    cpi = fp.cpi_annual()
    s = fp.steo_annual()
    a = pd.read_csv(REPO / "s0_workflow/data/fuel/aeo2026_power_fuel_national.csv")
    for mode, case in (("steo_aeo", "reference"), ("steo_aeo_low_supply", "low_ogs"), ("steo_aeo_high_supply", "high_ogs")):
        p = fp.national_path(mode).set_index(["fuel", "year"]).price
        for fuel in ("naturalgas", "coal"):
            # 2026-27: STEO, consumption-weighted, nominal -> 2024 $
            for y in (2026, 2027):
                r = s[(s.fuel == fuel) & (s.year == y)].iloc[0]
                assert p[(fuel, y)] == pytest.approx(r.price_nominal * cpi[2024] / cpi[y])
            # 2035 on: the AEO case, 2025 $ -> 2024 $
            for y in (2035, 2040, 2045, 2050):
                v = a[(a.case == case) & (a.fuel == fuel) & (a.year == y)].price_2025usd_per_mmbtu.iloc[0]
                assert p[(fuel, y)] == pytest.approx(v * cpi[2024] / cpi[2025])
            # 2028-34: a straight line from STEO 2027 to AEO 2035
            step = (p[(fuel, 2035)] - p[(fuel, 2027)]) / 8
            for y in range(2028, 2035):
                assert p[(fuel, y)] == pytest.approx(p[(fuel, 2027)] + step * (y - 2027))
    # the STEO start is the same in every case
    starts = {m: fp.national_path(m).query("year <= 2027").price.round(9).tolist() for m in MODES}
    assert starts["steo_aeo"] == starts["steo_aeo_low_supply"] == starts["steo_aeo_high_supply"]
    # the weighting: STEO's monthly prices weighted by monthly power-sector burn (gas: bcf/d x days)
    m = pd.read_csv(REPO / "s0_workflow/data/fuel/steo_power_fuel_monthly.csv")
    g = m[m.month.str.startswith("2026")]
    days = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
    w = g.ngepcon_bcfd.values * days
    assert s[(s.fuel == "naturalgas") & (s.year == 2026)].price_nominal.iloc[0] == pytest.approx(
        (g.ngeudus.values * w).sum() / w.sum())


def test_regional_scaling_and_stages():
    zp = fp.zone_prices("steo_aeo")
    cw = pd.read_csv(REPO / "s0_workflow/specs/fuel/zone_emm.csv", dtype={"emm": str})
    h = pd.read_csv(REPO / "hierarchy.csv")
    assert set(cw.zone) == set(h[h.country == "USA"].ba) and cw.emm.nunique() == 25
    assert cw.groupby("zone").share.sum().round(9).eq(1).all() and (cw.zone.value_counts() > 1).sum() == 5
    # the source: growth_rates/crosswalk_v7.csv (ollie/edf-baseline)
    assert cw[cw.zone == "p89"].set_index("emm").share.round(4).to_dict() == {"16": 0.5242, "15": 0.4758}
    path = fp.national_path("steo_aeo").set_index(["fuel", "year"]).price
    e = pd.read_csv(REPO / "s0_workflow/data/fuel/aeo2026_power_fuel_emm.csv", dtype={"emm": str})
    # the consumption-weighted national average of the regional prices is the path, every year
    for fuel in ("naturalgas", "coal"):
        for y in (2026, 2030, 2035, 2045):
            g = e[(e.fuel == fuel) & (e.year == y) & (e.price_2025usd_per_mmbtu > 0) & (e.consumption_quads > 0)]
            f = fp.regional_factors().query("fuel == @fuel and year == @y").set_index("emm").factor
            assert (f[g.emm] * g.consumption_quads.values).sum() / g.consumption_quads.sum() == pytest.approx(1.0)
            r = zp[(zp.fuel == fuel) & (zp.year == y)].set_index("zone")
            assert r.price["p60"] == pytest.approx(path[(fuel, y)] * f["01"])          # ERCOT zone: TRE factor
            assert r.price["p89"] == pytest.approx(path[(fuel, y)] * (0.5242 * f["16"] + 0.4758 * f["15"]), rel=1e-4)
    # coal in a region AEO no longer prices keeps its nearest year's factor; never priced: x2
    f = fp.regional_factors().set_index(["fuel", "emm", "year"])
    assert f.at[("coal", "05", 2035), "basis"] == "nearest AEO year" and f.at[("coal", "05", 2035), "factor"] > 0
    assert f.at[("coal", "21", 2030), "factor"] == 2.0
    # stage = mean over the period's years
    per = pd.DataFrame(PERIODS, columns=["INVESTMENT_PERIOD", "period_start", "period_end"])
    sp = fp.stage_prices("steo_aeo", per, ["p60", "p128"]).set_index(["zone", "fuel", "period"]).price
    z = zp[zp.zone == "p60"].set_index(["fuel", "year"]).price
    assert sp[("p60", "naturalgas", 2028)] == pytest.approx(z["naturalgas"][[2026, 2027, 2028]].mean())
    assert sp[("p60", "coal", 2035)] == pytest.approx(z["coal"][list(range(2031, 2036))].mean())
    nat = fp.national_by_period("steo_aeo", PERIODS).set_index(["fuel", "period"]).price
    assert nat[("naturalgas", 2030)] == pytest.approx(path["naturalgas"][[2029, 2030]].mean())


def _case(folder):
    pd.DataFrame(PERIODS, columns=["INVESTMENT_PERIOD", "period_start", "period_end"]).to_csv(
        folder / "periods.csv", index=False)
    rows = [(z, fuel, p, v) for z in ("p60", "p11", "p135") for fuel, v in (("naturalgas", 5.0), ("coal", 2.5),
                                                                            ("distillate", 20.0))
            for p, _, _ in PERIODS]
    pd.DataFrame(rows, columns=["load_zone", "fuel", "period", "fuel_cost"]).to_csv(folder / "fuel_cost.csv", index=False)


def test_case_writer(tmp_path):
    _case(tmp_path)
    lines = []
    s0 = {"enabled": True, "fuel_prices": {"mode": "steo_aeo_low_supply"}}
    fp.write_case_inputs(tmp_path, s0, {}, lines.append)
    fc = pd.read_csv(tmp_path / "fuel_cost.csv").set_index(["load_zone", "fuel", "period"]).fuel_cost
    per = pd.DataFrame(PERIODS, columns=["INVESTMENT_PERIOD", "period_start", "period_end"])
    sp = fp.stage_prices("steo_aeo_low_supply", per, ["p60", "p11"]).set_index(["zone", "fuel", "period"]).price
    for z in ("p60", "p11"):
        for fuel in ("naturalgas", "coal"):
            for p, _, _ in PERIODS:
                assert fc[(z, fuel, p)] == pytest.approx(sp[(z, fuel, p)])
    assert (fc.xs("distillate", level="fuel") == 20.0).all()                     # other fuels: the case's
    assert (fc.loc["p135"].xs("naturalgas", level="fuel") == 5.0).all()          # Mexico zone: no EMM region
    rep = pd.read_csv(tmp_path / "fuel_prices_by_stage.csv")
    assert set(rep.columns) == {"zone", "fuel", "price", "period", "national"} and "p135" in lines[0]
    # hist5 (the regression case) and non-S0: nothing changes
    _case(tmp_path)
    before = (tmp_path / "fuel_cost.csv").read_bytes()
    fp.write_case_inputs(tmp_path, {"enabled": True, "fuel_prices": {"mode": "hist5"}}, {}, lines.append)
    fp.write_case_inputs(tmp_path, {"enabled": False, "fuel_prices": {"mode": "steo_aeo"}}, {}, lines.append)
    assert (tmp_path / "fuel_cost.csv").read_bytes() == before
    # a fuel name that isn't there stops the build
    fc0 = pd.read_csv(tmp_path / "fuel_cost.csv")
    fc0[fc0.fuel != "coal"].to_csv(tmp_path / "fuel_cost.csv", index=False)
    with pytest.raises(ValueError, match="no rows for \\['coal'\\]"):
        fp.write_case_inputs(tmp_path, {"enabled": True, "fuel_prices": {"mode": "steo_aeo"}}, {}, lines.append)
    with pytest.raises(ValueError, match="mode"):
        fp.settings({"fuel_prices": {"mode": "aeo2025"}})


def test_settings_axis_column_and_regression():
    S0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    ax = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]
    assert S0["fuel_prices"]["mode"] == "steo_aeo"
    assert ax["fuel_prices"] == {"steo_aeo": None,
                                 "steo_aeo_low_supply": {"s0_production": {"fuel_prices": {"mode": "steo_aeo_low_supply"}}},
                                 "steo_aeo_high_supply": {"s0_production": {"fuel_prices": {"mode": "steo_aeo_high_supply"}}},
                                 "hist5": {"s0_production": {"fuel_prices": {"mode": "hist5"}}}}
    reg = s0prod.deep_merge(copy.deepcopy(S0), ax["s0_production"]["on_pgdays"]["s0_production"])
    assert not fp.active(reg) and fp.active(dict(S0, enabled=True))
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    assert list(si.columns)[-1] == "fuel_prices"
    assert set(si[si.fuel_prices != "steo_aeo"].case_id) == {"s4x1_S0_tx_2035_fuel_low", "s4x1_S0_tx_2035_fuel_high",
                                                             "s4x1_S0_tx_2035_fuel_hist5"}
    base = si[si.case_id == "s4x1_S0_tx_2035"].iloc[0]
    for k, v in (("fuel_low", "steo_aeo_low_supply"), ("fuel_high", "steo_aeo_high_supply"), ("fuel_hist5", "hist5")):
        r = si[si.case_id == f"s4x1_S0_tx_2035_{k}"].iloc[0]
        assert [c for c in si.columns if c != "case_id" and str(r[c]) != str(base[c])] == ["fuel_prices"]
        assert r.fuel_prices == v
