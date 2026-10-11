"""Stage 2a (§103): capacity status rule, the annual LP, arc error, and the real-data run."""
import numpy as np
import pandas as pd
import pytest
from conftest import PKG, needs_raw

from gasnet import capacity, stage2a

OUT = PKG / "outputs" / "stage2a"


def _toy_ref(tmp_path, rows):
    pd.DataFrame(rows, columns=["project", "status", "in_service_year", "mmcfd", "src", "dst", "in_base_2025",
                                "note"]).to_csv(tmp_path / "capacity_projects.csv", index=False)
    return tmp_path


def test_status_rule(tmp_path, monkeypatch):
    base = pd.DataFrame({"src": ["A"], "dst": ["B"], "mmcfd": [100.0]})
    monkeypatch.setattr(capacity, "eia_base", lambda raw, m, year=2025: (base.copy(), []))
    monkeypatch.setattr(capacity, "ngmm_texas", lambda raw, year=2024: base.iloc[0:0])
    ref = _toy_ref(tmp_path, [
        ("built", "Construction", 2030, 120, "A", "B", "no", ""),
        ("maybe", "Approved", 2030, 60, "A", "B", "no", ""),
        ("dead", "On Hold", 2027, 999, "A", "B", "no", ""),
        ("old", "Completed", 2025, 30, "A", "B", "yes", "")])   # in the end-2025 base already
    cap = lambda y, a=False: float(capacity.capacity(None, ref, {}, y, a)[0].mmcfd.sum())
    assert cap(2029) == pytest.approx(100)                       # nothing new before the in-service year
    assert cap(2030) == pytest.approx(100 + 120 * 2 / 12)         # November-December of the in-service year
    assert cap(2031) == pytest.approx(220)
    assert cap(2031, True) == pytest.approx(280)                  # approved only as the sensitivity
    assert cap(2024) == pytest.approx(70)                         # 2025 completion removed from the 2024 run
    assert cap(2025) == pytest.approx(100 - 30 * 10 / 12)         # ... and counts only from November 2025


XY = pd.DataFrame({"lat": [30.0, 30.0, 30.0], "lon": [-100.0, -95.0, -90.0]}, index=["A", "B", "C"])


def test_lp_known_answer_and_binding():
    sup = pd.DataFrame({"hub": ["A"], "basin": ["x"], "value": [10.0]})
    dem = pd.Series({"B": 4.0, "C": 6.0})
    cap = pd.DataFrame({"src": ["A", "B", "A"], "dst": ["B", "C", "C"], "mmcfd": [100.0, 100.0, 3.0 / 1.0]})
    r = stage2a.solve(["A", "B", "C"], XY, sup, dem, cap, 1, 1.0, 0.0)
    f = r["flows"].set_index(["src", "dst"]).flow
    # direct A->C (shorter) takes its full 3, the rest goes A->B->C
    assert f[("A", "C")] == pytest.approx(3.0)
    assert f[("B", "C")] == pytest.approx(3.0) and f[("A", "B")] == pytest.approx(7.0)
    assert r["unserved"].sum() == pytest.approx(0) and r["spill"].sum() == pytest.approx(0)
    assert (r["flows"].set_index(["src", "dst"]).utilisation[("A", "C")]) == pytest.approx(1.0)


def test_lp_unserved_and_min_expansion():
    sup = pd.DataFrame({"hub": ["A"], "basin": ["x"], "value": [10.0]})
    dem = pd.Series({"C": 10.0})
    cap = pd.DataFrame({"src": ["A"], "dst": ["C"], "mmcfd": [4.0]})
    r = stage2a.solve(["A", "B", "C"], XY, sup, dem, cap, 1, 1.0, 0.0)
    assert r["unserved"]["C"] == pytest.approx(6.0) and r["spill"]["A"] == pytest.approx(6.0)
    r = stage2a.solve(["A", "B", "C"], XY, sup, dem, cap, 1, 1.0, 0.0, expand=True)
    assert r["unserved"].sum() == pytest.approx(0)
    assert r["flows"].expansion.sum() == pytest.approx(6.0)


def test_arc_error():
    model = pd.DataFrame({"src": ["A", "B"], "dst": ["B", "A"], "flow": [5.0, 0.0]})
    obs = pd.DataFrame({"src": ["A", "B"], "dst": ["B", "A"], "value": [8.0, 3.0]})
    e = stage2a.arc_error(model, obs)
    assert e["weighted_abs_error"] == pytest.approx(6 / 11)
    assert e["weighted_abs_error_net"] == pytest.approx(0.0)      # same net A->B flow (5)


@needs_raw
def test_stage2a_outputs():
    s = pd.read_csv(OUT / "summary.csv")
    base = s[(s.year == 2024) & (s.variant == "status_rule")].iloc[0]
    assert base.unserved_bcf < 0.001 * base.supply_tcf * 1000      # 2024 calibrated: all served (0.1% tolerance)
    assert base.util == 0.9
    g = pd.read_csv(OUT / "calibration_grid.csv")
    assert len(g) == len(stage2a.UTIL_GRID) * len(stage2a.HURDLE_GRID)
    h = pd.read_csv(OUT / "hub_basin_shares.csv")
    sums = h.dropna(subset=["share"]).groupby(["variant", "year", "hub"]).share.sum()
    np.testing.assert_allclose(sums.values, 1.0, atol=1e-5)
    z = pd.read_csv(OUT / "zone_shares_vs_stage1.csv")
    va = z[(z.zones.str.startswith("VA")) & (z.year == 2024) & (z.variant == "status_rule")].set_index("basin")
    assert va.loc["appalachia", "stage2a"] > 0.99
    ex = s[(s.variant == "status_rule_min_expansion") & (s.year == 2045)].iloc[0]
    assert ex.unserved_bcf < 10                                      # only Vermont (no arc) left


@needs_raw
def test_demand_2024_matches_eia():
    from gasnet import load_config, network
    inp = network.Inputs(load_config())
    bd = stage2a.base_demand(inp)
    cons = bd[~bd.use.str.startswith("exports")].mmcf.sum()
    assert cons / 1e6 == pytest.approx(32.515, abs=0.01)             # EIA 2024 total consumption, 49 files


@needs_raw
def test_nems_capacity_vs_stage2a():
    from gasnet import nems_capacity
    d = pd.read_csv(OUT / "nems_capacity_vs_stage2a.csv").set_index(["year", "src", "dst"])
    a = pd.read_csv(OUT / "nems_ngmm_capacity_d011626a.csv")
    m = a.dropna(subset=["t64_bcf"])
    m = m[(m.year >= 2025) & (m.t64_bcf.abs() > 50)]
    assert (m.flow_bcf - m.t64_bcf).abs().sum() / m.t64_bcf.abs().sum() < 0.03   # arcs identified (run d011626a vs d021826b)
    k = (2045, "MidAtlantic_OH", "EasternMidwest")
    assert d.loc[k, "ngmm_added_bcfd"] > 20 and 5 < d.loc[k, "stage2a_min_expansion_bcfd"] < d.loc[k, "ngmm_added_bcfd"]
    assert nems_capacity.DEST[2] == "EasternMidwest"
