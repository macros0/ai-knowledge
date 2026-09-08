"""Контракт тела ошибки: {detail, code}.

`code` — стабильный контракт, по которому фронтенд берёт текст из своего
словаря и показывает его на языке интерфейса. `detail` остаётся русской
диагностикой: клиент показывает её только как фолбэк.
"""
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.api import errors
from app.config import Settings

ROLE_GROUPS = {"KB_Viewer": "viewer", "KB_Admin": "admin"}


def _client(tmp_path, monkeypatch) -> TestClient:
    from app.main import create_app

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        auth_provider="simulation",
        auth_role_groups=ROLE_GROUPS,
        auth_default_role="viewer",
        auth_sim_users=[
            {"user_id": "u1", "username": "demo.user", "email": "u@d.local", "groups": ["KB_Viewer"]},
        ],
    )
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return TestClient(create_app())


class TestErrorBody:
    def test_not_found_carries_stable_code(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/auth/simulate", json={"username": "demo.user"})

        resp = client.get("/api/documents/ffffffffffffffff")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == errors.DOCUMENT_NOT_FOUND
        # detail остаётся диагностикой — тесты и логи на неё опираются.
        assert body["detail"] == "Документ не найден"

    def test_auth_required_carries_code(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)  # без входа

        resp = client.get("/api/documents")

        assert resp.status_code == 401
        assert resp.json()["code"] == errors.AUTH_REQUIRED


class TestCodeCoverage:
    def test_every_raise_site_has_a_code(self):
        """Ни одного HTTPException без кода: иначе ошибка снова станет русской."""
        import pathlib

        app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
        offenders = [
            str(p.relative_to(app_dir.parent))
            for p in app_dir.rglob("*.py")
            if "raise HTTPException(" in p.read_text()
        ]
        assert offenders == [], f"остались непокрытые места: {offenders}"

    def test_codes_are_unique_and_snake_case(self):
        import re

        values = [
            v for k, v in vars(errors).items()
            if k.isupper() and isinstance(v, str)
        ]
        assert len(values) == len(set(values)), "дубли кодов ошибок"
        for v in values:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", v), f"код не snake_case: {v}"


class TestCodeTranslations:
    """Каждый код должен иметь перевод — иначе пользователь снова увидит detail."""

    def _ru_keys(self) -> set[str]:
        import re

        ru = (
            pathlib.Path(__file__).resolve().parents[2]
            / "frontend/src/i18n/locales/ru.js"
        ).read_text(encoding="utf-8")
        return set(re.findall(r'"(apiError\.[a-z0-9_.]+)"', ru))

    def test_every_code_has_a_dictionary_key(self):
        keys = self._ru_keys()
        missing = [
            v
            for k, v in vars(errors).items()
            if k.isupper() and isinstance(v, str) and f"apiError.{v}" not in keys
        ]
        assert missing == [], (
            f"нет ключа apiError.<code> для: {missing} — "
            "добавьте в frontend/src/i18n/locales/{ru,en}.js"
        )


class TestDomainErrorCodes:
    """Доменные ошибки сервисов несут свой код, а не общий по HTTP-статусу."""

    def test_service_error_keeps_its_code(self):
        from app.services.errors import ConflictError, DomainError, NotFoundError

        assert issubclass(DomainError, ValueError), (
            "существующие except ValueError должны продолжать ловить доменные ошибки"
        )
        assert NotFoundError("нет").code == errors.DOCUMENT_NOT_FOUND
        assert ConflictError("конфликт").code == errors.CONFLICT
        assert DomainError("x", code=errors.TAG_IN_USE).code == errors.TAG_IN_USE

    def test_status_stays_with_the_router(self):
        """У статуса один источник истины — роутер, а не класс исключения."""
        from app.services.errors import ConflictError

        assert not hasattr(ConflictError("x"), "status_code")

    def test_domain_error_response_uses_exception_code(self):
        from app.api.errors import domain_error
        from app.services.errors import ConflictError

        api_exc = domain_error(ConflictError("Задача не ожидает одобрения",
                                             code=errors.JOB_NOT_AWAITING_APPROVAL), 400)
        assert api_exc.status_code == 400
        assert api_exc.code == errors.JOB_NOT_AWAITING_APPROVAL
        assert api_exc.detail == "Задача не ожидает одобрения"
