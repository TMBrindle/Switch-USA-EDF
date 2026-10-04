"""Forced transmission for the S0 new-defaults cases (CHANGES §54): the ReEDS release's certain additions
(forced_tx: reeds_certain) and forced lines limited to their minimum (forced_tx_expansion_limit: minimum), in
pg_to_switch.transmission_tables; other cases unchanged."""
import ast
import logging
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import production as s0prod  # noqa: E402

TX = REPO / "pg/extra_inputs/transmission"
REGIONS = list(pd.read_csv(REPO / "hierarchy.csv").ba)


def _fns(path, names, g):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(nodes, []), str(path), "exec"), g)


def transmission_tables(source=None):
    """pg_to_switch.transmission_tables as written, with conversion_functions' helpers; PowerGenome's
    agg_transmission_constraints (existing capacity from its database, VM-only) and network_max_reinforcement
    stood in for."""
    g = {"pd": pd, "np": np, "Path": Path, "logger": logging.getLogger("t"), "__file__": str(REPO / "pg_to_switch.py"),
         "s0prod": s0prod}
    _fns(REPO / "conversion_functions.py", ["first_value", "load_zones_table", "tx_cost_transform"], g)
    _fns(source or REPO / "pg_to_switch.py", ["transmission_tables"], g)
    nc = pd.read_csv(TX / "network_costs_ReEDS.csv")

    def agg_transmission_constraints(pg_engine, settings):
        return pd.DataFrame({"transmission_path_name": nc.start_region + "_to_" + nc.dest_region,
                             "Line_Max_Flow_MW": 1000.0})

    def network_max_reinforcement(transmission, settings):          # PowerGenome GenX, fraction form
        transmission["Line_Max_Reinforcement_MW"] = (transmission["Line_Max_Flow_MW"]
                                                     * settings.get("tx_expansion_per_period", 0)).round(0)
        return transmission
    g.update(agg_transmission_constraints=agg_transmission_constraints,
             network_max_reinforcement=network_max_reinforcement)
    return g["transmission_tables"]


def build(tmp_path, years=(2028, 2030, 2035), source=None, **kw):
    s = {"model_regions": REGIONS, "input_folder": REPO / "pg/extra_inputs",
         "user_transmission_costs": "transmission/network_costs_ReEDS.csv", "transmission_policy": "constrained",
         "trans_expansion_policy": "zero", "build_minimum_policy": "yes", "degrade_policy": "no",
         "hurdle_policy": "no", "asymmetry_policy": "no", "tx_expansion_per_period": 0, **kw}
    out = tmp_path / f"run{len(list(tmp_path.iterdir()))}"
    out.mkdir(parents=True)
    transmission_tables(source)({y: dict(s) for y in years}, out, None)
    if source is not None:
        return out
    rd = lambda f: pd.read_csv(out / f) if (out / f).exists() else None  # noqa: E731
    lines = rd("transmission_lines.csv")
    name = {r.TRANSMISSION_LINE: "-".join(sorted([r.trans_lz1, r.trans_lz2], key=lambda z: int(z[1:])))
            for r in lines.itertuples()}
    return lines, rd("trans_build_minimum.csv"), rd("trans_path_expansion_limit.csv"), name


