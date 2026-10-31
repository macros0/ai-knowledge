# Portable artifacts and generations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track steps with checkboxes.

**Goal:** Поддержать local/RWX/S3 без потери принятых файлов и с одной системой публикации поколений.
**Architecture:** Immutable blobs и metadata в PostgreSQL, локальная материализация только для парсера/экспорта. Расширяем существующие generation IDs, active pointer и integrity checks; готовность shared storage предшествует distributed runtime.
**Tech Stack:** Python, SQLAlchemy/Alembic, файловая система, S3 SDK из общего requirements lock, существующие Qdrant generation filters.
**Spec:** [Согласованный дизайн](../specs/2026-10-31-kubernetes-replicas-storage-design.md).

Статус 31.10.2026: S1 в работе. Реализованы BlobRef/BlobStore, local/RWX/S3,
factory/settings и materialize с проверкой integrity, concurrent publication и
типизированных ошибок. Есть общий suite и реальные botocore model/hook tests.
Рабочий pipeline пока не подключён к адаптеру. Реальные provider contract tests
на инфраструктуре заказчика и S2–S4 остаются открытыми. Локальная проверка
не считается завершением S1 целиком или приёмкой общего хранилища.

## Global Constraints

Все ограничения [основного плана](2026-10-31-kubernetes-replicas.md) обязательны.
Не подменять `services/storage.py`: сейчас это классификация disk-full, её сохранить.
Ни ETag S3, ни успешный multipart-init не доказывают целостность или завершённую запись.
Не давать local paths и credentials в API. Новые зависимости — `requirements.txt`
и `requirements.lock.txt` вместе. S3 SDK выбрать/закрепить с аудитом зависимостей в S1.

## Review Focus

Interrupted upload; сохранённый blob без строки БД; устаревший checkpoint;
GC во время download; восстановление старых filepath и таблиц/вложений. S1–S4
содержат соответствующие проверки, stale-owner protection завершается в R2.

## Контракты

Новый пакет `backend/app/services/artifacts/`: `contract.py`, `local.py`, `rwx.py`,
`s3.py`, `repository.py`, `materialize.py`, `migration.py`, `__init__.py`.

`BlobRef` (frozen dataclass): `object_id: str`, `key: str`, `sha256: str`,
`size: int`, `media_type: str`, `store_id: str`. Object ID — uuid hex, не filename.
Ключ генерирует сервер `objects/<object_id>`; имена пользователя не образуют paths.
`BlobStore` protocol в contract.py:

```python
def put(self, source: BinaryIO, *, object_id: str, media_type: str) -> BlobRef: ...
def stat(self, ref: BlobRef) -> BlobRef: ...
def open(self, ref: BlobRef, *, start: int = 0, end: int | None = None) -> ContextManager[BinaryIO]: ...
def delete(self, ref: BlobRef) -> None: ...
```

`end` exclusive, `0 <= start <= end <= size`; неверный диапазон — typed error,
удаление отсутствующего объекта идемпотентно. `put` immutable: повтор того же ID
с теми же bytes возвращает тот же ref, с другими bytes — `BlobConflict`; это
проверяется и для S3, где требуются conditional writes или эквивалентный доказанный
механизм. Недоступность — `BlobUnavailable`, corruption — `BlobIntegrityError`,
quota/full — существующая доменная storage-full семантика. Ошибка не раскрывает URL с credentials.

`materialize(ref: BlobRef, *, store: BlobStore, scratch_root: Path) -> ContextManager[Path]`
проверяет sha256/size, очищает scratch при выходе. Blob backend не выполняет auth:
сервисный слой сначала проверяет документ/роль/lease, затем открывает blob.

## Task S1: адаптеры и контрактные проверки

**Files:** создать перечисленный пакет (кроме migration.py в S4),
`backend/tests/test_blob_contract.py`, `backend/tests/test_blob_materialize.py`;
изменить `backend/app/config.py`, зависимости/lock при добавлении SDK, `SECURITY.md`.

**Interfaces:** `get_blob_store(settings) -> BlobStore`; local использует отдельный
subdirectory `data/artifacts`, legacy paths пока читает прежний код; RWX использует
тот же immutable layout с проверенным mounted root. S3 settings: endpoint/bucket/
prefix/region/CA/addressing style и Secret credentials; `store_id` не меняется
при ротации ключа, только при смене хранилища. Явно разрешённый endpoint, без
автоматического произвольного redirect с credentials.

