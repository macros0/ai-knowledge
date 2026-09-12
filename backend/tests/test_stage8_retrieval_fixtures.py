from __future__ import annotations

import json
from pathlib import Path

from app.services import search_filter
from app.services.registry import DocumentRegistry
from app.services.vector_store import VectorStore


FIXTURE = Path(__file__).with_name("fixtures") / "stage8_retrieval_documents.json"


class _Hit:
    def __init__(self, doc_id: str):
        self.payload = {"doc_id": doc_id}


def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_covers_original_and_ui_locale_variants_and_filters():
    rows = _load_fixture()
    by_id = {row["id"]: row for row in rows}
    assert by_id["stg8deoriginal01"]["source_locale"] == "de"
    assert by_id["stg8deoriginal01"]["ui_locale"] == "en"
    assert by_id["stg8undoriginal"]["source_locale"] is None
    assert by_id["stg8undoriginal"]["ui_locale"] == "ru"
    assert any(row["deleted"] for row in rows)
    assert any(row.get("orphan") for row in rows)
    assert any(row["dev_tags"] for row in rows)

    de_filter = VectorStore._build_search_filter(
        by_id["stg8deoriginal01"]["tags"],
        source_locales=[by_id["stg8deoriginal01"]["source_locale"]],
    )
    assert len(de_filter.must) == 2
    assert de_filter.must_not

    unknown_filter = VectorStore._build_search_filter(
        by_id["stg8undoriginal"]["tags"],
        source_locales=[],
        include_unknown=True,
    )
    assert len(unknown_filter.must) == 2
    assert unknown_filter.must_not


def test_fixture_visibility_excludes_deleted_and_orphan_points():
    rows = _load_fixture()
    registry = DocumentRegistry()
    for row in rows:
        if row.get("orphan"):
            continue
        registry.create(row["id"], row["filename"], "application/octet-stream", 1, tags=row["tags"])
        registry.update(
            row["id"],
            source_locale=row["source_locale"],
            source_locale_source=row["source_locale_source"],
        )
        if row["deleted"]:
            registry.soft_delete(row["id"], "stage8-fixture")

    hits = [_Hit(row["id"]) for row in rows]
    lookup = search_filter.build_doc_lookup(hits)
    visible = search_filter.drop_invisible_hits(hits, lookup)

    assert [hit.payload["doc_id"] for hit in visible] == [
        "stg8deoriginal01",
        "stg8undoriginal",
        "stg8ruoriginal1",
    ]
    assert lookup["stg8deleted001"]["deleted_at"] is not None
    assert lookup["stg8orphan001"] is None
