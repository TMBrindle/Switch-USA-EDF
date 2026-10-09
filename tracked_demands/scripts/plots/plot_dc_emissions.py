#!/usr/bin/env python3
"""
plot_dc_emissions.py

Barbell / dumbbell charts for DC on-site power CO₂ analysis.
Reads dc_analysis_summary.csv (produced by analyze_dc_scenarios.py).

Two figures:
  dc_emissions_barbell.png  — CO₂ intensity per state, all scenarios
  dc_emissions_delta.png    — Δ CO₂ vs BASE barbell, SC and MC families
"""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.lines as mlines
import numpy as np

SUMMARY_CSV = "dc_analysis_summary.csv"
MWH_PER_YEAR = 8_760_000

STATE_ORDER = ["CA", "TX", "VA", "GA", "AZ", "CO"]   # cleanest → dirtiest grid

VARIANT_COLORS = {
    "BASE":     "#444444",
    "SC":       "#2c7bb6",
    "SC_CURT":  "#74add1",
    "FLEX_SC":  "#abd9e9",
    "MC":       "#d7191c",
    "MC_CURT":  "#f46d43",
    "FLEX_MC":  "#fdae61",
}
VARIANT_MARKERS = {
    "BASE": "D",
    "SC": "o",    "SC_CURT": "^",    "FLEX_SC": "s",
    "MC": "o",    "MC_CURT": "^",    "FLEX_MC": "s",
}
VARIANT_LABELS = {
    "BASE": "BASE (grid only)",
    "SC":      "SC  (250 MW cap)",
    "SC_CURT": "SC+CURT",
    "FLEX_SC": "FLEX-SC  (model-sited)",
    "MC":      "MC  (900 MW cap)",
    "MC_CURT": "MC+CURT",
    "FLEX_MC": "FLEX-MC  (model-sited)",
}


def co2_kgmwh(tonne_per_yr):
    return tonne_per_yr / MWH_PER_YEAR * 1000


def prep(df):
    df = df.copy()
    df["co2_kgmwh"] = co2_kgmwh(df["annual_co2_tonne"])
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Figure 1: full barbell — all 7 variants per state, SC left / MC right
# ──────────────────────────────────────────────────────────────────────────────

