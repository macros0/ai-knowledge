"""Cold backup health and operator-owned attempt status; no scheduler or providers."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from filelock import FileLock
from pydantic import AwareDatetime, model_validator

from app.deployment.replica_backup import (
    Hex32, Model, _regular_open, _sync_directory, recovery_point_age, verify_backup,
)
from app.services.diagnostics.paths import assert_no_links, canonical_root


MAX_ATTEMPT_BYTES = 16384


class BackupAttempt(Model):
    version: Literal[1] = 1
    installation_id: Hex32
    attempt_id: Hex32
    state: Literal["running", "complete", "failed"]
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    backup_id: Hex32 | None = None
    error_code: Literal["backup_failed"] | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.state == "running":
            valid = self.finished_at is None and self.backup_id is None and self.error_code is None
        else:
            valid = self.finished_at is not None and self.finished_at >= self.started_at
            valid = valid and (self.backup_id is not None and self.error_code is None if self.state == "complete"
                else self.backup_id is None and self.error_code == "backup_failed")
        if not valid:
            raise ValueError("Inconsistent backup attempt")
        return self


def _aware(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Aware observation time required")


def _path(path):
    selected = Path(path)
    root = canonical_root(selected.parent)
    selected = root / selected.name
    assert_no_links(selected, root)
    if not root.is_dir():
        raise ValueError("Existing protected journal directory required")
    return selected


def _assert_no_write(path):
    marker = path.with_name(path.name + ".writing")
    assert_no_links(marker, path.parent)
    if marker.exists():
        raise ValueError("Backup attempt status write is uncertain")


def load_attempt(path):
    try:
        path = _path(path)
        _assert_no_write(path)
        with _regular_open(path, path.parent) as stream:
            raw = stream.read(MAX_ATTEMPT_BYTES + 1)
        _assert_no_write(path)
        if len(raw) > MAX_ATTEMPT_BYTES:
            raise ValueError("Attempt journal exceeds its bound")
        return BackupAttempt.model_validate_json(raw)
    except Exception:
        raise ValueError("Backup attempt journal is unavailable or invalid") from None


def _persist(path, attempt):
    assert_no_links(path, path.parent)
    marker = path.with_name(path.name + ".writing")
    assert_no_links(marker, path.parent)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".pending")
    try:
        # Publish uncertainty before the status replacement. If a later write,
        # rename or fsync fails, readers must refuse even a visible complete JSON.
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(b"status-write-in-progress\n")
            output.flush(); os.fsync(output.fileno())
        _sync_directory(path.parent)
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write((attempt.model_dump_json() + "\n").encode())
            output.flush(); os.fsync(output.fileno())
        assert_no_links(path, path.parent)
        os.replace(temporary, path)
        _sync_directory(path.parent)
        # The new status is already durable. If this unflushed marker removal
        # is lost in a crash, recovery conservatively reports uncertainty.
        marker.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def start_attempt(path, *, installation_id, now):
    _aware(now)
    path = _path(path)
    lock = path.with_name(path.name + ".lock")
    assert_no_links(lock, path.parent)
    with FileLock(lock, timeout=5):
        _assert_no_write(path)
        if path.exists():
            previous = load_attempt(path)
            if previous.installation_id != installation_id or previous.state == "running" or previous.finished_at > now:
                raise ValueError("Another or uncertain backup attempt cannot be replaced")
        attempt = BackupAttempt(installation_id=installation_id, attempt_id=uuid4().hex,
            state="running", started_at=now)
        _persist(path, attempt)
        return attempt


def finish_attempt(path, attempt, *, now, backup_id=None, failed=False):
    _aware(now)
    path = _path(path)
    lock = path.with_name(path.name + ".lock")
    assert_no_links(lock, path.parent)
    with FileLock(lock, timeout=5):
        current = load_attempt(path)
        if current != attempt or current.state != "running":
            raise ValueError("Only the recorded active backup attempt may finish")
        completed = BackupAttempt(installation_id=current.installation_id, attempt_id=current.attempt_id,
            state="failed" if failed else "complete", started_at=current.started_at, finished_at=now,
            backup_id=backup_id, error_code="backup_failed" if failed else None)
        _persist(path, completed)
        return completed


def assess_backup(archive, key, *, installation_id, destination_store_id, now,
                  attempt_path=None, max_age_seconds=86400, attempt_timeout_seconds=3600):
    """Authenticate every object; expose fixed alerts, never paths/provider/key errors.

    A healthy archive is copy evidence only. It never certifies a restore drill or
    production RPO. Supply the same latest-attempt journal used by the backup CLI
    to detect failures that occur while an older good copy is still available.
    """
    _aware(now)
    if (type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400
            or type(attempt_timeout_seconds) is not int or not 1 <= attempt_timeout_seconds <= 86400):
        raise ValueError("Bounded backup health policy required")
    result = {"schema_version": 1, "state": "alert", "checked_at": now.isoformat(), "alerts": [],
        "archive_verified": False, "backup_id": None, "recovery_point_age_seconds": None,
        "max_age_seconds": max_age_seconds, "attempt_status": None,
        "restore_verified": False, "rpo_assured": False}
    manifest = None
    try:
        manifest = verify_backup(archive, key)
        if (manifest.plan.source.installation_id != installation_id
                or manifest.plan.destination.store_id != destination_store_id):
            raise ValueError("Selected recovery scope differs")
        age = recovery_point_age(manifest, now=now)
        if manifest.completed_at > now:
            raise ValueError("Backup verification time is in the future")
        result.update(archive_verified=True, backup_id=manifest.plan.backup_id, recovery_point_age_seconds=age)
        if age > max_age_seconds:
            result["alerts"].append("backup_recovery_point_stale")
    except Exception:
        manifest = None
        result["alerts"].append("backup_unverifiable")
    pending = False
    if attempt_path is not None:
        try:
            attempt = load_attempt(attempt_path)
            if (attempt.installation_id != installation_id or attempt.started_at > now
                    or attempt.finished_at is not None and attempt.finished_at > now):
                raise ValueError("Selected attempt scope/time differs")
            result["attempt_status"] = attempt.state
            if attempt.state == "failed":
                result["alerts"].append("backup_last_attempt_failed")
            elif attempt.state == "running":
                if (now - attempt.started_at).total_seconds() > attempt_timeout_seconds:
                    result["alerts"].append("backup_attempt_overdue")
                else:
                    pending = True
            elif manifest is not None:
                if attempt.backup_id != manifest.plan.backup_id:
                    result["alerts"].append("backup_selection_stale")
                elif attempt.finished_at < manifest.completed_at:
                    raise ValueError("Attempt completed before its verified archive")
        except Exception:
            result["alerts"].append("backup_attempt_unverifiable")
    result["state"] = "alert" if result["alerts"] else "pending" if pending else "healthy"
    return result
