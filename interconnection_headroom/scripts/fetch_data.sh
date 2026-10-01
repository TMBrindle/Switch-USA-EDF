#!/bin/bash
# Fetch the raw inputs that are not committed to the repo.
#   EIA-860M monthly generator inventory  -> data/raw/
#   LBNL interconnection cost workbooks   -> data/raw/lbnl/  (copied from project files if uploaded there)
# Needs www.eia.gov on the environment's network allowlist.
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
# LBNL workbooks: upload them to the project Library (they appear under /mnt/project-files)
if compgen -G "/mnt/project-files/**/*.xls*" > /dev/null || compgen -G "/mnt/project-files/*.xls*" > /dev/null; then
  shopt -s globstar nullglob
  for f in /mnt/project-files/**/*.xls*; do
    case "$(basename "$f")" in august_generator*|*generator20*) continue ;; esac
    cp -n "$f" data/raw/lbnl/
  done
fi
n=$(ls data/raw/lbnl/*.xls* 2>/dev/null | grep -vc SYNTHETIC || true)
echo "LBNL: ${n} real workbook(s) in data/raw/lbnl"
