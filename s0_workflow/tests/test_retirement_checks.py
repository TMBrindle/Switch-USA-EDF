"""§249: Diablo Canyon retires after the 2030 stage (SB 846), via additional_retirements, and the build checks it.
Synthetic case files are fixtures, not results."""
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import production as s0prod  # noqa: E402

PERIODS = {2028: (2026, 2028), 2030: (2029, 2030), 2035: (2031, 2035)}
S0 = {"retirement_checks": [{"name": "Diablo Canyon", "project": "p9_nuclear_1", "last_period": 2030}]}


def _stage(folder: Path, period: int, builds):
    """One stage folder: Diablo as pg_to_switch writes an existing unit with a planned retirement year Y: build_year
    Y - 500, gen_max_age 500 (builds = [(build_year, MW)])."""
    folder.mkdir(parents=True)
    a, b = PERIODS[period]
    pd.DataFrame({"INVESTMENT_PERIOD": [period], "period_start": [a], "period_end": [b]}).to_csv(
        folder / "periods.csv", index=False)
    gi = pd.DataFrame({"GENERATION_PROJECT": ["p9_nuclear_1", "p9_gas"], "gen_tech": ["Nuclear", "NaturalGas"],
                       "gen_load_zone": ["p9", "p9"], "gen_max_age": [500, 40]})
    if not builds:
        gi = gi.iloc[1:]
    gi.to_csv(folder / "gen_info.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["p9_nuclear_1"] * len(builds) + ["p9_gas"],
                  "build_year": [b_ for b_, _ in builds] + [2000],
                  "build_gen_predetermined": [m for _, m in builds] + [500.0]}).to_csv(
        folder / "gen_build_predetermined.csv", index=False)
    return folder


def test_settings_v32_on_v31_off():
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["settings"]["additional_retirements"] == [[6099, 1, 2031], [6099, 2, 2031]]
    assert s0prod.retirement_checks(s0) == S0["retirement_checks"] and s0prod.retirement_checks({}) == []
    pg = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"][
        "s0_production"]["on_pgdays"]["s0_production"]
    assert pg["settings"]["additional_retirements"] is None and pg["retirement_checks"] == []    # regression row
    # merged as apply_settings does: the regression row's ~ replaces the list (no retirement date, as v3.1)
    merged = s0prod.deep_merge({"s0_production": s0}, {"s0_production": pg})["s0_production"]
    assert merged["settings"]["additional_retirements"] is None
    with pytest.raises(ValueError, match="needs name, project, last_period"):
        s0prod.retirement_checks({"retirement_checks": [{"name": "x"}]})


def test_in_service_through_2030_and_gone_from_2035(tmp_path):
    enc = [(2031 - 500, 1122.0), (2031 - 500, 1118.0)]                      # year 2031, encoded
    for period in (2028, 2030):
        f = _stage(tmp_path / str(period), period, enc)
        assert s0prod.operable_mw(f, "p9_nuclear_1") == {period: 2240.0}
        logs = []
        s0prod.write_retirement_checks(f, S0, logs.append)
        assert "Diablo Canyon %d: 2,240 MW (in service)" % period in logs[-1]
    # 2035: encoded 2031 retires at the start of the period starting 2031; or PowerGenome leaves it out
    for name, builds in (("2035enc", enc), ("2035gone", [])):
        f = _stage(tmp_path / name, 2035, builds)
        assert s0prod.operable_mw(f, "p9_nuclear_1") == {2035: 0.0}
        s0prod.write_retirement_checks(f, S0, print)
        assert pd.read_csv(f / "retirement_checks.csv").ok.all()


def test_wrong_dates_stop_the_build(tmp_path):
    # v3.1 (no date: build years 1985 / 1986) still in service in 2035
    with pytest.raises(ValueError, match="Diablo Canyon .* 2,240 MW in 2035, expected retired"):
        s0prod.write_retirement_checks(_stage(tmp_path / "a", 2035, [(1985, 1122.0), (1986, 1118.0)]), S0, print)
    # SB 846's own years (2029 / 2030): Unit 1 would be gone from the 2030 stage (period starting 2029)
    f = _stage(tmp_path / "b", 2030, [(2029 - 500, 1122.0), (2030 - 500, 1118.0)])
    assert s0prod.operable_mw(f, "p9_nuclear_1") == {2030: 1118.0}
    # missing altogether in 2028
    with pytest.raises(ValueError, match="0 MW in 2028, expected in service"):
        s0prod.write_retirement_checks(_stage(tmp_path / "c", 2028, []), S0, print)


def test_off_writes_nothing(tmp_path):
    f = _stage(tmp_path / "off", 2035, [(1985, 1122.0)])
    s0prod.write_retirement_checks(f, {}, print)
    assert not (f / "retirement_checks.csv").exists()


def test_hooked_into_case_inputs():
    src = (REPO / "s0_workflow/production.py").read_text()
    assert "write_retirement_checks(out_folder, s0, log)" in src[src.index("def write_case_inputs("):]
