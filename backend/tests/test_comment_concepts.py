# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Тесты программного извлечения концептов из комментариев рецензентов."""
from app.config import get_settings
from app.services.comment_concepts import extract_comment_concepts
from app.services.field_table import extract_field_table_concepts
from app.services.okf_generator import OKFGenerator


THREAD_MD = """Алгоритм выбора табельного.

> **Контекст:** Если найдено несколько табельных, сортируем и выбираем последний номер.
> **Комментарий рецензента (Волкова Анастасия Владимировна, 2026-06-23):** Имеется ввиду самый свежий ТН или последний в списке?
> **Ответ (Сагитов Алексей Минерович [2]):** Что бы не усложнять алгоритм считаем наибольший табельный является самым свежим.
> **Статус:** замечание закрыто

Далее по тексту.
"""

OLD_FORMAT_MD = """Абзац до.

> **Комментарий рецензента (Рецензент):** Поправьте формулировку

Абзац после.
"""


class TestExtractCommentConcepts:
    def test_thread_to_concept(self):
        concepts, remainder = extract_comment_concepts(THREAD_MD)
        assert len(concepts) == 1
        c = concepts[0]
        assert c.type == "note"
        assert c.title.startswith("Замечание рецензента: Имеется ввиду самый свежий ТН")
        assert c.tags == ["review", "comment", "Волкова"]
        assert "**Контекст:** Если найдено несколько табельных" in c.content
        assert "**Комментарий рецензента (Волкова Анастасия Владимировна, 2026-06-23):**" in c.content
        assert "**Ответ (Сагитов Алексей Минерович [2]):**" in c.content
        assert "**Статус:** замечание закрыто" in c.content

    def test_stub_replaces_thread_in_remainder(self):
        _, remainder = extract_comment_concepts(THREAD_MD)
        assert "[Комментарии извлечены программно: 2]" in remainder
        assert "самый свежий ТН" not in remainder
        assert "Алгоритм выбора табельного." in remainder
        assert "Далее по тексту." in remainder

    def test_old_format_single_comment_still_extracted(self):
        concepts, remainder = extract_comment_concepts(OLD_FORMAT_MD)
        assert len(concepts) == 1
        assert "Поправьте формулировку" in concepts[0].content
        assert "[Комментарии извлечены программно: 1]" in remainder
        assert "Поправьте формулировку" not in remainder

    def test_regular_blockquote_untouched(self):
        chunk = "Текст.\n\n> Обычная цитата без меток рецензента\n\nЕщё текст."
        concepts, remainder = extract_comment_concepts(chunk)
        assert concepts == []
        assert "> Обычная цитата без меток рецензента" in remainder

    def test_two_threads_in_one_chunk(self):
        chunk = (
            "> **Комментарий рецензента (Рецензент):** Первый вопрос\n\n"
            "Промежуточный абзац.\n\n"
            "> **Комментарий рецензента (Рецензент):** Второй вопрос\n"
            "> **Ответ (Автор):** Второй ответ\n"
        )
        concepts, remainder = extract_comment_concepts(chunk)
        assert len(concepts) == 2
        assert "Первый вопрос" in concepts[0].content
        assert "Второй ответ" in concepts[1].content
        assert remainder.count("[Комментарии извлечены программно:") == 2
        assert "Промежуточный абзац." in remainder

    def test_no_comments_passthrough(self):
        chunk = "# Заголовок\n\nАбзац.\n\n| a | b |\n|---|---|\n| 1 | 2 |"
        concepts, remainder = extract_comment_concepts(chunk)
        assert concepts == []
        assert remainder == chunk

    def test_unresolved_thread_has_no_status(self):
        chunk = "> **Комментарий рецензента (Рецензент):** Открытый вопрос\n"
        concepts, _ = extract_comment_concepts(chunk)
        assert "Статус" not in concepts[0].content

    def test_answer_without_question_handled(self):
        chunk = "> **Ответ (Автор):** Осиротевший ответ\n"
        concepts, remainder = extract_comment_concepts(chunk)
        assert len(concepts) == 1
        assert "Осиротевший ответ" in concepts[0].content
        assert "[Комментарии извлечены программно: 1]" in remainder

    def test_author_with_comma_not_misread_as_date(self):
        chunk = "> **Комментарий рецензента (Иванов, Иван Иванович):** Вопрос\n"
        concepts, _ = extract_comment_concepts(chunk)
        # «Иван Иванович» — не дата: автор не должен обрезаться
        assert "Иванов, Иван Иванович" in concepts[0].content


class TestThreadTitles:
    """Короткий/неразговорный вопрос неинформативен как заголовок —
    дополняется префиксом контекста якоря (виден в поиске и источниках)."""

    def test_short_question_title_from_context(self):
        md = (
            "> **Контекст:** Если найдено несколько табельных, тогда сортируем их по статусу занятости и выбираем последний\n"
            "> **Комментарий рецензента (Волкова, 2026-06-23):** Аналогично вопросу выше\n"
            "> **Ответ (Сагитов):** Речь о наибольшем табельном номере\n"
        )
        concepts, _ = extract_comment_concepts(md)
        assert len(concepts) == 1
        title = concepts[0].title
        assert title.startswith("Замечание рецензента (Если найдено несколько табельных")
        assert title.endswith("): Аналогично вопросу выше")

    def test_context_prefix_truncated_at_word_boundary(self):
        long_ctx = "Если не найден ни один табельный номер тогда пытаемся найти ГПХ по тем же критериям"
        md = (
            f"> **Контекст:** {long_ctx}\n"
            "> **Комментарий рецензента (Рецензент):** А что дальше\n"
        )
        concepts, _ = extract_comment_concepts(md)
        title = concepts[0].title
        assert "…" in title  # контекст длиннее лимита — обрезан по границе слова
        assert title.endswith("): А что дальше")
        assert len(title) < 150

    def test_long_question_title_unchanged(self):
        """Полноценный вопрос (>= 5 слов) — заголовок из вопроса, без контекста."""
        md = (
            "> **Контекст:** Какой-то контекст якоря\n"
            "> **Комментарий рецензента (Рецензент):** Имеется ввиду самый свежий ТН или последний в списке\n"
        )
        concepts, _ = extract_comment_concepts(md)
        assert concepts[0].title == "Замечание рецензента: Имеется ввиду самый свежий ТН или последний в списке"

    def test_short_question_without_context_falls_back(self):
        md = "> **Комментарий рецензента (Рецензент):** А что тут\n"
        concepts, _ = extract_comment_concepts(md)
        assert concepts[0].title == "Замечание рецензента: А что тут"


