# run_td_tests.ps1
# Systematic test suite for the tracked_demands module.
# Usage:
#   .\run_td_tests.ps1                          # run all tests
#   .\run_td_tests.ps1 -Group A                 # run one group (A-F)
#   .\run_td_tests.ps1 -Test A1_DC_noCFE        # run single test by ID
#   .\run_td_tests.ps1 -SkipExisting            # skip already-completed runs
#   .\run_td_tests.ps1 -CheckOnly               # check results without re-running
#
# Test groups:
#   A – Data Center: CFE constraint variants (noCFE, 99%, advisory)
#   B – Data Center: supply-side (RECs-only, solar+stor, firm-clean, uncapped)
#   C – Data Center: geography & grid constraints (CA, MDA, grid cap, multi-zone)
#   D – Electrolyzer: basic (no CFE, H2 storage, flex)
#   E – Electrolyzer: 45V compliance (CFE target, full integration)
#   F – Multi-TD and regression (DC+elec simultaneously, no-TD baseline)
# Run from: Switch-USA-PG-ReEDS\switch\

param(
    [string]$Group = "",
    [string]$Test = "",
    [switch]$SkipExisting,
    [switch]$CheckOnly
)

Set-Location "D:/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS/switch"

$INPUTS  = "in/2035/s4x1_caelp_parclust_zoned"
$OUT_PRE = "out/2035/s4x1_caelp_parclust_zoned"

# ── Reusable alias fragments ──────────────────────────────────────────────────
$AZ  = "gen_group_load_ratio.csv=gen_group_load_ratio_max1.30.csv"
$AS  = "tracked_demand_storage.csv=tracked_demand_storage_1hr.csv"
$ANS = "tracked_demand_storage.csv=tracked_demand_storage_empty.csv"
$AH  = "tracked_demand_h2_storage.csv=tracked_demand_h2_storage_empty.csv"
$AP  = "tracked_demand_onsite_predetermined.csv=tracked_demand_onsite_predetermined_empty.csv"
$AGO = "tracked_demand_grid_clean_caps.csv=tracked_demand_grid_clean_caps_empty.csv"
$ADB = "tracked_demand_dispatch_bounds.csv=tracked_demand_dispatch_bounds_empty.csv"

$CFE_STD  = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets.csv"
$CFE_NONE = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_empty.csv"
$CFE_99   = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_99pct.csv"
$CFE_ADV  = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_advisory.csv"
$CFE_45V  = "tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_elec_45v.csv"

$REC_STD  = "tracked_demand_rec_supply.csv=tracked_demand_rec_supply.csv"
$REC_NONE = "tracked_demand_rec_supply.csv=tracked_demand_rec_supply_empty.csv"

$CAP_REAL = "tracked_demand_onsite_build_caps.csv=tracked_demand_onsite_build_caps_dc_realistic.csv"
$CAP_NONE = "tracked_demand_onsite_build_caps.csv=tracked_demand_onsite_build_caps_empty.csv"
$CAP_NB   = "tracked_demand_onsite_build_caps.csv=tracked_demand_onsite_build_caps_no_build.csv"
$CAP_SS   = "tracked_demand_onsite_build_caps.csv=tracked_demand_onsite_build_caps_solar_storage.csv"
$CAP_FIRM = "tracked_demand_onsite_build_caps.csv=tracked_demand_onsite_build_caps_firm_only.csv"

$TD_ELEC  = "tracked_demands.csv=tracked_demands_elec.csv"
$TD_MULTI = "tracked_demands.csv=tracked_demands_multi.csv"
$CZ_P8    = "tracked_demand_candidate_zones.csv=tracked_demand_candidate_zones_p8.csv"
$CZ_P125  = "tracked_demand_candidate_zones.csv=tracked_demand_candidate_zones_p125.csv"
$CZ_FLEX  = "tracked_demand_candidate_zones.csv=tracked_demand_candidate_zones_flex.csv"
$CZ_MULTI = "tracked_demand_candidate_zones.csv=tracked_demand_candidate_zones_multi.csv"
$H2_ELEC  = "tracked_demand_h2_storage.csv=tracked_demand_h2_storage_elec.csv"
$FX_ELEC  = "tracked_demand_flex_events.csv=tracked_demand_flex_events_elec.csv"
$GC_ON    = "tracked_demand_grid_clean_caps.csv=tracked_demand_grid_clean_caps.csv"

