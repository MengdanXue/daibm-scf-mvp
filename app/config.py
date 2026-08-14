from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def database_path() -> Path:
    configured = os.getenv("DAIBM_DB_PATH", "data/daibm_scf.db")
    path = Path(configured)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

