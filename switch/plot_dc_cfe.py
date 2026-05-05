#!/usr/bin/env python3
"""
plot_dc_cfe.py

Two figures focused on the CFE (clean energy) scenario variants:

  dc_cfe_ladder.png    — per-state CO₂ progression across all 9 variants
                         (BASE, SC/MC +/- CFE75/CFE100, FLEX+CFE100)
  dc_cfe_portfolio.png — onsite build composition for selected variants,
                         showing how the portfolio shifts under CFE requirements
"""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

SUMMARY_CSV = "dc_analysis_summary.csv"
MWH_PER_YEAR = 8_760_000

STATE_ORDER = ["CA", "TX", "VA", "GA", "AZ", "CO"]

# Variants to show and their display names
LADDER_VARIANTS = [
    ("BASE",           "BASE (no cap)"),
    ("SC",             "SC  (250 MW cap)"),
    ("SC_CFE75",       "SC + 75% CFE"),
    ("SC_CFE100",      "SC + 100% CFE"),
    ("MC",             "MC  (900 MW cap)"),
    ("MC_CFE75",       "MC + 75% CFE"),
    ("MC_CFE100",      "MC + 100% CFE"),
    ("FLEX_SC_CFE100", "FLEX-SC + 100% CFE"),
    ("FLEX_MC_CFE100", "FLEX-MC + 100% CFE"),
]

LADDER_COLORS = {
    "BASE":           "#555555",
    "SC":             "#4292c6",
    "SC_CFE75":       "#2171b5",
    "SC_CFE100":      "#08519c",
    "MC":             "#fb6a4a",
    "MC_CFE75":       "#de2d26",
    "MC_CFE100":      "#a50f15",
    "FLEX_SC_CFE100": "#6baed6",
    "FLEX_MC_CFE100": "#fc8d59",
}

LADDER_MARKERS = {
    "BASE":           "D",
    "SC":             "o",   "SC_CFE75":  "o",   "SC_CFE100":  "s",
    "MC":             "o",   "MC_CFE75":  "o",   "MC_CFE100":  "s",
    "FLEX_SC_CFE100": "^",
    "FLEX_MC_CFE100": "^",
}

# Portfolio figure
PORTFOLIO_VARIANTS = ["BASE", "SC_CFE100", "MC_CFE100", "FLEX_SC_CFE100"]
PORTFOLIO_LABELS = {
    "BASE":           "BASE",
    "SC_CFE100":      "SC+CFE100",
    "MC_CFE100":      "MC+CFE100",
    "FLEX_SC_CFE100": "FLEX-SC\n+CFE100",
}

TECH_COLORS = {
    "gas_cc_MW":  "#e6550d",
    "wind_MW":    "#31a354",
    "solar_MW":   "#fdae6b",
    "grid_draw":  "#9ecae1",
}
TECH_LABELS = {
    "gas_cc_MW":  "Gas CC (onsite)",
    "wind_MW":    "Wind (onsite)",
    "solar_MW":   "Solar (onsite)",
    "grid_draw":  "Grid draw",
}


def load():
    df = pd.read_csv(SUMMARY_CSV)
    df["co2_kgmwh"] = df["annual_co2_tonne"] * 1000 / MWH_PER_YEAR
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Figure 1: CFE Ladder — per-state CO₂ progression
# ─────────────────────────────────────────────────────────────────────────────

