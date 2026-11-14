"""Schreibt die Abhängigkeiten für Release-Build und Signatur mit exakten Versionen und Prüfsummen fest.

Aus requirements-build.in und requirements-sign.in entstehen requirements-build.txt und requirements-sign.txt.
Der Release-Workflow installiert nur daraus (`pip install --require-hashes`): ein nachträglich ausgetauschtes oder
neu veröffentlichtes Paket kann so weder in die Exe noch in den Job mit dem Signaturschlüssel gelangen.

Gilt für die Plattform, auf der das Skript läuft – also unter Windows mit der Python-Version des Workflows (3.12)
ausführen. Verwendet `pip lock` (pip ≥ 25.1).

Aufruf: python tools/lock_requirements.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ["requirements-build", "requirements-sign"]


def lock(name: str) -> int:
    source, target = ROOT / f"{name}.in", ROOT / f"{name}.txt"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "pylock.toml"
        result = subprocess.run([sys.executable, "-m", "pip", "lock", "-q", "--only-binary", ":all:",  # noqa: S603
                                 "-r", str(source), "-o", str(out)], cwd=ROOT, check=False)
        if result.returncode != 0:
            return result.returncode
        packages = sorted(tomllib.loads(out.read_text(encoding="utf-8"))["packages"], key=lambda p: p["name"].lower())
    lines = [f"# Automatisch erzeugt aus {source.name} mit tools/lock_requirements.py – nicht von Hand ändern.",
             f"# Plattform: {sys.platform}, Python {sys.version_info.major}.{sys.version_info.minor}",
             "# Installation: python -m pip install --require-hashes --only-binary :all: -r " + target.name]
    for p in packages:
        hashes = sorted({w["hashes"]["sha256"] for w in p.get("wheels", [])})
        if not hashes:
            print(f"{p['name']}: kein Wheel mit Prüfsumme gefunden", file=sys.stderr)
            return 1
        lines.append(f"{p['name']}=={p['version']} \\")
        lines += [f"    --hash=sha256:{h}" + (" \\" if i < len(hashes) - 1 else "") for i, h in enumerate(hashes)]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{target.name}: {len(packages)} Pakete")
    return 0


def main() -> int:
    for name in TARGETS:
        code = lock(name)
        if code:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
