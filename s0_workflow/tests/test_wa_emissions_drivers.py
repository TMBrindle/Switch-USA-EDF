"""§127: the WA emissions-driver script on a hand-built two-stage fixture (not results)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "s0_workflow/scripts"))
import wa_emissions_drivers as wd  # noqa: E402


def _stage(root: Path, year: int, gas_gwh: float, to_ca_mw: float):
    i, o = root / str(year) / "inputs", root / str(year) / "outputs"
    i.mkdir(parents=True)
    o.mkdir(parents=True)
    pd.DataFrame({"INVESTMENT_PERIOD": [year], "period_start": [year - 1], "period_end": [year]}).to_csv(
        i / "periods.csv", index=False)
    # one timeseries: 2 timepoints x 12 h, scaled to 2 years -> each timepoint stands for 8760 / 2 h per year
    pd.DataFrame({"TIMESERIES": ["s"], "ts_period": [year], "ts_duration_of_tp": [12.0], "ts_num_tps": [2],
                  "ts_scale_to_period": [730.0]}).to_csv(i / "timeseries.csv", index=False)
    pd.DataFrame({"timepoint_id": [1, 2], "timestamp": ["a", "b"], "timeseries": ["s", "s"]}).to_csv(
        i / "timepoints.csv", index=False)
    pd.DataFrame({"TRANSMISSION_LINE": ["l1", "l2"], "trans_lz1": ["p1", "p1"], "trans_lz2": ["p8", "p20"],
                  "trans_efficiency": [0.9, 1.0]}).to_csv(i / "transmission_lines.csv", index=False)
    pd.DataFrame({"gen_load_zone": ["p1", "p1", "p20"], "gen_energy_source": ["Naturalgas", "Water", "Naturalgas"],
                  "Energy_GWh_typical_yr": [gas_gwh, 1000.0, 5000.0],
                  "DispatchEmissions_tCO2_per_typical_yr": [gas_gwh * 400.0, 0.0, 2e6]}).to_csv(
        o / "dispatch_gen_annual_summary.csv", index=False)
    pd.DataFrame({"a": ["p1", "p1", "p20"], "b": ["p8", "p20", "p1"], "c": [1, 1, 2],
                  "DispatchTx": [to_ca_mw, 0.0, 100.0]}).to_csv(o / "DispatchTx.csv", index=False)
    pd.DataFrame({"trans_lz_from": ["p20"], "trans_lz_to": ["p1"], "PERIOD": [year], "delivered_mwh_per_yr": [438000.0],
                  "cost_per_mwh": [21.24], "annual_cost": [9.3e6]}).to_csv(o / "trans_import_cost_results.csv",
                                                                           index=False)


def test_wa_drivers_fixture(tmp_path):
    st = wd.states()
    assert st["p1"] == "WA" and st["p8"] == "CA" and st["p20"] not in ("WA", "CA")
    _stage(tmp_path, 2028, 1000.0, 0.0)
    _stage(tmp_path, 2045, 4000.0, 500.0)
    wd.main(["--stage", f"2028={tmp_path / '2028'}", "--stage", f"2045={tmp_path / '2045'}", "--out", str(tmp_path / "o")])
    s = pd.read_csv(tmp_path / "o/wa_summary.csv").set_index("stage")
    assert s.at[2028, "wa_emissions_mt"] == pytest.approx(0.4) and s.at[2045, "wa_gas_mt"] == pytest.approx(1.6)
    assert s.at[2045, "d_wa_gas_mt_vs_first"] == pytest.approx(1.2)
    # 500 MW x 0.9 for 4380 h/yr = 1.971 TWh to CA in 2045; 100 MW from p20 for 4380 h = 0.438 TWh, charged
    assert s.at[2045, "wa_net_exports_to_CA_twh"] == pytest.approx(500 * 0.9 * 4380 / 1e6)
    assert s.at[2045, "wa_imports_from_other_twh"] == pytest.approx(0.438)
    assert s.at[2028, "wa_charged_imports_twh"] == pytest.approx(0.438)
    assert s.at[2045, "d_wa_net_exports_to_CA_twh_vs_first"] == pytest.approx(1.971)
