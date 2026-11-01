"""Accepted translations and their cursor commit together across worker loss."""
import pytest

from app.auth.models import User
from app.config import Settings
from app.db.models import Job, Tag, TagTranslation, WorkItem
from app.db.session import session_scope
from app.services.work.handlers import TransientWorkError
from app.services.work.models import LostOwnership
from app.services.work.runtime import build_handlers
from app.services.work.store import claim
from tests.test_work_store import expire


@pytest.fixture
def translation_settings(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.translation.get_settings", lambda: settings)
    return settings


def admit():
    from app.services.work.translations import admit_reference_translation
    return admit_reference_translation("en", ["tags"], User(user_id="admin", username="Administrator", roles=["admin"]))


def take():
    with session_scope() as session:
        return claim(session, owner_id="translation-worker", supported={"reference_translation": {1}}, lease_seconds=120)


def test_translation_admission_is_atomic_and_does_not_call_model(translation_settings, monkeypatch):
    monkeypatch.setattr("app.services.translation.translate_texts_batch", lambda *_a, **_k: pytest.fail("API must not call model"))
    receipt = admit()
    with session_scope() as session:
        assert session.get(Job, receipt["job_id"]).status == "queued"
        assert session.query(WorkItem).one().payload_json["job_id"] == receipt["job_id"]
    def audit_down(*_a, **_k):
        raise OSError("audit unavailable")
    monkeypatch.setattr("app.services.audit.record_in_session", audit_down)
    with pytest.raises(OSError):
        admit()
    with session_scope() as session:
        assert session.query(Job).count() == session.query(WorkItem).count() == 1


def test_translation_reclaim_does_not_repeat_an_accepted_result(translation_settings, monkeypatch):
    with session_scope() as session:
        session.add_all([Tag(canonical_text="Первый", canonical_locale="ru"), Tag(canonical_text="Второй", canonical_locale="ru")])
    receipt = admit()
    old = take()
    calls = []
    def translate(texts, *_a, **_k):
        calls.extend(texts)
        if texts == ["Второй"]:
            raise TransientWorkError("model_unavailable")
        return ["First"]
    monkeypatch.setattr("app.services.translation.translate_texts_batch", translate)
    handler = build_handlers().resolve(old.spec)
    with pytest.raises(TransientWorkError):
        handler(old)
    with session_scope() as session:
        assert session.query(TagTranslation).one().text == "First"
    expire(old.work_id)
    current = take()
    monkeypatch.setattr("app.services.translation.translate_texts_batch", lambda texts, *_a, **_k: calls.extend(texts) or ["Second"])
    handler(current)
    with pytest.raises(LostOwnership):
        handler(old)
    with session_scope() as session:
        job = session.get(Job, receipt["job_id"])
        assert job.status == "completed" and job.result["result"]["tags"]["created"] == 2
        assert session.get(WorkItem, old.work_id).status == "succeeded"
    assert calls == ["Первый", "Второй", "Второй"]


def test_translation_cursor_failure_rolls_back_the_translation(translation_settings, monkeypatch):
    from app.services.work import translations
    with session_scope() as session:
        session.add(Tag(canonical_text="Первый", canonical_locale="ru"))
    admit()
    work = take()
    monkeypatch.setattr("app.services.translation.translate_texts_batch", lambda *_a, **_k: ["First"])
    original = translations.save_progress
    def crash(session, claim, *, state, **kwargs):
        if state.get("last_id"):
            raise RuntimeError("crash before cursor acceptance")
        return original(session, claim, state=state, **kwargs)
    monkeypatch.setattr(translations, "save_progress", crash)
    with pytest.raises(RuntimeError):
        build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        assert session.query(TagTranslation).count() == 0


def test_future_translation_route_only_admits_work(translation_settings, monkeypatch):
    from starlette.requests import Request
    from app.api import tags
    from app.models.schemas import TagTranslationBackfillRequest
    monkeypatch.setattr(tags, "backfill_reference_data", lambda *_a, **_k: pytest.fail("API started backfill"))
    result = tags.backfill_translations(TagTranslationBackfillRequest(locale="en", entities=["tags"]),
        Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)}),
        User(user_id="admin", username="Administrator", roles=["admin"]))
    assert result["status"] == "queued" and result["job_id"] > 0


def test_malformed_model_result_is_counted_without_losing_other_translations(translation_settings, monkeypatch):
    with session_scope() as session:
        session.add_all([Tag(canonical_text="Первый", canonical_locale="ru"), Tag(canonical_text="Второй", canonical_locale="ru")])
    receipt = admit()
    def translate(texts, *_a, **_k):
        if texts == ["Первый"]:
            raise ValueError("Malformed model JSON")
        return ["Second"]
    monkeypatch.setattr("app.services.translation.translate_texts_batch", translate)
    work = take()
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        result = session.get(Job, receipt["job_id"]).result["result"]["tags"]
        assert result["created"] == result["failed"] == 1


