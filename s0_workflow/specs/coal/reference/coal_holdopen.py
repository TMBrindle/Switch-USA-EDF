"""Coal units held open by federal (FPA 202(c)) or state orders: actual output since the order, and the "hold online, no dispatch"
unit-level max annual CF. Run from the Switch repo root. Public data only.
Generation: EIA-923 Page 4 (generator-level monthly net generation), 2025 final (M_12_2025_Final) and 2026 year-to-date
(M_07_2026, released 2026-09-21), in ic_test_fedpol_work/coalcap_cache/. Capacity: EIA-860M August 2026 Net Winter Capacity.
CF since the order = sum of monthly net generation over the hold period / (winter MW x hours in those months), clipped at 0.
Hold period starts with the first full month after the unit's original planned retirement date (the month it would otherwise
have been retired), or the first full month of the first order if later; it ends with the latest EIA-923 month (July 2026).
"No usable data" (unit absent from Page 4, or fewer than 3 months with values) -> 0.01. Floor 0.001 (see code comment).
If the first order is < 3 months old, the window starts at the month after the original planned retirement instead.
Outputs: switch/out_ictest/2035/report_tables/emissions/coal_spec_hold_online.csv (+ _monthly.csv)
"""
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd

W = Path(__file__).resolve().parent; C = W / "coalcap_cache"
T = Path("switch/out_ictest/2035/report_tables/emissions")
nid = lambda s: str(s).strip().lstrip("0") or "0"  # noqa: E731
# unit list: (plant, generator(s), name, state, basis, orders, hold start (YYYY-MM), decision)
U = [
    (3845, ["2"], "TransAlta Centralia", "WA", "DOE FPA 202(c)",
     "202-25-11 (2025-12-16) -> 202-26-18 (2026-03-16) -> 202-26-28 (2026-06-12) -> 202-26-44 (2026-09-11; effective 2026-09-13 to 2026-12-11); "
     "Ninth Circuit petition filed 2026-03-02", "2026-01", "S0+persist"),
    (1710, ["1", "2", "3"], "J.H. Campbell", "MI", "DOE FPA 202(c)",
     "202-25-3 (2025-05-23; VACATED by D.C. Cir., Michigan v. DOE, 2026-09-11) -> ... -> 202-26-22 (2026-05-18) -> 202-26-39 (2026-08-14; "
     "effective 2026-08-17 to 2026-11-14, still in force)", "2025-06", "S0+persist"),
    (6085, ["17", "18"], "R.M. Schahfer", "IN", "DOE FPA 202(c)",
     "202-25-12 (2025-12-23) -> 202-26-19 (2026-03-23) -> 202-26-29 (2026-06-18) -> 202-26-46 (2026-09-18; effective 2026-09-20 to 2026-12-18)",
     "2026-01", "S0+persist"),
    (1012, ["2"], "F.B. Culley", "IN", "DOE FPA 202(c)",
     "202-25-13 (2025-12-23) -> 202-26-20 (2026-03-23) -> 202-26-30 (2026-06-18) -> 202-26-47 (2026-09-18; effective 2026-09-20 to 2026-12-18)",
     "2026-01", "S0+persist"),
    (6021, ["1"], "Craig Station", "CO", "DOE FPA 202(c)",
     "202-25-14 (2025-12-30) -> 202-26-21 (2026-03-30) -> 202-26-31 (2026-06-26) -> 202-26-49 (2026-09-25; effective 2026-09-27 to 2026-12-25)",
     "2026-01", "S0+persist"),
    (564, ["1"], "Stanton Energy Center", "FL", "DOE FPA 202(c)",
     "202-26-26 (2026-06-04; effective 2026-06-04 to 2026-09-01) -> 202-26-42 (2026-09-01; effective 2026-09-02 to 2026-11-30)",
     "2026-06", "not held (stays keep online, coal_spec.md section 2)", "2026-01"),
    (6481, ["1", "2"], "Intermountain Power Project", "UT", "Utah state law (HB 70, 2025: units kept operable while a buyer is sought)",
     "no federal order; coal units ceased operation Nov 2025 when the IPP Renewed gas units entered service; IPA RFP for acquisition issued 2026-08-05",
     "2025-12", "persist only"),
]


