"""Explicit fresh-install bootstrap cannot adopt data or replace an identity."""
import json

import pytest
from sqlalchemy import text

from app.db.models import Document, ReplicaRuntimeIdentity
from app.db.session import get_engine, session_scope
from app.services.artifacts.control import migration_access
from tests.test_replica_runtime_identity import configured


@pytest.fixture
def bootstrap_settings(tmp_path):
    from app.deployment.bootstrap import OperatorSettings
    values, _ = configured(tmp_path)
    values["artifact_backend"] = "rwx"
    values["artifact_root"] = tmp_path / "shared"
    values["artifact_root"].mkdir()
    values["runtime_identity_file"].unlink()
    return OperatorSettings(**values)


def stamp():
    from app.deployment.runtime_identity import SCHEMA_REVISION
    with get_engine().begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": SCHEMA_REVISION})


def test_probe_receipt_is_bound_and_contains_no_credentials(bootstrap_settings, monkeypatch):
    from app.deployment.bootstrap import probe_artifacts, install_identity, write_identity
    from app.config import Settings
    from app.deployment.runtime_identity import enforce_runtime_identity
    settings = bootstrap_settings
    receipt = probe_artifacts(settings, installation_id="1" * 32, selection_digest="sha256:" + "3" * 64)
    assert receipt["qualification"] == "basic-bootstrap-only"
    assert "CANARY" not in json.dumps(receipt)
    stamp()
    with migration_access(), session_scope() as session:
        document = install_identity(session, settings, receipt)
    write_identity(settings.runtime_identity_file, document)
    running = Settings(**{**settings.model_dump(), "_env_file": None})
    monkeypatch.setattr("app.config.get_settings", lambda: running)
    assert enforce_runtime_identity(running).installation_id == "1" * 32
    # Repeating with the same proof is harmless after later application writes.
    with migration_access(), session_scope() as session:
        session.add(Document(id="newdoc", filename="example.pdf", status="done"))
        assert install_identity(session, settings, receipt) == document


def test_nonempty_installation_requires_maintenance_adoption(bootstrap_settings):
    from app.deployment.bootstrap import probe_artifacts, install_identity
    receipt = probe_artifacts(bootstrap_settings, installation_id="1" * 32, selection_digest="sha256:" + "3" * 64)
    stamp()
    with session_scope() as session:
        session.add(Document(id="legacy", filename="keep.pdf", status="done"))
    with migration_access(), session_scope() as session, pytest.raises(ValueError, match="nonempty"):
        install_identity(session, bootstrap_settings, receipt)
    assert not bootstrap_settings.runtime_identity_file.exists()


def test_changed_identity_is_not_overwritten(bootstrap_settings):
    from app.deployment.bootstrap import probe_artifacts, install_identity
    receipt = probe_artifacts(bootstrap_settings, installation_id="1" * 32, selection_digest="sha256:" + "3" * 64)
    stamp()
    with migration_access(), session_scope() as session:
        original = install_identity(session, bootstrap_settings, receipt)
    bootstrap_settings.llm_global_max_concurrency = 2
    with migration_access(), session_scope() as session, pytest.raises(ValueError, match="identity"):
        install_identity(session, bootstrap_settings, receipt)
    with migration_access(), session_scope() as session:
        assert session.get(ReplicaRuntimeIdentity, 1).fingerprint == original["runtime_fingerprint"]


def test_operator_settings_cannot_skip_runtime_identity_file(bootstrap_settings):
    from app.deployment.runtime_identity import enforce_runtime_identity
    with pytest.raises(ValueError, match="identity"):
        enforce_runtime_identity(bootstrap_settings)


def test_resume_after_sql_commit_before_identity_file_publication(bootstrap_settings):
    from app.deployment.bootstrap import probe_artifacts, install_identity, write_identity
    receipt = probe_artifacts(bootstrap_settings, installation_id="1" * 32, selection_digest="sha256:" + "3" * 64)
    stamp()
    with migration_access(), session_scope() as session:
        original = install_identity(session, bootstrap_settings, receipt)
    assert not bootstrap_settings.runtime_identity_file.exists()
    with migration_access(), session_scope() as session:
        retried = install_identity(session, bootstrap_settings, receipt)
    assert retried == original
    write_identity(bootstrap_settings.runtime_identity_file, retried)
    write_identity(bootstrap_settings.runtime_identity_file, retried)
    content = bootstrap_settings.runtime_identity_file.read_bytes()
    with pytest.raises(ValueError, match="replace"):
        write_identity(bootstrap_settings.runtime_identity_file, {"different": True})
    assert bootstrap_settings.runtime_identity_file.read_bytes() == content


