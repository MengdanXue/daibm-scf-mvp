from __future__ import annotations

import hashlib
from dataclasses import asdict

import pytest

from app.canonical import canonical_bytes, canonical_json
from app.ledger import canonical_json as ledger_canonical_json
from app.services.outcome_calibration import (
    CalibrationObservation,
    summarize_calibration_observations,
)
from app.services.outcomes import OutcomeService


def _observations(count: int = 6) -> tuple[CalibrationObservation, ...]:
    return tuple(
        CalibrationObservation(
            outcome_id=f"outcome-{index}",
            facility_id=f"facility-{index}",
            request_id=f"request-{index}",
            risk_assessment_id=f"assessment-{index}",
            model_version_id=None,
            risk_engine_version="transparent_logistic_baseline_v0.1",
            risk_input_sha256="0" * 64,
            evidence_sha256="1" * 64,
            original_score=0.30 + index * 0.05,
            defaulted=bool(index % 2),
            observed_at="2026-01-01T00:00:00.000000+00:00",
            provenance="CONTROLLED_DEMO",
        )
        for index in range(count)
    )


def test_the_ledger_encoder_is_the_shared_encoder():
    # Re-exported for the many call sites that import it from app.ledger; a
    # second implementation behind that name is the failure this pins.
    assert ledger_canonical_json is canonical_json


def test_non_finite_numbers_cannot_enter_a_hash():
    # json.dumps would otherwise emit bare NaN/Infinity: not JSON, not
    # storable in jsonb, and not reproducible by any other reader.
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            canonical_json({"score": value})


def test_encoding_is_key_order_independent_and_utf8_transparent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert canonical_json({"name": "Якорная"}) == '{"name":"Якорная"}'
    assert canonical_bytes({"a": 1}) == canonical_json({"a": 1}).encode("utf-8")


def test_fallback_and_training_paths_agree_on_the_dataset_digest():
    """The two calibration paths must hash a dataset identically.

    OutcomeService records a run whether or not training succeeded, and the
    two branches reach the digest through different code. If they diverge, a
    failed run is attributed to a dataset that never existed -- and nothing
    else in the suite would notice.
    """

    observations = _observations()
    trained = summarize_calibration_observations(observations).dataset_sha256
    fallback = OutcomeService._fallback_summary(observations).dataset_sha256
    assert trained == fallback

    ordered = sorted(observations, key=lambda item: item.outcome_id)
    assert fallback == hashlib.sha256(
        canonical_bytes([asdict(item) for item in ordered])
    ).hexdigest()


def test_the_dataset_digest_ignores_observation_order():
    observations = _observations()
    reversed_order = tuple(reversed(observations))
    assert (
        summarize_calibration_observations(observations).dataset_sha256
        == summarize_calibration_observations(reversed_order).dataset_sha256
    )