def page4(zpath, member_pat):
    with zipfile.ZipFile(zpath) as z:
        n = [x for x in z.namelist() if member_pat in x][0]
        p = C / Path(zpath).stem / n
        if not p.exists():
            z.extract(n, C / Path(zpath).stem)
    top = pd.read_excel(p, sheet_name="Page 4 Generator Data", header=None, nrows=12)
    hdr = int(top.index[top.iloc[:, 0].astype(str).str.strip() == "Plant Id"][0])  # header row differs by release (5 in 2025, 4 in 2026)
    d = pd.read_excel(p, sheet_name="Page 4 Generator Data", header=hdr, dtype={"Generator Id": str})
    d.columns = [" ".join(str(c).split()) for c in d.columns]
    d = d[pd.to_numeric(d["Plant Id"], errors="coerce").notna()]
    return d


g25 = page4(C / "923_2025.zip", "Schedules_2_3_4_5"); g26 = page4(C / "923_2026.zip", "Schedules_2_3_4_5")
MON = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
m860 = pd.read_excel(C / "august_generator2026.xlsx", sheet_name="Operating", header=2, dtype={"Generator ID": str})
m860 = m860[pd.to_numeric(m860["Plant ID"], errors="coerce").notna()]
m860["kn"] = m860["Plant ID"].astype(int).astype(str) + "|" + m860["Generator ID"].map(nid)
r860 = pd.read_excel(C / "august_generator2026.xlsx", sheet_name="Retired", header=2, dtype={"Generator ID": str})
r860 = r860[pd.to_numeric(r860["Plant ID"], errors="coerce").notna()]
r860["kn"] = r860["Plant ID"].astype(int).astype(str) + "|" + r860["Generator ID"].map(nid)
ov = pd.read_csv(T / "coal_spec_overrides.csv", dtype={"generator_id": str})
ov["kn"] = ov.plant_id_eia.astype(str) + "|" + ov.generator_id.map(nid)
mu = pd.read_csv(T / "coal_spec_model_units_2035.csv", dtype={"generator_id": str})
mu["kn"] = mu.plant_id_eia.astype(str) + "|" + mu.generator_id.map(nid)
pm = pd.read_csv("pg/extra_inputs/reeds_plant_map.csv")

