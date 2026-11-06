"""Old operator commands cannot bypass the future runtime's writer boundary."""
from importlib import import_module

import pytest

from app.config import Settings


@pytest.mark.parametrize("name", ["reindex", "migrate_payload", "rebuild_sparse", "backfill_dedup"])
def test_legacy_writer_command_refuses_distributed_runtime_before_any_provider(name, tmp_path, monkeypatch):
    module = import_module("scripts." + name)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "worker"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    if hasattr(module, "get_settings"):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr("sys.argv", [name + ".py"])
    def forbidden(*_a, **_k):
        pytest.fail("An offline writer constructed a provider before its maintenance guard")
    for boundary in ("VectorStore", "Embedder", "Pipeline"):
        if hasattr(module, boundary):
            monkeypatch.setattr(module, boundary, forbidden)
    with pytest.raises(SystemExit, match="maintenance"):
        module.main()


@pytest.mark.parametrize("name,confirmation", [
    ("audit_hold_restored_events", "--confirm-hold-restored-events"),
    ("migrate_audit_schema", "--confirm-stopped"),
])
def test_operator_env_selection_precedes_the_distributed_guard(name, confirmation, tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = import_module("scripts." + name)
    legacy = Settings(_env_file=None, data_dir=tmp_path)
    future = legacy.model_copy()
    future.okf_deployment_schema, future.app_role = "replicas-v1", "worker"
    monkeypatch.setattr("app.config.get_settings", lambda: legacy)
    configured = []
    def configure(_path):
        configured.append(True)
        monkeypatch.setattr("app.config.get_settings", lambda: future)
        return future
    monkeypatch.setattr(module, "configure", configure)
    monkeypatch.setattr("sys.argv", [name + ".py", confirmation])
    def forbidden(*_a, **_k):
        pytest.fail("Selected future environment reached the audit writer")
    monkeypatch.setattr("app.services.audit_outbox.AuditDispatcher", forbidden)
    with pytest.raises(SystemExit, match="maintenance"):
        module.main()
    assert configured == [True]


@pytest.mark.parametrize("name,args", [("collect_artifacts", ["--apply"]), ("migrate_artifact_storage", ["--show-installation"])])
def test_explicit_operator_settings_are_the_guard_authority(name, args, tmp_path, monkeypatch):
    module = import_module("scripts." + name)
    legacy = Settings(_env_file=None, data_dir=tmp_path)
    future = legacy.model_copy()
    future.okf_deployment_schema, future.app_role = "replicas-v1", "worker"
    monkeypatch.setattr("app.config.get_settings", lambda: legacy)
    with pytest.raises(SystemExit, match="maintenance"):
        module.main(args, settings=future)