# ── Test definitions ──────────────────────────────────────────────────────────
# CFETgt: minimum expected cfe_computed_fraction (null = compliance not asserted)
$Tests = @(

  # A: Data Center — CFE constraint variants
  [PSCustomObject]@{ ID="A1_DC_noCFE";     Grp="A"
    Desc   ="DC plain load, no CFE targets"
    Purpose="Regression: module loads with no targets; DC adds load only"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$CFE_NONE,$REC_NONE,$CAP_NONE)
    CFETgt =$null }

  [PSCustomObject]@{ ID="A2_DC_CFE_99pct"; Grp="A"
    Desc   ="DC, 99%/99% CFE, no RECs, uncapped onsite build"
    Purpose="Upper-bound stress: forces maximum clean investment; tests near-100% feasibility"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$CFE_99,$REC_NONE,$CAP_NONE)
    CFETgt =0.99 }

  [PSCustomObject]@{ ID="A3_DC_CFE_advisory"; Grp="A"
    Desc   ="DC, 90%/75% targets, zero shortfall penalty (advisory)"
    Purpose="Zero-penalty edge case: shortfall variable active but costless; no investment expected"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$CFE_ADV,$REC_NONE,$CAP_NONE)
    CFETgt =$null }

  # B: Data Center — supply-side options
  [PSCustomObject]@{ ID="B1_DC_RECsOnly";  Grp="B"
    Desc   ="DC, 90%/75% CFE, RECs only (all onsite build capped at 0)"
    Purpose="Stage 11 isolation: RECs alone must meet CFE; onsite build should be zero"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$CFE_STD,$REC_STD,$CAP_NB)
    CFETgt =0.75 }

  [PSCustomObject]@{ ID="B2_DC_solar_stor"; Grp="B"
    Desc   ="DC, 90%/75% CFE, solar + battery only (no firm clean, no RECs)"
    Purpose="Variable RE matching: solar covers solar block; battery attempts non-solar; likely non-solar shortfall"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$CFE_STD,$REC_NONE,$CAP_SS)
    CFETgt =$null }

  [PSCustomObject]@{ ID="B3_DC_firm_only";  Grp="B"
    Desc   ="DC, 90%/75% CFE, geothermal/nuclear/gas-CCS only (no variable RE, no RECs)"
    Purpose="Firm clean strategy: geothermal builds to 24/7; no storage required; tests Stage 3+4 with dispatchable techs only"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$CFE_STD,$REC_NONE,$CAP_FIRM)
    CFETgt =0.75 }

  [PSCustomObject]@{ ID="B4_DC_full_uncapped"; Grp="B"
    Desc   ="DC, 90%/75% CFE, all techs uncapped, no RECs"
    Purpose="Free portfolio optimization: compares with DC_realistic to reveal impact of build caps"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$CFE_STD,$REC_NONE,$CAP_NONE)
    CFETgt =0.75 }

  # C: Data Center — geography and grid constraints
  [PSCustomObject]@{ ID="C1_DC_CA"; Grp="C"
    Desc   ="DC in California (p8), 90%/75% CFE, realistic caps + RECs"
    Purpose="Clean-grid context: CA ~80% clean; tests whether CFE trivially met or drives specific high-solar investment"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$CFE_STD,$REC_STD,$CAP_REAL,$CZ_P8)
    CFETgt =0.75 }

  [PSCustomObject]@{ ID="C2_DC_MDA"; Grp="C"
    Desc   ="DC in MidAtlantic (p125), 90%/75% CFE, realistic caps + RECs"
    Purpose="Dirty-grid stress: MDA ~12% incremental-clean; tests heavy investment / REC sourcing in fossil-heavy context"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$CFE_STD,$REC_STD,$CAP_REAL,$CZ_P125)
    CFETgt =0.75 }

  [PSCustomObject]@{ ID="C3_DC_gridcap"; Grp="C"
    Desc   ="DC in p33, binding grid clean cap (150k/50k MWh), realistic caps + RECs"
    Purpose="Stage 9: physical grid attribution limit forces onsite investment; tests td_grid_clean_max_mwh binding"
    Aliases=@($AZ,$AS,$AH,$AP,$CFE_STD,$REC_STD,$CAP_REAL,$GC_ON)
    CFETgt =0.75 }

  [PSCustomObject]@{ ID="C4_DC_multizone"; Grp="C"
    Desc   ="DC siting across three Mountain West zones (p32/p33/p34)"
    Purpose="Stage 1 multi-zone: model selects optimal zone for load placement; tests TD_CANDIDATE_ZONES siting logic"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$CFE_STD,$REC_STD,$CAP_REAL,$CZ_FLEX)
    CFETgt =0.75 }

  # D: Electrolyzer — basic configurations
  [PSCustomObject]@{ ID="D1_ELEC_basic"; Grp="D"
    Desc   ="Electrolyzer 438 GWh/yr, flexible 0-100 MW, no CFE, no H2 storage, no flex module"
    Purpose="Stage 1 electrolyzer: basic load tracking; verifies td_type=electrolyzer loads without H2/45V features"
    Aliases=@($AZ,$ANS,$AH,$AP,$AGO,$ADB,$CFE_NONE,$REC_NONE,$CAP_NONE,$TD_ELEC)
    CFETgt =$null }

  [PSCustomObject]@{ ID="D2_ELEC_h2stor"; Grp="D"
    Desc   ="Electrolyzer + H2 storage, no CFE"
    Purpose="Stage 6: H2 storage decouples production from delivery; checks sizing and dispatch variables"
    Aliases=@($AZ,$ANS,$AP,$AGO,$ADB,$CFE_NONE,$REC_NONE,$CAP_NONE,$TD_ELEC,$H2_ELEC)
    CFETgt =$null }

  [PSCustomObject]@{ ID="D3_ELEC_flex"; Grp="D"
    Desc   ="Electrolyzer + strong flex (10 MW cap non-solar), 3-zone siting, no CFE"
    Purpose="Stage 7 flex: non-solar draw cap forces production into solar hours; tests noncompliance penalty and zone selection"
    Aliases=@($AZ,$ANS,$AH,$AP,$AGO,$ADB,$CFE_NONE,$REC_NONE,$CAP_NONE,$TD_ELEC,$FX_ELEC,$CZ_FLEX)
    CFETgt =$null }

  # E: Electrolyzer — 45V compliance
  [PSCustomObject]@{ ID="E1_ELEC_45v_target"; Grp="E"
    Desc   ="Electrolyzer, 90%/90% CFE both blocks (symmetric), no RECs, realistic build caps"
    Purpose="Stage 10 45V: symmetric high CFE forces clean sourcing; post-solve 45V assessment should show tier and lifecycle CO2"
    Aliases=@($AZ,$ANS,$AH,$AP,$AGO,$ADB,$CFE_45V,$REC_NONE,$CAP_REAL,$TD_ELEC)
    CFETgt =0.9 }

  [PSCustomObject]@{ ID="E2_ELEC_full_45v"; Grp="E"
    Desc   ="Electrolyzer: flex + H2 storage + 90%/90% CFE + onsite solar (full 45V setup)"
    Purpose="Integration: all electrolyzer features simultaneously; flex shifts load, H2 storage buffers delivery, CFE forces clean sourcing"
    Aliases=@($AZ,$ANS,$AP,$AGO,$ADB,$CFE_45V,$REC_NONE,$CAP_SS,$TD_ELEC,$H2_ELEC,$FX_ELEC,$CZ_FLEX)
    CFETgt =0.9 }

  # F: Multi-TD and regression
  [PSCustomObject]@{ ID="F1_MULTI_dc_elec"; Grp="F"
    Desc   ="DC (876 GWh/yr firm, 90%/75% CFE + RECs) + Electrolyzer (438 GWh/yr flex) in p33"
    Purpose="Multi-TD regression: two TDs in same zone; verifies no cross-contamination; DC has full CFE stack, electrolyzer has none"
    Aliases=@($AZ,$AS,$AH,$AP,$AGO,$ADB,$CFE_STD,$REC_STD,$CAP_REAL,$TD_MULTI,$CZ_MULTI)
    CFETgt =0.75 }

  [PSCustomObject]@{ ID="F2_noTD"; Grp="F"
    Desc   ="No tracked demands (module excluded)"
    Purpose="Emissions/generation baseline: confirms clean solve without tracked_demands module"
    Aliases=@($AZ)
    ExcludeModule="study_modules.tracked_demands"
    CFETgt =$null }
)

