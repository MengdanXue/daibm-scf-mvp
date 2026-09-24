"""Model version lifecycle: the registry of record for deployable risk models."""

from __future__ import annotations

from enum import StrEnum


class ModelVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    EVALUATING = "EVALUATING"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


S = ModelVersionStatus

# Frozen in migration 20260926_0017; tests pin the equality.
ALLOWED_TRANSITIONS: frozenset[tuple[ModelVersionStatus, ModelVersionStatus]] = frozenset(
    {
        (S.DRAFT, S.EVALUATING),
        (S.DRAFT, S.REJECTED),
        (S.DRAFT, S.RETIRED),
        (S.EVALUATING, S.CANDIDATE),
        (S.EVALUATING, S.REJECTED),
        (S.EVALUATING, S.RETIRED),
        (S.CANDIDATE, S.ACTIVE),
        (S.CANDIDATE, S.REJECTED),
        (S.CANDIDATE, S.RETIRED),
        (S.ACTIVE, S.ROLLED_BACK),
        (S.ACTIVE, S.RETIRED),
        (S.ROLLED_BACK, S.RETIRED),
        # Rollback restores the immediately previous ACTIVE version.
        (S.RETIRED, S.ACTIVE),
        (S.REJECTED, S.RETIRED),
    }
)


def model_id_for(scope: str) -> str:
    return f"calibration:{scope}"
