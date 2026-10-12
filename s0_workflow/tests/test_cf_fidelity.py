"""Sample-fidelity rescale, option B (CHANGES §116; s0_workflow/cf_fidelity.py, pg_to_switch.operational_files).
Hand-built profiles and sample: fixtures, not results."""
import ast
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import cf_fidelity as cf  # noqa: E402

GENS = pd.DataFrame({"Resource": ["pv", "wind", "osw", "gas", "dpv", "pv_dark"],
                     "technology": ["UtilityPV_Class1_Moderate", "LandbasedWind_Class3_Moderate",
                                    "OffShoreWind_Class3_Moderate", "NaturalGas_CCAvg", "Distributed_Rooftop_PV",
                                    "UtilityPV_Class1_Moderate"]})


def _case():
    rng = np.random.default_rng(3)
    H = 24 * 70
    day = np.clip(np.sin(np.linspace(0, np.pi, 24)), 0, None)
    full = pd.DataFrame({"pv": np.tile(day, 70) * rng.uniform(0.3, 1.0, H),
                         "wind": rng.uniform(0, 0.9, H), "osw": rng.uniform(0.3, 0.98, H),
                         "gas": np.ones(H), "dpv": np.tile(day, 70) * 0.5, "pv_dark": np.tile(day, 70) * 0.4})
    picks = [3, 10, 25, 40, 61]                                   # 4 sample days + 1 stress day (the last)
    sampled = pd.concat([full.iloc[d * 24:(d + 1) * 24] for d in picks], ignore_index=True)
    sampled.loc[:, "pv_dark"] = 0.0                               # no output on the sampled days
    ts = pd.DataFrame({"timeseries": [f"2030_p{i}" for i in range(4)] + ["2030_p4_prm"],
                       "ts_scale_to_period": [30.0, 20.0, 40.0, 10.0, 0.0], "ts_duration_of_tp": [1.0] * 5,
                       "ts_num_tps": [24] * 5})
    tp = pd.DataFrame({"timeseries": np.repeat(ts.timeseries.values, 24), "timepoint_id": range(120)})
    return full, sampled, ts, tp


CFG = {**cf.DEFAULTS, "mode": "rescale"}


def test_weights_and_stress_days():
    _, _, ts, tp = _case()
    w = cf.row_weights(ts, tp)
    assert list(np.unique(w[:96])) == [10.0, 20.0, 30.0, 40.0] and (w[96:] == 0).all()


def test_rescale_matches_full_record_and_leaves_the_rest():
    full, sampled, ts, tp = _case()
    w = cf.row_weights(ts, tp)
    out, rep = cf.rescale(full, sampled, GENS, w, CFG, 2030)
    rep = rep.set_index("GENERATION_PROJECT")
    assert set(rep.index) == {"pv", "wind", "osw", "pv_dark"}                     # VRE only: no gas, no rooftop PV
    on = w > 0
    for g in ("pv", "wind", "osw"):
        got = (out[g].values[on] * w[on]).sum() / w[on].sum()
        assert got == pytest.approx(full[g].mean(), abs=1e-6), g                  # the full-record CF, exactly
        assert rep.at[g, "residual"] == pytest.approx(0, abs=1e-6)
        assert (out[g] <= 1.0).all() and (out[g] >= 0).all()
        np.testing.assert_array_equal(out[g].values[~on], sampled[g].values[~on])  # stress day untouched
    for g in ("gas", "dpv"):
        pd.testing.assert_series_equal(out[g], sampled[g])
    assert rep.at["pv_dark", "flag"].startswith("no output") and (out["pv_dark"] == 0).all()
    assert list(out.columns) == list(sampled.columns) and len(out) == len(sampled)   # same rows: same model size


def test_cap_at_one_puts_energy_back_and_flags_big_scales():
    full, sampled, ts, tp = _case()
    w = cf.row_weights(ts, tp)
    sampled = sampled.copy()
    sampled["osw"] = np.where(np.arange(len(sampled)) % 2 == 0, 0.95, 0.05) * 0.5  # far below the record...
    sampled.loc[::4, "osw"] = 0.9                                                    # ...with hours that cap at 1
    out, rep = cf.rescale(full, sampled, GENS, w, CFG, 2030)
    r = rep.set_index("GENERATION_PROJECT").loc["osw"]
    assert r.scale > 1.25 and "outside 0.8-1.25" in r.flag
    assert r.capped_share_of_sample_hours > 0 and out["osw"].max() <= 1.0
    on = w > 0
    assert (out["osw"].values[on] * w[on]).sum() / w[on].sum() == pytest.approx(full["osw"].mean(), abs=1e-4)


def test_settings_default_off():
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["time_sampling"]["cf_fidelity"]["mode"] == "off"
    assert cf.settings({"s0_production": {**s0, "enabled": True}}) is None
    on = {**s0, "enabled": True, "time_sampling": {**s0["time_sampling"], "cf_fidelity": {"mode": "rescale"}}}
    assert cf.settings({"s0_production": on})["techs"] == ["solar", "onwind", "offwind"]
    with pytest.raises(ValueError, match="cf_fidelity.mode"):
        cf.settings({"s0_production": {"enabled": True, "time_sampling": {"cf_fidelity": {"mode": "zone_rows"}}}})


def test_wired_before_the_capacity_factor_table():
    src = (REPO / "pg_to_switch.py").read_text()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "operational_files")
    body = ast.unparse(fn)
    i, j = body.index("s0_cf_fidelity.rescale("), body.index("variable_capacity_factors_table(")
    assert i < j and "s0_cf_fidelity.settings(year_settings) if cluster_time else None" in body
    assert "vcf_fidelity.csv" in body
