# OKF Knowledge Service

[English](README.md) · [Русский](README.ru.md) · **Español** · [Deutsch](README.de.md)

Convierte la documentación de proyecto (DOCX / XLSX / PDF) en una base de conocimiento
consultable: un LLM divide los documentos en **conceptos** semánticos (Open Knowledge
Format), un índice vectorial híbrido los almacena para la recuperación y un chat
interactivo responde preguntas **basándose en tus documentos y citando las fuentes con
enlaces**.

Nace de un problema real: un gran volumen acumulado de especificaciones funcionales y
técnicas donde la búsqueda de texto completo no responde a preguntas como *«cómo se
implementó el campo `lnState` en el desarrollo 111»* o *«qué estados de baja médica
electrónica gestionamos»*. La búsqueda por palabras clave encuentra coincidencias de
palabras, no significado. Este servicio sí lo encuentra, y siempre remite al punto exacto
del documento de origen.

## Características principales

- **Extracción semántica de conceptos (OKF).** Un LLM descompone los documentos en
  conceptos con sentido propio (un concepto por campo de mensaje, regla de negocio o
  definición de término) con metadatos YAML (`type`, `title`, `tags`, `relations`), en
  lugar de bloques opacos de texto completo.
- **Extracción determinista de tablas de campos.** Las tablas de enumeraciones y de campos
  XML se procesan *programáticamente* (un concepto por fila), con un clasificador LLM
  opcional: cada fila (`lnState`, `snils`, …) se encuentra con seguridad, incluso en tablas
  de más de 40 filas.
- **Comentarios de revisión como datos estructurados.** Los comentarios de revisión de
  `.docx` se convierten en hilos «pregunta → respuesta», indexados y consultables bajo la
  etiqueta `review`.
- **Recuperación híbrida.** Índice dual sobre los resúmenes de conceptos del LLM **y** el
  texto bruto de los fragmentos; embeddings densos + BM25 disperso fusionados con
  Reciprocal Rank Fusion, más expansión por el grafo de relaciones entre conceptos.
  Prefiltrado por etiquetas. Tres modos de consulta: `dense`, `bm25`, `hybrid`.
- **Chat RAG con respuestas fundamentadas.** Las respuestas se sintetizan estrictamente a
  partir de los fragmentos recuperados y citan las fuentes como enlaces `[N]`, mostrando
  extractos de cada fuente en la interfaz.
- **PostgreSQL como fuente de verdad.** Documentos, etiquetas, conceptos, fragmentos y
  staging viven en PostgreSQL; Qdrant guarda proyecciones vectoriales ligeras y el texto
  completo se recupera en la lectura.
- **Deduplicación en la carga.** El hash del archivo (nivel 1) más el hash de contenido y
  MinHash/LSH (niveles 2–3) avisan de documentos duplicados o casi duplicados.
- **Autenticación y RBAC.** Proveedores de autenticación intercambiables, SSO con
  Keycloak/OIDC (Authorization Code), roles `viewer` / `editor` / `admin` / `security`,
  fail-closed por defecto.
- **Operaciones de seguridad.** Registro de auditoría de solo anexado (cuenta de BD con
  permiso únicamente de INSERT en producción), aprobación a cuatro ojos y límites de
  frecuencia para operaciones masivas, lista de bloqueo de usuarios.
- **Detalles para producción.** Papelera con borrado lógico, restauración y purga por
  política de retención; historial de sesiones de chat; registro de
  documento ↔ desarrollo/módulo para filtrar y agrupar.
- **Canalización LLM resistente a fallos.** Streaming con tiempos de espera por
  inactividad, cascadas de reintentos, recuperación ante truncamientos (subir max tokens →
  dividir el fragmento → salvage) y puntos de control por fragmento con `resume`.
- **Interfaz multilingüe (RU/EN)** con listas de palabras vacías gestionadas como datos,
  traducciones de los datos de referencia y diccionarios de UI cargados en tiempo de
  ejecución.

## Capturas de pantalla

| Chat de preguntas y respuestas | Carga de documentos y estado | Fuentes bajo una respuesta |
|---|---|---|
| ![Interfaz de chat](screenshots/screenshot-01.png) | ![Carga y estado de procesamiento](screenshots/screenshot-02.png) | ![Bloque de fuentes con citas](screenshots/screenshot-03.png) |

## Cómo funciona

```
Carga (docx / xlsx / pdf)
        │
        ▼
┌──────────────────────────┐   ┌────────────────────────────┐   ┌────────────────────────┐
│ Analizador de documentos │──►│ Generador OKF (LLM)        │──►│ PostgreSQL             │
│ docx/xlsx/pdf, tablas,   │   │ segmentación semántica,    │   │ documentos, etiquetas, │
│ comentarios de revisión, │   │ filas de tablas de campos, │   │ conceptos, fragmentos  │
│ adjuntos incrustados     │   │ hilos de comentarios       │   │ (fuente de verdad)     │
└──────────────────────────┘   └────────────────────────────┘   └──────┬─────────────────┘
                                                                       │ embeddings
                                                                       ▼
┌───────────────┐   ┌─────────────────────────────────────────────────────────┐
│ Chat web (UI) │◄──│ Qdrant: búsqueda híbrida                                │
│ (Next.js)     │   │ índice dual de conceptos y fragmentos,                  │
└───────────────┘   │ denso + BM25 + grafo, fusión RRF, filtros por etiquetas │
                    └─────────────────────────────────────────────────────────┘
```

