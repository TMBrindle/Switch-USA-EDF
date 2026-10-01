# LBNL interconnection cost workbooks go here

Download the project-level cost data from https://emp.lbl.gov/interconnection_costs
(one workbook per study: MISO, PJM, SPP, NYISO, ISO-NE, CAISO, and the non-ISO "study BAs"
release). Save each .xlsx in this folder, ideally named by region (e.g. `MISO.xlsx`) because
the file name is used as the region label when a workbook has no region column.

Then run `python -m icsc.cli inspect-lbnl` and fix any MISSING columns in `config.yaml`
(`lbnl.columns`). `python -m icsc.cli synthetic` writes SYNTHETIC_*.xlsx test files here;
delete them before a real run.
