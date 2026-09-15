# PDF Provider and Bundled Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the standard PyMuPDF dependency with a pypdf+pypdfium2 PDF provider, relicense the project to MIT, and ship supported external and bundled production storage modes.

**Architecture:** `pdf_parser.py` remains the library-independent orchestration layer and delegates low-level PDF access to a provider abstraction. The only standard provider combines pypdf with PDFium rendering and serializes every PDFium call in-process. Docker Compose continues to run one backend replica, but exposes two validated storage topologies: externally managed databases or a single bundled PostgreSQL/Qdrant profile on the same host.

**Tech Stack:** Python 3.12, pypdf, pypdfium2/PDFium, Pillow, FastAPI, Pydantic Settings, PostgreSQL 17, Qdrant 1.19, Docker Compose v2, GitHub Actions, systemd.

**Spec:** [../specs/2026-09-15-pdf-provider-bundled-storage-design.md](../specs/2026-09-15-pdf-provider-bundled-storage-design.md)

## Global Constraints

- Preserve `parse_document(path, filename=None, attachments_dir=None, depth=0, budget=None) -> list[Block]` and the existing `Block` metadata contract.
- Standard source dependencies, CI environments, and backend images must contain no PyMuPDF, `pymupdf`, `fitz`, or MuPDF artifact.
- The standard provider is pypdf+pypdfium2; PyMuPDF is described only as a non-standard reintroduction procedure and has no shipped adapter or extra.
- PDFium calls must be serialized across the process because the document pipeline uses `ThreadPoolExecutor` with `pipeline_max_workers` greater than one.
- Production storage supports exactly `STORAGE_MODE=external` and `STORAGE_MODE=bundled`; reject all mixed configurations before Alembic runs.
- Bundled PostgreSQL/Qdrant receive no host ports; frontend/reverse-proxy is the only public entry point.
- Keep `backend` single-process and single-replica.
- Relicense project-owned source to MIT only after the project owner confirms authority to relicense every accepted contribution. Preserve third-party license notices.
- A production Compose invocation must use one named runtime env file both for interpolation and for service environment; a developer `.env` must never be read implicitly in production.
- Restore is non-destructive by default: it targets a new Compose project, new named volumes, and a new data directory. Replacing a live target is a separate, explicitly confirmed operation.
- Work in the shared dirty `main` checkout. Do not stage, commit, push, reset, delete unrelated files, or create a worktree unless the user explicitly asks.
- On Windows, run backend tests through `backend\.venv\Scripts\python.exe`; keep pytest artifacts under the repository test-artifact configuration.

Before Task 1, record the project owner's explicit relicensing confirmation in
the change record. It must cover all accepted contributions, not only the
current maintainer's files. This is a release gate: no task may alter `LICENSE`,
project SPDX identifiers, or package license metadata until that evidence exists.

---

## File Structure

| Path | Responsibility |
|---|---|
| `doc-parser/src/docparser/pdf_provider.py` | Library-neutral provider protocols, value objects, errors, singleton factory, and metadata reporting. |
| `doc-parser/src/docparser/pypdf_pdfium_provider.py` | The sole standard provider: pypdf text/image/attachment extraction plus PDFium JPEG rendering under a process-wide lock. |
| `doc-parser/src/docparser/pdf_parser.py` | Existing library-independent PDF-to-`Block` orchestration, rewritten to consume `PdfProvider`. |
| `doc-parser/tests/test_pdf_provider.py` | Provider contract, resource close, error normalization, metadata, and PDFium serialization tests. |
| `doc-parser/tests/test_parse.py` | Existing PDF observable-behavior regression coverage, updated to avoid PyMuPDF internals. |
| `doc-parser/pyproject.toml` | Sole owner of parser runtime dependencies and MIT package metadata. |
| `backend/app/services/health.py` | PDF provider readiness/version report in `/health`. |
| `backend/app/main.py` | Startup validation that fails before serving if the standard PDF provider cannot load. |
| `backend/tests/test_pdf_provider_health.py` | Startup and health API contract tests. |
| `backend/app/deployment/storage_mode.py` | Pure URL/mode validation shared by deployment preflight and unit tests. |
| `backend/scripts/validate_storage_mode.py` | CLI that reads deployment environment, calls `validate_storage_mode`, and exits before migration on an invalid topology. |
| `backend/scripts/wait_for_qdrant.py` | Bounded HTTP readiness wait used by the migration service before backend startup. |
| `backend/tests/test_storage_mode.py` | Valid bundled/external and rejected mixed mode matrix. |
| `docker-compose.yml` | Common services plus the one `bundled` storage profile, internal networks, startup ordering, and persistent volumes. |
| `deploy/production/docker-compose.external.yml` | External-mode overlay that gives only the migration service egress to external storage. |
| `deploy/production/bundled.env.example` | Complete bundled production environment template with compose service URLs. |
| `deploy/production/external.env.example` | Complete external production environment template with explicit external URLs. |
| `deploy/production/okf-knowledge.service` | systemd unit that reapplies the selected compose topology at host boot. |
| `scripts/production/backup-bundled.sh` | Quiesced bundled backup procedure with dump, Qdrant snapshot, data archive, manifest, and validation. |
| `scripts/production/restore-bundled.sh` | Default-safe restore into clean named volumes, with a separately confirmed replace path. |
| `scripts/production/audit-standard-image.sh` | Final-image filesystem audit that rejects MuPDF/PyMuPDF artifacts. |
| `backend/scripts/create_qdrant_snapshot.py` | Qdrant snapshot creation/download helper used by the host backup script. |
| `backend/scripts/restore_qdrant_snapshot.py` | Qdrant snapshot upload/recovery helper used by the host restore script. |
| `backend/scripts/check_integrity.py` | Strict manifest-aware verification of relational data, files, and Qdrant points after restore. |
| `backend/scripts/generate_sbom.py` | Deterministic CycloneDX JSON inventory of installed Python distributions. |
| `THIRD_PARTY_NOTICES.md` | Redistributable notices for pypdf, pypdfium2/PDFium, and remaining runtime components. |
| `docs/PYMUPDF_PROVIDER.md` | Controlled procedure for reintroducing a separately licensed provider without changing the standard image. |
| `docs/PRODUCTION_DEPLOYMENT.md` | Operations documentation for both modes, backup, restore, update, and rollback. |
| `README.md`, `README.ru.md`, `README.de.md`, `README.es.md`, `LICENSE`, `frontend/package.json` | Project license metadata and user-facing licensing/deployment text. |
| `.github/workflows/ci.yml` | Provider, image-composition, SBOM, external/bundled compose, migration, and cleanup gates. |

## Task 1: Establish the library-neutral PDF provider contract

**Files:**
- Create: `doc-parser/src/docparser/pdf_provider.py`
- Create: `doc-parser/tests/test_pdf_provider.py`
- Modify: `doc-parser/src/docparser/__init__.py`

