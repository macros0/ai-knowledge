# OKF Knowledge Service

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · **Deutsch**

Verwandelt Projektdokumentation (DOCX / XLSX / PDF) in eine durchsuchbare Wissensbasis: Ein
LLM zerlegt Dokumente in semantische **Konzepte** (Open Knowledge Format), ein hybrider
Vektorindex hält sie für die Suche vor, und ein interaktiver Chat beantwortet Fragen
**belegt aus Ihren Dokumenten – mit anklickbaren Quellenangaben**.

Entstanden aus einem realen Problem: ein großer gewachsener Bestand an Fach- und
Technikspezifikationen, bei dem eine reine Volltextsuche Fragen wie *„wie wurde das Feld
`lnState` in Entwicklung 111 umgesetzt“* oder *„welche Status elektronischer
Krankschreibungen verarbeiten wir“* nicht beantwortet. Die Stichwortsuche findet
Wortübereinstimmungen, keine Bedeutung. Dieser Dienst findet Bedeutung – und verweist immer
auf die genaue Stelle im Ausgangsdokument.

## Kernfunktionen

- **Semantische Konzeptextraktion (OKF).** Ein LLM zerlegt Dokumente in inhaltlich
  sinnvolle Konzepte (ein Konzept je Nachrichtenfeld, Geschäftsregel oder
  Begriffsdefinition) mit YAML-Metadaten (`type`, `title`, `tags`, `relations`) – statt
  undurchsichtiger Volltextblöcke.
- **Deterministische Extraktion von Feldtabellen.** Aufzählungs- und XML-Feldtabellen
  werden *programmatisch* verarbeitet (ein Konzept je Zeile), optional mit einem
  LLM-Klassifikator – jede Zeile (`lnState`, `snils`, …) ist garantiert auffindbar, auch in
  Tabellen mit über 40 Zeilen.
- **Reviewer-Kommentare als strukturierte Daten.** Kommentare aus `.docx` werden in
  Threads „Frage → Antwort“ überführt, indexiert und unter dem Tag `review` durchsuchbar.
- **Hybride Suche.** Dualer Index über die LLM-Konzeptzusammenfassungen **und** den rohen
  Chunk-Text; dichte Embeddings + sparse BM25, zusammengeführt per Reciprocal Rank Fusion,
  dazu Graph-Expansion über die Konzeptbeziehungen. Vorfilterung nach Tags. Drei
  Abfragemodi: `dense`, `bm25`, `hybrid`.
- **Belegter RAG-Chat.** Antworten werden ausschließlich aus den gefundenen Fragmenten
  synthetisiert und zitieren die Quellen als `[N]`-Links; Auszüge der Quellen erscheinen in
  der Oberfläche.
- **PostgreSQL als Single Source of Truth.** Dokumente, Tags, Konzepte, Chunks und Staging
  liegen in PostgreSQL; Qdrant hält schlanke Vektorprojektionen, der Volltext wird beim
  Lesen nachgeladen.
- **Deduplizierung beim Upload.** Datei-Hash (Stufe 1) sowie Inhalts-Hash und MinHash/LSH
  (Stufen 2–3) warnen vor doppelten oder nahezu doppelten Dokumenten.
- **Authentifizierung & RBAC.** Austauschbare Auth-Provider, SSO über Keycloak/OIDC
  (Authorization Code), Rollen `viewer` / `editor` / `admin` / `security`, standardmäßig
  fail-closed.
- **Security-Betrieb.** Append-only-Audit-Log (in der Produktion ein DB-Konto mit reinem
  INSERT-Recht), Vier-Augen-Freigabe und Rate-Limits für Massenoperationen,
  Benutzer-Sperrliste.
- **Produktionstaugliche Extras.** Papierkorb mit Soft-Delete, Wiederherstellung und
  aufbewahrungsbasierter Bereinigung, Verlauf der Chat-Sitzungen, Register
  Dokument ↔ Entwicklung/Modul zum Filtern und Gruppieren.
