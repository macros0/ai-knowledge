"""Format3 is complete only after cold authenticated verification of every part."""
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json

import pytest

from app.deployment.replica_backup import BackupError, BackupPlan, BackupWriter, verify_backup, recovery_point_age


KEY = bytes(range(32))
CANARY = b"PRIVATE_SOURCE_CONTENT_not_manifest_data"


def fixture_plan(*, consistent_at=None):
    now = consistent_at or datetime.now(timezone.utc) - timedelta(seconds=5)
    payloads = {f"{i:032x}": CANARY + bytes([i]) * (i + 1) for i in range(1, 5)}
    inventory = [{"id": entry_id, "component": component,
        "expected_size": len(payloads[entry_id]) if component == "artifacts" else None,
        "expected_sha256": hashlib.sha256(payloads[entry_id]).hexdigest() if component == "artifacts" else None}
        for entry_id, component in zip(payloads, ("postgres", "qdrant", "artifacts", "artifacts"), strict=True)]
    value = {"format": 3, "backup_id": "b" * 32, "schema_revision": "rpl05_maintenance_epoch",
        "started_at": (now - timedelta(seconds=2)).isoformat(),
        "source": {"installation_id": "a" * 32, "namespace": "replica-test-fixture1",
            "operation_id": "b" * 32, "epoch": 1, "consistent_at": now.isoformat(),
            "failure_domain": "source-pve-host", "unknown_writers": 0, "provider_requests": 0,
            "writers": {name: "stopped" for name in ("api", "worker", "audit_dispatcher",
                "diagnostic_children", "provider_requests", "maintenance_jobs")},
            "snapshot_id": "frozen-snapshot-1"},
        "destination": {"store_id": "independent-backup", "failure_domain": "separate-controller",
            "platform_evidence_sha256": "9" * 64},
        "images": {role: "sha256:" + "2" * 64 for role in ("frontend", "api", "worker")},
        "storage": [{"component": role, "provider": "managed", "recipe": "selected-basic-v1",
            "scope_sha256": str(i) * 64} for i, role in enumerate(("postgres", "qdrant", "artifacts"), 1)],
        "model_vector_sha256": "3" * 64,
        "counts": {"documents": 2, "concepts": 2, "chunks": 2, "blobs": 2, "work_items": 3, "audit_events": 1},
        "component_receipts": {role: "4" * 64 for role in ("postgres", "qdrant", "artifacts")},
        "key_reference": "secretref:backup-key-v1", "inventory": inventory}
    return BackupPlan.model_validate_json(json.dumps(value)), payloads


def bound_writer(root, plan, key, **kwargs):
    return BackupWriter(root, plan, key, destination_guard=lambda: plan.destination, **kwargs)


def create_backup(root, *, plan=None, payloads=None):
    if plan is None:
        plan, payloads = fixture_plan()
    with bound_writer(root, plan, KEY, source_guard=lambda: plan.source) as writer:
        for entry_id, data in payloads.items():
            writer.add(entry_id, BytesIO(data))
        return writer.finish()


def test_complete_backup_cold_verifies_all_components_without_plaintext(tmp_path):
    root = tmp_path / "backup"
    manifest = create_backup(root)
    cold = verify_backup(root, KEY)
    assert manifest.status == cold.status == "complete"
    assert json.loads((root / "manifest.json").read_text())["format"] == 3
    assert len(cold.objects) == 4
    assert cold.plan.counts.blobs == 2
    for path in root.rglob("*"):
        if path.is_file():
            assert CANARY not in path.read_bytes()
            assert KEY not in path.read_bytes()


def test_partial_backup_never_complete(tmp_path):
    plan, payloads = fixture_plan()
    root = tmp_path / "partial"
    with bound_writer(root, plan, KEY, source_guard=lambda: plan.source) as writer:
        writer.add(next(iter(payloads)), BytesIO(next(iter(payloads.values()))))
        with pytest.raises(BackupError):
            writer.finish()
    assert json.loads((root / "manifest.json").read_text())["status"] == "incomplete"
    with pytest.raises(BackupError):
        verify_backup(root, KEY)


@pytest.mark.parametrize("fault", ["wrong_key", "ciphertext", "manifest", "missing_object"])
def test_corruption_or_missing_component_never_verifies(tmp_path, fault):
    root = tmp_path / "backup"
    create_backup(root)
    key = KEY
    if fault == "wrong_key":
        key = b"x" * 32
    elif fault == "manifest":
        path = root / "manifest.json"
        value = json.loads(path.read_text())
        value["objects"].pop(next(iter(value["objects"])))
        path.write_text(json.dumps(value))
    else:
        path = next((root / "objects").glob("*.bin"))
        if fault == "missing_object":
            path.unlink()
        else:
            data = bytearray(path.read_bytes())
            data[-1] ^= 1
            path.write_bytes(data)
    with pytest.raises(BackupError):
        verify_backup(root, key)


def test_unknown_writer_and_same_failure_domain_refused_before_copy(tmp_path):
    plan, _payloads = fixture_plan()
    for mutate in ("unknown_writer", "same_domain"):
        value = plan.model_dump(mode="json")
        if mutate == "unknown_writer":
            value["source"]["writers"]["api"] = "unknown"
        else:
            value["destination"]["failure_domain"] = value["source"]["failure_domain"]
        with pytest.raises(ValueError):
            BackupPlan.model_validate_json(json.dumps(value))
    assert not (tmp_path / "backup").exists()


