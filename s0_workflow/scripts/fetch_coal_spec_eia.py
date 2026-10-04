"""Build the coal specification's public-EIA tables (s0_workflow/specs/coal/coal_spec.md, rev. 2).

Downloads (to --cache, default s0_workflow/data/raw/, gitignored):
  EIA-860M, latest month (--m860, default August 2026, released 2026-09-24), and June 2025 (the 2025
    record of the conversion-year rule: the vintage of PUDL 2025_08's 2025 data)
      https://www.eia.gov/electricity/data/eia860m/xls/<month>_generator<year>.xlsx (or .../archive/xls/)
  EIA-860 annual 2020-24   https://www.eia.gov/electricity/data/eia860/archive/xls/eia860<year>.zip
  EIA-923 annual 2021-24, 2025 final and 2026 year-to-date
      https://www.eia.gov/electricity/data/eia923/archive/xls/f923_<year>.zip (or .../xls/)
Plant -> zone: pg/extra_inputs/reeds_plant_map.csv; county -> zone: interconnection_headroom/data/reference/
county2zone.csv.

Writes (s0_workflow/data/):
  coal_cap_units_860m.csv   every latest-860M Operating unit with Technology "Conventional Steam Coal" and
                            status OP/SB/OA, any planned retirement: zone, winter MW, 2021-24 max CF (§1.1-1.4)
  coal_fleet_860m.csv       latest-860M Operating and Retired rows of every plant with a coal-group unit in
                            EIA-860 2024 or the latest 860M, with the conversion year of NG-coded units (§2)
  coal_plant_st_fuel.csv    monthly ST fuel and net generation (NG, coal) 2023-26 of the plants with a
                            converted unit, and their 2024 ST coal heat rate (§2.3)
  coal_holds.csv            the order-held units (coal_spec.md §3.2, §3.4) with their hold caps (§3.1)
  coal_model_basis_860er2024.csv  a public reconstruction of PowerGenome's coal-group fleet (§2.1): EIA-860 2024
                            early release (PUDL 2025_08's vintage) and the July 2025 860M Retired sheet; used for
                            the per-option validation tables (scripts/build_coal_option_tables.py)
and checks them against the validation tables in s0_workflow/specs/coal/ (--check-only: no writes).

usage (repo root): python s0_workflow/scripts/fetch_coal_spec_eia.py [--m860 august_generator2026.xlsx]
"""
import argparse
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import coal_spec as cs  # noqa: E402

DATA = REPO / "s0_workflow/data"
URLS = {
    "923": ["https://www.eia.gov/electricity/data/eia923/archive/xls/f923_{y}.zip",
            "https://www.eia.gov/electricity/data/eia923/xls/f923_{y}.zip"],
    "860": ["https://www.eia.gov/electricity/data/eia860/archive/xls/eia860{y}.zip",
            "https://www.eia.gov/electricity/data/eia860/xls/eia860{y}.zip"],
    "860m": ["https://www.eia.gov/electricity/data/eia860m/xls/{f}",
             "https://www.eia.gov/electricity/data/eia860m/archive/xls/{f}"],
}
COAL_FUELS = {"ANT", "BIT", "LIG", "SUB", "RC", "WC", "SGC"}

# Units held open by orders (§3.2, §3.4): plant, generator, name, state, zone, S0, holds_persist, window start
# (first full month after the original planned retirement, or of the first order if later), order basis.
HOLDS = [
    (3845, "2", "TransAlta Centralia", "WA", True, True, "2026-01",
     "DOE FPA 202(c): 202-25-11 (2025-12-16) -> 202-26-18 -> 202-26-28 -> 202-26-44 (2026-09-13 to 2026-12-11); "
     "Ninth Circuit petition filed 2026-03-02"),
    (1710, "1", "J.H. Campbell", "MI", True, True, "2025-06",
     "DOE FPA 202(c): 202-25-3 (2025-05-23; vacated by the D.C. Circuit, Michigan v. DOE, 2026-09-11) -> ... -> "
     "202-26-39 (2026-08-17 to 2026-11-14, in force)"),
    (1710, "2", "J.H. Campbell", "MI", True, True, "2025-06", "as Campbell 1"),
    (1710, "3", "J.H. Campbell", "MI", True, True, "2025-06", "as Campbell 1"),
    (6085, "17", "R.M. Schahfer", "IN", True, True, "2026-01",
     "DOE FPA 202(c): 202-25-12 (2025-12-23) -> ... -> 202-26-46 (2026-09-20 to 2026-12-18)"),
    (6085, "18", "R.M. Schahfer", "IN", True, True, "2026-01", "as Schahfer 17"),
    (1012, "2", "F.B. Culley", "IN", True, True, "2026-01",
     "DOE FPA 202(c): 202-25-13 (2025-12-23) -> ... -> 202-26-47 (2026-09-20 to 2026-12-18)"),
    (6021, "1", "Craig Station", "CO", True, True, "2026-01",
     "DOE FPA 202(c): 202-25-14 (2025-12-30) -> ... -> 202-26-49 (2026-09-27 to 2026-12-25); challenged by "
     "Colorado and others 2026-01-28"),
    (6481, "1", "Intermountain Power Project", "UT", False, True, "2025-12",
     "Utah HB 70 (2025): units kept operable while a buyer is sought; IPA acquisition RFP 2026-08-05; latest 860M "
     "lists them Retired (2025): held in holds_persist only"),
    (6481, "2", "Intermountain Power Project", "UT", False, True, "2025-12", "as Intermountain 1"),
]
S0_LAST_STAGE, S0_ENCODED_RET, PERSIST_LAST_STAGE = 2028, 2029, 2045


