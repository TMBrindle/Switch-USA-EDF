"""
Interactive dashboard for comparing SWITCH model runs (e.g. the epri_med
flexibility scenario matrix). Reads per-generator results
(dispatch_gen_annual_summary.csv) and system-level cost components
(costs_itemized.csv) from each case's output folder, plus hierarchy.csv for
regional aggregation, and serves a local Dash app with dropdown filters.

Run with the dashboard venv (kept separate from switch-pg-reeds to avoid a
pydantic v1/v2 conflict between Dash and PUDL):

    .venv-viz\Scripts\python.exe compare_runs_dashboard_s4x1.py

Then open http://127.0.0.1:8051 in a browser.
"""

from pathlib import Path

import pandas as pd
import plotly.express as px
from dash import Dash, dcc, html, Input, Output

# ------------------------------------------------------------------ #
# Configuration -- edit this to compare a different set of runs
# ------------------------------------------------------------------ #
CASES = [
    "s4x1_edf_med",
    "s4x1_edf_WestTEC",
]
OUT_DIR = Path("switch/out/foresight")
HIERARCHY_FILE = Path("hierarchy.csv")

METRICS = {
    "Capacity (MW)": "GenCapacity_MW",
    "Generation (GWh/yr)": "Energy_GWh_typical_yr",
    "Emissions (tCO2/yr)": "DispatchEmissions_tCO2_per_typical_yr",
    "Generator cost ($/yr)": "TotalGenCost",
}
BREAKDOWNS = {
    "Total": None,
    "By energy source": "gen_energy_source",
    "By technology": "gen_tech",
    "By period": "period",
}

GEN_COST_COMPONENTS = {
    "Total generator cost": "TotalGenCost",
    "Capital cost": "GenCapitalCosts",
    "Fixed O&M cost": "GenFixedOMCosts",
    "Variable cost": "VariableCost_per_yr",
}
COST_BREAKDOWNS = {
    "Total": None,
    "By energy source": "gen_energy_source",
    "By technology": "gen_tech",
    "By period": "period",
}

# ------------------------------------------------------------------ #
# Load data
# ------------------------------------------------------------------ #
hierarchy = pd.read_csv(HIERARCHY_FILE)
REGION_LEVELS = ["ba"] + [c for c in hierarchy.columns if c != "ba"]

gen_frames = []
cost_frames = []
for case_id in CASES:
    case_dir = OUT_DIR / case_id
    gen = pd.read_csv(case_dir / "dispatch_gen_annual_summary.csv")
    gen["TotalGenCost"] = (
        gen["VariableCost_per_yr"] + gen["GenCapitalCosts"] + gen["GenFixedOMCosts"]
    )
    gen["case_id"] = case_id
    gen_frames.append(gen)

    cost = pd.read_csv(case_dir / "costs_itemized.csv")
    cost["case_id"] = case_id
    cost_frames.append(cost)

gen_df = pd.concat(gen_frames, ignore_index=True)
gen_df = gen_df.merge(hierarchy, left_on="gen_load_zone", right_on="ba", how="left")
# Keep period as string so Plotly treats it as a discrete color category
gen_df["period"] = gen_df["period"].astype(int).astype(str)

cost_df = pd.concat(cost_frames, ignore_index=True)
cost_df["PERIOD"] = cost_df["PERIOD"].astype(int).astype(str)

ALL_PERIODS = sorted(gen_df["period"].dropna().unique().tolist())
PERIOD_OPTIONS = ["All"] + ALL_PERIODS

CASE_ORDER = CASES


# ------------------------------------------------------------------ #
# Layout helpers
# ------------------------------------------------------------------ #
def region_period_row(id_prefix):
    """Shared region + period filter row, parameterised by an id prefix."""
    return html.Div(
        [
            html.Div(
                [
                    html.Label("Region aggregation"),
                    dcc.Dropdown(
                        id=f"{id_prefix}-region-level",
                        options=REGION_LEVELS,
                        value="ba",
                        clearable=False,
                    ),
                ],
                style={"width": "23%", "display": "inline-block"},
            ),
            html.Div(
                [
                    html.Label("Region"),
                    dcc.Dropdown(id=f"{id_prefix}-region-value", clearable=False),
                ],
                style={"width": "23%", "display": "inline-block", "marginLeft": "2%"},
            ),
            html.Div(
                [
                    html.Label("Period"),
                    dcc.Dropdown(
                        id=f"{id_prefix}-period",
                        options=PERIOD_OPTIONS,
                        value="All",
                        clearable=False,
                    ),
                ],
                style={"width": "15%", "display": "inline-block", "marginLeft": "2%"},
            ),
        ],
        style={"marginTop": "6px", "marginBottom": "10px"},
    )


