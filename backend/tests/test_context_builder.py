"""Юнит-тесты context_builder: resolve_branches, merge_and_format, format_context."""
import pytest

from app.config import Settings, SEARCH_MODE_PRESETS
from app.services.context_builder import (
    _stem_ru,
    drop_partial_title_matches,
    drop_unmatched_blocks,
    format_context,
    matched_terms,
    merge_and_format,
    resolve_branches,
    title_matched_terms,
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

    def test_concept_content_kept_for_concept_plus_chunk(self, settings):
        """Merge сохраняет собственный контент репрезентативного концепта
        (concept_content) — точный фильтр подменяет им сырой чанк."""
        c1 = Hit("c1", 0.9, {"point_type": "concept", "doc_id": "d1", "chunk_index": 0,
                            "title": "Настройка", "tags": ["a"], "content": "выжимка про настройку",
                            "filepath": "d1/setup.md", "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 0)
        ch = Hit("ch0", 0.8, {"point_type": "chunk", "doc_id": "d1", "chunk_index": 0,
                              "tags": ["a"], "content": "полный текст чанка", "section_title": ""}, 2)
        merged = merge_and_format([c1, ch], settings)
        assert len(merged) == 1
        assert merged[0]["content"] == "полный текст чанка"
        assert merged[0]["concept_content"] == "выжимка про настройку"

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


class TestMergeReviewConcepts:
    """Замечания рецензентов (тег review) не представляют смешанную группу:
    primary — первый основной концепт, замечания — сиблинги (регрессия
    02.09.2026: узкое замечание обгоняло широкий основной концепт по fused
    score и перехватывало заголовок/цитату [1] группы)."""

    def _review(self, pid: str, score: float, title: str) -> Hit:
        return Hit(pid, score, {"point_type": "concept", "doc_id": "d1", "chunk_index": 1,
                                "title": title, "tags": ["review", "comment", "Рецензентов"],
                                "content": "текст замечания", "filepath": f"d1/{pid}.md",
                                "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 0)

    def _main(self, pid: str, score: float, title: str) -> Hit:
        return Hit(pid, score, {"point_type": "concept", "doc_id": "d1", "chunk_index": 1,
                                "title": title, "tags": ["business"], "content": "выжимка основного концепта",
                                "filepath": f"d1/{pid}.md",
                                "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 1)

    def _chunk(self, pid: str, score: float, section_title: str = "Алгоритм выбора") -> Hit:
        return Hit(pid, score, {"point_type": "chunk", "doc_id": "d1", "chunk_index": 1, "tags": [],
                                "content": "полный сырой текст чанка", "section_title": section_title}, 2)

    def test_mixed_group_primary_is_main_concept(self, settings):
        """Чанк + замечание (score выше) + основной концепт: primary — основной
        концепт, замечание — сиблинг; content по-прежнему из чанка."""
        review = self._review("rev1", 0.95, "Замечание рецензента: Аналогично вопросу выше")
        main = self._main("main1", 0.80, "Проверка персональных данных сотрудника")
        chunk = self._chunk("ch1", 0.90)
        merged = merge_and_format([review, main, chunk], settings)
        assert len(merged) == 2
        primary, sibling = merged[0], merged[1]
        assert primary["title"] == "Проверка персональных данных сотрудника"
        assert primary["kind"] == "concept+chunk"
        assert primary["content"] == "полный сырой текст чанка"
        assert primary["filepath"] == "d1/main1.md"
        assert primary["concept_content"] == "выжимка основного концепта"
        assert primary["score"] == 0.95  # best_score группы
        assert sibling["title"] == "Замечание рецензента: Аналогично вопросу выше"
        assert sibling["kind"] == "review"  # иммунитет анти-шумового фильтра
        assert sibling["score"] == 0.95

    def test_chunk_with_only_reviews_chunk_is_primary(self, settings):
        """Чанк + только замечания: primary — сам чанк (kind=chunk, section
        title), замечания — сиблинги, замечание не представляет группу."""
        review = self._review("rev1", 0.95, "Замечание рецензента: Вопрос к таблице")
        chunk = self._chunk("ch1", 0.90, section_title="Таблица полей сообщения")
        merged = merge_and_format([review, chunk], settings)
        assert len(merged) == 2
        primary, sibling = merged[0], merged[1]
        assert primary["title"] == "Таблица полей сообщения"
        assert primary["kind"] == "chunk"
        assert primary["point_type"] == "chunk"
        assert primary["filepath"] == "d1/chunks/chunk_01.md"
        assert primary["concept_content"] is None
        assert sibling["title"] == "Замечание рецензента: Вопрос к таблице"
        assert sibling["kind"] == "review"

    def test_review_only_group_unchanged(self, settings):
        """Группа из одних замечаний без чанка (поиск с фильтром tags=[review])
        — прежнее поведение: первый замечание как primary."""
        r1 = self._review("rev1", 0.95, "Замечание рецензента: Первое")
        r2 = self._review("rev2", 0.90, "Замечание рецензента: Второе")
        merged = merge_and_format([r1, r2], settings)
        assert len(merged) == 2
        assert merged[0]["title"] == "Замечание рецензента: Первое"
        assert merged[0]["kind"] == "concept"
        assert merged[0]["filepath"] == "d1/rev1.md"

    def test_main_and_review_without_chunk(self, settings):
        """Основной концепт + замечание без чанка: primary — основной."""
        review = self._review("rev1", 0.95, "Замечание рецензента: Вопрос")
        main = self._main("main1", 0.80, "Основной концепт")
        merged = merge_and_format([review, main], settings)
        assert len(merged) == 2
        assert merged[0]["title"] == "Основной концепт"
        assert merged[1]["title"] == "Замечание рецензента: Вопрос"

    def test_main_concept_without_title_falls_to_section(self, settings):
        """Основной концепт с пустым title → секционный заголовок чанка."""
        main = Hit("main1", 0.80, {"point_type": "concept", "doc_id": "d1", "chunk_index": 1,
                                   "title": "", "tags": ["business"], "content": "выжимка",
                                   "filepath": "d1/main1.md",
                                   "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 1)
        chunk = self._chunk("ch1", 0.90, section_title="Секция X")
        merged = merge_and_format([main, chunk], settings)
        assert merged[0]["title"] == "Секция X"
        assert merged[0]["kind"] == "concept+chunk"


class TestStemRu:
    """Стемминг словоформ для маркерных функций: «табельного» ↔ «табельных».

    Guard: основа после среза >= 5 символов, только кириллица, консервативная
    таблица окончаний. sparse-токенайзер (services/sparse.py) не трогается.
    """

    def test_tabelny_word_forms_same_stem(self):
        assert _stem_ru("табельного") == _stem_ru("табельных") == "табельн"

    def test_number_forms_same_stem(self):
        assert _stem_ru("номера") == _stem_ru("номеров") == _stem_ru("номеру") == "номер"

    def test_domain_term_otpusk(self):
        """Доменный SAP HCM термин 6 букв: «отпуск» (нулевое окончание) и
        «отпуска» дают один стем — ложных срезов коротких основ нет."""
        assert _stem_ru("отпуск") == "отпуск"
        assert _stem_ru("отпуска") == "отпуск"
        assert _stem_ru("отпуском") == "отпуск"

    def test_short_stem_guard(self):
        """«дата» → основа «дат» < 5: срез не выполняется (ложные совпадения
        коротких доменных терминов недопустимы)."""
        assert _stem_ru("дата") == "дата"
        assert _stem_ru("кода") == "кода"

    def test_latin_untouched(self):
        assert _stem_ru("zprp") == "zprp"
        assert _stem_ru("expanded") == "expanded"

    def test_posobie_forms(self):
        """«пособий» → «пособ»; подстроковый матч поймает «пособие» («пособ»
        входит в «пособие») — словоформы пособия матчатся."""
        assert _stem_ru("пособий") == "пособ"
        assert "пособ" in "пособие"


class TestMatchedTermsStemming:
    def _item(self, title: str, content: str) -> dict:
        return {"title": title, "content": content, "tags": [], "source_filename": "f",
                "point_type": "concept", "kind": "concept", "chunk_index": None}

    def test_word_form_matched_via_stem(self):
        """Запрос «табельного» (род.п.), текст «табельных» — маркер непуст
        («выбор» матчится точно через title, «номера» — подстрокой «номерам»)."""
        item = self._item("Выбор ТН", "сортируем по статусу занятости и табельных номерам")
        assert matched_terms(item, "выбор табельного номера") == ["выбор", "табельного", "номера"]

    def test_domain_term_matched_via_stem(self):
        """«отпуска» (запрос) ↔ «отпуск» (текст) — частый SAP HCM термин."""
        item = self._item("Отпуск", "порядок предоставления отпуска сотруднику")
        assert "отпуска" in matched_terms(item, "как оформить отпуска")

    def test_short_words_not_stem_matched(self):
        """Guard: «даты» ↔ «дата» — основа < 5, стемминг не срабатывает."""
        item = self._item("Период", "дата начала и дата окончания")
        assert matched_terms(item, "какие даты увольнения") == []

    def test_exact_match_still_primary(self):
        item = self._item("ЭЛН", "текст")
        assert matched_terms(item, "ЭЛН") == ["элн"]


class TestDropUnmatchedReviewImmunity:
    """Иммунитет сиблинг-замечаний (kind="review") в анти-шумовом фильтре —
    строго по kind, не по тегу: primary-блок группы несёт union-теги (включая
    review от замечаний-хитов) и должен фильтроваться как обычный блок."""

    def _item(self, title: str, content: str, kind: str = "concept", tags=None) -> dict:
        return {"title": title, "content": content, "tags": tags or [],
                "source_filename": "f", "point_type": "concept",
                "kind": kind, "chunk_index": None}

    def test_review_sibling_survives_empty_marker(self):
        """Позитив: review-сиблинг без лексических совпадений выживает при
        матчевых соседях (регрессия 02.09.2026: замечания пропадали из
        источников — их текст в другой словоформе, чем запрос)."""
        matched = self._item("Проверка персональных данных", "сортировка табельных номеров")
        review = self._item("Замечание рецензента: Имеется ввиду самый свежий ТН?",
                            "Что бы не усложнять алгоритм, наибольший табельный",
                            kind="review", tags=["review", "comment", "Волкова"])
        noise = self._item("Перечень: Название столбца", "таблица полей")
        kept = drop_unmatched_blocks([matched, review, noise], "Выбор табельного номера")
        assert [m["title"] for m in kept] == [matched["title"], review["title"]]

    def test_mixed_primary_with_review_tags_is_cut(self):
        """Негатив: mixed-primary (kind=concept+chunk) c review в union-тегах
        и пустым маркером РЕЖЕТСЯ — иммунитет не наследуется через теги."""
        mixed_primary = self._item(
            "Группа с замечаниями", "контент без лексики запроса",
            kind="concept+chunk", tags=["business", "review", "comment"],
        )
        matched = self._item("Матчевый блок", "табельного номера выбор")
        kept = drop_unmatched_blocks([matched, mixed_primary], "Выбор табельного номера")
        assert [m["title"] for m in kept] == [matched["title"]]

    def test_primary_main_concept_with_review_union_tags_is_cut(self):
        """Негатив (тонкий случай): primary основной группы БЕЗ чанка получает
        union-теги группы (review от замечаний-хитов) при kind="concept" —
        иммунитет по kind="review" его не защищает, пустой маркер режется."""
        primary = self._item("Основной концепт группы", "текст без совпадений",
                             kind="concept", tags=["business", "review"])
        matched = self._item("Матчевый блок", "выбор табельного номера")
        kept = drop_unmatched_blocks([matched, primary], "Выбор табельного номера")
        assert [m["title"] for m in kept] == [matched["title"]]

    def test_plain_concept_without_match_is_cut(self):
        noise = self._item("Шумовой концепт", "чужая таблица полей")
        matched = self._item("Матчевый блок", "выбор табельного номера")
        kept = drop_unmatched_blocks([matched, noise], "Выбор табельного номера")
        assert [m["title"] for m in kept] == [matched["title"]]

    def test_review_only_list_all_survive(self):
        """Выдача только из замечаний без совпадений — фильтр отключён
        (нет ни одного матчевого блока), прежнее поведение."""
        reviews = [self._item("Замечание рецензента: Первое", "текст", kind="review"),
                   self._item("Замечание рецензента: Второе", "текст", kind="review")]
        assert drop_unmatched_blocks(reviews, "парафразный вопрос") == reviews

    def test_merge_marks_review_siblings_with_review_kind(self, settings):
        """merge_and_format проставляет сиблингам-замечаниям kind="review"
        (основные сиблинги — kind="concept")."""
        review = Hit("rev1", 0.95, {"point_type": "concept", "doc_id": "d1", "chunk_index": 1,
                                     "title": "Замечание рецензента: Вопрос", "tags": ["review", "comment"],
                                     "content": "текст замечания", "filepath": "d1/rev1.md",
                                     "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 0)
        main = Hit("main1", 0.80, {"point_type": "concept", "doc_id": "d1", "chunk_index": 1,
                                   "title": "Основной концепт", "tags": ["business"],
                                   "content": "выжимка", "filepath": "d1/main1.md",
                                   "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 1)
        chunk = Hit("ch1", 0.90, {"point_type": "chunk", "doc_id": "d1", "chunk_index": 1, "tags": [],
                                  "content": "сырой чанк", "section_title": "Секция"}, 2)
        main_sibling = Hit("main2", 0.70, {"point_type": "concept", "doc_id": "d1", "chunk_index": 1,
                                           "title": "Второй основной", "tags": ["business"],
                                           "content": "выжимка2", "filepath": "d1/main2.md",
                                           "source_document": {"filename": "f.docx", "doc_id": "d1"}}, 3)
        merged = merge_and_format([review, main, chunk, main_sibling], settings)
        kinds = {m["title"]: m["kind"] for m in merged}
        assert kinds["Основной концепт"] == "concept+chunk"  # primary: union-теги, но kind не review
        assert kinds["Замечание рецензента: Вопрос"] == "review"
        assert kinds["Второй основной"] == "concept"

    def test_regression_scenario_seven_blocks(self):
        """Регрессионный сценарий по пробы 02.09.2026: 5 матчевых блоков +
        2 review-сиблинга (пустой маркер — словоформы) + 3 шума → 7 блоков
        в контексте и источниках (было 5: замечания терялись)."""
        blocks = [
            self._item("Проверка персональных данных", "выбор табельного номера"),
            self._item("Регистрация своих МЧД", "выбор номера"),
            self._item("Заполнение полей", "выбор табельного номера"),
            self._item("Доработка расширения", "выбор"),
            self._item("CERTIFIED_COPIES", "табельного номера"),
            self._item("Замечание рецензента: Имеется ввиду самый свежий ТН?",
                       "наибольший табельный самый свежий", kind="review", tags=["review"]),
            self._item("Замечание рецензента (Если не найден ни один табельный…)",
                       "речь о наибольшем табельном номере", kind="review", tags=["review"]),
            self._item("Перечень: Название столбца", "таблица"),
            self._item("TYPEX", "поле"),
            self._item("ZT3409_BEN_MES", "поле"),
        ]
        kept = drop_unmatched_blocks(blocks, "Выбор табельного номера")
        assert len(kept) == 7
        assert sum(1 for m in kept if m["kind"] == "review") == 2


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

    def test_title_match_terms_in_title(self):
        item = self._item("Доработка расширения для ZPRP_DISABILITY_CHLD", "тело без имени")
        assert title_matched_terms(item, "ZPRP_DISABILITY_CHLD") == ["zprp", "disability", "chld"]

    def test_title_match_only_in_body_is_empty(self):
        item = self._item("Отчет «Контроль ЭЛН в проактиве»", "упоминает ZPRP_JOURNAL в тексте")
        assert title_matched_terms(item, "ZPRP_DISABILITY_CHLD") == []

    def test_title_match_without_query(self):
        item = self._item("ZPRP_DISABILITY_CHLD", "тело")
        assert title_matched_terms(item, None) == []

    def test_title_match_service_words_filtered(self):
        item = self._item("какие ZPRP поля", "тело")
        # «какие» — служебное слово, отфильтровано; «поля» и «zprp» легитимно в титле.
        assert title_matched_terms(item, "какие поля ZPRP") == ["поля", "zprp"]

    def test_format_context_title_match_metadata(self):
        about = self._item("Расширения для тр. ZPRP_DISABILITY_CHLD", "тело")
        mentions = self._item("Отчет «Контроль ЭЛН»", "ZPRP_JOURNAL упомянут")
        ctx = format_context([about, mentions], query="ZPRP_DISABILITY_CHLD")
        assert "Title match: [zprp, disability, chld]" in ctx
        assert "Title match: []" in ctx


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


class TestDropPartialTitleMatches:
    def _item(self, title: str, content: str = "тело") -> dict:
        return {"title": title, "content": content, "tags": [], "source_filename": "f",
                "point_type": "concept", "kind": "concept", "chunk_index": None}

    def test_exact_name_query_keeps_only_full_title_blocks(self):
        """«ZPRP_DISABILITY_CHLD»: заголовок [2] покрывает все токены — остаётся
        только он, смежный ЭЛН-отчёт (упоминание в теле) выбрасывается."""
        about = self._item("Расширения для тр. ZPRP_DISABILITY_CHLD - Журнал")
        adjacent = self._item("Отчет «Контроль ЭЛН в проактиве»", "ZPRP_JOURNAL в тексте")
        kept = drop_partial_title_matches([about, adjacent], "ZPRP_DISABILITY_CHLD")
        assert kept == [about]

    def test_exact_name_swaps_chunk_content_for_concept_content(self):
        """Точный запрос: у concept+chunk-блока контент заменяется на выжимку
        концепта — сырой чанк содержит чужие подразделы раздела."""
        about = {
            "title": "Доработка расширения для ZPRP_DISABILITY_CHLD",
            "content": "сырой чанк с таблицей ЭЛН-журнала и чужими подразделами",
            "concept_content": "Алгоритм ZCL_3409_BADI_PRP-ADD_DIS_CHLD_FROM_IT, отчет HRULAPL4.",
            "tags": [], "source_filename": "f", "point_type": "concept",
            "kind": "concept+chunk", "chunk_index": 4,
        }
        kept = drop_partial_title_matches([about], "ZPRP_DISABILITY_CHLD")
        assert kept[0]["content"] == "Алгоритм ZCL_3409_BADI_PRP-ADD_DIS_CHLD_FROM_IT, отчет HRULAPL4."
        # Исходный блок не мутирован (контент чанка сохранён в копии).
        assert "сырой чанк" in about["content"]

    def test_exact_name_without_concept_content_keeps_chunk(self):
        about = self._item("ZPRP DISABILITY CHLD журнал")
        kept = drop_partial_title_matches([about], "ZPRP DISABILITY_CHLD")
        assert kept[0]["content"] == "тело"

    def test_no_full_title_match_keeps_all(self):
        """Естественный запрос: ни один заголовок не покрывает все токены."""
        a = self._item("Создание записи для ЛК")
        b = self._item("Перечень: Наименование поля", "поля заполняются из ЛК")
        kept = drop_partial_title_matches([a, b], "Какие интеграции с ЛК есть?")
        assert kept == [a, b]

    def test_single_token_query_keeps_all(self):
        a = self._item("Отпуск")
        b = self._item("Журнал отсутствий", "отпуск упоминается")
        kept = drop_partial_title_matches([a, b], "отпуск")
        assert kept == [a, b]

    def test_word_form_now_matches(self):
        """С лёгким стеммингом словоформа в заголовке считается совпадением:
        «интеграции» (запрос) ↔ «Интеграция» (заголовок) — фильтр срабатывает
        и ограничивает контекст блоками «про объект». До 02.09.2026 словоформы
        не матчились и фильтр пропускал смежный шум."""
        a = self._item("Интеграция SAP HCM с СФР СЭДО")
        b = self._item("Журнал", "интеграции описаны в тексте")
        kept = drop_partial_title_matches([a, b], "интеграции сэдо")
        assert kept == [a]

    def test_case_insensitive_and_underscore(self):
        about = self._item("lk_stat - статус согласования")
        other = self._item("Другое", "LK_STAT в тексте")
        kept = drop_partial_title_matches([about, other], "LK_STAT")
        assert kept == [about]

    def test_empty_inputs(self):
        assert drop_partial_title_matches([], "ЛК журнал") == []
        item = self._item("ЛК")
        assert drop_partial_title_matches([item], None) == [item]
