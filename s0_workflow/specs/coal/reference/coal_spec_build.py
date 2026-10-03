"""Coal spec validation tables: model coal fleet reconstruction, unit-level fleet overrides vs the latest EIA-860M, converted gas
units, and expected zonal coal CF caps under rule C. Run from the Switch repo root. Public EIA data only.

Inputs
  model fleet basis : pg_data/pudl.2025_08.sqlite generators_eia860, report year 2024 (pg/settings/resources.yml eia_data_years),
                      technology_description == "Conventional Steam Coal", operational_status existing; retirement year from
                      PowerGenome's cached july_generator2025.xlsx (pg eia_860m_fn, GEM-edited by update_coal_closures.py)
                      Operating "Planned Retirement Year" / Retired sheet; zone = pg/extra_inputs/reeds_plant_map.csv
                      (PowerGenome plant_region_map_fn). Online in 2035 if no retirement year or retirement year >= 2035.
  latest status     : EIA-860M August 2026 (latest available, released 2026-09-24): ic_test_fedpol_work/coalcap_cache/
  CF history        : EIA-860 2021-24 + EIA-923 2021-24 Page 4 (same cache; see coalcap_adjudicate.py)
  converted-unit HR : pudl generation_fuel_eia923 (plant x prime mover x energy source), ST + NG, latest year with >= 10 GWh
Outputs (switch/out_ictest/2035/report_tables/emissions/)
  coal_spec_model_units_2035.csv, coal_spec_overrides.csv, coal_spec_converted_gas_units.csv, coal_spec_expected_caps.csv
"""
import sqlite3
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd

W = Path(__file__).resolve().parent; C = W / "coalcap_cache"
T = Path("switch/out_ictest/2035/report_tables/emissions")
M860_LATEST, LATEST = C / "august_generator2026.xlsx", "860M Aug-2026"
M860_MODEL = Path("PowerGenome/data/eia/860m/july_generator2025.xlsx")
CSC = "Conventional Steam Coal"
nid = lambda s: str(s).strip().lstrip("0") or "0"  # noqa: E731
key = lambda p, g: f"{int(p)}|{nid(g)}"  # noqa: E731


def m860(path, sheet):
    x = pd.read_excel(path, sheet_name=sheet, header=2, dtype={"Generator ID": str})
    x = x[pd.to_numeric(x["Plant ID"], errors="coerce").notna()].copy()
    x["kn"] = [key(p, g) for p, g in zip(x["Plant ID"], x["Generator ID"])]
    for c in ("Planned Retirement Year", "Retirement Year", "Net Winter Capacity (MW)", "Operating Year"):
        if c in x:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    return x.drop_duplicates("kn").set_index("kn")


# ---------- 1. model coal fleet in 2035 (reconstruction of PowerGenome's unit list)
c = sqlite3.connect("file:pg_data/pudl.2025_08.sqlite?mode=ro", uri=True)
g = pd.read_sql("""select plant_id_eia, generator_id, operational_status, operational_status_code, technology_description, energy_source_code_1,
                   winter_capacity_mw, capacity_mw, planned_retirement_date from generators_eia860
                   where report_date like '2024%' and technology_description in ('Conventional Steam Coal', 'Coal Integrated Gasification Combined Cycle', 'Petroleum Coke')""", c)
