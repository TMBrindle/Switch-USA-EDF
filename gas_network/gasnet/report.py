"""basin_gas_mmbtu: a Switch run's natural-gas burn by zone, period and source basin.

Reads <outputs>/GenFuelUseRate.csv (Switch generic output: GEN_TP_FUELS_1 = generator, _2 = timepoint, _3 = fuel,
GenFuelUseRate in MMBtu/h) and <outputs>/dispatch.csv (generation_project, timestamp, gen_load_zone, period,
tp_weight_in_year_hrs). In this repo's cases timepoint ids equal timestamps (conversion_functions.py). If dispatch.csv
is absent, pass inputs_dir: gen_info.csv, timepoints.csv and timeseries.csv give zone, period and weight.
Fuel use x weight = MMBtu per typical year of the period; x the zone's basin shares for the share year (the period's
own year if present, else the latest share year not after it).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def _tp_table(outputs: Path, inputs: Path | None) -> pd.DataFrame:
    disp = outputs / "dispatch.csv"
    if disp.exists():
        d = pd.read_csv(disp, usecols=["generation_project", "timestamp", "gen_load_zone", "period",
                                       "tp_weight_in_year_hrs"], dtype={"timestamp": str})
        return d.rename(columns={"generation_project": "gen", "timestamp": "tp", "gen_load_zone": "zone",
                                 "tp_weight_in_year_hrs": "weight"})
    if inputs is None:
        raise FileNotFoundError(f"{disp} missing; pass inputs_dir (gen_info, timepoints, timeseries)")
    gi = pd.read_csv(inputs / "gen_info.csv", usecols=["GENERATION_PROJECT", "gen_load_zone"])
    tp = pd.read_csv(inputs / "timepoints.csv", dtype={"timepoint_id": str})
    ts = pd.read_csv(inputs / "timeseries.csv")
    per = pd.read_csv(inputs / "periods.csv")
    plen = dict(zip(per.INVESTMENT_PERIOD, per.period_end - per.period_start + 1))
    ts["weight"] = ts.ts_duration_of_tp * ts.ts_scale_to_period / ts.ts_period.map(plen)
    tp = tp.merge(ts[["timeseries", "ts_period", "weight"]], on="timeseries")
    g = gi.rename(columns={"GENERATION_PROJECT": "gen", "gen_load_zone": "zone"})
    t = tp.rename(columns={"timepoint_id": "tp", "ts_period": "period"})[["tp", "period", "weight"]]
    return g, t


def basin_gas_mmbtu(outputs_dir, shares_csv, gas_fuels, inputs_dir=None, write=True) -> pd.DataFrame:
    outputs = Path(outputs_dir)
    fu = pd.read_csv(outputs / "GenFuelUseRate.csv", dtype={"GEN_TP_FUELS_2": str})
    fu.columns = ["gen", "tp", "fuel", "mmbtu_per_h"]
    fu["mmbtu_per_h"] = pd.to_numeric(fu.mmbtu_per_h, errors="coerce").fillna(0.0)
    unlisted = sorted(f for f in set(fu.fuel) if "gas" in str(f).lower() and f not in gas_fuels)
    if unlisted:
        raise ValueError(f"gas-like fuels not in config gas_fuels: {unlisted}")
    fu = fu[fu.fuel.isin(gas_fuels)]
    tp = _tp_table(outputs, Path(inputs_dir) if inputs_dir else None)
    if isinstance(tp, tuple):
        m = fu.merge(tp[0], on="gen", how="left").merge(tp[1], on="tp", how="left")
    else:
        m = fu.merge(tp, on=["gen", "tp"], how="left")
    if m.zone.isna().any():
        bad = m[m.zone.isna()].head(3)
        raise ValueError(f"fuel-use rows with no zone/weight (first: {bad[['gen', 'tp']].values.tolist()})")
    m["mmbtu"] = m.mmbtu_per_h * m.weight
    burn = m.groupby(["zone", "period"], as_index=False).mmbtu.sum()
    sh = pd.read_csv(shares_csv)
    years = sorted(sh.year.unique())
    rows = []
    for (z, p), v in burn.set_index(["zone", "period"]).mmbtu.items():
        yrs = [y for y in years if y <= int(p)]
        if not yrs:
            raise ValueError(f"no share year at or before period {p}")
        y = yrs[-1]
        s = sh[(sh.zone == z) & (sh.year == y)]
        if s.empty:
            raise ValueError(f"no basin shares for zone {z}")
        for b, x in zip(s.basin, s.share):
            rows.append((z, int(p), y, b, v * x))
    out = pd.DataFrame(rows, columns=["zone", "period", "share_year", "basin", "basin_gas_mmbtu"])
    if write:
        out.to_csv(outputs / "basin_gas_mmbtu.csv", index=False)
    return out
