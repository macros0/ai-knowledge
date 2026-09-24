"""Контракт тела ошибки: {detail, code}.

`code` — стабильный контракт, по которому фронтенд берёт текст из своего
словаря и показывает его на языке интерфейса. Сырой `detail` не выводится в UX;
неизвестные коды получают безопасное сообщение об обращении в поддержку.
"""
import asyncio
import errno
import io
import pathlib
import tempfile

import pytest

from fastapi.testclient import TestClient

from app.api import errors
from app.config import Settings

ROLE_GROUPS = {"KB_Viewer": "viewer", "KB_Admin": "admin"}


def _raise_too_large(*args, **kwargs):
    from app import error_codes as codes
    from app.services.errors import DomainError

    raise DomainError("Файл превышает максимальный размер 1 МБ", code=codes.FILE_TOO_LARGE)


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
            # Загрузка требует editor/admin — нужен для тестов кодов отказа загрузки.
            {"user_id": "u2", "username": "demo.admin", "email": "a@d.local", "groups": ["KB_Admin"]},
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
        """Ни одного HTTPException без кода: иначе ошибка снова станет русской.

        Ищем любое упоминание `HTTPException(`, а не только `raise
        HTTPException(`: admin_locales собирал исключение в хелпере
        (`return HTTPException(...)` + `raise _raise(exc)`) и проходил мимо
        прежней подстроки, отдавая 12 эндпоинтов «Поддержки языков» без кода.

        Единственное законное место — app/api/errors.py: там ApiError
        наследуется от HTTPException и вызывает его конструктор.
        """
        import pathlib

        app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
        allowed = {app_dir / "api" / "errors.py"}
        offenders = [
            str(p.relative_to(app_dir.parent))
            for p in app_dir.rglob("*.py")
            if p not in allowed and "HTTPException(" in p.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"остались непокрытые места: {offenders}"

    def test_no_code_is_declared_but_never_raised(self):
        """Объявлен и переведён, но никем не выдаётся — код мёртв.

        Так жили unsupported_file_type / not_resumable / regenerate_failed /
        unknown_job_type: сервис бросал голый ValueError, а роутер разбирал его
        подстрокой русского сообщения. И так же жили version_conflict /
        duplicate / dependency_unavailable — код уходил на провод литералом
        мимо константы, поэтому переименование константы его бы не задело.
        """
        import re

        app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
        sources = [
            p.read_text(encoding="utf-8")
            for p in app_dir.rglob("*.py")
            if p.name != "error_codes.py"
        ]
        unused = [
            name
            for name, value in vars(errors).items()
            if name.isupper()
            and isinstance(value, str)
            and not any(re.search(rf"\b(?:codes|errors|error_codes)\.{name}\b", src) for src in sources)
        ]
        assert unused == [], (
            f"коды объявлены, но никем не выдаются: {unused} — "
            "выдавайте их из кода или удалите вместе с переводами"
        )

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
    """Каждый код должен иметь перевод для понятного объяснения пользователю."""

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


class TestServiceLayerCodes:
    """Отказы сервисного слоя несут код, а не только русский текст.

    Раньше save_upload_stream бросал два неразличимых ValueError, и роутер
    выбирал между 413 и 400 по подстроке «максимальный размер» в сообщении —
    управляющий поток на тексте диагностики.
    """

    def test_unsupported_extension_has_its_own_code(self):
        import pytest as _pytest

        from app.services.errors import DomainError
        from app.services.pipeline import save_upload_stream

        with _pytest.raises(DomainError) as excinfo:
            save_upload_stream(io.BytesIO(b"x"), "malware.exe")
        assert excinfo.value.code == errors.UNSUPPORTED_FILE_TYPE

    def test_oversized_upload_has_file_too_large_code(self, tmp_path, monkeypatch):
        import pytest as _pytest

        from app.services import pipeline as pipeline_mod
        from app.services.errors import DomainError

        settings = Settings(_env_file=None, data_dir=tmp_path)
        monkeypatch.setattr(pipeline_mod, "get_settings", lambda: settings)

        with _pytest.raises(DomainError) as excinfo:
            pipeline_mod.save_upload_stream(io.BytesIO(b"x" * 64), "big.pdf", max_bytes=8)
        assert excinfo.value.code == errors.FILE_TOO_LARGE

    @pytest.mark.parametrize(
        "make_error",
        [
            lambda: OSError(errno.ENOSPC, "No space left on device"),
            lambda: _windows_disk_full_error(),
        ],
        ids=["linux-enospc", "windows-disk-full"],
    )
    def test_disk_full_during_upload_cleans_partial_file_and_has_stable_code(
        self, tmp_path, monkeypatch, make_error
    ):
        """Removing the storage-full branch must leak a partial upload or the wrong API contract."""
        from app.services import pipeline as pipeline_mod
        from app.services.errors import DomainError

        settings = Settings(_env_file=None, data_dir=tmp_path)
        monkeypatch.setattr(pipeline_mod, "get_settings", lambda: settings)
        original_open = pathlib.Path.open

        class _FullWriter:
            def __init__(self, target):
                self.target = target

            def __enter__(self):
                self.target.__enter__()
                return self

            def __exit__(self, *args):
                return self.target.__exit__(*args)

            def write(self, data):
                self.target.write(data[:1])
                raise make_error()

        def open_with_full_disk(path, mode="r", *args, **kwargs):
            target = original_open(path, mode, *args, **kwargs)
            return _FullWriter(target) if "w" in mode and path.suffix == ".pdf" else target

        monkeypatch.setattr(pipeline_mod.Path, "open", open_with_full_disk)

        with pytest.raises(DomainError) as excinfo:
            pipeline_mod.save_upload_stream(io.BytesIO(b"pdf bytes"), "source.pdf")

        assert excinfo.value.code == "storage_full"
        assert list(settings.uploads_dir.iterdir()) == []

    def test_disk_full_creating_upload_directory_has_stable_code(self, tmp_path, monkeypatch):
        """The upload contract also covers the first write: creating uploads/ itself."""
        from app.services import pipeline as pipeline_mod
        from app.services.errors import DomainError

        settings = Settings(_env_file=None, data_dir=tmp_path)
        monkeypatch.setattr(pipeline_mod, "get_settings", lambda: settings)
        original_mkdir = pathlib.Path.mkdir

        def mkdir_on_full_disk(path, *args, **kwargs):
            if path == settings.uploads_dir:
                raise OSError(errno.ENOSPC, "No space left on device")
            return original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(pipeline_mod.Path, "mkdir", mkdir_on_full_disk)

        with pytest.raises(DomainError) as excinfo:
            pipeline_mod.save_upload_stream(io.BytesIO(b"pdf bytes"), "source.pdf")

        assert excinfo.value.code == "storage_full"

    def test_unknown_job_type_has_its_own_code(self):
        import pytest as _pytest

        from app.services.errors import DomainError
        from app.services.job_queue import JobQueue

        class _User:
            user_id = "u1"
            username = "demo.admin"

        with _pytest.raises(DomainError) as excinfo:
            JobQueue(start_worker=False).submit("no_such_type", ["0123456789abcdef"], _User())
        assert excinfo.value.code == errors.UNKNOWN_JOB_TYPE


class TestUploadStatusFromCode:
    """Статус загрузки выбирается по коду: 413 — размер, 400 — тип файла."""

    def _client_with_login(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/auth/simulate", json={"username": "demo.admin"})
        return client

    def test_unsupported_type_is_400_not_413(self, tmp_path, monkeypatch):
        client = self._client_with_login(tmp_path, monkeypatch)
        resp = client.post(
            "/api/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")}
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == errors.UNSUPPORTED_FILE_TYPE

    def test_oversized_is_413(self, tmp_path, monkeypatch):
        client = self._client_with_login(tmp_path, monkeypatch)
        # Заявленный размер не превышен (file.size маленький) — лимит ловится
        # уже в потоке, то есть ровно тем DomainError, что раньше был ValueError.
        monkeypatch.setattr(
            "app.api.documents.save_upload_stream",
            _raise_too_large,
        )
        resp = client.post(
            "/api/documents", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}
        )
        assert resp.status_code == 413, resp.text
        assert resp.json()["code"] == errors.FILE_TOO_LARGE

    def test_disk_full_is_507(self, tmp_path, monkeypatch):
        from app.services.errors import DomainError

        client = self._client_with_login(tmp_path, monkeypatch)

        def disk_full(*_args, **_kwargs):
            raise DomainError("disk full", code="storage_full")

        monkeypatch.setattr("app.api.documents.save_upload_stream", disk_full)

        resp = client.post(
            "/api/documents", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}
        )

        assert resp.status_code == 507, resp.text
        assert resp.json()["code"] == "storage_full"

    def test_multipart_tempfile_disk_full_is_507(self, tmp_path, monkeypatch):
        """The multipart parser writes before the endpoint; it needs the same contract."""
        client = self._client_with_login(tmp_path, monkeypatch)

        with monkeypatch.context() as context:
            context.setattr(
                tempfile.SpooledTemporaryFile,
                "rollover",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError(errno.ENOSPC, "No space left on device")
                ),
            )
            response = client.post(
                "/api/documents",
                files={"file": ("x.pdf", b"x" * (2 * 1024 * 1024), "application/pdf")},
            )

        assert response.status_code == 507, response.text
        assert response.json()["code"] == errors.STORAGE_FULL


