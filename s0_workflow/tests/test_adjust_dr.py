"""adjust/add_dr_info.py sizes DR cost off the weighted (normal) peak: zero-weight stress days, which the S0
regional reserve adds during time sampling (before the adjust scripts run), don't change dr_annual_cost.csv
(CHANGES §63). Small hand-built inputs: fixtures, not results."""
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]


def case(d: Path, stress: bool) -> Path:
    d.mkdir(parents=True)
    ts = [{"timeseries": "2035_p10", "ts_period": 2035, "ts_duration_of_tp": 1, "ts_num_tps": 2, "ts_scale_to_period": 900.0},
          {"timeseries": "2035_p200", "ts_period": 2035, "ts_duration_of_tp": 1, "ts_num_tps": 2, "ts_scale_to_period": 925.0}]
    tp = [(20351011000, "2035_p10"), (20351011001, "2035_p10"), (20351071900, "2035_p200"), (20351071901, "2035_p200")]
    load = {"z1": [100, 120, 150, 140], "z2": [50, 55, 60, 58]}
    if stress:                                                   # stress day: super-peak load, zero weight
        ts.append({"timeseries": "2035_p300_prm", "ts_period": 2035, "ts_duration_of_tp": 1, "ts_num_tps": 2,
                   "ts_scale_to_period": 0.0})
        tp += [(920351102700, "2035_p300_prm"), (920351102701, "2035_p300_prm")]
        load = {"z1": load["z1"] + [400, 390], "z2": load["z2"] + [90, 95]}
    pd.DataFrame(ts).to_csv(d / "timeseries.csv", index=False)
    pd.DataFrame(tp, columns=["timepoint_id", "timeseries"]).assign(timestamp=lambda x: x.timepoint_id).to_csv(
        d / "timepoints.csv", index=False)
    pd.DataFrame([{"LOAD_ZONE": z, "TIMEPOINT": t, "zone_demand_mw": v} for z, vals in load.items()
                  for (t, _), v in zip(tp, vals)]).to_csv(d / "loads.csv", index=False)
    return d


def run(script: Path, d: Path):
    r = subprocess.run([sys.executable, str(script), str(d)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return pd.read_csv(d / "dr_annual_cost.csv"), (d / "dr_annual_cost.csv").read_bytes()


def test_stress_days_dont_change_dr_cost(tmp_path):
    new = REPO / "adjust/add_dr_info.py"
    a, ab = run(new, case(tmp_path / "base", False))
    b, bb = run(new, case(tmp_path / "stress", True))
    assert ab == bb                                                     # stress days: no effect
    assert a.set_index("LOAD_ZONE").dr_annual_cost.to_dict() == {"z1": 0.03 * 150 * 43000, "z2": 0.03 * 60 * 43000}
    # dr_data.csv still covers every timepoint, stress days included (DR can shift load there)
    assert len(pd.read_csv(tmp_path / "stress/dr_data.csv")) == 12
    # the previous version: stress days raised the cost; without them, both versions agree byte for byte
    old = tmp_path / "old_add_dr_info.py"
    old.write_text(subprocess.run(["git", "show", "113dc89:adjust/add_dr_info.py"], cwd=REPO, capture_output=True,
                                  text=True, check=True).stdout)
    c, _ = run(old, case(tmp_path / "old_stress", True))
    assert c.set_index("LOAD_ZONE").dr_annual_cost["z1"] == 0.03 * 400 * 43000
    _, db = run(old, case(tmp_path / "old_base", False))
    assert db == ab


def test_other_adjust_scripts_use_weighted_peaks():
    """add_ee_info.py weights by tp_weight; define_scenarios.py's low-growth peak now ignores zero-weight days."""
    ee = (REPO / "adjust/add_ee_info.py").read_text()
    assert "tp_weight=ts_duration_of_tp * ts_scale_to_period" in ee
    ds = (REPO / "adjust/define_scenarios.py").read_text()
    assert 'l[l["tp_weight"] > 0]' in ds
