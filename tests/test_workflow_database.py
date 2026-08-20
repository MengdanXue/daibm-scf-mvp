from sqlalchemy import inspect


def test_migrated_schema_contains_identity_and_workflow_tables(migrated_engine):
    table_names = set(inspect(migrated_engine).get_table_names())

    assert {
        "organizations",
        "users",
        "user_sessions",
        "workflow_actions",
    } <= table_names


def test_financing_requests_support_draft_workflow_state(migrated_engine):
    inspector = inspect(migrated_engine)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("financing_requests")
    }

    assert columns["status"]["nullable"] is False
    assert columns["version"]["nullable"] is False
    assert columns["updated_at"]["nullable"] is False
    assert columns["risk_score"]["nullable"] is True
    assert columns["decision"]["nullable"] is True
    assert columns["explanations"]["nullable"] is True
    assert columns["control_action"]["nullable"] is True
    assert {
        "created_by_user_id",
        "supplier_organization_id",
        "core_enterprise_organization_id",
        "contract_number",
        "invoice_number",
    } <= columns.keys()


def test_identity_and_workflow_foreign_keys_are_indexed(migrated_engine):
    inspector = inspect(migrated_engine)
    for table in ("users", "user_sessions", "workflow_actions"):
        assert inspector.has_table(table), f"missing table: {table}"

    indexed_columns = {
        table: {
            column
            for index in inspector.get_indexes(table)
            for column in index["column_names"]
        }
        for table in ("users", "user_sessions", "workflow_actions")
    }

    assert "organization_id" in indexed_columns["users"]
    assert {"user_id", "expires_at"} <= indexed_columns["user_sessions"]
    assert {"request_id", "actor_user_id", "created_at"} <= indexed_columns[
        "workflow_actions"
    ]
