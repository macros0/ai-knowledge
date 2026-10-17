"""Materialized parts retain independent evidence through retrieval and replay."""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from app.api.errors import ApiError
from app.db.models import (
    Document,
    DocumentChunk,
    DocumentChunkFragment,
    DocumentGeneration,
    DocumentSource,
    OkfConcept,
)
from app.db.session import session_scope
from app.services.vector_store import chunk_point_id
from app.services.chunk_projection_store import materialize_chunk_projection
from app.services.chat_source_selection import snapshot_blocks, restore_blocks
from app.services.context_builder import merge_and_format
from app.services.fusion import Hit
from app.services.retrieval_hydration import load_visible_retrieval_hits
from tests.test_chunk_projection_store import DOC, TITLE, candidate, activate


BODY = "same physical row 0 False\n" * 210


def published(body=BODY, *, title=TITLE, source_id=None):
    gen = candidate()
    materialize_chunk_projection(DOC, gen, 0, title, body, source_id=source_id)
    activate(gen, body)
    with session_scope() as s:
        chunk = s.scalar(
            select(DocumentChunk).where(DocumentChunk.doc_id == DOC, DocumentChunk.chunk_index == 0)
        )
        chunk.section_title = title
        chunk.source_id = source_id
    with session_scope() as s:
        parts = list(
            s.scalars(
                select(DocumentChunkFragment)
                .where(
                    DocumentChunkFragment.doc_id == DOC, DocumentChunkFragment.generation_id == gen
                )
                .order_by(DocumentChunkFragment.ordinal)
            )
        )
        return (
            gen,
            [
                Hit(
                    p.point_id,
                    10.0 - p.ordinal,
                    {
                        "doc_id": DOC,
                        "generation_id": gen,
                        "point_type": "chunk",
                        "chunk_index": 0,
                        "source_id": source_id,
                        "chunk_fragment": deepcopy(p.descriptor),
                        "content": "untrusted payload",
                        "section_title": "untrusted title",
                    },
                )
                for p in parts
            ],
            [p.content for p in parts],
        )


def test_multiple_parts_keep_own_text_score_and_snapshot_offsets():
    gen, hits, texts = published()
    selected = [hits[0], hits[1], deepcopy(hits[0])]
    visible, _ = load_visible_retrieval_hits(selected)
    assert [h.payload["content"] for h in visible] == [texts[0], texts[1], texts[0]]
    blocks = merge_and_format(visible, limit_total_chars=False)
    assert len(blocks) == 2
    assert [b["score"] for b in blocks] == [hits[0].score, hits[1].score]
    assert [b["content"] for b in blocks] == texts[:2]
    assert all(b["chunk_index"] == 0 and b["generation_id"] == gen for b in blocks)
    assert all(
        b["_evidence"]["chunk_fragment"] == hits[i].payload["chunk_fragment"]
        for i, b in enumerate(blocks)
    )
    saved = snapshot_blocks(blocks)
    assert all("content" not in b for b in saved)
    restored = restore_blocks(saved, mail_mode="all")
    assert [b["content"] for b in restored] == texts[:2]
    assert all(b["filepath"].endswith("/chunks/chunk_00.md") for b in restored)


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "bool",
        "range",
        "id",
        "source",
        "generation",
        "sql-text",
        "canonical-hash",
        "canonical-title",
        "trash",
        "version",
    ],
)
def test_invalid_part_never_falls_back_to_raw_chunk(damage):
    gen, hits, _ = published()
    hit = hits[0]
    if damage == "missing":
        hit.payload.pop("chunk_fragment")
    elif damage == "bool":
        hit.payload["chunk_fragment"]["version"] = True
    elif damage == "range":
        hit.payload["chunk_fragment"]["end"] += 1
    elif damage == "id":
        hit.point_id = str(uuid4())
    elif damage == "source":
        hit.payload["source_id"] = "forged"
    elif damage == "generation":
        hit.payload["generation_id"] = "stale"
    else:
        with session_scope() as s:
            if damage == "sql-text":
                part = s.scalar(
                    select(DocumentChunkFragment).where(
                        DocumentChunkFragment.point_id == hit.point_id
                    )
                )
                part.content += "corrupt"
            elif damage == "canonical-hash":
                s.scalar(
                    select(DocumentChunk).where(
                        DocumentChunk.doc_id == DOC, DocumentChunk.chunk_index == 0
                    )
                ).content_hash = "0" * 64
            elif damage == "canonical-title":
                s.scalar(
                    select(DocumentChunk).where(
                        DocumentChunk.doc_id == DOC, DocumentChunk.chunk_index == 0
                    )
                ).section_title += "changed"
            elif damage == "trash":
                s.get(Document, DOC).deleted_at = datetime.now(timezone.utc)
            else:
                s.get(DocumentGeneration, gen).chunk_projection_version = 999
    visible, _ = load_visible_retrieval_hits([hit])
    assert visible == []


