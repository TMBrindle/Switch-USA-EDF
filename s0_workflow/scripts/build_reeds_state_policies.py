"""Build the state RPS/CES policy inputs from the pinned ReEDS release, for the S0 new-defaults cases.

make_emission_policies.py writes the shared policy files in place (rggi_carbon/emission_policies_current.csv,
the ESR_* entries of regional_resource_tags.yml, the ESR_* tag lists of resource_tags.yml and
model_definition.yml). Rerunning it would change every case. This script applies the same rules to the
pinned copies in pg/extra_inputs/reeds_state_policies/ (REEDS_RELEASE.yml) and writes NEW files only:

  pg/extra_inputs/rggi_carbon/emission_policies_reeds_<release>.csv
      same layout as emission_policies_current.csv: ESR_<st>_<prog> targets, UREC_Limit_ESR_* out-of-state
      REC limits, and the carbon columns (RGGI cap from rggi_carbon/rggicon_3pr.csv, RGGI states from
      the pinned rggi_states.csv, CA and WA carbon prices)
  pg/extra_inputs/reeds_state_policies/s0_state_policies_<release>.yml
      ESR eligibility by region and program (regional_tag_values form) and the ESR tag list

S0 cases select them with s0_production.state_policies.release (s0_workflow/production.py,
apply_state_policies); the legacy regression case keeps the current files.

Rules (make_emission_policies.py, sections "ESR target", "ESR eligibility" and "carbon targets"):
model years 2024-29 and 2030-50 by 5; rps_fraction rps_all -> rps (plus rps_solar, rps_wind);
ces_fraction Value -> ces; programs with a target > 0; UREC_Limit_<program> = the state's oosfrac.
RPS-eligible = RE techs in tech-subset-table less techs_banned_rps (can-imports also banned in CA, AZ,
NM, TX); CES-eligible = RE, NUCLEAR, HYDRO, CCS or CANADA less both ban lists; rps_solar = PV/PVB,
rps_wind = WIND. Partner states in rectable add <program>_bundled (trade 1 or 2) and _unbundled
(trade 1). ReEDS techs map to PowerGenome techs with pg_reeds_tech_map.csv; states to regions with the
region shapefile's attribute table (US_PCA_region.dbf). Offshore wind mandates are not rebuilt: the
pinned offshore_req_default.csv gives the same MinCapReq values as scenario_management.yml in every
model year (checked by the test).

usage (repo root): python s0_workflow/scripts/build_reeds_state_policies.py [--check]
  --check  build in memory and compare with the committed files (exit 1 if they differ)
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
EXTRA = REPO / "pg/extra_inputs"
PIN = EXTRA / "reeds_state_policies"
YEARS = list(range(2024, 2030)) + list(range(2030, 2051, 5))
TAX_STATES = ["CA", "WA"]


def release() -> dict:
    return yaml.safe_load(open(PIN / "REEDS_RELEASE.yml"))


def output_paths(rel: str) -> tuple[Path, Path]:
    return (EXTRA / f"rggi_carbon/emission_policies_reeds_{rel}.csv",
            PIN / f"s0_state_policies_{rel}.yml")


def read_dbf(path: Path) -> pd.DataFrame:
    """Attribute table of a shapefile (dBase III), without geopandas."""
    with open(path, "rb") as f:
        n, header_len, rec_len = struct.unpack("<xxxxLHH", f.read(12))
        f.seek(32)
        fields = []
        while True:
            d = f.read(32)
            if d[0] == 0x0D:
                break
            fields.append((d[:11].split(b"\0")[0].decode(), chr(d[11]), d[16]))
        f.seek(header_len)
        rows = []
        for _ in range(n):
            r = f.read(rec_len)
            if r[:1] == b"*":
                continue
            pos, row = 1, {}
            for name, _typ, size in fields:
                row[name] = r[pos:pos + size].decode("latin-1").strip()
                pos += size
            rows.append(row)
    return pd.DataFrame(rows)


def region_states() -> pd.DataFrame:
    r = read_dbf(EXTRA / "US_PCA_region/US_PCA_region.dbf")
    return pd.DataFrame({"st": r["st"].str.upper(), "region": r["region"]})


def targets(region_info: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(esr_long, esr_wide) as make_emission_policies.py builds them."""
    dfs = []
    oos_limit = pd.read_csv(PIN / "state_policies/oosfrac.csv").set_index("*st")["value"]
    for prog in ["ces", "rps"]:
        frac = pd.read_csv(PIN / f"state_policies/{prog}_fraction.csv").rename(
            columns={"*t": "year", "t": "year", "rps_all": "rps", "Value": "ces"})
        frac = frac[frac["year"].isin(YEARS)]
        frac = frac.melt(id_vars=["year", "st"], var_name="prog", value_name="target")
        frac["program"] = "ESR_" + frac["st"] + "_" + frac["prog"]
        frac = frac[frac["target"] > 0]
        dfs.append(frac)
        dfs.append(frac.assign(program="UREC_Limit_" + frac["program"], target=frac["st"].map(oos_limit))
                   .dropna(subset=["target"]))
    esr_long = pd.concat(dfs).merge(region_info, on="st")
    esr_wide = esr_long.pivot(index=["year", "region"], values="target", columns="program")
    return esr_long, esr_wide[sorted(esr_wide.columns)]


