"""Phase 5.2: the stable demo dataset is complete, governed and idempotent."""

from __future__ import annotations

from sqlalchemy import func, select

from app.demo_dataset import CANDIDATE_EXTRA, HISTORY_SIZE, DemoDatasetSeeder, baseline_scores
from app.models import LedgerEventModel
from app.models_facility import FinancingFacilityModel
from app.models_identity import OrganizationModel
from app.models_model_governance import (
    RiskDecisionRecordModel,
    RiskModelVersionModel,
    TrainingDatasetSnapshotModel,
)
from app.models_risk_ops import RiskAlertModel, RiskTaskModel
from app.services.identity import IdentityService
from app.services.outcome_governance import OutcomeGovernanceService
from app.services.outcomes import OutcomeService


def test_demo_dataset_tells_every_story_through_the_services(session_factory, tmp_path):
    seeder = DemoDatasetSeeder(session_factory, artifact_root=tmp_path, demo_password="Demo123!")
    summary = seeder.seed()
    assert summary["seeded"] is True
    identity = IdentityService(session_factory, demo_password="Demo123!")
    auditor = identity.login("auditor.demo", "Demo123!").user
    risk = identity.login("risk.demo", "Demo123!").user

    with session_factory() as session:
        statuses = dict(
            session.execute(
                select(FinancingFacilityModel.facility_id, FinancingFacilityModel.status)
            ).all()
        )
        # Normal enterprise: repaid and closed; defaulted: written off and closed;
        # at-risk: overdue with a live alert in processing and a task in progress.
        assert statuses[_uuid(summary["normal"]["facility_id"])] == "closed"
        assert statuses[_uuid(summary["defaulted"]["facility_id"])] == "closed"
        assert statuses[_uuid(summary["watch"]["facility_id"])] == "overdue"
        assert len(statuses) == HISTORY_SIZE + CANDIDATE_EXTRA + 3
        alert = session.get(RiskAlertModel, _uuid(summary["watch"]["alert_id"]))
        task = session.get(RiskTaskModel, _uuid(summary["watch"]["task_id"]))
        assert alert.status == "PROCESSING" and task.status == "IN_PROGRESS"
        # History cases were handled; only the story enterprises' alerts are live.
        live = session.scalars(
            select(RiskAlertModel.facility_id).where(RiskAlertModel.status != "CLOSED")
        ).all()
        assert set(map(str, live)) <= {
            summary["watch"]["facility_id"], summary["defaulted"]["facility_id"]
        }

        # Model governance: an ACTIVE model, a newer CANDIDATE, their snapshots.
        versions = session.scalars(
            select(RiskModelVersionModel).order_by(RiskModelVersionModel.version)
        ).all()
        assert [item.status for item in versions] == ["ACTIVE", "CANDIDATE"]
        lender = session.scalar(
            select(OrganizationModel.organization_id).where(
                OrganizationModel.organization_code == "BANK-001"
            )
        )
        assert {item.organization_id for item in versions} == {lender}
        snapshots = session.scalars(select(TrainingDatasetSnapshotModel)).all()
        assert {item.snapshot_id for item in snapshots} >= {
            item.dataset_snapshot_id for item in versions
        }
        assert versions[0].metrics["holdout_after"]["brier_score"] < versions[0].metrics[
            "holdout_before"
        ]["brier_score"]
        # Decisions made after activation used the ACTIVE model.
        calibrated = session.scalars(
            select(RiskDecisionRecordModel).where(
                RiskDecisionRecordModel.model_version_id == versions[0].id
            )
        ).all()
        assert len(calibrated) >= 3
        assert session.scalar(
            select(func.count()).select_from(LedgerEventModel)
        ) > 0

    lineage = OutcomeGovernanceService(
        session_factory, outcome_service=OutcomeService(session_factory, artifact_root=tmp_path)
    ).decision_lineage(calibrated[0].decision_record_id, auditor)
    assert lineage["model_version"]["id"] == str(versions[0].id)
    assert len(lineage["training_outcome_ids"]) == HISTORY_SIZE
    assert risk.username == "risk.demo"

    # Seeding is idempotent.
    assert seeder.seed() == {"seeded": False, "reason": "already_present"}


def test_demo_dataset_needs_demo_accounts(session_factory, tmp_path, monkeypatch):
    monkeypatch.delenv("DAIBM_DEMO_PASSWORD", raising=False)
    result = DemoDatasetSeeder(session_factory, artifact_root=tmp_path, demo_password="").seed()
    assert result == {"seeded": False, "reason": "demo_accounts_disabled"}


def test_history_portfolio_has_signal_for_calibration():
    scores = baseline_scores()
    assert len(set(scores)) >= 5 and max(scores) - min(scores) >= 0.05
    assert max(scores) < 0.72  # no history case would have been rejected outright


def _uuid(value: str):
    import uuid

    return uuid.UUID(value)
