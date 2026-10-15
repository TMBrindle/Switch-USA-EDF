"""§249, §250: Diablo Canyon retires after the 2030 stage (SB 846). The case writer sets the date (apply_retirements:
gen_max_age = retire_year - the latest build year), not PowerGenome; the build checks it, and check_retirements.py
checks the chained 2035 inputs and the outputs. Synthetic case files are fixtures, not results."""
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import production as s0prod  # noqa: E402
from s0_workflow.scripts import check_retirements as cr  # noqa: E402

PERIODS = {2028: (2026, 2028), 2030: (2029, 2030), 2035: (2031, 2035), 2040: (2036, 2040)}
ENTRY = {"name": "Diablo Canyon", "project": "p9_nuclear_1", "last_period": 2030, "retire_year": 2031,
         "expected_mw": 2240}
S0 = {"retirement_checks": [ENTRY]}
DIABLO = [(1985, 1122.0), (1986, 1118.0)]                  # as pg_to_switch writes the existing units (no date)


def _stage(folder: Path, period: int, builds=DIABLO, zone="p9"):
    """One stage folder as pg_to_switch writes it: Diablo with its build years and gen_max_age 500 (no date), a gas
    plant, and "." in gen_info (written back unchanged)."""
    folder.mkdir(parents=True)
    a, b = PERIODS[period]
    pd.DataFrame({"INVESTMENT_PERIOD": [period], "period_start": [a], "period_end": [b]}).to_csv(
        folder / "periods.csv", index=False)
    gi = pd.DataFrame({"GENERATION_PROJECT": ["p9_nuclear_1", "p9_gas"], "gen_tech": ["Nuclear", "NaturalGas"],
                       "gen_load_zone": [zone, zone], "gen_max_age": ["500", "40"],
                       "gen_max_annual_availability": [".", "0.9"]})
    if not builds:
        gi = gi.iloc[1:]
    gi.to_csv(folder / "gen_info.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["p9_nuclear_1"] * len(builds) + ["p9_gas"],
                  "build_year": [b_ for b_, _ in builds] + [2000],
                  "build_gen_predetermined": [m for _, m in builds] + [500.0]}).to_csv(
        folder / "gen_build_predetermined.csv", index=False)
    return folder


def _build(folder, s0=S0):
    """The case writer's two steps, in order (write_case_inputs)."""
    logs = []
    s0prod.apply_retirements(folder, s0, logs.append)
    s0prod.write_retirement_checks(folder, s0, logs.append)
    return logs


def test_settings_v32_on_v31_off():
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert "additional_retirements" not in s0["settings"]                  # §250: not PowerGenome's
    assert s0prod.retirement_checks(s0) == [ENTRY] and s0prod.retirement_checks({}) == []
    pg = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"][
        "s0_production"]["on_pgdays"]["s0_production"]
    assert pg["retirement_checks"] == [] and "additional_retirements" not in pg["settings"]   # regression row
    merged = s0prod.deep_merge({"s0_production": s0}, {"s0_production": pg})["s0_production"]
    assert merged["retirement_checks"] == []
    with pytest.raises(ValueError, match="needs name, project, last_period"):
        s0prod.retirement_checks({"retirement_checks": [{"name": "x"}]})
    with pytest.raises(ValueError, match="retire_year 2030 must be after last_period 2030"):
        s0prod.retirement_checks({"retirement_checks": [{**ENTRY, "retire_year": 2030}]})


def test_in_service_through_2030_and_gone_from_2035(tmp_path):
    for period in (2028, 2030):
        f = _stage(tmp_path / str(period), period)
        logs = _build(f)
        gi = pd.read_csv(f / "gen_info.csv", dtype=str, keep_default_na=False).set_index("GENERATION_PROJECT")
        assert gi.at["p9_nuclear_1", "gen_max_age"] == "45" and gi.at["p9_gas", "gen_max_age"] == "40"
        assert gi.at["p9_nuclear_1", "gen_max_annual_availability"] == "."           # "." kept
        assert "builds 1985 1986, gen_max_age 500 -> 45" in logs[0]
        assert s0prod.operable_mw(f, "p9_nuclear_1") == {period: 2240.0}
        assert "Diablo Canyon %d: 2,240 MW (in service at 2,240 MW)" % period in logs[-1]
        assert pd.read_csv(f / "retirements_applied.csv").gen_max_age.tolist() == [45]
    # 2035 and 2040: PowerGenome still writes both units; with gen_max_age 45 they retire in 2031
    for period in (2035, 2040):
        f = _stage(tmp_path / str(period), period)
        _build(f)
        assert s0prod.operable_mw(f, "p9_nuclear_1") == {period: 0.0}
        assert pd.read_csv(f / "retirement_checks.csv").ok.all()
    # a stage PowerGenome writes without the project: reported, nothing to set
    f = _stage(tmp_path / "gone", 2035, [])
    logs = _build(f)
    assert "Diablo Canyon (p9_nuclear_1): not in this case/stage" in logs[0]


