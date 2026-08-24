# Fabric Anchoring and ZKP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in real Hyperledger Fabric hash-anchoring network and a real Groth16 proof that a committed invoice amount does not exceed a public financing limit.

**Architecture:** PostgreSQL remains authoritative. Business transactions append a transactional outbox row; an optional dispatcher submits payload-free hashes through a narrow Node Fabric Gateway, and the UI verifies PostgreSQL/Fabric agreement. The same gateway proves/verifies one fixed Circom circuit and stores only public signals, version, result, and proof digest.

**Tech Stack:** Hyperledger Fabric 2.5.16 LTS, `@hyperledger/fabric-gateway` 1.12.0, Node.js 24, Circom 2.2.3, circomlib 2.0.5, snarkjs 0.7.6, Docker Compose profiles, FastAPI, PostgreSQL/Alembic.

**Spec:** `docs/superpowers/specs/2026-08-24-research-evidence-defense-pack-design.md`

## Global Constraints

- The default `docker compose up` path must remain PostgreSQL + FastAPI only and pass when Fabric is absent.
- Fabric records no names, invoice/contract identifiers, amounts, feature payloads, or credentials; only opaque IDs, hashes, versions, timestamps, and proof digest.
- Use one local orderer, one peer organization, one channel, and one chaincode; describe it as local anchoring, not decentralized production consensus.
- Represent amount/limit as unsigned 64-bit integer minor units and constrain every bit in the circuit.
- Demo proving keys and MSP identities are local development material, not production trust evidence.
- A Fabric outage leaves a retryable outbox row and never rolls back an already committed financing transaction.

---

### Task 1: Invoice-limit circuit and deterministic artifact contract

**Files:**
- Create: `advanced/zkp/circuits/invoice_limit.circom`
- Create: `advanced/zkp/package.json`
- Create: `advanced/zkp/package-lock.json`
- Create: `advanced/zkp/scripts/build-artifacts.mjs`
- Create: `advanced/zkp/scripts/verify-artifacts.mjs`
- Create: `advanced/zkp/test/invoice-limit.test.mjs`
- Create: `advanced/zkp/artifacts/manifest.json`
- Create: `scripts/build-zkp-artifacts.ps1`

**Interfaces:**
- Produces: public signals `[commitment, financingLimit]`, private inputs `{invoiceAmount, salt}`, `proveInvoiceLimit(input)`, `verifyInvoiceLimit(proof, publicSignals)`, and a SHA-256 artifact manifest.

- [ ] **Step 1: Write failing valid, invalid, and wraparound tests**

```javascript
it('proves a committed amount within the public limit', async () => {
  const result = await proveInvoiceLimit({ invoiceAmount: 120000000n, financingLimit: 150000000n, salt: 73421n });
  assert.equal(await verifyInvoiceLimit(result.proof, result.publicSignals), true);
  assert.equal(result.publicSignals.length, 2);
});

it('rejects amount above limit and values outside 64 bits', async () => {
  await assert.rejects(() => proveInvoiceLimit({ invoiceAmount: 150000001n, financingLimit: 150000000n, salt: 1n }));
  await assert.rejects(() => proveInvoiceLimit({ invoiceAmount: 2n ** 64n, financingLimit: 2n ** 64n - 1n, salt: 1n }));
});
```

- [ ] **Step 2: Run and verify failure**

Run: `docker run --rm -v "${PWD}/advanced/zkp:/work" -w /work node:24-bookworm-slim npm test`

Expected: FAIL because the circuit/artifacts/functions are absent.

- [ ] **Step 3: Implement the fixed circuit**

```circom
pragma circom 2.2.3;
include "../node_modules/circomlib/circuits/poseidon.circom";
include "../node_modules/circomlib/circuits/comparators.circom";

template InvoiceLimit() {
    signal input invoiceAmount;
    signal input salt;
    signal input financingLimit;
    signal output commitment;
    component amountBits = Num2Bits(64);
    component limitBits = Num2Bits(64);
    component lessEq = LessEqThan(64);
    component poseidon = Poseidon(2);
    amountBits.in <== invoiceAmount;
    limitBits.in <== financingLimit;
    lessEq.in[0] <== invoiceAmount;
    lessEq.in[1] <== financingLimit;
    lessEq.out === 1;
    poseidon.inputs[0] <== invoiceAmount;
    poseidon.inputs[1] <== salt;
    commitment <== poseidon.out;
}
component main {public [financingLimit]} = InvoiceLimit();
```

Compile with Circom 2.2.3, use a local demo Powers-of-Tau/circuit contribution, export WASM/R1CS/zkey/verification key, and write canonical hashes plus the explicit `demo_trusted_setup=true` boundary. Do not download setup material at application startup.

- [ ] **Step 4: Run proof vectors and artifact verification**

