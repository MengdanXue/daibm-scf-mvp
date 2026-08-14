# MVP Design

## Objective

Demonstrate the smallest defensible DAIBM-SCF loop:

```text
financing request
    -> auditable request event
    -> explainable risk score
    -> financing decision
    -> control action
    -> auditable feedback event
```

## Components

### API and UI

FastAPI provides the JSON API, generated OpenAPI documentation, and a dependency-free HTML dashboard.

### PostgreSQL data layer

PostgreSQL 17 is the only database. SQLAlchemy repositories isolate persistence from the FastAPI routes, while Alembic is the only schema-management path. Financing identifiers use UUID, monetary amounts use `NUMERIC(14,2)`, timestamps use `TIMESTAMPTZ`, and variable feature/explanation payloads use JSONB.

Creating a financing request and its four audit events is one database transaction. PostgreSQL transaction-level advisory locking serializes chain-head updates so concurrent requests cannot create two branches from the same previous hash.

### Audit ledger

Every event includes the previous event hash. The current event hash is calculated from the previous hash, timestamp, type, entity ID, and canonical JSON payload. `GET /api/ledger/verify` recomputes the chain and reports the first invalid event.

This is a tamper-evident log persisted in PostgreSQL, not a distributed blockchain.

### Risk baseline

The baseline converts six transparent features into normalized risk factors:

- requested amount;
- recent payment delay;
- counterparty risk;
- invoice mismatch;
- relationship age;
- recent transaction velocity.

The factors are combined into a logistic score. The API returns each contribution so that a reviewer can explain why a scenario was approved, rejected, or sent to manual review.

### Closed-loop action

- Low risk: standard monitoring.
- Medium risk: request additional documents and enhanced validation.
- High risk: suspend automatic approval and require enhanced validation.

The action is written back to the audit ledger, providing the minimal AI-to-control feedback loop described by the thesis architecture.

## Non-goals for v0.1

- distributed consensus;
- production identity and access management;
- cryptographic privacy proofs;
- trained graph or temporal models;
- claims of financial or operational improvement;
- integration with external financial institutions.

## Next defensible increments

1. Replace the baseline with a reproducible XGBoost experiment on a documented synthetic dataset.
2. Add supply-chain graph construction and a temporal split protocol.
3. Add a TGNN research module with fixed seeds and comparison baselines.
4. Move the audit adapter behind an interface and add a Hyperledger Fabric implementation.
5. Add privacy-preserving fields only after a concrete threat model is defined.
