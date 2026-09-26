"""Хранение структурного происхождения документа."""
from __future__ import annotations

import pytest

from app.db.models import Document, DocumentChunk, DocumentSource, OkfAttachment, OkfConcept
from app.db.session import session_scope
from app.models.schemas import OkfDocument
from app.services.attachment_store import replace_attachments
from app.services.chunk_store import replace_chunks
from app.services.concept_store import replace_concepts
from app.services.registry import DocumentRegistry
from app.services.source_store import replace_sources

DOC_ID = "maildoc000000001"


def _document() -> None:
    with session_scope() as session:
        session.add(
            Document(
                id=DOC_ID,
                filename="forward.eml",
                content_type="message/rfc822",
                size=12,
                status="done",
            )
        )


def _sources() -> list[dict]:
    return [
        {
            "source_id": "root",
            "parent_source_id": None,
            "ordinal": 0,
            "kind": "mail",
            "display_name": "forward.eml",
            "metadata": {"subject": "Пересылка"},
            "extraction_status": "parsed",
            "artifact_kind": "original",
        },
        {
            "source_id": "root/0",
            "parent_source_id": "root",
            "ordinal": 0,
            "kind": "mail",
            "display_name": "decision.eml",
            "metadata": {"subject": "Решение"},
            "saved_path": "attachments/root-0.eml",
            "extraction_status": "parsed",
            "artifact_kind": "original",
        },
    ]


def test_source_tree_and_chunk_attachment_references_are_persisted(tmp_path):
    _document()
    stored = tmp_path / "attachments" / "root-0.eml"
    stored.parent.mkdir()
    stored.write_bytes(b"From: sender@example.test\n\nDecision")

    with session_scope() as session:
        replace_sources(session, DOC_ID, _sources())
        replace_chunks(
            session,
            DOC_ID,
            [
                {
                    "chunk_index": 0,
                    "section_title": "Решение",
                    "content": "Лимит 12 дней.",
                    "char_count": 15,
                    "source_id": "root/0",
                }
            ],
        )
        replace_attachments(
            session,
            DOC_ID,
            [
                {
                    "name": "decision.eml",
                    "saved_path": "attachments/root-0.eml",
                    "source_id": "root/0",
                    "is_processable": True,
                    "extraction_status": "parsed",
                }
            ],
            storage_root=tmp_path,
        )
        replace_concepts(
            session,
            DOC_ID,
            [
                OkfDocument(
                    filepath=f"{DOC_ID}/decision.md",
                    metadata={"title": "Решение", "source_id": "root/0"},
                    content="Лимит 12 дней.",
                    markdown="",
                )
            ],
        )

    with session_scope() as session:
        sources = session.query(DocumentSource).filter_by(doc_id=DOC_ID).order_by(DocumentSource.source_id).all()
        assert [(row.source_id, row.parent_source_id, row.metadata_json) for row in sources] == [
            ("root", None, {"subject": "Пересылка"}),
            ("root/0", "root", {"subject": "Решение"}),
        ]
        assert session.query(DocumentChunk).one().source_id == "root/0"
        assert session.query(OkfAttachment).one().source_id == "root/0"
        assert session.query(OkfConcept).one().source_id == "root/0"


def test_chunk_cannot_reference_unknown_source():
    _document()
    with session_scope() as session:
        replace_sources(session, DOC_ID, _sources())
        with pytest.raises(ValueError, match="source_id"):
            replace_chunks(
                session,
                DOC_ID,
                [{"chunk_index": 0, "content": "text", "source_id": "root/missing"}],
            )


