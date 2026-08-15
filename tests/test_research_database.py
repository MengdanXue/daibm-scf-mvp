from sqlalchemy import inspect


RESEARCH_TABLES = {
    "dataset_versions",
    "synthetic_scenarios",
    "graph_snapshots",
    "model_runs",
    "model_versions",
    "risk_assessments",
    "policy_decisions",
    "integrity_incidents",
}


def test_research_migration_creates_registry_tables(migrated_engine):
    inspector = inspect(migrated_engine)

    assert RESEARCH_TABLES <= set(inspector.get_table_names())
    assert str(
        {
            column["name"]: column
            for column in inspector.get_columns("dataset_versions")
        }["manifest"]["type"]
    ) == "JSONB"
    assert str(
        {
            column["name"]: column
            for column in inspector.get_columns("risk_assessments")
        }["risk_score"]["type"]
    ) == "DOUBLE PRECISION"


def test_research_foreign_keys_have_query_indexes(migrated_engine):
    inspector = inspect(migrated_engine)

    for table_name in RESEARCH_TABLES:
        indexed_columns = {
            tuple(index["column_names"])
            for index in inspector.get_indexes(table_name)
            if index["column_names"]
        }
        for foreign_key in inspector.get_foreign_keys(table_name):
            columns = tuple(foreign_key["constrained_columns"])
            assert columns in indexed_columns, (table_name, columns)


def test_ledger_supports_global_stream_and_research_entities(migrated_engine):
    inspector = inspect(migrated_engine)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("ledger_events")
    }

    assert columns["stream_id"]["default"] == "'global'::text"
    assert inspector.get_foreign_keys("ledger_events") == []


def test_only_one_promoted_model_can_occupy_default_slot(migrated_engine):
    indexes = inspect(migrated_engine).get_indexes("model_versions")
    deployment_index = next(
        index
        for index in indexes
        if index["name"] == "uq_model_versions_promoted_slot"
    )

    assert deployment_index["unique"] is True
    assert deployment_index["column_names"] == ["deployment_slot"]
    assert "lifecycle_status = 'promoted'" in str(
        deployment_index["dialect_options"]["postgresql_where"]
    )