1. **Carga y API** — Python **FastAPI**; `POST /api/documents`, procesamiento asíncrono.
2. **Análisis** — el paquete independiente **`doc-parser`**: `python-docx` (texto,
   comentarios de revisión, objetos OLE incrustados), `openpyxl` (hojas → Markdown),
   `pypdf`; extracción recursiva de adjuntos.
3. **Generación OKF** — un LLM (local con Ollama/vLLM o en la nube con LiteLLM/OpenRouter)
   divide el texto en conceptos; las tablas de enumeraciones y de campos XML se extraen
   programáticamente, fila a fila.
4. **Almacenamiento** — PostgreSQL es el almacén canónico de metadatos, conceptos y
   fragmentos; Qdrant guarda los vectores (densos + BM25 disperso) para dos tipos de
   puntos: `concept` (resúmenes del LLM) y `chunk` (texto bruto, para que los detalles que
   el LLM pudiera haber descartado —URL, códigos, configuraciones— sigan siendo
   localizables).
5. **Búsqueda** — la consulta se convierte en embedding y se ejecuta por las ramas dense /
   bm25 / hybrid (fusión RRF en Python); los resultados se combinan y colapsan por
   fragmento de origen y se filtran por etiquetas.
6. **Chat** — el LLM redacta la respuesta a partir del contexto recuperado y cita cada dato
   con `[N]`; las fuentes (título, extracto, puntuación) se devuelven por separado y se
   muestran como enlaces.

## Pila tecnológica

| Capa | Tecnología |
| :-- | :-- |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, LiteLLM |
| Frontend | Next.js (App Router), JavaScript |
| Base de datos vectorial | Qdrant (denso + BM25 disperso en una sola colección) |
| Base de datos de metadatos | PostgreSQL (alternativa de desarrollo: SQLite) |
| Analizador | Paquete propio `doc-parser` (python-docx, openpyxl, pypdf) |
| LLM / embeddings | Cualquier endpoint compatible con OpenAI (Ollama, vLLM, OpenRouter, …) |

## Inicio rápido

### Docker (pila totalmente local)

```bash
cp .env.example .env
# Apunta .env a los servicios locales de compose y al endpoint de tu modelo:
#   DATABASE_URL=postgresql+psycopg://postgres:<tu-contraseña>@postgres:5432/okf_knowledge
#   QDRANT_URL=http://qdrant:6333
#   LLM_BASE_URL / EMBEDDING_API_BASE = endpoint de tu modelo (p. ej. http://host.docker.internal:11434)
#   ENVIRONMENT=development          # compose usa production por defecto (fail-fast)
docker compose --profile local-qdrant --profile local-postgres up --build
```

- Interfaz: http://localhost:8080
- Perfiles: `local-qdrant` / `local-postgres` levantan las bases vectorial y de metadatos
  incluidas; sin ellos el backend se conecta a tus propios Qdrant / PostgreSQL vía `.env`.

### Desarrollo local (sin Docker)

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -e ../doc-parser      # paquete de análisis de documentos (editable)
pip install -r requirements.txt
uvicorn app.main:app --reload --port 18000

# 2. Frontend (otra terminal)
cd frontend
npm install
npm run dev
```

Configura los endpoints de los modelos y la autenticación en `.env` (consulta
`.env.example`: todas las claves están documentadas ahí; nunca subas secretos reales al
repositorio). Usa `ENVIRONMENT=development` para las ejecuciones locales.

## Documentación

- **[SECURITY.md](SECURITY.md)** — política de seguridad: modelo de amenazas, fronteras de
  confianza, autenticación/RBAC, registro de auditoría, retención. *(en inglés)*
- Lista de verificación para despliegue en producción: [PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md) *(en ruso)*
- Arquitectura y hoja de ruta: [OKF_Knowledge_Service_Roadmap.md](docs/OKF_Knowledge_Service_Roadmap.md) *(en ruso)*
- Esquema de base de datos y migraciones: [MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md) *(en ruso)*
- Reproducibilidad y calidad de la búsqueda: [SEARCH_REPRODUCTION.md](docs/SEARCH_REPRODUCTION.md) *(en ruso)*
- Guía de usuario final: [OKF_User_Guide.md](docs/OKF_User_Guide.md) / [PDF](docs/OKF_User_Guide.pdf) *(en ruso)*
- SSO / Keycloak OIDC: [guía para desarrolladores](docs/SSO_Keycloak_OIDC_Dev_Guide.md) y
  [guía de pruebas](docs/SSO_TESTING_GUIDE.md) *(en ruso)*
- Añadir un idioma e instrucciones de administración de palabras vacías: [docs/ADD_LANGUAGE.md](docs/ADD_LANGUAGE.md) *(en ruso)*
- [AGENTS.md](AGENTS.md) — notas de ingeniería y peculiaridades operativas para agentes de
  programación con IA (se mantiene en ruso; la documentación técnica detallada anterior
  también está en ruso de forma deliberada: se escribe en el idioma de trabajo del autor).

## Licencia

[GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

La dependencia de extracción de texto de PDF [PyMuPDF](https://github.com/pymupdf/PyMuPDF)
es AGPL-3.0, por lo que este proyecto se distribuye bajo los mismos términos. La AGPL añade
además protección copyleft para los usuarios de despliegues en red o en la nube: el código
fuente debe ofrecerse a cualquiera que use el servicio a través de la red.
