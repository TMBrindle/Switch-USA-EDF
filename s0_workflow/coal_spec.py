"""Coal specification rev. 2 (s0_workflow/specs/coal/coal_spec.md): zonal coal CF caps by stage, the
fleet overrides and the order holds, for the S0 new-defaults cases.

This module holds the rules; data come from two places:
  public EIA files, read by s0_workflow/scripts/fetch_coal_spec_eia.py, which writes the committed tables
    s0_workflow/data/coal_cap_units_860m.csv       the cap units: every 860M Conventional Steam Coal unit
                                                    (OP/SB/OA) with its 2021-24 maximum CF and zone (§1.1-1.4)
    s0_workflow/data/coal_fleet_860m.csv           860M Operating/Retired rows of every plant with a coal-group
                                                    unit, with the conversion year and plant heat rates (§2)
    s0_workflow/data/coal_holds.csv                the order-held units and their hold caps (§3)
  the model fleet (PowerGenome's unit table, VM only), read inside the case build by coal_fleet.py.

Spec references (§) are to coal_spec.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "s0_workflow/specs/coal"
CSC = "Conventional Steam Coal"
IGCC = "Coal Integrated Gasification Combined Cycle"
PETCOKE = "Petroleum Coke"
COAL_GROUP = (CSC, IGCC, PETCOKE)          # PowerGenome tech group "Conventional Steam Coal" (resources.yml)
CAP_STATUSES = ("OP", "SB", "OA")          # §1.1: OS excluded
STAGES = (2028, 2030, 2035, 2040, 2045)
HORIZON = 2045
BLEND_MW = 500.0                           # §1.5 blend weight on the national value
COVERAGE_MIN = 0.5
RULE_OWN, RULE_BLEND, RULE_NAT = "own history (coverage >= 0.5)", "blend", "no history: national"
NO_COAL = " (no model coal after overrides)"


# ------------------------------------------------------------------------------------------- keys (§1.2)
def norm_gen(g) -> str:
    """Generator-ID normalisation: strip whitespace, then leading zeros; empty -> "0"."""
    return str(g).strip().lstrip("0") or "0"


def unit_key(plant, gen) -> str:
    return f"{int(plant)}|{norm_gen(gen)}"


def keys(plants, gens) -> list[str]:
    return [unit_key(p, g) for p, g in zip(plants, gens)]


def check_collisions(name: str, plants, gens, coal_keys: set) -> None:
    """§1.2: stop if two raw generator IDs of one plant normalise to the same key, among coal-group units."""
    d = pd.DataFrame({"raw": [str(g).strip() for g in gens], "k": keys(plants, gens)})
    d = d[d.k.isin(coal_keys)].drop_duplicates()
    n = d.groupby("k").raw.nunique()
    bad = n[n > 1]
    if len(bad):
        detail = d[d.k.isin(bad.index)].groupby("k").raw.apply(list).to_dict()
        raise ValueError(f"{name}: generator IDs collide after normalisation (coal-group units): {detail}")


def norm_county(name) -> str:
    """§1.4 county normalisation."""
    if pd.isna(name):
        return ""
    s = str(name).lower().strip()
    s = s.replace("saint ", "st ").replace("st. ", "st ").replace("ste. ", "ste ")
    s = re.sub(r"\s+(county|parish|borough|census area|city and borough|municipality|municipio|city)$", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


# ------------------------------------------------------------------------------------------- EIA readers
def read_860m(path: Path, sheet: str) -> pd.DataFrame:
    """EIA-860M sheet (header on row 3), with kn, status code and numeric year/capacity columns."""
    x = pd.read_excel(path, sheet_name=sheet, header=2, dtype={"Generator ID": str})
    x = x[pd.to_numeric(x["Plant ID"], errors="coerce").notna()].copy()
    x["Plant ID"] = x["Plant ID"].astype(int)
    x["Generator ID"] = x["Generator ID"].astype(str).str.strip()
    x["kn"] = keys(x["Plant ID"], x["Generator ID"])
    if "Status" in x:
        x["status"] = x["Status"].astype(str).str.extract(r"\((\w\w)\)")[0]
    for c in ("Planned Retirement Year", "Retirement Year", "Net Winter Capacity (MW)", "Operating Year",
              "Nameplate Capacity (MW)", "Latitude", "Longitude"):
        if c in x:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    return x


def read_860_annual(path: Path, sheet: str = "Operable") -> pd.DataFrame:
    """EIA-860 3_1_Generator sheet (header on row 2)."""
    top = pd.read_excel(path, sheet_name=sheet, header=None, nrows=8)
    hdr = int(top.index[(top.astype(str).apply(lambda c: c.str.strip()) == "Plant Code").any(axis=1)][0])
    df = pd.read_excel(path, sheet_name=sheet, header=hdr, dtype={"Generator ID": str})   # row 2; 3 in early releases
    df = df[pd.to_numeric(df["Plant Code"], errors="coerce").notna()].copy()
    df["Plant Code"] = df["Plant Code"].astype(int)
    df["Generator ID"] = df["Generator ID"].astype(str).str.strip()
    for c in ("Winter Capacity (MW)", "Nameplate Capacity (MW)", "Operating Year", "Planned Retirement Year",
              "Retirement Year"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _sheet_with_header(path: Path, sheet: str, first: str) -> pd.DataFrame:
    """EIA-923 sheet whose header row isn't fixed (§ Sources): the row whose first cell is `first`."""
    top = pd.read_excel(path, sheet_name=sheet, header=None, nrows=15)
    rows = top.index[top.iloc[:, 0].astype(str).str.strip() == first]
    if not len(rows):
        raise ValueError(f"{Path(path).name} {sheet}: no header row starting {first!r}")
    d = pd.read_excel(path, sheet_name=sheet, header=int(rows[0]), dtype={"Generator Id": str})
    d.columns = [" ".join(str(c).split()) for c in d.columns]
    return d


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def read_923_page4(path: Path) -> pd.DataFrame:
    """EIA-923 Page 4 Generator Data, long by month: plant, gen (raw), kn, year, month, mwh (NaN if blank)."""
    d = _sheet_with_header(path, "Page 4 Generator Data", "Plant Id")
    d = d[pd.to_numeric(d["Plant Id"], errors="coerce").notna()].copy()
    year = int(pd.to_numeric(d["YEAR"], errors="coerce").dropna().iloc[0])
    out = []
    for i, m in enumerate(MONTHS, 1):
        col = f"Net Generation {m}"
        if col not in d:
            continue
        out.append(pd.DataFrame({"plant": d["Plant Id"].astype(int).values,
                                 "gen": d["Generator Id"].astype(str).str.strip().values,
                                 "year": year, "month": i, "mwh": pd.to_numeric(d[col], errors="coerce").values}))
    g = pd.concat(out, ignore_index=True)
    g["kn"] = keys(g.plant, g.gen)
    return g