**Interfaces:**
- Consumes: existing `Block` contract only at the parser orchestration boundary.
- Produces: `PdfProvider`, `PdfDocument`, `PdfImage`, `PdfAttachment`, `PdfParseError`, `PdfProviderUnavailable`, `get_pdf_provider()`, and `get_pdf_provider_metadata()`.

- [x] **Step 1: Write the failing provider-contract test**

  Add a fake provider and document in `test_pdf_provider.py`. Monkeypatch `get_pdf_provider()` to return the fake provider so factory metadata can be tested before the concrete provider exists.

  ```python
  from docparser.pdf_provider import PdfAttachment, PdfImage, get_pdf_provider_metadata

  def test_provider_metadata_has_no_path_or_secret(monkeypatch):
      monkeypatch.setattr("docparser.pdf_provider.get_pdf_provider", lambda: FakeProvider())
      metadata = get_pdf_provider_metadata()
      assert metadata["provider"] == "pypdf-pdfium"
      assert set(metadata) == {"provider", "pypdf_version", "pdfium_version"}
      assert all("/" not in str(value) for value in metadata.values())

  def test_value_objects_preserve_raw_pdf_data():
      image = PdfImage(data=b"image", name="figure", extension=".png")
      attachment = PdfAttachment(name="source.docx", data=b"document")
      assert image.extension == ".png"
      assert attachment.data == b"document"
  ```

- [x] **Step 2: Run the test to verify the contract does not exist yet**

  Run from `doc-parser`:

  ```powershell
  python -m pytest tests/test_pdf_provider.py -q
  ```

  Expected: collection fails because `docparser.pdf_provider` does not exist.

- [x] **Step 3: Implement the provider value objects and protocols**

  Add immutable data classes and protocols. Keep page indexes zero-based inside the provider layer.

  ```python
  @dataclass(frozen=True)
  class PdfImage:
      data: bytes
      name: str
      extension: str

  @dataclass(frozen=True)
  class PdfAttachment:
      name: str
      data: bytes

  class PdfDocument(Protocol):
      def page_count(self) -> int: ...
      def extract_text(self, page_index: int) -> str: ...
      def extract_images(self, page_index: int) -> list[PdfImage]: ...
      def render_page_jpeg(self, page_index: int, *, dpi: int, quality: int) -> bytes: ...
      def attachments(self) -> list[PdfAttachment]: ...
      def close(self) -> None: ...

   class PdfProvider(Protocol):
       name: str
       def open(self, path: str | Path) -> PdfDocument: ...
       def version(self) -> str: ...
       def metadata(self) -> dict[str, str]: ...
  ```

  `get_pdf_provider()` imports only `PypdfPdfiumProvider`; convert `ImportError` and provider initialization errors to `PdfProviderUnavailable`. `get_pdf_provider_metadata()` returns only the three keys asserted by the test.

- [x] **Step 4: Re-export only the intended provider API**

  Update `docparser/__init__.py` to export `PdfParseError`, `PdfProviderUnavailable`, and `get_pdf_provider_metadata`, without changing the existing parse exports.

- [x] **Step 5: Run the new contract test**

  ```powershell
  python -m pytest tests/test_pdf_provider.py -q
  ```

  Expected: tests pass; the metadata test uses the fake factory and therefore does not require the concrete provider yet.

- [x] **Step 6: Verify public API compatibility**

  ```powershell
  python -c "from docparser import Block, ParseError, SUPPORTED_EXTENSIONS, parse_document; print(SUPPORTED_EXTENSIONS)"
  ```

  Expected: output remains `{'.docx', '.pdf', '.xlsx'}`.

## Task 2: Implement the pypdf+PDFium provider and PDFium serialization

**Files:**
- Create: `doc-parser/src/docparser/pypdf_pdfium_provider.py`
- Modify: `doc-parser/pyproject.toml`
- Modify: `doc-parser/tests/test_pdf_provider.py`
- Modify: `doc-parser/tests/fixtures.py`

**Interfaces:**
- Consumes: protocols and value objects from `pdf_provider.py`; fixture helpers `make_pdf`, `make_pdf_with_image`, and `make_pdf_scanned_like`.
- Produces: `PypdfPdfiumProvider(name="pypdf-pdfium")` and `PypdfPdfiumDocument` implementing every `PdfDocument` method.

- [x] **Step 1: Add provider behavior tests before installing its implementation**

  Cover text, image, attachment, scan rendering, cleanup after a render
  exception, and concurrent render serialization. Test the provider directly:
  `parse_document()` still uses the old implementation until Task 3. Create a
  PDF attachment with `PdfWriter.add_attachment()` rather than mocking
  `reader.attachments`; pypdf returns that attachment as `list[bytes]`.

  ```python
  import threading
  import time

  def test_pdfium_render_calls_do_not_overlap(monkeypatch, tmp_path):
      active = maximum = 0
      original = PypdfPdfiumDocument._render_page_unlocked
      first_entered = threading.Event()
      release_first = threading.Event()
      state_lock = threading.Lock()

      def observed(self, page_index, dpi, quality):
          nonlocal active, maximum
          with state_lock:
              active += 1
              maximum = max(maximum, active)
              is_first = active == 1
          if is_first:
              first_entered.set()
              assert release_first.wait(timeout=3)
          try:
              return original(self, page_index, dpi, quality)
          finally:
              with state_lock:
                  active -= 1

      monkeypatch.setattr(PypdfPdfiumDocument, "_render_page_unlocked", observed)
      scans = [make_pdf_scanned_like(tmp_path / f"scan-{i}.pdf") for i in range(2)]
      first_document = PypdfPdfiumProvider().open(scans[0])
      second_document = PypdfPdfiumProvider().open(scans[1])
      try:
          with ThreadPoolExecutor(max_workers=2) as pool:
              first = pool.submit(first_document.render_page_jpeg, 0, dpi=144, quality=85)
              assert first_entered.wait(timeout=3)
              second = pool.submit(second_document.render_page_jpeg, 0, dpi=144, quality=85)
              time.sleep(0.1)
              assert maximum == 1
              release_first.set()
              first.result(timeout=3)
              second.result(timeout=3)
      finally:
          release_first.set()
          first_document.close()
          second_document.close()
      assert maximum == 1
  ```

  The first render waits while the second attempts to enter; without the lock,
  `maximum` becomes two. Add a separate assertion that the real PDF attachment
  is returned as `PdfAttachment(name="proof.txt", data=b"proof")`.

- [x] **Step 2: Run the test and confirm the missing implementation failure**

  ```powershell
  python -m pytest tests/test_pdf_provider.py -q
  ```

  Expected: import failure for `docparser.pypdf_pdfium_provider`.

- [x] **Step 3: Make parser packaging own the permissive dependencies**

  In `doc-parser/pyproject.toml`, retain `pypdf[image]>=6.16.1`, add a tested,
  bounded `pypdfium2` dependency and explicit `Pillow` dependency, and remove
  `pymupdf>=1.24`. Do not add `license = "MIT"` here; Task 6 makes that
  coordinated metadata change only after the relicensing preflight passes.

  Do not add a PyMuPDF optional dependency group. Keep `reportlab` in the existing `dev` group.

