"""Pin down screen_anomalies.py: the planted flags, and each preflight refusal."""
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def data(tmp_path):
    subprocess.run([sys.executable, REPO / "make_sample_data.py"], cwd=tmp_path, check=True,
                   capture_output=True)
    return tmp_path / "data"


def screen(tmp_path, imports, exports, *extra):
    out = tmp_path / "anomalies.csv"
    r = subprocess.run([sys.executable, REPO / "screen_anomalies.py", "--imports", imports,
                        "--exports", exports, "--out", out, *extra],
                       capture_output=True, text=True)
    return r, out


def test_flags_the_planted_anomalies(tmp_path, data):
    r, out = screen(tmp_path, data / "imports.parquet", data / "exports.parquet", "--barrels-only")
    assert r.returncode == 0, r.stderr
    flags = pd.read_csv(out)
    assert set(zip(flags.country, flags.rule)) == {("Netherlands", "Z-SPIKE"),
                                                   ("Turkey", "NEW ENTRANT")}


def test_refuses_mixed_units(tmp_path, data):
    r, _ = screen(tmp_path, data / "imports.parquet", data / "exports.parquet")
    assert r.returncode != 0 and "mix units" in r.stderr


def test_hs10_level_keeps_units_apart(tmp_path, data):
    r, out = screen(tmp_path, data / "imports.parquet", data / "exports.parquet", "--level", "hs10")
    assert r.returncode == 0, r.stderr
    assert {"Netherlands", "Turkey"} <= set(pd.read_csv(out).country)


def test_refuses_duplicates(tmp_path, data):
    imp = pd.read_parquet(data / "imports.parquet")
    pd.concat([imp, imp.iloc[[50]]]).to_parquet(tmp_path / "dup.parquet", index=False)
    r, _ = screen(tmp_path, tmp_path / "dup.parquet", data / "exports.parquet", "--barrels-only")
    assert r.returncode != 0 and "duplicate" in r.stderr


def test_refuses_interior_gap_in_one_flow(tmp_path, data):
    imp = pd.read_parquet(data / "imports.parquet")
    imp[imp.date != "2025-06-01"].to_parquet(tmp_path / "gap.parquet", index=False)
    r, _ = screen(tmp_path, tmp_path / "gap.parquet", data / "exports.parquet", "--barrels-only")
    assert r.returncode != 0 and "missing" in r.stderr


def test_accepts_flows_ending_in_different_months(tmp_path, data):
    exp = pd.read_parquet(data / "exports.parquet")
    exp[exp.date < "2026-06-01"].to_parquet(tmp_path / "lag.parquet", index=False)
    r, _ = screen(tmp_path, data / "imports.parquet", tmp_path / "lag.parquet", "--barrels-only")
    assert r.returncode == 0, r.stderr


def test_rejects_bad_since(tmp_path, data):
    r, _ = screen(tmp_path, data / "imports.parquet", data / "exports.parquet",
                  "--barrels-only", "--since", "not-a-date")
    assert r.returncode != 0 and "--since" in r.stderr
