"""Risk-intelligence governance vocabulary: model registry, scope, outcome review.

The registry status of a calibration model is a projection of the governed
columns of ``calibration_runs``. PostgreSQL computes the same projection in
``calibration_registry_status`` (revision 20260925_0016) to write the append-only
``model_registry_events`` log; ``tests/test_model_governance.py`` pins that the
two agree for every reachable state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RegistryStatus(StrEnum):
    DRAFT = "DRAFT"
    EVALUATING = "EVALUATING"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


REGISTRY_STATUS_DESCRIPTIONS = {
    RegistryStatus.DRAFT: "Trained on too little data for independent validation; never deployable.",
    RegistryStatus.EVALUATING: "Artifact published; awaiting independent chronological-holdout validation.",
    RegistryStatus.CANDIDATE: "Passed independent validation; awaiting the promotion decision.",
    RegistryStatus.ACTIVE: "Serves assessments in its declared scope; at most one per scope.",
    RegistryStatus.ROLLED_BACK: "Deactivated by a governed rollback to its predecessor.",
    RegistryStatus.RETIRED: "Superseded, invalidated by an outcome correction, or retired by an auditor.",
    RegistryStatus.REJECTED: "Training, validation, integrity or promotion gate failed.",
}


def calibration_registry_status(
    *,
    status: str,
    deployment_status: str,
    rolled_back_at: object | None,
    retired_at: object | None,
) -> RegistryStatus:
    if deployment_status == "active":
        return RegistryStatus.ACTIVE
    if retired_at is not None:
        return RegistryStatus.RETIRED
    if rolled_back_at is not None:
        return RegistryStatus.ROLLED_BACK
    if deployment_status in {"superseded", "invalidated"}:
        return RegistryStatus.RETIRED
    if status == "failed" or deployment_status in {"rejected", "activation_failed"}:
        return RegistryStatus.REJECTED
    if status == "exploratory_candidate":
        return RegistryStatus.DRAFT
    return RegistryStatus.EVALUATING


def research_registry_status(lifecycle_status: str, deployment_slot: str | None) -> RegistryStatus:
    if deployment_slot is not None:
        return RegistryStatus.ACTIVE
    return {
        "candidate": RegistryStatus.DRAFT,
        "evaluated": RegistryStatus.CANDIDATE,
        "promoted": RegistryStatus.RETIRED,
        "retired": RegistryStatus.RETIRED,
        "failed": RegistryStatus.REJECTED,
    }.get(lifecycle_status, RegistryStatus.DRAFT)


# --- Scope compatibility --------------------------------------------------------


class Scope(StrEnum):
    CONTROLLED_DEMO = "controlled_demo"
    EXTERNAL_VERIFIED = "external_verified"
    MIXED = "mixed"


class ScopeResult(StrEnum):
    ALLOW = "allow"
    ALLOW_WITH_WARNING = "allow_with_warning"
    REJECT = "reject"


@dataclass(frozen=True)
class ScopeDecision:
    model_scope: str
    request_scope: str
    result: ScopeResult
    reason: str

    @property
    def permits_model(self) -> bool:
        return self.result != ScopeResult.REJECT


_MATRIX: dict[tuple[Scope, Scope], tuple[ScopeResult, str]] = {
    (Scope.CONTROLLED_DEMO, Scope.CONTROLLED_DEMO): (ScopeResult.ALLOW, "scope_match"),
    (Scope.EXTERNAL_VERIFIED, Scope.EXTERNAL_VERIFIED): (ScopeResult.ALLOW, "scope_match"),
    (Scope.MIXED, Scope.MIXED): (
        ScopeResult.ALLOW_WITH_WARNING,
        "mixed_provenance_model_on_mixed_request",
    ),
    (Scope.CONTROLLED_DEMO, Scope.EXTERNAL_VERIFIED): (
        ScopeResult.REJECT,
        "demo_model_cannot_serve_verified_request",
    ),
    (Scope.EXTERNAL_VERIFIED, Scope.CONTROLLED_DEMO): (
        ScopeResult.REJECT,
        "verified_model_not_approved_for_demo_request",
    ),
    (Scope.MIXED, Scope.CONTROLLED_DEMO): (
        ScopeResult.REJECT,
        "mixed_model_cannot_serve_single_scope_request",
    ),
    (Scope.MIXED, Scope.EXTERNAL_VERIFIED): (
        ScopeResult.REJECT,
        "mixed_model_cannot_serve_single_scope_request",
    ),
    (Scope.CONTROLLED_DEMO, Scope.MIXED): (
        ScopeResult.REJECT,
        "single_scope_model_cannot_serve_mixed_request",
    ),
    (Scope.EXTERNAL_VERIFIED, Scope.MIXED): (
        ScopeResult.REJECT,
        "single_scope_model_cannot_serve_mixed_request",
    ),
}


def check_scope(model_scope: str, request_scope: str) -> ScopeDecision:
    """Decide whether a model declared for ``model_scope`` may serve a request."""

    try:
        result, reason = _MATRIX[(Scope(model_scope), Scope(request_scope))]
    except ValueError:
        result, reason = ScopeResult.REJECT, "undeclared_scope"
    return ScopeDecision(model_scope, request_scope, result, reason)


def scope_matrix() -> list[dict[str, str]]:
    return [
        {
            "model_scope": model.value,
            "request_scope": request.value,
            "result": result.value,
            "reason": reason,
        }
        for (model, request), (result, reason) in _MATRIX.items()
    ]


# --- Outcome review ---------------------------------------------------------------


class OutcomeReviewStatus(StrEnum):
    CREATED = "CREATED"
    REVIEWING = "REVIEWING"
    ELIGIBLE = "ELIGIBLE"
    TRAINING_USED = "TRAINING_USED"
    REJECTED = "REJECTED"


TRAINING_ELIGIBLE_STATUSES = (
    OutcomeReviewStatus.ELIGIBLE.value,
    OutcomeReviewStatus.TRAINING_USED.value,
)

OUTCOME_REJECTION_REASONS = {
    "SCOPE_MISMATCH": "Outcome provenance does not match the assessment scope.",
    "DATA_QUALITY_INSUFFICIENT": "Score, days past due or observation time cannot support training.",
    "BUSINESS_INCONSISTENT": "Loss without default, or loss above principal.",
    "BUSINESS_EXCEPTION": "Facility or application lineage is missing or not closed.",
    "MANUAL_CORRECTION": "An auditor excluded the outcome from training.",
    "SUPERSEDED_BY_CORRECTION": "A superseding correction replaced this outcome.",
}
