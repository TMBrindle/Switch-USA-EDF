"""check_vre_profiles.py on the committed fedpol case inputs (switch/in/foresight/s4x1_fedpol_current): a fixture,
not results."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow.scripts import check_vre_profiles as cv  # noqa: E402

CASE = REPO / "switch/in/foresight/s4x1_fedpol_current"


def test_p1_pv_timing_matches_peers_and_no_missing_hours():
    if not (CASE / "variable_capacity_factors.csv").exists():
        pytest.skip("fedpol case inputs not in this checkout")
    out = cv.check(cv.load(CASE, ["p1", "p3", "p8"], "utilitypv"))
    p1 = out[out.GENERATION_PROJECT == "p1_utilitypv_class1_moderate_1"]
    assert set(p1.weather_year) == {2008, 2011, 2012, 2013}
    assert (abs(p1.first_hour - p1.peer_first_hour) <= 1).all() and (abs(p1.last_hour - p1.peer_last_hour) <= 1).all()
    assert (p1.zero_daylight == 0).all()                                    # no daylight hours filled with zeros
    assert (p1.ratio_to_peers < 0.8).all()                                  # but well below the other zones
