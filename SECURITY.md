# SECURITY.md — security model

Reference document describing how the service's security is designed and which trust
boundaries are deliberately chosen. It does not paraphrase the implementation — it points
to the code by file/function name. It is kept up to date together with changes (the rule
lives in `AGENTS.md`).

Splitting with other documents:
- **`PRODUCTION_DEPLOYMENT.md`** — operational checklist "what to check/configure before
  going to production" (secrets, fail-fast checks, DB seeds). Pointers to it appear in the
  sections below.
- **`SECURITY.md`** (this file) — the broader model: threats, trust boundaries, roles,
  data, incident log.

## 1. Threat model and trust boundaries

- **System client** — a corporate user with a role (Viewer/Editor/Admin/Security),
  authenticated through the corporate SSO (Keycloak/IDB). There is no anonymous access in
  production.
- **Only public entry point is the frontend** (Next.js). The browser talks same-origin to
  the frontend; `/api/*` and `/health` are proxied to the backend **server-side** through
  `rewrites()` in `frontend/next.config.js`. The browser never contacts the backend
  directly.
- **Must not be reachable from outside**: backend (`:8000`), Qdrant (`:6333`/`:6334`),
  PostgreSQL (`:5432`), Ollama (`:12400`). In `docker-compose.yml` only `frontend`
  (`8080:3000`) publishes a port.
- **Trust boundary** — the reverse proxy plus the backend authentication layer. Everything
  inside the compose network is considered trusted (backend ↔ Qdrant ↔ DB ↔ Ollama talk
  over HTTP).
- **Main threats**: leaking the document corpus through an open network surface, bypassing
  authorization through a weak/default secret or a wrong `AUTH_PROVIDER`, unauthorized data
  modification through insufficient role checks.

## 2. Authentication and authorization

### Authentication providers (`AUTH_PROVIDER`, `app/config.py`)

| Value | Purpose | Allowed in production |
|---|---|---|
| `disabled` | everything open (anonymous) | **no** — rejected by the validator |
| `simulation` | demo users (`AUTH_SIM_USERS`), `/auth/simulate` | **no** — rejected by the validator |
| `keycloak_oidc` | corporate SSO through IDB/broker (authlib) | yes |
| `direct_ldap` | stub, not implemented | no |
| `custom_client` | stub, not implemented | no |

`validate_auth_provider` in `app/config.py` **fail-fasts** under `ENVIRONMENT=production`,
rejecting `disabled`/`simulation` (`ValueError` before uvicorn starts), as well as a weak
`APP_SECRET_KEY` and `AUTH_SESSION_HTTPS_ONLY=false`.

### Check flow

- `require_user` (`app/auth/service.py`) — current user or `401`/`403`:
  - `disabled` → anonymous `public_user()`;
  - no session → `401`;
  - role unrecognized and `AUTH_DEFAULT_ROLE` empty (fail-closed) → `403`;
  - user on an active blocklist → `403`.
- The role is computed on every request via `GroupRoleAuthorizer` (`app/auth/authorizer.py`):
  group → role mapping from `AUTH_ROLE_GROUPS`, priority `security > admin > editor > viewer`.
- All "business" routers are mounted under a single guard
  `APIRouter(dependencies=[Depends(require_user)])` in `app/main.py:137` — protection at
  the router level, plus explicit `Depends(require_user/require_role)` on endpoints.

### Role matrix

| Role | Rights |
|---|---|
| `viewer` | read-only: list/search/chat, viewing documents, developments, tags, attributes (`require_user`) |
| `editor` | + content changes: document upload/delete, resume/regenerate, development assignment, tag edits, bulk tag editing, removing unused tags from the registry, creating/updating/deleting developments and attribute values (`require_role("editor","admin")`) |
| `admin` | + bulk/destructive operations and jobs: bulk-delete, bulk-regenerate, approve/cancel job (`require_role("admin")`) |
| `security` | blocking/unblocking users (`users.py`) and read-only security audit log (`audit.py`, `require_role("security")`) |

Four-eyes for bulk operations (`app/services/job_queue.py`): a job above the threshold
(`approval_threshold_docs_<type>`) moves to `awaiting_approval` and requires approval by a
**different** administrator — the job creator cannot approve their own job
(`SelfApprovalError` → `403`). Without this, a single admin could run a destructive
operation of any size alone, making the threshold meaningless.

Bulk tag editing (`POST /documents/bulk-tags`, Stage 4a) is a synchronous, non-destructive
operation, so it is available to `Editor`/`Admin`, unlike bulk-delete/bulk-regenerate
(`admin` only). Limit — `BULK_TAGS_MAX_DOCS` (50), deliberately not below the
regeneration threshold (`bulk_regenerate_max_docs=20`).

The only active action of the Security role is the blocklist (`app/services/blocklist.py`),
enforced in `require_user` (a blocked user is rejected even with a valid session).

Reading another user's chat history (Stage 6) is available to the `security` and `admin`
roles through `/chat/admin/history/*` (`require_role("security","admin")`). Opening the
contents of a foreign thread is written to the audit log (`chat_history_view`); viewing
only the session list and one's own history is not logged. An owner sees only their own
history: `session_id` is validated on the backend and bound to the current `user_id`
(`app/services/chat_history.py`), so a foreign thread cannot be substituted
(`store_turn`/`get_thread` raise `ChatOwnershipError`).

## 3. Network topology

