"""S0 v3.1 (tom/s0-v3.1): build rates stay exactly at v3 (79f4064). The rate tables (build_rate/outputs, generated on
the VM by `python -m brc.cli run`, not tracked) are a function of the build_rate/ code, config and reference data, the
settings file, the Switch module and the case writer; every one of those is pinned here to its v3 content.

Hashes are git blob ids of the content with line endings normalised to LF (CRLF -> LF), so a Windows checkout with
core.autocrlf (CRLF in the working tree) passes: the id of a whole file equals `git rev-parse 79f4064:<path>`."""
import ast
import hashlib
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
V3 = "79f4064"
# files pinned to their 79f4064 content (git blob ids in s0_workflow/data/build_rate_v3_manifest.csv)
PINNED = {
    "build_rate/README.md", "build_rate/__init__.py", "build_rate/brc/__init__.py", "build_rate/brc/cli.py",
    "build_rate/brc/data.py", "build_rate/brc/groups.py", "build_rate/brc/rates.py", "build_rate/brc/switch_case.py",
    "build_rate/brc/turbine_cap.py", "build_rate/config.yaml", "build_rate/data/reference/REEDS_COMMIT.txt",
    "build_rate/data/reference/county2zone.csv", "build_rate/scripts/fetch_data.sh",
    "build_rate/scripts/reform_sensitivity.py", "pg/settings/build_rate.yml",
}
# the Switch module's model (everything but post_solve, whose outputs gained a units column in v3.1)
MODULE = "switch/study_modules/build_rate.py"


def _blob(b: bytes) -> str:
    """git blob id of the content with CRLF normalised to LF (git's own id for an LF-committed file)."""
    b = b.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _manifest():
    rows = _text(REPO / "s0_workflow/data/build_rate_v3_manifest.csv").splitlines()[1:]
    return dict(r.split(",") for r in rows if r)


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
        assert _blob((REPO / f).read_bytes()) == man[f], f"{f} differs from v3 ({V3}): build rates must stay at v3"
    src = _case_writer_src(_text(REPO / "pg_to_switch.py"))
    assert _blob(src.encode()) == man["pg_to_switch.py::write_build_rate_files"], "build-rate case writer changed"
    assert _blob(_model_src(_text(REPO / MODULE)).encode()) == man[MODULE + "::model"], \
        "the build-rate Switch module's model changed"


def test_manifest_matches_v3_commit():
    """The manifest is v3's own content (where the git history is available)."""
    ok = subprocess.run(["git", "cat-file", "-e", V3], cwd=REPO, capture_output=True).returncode == 0
    if not ok:
        pytest.skip(f"{V3} not in this checkout's history")
    man = _manifest()
    for f in sorted(PINNED):
        oid = subprocess.run(["git", "rev-parse", f"{V3}:{f}"], cwd=REPO, capture_output=True, text=True,
                             check=True).stdout.strip()
        assert oid == man[f], f                                      # the manifest holds git's own blob ids
    show = lambda f: subprocess.run(["git", "show", f"{V3}:{f}"], cwd=REPO, capture_output=True,  # noqa: E731
                                    check=True).stdout.decode("utf-8").replace("\r\n", "\n")
    assert _blob(_case_writer_src(show("pg_to_switch.py")).encode()) == man["pg_to_switch.py::write_build_rate_files"]
    assert _blob(_model_src(show(MODULE)).encode()) == man[MODULE + "::model"]


def test_crlf_checkout_gives_the_same_ids(tmp_path):
    """A file with CRLF line endings (a Windows checkout) hashes like its LF original."""
    lf = (REPO / "pg/settings/build_rate.yml").read_bytes().replace(b"\r\n", b"\n")
    assert _blob(lf.replace(b"\n", b"\r\n")) == _blob(lf) == _manifest()["pg/settings/build_rate.yml"]
