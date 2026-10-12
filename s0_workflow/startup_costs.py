"""Start-up costs of new gas builds (V32-01; CHANGES §119; s0_production.startup_cost_fix). Changes results: for Ollie's
review.

PowerGenome gives a new build its start-up cost class from pg/settings/startup_costs.yml `new_build_startup_costs`,
matching each key with str.contains against the ATB technology name (generators.py:296, as the housekeeping review
found). The v3.1 keys `NaturalGas_CT` and `NaturalGas_CC` match the older ATB names (NaturalGas_CTAvgCF, _CCAvgCF) but
not the ones S0 builds, "NaturalGas_Combustion Turbine (F-Frame)_Moderate" and "NaturalGas_1-on-1 Combined Cycle
(H-Frame)_Moderate". So new CTs and CCs carry Start_Cost_per_MW = 0 (gen_startup_om), while existing CTs carry about
$145/MW-start and existing CCs about $112 (s4x1 fedpol inputs).

startup_cost_fix (off = v3.1): adds keys that match the current ATB names, without regex metacharacters (str.contains
reads keys as regular expressions, so "(H-Frame)" would be a group), and keeps the v3.1 keys for the older names:
    NaturalGas_Combustion Turbine  -> gas_large_ct
    NaturalGas_1-on-1 Combined Cycle -> gas_cc        (also its 95% / 97% CCS variants)
    NaturalGas_2-on-1 Combined Cycle -> gas_cc
    Coal_95%-CCS, Coal_99%-CCS       -> coal_supercritical (the CCS axes build them; v3.1 gives them no start cost)
`audit` lists, for every technology name in resources.yml (atb_new_gen, modified_atb_new_gen and the ATB names of the
technology groups), the keys that match it: none (no start cost), one, or several (ambiguous). The build check
(`check_new_gas`) stops the build if a new natural-gas build in gen_info.csv has gen_startup_om 0 while the fix is on.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[1]
FIXED_KEYS = {
    "NaturalGas_Combustion Turbine": "gas_large_ct",
    "NaturalGas_1-on-1 Combined Cycle": "gas_cc",
    "NaturalGas_2-on-1 Combined Cycle": "gas_cc",
    "Coal_95%-CCS": "coal_supercritical",       # built by the CCS axes (scenario_management.yml); no key in v3.1
    "Coal_99%-CCS": "coal_supercritical",
}


def enabled(s0: dict) -> bool:
    v = (s0 or {}).get("startup_cost_fix") or {}
    return bool(v.get("enabled")) if isinstance(v, dict) else bool(v)


def fixed_keys(v31: dict) -> dict:
    return {**(v31 or {}), **FIXED_KEYS}


def apply_settings(s: dict, s0: dict) -> list[str]:
    """In place for one case/year: the fixed new-build keys when the flag is on. Returns the keys added."""
    if not enabled(s0):
        return []
    old = dict(s.get("new_build_startup_costs") or {})
    s["new_build_startup_costs"] = fixed_keys(old)
    return [k for k in FIXED_KEYS if k not in old]


def known_technologies(resources: dict) -> list[str]:
    """ATB-style technology names (technology_tech-detail) the repo's resources.yml can build or names."""
    names = set()
    for t in resources.get("atb_new_gen") or []:
        names.add(f"{t[0]}_{t[1]}")
    for v in (resources.get("modified_atb_new_gen") or {}).values():
        if isinstance(v, dict) and v.get("new_technology"):
            names.add(f"{v['new_technology']}_{v.get('new_tech_detail', '')}")
    for v in (resources.get("cost_multiplier_technology_map") or {}).values():
        for n in (v if isinstance(v, list) else [v]):
            names.add(str(n))
    return sorted(names)


def scenario_technologies(scenario_management: dict) -> list[str]:
    """ATB technologies the scenario axes add (any atb_new_gen list under settings_management)."""
    names = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "atb_new_gen" and isinstance(v, list):
                    names.update(f"{e[0]}_{e[1]}" for e in v if isinstance(e, list) and len(e) >= 2)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(scenario_management)
    return sorted(names)


def audit(keys: dict, techs: list[str]) -> pd.DataFrame:
    """One row per technology: the keys whose str.contains (regex, as PowerGenome) matches it, and the class."""
    rows = []
    for t in techs:
        hit = [k for k in keys if re.search(k, t)]
        rows.append({"technology": t, "keys": ";".join(hit), "n_keys": len(hit),
                     "class": keys[hit[0]] if len(hit) == 1 else ("" if not hit else "AMBIGUOUS"),
                     "is_gas": t.startswith("NaturalGas"), "is_new_build_family": t.split("_")[0] in
                     ("NaturalGas", "Coal", "Nuclear")})
    return pd.DataFrame(rows)


def unmatched_keys(keys: dict, techs: list[str]) -> list[str]:
    return [k for k in keys if not any(re.search(k, t) for t in techs)]


def check_new_gas(folder: Path) -> pd.DataFrame:
    """New natural-gas builds (ATB names NaturalGas_*) with no start-up cost in gen_info.csv. Empty = pass."""
    gi = pd.read_csv(Path(folder) / "gen_info.csv", na_values=["."])
    if "gen_startup_om" not in gi:
        return gi.iloc[0:0]
    new_gas = gi[gi.gen_tech.astype(str).str.startswith("NaturalGas_")]
    return new_gas[pd.to_numeric(new_gas.gen_startup_om, errors="coerce").fillna(0) <= 0][
        ["GENERATION_PROJECT", "gen_tech", "gen_startup_om"]]


def write_check(folder: Path, s0: dict, log) -> None:
    """Build check (flag on): stop if any new gas build has gen_startup_om 0; log the count either way."""
    bad = check_new_gas(folder)
    if not enabled(s0):
        if len(bad):
            log(f"startup costs: {len(bad)} new gas projects have no start-up cost (v3.1 behaviour; "
                f"startup_cost_fix off): {sorted(bad.gen_tech.unique())}")
        return
    if len(bad):
        raise ValueError(f"startup_cost_fix: {len(bad)} new gas projects still have gen_startup_om 0 "
                         f"({sorted(bad.gen_tech.unique())}); check new_build_startup_costs against the ATB names")
    gi = pd.read_csv(Path(folder) / "gen_info.csv", na_values=["."])
    ng = gi[gi.gen_tech.astype(str).str.startswith("NaturalGas_")]
    if "gen_startup_om" not in gi or ng.empty:
        log("startup costs (startup_cost_fix): no new gas projects with gen_startup_om in gen_info.csv; nothing to check")
        return
    log("startup costs (startup_cost_fix): new gas start-up cost $/MW-start by technology: "
        + "; ".join(f"{t} {v:g}" for t, v in ng.groupby("gen_tech").gen_startup_om.max().items()))


def load_yml(name: str) -> dict:
    return yaml.safe_load(open(REPO / "pg/settings" / name))