def test_changed_descriptor_snapshot_is_409():
    _, hits, _ = published()
    visible, _ = load_visible_retrieval_hits(hits[:2])
    saved = snapshot_blocks(merge_and_format(visible, limit_total_chars=False))
    saved[0]["_evidence"]["chunk_fragment"]["text_hash"] = "0" * 64
    with pytest.raises(ApiError) as caught:
        restore_blocks(saved, mail_mode="all")
    assert caught.value.status_code == 409


def test_stale_generation_snapshot_is_409():
    _, hits, _ = published()
    visible, _ = load_visible_retrieval_hits(hits[:2])
    saved = snapshot_blocks(merge_and_format(visible, limit_total_chars=False))
    published(BODY + "changed")
    with pytest.raises(ApiError) as caught:
        restore_blocks(saved, mail_mode="all")
    assert caught.value.status_code == 409


def test_short_marker_one_retains_only_canonical_legacy_root():
    gen, _, _ = published("short body")
    root = Hit(
        chunk_point_id(DOC, 0, generation_id=gen),
        1.0,
        {
            "doc_id": DOC,
            "generation_id": gen,
            "point_type": "chunk",
            "chunk_index": 0,
            "source_id": None,
        },
    )
    visible, _ = load_visible_retrieval_hits([root, Hit(str(uuid4()), 1.0, deepcopy(root.payload))])
    assert len(visible) == 1 and visible[0].point_id == root.point_id
    assert visible[0].payload["content"] == "short body"
    saved = snapshot_blocks(merge_and_format(visible))
    assert restore_blocks(saved, mail_mode="all")[0]["content"] == "short body"


def test_retrieval_selector_includes_fragment_identity():
    from app.services.vector_store import RETRIEVAL_PAYLOAD_FIELDS

    assert {"chunk_fragment", "source_id"} <= set(RETRIEVAL_PAYLOAD_FIELDS)


def test_parts_do_not_disappear_with_multiple_concepts_or_review_siblings():
    gen, hits, texts = published()
    with session_scope() as s:
        for slug in ("one", "two", "review"):
            s.add(
                OkfConcept(
                    doc_id=DOC, slug=slug, title=slug, content="digest " + slug, chunk_index=0
                )
            )
    concepts = [
        Hit(
            slug,
            5.0,
            {
                "doc_id": DOC,
                "point_type": "concept",
                "generation_id": gen,
                "chunk_index": 0,
                "slug": slug,
                "title": slug,
                "tags": ["review"] if slug == "review" else [],
            },
        )
        for slug in ("one", "two", "review")
    ]
    visible, _ = load_visible_retrieval_hits(hits[:2] + concepts)
    blocks = merge_and_format(visible, limit_total_chars=False)
    assert len(blocks) == 5
    assert [b["content"] for b in blocks if b["point_type"] == "chunk"] == texts[:2]
    assert {b["kind"] for b in blocks} == {"chunk", "concept", "review"}
    assert [b["content"] for b in restore_blocks(snapshot_blocks(blocks), mail_mode="all")] == [
        b["content"] for b in blocks
    ]


def mail_source():
    with session_scope() as s:
        s.add(Document(id=DOC, filename="archive.docx", status="done"))
        s.flush()
        s.add_all(
            [
                DocumentSource(
                    doc_id=DOC, source_id="root", kind="document", display_name="archive"
                ),
                DocumentSource(
                    doc_id=DOC,
                    source_id="mail",
                    parent_source_id="root",
                    kind="mail",
                    display_name="letter",
                ),
            ]
        )


