"""
screen_anomalies.py — standing anomaly screen for the G&M DataWeb panel.

Question it answers: which country-code-month observations broke from their own
recent history, including destinations that appeared from nothing?

Why it exists: stacked area charts show composition, not deviation. A small-base
mover renders as an invisible sliver — Turkey's crude imports went from about 0.1m to
more than 4m bbl/month in Apr 2026 and could not be seen on the chart at all.
Visual inspection is not a substitute for a screen.

Grain: hs6 (or hs10) x country x month, per flow.

Two rules, because one is not enough:
  1. Z-SPIKE     — z-score vs the country's own trailing 12 months. Judges each
                   destination on its own scale, so a large absolute mover in a
                   small market is caught.
  2. NEW ENTRANT — near-zero trailing baseline plus meaningful current volume.
                   Needed because z-score is undefined when trailing sd ~ 0,
                   which is exactly the case that matters most (Greece, Croatia:
                   prior volume literally zero).

Usage:
    python screen_anomalies.py --imports data/imports.parquet --exports data/exports.parquet --hs6 270900 --barrels-only
    python screen_anomalies.py ... --level hs10 --since 2026-01-01
"""

import argparse
import duckdb


# --- tuning parameters -------------------------------------------------------
# VOLUME_FLOOR is the parameter that matters. Too low and every one-off cargo
# fires; too high and the next Turkey is missed. Set per commodity family:
# crude/products in barrels want ~5e5; coal in metric tons wants far lower.
VOLUME_FLOOR = 5e5      # ignore observations below this quantity
Z_THRESHOLD = 3.0       # z-score above which a spike is flagged
ENTRANT_BASELINE = 1e5  # trailing average below this counts as "absent"
ENTRANT_CURRENT = 1e6   # ...and current volume above this counts as "arrived"
MIN_HISTORY = 12        # months of trailing history required before judging
# ----------------------------------------------------------------------------


def build_view(con, imports: str, exports: str, level: str, hs6: str | None, unit_filter: bool):
    """Stack both flows. Filter to a single unit where the family mixes units.

    The unit filter is not optional hygiene — under 271019 one HS-10 line
    (2710199000, lubricating oils) reports in kilograms while the other 31
    report barrels. Summing across them produces a silently wrong total.
    """
    where = ["quantity is not null"]
    if hs6:
        if not hs6.isdigit():
            raise SystemExit("--hs6 must be numeric, e.g. 270900")
        where.append(f"hs6 = '{hs6}'")
    if unit_filter:
        where.append("unit = 'barrels'")
    clause = " and ".join(where)

    con.read_parquet(imports).create_view("imports")
    con.read_parquet(exports).create_view("exports")
    con.sql(f"""
        create or replace view panel as
        select 'import' as flow, date, hs6, hs10, country, unit, quantity
        from imports where {clause}
        union all
        select 'export' as flow, date, hs6, hs10, country, unit, quantity
        from exports where {clause}
    """)

    code_col = "hs10" if level == "hs10" else "hs6"
    preflight(con, code_col, unit_filter)

    # CRITICAL: DataWeb writes a row only where trade occurred, so a sparse
    # trader has few rows. A window of "12 preceding ROWS" would then span
    # years of calendar time, and a minimum-history filter would drop the
    # intermittent destinations entirely -- which are precisely the new
    # entrants worth catching. Turkey (crude, Apr 2026: ~0.1m -> 4m+ bbl)
    # had only a few rows in the history window and was silently excluded before this fix.
    # Build a dense flow x code x country x month spine and zero-fill.
    con.sql(f"""
        create or replace view observed as
        select flow, {code_col} as code, country, date, sum(quantity) as q
        from panel
        group by 1, 2, 3, 4
    """)
    # The calendar is generated, not read from the data: a month missing from
    # the file must not silently drop out of the trailing window (that would
    # stretch "12 preceding months" to 13 calendar months). preflight() has
    # already refused to run on interior gaps, so every month here is real.
    con.sql("""
        create or replace view calendar as
        select unnest(generate_series(min(date)::date, max(date)::date,
                                      interval 1 month))::date as date
        from observed
    """)
    con.sql("""
        create or replace view monthly as
        select
            k.flow, k.code, k.country, d.date,
            coalesce(o.q, 0) as q
        from (select distinct flow, code, country from observed) k
        cross join calendar d
        left join observed o
               on o.flow = k.flow
              and o.code = k.code
              and o.country = k.country
              and o.date = d.date
    """)


