"""Adjudicate zonal coal CF caps: cloud table (public EIA-923/860 files, s0_workflow/coal_cf.py on origin/tom/s0-prod-scripts)
vs the Switch session's B8 table (PUDL pudl.2025_08.sqlite, ic_test_fedpol_work/b8_coalcf.py). Run from the Switch repo root.
Read-only on both: the cloud module and table are read with `git show` from the branch (no checkout); B8 units come from
report_tables/emissions/b8_units.csv. Independent checks from the EDF Power Data Compiler's pipeline: its PUDL cache
(out_eia__yearly_generators 2021-26, out_eia923__yearly_generation_fuel_by_generator) and EIA-860M February 2026.
Raw public EIA files (EIA-860 2021-24, EIA-923 2021-24) are downloaded once to ic_test_fedpol_work/coalcap_cache/.
Outputs: switch/out_ictest/2035/report_tables/emissions/coal_caps_adjudication_{units,zones}.csv
"""
import importlib.util
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd

W = Path(__file__).resolve().parent
C = W / "coalcap_cache"; C.mkdir(exist_ok=True)
T = Path("switch/out_ictest/2035/report_tables/emissions")
PDC = Path(r"D:\EDF Power Data Compiler")
BRANCH = "origin/tom/s0-prod-scripts"
import argparse
_ap = argparse.ArgumentParser(); _ap.add_argument("--m860", default=str(C / "august_generator2026.xlsx"),
    help="EIA-860M workbook (default: latest available, August 2026, released 2026-09-24)"); _ap.add_argument("--label", default="Aug-2026")
_ap.add_argument("--suffix", default="", help="output file suffix, e.g. _feb2026"); _A = _ap.parse_args()
M860_FILE, M860_LABEL, SFX = Path(_A.m860), _A.label, _A.suffix
ZONES = ["p130", "p108", "p12", "p79", "p58", "p48", "p23", "p97", "p99", "p107", "p75", "p44"]
YEARS = range(2021, 2025)

# --- cloud module, read from the branch
src = subprocess.run(["git", "show", f"{BRANCH}:s0_workflow/coal_cf.py"], capture_output=True, text=True, check=True).stdout
(C / "coal_cf_cloud.py").write_text(src, encoding="utf-8")
spec = importlib.util.spec_from_file_location("coal_cf_cloud", C / "coal_cf_cloud.py"); cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)
cloud_tbl = pd.read_csv(pd.io.common.StringIO(subprocess.run(["git", "show", f"{BRANCH}:s0_workflow/data/coal_cf_caps_eia923_2021_2024.csv"],
                                                              capture_output=True, text=True, check=True).stdout), comment="#")

URLS = {"923": ["https://www.eia.gov/electricity/data/eia923/archive/xls/f923_{y}.zip", "https://www.eia.gov/electricity/data/eia923/xls/f923_{y}.zip"],
        "860": ["https://www.eia.gov/electricity/data/eia860/archive/xls/eia860{y}.zip", "https://www.eia.gov/electricity/data/eia860/xls/eia860{y}.zip"]}


def fetch(kind, y):
    out = C / f"{kind}_{y}.zip"
    if out.exists() and zipfile.is_zipfile(out):
        return out
    for u in URLS[kind]:
        try:
            req = urllib.request.Request(u.format(y=y), headers={"User-Agent": "Mozilla/5.0"})
            out.write_bytes(urllib.request.urlopen(req, timeout=300).read())
        except Exception as e:  # noqa: BLE001
            print("  failed", u.format(y=y), e); continue
        if zipfile.is_zipfile(out):
            return out
    raise SystemExit(f"could not download EIA-{kind} {y}")


def member(z, pattern):
    with zipfile.ZipFile(z) as f:
        n = [x for x in f.namelist() if pattern in x][0]
        p = C / z.stem / n
        return p if p.exists() else Path(f.extract(n, C / z.stem))


gens, g923 = {}, []
for y in YEARS:
    gens[y] = cc.read_860_generators(member(fetch("860", y), "3_1_Generator"))
    g923.append(cc.read_923_generation(member(fetch("923", y), "Schedules_2_3_4_5")))
