#!/usr/bin/env python3
"""
plot_dc_cfe_supplemental.py

Supplemental figures for CFE scenario analysis:

  dc_cfe_cost_premium.png   — cost premium over BASE ($/MWh) per variant × state
  dc_cfe_storage_build.png  — storage MW built under CFE variants
  dc_cfe_shortfall.png      — CFE shortfall penalty ($/yr) by variant × state
  dc_cfe_co2_delta.png      — CO₂ reduction per $M of cost premium vs BASE
"""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

SUMMARY_CSV = "dc_analysis_summary.csv"
MWH_PER_YEAR = 8_760_000

STATE_ORDER = ["CA", "TX", "VA", "GA", "AZ", "CO"]
STATE_COLORS = {
    "CA": "#2166ac", "TX": "#4dac26", "VA": "#d01c8b",
    "GA": "#f1b6da", "AZ": "#b2df8a", "CO": "#e66101",
}

# Variants to include (excluding BASE which is the reference)
CFE_VARIANT_ORDER = [
    "SC", "SC_CFE75", "SC_CFE100",
    "MC", "MC_CFE75", "MC_CFE100",
    "FLEX_SC", "FLEX_SC_CFE100",
    "FLEX_MC", "FLEX_MC_CFE100",
]

CFE_VARIANT_LABELS = {
    "SC":             "SC",
    "SC_CFE75":       "SC\n+CFE75",
    "SC_CFE100":      "SC\n+CFE100",
    "MC":             "MC",
    "MC_CFE75":       "MC\n+CFE75",
    "MC_CFE100":      "MC\n+CFE100",
    "FLEX_SC":        "FLEX\nSC",
    "FLEX_SC_CFE100": "FLEX-SC\n+CFE100",
    "FLEX_MC":        "FLEX\nMC",
    "FLEX_MC_CFE100": "FLEX-MC\n+CFE100",
}

TECH_COLORS = {
    "storage_power_MW": "#9ecae1",
}


def load():
    df = pd.read_csv(SUMMARY_CSV)
    df["co2_kgmwh"] = df["annual_co2_tonne"] * 1000 / MWH_PER_YEAR
    return df


def get_base_costs(df):
    """Return dict: state → BASE total_system_cost."""
    base = df[df["variant"] == "BASE"].set_index("state")
    return base["total_system_cost"].to_dict()


def get_base_co2(df):
    """Return dict: state → BASE co2_kgmwh."""
    base = df[df["variant"] == "BASE"].set_index("state")
    return base["co2_kgmwh"].to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Figure 1: Cost premium over BASE ($/MWh DC output)
# ─────────────────────────────────────────────────────────────────────────────

