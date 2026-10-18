from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db.session import session_scope


def test_revision_conflict_and_atomic_rollback():
    from app.services.reference_resources import ReferenceKey
    from app.services.reference_concurrency import (lock_reference_resources, require_reference_revision,
                                                     bump_reference_revision, ReferenceError)
    key = ReferenceKey("development", "7")
    with session_scope() as s:
        state = lock_reference_resources(s, [key])[key]
        assert state.revision == 1
        require_reference_revision(state, 1)
        assert bump_reference_revision(state) == 2
    with pytest.raises(ReferenceError) as exc:
        with session_scope() as s:
            require_reference_revision(lock_reference_resources(s, [key])[key], 1)
    assert exc.value.code == "version_conflict"
    with pytest.raises(RuntimeError):
        with session_scope() as s:
            bump_reference_revision(lock_reference_resources(s, [key])[key])
            raise RuntimeError("rollback")
    with session_scope() as s:
        assert lock_reference_resources(s, [key])[key].revision == 2


def test_two_writers_with_same_revision_have_one_winner():
    from app.services.reference_resources import ReferenceKey
    from app.services.reference_concurrency import (lock_reference_resources, require_reference_revision,
                                                     bump_reference_revision, ReferenceError)
    key = ReferenceKey("tag", "7")
    def write(_):
        try:
            with session_scope() as s:
                row = lock_reference_resources(s, [key])[key]
                require_reference_revision(row, 1)
                bump_reference_revision(row)
            return "saved"
        except ReferenceError:
            return "conflict"
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(write, range(2))) == ["conflict", "saved"]


def test_resource_keys_validate_and_distinguish_global_attribute_scope():
    from app.services.reference_resources import ReferenceKey
    assert ReferenceKey("tag", "007") == ReferenceKey("tag", "7")
    assert ReferenceKey("attribute_catalog", "module").canonical != ReferenceKey("attribute_catalog", "module", org_id=1).canonical
    assert ReferenceKey("stopword_set", "ru", kind="bm25").canonical != ReferenceKey("stopword_set", "ru", kind="marker").canonical
    for args in [("document", "7"), ("tag", "../7"), ("tag", "0"), ("stopword_set", "ru")]:
        with pytest.raises(ValueError):
            ReferenceKey(*args)


def test_missing_precondition_is_not_a_permitted_write():
    from app.services.reference_resources import ReferenceKey
    from app.services.reference_concurrency import lock_reference_resources, require_reference_revision, ReferenceError
    key = ReferenceKey("tag", "1")
    with session_scope() as s:
        with pytest.raises(ReferenceError) as exc:
            require_reference_revision(lock_reference_resources(s, [key])[key], None)
        assert exc.value.status_code == 428


def test_sqlite_write_wait_is_bounded_without_changing_other_connections():
    from app.services.reference_resources import ReferenceKey
    from app.services.reference_concurrency import lock_reference_resources
    from app.db.session import get_engine
    with get_engine().connect() as connection:
        original = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
    with session_scope() as s:
        lock_reference_resources(s, [ReferenceKey("tag", "88")])
        assert s.connection().exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 2000
    with get_engine().connect() as connection:
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == original


def test_empty_resource_set_does_not_start_a_transaction():
    from sqlalchemy.orm import Session
    from app.services.reference_concurrency import lock_reference_resources
    with Session() as session:
        assert lock_reference_resources(session, []) == {}
        assert not session.in_transaction()


@pytest.mark.parametrize("phase", ["body", "commit"])
@pytest.mark.parametrize("sqlstate", ["55P03", "57014", "40P01"])
def test_late_reference_timeout_is_translated_and_rolled_back(monkeypatch, phase, sqlstate):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from app.db.models import ReferenceResourceState
    from app.services.reference_resources import ReferenceKey
    from app.services.reference_concurrency import lock_reference_resources, ReferenceError
    class DriverError(Exception):
        pass
    original = DriverError("timeout")
    original.sqlstate = sqlstate
    failure = OperationalError("late domain write", {}, original)
    key = ReferenceKey("tag", "89")
    def fail(*args):
        raise failure
    with pytest.raises(ReferenceError) as caught:
        with session_scope() as session:
            state = lock_reference_resources(session, [key])[key]
            state.revision += 1
            if phase == "body":
                fail()
            else:
                event.listen(session, "before_commit", fail)
    assert caught.value.code == "reference_write_busy"
    assert caught.value.status_code == 503
    with session_scope() as session:
        assert session.get(ReferenceResourceState, key.canonical) is None


def test_non_reference_operational_error_is_not_translated():
    from sqlalchemy.exc import OperationalError
    class DriverError(Exception):
        sqlstate = "55P03"
    failure = OperationalError("ordinary write", {}, DriverError("timeout"))
    with pytest.raises(OperationalError) as caught:
        with session_scope():
            raise failure
    assert caught.value is failure
