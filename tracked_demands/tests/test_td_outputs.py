"""Switch writes every variable to <name>.csv, empty ones as a header only. The on-site tech menu's variables are
dropped when empty (§140, VM test): no menu, no BuildOnsiteTech*.csv or DispatchOnsiteTech.csv; with a menu (LP),
BuildOnsiteTech.csv and DispatchOnsiteTech.csv stay and only the empty MIP sizing files go."""
from __future__ import annotations

import pandas as pd

from td_toy import td_case


def test_no_menu_no_header_only_files(tmp_path):
    run = td_case(tmp_path, "nomenu", onsite=False, storage=False)
    out = run / "outputs"
    for f in ("BuildOnsiteTech", "BuildOnsiteTechFlag", "BuildOnsiteTechUnits", "DispatchOnsiteTech"):
        assert not (out / f"{f}.csv").exists(), f
    assert (out / "tracked_demand_dispatch.csv").exists()


def test_menu_keeps_its_files(tmp_path):
    run = td_case(tmp_path, "menu", storage=False)
    out = run / "outputs"
    assert len(pd.read_csv(out / "BuildOnsiteTech.csv")) > 0 and len(pd.read_csv(out / "DispatchOnsiteTech.csv")) > 0
    assert not (out / "BuildOnsiteTechFlag.csv").exists() and not (out / "BuildOnsiteTechUnits.csv").exists()