def test_reeds_certain_table_and_comparison():
    """The committed table is what the script builds from the pinned ReEDS file (zones of the committed table when the
    geo libraries are missing); two certain lines, none already in the 2024 capacity."""
    r = subprocess.run([sys.executable, str(REPO / "s0_workflow/scripts/build_reeds_forced_tx.py"), "--check"],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    src = pd.read_csv(TX / "reeds_2026.09.21/hvdc_planned-baseline.csv")
    assert set(src.name[src.certain == 1]) == {"sunzia", "transwestexpress"}
    t = pd.read_csv(TX / "forced_tx_reeds_certain_2026.09.21.csv").set_index("project_name")
    assert t.loc["sunzia", ["from_zone", "to_zone", "new_cap_mw", "new_cap_year", "period"]].tolist() == \
        ["p28", "p31", 3000.0, 2026, 2028]
    assert t.loc["transwestexpress", ["from_zone", "to_zone", "new_cap_mw", "new_cap_year", "period"]].tolist() == \
        ["p24", "p25", 3000.0, 2032, 2035]
    nonac = pd.read_csv(TX / "transmission_capacity_init_nonAC_ba.csv")
    assert not {frozenset(p) for p in zip(nonac.r, nonac.rr)} & {frozenset(p) for p in zip(t.from_zone, t.to_zone)}
    c = pd.read_csv(TX / "forced_tx_comparison_2026.09.21.csv").set_index(["option", "period"])
    assert c.loc[("named_projects", "all"), "MW"] == 245857.0 and c.loc[("named_projects", "all"), "lines"] == 72
    assert c.loc[("reeds_certain", "all"), "MW"] == 6000.0
    assert c.loc[("reeds_certain", "all"), "interregional_share"] == 0.5          # TransWest: WestConnect-NorthernGrid
    assert list(c.loc["named_projects"].MW[["2028", "2030", "2035"]]) == [36976.0, 53692.0, 155189.0]


def test_settings_options_and_legacy():
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["forced_tx"] == "reeds_certain" and s0["forced_tx_expansion_limit"] == "minimum"
    ax = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]
    assert ax["forced_tx"] == {"reeds_certain": {"s0_production": {"forced_tx": "reeds_certain"}},
                               "named_projects": {"s0_production": {"forced_tx": "named_projects"}}, "legacy": None}
    leg = ax["s0_production"]["on_pgdays"]["s0_production"]
    assert leg["forced_tx"] == "named_projects" and leg["forced_tx_expansion_limit"] == "legacy"
    for opt, table in (("reeds_certain", "pg/extra_inputs/transmission/forced_tx_reeds_certain_2026.09.21.csv"),
                       ("named_projects", None)):
        s = {}
        s0prod.apply_forced_tx(s, dict(s0, forced_tx=opt))
        assert s.get("forced_tx_table") == table and s["forced_tx_expansion_limit"] == "minimum"
    s = {}
    s0prod.apply_forced_tx(s, dict(s0, **leg))
    assert s == {}                                                         # legacy: pg_to_switch as before
    with pytest.raises(ValueError, match="forced_tx"):
        s0prod.apply_forced_tx({}, dict(s0, forced_tx="all"))
    # the 2035 comparison pair differs only in forced_tx
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    a = si[si.case_id == "s4x1_S0prod_2035_txreeds"].iloc[0]
    b = si[si.case_id == "s4x1_S0prod_2035_txnamed"].iloc[0]
    assert [c for c in si.columns if a[c] != b[c]] == ["case_id", "forced_tx"] and a.year == 2035
    assert a.forced_tx == "reeds_certain" and b.forced_tx == "named_projects" and a.s0_production == "on_pgdays_new"
    new = si[si.case_id == "s4x1_S0prod_2035_new"].iloc[0]
    assert [c for c in si.columns if a[c] != new[c]] == ["case_id"]
    assert set(si.loc[si.case_id == "s4x1_S0prod_2035", "forced_tx"]) == {"legacy"}
    rest = si[~si.case_id.isin(["s4x1_S0prod_2035", "s4x1_S0prod_2035_txnamed"])]
    assert (rest.forced_tx == "reeds_certain").all()                  # inert where s0_production is off


def test_transmission_tables_forced_lines(tmp_path):
    """Legacy: the named projects are forced and left out of trans_path_expansion_limit.csv (unlimited in Switch).
    S0: reeds_certain forces SunZia (2028) and TransWest Express (2035) only, and with the minimum limit each forced
    line is limited to its minimum in its forced period and to the case's rule (0 here) otherwise; under nerc_growth
    and unlimited too."""
    lines, bm, lim, name = build(tmp_path)
    forced = {name[x] for x in bm.TRANSMISSION_LINE}
    assert len(bm) == 72 and {"p60-p61", "p15-p16"} <= forced           # the 72 named projects
    assert not set(lim.TRANSMISSION_LINE) & set(bm.TRANSMISSION_LINE)  # the gap: no limit on forced lines
    for pol in ("zero", "nerc_growth", "unlimited"):
        lines, bm, lim, name = build(tmp_path, trans_expansion_policy=pol,
                                     forced_tx_table="pg/extra_inputs/transmission/forced_tx_reeds_certain_2026.09.21.csv",
                                     forced_tx_expansion_limit="minimum")
        b = {(name[r.TRANSMISSION_LINE], r.PERIOD): r.trans_build_minimum_mw for r in bm.itertuples()}
        assert b == {("p28-p31", 2028): 3000.0, ("p24-p25", 2035): 3000.0}, pol
        L = {(name[r.TRANSMISSION_LINE], r.PERIOD): r.trans_path_expansion_limit_mw for r in lim.itertuples()}
        assert L[("p28-p31", 2028)] == 3000.0 and L[("p24-p25", 2035)] == 3000.0, pol
        if pol == "unlimited":
            assert len(lim) == 2                                           # only the forced periods are limited
        elif pol == "zero":
            assert L[("p28-p31", 2030)] == 0 and L[("p28-p31", 2035)] == 0 and L[("p24-p25", 2028)] == 0
        else:                                                              # nerc_growth: its rule, as other lines
            assert all(np.isfinite(L[(ln, y)]) for ln in ("p28-p31", "p24-p25") for y in (2028, 2030, 2035))
            assert L[("p28-p31", 2030)] > 0
        if pol != "unlimited":
            assert len(lim) == lim[["TRANSMISSION_LINE", "PERIOD"]].drop_duplicates().shape[0]
        # the named projects are no longer forced; their cross-transreg corridors are blocked like any other
        nb = {name[r.TRANSMISSION_LINE] for r in lines.itertuples() if r.trans_new_build_allowed == 0}
        assert "p37-p38" in nb and "p24-p25" not in nb
    # named_projects with the minimum limit: the same 72 forced lines, each limited to its minimum
    lines, bm, lim, name = build(tmp_path, forced_tx_expansion_limit="minimum")
    m = bm.merge(lim, on=["TRANSMISSION_LINE", "PERIOD"])
    assert len(bm) == 72 and len(m) == 72 and (m.trans_path_expansion_limit_mw == m.trans_build_minimum_mw).all()


