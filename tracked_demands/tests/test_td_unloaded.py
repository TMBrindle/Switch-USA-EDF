"""Opt-in: with tracked_demands unloaded, every existing case builds and solves byte-identically.

1. Code: against the s0-v3.1 tag (merged into this branch), this branch only adds files (the module, its guide,
   tracked_demands/) and the CHANGES/SHARED_CHANGES notes, plus the .gitignore lines that keep tracked_demands/ in
   the repo, and the shared-module edits in SHARED_EDITS (each with its SHARED_CHANGES row). No module list, option
   file, setting or case-build script changes. Each shared edit is proved on the toy: with tracked_demands
   unloaded, the edited module writes the same bytes as its copy on the base. So every existing case (the S0
   regression row s4x1_S0prod_2035 included) builds with the same code and solves to the same outputs.
2. Wiring: nothing loads the module by default (switch/modules.txt, options.txt, pg settings, scenario rows, the S0
   case writer, pg_to_switch, other study modules).
3. Solve: on the toy, a case with every tracked-demand input file present but the module unloaded writes the same
   bytes, in every output file, as the same case without them; with the module loaded and no tracked demands, the
   objective and the plan are the same.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from td_toy import REPO, read, cost, td_case, toyutil

BASE = "3160be1"   # the s0-v3.1 tag, merged into this branch (CHANGES §108; cut from 9d68751, §107)
ALLOWED_NEW = ("tracked_demands/", "switch/study_modules/tracked_demands.py",
               "Guides and documentation/tracked_demands.md")
ALLOWED_MODIFIED = ("CHANGES.md", "SHARED_CHANGES.md", ".gitignore")
# shared modules this branch edits (each a SHARED_CHANGES row, for Ollie's review), and the toy setup that proves the
# edit leaves outputs byte-identical with tracked_demands unloaded: {path: (SHARED_CHANGES row, study modules, edit)}
SHARED_EDITS = {
    "switch/study_modules/carbon_policies_regional.py": (111, ("carbon_policies_regional",), "carbon"),
}
GITIGNORE_ADDED = {"!tracked_demands/", "!tracked_demands/**", "tracked_demands/outputs/"}


def _git(*args) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def _base() -> str:
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    for ref in (BASE, "s0-v3.1"):
        try:
            return _git("merge-base", "HEAD", ref).strip()
        except subprocess.CalledProcessError:
            continue
    pytest.skip("base commit not available (shallow clone?)")


def test_branch_changes_no_shared_code():
    base = _base()
    changed = [l.split("\t") for l in _git("diff", "--name-status", "--no-renames", base).splitlines()]
    untracked = _git("ls-files", "--others", "--exclude-standard").splitlines()
    bad = []
    for status, path in [(s, p) for s, p in changed] + [("A", p) for p in untracked]:
        if status == "A" and path.startswith(ALLOWED_NEW):
            continue
        if status == "M" and (path in ALLOWED_MODIFIED or path in SHARED_EDITS):
            continue
        bad.append(f"{status} {path}")
    assert not bad, "shared files changed (needs a SHARED_CHANGES entry and its own proof):\n" + "\n".join(bad)
    # .gitignore: only the three lines that keep tracked_demands/ tracked
    diff = _git("diff", base, "--", ".gitignore").splitlines()
    added = {l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")}
    removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
    assert not removed and added <= GITIGNORE_ADDED


def test_shared_edits_have_their_row():
    text = (REPO / "SHARED_CHANGES.md").read_text()
    for path, (row, _, _) in SHARED_EDITS.items():
        assert any(l.startswith(f"| {row} |") and Path(path).name in l for l in text.splitlines()), (path, row)


def _toy_edit(kind):
    def edit(inp):
        if kind == "carbon":
            # a binding hard cap on North, so the cap row and its dual shape the solution
            pd.DataFrame([{"CO2_PROGRAM": "RGGI", "PERIOD": p, "LOAD_ZONE": "North",
                           "carbon_cap_tco2_per_yr": 15000.0, "carbon_cost_dollar_per_tco2": "."}
                          for p in (2020, 2030)]).to_csv(inp / "carbon_policies_regional.csv", index=False)
    return edit


@pytest.mark.parametrize("path", sorted(SHARED_EDITS))
def test_shared_edit_unloaded_is_byte_identical(tmp_path, path):
    """The edited shared module, with tracked_demands unloaded, writes the same bytes as its copy on the base."""
    base = _base()
    row, mods, kind = SHARED_EDITS[path]
    new = toyutil.toy_inputs(tmp_path, "branch", modules=mods, edit=_toy_edit(kind))
    old = toyutil.toy_inputs(tmp_path, "base", modules=mods, edit=_toy_edit(kind))
    (old / "mods" / Path(path).name).write_text(_git("show", f"{base}:{path}"))
    for run in (new, old):
        toyutil.solve(run)
    a, b = new / "outputs", old / "outputs"
    assert sorted(os.listdir(a)) == sorted(os.listdir(b))
    diff = [f for f in sorted(os.listdir(a)) if (a / f).read_bytes() != (b / f).read_bytes()]
    assert not diff, diff


def test_nothing_loads_the_module_by_default():
    hits = []
    files = [REPO / "switch/modules.txt", REPO / "switch/options.txt", REPO / "pg_to_switch.py",
             REPO / "pg/extra_inputs/scenario_inputs.csv"]
    files += sorted((REPO / "pg/settings").glob("*.yml"))
    files += sorted((REPO / "s0_workflow").glob("*.py")) + sorted((REPO / "adjust").glob("*.py"))
    files += [f for f in sorted((REPO / "switch/study_modules").glob("*.py")) if f.name != "tracked_demands.py"]
    for f in files:
        if f.exists() and "tracked_demand" in f.read_text(errors="ignore"):
            hits.append(str(f.relative_to(REPO)))
    assert not hits, hits


def test_unloaded_toy_is_byte_identical(tmp_path):
    plain = toyutil.toy_run(tmp_path, "plain")
    # every tracked-demand input file the module reads, present but the module not loaded
    with_files = td_case(tmp_path, "files", load_module=False, matching="hourly", stress=False)
    a, b = plain / "outputs", with_files / "outputs"
    fa, fb = sorted(os.listdir(a)), sorted(os.listdir(b))
    assert fa == fb
    diff = [f for f in fa if (a / f).read_bytes() != (b / f).read_bytes()]
    assert not diff, diff


def test_loaded_without_tracked_demands_is_a_no_op(tmp_path):
    plain = toyutil.toy_run(tmp_path, "plain")
    loaded = td_case(tmp_path, "loaded", td=False)
    assert cost(loaded) == pytest.approx(cost(plain), rel=1e-9)
    for f in ("BuildGen.csv", "dispatch.csv"):
        x, y = read(plain, f), read(loaded, f)
        pd.testing.assert_frame_equal(x, y, check_exact=False, rtol=1e-7, atol=1e-6)
