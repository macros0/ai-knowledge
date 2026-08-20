"""Тесты программного извлечения концептов из таблиц полей XML-сообщений."""
import pytest

from app.config import get_settings
from app.services.field_table import (
    FieldRow,
    TableBlock,
    build_field_concepts,
    build_overview_concept,
    detect_field_tables,
    extract_field_table_concepts,
    extract_table_concepts,
)


@pytest.fixture(autouse=True)
def _enable_field_table_min_rows(monkeypatch):
    """Программная экстракция таблиц полей по умолчанию выключена
    (okf_field_table_min_rows=0). Для тестов детектора включаем порог = 5."""
    s = get_settings()
    monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
    # get_settings кэшируется в lru_cache — патчим возвращаемое значение
    monkeypatch.setattr("app.services.field_table.get_settings", lambda: s)

# Минимальная таблица полей XML-сообщения (заголовок: поле | тип | длина | кратность | описание)
FIELD_TABLE = """# Вид сообщения 111: уведомление об изменении ЭЛН

| Поле/Элемент | Тип | Длина | Кратность | Описание |
|---|---|---|---|---|
| snils | p:snils |  | 1..1 | СНИЛС |
| surname | com:surname | Тип.Длина: 60 | 1..1 | Фамилия застрахованного лица |
| lnState | com:lnState | Тип.Длина: 3 | 1..1 | Код статуса ЭЛН |
| gender | xs:int |  | 1..1 | Пол застрахованного лица
0-женщина
1-мужчина |
| innPerson | p:inn | Тип.Длина: 12 | 0..1 | ИНН застрахованного |
"""

# Таблица данных (Excel-стиль) — НЕ должна детектироваться как таблица полей
DATA_TABLE = """| Дата | Сумма | Регион |
|---|---|---|
| 2024-01-01 | 1000 | Юг |
| 2024-02-01 | 2000 | Север |
| 2024-03-01 | 1500 | Восток |
| 2024-04-01 | 3000 | Запад |
| 2024-05-01 | 2500 | Центр |
"""


class TestDetectFieldTables:
    def test_detects_field_table(self):
        blocks = detect_field_tables(FIELD_TABLE)
        assert len(blocks) == 1
        b = blocks[0]
        assert isinstance(b, TableBlock)
        assert len(b.rows) == 5

    def test_finds_message_code_from_heading(self):
        blocks = detect_field_tables(FIELD_TABLE)
        # таблица под заголовком «Вид сообщения 111»
        lines = FIELD_TABLE.split("\n")
        from app.services.field_table import _find_message_code
        code = _find_message_code(lines, blocks[0].start)
        assert code == "111"

    def test_data_table_not_detected_as_field_table(self):
        blocks = detect_field_tables(DATA_TABLE)
        assert blocks == []

    def test_multiline_cell_gender_parsed(self):
        blocks = detect_field_tables(FIELD_TABLE)
        names = [r.name for r in blocks[0].rows]
        assert "gender" in names
        gender = next(r for r in blocks[0].rows if r.name == "gender")
        assert "0-женщина" in gender.description
        assert "1-мужчина" in gender.description

    def test_min_field_rows_threshold(self):
        # 4 строки данных — ниже порога 5, не детектируется
        small = """| Поле | Тип | Длина | Кратность | Описание |
|---|---|---|---|---|
| a | xs:int | | 1..1 | описание a |
| b | xs:int | | 1..1 | описание b |
| c | xs:int | | 1..1 | описание c |
| d | xs:int | | 1..1 | описание d |
"""
        assert detect_field_tables(small) == []

    def test_disabled_when_min_rows_zero(self, monkeypatch):
        # при okf_field_table_min_rows=0 (или <0) экстракция выключена
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 0)
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: s)
        assert detect_field_tables(FIELD_TABLE) == []


