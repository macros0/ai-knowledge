"""P05: compare logical parser output in two work directories and across OSes.

Input corpus is prepared once and mounted unchanged on both hosts. Only physical
saved_path roots and canonical newline conventions are normalized; source IDs,
metadata, text, warnings, Markdown links and emitted file hashes must match.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import tempfile
from dataclasses import asdict
from pathlib import Path

from docparser import PARSER_VERSION, blocks_to_markdown, parse_document_result


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_once(source: Path, workspace: Path) -> dict:
    copied = workspace / source.name
    copied.write_bytes(source.read_bytes())
    attachment_dir = workspace / "attachments"
    result = parse_document_result(copied, attachments_dir=attachment_dir)
    blocks = []
    for block in result.blocks:
        value = asdict(block)
        saved = value["meta"].get("saved_path")
        if saved:
            saved = Path(saved).resolve().relative_to(workspace.resolve()).as_posix()
            assert saved.startswith("attachments/")
            value["meta"]["saved_path"] = saved
        value["text"] = value["text"].replace("\r\n", "\n").replace("\r", "\n")
        blocks.append(value)
    markdown = blocks_to_markdown(result.blocks).replace("\r\n", "\n").replace("\r", "\n")
    files = {path.relative_to(workspace).as_posix(): digest(path.read_bytes())
             for path in sorted(attachment_dir.rglob("*")) if path.is_file()}
    links = re.findall(r"\]\((attachments/[^)]+)\)", markdown)
    assert all(link in files for link in links), (source.name, links, files)
    assert str(workspace) not in markdown
    return {
        "input_sha256": digest(source.read_bytes()), "parser_version": result.parser_version,
        "sources": [asdict(node) for node in result.sources], "warnings": result.warnings,
        "blocks": blocks, "canonical_markdown": markdown, "files": files, "links": links,
    }


def run(corpus: Path, output: Path, compare: Path | None) -> None:
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    results = {}
    for item in manifest["files"]:
        source = corpus / item["file"]
        assert digest(source.read_bytes()) == item["sha256"], source.name
        with tempfile.TemporaryDirectory(prefix="mail-first-") as first, tempfile.TemporaryDirectory(prefix="mail-second-") as second:
            left = parse_once(source, Path(first))
            right = parse_once(source, Path(second))
        assert left == right, f"Unstable reparse: {source.name}"
        results[source.name] = left
    report = {"phase": "passed", "platform": platform.system(), "parser_version": PARSER_VERSION,
              "corpus_manifest_sha256": digest((corpus / "manifest.json").read_bytes()),
              "same_host_reparse_equal": True, "files": results}
    if compare:
        baseline = json.loads(compare.read_text(encoding="utf-8"))
        assert baseline["corpus_manifest_sha256"] == report["corpus_manifest_sha256"]
        assert baseline["parser_version"] == report["parser_version"]
        differing = [name for name in results if baseline["files"].get(name) != results[name]]
        report["comparison_platform"] = baseline["platform"]
        report["cross_platform_equal"] = not differing and baseline["files"].keys() == results.keys()
        report["differing_files"] = differing
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert report.get("cross_platform_equal", True), report.get("differing_files")
    print(json.dumps({key: value for key, value in report.items() if key != "files"} | {"file_count": len(results)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    run(args.corpus.resolve(), args.output.resolve(), args.compare.resolve() if args.compare else None)