def read_923_page1(path: Path) -> pd.DataFrame:
    """EIA-923 Page 1 Generation and Fuel Data, long by month: plant, prime mover, fuel, year, month,
    fuel for electricity (MMBtu), net generation (MWh)."""
    d = _sheet_with_header(path, "Page 1 Generation and Fuel Data", "Plant Id")
    d = d[pd.to_numeric(d["Plant Id"], errors="coerce").notna()].copy()
    year = int(pd.to_numeric(d["YEAR"], errors="coerce").dropna().iloc[0])
    out = []
    for i, m in enumerate(MONTHS, 1):
        f, n = f"Elec_MMBtu {m}", f"Netgen {m}"
        if f not in d or n not in d:
            continue
        out.append(pd.DataFrame({"plant": d["Plant Id"].astype(int).values,
                                 "pm": d["Reported Prime Mover"].astype(str).str.strip().values,
                                 "fuel": d["Reported Fuel Type Code"].astype(str).str.strip().values,
                                 "year": year, "month": i,
                                 "mmbtu": pd.to_numeric(d[f], errors="coerce").values,
                                 "mwh": pd.to_numeric(d[n], errors="coerce").values}))
    return pd.concat(out, ignore_index=True)


# ------------------------------------------------------------------------------------------- CF history (§1.3)
def unit_cf_max(units: pd.DataFrame, gens_by_year: dict[int, pd.DataFrame], page4: pd.DataFrame,
                years=(2021, 2024)) -> pd.DataFrame:
    """Per unit (kn): highest valid annual CF over `years` (clipped to [0, 1]) and the number of valid years.
    units: kn, Operating Year (latest 860M). A unit-year is valid with 12 monthly values, not the operating
    year, not the retirement year, winter capacity > 0. Capacity: that year's EIA-860 winter capacity."""
    cap = pd.concat([g.assign(year=y, kn=keys(g["Plant Code"], g["Generator ID"]))[
        ["kn", "year", "Winter Capacity (MW)"] + (["Retirement Year"] if "Retirement Year" in g else [])]
        for y, g in gens_by_year.items() if years[0] <= y <= years[1]])
    gen = page4[page4.year.between(*years)].groupby(["kn", "year"], as_index=False).agg(
        mwh=("mwh", lambda s: s.sum(min_count=1)), months=("mwh", "count"))
    # one 923 row per raw ID: months counts values per (kn, year) across rows; a unit has one row per year
    uy = units[["kn", "Operating Year"]].merge(cap, on="kn").merge(gen, on=["kn", "year"], how="left")
    hrs = np.where(uy.year % 4 == 0, 8784, 8760)
    uy["cf"] = uy.mwh / (uy["Winter Capacity (MW)"] * hrs)
    valid = (uy.mwh.notna() & (uy.months >= 12) & (uy["Operating Year"] != uy.year)
             & (uy["Winter Capacity (MW)"] > 0))
    if "Retirement Year" in uy:
        valid &= uy["Retirement Year"] != uy.year
    um = uy[valid].groupby("kn").agg(cf_max=("cf", "max"), years=("year", "nunique")).reset_index()
    um["cf_max"] = um.cf_max.clip(0, 1)
    return um


