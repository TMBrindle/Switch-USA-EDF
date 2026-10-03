@echo off
REM New-stack base solve (S0 uncapped + ic_v4 headroom + build_rate central v2 + B6 gas capex + B8 coal caps
REM + NY RPS buyout + windloss2 + amortisation), s4x1 2035. Template; set the variables first:
REM   REPO        repo root
REM   SWITCH_EXE  switch.exe of the solver env (VM: the switch-pg-reeds-fedpol env's Scripts\switch.exe)
REM   CASE        case inputs folder, relative to %REPO%\switch (e.g. in_ictest/cases/2035_icv4/s4x1_S0unc_2035_icon)
REM   OUT         outputs folder, relative to %REPO%\switch
REM   TEMP_DIR    solver scratch (VM: D:\tmp)
REM   GRB_LICENSE_FILE  Gurobi licence file (set explicitly: a conda env's trial licence can shadow the real one)
REM   THREADS     solver threads (default 8)
if "%THREADS%"=="" set THREADS=8
set TEMP=%TEMP_DIR%
set TMP=%TEMP_DIR%
set TMPDIR=%TEMP_DIR%
cd /d "%REPO%\switch"
"%SWITCH_EXE%" solve ^
  --inputs-dir %CASE% ^
  --outputs-dir %OUT% ^
  --solver-options-string "method=2 crossover=0 BarConvTol=1e-6 ScaleFlag=2 Threads=%THREADS%" ^
  --include-module study_modules.gen_amortization_period ^
  --input-alias rps_requirements.csv=rps_requirements.ic_v2.csv ^
  --input-alias ic_zones.csv=ic_zones.ic_v4.csv ^
  --input-alias ic_tranches.csv=ic_tranches.ic_v4.csv ^
  --input-alias ic_uprates.csv=ic_uprates.ic_v4.csv ^
  --input-alias ic_weights.csv=ic_weights.ic_v4.csv ^
  --input-alias ic_params.csv=ic_params.ic_v2.csv ^
  --input-alias build_rate_groups.csv=build_rate_groups.central.v2.csv ^
  --input-alias build_rate_periods.csv=build_rate_periods.central.v2.csv ^
  --input-alias build_rate_tiers.csv=build_rate_tiers.central.v2.csv ^
  --input-alias build_rate_zones.csv=build_rate_zones.central.v2.csv ^
  --input-alias build_rate_regions.csv=build_rate_regions.central.v2.csv ^
  --input-alias build_rate_gens.csv=build_rate_gens.central.v2.csv ^
  --input-alias gen_info.csv=gen_info.ic_v2.coalcfhist.csv ^
  --input-alias gen_build_costs.csv=gen_build_costs.gas_atb.csv ^
  --input-alias variable_capacity_factors.csv=variable_capacity_factors.windloss2.csv ^
  --tempdir %TEMP_DIR%
echo SOLVE_EXIT_CODE=%ERRORLEVEL%
