from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def test_standard_runtime_has_pdfium_and_no_mupdf():
    names = {dist.metadata["Name"].lower() for dist in importlib.metadata.distributions()}

    assert "pypdfium2" in names
    assert "pymupdf" not in names
    assert importlib.util.find_spec("pymupdf") is None
    assert importlib.util.find_spec("fitz") is None


def test_sbom_generator_writes_sorted_cyclonedx_components(tmp_path):
    output = tmp_path / "backend-sbom.json"
    script = Path(__file__).parents[1] / "scripts" / "generate_sbom.py"

    subprocess.run([sys.executable, str(script), "--output", str(output)], check=True)

    payload = json.loads(output.read_text(encoding="utf-8"))
    names = [component["name"] for component in payload["components"]]
    assert payload["bomFormat"] == "CycloneDX"
    assert payload["specVersion"] == "1.5"
    assert names == sorted(names, key=str.lower)
    assert "pypdfium2" in {name.lower() for name in names}
