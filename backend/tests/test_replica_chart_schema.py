"""New profile values are representable, but not executable before capability gates."""
import copy
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "deploy/helm/ai-knowledge"


@pytest.mark.parametrize("section,key,value", [
    ("api", "replicas", 2), ("resilience", "class", "resilient"),
    ("worker", "replicas", 1), ("frontend", "replicas", 2),
    ("replicaStorage", "postgres", {"provider": "external"}),
    ("replicaStorage", "artifacts", {"provider": "managed", "backend": "s3"}),
])
def test_compact_cannot_silently_ignore_replica_settings(section, key, value):
    schema = json.loads((CHART / "values.schema.json").read_text())
    compact = {"deployment": {"mode": "compact"}, section: {key: value}}
    # Exercise the conditional independently from unrelated required Secret/PVC inputs.
    guard = {"allOf": schema.get("allOf", [])}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(guard).validate(compact)


def test_replica_values_schema_supports_mixed_profile():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    values["secrets"]["existingSecret"] = "test-runtime"
    for claim in values["persistence"].values():
        claim["existingClaim"] = "test-claim"
    for image in values["images"].values():
        if isinstance(image, dict):
            image["tag"] = "test"
    values["deployment"]["mode"] = "distributed"
    values["replicaStorage"] = {
        "postgres": {"provider": "external"}, "qdrant": {"provider": "managed"},
        "artifacts": {"provider": "external", "backend": "s3"},
    }
    schema = json.loads((CHART / "values.schema.json").read_text())
    validator = jsonschema.Draft7Validator(schema)
    validator.validate(values)
    bad = copy.deepcopy(values)
    bad["replicaStorage"]["artifacts"]["provider"] = "typo"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(bad)
