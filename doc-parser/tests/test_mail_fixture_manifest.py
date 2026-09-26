"""Проверяем происхождение и неизменность закреплённых MSG fixture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from docparser import parse_document_result

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "mail"


def test_pinned_mail_fixture_manifest_matches_local_files():
    manifest = json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert isinstance(manifest["release_gate_unverified"], list)
    for item in manifest["pinned_fixtures"]:
        path = FIXTURES_DIR / item["file"]
        assert path.is_file(), item["id"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], item["id"]
        assert item["license"]
        assert item["expected_source_ids"]
        parsed = parse_document_result(path)
        assert [source.source_id for source in parsed.sources] == item["expected_source_ids"], item["id"]
