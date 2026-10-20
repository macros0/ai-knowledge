import json
import os
from pathlib import Path
import shutil

import jsonschema
import pytest
import yaml

CHART=Path(__file__).parents[2]/"deploy/helm/ai-knowledge"


def test_chart_schema_accepts_only_supported_format():
    schema=json.loads((CHART/"values.schema.json").read_text(encoding="utf-8"))
    values=yaml.safe_load((CHART/"values.yaml").read_text(encoding="utf-8"))
    assert values["runtime"]["auditStdoutFormat"]=="native"
    for value in ["native","logstash"]:
        values["runtime"]["auditStdoutFormat"]=value
        jsonschema.Draft7Validator(schema["properties"]["runtime"]).validate(values["runtime"])
    values["runtime"]["auditStdoutFormat"]="unknown"
    with pytest.raises(jsonschema.ValidationError):jsonschema.Draft7Validator(schema["properties"]["runtime"]).validate(values["runtime"])


@pytest.mark.skipif(not shutil.which(os.environ.get("HELM_BIN","helm")),reason="Helm required")
@pytest.mark.parametrize("fmt",["native","logstash"])
def test_render_and_reserved_key(fmt):
    from tests.test_kubernetes_chart import render
    docs=render("runtime.auditStdoutMode=durable",f"runtime.auditStdoutFormat={fmt}")
    config=next(d for d in docs if d["kind"]=="ConfigMap" and "AUDIT_STDOUT_FORMAT" in d.get("data",{}))
    assert config["data"]["AUDIT_STDOUT_FORMAT"]==fmt
    result=render("runtime.config.AUDIT_STDOUT_FORMAT=logstash",check=False)
    assert result.returncode!=0
