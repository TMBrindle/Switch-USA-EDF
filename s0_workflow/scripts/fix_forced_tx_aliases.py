"""Corrected forced-transmission inputs for an S0 chain that was built before the §57 fix, as input aliases.

Builds before §57 (e.g. 1eab1ac) force every forced line in every stage at or after its in-service year: each stage's
trans_build_minimum.csv re-forces it, and with forced_tx_expansion_limit: minimum its trans_path_expansion_limit row in
that stage is the minimum instead of the policy's normal limit. Together with prepare_next_stage carrying earlier
builds forward, SunZia would be built 5 times and TransWest 3 times in S0prod_A.

For every stage of a built chain this script writes what the fixed case build (pg_to_switch, §57) writes, without
rebuilding:
  <stage>/<case>/trans_build_minimum.fixed.csv         each forced line only in the period whose span holds its
                                                       in-service year (s0_workflow.production.forced_tx_period over
                                                       the chain's years), and only in stages that model that period
  <stage>/<case>/trans_path_expansion_limit.fixed.csv  the built file with every wrongly forced row back at the
                                                       policy's normal limit (zero: 0; nerc_growth: recomputed;
                                                       unlimited: row removed); the forced period stays capped at
                                                       the minimum
When the fixed build would write no file (no line forced in the stage; or unlimited with nothing forced), the alias is
`<file>.csv=none` and no .fixed file is written. It also writes, next to the scenarios file:
  scenarios_<case>.fixed.txt           the case's scenario lines with the aliases added (replacing any alias of the
                                       two files)
  forced_tx_aliases.<case>.csv         stage, inputs dir and the alias pairs, for a chain runner (run_chain_A.py) that
                                       builds its own --input-aliases list
  forced_tx_fix_report.<case>.csv      every forced row: stage, line, period, MW as built and as fixed, and the
                                       limit as built and as fixed
Nothing existing is changed or removed; existing .fixed files stop the script (pass --overwrite to replace them).

The forced lines come from the case's option: reeds_certain (default; the pinned ReEDS table) or named_projects
(transmission_connections.csv), or --forced-table. Before writing, every fixed minimum row must be one of the built
rows (same line, period and MW) and, for the zero policy, every unforced built limit must be 0; otherwise it stops.

usage (repo root):
  python s0_workflow/scripts/fix_forced_tx_aliases.py <case root> --case S0prod_A
      [--forced-tx reeds_certain|named_projects] [--release 2026.09.21] [--forced-table CSV]
      [--trans-expansion zero|nerc_growth|unlimited] [--scenarios FILE] [--overwrite]
  <case root> holds the stage folders (2028/S0prod_A/, 2030/S0prod_A/, ...), as pg_to_switch writes them
  (e.g. switch/in/s0prod_A). Default --scenarios: <case root>/scenarios_<case>.txt.
"""
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import production as s0prod  # noqa: E402

FILES = ("trans_build_minimum", "trans_path_expansion_limit")
SUFFIX = "fixed"


def stage_folders(root: Path, case: str) -> list[dict]:
    """The chain's stages in order: [{"name", "dir", "years", "next"}], from stage_info.csv and periods.csv."""
    st = {}
    for d in sorted(Path(root).iterdir()):
        f = d / case / "stage_info.csv"
        if f.exists():
            info = pd.read_csv(f).iloc[0]
            years = sorted(int(y) for y in pd.read_csv(d / case / "periods.csv").INVESTMENT_PERIOD)
            nxt = str(info.next_stage)
            st[d.name] = {"name": d.name, "dir": d / case, "years": years,
                          "next": None if nxt in (".", "", "nan") else nxt}
    if not st:
        raise SystemExit(f"no stage folders with {case}/stage_info.csv under {root}")
    firsts = set(st) - {s["next"] for s in st.values() if s["next"]}
    if len(firsts) != 1:
        raise SystemExit(f"cannot order the stages under {root}: first-stage candidates {sorted(firsts)}")
    out, name = [], firsts.pop()
    while name:
        if name not in st:
            raise SystemExit(f"stage {name} (next of {out[-1]['name']}) not found under {root}")
        out.append(st[name])
        name = st[name]["next"]
    return out


