"""Reusing leading stages of a mode-A chain from a reference chain (CHANGES §71; s0_workflow/chain_reuse.py), on the
Switch 3-zone toy with a third period (stages 2020, 2030, 2040). Small hand-built inputs: fixtures, not results."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_workflow import chain_reuse as cr  # noqa: E402
from s0_workflow import production as s0prod  # noqa: E402
from test_foresight import three_periods  # noqa: E402
from toyutil import subset_periods, toy_inputs  # noqa: E402

SOLVER = "--solver appsi_highs"
VERSION = "highs (toy)"
CHAINED = ("gen_build_predetermined", "gen_build_costs", "transmission_lines", "trans_built_to_date")


def chain_run(tmp_path):
    """A toy run folder with the master inputs, mods/ and the stage plan."""
    run = toy_inputs(tmp_path, "run", ["retirement_rules"], lambda i: three_periods(i))
    (run / "mods/prepare_next_stage.py").write_text((REPO / "switch/study_modules/prepare_next_stage.py").read_text())
    return run, s0prod.plan_stages([2020, 2030, 2040], "myopic")


def make_case(run, stages, case, edit=None):
    """Stage inputs in/<stage>/<case> (edit(stage name, folder) may change them) and scenarios_<case>.txt with
    pg_to_switch's chain lines (chained aliases from the second stage)."""
    lines = []
    for i, st in enumerate(stages):
        d = run / "in" / st["name"] / case
        d.parent.mkdir(parents=True, exist_ok=True)
        subset_periods(run / "inputs", d, st["years"])
        s0prod.write_stage_info(d, st)
        if edit:
            edit(st["name"], d)
        line = (f"--scenario-name {case}_{st['name']} --inputs-dir in/{st['name']}/{case} "
                f"--outputs-dir out/{st['name']}/{case} --include-module mods.retirement_rules "
                f"--include-module switch_model.generators.extensions.storage ")
        if st["next"]:
            line += "--include-module mods.prepare_next_stage "
        if i > 0:
            line += "--input-aliases " + " ".join(f"{f}.csv={f}.chained.{case}.csv" for f in CHAINED)
        lines.append(line.strip())
    f = run / f"scenarios_{case}.txt"
    f.write_text("".join(ln + "\n" for ln in lines))
    return f


def solve_lines(run, scen_file):
    for line in Path(scen_file).read_text().splitlines():
        r = subprocess.run(["switch", "solve", *line.split(), *SOLVER.split()], cwd=run, capture_output=True,
                           text=True, env={**os.environ, "PYTHONPATH": str(run)})
        assert r.returncode == 0, r.stderr[-3000:] + r.stdout[-2000:]


def cost_2040(inp_folder):
    """Make 2040 differ: dearer new-build costs in that stage only."""
    bc = pd.read_csv(inp_folder / "gen_build_costs.csv")
    bc.loc[bc.build_year == 2040, "gen_overnight_cost"] *= 1.5
    bc.to_csv(inp_folder / "gen_build_costs.csv", index=False)


@pytest.fixture
def code_ok(monkeypatch):
    """The working tree is under development during tests: the code check is tested on its own (below)."""
    monkeypatch.setattr(cr, "code_check", lambda head, scope="all": (True, "test", []))


