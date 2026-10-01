"""Patch an existing Switch case input folder for interconnection headroom, without regenerating it.

Writes, next to the originals (which are never overwritten):

    gen_info.<tag>.csv               gen_connect_cost_per_mw with network reinforcement removed by
                                     icsc.switch_case.strip_network_reinforcement (new wind/solar
                                     clusters with ReEDS-CPA bundled costs: reinforcement_share.csv)
    ic_weights.<tag>.csv             icsc.switch_case.weights_by_tech (distributed generation 0)
    ic_connect_cost_check.<tag>.csv  diagnostic: share used, before/after, removed
    ic_params.<tag>.csv              (with --slack-cost) ic_params.csv plus ic_slack_cost_per_mw,
                                     the headroom limit's diagnostic slack cost ($/MW)
    patch_log.<tag>.txt              commit, date, what changed (later --slack-cost runs append)

Use them at solve time with
    --input-alias gen_info.csv=gen_info.<tag>.csv --input-alias ic_weights.csv=ic_weights.<tag>.csv

The source cost columns (tx_capex, interconnect_capex_mw, ...) come from the folder's original
ic_connect_cost_check.csv, written by pg_to_switch row-aligned with gen_info.csv. The folder must
be an interconnection-headroom "on" case whose gen_info.csv has not had reinforcement removed yet
(its gen_connect_cost_per_mw equals the check file's "after" column, and nothing was removed).

    python scripts/patch_case_inputs.py <case_input_dir> [<case_input_dir> ...] [--tag ic_v2]
    python scripts/patch_case_inputs.py <case_input_dir> ... --slack-cost 5e7 --params-only
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from icsc import switch_case as sc  # noqa: E402

SOURCE_COLS = ["spur_capex", "offshore_spur_capex", "tx_capex", "interconnect_capex_mw",
               "interconnect_annuity"]


def _same(a, b) -> pd.Series:
    a, b = pd.Series(a).reset_index(drop=True), pd.Series(b).reset_index(drop=True)
    return (a == b) | (a.isna() & b.isna())


def git_commit() -> str:
    try:
        root = Path(__file__).resolve().parents[2]
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--",
                                "interconnection_headroom"], capture_output=True, text=True).stdout.strip()
        return sha + (" (interconnection_headroom/ has uncommitted changes)" if dirty else "")
    except Exception as e:  # noqa: BLE001
        return f"unknown ({e})"


def patch(case: Path, tag: str = "ic_v2") -> dict:
    case = Path(case)
    out = {n: case / f"{n}.{tag}.csv" for n in ("gen_info", "ic_weights", "ic_connect_cost_check")}
    log_fn = case / f"patch_log.{tag}.txt"
    existing = [p for p in [*out.values(), log_fn] if p.exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite: {', '.join(map(str, existing))}")

    gen_info = pd.read_csv(case / "gen_info.csv", na_values=["."], keep_default_na=False)
    raw_gen_info = pd.read_csv(case / "gen_info.csv", dtype=str, keep_default_na=False)
    check = pd.read_csv(case / "ic_connect_cost_check.csv")
    old_weights = pd.read_csv(case / "ic_weights.csv")
    if not (gen_info["GENERATION_PROJECT"].values == check["GENERATION_PROJECT"].values).all():
        raise ValueError("gen_info.csv and ic_connect_cost_check.csv are not row-aligned")
    if not _same(gen_info["gen_connect_cost_per_mw"], check["gen_connect_cost_per_mw_after"]).all():
        raise ValueError("gen_info.csv gen_connect_cost_per_mw differs from the check file's 'after' column")
    if not _same(check["gen_connect_cost_per_mw_before"], check["gen_connect_cost_per_mw_after"]).all():
        raise ValueError("reinforcement was already removed when this case was generated; nothing to patch")

    # costs: the original diag's source columns are the PowerGenome frame strip_network_reinforcement expects
    src = check[[c for c in SOURCE_COLS if c in check]]
    patched = gen_info.copy()
    diag = sc.strip_network_reinforcement(patched, src)

    # gen_info.<tag>.csv: change only gen_connect_cost_per_mw, keep every other cell as written
    new_raw = raw_gen_info.copy()
    changed = ~_same(patched["gen_connect_cost_per_mw"], gen_info["gen_connect_cost_per_mw"]).values
    new_raw.loc[changed, "gen_connect_cost_per_mw"] = patched.loc[changed, "gen_connect_cost_per_mw"].map(repr)
    new_raw.to_csv(out["gen_info"], index=False)

    # weights: same weights write_case_inputs uses (pipeline config tech_weights)
    weights = sc.pipeline_config()["saturation"]["tech_weights"]
    w = sc.weights_by_tech(gen_info, weights)[["ic_key", "ic_weight"]]
    w.to_csv(out["ic_weights"], index=False)
    diag.to_csv(out["ic_connect_cost_check"], index=False)

    wc = old_weights.merge(w, on="ic_key", how="outer", suffixes=("_old", "_new"))
    wchg = wc[wc["ic_weight_old"] != wc["ic_weight_new"]]
    rem = diag[diag["removed_per_mw"] > 0]
    by_tech = (rem.groupby("gen_tech")["removed_per_mw"].agg(["size", "mean", "median"])
               .assign(mean=lambda x: x["mean"] / 1000, median=lambda x: x["median"] / 1000))
    lines = [
        f"patch_case_inputs.py tag={tag}",
        f"date: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"commit: {git_commit()}",
        f"case: {case}",
        f"reinforcement_share: {sc.REINFORCEMENT_SHARE}",
        f"removal method: {diag['removal_method'].iloc[0]}",
        f"gen_info.{tag}.csv: gen_connect_cost_per_mw changed on {int(changed.sum())} rows "
        f"(of {len(gen_info)}); all other cells unchanged",
        "removed $/kW by gen_tech (n, mean, median):",
        *[f"  {t}: {int(r['size'])}, {r['mean']:.1f}, {r['median']:.1f}" for t, r in by_tech.iterrows()],
        f"ic_weights.{tag}.csv: {len(wchg)} weight(s) changed:",
        *[f"  {r.ic_key}: {r.ic_weight_old} -> {r.ic_weight_new}" for r in wchg.itertuples()],
        f"solve with: --input-alias gen_info.csv=gen_info.{tag}.csv "
        f"--input-alias ic_weights.csv=ic_weights.{tag}.csv",
    ]
    log_fn.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return {"rows_changed": int(changed.sum()), "weights_changed": wchg}


def write_params(case: Path, tag: str, slack_cost: float) -> None:
    """ic_params.<tag>.csv = ic_params.csv plus ic_slack_cost_per_mw (never overwrites)."""
    case = Path(case)
    fn = case / f"ic_params.{tag}.csv"
    if fn.exists():
        raise FileExistsError(f"refusing to overwrite: {fn}")
    params = pd.read_csv(case / "ic_params.csv")
    if "ic_slack_cost_per_mw" in params:
        raise ValueError(f"{case / 'ic_params.csv'} already has ic_slack_cost_per_mw")
    params["ic_slack_cost_per_mw"] = slack_cost
    params.to_csv(fn, index=False)
    lines = [
        f"patch_case_inputs.py tag={tag} --slack-cost {slack_cost:g}",
        f"date: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"commit: {git_commit()}",
        f"ic_params.{tag}.csv: ic_params.csv plus ic_slack_cost_per_mw = {slack_cost:g} $/MW "
        f"({slack_cost / 1000:,.0f} $/kW); all other values unchanged",
        f"solve with: --input-alias ic_params.csv=ic_params.{tag}.csv",
    ]
    with open(case / f"patch_log.{tag}.txt", "a") as f:
        f.write("\n" + "\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--tag", default="ic_v2")
    ap.add_argument("--slack-cost", type=float, default=None,
                    help="also write ic_params.<tag>.csv with this ic_slack_cost_per_mw ($/MW)")
    ap.add_argument("--params-only", action="store_true",
                    help="only write ic_params.<tag>.csv (case already patched)")
    a = ap.parse_args()
    if a.params_only and a.slack_cost is None:
        ap.error("--params-only needs --slack-cost")
    for c in a.cases:
        if not a.params_only:
            patch(Path(c), a.tag)
        if a.slack_cost is not None:
            write_params(Path(c), a.tag, a.slack_cost)
        print()