class TestExtractorOrderVsFieldTables:
    """Edge-case: незакрытая строка markdown-таблицы «заглатывает» последующие
    строки (многострочные ячейки field_table). Если комментарии извлекать
    ПОСЛЕ таблиц, блок-цитата после незакрытой последней строки таблицы
    попадает в ячейку и теряется. Порядок generate_chunk: комментарии →
    таблицы → LLM."""

    # Таблица полей: ПОСЛЕДНЯЯ строка не закрыта (многострочная ячейка),
    # блок-цитата комментария идёт вплотную (худший случай).
    WORST_CASE = """# Вид сообщения 112: контроль ЭЛН

| Поле/Элемент | Тип | Длина | Кратность | Описание |
|---|---|---|---|---|
| snils | p:snils |  | 1..1 | СНИЛС |
| surname | com:surname | Тип.Длина: 60 | 1..1 | Фамилия |
| lnState | com:lnState | Тип.Длина: 3 | 1..1 | Код статуса |
| gender | xs:int |  | 1..1 | Пол |
| innPerson | p:inn | Тип.Длина: 12 | 0..1 | ИНН застрахованного
> **Комментарий рецензента (Рецензент):** Проверить длину ИНН
> **Ответ (Автор):** Учтено в тексте
"""

    def test_comment_survives_table_swallow_in_new_order(self, monkeypatch):
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: s)

        # НОВЫЙ порядок (как в generate_chunk): комментарии → таблицы
        comments, remainder1 = extract_comment_concepts(self.WORST_CASE)
        tables, _rows, remainder2 = extract_field_table_concepts(remainder1)

        assert any("Проверить длину ИНН" in c.content for c in comments)
        assert "Проверить длину ИНН" not in remainder2  # LLM-вход без сырого комментария
        assert tables  # таблица тоже извлечена программно
        assert any(c.title.startswith("innPerson") or "innPerson" in c.content for c in tables)

    def test_old_order_loses_comment(self, monkeypatch):
        """Демонстрация диагностированной проблемы: при порядке «таблицы
        первыми» комментарий проглатывается в ячейку innPerson и исчезает
        из LLM-входа. Регрессионная защита порядка экстракторов."""
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: s)

        tables, rows, remainder = extract_field_table_concepts(self.WORST_CASE)
        comments_after, _ = extract_comment_concepts(remainder)

        assert tables
        assert comments_after == []  # комментарий уже съеден таблицей
        assert "Проверить длину ИНН" not in remainder
        assert any("Проверить длину" in (r.description or "") for r in rows)


class TestGenerateChunkHook:
    def test_comment_concepts_extracted_before_llm(self, monkeypatch):
        """generate_chunk: концепты-комментарии идут ДО LLM-концептов,
        заглушка не доходит до LLM."""
        gen = OKFGenerator(llm=_FakeLLM())
        chunk = (
            "# Раздел\n\n"
            "> **Комментарий рецензента (Рецензент):** Вопрос по разделу\n"
            "> **Ответ (Автор):** Ответ автора\n\n"
            "Текст раздела для LLM.\n"
        )
        concepts = gen.generate_chunk(chunk, "doc.docx", 1, 1, doc_id="test")
        comments = [c for c in concepts if "review" in c.tags]
        assert len(comments) == 1
        assert "Вопрос по разделу" in comments[0].content
        # LLM получил остаток без комментария
        assert "Вопрос по разделу" not in _FakeLLM.last_user
        assert "Текст раздела для LLM." in _FakeLLM.last_user

    def test_flag_disables_extraction(self, monkeypatch):
        s = get_settings()
        monkeypatch.setattr(s, "okf_comment_concepts_enabled", False)
        monkeypatch.setattr("app.services.okf_generator.get_settings", lambda: s)
        gen = OKFGenerator(llm=_FakeLLM(), bundle_root=None)
        # OKFGenerator кэширует settings в __init__ — пересоздаём после патча
        gen.settings = s
        chunk = "> **Комментарий рецензента (Рецензент):** Вопрос\n\nТекст.\n"
        concepts = gen.generate_chunk(chunk, "doc.docx", 1, 1, doc_id="test")
        assert not any("review" in c.tags for c in concepts)
        assert "Комментарий рецензента" in _FakeLLM.last_user  # ушло в LLM как есть


class _FakeLLM:
    last_user: str = ""

    def chat_json(self, system, user, doc_id="unknown", chunk_idx=0, salvage_truncated=False):
        _FakeLLM.last_user = user
        return [
            {
                "id": "llm-concept",
                "title": "LLM-концепт",
                "type": "concept",
                "tags": ["llm"],
                "content": "Содержимое из LLM.",
                "relations": [],
            }
        ]