def test_reuse_matches_a_full_run(tmp_path, code_ok):
    """Reference chain `ref`; scenario `scen` the same in 2020 and 2030, dearer new builds in 2040. Solved in full,
    and as `reuse` (same inputs as scen) with 2020 and 2030 reused from ref: the 2030 and 2040 inputs, chained files
    included, are byte-identical to scen's, and so are the final results."""
    run, stages = chain_run(tmp_path)
    edit = lambda name, d: cost_2040(d) if name == "2040" else None  # noqa: E731
    ref = make_case(run, stages, "ref")
    full = make_case(run, stages, "scen", edit)
    part = make_case(run, stages, "reuse", edit)
    solve_lines(run, ref)
    solve_lines(run, full)
    assert cr.record(ref, solver_version=VERSION, solver_args=SOLVER, switch_dir=run)
    res = cr.reuse(part, ref, solver_args=SOLVER, solver_version=VERSION, switch_dir=run, log=lambda *a: None)
    assert res["reused"] == ["2020", "2030"] and res["start"] == "2040"
    assert res["reason"].startswith("first difference: file gen_build_costs") and "contents differ" in res["reason"]
    assert res["remaining_file"].read_text().splitlines() == part.read_text().splitlines()[2:]
    # inputs of the handed-over stages, chained files included: identical to the full run's (names normalised)
    for stage in ("2030", "2040"):
        a = cr.fingerprint({"inputs": run / "in" / stage / "reuse", "case": "reuse", "aliases": {}, "options": []})
        b = cr.fingerprint({"inputs": run / "in" / stage / "scen", "case": "scen", "aliases": {}, "options": []})
        assert a["files"] == b["files"] and any(".chained." in k for k in a["files"])
    # provenance
    prov = json.loads((run / "out/2030/reuse" / cr.REUSED).read_text())
    # Path(...).as_posix(): the provenance holds the platform's own separators (backslashes on Windows)
    assert Path(prov["reused_from"]).as_posix().endswith("out/2030/ref") and prov["solver_args"] == SOLVER.split()
    rec = json.loads((run / "out/2020/ref" / cr.PROVENANCE).read_text())
    assert rec["solver_args"] == SOLVER.split()                       # §83: recorded as a token list
    assert any(f.startswith("gen_build_predetermined.chained.reuse") for f in prov["handed_over"])
    runs = pd.read_csv(run / "out/handoff_runs.csv")
    assert list(runs.action) == ["reused", "reused", "solve"] and list(runs.stage.astype(str)) == ["2020", "2030", "2040"]
    # solve the rest: same results as the full run
    solve_lines(run, res["remaining_file"])
    for f in ("total_cost.txt", "BuildGen.csv", "SuspendGen.csv"):
        x, y = run / "out/2040/reuse" / f, run / "out/2040/scen" / f
        if f.endswith(".txt"):
            assert float(x.read_text()) == pytest.approx(float(y.read_text()), rel=1e-9)
        else:
            assert x.read_bytes() == y.read_bytes()


def test_reuse_refused(tmp_path, code_ok):
    """One input differs in the first stage: nothing is reused or copied, the whole chain is left to solve and the
    first differing file is named. Different solver arguments, a reference without a record, and --reuse-through
    also stop reuse."""
    run, stages = chain_run(tmp_path)
    ref = make_case(run, stages, "ref")

    def fuel(name, d):
        if name == "2020":
            fc = pd.read_csv(d / "fuel_cost.csv")
            fc.loc[fc.index[0], "fuel_cost"] += 0.01
            fc.to_csv(d / "fuel_cost.csv", index=False)
    other = make_case(run, stages, "other", fuel)
    same = make_case(run, stages, "same")
    quiet = lambda *a: None  # noqa: E731
    r = cr.reuse(same, ref, solver_args=SOLVER, solver_version=VERSION, switch_dir=run, log=quiet)
    assert r["reused"] == [] and "chain_provenance.json" in r["reason"]           # reference not recorded
    solve_lines(run, ref)
    cr.record(ref, solver_version=VERSION, solver_args=SOLVER, switch_dir=run)
    r = cr.reuse(other, ref, solver_args=SOLVER, solver_version=VERSION, switch_dir=run, log=quiet)
    assert r["reused"] == [] and r["start"] == "2020" and r["reason"] == "first difference: file fuel_cost.csv: contents differ"
    assert not (run / "out/2020/other").exists() and len(r["remaining_file"].read_text().splitlines()) == 3
    r = cr.reuse(same, ref, solver_args=SOLVER + " --threads 2", solver_version=VERSION, switch_dir=run, log=quiet)
    assert r["reused"] == [] and r["reason"].startswith("solver arguments")
    r = cr.reuse(same, ref, solver_args=SOLVER, solver_version="highs 0", switch_dir=run, log=quiet)
    assert r["reused"] == [] and r["reason"].startswith("solver version")
    r = cr.reuse(same, ref, solver_args=SOLVER, solver_version=VERSION, switch_dir=run, log=quiet, dry_run=True)
    assert r["reused"] == ["2020", "2030", "2040"] and not (run / "out/2020/same").exists()   # dry run: compare only
    r = cr.reuse(same, ref, through=2020, solver_args=SOLVER, solver_version=VERSION, switch_dir=run, log=quiet)
    assert r["reused"] == ["2020"] and r["start"] == "2030" and "--reuse-through 2020" in r["reason"]
    with pytest.raises(FileExistsError):                                            # fresh output folders only
        cr.reuse(same, ref, solver_args=SOLVER, solver_version=VERSION, switch_dir=run, log=quiet)
    # §83: a record written before (a string, --tempdir unquoted with spaces) matches list arguments with another
    # tempdir; a real difference behind it still refuses
    for st in ("2020", "2030", "2040"):
        f = run / "out" / st / "ref" / cr.PROVENANCE
        f.write_text(json.dumps(dict(json.loads(f.read_text()), solver_args=SOLVER + " --tempdir /d/my tmp dir")))
    fresh = make_case(run, stages, "fresh")
    r = cr.reuse(fresh, ref, solver_args=SOLVER.split() + ["--tempdir", "D:/other tmp"], solver_version=VERSION,
                 switch_dir=run, log=quiet, dry_run=True)
    assert r["reused"] == ["2020", "2030", "2040"], r["reason"]
    r = cr.reuse(fresh, ref, solver_args=SOLVER.split() + ["--threads", "2", "--tempdir", "D:/x y"],
                 solver_version=VERSION, switch_dir=run, log=quiet, dry_run=True)
    assert r["reused"] == [] and r["reason"].startswith("solver arguments")