def fetch(kind: str, cache: Path, y: int | None = None, f: str | None = None) -> Path:
    name = f if kind == "860m" else f"{kind}_{y}.zip"
    out = cache / name
    ok = (lambda p: zipfile.is_zipfile(p)) if kind != "860m" else (lambda p: p.exists() and p.stat().st_size > 1e6)
    if out.exists() and ok(out):
        return out
    for url in URLS[kind]:
        u = url.format(y=y, f=f)
        print("downloading", u)
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            out.write_bytes(urllib.request.urlopen(req, timeout=600).read())
        except Exception as e:  # noqa: BLE001
            print("  failed:", e)
            continue
        if ok(out):
            return out
        print("  not the expected file (EIA redirects missing files to an HTML page)")
    raise SystemExit(f"could not download {name}")


def member(z: Path, pattern: str, cache: Path) -> Path:
    with zipfile.ZipFile(z) as f:
        names = [n for n in f.namelist() if pattern in n]
        if not names:
            raise SystemExit(f"{z.name}: no member matching {pattern!r}")
        p = cache / z.stem / names[0]
        return p if p.exists() else Path(f.extract(names[0], cache / z.stem))


def _923(y: int, cache: Path) -> Path:
    for alt in (cache / f"f923_{y}.zip",):
        if alt.exists() and zipfile.is_zipfile(alt):
            return member(alt, "Schedules_2_3_4_5", cache)
    return member(fetch("923", cache, y), "Schedules_2_3_4_5", cache)


def _860(y: int, cache: Path) -> Path:
    alt = cache / f"eia860{y}.zip"
    z = alt if alt.exists() and zipfile.is_zipfile(alt) else fetch("860", cache, y)
    return member(z, "3_1_Generator", cache)