def forced_lines(table: Path) -> dict:
    """frozenset({zone, zone}) -> (MW, in-service year), as pg_to_switch.transmission_tables reads the table."""
    forced = pd.read_csv(table)
    out = {}
    for _, row in forced.iterrows():
        mw = row.get("new_cap_mw")
        if pd.notna(mw) and float(mw) > 0:
            yr = row.get("new_cap_year")
            if pd.notna(yr):
                out[frozenset([row["from_zone"], row["to_zone"]])] = (float(mw), int(yr))
    return out


def fixed_minimum(stage: dict, chain_years, planned: dict) -> list[dict]:
    """The trans_build_minimum rows the fixed build writes for this stage."""
    tl = pd.read_csv(stage["dir"] / "transmission_lines.csv")
    lookup = {frozenset([r["trans_lz1"], r["trans_lz2"]]): r["TRANSMISSION_LINE"] for _, r in tl.iterrows()}
    rows = []
    for key, (mw, yr) in planned.items():
        tx = lookup.get(key)
        if tx is None:
            continue
        per = s0prod.forced_tx_period(yr, chain_years, stage["years"])
        if per is not None:
            rows.append({"TRANSMISSION_LINE": tx, "PERIOD": per, "trans_build_minimum_mw": mw})
    return rows


def nerc_growth_limits(stage: dict) -> dict:
    """(line, period) -> the nerc_growth limit, as pg_to_switch.transmission_tables computes it (all lines that may be
    built: forced lines are not excluded when forced lines are capped at their minimum)."""
    tl = pd.read_csv(stage["dir"] / "transmission_lines.csv")
    g = pd.read_csv(REPO / "pg/extra_inputs/transmission/nerc_growth_pct.csv")
    lookup = {(int(r["period"]), r["nercr"]): float(r["growth_pct"]) for _, r in g.iterrows()}
    by_region = {}
    for (p, n), pct in lookup.items():
        by_region.setdefault(n, []).append((p, pct))
    for n in by_region:
        by_region[n].sort()

    def growth(y, n):
        if (y, n) in lookup:
            return lookup[(y, n)]
        c = [pct for p, pct in by_region.get(n, []) if p <= y]
        return c[-1] if c else 0.0
    zone_nercr = dict(pd.read_csv(REPO / "hierarchy.csv")[["ba", "nercr"]].values)
    work = tl.loc[tl["trans_new_build_allowed"] == 1].reset_index(drop=True)
    n1, n2 = work["trans_lz1"].map(zone_nercr), work["trans_lz2"].map(zone_nercr)
    cum = work["existing_trans_cap"].copy()
    out = {}
    for y in stage["years"]:
        pg = {n: growth(y, n) for n in set(n1) | set(n2)}
        lim = (cum * (n1.map(pg).fillna(0.0) + n2.map(pg).fillna(0.0)) / 2).round(0)
        out.update({(t, y): v for t, v in zip(work["TRANSMISSION_LINE"], lim)})
        cum = cum + lim
    return out