retired = cc.read_860_generators(member(fetch("860", 2024), "3_1_Generator"), "Retired and Canceled")
c2z = pd.read_csv("interconnection_headroom/data/reference/county2zone.csv")
gen923 = pd.concat(g923)
zc, cu = cc.zone_caps(gens, gens[2024], retired, gen923, c2z, (2021, 2024), 2035)
chk = zc.merge(cloud_tbl, on="ba", suffixes=("", "_tbl"))
print("cloud rerun reproduces committed table: max |d cap_cf| =", float((chk.cap_cf - chk.cap_cf_tbl).abs().max()),
      "; max |d hist_mw| =", float((chk.hist_mw - chk.hist_mw_tbl).abs().max()), "; zones", len(zc), "vs", len(cloud_tbl))

# --- per-unit CF by year from raw EIA-923 Page 4 (both methods' source), on each year's 860 winter capacity
cap = pd.concat([g.assign(year=y)[["Plant Code", "Generator ID", "year", "Winter Capacity (MW)", "Status"]] for y, g in gens.items()])
cap["k"] = cap["Plant Code"].astype(str) + "|" + cap["Generator ID"].astype(str).str.strip()
gen923["k"] = gen923.plant.astype(str) + "|" + gen923.gen
cy = cap.merge(gen923[["k", "year", "mwh", "months"]], on=["k", "year"], how="left")
cy["cf"] = cy.mwh / (cy["Winter Capacity (MW)"] * np.where(cy.year % 4 == 0, 8784, 8760))
cfw = cy.pivot_table(index="k", columns="year", values="cf", aggfunc="first").add_prefix("CF ")
mon = cy.pivot_table(index="k", columns="year", values="months", aggfunc="first").add_prefix("months ")

# --- B8 units
b8 = pd.read_csv(T / "b8_units.csv", dtype={"generator_id": str})
b8["k"] = b8.plant_id_eia.astype(str) + "|" + b8.generator_id.astype(str).str.strip()
# --- independent: compiler PUDL cache (2024-26 status, retirement) and 860M Feb 2026
pg = pd.read_parquet(PDC / "cache/pudl/out_eia__yearly_generators.parquet",
                     columns=["plant_id_eia", "generator_id", "report_date", "operational_status", "operational_status_code", "energy_source_code_1",
                              "winter_capacity_mw", "planned_generator_retirement_date", "generator_retirement_date", "state", "county", "plant_name_eia"])
pg["yr"] = pd.to_datetime(pg.report_date).dt.year
pg["k"] = pg.plant_id_eia.astype(str) + "|" + pg.generator_id.astype(str).str.strip()
st = pg[pg.yr.between(2024, 2026)].pivot_table(index="k", columns="yr", values="operational_status_code", aggfunc="first").add_prefix("PUDL status ")
pr = pg.sort_values("yr").groupby("k").agg(pudl_planned_ret=("planned_generator_retirement_date", "last"), pudl_ret=("generator_retirement_date", "last"),
                                         pudl_county=("county", "last"), pudl_state=("state", "last"), plant=("plant_name_eia", "last"),
                                         esc=("energy_source_code_1", "last"))
m860 = {}
for sh in ("Operating", "Retired"):
    x = pd.read_excel(M860_FILE, sheet_name=sh, header=2, dtype={"Generator ID": str})
    x = x[pd.to_numeric(x["Plant ID"], errors="coerce").notna()]
    x["k"] = x["Plant ID"].astype(int).astype(str) + "|" + x["Generator ID"].astype(str).str.strip()
    m860[sh] = x.set_index("k")

