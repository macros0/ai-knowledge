"""Generate a deterministic CycloneDX inventory for the current Python runtime."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path


def _license(metadata: importlib.metadata.PackageMetadata) -> str | None:
    return metadata.get("License-Expression") or metadata.get("License")


def build_components() -> list[dict]:
    components: list[dict] = []
    for distribution in importlib.metadata.distributions():
        metadata = distribution.metadata
        name = metadata.get("Name")
        if not name:
            continue
        component = {
            "type": "library",
            "name": name,
            "version": distribution.version,
        }
        license_name = _license(metadata)
        if license_name:
            component["licenses"] = [{"license": {"name": license_name}}]
        components.append(component)
    helper_manifest=Path('/opt/7zip/distribution.txt')
    if helper_manifest.is_file():
        lines=helper_manifest.read_text(encoding='utf-8').splitlines()
        components.append({'type':'application','name':'7-Zip', 'version':lines[0].split()[-1],
            'hashes':[{'alg':'SHA-256','content':lines[2].split()[-1]}],
            'licenses':[{'license':{'name':'LGPL-2.1-or-later with upstream BSD notices and unRAR restriction'}}],
            'externalReferences':[{'type':'distribution','url':lines[1]}]})
    return sorted(components, key=lambda component: component["name"].lower())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() and not args.output.is_file():
        parser.error("--output must be a file path")

    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "components": build_components(),
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
