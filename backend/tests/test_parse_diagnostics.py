"""Regression tests for parse-warning problem aggregation."""
import pytest

from app.services.parse_diagnostics import summarize_problems


def test_index_problem_has_priority_over_llm_and_parse_warning():
    assert summarize_problems(
        [{"code": "mail_parse_failed"}],
        generation_problem="llm_partial_result",
        index_problem="index_partial_failure",
        text_problem="no_text_layer",
    ) == "index_partial_failure"


def test_parse_warning_is_reported_when_no_higher_problem_exists():
    assert summarize_problems(
        [{"code": "mail_parse_failed", "source_id": "root/1"}],
        generation_problem=None,
        index_problem=None,
        text_problem=None,
    ) == "attachment_partial_result"


def test_empty_parse_warnings_do_not_change_existing_text_problem():
    assert summarize_problems([], None, None, "no_concepts") == "no_concepts"


def test_text_limit_warning_has_its_own_partial_result():
    assert summarize_problems(
        [{"code": "text_limit_exceeded", "source_id": "root"}],
        None,
        None,
        None,
    ) == "text_partial_result"


def test_encrypted_mail_warning_has_a_specific_problem_code():
    assert summarize_problems(
        [{"code": "encrypted_mail", "source_id": "root"}],
        None,
        None,
        None,
    ) == "encrypted_mail"


@pytest.mark.parametrize("code", ["encrypted_mail", "protected_mail", "unsupported_mail_class", "unsupported_rtf_body"])
@pytest.mark.parametrize("empty_problem", ["no_text_layer", "no_concepts", None])
def test_unavailable_mail_reason_is_not_hidden_by_empty_extraction(code, empty_problem):
    assert summarize_problems([{"code": code, "source_id": "root"}], empty_problem, None, None) == code

from app.services.problem_codes import ATTACHMENT_PARTIAL_RESULT, problem_message


def test_attachment_partial_result_has_stable_user_message():
    assert "влож" in problem_message(ATTACHMENT_PARTIAL_RESULT).lower()


def test_unsupported_rtf_body_has_actionable_message():
    message = problem_message("unsupported_rtf_body")
    assert "RTF" in message
    assert "скач" in message.lower()

from app.services.registry import DocumentRegistry


def test_document_registry_persists_parse_diagnostics():
    registry = DocumentRegistry()
    registry.create("diag000000000001", "forward.eml", "message/rfc822", 12)
    registry.update(
        "diag000000000001",
        parser_version="mail-sources-v1",
        parse_warnings=[{"code": "mail_parse_failed", "source_id": "root/1"}],
    )

    stored = registry.get("diag000000000001")

    assert stored["parser_version"] == "mail-sources-v1"
    assert stored["parse_warnings"] == [{"code": "mail_parse_failed", "source_id": "root/1"}]