- [ ] Один параметризованный suite local/rwx/s3: `test_roundtrip_digest_and_range`, `test_interrupted_put_not_visible`, `test_same_id_different_bytes_conflicts`, `test_no_path_escape_or_symlink`, `test_materialize_cleans_after_error`. Assertions: bytes/hash/size совпали; прерванный объект не доступен как готовый; активный объект не перезаписан; scratch отсутствует после выхода.
- [ ] RED: `python -m pytest tests/test_blob_contract.py tests/test_blob_materialize.py -q`; local работает из temp, сетевые providers явно маркированы integration.
- [ ] Реализовать adapters: fsync/атомарное завершение для local, подтверждённую семантику RWX, bounded streaming/multipart completion/abort для S3. Не загружать весь большой upload в память. Не объявлять mock-S3 подтверждением реального provider.
- [ ] GREEN unit suite; runner `--suite storage` на реализациях P0/O1 проверяет реальные RWX/S3, timeout, quota, denied access, restart и повторный доступ. Обязательные cases не skip. Поддержку conditional writes включить в provider certification.
- [ ] Commit `feat: add portable immutable artifact adapters`.

## Task S2: upload и metadata blobs

**Files:** изменить `backend/app/db/models.py`, `backend/app/api/documents.py`,
`backend/app/services/pipeline.py`, `backend/app/services/attachment_store.py`,
`backend/app/services/artifact_response.py`, `backend/app/services/source_store.py`;
создать `backend/alembic/versions/rpl01_artifact_objects.py`,
`backend/tests/test_artifact_upload_commit.py`, `backend/tests/test_artifact_download.py`;
реализовать repository.py. Не редактировать существующие миграции.

**Interfaces:** `ArtifactObject` хранит BlobRef плюс created_at; `ArtifactBinding`
связывает `(owner_type, owner_id, generation_id, logical_name)` с object_id.
Unique logical binding на owner/version; существующие filepath остаются читаемыми
на время миграции. `attach_blob(session: Session, *, ref: BlobRef, owner_type: str,
owner_id: str, generation_id: str | None, logical_name: str) -> None` не commit.
`resolve_blob(session: Session, *, owner_type: str, owner_id: str,
generation_id: str | None, logical_name: str) -> BlobRef | None`.
Owner types: original, source, generation, checkpoint, export, diagnostic_bundle.

`UploadReceipt`: уникальный `(actor_id, idempotency_key)` и request fingerprint,
document_id и состояние. Повтор того же запроса возвращает исходный document ID;
тот же ключ с другими bytes/параметрами — 409. API допускает старый клиент без
ключа; UI получает ключ на одну попытку upload и сохраняет его при retry.
Для existing dedup проверить гонку по digest с текущими allow_similar правилами.

- [ ] Тесты `test_commit_failure_has_no_success_ack`, `test_retry_returns_same_document`, `test_conflicting_key_returns_409`, `test_missing_blob_blocks_download`, `test_unauthorized_cannot_resolve_blob`. Assertions: отсутствует успешный ACK при сбое; один document/receipt; access denied до `store.open`; range корректен, stream закрыт при disconnect.
- [ ] RED: `python -m pytest tests/test_artifact_upload_commit.py tests/test_artifact_download.py -q`.
- [ ] После полного durable put в одной SQL transaction создать document/binding/receipt; R2 добавляет enqueue в эту же transaction. Перед включением R2 новые paths работают только combined; crash между blob и SQL оставляет собираемый orphan. Download использует existing authorization/content disposition/range semantics. Не разрешать purge удалять используемый blob.
- [ ] GREEN plus `python -m pytest tests/test_artifact_response.py tests/test_bulk_export_api.py -q`; Alembic upgrade непустой legacy БД в disposable target, NULL legacy references читаются. Migration revision rpl01 цепляется к фактическому текущему head, merge unrelated migrations разрешается явно.
- [ ] Commit `feat: persist artifact references and upload receipts`.

## Task S3: checkpoints, публикация, GC и потребители файлов

**Files:** изменить `generation_files.py`, `generation_artifacts.py`,
`generation_publication.py`, `generation_store.py`, `generation_cleanup.py`,
`generation_search.py`, `staging.py`, `pipeline.py`, `export_queue.py`,
`processing_scope_cache.py`, `processing_scope_originals.py`, `table_store.py`,
`diagnostics/bundle.py`, `diagnostics/store.py` (все в `backend/app/services/`),
`backend/app/api/documents.py`, `backend/app/db/models.py`;
создать `backend/tests/test_artifact_generation.py`, `backend/tests/test_artifact_gc.py`,
`backend/alembic/versions/rpl01b_artifact_lifecycle.py` для checkpoint/read leases.

**Interfaces:** Publication manifest v2 сохраняет существующие semantic поля и
содержит `artifacts: dict[str, BlobRef]`; v1 filesystem manifest читается только
в legacy compact/migration path. Hash берётся от канонической сериализации manifest.
`load_verified_publication` сохраняет текущий интерфейс, выбирая adapter по version.
`CheckpointBinding` связывает generation/chunk/source signature с завершённым immutable
blob; его pointer меняется в transaction владельца. Ранее созданные generation IDs,
Qdrant point ID formula и active pointer не заменяются другим протоколом.

