"""National natural gas and coal prices by S0 model year for each fuel_prices option against the current setting
(CHANGES §67). Writes s0_workflow/data/fuel/fuel_price_options_by_stage.csv and prints it.

Two measures per option and stage (2024 $/MMBtu, mean over the stage's years):
  path      the national path (steo_aeo options only; consumption-weighted by AEO's own national price);
  zone_avg  the zone prices weighted the same way for every option, hist5 included: each EMM region's AEO2026 2026
            power-sector consumption of the fuel, split equally over the region's zones (s0_workflow/specs/fuel/
            zone_emm.csv). hist5 / hist5_high_gas are the user_fuel_price values in pg/settings/scenario_management.yml
            (flat: every stage the same).

usage (repo root): python s0_workflow/scripts/compare_fuel_prices.py
"""
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import fuel_prices as fp  # noqa: E402

PERIODS = [(2028, 2026, 2028), (2030, 2029, 2030), (2035, 2031, 2035), (2040, 2036, 2040), (2045, 2041, 2045)]
HIST = ("hist5", "hist5_high_gas")


def weights(fuel: str, year: int = 2026) -> pd.Series:
    e = fp._data(fp.DEFAULTS, "aeo2026_power_fuel_emm.csv", dtype={"emm": str})
    e = e[(e.fuel == fuel) & (e.year == year)].set_index("emm").consumption_quads
    cw = pd.Series(fp.crosswalk())
    n = cw.value_counts()
    return cw.map(lambda r: e.get(r, 0.0) / n[r])


def table() -> pd.DataFrame:
    fpf = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"][
        "fuel_price_forecast"]
    rows = []
    per = pd.DataFrame(PERIODS, columns=["INVESTMENT_PERIOD", "period_start", "period_end"])
    for fuel in ("naturalgas", "coal"):
        w = weights(fuel)
        for h in HIST:
            zp = pd.Series(fpf[h]["user_fuel_price"][fuel])
            z = w.index.intersection(zp.index)
            v = float((zp[z] * w[z]).sum() / w[z].sum())
            rows += [{"option": h, "fuel": fuel, "period": p, "path": float("nan"), "zone_avg": v} for p, _, _ in PERIODS]
        for m in ("steo_aeo", "steo_aeo_low_supply", "steo_aeo_high_supply"):
            nat = fp.national_by_period(m, PERIODS).query("fuel == @fuel").set_index("period").price
            sp = fp.stage_prices(m, per).query("fuel == @fuel")
            for p, _, _ in PERIODS:
                s = sp[sp.period == p].set_index("zone").price
                z = w.index.intersection(s.index)
                rows.append({"option": m, "fuel": fuel, "period": p, "path": nat[p],
                             "zone_avg": float((s[z] * w[z]).sum() / w[z].sum())})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    t = table()
    t.round(4).to_csv(REPO / "s0_workflow/data/fuel/fuel_price_options_by_stage.csv", index=False)
    for fuel in ("naturalgas", "coal"):
        print(f"\n{fuel} (2024 $/MMBtu): path / zone-weighted average")
        x = t[t.fuel == fuel]
        print(x.pivot(index="option", columns="period", values="path").round(2).to_string())
        print(x.pivot(index="option", columns="period", values="zone_avg").round(2).to_string())
