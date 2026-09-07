from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.services import adaptive_risk as adaptive_risk_module

from app.services.adaptive_risk import (
    ActiveCalibration,
    AdaptiveRiskInferenceService,
    apply_platt_calibration,
    evaluate_activation_gate,
    is_strictly_newer_candidate,
    load_verified_calibration,
)
from app.services.outcome_calibration import (
    CalibrationCandidate,
    CalibrationObservation,
    _apply_coefficients,
    _metrics,
    _serialized_metrics,
    TEMPORAL_POLICY as PERSISTED_TEMPORAL_POLICY,
    build_calibration_candidate,
    temporal_partition,
)
import numpy as np


def _built_candidate() -> CalibrationCandidate:
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    observations = tuple(
        CalibrationObservation(
            outcome_id=f"00000000-0000-0000-0000-{index + 1:012d}",
            facility_id=f"10000000-0000-0000-0000-{index + 1:012d}",
            request_id=f"20000000-0000-0000-0000-{index + 1:012d}",
            risk_assessment_id=f"30000000-0000-0000-0000-{index + 1:012d}",
            model_version_id=f"40000000-0000-0000-0000-{index + 1:012d}",
            risk_engine_version="tgnn-test@v1",
            risk_input_sha256=f"{index + 1:064x}",
            evidence_sha256=f"{index + 2:064x}",
            original_score=0.05 + (index % 10) * 0.09,
            defaulted=index % 10 >= 5,
            observed_at=(started + timedelta(minutes=index)).isoformat(timespec="microseconds"),
            provenance="CONTROLLED_DEMO",
            correction_head_id=None,
        )
        for index in range(40)
    )
    return build_calibration_candidate(observations)


def _candidate(
    *,
    sample_count: int = 40,
    positive_count: int = 20,
    status: str = "eligible_candidate",
    before_brier: float | None = None,
    after_brier: float | None = None,
    before_log_loss: float | None = None,
    after_log_loss: float | None = None,
    distinct_score_count: int = 10,
) -> CalibrationCandidate:
    baseline = _built_candidate()
    artifact = json.loads(baseline.artifact_bytes)
    before = dict(baseline.metrics_before)
    after = dict(baseline.metrics_after)
    if before_brier is not None:
        before["brier_score"] = before_brier
    if after_brier is not None:
        after["brier_score"] = after_brier
    if before_log_loss is not None:
        before["log_loss"] = before_log_loss
    if after_log_loss is not None:
        after["log_loss"] = after_log_loss
    artifact["dataset"]["sample_count"] = sample_count
    artifact["dataset"]["positive_count"] = positive_count
    artifact["dataset"]["negative_count"] = sample_count - positive_count
    artifact["dataset"]["distinct_score_count"] = distinct_score_count
    artifact["limitations"] = ["activation_gate_required"]
    if distinct_score_count < 2:
        artifact["limitations"].insert(0, "insufficient_distinct_scores")
    artifact["status"] = status
    artifact["validation"]["metrics_before"] = before
    artifact["validation"]["metrics_after"] = after
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return replace(
        baseline,
        sample_count=sample_count,
        positive_count=positive_count,
        negative_count=sample_count - positive_count,
        status=status,
        metrics_before=before,
        metrics_after=after,
        distinct_score_count=distinct_score_count,
        artifact=artifact,
        artifact_bytes=artifact_bytes,
        artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
    )


def _candidate_with_coefficients(slope: float, intercept: float) -> CalibrationCandidate:
    """Keep v4 lineage valid while exercising the metric gate branches."""
    baseline = _candidate()
    artifact = deepcopy(baseline.artifact)
    rows = tuple(
        CalibrationObservation(**row) for row in artifact["dataset"]["observations"]
    )
    _, validation = temporal_partition(rows)
    labels = np.asarray([int(row.defaulted) for row in validation])
    scores = np.asarray([row.original_score for row in validation])
    epsilon = float(artifact["configuration"]["probability_epsilon"])
    metrics_after = _serialized_metrics(
        _metrics(labels, _apply_coefficients(scores, slope, intercept, epsilon), epsilon)
    )
    artifact["coefficients"] = {"intercept": intercept, "slope": slope}
    artifact["validation"]["metrics_after"] = metrics_after
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return replace(
        baseline,
        slope=slope,
        intercept=intercept,
        metrics_after=metrics_after,
        artifact=artifact,
        artifact_bytes=artifact_bytes,
        artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
    )


