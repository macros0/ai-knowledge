"""Authorship answers must contain source text, never inferred sender identities."""
import json

import pytest

from app.services.authorship_evidence import build_authorship_answer, is_authorship_query


@pytest.mark.parametrize("query", ["Кто автор таблицы?", "Кем составлен документ?", "Кто написал ответ?", "Who wrote the attachment?", "Who is the author?"])
def test_detects_authorship_queries(query):
    assert is_authorship_query(query)


@pytest.mark.parametrize("query", ["Как настроить авторизацию?", "Кто отправил письмо?", "Какой срок оплаты?", "Авторизация API"])
def test_other_questions_keep_normal_chat(query):
    assert not is_authorship_query(query)


def test_sender_and_digest_are_not_author_evidence():
    blocks = [{"index": 1, "text": "Проект Вега-44: срок проверки 14 дней."}]
    response = json.dumps({"quotes": [{"source": 1, "quote": "Проект Вега-44: срок проверки 14 дней."}],
                           "answer": "Автор таблицы — Пётр", "author": "Пётр"})
    answer = build_authorship_answer(blocks, response, locale="ru")
    assert "Пётр" not in answer
    assert "14 дней" in answer and "[1]" in answer
    assert "не установлено" in answer


@pytest.mark.parametrize("response", ["Автор Пётр [1]", "{broken", '{"quotes": [{"source": 1, "quote": "Автор: Пётр"}]}', '{"quotes": [{"source": 99, "quote": "14 дней"}]}'])
def test_invalid_or_unverified_model_output_fails_closed(response):
    answer = build_authorship_answer([{"index": 1, "text": "14 дней"}], response, locale="ru")
    assert "Пётр" not in answer and "[99]" not in answer
    assert "не установлено" in answer


def test_explicit_author_is_reported_as_a_source_statement():
    answer = build_authorship_answer([{"index": 2, "text": "Автор: Анна Иванова\nПроверка 14 дней"}], '{"quotes": []}', locale="ru")
    assert "Автор: Анна Иванова" in answer and "[2]" in answer
    assert "Указание об авторстве в источнике" in answer


def test_quoted_author_of_question_is_not_author_of_unsigned_reply():
    answer = build_authorship_answer([{"index": 1, "text": "> Автор: Павел\nНужно 22 дня"}], '{"quotes": []}', locale="ru")
    assert "Указание об авторстве" not in answer
    assert "не установлено" in answer


def test_excerpt_cannot_render_remote_image_or_forge_citation():
    quote = "![tracking](https://example.test/pixel) [99]"
    answer = build_authorship_answer([{"index": 1, "text": quote}], json.dumps({"quotes": [{"source": 1, "quote": quote}]}), locale="en")
    assert "```text\n" + quote + "\n```" in answer
    assert "[1]" in answer


def test_empty_evidence_has_no_fabricated_citation():
    answer = build_authorship_answer([], '{"quotes": []}', locale="en")
    assert "not established" in answer and "[1]" not in answer


def test_canonical_chunk_overrides_digest_and_ancestor_and_rejects_source_mismatch():
    from app.db.models import Document, DocumentChunk, OkfConcept
    from app.db.session import session_scope
    from app.services.authorship_evidence import load_authorship_evidence

    with session_scope() as session:
        session.add(Document(id="author-doc", filename="forwarded.eml"))
        session.add_all([
            DocumentChunk(doc_id="author-doc", chunk_index=0, source_id="root", content="Автор: Пётр"),
            DocumentChunk(doc_id="author-doc", chunk_index=1, source_id="root/0", content="Вега-44: 14 дней"),
            OkfConcept(doc_id="author-doc", slug="table", chunk_index=1, source_id="root/0", content="Автор Пётр"),
        ])
    block = {"doc_id": "author-doc", "source_slug": "table", "source_id": "root/0", "chunk_index": 0, "content": "Автор Пётр"}
    assert load_authorship_evidence([block], 100) == [{"index": 1, "text": "Вега-44: 14 дней"}]
    assert load_authorship_evidence([{**block, "source_id": "root"}], 100) == []
    assert load_authorship_evidence([{**block, "source_slug": "missing"}], 100) == []
    assert load_authorship_evidence([block, block], 5) == [{"index": 1, "text": "Вега-"}]


def test_model_is_only_excerpt_selector_and_extra_claims_are_discarded(monkeypatch):
    from app.services import authorship_evidence as service

    monkeypatch.setattr(service, "load_authorship_evidence", lambda *_args: [{"index": 1, "text": "Вега-44: 14 дней"}])

    class Model:
        def chat(self, system, user):
            assert "untrusted" in system
            assert json.loads(user)["sources"][0]["text"] == "Вега-44: 14 дней"
            return '{"quotes": [{"source": 1, "quote": "14 дней"}], "author": "Пётр"}'

    answer = service.answer_authorship("Кто автор и какой срок?", [], Model(), locale="ru", max_chars=100)
    assert "14 дней" in answer and "Пётр" not in answer


