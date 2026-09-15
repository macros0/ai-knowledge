# Bulk Raw Document Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать администраторам аудируемую фоновую выгрузку до 1000 исходных документов общим объёмом до 1 ГиБ в ZIP-частях до 250 МиБ.

**Architecture:** Новый `ExportQueue` использует существующую таблицу `jobs`, но отдельный worker и отдельное владение `job_type=bulk_export`. Preflight выполняется до постановки задачи, части собираются без сжатия в `.building`, публикуются атомарным rename в `.ready`, скачиваются потоково и удаляются по TTL/квоте с обязательным аудитом.

**Tech Stack:** FastAPI/Starlette `FileResponse`, SQLAlchemy 2.0, Python `zipfile`, PostgreSQL/SQLite, React/Next.js App Router proxy, Node test runner, pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-bulk-raw-document-export-design.md`

**Baseline:** повторно проверено 2026-09-15: реализации `bulk_export` в backend/frontend нет; `JobQueue` по-прежнему выбирает queued/running Job без фильтра `job_type`, `/api/settings` не публикует export-capabilities, а тестовые probes уже перенесены в `backend/test_scripts/`.

## Global Constraints

- Экспортируются только исходники `data/uploads/<doc_id>.<ext>`; OKF, chunks и attachments не входят.
- Create/download/delete доступны только `require_role("admin")`.
- Лимиты по умолчанию: 1000 документов, 1 ГиБ суммарно, 250 МиБ на часть, 3 queued/running export, 1 active export на администратора, 3 создания в час.
- Один export-worker; он не делит очередь с bulk delete/regenerate.
- ZIP создаётся с `ZIP_STORED`, `allowZip64=True`; весь архив или файл не загружается в память.
- TTL 24 часа, ready-квота 5 ГиБ, резерв свободного места после оценки 2 ГиБ.
- Job transition и обязательный audit append выполняются одной SQL-транзакцией.
- При сбое обязательного audit-write создание/публикация/начало скачивания не продолжаются.
- Не создавать worktree, ветку, commit, merge или PR: сохранять текущий dirty `main`; после каждой задачи обновлять чекбоксы этого плана только после свежей проверки.
- Backend-тесты запускать из `backend` с workspace-local `TEMP`/`TMP`, отдельным `--basetemp` и `-p no:cacheprovider`.
- Новая миграция БД не создаётся: `jobs` и `audit_log` уже имеют строковые/JSON-поля нужной формы.
- Job `params`/`result`, API-ответы, аудит и пользовательские ошибки не содержат абсолютных серверных путей или traceback; техническая причина остаётся только в server log.
- Preflight резервирует место не только под новую заявку, но и под оценки всех queued/running export Job, чтобы три допустимые заявки не переобещали одну и ту же квоту/свободное место.
- Worker повторно проверяет имя, containment, размер и `mtime_ns` исходника непосредственно перед и после потоковой записи; изменение исходника после preflight проваливает весь export без публикации частичного результата.
- В v1 `bulk_export` не поддерживает approve/cancel через общий JobQueue: UI скрывает эти действия, а backend отклоняет межочередную мутацию. Откат и удаление артефактов имеют отдельные управляемые пути.
- Cleanup запускается при recovery, после каждой export-задачи и не реже одного раза в 5 минут во время простоя worker.

## State and audit contracts

| Job status | `result.artifact_status` | Filesystem | Download | Recovery action |
|---|---|---|---|---|
| `queued` | absent | no directory | no | requeue; retire any unexpected artifact |
| `running` | absent/building progress | `<id>.building` | no | retire artifacts, transactionally fail + audit |
| `completed` | `available` | `<id>.ready` | yes, while unexpired and enabled | keep; expiry cleanup owns it |
| `completed` | `expired` | absent or `<id>.deleting` retry | `410` | retry physical delete only |
| `completed` | `deleted` | absent or `<id>.deleting` retry | `410` | retry physical delete only |
| `failed` | absent | absent or `<id>.deleting` retry | no | retry physical delete only |

The only publication path is `<id>.building -> <id>.ready -> completed/available`. The download gate requires all three signals: completed Job, available metadata, and the canonical ready file. A directory name alone never grants access.

| Audit action | Actor | Required payload |
|---|---|---|
| `document_bulk_export_requested` | requesting admin | ordered `doc_ids`, count, source bytes, estimated bytes/parts, effective limits |
| `document_bulk_export_completed` | requesting admin | job ID, counts, source/output bytes, path-free parts, expiry |
| `document_bulk_export_failed` | requesting admin or rollback system actor | job ID, stable reason code, processed/total counts |
| `document_bulk_export_download_started` | downloading admin | job ID, part number, filename, bytes |
| `document_bulk_export_expired` | `SystemUser` | job ID, part count, bytes, expiry |
| `document_bulk_export_deleted` | deleting admin or rollback system actor | job ID, part count, bytes, reason |

## Progress

- [x] Task 1 — Configuration and queue ownership (2026-09-15: `47 passed` in `test_settings.py` + `test_jobs.py`)
- [x] Task 2 — Pure export planning and ZIP builder (2026-09-15: `7 passed`; targeted Ruff clean)
- [x] Task 3 — Admission and transactional submission (2026-09-15: `24 passed`; targeted Ruff clean)
- [x] Task 4 — Dedicated worker and crash recovery (2026-09-15: `32 passed`; targeted Ruff clean)
- [x] Task 5 — Download leases, expiry, and deletion (2026-09-15: `17 passed`; targeted Ruff clean)
- [x] Task 6 — Admin-only HTTP API and audit contract (2026-09-15: focused backend evidence `134 passed`; role matrix, error headers, feature flags, streaming download/lease release, delete and shared Job visibility covered)
- [x] Task 7 — Selection limits and export UI (2026-09-15: `35 passed` in bulk-limit/i18n tests; ESLint clean)
- [x] Task 8 — Admin/Security panels and i18n (2026-09-15: `158 passed`; ESLint clean; generated catalogs current)
- [x] Task 9 — Integrated resilience and load acceptance (2026-09-15: integration/API/queue `21 passed`; 1000 files / 734003200 bytes benchmark passed: 3 parts, CRC clean, RSS +4349952 bytes, 100+100 retrieval requests, full overlap)
- [x] Task 10 — Safe rollback command (2026-09-15: `7 passed`; dry-run, exact confirmation, active lease refusal, queued/running and ready artifact retirement, audit rollback and repeated apply covered)
- [x] Task 11 — Operations documentation and final verification (2026-09-15: Ruff clean; focused backend `134 passed`; frontend `158 passed`; full backend regression `1551 passed, 5 skipped`; benchmark acceptance passed; `git diff --check` clean)

---

### Task 1: Configuration and queue ownership

**Files:**
- Modify: `backend/app/config.py:241-274`
- Modify: `backend/app/models/schemas.py:211-224`
- Modify: `backend/app/api/settings.py:11-26`
- Modify: `backend/app/services/job_queue.py:33-45,98-135,139-217,219-267,282-325`
- Modify: `backend/tests/test_settings.py`
- Modify: `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: existing `Settings`, `Job`, `JobQueue`, and `JOB_TYPES`.
- Produces: exact `bulk_export_*` settings, non-secret UI capability fields, and a `JobQueue` that mutates/recovers/counts/executes only its owned types while keeping read-only `get()`/`list()` shared for AdminPanel.

