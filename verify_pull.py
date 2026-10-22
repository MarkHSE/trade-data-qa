#!/usr/bin/env python3
"""
Verify an assembled parquet file against DataWeb itself: for each year, run
one cheap, fully-aggregated API call (no district/country/commodity
breakout -- exactly the shape captured live from the DataWeb UI on
2026-07-23/24) for BOTH value and quantity, and compare against the file's
own totals for the same slice.

This exists because a pull can look successful (no errors, plausible row
count) while actually being incomplete or on the wrong basis -- exactly
what happened with the interrupted import_2026-07-18 pull. This check
would have caught that in seconds instead of the hours it actually took.

Usage:
    python verify_pull.py --file data/parsed/import_2026-07-24_patched.parquet \
        --flow import --import-type consumption --basis cif

    # check only specific codes/years instead of everything in the file
    python verify_pull.py --file data/parsed/export_2026-07-24_patched.parquet \
        --flow export --codes 310100 310229 310250 310260 --years 2024 2025

WHAT YOU CAN SPECIFY (all optional -- omit any and it's asked or defaulted):
    --file           path to the parquet to check                  [always asked if missing]
    --flow           export / import                                [read from file if present]
    --import-type    consumption / general  (imports only)          [default: consumption]
    --basis          cif / customs          (imports only)          [read from file if present]
    --codes          specific HS6 codes to check, space-separated   [default: every code in the file]
    --years          specific years to check, space-separated       [default: every year in the file]
    --tolerance-pct  allowed %% difference before flagging mismatch  [default: 0.1]

Run with no arguments at all for a fully interactive, full-file check.
Run with --help to see this message without doing anything.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

# fetch_dataweb (authentication and DataWeb requests) is part of the private
# pipeline. It is imported in main(), after argument parsing, so --help works
# without it; tests substitute a stub for F.
F = None  # set in main(): headers_for, load_token, run_report, MEASURE_TOKENS, MONTH_NUM


def build_aggregate_payload(flow: str, import_type: str, basis: str,
                            codes: list[str], year: int,
                            months: list[int] | None = None) -> dict:
    """Same shape as the live DataWeb captures from 2026-07-23/24, but
    filtered to a specific code list (list, not 'all') and fully
    aggregated (no district/country/commodity breakout) -- one cheap call
    returns the whole year's total in a handful of rows."""
    key = (flow, import_type, basis) if flow == "import" else (flow, "domestic", "fas")
    if key not in F.MEASURE_TOKENS:
        sys.exit(f"ERROR: no measure tokens known for {key}")
    qty_tok, val_tok = F.MEASURE_TOKENS[key]

    if flow == "export":
        trade_type = "Export"
    elif import_type == "general":
        trade_type = "GenImp"
    else:
        trade_type = "Import"

    codes = [str(c) for c in codes]

    # Mirror whatever period the FILE actually covers. A fullYears request
    # sums every month DataWeb has, so checking a part-year file (e.g. a
    # single-month --period pull) against it reports a huge bogus mismatch:
    # the file is correct, the comparison window is not.
    if months and sorted(months) != list(range(1, 13)):
        timeframe = {"timeframeSelectType": "specificDateRange", "years": [],
                     "startDate": f"{min(months):02d}/{year}",
                     "endDate": f"{max(months):02d}/{year}"}
    else:
        timeframe = {"timeframeSelectType": "fullYears", "years": [str(year)],
                     "startDate": None, "endDate": None}

    return {
        "savedQueryType": "", "savedQueryName": "", "savedQueryDesc": "",
        "isOwner": True, "runMonthly": False, "unitConversion": "0", "manualConversions": [],
        "reportOptions": {"tradeType": trade_type, "classificationSystem": "HTS"},
        "searchOptions": {
            "MiscGroup": {
                "districts": {"aggregation": "Aggregate District", "districtGroups": {"userGroups": []},
                              "districts": [], "districtsExpanded": [{"name": "All Districts", "value": "all"}],
                              "districtsSelectType": "all"},
                "importPrograms": {"aggregation": None, "importPrograms": [], "programsSelectType": "all"},
                "extImportPrograms": {"aggregation": "Aggregate CSC", "extImportPrograms": [],
                                      "extImportProgramsExpanded": [], "programsSelectType": "all"},
                "provisionCodes": {"aggregation": "Aggregate RPCODE", "provisionCodesSelectType": "all",
                                   "rateProvisionCodes": [],
                                   "rateProvisionCodesExpanded": [
                                       {"name": "All Rate Provision Codes", "value": "all"}],
                                   "rateProvisionGroups": {"systemGroups": []}}},
            "commodities": {"aggregation": "Aggregate Commodities", "codeDisplayFormat": "YES",
                            "commodities": codes, "commoditiesExpanded": [{"name": "", "value": c} for c in codes],
                            "commoditiesManual": ";".join(codes),
                            "commodityGroups": {"systemGroups": [], "userGroups": []},
                            "commoditySelectType": "list", "granularity": "10",
                            "groupGranularity": None, "searchGranularity": None, "showHTSValidDetails": ""},
            "componentSettings": {"dataToReport": [qty_tok, val_tok], "scale": "1",
                                  **timeframe, "startMonth": None, "endMonth": None,
                                  "yearsTimeline": "Monthly"},
            "countries": {"aggregation": "Aggregate Countries", "countries": [],
                          "countriesExpanded": [{"name": "All Countries", "value": "all"}],
                          "countriesSelectType": "all", "countryGroups": {"systemGroups": [], "userGroups": []}}},
        "sortingAndDataFormat": {
            "DataSort": {"columnOrder": [], "fullColumnOrder": [], "sortOrder": []},
            "reportCustomizations": {"exportCombineTables": False, "totalRecords": "20000",
                                     "exportRawData": False}},
        "deletedCountryUserGroups": [], "deletedCommodityUserGroups": [], "deletedDistrictUserGroups": []}