- **Only the frontend is published outward**: `docker-compose.yml` — `frontend: "8080:3000"`.
  `backend` and `qdrant` publish no host ports (reachable only inside the compose network).
- **Qdrant — optional compose service** (`profiles: ["local-qdrant"]`). Not started by
  default; the backend connects via `QDRANT_URL` from `.env` — either to the internal
  `http://qdrant:6333` (profile enabled, trusted compose network) or to an external
  corporate Qdrant. In the latter case backend → Qdrant traffic leaves the trusted network,
  so **HTTPS is mandatory** and, if infrastructure policy requires it, `QDRANT_API_KEY` —
  the same principle as for external LLM/embeddings.
- **Postgres — optional compose service** (`profiles: ["local-postgres"]`). Not started by
  default; the backend connects via `DATABASE_URL` from `.env` — either to the internal
  `postgres:5432` (profile enabled, trusted compose network) or to an external corporate
  Postgres. In the latter case backend → Postgres traffic leaves the trusted network — the
  corporate perimeter itself must protect the network connection (TLS/VPN); the password is
  carried in `DATABASE_URL`.
- **Backend bind** (`backend/Dockerfile`): `uvicorn --host ${UVICORN_HOST:-0.0.0.0}` —
  `0.0.0.0` is needed for inter-container communication; isolation is achieved by not
  publishing a host port, not by the choice of bind host. Local startup
  (`scripts/start-all.ps1`) binds `127.0.0.1:18000` (loopback, same machine only; port
  18000 instead of 8000 because the latter falls into the Windows Hyper-V/WSL excluded
  range — see `AGENTS.md`).
- **Local Qdrant binary bind** (`scripts/start-qdrant.ps1`): Qdrant by default listens on
  `0.0.0.0:6333/:6334` without authorization — the script passes
  `QDRANT__SERVICE__HOST=127.0.0.1` (Qdrant env vars take precedence over config), so the
  dev knowledge base is reachable only from the same machine. The local binary listens on
  ports **16333/16334** (not the default 6333/6334 — those fall into the Windows Hyper-V/WSL
  excluded range, see `AGENTS.md`); the isolation profile is unchanged: still loopback-only.
  The compose variant is isolated by publishing no host port (see above).
- **CORS**: `allow_origins=settings.cors_allowed_origins` (`app/main.py`), default is an
  empty list (`CORS_ALLOWED_ORIGINS`). The browser does not call the backend directly
  (server-side rewrites), so cross-origin CORS is unnecessary, and a wildcard would be a
  hole. Cross-origin deployment (frontend on domain A, backend on domain B without a proxy)
  is **incompatible** with the signed-cookie session.
- **TLS**: terminated at the external reverse proxy (nginx/Ingress/ALB) — not in the
  repository (a separate infrastructure layer). Inside the compose network traffic is HTTP
  (trusted network).

## 4. Secrets and configuration

Secrets (not in git, only in a secret store / `.env` with restricted permissions):

| Variable | What it protects |
|---|---|
| `APP_SECRET_KEY` | session cookie signature (`TimestampSigner`, Starlette `SessionMiddleware`); default `dev-secret-change-me` — session forgery → authorization bypass |
| `KEYCLOAK_CLIENT_SECRET` | confidential OIDC client |
| `DATABASE_URL` | PostgreSQL password |
| `POSTGRES_PASSWORD` | superuser password for the compose `postgres` service (`local-postgres` profile, dev default `okf_dev_pg`) |
| `QDRANT_API_KEY` | access to a corporate Qdrant (if it requires authorization) |
| `LLM_API_KEY`, `EMBEDDING_API_KEY` | access to external LLM/embedding providers |

Invariants enforced by `validate_auth_provider` in `app/config.py` (production):
`AUTH_PROVIDER ∉ {disabled, simulation}`; `APP_SECRET_KEY` random and `>= 32` chars;
`AUTH_SESSION_HTTPS_ONLY=true`. Full checklist — `PRODUCTION_DEPLOYMENT.md`.

## 5. Data and privacy

- **Stores**: metadata and processed knowledge — PostgreSQL/SQLite (`app/db/`):
  `documents`, `document_tags`, `document_chunks` (full chunk text), `okf_concepts`
  (full concept text), `okf_attachments` (attachment metadata). Binaries
  (originals, attachments) and temporary artifacts — FS: `data/uploads` (including
  `uploads/<doc_id>/attachments/`), `data/staging`, `data/cache`, `data/debug`;
  `.md` bundles (`data/okf_bundles`) — a derived projection (export), not the working
  store. Vectors/mini-payload — Qdrant (slim, without full text — `content` is hydrated
  from the DB by the natural key `(doc_id, slug)` / `(doc_id, chunk_index)`).
- **Encryption at rest**: not used — relies on host disk/volume and DBMS encryption
  (see "Known limitations / accepted risks").
- **Encryption in transit**: only at the external reverse proxy (HTTPS). Internal network —
  HTTP (trusted).
