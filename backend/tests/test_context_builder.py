"""Юнит-тесты context_builder: resolve_branches, merge_and_format, format_context."""
import pytest

from app.config import Settings, SEARCH_MODE_PRESETS
from app.services.context_builder import (
    drop_unmatched_blocks,
    format_context,
    matched_terms,
    merge_and_format,
    resolve_branches,
)
from app.services.fusion import Hit


def _settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, embedding_provider="fake", embedding_dimensions=8)


@pytest.fixture
def settings(tmp_path):
    return _settings(tmp_path)


class TestResolveBranches:
    def test_preset_dense(self, settings):
        assert resolve_branches("dense", None, None, settings) == {"dense"}

    def test_preset_hybrid(self, settings):
        assert resolve_branches("hybrid", None, None, settings) == {"dense", "bm25"}

    def test_explicit_flags_override_preset(self, settings):
        branches = resolve_branches("hybrid", True, False, settings)
        assert branches == {"dense"}

    def test_explicit_flags_all_none_uses_default(self, settings):
        branches = resolve_branches(None, None, None, settings)
        assert "dense" in branches

    def test_empty_flags_falls_back_to_dense(self, settings):
        branches = resolve_branches("hybrid", False, False, settings)
        assert branches == {"dense"}


class TestMergeAndFormat:
    def test_case_a_concept_plus_chunk_merges(self, settings):
        """Случай A: концепт + чанк из одного раздела → merge, title из концепта, content из чанка."""
        concept = Hit(
            "c1", 0.9,
            {"point_type": "concept", "doc_id": "d1", "chunk_index": 0, "title": "Маршрутизация",
             "tags": ["net"], "content": "краткое описание", "filepath": "d1/routing.md",
             "source_document": {"filename": "net.docx", "doc_id": "d1"}},
            rank=0,
        )
        chunk = Hit(
            "ch0", 0.8,
            {"point_type": "chunk", "doc_id": "d1", "chunk_index": 0, "tags": ["net"],
             "content": "полный сырой текст чанка со всеми WSDL URL",
             "section_title": "Настройка маршрутизации"},
            rank=1,
        )
        merged = merge_and_format([concept, chunk], settings)
        assert len(merged) == 1
        assert merged[0]["title"] == "Маршрутизация"
        assert "WSDL URL" in merged[0]["content"]
        assert merged[0]["point_type"] == "concept"
        assert merged[0]["kind"] == "concept+chunk"

    def test_case_b_only_concept(self, settings):
        """Случай B: только концепт → title + summary."""
        concept = Hit(
            "c1", 0.9,
            {"point_type": "concept", "doc_id": "d1", "chunk_index": None, "title": "VLAN",
             "tags": ["net"], "content": "виртуальные сети", "filepath": "d1/vlan.md",
             "source_document": {"filename": "net.docx", "doc_id": "d1"}},
            rank=0,
        )
        merged = merge_and_format([concept], settings)
        assert len(merged) == 1
        assert merged[0]["title"] == "VLAN"
        assert merged[0]["content"] == "виртуальные сети"
        assert merged[0]["kind"] == "concept"

    def test_case_c_only_chunk_synthetic_title(self, settings):
        """Случай C: только чанк без section_title → синтетический title с разделом."""
        chunk = Hit(
            "ch0", 0.8,
            {"point_type": "chunk", "doc_id": "d1", "chunk_index": 2, "tags": ["doc"],
             "content": "сырой текст", "section_title": ""},
            rank=0,
        )
        lookup = {"d1": "manual.docx"}
        merged = merge_and_format([chunk], settings, filename_lookup=lookup)
        assert len(merged) == 1
        assert merged[0]["title"] == "manual.docx (Раздел 3)"
        assert merged[0]["point_type"] == "chunk"
        assert merged[0]["kind"] == "chunk"

    def test_case_c_only_chunk_with_section_title(self, settings):
        """Случай C: только чанк с section_title → title из section_title."""
        chunk = Hit(
            "ch0", 0.8,
            {"point_type": "chunk", "doc_id": "d1", "chunk_index": 2, "tags": ["doc"],
             "content": "сырой текст", "section_title": "Настройка сервера"},
            rank=0,
        )
        lookup = {"d1": "manual.docx"}
        merged = merge_and_format([chunk], settings, filename_lookup=lookup)
        assert len(merged) == 1
        assert merged[0]["title"] == "Настройка сервера"

    def test_many_to_one_two_concepts_one_chunk(self, settings):
        """2 концепта из одного чанка + сам чанк → первичный concept+chunk блок
        (title/content по первому по score) + сиблинг-концепт отдельным блоком."""
        c1 = Hit("c1", 0.9, {"point_type": "concept", "doc_id": "d1", "chunk_index": 0,
                            "title": "Настройка", "tags": ["a"], "content": "summary1",
                            "filepath": "d1/setup.md", "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 0)
        c2 = Hit("c2", 0.85, {"point_type": "concept", "doc_id": "d1", "chunk_index": 0,
                             "title": "Проверка", "tags": ["b"], "content": "summary2",
                             "filepath": "d1/check.md", "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 1)
        ch = Hit("ch0", 0.8, {"point_type": "chunk", "doc_id": "d1", "chunk_index": 0,
                             "tags": ["a"], "content": "полный текст", "section_title": ""}, 2)
        merged = merge_and_format([c1, c2, ch], settings)
        assert len(merged) == 2
        # Первичный блок: title репрезентативного концепта, content чанка.
        assert merged[0]["title"] == "Настройка"
        assert merged[0]["filepath"] == "d1/setup.md"
        assert merged[0]["content"] == "полный текст"
        assert sorted(merged[0]["tags"]) == ["a", "b"]
        assert merged[0]["kind"] == "concept+chunk"
        # Сиблинг: свой title, свой content, свой filepath.
        assert merged[1]["title"] == "Проверка"
        assert merged[1]["content"] == "summary2"
        assert merged[1]["filepath"] == "d1/check.md"
        assert merged[1]["kind"] == "concept"
        assert merged[1]["tags"] == ["b"]
        assert merged[1]["score"] == 0.85

    def test_many_to_one_siblings_respect_context_cap(self, settings):
        """Сиблинг-блоки не пробивают жёсткий лимит chat_max_context_chars."""
        cap = Settings(
            data_dir=settings.data_dir, embedding_provider="fake", embedding_dimensions=8,
            chat_max_context_chars=50,
        )
        concepts = [
            Hit(f"c{i}", 0.9 - i, {"point_type": "concept", "doc_id": "d1", "chunk_index": 0,
                                   "title": f"Поле {i}", "tags": [], "content": "x" * 90,
                                   "filepath": f"d1/f{i}.md",
                                   "source_document": {"filename": "f.docx", "doc_id": "d1"}}, i)
            for i in range(4)
        ]
        merged = merge_and_format(concepts, cap)
        # Первичный блок эмитится всегда (как и раньше), сиблинги — до исчерпания капа.
        assert len(merged) == 1

    def test_different_chunks_not_merged(self, settings):
        """Концепты из разных чанков → не объединяются."""
        c1 = Hit("c1", 0.9, {"point_type": "concept", "doc_id": "d1", "chunk_index": 0,
                            "title": "A", "tags": [], "content": "x",
                            "filepath": "d1/a.md", "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 0)
        c2 = Hit("c2", 0.8, {"point_type": "concept", "doc_id": "d1", "chunk_index": 1,
                            "title": "B", "tags": [], "content": "y",
                            "filepath": "d1/b.md", "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 1)
        merged = merge_and_format([c1, c2], settings)
        assert len(merged) == 2

    def test_empty_hits(self, settings):
        merged = merge_and_format([], settings)
        assert merged == []

    def test_filename_lookup_for_minimal_chunk(self, settings):
        """Чанк с минимальным payload (без source_document) → filename из lookup."""
        chunk = Hit(
            "ch0", 0.8,
            {"point_type": "chunk", "doc_id": "d1", "chunk_index": 0, "tags": [],
             "content": "текст", "section_title": ""},
            rank=0,
        )
        lookup = {"d1": "doc.docx"}
        merged = merge_and_format([chunk], settings, filename_lookup=lookup)
        assert merged[0]["source_filename"] == "doc.docx"
        assert merged[0]["filepath"] == "d1/chunks/chunk_00.md"


class TestFormatContext:
    def test_xml_format(self, settings):
        merged = [{"title": "Test", "content": "body text", "tags": ["a", "b"],
                   "source_filename": "doc.docx", "point_type": "concept", "kind": "concept",
                   "chunk_index": None}]
        ctx = format_context(merged)
        assert '<context_block id="1">' in ctx
        assert "Title: Test" in ctx
        assert "Type: concept" in ctx
        assert "Tags: [a, b]" in ctx
        assert "Source: doc.docx" in ctx
        assert "body text" in ctx

    def test_xml_format_kind_fallback_to_point_type(self, settings):
        """Без поля kind форматтер откатывается на point_type."""
        merged = [{"title": "Test", "content": "body text", "tags": [],
                   "source_filename": "doc.docx", "point_type": "chunk", "chunk_index": 0}]
        ctx = format_context(merged)
        assert "Type: chunk" in ctx

    def test_empty_context(self):
        ctx = format_context([])
        assert ctx == "Контекст пуст."

    def test_multiple_blocks(self, settings):
        merged = [
            {"title": "A", "content": "x", "tags": [], "source_filename": "f",
             "point_type": "concept", "kind": "concept", "chunk_index": None},
            {"title": "B", "content": "y", "tags": [], "source_filename": "g",
             "point_type": "chunk", "kind": "chunk", "chunk_index": 1},
        ]
        ctx = format_context(merged)
        assert 'id="1"' in ctx
        assert 'id="2"' in ctx
        assert "Type: concept" in ctx
        assert "Type: chunk" in ctx


class TestMatchedTerms:
    def _item(self, title: str, content: str) -> dict:
        return {"title": title, "content": content, "tags": [], "source_filename": "f",
                "point_type": "concept", "kind": "concept", "chunk_index": None}

    def test_terms_in_title_and_content(self):
        item = self._item("Создание записи для ЛК", "интеграции: Служебная таблица для ЛК")
        assert matched_terms(item, "Какие интеграции с ЛК есть?") == ["интеграции", "лк"]

    def test_no_terms_in_block(self):
        item = self._item("Фоновая загрузка", "обрабатываемых ЭЛН проверки")
        assert matched_terms(item, "Какие интеграции с ЛК есть?") == []

    def test_no_query_returns_empty(self):
        item = self._item("Заголовок", "ЛК упоминается")
        assert matched_terms(item, None) == []
        assert matched_terms(item, "") == []

    def test_short_and_stopword_tokens_skipped(self):
        """Служебные слова вопроса («какие», «есть») исключаются из маркера,
        хотя в BM25-поиске они участвуют."""
        item = self._item("ЛК", "интеграции есть какие")
        assert matched_terms(item, "Какие есть интеграции с ЛК") == ["интеграции", "лк"]

    def test_service_words_alone_give_empty_marker(self):
        """Блок, где совпали только служебные слова — маркер пустой."""
        item = self._item("Раздел", "здесь есть текст")
        assert matched_terms(item, "Какие есть интеграции с ЛК") == []

    def test_case_insensitive_match(self):
        """Токенизация lowercase: латиница и кириллица матчатся без учёта регистра."""
        item = self._item("lk_stat", "Заполняется из ЛК")
        assert "лк" in matched_terms(item, "интеграции ЛК")
        assert "lk" in matched_terms(item, "LK_STAT заполняется")

    def test_format_context_marker_present(self):
        merged = [self._item("Создание записи для ЛК", "интеграции с ЛК")]
        ctx = format_context(merged, query="Какие интеграции с ЛК есть?")
        assert "Matched terms: [интеграции, лк]" in ctx

    def test_format_context_marker_empty_without_match(self):
        merged = [self._item("Другое", "нет совпадений")]
        ctx = format_context(merged, query="Какие интеграции с ЛК есть?")
        assert "Matched terms: []" in ctx

    def test_format_context_marker_empty_without_query(self):
        merged = [self._item("ЛК", "текст")]
        ctx = format_context(merged)
        assert "Matched terms: []" in ctx


class TestDropUnmatchedBlocks:
    def _item(self, title: str, content: str) -> dict:
        return {"title": title, "content": content, "tags": [], "source_filename": "f",
                "point_type": "concept", "kind": "concept", "chunk_index": None}

    def test_drops_unmatched_when_some_match(self):
        merged = [
            self._item("Создание записи для ЛК", "текст про ЛК"),
            self._item("Сверка персональных данных", "100 сообщение СФР"),
            self._item("LK_STAT", "заполняется из ЛК"),
        ]
        kept = drop_unmatched_blocks(merged, "Какие интеграции с ЛК есть?")
        assert [m["title"] for m in kept] == ["Создание записи для ЛК", "LK_STAT"]

    def test_keeps_all_when_none_match(self):
        """Парафразный запрос без лексических совпадений — фильтр не срабатывает."""
        merged = [
            self._item("Отзыв из отпуска", "порядок отзыва"),
            self._item("Журнал отсутствий", "статусы"),
        ]
        kept = drop_unmatched_blocks(merged, "как закрыть больничный?")
        assert kept == merged

    def test_keeps_all_without_query(self):
        merged = [self._item("A", "x")]
        assert drop_unmatched_blocks(merged, None) == merged

    def test_empty_merged(self):
        assert drop_unmatched_blocks([], "ЛК") == []

    def test_preserves_order_and_objects(self):
        a = self._item("ЛК", "упоминает ЛК")
        b = self._item("Другое", "нет")
        kept = drop_unmatched_blocks([a, b], "интеграции ЛК")
        assert kept == [a]
