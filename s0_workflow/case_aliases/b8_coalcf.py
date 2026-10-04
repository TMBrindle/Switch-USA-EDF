"""B8: zone-level coal capacity-factor caps from EIA-923 2021-24 (PUDL), applied via gen_max_annual_availability.

Units: coal generators (fuel_type_code_pudl == 'coal') operating in the latest EIA-860 year in PUDL, with no actual
  retirement and no planned retirement before 2035 (= units online in 2035 under the case's retirement schedule).
Unit-year CF = annual net generation (generation_eia923, sum of 12 months) / (capacity x hours). Capacity basis:
  WINTER capacity (winter_capacity_mw), the column PowerGenome uses for model capacity (resources.yml capacity_col),
  so the cap is on the same basis as the model's capacity; a nameplate-based variant is computed for reference.
  Unit-years skipped: first year in service, retirement year, missing generation, or fewer than 12 monthly reports.
Unit max = highest valid CF over 2021-24 (clipped at 1). Zone cap = capacity-weighted mean of unit maxima (weights:
  winter capacity) over units with history; plants mapped to zones by (state, county) via county2zone.csv.
  Zones with < 500 MW of coal with history (or none) get 0.65.
gen_info.coalcfhist.csv: every coal cluster in a zone gets gen_max_annual_availability = zone_cap / (1 - forced),
  capped at 1 (non-binding) where it would exceed 1; clusters whose cap exceeds the outage-limited max CF
  (1 - forced)(1 - scheduled) are flagged as effectively non-binding.
"""
import sqlite3, sys
from datetime import datetime
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path("."); sys.path.insert(0, str(ROOT / "build_rate"))
from brc.data import norm_county

import argparse
ap = argparse.ArgumentParser(description="B8 zonal coal CF caps from EIA-923 2021-24 (PUDL).")
ap.add_argument("--case", default="switch/in_ictest/cases/2035/s4x1_S0_2035_icoff")
ap.add_argument("--base-gen-info", default="gen_info.csv", help="gen_info file the caps are applied to (gen_info.ic_v2.csv for the headroom stack)")
ap.add_argument("--out-name", default=None, help="output alias name (default: <base stem>.coalcfhist.csv, i.e. gen_info.coalcfhist.csv / gen_info.ic_v2.coalcfhist.csv)")
ap.add_argument("--tables-dir", default="switch/out_ictest/2035/report_tables/emissions")
ap.add_argument("--pudl", default="pg_data/pudl.2025_08.sqlite")
A = ap.parse_args()
D = ROOT / A.case; T = ROOT / A.tables_dir; T.mkdir(parents=True, exist_ok=True)
c = sqlite3.connect(str(ROOT / A.pudl))
g = pd.read_sql("""select plant_id_eia, generator_id, report_date, operational_status, capacity_mw, winter_capacity_mw,
                   planned_retirement_date, generator_retirement_date, fuel_type_code_pudl from generators_eia860
                   where fuel_type_code_pudl = 'coal'""", c, parse_dates=["report_date", "planned_retirement_date", "generator_retirement_date"])
g["yr"] = g.report_date.dt.year
latest = g.yr.max()
cur = g[g.yr == latest].copy()
ent = pd.read_sql("select plant_id_eia, generator_id, operating_date from generators_entity_eia", c, parse_dates=["operating_date"])
units = cur[(cur.operational_status == "existing") & cur.generator_retirement_date.isna()
            & (cur.planned_retirement_date.isna() | (cur.planned_retirement_date.dt.year >= 2035))].merge(ent, on=["plant_id_eia", "generator_id"], how="left")
# annual CF 2021-24
gen = pd.read_sql("""select plant_id_eia, generator_id, report_date, net_generation_mwh from generation_eia923
                     where report_date >= '2021-01-01' and report_date < '2025-01-01'""", c, parse_dates=["report_date"])
gen["yr"] = gen.report_date.dt.year
ag = gen.groupby(["plant_id_eia", "generator_id", "yr"]).agg(mwh=("net_generation_mwh", "sum"), months=("net_generation_mwh", "count")).reset_index()
cap_y = g[g.yr.between(2021, 2024)][["plant_id_eia", "generator_id", "yr", "winter_capacity_mw", "capacity_mw", "generator_retirement_date"]]
uy = units[["plant_id_eia", "generator_id", "operating_date"]].merge(cap_y, on=["plant_id_eia", "generator_id"]).merge(ag, on=["plant_id_eia", "generator_id", "yr"], how="left")
hrs = uy.yr.map(lambda y: 8784 if y % 4 == 0 else 8760)
uy["cf_winter"] = uy.mwh / (uy.winter_capacity_mw * hrs); uy["cf_nameplate"] = uy.mwh / (uy.capacity_mw * hrs)
valid = uy.mwh.notna() & (uy.months >= 12) & (uy.operating_date.dt.year != uy.yr) & ~(uy.generator_retirement_date.dt.year == uy.yr) & (uy.winter_capacity_mw > 0)
uy["valid"] = valid
um = uy[uy.valid].groupby(["plant_id_eia", "generator_id"]).agg(cf_max=("cf_winter", "max"), cf_max_np=("cf_nameplate", "max"), years=("yr", "nunique")).reset_index()
um["cf_max"] = um.cf_max.clip(0, 1); um["cf_max_np"] = um.cf_max_np.clip(0, 1)
units = units.merge(um, on=["plant_id_eia", "generator_id"], how="left")
# zones by county
pl = pd.read_sql("select plant_id_eia, state, county from plants_entity_eia", c)
c2z = pd.read_csv(ROOT / "interconnection_headroom/data/reference/county2zone.csv")
c2z["key"] = c2z.state.str.upper() + "|" + c2z.county_name.map(norm_county)
pl["key"] = pl.state.str.upper() + "|" + pl.county.map(lambda s: norm_county(s) if isinstance(s, str) else "")
zmap = dict(zip(c2z.key, c2z.ba))
units = units.merge(pl[["plant_id_eia", "key"]], on="plant_id_eia", how="left"); units["zone"] = units.key.map(zmap)
cov = {"units": len(units), "GW (winter)": units.winter_capacity_mw.sum() / 1e3,
       "GW with zone": units[units.zone.notna()].winter_capacity_mw.sum() / 1e3,
       "GW with CF history": units[units.cf_max.notna()].winter_capacity_mw.sum() / 1e3,
       "latest EIA-860 year in PUDL": int(latest)}
