#!/bin/bash
# Fetch the raw inputs for the build-rate pipeline (not committed).
#   EIA-860M monthly generator inventory -> data/raw/<month>.xlsx      (www.eia.gov)
#   LBNL Queued Up (through 2025)        -> data/raw/queued_up.xlsx
#     read with `git show` from origin/tom/interconnection-headroom, where it is committed
#     (that branch is not merged here)
set -u
cd "$(dirname "$0")/.."
MONTH="${EIA860M_MONTH:-august_generator2026}"
mkdir -p data/raw
if [ ! -s "data/raw/${MONTH}.xlsx" ]; then
  for path in "xls/${MONTH}.xlsx" "archive/xls/${MONTH}.xlsx"; do
    curl -fsSL -o "data/raw/${MONTH}.xlsx" "https://www.eia.gov/electricity/data/eia860m/${path}" && break
  done
fi
[ -s "data/raw/${MONTH}.xlsx" ] && echo "EIA-860M: data/raw/${MONTH}.xlsx" || echo "EIA-860M: download FAILED (check network allowlist or EIA860M_MONTH)"
QU_REF="origin/tom/interconnection-headroom:interconnection_headroom/data/LBNL_Ix_Queue_Data_File_thru2025.xlsx"
if [ ! -s data/raw/queued_up.xlsx ]; then
  git fetch -q origin tom/interconnection-headroom 2>/dev/null
  git show "$QU_REF" > data/raw/queued_up.xlsx 2>/dev/null || rm -f data/raw/queued_up.xlsx
fi
[ -s data/raw/queued_up.xlsx ] && echo "Queued Up: data/raw/queued_up.xlsx" || echo "Queued Up: NOT FOUND at $QU_REF"
