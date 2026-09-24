# Direction B — Blockchain–AI Closed-Loop Design

Status: design/evidence map only. Direction B remains independent from Direction A experiments.

## Paper story

The proposed architectural distinction is not "blockchain plus AI" by itself. The claim to investigate is a governed bidirectional loop:

`business/audit state -> RiskAssessment -> PolicyEngine -> PolicyDecision -> LedgerEvent -> governed downstream state/evidence -> later assessment context`

The model is an estimator. It is **not** an authority boundary.

## Mandatory decision boundary

`PolicyEngine` is the only component permitted to translate a model score into a permitted business control action. A model implementation must not directly:

- mutate financing/application state;
- write arbitrary audit history;
- change database schema;
- change blockchain consensus configuration;
- alter validator/peer membership;
- bypass role authorization.

The current application already keeps `RiskAssessment -> PolicyEngine -> PolicyDecision` explicit and persists the resulting audit trace. This is implementation evidence for the application control boundary, not yet a conference ablation result.

## Ledger layers

Two mechanisms must remain accurately named:

1. PostgreSQL JSONB hash chain: **tamper-evident audit ledger**. It is not Hyperledger Fabric and must not be presented as a distributed blockchain.
2. Optional Hyperledger Fabric path under `advanced/fabric`: a minimal synthetic-demo anchor network that can anchor audit hashes. It remains materially smaller than the topology described in the frozen thesis and does not implement the thesis PoA+ consensus.

The paper must state which layer an experiment actually uses.

## Canonical event flow

### 1. RiskAssessment

Required lineage:

- assessment ID;
- enterprise/application identity within the synthetic/demo scope;
- model version and checkpoint/artifact hash;
- graph/data snapshot identity;
- feature/input hash;
- score and inference timestamp.

### 2. PolicyDecision

Required lineage:

- policy decision ID;
- source assessment ID;
- policy version;
- policy thresholds/rules;
- reason code;
- permitted action.

The policy may reject or constrain a high model score; score alone never authorizes a state transition.

### 3. LedgerEvent

The auditable event binds the assessment and policy lineage. Optional Fabric anchoring may bind the event/hash externally, but anchoring does not make the AI model a smart contract and does not move PolicyEngine logic on-chain.

## Evidence boundary

### ORIGINAL_THESIS_RESULT — reference only

The frozen thesis reports an ablation in which changing the proposed bidirectional mechanism to a one-way mechanism reduced risk-prediction performance by 18.3% and business indicators by 29.5%.

Those values are historical thesis claims. They are not reproduced by the current repository and must not be used as new implementation evidence.

### Current 2026 implementation evidence

Current code supports an application-level closed trace from research inference through PolicyEngine to persisted audit events, plus optional external hash anchoring. This can support an architecture/mechanism paper, but it does not by itself reproduce the thesis ablation or prove business improvement.

### Conference experiment still required

A Direction B conference rerun needs a separately frozen protocol comparing at least:

- governed bidirectional feedback;
- one-way AI output with no returned audited outcome/control context;
- no-policy-boundary or direct-model-action variant only as a **sandboxed simulation**, never by weakening production/demo authorization;
- ledger anchoring enabled/disabled when the measured question genuinely concerns audit integrity rather than prediction accuracy.

Metrics must be defined before test inspection. Candidate categories include predictive calibration after feedback, control-action error rate, trace completeness, tamper-detection coverage, and workflow/business simulation outcomes. The exact primary endpoint is `NEED HUMAN CONFIRMATION` before formal experiments.

## Non-claims

The current repository does not justify claims that:

- Fabric improves TGNN accuracy;
- blockchain consensus directly trains the model;
- the demo is a bank production system;
- the minimal Fabric topology reproduces the thesis validator network;
- the thesis -18.3%/-29.5% figures have been reproduced;
- synthetic workflow outcomes establish real enterprise benefit.

## Separation rule

Direction A may consume neither Fabric state nor PolicyEngine outcomes as labels. Direction B may reference a frozen model output as an input to the closed loop, but its contribution and experiment tables must remain distinct from Direction A's TGNN/propagation results.
