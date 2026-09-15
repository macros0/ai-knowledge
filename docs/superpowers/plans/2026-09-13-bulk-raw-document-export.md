# Bulk Raw Document Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать администраторам аудируемую фоновую выгрузку до 1000 исходных документов общим объёмом до 1 ГиБ в ZIP-частях до 250 МиБ.

**Architecture:** Новый `ExportQueue` использует существующую таблицу `jobs`, но отдельный worker и отдельное владение `job_type=bulk_export`. Preflight выполняется до постановки задачи, части собираются без сжатия в `.building`, публикуются атомарным rename в `.ready`, скачиваются потоково и удаляются по TTL/квоте с обязательным аудитом.

**Tech Stack:** FastAPI/Starlette `FileResponse`, SQLAlchemy 2.0, Python `zipfile`, PostgreSQL/SQLite, React/Next.js App Router proxy, Node test runner, pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-bulk-raw-document-export-design.md`

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

## Progress

- [ ] Task 1 — Configuration and queue ownership
- [ ] Task 2 — Pure export planning and ZIP builder
- [ ] Task 3 — Admission and transactional submission
- [ ] Task 4 — Dedicated worker and crash recovery
- [ ] Task 5 — Download leases, expiry, and deletion
- [ ] Task 6 — Admin-only HTTP API and audit contract
- [ ] Task 7 — Selection limits and export UI
- [ ] Task 8 — Admin/Security panels and i18n
- [ ] Task 9 — Integrated resilience and load acceptance
- [ ] Task 10 — Operations documentation and final verification

---

### Task 1: Configuration and queue ownership

**Files:**
- Modify: `backend/app/config.py:241-274`
- Modify: `backend/app/services/job_queue.py:22-120`
- Modify: `backend/tests/test_settings.py`
- Modify: `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: existing `Settings`, `Job`, `JobQueue`, and `JOB_TYPES`.
- Produces: exact `bulk_export_*` settings and a `JobQueue` that recovers/counts/executes only its owned types.

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
```

- [ ] **Step 2: Write a failing recovery-ownership regression test**

Seed one queued `bulk_export` Job, call `JobQueue(start_worker=False).recover_after_restart()`, and assert its status stays queued and the regular queue remains empty. Also seed one running export and assert regular recovery does not mark it failed.

```python
def test_regular_queue_recovery_ignores_bulk_export(isolated_db):
    export_id = seed_job("bulk_export", "queued")
    q = JobQueue(start_worker=False)
    q.recover_after_restart()
    assert q.get(export_id)["status"] == "queued"
    assert q._queue.empty()
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
```

In `recover_after_restart()` and `pending_count()` add `Job.job_type.in_(JOB_TYPES)`. In `_execute()` return unless `job["job_type"] in JOB_TYPES`.

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
  - `ExportDocument(doc_id: str, filename: str, path: Path, size_bytes: int)`
  - `ExportPart(number: int, documents: tuple[ExportDocument, ...], source_bytes: int)`
  - `BuiltExportPart(number: int, path: Path, filename: str, size_bytes: int, document_count: int)`
  - `partition_documents(documents, payload_limit_bytes) -> list[ExportPart]`
  - `build_export_parts(job_id, parts, building_dir) -> list[BuiltExportPart]`

- [ ] **Step 1: Write failing unit tests for sanitization and partitioning**

Cover preserved order, exact boundary, 1 MiB metadata reserve, oversize single file, duplicate filenames, Windows paths, `..`, and control characters.

```python
def test_partition_preserves_request_order(tmp_path):
    docs = tuple(make_doc(tmp_path, i, size) for i, size in enumerate((4, 6, 2)))
    parts = partition_documents(docs, payload_limit_bytes=10)
    assert [[d.doc_id for d in p.documents] for p in parts] == [[docs[0].doc_id, docs[1].doc_id], [docs[2].doc_id]]

def test_safe_name_discards_client_path():
    assert safe_archive_filename(r"C:\\temp\\report.docx") == "report.docx"
    assert safe_archive_filename("../report.docx") == "report.docx"
```

- [ ] **Step 2: Write a failing ZIP contract test**

Build two parts and assert `ZIP_STORED`, safe `documents/<doc_id>/<name>` entries, `_manifest.json`, exact order, counts, and no absolute server path in names or manifest.

```python
with zipfile.ZipFile(result[0]["path"]) as zf:
    info = zf.getinfo(f"documents/{doc.doc_id}/report.docx")
    assert info.compress_type == zipfile.ZIP_STORED
    manifest = json.loads(zf.read("_manifest.json"))
    assert manifest["parts_total"] == 2
