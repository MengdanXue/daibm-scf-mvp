from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_canonical_json(
    path: str | Path,
    payload: Any,
) -> Path:
    target = Path(path)
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    target.write_bytes(encoded)
    return target