def fig_barbell(df):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
    fig.suptitle(
        "1 GW Data Center — CO₂ Intensity by Location & Scenario\n"
        "Points show kg CO₂ per MWh delivered to the DC  (lower = cleaner)",
        fontsize=12, fontweight="bold", y=1.01,
    )

    families = [
        ("SC family — 250 MW soft cap",
         ["BASE", "SC", "SC_CURT", "FLEX_SC"]),
        ("MC family — 900 MW soft cap",
         ["BASE", "MC", "MC_CURT", "FLEX_MC"]),
    ]

    for ax, (title, variants) in zip(axes, families):
        for yi, state in enumerate(STATE_ORDER):
            rows = df[df["state"] == state]

            xs = {}
            for v in variants:
                r = rows[rows["variant"] == v]
                if not r.empty:
                    xs[v] = float(r["co2_kgmwh"])

            # Spine connecting min→max of this family for visual clarity
            all_x = list(xs.values())
            ax.hlines(yi, min(all_x), max(all_x),
                      color="#cccccc", lw=2, zorder=2)

            # Dots
            for v, x in xs.items():
                ax.scatter(x, yi,
                           color=VARIANT_COLORS[v],
                           marker=VARIANT_MARKERS[v],
                           s=90, zorder=5,
                           edgecolors="white", linewidths=0.6)

        ax.set_yticks(range(len(STATE_ORDER)))
        ax.set_yticklabels(STATE_ORDER, fontsize=11, fontweight="bold")
        ax.set_xlabel("kg CO₂ / MWh", fontsize=10)
        ax.set_title(title, fontsize=11, fontweight="bold", pad=6)
        ax.grid(axis="x", alpha=0.25, lw=0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        legend_handles = [
            mlines.Line2D([], [],
                          color=VARIANT_COLORS[v],
                          marker=VARIANT_MARKERS[v],
                          markersize=7, linewidth=0,
                          label=VARIANT_LABELS[v])
            for v in variants
        ]
        ax.legend(handles=legend_handles, fontsize=8.5,
                  loc="lower right", framealpha=0.9, edgecolor="#cccccc")

    plt.tight_layout()
    plt.savefig("dc_emissions_barbell.png", dpi=150, bbox_inches="tight")
    print("Saved dc_emissions_barbell.png")
    plt.close()


# ──────────────────────────────────────────────────────────────────────────────
# Figure 2: Δ from BASE barbell — one row per state × variant pair,
#           showing three "steps": BASE→cap, cap→curt, cap→flex
# ──────────────────────────────────────────────────────────────────────────────

def fig_delta(df):
    base_vals = (df[df["variant"] == "BASE"]
                 .set_index("state")["co2_kgmwh"])

    # Barbell pairs: BASE→cap, then cap→FLEX (CURT omitted — near-zero shift for most zones)
    SC_STEPS = [
        ("BASE → SC",    "BASE", "SC"),
        ("SC → FLEX-SC", "SC",   "FLEX_SC"),
    ]
    MC_STEPS = [
        ("BASE → MC",    "BASE", "MC"),
        ("MC → FLEX-MC", "MC",   "FLEX_MC"),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=False)
    fig.suptitle(
        "1 GW Data Center — Shift in CO₂ Intensity Between Consecutive Scenarios\n"
        "Each barbell shows one transition step; position on x-axis = absolute kg CO₂/MWh  |  "
        "right shift = more emissions",
        fontsize=11, fontweight="bold", y=1.02,
    )

    step_palette_sc = ["#2166ac", "#41ab5d"]   # BASE→SC, SC→FLEX
    step_palette_mc = ["#d7191c", "#fe9929"]   # BASE→MC, MC→FLEX

    def draw_delta_panel(ax, steps, palette, title):
        # Each state gets a cluster of n_steps rows with a small gap between states
        n_steps = len(steps)
        n_states = len(STATE_ORDER)
        gap = 0.3   # extra space between state clusters

        tick_positions = []
        tick_labels = []

        for si, state in enumerate(STATE_ORDER):
            rows = df[df["state"] == state]
            y_base = si * (n_steps + gap)

            for step_i, (label, from_v, to_v) in enumerate(steps):
                y = y_base + step_i
                tick_positions.append(y)
                tick_labels.append(f"{state}  {label}" if step_i == 0 else f"   {label}")

                r_from = rows[rows["variant"] == from_v]
                r_to   = rows[rows["variant"] == to_v]
                if r_from.empty or r_to.empty:
                    continue

                x_from = float(r_from["co2_kgmwh"])
                x_to   = float(r_to["co2_kgmwh"])
                color  = palette[step_i]

                # Barbell spine
                ax.hlines(y, min(x_from, x_to), max(x_from, x_to),
                          color=color, lw=2.2, zorder=3, alpha=0.85)

                # From-dot (open)
                ax.scatter(x_from, y, color=color, marker="o",
                           s=55, zorder=5, facecolors="white",
                           edgecolors=color, linewidths=1.5)
                # To-dot (filled)
                ax.scatter(x_to, y, color=color, marker="o",
                           s=55, zorder=5)

                # Arrow tip if shift > 5 kg/MWh
                dx = x_to - x_from
                if abs(dx) > 5:
                    ax.annotate(
                        "", xy=(x_to, y), xytext=(x_from, y),
                        arrowprops=dict(
                            arrowstyle="-|>",
                            color=color, lw=1.2,
                            mutation_scale=8,
                        ),
                        zorder=6,
                    )

        ax.set_yticks(tick_positions)
        ax.set_yticklabels(tick_labels, fontsize=8)
        ax.set_xlabel("kg CO₂ / MWh", fontsize=10)
        ax.set_title(title, fontsize=11, fontweight="bold", pad=6)
        ax.grid(axis="x", alpha=0.25, lw=0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        # State dividers
        for si in range(1, n_states):
            ax.axhline(si * (n_steps + gap) - gap / 2,
                       color="#dddddd", lw=0.8, zorder=1)

        # Legend
        handles = [
            mlines.Line2D([], [], color=palette[i],
                          marker="o", markersize=6, lw=1.8,
                          label=label)
            for i, (label, _, _) in enumerate(steps)
        ]
        ax.legend(handles=handles, fontsize=8.5, loc="lower right",
                  framealpha=0.9, edgecolor="#cccccc")

    draw_delta_panel(axes[0], SC_STEPS, step_palette_sc,
                     "SC family — 250 MW soft cap")
    draw_delta_panel(axes[1], MC_STEPS, step_palette_mc,
                     "MC family — 900 MW soft cap")

    plt.tight_layout()
    plt.savefig("dc_emissions_delta.png", dpi=150, bbox_inches="tight")
    print("Saved dc_emissions_delta.png")
    plt.close()


def main():
    df = prep(pd.read_csv(SUMMARY_CSV))
    fig_barbell(df)
    fig_delta(df)
    print("Done.")


if __name__ == "__main__":
    main()
