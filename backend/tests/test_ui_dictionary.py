"""Тесты runtime-override UI-словарей (Этап 7 фаза C): валидация ключей/параметров,
импорт preview→confirm, версии, rollback, GET /api/i18n."""
import pytest

from app.services import ui_dictionary as ud
from app.services.stopwords import ensure_seeded, invalidate


class _User:
    user_id = "u-admin"
    username = "demo.admin"


@pytest.fixture(autouse=True)
def _seed():
    ensure_seeded()
    yield
    invalidate()


class TestValidate:
    def test_valid_subset(self):
        assert ud.validate("en", {"nav.documents": "Docs", "admin.approve": "OK"}) == []

    def test_unknown_key(self):
        errors = ud.validate("en", {"no.such.key": "x"})
        assert any("no.such.key" in e for e in errors)

    def test_param_mismatch(self):
        # admin.approvedBy в ru имеет параметр {name} — перевод без него — ошибка.
        errors = ud.validate("en", {"admin.approvedBy": "approved by"})
        assert any("admin.approvedBy" in e for e in errors)

    def test_non_dict(self):
        assert ud.validate("en", ["a", "b"]) != []


class TestValidatePlural:
    def test_en_plural_with_ru_forms_rejected(self):
        errors = ud.validate(
            "en",
            {"docs.tagsUpdated": {"one": "x", "few": "x", "many": "x"}},
        )
        assert any("few, many" in e and "en" in e and "one, other" in e for e in errors)

    def test_en_plural_one_other_valid(self):
        assert ud.validate(
            "en", {"docs.tagsUpdated": {"one": "{count} document", "other": "{count} documents"}}
        ) == []

    def test_en_missing_other_rejected(self):
        errors = ud.validate("en", {"docs.tagsUpdated": {"one": "x"}})
        assert any("other" in e for e in errors)

    def test_string_value_allowed_for_plural_key(self):
        # en.js часто представляет plural-ключ одной строкой — валидно.
        assert ud.validate("en", {"docs.tagsUpdated": "Tags updated for {count} doc(s)"}) == []

    def test_ru_plural_one_few_many_valid(self):
        assert ud.validate(
            "ru",
            {"docs.tagsUpdated": {"one": "1", "few": "2", "many": "5"}},
        ) == []

    def test_per_form_param_mismatch(self):
        errors = ud.validate(
            "en",
            {"docs.tagsUpdated": {"one": "{count} {x}", "other": "{count} documents"}},
        )
        assert any("docs.tagsUpdated" in e and "one" in e for e in errors)

    def test_non_string_form_rejected(self):
        errors = ud.validate("en", {"docs.tagsUpdated": {"one": "x", "other": 5}})
        assert any("строкой" in e for e in errors)

    def test_unknown_locale_falls_back_to_en_rule(self):
        # Неизвестная локаль (de) → en-модель one/other: few/many отклоняются.
        errors = ud.validate("de", {"docs.tagsUpdated": {"one": "x", "few": "x"}})
        assert any("few" in e for e in errors)


class TestImport:
    def test_preview_then_confirm(self):
        r = ud.import_dictionary("en", {"nav.documents": "Docs"}, "note", "admin")
        assert r["applied"] is False
        assert r["preview"]["total"] == 1

        r = ud.import_dictionary(
            "en", {"nav.documents": "Docs"}, "note", "admin", confirm=True, user=_User()
        )
        assert r["applied"] is True
        assert r["version"] == 1

        active = ud.get_active("en")
        assert active["data"] == {"nav.documents": "Docs"}

    def test_second_import_bumps_version(self):
        ud.import_dictionary("en", {"nav.documents": "Docs"}, None, "a", confirm=True)
        r = ud.import_dictionary("en", {"nav.chat": "Chat"}, None, "a", confirm=True)
        assert r["version"] == 2

    def test_invalid_import_rejected(self):
        r = ud.import_dictionary("en", {"no.such.key": "x"}, None, "a", confirm=True)
        assert r["applied"] is False
        assert r["errors"]

    def test_history_and_rollback(self):
        ud.import_dictionary("en", {"nav.documents": "Docs V1"}, None, "a", confirm=True)
        ud.import_dictionary("en", {"nav.documents": "Docs V2"}, None, "a", confirm=True)
        hist = ud.history("en")
        assert len(hist) == 2
        # Откат к v1 (самый старый).
        v1 = hist[-1]
        res = ud.rollback("en", v1["id"], user=_User())
        assert res["version"] == 1
        assert ud.get_active("en")["data"]["nav.documents"] == "Docs V1"


class TestSeedEnglishCopy:
    @staticmethod
    def _create_locale(code: str) -> None:
        from app.db.models import Locale
        from app.db.session import session_scope

        with session_scope() as s:
            s.add(Locale(code=code, name=code, status="draft"))

    def test_seeds_full_en_dictionary_when_empty(self):
        # de в манифесте фронта, но без runtime-словаря — автосид создаёт v1.
        self._create_locale("de")
        assert ud.seed_english_copy("de", user=_User()) is True
        active = ud.get_active("de")
        assert active is not None
        assert active["version"] == 1
        assert active["data"]["nav.documents"] == "Documents"

    def test_noop_when_override_exists(self):
        self._create_locale("fr")
        ud.import_dictionary("fr", {"nav.documents": "Docs FR"}, "custom", "a", confirm=True)
        assert ud.seed_english_copy("fr", user=_User()) is False
        active = ud.get_active("fr")
        assert active["data"] == {"nav.documents": "Docs FR"}
        assert active["version"] == 1  # автосид не сдвинул версию

    def test_skips_ru_and_en(self):
        assert ud.seed_english_copy("ru", user=_User()) is False
        assert ud.seed_english_copy("en", user=_User()) is False
        assert ud.get_active("ru") is None
        assert ud.get_active("en") is None

    def test_missing_locale_returns_false(self):
        # Локали нет вообще — сид не сеет и не падает.
        assert ud.seed_english_copy("de", user=_User()) is False
