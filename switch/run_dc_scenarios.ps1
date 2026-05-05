# run_dc_scenarios.ps1
# 42-scenario DC on-site power analysis runner.
# Scenarios: 6 zones x 7 variants (BASE, SC, MC, SC_CURT, MC_CURT, FLEX_SC, FLEX_MC)
#
# Zone groups:
#   A = p8  / CA     B = p27 / AZ     C = p33 / CO
#   D = p48 / TX     E = p94 / GA     F = p100 / VA
#
# Cap variants:
#   BASE    = no grid soft cap, no curtailment
#   SC      = 250 MW soft cap ($750/MWh), no curtailment
#   MC      = 900 MW soft cap ($750/MWh), no curtailment
#   SC_CURT = SC + summer peak curtailment (max 800 MW on May/Jun peak TPs)
#   MC_CURT = MC + summer peak curtailment
#   FLEX_SC = model-chosen siting across all in-state zones, SC cap
#   FLEX_MC = model-chosen siting across all in-state zones, MC cap
#
# Usage:
#   .\run_dc_scenarios.ps1                          # run all 42 scenarios
#   .\run_dc_scenarios.ps1 -Group A                 # run one zone group (A-F)
#   .\run_dc_scenarios.ps1 -Variant BASE            # run one variant across all zones
#   .\run_dc_scenarios.ps1 -Scenario p33_SC         # run single scenario by ID
#   .\run_dc_scenarios.ps1 -SkipExisting            # skip already-completed runs
#   .\run_dc_scenarios.ps1 -CheckOnly               # show status without running
#
# Run from: Switch-USA-PG-ReEDS\switch\

param(
    [string]$Group    = "",
    [string]$Variant  = "",
    [string]$Scenario = "",
    [switch]$SkipExisting,
    [switch]$CheckOnly
)

Set-Location "D:/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS/switch"

$INPUTS  = "in/2035/s4x1_caelp_parclust_zoned"
$OUT_PRE = "out/2035/s4x1_caelp_parclust_zoned"

# ── Base aliases shared by all 42 scenarios ───────────────────────────────────
# Default input files reference dc_test, which is not in TRACKED_DEMANDS when using
# tracked_demands_dc1gw.csv (dc_main only). All such files must be aliased to empty
# versions to avoid Pyomo index validation errors.
# Note: tracked_demand_flex_events.csv is NOT aliased here because _expand_flex_events
# returns an empty dataframe after merging dc_test entries with dc_main time blocks,
# and the module now skips loading empty dataframes (len > 0 guard).
$BASE_ALIASES = @(
    "tracked_demands.csv=tracked_demands_dc1gw.csv",
    "tracked_demand_time_blocks.csv=tracked_demand_time_blocks_dc.csv",
    "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_empty.csv",
    "tracked_demand_rec_supply.csv=tracked_demand_rec_supply_empty.csv",
    "tracked_demand_h2_storage.csv=tracked_demand_h2_storage_empty.csv",
    "tracked_demand_onsite_predetermined.csv=tracked_demand_onsite_predetermined_empty.csv",
    "tracked_demand_dispatch_bounds.csv=tracked_demand_dispatch_bounds_empty.csv",
    "tracked_demand_grid_clean_caps.csv=tracked_demand_grid_clean_caps_empty.csv",
    "gen_group_load_ratio.csv=gen_group_load_ratio_max1.30.csv"
)

# ── Per-cap-variant soft-cap aliases ──────────────────────────────────────────
# BASE: omit the alias entirely; tracked_demand_grid_soft_cap.csv is optional and
# won't be found in the inputs dir (no file = no soft cap applied).
$CAP_ALIAS = @{
    "BASE"         = ""
    "SC"           = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_SC.csv"
    "MC"           = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_MC.csv"
    "SC_CURT"      = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_SC.csv"
    "MC_CURT"      = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_MC.csv"
    "FLEX_SC"      = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_SC.csv"
    "FLEX_MC"      = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_MC.csv"
    "SC_CFE75"     = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_SC.csv"
    "SC_CFE100"    = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_SC.csv"
    "MC_CFE75"     = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_MC.csv"
    "MC_CFE100"    = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_MC.csv"
    "FLEX_SC_CFE100" = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_SC.csv"
    "FLEX_MC_CFE100" = "tracked_demand_grid_soft_cap.csv=tracked_demand_grid_soft_cap_MC.csv"
}