- [x] **Step 4: Implement the concrete provider with a process-wide lock**

  Use one module-level `threading.RLock` for all interactions with `pypdfium2`. pypdf remains the source for text, named attachments, and embedded images.

  ```python
  _PDFIUM_LOCK = threading.RLock()

  class PypdfPdfiumDocument:
      def __init__(self, path: str | Path):
          self._path = str(path)
          self._reader = PdfReader(self._path)
          self._pdfium_document = None

      def render_page_jpeg(self, page_index: int, *, dpi: int, quality: int) -> bytes:
          with _PDFIUM_LOCK:
              return self._render_page_unlocked(page_index, dpi, quality)

      def _render_page_unlocked(self, page_index: int, dpi: int, quality: int) -> bytes:
          if self._pdfium_document is None:
              self._pdfium_document = pdfium.PdfDocument(self._path)
          page = bitmap = image = None
          try:
              page = self._pdfium_document[page_index]
              bitmap = page.render(scale=dpi / 72)
              image = bitmap.to_pil()
              with io.BytesIO() as output:
                  image.save(output, format="JPEG", quality=quality)
                  return output.getvalue()
          finally:
              for resource in (image, bitmap, page):
                  if resource is not None:
                      resource.close()

      def close(self) -> None:
          with _PDFIUM_LOCK:
              if self._pdfium_document is not None:
                  self._pdfium_document.close()
                  self._pdfium_document = None
  ```

  Keep the entire lifecycle, including every close in `finally`, under
  `_PDFIUM_LOCK`. Make `extract_images` reuse the current pypdf image-format
  detection and convert failures per-image to debug logging. Normalize attachment
  values as `list[bytes]`, while retaining the old mapping form
  `{"data": bytes | list[bytes]}`; emit one `PdfAttachment` for each nonempty
  byte value, including duplicate names. Close pypdf file resources when its
  reader exposes a `close()` method.

- [x] **Step 5: Implement provider metadata and unavailable-provider errors**

  Implement `version()` as the stable combined provider version and `metadata()`
  from `importlib.metadata.version("pypdf")` plus
  `pypdfium2.version.PDFIUM_INFO` (or its documented runtime version API).
  `get_pdf_provider_metadata()` delegates to `metadata()` and validates exactly
  the three public keys. If pypdfium2 cannot be imported, raise
  `PdfProviderUnavailable("PDF provider pypdf-pdfium is unavailable: ...")`.

- [x] **Step 6: Run the provider contract suite**

  ```powershell
  python -m pytest tests/test_pdf_provider.py tests/test_parse.py::TestPdf tests/test_attachments.py -q
  ```

  Expected: text, image, attachment, scan, cap, and concurrent-render tests pass.

- [x] **Step 7: Verify that no fixture mentions PyMuPDF as the active implementation**

  Replace the old fixture wording about the PyMuPDF fallback with wording that describes PDFium rendering. Then run:

  ```powershell
  rg -n -i "pymupdf|fitz|mupdf" doc-parser
  ```

  Expected: no result after Task 6 except the future return-procedure documentation, which is outside `doc-parser`.

## Task 3: Move PDF orchestration onto the provider boundary

**Files:**
- Modify: `doc-parser/src/docparser/pdf_parser.py`
- Modify: `doc-parser/src/docparser/parser.py`
- Modify: `doc-parser/tests/test_parse.py`
- Modify: `doc-parser/tests/test_attachments.py`

**Interfaces:**
- Consumes: `PdfProvider`, `PdfDocument`, `get_pdf_provider`, and existing `process_embedded`/`save_image_file` helpers.
- Produces: unchanged `parse_pdf(path, attachments_dir=None, depth=0, budget=None) -> list[Block]` behavior without library imports in `pdf_parser.py`.

- [x] **Step 1: Write a failing orchestration test with a fake document**

  Inject a fake provider through an internal `provider_factory` parameter of `parse_pdf`, not through the public `parse_document` signature.

  ```python
  def test_parse_pdf_closes_provider_document_on_save_failure(monkeypatch, tmp_path):
      document = FakePdfDocument(images=[PdfImage(data=b"image", name="figure", extension=".png")])
      monkeypatch.setattr(pdf_parser, "save_image_file", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
      with pytest.raises(OSError, match="disk full"):
          pdf_parser.parse_pdf(tmp_path / "source.pdf", attachments_dir=tmp_path, provider_factory=lambda: FakeProvider(document))
      assert document.closed is True
  ```

- [x] **Step 2: Run the new test to establish the missing injection/cleanup behavior**

  ```powershell
  python -m pytest tests/test_pdf_provider.py::test_parse_pdf_closes_provider_document_on_save_failure -q
  ```

  Expected: failure because `parse_pdf` does not accept `provider_factory` and currently owns PDF handles.

- [x] **Step 3: Refactor `parse_pdf` around the provider**

  Keep constants `_RENDER_DPI`, `_RENDER_JPEG_QUALITY`, and `_RENDER_PAGE_LIMIT` in the orchestrator. Replace direct `PdfReader` and PyMuPDF calls with:

  ```python
  def parse_pdf(..., provider_factory=get_pdf_provider) -> list[Block]:
      provider = provider_factory()
      document = provider.open(path)
      try:
          for page_index in range(document.page_count()):
              page = page_index + 1
              text = document.extract_text(page_index)
              images = document.extract_images(page_index)
              # Existing Block conversion, file saving, and render cap remain here.
              if not images and not text.strip() and attachments_dir is not None:
                  jpeg = document.render_page_jpeg(page_index, dpi=_RENDER_DPI, quality=_RENDER_JPEG_QUALITY)
                  saved = save_image_file(jpeg, attachments_dir, image_count, ext=".jpg")
          for attachment in document.attachments():
              blocks.extend(process_embedded(attachment.data, attachment.name, "", "", attachments_dir, ...))
          return blocks
      finally:
          document.close()
  ```

  Preserve one-based `Block.meta["page"]`, image captions, `save_image_file` call shape, render cap warning, attachment index ordering, and the no-attachments-dir behavior that skips full-page rendering.

- [x] **Step 4: Normalize provider failures at the parser boundary**

  Catch `PdfParseError` only around provider operations and raise `ParseError` with the source filename and provider operation. Do not catch `OSError` from saving attachments or broad `Exception`; those are existing pipeline failures and must retain their original signal.

- [x] **Step 5: Remove obsolete private rendering helpers**

  Delete `_open_render_doc` and `_render_page_image` from `pdf_parser.py`. Rewrite the old monkeypatch cap test to inject a fake provider whose `render_page_jpeg` increments a counter. Do not retain compatibility aliases that import PyMuPDF.

- [x] **Step 6: Run all parser tests**

  ```powershell
  python -m pytest tests/test_parse.py tests/test_attachments.py tests/test_markdown.py tests/test_archive_guard.py -q
  ```

  Expected: all document-parser tests pass and no test imports PyMuPDF.