# ------------------------------------------------------------------------------------------- zones (§1.4)
def read_plant_map(path: Path | None = None) -> dict:
    pm = pd.read_csv(path or REPO / "pg/extra_inputs/reeds_plant_map.csv", encoding="utf-8-sig")
    return dict(zip(pm.plant_id_eia.astype(int), pm.region))


def county_zone_map(c2z: pd.DataFrame) -> dict:
    return dict(zip(c2z.state.str.upper() + "|" + c2z.county_name.map(norm_county), c2z.ba))


def place_zones(units: pd.DataFrame, plant_map: dict, c2z: pd.DataFrame, latlon=None) -> pd.DataFrame:
    """zone and zone_source for units with Plant ID, Plant State, County, Latitude, Longitude: the plant
    map, then (state, county), then `latlon` (a callable (lat, lon) -> zone or None; §1.4 step 3)."""
    u = units.copy()
    u["zone"] = u["Plant ID"].map(plant_map)
    u["zone_source"] = np.where(u.zone.notna(), "plant map", None)
    ck = (u["Plant State"].astype(str).str.upper() + "|" + u["County"].map(norm_county)).map(county_zone_map(c2z))
    m = u.zone.isna() & ck.notna()
    u.loc[m, "zone"], u.loc[m, "zone_source"] = ck[m], "county"
    if latlon is not None:
        for i in u.index[u.zone.isna() & u.Latitude.notna()]:
            z = latlon(u.at[i, "Latitude"], u.at[i, "Longitude"])
            if z:
                u.at[i, "zone"], u.at[i, "zone_source"] = z, "lat/lon"
    return u


# ------------------------------------------------------------------------------------------- cap unit set (§1.1)
def cap_units(op860m: pd.DataFrame, gens_by_year: dict[int, pd.DataFrame], page4: pd.DataFrame,
              plant_map: dict, c2z: pd.DataFrame, latlon=None) -> pd.DataFrame:
    """Every latest-860M Operating unit with Technology Conventional Steam Coal and status OP/SB/OA, any planned
    retirement, with its zone and 2021-24 CF maximum: the source of every stage's cap unit set."""
    u = op860m[(op860m.Technology == CSC) & op860m.status.isin(CAP_STATUSES)].copy()
    u = u.merge(unit_cf_max(u, gens_by_year, page4), on="kn", how="left")
    u = place_zones(u, plant_map, c2z, latlon)
    return u.rename(columns={"Net Winter Capacity (MW)": "winter_mw"})[
        ["kn", "Plant ID", "Generator ID", "Plant Name", "Plant State", "County", "Latitude", "Longitude",
         "zone", "zone_source", "Technology", "Energy Source Code", "status", "Operating Year",
         "Planned Retirement Year", "winter_mw", "cf_max", "years"]].sort_values("kn").reset_index(drop=True)