g = g[g.operational_status == "existing"].copy()
g_all = g.copy()  # PowerGenome tech_groups: coal cluster = CSC + IGCC + Petroleum Coke
g["kn"] = [key(p, x) for p, x in zip(g.plant_id_eia, g.generator_id)]
jo, jr = m860(M860_MODEL, "Operating"), m860(M860_MODEL, "Retired")
# the case encodes PUDL-2024 planned retirement dates (e.g. Comanche 2 -> 2025, Harrington 2/3 -> 2038/2040 in gen_build_predetermined),
# not the GEM-edited July-2025 860M planned dates; actual retirements in the July-2025 Retired sheet are applied
g["model_ret_year"] = pd.to_datetime(g.planned_retirement_date, errors="coerce").dt.year
g.loc[g.kn.isin(jr.index), "model_ret_year"] = g.kn.map(jr["Retirement Year"])
pm = pd.read_csv("pg/extra_inputs/reeds_plant_map.csv")
g["zone"] = g.plant_id_eia.map(dict(zip(pm.plant_id_eia, pm.region)))
# fallback for plants not in the plant map: county -> zone (county2zone.csv), PUDL plant county
import sys as _s; _s.path.insert(0, "build_rate"); from brc.data import norm_county  # noqa: E402
pl = pd.read_sql("select plant_id_eia, state, county from plants_entity_eia", c)
c2z = pd.read_csv("interconnection_headroom/data/reference/county2zone.csv")
zk = dict(zip(c2z.state.str.upper() + "|" + c2z.county_name.map(norm_county), c2z.ba))
pz = dict(zip(pl.plant_id_eia, (pl.state.str.upper() + "|" + pl.county.map(lambda x: norm_county(x) if isinstance(x, str) else "")).map(zk)))
g["zone_source"] = np.where(g.zone.notna(), "reeds_plant_map", "county fallback")
g["zone"] = g.zone.fillna(g.plant_id_eia.map(pz))
g = g[g.operational_status_code != "OS"]  # out-of-service units are not in the case fleet (e.g. Schiller 4/6 in p130)
g["online_2035_model"] = g.model_ret_year.isna() | (g.model_ret_year >= 2035)
M = g[g.online_2035_model & g.zone.notna()].copy()
cl = pd.read_csv(T / "b8_coal_cluster_caps.csv").groupby("gen_load_zone").model_MW_2035.sum()
chk = pd.DataFrame({"reconstructed_MW": M.groupby("zone").winter_capacity_mw.sum(), "model_MW_2035": cl}).fillna(0)
chk["diff"] = chk.reconstructed_MW - chk.model_MW_2035
print(f"model fleet reconstruction: {len(M)} units, {M.winter_capacity_mw.sum()/1e3:.2f} GW vs model {cl.sum()/1e3:.2f} GW; "
      f"zones with |diff| > 1 MW: {(chk['diff'].abs() > 1).sum()} (max {chk['diff'].abs().max():.1f} MW); unmapped units {g[g.online_2035_model & g.zone.isna()].shape[0]}")
if (chk["diff"].abs() > 1).any():
    print(chk[chk["diff"].abs() > 1].round(1).to_string())
M[["plant_id_eia", "generator_id", "kn", "zone", "winter_capacity_mw", "capacity_mw", "energy_source_code_1", "model_ret_year"]].to_csv(
    T / "coal_spec_model_units_2035.csv", index=False)

# ---------- 2. overrides over the S0 horizon (stages 2028-2045): every coal-group unit vs the latest 860M
HZ, STAGES = 2045, [2028, 2030, 2035, 2040, 2045]
lo, lr = m860(M860_LATEST, "Operating"), m860(M860_LATEST, "Retired")
gf = pd.read_sql("""select report_date, plant_id_eia, prime_mover_code, energy_source_code, fuel_consumed_for_electricity_mmbtu f,
                    net_generation_mwh n from generation_fuel_eia923 where report_date >= '2023-01-01'""", c, parse_dates=["report_date"])
gf["yr"] = gf.report_date.dt.year


def gas_hr(plant, coal_hr):
    x = gf[(gf.plant_id_eia == plant) & (gf.prime_mover_code == "ST") & (gf.energy_source_code == "NG")]
    a = x.groupby("yr").agg(f=("f", "sum"), n=("n", "sum"), months=("report_date", "nunique")).sort_index(ascending=False)
    a = a[a.n >= 10_000]
    if len(a):
        y = a.index[0]
        return round(a.loc[y, "f"] / a.loc[y, "n"], 3), f"EIA-923 plant ST/NG {y} ({int(a.loc[y, 'months'])} months, {a.loc[y, 'n']/1e3:.0f} GWh)"
    return (round(coal_hr, 3) if pd.notna(coal_hr) else np.nan), "no gas-fired history: unit's 2024 coal heat rate (PowerGenome) kept"


coal_hr = pd.read_sql("""select plant_id_eia, sum(fuel_consumed_for_electricity_mmbtu) f, sum(net_generation_mwh) n from generation_fuel_eia923
                         where report_date like '2024%' and prime_mover_code = 'ST' and energy_source_code in ('BIT','SUB','LIG','RC','WC','ANT')
                         group by 1""", c)
