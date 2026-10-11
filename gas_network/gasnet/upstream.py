"""Post-solve upstream methane for a Switch run (CHANGES §105). No model change: reads a finished run's outputs.

Inputs (Switch outputs directory):
  * GenFuelUseRate.csv (generic output: GEN_TP_FUELS_1 = generator, _2 = timepoint, _3 = fuel, GenFuelUseRate MMBtu/h)
  * dispatch.csv (generation_project, timestamp, gen_load_zone, period, tp_weight_in_year_hrs, Energy_GWh_typical_yr,
    DispatchEmissions_tCO2_per_typical_yr). As in report.py, timepoint ids equal timestamps in this repo's cases.
  * tracked_demand_onsite_fuel_use.csv, when present (tracked-demands module, branch tom/tracked-demands-v2:
    tracked_demand, generation_project, load_zone, timepoint, timestamp, period, fuel, fuel_use_mmbtu_per_h,
    tp_weight_in_year, emissions_tco2_per_h). On-site generators are ordinary Switch generators, so their fuel is
    already in GenFuelUseRate.csv: the file labels that burn as on-site and is NOT added again. Rows whose
    generator/timepoint is missing from GenFuelUseRate.csv are added (flagged), with no MWh.
Per generator and timepoint: gas MMBtu/yr = rate x tp_weight_in_year; MWh and stack CO2 from dispatch.csv x the gas
fraction of the generator's fuel use. Summed by zone and period (typical year of the period).

Upstream CH4 = gas MMBtu x sum_b share(zone, b) x intensity(b) + gas MMBtu x transmission & storage (distribution is
not on the power-plant path and is excluded). Options:
  share path   aeo (zone_basin_shares.csv) | constrained_pipe (zone_basin_shares_constrained_pipe.csv)
  basis        measured (IDEA-01 default) | inventory (GHGRP RY2023 basin rates; W. Canada NIR-level; E. Canada and
               LNG imports have no inventory value and keep the measured one)
  methane path current_rules | no_further_reductions (inventory values follow the measured path's multipliers)
  level        low | central | high (inventory: one value for all three)
Share year and intensity year: the latest year in the table not after the period (2024 before 2028).
GWP: IPCC AR6 WG1 Table 7.15, fossil CH4: GWP100 29.8, GWP20 82.5.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import PKG

GWP100, GWP20 = 29.8, 82.5
K = 0.95 * 19.2 / 1.037        # kg CH4 per MMBtu delivered per unit L/(1-L); same conversion as the research note
SHARE_FILES = {"aeo": "zone_basin_shares.csv", "constrained_pipe": "zone_basin_shares_constrained_pipe.csv"}
LEVELS = ("low", "central", "high")
METHANE_PATHS = ("current_rules", "no_further_reductions")
ONSITE = "tracked_demand_onsite_fuel_use.csv"


def kg_from_pct(pct):
    x = np.asarray(pct, dtype=float) / 100
    return K * x / (1 - x)


def intensities(csv: Path) -> tuple[pd.DataFrame, dict]:
    """(basis, methane_path, basin, year, level, kg_per_mmbtu) and transmission & storage {level: kg/MMBtu}."""
    d = pd.read_csv(csv)
    up = d[(d.segment == "upstream_total") & ~d.basin.isin(["all", "eagle_ford_conventional"])]
    kgm = up[(up.unit == "kg_ch4_per_mmbtu_delivered") & up.flag.isin(["measurement", "assumption", "projection"])]
    rows = []
    for r in kgm.itertuples():
        paths = METHANE_PATHS if r.path == "observed" else (r.path,)
        for p in paths:
            for lv in LEVELS:
                rows.append(("measured", p, r.basin, int(r.year), lv, float(getattr(r, lv))))
    meas = pd.DataFrame(rows, columns=["basis", "methane_path", "basin", "year", "level", "kg_per_mmbtu"])
    cen = meas[meas.level == "central"].set_index(["methane_path", "basin", "year"]).kg_per_mmbtu
    inv = up[(up.unit == "pct_gross_gas") & (((up.flag == "inventory") & (up.source == "EPA_GHGRP_W"))
                                            | (up.flag == "inventory_derived"))]
    base = dict(zip(inv.basin, kg_from_pct(inv.central)))
    rows = []
    for (p, b, y), c in cen.items():
        c24 = cen[(p, b, 2024)]
        v = base[b] * (c / c24 if c24 else 1.0) if b in base else c
        rows += [("inventory", p, b, y, lv, float(v)) for lv in LEVELS]
    out = pd.concat([meas, pd.DataFrame(rows, columns=meas.columns)], ignore_index=True)
    ts = d[(d.basin == "all") & (d.segment == "transmission_storage") & (d.unit == "kg_ch4_per_mmbtu_delivered")].iloc[0]
    return out, {lv: float(ts[lv]) for lv in LEVELS}


def gas_burn(outputs: Path, gas_fuels) -> tuple[pd.DataFrame, list]:
    """zone, period, gas_mmbtu, onsite_gas_mmbtu, gas_mwh, stack_tco2 (typical year of the period) and flags."""
    gas = {f.lower() for f in gas_fuels}
    fu = pd.read_csv(outputs / "GenFuelUseRate.csv", dtype={"GEN_TP_FUELS_2": str})
    fu.columns = ["gen", "tp", "fuel", "rate"]
    fu["rate"] = pd.to_numeric(fu.rate, errors="coerce").fillna(0.0)
    unlisted = sorted(f for f in set(fu.fuel) if "gas" in str(f).lower() and str(f).lower() not in gas)
    if unlisted:
        raise ValueError(f"gas-like fuels not in the gas fuel list: {unlisted}")
    fu["is_gas"] = fu.fuel.str.lower().isin(gas)
    fu["gas_rate"] = fu.rate.where(fu.is_gas, 0.0)
    g = fu.groupby(["gen", "tp"], as_index=False).agg(gas_rate=("gas_rate", "sum"), all_rate=("rate", "sum"))
    g = g[g.gas_rate > 0]
    disp = pd.read_csv(outputs / "dispatch.csv", dtype={"timestamp": str},
                       usecols=["generation_project", "timestamp", "gen_load_zone", "period", "tp_weight_in_year_hrs",
                                "Energy_GWh_typical_yr", "DispatchEmissions_tCO2_per_typical_yr"])
    disp.columns = ["gen", "tp", "zone", "period", "weight", "gwh", "tco2"]
    m = g.merge(disp, on=["gen", "tp"], how="left")
    if m.zone.isna().any():
        bad = m[m.zone.isna()].head(3)[["gen", "tp"]].values.tolist()
        raise ValueError(f"gas fuel-use rows with no dispatch.csv row (first: {bad})")
    frac = m.gas_rate / m.all_rate
    m["gas_mmbtu"] = m.gas_rate * m.weight
    m["gas_mwh"] = m.gwh * 1000 * frac
    m["stack_tco2"] = m.tco2 * frac
    m["onsite_gas_mmbtu"] = 0.0
    flags = []
    p = outputs / ONSITE
    if p.exists():
        o = pd.read_csv(p, dtype={"timestamp": str})
        o = o[o.fuel.str.lower().isin(gas) & (o.fuel_use_mmbtu_per_h > 0)]
        o = o.groupby(["generation_project", "timestamp", "load_zone", "period"], as_index=False).agg(
            rate=("fuel_use_mmbtu_per_h", "sum"), weight=("tp_weight_in_year", "first"),
            eh=("emissions_tco2_per_h", "sum"))
        key = m.set_index(["gen", "tp"]).index
        ok = pd.MultiIndex.from_arrays([o.generation_project, o.timestamp]).isin(key)
        lab = o[ok].set_index(["generation_project", "timestamp"]).rate
        m["onsite_gas_mmbtu"] = [lab.get((a, b), 0.0) * w for a, b, w in zip(m.gen, m.tp, m.weight)]
        extra = o[~ok]
        if len(extra):
            flags.append(f"{len(extra)} on-site gas rows not in GenFuelUseRate.csv were added (no MWh)")
            m = pd.concat([m, pd.DataFrame({
                "gen": extra.generation_project, "tp": extra.timestamp, "zone": extra.load_zone,
                "period": extra.period, "gas_mmbtu": extra.rate * extra.weight, "gas_mwh": 0.0,
                "stack_tco2": extra.eh * extra.weight, "onsite_gas_mmbtu": extra.rate * extra.weight})],
                ignore_index=True)
    cols = ["gas_mmbtu", "onsite_gas_mmbtu", "gas_mwh", "stack_tco2"]
    return m.groupby(["zone", "period"], as_index=False)[cols].sum(), flags


def _latest(years, p):
    ys = [y for y in years if y <= p]
    return ys[-1] if ys else years[0]


def upstream(outputs_dir, cfg, shares_dir=None, intensity_csv=None, share_paths=None, bases=None,
             methane_paths=None, out_dir=None, write=True) -> dict:
    outputs = Path(outputs_dir)
    shares_dir = Path(shares_dir or PKG / "outputs")
    burn, flags = gas_burn(outputs, cfg["gas_fuels"])
    it, ts = intensities(Path(intensity_csv or PKG / "data/basin_methane_intensity.csv"))
    h = pd.read_csv(cfg["paths"]["hierarchy"])
    state = dict(zip(h.ba, h.st))
    rows = []
    for sp in share_paths or list(SHARE_FILES):
        sh = pd.read_csv(shares_dir / SHARE_FILES[sp])
        syears = sorted(sh.year.unique())
        shd = {k: list(zip(v.basin, v.share)) for k, v in sh[sh.share > 0].groupby(["zone", "year"])}
        missing = sorted(set(burn.zone) - set(sh.zone))
        if missing:
            raise ValueError(f"no basin shares for zones {missing[:5]}")
        for (basis, mp, lv), t in it.groupby(["basis", "methane_path", "level"]):
            if (bases and basis not in bases) or (methane_paths and mp not in methane_paths):
                continue
            iyears = sorted(t.year.unique())
            tk = t.set_index(["year", "basin"]).kg_per_mmbtu
            for r in burn.itertuples():
                sy, iy = _latest(syears, int(r.period)), _latest(iyears, int(r.period))
                k = float(sum(x * tk[(iy, b)] for b, x in shd[(r.zone, sy)]))
                up_t, ts_t = r.gas_mmbtu * k / 1000, r.gas_mmbtu * ts[lv] / 1000
                rows.append(dict(zone=r.zone, state=state.get(r.zone, ""), period=int(r.period), share_path=sp,
                                 basis=basis, methane_path=mp, level=lv, share_year=sy, intensity_year=iy,
                                 gas_mmbtu=r.gas_mmbtu, onsite_gas_mmbtu=r.onsite_gas_mmbtu, gas_mwh=r.gas_mwh,
                                 stack_tco2=r.stack_tco2, upstream_tch4=up_t, ts_tch4=ts_t))
    z = pd.DataFrame(rows)
    keys = ["share_path", "basis", "methane_path", "level"]

    def finish(d):
        d = d.copy()
        d["total_tch4"] = d.upstream_tch4 + d.ts_tch4
        d["tco2e_gwp100"] = d.total_tch4 * GWP100
        d["tco2e_gwp20"] = d.total_tch4 * GWP20
        d["kg_ch4_per_mwh"] = np.where(d.gas_mwh > 0, 1000 * d.total_tch4 / d.gas_mwh.where(d.gas_mwh > 0), np.nan)
        d["gwp100_pct_of_stack"] = np.where(d.stack_tco2 > 0, 100 * d.tco2e_gwp100 / d.stack_tco2.where(d.stack_tco2 > 0), np.nan)
        d["gwp20_pct_of_stack"] = np.where(d.stack_tco2 > 0, 100 * d.tco2e_gwp20 / d.stack_tco2.where(d.stack_tco2 > 0), np.nan)
        return d

    num = ["gas_mmbtu", "onsite_gas_mmbtu", "gas_mwh", "stack_tco2", "upstream_tch4", "ts_tch4"]
    res = {"by_zone": finish(z),
           "by_state": finish(z.groupby(["state", "period"] + keys, as_index=False)[num].sum()),
           "by_period": finish(z.groupby(["period"] + keys, as_index=False)[num].sum()),
           "flags": flags}
    if write:
        out = Path(out_dir or outputs / "upstream_ch4")
        out.mkdir(parents=True, exist_ok=True)
        for k in ("by_zone", "by_state", "by_period"):
            res[k].to_csv(out / f"upstream_ch4_{k}.csv", index=False, float_format="%.6g")
        res["out_dir"] = out
    return res
