"""One controlled actor in opt-in PostgreSQL acceptance. Never uses a default DB."""
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlparse

from app.db.session import configure_for_tests, session_scope
from app.auth.models import User
from app.services.reference_resources import ReferenceKey
from app.services.reference_mutation import ReferenceWriteContext
from app.services.reference_concurrency import ReferenceError, lock_reference_resources, require_reference_revision, bump_reference_revision


def execute(action, data):
    from app.services.tag_registry import TagRegistry
    from app.services.registry import get_registry
    from app.services.attribute_registry import AttributeRegistry
    from app.services.development_registry import DevelopmentRegistry
    actor = User(user_id=data.get("actor", "pg-test"), username="PG test", roles=["admin"])
    if action == "translate":
        TagRegistry().set_translation(data["id"], "en", data["text"],
            reviewed_by=data.get("reviewed_by"), expected_reference_revision=data["revision"])
        return {"status": "saved"}
    if action == "acquire":
        from app.services.reference_edit_lock import acquire_edit_lock
        with session_scope() as session:
            result = acquire_edit_lock(session, ReferenceKey("tag", data["id"]), actor, data["token"])
        return {"status": "acquired", "epoch": result["lease_epoch"]}
    if action == "lock_many":
        keys = [ReferenceKey("tag", identifier) for identifier in data["ids"]]
        with session_scope() as session:
            states = lock_reference_resources(session, keys)
            for row in states.values():
                require_reference_revision(row, 1)
                bump_reference_revision(row)
        return {"status": "saved"}
    if action == "attribute_add":
        result = AttributeRegistry().add("module", "PG", actor=actor, expected_reference_revision=1)
        return {"status": "added", "id": result["id"], "revision": result["reference_revision"]}
    if action == "module_create":
        DevelopmentRegistry().create("PG-001", "PG development", module="PG")
        return {"status": "created"}
    if action == "module_remove":
        return {"status": "removed" if AttributeRegistry().remove("module", "PG") else "missing"}
    if action == "assign_dev":
        get_registry().update(data["doc_id"], development_id=data["id"])
        return {"status": "assigned"}
    if action == "delete_dev":
        registry = DevelopmentRegistry()
        registry._schedule_sync_many = lambda *_args: None
        return {"status": "deleted" if registry.delete(data["id"], 1) else "missing"}
    if action == "cleanup":
        from app.services.reference_tag_actions import apply_cleanup, preview_digest
        candidate = {"tag_id": data["id"], "expected_reference_revision": data["revision"]}
        result = apply_cleanup([candidate], preview_digest([candidate]), actor)
        return {"status": result["results"][0]["status"]}
    if action == "restore":
        return {"status": "restored" if get_registry().restore(data["doc_id"]) else "missing"}
    if action == "assign_tag":
        get_registry().update(data["doc_id"], tags=["pg-tag"])
        return {"status": "assigned"}
    if action == "stopword_add":
        from app.services.locale_service import add_stopword
        add_stopword("zz", "two", "bm25", user=actor)
        return {"status": "added"}
    if action == "dictionary_import":
        from app.services.ui_dictionary import import_dictionary
        key = ReferenceKey("ui_dictionary", "zz")
        result = import_dictionary("zz", {"nav.documents": data["text"]}, None, actor.username, confirm=True,
            reference_context=ReferenceWriteContext(actor, {key: data["revision"]}),
            expected_active_version=data["active_version"], preview_digest=data["digest"])
        return {"status": "saved", "version": result["version"]}
    raise ValueError("Unknown acceptance action")


if __name__ == "__main__":
    url = os.environ["REFERENCE_TEST_POSTGRES_URL"]
    if not urlparse(url).path.rsplit("/", 1)[-1].startswith("test_reference_"):
        raise RuntimeError("Isolated test_reference_* database required")
    configure_for_tests(url)
    action, data, barrier, index = sys.argv[1:]
    directory = Path(barrier)
    (directory / f"ready-{index}").touch()
    deadline = time.monotonic() + 20
    while not (directory / "go").exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Acceptance barrier timed out")
        time.sleep(0.01)
    started = time.monotonic()
    try:
        result = execute(action, json.loads(data))
    except Exception as error:
        if not isinstance(error, ReferenceError) and type(error).__name__ not in {"DevelopmentModuleError", "AttributeValueInUseError"}:
            raise
        result = {"status": getattr(error, "code", type(error).__name__)}
    result["elapsed"] = time.monotonic() - started
    print(json.dumps(result))