def header(text: str) -> str:
    return "".join(f"# {ln}\n" for ln in text.strip().splitlines())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--m860", default="august_generator2026.xlsx", help="latest EIA-860M workbook (basename)")
    ap.add_argument("--m860-2025", default="june_generator2025.xlsx", help="860M used as the 2025 record")
    ap.add_argument("--m860-model", default="july_generator2025.xlsx", help="PowerGenome's 860M (model basis)")
    ap.add_argument("--cache", default=str(REPO / "s0_workflow/data/raw"))
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    label = a.m860.replace("_generator", " ").replace(".xlsx", "")

    m = fetch("860m", cache, f=a.m860)
    op, rt = cs.read_860m(m, "Operating"), cs.read_860m(m, "Retired")
    gens = {y: cs.read_860_annual(_860(y, cache)) for y in range(2020, 2025)}
    ret24 = cs.read_860_annual(_860(2024, cache), "Retired and Canceled")
    p4 = pd.concat([cs.read_923_page4(_923(y, cache)) for y in range(2021, 2027)], ignore_index=True)
    c2z = pd.read_csv(REPO / "interconnection_headroom/data/reference/county2zone.csv")
    pmap = cs.read_plant_map()

    # §1.2 collision check, coal-group units, in every file
    coal_keys = set(op.kn[op.Technology.isin(cs.COAL_GROUP)]) | set(rt.kn[rt.Technology.isin(cs.COAL_GROUP)])
    for y, g in gens.items():
        coal_keys |= set(cs.keys(g["Plant Code"], g["Generator ID"])[i] for i in np.flatnonzero(
            g.Technology.isin(cs.COAL_GROUP).values))
    cs.check_collisions(f"860M {label} Operating", op["Plant ID"], op["Generator ID"], coal_keys)
    cs.check_collisions(f"860M {label} Retired", rt["Plant ID"], rt["Generator ID"], coal_keys)
    for y, g in gens.items():
        cs.check_collisions(f"EIA-860 {y}", g["Plant Code"], g["Generator ID"], coal_keys)
    for y in sorted(p4.year.unique()):
        x = p4[p4.year == y].drop_duplicates(["plant", "gen"])
        cs.check_collisions(f"EIA-923 {y} Page 4", x.plant, x.gen, coal_keys)
    print(f"collision check: none among {len(coal_keys)} coal-group units")

    # 1. cap units
    cu = cs.cap_units(op, {y: g for y, g in gens.items() if y >= 2021}, p4, pmap, c2z)
    lower48 = ~cu["Plant State"].isin(["AK", "HI"])
    if cu[lower48].zone.isna().any():
        raise SystemExit(f"unmapped lower-48 cap units (§1.4 step 3, lat/lon, needed): "
                         f"{cu[lower48 & cu.zone.isna()][['kn', 'Plant Name', 'Plant State', 'County']].to_dict('records')}")
    print(f"cap units: {len(cu)} ({cu.winter_mw.sum() / 1e3:.1f} GW); zones by "
          f"{cu.zone_source.value_counts(dropna=False).to_dict()}; statuses {cu.status.value_counts().to_dict()}")

    # 2. fleet extract: plants with a coal-group unit in EIA-860 2024 (operable or retired) or the latest 860M
    plants = (set(gens[2024].loc[gens[2024].Technology.isin(cs.COAL_GROUP), "Plant Code"])
              | set(ret24.loc[ret24.Technology.isin(cs.COAL_GROUP), "Plant Code"])
              | set(op.loc[op.Technology.isin(cs.COAL_GROUP), "Plant ID"])
              | set(rt.loc[rt.Technology.isin(cs.COAL_GROUP), "Plant ID"]))
    cols = ["kn", "Plant ID", "Generator ID", "Plant Name", "Plant State", "County", "Technology",
            "Energy Source Code", "Prime Mover Code", "status", "Net Winter Capacity (MW)", "Operating Year",
            "Planned Retirement Year", "Retirement Year"]
    fl = pd.concat([op[op["Plant ID"].isin(plants)].assign(sheet="Operating"),
                    rt[rt["Plant ID"].isin(plants)].assign(sheet="Retired", status="RE")], ignore_index=True)
    fl = fl[["sheet"] + [c for c in cols if c in fl]]
    # conversion year of NG-coded units (§2.3): annual EIA-860 2020-24, the 2025 record, the latest 860M
    m25 = cs.read_860m(fetch("860m", cache, f=a.m860_2025), "Operating").drop_duplicates("kn").set_index("kn")
    hist = {y: g.assign(kn=cs.keys(g["Plant Code"], g["Generator ID"])).drop_duplicates("kn").set_index("kn")[
        "Energy Source 1"] for y, g in gens.items()}
    hist[2025] = m25["Energy Source Code"]
    ng = (fl.sheet == "Operating") & (fl["Energy Source Code"] == "NG") & ~fl.Technology.isin(cs.COAL_GROUP)
    fl["conversion_year"] = np.nan
    for i in fl.index[ng]:
        k = fl.at[i, "kn"]
        s = pd.Series({y: h.get(k) for y, h in hist.items()}).dropna()
        s[2026] = "NG"
        fl.at[i, "conversion_year"] = cs.conversion_year(s, 2026)
    fl["technology_2024"] = fl.kn.map(gens[2024].assign(kn=cs.keys(gens[2024]["Plant Code"], gens[2024]["Generator ID"]))
                                      .drop_duplicates("kn").set_index("kn").Technology)

    # 3. ST fuel of the plants with a converted unit (coal-group in EIA-860 2024, NG steam now)
    conv = fl[ng & fl.technology_2024.isin(cs.COAL_GROUP)]
    cplants = sorted(set(conv["Plant ID"]))
    p1 = pd.concat([cs.read_923_page1(_923(y, cache)) for y in range(2023, 2027)], ignore_index=True)
    st = p1[p1.plant.isin(cplants) & (p1.pm == "ST")].copy()
    st["fuel"] = np.where(st.fuel == "NG", "NG", np.where(st.fuel.isin(COAL_FUELS), "COAL", None))
    st = st[st.fuel.notna()].groupby(["plant", "year", "month", "fuel"], as_index=False)[["mmbtu", "mwh"]].sum(min_count=1)
    st = st[st.mwh.notna() | st.mmbtu.notna()]

    # 4. holds
    end = p4[p4.mwh.notna()].assign(ym=lambda d: d.year.astype(str) + "-" + d.month.map("{:02d}".format)).ym.max()
    rows = []
    for plant, gen, name, state, s0, persist, start, basis in HOLDS:
        k = cs.unit_key(plant, gen)
        src = op[op.kn == k] if (op.kn == k).any() else rt[rt.kn == k]
        wmw = float(src["Net Winter Capacity (MW)"].iloc[0])
        r = cs.hold_cap(p4, k, wmw, start, end)
        pry = src["Planned Retirement Year"].iloc[0] if "Planned Retirement Year" in src and len(src) else np.nan
        latest = (f"860M {label} Operating ({src.status.iloc[0]}), planned retirement "
                  f"{int(pry) if pd.notna(pry) else 'blank'}" if (op.kn == k).any()
                  else f"860M {label} Retired {int(src['Retirement Year'].iloc[0])}")
        rows.append({"plant_id_eia": plant, "generator_id": gen, "plant_name": name, "state": state,
                     "zone": pmap.get(plant), "winter_mw": wmw, "hold_cap": r["hold_cap"],
                     "cf_since_order": r["cf_since_order"], "window": r["window"],
                     "months_with_data": r["months_with_data"], "net_mwh": r["net_mwh"],
                     "in_S0": s0, "in_holds_persist": persist,
                     "S0_last_stage": S0_LAST_STAGE if s0 else np.nan,
                     "S0_encoded_retirement_year": S0_ENCODED_RET if s0 else np.nan,
                     "persist_last_stage": PERSIST_LAST_STAGE if persist else np.nan,
                     "latest_860m": latest, "source": basis})
    holds = pd.DataFrame(rows)
    # §3.2 post-hold rule: planned retirement >= 2030 would retire then (none in this 860M)
    for i, h in holds[holds.in_S0].iterrows():
        k = cs.unit_key(h.plant_id_eia, h.generator_id)
        p = op.loc[op.kn == k, "Planned Retirement Year"]
        if len(p) and pd.notna(p.iloc[0]) and p.iloc[0] >= 2030:
            holds.at[i, "S0_encoded_retirement_year"] = int(p.iloc[0])

    # 5. model basis: EIA-860 2024 early release + PowerGenome's 860M (July 2025) Retired sheet
    er = cache / "eia8602024ER.zip"
    if not (er.exists() and zipfile.is_zipfile(er)):
        req = urllib.request.Request("https://www.eia.gov/electricity/data/eia860/archive/xls/eia8602024ER.zip",
                                     headers={"User-Agent": "Mozilla/5.0"})
        er.write_bytes(urllib.request.urlopen(req, timeout=600).read())
    basis = cs.model_basis_from_eia(cs.read_860_annual(member(er, "3_1_Generator", cache)),
                                    cs.read_860m(fetch("860m", cache, f=a.m860_model), "Retired"), pmap, c2z)

    ok = check(cu, fl, st, holds)
    if a.check_only:
        sys.exit(0 if ok else 1)
    stamp = f"built {date.today()} by s0_workflow/scripts/fetch_coal_spec_eia.py from EIA-860M {label}"
    write(DATA / "coal_cap_units_860m.csv", cu.round({"cf_max": 6}), header(
        f"coal cap units (coal_spec.md §1.1-1.4): latest-860M Operating, Technology Conventional Steam Coal,\n"
        f"status OP/SB/OA, any planned retirement; cf_max = highest valid 2021-24 annual CF (EIA-923 Page 4 /\n"
        f"EIA-860 winter capacity); zone: reeds_plant_map.csv, then county2zone.csv; {stamp}"))
    write(DATA / "coal_fleet_860m.csv", fl, header(
        f"860M {label} Operating and Retired rows of every plant with a coal-group unit (Conventional Steam\n"
        f"Coal, IGCC, Petroleum Coke) in EIA-860 2024 or this 860M (coal_spec.md §2); conversion_year: first\n"
        f"year of the latest NG run in EIA-860 2020-24, 860M {a.m860_2025} (2025) and this 860M; {stamp}"))
    write(DATA / "coal_plant_st_fuel.csv", st, header(
        f"monthly ST fuel for electricity (MMBtu) and net generation (MWh) by fuel (NG; COAL = BIT/SUB/LIG/RC/\n"
        f"WC/ANT/SGC), EIA-923 Page 1 2023-26 (2026 year-to-date), plants with a converted unit\n"
        f"(coal_spec.md §2.3); {stamp}"))
    write(DATA / "coal_model_basis_860er2024.csv", basis, header(
        f"public reconstruction of PowerGenome's coal-group fleet (coal_spec.md §2.1): EIA-860 2024 early release\n"
        f"Operable, Conventional Steam Coal / IGCC / Petroleum Coke, not OS; retirement_year_basis = planned retirement,\n"
        f"or the Retirement Year of 860M {a.m860_model} Retired; zone: plant map, then county; {stamp}"))
    write(DATA / "coal_holds.csv", holds.round({"cf_since_order": 6}), header(
        f"units held open by orders (coal_spec.md §3): hold_cap = max(CF since the order, 0.001), 0.01 with\n"
        f"< 3 months of data; CF = EIA-923 Page 4 net MWh over the window / (860M winter MW x hours); window\n"
        f"ends at the latest EIA-923 month ({end}); {stamp}"))