## Task 4: Make provider readiness visible and fail fast in the backend

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/health.py`
- Create: `backend/tests/test_pdf_provider_health.py`

**Interfaces:**
- Consumes: `docparser.get_pdf_provider_metadata()` and `docparser.PdfProviderUnavailable`.
- Produces: application startup failure for an unavailable provider and `/health` field `dependencies.pdf_parser` with provider metadata.

- [x] **Step 1: Write failing health and startup tests**

   ```python
   from unittest.mock import Mock

   def test_health_reports_pdf_provider(monkeypatch):
       monkeypatch.setattr(health, "_cache", {})
       monkeypatch.setattr(health, "_cache_ts", 0.0)
       for check in ("_check_llm", "_check_embeddings", "_check_qdrant", "_check_database"):
           monkeypatch.setattr(health, check, lambda: {"status": "ok"})
       monkeypatch.setattr(health, "get_pdf_provider_metadata", lambda: {
           "provider": "pypdf-pdfium", "pypdf_version": "6.16.1", "pdfium_version": "pdfium-test",
       })
       result = health.get_health()
       assert result["dependencies"]["pdf_parser"] == {
           "status": "ok", "provider": "pypdf-pdfium", "pypdf_version": "6.16.1", "pdfium_version": "pdfium-test",
       }

   def test_startup_rejects_unavailable_pdf_provider(monkeypatch):
       unavailable = Mock(side_effect=PdfProviderUnavailable("missing pypdfium2"))
       monkeypatch.setattr(main, "get_pdf_provider_metadata", unavailable)
       with pytest.raises(PdfProviderUnavailable, match="missing pypdfium2"):
           main._validate_runtime_dependencies()
   ```

- [x] **Step 2: Run the tests before adding runtime validation**

  ```powershell
  Set-Location backend
  .\.venv\Scripts\python.exe -m pytest tests/test_pdf_provider_health.py -q
  ```

  Expected: import or attribute failure because no PDF provider health integration exists.

- [x] **Step 3: Add a dedicated startup validator**

  Add `_validate_runtime_dependencies()` in `backend/app/main.py`; call it once in the application lifespan before background workers start. It calls `get_pdf_provider_metadata()` and lets `PdfProviderUnavailable` terminate startup. Do not make `/health` the first place that discovers a missing required library.

- [x] **Step 4: Add provider status to health computation**

  In `health.py`, create `_check_pdf_parser()` that converts metadata to:

  ```python
  {
      "status": "ok",
      "provider": metadata["provider"],
      "pypdf_version": metadata["pypdf_version"],
      "pdfium_version": metadata["pdfium_version"],
  }
  ```

  On `PdfProviderUnavailable`, return `{"status": "down", "error": "PDF provider unavailable"}` without leaking host paths. Include it in `deps`, so a failed PDF provider makes overall health `degraded` when Qdrant remains reachable.

- [x] **Step 5: Run focused and existing health-adjacent tests**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_pdf_provider_health.py tests/test_error_handling.py -q
  ```

  Expected: provider startup and health tests pass; pipeline integration remains unchanged.

