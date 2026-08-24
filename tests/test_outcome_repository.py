from __future__ import annotations

import threading

from app.repositories.outcomes import OutcomeRepository


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
