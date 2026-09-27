from datetime import datetime, timezone

from app.db.models import Document, DocumentChunk, DocumentSource, OkfConcept
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


def test_enrich_retrieval_hits_uses_canonical_source_id_when_qdrant_payload_is_legacy():
    with session_scope() as session:
        session.add(Document(id="mail-hydrate", filename="archive.docx"))
        session.add_all([
            DocumentSource(
                doc_id="mail-hydrate", source_id="root", ordinal=0, kind="document",
                display_name="archive.docx", artifact_kind="original",
            ),
            DocumentSource(
                doc_id="mail-hydrate", source_id="root/0", parent_source_id="root", ordinal=0,
                kind="mail", display_name="decision.eml", artifact_kind="original",
            ),
            OkfConcept(doc_id="mail-hydrate", slug="decision", title="Решение", content="Лимит 12 дней.", chunk_index=0, source_id="root/0"),
            DocumentChunk(doc_id="mail-hydrate", chunk_index=0, content="Лимит 12 дней.", char_count=15, source_id="root/0"),
        ])
    hits = [
        Hit("concept", 1.0, {"point_type": "concept", "doc_id": "mail-hydrate", "slug": "decision", "chunk_index": 0}),
        Hit("chunk", 0.9, {"point_type": "chunk", "doc_id": "mail-hydrate", "chunk_index": 0}),
    ]

    enrich_retrieval_hits(hits)

    assert [hit.payload["source_id"] for hit in hits] == ["root/0", "root/0"]


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


import pytest


def _mail_filter_records(chunk_source='mail', concept_source='mail', *, chunk_exists=True):
    with session_scope() as session:
        session.add(Document(id='scope-doc', filename='archive.docx'))
        session.add_all([
            DocumentSource(doc_id='scope-doc', source_id='root', kind='document', display_name='archive.docx'),
            DocumentSource(doc_id='scope-doc', source_id='mail', parent_source_id='root',
                           kind='mail', display_name='letter.eml', metadata_json={'subject': 'PRIVATE_PARENT'}),
            DocumentSource(doc_id='scope-doc', source_id='attachment', parent_source_id='mail',
                           kind='document', display_name='attached.docx'),
        ])
        for slug in ('one', 'two'):
            session.add(OkfConcept(doc_id='scope-doc', slug=slug, title='Shared topic',
                                  content='CANONICAL_DIGEST', source_id=concept_source, chunk_index=7))
        if chunk_exists:
            session.add(DocumentChunk(doc_id='scope-doc', chunk_index=7, source_id=chunk_source,
                                      content='CANONICAL_FRAGMENT', section_title='Shared topic'))
    return [Hit(slug, 1.0, dict(point_type='concept', doc_id='scope-doc', slug=slug,
             title='Shared topic', chunk_index=7, mail_scope='document', mail_scope_version=1,
             source_id='forged', source_path=[{'subject': 'FORGED_PATH'}],
             mail_fragment={'content': 'FORGED_FRAGMENT'}, _canonical_verified=True,
             _mail_components=[{'mail_scope': 'document'}], content='FORGED_CONTENT'))
            for slug in ('one', 'two')]


def test_payload_document_sql_mail_is_excluded_and_logged_without_private_data(caplog):
    import logging
    hits = _mail_filter_records()
    with caplog.at_level(logging.INFO):
        kept, _ = load_visible_retrieval_hits(hits, mail_mode='exclude')
    assert kept == []
    assert 'chat_mail_scope_filter' in caplog.text
    assert 'payload_mismatch' in caplog.text
    assert all(secret not in caplog.text for secret in ('PRIVATE_PARENT', 'CANONICAL', 'FORGED'))


def test_canonical_identity_replaces_forged_provenance_and_chunk_index():
    hits = _mail_filter_records()
    hits[0].payload['chunk_index'] = 99
    kept, _ = load_visible_retrieval_hits(hits, mail_mode='only')
    assert len(kept) == 2
    for hit in kept:
        assert hit.payload['source_id'] == 'mail'
        assert hit.payload['chunk_index'] == 7
        assert hit.payload['mail_scope'] == 'mail'
        assert hit.payload['_canonical_verified'] is True
        assert hit.payload['generation_id'] is None
        assert hit.payload['content'] == 'CANONICAL_DIGEST'
        assert hit.payload['mail_fragment']['content'] == 'CANONICAL_FRAGMENT'
        assert 'FORGED' not in str(hit.payload)