## Task 5: Remove PyMuPDF from standard packaging and prove image composition

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/Dockerfile`
- Create: `backend/scripts/generate_sbom.py`
- Create: `backend/tests/test_distribution_inventory.py`
- Create: `scripts/production/audit-standard-image.sh`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: doc-parser package metadata and Docker image Python environment.
- Produces: a standard image whose parser dependencies arrive only through editable `doc-parser`, plus deterministic JSON SBOM output.

- [x] **Step 1: Write a failing distribution-inventory test**

  ```python
  def test_standard_runtime_has_pdfium_and_no_mupdf():
      names = {dist.metadata["Name"].lower() for dist in importlib.metadata.distributions()}
      assert "pypdfium2" in names
      assert "pymupdf" not in names
      assert importlib.util.find_spec("pymupdf") is None
      assert importlib.util.find_spec("fitz") is None
  ```

  Run it in the current backend environment first; it should fail while PyMuPDF remains installed.

- [x] **Step 2: Remove duplicate and prohibited runtime dependencies**

  Delete `pymupdf` and `pillow` from `backend/requirements.txt`. Keep PDF dependencies only in `doc-parser/pyproject.toml`; `Pillow` remains an explicit parser dependency from Task 2. Do not change unrelated backend dependencies.

- [x] **Step 3: Make Docker installation order preserve one dependency owner**

  Keep `COPY doc-parser ./doc-parser` and `pip install -e ./doc-parser` before backend requirements. Add an image build assertion after installation:

  ```dockerfile
   RUN python -c "import importlib.util, importlib.metadata as m; assert m.version('pypdfium2'); assert importlib.util.find_spec('pymupdf') is None; assert importlib.util.find_spec('fitz') is None"
  ```

  This is a build-time gate; the CI runtime gate in Step 6 remains required as independent evidence.

- [x] **Step 4: Implement the SBOM generator**

  `generate_sbom.py` accepts `--output` and writes a reproducible CycloneDX 1.5 JSON document. Sort distributions by normalized name and emit `type`, `name`, `version`, and the `License-Expression` or `License` metadata value when present.

  ```python
  payload = {
      "bomFormat": "CycloneDX",
      "specVersion": "1.5",
      "version": 1,
      "components": components,
  }
  Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  ```

  Reject an output path that is not a file path. The script must not read secrets or environment variables.

- [x] **Step 5: Run package-level checks**

  ```powershell
  Set-Location backend
  .\.venv\Scripts\python.exe -m pytest tests/test_distribution_inventory.py -q
  .\.venv\Scripts\python.exe scripts/generate_sbom.py --output ..\tests\tmp\backend-sbom.json
  Get-Content ..\tests\tmp\backend-sbom.json | Select-String 'pypdfium2|pymupdf'
  ```

  Expected: the SBOM contains pypdfium2 and no PyMuPDF entry.

- [x] **Step 6: Add image-level CI gates**

  In the existing `docker` job, after `docker compose build`, run the backend
  image with `importlib.metadata`/`find_spec` assertions and
  `scripts/production/audit-standard-image.sh`. The audit script must run
  `find /` in the final image filesystem for names containing `pymupdf`,
  `mupdf`, or a top-level `fitz` module, print every match, and exit non-zero
  on a match. Mount a CI artifact directory for `generate_sbom.py`, and fail
  if `rg -i 'pymupdf|mupdf|fitz'` finds an artifact in the SBOM, final-image
  inventory, or filesystem-audit output. Task 6 adds and asserts the notices
  file in the image. Preserve the existing Alembic migration gate.

- [x] **Step 7: Run the Docker verification locally when Docker is available**

  ```powershell
   docker compose build backend migrate
   docker compose run --rm --no-deps backend python -c "import importlib.util, importlib.metadata as m; assert m.version('pypdfium2'); assert importlib.util.find_spec('pymupdf') is None; assert importlib.util.find_spec('fitz') is None"
   ./scripts/production/audit-standard-image.sh okf-backend:local
  ```

  Expected: all commands exit zero. The production guide additionally requires
  an inventory of old images and manually installed Python environments before
  cutover; this check proves the standard release image, not arbitrary historic
  server state.

## Task 6: Relicense project-owned material and preserve third-party notices

**Files:**
- Modify: `LICENSE`
- Modify: every project-owned source file returned by `rg -l 'SPDX-License-Identifier: AGPL-3.0-or-later' backend doc-parser`
- Modify: `README.md`, `README.ru.md`, `README.de.md`, `README.es.md`
- Modify: `frontend/package.json`
- Modify: `backend/Dockerfile`
- Create: `THIRD_PARTY_NOTICES.md`
- Create: `docs/PYMUPDF_PROVIDER.md`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: confirmed relicensing authority and actual package licenses from the standard image/SBOM.
- Produces: MIT project metadata plus auditable third-party attribution, without a PyMuPDF package, adapter, or binary.

- [x] **Step 1: Record the licensing precondition in the change review**

  Before modifying license declarations, add a review checklist entry stating that the project owner confirmed authority to relicense all accepted contributions. Do not silently infer contributor consent from Git history.

  Review record (2026-09-15): the project owner directed the switch to MIT in
  this change and confirmed the licensing decision before the implementation.

- [x] **Step 2: Replace the root license and source SPDX identifiers**

  Replace `LICENSE` with the canonical MIT text using `Copyright (c) 2026 Alexey`. Change only project-owned `SPDX-License-Identifier: AGPL-3.0-or-later` lines to `SPDX-License-Identifier: MIT`; retain every third-party license header unchanged.

- [x] **Step 3: Update package and README declarations**

  Add `"license": "MIT"` to `frontend/package.json`. Update all language README license sections to say that the current release is MIT, preserve a short historical note that older releases remain AGPL, and remove any statement that the project license follows PyMuPDF.

- [x] **Step 4: Write `THIRD_PARTY_NOTICES.md` from installed evidence**

  Include package name, exact version source, upstream URL, SPDX license, and
  required redistribution note for pypdf, pypdfium2, PDFium and PDFium's
  bundled notices. Reference the standard image SBOM as the authoritative
  inventory; do not claim that a component is MIT merely because the project is
  MIT. Add `COPY THIRD_PARTY_NOTICES.md /app/THIRD_PARTY_NOTICES.md` to
  `backend/Dockerfile`, then test with
  `docker compose run --rm --no-deps backend test -s /app/THIRD_PARTY_NOTICES.md`.

- [x] **Step 5: Document the controlled PyMuPDF return path**

  In `docs/PYMUPDF_PROVIDER.md`, require: separate provider module, separately reviewed dependency extra, distinct image tag, generated SBOM, complete provider contract suite, and recorded AGPL/commercial license decision. State that standard source and image must remain PyMuPDF-free.

- [x] **Step 6: Add license-text and source-marker CI checks**

  Add a CI step that fails when project-owned source still contains `SPDX-License-Identifier: AGPL-3.0-or-later`, `LICENSE` is not MIT, `THIRD_PARTY_NOTICES.md` is missing `pypdfium2` or `PDFium`, or a standard-image SBOM contains `PyMuPDF`.

- [x] **Step 7: Verify the relicensing surface**

  ```powershell
  rg -n "SPDX-License-Identifier: AGPL-3.0-or-later|GNU AFFERO|PyMuPDF" backend doc-parser README.md README.ru.md README.de.md README.es.md frontend\package.json LICENSE
  ```

  Expected: no result except the explicitly scoped historical/return-procedure text in `docs/PYMUPDF_PROVIDER.md`, which must state that it is not part of the standard image.

## Task 7: Implement the explicit external/bundled storage contract

**Files:**
- Create: `backend/app/deployment/__init__.py`
- Create: `backend/app/deployment/storage_mode.py`
- Create: `backend/scripts/validate_storage_mode.py`
- Create: `backend/scripts/wait_for_qdrant.py`
- Create: `backend/tests/test_storage_mode.py`
- Modify: `docker-compose.yml`
- Create: `deploy/production/docker-compose.external.yml`
- Modify: `.env.example`
- Create: `deploy/production/bundled.env.example`
- Create: `deploy/production/external.env.example`

**Interfaces:**
- Consumes: `STORAGE_MODE`, direct `DATABASE_URL`, `QDRANT_URL`, and `POSTGRES_PASSWORD` values.
- Produces: `validate_storage_mode(mode: str, database_url: str | None, qdrant_url: str | None, postgres_password: str | None) -> None` and compose startup that cannot reach Alembic with an unsupported topology or an unintended env file.

- [x] **Step 1: Write a failing pure validation matrix**

  ```python
  @pytest.mark.parametrize(
      ("mode", "database_url", "qdrant_url"),
      [
          ("bundled", "postgresql+psycopg://okf:secret@postgres:5432/okf_knowledge", "http://qdrant:6333"),
          ("external", "postgresql+psycopg://okf:secret@db.corp.internal:5432/okf_knowledge", "https://vectors.corp.internal:6333"),
      ],
  )
  def test_valid_storage_modes(mode, database_url, qdrant_url):
      validate_storage_mode(mode, database_url, qdrant_url, "test-secret")

  def test_bundled_rejects_external_qdrant():
      with pytest.raises(StorageModeError, match="bundled.*qdrant"):
          validate_storage_mode("bundled", "postgresql+psycopg://okf:secret@postgres:5432/okf_knowledge", "https://vectors.corp.internal:6333", "test-secret")
  ```

- [x] **Step 2: Run the test before implementing validation**

  ```powershell
  Set-Location backend
  .\.venv\Scripts\python.exe -m pytest tests/test_storage_mode.py -q
  ```

  Expected: collection failure because `app.deployment.storage_mode` does not exist.

- [x] **Step 3: Implement URL-aware, non-network validation**

  Parse URLs with `urllib.parse.urlparse`. `bundled` requires a non-empty `POSTGRES_PASSWORD`, host `postgres` and port `5432` for the database, and host `qdrant` and port `6333` for Qdrant. `external` requires both direct URLs and rejects either compose hostname. Raise `StorageModeError` with variable name and expected topology. Do not open a network connection in this pure function.

- [x] **Step 4: Implement the deployment CLI and Qdrant readiness wait**

  `validate_storage_mode.py` reads only `STORAGE_MODE`, `DATABASE_URL`, `QDRANT_URL`, and `POSTGRES_PASSWORD`, calls the pure function, prints the selected mode without passwords, and exits non-zero on error.

  `wait_for_qdrant.py` accepts `--url`, `--timeout-seconds 60`, and
  `--interval-seconds 1`; it reads `QDRANT_API_KEY` and an optional CA bundle
  path from the environment, passes them to `httpx`, redacts credentials in
  failures, and exits non-zero after 60 seconds. It does not create collections
  or modify Qdrant.

- [x] **Step 5: Convert compose to one bundled profile and two networks**

  In `docker-compose.yml`:

  - replace `local-postgres` and `local-qdrant` with one profile, `bundled`;
  - define `app_network` and `storage_network` with `internal: true` for storage;
  - join frontend only to `app_network`;
  - join backend to both networks and PostgreSQL/Qdrant only to `storage_network`;
  - keep `migrate` on `storage_network` in bundled mode, but attach it also to
    `app_network` through `deploy/production/docker-compose.external.yml` in
    external mode, so it can reach the customer endpoints while PostgreSQL and
    Qdrant remain absent;
  - retain no `ports` for backend, PostgreSQL, or Qdrant;
  - make the production PostgreSQL password mandatory when `bundled` is selected, rather than defaulting to `okf_dev_pg`;
  - replace mutable image tags with the immutable repository digests obtained by `docker pull postgres:17` / `docker pull qdrant/qdrant:v1.19.0` followed by `docker image inspect --format '{{index .RepoDigests 0}}'`; record the resulting digests in the release manifest and update them only through the tested upgrade procedure;
  - preserve named `postgres_data` and `qdrant_data` volumes;
  - replace fixed service `env_file: .env` with
    `env_file: ${OKF_RUNTIME_ENV_FILE:?set OKF_RUNTIME_ENV_FILE}` for backend
    and migrate. Production commands must set the same path as both
    `OKF_RUNTIME_ENV_FILE` and `docker compose --env-file`; this prevents a
    developer `.env` from silently supplying `DATABASE_URL` or `QDRANT_URL`;
  - change `migrate.command` to run storage validation, Qdrant readiness wait, then `alembic upgrade head` in that order;
  - make backend depend on successful migration, not merely a started Qdrant
    container, and give backend a healthcheck that requires the JSON health
  status `ok`, not merely HTTP 200 (the current `/health` contract).

  Keep an external run free of bundled database services. Do not introduce a local/external hybrid profile.

- [x] **Step 6: Write complete production environment templates**

  `bundled.env.example` must set `STORAGE_MODE=bundled`,
  `COMPOSE_PROFILES=bundled`, database host `postgres`, Qdrant URL
  `http://qdrant:6333`, production auth/HTTPS placeholders, and require an
  operator-generated PostgreSQL password.

  `external.env.example` must set `STORAGE_MODE=external`, external FQDN
  examples for both databases, an empty `COMPOSE_PROFILES`, and external
  Qdrant API-key/CA-bundle guidance. It must not declare database containers.

  Update `.env.example` to describe local development separately from production and remove all references to independent `local-postgres`/`local-qdrant` profiles. Document the exact PowerShell invocation:

  ```powershell
  $env:OKF_RUNTIME_ENV_FILE = 'deploy/production/bundled.env'
  docker compose --env-file $env:OKF_RUNTIME_ENV_FILE up -d --wait
  ```