class TestParseFieldRows:
    def test_row_has_all_fields(self):
        blocks = detect_field_tables(FIELD_TABLE)
        lnstate = next(r for r in blocks[0].rows if r.name == "lnState")
        assert lnstate.type == "com:lnState"
        assert lnstate.cardinality == "1..1"
        assert "Код статуса" in lnstate.description

    def test_cardinality_parsed(self):
        blocks = detect_field_tables(FIELD_TABLE)
        snils = next(r for r in blocks[0].rows if r.name == "snils")
        assert snils.cardinality == "1..1"

    def test_field_name_must_be_valid_xml_name(self):
        # кириллица валидна (W3C XML: Товар, Название) — не пропускаются только
        # имена с недопустимыми символами (пробел, спецсимвол) или начинающиеся
        # с цифры/дефиса/точки.
        text = """| Поле | Тип | Длина | Кратность | Описание |
|---|---|---|---|---|
| snils | xs:string | | 1..1 | СНИЛС |
| фамилия | xs:string | | 1..1 | Фамилия (кириллица валидна) |
| name | xs:string | | 1..1 | Имя |
| patronymic | xs:string | | 1..1 | Отчество |
| inn | xs:string | | 1..1 | ИНН |
| birthday | xs:date | | 1..1 | День рождения |
| gender | xs:int | | 1..1 | Пол |
| employer | xs:string | | 0..1 | Работодатель |
"""
        blocks = detect_field_tables(text)
        assert blocks  # найдена таблица (минимум 5 валидных имён)
        names = [r.name for r in blocks[0].rows]
        # кириллица «фамилия» теперь валидна (W3C XML)
        assert "фамилия" in names
        # некорректные имена должны пропускаются — проверим отдельно
        invalid = """| Поле | Тип | Длина | Кратность | Описание |
|---|---|---|---|---|
| snils | xs:string | | 1..1 | СНИЛС |
| 1bad | xs:string | | 1..1 | имя с цифры |
| name | xs:string | | 1..1 | Имя |
| patronymic | xs:string | | 1..1 | Отчество |
| inn | xs:string | | 1..1 | ИНН |
| birthday | xs:date | | 1..1 | День рождения |
| gender | xs:int | | 1..1 | Пол |
| employer | xs:string | | 0..1 | Работодатель |
"""
        blocks_inv = detect_field_tables(invalid)
        if blocks_inv:
            names_inv = [r.name for r in blocks_inv[0].rows]
            assert "1bad" not in names_inv


class TestBuildFieldConcepts:
    def test_creates_concept_per_field(self):
        blocks = detect_field_tables(FIELD_TABLE)
        concepts = build_field_concepts(blocks[0].rows, code="111")
        assert len(concepts) == 5

    def test_lnstate_concept_title_and_content(self):
        blocks = detect_field_tables(FIELD_TABLE)
        concepts = build_field_concepts(blocks[0].rows, code="111")
        lnstate = next(c for c in concepts if "lnState" in c.title)
        assert "lnState" in lnstate.title
        assert "com:lnState" in lnstate.content
        assert "1..1" in lnstate.content
        assert c_type_all_reference(concepts)

    def test_tags_include_code(self):
        blocks = detect_field_tables(FIELD_TABLE)
        concepts = build_field_concepts(blocks[0].rows, code="111")
        assert all("111" in c.tags for c in concepts)


def c_type_all_reference(concepts) -> bool:
    return all(c.type == "reference" for c in concepts)


class TestBuildOverviewConcept:
    def test_overview_has_all_fields(self):
        blocks = detect_field_tables(FIELD_TABLE)
        ov = build_overview_concept(blocks[0].rows, code="111")
        assert ov is not None
        assert "snils" in ov.content
        assert "lnState" in ov.content
        assert "innPerson" in ov.content

    def test_overview_title_has_code(self):
        blocks = detect_field_tables(FIELD_TABLE)
        ov = build_overview_concept(blocks[0].rows, code="111")
        assert "111" in ov.title


class TestExtractFieldTableConcepts:
    def test_returns_concepts_and_remainder(self):
        concepts, rows, remainder = extract_field_table_concepts(FIELD_TABLE, chunk_index=1)
        # 5 полей + 1 обзорный
        assert len(concepts) == 6
        assert len(rows) == 5
        assert "Таблица полей извлечена программно" in remainder
        # в remainder нет строк таблицы (snils | ...)
        assert "p:snils" not in remainder

    def test_lnState_present(self):
        concepts, _rows, _r = extract_field_table_concepts(FIELD_TABLE, chunk_index=1)
        assert any("lnState" in c.title for c in concepts)

    def test_no_table_returns_empty(self):
        concepts, rows, remainder = extract_field_table_concepts("просто текст без таблиц", chunk_index=1)
        assert concepts == []
        assert rows == []
        assert remainder == "просто текст без таблиц"

    def test_non_field_table_passthrough(self):
        # таблица данных не извлекается программно — remainder сохраняет её
        concepts, rows, remainder = extract_field_table_concepts(DATA_TABLE, chunk_index=1)
        assert concepts == []
        assert "| 2024-01-01 |" in remainder