coal_hr = dict(zip(coal_hr.plant_id_eia, coal_hr.f / coal_hr.n.where(coal_hr.n > 0)))
HOLD = pd.read_csv(T / "coal_spec_hold_online.csv", dtype={"generator_id": str})
HOLD["kn"] = [key(p, x) for p, x in zip(HOLD.plant_id_eia, HOLD.generator_id)]
HS0 = HOLD[HOLD.in_S0].set_index("kn"); HP = HOLD[HOLD.in_sensitivity_holds_persist].set_index("kn")
eff = lambda y: (int(y) if pd.notna(y) and y <= HZ else None)  # noqa: E731  (dates after the horizon = no retirement)
TG = ("Petroleum Coke", "Coal Integrated Gasification Combined Cycle")
U = g[g.zone.notna()].copy()                       # every coal-group unit in the model basis, incl. those it retires before 2035
UB = U.set_index("kn").model_ret_year
rows, final = [], {}                               # final[kn] = (kind, encoded retirement year or None); kind: coal | gas | held_s0
for _, u in U.iterrows():
    k = u.kn; base = eff(u.model_ret_year)
    r = {"plant_id_eia": u.plant_id_eia, "generator_id": u.generator_id, "zone": u.zone, "model_winter_MW": u.winter_capacity_mw,
         "model_technology": u.technology_description, "model_ret_year": base,
         "plant_name": (lo["Plant Name"].get(k) if k in lo.index else lr["Plant Name"].get(k, ""))}
    o, st, pry = None, None, np.nan
    if k in lo.index:
        o = lo.loc[k]; st = str(o["Status"])[1:3]; pry = o["Planned Retirement Year"]
        r.update(latest_status=st, latest_technology=o["Technology"], latest_energy_source=o["Energy Source Code"],
                 latest_winter_MW=o["Net Winter Capacity (MW)"], latest_planned_ret=pry)
    if k in HS0.index:                              # S0 holds: rows added below
        continue
    if k in lr.index:
        y = int(lr.loc[k, "Retirement Year"]); final[k] = ("coal", y)
        if base is not None and base <= y:
            continue                                # model already retires it by then
        r.update(action="retire", effective_year=y, source=f"{LATEST} Retired sheet (status RE), Retirement Year {y}", latest_status="RE",
                 latest_technology=lr.loc[k, "Technology"])
    elif o is None:
        final[k] = ("coal", base)
        r.update(action="REVIEW: not in latest 860M", effective_year=np.nan, source=f"{LATEST}: absent from Operating and Retired")
    elif st == "OS":
        final[k] = ("coal", 2026)
        if base is not None and base <= 2026:
            continue
        r.update(action="remove (out of service, not returning)", effective_year=2026, source=f"{LATEST} Operating, status OS")
    elif u.technology_description == TG[1] and o["Technology"] != TG[1]:
        final[k] = ("coal", base)
        r.update(action="no change: IGCC partly recoded (CTs NG combined cycle, ST IGCC) - wait for consistent 860M coding (Tom 2026-10-03)",
                 effective_year=np.nan, source=f"{LATEST} Operating")
    elif o["Technology"] not in (CSC,) + TG and o["Energy Source Code"] == "NG":
        final[k] = ("gas", None)
        hr, hsrc = gas_hr(int(u.plant_id_eia), coal_hr.get(int(u.plant_id_eia)))
        r.update(action="convert to gas", effective_year=np.nan, source=f"{LATEST} Operating: Technology '{o['Technology']}', energy source NG",
                 convert_tech=o["Technology"], convert_fuel="NG", convert_winter_MW=o["Net Winter Capacity (MW)"], convert_heat_rate=hr,
                 convert_heat_rate_source=hsrc, convert_planned_ret=pry)
    elif o["Technology"] not in (CSC,) + TG:
        final[k] = ("coal", base)
        r.update(action=f"REVIEW: now '{o['Technology']}' ({o['Energy Source Code']})", effective_year=np.nan, source=f"{LATEST} Operating")
    else:                                           # still coal-group: the 860M planned retirement date is authoritative over the horizon
        R = eff(pry); final[k] = ("coal", R)
        if R == base:
            if u.technology_description in TG:
                r.update(action=f"tech-group member ({u.technology_description}): stays in coal cluster with the coal cap (Tom 2026-10-03; split out in full refresh)",
                         effective_year=np.nan, source=f"pg/settings/resources.yml tech_groups; {LATEST} Operating")
            else:
                continue
        elif R is None:
            r.update(action="keep online (planned retirement withdrawn or after 2045)", effective_year=np.nan,
                     source=f"{LATEST} Operating, Planned Retirement Year {'blank' if pd.isna(pry) else int(pry)} vs model basis {base}")
        else:
            r.update(action="retire" if (base is None or R < base) else "retire later than model basis", effective_year=R,
                     source=f"{LATEST} Operating, Planned Retirement Year {R} vs model basis {base if base is not None else 'none'}")
    rows.append(r)
