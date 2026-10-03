"""Compare an S0 production build with the hand-built test run it replaces (VM regression, item 9).

  inputs   NEW_CASE OLD_CASE [--alias name=file ...]
           Compare every case input file. --alias maps a file name to the alias the old run solved with
           (e.g. gen_info.csv=gen_info.ic_v2.coalcfhist.csv), so the comparison is with what was solved.
           Reports, per file: identical / same rows in another order / numeric differences (max abs,
           max rel, rows) / different rows. Exit code 0 always; read the report.

  outputs  NEW_OUT OLD_OUT [--period 2035] [--tolerance-file tol.yml]
           Metrics from dispatch_gen_annual_summary.csv and BuildGen.csv: CO2 (Mt), coal capacity (GW),
           coal generation (TWh), coal CF, and new CC, CT, onshore wind, offshore wind, solar and
           storage (GW built in the period). Prints a table and PASS/FAIL against the tolerances
           (defaults below); exit code 1 on any FAIL.

Tolerances (defaults; a metric passes if within EITHER the absolute or the relative bound):
  co2_mt: 1% or 5 Mt;  coal_gw: 2% or 1 GW;  coal_twh: 2% or 5 TWh;
  new builds: 5% or 1 GW per technology.
usage (repo root): python s0_workflow/scripts/compare_s0_runs.py outputs <new out> <old out> --period 2035
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

TOL = {"co2_mt": (0.01, 5.0), "coal_gw": (0.02, 1.0), "coal_twh": (0.02, 5.0), "coal_cf": (0.02, 0.01),
       "new_cc_gw": (0.05, 1.0), "new_ct_gw": (0.05, 1.0), "new_wind_onshore_gw": (0.05, 1.0),
       "new_wind_offshore_gw": (0.05, 1.0), "new_solar_gw": (0.05, 1.0), "new_storage_gw": (0.05, 1.0)}


def tech_group(t: str) -> str | None:
    t = str(t)
    if t.startswith("NaturalGas") and "Combined Cycle" in t:
        return "cc"
    if t.startswith("NaturalGas") and "Combustion Turbine" in t:
        return "ct"
    if t.startswith("LandbasedWind"):
        return "wind_onshore"
    if t.startswith("OffShoreWind"):
        return "wind_offshore"
    if t.startswith("UtilityPV"):
        return "solar"
    if "Battery" in t:
        return "storage"
    return None


def metrics(out: Path, period: int) -> dict:
    d = pd.read_csv(out / "dispatch_gen_annual_summary.csv")
    d = d[d["period"] == period]
    coal = d[d["gen_energy_source"].str.lower() == "coal"]
    m = {"co2_mt": d["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6,
         "coal_gw": coal["GenCapacity_MW"].sum() / 1e3,
         "coal_twh": coal["Energy_GWh_typical_yr"].sum() / 1e3}
    m["coal_cf"] = m["coal_twh"] * 1e3 / (m["coal_gw"] * 8760) if m["coal_gw"] else np.nan
    tech = d.drop_duplicates("generation_project").set_index("generation_project")["gen_tech"]
    b = pd.read_csv(out / "BuildGen.csv")
    b = b[b["GEN_BLD_YRS_2"] == period]
    b = b.assign(g=b["GEN_BLD_YRS_1"].map(tech).map(tech_group))
    for k in ("cc", "ct", "wind_onshore", "wind_offshore", "solar", "storage"):
        m[f"new_{k}_gw"] = b.loc[b["g"] == k, "BuildGen"].sum() / 1e3
    return m


def compare_outputs(a):
    new, old = metrics(Path(a.new), a.period), metrics(Path(a.old), a.period)
    tol = dict(TOL)
    if a.tolerance_file:
        import yaml
        tol.update({k: tuple(v) for k, v in yaml.safe_load(open(a.tolerance_file)).items()})
    rows, ok = [], True
    for k in new:
        rel, ab = tol.get(k, (0.05, 1.0))
        diff = new[k] - old[k]
        passed = bool(abs(diff) <= ab or (old[k] and abs(diff) <= rel * abs(old[k])))
        ok &= passed
        rows.append({"metric": k, "new": round(new[k], 4), "old": round(old[k], 4), "diff": round(diff, 4),
                     "tolerance": f"{rel:.0%} or {ab:g}", "result": "PASS" if passed else "FAIL"})
    print(pd.DataFrame(rows).to_string(index=False))
    print("OVERALL", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def compare_inputs(a):
    new, old = Path(a.new), Path(a.old)
    alias = dict(x.split("=", 1) for x in (a.alias or []))
    rows = []
    for f in sorted(p.name for p in new.glob("*.csv")):
        o = old / alias.get(f, f)
        if not o.exists():
            rows.append((f, "new only", ""))
            continue
        x, y = pd.read_csv(new / f, dtype=str, keep_default_na=False), pd.read_csv(o, dtype=str, keep_default_na=False)
        if list(x.columns) != list(y.columns):
            rows.append((f, "columns differ", f"new {sorted(set(x.columns) - set(y.columns))} / old "
                                              f"{sorted(set(y.columns) - set(x.columns))}"))
            continue
        if x.equals(y):
            rows.append((f, "identical", ""))
            continue
        if len(x) == len(y) and x.sort_values(list(x.columns)).reset_index(drop=True).equals(
                y.sort_values(list(y.columns)).reset_index(drop=True)):
            rows.append((f, "same rows, other order", ""))
            continue
        num = [c for c in x.columns if pd.to_numeric(x[c], errors="coerce").notna().all()
               and pd.to_numeric(y[c], errors="coerce").notna().all()]
        key = [c for c in x.columns if c not in num]
        if key and len(x) == len(y):
            xm = x.set_index(key)[num].apply(pd.to_numeric)
            ym = y.set_index(key)[num].apply(pd.to_numeric).reindex(xm.index)
            if ym.notna().all().all():
                dabs = (xm - ym).abs()
                drel = dabs / ym.abs().where(ym.abs() > 0)
                nrows = int((dabs > 1e-9).any(axis=1).sum())
                rows.append((f, "numeric differences", f"rows {nrows}; max abs {dabs.max().max():.6g}; "
                                                      f"max rel {drel.max().max():.3g}; columns "
                                                      f"{list(dabs.columns[(dabs > 1e-9).any()])}"))
                continue
        rows.append((f, "different rows", f"new {len(x)} rows, old {len(y)} rows; "
                                          f"{len(set(map(tuple, x.values)) ^ set(map(tuple, y.values)))} rows differ"))
    for f in sorted(p.name for p in old.glob("*.csv")):
        if f not in {alias.get(n, n) for n in (p.name for p in new.glob("*.csv"))} and f not in alias.values() \
                and not (new / f).exists():
            rows.append((f, "old only", ""))
    print(pd.DataFrame(rows, columns=["file", "result", "detail"]).to_string(index=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("inputs")
    i.add_argument("new"); i.add_argument("old"); i.add_argument("--alias", action="append")
    o = sub.add_parser("outputs")
    o.add_argument("new"); o.add_argument("old"); o.add_argument("--period", type=int, default=2035)
    o.add_argument("--tolerance-file")
    a = ap.parse_args()
    sys.exit(compare_inputs(a) if a.cmd == "inputs" else compare_outputs(a))


if __name__ == "__main__":
    main()
