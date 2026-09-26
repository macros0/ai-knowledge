import pytest

from docparser.source_model import ParseContext

from app.services.errors import DomainError
from app.services.pipeline import validate_resume_parser_version


def test_legacy_staging_without_parser_version_can_resume():
    validate_resume_parser_version({}, "mail-sources-v1")


def test_staging_from_another_parser_version_requires_regenerate():
    with pytest.raises(DomainError) as raised:
        validate_resume_parser_version({"parser_version": "mail-sources-v0"}, "mail-sources-v1")

    assert raised.value.code == "parser_version_mismatch"


def test_switching_mail_import_mode_requires_new_parser_checkpoint():
    enabled = ParseContext("container.pdf", mail_enabled=True).parser_version
    disabled = ParseContext("container.pdf", mail_enabled=False).parser_version

    assert enabled != disabled
    for old, current in ((enabled, disabled), (disabled, enabled)):
        with pytest.raises(DomainError) as raised:
            validate_resume_parser_version({"parser_version": old}, current)
        assert raised.value.code == "parser_version_mismatch"


def test_legacy_staging_without_source_file_hash_can_resume():
    from app.services.pipeline import validate_resume_source_file_hash

    validate_resume_source_file_hash({}, "a" * 64)


def test_changed_source_file_requires_full_regenerate():
    from app.services.pipeline import validate_resume_source_file_hash

    with pytest.raises(DomainError) as raised:
        validate_resume_source_file_hash({"source_file_hash": "a" * 64}, "b" * 64)

    assert raised.value.code == "partial_regeneration_unavailable"
