"""
Regenerate max_cap_generators.csv and max_cap_requirements.csv for all Switch
input cases, incorporating the new per-transreg MaxCapTag_WindGrowth_* tags
added to regional_resource_tags.yml and scenario_management.yml by
make_emission_policies.py.

Does NOT re-run the full pg_to_switch pipeline — only touches these two files.

Usage:
  python pg/update_max_cap_files.py
"""
from pathlib import Path
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
SWITCH_IN = ROOT / "switch" / "in"
SETTINGS_DIR = ROOT / "pg" / "settings"

# ── Load settings YAMLs ───────────────────────────────────────────────────────
def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)

regional_tags = load_yaml(SETTINGS_DIR / "regional_resource_tags.yml")["regional_tag_values"]
scenario_mgmt = load_yaml(SETTINGS_DIR / "scenario_management.yml")["settings_management"]

# ── Build zone → {tag: {tech: 1}} lookup from regional_resource_tags.yml ─────
# regional_tags structure: {zone: {tag_name: {tech_key: 1, ...}}}
def get_zone_tags(zone):
    """Return dict of {tag_name: set_of_tech_keys} for a zone."""
    result = {}
    for tag, techs in regional_tags.get(zone, {}).items():
        if tag.startswith("MaxCapTag_"):
            result[tag] = set(techs.keys()) if isinstance(techs, dict) else set()
    return result

# ── Discover all input case folders ──────────────────────────────────────────
YEARS = [2028, 2030, 2035]
CASES = ["s4x1_edf_med", "s4x1_icf", "s4x5_edf_med", "s4x5_icf",
         "s20x1_edf_med", "s20x1_icf"]

updated = []
skipped = []

# Per-case effective caps from the previous model year, used to enforce
# monotonicity: each year's cap >= the prior year's effective cap (post-floor).
# Keyed by case name → {tag: effective_mw}.
prev_effective_caps: dict[str, dict[str, float]] = {}

