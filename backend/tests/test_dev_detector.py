"""Тесты автоопределения номера разработки (regex + LLM + сопоставление)."""
import pytest

from app.services.attribute_registry import AttributeRegistry
from app.services.development_registry import get_development_registry
from app.services.registry import get_registry


class TestFilenameRegex:
    def test_extracts_number(self):
        from app.services.dev_detector import extract_number_from_filename

        assert extract_number_from_filename("12010_СЭДО.docx") == "12010"
        assert extract_number_from_filename("spec_10010.pdf") == "10010"

    def test_no_number(self):
        from app.services.dev_detector import extract_number_from_filename

        assert extract_number_from_filename("просто_документ.docx") is None


class TestMatchReference:
    def test_exact_number_match(self):
        get_development_registry().create("12010", "СЭДО")
        from app.services.dev_detector import match_reference

        dev = match_reference({"number": "12010", "name": None, "module": None})
        assert dev is not None
        assert dev["name"] == "СЭДО"

    def test_fuzzy_name_match(self):
        get_development_registry().create("12010", "Единая система электронного документооборота")
        from app.services.dev_detector import match_reference

        dev = match_reference({"number": "99999", "name": "Единая система электронного документооборот", "module": None})
        assert dev is not None
        assert dev["number"] == "12010"

    def test_no_match(self):
        from app.services.dev_detector import match_reference

        assert match_reference({"number": "00000", "name": "Нечто несвязанное", "module": None}) is None


class TestDetect:
    def test_detect_filename_regex_matched(self):
        dev = get_development_registry().create("12010", "СЭДО")
        from app.services.dev_detector import detect

        d = detect("", "12010_СЭДО.docx")
        assert d.matched is True
        assert d.development_id == dev["id"]
        assert d.confidence == 0.9
        assert d.source == "filename"

    def test_detect_filename_unmatched_sets_suggestion(self):
        from app.services.dev_detector import detect

        d = detect("", "12345_Что-то.docx")
        assert d.matched is False
        assert d.development_id is None
        assert d.suggestion == {"number": "12345", "name": None, "module": None}

    def test_detect_llm_path(self, monkeypatch):
        get_development_registry().create("12010", "СЭДО")

        class FakeLLM:
            def chat_json(self, system, user, doc_id="unknown", chunk_idx=0, salvage_truncated=False):
                return {"dev_number": "12010", "dev_name": "СЭДО", "module": "PY"}

        monkeypatch.setattr("app.services.dev_detector.LLMClient", FakeLLM)
        from app.services.dev_detector import detect

        d = detect("# Титульный лист\nРазработка 12010 СЭДО\n", "spec.docx")
        assert d.matched is True
        assert d.number == "12010"
        assert d.source == "llm"

    def test_llm_invalid_module_not_raising(self, monkeypatch):
        # module вне справочника → не должно быть 422/исключения: кандидат в suggestion.
        class FakeLLM:
            def chat_json(self, *a, **k):
                return {"dev_number": "77777", "dev_name": "Неизвестно", "module": "ZZZ"}

        monkeypatch.setattr("app.services.dev_detector.LLMClient", FakeLLM)
        from app.services.dev_detector import detect

        d = detect("# Титул\nРазработка 77777\n", "spec.docx")
        assert d.matched is False
        assert d.suggestion["module"] == "ZZZ"


class TestAttach:
    def test_attach_sets_fields(self):
        from app.services.dev_detector import Detection, attach_development

        dreg = get_registry()
        dreg.create("0123456789abcdef", "a.docx", "x", 10, tags=["t"])
        attach_development(
            "0123456789abcdef",
            Detection(number="12010", confidence=0.6, matched=False, suggestion={"number": "12010"}),
        )
        doc = dreg.get("0123456789abcdef")
        assert doc["development_id"] is None
        assert doc["development_confidence"] == 0.6
        assert doc["development_suggestion"] == {"number": "12010"}

    def test_attach_matched_sets_id(self):
        from app.services.dev_detector import Detection, attach_development

        dev = get_development_registry().create("12010", "СЭДО")
        dreg = get_registry()
        dreg.create("0123456789abcdef", "a.docx", "x", 10, tags=["t"])
        attach_development(
            "0123456789abcdef",
            Detection(number="12010", development_id=dev["id"], confidence=0.9, matched=True),
        )
        doc = dreg.get("0123456789abcdef")
        assert doc["development_id"] == dev["id"]
        assert doc["development_number"] == "12010"