- [ ] **Step 1: Write failing settings tests**

Add assertions for exact defaults and bounds:

```python
def test_bulk_export_defaults(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.bulk_export_enabled is True
    assert s.bulk_export_download_enabled is True
    assert s.bulk_export_max_docs == 1000
    assert s.bulk_export_max_total_mb == 1024
    assert s.bulk_export_part_size_mb == 250
    assert s.bulk_export_max_pending == 3
    assert s.bulk_export_max_active_per_user == 1
    assert s.bulk_export_max_ops_per_hour == 3
    assert s.bulk_export_ttl_hours == 24
    assert s.bulk_export_max_retained_mb == 5120
    assert s.bulk_export_min_free_mb == 2048
    assert s.exports_dir == tmp_path / "exports"

def test_settings_endpoint_exposes_only_export_capabilities(client):
    data = client.get("/api/settings").json()
    assert data["bulk_export_enabled"] is True
    assert data["bulk_export_download_enabled"] is True
    assert data["bulk_export_max_docs"] == 1000
    assert "bulk_export_max_retained_mb" not in data
```

- [ ] **Step 2: Write a failing recovery-ownership regression test**

Seed queued/running `bulk_export` Jobs, call `JobQueue(start_worker=False).recover_after_restart()`, and assert they remain unchanged while the regular queue stays empty. Also assert that `cancel()` rejects an export Job with `JOB_NOT_CANCELLABLE`, `approve()` rejects it with `JOB_NOT_AWAITING_APPROVAL`, and `_execute()` cannot run it even if its ID is injected into the in-memory queue.

```python
def test_regular_queue_recovery_ignores_bulk_export(isolated_db):
    export_id = seed_job("bulk_export", "queued")
    q = JobQueue(start_worker=False)
    q.recover_after_restart()
    assert q.get(export_id)["status"] == "queued"
    assert q._queue.empty()

def test_regular_queue_cannot_cancel_export(isolated_db):
    export_id = seed_job("bulk_export", "queued")
    with pytest.raises(ConflictError) as exc:
        JobQueue(start_worker=False).cancel(export_id, ADMIN)
    assert exc.value.code == codes.JOB_NOT_CANCELLABLE
```

- [ ] **Step 3: Run the focused tests and confirm RED**

Run from `backend`:

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task1'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_settings.py tests/test_jobs.py -p no:cacheprovider --basetemp "$t\base" -q
```

Expected: failures for missing settings and export jobs being captured by regular recovery.

- [ ] **Step 4: Add settings and isolate JobQueue ownership**

Add Pydantic fields with positive bounds and filter all regular queue DB queries:

```python
bulk_export_enabled: bool = True
bulk_export_download_enabled: bool = True
bulk_export_max_docs: int = Field(default=1000, ge=1, le=10_000)
bulk_export_max_total_mb: int = Field(default=1024, ge=1)
bulk_export_part_size_mb: int = Field(default=250, ge=1)
bulk_export_max_pending: int = Field(default=3, ge=1, le=100)
bulk_export_max_active_per_user: int = Field(default=1, ge=1, le=10)
bulk_export_max_ops_per_hour: int = Field(default=3, ge=1, le=100)
bulk_export_ttl_hours: int = Field(default=24, ge=1, le=168)
bulk_export_max_retained_mb: int = Field(default=5120, ge=1)
bulk_export_min_free_mb: int = Field(default=2048, ge=0)

@property
def exports_dir(self) -> Path:
    return self.data_dir / "exports"
```

In `recover_after_restart()` and `pending_count()` add `Job.job_type.in_(JOB_TYPES)`. In `_execute()` return unless `job["job_type"] in JOB_TYPES`. In `approve()`/`cancel()` validate ownership before changing state. Extend `ChatSettingsOut` and `/api/settings` with only `bulk_export_enabled`, `bulk_export_download_enabled`, and `bulk_export_max_docs`; quota/free-space/rate-limit internals stay server-side.

- [ ] **Step 5: Run focused tests and record the checkpoint**

Repeat the Task 1 command; expected: PASS. Inspect `git diff -- backend/app/config.py backend/app/services/job_queue.py backend/tests/test_settings.py backend/tests/test_jobs.py`, then mark Task 1 complete in `Progress`.

---

### Task 2: Pure export planning and ZIP builder

**Files:**
- Create: `backend/app/services/bulk_export.py`
- Create: `backend/tests/test_bulk_export.py`

**Interfaces:**
- Consumes: filesystem Paths and exact byte limits from Task 1.
- Produces:
  - `ExportDocument(doc_id: str, filename: str, path: Path, size_bytes: int, mtime_ns: int)`
  - `ExportPart(number: int, documents: tuple[ExportDocument, ...], source_bytes: int)`
  - `BuiltExportPart(number: int, path: Path, filename: str, size_bytes: int, document_count: int)`
  - `safe_archive_filename(filename: str) -> str`
  - `partition_documents(documents: Sequence[ExportDocument], payload_limit_bytes: int) -> list[ExportPart]`
  - `build_export_parts(job_id: int, parts: Sequence[ExportPart], building_dir: Path, *, archive_limit_bytes: int, on_part_built: Callable[[BuiltExportPart], None] | None = None) -> list[BuiltExportPart]`
  - `ExportSourceChangedError` and `ExportPartTooLargeError`, internal builder failures mapped by ExportQueue to stable public error codes.

- [ ] **Step 1: Write failing unit tests for sanitization and partitioning**

Cover preserved order, exact boundary, 1 MiB metadata reserve, oversize single file, duplicate filenames, Windows paths, `..`, control characters, empty basename fallback, and deterministic UTF-8 names.

```python
def test_partition_preserves_request_order(tmp_path):
    docs = tuple(make_doc(tmp_path, i, size) for i, size in enumerate((4, 6, 2)))
    parts = partition_documents(docs, payload_limit_bytes=10)
    assert [[d.doc_id for d in p.documents] for p in parts] == [[docs[0].doc_id, docs[1].doc_id], [docs[2].doc_id]]

def test_safe_name_discards_client_path():
    assert safe_archive_filename(r"C:\\temp\\report.docx") == "report.docx"
    assert safe_archive_filename("../report.docx") == "report.docx"
    assert safe_archive_filename("..") == "document.bin"
```

- [ ] **Step 2: Write a failing ZIP contract test**

Build two parts and assert `ZIP_STORED`, safe root-level names (duplicate names receive `__<doc_id>` before the extension), `_manifest.json`, exact order, counts, CRC validity, and no absolute server path in names or manifest. Add a boundary case with many long UTF-8 names and assert the actual `stat().st_size` never exceeds `archive_limit_bytes`. Add source-drift cases (size/mtime changed before write and file changed during write) and assert the builder raises `ExportSourceChangedError` and leaves no valid result list.

```python
with zipfile.ZipFile(result[0].path) as zf:
    info = zf.getinfo("report.docx")
    assert info.compress_type == zipfile.ZIP_STORED
    assert zf.testzip() is None
    manifest = json.loads(zf.read("_manifest.json"))
    assert manifest["parts_total"] == 2
