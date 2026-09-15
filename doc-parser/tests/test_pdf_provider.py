from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image
from pypdf import PdfWriter

import docparser.pdf_parser as pdf_parser
from docparser import ParseError
from docparser.pdf_provider import PdfAttachment, PdfImage, PdfParseError, get_pdf_provider_metadata
from docparser.pypdf_pdfium_provider import PypdfPdfiumDocument, PypdfPdfiumProvider

from tests.fixtures import make_pdf, make_pdf_scanned_like, make_pdf_with_image


class FakeProvider:
    name = "pypdf-pdfium"

    def version(self) -> str:
        return "fake-provider-1"

    def metadata(self) -> dict[str, str]:
        return {
            "provider": self.name,
            "pypdf_version": "fake-pypdf",
            "pdfium_version": "fake-pdfium",
        }


def test_provider_metadata_has_no_path_or_secret(monkeypatch):
    monkeypatch.setattr("docparser.pdf_provider.get_pdf_provider", lambda: FakeProvider())

    metadata = get_pdf_provider_metadata()

    assert metadata["provider"] == "pypdf-pdfium"
    assert set(metadata) == {"provider", "pypdf_version", "pdfium_version"}
    assert all("/" not in str(value) for value in metadata.values())


def test_value_objects_preserve_raw_pdf_data():
    image = PdfImage(data=b"image", name="figure", extension=".png")
    attachment = PdfAttachment(name="source.docx", data=b"document")

    assert image.extension == ".png"
    assert attachment.data == b"document"


def test_provider_extracts_text_with_zero_based_page_index(tmp_path):
    source = make_pdf(tmp_path / "text.pdf", "Provider text")
    document = PypdfPdfiumProvider().open(source)
    try:
        assert document.page_count() == 1
        assert "Provider text" in document.extract_text(0)
    finally:
        document.close()


def test_provider_returns_real_pdf_attachments(tmp_path):
    source = tmp_path / "attachment.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_attachment("proof.txt", b"proof")
    with source.open("wb") as output:
        writer.write(output)

    document = PypdfPdfiumProvider().open(source)
    try:
        assert document.attachments() == [PdfAttachment(name="proof.txt", data=b"proof")]
    finally:
        document.close()


def test_provider_extracts_embedded_image_with_detected_extension(tmp_path):
    source = make_pdf_with_image(tmp_path / "image.pdf")
    document = PypdfPdfiumProvider().open(source)
    try:
        images = document.extract_images(0)
    finally:
        document.close()

    assert len(images) == 1
    assert images[0].data
    assert images[0].extension == ".png"


def test_provider_renders_scanned_page_as_jpeg(tmp_path):
    source = make_pdf_scanned_like(tmp_path / "scan.pdf")
    document = PypdfPdfiumProvider().open(source)
    try:
        rendered = document.render_page_jpeg(0, dpi=144, quality=85)
    finally:
        document.close()

    with Image.open(io.BytesIO(rendered)) as image:
        assert image.format == "JPEG"
        assert image.width > 0
        assert image.height > 0


def test_provider_closes_native_page_resources_when_encoding_fails(tmp_path):
    class FailingImage:
        mode = "RGB"

        def __init__(self):
            self.closed = False

        def save(self, output, *, format, quality):
            raise OSError("encode failed")

        def close(self):
            self.closed = True

    class FakeBitmap:
        def __init__(self, image):
            self.image = image
            self.closed = False

        def to_pil(self):
            return self.image

        def close(self):
            self.closed = True

    class FakePage:
        def __init__(self, bitmap):
            self.bitmap = bitmap
            self.closed = False

        def render(self, *, scale):
            return self.bitmap

        def close(self):
            self.closed = True

    class FakePdfiumDocument:
        def __init__(self, page):
            self.page = page

        def __getitem__(self, index):
            return self.page

        def close(self):
            pass

    image = FailingImage()
    bitmap = FakeBitmap(image)
    page = FakePage(bitmap)
    document = PypdfPdfiumProvider().open(make_pdf_scanned_like(tmp_path / "scan.pdf"))
    document._pdfium_document = FakePdfiumDocument(page)
    try:
        with pytest.raises(PdfParseError, match="Unable to render page 1"):
            document.render_page_jpeg(0, dpi=144, quality=85)
    finally:
        document.close()

    assert image.closed
    assert bitmap.closed
    assert page.closed


def test_pdfium_render_calls_do_not_overlap(monkeypatch, tmp_path):
    active = maximum = 0
    original = PypdfPdfiumDocument._render_page_unlocked
    first_entered = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()

    def observed(self, page_index, dpi, quality):
        nonlocal active, maximum
        with state_lock:
            active += 1
            maximum = max(maximum, active)
            is_first = active == 1
        if is_first:
            first_entered.set()
            assert release_first.wait(timeout=3)
        try:
            return original(self, page_index, dpi, quality)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(PypdfPdfiumDocument, "_render_page_unlocked", observed)
    first_document = PypdfPdfiumProvider().open(make_pdf_scanned_like(tmp_path / "first.pdf"))
    second_document = PypdfPdfiumProvider().open(make_pdf_scanned_like(tmp_path / "second.pdf"))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_document.render_page_jpeg, 0, dpi=144, quality=85)
            assert first_entered.wait(timeout=3)
            second = pool.submit(second_document.render_page_jpeg, 0, dpi=144, quality=85)
            time.sleep(0.1)
            assert maximum == 1
            release_first.set()
            first.result(timeout=3)
            second.result(timeout=3)
    finally:
        release_first.set()
        first_document.close()
        second_document.close()

    assert maximum == 1


def test_parse_pdf_closes_provider_document_on_save_failure(monkeypatch, tmp_path):
    class FakePdfDocument:
        closed = False

        def page_count(self):
            return 1

        def extract_text(self, page_index):
            return ""

        def extract_images(self, page_index):
            return [PdfImage(data=b"image", name="figure", extension=".png")]

        def render_page_jpeg(self, page_index, *, dpi, quality):
            raise AssertionError("rendering must not be reached when an image exists")

        def attachments(self):
            return []

        def close(self):
            self.closed = True

    class FakeProvider:
        def __init__(self, document):
            self.document = document

        def open(self, path):
            return self.document

    document = FakePdfDocument()
    monkeypatch.setattr(
        pdf_parser,
        "save_image_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        pdf_parser.parse_pdf(
            tmp_path / "source.pdf",
            attachments_dir=tmp_path,
            provider_factory=lambda: FakeProvider(document),
        )

    assert document.closed is True


def test_parse_pdf_normalizes_provider_open_error(tmp_path):
    class FailingProvider:
        def open(self, path):
            raise PdfParseError("broken xref")

    with pytest.raises(ParseError, match=r"source\.pdf: cannot open: broken xref"):
        pdf_parser.parse_pdf(tmp_path / "source.pdf", provider_factory=FailingProvider)
