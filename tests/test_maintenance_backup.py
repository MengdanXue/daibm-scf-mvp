"""Cold-backup safety checks, with a separately opted-in disposable Docker probe."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import uuid

import pytest

from scripts import maintenance_backup as backup


HELPER = "sha256:" + "a" * 64
CONTENT = b"synthetic backup evidence\n"


def file_record(content=CONTENT):
    return {
        "path": "evidence.bin", "mode": 0o640, "uid": 1234, "gid": 2345,
        "mtime": 1700000000, "type": "file", "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def root_record():
    return {
        "path": ".", "mode": 0o750, "uid": 3456, "gid": 4567,
        "mtime": 1700000001, "type": "directory",
    }


@pytest.fixture
def plan():
    return {
        "schema": "daibm.cold-backup.v1", "helper_image": HELPER,
        "containers": {
            "synthetic-db": {"id": "b" * 64, "image": "sha256:" + "c" * 64},
            "synthetic-peer": {"id": "d" * 64, "image": "sha256:" + "e" * 64},
        },
        "stores": [
            {"name": "database", "container": "synthetic-db", "type": "volume",
             "source": "SyntheticDatabase", "destination": "/data"},
            {"name": "peer-ledger", "container": "synthetic-peer", "type": "volume",
             "source": "SyntheticLedger", "destination": "/ledger"},
        ],
    }


class FakeDocker:
    """Independent source snapshots and real tar bytes, never a Docker process."""

    def __init__(self, plan):
        self.plan = plan
        self.sources = {}
        for name, expected in plan["containers"].items():
            self.sources[name] = {
                "Name": "/" + name, "Id": expected["id"], "Image": expected["image"],
                "State": {"Running": False, "Restarting": False, "Paused": False},
                "HostConfig": {"RestartPolicy": {"Name": "no"}}, "Mounts": [],
            }
        for store in plan["stores"]:
            mount = {"Type": store["type"], "Destination": store["destination"], "RW": True}
            mount["Name" if store["type"] == "volume" else "Source"] = store["source"]
            self.sources[store["container"]]["Mounts"].append(mount)
        self.foreign = {}
        self.events = []
        self.archives = 0
        self.after_archive = None
        self.fail_archive = False
        self.archive_content = CONTENT
        self.final_content = CONTENT

    def inspect(self, names):
        if list(names) == list(self.plan["containers"]):
            self.events.append(("frozen-check", deepcopy(self.sources)))
        return deepcopy([(self.sources | self.foreign)[name] for name in names])

    def call(self, args, *, destination=None):
        self.events.append(("call", list(args)))
        if args == ["ps", "-q"]:
            return "\n".join(self.foreign).encode()
        # Any accidental service start/stop, Compose invocation, or mutable source mount fails.
        assert args[:6] == ["run", "--rm", "--network", "none", "--read-only", "--mount"]
        assert args[6].endswith(",target=/source,readonly")
        assert args[7] == "--entrypoint"
        assert args[9] == HELPER
        if args[8] == "python":
            assert destination is None
            content = self.final_content if self.archives == len(self.plan["stores"]) else CONTENT
            return json.dumps([root_record(), file_record(content)]).encode()
        assert args[8] == "tar"
        assert args[10:] == ["-C", "/source", "-czf", "-", "."]
        assert isinstance(destination, Path)
        if self.fail_archive:
            destination.write_bytes(b"partial archive")
            raise RuntimeError("synthetic tar failure")
        with tarfile.open(destination, "w:gz") as archive:
            root = tarfile.TarInfo(".")
            root.type = tarfile.DIRTYPE
            for attribute in ("mode", "uid", "gid", "mtime"):
                setattr(root, attribute, root_record()[attribute])
            archive.addfile(root)
            info = tarfile.TarInfo("./evidence.bin")
            expected = file_record(self.archive_content)
            for attribute in ("mode", "uid", "gid", "mtime", "size"):
                setattr(info, attribute, expected[attribute])
            archive.addfile(info, io.BytesIO(self.archive_content))
        self.archives += 1
        if self.after_archive:
            self.after_archive(self)
        return b""


def assert_no_helpers(docker):
    assert not any(kind == "call" and value[0] == "run" for kind, value in docker.events)


@pytest.mark.parametrize("field,value", [
    ("Id", "f" * 64), ("Image", "sha256:" + "f" * 64),
])
def test_identity_and_image_mismatch_rejected_before_output(plan, tmp_path, field, value):
    docker = FakeDocker(plan)
    docker.sources["synthetic-db"][field] = value
    destination = tmp_path / "backup"
    with pytest.raises(RuntimeError, match="identity/image changed"):
        backup.backup_frozen(plan, destination, docker)
    assert not destination.exists()
    assert_no_helpers(docker)


@pytest.mark.parametrize("state", ["Running", "Restarting", "Paused"])
def test_active_source_is_rejected(plan, tmp_path, state):
    docker = FakeDocker(plan)
    docker.sources["synthetic-peer"]["State"][state] = True
    with pytest.raises(RuntimeError, match="not frozen"):
        backup.backup_frozen(plan, tmp_path / "backup", docker)
    assert_no_helpers(docker)


@pytest.mark.parametrize("policy", ["always", "unless-stopped", "on-failure"])
def test_source_automatic_restart_is_rejected(plan, tmp_path, policy):
    docker = FakeDocker(plan)
    docker.sources["synthetic-db"]["HostConfig"]["RestartPolicy"]["Name"] = policy
    with pytest.raises(RuntimeError, match="Automatic restart"):
        backup.backup_frozen(plan, tmp_path / "backup", docker)
    assert_no_helpers(docker)


@pytest.mark.parametrize("change", ["missing", "duplicate", "destination", "type", "source", "case"])
def test_source_mount_must_match_exactly(plan, tmp_path, change):
    docker = FakeDocker(plan)
    mounts = docker.sources["synthetic-db"]["Mounts"]
    if change == "missing":
        mounts.clear()
    elif change == "duplicate":
        mounts.append(deepcopy(mounts[0]))
    elif change == "destination":
        mounts[0]["Destination"] = "/other"
    elif change == "type":
        mounts[0]["Type"] = "bind"
    elif change == "source":
        mounts[0]["Name"] = "different-volume"
    else:
        mounts[0]["Name"] = mounts[0]["Name"].lower()
    with pytest.raises(RuntimeError, match="Source mount"):
        backup.backup_frozen(plan, tmp_path / "backup", docker)
    assert_no_helpers(docker)


@pytest.mark.parametrize("relationship", ["same", "parent", "child"])
def test_foreign_running_bind_writer_is_rejected(plan, tmp_path, relationship):
    source = tmp_path / "synthetic" / "source"
    plan["stores"][0].update(type="bind", source=str(source))
    docker = FakeDocker(plan)
    foreign_source = {"same": source, "parent": source.parent, "child": source / "nested"}[relationship]
    docker.foreign["foreign-writer"] = {
        "Name": "/foreign-writer", "Mounts": [
            {"Type": "bind", "Source": str(foreign_source), "RW": True},
        ],
    }
    with pytest.raises(RuntimeError, match="running container can write"):
        backup.backup_frozen(plan, tmp_path / "backup", docker)
    assert_no_helpers(docker)


def test_foreign_running_volume_writer_is_rejected(plan, tmp_path):
    docker = FakeDocker(plan)
    docker.foreign["foreign-writer"] = {
        "Name": "/foreign-writer", "Mounts": [
            {"Type": "volume", "Name": "SyntheticLedger", "RW": True},
        ],
    }
    with pytest.raises(RuntimeError, match="running container can write"):
        backup.backup_frozen(plan, tmp_path / "backup", docker)
    assert_no_helpers(docker)


@pytest.mark.parametrize("mount", [
    {"Type": "volume", "Name": "SyntheticLedger", "RW": False},
    {"Type": "volume", "Name": "unrelated-synthetic-volume", "RW": True},
])
def test_foreign_readonly_or_unrelated_mount_is_allowed(plan, tmp_path, mount):
    docker = FakeDocker(plan)
    docker.foreign["unrelated"] = {"Name": "/unrelated", "Mounts": [mount]}
    assert backup.backup_frozen(plan, tmp_path / "backup", docker)["archives_verified"]


def test_all_sources_remain_frozen_through_complete_backup_without_service_mutations(plan, tmp_path):
    docker = FakeDocker(plan)
    destination = tmp_path / "backup"
    result = backup.backup_frozen(plan, destination, docker)
    assert result["sources_remain_frozen"] and result["archives_verified"]
    assert json.loads((destination / "backup-manifest.json").read_text()) == result
    checks = [snapshot for kind, snapshot in docker.events if kind == "frozen-check"]
    assert len(checks) >= len(plan["stores"]) + 2
    for snapshot in checks:
        assert set(snapshot) == set(plan["containers"])
        assert all(not any(item["State"].values()) for item in snapshot.values())
        assert all(item["HostConfig"]["RestartPolicy"]["Name"] == "no" for item in snapshot.values())
    assert docker.archives == len(plan["stores"])
    for item in result["files"]:
        data = (destination / item["name"]).read_bytes()
        assert item["bytes"] == len(data)
        assert item["sha256"] == hashlib.sha256(data).hexdigest()
    assert {entry["name"] for entry in result["files"]} >= {"database.tar.gz", "peer-ledger.tar.gz"}
    assert all(args[0] in {"ps", "run"} for kind, args in docker.events if kind == "call")


@pytest.mark.parametrize("on_archive", [1, 2])
def test_source_restart_mid_backup_prevents_completion(plan, tmp_path, on_archive):
    docker = FakeDocker(plan)

    def restart_source(fake):
        if fake.archives == on_archive:
            fake.sources["synthetic-peer"]["State"]["Running"] = True

    docker.after_archive = restart_source
    destination = tmp_path / "backup"
    with pytest.raises(RuntimeError, match="not frozen"):
        backup.backup_frozen(plan, destination, docker)
    assert not (destination / "backup-manifest.json").exists()
    assert docker.archives == on_archive


def test_new_foreign_writer_between_stores_prevents_completion(plan, tmp_path):
    docker = FakeDocker(plan)

    def introduce_writer(fake):
        fake.foreign["new-writer"] = {
            "Name": "/new-writer", "Mounts": [
                {"Type": "volume", "Name": "SyntheticDatabase", "RW": True},
            ],
        }

    docker.after_archive = introduce_writer
    destination = tmp_path / "backup"
    with pytest.raises(RuntimeError, match="running container can write"):
        backup.backup_frozen(plan, destination, docker)
    assert docker.archives == 1
    assert not (destination / "backup-manifest.json").exists()


@pytest.mark.parametrize("failure", ["archive-error", "archive-mismatch", "source-mutation"])
def test_incomplete_or_mutated_backup_has_no_completion_marker(plan, tmp_path, failure):
    docker = FakeDocker(plan)
    if failure == "archive-error":
        docker.fail_archive = True
    elif failure == "archive-mismatch":
        docker.archive_content = b"different archive content"
    else:
        docker.final_content = b"source changed after archives"
    destination = tmp_path / "backup"
    with pytest.raises(RuntimeError):
        backup.backup_frozen(plan, destination, docker)
    assert destination.is_dir()
    assert not (destination / "backup-manifest.json").exists()
    assert all(not item["State"]["Running"] for item in docker.sources.values())


def test_preexisting_destination_is_untouched_and_never_overwritten(plan, tmp_path):
    docker = FakeDocker(plan)
    destination = tmp_path / "already-used"
    destination.mkdir()
    sentinel = destination / "backup-manifest.json"
    original = b"existing independent backup; must remain untouched\n"
    sentinel.write_bytes(original)
    with pytest.raises(FileExistsError):
        backup.backup_frozen(plan, destination, docker)
    assert list(destination.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == original
    assert_no_helpers(docker)


@pytest.mark.skipif(
    os.environ.get("DAIBM_RUN_COLD_BACKUP_DOCKER_TEST") != "1",
    reason="Explicit opt-in required for uniquely labelled disposable Docker resources",
)
def test_disposable_real_docker_backup_preserves_content_ownership_and_freeze(tmp_path):
    """Never references deployment/clone resources; cleanup checks exact IDs and labels."""
    assert "TEST_POSTGRES_URL" not in os.environ, "Real backup probe rejects any database URL"
    helper = os.environ.get("DAIBM_BACKUP_HELPER_IMAGE", "")
    assert helper.startswith("sha256:") and len(helper) == 71, "Supply a full local helper image ID"
    docker = backup.Docker("desktop-linux")
    image = json.loads(docker.call(["image", "inspect", helper]))[0]
    assert image["Id"] == helper
    token = uuid.uuid4().hex
    source_name = "cold-backup-probe-source-" + token
    volume_name = "cold-backup-probe-volume-" + token
    label = "org.daibm.cold-backup-test"
    owned_volume = False
    owned_source_id = None
    try:
        docker.call(["volume", "create", "--label", label + "=" + token, volume_name])
        owned_volume = True
        seed = (
            "import os,pathlib; p=pathlib.Path('/seed/evidence.bin'); "
            "p.write_bytes(b'synthetic backup evidence\\n'); os.chown(p,1234,2345); "
            "p.chmod(0o640); os.utime(p,(1700000000,1700000000)); "
            "root=pathlib.Path('/seed'); os.chown(root,3456,4567); root.chmod(0o750); "
            "os.utime(root,(1700000001,1700000001))"
        )
        docker.call([
            "run", "--rm", "--network", "none", "--read-only", "--label", label + "=" + token,
            "--mount", "type=volume,source=" + volume_name + ",target=/seed",
            "--entrypoint", "python", helper, "-c", seed,
        ])
        owned_source_id = docker.call([
            "create", "--name", source_name, "--label", label + "=" + token,
            "--restart", "no", "--network", "none", "--read-only",
            "--mount", "type=volume,source=" + volume_name + ",target=/data",
            "--entrypoint", "python", helper, "-c", "pass",
        ]).decode().strip()
        assert len(owned_source_id) == 64
        source = docker.inspect([owned_source_id])[0]
        assert source["Config"]["Labels"][label] == token
        assert source["State"]["Status"] == "created"
        real_plan = {
            "schema": "daibm.cold-backup.v1", "helper_image": helper,
            "containers": {source_name: {"id": owned_source_id, "image": helper}},
            "stores": [{"name": "synthetic-data", "container": source_name,
                        "type": "volume", "source": volume_name, "destination": "/data"}],
        }
        destination = tmp_path / "real-backup"
        result = backup.backup_frozen(real_plan, destination, docker)
        assert result["archives_verified"] and result["sources_remain_frozen"]
        assert backup.archive_manifest(destination / "synthetic-data.tar.gz") == [root_record(), file_record()]
        with tarfile.open(destination / "synthetic-data.tar.gz", "r:gz") as archive:
            member = next(m for m in archive if m.name.removeprefix("./") == "evidence.bin")
            assert archive.extractfile(member).read() == CONTENT
            assert (member.uid, member.gid, member.mode) == (1234, 2345, 0o640)
        after = docker.inspect([owned_source_id])[0]
        assert after["State"]["Status"] == "created"
        assert after["State"]["StartedAt"] == source["State"]["StartedAt"]
        assert not after["State"]["Running"]
        assert after["HostConfig"]["RestartPolicy"]["Name"] == "no"
    finally:
        if owned_source_id:
            source = docker.inspect([owned_source_id])[0]
            assert source["Id"] == owned_source_id
            assert source["Name"] == "/" + source_name
            assert source["Config"]["Labels"][label] == token
            assert not source["State"]["Running"]
            docker.call(["rm", owned_source_id])
        if owned_volume:
            volume = json.loads(docker.call(["volume", "inspect", volume_name]))[0]
            assert volume["Name"] == volume_name and volume["Labels"][label] == token
            docker.call(["volume", "rm", volume_name])
