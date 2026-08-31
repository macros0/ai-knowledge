"""Тесты правки глобальных тегов документа (Этап 4a): сервис + API."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import document_tag_service as dts
from app.services.attribute_registry import AttributeRegistry
from app.services.audit import (
    DOCUMENT_BULK_TAGS_UPDATE,
    DOCUMENT_TAGS_UPDATE,
    AuditService,
)
from app.services.development_registry import get_development_registry
from app.services.registry import get_registry
from app.services.tag_registry import TagRegistry
from app.services.vector_store import VectorStore

DOC_ID = "0123456789abcdef"


class _User:
    user_id = "u-editor"
    username = "demo.editor"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from app.config import Settings as S

    s = S(_env_file=None, data_dir=tmp_path)
    monkeypatch.setattr(dts, "get_settings", lambda: s)
    return s


@pytest.fixture(autouse=True)
def _no_background_sync(monkeypatch):
    """Не плодить фоновые потоки синка тегов в тестах.

    Фоновый поток доживал бы до следующего теста и читал бы его свежую SQLite-БД
    (где таблиц ещё нет). В тестах синк Qdrant выполняется синхронно.
    """
    monkeypatch.setattr(dts, "schedule_document_tags_sync", dts._sync_qdrant_tags)


@pytest.fixture
def qdrant_ok(monkeypatch):
    monkeypatch.setattr(VectorStore, "set_document_tags_payload", lambda *a, **k: None)
    monkeypatch.setattr(dts, "reindex_document_dev_tags", lambda *a, **k: True)


class TestUpdateDocumentTags:
    def test_replaces_tags_and_registers_names(self, settings, qdrant_ok):
        # Регистрация начальных тегов — как при upload (upload вызывает TagRegistry.add).
        TagRegistry().add(["proxmox", "network"])
        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=["proxmox", "network"])

        result = dts.update_document_tags(DOC_ID, ["vlan", "proxmox"], _User())

        assert result["changed"] is True
        assert set(reg.get(DOC_ID)["tags"]) == {"vlan", "proxmox"}
        names = {t["name"] for t in TagRegistry().all()}
        assert {"proxmox", "network", "vlan"} <= names

    def test_unchanged_set_is_noop_without_audit(self, settings, qdrant_ok):
        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=["proxmox"])

        result = dts.update_document_tags(DOC_ID, ["proxmox"], _User())

        assert result["changed"] is False
        assert AuditService().query(action_type=DOCUMENT_TAGS_UPDATE) == []

    def test_missing_document_raises(self, settings, qdrant_ok):
        with pytest.raises(ValueError):
            dts.update_document_tags(DOC_ID, ["a"], _User())

    def test_normalizes_input(self, settings, qdrant_ok):
        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=[])
        dts.update_document_tags(DOC_ID, ["  proxmox ", "", "proxmox"], _User())
        assert reg.get(DOC_ID)["tags"] == ["proxmox"]

    def test_writes_audit_entry(self, settings, qdrant_ok):
        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=["proxmox"])

        dts.update_document_tags(DOC_ID, ["network"], _User(), ip_address="127.0.0.1")

        entries = AuditService().query(action_type=DOCUMENT_TAGS_UPDATE)
        assert len(entries) == 1
        e = entries[0]
        assert e["target_id"] == DOC_ID
        assert e["old_value"] == {"tags": ["proxmox"]}
        assert e["new_value"] == {"tags": ["network"]}
        assert e["username"] == "demo.editor"
        assert e["ip_address"] == "127.0.0.1"

    def test_updates_concept_tags_and_bundle_frontmatter(self, settings, qdrant_ok):
        import yaml

        from app.db.models import OkfConcept
        from app.db.session import session_scope

        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=["proxmox", "llm-user"])
        with session_scope() as s:
            # Консистентно с реальной генерацией: concept.tags = LLM-теги + user-теги.
            s.add(OkfConcept(doc_id=DOC_ID, slug="k1", tags=["llm-tag", "proxmox", "llm-user"]))

        bundle = settings.okf_dir / DOC_ID
        bundle.mkdir(parents=True)
        md = bundle / "k1.md"
        md.write_text(
            "---\ntype: concept\ntitle: K1\ntags: [llm-tag, proxmox, llm-user]\nglobal_tags: [proxmox, llm-user]\n---\n\n# K1\nbody\n",
            encoding="utf-8",
        )

        # Убираем proxmox+llm-user, добавляем vlan.
        dts.update_document_tags(DOC_ID, ["vlan"], _User())

        with session_scope() as s:
            concept = s.query(OkfConcept).filter(OkfConcept.doc_id == DOC_ID).one()
            assert set(concept.tags) == {"llm-tag", "vlan"}
        text = md.read_text(encoding="utf-8")
        meta = yaml.safe_load(text.split("---", 2)[1])
        assert meta["tags"] == ["llm-tag", "vlan"]
        assert meta["global_tags"] == ["vlan"]
        assert "# K1\nbody\n" in text

    def test_qdrant_down_is_best_effort(self, settings, monkeypatch):
        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=["proxmox"])

        def boom(*a, **k):
            raise RuntimeError("qdrant down")

        monkeypatch.setattr(VectorStore, "set_document_tags_payload", boom)

        result = dts.update_document_tags(DOC_ID, ["network"], _User())
        assert result["changed"] is True
        assert reg.get(DOC_ID)["tags"] == ["network"]

    def test_dev_number_tag_links_development(self, settings, qdrant_ok):
        AttributeRegistry().add("module", "PY")
        dev = get_development_registry().create("12010", "СЭДО", module="PY")
        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=["proxmox"])

        result = dts.update_document_tags(DOC_ID, ["proxmox", "12010"], _User())

        doc = reg.get(DOC_ID)
        assert doc["development_id"] == dev["id"]
        assert doc["development_confidence"] == 1.0
        assert doc["development_confirmed_by"] == "demo.editor"
        assert result["dev_tags_sync_pending"] is False

    def test_removing_dev_number_tag_unlinks(self, settings, qdrant_ok):
        AttributeRegistry().add("module", "PY")
        dev = get_development_registry().create("12010", "СЭДО", module="PY")
        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=["12010"])
        reg.update(
            DOC_ID,
            development_id=dev["id"],
            development_confidence=1.0,
            development_confirmed_by="demo.editor",
        )

        dts.update_document_tags(DOC_ID, ["network"], _User())

        doc = reg.get(DOC_ID)
        assert doc["development_id"] is None
        assert doc["development_confidence"] is None

    def test_dev_sync_fallback_flags_pending(self, settings, monkeypatch):
        AttributeRegistry().add("module", "PY")
        get_development_registry().create("12010", "СЭДО", module="PY")
        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=[])

        monkeypatch.setattr(VectorStore, "set_document_tags_payload", lambda *a, **k: None)
        monkeypatch.setattr(dts, "reindex_document_dev_tags", lambda *a, **k: False)
        schedule = []
        monkeypatch.setattr(dts, "schedule_document_dev_tags_sync", lambda d: schedule.append(d))

        result = dts.update_document_tags(DOC_ID, ["12010"], _User())

        assert result["dev_tags_sync_pending"] is True
        assert schedule == [DOC_ID]


class TestBulkUpdateTags:
    def test_add_remove_delta(self, settings, qdrant_ok):
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["proxmox"])
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10, tags=["network"])

        result = dts.bulk_update_tags(
            ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"],
            add=["vlan"],
            remove=["proxmox"],
            user=_User(),
        )

        assert result["updated"] == ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"]
        assert reg.get("aaaaaaaaaaaaaaaa")["tags"] == ["vlan"]
        assert reg.get("bbbbbbbbbbbbbbbb")["tags"] == ["network", "vlan"]

    def test_unchanged_docs_not_updated(self, settings, qdrant_ok):
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["vlan"])

        result = dts.bulk_update_tags(
            ["aaaaaaaaaaaaaaaa"], add=["vlan"], remove=[], user=_User()
        )
        assert result["updated"] == []
        assert result["unchanged"] == ["aaaaaaaaaaaaaaaa"]

    def test_per_document_audit_with_bulk_action_type(self, settings, qdrant_ok):
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["proxmox"])
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10, tags=["network"])

        dts.bulk_update_tags(
            ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"],
            add=["vlan"],
            remove=[],
            user=_User(),
            ip_address="10.0.0.1",
        )

        entries = AuditService().query(action_type=DOCUMENT_BULK_TAGS_UPDATE)
        assert len(entries) == 2
        assert {e["target_id"] for e in entries} == {"aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"}
        assert all(e["meta"]["bulk"] is True for e in entries)
        assert all(e["meta"]["add"] == ["vlan"] for e in entries)


class TestQdrantSync:
    def test_sync_reads_db_and_derives_concept_points(self, settings, monkeypatch):
        import uuid as uuid_mod

        from app.db.models import OkfConcept
        from app.db.session import session_scope

        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=["proxmox", "vlan"])
        with session_scope() as s:
            s.add(OkfConcept(doc_id=DOC_ID, slug="k1", tags=["llm", "proxmox"]))
            s.add(OkfConcept(doc_id=DOC_ID, slug="k2", tags=["llm", "proxmox"]))

        captured = {}

        def capture(_self, doc_id, global_tags, **kwargs):
            captured["doc_id"] = doc_id
            captured["global_tags"] = global_tags
            captured["concept_points"] = kwargs.get("concept_points")

        monkeypatch.setattr(VectorStore, "set_document_tags_payload", capture)

        assert dts._sync_qdrant_tags(DOC_ID) is True
        assert captured["doc_id"] == DOC_ID
        assert captured["global_tags"] == ["proxmox", "vlan"]
        expected = [
            (
                str(uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, str(settings.okf_dir / DOC_ID / f"{slug}.md"))),
                ["llm", "proxmox"],
            )
            for slug in ("k1", "k2")
        ]
        assert sorted(captured["concept_points"]) == sorted(expected)

    def test_sync_missing_doc_returns_false(self, settings, monkeypatch):
        monkeypatch.setattr(
            VectorStore, "set_document_tags_payload", lambda *a, **k: None
        )
        assert dts._sync_qdrant_tags("nope") is False

    def test_sync_qdrant_failure_is_best_effort(self, settings, monkeypatch):
        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=["proxmox"])

        def boom(*a, **k):
            raise RuntimeError("qdrant down")

        monkeypatch.setattr(VectorStore, "set_document_tags_payload", boom)
        assert dts._sync_qdrant_tags(DOC_ID) is False


# ---------- API ----------

ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        auth_provider="simulation",
        auth_role_groups=ROLE_GROUPS,
        auth_default_role="viewer",
        dedup_enabled=False,
        auth_sim_users=[
            {"user_id": "sim-editor", "username": "demo.editor", "groups": ["KB_Editor"]},
            {"user_id": "sim-viewer", "username": "demo.viewer", "groups": ["KB_Viewer"]},
            {"user_id": "sim-admin", "username": "demo.admin", "groups": ["KB_Admin"]},
        ],
    )
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return TestClient(create_app())


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


def login(client, username):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200, resp.text


class TestPatchTagsApi:
    def test_editor_can_update(self, client, monkeypatch):
        monkeypatch.setattr(
            VectorStore, "set_document_tags_payload", lambda *a, **k: None
        )
        get_registry().create(DOC_ID, "a.docx", "x", 10, tags=["proxmox"])
        login(client, "demo.editor")

        resp = client.patch(f"/api/documents/{DOC_ID}/tags", json={"tags": ["vlan"]})

        assert resp.status_code == 200, resp.text
        assert resp.json()["tags"] == ["vlan"]
        assert resp.json()["dev_tags_sync_pending"] is False

    def test_viewer_forbidden(self, client, monkeypatch):
        get_registry().create(DOC_ID, "a.docx", "x", 10, tags=["proxmox"])
        login(client, "demo.viewer")

        resp = client.patch(f"/api/documents/{DOC_ID}/tags", json={"tags": ["vlan"]})

        assert resp.status_code == 403

    def test_missing_document_404(self, client, monkeypatch):
        login(client, "demo.editor")
        resp = client.patch(f"/api/documents/{DOC_ID}/tags", json={"tags": ["vlan"]})
        assert resp.status_code == 404

    def test_invalid_doc_id_404(self, client, monkeypatch):
        login(client, "demo.editor")
        resp = client.patch("/api/documents/not-a-real-id/tags", json={"tags": ["vlan"]})
        assert resp.status_code == 404


class TestBulkTagsApi:
    def test_editor_can_bulk_update_with_per_doc_audit(self, client, monkeypatch):
        monkeypatch.setattr(
            VectorStore, "set_document_tags_payload", lambda *a, **k: None
        )
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=[])
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10, tags=[])
        login(client, "demo.editor")

        resp = client.post(
            "/api/documents/bulk-tags",
            json={"doc_ids": ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"], "add": ["vlan"], "remove": []},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 2
        assert len(data["updated"]) == 2
        entries = AuditService().query(action_type=DOCUMENT_BULK_TAGS_UPDATE)
        assert len(entries) == 2

    def test_exceeds_limit_rejected(self, client, monkeypatch):
        login(client, "demo.editor")
        doc_ids = [f"{i:016x}" for i in range(51)]
        for d in doc_ids:
            get_registry().create(d, f"{d}.docx", "x", 10, tags=[])
        resp = client.post(
            "/api/documents/bulk-tags",
            json={"doc_ids": doc_ids, "add": ["vlan"], "remove": []},
        )
        assert resp.status_code == 400

    def test_missing_doc_404(self, client, monkeypatch):
        login(client, "demo.editor")
        resp = client.post(
            "/api/documents/bulk-tags",
            json={"doc_ids": [DOC_ID], "add": ["vlan"], "remove": []},
        )
        assert resp.status_code == 404