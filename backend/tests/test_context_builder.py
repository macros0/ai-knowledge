"""Юнит-тесты context_builder: resolve_branches, merge_and_format, format_context."""
import pytest

from app.config import Settings, SEARCH_MODE_PRESETS
from app.services.context_builder import format_context, merge_and_format, resolve_branches
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
        """2 концепта из одного чанка + сам чанк → merge, title через ' / ', content один раз."""
        c1 = Hit("c1", 0.9, {"point_type": "concept", "doc_id": "d1", "chunk_index": 0,
                            "title": "Настройка", "tags": ["a"], "content": "summary1",
                            "filepath": "d1/setup.md", "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 0)
        c2 = Hit("c2", 0.85, {"point_type": "concept", "doc_id": "d1", "chunk_index": 0,
                             "title": "Проверка", "tags": ["b"], "content": "summary2",
                             "filepath": "d1/check.md", "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 1)
        ch = Hit("ch0", 0.8, {"point_type": "chunk", "doc_id": "d1", "chunk_index": 0,
                             "tags": ["a"], "content": "полный текст", "section_title": ""}, 2)
        merged = merge_and_format([c1, c2, ch], settings)
        assert len(merged) == 1
        assert "Настройка" in merged[0]["title"]
        assert "Проверка" in merged[0]["title"]
        assert " / " in merged[0]["title"]
        assert merged[0]["content"] == "полный текст"
        assert sorted(merged[0]["tags"]) == ["a", "b"]
        assert merged[0]["kind"] == "concept+chunk"

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


class TestContextCharLimit:
    """Лимит проверялся до добавления блока, поэтому итог мог превысить
    chat_max_context_chars на размер целого блока (до 6000 символов)."""

    def _hit(self, i: int, size: int) -> Hit:
        return Hit(
            f"c{i}", 1.0 / (i + 1),
            {"point_type": "concept", "doc_id": f"d{i}", "chunk_index": 0,
             "title": f"Концепт {i}", "content": "я" * size, "tags": [],
             "source_document": {"filename": "doc.docx"}, "filepath": f"d{i}/c.md"},
        )

    def test_total_never_exceeds_limit(self):
        settings = Settings(chat_max_context_chars=1000, chat_concept_max_chars=400)
        hits = [self._hit(i, 400) for i in range(10)]
        merged = merge_and_format(hits, settings=settings)
        total = sum(len(m["content"]) for m in merged)
        assert total <= 1000, f"контекст {total} символов при лимите 1000"
        assert len(merged) == 2

    def test_single_oversized_block_still_returned(self):
        """Пустой контекст хуже небольшого перебора — первый блок берём всегда."""
        settings = Settings(chat_max_context_chars=100, chat_concept_max_chars=5000)
        merged = merge_and_format([self._hit(0, 3000)], settings=settings)
        assert len(merged) == 1

    def test_stops_before_oversized_block_not_after(self):
        settings = Settings(chat_max_context_chars=1000, chat_concept_max_chars=900)
        hits = [self._hit(0, 600), self._hit(1, 600)]
        merged = merge_and_format(hits, settings=settings)
        assert len(merged) == 1
        assert sum(len(m["content"]) for m in merged) == 600
