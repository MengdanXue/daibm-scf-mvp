from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import app.services.outcome_calibration as calibration_module
from app.services.outcome_calibration import (
    CalibrationObservation,
    _metrics,
    assign_stratified_folds,
    build_calibration_candidate,
    recover_candidate_artifact,
    stage_candidate_artifact,
    verify_candidate_artifact,
    write_candidate_artifact,
)


def _observation(
    index: int,
    score: float,
    defaulted: bool,
    *,
    correction_head_id: str | None = None,
    observed_at: str | None = None,
) -> CalibrationObservation:
    timestamp = observed_at or (
        datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    ).isoformat(timespec="microseconds")
    return CalibrationObservation(
        outcome_id=f"00000000-0000-0000-0000-{index + 1:012d}",
        facility_id=f"10000000-0000-0000-0000-{index + 1:012d}",
        request_id=f"20000000-0000-0000-0000-{index + 1:012d}",
        risk_assessment_id=f"30000000-0000-0000-0000-{index + 1:012d}",
        model_version_id=f"40000000-0000-0000-0000-{index + 1:012d}",
        risk_engine_version="tgnn-test@v1",
        risk_input_sha256=f"{index + 1:064x}",
        evidence_sha256=f"{index + 2:064x}",
        original_score=score,
        defaulted=defaulted,
        observed_at=timestamp,
        provenance="CONTROLLED_DEMO",
        correction_head_id=correction_head_id,
    )


def _varied_observations() -> tuple[CalibrationObservation, ...]:
    return tuple(
        _observation(index, 0.05 + index * 0.045, index >= 10)
        for index in range(20)
    )


def test_candidate_is_deterministic_and_reports_oof_metrics():
    observations = _varied_observations()

    first = build_calibration_candidate(observations)
    second = build_calibration_candidate(tuple(reversed(observations)))

    assert first == second
    assert first.sample_count == 20
    assert first.positive_count == 10
    assert first.negative_count == 10
    assert first.status == "eligible_candidate"
    assert first.distinct_score_count == 20
    assert first.metrics_before["brier_score"] == pytest.approx(
        sum(
            (row.original_score - float(row.defaulted)) ** 2
            for row in observations
        )
        / 20
    )
    assert first.artifact["artifact_schema"] == "daibm.platt-calibration.v3"
    assert first.artifact["validation"]["method"] == (
        "deterministic_stratified_5_fold_oof"
    )
    assert first.artifact["validation"]["metrics_after"] == first.metrics_after
    assert first.artifact["diagnostics"]["final_fit"]["metrics"] != (
        first.metrics_after
    )
    assert json.dumps(
        first.artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") == first.artifact_bytes


def test_oof_fit_inputs_exclude_every_held_out_observation(monkeypatch):
    observations = _varied_observations()
    assignments = assign_stratified_folds(observations)
    calls: list[tuple[tuple[float, ...], tuple[float, ...]]] = []
    real_fit = calibration_module._fit_platt

    def recording_fit(scores, labels, config):
        calls.append((tuple(scores.tolist()), tuple(labels.tolist())))
        return real_fit(scores, labels, config)

    monkeypatch.setattr(calibration_module, "_fit_platt", recording_fit)

    candidate = build_calibration_candidate(observations)

    assert len(calls) == 6
    for fold, (training_scores, training_labels) in enumerate(calls[:5]):
        expected_training = {
            row.original_score
            for row in observations
            if assignments[row.outcome_id] != fold
        }
        held_out = {
            row.original_score
            for row in observations
            if assignments[row.outcome_id] == fold
        }
        assert set(training_scores) == expected_training
        assert set(training_scores).isdisjoint(held_out)
        assert set(training_labels) == {0.0, 1.0}
    assert set(calls[-1][0]) == {row.original_score for row in observations}
    held_out_ids = [
        outcome_id
        for fold_evidence in candidate.artifact["validation"]["folds"]
        for outcome_id in fold_evidence["held_out_outcome_ids"]
    ]
    assert sorted(held_out_ids) == sorted(row.outcome_id for row in observations)
    for fold_evidence in candidate.artifact["validation"]["folds"]:
        assert set(fold_evidence["training_outcome_ids"]).isdisjoint(
            fold_evidence["held_out_outcome_ids"]
        )


def test_fold_assignment_is_stratified_canonical_and_order_independent():
    observations = _varied_observations()

    assignment = assign_stratified_folds(observations)
    reversed_assignment = assign_stratified_folds(tuple(reversed(observations)))

    assert assignment == reversed_assignment
    assert sorted(assignment.values()) == [
        0, 0, 0, 0, 1, 1, 1, 1, 2, 2,
        2, 2, 3, 3, 3, 3, 4, 4, 4, 4,
    ]
    for fold in range(5):
        labels = {
            row.defaulted
            for row in observations
            if assignment[row.outcome_id] == fold
        }
        assert labels == {False, True}


def test_fold_hash_changes_only_when_assignment_changes():
    observations = _varied_observations()
    baseline = build_calibration_candidate(observations)
    score_changed = tuple(
        replace(row, original_score=row.original_score + 0.001)
        if row is observations[0]
        else row
        for row in observations
    )
    assignment_changed = tuple(
        replace(row, observed_at="2026-09-01T00:00:00.000000+00:00")
        if row is observations[0]
        else row
        for row in observations
    )

    score_candidate = build_calibration_candidate(score_changed)
    assignment_candidate = build_calibration_candidate(assignment_changed)
    assert score_candidate.fold_assignment_sha256 == baseline.fold_assignment_sha256
    assert score_candidate.dataset_sha256 != baseline.dataset_sha256
    assert assignment_candidate.fold_assignment_sha256 != (
        baseline.fold_assignment_sha256
    )
    canonical_assignment = json.dumps(
        assign_stratified_folds(observations),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert baseline.fold_assignment_sha256 == hashlib.sha256(
        canonical_assignment
    ).hexdigest()


def test_metrics_match_hand_calculated_brier_and_log_loss_with_clamping():
    metrics = _metrics(
        np.asarray([0.0, 1.0]),
        np.asarray([0.0, 1.0]),
        0.1,
    )

    assert metrics["brier_score"] == pytest.approx(0.01, abs=1e-15)
    assert metrics["log_loss"] == pytest.approx(-math.log(0.9), abs=1e-15)


@pytest.mark.parametrize(
    "probabilities",
    (np.asarray([math.nan]), np.asarray([math.inf]), np.asarray([-0.1])),
)
def test_metrics_reject_malformed_probabilities(probabilities):
    with pytest.raises(ValueError, match="probabilities"):
        _metrics(np.asarray([0.0]), probabilities, 1e-6)


def test_dataset_hash_tracks_correction_head_lineage():
    observations = _varied_observations()
    corrected = tuple(
        replace(
            row,
            correction_head_id="50000000-0000-0000-0000-000000000001",
        )
        if row is observations[0]
        else row
        for row in observations
    )

    original = build_calibration_candidate(observations)
    changed = build_calibration_candidate(corrected)

    assert changed.dataset_sha256 != original.dataset_sha256
    assert changed.artifact["dataset"]["correction_heads"][
        observations[0].outcome_id
    ] == "50000000-0000-0000-0000-000000000001"


def test_duplicate_outcome_ids_are_rejected_before_fold_assignment():
    observations = _varied_observations()
    duplicate = observations[:-1] + (
        replace(observations[-1], outcome_id=observations[0].outcome_id),
    )

    with pytest.raises(ValueError, match="unique"):
        assign_stratified_folds(duplicate)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ({"observed_at": "2026-08-01T00:00:00"}, "timezone-aware"),
        ({"correction_head_id": "not-a-uuid"}, "correction head"),
        ({"risk_input_sha256": "xyz"}, "SHA-256"),
    ),
)
def test_malformed_observations_are_rejected_early(mutation, message):
    observations = _varied_observations()
    malformed = (replace(observations[0], **mutation),) + observations[1:]

    with pytest.raises(ValueError, match=message):
        build_calibration_candidate(malformed)