def test_legacy_transmission_files_unchanged(tmp_path):
    """Without the S0 keys (the regression case and every other case) transmission_tables writes the same files as
    before this change (4f882b6), under each expansion policy."""
    old = tmp_path / "old_pg_to_switch.py"
    old.write_text(subprocess.run(["git", "show", "4f882b6:pg_to_switch.py"], cwd=REPO, capture_output=True,
                                  text=True, check=True).stdout)
    for pol in ("zero", "nerc_growth", "unlimited"):
        for tr in ("constrained", "unconstrained"):
            a = build(tmp_path, source=old, trans_expansion_policy=pol, transmission_policy=tr)
            b = build(tmp_path, source=REPO / "pg_to_switch.py", trans_expansion_policy=pol, transmission_policy=tr)
            files = sorted(f.name for f in a.glob("*.csv"))
            assert files == sorted(f.name for f in b.glob("*.csv")) and "transmission_lines.csv" in files
            for f in files:
                assert (a / f).read_bytes() == (b / f).read_bytes(), (pol, tr, f)


def test_chained_stages_force_each_line_once(tmp_path):
    """S0 chains (CHANGES §57): with _chain_years each stage forces only the lines whose period it models, so over a
    mode-A chain every forced line is forced in exactly one stage, at the single-stage period (reeds_certain and
    named_projects); in mode B each line is forced only in the windows holding its period."""
    chain = [2028, 2030, 2035, 2040, 2045]
    for table in ("pg/extra_inputs/transmission/forced_tx_reeds_certain_2026.09.21.csv", None):
        kw = {"forced_tx_table": table} if table else {}
        _, full, _, name_full = build(tmp_path, years=chain, forced_tx_expansion_limit="minimum", **kw)
        want = sorted((name_full[r.TRANSMISSION_LINE], r.PERIOD, r.trans_build_minimum_mw) for r in full.itertuples())
        got = []
        for st in s0prod.plan_stages(chain, "myopic"):
            _, bm, lim, name = build(tmp_path, years=st["years"], _chain_years=chain, forced_tx_expansion_limit="minimum",
                                     **kw)
            if bm is not None:
                got += [(name[r.TRANSMISSION_LINE], r.PERIOD, r.trans_build_minimum_mw) for r in bm.itertuples()]
                L = {(name[r.TRANSMISSION_LINE], r.PERIOD): r.trans_path_expansion_limit_mw for r in lim.itertuples()}
                assert all(L[(n, p)] == mw for n, p, mw in got if p in st["years"])       # capped at the minimum
        assert sorted(got) == want and len(got) == len(set((n, p) for n, p, _ in got))
        if table:
            assert sorted(got) == [("p24-p25", 2035, 3000.0), ("p28-p31", 2028, 3000.0)]
    # mode B: TransWest (2035) in the windows 2030-35 and 2035-40 only; SunZia (2028) in the first window only
    table = "pg/extra_inputs/transmission/forced_tx_reeds_certain_2026.09.21.csv"
    per_window = {}
    for st in s0prod.plan_stages(chain, "windows", 2):
        _, bm, _, name = build(tmp_path, years=st["years"], _chain_years=chain, forced_tx_table=table)
        per_window[st["name"]] = [] if bm is None else sorted((name[r.TRANSMISSION_LINE], r.PERIOD) for r in bm.itertuples())
    assert per_window == {"2028_2030": [("p28-p31", 2028)], "2030_2035": [("p24-p25", 2035)],
                          "2035_2040": [("p24-p25", 2035)], "2040_2045": []}
    # outside its forced period a forced line gets the policy's normal limit (zero: 0; unlimited: no row)
    for pol, want in (("zero", 0.0), ("unlimited", None)):
        for y in (2030, 2040):
            _, bm, lim, name = build(tmp_path, years=[y], _chain_years=chain, forced_tx_table=table,
                                     trans_expansion_policy=pol, forced_tx_expansion_limit="minimum")
            L = {} if lim is None else {(name[r.TRANSMISSION_LINE], r.PERIOD): r.trans_path_expansion_limit_mw
                                        for r in lim.itertuples()}
            assert bm is None and L.get(("p28-p31", y)) == want and L.get(("p24-p25", y)) == want, (pol, y)
    # the bug this fixes: without _chain_years a 2040 stage re-forces both lines at 2040
    _, bm, _, name = build(tmp_path, years=[2040], forced_tx_table=table)
    assert sorted(name[x] for x in bm.TRANSMISSION_LINE) == ["p24-p25", "p28-p31"]


