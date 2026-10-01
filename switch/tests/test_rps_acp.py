"""Toy tests for the opt-in RPS buyout (rps_acp_per_mwh) in study_modules/rps_regional.py.

Uses the Switch 2.0.9 3zone_toy example (set SWITCH_SRC to a clone of
https://github.com/switch-model/switch) and HiGHS. A North-only program at 95% with only North's
small wind/PV eligible and no REC trade cannot be met:
  - with an ACP the model buys out the gap (rps_shortfall.csv, RPSShortfallCost);
  - with the column blank (".") the requirement is hard: infeasible;
  - with a feasible share, a blank column gives the same result as no column at all.

    SWITCH_SRC=... pytest -q switch/tests/test_rps_acp.py
"""
import os
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent / "study_modules" / "rps_regional.py"
pytestmark = pytest.mark.skipif(shutil.which("switch") is None, reason="switch_model not installed")


def _toy(tmp_path, share, acp=None, column=True):
    base = Path(os.environ.get("SWITCH_SRC", HERE.parents[2] / "switch-src"))
    src = base / "examples" / "3zone_toy"
    if not src.exists():
        pytest.skip("set SWITCH_SRC to a clone of https://github.com/switch-model/switch to run")
    run = tmp_path / "toy"
    shutil.copytree(src, run)
    (run / "rps_mod").mkdir()
    shutil.copy(MODULE, run / "rps_mod")
    (run / "rps_mod/__init__.py").touch()
    with open(run / "inputs/modules.txt", "a") as f:
        f.write("\nrps_mod.rps_regional\n")
    inp = run / "inputs"
    gi = pd.read_csv(inp / "gen_info.csv")
    elig = gi[(gi.gen_load_zone == "North") & gi.gen_tech.isin(["Wind", "Central_PV", "Commercial_PV", "Residential_PV"])]
    pd.DataFrame({"RPS_PROGRAM": "TEST_RPS", "RPS_GEN": elig.GENERATION_PROJECT,
                  "send_bundled_recs": 0, "send_unbundled_recs": 0}).to_csv(inp / "rps_generators.csv", index=False)
    periods = pd.read_csv(inp / "periods.csv").INVESTMENT_PERIOD
    req = pd.DataFrame({"RPS_PROGRAM": "TEST_RPS", "LOAD_ZONE": "North", "PERIOD": periods,
                        "rps_share": share, "unbundled_rec_limit_fraction": 0})
    if column:
        req["rps_acp_per_mwh"] = "." if acp is None else acp
    req.to_csv(inp / "rps_requirements.csv", index=False)
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs"], cwd=run, capture_output=True,
                       text=True, env={**os.environ, "PYTHONPATH": str(run)})
    return run, r


def test_acp_buys_out_unmeetable_target(tmp_path):
    run, r = _toy(tmp_path, share=0.95, acp=50.0)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    s = pd.read_csv(run / "outputs/rps_shortfall.csv")
    assert (s.rps_acp_per_mwh == 50.0).all()
    assert (s.shortfall_mwh_per_yr > 0).any()
    assert (s.shortfall_cost_per_yr - 50.0 * s.shortfall_mwh_per_yr).abs().max() < 1e-3
    ci = pd.read_csv(run / "outputs/costs_itemized.csv")
    assert "RPSShortfallCost" in set(ci.Component)
    assert "RPS buyout used" in r.stdout


def test_blank_acp_is_hard(tmp_path):
    run, r = _toy(tmp_path, share=0.95, acp=None)          # column present, "." everywhere
    out = (r.stdout + r.stderr).lower()
    assert r.returncode != 0
    assert "constructing component" not in out              # "." loads as "no ACP", not a data error
    assert "infeasible" in out or "feasible solution was not found" in out


def test_blank_column_matches_no_column(tmp_path):
    run_a, ra = _toy(tmp_path / "a", share=0.05, column=False)
    run_b, rb = _toy(tmp_path / "b", share=0.05, acp=None)
    assert ra.returncode == 0 and rb.returncode == 0, (ra.stdout + rb.stdout)[-3000:]
    ca = float((run_a / "outputs/total_cost.txt").read_text())
    cb = float((run_b / "outputs/total_cost.txt").read_text())
    assert abs(ca - cb) <= 1e-6 * abs(ca)
    assert not (run_a / "outputs/rps_shortfall.csv").exists()
    assert not (run_b / "outputs/rps_shortfall.csv").exists()