def test_line_parsing_and_names():
    p = cr.parse_line("--scenario-name a_2030 --inputs-dir in/2030/a --outputs-dir out/2030/a --include-module m "
                      "--input-aliases x.csv=x.chained.a.csv y.csv=none --input-alias z.csv=z.2.csv --flag")
    assert p["inputs_dir"] == "in/2030/a" and p["options"] == ["--include-module", "m", "--flag"]
    assert p["aliases"] == {"x.csv": "x.chained.a.csv", "y.csv": "none", "z.csv": "z.2.csv"}
    assert cr._norm_name("dup.gen_build_costs.chained.BILL_central.csv", "BILL_central") == \
        "dup.gen_build_costs.chained.{case}.csv"
    assert cr._excluded("s0_production_log.txt") and cr._excluded("scenarios_x.txt") and not cr._excluded("loads.csv")
    assert cr.is_code("switch/study_modules/prm_regional.py") and cr.is_code("pg_to_switch.py")
    assert not cr.is_code("s0_workflow/tests/test_prm.py") and not cr.is_code("s0_workflow/VM_RECIPES.md")
    assert not cr.is_code("CHANGES.md")


def test_code_check(tmp_path, monkeypatch):
    """Same head: OK. Commits since that leave code paths alone: OK, listed. A commit to a module, or an
    uncommitted change in a code path: refused."""
    repo = tmp_path / "repo"
    (repo / "switch/study_modules").mkdir(parents=True)
    git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()  # noqa: E731
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "switch/study_modules/m.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    h0 = git("rev-parse", "HEAD")
    monkeypatch.setattr(cr, "REPO", repo)
    assert cr.code_check(h0)[:2] == (True, "same code head")
    (repo / "CHANGES.md").write_text("notes\n")
    git("add", "-A")
    git("commit", "-qm", "docs")
    ok, why, commits = cr.code_check(h0)
    assert ok and len(commits) == 1 and "none in code paths" in why
    (repo / "switch/study_modules/m.py").write_text("x = 2\n")
    assert not cr.code_check(h0)[0]                                                 # uncommitted module change
    git("commit", "-qam", "module")
    ok, why, _ = cr.code_check(h0)
    assert not ok and "switch/study_modules/m.py" in why
    assert not cr.code_check("")[0]