assert all(part.size_bytes <= ARCHIVE_LIMIT for part in result)
```

- [ ] **Step 3: Run the focused test and confirm RED**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task2'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_bulk_export.py -p no:cacheprovider --basetemp "$t\base" -q
```

Expected: import failure for `app.services.bulk_export`.

- [ ] **Step 4: Implement the pure builder**

Use frozen dataclasses, sequential 1-MiB buffered reads, basename-only sanitization, `ZIP_STORED`, and safe metadata returned without server paths. Do not use `Path.read_bytes()` or `ZipFile.writestr()` for document payloads. Open each source once, compare `os.fstat()` with the preflight fingerprint before and after copying, and stream into `ZipFile.open(ZipInfo(...), "w")`; this keeps memory bounded and detects source mutation. Manifest bytes may use `writestr()` because they are bounded metadata.

```python
@dataclass(frozen=True)
class ExportDocument:
    doc_id: str
    filename: str
    path: Path
    size_bytes: int
    mtime_ns: int

def build_export_parts(
    job_id: int,
    parts: Sequence[ExportPart],
    building_dir: Path,
    *,
    archive_limit_bytes: int,
    on_part_built: Callable[[BuiltExportPart], None] | None = None,
) -> list[BuiltExportPart]:
    building_dir.mkdir(parents=True, exist_ok=False)
    outputs = []
    for part in parts:
        name = f"documents-export-{job_id}-part-{part.number:03d}-of-{len(parts):03d}.zip"
        with zipfile.ZipFile(building_dir / name, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for doc in part.documents:
                _write_verified_document(zf, doc)
            zf.writestr("_manifest.json", manifest_bytes(job_id, part, len(parts)))
        path = building_dir / name
        built = BuiltExportPart(part.number, path, name, path.stat().st_size, len(part.documents))
        if built.size_bytes > archive_limit_bytes:
            raise ExportPartTooLargeError(name)
        outputs.append(built)
        if on_part_built is not None:
            on_part_built(built)
    return outputs
```

- [ ] **Step 5: Verify and mark the checkpoint**

Repeat the Task 2 command; expected: PASS. Inspect the two-file diff and mark Task 2 complete.

---

### Task 3: Admission and transactional submission

**Files:**
- Create: `backend/app/services/export_queue.py`
- Modify: `backend/app/services/registry.py:173-213`
- Modify: `backend/app/services/audit.py:21-121`
- Modify: `backend/app/error_codes.py`
- Create: `backend/tests/test_export_queue.py`
- Modify: `backend/tests/test_audit.py`

**Interfaces:**
- Consumes: Task 1 settings and Task 2 dataclasses/partitioner.
- Produces:
  - `BULK_EXPORT = "bulk_export"`
  - `ExportQueue(start_worker: bool = True)`
  - `ExportQueue.submit(doc_ids: list[str], user, ip_address: str | None) -> dict`
  - `get_export_queue() -> ExportQueue`
  - `DocumentRegistry.get_export_metadata_many(doc_ids) -> dict[str, dict | None]`
  - `ExportPlan.to_job_params(ip_address: str | None) -> dict` and `ExportPlan.audit_value() -> dict`.
  - `ExportAdmissionError(DomainError)` and `ExportAuditUnavailableError(DomainError)` carrying one of the stable codes below.
  - stable codes `BULK_EXPORT_DISABLED`, `BULK_EXPORT_SOURCE_CONFLICT`, `BULK_EXPORT_SOURCE_CHANGED`, `BULK_EXPORT_SIZE_LIMIT`, `BULK_EXPORT_USER_ACTIVE`, `BULK_EXPORT_QUEUE_FULL`, `BULK_EXPORT_RATE_LIMITED`, `BULK_EXPORT_STORAGE_QUOTA`, `BULK_EXPORT_STORAGE_RESERVE`, `BULK_EXPORT_AUDIT_UNAVAILABLE`, `BULK_EXPORT_NOT_READY`, `BULK_EXPORT_GONE`, and `BULK_EXPORT_PART_NOT_FOUND`.

- [ ] **Step 1: Add failing preflight tests**

Test empty, invalid 16-hex ID, dedup preserving order, 1000/1001, deleted document, zero/multiple source matches, symlink/junction escape, real stat size instead of DB size, >250 MiB single file, >1 GiB total, one active per user, three jobs/hour, pending cap, retained quota, and free-space reserve. Add two queued reservations and prove the third admission counts both `params.estimated_output_bytes`; monkeypatch `shutil.disk_usage` and byte settings to small values, never allocate GiB test files.

```python
def test_submit_is_atomic_with_requested_audit(queue, admin, monkeypatch):
    monkeypatch.setattr("app.services.audit.record_in_session", raising_audit)
    with pytest.raises(RuntimeError):
        queue.submit([DOC_ID], admin)
    assert jobs_for("bulk_export") == []

def test_pending_jobs_reserve_storage(queue, admin, monkeypatch):
    seed_export_job("queued", estimated_output_bytes=600)
    seed_export_job("running", estimated_output_bytes=600)
    monkeypatch.setattr(queue, "_ready_bytes", lambda: 100)
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=10_000, used=0, free=1_500),
    )
    with pytest.raises(ExportAdmissionError) as exc:
        queue.submit([DOC_ID], admin)
    assert exc.value.code == codes.BULK_EXPORT_STORAGE_RESERVE
```

- [ ] **Step 2: Add audit constants and contract tests**

Add all six exact actions to `ACTION_TYPES` and the exact-set test. `failed` stores a stable reason code and counts, not `repr(exc)`, absolute paths, or traceback:

```python
DOCUMENT_BULK_EXPORT_REQUESTED = "document_bulk_export_requested"
DOCUMENT_BULK_EXPORT_COMPLETED = "document_bulk_export_completed"
DOCUMENT_BULK_EXPORT_FAILED = "document_bulk_export_failed"
DOCUMENT_BULK_EXPORT_DOWNLOAD_STARTED = "document_bulk_export_download_started"
DOCUMENT_BULK_EXPORT_EXPIRED = "document_bulk_export_expired"
DOCUMENT_BULK_EXPORT_DELETED = "document_bulk_export_deleted"
```

- [ ] **Step 3: Run focused tests and confirm RED**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task3'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_export_queue.py tests/test_audit.py -p no:cacheprovider --basetemp "$t\base" -q
```

- [ ] **Step 4: Implement batch metadata and preflight**

`get_export_metadata_many` must select only `Document.id`, `filename`, `size`, and `deleted_at`. `ExportQueue._preflight` resolves `<doc_id>.*`, excludes directories, verifies `Path.is_relative_to(uploads_root)`, stats actual bytes and `st_mtime_ns`, computes parts, active-user count, rolling-hour count, pending cap, ready bytes, queued/running reservations, and `Retry-After`. All filesystem/DB-dependent admission runs under one process-wide `RLock`; immediately before commit, repeat counts, reserved bytes and disk-free checks under that same lock.

```python
@dataclass(frozen=True)
class ExportPlan:
    documents: tuple[ExportDocument, ...]
    parts: tuple[ExportPart, ...]
    total_bytes: int
    estimated_output_bytes: int

