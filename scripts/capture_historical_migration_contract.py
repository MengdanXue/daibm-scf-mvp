"""Reproduce frozen contracts on a NEW explicitly named isolated test database.

Run before modifying historical migrations; stdout is reviewable JSON, not a
write to the contract. This deliberately refuses any non-test database name.
"""

import importlib.util
import json
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa

from app.migration_compatibility import schema_contract


url = sa.make_url(os.environ["TEST_POSTGRES_URL"])
if not (url.database or "").startswith("integration_test_"):
    raise ValueError("Only a dedicated integration_test_ database is permitted")
engine = sa.create_engine(url)
with engine.begin() as connection:
    if sa.inspect(connection).get_table_names():
        raise ValueError("Contract capture requires an empty database")
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.upgrade(config, "20260824_0009")
    contracts = {"baseline": schema_contract(connection)}
    path = Path("alembic/versions/20260824_0010_lifecycle_corrections_scope.py")
    spec = importlib.util.spec_from_file_location("capture_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        # Frozen historical DDL, never application ORM create_all.
        getattr(module, "upgrade_lifecycle_schema", module.upgrade)()
    contracts["lifecycle"] = schema_contract(connection)
    connection.execute(
        sa.text("ALTER TABLE calibration_runs DROP CONSTRAINT calibration_runs_dataset_sha256_key")
    )
    connection.execute(
        sa.text(
            "CREATE INDEX ix_calibration_runs_scope_dataset ON calibration_runs (deployment_scope, dataset_sha256)"
        )
    )
    contracts["recovery"] = schema_contract(connection)
    connection.execute(
        sa.text("ALTER TABLE financing_requests ADD COLUMN confirmed_payable_amount numeric(14,2)")
    )
    connection.execute(
        sa.text(
            "ALTER TABLE financing_requests ADD CONSTRAINT ck_financing_requests_confirmed_payable_amount CHECK (confirmed_payable_amount IS NULL OR confirmed_payable_amount > 0)"
        )
    )
    connection.execute(
        sa.text(
            "ALTER TABLE financing_requests ADD CONSTRAINT ck_financing_requests_payable_covers_amount CHECK (confirmed_payable_amount IS NULL OR confirmed_payable_amount >= amount)"
        )
    )
    contracts["proof"] = schema_contract(connection)
    print(json.dumps(contracts, indent=2, sort_keys=True))
    connection.rollback()
engine.dispose()
