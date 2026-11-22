"""Klicktest der ganzen Oberfläche (tools/klicktest.py) in eigenem Prozess: kein aufblitzendes Fenster, kein
flackernder Dialog, keine Ausnahme – einmal mit abgebrochenen, einmal mit bestätigten Dialogen."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "klicktest.py"


@pytest.mark.parametrize("mode", ["reject", "accept"])
def test_klicktest(mode):
    r = subprocess.run([sys.executable, str(TOOL), mode], capture_output=True, text=True, encoding="utf-8",
                       timeout=300, check=False)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "Klicktest" in r.stdout and " 0 Befunde" in r.stdout
