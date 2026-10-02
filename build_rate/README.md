# build_rate

Build-rate supply curves for new onshore wind, solar, storage (and opt-in gas). See
`Guides and documentation/build_rate.md` for the method, sources and the VM test handoff.

```
bash scripts/fetch_data.sh   # EIA-860M (www.eia.gov) and LBNL Queued Up (git show from tom/interconnection-headroom)
python -m brc.cli run        # tables -> outputs/ (base rates, R0, near-term, rates_<level>.csv, tiers)
pytest -q                    # pipeline unit tests and Switch toy solves (HiGHS)
```

House rules as in `interconnection_headroom/CLAUDE.md`: synthetic data is never a result; if a real
input is missing, say what and stop; mark placeholders in config.yaml and replace them only with
sourced values.
