"""Acceptance must never turn incomplete prerequisites into a green result."""
import copy
import json

import pytest

from test_scripts.run_replica_acceptance import build_report, main, validate_manifest


def manifest():
    return {
        "schema_version": 1, "run_id": "abcdefgh", "profile": "basic",
        "context": "isolated-test", "namespace": "replica-test-abcdefgh",
        "database_url": "postgresql+psycopg://user:test-secret@db/replica_test_abcdefgh",
        "qdrant_collection": "replica_test_abcdefgh",
        "bucket_prefix": "replica_test/abcdefgh/",
    }


@pytest.mark.parametrize("field,value", [
    ("database_url", "postgresql://user:secret@db/production"),
    ("database_url", "sqlite:///replica_test_abcdefgh"),
    ("qdrant_collection", "replica_test_other"),
    ("bucket_prefix", "replica_test/abcdefgh/../production/"),
    ("namespace", "default"), ("run_id", "../../production"),
    ("context", ""), ("schema_version", True),
    ("database_url", ["test-secret"]), ("profile", ["basic"]),
])
def test_rejects_live_targets_before_mutation(field, value):
    value_manifest = manifest()
    value_manifest[field] = value
    with pytest.raises(ValueError):
        validate_manifest(value_manifest)


def test_valid_manifest_does_not_require_credentials_in_report():
    validate_manifest(manifest())


def test_required_skip_is_failure(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest()), encoding="utf-8")
    assert main(["--suite", "all", "--manifest", str(path)]) == 1


def test_report_redacts_secrets():
    data = manifest()
    original = copy.deepcopy(data)
    data["profile"] = "test-secret"
    report = build_report(data, "contracts")
    serialized = json.dumps(report)
    assert "test-secret" not in serialized
    assert "database_url" not in serialized
    assert report["passed"] is False
    assert report["skipped_required"] > 0
    assert original["database_url"] == data["database_url"]


def test_malformed_manifest_cli_does_not_echo_secret(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text('{"password": "test-secret",', encoding="utf-8")
    assert main(["--suite", "contracts", "--manifest", str(path)]) == 1
    assert "test-secret" not in capsys.readouterr().out
