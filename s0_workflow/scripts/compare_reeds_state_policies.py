"""Compare the state RPS/CES targets the VM uses with those implied by the pinned ReEDS release (item 4).

Current: pg/extra_inputs/rggi_carbon/emission_policies_current.csv (ESR_<st>_<prog> and
UREC_Limit_ESR_* columns by region and year; created 2025-12-14 from ReEDS main by
make_emission_policies.py, regenerated 2026-05-12).
Pinned: pg/extra_inputs/reeds_state_policies/ (REEDS_RELEASE.yml), turned into program targets with
make_emission_policies.py's rules: model years 2024-29 and 2030-50 by 5; rps_fraction columns
rps_all -> rps, rps_solar, rps_wind; ces_fraction Value -> ces; programs with a target > 0;
UREC_Limit_<program> = oosfrac of the state.
Regions map to states with hierarchy.csv (ba -> st). Writes one row per program and year that differs
(new / dropped / changed) and prints a summary.

usage (repo root): python s0_workflow/scripts/compare_reeds_state_policies.py [--out <csv>]
"""
import argparse
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
PIN = REPO / "pg/extra_inputs/reeds_state_policies"
YEARS = list(range(2024, 2030)) + list(range(2030, 2051, 5))


def pinned_targets() -> pd.DataFrame:
    dfs = []
    for prog in ("ces", "rps"):
        f = pd.read_csv(PIN / f"state_policies/{prog}_fraction.csv").rename(
            columns={"*t": "year", "t": "year", "rps_all": "rps", "Value": "ces"})
        f = f[f["year"].isin(YEARS)].melt(id_vars=["year", "st"], var_name="prog", value_name="target")
        f["program"] = "ESR_" + f["st"] + "_" + f["prog"]
        f = f[f["target"] > 0]
        dfs.append(f[["year", "st", "program", "target"]])
        oos = pd.read_csv(PIN / "state_policies/oosfrac.csv").set_index("*st")["value"]
        u = f.assign(program="UREC_Limit_" + f["program"], target=f["st"].map(oos)).dropna(subset=["target"])
        dfs.append(u[["year", "st", "program", "target"]])
    return pd.concat(dfs, ignore_index=True)


def current_targets(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path)
    st = pd.read_csv(REPO / "hierarchy.csv").set_index("ba")["st"]
    cols = [c for c in d.columns if c.startswith("ESR_") or c.startswith("UREC_Limit_ESR_")]
    long = d.melt(id_vars=["year", "region"], value_vars=cols, var_name="program", value_name="target")
    long["prog_st"] = long["program"].str.replace("UREC_Limit_", "").str.split("_").str[1]
    long = long[long["region"].map(st) == long["prog_st"]]           # the program's own state
    g = long.groupby(["year", "program"])["target"]
    out = g.max().reset_index()
    out["n_values"] = g.nunique().values
    out["st"] = out["program"].str.replace("UREC_Limit_", "").str.split("_").str[1]
    return out[out["target"] > 0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", default=str(REPO / "pg/extra_inputs/rggi_carbon/emission_policies_current.csv"))
    ap.add_argument("--out", default=str(REPO / "s0_workflow/data/reeds_state_policy_diff_2026.09.21.csv"))
    a = ap.parse_args()
    rel = yaml.safe_load(open(PIN / "REEDS_RELEASE.yml"))
    cur, pin = current_targets(Path(a.current)), pinned_targets()
    cur = cur[cur["year"].isin(YEARS)]
    m = cur.merge(pin, on=["year", "program"], how="outer", suffixes=("_current", "_pinned"))
    m["st"] = m["st_current"].fillna(m["st_pinned"])
    m["status"] = "changed"
    m.loc[m["target_current"].isna(), "status"] = "new in release"
    m.loc[m["target_pinned"].isna(), "status"] = "dropped in release"
    m["diff"] = m["target_pinned"] - m["target_current"]
    same = m["diff"].abs() < 1e-6
    d = m[~same].sort_values(["program", "year"])[
        ["program", "st", "year", "target_current", "target_pinned", "diff", "status"]]
    with open(a.out, "w") as f:
        f.write(f"# state RPS/CES targets: {Path(a.current).name} (VM) vs ReEDS {rel['release']} "
                f"(commit {rel['commit'][:10]}); rows that differ only\n")
        d.round(6).to_csv(f, index=False)
    esr = lambda df: df[df["program"].str.startswith("ESR_")]
    print(f"ReEDS {rel['release']} ({rel['commit'][:10]}) vs {Path(a.current).name}")
    print(f"programs: current {esr(cur).program.nunique()}, pinned {esr(pin).program.nunique()}; "
          f"program-years compared {len(m)}, identical {int(same.sum())}, differing {len(d)}")
    print(d.groupby("status").size().to_string())
    big = esr(d[d["status"] == "changed"]).assign(a=lambda x: x["diff"].abs()).sort_values("a", ascending=False)
    print("largest ESR changes:\n" + big.head(15)[["program", "year", "target_current", "target_pinned"]].round(4).to_string(index=False))
    print("ESR programs new / dropped:", sorted(set(esr(d[d.status == "new in release"]).program)),
          sorted(set(esr(d[d.status == "dropped in release"]).program)))
    if (cur["n_values"] > 1).any():
        print("note: some current programs have more than one value within a state:",
              sorted(set(cur[cur.n_values > 1].program))[:10])


if __name__ == "__main__":
    main()