rows, mrows = [], []
for entry in U:
    plant, gens, name, st, basis, orders, start, decision = entry[:8]
    fallback = entry[8] if len(entry) > 8 else start
    for gen in gens:
        k = f"{plant}|{nid(gen)}"
        o = m860[m860.kn == k]; rr = r860[r860.kn == k]
        wmw = float(pd.to_numeric(o["Net Winter Capacity (MW)"], errors="coerce").iloc[0]) if len(o) else (
            float(pd.to_numeric(rr["Net Winter Capacity (MW)"], errors="coerce").iloc[0]) if len(rr) else np.nan)
        def window(st_):
            out = []
            for yr, d in ((2025, g25), (2026, g26)):
                x = d[(pd.to_numeric(d["Plant Id"]) == plant) & (d["Generator Id"].map(nid) == nid(gen))]
                for i, mname in enumerate(MON, 1):
                    ym = f"{yr}-{i:02d}"
                    if ym < st_ or (yr == 2026 and i > 7):
                        continue
                    col = f"Net Generation {mname}"
                    v = pd.to_numeric(x[col], errors="coerce").sum(min_count=1) if len(x) and col in x else np.nan
                    out.append((ym, v))
            return out
        w = window(start); used = start
        if sum(pd.notna(v) for _, v in w) < 3 and fallback < start:
            w = window(fallback); used = fallback   # first order < 3 months old: extend back to the original planned retirement
        mwh, hrs, nmon = 0.0, 0.0, 0
        for ym, v in w:
            if True:
                h = pd.Period(ym).days_in_month * 24
                mrows.append({"plant_id_eia": plant, "generator_id": gen, "month": ym, "net_generation_MWh": v,
                              "CF": (v / (wmw * h)) if pd.notna(v) and wmw > 0 else np.nan})
                if pd.notna(v):
                    mwh += v; hrs += h; nmon += 1
        cf = max(mwh / (wmw * hrs), 0.0) if nmon >= 3 and wmw > 0 else np.nan
        # floor 0.001: a cap of exactly 0 makes Switch force the unit off in every timepoint (UNAVAILABLE_GENS in
        # study_modules/gen_annual_availability_limits.py), incl. the zero-weight PRM day, so it would no longer count for adequacy
        hold = max(cf, 0.001) if pd.notna(cf) else 0.01
        in_model = k in set(mu.kn)
        act = ov.loc[ov.kn == k, "action"]
        rows.append({"plant_id_eia": plant, "generator_id": gen, "plant_name": name, "state": st,
                     "zone": pm.set_index("plant_id_eia").region.get(plant), "winter_MW_860M_Aug2026": wmw,
                     "860M_Aug2026": (f"Operating {str(o['Status'].iloc[0])[:4]} {o['Technology'].iloc[0]}, planned ret "
                                      f"{o['Planned Retirement Year'].iloc[0] if pd.notna(o['Planned Retirement Year'].iloc[0]) else 'blank'}") if len(o)
                     else (f"Retired {rr['Retirement Year'].iloc[0]}" if len(rr) else "not listed"),
                     "order_basis": basis, "orders": orders, "hold_period": f"{used} to 2026-07" + (" (extended: first order < 3 months old)" if used != start else ""), "months_with_data": nmon,
                     "net_generation_MWh_since_order": mwh if nmon else np.nan, "CF_since_order": cf,
                     "hold_max_annual_CF": round(hold, 4),
                     "hold_CF_source": (f"EIA-923 Page 4 monthly, {nmon} months" + (" (actual < 0.001 -> floor 0.001)" if cf < 0.001 else "")) if pd.notna(cf) else "no usable data -> 0.01",
                     "in_current_model_2035": in_model, "existing_override": act.iloc[0] if len(act) else "", "decision": decision})
H = pd.DataFrame(rows)
H["in_S0"] = H.decision.eq("S0+persist")
H["in_sensitivity_holds_persist"] = H.decision.isin(["S0+persist", "persist only"])
H["S0_hold_last_stage"] = np.where(H.in_S0, 2028, np.nan)          # S0: held in the 2028 stage only (period 2026-28)
H["persist_hold_last_stage"] = np.where(H.in_sensitivity_holds_persist, 2045, np.nan)
pry = H["860M_Aug2026"].str.extract(r"planned ret ([0-9]{4})")[0].astype(float)
H["latest_860M_planned_ret"] = pry
def _post(r, py):
    if not r.in_S0:
        return "", np.nan
    if pd.notna(py) and py >= 2030:
        return f"after hold: retire {int(py)} (latest 860M planned retirement)", py
    if pd.notna(py):
        return f"after hold: retired from the 2030 stage (latest 860M planned retirement {int(py)} has passed)", 2029
    return "after hold: retired from the 2030 stage (no planned retirement; the order was the only thing keeping it open)", 2029
pp = [_post(r, py) for (_, r), py in zip(H.iterrows(), pry)]
H["S0_post_hold_action"] = [a for a, _ in pp]
H["S0_encoded_retirement_year"] = [y for _, y in pp]               # pg_to_switch: in service in period p iff year >= p
H.loc[H.plant_id_eia == 3845, "S0_post_hold_action"] += "; gas conversion not modelled until sourced (Tom 2026-10-03)"
H.to_csv(T / "coal_spec_hold_online.csv", index=False)
pd.DataFrame(mrows).to_csv(T / "coal_spec_hold_online_monthly.csv", index=False)
pd.set_option("display.width", 300); pd.set_option("display.max_columns", 30); pd.set_option("display.max_colwidth", 45)
print(H[["plant_id_eia", "generator_id", "plant_name", "zone", "winter_MW_860M_Aug2026", "860M_Aug2026", "hold_period", "months_with_data",
         "net_generation_MWh_since_order", "CF_since_order", "hold_max_annual_CF", "in_current_model_2035", "existing_override", "decision"]].to_string(index=False))
