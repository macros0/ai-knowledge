"""Stable summary problem for parser warnings and existing generation diagnostics."""
from __future__ import annotations

from app.services.problem_codes import (
    ATTACHMENT_PARTIAL_RESULT,
    ENCRYPTED_MAIL,
    INDEX_PARTIAL_FAILURE,
    LLM_CLASSIFIER_FALLBACK,
    LLM_PARTIAL_RESULT,
    MAIL_TEXT_PARTIAL_RESULT,
    NO_CONCEPTS,
    NO_TEXT_LAYER,
    PROTECTED_MAIL,
    TEXT_PARTIAL_RESULT,
    UNSUPPORTED_MAIL_CLASS,
    UNSUPPORTED_RTF_BODY,
)


def summarize_problems(
    parse_warnings: list[dict] | None,
    generation_problem: str | None,
    index_problem: str | None,
    text_problem: str | None,
) -> str | None:
    """Return one stable UI summary without discarding detailed warnings."""
    candidates = {index_problem, generation_problem, text_problem}
    for code in (
        INDEX_PARTIAL_FAILURE,
        LLM_PARTIAL_RESULT,
        LLM_CLASSIFIER_FALLBACK,
    ):
        if code in candidates:
            return code
    warning_codes = {warning.get("code") for warning in parse_warnings or []}
    for code in (ENCRYPTED_MAIL, PROTECTED_MAIL, UNSUPPORTED_MAIL_CLASS, UNSUPPORTED_RTF_BODY):
        if code in warning_codes:
            return code
    for code in (NO_CONCEPTS, NO_TEXT_LAYER):
        if code in candidates:
            return code
    if "text_limit_exceeded" in warning_codes:
        return TEXT_PARTIAL_RESULT
    if warning_codes & {"mail_decode_recovered", "mail_alternative_mismatch"}:
        return MAIL_TEXT_PARTIAL_RESULT
    if parse_warnings:
        return ATTACHMENT_PARTIAL_RESULT
    return next((code for code in (index_problem, generation_problem, text_problem) if code), None)


def source_extraction_status(warnings: list[dict], fallback: str) -> str:
    codes = {warning.get("code") for warning in warnings}
    for code, status in ((ENCRYPTED_MAIL, "encrypted"), (PROTECTED_MAIL, "protected"),
                         (UNSUPPORTED_MAIL_CLASS, "unsupported"), (UNSUPPORTED_RTF_BODY, "unsupported")):
        if code in codes:
            return status
    return fallback
