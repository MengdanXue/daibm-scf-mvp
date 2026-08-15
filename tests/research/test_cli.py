import subprocess
import sys


def test_research_cli_exposes_reproducibility_commands():
    result = subprocess.run(
        [sys.executable, "-m", "research.cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "generate" in result.stdout
    assert "train-xgboost" in result.stdout
    assert "train-tgnn" in result.stdout
    assert "promote" in result.stdout
    assert "build-reference" in result.stdout
    assert "verify" in result.stdout