# Таблицы полей с поддержкой кириллицы (W3C XML) и нумерованной первой колонки.

# PascalCase латиница (WSResult, RowsetWrapper) — в реальных СЭДО-схемах.
PASCAL_TABLE = """# Элементы WSResult

| Элемент | Тип | Длина | Кратность | Описание |
|---|---|---|---|---|
| WSResult | com:WSResult | | 1..1 | Корневой элемент ответа |
| RowsetWrapper | com:RowsetWrapper | | 0..1 | Обёртка набора строк |
| Row | com:Row | | 0..n | Строка данных |
| PrParseReestrFileType | com:PrParseReestrFileType | | 0..1 | Тип файла реестра |
| status | xs:string | | 1..1 | Статус обработки |
"""

# Кириллические имена (CommerceML / 1С: <Товар>, <Название>, <Цена>).
CYR_TABLE = """# Структура каталога CommerceML

| Элемент | Тип | Длина | Кратность | Описание |
|---|---|---|---|---|
| Каталог | com:CatalogType | | 1..1 | Корневой каталог |
| Товар | com:ProductType | | 0..n | Товарная позиция |
| Название | xs:string | Тип.Длина: 255 | 1..1 | Наименование товара |
| Цена | com:MoneyType | | 1..1 | Цена товара |
| Валюта | xs:string | Тип.Длина: 3 | 1..1 | Код валюты |
"""

# Нумерованная первая колонка (1, 2, 1.1) — имя во второй колонке.
NUMBERED_TABLE = """# Поля сообщения 111

| № | Имя | Тип | Длина | Кратность | Описание |
|---|---|---|---|---|---|
| 1 | snils | p:snils | | 1..1 | СНИЛС |
| 2 | surname | com:surname | Тип.Длина: 60 | 1..1 | Фамилия |
| 1.1 | lnState | com:lnState | Тип.Длина: 3 | 1..1 | Код статуса ЭЛН |
| 2.1 | lnCode | com:lnCode | | 1..1 | Код больничного |
| 3 | gender | xs:int | | 1..1 | Пол застрахованного |
| 4 | innPerson | p:inn | Тип.Длина: 12 | 0..1 | ИНН застрахованного |
"""


class TestCyrillicAndNumberedTables:
    def test_pascalcase_field_names_detected(self):
        blocks = detect_field_tables(PASCAL_TABLE)
        assert len(blocks) == 1
        names = [r.name for r in blocks[0].rows]
        assert "WSResult" in names
        assert "RowsetWrapper" in names
        assert "PrParseReestrFileType" in names

    def test_cyrillic_field_names_detected(self):
        blocks = detect_field_tables(CYR_TABLE)
        assert len(blocks) == 1
        names = [r.name for r in blocks[0].rows]
        assert "Товар" in names
        assert "Название" in names
        assert "Цена" in names

    def test_cyrillic_concepts_built(self):
        blocks = detect_field_tables(CYR_TABLE)
        concepts = build_field_concepts(blocks[0].rows, code=None)
        assert len(concepts) == 5
        tov = next(c for c in concepts if "Товар" in c.title)
        assert "Товар" in tov.title
        assert "com:ProductType" in tov.content

    def test_numbered_first_column_name_from_second(self):
        blocks = detect_field_tables(NUMBERED_TABLE)
        assert len(blocks) == 1
        names = [r.name for r in blocks[0].rows]
        # имена из второй колонки, не номера
        assert "snils" in names
        assert "lnState" in names
        assert "1" not in names
        assert "1.1" not in names

    def test_numbered_decimal_parsing(self):
        blocks = detect_field_tables(NUMBERED_TABLE)
        lnstate = next(r for r in blocks[0].rows if r.name == "lnState")
        assert lnstate.type == "com:lnState"
        assert lnstate.cardinality == "1..1"
        assert "Код статуса" in lnstate.description

    def test_header_number_column_detected(self):
        # заголовок «№ | Имя | Тип ...» → name_col=1
        from app.services.field_table import _name_col_and_value, _parse_row_cells
        row = _parse_row_cells("| 1 | snils | p:snils | | 1..1 | СНИЛС |")
        col, name = _name_col_and_value(row)
        assert col == 1
        assert name == "snils"