# ------------------------------------------------------------------------------------------- pre-2030 retirements
RETIREMENT_OPTIONS = ("block_all", "planned_only", "unrestricted")
# fedpol's blocked_2030_coal_gas predetermined-retirement override (scenario_management.yml retirement_policy)
BLOCK_RULE = {"technologies": ["coal", "natural gas"], "window": [2026, 2029], "target_year": 2030}


def pushed_year(y, rule: dict | None):
    """A retirement year under a predetermined-retirement push rule (window inclusive -> target). With Switch's
    --retire early a unit with retirement year Y runs in a period iff Y >= the period's end (its label), so fedpol's
    push to 2030 keeps a unit dated 2026-29 through the 2030 stage; it first disappears from the 2035 stage.
    PowerGenome counts a unit in model year M only if Y > M, so the S0 build encodes the pushed year as
    target + 1 (2031): the same stages in Switch, and PowerGenome keeps it in model year 2030 too."""
    if rule is None or y is None or pd.isna(y):
        return y
    a, b = rule["window"]
    return int(rule["target_year"]) + 1 if a <= y <= b else y


def stage_units(cu: pd.DataFrame, stage: int, held: set, rule: dict | None = None) -> pd.DataFrame:
    """§1.1 rules 3-4: planned retirement blank or >= stage (after the pre-2030 push, if any), not a held unit
    (in either hold scenario)."""
    pry = pd.to_numeric(cu["Planned Retirement Year"], errors="coerce").map(lambda y: pushed_year(y, rule))
    pry = pd.to_numeric(pry, errors="coerce")
    return cu[(pry.isna() | (pry >= stage)) & ~cu.kn.isin(held)]


def stage_history(cu: pd.DataFrame, stage: int, held: set, rule: dict | None = None) -> tuple[pd.DataFrame, float]:
    """(per-zone H, n_units, own) and the national fallback N for one stage (§1.5)."""
    h = stage_units(cu, stage, held, rule)
    h = h[h.zone.notna() & h.cf_max.notna()]
    nat = float(np.average(h.cf_max, weights=h.winter_mw))
    z = weighted_by(h, "zone", "cf_max", "winter_mw").rename(columns={"weight": "hist_MW", "mean": "own_cap"})
    return z[["hist_MW", "n_units", "own_cap"]], nat


def weighted_by(df: pd.DataFrame, by: str, value: str, weight: str) -> pd.DataFrame:
    """Per group: sum of weights, row count and weighted mean of `value` (np.average per group). Plain aggregations
    rather than groupby.apply, for pandas 1.4 (the case-build env) and 2.x alike."""
    d = pd.DataFrame({by: df[by].values, "weight": df[weight].values.astype(float),
                      "vw": (df[value] * df[weight]).values.astype(float)})
    g = d.groupby(by).agg(weight=("weight", "sum"), n_units=("weight", "size"), vw=("vw", "sum"))
    g["mean"] = g.vw / g.weight
    g.index.name = by
    return g.drop(columns="vw")


def csv_text(df: pd.DataFrame, **kw) -> str:
    """df.to_csv as text with "\n" line ends on every platform and pandas version (to_csv's line-terminator argument
    was renamed in pandas 1.5)."""
    return df.to_csv(**kw).replace("\r\n", "\n")


