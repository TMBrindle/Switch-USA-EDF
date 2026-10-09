import pandas as pd
import pytest

from gasnet import report

FUELS = ["naturalgas", "naturalgas_ccs90", "naturalgas_ccs100"]


def _shares(p):
    rows = [("z1", 2030, "appalachia", 0.75), ("z1", 2030, "permian", 0.25),
            ("z2", 2030, "permian", 1.0), ("z1", 2024, "appalachia", 1.0), ("z2", 2024, "permian", 1.0)]
    pd.DataFrame(rows, columns=["zone", "year", "basin", "share"]).assign(method="t", source_vintage="v").to_csv(
        p, index=False)


def _outputs(d, extra_fuel=None):
    fu = [("g1", "2030010100", "naturalgas", 10.0), ("g1", "2030010101", "naturalgas", 20.0),
          ("g2", "2030010100", "naturalgas_ccs90", 4.0), ("g3", "2030010100", "coal", 99.0),
          ("g4", "2030010100", "naturalgas_ccs100", 1.0)]
    if extra_fuel:
        fu.append(("g1", "2030010100", extra_fuel, 1.0))
    pd.DataFrame(fu, columns=["GEN_TP_FUELS_1", "GEN_TP_FUELS_2", "GEN_TP_FUELS_3", "GenFuelUseRate"]).to_csv(
        d / "GenFuelUseRate.csv", index=False)
    disp = [("g1", "2030010100", "z1", 2030, 100.0), ("g1", "2030010101", "z1", 2030, 50.0),
            ("g2", "2030010100", "z2", 2030, 100.0), ("g3", "2030010100", "z2", 2030, 100.0),
            ("g4", "2030010100", "z1", 2030, 0.0)]       # stress-day style zero weight
    pd.DataFrame(disp, columns=["generation_project", "timestamp", "gen_load_zone", "period",
                                "tp_weight_in_year_hrs"]).to_csv(d / "dispatch.csv", index=False)


def test_basin_gas_mmbtu_from_dispatch(tmp_path):
    _outputs(tmp_path)
    _shares(tmp_path / "s.csv")
    out = report.basin_gas_mmbtu(tmp_path, tmp_path / "s.csv", FUELS)
    got = out.set_index(["zone", "basin"]).basin_gas_mmbtu.to_dict()
    # z1: 10*100 + 20*50 = 2000 MMBtu -> 1500 appalachia, 500 permian; z2: 4*100 = 400 (coal excluded)
    assert got == {("z1", "appalachia"): 1500.0, ("z1", "permian"): 500.0, ("z2", "permian"): 400.0}
    assert set(out.share_year) == {2030}
    assert (tmp_path / "basin_gas_mmbtu.csv").exists()


def test_basin_gas_mmbtu_from_inputs(tmp_path):
    _outputs(tmp_path)
    (tmp_path / "dispatch.csv").unlink()
    inp = tmp_path / "in"
    inp.mkdir()
    pd.DataFrame({"GENERATION_PROJECT": ["g1", "g2", "g3", "g4"], "gen_load_zone": ["z1", "z2", "z2", "z1"]}).to_csv(
        inp / "gen_info.csv", index=False)
    pd.DataFrame({"timepoint_id": ["2030010100", "2030010101"], "timestamp": ["2030010100", "2030010101"],
                  "timeseries": ["ts1", "ts1"]}).to_csv(inp / "timepoints.csv", index=False)
    # weight = 1 h x 1000 / 10-yr period = 100 h/yr
    pd.DataFrame({"timeseries": ["ts1"], "ts_period": [2030], "ts_duration_of_tp": [1], "ts_num_tps": [2],
                  "ts_scale_to_period": [1000.0]}).to_csv(inp / "timeseries.csv", index=False)
    pd.DataFrame({"INVESTMENT_PERIOD": [2030], "period_start": [2026], "period_end": [2035]}).to_csv(
        inp / "periods.csv", index=False)
    _shares(tmp_path / "s.csv")
    out = report.basin_gas_mmbtu(tmp_path, tmp_path / "s.csv", FUELS, inputs_dir=inp, write=False)
    tot = out.groupby("zone").basin_gas_mmbtu.sum().to_dict()
    assert tot == {"z1": 10 * 100 + 20 * 100 + 1 * 100, "z2": 4 * 100.0}


def test_unlisted_gas_fuel_fails(tmp_path):
    _outputs(tmp_path, extra_fuel="naturalgas_onsite")
    _shares(tmp_path / "s.csv")
    with pytest.raises(ValueError, match="naturalgas_onsite"):
        report.basin_gas_mmbtu(tmp_path, tmp_path / "s.csv", FUELS)


def test_share_year_falls_back_to_latest_earlier(tmp_path):
    _outputs(tmp_path)
    d = pd.read_csv(tmp_path / "dispatch.csv")
    d["period"] = 2032
    d.to_csv(tmp_path / "dispatch.csv", index=False)
    _shares(tmp_path / "s.csv")
    out = report.basin_gas_mmbtu(tmp_path, tmp_path / "s.csv", FUELS, write=False)
    assert set(out.share_year) == {2030} and set(out.period) == {2032}
