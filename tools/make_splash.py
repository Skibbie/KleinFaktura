"""Erzeugt das Startbild der Exe (gleiches Bild wie das Startfenster beim Quellcode-Start, mit Versionsnummer).

Der PyInstaller-Starter zeigt es an, bevor Python geladen ist. Wird beim Bauen erzeugt, nicht eingecheckt.
Aufruf: python tools/make_splash.py  ->  build/splash.png
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if sys.platform != "win32":  # unter Windows bringt "offscreen" keine Schriften mit
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

import main  # noqa: E402

app = QGuiApplication(sys.argv)
out = ROOT / "build" / "splash.png"
out.parent.mkdir(exist_ok=True)
if not main.splash_pixmap().save(str(out)):
    sys.exit(f"Startbild konnte nicht gespeichert werden: {out}")
print(out)
