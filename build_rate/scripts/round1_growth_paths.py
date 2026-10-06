"""Post-2030 growth paths of the build rate R anchored to the first-round S0-S4 caps (CHANGES §76).

Round 1 (data/reference/round1/: cap_derivation_methodology.py and cap_derivation_input_data.csv, EIA-860 state
capacity 2009-2025, from Switch_cap_methodology.zip) set cumulative wind and solar capacity caps. This converts them to
annual additions A(y) = C(y) - C(y-1) and to the growth of additions g(y) = A(y) / A(y-1) - 1, the growth applied to R
after the near-term years (config.yaml growth_paths):

  central = round 1's Quadratic Trend (S0/S1): the national quadratic fit of cap_derivation_methodology.py (wind
            2009-2025, solar 2015-2025), which reproduces the implemented caps (wind 198.2 / 221.3 / 283.9, solar
            243.7 / 321.5 / 566.6 GW at 2028 / 2030 / 2035), extended to 2045 by the same polynomial.
  high    = round 1's Implied Rate (S2): the implemented S2 caps compound at the Implied Rates implied by the
            implemented 2030 and 2035 values (wind 9.10% / 8.27%, solar 22.28% / 14.84%). Compounding cumulative
            capacity at r makes annual additions grow at r too, so the growth of additions in each five-year period is
            that period's rate. Extended to 2045 by round 1's own method for two further periods: each state's quadratic
            projected to 2040 and 2045, annualised 2035-40 and 2040-45 growth, trimmed top-quartile mean. The literal
            year-on-year additions series (also printed) drops at each period boundary (solar -18.6% in 2031) because
            round 1 steps the compounding rate down; that artefact is not carried into R.

The write-up's printed solar formula (+1.607x) does not reproduce the implemented caps (about 285 GW in 2028 against
243.7); the script's fit (-1.607x) does, so the script and the implemented values are used.

Storage had no round-1 cap. Two candidate rules are printed: follow solar's path (proposed), or storage's own
2015-2025 trend (round 1's national quadratic on EIA-860M storage additions, build_rate/outputs/base_rates.csv).

usage (build_rate/): python scripts/round1_growth_paths.py   (prints the paths and a config.yaml snippet)
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
R1 = ROOT / "data/reference/round1"
YEARS = range(2031, 2046)
IMPLEMENTED = {  # round 1's implemented caps, GW (technical note 3.1; cap_derivation_methodology.py output)
    "wind_onshore": {"S0": {2028: 198.2, 2030: 221.3, 2035: 283.9}, "S2": {2028: 198.2, 2030: 235.9, 2035: 351.0}},
    "solar": {"S0": {2028: 243.7, 2030: 321.5, 2035: 566.6}, "S2": {2028: 243.7, 2030: 364.4, 2035: 727.8}},
}


def round1():
    spec = importlib.util.spec_from_file_location("round1", R1 / "cap_derivation_methodology.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def quadratic_caps(m, wind, solar) -> dict:
    """Round 1's national quadratic, by group, as {year: GW} for 2027-2045."""
    q = m.quad_national_trend(wind, solar)
    out = {}
    for tech, g in (("Wind", "wind_onshore"), ("Solar", "solar")):
        c, y0 = q[tech]["coeffs"], q[tech]["fit_start"]
        out[g] = {y: float(np.polyval(c, y - y0)) for y in range(2027, 2046)}
    return out


def extended_implied_rates(m, wind, solar) -> dict:
    """Round 1's Implied Rate method for 2035-40 and 2040-45: each state's quadratic (round 1's fit windows) projected,
    annualised growth per period, trimmed top-quartile mean."""
    out = {}
    for tech, g, df in (("Wind", "wind_onshore", wind), ("Solar", "solar", solar)):
        y0, y1 = m.FIT_WINDOW[tech]
        years = list(range(y0, y1 + 1))
        rates = {"2035_2040": [], "2040_2045": []}
        for st in df.index:
            s = df.loc[st, years].values.astype(float)
            if np.all(s == 0):
                continue
            c, _ = m.fit_quadratic(years, s)
            p = {y: np.polyval(c, y - y0) for y in (2035, 2040, 2045)}
            r1 = m.annualized_rate(p[2035], p[2040], 5)
            r2 = m.annualized_rate(p[2040], p[2045], 5)
            if r1 is not None:
                rates["2035_2040"].append(r1)
            if r2 is not None:
                rates["2040_2045"].append(r2)
        out[g] = {k: float(m.trimmed_top_quartile_mean(v)[0]) for k, v in rates.items()}
    return out