# ---------------------------------------------------------------------------
# LLM-классификатор таблиц-перечней (обобщённый режим)
# ---------------------------------------------------------------------------

# Таблица перечня ситуаций (не XML — обобщённый случай)
SITUATIONS_TABLE = """# Перечень ситуаций обработки

| Код | Описание ситуации | Условие применения |
|---|---|---|
| S01 | Первичная подача | Документ не подавался ранее |
| S02 | Исправление ошибки | Обнаружена ошибка в ранее поданном |
| S03 | Отзыв документа | Инициатива страхователя |
| S04 | Дубликат | Утрата оригинала |
| S05 | Аннулирование | Ошибка регистратора |
"""

# Таблица данных (не перечень)
DATA_TABLE_2 = """| Дата | Сумма | Регион |
|---|---|---|
| 2024-01-01 | 1000 | Юг |
| 2024-02-01 | 2000 | Север |
| 2024-03-01 | 1500 | Восток |
| 2024-04-01 | 3000 | Запад |
| 2024-05-01 | 2500 | Центр |
"""


class FakeClassifierLLM:
    """Mock LLM для тестов классификатора таблиц."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.call_count = 0

    def chat_json(self, system, user, doc_id="unknown", chunk_idx=0, salvage_truncated=False):
        self.call_count += 1
        if not self._responses:
            raise RuntimeError("no more mock responses")
        return self._responses.pop(0)


class TestLLMClassifier:
    def test_llm_classifies_field_table_as_concept_per_row(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: get_settings())
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr(s, "data_dir", tmp_path / "data")
        llm = FakeClassifierLLM([{
            "concept_per_row": True, "title_col": 0,
            "description_cols": [4], "concept_type": "reference",
        }])
        concepts, remainder = extract_table_concepts(
            FIELD_TABLE, chunk_index=1, llm=llm, use_llm_classify=True
        )
        assert len(concepts) >= 6  # 5 полей + обзорный
        assert llm.call_count == 1
        assert "Таблица-перечень извлечена программно" in remainder

    def test_llm_classifies_data_table_as_not_concept_per_row(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: get_settings())
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr(s, "data_dir", tmp_path / "data")
        llm = FakeClassifierLLM([{
            "concept_per_row": False, "title_col": 0,
            "description_cols": [], "concept_type": "note",
        }])
        concepts, remainder = extract_table_concepts(
            DATA_TABLE_2, chunk_index=1, llm=llm, use_llm_classify=True
        )
        assert concepts == []
        # таблица осталась в remainder (не извлечена)
        assert "| 2024-01-01 |" in remainder

    def test_llm_classifies_situations_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: get_settings())
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr(s, "data_dir", tmp_path / "data")
        llm = FakeClassifierLLM([{
            "concept_per_row": True, "title_col": 0,
            "description_cols": [1, 2], "concept_type": "concept",
        }])
        concepts, remainder = extract_table_concepts(
            SITUATIONS_TABLE, chunk_index=1, llm=llm, use_llm_classify=True
        )
        assert len(concepts) >= 6  # 5 ситуаций + обзорный
        titles = [c.title for c in concepts if c.type == "concept" and "Перечень" not in c.title]
        assert "S01" in titles
        assert "S05" in titles

    def test_cache_hit_avoids_llm_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: get_settings())
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr(s, "data_dir", tmp_path / "data")
        # промпт-хеш стабилен в рамках теста (PromptStore отдаёт дефолт)
        llm = FakeClassifierLLM([{
            "concept_per_row": True, "title_col": 0,
            "description_cols": [4], "concept_type": "reference", "extraction_mode": "per_row",
        }])
        extract_table_concepts(FIELD_TABLE, chunk_index=1, llm=llm, use_llm_classify=True)
        assert llm.call_count == 1
        # второй вызов с тем же промптом — LLM не должен зваться (кэш)
        llm2 = FakeClassifierLLM([{
            "concept_per_row": True, "title_col": 0,
            "description_cols": [4], "concept_type": "reference", "extraction_mode": "per_row",
        }])
        extract_table_concepts(FIELD_TABLE, chunk_index=1, llm=llm2, use_llm_classify=True)
        assert llm2.call_count == 0

    def test_cache_miss_on_prompt_change(self, tmp_path, monkeypatch):
        """Смена промпта классификатора → cache-key меняется → cache miss → LLM зовётся."""
        from app.services import field_table
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: get_settings())
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr(s, "data_dir", tmp_path / "data")
        # первый вызов — промпт v1
        monkeypatch.setattr(field_table, "_get_prompt_hash", lambda: "aaa")
        llm1 = FakeClassifierLLM([{
            "concept_per_row": True, "title_col": 0,
            "description_cols": [4], "concept_type": "reference", "extraction_mode": "per_row",
        }])
        extract_table_concepts(FIELD_TABLE, chunk_index=1, llm=llm1, use_llm_classify=True)
        assert llm1.call_count == 1
        # второй вызов — промпт изменился (другой hash)
        monkeypatch.setattr(field_table, "_get_prompt_hash", lambda: "bbb")
        llm2 = FakeClassifierLLM([{
            "concept_per_row": True, "title_col": 0,
            "description_cols": [4], "concept_type": "reference", "extraction_mode": "per_row",
        }])
        extract_table_concepts(FIELD_TABLE, chunk_index=1, llm=llm2, use_llm_classify=True)
        assert llm2.call_count == 1  # cache miss, LLM звался заново

    def test_cache_persisted_to_disk(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: get_settings())
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr(s, "data_dir", tmp_path / "data")
        llm = FakeClassifierLLM([{
            "concept_per_row": True, "title_col": 0,
            "description_cols": [4], "concept_type": "reference",
        }])
        extract_table_concepts(FIELD_TABLE, chunk_index=1, llm=llm, use_llm_classify=True)
        cache_files = list((tmp_path / "data" / "cache" / "table_classify").glob("*.json"))
        assert len(cache_files) == 1

    def test_fallback_on_llm_error(self, tmp_path, monkeypatch):
        """При ошибке LLM — fallback на XML-эвристику."""
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: get_settings())
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr(s, "data_dir", tmp_path / "data")

        class FailingLLM:
            call_count = 0
            def chat_json(self, system, user, doc_id="unknown", chunk_idx=0, salvage_truncated=False):
                self.call_count += 1
                raise RuntimeError("LLM unavailable")

        llm = FailingLLM()
        # FIELD_TABLE — валидная XML-таблица полей, fallback должен сработать
        concepts, remainder = extract_table_concepts(
            FIELD_TABLE, chunk_index=1, llm=llm, use_llm_classify=True
        )
        assert llm.call_count == 1
        # fallback извлёк концепты через эвристику
        assert len(concepts) >= 5
        assert any("lnState" in c.title for c in concepts)

    def test_fallback_when_llm_classify_disabled(self, tmp_path, monkeypatch):
        """use_llm_classify=False → только XML-эвристика, LLM не зовётся."""
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: get_settings())
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr(s, "data_dir", tmp_path / "data")
        llm = FakeClassifierLLM([])
        concepts, remainder = extract_table_concepts(
            FIELD_TABLE, chunk_index=1, llm=llm, use_llm_classify=False
        )
        assert llm.call_count == 0
        assert len(concepts) >= 5

    def test_build_row_concepts_generalized(self, tmp_path, monkeypatch):
        """Обобщённый экстрактор создаёт концепт на каждую строку."""
        from app.services.field_table import RawTableBlock, TableClassification, build_row_concepts
        block = RawTableBlock(
            start=0, end=10,
            header=["Код", "Описание", "Условие"],
            raw_rows=[
                "| S01 | Первичная подача | Не подавался |",
                "| S02 | Исправление | Ошибка |",
                "| S03 | Отзыв | Инициатива |",
            ],
        )
        cls = TableClassification(
            concept_per_row=True, title_col=0,
            description_cols=[1, 2], concept_type="concept",
        )
        concepts = build_row_concepts(block, cls, code=None)
        assert len(concepts) == 4  # 3 строки + обзорный
        s01 = next(c for c in concepts if c.title == "S01")
        assert "Первичная подача" in s01.content
        assert s01.type == "concept"

    def test_whole_mode_creates_one_concept(self, tmp_path, monkeypatch):
        """extraction_mode='whole' → один концепт с content = вся таблица."""
        from app.services.field_table import RawTableBlock, TableClassification, build_row_concepts
        lines = [
            "Текст перед таблицей.",
            "",
            "## Справочник причин нетрудоспособности",
            "",
            "| Значение | Наименование |",
            "|---|---|",
            "| 01 | заболевание |",
            "| 02 | травма |",
            "| 03 | карантии |",
            "| 05 | отпуск по БиР |",
            "| 06 | протезирование |",
        ]
        block = RawTableBlock(start=4, end=11, header=["Значение", "Наименование"], raw_rows=lines[6:11])
        cls = TableClassification(
            concept_per_row=True, title_col=1,
            description_cols=[0], concept_type="reference",
            extraction_mode="whole",
        )
        concepts = build_row_concepts(block, cls, code=None, lines=lines)
        assert len(concepts) == 1
        c = concepts[0]
        assert "Справочник причин нетрудоспособности" in c.title
        # content содержит всю таблицу
        assert "| 01 |" in c.content
        assert "| 06 |" in c.content
        assert c.type == "reference"

    def test_whole_mode_fallback_title_without_heading(self):
        """whole-режим без заголовка над таблицей → title из header[0]."""
        from app.services.field_table import RawTableBlock, TableClassification, build_row_concepts
        block = RawTableBlock(
            start=0, end=5,
            header=["Код", "Описание"],
            raw_rows=["| 01 | первое |", "| 02 | второе |", "| 03 | третье |", "| 04 | четвёртое |", "| 05 | пятое |"],
        )
        cls = TableClassification(
            concept_per_row=True, title_col=0,
            concept_type="reference", extraction_mode="whole",
        )
        concepts = build_row_concepts(block, cls, code=None, lines=None)
        assert len(concepts) == 1
        assert "Код" in concepts[0].title

    def test_per_row_overview_title_from_heading(self):
        """per_row-режим: обзорный title из заголовка над таблицей, не header[0].
        Обзорный содержит все колонки (коды + наименования), не только title_col."""
        from app.services.field_table import RawTableBlock, TableClassification, build_row_concepts
        lines = [
            "## Перечень ситуаций",
            "",
            "| Код | Описание |",
            "|---|---|",
            "| S01 | Первичная |",
            "| S02 | Исправление |",
            "| S03 | Отзыв |",
            "| S04 | Дубликат |",
            "| S05 | Аннулирование |",
        ]
        block = RawTableBlock(start=2, end=9, header=["Код", "Описание"], raw_rows=lines[4:9])
        cls = TableClassification(
            concept_per_row=True, title_col=1,
            concept_type="concept", extraction_mode="per_row",
        )
        concepts = build_row_concepts(block, cls, code=None, lines=lines)
        # 5 строк + обзорный
        assert len(concepts) == 6
        overview = next(c for c in concepts if c.tags and "table-overview" in c.tags)
        assert "Перечень ситуаций" in overview.title
        # обзорный содержит все колонки: коды S01-S05 и описания
        assert "S01" in overview.content
        assert "Первичная" in overview.content
        assert "S05" in overview.content
        assert "Аннулирование" in overview.content

    def test_dedup_skips_duplicate_titles(self, tmp_path, monkeypatch):
        """Дедуп: концепты с одинаковым title из разных таблиц — только первый."""
        monkeypatch.setattr("app.services.field_table.get_settings", lambda: get_settings())
        s = get_settings()
        monkeypatch.setattr(s, "okf_field_table_min_rows", 5)
        monkeypatch.setattr(s, "data_dir", tmp_path / "data")
        # две одинаковые таблицы (как из разных чанков) → LLM вернёт одинаковый title
        llm = FakeClassifierLLM([
            {"concept_per_row": True, "title_col": 0, "description_cols": [], "concept_type": "reference", "extraction_mode": "whole"},
            {"concept_per_row": True, "title_col": 0, "description_cols": [], "concept_type": "reference", "extraction_mode": "whole"},
        ])
        # chunk с двумя одинаковыми таблицами под одинаковым заголовком
        chunk = "## Справочник кодов\n\n" + FIELD_TABLE.strip() + "\n\n## Справочник кодов\n\n" + FIELD_TABLE.strip()
        concepts, _r = extract_table_concepts(chunk, chunk_index=1, llm=llm, use_llm_classify=True)
        # dedup: только 2 концепта (по одному на таблицу, но второй дубликат пропущен)
        whole_concepts = [c for c in concepts if c.tags and "table-whole" in c.tags]
        assert len(whole_concepts) == 1  # второй пропущен дедупом