- **Soft delete / trash**: implemented (Stage 4a.2) — a two-layer model:
  `deleted_at`/`deleted_by` in `documents` (Postgres) + the payload flag `deleted=true`
  on all points of the document in Qdrant (via `set_payload`, without physical
  `Delete Points`). Every search path to Qdrant (main RAG search from chat,
  `/search`, graph expansion) must add `must_not: {deleted: true}` through the single
  wrapper `vector_store._not_deleted()` / `_build_search_filter()` — filter discipline in
  one place, so a new search scenario cannot forget the condition. Additional protection:
  `/chat` and `/search` through the unified filter (`app/services/search_filter.py`)
  drop hits whose document is marked deleted in the DB (closes the race between
  `deleted_at` and a not-yet-synced payload), as well as orphan hits whose document is
  absent from the DB entirely (recovered during physical purge, finalization failure).
  Restore from trash clears both flags without re-embedding. Final physical deletion
  (Qdrant `Delete Points` + `DELETE` from the DB — including `document_chunks` and
  `okf_attachments` rows — + files) happens only in a background task after
  `trash_retention_days` (14 days) expire; until then restore is available at any
  moment — including racing a running purge: physical deletion starts with an atomic
  claim of the DB row with the precondition `deleted_at IS NOT NULL`
  (`registry.delete_if_deleted`, FOR UPDATE on Postgres; `pipeline.remove_if_deleted`),
  so a document restored after the purge query survives cleanup entirely
  (DB + files + points). Auto-purge writes to the audit log as a system action
  (`document_auto_delete`, `user_id="system"`); user delete/restore are
  `document_delete` / `document_restore` / `document_bulk_restore`. On restore (including
  bulk) deduplication runs against active documents
  (`find_active_duplicates_for_document`) — a conflict returns 409
  `code=duplicate` (the bulk path skips conflicting documents into `conflicts`);
  force-restore is available via explicit `?force=true`.

  **Deduplication against the trash — a deliberate decision (2026-09-01), not a bug**:
  a twin of the uploaded file sitting in the trash does NOT block the upload
  (`file_hash_exists` only looks at active documents). The user must not depend on a
  state of someone else's trash they cannot see. The upload goes through and the user
  is shown an informational message ("similar document in trash", `duplicate_in_trash`
  in the response + an audit record in `new_value`); the version conflict is resolved
  when the twin is restored (restore with dedup check). Temporary coexistence of two
  identical documents (one in trash) is an expected state, not something to "fix" as a
  bug. Deduplication candidates (level 2/3, flag `has_duplicates`) are likewise not
  polluted by documents from the trash.
- **Chat history** (Stage 6): stored in `chat_sessions`/`chat_messages`
  (PostgreSQL/SQLite). Active history is kept indefinitely; manual thread deletion is a
  soft delete (`deleted_at`/`deleted_by`); physical cleanup is a background task
  (`app/services/chat_history.py:start_chat_purge_loop`) after `chat_history_retention_days`
  (90 days) expire, recorded as `chat_history_auto_delete` (`user_id="system"`). Messages
  store a snapshot of sources (`sources`) as of the answer — it does not "expire" on
  reindexing.
- **Retention**: `audit_retention_days=365` (`app/config.py`) sets the audit log horizon,
  but automatic cleanup is not implemented (see "Known limitations / accepted risks").
- **Audit log** (`app/services/audit.py`): append-only (only `append`/`query` exist, no
  update/delete). It records mutating actions (upload/delete/regenerate, bulk operations,
  development change, tag edits — including one record per document affected by a bulk tag
  edit, removing/cleaning tags from the registry (`tag_delete`/`tag_cleanup`), blocks,
  developments/attributes CRUD, viewing another user's chat history (`chat_history_view`)
  and auto-purge of chat history (`chat_history_auto_delete`)) with `username`, `target_id`,
  `old_value`/`new_value`, `ip_address`.
  Only the `security` role reads it (`app/api/audit.py`). For PostgreSQL production, a
  dedicated service account with INSERT-only privileges is recommended
  (see `README.md`, "Audit log hardening").

### Collaboration and concurrent data conflicts

Concurrency model: **optimistic locking — only for the developments registry**; all other
data works last-write-wins, deliberately accepted as non-dangerous (rationale below).

**Protected (optimistic locking via `version`):**
- `developments` (`app/services/development_registry.py`, migration `e5a6b7c8d9f0`):
  integer `version`. `PATCH`/`DELETE /developments/{id}` require `version` and return a
  structured `409` with `code`: `version_conflict` (body carries the current record in
  `current` — the client bumps the version and resubmits without re-typing input) or
  `duplicate_number` (unique `number` collision). The version check is atomic at the DB
  level — a conditional `UPDATE … WHERE id = :id AND version = :version` with `rowcount`
  control (not read-then-write in Python), so the "rename vs delete" race and "two editors
  at once" are detected regardless of the transaction isolation level. For `DELETE` the
  version is first "claimed" by an atomic increment (FK requirement: documents must be
  detached before the row is deleted), then documents are detached and the row is deleted.

  Note (integrity): when a development `number` changes, the literal user tag equal to the
  old number is **not** renamed automatically — the canonical search mechanism for a
  development number is the denormalized `dev_tags` projection (updated in the background
  via `dev_sync`), while a tag is free text edited only by explicit user action. When a
  development is deleted, `dev_tags` of detached documents are cleared (see the 2026-09-01
  log entry).

**Not protected (last-write-wins), assessed as non-dangerous:**
- Tag/document-assignment edits (`PATCH /documents/{id}/tags`,
  `POST /documents/{id}/development`, `POST /documents/bulk-tags`): concurrent edits of
  one document are not detected — the last write wins. Non-dangerous: the operation is
  essentially idempotent (replacing a tag set / a single field), the projection sync to
  Qdrant re-reads the current DB state (`app/services/document_tag_service.py` is
  serialized per document with a dirty flag), and the consequence is an overwrite of one
  field, recoverable by editing again.
