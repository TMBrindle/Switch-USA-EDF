"""
Cap Derivation Methodology
===========================
Reproduces the two build-rate cap methods documented in the SWITCH Federal
Policy Scenarios technical note (Section 2: Cap Derivation Methodology):

  Method 1 - Quadratic Trend
      A 2nd-degree polynomial fit to NATIONAL annual capacity.
      Wind is fit over 2009-2025 (full series).
      Solar is fit over 2015-2025 only (2009-2014 excluded: that period was
      slow/flat and flattens the fitted curve - see technical note Sec 2.1
      for the R^2 comparison that motivates the shorter window).

  Method 2 - Implied Rate (trimmed top-quartile state growth)
      1. Fit the same quadratic procedure independently to each STATE's own
         capacity history (same fit windows as Method 1).
      2. Convert each state's fit into an annualized growth rate for two
         five-year periods: 2025->2030 and 2030->2035.
      3. Trim the states in the top and bottom 10% of that rate (removes
         small-base outliers in both directions).
      4. Average the top 25% of what remains -> the Implied Rate for that
         period.
      5. Apply the two resulting rates to the actual 2025 national total,
         compounding annually.

Both methods use only the historical EIA-860 capacity data in
cap_derivation_input_data.csv (long format: state, year, technology,
capacity_gw) - no policy, credit-status, or SWITCH model output is involved
in the cap derivation itself.

Usage:
    python cap_derivation_methodology.py [path_to_csv]
    (defaults to cap_derivation_input_data.csv in the same directory)

Reproducibility note:
    Running this against cap_derivation_input_data.csv reproduces the Solar
    cap values in the technical note EXACTLY (243.7 / 321.5 / 566.6 GW;
    22.28% / 14.84%). Wind comes out very close but not bit-identical
    (e.g. 198.3 vs. 198.2 GW at 2028; 9.50% vs. 9.10% for the 2026-2030
    Implied Rate) - the 2009 national wind total in this extract (34.53 GW)
    differs slightly from the value originally used (34.69 GW), and a
    17-point quadratic fit is sensitive enough to that small an early-year
    difference to shift the fitted coefficients by a similar margin. Solar's
    fit window (2015-2025) doesn't touch that period, which is why it isn't
    affected. The gap is well under 1% at every projected value and does
    not change any conclusion in the technical note, but it means this
    script's Wind output should be read as a close reproduction rather than
    a byte-identical one against the published figures.
"""

import sys
import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# 0. Load data
# ----------------------------------------------------------------------

def load_data(path):
    df = pd.read_csv(path)
    wind = df[df.technology == 'Wind'].pivot(index='state', columns='year', values='capacity_gw')
    solar = df[df.technology == 'Solar'].pivot(index='state', columns='year', values='capacity_gw')
    return wind, solar


FIT_WINDOW = {'Wind': (2009, 2025), 'Solar': (2015, 2025)}


# ----------------------------------------------------------------------
# Method 1 - National Quadratic Trend
# ----------------------------------------------------------------------

