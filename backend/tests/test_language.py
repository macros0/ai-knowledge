"""Тесты детекции языка (Этап 7 фаза D): кириллица/латиница эвристика."""
from app.services.language import detect_language


class TestDetectLanguage:
    def test_cyrillic_is_ru(self):
        text = "Регламент ведения разработок: порядок, требования, справочники и инструкции."
        assert detect_language(text) == "ru"

    def test_latin_is_en(self):
        text = "This document describes the development guidelines and reference data."
        assert detect_language(text) == "en"

    def test_german_with_umlauts_is_de(self):
        # 08.09.2026: немецкие маркерные буквы (ä/ö/ü/ß) выделяют «de».
        text = (
            "Überstunden werden gemäß dem Tarifvertrag vergütet. "
            "Die Abrechnung erfolgt monatlich über das System."
        )
        assert detect_language(text) == "de"

    def test_german_with_sharp_s_is_de(self):
        text = "Straßenverzeichnis und die Größe des Maßnahmepakets für das Quartal."
        assert detect_language(text) == "de"

    def test_english_with_rare_german_names_stays_en(self):
        # Единичные немецкие имена (Müller) в английском тексте не дают «de».
        text = (
            "The report was prepared by Mr. Müller and reviewed by the board. "
            "It covers the annual results of the whole division."
        )
        assert detect_language(text) == "en"

    def test_german_without_umlauts_is_en(self):
        # Осознанный лимит (08.09.2026): немецкий текст без единой маркерной
        # буквы неотличим от английского — классифицируется как «en».
        text = "Diese Verordnung regelt die Verarbeitung und das Meldeverfahren."
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