Run: `powershell -File scripts/build-zkp-artifacts.ps1`

Run: `docker run --rm -v "${PWD}/advanced/zkp:/work" -w /work node:24-bookworm-slim npm test`

Expected: valid proof passes, over-limit/tampered-public-signal proofs fail, and every artifact hash verifies.

- [ ] **Step 5: Commit circuit source, lockfile, tests, and fixed demo artifacts**

```powershell
git add advanced/zkp scripts/build-zkp-artifacts.ps1
git commit -m "feat: add invoice-limit zero-knowledge proof"
```

### Task 2: Minimal Fabric network and anchor chaincode

**Files:**
- Create: `advanced/fabric/network/crypto-config.yaml`
- Create: `advanced/fabric/network/configtx.yaml`
- Create: `advanced/fabric/network/docker-compose.fabric.yml`
- Create: `advanced/fabric/network/bootstrap.sh`
- Create: `advanced/fabric/network/deploy-chaincode.sh`
- Create: `advanced/fabric/chaincode/package.json`
- Create: `advanced/fabric/chaincode/package-lock.json`
- Create: `advanced/fabric/chaincode/lib/anchor-contract.js`
- Create: `advanced/fabric/chaincode/index.js`
- Create: `advanced/fabric/chaincode/test/anchor-contract.test.js`
- Modify: `.gitignore`

**Interfaces:**
- Produces chaincode transactions `CreateAnchor(anchorJson)`, `ReadAnchor(anchorId)`, and `AnchorExists(anchorId)` on channel `scfchannel`, contract `audit-anchor`.

- [ ] **Step 1: Write failing deterministic/idempotent chaincode tests**

```javascript
it('stores only the declared hash envelope', async () => {
  await contract.CreateAnchor(ctx, JSON.stringify(validAnchor));
  assert.deepEqual(JSON.parse(await contract.ReadAnchor(ctx, validAnchor.anchorId)), validAnchor);
  assert.equal(JSON.stringify(validAnchor).includes('invoiceNumber'), false);
});

it('accepts an identical retry and rejects conflicting reuse', async () => {
  await contract.CreateAnchor(ctx, JSON.stringify(validAnchor));
  await contract.CreateAnchor(ctx, JSON.stringify(validAnchor));
  await assert.rejects(() => contract.CreateAnchor(ctx, JSON.stringify({...validAnchor, eventHash: otherHash})));
});
```

- [ ] **Step 2: Run and verify failure**

Run: `docker run --rm -v "${PWD}/advanced/fabric/chaincode:/work" -w /work node:24-bookworm-slim npm test`

Expected: FAIL because chaincode is absent.

- [ ] **Step 3: Implement validated chaincode and pinned network**

Validate UUID/opaque identifiers, 64-character lowercase hashes, RFC3339 timestamps, allowed optional model/proof fields, and reject undeclared properties. Use Fabric images `hyperledger/fabric-orderer:2.5.16`, `hyperledger/fabric-peer:2.5.16`, and `hyperledger/fabric-tools:2.5.16`. Generate demo MSP/channel artifacts with `cryptogen` and `configtxgen` under ignored `output/fabric`, create `scfchannel`, join the peer, and deploy chaincode with endorsement policy `OR('Org1MSP.peer')`.

- [ ] **Step 4: Run unit test and live network smoke test**

Run: chaincode `npm test` command above.

Run: `docker compose -f advanced/fabric/network/docker-compose.fabric.yml up -d`

Run: `docker compose -f advanced/fabric/network/docker-compose.fabric.yml run --rm cli /network/deploy-chaincode.sh`

Run: a CLI `CreateAnchor` then `ReadAnchor` round trip.

Expected: unit tests pass and live query returns byte-for-byte canonical anchor JSON.

- [ ] **Step 5: Commit**

```powershell
git add advanced/fabric .gitignore
git commit -m "feat: add local Fabric anchoring network"
```

### Task 3: Narrow Fabric/ZKP Gateway

**Files:**
- Create: `advanced/gateway/package.json`
- Create: `advanced/gateway/package-lock.json`
- Create: `advanced/gateway/Dockerfile`
- Create: `advanced/gateway/src/config.js`
- Create: `advanced/gateway/src/fabric.js`
- Create: `advanced/gateway/src/zkp.js`
- Create: `advanced/gateway/src/server.js`
- Create: `advanced/gateway/test/server.test.js`

**Interfaces:**
- Consumes: Task 1 artifacts and Task 2 Fabric Gateway endpoint/identity.
- Produces authenticated local endpoints `GET /health`, `POST /anchors`, `GET /anchors/:id`, `POST /proofs/invoice-limit`, and `POST /proofs/invoice-limit/verify`.

