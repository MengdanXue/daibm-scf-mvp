# Thesis Traceability and Evidence Matrix

This is the canonical boundary between claims in the frozen thesis and evidence produced by this repository. A status describes implementation maturity; provenance describes where the evidence came from. Synthetic implementation can therefore be `IMPLEMENTED` with `SYNTHETIC_DEMO` provenance without becoming empirical validation on real enterprises.

## Controlled vocabulary

Implementation status is restricted to `IMPLEMENTED`, `PARTIAL`, `SIMULATED`, `PLANNED`, and `NOT IMPLEMENTED`.

Evidence provenance is restricted to `THESIS_REPORTED`, `2026_REIMPLEMENTATION`, `SYNTHETIC_DEMO`, and `CONFERENCE_RERUN`.

## Matrix

| Thesis section and claim | Frozen-thesis location | Repository component | Status | Provenance | Verification artifact | Limitation or next action |
|---|---|---|---|---|---|---|
| Supply-chain finance application state | Architecture chapters | FastAPI, SQLAlchemy, PostgreSQL, Alembic | IMPLEMENTED | 2026_REIMPLEMENTATION | PostgreSQL integration and migration tests | Demonstration architecture, not a bank production system |
| Auditable transaction history | §3 and §4.5.3, body PDF p.67 | PostgreSQL JSONB hash chain | IMPLEMENTED | SYNTHETIC_DEMO | Ledger verification and concurrency tests | Tamper-evident ledger, not blockchain |
| Integrity-violation recovery | §4.5.3, body PDF p.67 | Integrity incident and recovery service | PLANNED | SYNTHETIC_DEMO | Recovery tests and audit events | Must leave detection and recovery evidence |
| Hyperledger Fabric network | Thesis architecture discussion | Adapter boundary only | NOT IMPLEMENTED | THESIS_REPORTED | This matrix and architecture design | No peers, ordering service, channels, or chaincode |
| PoA+ consensus | Thesis architecture discussion | No runtime component | NOT IMPLEMENTED | THESIS_REPORTED | This matrix | Requires a distributed protocol and threat model |
| Smart contracts | Thesis architecture discussion | Policy Engine is not chaincode | NOT IMPLEMENTED | THESIS_REPORTED | Policy tests | Declarative application policy only |
| Zero-knowledge proof | Thesis architecture discussion | No runtime component | NOT IMPLEMENTED | THESIS_REPORTED | This matrix | Requires defined private statements and proof system |
| Transparent rule score | Existing MVP risk loop | `RULE_LOGISTIC_BASELINE` | IMPLEMENTED | SYNTHETIC_DEMO | Rule ordering and API tests | Hand-authored logistic-shaped rule, not fitted LR |
| Synthetic 500 × 24 reference dataset | §4.1.2 comparison context, body PDF p.51 | Deterministic generator and manifest | PLANNED | SYNTHETIC_DEMO | Dataset hash and invariant tests | Not representative empirical data |
| Directed temporal graph | §3.2.3, body PDF p.41 | Graph builder and snapshot registry | PLANNED | SYNTHETIC_DEMO | Direction, window, leakage, and hash tests | First GCN symmetrizes adjacency for aggregation |
| XGBoost comparison | §3.2.3 and §4.2.3, body PDF pp.41,57 | Offline XGBoost trainer | PLANNED | SYNTHETIC_DEMO | Fixed-split metrics and model hash | Comparison model, not default runtime model |
| Minimal GCN–BiLSTM TGNN | §3.2.3 and §4.2.3, body PDF pp.41,57 | PyTorch trainer and ONNX runtime | PLANNED | SYNTHETIC_DEMO | Training log, metrics, ONNX parity, artifact hash | Smaller than the full thesis architecture; no attention |
| Original thesis TGNN metrics | §4.2.3, body PDF p.57 | No original data/code available | NOT IMPLEMENTED | THESIS_REPORTED | Frozen thesis only | Never relabel as a 2026 reproduction result |
| Model version registry | Research traceability requirement | PostgreSQL model/data/graph registries | PLANNED | 2026_REIMPLEMENTATION | Migration and lineage tests | Stores identities and locators, not large tensors |
| AI-assisted policy decision | Thesis closed-loop design | Policy Engine after model inference | PARTIAL | 2026_REIMPLEMENTATION | Exact boundary tests | Rule baseline exists; model-backed path is pending |
| Atomic AI → policy → ledger trace | Thesis closed-loop design | Assessment/decision application service | PLANNED | SYNTHETIC_DEMO | Rollback and lineage tests | Application control only; cannot alter infrastructure |
| Risk-injection demonstration | §4 simulation methodology | Immutable synthetic-scenario overlay | PLANNED | SYNTHETIC_DEMO | Before/after graph and inference trace | Input mutation only; score cannot be supplied directly |
| Thesis 10,000 enterprises / 500,000 transactions | §4.1.2, body PDF p.51 | No matching dataset in this repository | NOT IMPLEMENTED | THESIS_REPORTED | Frozen thesis only | Reference implementation intentionally uses 500 × 24 |
| Anonymous 1,200-enterprise empirical dataset | §4.1.2, body PDF p.51 | No real data in repository | NOT IMPLEMENTED | THESIS_REPORTED | Frozen thesis only | Access, consent, schema, and code are unavailable |
| Conference-grade multi-seed rerun and ablations | Future research package | `experiments/` boundary | PLANNED | CONFERENCE_RERUN | Mean/SD/CI/significance artifacts when executed | Outside the master-thesis MVP acceptance gate |

## Interpretation rules

- `THESIS_REPORTED` records what the frozen thesis says; it does not prove that this repository reproduced it.
- `SYNTHETIC_DEMO` is always shown prominently in the API and UI whenever generated data or simulated tampering is involved.
- A component moves to `IMPLEMENTED` only after its listed verification artifact exists and passes.
- Failed or weak model metrics remain recorded. There is no hidden minimum-score release gate.