```

- [ ] **Step 3: Run the focused test and confirm RED**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task2'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_bulk_export.py -p no:cacheprovider --basetemp "$t\base" -q
```

Expected: import failure for `app.services.bulk_export`.

- [ ] **Step 4: Implement the pure builder**

Use frozen dataclasses, sequential reads, basename-only sanitization, `ZIP_STORED`, and safe metadata returned without server paths:

```python
@dataclass(frozen=True)
class ExportDocument:
    doc_id: str
    filename: str
    path: Path
    size_bytes: int

def build_export_parts(job_id: int, parts: Sequence[ExportPart], building_dir: Path) -> list[BuiltExportPart]:
    building_dir.mkdir(parents=True, exist_ok=False)
    outputs = []
    for part in parts:
        name = f"documents-export-{job_id}-part-{part.number:03d}-of-{len(parts):03d}.zip"
        with zipfile.ZipFile(building_dir / name, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for doc in part.documents:
                zf.write(doc.path, f"documents/{doc.doc_id}/{safe_archive_filename(doc.filename)}")
            zf.writestr("_manifest.json", manifest_bytes(job_id, part, len(parts)))
        path = building_dir / name
        outputs.append(BuiltExportPart(part.number, path, name, path.stat().st_size, len(part.documents)))
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

- [ ] **Step 1: Add failing preflight tests**

Test empty, dedup preserving order, 1000/1001, deleted document, zero/multiple source matches, real stat size instead of DB size, >250 MiB single file, >1 GiB total, one active per user, three jobs/hour, pending cap, retained quota, and free-space reserve. Monkeypatch `shutil.disk_usage` and byte settings to small values; do not allocate GiB test files.

```python
def test_submit_is_atomic_with_requested_audit(queue, admin, monkeypatch):
    monkeypatch.setattr("app.services.audit.record_in_session", raising_audit)
    with pytest.raises(RuntimeError):
        queue.submit([DOC_ID], admin)
    assert jobs_for("bulk_export") == []
```

- [ ] **Step 2: Add audit constants and contract tests**

Add all six exact actions to `ACTION_TYPES` and the expected-set test:

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

`get_export_metadata_many` must select only `Document.id`, `filename`, `size`, and `deleted_at`. `ExportQueue._preflight` resolves `<doc_id>.*`, excludes directories, verifies containment, stats actual bytes, computes parts, quota, active-user count, rolling-hour count, and `Retry-After`.

```python
@dataclass(frozen=True)
class ExportPlan:
    documents: tuple[ExportDocument, ...]
    parts: tuple[ExportPart, ...]
    total_bytes: int

def _preflight(self, doc_ids: list[str], user_id: str) -> ExportPlan:
    ids = list(dict.fromkeys(doc_ids))
    metadata = self.registry.get_export_metadata_many(ids)
    # Validate every id and source before returning a complete immutable plan.
```

- [ ] **Step 5: Implement transactional Job + requested audit**

Under the ExportQueue admission lock, repeat DB-dependent quota checks, create/flush Job, call `record_in_session()` with the full doc list and byte counts, commit via `session_scope`, then enqueue only after commit.

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
- Modify: `backend/app/main.py:160-170`
- Modify: `backend/tests/test_export_queue.py`
- Modify: `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: committed export Jobs and Task 2 builder.
- Produces: `_execute(job_id)`, `recover_after_restart()`, part-level progress, atomic ready publication, completed/failed audit transitions.

- [ ] **Step 1: Write failing execution/recovery tests**

Cover `.building -> .ready`, all-part publication, builder failure cleanup, completion audit atomicity, audit failure deleting ready output, queued export recovery, running export failure recovery, and regular/export queues not stealing each other's jobs.

```python
def test_ready_is_not_visible_until_every_part_exists(queue, seeded_job, monkeypatch):
    seen = []
    monkeypatch.setattr(queue, "_publish", lambda *args: seen.append("publish"))
    queue._execute(seeded_job)
    assert seen == ["publish"]
    assert queue.get(seeded_job)["status"] == "completed"
```