def fix_stage(stage: dict, chain_years, planned: dict, policy: str):
    """(minimum DataFrame or None, limit DataFrame or None, report rows) for one stage."""
    d = stage["dir"]
    built_min = (pd.read_csv(d / "trans_build_minimum.csv") if (d / "trans_build_minimum.csv").exists()
                 else pd.DataFrame(columns=["TRANSMISSION_LINE", "PERIOD", "trans_build_minimum_mw"]))
    built = {(str(r.TRANSMISSION_LINE), int(r.PERIOD)): float(r.trans_build_minimum_mw) for r in built_min.itertuples()}
    rows = fixed_minimum(stage, chain_years, planned)
    good = {(str(r["TRANSMISSION_LINE"]), int(r["PERIOD"])): r["trans_build_minimum_mw"] for r in rows}
    for k, mw in good.items():
        if k not in built or abs(built[k] - mw) > 1e-6:
            raise SystemExit(f"{d}: the fixed minimum {k} {mw} MW is not in the built trans_build_minimum.csv "
                             f"(built: {built.get(k)}): wrong --forced-tx / --forced-table, or not a pre-§57 build")
    wrong = {k for k in built if k not in good}
    mins = built_min[[(str(a), int(b)) in good for a, b in zip(built_min.TRANSMISSION_LINE, built_min.PERIOD)]] \
        if rows else None

    lim = None
    if (d / "trans_path_expansion_limit.csv").exists():
        lim = pd.read_csv(d / "trans_path_expansion_limit.csv")
        keys = [(str(a), int(b)) for a, b in zip(lim.TRANSMISSION_LINE, lim.PERIOD)]
        tl = pd.read_csv(d / "transmission_lines.csv")
        flag = tl["trans_new_build_allowed"] if "trans_new_build_allowed" in tl else pd.Series(1, index=tl.index)
        allowed = {str(t) for t, a in zip(tl.TRANSMISSION_LINE, flag) if a == 1}
        if policy == "zero":
            unforced = [v for k, v in zip(keys, lim.trans_path_expansion_limit_mw) if k not in built]
            if any(abs(float(v)) > 1e-9 for v in unforced):
                raise SystemExit(f"{d}: --trans-expansion zero, but unforced limits are not all 0")
        normal = nerc_growth_limits(stage) if policy == "nerc_growth" else {}
        drop, vals = [], list(lim.trans_path_expansion_limit_mw)
        for i, k in enumerate(keys):
            if k in wrong:
                if policy == "unlimited" or k[0] not in allowed:
                    drop.append(i)                       # the fixed build has no such row
                elif policy == "zero":
                    vals[i] = 0.0
                else:
                    vals[i] = normal[k]
        lim = lim.assign(trans_path_expansion_limit_mw=vals).drop(index=lim.index[drop])
        if policy == "unlimited" and not rows:
            lim = None                                   # the fixed build writes no file
    report = []
    for k, mw in built.items():
        before = after = None
        if (d / "trans_path_expansion_limit.csv").exists():
            b = pd.read_csv(d / "trans_path_expansion_limit.csv")
            m = (b.TRANSMISSION_LINE.astype(str) == k[0]) & (b.PERIOD == k[1])
            before = float(b.loc[m, "trans_path_expansion_limit_mw"].iloc[0]) if m.any() else None
            if lim is not None:
                m = (lim.TRANSMISSION_LINE.astype(str) == k[0]) & (lim.PERIOD == k[1])
                after = float(lim.loc[m, "trans_path_expansion_limit_mw"].iloc[0]) if m.any() else None
        report.append({"stage": stage["name"], "TRANSMISSION_LINE": k[0], "PERIOD": k[1], "minimum_built_mw": mw,
                       "minimum_fixed_mw": good.get(k, 0.0), "limit_built_mw": before, "limit_fixed_mw": after,
                       "action": "kept" if k in good else "removed (re-forced)"})
    return mins, lim, report


