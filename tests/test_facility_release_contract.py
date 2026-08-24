from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts import facility_browser_acceptance


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_public_docs_bound_lifecycle_to_controlled_simulation() -> None:
    public_boundary = _read("README.md") + _read("docs/thesis-traceability.md")

    assert "controlled financing lifecycle simulation" in public_boundary
    assert "does not execute a real bank transfer" in public_boundary


def test_design_and_demo_script_disclose_excluded_financial_functions() -> None:
    implementation_boundary = _read("docs/mvp-design.md") + _read(
        "docs/demo-script.md"
    )

    for excluded_function in (
        "interest",
        "fees",
        "accounting",
    ):
        assert excluded_function in implementation_boundary.lower()
    assert "real bank transfer" in implementation_boundary.lower()


def test_browser_acceptance_has_a_discoverable_cli() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "facility_browser_acceptance.py"),
            "--help",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "approved audited application" in result.stdout
    assert "facility-lifecycle-acceptance.png" in result.stdout


def test_browser_facility_plan_preserves_workflow_cny_without_fx() -> None:
    plan = facility_browser_acceptance._facility_plan({"amount": 1000.01})

    assert plan == {
        "currency": "CNY",
        "principal": "1000.01",
        "installments": ("500.00", "500.01"),
    }