def fit_quadratic(years, values):
    """Least-squares 2nd-degree fit. x = year - years[0]."""
    x = np.array(years) - years[0]
    coeffs = np.polyfit(x, values, 2)          # returns [a, b, c] for a*x^2+b*x+c
    pred = np.polyval(coeffs, x)
    ss_res = np.sum((values - pred) ** 2)
    ss_tot = np.sum((values - np.mean(values)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return coeffs, r2


def quad_national_trend(wind_df, solar_df):
    results = {}
    for tech, df in [('Wind', wind_df), ('Solar', solar_df)]:
        y0, y1 = FIT_WINDOW[tech]
        years = list(range(y0, y1 + 1))
        national = df[years].sum(axis=0).values.astype(float)
        coeffs, r2 = fit_quadratic(years, national)

        def project(year, coeffs=coeffs, base_year=y0):
            x = year - base_year
            return np.polyval(coeffs, x)

        results[tech] = {
            'coeffs': coeffs, 'r2': r2, 'fit_start': y0,
            'proj_2028': project(2028), 'proj_2030': project(2030), 'proj_2035': project(2035),
        }
    return results


# ----------------------------------------------------------------------
# Method 2 - Implied Rate (trimmed top-quartile state growth)
# ----------------------------------------------------------------------

def annualized_rate(start_val, end_val, n_years):
    if start_val is None or end_val is None or start_val <= 0 or end_val < 0:
        return None
    return (end_val / start_val) ** (1.0 / n_years) - 1.0


def state_implied_rates(df, tech):
    """Per-state quadratic fit -> annualized 2025->2030 and 2030->2035 rates."""
    y0, y1 = FIT_WINDOW[tech]
    years = list(range(y0, y1 + 1))
    rows = []
    for state in df.index:
        series = df.loc[state, years].values.astype(float)
        if np.all(series == 0):
            continue
        coeffs, _ = fit_quadratic(years, series)

        def project(year, coeffs=coeffs, base_year=y0):
            x = year - base_year
            return np.polyval(coeffs, x)

        actual_2025 = series[years.index(2025)] if 2025 in years else project(2025)
        quad_2030 = project(2030)
        quad_2035 = project(2035)

        r_2530 = annualized_rate(actual_2025, quad_2030, 5)
        r_3035 = annualized_rate(quad_2030, quad_2035, 5)
        rows.append({'state': state, 'rate_2025_2030': r_2530, 'rate_2030_2035': r_3035})
    return pd.DataFrame(rows)


def trimmed_top_quartile_mean(rates, trim_frac=0.10, top_frac=0.25):
    """Drop top/bottom `trim_frac` by value, then average the top `top_frac`
    of what remains. `rates` is a 1-D array/Series of per-state rates
    (NaNs already dropped by the caller)."""
    s = pd.Series(rates).dropna().sort_values().reset_index(drop=True)
    n = len(s)
    trim_n = round(n * trim_frac)
    middle = s.iloc[trim_n: n - trim_n] if trim_n > 0 else s
    m = len(middle)
    top_n = round(m * top_frac)
    top = middle.sort_values(ascending=False).head(top_n)
    return top.mean(), n, trim_n, m, top_n


def implied_rate_method(wind_df, solar_df):
    results = {}
    for tech, df in [('Wind', wind_df), ('Solar', solar_df)]:
        state_rates = state_implied_rates(df, tech)
        rate_2530, n1, trim1, m1, topn1 = trimmed_top_quartile_mean(state_rates['rate_2025_2030'])
        rate_3035, n2, trim2, m2, topn2 = trimmed_top_quartile_mean(state_rates['rate_2030_2035'])
        results[tech] = {
            'rate_2026_2030': rate_2530, 'rate_2031_2035': rate_3035,
            'n_states_period1': n1, 'n_states_period2': n2,
            'state_rates': state_rates,
        }
    return results


def apply_implied_rate(national_2025, rate_2530, rate_3035):
    """Compound the two rates onto the actual 2025 national total."""
    levels = {2025: national_2025}
    for y in range(2026, 2031):
        levels[y] = levels[y - 1] * (1 + rate_2530)
    for y in range(2031, 2036):
        levels[y] = levels[y - 1] * (1 + rate_3035)
    return levels


# ----------------------------------------------------------------------
# Scenario cap construction (S0/S1 vs S2, per technical note Sec 3 & 5)
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main(csv_path):
    wind_df, solar_df = load_data(csv_path)

    nat_2025 = {'Wind': wind_df[2025].sum(), 'Solar': solar_df[2025].sum()}

    print("=" * 70)
    print("METHOD 1 - NATIONAL QUADRATIC TREND")
    print("=" * 70)
    quad = quad_national_trend(wind_df, solar_df)
    for tech in ['Wind', 'Solar']:
        r = quad[tech]
        a, b, c = r['coeffs']
        print(f"\n{tech}:  Capacity(x) = {a:.4f}x^2 + {b:.3f}x + {c:.2f}"
              f"   (x = year - {r['fit_start']},  R^2 = {r['r2']:.3f})")
        print(f"  Projected 2028: {r['proj_2028']:.1f} GW")
        print(f"  Projected 2030: {r['proj_2030']:.1f} GW")
        print(f"  Projected 2035: {r['proj_2035']:.1f} GW")

    print("\n" + "=" * 70)
    print("METHOD 2 - IMPLIED RATE (TRIMMED TOP-QUARTILE STATE GROWTH)")
    print("=" * 70)
    implied = implied_rate_method(wind_df, solar_df)
    implied_levels = {}
    for tech in ['Wind', 'Solar']:
        r = implied[tech]
        print(f"\n{tech}:")
        print(f"  2026-2030 rate: {r['rate_2026_2030']*100:.2f}%   (n={r['n_states_period1']} states)")
        print(f"  2031-2035 rate: {r['rate_2031_2035']*100:.2f}%   (n={r['n_states_period2']} states)")
        levels = apply_implied_rate(nat_2025[tech], r['rate_2026_2030'], r['rate_2031_2035'])
        implied_levels[tech] = levels
        print(f"  Applied to national 2025 base ({nat_2025[tech]:.1f} GW):")
        print(f"    2030: {levels[2030]:.1f} GW   2035: {levels[2035]:.1f} GW")

    print("\n" + "=" * 70)
    print("SCENARIO CAPS (S0/S1 = Quadratic Trend; S2 = Quadratic through 2028,")
    print("then Implied Rate from 2029 - see technical note Sec 5.2)")
    print("=" * 70)
    for tech in ['Wind', 'Solar']:
        r = quad[tech]
        cap_2028 = r['proj_2028']
        s2 = {2028: cap_2028}
        # 2029-2030 at the 2026-2030 implied rate, 2031-2035 at the 2031-2035 rate
        rate1 = implied[tech]['rate_2026_2030']
        rate2 = implied[tech]['rate_2031_2035']
        val = cap_2028
        for y in [2029, 2030]:
            val *= (1 + rate1)
        s2[2030] = val
        for y in range(2031, 2036):
            val *= (1 + rate2)
        s2[2035] = val
        print(f"\n{tech}:")
        print(f"  S0/S1 cap:  2028={r['proj_2028']:.1f}  2030={r['proj_2030']:.1f}  2035={r['proj_2035']:.1f}")
        print(f"  S2 cap:     2028={s2[2028]:.1f}  2030={s2[2030]:.1f}  2035={s2[2035]:.1f}")


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'cap_derivation_input_data.csv'
    main(path)
