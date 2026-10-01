# LBNL interconnection cost workbooks go here

`bash scripts/fetch_data.sh` downloads them automatically (needs eta-publications.lbl.gov on the
network allowlist) and saves each as `<REGION>.xlsx`: MISO, PJM, SPP, ISONE, NYISO and NonISO
(PacifiCorp, BPA, Duke Energy Progress, Carolinas and Florida). The source URLs are in the script;
the index of studies is https://emp.lbl.gov/interconnection_costs. There is no CAISO or ERCOT file.

To add them by hand, download the data file from each study page and save it here under the
region name; the file name is used as the region label when a workbook has no region column.

Then run `python -m icsc.cli inspect-lbnl` and fix any MISSING columns in `config.yaml`
(`lbnl.columns`). `python -m icsc.cli synthetic` writes SYNTHETIC_*.xlsx test files here; delete
them before a real run.
