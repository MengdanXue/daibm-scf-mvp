from dataclasses import replace
from types import SimpleNamespace

import uuid

import pytest

from app.services import outcome_calibration as module
from app.services.adaptive_risk import AdaptiveRiskInferenceService
from app.repositories.outcomes import OutcomeRepository
from tests.test_outcome_calibration import _observation


def observations(count=40):
    return tuple(_observation(i, 0.1 + (i % 10) * 0.07, i % 3 == 0) for i in range(count))


def test_temporal_fit_never_sees_validation_identities(monkeypatch):
    rows = observations()
    fitted = []
    original = module._fit_platt

    def spy(*args, training_outcome_ids=(), **kwargs):
        fitted.append(training_outcome_ids)
        return original(*args, training_outcome_ids=training_outcome_ids, **kwargs)

    monkeypatch.setattr(module, "_fit_platt", spy)
    candidate = module.build_calibration_candidate(tuple(reversed(rows)))
    validation = candidate.artifact["validation"]
    assert validation["method"] == "chronological_holdout_70_30"
    assert fitted == [tuple(row.outcome_id for row in rows[:28])]
    assert validation["training_outcome_ids"] == list(fitted[0])
    assert validation["validation_outcome_ids"] == [row.outcome_id for row in rows[28:]]
    assert validation["training_summary"]["sample_count"] == 28
    assert validation["validation_summary"]["sample_count"] == 12
    assert validation["training_summary"]["distinct_score_count"] == 10
    assert validation["validation_summary"]["positive_count"] == 4
    assert candidate.metrics_before["brier_score"] == pytest.approx(
        sum((row.original_score - row.defaulted) ** 2 for row in rows[28:]) / 12
    )


def test_timestamp_groups_choose_earlier_equidistant_boundary():
    rows = list(observations())
    rows[27] = replace(rows[27], observed_at=rows[26].observed_at)
    rows[28] = replace(rows[28], observed_at=rows[26].observed_at)
    # Allowed boundaries 26 and 29: nearest to 28 is 29.
    result = module.build_calibration_candidate(tuple(rows))
    assert len(result.artifact["validation"]["training_outcome_ids"]) == 29


def test_boundary_tie_break_uses_earlier_timestamp_without_class_search():
    rows = list(observations())
    rows[28] = replace(rows[28], observed_at=rows[27].observed_at)
    result = module.build_calibration_candidate(tuple(rows))
    assert len(result.artifact["validation"]["training_outcome_ids"]) == 27


def test_minimum_partition_sizes_and_exact_decimal_score_span_are_eligible():
    scores = (0.10, 0.11, 0.12, 0.13, 0.15)
    rows = tuple(replace(row, original_score=scores[i % 5]) for i, row in enumerate(observations(30)))
    candidate = module.build_calibration_candidate(rows)
    assert candidate.artifact["validation"]["training_summary"]["sample_count"] == 20
    assert candidate.artifact["validation"]["validation_summary"]["sample_count"] == 10
    assert candidate.artifact["validation"]["training_summary"]["score_span"] == 0.05


def test_v3_bytes_remain_loadable_only_at_explicit_scope_not_promotable(tmp_path):
    import hashlib
    import json
    from pathlib import Path
    from app.services.adaptive_risk import load_verified_calibration, evaluate_activation_gate

    payload = (Path(__file__).parent / "fixtures/calibration-v3-95841d6.json").read_bytes().rstrip()
    artifact = json.loads(payload)
    path = tmp_path / "historical.json"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    assert digest == "f3c41b086c7039c79e4f89123dbec6eac8d7c5df3c2e2392a10597fafba2a529"
    loaded = load_verified_calibration(
        path,
        expected_sha256=digest,
        run_id="legacy",
        deployment_scope="controlled_demo",
        expected_dataset_sha256=artifact["dataset"]["sha256"],
    )
    assert loaded.slope == artifact["coefficients"]["slope"]
    with pytest.raises(ValueError):
        load_verified_calibration(
            path,
            expected_sha256=digest,
            run_id="legacy",
            deployment_scope="external_verified",
            expected_dataset_sha256=artifact["dataset"]["sha256"],
        )
    # Historical count must not mask the explicit legacy-schema prohibition.
    candidate = replace(
        module.build_calibration_candidate(observations()),
        artifact=artifact,
        artifact_bytes=payload,
        artifact_sha256=digest,
    )
    decision = evaluate_activation_gate(
        candidate, artifact_integrity="verified", provenances=("CONTROLLED_DEMO",) * 40
    )
    assert decision.reason == "legacy_artifact_not_activatable"
    assert path.read_bytes() == payload