def apply_rule(hist: pd.DataFrame, model_before: pd.Series, model_after: pd.Series, nat: float) -> pd.DataFrame:
    """§1.5 coverage rule per zone: hist (hist_MW, n_units, own_cap by zone), model MW before / after the
    overrides (winter MW in service in the stage, coal clusters only). Labels as coal_spec_build.py."""
    e = pd.DataFrame({"model_MW_before": model_before, "model_MW_after_overrides": model_after})
    e = e.join(hist, how="outer")
    e[["model_MW_before", "model_MW_after_overrides"]] = e[["model_MW_before", "model_MW_after_overrides"]].fillna(0)
    e["coverage"] = e.hist_MW / e.model_MW_after_overrides.replace(0, np.nan)
    own = e.coverage.fillna(np.inf) >= COVERAGE_MIN
    e["rule"] = np.where(e.hist_MW.isna(), RULE_NAT, np.where(own, RULE_OWN, RULE_BLEND))
    e["expected_cap"] = np.where(e.hist_MW.isna(), nat, np.where(
        own, e.own_cap, (e.hist_MW * e.own_cap + BLEND_MW * nat) / (e.hist_MW + BLEND_MW)))
    none_left = e.model_MW_after_overrides <= 0
    e.loc[none_left, "rule"] = e.loc[none_left, "rule"] + NO_COAL
    e["national_N"] = nat
    e.index.name = "zone"
    return e


def aggregate_rule(zv: pd.DataFrame, members: dict, nat: float) -> pd.DataFrame:
    """§1.5 aggregated load zones: H-weighted own, H and M summed, the rule applied to the aggregate.
    zv: apply_rule output by BA; members: load zone -> [BAs]."""
    rows = {}
    for z, bas in members.items():
        x = zv.reindex([b for b in bas if b in zv.index])
        h = x.hist_MW.sum(min_count=1)
        own = float(np.average(x.own_cap.dropna(), weights=x.hist_MW.dropna())) if pd.notna(h) and h > 0 else np.nan
        rows[z] = {"hist_MW": h if pd.notna(h) and h > 0 else np.nan, "n_units": x.n_units.sum(), "own_cap": own}
    hist = pd.DataFrame.from_dict(rows, orient="index")
    hist = hist[hist.hist_MW.notna()]
    mb = {z: zv.reindex(b).model_MW_before.sum() for z, b in members.items()}
    ma = {z: zv.reindex(b).model_MW_after_overrides.sum() for z, b in members.items()}
    return apply_rule(hist, pd.Series(mb), pd.Series(ma), nat)


def availability(cap: float, forced_outage_rate: float) -> float:
    """Switch gen_max_annual_availability for a CF cap (§1.5, §3.5)."""
    return float(min(1.0, cap / (1.0 - (forced_outage_rate or 0.0))))


# ------------------------------------------------------------------------------------------- committed data
def read_table(path: Path, **kw) -> pd.DataFrame:
    """CSV with leading "# " header lines (not comment="#": plant names can contain "#")."""
    with open(path) as f:
        n = 0
        for line in f:
            if not line.startswith("#"):
                break
            n += 1
    return pd.read_csv(path, skiprows=n, **kw)


def load_cap_units(path: Path | None = None) -> pd.DataFrame:
    return read_table(path or REPO / "s0_workflow/data/coal_cap_units_860m.csv", dtype={"Generator ID": str})


def load_holds(path: Path | None = None) -> pd.DataFrame:
    h = read_table(path or REPO / "s0_workflow/data/coal_holds.csv", dtype={"generator_id": str})
    h["kn"] = keys(h.plant_id_eia, h.generator_id)
    return h


def held_keys(holds: pd.DataFrame) -> set:
    """Units held in either scenario: excluded from every stage's zone values (§1.1 rule 4, §1.5)."""
    return set(holds.loc[holds.in_S0 | holds.in_holds_persist, "kn"])


# ------------------------------------------------------------------------------------------- holds (§3.1)
HOLD_FLOOR, HOLD_NO_DATA, HOLD_MIN_MONTHS = 0.001, 0.01, 3


