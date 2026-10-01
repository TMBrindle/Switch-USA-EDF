# Finishing the build in Claude Code cloud sessions

Checked against the Claude Code docs on 1 Oct 2026:
https://code.claude.com/docs/en/claude-projects and
https://code.claude.com/docs/en/cloud-environments. Projects are in public beta on Pro and Max
plans only (not Team or Enterprise yet). If **Projects** isn't in the claude.ai/code sidebar, use
ordinary cloud sessions with the same environment and paste the project instructions below into
the first message.

## 1. Repository

The work lives in `TMBrindle/Switch-USA-EDF` on branch `tom/interconnection-headroom` (off
`edf-baseline`). The pipeline is in `interconnection_headroom/`.

Install the Claude GitHub App (https://github.com/apps/claude) on Switch-USA-EDF if it isn't
already. Project threads need the App; a `/web-setup` token alone isn't enough.

## 2. Create a cloud environment

At claude.ai/code, click the cloud icon showing the current environment's name (in the row above the
message box), choose **Add cloud environment**, and name it `switch-usa-edf`:

**Network access: Custom**, with **Also include default list of common package managers**
checked, and these **Allowed domains**:

```
www.eia.gov
emp.lbl.gov
eta-publications.lbl.gov
www.bls.gov
api.bls.gov
```

Add HIFLD's host later if a thread needs line data. GitHub traffic goes through its own proxy and
doesn't need listing.

**Setup script** (runs before Claude starts; cached if it finishes in about 5 minutes):

```bash
#!/bin/bash
# Must exit 0 or the session won't start, so nothing here is allowed to fail hard.
PKGS="pandas numpy<2 statsmodels openpyxl pyyaml pytest highspy switch_model==2.0.9"
pip install --quiet $PKGS || pip install --quiet --break-system-packages $PKGS || true
git clone --depth 1 https://github.com/switch-model/switch.git /opt/switch-src || true
exit 0
```

**Environment variables:**

```
SWITCH_SRC=/opt/switch-src
EIA860M_MONTH=august_generator2026
```

No secrets are needed: all inputs are public.

## 3. Create the project

**New project** → name `Interconnection headroom curves`, repository `Switch-USA-EDF`.
In **Project settings > Environment**, pick the `switch-usa-edf` environment.

The six LBNL interconnection cost workbooks (MISO, PJM, SPP, ISO-NE, NYISO and the non-ISO
release) are committed to the branch under `interconnection_headroom/data/raw/lbnl/`. LBNL's site
sits behind a Cloudflare browser check, so scripts get a 403 whatever the allowlist says. When LBNL
publishes a new release, download it in a browser from https://emp.lbl.gov/interconnection_costs,
save it as `<REGION>.xlsx` in that folder and commit it.

## 4. Project instructions

Paste into **Project settings > Memory > Project instructions**:

```text
This project builds zonal interconnection (network-upgrade) supply curves for the 134 ReEDS
zones from LBNL project-level costs, and a Switch module that uses them, in the Switch-USA-EDF
repository: pipeline in interconnection_headroom/, module in
switch/study_modules/interconnection_headroom.py, pg_to_switch hooks behind
pg/settings/interconnection_headroom.yml. Method and status: interconnection_headroom/docs/project_doc.html;
rules: interconnection_headroom/CLAUDE.md.

- Start every thread with `bash interconnection_headroom/scripts/fetch_data.sh` and report what it found.
- Branch from tom/interconnection-headroom and open one draft pull request per thread into it.
  Never push to edf-baseline or main. Don't merge or force-push.
- Before calling work done, run `pytest -q` in interconnection_headroom/ and paste the summary line, plus the headline
  numbers from any pipeline step you ran on real data.
- Never report results from SYNTHETIC_* data. If the LBNL workbooks or another real input is
  missing, say exactly what is missing in your first message and stop.
- Ask me before changing the estimation method, the tech-weight or reuse-share defaults in
  config.yaml, or the Switch module's constraint structure.
- Full Switch-USA-PG runs (PowerGenome data, Gurobi) happen on energyVm1, not here. Stop at
  writing outputs/{zones,tranches,uprates}_<scenario>.csv and testing on the Switch toy model.
```

## 5. First threads

Send these as separate tasks once the workbooks are uploaded. The first two can run in parallel.

1. **Ingest LBNL.** Run `inspect-lbnl`, fix `lbnl.columns` and region aliases in `config.yaml`
   until every workbook resolves, and report county→zone match rates by region.
2. **Verify reference inputs.** Check `data/reference/cpi_u_annual.csv` against BLS and add
   2025. Source GETs and reconductoring costs and caps (Brattle on GETs in SPP; the
   GridLab/Berkeley reconductoring study), and replace the placeholders with cited values.
3. **Fit weights.** Run `fit-weights` on the real sample. Report the top combinations and how
   flat the AIC surface is, and propose `config.yaml` defaults. Wait for my go-ahead before
   changing them.
4. **Run and review curves.** Run the full pipeline for all scenarios. Produce a short review of
   extrapolated tranches, zones on the mean regime effect, and the PJM result. Update the
   status and first-results sections of docs/project_doc.html.
5. **Prepare for Switch-USA-PG.** Review the pg_to_switch hooks against the checks in
   `Guides and documentation/interconnection_headroom.md`, and write the exact steps for a first
   2030 test case. I'll do the real run on energyVm1.

## Moving work between the cloud and energyVm1

- On energyVm1: `git fetch origin && git checkout tom/interconnection-headroom`.
- To continue a cloud thread locally: `claude --teleport <session-id>` from a clone of the repo.
- To send a new task from energyVm1: `claude --cloud "<task>"` from the repo. This clones the
  pushed branch, so push first.
- A project thread can also run on your own computer through Remote Control, if Claude Code is
  set up there. That's the route for anything that needs Switch-USA-PG data or Gurobi.
