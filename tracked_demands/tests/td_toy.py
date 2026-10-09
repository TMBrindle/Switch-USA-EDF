"""Toy tracked-demand cases on Switch's 3-zone example (hand-built fixtures, not results).

td_case(tmp_path, name, ...) prepares a toy run folder with s0_workflow/tests/toyutil.toy_inputs (LP version of
examples/3zone_toy, repo study modules copied into a local `mods` package) and writes the tracked-demand inputs:
one 24/7 data centre "DC1" in North (2 MW flat, 2 MW grid connection), optional CFE targets matched hourly (one
time block per timepoint) or annually (one block per period), an on-site menu (solar, gas reciprocating engine) and a
behind-the-meter battery. Optional zero-weight stress timeseries (as in S0's stress days).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "s0_workflow" / "tests"))
import toyutil  # noqa: E402

DC_MW = 2.0


def add_stress_timeseries(inp: Path, period=2020, n=2, load_scale=1.5):
    """Append a zero-weight stress timeseries to `period` (copies of the period's first timepoints, loads scaled)."""
    ts = pd.read_csv(inp / "timeseries.csv")
    name = f"{period}_stress"
    ts = pd.concat([ts, pd.DataFrame([{"TIMESERIES": name, "ts_period": period, "ts_duration_of_tp": 12,
                                        "ts_num_tps": n, "ts_scale_to_period": 0.0}])])
    ts.to_csv(inp / "timeseries.csv", index=False)
    tp = pd.read_csv(inp / "timepoints.csv")
    src = tp[tp["timeseries"].str.startswith(str(period))].head(n)
    new_ids = list(range(int(tp["timepoint_id"].max()) + 1, int(tp["timepoint_id"].max()) + 1 + n))
    tmap = dict(zip(src["timepoint_id"], new_ids))
    s = src.assign(timepoint_id=new_ids, timeseries=name, timestamp=src["timestamp"] + 90000000)
    pd.concat([tp, s]).to_csv(inp / "timepoints.csv", index=False)
    lo = pd.read_csv(inp / "loads.csv")
    sl = lo[lo["TIMEPOINT"].isin(tmap)].copy()
    sl["TIMEPOINT"] = sl["TIMEPOINT"].map(tmap)
    sl["zone_demand_mw"] *= load_scale
    pd.concat([lo, sl]).to_csv(inp / "loads.csv", index=False)
    vcf = pd.read_csv(inp / "variable_capacity_factors.csv")
    sv = vcf[vcf["timepoint"].isin(tmap)].copy()
    sv["timepoint"] = sv["timepoint"].map(tmap)
    pd.concat([vcf, sv]).to_csv(inp / "variable_capacity_factors.csv", index=False)
    return new_ids


def write_td_inputs(inp: Path, matching=None, target=0.9, penalty=1000.0, onsite=True, storage=True,
                    clean_fraction=0.5, min_mw=DC_MW, td=True):
    """matching: None (no CFE targets), "annual" (one block per period) or "hourly" (one block per timepoint).
    td=False writes nothing (a toy with the module loaded but no tracked demands)."""
    if not td:
        return
    tp = pd.read_csv(inp / "timepoints.csv")
    ts = pd.read_csv(inp / "timeseries.csv").set_index("TIMESERIES")
    tp["period"] = tp["timeseries"].map(ts["ts_period"])
    pd.DataFrame([{"TRACKED_DEMAND": "DC1", "td_type": "data_center",
                   "td_energy_requirement_mwh_per_year": 0.9 * DC_MW * 8760,
                   "td_default_min_power_mw": min_mw, "td_default_max_power_mw": DC_MW,
                   "td_grid_interconnect_mw": DC_MW}]).to_csv(inp / "tracked_demands.csv", index=False)
    pd.DataFrame([{"TRACKED_DEMAND": "DC1", "LOAD_ZONE": "North"}]).to_csv(
        inp / "tracked_demand_candidate_zones.csv", index=False)
    if matching is not None:
        blk = (tp["timepoint_id"].map(lambda t: f"h{t}") if matching == "hourly" else tp["period"].map(lambda p: "all"))
        pd.DataFrame({"TRACKED_DEMAND": "DC1", "time_block": blk, "TIMEPOINT": tp["timepoint_id"]}).to_csv(
            inp / "tracked_demand_time_blocks.csv", index=False)
        tg = pd.DataFrame({"TRACKED_DEMAND": "DC1", "PERIOD": tp["period"], "time_block": blk}).drop_duplicates()
        tg["td_cfe_target"] = target
        tg["td_cfe_shortfall_penalty"] = penalty
        tg.to_csv(inp / "tracked_demand_cfe_targets.csv", index=False)
    if clean_fraction is not None:
        pd.DataFrame({"LOAD_ZONE": "North", "TIMEPOINT": tp["timepoint_id"],
                      "grid_clean_fraction": clean_fraction}).to_csv(inp / "grid_clean_fraction.csv", index=False)
    if onsite:
        pd.DataFrame([
            {"td_onsite_tech": "solar_onsite", "td_onsite_tech_capital_cost": 90000.0, "td_onsite_tech_is_clean": 1,
             "td_onsite_tech_cf_source": "Solar"},
            {"td_onsite_tech": "gas_recip", "td_onsite_tech_capital_cost": 120000.0,
             "td_onsite_tech_variable_om": 5.0, "td_onsite_tech_fuel_cost": 40.0,
             "td_onsite_tech_emissions": 0.45, "td_onsite_tech_is_clean": 0, "td_onsite_tech_cf_source": "."},
        ]).to_csv(inp / "tracked_demand_onsite_techs.csv", index=False, na_rep=".")
    if storage:
        pd.DataFrame([{"TRACKED_DEMAND": "DC1", "td_storage_power_cost": 30000.0,
                       "td_storage_energy_cost": 20000.0, "td_storage_max_hours": 8}]).to_csv(
            inp / "tracked_demand_storage.csv", index=False)


def solve(run: Path, inputs="inputs", outputs="outputs", extra=(), duals=True):
    """toyutil.solve, optionally without the dual suffix (a MIP has no duals)."""
    if duals:
        return toyutil.solve(run, inputs, outputs, extra)
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--inputs-dir", inputs, "--outputs-dir", outputs,
                        *extra], cwd=run, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(run)})
    assert r.returncode == 0, r.stderr[-3000:] + r.stdout[-2000:]
    return run / outputs


def td_case(tmp_path, name, stress=False, solve=True, extra=(), after=None, load_module=True, duals=True, **kw):
    """Toy run folder with the tracked-demand inputs; after(inp) edits them further. load_module=False leaves the
    module out of modules.txt (the input files are still written)."""
    def edit(inp):
        if stress:
            add_stress_timeseries(inp)
        write_td_inputs(inp, **kw)
        if after is not None:
            after(inp)
    run = toyutil.toy_inputs(tmp_path, name, modules=("tracked_demands",) if load_module else (), edit=edit)
    if solve:
        globals()["solve"](run, extra=extra, duals=duals)
    return run


def read(run: Path, f: str, outputs="outputs") -> pd.DataFrame:
    return pd.read_csv(Path(run) / outputs / f)


def cost(run: Path, outputs="outputs") -> float:
    return toyutil.total_cost(Path(run) / outputs)


def csv(inp: Path, name: str, rows):
    pd.DataFrame(rows).to_csv(Path(inp) / name, index=False, na_rep=".")