def fig_cost_premium(df):
    base_costs = get_base_costs(df)
    variants = [v for v in CFE_VARIANT_ORDER if v in df["variant"].unique()]

    fig, ax = plt.subplots(figsize=(14, 6))
    fig.suptitle(
        "Incremental Cost vs BASE — $/MWh of DC Output (1 GW × 8,760 MWh/yr = 8.76 TWh/yr)\n"
        "Negative = cheaper than BASE; positive = premium",
        fontsize=11, fontweight="bold",
    )

    x = np.arange(len(variants))
    width = 0.12
    offsets = np.linspace(-(len(STATE_ORDER) - 1) / 2, (len(STATE_ORDER) - 1) / 2, len(STATE_ORDER)) * width

    for i, state in enumerate(STATE_ORDER):
        sdf = df[df["state"] == state].set_index("variant")
        base_cost = base_costs.get(state)
        if base_cost is None:
            continue
        ys = []
        for v in variants:
            if v in sdf.index and base_cost is not None:
                delta_cost = float(sdf.loc[v, "total_system_cost"]) - base_cost
                delta_mwh = delta_cost / MWH_PER_YEAR
                ys.append(delta_mwh)
            else:
                ys.append(np.nan)
        ax.bar(x + offsets[i], ys, width=width * 0.9,
               color=STATE_COLORS[state], label=state, alpha=0.85,
               edgecolor="white", linewidth=0.4)

    ax.axhline(0, color="#888888", lw=0.8, ls="--")
    ax.set_xticks(x)
    ax.set_xticklabels([CFE_VARIANT_LABELS[v] for v in variants], fontsize=8)
    ax.set_ylabel("$/MWh vs BASE", fontsize=10)
    ax.grid(axis="y", alpha=0.25, lw=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Group brackets
    for grp_label, grp_start, grp_end in [
        ("SC family", 0, 2), ("MC family", 3, 5),
        ("FLEX-SC family", 6, 7), ("FLEX-MC family", 8, 9)
    ]:
        ax.annotate("", xy=(grp_end + 0.45, ax.get_ylim()[0]),
                    xytext=(grp_start - 0.45, ax.get_ylim()[0]),
                    arrowprops=dict(arrowstyle="-", color="#cccccc", lw=1))
        ax.text((grp_start + grp_end) / 2, ax.get_ylim()[0],
                grp_label, ha="center", va="top", fontsize=7,
                color="#666666", style="italic")

    handles = [mpatches.Patch(color=STATE_COLORS[s], label=s) for s in STATE_ORDER]
    ax.legend(handles=handles, fontsize=9, ncol=6,
              loc="upper left", framealpha=0.9)

    plt.tight_layout()
    plt.savefig("dc_cfe_cost_premium.png", dpi=150, bbox_inches="tight")
    print("Saved dc_cfe_cost_premium.png")
    plt.close()


# ─────────────────────────────────────────────────────────────────────────────
# Figure 2: Storage build under CFE variants
# ─────────────────────────────────────────────────────────────────────────────

def fig_storage_build(df):
    cfe_variants = ["SC", "SC_CFE75", "SC_CFE100",
                    "MC", "MC_CFE75", "MC_CFE100",
                    "FLEX_SC", "FLEX_SC_CFE100",
                    "FLEX_MC", "FLEX_MC_CFE100"]
    cfe_variants = [v for v in cfe_variants if v in df["variant"].unique()]

    n_states = len(STATE_ORDER)
    n_variants = len(cfe_variants)
    bar_height = 0.14
    group_gap = 0.12
    group_height = n_variants * bar_height + group_gap

    fig, ax = plt.subplots(figsize=(12, 9))
    fig.suptitle(
        "Storage Built (MW) — CFE Variants by State\n"
        "Shows the battery / storage investment needed to meet CFE targets",
        fontsize=11, fontweight="bold",
    )

    ytick_pos = []
    ytick_labels = []

    for si, state in enumerate(reversed(STATE_ORDER)):
        y_base = si * group_height
        sdf = df[df["state"] == state].set_index("variant")

        for vi, var in enumerate(cfe_variants):
            if var not in sdf.index:
                continue
            row = sdf.loc[var]
            y = y_base + vi * bar_height
            stor_mw = float(row["storage_power_MW"])
            stor_mwh = float(row["storage_energy_MWh"])

            color = "#9ecae1" if "CFE" in var else "#cccccc"
            ax.barh(y, stor_mw, height=bar_height * 0.85,
                    color=color, edgecolor="white", linewidth=0.3)

            if stor_mwh > 50:
                ax.text(stor_mw + 15, y,
                        f"{stor_mwh:.0f} MWh", va="center", fontsize=6.5,
                        color="#333333")

            ytick_pos.append(y)
            ytick_labels.append(CFE_VARIANT_LABELS.get(var, var))

        label_y = y_base + (n_variants - 1) * bar_height / 2
        ax.text(3100, label_y, state, va="center", ha="left",
                fontsize=11, fontweight="bold", color="#333333")

        if si > 0:
            ax.axhline(y_base - group_gap / 2,
                       color="#dddddd", lw=0.8, zorder=0)

    ax.set_yticks(ytick_pos)
    ax.set_yticklabels(ytick_labels, fontsize=7.5)
    ax.set_xlabel("Storage Power (MW)", fontsize=10)
    ax.set_xlim(0, 3300)
    ax.grid(axis="x", alpha=0.25, lw=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    handles = [
        mpatches.Patch(color="#9ecae1", label="CFE-constrained variant"),
        mpatches.Patch(color="#cccccc", label="Unconstrained variant (BASE, SC, MC, FLEX)"),
    ]
    ax.legend(handles=handles, fontsize=9, loc="lower right",
              framealpha=0.9, edgecolor="#cccccc")

    plt.tight_layout()
    plt.savefig("dc_cfe_storage_build.png", dpi=150, bbox_inches="tight")
    print("Saved dc_cfe_storage_build.png")
    plt.close()


# ─────────────────────────────────────────────────────────────────────────────
# Figure 3: CFE shortfall penalty heat map (state × variant)
# ─────────────────────────────────────────────────────────────────────────────

def fig_shortfall_heatmap(df):
    cfe_variants = ["SC_CFE75", "SC_CFE100", "MC_CFE75", "MC_CFE100",
                    "FLEX_SC_CFE100", "FLEX_MC_CFE100"]
    cfe_variants = [v for v in cfe_variants if v in df["variant"].unique()]

    pivot = df[df["variant"].isin(cfe_variants)].pivot_table(
        index="state", columns="variant",
        values="cap_penalty_per_yr", aggfunc="first"
    ).reindex(index=STATE_ORDER, columns=cfe_variants)

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle(
        "Annual CFE Shortfall Penalty ($/yr)\n"
        "Non-zero entries = model accepts constraint violation rather than building renewables",
        fontsize=11, fontweight="bold",
    )

    vals = pivot.values.astype(float)
    max_val = max(vals[~np.isnan(vals)].max(), 1)
    im = ax.imshow(vals, cmap="YlOrRd", aspect="auto",
                   vmin=0, vmax=max_val)

    ax.set_xticks(range(len(cfe_variants)))
    ax.set_xticklabels([CFE_VARIANT_LABELS.get(v, v) for v in cfe_variants],
                       fontsize=9)
    ax.set_yticks(range(len(STATE_ORDER)))
    ax.set_yticklabels(STATE_ORDER, fontsize=10)

    for i in range(len(STATE_ORDER)):
        for j in range(len(cfe_variants)):
            v = vals[i, j]
            if not np.isnan(v) and v > 0:
                txt = f"${v/1e3:.0f}K" if v < 1e6 else f"${v/1e6:.1f}M"
                ax.text(j, i, txt, ha="center", va="center",
                        fontsize=8, color="black" if v < max_val * 0.6 else "white")
            elif not np.isnan(v):
                ax.text(j, i, "—", ha="center", va="center",
                        fontsize=9, color="#aaaaaa")

    plt.colorbar(im, ax=ax, label="$/yr shortfall penalty", shrink=0.8)
    ax.set_title("States × CFE variant — shortfall penalty", fontsize=9, pad=4)

    plt.tight_layout()
    plt.savefig("dc_cfe_shortfall.png", dpi=150, bbox_inches="tight")
    print("Saved dc_cfe_shortfall.png")
    plt.close()


# ─────────────────────────────────────────────────────────────────────────────
# Figure 4: CO₂ reduction efficiency (tonne CO₂ avoided per $M premium)
# ─────────────────────────────────────────────────────────────────────────────

def fig_co2_efficiency(df):
    base_costs = get_base_costs(df)
    base_co2 = get_base_co2(df)

    cfe_variants = ["SC_CFE75", "SC_CFE100", "MC_CFE75", "MC_CFE100",
                    "FLEX_SC_CFE100", "FLEX_MC_CFE100"]
    cfe_variants = [v for v in cfe_variants if v in df["variant"].unique()]

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        "CFE Compliance: CO₂ Intensity vs Cost Premium over BASE\n"
        "Lower-left = cleaner AND cheaper; bubble size = storage built (MW)",
        fontsize=11, fontweight="bold",
    )

    variant_markers = {
        "SC_CFE75":       "o", "SC_CFE100":      "s",
        "MC_CFE75":       "^", "MC_CFE100":      "D",
        "FLEX_SC_CFE100": "P", "FLEX_MC_CFE100": "X",
    }
    variant_colors = {
        "SC_CFE75":       "#2171b5", "SC_CFE100":      "#08519c",
        "MC_CFE75":       "#de2d26", "MC_CFE100":      "#a50f15",
        "FLEX_SC_CFE100": "#6baed6", "FLEX_MC_CFE100": "#fc8d59",
    }

    for ax, states, title in [
        (axes[0], ["CA", "TX", "VA"], "CA / TX / VA"),
        (axes[1], ["GA", "AZ", "CO"], "GA / AZ / CO"),
    ]:
        for state in states:
            sdf = df[df["state"] == state].set_index("variant")
            b_cost = base_costs.get(state, 0)
            b_co2 = base_co2.get(state, 0)

            # Plot BASE reference
            if state in df[df["variant"] == "BASE"]["state"].values:
                base_row = df[(df["state"] == state) & (df["variant"] == "BASE")].iloc[0]
                ax.scatter(0, b_co2, marker="D", s=100,
                           color=STATE_COLORS[state], zorder=6,
                           edgecolors="black", linewidths=0.8,
                           label=f"{state} BASE" if state == states[0] else "_nolegend_")

            for var in cfe_variants:
                if var not in sdf.index:
                    continue
                row = sdf.loc[var]
                delta_cost_per_mwh = (float(row["total_system_cost"]) - b_cost) / MWH_PER_YEAR
                co2 = float(row["co2_kgmwh"])
                stor = float(row["storage_power_MW"])

                ax.scatter(delta_cost_per_mwh, co2,
                           marker=variant_markers[var],
                           s=max(40, stor * 0.15),
                           color=variant_colors[var],
                           edgecolors=STATE_COLORS[state],
                           linewidths=1.5, zorder=5, alpha=0.85)
                ax.annotate(f"{state}", (delta_cost_per_mwh, co2),
                            textcoords="offset points", xytext=(4, 2),
                            fontsize=7, color=STATE_COLORS[state])

        ax.set_xlabel("Cost premium vs BASE ($/MWh)", fontsize=9)
        ax.set_ylabel("CO₂ intensity (kg/MWh)", fontsize=9)
        ax.set_title(title, fontsize=10, fontweight="bold")
        ax.axvline(0, color="#cccccc", lw=0.8, ls="--")
        ax.grid(alpha=0.2, lw=0.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # Shared variant legend
    handles = [
        plt.scatter([], [], marker=variant_markers[v], s=80,
                    color=variant_colors[v], edgecolors="#555555",
                    linewidths=1, label=CFE_VARIANT_LABELS.get(v, v).replace("\n", " "))
        for v in cfe_variants
    ]
    handles += [plt.scatter([], [], marker="D", s=80, color="#555555",
                             edgecolors="black", linewidths=0.8, label="BASE (ref)")]
    fig.legend(handles=handles, fontsize=8.5, ncol=4,
               loc="lower center", bbox_to_anchor=(0.5, -0.03),
               framealpha=0.9, edgecolor="#cccccc")

    plt.tight_layout()
    plt.savefig("dc_cfe_co2_efficiency.png", dpi=150, bbox_inches="tight")
    print("Saved dc_cfe_co2_efficiency.png")
    plt.close()


def main():
    df = load()
    fig_cost_premium(df)
    fig_storage_build(df)
    fig_shortfall_heatmap(df)
    fig_co2_efficiency(df)
    print("Done.")


if __name__ == "__main__":
    main()
