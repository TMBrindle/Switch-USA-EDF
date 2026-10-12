"""v3.2 build-rate package (CHANGES §121-§125): R and the ramp bound by technology and S0 stage, v3.1 against each flag.

    python scripts/v32_report.py --v31 <tables> --r2 <tables> --r7 <tables> --r2r7 <tables> --out <csv>

Levels: --level (default central; S1 runs central, S2 / S4 high). <tables> are `python -m brc.cli run --outputs <dir>` folders made from the same raw data (v3.1: no --set; r2: --set
near_term.handover.mode=linear; r7: --set near_term.construction_completion_source=lbnl_post_ia; r2r7: both).

Per group and stage (S0 mode A: 2028 = 2026-28, 2030 = 2029-30, 2035 = 2031-35, 2040 = 2036-40, 2045 = 2041-45):
  R            national data rate, the window mean (MW/yr), for each table set (R2 and R7 act here)
  ramp bound   on R in the stage, given the previous stage's build b (MW/yr): (1+growth)^W x base + floor, with
               base = b (v3.1) or max(b, previous stage's R) (R1), floor 1,000 (v3.1) or 4,800 MW/yr (R4); shown for
               b = 0, 0.5 R_prev, R_prev and 1.3 R_prev (the free band full) and 2.0 R_prev (the ceiling)
  R11          R's upper bound max(R, b / 1.3) for the same b
  binding R    min(R's upper bound, ramp bound): the R the stage can use (ceiling = 2.0 x it)
Committed (predetermined) builds, which raise R and the ramp in the case writer and module, are not included.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

STAGES = [(2028, 2026, 2028), (2030, 2029, 2030), (2035, 2031, 2035), (2040, 2036, 2040), (2045, 2041, 2045)]
GROUPS = ["wind_onshore", "solar", "storage"]
REPO = Path(__file__).resolve().parents[2]


def stage_r(tables: Path, level="central") -> pd.DataFrame:
    r = pd.read_csv(Path(tables) / f"rates_{level}.csv")
    n = r[r.region == "national"]
    rows = []
    for g in GROUPS:
        s = n[n.group == g].set_index("year")["r_data_mw_per_yr"]
        growth = float(n[n.group == g]["growth"].iat[0])
        for p, a, b in STAGES:
            rows.append({"group": g, "stage": p, "window": f"{a}-{b}", "W": b - a + 1, "growth": growth,
                         "R": float(s.loc[a:b].mean())})
    return pd.DataFrame(rows)


def report(sets: dict[str, Path], level: str = "central") -> pd.DataFrame:
    cfg = yaml.safe_load(open(REPO / "build_rate/config.yaml"))
    h = pd.read_csv(REPO / "hierarchy.csv")
    nst = h.loc[h.country == "USA", "st"].nunique()
    base = {k: stage_r(v, level) for k, v in sets.items()}
    out = []
    for name, t in base.items():
        for g in GROUPS:
            d = t[t.group == g].reset_index(drop=True)
            f31 = float(cfg["ramp_floor_mw"][g])
            f4 = float(cfg["ramp_floor_reeds"]["gbin_min_mw_per_state"][g]) * nst
            for i, r in d.iterrows():
                row = {"tables": name, "group": g, "stage": r.stage, "window": r.window, "R": r.R}
                if i == 0:
                    out.append({**row, "prev_build_x_Rprev": None})
                    continue
                rp = d.R[i - 1]
                gw = (1 + r.growth) ** r.W
                for k in (0.0, 0.5, 1.0, 1.3, 2.0):
                    b = k * rp
                    v = {"ramp_v31": gw * b + f31, "ramp_R1": gw * max(b, rp) + f31, "ramp_R4": gw * b + f4,
                         "ramp_R1_R4": gw * max(b, rp) + f4, "cap_R11": max(r.R, b / 1.3)}
                    v["binding_v31"] = min(r.R, v["ramp_v31"])
                    v["binding_all"] = min(v["cap_R11"], v["ramp_R1_R4"])
                    out.append({**row, "R_prev": rp, "prev_build_x_Rprev": k, "prev_build": b, **v})
    return pd.DataFrame(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("v31", "r2", "r7", "r2r7"):
        ap.add_argument(f"--{k}", required=k == "v31")
    ap.add_argument("--out", required=True)
    ap.add_argument("--level", default="central", help="build-rate level (S1: central, S2 / S4: high)")
    a = ap.parse_args(argv)
    sets = {k: Path(getattr(a, k)) for k in ("v31", "r2", "r7", "r2r7") if getattr(a, k)}
    rep = report(sets, a.level)
    rep.round(1).to_csv(a.out, index=False)
    piv = rep[rep.prev_build_x_Rprev.isna() | (rep.prev_build_x_Rprev == 1.0)].pivot_table(
        index=["group", "stage"], columns="tables", values="R").round(0)
    print(piv.to_string())


if __name__ == "__main__":
    main()