def hold_cap(page4: pd.DataFrame, kn: str, winter_mw: float, start: str, end: str, fallback_start: str | None = None
             ) -> dict:
    """§3.1 hold cap from monthly EIA-923 Page 4 net generation for one unit over [start, end] (YYYY-MM).
    CF = sum of net MWh / (winter MW x hours of the months with values); negative totals count as 0;
    cap = max(CF, 0.001), or 0.01 with fewer than 3 months of values. With < 3 months and a
    fallback_start earlier than start (first order < 3 months old), the window starts there."""
    def window(s):
        x = page4[page4.kn == kn].copy()
        x["ym"] = x.year.astype(str) + "-" + x.month.map("{:02d}".format)
        x = x[(x.ym >= s) & (x.ym <= end)]
        return x.groupby("ym").mwh.apply(lambda v: v.sum(min_count=1))
    w, used = window(start), start
    if w.notna().sum() < HOLD_MIN_MONTHS and fallback_start and fallback_start < start:
        w, used = window(fallback_start), fallback_start
    vals = w.dropna()
    hrs = sum(pd.Period(m).days_in_month * 24 for m in vals.index)
    n = len(vals)
    cf = max(float(vals.sum()) / (winter_mw * hrs), 0.0) if n >= HOLD_MIN_MONTHS and winter_mw > 0 else np.nan
    cap = round(max(cf, HOLD_FLOOR), 4) if pd.notna(cf) else HOLD_NO_DATA
    return {"window": f"{used} to {end}", "months_with_data": n, "net_mwh": float(vals.sum()) if n else np.nan,
            "cf_since_order": cf, "hold_cap": cap}


# ------------------------------------------------------------------------------------------- converted units (§2.3)
def conversion_year(codes: pd.Series, latest_year: int = 2026) -> int:
    """First year of the unit's latest unbroken run of NG coding. codes: energy source code by year (annual
    EIA-860 2020-24, the 2025 record, then the latest 860M as `latest_year`); `latest_year` if the year
    before it still codes coal."""
    c = codes.sort_index()
    hist = c[c.index < latest_year]
    if not len(hist) or hist.iloc[-1] != "NG":
        return latest_year
    yrs, ng = list(hist.index), list(hist == "NG")
    i = len(ng) - 1
    while i > 0 and ng[i - 1]:
        i -= 1
    return int(yrs[i])


GAS_HR_MIN_MWH = 10_000.0


def plant_gas_heat_rate(st_monthly: pd.DataFrame, plant: int, coal_hr_2024: float | None, through: str | None = None
                        ) -> tuple[float, str, bool]:
    """§2.3 full-load heat rate of a converted unit: the plant's ST/NG fuel for electricity / net generation in
    the most recent year with >= 10 GWh (partial years allowed); else the 2024 coal heat rate, flagged.
    st_monthly: plant, year, month, fuel (NG | COAL), mmbtu, mwh (prime mover ST). `through` (YYYY-MM)
    limits the data to months up to then. Returns (heat rate, source, flagged)."""
    x = st_monthly[(st_monthly.plant == plant) & (st_monthly.fuel == "NG")]
    if through:
        x = x[(x.year.astype(str) + "-" + x.month.map("{:02d}".format)) <= through]
    a = x.groupby("year").agg(f=("mmbtu", "sum"), n=("mwh", "sum"),
                              months=("mwh", lambda s: int((s.fillna(0) != 0).sum())))
    a = a[a.n >= GAS_HR_MIN_MWH].sort_index()
    if len(a):
        y = int(a.index[-1])
        return (round(float(a.at[y, "f"] / a.at[y, "n"]), 3),
                f"EIA-923 plant ST/NG {y} ({a.at[y, 'months']} months, {a.at[y, 'n'] / 1e3:.0f} GWh)", False)
    hr = round(float(coal_hr_2024), 3) if coal_hr_2024 is not None and pd.notna(coal_hr_2024) else np.nan
    return hr, "no gas-fired history: unit's 2024 coal heat rate kept (flag)", True


