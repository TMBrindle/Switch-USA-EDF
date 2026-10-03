"""Integration toy: interconnection headroom (host mode) and build-rate limits switched on together.

Run from interconnection_headroom/: SWITCH_SRC=/opt/switch-src pytest -q tests/test_s0_integration.py
Small hand-built frames are test fixtures, not results.
"""
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]


def _switch_src() -> Path:
    for p in (os.environ.get("SWITCH_SRC"), "/opt/switch-src"):
        if p and Path(p).exists():
            return Path(p)
    return Path("/opt/switch-src")


def _solve(run):
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--suffixes", "dual"], cwd=run,
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(run)})
    assert r.returncode == 0, r.stderr[-3000:] + r.stdout[-2000:]


def _toy(tmp_path, ic=True, br=True, wind_rate=0.5):
    src = _switch_src() / "examples" / "3zone_toy"
    if shutil.which("switch") is None or not src.exists():
        pytest.skip("switch / 3zone_toy example not available (set SWITCH_SRC)")
    run = tmp_path / f"toy_{int(ic)}{int(br)}"
    shutil.copytree(src, run)
    (run / "mods").mkdir()
    (run / "mods/__init__.py").touch()
    inp = run / "inputs"
    gi = pd.read_csv(inp / "gen_info.csv", na_values=".")
    gi["gen_unit_size"] = np.nan            # LP, so duals exist
    gi["gen_min_build_capacity"] = 0
    gi.to_csv(inp / "gen_info.csv", index=False, na_rep=".")
    mods = []
    if ic:
        shutil.copy(REPO / "switch/study_modules/interconnection_headroom.py", run / "mods")
        mods.append("mods.interconnection_headroom")
        for f in ("ic_params.csv", "ic_weights.csv", "ic_zones.csv", "ic_tranches.csv"):
            shutil.copy(REPO / "interconnection_headroom/tests/switch_toy" / f, inp)
        # the three options in host mode (pipeline default atts_mode / reinforcement_mode: host)
        pd.DataFrame({"IC_UPRATE": ["N_gets", "N_reconductor", "N_conv"], "ic_uprate_zone": ["N"] * 3,
                      "ic_uprate_type": ["gets", "reconductor", "conv_reinforcement"],
                      "ic_uprate_max_mw": [0.5, 1.0, 100.0],
                      "ic_uprate_cost_per_mw": [30000.0, 130000.0, 200000.0],
                      "ic_uprate_available_year": [0, 0, 0],
                      "ic_uprate_mode": ["host"] * 3}).to_csv(inp / "ic_uprates.csv", index=False)
    if br:
        shutil.copy(REPO / "switch/study_modules/build_rate.py", run / "mods")
        mods.append("mods.build_rate")
        wind = gi[gi["gen_tech"] == "Wind"]["GENERATION_PROJECT"]
        pv = gi[gi["gen_tech"] == "Central_PV"]["GENERATION_PROJECT"]
        pd.DataFrame({"GENERATION_PROJECT": list(wind) + list(pv),
                      "br_gen_group": ["wind_onshore"] * len(wind) + ["solar"] * len(pv)}).to_csv(
            inp / "build_rate_gens.csv", index=False)
        pd.DataFrame({"BR_GROUP": ["wind_onshore", "solar"], "br_growth": [0.05, 0.05],
                      "br_ramp_floor_mw": [10.0, 100.0], "br_life_years": [30, 30],
                      "br_ceiling_slack_cost_per_mw": [-1, -1]}).to_csv(inp / "build_rate_groups.csv", index=False)
        rows_p, rows_t = [], []
        for p in (2020, 2030):
            for g, r in (("wind_onshore", wind_rate), ("solar", 100.0)):
                rows_p.append({"BR_GROUP": g, "PERIOD": p, "br_rate_data_mw": r})
                for k, (w, a) in enumerate(zip((1.3, 0.45, 0.25), (0.0, 0.15, 0.50))):
                    rows_t.append({"BR_GROUP": g, "PERIOD": p, "BR_TIER": f"t{k + 1}", "br_tier_width": w,
                                   "br_tier_adder_per_mw": a * 1.5e6})
        pd.DataFrame(rows_p).to_csv(inp / "build_rate_periods.csv", index=False)
        pd.DataFrame(rows_t).to_csv(inp / "build_rate_tiers.csv", index=False)
    with open(inp / "modules.txt", "a") as f:
        f.write("\n" + "\n".join(mods) + "\n")
    _solve(run)
    return run


def _objective(run):
    return float(pd.read_csv(run / "outputs/total_cost.txt", header=None).iat[0, 0])


def test_toy_both_modules_on(tmp_path):
    both = _toy(tmp_path)
    ic_only = _toy(tmp_path, br=False)
    br_only = _toy(tmp_path, ic=False)
    out = both / "outputs"
    for f in ("ic_spend.csv", "ic_network.csv", "ic_headroom.csv", "build_rate_new_build.csv",
              "build_rate_tiers_built.csv"):
        assert (out / f).exists(), f
    # headroom constraint holds with build-rate limits active
    hr = pd.read_csv(out / "ic_headroom.csv")
    assert (hr.new_capacity_mw_weighted <= hr.headroom_bought_mw + hr.freed_headroom_mw + 1e-6).all()
    # build-rate ceiling (2.0 x rate x 10-year window = 10 MW of wind in 2030) binds with headroom active
    nb = pd.read_csv(out / "build_rate_new_build.csv").set_index(["group", "period"])
    assert nb.at[("wind_onshore", 2030), "new_build_mw"] == pytest.approx(10.0)
    assert nb.at[("wind_onshore", 2030), "adder_cost_overnight"] > 0
    # hosted options fill in cost order (GETs, advanced conductors, then conventional reinforcement),
    # each paying only its own $/MW; generation on the curve pays the reactive steps
    sp = pd.read_csv(out / "ic_spend.csv").query("ic_zone == 'N' and period == 2030").set_index("type")
    assert sp.at["gets", "generation_mw_enabled"] == pytest.approx(0.5)
    assert sp.at["reconductor", "generation_mw_enabled"] == pytest.approx(1.0)
    assert 0 < sp.at["conv_reinforcement", "generation_mw_enabled"] < 100
    for ty, c in (("gets", 30000.0), ("reconductor", 130000.0), ("conv_reinforcement", 200000.0)):
        assert sp.at[ty, "overnight_cost"] == pytest.approx(sp.at[ty, "generation_mw_enabled"] * c)
    # each module only adds constraints: the joint solve costs at least as much as either alone
    assert _objective(both) >= max(_objective(ic_only), _objective(br_only)) - 1e-6 * _objective(both)