- [x] **Step 7: Run unit and compose-config validation**

  ```powershell
  Set-Location backend
  .\.venv\Scripts\python.exe -m pytest tests/test_storage_mode.py -q
  Set-Location ..
  $env:OKF_RUNTIME_ENV_FILE = 'deploy/production/bundled.env.example'
  docker compose --env-file $env:OKF_RUNTIME_ENV_FILE config --quiet
  $env:OKF_RUNTIME_ENV_FILE = 'deploy/production/external.env.example'
  docker compose --env-file $env:OKF_RUNTIME_ENV_FILE -f docker-compose.yml -f deploy/production/docker-compose.external.yml config --quiet
  ```

  Expected: both configurations parse and show the selected values in backend
  and migrate without reading root `.env`. Add a test whose root `.env` has
  deliberately invalid database hosts; it must not affect either production
  configuration. Runtime CI in Task 9 proves that `external` creates no bundled
  database containers and that its migration can reach a service outside
  `storage_network`.

## Task 8: Add bundled backup, restore, reboot, and operations tooling

**Files:**
- Create: `backend/scripts/create_qdrant_snapshot.py`
- Create: `backend/scripts/restore_qdrant_snapshot.py`
- Create: `scripts/production/backup-bundled.sh`
- Create: `scripts/production/restore-bundled.sh`
- Create: `deploy/production/okf-knowledge.service`
- Create: `scripts/production/tests/test_backup_scripts.sh`
- Modify: `backend/scripts/check_integrity.py`
- Create: `backend/tests/test_check_integrity.py`
- Modify: `docs/PRODUCTION_DEPLOYMENT.md`

**Interfaces:**
- Consumes: a running bundled compose project, Qdrant endpoint credentials, persistent named volumes, and `backend/scripts/check_integrity.py`.
- Produces: versioned, validated backup directories plus a default-safe restore
  into a clean target project, with a separate explicitly destructive replace
  operation.

- [x] **Step 1: Write shell dry-run tests before the operation scripts**

  Create a shell test that substitutes a fake `docker` executable in `PATH`, runs each script with `--dry-run`, and asserts command ordering:

  ```sh
  PATH="$fixture_bin:$PATH" ./scripts/production/backup-bundled.sh --env-file "$fixture_env" --backup-dir "$tmp/backup" --dry-run
  grep -qx 'compose stop frontend backend' "$fake_docker_log"
  grep -qx 'compose exec postgres pg_dump' "$fake_docker_log"
  grep -qx 'compose run backend create_qdrant_snapshot' "$fake_docker_log"
  ```

  Add tests proving that default restore never invokes `docker volume rm`, that
  it rejects an existing target data directory, and that
  `--replace-existing` fails without the literal `--confirm-replace-existing`.

- [x] **Step 2: Run the shell tests and confirm scripts are absent**

  ```sh
  bash scripts/production/tests/test_backup_scripts.sh
  ```

  Expected: failure because backup/restore scripts do not exist.

- [x] **Step 3: Implement Qdrant snapshot helpers with explicit input/output**

  `create_qdrant_snapshot.py` accepts `--output-dir`, calls the configured Qdrant client's collection snapshot API, downloads the returned snapshot to the output directory, writes SHA-256 alongside it, and exits non-zero on API/download failure.

  `restore_qdrant_snapshot.py` accepts `--snapshot-file`, verifies its SHA-256 sidecar, uploads and recovers the collection through the Qdrant client API, and polls the collection endpoint until it becomes readable. Neither helper accepts a collection name from an untrusted filename; both use `get_settings().qdrant_collection`.

