# PDF Provider Isolation and Bundled Production Storage Design

**Date:** 2026-09-15
**Status:** Approved design; implementation plan pending
**Target license:** MIT

## 1. Purpose

This change has two related delivery goals:

1. Remove PyMuPDF/MuPDF completely from the standard project dependencies and
   production image while preserving PDF text extraction, embedded image and
   attachment handling, and scanned-page rendering through a permissively
   licensed implementation.
2. Support two explicit production storage modes: corporate external
   PostgreSQL/Qdrant and bundled PostgreSQL/Qdrant containers running on the
   same knowledge-base server.

The existing public parser contract remains stable:

```python
parse_document(
    path,
    filename=None,
    attachments_dir=None,
    depth=0,
    budget=None,
) -> list[Block]
```

Backend pipeline callers, backfill scripts, and recursive embedded-document
parsing must not need to know which PDF implementation is installed.

## 2. Current State and Problems

### 2.1 PDF parsing

`doc-parser/src/docparser/pdf_parser.py` currently combines orchestration and
library-specific work:

- pypdf reads pages, extracts text and embedded images, and enumerates PDF
  attachments;
- PyMuPDF renders an otherwise empty page to JPEG;
- the parser converts those results to the shared `Block` contract and applies
  attachment and rendered-page limits.

PyMuPDF is installed unconditionally by both `doc-parser/pyproject.toml` and
`backend/requirements.txt`. A runtime flag therefore cannot satisfy the
requirement that PyMuPDF and MuPDF be absent from the server.

### 2.2 Production storage

`docker-compose.yml` already contains PostgreSQL and Qdrant services behind two
independent development-oriented profiles, `local-postgres` and
`local-qdrant`. The production guide explicitly directs operators to external
databases. This leaves bundled production without a supported startup command,
configuration contract, backup/restore procedure, or acceptance test.

Independent profiles also allow mixed local/external combinations. Those
combinations are not part of the supported production matrix.

## 3. Decisions

### 3.1 PDF stack

The standard PDF stack is:

- pypdf for document structure, text, embedded images, and PDF attachments;
- pypdfium2/PDFium for full-page rendering when pypdf returns neither text nor
  embedded images for a page.

PyMuPDF is removed from runtime requirements, optional extras, imports,
container layers, and standard CI environments. The repository may contain a
maintenance document explaining how to reintroduce a separately licensed
provider, but it does not ship a PyMuPDF adapter or dependency.

The standard Docker build has only the permissive provider. The provider
boundary is designed so a future separately maintained build target can add a
different implementation without modifying backend callers.

### 3.2 Project license

The project moves from `AGPL-3.0-or-later` to MIT after the project owner has
confirmed the right to relicense all accepted contributions. Previously
published releases remain under their original license.

Third-party components retain their own licenses. Release artifacts include a
`THIRD_PARTY_NOTICES` file and a machine-readable SBOM. In particular, binary
PDFium distributions must retain the license notices shipped with the selected
pypdfium2 wheel.

### 3.3 Storage deployment modes

Exactly two production storage modes are supported:

| Mode | PostgreSQL | Qdrant |
|---|---|---|
| `external` | External endpoint | External endpoint |
| `bundled` | Container on the application server | Container on the application server |

Mixed production modes are rejected before migrations or application startup.
Application code continues to use `DATABASE_URL`, `QDRANT_URL`, and
`QDRANT_API_KEY`; the deployment layer selects and validates the mode.

## 4. PDF Component Design

### 4.1 Responsibilities

The PDF implementation is split into a library-independent orchestrator and a
provider.

The orchestrator owns:

- conversion to `Block` objects and stable metadata (`page`, `kind`, `name`,
  `caption`, `saved_path`);
- paragraph cleanup and page ordering;
- recursive processing of PDF attachments;
- attachment depth and byte budgets;
- the 200-page rendered-image limit;
- normalized parser errors and resource cleanup;
- logging that identifies the provider without exposing document content.

The provider owns:

