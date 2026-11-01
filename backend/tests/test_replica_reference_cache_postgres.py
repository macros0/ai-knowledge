"""Warm caches in two API processes observe edits from another process."""
from multiprocessing import get_context
from pathlib import Path
from types import SimpleNamespace

from starlette.responses import Response

from app.config import Settings
from app.db.session import configure_database, get_engine, session_scope
from app.db.models import Tag
from app.services.reference_resources import ReferenceKey
from app.services.reference_concurrency import initialize_reference_resource
from tests.support.replica_postgres import disposable_postgres


def _settings(root):
    settings = Settings(_env_file=None, data_dir=root, stopwords_cache_ttl_seconds=3600)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    return settings


def _cache_reader(url, root, pipe):
    import app.config
    from app.services import stopwords
    from app.api.settings import get_config_revisions
    settings = _settings(Path(root))
    app.config.get_settings = lambda: settings
    stopwords.get_settings = lambda: settings
    configure_database(url)
    stopwords.invalidate()
    try:
        while True:
            response = get_config_revisions(Response(), settings=settings, user=SimpleNamespace(user_id="actor"))
            pipe.send(("replicaupdate" in stopwords.get_stopwords("marker"), response["revisions"]["references"]))
            if not pipe.poll(30):
                raise RuntimeError("Cache acceptance control timed out")
            if pipe.recv() == "stop":
                return
    finally:
        get_engine().dispose()
        pipe.close()


def test_committed_revision_invalidates_both_warm_apis(tmp_path, monkeypatch):
    from app.services import stopwords
    from app.services.locale_service import import_stopwords
    with disposable_postgres() as url:
        stopwords.invalidate()
        stopwords.ensure_seeded()
        settings = _settings(tmp_path)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        context = get_context("spawn")
        processes, controls = [], []
        for number in range(2):
            parent, child = context.Pipe()
            process = context.Process(target=_cache_reader,
                args=(url, str(tmp_path / f"api-{number}"), child))
            process.start()
            child.close()
            processes.append(process)
            controls.append(parent)
        def receive():
            values = []
            for pipe in controls:
                assert pipe.poll(25)
                values.append(pipe.recv())
            return values
        try:
            assert receive() == [(False, "0"), (False, "0")]
            import_stopwords("ru", ["replicaupdate"], "marker", "merge", confirm=True,
                user=SimpleNamespace(user_id="admin", username="admin", roles=["admin"]))
            for pipe in controls:
                pipe.send("probe")
            assert receive() == [(True, "1"), (True, "1")]
            with session_scope() as session:
                session.add(Tag(id=1, canonical_text="created"))
                session.flush()
                initialize_reference_resource(session, ReferenceKey("tag", "1"))
            for pipe in controls:
                pipe.send("probe")
            assert receive() == [(True, "2"), (True, "2")]
        finally:
            for process, pipe in zip(processes, controls, strict=True):
                if process.is_alive():
                    pipe.send("stop")
                process.join(15)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                pipe.close()
        assert all(process.exitcode == 0 for process in processes)


def _glossary_reader(url, root, pipe):
    import app.config
    from app.services.glossary.expansion import prepare_query
    app.config.get_settings = lambda: _settings(Path(root))
    configure_database(url)
    try:
        while True:
            plan = prepare_query("Personnel editor", ui_locale="en", enabled=True)
            pipe.send((bool(plan.strict_groups), plan.glossary_revision))
            if not pipe.poll(30):
                raise RuntimeError("Glossary acceptance control timed out")
            if pipe.recv() == "stop":
                return
    finally:
        get_engine().dispose()
        pipe.close()


def test_existing_glossary_snapshot_observes_remote_mutation_on_both_apis(tmp_path, monkeypatch):
    from app.services.glossary.registry import GlossaryRegistry
    with disposable_postgres() as url:
        settings = _settings(tmp_path)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        registry = GlossaryRegistry()
        term = registry.create(None, "sap_transaction", "PA30")
        context = get_context("spawn")
        processes, controls = [], []
        for number in range(2):
            parent, child = context.Pipe()
            process = context.Process(target=_glossary_reader,
                args=(url, str(tmp_path / f"glossary-api-{number}"), child))
            process.start()
            child.close()
            processes.append(process)
            controls.append(parent)
        def receive():
            values = []
            for pipe in controls:
                assert pipe.poll(25)
                values.append(pipe.recv())
            return values
        try:
            before = receive()
            assert all(not matched for matched, _revision in before)
            registry.add_alias(term["id"], term["version"], "Personnel editor", auto_expand=True, search_enabled=True)
            for pipe in controls:
                pipe.send("probe")
            after = receive()
            assert all(matched for matched, _revision in after)
            assert all(current[1] > previous[1] for previous, current in zip(before, after, strict=True))
        finally:
            for process, pipe in zip(processes, controls, strict=True):
                if process.is_alive():
                    pipe.send("stop")
                process.join(15)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                pipe.close()
        assert all(process.exitcode == 0 for process in processes)
