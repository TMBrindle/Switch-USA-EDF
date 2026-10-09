"""S0 v3.1 (tom/s0-v3.1): build rates stay exactly at v3 (79f4064). The rate tables (build_rate/outputs, generated on
the VM by `python -m brc.cli run`, not tracked) are a function of the build_rate/ code, config and reference data, the
settings file, the Switch module and the case writer; every one of those is pinned here to its v3 content."""
import ast
import hashlib
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
V3 = "79f4064"
# files pinned to their 79f4064 content (sha256 in s0_workflow/data/build_rate_v3_manifest.csv)
PINNED = {
    "build_rate/README.md", "build_rate/__init__.py", "build_rate/brc/__init__.py", "build_rate/brc/cli.py",
    "build_rate/brc/data.py", "build_rate/brc/groups.py", "build_rate/brc/rates.py", "build_rate/brc/switch_case.py",
    "build_rate/brc/turbine_cap.py", "build_rate/config.yaml", "build_rate/data/reference/REEDS_COMMIT.txt",
    "build_rate/data/reference/county2zone.csv", "build_rate/scripts/fetch_data.sh",
    "build_rate/scripts/reform_sensitivity.py", "pg/settings/build_rate.yml",
}
# the Switch module's model (everything but post_solve, whose outputs gained a units column in v3.1)
MODULE = "switch/study_modules/build_rate.py"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _manifest():
    rows = (REPO / "s0_workflow/data/build_rate_v3_manifest.csv").read_text().splitlines()[1:]
    return dict(r.split(",") for r in rows)


def _case_writer_src(text: str) -> str:
    tree = ast.parse(text)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "write_build_rate_files")
    return ast.get_source_segment(text, fn)


def _model_src(text: str) -> str:
    """The module's source text without the post_solve function (cut by line numbers: the same on every Python)."""
    fn = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "post_solve")
    lines = text.splitlines(keepends=True)
    return "".join(lines[:fn.lineno - 1] + lines[fn.end_lineno:])


def test_build_rate_inputs_are_v3():
    man = _manifest()
    assert set(man) == PINNED | {"pg_to_switch.py::write_build_rate_files", MODULE + "::model"}
    for f in sorted(PINNED):
        assert _sha((REPO / f).read_bytes()) == man[f], f"{f} differs from v3 ({V3}): build rates must stay at v3"
    src = _case_writer_src((REPO / "pg_to_switch.py").read_text())
    assert _sha(src.encode()) == man["pg_to_switch.py::write_build_rate_files"], "build-rate case writer changed"
    assert _sha(_model_src((REPO / MODULE).read_text()).encode()) == man[MODULE + "::model"], \
        "the build-rate Switch module's model changed"


def test_manifest_matches_v3_commit():
    """The manifest is v3's own content (where the git history is available)."""
    ok = subprocess.run(["git", "cat-file", "-e", V3], cwd=REPO, capture_output=True).returncode == 0
    if not ok:
        pytest.skip(f"{V3} not in this checkout's history")
    man = _manifest()
    for f in sorted(PINNED):
        b = subprocess.run(["git", "show", f"{V3}:{f}"], cwd=REPO, capture_output=True, check=True).stdout
        assert _sha(b) == man[f], f
    t = subprocess.run(["git", "show", f"{V3}:pg_to_switch.py"], cwd=REPO, capture_output=True, text=True,
                       check=True).stdout
    assert _sha(_case_writer_src(t).encode()) == man["pg_to_switch.py::write_build_rate_files"]
    t = subprocess.run(["git", "show", f"{V3}:{MODULE}"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    assert _sha(_model_src(t).encode()) == man[MODULE + "::model"]