# ------------------------------------------------------------------ #
# App layout
# ------------------------------------------------------------------ #
app = Dash(__name__)

app.layout = html.Div(
    [
        html.H2("SWITCH run comparison -- s4x1 foresight"),
        dcc.Tabs(
            [
                # ── Tab 1: Generator-level metrics ──────────────────────────
                dcc.Tab(
                    label="Generator-level metrics",
                    children=[
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.Label("Metric"),
                                        dcc.Dropdown(
                                            id="metric",
                                            options=list(METRICS.keys()),
                                            value="Capacity (MW)",
                                            clearable=False,
                                        ),
                                    ],
                                    style={"width": "23%", "display": "inline-block"},
                                ),
                                html.Div(
                                    [
                                        html.Label("Breakdown"),
                                        dcc.Dropdown(
                                            id="breakdown",
                                            options=list(BREAKDOWNS.keys()),
                                            value="By energy source",
                                            clearable=False,
                                        ),
                                    ],
                                    style={
                                        "width": "23%",
                                        "display": "inline-block",
                                        "marginLeft": "2%",
                                    },
                                ),
                            ],
                            style={"marginTop": "10px"},
                        ),
                        region_period_row("gen"),
                        dcc.Graph(id="gen-chart", style={"height": "70vh"}),
                    ],
                ),
                # ── Tab 2: Generator costs by region ────────────────────────
                dcc.Tab(
                    label="Generator costs by region",
                    children=[
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.Label("Cost component"),
                                        dcc.Dropdown(
                                            id="cost-component",
                                            options=list(GEN_COST_COMPONENTS.keys()),
                                            value="Total generator cost",
                                            clearable=False,
                                        ),
                                    ],
                                    style={"width": "23%", "display": "inline-block"},
                                ),
                                html.Div(
                                    [
                                        html.Label("Breakdown"),
                                        dcc.Dropdown(
                                            id="cost-breakdown",
                                            options=list(COST_BREAKDOWNS.keys()),
                                            value="Total",
                                            clearable=False,
                                        ),
                                    ],
                                    style={
                                        "width": "23%",
                                        "display": "inline-block",
                                        "marginLeft": "2%",
                                    },
                                ),
                            ],
                            style={"marginTop": "10px"},
                        ),
                        region_period_row("creg"),
                        dcc.Graph(id="cost-reg-chart", style={"height": "70vh"}),
                    ],
                ),
                # ── Tab 3: System cost components ────────────────────────────
                dcc.Tab(
                    label="System cost components",
                    children=[
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.Label("Cost basis"),
                                        dcc.Dropdown(
                                            id="cost-basis",
                                            options=["AnnualCost_NPV", "AnnualCost_Real"],
                                            value="AnnualCost_NPV",
                                            clearable=False,
                                        ),
                                    ],
                                    style={"width": "23%", "display": "inline-block"},
                                ),
                                html.Div(
                                    [
                                        html.Label("Period"),
                                        dcc.Dropdown(
                                            id="syscost-period",
                                            options=PERIOD_OPTIONS,
                                            value="All",
                                            clearable=False,
                                        ),
                                    ],
                                    style={
                                        "width": "15%",
                                        "display": "inline-block",
                                        "marginLeft": "2%",
                                    },
                                ),
                            ],
                            style={"marginTop": "10px", "marginBottom": "10px"},
                        ),
                        dcc.Graph(id="cost-chart", style={"height": "70vh"}),
                    ],
                ),
            ]
        ),
    ],
    style={"margin": "20px"},
)


# ------------------------------------------------------------------ #
# Callbacks — Tab 1: Generator-level metrics
# ------------------------------------------------------------------ #
@app.callback(
    Output("gen-region-value", "options"), Input("gen-region-level", "value")
)
def gen_region_options(region_level):
    values = sorted(gen_df[region_level].dropna().unique().tolist())
    return ["All"] + values


@app.callback(
    Output("gen-region-value", "value"), Input("gen-region-level", "value")
)
def gen_region_reset(_):
    return "All"