def sum_all_months(df: pd.DataFrame) -> float:
    """Sum every month column present (Jan..Dec) across every row -- works
    regardless of how many rows the aggregate call returns."""
    total = 0.0
    for col in df.columns:
        if col in F.MONTH_NUM:
            total += pd.to_numeric(
                df[col].astype(str).str.replace(",", "", regex=False), errors="coerce"
            ).fillna(0).sum()
    return total


def get_aggregate_totals(headers, flow, import_type, basis, codes, year, months=None):
    """One cheap API call -> (value_total, quantity_total) for the period
    the file covers (whole year, or the file's own month span)."""
    payload = build_aggregate_payload(flow, import_type, basis, codes, year, months)
    value_df, qty_df = F.run_report(headers, payload)
    return sum_all_months(value_df), sum_all_months(qty_df)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None, help="assembled parquet to verify")
    ap.add_argument("--flow", default=None, choices=["export", "import"])
    ap.add_argument("--import-type", choices=["consumption", "general"], default=None)
    ap.add_argument("--basis", choices=["cif", "customs"], default=None)
    ap.add_argument("--codes", nargs="+", default=None,
                    help="HS6 codes to check (default: every code in the file)")
    ap.add_argument("--years", nargs="+", type=int, default=None,
                    help="years to check (default: every year in the file)")
    ap.add_argument("--tolerance-pct", type=float, default=0.1,
                    help="allowed %% difference before flagging a mismatch (default 0.1%%)")
    args = ap.parse_args()

    global F
    if F is None:
        try:
            import fetch_dataweb as F
        except ModuleNotFoundError:
            sys.exit("ERROR: verify_pull.py needs the pipeline's fetch_dataweb module "
                     "and a DataWeb API token (not included in this repository).")

    if not args.file:
        args.file = input("Parquet file to verify: ").strip()
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"ERROR: file not found: {path}")
    df = pd.read_parquet(path)

    # Read flow/basis FROM THE FILE where possible -- this is what the file
    # actually claims to be, and is the right default to check against.
    # A --flow/--basis flag on the command line overrides this, for the rare
    # case you deliberately want to check against a different basis than
    # what's stamped in the file (e.g. sanity-checking the stamp itself).
    file_flow = df["flow"].iloc[0] if "flow" in df.columns and len(df) else None
    file_basis_raw = df["value_basis"].iloc[0] if "value_basis" in df.columns and len(df) else None
    file_basis = "cif" if file_basis_raw == "CIF" else ("customs" if file_basis_raw == "Customs" else None)
    file_import_type = (df["import_type"].iloc[0]
                        if "import_type" in df.columns and len(df) else None)
    if file_import_type not in ("consumption", "general"):
        file_import_type = None  # old files without this column, or "unknown"/None

    if not args.flow:
        args.flow = file_flow or input("Flow? [export/import]: ").strip().lower()
    if args.flow == "import" and not args.import_type:
        args.import_type = file_import_type or (
            input("Import type? [consumption/general] (default consumption): ").strip().lower()
            or "consumption"
        )
    elif not args.import_type:
        args.import_type = "consumption"  # unused for exports, harmless default
    if args.flow == "import" and not args.basis:
        args.basis = file_basis or input("Basis? [cif/customs] (default cif): ").strip().lower() or "cif"
    elif not args.basis:
        args.basis = "fas"  # unused for exports (always FAS), harmless placeholder

    codes = args.codes or sorted(df["hs6"].unique().tolist())
    all_years = sorted(df["year"].unique().tolist())
    # Default to EVERY year the file contains. Narrowing to a trailing
    # window assumes older years were verified when first pulled and have
    # not changed since -- neither holds. Files get reassembled from
    # checkpoints, and DataWeb revises. Pass --years to narrow deliberately.
    years = args.years or all_years

    basis_source = "from file" if (file_basis and args.basis == file_basis) else "from you"
    print(f"Verifying {path.name}")
    print(f"  flow={args.flow}  import_type={args.import_type}  basis={args.basis}  ({basis_source})")
    print(f"  {len(codes)} codes, years {years}")
    if args.codes is None and args.years is None:
        print("  (checking every code and year in the file -- "
              "use --codes / --years to narrow this down)")

    headers = F.headers_for(F.load_token())

    print("=" * 109)
    print(f"{'Period':<12}{'Codes':>7}{'File value':>18}{'API value':>18}{'Diff%':>9}{'Value':>10}"
          f"{'File qty':>16}{'API qty':>16}{'Diff%':>9}{'Qty':>10}")
    print("-" * 109)

    all_ok = True
    for year in years:
        sl = df[(df.year == year) & (df.hs6.isin(codes))]
        file_value = sl["value_usd"].sum()
        file_qty = sl["quantity"].sum()
        months = sorted(int(m) for m in sl["month"].unique()) if "month" in sl.columns else None
        # Ask the API for exactly the codes the FILE has in THIS year, not
        # every code anywhere in the file. A taxonomy extension (codes added
        # partway through, e.g. the resin chain from 2026-07) otherwise makes
        # every earlier year look short by the new codes' worth -- a false
        # mismatch across the whole history, with the file perfectly correct.
        year_codes = sorted(sl["hs6"].unique().tolist())
        if not year_codes:
            print(f"{year:<12}  (no rows in file for these codes -- skipped)")
            continue

        api_value = api_qty = 0.0
        try:
            api_value, api_qty = get_aggregate_totals(
                headers, args.flow, args.import_type, args.basis,
                year_codes, year, months
            )
        except Exception as exc:
            print(f"{year:<12}  ERROR fetching aggregate: {exc}")
            all_ok = False
            continue

        tol = args.tolerance_pct / 100
        value_diff_pct = ((file_value - api_value) / api_value * 100) if api_value else float("nan")
        qty_diff_pct = ((file_qty - api_qty) / api_qty * 100) if api_qty else float("nan")
        value_ok = abs(file_value - api_value) <= max(1, abs(api_value) * tol)
        qty_ok = abs(file_qty - api_qty) <= max(1, abs(api_qty) * tol)
        all_ok = all_ok and value_ok and qty_ok

        label = (str(year) if not months or len(months) == 12
                 else f"{year}/{min(months):02d}-{max(months):02d}")
        print(f"{label:<12}{len(year_codes):>7}{file_value:>18,.0f}{api_value:>18,.0f}{value_diff_pct:>8.2f}%"
              f"{'OK' if value_ok else 'MISMATCH':>10}"
              f"{file_qty:>16,.0f}{api_qty:>16,.0f}{qty_diff_pct:>8.2f}%"
              f"{'OK' if qty_ok else 'MISMATCH':>10}")

    print("=" * 109)
    if all_ok:
        print("[verify] all years match within tolerance -- file is complete and consistent")
    else:
        print("[verify] *** MISMATCH DETECTED *** -- do not trust this file until resolved")
    print("=" * 109)
    # Exit status is the contract for automation: non-zero means do not use the file.
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
