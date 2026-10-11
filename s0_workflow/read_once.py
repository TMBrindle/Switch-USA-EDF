"""Read-once, in-memory cache for the large input files a case build re-reads in every stage (CHANGES §102).

pg_to_switch builds all the stages of a run in one process. PowerGenome re-reads the same input files in every stage:
the hourly load file (regional_load_fn, "Loading user supplied load profile"), the flexible-demand file
(demand_response_fn, once per resource type and stage, plus pg_to_switch's own aggregate-zone fix), the EIA-860M
and coal tracker workbooks and the distributed-generation profiles. Parsing them doesn't depend on the stage year.

Inside `read_once(names)`, pandas.read_csv / read_parquet / read_excel calls on one of the named input files are
parsed once and then served from memory:
  * key: the resolved path, its size and mtime (a changed file is re-read), the reader and the call's arguments
    (a different usecols, dtype, sheet, ... is a separate entry);
  * every call returns a deep copy, so a caller that edits its frame in place can't change what the next one gets;
  * calls with a file handle or buffer, a callable argument, chunksize / iterator, or on a file that isn't named, go
    straight to pandas.
Nothing is written to disk. What a caller receives is equal to what pandas would have returned (same values, dtypes,
index, columns), so the written inputs are unchanged. The build log reports hits, misses and the parse time saved.

Off with the environment variable PG_READ_ONCE=0.
"""
from __future__ import annotations

import copy
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)
READERS = ("read_csv", "read_parquet", "read_excel")
PATH_ARGS = {"read_csv": "filepath_or_buffer", "read_parquet": "path", "read_excel": "io"}
UNCACHEABLE_KW = {"chunksize", "iterator"}


def enabled() -> bool:
    return os.environ.get("PG_READ_ONCE", "1").strip().lower() not in ("0", "false", "no", "off")


def input_file_names(case_settings: dict) -> set[str]:
    """Base names of every input file a case's settings name (keys ending in _fn, any year, any case)."""
    names = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).endswith("_fn") and isinstance(v, (str, os.PathLike)) and str(v):
                    names.add(Path(str(v)).name)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(case_settings)
    return names


def _copy(obj):
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return obj.copy(deep=True)
    if isinstance(obj, dict):                       # read_excel with several sheets
        return {k: _copy(v) for k, v in obj.items()}
    return copy.deepcopy(obj)


def _freeze(v):
    """A hashable stand-in for an argument, or raise TypeError (then the call isn't cached)."""
    if callable(v) and not isinstance(v, type):
        raise TypeError("callable argument")
    if isinstance(v, dict):
        return ("dict", tuple(sorted((repr(k), _freeze(x)) for k, x in v.items())))
    if isinstance(v, (list, tuple, set, frozenset)):
        items = [_freeze(x) for x in v]
        return (type(v).__name__, tuple(sorted(items, key=repr)) if isinstance(v, (set, frozenset)) else tuple(items))
    if isinstance(v, os.PathLike):
        return ("path", os.fspath(v))
    if isinstance(v, type):
        return ("type", v.__module__, v.__qualname__)
    hash(v)
    return (type(v).__name__, repr(v))


class ReadOnceCache:
    def __init__(self, names):
        self.names = {str(n) for n in names}
        self.store = {}
        self.stats = {}                              # path -> [misses, hits, seconds parsing (misses)]

    def key(self, reader, args, kwargs):
        if UNCACHEABLE_KW & {k for k, v in kwargs.items() if v not in (None, False)}:
            return None
        if args:
            src, rest = args[0], args[1:]
        elif PATH_ARGS[reader] in kwargs:
            src, rest = kwargs[PATH_ARGS[reader]], ()
            kwargs = {k: v for k, v in kwargs.items() if k != PATH_ARGS[reader]}
        else:
            return None
        if not isinstance(src, (str, os.PathLike)):
            return None
        p = Path(os.fspath(src))
        if p.name not in self.names:
            return None
        try:
            rp = p.resolve()
            st = rp.stat()
            frozen = (_freeze(list(rest)), _freeze(kwargs))
        except (OSError, TypeError):
            return None
        return (reader, str(rp), st.st_size, st.st_mtime_ns, frozen)

    def wrap(self, reader, original):
        def cached(*args, **kwargs):
            k = self.key(reader, args, kwargs)
            if k is None:
                return original(*args, **kwargs)
            s = self.stats.setdefault(k[1], [0, 0, 0.0])
            if k in self.store:
                s[1] += 1
                return _copy(self.store[k])
            t0 = time.perf_counter()
            out = original(*args, **kwargs)
            s[0] += 1
            s[2] += time.perf_counter() - t0
            self.store[k] = _copy(out)
            return out
        cached.__wrapped__ = original
        return cached

    def summary(self) -> list[dict]:
        rows = []
        for path, (miss, hit, sec) in sorted(self.stats.items()):
            rows.append({"file": path, "reads": miss + hit, "parsed": miss, "served_from_memory": hit,
                         "parse_seconds": round(sec, 2),
                         "seconds_saved_est": round(sec / miss * hit, 1) if miss else 0.0})
        return rows


@contextmanager
def read_once(names, log=None):
    """Within the block, pandas readers serve repeat reads of the named input files from memory."""
    log = log or logger.info
    if not enabled() or not names:
        yield None
        return
    cache = ReadOnceCache(names)
    saved = {r: getattr(pd, r) for r in READERS}
    try:
        for r in READERS:
            setattr(pd, r, cache.wrap(r, saved[r]))
        yield cache
    finally:
        for r in READERS:
            setattr(pd, r, saved[r])
        rows = cache.summary()
        if rows:
            log("read-once input cache (§102): "
                + "; ".join(f"{Path(x['file']).name}: {x['reads']} reads, parsed {x['parsed']}x "
                            f"({x['parse_seconds']} s), ~{x['seconds_saved_est']} s saved" for x in rows)
                + f"; total ~{sum(x['seconds_saved_est'] for x in rows) / 60:.1f} min saved")
        else:
            log("read-once input cache (§102): no reads of the named input files went through pandas")
        cache.store.clear()