# ── Helpers ───────────────────────────────────────────────────────────────────

function Check-CFE($outDir, $minTgt) {
    $f = Join-Path $outDir "tracked_demand_cfe_computed.csv"
    if (-not (Test-Path $f)) { return "no CFE file" }
    $rows = Import-Csv $f
    $fails = @()
    foreach ($row in $rows) {
        $comp = [double]$row.cfe_computed_fraction
        $tgt  = if ($null -ne $minTgt) { $minTgt } else { [double]$row.cfe_target }
        if ($comp -lt ($tgt - 0.002)) {
            $fails += "$($row.time_block):$([math]::Round($comp,3))<$tgt"
        }
    }
    if ($fails.Count -eq 0) { return "CFE OK" }
    return "CFE FAIL: " + ($fails -join "; ")
}

function Invoke-TDTest($t) {
    $outDir = "${OUT_PRE}_$($t.ID)"

    if ($SkipExisting -and (Test-Path "$outDir/total_cost.txt")) {
        Write-Host "  SKIP $($t.ID)" -ForegroundColor DarkGray
        return [PSCustomObject]@{ID=$t.ID;Grp=$t.Grp;Solve="SKIPPED";CFE="N/A";Desc=$t.Desc}
    }

    Write-Host ""
    Write-Host "--- $($t.ID): $($t.Desc)" -ForegroundColor Cyan

    $aliasArgs   = $t.Aliases | ForEach-Object { "--input-alias"; $_ }
    $excludeArgs = if ($t.ExcludeModule) { @("--exclude-module",$t.ExcludeModule) } else { @() }

    & switch solve --inputs-dir $INPUTS --outputs-dir $outDir @aliasArgs @excludeArgs |
        Select-Object -Last 6 | ForEach-Object { Write-Host $_ }

    $solveOK = ($LASTEXITCODE -eq 0)
    if ($solveOK -and ($null -ne $t.CFETgt)) {
        $cfe = Check-CFE $outDir $t.CFETgt
    } else {
        $cfe = "N/A"
    }
    $solveStr = if ($solveOK) { "PASS" } else { "FAIL($LASTEXITCODE)" }

    [PSCustomObject]@{ ID=$t.ID; Grp=$t.Grp; Solve=$solveStr; CFE=$cfe; Desc=$t.Desc }
}