def _preflight(self, doc_ids: list[str], user_id: str) -> ExportPlan:
    ids = list(dict.fromkeys(doc_ids))
    metadata = self.registry.get_export_metadata_many(ids)
    # Validate every id and source before returning a complete immutable plan.
```

`ExportPlan.to_job_params()` serializes only doc ID, safe original filename, source basename, size, `mtime_ns`, estimated bytes, part allocation and request IP. It must not serialize `Path`. Worker-side rehydration rebuilds every path below the configured uploads root and repeats containment/fingerprint checks.

Convert configured MiB values with `value * 1024 * 1024`. Partition with `payload_limit_bytes = part_size_bytes - 1024 * 1024`; reject a single source that cannot fit that payload budget. Set `estimated_output_bytes = total_bytes + len(parts) * 1024 * 1024`, then require both `(ready_actual_bytes + pending_reserved_bytes + estimated_output_bytes) <= retained_limit_bytes` and `(disk_free_bytes - pending_reserved_bytes - estimated_output_bytes) >= min_free_bytes`.

- [ ] **Step 5: Implement transactional Job + requested audit**

Under the ExportQueue admission lock, repeat DB-dependent quota checks, create/flush Job, call `record_in_session()` with the full ordered doc list, byte counts, estimated parts, and effective limits, commit via `session_scope`, then enqueue only after commit. Translate an audit persistence failure into `ExportAuditUnavailableError(code=BULK_EXPORT_AUDIT_UNAVAILABLE)` without leaving a Job row.

```python
with session_scope() as session:
    job = Job(job_type=BULK_EXPORT, status=STATUS_QUEUED, created_by_id=user.user_id,
              created_by=user.username, params=plan.to_job_params(ip_address))
    session.add(job)
    session.flush()
    audit.record_in_session(session, action_type=audit.DOCUMENT_BULK_EXPORT_REQUESTED,
                            user_id=user.user_id, username=user.username,
                            target_type=audit.TARGET_JOB, target_id=str(job.id),
                            new_value=plan.audit_value(), ip_address=ip_address)
```

- [ ] **Step 6: Verify and mark the checkpoint**

Repeat the Task 3 command; expected: PASS, including zero Job rows after audit failure. Mark Task 3 complete.

---

### Task 4: Dedicated worker and crash recovery

**Files:**
- Modify: `backend/app/services/export_queue.py`
- Modify: `backend/app/main.py:130-242`
- Modify: `backend/tests/test_export_queue.py`
- Modify: `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: committed export Jobs and Task 2 builder.
- Produces: `_execute(job_id)`, `recover_after_restart()`, `run_maintenance_once()`, `shutdown(timeout_seconds=5.0)`, `shutdown_export_queue_if_started(timeout_seconds=5.0)`, part-level progress, atomic ready publication, completed/failed audit transitions, and `_reset_export_queue_for_tests()`.

- [ ] **Step 1: Write failing execution/recovery tests**

Cover `.building -> .ready`, all-part publication, source change after admission, builder failure cleanup, completion audit atomicity, audit failure retiring ready output to `.deleting`, queued export recovery, running export failure recovery, stale `.building`, retry of `.deleting`, unknown/orphan `.ready`, regular/export queues not stealing each other's jobs, cleanup on idle timeout, and graceful worker shutdown without leaked threads.

```python
def test_ready_is_not_visible_until_every_part_exists(queue, seeded_job, monkeypatch):
    seen = []
    monkeypatch.setattr(queue, "_publish", lambda *args: seen.append("publish"))
    queue._execute(seeded_job)
    assert seen == ["publish"]
    assert queue.get(seeded_job)["status"] == "completed"

def test_completion_audit_failure_never_leaves_downloadable_ready(queue, seeded_job, monkeypatch):
    monkeypatch.setattr(audit, "record_in_session", raising_audit)
    queue._execute(seeded_job)
    assert not queue.ready_dir(seeded_job).exists()
    assert queue.get(seeded_job)["status"] != "completed"
```

- [ ] **Step 2: Run focused tests and confirm RED**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task4'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_export_queue.py tests/test_jobs.py -p no:cacheprovider --basetemp "$t\base" -q
```

- [ ] **Step 3: Implement worker transitions**

Use one daemon thread, `queue.Queue[int | object]`, a private stop sentinel, and `queue.get(timeout=300)`. On timeout call `run_maintenance_once()`; also call it after every job. Update Job progress after each completed part through the builder callback. Rename only after all ZIP files exist and CRC/size verification passes. Complete Job and audit in one transaction. If that transaction fails, atomically rename `.ready` to `.deleting`, attempt physical deletion, and leave the Job recoverable as running; never leave a directory named `.ready` for a non-completed Job.

Running progress assigns a fresh JSON dict on every SQLAlchemy update (`processed`, `total`, `parts_completed`, `parts_total`); never mutate `job.result` in place. Completion sets `result.expires_at` to a UTC ISO-8601 string equal to `finished_at + ttl`, `result.artifact_status="available"`, source/output bytes, and path-free part metadata. Failure sets a stable public `job.error` plus `result.error_code`; the exception and traceback are logged server-side only.

```python
def _finish_completed(self, job_id: int, result: dict) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        job.status = STATUS_COMPLETED
        job.finished_at = _utcnow()
        job.result = result
        audit.record_in_session(session, action_type=audit.DOCUMENT_BULK_EXPORT_COMPLETED,
                                user_id=job.created_by_id, username=job.created_by,
                                target_type=audit.TARGET_JOB, target_id=str(job.id),
                                new_value=completion_audit_value(result))
