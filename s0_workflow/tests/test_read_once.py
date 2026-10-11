"""Read-once input cache (CHANGES §102; s0_workflow/read_once.py). A five-stage stand-in for the build's per-stage
reads: the load and flexible-demand zips and an 860M-style workbook, each re-read and edited in place every stage, as
PowerGenome does. The written files must be byte-identical with and without the cache. Hand-built fixtures, not
results."""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import read_once as ro  # noqa: E402

STAGES = (2028, 2030, 2035, 2040, 2045)


@pytest.fixture()
def inputs(tmp_path):
    d = tmp_path / "extra_inputs"
    d.mkdir()
    rng = np.random.default_rng(0)
    hours = np.arange(8760 * 2)
    load = pd.DataFrame({"time_index": np.tile(hours, 3), "region": np.repeat(["p1", "p2", "p3"], len(hours)),
                         "load_mw": rng.uniform(100, 900, 3 * len(hours)).round(3)})
    load.to_csv(d / "reeds_2023_loads.csv.zip", index=False)
    dr = pd.DataFrame({"resource": np.repeat(["ev", "heat"], 3 * 24), "year": np.tile(np.repeat(STAGES[:3], 24), 2),
                       "hour": np.tile(np.arange(24), 6), "p1": rng.uniform(0, 5, 144).round(4),
                       "p2": rng.uniform(0, 5, 144).round(4)})
    dr.to_csv(d / "load_adjustments_edf_epri_med.csv.zip", index=False)
    gens = pd.DataFrame({"Generator ID": ["1", "02", "3A", "4"], "Plant ID": [10, 11, 12, 13],
                         "Nameplate Capacity (MW)": [100.0, 250.5, 30.0, 700.0],
                         "Planned Retirement Year": [2030, None, 2041, 2050]})
    with pd.ExcelWriter(d / "july_generator2025.xlsx") as w:
        pd.DataFrame([["title row"]]).to_excel(w, sheet_name="Operating", index=False, header=False, startrow=0)
        gens.to_excel(w, sheet_name="Operating", index=False, startrow=1)
        gens.head(2).to_excel(w, sheet_name="Retired", index=False)
    pd.DataFrame({"a": [1, 2]}).to_csv(d / "not_named.csv", index=False)
    settings = {"regional_load_fn": "reeds_2023_loads.csv.zip", "demand_response_fn": "load_adjustments_edf_epri_med.csv.zip",
                "eia_860m_fn": "july_generator2025.xlsx", "input_folder": str(d)}
    return d, {"c": {y: dict(settings) for y in STAGES}}


def _stage(d: Path, year: int, out: Path):
    """What a stage does with the files: read, edit in place, write (the edits must not leak into later stages)."""
    out.mkdir(parents=True)
    load = pd.read_csv(d / "reeds_2023_loads.csv.zip")                                      # positional path
    load["load_mw"] *= 1 + 0.01 * (year - 2028)                                             # in place
    load.drop(columns=["time_index"], inplace=True)
    load.groupby("region").load_mw.agg(["sum", "max"]).to_csv(out / "loads.csv")
    for typ in ("ev", "heat"):                                                               # once per resource type
        dr = pd.read_csv(filepath_or_buffer=str(d / "load_adjustments_edf_epri_med.csv.zip"))   # keyword path, str
        dr = dr[(dr.resource == typ) & (dr.year == min(year, 2035))]
        dr.loc[:, "p1"] = dr.p1 * 2
        dr.to_csv(out / f"dr_{typ}.csv", index=False)
    gens = pd.read_excel(d / "july_generator2025.xlsx", sheet_name="Operating", header=1, dtype={"Generator ID": str})
    gens = gens[gens["Planned Retirement Year"].isna() | (gens["Planned Retirement Year"] > year)]
    gens.to_csv(out / "gens.csv", index=False)
    sheets = pd.read_excel(d / "july_generator2025.xlsx", sheet_name=None)                  # dict of frames
    sheets["Retired"]["note"] = year
    sheets["Retired"].to_csv(out / "retired.csv", index=False)
    pd.read_csv(d / "not_named.csv").to_csv(out / "other.csv", index=False)                 # not named: not cached


def _build(d, case_settings, root, cached):
    if cached:
        with ro.read_once(ro.input_file_names(case_settings), lambda m: None) as cache:
            for y in STAGES:
                _stage(d, y, root / str(y))
        return cache
    for y in STAGES:
        _stage(d, y, root / str(y))


