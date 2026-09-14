"""Create a read-only reproducibility manifest for the Stage 8 probes."""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db.models import Document, DocumentChunk, DomainTerm, OkfConcept
from app.db.session import session_scope
from app.services.glossary.registry import get_glossary_registry
from app.services.vector_store import VectorStore
from test_scripts.probe_sources import load_cases


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _digest(value: Any) -> str:
    encoded = json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _runtime_snapshot(settings) -> dict[str, Any]:
    """Identify the measured contour without exposing connection credentials."""
    database_url = getattr(settings, "database_url", None) or getattr(settings, "database_url_dev", None)
    database_name = None
    if database_url:
        try:
            database_name = make_url(database_url).database
        except (TypeError, ValueError):
            database_name = None
    return {
        "knowledge_profile": getattr(settings, "knowledge_profile", None),
        "database_name": database_name,
        "data_dir": str(getattr(settings, "data_dir", "")),
        "qdrant_collection": getattr(settings, "qdrant_collection", None),
    }


def _label_snapshot(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return the complete label input snapshot and explicit approval state."""
    labels = {
        case["id"]: {
            "expected_canonicals": case.get("expected_canonicals", []),
            "forbidden_canonicals": case.get("forbidden_canonicals", []),
            "relevance": case.get("relevance", {}),
            "expected_documents": case.get("expected_documents", []),
            "mandatory_sources": case.get("mandatory_sources", []),
        }
        for case in payload.get("cases", [])
    }
    approval = payload.get("labels") or {}
    return labels, approval.get("approved") is True


def _file_hashes(root: Path, paths: list[Path]) -> dict[str, str]:
    result: dict[str, str] = {}

    def include(path: Path) -> bool:
        return path.is_file() and path.suffix not in {".pyc", ".pyo"} and "__pycache__" not in path.parts

    for path in sorted(paths):
        if include(path):
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if include(child):
                    result[str(child.relative_to(root))] = hashlib.sha256(child.read_bytes()).hexdigest()
    return result


def _db_snapshot() -> dict[str, Any]:
    with session_scope() as session:
        documents = session.execute(
            select(
                Document.id,
                Document.file_hash,
                Document.content_hash,
                Document.status,
                Document.updated_at,
                Document.deleted_at,
            ).order_by(Document.id)
        ).all()
        chunks = session.execute(
            select(
                DocumentChunk.doc_id,
                DocumentChunk.chunk_index,
                DocumentChunk.content_hash,
            ).order_by(DocumentChunk.doc_id, DocumentChunk.chunk_index)
        ).all()
        concepts = session.execute(
            select(
                OkfConcept.doc_id,
                OkfConcept.slug,
                OkfConcept.generated_at,
                OkfConcept.content,
                OkfConcept.tags,
            ).order_by(OkfConcept.doc_id, OkfConcept.slug)
        ).all()
        glossary = session.execute(
            select(DomainTerm.id, DomainTerm.version, DomainTerm.source_revision)
            .where(DomainTerm.enabled.is_(True))
            .order_by(DomainTerm.id)
        ).all()

    documents_value = [
        {
            "id": row.id,
            "file_hash": row.file_hash,
            "content_hash": row.content_hash,
            "status": row.status,
            "updated_at": row.updated_at,
            "deleted_at": row.deleted_at,
        }
        for row in documents
    ]
    chunks_value = [
        {"doc_id": row.doc_id, "chunk_index": row.chunk_index, "content_hash": row.content_hash}
        for row in chunks
    ]
    concepts_value = [
        {
            "doc_id": row.doc_id,
            "slug": row.slug,
            "generated_at": row.generated_at,
            "content_sha256": hashlib.sha256((row.content or "").encode("utf-8")).hexdigest(),
            "tags": row.tags or [],
        }
        for row in concepts
    ]
    glossary_value = [
        {"id": row.id, "version": row.version, "source_revision": row.source_revision}
        for row in glossary
    ]
    return {
        "documents": {"count": len(documents_value), "sha256": _digest(documents_value)},
        "document_chunks": {"count": len(chunks_value), "sha256": _digest(chunks_value)},
        "okf_concepts": {"count": len(concepts_value), "sha256": _digest(concepts_value)},
        "active_glossary_versions": {"count": len(glossary_value), "sha256": _digest(glossary_value)},
    }


def _qdrant_snapshot(settings) -> dict[str, Any]:
    store = VectorStore()
    records: list[dict[str, Any]] = []
    offset = None
    while True:
        points, offset = store.client.scroll(
            collection_name=settings.qdrant_collection,
            offset=offset,
            limit=256,
            with_payload=True,
            with_vectors=False,
        )
        records.extend(
            {"id": str(point.id), "payload": point.payload or {}}
            for point in points
        )
        if offset is None:
            break
    records.sort(key=lambda item: item["id"])
    return {"collection": settings.qdrant_collection, "count": len(records), "sha256": _digest(records)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("glossary-probe-cases.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    cases_path = args.cases.resolve()
    output_path = args.output.resolve()
    settings = get_settings()
    cases_payload = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = load_cases(cases_path)
    labels, labels_approved = _label_snapshot(cases_payload)
    snapshot = get_glossary_registry().snapshot()
    source_root = Path(__file__).resolve().parents[2]
    source_paths = [
        source_root / "backend" / "app" / "api" / "chat.py",
        source_root / "backend" / "app" / "api" / "search.py",
        source_root / "backend" / "app" / "config.py",
        source_root / "backend" / "app" / "services" / "context_builder.py",
        source_root / "backend" / "app" / "services" / "glossary",
        source_root / "backend" / "app" / "services" / "retrieval_hydration.py",
        source_root / "backend" / "app" / "services" / "search_filter.py",
        source_root / "backend" / "app" / "services" / "vector_store.py",
        source_root / "backend" / "test_scripts" / "probe_sources.py",
        source_root / "backend" / "test_scripts" / "glossary_probe_report.py",
        source_root / "backend" / "test_scripts" / "validate_stage8_labels.py",
        source_root / "backend" / "test_scripts" / "stage8_sla_validator.py",
        source_root / "backend" / "test_scripts" / "stage8_manifest.py",
    ]
    setting_names = (
        "glossary_query_expansion_enabled",
        "glossary_max_terms_per_query",
        "glossary_max_added_aliases_per_term",
        "glossary_max_added_tokens",
        "glossary_max_added_chars",
        "glossary_query_text_max_chars",
        "glossary_sparse_expansion_weight",
        "search_per_branch_top_k",
        "search_rrf_k",
        "embedding_provider",
        "embedding_model",
        "embedding_dimensions",
        "qdrant_collection",
    )
    manifest = {
        "schema_version": 1,
        "purpose": "stage8_acceptance_reproducibility",
        "runtime": _runtime_snapshot(settings),
        "cases": {
            "path": str(cases_path.relative_to(source_root)),
            "sha256": _digest(cases),
            "count": len(cases),
        },
        "labels": {"sha256": _digest(labels), "approved": labels_approved},
        "glossary": {
            "snapshot_sha256": _digest(snapshot),
            "snapshot_count": len(snapshot),
            "seed_path": "backend/seeds/glossary.json",
            "seed_sha256": hashlib.sha256((source_root / "backend" / "seeds" / "glossary.json").read_bytes()).hexdigest(),
        },
        "source_files": _file_hashes(source_root, source_paths),
        "models": {
            "embedding": getattr(settings, "embedding_model", None),
            "embedding_dimensions": getattr(settings, "embedding_dimensions", None),
            "llm": getattr(settings, "llm_model", None),
            "llm_chat": getattr(settings, "llm_chat_model", None) or getattr(settings, "llm_model", None),
        },
        "settings": {name: getattr(settings, name) for name in setting_names},
        "database": _db_snapshot(),
        "qdrant": _qdrant_snapshot(settings),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
