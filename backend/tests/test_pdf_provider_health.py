from __future__ import annotations

from unittest.mock import Mock

import pytest

from app import main
from app.services import health
from docparser.pdf_provider import PdfProviderUnavailable


def _clear_health_cache(monkeypatch):
    monkeypatch.setattr(health, "_cache", {})
    monkeypatch.setattr(health, "_cache_ts", 0.0)
    for check in ("_check_llm", "_check_embeddings", "_check_qdrant", "_check_database"):
        monkeypatch.setattr(health, check, lambda: {"status": "ok"})


def test_health_reports_pdf_provider(monkeypatch):
    _clear_health_cache(monkeypatch)
    monkeypatch.setattr(
        health,
        "get_pdf_provider_metadata",
        lambda: {
            "provider": "pypdf-pdfium",
            "pypdf_version": "6.18.0",
            "pdfium_version": "test-pdfium",
        },
        raising=False,
    )

    result = health.get_health()

    assert result["dependencies"]["pdf_parser"] == {
        "status": "ok",
        "provider": "pypdf-pdfium",
        "pypdf_version": "6.18.0",
        "pdfium_version": "test-pdfium",
    }


def test_startup_rejects_unavailable_pdf_provider(monkeypatch):
    unavailable = Mock(side_effect=PdfProviderUnavailable("missing pypdfium2"))
    monkeypatch.setattr(main, "get_pdf_provider_metadata", unavailable, raising=False)

    with pytest.raises(PdfProviderUnavailable, match="missing pypdfium2"):
        main._validate_runtime_dependencies()