def test_translation_reconciliation_retires_failed_work_with_audit(translation_settings, monkeypatch):
    from app.services.work.models import WorkSpec
    from app.services.work.store import enqueue
    from app.services import audit
    receipt = admit()
    work = take()
    with session_scope() as session:
        job = session.get(Job, receipt["job_id"])
        job.status = "running"
        session.get(WorkItem, work.work_id).status = "failed"
        enqueue(session, WorkSpec("translation_reconcile", 1, {}, "translation-reconcile"))
        reconcile = claim(session, owner_id="reconcile-worker", supported={"translation_reconcile": {1}}, lease_seconds=120)
    handler = build_handlers().resolve(reconcile.spec)
    original = audit.record_in_session
    def audit_down(*_a, **_k):
        raise OSError("audit unavailable")
    monkeypatch.setattr(audit, "record_in_session", audit_down)
    with pytest.raises(OSError):
        handler(reconcile)
    with session_scope() as session:
        assert session.get(Job, receipt["job_id"]).status == "running"
    monkeypatch.setattr(audit, "record_in_session", original)
    handler(reconcile)
    with session_scope() as session:
        assert session.get(Job, receipt["job_id"]).status == "failed"


def test_glossary_translation_commits_a_term_and_cursor_atomically(translation_settings, monkeypatch):
    from app.db.models import DomainTermTranslation
    from app.services.glossary.registry import GlossaryRegistry
    from app.services.work.glossary_translation import admit_glossary_translation
    from app.services.work import glossary_translation
    term = GlossaryRegistry().create("IT0003", "sap_infotype", "Original", "Description", "de", created_by="admin")
    receipt = admit_glossary_translation("en", [term["id"]], {}, User(user_id="admin", username="Administrator", roles=["admin"]))
    with session_scope() as session:
        work = claim(session, owner_id="glossary-worker", supported={"glossary_translation": {1}}, lease_seconds=120)
    monkeypatch.setattr("app.services.glossary.translations.translate_texts_batch", lambda *_a, **_k: ["Name", "Description"])
    original = glossary_translation.save_progress
    def crash(*_a, **_k):
        raise RuntimeError("cursor not accepted")
    monkeypatch.setattr(glossary_translation, "save_progress", crash)
    with pytest.raises(RuntimeError):
        build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        assert session.query(DomainTermTranslation).count() == 0
    monkeypatch.setattr(glossary_translation, "save_progress", original)
    # A fresh accepted operation can run after a permanent failed receipt.
    receipt = admit_glossary_translation("en", [term["id"]], {}, User(user_id="admin", username="Administrator", roles=["admin"]))
    with session_scope() as session:
        work = claim(session, owner_id="replacement", supported={"glossary_translation": {1}}, lease_seconds=120)
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        job = session.get(Job, receipt["job_id"])
        assert job.status == "completed" and job.result["created"] == 1
        assert session.query(DomainTermTranslation).one().display_name == "Name"


def test_future_glossary_route_only_admits_work(translation_settings, monkeypatch):
    from starlette.requests import Request
    from app.api import glossary
    from app.models.glossary import GlossaryTranslationBackfillRequest
    monkeypatch.setattr(glossary, "get_settings", lambda: translation_settings)
    monkeypatch.setattr(glossary, "backfill_glossary_translations", lambda *_a, **_k: pytest.fail("API started glossary translation"))
    receipt = glossary.backfill_glossary_translations_route(GlossaryTranslationBackfillRequest(locale="en", term_ids=[1]),
        Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)}),
        User(user_id="admin", username="Administrator", roles=["admin"]))
    assert receipt["status"] == "queued" and receipt["job_id"] > 0


def test_translation_reconcile_cursor_reaches_jobs_after_a_full_pending_page(translation_settings):
    from app.services.artifacts.publication import canonical_hash
    from app.services.work.handlers import WorkDeferred
    from app.services.work.models import WorkSpec
    from app.services.work.store import enqueue
    with session_scope() as session:
        for index in range(101):
            job = Job(job_type="reference_translation", status="queued", params={"locale": "en", "entities": ["tags"]})
            session.add(job)
            session.flush()
            work_id = enqueue(session, WorkSpec("reference_translation", 1, {"job_id": job.id, "input_hash": canonical_hash(job.params)}, f"page/{index}"))
        last_id = job.id
        session.get(WorkItem, work_id).status = "failed"
        enqueue(session, WorkSpec("translation_reconcile", 1, {}, "reconcile-pages"))
        old = claim(session, owner_id="first-page", supported={"translation_reconcile": {1}}, lease_seconds=120)
    handler = build_handlers().resolve(old.spec)
    assert handler(old) == WorkDeferred(1)
    expire(old.work_id)
    with session_scope() as session:
        current = claim(session, owner_id="second-page", supported={"translation_reconcile": {1}}, lease_seconds=120)
    handler(current)
    with session_scope() as session:
        assert session.get(Job, last_id).status == "failed"
