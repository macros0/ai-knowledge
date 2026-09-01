"""Тесты справочника разработок и связи документа с разработкой (Этап 4)."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.attribute_registry import AttributeRegistry
from app.services.development_registry import (
    DevelopmentConflictError,
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
        updated = reg.update(dev["id"], dev["version"], name="СЭДО 2.0", module="PY")
        assert updated["name"] == "СЭДО 2.0"
        assert updated["version"] == dev["version"] + 1

    def test_update_invalid_module_rejected(self):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        with pytest.raises(DevelopmentModuleError):
            reg.update(dev["id"], dev["version"], module="WRONG")

    def test_update_stale_version_conflict(self):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        reg.update(dev["id"], dev["version"], name="СЭДО 2.0")
        with pytest.raises(DevelopmentConflictError) as excinfo:
            reg.update(dev["id"], dev["version"], name="Старое имя")
        # Строка не изменена, версия не бампнута.
        assert reg.get(dev["id"])["name"] == "СЭДО 2.0"
        assert excinfo.value.current["version"] == dev["version"] + 1

    def test_update_noop_does_not_bump_version(self):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        updated = reg.update(dev["id"], dev["version"], name="СЭДО")
        assert updated["version"] == dev["version"]

    def test_update_rowcount_detects_lost_update(self, monkeypatch):
        """rowcount-ветка update: TOCTOU между fast-path (s.get) и conditional-UPDATE.

        Fast-path в update не убираем — этот тест детерминированно эмулирует гонку,
        вклинивая сырой bump версии (имитация закоммиченной правки конкурента)
        между чтением версии в fast-path и conditional-UPDATE. Из-за этого
        conditional-UPDATE матчит 0 строк (WHERE version = старая), и срабатывает
        именно rowcount-ветка — то же устранение TOCTOU, что доказано для delete.
        """
        from contextlib import contextmanager

        from sqlalchemy import update as sa_update
        from sqlalchemy.sql.dml import Update

        from app.db.models import Development
        from app.db.session import get_session_factory
        from app.services import development_registry as dr

        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")

        @contextmanager
        def raced_session_scope():
            session = get_session_factory()()
            real_execute = session.execute
            state = {"bumped": False}

            def execute(stmt, *args, **kwargs):
                if (
                    not state["bumped"]
                    and isinstance(stmt, Update)
                    and stmt.table == Development.__table__
                ):
                    state["bumped"] = True
                    # Конкурент «закоммитил» правку между fast-path и UPDATE.
                    real_execute(
                        sa_update(Development)
                        .where(Development.id == dev["id"])
                        .values(version=Development.version + 1)
                    )
                return real_execute(stmt, *args, **kwargs)

            session.execute = execute
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        monkeypatch.setattr(dr, "session_scope", raced_session_scope)

        with pytest.raises(DevelopmentConflictError) as excinfo:
            reg.update(dev["id"], dev["version"], name="СЭДО 2.0")

        # Конфликт отдал актуальную версию конкурента (V+1), полученную re-fetch'ем.
        assert excinfo.value.current["version"] == dev["version"] + 1
        # Транзакция откатилась: запись не изменена, версия не бампнута.
        after = reg.get(dev["id"])
        assert after["name"] == "СЭДО"
        assert after["version"] == dev["version"]

    def test_delete_stale_version_conflict(self):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        reg.update(dev["id"], dev["version"], name="СЭДО 2.0")
        with pytest.raises(DevelopmentConflictError):
            reg.delete(dev["id"], dev["version"])
        assert reg.get(dev["id"]) is not None

    def test_delete_current_version(self):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        assert reg.delete(dev["id"], dev["version"]) is True
        assert reg.get(dev["id"]) is None

    def test_delete_concurrent_claim_conflicts(self):
        """Атомарность «заявки» на удаление: второй delete с той же версией — конфликт.

        Эмулирует гонку «первый delete уже заявил строку (bump версии), но ещё не
        удалил»: версия поднимается сырым conditional-UPDATE тем же способом, что
        делает шаг «заявки» в `delete`. Второй `delete` с исходной версией обязан
        получить `DevelopmentConflictError` по rowcount == 0 — и НЕ должен ни
        отвязать документы, ни удалить строку (иначе это была бы потерянная/двойная
        правка, а не детектированная гонка).
        """
        from sqlalchemy import update as sa_update

        from app.db.models import Development
        from app.db.session import session_scope

        reg = get_development_registry()
        dreg = get_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        dreg.create("0123456789abcdef", "a.docx", "x", 10, tags=["t"])
        dreg.update("0123456789abcdef", development_id=dev["id"])

        # Первый delete «заявил» строку (версия 1 -> 2), строка ещё существует.
        with session_scope() as s:
            s.execute(
                sa_update(Development)
                .where(Development.id == dev["id"], Development.version == dev["version"])
                .values(version=Development.version + 1)
            )

        with pytest.raises(DevelopmentConflictError):
            reg.delete(dev["id"], dev["version"])

        # Строка цела, документ не отвязан (отвязка идёт только после успешной заявки).
        assert reg.get(dev["id"]) is not None
        assert dreg.get("0123456789abcdef")["development_id"] == dev["id"]

    def test_delete_twice_returns_not_found(self):
        """Повторное удаление уже удалённой записи — not found (False), не конфликт."""
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        assert reg.delete(dev["id"], dev["version"]) is True
        assert reg.delete(dev["id"], dev["version"]) is False

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
        dreg.update(
            "0123456789abcdef",
            development_id=dev["id"],
            development_confidence=0.9,
            development_confirmed_by="tester",
            development_suggestion={"number": "12010", "name": "СЭДО", "module": "PY"},
        )

        assert reg.delete(dev["id"], dev["version"]) is True
        doc = dreg.get("0123456789abcdef")
        assert doc["development_id"] is None
        assert doc["development_confidence"] is None
        assert doc["development_confirmed_by"] is None
        assert doc["development_suggestion"] is None

    def test_delete_schedules_dev_tags_clear_for_linked_documents(self, monkeypatch):
        """После удаления разработки для отвязанных документов планируется очистка dev_tags.

        Регрессия: _schedule_sync(dev_id) после удаления не находил ни записи, ни
        документов (они уже отвязаны), поэтому dev_tags в Qdrant оставались
        протухшими. Теперь id документов захватываются ДО отвязки и передаются в
        schedule_dev_tags_sync_many.
        """
        from app.services import dev_sync

        captured: list = []
        monkeypatch.setattr(dev_sync, "schedule_dev_tags_sync_many", lambda ids: captured.append(ids))

        reg = get_development_registry()
        dreg = get_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        dreg.create("0123456789abcdef", "a.docx", "x", 10, tags=["t"])
        dreg.update("0123456789abcdef", development_id=dev["id"])

        assert reg.delete(dev["id"], dev["version"]) is True
        assert captured == [["0123456789abcdef"]]


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


def _make_client(monkeypatch) -> TestClient:
    """Клиент с отключённой авторизацией (как make_client в test_audit.py)."""
    from app.config import Settings

    settings = Settings(_env_file=None, auth_provider="disabled")
    for mod in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: settings)
    return TestClient(create_app())


class TestDevelopmentConflictApi:
    """409 с структурированным code: version_conflict / duplicate_number."""

    def test_patch_version_conflict_returns_code(self, monkeypatch):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        reg.update(dev["id"], dev["version"], name="СЭДО 2.0")

        client = _make_client(monkeypatch)
        resp = client.patch(
            f"/api/developments/{dev['id']}",
            json={"name": "Старое имя", "version": dev["version"]},
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["code"] == "version_conflict"
        assert body["current"]["version"] == dev["version"] + 1
        assert body["current"]["name"] == "СЭДО 2.0"
        # Запись не изменена повторной (устаревшей) правкой.
        assert reg.get(dev["id"])["name"] == "СЭДО 2.0"

    def test_patch_duplicate_number_returns_code(self, monkeypatch):
        reg = get_development_registry()
        dev_a = reg.create("12010", "A", module="PY")
        dev_b = reg.create("12020", "B", module="PY")

        client = _make_client(monkeypatch)
        resp = client.patch(
            f"/api/developments/{dev_b['id']}",
            json={"number": dev_a["number"], "version": dev_b["version"]},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "duplicate_number"

    def test_delete_version_conflict_returns_code(self, monkeypatch):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")
        reg.update(dev["id"], dev["version"], name="СЭДО 2.0")

        client = _make_client(monkeypatch)
        resp = client.delete(f"/api/developments/{dev['id']}?version={dev['version']}")
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "version_conflict"
        assert reg.get(dev["id"]) is not None

    def test_delete_success_with_current_version(self, monkeypatch):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")

        client = _make_client(monkeypatch)
        resp = client.delete(f"/api/developments/{dev['id']}?version={dev['version']}")
        assert resp.status_code == 200, resp.text
        assert reg.get(dev["id"]) is None

    def test_patch_success_bumps_version(self, monkeypatch):
        reg = get_development_registry()
        dev = reg.create("12010", "СЭДО", module="PY")

        client = _make_client(monkeypatch)
        resp = client.patch(
            f"/api/developments/{dev['id']}",
            json={"name": "СЭДО 2.0", "version": dev["version"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["version"] == dev["version"] + 1
