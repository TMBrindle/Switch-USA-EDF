"""Check that built wind capacity does not exceed MaxCapTag_WindGrowth caps.

Usage: python check_track_b_wind_caps.py <inputs_dir> <outputs_dir>
"""
import sys
import csv
from pathlib import Path


def load_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    inputs_dir = Path(sys.argv[1])
    outputs_dir = Path(sys.argv[2])

    gen_info = {row["GENERATION_PROJECT"]: row for row in load_csv(inputs_dir / "gen_info.csv")}
    build_gen = load_csv(outputs_dir / "BuildGen.csv")
    max_cap = load_csv(inputs_dir / "max_cap_requirements.csv")

    # index max_cap_generators.csv: which gens count toward each MAX_CAP_PROGRAM tag
    tag_gens = {}  # tag -> set of gens
    for row in load_csv(inputs_dir / "max_cap_generators.csv"):
        tag_gens.setdefault(row["MAX_CAP_PROGRAM"], set()).add(row["MAX_CAP_GEN"])

    # sum built capacity (BuildGen, all vintages) per generator
    built_by_gen = {}
    for row in build_gen:
        g = row["GEN_BLD_YRS_1"]
        mw = float(row["BuildGen"])
        built_by_gen[g] = built_by_gen.get(g, 0.0) + mw

    print(f"{'tag':35s} {'period':>7s} {'cap_mw':>12s} {'built_mw':>12s} {'ok':>4s}")
    all_ok = True
    for row in max_cap:
        tag = row["MAX_CAP_PROGRAM"]
        period = row["PERIOD"]
        cap_mw = float(row["max_cap_mw"])
        gens = tag_gens.get(tag, set())
        built_mw = sum(built_by_gen.get(g, 0.0) for g in gens)
        ok = built_mw <= cap_mw + 1e-3
        all_ok = all_ok and ok
        print(f"{tag:35s} {period:>7s} {cap_mw:12.1f} {built_mw:12.1f} {'OK' if ok else 'FAIL':>4s}")

    print("\nAll caps respected" if all_ok else "\nSOME CAPS VIOLATED")


if __name__ == "__main__":
    main()
