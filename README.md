# OKF Knowledge Service

Turn project documentation (DOCX / XLSX / PDF) into a searchable knowledge base: an LLM
splits documents into semantic **concepts** (Open Knowledge Format), a hybrid vector index
stores them for retrieval, and an interactive chat answers questions **grounded in your
documents with clickable source citations**.

Built to solve a real-world pain: a large body of accumulated functional and technical
specifications where plain full-text search cannot answer questions like *"how was the
`lnState` field implemented in development 111"* or *"which e-sick-leave statuses do we
handle"*. Keyword search finds word matches; it does not find meaning. This service does —
and always points back to the exact place in the source document.

## Key features

- **Semantic concept extraction (OKF).** An LLM breaks documents into meaningful concepts
  (a concept per message field, business rule, term definition) with YAML metadata
  (`type`, `title`, `tags`, `relations`) — not opaque full-text blobs.
- **Deterministic field-table extraction.** Enumeration and XML-field tables are processed
  *programmatically* (row-by-row concepts) with an optional LLM classifier — every row
  (`lnState`, `snils`, …) is guaranteed findable, even in 40+ row tables.
- **Reviewer comments as structured data.** `.docx` reviewer comments are parsed into
  question → answer threads, indexed and searchable under a `review` tag.
- **Hybrid retrieval.** Dual index over LLM concept summaries **and** raw chunk text; dense
  embeddings + sparse BM25 fused with Reciprocal Rank Fusion, plus graph expansion over
  concept relations. Tag-based pre-filtering. Three query modes: `dense`, `bm25`, `hybrid`.
- **Grounded RAG chat.** Answers are synthesized strictly from retrieved fragments and cite
  sources as `[N]` links, with source snippets shown in the UI.
- **PostgreSQL as the source of truth.** Documents, tags, concepts, chunks and staging live
  in PostgreSQL; Qdrant keeps slim vector projections and full text is hydrated on read.
- **Upload-time deduplication.** File hash (level 1) plus content hash + MinHash/LSH
  (levels 2–3) warn about duplicate or near-duplicate documents.
- **Authentication & RBAC.** Pluggable auth providers, Keycloak/OIDC (Authorization Code)
  SSO, roles `viewer` / `editor` / `admin` / `security`, fail-closed by default.
- **Security operations.** Append-only audit log (INSERT-only DB account on production),
  four-eyes approval and rate limits for bulk operations, user blocklist.
- **Production niceties.** Soft-delete trash with restore and retention-based purge, chat
  session history, document ↔ development/module registry for filtering and grouping.
- **Resilient LLM pipeline.** Streaming with idle timeouts, retry cascades, truncation
  recovery (max-token bump → chunk split → salvage), per-chunk checkpointing with `resume`.
- **Multilingual UI (RU/EN)** with data-driven stop words, reference-data translations and
  runtime UI dictionaries.

## Screenshots

| Q&A chat | Document upload & status | Sources under an answer |
|---|---|---|
| ![Chat interface](screenshots/screenshot-01.png) | ![Upload and processing status](screenshots/screenshot-02.png) | ![Sources block with citations](screenshots/screenshot-03.png) |

## How it works

```
Upload (docx / xlsx / pdf)
        │
        ▼
┌────────────────────────┐   ┌──────────────────────┐   ┌─────────────────────┐
│ Document parser        │──►│ OKF generator (LLM)  │──►│ PostgreSQL          │
│ docx/xlsx/pdf, tables, │   │ semantic chunking,   │   │ documents, tags,    │
│ reviewer comments,     │   │ field-table rows,    │   │ concepts, chunks    │
│ embedded attachments   │   │ comment threads      │   │ (source of truth)   │
└────────────────────────┘   └──────────────────────┘   └─────────┬───────────┘
                                                                  │ embed
                                                                  ▼
┌──────────────────┐   ┌───────────────────────────────────────────────────────┐
│ Chat Web UI      │◄──│ Qdrant: hybrid retrieval                              │
│ (Next.js)        │   │ concept + chunk dual index,                           │
└──────────────────┘   │ dense + BM25 + graph, RRF fusion, tag filters        │
                       └───────────────────────────────────────────────────────┘
```