# ------------------------------------------------------------------------------------------- model basis (public)
def model_basis_from_eia(gens2024: pd.DataFrame, retired_model_860m: pd.DataFrame, plant_map: dict,
                         c2z: pd.DataFrame) -> pd.DataFrame:
    """A public reconstruction of PowerGenome's coal-group fleet (§2.1): EIA-860 2024 (early release, the vintage of
    PUDL 2025_08) Operable units of the coal group, except status OS; retirement year = the planned retirement year,
    or the Retirement Year of PowerGenome's 860M (July 2025) Retired sheet; zone from the plant map, then county (the
    county-placed plants are not in PowerGenome's fleet: load_model_basis leaves them out); winter MW, or the
    nameplate MW where EIA-860 has no winter MW (Edwardsport CT1 / CT2, 240.6 MW each, as PowerGenome's unit table
    has them: VM build at 88e6b30). Used for the per-option validation tables; the case build uses PowerGenome's own
    unit table."""
    g = gens2024[gens2024.Technology.isin(COAL_GROUP)].copy()
    g = g[g.Status.astype(str).str.strip() != "OS"]
    g["kn"] = keys(g["Plant Code"], g["Generator ID"])
    rt = retired_model_860m.drop_duplicates("kn").set_index("kn")["Retirement Year"]
    g["retirement_year_basis"] = g["Planned Retirement Year"]
    m = g.kn.isin(rt.index)
    g.loc[m, "retirement_year_basis"] = g.loc[m, "kn"].map(rt)
    z = place_zones(g.rename(columns={"Plant Code": "Plant ID", "State": "Plant State"}).assign(Latitude=np.nan,
                                                                                                 Longitude=np.nan),
                    plant_map, c2z)
    out = pd.DataFrame({"kn": g.kn.values, "plant_id_eia": g["Plant Code"].values, "generator_id": g["Generator ID"].values,
                        "technology_description": g.Technology.values, "status": g.Status.astype(str).str.strip().values,
                        "model_region": z.zone.values, "zone_source": z.zone_source.values,
                        "winter_capacity_mw": pd.to_numeric(g["Winter Capacity (MW)"], errors="coerce").fillna(
                            pd.to_numeric(g["Nameplate Capacity (MW)"], errors="coerce")).values,
                        "operating_year": g["Operating Year"].values,
                        "retirement_year_basis": g.retirement_year_basis.values})
    return out.sort_values("kn").reset_index(drop=True)


NOT_IN_MODEL = "not in model: plant not in reeds_plant_map.csv"


def in_plant_map(plants, plant_map: dict | None = None) -> np.ndarray:
    """PowerGenome's EIA-860 unit table keeps only plants with a model region, and with no region_aggregations
    (model_definition.yml) only reeds_plant_map.csv gives one: a plant missing from it never enters the model's
    EIA-860 units (Tom 2026-10-04; Biron Mill comes back through the 860M new-generator rows and is removed)."""
    pm = read_plant_map() if plant_map is None else plant_map
    return pd.Series(plants).astype(int).isin(set(pm)).values


def not_in_model(path: Path | None = None, plant_map: dict | None = None) -> pd.DataFrame:
    """Coal-group units of the public basis whose plant is not in reeds_plant_map.csv: left out of the expected
    tables (a known gap for the fleet refresh), with the zone the county step would give them."""
    b = read_table(path or REPO / "s0_workflow/data/coal_model_basis_860er2024.csv", dtype={"generator_id": str})
    b = b[~in_plant_map(b.plant_id_eia, plant_map)]
    return b.assign(status=NOT_IN_MODEL)[["kn", "plant_id_eia", "generator_id", "technology_description",
                                          "model_region", "winter_capacity_mw", "status"]].rename(
        columns={"model_region": "county_zone"}).reset_index(drop=True)


def load_model_basis(path: Path | None = None, plant_map: dict | None = None) -> pd.DataFrame:
    """The committed basis as a PowerGenome-like unit table (retirement_year: the basis year, or operating year + 500
    with none, as PowerGenome encodes no planned retirement): plants in reeds_plant_map.csv only (not_in_model)."""
    b = read_table(path or REPO / "s0_workflow/data/coal_model_basis_860er2024.csv", dtype={"generator_id": str})
    b = b[b.model_region.notna() & in_plant_map(b.plant_id_eia, plant_map)].copy()
    b["retirement_year"] = b.retirement_year_basis.fillna(b.operating_year + 500)
    b["operating_date"] = b.operating_year
    b["retirement_age"] = 500
    return b
