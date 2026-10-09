"""Tests on the real (pinned) inputs and the committed outputs. The raw-data tests skip when data/raw is absent."""
import numpy as np
import pandas as pd
import pytest
from conftest import PKG, needs_raw

from gasnet import aeo, eia, fetch, load_config, network, tracing

OUT = PKG / "outputs"


def test_committed_zone_shares_sum_to_one_and_complete():
    z = pd.read_csv(OUT / "zone_basin_shares.csv")
    cfg = load_config()
    s = z.groupby(["zone", "year"]).share.sum()
    np.testing.assert_allclose(s.values, 1.0, atol=1e-5)          # 6-decimal rounding in the file
    assert z.zone.nunique() == 134
    assert sorted(z.year.unique()) == [cfg["base_year"]] + cfg["model_years"]
    assert set(z.basin) == set(cfg["basins"])
    assert (z.share >= -1e-9).all()
    assert list(z.columns) == ["zone", "year", "basin", "share", "method", "source_vintage"]


def test_basin_intensity_template_empty():
    b = pd.read_csv(PKG / "data/basin_intensity.csv")
    assert list(b.columns) == ["basin", "year", "kg_ch4_per_mmbtu", "gwp_basis", "source"]
    assert b.kg_ch4_per_mmbtu.isna().all()


# scratch run reported to Tom in the Step 1 note (state-level basins, Texas and NM unsplit), % of hub gas
SCRATCH = {"FL": {"Appalachia": 34.6, "LA": 27.0, "TX": 21.7, "MidCon": 8.0, "GOM": 4.8, "Other": 2.4},
           "VA": {"Appalachia": 99.9},
           "MA": {"Appalachia": 81.8, "Imports": 18.2},
           "CA": {"NM": 34.0, "Imports": 28.6, "Rockies": 26.9, "Other": 5.7, "TX": 3.8, "MidCon": 0.9}}
LEGACY = {"PA": "Appalachia", "WV": "Appalachia", "OH": "Appalachia", "NY": "Appalachia", "KY": "Appalachia",
          "VA": "Appalachia", "LA": "LA", "TX": "TX", "NM": "NM", "OK": "MidCon", "KS": "MidCon", "AR": "MidCon",
          "WY": "Rockies", "CO": "Rockies", "UT": "Rockies", "ND": "Bakken", "MT": "Bakken", "GOM": "GOM"}


@needs_raw
def test_reproduces_2024_scratch_numbers():
    """The Step 1 scratch tracing (Texas one node, basins by state) from the same pinned EIA files."""
    raw = PKG / "data/raw/eia"
    dry = eia.dry_production(raw / "NG_PROD_SUM_A_EPG0_FPD_MMCF_A.xls", 2024).drop(["AK", "HI"], errors="ignore")
    flows, intl = eia.movements(raw / "movements", fetch.STATES, 2024)
    sup = [(st, LEGACY.get(st, "Other"), v) for st, v in dry.items()]
    sup += [(r.state, "Imports", r.mmcf, r.state == "AZ") for r in intl.itertuples()]
    sup = pd.DataFrame([x if len(x) == 4 else x + (False,) for x in sup], columns=["hub", "basin", "value", "az_mx"])
    fl = flows.rename(columns={"mmcf": "value"})
    nodes = sorted(set(fetch.STATES) | {"GOM"})
    # the scratch parser missed two items the package reads: Mississippi's 133,568 MMcf "Receipts from Federal
    # Offshore" (it matched only "From") and Arizona's 1,594 MMcf Mexican imports (column "Imports + Intransit From
    # ..."). Dropping both reproduces the scratch numbers; keeping them gives the corrected values.
    gom_ms = (fl.src == "GOM") & (fl.dst == "MS")
    assert fl[gom_ms].value.sum() == pytest.approx(133568)
    assert sup[sup.az_mx].value.sum() == pytest.approx(1594)
    t = tracing.trace(sup[~sup.az_mx], fl[~gom_ms], nodes, sorted(set(sup.basin)))
    for hub, exp in SCRATCH.items():
        got = t[t.hub == hub].set_index("basin").share * 100
        for b, v in exp.items():
            assert got[b] == pytest.approx(v, abs=0.051), (hub, b)
    t = tracing.trace(sup, fl, nodes, sorted(set(sup.basin)))
    got = t[t.hub == "FL"].set_index("basin").share * 100
    assert got["Appalachia"] == pytest.approx(34.1, abs=0.051) and got["GOM"] == pytest.approx(7.1, abs=0.051)


@needs_raw
def test_sources_pinned():
    assert fetch.verify(*fetch.default_paths()) == []


@needs_raw
def test_texas_and_nm_splits():
    cfg = load_config()
    inp = network.Inputs(cfg)
    sh = network._area_shares(inp).set_index("area").share_of_state
    assert sh[[a for a in sh.index if a.startswith("TX_")]].sum() == pytest.approx(1.0)
    # proved-reserves report Table 8 (2024 estimated production, Bcf): NM East 3,079, West 506
    assert sh["NM_E"] == pytest.approx(3079 / 3585)
    assert sh["TX_D8"] == pytest.approx(5116 / 12724)
    base = network.base_network(inp)
    s = base["supply"]
    tx = s[s.hub.isin(["TX_E", "TX_N", "TX_W"]) & (s.kind == "production")].value.sum()
    assert tx == pytest.approx(inp.dry["TX"])                       # the split conserves Texas dry production


@needs_raw
def test_table64_parse_and_anomaly():
    inp = network.Inputs(load_config())
    t = inp.t64
    assert t.code.nunique() == 35 and inp.aeo_datekey == "d021826b"
    a = t[(t.src == "RockiesPlains") & (t.dst == "OR_WA")].set_index("year").bcf
    b = t[(t.src == "Canada@WA") & (t.dst == "OR_WA")].set_index("year").bcf
    # the published anomaly reported in the guide: these two rows are identical in every year
    assert (a == b).all()


@needs_raw
def test_fresh_run_matches_committed(tmp_path):
    from gasnet import cli
    cfg = load_config()
    cfg["paths"]["outputs"] = tmp_path
    cli.run(cfg, log=lambda *a: None)
    new = pd.read_csv(tmp_path / "zone_basin_shares.csv")
    old = pd.read_csv(OUT / "zone_basin_shares.csv")
    m = old.merge(new, on=["zone", "year", "basin"], suffixes=("_old", "_new"))
    assert len(m) == len(old) == len(new)
    np.testing.assert_allclose(m.share_old, m.share_new, atol=2e-6)
