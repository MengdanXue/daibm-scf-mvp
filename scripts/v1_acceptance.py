"""v1.0.0 end-to-end acceptance walk over HTTP against a running stack.

企业申请 → 风险评估 → 模型决策 → 放款 → 还款 → 逾期 → 预警 → 任务处理 → 结果反馈 → 模型治理

Every step goes through the public API as the role that owns it, on a stack
started with the demo dataset (an ACTIVE model must exist). Prints one line per
step and exits non-zero on the first failure.

    DAIBM_DEMO_PASSWORD=... python scripts/v1_acceptance.py --base-url http://127.0.0.1:8010
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib import request as urlrequest
from urllib.error import HTTPError
from http.cookiejar import CookieJar


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.opener = urlrequest.build_opener(urlrequest.HTTPCookieProcessor(CookieJar()))

    def call(self, method: str, path: str, body: dict | None = None, expect: int = 200):
        data = None if body is None else json.dumps(body).encode()
        req = urlrequest.Request(self.base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=60) as response:
                status, payload = response.status, response.read()
        except HTTPError as error:
            status, payload = error.code, error.read()
        parsed = json.loads(payload) if payload else None
        if status != expect:
            raise AssertionError(f"{method} {path} -> {status} (expected {expect}): {parsed}")
        return parsed


def login(base: str, username: str, password: str) -> Client:
    client = Client(base)
    client.call("POST", "/api/v1/auth/login", {"username": username, "password": password})
    return client


def step(message: str) -> None:
    print(f"[v1] {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    args = parser.parse_args()
    password = os.environ.get("DAIBM_DEMO_PASSWORD", "")
    if not password:
        print("DAIBM_DEMO_PASSWORD is required", file=sys.stderr)
        return 2
    base = args.base_url
    supplier, core, financier, risk, auditor = (
        login(base, f"{name}.demo", password)
        for name in ("supplier", "core", "financier", "risk", "auditor")
    )
    risk_user = risk.call("GET", "/api/v1/auth/me")
    suffix = uuid.uuid4().hex[:6].upper()

    # 1. Enterprise application.
    lenders = supplier.call("GET", "/api/v1/organizations/lenders")
    app = supplier.call("POST", "/api/v1/applications", {
        "core_enterprise_organization_code": "CORE-001",
        "lender_organization_code": lenders[0]["organization_code"],
        "contract_number": f"V1-{suffix}", "invoice_number": f"INV-V1-{suffix}",
        "amount": 2000.0, "term_days": 90, "payment_delay_days": 6, "counterparty_risk": 0.3,
        "invoice_mismatch": False, "relationship_months": 14, "transactions_last_30d": 10,
    }, expect=201)
    rid = app["request_id"]
    app = supplier.call("POST", f"/api/v1/applications/{rid}/submit", {"version": app["version"]})
    app = core.call("POST", f"/api/v1/applications/{rid}/trade-confirmation", {
        "version": app["version"], "confirmed": True, "comment": "Delivery verified",
        "confirmed_payable_amount": "2000.00"})
    step(f"application {rid} submitted to {lenders[0]['organization_code']} and trade confirmed")

    # 2-3. Risk assessment with the lender's ACTIVE model, then the decision.
    app = financier.call("POST", f"/api/v1/applications/{rid}/risk-assessment", {"version": app["version"]})
    evidence = app["risk_evidence"]
    assert evidence["calibration_run_id"], "the ACTIVE model did not calibrate the decision"
    (decision,) = financier.call("GET", f"/api/v1/applications/{rid}/risk-decisions")
    lineage = auditor.call("GET", f"/api/v1/risk-decisions/{decision['decision_record_id']}/lineage")
    step(f"risk assessed: raw {evidence['raw_score']} -> calibrated {evidence['final_score']} "
         f"by {lineage['model_version']['label']} trained on snapshot "
         f"{lineage['dataset_snapshot']['snapshot_id']} ({len(lineage['training_outcome_ids'])} outcomes)")
    app = financier.call("POST", f"/api/v1/applications/{rid}/decision",
                         {"version": app["version"], "decision": "approved", "comment": "Approved"})
    app = risk.call("POST", f"/api/v1/applications/{rid}/control-action",
                    {"version": app["version"], "comment": "Standard monitoring"})
    app = auditor.call("POST", f"/api/v1/applications/{rid}/audit-review",
                       {"version": app["version"], "comment": "Verified"})
    step(f"decision approved, controlled and audited ({app['status']})")

    # 4. Disbursement (first installment already due, second in the future).
    facility = financier.call("POST", "/api/v1/facilities", {
        "request_id": rid, "principal": "2000.00", "currency": "CNY", "version": 1,
        "idempotency_key": str(uuid.uuid4()),
        "installments": [
            {"sequence": 1, "due_date": (date.today() - timedelta(days=20)).isoformat(), "amount": "1000.00"},
            {"sequence": 2, "due_date": (date.today() + timedelta(days=40)).isoformat(), "amount": "1000.00"},
        ]}, expect=201)
    fid = facility["facility_id"]
    for action in ("initiate-disbursement", "confirm-disbursement"):
        facility = financier.call("POST", f"/api/v1/facilities/{fid}/{action}",
                                  {"version": facility["version"], "idempotency_key": str(uuid.uuid4())})
    step(f"facility {fid} disbursed ({facility['status']})")

    def pay(index: int) -> None:
        nonlocal facility
        installment = facility["installments"][index]
        facility = supplier.call("POST", f"/api/v1/facilities/{fid}/payments", {
            "installment_id": installment["installment_id"], "amount": installment["amount"],
            "payment_reference": f"V1-PAY-{index}", "version": facility["version"],
            "idempotency_key": str(uuid.uuid4())})
        facility = financier.call("POST", f"/api/v1/facilities/{fid}/payments/{facility['payments'][-1]['payment_id']}/decision", {
            "decision": "confirmed", "comment": "Received", "version": facility["version"],
            "idempotency_key": str(uuid.uuid4())})

    # 5. Repayment of the second installment.
    pay(1)
    step(f"second installment repaid (outstanding {facility['outstanding_amount']})")

    # 6. Overdue.
    first = facility["installments"][0]["installment_id"]
    facility = financier.call("POST", f"/api/v1/facilities/{fid}/mark-overdue", {
        "installment_id": first, "days_past_due": 20,
        "evidence_sha256": hashlib.sha256(f"overdue-{fid}".encode()).hexdigest(),
        "version": facility["version"], "idempotency_key": str(uuid.uuid4())})
    step(f"first installment overdue ({facility['status']})")

    # 7. Alert.
    risk.call("POST", "/api/v1/risk/alerts/scan")
    alerts = risk.call("GET", f"/api/v1/risk/alerts?facility_id={fid}")
    alert = next(item for item in alerts if item["status"] == "OPEN")
    alert = risk.call("POST", f"/api/v1/risk/alerts/{alert['alert_id']}/assign",
                      {"version": alert["version"], "owner_user_id": risk_user["user_id"]})
    alert = risk.call("POST", f"/api/v1/risk/alerts/{alert['alert_id']}/start", {"version": alert["version"]})
    step(f"alert {alert['rule_key'] if 'rule_key' in alert else alert['alert_id']} assigned and in processing")

    # 8. Task handling.
    task = risk.call("POST", "/api/v1/risk/tasks", {
        "title": "Collect the overdue installment", "task_type": "COLLECTION",
        "description": "Agree a payment date with the supplier.", "assignee_user_id": risk_user["user_id"],
        "due_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        "alert_id": alert["alert_id"]}, expect=201)
    tid = task["task_id"]
    task = risk.call("POST", f"/api/v1/risk/tasks/{tid}/start", {"version": task["version"]})
    task = risk.call("POST", f"/api/v1/risk/tasks/{tid}/notes", {"version": task["version"],
                                                                  "note": "Supplier promised payment today"})
    task = risk.call("POST", f"/api/v1/risk/tasks/{tid}/result", {
        "version": task["version"], "filename": "call-log.txt", "content_type": "text/plain",
        "content_base64": base64.b64encode(b"Call log: payment promised").decode()})
    pay(0)
    task = risk.call("POST", f"/api/v1/risk/tasks/{tid}/complete",
                     {"version": task["version"], "result_summary": "Overdue installment collected"})
    alert = risk.call("POST", f"/api/v1/risk/alerts/{alert['alert_id']}/resolve",
                      {"version": alert["version"], "resolution": "Installment collected"})
    alert = auditor.call("POST", f"/api/v1/risk/alerts/{alert['alert_id']}/close",
                         {"version": alert["version"], "comment": "Payment verified"})
    step(f"task {task['status']}, overdue cured, facility {facility['status']}, alert {alert['status']}")

    # 9. Outcome feedback.
    before = {item["id"] for item in auditor.call("GET", "/api/v1/model-versions")["versions"]}
    facility = auditor.call("POST", f"/api/v1/facilities/{fid}/close",
                            {"version": facility["version"], "idempotency_key": str(uuid.uuid4())})
    outcome = auditor.call("POST", f"/api/v1/facilities/{fid}/actual-outcome", {
        "idempotency_key": str(uuid.uuid4()), "observed_at": facility["closed_at"],
        "evidence_sha256": hashlib.sha256(f"outcome-{fid}".encode()).hexdigest(),
        "provenance": "CONTROLLED_DEMO"}, expect=201)
    step(f"facility closed; outcome {outcome['outcome']['outcome_id']} recorded, training job queued")

    # 10. Model governance: the worker retrains on the lender's snapshot.
    deadline = time.monotonic() + 120
    new: list[dict] = []
    while time.monotonic() < deadline and not new:
        time.sleep(3)
        registry = auditor.call("GET", "/api/v1/model-versions")
        new = [item for item in registry["versions"] if item["id"] not in before]
    assert new, "no new model version after the outcome"
    version = new[0]
    detail = auditor.call("GET", f"/api/v1/model-versions/{version['id']}")
    step(f"new model {version['label']} ({version['organization_code']}): {version['status']}, "
         f"snapshot {version['dataset_snapshot_id']}, "
         f"transitions {[item['to_status'] for item in detail['transitions']]}")
    active = registry["active_by_scope"]["controlled_demo"]
    step(f"ACTIVE model now {active['label']}; lineage and ledger verified: "
         f"{auditor.call('GET', '/api/ledger/verify')['valid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
