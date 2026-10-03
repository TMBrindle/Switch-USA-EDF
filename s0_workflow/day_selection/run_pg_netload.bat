@echo off
REM Generate a case with fleet-independent day selection. Run from anywhere; set the variables below first.
REM   REPO       repo root (this checkout)
REM   PG_PYTHON  python of the conda env that has PowerGenome + typer (on the VM: switch-pg-reeds-fedpol)
REM   NL_CFG     JSON config file (see nl_days_config.example.json); repo-relative paths inside are resolved from REPO
REM   CASE_ID    scenario_inputs.csv case id (new stack base: s4x1_S0unc_2035_icon)
REM   OUT        output folder (must not exist), e.g. switch/in_ictest/cases/nl24_fi
if "%REPO%"=="" (echo set REPO & exit /b 1)
if "%PG_PYTHON%"=="" (echo set PG_PYTHON & exit /b 1)
if "%TEMP_DIR%"=="" set TEMP_DIR=%TEMP%
set TEMP=%TEMP_DIR%
set TMP=%TEMP_DIR%
set PYTHONIOENCODING=utf-8
set NL_SELECTOR=netload_days_fi
for /f "usebackq delims=" %%j in (`"%PG_PYTHON%" -c "import json,sys;print(json.dumps(json.load(open(sys.argv[1]))))" "%NL_CFG%"`) do set NL_DAYS_CFG=%%j
cd /d "%REPO%"
if exist "%OUT%" (echo %OUT% exists - pick another name & exit /b 1)
"%PG_PYTHON%" s0_workflow\day_selection\pg_to_switch_netload.py pg/settings %OUT% --case-id %CASE_ID% --year 2035
echo PG_EXIT_CODE=%ERRORLEVEL%