- opening and closing low-level document handles;
- page enumeration;
- extraction of page text and embedded image bytes;
- full-page JPEG rendering;
- extraction of named PDF attachment bytes;
- reporting provider name and version.

The provider returns library-neutral data objects. It does not create
`Block` instances, write files, recurse into attachments, or apply business
limits.

### 4.2 Provider contract

The contract exposes a document context and these logical operations:

```python
class PdfProvider(Protocol):
    name: str

    def version(self) -> str: ...
    def open(self, path: str | Path) -> PdfDocument: ...


class PdfDocument(Protocol):
    def page_count(self) -> int: ...
    def extract_text(self, page_index: int) -> str: ...
    def extract_images(self, page_index: int) -> list[PdfImage]: ...
    def render_page_jpeg(
        self,
        page_index: int,
        *,
        dpi: int,
        quality: int,
    ) -> bytes: ...
    def attachments(self) -> list[PdfAttachment]: ...
    def close(self) -> None: ...
```

`PdfImage` contains `data`, `name`, and detected extension. `PdfAttachment`
contains `name` and `data`. Page indexes in this provider contract are
zero-based; `Block.meta["page"]` remains one-based.

The first provider combines a pypdf reader with a lazily opened PDFium document.
PDFium is opened only when a page needs full-page rendering. Normal text PDFs
therefore do not incur PDFium work.

### 4.3 Thread safety

The backend parses documents in a `ThreadPoolExecutor`, while PDFium does not
allow concurrent calls from different threads. The provider uses one
process-wide re-entrant lock around every PDFium call, including document open,
page access, rendering, and close. pypdf extraction remains parallel.

This design intentionally serializes only scan rendering. Process isolation or
parallel PDFium workers are out of scope until measurements show that scanned
PDF throughput is a production bottleneck.

### 4.4 Errors and degradation

Provider-specific exceptions are wrapped as a stable `PdfParseError`, then
translated through the existing `ParseError` boundary used by the document
pipeline. Error messages identify the operation and page number but do not
expose extracted document text.

There is no automatic fallback to another installed PDF library. A missing or
unloadable standard provider is a startup/configuration error, not a silent
runtime downgrade.

Encrypted PDFs without a usable password and structurally invalid PDFs fail
with normalized errors. A page that contains no extractable text or embedded
image is rendered to JPEG as today. OCR remains out of scope; the existing
`no_text_layer` problem status continues to describe documents without a text
layer.

### 4.5 Health and provenance

`/health` adds non-secret PDF runtime metadata:

```json
{
  "dependencies": {
    "pdf_parser": {
      "status": "ok",
      "provider": "pypdf-pdfium",
      "pypdf_version": "runtime value from package metadata",
      "pdfium_version": "runtime value from PDFium metadata"
    }
  }
}
```

The implementation reports actual installed versions. It does not expose file
paths or configuration secrets.

## 5. Packaging and Image Composition

`doc-parser/pyproject.toml` becomes the single owner of PDF runtime
dependencies. `backend/requirements.txt` must not repeat PDF-parser libraries.
The backend image installs the local parser package and receives its transitive
runtime dependencies from that package.

Production versions of pypdf and pypdfium2 are bounded or pinned consistently
with the repository's dependency policy. The release SBOM records the exact
wheel and bundled PDFium versions.

The standard image acceptance gate inspects installed distributions and the
image filesystem. It fails if it finds PyMuPDF, a `pymupdf` or `fitz` module, or
a MuPDF shared library. Import failure alone is not sufficient evidence because
an unused distribution could still be present.

`docs/PYMUPDF_PROVIDER.md` describes the non-standard return path:

1. implement the provider contract in a separate adapter module;
2. add a separately reviewed dependency extra and Docker build target;
3. keep the standard target unchanged;
4. run the complete PDF provider contract suite;
5. produce a distinct image tag and SBOM;
6. obtain and record the applicable PyMuPDF/MuPDF license before deployment.

The document contains no bundled PyMuPDF source or package.