- [ ] **Step 2: Run focused tests and confirm RED**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task4'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_export_queue.py tests/test_jobs.py -p no:cacheprovider --basetemp "$t\base" -q
```

- [ ] **Step 3: Implement worker transitions**

Use one daemon thread and `queue.Queue[int]`. Update progress after each completed part. Rename only after all ZIP files exist. Complete Job and audit in one transaction; if that transaction fails, remove the ready directory and leave recovery a non-completed Job.

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

Recovery queries only `Job.job_type == BULK_EXPORT`. It removes export `.building`, marks export `running` failed with `document_bulk_export_failed`, requeues export `queued`, and never touches delete/regenerate. Call it from lifespan after regular `JobQueue.recover_after_restart()`.

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
  - `ExportQueue.acquire_download(job_id, part_number, user, ip_address) -> ExportDownloadLease`
  - `ExportQueue.delete_artifacts(job_id, user, ip_address, reason="deleted") -> dict`
  - `ExportQueue.cleanup_expired(now: datetime | None = None) -> int`

- [ ] **Step 1: Write failing lease and cleanup tests**

Test ready-only access, part-number lookup, rebuilt safe path, active lease blocking cleanup, release enabling cleanup, `.ready -> .deleting`, DB/audit rollback restoring `.ready`, physical-delete retry, expiry metadata preservation, and mandatory download audit.

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

Under one reentrant lock, validate result state/path, increment `(job_id, part_number)` lease count, append audit in a DB transaction, and decrement on failure. Return only a closure that decrements once.

- [ ] **Step 4: Implement expiry/manual deletion state machine**

Rename to `.deleting` before DB transition; audit and set `artifact_status` in one transaction; restore `.ready` on rollback; delete `.deleting` after commit. Cleanup skips active leases and retries orphan `.deleting` directories without making them downloadable.

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

**Interfaces:**
- Consumes: `get_export_queue()` and lease methods.
- Produces:
  - `POST /api/documents/bulk-export`
  - `GET /api/jobs/{job_id}/export/{part_number}`
  - `DELETE /api/jobs/{job_id}/export`

- [ ] **Step 1: Write the role matrix and response-contract tests**

Parametrize viewer/editor/security/admin for all three endpoints. Assert admin create returns 202; download has ZIP/content-disposition/no-store/nosniff; delete preserves Job. Assert 400/409/413/429 with Retry-After/503/507/410 stable error codes.

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

Use `status_code=202`, `require_role("admin")`, the existing request schema, and explicit DomainError-code-to-status mapping. `BULK_EXPORT_ENABLED=false` returns 404 without creating Job/audit.

- [ ] **Step 4: Implement download/delete endpoints**

Download acquires a lease only after admin authorization and returns:

```python
lease = get_export_queue().acquire_download(job_id, part_number, user, _client_ip(request))
return FileResponse(
    lease.path,
    media_type="application/zip",
    filename=lease.filename,
    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    background=BackgroundTask(lease.release),
)
```

`BULK_EXPORT_DOWNLOAD_ENABLED=false` blocks GET but not cleanup/delete.

- [ ] **Step 5: Verify audit fail-closed behavior**

Add a test that forces download audit insertion to fail and asserts 503 plus zero response bytes and no leaked path. Repeat the Task 6 command; expected: PASS. Mark Task 6 complete.

---

### Task 7: Selection limits and export UI

**Files:**
- Create: `frontend/src/lib/documentBulkLimits.mjs`
- Modify: `frontend/src/lib/api.js:298-336`
- Modify: `frontend/src/components/DocumentList.jsx:23-34,453-533,752-783`
- Modify: `frontend/src/components/SelectionBar.jsx`
- Modify: `frontend/src/components/PreviewModal.jsx`
- Create: `frontend/test/bulkExport.test.mjs`

**Interfaces:**
- Produces `BULK_LIMITS`, `selectionLimit(isAdmin)`, `bulkCapabilities(count, isAdmin)`, `createBulkExport(docIds)`, and an admin-only export action.

- [ ] **Step 1: Write failing pure UI-limit tests**

```javascript
test("admin can select 1000 for export without enabling 50-doc mutations", () => {
  const c = bulkCapabilities(1000, true);
  assert.equal(c.export, true);
  assert.equal(c.tags, false);
  assert.equal(c.delete, false);
  assert.equal(c.regenerate, false);
});

