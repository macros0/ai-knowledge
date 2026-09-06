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
        assert ud.validate({"nav.documents": "Docs", "admin.approve": "OK"}) == []

    def test_unknown_key(self):
        errors = ud.validate({"no.such.key": "x"})
        assert any("no.such.key" in e for e in errors)

    def test_param_mismatch(self):
        # admin.approvedBy в ru имеет параметр {name} — перевод без него — ошибка.
        errors = ud.validate({"admin.approvedBy": "approved by"})
        assert any("admin.approvedBy" in e for e in errors)

    def test_non_dict(self):
        assert ud.validate(["a", "b"]) != []


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