def test_five_stages_byte_identical_and_read_once(inputs, tmp_path):
    d, cs = inputs
    _build(d, cs, tmp_path / "off", cached=False)
    cache = _build(d, cs, tmp_path / "on", cached=True)
    files = sorted(p.relative_to(tmp_path / "off") for p in (tmp_path / "off").rglob("*.csv"))
    assert len(files) == 5 * 6
    for f in files:
        assert (tmp_path / "on" / f).read_bytes() == (tmp_path / "off" / f).read_bytes(), f
    st = {Path(k).name: v for k, v in cache.stats.items()}
    assert st["reeds_2023_loads.csv.zip"][:2] == [1, 4]                    # parsed once, 4 stages from memory
    assert st["load_adjustments_edf_epri_med.csv.zip"][:2] == [1, 9]       # 2 types x 5 stages = 10 reads
    assert st["july_generator2025.xlsx"][:2] == [2, 8]                      # two distinct calls (sheet / all sheets)
    assert "not_named.csv" not in st
    assert pd.read_csv is ro.pd.read_csv and not hasattr(pd.read_csv, "__wrapped__")   # pandas restored


def test_cached_equals_pandas_and_isolated(inputs):
    d, cs = inputs
    direct = pd.read_excel(d / "july_generator2025.xlsx", sheet_name="Operating", header=1, dtype={"Generator ID": str})
    with ro.read_once({"july_generator2025.xlsx"}, lambda m: None):
        a = pd.read_excel(d / "july_generator2025.xlsx", sheet_name="Operating", header=1, dtype={"Generator ID": str})
        a.iloc[0, 0] = "edited"
        b = pd.read_excel(d / "july_generator2025.xlsx", sheet_name="Operating", header=1, dtype={"Generator ID": str})
    pd.testing.assert_frame_equal(b, direct)                               # values, dtypes, index, columns
    assert b.iloc[0, 0] == "1"


def test_changed_file_is_reread_and_bypasses(inputs):
    d, _ = inputs
    f = d / "reeds_2023_loads.csv.zip"
    with ro.read_once({f.name}, lambda m: None) as cache:
        n1 = len(pd.read_csv(f))
        pd.DataFrame({"time_index": [0], "region": ["p1"], "load_mw": [1.0]}).to_csv(f, index=False)
        os.utime(f, ns=(1, 1))                                             # a different mtime even on coarse clocks
        assert len(pd.read_csv(f)) == 1 and n1 > 1                         # re-read: size and mtime changed
        assert len(list(pd.read_csv(f, chunksize=1))) == 1                 # chunked: straight to pandas
        with open(f, "rb") as h:
            assert len(pd.read_csv(h, compression="zip")) == 1             # a file handle: straight to pandas
        pd.read_csv(f, converters={"load_mw": lambda x: float(x)})       # a callable argument: not cached
    assert list(cache.stats.values())[0][:2] == [2, 0]


def test_off_switch_and_names(inputs, monkeypatch):
    d, cs = inputs
    assert ro.input_file_names({"c": {2028: {"a_fn": "x/y.csv", "nested": {"b_fn": "z.zip"}, "c": "w.csv",
                                             "list": [{"d_fn": "v.parquet"}], "e_fn": None}}}) == {"y.csv", "z.zip",
                                                                                                    "v.parquet"}
    assert {"reeds_2023_loads.csv.zip", "load_adjustments_edf_epri_med.csv.zip",
            "july_generator2025.xlsx"} <= ro.input_file_names(cs)
    monkeypatch.setenv("PG_READ_ONCE", "0")
    with ro.read_once(ro.input_file_names(cs)) as cache:
        assert cache is None and not hasattr(pd.read_csv, "__wrapped__")


def test_pandas_restored_after_an_error(inputs):
    d, cs = inputs
    with pytest.raises(RuntimeError):
        with ro.read_once(ro.input_file_names(cs), lambda m: None):
            assert hasattr(pd.read_csv, "__wrapped__")
            raise RuntimeError
    assert not hasattr(pd.read_csv, "__wrapped__") and not hasattr(pd.read_excel, "__wrapped__")


def test_build_loop_runs_inside_read_once():
    """pg_to_switch.main builds every stage inside one read_once block over the settings' input files."""
    import ast
    tree = ast.parse((REPO / "pg_to_switch.py").read_text())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    withs = [n for n in ast.walk(main) if isinstance(n, ast.With)
             and "read_once" in ast.unparse(n.items[0].context_expr)]
    assert len(withs) == 1
    assert ast.unparse(withs[0].items[0].context_expr) == \
        "s0_read_once.read_once(s0_read_once.input_file_names(case_settings), logger.info)"
    loop = withs[0].body[0]
    assert isinstance(loop, ast.For) and ast.unparse(loop.iter) == "to_run"
    assert any("generator_fuel_and_load_files" in ast.unparse(s) for s in loop.body)
