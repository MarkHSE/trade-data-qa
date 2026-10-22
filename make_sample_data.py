"""
make_sample_data.py -- build a small synthetic customs panel with known errors.

The real pipeline runs on certified USITC DataWeb pulls (HS-10, monthly).
This script was written for this repository: it produces a small stand-in with
the same schema so screen_anomalies.py can be run without DataWeb access, and
plants the patterns the screen exists to catch:

  * a new entrant from a near-zero base (modelled on Turkey crude, Apr 2026)
  * a one-month spike against a destination's own history
  * one HS-10 line reported in kilograms inside a barrel-denominated heading
    (the reason screen_anomalies.py has --barrels-only)

Usage:
    python make_sample_data.py          # writes data/imports.parquet, data/exports.parquet
"""
from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(7)
months = pd.date_range("2024-01-01", "2026-06-01", freq="MS")
OUT = Path("data")
OUT.mkdir(exist_ok=True)


def series(base, noise=0.08):
    return np.maximum(base * (1 + rng.normal(0, noise, len(months))), 0)


rows = []
# crude imports, barrels
for country, base in [("Canada", 1.2e8), ("Mexico", 1.5e7), ("Brazil", 6e6), ("Netherlands", 2e6)]:
    for d, q in zip(months, series(base)):
        rows.append(("import", d, "270900", "2709002090", country, "barrels", q))
# planted: Turkey arrives from nothing
for d in months:
    q = 4.4e6 if d == pd.Timestamp("2026-04-01") else (8e4 if d.month % 7 == 0 else None)
    if q:
        rows.append(("import", d, "270900", "2709002090", "Turkey", "barrels", q))
# planted: one-month spike for Netherlands
rows = [r if not (r[4] == "Netherlands" and r[1] == pd.Timestamp("2026-05-01"))
        else r[:6] + (9e6,) for r in rows]

# refined products exports, barrels, plus one kilogram line in the same heading
for country, base in [("Mexico", 2e7), ("Canada", 5e6)]:
    for d, q in zip(months, series(base)):
        rows.append(("export", d, "271019", "2710191630", country, "barrels", q))
        rows.append(("export", d, "271019", "2710199000", country, "kilograms", q * 40))

df = pd.DataFrame(rows, columns=["flow", "date", "hs6", "hs10", "country", "unit", "quantity"])

for flow in ("import", "export"):
    part = df[df.flow == flow].drop(columns="flow")
    part.to_parquet(OUT / f"{flow}s.parquet", index=False)
print(f"wrote {len(df)} rows to {OUT}/")
