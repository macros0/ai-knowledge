from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_project_license_and_pdf_notices_are_present():
    assert ROOT.joinpath("LICENSE").read_text(encoding="utf-8").startswith("MIT License\n")
    notices = ROOT.joinpath("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "pypdfium2" in notices
    assert "PDFium" in notices


def test_project_owned_source_has_no_agpl_spdx_marker():
    forbidden = "SPDX-License-Identifier: AGPL-3.0-" + "or-later"
    for base in (ROOT / "backend", ROOT / "doc-parser"):
        for source in base.rglob("*.py"):
            if {".venv", "__pycache__", ".pytest_cache"}.intersection(source.parts):
                continue
            assert forbidden not in source.read_text(encoding="utf-8"), source
