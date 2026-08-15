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
| Synthetic 500 × 24 reference dataset | §4.1.2 comparison context, body PDF p.51 | Deterministic generator and manifest | IMPLEMENTED | SYNTHETIC_DEMO | Invariant tests; reference SHA-256 `f784faa8bdef23625888d64de75c2f29a80c0a51e266e0822652569507648353` | Not representative empirical data |
| Directed temporal graph | §3.2.3, body PDF p.41 | Graph builder and snapshot registry | IMPLEMENTED | SYNTHETIC_DEMO | Direction, active-interval, temporal-window, leakage, normalization, and hash tests | First GCN symmetrizes adjacency for aggregation |
| XGBoost comparison | §3.2.3 and §4.2.3, body PDF pp.41,57 | Offline XGBoost trainer | IMPLEMENTED | SYNTHETIC_DEMO | Fixed-split metrics and model SHA-256 `6125e2ab113c0290661480d42c3f50076c9dabb684aa0f95566863298cd89e48` | Reference ROC-AUC `0.5969`; weak result retained honestly; not the default runtime model |
| Minimal GCN–BiLSTM TGNN | §3.2.3 and §4.2.3, body PDF pp.41,57 | PyTorch trainer and ONNX runtime | IMPLEMENTED | SYNTHETIC_DEMO | ROC-AUC `0.6597`; ONNX SHA-256 `158d273db310c3f1abf4be7cb06aee78568e475ebb7564ebbeaa16d3efeeb0e5`; parity max error `2.38e-7` | Smaller than the full thesis architecture; no attention; result is not the thesis-reported metric |
| Original thesis TGNN metrics | §4.2.3, body PDF p.57 | No original data/code available | NOT IMPLEMENTED | THESIS_REPORTED | Frozen thesis only | Never relabel as a 2026 reproduction result |
| Model version registry | Research traceability requirement | PostgreSQL model/data/graph registries | IMPLEMENTED | 2026_REIMPLEMENTATION | Migration, schema-drift, artifact-seeding, status, and inference tests | Stores identities and locators, not large tensors |
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
