#!/bin/bash
# Fetch the raw inputs that are not committed to the repo.
#   EIA-860M monthly generator inventory  -> data/raw/
#   LBNL interconnection cost workbooks   -> data/raw/lbnl/<REGION>.xlsx
#     Normally already committed to the repo: LBNL's site sits behind a Cloudflare browser
#     check that returns 403 to scripts (seen 2026-10-01). The download below is only tried for
#     files that are missing, then project files (/mnt/project-files) are used as a fallback.
# Needs www.eia.gov and eta-publications.lbl.gov on the environment's network allowlist.
set -u
cd "$(dirname "$0")/.."
MONTH="${EIA860M_MONTH:-august_generator2026}"
mkdir -p data/raw/lbnl
if [ ! -s "data/raw/${MONTH}.xlsx" ]; then
  for path in "xls/${MONTH}.xlsx" "archive/xls/${MONTH}.xlsx"; do
    curl -fsSL -o "data/raw/${MONTH}.xlsx" "https://www.eia.gov/electricity/data/eia860m/${path}" && break
  done
  [ -s "data/raw/${MONTH}.xlsx" ] && echo "EIA-860M: downloaded ${MONTH}.xlsx" || echo "EIA-860M: download FAILED (check allowlist or EIA860M_MONTH)"
else
  echo "EIA-860M: ${MONTH}.xlsx already present"
fi
# LBNL interconnection cost data (index: https://emp.lbl.gov/interconnection_costs).
# Saved under region names: the pipeline uses the file name as the region label when a
# workbook has no region column. Add new releases here as LBNL publishes them.
LBNL_BASE="https://eta-publications.lbl.gov/sites/default/files"
declare -A LBNL=(
  [MISO]="${LBNL_BASE}/miso_costs_2021_clean_data.xlsx"
  [PJM]="${LBNL_BASE}/pjm_costs_2022_clean_data.xlsx"
  [SPP]="${LBNL_BASE}/spp_costs_2023_clean_data.xlsx"
  [ISONE]="${LBNL_BASE}/isone_interconnection_cost_data_publication_vfinal.xlsx"
  [NYISO]="${LBNL_BASE}/nyiso_2022_final_data_cleaned_publication_vfinal.xlsx"
  [NonISO]="${LBNL_BASE}/2026-02/ba_costs_2024_clean_data.xlsx"
)
is_xlsx() { [ -s "$1" ] && [ "$(head -c 2 "$1")" = "PK" ]; }  # xlsx files are zip archives
failed=()
for region in "${!LBNL[@]}"; do
  dest="data/raw/lbnl/${region}.xlsx"
  if is_xlsx "$dest"; then
    echo "LBNL ${region}: already present"
    continue
  fi
  tmp="$(mktemp)"
  if curl -fsSL --retry 2 -o "$tmp" "${LBNL[$region]}" && is_xlsx "$tmp"; then
    mv "$tmp" "$dest"
    echo "LBNL ${region}: downloaded"
  else
    rm -f "$tmp"
    failed+=("$region")
  fi
done
# fallback: workbooks uploaded to the project Library
if [ ${#failed[@]} -gt 0 ]; then
  shopt -s globstar nullglob
  uploads=(/mnt/project-files/**/*.xls*)
  for f in "${uploads[@]}"; do
    case "$(basename "$f")" in august_generator*|*generator20*) continue ;; esac
    cp -n "$f" data/raw/lbnl/
    echo "LBNL: copied $(basename "$f") from project files"
  done
  echo "LBNL: download FAILED for ${failed[*]}. LBNL's site blocks scripted downloads; download them"
  echo "      in a browser from https://emp.lbl.gov/interconnection_costs and commit them to"
  echo "      interconnection_headroom/data/raw/lbnl/<REGION>.xlsx"
fi
n=$(find data/raw/lbnl -maxdepth 1 -name '*.xls*' ! -name 'SYNTHETIC*' | wc -l)
echo "LBNL: ${n} real workbook(s) in data/raw/lbnl"
