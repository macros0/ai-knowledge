from datetime import datetime, timezone

from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services.fusion import Hit
from app.services.retrieval_hydration import (
    _chunk_pairs_needed_for_merge,
    _dedupe_pairs,
    enrich_retrieval_hits,
    load_visible_retrieval_hits,
)


def test_dedupe_pairs_preserves_first_seen_order():
    assert _dedupe_pairs(
        [("doc-a", "slug-a"), ("doc-b", "slug-b"), ("doc-a", "slug-a")]
    ) == [("doc-a", "slug-a"), ("doc-b", "slug-b")]


def test_exact_filter_reads_full_chunk_even_when_multitopic_concepts_would_skip_it():
    from app.services.glossary.registry import GlossaryRegistry
    from app.services.glossary.expansion import prepare_query
    GlossaryRegistry().create(None, 'sap_transaction', 'PA30')
    groups = prepare_query('PA30', ui_locale='en', enabled=True).strict_groups
    content = 'Other information. ' * 100 + 'PA30'
    with session_scope() as session:
        session.add(Document(id='exact-full-chunk', filename='source.docx'))
        for slug in ('first', 'second'):
            session.add(OkfConcept(doc_id='exact-full-chunk', slug=slug, title=slug, content='Unrelated concept', chunk_index=0))
        session.add(DocumentChunk(doc_id='exact-full-chunk', chunk_index=0, content=content, char_count=len(content)))
    hits = [Hit(slug, 1.0, dict(point_type='concept', doc_id='exact-full-chunk', slug=slug, title=slug, chunk_index=0))
            for slug in ('first', 'second')]
    hits.append(Hit('chunk', 0.9, dict(point_type='chunk', doc_id='exact-full-chunk', chunk_index=0)))
    kept, _ = load_visible_retrieval_hits(hits, max_concept_chars=10, max_chunk_chars=10, exact_groups=groups)
    assert [hit.point_id for hit in kept] == ['chunk']
    assert kept[0].payload['content'] == content


def test_enrich_retrieval_hits_hydrates_concepts_and_chunks_together():
    with session_scope() as session:
        session.add(Document(id="doc-hydrate", filename="source.docx"))
        session.add(
            OkfConcept(
                doc_id="doc-hydrate",
                slug="concept-one",
                title="Concept one",
                content="Full concept content",
                chunk_index=0,
            )
        )
        session.add(
            DocumentChunk(
                doc_id="doc-hydrate",
                chunk_index=0,
                section_title="Section one",
                content="Full chunk content",
                char_count=19,
            )
        )

    hits = [
        Hit(
            point_id="concept-point",
            score=1.0,
            payload={
                "point_type": "concept",
                "doc_id": "doc-hydrate",
                "slug": "concept-one",
            },
        ),
        Hit(
            point_id="chunk-point",
            score=0.9,
            payload={
                "point_type": "chunk",
                "doc_id": "doc-hydrate",
                "chunk_index": 0,
            },
        ),
    ]

    result = enrich_retrieval_hits(hits)

    assert result is hits
    assert hits[0].payload["content"] == "Full concept content"
    assert hits[0].payload["filepath"] == "doc-hydrate/concept-one.md"
    assert hits[1].payload["content"] == "Full chunk content"
    assert hits[1].payload["section_title"] == "Section one"


def test_enrich_retrieval_hits_skips_chunk_content_for_multitopic_group(caplog):
    with session_scope() as session:
        session.add(Document(id="doc-hydrate-multitopic", filename="source.docx"))
        for slug, title, content in (
            ("concept-one", "Concept one", "Primary concept content"),
            ("concept-two", "Concept two", "Sibling concept content"),
        ):
            session.add(
                OkfConcept(
                    doc_id="doc-hydrate-multitopic",
                    slug=slug,
                    title=title,
                    content=content,
                    chunk_index=0,
                )
            )
        session.add(
            DocumentChunk(
                doc_id="doc-hydrate-multitopic",
                chunk_index=0,
                section_title="Section one",
                content="Raw chunk content is not needed here",
                char_count=36,
            )
        )

    hits = [
        Hit(
            point_id="concept-one-point",
            score=1.0,
            payload={
                "point_type": "concept",
                "doc_id": "doc-hydrate-multitopic",
                "slug": "concept-one",
                "chunk_index": 0,
                "title": "Concept one",
            },
        ),
        Hit(
            point_id="concept-two-point",
            score=0.9,
            payload={
                "point_type": "concept",
                "doc_id": "doc-hydrate-multitopic",
                "slug": "concept-two",
                "chunk_index": 0,
                "title": "Concept two",
            },
        ),
        Hit(
            point_id="chunk-point",
            score=0.8,
            payload={
                "point_type": "chunk",
                "doc_id": "doc-hydrate-multitopic",
                "chunk_index": 0,
                "section_title": "Section one",
            },
        ),
    ]

    enrich_retrieval_hits(hits)

    assert hits[0].payload["content"] == "Primary concept content"
    assert hits[1].payload["content"] == "Sibling concept content"
    assert "content" not in hits[2].payload
    assert "рассинхрон БД и Qdrant" not in caplog.text