test("editor selection remains capped at 50 and has no export", () => {
  assert.equal(selectionLimit(false), 50);
  assert.equal(bulkCapabilities(20, false).export, false);
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
export const selectionLimit = (isAdmin) => isAdmin ? BULK_LIMITS.export : BULK_LIMITS.tags;
export function bulkCapabilities(count, isAdmin) {
  return {
    export: isAdmin && count > 0 && count <= BULK_LIMITS.export,
    tags: count > 0 && count <= BULK_LIMITS.tags,
    delete: isAdmin && count > 0 && count <= BULK_LIMITS.delete,
    regenerate: isAdmin && count > 0 && count <= BULK_LIMITS.regenerate,
  };
}
```

`createBulkExport` posts JSON to `/documents/bulk-export` through the existing CSRF-aware `request()`.

- [ ] **Step 4: Decouple selection from action limits**

Admin `selectPage/selectAllFiltered` use 1000; editor uses 50. SelectionBar disables only the actions whose caps are exceeded and shows exact localized reasons. Add “Выгрузить исходники” for admin; on success show queued Job ID and keep document data unchanged. PreviewModal disables regenerate above 20 and delete above 50.

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

**Interfaces:**
- Consumes: generic Job JSON with `result.parts`, `artifact_status`, and `expires_at`.
- Produces download links using direct browser navigation and manual artifact deletion.

- [ ] **Step 1: Extend failing frontend source/i18n tests**

Assert `bulk_export` label, all six security audit labels, available/expired/deleted artifact states, direct `/api/jobs/${job.id}/export/${part.number}` href, and absence of `blob()`/automatic multi-download code.

- [ ] **Step 2: Run frontend tests and confirm RED**

```powershell
node --test test/bulkExport.test.mjs test/i18n.test.mjs
```

- [ ] **Step 3: Implement AdminPanel artifact controls**

Show processed/total, formatted byte size, expiry, one `<a download>` per part, and a manual delete button. Do not auto-click links. Hide links when `artifact_status !== "available"`.

- [ ] **Step 4: Implement audit filters and bilingual copy**

Add all action names to SecurityPanel and ru/en dictionaries. Generate backend key catalogs using the repository script rather than hand-sorting generated JSON:

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
- Create: `backend/scripts/benchmark_bulk_export.py`
- Create: `backend/tests/test_bulk_export_integration.py`
- Modify: `backend/tests/test_export_queue.py`
- Modify: `backend/tests/test_bulk_export_api.py`

**Interfaces:**
- Produces a JSON benchmark artifact with input count/bytes, part sizes, elapsed time, peak RSS, cleanup result, and baseline/concurrent search latency samples.

- [ ] **Step 1: Write integration tests for the full lifecycle**

Use small byte-limit settings to exercise 3-part create → worker → completed → download audit → lease release → expiry. Add restart between queued/running states and verify regular bulk jobs are unaffected.

- [ ] **Step 2: Run integration tests and confirm RED before final wiring**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-task9'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_bulk_export_integration.py tests/test_export_queue.py tests/test_bulk_export_api.py -p no:cacheprovider --basetemp "$t\base" -q
```

- [ ] **Step 3: Implement the standalone benchmark script**

The script must use an isolated temporary data directory, generate exactly the requested count/total bytes, invoke the production partition/builder, record peak RSS (`GetProcessMemoryInfo` through `ctypes` on Windows and `resource.getrusage` on POSIX), verify every ZIP CRC with `ZipFile.testzip()`, and remove its temporary corpus in `finally`. With `--retrieval-cases`, it runs the existing parallel retrieval probe once as baseline and once while the builder thread is active.

```python
parser.add_argument("--documents", type=int, default=1000)
parser.add_argument("--total-mb", type=int, default=700)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--retrieval-cases", type=Path)
parser.add_argument("--clients", type=int, default=5)
parser.add_argument("--queries-per-client", type=int, default=20)
```

Do not point it at `data/uploads` and do not create DB rows in the real database.

- [ ] **Step 4: Make lifecycle tests pass**

Complete any startup/shutdown, singleton reset, error mapping, or lease cleanup wiring exposed by the integration test. Repeat the Task 9 pytest command; expected: PASS.

- [ ] **Step 5: Run the 1000-file/700-MiB builder acceptance**

From `backend`, with PostgreSQL/Qdrant/Ollama healthy for the retrieval side:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_bulk_export.py --documents 1000 --total-mb 700 --retrieval-cases scripts/glossary-probe-cases.json --clients 5 --queries-per-client 20 --output scripts/bulk-export-benchmark-20260913.json
```

Expected: about three parts; every part <=250 MiB; `testzip=null`; RSS delta <=150 MiB; cleanup succeeds; retrieval baseline/during samples are present. Keep the JSON as an explicit local acceptance artifact unless the user asks to version it.

- [ ] **Step 6: Measure search impact during a real queued export**

Read the benchmark JSON and calculate the degradation from its exact p95 fields:

```powershell
$r = Get-Content -LiteralPath 'scripts/bulk-export-benchmark-20260913.json' -Raw | ConvertFrom-Json; $baseline=[double]$r.retrieval.baseline.p95_ms; $during=[double]$r.retrieval.during_export.p95_ms; [pscustomobject]@{BaselineP95Ms=$baseline;DuringP95Ms=$during;DegradationPct=[math]::Round((($during-$baseline)/$baseline)*100,2)} | Format-List
```

Record the command and output in the final verification report. Acceptance: both runs report zero errors and p95 degradation <=20%.

- [ ] **Step 7: Mark the checkpoint only after measured acceptance**

Record part sizes, elapsed build time, memory peak, baseline/during p95, and cleanup evidence. Mark Task 9 complete only if every criterion passes; otherwise leave it unchecked with the measured blocker.

---

### Task 10: Operations documentation and final verification

**Files:**
- Modify: `.env.example:275-300`
- Modify: `docs/PRODUCTION_DEPLOYMENT.md:132-211`
- Modify: `SECURITY.md`
- Modify: `docs/superpowers/specs/2026-09-13-bulk-raw-document-export-design.md`
- Modify: `docs/superpowers/plans/2026-09-13-bulk-raw-document-export.md`

**Interfaces:**
- Produces an operator-visible configuration/rollback contract and verified final checklist.

- [ ] **Step 1: Document every environment variable**

Add exact defaults for both feature flags and every limit from Global Constraints. State that exports require write capacity under `data/exports`, are raw-data egress, and must not be placed under a web-served static root.

- [ ] **Step 2: Document deployment and rollback**

Cover one export-worker, 5-GiB ready quota, 2-GiB reserve, 24-hour cleanup, direct streaming through Next/reverse-proxy, proxy timeout checks, backup exclusion for ephemeral exports, admin-only policy, audit fail-closed semantics, and rollback flags.

- [ ] **Step 3: Run backend focused suite**

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-final'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest tests/test_bulk_export.py tests/test_export_queue.py tests/test_bulk_export_api.py tests/test_bulk_export_integration.py tests/test_jobs.py tests/test_audit.py tests/test_authz.py tests/test_error_headers.py tests/test_settings.py -p no:cacheprovider --basetemp "$t\base" -q
```

Expected: all selected tests PASS.

- [ ] **Step 4: Run full frontend suite**

From `frontend`:

```powershell
node --test test/*.test.mjs
```

Expected: all PASS.

- [ ] **Step 5: Run the broader backend regression suite**

From `backend`, with a new workspace-local temp root:

```powershell
$t = Join-Path (Resolve-Path '..').Path '.pytest-run-bulk-export-regression'; New-Item -ItemType Directory -Force -Path $t | Out-Null; $env:TEMP=$t; $env:TMP=$t; .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp "$t\base" -q
```

Report the known order-sensitive `_inflight_over_limit_is_logged` flake separately if it appears: rerun it isolated and report both results; do not call the whole suite green without fresh evidence.

- [ ] **Step 6: Perform spec/plan self-review**

Check every spec section against a completed task, search both documents for placeholder markers and stale names, and verify function names/types match across tasks. Fix discrepancies inline.

- [ ] **Step 7: Update final status without committing**

Mark only verified tasks complete, add the date and exact verification results under `Progress`, list any open acceptance stages, inspect `git status --short`, and leave all existing user changes untouched. Do not commit, merge, push, or create a PR.

## Completion Gate

Implementation is complete only when all ten Task checkboxes are checked from fresh evidence, the 1000-file/700-MiB acceptance passes, search p95 degradation is within 20%, required audit failures are fail-closed, and no untracked `.building`/`.deleting` export artifact remains.