- [ ] **Step 1: Write failing gateway API tests**

```javascript
it('submits and reads an anchor without business payload', async () => {
  const response = await request(app).post('/anchors').set('Authorization', token).send(validAnchor);
  assert.equal(response.status, 201);
  assert.deepEqual((await request(app).get(`/anchors/${validAnchor.anchorId}`).set('Authorization', token)).body, validAnchor);
});

it('returns proof digest but never private amount or salt', async () => {
  const response = await request(app).post('/proofs/invoice-limit').set('Authorization', token).send(validPrivateInput);
  assert.equal(response.status, 201);
  assert.equal('invoiceAmount' in response.body, false);
  assert.equal('salt' in response.body, false);
  assert.match(response.body.proofSha256, /^[0-9a-f]{64}$/);
});
```

- [ ] **Step 2: Run and verify failure**

Run: `docker run --rm -v "${PWD}/advanced:/work" -w /work/gateway node:24-bookworm-slim npm test`

Expected: FAIL because gateway modules are absent.

- [ ] **Step 3: Implement local-only authenticated adapter**

Use `@hyperledger/fabric-gateway@1.12.0`, gRPC TLS, exact contract/channel names, and an application token read from a mounted Docker secret/file. Bind `0.0.0.0:8090` only inside the Compose network; expose no host port. Canonicalize proof JSON before hashing, zero private input references after proving, enforce request-size limits, and use consistent `{code,message}` errors.

- [ ] **Step 4: Run unit and live integration tests**

Run: gateway `npm test` command above.

Run: start gateway against Task 2 network and execute health, proof, anchor, and query requests from the Compose network.

Expected: all responses verify; incorrect token, tampered proof, and conflicting anchor return non-2xx.

- [ ] **Step 5: Commit**

```powershell
git add advanced/gateway
git commit -m "feat: bridge Fabric anchors and ZKP verification"
```

### Task 4: Transactional outbox and Python advanced services

**Files:**
- Create: `app/models_advanced.py`
- Create: `app/repositories/anchors.py`
- Create: `app/services/anchor_dispatch.py`
- Create: `app/services/zkp.py`
- Create: `alembic/versions/20260824_0006_anchor_outbox_and_proofs.py`
- Create: `tests/test_anchor_outbox.py`
- Create: `tests/test_zkp_service.py`
- Modify: `app/config.py`
- Modify: `app/models.py`
- Modify: `app/main.py`

**Interfaces:**
- Produces: `AnchorOutboxService.enqueue`, `dispatch_batch(limit=20)`, `ZkpService.create_limit_proof`, and persisted states `pending|anchored|failed` / `verified|rejected`.

- [ ] **Step 1: Write failing outbox/retry/privacy tests**

```python
def test_business_commit_and_outbox_are_atomic(session_factory, ledger_event):
    enqueue_anchor_in_transaction(session_factory, ledger_event)
    assert read_outbox(ledger_event.id).event_hash == ledger_event.event_hash

def test_gateway_outage_keeps_retryable_pending_row(anchor_service, fake_gateway):
    fake_gateway.fail_with_timeout()
    anchor_service.dispatch_batch()
    row = read_outbox()
    assert row.status == "pending" and row.attempt_count == 1

def test_zkp_record_contains_no_private_amount_or_salt(zkp_service):
    result = zkp_service.create_limit_proof(facility_id, amount, limit, salt)
    assert "invoice_amount" not in result and "salt" not in result
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_anchor_outbox.py tests/test_zkp_service.py -q`

Expected: FAIL because schema/services are absent.

- [ ] **Step 3: Implement migration, repositories, and bounded retry loop**

Add unique `anchor_id`, event FK, canonical envelope, status, attempts, `next_attempt_at`, Fabric transaction ID, and last error. Claim rows with `FOR UPDATE SKIP LOCKED`; HTTP timeout 5 seconds; exponential retry capped at 60 seconds. When `FABRIC_GATEWAY_URL` is absent, do not start a dispatcher and report `disabled` without creating failures.

Store proof public signals, circuit/key hashes, result, and proof digest. Accept private amount/salt only in local call scope and gateway request body; do not log them.

