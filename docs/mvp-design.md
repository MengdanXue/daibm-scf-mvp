# Research Core v0.4 — Frozen MVP Design

This document preserves the frozen Research Core v0.4 scope, with later workflow annotations. Its original exclusions are not a current inventory of the integrated application; see [README](../README.md) and [the evidence matrix](thesis-traceability.md) for the optional local Fabric and proof components.

## Objective

Demonstrate the smallest defensible research and engineering loop for the thesis:

```text
deterministic synthetic data
    -> temporal supply-chain graph
    -> promoted TGNN inference
    -> explicit policy decision
    -> atomic PostgreSQL evidence
    -> tamper detection and evidence-preserving recovery
```

The application is a computer-simulation demonstrator. It proves that this path can be implemented and traced; it does not prove production banking performance or reproduce unavailable thesis data.

## Runtime topology

Docker Compose contains exactly two services: `postgres` and `mvp`. PostgreSQL 17 is the only database. FastAPI serves both JSON APIs and the dependency-free bilingual page. SQLAlchemy 2 is the shared persistence layer and Alembic is the only schema-management path.

The application constructs its connection from the five `POSTGRES_*` settings. There is no SQLite mode and no database-type switch. `RESEARCH_ARTIFACT_DIR` points to the promoted reference bundle inside the application container, and the shipped profile requires that bundle to verify successfully.

## Research evidence chain

### Data and graph

`synthetic-scf-v1` is deterministically generated with seed `20260815` for 500 enterprises over 24 months. It contains no real enterprise records. The graph builder retains directed supplier-to-customer relationships, creates symmetric self-loop-normalized adjacency for the GCN, and uses 12-month feature windows with future 3-month labels. Fixed train/validation/test anchors and the normalization fit boundary make the split inspectable and repeatable, not leakage-free. The historical TGNN checkpoint-selection protocol uses validation labels whose three-month horizon overlaps the earliest test prediction times. Existing checks cover windows, split anchors, and normalization boundaries; fixed-artifact replay does not establish strict prospective prediction.

### Models

The offline research package trains an XGBoost comparison and a minimal temporal graph neural network:

```text
32-dimensional GCN
    -> bidirectional LSTM (32 units per direction)
    -> MLP 64 → 32 → 1
```

The promoted TGNN is exported to ONNX and checked for PyTorch/ONNX parity. FastAPI never trains at startup. It verifies the artifact SHA-256, feature schema, dataset identity, and reference inputs, then performs real CPU inference with ONNX Runtime. It does not silently fall back to the rule baseline.

### Policy and atomic trace

Policy `scf-risk-policy-v0.4` maps scores to `NORMAL`, `ADDITIONAL_CHECK`, or `FINANCING_REVIEW` at thresholds `0.40` and `0.75`. A successful inference transaction persists the risk assessment, policy decision, and three linked ledger events together. Failure rolls back the whole trace.

### Risk scenario

Risk injection changes only a versioned synthetic input overlay. It cannot accept a caller-provided score or decision. The graph is rebuilt and the same promoted ONNX model reruns; the UI displays changed observable inputs, before/after scores, policy outcomes, lineage hashes, related enterprises, and ledger event IDs.

## PostgreSQL audit ledger

Each event hash binds the previous hash, timestamp, event type, entity ID, and canonical JSON payload. A transaction-level advisory lock serializes global chain-head updates. This is a tamper-evident application audit ledger stored in PostgreSQL, not a distributed blockchain.

Simulated tampering leaves the chain invalid and creates an unresolved integrity incident. Recovery restores only the affected trusted synthetic event, appends `INTEGRITY_VIOLATION_DETECTED` and `LEDGER_RECOVERY_COMPLETED`, and then verifies the extended chain. This is distinct from the explicitly destructive demo reset.

## User interface

Russian is the default language; every Research Core action and result also has Chinese copy. The Research Core page presents a visual evidence rail for data, graph, model, policy, and audit. It labels real model execution as `REAL MODEL INFERENCE` and generated inputs as `SYNTHETIC DATA`.

Integrated application v0.6 adds a database-backed five-role workflow around the frozen Research Core: supplier, core enterprise, financier, risk manager, and auditor. HttpOnly sessions and server-side authorization control visibility and transitions. The modular-monolith state machine advances a versioned application from draft to audited; each transition, workflow action, and ledger event is committed atomically. This workflow is engineering evidence for the thesis demonstrator and does not expand the scientific claim of Research Core v0.4.

New workflow applications also receive a SHA-256 fingerprint of their declared trade fields and a unique invoice-claim hash. The latter rejects reuse of the same normalized invoice by the same supplier/core-enterprise pair. This is duplicate-claim control over declared fields, not uploaded-document, tax-platform, blockchain, or ZKP verification. Business baseline assessments persist an assessment UUID, engine version, input hash, timestamp, and provenance; they remain explicitly separate from Research Core TGNN inference.

The Research Core dossier displays hash-verified TGNN and XGBoost rerun metrics under `2026_REIMPLEMENTATION`. The original application workflow remains an approval, risk-control, and audit loop. A separate versioned facility aggregate provides a controlled financing lifecycle simulation after an application is both approved and audited: exact-cent principal, two or more installments, simulated disbursement evidence, submitted and confirmed repayments, overdue control, full repayment, and audited closure. Every facility command is role-authorized, version checked, idempotent, and committed with its ledger evidence.

The facility aggregate does not execute a real bank transfer. It has no payment-rail, core-banking, interest, fees, FX, accounting, or reconciliation integration. A disbursement reference and a confirmed repayment are controlled synthetic evidence, not proof that external money moved.

The page is responsive, keyboard-focusable, reduced-motion aware, and uses only local assets. No external network API is needed during a defense demonstration.

## Health and startup contract

`start-demo.cmd` builds both containers and opens `http://127.0.0.1:8010` only when `/api/health` semantically confirms:

- PostgreSQL is reachable;
- the audit ledger is valid;
- the required promoted research artifact is ready.

A missing or incompatible model, unreachable database, or invalid ledger returns HTTP 503. Training is never part of startup.

## Evidence boundaries

Implemented in the integrated application: PostgreSQL application state, Alembic schema, deterministic synthetic generator, temporal graph builder, XGBoost comparison, minimal GCN–BiLSTM TGNN, ONNX Runtime inference, model/data/graph registries, policy engine, atomic decision trace, risk injection, hash-chain verification, evidence-preserving recovery, and the simulated financing-facility lifecycle described above.

Not implemented or not claimed: real enterprise data, numerical reproduction of the original thesis results, the full thesis TGNN, production credit decisioning, PoA+, production identity infrastructure, online TGNN learning, external bank integration, actual settlement, interest, fees, or accounting. Hyperledger Fabric, chaincode, and ZKP were outside the frozen v0.4 core; the integrated application now has optional local single-organisation hash anchoring and one invoice-limit proof circuit, with the limits recorded in the evidence matrix.

The canonical claim-to-evidence mapping is `docs/thesis-traceability.md`; no second matrix should be maintained.