def s2_caps(g: str, ext: dict) -> dict:
    """S2 cumulative caps 2028-2045: implemented 2028 / 2030 / 2035 (compounding between at their implied rates), then
    the extended rates."""
    imp = IMPLEMENTED[g]["S2"]
    r1 = (imp[2030] / imp[2028]) ** (1 / 2) - 1
    r2 = (imp[2035] / imp[2030]) ** (1 / 5) - 1
    c = {2028: imp[2028]}
    for y in range(2029, 2046):
        r = r1 if y <= 2030 else r2 if y <= 2035 else ext[g]["2035_2040"] if y <= 2040 else ext[g]["2040_2045"]
        c[y] = c[y - 1] * (1 + r)
    return c


def growth_of_additions(caps: dict) -> dict:
    a = {y: caps[y] - caps[y - 1] for y in range(2030, 2046)}
    return {y: a[y] / a[y - 1] - 1 for y in YEARS}


def storage_own_trend() -> dict:
    """Round 1's national quadratic on cumulative EIA-860M storage additions 2015-2025 (base_rates.csv), additions
    growth 2031-2045."""
    b = pd.read_csv(ROOT / "outputs/base_rates.csv")
    s = b[(b.group == "storage") & (b.region == "national")].set_index("year")["mw"].sort_index() / 1e3
    cum = s.cumsum()
    years = list(cum.index)
    c = np.polyfit(np.array(years) - years[0], cum.values, 2)
    caps = {y: float(np.polyval(c, y - years[0])) for y in range(2029, 2046)}
    return growth_of_additions(caps)


def paths() -> tuple[dict, dict]:
    m = round1()
    wind, solar = m.load_data(R1 / "cap_derivation_input_data.csv")
    q = quadratic_caps(m, wind, solar)
    for g in ("wind_onshore", "solar"):          # the quadratic reproduces the implemented S0/S1 caps
        for y, v in IMPLEMENTED[g]["S0"].items():
            assert abs(q[g][y] - v) < 0.06, (g, y, q[g][y], v)
    ext = extended_implied_rates(m, wind, solar)
    central = {g: growth_of_additions(q[g]) for g in ("wind_onshore", "solar")}
    high = {}
    for g in ("wind_onshore", "solar"):
        imp = IMPLEMENTED[g]["S2"]
        r2 = (imp[2035] / imp[2030]) ** (1 / 5) - 1
        high[g] = {y: r2 if y <= 2035 else ext[g]["2035_2040"] if y <= 2040 else ext[g]["2040_2045"] for y in YEARS}
    info = {"extended_implied_rates": ext, "quadratic_caps": q,
            "s2_caps": {g: s2_caps(g, ext) for g in ("wind_onshore", "solar")},
            "high_literal": {g: growth_of_additions(s2_caps(g, ext)) for g in ("wind_onshore", "solar")},
            "storage_own_trend": storage_own_trend()}
    return {"central": central, "high": high}, info


if __name__ == "__main__":
    p, info = paths()
    print("extended implied rates (round 1 method, 2035-40 / 2040-45):", info["extended_implied_rates"])
    for lv, d in p.items():
        print(f"\n{lv}: growth of annual additions")
        print(pd.DataFrame(d).round(4).to_string())
    print("\nhigh, literal year-on-year additions (not used):")
    print(pd.DataFrame(info["high_literal"]).round(4).to_string())
    print("\nstorage own 2015-2025 quadratic trend:", {y: round(v, 4) for y, v in info["storage_own_trend"].items()})
    print("\nconfig.yaml growth_paths snippet:")
    for lv, d in p.items():
        print(f"  {lv}:")
        for g in ("wind_onshore", "solar"):
            print(f"    {g}: {{" + ", ".join(f"{y}: {d[g][y]:.4f}" for y in YEARS) + "}")
        print("    storage: solar          # PROPOSED: follow solar's path")