def test_gate_rejects_identity_transform_with_no_holdout_improvement():
    import hashlib
    import json
    from app.canonical import canonical_bytes
    from app.services.adaptive_risk import evaluate_activation_gate
    import numpy as np

    baseline = module.build_calibration_candidate(observations())
    artifact = json.loads(baseline.artifact_bytes)
    artifact["coefficients"] = {"slope": 1.0, "intercept": 0.0}
    rows = observations()[28:]
    values = np.asarray([row.original_score for row in rows])
    after = module._serialized_metrics(
        module._metrics(
            np.asarray([int(row.defaulted) for row in rows]),
            module._apply_coefficients(values, 1.0, 0.0, 1e-6),
            1e-6,
        )
    )
    artifact["validation"]["metrics_after"] = after
    payload = canonical_bytes(artifact)
    candidate = replace(
        baseline,
        slope=1.0,
        intercept=0.0,
        metrics_after=after,
        artifact=artifact,
        artifact_bytes=payload,
        artifact_sha256=hashlib.sha256(payload).hexdigest(),
    )
    result = evaluate_activation_gate(
        candidate, artifact_integrity="verified", provenances=("CONTROLLED_DEMO",) * 40
    )
    assert not result.activate
    assert result.reason == "no_metric_improvement"


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("small", "temporal_insufficient_partition_sizes"),
        ("ties", "temporal_insufficient_partition_sizes"),
        ("distinct", "temporal_insufficient_distinct_scores"),
        ("span", "temporal_insufficient_score_span"),
        ("train_class", "temporal_insufficient_training_class_support"),
        ("validation_class", "temporal_insufficient_validation_class_support"),
    ],
)
def test_rejects_unsupported_partitions_without_searching_labels(kind, reason):
    rows = observations(29 if kind == "small" else 40)
    if kind == "ties":
        rows = tuple(replace(row, observed_at=rows[0].observed_at) for row in rows)
    if kind == "distinct":
        rows = tuple(replace(row, original_score=0.5) for row in rows)
    if kind == "span":
        rows = tuple(replace(row, original_score=0.5 + i / 10000) for i, row in enumerate(rows))
    if kind in {"train_class", "validation_class"}:
        rows = tuple(
            replace(row, defaulted=False) if (i < 28) == (kind == "train_class") else row
            for i, row in enumerate(rows)
        )
    with pytest.raises(ValueError, match=reason):
        module.build_calibration_candidate(rows)


def test_scope_is_required_at_inference_and_repository_boundaries():
    with pytest.raises(TypeError):
        AdaptiveRiskInferenceService().assess(None, 0.4)
    with pytest.raises(TypeError):
        OutcomeRepository().get_active_run(None)


def test_scope_mismatch_keeps_attempted_run_lineage():
    class Registry:
        def get_active_version(self, session, *, scope, organization_id=None):
            return (
                None
                if scope == "external_verified"
                else SimpleNamespace(
                    id="demo-version", calibration_run_id="demo-run", scope="controlled_demo"
                )
            )

    result = AdaptiveRiskInferenceService(registry=Registry()).assess(
        None, 0.4, "external_verified", uuid.uuid4()
    )
    assert result.final_score == 0.4
    assert result.calibration_run_id is None
    assert result.fallback_code == "calibration_scope_mismatch"
    assert result.attempted_calibration_run_id == "demo-run"
    assert result.attempted_model_version_id == "demo-version"


def test_rollback_body_requires_explicit_scope():
    from pydantic import ValidationError
    from app.schemas_outcome import CalibrationRollbackRequest

    with pytest.raises(ValidationError):
        CalibrationRollbackRequest(expected_active_run_id="00000000-0000-0000-0000-000000000001")


def test_legacy_financing_service_persists_scope_and_raw_calibration_lineage(session_factory):
    from app.service import FinancingService, DEMO_SCENARIOS
    from app.models import FinancingRequestModel, LedgerEventModel
    from sqlalchemy import select
    import uuid

    result = FinancingService(session_factory).create_request(DEMO_SCENARIOS[0])
    with session_factory() as session:
        row = session.get(FinancingRequestModel, uuid.UUID(result["request_id"]))
        assert row.assessment_scope == "controlled_demo"
        assert row.raw_risk_score == row.risk_score
        event = session.scalar(
            select(LedgerEventModel).where(
                LedgerEventModel.entity_id == row.request_id,
                LedgerEventModel.event_type == "RISK_ASSESSMENT",
            )
        )
        assert event.payload["assessment_scope"] == "controlled_demo"