```

- [ ] **Step 4: Implement owned recovery and startup wiring**

Recovery queries only `Job.job_type == BULK_EXPORT`. It requeues export `queued`; for export `running`, it retires `.building`/`.ready`, then atomically marks the Job failed with `document_bulk_export_failed`. It also removes unknown `.building`, retries every `.deleting`, and retires an orphan `.ready` whose Job is missing/not completed. Unknown artifact cleanup is security-logged with directory basename and byte count, never an absolute path. It never touches delete/regenerate Jobs.

Wire startup after regular `JobQueue.recover_after_restart()`. Structure lifespan teardown as `try: yield` / `finally: shutdown_export_queue_if_started()` so a failed startup does not instantiate a worker during teardown. `shutdown()` stops accepting new submissions, signals the sentinel, waits only the bounded timeout, and leaves an in-flight build recoverable if the process must exit. `_reset_export_queue_for_tests()` first shuts down the existing singleton and only then clears it.

- [ ] **Step 5: Verify and mark the checkpoint**

Repeat the Task 4 command. Expected: both cross-ownership tests PASS and no `.building` remains after failure/recovery. Mark Task 4 complete.

---

### Task 5: Download leases, expiry, and deletion

**Files:**
- Modify: `backend/app/services/export_queue.py`
- Modify: `backend/tests/test_export_queue.py`

**Interfaces:**
- Produces:
  - `ExportDownloadLease(path: Path, filename: str, size_bytes: int, release: Callable[[], None])`
  - `ExportCleanupResult(expired: int, skipped_leased: int, delete_retries: int, errors: tuple[str, ...])`
  - `ExportQueue.acquire_download(job_id, part_number, user, ip_address) -> ExportDownloadLease`
  - `ExportQueue.delete_artifacts(job_id, user, ip_address, reason="deleted") -> dict`
  - `ExportQueue.cleanup_expired(now: datetime | None = None) -> ExportCleanupResult`
  - artifact transitions `available -> deleting -> expired|deleted`; `.deleting` is never downloadable.

- [ ] **Step 1: Write failing lease and cleanup tests**

Test completed/available/non-expired access, part-number lookup, canonical generated filename, rebuilt safe path and containment, on-disk size match, active lease blocking cleanup, idempotent release, release enabling cleanup, `.ready -> .deleting`, DB/audit rollback restoring `.ready`, physical-delete retry, expiry metadata preservation, manual-delete idempotency, and mandatory download audit. Freeze time around `expires_at` and test the exact boundary (`now >= expires_at` means gone).

```python
def test_download_audit_failure_releases_lease_and_returns_no_path(queue, ready_job, monkeypatch):
    monkeypatch.setattr("app.services.audit.record_in_session", raising_audit)
    with pytest.raises(RuntimeError):
        queue.acquire_download(ready_job, 1, ADMIN, "127.0.0.1")
    assert queue.active_leases(ready_job) == 0
```

- [ ] **Step 2: Run focused tests and confirm RED**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task5'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_export_queue.py -p no:cacheprovider --basetemp "$t\base" -q
```

- [ ] **Step 3: Implement leases and mandatory download-start audit**

Under one reentrant lock, validate Job type/status, `artifact_status`, expiry, part metadata, canonical path/filename, containment and actual file size; increment `(job_id, part_number)` lease count; append audit in a DB transaction; decrement on failure. Return a closure guarded by a local boolean/lock so duplicate BackgroundTask/failure cleanup cannot decrement twice. Audit `new_value` contains `part_number`, filename, byte size and export count, never a path.

- [ ] **Step 4: Implement expiry/manual deletion state machine**

Rename to `.deleting` before DB transition; audit and set `artifact_status` in one transaction; restore `.ready` on rollback; delete `.deleting` after commit. `reason="expired"` accepts only `SystemUser` and emits `document_bulk_export_expired`; manual admin deletion emits `document_bulk_export_deleted`. Cleanup skips any Job with at least one active part lease, retries orphan `.deleting` directories without making them downloadable, and returns structured counts (`expired`, `skipped_leased`, `delete_retries`, `errors`) for logs/tests rather than only a total.

- [ ] **Step 5: Verify and mark the checkpoint**

Repeat the Task 5 command; expected: PASS. Mark Task 5 complete.

---

### Task 6: Admin-only HTTP API and audit contract

**Files:**
- Modify: `backend/app/api/documents.py:831-964`
- Modify: `backend/app/api/jobs.py`
- Modify: `backend/app/models/schemas.py:401-430`
- Modify: `backend/app/error_codes.py`
- Create: `backend/tests/test_bulk_export_api.py`
- Modify: `backend/tests/test_authz.py`
- Modify: `backend/tests/test_error_headers.py`
- Modify: `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: `get_export_queue()` and lease methods.
- Produces:
  - `POST /api/documents/bulk-export`
  - `GET /api/jobs/{job_id}/export/{part_number}`
  - `DELETE /api/jobs/{job_id}/export`
  - unchanged shared `GET /api/jobs` and `GET /api/jobs/{job_id}` visibility for export Job metadata.

- [ ] **Step 1: Write the role matrix and response-contract tests**

Parametrize viewer/editor/security/admin for all three endpoints. Assert admin create returns 202; download has ZIP/content-length/content-disposition/no-store/nosniff; delete preserves Job and historical part metadata. Assert exact mappings: malformed/empty input `400`; source/active conflict `409`; count/file/total limit `413`; rate limit `429` with integer `Retry-After`; queue or mandatory audit unavailable `503`; quota/reserve `507`; expired/deleted `410`. Assert both feature flags return `404/BULK_EXPORT_DISABLED` on their gated endpoint without changing Job/audit/files.

```python
@pytest.mark.parametrize("username,expected", [
    ("demo.user", 403), ("demo.editor", 403),
    ("demo.security", 403), ("demo.admin", 202),
])
def test_bulk_export_create_role_gate(client, username, expected):
    login(client, username)
    assert client.post("/api/documents/bulk-export", json={"doc_ids": [DOC_ID]}).status_code == expected
```

- [ ] **Step 2: Run focused API tests and confirm RED**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task6'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_bulk_export_api.py tests/test_authz.py tests/test_error_headers.py -p no:cacheprovider --basetemp "$t\base" -q
```

- [ ] **Step 3: Implement create endpoint and error mapping**

Use `status_code=202`, `require_role("admin")`, the existing request schema, and one explicit `EXPORT_ERROR_STATUS` mapping keyed by the stable codes from Task 3. Do not fall back every export error to 400. `BULK_EXPORT_ENABLED=false` returns 404 without creating Job/audit. Unexpected exceptions are handled by the existing catch-all middleware and must not expose paths.

```python
EXPORT_ERROR_STATUS = {
    errors.BULK_EXPORT_DISABLED: 404,
    errors.EMPTY_DOCUMENT_LIST: 400,
    errors.INVALID_REQUEST: 400,
    errors.BULK_EXPORT_SOURCE_CONFLICT: 409,
    errors.BULK_EXPORT_USER_ACTIVE: 409,
    errors.BULK_EXPORT_SIZE_LIMIT: 413,
    errors.BULK_EXPORT_RATE_LIMITED: 429,
    errors.BULK_EXPORT_QUEUE_FULL: 503,
    errors.BULK_EXPORT_AUDIT_UNAVAILABLE: 503,
    errors.BULK_EXPORT_STORAGE_QUOTA: 507,
    errors.BULK_EXPORT_STORAGE_RESERVE: 507,
    errors.BULK_EXPORT_NOT_READY: 409,
    errors.BULK_EXPORT_GONE: 410,
    errors.BULK_EXPORT_PART_NOT_FOUND: 404,
}
```

- [ ] **Step 4: Implement download/delete endpoints**

Download acquires a lease only after admin authorization and returns:

```python
lease = get_export_queue().acquire_download(job_id, part_number, user, _client_ip(request))
try:
    return FileResponse(
        lease.path,
        media_type="application/zip",
        filename=lease.filename,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(lease.release),
    )
except Exception:
    lease.release()
    raise
```

