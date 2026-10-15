"""§130: s0_workflow/data/v32/v32_flags_off.patch turns every v3.2 default in s0_production.yml back to v3.1, for the
runner's flags-off hash-check build. The patched file must parse to exactly the settings of 4696653, the last commit
before the defaults went on (§126, §129)."""
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
PATCH = REPO / "s0_workflow/data/v32/v32_flags_off.patch"
OFF = "4696653"


def _patched_text() -> str:
    text = (REPO / "pg/settings/s0_production.yml").read_text()
    lines = PATCH.read_text().splitlines()
    minus = [l[1:] for l in lines if l.startswith("-") and not l.startswith("---")]
    plus = [l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")]
    # §132: + pv_profile_repair; §134: + powergenome_commit; §245: + state_nuclear_bans; §246: cf_fidelity's line
    # (load_hydro); §247: + the six build-rate package keys; §249/§250: + retirement_checks (Diablo's date and check)
    assert len(minus) == len(plus) == 17
    for a, b in zip(minus, plus):
        assert text.count(a + "\n") == 1, a
        text = text.replace(a + "\n", b + "\n")
    return text


def test_patch_applies_with_git():
    r = subprocess.run(["git", "apply", "--check", str(PATCH)], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_patched_settings_equal_the_flags_off_commit():
    old = subprocess.run(["git", "show", f"{OFF}:pg/settings/s0_production.yml"], cwd=REPO, capture_output=True,
                         text=True)
    if old.returncode:
        pytest.skip(f"{OFF} not available")
    new, off = yaml.safe_load(_patched_text()), yaml.safe_load(old.stdout)
    # the one difference: wind's price factor explicit null (v3.1: absent; both mean config.yaml's {wind: null}, 0.25 x)
    ic = new["s0_production"]["settings"]["interconnection_headroom"]
    assert ic.pop("price_factors") == {"wind": None}
    assert new["s0_production"].pop("powergenome_commit") is None             # §134: record only
    assert new["s0_production"].pop("state_nuclear_bans") == {"states": []}   # §245: off
    assert new["s0_production"].pop("retirement_checks") == []                # §249, §250: off (no Diablo date)
    br = new["s0_production"]["settings"]["build_rate"]                       # §247: the package at v3.1 (= absent)
    assert {k: br.pop(k) for k in ("ramp_base", "stage_r", "ramp_floor", "handover", "construction_completion",
                                   "tables_dir")} == {"ramp_base": "build", "stage_r": "data",
                                                      "ramp_floor": "placeholder", "handover": "off",
                                                      "construction_completion": "placeholder",
                                                      "tables_dir": "build_rate/outputs"}
    assert new == off


def test_br_twostage_isolation_patch_turns_off_load_hydro_only():
    """§247: the two-stage build-rate test's patch changes one setting, cf_fidelity.load_hydro rescale -> off."""
    p = REPO / "s0_workflow/data/v32/br_twostage_isolation.patch"
    r = subprocess.run(["git", "apply", "--check", str(p)], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    lines = p.read_text().splitlines()
    minus = [l[1:] for l in lines if l.startswith("-") and not l.startswith("---")]
    plus = [l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")]
    assert len(minus) == len(plus) == 1
    text = (REPO / "pg/settings/s0_production.yml").read_text().replace(minus[0] + "\n", plus[0] + "\n")
    now, patched = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml")), yaml.safe_load(text)
    cf_now = now["s0_production"]["time_sampling"]["cf_fidelity"]
    cf_pat = patched["s0_production"]["time_sampling"]["cf_fidelity"]
    assert cf_now.pop("load_hydro") == "rescale" and cf_pat.pop("load_hydro") == "off"
    assert patched == now
