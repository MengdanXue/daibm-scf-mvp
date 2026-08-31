from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

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
    build_calibration_candidate,
)


def _candidate(
    *,
    sample_count: int = 20,
    positive_count: int = 5,
    status: str = "eligible_candidate",
    before_brier: float = 0.24,
    after_brier: float = 0.18,
    before_log_loss: float = 0.70,
    after_log_loss: float = 0.56,
) -> CalibrationCandidate:
    artifact = {
        "artifact_schema": "daibm.platt-calibration.v3",
        "coefficients": {"intercept": -0.2, "slope": 1.3},
        "configuration": {"probability_epsilon": 1e-6},
        "dataset": {"sha256": "1" * 64, "distinct_score_count": 20},
        "deployment": {"scope": "controlled_demo"},
    }
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return CalibrationCandidate(
        dataset_sha256="1" * 64,
        sample_count=sample_count,
        positive_count=positive_count,
        negative_count=sample_count - positive_count,
        status=status,
        slope=1.3,
        intercept=-0.2,
        metrics_before={
            "brier_score": before_brier,
            "log_loss": before_log_loss,
        },
        metrics_after={
            "brier_score": after_brier,
            "log_loss": after_log_loss,
        },
        artifact=artifact,
        artifact_bytes=artifact_bytes,
        distinct_score_count=20,
        fold_assignment_sha256="2" * 64,
    )


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
            original_score=0.05 + index * 0.045,
            defaulted=index >= 10,
            observed_at=(started + timedelta(minutes=index)).isoformat(
                timespec="microseconds"
            ),
            provenance="CONTROLLED_DEMO",
            correction_head_id=None,
        )
        for index in range(20)
    )
    return build_calibration_candidate(observations)


def test_gate_activates_only_an_integrity_verified_non_regressing_eligible_run():
    decision = evaluate_activation_gate(
        _candidate(),
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 20,
    )

    assert decision.activate is True
    assert decision.reason == "gate_passed"
    assert decision.deployment_scope == "controlled_demo"


@pytest.mark.parametrize(
    ("candidate", "integrity", "provenances", "reason"),
    (
        (_candidate(sample_count=19, positive_count=5), "verified", ("CONTROLLED_DEMO",) * 19, "insufficient_samples"),
        (_candidate(positive_count=4), "verified", ("CONTROLLED_DEMO",) * 20, "insufficient_positive_support"),
        (_candidate(positive_count=16), "verified", ("CONTROLLED_DEMO",) * 20, "insufficient_negative_support"),
        (_candidate(status="exploratory_candidate"), "verified", ("CONTROLLED_DEMO",) * 20, "training_not_eligible"),
        (_candidate(), "mismatch", ("CONTROLLED_DEMO",) * 20, "artifact_unverified"),
        (_candidate(after_brier=0.25), "verified", ("CONTROLLED_DEMO",) * 20, "brier_regression"),
        (_candidate(after_log_loss=0.71), "verified", ("CONTROLLED_DEMO",) * 20, "log_loss_regression"),
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
        replace(_candidate(), distinct_score_count=1),
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 20,
    )

    assert decision.activate is False
    assert decision.reason == "insufficient_distinct_scores"


@pytest.mark.parametrize(
    "candidate",
    (
        replace(_candidate(), negative_count=14),
        replace(_candidate(), distinct_score_count=21),
    ),
)
def test_gate_rejects_inconsistent_count_evidence(candidate):
    decision = evaluate_activation_gate(
        candidate,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 20,
    )

    assert decision.activate is False
    assert decision.reason == "count_evidence_mismatch"


def test_gate_rejects_v2_as_a_new_candidate():
    candidate = _candidate()
    candidate.artifact["artifact_schema"] = "daibm.platt-calibration.v2"

    decision = evaluate_activation_gate(
        candidate,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 20,
    )

    assert decision == type(decision)(
        False,
        "legacy_artifact_not_activatable",
        "controlled_demo",
    )


def test_gate_does_not_read_final_fit_coefficients_or_diagnostics():
    baseline = _candidate()
    changed_artifact = dict(baseline.artifact)
    changed_artifact["coefficients"] = {"slope": math.nan, "intercept": math.inf}
    changed_artifact["diagnostics"] = {
        "final_fit": {
            "metrics": {"brier_score": 999.0, "log_loss": 999.0}
        }
    }
    changed = replace(
        baseline,
        slope=math.nan,
        intercept=math.inf,
        artifact=changed_artifact,
    )

    first = evaluate_activation_gate(
        baseline,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 20,
    )
    second = evaluate_activation_gate(
        changed,
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 20,
    )

    assert first == second
    assert second.activate is True


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
    assert apply_platt_calibration(0.8, calibration) == pytest.approx(
        16 / 17
    )


def test_verified_v3_artifact_accepts_canonical_oof_lineage(tmp_path):
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


def test_v3_loader_rejects_noncanonical_bytes_and_internal_lineage_tampering(
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
    tampered["validation"]["fold_assignments"][first_outcome] = 4
    tampered_bytes = json.dumps(
        tampered, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
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
    artifact_bytes = json.dumps(
        artifact, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
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
        def get_active_run(self, _session):
            return run

    result = AdaptiveRiskInferenceService(repository=Repository()).assess(
        object(),
        0.8,
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
        def get_active_run(self, _session):
            return run

    result = AdaptiveRiskInferenceService(repository=Repository()).assess(
        object(),
        0.8,
    )

    assert result.final_score == 0.8
    assert result.calibration_run_id is None
    assert result.deployment_scope is None
    assert result.fallback_code == "active_artifact_invalid"
    assert result.attempted_calibration_run_id == str(run.calibration_run_id)