`BULK_EXPORT_DOWNLOAD_ENABLED=false` blocks GET but not cleanup/delete. Keep direct `FileResponse`; never read bytes in the handler. General approve/cancel endpoints must return `409` for `bulk_export`, while list/get continue returning it for AdminPanel.

- [ ] **Step 5: Verify audit fail-closed behavior**

Add a test that forces download audit insertion to fail and asserts 503 plus zero response bytes, no leaked path, and lease count back at zero. Add a response-disconnect/background-callback test proving release is idempotent. Repeat the Task 6 command; expected: PASS. Mark Task 6 complete.

---

### Task 7: Selection limits and export UI

**Files:**
- Create: `frontend/src/lib/documentBulkLimits.mjs`
- Modify: `frontend/src/lib/api.js:298-336`
- Modify: `frontend/src/components/DocumentList.jsx:23-34,453-533,752-783`
- Modify: `frontend/src/components/SelectionBar.jsx`
- Modify: `frontend/src/components/PreviewModal.jsx`
- Modify: `frontend/src/context/ChatContext.js`
- Create: `frontend/test/bulkExport.test.mjs`

**Interfaces:**
- Consumes `/api/settings.bulk_export_enabled`, `.bulk_export_download_enabled`, and `.bulk_export_max_docs` through the existing global `ChatContext` settings load.
- Produces `BULK_LIMITS`, `selectionLimit({isAdmin, exportEnabled, exportMaxDocs})`, `bulkCapabilities(count, {isAdmin, exportEnabled, exportMaxDocs})`, `createBulkExport(docIds)`, and an admin-only export action.

- [ ] **Step 1: Write failing pure UI-limit tests**

```javascript
test("admin can select 1000 for export without enabling 50-doc mutations", () => {
  const c = bulkCapabilities(1000, { isAdmin: true, exportEnabled: true, exportMaxDocs: 1000 });
  assert.equal(c.export, true);
  assert.equal(c.tags, false);
  assert.equal(c.delete, false);
  assert.equal(c.regenerate, false);
});

test("editor selection remains capped at 50 and has no export", () => {
  assert.equal(selectionLimit({ isAdmin: false, exportEnabled: true, exportMaxDocs: 1000 }), 50);
  assert.equal(bulkCapabilities(20, { isAdmin: false, exportEnabled: true, exportMaxDocs: 1000 }).export, false);
});

test("disabled export does not raise the admin selection cap", () => {
  assert.equal(selectionLimit({ isAdmin: true, exportEnabled: false, exportMaxDocs: 1000 }), 50);
});
```

- [ ] **Step 2: Run frontend test and confirm RED**

Run from `frontend`:

```powershell
node --test test/bulkExport.test.mjs
```

- [ ] **Step 3: Implement pure limits and API call**

```javascript
export const BULK_LIMITS = Object.freeze({ export: 1000, tags: 50, delete: 50, regenerate: 20 });
export const selectionLimit = ({ isAdmin, exportEnabled, exportMaxDocs = BULK_LIMITS.export }) =>
  isAdmin && exportEnabled ? exportMaxDocs : BULK_LIMITS.tags;
export function bulkCapabilities(count, { isAdmin, exportEnabled, exportMaxDocs = BULK_LIMITS.export }) {
  return {
    export: isAdmin && exportEnabled && count > 0 && count <= exportMaxDocs,
    tags: count > 0 && count <= BULK_LIMITS.tags,
    delete: isAdmin && count > 0 && count <= BULK_LIMITS.delete,
    regenerate: isAdmin && count > 0 && count <= BULK_LIMITS.regenerate,
  };
}
```

`createBulkExport` posts JSON to `/documents/bulk-export` through the existing CSRF-aware `request()`.

- [ ] **Step 4: Decouple selection from action limits**

Read export capability from `useChat().settings`; add the three export defaults to `FALLBACK_SETTINGS` so the UI remains conservative (`enabled=false`, `download_enabled=false`, `max_docs=1000`) until the request completes. Admin `toggleSelect`, `selectPage`, and `selectAllFiltered` use the dynamic export cap only when enabled; editor and disabled-export admin use 50. Clamp stale selection if role/capability changes.

SelectionBar calculates capabilities once and disables only the actions whose caps are exceeded, with an exact localized reason in `title` and visible help text. Add “Выгрузить исходники” only for enabled admin; require a native confirmation containing document count, call `createBulkExport`, show the queued Job ID, and preserve selection/document data so the admin can inspect or retry. PreviewModal disables regenerate above 20 and delete above 50 based on `docIds.length` (not only `preview.matched`) and does not offer export inside the destructive preview.

- [ ] **Step 5: Verify UI logic and mark the checkpoint**

Run:

```powershell
node --test test/bulkExport.test.mjs test/i18n.test.mjs
```

Expected: PASS. Mark Task 7 complete.

---

### Task 8: Admin/Security panels and i18n

