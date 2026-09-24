import hashlib

from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services.source_evidence import (
    locate_unique_quote,
    span_for_lines,
    span_from_offsets,
)
from app.services.source_location import get_document_text_chunks, get_source_location


def test_source_spans_use_python_character_offsets_and_chunk_hash():
    text = "😀 начало\r\nСвязанный фрагмент\r\nконец"

    span = span_for_lines(text, 1, 2)

    assert span is not None
    assert text[span.start : span.end] == "Связанный фрагмент\r\n"
    assert span.chunk_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_source_span_rejects_invalid_ranges():
    assert span_from_offsets("текст", -1, 2) is None
    assert span_from_offsets("текст", 2, 2) is None
    assert span_from_offsets("текст", 0, 99) is None
    assert span_for_lines("one\ntwo", 1, 3) is None


def test_unique_quote_only_returns_unambiguous_evidence():
    text = "До. Единственная цитата. После."

    span = locate_unique_quote(text, "Единственная цитата")

    assert span is not None
    assert text[span.start : span.end] == "Единственная цитата"
    assert locate_unique_quote("повтор; повтор", "повтор") is None
    assert locate_unique_quote(text, "нет в тексте") is None
    assert locate_unique_quote(text, "") is None


def _insert_source_fixture(
    doc_id: str,
    content: str,
    spans: list | None,
    concept_text: str = "Описание",
):
    with session_scope() as session:
        session.add(Document(id=doc_id, filename=f"{doc_id}.docx"))
        session.add(
            DocumentChunk(doc_id=doc_id, chunk_index=4, content=content)
        )
        session.add(
            OkfConcept(
                doc_id=doc_id,
                slug="source-concept",
                title="Связанный концепт",
                content=concept_text,
                chunk_index=4,
                source_spans=spans,
            )
        )


def test_source_location_returns_verified_excerpt_and_sorted_unique_spans():
    content = "Префикс. Цитата один. Цитата два."
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    first_start = content.index("Цитата один")
    second_start = content.index("Цитата два")
    first_quote = "Цитата один"
    second_quote = "Цитата два"
    _insert_source_fixture(
        "source1",
        content,
        [
            {"start": second_start, "end": second_start + len(second_quote), "chunk_hash": digest},
            {"start": first_start, "end": first_start + len(first_quote), "chunk_hash": digest},
            {"start": first_start, "end": first_start + len(first_quote), "chunk_hash": digest},
        ],
    )

    result = get_source_location("source1", "source-concept")

    assert result is not None
    assert result.status == "exact"
    assert result.chunk_index == 4
    assert [(span.start, span.quote) for span in result.spans] == [
        (first_start, first_quote),
        (second_start, second_quote),
    ]


def test_source_location_marks_unique_legacy_text_as_recovered():
    quote = (
        "Необходимо реализовать форму PDF и XML-файл «ЕФС-1» в системе SAP HCM "
        "в соответствии с постановлением №245п от 30.10.2022."
    )
    content = f"Требования документа.\n\n{quote}\n\nСледующий раздел."
    _insert_source_fixture("source5", content, None, concept_text=quote)

    result = get_source_location("source5", "source-concept")

    assert result is not None
    assert result.status == "recovered"
    assert len(result.spans) == 1
    span = result.spans[0]
    assert content[span.start : span.end] == quote
    assert span.quote == quote


def test_source_location_does_not_guess_when_legacy_text_is_ambiguous():
    content = "Требование: повтор. Примечание: повтор."
    _insert_source_fixture("source6", content, None, concept_text="повтор")

    result = get_source_location("source6", "source-concept")

    assert result is not None
    assert result.status == "chunk"
    assert result.spans == []


def test_source_location_falls_back_to_chunk_for_stale_or_malformed_ranges():
    content = "Обновлённый текст источника"
    _insert_source_fixture(
        "source2",
        content,
        [
            {"start": 0, "end": 8, "chunk_hash": "0" * 64},
            {"start": True, "end": 8, "chunk_hash": "0" * 64},
            "not a span",
        ],
    )

    result = get_source_location("source2", "source-concept")

    assert result is not None
    assert result.status == "chunk"
    assert result.chunk_index == 4
    assert result.spans == []


def test_stale_span_does_not_recover_from_concept_text():
    content = "Обновлённое требование к документу полностью совпадает с текстом концепта."
    _insert_source_fixture(
        "source7", content,
        [{"start": 0, "end": 24, "chunk_hash": "0" * 64}],
        concept_text=content,
    )

    result = get_source_location("source7", "source-concept")

    assert result is not None
    assert result.status == "chunk"
    assert result.spans == []


def test_empty_chunk_is_unavailable():
    _insert_source_fixture("source8", "", None)

    result = get_source_location("source8", "source-concept")

    assert result is not None
    assert result.status == "unavailable"


def test_source_location_handles_missing_concept_chunk_and_unlinked_concept():
    with session_scope() as session:
        session.add(Document(id="source3", filename="source3.docx"))
        session.add(
            OkfConcept(
                doc_id="source3",
                slug="no-chunk",
                title="Concept without chunk",
                content="",
                chunk_index=9,
            )
        )
        session.add(
            OkfConcept(
                doc_id="source3",
                slug="unlinked",
                title="Unlinked concept",
                content="",
            )
        )

    assert get_source_location("source3", "missing") is None
    assert get_source_location("source3", "no-chunk").status == "unavailable"
    assert get_source_location("source3", "unlinked").status == "unavailable"


def test_document_text_chunks_are_returned_in_source_order():
    with session_scope() as session:
        session.add(Document(id="source4", filename="source4.docx"))
        session.add_all(
            [
                DocumentChunk(doc_id="source4", chunk_index=2, content="Второй"),
                DocumentChunk(doc_id="source4", chunk_index=0, content="Первый"),
            ]
        )

    chunks = get_document_text_chunks("source4")

    assert [(chunk.chunk_index, chunk.content) for chunk in chunks] == [
        (0, "Первый"),
        (2, "Второй"),
    ]