- [x] **Step 4: Implement `backup-bundled.sh`**

  Require `--env-file` and `--backup-dir`; source no arbitrary shell code from
  the environment file. Read required variables with a dotenv parser limited to
  `KEY=VALUE` lines. Refuse any mode except `bundled`. Acquire a non-blocking
  `flock` keyed to the resolved compose project so backup, restore, and upgrade
  cannot overlap. Before every internal `docker compose` call, export
  `OKF_RUNTIME_ENV_FILE` to exactly the supplied `--env-file`, and pass that
  same value using `docker compose --env-file`; never fall back to root `.env`.

  Implement this exact sequence with `set -euo pipefail` and a `trap` that restarts frontend/backend when they were stopped by this invocation:

  1. create a timestamped subdirectory with mode `0700` owned by the backend
     container UID/GID (or run the snapshot helper with that UID/GID), so its
     mounted `/backup` path is writable;
  2. stop frontend/backend;
  3. run `pg_dump --format=custom` inside the PostgreSQL container and copy the dump to the backup directory;
  4. run `create_qdrant_snapshot.py` in the backend image with `--no-deps` and
     the backup directory mounted at `/backup`;
  5. archive the configured host application data directory (`OKF_DATA_DIR`,
     falling back to `DATA_DIR`/`./data`) excluding the backup root;
  6. write `manifest.json` containing SHA-256 values for every archive, original,
     and attachment; document/chunk/concept/Qdrant-point totals; schema revision,
     collection name, PDF provider metadata, and compose image digests, but no
     secrets;
  7. run `pg_restore --list` and checksum every manifest artifact;
  8. restart frontend/backend.

- [x] **Step 5: Implement guarded restore and strict integrity verification**

  `restore-bundled.sh` requires `--env-file`, `--backup-dir`,
  `--target-project`, and `--target-data-dir`. Its default mode rejects an
  existing target data directory, uses `docker compose --project-name
  "$target_project"` to create fresh PostgreSQL/Qdrant volumes, and never calls
  `docker volume rm`. It exports `OKF_RUNTIME_ENV_FILE` to the supplied env file
  for every Compose call. It verifies the bundled mode, manifest checksums, pinned
  service compatibility, and the target names before starting only PostgreSQL
  and Qdrant with `--no-deps`.

  Add a distinct `--replace-existing --confirm-replace-existing` path. Before
  it removes resolved volumes, require that *all* containers in the target
  project are stopped and removed, print the two exact volume names, and reject
  names outside that project. This path is not used by CI or the ordinary
  recovery guide.

  Extend `check_integrity.py` with `--strict` and `--expected-manifest`. Strict
  mode exits non-zero when Qdrant cannot be queried, when zero documents are
  found but the manifest declares documents, or when document/chunk/concept/
  Qdrant-point totals differ from the manifest. It also verifies every archived
  original and attachment checksum recorded in the manifest; a warning is not a
  success. Unit tests must cover an unavailable Qdrant and an empty unexpected
  corpus.

  Restore PostgreSQL, Qdrant, and `data` in that order; then apply forward
  `alembic upgrade head` from the migration image and run
  `check_integrity.py --strict --expected-manifest /backup/manifest.json`.
  Start backend next, require its JSON health status `ready`, then start
  frontend. Never call `alembic downgrade`.

- [x] **Step 6: Add the systemd startup unit**

  Create `deploy/production/okf-knowledge.service` with a fixed
  `WorkingDirectory=/opt/okf-knowledge`, a mode-specific environment file, and
  this complete lifecycle unit:

  ```ini
   [Unit]
   Requires=docker.service
   Wants=network-online.target
   After=docker.service network-online.target

   [Service]
   Type=oneshot
   RemainAfterExit=yes
   WorkingDirectory=/opt/okf-knowledge
   EnvironmentFile=/opt/okf-knowledge/.env
   ExecStart=/usr/bin/docker compose --env-file /opt/okf-knowledge/.env up -d --wait
   ExecStop=/usr/bin/docker compose --env-file /opt/okf-knowledge/.env stop
   TimeoutStartSec=10min
   TimeoutStopSec=2min

   [Install]
   WantedBy=multi-user.target
  ```

  Bundled `.env` sets `COMPOSE_PROFILES=bundled`; external `.env` leaves it
  empty, so this single unit does not hard-code a profile. The backend
  healthcheck from Task 7 makes `up --wait` a readiness assertion rather than a
  container-start assertion. The staging drill must reboot the host, not merely
  call `systemctl restart`.

- [x] **Step 7: Run dry-run safety tests and a clean test-host drill**

  ```sh
  bash scripts/production/tests/test_backup_scripts.sh
   docker compose --env-file deploy/production/bundled.env.example up -d --wait
   docker compose exec -T backend python scripts/check_integrity.py --strict
   docker compose --env-file deploy/production/bundled.env.example down
  ```

  Expected: script tests pass, bundled start reaches JSON-ready health, strict
  integrity has no issues, and no database port is published. Separately run a
  nonempty fixture backup→default-safe restore drill and compare its manifest
  totals; dry-run tests alone are not restore evidence.

  Local Windows Docker evidence (2026-09-15): the nonempty fixture drill was
  completed in isolated projects `okfcleandrill` → `okfcleanrestore`. The
  fixture contained one document, one chunk, one concept, one attachment, and
  two Qdrant points. The manifest listed the corresponding totals, two file
  checksums, and five Compose images; after PostgreSQL/Qdrant/data recovery
  and forward migration, `check_integrity.py --strict --expected-manifest`
  exited zero. JSON health was `ok` with `pypdf-pdfium`, and no host port was
  published. The temporary projects, volumes, and data were removed. A host
  reboot/systemd acceptance and a production-configured LLM health check remain
  separate release-candidate steps.

## Task 9: Extend CI for provider, deployment, and disaster-recovery gates

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `docker-compose.ci-external.yml`
- Modify: `backend/tests/test_distribution_inventory.py`
- Modify: `backend/tests/test_storage_mode.py`

**Interfaces:**
- Consumes: standard backend image, the bundled profile, an external-storage CI
  overlay using `postgres-external` and `qdrant-external` service names, and a
  nonempty backup/restore fixture.
- Produces: separate CI evidence for source tests, image composition, bundled
  boot/migration, external boot/migration with migrator egress, and an actual
  isolated restore.

- [x] **Step 1: Add a failing CI fixture assertion for external topology**

  Create `docker-compose.ci-external.yml` that defines only CI storage services
  named `postgres-external` and `qdrant-external`; it must not reuse `postgres`
  or `qdrant`. Attach those services and the `migrate` override to the egress
  network named in `deploy/production/docker-compose.external.yml`, leaving the
  base `storage_network` isolated. Set its Qdrant data volume to an ephemeral
  CI volume. Add a script assertion that the external environment URL hosts
  equal those names, pass `STORAGE_MODE=external` validation, and allow the
  migration container to complete.

- [x] **Step 2: Expand the Docker CI job into explicit modes**

  Run these independent blocks:

  1. standard-image build, provider import, no-MuPDF inventory, SBOM generation;
  2. bundled `compose config`, PostgreSQL/Qdrant startup, migration twice, Alembic check, seed-script availability, `/health` provider report, and published-port inspection;
   3. external base compose plus `deploy/production/docker-compose.external.yml`
      and `docker-compose.ci-external.yml`, an explicit runtime env file,
      migration and JSON-ready `/health` smoke, assertion that the bundled
      profile was not enabled, and an assertion that the root `.env` contained
      different invalid storage hosts without affecting the run;
  4. invalid mixed-mode preflight invocation that must exit non-zero;
   5. shell backup/restore dry-run safety suite and a nonempty fixture backup
      restored into a new `--target-project`/`--target-data-dir`, followed by
      strict manifest integrity verification.

