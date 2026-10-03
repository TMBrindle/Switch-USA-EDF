"""
Compare LBNL pipeline caps vs predetermined capacity per region.
Shows where the floor was binding (pipeline < predetermined) and where it wasn't.
Also shows what pipeline headroom above predetermined exists.
"""
import pandas as pd, yaml
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Load LBNL queue data
lbnl_q = pd.read_csv(ROOT / "docs/analysis/RGGI/lbnl_queue_by_state.csv")
NON_IA_FRACTION = 0.25
lbnl_wind28 = (
    lbnl_q[(lbnl_q["tech"] == "OnshoreWind") & (lbnl_q["year_cutoff"] == 2028)]
    .groupby("transreg")[["queue_mw", "ia_executed_mw"]].sum()
)
lbnl_wind28["non_ia_mw"] = lbnl_wind28["queue_mw"] - lbnl_wind28["ia_executed_mw"]
lbnl_wind28["pipeline_new_mw"] = (
    lbnl_wind28["ia_executed_mw"] + NON_IA_FRACTION * lbnl_wind28["non_ia_mw"]
)

# Load baselines from scenario_management
with open(ROOT / "pg/settings/scenario_management.yml") as f:
    sm = yaml.safe_load(f)["settings_management"]

yr_caps = sm.get(2028, {}).get("all_cases", {}).get("MaxCapReq", {})
print("Pipeline cap = baseline + pipeline_new from make_emission_policies.py:")
print("(Before floor was applied by update_max_cap_files.py)")
print()

for tag, cfg in sorted(yr_caps.items()):
    if not tag.startswith("MaxCapTag_WindGrowth_"):
        continue
    transreg = tag.replace("MaxCapTag_WindGrowth_", "")
    max_mw = cfg.get("max_mw", 0)
    print(f"  {tag}: cap_in_yaml={max_mw:.1f}")

print()

# Load case predetermined totals from diagnostic output
case_dir = ROOT / "switch/in/2028/s4x1_edf_med"
max_cap_gens = pd.read_csv(case_dir / "max_cap_generators.csv")
gen_build_pre = pd.read_csv(case_dir / "gen_build_predetermined.csv")
gen_build_pre["build_gen_predetermined"] = pd.to_numeric(
    gen_build_pre["build_gen_predetermined"], errors="coerce"
).fillna(0)
max_cap_reqs = pd.read_csv(case_dir / "max_cap_requirements.csv")
max_cap_reqs = max_cap_reqs[max_cap_reqs["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")]

# Get pipeline new from LBNL
pipeline = lbnl_wind28["pipeline_new_mw"].to_dict()

print(f"{'Tag':<45} {'YAML_cap':>10} {'Pred_total':>12} {'Pipe_new':>10} {'Headroom':>10}")
print("-" * 92)
for _, row in max_cap_reqs.sort_values("MAX_CAP_PROGRAM").iterrows():
    tag = row["MAX_CAP_PROGRAM"]
    yaml_cap = row["max_cap_mw"]
    transreg = tag.replace("MaxCapTag_WindGrowth_", "")

    tagged = max_cap_gens[max_cap_gens["MAX_CAP_PROGRAM"] == tag]["MAX_CAP_GEN"].tolist()
    pred = gen_build_pre[gen_build_pre["GENERATION_PROJECT"].isin(tagged)]["build_gen_predetermined"].sum()

    pipe_new = pipeline.get(transreg, 0)
    headroom = yaml_cap - pred
    floor_flag = " << floor applied" if pred >= yaml_cap - 0.5 else ""

    print(f"{tag:<45} {yaml_cap:>10.1f} {pred:>12.1f} {pipe_new:>10.1f} {headroom:>10.1f}{floor_flag}")
