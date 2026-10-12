"""§128 (V32-04): the PowerGenome submodule is pinned to c4c433c, the first commit with the regional capex table that
pg/settings/resources.yml names (cost_multiplier_fn), so a fresh clone with `git submodule update` can build S0."""
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
PIN = "c4c433cfefd0ae11f5b3f47ae05e3b9d7c90a691"


def test_powergenome_gitlink_is_c4c433c():
    out = subprocess.run(["git", "ls-files", "-s", "PowerGenome"], cwd=REPO, capture_output=True, text=True).stdout
    if not out:
        pytest.skip("not a git checkout")
    mode, sha = out.split()[:2]
    assert mode == "160000" and sha == PIN


def test_cost_multiplier_file_in_checked_out_submodule():
    """Where the submodule is checked out at the pin (the runner), the named file is there."""
    fn = yaml.safe_load(open(REPO / "pg/settings/resources.yml"))["cost_multiplier_fn"]
    assert fn == "AEO_2026_regional_cost_corrections.csv"
    head = subprocess.run(["git", "-C", "PowerGenome", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True)
    if head.returncode or head.stdout.strip() != PIN:
        pytest.skip("PowerGenome not checked out at the pin here (run `git submodule update`)")
    assert (REPO / "PowerGenome/data/cost_multipliers" / fn).exists()
