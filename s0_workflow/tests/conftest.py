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
