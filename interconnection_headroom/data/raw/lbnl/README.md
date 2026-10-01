# LBNL interconnection cost workbooks go here

The workbooks are committed here, one per region as `<REGION>.xlsx`, because LBNL's site blocks
scripted downloads with a Cloudflare browser check (`fetch_data.sh` only tries for missing ones): MISO, PJM, SPP, ISONE, NYISO and NonISO
(PacifiCorp, BPA, Duke Energy Progress, Carolinas and Florida). The source URLs are in the script;
the index of studies is https://emp.lbl.gov/interconnection_costs. There is no CAISO or ERCOT file.

To add them by hand, download the data file from each study page and save it here under the
region name; the file name is used as the region label when a workbook has no region column.

Then run `python -m icsc.cli inspect-lbnl` and fix any MISSING columns in `config.yaml`
(`lbnl.columns`). `python -m icsc.cli synthetic` writes SYNTHETIC_*.xlsx test files here; delete
them before a real run.
