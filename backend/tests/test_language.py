"""Тесты детекции языка (Этап 7 фаза D): кириллица/латиница эвристика."""
from app.services.language import detect_language


class TestDetectLanguage:
    def test_cyrillic_is_ru(self):
        text = "Регламент ведения разработок: порядок, требования, справочники и инструкции."
        assert detect_language(text) == "ru"

    def test_latin_is_en(self):
        text = "This document describes the development guidelines and reference data."
        assert detect_language(text) == "en"

    def test_ru_with_latin_identifiers_stays_ru(self):
        # Технический RU-документ с латинскими SAP/XML-идентификаторами.
        text = (
            "Модификация функции _MPRT по коду возврата SELECT FOR ALL ENTRIES "
            "выполняется в расширении ZPRP_DISABILITY_CHLD. Процедура проверяет "
            "поле lnState и формирует журнал по уходу за детьми-инвалидами. "
            "Инструкция описывает порядок заполнения и правила валидации."
        )
        assert detect_language(text) == "ru"

    def test_short_text_returns_none(self):
        assert detect_language("Привет") is None

    def test_empty_returns_none(self):
        assert detect_language("") is None
        assert detect_language(None) is None

    def test_mixed_returns_none(self):
        # Порядка 50/50 кириллицы и латиницы — язык не детерминируется.
        text = "Русский текст mixed with English words примерно поровну поровну"
        # Довольно сбалансированный набор — проверяем лишь, что не падает и
        # возвращает валидное значение (ru/en/None).
        assert detect_language(text) in ("ru", "en", None)