# --- Main --------------------------------------------------------------------
$selected = $Tests | Where-Object {
    ($Group -eq "" -or $_.Grp -eq $Group) -and
    ($Test  -eq "" -or $_.ID  -eq $Test)
}

if ($CheckOnly) {
    $results = $selected | ForEach-Object {
        $outDir = "${OUT_PRE}_$($_.ID)"
        $exists = Test-Path "$outDir/total_cost.txt"
        $solveStr = if ($exists) { "EXISTS" } else { "NOT RUN" }
        if ($exists -and ($null -ne $_.CFETgt)) {
            $cfe = Check-CFE $outDir $_.CFETgt
        } else {
            $cfe = "N/A"
        }
        [PSCustomObject]@{ ID=$_.ID; Grp=$_.Grp; Solve=$solveStr; CFE=$cfe; Desc=$_.Desc }
    }
} else {
    $results = $selected | ForEach-Object { Invoke-TDTest $_ }
}

# --- Summary -----------------------------------------------------------------
Write-Host ""
Write-Host "=================================================================="
Write-Host " TRACKED DEMAND TEST SUITE RESULTS"
Write-Host "=================================================================="
$results | Format-Table -Property Grp,ID,Solve,CFE -AutoSize
$nP = ($results | Where-Object { $_.Solve -eq "PASS" }).Count
$nF = ($results | Where-Object { $_.Solve -match "^FAIL" }).Count
$nS = ($results | Where-Object { $_.Solve -match "SKIP|EXISTS|NOT" }).Count
Write-Host "Pass: $nP  |  Fail: $nF  |  Skip/existing: $nS  |  Total: $($results.Count)"