def test_gate_activates_only_an_integrity_verified_non_regressing_eligible_run():
    decision = evaluate_activation_gate(
        _candidate(),
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 40,
    )

    assert decision.activate is True
    assert decision.reason == "gate_passed"
    assert decision.deployment_scope == "controlled_demo"


def test_temporal_policy_is_shared_by_artifact_validation_and_activation_gate():
    assert adaptive_risk_module.TEMPORAL_POLICY is PERSISTED_TEMPORAL_POLICY
    assert adaptive_risk_module.TEMPORAL_POLICY["metric_tolerance"] == 1e-12


@pytest.mark.parametrize(
    ("slope", "intercept", "reason"),
    (
        (0.0, 0.0, "brier_regression"),
        (28.5, -4.5, "log_loss_regression"),
        (1.0, 0.0, "no_metric_improvement"),
    ),
)
def test_metric_gate_branches_validate_holdout_evidence_before_deciding(
    slope, intercept, reason
):
    candidate = _candidate_with_coefficients(slope, intercept)

    decision = evaluate_activation_gate(
        candidate,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 40,
    )

    assert decision.activate is False
    assert decision.reason == reason


@pytest.mark.parametrize(
    ("candidate", "integrity", "provenances", "reason"),
    (
        (
            _candidate(sample_count=19, positive_count=5),
            "verified",
            ("CONTROLLED_DEMO",) * 19,
            "insufficient_samples",
        ),
        (
            _candidate(positive_count=3),
            "verified",
            ("CONTROLLED_DEMO",) * 40,
            "insufficient_positive_support",
        ),
        (
            _candidate(positive_count=37),
            "verified",
            ("CONTROLLED_DEMO",) * 40,
            "insufficient_negative_support",
        ),
        (
            _candidate(status="exploratory_candidate"),
            "verified",
            ("CONTROLLED_DEMO",) * 40,
            "training_not_eligible",
        ),
        (_candidate(), "mismatch", ("CONTROLLED_DEMO",) * 40, "artifact_unverified"),
        (
            _candidate(after_brier=0.25),
            "verified",
            ("CONTROLLED_DEMO",) * 40,
            "artifact_unverified",
        ),
        (
            _candidate(after_log_loss=0.71),
            "verified",
            ("CONTROLLED_DEMO",) * 40,
            "artifact_unverified",
        ),
    ),
)
def test_gate_rejects_each_unsafe_activation_boundary(
    candidate,
    integrity,
    provenances,
    reason,
):
    decision = evaluate_activation_gate(
        candidate,
        artifact_integrity=integrity,
        provenances=provenances,
    )

    assert decision.activate is False
    assert decision.reason == reason


def test_gate_never_labels_mixed_demo_data_as_externally_verified():
    decision = evaluate_activation_gate(
        _candidate(),
        artifact_integrity="verified",
        provenances=("EXTERNAL_VERIFIED",) * 19 + ("CONTROLLED_DEMO",),
    )

    assert decision.activate is False
    assert decision.reason == "unsupported_deployment_scope"
    assert decision.deployment_scope == "mixed"


def test_gate_rejects_identical_scores_despite_sample_and_class_support():
    decision = evaluate_activation_gate(
        _candidate(distinct_score_count=1),
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 40,
    )

    assert decision.activate is False
    assert decision.reason == "artifact_unverified"


@pytest.mark.parametrize(
    "candidate",
    (
        replace(_candidate(), negative_count=14),
        replace(_candidate(), distinct_score_count=41),
    ),
)
def test_gate_rejects_inconsistent_count_evidence(candidate):
    decision = evaluate_activation_gate(
        candidate,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 40,
    )

    assert decision.activate is False
    assert decision.reason == "count_evidence_mismatch"


