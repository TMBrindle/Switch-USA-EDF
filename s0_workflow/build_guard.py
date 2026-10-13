"""Build guards for the v3.2 S0 build (CHANGES §134).

PowerGenome provenance (s0_production.powergenome_commit; V32-04):
    The build imports whichever `powergenome` package Python finds first. On the VM that was another checkout
    ("Q3 Federal Policy analysis/SWITCH-USA-EDF/PowerGenome"), not this repo's submodule. Then V32-04's pin (c4c433c, which
    adds data/cost_multipliers/AEO_2026_regional_cost_corrections.csv) would not apply, and nothing would say so.
    `powergenome_info` records the imported package's folder, its git commit and whether the cost-multiplier file is
    in its data/cost_multipliers. Each case folder gets powergenome_provenance.json, and the log gets one line.
    With powergenome_commit set (v3.2: c4c433c), the build stops unless the imported commit starts with it and the
    cost-multiplier file is present. Import the submodule with PYTHONPATH=<worktree>/PowerGenome (VM_RECIPES.md,
    recipe L, step 6).

PowerGenome's cluster cache (pg/extra_inputs/cluster_assignments):
    The cache folders are named by a hash that this repo can't check against the resource-group or profile data (the
    README's rule is to clear the cache when resource groups change). With the PV repair or the offshore split on, a
    cache made from the unrepaired profiles or the unsplit offshore groups would be reused silently. So the v3.2 build
    keeps a marker, S0_V32_CACHE.json, in the cache folder: a fingerprint of the repaired PV file's sha256 and the
    offshore split manifest.
    - The first v3.2 build needs an empty (or new) cache folder, and writes the marker.
    - Later builds need the same marker.
    - A build with both flags off refuses a cache that carries a v3.2 marker.
    Anything else stops the build, and the message tells the runner to move the folder aside (a rename, not a delete).
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[1]
MARKER = "S0_V32_CACHE.json"
PROVENANCE = "powergenome_provenance.json"
_PG_CHECKED: dict = {}


# ---- PowerGenome provenance -----------------------------------------------------------------------------------------
def powergenome_info(cost_multiplier_fn: str | None = None) -> dict:
    import powergenome
    pkg = Path(powergenome.__file__).resolve().parent
    root = pkg.parent
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    commit = r.stdout.strip() if r.returncode == 0 else None
    d = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                       text=True)
    info = {"package": str(pkg), "root": str(root), "commit": commit,
            "dirty": bool(d.stdout.strip()) if d.returncode == 0 else None,
            "is_repo_submodule": root == (REPO / "PowerGenome").resolve()}
    if cost_multiplier_fn:
        f = root / "data" / "cost_multipliers" / cost_multiplier_fn
        info["cost_multiplier_fn"] = cost_multiplier_fn
        info["cost_multiplier_file_present"] = f.exists()
        info["cost_multiplier_sha256"] = hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None
    return info


def check_powergenome(s: dict, s0: dict) -> dict:
    """Once per build: record the imported PowerGenome; stop if s0_production.powergenome_commit is set and not met."""
    want = (s0 or {}).get("powergenome_commit")
    key = (want, s.get("cost_multiplier_fn"))
    if key in _PG_CHECKED:
        return _PG_CHECKED[key]
    info = powergenome_info(s.get("cost_multiplier_fn"))
    logger.info("PowerGenome imported from %s (commit %s%s; repo submodule: %s; %s present: %s)", info["root"],
                info["commit"], ", uncommitted changes" if info["dirty"] else "", info["is_repo_submodule"],
                info.get("cost_multiplier_fn"), info.get("cost_multiplier_file_present"))
    if want:
        if not info["commit"] or not str(info["commit"]).startswith(str(want)):
            raise ValueError(
                f"s0_production.powergenome_commit is {want} but PowerGenome was imported from {info['root']} at "
                f"commit {info['commit']}. Run the build with PYTHONPATH=<worktree>/PowerGenome (git submodule update "
                f"first; VM_RECIPES.md recipe L step 6)")
        if info.get("cost_multiplier_fn") and not info.get("cost_multiplier_file_present"):
            raise ValueError(f"PowerGenome at {info['root']} has no data/cost_multipliers/{info['cost_multiplier_fn']}")
    _PG_CHECKED[key] = info
    return info


def write_provenance(folder: Path, s: dict, s0: dict, log) -> None:
    info = check_powergenome(s, s0)
    Path(folder, PROVENANCE).write_text(json.dumps(info, indent=2))
    log(f"PowerGenome: {info['root']} at {info['commit']}" + (" (uncommitted changes)" if info["dirty"] else "")
        + f"; repo submodule {info['is_repo_submodule']}; {PROVENANCE}")


# ---- cluster cache --------------------------------------------------------------------------------------------------
def cache_dir(s: dict) -> Path:
    base = s.get("input_folder") or (REPO / "pg/extra_inputs")
    return Path(base) / "cluster_assignments"


def fingerprint(s0: dict) -> dict | None:
    """What the clusters depend on beyond v3.1: the repaired PV file and the offshore split (None when both are off)."""
    from s0_workflow import production as p
    out = {}
    pv = p.pv_repair_settings(s0)
    if pv["mode"] != "off":
        out["pv_profile_repair"] = {"profiles_dir": str(pv["profiles_dir"]), "output_sha256": pv.get("output_sha256")}
    off = p.offshore_fix_settings(s0)
    if off["mode"] != "off":
        from s0_workflow import offshore_groups as og
        mf = og.meta_dir(p._repo_path(off["resource_groups_dir"])) / og.MANIFEST
        out["offshore_filter_fix"] = {"resource_groups_dir": str(off["resource_groups_dir"]),
                                      "manifest_sha256": hashlib.sha256(mf.read_bytes()).hexdigest()
                                      if mf.exists() else None}
    return out or None


def check_cluster_cache(s: dict, s0: dict, case=None, year=None) -> None:
    d = cache_dir(s)
    fp = fingerprint(s0)
    entries = [p for p in d.iterdir() if p.name != MARKER] if d.exists() else []
    mk = d / MARKER
    have = json.loads(mk.read_text()) if mk.exists() else None
    move = (f"Move it aside before this build (e.g. rename {d} to {d.name}.<label>); PowerGenome rebuilds the clusters "
            f"(VM_RECIPES.md recipe L step 4).")
    if fp is None:
        if have is not None:
            raise ValueError(f"cluster cache {d} was made by a v3.2 build (PV repair / offshore split; {MARKER}) and "
                             f"this build has both off. {move}")
        return
    if have == fp:
        return
    if have is None and not entries:
        d.mkdir(parents=True, exist_ok=True)
        mk.write_text(json.dumps(fp, indent=2))
        logger.info("s0_production %s/%s: new cluster cache %s for the v3.2 data (%s)", case, year, d, MARKER)
        return
    why = (f"{len(entries)} entries made without the v3.2 data (no {MARKER})" if have is None
           else f"made for other data ({MARKER}: {have}, this build: {fp})")
    raise ValueError(f"cluster cache {d}: {why}. {move}")