- Trash/document restore: soft delete is an idempotent flag set with an explicit
  precondition check (`409` "already in trash"); the "delete vs restore" race yields either
  another `409` or a harmless flag overwrite.
- Tag/attribute registries (`tags`, `attribute_values`): operations over the name pool;
  uniqueness is guaranteed by the DB, deletion is guarded by a "value in use" check
  (`409`). The race is rare and leads only to a `409`, not data corruption.
- Pipeline statuses and background sync: edits are serialized in-process (thread locks +
  row-level UPDATE); the Qdrant sync is idempotent (reads the DB at execution time);
  "latest state wins" is correct here by construction.
- Chat history: threads are bound to their owner (`ChatOwnershipError` on substitution),
  records are append + soft delete only — there is no inter-user edit conflict.

Precondition checks in `app/api/documents.py` ("already processing", "already in trash")
are not atomic (check-then-act), but the operations behind them are idempotent, so the race
does not corrupt data.

## 6. Known limitations / accepted risks

- **No encryption at rest** — protection is delegated to the infrastructure (disk/volume/DBMS).
- **Internal traffic is HTTP** — trusted compose network; TLS only at the external proxy.
- **No automatic audit_log cleanup** — the horizon is set (`audit_retention_days`), cleanup
  is not automated.
- **`direct_ldap` / `custom_client` providers** — stubs; unusable in production
  (the validator formally allows them, but they are not implemented — the only working
  production provider is `keycloak_oidc`).
- **`AUTH_DEFAULT_ROLE`** when non-empty hands the default role to unknown users — in
  production an empty value is recommended (fail-closed).
- **Rate limiter** (`app/services/rate_limiter.py`) — in-memory sliding window; multi-worker
  production needs an external backing (Redis/DB).
- **In-memory session secrets** — the session is in a signed cookie (not stored on the
  server): compromising `APP_SECRET_KEY` compromises all sessions.

## 7. Security change log

### 2026-09-07 — Local-only logout when the session has no id_token
Change: `KeycloakOidcProvider.logout` (`backend/app/auth/providers/keycloak_oidc.py`) no
longer redirects the browser to the Keycloak `end_session_endpoint` when the app session
carries no `id_token` (a session created before id_token storage, or an IdP that issued
none). RP-Initiated Logout without `id_token_hint` is honored by Keycloak only while its
SSO session cookie is live; otherwise the endpoint renders the error page
"Missing parameters: id_token_hint" and the user is stranded on a Keycloak error instead
of returning to the app. Now, without `id_token`, the local session is cleared and the
endpoint redirects to `/` (login gate) — a local-only logout; the request is logged with a
fact-only message (no session contents). The normal path is unchanged: sessions created
after the fix always carry `id_token`, and logout performs full RP-Initiated Logout with
`id_token_hint`. Security nuance of the fallback: if the user's Keycloak SSO session is
still alive at that moment, it remains (the next sign-in silently re-authenticates via
SSO) — app access is still revoked immediately, and the full-SSO-logout semantics apply to
all current sessions.

### 2026-09-07 — Attachment marker blocks no longer leak absolute local paths
Change: `blocks_to_markdown` renders the attachment marker's file reference as a portable
relative path (`attachments/<name>`, mirroring image blocks) instead of the raw absolute
`saved_path` that parsers place into block metadata. Already-ingested rows were rewritten
by the one-shot `backend/scripts/fix_attachment_paths.py` in two textual variants:
`document_chunks.content` / `okf_concepts.content` marker segments
`(файл: <absolute path>)` and legacy pre-SSOT markdown hyperlinks
`[label](file:<absolute path>)` became `(файл: attachments/<name>)` / `](attachments/<name>)`
(only absolute paths were touched; `content_hash` of edited chunks recomputed). The affected
chunk points were deleted and re-embedded from the DB via `VectorStore.backfill_chunks`
(single canonical dense/sparse formula — no third copy of the indexing logic). Concept points
were not re-embedded: the slim payload does not store concept content (it is hydrated from
`okf_concepts`), and no chunk/DB residue of the fixed rows remained. Reason: a marker-derived
concept or chunk surfaced the uploader's machine-specific local path
(`C:\Users\<user>\...\uploads\<doc_id>\attachments\<name>`) to any authenticated viewer via
`/documents/{id}` content and into the search index — low-risk disclosure of the local
processing environment and a machine-specific string meaningless outside the originating host.

### 2026-09-06 — Destructive stop-words clear gate + ui-dictionary 404 semantics
Change: (1) an empty `replace`-import of stop words (`mode=replace` + `words=[]` with a
non-empty current set) — effectively a mass destructive clear of a language's set — now
requires an explicit server-side confirmation `confirm_empty_replace=true` (without it
`applied=false` + `requires_empty_replace_confirmation`; the UI shows a "Delete all N stop
words" button and a confirm with locale/kind/count — a direct API call without the UI also
cannot silently clear the set); `audit_log` (action `stopwords_import`, meta) additionally
records `empty_replace=true` and `removed_count` — quick extraction of clears for
information security. Merging an empty list remains a no-op (non-destructive).
(2) `POST /admin/locales/{code}/ui-dictionary/import` (preview branch) and
`GET .../ui-dictionary/history` for a nonexistent locale now return 404 instead of 200
(history) — removed the masking of caller errors due to which the frontend "[object
Object]" URL bug looked like "backend unavailable". Reason: the class of dangerous bulk
operations must follow the preview → explicit-confirmation contract (like four-eyes for
documents), and the 404/200 ambiguity hindered incident diagnostics. Permissions unchanged
(admin/editor as before).

