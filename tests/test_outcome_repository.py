from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

import pytest

from app.repositories.outcomes import OutcomeRepository


def test_governance_reads_are_empty_and_scope_is_required_on_empty_database(
    session_factory,
):
    repository = OutcomeRepository()
    outcome_id = uuid.uuid4()
    with session_factory() as session:
        assert repository.get_outcome_for_update(session, outcome_id) is None
        assert repository.get_correction_head(session, outcome_id) is None
        assert repository.list_corrections(session, outcome_id) == []
        assert repository.list_eligible_outcomes(
            session, scope="controlled_demo"
        ) == []
        assert repository.invalidate_active_runs_containing(
            session,
            outcome_id,
            scope="controlled_demo",
            now=datetime.now(timezone.utc),
        ) == []

        with pytest.raises(TypeError):
            repository.list_eligible_outcomes(session, "controlled_demo")


def test_calibration_training_lock_serializes_dataset_snapshots(session_factory):
    repository = OutcomeRepository()
    first = session_factory()
    second = session_factory()
    acquired = threading.Event()

    try:
        first.begin()
        repository.acquire_training_lock(first)

        def acquire_second() -> None:
            with second.begin():
                repository.acquire_training_lock(second)
                acquired.set()

        worker = threading.Thread(target=acquire_second)
        worker.start()
        assert acquired.wait(timeout=0.2) is False

        first.commit()
        worker.join(timeout=2)
        assert acquired.is_set()
        assert not worker.is_alive()
    finally:
        first.close()
        second.close()
