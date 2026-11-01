"""Bounded glossary translation work preserves source/target revision checks."""
from copy import deepcopy

from app.auth.models import User
from app.config import get_settings
from app.db.session import session_scope
from app.models.glossary import GlossaryTranslationBackfillRequest
from app.services import audit
from .context import work_execution
from .handlers import TransientWorkError
from .models import LostOwnership
from .progress import load_progress, save_progress
from .store import finish, _now
from .translations import admit_translation, _job, project_failure


def admit_glossary_translation(locale, term_ids, expected_versions, user, *, ip_address=None):
    request = GlossaryTranslationBackfillRequest(locale=locale, term_ids=term_ids, expected_translation_versions=expected_versions)
    params = {"locale": request.locale, "term_ids": list(request.term_ids),
        "expected_translation_versions": {str(key): value for key, value in request.expected_translation_versions.items()},
        "user_id": user.user_id, "username": user.username, "ip_address": ip_address}
    return admit_translation(params, user, "glossary_translation")


def _summary(locale, total):
    return {"locale": locale, "requested": total, "status": "completed", **{key: 0 for key in (
        "created", "updated", "failed", "skipped_changed", "skipped_locked", "skipped_reviewed", "skipped_same_locale",
        "skipped_disabled", "skipped_missing", "skipped_expected_version")}}


def _candidate_outcome(candidate, expected, locale):
    if candidate is None:
        return "skipped_missing"
    if candidate[11]:
        return "skipped_locked"
    if expected is not None and expected != candidate[6]:
        return "skipped_expected_version"
    if not candidate[1]:
        return "skipped_disabled"
    if candidate[2] == locale:
        return "skipped_same_locale"
    if candidate[7] or candidate[8] is False:
        return "skipped_reviewed"
    return None


def run_glossary_translation(work):
    from app.services.glossary import translations as adapter
    from app.services.errors import public_error_code
    from app import error_codes as codes
    with work_execution(work):
        try:
            with session_scope() as session:
                job = _job(session, work)
                params = deepcopy(job.params)
                state = load_progress(session, work, version=1) or {"next_index": 0, "result": _summary(params["locale"], len(params["term_ids"]))}
                if type(state.get("next_index")) is not int or not 0 <= state["next_index"] <= len(params["term_ids"]) or type(state.get("result")) is not dict:
                    raise ValueError("Invalid glossary translation cursor")
                job.status, job.started_at = "running", job.started_at or _now(session)
            user = User(user_id=params["user_id"], username=params["username"], roles=["admin"])
            if get_settings().translation_provider == "off":
                state["next_index"], state["result"]["status"] = len(params["term_ids"]), "skipped_provider_off"
            while state["next_index"] < len(params["term_ids"]):
                identifier = params["term_ids"][state["next_index"]]
                candidate = adapter._candidate(identifier, params["locale"])
                expected = params["expected_translation_versions"].get(str(identifier))
                outcome = _candidate_outcome(candidate, expected, params["locale"])
                if outcome is None:
                    try:
                        texts = [candidate[3], *([candidate[4]] if candidate[4] is not None else [])]
                        translated = adapter.translate_texts_batch(texts, params["locale"], source_locale=candidate[2], strict=True)
                        name = adapter._clean_name(translated[0])
                        description = adapter._clean_description(translated[1] if candidate[4] is not None else None)
                    except LostOwnership:
                        raise
                    except Exception as exc:
                        if isinstance(exc, TransientWorkError) or public_error_code(exc) in {
                                codes.DEPENDENCY_UNAVAILABLE, codes.TIMEOUT, codes.RATE_LIMITED}:
                            raise
                        outcome = "failed"
                with session_scope() as session:
                    job = _job(session, work)
                    if outcome is None:
                        outcome = adapter._write_machine_translation(term_id=identifier, locale=params["locale"], source_revision=candidate[5],
                            expected_version=candidate[6], expected_reference_revision=candidate[10], display_name=name,
                            description=description, user=user, ip_address=params["ip_address"])
                    state["result"][outcome] += 1
                    state["next_index"] += 1
                    save_progress(session, work, version=1, state=state)
                    job.result = deepcopy(state["result"])
            if any(state["result"][key] for key in ("failed", "skipped_changed", "skipped_reviewed", "skipped_locked")):
                state["result"]["status"] = "partial"
            with session_scope() as session:
                job = _job(session, work)
                save_progress(session, work, version=1, state=state)
                job.status, job.finished_at, job.result = "completed", _now(session), deepcopy(state["result"])
                audit.record_in_session(session, action_type=audit.GLOSSARY_TRANSLATION_BACKFILL, user_id=user.user_id,
                    username=user.username, target_type=audit.TARGET_GLOSSARY_TERM, target_id=None,
                    new_value=state["result"], ip_address=params["ip_address"])
                finish(session, work, outcome="succeeded")
        except LostOwnership:
            raise
        except Exception as exc:
            project_failure(work, exc)
            raise