def test_fix_aliases_script_matches_fixed_build(tmp_path):
    """s0_workflow/scripts/fix_forced_tx_aliases.py on a chain built before §57 (every stage re-forces earlier lines)
    writes, for every stage, exactly the trans_build_minimum / trans_path_expansion_limit files a fresh build under
    the fix writes (byte-compared), or a `=none` alias where the fixed build writes no file; and adds the aliases
    to the scenario lines."""
    import importlib.util
    import shutil
    spec = importlib.util.spec_from_file_location("fixal", REPO / "s0_workflow/scripts/fix_forced_tx_aliases.py")
    fixal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixal)
    chain = [2028, 2030, 2035, 2040, 2045]
    reeds = "pg/extra_inputs/transmission/forced_tx_reeds_certain_2026.09.21.csv"
    combos = [("reeds_certain", pol, mode) for pol in ("zero", "nerc_growth", "unlimited") for mode in ("myopic", "windows")]
    combos += [("named_projects", "zero", "myopic"), ("named_projects", "zero", "windows")]
    (tmp_path / "b").mkdir()
    for opt, pol, mode in combos:
        kw = dict(trans_expansion_policy=pol, forced_tx_expansion_limit="minimum")
        if opt == "reeds_certain":
            kw["forced_tx_table"] = reeds
        root = tmp_path / f"{opt}_{pol}_{mode}"
        stages = s0prod.plan_stages(chain, mode, 2)
        lines = []
        for st in stages:
            old = build(tmp_path / "b", years=st["years"], source=REPO / "pg_to_switch.py", **kw)    # pre-§57 stage
            d = root / st["name"] / "C"
            shutil.copytree(old, d)
            pd.DataFrame({"INVESTMENT_PERIOD": st["years"]}).to_csv(d / "periods.csv", index=False)
            s0prod.write_stage_info(d, st)
            lines.append(f"--scenario-name C_{st['name']} --inputs-dir {st['name']}/C --outputs-dir out/{st['name']}/C"
                         + (" --input-aliases gen_build_costs.csv=gen_build_costs.chained.C.csv" if lines else ""))
        (root / "scenarios_C.txt").write_text("\n".join(lines) + "\n")
        assert fixal.main([str(root), "--case", "C", "--forced-tx", opt, "--trans-expansion", pol]) == 0
        al = pd.read_csv(root / "forced_tx_aliases.C.csv", dtype={"stage": str}).set_index("stage")
        fixed_lines = (root / "scenarios_C.fixed.txt").read_text().splitlines()
        for st, ln in zip(stages, fixed_lines):
            new = build(tmp_path / "b", years=st["years"], source=REPO / "pg_to_switch.py", _chain_years=chain, **kw)
            d = root / st["name"] / "C"
            for f in ("trans_build_minimum", "trans_path_expansion_limit"):
                if (new / f"{f}.csv").exists():
                    assert (d / f"{f}.fixed.csv").read_bytes() == (new / f"{f}.csv").read_bytes(), (opt, pol, mode, st, f)
                    pair = f"{f}.csv={f}.fixed.csv"
                else:
                    assert not (d / f"{f}.fixed.csv").exists(), (opt, pol, mode, st, f)
                    pair = f"{f}.csv=none"
                assert pair in al.at[st["name"], "aliases"].split() and pair in ln.split(), (st, ln)
            assert ("gen_build_costs.csv=gen_build_costs.chained.C.csv" in ln) == (st is not stages[0])
            assert ln.count("--input-aliases") == 1
        rep = pd.read_csv(root / "forced_tx_fix_report.C.csv")
        if opt == "reeds_certain" and mode == "myopic":
            # SunZia re-forced in 2030-2045, TransWest in 2040 and 2045: 6 rows removed; 2 kept
            assert (rep.action == "kept").sum() == 2 and (rep.action != "kept").sum() == 6, rep
        # existing outputs are never replaced without --overwrite
        with pytest.raises(SystemExit, match="exists"):
            fixal.main([str(root), "--case", "C", "--forced-tx", opt, "--trans-expansion", pol])
    # a wrong forced list is caught before anything is written
    root = tmp_path / "reeds_certain_zero_myopic"
    with pytest.raises(SystemExit, match="not in the built"):
        fixal.main([str(root), "--case", "C", "--forced-tx", "named_projects", "--overwrite"])
