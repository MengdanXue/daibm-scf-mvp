import json

from app.db import connect, initialize
from app.ledger import Ledger


def test_ledger_detects_payload_tampering(tmp_path):
    db_path = tmp_path / "ledger.db"
    initialize(db_path)
    ledger = Ledger(db_path)
    ledger.append("REQUEST", "r-1", {"amount": 100})
    ledger.append("DECISION", "r-1", {"decision": "approved"})
    assert ledger.verify()["valid"] is True

    with connect(db_path) as connection:
        connection.execute(
            "UPDATE ledger_events SET payload_json = ? WHERE id = 1",
            (json.dumps({"amount": 999}),),
        )
    verification = ledger.verify()
    assert verification["valid"] is False
    assert verification["invalid_event_id"] == 1

