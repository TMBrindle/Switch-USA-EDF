"""S0 fuel prices (CHANGES §67): natural gas and coal delivered to the electric power sector on a path from EIA's
latest Short-Term Energy Outlook to AEO2026, regionalised by AEO2026's EMM regions. Setting:
s0_production.fuel_prices (pg/settings/s0_production.yml); data pinned by s0_workflow/scripts/fetch_fuel_prices_eia.py
in s0_workflow/data/fuel/ (SOURCES.yml has the editions and release dates).

mode (axis `fuel_prices`, scenario_inputs.csv column of that name):
  hist5                 the case's fuel_price_forecast as it is (hist5 / hist5_high_gas: flat 2020-24 SEDS state prices,
                        gas +15% for hist5_high_gas); nothing here runs. The regression case.
  steo_aeo              (S0 default) AEO2026 reference case (EIA's "Counterfactual Baseline", formerly Reference)
  steo_aeo_low_supply   AEO2026 Low Oil and Gas Supply case
  steo_aeo_high_supply  AEO2026 High Oil and Gas Supply case

National path, 2024 $/MMBtu, for each fuel (naturalgas, coal):
  - steo_years (2026, 2027): the STEO electric-power-sector delivered price (NGEUDUS, CLEUDUS), annual average weighted
    by the month's power-sector consumption (NGEPCON x days, CLEPCON_TON), nominal -> 2024 $ with STEO's CPI-U
    (CICPIUS, annual mean of the months);
  - 2028 to glide_to - 1: linear from STEO's last year to the AEO case's glide_to (2035) value;
  - glide_to on: the AEO case's Table 3 Electric Power price (2025 $ -> 2024 $ with STEO's CPI-U 2024 / 2025).
The STEO start and the glide are the same in every case; only the AEO end differs.

Regional: zone price(y) = path(y) x AEO2026 reference EMM-region price(y) / AEO consumption-weighted national price(y),
so the consumption-weighted national average of the regional prices follows the path each year. Zones -> EMM regions:
s0_workflow/specs/fuel/zone_emm.csv. A region with no price in a year (no consumption: e.g. coal after its plants
retire) takes its factor from the nearest year that has one; a region never priced takes missing_factor (2.0, the
hist5 convention for states without prices: no cheap restart). The side cases publish national tables only, so they
use the reference case's regional factors.

Stage value: the mean over the period's years (periods.csv period_start..period_end; 2028 = 2026-28). The case's
fuel_cost.csv rows for these fuels in US zones are replaced; other fuels (distillate, uranium) and other zones keep the
case's fuel_price_forecast. Report: <case>/fuel_prices_by_stage.csv and a log line.
"""
from __future__ import annotations

import calendar
import copy
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "s0_workflow/data/fuel"
MODES = {"hist5": None, "steo_aeo": "reference", "steo_aeo_low_supply": "low_ogs", "steo_aeo_high_supply": "high_ogs"}
DEFAULTS = {
    "mode": "hist5",
    "steo_years": [2026, 2027],
    "glide_to": 2035,
    "fuels": ["naturalgas", "coal"],
    "dollar_year": 2024,
    "missing_factor": 2.0,
    "crosswalk": "s0_workflow/specs/fuel/zone_emm.csv",
    "data_dir": "s0_workflow/data/fuel",
}
STEO_COLS = {"naturalgas": ("ngeudus", "ngepcon_bcfd"), "coal": ("cleudus", "clepcon_mst")}


def settings(s0: dict) -> dict:
    f = copy.deepcopy(DEFAULTS)
    f.update((s0 or {}).get("fuel_prices") or {})
    if f["mode"] not in MODES:
        raise ValueError(f"s0_production.fuel_prices.mode must be one of {sorted(MODES)}, not {f['mode']!r}")
    return f


def active(s0: dict) -> bool:
    return bool((s0 or {}).get("enabled")) and settings(s0)["mode"] != "hist5"


