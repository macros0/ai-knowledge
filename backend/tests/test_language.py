"""Тесты детекции языка (Этап 7 фаза D): офлайн статистическая идентификация.

py3langid (139 языков + zxx) заменила эвристику «кириллица/латиница + маркерные
буквы». Фикстуры длиной >= 2 предложений: короткий текст модель честно помечает
`und` (conf < 0.90) → fallback 'en', поэтому однопредложенческие фразы здесь
недопустимы (валидировано smoke-прогоном).
"""
from unittest import mock

import app.services.language as language
from app.services.language import detect_language


class TestDetectLanguage:
    # --- Позитивные: реальные языки (проза из >= 2 предложений) ---
    def test_cyrillic_is_ru(self):
        text = (
            "Регламент ведения разработок устанавливает порядок, требования и справочники. "
            "Каждый сотрудник обязан следовать инструкциям при оформлении изменений и согласовании документов."
        )
        assert detect_language(text) == "ru"

    def test_ukrainian(self):
        text = (
            "Цей регламент встановлює порядок ведення розробок, вимоги та довідники. "
            "Кожен працівник зобов'язаний дотримуватися інструкцій під час оформлення змін і погодження документів."
        )
        assert detect_language(text) == "uk"

    def test_bulgarian(self):
        text = (
            "Този регламент установява реда за водене на разработките, изискванията и справочниците. "
            "Всеки служител е длъжен да спазва инструкциите при оформянето на промени и съгласуването на документи."
        )
        assert detect_language(text) == "bg"

    def test_serbian_cyrillic(self):
        text = (
            "Овај правилник утврђује поступак вођења развоја, захтеве и референтне податке. "
            "Сваки запослени је дужан да поштује упутства приликом уноса измена и одобравања докумената."
        )
        assert detect_language(text) == "sr"

    def test_german(self):
        text = (
            "Diese Verordnung legt das Verfahren für die Entwicklungsführung, die Anforderungen und die "
            "Referenzdaten fest. Jeder Mitarbeiter muss die Anweisungen bei der Erfassung von Änderungen befolgen."
        )
        assert detect_language(text) == "de"

    def test_german_without_umlauts_is_de(self):
        # Новое качество против эвристики: немецкий БЕЗ умлаутов распознаётся
        # моделью (раньше падал в 'en').
        text = (
            "Diese Verordnung regelt die Verarbeitung personenbezogener Daten und legt die "
            "Meldeverfahren fuer die beteiligten Stellen fest. Jeder Mitarbeiter muss die Anweisungen befolgen."
        )
        assert detect_language(text) == "de"

    def test_french(self):
        text = (
            "Le présent règlement définit les modalités de gestion des développements, les exigences et les "
            "données de référence. Chaque collaborateur doit respecter les instructions lors de la saisie des modifications."
        )
        assert detect_language(text) == "fr"

    def test_spanish(self):
        text = (
            "El presente reglamento define el procedimiento de gestión de los desarrollos, los requisitos y los "
            "datos de referencia. Cada empleado debe respetar las instrucciones al registrar las modificaciones."
        )
        assert detect_language(text) == "es"

    def test_portuguese(self):
        text = (
            "O presente regulamento define o procedimento de gestão dos desenvolvimentos, os requisitos e os "
            "dados de referência. Cada funcionário deve respeitar as instruções ao registar as alterações."
        )
        assert detect_language(text) == "pt"

    def test_italian(self):
        text = (
            "Il presente regolamento definisce le modalità di gestione degli sviluppi, i requisiti e i dati di "
            "riferimento. Ogni dipendente deve rispettare le istruzioni durante la registrazione delle modifiche."
        )
        assert detect_language(text) == "it"

    def test_swedish(self):
        text = (
            "Denna förordning fastställer förfarandet för utvecklingshantering, kraven och referensuppgifterna. "
            "Varje medarbetare måste följa anvisningarna vid registrering av ändringar."
        )
        assert detect_language(text) == "sv"

    def test_finnish(self):
        text = (
            "Tässä asetuksessa vahvistetaan kehitystyön hallinnan menettely, vaatimukset ja viitetiedot. "
            "Jokaisen työntekijän on noudatettava ohjeita muutoksia kirjattaessa."
        )
        assert detect_language(text) == "fi"

    def test_turkish(self):
        text = (
            "Bu yönetmelik, geliştirme yönetimi prosedürünü, gereksinimleri ve referans verilerini belirler. "
            "Her çalışan, değişikliklerin kaydedilmesi sırasında talimatlara uymalıdır."
        )
        assert detect_language(text) == "tr"

    def test_hungarian(self):
        text = (
            "Ez a rendelet meghatározza a fejlesztéskezelés eljárását, a követelményeket és a referencia-adatokat. "
            "Minden munkavállalónak követnie kell az utasításokat a módosítások rögzítésekor."
        )
        assert detect_language(text) == "hu"

    def test_polish(self):
        text = (
            "Niniejsze rozporządzenie określa procedurę zarządzania rozwojem, wymagania i dane referencyjne. "
            "Każdy pracownik musi przestrzegać instrukcji podczas rejestrowania zmian."
        )
        assert detect_language(text) == "pl"

    def test_czech(self):
        text = (
            "Toto nařízení stanoví postup řízení vývoje, požadavky a referenční údaje. "
            "Každý zaměstnanec musí dodržovat pokyny při zaznamenávání změn."
        )
        assert detect_language(text) == "cs"

    def test_romanian(self):
        text = (
            "Prezentul regulament stabilește procedura de gestionare a dezvoltărilor, cerințele și datele de referință. "
            "Fiecare angajat trebuie să respecte instrucțiunile la înregistrarea modificărilor."
        )
        assert detect_language(text) == "ro"

    def test_dutch(self):
        text = (
            "Deze verordening legt de procedure voor het beheer van ontwikkelingen, de vereisten en de "
            "referentiegegevens vast. Elke medewerker moet de instructies bij het vastleggen van wijzigingen volgen."
        )
        assert detect_language(text) == "nl"

    def test_english(self):
        text = (
            "This document describes the development guidelines and the reference data used across the "
            "organization. Every employee must follow these instructions when recording changes."
        )
        assert detect_language(text) == "en"

    # --- Регрессии прежних ложных срабатываний эвристики ---
    def test_portuguese_not_french(self):
        # Раньше ç/ã давали ложное 'fr'.
        text = (
            "O presente regulamento define as modalidades de tratamento de dados pessoais e os procedimentos "
            "de declaração, com ação e coração para a nação."
        )
        assert detect_language(text) == "pt"

    def test_italian_not_french(self):
        # Раньше à/è/é давали ложное 'fr'.
        text = (
            "Il presente regolamento definisce le modalità di trattamento dei dati personali e le procedure "
            "di dichiarazione per la città. Ogni dipendente deve rispettare queste regole."
        )
        assert detect_language(text) == "it"

    def test_swedish_not_german(self):
        # Раньше ä/ö давали ложное 'de'.
        text = (
            "Förordningen fastställer närmare bestämmelser om behandling av personuppgifter och förfaranden "
            "för anmälan. Varje medarbetare ska följa anvisningarna."
        )
        assert detect_language(text) == "sv"

    def test_finnish_not_german(self):
        text = (
            "Asetuksessa vahvistetaan säännöt henkilötietojen käsittelystä ja ilmoitusmenettelyistä. "
            "Jokaisen työntekijän on noudatettava näitä ohjeita."
        )
        assert detect_language(text) == "fi"

    def test_turkish_not_german(self):
        text = (
            "Yönetmelik, kişisel verilerin işlenmesine ilişkin kuralları ve bildirim prosedürlerini belirler. "
            "Her çalışan, çalışma sırasında bu talimatlara uymalıdır."
        )
        assert detect_language(text) == "tr"

    def test_hungarian_not_french(self):
        # Раньше é давало ложное 'fr'.
        text = (
            "A rendelet a személyes adatok kezelésének szabályait és a bejelentési eljárásokat határozza meg. "
            "Minden munkavállalónak követnie kell az utasításokat."
        )
        assert detect_language(text) == "hu"

    # --- RU + англ. SAP-идентификаторы (реальный кейс корпуса) ---
    def test_ru_with_latin_identifiers_stays_ru(self):
        text = (
            "Модификация функции _MPRT по коду возврата SELECT FOR ALL ENTRIES "
            "выполняется в расширении ZPRP_DISABILITY_CHLD. Процедура проверяет "
            "поле lnState и формирует журнал по уходу за детьми-инвалидами. "
            "Инструкция описывает порядок заполнения и правила валидации."
        )
        assert detect_language(text) == "ru"

    # --- Fallback: пустой / короткий / мусор / низкая уверенность ---
    def test_empty_returns_none(self):
        assert detect_language("") is None
        assert detect_language(None) is None

    def test_short_text_returns_en(self):
        # «Почти пустой» ввод — нейтральный fallback, не de/fr.
        assert detect_language("Привет") == "en"

    def test_garbage_identifiers_return_en(self):
        # zxx/und: чистые идентификаторы/числа — «не язык».
        text = "ZPRP_1234 SELECT FOR ALL ENTRIES _MPRT lnState BUKRS 12345 67890"
        assert detect_language(text) == "en"

    def test_scan_markup_without_ocr_returns_en(self):
        # Скан без OCR: текст — markdown-ссылки на изображения. Модель честно
        # возвращает 'und' (нет прозы) → 'en'. Порог под этот случай не подгонялся
        # (валидировано smoke-прогоном); документ несёт problem=no_text_layer.
        text = (
            "![Страница 1 — изображение страницы (скан)](attachments/image-0.jpg) "
            "![Страница 2 — изображение страницы (скан)](attachments/image-1.jpg) "
            "![Страница 3 — изображение страницы (скан)](attachments/image-2.jpg)"
        ) * 8
        assert detect_language(text) == "en"

    def test_deterministic(self):
        text = (
            "Регламент ведения разработок устанавливает порядок и требования. "
            "Каждый сотрудник обязан следовать инструкциям при оформлении изменений."
        )
        assert detect_language(text) == detect_language(text)

    # --- Синглтон: модель создаётся один раз на уровне модуля ---
    def test_identifier_initialized_once_at_module_level(self):
        assert isinstance(language._IDENTIFIER, language.LanguageIdentifier)
        with mock.patch.object(language.LanguageIdentifier, "from_model_file") as spy:
            detect_language(
                "Регламент ведения разработок устанавливает порядок. "
                "Каждый сотрудник обязан следовать инструкциям."
            )
            spy.assert_not_called()
