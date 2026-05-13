"""Patch carbon_policies_regional_va.csv in all RGGI input folders with new VA fraction."""
import pandas as pd
from pathlib import Path

VA_FRACTION = 0.25
VA_ZONES = {"p99", "p100", "p118", "p124"}

base = Path("switch/in")
folders = [
    base / "2028" / "s4x1_edf_med",
    base / "2028" / "s4x1_icf",
    base / "2030" / "s4x1_edf_med",
    base / "2030" / "s4x1_icf",
    base / "2035" / "s4x1_edf_med",
    base / "2035" / "s4x1_icf",
    base / "foresight" / "s4x1_edf_med",
    base / "foresight" / "s4x1_icf",
]

for folder in folders:
    f = folder / "carbon_policies_regional_va.csv"
    if not f.exists():
        print(f"MISSING: {f}")
        continue

    df = pd.read_csv(f)
    ets1 = df[df["CO2_PROGRAM"] == "ETS 1"]

    for period, grp in ets1.groupby("PERIOD"):
        base_cap = grp.loc[~grp["LOAD_ZONE"].isin(VA_ZONES), "carbon_cap_tco2_per_yr"].sum()
        new_va_per_zone = base_cap * VA_FRACTION / (1 - VA_FRACTION) / len(VA_ZONES)
        mask = df["CO2_PROGRAM"].eq("ETS 1") & df["PERIOD"].eq(period) & df["LOAD_ZONE"].isin(VA_ZONES)
        old_val = df.loc[mask, "carbon_cap_tco2_per_yr"].iloc[0]
        df.loc[mask, "carbon_cap_tco2_per_yr"] = new_va_per_zone
        print(f"  {folder.parts[-2]}/{folder.name} {period}: {old_val:,.2f} -> {new_va_per_zone:,.2f} per zone "
              f"(total {base_cap + 4*new_va_per_zone:,.0f})")

    df.to_csv(f, index=False)
