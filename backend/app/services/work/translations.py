"""Versioned translation cursors; no model call holds a SQL transaction."""
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import func, or_, select

from app.config import get_settings
from app.db.models import Job, WorkItem
from app.db.session import session_scope
from app.services import audit
from app.services.artifacts.job_files import lock_job
from app.services.artifacts.publication import canonical_hash
from app.services.reference_translation import CONFIG, SKIPS, capture_translation_candidate, commit_translation_candidate
from .admission import lock_admission
from .context import work_execution
from .handlers import WorkDeferred, TransientWorkError
from .models import WorkSpec, LostOwnership
from .progress import load_progress, save_progress
from .store import enqueue, finish, _locked, _now

JOB_TYPE = "reference_translation"
JOB_TYPES = frozenset({JOB_TYPE, "glossary_translation"})


def admit_reference_translation(locale, entities, user, *, ip_address=None):
    from app.services.glossary.normalization import validate_locale
    locale = validate_locale(locale, allow_und=False)
    if (not isinstance(entities, list) or not entities or len(entities) > 3
            or len(set(entities)) != len(entities) or any(entity not in CONFIG for entity in entities)):
        raise ValueError("Invalid translation entities")
    params = {"locale": locale, "entities": list(entities), "user_id": user.user_id, "username": user.username,
        "ip_address": ip_address}
    return admit_translation(params, user, JOB_TYPE)


def admit_translation(params, user, kind):
    from app.services.errors import DomainError
    if kind not in JOB_TYPES:
        raise ValueError("Unsupported translation operation")
    with session_scope() as session:
        lock_admission(session)
        pending = session.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running", "awaiting_approval"])))
        if pending >= get_settings().job_queue_max_pending:
            raise DomainError("Очередь обработки заполнена", code="queue_overloaded")
        job = Job(job_type=kind, status="queued", params=params, created_by_id=user.user_id, created_by=user.username)
        session.add(job)
        session.flush()
        enqueue(session, WorkSpec(kind, 1, {"job_id": job.id, "input_hash": canonical_hash(params)},
            f"translation/{job.id}/{uuid4().hex}"))
        audit.record_in_session(session, action_type=audit.TRANSLATIONS_BACKFILL, user_id=user.user_id,
            username=user.username, target_type=audit.TARGET_JOB, target_id=str(job.id), ip_address=params["ip_address"],
            new_value={"locale": params["locale"], "operation": kind, "status": "queued"})
        return {"job_id": job.id, "status": "queued", "locale": params["locale"]}


def _job(session, work):
    payload = work.spec.payload
    if (work.spec.payload_version != 1 or work.spec.doc_id is not None or set(payload) != {"job_id", "input_hash"}
            or type(payload["job_id"]) is not int or payload["job_id"] < 1):
        raise ValueError("Invalid translation work")
    job = lock_job(session, Job, payload["job_id"])
    if (work.spec.kind not in JOB_TYPES or job is None or job.job_type != work.spec.kind or job.status not in {"queued", "running"}
            or canonical_hash(job.params) != payload["input_hash"]):
        raise ValueError("Translation input or job state changed")
    return job


def _pending_ids(session, entity, locale, after_id):
    parent, translation, foreign_key, source_field, _, _ = CONFIG[entity]
    query = select(parent.id).outerjoin(translation,
        (getattr(translation, foreign_key) == parent.id) & (translation.locale == locale)).where(
            parent.id > after_id, func.coalesce(parent.canonical_locale, "und") != locale,
            getattr(parent, source_field).is_not(None), translation.reviewed_by.is_(None),
            or_(getattr(translation, foreign_key).is_(None), translation.is_machine_translated.is_(True)))
    return list(session.scalars(query.order_by(parent.id).limit(10)))


def _counts():
    return {key: 0 for key in ("created", "updated", "unchanged", "failed", *SKIPS)}


