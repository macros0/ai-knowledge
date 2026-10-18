"""Opt-in acceptance with independent OS processes and an explicitly test-only DB."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from app.db.models import AttributeValue, Development, Document, TagTranslation
from app.db.session import session_scope
from app.services.tag_registry import TagRegistry
from app.services.reference_resources import ReferenceKey
from app.services.reference_concurrency import lock_reference_resources, ReferenceError


@pytest.fixture
def reference_pg_db():
    url = os.environ.get("REFERENCE_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("REFERENCE_TEST_POSTGRES_URL is not configured")
    if not urlparse(url).path.rsplit("/", 1)[-1].startswith("test_reference_"):
        pytest.fail("Acceptance requires an isolated test_reference_* database")
    from app.db.base import Base
    from app.db.session import configure_for_tests, get_engine, init_db
    configure_for_tests(url)
    Base.metadata.drop_all(get_engine())
    init_db()
    yield
    Base.metadata.drop_all(get_engine())


def race(tmp_path, *actors):
    barrier = tmp_path / "actors"
    barrier.mkdir()
    script = Path(__file__).parent / "support/reference_pg_worker.py"
    processes = [subprocess.Popen([sys.executable, str(script), action, json.dumps(data), str(barrier), str(index)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for index, (action, data) in enumerate(actors)]
    try:
        deadline = time.monotonic() + 20
        while not all((barrier / f"ready-{index}").exists() for index in range(len(actors))):
            assert all(process.poll() is None for process in processes), "Actor exited before the barrier"
            assert time.monotonic() < deadline, "Actors did not reach the barrier"
            time.sleep(0.01)
        (barrier / "go").touch()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout.strip().splitlines()[-1]))
        return results
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()


def tag():
    TagRegistry().add(["pg-tag"])
    return TagRegistry().all()[0]


def test_same_revision_and_absent_translation_have_one_process_winner(reference_pg_db, tmp_path):
    item = tag()
    result = race(tmp_path, *(('translate', {"id": item["id"], "revision": item["reference_revision"], "text": text}) for text in ("Alice", "Bob")))
    assert sorted(row["status"] for row in result) == ["saved", "version_conflict"]
    with session_scope() as session:
        assert len(session.scalars(select(TagTranslation)).all()) == 1
    assert TagRegistry().all()[0]["reference_revision"] == item["reference_revision"] + 1


def test_lease_is_shared_between_processes(reference_pg_db, tmp_path):
    item = tag()
    result = race(tmp_path, *(('acquire', {"id": item["id"], "actor": owner, "token": owner * 64}) for owner in ("a", "b")))
    assert sorted(row["status"] for row in result) == ["acquired", "reference_edit_locked"]


def test_reversed_input_order_does_not_reverse_sql_lock_order(reference_pg_db, tmp_path):
    TagRegistry().add(["a", "b"])
    identifiers = [row["id"] for row in TagRegistry().all()]
    result = race(tmp_path, ("lock_many", {"ids": identifiers}), ("lock_many", {"ids": identifiers[::-1]}))
    assert sorted(row["status"] for row in result) == ["saved", "version_conflict"]


def test_sql_writer_wait_is_bounded(reference_pg_db, tmp_path):
    item = tag()
    with session_scope() as session:
        lock_reference_resources(session, [ReferenceKey("tag", item["id"])])
        result = race(tmp_path, ("translate", {"id": item["id"], "revision": 1, "text": "blocked"}))[0]
        assert result["status"] == "reference_write_busy"
        assert 1.5 <= result["elapsed"] < 5


def test_parallel_global_attribute_add_is_idempotent(reference_pg_db, tmp_path):
    result = race(tmp_path, ("attribute_add", {}), ("attribute_add", {}))
    assert [row["status"] for row in result] == ["added", "added"]
    assert result[0]["id"] == result[1]["id"]
    assert result[0]["revision"] == result[1]["revision"] == 2
    with session_scope() as session:
        assert len(session.scalars(select(AttributeValue)).all()) == 1


def test_module_remove_and_development_create_cannot_leave_an_invalid_module(reference_pg_db, tmp_path):
    from app.services.attribute_registry import AttributeRegistry
    AttributeRegistry().add("module", "PG")
    result = race(tmp_path, ("module_create", {}), ("module_remove", {}))
    assert sorted(row["status"] for row in result) in (["AttributeValueInUseError", "created"], ["DevelopmentModuleError", "removed"])
    with session_scope() as session:
        development = session.scalar(select(Development))
        if development:
            assert AttributeRegistry().exists("module", development.module)


@pytest.mark.parametrize("link_action", ["restore", "assign_tag"])
def test_cleanup_and_document_usage_leave_the_used_tag_visible(reference_pg_db, tmp_path, link_action):
    from app.services.registry import get_registry
    item = tag()
    registry = get_registry()
    document = "1234123412341234"
    registry.create(document, "pg.txt", "text/plain", 1, tags=["pg-tag"] if link_action == "restore" else [])
    if link_action == "restore":
        registry.soft_delete(document, "pg-test")
    result = race(tmp_path, ("cleanup", {"id": item["id"], "revision": item["reference_revision"]}),
        (link_action, {"doc_id": document}))
    assert result[0]["status"] in {"deleted", "skipped_used", "skipped_changed"}
    assert result[1]["status"] in {"restored", "assigned"}
    assert TagRegistry().all()[0]["count"] == 1


def test_development_delete_and_document_assignment_do_not_leave_a_dangling_reference(reference_pg_db, tmp_path):
    from app.services.registry import get_registry
    from app.services.development_registry import DevelopmentRegistry
    development = DevelopmentRegistry().create("PG-001", "Development")
    document = "1234123412341234"
    get_registry().create(document, "pg.txt", "text/plain", 1)
    result = race(tmp_path, ("delete_dev", {"id": development["id"]}), ("assign_dev", {"id": development["id"], "doc_id": document}))
    assert result[0]["status"] == "deleted"
    assert result[1]["status"] in {"assigned", "development_not_found"}
    with session_scope() as session:
        assert session.get(Document, document).development_id is None


def test_two_dictionary_imports_from_one_preview_revision_have_one_winner(reference_pg_db, tmp_path):
    from app.services.locale_service import create_locale
    from app.services.ui_dictionary import import_dictionary, history
    create_locale("zz", "Test")
    actors = []
    for text in ("Alice", "Bob"):
        preview = import_dictionary("zz", {"nav.documents": text}, None, "PG test")
        actors.append(("dictionary_import", {"text": text, "revision": preview["reference_revision"],
            "digest": preview["preview_digest"], "active_version": preview["active_version"]}))
    assert sorted(row["status"] for row in race(tmp_path, *actors)) == ["saved", "version_conflict"]
    assert len(history("zz")) == 1


def test_stopword_preview_does_not_overwrite_another_process_add(reference_pg_db, tmp_path):
    from app.auth.models import User
    from app.services.locale_service import create_locale, import_stopwords, list_stopwords
    from app.services.reference_mutation import ReferenceWriteContext
    create_locale("zz", "Test")
    actor = User(user_id="pg", username="PG", roles=["admin"])
    preview = import_stopwords("zz", ["one"], "bm25", "replace", confirm=False, user=actor)
    assert race(tmp_path, ("stopword_add", {}))[0]["status"] == "added"
    with pytest.raises(ReferenceError):
        import_stopwords("zz", ["one"], "bm25", "replace", confirm=True, user=actor, preview_digest=preview["preview_digest"],
            reference_context=ReferenceWriteContext(actor, {ReferenceKey("stopword_set", "zz", kind="bm25"): preview["reference_revision"]}))
    assert [row["word"] for row in list_stopwords("zz", "bm25")] == ["two"]


def test_ten_process_contenders_have_exactly_one_owner(reference_pg_db, tmp_path):
    item = tag()
    actors = [("acquire", {"id": item["id"], "actor": f"contender-{index}", "token": f"{index:064x}"})
              for index in range(10)]
    results = race(tmp_path, *actors)
    assert sum(row["status"] == "acquired" for row in results) == 1
    assert sum(row["status"] == "reference_edit_locked" for row in results) == 9


def test_fifty_open_editors_and_hundred_cycles_leave_no_lease_leak(reference_pg_db):
    from app.auth.models import User
    from app.services.reference_edit_lock import acquire_edit_lock, release_edit_lock
    from app.services.diagnostics.reference_counters import snapshot
    registry = TagRegistry()
    registry.add([f"load-{index:03d}" for index in range(50)])
    rows = registry.all()
    actor = User(user_id="load-editor", username="Load editor", roles=["admin"])
    token = "d" * 64
    opened = []
    for row in rows:
        key = ReferenceKey("tag", row["id"])
        with session_scope() as session:
            acquired = acquire_edit_lock(session, key, actor, token)
        opened.append((key, acquired["lease_epoch"]))
    assert snapshot()["active_leases"]["tag"] == 50
    for key, epoch in opened:
        with session_scope() as session:
            release_edit_lock(session, key, actor, token, epoch)
    target = rows[0]
    key = ReferenceKey("tag", target["id"])
    timings = {"read_ms": [], "save_ms": []}
    for index in range(100):
        started = time.perf_counter()
        current = next(row for row in registry.all("en") if row["id"] == target["id"])
        timings["read_ms"].append((time.perf_counter() - started) * 1000)
        with session_scope() as session:
            lease = acquire_edit_lock(session, key, actor, token)
        started = time.perf_counter()
        registry.set_translation(target["id"], "en", f"Cycle {index}", actor=actor,
            expected_reference_revision=current["reference_revision"],
            lease={"edit_token": token, "lease_epoch": lease["lease_epoch"]})
        timings["save_ms"].append((time.perf_counter() - started) * 1000)
        with session_scope() as session:
            release_edit_lock(session, key, actor, token, lease["lease_epoch"])
    assert snapshot()["active_leases"]["tag"] == 0
    assert next(row for row in registry.all("en") if row["id"] == target["id"])["reference_revision"] == 101
    print(json.dumps({"scope": "service_pg_local", "editors": 50, "cycles": 100,
        "timings": {kind: {"p50": sorted(values)[49], "p95": sorted(values)[94]} for kind, values in timings.items()}}))


def test_tag_read_api_p95_against_committed_baseline(reference_pg_db):
    baseline = os.environ.get("REFERENCE_TEST_BASELINE_APP")
    if not baseline:
        pytest.skip("REFERENCE_TEST_BASELINE_APP is not configured")
    assert (Path(baseline) / "app/main.py").is_file()
    TagRegistry().add([f"benchmark-{index:03d}" for index in range(50)])
    script = Path(__file__).parent / "support/reference_api_benchmark.py"
    samples = {"baseline": [], "candidate": []}
    for name in ["baseline", "candidate", "candidate", "baseline"]:
        env = dict(os.environ)
        if name == "baseline":
            env["PYTHONPATH"] = baseline + os.pathsep + str(Path(__file__).parents[2] / "doc-parser/src")
        process = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=90)
        assert process.returncode == 0, process.stderr
        samples[name].append(json.loads(process.stdout.strip().splitlines()[-1]))
    base = sum(row["p95_ms"] for row in samples["baseline"]) / 2
    current = sum(row["p95_ms"] for row in samples["candidate"]) / 2
    result = {"samples": samples, "p95_ratio": current / base, "gate": "candidate_p95<=baseline_p95*1.10"}
    print(json.dumps(result))
    assert current <= base * 1.10, result


def test_domain_row_timeout_after_reference_guard_is_translated(reference_pg_db):
    from sqlalchemy import update
    from app.db.models import Tag, ReferenceResourceState
    from app.db.session import get_engine
    item = tag()
    key = ReferenceKey("tag", item["id"])
    with get_engine().begin() as blocker:
        blocker.execute(update(Tag).where(Tag.id == item["id"]).values(canonical_text="pg-tag"))
        started = time.monotonic()
        with pytest.raises(ReferenceError) as caught:
            with session_scope() as session:
                state = lock_reference_resources(session, [key])[key]
                state.revision += 1
                session.execute(update(Tag).where(Tag.id == item["id"]).values(canonical_text="late-write"))
        assert caught.value.code == "reference_write_busy"
        assert caught.value.status_code == 503
        assert 1.5 <= time.monotonic() - started < 5
    with session_scope() as session:
        assert session.get(Tag, item["id"]).canonical_text == "pg-tag"
        assert session.get(ReferenceResourceState, key.canonical).revision == item["reference_revision"]