### 2026-09-06 — Local frontend moved to port 16300
Change: host port 3000 (local Next dev server) fell into the Windows Hyper-V/WSL excluded
range (`netsh interface ipv4 show excludedportrange` — blocks change per boot), `next dev`
failed with `EACCES: permission denied 0.0.0.0:3000`. Local startup (`scripts/start-all.ps1`)
now runs `next dev -p 16300` (above the dynamic TCP range 1024–15000, HNS does not reserve
it). **16300 is the host port of the local dev server; 3000 remains only the internal port
of the frontend docker-compose container (`8080:3000`) and is unchanged.** The Keycloak
client SSO redirect/post-logout URIs (docker-volume `keycloak-data`) were moved from
`localhost:3000` to `localhost:16300` — otherwise `invalid_redirect_uri`. Security profile
unchanged: only the frontend (compose) is still published outward; local services are
loopback.

### 2026-09-06 — Runtime override of UI dictionaries (Stage 7, phase C)
Change: table `ui_dictionaries` (locale, version, data JSON) + Alembic `9d4e5f6a7b8c`;
the current version lives in `locales.ui_dictionary_version`. Admin import of a JSON
dictionary (two-step preview→confirm) with validation: keys ⊆ the canonical manifest
(`backend/app/i18n/ui_keys.json`, generated by the node script
`frontend/scripts/export-ui-keys.mjs`; drift is caught by the i18n test) and {param}
placeholders match ru. `GET /api/i18n/{locale}` (require_user) returns the current override
with an ETag by version; version history + rollback. The audit log gained actions
`ui_dictionary_import` / `ui_dictionary_rollback` (target_type `locale`);
`ACTION_TYPES`/`EXPECTED_ACTION_TYPES` updated. Reason: editing UI translations is a
state-changing admin-level operation requiring key validation (protection from typos that
break interpolation) and an append-only history (rollback). A runtime dictionary holds
displayed UI strings, not document content; it carries no data disclosure, so
`GET /api/i18n` needs no extra scoping beyond require_user.

### 2026-09-06 — Surrogate tag_id and registry translations (Stage 7, phase B)
Change: `tags.name` (textual PK) → `tags.id` (surrogate) + `canonical_text` (unique) +
`canonical_locale` + `deleted_at`; `document_tags.tag` → `document_tags.tag_id` (FK);
new tables `tag_translations` / `development_translations` /
`attribute_value_translations` (Alembic `8c3d4e5f6a7b`; for dev-Postgres — companion
`scripts/migrate_tags_to_id.py` because of the create_all Alembic quirk). The Qdrant
payload and `okf_concepts.tags` remain canonical text — no reindex needed. Deleting a tag
from the "pool" is now a soft delete (`tags.deleted_at`); `document_tags` links are left
untouched (a trashed document keeps its tag until purge/restore — the behavior of the
06.09.2026 bug is preserved). The audit log gained actions `tag_translation_update`,
`tag_translation_review`, `translations_backfill`; `ACTION_TYPES`/
`EXPECTED_ACTION_TYPES` updated. New mutating endpoints:
`PATCH /tags/{id}/translations/{locale}`, `POST /tags/bulk-review` (editor/admin),
`POST /tags/translations/backfill` (admin). Reason: a textual tag PK is a displayed,
localizable string, not a stable identifier; a surrogate id + translations let names be
localized without changing the wire/search identifier. Deviation from the plan: backfill
runs synchronously in the admin request (bulk LLM semaphore) instead of through the job
queue — the queue is document-centric, the operation is non-destructive and reversible via
review; LLM load does not compete with the interactive chat.

### 2026-09-06 — Language support and stop words (Stage 7, phase A)
Change: added tables `locales` (code, name, status draft|active|disabled,
ui_dictionary_version) and `stopwords` (locale, word, kind bm25|marker) + Alembic
migration `7a1b2c3d4e5f` (dev — `create_all` + idempotent `ensure_seeded`, prod — Alembic
only). New admin area "Language support" (`/api/admin/locales/*`, role `admin`): locale
CRUD, activation/deactivation, stop-words import/edit/rollback (two-step preview→confirm,
single transaction + synchronous cache invalidation), read-only probe. The audit log gained
actions `locale_create/update/activate/disable`, `stopwords_import/update/rollback` and
target_type `locale`; `ACTION_TYPES`/`EXPECTED_ACTION_TYPES` updated. Reason: stop words
moved from two desynchronized code constants (`sparse._STOPWORDS`,
`context_builder._MARKER_STOPWORDS`) into data with a single service and change audit;
language management is a state-changing operation requiring append-only recording. Stop
words apply only on the query side (the sparse.py index formula is frozen to constants —
no reindex needed), so editing stop words does not affect stored vectors and needs no extra
scoping beyond the `admin` role.

