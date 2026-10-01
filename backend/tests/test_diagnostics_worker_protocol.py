"""The child IPC rejects unbounded and out-of-order protocol data."""
from io import BytesIO
from uuid import uuid4
import pytest

from app.services.diagnostics.worker_protocol import FrameError, JobChannel, QuotaBroker
from app.services.diagnostics.schema import DiagnosticLimits
from app.services.diagnostics.store import DiagnosticStore


def test_frames_are_bounded_versioned_and_sequenced():
    job = str(uuid4())
    stream = BytesIO()
    sender = JobChannel(job)
    sender.send(stream, "phase", {"name": "prepare"})
    sender.send(stream, "phase", {"name": "zip"})
    stream.seek(0)
    receiver = JobChannel(job)
    assert receiver.receive(stream)["payload"]["name"] == "prepare"
    assert receiver.receive(stream)["payload"]["name"] == "zip"
    stream.seek(0)
    with pytest.raises(FrameError):
        receiver.receive(stream)
    with pytest.raises(FrameError):
        sender.send(BytesIO(), "unknown", {})
    with pytest.raises(FrameError):
        sender.send(BytesIO(), "phase", {"name": "x" * 70000})


def test_quota_credit_cannot_be_committed_twice_or_beyond_grant(tmp_path):
    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        broker = QuotaBroker(store)
        job = str(uuid4())
        output = store.root / "snapshots" / job / "prepared.jsonl"
        output.parent.mkdir()
        grant = broker.grant(job, 4096, output)
        output.write_bytes(b"x" * 1024)
        with pytest.raises(ValueError):
            broker.commit(grant, 4097)
        broker.commit(grant, 1024)
        with pytest.raises(ValueError):
            broker.commit(grant, 1)
        broker.release_job(job)
        assert store.reserved_bytes == 0


def test_chunked_prepare_round_trip_and_rejects_missing_chunk():
    payload = {"descriptors": ["x" * 1000 for _ in range(100)]}
    job = str(uuid4())
    stream = BytesIO()
    JobChannel(job).send_prepare(stream, payload)
    stream.seek(0)
    assert JobChannel(job).receive_prepare(stream) == payload
    raw = stream.getvalue()
    # Remove the final framed chunk: incomplete prepare is never executable.
    broken = BytesIO(raw[:-100])
    with pytest.raises(FrameError):
        JobChannel(job).receive_prepare(broken)


def test_aborted_worker_credit_accounts_surviving_uncommitted_output(tmp_path):
    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        broker = QuotaBroker(store)
        job = str(uuid4())
        output = store.root / "snapshots" / job / "prepared.jsonl"
        output.parent.mkdir()
        broker.grant(job, 4096, output)
        before = store.used_bytes
        output.write_bytes(b"x" * 1024)
        broker.release_job(job)
        assert store.used_bytes == before + 1024
        assert store.reserved_bytes == 0
        store.delete_tree(output.parent)
        assert store.used_bytes == before


def test_aborted_worker_retains_credit_when_remaining_file_cannot_be_measured(tmp_path, monkeypatch):
    from pathlib import Path

    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        broker = QuotaBroker(store)
        job = str(uuid4())
        output = store.root / "snapshots" / job / "prepared.jsonl"
        output.parent.mkdir()
        broker.grant(job, 4096, output)
        output.write_bytes(b"x" * 1024)
        real_stat = Path.stat

        def denied_stat(path, *args, **kwargs):
            if path == output:
                raise PermissionError("Synthetic measurement denial")
            return real_stat(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "stat", denied_stat)
            broker.release_job(job)
            assert store.reserved_bytes == 4096
            assert broker.outstanding(job) == 1
            assert store.status()["storage_degraded"]
        broker.release_job(job)
        assert store.reserved_bytes == 0
        assert store._index.size(output) == 1024