_RANGE = re.compile(r"^(?P<prefix>.+?)_(?P<start>\d+)\*(?P=prefix)_(?P<stop>\d+)$")


def expand_label(s: str) -> list[str]:
    m = _RANGE.match(s)
    if not m:
        return [s]
    return [f"{m['prefix']}_{i}" for i in range(int(m["start"]), int(m["stop"]) + 1)]


def eligibility(esr_long: pd.DataFrame, region_info: pd.DataFrame) -> pd.DataFrame:
    """Rows (st, program, reeds_tech, pg_tech, region) of eligible techs, incl. bundled/unbundled trade."""
    techs = pd.read_csv(PIN / "tech-subset-table.csv")
    techs = techs.rename(columns={techs.columns[0]: "reeds_tech"})
    techs["reeds_tech"] = techs["reeds_tech"].apply(expand_label)
    techs = techs.explode("reeds_tech", ignore_index=True)
    re_techs = techs.query("RE == 'YES'")["reeds_tech"]
    solar_techs = techs.query("PV == 'YES' or PVB == 'YES'")["reeds_tech"]
    wind_techs = techs.query("WIND == 'YES'")["reeds_tech"]
    ce_techs = techs.loc[techs[["RE", "NUCLEAR", "HYDRO", "CCS", "CANADA"]].notna().any(axis=1), "reeds_tech"]
    banned_ces = pd.read_csv(PIN / "state_policies/techs_banned_ces.csv")
    banned_rps = pd.read_csv(PIN / "state_policies/techs_banned_rps.csv")
    banned_rps.loc[banned_rps["i"] == "can-imports", ["CA", "AZ", "NM", "TX"]] = 1
    rps_ban = {st: banned_rps.loc[col.notna(), "i"] for st, col in banned_rps.iloc[:, 1:].items()}
    ces_ban = {st: banned_ces.loc[col.notna(), "i"] for st, col in banned_ces.iloc[:, 1:].items()}
    rules = {"rps": (re_techs, [rps_ban]), "rps_solar": (solar_techs, [rps_ban]),
             "rps_wind": (wind_techs, [rps_ban]), "ces": (ce_techs, [rps_ban, ces_ban])}

    trade = pd.read_csv(PIN / "state_policies/rectable.csv").melt(
        id_vars="st", var_name="ast", value_name="trade").query("st != ast and trade > 0")
    bundled, unbundled = defaultdict(list), defaultdict(list)
    for r in trade.itertuples():
        bundled[r.st].append(r.ast)
        if r.trade == 1:
            unbundled[r.st].append(r.ast)

    dfs = []
    for st, prog in esr_long[["st", "prog"]].drop_duplicates().itertuples(index=False):
        t, bans = rules[prog]
        for ban in bans:
            t = t[~t.isin(ban.get(st, []))]
        local = pd.DataFrame({"st": st, "program": f"ESR_{st}_{prog}", "reeds_tech": t})
        dfs.append(local)
        for tag, partners in [("bundled", bundled[st]), ("unbundled", unbundled[st])]:
            for ast in partners:
                dfs.append(local.assign(st=ast, program=local["program"] + "_" + tag))
    el = pd.concat(dfs, ignore_index=True)
    # row order as an order-preserving inner merge (pandas >= 2.2): left rows, then the map's rows; pandas 1.4 groups by
    # key, which would reorder the tags in the yml
    tmap = pd.read_csv(EXTRA / "pg_reeds_tech_map.csv").reset_index().rename(columns={"index": "_r"})
    el = (el.reset_index(drop=True).reset_index().rename(columns={"index": "_l"}).merge(tmap, on="reeds_tech")
          .sort_values(["_l", "_r"], kind="mergesort").drop(columns=["_l", "_r"]).reset_index(drop=True))
    return el.merge(region_info, on="st")