for k, h in HS0.iterrows():                         # S0 holds (spec section 3)
    final[k] = ("held_s0", int(h.S0_encoded_retirement_year))
    rows.append({"plant_id_eia": h.plant_id_eia, "generator_id": h.generator_id, "zone": h.zone, "plant_name": h.plant_name,
                 "model_winter_MW": h.winter_MW_860M_Aug2026, "model_ret_year": (eff(UB.get(k)) if k in UB.index else "not in model basis"),
                 "action": "hold online (S0: 2028 stage only)", "effective_year": 2026, "hold_last_stage_S0": 2028,
                 "post_hold_action_S0": h.S0_post_hold_action, "encoded_retirement_year_S0": int(h.S0_encoded_retirement_year),
                 "hold_last_stage_persist": 2045, "hold_max_annual_CF": h.hold_max_annual_CF,
                 "hold_CF_source": f"{h.hold_CF_source}; window {h.hold_period}; actual CF {h.CF_since_order:.4f}",
                 "source": f"Tom 2026-10-03; {h.order_basis}: {h.orders}; {LATEST} {h['860M_Aug2026']}",
                 "latest_status": "OP", "latest_technology": "Conventional Steam Coal"})
for k, h in HP[~HP.index.isin(HS0.index)].iterrows():   # persist-only holds (sensitivity)
    rows.append({"plant_id_eia": h.plant_id_eia, "generator_id": h.generator_id, "zone": h.zone, "plant_name": h.plant_name,
                 "model_winter_MW": h.winter_MW_860M_Aug2026, "model_ret_year": (eff(UB.get(k)) if k in UB.index else "not in model basis"),
                 "action": "hold online (sensitivity holds_persist only; not in S0)",
                 "effective_year": 2026, "hold_last_stage_persist": 2045, "hold_max_annual_CF": h.hold_max_annual_CF,
                 "hold_CF_source": f"{h.hold_CF_source}; window {h.hold_period}", "post_hold_action_S0": "not held in S0: stays retired (860M Retired 2025)",
                 "source": f"Tom 2026-10-03; {h.order_basis}: {h.orders}; {LATEST} {h['860M_Aug2026']}"})
O = pd.DataFrame(rows)
hist = pd.read_sql("""select plant_id_eia, generator_id, strftime('%Y', report_date) yr, energy_source_code_1 from generators_eia860
                      where report_date >= '2020-01-01'""", c)
hist["kn"] = [key(p, x) for p, x in zip(hist.plant_id_eia, hist.generator_id)]
hist["yr"] = hist.yr.astype(int)


def _run_start(x):
    x = x.sort_values("yr"); ng = (x.energy_source_code_1 == "NG").values; yrs = x.yr.values
    if not ng[-1]:
        return 2026          # coded coal in PUDL's latest year, NG only in the latest 860M
    i = len(ng) - 1
    while i > 0 and ng[i - 1]:
        i -= 1
    return int(yrs[i])


first_ng = hist.groupby("kn").apply(_run_start, include_groups=False)
conv = O.action.str.startswith("convert")
O.loc[conv, "conversion_year"] = [first_ng.get(key(p, x), 2026) for p, x in zip(O[conv].plant_id_eia, O[conv].generator_id)]
O.loc[conv, "effective_year"] = O.loc[conv, "conversion_year"]
O = O.sort_values(["action", "zone", "plant_id_eia"])
O.to_csv(T / "coal_spec_overrides.csv", index=False)
print("\noverrides by action (model winter MW):"); print(O.groupby("action").agg(units=("generator_id", "count"), MW=("model_winter_MW", "sum")).round(0).to_string())
pd.set_option("display.width", 300); pd.set_option("display.max_columns", 30); pd.set_option("display.max_colwidth", 40)
print(O[["plant_id_eia", "generator_id", "plant_name", "zone", "model_winter_MW", "model_technology", "action", "effective_year", "model_ret_year", "latest_planned_ret"]].to_string(index=False))

# ---------- 3. converted gas units as they should appear
G = O.loc[conv & O.model_winter_MW.notna()][["plant_id_eia", "generator_id", "plant_name", "zone", "convert_tech", "convert_fuel", "convert_winter_MW",
                                             "convert_heat_rate", "convert_heat_rate_source", "conversion_year", "convert_planned_ret"]].copy()
t24 = pd.read_sql("select plant_id_eia, generator_id, technology_description from generators_eia860 where report_date like '2024%'", c)
t24["kn"] = [key(p, x) for p, x in zip(t24.plant_id_eia, t24.generator_id)]
G["pudl_2024_technology"] = [dict(zip(t24.kn, t24.technology_description)).get(key(p, x)) for p, x in zip(G.plant_id_eia, G.generator_id)]
G.to_csv(T / "coal_spec_converted_gas_units.csv", index=False)
print("\nconverted gas units:"); print(G.round(3).to_string(index=False))