# ── Per-variant flex-events aliases ──────────────────────────────────────────
# CURT variants cap grid draw (not total dispatch) during 18 summer peak TPs via
# flex_events, which constrains TDGridDraw and allows onsite generation to compensate.
# Using dispatch_bounds (which caps TDDispatch total) caused LP infeasibility.
$FLEX_ALIAS = @{
    "BASE"           = ""
    "SC"             = ""
    "MC"             = ""
    "SC_CURT"        = "tracked_demand_flex_events.csv=tracked_demand_flex_events_curt.csv"
    "MC_CURT"        = "tracked_demand_flex_events.csv=tracked_demand_flex_events_curt.csv"
    "FLEX_SC"        = ""
    "FLEX_MC"        = ""
    "SC_CFE75"       = ""
    "SC_CFE100"      = ""
    "MC_CFE75"       = ""
    "MC_CFE100"      = ""
    "FLEX_SC_CFE100" = ""
    "FLEX_MC_CFE100" = ""
}

# ── Per-variant CFE target aliases ───────────────────────────────────────────
$CFE_ALIAS = @{
    "BASE"           = ""
    "SC"             = ""
    "MC"             = ""
    "SC_CURT"        = ""
    "MC_CURT"        = ""
    "FLEX_SC"        = ""
    "FLEX_MC"        = ""
    "SC_CFE75"       = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_dc_75.csv"
    "SC_CFE100"      = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_dc_100.csv"
    "MC_CFE75"       = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_dc_75.csv"
    "MC_CFE100"      = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_dc_100.csv"
    "FLEX_SC_CFE100" = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_dc_100.csv"
    "FLEX_MC_CFE100" = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_dc_100.csv"
}

# ── Zone metadata ─────────────────────────────────────────────────────────────
$ZONES = @(
    [PSCustomObject]@{ Zone="p8";   Grp="A"; Label="CA" }
    [PSCustomObject]@{ Zone="p27";  Grp="B"; Label="AZ" }
    [PSCustomObject]@{ Zone="p33";  Grp="C"; Label="CO" }
    [PSCustomObject]@{ Zone="p48";  Grp="D"; Label="TX" }
    [PSCustomObject]@{ Zone="p94";  Grp="E"; Label="GA" }
    [PSCustomObject]@{ Zone="p100"; Grp="F"; Label="VA" }
)

$VARIANTS = @("BASE","SC","MC","SC_CURT","MC_CURT","FLEX_SC","FLEX_MC",
              "SC_CFE75","SC_CFE100","MC_CFE75","MC_CFE100",
              "FLEX_SC_CFE100","FLEX_MC_CFE100")

# ── Build scenario list ───────────────────────────────────────────────────────
$Scenarios = @()
foreach ($z in $ZONES) {
    foreach ($v in $VARIANTS) {
        # Candidate zone alias: FLEX variants use all in-state zones
        if ($v -like "FLEX_*") {
            $czAlias = "tracked_demand_candidate_zones.csv=tracked_demand_candidate_zones_flex_$($z.Zone).csv"
        } else {
            $czAlias = "tracked_demand_candidate_zones.csv=tracked_demand_candidate_zones_fixed_$($z.Zone).csv"
        }

        # Filter out empty strings
        $aliases = ($BASE_ALIASES + @(
            "tracked_demand_onsite_techs.csv=tracked_demand_onsite_techs_$($z.Zone).csv",
            "tracked_demand_onsite_build_caps.csv=tracked_demand_onsite_build_caps_$($z.Zone).csv",
            "tracked_demand_storage.csv=tracked_demand_storage_$($z.Zone).csv",
            $czAlias,
            $CAP_ALIAS[$v],
            $FLEX_ALIAS[$v],
            $CFE_ALIAS[$v]
        )) | Where-Object { $_ -ne "" }

        $siting = if ($v -like "FLEX_*") { "model-flex" } else { "fixed" }

        $Scenarios += [PSCustomObject]@{
            ID      = "$($z.Zone)_$v"
            Grp     = $z.Grp
            Variant = $v
            Zone    = $z.Zone
            Label   = $z.Label
            Desc    = "1 GW DC in $($z.Label) ($($z.Zone)), $v, $siting siting"
            Aliases = $aliases
        }
    }
}

