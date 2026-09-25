"""Outcome data governance: review lifecycle, training eligibility, dataset snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from app.domain.governance import OutcomeReviewStatus

R = OutcomeReviewStatus

# Frozen in migration 20260927_0018; tests pin the equality.
REVIEW_TRANSITIONS: frozenset[tuple[OutcomeReviewStatus | None, OutcomeReviewStatus]] = frozenset(
    {
        (None, R.CREATED),
        (R.CREATED, R.REVIEWING),
        (R.CREATED, R.REJECTED),
        (R.REVIEWING, R.ELIGIBLE),
        (R.REVIEWING, R.REJECTED),
        (R.ELIGIBLE, R.TRAINING_USED),
        (R.ELIGIBLE, R.REJECTED),
        (R.ELIGIBLE, R.REVIEWING),
        (R.TRAINING_USED, R.TRAINING_USED),
        (R.TRAINING_USED, R.REJECTED),
        (R.TRAINING_USED, R.REVIEWING),
        (R.REJECTED, R.REVIEWING),
        (R.REJECTED, R.REJECTED),
    }
)

# Reasons a reviewer may give when rejecting an outcome by hand.
MANUAL_REJECTION_REASONS = frozenset(
    {
        "QUALITY_ANOMALY",
        "DATA_QUALITY_INSUFFICIENT",
        "BUSINESS_INCONSISTENT",
        "BUSINESS_EXCEPTION",
        "SCOPE_MISMATCH",
    }
)

# Why an outcome of the scope is left out of a training dataset snapshot.
EXCLUSION_REASONS = {
    "SUPERSEDED_BY_CORRECTION": "被更正修订替代，只使用有效修订",
    "EXCLUDED_BY_CORRECTION": "审计员更正排除",
    "SCOPE_MISMATCH": "结果来源与申请 scope 不一致",
    "BUSINESS_EXCEPTION": "生命周期未结束或业务链路缺失",
    "DATA_QUALITY_INSUFFICIENT": "数据不完整或不合理",
    "BUSINESS_INCONSISTENT": "损失与违约、本金不一致",
    "QUALITY_ANOMALY": "人工审核判定质量异常",
    "MANUAL_CORRECTION": "人工更正排除",
    "REVIEW_PENDING": "尚未通过审核",
}

ELIGIBILITY_POLICY = "outcome-eligibility-v1"

# Explicit, user-facing reasons a calibration attempt produced no candidate or
# no promotion. Raw stored codes stay unchanged; this is the stable vocabulary.
_FAILURE_REASONS = {
    "temporal_insufficient_partition_sizes": "insufficient_samples",
    "temporal_insufficient_training_class_support": "insufficient_class_support",
    "temporal_insufficient_validation_class_support": "insufficient_class_support",
    "temporal_insufficient_distinct_scores": "no_risk_variance",
    "temporal_insufficient_score_span": "no_risk_variance",
    "deployment_scope_mismatch": "scope_mismatch",
    "validation_not_independent": "validation_not_independent",
    "stale_candidate": "not_newer_than_active",
    "dataset_snapshot_changed": "dataset_changed_before_deployment",
    "artifact_unverified": "artifact_unverified",
    "job_failed": "training_job_failed",
    # Activation gate reasons.
    "insufficient_samples": "insufficient_samples",
    "insufficient_distinct_scores": "no_risk_variance",
    "insufficient_positive_support": "insufficient_class_support",
    "insufficient_negative_support": "insufficient_class_support",
    "unsupported_deployment_scope": "scope_mismatch",
    "brier_regression": "no_holdout_improvement",
    "log_loss_regression": "no_holdout_improvement",
    "no_metric_improvement": "no_holdout_improvement",
    "metrics_nonfinite": "invalid_training_data",
    "training_not_eligible": "exploratory_dataset_not_deployable",
    "calibration_data_rejected": "invalid_training_data",
}


def training_failure_reason(
    *,
    failure_code: str | None,
    activation_reason: str | None,
) -> str | None:
    """Stable reason a run yielded no usable model; None when it did."""

    if activation_reason in _FAILURE_REASONS:
        return _FAILURE_REASONS[activation_reason]
    if failure_code is not None:
        return _FAILURE_REASONS.get(failure_code, failure_code)
    return None


def snapshot_manifest(
    *,
    scope: str,
    policy: str,
    included: Iterable[tuple[str, str | None]],
    excluded: Iterable[tuple[str, str]],
) -> bytes:
    """Canonical bytes of a snapshot; its SHA-256 is the dataset hash."""

    return json.dumps(
        {
            "scope": scope,
            "policy": policy,
            "included": sorted([outcome_id, head] for outcome_id, head in included),
            "excluded": sorted([outcome_id, reason] for outcome_id, reason in excluded),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def snapshot_hash(**kwargs) -> str:
    return hashlib.sha256(snapshot_manifest(**kwargs)).hexdigest()


__all__ = [
    "ELIGIBILITY_POLICY",
    "EXCLUSION_REASONS",
    "MANUAL_REJECTION_REASONS",
    "REVIEW_TRANSITIONS",
    "snapshot_hash",
    "snapshot_manifest",
    "training_failure_reason",
]