# ---------- 4. expected caps by stage (coverage rule) on the refreshed fleet; identical in S0 and holds_persist
A = pd.read_csv(T / "coal_cap_units_all.csv")      # all current CSC units OP/SB/OA with history (coalcap_adjudicate.py)
A["plant"] = A.kn.str.split("|").str[0].astype(int)
A["zone_final"] = A.plant.map(dict(zip(pm.plant_id_eia, pm.region))).fillna(A.zone)   # PowerGenome plant map first, county fallback
A.to_csv(T / "coal_cap_units_all.csv", index=False)
held_any = set(HOLD[HOLD.in_S0 | HOLD.in_sensitivity_holds_persist].kn)
alive = lambda y, p: y is None or y >= p   # noqa: E731  in service in period p (pg_to_switch encoding)
out = []
for p in STAGES:
    h = A[A.zone_final.notna() & A["max annual CF 2021-24"].notna() & ~A.kn.isin(held_any)
          & (A["Planned Retirement Year"].isna() | (A["Planned Retirement Year"] >= p))]
    nat = float(np.average(h["max annual CF 2021-24"], weights=h["winter MW (860M)"]))
    z = h.groupby("zone_final").apply(lambda x: pd.Series({"hist_MW": x["winter MW (860M)"].sum(), "n_units": len(x),
                                                           "own_cap": np.average(x["max annual CF 2021-24"], weights=x["winter MW (860M)"])}), include_groups=False)
    before = U[[alive(eff(y), p) for y in U.model_ret_year]].groupby("zone").winter_capacity_mw.sum()
    keep_ = [k for k, (kind, y) in final.items() if kind == "coal" and alive(y, p)]
    after = U[U.kn.isin(keep_)].groupby("zone").winter_capacity_mw.sum()
    E = pd.DataFrame({"model_MW_before": before, "model_MW_after_overrides": after}).fillna(0).join(z, how="outer")
    E[["model_MW_before", "model_MW_after_overrides"]] = E[["model_MW_before", "model_MW_after_overrides"]].fillna(0)
    E["coverage"] = E.hist_MW / E.model_MW_after_overrides.replace(0, np.nan)
    E["rule"] = np.where(E.hist_MW.isna(), "no history: national", np.where(E.coverage.fillna(np.inf) >= 0.5, "own history (coverage >= 0.5)", "blend"))
    E["expected_cap"] = np.where(E.hist_MW.isna(), nat, np.where(E.coverage.fillna(np.inf) >= 0.5, E.own_cap,
                                                                 (E.hist_MW * E.own_cap + 500 * nat) / (E.hist_MW + 500)))
    E.loc[E.model_MW_after_overrides <= 0, "rule"] = E.loc[E.model_MW_after_overrides <= 0, "rule"] + " (no model coal after overrides)"
    E["stage"] = p; E["national_N"] = nat; E.index.name = "zone"
    out.append(E.reset_index())
    w = E.model_MW_after_overrides
    print(f"stage {p}: N {nat:.4f}; zones {len(E)}; model GW before/after {E.model_MW_before.sum()/1e3:.2f}/{w.sum()/1e3:.2f}; "
          f"weighted cap {np.average(E.expected_cap, weights=w):.4f}; rules {E.rule.value_counts().to_dict()}")
S = pd.concat(out)
S.round(4).to_csv(T / "coal_spec_expected_caps_by_stage.csv", index=False)
S[S.stage == 2035].drop(columns="stage").set_index("zone").round(4).to_csv(T / "coal_spec_expected_caps.csv")
hs = []
for _, h in HOLD[HOLD.in_S0 | HOLD.in_sensitivity_holds_persist].iterrows():
    for p in STAGES:
        hs.append({"plant_id_eia": h.plant_id_eia, "generator_id": h.generator_id, "plant_name": h.plant_name, "zone": h.zone, "stage": p,
                   "S0": "held" if (h.in_S0 and p <= 2028) else ("retired" if h.in_S0 else "retired (not held in S0)"),
                   "holds_persist": "held", "cap_while_held": h.hold_max_annual_CF, "winter_MW": h.winter_MW_860M_Aug2026})
pd.DataFrame(hs).to_csv(T / "coal_spec_hold_by_stage.csv", index=False)
