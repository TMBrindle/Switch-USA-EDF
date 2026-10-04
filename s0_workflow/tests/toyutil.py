"""Switch 3-zone toy runs for the s0_workflow tests (small hand-built inputs: fixtures, not results).

toy_run(tmp_path, name, modules, edit) copies Switch's examples/3zone_toy, makes it an LP (no unit
sizes or minimum builds, so duals exist), copies the named repo study modules into a local package,
lets `edit(inputs_dir)` change the inputs, solves with HiGHS and returns the run folder.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]


def switch_src() -> Path:
    for p in (os.environ.get("SWITCH_SRC"), "/opt/switch-src"):
        if p and Path(p).exists():
            return Path(p)
    try:
        import switch_model
        return Path(switch_model.__file__).resolve().parents[1]
    except ImportError:
        return Path("/opt/switch-src")


def toy_inputs(tmp_path: Path, name: str, modules=(), edit=None, periods=None) -> Path:
    """Prepare (but don't solve) a toy run folder."""
    src = switch_src() / "examples" / "3zone_toy"
    if shutil.which("switch") is None or not src.exists():
        pytest.skip("switch / 3zone_toy example not available (set SWITCH_SRC)")
    run = Path(tmp_path) / name
    shutil.copytree(src, run)
    (run / "mods").mkdir()
    (run / "mods/__init__.py").touch()
    inp = run / "inputs"
    gi = pd.read_csv(inp / "gen_info.csv", na_values=".")
    gi["gen_unit_size"] = np.nan
    gi["gen_min_build_capacity"] = 0
    gi.to_csv(inp / "gen_info.csv", index=False, na_rep=".")
    lines = []
    for mod in modules:
        f = REPO / "switch" / "study_modules" / f"{mod}.py"
        shutil.copy(f, run / "mods")
        lines.append(f"mods.{mod}")
    if lines:
        with open(inp / "modules.txt", "a") as fh:
            fh.write("\n" + "\n".join(lines) + "\n")
    if periods is not None:
        pd.DataFrame(periods, columns=["INVESTMENT_PERIOD", "period_start", "period_end"]).to_csv(
            inp / "periods.csv", index=False)
    if edit is not None:
        edit(inp)
    return run


def solve(run: Path, inputs="inputs", outputs="outputs", extra=()):
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--suffixes", "dual",
                        "--inputs-dir", inputs, "--outputs-dir", outputs, *extra], cwd=run,
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(run)})
    assert r.returncode == 0, r.stderr[-3000:] + r.stdout[-2000:]
    return run / outputs


def toy_run(tmp_path: Path, name: str, modules=(), edit=None, periods=None, extra=()) -> Path:
    run = toy_inputs(tmp_path, name, modules, edit, periods)
    solve(run, extra=extra)
    return run


def total_cost(out: Path) -> float:
    return float(open(Path(out) / "total_cost.txt").read().strip())


def build(out: Path) -> pd.DataFrame:
    return pd.read_csv(Path(out) / "BuildGen.csv").rename(
        columns={"GEN_BLD_YRS_1": "gen", "GEN_BLD_YRS_2": "build_year", "BuildGen": "mw"})


# ---------------------------------------------------------------------------------------------------
# multi-period toys and stage chains (bounded foresight tests)
# ---------------------------------------------------------------------------------------------------
def add_period(inp: Path, new=(2040, 2037, 2046), src_period=2030, load_scale=1.2):
    """Add a period to a toy inputs folder by copying src_period's data (loads x load_scale)."""
    inp = Path(inp)
    p, start, end = new
    per = pd.read_csv(inp / "periods.csv")
    pd.concat([per, pd.DataFrame([{"INVESTMENT_PERIOD": p, "period_start": start, "period_end": end}])]).to_csv(
        inp / "periods.csv", index=False)
    ts = pd.read_csv(inp / "timeseries.csv")
    src = ts[ts["ts_period"] == src_period].copy()
    rename = {t: f"{p}_{t.split('_', 1)[1]}" for t in src["TIMESERIES"]}
    src["TIMESERIES"] = src["TIMESERIES"].map(rename)
    src["ts_period"] = p
    pd.concat([ts, src]).to_csv(inp / "timeseries.csv", index=False)
    tp = pd.read_csv(inp / "timepoints.csv")
    stp = tp[tp["timeseries"].isin(rename)].copy()
    tmap = dict(zip(stp["timepoint_id"], range(tp["timepoint_id"].max() + 1, tp["timepoint_id"].max() + 1 + len(stp))))
    stp["timepoint_id"] = stp["timepoint_id"].map(tmap)
    stp["timeseries"] = stp["timeseries"].map(rename)
    stp["timestamp"] = stp["timestamp"] + (p - src_period) * 1000000
    pd.concat([tp, stp]).to_csv(inp / "timepoints.csv", index=False)
    lo = pd.read_csv(inp / "loads.csv")
    sl = lo[lo["TIMEPOINT"].isin(tmap)].copy()
    sl["TIMEPOINT"] = sl["TIMEPOINT"].map(tmap)
    sl["zone_demand_mw"] *= load_scale
    pd.concat([lo, sl]).to_csv(inp / "loads.csv", index=False)
    vcf = pd.read_csv(inp / "variable_capacity_factors.csv")
    sv = vcf[vcf["timepoint"].isin(tmap)].copy()
    sv["timepoint"] = sv["timepoint"].map(tmap)
    pd.concat([vcf, sv]).to_csv(inp / "variable_capacity_factors.csv", index=False)
    for f, col in (("fuel_cost.csv", "period"), ("fuel_supply_curves.csv", "period"),
                   ("zone_fuel_cost_diff.csv", "period"), ("zone_coincident_peak_demand.csv", "PERIOD")):
        if (inp / f).exists():
            d = pd.read_csv(inp / f, na_values=["."])
            s = d[d[col] == src_period].copy()
            s[col] = p
            pd.concat([d, s]).to_csv(inp / f, index=False, na_rep=".")
    bc = pd.read_csv(inp / "gen_build_costs.csv")
    s = bc[bc["build_year"] == src_period].copy()
    s["build_year"] = p
    pd.concat([bc, s]).to_csv(inp / "gen_build_costs.csv", index=False)


