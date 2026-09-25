"""Backup and restore: database, calibration artifacts and audit data, hash-verified.

A backup directory contains::

    manifest.json            sha256 + size of every file, row counts, ledger head
    database/<table>.csv     every public table (COPY ... CSV), incl. alembic_version
    audit/ledger.jsonl       the hash-chained ledger, one event per line
    audit/security_events.jsonl
    artifacts.tar            the calibration artifact directory

Restore verifies every file hash first, migrates an empty target database to the
backup's schema revision, loads all tables in one transaction (triggers held
off), resets identity sequences, restores artifacts, then proves the result:
row counts equal the manifest, the ledger head hash matches and the ledger hash
chain verifies. Usage::

    python -m app.ops.backup create  --out DIR [--artifacts DIR]
    python -m app.ops.backup verify  DIR
    python -m app.ops.backup restore DIR [--artifacts DIR] --yes
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.repositories.ledger import LedgerRepository

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "manifest.json"
FORMAT = "daibm-backup-v1"


class BackupError(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tables(connection) -> list[str]:
    return list(
        connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        ).scalars()
    )


def fingerprint(engine: Engine) -> dict[str, Any]:
    """Row count of every table, schema revision and ledger head and verification."""

    with engine.connect() as connection:
        counts = {
            table: int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())
            for table in _tables(connection)
            if table != "alembic_version"
        }
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        head = connection.execute(
            text("SELECT event_hash FROM ledger_events ORDER BY id DESC LIMIT 1")
        ).scalar_one_or_none()
    with Session(engine) as session:
        verification = LedgerRepository().verify(session)
    return {
        "revision": revision,
        "row_counts": counts,
        "ledger_head_hash": head,
        "ledger_valid": bool(verification.get("valid")),
    }


def create_backup(engine: Engine, out_dir: Path, *, artifacts_dir: Path | None = None) -> dict[str, Any]:
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise BackupError(f"{out_dir} is not empty")
    (out_dir / "database").mkdir(parents=True, exist_ok=True)
    (out_dir / "audit").mkdir(exist_ok=True)
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        # One snapshot for every table.
        cursor.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        tables = [row[0] for row in cursor.fetchall()]
        for table in tables:
            with (out_dir / "database" / f"{table}.csv").open("wb") as handle:
                with cursor.copy(f'COPY "{table}" TO STDOUT WITH (FORMAT csv)') as copy:  # type: ignore[attr-defined]
                    for block in copy:
                        handle.write(bytes(block))
        for name, query in (
            ("ledger", "SELECT row_to_json(e) FROM ledger_events e ORDER BY id"),
            ("security_events", "SELECT row_to_json(e) FROM security_events e ORDER BY event_id"),
        ):
            cursor.execute(query)
            with (out_dir / "audit" / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
                for (row,) in cursor.fetchall():
                    handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        cursor.execute("COMMIT")
    finally:
        raw.close()
    if artifacts_dir is not None and Path(artifacts_dir).exists():
        with tarfile.open(out_dir / "artifacts.tar", "w") as archive:
            archive.add(str(artifacts_dir), arcname=".")
    files = {
        str(path.relative_to(out_dir)): {"sha256": _sha256(path), "bytes": path.stat().st_size}
        for path in sorted(out_dir.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "format": FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
        "files": files,
        "artifacts_dir": str(artifacts_dir) if artifacts_dir else None,
        **fingerprint(engine),
    }
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return manifest


def verify_backup(backup_dir: Path) -> dict[str, Any]:
    """Every file must match its recorded SHA-256; nothing extra, nothing missing."""

    backup_dir = Path(backup_dir)
    manifest = json.loads((backup_dir / MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise BackupError("unknown backup format")
    present = {
        str(path.relative_to(backup_dir))
        for path in backup_dir.rglob("*")
        if path.is_file() and path.name != MANIFEST
    }
    if present != set(manifest["files"]):
        raise BackupError(f"backup files differ from manifest: {sorted(present ^ set(manifest['files']))}")
    for name, recorded in manifest["files"].items():
        if _sha256(backup_dir / name) != recorded["sha256"]:
            raise BackupError(f"hash mismatch: {name}")
    return manifest


def restore_backup(
    backup_dir: Path, engine: Engine, *, artifacts_dir: Path | None = None
) -> dict[str, Any]:
    backup_dir = Path(backup_dir)
    manifest = verify_backup(backup_dir)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, manifest["revision"])
        connection.commit()
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("BEGIN")
        cursor.execute("SET LOCAL session_replication_role = replica")
        data_tables = [table for table in manifest["tables"] if table != "alembic_version"]
        cursor.execute("TRUNCATE " + ", ".join(f'"{table}"' for table in data_tables) + " CASCADE")
        for table in data_tables:
            with cursor.copy(f'COPY "{table}" FROM STDIN WITH (FORMAT csv)') as copy:  # type: ignore[attr-defined]
                copy.write((backup_dir / "database" / f"{table}.csv").read_bytes())
        cursor.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND (is_identity = 'YES' OR column_default LIKE 'nextval(%')"
        )
        for table, column in cursor.fetchall():
            cursor.execute(
                f"SELECT setval(pg_get_serial_sequence('\"{table}\"', '{column}'), "
                f"COALESCE((SELECT max(\"{column}\") FROM \"{table}\"), 0) + 1, false)"
            )
        cursor.execute("COMMIT")
    finally:
        raw.close()
    if (backup_dir / "artifacts.tar").exists():
        target = Path(artifacts_dir or manifest["artifacts_dir"])
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(backup_dir / "artifacts.tar") as archive:
            # The "data" filter refuses absolute paths, links out of the target and devices.
            archive.extractall(target, filter="data")
    result = fingerprint(engine)
    expected = {key: manifest[key] for key in ("revision", "row_counts", "ledger_head_hash")}
    actual = {key: result[key] for key in ("revision", "row_counts", "ledger_head_hash")}
    if actual != expected or not result["ledger_valid"]:
        raise BackupError(f"restore verification failed: expected {expected}, got {actual}, ledger_valid={result['ledger_valid']}")
    return {"verified": True, **result}


def _engine_from_env() -> Engine:
    from app.config import PostgresSettings

    return create_engine(PostgresSettings.from_env().sqlalchemy_url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ops.backup")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--out", required=True, type=Path)
    create.add_argument("--artifacts", type=Path)
    check = sub.add_parser("verify")
    check.add_argument("backup", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--artifacts", type=Path)
    restore.add_argument("--yes", action="store_true", help="replace all data in the target database")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            manifest = create_backup(_engine_from_env(), args.out, artifacts_dir=args.artifacts)
            print(json.dumps({"backup": str(args.out), "files": len(manifest["files"]),
                              "ledger_head_hash": manifest["ledger_head_hash"]}, indent=1))
        elif args.command == "verify":
            manifest = verify_backup(args.backup)
            print(json.dumps({"verified": True, "files": len(manifest["files"])}, indent=1))
        else:
            if not args.yes:
                print("restore replaces every table; pass --yes to confirm", file=sys.stderr)
                return 2
            print(json.dumps(restore_backup(args.backup, _engine_from_env(), artifacts_dir=args.artifacts),
                             indent=1, sort_keys=True))
    except BackupError as error:
        print(f"backup error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