**Files:**
- Modify: `frontend/src/components/AdminPanel.jsx`
- Modify: `frontend/src/components/SecurityPanel.jsx:60-70`
- Modify: `frontend/src/i18n/locales/ru.js`
- Modify: `frontend/src/i18n/locales/en.js`
- Modify: `backend/app/i18n/ui_en.json`
- Modify: `backend/app/i18n/ui_keys.json`
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/test/bulkExport.test.mjs`
- Modify: `frontend/test/i18n.test.mjs`
- Modify: `frontend/test/api-proxy.test.js`

**Interfaces:**
- Consumes: generic Job JSON with `result.parts`, `result.artifact_status`, and `result.expires_at`.
- Produces download links using direct browser navigation, manual artifact deletion, and no generic approve/cancel control for `bulk_export`.

- [ ] **Step 1: Extend failing frontend source/i18n tests**

Assert `bulk_export` label, all six security audit labels, available/expired/deleted artifact states, direct `/api/jobs/${job.id}/export/${part.number}` href, download-link suppression when `bulk_export_download_enabled=false`, no approve/cancel button for export Jobs, and absence of `blob()`/automatic multi-download code. Keep the existing audit CSV `Blob` test out of this prohibition: only ZIP download code must avoid `blob()`. Extend `api-proxy.test.js` with a GET whose mocked upstream body is a `ReadableStream`; assert the returned response uses that stream, preserves `Content-Length`, `Content-Disposition`, `Cache-Control`, and does not call `arrayBuffer()`/`blob()`.

- [ ] **Step 2: Run frontend tests and confirm RED**

```powershell
node --test test/bulkExport.test.mjs test/i18n.test.mjs
```

- [ ] **Step 3: Implement AdminPanel artifact controls**

Show processed/total, formatted source/output byte size, expiry, one `<a download>` per part, and a manual delete button. Use `deleteExportArtifacts(jobId)` from `api.js` with the existing CSRF-aware request helper. Do not auto-click links. Hide links when `artifact_status !== "available"`, download feature is off, or the Job is not completed. Show an explicit “выдача временно отключена” state when artifacts exist but download is disabled. Generic approve/cancel controls render only for `bulk_delete`/`bulk_regenerate`.

- [ ] **Step 4: Implement audit filters and bilingual copy**

Add all action names to `SecurityPanel.KNOWN_ACTIONS`, labels/states/buttons, and `apiError.<stable-code>` strings for every Task 3 export code to ru/en dictionaries. German/French continue using the existing English fallback and need no separate locale file. Generate backend key catalogs using the repository script rather than hand-sorting generated JSON:

```powershell
node scripts/export-ui-keys.mjs
```

- [ ] **Step 5: Verify and mark the checkpoint**

Run all frontend tests:

```powershell
node --test test/*.test.mjs
```

Expected: all PASS. Mark Task 8 complete.

---

### Task 9: Integrated resilience and load acceptance

**Files:**
- Create: `backend/test_scripts/benchmark_bulk_export.py`
- Modify: `backend/test_scripts/probe_parallel_retrieval.py`
- Create: `backend/tests/test_bulk_export_integration.py`
- Modify: `backend/tests/test_export_queue.py`
- Modify: `backend/tests/test_bulk_export_api.py`

**Interfaces:**
- Produces `run_parallel_retrieval(cases, *, state, clients, queries_per_client) -> dict` for reuse without subprocess parsing.
- Produces a schema-versioned JSON benchmark artifact with exact input bytes, actual part sizes, elapsed time, RSS baseline/peak/delta, CRC results, cleanup result, and same-mode baseline/concurrent search samples.

- [ ] **Step 1: Write integration tests for the full lifecycle**

Use small byte-limit settings to exercise 3-part create → worker → completed → streamed download audit → lease release → expiry. Add feature-off cases, audit failure at each transition, source replacement after submit, restart between queued/running states, orphan filesystem states, and verify regular bulk jobs are unaffected. Every test uses a temporary SQLite database plus temporary `data_dir`; no repository runtime data is touched.

- [ ] **Step 2: Run integration tests and confirm RED before final wiring**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task9'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_bulk_export_integration.py tests/test_export_queue.py tests/test_bulk_export_api.py -p no:cacheprovider --basetemp "$t\base" -q
```

- [ ] **Step 3: Refactor the existing retrieval probe behind an importable function**

Rename `_run_state` in `backend/test_scripts/probe_parallel_retrieval.py` to the public `run_parallel_retrieval` interface above without changing its CLI JSON schema. Add a unit-level regression in `test_bulk_export_integration.py` that monkeypatches retrieval dependencies and proves the importable function and CLI use the same implementation. The bulk-export benchmark always uses `state="on"` for both baseline and concurrent samples so only export I/O changes between measurements.

- [ ] **Step 4: Implement the standalone benchmark script**

The script uses `TemporaryDirectory` outside repository `data/uploads`, generates deterministic sparse files totalling exactly `total_mb * 1024 * 1024`, invokes the production partition/builder, samples the current process RSS every 50 ms (`GetProcessMemoryInfo` through `ctypes` on Windows and `/proc/self/statm` or `resource.getrusage` fallback on POSIX), verifies every ZIP CRC with `ZipFile.testzip()`, and removes corpus/output in `finally`. A `threading.Barrier` starts builder and concurrent retrieval together; timestamps prove the builder remained active for the complete concurrent probe. If there is no full overlap, the script exits non-zero and labels the performance result `inconclusive`, never `passed`.

```python
parser.add_argument("--documents", type=int, default=1000)
parser.add_argument("--total-mb", type=int, default=700)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--work-root", type=Path)
parser.add_argument(
    "--retrieval-cases",
    type=Path,
    default=Path(__file__).with_name("glossary-probe-cases.json"),
)
parser.add_argument("--clients", type=int, default=5)
parser.add_argument("--queries-per-client", type=int, default=20)
```

`--work-root` selects the parent for a uniquely named temporary directory and is useful to keep the benchmark on the same filesystem as production data. The script resolves the generated child, proves it is below `work_root`, and deletes only that child in `finally`.

The output contract is fixed:

```json
{
  "schema_version": 1,
  "input": {"documents": 1000, "total_bytes": 734003200},
  "build": {"elapsed_ms": 0, "part_sizes_bytes": [], "crc_errors": [], "cleanup_ok": true},
  "memory": {"baseline_rss_bytes": 0, "peak_rss_bytes": 0, "delta_rss_bytes": 0},
  "retrieval": {
    "baseline": {"errors": 0, "timings": {"p95_ms": 0}},
    "during_export": {"errors": 0, "timings": {"p95_ms": 0}},
    "full_overlap": true,
    "p95_degradation_pct": 0
  },
  "acceptance": {"passed": true, "failures": []}
}
```

Values shown as zero are type examples, not expected measurements. Do not create Job/Document rows in the configured application database. The integration suite covers queue semantics; this script isolates physical ZIP I/O plus its effect on live retrieval.

- [ ] **Step 5: Make lifecycle tests pass**

Complete any startup/shutdown, singleton reset, error mapping, or lease cleanup wiring exposed by the integration test. Repeat the Task 9 pytest command; expected: PASS.

- [ ] **Step 6: Run the 1000-file/700-MiB builder acceptance**

From `backend`, with PostgreSQL/Qdrant/Ollama healthy for the retrieval side:

```powershell
$out = '..\tests\artifacts\bulk-export\bulk-export-benchmark.json'; $work = '..\tests\tmp\bulk-export-benchmark'; New-Item -ItemType Directory -Force -Path $work | Out-Null; .\.venv\Scripts\python.exe -m test_scripts.benchmark_bulk_export --documents 1000 --total-mb 700 --work-root $work --retrieval-cases test_scripts/glossary-probe-cases.json --clients 5 --queries-per-client 20 --output $out
```

Expected: exactly 1000 inputs and 734003200 bytes; about three parts; every actual part <=262144000 bytes; `crc_errors=[]`; RSS delta <=157286400 bytes; cleanup succeeds; both retrieval runs have 100 requests/zero errors; `full_overlap=true`; p95 degradation <=20%. `tests/artifacts/bulk-export/` is a local ignored acceptance artifact unless the user asks to version it.

- [ ] **Step 7: Independently read and verify measured acceptance**

Read the benchmark JSON and calculate the degradation from its exact p95 fields:

```powershell
$r = Get-Content -LiteralPath '..\tests\artifacts\bulk-export\bulk-export-benchmark.json' -Raw | ConvertFrom-Json; $baseline=[double]$r.retrieval.baseline.timings.p95_ms; $during=[double]$r.retrieval.during_export.timings.p95_ms; [pscustomobject]@{Documents=$r.input.documents;TotalBytes=$r.input.total_bytes;Parts=($r.build.part_sizes_bytes -join ',');RssDeltaBytes=$r.memory.delta_rss_bytes;BaselineP95Ms=$baseline;DuringP95Ms=$during;DegradationPct=[math]::Round((($during-$baseline)/$baseline)*100,2);FullOverlap=$r.retrieval.full_overlap;Passed=$r.acceptance.passed} | Format-List
```

Record the command and output in the final verification report. Cross-check the computed percentage with `retrieval.p95_degradation_pct`; a mismatch is a benchmark-schema failure.

- [ ] **Step 8: Mark the checkpoint only after measured acceptance**

Record part sizes, elapsed build time, memory peak, baseline/during p95, and cleanup evidence. Mark Task 9 complete only if every criterion passes; otherwise leave it unchecked with the measured blocker.

---

### Task 10: Safe rollback command

**Files:**
- Create: `backend/scripts/rollback_bulk_exports.py`
- Create: `backend/tests/test_rollback_bulk_exports.py`
- Modify: `backend/app/services/export_queue.py`

**Interfaces:**
- Produces `ExportRollbackPlan(job_ids: tuple[int, ...], artifact_names: tuple[str, ...], artifact_bytes: int, active_lease_job_ids: tuple[int, ...], include_ready: bool)`.
- Produces `ExportRollbackResult(failed_jobs: int, deleted_ready: int, deleted_bytes: int, skipped_leases: tuple[int, ...], audit_events: int, errors: tuple[str, ...])`.
- Produces `ExportQueue.plan_rollback(*, include_ready: bool) -> ExportRollbackPlan`.
- Produces `ExportQueue.apply_rollback(plan: ExportRollbackPlan, *, actor: SystemUser) -> ExportRollbackResult`.
- Produces `scripts.rollback_bulk_exports.main(argv: list[str] | None = None) -> int`, a dry-run-by-default CLI; mutation requires both `--apply` and `--confirm DELETE_EXPORT_ARTIFACTS`.

- [ ] **Step 1: Write failing rollback safety tests**

Cover no-flag dry run, wrong confirmation, queued/running Jobs, optional completed/available Jobs, active download lease, `.building/.ready/.deleting` artifacts, audit rollback, repeat execution, and unrelated Job/artifact preservation. Assert dry run changes no DB row, audit row, directory, or file byte.

```python
def test_rollback_is_dry_run_by_default(tmp_path, isolated_db, capsys):
    job_id = seed_export_job("queued")
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert load_job(job_id).status == "queued"

def test_apply_requires_exact_confirmation():
    assert main(["--apply", "--confirm", "yes"]) == 2
```

- [ ] **Step 2: Run rollback tests and confirm RED**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task10'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_rollback_bulk_exports.py -p no:cacheprovider --basetemp "$t\base" -q
```

- [ ] **Step 3: Implement planning and apply phases**

`plan_rollback()` is read-only and returns Job IDs/statuses, artifact basenames/bytes, active leases and proposed transitions. `apply_rollback()` refuses any active lease; for queued/running Jobs it retires artifacts and transactionally writes `document_bulk_export_failed` with reason `operator_rollback`; with `include_ready=True`, completed/available artifacts transition through `.deleting` and transactionally write `document_bulk_export_deleted` with reason `code_rollback`. On audit/DB failure restore `.ready` and leave the Job unchanged. Unrelated `bulk_delete`/`bulk_regenerate`, uploads, OKF, Qdrant and LLM state are never touched.

- [ ] **Step 4: Implement the guarded CLI**

Print one JSON object to stdout containing `dry_run`, `include_ready`, affected counts/bytes, skipped leases, audit results and errors. Exit codes: `0` success/dry-run, `2` invalid confirmation/arguments, `3` active lease or precondition conflict, `4` partial/failed apply. The operational sequence is explicit: disable new create/download as intended, stop backend, run dry-run, review JSON, then rerun with the confirmation token before deploying code that no longer knows `bulk_export`.

```powershell
.\.venv\Scripts\python.exe scripts/rollback_bulk_exports.py --include-ready
.\.venv\Scripts\python.exe scripts/rollback_bulk_exports.py --include-ready --apply --confirm DELETE_EXPORT_ARTIFACTS
```

- [ ] **Step 5: Verify rollback behavior and mark the checkpoint**

Repeat the Task 10 pytest command. Expected: PASS; a second apply reports zero new transitions, and seeded unrelated Jobs/files remain byte-for-byte unchanged. Mark Task 10 complete.

---

### Task 11: Operations documentation and final verification

**Files:**
- Modify: `.env.example:275-300`
- Modify: `.gitignore`
- Create: `data/exports/.gitkeep`
- Modify: `docs/PRODUCTION_DEPLOYMENT.md:132-211`
- Modify: `SECURITY.md`
- Modify: `docs/superpowers/specs/2026-09-13-bulk-raw-document-export-design.md`
- Modify: `docs/superpowers/plans/2026-09-13-bulk-raw-document-export.md`

**Interfaces:**
- Produces an operator-visible configuration/rollback contract and verified final checklist.

- [ ] **Step 1: Document every environment variable**

Add exact defaults for both feature flags and every limit from Global Constraints. Ignore `data/exports/*` while retaining `.gitkeep`. State that exports require write capacity under `data/exports`, are raw-data egress, and must not be placed under a web-served static root.

- [ ] **Step 2: Document deployment and rollback**

Cover one export-worker, 5-GiB ready quota plus queued/running reservations, 2-GiB reserve, 24-hour cleanup, direct streaming through Next/reverse-proxy, proxy buffering/body-size/timeout checks, backup exclusion for ephemeral exports, admin-only policy, audit fail-closed semantics, routine flag rollback, and the guarded Task 10 command required before a code downgrade.

- [ ] **Step 3: Run backend lint**

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

Expected: exit 0. CI runs Ruff before pytest, so a green test suite with lint failures is not complete.

- [ ] **Step 4: Run backend focused suite**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-final'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_bulk_export.py tests/test_export_queue.py tests/test_bulk_export_api.py tests/test_bulk_export_integration.py tests/test_rollback_bulk_exports.py tests/test_jobs.py tests/test_audit.py tests/test_authz.py tests/test_error_headers.py tests/test_settings.py -p no:cacheprovider --basetemp "$t\base" -q
```

Expected: all selected tests PASS.

- [ ] **Step 5: Run full frontend suite**

From `frontend`:

```powershell
node --test test/*.test.mjs
```

Expected: all PASS.

- [ ] **Step 6: Run the broader backend regression suite**

From `backend`, with a new workspace-local temp root:

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-regression'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp "$t\base" -q
```

Report the known order-sensitive `_inflight_over_limit_is_logged` flake separately if it appears: rerun it isolated and report both results; do not call the whole suite green without fresh evidence.

- [ ] **Step 7: Perform spec/plan self-review**

Check every spec section against a completed task, search both documents for placeholder markers and stale names, and verify function names/types match across tasks. Fix discrepancies inline.

- [ ] **Step 8: Update final status without committing**

Mark only verified tasks complete, add the date and exact verification results under `Progress`, list any open acceptance stages, inspect `git status --short`, and leave all existing user changes untouched. Do not commit, merge, push, or create a PR.

Expected: Task 11 is checked only after focused/backend/frontend/regression/benchmark evidence has been recorded and every remaining checkbox is backed by a fresh command result.

## Completion Gate

Implementation is complete only when all eleven Task checkboxes are checked from fresh evidence, the 1000-file/700-MiB acceptance passes, search p95 degradation is within 20%, required audit failures are fail-closed, dry-run/apply rollback is verified, and no unregistered `.ready` or stale `.building`/`.deleting` export artifact remains.