def test_strict_mail_parts_restore_and_broken_source_tree_is_rejected():
    mail_source()
    gen, hits, texts = published(source_id="mail")
    visible, _ = load_visible_retrieval_hits(deepcopy(hits[:2]), mail_mode="only")
    blocks = merge_and_format(visible, mail_mode="only", limit_total_chars=False)
    assert len(blocks) == 2
    assert [
        b["content"] for b in restore_blocks(snapshot_blocks(blocks), mail_mode="only")
    ] == texts[:2]
    assert all(b["source_path"][-1]["source_id"] == "mail" for b in blocks)
    assert load_visible_retrieval_hits(deepcopy(hits[:2]), mail_mode="exclude")[0] == []
    with session_scope() as s:
        s.delete(
            s.scalar(
                select(DocumentSource).where(
                    DocumentSource.doc_id == DOC, DocumentSource.source_id == "mail"
                )
            )
        )
    assert load_visible_retrieval_hits(hits[:2])[0] == []


def test_concept_mail_snapshot_restores_raw_mail_without_legacy_index_root():
    mail_source()
    gen, _, _ = published(source_id="mail")
    with session_scope() as s:
        s.add(
            OkfConcept(
                doc_id=DOC,
                slug="mail-summary",
                title="Summary",
                content="mail digest",
                chunk_index=0,
                source_id="mail",
            )
        )
    hit = Hit(
        "concept",
        1.0,
        {
            "doc_id": DOC,
            "generation_id": gen,
            "point_type": "concept",
            "slug": "mail-summary",
            "title": "Summary",
            "chunk_index": 0,
        },
    )
    visible, _ = load_visible_retrieval_hits([hit], mail_mode="only")
    blocks = merge_and_format(visible, mail_mode="only")
    assert len(blocks) == 1 and blocks[0]["mail_fragment"]["content"]
    restored = restore_blocks(snapshot_blocks(blocks), mail_mode="only")
    assert restored[0]["content"] == "mail digest"
    assert restored[0]["mail_fragment"] == blocks[0]["mail_fragment"]


def test_title_parts_restore_with_bounded_display_title():
    _, hits, texts = published("body", title="Very long canonical title " * 400)
    visible, _ = load_visible_retrieval_hits(hits[:2])
    assert all(len(h.payload["section_title"]) <= 128 for h in visible)
    blocks = merge_and_format(visible, limit_total_chars=False)
    assert [
        b["content"] for b in restore_blocks(snapshot_blocks(blocks), mail_mode="all")
    ] == texts[:2]


def test_sql_metadata_reused_and_parts_read_in_bounded_batches():
    from sqlalchemy import event
    from app.db.session import get_engine

    _, hits, _ = published("physical row 0 False\n" * 30000)
    assert len(hits) > 256
    calls = []

    def observed(conn, cursor, statement, parameters, context, executemany):
        calls.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", observed)
    try:
        visible, _ = load_visible_retrieval_hits(hits[:300])
    finally:
        event.remove(engine, "before_cursor_execute", observed)
    assert len(visible) == 300
    assert sum("FROM document_sources" in sql for sql in calls) == 1
    assert sum("FROM document_chunk_projections" in sql for sql in calls) == 1
    assert sum("FROM document_chunk_fragments" in sql for sql in calls) == 2
    assert not any("substr(document_chunks.content" in sql for sql in calls)


@pytest.mark.parametrize(
    "damage",
    [
        "offset-type",
        "negative-offset",
        "length-type",
        "missing-digest",
        "missing-doc",
        "component-type",
    ],
)
def test_malformed_snapshot_rejected_as_changed_sources(damage):
    _, hits, _ = published()
    visible, _ = load_visible_retrieval_hits(hits[:2])
    saved = snapshot_blocks(merge_and_format(visible, limit_total_chars=False))
    ref = saved[0]["_evidence"]
    if damage == "offset-type":
        ref["start"] = "0"
    elif damage == "negative-offset":
        ref["start"] = -1
    elif damage == "length-type":
        ref["length"] = True
    elif damage == "missing-digest":
        ref.pop("digest")
    elif damage == "missing-doc":
        ref.pop("doc_id")
    else:
        saved[0]["_mail_components"] = ["corrupt"]
    with pytest.raises(ApiError) as caught:
        restore_blocks(saved, mail_mode="all")
    assert caught.value.status_code == 409