- **Robuste LLM-Pipeline.** Streaming mit Idle-Timeouts, Retry-Kaskaden, Erholung nach
  Abschneiden (Max-Token erhöhen → Chunk teilen → Salvage), Checkpoints je Chunk mit
  `resume`.
- **Mehrsprachige Oberfläche (RU/EN)** mit datengetriebenen Stoppwörtern, Übersetzungen der
  Referenzdaten und zur Laufzeit geladenen UI-Wörterbüchern.

## Screenshots

| Frage-Antwort-Chat | Dokument-Upload und Status | Quellen unter einer Antwort |
|---|---|---|
| ![Chat-Oberfläche](screenshots/screenshot-01.png) | ![Upload und Verarbeitungsstatus](screenshots/screenshot-02.png) | ![Quellenblock mit Zitaten](screenshots/screenshot-03.png) |

## Funktionsweise

```
Upload (docx / xlsx / pdf)
        │
        ▼
┌──────────────────────────┐   ┌──────────────────────────┐   ┌──────────────────────────┐
│ Dokument-Parser          │──►│ OKF-Generator (LLM)      │──►│ PostgreSQL               │
│ docx/xlsx/pdf, Tabellen, │   │ semantisches Chunking,   │   │ Dokumente, Tags,         │
│ Reviewer-Kommentare,     │   │ Zeilen aus Feldtabellen, │   │ Konzepte, Chunks         │
│ eingebettete Anhänge     │   │ Kommentar-Threads        │   │ (Single Source of Truth) │
└──────────────────────────┘   └──────────────────────────┘   └────────────┬─────────────┘
                                                                           │ Embeddings
                                                                           ▼
                     ┌─────────────┐      ┌──────────────────────────────────────────────┐
                     │ Chat-Web-UI │◄─────│ Qdrant: hybride Suche                        │
                     │ (Next.js)   │      │ dualer Index aus Konzepten und Chunks,       │
                     └─────────────┘      │ dense + BM25 + Graph, RRF-Fusion, Tag-Filter │
                                          └──────────────────────────────────────────────┘
```

1. **Upload & API** — Python **FastAPI**; `POST /api/documents`, asynchrone Verarbeitung.
2. **Parsing** — das eigenständige Paket **`doc-parser`**: `python-docx` (Text,
   Reviewer-Kommentare, eingebettete OLE-Objekte), `openpyxl` (Blätter → Markdown),
   `pypdf`; rekursives Extrahieren von Anhängen.
3. **OKF-Generierung** — ein LLM (lokal über Ollama/vLLM oder in der Cloud über
   LiteLLM/OpenRouter) zerlegt den Text in Konzepte; Aufzählungs- und XML-Feldtabellen
   werden programmatisch Zeile für Zeile extrahiert.
4. **Speicherung** — PostgreSQL ist der kanonische Speicher für Metadaten, Konzepte und
   Chunks; Qdrant hält die Vektoren (dense + sparse BM25) für zwei Punkttypen: `concept`
   (LLM-Zusammenfassungen) und `chunk` (Rohtext, damit Details, die das LLM verworfen haben
   könnte – URLs, Codes, Konfigurationen – weiterhin auffindbar bleiben).
5. **Suche** — die Anfrage wird eingebettet und über die Zweige dense / bm25 / hybrid
   ausgeführt (RRF-Fusion in Python); Treffer werden je Quellfragment zusammengeführt bzw.
   zusammengefasst und nach Tags gefiltert.
6. **Chat** — das LLM formuliert die Antwort aus dem gefundenen Kontext und belegt jede
   Aussage mit `[N]`; die Quellen (Titel, Auszug, Score) werden separat zurückgegeben und
   als anklickbare Links dargestellt.

## Technologie-Stack