def subset_periods(src: Path, dst: Path, periods):
    """Copy a toy inputs folder keeping only the given investment periods."""
    src, dst = Path(src), Path(dst)
    shutil.copytree(src, dst)
    periods = set(int(p) for p in periods)
    per = pd.read_csv(src / "periods.csv")
    per[per["INVESTMENT_PERIOD"].isin(periods)].to_csv(dst / "periods.csv", index=False)
    ts = pd.read_csv(src / "timeseries.csv")
    ts = ts[ts["ts_period"].isin(periods)]
    ts.to_csv(dst / "timeseries.csv", index=False)
    tp = pd.read_csv(src / "timepoints.csv")
    tp = tp[tp["timeseries"].isin(ts["TIMESERIES"])]
    tp.to_csv(dst / "timepoints.csv", index=False)
    lo = pd.read_csv(src / "loads.csv")
    lo[lo["TIMEPOINT"].isin(tp["timepoint_id"])].to_csv(dst / "loads.csv", index=False)
    vcf = pd.read_csv(src / "variable_capacity_factors.csv")
    vcf[vcf["timepoint"].isin(tp["timepoint_id"])].to_csv(dst / "variable_capacity_factors.csv", index=False)
    for f, col in (("fuel_cost.csv", "period"), ("fuel_supply_curves.csv", "period"),
                   ("zone_fuel_cost_diff.csv", "period"), ("zone_coincident_peak_demand.csv", "PERIOD")):
        if (src / f).exists():
            d = pd.read_csv(src / f, na_values=["."])
            d[d[col].isin(periods)].to_csv(dst / f, index=False, na_rep=".")
    pre = pd.read_csv(src / "gen_build_predetermined.csv")
    keys = set(zip(pre["GENERATION_PROJECT"], pre["build_year"]))
    bc = pd.read_csv(src / "gen_build_costs.csv")
    bc[bc["build_year"].isin(periods) | pd.Series([k in keys for k in zip(bc["GENERATION_PROJECT"], bc["build_year"])],
                                                  index=bc.index)].to_csv(dst / "gen_build_costs.csv", index=False)


def run_chain(run: Path, stages: list, case="case", modules=(), extra=()) -> list[Path]:
    """Solve the stages of a chain in order (stages from s0_workflow.production.plan_stages; inputs in
    run/in/<stage>/<case> with stage_info.csv). Returns the outputs folders. Later stages read the chained
    files as pg_to_switch's scenario lines do (forced-line files only when the stage has trans_build_minimum.csv)."""
    outs = []
    for i, st in enumerate(stages):
        args = list(extra)
        for mod in modules:
            args += ["--include-module", f"mods.{mod}"]
        if st["next"]:
            args += ["--include-module", "mods.prepare_next_stage"]
        if i > 0:
            files = ["gen_build_predetermined", "gen_build_costs", "transmission_lines"]
            d = run / "in" / st["name"] / case
            if (d / "trans_build_minimum.csv").exists():
                files += [f for f in ("trans_build_minimum", "trans_path_expansion_limit") if (d / f"{f}.csv").exists()]
            args += ["--input-aliases"] + [f"{f}.csv={f}.chained.{case}.csv" for f in files]
        outs.append(solve(run, f"in/{st['name']}/{case}", f"out/{st['name']}/{case}", args))
    return outs
