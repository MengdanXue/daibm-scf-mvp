# Changelog

All notable changes of DAIBM-SCF. Versions follow semantic versioning from v1.0.0.

## [1.0.0] — Product release

### Added
- **Tenant isolation completed.** Applications name their lender organization
  (`lender_organization_id`, fixed once submitted); calibration jobs, dataset
  snapshots, calibration runs and model versions carry an `organization_id`
  derived from and checked against their lineage. Each organization trains on
  its own outcomes and has its own ACTIVE model per scope; decisions are
  calibrated only with the lender's model. The database rejects any snapshot
  item, run observation, decision or outcome that would cross organizations.
- Supplier form chooses the lender; `GET /api/v1/organizations/lenders`.
- Stable demo dataset created only through the services
  (`app/demo_dataset.py`, `DAIBM_DEMO_DATASET=true` or `python -m app.demo_dataset`):
  normal, at-risk and defaulted enterprise stories, an ACTIVE and a CANDIDATE
  model, dataset snapshots and decision lineage.
- Performance baseline script and results (`scripts/perf_baseline.py`,
  `docs/performance/`).
- Release security tests: tracked-file secret scan, per-role permission matrix,
  audit coverage of login, denial, state change, model switch and configuration.
- `VERSION`, `RELEASE_NOTES.md`, `USER_GUIDE.md`, `V1_RELEASE_REPORT.md`.

### Changed
- Migration `20260930_0021` (backfill from lineage; refuses genuinely mixed
  historical rows; no synthetic state changes recorded).
- Model registry, snapshots, lineage, dashboard model panel and deployment APIs
  are scoped per organization; the registry page shows each model's owner.
- Facility creation is refused (404) for any organization other than the lender.
- API version is read from `VERSION`.

### Fixed
- Decision lineage of a not-yet-funded application was readable across organizations.
- A financier of any bank could open a facility on any approved application.

## [0.9.0] — Phase 4 Enterprise Readiness
Multi-tenant facilities, alerts, tasks and outcomes; `PermissionService`;
secrets from the environment; login lock-out, session expiry, secure cookies,
password policy; immutable security events; health, metrics, hash-verified
backup/restore; versioned configuration center; Docker healthcheck and restart
policy; admin console; `DEPLOYMENT_GUIDE.md`.

## [0.8.0] — Phase 3 Risk Operations Platform
Risk dashboard, alert center, task center, risk detail, versioned risk rules,
in-process rule monitor, admin role.

## [0.7.x] — Phase 2 Model & Outcome Governance
Model version registry (DRAFT → CANDIDATE → ACTIVE, rollback), outcome review
lifecycle, eligibility service, dataset snapshots, decision lineage.

## [0.6] — Phase 1 and earlier
Five-role financing workflow, governed facility lifecycle, hash-chained audit
ledger with integrity recovery, research core (TGNN/XGBoost, ONNX), optional
Fabric anchoring and ZKP invoice-limit proof.