def test_lost_source_guard_prevents_completion(tmp_path):
    plan, payloads = fixture_plan()
    allowed = True
    def guard():
        return plan.source if allowed else plan.source.model_copy(update={"epoch": 2})
    root = tmp_path / "backup"
    with bound_writer(root, plan, KEY, source_guard=guard) as writer:
        for entry_id, data in payloads.items():
            writer.add(entry_id, BytesIO(data))
        allowed = False
        with pytest.raises(BackupError):
            writer.finish()
    assert json.loads((root / "manifest.json").read_text())["status"] == "incomplete"


def test_resume_preserves_completed_entries_and_rejects_changed_plan(tmp_path):
    plan, payloads = fixture_plan()
    root = tmp_path / "backup"
    first = next(iter(payloads))
    with bound_writer(root, plan, KEY, source_guard=lambda: plan.source) as writer:
        writer.add(first, BytesIO(payloads[first]))
    ciphertext = (root / "objects" / (first + ".bin")).read_bytes()
    with bound_writer(root, plan, KEY, source_guard=lambda: plan.source, resume=True) as writer:
        for entry_id, data in payloads.items():
            writer.add(entry_id, BytesIO(data))
        writer.finish()
    assert (root / "objects" / (first + ".bin")).read_bytes() == ciphertext
    assert verify_backup(root, KEY).status == "complete"
    changed = plan.model_copy(update={"key_reference": "different-key"})
    with pytest.raises(BackupError):
        with bound_writer(root, changed, KEY, source_guard=lambda: changed.source, resume=True):
            pass


def test_24h_age_from_consistent_at_not_copy_completion(tmp_path):
    now = datetime.now(timezone.utc)
    plan, payloads = fixture_plan(consistent_at=now - timedelta(hours=25))
    manifest = create_backup(tmp_path / "backup", plan=plan, payloads=payloads)
    assert recovery_point_age(manifest, now=now) == 25 * 3600


def test_source_checksum_mismatch_keeps_backup_incomplete(tmp_path):
    plan, _payloads = fixture_plan()
    entry = plan.inventory[-1]
    root = tmp_path / "backup"
    with bound_writer(root, plan, KEY, source_guard=lambda: plan.source) as writer:
        with pytest.raises(BackupError):
            writer.add(entry.id, BytesIO(b"changed accepted artifact"))
    assert json.loads((root / "manifest.json").read_text())["status"] == "incomplete"


def test_streaming_large_object_uses_bounded_reads(tmp_path):
    from app.deployment.replica_backup import CHUNK
    plan, payloads = fixture_plan()
    entry_id = plan.inventory[-1].id
    data = b"a" * CHUNK + b"tail" * 1024
    payloads[entry_id] = data
    values = plan.model_dump(mode="json")
    values["inventory"][-1].update(expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest())
    plan = BackupPlan.model_validate_json(json.dumps(values))
    class Bounded(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= CHUNK
            return super().read(size)
    root = tmp_path / "large"
    with bound_writer(root, plan, KEY, source_guard=lambda: plan.source) as writer:
        for key, value in payloads.items():
            writer.add(key, Bounded(value))
        writer.finish()
    assert verify_backup(root, KEY).objects[entry_id].size == len(data)


def test_resume_recovers_publication_before_manifest_receipt(tmp_path):
    plan, payloads = fixture_plan()
    root = tmp_path / "crash"
    with bound_writer(root, plan, KEY, source_guard=lambda: plan.source) as writer:
        before_publication = (root / "manifest.json").read_bytes()
        first = next(iter(payloads))
        writer.add(first, BytesIO(payloads[first]))
    # Simulate process death after atomic object publication but before the
    # first receipt publication. The original authenticated manifest survives.
    (root / "manifest.json").write_bytes(before_publication)
    with bound_writer(root, plan, KEY, source_guard=lambda: plan.source, resume=True) as writer:
        assert first in writer.manifest.objects
        for entry_id, data in payloads.items():
            writer.add(entry_id, BytesIO(data))
        writer.finish()
    assert verify_backup(root, KEY).status == "complete"


def test_growing_source_stops_at_accepted_size_instead_of_filling_destination(tmp_path):
    plan, _payloads = fixture_plan()
    class Growing:
        reads = 0
        def read(self, size):
            self.reads += 1
            if self.reads > 2:
                raise OSError("Test stopped an unbounded source")
            return b"x" * size
    source = Growing()
    with bound_writer(tmp_path / "growing", plan, KEY, source_guard=lambda: plan.source) as writer:
        with pytest.raises(BackupError):
            writer.add(plan.inventory[-1].id, source)
    assert source.reads == 2


def test_missing_destination_binding_cannot_complete_a_local_spool(tmp_path):
    plan, _payloads = fixture_plan()
    with pytest.raises(BackupError):
        with BackupWriter(tmp_path / "unverified-destination", plan, KEY, source_guard=lambda: plan.source):
            pass
    assert not (tmp_path / "unverified-destination").exists()


@pytest.mark.parametrize("component", ["postgres", "qdrant"])
def test_empty_provider_export_is_not_a_backup_component(tmp_path, component):
    plan, _payloads = fixture_plan()
    entry = next(e for e in plan.inventory if e.component == component)
    with bound_writer(tmp_path / "empty-provider", plan, KEY, source_guard=lambda: plan.source) as writer:
        with pytest.raises(BackupError):
            writer.add(entry.id, BytesIO(b""))