def run_reference_translation(work):
    from app.services import translation
    from app.services.errors import public_error_code
    from app import error_codes as codes
    with work_execution(work):
        try:
            with session_scope() as session:
                job = _job(session, work)
                params = deepcopy(job.params)
                state = load_progress(session, work, version=1) or {"entity_index": 0, "last_id": 0, "result": {}}
                if (type(state.get("entity_index")) is not int or not 0 <= state["entity_index"] <= len(params["entities"])
                        or type(state.get("last_id")) is not int or state["last_id"] < 0 or type(state.get("result")) is not dict):
                    raise ValueError("Invalid translation cursor")
                job.status, job.started_at = "running", job.started_at or _now(session)
            processed = 0
            while state["entity_index"] < len(params["entities"]) and processed < 10:
                entity = params["entities"][state["entity_index"]]
                state["result"].setdefault(entity, _counts())
                with session_scope() as session:
                    ids = _pending_ids(session, entity, params["locale"], state["last_id"])
                if not ids:
                    state["entity_index"], state["last_id"] = state["entity_index"] + 1, 0
                    with session_scope() as session:
                        save_progress(session, work, version=1, state=state)
                    continue
                for identifier in ids[:10 - processed]:
                    candidate, outcome = capture_translation_candidate(entity, identifier, params["locale"])
                    translated = None
                    if candidate is not None:
                        try:
                            values = translation.translate_texts_batch([candidate.source_text], params["locale"], source_locale=candidate.source_locale)
                            translated = values[0] if isinstance(values, list) and len(values) == 1 else None
                        except LostOwnership:
                            raise
                        except Exception as exc:
                            if isinstance(exc, TransientWorkError) or public_error_code(exc) in {
                                    codes.DEPENDENCY_UNAVAILABLE, codes.TIMEOUT, codes.RATE_LIMITED}:
                                raise
                        if not isinstance(translated, str) or not translated.strip() or len(translated) > 255:
                            outcome = "failed"
                    with session_scope() as session:
                        job = _job(session, work)
                        if outcome is None:
                            outcome = commit_translation_candidate(session, candidate, translated.strip(), actor_name=params["username"])
                        state["result"][entity][outcome] += 1
                        state["last_id"] = identifier
                        save_progress(session, work, version=1, state=state)
                        job.result = {"locale": params["locale"], "result": deepcopy(state["result"])}
                    processed += 1
            if state["entity_index"] < len(params["entities"]):
                return WorkDeferred(1)
            with session_scope() as session:
                job = _job(session, work)
                job.status, job.finished_at = "completed", _now(session)
                job.result = {"locale": params["locale"], "result": deepcopy(state["result"])}
                audit.record_in_session(session, action_type=audit.TRANSLATIONS_BACKFILL, user_id=params["user_id"],
                    username=params["username"], target_type=audit.TARGET_LOCALE, target_id=params["locale"],
                    new_value={"entities": params["entities"], "result": state["result"]}, ip_address=params["ip_address"])
                finish(session, work, outcome="succeeded")
        except LostOwnership:
            raise
        except Exception as exc:
            project_failure(work, exc)
            raise


def project_failure(work, exc):
    from app.services.errors import public_error_code
    from app import error_codes as codes
    transient = isinstance(exc, TransientWorkError) or public_error_code(exc) in {
        codes.DEPENDENCY_UNAVAILABLE, codes.TIMEOUT, codes.RATE_LIMITED}
    with session_scope() as session:
        item, now = _locked(session, work)
        if not transient or item.attempts >= get_settings().work_max_attempts:
            job = _job(session, work)
            job.status, job.finished_at = "failed", now
            job.error = "Обработка прервана"
            job.result = {**(job.result or {}), "error_code": codes.DEPENDENCY_UNAVAILABLE if transient else codes.INTERNAL_ERROR}
            audit.record_in_session(session, action_type=audit.TRANSLATIONS_BACKFILL, user_id=job.created_by_id,
                username=job.created_by, target_type=audit.TARGET_JOB, target_id=str(job.id),
                new_value={"status": "failed", "error_code": job.result["error_code"]})
    if transient and not isinstance(exc, TransientWorkError):
        raise TransientWorkError("model_unavailable") from exc


def reconcile_translations(work):
    if work.spec.payload:
        raise ValueError("Invalid translation reconciliation input")
    with work_execution(work):
        with session_scope() as session:
            cursor = load_progress(session, work, version=1).get("last_id", 0)
            if type(cursor) is not int or cursor < 0:
                raise ValueError("Invalid translation reconciliation cursor")
            ids = list(session.scalars(select(Job.id).where(Job.job_type.in_(JOB_TYPES),
                Job.id > cursor, Job.status.in_(["queued", "running"])).order_by(Job.id).limit(100)))
        for job_id in ids:
            with session_scope() as session:
                job = lock_job(session, Job, job_id)
                save_progress(session, work, version=1, state={"last_id": job_id})
                if job is None or job.status not in {"queued", "running"}:
                    continue
                parent = session.scalar(select(WorkItem).where(WorkItem.kind == job.job_type,
                    WorkItem.payload_json["job_id"].as_integer() == job_id).limit(1))
                if parent is None or parent.status not in {"failed", "cancelled"}:
                    continue
                job.status, job.finished_at = "failed", _now(session)
                job.error = "Обработка прервана"
                job.result = {**(job.result or {}), "error_code": "job_interrupted"}
                audit.record_in_session(session, action_type=audit.TRANSLATIONS_BACKFILL, user_id=job.created_by_id,
                    username=job.created_by, target_type=audit.TARGET_JOB, target_id=str(job.id),
                    new_value={"status": "failed", "error_code": "job_interrupted"})
    return WorkDeferred(1) if len(ids) == 100 else None
