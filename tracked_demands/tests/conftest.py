"""Shared test setup for tracked_demands: the same Windows C long emulation (int64 guard) as s0_workflow's tests.

`astype(int)` on pandas objects raises on any value outside int32, as numpy < 2 does on Windows; see
s0_workflow/tests/conftest.py. The fixture is loaded from there so both suites use one definition.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "s0_tests_conftest", Path(__file__).resolve().parents[2] / "s0_workflow" / "tests" / "conftest.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
windows_c_long = _mod.windows_c_long
