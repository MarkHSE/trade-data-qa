"""Pin down verify_pull.py against a stubbed DataWeb API (no token needed)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_pull  # noqa: E402

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def make_file(tmp_path, months=range(1, 13)):
    rows = []
    for year, codes in [(2024, ["310100"]), (2025, ["310100", "310229"])]:
        for code in codes:
            for m in months:
                rows.append(dict(flow="export", hs6=code, year=year, month=m,
                                 value_usd=1000.0, quantity=10.0))
    path = tmp_path / "export.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path, pd.DataFrame(rows)


def stub(file_df, qty_factor=1.0):
    """A fake fetch_dataweb whose API returns the file's own totals (times qty_factor)."""
    calls = []

    def run_report(headers, payload):
        calls.append(payload)
        tf = payload["searchOptions"]["componentSettings"]
        codes = payload["searchOptions"]["commodities"]["commodities"]
        year = int(tf["years"][0]) if tf["years"] else int(tf["startDate"][-4:])
        sl = file_df[(file_df.year == year) & file_df.hs6.isin(codes)]
        by_m = sl.groupby("month")[["value_usd", "quantity"]].sum()
        val = pd.DataFrame({MONTHS[m - 1]: [by_m.loc[m, "value_usd"]] for m in by_m.index})
        qty = pd.DataFrame({MONTHS[m - 1]: [by_m.loc[m, "quantity"] * qty_factor] for m in by_m.index})
        return val, qty

    return SimpleNamespace(
        MEASURE_TOKENS={("export", "domestic", "fas"): ("QTY", "VAL")},
        MONTH_NUM={name: i + 1 for i, name in enumerate(MONTHS)},
        headers_for=lambda token: {}, load_token=lambda: "x",
        run_report=run_report), calls


def run(monkeypatch, path, fake):
    monkeypatch.setattr(verify_pull, "F", fake)
    monkeypatch.setattr(sys, "argv", ["verify_pull.py", "--file", str(path)])
    with pytest.raises(SystemExit) as e:
        verify_pull.main()
    return e.value.code


def test_matching_file_exits_zero(tmp_path, monkeypatch):
    path, df = make_file(tmp_path)
    fake, _ = stub(df)
    assert run(monkeypatch, path, fake) == 0


def test_mismatch_exits_nonzero(tmp_path, monkeypatch):
    path, df = make_file(tmp_path)
    fake, _ = stub(df, qty_factor=1.05)
    assert run(monkeypatch, path, fake) == 1


def test_asks_only_for_each_years_own_codes(tmp_path, monkeypatch):
    path, df = make_file(tmp_path)
    fake, calls = stub(df)
    run(monkeypatch, path, fake)
    codes = [c["searchOptions"]["commodities"]["commodities"] for c in calls]
    assert codes == [["310100"], ["310100", "310229"]]


def test_part_year_file_asks_for_its_own_months(tmp_path, monkeypatch):
    path, df = make_file(tmp_path, months=range(1, 7))
    fake, calls = stub(df)
    assert run(monkeypatch, path, fake) == 0
    tf = calls[0]["searchOptions"]["componentSettings"]
    assert tf["timeframeSelectType"] == "specificDateRange"
    assert (tf["startDate"], tf["endDate"]) == ("01/2024", "06/2024")
