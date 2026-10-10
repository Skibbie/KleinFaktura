"""Programm-Update: Versionsvergleich, ZIP entpacken, Austausch-Skript (echt unter Windows)."""
from __future__ import annotations

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import update  # noqa: E402


def test_versions():
    assert update.is_newer("1.2.1", "1.2.0") and update.is_newer("v1.10.0", "1.9.3")
    assert not update.is_newer("1.2.0", "1.2.0") and not update.is_newer("1.1.9", "1.2.0")
    assert not update.is_newer("kaputt", "1.2.0")


def test_latest_release(monkeypatch):
    body = b'{"tag_name": "v1.3.0", "body": "- Neu\\n", "assets": [{"name": "KleinFaktura-1.3.0.zip"}]}'
    monkeypatch.setattr(update, "_open", lambda url, timeout, accept="": io.BytesIO(body))
    r = update.latest_release()
    assert r.version == "1.3.0" and r.notes == "- Neu"
    assert update.zip_asset(r)["name"] == "KleinFaktura-1.3.0.zip"
    with pytest.raises(update.UpdateError):
        update.zip_asset(update.Release("1.3.0", "", [{"name": "Anderes-Programm-1.3.0.zip"}]))


def test_check_digest():
    update.check_digest({}, "abc")                                 # ohne Prüfsumme von GitHub: nichts zu prüfen
    update.check_digest({"digest": "sha256:ABC"}, "abc")
    with pytest.raises(update.UpdateError):
        update.check_digest({"digest": "sha256:abc"}, "def")


def _zip(path: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


def test_extract(tmp_path):
    z = _zip(tmp_path / "a.zip", {"KleinFaktura/KleinFaktura.exe": "exe", "KleinFaktura\\_internal\\x.dll": "dll",
                                  "KleinFaktura/CHANGELOG.md": "log", "fremd.txt": "x"})
    new = tmp_path / "KleinFaktura.neu"
    update.extract(z, new)
    assert (new / "KleinFaktura.exe").read_text() == "exe" and (new / "_internal" / "x.dll").exists()
    assert not (new / "fremd.txt").exists()
    with pytest.raises(update.UpdateError):
        update.extract(_zip(tmp_path / "b.zip", {"KleinFaktura/../boese.txt": "x"}), tmp_path / "b")
    with pytest.raises(update.UpdateError):
        update.extract(_zip(tmp_path / "c.zip", {"KleinFaktura/nur.txt": "x"}), tmp_path / "c")
    assert not (tmp_path / "c").exists()


def _run_helper(tmp_path, source: Path, target: Path, setup: bool = False) -> tuple[int, str]:
    waiter = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    log = tmp_path / "update.log"
    script = update.write_helper(tmp_path)
    r = subprocess.run(update.helper_command(script, waiter.pid, source, target, log, start=False, setup=setup),
                       timeout=90)
    assert waiter.poll() is not None  # Skript hat auf das Programmende gewartet
    return r.returncode, log.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell-Skript nur unter Windows")
def test_helper_swaps_folder(tmp_path):
    target, source = tmp_path / "KleinFaktura", tmp_path / "KleinFaktura.neu"
    target.mkdir()
    (target / "alt.txt").write_text("alt")
    source.mkdir()
    (source / "neu.txt").write_text("neu")
    code, log = _run_helper(tmp_path, source, target)
    assert code == 0, log
    assert (target / "neu.txt").exists() and not source.exists()
    assert (tmp_path / "KleinFaktura.vorher" / "alt.txt").exists()
    assert "Update installiert" in log


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell-Skript nur unter Windows")
def test_helper_restores_on_failure(tmp_path):
    target = tmp_path / "KleinFaktura"
    target.mkdir()
    (target / "alt.txt").write_text("alt")
    code, log = _run_helper(tmp_path, tmp_path / "fehlt", target)
    assert code == 1 and "FEHLER" in log
    assert (target / "alt.txt").exists()  # alte Version wieder an ihrem Platz


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell-Skript nur unter Windows")
@pytest.mark.parametrize("code", [0, 3])
def test_helper_runs_setup(tmp_path, code):
    """Mit Installer eingerichtet: das Skript führt das neue Setup still mit dem Programmordner aus."""
    target = tmp_path / "KleinFaktura"
    target.mkdir()
    setup = tmp_path / "setup.cmd"                              # Ersatz für KleinFaktura-Setup-x.y.z.exe
    setup.write_text(f'@echo %* > "{tmp_path / "args.txt"}"\n@exit /b {code}\n')
    rc, log = _run_helper(tmp_path, setup, target, setup=True)
    args = (tmp_path / "args.txt").read_text()
    assert "/VERYSILENT" in args and f'/DIR="{target}"' in args
    if code == 0:
        assert rc == 0 and "per Setup installiert" in log and not setup.exists()
    else:
        assert rc == 1 and "Code 3" in log


def test_installed_by_setup(tmp_path):
    assert not update.installed_by_setup(tmp_path)
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert update.installed_by_setup(tmp_path)
    r = update.Release("2.1.0", "", [{"name": "KleinFaktura-Setup-2.1.0.exe"}])
    assert update.setup_asset(r)["name"] == "KleinFaktura-Setup-2.1.0.exe"
