"""
Compare RGGI vs no-RGGI demand sensitivity runs.
True leakage = non-RGGI_VA emissions (RGGI run) - non-RGGI_VA emissions (noRGGI run).
"""
import pandas as pd
from pathlib import Path

ROOT   = Path(__file__).parent.parent
SWITCH = Path(__file__).parent
IN_DIR = SWITCH / "in/2028/s4x1_edf_med"

hier     = pd.read_csv(ROOT / "hierarchy.csv")
zone_rv  = hier.set_index("ba")["rggi_va"].to_dict()
zone_st  = hier.set_index("ba")["st"].to_dict()
RGGI_STATES = {"CT","DE","MA","MD","ME","NH","NJ","NY","PA","RI","VT","VA"}

gi = pd.read_csv(IN_DIR / "gen_info.csv")
gi["rggi_va"]  = gi["gen_load_zone"].map(zone_rv)
gi["in_rggi"]  = gi["gen_load_zone"].apply(lambda z: zone_st.get(z,"") in RGGI_STATES)

MULTS = [1.00, 1.05, 1.10, 1.12, 1.15, 1.20]

def tag(m): return f"d{int(round(m*100)):03d}"

def load_dispatch(rel_path):
    d = pd.read_csv(SWITCH / "out" / rel_path / "dispatch.csv")
    d = d.merge(gi[["GENERATION_PROJECT","rggi_va","in_rggi"]],
                left_on="generation_project", right_on="GENERATION_PROJECT", how="left")
    return d

def co2_by_region(disp):
    rv   = disp[disp["rggi_va"]=="RGGI_VA"]["DispatchEmissions_tCO2_per_typical_yr"].sum()/1e6
    nrv  = disp[(disp["in_rggi"]) & (disp["rggi_va"]!="RGGI_VA")]["DispatchEmissions_tCO2_per_typical_yr"].sum()/1e6
    nrggi= disp[~disp["in_rggi"]]["DispatchEmissions_tCO2_per_typical_yr"].sum()/1e6
    return rv, nrv, nrggi

def load_prices(rel_path):
    f = SWITCH / "out" / rel_path / "carbon_program_clearing_prices.csv"
    return pd.read_csv(f)

# ── Collect data ──────────────────────────────────────────────────────────────
rows = []
for m in MULTS:
    t = tag(m)
    rggi_path   = f"RGGI_gzr95_mrp_demand/{t}/s4x1_edf_med/nsp_va"   if m > 1.00 else "RGGI_gzr95_mrp/2028/s4x1_edf_med/nsp_va"
    norggi_path = f"RGGI_gzr95_noRGGI_demand/{t}/s4x1_edf_med/nsp_va"

    d_rggi   = load_dispatch(rggi_path)
    d_norggi = load_dispatch(norggi_path)

    rv_r,  nrv_r,  nr_r  = co2_by_region(d_rggi)
    rv_n,  nrv_n,  nr_n  = co2_by_region(d_norggi)

    prices = load_prices(rggi_path)
    ets1   = prices[prices["CO2_PROGRAM"]=="ETS 1"].iloc[0]

    rows.append(dict(
        mult       = m,
        ets1_price = ets1["clearing_price_dollar_per_tco2"],
        t1_purch   = ets1["ccr_tier1_purchases_tco2"]/1e6,
        # RGGI run
        rv_rggi    = rv_r,
        nrv_rggi   = nrv_r,
        nr_rggi    = nr_r,
        total_rggi = rv_r + nrv_r + nr_r,
        # noRGGI run
        rv_norggi  = rv_n,
        nrv_norggi = nrv_n,
        nr_norggi  = nr_n,
        total_norggi = rv_n + nrv_n + nr_n,
        # leakage = non-RGGI_VA emissions increase caused by RGGI
        leak_nrv   = nrv_r  - nrv_n,
        leak_nr    = nr_r   - nr_n,
        leak_total = (nrv_r + nr_r) - (nrv_n + nr_n),
        # RGGI abatement = RGGI_VA reduction from no-RGGI counterfactual
        abatement  = rv_n - rv_r,
    ))

df = pd.DataFrame(rows)

# ── Print ─────────────────────────────────────────────────────────────────────
print("=== RGGI run: CO2 by region (Mt/yr) ===")
print(f"{'Mult':>5}  {'$/mt':>6}  {'T1 Mt':>6}  {'RGGI_VA':>8}  {'RGGI non-VA':>12}  {'Non-RGGI':>10}  {'Total':>8}")
print("-"*70)
for _, r in df.iterrows():
    print(f"{r.mult:>5.2f}  {r.ets1_price:>6.2f}  {r.t1_purch:>6.2f}  "
          f"{r.rv_rggi:>8.2f}  {r.nrv_rggi:>12.2f}  {r.nr_rggi:>10.2f}  {r.total_rggi:>8.2f}")

print("\n=== No-RGGI counterfactual: CO2 by region (Mt/yr) ===")
print(f"{'Mult':>5}  {'RGGI_VA':>8}  {'RGGI non-VA':>12}  {'Non-RGGI':>10}  {'Total':>8}")
print("-"*50)
for _, r in df.iterrows():
    print(f"{r.mult:>5.2f}  {r.rv_norggi:>8.2f}  {r.nrv_norggi:>12.2f}  "
          f"{r.nr_norggi:>10.2f}  {r.total_norggi:>8.2f}")

print("\n=== Leakage analysis: RGGI minus no-RGGI counterfactual (Mt/yr) ===")
print(f"{'Mult':>5}  {'Abatement':>10}  {'Leak RGGI':>10}  {'Leak non-RGGI':>14}  {'Leak total':>11}  {'Leak rate':>10}")
print("-"*70)
for _, r in df.iterrows():
    leak_rate = r.leak_total / r.abatement * 100 if r.abatement > 0.01 else float("nan")
    print(f"{r.mult:>5.2f}  {r.abatement:>10.2f}  {r.leak_nrv:>10.2f}  "
          f"{r.leak_nr:>14.2f}  {r.leak_total:>11.2f}  "
          f"{leak_rate:>9.1f}%")
print("  Abatement = RGGI_VA reduction vs counterfactual")
print("  Leak rate = total leaked emissions / abatement")