def test_expected_reuse_from_case_definitions():
    """From this repo's case definitions (§72: the bill rows are S0 until each change): S0_tx is S0prod_A in every
    stage; BILL_central from 2035 differs in headroom, build rate, cap, moratorium and import allowance; BILL_high
    from 2030 (headroom, build rate, cap); BILL_central_S1 from 2030 (tax credits); S0prod_B (mode B) everywhere."""
    df = cr.expected_reuse(REPO / "pg/extra_inputs/scenario_inputs.csv", REPO / "pg/settings/scenario_management.yml")
    exp = df[df.expected_reuse].groupby("case").stage.apply(list).to_dict()
    assert exp.get("S0_tx") == [2028, 2030, 2035, 2040, 2045] and "S0prod_B" not in exp
    c35 = df[(df.case == "BILL_central") & (df.stage == 2035)].differences.iat[0]
    for k in ("build_rate.level", "interconnection_headroom.scenario: 'atts_s0' -> 'atts_reform'",
              "new_tx_allowance: 0.0 -> 0.85", "cap_tw_mi_per_yr: 1.4 -> 3.0", "moratorium_first_period"):
        assert k in c35, k
    h30 = df[(df.case == "BILL_high") & (df.stage == 2030)].differences.iat[0]
    assert "cap_tw_mi_per_yr: 1.4 -> 2.0" in h30 and "atts_reform" in h30 and "new_tx_allowance" not in h30
    assert "tax_credit" in df[(df.case == "BILL_central_S1") & (df.stage == 2030)].differences.iat[0]
    # period-keyed values and start years taken at the stage's year
    assert cr._at_year({"2028": 0.0, "2030": 1.4}, 2029) == 0.0 and cr._at_year({"2028": 0.0, "2030": 1.4}, 2045) == 1.4
    assert cr._at_year({"moratorium_first_period": 2040}, 2035) == {"moratorium_first_period": "not yet"}


def test_hand_over_copies_the_model_dependent_build_rate_history(tmp_path, monkeypatch):
    """build_rate_prev_build.chained.<case>.csv comes from the solved model (BRNewBuild), not from written outputs:
    after a reused stage it is the reference's next-stage file, renamed to the scenario."""
    called = []
    monkeypatch.setattr(cr, "run_prepare_next_stage", lambda i, o, py=None: called.append(str(o)))
    st = {"inputs": tmp_path / "in/2028/s", "outputs": tmp_path / "out/2028/s", "case": "s"}
    nxt = {"inputs": tmp_path / "in/2030/s", "case": "s"}
    rnx = {"inputs": tmp_path / "in/2030/r", "case": "r"}
    for d in (st["inputs"], st["outputs"], nxt["inputs"], rnx["inputs"]):
        d.mkdir(parents=True)
    (rnx["inputs"] / "build_rate_prev_build.chained.r.csv").write_text("BR_GROUP,br_prev_rate_mw_per_yr\nsolar,10.0\n")
    done = cr.hand_over(st, nxt, rnx)
    assert called == [str(st["outputs"])]
    assert (nxt["inputs"] / "build_rate_prev_build.chained.s.csv").read_text() == \
        (rnx["inputs"] / "build_rate_prev_build.chained.r.csv").read_text()
    assert done == ["build_rate_prev_build.chained.s.csv (copied from the reference)"]