for year in YEARS:
    year_dir = SWITCH_IN / str(year)
    if not year_dir.exists():
        continue

    # Build max_cap_mw lookup for this year from scenario_management
    # Collect MaxCapReq entries from the per-year settings
    max_cap_by_tag = {}
    for yr_key, yr_settings in scenario_mgmt.items():
        if yr_key == "all_years" or int(yr_key) != year:
            continue
        for case_key, case_settings in yr_settings.items():
            if case_key != "all_cases":
                continue
            for tag, tag_cfg in case_settings.get("MaxCapReq", {}).items():
                if tag.startswith("MaxCapTag_"):
                    max_cap_by_tag[tag] = tag_cfg.get("max_mw", 0)

    for case in CASES:
        case_dir = year_dir / case
        if not case_dir.exists():
            skipped.append(f"{year}/{case}")
            continue

        gen_info_f = case_dir / "gen_info.csv"
        if not gen_info_f.exists():
            skipped.append(f"{year}/{case} (no gen_info.csv)")
            continue

        gen_info = pd.read_csv(gen_info_f, usecols=["GENERATION_PROJECT", "gen_tech", "gen_load_zone"])

        # ── Build max_cap_generators.csv ──────────────────────────────────────
        rows = []
        for _, row in gen_info.iterrows():
            gen = row["GENERATION_PROJECT"]
            zone = row["gen_load_zone"]
            tech = row["gen_tech"]
            for tag, tech_keys in get_zone_tags(zone).items():
                # Match if tech starts with any key (handles ATB names like
                # LandbasedWind_Class3_Moderate matching key "LandbasedWind")
                if any(tech.startswith(key) for key in tech_keys):
                    rows.append({"MAX_CAP_PROGRAM": tag, "MAX_CAP_GEN": gen})

        # Only include regional WindGrowth_ generator entries for programs that
        # have a requirement for this year.  If a program has generators but no
        # requirement, Pyomo will reject the generator as outside MAX_CAP_PROGRAMS
        # (which is derived solely from max_cap_requirements.csv).
        valid_programs = set(max_cap_by_tag.keys())
        rows = [r for r in rows if r["MAX_CAP_PROGRAM"] in valid_programs]

        # Preserve existing entries for non-WindGrowth_ tags
        existing_f = case_dir / "max_cap_generators.csv"
        if existing_f.exists():
            existing = pd.read_csv(existing_f)
            # Keep entries for programs not covered by regional_resource_tags.yml
            other = existing[~existing["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")]
            new_rows = pd.DataFrame(rows)
            combined = pd.concat([other, new_rows], ignore_index=True).drop_duplicates()
        else:
            combined = pd.DataFrame(rows)

        combined.to_csv(case_dir / "max_cap_generators.csv", index=False)

        # ── Compute predetermined capacity floor per tag ──────────────────────
        # The cap must not fall below total predetermined builds, otherwise the
        # model is immediately infeasible before the optimiser makes any decisions.
        pred_f = case_dir / "gen_build_predetermined.csv"
        pred_floor = {}
        if pred_f.exists() and not combined.empty:
            pred = pd.read_csv(pred_f)
            pred["build_gen_predetermined"] = pd.to_numeric(
                pred["build_gen_predetermined"], errors="coerce"
            ).fillna(0)
            # Sum predetermined capacity for each tagged generator, per tag
            tagged = combined.rename(columns={"MAX_CAP_GEN": "GENERATION_PROJECT"})
            pred_tagged = pred.merge(tagged, on="GENERATION_PROJECT", how="inner")
            pred_floor = (
                pred_tagged.groupby("MAX_CAP_PROGRAM")["build_gen_predetermined"]
                .sum()
                .to_dict()
            )

        # ── Build max_cap_requirements.csv ────────────────────────────────────
        req_rows = []
        if existing_f.exists():
            existing_req_f = case_dir / "max_cap_requirements.csv"
            if existing_req_f.exists():
                existing_req = pd.read_csv(existing_req_f)
                # Keep non-WindGrowth_ regional entries
                existing_req = existing_req[
                    ~existing_req["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")
                    | (existing_req["MAX_CAP_PROGRAM"] == "MaxCapTag_WindGrowth")
                ]
                req_rows_df = existing_req.copy()
            else:
                req_rows_df = pd.DataFrame(columns=["MAX_CAP_PROGRAM", "PERIOD", "max_cap_mw"])
        else:
            req_rows_df = pd.DataFrame(columns=["MAX_CAP_PROGRAM", "PERIOD", "max_cap_mw"])

        # Add regional WindGrowth_ requirements.
        # Apply two floors in priority order:
        #   1. Predetermined builds — cap must accommodate everything already committed.
        #   2. Prior model year's effective cap — caps are monotonically non-decreasing
        #      across years (2028 → 2030); a lower 2030 cap than 2028 would force the
        #      model to un-build capacity that existed in the prior period.
        new_reqs = []
        tagged_programs = set(combined["MAX_CAP_PROGRAM"].unique())
        case_prev = prev_effective_caps.get(case, {})
        if case not in prev_effective_caps:
            prev_effective_caps[case] = {}
        for tag, max_mw in max_cap_by_tag.items():
            if tag in tagged_programs:
                pred_floor_mw = pred_floor.get(tag, 0)
                prior_yr_mw   = case_prev.get(tag, 0)
                effective_mw  = max(max_mw, pred_floor_mw, prior_yr_mw)
                reasons = []
                if effective_mw > max_mw:
                    if pred_floor_mw >= prior_yr_mw and pred_floor_mw > max_mw:
                        reasons.append(f"predetermined builds ({pred_floor_mw:.0f} MW)")
                    if prior_yr_mw > max_mw:
                        reasons.append(f"prior-year cap ({prior_yr_mw:.0f} MW)")
                    print(f"  [{year}/{case}] {tag}: cap raised {max_mw:.0f} -> {effective_mw:.0f} MW ({', '.join(reasons)})")
                prev_effective_caps[case][tag] = effective_mw
                new_reqs.append({"MAX_CAP_PROGRAM": tag, "PERIOD": year, "max_cap_mw": round(effective_mw, 1)})

        if new_reqs:
            req_rows_df = pd.concat(
                [req_rows_df, pd.DataFrame(new_reqs)], ignore_index=True
            ).drop_duplicates(subset=["MAX_CAP_PROGRAM", "PERIOD"])

        req_rows_df.to_csv(case_dir / "max_cap_requirements.csv", index=False)
        updated.append(f"{year}/{case}")

print("Updated:")
for u in updated:
    print(f"  {u}")
if skipped:
    print("Skipped (not found):")
    for s in skipped:
        print(f"  {s}")