## 6. Production Compose Design

### 6.1 Common application services

The common compose definition retains `frontend`, `backend`, and the one-shot
`migrate` service. Backend remains a single process and single replica.

The storage mode is declared explicitly as `STORAGE_MODE=external|bundled` in
the production environment. A deployment preflight runs before `migrate` and
validates the complete pair:

- `bundled` requires PostgreSQL host `postgres:5432` and Qdrant host
  `qdrant:6333`;
- `external` rejects the compose service names `postgres` and `qdrant` and
  requires both URLs to be explicitly set;
- any other value or mixed pair exits non-zero with a message naming the
  invalid variable.

The backend itself still treats storage as endpoints. This avoids spreading
deployment topology checks through data-access code.

### 6.2 Bundled profile

Both storage services belong to one profile named `bundled`. There are no
independently selectable production storage profiles.

The bundled topology uses two networks:

- an application network joins frontend and backend;
- an internal storage network joins backend, migrate, PostgreSQL, and Qdrant.

Frontend does not join the storage network. PostgreSQL, Qdrant, migrate, and
backend publish no host ports. The reverse proxy or frontend is the only public
entry point.

PostgreSQL 17 and Qdrant use pinned image versions and stable named volumes.
The PostgreSQL password has no production default and must be supplied through
the deployment secret mechanism. The application connects as the configured
database owner rather than relying on the development password.

Startup ordering is:

1. PostgreSQL and Qdrant containers start;
2. PostgreSQL and Qdrant readiness checks pass;
3. deployment preflight validates the storage mode;
4. `migrate` completes `alembic upgrade head`;
5. backend starts and its `/health` becomes ready;
6. frontend becomes available.

### 6.3 External mode

External mode starts no PostgreSQL or Qdrant containers. Preflight validates
the URLs, and `migrate` connects to the external PostgreSQL endpoint. External
database provisioning, availability, encryption, backup, and restoration remain
the customer's infrastructure responsibility.

### 6.4 Host reboot

All long-running services use `restart: unless-stopped`. A systemd unit ordered
after Docker runs the selected production compose command with `up -d --wait`.
This reapplies the declared topology after a host reboot instead of depending
only on stale container state.

## 7. Bundled Data Protection

### 7.1 Backup

Bundled mode provides an operator command that creates a versioned backup
directory and manifest. The procedure uses a short maintenance window:

1. stop frontend and backend to prevent new writes;
2. leave PostgreSQL and Qdrant running;
3. run `pg_dump` in PostgreSQL custom format;
4. create a Qdrant collection snapshot through its snapshot API;
5. archive the application `/data` directory;
6. record checksums, collection name, schema revision, provider versions, and
   container image digests in a non-secret manifest;
7. verify that `pg_restore --list` can read the dump and that every declared
   artifact exists and matches its checksum;
8. restart backend and frontend only after validation succeeds or the failure
   has been clearly reported.

Secrets and the production `.env` file are excluded. Backup encryption,
off-host transfer, retention, and schedule are controlled by the customer's
backup platform or a supplied systemd timer/cron invocation.

### 7.2 Restore

Restore is intentionally explicit and refuses non-empty target volumes unless
the operator supplies the documented destructive confirmation option.

The normal restore order is:

1. verify backup checksums and compatible pinned service versions;
2. start only PostgreSQL and Qdrant with clean target volumes;
3. restore PostgreSQL;
4. restore the Qdrant collection snapshot;
5. restore `/data`;
6. apply forward Alembic migrations when restoring into a newer application;
7. run `check_integrity.py` against PostgreSQL, Qdrant, and stored files;
8. run production smoke checks;
9. start backend and frontend.

If a Qdrant snapshot is unavailable, the documented disaster path rebuilds the
vector index from canonical PostgreSQL/document data. This path is slower and
requires the correct embedding endpoint and dimensions.

### 7.3 Upgrade and rollback

