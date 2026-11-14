"""GitHub-Workflows müssen gültiges YAML sein – ein Fehler fällt sonst erst beim Release auf (so geschehen bei 2.2.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_is_valid_yaml(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data.get("jobs"), "keine Jobs"
    for name, job in data["jobs"].items():
        for step in job.get("steps", []):
            assert "uses" in step or isinstance(step.get("run"), str), f"{name}: Schritt ohne gültigen Befehl: {step}"


def test_release_workflow_has_build_and_sign_jobs():
    data = yaml.safe_load((WORKFLOWS[0].parent / "release.yml").read_text(encoding="utf-8"))
    assert set(data["jobs"]) == {"build", "sign-release"}
    assert data["jobs"]["sign-release"]["needs"] == "build"
