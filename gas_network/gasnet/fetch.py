"""Download the public inputs into data/raw/ and pin them (URL, release date, sha256) in data/SOURCES.yml.

    python -m gasnet.cli fetch     # download missing files, then verify the sha256 pins (if any)
    python -m gasnet.cli pin       # (re)write SOURCES.yml from the files on disk; only after a deliberate refresh
"""
from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
from pathlib import Path

import pandas as pd
import yaml

from . import PKG

STATES = ("AL AZ AR CA CO CT DE DC FL GA ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR "
          "PA RI SC SD TN TX UT VT VA WA WV WI WY").split()
NEMS_COMMIT = "1bfbb2af4c3eb6e884227630ae67eb0e0e3c72ba"   # EIAgov/NEMS, 2026-06-26 (AEO2026 cases)
NEMS_RAW = f"https://raw.githubusercontent.com/EIAgov/NEMS/{NEMS_COMMIT}"
AEO = "https://www.eia.gov/outlooks/aeo/supplement/excel"
AEO_RELEASE = "April 2026 (AEO2026, run cb2026.d021826b)"


def manifest() -> list[dict]:
    m = [
        dict(id="eia_dry_production_state", path="eia/NG_PROD_SUM_A_EPG0_FPD_MMCF_A.xls",
             url="https://www.eia.gov/dnav/ng/xls/NG_PROD_SUM_A_EPG0_FPD_MMCF_A.xls",
             publisher="EIA Natural Gas Annual (dnav): dry natural gas production by state, annual MMcf"),
        dict(id="eia_consumption_tx", path="eia/NG_CONS_SUM_DCU_STX_A.xls",
             url="https://www.eia.gov/dnav/ng/xls/NG_CONS_SUM_DCU_STX_A.xls",
             publisher="EIA Natural Gas Annual (dnav): Texas consumption by end use, annual MMcf"),
        dict(id="eia_arr_2024", path="eia/ARR_2024_TABLES_ALL.xlsx",
             url="https://www.eia.gov/naturalgas/crudeoilreserves/excel/ARR_2024_TABLES_ALL.xlsx",
             release="2026-04-07",
             publisher="EIA, U.S. Crude Oil and Natural Gas Proved Reserves, Year-end 2024 (Table 8: estimated 2024 "
                       "production by state and subdivision incl. Texas RRC districts, NM East/West, LA North/South)"),
        dict(id="eia860m_aug2026", path="eia/august_generator2026.xlsx",
             url="https://www.eia.gov/electricity/data/eia860m/xls/august_generator2026.xlsx",
             release="2026-09-24", publisher="EIA-860M, Inventory of Operating Generators as of August 2026"),
    ]
    for s in STATES:
        m.append(dict(id=f"eia_movements_{s}", path=f"eia/movements/NG_MOVE_IST_A2DCU_S{s}_A.xls",
                      url=f"https://www.eia.gov/dnav/ng/xls/NG_MOVE_IST_A2DCU_S{s}_A.xls",
                      publisher=f"EIA Natural Gas Annual (dnav): {s} international and interstate movements, annual MMcf"))
    for t, what in [(59, "Lower 48 Natural Gas Production and Supply Prices by Supply Region"),
                    (60, "Shale Gas & Tight Oil Production by Play"), (61, "Natural Gas Imports and Exports"),
                    (62, "Natural Gas Consumption by End-Use Sector and Census Division"),
                    (64, "Primary Natural Gas Flows Entering NGMM Region from Neighboring Regions")]:
        m.append(dict(id=f"aeo2026_suptab_{t}", path=f"aeo2026/suptab_{t}.xlsx", url=f"{AEO}/suptab_{t}.xlsx",
                      release=AEO_RELEASE, publisher=f"EIA AEO2026 supplemental Table {t}: {what}"))
    for p, what in [("models/ngas/input/ngtexas.txt", "NGMM Texas inputs (consumption shares, Texas arc history)"),
                    ("models/ngas/input/texas_capacity.txt", "NGMM intra-Texas and border capacity, MMcf/d"),
                    ("models/ngas/input/ngsetmap.txt", "NGMM sets and maps (state -> flow region, border crossings)")] + [
            (f"models/hsm/input/onshore/projects/on_projects_{k}.csv",
             "HSM onshore project file (state, county FIPS, RRC district) - used only for Texas county -> RRC district")
            for k in ("producing_gas", "producing_oil", "continuous", "undiscovered", "co2_eor")]:
        m.append(dict(id="nems_" + Path(p).stem, path="nems/" + Path(p).name, url=f"{NEMS_RAW}/{p}",
                      release=f"EIAgov/NEMS commit {NEMS_COMMIT[:7]} (2026-06-26)", publisher=f"EIA NEMS (AEO2026): {what}"))
    return m


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def dnav_release(p: Path) -> str | None:
    """Release date printed on a dnav workbook's Contents sheet."""
    try:
        c = pd.read_excel(p, sheet_name="Contents", header=None)
    except Exception:
        return None
    for row in c.itertuples(index=False):
        vals = [str(v) for v in row if str(v) != "nan"]
        if vals and vals[0].startswith("Release Date"):
            return pd.to_datetime(vals[1]).strftime("%Y-%m-%d")
    return None


def fetch(raw: Path, log=print) -> int:
    missing = 0
    for e in manifest():
        dst = raw / e["path"]
        if dst.exists() and dst.stat().st_size > 0:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["curl", "-fsSL", "-m", "300", "-A", "Mozilla/5.0", "-o", str(dst), e["url"]])
        if r.returncode != 0 or not dst.exists():
            log(f"MISSING {e['path']}: download failed from {e['url']}")
            missing += 1
        else:
            log(f"downloaded {e['path']}")
    return missing


def pin(raw: Path, out: Path) -> dict:
    entries = []
    for e in manifest():
        p = raw / e["path"]
        if not p.exists():
            raise FileNotFoundError(f"{p} missing: run `python -m gasnet.cli fetch` first")
        d = dict(e)
        d["release"] = e.get("release") or dnav_release(p) or "not stated in file"
        d["sha256"] = sha256(p)
        d["retrieved"] = dt.date.fromtimestamp(p.stat().st_mtime).isoformat()
        entries.append(d)
    doc = {"note": "Raw files live in gas_network/data/raw/ (git-ignored); `python -m gasnet.cli fetch` re-downloads "
                   "them and checks these sha256 pins.", "files": entries}
    with open(out, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, width=120)
    return doc


def verify(raw: Path, sources: Path) -> list[str]:
    """Paths whose sha256 differs from the pin (empty = all good)."""
    if not sources.exists():
        return []
    doc = yaml.safe_load(open(sources))
    bad = []
    for e in doc["files"]:
        p = raw / e["path"]
        if not p.exists() or sha256(p) != e["sha256"]:
            bad.append(e["path"])
    return bad


def default_paths():
    return PKG / "data/raw", PKG / "data/SOURCES.yml"
