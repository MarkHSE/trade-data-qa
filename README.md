# trade-data-qa

Two data-quality checks from the pipeline behind **Grains & Molecules**, my independent research
on US commodity trade built on USITC DataWeb at HS-10 detail.

The principle behind them: a trade number is not ready to use until I can explain why it is what it
is. Most errors in customs data do not look like errors. They produce totals that are plausible
and wrong. These scripts catch them before anything is charted or published.

## What's here

| File | What it does |
|---|---|
| `verify_pull.py` | Checks a downloaded dataset against the source itself. For each year it makes one cheap, fully aggregated DataWeb API call for value and quantity, compares the totals with the file's own totals for the same codes and months, and flags any difference above tolerance. |
| `screen_anomalies.py` | Flags country × code × month observations that break from their own history. Two rules: a z-score against each partner's trailing 12 months, and a "new entrant" rule for flows that appear from a near-zero base. |
| `tests/` | pytest suites: the planted flags, every refusal, and `verify_pull.py` against a stubbed DataWeb API (no token needed). |
| `make_sample_data.py` | Written for this repository: builds a small synthetic panel with the same schema as the real pulls, so the anomaly screen can be run without DataWeb access. |

## Why each check exists

**`verify_pull.py`: a pull can succeed and still be wrong.** An interrupted import pull once
finished with no errors and a plausible row count, but was incomplete. It took hours to find. This
check would have caught it in seconds. Two refinements came from false alarms, and both are
documented in the code:

- **Compare the same period.** A part-year file checked against a full-year API total produced a
  large mismatch even though the file was correct. The check now asks the API for exactly the
  months the file covers.
- **Compare the same codes.** When new HS codes were added to the taxonomy partway through, every
  earlier year looked short by the new codes' volume. The check now asks the API only for the
  codes the file contains in each year.

It also checks every year by default rather than recent years only, because files get reassembled
and DataWeb revises history, and it exits non-zero on any mismatch so it can stop a pipeline.

**`screen_anomalies.py`: a chart is not a check.**

- **Invisible new entrants.** Turkey's US crude imports went from about 0.1m to more than 4m
  barrels a month in April 2026. On a stacked area chart it was a sliver nobody could see.
- **Sparse rows hide history.** DataWeb only writes a row where trade occurred, so "12 preceding
  rows" can span years for an intermittent partner, and a minimum-history filter silently dropped
  exactly the new entrants worth catching. The fix: build a dense flow × code × country × month
  grid with zero fill before any window function runs.
- **Mixed units.** Under HS 271019, lubricating oils (2710199000) report in kilograms while the
  other 31 lines report barrels. Summing the heading gives a wrong total that still looks
  reasonable. The screen refuses to sum a code whose lines report in different units.

**The screen also refuses input that would corrupt its own statistics:** duplicate rows (a
month counted twice becomes a phantom spike) and months missing from either flow (a failed pull
would otherwise read as zero trade). Gaps are checked inside each flow's own date range, because
imports and exports can legitimately end in different months. Its month calendar is generated rather than read from the
data, so a trailing window of 12 months is always 12 calendar months.

## Run it

```bash
pip install -r requirements.txt
python make_sample_data.py
python screen_anomalies.py --imports data/imports.parquet --exports data/exports.parquet --hs6 270900 --barrels-only
```

On the sample data the screen flags the planted Netherlands spike and Turkey as a new entrant.

Run the tests:

```bash
python -m pytest tests
```

`verify_pull.py` calls the live DataWeb API. It needs a DataWeb API token and the pipeline's
`fetch_dataweb` module (authentication and request handling), which is not included here.

## Scope: a tripwire, not the full classification

`screen_anomalies.py` is deliberately simple: a fast monthly check for upward moves that break
from a partner's own history. Read its flags with that in mind:

- It flags spikes and arrivals, not collapses or exits.
- "NEW ENTRANT" means newly material: a partner returning after a year's absence fires the same rule.
- Its volume floors suit crude in barrels.
- Z-scores suit steady flows; an irregular shipper's normal cargo can look unusual.

In the full pipeline, a separate exception report (not included here) classifies every partner
against the same month, year-to-date and quarter a year earlier, and covers each of these points:

- **Exits and declines:** partners are typed as EXIT, RETREATING or driving the decline, not only as
  increases.
- **True entrants:** ENTRANT requires the partner to have been below 30% of the floor a year
  earlier and above it now.
- **Irregular flows:** partners present in fewer than 60% of the trailing 12 months are typed
  EPISODIC instead of being judged by volatility.
- **Commodity-specific floors:** each code's floor is the larger of a per-family minimum (crude,
  LNG, coal, fertilizer and so on) and 0.5% of the code's total, scaled to the window length.
- **Dominant partners** (above about 40% share) are separated out before the rest of the field is
  judged.

One limit applies to both: a file missing its **latest** month has no interior gap, so the as-of
month has to be checked against the release calendar.

## Design choices

- Raw pulls are immutable; all transformation happens downstream.
- Results are written to CSV as well as printed, so they can be reproduced and compared.
- Checks are simple and explainable on purpose, so every flag can be defended to someone who did
  not write the code.

## Stack

Python 3.10+, DuckDB, pandas, Parquet, DataWeb API. The full pipeline also uses the UN Comtrade API for
mirror-statistics reconciliation (US figures against partner-reported figures), Altair and Marimo.