- [ ] **Step 4: Run migration/service tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_anchor_outbox.py tests/test_zkp_service.py tests/test_database.py -q`

Expected: PASS with disabled/default mode and enabled/fake-gateway behavior.

- [ ] **Step 5: Commit**

```powershell
git add app/models.py app/models_advanced.py app/repositories/anchors.py app/services/anchor_dispatch.py app/services/zkp.py app/config.py app/main.py alembic tests/test_anchor_outbox.py tests/test_zkp_service.py
git commit -m "feat: add retryable Fabric outbox and proof records"
```

### Task 5: Advanced APIs, health, and bilingual evidence UI

**Files:**
- Create: `app/api/advanced.py`
- Create: `tests/test_advanced_api.py`
- Modify: `app/main.py`
- Modify: `app/static/index.html`
- Modify: `app/static/workflow.css`
- Modify: `app/static/workflow.js`
- Modify: `tests/test_ui_contract.py`

**Interfaces:**
- Produces: `GET /api/v1/advanced/health`, `GET /anchors/{event_id}`, `POST /facilities/{id}/limit-proof`, and UI proof/anchor status cards.

- [ ] **Step 1: Write failing API/UI tests**

```python
def test_default_health_remains_ok_when_advanced_profile_is_disabled(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/v1/advanced/health").json()["fabric"] == "disabled"

def test_limit_proof_requires_financier_and_never_returns_private_input(client, login_user, facility):
    login_user(client, "financier.demo")
    response = client.post(f"/api/v1/facilities/{facility.id}/limit-proof", json={"financing_limit_minor": 150000000, "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 201
    assert "invoice_amount" not in response.text and "salt" not in response.text
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_advanced_api.py tests/test_ui_contract.py -q`

Expected: FAIL because advanced API/UI is absent.

- [ ] **Step 3: Implement role-safe endpoints and truthful UI**

Derive private amount from the authorized facility record, generate salt with `secrets.randbelow`, and never accept amount/salt from the browser. Display `pending`, `anchored`, `mismatch`, proof verified, public limit, commitment, circuit version, and proof hash. Russian/Chinese text must say “local Fabric hash anchor” and “numeric limit proof,” never “invoice authenticity” or “fully decentralized blockchain.”

- [ ] **Step 4: Run API/UI tests and default browser acceptance**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_advanced_api.py tests/test_ui_contract.py -q`

Run: `.\.venv\Scripts\python.exe scripts\browser_acceptance.py`

Expected: PASS with Fabric disabled and no regression to the original journey.

- [ ] **Step 5: Commit**

```powershell
git add app/api/advanced.py app/main.py app/static tests/test_advanced_api.py tests/test_ui_contract.py
git commit -m "feat: expose Fabric and ZKP evidence"
```

### Task 6: Advanced Compose launcher, live acceptance, and boundaries

**Files:**
- Create: `docker-compose.advanced.yml`
- Create: `start-advanced-demo.cmd`
- Create: `scripts/advanced_browser_acceptance.py`
- Create: `tests/test_advanced_release_contract.py`
- Modify: `Dockerfile`
- Modify: `README.md`
- Modify: `docs/demo-script.md`
- Modify: `docs/thesis-traceability.md`
- Modify: `docs/mvp-design.md`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: one advanced launcher and live proof→outbox→Fabric→query acceptance while preserving ordinary launcher semantics.

- [ ] **Step 1: Write failing Compose/launcher boundary tests**

```python
def test_advanced_services_are_not_in_default_compose():
    default = _read("docker-compose.yml")
    advanced = _read("docker-compose.advanced.yml")
    assert "fabric-peer" not in default and "fabric-gateway" not in default
    assert "hyperledger/fabric-peer:2.5.16" in advanced
    assert "advanced-gateway" in advanced

def test_advanced_launcher_checks_fabric_and_zkp_health():
    text = _read("start-advanced-demo.cmd")
    assert "/api/v1/advanced/health" in text
    assert "fabric" in text and "zkp" in text
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_advanced_release_contract.py -q`

Expected: FAIL because advanced release files are absent.

- [ ] **Step 3: Implement opt-in composition and acceptance route**

Compose the default services plus pinned Fabric network and gateway; mount ignored MSP/channel state and fixed ZKP artifacts read-only. The launcher bootstraps only the named project, deploys chaincode idempotently, waits for advanced health, and opens the existing page. Browser acceptance creates a facility proof, waits for anchor status `anchored`, queries it back, compares hashes, switches Russian/Chinese, and saves `output/advanced-evidence-acceptance.png`.

- [ ] **Step 4: Run complete default and advanced verification**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Run: `docker compose up --build -d` and normal health/browser acceptance.

Run: `docker compose -f docker-compose.yml -f docker-compose.advanced.yml up --build -d` and advanced health/browser acceptance.

Expected: both profiles pass independently; stopping advanced services leaves the default path usable and pending anchors retryable.

- [ ] **Step 5: Security/boundary review and commit**

Verify no private amount/salt in Fabric state, logs, API responses, or screenshots; no host gateway port; demo keys clearly labeled; dependency lockfiles committed.

```powershell
git add docker-compose.advanced.yml start-advanced-demo.cmd scripts/advanced_browser_acceptance.py tests/test_advanced_release_contract.py Dockerfile README.md docs
git commit -m "feat: ship optional Fabric and ZKP demonstration"
git push origin main
```