`ArtifactReadLease` в models.py: object_id, owner_token, expires_at. Lease берётся
в БД до streaming/materialize, renew во время чтения; после потери lease чтение
останавливается. GC блокирует owner/document, перепроверяет references и read leases,
ставит deleting state и только потом вызывает adapter.delete. Reference creation
не может привязать deleting объект. Staging и exports получают те же правила.

- [ ] Тесты `test_incomplete_manifest_cannot_publish`, `test_checkpoint_survives_worker_disk_loss`, `test_gc_skips_bound_or_leased_blob`, `test_attach_races_gc`, `test_missing_artifact_keeps_previous_active`. Assertions: active pointer неизменен при ошибке; checkpoint читается на другом scratch; GC не удалил доступный объект; deleting ref отвергнут.
- [ ] RED: `python -m pytest tests/test_artifact_generation.py tests/test_artifact_gc.py -q`.
- [ ] Перевести все durable файловые категории из инвентаря P0 на bindings/materialize, включая table sidecars и attachment provenance; scratch cache оставлять локальным. Продолжить existing generation_search + canonical hydration filters; не считать fencing готовым до R2. Обновить GC, query/download и diagnostics/export cleanup; оставить tombstones поколений при возможности поздних writers.
- [ ] GREEN plus `python -m pytest tests/test_generation_integrity.py tests/test_generation_publication_service.py tests/test_generation_search.py tests/test_generation_cleanup.py tests/test_export_queue.py -q`; runner storage выполняет corpus roundtrip на RWX/S3 и исчезновение scratch.
- [ ] Commit `feat: move generation artifacts to portable storage`.

## Task S4: миграция без потери старого корпуса

**Files:** создать `backend/scripts/migrate_artifact_storage.py`, artifacts/migration.py,
`backend/tests/test_artifact_storage_migration.py`, `docs/ARTIFACT_STORAGE_MIGRATION.md`;
изменить `backend/scripts/reindex.py`, `scripts/kubernetes/lifecycle.py` только для
вызова явной операции в maintenance и `SECURITY.md`.

**Interfaces:** CLI `--source-manifest PATH --target-manifest PATH --phase {plan,copy,verify,cutover}`;
default phase plan, mutation phases требуют `--apply`. Manifest v1: installation_id,
source/target store IDs, object list with sha256/size/owner/generation, counts,
source_revision, completed copy receipts; credentials только из private config.
Cutover допускается при остановленных writers и полной verify. Retry copy идемпотентен.
До cutover source authoritative; после cutover source read-only, не удаляется этим CLI.

- [ ] `test_resume_copy_without_duplicates`, `test_bad_hash_refuses_cutover`, `test_paths_rebased_for_attachments`, `test_wrong_installation_refused`, `test_unverified_target_never_active`: assert source остаётся выбранным при любом отказе, counts/hash совпадают, storage refs не ссылаются на старый абсолютный путь.
- [ ] RED: `python -m pytest tests/test_artifact_storage_migration.py -q`.
- [ ] Реализовать phases и receipt. Existing generations/staging/scope sidecars сохраняют identity/signatures. Смена storage не меняет embedding model/sparse formula; reindex нужен только как проверенный recovery вариант. Перед миграцией обязателен отдельный backup, после начала новых записей rollback — согласованное восстановление, не обратный env toggle.
- [ ] GREEN и runner storage: legacy nonempty corpus → local adapter → RWX и S3 на отдельных targets, затем download/search/export и прерванный copy/resume. Никакого запуска против рабочего `data/`.
- [ ] Commit `feat: migrate artifact storage with verified cutover`.

## Ключевые assertions для тестов задач

S1: `assert read_bytes == uploaded_bytes`;
`assert ref.sha256 == hashlib.sha256(uploaded_bytes).hexdigest()`;
повтор иной записи по object ID проверяется через `pytest.raises(BlobConflict)`.
S2: `assert retry_document_id == original_document_id`; `assert document_count == 1`;
`assert response.status_code == 409` при конфликте ключа;
`assert store_open_calls == 0` при отказе авторизации.
S3: `assert active_generation_id == previous_generation_id` при неполном manifest;
`assert store.stat(leased_ref) == leased_ref` после GC;
`assert restored_checkpoint_hash == committed_checkpoint_hash` на новом scratch.
S4: `assert active_store_id == source_store_id` после failed verify;
`assert target_manifest_checksums == source_manifest_checksums` после переноса;
`assert duplicate_bindings == 0` после resume.