def test_catch_all_returns_507_for_storage_full_database_error():
    """Any API write path must not turn an SQL disk-full into an opaque 500."""
    from app.main import CatchAllErrorsMiddleware

    async def disk_full(_request):
        raise OSError(errno.ENOSPC, "No space left on device")

    response = asyncio.run(CatchAllErrorsMiddleware(None).dispatch(None, disk_full))

    assert response.status_code == 507
    assert b'"code":"storage_full"' in response.body


@pytest.mark.parametrize(
    "attribute,value",
    [("sqlstate", "53100"), ("sqlite_errorcode", 13)],
    ids=["postgres-disk-full", "sqlite-full"],
)
def test_database_storage_full_codes_are_recognized(attribute, value):
    """Removing a database driver's code mapping must not degrade API UX to 500."""
    from app.services.storage import is_storage_full

    error = RuntimeError("database write failed")
    setattr(error, attribute, value)

    assert is_storage_full(error)


def test_provider_quota_message_is_not_a_disk_full_error():
    """A provider's quota must remain a provider failure, not a local disk incident."""
    from app.services.errors import LLMError
    from app.services.storage import is_storage_full

    assert not is_storage_full(LLMError("Provider quota exceeded"))


def test_foreign_error_text_is_not_promoted_to_disk_full():
    """Only a storage adapter may interpret free-form provider text as ENOSPC."""
    from app.services.errors import LLMError
    from app.services.storage import is_storage_full

    error = LLMError("upstream error")
    error.content = b"No space left on device"

    assert not is_storage_full(error)