def add_aliases(line: str, pairs: list[str]) -> str:
    """A scenario line with these alias pairs added (replacing any alias of the same files)."""
    args = shlex.split(line)
    names = {p.split("=")[0] for p in pairs}
    out, i, done = [], 0, False
    while i < len(args):
        a = args[i]
        if a in ("--input-aliases", "--input-alias"):
            j = i + 1
            vals = []
            while j < len(args) and not args[j].startswith("--"):
                vals.append(args[j])
                j += 1
            vals = [v for v in vals if v.split("=")[0] not in names]
            if not done:
                vals += pairs
                done = True
            if vals:
                out += [a] + vals
            i = j
            continue
        out.append(a)
        i += 1
    if not done:
        out += ["--input-aliases"] + pairs
    return " ".join(shlex.quote(x) for x in out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("--case", required=True)
    ap.add_argument("--forced-tx", choices=["reeds_certain", "named_projects"], default="reeds_certain")
    ap.add_argument("--release", default="2026.09.21")
    ap.add_argument("--forced-table", type=Path)
    ap.add_argument("--trans-expansion", choices=["zero", "nerc_growth", "unlimited"], default="zero")
    ap.add_argument("--scenarios", type=Path)
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)
    table = a.forced_table or (REPO / (s0prod.forced_tx_table(a.release) if a.forced_tx == "reeds_certain"
                                       else "pg/extra_inputs/transmission/transmission_connections.csv"))
    planned = forced_lines(table)
    stages = stage_folders(a.root, a.case)
    chain = sorted({y for s in stages for y in s["years"]})
    results, report = [], []
    for st in stages:
        mins, lim, rep = fix_stage(st, chain, planned, a.trans_expansion)
        report += rep
        pairs = []
        for f, df in zip(FILES, (mins, lim)):
            target = st["dir"] / f"{f}.{SUFFIX}.csv"
            if target.exists() and not a.overwrite:
                raise SystemExit(f"{target} exists (pass --overwrite to replace it)")
            if df is None:
                pairs.append(f"{f}.csv=none")
            else:
                pairs.append(f"{f}.csv={f}.{SUFFIX}.csv")
        results.append((st, mins, lim, pairs))
    for st, mins, lim, pairs in results:                      # write only once every stage passed its checks
        for f, df in zip(FILES, (mins, lim)):
            if df is not None:
                df.to_csv(st["dir"] / f"{f}.{SUFFIX}.csv", index=False)
    scen = a.scenarios or (a.root / f"scenarios_{a.case}.txt")
    out_dir = scen.parent if scen.exists() else a.root
    rows = [{"stage": st["name"], "inputs_dir": str(st["dir"]), "aliases": " ".join(p)} for st, _, _, p in results]
    for name, df in ((f"forced_tx_aliases.{a.case}.csv", pd.DataFrame(rows)),
                     (f"forced_tx_fix_report.{a.case}.csv", pd.DataFrame(report))):
        if (out_dir / name).exists() and not a.overwrite:
            raise SystemExit(f"{out_dir / name} exists (pass --overwrite to replace it)")
        df.to_csv(out_dir / name, index=False)
    if scen.exists():
        by_dir = {st["dir"].resolve(): p for st, _, _, p in results}
        lines, matched = [], 0
        for ln in scen.read_text().splitlines():
            args = shlex.split(ln)
            p = None
            if "--inputs-dir" in args:
                d = Path(args[args.index("--inputs-dir") + 1])
                for base in (scen.parent, scen.parent.parent, Path.cwd(), REPO / "switch"):
                    if (base / d).resolve() in by_dir:
                        p = by_dir[(base / d).resolve()]
                        break
            if p:
                matched += 1
                ln = add_aliases(ln, p)
            lines.append(ln)
        if matched != len(results):
            raise SystemExit(f"{scen}: matched {matched} lines to the {len(results)} stages; aliases written to "
                             f"{out_dir / f'forced_tx_aliases.{a.case}.csv'}, scenarios file not written")
        fixed = scen.with_name(f"{scen.stem}.{SUFFIX}.txt")
        if fixed.exists() and not a.overwrite:
            raise SystemExit(f"{fixed} exists (pass --overwrite to replace it)")
        fixed.write_text("\n".join(lines) + "\n")
        print(f"wrote {fixed}")
    removed = sum(r["action"] != "kept" for r in report)
    print(f"{a.case}: {len(stages)} stages, chain {chain}; {removed} re-forced minimum rows removed; "
          f"aliases in {out_dir / f'forced_tx_aliases.{a.case}.csv'}")
    for st, _, _, p in results:
        print(f"  {st['name']}: --input-aliases {' '.join(p)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