def carbon(region_info: pd.DataFrame) -> pd.DataFrame:
    """Carbon columns as make_emission_policies.py writes them (RGGI cap zone 1, CA and WA prices)."""
    local = EXTRA / "rggi_carbon/rggicon_3pr.csv"
    cap = pd.read_csv(local if local.exists() else PIN / "emission_constraints/rggicon.csv",
                      names=["year", "cap"], header=None)
    cap = cap[cap["year"].isin(YEARS)].copy()
    cap["cap"] *= 0.000001
    states = pd.read_csv(PIN / "emission_constraints/rggi_states.csv").rename(columns={"*st": "st"})
    co2 = cap.assign(dummy=1).merge(states.assign(dummy=1), on="dummy").drop(columns="dummy")
    co2["CO_2_Cap_Zone_1"] = 1
    co2 = co2.rename(columns={"cap": "CO_2_Max_Mtons_1"})
    long = co2.melt(id_vars=["year", "st"], var_name="col", value_name="value")
    taxes = pd.DataFrame([(y, st, c, v) for i, st in enumerate(TAX_STATES) for y in long["year"].unique()
                          for c, v in [(f"CO_2_Cap_Zone_{i + 2}", 1), (f"CO_2_Max_Mtons_{i + 2}", 0)]],
                         columns=["year", "st", "col", "value"])
    long = pd.concat([long, taxes]).merge(region_info, on="st").drop(columns="st")
    wide = long.pivot(columns="col", index=["year", "region"], values="value")
    return wide[sorted(wide.columns, key=lambda c: (int(c.split("_")[-1]), c))]


def policy_table(esr_wide: pd.DataFrame, co2_wide: pd.DataFrame) -> pd.DataFrame:
    ep = pd.concat([esr_wide, co2_wide], axis=1)
    ep = ep.loc[sorted(ep.index.to_list(), key=lambda r: (r[0], int(r[1][1:]))), :]
    for c in ep.columns:
        if c.startswith("CO_2_Cap_Zone_"):
            ep[c] = ep[c].astype("Int64")
    for c in ep.columns:
        if c.startswith("CO_2_Max_Mtons_"):
            nz = ep[c] > 0
            ep.loc[nz & (nz.groupby(level="year").cumsum() > 1), c] = 0
    ep = pd.concat({"all": ep}, names=["case_id"])
    return ep.fillna(0)


def tags_doc(el: pd.DataFrame, rel: dict) -> dict:
    el = el.assign(region_num=el["region"].str[1:].astype(int))
    rtv = {}
    for (_n, r), g1 in el.groupby(["region_num", "region"]):
        rtv[r] = {p: {t: 1 for t in g2["pg_tech"]} for p, g2 in g1.groupby("program")}
    return {
        "release": str(rel["release"]),
        "commit": rel["commit"],
        "emission_policies_fn": f"rggi_carbon/emission_policies_reeds_{rel['release']}.csv",
        "esr_tags": sorted(set(el["program"])),
        "regional_tag_values": rtv,
    }


def build() -> tuple[pd.DataFrame, dict]:
    rel = release()
    region_info = region_states()
    esr_long, esr_wide = targets(region_info)
    el = eligibility(esr_long, region_info)
    return policy_table(esr_wide, carbon(region_info)), tags_doc(el, rel)


def csv_text(ep: pd.DataFrame) -> str:
    return ep.to_csv(index=True).replace("\r\n", "\n")         # as make_emission_policies.py writes it (any pandas)


HEADER = ("# S0 state-policy eligibility from ReEDS release {release} (commit {commit}).\n"
          "# Written by s0_workflow/scripts/build_reeds_state_policies.py; do not edit by hand.\n"
          "# Used by S0 cases with s0_production.state_policies.release = \"{release}\" (production.py,\n"
          "# apply_state_policies), which replace their ESR_* regional tags, ESR tag list and\n"
          "# emission_policies_fn with these. Other cases use regional_resource_tags.yml as before.\n")


def yml_text(doc: dict) -> str:
    return HEADER.format(**doc) + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=120)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    ep, doc = build()
    csv_path, yml_path = output_paths(doc["release"])
    texts = {csv_path: csv_text(ep), yml_path: yml_text(doc)}
    if a.check:
        bad = [p for p, t in texts.items() if not p.exists() or p.read_text() != t]
        for p in bad:
            print(f"differs from a fresh build: {p.relative_to(REPO)}")
        sys.exit(1 if bad else 0)
    for p, t in texts.items():
        p.write_text(t)
        print(f"wrote {p.relative_to(REPO)}")
    print(f"{len(doc['esr_tags'])} ESR tags; {ep.shape[0]} region-years; "
          f"{sum(c.startswith('ESR_') for c in ep.columns)} target columns")


if __name__ == "__main__":
    main()
