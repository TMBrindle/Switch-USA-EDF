"""§245: no new nuclear in California's zones (s0_production.state_nuclear_bans). Synthetic case files are fixtures."""
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import production as s0prod  # noqa: E402

NEW = "Nuclear_Nuclear - Large_Moderate"


def _case(folder: Path, predetermined=("p9_nuclear_1",)):
    folder.mkdir(parents=True, exist_ok=True)
    gi = pd.DataFrame({"GENERATION_PROJECT": ["p9_nuclear_1", "p8_nuclear_nuclear__large_moderate_0",
                                              "p9_nuclear_nuclear__large_moderate_0", "p11_nuclear_nuclear__large_moderate_0",
                                              "p1_nuclear_nuclear__large_moderate_0", "p8_solar"],
                       "gen_tech": ["Nuclear", NEW, NEW, NEW, NEW, "UtilityPV_Class1_Moderate"],
                       "gen_load_zone": ["p9", "p8", "p9", "p11", "p1", "p8"]})
    gi.to_csv(folder / "gen_info.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": list(predetermined), "build_year": [1985] * len(predetermined),
                  "build_gen_predetermined": [2200.0] * len(predetermined)}).to_csv(
        folder / "gen_build_predetermined.csv", index=False)
    pd.DataFrame({"INVESTMENT_PERIOD": [2035], "period_start": [2031], "period_end": [2035]}).to_csv(
        folder / "periods.csv", index=False)
    pd.DataFrame({"MAX_CAP_PROGRAM": ["MaxCapTag_Ban", "MaxCapTag_NuclearGrowth"], "PERIOD": [2035, 2035],
                  "max_cap_mw": [0.0, 100016.4]}).to_csv(folder / "max_cap_requirements.csv", index=False)
    pd.DataFrame({"MAX_CAP_PROGRAM": ["MaxCapTag_NuclearGrowth"] * 3,
                  "MAX_CAP_GEN": ["p8_nuclear_nuclear__large_moderate_0", "p9_nuclear_nuclear__large_moderate_0",
                                  "p1_nuclear_nuclear__large_moderate_0"]}).to_csv(folder / "max_cap_generators.csv",
                                                                                   index=False)
    return folder


def test_v32_on_code_default_off():
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0prod.state_nuclear_bans(s0) == ["CA"] and s0prod.state_nuclear_bans({}) == []
    sm = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]
    assert sm["s0_production"]["on_pgdays"]["s0_production"]["state_nuclear_bans"] == {"states": []}   # regression row
    with pytest.raises(ValueError, match="two-letter"):
        s0prod.state_nuclear_bans({"state_nuclear_bans": {"states": ["California"]}})


def test_ca_new_nuclear_capped_at_zero(tmp_path):
    c = _case(tmp_path / "c")
    logs = []
    s0prod.write_state_nuclear_bans(c, {"state_nuclear_bans": {"states": ["CA"]}}, logs.append)
    req = pd.read_csv(c / "max_cap_requirements.csv")
    gens = pd.read_csv(c / "max_cap_generators.csv")
    r = req[req.MAX_CAP_PROGRAM == "MaxCapTag_StateBan_CA_Nuclear"]
    assert r.PERIOD.tolist() == [2035] and r.max_cap_mw.tolist() == [0.0]
    g = set(gens[gens.MAX_CAP_PROGRAM == "MaxCapTag_StateBan_CA_Nuclear"].MAX_CAP_GEN)
    # CA's new-build candidates only: not Diablo Canyon (existing), not p1 (WA), not solar
    assert g == {"p8_nuclear_nuclear__large_moderate_0", "p9_nuclear_nuclear__large_moderate_0",
                 "p11_nuclear_nuclear__large_moderate_0"}
    # the existing programs are kept
    assert {"MaxCapTag_Ban", "MaxCapTag_NuclearGrowth"} <= set(req.MAX_CAP_PROGRAM) and len(gens) == 6
    assert "CA 3 new-nuclear projects capped at 0 MW (p8 p9 p11)" in logs[-1]
    assert pd.read_csv(c / "state_nuclear_bans.csv").projects.tolist() == [3]
    # run twice: refused, not doubled
    with pytest.raises(ValueError, match="already in max_cap_requirements"):
        s0prod.write_state_nuclear_bans(c, {"state_nuclear_bans": {"states": ["CA"]}}, logs.append)


def test_off_and_no_candidates(tmp_path):
    c = _case(tmp_path / "off")
    before = {f: (c / f).read_bytes() for f in ("max_cap_requirements.csv", "max_cap_generators.csv")}
    s0prod.write_state_nuclear_bans(c, {}, print)                                 # flag off (v3.1)
    assert all((c / f).read_bytes() == b for f, b in before.items()) and not (c / "state_nuclear_bans.csv").exists()
    # a stage that offers no new nuclear (before 2035): nothing added, reported
    gi = pd.read_csv(c / "gen_info.csv")
    gi[~gi.gen_tech.str.startswith("Nuclear_")].to_csv(c / "gen_info.csv", index=False)
    logs = []
    s0prod.write_state_nuclear_bans(c, {"state_nuclear_bans": {"states": ["CA"]}}, logs.append)
    assert all((c / f).read_bytes() == b for f, b in before.items()) and "none offered in this stage" in logs[-1]


def test_predetermined_candidate_refused(tmp_path):
    c = _case(tmp_path / "p", predetermined=("p9_nuclear_1", "p9_nuclear_nuclear__large_moderate_0"))
    with pytest.raises(ValueError, match="predetermined capacity"):
        s0prod.write_state_nuclear_bans(c, {"state_nuclear_bans": {"states": ["CA"]}}, print)


def test_hooked_into_case_inputs():
    src = (REPO / "s0_workflow/production.py").read_text()
    body = src[src.index("def write_case_inputs("):]
    assert "write_state_nuclear_bans(out_folder, s0, log)" in body
