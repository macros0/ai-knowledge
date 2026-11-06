"""Caller-owned, fenced and versioned checkpoints for bounded background work."""
from copy import deepcopy
import hashlib
import json

from app.db.models import WorkProgress
from .context import bind_session


def _identity(work, version):
    if type(version) is not int or not 1 <= version <= 2147483647:
        raise ValueError("Invalid progress version")
    spec = work.spec
    body = json.dumps([spec.kind, spec.payload_version, spec.doc_id, spec.payload], sort_keys=True,
        allow_nan=False, separators=(",", ":")).encode("utf8")
    return hashlib.sha256(body).hexdigest()


def _load(session, work, version):
    expected = _identity(work, version)
    bind_session(session, work)
    row = session.get(WorkProgress, work.work_id, populate_existing=True)
    if row is not None and (row.version != version or row.input_hash != expected):
        raise ValueError("Work progress version or input mismatch")
    return row, expected


def load_progress(session, work, *, version):
    row, _ = _load(session, work, version)
    return deepcopy(row.state_json) if row is not None else {}


def save_progress(session, work, *, version, state):
    if type(state) is not dict:
        raise ValueError("Work progress must be a bounded JSON object")
    body = json.dumps(state, sort_keys=True, allow_nan=False, separators=(",", ":"))
    if len(body.encode("utf8")) > 65536 or json.loads(body) != state:
        raise ValueError("Work progress exceeds canonical JSON limit")
    row, expected = _load(session, work, version)
    if row is None:
        row = WorkProgress(work_id=work.work_id, version=version, input_hash=expected, state_json=deepcopy(state))
        session.add(row)
    else:
        row.state_json = deepcopy(state)
    session.flush()