def test_edited_migration_seeds_are_not_adopted(bootstrap_settings):
    from app.deployment.bootstrap import probe_artifacts, install_identity
    from app.db.models import Locale
    receipt = probe_artifacts(bootstrap_settings, installation_id="1" * 32, selection_digest="sha256:" + "3" * 64)
    stamp()
    with session_scope() as session:
        session.add(Locale(code="ru", name="Edited reference data", status="active"))
    with migration_access(), session_scope() as session, pytest.raises(ValueError, match="nonempty"):
        install_identity(session, bootstrap_settings, receipt)


def selection():
    from app.deployment.storage_selection import PINS
    components = {name: {"provider": "managed", "recipe": recipe, "image": image, "license": license_name,
        "install_scope": "namespace", "disk_layout": "dedicated RWO PVC", "failure_model": "single node",
        "backup_restore_recipe": "independent logical export", "maintenance_owner": "test-operator"}
        for name, (recipe, image, license_name) in PINS.items()}
    components["artifacts"].update(provider="external", recipe="external-rwx-v1", image=None,
        install_scope="external", license="provider-managed")
    return {"version": 1, "installation_id": "1" * 32, "namespace": "test-namespace",
        "profile": "basic", "artifact_backend": "rwx", **components}


def test_selection_schema_and_immutable_recipe(tmp_path):
    from pathlib import Path
    from app.deployment.storage_selection import StorageSelection
    schema = Path(__file__).resolve().parents[2] / "deploy/kubernetes/replicas/storage-selection.schema.json"
    assert json.loads(schema.read_text()) == StorageSelection.model_json_schema()
    value = selection()
    StorageSelection.model_validate(value)
    value["postgres"]["image"] = "postgres:latest"
    with pytest.raises(ValueError, match="selected recipe"):
        StorageSelection.model_validate(value)


def test_cli_plan_has_no_database_or_storage_side_effects(tmp_path, monkeypatch, capsys):
    from scripts.bootstrap_replica_runtime import main
    def forbidden(*_a, **_k):
        raise AssertionError("Dry-run must not contact a database/provider")
    monkeypatch.setattr("app.db.session.configure_database", forbidden)
    monkeypatch.setattr("app.deployment.bootstrap.probe_artifacts", forbidden)
    manifest = tmp_path / "selection.json"
    manifest.write_text(json.dumps(selection()), encoding="utf-8")
    runtime = tmp_path / "private.env"
    runtime.write_text("\n".join([
        "OKF_DEPLOYMENT_SCHEMA=replicas-v1", "DEPLOYMENT_MODE=distributed", "APP_ROLE=api", "WORKER_REPLICAS=1",
        "DEPLOYMENT_NAMESPACE=test-namespace", "ENVIRONMENT=development", "APP_SECRET_KEY=" + "a" * 64,
        "DATABASE_URL=postgresql+psycopg://app:CANARY_PASSWORD@database:5432/replica_test_example",
        "ARTIFACT_BACKEND=rwx", "ARTIFACT_PROVIDER=external", "ARTIFACT_UPLOADS_ENABLED=true",
        "ARTIFACT_STORE_ID=test-artifacts", "ARTIFACT_ROOT=" + (tmp_path / "shared").as_posix(),
        "RUNTIME_IDENTITY_FILE=" + (tmp_path / "identity.json").as_posix(),
    ]), encoding="utf-8")
    assert main(["--runtime-env-file", str(runtime), "--selection", str(manifest)]) == 0
    output = capsys.readouterr()
    assert "CANARY" not in output.out + output.err and '"mutations": 0' in output.out
    assert not (tmp_path / "identity.json").exists()


def test_cli_failure_does_not_print_private_selection(tmp_path, capsys):
    from scripts.bootstrap_replica_runtime import main
    manifest = tmp_path / "selection.json"
    manifest.write_text('{"CANARY_PASSWORD": true}', encoding="utf-8")
    runtime = tmp_path / "private.env"
    runtime.write_text("", encoding="utf-8")
    assert main(["--runtime-env-file", str(runtime), "--selection", str(manifest), "--apply"]) == 2
    output = capsys.readouterr()
    assert "CANARY" not in output.out + output.err
