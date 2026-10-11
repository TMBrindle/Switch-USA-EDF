"""Exporting-zone emission factors for the CA/WA import charge (CHANGES §86; s0_production.ca_wa_carbon.import_charge
source_table, or unspecified_share with specified_factor: source_table), from a SOLVED reference case:

  python s0_workflow/scripts/zone_import_factors.py <case outputs dir> [more outputs dirs] --out factors.csv
      [--method average|fossil]

Reads dispatch_zonal_annual_summary.csv (or dispatch_gen_annual_summary.csv) of each outputs dir and writes one row per zone and
period: zone, period, tco2_per_mwh, emissions_t, generation_gwh, method, filled, source.
  average  the zone's emissions / its generation (storage and net-negative rows left out): the average rate.
  fossil   emissions / the generation of rows that emit: the rate of the zone's emitting fleet, a proxy for the
           marginal rate where gas or coal is on the margin (an upper bound on the average; not a true marginal rate,
           which needs duals or a re-solve per zone).
A zone with no generation in a period gets the period's system value (filled = system). For a chain, pass the stages'
outputs dirs in chain order: a period solved by more than one stage (windows) takes the first, the stage that
commits it (--prefer last for the other).

The table is exogenous to the case that uses it: the factors come from another (reference) solve, so the charge stays
linear. To approach a fixed point, rebuild the case with the new table, re-solve and repeat until the factors settle
(each round is a full solve). Factors from a solve are model output for that case, not observed data.
"""
import argparse
from pathlib import Path

import pandas as pd

COLS = ["gen_load_zone", "period", "Energy_GWh_typical_yr", "DispatchEmissions_tCO2_per_typical_yr"]


def factors(summary: pd.DataFrame, method: str = "average") -> pd.DataFrame:
    d = summary.copy()
    if "Store_GWh_typical_yr" in d.columns:
        d = d[~(pd.to_numeric(d.Store_GWh_typical_yr, errors="coerce").fillna(0) > 0)]
    d = d[d.Energy_GWh_typical_yr > 0]
    if method == "fossil":
        d = d[d.DispatchEmissions_tCO2_per_typical_yr > 0]
    elif method != "average":
        raise ValueError(f"method must be average or fossil, not {method!r}")
    g = d.groupby(["gen_load_zone", "period"])[["DispatchEmissions_tCO2_per_typical_yr", "Energy_GWh_typical_yr"]].sum()
    out = g.reset_index().rename(columns={"gen_load_zone": "zone", "DispatchEmissions_tCO2_per_typical_yr": "emissions_t",
                                          "Energy_GWh_typical_yr": "generation_gwh"})
    out["tco2_per_mwh"] = out.emissions_t / (out.generation_gwh * 1e3)
    out["filled"] = ""
    # zones with no (emitting) generation in a period: the period's system value
    sysv = g.groupby(level="period").sum()
    sysv = sysv.DispatchEmissions_tCO2_per_typical_yr / (sysv.Energy_GWh_typical_yr * 1e3)
    zones = sorted(set(summary.gen_load_zone))
    rows = [{"zone": z, "period": p, "emissions_t": 0.0, "generation_gwh": 0.0, "tco2_per_mwh": sysv.get(p, 0.0),
             "filled": "system"}
            for p in sorted(set(summary.period)) for z in zones
            if not ((out.zone == z) & (out.period == p)).any()]
    if rows:
        out = pd.concat([out, pd.DataFrame(rows)], ignore_index=True)
    out["method"] = method
    return out.sort_values(["period", "zone"])[["zone", "period", "tco2_per_mwh", "emissions_t", "generation_gwh",
                                                "method", "filled"]].reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outputs", nargs="+", type=Path)
    ap.add_argument("--method", default="average", choices=["average", "fossil"])
    ap.add_argument("--prefer", default="first", choices=["first", "last"])
    ap.add_argument("--out", type=Path, default=Path("zone_import_factors.csv"))
    a = ap.parse_args(argv)
    frames = []
    for o in a.outputs:
        f = o / "dispatch_zonal_annual_summary.csv"
        s = pd.read_csv(f if f.exists() else o / "dispatch_gen_annual_summary.csv")
        f = factors(s, a.method)
        f["source"] = str(o)
        frames.append(f)
    t = pd.concat(frames, ignore_index=True)
    t = t.drop_duplicates(["zone", "period"], keep=a.prefer)
    t.round({"tco2_per_mwh": 5}).to_csv(a.out, index=False)
    print(f"{len(t)} zone-periods -> {a.out}")
    print(t.groupby("period").tco2_per_mwh.describe()[["min", "50%", "max"]].round(3).to_string())


if __name__ == "__main__":
    main()