@pytest.mark.parametrize("invalid", [
    [],
    [{"source_id": "root"}, {"source_id": "root"}],
    [{"source_id": "root", "parent_source_id": "root"}],
    [{"source_id": "root"}, {"source_id": "root/0", "parent_source_id": "missing"}],
    [{"source_id": "root"}, {"source_id": "root/0", "parent_source_id": "root/1"},
     {"source_id": "root/1", "parent_source_id": "root/0"}],
    # The child comes before its malformed ancestor: report a validation error,
    # not KeyError while following an unvalidated parent reference.
    [{"source_id": "root"}, {"source_id": "root/0/0", "parent_source_id": "root/0"},
     {"source_id": "root/0", "parent_source_id": "missing"}],
])
def test_invalid_source_tree_is_rejected_before_replacing_published_rows(invalid):
    _document()
    with session_scope() as session:
        replace_sources(session, DOC_ID, _sources())
    with session_scope() as session:
        with pytest.raises(ValueError):
            replace_sources(session, DOC_ID, invalid)
        assert [row.source_id for row in session.query(DocumentSource).order_by(DocumentSource.source_id)] == [
            "root", "root/0",
        ]


@pytest.mark.parametrize("store", ["chunks", "concepts", "attachments"])
def test_source_reference_cannot_borrow_another_documents_node(tmp_path, store):
    _document()
    with session_scope() as session:
        session.add(Document(id="anotherdoc", filename="other.eml"))
        session.flush()
        replace_sources(session, DOC_ID, [_sources()[0]])
        replace_sources(session, "anotherdoc", _sources())
    with pytest.raises(ValueError, match="source_id"), session_scope() as session:
        if store == "chunks":
            replace_chunks(session, DOC_ID, [{"chunk_index": 0, "content": "text", "source_id": "root/0"}])
        elif store == "concepts":
            replace_concepts(session, DOC_ID, [OkfDocument(
                filepath="test.md", metadata={"source_id": "root/0"}, content="text", markdown="",
            )])
        else:
            replace_attachments(session, DOC_ID, [{"name": "test.bin", "saved_path": "test.bin",
                                                  "source_id": "root/0"}], storage_root=tmp_path)


def test_source_tree_write_rolls_back_as_a_whole():
    _document()
    with session_scope() as session:
        replace_sources(session, DOC_ID, _sources())
    with pytest.raises(RuntimeError, match="simulated"), session_scope() as session:
        replace_sources(session, DOC_ID, [{"source_id": "root", "display_name": "replacement"}])
        raise RuntimeError("simulated failure after inserting sources")
    with session_scope() as session:
        assert session.get(DocumentSource, (DOC_ID, "root/0")).display_name == "decision.eml"
        assert session.get(DocumentSource, (DOC_ID, "root")).display_name == "forward.eml"


def test_purging_a_trashed_document_cascades_to_its_source_tree():
    _document()
    with session_scope() as session:
        replace_sources(session, DOC_ID, _sources())

    registry = DocumentRegistry()
    assert registry.soft_delete(DOC_ID, "tester") is True
    assert registry.delete_if_deleted(DOC_ID) is True

    with session_scope() as session:
        assert session.query(DocumentSource).filter_by(doc_id=DOC_ID).count() == 0


def test_source_rows_use_the_parser_version_from_the_completed_parse(tmp_path):
    from docparser.source_model import ParseContext

    from app.services.pipeline import _source_rows

    context = ParseContext("letter.eml")
    child = context.add_child("root", "nested.eml", "mail")
    context.warn(child, "mail_parse_failed")

    rows = _source_rows(context.sources, [], tmp_path, parser_version="worker-contract-v2")

    assert {row["parser_version"] for row in rows} == {"worker-contract-v2"}
    assert next(row for row in rows if row["source_id"] == child)["warnings"] == [
        {"code": "mail_parse_failed", "source_id": child}
    ]


@pytest.mark.parametrize("code,status", [
    ("protected_mail", "protected"), ("unsupported_mail_class", "unsupported"),
    ("encrypted_mail", "encrypted"),
    ("unsupported_rtf_body", "unsupported"),
])
def test_source_warning_overrides_false_parsed_status(tmp_path, code, status):
    from docparser.blocks import Block
    from docparser.source_model import ParseContext

    from app.services.pipeline import _source_rows

    context = ParseContext("protected.msg")
    child = context.add_child("root", "child.msg", "mail")
    context.warn("root", code)
    context.warn(child, code)
    blocks = [Block("attachment", "child", meta={"source_id": child, "extraction_status": "parsed"})]
    assert {row["extraction_status"] for row in _source_rows(context.sources, blocks, tmp_path)} == {status}
