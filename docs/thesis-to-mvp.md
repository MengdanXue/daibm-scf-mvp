# Thesis-to-MVP Traceability

| Thesis concept | MVP status | Implementation boundary |
|---|---|---|
| Auditable transaction history | Implemented | PostgreSQL JSONB hash-chain event ledger with transaction-level advisory locking |
| AI-assisted risk assessment | Implemented as baseline | Transparent logistic score; no trained ML claim |
| Financing decision | Implemented | Three decision states with explicit thresholds |
| AI-to-system feedback | Implemented | Decision triggers a control event written to the ledger |
| Scenario-based validation | Implemented | Three synthetic financing requests plus real-PostgreSQL integration and concurrency tests |
| Hyperledger Fabric | Not implemented | Planned adapter boundary only |
| PoA+ consensus | Not implemented | Requires a real distributed protocol and threat model |
| Zero-knowledge proof | Not implemented | Requires concrete private attributes and proof statements |
| XGBoost/LSTM/TGNN | Not implemented | Reserved for a reproducible research increment |
| Real enterprise deployment | Not claimed | No real enterprise or bank data is included |

The MVP should be described as a scenario demonstrator or executable architecture slice. It must not be presented as empirical confirmation of the thesis's production or financial-effect claims.
