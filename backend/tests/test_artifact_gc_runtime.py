import asyncio
from types import SimpleNamespace

from app.config import Settings


def test_application_lifespan_starts_and_stops_gc(tmp_path, monkeypatch):
    from app import main
    from app.services.artifacts import gc
    import app.services.audit_runtime as audit
    settings = Settings(_env_file=None, data_dir=tmp_path)
    calls = []
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "_validate_runtime_dependencies", lambda: None)
    for name in ("validate_existing_audit", "validate_audit_transport_profile", "preflight_audit"):
        monkeypatch.setattr(audit, name, lambda *_a, **_k: None)
    runtime = SimpleNamespace(available=False, start=lambda: None, stop=lambda: None)
    monkeypatch.setattr("app.services.diagnostics.runtime.initialize_diagnostics", lambda: runtime)
    monkeypatch.setattr("app.services.job_queue.get_job_queue", lambda: SimpleNamespace(recover_after_restart=lambda: None))
    monkeypatch.setattr("app.services.export_queue.get_export_queue", lambda: SimpleNamespace(recover_after_restart=lambda: None))
    monkeypatch.setattr("app.services.trash.start_purge_loop", lambda: None)
    monkeypatch.setattr("app.services.chat_history.start_chat_purge_loop", lambda: None)
    monkeypatch.setattr("app.services.generation_cleanup.run_cleanup_loop", lambda *_a: None)
    monkeypatch.setattr(main, "VectorStore", lambda: SimpleNamespace(ensure_collection=lambda: None,
        backfill_sparse=lambda: 0, backfill_relations=lambda: 0, backfill_chunks=lambda *_a: 0))
    monkeypatch.setattr(gc, "start_gc_if_enabled", lambda actual: calls.append(actual) or SimpleNamespace(stop=lambda: calls.append("stopped")))
    async def run():
        async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
            assert calls == [settings]
        assert calls == [settings, "stopped"]
    asyncio.run(run())


def test_gc_cli_requires_apply_before_provider_changes(tmp_path, monkeypatch):
    from scripts.collect_artifacts import main
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda *_a: (_ for _ in ()).throw(AssertionError("provider accessed")))
    assert main(["--initialize", "--adopt-existing"], settings=Settings(_env_file=None, data_dir=tmp_path)) == 0
