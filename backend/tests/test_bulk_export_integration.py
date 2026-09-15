"""End-to-end lifecycle coverage for the isolated raw-export queue."""
from __future__ import annotations

from datetime import datetime, timezone

from app.config import Settings
from app.services.export_queue import ExportQueue
from app.services.registry import DocumentRegistry


class _Admin:
    user_id = "integration-admin"
    username = "integration.admin"


def test_export_lifecycle_submit_build_download_and_expire(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        bulk_export_part_size_mb=2,
        bulk_export_max_total_mb=4,
        bulk_export_min_free_mb=0,
    )
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    source = settings.uploads_dir / "0123456789abcdef.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"integration export")
    DocumentRegistry().create("0123456789abcdef", "integration.docx", "application/octet-stream", source.stat().st_size)
    queue = ExportQueue(start_worker=False)

    submitted = queue.submit(["0123456789abcdef"], _Admin())
    queue._execute(submitted["id"])
    ready = queue.get(submitted["id"])
    assert ready["status"] == "completed"
    lease = queue.acquire_download(submitted["id"], 1, _Admin(), None)
    assert lease.path.is_file()
    lease.release()
    cleanup = queue.cleanup_expired(datetime.max.replace(tzinfo=timezone.utc))
    assert cleanup.expired == 1
    assert queue.get(submitted["id"])["result"]["artifact_status"] == "expired"


def test_parallel_retrieval_cli_uses_public_function(tmp_path, monkeypatch):
    from test_scripts import probe_parallel_retrieval as probe

    calls = []
    output = tmp_path / "probe.json"
    monkeypatch.setattr(probe, "load_cases", lambda _path: [{"id": "case"}])
    monkeypatch.setattr(
        probe,
        "run_parallel_retrieval",
        lambda cases, *, state, clients, queries_per_client: calls.append(
            (cases, state, clients, queries_per_client)
        ) or {"requests": 1, "errors": 0, "timings": {"p95_ms": 1}},
    )
    monkeypatch.setattr(
        "sys.argv",
        ["probe_parallel_retrieval.py", "--output", str(output), "--clients", "2", "--queries-per-client", "3"],
    )

    assert probe.main() == 0
    assert [call[1:] for call in calls] == [("off", 2, 3), ("on", 2, 3)]
