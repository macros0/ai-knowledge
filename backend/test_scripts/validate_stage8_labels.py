"""Validate subject-matter labels before a Stage 8 acceptance rerun."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


_ALLOWED_LABELS = {"relevant", "irrelevant", "unresolved"}
_QUALITY_CATEGORIES = {"positive", "compound"}


def _error(code: str, message: str, case_id: str | None = None) -> dict[str, str]:
    value = {"code": code, "message": message}
    if case_id is not None:
        value["case_id"] = case_id
    return value


def _valid_source_key(value: str) -> bool:
    parts = value.split("|")
    # A chunk has no concept slug.  Its stable text form is either
    # ``doc_id||chunk_index`` (canonical) or ``doc_id|None|chunk_index`` from
    # older probe artifacts.  Both still carry the complete three-part
    # identity and must be accepted by the review validator.
    if len(parts) != 3 or not parts[0] or not parts[2]:
        return False
    try:
        int(parts[2])
    except (TypeError, ValueError):
        return False
    return True


def _canonical_source_key(value: str) -> str:
    """Return one identity for canonical and legacy chunk source spellings."""
    parts = value.split("|")
    slug = parts[1]
    if slug.strip().lower() in {"", "none", "null"}:
        slug = ""
    return f"{parts[0]}|{slug}|{int(parts[2])}"


def _validate_source(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not value.get("doc_id") or "slug" not in value:
        return False
    if value.get("slug") is not None and not isinstance(value.get("slug"), str):
        return False
    try:
        int(value["chunk_index"])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def validate_cases(payload: dict[str, Any], *, require_approved: bool = False) -> dict[str, Any]:
    """Return a machine-readable validation result without changing the cases file.

    Positive and compound cases are acceptance-quality cases. They need explicit
    source labels and expected documents; negative, legacy, and isolation cases
    remain valid without subject-matter labels unless labels are supplied.
    """
    errors: list[dict[str, str]] = []
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list):
        return {
            "status": "BLOCKED",
            "summary": {"total": 0, "quality_applicable": 0, "complete": 0, "incomplete": 0},
            "errors": [_error("cases_missing", "cases must be a list")],
        }

    approval = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
    if require_approved and approval.get("approved") is not True:
        errors.append(_error("labels_not_approved", "labels.approved must be true"))

    seen_ids: set[str] = set()
    quality_applicable = complete = incomplete = 0
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(_error("invalid_case", f"case at index {index} must be an object"))
            incomplete += 1
            continue
        case_id = str(case.get("id") or f"<index:{index}>")
        if case_id in seen_ids:
            errors.append(_error("duplicate_case_id", f"duplicate case id: {case_id}", case_id))
        seen_ids.add(case_id)

        relevance = case.get("relevance")
        if relevance is not None and not isinstance(relevance, dict):
            errors.append(_error("invalid_relevance", "relevance must be an object", case_id))
            relevance = {}
        relevance = relevance or {}
        labels_by_source: dict[str, str] = {}
        conflicting_sources: set[str] = set()
        for source_key, label in relevance.items():
            if not isinstance(source_key, str) or not _valid_source_key(source_key):
                errors.append(_error("invalid_relevance_key", f"invalid source key: {source_key!r}", case_id))
            else:
                canonical_key = _canonical_source_key(source_key)
                previous_label = labels_by_source.get(canonical_key)
                if previous_label is not None and previous_label != label:
                    if canonical_key not in conflicting_sources:
                        errors.append(
                            _error(
                                "conflicting_relevance_labels",
                                "multiple relevance labels for the same source identity",
                                case_id,
                            )
                        )
                        conflicting_sources.add(canonical_key)
                else:
                    labels_by_source[canonical_key] = label
            if label not in _ALLOWED_LABELS:
                errors.append(_error("invalid_relevance_value", f"invalid relevance value: {label!r}", case_id))

        mandatory_sources = case.get("mandatory_sources")
        if mandatory_sources is not None and not isinstance(mandatory_sources, list):
            errors.append(_error("invalid_mandatory_sources", "mandatory_sources must be a list", case_id))
            mandatory_sources = []
        for source in mandatory_sources or []:
            if not _validate_source(source):
                errors.append(_error("invalid_mandatory_source", f"invalid mandatory source: {source!r}", case_id))

        is_quality_case = case.get("category") in _QUALITY_CATEGORIES
        if not is_quality_case:
            continue
        quality_applicable += 1
        case_errors_before = len(errors)
        if not relevance:
            errors.append(_error("relevance_missing", "positive/compound case needs relevance labels", case_id))
        elif any(label == "unresolved" for label in relevance.values()):
            errors.append(_error("relevance_unresolved", "relevance contains unresolved labels", case_id))

        expected_documents = case.get("expected_documents")
        if not isinstance(expected_documents, list) or not expected_documents or not all(
            isinstance(document, (str, dict)) and (document.get("doc_id") if isinstance(document, dict) else document)
            for document in expected_documents
        ):
            errors.append(_error("expected_documents_missing", "positive/compound case needs expected_documents", case_id))

        if len(errors) == case_errors_before:
            complete += 1
        else:
            incomplete += 1

    if errors:
        status = "BLOCKED"
    elif quality_applicable != complete:
        status = "BLOCKED"
    else:
        status = "READY"
    return {
        "status": status,
        "summary": {
            "total": len(cases),
            "quality_applicable": quality_applicable,
            "complete": complete,
            "incomplete": incomplete,
        },
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("glossary-probe-cases.json"))
    parser.add_argument("--require-approved", action="store_true")
    args = parser.parse_args(argv)
    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    result = validate_cases(payload, require_approved=args.require_approved)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