def test_multitopic_hydration_keeps_chunk_fallback_for_empty_primary_digest():
    hits = [
        Hit(
            point_id="primary",
            score=1.0,
            payload={
                "point_type": "concept",
                "doc_id": "doc-1",
                "slug": "primary",
                "chunk_index": 0,
                "title": "Primary",
            },
        ),
        Hit(
            point_id="sibling",
            score=0.9,
            payload={
                "point_type": "concept",
                "doc_id": "doc-1",
                "slug": "sibling",
                "chunk_index": 0,
                "title": "Sibling",
            },
        ),
        Hit(
            point_id="chunk",
            score=0.8,
            payload={"point_type": "chunk", "doc_id": "doc-1", "chunk_index": 0},
        ),
    ]

    assert _chunk_pairs_needed_for_merge(
        hits, {("doc-1", "primary"): ""}, [("doc-1", 0)]
    ) == [("doc-1", 0)]


def test_multitopic_hydration_keeps_chunk_fallback_for_empty_primary_title():
    hits = [
        Hit(
            point_id="primary",
            score=1.0,
            payload={
                "point_type": "concept",
                "doc_id": "doc-1",
                "slug": "primary",
                "chunk_index": 0,
                "title": "",
            },
        ),
        Hit(
            point_id="sibling",
            score=0.9,
            payload={
                "point_type": "concept",
                "doc_id": "doc-1",
                "slug": "sibling",
                "chunk_index": 0,
                "title": "Sibling",
            },
        ),
        Hit(
            point_id="chunk",
            score=0.8,
            payload={"point_type": "chunk", "doc_id": "doc-1", "chunk_index": 0},
        ),
    ]

    assert _chunk_pairs_needed_for_merge(
        hits, {("doc-1", "primary"): "Primary content"}, [("doc-1", 0)]
    ) == [("doc-1", 0)]


def test_load_visible_retrieval_hits_filters_and_hydrates_in_one_retrieval_operation():
    with session_scope() as session:
        session.add(Document(id="doc-visible", filename="visible.docx"))
        session.add(
            Document(
                id="doc-deleted",
                filename="deleted.docx",
                deleted_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
            )
        )
        session.add(
            OkfConcept(
                doc_id="doc-visible",
                slug="concept",
                title="Visible concept",
                content="Canonical content",
                chunk_index=0,
            )
        )

    hits = [
        Hit(
            point_id="visible-point",
            score=1.0,
            payload={
                "point_type": "concept",
                "doc_id": "doc-visible",
                "slug": "concept",
            },
        ),
        Hit(
            point_id="deleted-point",
            score=0.9,
            payload={
                "point_type": "concept",
                "doc_id": "doc-deleted",
                "slug": "concept",
            },
        ),
        Hit(
            point_id="orphan-point",
            score=0.8,
            payload={
                "point_type": "concept",
                "doc_id": "doc-orphan",
                "slug": "concept",
            },
        ),
    ]

    visible, lookup = load_visible_retrieval_hits(hits)

    assert [hit.payload["doc_id"] for hit in visible] == ["doc-visible"]
    assert visible[0].payload["content"] == "Canonical content"
    assert lookup["doc-visible"]["filename"] == "visible.docx"
    assert lookup["doc-deleted"]["deleted_at"] is not None
    assert lookup["doc-orphan"] is None


def test_retrieval_hydration_limits_canonical_text_to_chat_output_bounds():
    with session_scope() as session:
        session.add(Document(id="doc-bounded", filename="bounded.docx"))
        session.add(
            OkfConcept(
                doc_id="doc-bounded",
                slug="concept",
                title="Bounded concept",
                content="c" * 5000,
                chunk_index=0,
            )
        )
        session.add(
            DocumentChunk(
                doc_id="doc-bounded",
                chunk_index=0,
                section_title="Bounded section",
                content="h" * 7000,
                char_count=7000,
            )
        )

    hits = [
        Hit(
            point_id="concept-point",
            score=1.0,
            payload={
                "point_type": "concept",
                "doc_id": "doc-bounded",
                "slug": "concept",
            },
        ),
        Hit(
            point_id="chunk-point",
            score=0.9,
            payload={
                "point_type": "chunk",
                "doc_id": "doc-bounded",
                "chunk_index": 0,
            },
        ),
    ]

    enrich_retrieval_hits(hits)

    assert len(hits[0].payload["content"]) == 4000
    assert len(hits[1].payload["content"]) == 6000


def test_visible_retrieval_hits_accept_search_specific_text_bounds():
    with session_scope() as session:
        session.add(Document(id="doc-search-bounded", filename="search.docx"))
        session.add(
            OkfConcept(
                doc_id="doc-search-bounded",
                slug="concept",
                title="Search concept",
                content="c" * 100,
                chunk_index=0,
            )
        )
        session.add(
            DocumentChunk(
                doc_id="doc-search-bounded",
                chunk_index=0,
                section_title="Search section",
                content="h" * 100,
                char_count=100,
            )
        )

    hits = [
        Hit(
            point_id="concept-point",
            score=1.0,
            payload={
                "point_type": "concept",
                "doc_id": "doc-search-bounded",
                "slug": "concept",
            },
        ),
        Hit(
            point_id="chunk-point",
            score=0.9,
            payload={
                "point_type": "chunk",
                "doc_id": "doc-search-bounded",
                "chunk_index": 0,
            },
        ),
    ]

    visible, _ = load_visible_retrieval_hits(
        hits, max_concept_chars=7, max_chunk_chars=11
    )

    assert len(visible[0].payload["content"]) == 7
    assert len(visible[1].payload["content"]) == 11