@app.callback(
    Output("gen-chart", "figure"),
    Input("metric", "value"),
    Input("breakdown", "value"),
    Input("gen-region-level", "value"),
    Input("gen-region-value", "value"),
    Input("gen-period", "value"),
)
def update_gen_chart(metric_label, breakdown_label, region_level, region_value, period_value):
    metric_col = METRICS[metric_label]
    breakdown_col = BREAKDOWNS[breakdown_label]

    df = gen_df
    if region_value and region_value != "All":
        df = df[df[region_level] == region_value]
    if period_value and period_value != "All":
        df = df[df["period"] == period_value]

    group_cols = ["case_id"] + ([breakdown_col] if breakdown_col else [])
    summary = df.groupby(group_cols, as_index=False)[metric_col].sum()

    region_label = "All regions" if region_value in (None, "All") else region_value
    period_label = "All periods" if period_value in (None, "All") else period_value
    title = f"{metric_label} — {breakdown_label.lower()} ({region_label}, {period_label})"

    if breakdown_col:
        fig = px.bar(
            summary,
            x="case_id",
            y=metric_col,
            color=breakdown_col,
            category_orders={"case_id": CASE_ORDER},
            title=title,
            labels={metric_col: metric_label, "case_id": "Case"},
        )
    else:
        fig = px.bar(
            summary,
            x="case_id",
            y=metric_col,
            color="case_id",
            category_orders={"case_id": CASE_ORDER},
            title=title,
            labels={metric_col: metric_label, "case_id": "Case"},
        )
    return fig


# ------------------------------------------------------------------ #
# Callbacks — Tab 2: Generator costs by region
# ------------------------------------------------------------------ #
@app.callback(
    Output("creg-region-value", "options"), Input("creg-region-level", "value")
)
def creg_region_options(region_level):
    values = sorted(gen_df[region_level].dropna().unique().tolist())
    return ["All"] + values


@app.callback(
    Output("creg-region-value", "value"), Input("creg-region-level", "value")
)
def creg_region_reset(_):
    return "All"


@app.callback(
    Output("cost-reg-chart", "figure"),
    Input("cost-component", "value"),
    Input("cost-breakdown", "value"),
    Input("creg-region-level", "value"),
    Input("creg-region-value", "value"),
    Input("creg-period", "value"),
)
def update_cost_reg_chart(cost_label, breakdown_label, region_level, region_value, period_value):
    cost_col = GEN_COST_COMPONENTS[cost_label]
    breakdown_col = COST_BREAKDOWNS[breakdown_label]

    df = gen_df
    if region_value and region_value != "All":
        df = df[df[region_level] == region_value]
    if period_value and period_value != "All":
        df = df[df["period"] == period_value]

    group_cols = ["case_id"] + ([breakdown_col] if breakdown_col else [])
    summary = df.groupby(group_cols, as_index=False)[cost_col].sum()

    region_label = "All regions" if region_value in (None, "All") else region_value
    period_label = "All periods" if period_value in (None, "All") else period_value
    title = f"{cost_label} — {breakdown_label.lower()} ({region_label}, {period_label})"

    if breakdown_col:
        fig = px.bar(
            summary,
            x="case_id",
            y=cost_col,
            color=breakdown_col,
            category_orders={"case_id": CASE_ORDER},
            title=title,
            labels={cost_col: cost_label, "case_id": "Case"},
        )
    else:
        fig = px.bar(
            summary,
            x="case_id",
            y=cost_col,
            color="case_id",
            category_orders={"case_id": CASE_ORDER},
            title=title,
            labels={cost_col: cost_label, "case_id": "Case"},
        )
    return fig


# ------------------------------------------------------------------ #
# Callbacks — Tab 3: System cost components
# ------------------------------------------------------------------ #
@app.callback(
    Output("cost-chart", "figure"),
    Input("cost-basis", "value"),
    Input("syscost-period", "value"),
)
def update_cost_chart(cost_basis, period_value):
    df = cost_df
    if period_value and period_value != "All":
        df = df[df["PERIOD"] == period_value]
    else:
        df = df.groupby(["Component", "case_id"], as_index=False)[cost_basis].sum()

    fig = px.bar(
        df,
        x="Component",
        y=cost_basis,
        color="case_id",
        barmode="group",
        category_orders={"case_id": CASE_ORDER},
        title=f"System cost components by case ({cost_basis})",
        labels={cost_basis: "Cost ($)", "Component": "Cost component"},
    )
    fig.update_layout(xaxis_tickangle=-30)
    return fig


if __name__ == "__main__":
    app.run(debug=True, port=8051)
