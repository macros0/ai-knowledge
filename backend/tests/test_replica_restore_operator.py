"""Offline restore uses operator SQL without changing the signed runtime settings."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.db.models import AuthSession, MaintenanceState, ReplicaRuntimeIdentity
from app.db.session import get_engine
from tests.test_replica_restore_identity import load, prepare


APP_URL = "postgresql+psycopg://runtime:APP_CANARY@postgres:5432/replica_test_target01?sslmode=verify-full&sslrootcert=/run/ca.pem"
OP_URL = APP_URL.replace("runtime:APP_CANARY", "operator:OPERATOR_CANARY")


def resolve(tool, settings, path):
    assert hasattr(tool, "operator_database_url"), "Restore needs a separate target-bound operator connection"
    return tool.operator_database_url(settings, path)


def test_operator_connection_preserves_application_settings(tmp_path):
    tool = load()
    settings = SimpleNamespace(db_url=APP_URL)
    path = tmp_path / "operator-url"
    path.write_text(OP_URL + "\n")
    assert resolve(tool, settings, path) == OP_URL
    assert settings.db_url == APP_URL
    assert resolve(tool, settings, None) == APP_URL


@pytest.mark.parametrize("change", [
    {"host": "foreign"}, {"database": "replica_test_foreign"}, {"port": 5433},
    {"drivername": "postgresql+psycopg2"}, {"username": None}, {"password": None},
    {"query": {"sslmode": "disable", "sslrootcert": "/run/ca.pem"}},
    {"query": {"sslmode": "verify-full", "sslrootcert": "/run/foreign-ca.pem"}},
    {"query": {"sslmode": "verify-full", "sslrootcert": "/run/ca.pem", "options": "-c search_path=foreign"}},
])
def test_operator_file_cannot_change_database_transport_or_tls(tmp_path, change):
    tool = load()
    # URL.set(None) preserves the old value; _replace is needed for missing credentials.
    url = make_url(OP_URL)._replace(**change)
    path = tmp_path / "operator-url"
    path.write_text(url.render_as_string(hide_password=False))
    with pytest.raises(ValueError):
        resolve(tool, SimpleNamespace(db_url=APP_URL), path)


@pytest.mark.parametrize("raw", [b"", b"x" * 16385, b"\xff", b"postgresql://bad\x00input", b"not a URL"],
    ids=["empty", "oversize", "invalid-utf8", "nul", "invalid-url"])
def test_operator_file_is_bounded_and_malformed_material_is_refused(tmp_path, raw):
    path = tmp_path / "operator-url"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        resolve(load(), SimpleNamespace(db_url=APP_URL), path)


def test_operator_file_requires_the_selected_verified_postgres_transport(tmp_path):
    path = tmp_path / "operator-url"
    path.write_text(OP_URL.replace("verify-full", "require"))
    with pytest.raises(ValueError):
        resolve(load(), SimpleNamespace(db_url=APP_URL.replace("verify-full", "require")), path)


def test_zero_port_is_not_the_default_postgres_port(tmp_path):
    path = tmp_path / "operator-url"
    path.write_text(OP_URL.replace(":5432/", ":0/"))
    with pytest.raises(ValueError):
        resolve(load(), SimpleNamespace(db_url=APP_URL), path)


@pytest.mark.parametrize("missing", ["host", "database"])
def test_operator_connection_requires_a_named_network_database(tmp_path, missing):
    app_url = make_url(APP_URL)._replace(**{missing: None})
    op_url = app_url.set(username="operator", password="OPERATOR_CANARY")
    path = tmp_path / "operator-url"
    path.write_text(op_url.render_as_string(hide_password=False))
    with pytest.raises(ValueError):
        resolve(load(), SimpleNamespace(db_url=app_url.render_as_string(hide_password=False)), path)


def test_operator_secret_projected_symlink_is_supported_when_available(tmp_path):
    import os
    if os.name == "nt":
        pytest.skip("Projected Secret symlinks are qualified on Linux")
    material = tmp_path / "generation"
    material.write_text(OP_URL)
    path = tmp_path / "operator-url"
    path.symlink_to(material)
    assert resolve(load(), SimpleNamespace(db_url=APP_URL), path) == OP_URL


def test_nonregular_operator_material_is_refused_without_waiting_for_a_writer(tmp_path):
    import os
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO refusal is qualified on Linux")
    path = tmp_path / "operator-url"
    os.mkfifo(path)
    with pytest.raises(ValueError):
        resolve(load(), SimpleNamespace(db_url=APP_URL), path)


@pytest.mark.parametrize("foreign", [False, True])
def test_restore_cli_uses_operator_engine_and_signs_unchanged_application_settings(tmp_path, monkeypatch, capsys, foreign):
    import app.db.session as database
    import app.deployment.bootstrap as bootstrap
    import app.deployment.storage_selection as selections
    import app.services.work.maintenance_control as maintenance
    tool = load()
    settings, plan = prepare(tmp_path)
    settings.database_url = APP_URL
    settings.runtime_identity_file = tmp_path / "identity.json"
    selected = SimpleNamespace(namespace=settings.deployment_namespace, installation_id="5" * 32,
        validate_runtime=lambda value: None, model_dump=lambda: {"selection": "fixture"})
    monkeypatch.setattr(bootstrap, "OperatorSettings", lambda **kwargs: settings)
    monkeypatch.setattr(selections.StorageSelection, "model_validate_json", lambda raw: selected)
    configured, probed = [], []
    monkeypatch.setattr(database, "configure_database", lambda url: configured.append(url))
    def probe(value, **kwargs):
        assert value is settings and value.db_url == APP_URL
        probed.append(True)
    monkeypatch.setattr(bootstrap, "probe_artifacts", probe)
    original = maintenance.MaintenanceOperator
    def operator(engine, **kwargs):
        assert kwargs["settings"] is settings and kwargs["settings"].db_url == APP_URL
        return original(engine, allow_sqlite=True, **kwargs)
    monkeypatch.setattr(maintenance, "MaintenanceOperator", operator)
    operator_file = tmp_path / "operator-url"
    operator_file.write_text(OP_URL.replace("@postgres:", "@foreign:") if foreign else OP_URL)
    plan_file, selection_file = tmp_path / "plan.json", tmp_path / "selection.json"
    plan_file.write_text(plan.model_dump_json())
    selection_file.write_text("{}")
    operation = uuid4().hex
    monkeypatch.setattr("sys.argv", ["restore_identity", "--selection", str(selection_file),
        "--backup-plan", str(plan_file), "--operation-id", operation, "--operator-db-file", str(operator_file)])
    assert tool.main() == (2 if foreign else 0)
    out, err = capsys.readouterr()
    assert "OPERATOR_CANARY" not in out + err and "APP_CANARY" not in out + err
    assert settings.db_url == APP_URL
    with get_engine().connect() as connection:
        if foreign:
            assert configured == probed == []
            assert connection.scalar(select(AuthSession.id)) == "old-session"
            assert connection.scalar(select(ReplicaRuntimeIdentity.installation_id)) == plan.source.installation_id
            assert not settings.runtime_identity_file.exists()
        else:
            assert configured == [OP_URL] and probed == [True]
            identity = json.loads(settings.runtime_identity_file.read_bytes())
            from app.deployment.runtime_identity import identity_document
            assert identity == identity_document(settings, installation_id=selected.installation_id,
                storage_receipt_digest=identity["storage_receipt_digest"])
            assert connection.scalar(select(ReplicaRuntimeIdentity.fingerprint)) == identity["runtime_fingerprint"]
            assert connection.scalar(select(AuthSession.id)) is None
            state = connection.execute(select(MaintenanceState)).mappings().one()
            assert state["enabled"] and state["operation_id"] == operation and state["epoch"] == plan.source.epoch + 1
