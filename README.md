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
and DataWeb revises history.

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
would otherwise read as zero trade). Its month calendar is generated rather than read from the
data, so a trailing window of 12 months is always 12 calendar months.

## Run it

```bash
pip install -r requirements.txt
python make_sample_data.py
python screen_anomalies.py --imports data/imports.parquet --exports data/exports.parquet --hs6 270900 --barrels-only
```

On the sample data the screen flags the planted Netherlands spike and Turkey as a new entrant.

`verify_pull.py` calls the live DataWeb API. It needs a DataWeb API token and the pipeline's
`fetch_dataweb` module (authentication and request handling), which is not included here.

## Known limitations

These are deliberate scope limits of the screen, listed so its flags are read correctly:

- **Upward moves only.** It flags spikes and arrivals, not collapses or exits. A partner falling
  to zero is not flagged yet.
- **"NEW ENTRANT" means newly material,** not necessarily first-ever trade: a partner returning
  after a year's absence fires the same rule.
- **One set of thresholds.** The volume floors suit crude in barrels; other commodity families
  need their own floors, as the code comments note.
- **Z-scores suit steady flows best.** For partners that ship one cargo every few months, a
  normal shipment can look unusual.
- **Truncated edges.** A file missing its latest month has no interior gap, so neither script
  sees it. The as-of month has to be checked against the release calendar.

## Design choices

- Raw pulls are immutable; all transformation happens downstream.
- Results are written to CSV as well as printed, so they can be reproduced and compared.
- Checks are simple and explainable on purpose, so every flag can be defended to someone who did
  not write the code.

## Stack

Python 3.10+, DuckDB, pandas, Parquet, DataWeb API. The full pipeline also uses the UN Comtrade API for
mirror-statistics reconciliation (US figures against partner-reported figures), Altair and Marimo.
