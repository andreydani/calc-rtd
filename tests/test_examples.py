"""The scripts in examples/ are part of the documentation: they must run."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def run(script, *args):
    completed = subprocess.run([sys.executable, str(EXAMPLES / script)] + list(args),
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def test_print_updates():
    out = run("print_updates.py", "--seconds", "2")
    assert "walk ACME 38.5" in out and "clock" in out


def test_log_to_csv(tmp_path):
    target = tmp_path / "quotes.csv"
    out = run("log_to_csv.py", str(target), "--seconds", "2")
    rows = target.read_text(encoding="utf-8").splitlines()
    assert "wrote %d rows" % len(rows) in out and len(rows) >= 2
    stamp, topic, value = rows[0].split(",")
    assert topic in ("walk/ACME/38.5", "walk/GLOBEX/112.4") and float(value) > 0 and "T" in stamp


def test_latest_values():
    out = run("latest_values.py", "--seconds", "2")
    assert out.count("spread") >= 2


def test_alert_callback():
    out = run("alert_callback.py", "--low", "1000", "--high", "2000", "--seconds", "1.5")
    assert "ALERT: ACME at" in out and "updates checked" in out


@pytest.mark.skipif(importlib.util.find_spec("pandas") is None, reason="pandas is not installed")
def test_to_pandas():
    out = run("to_pandas.py", "--seconds", "3")
    assert "ACME" in out and "GLOBEX" in out and "updates" in out