# --- union of units counted by either table in the target zones (+ any unit mapped there by either)
cl_units = cu.assign(k=cu["Plant Code"].astype(str) + "|" + cu["Generator ID"].astype(str).str.strip())
cl_z = cl_units[cl_units.zone.isin(ZONES)].set_index("k")
b8_z = b8[b8.zone.isin(ZONES)].set_index("k")
keys = sorted(set(cl_z.index) | set(b8_z.index))
rows = []
for k in keys:
    a = cl_z.loc[k] if k in cl_z.index else None; b = b8_z.loc[k] if k in b8_z.index else None
    r = {"k": k, "plant": pr.plant.get(k) if k in pr.index else (a["Plant Name"] if a is not None else ""),
         "zone (cloud)": a["zone"] if a is not None else "", "zone (B8)": b["zone"] if b is not None else "",
         "energy source (860-2024)": a["Energy Source 1"] if a is not None else (pr.esc.get(k) if k in pr.index else ""),
         "winter MW (cloud: 860-2024)": a["Winter Capacity (MW)"] if a is not None else np.nan,
         "winter MW (B8: PUDL latest)": b["winter_capacity_mw"] if b is not None else np.nan,
         "cloud counts (has history)": bool(a is not None and pd.notna(a["cf_max"])),
         "B8 counts (has history)": bool(b is not None and pd.notna(b["cf_max"])),
         "cf_max cloud": a["cf_max"] if a is not None else np.nan, "cf_max B8": b["cf_max"] if b is not None else np.nan,
         "860-2024 status": (gens[2024].assign(k=gens[2024]["Plant Code"].astype(str) + "|" + gens[2024]["Generator ID"].astype(str).str.strip())
                             .set_index("k")["Status"].get(k, "not in Operable")),
         "860-2024 planned ret yr": a["Planned Retirement Year"] if a is not None else np.nan,
         "B8 planned ret": b["planned_retirement_date"] if b is not None else "", "B8 report yr": b["yr"] if b is not None else np.nan,
         f"860M {M860_LABEL}": ("Operating " + str(m860["Operating"].loc[k, "Status"])[:4] + (f", planned ret {m860['Operating'].loc[k, 'Planned Retirement Year']}"
                           if pd.notna(m860["Operating"].loc[k, "Planned Retirement Year"]) and str(m860["Operating"].loc[k, "Planned Retirement Year"]).strip() else "")
                           if k in m860["Operating"].index else (f"Retired {m860['Retired'].loc[k, 'Retirement Year']}" if k in m860["Retired"].index else "not listed")),
         "county (860)": a["County"] if a is not None else "", "county (PUDL)": pr.pudl_county.get(k, "") if k in pr.index else ""}
    for y in YEARS:
        r[f"CF {y}"] = cfw.loc[k, f"CF {y}"] if k in cfw.index and f"CF {y}" in cfw.columns else np.nan
        r[f"months {y}"] = mon.loc[k, f"months {y}"] if k in mon.index and f"months {y}" in mon.columns else np.nan
    for c in st.columns:
        r[c] = st.loc[k, c] if k in st.index else ""
    rows.append(r)
U = pd.DataFrame(rows)
U["zone"] = np.where(U["zone (cloud)"] != "", U["zone (cloud)"], U["zone (B8)"])
U.sort_values(["zone", "k"]).round(4).to_csv(T / "coal_caps_adjudication_units.csv".replace(".csv", SFX + ".csv"), index=False)
pd.set_option("display.width", 320); pd.set_option("display.max_columns", 40); pd.set_option("display.max_colwidth", 40)
for z in ZONES:
    x = U[U.zone == z]
    print(f"\n=== {z}")
    print(x.drop(columns=["zone"]).round(3).to_string(index=False))

# ---------------- adjudicated variant: current EIA unit set (latest 860M, M860_FILE), model-consistent technology, robust ID matching
# Units: 860M "Operating" sheet, Technology == Conventional Steam Coal (the only coal technology in the Switch fleet), status
# OP or SB, no planned retirement before 2035. Generator IDs matched to EIA-923 / EIA-860 after stripping leading zeros.
# History: every 2021-24 unit-year with 12 monthly values (not the first year in service), whatever that year's fuel code
# (dual-fuel units flip BIT/NG between years). Capacity basis: that year's EIA-860 winter capacity. Zone: 860M county.
nid = lambda s: str(s).strip().lstrip("0") or "0"  # noqa: E731
op = m860["Operating"].reset_index()
op = op[(op.Technology == "Conventional Steam Coal") & op.Status.astype(str).str[:4].isin(["(OP)", "(SB)"])]
op["Planned Retirement Year"] = pd.to_numeric(op["Planned Retirement Year"], errors="coerce"); op = op.copy()  # stage filter applied below
op["kn"] = op["Plant ID"].astype(int).astype(str) + "|" + op["Generator ID"].map(nid)
op["wcap"] = pd.to_numeric(op["Net Winter Capacity (MW)"], errors="coerce")
cyn = cap.assign(kn=cap["Plant Code"].astype(str) + "|" + cap["Generator ID"].map(nid))
gn = gen923.assign(kn=gen923.plant.astype(str) + "|" + gen923.gen.map(nid)).groupby(["kn", "year"], as_index=False).agg(mwh=("mwh", "sum"), months=("months", "max"))
uy2 = op[["kn", "Operating Year"]].merge(cyn[["kn", "year", "Winter Capacity (MW)"]], on="kn").merge(gn, on=["kn", "year"], how="left")
uy2["cf"] = uy2.mwh / (uy2["Winter Capacity (MW)"] * np.where(uy2.year % 4 == 0, 8784, 8760))
v2 = uy2.mwh.notna() & (uy2.months >= 12) & (pd.to_numeric(uy2["Operating Year"], errors="coerce") != uy2.year) & (uy2["Winter Capacity (MW)"] > 0)
um2 = uy2[v2].groupby("kn").agg(cf_max=("cf", "max"), years=("year", "nunique")).reset_index(); um2["cf_max"] = um2.cf_max.clip(0, 1)
op = op.merge(um2, on="kn", how="left")
c2zk = dict(zip(c2z.state.str.upper() + "|" + c2z.county_name.map(cc._norm_county), c2z.ba))
op["zone"] = (op["Plant State"].str.upper() + "|" + op["County"].map(cc._norm_county)).map(c2zk)
# all current Conventional Steam Coal units (OP/SB/OA) with history, any planned retirement: used for stage-specific cap unit sets
op[["kn", "Plant ID", "Plant Name", "Generator ID", "Plant State", "County", "zone", "Technology", "Energy Source Code", "Status",
    "Planned Retirement Year", "wcap", "cf_max", "years"]].rename(columns={"wcap": "winter MW (860M)", "cf_max": "max annual CF 2021-24"}).round(4).to_csv(
    T / f"coal_cap_units_all{SFX}.csv", index=False)
