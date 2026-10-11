"""Wind's interconnection charge against the data (analysis only; docs/wind_price_check.md).

  python scripts/wind_price_check.py --outputs <run outputs folder> [--config config.yaml] [--out-dir <folder>]

Reads a pipeline run (`python -m icsc.cli run --start-year 2026`, scenario atts_s0: estimation_sample.csv,
zones_atts_s0.csv, tranches_atts_s0.csv) and writes, for the LBNL regimes PJM, MISO, SPP, NYISO, ISO-NE and the
non-ISO pool (PacifiCorp, BPA, FRCC, DEP, DEC):
  wind_price_observed.csv   network-upgrade $/kW (real), wind and solar, completed and active, all queue years and
                            2018+: median, p25, p75, mean (what the PPML fit models), n
  wind_price_model.csv      the model's charge per kW of nameplate for a new project at each zone's 2026 start
                            fullness (first-step price x the tech weight; Switch prices every step at the
                            reference solar curve), and the fit's predicted $/kW for a completed 150 MW wind and
                            solar project there (NRIS, the zone's trend year, the regime's own completed effect);
                            zone averages weighted by base capacity (H0)
  wind_price_refits.csv     the wind effect: as fitted; split by status (wind x completed, wind x active); fitted
                            on SPP, MISO and PJM only (both forms)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from icsc import cli, estimate  # noqa: E402

GROUPS = {"PJM": ["PJM"], "MISO": ["MISO"], "SPP": ["SPP"], "NYISO": ["NYISO"], "ISO-NE": ["ISONE"],
          "non-ISO pool": ["PacifiCorp", "BPA", "FRCC", "DEP", "DEC"]}
WIND_COMMON = ["SPP", "MISO", "PJM"]


def _stats(v: pd.Series) -> dict:
    v = v.dropna()
    return {"n": len(v), "median": v.median() if len(v) else np.nan, "p25": v.quantile(.25) if len(v) else np.nan,
            "p75": v.quantile(.75) if len(v) else np.nan, "mean": v.mean() if len(v) else np.nan}


def observed(sample: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for g, regs in GROUPS.items():
        s = sample[sample.regime.isin(regs)]
        for status in ("completed", "active"):
            for years, cut in (("all", 0), ("2018+", 2018)):
                for tech in ("wind", "solar"):
                    x = s[(s.tech_n == tech) & (s.status_n == status) & (s.queue_year >= cut)]
                    rows.append({"group": g, "status": status, "queue_years": years, "tech": tech,
                                 **_stats(x.network_cost_real)})
    return pd.DataFrame(rows)


def modelled(model, zones: pd.DataFrame, tranches: pd.DataFrame, weights: dict) -> pd.DataFrame:
    first = tranches.sort_values(["ba", "sat_from"]).groupby("ba").first()["cost_per_kw"]
    z = zones.set_index("ba").assign(first_step=first)
    rows = []
    for g, regs in GROUPS.items():
        zz = z[z.regime.isin(regs)].copy()
        if zz.empty:
            continue
        for tech in ("wind", "solar"):
            zz[f"fit_{tech}"] = [float(model.predict_cost(r.start_saturation, tech, "NRIS", 150.0, r.trend_year,
                                                          r.regime, status="completed")[0])
                                 for r in zz.itertuples()]
        w = zz.base_capacity_mw / zz.base_capacity_mw.sum()
        step = float((zz.first_step * w).sum())
        rows.append({"group": g, "zones": len(zz), "start_saturation": float((zz.start_saturation * w).sum()),
                     "first_step_per_kw": step, "model_solar_per_kw": step * weights["solar"],
                     "model_wind_per_kw": step * weights["wind"],
                     "fit_solar_completed_per_kw": float((zz.fit_solar * w).sum()),
                     "fit_wind_completed_per_kw": float((zz.fit_wind * w).sum())})
    return pd.DataFrame(rows)


def refits(sample: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    def wind_rows(m, label, n):
        p, se = m.result.params, m.result.bse
        out = []
        for c in [c for c in p.index if c.startswith("tech_wind")]:
            out.append({"fit": label, "term": c, "coef": p[c], "se": se[c], "ratio_to_solar": np.exp(p[c]), "n": n,
                        "n_wind_completed": int(((sample_used := m_sample[label]).tech_n.isin(["wind", "wind_completed"])
                                                 & (sample_used.status_n == "completed")).sum()),
                        "n_wind_active": int((sample_used.tech_n.isin(["wind", "wind_active"])
                                              & (sample_used.status_n == "active")).sum())})
        return out

    split = sample.assign(tech_n=np.where(sample.tech_n == "wind", "wind_" + sample.status_n, sample.tech_n))
    common = sample[sample.regime.isin(WIND_COMMON)]
    m_sample = {"as fitted": sample, "split by status": split, "SPP+MISO+PJM": common,
                "SPP+MISO+PJM, split by status": split[split.regime.isin(WIND_COMMON)]}
    rows = []
    for label, s in m_sample.items():
        m = estimate.fit(s, cfg)
        rows += wind_rows(m, label, int(m.result.nobs))
    return pd.DataFrame(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outputs", type=Path, required=True)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--out-dir", type=Path)
    a = ap.parse_args(argv)
    cfg = cli.load_cfg(a.config)
    out = a.out_dir or a.outputs
    sample = pd.read_csv(a.outputs / "estimation_sample.csv")
    model = estimate.fit(sample, cfg)
    weights = cfg["saturation"]["tech_weights"]
    obs = observed(sample)
    mod = modelled(model, pd.read_csv(a.outputs / "zones_atts_s0.csv"), pd.read_csv(a.outputs / "tranches_atts_s0.csv"),
                   weights)
    ref = refits(sample, cfg)
    obs.to_csv(out / "wind_price_observed.csv", index=False)
    mod.to_csv(out / "wind_price_model.csv", index=False)
    ref.to_csv(out / "wind_price_refits.csv", index=False)
    pd.set_option("display.width", 200)
    print(obs.round(1).to_string(index=False))
    print(mod.round(3).to_string(index=False))
    print(ref.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