def test_chat_api_can_select_one_part_without_search_and_rejects_stale_selection(monkeypatch):
    from types import SimpleNamespace
    from app.api import chat as api
    from tests.test_chat import make_client

    _, hits, texts = published()
    body_indices = [
        i for i, h in enumerate(hits) if h.payload["chunk_fragment"]["field"] == "body"
    ][:2]
    assert len(body_indices) == 2
    body_hits = [hits[i] for i in body_indices]
    monkeypatch.setattr(api, "_get_embedder", lambda: SimpleNamespace(embed=lambda _: [0.0]))
    monkeypatch.setattr(
        api,
        "_get_vector_store",
        lambda: SimpleNamespace(search_composite=lambda **_: deepcopy(body_hits)),
    )
    prompts = []
    monkeypatch.setattr(
        api,
        "_get_llm",
        lambda: SimpleNamespace(
            model="test", chat=lambda system, user: prompts.append(user) or "Ответ [1]"
        ),
    )
    monkeypatch.setattr(api.ChatTokenBudget, "fits", lambda *a, **k: True)
    client = make_client(monkeypatch)
    response = client.post(
        "/api/chat",
        json={
            "query": "physical",
            "response_mode": "documents",
            "attempt_id": str(uuid4()),
            "session_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    search = response.json()
    assert len(search["sources"]) == 2
    assert all(source["chunk_index"] == 0 and source["selectable"] for source in search["sources"])

    def forbidden():
        raise AssertionError("Selection must reuse saved evidence")

    monkeypatch.setattr(api, "_get_embedder", forbidden)
    monkeypatch.setattr(api, "_get_vector_store", forbidden)
    request = {
        "query": "physical",
        "response_mode": "full",
        "session_id": search["session_id"],
        "source_selection": {"attempt_id": search["attempt_id"], "indexes": [2]},
    }
    selected = client.post("/api/chat", json=request)
    assert selected.status_code == 200, selected.text
    assert len(selected.json()["sources"]) == 1
    assert texts[body_indices[1]] in prompts[-1]
    published(BODY + "changed")
    stale = client.post("/api/chat", json=request)
    assert stale.status_code == 409, stale.text
    assert len(prompts) == 1


def test_title_only_part_has_no_unearned_lexical_filter_immunity():
    from app.services.context_builder import drop_unmatched_blocks

    _, hits, _ = published()
    visible, _ = load_visible_retrieval_hits(hits[:2])
    assert visible[0].payload["chunk_fragment"]["field"] == "title"
    assert visible[1].payload["chunk_fragment"]["field"] == "body"
    blocks = merge_and_format(visible, limit_total_chars=False)
    kept = drop_unmatched_blocks(blocks, "physical")
    assert len(kept) == 1
    assert kept[0]["_evidence"]["chunk_fragment"]["field"] == "body"


def test_each_mail_fragment_component_keeps_its_own_concept_identity():
    mail_source()
    gen, _, _ = published(source_id='mail')
    with session_scope() as s:
        for slug in ['first','second']:
            s.add(OkfConcept(doc_id=DOC,slug=slug,title=slug,type='concept',content=slug,
                             chunk_index=0,source_id='mail'))
    hits=[Hit(slug,1.,{'point_type':'concept','doc_id':DOC,'slug':slug,
                      'generation_id':gen,'source_id':'mail','chunk_index':0}) for slug in ['first','second']]
    visible,_=load_visible_retrieval_hits(hits,mail_mode='only')
    assert len(visible)==2
    assert [h.payload['_mail_fragment_component']['slug'] for h in visible]==['first','second']
    assert all(h.payload['_mail_fragment_component']['point_type']=='concept' for h in visible)