def test_candidate_requires_the_approved_oof_sample_and_class_support():
    with pytest.raises(ValueError, match="at least 20"):
        build_calibration_candidate(_varied_observations()[:-1])
    unsupported = tuple(
        replace(row, defaulted=index >= 16)
        for index, row in enumerate(_varied_observations())
    )
    with pytest.raises(ValueError, match="at least five observations per class"):
        build_calibration_candidate(unsupported)


def test_identical_scores_preserve_count_evidence_for_the_gate():
    candidate = build_calibration_candidate(
        tuple(replace(row, original_score=0.5595) for row in _varied_observations())
    )

    assert candidate.status == "eligible_candidate"
    assert candidate.distinct_score_count == 1


def test_candidate_artifact_is_atomically_written_and_verified(tmp_path):
    candidate = build_calibration_candidate(_varied_observations())
    expected_sha256 = hashlib.sha256(candidate.artifact_bytes).hexdigest()

    first = write_candidate_artifact(tmp_path, candidate)
    second = write_candidate_artifact(tmp_path, candidate)

    assert first == second
    assert first.sha256 == expected_sha256
    assert first.path == tmp_path / f"{expected_sha256}.json"
    assert first.path.read_bytes() == candidate.artifact_bytes
    assert verify_candidate_artifact(first.path, first.sha256) == "verified"
    assert list(tmp_path.iterdir()) == [first.path]


def test_candidate_artifact_verifier_reports_missing_and_mismatch(tmp_path):
    missing = tmp_path / f"{'a' * 64}.json"
    assert verify_candidate_artifact(missing, "a" * 64) == "missing"

    missing.write_bytes(b"tampered")
    assert verify_candidate_artifact(missing, "a" * 64) == "mismatch"


def test_staged_artifact_is_not_visible_until_commit_recovery(tmp_path):
    candidate = build_calibration_candidate(_varied_observations())

    staged = stage_candidate_artifact(tmp_path, candidate)

    assert staged.staged_path is not None and staged.staged_path.is_file()
    assert not staged.path.exists()
    assert recover_candidate_artifact(staged.path, staged.sha256) == "verified"
    assert staged.path.read_bytes() == candidate.artifact_bytes
    assert not staged.staged_path.exists()


def test_recovery_treats_a_concurrent_successful_rename_as_verified(
    tmp_path,
    monkeypatch,
):
    candidate = build_calibration_candidate(_varied_observations())
    staged = stage_candidate_artifact(tmp_path, candidate)
    real_replace = __import__("os").replace

    def concurrent_replace(source, destination):
        real_replace(source, destination)
        raise FileNotFoundError("another reader completed the rename")

    monkeypatch.setattr(
        "app.services.outcome_calibration.os.replace",
        concurrent_replace,
    )

    assert recover_candidate_artifact(staged.path, staged.sha256) == "verified"
