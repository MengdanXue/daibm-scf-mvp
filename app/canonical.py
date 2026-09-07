"""The one canonical JSON encoding used for every integrity hash in ``app``.

Every SHA-256 this application treats as evidence -- ledger event hashes,
calibration dataset digests, invoice-limit proof digests -- is taken over the
output of this module. Keeping a single encoder is not tidiness: the digest
*is* the claim, so two encoders that merely happen to agree today are a
silent divergence waiting to happen. ``OutcomeService._fallback_summary``
depends on exactly that agreement, and ``tests/test_canonical.py`` pins it.

``allow_nan=False`` is deliberate. ``json.dumps`` otherwise emits bare ``NaN``
and ``Infinity``, which are not JSON, cannot be stored in ``jsonb``, and would
make a hash unreproducible by any other reader. Failing loudly at the point of
encoding is the only outcome that keeps a digest meaningful.

``research`` deliberately keeps its own encoders: they hash different things
(ASCII-only graph snapshots, indented artifact files read back from disk) and
must not be coupled to this one, which would also invert the package
dependency, since ``app`` imports ``research``.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["canonical_bytes", "canonical_json"]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")
