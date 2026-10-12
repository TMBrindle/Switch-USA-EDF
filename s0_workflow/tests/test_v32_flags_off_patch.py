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
    assert len(minus) == len(plus) == 8                      # §132: + pv_profile_repair
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
    assert new == off
