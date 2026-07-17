"""Check RGGI carbon-cap clearing prices and gen_zone_ratio group constraints.

Usage: python check_track_a_rggi_genzoneratio.py <outputs_dir>
"""
import sys
import csv
from pathlib import Path


def load_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    outputs_dir = Path(sys.argv[1])

    print("=== carbon_program_clearing_prices.csv ===")
    clearing = load_csv(outputs_dir / "carbon_program_clearing_prices.csv")
    all_ok = True
    for row in clearing:
        floor = float(row["floor_price_dollar_per_tco2"])
        price = float(row["clearing_price_dollar_per_tco2"])
        revenue = float(row["auction_revenue_dollar_per_yr"])
        ok = price >= floor - 1e-6 and revenue >= -1e-6
        all_ok = all_ok and ok
        print(
            f"{row['CO2_PROGRAM']:10s} {row['PERIOD']:>6s} "
            f"floor={floor:8.2f} clearing={price:8.2f} revenue={revenue:14.0f} "
            f"{'OK' if ok else 'FAIL'}"
        )

    print("\n=== gen_zone_ratio_summary.csv (group rows) ===")
    gzr = load_csv(outputs_dir / "gen_zone_ratio_summary.csv")
    groups = [row for row in gzr if row["TYPE"] == "group"]
    n_violations = 0
    for row in groups:
        actual = float(row["actual_annual_ratio"])
        min_r = row["min_annual_ratio"]
        max_r = row["max_annual_ratio"]
        ok = True
        if min_r:
            ok = ok and actual >= float(min_r) - 1e-6
        if max_r:
            ok = ok and actual <= float(max_r) + 1e-6
        if not ok:
            n_violations += 1
            print(f"FAIL {row['LOAD_ZONE']:35s} actual={actual:.4f} min={min_r} max={max_r}")
    print(f"{len(groups)} group rows checked, {n_violations} violations")

    print("\nAll checks passed" if all_ok and n_violations == 0 else "\nSOME CHECKS FAILED")


if __name__ == "__main__":
    main()
