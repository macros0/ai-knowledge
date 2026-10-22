"""Opt-in PostgreSQL acceptance; requires a separately created disposable DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy import inspect,select,text
from app.auth.models import User
from app.api.errors import ApiError
from app.db.session import configure_for_tests,get_engine,init_db,session_scope
from app.models.answer_scenarios import ScenarioCreate,ScenarioUpdate
from app.services import answer_scenarios as svc,chat_history
from app.services.answer_scenario_schema import upgrade_answer_scenario_schema

@pytest.fixture
def postgres(monkeypatch):
    url=os.environ.get("SCENARIO_TEST_POSTGRES_URL")
    if not url:pytest.skip("SCENARIO_TEST_POSTGRES_URL not configured")
    if not make_url(url).database.startswith("test_scenario_"):pytest.fail("Disposable test_scenario_* DB required")
    configure_for_tests(url);init_db()
    monkeypatch.setattr(svc,"get_settings",lambda:SimpleNamespace(chat_scenarios_enabled=True,auth_provider="simulation"))
    yield
    get_engine().dispose()


def test_postgres_repeat_migration_and_atomic_save(postgres):
    engine=get_engine()
    with engine.begin() as connection:
        upgrade_answer_scenario_schema(connection);upgrade_answer_scenario_schema(connection)
        assert "answer_scenarios" in inspect(connection).get_table_names()
    user=User(user_id="a",username="a")
    item=svc.create_scenario(user,ScenarioCreate(name="n",instructions="old"))
    barrier=Barrier(2)
    def actor(text):
        barrier.wait(timeout=10)
        try:
            return svc.update_scenario(item.id,user,ScenarioUpdate(name="n",instructions=text,expected_version=1)).version
        except ApiError as error:return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(actor,["first","second"]))
    assert sorted(outcomes)==[2,409]
    assert svc.get_scenario(item.id,user).version==2


def test_postgres_accept_before_unpublish_keeps_snapshot(postgres):
    author=User(user_id="a",username="a");reader=User(user_id="b",username="b")
    item=svc.create_scenario(author,ScenarioCreate(name="n",instructions="old",visibility="public"))
    first=chat_history.begin_attempt(None,reader,"q",str(uuid4()),"scenario",scenario_id=item.id)
    svc.update_scenario(item.id,author,ScenarioUpdate(name="n",instructions="old",visibility="private",expected_version=1))
    assert chat_history.read_attempt_scenario(first,reader).version==1
    with pytest.raises(ApiError):chat_history.begin_attempt(None,reader,"q",str(uuid4()),"scenario",scenario_id=item.id)


def test_postgres_resolve_holds_catalog_until_snapshot_is_read(postgres):
    from threading import Event
    author=User(user_id="a",username="a");reader=User(user_id="b",username="b")
    item=svc.create_scenario(author,ScenarioCreate(name="n",instructions="old",visibility="public"))
    entered=Event()
    def unpublish():
        entered.set()
        return svc.update_scenario(item.id,author,ScenarioUpdate(name="n",instructions="old",visibility="private",expected_version=1))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with session_scope() as session:
            snapshot=svc.resolve_scenario_snapshot(session,item.id,reader)
            future=pool.submit(unpublish)
            assert entered.wait(timeout=5)
            assert snapshot.version==1
        assert future.result(timeout=10).version==2


def test_postgres_update_before_pending_resolve_uses_new_version(postgres):
    import time
    from app.db.models import AnswerScenario,AnswerScenarioVersion
    author=User(user_id="a",username="a");reader=User(user_id="b",username="b")
    item=svc.create_scenario(author,ScenarioCreate(name="n",instructions="old",visibility="public"))
    def resolve():
        with session_scope() as session:return svc.resolve_scenario_snapshot(session,item.id,reader)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with session_scope() as session:
            head=session.scalar(select(AnswerScenario).where(AnswerScenario.id==item.id).with_for_update())
            head.current_version=2
            session.add(AnswerScenarioVersion(scenario_id=item.id,version=2,name="n",description="",instructions="new",instructions_sha256="a"*64,visibility="public"))
            session.flush()
            future=pool.submit(resolve)
            deadline=time.monotonic()+5
            while True:
                with get_engine().connect() as connection:
                    waiting=connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query ILIKE '%answer_scenarios%' AND pid <> pg_backend_pid()"))
                if waiting:break
                assert not future.done(),"Resolver should wait for writer"
                assert time.monotonic()<deadline,"Resolver did not reach row lock"
                time.sleep(.02)
        assert future.result(timeout=10).version==2
