"""Транзакционное хранение дерева источников документа."""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import DocumentSource


def fetch_source_paths(session, pairs: set[tuple[str, str]]) -> dict[tuple[str, str], list[dict]]:
    """Batch-read complete ancestry in the caller's canonical read snapshot.

    Only allowlisted display metadata leaves storage. Broken trees yield no
    attribution; identical source IDs in different documents never share nodes.
    """
    if not pairs:
        return {}
    rows = session.scalars(select(DocumentSource).where(
        DocumentSource.doc_id.in_({doc_id for doc_id, _ in pairs})
    )).all()
    nodes = {(row.doc_id, row.source_id): row for row in rows}
    paths = {}
    for doc_id, source_id in pairs:
        chain = []
        seen = set()
        current = source_id
        while current is not None:
            row = nodes.get((doc_id, current))
            if row is None or current in seen or len(chain) >= 32:
                chain = []
                break
            seen.add(current)
            node = {
                "source_id": row.source_id,
                "kind": row.kind,
                "display_name": row.display_name[:256],
            }
            metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
            if row.kind == "mail" or metadata.get("mail") is True:
                node["mail"] = True
                for field, limit in (("subject", 512), ("sender", 512), ("sent_at", 64), ("date_raw", 128)):
                    value = metadata.get(field)
                    if isinstance(value, str) and value:
                        node[field] = value[:limit]
            chain.append(node)
            current = row.parent_source_id
        if chain and chain[-1]["source_id"] == "root":
            paths[(doc_id, source_id)] = list(reversed(chain))
    return paths


def replace_sources(session, doc_id: str, rows: list[dict]) -> None:
    """Атомарно заменяет дерево источников после валидации его структуры."""
    _validate_tree(rows)
    session.query(DocumentSource).filter(DocumentSource.doc_id == doc_id).delete(
        synchronize_session=False
    )
    for row in sorted(rows, key=lambda item: (item["source_id"].count("/"), item["source_id"])):
        session.add(
            DocumentSource(
                doc_id=doc_id,
                source_id=row["source_id"],
                parent_source_id=row.get("parent_source_id"),
                ordinal=int(row.get("ordinal", 0)),
                kind=row.get("kind", "document"),
                display_name=row.get("display_name", ""),
                metadata_json=row.get("metadata"),
                saved_path=row.get("saved_path"),
                extraction_status=row.get("extraction_status"),
                artifact_kind=row.get("artifact_kind", "original"),
                container_source_id=row.get("container_source_id"),
                container_locator=row.get("container_locator"),
                content_fingerprint=row.get("content_fingerprint"),
                parser_version=row.get("parser_version"),
                warnings=row.get("warnings"),
            )
        )
        session.flush()


def validate_source_references(session, doc_id: str, source_ids: set[str]) -> None:
    """Не даёт чанкам/вложениям сослаться на чужой или отсутствующий source_id."""
    if not source_ids:
        return
    known = set(
        session.scalars(
            select(DocumentSource.source_id).where(
                DocumentSource.doc_id == doc_id,
                DocumentSource.source_id.in_(source_ids),
            )
        )
    )
    unknown = sorted(source_ids - known)
    if unknown:
        raise ValueError(f"Неизвестный source_id для документа {doc_id}: {unknown}")


def _validate_tree(rows: list[dict]) -> None:
    ids = [str(row.get("source_id") or "") for row in rows]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Дерево источников должно содержать уникальные source_id")
    by_id = {row["source_id"]: row for row in rows}
    root = by_id.get("root")
    if root is None or root.get("parent_source_id") is not None:
        raise ValueError("Дерево источников должно содержать корень root без parent_source_id")
    for source_id, row in by_id.items():
        parent = row.get("parent_source_id")
        if source_id == "root":
            continue
        if not parent or parent not in by_id or parent == source_id:
            raise ValueError(f"Некорректный parent_source_id для {source_id}")
    # Validate every direct edge before following ancestor chains: input rows
    # may put a descendant before an ancestor with an invalid parent.
    for source_id, row in by_id.items():
        seen = {source_id}
        current = row.get("parent_source_id")
        while current is not None:
            if current in seen:
                raise ValueError(f"Цикл в дереве источников: {source_id}")
            seen.add(current)
            current = by_id[current].get("parent_source_id")
