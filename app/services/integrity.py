from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.domain.research import IntegrityIncident
from app.ledger import canonical_json, canonical_timestamp, calculate_hash
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_research import IntegrityIncidentModel
from app.repositories.ledger import LedgerRepository
from app.repositories.research import ResearchRepository
from app.risk import assess, band_for_score
from app.schemas import FinancingRequestCreate


TRUSTED_RECOVERY_SOURCE = "financing_request_projection_v0.1"
RECOVERY_METHOD = "restore_registered_synthetic_event_payload"

TrustedPayloadResolver = Callable[
    [Session, LedgerEventModel],
    dict[str, Any],
]


class IntegrityError(RuntimeError):
    """Base class for safe integrity workflow failures."""


class NoIntegrityViolation(IntegrityError):
    """Raised when incident detection is requested for a valid chain."""


class IntegrityIncidentNotFound(IntegrityError):
    """Raised when a requested incident does not exist."""


class IntegrityIncidentAlreadyRecovered(IntegrityError):
    """Raised when recovery is repeated for a completed incident."""


class RecoverySourceMismatch(IntegrityError):
    """Raised when trusted application state cannot restore the sealed hash."""


class IntegrityService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        ledger_repository: LedgerRepository | None = None,
        research_repository: ResearchRepository | None = None,
        trusted_payload_resolver: TrustedPayloadResolver | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.ledger_repository = ledger_repository or LedgerRepository()
        self.research_repository = research_repository or ResearchRepository()
        self.trusted_payload_resolver = (
            trusted_payload_resolver or self._resolve_financing_risk_payload
        )

    def detect(self) -> IntegrityIncident:
        with self.session_factory.begin() as session:
            self.ledger_repository.acquire_global_lock(session)
            verification = self.ledger_repository.verify(session)
            if verification["valid"]:
                raise NoIntegrityViolation("Audit ledger is valid")
            if verification["reason"] != "event_hash_mismatch":
                raise RecoverySourceMismatch(
                    "Only the registered payload-tamper scenario is recoverable"
                )
            event_id = int(verification["invalid_event_id"])
            existing = self.research_repository.find_unresolved_incident(
                session,
                event_id,
            )
            if existing is not None:
                return self._to_domain(existing)
            event = self.ledger_repository.get_event(
                session,
                event_id,
                for_update=True,
            )
            if event is None:
                raise RecoverySourceMismatch("Affected ledger event is missing")
            model = self.research_repository.add_integrity_incident(
                session,
                integrity_incident_id=uuid.uuid4(),
                affected_ledger_event_id=event.id,
                detected_at=datetime.now(timezone.utc),
                expected_hash=event.event_hash,
                actual_hash=self.ledger_repository.recompute_event_hash(event),
                corrupted_payload=dict(event.payload),
                trusted_recovery_source=TRUSTED_RECOVERY_SOURCE,
            )
            return self._to_domain(model)

    def recover(
        self,
        integrity_incident_id: uuid.UUID,
        operator: str,
    ) -> dict[str, Any]:
        normalized_operator = operator.strip()
        if not normalized_operator:
            raise ValueError("operator must not be blank")

        with self.session_factory.begin() as session:
            self.ledger_repository.acquire_global_lock(session)
            incident = self.research_repository.get_integrity_incident(
                session,
                integrity_incident_id,
                for_update=True,
            )
            if incident is None:
                raise IntegrityIncidentNotFound(str(integrity_incident_id))
            if incident.recovery_status != "unresolved":
                raise IntegrityIncidentAlreadyRecovered(
                    str(integrity_incident_id)
                )
            event = self.ledger_repository.get_event(
                session,
                incident.affected_ledger_event_id,
                for_update=True,
            )
            if event is None:
                raise RecoverySourceMismatch("Affected ledger event is missing")
            if (
                self.ledger_repository.recompute_event_hash(event)
                != incident.actual_hash
                or event.event_hash != incident.expected_hash
                or dict(event.payload) != dict(incident.corrupted_payload)
            ):
                raise RecoverySourceMismatch(
                    "Ledger event changed after incident detection"
                )

            trusted_payload = self.trusted_payload_resolver(session, event)
            trusted_hash = calculate_hash(
                event.previous_hash,
                canonical_timestamp(event.created_at),
                event.event_type,
                str(event.entity_id),
                canonical_json(trusted_payload),
            )
            if trusted_hash != incident.expected_hash:
                raise RecoverySourceMismatch(
                    "Trusted recovery source does not match the sealed hash"
                )

            event.payload = trusted_payload
            session.flush()
            base_verification = self.ledger_repository.verify(session)
            if not base_verification["valid"]:
                raise RecoverySourceMismatch(
                    "Restored base chain does not verify"
                )

            recovered_at = datetime.now(timezone.utc)
            appended = self.ledger_repository.append_many(
                session,
                incident.integrity_incident_id,
                [
                    (
                        "INTEGRITY_VIOLATION_DETECTED",
                        {
                            "integrity_incident_id": str(
                                incident.integrity_incident_id
                            ),
                            "affected_ledger_event_id": event.id,
                            "detected_at": canonical_timestamp(
                                incident.detected_at
                            ),
                            "expected_hash": incident.expected_hash,
                            "actual_hash": incident.actual_hash,
                            "trusted_recovery_source": (
                                incident.trusted_recovery_source
                            ),
                            "simulated": True,
                        },
                    ),
                    (
                        "LEDGER_RECOVERY_COMPLETED",
                        {
                            "integrity_incident_id": str(
                                incident.integrity_incident_id
                            ),
                            "affected_ledger_event_id": event.id,
                            "recovery_method": RECOVERY_METHOD,
                            "operator": normalized_operator,
                            "recovered_at": canonical_timestamp(recovered_at),
                            "simulated": True,
                        },
                    ),
                ],
                stream_id="global",
            )
            incident.recovery_status = "recovered"
            incident.recovery_method = RECOVERY_METHOD
            incident.operator = normalized_operator
            incident.recovered_at = recovered_at
            session.flush()
            incident_payload = self._model_to_dict(incident)
            appended_event_ids = [event.id for event in appended]

        with self.session_factory() as session:
            verification = self.ledger_repository.verify(session)
        return {
            "incident": incident_payload,
            "appended_event_ids": appended_event_ids,
            "verification": verification,
        }

    @staticmethod
    def _resolve_financing_risk_payload(
        session: Session,
        event: LedgerEventModel,
    ) -> dict[str, Any]:
        if event.event_type != "RISK_ASSESSMENT":
            raise RecoverySourceMismatch("Trusted source supports only the demo risk event")
        base_fields = {"score", "band", "top_contributions", "model"}
        lineage_fields = {
            "assessment_scope",
            "raw_score",
            "calibration_run_id",
            "calibration_fallback_code",
            "attempted_calibration_run_id",
        }
        fields = set(event.payload)
        historical = fields == base_fields | {"demo_tampered"}
        if (
            not historical
            and fields != base_fields | lineage_fields | {"demo_tampered"}
            or event.payload.get("demo_tampered") is not True
            or event.payload.get("score") != 0.9999
        ):
            raise RecoverySourceMismatch("Payload is not the registered synthetic tamper shape")
        request = session.get(FinancingRequestModel, event.entity_id)
        if request is None:
            raise RecoverySourceMismatch("Trusted financing request is missing")
        try:
            result = assess(FinancingRequestCreate.model_validate(request.features))
        except (TypeError, ValueError) as error:
            raise RecoverySourceMismatch("Trusted financing features are invalid") from error
        expected_raw_score = request.risk_score if historical else request.raw_risk_score
        if (
            result.score != expected_raw_score
            or result.contributions != request.explanations
            or request.risk_score is None
            or not math.isfinite(request.risk_score)
            or not 0 <= request.risk_score <= 1
        ):
            raise RecoverySourceMismatch(
                "Trusted financing projection does not reproduce stored state"
            )
        payload: dict[str, Any] = {
            "score": request.risk_score,
            "band": band_for_score(request.risk_score),
            "top_contributions": result.contributions[:3],
            "model": "transparent_logistic_baseline_v0.1",
        }
        if not historical:
            # Use assessment-time projection state, never today's active calibration.
            # Attempted-run identity exists only in the sealed event. It remains an
            # untrusted candidate until recover() verifies the original expected hash.
            attempted = event.payload["attempted_calibration_run_id"]
            fallback = request.calibration_fallback_code
            if attempted is not None:
                try:
                    if not isinstance(attempted, str) or str(uuid.UUID(attempted)) != attempted:
                        raise ValueError("noncanonical attempted run")
                except ValueError as error:
                    raise RecoverySourceMismatch(
                        "Attempted calibration lineage is invalid"
                    ) from error
            if (
                request.assessment_scope not in {"controlled_demo", "external_verified"}
                or (
                    request.calibration_run_id is not None
                    and (fallback is not None or attempted is not None)
                )
                or (request.calibration_run_id is None and request.risk_score != result.score)
                or (fallback is None and attempted is not None)
                or (
                    fallback is not None
                    and (
                        fallback not in {"active_artifact_invalid", "calibration_scope_mismatch"}
                        or attempted is None
                    )
                )
            ):
                raise RecoverySourceMismatch("Financing calibration projection is inconsistent")
            payload.update(
                {
                    "assessment_scope": request.assessment_scope,
                    "raw_score": result.score,
                    "calibration_run_id": str(request.calibration_run_id)
                    if request.calibration_run_id
                    else None,
                    "calibration_fallback_code": fallback,
                    "attempted_calibration_run_id": attempted,
                }
            )
        # The registered operation changes score and adds exactly one marker.
        # Any other altered field must fail, even if projection could rebuild it.
        registered_corruption = dict(payload, score=0.9999, demo_tampered=True)
        if canonical_json(registered_corruption) != canonical_json(event.payload):
            raise RecoverySourceMismatch("Unregistered changes to risk payload or lineage")
        return payload

    @staticmethod
    def _to_domain(model: IntegrityIncidentModel) -> IntegrityIncident:
        return IntegrityIncident(
            integrity_incident_id=model.integrity_incident_id,
            affected_ledger_event_id=model.affected_ledger_event_id,
            detected_at=model.detected_at,
            expected_hash=model.expected_hash,
            actual_hash=model.actual_hash,
            corrupted_payload=dict(model.corrupted_payload),
            recovery_status=model.recovery_status,
            trusted_recovery_source=model.trusted_recovery_source,
            recovery_method=model.recovery_method,
            operator=model.operator,
            recovered_at=model.recovered_at,
        )

    @staticmethod
    def _model_to_dict(model: IntegrityIncidentModel) -> dict[str, Any]:
        return {
            "integrity_incident_id": str(model.integrity_incident_id),
            "affected_ledger_event_id": model.affected_ledger_event_id,
            "detected_at": canonical_timestamp(model.detected_at),
            "expected_hash": model.expected_hash,
            "actual_hash": model.actual_hash,
            "corrupted_payload": model.corrupted_payload,
            "recovery_status": model.recovery_status,
            "trusted_recovery_source": model.trusted_recovery_source,
            "recovery_method": model.recovery_method,
            "operator": model.operator,
            "recovered_at": (
                canonical_timestamp(model.recovered_at)
                if model.recovered_at is not None
                else None
            ),
        }
