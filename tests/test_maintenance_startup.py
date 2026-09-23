"""The controlled 8010 entrypoint never performs default startup writes."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.config import ResearchSettings
from app.database import Database
from app.main import create_app
from app.maintenance import create_maintenance_app
from app.models_identity import OrganizationModel, UserModel
from app.models_research import DatasetVersionModel, ModelVersionModel
from app.services.calibration_jobs import CalibrationJobService
from app.services.identity import IdentityService
from app.services.outcomes import OutcomeService
from app.services.research_inference import (
    ResearchInferenceService,
    ResearchModelUnavailable,
)


REFERENCE_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "reference"


def _database(migrated_engine, session_factory):
    return Database(engine=migrated_engine, session_factory=session_factory)


def _settings(reference_dir=REFERENCE_DIR):
    return ResearchSettings(reference_dir=reference_dir, required=True)


def test_maintenance_entrypoint_is_explicit():
    from app.maintenance import app

    assert app.title == "DAIBM-SCF Minimal MVP"


def test_maintenance_startup_loads_existing_registry_without_seed_or_workers(
    migrated_engine, session_factory, monkeypatch
):
    registry = ResearchInferenceService(session_factory, _settings())
    registry.initialize()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("default startup mutation was invoked")

    async def forbidden_worker(*_args, **_kwargs):
        raise AssertionError("calibration worker was started")

    monkeypatch.setattr(IdentityService, "seed_demo_accounts", forbidden)
    monkeypatch.setattr(ResearchInferenceService, "initialize", forbidden)
    monkeypatch.setattr(OutcomeService, "reconcile_deployments", forbidden)
    monkeypatch.setattr(CalibrationJobService, "run", forbidden_worker)
    application = create_maintenance_app(
        _database(migrated_engine, session_factory),
        research_settings=_settings(),
    )
    with TestClient(application) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["research_core"]["status"] == "ready"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UserModel)) == 0
        assert session.scalar(select(func.count()).select_from(OrganizationModel)) == 0
        assert session.scalar(select(func.count()).select_from(DatasetVersionModel)) == 1


def test_maintenance_mode_blocks_worker_even_if_default_enabled(
    migrated_engine, session_factory, monkeypatch
):
    ResearchInferenceService(session_factory, _settings()).initialize()

    async def forbidden_worker(*_args, **_kwargs):
        raise AssertionError("calibration worker was started")

    monkeypatch.setattr(CalibrationJobService, "run", forbidden_worker)
    application = create_app(
        _database(migrated_engine, session_factory),
        research_settings=_settings(),
        maintenance_mode=True,
    )
    with TestClient(application) as client:
        assert client.get("/api/health").status_code == 200


def test_maintenance_startup_rejects_missing_reference_registry(
    migrated_engine, session_factory
):
    application = create_maintenance_app(
        _database(migrated_engine, session_factory),
        research_settings=_settings(),
    )
    with pytest.raises(ValueError, match="Reference registry is incomplete"):
        with TestClient(application):
            pass
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DatasetVersionModel)) == 0
        assert session.scalar(select(func.count()).select_from(UserModel)) == 0


def test_maintenance_startup_rejects_mismatched_reference_registry(
    migrated_engine, session_factory
):
    registry = ResearchInferenceService(session_factory, _settings())
    registry.initialize()
    with session_factory.begin() as session:
        model = session.get(ModelVersionModel, registry.identities["model_version_id"])
        assert model is not None
        model.checkpoint_sha256 = "0" * 64
    application = create_maintenance_app(
        _database(migrated_engine, session_factory),
        research_settings=_settings(),
    )
    with pytest.raises(ValueError, match="does not match verified artifact"):
        with TestClient(application):
            pass
    with session_factory() as session:
        model = session.get(ModelVersionModel, registry.identities["model_version_id"])
        assert model is not None
        assert model.checkpoint_sha256 == "0" * 64


def test_maintenance_startup_rejects_unverified_artifact(
    migrated_engine, session_factory, tmp_path
):
    application = create_maintenance_app(
        _database(migrated_engine, session_factory),
        research_settings=_settings(tmp_path / "missing"),
    )
    with pytest.raises(ResearchModelUnavailable, match="artifact is unavailable"):
        with TestClient(application):
            pass


def test_existing_registry_loader_enforces_read_only_transaction(
    session_factory, monkeypatch
):
    registry = ResearchInferenceService(session_factory, _settings())

    def attempting_write(session, _artifact):
        session.execute(text("UPDATE alembic_version SET version_num = version_num"))
        return {}

    monkeypatch.setattr(
        registry.repository,
        "load_existing_reference_registry",
        attempting_write,
    )
    with pytest.raises(DBAPIError) as caught:
        registry.initialize_existing()
    assert caught.value.orig.sqlstate == "25006"


def test_default_startup_still_seeds_identities_and_registry(
    migrated_engine, session_factory
):
    application = create_app(
        _database(migrated_engine, session_factory),
        research_settings=_settings(),
        calibration_worker_enabled=False,
    )
    with TestClient(application) as client:
        assert client.get("/api/health").status_code == 200
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UserModel)) == 5
        assert session.scalar(select(func.count()).select_from(OrganizationModel)) == 4
        assert session.scalar(select(func.count()).select_from(DatasetVersionModel)) == 1
