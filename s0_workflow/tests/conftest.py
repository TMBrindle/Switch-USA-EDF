"""Shared test setup for s0_workflow.

Windows C long emulation (autouse): with numpy < 2 on Windows, `astype(int)` converts to the platform C long, which is
32-bit, so a value of 2**31 or more raises "OverflowError: Python int too large to convert to C long". Timepoint ids
are 11-12 digits (yyyy[rep]mmddhh, and 9-prefixed stress-day ids), so any id that goes through `astype(int)` fails
there but not on Linux, where the C long is 64-bit. This fixture makes `astype(int)` (also "int" / "long") on pandas
objects raise the same error on every platform when a value is outside int32, so the tests catch it off Windows.
Convert ids with `astype("int64")` (or keep them as strings).
"""
import numpy as np
import pandas as pd
import pytest

INT32_MIN, INT32_MAX = -(2 ** 31), 2 ** 31 - 1
PLATFORM_INT = (int, "int", "long")


def _is_platform_int(dtype) -> bool:
    return any(dtype is t or (isinstance(dtype, str) and dtype == t) for t in PLATFORM_INT)


def _check(result, what):
    vals = result.to_numpy().ravel() if hasattr(result, "to_numpy") else np.asarray(result).ravel()
    if vals.size and np.issubdtype(vals.dtype, np.integer) and (vals.min() < INT32_MIN or vals.max() > INT32_MAX):
        raise OverflowError(f"Python int too large to convert to C long (Windows C long emulated by "
                            f"s0_workflow/tests/conftest.py: {what}.astype(int) with a value outside int32; "
                            f"use astype('int64'))")


@pytest.fixture(autouse=True)
def windows_c_long(monkeypatch):
    series_astype, frame_astype, index_astype = pd.Series.astype, pd.DataFrame.astype, pd.Index.astype

    def s_astype(self, dtype, *a, **k):
        out = series_astype(self, dtype, *a, **k)
        if _is_platform_int(dtype):
            _check(out, "Series")
        return out

    def f_astype(self, dtype, *a, **k):
        out = frame_astype(self, dtype, *a, **k)
        cols = ([c for c, d in dtype.items() if _is_platform_int(d)] if isinstance(dtype, dict)
                else (list(out.columns) if _is_platform_int(dtype) else []))
        if cols:
            _check(out[cols], "DataFrame")
        return out

    def i_astype(self, dtype, *a, **k):
        out = index_astype(self, dtype, *a, **k)
        if _is_platform_int(dtype):
            _check(out, "Index")
        return out

    monkeypatch.setattr(pd.Series, "astype", s_astype)
    monkeypatch.setattr(pd.DataFrame, "astype", f_astype)
    monkeypatch.setattr(pd.Index, "astype", i_astype)
    yield


# §129: offshore_filter_fix is ON in s0_production.yml, pointing at a split RESOURCE_GROUPS folder written on the VM
# (split_offshore_groups.py). Unit tests that resolve the S0 defaults have no such folder; for that one default path
# only, while it is missing, check_folder returns a stub manifest (tests of the offshore fix use their own folders and
# the real check). The case build itself still stops when the folder is missing.
@pytest.fixture(autouse=True)
def offshore_split_default_folder(monkeypatch):
    from pathlib import Path

    import yaml
    from s0_workflow import offshore_groups as og
    repo = Path(__file__).resolve().parents[2]
    d = ((yaml.safe_load(open(repo / "pg/settings/s0_production.yml"))["s0_production"].get("offshore_filter_fix")
          or {}).get("resource_groups_dir"))
    if not d:
        yield
        return
    default = (repo / d).resolve()
    real = og.check_folder

    def check_folder(folder, profile_roots=()):
        if Path(folder).resolve() == default and not (og.meta_dir(default) / og.MANIFEST).exists():
            return {"groups": [], "true_resource_mw": 0.0, "stub": "s0_workflow/tests/conftest.py"}
        return real(folder, profile_roots)

    monkeypatch.setattr(og, "check_folder", check_folder)
    yield


# §132: likewise pv_profile_repair is ON, pointing at the repaired profiles folder on the VM (pg_data/profiles_pvfix).
# For that default path only, while its manifest is missing, check_folder returns a stub manifest matching the
# settings' method and hash prefix.
@pytest.fixture(autouse=True)
def pv_repair_default_folder(monkeypatch):
    from pathlib import Path

    import yaml
    from s0_workflow import pv_profile_repair as pr
    repo = Path(__file__).resolve().parents[2]
    r = yaml.safe_load(open(repo / "pg/settings/s0_production.yml"))["s0_production"].get("pv_profile_repair") or {}
    if not r.get("profiles_dir"):
        yield
        return
    default = (repo / r["profiles_dir"]).resolve()
    real = pr.check_folder

    def check_folder(folder, sites=None, full=False):
        if Path(folder).resolve() == default and not (default / pr.MANIFEST).exists():
            return {"site_years_filled": 0, "sites_zero_before": 0, "neighbour_method": r.get("neighbour_method"),
                    "output": {"sha256": str(r.get("output_sha256") or "")}, "stub": "s0_workflow/tests/conftest.py"}
        return real(folder, sites, full)

    monkeypatch.setattr(pr, "check_folder", check_folder)
    yield


# §134: the build guards. The cluster-cache marker goes to a temporary folder (never the repo's pg/extra_inputs), and
# PowerGenome provenance is stubbed where powergenome isn't importable (the cloud test environment): the stub reports
# the commit the settings ask for. Tests of the guards themselves call build_guard with their own folders and info.
@pytest.fixture(autouse=True)
def build_guard_sandbox(monkeypatch, tmp_path_factory):
    from s0_workflow import build_guard as bg
    # a fresh folder per call: unit tests build v3.2 and flags-off cases side by side, which one VM build can't
    # (the guard stops it; test_build_guard.py tests that with its own folder)
    monkeypatch.setattr(bg, "cache_dir", lambda s: tmp_path_factory.mktemp("cluster_cache") / "cluster_assignments")
    bg._PG_CHECKED.clear()
    try:
        import powergenome  # noqa: F401
    except ImportError:
        def info(fn=None):
            import yaml
            from pathlib import Path
            repo = Path(__file__).resolve().parents[2]
            want = yaml.safe_load(open(repo / "pg/settings/s0_production.yml"))["s0_production"].get("powergenome_commit")
            return {"package": "stub", "root": "stub", "commit": want or "stub", "dirty": False,
                    "is_repo_submodule": True, "cost_multiplier_fn": fn, "cost_multiplier_file_present": True,
                    "cost_multiplier_sha256": None, "stub": "s0_workflow/tests/conftest.py"}
        monkeypatch.setattr(bg, "powergenome_info", info)
    yield