op = op[op["Planned Retirement Year"].isna() | (op["Planned Retirement Year"] >= 2035)].copy()  # 2035 cap unit set
op[["kn", "Plant ID", "Plant Name", "Generator ID", "Plant State", "County", "zone", "Technology", "Energy Source Code", "Status",
    "Planned Retirement Year", "wcap", "cf_max", "years"]].rename(columns={"wcap": "winter MW (860M)", "cf_max": "max annual CF 2021-24"}).round(4).to_csv(
    T / f"coal_cap_units_adjudicated{SFX}.csv", index=False)
h2 = op[op.zone.notna() & op.cf_max.notna()]
adj = h2.groupby("zone").apply(lambda x: pd.Series({"hist_MW_adj": x.wcap.sum(), "n_units_adj": len(x),
                                                    "cap_own_history_adj": np.average(x.cf_max, weights=x.wcap)}), include_groups=False)
nat = float(np.average(adj.cap_own_history_adj, weights=adj.hist_MW_adj))
print(f"\nadjudicated set: {len(op)} units, {op.wcap.sum()/1e3:.1f} GW; mapped {op[op.zone.notna()].wcap.sum()/1e3:.1f} GW; "
      f"with history {h2.wcap.sum()/1e3:.1f} GW; zones {len(adj)}; capacity-weighted national cap {nat:.3f}")
print("unmapped / no-history units:", op[op.zone.isna() | op.cf_max.isna()][["kn", "Plant Name", "Plant State", "County", "wcap", "cf_max"]].to_string(index=False))
cmp_ = pd.read_csv(T / "coal_caps_cloud_vs_b8.csv", index_col=0)
Z = cmp_[["hist_mw", "cap_cf", "applied_cloud", "hist_GW", "cap_winter", "applied_b8"]].join(adj, how="outer")
cl = pd.read_csv(T / "b8_coal_cluster_caps.csv").groupby("gen_load_zone").model_MW_2035.sum()
Z["model_MW_2035"] = cl.reindex(Z.index)
# candidate rules for zones near the threshold
K = 500.0
Z["rule_now (>=500 MW own, else 0.65)"] = np.where(Z.hist_MW_adj >= 500, Z.cap_own_history_adj, 0.65)
Z["rule_A (>=100 MW own, else 0.65)"] = np.where(Z.hist_MW_adj >= 100, Z.cap_own_history_adj, 0.65)
Z["rule_B credibility blend H/(H+500) own + rest national"] = np.where(Z.hist_MW_adj.notna(),
    (Z.hist_MW_adj * Z.cap_own_history_adj + K * nat) / (Z.hist_MW_adj + K), nat)
Z.round(4).to_csv(T / "coal_caps_adjudication_zones.csv".replace(".csv", SFX + ".csv"))
print(f"NATIONAL_CAP {nat:.4f}")
print(Z.loc[[z for z in ZONES if z in Z.index]].round(3).to_string())
lo = Z[(Z.hist_MW_adj < 500) & (Z.model_MW_2035 > 0)]
print(f"\nzones with model coal in 2035 and < 500 MW adjudicated history: {len(lo)}, model MW {lo.model_MW_2035.sum():.0f}")
print(lo.round(3).to_string())
