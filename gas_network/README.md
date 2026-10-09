# gas_network (inside Switch-USA-EDF)

Natural-gas pipeline capacity and flows for Switch-USA-EDF, following EIA's NEMS Natural Gas Market Module.
Stage 1 (CHANGES §93) attributes each zone's delivered gas to production basins; it changes nothing in the model.
Guide: `../Guides and documentation/gas_network.md`. Design and later stages: `docs/design_note_step1.md`.
House rules: `../interconnection_headroom/CLAUDE.md` (sourced numbers only, marked assumptions, CHANGES section).

```
python -m gasnet.cli fetch    # pinned inputs -> data/raw/ (git-ignored), sha256-checked against data/SOURCES.yml
python -m gasnet.cli run      # outputs/zone_basin_shares.csv and the cross-checks
python -m gasnet.cli report <switch outputs dir>   # basin_gas_mmbtu.csv for a Switch run
python -m pytest -q           # pandas 3.x and 1.4.4
```

Layout: `gasnet/` package · `config.yaml` settings and marked assumptions · `data/reference/` hand-built mappings
(hubs, production areas, Table 64 labels) · `data/SOURCES.yml` pins · `data/basin_intensity.csv` (to be filled by the
methane research task) · `outputs/` committed results · `tests/`.