# ── Result checking ───────────────────────────────────────────────────────────
function Get-SolveStatus($outDir) {
    if (Test-Path "$outDir/total_cost.txt") { return "PASS" }
    return "NOT_RUN"
}

function Get-GridCapSummary($outDir) {
    $csvPath = "$outDir/tracked_demand_grid_cap_summary.csv"
    if (-not (Test-Path $csvPath)) { return "" }
    try {
        $rows = Import-Csv $csvPath | Where-Object { $_.tracked_demand -eq "dc_main" }
        if (-not $rows) { return "" }
        $r = $rows[0]
        $excess = [double]$r.annual_excess_mwh
        $hrs    = [double]$r.hours_binding
        if ($excess -lt 1) { return "cap_ok" }
        return "excess={0:N0}MWh,hrs={1:N0}" -f $excess, $hrs
    } catch { return "?" }
}

# ── Invoke a single scenario ──────────────────────────────────────────────────
function Invoke-Scenario($s) {
    $outDir = "${OUT_PRE}_$($s.ID)"

    if ($SkipExisting -and (Test-Path "$outDir/total_cost.txt")) {
        Write-Host "  SKIP $($s.ID)" -ForegroundColor DarkGray
        return [PSCustomObject]@{ID=$s.ID;Grp=$s.Grp;Variant=$s.Variant;
            Zone=$s.Zone;Solve="SKIPPED";Cap="";Desc=$s.Desc}
    }

    Write-Host ""
    Write-Host "--- $($s.ID): $($s.Desc)" -ForegroundColor Cyan

    $aliasArgs = $s.Aliases | ForEach-Object { "--input-alias"; $_ }

    & switch solve --inputs-dir $INPUTS --outputs-dir $outDir @aliasArgs |
        Select-Object -Last 6 | ForEach-Object { Write-Host $_ }

    $solveOK  = ($LASTEXITCODE -eq 0)
    $solveStr = if ($solveOK) { "PASS" } else { "FAIL($LASTEXITCODE)" }
    $capStr   = if ($solveOK) { Get-GridCapSummary $outDir } else { "" }

    [PSCustomObject]@{ID=$s.ID; Grp=$s.Grp; Variant=$s.Variant;
        Zone=$s.Zone; Solve=$solveStr; Cap=$capStr; Desc=$s.Desc}
}

# ── Main ──────────────────────────────────────────────────────────────────────
$selected = $Scenarios | Where-Object {
    ($Group    -eq "" -or $_.Grp     -eq $Group) -and
    ($Variant  -eq "" -or $_.Variant -eq $Variant) -and
    ($Scenario -eq "" -or $_.ID      -eq $Scenario)
}

Write-Host "DC Scenario runner - $($selected.Count) scenario(s) selected"

if ($CheckOnly) {
    $results = $selected | ForEach-Object {
        $outDir  = "${OUT_PRE}_$($_.ID)"
        $solve   = Get-SolveStatus $outDir
        $cap     = if ($solve -eq "PASS") { Get-GridCapSummary $outDir } else { "" }
        [PSCustomObject]@{ID=$_.ID; Grp=$_.Grp; Variant=$_.Variant;
            Zone=$_.Zone; Solve=$solve; Cap=$cap; Desc=$_.Desc}
    }
} else {
    $results = $selected | ForEach-Object { Invoke-Scenario $_ }
}

# ── Summary table ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=================================================================="
Write-Host " DC SCENARIO RESULTS"
Write-Host "=================================================================="
$results | Format-Table -Property Grp,Zone,Variant,Solve,Cap -AutoSize

$nP = ($results | Where-Object { $_.Solve -eq "PASS" }).Count
$nF = ($results | Where-Object { $_.Solve -match "^FAIL" }).Count
$nS = ($results | Where-Object { $_.Solve -match "SKIP|NOT_RUN" }).Count
Write-Host "Pass: $nP  |  Fail: $nF  |  Not run/skipped: $nS  |  Total: $($results.Count)"