| Schicht | Technologie |
| :-- | :-- |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, LiteLLM |
| Frontend | Next.js (App Router), JavaScript |
| Vektordatenbank | Qdrant (dense + sparse BM25 in einer Collection) |
| Metadatenbank | PostgreSQL (Fallback für die Entwicklung: SQLite) |
| Parser | Eigenes Paket `doc-parser` (python-docx, openpyxl, pypdf) |
| LLM / Embeddings | Beliebiger OpenAI-kompatibler Endpoint (Ollama, vLLM, OpenRouter, …) |

## Schnellstart

### Docker (lokaler bundled-Stack)

```bash
cp deploy/production/bundled.env.example .docker.local.env
# PostgreSQL-Passwort und LLM-/Embedding-Endpunkte setzen. Für einen lokalen
# UX-Smoke-Test ENVIRONMENT=development und AUTH_PROVIDER=disabled setzen.
export OKF_RUNTIME_ENV_FILE=.docker.local.env
docker compose --env-file "$OKF_RUNTIME_ENV_FILE" up -d --build
```

- Oberfläche: http://localhost:8080
- `STORAGE_MODE=bundled` startet PostgreSQL und Qdrant im einzelnen Profil
  `bundled` ohne Host-Ports. Für kundenseitig betriebene Datenbanken die Vorlage
  `external.env.example` mit `deploy/production/docker-compose.external.yml` nutzen.

### Lokale Entwicklung (ohne Docker)

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -e ../doc-parser      # Paket zum Parsen von Dokumenten (editable)
pip install -r requirements.txt
uvicorn app.main:app --reload --port 18000

# 2. Frontend (eigenes Terminal)
cd frontend
npm install
npm run dev
```

Modell-Endpoints und Authentifizierung werden in `.env` konfiguriert (siehe
`.env.example` – dort sind alle Schlüssel dokumentiert; echte Geheimnisse niemals
committen). Für lokale Läufe `ENVIRONMENT=development` setzen.

## Dokumentation

- **[SECURITY.md](SECURITY.md)** — Sicherheitsrichtlinie: Bedrohungsmodell,
  Vertrauensgrenzen, Authentifizierung/RBAC, Audit-Log, Aufbewahrung. *(englisch)*
- Checkliste für das Produktions-Deployment: [PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md) *(russisch)*
- Architektur & Roadmap: [OKF_Knowledge_Service_Roadmap.md](docs/OKF_Knowledge_Service_Roadmap.md) *(russisch)*
- Datenbankschema & Migrationen: [MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md) *(russisch)*
- Reproduzierbarkeit und Qualität der Suche: [SEARCH_REPRODUCTION.md](docs/SEARCH_REPRODUCTION.md) *(russisch)*
- Endnutzer-Handbuch: [OKF_User_Guide.md](docs/OKF_User_Guide.md) / [PDF](docs/OKF_User_Guide.pdf) *(russisch)*
- SSO / Keycloak OIDC: [Entwicklerhandbuch](docs/SSO_Keycloak_OIDC_Dev_Guide.md) und
  [Testhandbuch](docs/SSO_TESTING_GUIDE.md) *(russisch)*
- Eine Sprache hinzufügen / Admin-Anleitung zu Stoppwörtern: [docs/ADD_LANGUAGE.md](docs/ADD_LANGUAGE.md) *(russisch)*
- [AGENTS.md](AGENTS.md) — Engineering-Notizen und betriebliche Eigenheiten für
  KI-Coding-Agenten (auf Russisch gepflegt; auch die ausführliche technische Dokumentation
  oben ist bewusst russisch – sie entsteht in der Arbeitssprache des Autors).

## Lizenz

[MIT-Lizenz](LICENSE). Vor dieser Lizenzänderung veröffentlichte Releases bleiben
unter ihren ursprünglichen AGPL-3.0-Bedingungen. Der Standard-PDF-Provider ist
pypdf + pypdfium2; Hinweise zu Drittkomponenten stehen in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