def preflight(con, code_col: str, unit_filter: bool):
    """Refuse to screen input that would make the statistics wrong.

    validate-then-screen is the intended order, but the screen can be run on
    its own, so it checks the three things that silently corrupt its own maths:
    duplicate rows (double-counted months), mixed units inside one code
    (kilograms added to barrels), and months missing from either flow (a
    failed pull that would otherwise read as zero trade).
    """
    dups = con.sql("""
        select flow, date, hs10, country, count(*) as n
        from panel group by all having count(*) > 1
    """).df()
    if len(dups):
        raise SystemExit(f"REFUSED: {len(dups)} duplicate flow/month/hs10/country rows "
                         f"would be double-counted. First:\n{dups.head().to_string(index=False)}")

    mixed = con.sql(f"""
        select flow, {code_col} as code,
               string_agg(distinct unit, ', ' order by unit) as units
        from panel group by all having count(distinct unit) > 1
    """).df()
    if len(mixed):
        hint = "" if unit_filter else " Use --barrels-only or screen at --level hs10."
        raise SystemExit(f"REFUSED: codes mix units and cannot be summed.{hint}\n"
                         f"{mixed.head().to_string(index=False)}")

    gaps = con.sql("""
        with span as (
            select unnest(generate_series(min(date)::date, max(date)::date,
                                          interval 1 month))::date as date
            from panel),
        flows as (select distinct flow from panel)
        select f.flow, s.date
        from flows f cross join span s
        where not exists (select 1 from panel p
                          where p.flow = f.flow and p.date = s.date)
        order by 1, 2
    """).df()
    if len(gaps):
        raise SystemExit(f"REFUSED: months missing from the input (failed pull?), "
                         f"which would read as zero trade:\n{gaps.to_string(index=False)}")


def screen(con, since: str | None):
    """Trailing-window stats computed per flow x code x country series."""
    date_clause = f"and date >= DATE '{since}'" if since else ""

    return con.sql(f"""
        with stats as (
            select *,
                avg(q)    over w as mu,
                stddev(q) over w as sd,
                count(*)  over w as n_hist
            from monthly
            window w as (
                partition by flow, code, country
                order by date
                rows between {MIN_HISTORY} preceding and 1 preceding
            )
        )
        select
            date,
            flow,
            code,
            country,
            round(q  / 1e6, 2) as m_units,
            round(mu / 1e6, 2) as trailing_avg,
            round((q - mu) / nullif(sd, 0), 1) as z,
            case
                when mu < {ENTRANT_BASELINE} and q > {ENTRANT_CURRENT}
                    then 'NEW ENTRANT'
                else 'Z-SPIKE'
            end as rule
        from stats
        where n_hist >= {MIN_HISTORY}
          and q > {VOLUME_FLOOR}
          {date_clause}
          and (
                (q - mu) / nullif(sd, 0) > {Z_THRESHOLD}
                or (mu < {ENTRANT_BASELINE} and q > {ENTRANT_CURRENT})
              )
        order by date desc, abs(q - mu) desc
    """)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--imports", required=True)
    p.add_argument("--exports", required=True)
    p.add_argument("--out", default="out/anomalies.csv")
    p.add_argument("--hs6", help="restrict to one HS-6 heading, e.g. 270900")
    p.add_argument("--level", choices=["hs6", "hs10"], default="hs6",
                   help="aggregate at HS-6 (default) or HS-10")
    p.add_argument("--since", help="only report flags on or after this date")
    p.add_argument("--barrels-only", action="store_true",
                   help="filter unit='barrels'; use for 2709/2710 families")
    p.add_argument("--limit", type=int, default=60,
                   help="rows to print (the CSV always gets every flag)")
    args = p.parse_args()
    if args.since:
        from datetime import date as _d
        try:
            _d.fromisoformat(args.since)
        except ValueError:
            raise SystemExit("--since must be a date like 2026-01-01")

    con = duckdb.connect()
    build_view(con, args.imports, args.exports, args.level, args.hs6, args.barrels_only)
    result = screen(con, args.since)

    df = result.df()
    n = len(df)
    print(f"{n} flagged observations "
          f"(floor {VOLUME_FLOOR:,.0f}, z>{Z_THRESHOLD}, "
          f"entrant if trailing<{ENTRANT_BASELINE:,.0f} and now>{ENTRANT_CURRENT:,.0f})\n")
    print(df.head(args.limit).to_string(index=False))
    from pathlib import Path
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
