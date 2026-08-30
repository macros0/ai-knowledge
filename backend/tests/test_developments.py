"""Тесты справочника разработок и связи документа с разработкой (Этап 4)."""
import pytest

from app.services.attribute_registry import AttributeRegistry
from app.services.development_registry import (
    DevelopmentModuleError,
    DevelopmentNumberExistsError,
    get_development_registry,
)
from app.services.registry import get_registry


@pytest.fixture(autouse=True)
def _seed_module():
    AttributeRegistry().add("module", "PY")


class TestDevelopmentRegistry:
    def test_create_and_get(self):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY", created_by="demo.editor")
        assert dev["number"] == "12010"
        assert dev["name"] == "СЭДО"
        assert dev["module"] == "PY"
        assert reg.get(dev["id"])["documents_count"] == 0

    def test_number_unique(self):
        reg = get_development_registry()
        reg.create("12010", "A", module="PY")
        with pytest.raises(DevelopmentNumberExistsError):
            reg.create("12010", "B", module="PY")

    def test_find_by_number(self):
        reg = get_development_registry()
        reg.create("12010", "СЭДО", module="PY")
        assert reg.find_by_number("12010")["name"] == "СЭДО"
        assert reg.find_by_number("99999") is None

    def test_update_rename_and_module(self):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        updated = reg.update(dev["id"], name="СЭДО 2.0", module="PY")
        assert updated["name"] == "СЭДО 2.0"

    def test_update_invalid_module_rejected(self):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        with pytest.raises(DevelopmentModuleError):
            reg.update(dev["id"], module="WRONG")

    def test_dev_tags(self):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        assert reg.dev_tags(dev["id"]) == ["12010", "СЭДО", "PY"]

    def test_dev_tags_without_module(self):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО")
        assert reg.dev_tags(dev["id"]) == ["12010", "СЭДО"]

    def test_document_link_and_count(self):
        reg = get_development_registry()
        dreg = get_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        dreg.create("0123456789abcdef", "a.docx", "x", 10, tags=["t"])
        dreg.update("0123456789abcdef", development_id=dev["id"])

        assert reg.get(dev["id"])["documents_count"] == 1
        docs = reg.documents_for(dev["id"])
        assert len(docs) == 1
        assert docs[0]["development_number"] == "12010"
        assert docs[0]["development_name"] == "СЭДО"

    def test_delete_unlinks_documents(self):
        reg = get_development_registry()
        dreg = get_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        dreg.create("0123456789abcdef", "a.docx", "x", 10, tags=["t"])
        dreg.update("0123456789abcdef", development_id=dev["id"], development_confidence=0.9)

        assert reg.delete(dev["id"]) is True
        doc = dreg.get("0123456789abcdef")
        assert doc["development_id"] is None
        assert doc["development_confidence"] is None


class TestDevelopmentQuery:
    def _seed(self):
        attr = AttributeRegistry()
        attr.add("module", "PY")
        attr.add("module", "PT")
        reg = get_development_registry()
        reg.create("12010", "СЭДО", module="PY")
        reg.create("12020", "ЭЛН", module="PT")
        reg.create("13000", "Инструкция", module=None)
        return reg

    def test_search_by_number_prefix(self):
        reg = self._seed()
        items, total = reg.query(search="120")
        assert total == 2
        assert {d["number"] for d in items} == {"12010", "12020"}

    def test_search_by_name_substring(self):
        reg = self._seed()
        # «струкц» — подстрока в середине названия (не префикс). SQLite lower()
        # не сворачивает кириллицу, поэтому берём регистр как в данных; в Postgres
        # сработает ILIKE без учёта регистра.
        items, total = reg.query(search="струкц")
        assert total == 1
        assert items[0]["name"] == "Инструкция"

    def test_module_filter_value(self):
        reg = self._seed()
        items, total = reg.query(module="PY")
        assert total == 1
        assert items[0]["number"] == "12010"

    def test_module_filter_none(self):
        reg = self._seed()
        from app.services.development_registry import MODULE_NONE

        items, total = reg.query(module=MODULE_NONE)
        assert total == 1
        assert items[0]["name"] == "Инструкция"

    def test_sort_by_documents_count(self):
        reg = self._seed()
        dreg = get_registry()
        # 2 документа у "12020", 1 у "12010", 0 у "13000".
        dreg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=[])
        dreg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10, tags=[])
        dev_12020 = reg.find_by_number("12020")
        dev_12010 = reg.find_by_number("12010")
        dreg.update("aaaaaaaaaaaaaaaa", development_id=dev_12020["id"])
        dreg.update("bbbbbbbbbbbbbbbb", development_id=dev_12020["id"])
        dreg.update("aaaaaaaaaaaaaaaa", development_id=dev_12020["id"])  # idempotent
        dreg.create("cccccccccccccccc", "c.docx", "x", 10, tags=[])
        dreg.update("cccccccccccccccc", development_id=dev_12010["id"])

        items, _ = reg.query(sort_by="documents_count", order="desc")
        assert [d["number"] for d in items] == ["12020", "12010", "13000"]

        items, _ = reg.query(sort_by="documents_count", order="asc")
        assert items[0]["number"] == "13000"

    def test_pagination(self):
        reg = self._seed()
        for i in range(5):
            reg.create(f"200{i}", f"Dev {i}", module="PY")

        items, total = reg.query(limit=3, offset=0)
        assert total == 8
        assert len(items) == 3

        page2, _ = reg.query(limit=3, offset=3)
        assert len(page2) == 3
        assert {d["id"] for d in items} & {d["id"] for d in page2} == set()

    def test_total_matches_filtered_count(self):
        reg = self._seed()
        for i in range(5):
            reg.create(f"220{i}", f"Фильтр {i}", module="PY")
        items, total = reg.query(search="220", limit=2, offset=0)
        assert total == 5
        assert len(items) == 2