h = units[units.zone.notna() & units.cf_max.notna()]
_w = h.assign(_wc=h.cf_max * h.winter_capacity_mw, _nc=h.cf_max_np * h.capacity_mw)   # pandas 1.4 and 2.x
zc = _w.groupby("zone").agg(_win=("winter_capacity_mw", "sum"), _wc=("_wc", "sum"), _np=("capacity_mw", "sum"),
                            _nc=("_nc", "sum"))
zc = pd.DataFrame({"hist_GW": zc._win / 1e3, "cap_winter": zc._wc / zc._win, "cap_nameplate": zc._nc / zc._np})
# model coal clusters
gi = pd.read_csv(D / A.base_gen_info, dtype=str, keep_default_na=False)
m = gi.gen_energy_source == "coal"
pre = pd.read_csv(D / "gen_build_predetermined.csv")
alive = pre[pre.build_year + 500 >= 2035].groupby("GENERATION_PROJECT").build_gen_predetermined.sum()
cl = gi.loc[m, ["GENERATION_PROJECT", "gen_load_zone", "gen_forced_outage_rate", "gen_scheduled_outage_rate"]].copy()
cl["model_MW_2035"] = cl.GENERATION_PROJECT.map(alive).fillna(0)
cl["zone_cap"] = cl.gen_load_zone.map(lambda z: zc.at[z, "cap_winter"] if z in zc.index and zc.at[z, "hist_GW"] >= 0.5 else 0.65)
cl["cap_source"] = cl.gen_load_zone.map(lambda z: "EIA-923 2021-24" if z in zc.index and zc.at[z, "hist_GW"] >= 0.5 else "default 0.65 (<500 MW history)")
f = cl.gen_forced_outage_rate.astype(float); s_ = cl.gen_scheduled_outage_rate.astype(float)
cl["availability"] = (cl.zone_cap / (1 - f))
cl["capped_at_1"] = cl.availability > 1
cl["availability"] = cl.availability.clip(upper=1.0)
cl["outage_limited_max_cf"] = (1 - f) * (1 - s_)
cl["effectively_nonbinding"] = cl.zone_cap >= cl.outage_limited_max_cf
out = D / (A.out_name or A.base_gen_info.replace(".csv", ".coalcfhist.csv"))
if out.exists(): raise FileExistsError(out)
gi.loc[m, "gen_max_annual_availability"] = cl.set_index(cl.index).availability.round(6).map(repr).values
gi.to_csv(out, index=False)
cl.to_csv(T / "b8_coal_cluster_caps.csv", index=False); zc.to_csv(T / "b8_zone_caps.csv")
units.to_csv(T / "b8_units.csv", index=False)
# distribution (weights: model coal MW online in 2035)
w = cl.groupby("gen_load_zone").agg(MW=("model_MW_2035", "sum"), cap=("zone_cap", "first"), src=("cap_source", "first"))
w = w[w.MW > 0].sort_values("cap"); cw = w.MW.cumsum() / w.MW.sum()
dec = {f"p{int(q*100)}": float(w.cap[cw >= q].iloc[0]) for q in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)}
nat = float(np.average(w.cap, weights=w.MW)); below = w[w.cap < 0.65].MW.sum() / 1e3
top5 = w.sort_values("MW", ascending=False).head(5)
summary = {"coverage": cov, "deciles (zones, weighted by model coal MW online 2035)": dec, "national cap-weighted cap (winter)": nat,
           "national cap-weighted cap (nameplate basis, reference)": float(np.average(zc.cap_nameplate, weights=zc.hist_GW)),
           "GW below 0.65": below, "zones with default 0.65": int((w.src != "EIA-923 2021-24").sum()), "GW at default": w[w.src != "EIA-923 2021-24"].MW.sum() / 1e3,
           "clusters capped at availability 1": int(cl.capped_at_1.sum()), "clusters effectively non-binding (cap >= outage-limited max)": int(cl.effectively_nonbinding.sum()),
           "MW effectively non-binding": float(cl[cl.effectively_nonbinding].model_MW_2035.sum())}
pd.Series({k: str(v) for k, v in summary.items()}).to_csv(T / "b8_summary.csv")
top5.to_csv(T / "b8_top5_zones.csv")
open(D / "patch_log.partb.txt", "a").write(f"\n{datetime.now().isoformat(timespec='seconds')} B8 {out.name} (from {A.base_gen_info}): coal gen_max_annual_availability = zone cap / (1 - forced), zone cap = cap-weighted mean of unit max annual CF 2021-24 (EIA-923 via PUDL, winter-capacity basis; zones < 500 MW history -> 0.65); see report_tables/emissions/b8_*.csv\n")
for k, v in summary.items(): print(k, ":", v)
print(top5.round(3).to_string())