def test_chained_2035_inputs_and_outputs(tmp_path):
    """The 2030 solve carries Diablo's builds (1985 / 1986, from BuildGen) into 2035's chained file; 2035's own
    gen_info (gen_max_age 45) retires them in 2031, so none is in service. check_retirements.py checks it."""
    f = _stage(tmp_path / "2035", 2035)
    _build(f)
    pd.DataFrame({"GENERATION_PROJECT": ["p9_nuclear_1", "p9_nuclear_1", "p9_gas"], "build_year": [1985, 1986, 2000],
                  "build_gen_predetermined": [1122.0, 1118.0, 500.0]}).to_csv(
        f / "gen_build_predetermined.chained.S0prod_A.csv", index=False)
    assert s0prod.operable_mw(f, "p9_nuclear_1", "gen_build_predetermined.chained.S0prod_A.csv") == {2035: 0.0}
    out = tmp_path / "out"
    out.mkdir()
    pd.DataFrame({"GENERATION_PROJECT": ["p9_nuclear_1", "p9_gas"], "PERIOD": [2035, 2035],
                  "GenCapacity": [0.0, 500.0]}).to_csv(out / "gen_cap.csv", index=False)
    lines = cr.check(f, S0, "S0prod_A", out)
    assert len(lines) == 3 and all(ln.startswith("ok") for ln in lines)
    assert cr.main([str(f), "--case", "S0prod_A", "--outputs", str(out)]) == 0
    # without the date (gen_max_age 500, v3.1): the carried builds are in service in 2035 -> FAIL
    gi = pd.read_csv(f / "gen_info.csv", dtype=str, keep_default_na=False)
    gi.loc[gi.GENERATION_PROJECT == "p9_nuclear_1", "gen_max_age"] = "500"
    gi.to_csv(f / "gen_info.csv", index=False)
    assert sum(ln.startswith("FAIL") for ln in cr.check(f, S0, "S0prod_A", out)) == 2
    assert cr.main([str(f), "--case", "S0prod_A"]) == 1


def test_wrong_dates_stop_the_build(tmp_path):
    # v3.1 (no date: gen_max_age 500) still in service in 2035
    f = _stage(tmp_path / "a", 2035)
    with pytest.raises(ValueError, match="Diablo Canyon .* 2,240 MW in 2035, expected retired"):
        s0prod.write_retirement_checks(f, S0, print)
    # SB 846's own years per unit would drop Unit 1 from the 2030 stage (period starting 2029): 2029 - 1985 = 44
    f = _stage(tmp_path / "b", 2030)
    s0prod.apply_retirements(f, {"retirement_checks": [{**ENTRY, "last_period": 2028, "retire_year": 2029}]}, print)
    assert s0prod.operable_mw(f, "p9_nuclear_1") == {2030: 0.0}
    # a different MW in service (e.g. another plant clustered in) stops the build
    f = _stage(tmp_path / "c", 2028, [(1985, 1122.0), (1986, 1118.0), (1986, 500.0)])
    with pytest.raises(ValueError, match="2,740 MW in 2028, expected in service at 2,240 MW"):
        _build(f)
    # missing altogether in 2028
    with pytest.raises(ValueError, match="0 MW in 2028, expected in service"):
        _build(_stage(tmp_path / "d", 2028, []))
    # a retire_year before a build year
    with pytest.raises(ValueError, match="retire_year 1986 is not after its latest build year 1986"):
        s0prod.apply_retirements(_stage(tmp_path / "e", 2028), {"retirement_checks": [
            {**ENTRY, "last_period": 1980, "retire_year": 1986}]}, print)
    # a regional case without zone p9 is not checked (reported)
    g = _stage(tmp_path / "f", 2028, [], zone="p1")
    logs = _build(g)
    assert "Diablo Canyon: zone p9 not in the case" in logs[-1]


def test_off_writes_nothing(tmp_path):
    """v3.1, flags-off and the regression row ([]): gen_info byte-identical, no files. An entry without retire_year
    (a check only) changes nothing either."""
    for name, s0 in (("off", {}), ("empty", {"retirement_checks": []}),
                     ("check_only", {"retirement_checks": [{k: v for k, v in ENTRY.items() if k != "retire_year"}]})):
        f = _stage(tmp_path / name, 2028)
        before = (f / "gen_info.csv").read_bytes()
        s0prod.apply_retirements(f, s0, print)
        assert (f / "gen_info.csv").read_bytes() == before and not (f / "retirements_applied.csv").exists()
    s0prod.write_retirement_checks(tmp_path / "off", {}, print)
    assert not (tmp_path / "off/retirement_checks.csv").exists()


def test_hooked_into_case_inputs():
    """apply_retirements runs first: before every step that reads gen_max_age (prm's existing capacity, the check)."""
    src = (REPO / "s0_workflow/production.py").read_text()
    body = src[src.index("def write_case_inputs("):]
    body = body[:body.index("\ndef ")]
    steps = [ln.strip().split("(")[0] for ln in body.splitlines() if ln.startswith("    ") and "(out_folder, s0" in ln]
    assert steps[0] == "apply_retirements"
    assert steps.index("apply_retirements") < steps.index("prm.write_case_inputs") < steps.index(
        "write_retirement_checks")