def test_gate_rejects_v2_as_a_new_candidate():
    candidate = _candidate()
    candidate.artifact["artifact_schema"] = "daibm.platt-calibration.v2"
    artifact_bytes = json.dumps(
        candidate.artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    candidate = replace(
        candidate,
        artifact_bytes=artifact_bytes,
        artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
    )

    decision = evaluate_activation_gate(
        candidate,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 40,
    )

    assert decision == type(decision)(
        False,
        "legacy_artifact_not_activatable",
        "controlled_demo",
    )


def test_gate_rejects_coefficients_inconsistent_with_holdout_metrics():
    baseline = _candidate()
    changed_artifact = json.loads(baseline.artifact_bytes)
    changed_artifact["coefficients"] = {"slope": 0.5, "intercept": 0.3}
    changed_artifact["diagnostics"] = {
        "final_fit": {"metrics": {"brier_score": 0.99, "log_loss": 0.99}}
    }
    changed_bytes = json.dumps(
        changed_artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    changed = replace(
        baseline,
        slope=0.5,
        intercept=0.3,
        artifact=changed_artifact,
        artifact_bytes=changed_bytes,
        artifact_sha256=hashlib.sha256(changed_bytes).hexdigest(),
    )

    first = evaluate_activation_gate(
        baseline,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 40,
    )
    second = evaluate_activation_gate(
        changed,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 40,
    )

    assert first.activate is True
    assert second.activate is False
    assert second.reason == "artifact_unverified"


def test_verified_v2_artifact_applies_hand_checked_platt_transform(tmp_path):
    artifact = {
        "artifact_schema": "daibm.platt-calibration.v2",
        "coefficients": {"intercept": 0.0, "slope": 2.0},
        "configuration": {"probability_epsilon": 1e-6},
        "dataset": {"sha256": "1" * 64},
    }
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    path = tmp_path / f"{expected_sha256}.json"
    path.write_bytes(artifact_bytes)

    calibration = load_verified_calibration(
        path,
        expected_sha256=expected_sha256,
        run_id="00000000-0000-0000-0000-000000000001",
        deployment_scope="controlled_demo",
        expected_dataset_sha256="1" * 64,
    )

    assert calibration == ActiveCalibration(
        run_id="00000000-0000-0000-0000-000000000001",
        artifact_sha256=expected_sha256,
        slope=2.0,
        intercept=0.0,
        probability_epsilon=1e-6,
        deployment_scope="controlled_demo",
    )
    assert apply_platt_calibration(0.8, calibration) == pytest.approx(16 / 17)


def test_verified_v4_artifact_accepts_canonical_holdout_lineage(tmp_path):
    candidate = _built_candidate()
    expected_sha256 = hashlib.sha256(candidate.artifact_bytes).hexdigest()
    path = tmp_path / f"{expected_sha256}.json"
    path.write_bytes(candidate.artifact_bytes)

    calibration = load_verified_calibration(
        path,
        expected_sha256=expected_sha256,
        run_id="00000000-0000-0000-0000-000000000001",
        deployment_scope="controlled_demo",
        expected_dataset_sha256=candidate.dataset_sha256,
    )

    assert calibration.slope == candidate.slope
    assert calibration.intercept == candidate.intercept
    assert calibration.deployment_scope == "controlled_demo"


@pytest.mark.parametrize(
    "mutation",
    (
        "extra_field",
        "reordered_holdout",
        "duplicate_holdout",
        "invalid_brier",
        "boolean_metric",
        "uppercase_uuid",
        "noncanonical_float",
    ),
)
def test_v4_loader_rejects_canonical_rehashed_noncanonical_evidence(
    tmp_path,
    mutation,
):
    candidate = _built_candidate()
    artifact = json.loads(candidate.artifact_bytes)
    if mutation == "extra_field":
        artifact["dataset"]["unexpected"] = "not-governed"
    elif mutation == "reordered_holdout":
        artifact["validation"]["validation_outcome_ids"].reverse()
    elif mutation == "duplicate_holdout":
        held_out = artifact["validation"]["validation_outcome_ids"]
        held_out.append(held_out[0])
    elif mutation == "invalid_brier":
        artifact["validation"]["metrics_after"]["brier_score"] = 1.1
    elif mutation == "boolean_metric":
        artifact["validation"]["metrics_after"]["brier_score"] = True
    elif mutation == "uppercase_uuid":
        first_id = artifact["dataset"]["outcome_ids"][0]
        artifact["dataset"]["correction_heads"][first_id] = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
    else:
        artifact["configuration"]["learning_rate"] = 0.05000000000000001
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    path = tmp_path / f"{mutation}.json"
    path.write_bytes(artifact_bytes)

    with pytest.raises(ValueError, match="calibration artifact"):
        load_verified_calibration(
            path,
            expected_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
            run_id="run",
            deployment_scope="controlled_demo",
            expected_dataset_sha256=candidate.dataset_sha256,
        )


def test_gate_rejects_artifact_bytes_or_holdout_metrics_that_contradict_candidate():
    candidate = _built_candidate()
    assert candidate.artifact_sha256 == hashlib.sha256(candidate.artifact_bytes).hexdigest()
    artifact = json.loads(candidate.artifact_bytes)
    artifact["validation"]["metrics_after"]["brier_score"] = 0.99
    contradictory_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    contradictory = replace(
        candidate,
        artifact=artifact,
        artifact_bytes=contradictory_bytes,
    )

    decision = evaluate_activation_gate(
        contradictory,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 40,
    )

    assert decision.activate is False
    assert decision.reason == "artifact_unverified"


@pytest.mark.parametrize(
    "field",
    ("outcome_ids", "correction_heads", "configuration", "artifact_schema"),
)
def test_gate_rejects_persisted_manifest_evidence_that_contradicts_artifact(field):
    candidate = _built_candidate()
    artifact = json.loads(candidate.artifact_bytes)
    assert candidate.outcome_ids == tuple(artifact["dataset"]["outcome_ids"])
    changes = {
        "outcome_ids": tuple(reversed(candidate.outcome_ids)),
        "correction_heads": ((candidate.outcome_ids[0], "a" * 36),),
        "configuration": (("epochs", 1),),
        "artifact_schema": "daibm.platt-calibration.v2",
    }

    decision = evaluate_activation_gate(
        replace(candidate, **{field: changes[field]}),
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 40,
    )

    assert decision.activate is False
    assert decision.reason == "artifact_unverified"


def test_gate_rejects_missing_persisted_outcome_scope_evidence():
    candidate = _built_candidate()

    decision = evaluate_activation_gate(
        candidate,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 19,
    )

    assert decision.activate is False
    assert decision.reason == "count_evidence_mismatch"


def test_v4_loader_rejects_noncanonical_bytes_and_internal_lineage_tampering(
    tmp_path,
):
    candidate = _built_candidate()
    pretty_bytes = json.dumps(candidate.artifact, indent=2).encode("utf-8")
    pretty_path = tmp_path / "pretty.json"
    pretty_path.write_bytes(pretty_bytes)

    with pytest.raises(ValueError, match="calibration artifact"):
        load_verified_calibration(
            pretty_path,
            expected_sha256=hashlib.sha256(pretty_bytes).hexdigest(),
            run_id="run",
            deployment_scope="controlled_demo",
            expected_dataset_sha256=candidate.dataset_sha256,
        )

    tampered = json.loads(candidate.artifact_bytes)
    first_outcome = tampered["dataset"]["outcome_ids"][0]
    tampered["validation"]["validation_outcome_ids"].append(first_outcome)
    tampered_bytes = json.dumps(tampered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tampered_path = tmp_path / "lineage.json"
    tampered_path.write_bytes(tampered_bytes)

    with pytest.raises(ValueError, match="calibration artifact"):
        load_verified_calibration(
            tampered_path,
            expected_sha256=hashlib.sha256(tampered_bytes).hexdigest(),
            run_id="run",
            deployment_scope="controlled_demo",
            expected_dataset_sha256=candidate.dataset_sha256,
        )


def test_v2_artifact_is_readable_only_for_controlled_demo(tmp_path):
    artifact = {
        "artifact_schema": "daibm.platt-calibration.v2",
        "coefficients": {"intercept": 0.0, "slope": 1.0},
        "configuration": {"probability_epsilon": 1e-6},
        "dataset": {"sha256": "1" * 64},
    }
    artifact_bytes = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path = tmp_path / "legacy.json"
    path.write_bytes(artifact_bytes)

    with pytest.raises(ValueError, match="calibration artifact"):
        load_verified_calibration(
            path,
            expected_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
            run_id="run",
            deployment_scope="external_verified",
            expected_dataset_sha256="1" * 64,
        )


@pytest.mark.parametrize(
    "mutation",
    ("hash", "schema", "coefficient", "epsilon"),
)
def test_artifact_loader_rejects_corruption_or_unsafe_parameters(tmp_path, mutation):
    artifact = {
        "artifact_schema": "daibm.platt-calibration.v2",
        "coefficients": {"intercept": 0.0, "slope": 1.0},
        "configuration": {"probability_epsilon": 1e-6},
        "dataset": {"sha256": "1" * 64},
    }
    if mutation == "schema":
        artifact["artifact_schema"] = "daibm.calibration-candidate.v1"
    if mutation == "coefficient":
        artifact["coefficients"]["slope"] = "not-a-number"
    if mutation == "epsilon":
        artifact["configuration"]["probability_epsilon"] = 0.5
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    actual_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    path = tmp_path / "artifact.json"
    path.write_bytes(artifact_bytes + (b"tampered" if mutation == "hash" else b""))

    with pytest.raises(ValueError, match="calibration artifact"):
        load_verified_calibration(
            path,
            expected_sha256=actual_sha256,
            run_id="00000000-0000-0000-0000-000000000001",
            deployment_scope="controlled_demo",
            expected_dataset_sha256="1" * 64,
        )


def test_artifact_loader_rejects_dataset_lineage_mismatch(tmp_path):
    artifact = {
        "artifact_schema": "daibm.platt-calibration.v2",
        "coefficients": {"intercept": 0.0, "slope": 1.0},
        "configuration": {"probability_epsilon": 1e-6},
        "dataset": {"sha256": "1" * 64},
    }
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    path = tmp_path / "artifact.json"
    path.write_bytes(artifact_bytes)

    with pytest.raises(ValueError, match="calibration artifact"):
        load_verified_calibration(
            path,
            expected_sha256=sha256,
            run_id="run",
            deployment_scope="controlled_demo",
            expected_dataset_sha256="2" * 64,
        )


def test_artifact_loader_normalizes_malformed_json_shapes_to_validation_error(
    tmp_path,
):
    artifact_bytes = b"[]"
    path = tmp_path / "artifact.json"
    path.write_bytes(artifact_bytes)

    with pytest.raises(ValueError, match="calibration artifact"):
        load_verified_calibration(
            path,
            expected_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
            run_id="run",
            deployment_scope="controlled_demo",
            expected_dataset_sha256="1" * 64,
        )


def test_replacement_order_never_allows_an_older_dataset_to_supersede_newer():
    assert is_strictly_newer_candidate(21, active_sample_count=20) is True
    assert is_strictly_newer_candidate(20, active_sample_count=20) is False
    assert is_strictly_newer_candidate(20, active_sample_count=21) is False


@pytest.mark.parametrize("score", (-0.1, 1.1, math.nan, math.inf))
def test_platt_transform_rejects_invalid_baseline_probability(score):
    calibration = ActiveCalibration(
        run_id="run",
        artifact_sha256="1" * 64,
        slope=1.0,
        intercept=0.0,
        probability_epsilon=1e-6,
        deployment_scope="controlled_demo",
    )

    with pytest.raises(ValueError, match="baseline probability"):
        apply_platt_calibration(score, calibration)


def test_inference_service_applies_the_single_active_verified_artifact(tmp_path):
    artifact = {
        "artifact_schema": "daibm.platt-calibration.v2",
        "coefficients": {"intercept": 0.0, "slope": 2.0},
        "configuration": {"probability_epsilon": 1e-6},
        "dataset": {"sha256": "1" * 64},
    }
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    path = tmp_path / f"{sha256}.json"
    path.write_bytes(artifact_bytes)
    run = SimpleNamespace(
        calibration_run_id="00000000-0000-0000-0000-000000000001",
        artifact_locator=str(path),
        artifact_sha256=sha256,
        deployment_scope="controlled_demo",
        dataset_sha256="1" * 64,
    )

    class Repository:
        def get_active_run(self, _session, *, scope):
            return run

    result = AdaptiveRiskInferenceService(repository=Repository()).assess(
        object(),
        0.8,
        "controlled_demo",
    )

    assert result.raw_score == 0.8
    assert result.final_score == pytest.approx(16 / 17)
    assert result.calibration_run_id == str(run.calibration_run_id)
    assert result.deployment_scope == "controlled_demo"
    assert result.fallback_code is None


def test_inference_service_falls_back_to_baseline_on_active_artifact_corruption(
    tmp_path,
):
    path = tmp_path / "corrupt.json"
    path.write_text("{}", encoding="utf-8")
    run = SimpleNamespace(
        calibration_run_id="00000000-0000-0000-0000-000000000001",
        artifact_locator=str(path),
        artifact_sha256="1" * 64,
        deployment_scope="controlled_demo",
        dataset_sha256="1" * 64,
    )

    class Repository:
        def get_active_run(self, _session, *, scope):
            return run

    result = AdaptiveRiskInferenceService(repository=Repository()).assess(
        object(),
        0.8,
        "controlled_demo",
    )

    assert result.final_score == 0.8
    assert result.calibration_run_id is None
    assert result.deployment_scope is None
    assert result.fallback_code == "active_artifact_invalid"
    assert result.attempted_calibration_run_id == str(run.calibration_run_id)