def fig_cfe_ladder(df):
    nv = len(LADDER_VARIANTS)
    variant_names = [v for v, _ in LADDER_VARIANTS]
    ylabels = [lbl for _, lbl in LADDER_VARIANTS]
    ypos = {v: i for i, v in enumerate(reversed(variant_names))}  # bottom=0

    fig, axes = plt.subplots(2, 3, figsize=(15, 9), sharey=True)
    fig.suptitle(
        "1 GW Data Center — CO₂ Intensity: Effect of CFE Requirements\n"
        "Diamonds = BASE (grid only benchmark)  |  lower = cleaner",
        fontsize=12, fontweight="bold", y=1.01,
    )

    for ax, state in zip(axes.flat, STATE_ORDER):
        rows = df[df["state"] == state].set_index("variant")

        base_co2 = float(rows.loc["BASE", "co2_kgmwh"]) if "BASE" in rows.index else None

        # Draw baseline reference line
        if base_co2 is not None:
            ax.axvline(base_co2, color="#cccccc", lw=1.2, ls="--", zorder=1)

        # Collect x positions for family spines
        sc_xs = []
        mc_xs = []

        for v, _ in LADDER_VARIANTS:
            if v not in rows.index:
                continue
            x = float(rows.loc[v, "co2_kgmwh"])
            y = ypos[v]
            ax.scatter(x, y,
                       color=LADDER_COLORS[v],
                       marker=LADDER_MARKERS[v],
                       s=80, zorder=5,
                       edgecolors="white", linewidths=0.5)
            if v.startswith("SC") or v == "SC":
                sc_xs.append((y, x))
            elif v.startswith("MC") or v == "MC":
                mc_xs.append((y, x))

        # Family spines (connect within SC and MC groups)
        for family_pts, col in [(sc_xs, "#4292c6"), (mc_xs, "#fb6a4a")]:
            if len(family_pts) >= 2:
                ys_pts, xs_pts = zip(*sorted(family_pts))
                ax.plot(xs_pts, ys_pts,
                        color=col, lw=1.0, alpha=0.45, zorder=2, ls="-")

        ax.set_yticks(range(nv))
        ax.set_yticklabels(list(reversed(ylabels)), fontsize=7.5)
        ax.set_xlabel("kg CO₂ / MWh", fontsize=9)
        ax.set_title(state, fontsize=12, fontweight="bold", pad=4)
        ax.grid(axis="x", alpha=0.2, lw=0.7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # Legend
    handles = [
        mpatches.Patch(color=LADDER_COLORS[v], label=lbl)
        for v, lbl in LADDER_VARIANTS
    ]
    fig.legend(handles=handles, fontsize=8, ncol=3,
               loc="lower center", bbox_to_anchor=(0.5, -0.04),
               framealpha=0.9, edgecolor="#cccccc")

    plt.tight_layout()
    plt.savefig("dc_cfe_ladder.png", dpi=150, bbox_inches="tight")
    print("Saved dc_cfe_ladder.png")
    plt.close()


# ─────────────────────────────────────────────────────────────────────────────
# Figure 2: Portfolio composition under CFE requirements
# ─────────────────────────────────────────────────────────────────────────────

def fig_cfe_portfolio(df):
    techs = ["gas_cc_MW", "wind_MW", "solar_MW", "grid_draw"]

    n_states = len(STATE_ORDER)
    n_variants = len(PORTFOLIO_VARIANTS)
    bar_height = 0.18
    group_gap = 0.15
    group_height = n_variants * bar_height + group_gap

    fig, ax = plt.subplots(figsize=(13, 9))
    fig.suptitle(
        "1 GW Data Center — Onsite Build & Grid Draw Under CFE Requirements\n"
        "Bars show MW of installed capacity (gas, wind, solar) + mean grid draw (MW)",
        fontsize=11, fontweight="bold",
    )

    ytick_pos = []
    ytick_labels = []

    for si, state in enumerate(reversed(STATE_ORDER)):
        y_base = si * group_height
        state_rows = df[df["state"] == state].set_index("variant")

        for vi, var in enumerate(PORTFOLIO_VARIANTS):
            if var not in state_rows.index:
                continue
            row = state_rows.loc[var]
            y = y_base + vi * bar_height

            left = 0
            for tech in techs:
                col = "grid_draw_MW_mean" if tech == "grid_draw" else tech
                val = float(row[col])
                if val < 1:
                    continue
                ax.barh(y, val, height=bar_height * 0.85,
                        left=left, color=TECH_COLORS[tech],
                        edgecolor="white", linewidth=0.3)
                left += val

            ytick_pos.append(y)
            ytick_labels.append(PORTFOLIO_LABELS[var])

        # State label on the right
        label_y = y_base + (n_variants - 1) * bar_height / 2
        ax.text(ax.get_xlim()[1] if ax.get_xlim()[1] > 100 else 4200,
                label_y, state,
                va="center", ha="left", fontsize=11, fontweight="bold",
                color="#333333")

        # State divider line
        if si > 0:
            ax.axhline(y_base - group_gap / 2,
                       color="#dddddd", lw=0.8, zorder=0)

    ax.set_yticks(ytick_pos)
    ax.set_yticklabels(ytick_labels, fontsize=8)
    ax.set_xlabel("MW (capacity build or mean grid draw)", fontsize=10)
    ax.set_xlim(0, 4300)
    ax.grid(axis="x", alpha=0.25, lw=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Legend
    handles = [mpatches.Patch(color=TECH_COLORS[t], label=TECH_LABELS[t])
               for t in techs]
    ax.legend(handles=handles, fontsize=9, loc="lower right",
              framealpha=0.9, edgecolor="#cccccc")

    plt.tight_layout()
    plt.savefig("dc_cfe_portfolio.png", dpi=150, bbox_inches="tight")
    print("Saved dc_cfe_portfolio.png")
    plt.close()


def main():
    df = load()
    fig_cfe_ladder(df)
    fig_cfe_portfolio(df)
    print("Done.")


if __name__ == "__main__":
    main()