def _data(f: dict, name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(REPO / f["data_dir"] / name, **kw)


def steo_annual(f: dict | None = None) -> pd.DataFrame:
    """year, fuel, price_nominal (consumption-weighted), cpi (annual mean)."""
    f = f or DEFAULTS
    m = _data(f, "steo_power_fuel_monthly.csv")
    p = pd.PeriodIndex(m.month, freq="M")
    m["year"] = p.year
    days = np.array([calendar.monthrange(y, mo)[1] for y, mo in zip(p.year, p.month)])
    rows = []
    for fuel, (price, cons) in STEO_COLS.items():
        w = m[cons] * (days if cons.endswith("bcfd") else 1.0)
        for y, g in m.assign(w=w).groupby("year"):
            ok = g[price].notna() & g.w.notna()
            if ok.sum() < 12:
                continue
            rows.append({"year": int(y), "fuel": fuel,
                         "price_nominal": float((g[price] * g.w)[ok].sum() / g.w[ok].sum())})
    out = pd.DataFrame(rows)
    cpi = m.groupby("year").cicpius.mean()
    out["cpi"] = out.year.map(cpi)
    return out


def cpi_annual(f: dict | None = None) -> pd.Series:
    m = _data(f or DEFAULTS, "steo_power_fuel_monthly.csv")
    return m.groupby(pd.PeriodIndex(m.month, freq="M").year).cicpius.mean()


def national_path(mode: str, f: dict | None = None, last_year: int = 2050) -> pd.DataFrame:
    """year, fuel, price (2024 $/MMBtu), source."""
    f = f or DEFAULTS
    case = MODES[mode]
    if case is None:
        raise ValueError("hist5 has no path")
    cpi = cpi_annual(f)
    dy = int(f["dollar_year"])
    s = steo_annual(f)
    a = _data(f, "aeo2026_power_fuel_national.csv")
    a = a[a.case == case]
    to_dy_2025 = cpi[dy] / cpi[2025]
    steo_years = sorted(int(y) for y in f["steo_years"])
    g = int(f["glide_to"])
    rows = []
    for fuel in f["fuels"]:
        sv = {int(r.year): r.price_nominal * cpi[dy] / r.cpi for r in s[s.fuel == fuel].itertuples()}
        av = {int(r.year): r.price_2025usd_per_mmbtu * to_dy_2025 for r in a[a.fuel == fuel].itertuples()}
        missing = [y for y in steo_years if y not in sv]
        if missing:
            raise ValueError(f"fuel_prices: STEO has no full year {missing} for {fuel}")
        y0, p0, p1 = steo_years[-1], sv[steo_years[-1]], av[g]
        for y in range(steo_years[0], last_year + 1):
            if y in steo_years:
                rows.append((y, fuel, sv[y], "STEO"))
            elif y < g:
                rows.append((y, fuel, p0 + (p1 - p0) * (y - y0) / (g - y0), f"glide STEO {y0} -> AEO {g}"))
            else:
                rows.append((y, fuel, av[y], f"AEO2026 {case}"))
    return pd.DataFrame(rows, columns=["year", "fuel", "price", "source"])


def regional_factors(f: dict | None = None) -> pd.DataFrame:
    """emm, year, fuel, factor = regional price / consumption-weighted national (AEO2026 reference)."""
    f = f or DEFAULTS
    e = _data(f, "aeo2026_power_fuel_emm.csv", dtype={"emm": str})
    out = []
    for (fuel, y), g in e.groupby(["fuel", "year"]):
        ok = (g.price_2025usd_per_mmbtu > 0) & (g.consumption_quads > 0)
        nat = (g.price_2025usd_per_mmbtu * g.consumption_quads)[ok].sum() / g.consumption_quads[ok].sum()
        out.append(pd.DataFrame({"emm": g.emm, "year": y, "fuel": fuel,
                                 "factor": np.where(g.price_2025usd_per_mmbtu > 0, g.price_2025usd_per_mmbtu / nat,
                                                    np.nan)}))
    r = pd.concat(out, ignore_index=True).sort_values(["fuel", "emm", "year"])
    filled = []
    for (fuel, emm), g in r.groupby(["fuel", "emm"]):
        g = g.set_index("year")
        v = g.factor.copy()
        if v.notna().any():
            v = v.interpolate(method="nearest", limit_area="inside").ffill().bfill() if v.notna().sum() > 1 \
                else v.fillna(v.dropna().iloc[0])
            basis = np.where(g.factor.notna(), "AEO", "nearest AEO year")
        else:
            v = v.fillna(float(f["missing_factor"]))
            basis = np.full(len(v), f"no AEO price: x{float(f['missing_factor']):g}")
        filled.append(pd.DataFrame({"emm": emm, "year": g.index, "fuel": fuel, "factor": v.values, "basis": basis}))
    return pd.concat(filled, ignore_index=True)


def crosswalk(f: dict | None = None) -> dict:
    x = pd.read_csv(REPO / (f or DEFAULTS)["crosswalk"], dtype={"emm": str})
    return dict(zip(x.zone, x.emm))


def zone_prices(mode: str, zones=None, f: dict | None = None) -> pd.DataFrame:
    """zone, year, fuel, price (2024 $/MMBtu), emm."""
    f = f or DEFAULTS
    path = national_path(mode, f)
    fac = regional_factors(f)
    cw = crosswalk(f)
    zones = [z for z in (zones if zones is not None else cw) if z in cw]
    z = pd.DataFrame({"zone": zones, "emm": [cw[x] for x in zones]})
    out = z.merge(fac, on="emm").merge(path[["year", "fuel", "price"]], on=["year", "fuel"])
    out["price"] = out.price * out.factor
    return out[["zone", "emm", "year", "fuel", "price", "factor", "basis"]]


def stage_prices(mode: str, periods: pd.DataFrame, zones=None, f: dict | None = None) -> pd.DataFrame:
    """zone, fuel, period, price: the mean over the period's years."""
    zp = zone_prices(mode, zones, f)
    rows = []
    for r in periods.itertuples():
        yrs = range(int(r.period_start), int(r.period_end) + 1)
        g = zp[zp.year.isin(yrs)]
        n = g.groupby(["zone", "fuel"]).year.nunique()
        if (n < len(yrs)).any():
            raise ValueError(f"fuel_prices: no price for every year of {r.INVESTMENT_PERIOD} ({yrs[0]}-{yrs[-1]})")
        m = g.groupby(["zone", "fuel"]).price.mean().reset_index()
        m["period"] = int(r.INVESTMENT_PERIOD)
        rows.append(m)
    return pd.concat(rows, ignore_index=True)


def national_by_period(mode: str, periods, f: dict | None = None) -> pd.DataFrame:
    """fuel, period, price: the national path's mean over each period's years."""
    p = national_path(mode, f)
    return pd.DataFrame([{"fuel": fuel, "period": int(per), "price": float(
        p[(p.fuel == fuel) & p.year.between(int(a), int(b))].price.mean())}
        for fuel in p.fuel.unique() for per, a, b in periods])


def write_case_inputs(folder: Path, s0: dict, scen_settings_dict: dict, log) -> None:
    if not active(s0):
        return
    f = settings(s0)
    folder = Path(folder)
    per = pd.read_csv(folder / "periods.csv")
    fc = pd.read_csv(folder / "fuel_cost.csv")
    cw = crosswalk(f)
    sp = stage_prices(f["mode"], per, sorted(set(fc.load_zone) & set(cw)), f)
    key = ["load_zone", "fuel", "period"]
    new = sp.rename(columns={"zone": "load_zone", "price": "new"})
    m = fc.merge(new, on=key, how="left")
    hit = m.new.notna()
    covered = fc.fuel.isin(f["fuels"]) & fc.load_zone.isin(cw)
    if (covered & ~hit).any():
        raise ValueError(f"fuel_prices: no new price for {int((covered & ~hit).sum())} fuel_cost rows")
    absent = [x for x in f["fuels"] if not (hit & (fc.fuel == x)).any()]
    if absent:
        raise ValueError(f"fuel_prices: fuel_cost.csv has no rows for {absent} in an EMM-mapped zone "
                         f"(fuels there: {sorted(set(fc.fuel))})")
    fc["fuel_cost"] = np.where(hit, m.new, fc.fuel_cost)
    fc.to_csv(folder / "fuel_cost.csv", index=False)
    nat = national_by_period(f["mode"], list(zip(per.INVESTMENT_PERIOD, per.period_start, per.period_end)), f)
    rep = sp.merge(nat.rename(columns={"price": "national"}), on=["fuel", "period"])
    rep.round(4).to_csv(folder / "fuel_prices_by_stage.csv", index=False)
    other = sorted(set(fc.load_zone[fc.fuel.isin(f["fuels"])]) - set(cw))
    log(f"fuel prices {f['mode']} (STEO {list(f['steo_years'])} -> glide -> AEO2026 {MODES[f['mode']]} from "
        f"{f['glide_to']}; EMM regional): national 2024$/MMBtu "
        + "; ".join(f"{r.fuel} {r.period} {r.price:.2f}" for r in nat.itertuples())
        + f"; {int(hit.sum())} fuel_cost rows replaced ({len(set(fc.load_zone[hit]))} zones)"
        + (f"; zones without an EMM region keep the case's prices: {other}" if other else ""))
