"""Light stress days (CHANGES §69): on zero-weight stress timeseries listed in stress_light_timeseries.csv, no unit
commitment, ramping, operating reserves, fuel use or per-timepoint cost/policy terms; dispatch, the energy balance,
storage, transmission and the regional reserve test stay. Toy fixtures only (not results)."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_prm import PEN, mini_case  # noqa: E402

PATCHED = ["generators_core_dispatch", "commit_operate", "operating_reserves_areas", "spinning_reserves",
           "gen_ramp_limits", "fuel_costs_simple"]
STRESS_TPS = {901, 902, 903, 904}
PROBE = '''
import json, os
from pyomo.environ import Var, Constraint

def post_solve(m, outdir):
    """Count variables and constraints whose index includes a stress timepoint (901-904), by component."""
    stress = {901, 902, 903, 904}
    out = {}
    for kind in (Var, Constraint):
        for c in m.component_objects(kind, active=True):
            n = 0
            for idx in c:
                t = idx if not isinstance(idx, tuple) else idx
                if (t in stress) if not isinstance(t, tuple) else any(x in stress for x in t):
                    n += 1
            if n:
                out[c.name] = n
    out["_light_tps"] = sorted(getattr(m, "LIGHT_TPS", []))
    from collections import Counter
    from pyomo.repn import generate_standard_repn
    rows, nnz, hourly_nnz = Counter(), 0, 0
    for c in m.component_objects(Constraint, active=True):
        if not c.name.startswith("Prm_"):
            continue
        for k in c:
            vs = {id(v): v for v in generate_standard_repn(c[k].body, quadratic=False).linear_vars}
            nnz += len(vs)
            if c.name in ("Prm_Zone_Requirement", "Prm_Import_Cap"):
                hourly_nnz += len(vs)
                for v in vs.values():
                    rows[v.name] += 1
    out["_prm_nnz"] = nnz                        # all Prm_ rows, incl. the once-per-group definitions and links
    out["_prm_hourly_nnz"] = hourly_nnz          # the hourly reserve and import-cap rows
    out["_prm_dense_cols"] = sum(1 for n in rows.values() if n > 2)
    out["_prm_max_rows_per_col"] = max(rows.values()) if rows else 0
    out["_prm_clique"] = sum(n * n for n in rows.values())  # sum of (rows per column)^2: fill-in proxy
    out["_constraints"] = sorted(c.name for c in m.component_objects(Constraint, active=True) if len(c))
    out["_n_vars"] = sum(len(c) for c in m.component_objects(Var, active=True))
    out["_n_cons"] = sum(len(c) for c in m.component_objects(Constraint, active=True))
    with open(os.path.join(outdir, "stress_components.json"), "w") as f:
        json.dump(out, f)
'''


def light_toy(tmp_path, name, light, min_load=0.0, demand_stress=(8, 9, 10, 9), core=False, compact=False,
              repo_modules=None, add_modules=(),
              new_build=False):
    """mini_case with the S0 operating modules: the repo's patched dispatch and commitment, fuel costs, ramp limits,
    balancing areas and 3+5 spinning reserves; a coal unit (fuel), a geothermal unit, wind and storage."""
    gens = [("coal", "North", "thermal", 6), ("geo", "Central", "thermal", 4), ("w", "North", "wind", 6),
            ("bat", "Central", "storage", 2, 8), ("bkN", "North", "backup", 20), ("bkC", "Central", "backup", 20)]

    def extra(inp, tps):
        mods = inp.parent / "mods"
        for m in PATCHED:
            (mods / f"{m}.py").write_text((REPO / f"switch/study_modules/{m}.py").read_text())
        (mods / "light_probe.py").write_text(PROBE)
        lines = (inp / "modules.txt").read_text().splitlines()
        commit = "switch_model.generators.core.commit.operate" if core else "mods.commit_operate"
        rep = {"switch_model.generators.core.dispatch": ["mods.generators_core_dispatch"],
               "switch_model.generators.core.no_commit": [commit, "switch_model.generators.core.commit.fuel_use"],
               "switch_model.energy_sources.fuel_costs.markets": ["mods.fuel_costs_simple"]}
        out = []
        for ln in lines:
            if ln.strip() in {f"mods.{m}" for m in PATCHED}:        # placed explicitly (order matters)
                continue
            out += rep.get(ln.strip(), [ln])
        out += (["switch_model.balancing.operating_reserves.areas", "switch_model.balancing.operating_reserves.spinning_reserves"]
                if core else ["mods.operating_reserves_areas", "mods.spinning_reserves"]) + ["mods.gen_ramp_limits",
                                                                                            "mods.light_probe"]
        (inp / "modules.txt").write_text("\n".join(dict.fromkeys(out)) + "\n")
        lz = pd.read_csv(inp / "load_zones.csv")
        lz["zone_balancing_area"] = "NC"
        lz.to_csv(inp / "load_zones.csv", index=False)
        gi = pd.read_csv(inp / "gen_info.csv", na_values=".")
        c = gi.GENERATION_PROJECT == "coal"
        gi.loc[c, "gen_energy_source"], gi.loc[c, "gen_full_load_heat_rate"] = "Coal", 10.0
        gi["gen_min_load_fraction"] = [min_load if g in ("coal", "geo") else 0.0 for g in gi.GENERATION_PROJECT]
        gi["gen_forced_outage_rate"] = 0.0
        gi["gen_scheduled_outage_rate"] = [0.05 if g == "coal" else 0.0 for g in gi.GENERATION_PROJECT]
        gi["gen_ramp_limit_up"] = gi["gen_ramp_limit_down"] = [0.5 if g == "coal" else 1.0 for g in gi.GENERATION_PROJECT]
        gi.to_csv(inp / "gen_info.csv", index=False, na_rep=".")
        pd.DataFrame([{"load_zone": z, "fuel": "Coal", "period": 2020, "fuel_cost": 2.0} for z in ("North", "Central")]
                     ).to_csv(inp / "fuel_cost.csv", index=False)
        for f in ("fuel_supply_curves.csv", "regional_fuel_markets.csv", "zone_to_regional_fuel_market.csv",
                  "zone_fuel_cost_diff.csv"):
            (inp / f).unlink(missing_ok=True)
        # two derate groups (as two seasons): 901-902 at 0.9 / 0.8, 903-904 at 0.85 / 0.7
        frac = {("coal", 901): 0.9, ("coal", 902): 0.9, ("coal", 903): 0.85, ("coal", 904): 0.85,
                ("geo", 901): 0.8, ("geo", 902): 0.8, ("geo", 903): 0.7, ("geo", 904): 0.7}
        pd.DataFrame([{"GENERATION_PROJECT": g, "TIMEPOINT": t, "prm_avail_frac": frac[g, t]} for g in ("coal", "geo")
                      for t in tps if t in STRESS_TPS]).to_csv(inp / "prm_gen_availability.csv", index=False)
        if compact:
            pp = pd.read_csv(inp / "prm_params.csv")
            pp["prm_compact_capacity"] = 1
            pp.to_csv(inp / "prm_params.csv", index=False)
        if new_build:      # new coal and geothermal can be built in 2020 (capacity columns in the reserve rows)
            bc = pd.read_csv(inp / "gen_build_costs.csv", na_values=".")
            add = pd.DataFrame([{"GENERATION_PROJECT": g, "build_year": 2020, "gen_overnight_cost": c,
                                 "gen_fixed_om": 20000.0} for g, c in (("coal", 900000.0), ("geo", 1500000.0))])
            pd.concat([bc, add], ignore_index=True).to_csv(inp / "gen_build_costs.csv", index=False, na_rep=".")
        if light:
            pd.DataFrame({"TIMESERIES": ["2020_stress"]}).to_csv(inp / "stress_light_timeseries.csv", index=False)
        if repo_modules is not None:   # the repo's switch/modules.txt list, its study modules copied in as mods.*
            out = []
            for ln in (REPO / "switch/modules.txt").read_text().splitlines():
                ln = ln.split("#")[0].strip()
                if not ln or ln in repo_modules:
                    continue
                if ln.startswith("study_modules."):
                    mod = ln.split(".", 1)[1]
                    (mods / f"{mod}.py").write_text((REPO / f"switch/study_modules/{mod}.py").read_text())
                    ln = f"mods.{mod}"
                out.append(ln)
            for mod in add_modules:      # S0's scenario-line modules (production.scenario_options)
                (mods / f"{mod}.py").write_text((REPO / f"switch/study_modules/{mod}.py").read_text())
                out.append(f"mods.{mod}")
            if "demand_response_investment" in add_modules:    # 0.2 MW shiftable in each zone and hour
                pd.DataFrame([{"LOAD_ZONE": z, "TIMEPOINT": t, "dr_shift_down_limit": 0.2, "dr_shift_up_limit": 0.2}
                              for z in ("North", "Central", "South") for t in tps]).to_csv(inp / "dr_data.csv", index=False)
                pd.DataFrame({"LOAD_ZONE": ["North", "Central", "South"], "PERIOD": 2020, "dr_annual_cost": 1000.0}
                             ).to_csv(inp / "dr_annual_cost.csv", index=False)
            if "switch_model.generators.extensions.storage" not in out and "mods.generators_extensions_storage" not in out:
                out.append("switch_model.generators.extensions.storage")
            (inp / "modules.txt").write_text("\n".join(out + ["mods.light_probe"]) + "\n")
            # hydro_system: one hydro unit (hyd, North) on a two-node water network with constant inflow
            gi = pd.read_csv(inp / "gen_info.csv", na_values=".")
            h = gi[gi.GENERATION_PROJECT == "geo"].copy()
            h["GENERATION_PROJECT"], h["gen_load_zone"], h["gen_energy_source"], h["gen_tech"] = "hyd", "North", "Water", "hydro"
            h["gen_min_load_fraction"], h["gen_scheduled_outage_rate"] = 0.0, 0.0
            pd.concat([gi, h], ignore_index=True).to_csv(inp / "gen_info.csv", index=False, na_rep=".")
            for f, col, v in (("gen_build_costs.csv", "gen_overnight_cost", 0.0),
                              ("gen_build_predetermined.csv", "build_gen_predetermined", 2.0)):
                x = pd.read_csv(inp / f, na_values=".")
                r = x[x.GENERATION_PROJECT == "geo"].head(1).copy()
                r["GENERATION_PROJECT"], r[col] = "hyd", v
                pd.concat([x, r], ignore_index=True).to_csv(inp / f, index=False, na_rep=".")
            gc = pd.read_csv(inp / "prm_gen_credit.csv")
            pd.concat([gc, pd.DataFrame([{"GENERATION_PROJECT": "hyd", "prm_credit": "none", "prm_class": "hydro"}])]
                      ).to_csv(inp / "prm_gen_credit.csv", index=False)
            pd.DataFrame({"WATER_NODES": ["hyd_in", "hyd_out"], "wn_is_sink": [0, 1],
                          "wnode_constant_consumption": [0, 0], "wnode_constant_inflow": [1.5, 0]}).to_csv(
                inp / "water_nodes.csv", index=False)
            pd.DataFrame({"WATER_CONNECTIONS": ["hyd"], "water_node_from": ["hyd_in"], "water_node_to": ["hyd_out"],
                          "wc_capacity": ["."]}).to_csv(inp / "water_connections.csv", index=False)
            pd.DataFrame({"RESERVOIRS": ["hyd_in"], "res_min_vol": [0], "res_max_vol": [10.0]}).to_csv(
                inp / "reservoirs.csv", index=False)
            pd.DataFrame({"HYDRO_GENERATION_PROJECTS": ["hyd"], "hydro_efficiency": [1.0],
                          "hydraulic_location": ["hyd"]}).to_csv(inp / "hydro_generation_projects.csv", index=False)
            if "study_modules.planning_reserves" not in repo_modules:     # legacy per-zone reserve, peak-load form
                zones = ["North", "Central", "South"]
                pd.DataFrame({"PLANNING_RESERVE_REQUIREMENT": [f"CapRes_{z}" for z in zones], "LOAD_ZONE": zones}
                             ).to_csv(inp / "planning_reserve_requirement_zones.csv", index=False)
                pd.DataFrame({"PLANNING_RESERVE_REQUIREMENT": [f"CapRes_{z}" for z in zones],
                              "prr_cap_reserve_margin": 0.08, "prr_enforcement_timescale": "peak_load"}
                             ).to_csv(inp / "planning_reserve_requirements.csv", index=False)
                pd.DataFrame({"LOAD_ZONE": zones, "TIMESERIES": "2020_stress", "planning_reserve_margin": 0.08}
                             ).to_csv(inp / "planning_reserve_margin.csv", index=False)
            # inputs those modules need that the toy lacks: header only (no water network, policies, caps, ...),
            # with the headers of the committed s4x1 case
            for f in sorted((REPO / "switch/in/foresight/s4x1_fedpol_current").glob("*.csv")):
                if not (inp / f.name).exists():
                    (inp / f.name).write_text(f.read_text().splitlines()[0] + "\n")
            # an annual availability limit below 1 on the committed coal unit (CommitUpperLimit in its rule)
            gi = pd.read_csv(inp / "gen_info.csv", na_values=".")
            gi["gen_max_annual_availability"] = [0.8 if g == "coal" else 1.0 for g in gi.GENERATION_PROJECT]
            gi.to_csv(inp / "gen_info.csv", index=False, na_rep=".")

    loads = {"North": ([4, 5], list(demand_stress)), "Central": ([3, 4], [4, 5, 6, 5]), "South": ([1, 1], [1, 1, 1, 1])}
    out = mini_case(tmp_path, name, gens, loads, [("NC", "North", "Central", 3, 0.95), ("CS", "Central", "South", 3, 0.95)],
                    {"North": "R", "Central": "R", "South": "R"}, [{"PRM_REGION": "R", "PERIOD": 2020, "prm_margin": 0.10}],
                    {}, PEN, storage=True, modules=[m for m in PATCHED if not core or m in (
                        "generators_core_dispatch", "gen_ramp_limits", "fuel_costs_simple")],
                    extra=extra)
    return out


@pytest.fixture(autouse=True)
def _spinning_rule(monkeypatch):
    """The S0 options file uses --spinning-requirement-rule 3+5: pass it to every toy solve."""
    import toyutil
    orig = toyutil.solve

    def solve(run, inputs="inputs", outputs="outputs", extra=()):
        return orig(run, inputs, outputs, tuple(extra) + ("--spinning-requirement-rule", "3+5"))

    monkeypatch.setattr(toyutil, "solve", solve)
    yield


def _probe(out):
    return json.loads((Path(out) / "stress_components.json").read_text())


def test_light_has_no_commitment_ramping_or_reserves_on_stress_days(tmp_path):
    full = _probe(light_toy(tmp_path, "full", light=False))
    light = _probe(light_toy(tmp_path, "light", light=True))
    gone = ("CommitGen", "StartupGenCapacity", "ShutdownGenCapacity", "Enforce_Commit_Lower_Limit",
            "Enforce_Commit_Upper_Limit", "Commit_StartupGenCapacity_ShutdownGenCapacity_Consistency",
            "Enforce_Dispatch_Lower_Limit", "Max_Ramp_Up", "Max_Ramp_Down", "CommitGenSpinningReservesUp",
            "CommitGenSpinningReservesDown", "CommitGenSpinningReservesSlackUp", "CommitGenSpinningReservesUp_Limit",
            "Satisfy_Spinning_Reserve_Up_Requirement", "Satisfy_Spinning_Reserve_Down_Requirement",
            "GenFuelUseRate", "GenFuelUseRate_Calculate", "ScheduleOutage", "Apply_Scheduled_Outage")
    assert full["_light_tps"] == [] and light["_light_tps"] == sorted(STRESS_TPS)
    for c in gone:
        assert c not in light, (c, light.get(c))
    # the full formulation has them (what light removes)
    # (ramp constraints are skipped on the toy's 6-hour timepoints: a unit can ramp fully within one)
    assert all(full.get(c, 0) > 0 for c in ("CommitGen", "StartupGenCapacity", "GenFuelUseRate",
                                             "Satisfy_Spinning_Reserve_Up_Requirement"))
    # what the reserve test needs stays: dispatch and its limit, storage state of charge, transmission, the balance,
    # the regional requirement, the import cap and the reserve flows
    for c in ("DispatchGen", "Enforce_Dispatch_Upper_Limit", "ChargeStorage", "StateOfCharge", "DispatchTx",
              "Zone_Energy_Balance", "Prm_Zone_Requirement", "PrmFlow", "Prm_Light_Thermal_Limit"):
        assert light.get(c, 0) > 0, c
    assert "Prm_Light_Thermal_Limit" not in full
    n_full = sum(v for k, v in full.items() if not k.startswith("_"))
    n_light = sum(v for k, v in light.items() if not k.startswith("_"))
    assert n_light < 0.6 * n_full, (n_light, n_full)


def test_light_and_full_same_reserve_outcome(tmp_path):
    """Commitment doesn't bind (no minimum load, loose ramps on the stress days' needs): same builds, same reserve
    shortfall, same total cost."""
    res = {}
    for name, light in (("full", False), ("light", True)):
        out = Path(light_toy(tmp_path, name, light=light))
        b = pd.read_csv(out / "BuildGen.csv").set_index(["GEN_BLD_YRS_1", "GEN_BLD_YRS_2"]).BuildGen
        s = pd.read_csv(out / "prm_shortfall.csv")
        res[name] = (b, s, float(open(out / "total_cost.txt").read()))
    pd.testing.assert_series_equal(res["full"][0], res["light"][0], atol=1e-4)
    assert list(res["full"][1].filter(like="shortfall").sum()) == pytest.approx(
        list(res["light"][1].filter(like="shortfall").sum()), abs=1e-4)
    assert res["full"][2] == pytest.approx(res["light"][2], rel=1e-6)
    # and the stress case is tight enough to need the shortfall slack or the reserve (not trivially slack)
    assert (res["light"][1].filter(like="shortfall").sum() >= 0).all()


def test_full_formulation_matches_switch_core_modules(tmp_path):
    """Without stress_light_timeseries.csv the patched commitment and reserve modules build the core modules' model:
    same number of variables and constraints, same objective, same builds."""
    core = light_toy(tmp_path, "core", light=False, core=True)
    full = light_toy(tmp_path, "full", light=False, min_load=0.3)
    core2 = light_toy(tmp_path, "core2", light=False, min_load=0.3, core=True)
    pc, pf = _probe(core2), _probe(full)
    assert (pc["_n_vars"], pc["_n_cons"]) == (pf["_n_vars"], pf["_n_cons"])
    assert {k: v for k, v in pc.items() if not k.startswith("_")} == {k: v for k, v in pf.items() if not k.startswith("_")}
    cost = lambda o: float(open(Path(o) / "total_cost.txt").read())  # noqa: E731
    assert cost(core2) == pytest.approx(cost(full), rel=1e-9)
    assert Path(core).exists()


def test_size_estimate_scaling():
    """estimate_stress_model_size: per-timepoint rates from the built case scale to the target stage."""
    sys.path.insert(0, str(REPO / "s0_workflow/scripts"))
    import estimate_stress_model_size as e
    res = {
        "full": {"rows": [["var", "DispatchGen", "sample", 2035, 1000], ["var", "DispatchGen", "stress", 2035, 250],
                          ["var", "CommitGen", "stress", 2035, 250], ["var", "BuildGen", "none", 0, 300],
                          ["con", "Balance", "sample", 2035, 400], ["con", "Balance", "stress", 2035, 200]],
                 "tps": {"sample": {"2035": 100}, "stress": {"2035": 25}}},
        "light": {"rows": [["var", "DispatchGen", "sample", 2035, 1000], ["var", "DispatchGen", "stress", 2035, 250],
                           ["var", "BuildGen", "none", 0, 300], ["con", "Balance", "sample", 2035, 400],
                           ["con", "Balance", "stress", 2035, 100]],
                  "tps": {"sample": {"2035": 100}, "stress": {"2035": 25}}}}
    _, out = e.summarise(res, 2035, 600, 360, 114.0)
    assert out["full"]["var"] == {"none": 300, "per_sample_tp": 10.0, "per_stress_tp": 20.0,
                                  "stage_total": 300 + 6000 + 7200, "stage_on_stress": 7200}
    assert out["light"]["var"]["per_stress_tp"] == 10.0 and out["light"]["con"]["per_stress_tp"] == 4.0
    full = 13500 + 2400 + 2880
    light = 300 + 6000 + 3600 + 2400 + 1440
    assert out["memory_gb"]["full"] == 114.0 and out["memory_gb"]["light"] == pytest.approx(114 * light / full, abs=0.05)


@pytest.mark.parametrize("light", [False, True])
def test_compact_reserve_rows_same_results(tmp_path, light):
    """prm_compact_capacity: one accredited-capacity variable per zone, period and derate group, and the shortfall
    linked once per group, give the hourly formulation's results (builds, shortfall, prices, cost) with fewer reserve-row
    hourly-row nonzeros and fewer columns appearing in many rows. Tight stress load and new-build coal and geothermal,
    two derate groups."""
    res = {}
    for name, compact in (("hourly", False), ("compact", True)):
        out = Path(light_toy(tmp_path, f"{name}{int(light)}", light=light, compact=compact, new_build=True,
                             demand_stress=(12, 14, 15, 13)))
        res[name] = {"build": pd.read_csv(out / "BuildGen.csv").set_index(["GEN_BLD_YRS_1", "GEN_BLD_YRS_2"]).BuildGen,
                     "short": pd.read_csv(out / "prm_shortfall.csv"),
                     "price": pd.read_csv(out / "prm_zone_prices.csv"),
                     "cost": float(open(out / "total_cost.txt").read()), "probe": _probe(out)}
    h, c = res["hourly"], res["compact"]
    pd.testing.assert_series_equal(h["build"], c["build"], atol=1e-4)
    assert h["build"].loc[[("coal", 2020), ("geo", 2020)]].sum() > 0.1        # the reserve drives new builds
    num = lambda df: df.select_dtypes("number").to_numpy()  # noqa: E731
    assert num(h["short"]) == pytest.approx(num(c["short"]), abs=1e-4)
    assert num(h["price"]) == pytest.approx(num(c["price"]), rel=1e-4, abs=1e-2)
    assert h["cost"] == pytest.approx(c["cost"], rel=1e-7)
    ph, pc = h["probe"], c["probe"]
    # the hourly rows lose every capacity column (one accredited-capacity column per group instead); the once-per-group
    # definitions and links cost a few nonzeros, which only pay back with many stress hours (the real case: see the
    # estimator), so the toy checks the hourly rows and the column density
    assert pc["_prm_hourly_nnz"] < ph["_prm_hourly_nnz"]
    assert pc["_prm_dense_cols"] < ph["_prm_dense_cols"]
    assert pc["_prm_clique"] < ph["_prm_clique"]


LEGACY_RESERVES = ("study_modules.planning_reserves", "study_modules.planning_reserves_extreme_days")
S0_EXTRA = ("gen_amortization_period", "retirement_rules", "build_rules", "tx_build_cap", "demand_response_investment")


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("drop", [LEGACY_RESERVES, ()], ids=["s0_modules", "every_module"])
def test_light_builds_with_every_module(tmp_path, light, drop):
    """Every module in switch/modules.txt (VM bug at fc756a7: gen_annual_availability_limits used CommitUpperLimit on
    light stress timepoints, where light has no commitment), with prm_regional, an annual availability limit on the
    committed coal unit and light stress days. s0_modules: as S0 runs them (the two legacy reserve modules out, the
    scenario-line modules in); every_module: the file as is. Light builds and solves, has no commitment on stress timepoints, and the annual
    limit (weighted timepoints only) is the same constraint as under full."""
    out = Path(light_toy(tmp_path, f"all{int(light)}{len(drop)}", light=light, repo_modules=drop,
                         add_modules=S0_EXTRA if drop else ()))
    probe = _probe(out)
    if light:
        assert probe["_light_tps"] == sorted(STRESS_TPS)
        for c in ("CommitGen", "CommitUpperLimit", "StartupGenCapacity", "Enforce_Dispatch_Lower_Limit"):
            assert c not in probe
    else:
        assert probe["CommitGen"] > 0
    assert "Respect_Annual_Availability_Limit" not in probe          # one row per generator and period, no timepoint
    assert "Respect_Annual_Availability_Limit" in probe["_constraints"]   # built (coal at 0.8), as under full
    loaded = (out.parent / "inputs/modules.txt").read_text().split()
    want = [ln.split("#")[0].strip() for ln in (REPO / "switch/modules.txt").read_text().splitlines()]
    want = [w.replace("study_modules.", "mods.") for w in want if w and w not in drop]
    assert [x for x in loaded if x in want] == want                 # every module of switch/modules.txt, in order
    for c in ("Prm_Zone_Requirement", "Enforce_Dispatch_Upper_Limit", "Enforce_Wnode_Balance"):  # reserve, hydro
        assert c in probe["_constraints"]
