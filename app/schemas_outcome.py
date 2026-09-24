from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ActualOutcomeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: UUID
    observed_at: datetime
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: Literal["CONTROLLED_DEMO", "EXTERNAL_VERIFIED"]

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


class OutcomeCorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: UUID
    action: Literal["EXCLUDE", "REINSTATE"]
    reason_code: str = Field(min_length=3, max_length=64)
    comment: str = Field(min_length=1, max_length=500)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("reason_code", mode="before")
    @classmethod
    def normalize_reason_code(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        if not value[0].isalpha() or not all(
            character.isupper() or character.isdigit() or character == "_"
            for character in value
        ):
            raise ValueError("reason_code must use uppercase governed-code syntax")
        return value

    @field_validator("comment", mode="before")
    @classmethod
    def normalize_comment(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("comment must not be blank")
        return value


class CalibrationJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    organization_id: UUID
    deployment_scope: Literal["controlled_demo", "external_verified"]
    trigger_type: Literal[
        "outcome_submitted", "correction_exclude", "correction_reinstate", "outcome_reviewed"
    ]
    trigger_outcome_id: UUID | None
    trigger_correction_id: UUID | None
    status: Literal["queued", "running", "completed", "failed"]
    attempt_count: int = Field(ge=0, le=3)
    failure_code: str | None
    result_run_id: UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ActualOutcomeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_id: UUID
    facility_id: UUID
    request_id: UUID
    risk_assessment_id: UUID
    model_version_id: UUID | None
    risk_engine_version: str
    defaulted: bool
    days_past_due: int = Field(ge=0)
    loss_amount: str = Field(pattern=r"^\d+\.\d{2}$")
    observed_at: datetime
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: Literal["CONTROLLED_DEMO", "EXTERNAL_VERIFIED"]
    original_risk_score: float = Field(ge=0, le=1)
    risk_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime
    effective_training_eligible: bool
    revision: int = Field(ge=1)
    supersedes_outcome_id: UUID | None
    correction_reason_code: str | None
    review_status: (
        Literal["CREATED", "REVIEWING", "ELIGIBLE", "TRAINING_USED", "REJECTED"] | None
    )
    review_reason: str | None


class OutcomeCorrectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correction_id: UUID
    outcome_id: UUID
    action: Literal["EXCLUDE", "REINSTATE"]
    reason_code: str
    comment: str
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    auditor_user_id: UUID
    idempotency_key: UUID
    recorded_at: datetime
    effective_training_eligible: bool


class OutcomeSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: ActualOutcomeResponse
    calibration_job: CalibrationJobResponse


class CorrectionSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correction: OutcomeCorrectionResponse
    effective_training_eligible: bool
    invalidated_run_ids: list[UUID]
    calibration_job: CalibrationJobResponse


class ActualOutcomePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: UUID
    defaulted: bool
    days_past_due: int = Field(ge=0)
    loss_amount: str = Field(pattern=r"^\d+\.\d{2}$")
    closure_reason: Literal["repaid", "settled_after_default", "written_off"]
    closed_at: datetime
    expected_provenance: Literal["CONTROLLED_DEMO", "EXTERNAL_VERIFIED"]
    deployment_scope: Literal["controlled_demo", "external_verified"]


class CalibrationRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_active_run_id: UUID
    deployment_scope: Literal["controlled_demo", "external_verified"]


__all__ = [
    "ActualOutcomeCreate",
    "ActualOutcomePreviewResponse",
    "ActualOutcomeResponse",
    "CalibrationJobResponse",
    "CalibrationRollbackRequest",
    "CorrectionSubmissionResponse",
    "OutcomeCorrectionCreate",
    "OutcomeCorrectionResponse",
    "OutcomeSubmissionResponse",
]


class OutcomeSupersedeCreate(BaseModel):
    """Correct an immutable outcome by appending a superseding revision."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: UUID
    defaulted: bool
    days_past_due: int = Field(ge=0, le=36500)
    loss_amount: str = Field(pattern=r"^\d{1,12}\.\d{2}$")
    observed_at: datetime
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    comment: str = Field(min_length=1, max_length=500)

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    @field_validator("comment", mode="before")
    @classmethod
    def normalize_comment(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("comment must not be blank")
        return value


class OutcomeReviewCreate(BaseModel):
    """A reviewer's decision on an outcome's training eligibility."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "REJECT"]
    reason_code: (
        Literal[
            "QUALITY_ANOMALY",
            "DATA_QUALITY_INSUFFICIENT",
            "BUSINESS_INCONSISTENT",
            "BUSINESS_EXCEPTION",
            "SCOPE_MISMATCH",
        ]
        | None
    ) = None
    comment: str = Field(min_length=4, max_length=500)

    @field_validator("comment", mode="before")
    @classmethod
    def normalize_comment(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def reason_matches_decision(self) -> "OutcomeReviewCreate":
        if self.decision == "REJECT" and self.reason_code is None:
            raise ValueError("a rejection needs a reason_code")
        if self.decision == "APPROVE" and self.reason_code is not None:
            raise ValueError("an approval takes no reason_code")
        return self
