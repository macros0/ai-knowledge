"""Repeat scope lease contracts on the explicitly isolated acceptance PostgreSQL."""
from datetime import datetime, timedelta, timezone
import multiprocessing
import os

import pytest

from app.db.models import Document, DocumentScopeEditLock, DocumentScopeChange
from app.db.session import session_scope
from tests.test_reference_postgres_concurrency import reference_pg_db as _pg_fixture
from tests.test_processing_scope_jobs import setup as _jobs_fixture
from tests import test_processing_scope_edit_lock as scope_tests

reference_pg_db = _pg_fixture
jobs_setup = _jobs_fixture


@pytest.fixture
def scope_doc(reference_pg_db):
    with session_scope() as session:
        session.add(Document(id="scope-pg", filename="scope.docx", status="done"))
    return "scope-pg"


def _claim(url, doc_id, owner, barrier, results):
    from app.db.session import configure_for_tests
    from app.services.processing_scope import ProcessingScopeConflict
    from app.services.processing_scope_edit_lock import acquire_edit_lock
    configure_for_tests(url)
    barrier.wait(timeout=15)
    try:
        with session_scope() as session:
            acquire_edit_lock(session, doc_id, owner, owner, owner * 64)
        results.put("acquired")
    except ProcessingScopeConflict as error:
        results.put(error.code)


def test_scope_lease_has_one_winner_across_processes(scope_doc):
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    processes = [context.Process(target=_claim, args=(os.environ["REFERENCE_TEST_POSTGRES_URL"],
                 scope_doc, owner, barrier, results)) for owner in ("a", "b")]
    try:
        for process in processes:
            process.start()
        assert sorted(results.get(timeout=25) for _ in processes) == ["acquired", "scope_edit_locked"]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        results.close()


def test_scope_stale_requests_preserve_new_lease(scope_doc):
    scope_tests.test_expiration_and_release_do_not_release_a_new_editor(scope_doc)


@pytest.mark.parametrize("state", ["admitted", "queued", "processing", "copying", "ready"])
def test_scope_operation_survives_editor_expiry(scope_doc, state):
    scope_tests.test_operation_blocks_editing_even_after_editor_and_worker_lease_expire(scope_doc, state)


def test_accepted_scope_request_replays_after_editor_expiry(reference_pg_db, jobs_setup):
    from tests.test_processing_scope_jobs import preview
    from app.services import processing_scope_jobs as jobs
    from app.services.processing_scope_edit_lock import acquire_edit_lock, release_edit_lock
    base, dispatched = jobs_setup
    with session_scope() as session:
        acquire_edit_lock(session, base.doc_id, "editor", "Editor", "a" * 64)
    plan = preview(base)
    first = jobs.apply_scope_change(base.doc_id, plan["plan_id"], "1" * 32,
                                    base.generation_id, "editor", {"fast_exclude"})
    with session_scope() as session:
        session.get(DocumentScopeEditLock, base.doc_id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        release_edit_lock(session, base.doc_id, "editor", "a" * 64)
        assert session.get(DocumentScopeChange, plan["plan_id"]).state not in {"canceled", "failed"}
    replay = jobs.apply_scope_change(base.doc_id, plan["plan_id"], "1" * 32,
                                     base.generation_id, "editor", {"fast_exclude"})
    assert replay["candidate_generation_id"] == first["candidate_generation_id"]
    assert len(dispatched) == 1