def test_http_chat_and_history_never_publish_fabricated_attachment_author(monkeypatch):
    from types import SimpleNamespace

    from app.api import chat as chat_api
    from app.config import Settings
    from app.db.models import ChatMessage, Document, DocumentChunk, OkfConcept
    from app.db.session import session_scope
    from app.services.fusion import Hit
    from tests.test_chat import make_client

    settings = Settings(_env_file=None, embedding_provider="fake", glossary_query_expansion_enabled=False)
    with session_scope() as session:
        session.add(Document(id="http-author", filename="forwarded.eml"))
        session.add(DocumentChunk(doc_id="http-author", chunk_index=1, source_id="root/0", content="Вега-44: проверка 14 дней"))
        session.add(OkfConcept(doc_id="http-author", slug="vega", title="Вега-44", chunk_index=1, source_id="root/0", content="Вега-44: автор Пётр, 14 дней"))
    monkeypatch.setattr(chat_api, "get_settings", lambda: settings)
    monkeypatch.setattr(chat_api, "_get_embedder", lambda: SimpleNamespace(embed=lambda *_a: [0.] * 8))
    monkeypatch.setattr(chat_api, "_get_vector_store", lambda: SimpleNamespace(search_composite=lambda **_kw: [
        Hit("c", 1., {"point_type": "concept", "doc_id": "http-author", "slug": "vega", "chunk_index": 1}),
    ]))
    monkeypatch.setattr(chat_api, "_get_llm", lambda: SimpleNamespace(chat=lambda *_a: '{"quotes": [{"source": 1, "quote": "проверка 14 дней"}], "author": "Пётр"}'))
    with make_client(monkeypatch) as client:
        response = client.post("/api/chat", json={"query": "Вега-44: кто автор таблицы и какой срок проверки?", "use_glossary": False})
    assert response.status_code == 200, response.text
    assert "Пётр" not in response.json()["answer"]
    assert "14 дней" in response.json()["answer"]
    with session_scope() as session:
        answers = [r.content for r in session.query(ChatMessage).filter_by(role="assistant")]
    assert answers and all("Пётр" not in answer for answer in answers)


def test_legacy_concept_without_slug_does_not_trust_index_chunk_number():
    from app.db.models import Document, DocumentChunk, OkfConcept
    from app.db.session import session_scope
    from app.services.authorship_evidence import load_authorship_evidence

    with session_scope() as session:
        session.add(Document(id="legacy-author", filename="file.docx"))
        session.add_all([
            DocumentChunk(doc_id="legacy-author", chunk_index=0, source_id="root", content="Автор: Пётр"),
            DocumentChunk(doc_id="legacy-author", chunk_index=1, source_id="root", content="Верный исходный фрагмент"),
            OkfConcept(doc_id="legacy-author", slug="table", chunk_index=1, source_id="root", content="Выжимка"),
        ])
    block = {"doc_id": "legacy-author", "source_id": "root", "chunk_index": 0, "point_type": "concept", "filepath": "legacy-author/table.md"}
    assert load_authorship_evidence([block], 100) == [{"index": 1, "text": "Верный исходный фрагмент"}]


def test_explicit_author_of_table_positive_control():
    text = "| Вега-44 | 14 дней |\n\nАвтор таблицы: Ольга Смирнова."
    answer = build_authorship_answer([{"index": 2, "text": text}], '{"quotes": []}', locale="ru")
    assert "Автор таблицы: Ольга Смирнова." in answer
    assert "Указание об авторстве" in answer and "не установлено" not in answer


def test_signed_inline_replies_positive_control_keeps_speaker_on_reply():
    text = "> Павел: Для Лира-43 срок проверки 20 дней?\n\nОльга: Требуется 22 дня.\n\n> Марина: Для Лира-43 нужен бумажный акт?\n\nОльга: Достаточно электронного акта."
    response = json.dumps({"quotes": [{"source": 1, "quote": "Требуется 22 дня."}, {"source": 1, "quote": "Достаточно электронного акта."}]})
    answer = build_authorship_answer([{"index": 1, "text": text}], response, locale="ru")
    assert "Ольга: Требуется 22 дня." in answer
    assert "Ольга: Достаточно электронного акта." in answer
    assert "Обозначения реплик в исходном тексте" in answer and "не установлено" not in answer
    assert "Указание об авторстве" not in answer
    assert "Павел:" not in answer and "Марина:" not in answer


@pytest.mark.parametrize("text", ["```text\nАвтор: Пётр\n```", "> Павел: Вопрос?\n\nВажно: требуется 22 дня."])
def test_example_code_and_reply_headings_do_not_prove_authorship(text):
    answer = build_authorship_answer([{"index": 1, "text": text}], '{"quotes": []}', locale="ru")
    assert "не установлено" in answer