def test_only_attachment_does_not_load_parent_mail_fragment():
    hits = _mail_filter_records(chunk_source='attachment', concept_source='attachment')
    kept, _ = load_visible_retrieval_hits(hits, mail_mode='only')
    assert kept
    for hit in kept:
        assert hit.payload['content'] == 'CANONICAL_DIGEST'
        assert hit.payload['source_id'] == 'attachment'
        assert not hit.payload.get('mail_fragment')
        assert [node['source_id'] for node in hit.payload['source_path']] == ['root', 'mail', 'attachment']


@pytest.mark.parametrize('chunk_source,chunk_exists,expected', [('root', True, ['one', 'two', 'chunk']),
                                                               ('mail', True, []),
                                                               (None, True, ['one', 'two']),
                                                               ('root', False, ['one', 'two'])])
def test_skipped_chunk_text_still_checks_identity_and_scope(chunk_source, chunk_exists, expected):
    hits = _mail_filter_records(chunk_source=chunk_source, concept_source='root', chunk_exists=chunk_exists)
    hits.append(Hit('chunk', .9, dict(point_type='chunk', doc_id='scope-doc', chunk_index=7,
                                    mail_scope='document', _canonical_verified=True)))
    kept, _ = load_visible_retrieval_hits(hits, mail_mode='exclude')
    assert [hit.point_id for hit in kept] == expected
    for hit in kept:
        assert hit.payload['source_id'] == 'root'
        assert hit.payload['_canonical_verified'] is True
        assert not hit.payload.get('mail_fragment')
    if 'chunk' in expected:
        assert 'content' not in kept[-1].payload


def test_missing_concept_does_not_inherit_chunk_and_unknown_is_strictly_rejected():
    hits = _mail_filter_records(chunk_source='root', concept_source=None)
    hits.append(Hit('missing', 1, dict(point_type='concept', doc_id='scope-doc', slug='missing',
                                     content='FORGED_CONTENT', source_id='root', mail_scope='document',
                                     _canonical_verified=True)))
    kept, _ = load_visible_retrieval_hits(hits, mail_mode='exclude')
    assert kept == []


def test_mail_hydration_batch_query_count_does_not_grow_per_hit():
    from copy import deepcopy
    from sqlalchemy import event
    from app.db.session import get_engine
    hits = _mail_filter_records(chunk_source='root', concept_source='root')
    counts = []
    for count in (1, 100):
        statements = []
        def observe(*args):
            statements.append(args[2])
        event.listen(get_engine(), 'before_cursor_execute', observe)
        try:
            kept, _ = load_visible_retrieval_hits([deepcopy(hits[0]) for _ in range(count)], mail_mode='exclude')
            assert len(kept) == count
        finally:
            event.remove(get_engine(), 'before_cursor_execute', observe)
        counts.append(len(statements))
    assert counts[1] == counts[0]
    assert counts[0] <= 10


def test_strict_database_failure_never_falls_back_to_payload(monkeypatch):
    from app.services import retrieval_hydration
    def fail():
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(retrieval_hydration, 'session_scope', fail)
    with pytest.raises(RuntimeError, match='database unavailable'):
        load_visible_retrieval_hits([Hit('x', 1, dict(doc_id='doc', mail_scope='document'))], mail_mode='exclude')


def test_invalid_mail_mode_is_rejected_with_zero_hits():
    with pytest.raises(ValueError):
        load_visible_retrieval_hits([], mail_mode='INVALID')


def test_bool_chunk_index_cannot_borrow_loaded_concept_identity():
    hits = _mail_filter_records(chunk_source='root', concept_source='root')
    with session_scope() as session:
        session.query(DocumentChunk).filter_by(doc_id='scope-doc').update({'chunk_index': 0})
        session.query(OkfConcept).filter_by(doc_id='scope-doc').update({'chunk_index': 0})
    hits.append(Hit('invalid', .9, dict(point_type='chunk', doc_id='scope-doc', chunk_index=False)))
    kept, _ = load_visible_retrieval_hits(hits, mail_mode='exclude')
    assert 'invalid' not in [hit.point_id for hit in kept]