def test_order_insensitive_files_and_tempdir_args(tmp_path):
    """Program-membership files match whatever their row order (an unordered write); other files are byte for byte
    (row order can define the model); --tempdir is left out of the solver comparison."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "max_cap_generators.csv").write_text("MAX_CAP_PROGRAM,MAX_CAP_GEN\nMaxCapTag_A,g1\nMaxCapTag_B,g2\n")
    (b / "max_cap_generators.csv").write_text("MAX_CAP_PROGRAM,MAX_CAP_GEN\r\nMaxCapTag_B,g2\r\nMaxCapTag_A,g1\r\n")
    (a / "timepoints.csv").write_text("timepoint_id,timeseries\n1,s\n2,s\n")
    (b / "timepoints.csv").write_text("timepoint_id,timeseries\n2,s\n1,s\n")
    fa = cr.fingerprint({"inputs": a, "case": "a", "aliases": {}, "options": []})
    fb = cr.fingerprint({"inputs": b, "case": "b", "aliases": {}, "options": []})
    assert fa["files"]["max_cap_generators.csv"] == fb["files"]["max_cap_generators.csv"]
    assert fa["files"]["timepoints.csv"] != fb["files"]["timepoints.csv"]
    assert cr.first_difference(fa, fb) == "file timepoints.csv: contents differ"
    (b / "max_cap_generators.csv").write_text("MAX_CAP_PROGRAM,MAX_CAP_GEN\nMaxCapTag_A,g1\nMaxCapTag_B,g3\n")
    assert cr.content_hash(a / "max_cap_generators.csv") != cr.content_hash(b / "max_cap_generators.csv")
    base = "--solver gurobi --solver-options-string 'method=2 crossover=0 Threads=8'"
    assert cr._norm_args(base + " --tempdir /d/tmp") == cr._norm_args(base)
    assert cr._norm_args("--tempdir=D:/tmp " + base) == cr._norm_args(base)
    assert cr._norm_args(base.replace("Threads=8", "Threads=4")) != cr._norm_args(base)


def test_tempdir_paths_with_spaces_and_list_arguments():
    """§83: --tempdir is left out whatever its path: quoted with spaces (one token), unquoted with spaces (several
    tokens, as S0prod_A's record held it), --tempdir=... unquoted with spaces, in the middle or at the end, from a
    string or a list. Everything else is kept and compared in order."""
    base = "--solver gurobi --solver-options-string 'method=2 crossover=0 Threads=8'"
    want = cr._norm_args(base)
    assert want == ["--solver", "gurobi", "--solver-options-string", "method=2 crossover=0 Threads=8"]
    for s in (base + ' --tempdir "/d/my tmp dir"',                 # quoted
              base + " --tempdir /d/my tmp dir",                   # unquoted with spaces, at the end
              "--tempdir /d/my tmp dir " + base,                   # unquoted with spaces, before the next option
              "--solver gurobi --tempdir D:/My Temp --solver-options-string 'method=2 crossover=0 Threads=8'",
              base + " --tempdir=/d/my tmp dir",                   # = form, unquoted with spaces
              "--tempdir='C:/Program Files/tmp' " + base):         # = form, quoted
        assert cr._norm_args(s) == want, s
    lst = ["--solver", "gurobi", "--tempdir", "/d/my tmp dir", "--solver-options-string", "method=2 crossover=0 Threads=8"]
    assert cr.arg_list(lst) == lst and cr._norm_args(lst) == want     # a list: items as given
    assert cr._norm_args(None) == [] and cr._norm_args("") == []
    assert cr.arg_list(base) == want                                   # strings split shell-style (quotes group)
    # an old string record against new list arguments with another tempdir: equal
    assert cr._norm_args(base + " --tempdir /d/my tmp dir") == cr._norm_args(lst[:2] + ["--tempdir", "E:/x"] + lst[4:])


def test_program_files_written_sorted():
    """pg_to_switch sorts the program-membership files by program (stable), so their row order no longer follows the
    run-dependent order of PowerGenome's tag columns."""
    src = (REPO / "pg_to_switch.py").read_text()
    assert 'prog_gens_long.sort_values(prog_gens_long.columns[0], kind="stable")' in src


def test_code_check_model_scope(tmp_path, monkeypatch):
    """§80: code_scope "model" ignores input-writing code (compared through the inputs) and still refuses a change to
    a Switch module, module list or options; the default keeps §71's rule."""
    repo = tmp_path / "repo"
    (repo / "switch/study_modules").mkdir(parents=True)
    (repo / "s0_workflow").mkdir()
    git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()  # noqa: E731
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "switch/study_modules/m.py").write_text("x = 1\n")
    (repo / "pg_to_switch.py").write_text("a = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    h0 = git("rev-parse", "HEAD")
    monkeypatch.setattr(cr, "REPO", repo)
    (repo / "pg_to_switch.py").write_text("a = 2\n")
    (repo / "s0_workflow/production.py").write_text("b = 1\n")
    git("add", "-A")
    git("commit", "-qm", "input writing")
    ok, why, _ = cr.code_check(h0)
    assert not ok and "pg_to_switch.py" in why                                     # §71 default
    ok, why, commits = cr.code_check(h0, "model")
    assert ok and len(commits) == 1 and "none in model code paths" in why
    (repo / "switch/modules.txt").write_text("m\n")
    assert not cr.code_check(h0, "model")[0]                                        # uncommitted module-list change
    git("add", "-A")
    git("commit", "-qm", "module list")
    ok, why, _ = cr.code_check(h0, "model")
    assert not ok and "switch/modules.txt" in why
    with pytest.raises(ValueError, match="code scope"):
        cr.code_check(h0, "none")
