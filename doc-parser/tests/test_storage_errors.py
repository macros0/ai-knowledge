import errno

import pytest
from docparser import parse_document_result
from docparser.embedded import process_embedded
from docparser.storage_errors import is_storage_full_error

from tests.mail_fixtures import unicode_msg


def _error(kind):
    if kind == "windows":
        error = OSError("synthetic disk full")
        error.winerror = 112
        return error
    return OSError(errno.ENOSPC, "synthetic disk full")


@pytest.mark.parametrize("kind", ["posix", "windows"])
@pytest.mark.parametrize("shape", ["embedded-eml", "msg"])
def test_attachment_disk_error_is_not_malformed_child(tmp_path, monkeypatch, kind, shape):
    from docparser import embedded

    error = _error(kind)

    def full(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(embedded, "_write_generated_file", full)
    with pytest.raises(OSError) as caught:
        if shape == "embedded-eml":
            process_embedded(b"From: synthetic@example.test\nSubject: Fact\n\nInspection takes 23 days.",
                             "child.eml", attachments_dir=tmp_path / "attachments")
        else:
            source = tmp_path / "root.msg"
            source.write_bytes(unicode_msg(subject="Fact", body="Inspection takes 23 days.", attachments={"proof.bin": b"proof"}))
            parse_document_result(source, attachments_dir=tmp_path / "attachments")
    assert caught.value is error


def test_wrapped_disk_error_and_cycle_are_classified_without_text_guessing():
    wrapped = ValueError("library wrapper")
    wrapped.__cause__ = _error("posix")
    assert is_storage_full_error(wrapped)
    cycle = ValueError("No space left on device")
    cycle.__cause__ = cycle
    assert not is_storage_full_error(cycle)
    assert not is_storage_full_error(OSError(errno.EACCES, "No space left on device"))


def test_quota_and_windows_handle_full_are_disk_exhaustion():
    assert is_storage_full_error(OSError(getattr(errno, "EDQUOT", 122), "quota"))
    error = OSError("synthetic handle full")
    error.winerror = 39
    assert is_storage_full_error(error)