def test_transient_storage_failure_is_visible_while_database_is_full():
    """The document list must not keep saying 'processing' when its status write failed."""
    from app.services.registry import DocumentRegistry
    from app.services.storage import (
        clear_transient_storage_failure,
        mark_transient_storage_failure,
    )

    doc_id = "abcdeabcdeabcdea"
    registry = DocumentRegistry()
    registry.create(doc_id, "waiting.pdf", "application/pdf", 1)
    mark_transient_storage_failure(doc_id)
    try:
        document = registry.get(doc_id)
        assert document["status"] == "paused"
        assert document["error"] == "Недостаточно свободного места на диске"
        assert document["error_code"] == errors.STORAGE_FULL
    finally:
        clear_transient_storage_failure(doc_id)


def test_manual_resume_invalidates_a_pending_storage_failure_retry():
    """A delayed retry must never overwrite a document that the user resumed."""
    from app.services.storage import (
        clear_transient_storage_failure,
        mark_transient_storage_failure,
        transient_storage_failure_is_active,
    )

    doc_id = "bcdeabcdeabcdeab"
    token = mark_transient_storage_failure(doc_id)
    clear_transient_storage_failure(doc_id)

    assert not transient_storage_failure_is_active(doc_id, token)


def _windows_disk_full_error() -> OSError:
    error = OSError("The disk is full")
    error.winerror = 112
    return error
