"""Gemeinsame Test-Einstellungen."""
from __future__ import annotations

import gc

import pytest


@pytest.fixture(autouse=True)
def _collect_garbage_between_tests():
    """Speicherbereinigung nach jedem Test, nicht zu einem zufälligen Zeitpunkt.

    Die Oberflächentests lassen ihre Fenster stehen (ein Abräumen führte mit PySide6 6.12 zu Abstürzen). Räumt Pythons
    Speicherbereinigung solche Qt-Objekte mitten in einem Qt-Aufruf ab (z.B. während setStyle alle Fenster neu
    formatiert), stürzt der Testlauf ab – je nach Zeitpunkt, etwa nur mit Abdeckungsmessung. Zwischen zwei Tests ist
    das Abräumen ungefährlich.
    """
    yield
    gc.collect()
