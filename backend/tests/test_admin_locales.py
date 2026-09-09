"""API-тесты «Поддержки языков» (Этап 7, фаза A): locales CRUD, активация,
импорт стоп-слов (preview→confirm), история/rollback, права и аудит."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.audit import LOCALE_CREATE, STOPWORDS_IMPORT
from app.services.stopwords import ensure_seeded, invalidate

ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}

ADMIN_USER = {
    "user_id": "sim-admin",
    "username": "demo.admin",
    "email": "a@d.local",
    "groups": ["KB_Admin"],
}

VIEWER_USER = {
    "user_id": "sim-viewer",
    "username": "demo.viewer",
    "email": "v@d.local",
    "groups": ["KB_Viewer"],
}


def make_client(tmp_path, monkeypatch, **overrides) -> TestClient:
    defaults = {
        "_env_file": None,
        "data_dir": tmp_path,
        "auth_provider": "simulation",
        "auth_role_groups": ROLE_GROUPS,
        "auth_default_role": "viewer",
        "auth_sim_users": [ADMIN_USER, VIEWER_USER],
    }
    defaults.update(overrides)
    settings = Settings(**defaults)
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _seed():
    ensure_seeded()
    yield
    invalidate()


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


def login(client, username="demo.admin"):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200, resp.text


class TestActiveLocales:
    def test_returns_seeded_active(self, client):
        login(client)
        resp = client.get("/api/locales")
        assert resp.status_code == 200, resp.text
        codes = {l["code"] for l in resp.json()["locales"]}
        assert {"ru", "en"} <= codes

    def test_etag_304(self, client):
        login(client)
        r1 = client.get("/api/locales")
        etag = r1.headers["etag"]
        r2 = client.get("/api/locales", headers={"if-none-match": etag})
        assert r2.status_code == 304

    def test_304_repeats_validator_headers(self, client):
        """RFC 7232 §4.1: на 304 едут ETag и Cache-Control, что и на 200.

        Раньше 304 собирался отдельным Response, а заголовки ставились на
        инжектированный `response` — и терялись, поэтому следующий запрос
        клиента приходил уже без If-None-Match.
        """
        login(client)
        etag = client.get("/api/locales").headers["etag"]
        r = client.get("/api/locales", headers={"if-none-match": etag})
        assert r.status_code == 304
        assert r.headers["etag"] == etag
        assert r.headers["cache-control"] == "private, max-age=60"

    def test_i18n_304_repeats_validator_headers(self, client):
        login(client)
        assert client.post(
            "/api/admin/locales/en/ui-dictionary/import",
            json={"data": {"nav.documents": "Papers"}, "confirm": True},
        ).status_code == 200
        first = client.get("/api/i18n/en")
        assert first.status_code == 200, first.text
        etag = first.headers["etag"]
        r = client.get("/api/i18n/en", headers={"if-none-match": etag})
        assert r.status_code == 304
        assert r.headers["etag"] == etag
        assert r.headers["cache-control"] == "private, max-age=60"


class TestLocalesCrud:
    def test_admin_list_has_counts(self, client):
        login(client)
        resp = client.get("/api/admin/locales")
        assert resp.status_code == 200, resp.text
        by_code = {l["code"]: l for l in resp.json()["locales"]}
        assert by_code["ru"]["status"] == "active"
        assert by_code["ru"]["stopwords_bm25_count"] > 0
        assert by_code["en"]["stopwords_bm25_count"] > 0

    def test_create_locale_draft(self, client):
        login(client)
        resp = client.post("/api/admin/locales", json={"code": "de", "name": "Deutsch"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "draft"

    def test_create_duplicate_422(self, client):
        login(client)
        assert client.post("/api/admin/locales", json={"code": "ru", "name": "X"}).status_code == 422

    def test_activate_without_stopwords_422(self, client):
        login(client)
        # 08.09.2026: гейта «presence в UI-манифесте» больше нет — активация
        # требует только набор stopwords (kind=bm25).
        client.post("/api/admin/locales", json={"code": "de", "name": "Deutsch"})
        resp = client.post("/api/admin/locales/de/activate")
        assert resp.status_code == 422, resp.text  # нет bm25-стоп-слов
        assert "stopwords" in resp.json()["detail"]

    def test_activate_outside_manifest_succeeds_and_seeds_en_copy(self, client):
        login(client)
        # Язык вне UI-манифеста фронтенда активируется (двухуровневая модель) —
        # автосид кладёт en-копию в историю словарей как заготовку для перевода.
        client.post("/api/admin/locales", json={"code": "fr", "name": "Français"})
        resp = client.post(
            "/api/admin/locales/fr/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["le", "la"], "confirm": True},
        )
        assert resp.status_code == 200, resp.text
        resp = client.post("/api/admin/locales/fr/activate")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"

        # Активный язык появляется в публичном списке.
        active = {l["code"] for l in client.get("/api/locales").json()["locales"]}
        assert "fr" in active

        # Заготовка НЕ активна: активный override заморозил бы английский текст
        # на момент активации и перекрыл бы словарь следующего релиза. Клиент
        # получает штатное «override нет» и берёт словарь релиза.
        assert client.get("/api/i18n/fr").status_code == 404

        # Заготовка видна админу в истории и включается откатом.
        history = client.get("/api/admin/locales/fr/ui-dictionary/history").json()["entries"]
        assert len(history) == 1
        assert history[0]["version"] == 1
        assert "auto: en copy" in (history[0]["note"] or "")
        resp = client.post(
            "/api/admin/locales/fr/ui-dictionary/rollback", json={"entry_id": history[0]["id"]}
        )
        assert resp.status_code == 200, resp.text
        body = client.get("/api/i18n/fr").json()
        assert body["version"] == 1
        assert body["data"]["nav.documents"] == "Documents"

        # Аудит: активация + автосид словаря.
        from app.services.audit import AuditService, UI_DICTIONARY_IMPORT

        entries = AuditService().query(action_type=UI_DICTIONARY_IMPORT)
        assert len(entries) == 1
        assert entries[0]["target_id"] == "fr"
        assert "auto: en copy" in (entries[0]["meta"] or {}).get("note", "")
        assert (entries[0]["meta"] or {}).get("activated") is False

    def test_activation_preserves_existing_dictionary(self, client):
        login(client)
        client.post("/api/admin/locales", json={"code": "fr", "name": "Français"})
        # Свой override импортирован ДО активации — автосид не должен его тронуть.
        resp = client.post(
            "/api/admin/locales/fr/ui-dictionary/import",
            json={
                "data": {"nav.documents": "Documents FR"},
                "note": "custom fr",
                "confirm": True,
            },
        )
        assert resp.status_code == 200, resp.text
        client.post(
            "/api/admin/locales/fr/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["le", "la"], "confirm": True},
        )
        resp = client.post("/api/admin/locales/fr/activate")
        assert resp.status_code == 200, resp.text
        body = client.get("/api/i18n/fr").json()
        assert body["version"] == 1
        assert body["data"]["nav.documents"] == "Documents FR"

    def test_disable_ru_forbidden(self, client):
        login(client)
        assert client.post("/api/admin/locales/ru/disable").status_code == 422

    def test_disable_en(self, client):
        login(client)
        resp = client.post("/api/admin/locales/en/disable")
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"

    def test_create_records_audit(self, client):
        login(client)
        client.post("/api/admin/locales", json={"code": "nl", "name": "Nederlands"})
        from app.services.audit import AuditService

        entries = AuditService().query(action_type=LOCALE_CREATE)
        assert len(entries) == 1
        assert entries[0]["target_id"] == "nl"

    def test_admin_requires_role(self, client):
        login(client, "demo.viewer")
        assert client.get("/api/admin/locales").status_code == 403
        assert client.post("/api/admin/locales", json={"code": "de", "name": "D"}).status_code == 403


class TestUiDictionaryAdmin:
    def test_get_active_empty_returns_null_data(self, client):
        login(client)
        resp = client.get("/api/admin/locales/en/ui-dictionary")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["locale"] == "en"
        assert body["version"] is None and body["data"] is None

    def test_get_active_after_import(self, client):
        login(client)
        resp = client.post(
            "/api/admin/locales/en/ui-dictionary/import",
            json={"data": {"nav.documents": "Papers"}, "confirm": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] is True

        body = client.get("/api/admin/locales/en/ui-dictionary").json()
        assert body["version"] == 1
        assert body["data"] == {"nav.documents": "Papers"}

    def test_get_active_unknown_locale_404(self, client):
        login(client)
        assert client.get("/api/admin/locales/zz/ui-dictionary").status_code == 404

    def test_import_preview_unknown_locale_404(self, client):
        # P0 hardening: preview для несуществующей локали — 404 (не 200 с diff),
        # чтобы ошибки вызывающей стороны не маскировались.
        login(client)
        resp = client.post(
            "/api/admin/locales/zz/ui-dictionary/import",
            json={"data": {"nav.documents": "X"}, "confirm": False},
        )
        assert resp.status_code == 404, resp.text

    def test_history_unknown_locale_404(self, client):
        login(client)
        assert client.get("/api/admin/locales/zz/ui-dictionary/history").status_code == 404

    def test_history_existing_empty_200(self, client):
        login(client)
        resp = client.get("/api/admin/locales/en/ui-dictionary/history")
        assert resp.status_code == 200, resp.text
        assert resp.json()["entries"] == []


class TestStopwordsEmptyReplace:
    def test_empty_replace_requires_explicit_confirmation(self, client):
        login(client)
        # Сначала есть слова (сид ru/bm25).
        before = client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]
        assert len(before) > 0

        # confirm=true БЕЗ confirm_empty_replace — применение запрещено.
        resp = client.post(
            "/api/admin/locales/ru/stopwords/import?mode=replace&kind=bm25",
            json={"words": [], "confirm": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["applied"] is False
        assert body["requires_empty_replace_confirmation"] is True

        after = client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]
        assert len(after) == len(before)  # набор не тронут

    def test_empty_replace_with_confirmation_wipes(self, client):
        login(client)
        resp = client.post(
            "/api/admin/locales/ru/stopwords/import?mode=replace&kind=bm25",
            json={"words": [], "confirm": True, "confirm_empty_replace": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] is True

        after = client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]
        assert after == []

        # Аудит помечает деструктивную очистку.
        from app.services.audit import AuditService

        entries = AuditService().query(action_type=STOPWORDS_IMPORT)
        assert entries and entries[0]["meta"].get("empty_replace") is True
        assert entries[0]["meta"].get("removed_count", 0) > 0

    def test_merge_empty_is_noop(self, client):
        login(client)
        resp = client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": [], "confirm": True},
        )
        assert resp.status_code == 200
        assert resp.json()["applied"] is True
        assert resp.json()["requires_empty_replace_confirmation"] is False


class TestTranslationPending:
    def test_pending_endpoint(self, client):
        login(client)
        resp = client.get("/api/admin/locales/en/stopwords?kind=bm25")
        assert resp.status_code == 200
        # count_pending живёт под /tags/translations/pending (admin).
        pend = client.get(
            "/api/tags/translations/pending", params={"locale": "en", "entities": "tags"}
        )
        assert pend.status_code == 200, pend.text
        assert "tags" in pend.json()["pending"]

    def test_pending_requires_admin(self, client):
        login(client, "demo.viewer")
        assert client.get(
            "/api/tags/translations/pending", params={"locale": "en"}
        ).status_code == 403


class TestStopwordsImport:
    def test_preview_then_confirm(self, client):
        login(client)
        preview = client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["abctest", "defgh"], "confirm": False},
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["applied"] is False
        assert set(body["added"]) == {"abctest", "defgh"}

        applied = client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["abctest", "defgh"], "confirm": True},
        )
        assert applied.status_code == 200
        assert applied.json()["applied"] is True

        words = client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]
        assert {"abctest", "defgh"} <= {w["word"] for w in words}

    def test_replace_removes_absent(self, client):
        login(client)
        client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["keepme", "dropme"], "confirm": True},
        )
        resp = client.post(
            "/api/admin/locales/ru/stopwords/import?mode=replace&kind=bm25",
            json={"words": ["keepme"], "confirm": True},
        )
        # replace — полная замена набора: удаляется всё, чего нет во входящем
        # (включая сид-слова), поэтому проверяем членство, а не точный список.
        assert "dropme" in resp.json()["removed"]
        words = {w["word"] for w in client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]}
        assert "keepme" in words
        assert "dropme" not in words

    def test_import_records_audit(self, client):
        login(client)
        client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["auditword"], "confirm": True},
        )
        from app.services.audit import AuditService

        entries = AuditService().query(action_type=STOPWORDS_IMPORT)
        assert len(entries) == 1
        assert entries[0]["target_id"] == "ru"
        assert entries[0]["new_value"]["kind"] == "bm25"

    def test_history_and_rollback(self, client):
        login(client)
        client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["v1"], "confirm": True},
        )
        client.post(
            "/api/admin/locales/ru/stopwords/import?mode=replace&kind=bm25",
            json={"words": ["v2"], "confirm": True},
        )
        history = client.get("/api/admin/locales/ru/stopwords/history").json()["entries"]
        assert len(history) == 2
        # history[0] — самый свежий (replace на {v2}), его old_value = сид ∪ {v1}.
        latest_entry_id = history[0]["id"]
        assert "v2" in history[0]["words"]

        resp = client.post(
            "/api/admin/locales/ru/stopwords/rollback", json={"entry_id": latest_entry_id}
        )
        assert resp.status_code == 200, resp.text
        words = {w["word"] for w in client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]}
        assert "v1" in words
        assert "v2" not in words

    def test_add_and_delete_word(self, client):
        login(client)
        assert client.post(
            "/api/admin/locales/ru/stopwords", json={"word": "singleword", "kind": "bm25"}
        ).status_code == 200
        words = {w["word"] for w in client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]}
        assert "singleword" in words
        assert client.delete("/api/admin/locales/ru/stopwords/singleword?kind=bm25").status_code == 200
        words = {w["word"] for w in client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]}
        assert "singleword" not in words

    def test_import_german_diacritics_accepted(self, client):
        # Регрессия 08.09.2026: слова с ä/ö/ü/ß (канонический список Snowball DE:
        # daß, für, können, könnte, über, während, würde, würden) отклонялись
        # валидатором «Некорректное слово» — весь импорт падал на первом же слове.
        login(client)
        words = ["daß", "für", "können", "könnte", "über", "während", "würde", "würden"]
        resp = client.post(
            "/api/admin/locales/en/stopwords/import?mode=merge&kind=bm25",
            json={"words": words, "confirm": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] is True
        stored = {w["word"] for w in client.get("/api/admin/locales/en/stopwords?kind=bm25").json()["words"]}
        assert set(words) <= stored

    def test_add_word_uppercase_umlaut_normalized(self, client):
        login(client)
        resp = client.post(
            "/api/admin/locales/en/stopwords", json={"word": "Über", "kind": "bm25"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["word"] == "über"

    def test_delete_word_with_umlaut_url(self, client):
        login(client)
        assert client.post(
            "/api/admin/locales/en/stopwords", json={"word": "über", "kind": "bm25"}
        ).status_code == 200
        resp = client.delete("/api/admin/locales/en/stopwords/über?kind=bm25")
        assert resp.status_code == 200, resp.text
        words = {w["word"] for w in client.get("/api/admin/locales/en/stopwords?kind=bm25").json()["words"]}
        assert "über" not in words

    def test_import_word_with_hyphen_rejected_with_all_words(self, client):
        # Неподдерживаемые символы по-прежнему блокируют импорт, но сообщение
        # перечисляет ВСЕ некорректные слова (не только первое).
        login(client)
        resp = client.post(
            "/api/admin/locales/en/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["gut-besser", "schlechter", "a_b"], "confirm": False},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "gut-besser" in detail
        assert "a_b" in detail

    def test_import_french_accents_accepted(self, client):
        # Регрессия 08.09.2026 (2): европейская латиница — французские акценты
        # (après, allô, ça, élève) больше не блокируют импорт.
        login(client)
        resp = client.post(
            "/api/admin/locales/en/stopwords/import?mode=merge&kind=bm25",
            json={
                "words": ["après", "allô", "ça", "élève", "où", "dès"],
                "confirm": True,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] is True
        stored = {w["word"] for w in client.get("/api/admin/locales/en/stopwords?kind=bm25").json()["words"]}
        assert {"après", "allô", "ça", "élève"} <= stored

    def test_import_french_apostrophe_hyphen_rejected_with_reason(self, client):
        # Апострофные/дефисные слитные формы (aujourd'hui, celle-ci) НЕ могут стать
        # токеном — импорт отклоняет их с объяснением (не «некорректная буква»).
        login(client)
        resp = client.post(
            "/api/admin/locales/en/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["après", "aujourd'hui", "celle-ci"], "confirm": False},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "апостроф/дефис" in detail
        assert "aujourd'hui" in detail
        assert "celle-ci" in detail


class TestErrorCodes:
    """Тело ошибки «Поддержки языков» несёт code — иначе UI покажет русский detail.

    Раздел локализован (friendlyApiError в LanguagesPanel/UiDictionaryEditor),
    но _raise собирал голый HTTPException без кода: клиент не находил ключ и
    откатывался на diagnostic detail с бэкенда.
    """

    def test_unknown_locale_404_carries_code(self, client):
        """Путь через _raise: get_locale -> LocaleNotFoundError -> 404."""
        login(client)
        resp = client.get("/api/admin/locales/zz/ui-dictionary")
        assert resp.status_code == 404
        assert resp.json()["code"] == "locale_not_found"

    def test_history_unknown_locale_carries_code(self, client):
        login(client)
        resp = client.get("/api/admin/locales/zz/ui-dictionary/history")
        assert resp.status_code == 404
        assert resp.json()["code"] == "locale_not_found"

    def test_validation_422_carries_code(self, client):
        login(client)
        resp = client.post("/api/admin/locales", json={"code": "ru", "name": "X"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_request"

    def test_ui_dictionary_unknown_locale_is_locale_code(self, client):
        """Не document_not_found: ui_dictionary бросает ValueError на «языка нет»."""
        login(client)
        resp = client.post(
            "/api/admin/locales/zz/ui-dictionary/import",
            json={"data": {"nav.documents": "X"}, "confirm": False},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "locale_not_found"
