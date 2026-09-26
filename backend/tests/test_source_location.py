import hashlib

import pytest

from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services.source_evidence import (
    locate_unique_quote,
    resolve_mail_paragraph_span,
    resolve_mail_source_spans,
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


def test_quote_matches_pdf_whitespace_with_original_offsets():
    text = "😀 Prefix. The PW parameter defines\r\n taxable\u00a0wages. End."
    span = locate_unique_quote(text, "The PW parameter defines taxable wages.")
    assert span is not None
    assert text[span.start:span.end] == "The PW parameter defines\r\n taxable\u00a0wages."
    assert span.chunk_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("source,title,concept", [
    ("Получено сообщение от заказчика.", "Новое сообщение", "Сообщение требует подтверждения."),
    ("Срок оплаты счёта семь дней.", "Срок оплаты", "Предоставлен доступ к базе данных."),
    ("Продление лицензии не согласовано.", "Продление лицензии", "Продление лицензии согласовано."),
])
def test_mail_paragraph_guess_rejects_generic_title_only_and_opposite_claims(source, title, concept):
    assert resolve_mail_paragraph_span(source, title, concept) is None


@pytest.mark.parametrize("source,title,concept", [
    ("Получено сообщение от заказчика.", "Новое сообщение", "Сообщение требует подтверждения."),
    ("Срок оплаты счёта семь дней.", "Срок оплаты", "Предоставлен доступ к базе данных."),
    ("Продление лицензии не согласовано.", "Продление лицензии", "Продление лицензии согласовано."),
])
def test_docx_location_rejects_generic_title_only_and_opposite_claims(source, title, concept):
    _insert_source_fixture(
        "docx-false-paragraph", f"{source}\n\nУстановка клиентского приложения.", None,
        concept_text=concept, concept_title=title, filename="instruction.docx",
    )

    result = get_source_location("docx-false-paragraph", "source-concept")

    assert result.status == "chunk"
    assert result.spans == []


def test_mail_paragraph_guess_uses_content_terms_without_title():
    source = "Оплата счёта должна поступить в течение семи дней."
    span = resolve_mail_paragraph_span(source, "Другая тема", "Оплата счёта в течение семи дней.")
    assert span is not None
    assert source[span.start:span.end] == source


@pytest.mark.parametrize("text,quote", [
    ("taxable wages; taxable\nwages", "taxable wages"),
    ("a a a", "a a"),
    ("Taxable wages", "taxable wages"),
    ("taxable-wages", "taxable wages"),
    ("taxable wages.", "taxable wages!"),
    ("text", " \n "),
])
def test_quote_rejects_ambiguity_and_changes_other_than_whitespace(text, quote):
    assert locate_unique_quote(text, quote) is None


def _insert_source_fixture(
    doc_id: str,
    content: str,
    spans: list | None,
    concept_text: str = "Описание",
    source_id: str | None = None,
    concept_title: str = "Связанный концепт",
    filename: str | None = None,
):
    with session_scope() as session:
        session.add(Document(id=doc_id, filename=filename or f"{doc_id}.docx"))
        session.add(
            DocumentChunk(doc_id=doc_id, chunk_index=4, content=content, source_id=source_id)
        )
        session.add(
            OkfConcept(
                doc_id=doc_id,
                slug="source-concept",
                title=concept_title,
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


def test_source_location_returns_the_chunk_source_id_when_available():
    content = "Решение: лимит 12 дней."
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    start = content.index("лимит")
    _insert_source_fixture(
        "mail-source",
        content,
        [{"start": start, "end": start + len("лимит 12 дней"), "chunk_hash": digest}],
        source_id="root/0",
    )

    result = get_source_location("mail-source", "source-concept")

    assert result is not None
    assert result.source_id == "root/0"


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


def test_source_location_recovers_unique_matching_paragraph_without_a_quote():
    content = (
        "Первый абзац описывает установку клиентского приложения.\n\n"
        "Во втором абзаце используется сертификат Keycloak для входа."
    )
    _insert_source_fixture(
        "paragraph-recovery",
        content,
        None,
        concept_title="Настройка Keycloak",
        concept_text="Для входа используется сертификат Keycloak.",
    )

    result = get_source_location("paragraph-recovery", "source-concept")

    assert result is not None
    assert result.status == "inferred"
    assert [(span.start, span.quote) for span in result.spans] == [
        (content.index("Во втором"), "Во втором абзаце используется сертификат Keycloak для входа."),
    ]


def test_mail_location_labels_lexical_paragraph_as_inferred_not_exact():
    source = "Оплата счёта должна поступить в течение семи дней."
    _insert_source_fixture(
        "mail-inferred", source, None,
        concept_title="Оплата", concept_text="Оплата счёта в течение семи дней.",
        filename="mail.eml", source_id="root",
    )

    result = get_source_location("mail-inferred", "source-concept")

    assert result.status == "inferred"
    assert result.spans[0].quote == source


def test_legacy_full_mail_paragraph_span_is_not_presented_as_exact():
    source = "Оплата счёта должна поступить в течение семи дней."
    _insert_source_fixture(
        "mail-legacy-paragraph", source,
        [{"start": 0, "end": len(source), "chunk_hash": hashlib.sha256(source.encode()).hexdigest()}],
        concept_title="Оплата", concept_text="Оплата счёта в течение семи дней.",
        filename="mail.eml", source_id="root",
    )

    result = get_source_location("mail-legacy-paragraph", "source-concept")

    assert result.status == "inferred"


@pytest.mark.parametrize("stored", [False, True])
def test_mail_summary_does_not_anchor_to_intro_or_list_heading(stored):
    intro = "высылаем обновление системы СЭДО 09.2026."
    heading = "Что вошло в обновление системы СЭДО 09.2026:"
    source = f"Коллеги, {intro}\n\n{heading}\n\nЗапрос ускорен.\n\nСхемы обновлены."
    spans = [span_from_offsets(source, source.index(q), source.index(q) + len(q)).model_dump()
             for q in (intro, heading)] if stored else None
    _insert_source_fixture(
        "summary-intro", source, spans,
        concept_text="Обновление системы СЭДО 09.2026 включает:\n\n- Запрос работает быстрее.\n- Схемы актуализированы.",
        filename="update.msg", source_id="root",
    )
    result = get_source_location("summary-intro", "source-concept")
    assert result.status == "chunk"
    assert result.spans == []


def test_mail_summary_preserves_specific_evidence_and_recovers_individual_items():
    first = "Первый уникальный фрагмент описывает подготовку сертификата для подключения к сервису."
    second = "Второй уникальный фрагмент фиксирует обязательную проверку доступа перед запуском системы."
    source = f"Коллеги, высылаем обновление.\n\n{first}\n\n{second}"
    concept = f"Высылаем обновление.\n\n- {first}\n- {second}"
    spans = resolve_mail_source_spans(source, concept, ["высылаем обновление."])
    assert len(spans) == 2
    assert [source[s.start:s.end] for s in spans] == [first, second]
    quote = "подготовку сертификата для подключения к сервису"
    spans = resolve_mail_source_spans(source, concept, [quote])
    assert [source[s.start:s.end] for s in spans] == [quote]


def test_mail_summary_rejects_quote_with_opposite_claim():
    source = "Продление лицензии не согласовано."
    concept = "Обновление:\n\n- Продление лицензии согласовано.\n- Запрос работает быстрее."
    assert resolve_mail_source_spans(source, concept, [source]) == []


def test_mail_summary_does_not_treat_embedded_evidence_as_claims():
    source = "Посторонняя цитата подтверждает удаление архивных файлов базы данных."
    concept = ('Обновление системы СЭДО 09.2026:\n\n- Запрос работает быстрее.\n- Схемы актуализированы.\n\n'
               f'**Source Quotes:**\n- "{source}"\n- "Что вошло в обновление:"')
    assert resolve_mail_source_spans(source, concept) == []


def test_mail_summary_stale_spans_still_fall_back_to_chunk():
    quote = "Первый уникальный фрагмент описывает подготовку сертификата для подключения к сервису."
    source = f"{quote}\n\nДругая секция."
    _insert_source_fixture(
        "summary-stale", source, [{"start": 0, "end": len(quote), "chunk_hash": "0" * 64}],
        concept_text=f"Обновление:\n\n- {quote}\n- Обновлены схемы.", filename="update.msg", source_id="root",
    )
    assert get_source_location("summary-stale", "source-concept").status == "chunk"


@pytest.mark.parametrize("doc_id,source,title,concept", [
    ("mail-legacy-generic", "Получено сообщение от заказчика.", "Новое сообщение", "Сообщение требует подтверждения."),
    ("mail-legacy-title", "Срок оплаты счёта семь дней.", "Срок оплаты", "Предоставлен доступ к базе данных."),
    ("mail-legacy-negated", "Продление лицензии не согласовано.", "Продление лицензии", "Продление лицензии согласовано."),
])
def test_legacy_false_mail_paragraph_span_falls_back_to_chunk(doc_id, source, title, concept):
    _insert_source_fixture(
        doc_id, source,
        [{"start": 0, "end": len(source), "chunk_hash": hashlib.sha256(source.encode()).hexdigest()}],
        concept_title=title, concept_text=concept,
        filename="mail.eml", source_id="root",
    )

    result = get_source_location(doc_id, "source-concept")

    assert result.status == "chunk"
    assert result.spans == []


def test_source_location_recovers_multiple_unique_non_overlapping_excerpts():
    first = "Первый уникальный фрагмент описывает подготовку сертификата для подключения к сервису."
    second = "Второй уникальный фрагмент фиксирует обязательную проверку доступа перед запуском системы."
    content = f"{first} Концепт пропускает пояснение. {second}"
    source = f"Введение. {first} Промежуточный текст источника. {second} Заключение."
    _insert_source_fixture("multiple-excerpts", source, None, concept_text=content)

    result = get_source_location("multiple-excerpts", "source-concept")

    assert result is not None
    assert result.status == "recovered"
    assert [span.quote for span in result.spans] == [first.rstrip("."), second.rstrip(".")]


def test_source_location_does_not_guess_when_legacy_text_is_ambiguous():
    content = "Требование: повтор. Примечание: повтор."
    _insert_source_fixture("source6", content, None, concept_text="повтор")

    result = get_source_location("source6", "source-concept")

    assert result is not None
    assert result.status == "chunk"
    assert result.spans == []


def test_source_location_recovers_embedded_quotes_without_rewriting_concept():
    content = "Prefix. The PW parameter defines\n taxable wages. Tax stays unchanged."
    concept = ('Reformatted table.\n\n**Source Quotes:**\n'
               '- "The PW parameter defines taxable wages."\n'
               '- "Tax stays unchanged."\n'
               '- "Tax stays unchanged."')
    _insert_source_fixture("embedded", content, None, concept_text=concept)

    result = get_source_location("embedded", "source-concept")

    assert result.status == "recovered"
    assert [content[s.start:s.end] for s in result.spans] == [
        "The PW parameter defines\n taxable wages.", "Tax stays unchanged.",
    ]
    with session_scope() as session:
        saved = session.query(OkfConcept).filter_by(doc_id="embedded").one()
        assert saved.content == concept
        assert saved.source_spans is None


@pytest.mark.parametrize("concept", [
    'Description.\n- "The PW parameter defines taxable wages."',
    'Description.\n**Source Quotes:**\n- "Invented source evidence."',
    'Description.\n**Source Quotes:**\n- "repeated quote"',
    'Description.\n**Source Quotes:**\nNo quotes.\n\n## Example\n- "The PW parameter defines taxable wages."',
])
def test_embedded_quotes_do_not_guess_or_scan_other_sections(concept):
    content = "The PW parameter defines taxable wages. repeated quote; repeated\nquote"
    _insert_source_fixture("no-guess", content, None, concept_text=concept)
    assert get_source_location("no-guess", "source-concept").status == "chunk"


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


@pytest.mark.parametrize("other_mapping", [
    "", "the value directly from the Infotype table P0002-OTHER. ",
    "If the Take from field is mapped to table T5UX9, another location is used. ",
])
def test_source_location_recovers_unique_excerpt_from_paraphrase(other_mapping):
    content = ("😀 In case of Infotype, the mapping is as shown below, system will take "
               "the value directly from the Infotype table P0002-PERIOD and\n"
               "the Or directly is the dollar routine to be considered:")
    content = other_mapping + content
    concept = ("If the Take from field is mapped to Infotype, the system takes "
               "the value directly from the Infotype table P0002-PERIOD.")
    _insert_source_fixture("paraphrase", content, None, concept_text=concept)

    result = get_source_location("paraphrase", "source-concept")

    assert result.status == "recovered"
    assert len(result.spans) == 1
    span = result.spans[0]
    assert content[span.start:span.end] == "the value directly from the Infotype table P0002-PERIOD"


@pytest.mark.parametrize("source,concept", [
    ("The value comes from P0002-PERIOD.", "Mapping uses P0002-PERIOD."),
    ("The system takes the value directly from the Infotype table P0002-PERIOD. "
     "It also takes the value directly from the Infotype table P0002-PERIOD.",
     "Mapping obtains the value directly from the Infotype table P0002-PERIOD."),
    ("the value directly from the Infotype table P0002-PERIOD",
     "Unrelated material " * 40 + "the value directly from the Infotype table P0002-PERIOD."),
    ("the value directly from the Infotype table P0002-PERIOD",
     "Mapping obtains the value directly from the Infotype table P0002-OTHER."),
])
def test_paraphrase_recovery_rejects_weak_or_ambiguous_evidence(source, concept):
    _insert_source_fixture("weak-paraphrase", source, None, concept_text=concept)
    assert get_source_location("weak-paraphrase", "source-concept").status == "chunk"


def test_paraphrase_recovery_keeps_distinct_equal_length_fragments():
    source = ("the value directly from the Infotype table P0002-PERIOD. Other text. "
              "the value directly from the Infotype table P0002-OTHER.")
    concept = ("Mapping reads the value directly from the Infotype table P0002-PERIOD "
               "and the value directly from the Infotype table P0002-OTHER.")
    _insert_source_fixture("equal-fragments", source, None, concept_text=concept)

    result = get_source_location("equal-fragments", "source-concept")

    assert result.status == "recovered"
    assert [span.quote for span in result.spans] == [
        "the value directly from the Infotype table P0002-PERIOD",
        "the value directly from the Infotype table P0002-OTHER",
    ]


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