Every bundled production upgrade begins with a verified backup. Application
images are addressed by immutable tags or digests. A code-only rollback returns
to the previous digests. If an applied migration changed data incompatibly,
rollback uses the pre-upgrade backup; automatic `alembic downgrade` is not the
recovery strategy.

## 8. Testing and Acceptance

### 8.1 PDF contract tests

The provider suite covers:

- page text and one-based page metadata;
- embedded image extraction and persisted image files;
- full-page PDFium rendering for a page without text or extractable images;
- PDF attachments and recursive PDF-in-DOCX parsing;
- attachment depth and total-byte budgets;
- the 200-page rendering limit;
- malformed and encrypted PDF error normalization;
- guaranteed resource close on success and exception;
- multiple concurrent parser calls, proving that PDFium sections never overlap;
- stable block ordering and metadata across the migration.

Rendered JPEG bytes are not compared byte-for-byte with PyMuPDF output. Tests
assert format, dimensions, page association, file existence, and downstream
markdown behavior.

### 8.2 Image and license gates

CI builds the standard backend image and verifies:

- PDF contract tests pass inside the image;
- distribution metadata and filesystem inspection find no PyMuPDF, `pymupdf`,
  `fitz`, or MuPDF library;
- dependency vulnerability and license audits pass;
- the generated SBOM contains pypdf/pypdfium2/PDFium provenance and no PyMuPDF;
- source SPDX identifiers and package metadata identify MIT consistently;
- required third-party notices are present in the release artifact.

### 8.3 Deployment gates

CI validates both production compose modes separately:

- `compose config` succeeds for each mode;
- mixed storage configurations fail preflight;
- bundled mode starts clean PostgreSQL and Qdrant volumes, runs Alembic, starts
  backend, and reaches a healthy state;
- external mode creates no bundled database containers and reaches the same
  health state against CI-provided external test endpoints;
- only the intended frontend port is published.

A release-candidate acceptance drill additionally verifies host restart
persistence and a full bundled backup/restore cycle. The restored installation
must contain the same document, chunk, concept, and Qdrant point counts and
must pass `check_integrity.py`.

## 9. Rollout Sequence

1. Confirm relicensing authority and establish the MIT/third-party notice
   baseline.
2. Add provider contract tests around the existing observable PDF behavior.
3. Extract the provider boundary without changing the active implementation.
4. Implement the pypdf+pypdfium2 provider and its process-wide PDFium lock.
5. Remove PyMuPDF from all standard dependencies and build layers.
6. Add absence, license, and SBOM gates to CI.
7. Replace the two independent local database profiles with the explicit
   `external` and `bundled` production mode contract.
8. Add readiness ordering, storage-network isolation, secrets validation, and
   systemd startup.
9. Add bundled backup, restore, upgrade, and integrity procedures.
10. Run PDF regression, clean bundled deployment, reboot persistence, and
    backup/restore acceptance drills.
11. Update operator and developer documentation, including the non-standard
    PyMuPDF return procedure.

## 10. Non-goals

- OCR for scanned PDFs.
- A PDF parsing microservice or network API.
- Runtime switching between PDF libraries in one installed image.
- Shipping a PyMuPDF adapter, wheel, source archive, or MuPDF binary.
- Supporting mixed production storage modes.
- Horizontal backend scaling.
- Automating customer-specific backup retention or off-host storage.
- Changing the public `Block` or `parse_document` contracts.

## 11. Completion Criteria

The change is complete only when all of the following are true:

1. The standard source dependency graph and backend image contain no PyMuPDF or
   MuPDF artifact.
2. The standard provider passes the PDF contract suite and the existing backend
   document pipeline tests.
3. Project-owned source and release metadata consistently declare MIT, and
   third-party notices/SBOM are shipped.
4. Both storage modes have unambiguous commands, validated environment
   templates, and passing CI configuration tests.
5. Bundled mode starts all required services on one server without exposing
   database ports and survives a host restart with its data intact.
6. A verified bundled backup can be restored into clean volumes and passes
   integrity and smoke checks.