### 2026-09-08 — UI language activation gate removed; en-copy auto-seed (Stage 7, phases A/C)
Change: the activation gate "locale must be present in the frontend manifest
(`SHIPPED_LOCALES`)" was removed from `locale_service._validate_activation`; activation now
requires only a non-empty bm25 stop-word set. On successful activation, a locale without a
runtime UI dictionary gets version 1 seeded as a full en copy
(`ui_dictionary.seed_english_copy`, source `backend/app/i18n/ui_en.json` generated from
`en.js`); the seed reuses the existing audited import path (`ui_dictionary_import`) and never
overwrites an existing override. Reason: a language should be activatable for the corpus
(stop-word union, `request_locale`) without a frontend UI release — "active" no longer
implies "UI language"; the UI toggle still filters by the static frontend manifest, so an
activated language without a manifest entry never reaches users. Authorization boundary is
unchanged (activation and the seed remain `admin`-role-only); the seed is a side effect of an
already-audited admin action and is recorded with the same `ui_dictionary_import` action
(note "auto: en copy on activation").

### 2026-09-05 — Qdrant v2 + bundles as export (Stage 2b, phases 4–5)
Change: the Qdrant collection was rebuilt (`okf_knowledge_base_v2`) from PostgreSQL with
logical point_ids `uuid5("okf:concept:{doc_id}:{slug}")` / `uuid5("okf:chunk:{doc_id}:{chunk_index}")`
(decoupled from the absolute data_dir path) and slim payload (no `content`/`filepath`/
`section_title` — full text is hydrated from the DB by natural key). `.md` bundles stopped
being the working state (`okf_write_bundles=false` by default) and are produced only by
explicit export: new endpoint `POST /documents/{id}/export-okf` (+ CLI `scripts/export_okf.py`)
assembles a YAML/Markdown package from the DB. The audit log gained the action
`document_export` (recorded on every export, target=document) — `ACTION_TYPES`/
`EXPECTED_ACTION_TYPES` updated. Reason: closing the last read dependency on files
(search/chat/OKF/chunks/attachments/reindex read the DB); an export is a portable artifact,
not a second source of truth. Export is available to any authenticated user (like reading)
— no separate scoping is needed, but the action is logged.

### 2026-09-05 — PostgreSQL becomes SSOT: `document_chunks` + `okf_attachments` activation (Stage 2b, phase 0)
Change: added table `document_chunks` (full chunk text, order, section title, hash —
canonically in the DB, not only in FS bundles/Qdrant payload); `okf_attachments` is no
longer a "dead" table (previously populated only by a one-off migration script) and gains
working columns (`content_type`, `size`, `sha256`, `is_processable`, `extraction_status`,
`processed_at`, `error`) with uniqueness on `(doc_id, saved_path)` and a relative
`saved_path`; `okf_concepts` gained provenance (`generated_at`, `model_id`,
`prompt_version`). Binary attachments remain in the local FS. Reason: closing consistency
gaps — attachments existed only in YAML/frontmatter, chunks only in files and Qdrant
payload; PostgreSQL is established as the single source of truth for structured knowledge.
Consequence for retention: physical trash cleanup (`registry.delete`/`delete_if_deleted`)
now also deletes `document_chunks` rows; soft delete/restore and atomic-claim purge
semantics are unchanged.

### 2026-09-01 — Anti-DoS in doc-parser (attachment chains, zip bombs, scans)
Change: recursive attachment parsing is bounded — `MAX_ATTACHMENT_DEPTH=3` (levels 1–2 are
parsed, level 3 is only a marker block), `MAX_ATTACHMENT_PAYLOAD=50MB` per attachment (and
per raw-data disk write), `AttachmentBudget` (200 MB cumulative) for a document's unpacked
attachments; fallback rendering of scanned PDF pages is bounded by `_RENDER_PAGE_LIMIT=200`
(text is extracted from all pages). Exceeding limits does not crash the pipeline — the
attachment is marked with a note. Reason: chains of OLE packages (docx-in-docx) and zip
bombs multiplied the work/files per uploaded file without bound — a DoS on the
authenticated upload path (verified by tests: depth, budget, payload limit, render).

