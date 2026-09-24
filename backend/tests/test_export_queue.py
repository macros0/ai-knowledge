"""Admission control for raw-document export jobs."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import error_codes as codes
from app.config import Settings
from app.db.models import Job
from app.db.session import session_scope
from app.services import audit as audit_mod
from app.services.export_queue import (
    BULK_EXPORT,
    ExportAdmissionError,
    ExportAuditUnavailableError,
    ExportQueue,
)
from app.services.registry import DocumentRegistry


class _User:
    user_id = "export-admin-id"
    username = "export.admin"


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        bulk_export_max_docs=3,
        bulk_export_max_total_mb=1,
        bulk_export_part_size_mb=2,
        bulk_export_max_pending=3,
        bulk_export_max_active_per_user=1,
        bulk_export_max_ops_per_hour=3,
        bulk_export_max_retained_mb=8,
        bulk_export_min_free_mb=0,
    )


def _document(settings: Settings, doc_id: str, filename: str, payload: bytes = b"source") -> None:
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    (settings.uploads_dir / f"{doc_id}.docx").write_bytes(payload)
    DocumentRegistry().create(
        doc_id,
        filename,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        len(payload),
    )


def _seed_export_job(
    *,
    status: str,
    user_id: str = "other-admin-id",
    estimated_output_bytes: int = 0,
    total_bytes: int = 0,
) -> None:
    with session_scope() as s:
        s.add(
            Job(
                job_type=BULK_EXPORT,
                status=status,
                created_by_id=user_id,
                params={"estimated_output_bytes": estimated_output_bytes},
                result={"total_bytes": total_bytes},
            )
        )


@pytest.fixture
def queue(tmp_path, monkeypatch) -> tuple[ExportQueue, Settings]:
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    return ExportQueue(start_worker=False), settings


def test_submit_deduplicates_order_snapshots_sources_and_audits(queue):
    service, settings = queue
    first = "0123456789abcdef"
    second = "fedcba9876543210"
    _document(settings, first, "План.docx", b"one")
    _document(settings, second, "Отчёт.docx", b"two2")

    submitted = service.submit([second, first, second], _User(), ip_address="127.0.0.1")

    assert submitted["job_type"] == "bulk_export"
    assert submitted["status"] == "queued"
    assert submitted["params"]["documents"] == [
        {
            "doc_id": second,
            "filename": "Отчёт.docx",
            "source_basename": f"{second}.docx",
            "size_bytes": 4,
            "mtime_ns": pytest.approx(
                (settings.uploads_dir / f"{second}.docx").stat().st_mtime_ns
            ),
            "part_number": 1,
        },
        {
            "doc_id": first,
            "filename": "План.docx",
            "source_basename": f"{first}.docx",
            "size_bytes": 3,
            "mtime_ns": pytest.approx(
                (settings.uploads_dir / f"{first}.docx").stat().st_mtime_ns
            ),
            "part_number": 1,
        },
    ]
    assert str(settings.data_dir) not in str(submitted["params"])

    entries = audit_mod.get_audit().query(target_id=str(submitted["id"]))
    assert len(entries) == 1
    assert entries[0]["action_type"] == "document_bulk_export_requested"
    assert entries[0]["new_value"] == {
        "document_ids": [second, first],
        "document_count": 2,
        "total_source_bytes": 7,
        "estimated_output_bytes": 1048583,
        "part_count": 1,
        "limits": {
            "max_docs": 3,
            "max_total_bytes": 1048576,
            "part_size_bytes": 2097152,
            "retained_limit_bytes": 8388608,
            "min_free_bytes": 0,
        },
    }


def test_submit_rejects_deleted_or_missing_or_ambiguous_source_before_creating_job(queue):
    service, settings = queue
    deleted = "1111111111111111"
    _document(settings, deleted, "Удалён.docx")
    DocumentRegistry().update(deleted, deleted_at=datetime.now(timezone.utc))

    with pytest.raises(ValueError) as deleted_error:
        service.submit([deleted], _User())
    assert deleted_error.value.code == codes.BULK_EXPORT_SOURCE_CONFLICT

    valid = "2222222222222222"
    _document(settings, valid, "Два файла.docx")
    (settings.uploads_dir / f"{valid}.pdf").write_bytes(b"other")
    with pytest.raises(ValueError) as ambiguous_error:
        service.submit([valid], _User())
    assert ambiguous_error.value.code == codes.BULK_EXPORT_SOURCE_CONFLICT

    with session_scope() as s:
        assert s.query(Job).count() == 0


@pytest.mark.parametrize(
    ("doc_ids", "code"),
    [([], codes.EMPTY_DOCUMENT_LIST), (["not-a-document-id"], codes.BULK_EXPORT_SOURCE_CONFLICT)],
)
def test_submit_rejects_empty_or_invalid_document_ids_before_creating_job(queue, doc_ids, code):
    service, _settings = queue

    with pytest.raises(ExportAdmissionError) as error:
        service.submit(doc_ids, _User())
    assert error.value.code == code


def test_submit_uses_actual_source_size_not_stale_registry_size(queue):
    service, settings = queue
    doc_id = "8888888888888888"
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    (settings.uploads_dir / f"{doc_id}.docx").write_bytes(b"actual bytes")
    DocumentRegistry().create(doc_id, "Фактический.docx", "application/octet-stream", 1)

    submitted = service.submit([doc_id], _User())

    assert submitted["params"]["documents"][0]["size_bytes"] == len(b"actual bytes")


def test_submit_rejects_missing_source_and_document_count_limit(queue):
    service, settings = queue
    missing = "9999999999999999"
    DocumentRegistry().create(missing, "Нет.docx", "application/octet-stream", 0)
    with pytest.raises(ExportAdmissionError) as missing_error:
        service.submit([missing], _User())
    assert missing_error.value.code == codes.BULK_EXPORT_SOURCE_CONFLICT

    settings.bulk_export_max_docs = 1
    first = "aaaaaaaaaaaaaaaa"
    second = "bbbbbbbbbbbbbbbb"
    _document(settings, first, "Первый.docx")
    _document(settings, second, "Второй.docx")
    with pytest.raises(ExportAdmissionError) as count_error:
        service.submit([first, second], _User())
    assert count_error.value.code == codes.DOCUMENT_LIMIT_EXCEEDED


def test_requested_audit_failure_rolls_back_job(queue, monkeypatch):
    service, settings = queue
    doc_id = "3333333333333333"
    _document(settings, doc_id, "Аудит.docx")

    def unavailable(*args, **kwargs):
        raise RuntimeError("audit database unavailable")

    monkeypatch.setattr("app.services.export_queue.audit_mod.record_in_session", unavailable)

    with pytest.raises(ExportAuditUnavailableError) as error:
        service.submit([doc_id], _User())
    assert error.value.code == codes.BULK_EXPORT_AUDIT_UNAVAILABLE
    with session_scope() as s:
        assert s.query(Job).count() == 0


def test_submit_rechecks_active_export_capacity_inside_transaction(queue):
    service, settings = queue
    first = "6666666666666666"
    second = "7777777777777777"
    _document(settings, first, "Первый.docx")
    _document(settings, second, "Второй.docx")
    service.submit([first], _User())

    with pytest.raises(ValueError) as error:
        service.submit([second], _User())
    assert error.value.code == codes.BULK_EXPORT_USER_ACTIVE
    with session_scope() as s:
        assert s.query(Job).count() == 1


def test_submit_rechecks_queue_rate_and_storage_reservations(queue, monkeypatch):
    service, settings = queue
    doc_id = "cccccccccccccccc"
    _document(settings, doc_id, "Резерв.docx", b"x")
    settings.bulk_export_max_pending = 1
    _seed_export_job(status="queued", estimated_output_bytes=600)
    with pytest.raises(ExportAdmissionError) as queue_error:
        service.submit([doc_id], _User())
    assert queue_error.value.code == codes.BULK_EXPORT_QUEUE_FULL

    with session_scope() as s:
        for job in s.query(Job).all():
            job.status = "completed"
    settings.bulk_export_max_ops_per_hour = 1
    _seed_export_job(status="completed", user_id=_User.user_id)
    with pytest.raises(ExportAdmissionError) as rate_error:
        service.submit([doc_id], _User())
    assert rate_error.value.code == codes.BULK_EXPORT_RATE_LIMITED
    assert rate_error.value.retry_after_seconds == 3600

    with session_scope() as s:
        for job in s.query(Job).all():
            job.created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    settings.bulk_export_max_ops_per_hour = 3
    settings.bulk_export_max_retained_mb = 1
    _seed_export_job(status="ready", total_bytes=1024 * 1024)
    with pytest.raises(ExportAdmissionError) as quota_error:
        service.submit([doc_id], _User())
    assert quota_error.value.code == codes.BULK_EXPORT_STORAGE_QUOTA

    with session_scope() as s:
        for job in s.query(Job).all():
            job.status = "completed"
            job.result = {"total_bytes": 0}
    settings.bulk_export_max_pending = 3
    settings.bulk_export_max_retained_mb = 8
    _seed_export_job(status="queued", estimated_output_bytes=600)
    monkeypatch.setattr(
        "app.services.export_queue.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=10_000, used=8_500, free=1_500),
    )
    with pytest.raises(ExportAdmissionError) as reserve_error:
        service.submit([doc_id], _User())
    assert reserve_error.value.code == codes.BULK_EXPORT_STORAGE_RESERVE


def test_registry_export_metadata_is_minimal_and_preserves_missing_entries(queue):
    _service, settings = queue
    doc_id = "4444444444444444"
    _document(settings, doc_id, "Минимум.docx", b"12345")

    rows = DocumentRegistry().get_export_metadata_many([doc_id, "5555555555555555"])

    assert rows == {
        doc_id: {
            "id": doc_id,
            "filename": "Минимум.docx",
            "size": 5,
            "deleted_at": None,
        },
        "5555555555555555": None,
    }


def test_execute_publishes_all_parts_only_after_build_and_records_completion(queue):
    service, settings = queue
    doc_id = "dddddddddddddddd"
    _document(settings, doc_id, "Готовый.docx", b"export me")
    submitted = service.submit([doc_id], _User())

    service._execute(submitted["id"])

    ready_dir = settings.exports_dir / f"{submitted['id']}.ready"
    assert ready_dir.is_dir()
    assert not (settings.exports_dir / f"{submitted['id']}.building").exists()
    completed = service.get(submitted["id"])
    assert completed["status"] == "completed"
    assert completed["result"]["artifact_status"] == "available"
    assert completed["result"]["parts_completed"] == completed["result"]["parts_total"] == 1
    assert completed["result"]["parts"][0]["filename"].endswith(".zip")
    assert len(audit_mod.get_audit().query(target_id=str(submitted["id"]))) == 2


def test_execute_source_change_fails_without_leaving_ready_artifacts(queue):
    service, settings = queue
    doc_id = "eeeeeeeeeeeeeeee"
    source = settings.uploads_dir / f"{doc_id}.docx"
    _document(settings, doc_id, "Изменён.docx", b"before")
    submitted = service.submit([doc_id], _User())
    source.write_bytes(b"after and different")

    service._execute(submitted["id"])

    failed = service.get(submitted["id"])
    assert failed["status"] == "failed"
    assert failed["result"]["error_code"] == codes.BULK_EXPORT_SOURCE_CHANGED
    assert not (settings.exports_dir / f"{submitted['id']}.building").exists()
    assert not (settings.exports_dir / f"{submitted['id']}.ready").exists()


def test_execute_disk_full_fails_with_stable_code_and_removes_partial_artifacts(queue, monkeypatch):
    """A partial ZIP must never be retained or diagnosed as a changed source."""
    service, settings = queue
    doc_id = "efefefefefefefef"
    _document(settings, doc_id, "Заполненный-диск.docx", b"before")
    submitted = service.submit([doc_id], _User())
    building = settings.exports_dir / f"{submitted['id']}.building"

    def disk_full(*_args, **_kwargs):
        building.mkdir(parents=True, exist_ok=True)
        (building / "partial.zip").write_bytes(b"partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("app.services.export_queue.build_export_parts", disk_full)

    service._execute(submitted["id"])

    failed = service.get(submitted["id"])
    assert failed["status"] == "failed"
    assert failed["result"]["error_code"] == codes.STORAGE_FULL
    assert not building.exists()
    assert not (settings.exports_dir / f"{submitted['id']}.ready").exists()


def test_execute_records_failure_when_artifact_cleanup_is_full(queue, monkeypatch):
    """A second ENOSPC during cleanup must not kill the export worker or hide the first failure."""
    service, settings = queue
    doc_id = "f0f0f0f0f0f0f0f0"
    _document(settings, doc_id, "Очистка.docx", b"before")
    submitted = service.submit([doc_id], _User())

    monkeypatch.setattr(
        "app.services.export_queue.build_export_parts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(28, "No space left on device")),
    )
    monkeypatch.setattr(service, "_retire_artifacts", lambda *_args: (_ for _ in ()).throw(OSError(28, "No space left on device")))

    service._execute(submitted["id"])

    failed = service.get(submitted["id"])
    assert failed["status"] == "failed"
    assert failed["result"]["error_code"] == codes.STORAGE_FULL


def test_completion_audit_failure_never_leaves_downloadable_ready(queue, monkeypatch):
    service, settings = queue
    doc_id = "ffffffffffffffff"
    _document(settings, doc_id, "Аудит-завершение.docx", b"payload")
    submitted = service.submit([doc_id], _User())

    def unavailable(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.services.export_queue.audit_mod.record_in_session", unavailable)
    service._execute(submitted["id"])

    assert not (settings.exports_dir / f"{submitted['id']}.ready").exists()
    assert service.get(submitted["id"])["status"] != "completed"


def test_recovery_requeues_only_export_queued_and_retires_export_running(queue):
    service, settings = queue
    queued_id = "1212121212121212"
    running_id = "1313131313131313"
    _document(settings, queued_id, "Очередь.docx")
    _document(settings, running_id, "Перезапуск.docx")
    queued = service.submit([queued_id], _User())
    with session_scope() as s:
        running = Job(
            job_type=BULK_EXPORT,
            status="running",
            created_by_id=_User.user_id,
            created_by=_User.username,
            params=queued["params"] | {"documents": [queued["params"]["documents"][0] | {"doc_id": running_id}]},
        )
        s.add(running)
        s.flush()
        running_id = running.id
    stale_ready = settings.exports_dir / f"{running_id}.ready"
    stale_ready.mkdir(parents=True)
    (stale_ready / "stale.zip").write_bytes(b"stale")

    service.recover_after_restart()

    assert service._queue.get_nowait() == queued["id"]
    assert service.get(running_id)["status"] == "failed"
    assert service.get(running_id)["result"]["error_code"] == "job_interrupted"
    assert not stale_ready.exists()


def test_download_lease_blocks_expiry_until_idempotent_release(queue):
    service, settings = queue
    doc_id = "1414141414141414"
    _document(settings, doc_id, "Скачать.docx", b"download")
    submitted = service.submit([doc_id], _User())
    service._execute(submitted["id"])

    lease = service.acquire_download(submitted["id"], 1, _User(), "127.0.0.1")
    assert lease.path.is_file()
    assert lease.filename.endswith(".zip")
    assert service.active_leases(submitted["id"]) == 1
    result = service.cleanup_expired(now=datetime.max.replace(tzinfo=timezone.utc))
    assert result.skipped_leased == 1
    assert (settings.exports_dir / f"{submitted['id']}.ready").is_dir()

    lease.release()
    lease.release()
    assert service.active_leases(submitted["id"]) == 0
    result = service.cleanup_expired(now=datetime.max.replace(tzinfo=timezone.utc))
    assert result.expired == 1
    assert service.get(submitted["id"])["result"]["artifact_status"] == "expired"


def test_download_audit_failure_releases_lease_and_returns_no_path(queue, monkeypatch):
    service, settings = queue
    doc_id = "1515151515151515"
    _document(settings, doc_id, "Аудит-download.docx", b"payload")
    submitted = service.submit([doc_id], _User())
    service._execute(submitted["id"])

    monkeypatch.setattr(
        "app.services.export_queue.audit_mod.record_in_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )
    with pytest.raises(ExportAuditUnavailableError):
        service.acquire_download(submitted["id"], 1, _User(), "127.0.0.1")
    assert service.active_leases(submitted["id"]) == 0
    assert (settings.exports_dir / f"{submitted['id']}.ready").is_dir()


def test_manual_delete_is_idempotent_and_preserves_part_history(queue):
    service, settings = queue
    doc_id = "1616161616161616"
    _document(settings, doc_id, "Удалить.docx", b"payload")
    submitted = service.submit([doc_id], _User())
    service._execute(submitted["id"])
    original_parts = service.get(submitted["id"])["result"]["parts"]

    deleted = service.delete_artifacts(submitted["id"], _User(), "127.0.0.1")
    assert deleted["result"]["artifact_status"] == "deleted"
    assert deleted["result"]["parts"] == original_parts
    assert not (settings.exports_dir / f"{submitted['id']}.ready").exists()
    assert service.delete_artifacts(submitted["id"], _User(), "127.0.0.1")["id"] == submitted["id"]