def write(path: Path, df: pd.DataFrame, head: str) -> None:
    with open(path, "w", newline="") as f:
        f.write(head)
        f.write(cs.csv_text(df, index=False))
    print("wrote", path.relative_to(REPO), len(df), "rows")


def check(cu, fl, st, holds) -> bool:
    """Compare with the validation tables (coal_spec.md §5)."""
    ok = True
    a = pd.read_csv(cs.SPEC / "coal_cap_units_all.csv")
    x = cu[cu.status.isin(["OP", "SB"])].merge(a, on="kn", how="outer", indicator=True)
    extra = cu[~cu.kn.isin(a.kn)]
    print(f"cap units vs coal_cap_units_all.csv: {(x._merge == 'both').sum()} common; only here: "
          f"{extra[['kn', 'status', 'winter_mw', 'zone']].to_dict('records')}; only there: "
          f"{x.loc[x._merge == 'right_only', 'kn'].tolist()}")
    b = x[x._merge == "both"]
    d = (b.cf_max - b["max annual CF 2021-24"]).abs()
    zb = (b.zone_x.fillna("") != b.zone_final.fillna("")).sum()
    print(f"  unit max CF: max |diff| {d.max():.6f} (tolerance 0.0005); zone mismatches {zb}")
    ok &= bool(d.max() <= 0.0005) and zb == 0 and (x._merge == "right_only").sum() == 0
    hs = pd.read_csv(cs.SPEC / "coal_spec_hold_online.csv", dtype={"generator_id": str})
    hs = hs[hs.in_S0 | hs.in_sensitivity_holds_persist]
    m = holds.merge(hs, on=["plant_id_eia", "generator_id"], how="outer", indicator=True)
    dcf = (m.cf_since_order - m.CF_since_order).abs().max()
    dcap = (m.hold_cap - m.hold_max_annual_CF).abs().max()
    flags = ((m.in_S0_x != m.in_S0_y) | (m.in_holds_persist != m.in_sensitivity_holds_persist)).sum()
    print(f"holds vs coal_spec_hold_online.csv: {len(m)} rows, unmatched {(m._merge != 'both').sum()}; CF max |diff| "
          f"{dcf:.6f} (0.0005); cap max |diff| {dcap:.6f} (exact); flag mismatches {flags}")
    ok &= (m._merge == "both").all() and dcf <= 0.0005 and dcap < 1e-9 and flags == 0
    g = pd.read_csv(cs.SPEC / "coal_spec_converted_gas_units.csv", dtype={"generator_id": str})
    g["kn"] = cs.keys(g.plant_id_eia, g.generator_id)
    f = fl.set_index("kn")
    dmw = (g.kn.map(f["Net Winter Capacity (MW)"]) - g.convert_winter_MW).abs().max()
    dy = (g.kn.map(f["conversion_year"]) != g.conversion_year).sum()
    hr = []
    for r in g.itertuples():
        c = st[(st.plant == r.plant_id_eia) & (st.fuel == "COAL") & (st.year == 2024)]
        hr.append(cs.plant_gas_heat_rate(st, r.plant_id_eia, c.mmbtu.sum() / c.mwh.sum() if c.mwh.sum() > 0 else None)[0])
    dhr = (pd.Series(hr, index=g.index) - g.convert_heat_rate).abs().max()
    print(f"converted units vs coal_spec_converted_gas_units.csv: winter MW max |diff| {dmw} (0.1); conversion "
          f"year mismatches {dy}; heat rate (latest EIA-923, rev. 2.1) max |diff| {dhr:.4f} (0.01)")
    ok &= dmw <= 0.1 and dy == 0 and dhr <= 0.01
    print("CHECK", "OK" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    main()