- [x] **Step 3: Ensure every CI storage start is cleaned independently**

  Use one cleanup step per compose project with `if: always()`. Each cleanup runs `down -v --remove-orphans` for only that project's files and environment, so a failed external-mode test cannot contaminate the bundled-mode evidence.

- [x] **Step 4: Add release-candidate manual acceptance commands to workflow comments**

  Document, without pretending CI performs it, the required staging-host drill:
  reboot the host, verify the unit becomes active and the backend JSON health is
  ready, record document/chunk/concept/Qdrant counts, create a backup, restore
  to a clean target project, compare the manifest totals, then run strict
  `check_integrity.py`.

- [x] **Step 5: Run the relevant checks locally before final review**

  ```powershell
  Set-Location doc-parser
  python -m pytest tests/test_pdf_provider.py tests/test_parse.py tests/test_attachments.py -q
  Set-Location ..\backend
  .\.venv\Scripts\python.exe -m pytest tests/test_pdf_provider_health.py tests/test_distribution_inventory.py tests/test_storage_mode.py -q
  Set-Location ..
   $env:OKF_RUNTIME_ENV_FILE = 'deploy/production/bundled.env.example'
   docker compose --env-file $env:OKF_RUNTIME_ENV_FILE config --quiet
  ```

  Expected: all focused tests and compose configuration checks pass.

## Task 10: Reconcile documentation and run the complete verification set

**Files:**
- Modify: `README.md`, `README.ru.md`, `README.de.md`, `README.es.md`
- Modify: `docs/PRODUCTION_DEPLOYMENT.md`
- Modify: `.env.example`
- Modify: `doc-parser/README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: final provider, deployment, license, and operation contracts from Tasks 1–9.
- Produces: operator/developer documentation that matches executable commands and no obsolete PyMuPDF/local-profile instructions.

- [x] **Step 1: Update parser documentation**

  In `doc-parser/README.md`, document pypdf+pypdfium2 responsibilities, the fact that scan rendering is serialized, the unchanged public API, and the absence of OCR. Remove PyMuPDF from dependency and license descriptions.

- [x] **Step 2: Update deployment documentation**

  In `docs/PRODUCTION_DEPLOYMENT.md`, replace the prior external-only production statement with the external/bundled matrix, exact startup commands, storage-network rule, secret requirements, systemd installation, backup/restore, update, and rollback procedures. State that external infrastructure remains responsible for its own backups.

- [x] **Step 3: Update developer operations notes**

  In `AGENTS.md`, retain Windows local-binary instructions but replace local compose profile names with `bundled` where applicable. Explain that production uses Linux Docker Compose/systemd and that developers must not enable a nonexistent PyMuPDF fallback.

- [x] **Step 4: Search for stale instructions**

  ```powershell
  rg -n -i "pymupdf|fitz|mupdf|local-postgres|local-qdrant|external.*only|agpl-3.0" README.md README.ru.md README.de.md README.es.md AGENTS.md docs doc-parser docker-compose.yml .env.example
  ```

  Expected: results appear only in `docs/PYMUPDF_PROVIDER.md`, historical release notes, and explicitly labelled migration history; no active install, build, or deployment command references them.

- [x] **Step 5: Run full language-specific regression suites**

  ```powershell
  Set-Location doc-parser
  python -m pytest -q
  Set-Location ..\backend
  .\.venv\Scripts\python.exe -m pytest -q
  Set-Location ..\frontend
  npm test
  npm run lint
  npm run build
  ```

  Expected: all suites pass. If a failure appears, identify whether it is caused by this change before altering unrelated work.

- [x] **Step 6: Perform the requirement-by-requirement completion audit**

  Record evidence for each completion criterion in the spec:

  | Requirement | Required evidence |
  |---|---|
   | No PyMuPDF/MuPDF in standard delivery | source `rg`, package inventory, final-image filesystem audit, Docker assertion, and SBOM |
  | PDF behavior preserved | full doc-parser suite plus focused provider/concurrency tests |
  | MIT and notices | root license, SPDX scan, package metadata, notices, CI gate |
  | Two storage modes only | unit matrix, both `compose config` outputs, rejected mixed invocation |
  | Bundled runtime isolation/persistence | staging startup, port inspection, host-reboot drill |
   | Backup/restore | verified nonempty staging restore into a new target project/volumes, strict manifest integrity result, and JSON-ready smoke |

  Do not report the work complete until every row has current output.

  Current audit (2026-09-15):

  | Requirement | Current evidence | Status |
  |---|---|---|
  | No PyMuPDF/MuPDF in standard delivery | Standard-source dependency/import scan is clean; `test_distribution_inventory.py` and `test_license_inventory.py` pass (4 tests); `audit-standard-image.sh`, an image runtime import assertion, and a freshly generated image SBOM all report `pypdf`/`pypdfium2` and no forbidden component. | verified |
  | PDF behavior preserved | The complete parser suite passes in the project runtime: 68 passed. The system Python lacks the declared `defusedxml` dependency, so it is not a valid test runtime; this is not a parser failure. | verified |
  | MIT and notices | The four license/distribution checks pass; they cover root MIT text, no project-owned AGPL SPDX marker, notices, package inventory, and deterministic SBOM generation. | verified |
  | Two storage modes only | Bundled and external Compose configurations validate, the storage-mode unit matrix passes, and an `external` invocation targeting `postgres`/`qdrant` is rejected. | verified |
  | Bundled runtime isolation/persistence | An isolated bundled project reached JSON health `ok` with `pypdf-pdfium`; PostgreSQL/Qdrant had no published host ports. The nonempty recovery drill established data persistence across new volumes. In CT 102 (`okf-staging`), a full Proxmox host reboot auto-started the CT, Docker, and `okf-knowledge.service`; backend/PostgreSQL were healthy, frontend returned HTTP 200, strict integrity reported 0 problematic documents, and only frontend port 8080 was published. | verified |
  | Backup/restore | Dry-run safety suite passes. A nonempty fixture recovered into a new target project/volumes; manifest totals, file checksums, strict integrity, and JSON health all passed. | verified |

  Regression evidence: the latest full backend run completed with 1495 passed
  and 5 skipped. The latest complete parser run completed with 68 passed.
  Frontend unit tests completed with 155 passed; ESLint completed with warnings
  but no errors. The current working UX was deliberately
  left running, so `next build` was not repeated against its shared `.next`
  output during this audit.

  The staging host was rebooted under `okf-knowledge.service`; the post-reboot
  service, health, port-isolation, and strict-integrity evidence above completes
  the audit. A local Compose restart was not used as a substitute.