### 2026-09-01 — Deduplication against the trash: policy scoping
Change: (1) Level-1 dedup (`file_hash_exists`) blocks upload only by an ACTIVE twin; a twin
in the trash does not block — the upload proceeds with an informational message
(`duplicate_in_trash` in the response + audit). Previously a 409 "already uploaded" could
point to a document invisible to the user (in someone else's trash), permanently blocking a
re-upload. (2) Dedup candidates (level 2/3, `has_duplicates`) are not polluted by documents
from the trash. (3) `bulk_restore` runs the dedup check against active documents, like a
single restore — previously the bulk path bypassed the 409 conflict; conflicting documents
are skipped into `conflicts` (restore them via single restore with `?force=true`). The
deliberate temporary state "two identical documents, one in trash" is documented in §5 as
NOT a bug.

### 2026-09-01 — Closed audit log gaps
Change (3 items): (1) `POST /documents/{id}/detect-development` — a mutation of
`development_id`/`suggestion` by auto-detection now writes `document_development_set`
(meta `source: "auto"`, old/new development_id); previously the path had no record at all.
(2) `users/{id}/block|unblock` and `jobs/{id}/approve|cancel` now record `ip_address`
(SECURITY.md §5 promises it for all records); the unblock record is moved after the
`n > 0` check — a phantom action against a nonexistent block is no longer logged.
(3) A tag edit that changes the development assignment (tag = number) records
`development_id` in old/new_value — previously a development change was invisible in the
`document_tags_update` record. Reason: the audit log must cover ALL mutations with full
context — the listed paths violated §5.

### 2026-09-01 — Stored XSS: bundle attachments no longer rendered inline
Change: `GET /documents/{doc_id}/okf/attachments/{filename}` serves attachments with
`Content-Disposition: attachment` + `X-Content-Type-Options: nosniff` (previously — inline
with a guessed media_type). Reason: an attachment's extension is derived from the name of
the embedded object inside an uploaded DOCX/XLSX/PDF (i.e. uploader-controlled), so an
`.svg` attachment with an embedded script was served same-origin as `image/svg+xml` and
executed in the user's session on direct navigation. Image rendering in the frontend is an
`<img>` subresource — Content-Disposition does not affect loading there; the UI is
unchanged.

### 2026-09-01 — Path traversal in the `/{doc_id}` content-serving routes
Change: all `GET /documents/{doc_id}` routes that touch the file system (`list_okf_files`,
`list_chunks`, `get_document_fulltext`) and, for consistency, `get_document`/
`download_document` validate the id format via `_valid_doc_id` (16 hex) before any disk
access. Reason: three endpoints were FS-first — they built the path `okf_dir / doc_id`
before the registry gate, so an id like `..` or an absolute Windows path gave an
authenticated user a listing of an arbitrary `.md`-file directory, reading of foreign chunks
and writing `_files.json` to an arbitrary directory. Registry-first routes were safe, but
format validation is a single discipline for all `{doc_id}` routes.

### 2026-09-01 — Trash restore-vs-purge race and orphan search hits
Change: physical trash cleanup (`pipeline.remove_if_deleted`) starts with an atomic claim —
deleting the DB row with the precondition `deleted_at IS NOT NULL`
(`registry.delete_if_deleted`, FOR UPDATE on Postgres). Previously purge selected expired
ids and then unconditionally deleted Qdrant/files/row: a document restored in that window
was irreversibly physically lost. Deleting the DB row first became safe only together with
the second change: a unified DB-side visibility filter in `/chat` and `/search`
(`services/search_filter.py`) now drops not only hits of deleted documents but also orphan
hits whose document is absent from the DB (previously such hits passed — `(None or {}).get(...)`
interpreted "no document" as "not deleted"). Reason: purge is the only irreversible step of
the lifecycle, and restoring at the moment of cleanup must not cost data; orphan points
(finalization failure) must not reach results.

### 2026-09-05 — Local backend moved to port 18000
Change: port 8000 (local Uvicorn) fell into the Windows Hyper-V/WSL excluded range
(`netsh interface ipv4 show excludedportrange` — blocks change per boot); the backend
failed to start with `winerror 10013` (bind `('127.0.0.1', 8000)`). Local startup
(`scripts/start-all.ps1`) now listens on `127.0.0.1:18000` (above the dynamic TCP range
1024–15000, HNS does not reserve it); `BACKEND_URL` in `.env` and the frontend-rewrites
fallbacks = `http://localhost:18000`. docker-compose is unaffected — inside the network it
remains `backend:8000`. Security profile unchanged: still only the frontend is published
outward, the backend binds to loopback. `start-all.ps1` gained an early check of fixed host
ports for reservations (a clear error instead of `winerror 10013`).

### 2026-09-02 — Local Qdrant moved to ports 16333/16334
Change: port 6333 (Qdrant REST) fell into the Windows Hyper-V/WSL excluded range
(`netsh interface ipv4 show excludedportrange` — blocks change per boot); the binary failed
to start with `os error 10013`. `scripts/start-qdrant.ps1` now sets
`QDRANT__SERVICE__HTTP_PORT=16333` / `QDRANT__SERVICE__GRPC_PORT=16334` (above the dynamic
TCP range 1024–15000, HNS does not reserve them); `QDRANT_URL` in `.env` =
`http://localhost:16333`. Reason for a security-log entry: network parameters (ports) of the
local installation changed, although the security profile did not — the binary still binds
only to `127.0.0.1`.

### 2026-09-01 — Local Qdrant bound to loopback
Change: `scripts/start-qdrant.ps1` passes `QDRANT__SERVICE__HOST=127.0.0.1` to the launched
binary (verified: `:6333`/`:6334` listen on `127.0.0.1`). Reason: Qdrant binds `0.0.0.0`
without authorization by default, and the dev start launches it as a local binary (not in
compose) — the knowledge base with full document texts was open to the LAN, contradicting
§1 ("Qdrant must not be reachable from outside").

### 2026-09-01 — Four-eyes: banning task self-approval
Change: `JobQueue.approve` (`app/services/job_queue.py`) rejects approving a job by its
creator (`SelfApprovalError` → `403` in `app/api/jobs.py`); on rejection the job status is
unchanged and another administrator can approve. Reason: the `approval_threshold_docs_<type>`
threshold promised "approval by a second administrator", but without the
`approver != created_by` check a single admin could submit a bulk-delete of any size and
immediately approve it themselves — four-eyes existed only on paper.

### 2026-09-01 — Development deletion allowed for editor + integrity on delete
Change: `DELETE /developments/{id}` is available to the `editor` role (was `admin` only);
rights aligned with `POST`/`PATCH /developments` (editor/admin). At the same time an
integrity gap was closed: when a development was deleted its documents were detached in the
DB, but their `dev_tags` projection in Qdrant was not cleared (detached documents kept
matching by the deleted development's number) — now document ids are captured before
detachment and their `dev_tags` are cleared in the background; the detachment fields
(`development_suggestion`, `development_confirmed_by`) are reset to a manual detachment.
Frontend: deletion confirmation with a counter; with `documents_count > 5` — typed
confirmation (enter the development number); a warning when changing a development number
with bound documents. Reason: an editor could already create/rename developments — the
"admin only" delete was inconsistent; plus an unobvious search desync after deletion.

### 2026-09-01 — Optimistic locking of the developments registry
Change: `developments` gained an integer `version` (migration `e5a6b7c8d9f0`); `PATCH`/
`DELETE /developments/{id}` require `version` and return a structured `409` with `code` —
`version_conflict` (with the current record in `current`) or `duplicate_number`. On
`version_conflict` the frontend keeps the edit form open and resubmits with the updated
version without re-typing input. Reason: collaborative editing of the developments registry
was last-write-wins — concurrent edits silently overwrote each other and the
"rename vs delete" race lost changes. Other data (tags/document assignment, trash,
tag/attribute registries, chat history) stays last-write-wins — assessed as non-dangerous
(see §5 "Collaboration and concurrent data conflicts").

### 2026-08-31 — Chat history (Stage 6)
Change: persistent chat history introduced (`chat_sessions`/`chat_messages`) with soft
deletion and auto-purge. Privacy: an owner sees only their own history, `session_id` is
bound to `user_id` on the backend (`ChatOwnershipError` on attempting to substitute a
foreign thread); foreign history is readable only by `security`/`admin`, opening a thread
is logged (`chat_history_view`), the session list and one's own history are not. Retention:
active history is indefinite, deleted threads are physically purged in the background after
`chat_history_retention_days` (90), recorded as `chat_history_auto_delete` (system).
Reason: history previously existed only in the client's memory; managed access was needed
(owner + auditable view for information security) with the same retention model as the
document trash (4a.2).

### 2026-08-31 — Trash / soft delete (Stage 4a.2)
Change: document deletion is no longer irreversible — a two-layer trash was introduced
(`documents.deleted_at`/`deleted_by` + Qdrant payload `deleted=true`), a mandatory
`must_not: {deleted: true}` filter on all search paths through a single wrapper, restore
without re-embedding, and background auto-purge after `trash_retention_days`. New audit
types: `document_restore`, `document_bulk_restore`, `document_auto_delete` (system).
Reason: previously `DELETE` hard-deleted a document with no undo window; the risk of an
"accidental" deletion is removed by a managed trash. Security nuance — the `deleted` filter
discipline (leakage of a "deleted" document into chat if the condition is skipped) is
closed by the centralized wrapper plus the DB-side cutoff in `/chat` and `/search`.

### 2026-08-31 — Qdrant optional (compose profile) + single QDRANT_URL
Change: the `qdrant` service in `docker-compose.yml` is marked with the `local-qdrant`
profile (not started by default); the hardcoded `QDRANT_URL=http://qdrant:6333` in the
backend's `environment` was removed — the URL comes only from `.env`. An optional
`QDRANT_API_KEY` was added (passed to `QdrantClient(api_key=...)` only if set). Application
code does not distinguish local and corporate Qdrant. Security nuance: with an external
Qdrant the traffic leaves the trusted compose network — HTTPS is mandatory and (per policy)
`QDRANT_API_KEY`; recorded in §3 and §4. Qdrant still publishes no host port.

### 2026-08-31 — Postgres optional (compose profile) + single DATABASE_URL
Change: a `postgres` service (`postgres:17`) with the `local-postgres` profile was added to
`docker-compose.yml` (not started by default). The backend connects via `DATABASE_URL` from
`.env` — either to the internal `postgres:5432` (profile enabled, trusted compose network)
or to an external corporate Postgres; application code does not distinguish these cases.
`DATABASE_URL_DEV` (SQLite) is kept as a third zero-config option. The compose service's
superuser password is `POSTGRES_PASSWORD` (dev default `okf_dev_pg`). Security nuance: with
an external Postgres the traffic leaves the trusted compose network — network protection is
delegated to the corporate perimeter (TLS/VPN); the password is in `DATABASE_URL`. Postgres
publishes no host port. Symmetric to Qdrant (§3, §4) and allows bringing up a fully local
stack with one command (`docker compose --profile local-qdrant --profile local-postgres up`).

### 2026-08-31 — Development assignment on upload (Stage 4a.1)
Change: `POST /documents` accepts an optional form parameter `development_id`
(pre-assignment of a development on upload from chat context). It lands in the audit log
within the existing `document_upload` record — the `new_value.development_id` field is
added only on explicit assignment (the `new_value` form without assignment is unchanged).
No new action_type was introduced; permissions are the previous
`require_role("editor","admin")`. No impact on trust boundaries/network topology.

### 2026-08-31 — Open network surface in production
Found: `AUTH_PROVIDER=disabled/simulation` were not forbidden in production
(`app/config.py` — the validator checked only the secret and the HTTPS cookie, not the
provider); backend/Qdrant were published outward (`docker-compose.yml` `ports` for `:8000`,
`:6333`, `:6334`); `CORS allow_origins=["*"]` (`app/main.py`).
Fixed: the fail-fast validator forbids `disabled`/`simulation` in production; host
publication of backend/Qdrant removed (only frontend outward); CORS reduced to the empty
allow-list `CORS_ALLOWED_ORIGINS` (the browser reaches the backend only through the
frontend's server-side rewrites); the backend binds `127.0.0.1` in the local script.
