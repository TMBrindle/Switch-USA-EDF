"""Opt-in: with tracked_demands unloaded, every existing case builds and solves byte-identically.

1. Code: against the branch's base on tom/s0-v3.1, this branch only adds files (the module, its guide, tracked_demands/)
   and the CHANGES/SHARED_CHANGES notes, plus the .gitignore lines that keep tracked_demands/ in the repo. No
   existing module, module list, option file, setting or case-build script changes, so the case build of every
   existing row (the S0 regression row s4x1_S0prod_2035 included) and its solve run the same code as on the base.
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

BASE_FALLBACK = "9d68751"   # tom/s0-v3.1 when this branch was cut (CHANGES §94)
ALLOWED_NEW = ("tracked_demands/", "switch/study_modules/tracked_demands.py",
               "Guides and documentation/tracked_demands.md")
ALLOWED_MODIFIED = ("CHANGES.md", "SHARED_CHANGES.md", ".gitignore")
GITIGNORE_ADDED = {"!tracked_demands/", "!tracked_demands/**", "tracked_demands/outputs/"}


def _git(*args) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def _base() -> str:
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    for ref in ("origin/tom/s0-v3.1", BASE_FALLBACK):
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
        if status == "M" and path in ALLOWED_MODIFIED:
            continue
        bad.append(f"{status} {path}")
    assert not bad, "shared files changed (needs a SHARED_CHANGES entry and its own proof):\n" + "\n".join(bad)
    # .gitignore: only the three lines that keep tracked_demands/ tracked
    diff = _git("diff", base, "--", ".gitignore").splitlines()
    added = {l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")}
    removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
    assert not removed and added <= GITIGNORE_ADDED


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