1. **Upload & API** — Python **FastAPI**; `POST /api/documents`, asynchronous processing.
2. **Parsing** — the standalone **`doc-parser`** package: `python-docx` (text, reviewer
   comments, embedded OLE objects), `openpyxl` (sheets → Markdown), `pypdf`; recursive
   attachment extraction.
3. **OKF generation** — an LLM (local via Ollama/vLLM or cloud via LiteLLM/OpenRouter)
   splits text into concepts; enumeration and XML-field tables are extracted
   programmatically, per row.
4. **Storage** — PostgreSQL is the canonical store for metadata, concepts and chunks;
   Qdrant holds the vectors (dense + sparse BM25) for two point types: `concept`
   (LLM summaries) and `chunk` (raw text, so details the LLM might have dropped — URLs,
   codes, configs — are still findable).
5. **Search** — query is embedded and run through dense / bm25 / hybrid branches (RRF
   fusion in Python); hits are merged/collapsed per source fragment and tag-filtered.
6. **Chat** — the LLM writes an answer from the retrieved context and cites each fact
   `[N]`; sources (title, snippet, score) are returned separately and rendered as
   clickable links.

## Tech stack

| Layer | Technology |
| :-- | :-- |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, LiteLLM |
| Frontend | Next.js (App Router), JavaScript |
| Vector database | Qdrant (dense + sparse BM25 in one collection) |
| Metadata database | PostgreSQL (dev fallback: SQLite) |
| Parser | Custom `doc-parser` package (python-docx, openpyxl, pypdf) |
| LLM / embeddings | Any OpenAI-compatible endpoint (Ollama, vLLM, OpenRouter, …) |

## Quick start

### Docker (fully local stack)

```bash
cp .env.example .env
# Point .env at the local compose services and your model endpoint:
#   DATABASE_URL=postgresql+psycopg://postgres:<your-password>@postgres:5432/okf_knowledge
#   QDRANT_URL=http://qdrant:6333
#   LLM_BASE_URL / EMBEDDING_API_BASE = your model endpoint (e.g. http://host.docker.internal:11434)
#   ENVIRONMENT=development          # compose defaults to production (fail-fast)
docker compose --profile local-qdrant --profile local-postgres up --build
```

- UI: http://localhost:8080
- Profiles: `local-qdrant` / `local-postgres` start the bundled vector/metadata DBs;
  without them the backend connects to your own Qdrant / PostgreSQL via `.env`.

### Local development (no Docker)

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -e ../doc-parser      # document parsing package (editable)
pip install -r requirements.txt
uvicorn app.main:app --reload --port 18000

# 2. Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Configure model endpoints and authentication in `.env` (see `.env.example` — all keys are
documented there; never commit real secrets). Set `ENVIRONMENT=development` for local runs.

## Documentation

- **[SECURITY.md](SECURITY.md)** — security policy: threat model, trust boundaries,
  authentication/RBAC, audit log, retention. (English)
- Production deployment checklist: [PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md) *(Russian)*
- Architecture & roadmap: [OKF_Knowledge_Service_Roadmap.md](docs/OKF_Knowledge_Service_Roadmap.md) *(Russian)*
- Database schema & migrations: [MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md) *(Russian)*
- Search reproduction & quality: [SEARCH_REPRODUCTION.md](docs/SEARCH_REPRODUCTION.md) *(Russian)*
- End-user guide: [OKF_User_Guide.md](docs/OKF_User_Guide.md) / [PDF](docs/OKF_User_Guide.pdf) *(Russian)*
- SSO / Keycloak OIDC: [developer guide](docs/SSO_Keycloak_OIDC_Dev_Guide.md) and
  [testing guide](docs/SSO_TESTING_GUIDE.md) *(Russian)*
- Adding a language / stop-words admin instructions: [docs/ADD_LANGUAGE.md](docs/ADD_LANGUAGE.md) *(Russian)*
- [AGENTS.md](AGENTS.md) — engineering notes and operational quirks for AI coding agents
  (maintained in Russian; the deep engineering docs above are intentionally Russian —
  detailed developer documentation is kept in the author's working language).

## License

[GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

The PDF text-extraction dependency [PyMuPDF](https://github.com/pymupdf/PyMuPDF) is
AGPL-3.0, so this project is distributed under the same terms. AGPL additionally grants
copyleft protection to users of network/cloud deployments (source must be offered to
anyone using the service over a network).
