"""Initial rule data is available before the backend accepts requests."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI

from app import main
from app.services.glossary.rule_registry import GlossaryRuleRegistry


@pytest.mark.parametrize('environment', ['development', 'production'])
def test_backend_startup_seeds_initial_rules_after_schema_is_ready(monkeypatch, environment):
    # Exercise the real lifespan and database; isolate external services and
    # background workers, which are unrelated to initial glossary data.
    monkeypatch.setattr(main, '_validate_runtime_dependencies', lambda: None)
    monkeypatch.setattr(main, 'get_settings', lambda: SimpleNamespace(environment=environment))
    monkeypatch.setattr(main, 'VectorStore', Mock())
    monkeypatch.setattr(main, 'threading', SimpleNamespace(Thread=Mock(), Event=threading.Event))
    from app.services import job_queue, export_queue, trash, chat_history
    from app.services.diagnostics import runtime

    monkeypatch.setattr(runtime, "initialize_diagnostics",
                        lambda: SimpleNamespace(available=False, start=Mock(), stop=Mock()))

    monkeypatch.setattr(job_queue, 'get_job_queue', Mock())
    monkeypatch.setattr(export_queue, 'get_export_queue', Mock())
    monkeypatch.setattr(trash, 'start_purge_loop', lambda: None)
    monkeypatch.setattr(chat_history, 'start_chat_purge_loop', lambda: None)
    registry = GlossaryRuleRegistry()
    assert registry.list() == []

    application = FastAPI()

    async def start_twice():
        async with main.lifespan(application):
            rules = registry.list()
            assert len(rules) == 4
            first_id = rules[0]['id']
            registry.update(first_id, rules[0]['version'], enabled=False, actor_id='admin')
        async with main.lifespan(application):
            rules = registry.list()
            assert len(rules) == 4
            assert rules[0]['id'] == first_id and rules[0]['enabled'] is False

    asyncio.run(start_twice())
