"""Audit deployment choices validate schema and rendered runtime configuration."""

import json
import os
from pathlib import Path
import shutil

import jsonschema
import pytest
import yaml

CHART = Path(__file__).parents[2] / "deploy/helm/ai-knowledge"


def validate(runtime):
    schema = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema["properties"]["runtime"]).validate(runtime)


@pytest.mark.parametrize("db,stdout", [(True, "disabled"), (False, "durable"), (True, "durable")])
def test_schema_accepts_three_audit_choices(db, stdout):
    validate({"auditDbEnabled": db, "auditStdoutMode": stdout})


@pytest.mark.parametrize("runtime", [
    {"auditDbEnabled": False, "auditStdoutMode": "disabled"},
    {"auditDbEnabled": "true", "auditStdoutMode": "durable"},
    {"auditDbEnabled": 1, "auditStdoutMode": "durable"},
    {"auditDbEnabled": None, "auditStdoutMode": "durable"},
    {"auditDbEnabled": False},
    {"auditDbEnabled": True, "auditStdoutMode": "disabled", "config": {"AUDIT_DB_ENABLED": "false"}},
])
def test_schema_rejects_invalid_or_duplicated_audit_choices(runtime):
    with pytest.raises(jsonschema.ValidationError):
        validate(runtime)


def test_default_values_enable_local_audit():
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert values["runtime"]["auditDbEnabled"] is True
    assert values["runtime"]["auditStdoutMode"] == "disabled"


@pytest.mark.skipif(not shutil.which(os.environ.get("HELM_BIN", "helm")), reason="Helm required")
@pytest.mark.parametrize("db,stdout", [("true", "disabled"), ("false", "durable"), ("true", "durable")])
def test_render_emits_selected_audit_sinks(db, stdout):
    from tests.test_kubernetes_chart import render
    docs = render(f"runtime.auditDbEnabled={db}", f"runtime.auditStdoutMode={stdout}")
    runtime = next(doc["data"] for doc in docs if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "runtime")
    assert runtime["AUDIT_DB_ENABLED"] == db
    assert runtime["AUDIT_STDOUT_MODE"] == stdout


@pytest.mark.skipif(not shutil.which(os.environ.get("HELM_BIN", "helm")), reason="Helm required")
@pytest.mark.parametrize("overrides", [
    ("runtime.auditDbEnabled=false",),
    ("runtime.config.AUDIT_DB_ENABLED=false",),
])
def test_render_refuses_disabled_or_duplicate_audit(overrides):
    from tests.test_kubernetes_chart import render
    assert render(*overrides, check=False).returncode != 0
