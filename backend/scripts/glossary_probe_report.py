"""Build a reviewable off/on report from compatible probe artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from probe_sources import compare_results


def _key(source: dict[str, Any]) -> tuple[Any, Any, Any]:
    return source.get("doc_id"), source.get("slug"), source.get("chunk_index")


def _normalized_slug(slug: Any) -> str:
    value = "" if slug is None else str(slug)
    return "" if value.strip().lower() in {"", "none", "null"} else value


def _source_key_variants(source: dict[str, Any]) -> tuple[str, ...]:
    doc_id, slug, chunk_index = _key(source)
    normalized_slug = _normalized_slug(slug)
    canonical = f"{doc_id}|{normalized_slug}|{chunk_index}"
    if normalized_slug:
        return (canonical,)
    return tuple(
        dict.fromkeys(
            (
                canonical,
                f"{doc_id}||{chunk_index}",
                f"{doc_id}|None|{chunk_index}",
                f"{doc_id}|null|{chunk_index}",
            )
        )
    )


def _source(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": value.get("doc_id"),
        "slug": value.get("slug"),
        "chunk_index": value.get("chunk_index"),
    }


def _stage_sources(case: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    if stage == "raw_candidates":
        for item in case.get(stage, []):
            source = _source(item.get("source") or {})
            values.append({**source, "rank": item.get("rank"), "title": item.get("title", "")})
        return values
    for index, item in enumerate(case.get(stage, []), 1):
        source = _source(item.get("source") or {})
        values.append({**source, "rank": index, "title": item.get("title", "")})
    return values


def _by_key(values: list[dict[str, Any]]) -> dict[tuple[Any, Any, Any], dict[str, Any]]:
    return {_key(value): value for value in values}


def _group_key(source: dict[str, Any]) -> tuple[Any, Any]:
    return source.get("doc_id"), source.get("chunk_index")


def _relevance(case: dict[str, Any], source: dict[str, Any]) -> str:
    values = _relevance_labels(case, source)
    if len(values) == 1:
        return next(iter(values))
    return "unresolved"


def _relevance_labels(case: dict[str, Any], source: dict[str, Any]) -> set[str]:
    labels = case.get("relevance") or {}
    return {
        value
        for source_key in _source_key_variants(source)
        for value in [labels.get(source_key)]
        if value in {"relevant", "irrelevant", "unresolved"}
    }


def _source_key_text(source: dict[str, Any]) -> str:
    doc_id, slug, chunk_index = _key(source)
    return f"{doc_id}|{_normalized_slug(slug)}|{chunk_index}"


def _block_sources(block: dict[str, Any]) -> list[dict[str, Any]]:
    values = block.get("source_keys") or ([block["source"]] if block.get("source") else [])
    return [_source(value) for value in values if isinstance(value, dict)]


def _primary_source(block: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(block.get("source"), dict):
        return _source(block["source"])
    sources = _block_sources(block)
    return sources[0] if sources else None


def _expected_documents(case: dict[str, Any], labels: dict[str, Any]) -> list[str]:
    explicit = case.get("expected_documents")
    if explicit is not None:
        values = explicit if isinstance(explicit, list) else [explicit]
        result: list[str] = []
        for value in values:
            if isinstance(value, dict):
                value = value.get("doc_id")
            if value is not None and str(value) not in result:
                result.append(str(value))
        return result

    result = []
    for source_key, label in labels.items():
        if label != "relevant":
            continue
        doc_id = str(source_key).split("|", 1)[0]
        if doc_id not in result:
            result.append(doc_id)
    return result


def _quality_applicable(case: dict[str, Any]) -> bool:
    category = case.get("category")
    return bool(
        category in {"positive", "compound"}
        or "expected_documents" in case
        or case.get("relevance")
    )


def _quality_label_snapshot(case: dict[str, Any]) -> dict[str, Any]:
    labels = case.get("relevance") or {}
    mandatory_sources = case.get("mandatory_sources") or []
    return {
        "relevance": labels,
        "expected_documents": sorted(_expected_documents(case, labels)),
        "mandatory_sources": sorted(
            _source_key_text(_source(value)) if isinstance(value, dict) else str(value)
            for value in mandatory_sources
        ),
    }


def quality_metrics(
    case: dict[str, Any], *, precision_k: int = 5, recall_k: int = 10
) -> dict[str, Any]:
    """Calculate label-dependent quality metrics from final ranked blocks.

    Precision uses the primary source of each block and always divides by
    ``precision_k``; missing result positions therefore count as zero. Recall
    uses all source keys carried by the first ``recall_k`` blocks, so merged
    sibling sources can satisfy document recall. Missing labels never become
    an accidental zero-quality PASS: they produce ``BLOCKED``.
    """
    if precision_k <= 0 or recall_k <= 0:
        raise ValueError("precision_k and recall_k must be positive")

    labels = case.get("relevance") or {}
    blocks = case.get("final_blocks") or []
    precision_blocks = blocks[:precision_k]
    recall_blocks = blocks[:recall_k]
    unresolved_sources: list[str] = []
    conflicting_sources: list[str] = []
    relevant_blocks = 0
    irrelevant_blocks = 0
    for block in precision_blocks:
        source = _primary_source(block)
        if source is None:
            unresolved_sources.append("None|None|None")
            continue
        if len(_relevance_labels({"relevance": labels}, source)) > 1:
            conflicting_sources.append(_source_key_text(source))
            continue
        label = _relevance({"relevance": labels}, source)
        if label == "relevant":
            relevant_blocks += 1
        elif label == "irrelevant":
            irrelevant_blocks += 1
        else:
            unresolved_sources.append(_source_key_text(source))

    expected_documents = _expected_documents(case, labels)
    found_document_set = {
        str(source.get("doc_id"))
        for block in recall_blocks
        for source in _block_sources(block)
        if source.get("doc_id") is not None
    }
    found_documents = [doc for doc in expected_documents if doc in found_document_set]
    missing_documents = [doc for doc in expected_documents if doc not in found_document_set]

    mandatory_sources = case.get("mandatory_sources") or []
    mandatory_keys = {
        _source_key_text(_source(value)) if isinstance(value, dict) else str(value)
        for value in mandatory_sources
    }
    found_source_keys = {
        _source_key_text(source)
        for block in recall_blocks
        for source in _block_sources(block)
    }
    mandatory_missing = sorted(mandatory_keys - found_source_keys)

    blocking_reasons: list[str] = []
    if conflicting_sources:
        blocking_reasons.append("conflicting_relevance_labels")
    if unresolved_sources:
        blocking_reasons.append("unresolved_precision_source")
    requires_recall = bool(
        case.get("category") in {"positive", "compound"}
        or "expected_documents" in case
        or any(label == "relevant" for label in labels.values())
    )
    if requires_recall and not expected_documents:
        blocking_reasons.append("expected_documents_missing")
    if requires_recall and expected_documents and not found_documents:
        blocking_reasons.append("expected_document_not_found")
    if mandatory_missing:
        blocking_reasons.append("mandatory_source_missing")

    recall = None
    if expected_documents:
        recall = round(len(found_documents) / len(expected_documents), 6)
    return {
        "status": "BLOCKED" if blocking_reasons else "READY",
        "precision_at_5": round(relevant_blocks / precision_k, 6),
        "recall_at_10": recall,
        "relevant_blocks_at_5": relevant_blocks,
        "irrelevant_blocks_at_5": irrelevant_blocks,
        "evaluated_blocks_at_5": len(precision_blocks),
        "expected_documents": expected_documents,
        "found_documents": found_documents,
        "missing_documents": missing_documents,
        "mandatory_missing_sources": mandatory_missing,
        "unresolved_sources": unresolved_sources,
        "conflicting_sources": sorted(set(conflicting_sources)),
        "blocking_reasons": blocking_reasons,
    }


def compare_quality(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Compare Precision@5/Recall@10 without treating missing labels as PASS."""
    compare_results(current, baseline)
    baseline_cases = {case["id"]: case for case in baseline["cases"]}
    current_cases = {case["id"]: case for case in current["cases"]}
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    blocked: list[str] = []
    counts = {"pass": 0, "failed": 0, "blocked": 0, "not_applicable": 0}

    for case_id in sorted(baseline_cases):
        before = baseline_cases[case_id]
        after = current_cases[case_id]
        if not _quality_applicable(before) and not _quality_applicable(after):
            counts["not_applicable"] += 1
            records.append({"case_id": case_id, "status": "NOT_APPLICABLE"})
            continue

        before_labels = before.get("relevance") or {}
        after_labels = after.get("relevance") or {}
        before_contract = _quality_label_snapshot(before)
        after_contract = _quality_label_snapshot(after)
        label_case = dict(after)
        label_case["relevance"] = after_labels or before_labels
        if "expected_documents" not in label_case and "expected_documents" in before:
            label_case["expected_documents"] = before["expected_documents"]
        baseline_metrics = quality_metrics({**before, "relevance": label_case["relevance"]})
        current_metrics = quality_metrics(label_case)
        reasons: list[str] = []
        if before_labels != after_labels:
            reasons.append("labels_differ_between_off_and_on")
        if before_contract["expected_documents"] != after_contract["expected_documents"]:
            reasons.append("expected_documents_differ_between_off_and_on")
        if before_contract["mandatory_sources"] != after_contract["mandatory_sources"]:
            reasons.append("mandatory_sources_differ_between_off_and_on")
        if baseline_metrics["status"] == "BLOCKED" or current_metrics["status"] == "BLOCKED":
            reasons.extend(
                f"{side}_{reason}"
                for side, metrics in (("baseline", baseline_metrics), ("current", current_metrics))
                for reason in metrics["blocking_reasons"]
            )
        if reasons:
            status = "BLOCKED"
            blocked.append(case_id)
            counts["blocked"] += 1
        else:
            if current_metrics["precision_at_5"] < baseline_metrics["precision_at_5"]:
                reasons.append("precision_regression")
            if (
                current_metrics["recall_at_10"] is not None
                and baseline_metrics["recall_at_10"] is not None
                and current_metrics["recall_at_10"] < baseline_metrics["recall_at_10"]
            ):
                reasons.append("recall_regression")
            if current_metrics["irrelevant_blocks_at_5"] > baseline_metrics["irrelevant_blocks_at_5"]:
                reasons.append("irrelevant_blocks_increased")
            if current_metrics["mandatory_missing_sources"]:
                reasons.append("mandatory_source_missing")
            status = "FAIL" if reasons else "PASS"
            counts["failed" if status == "FAIL" else "pass"] += 1
            if status == "FAIL":
                failures.append(case_id)

        records.append({
            "case_id": case_id,
            "status": status,
            "reasons": reasons,
            "precision_delta": round(
                current_metrics["precision_at_5"] - baseline_metrics["precision_at_5"], 6
            ),
            "recall_delta": (
                None
                if current_metrics["recall_at_10"] is None
                or baseline_metrics["recall_at_10"] is None
                else round(current_metrics["recall_at_10"] - baseline_metrics["recall_at_10"], 6)
            ),
            "baseline": baseline_metrics,
            "current": current_metrics,
        })

    status = "BLOCKED" if blocked else ("FAIL" if failures else "PASS")
    return {
        "status": status,
        "precision_k": 5,
        "recall_k": 10,
        "summary": counts,
        "blocked_cases": blocked,
        "failed_cases": failures,
        "cases": records,
    }


def _case_changes(current: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    stages = ("raw_candidates", "merged_blocks", "top_k_blocks", "final_blocks")
    before = {stage: _by_key(_stage_sources(baseline, stage)) for stage in stages}
    after = {stage: _by_key(_stage_sources(current, stage)) for stage in stages}
    keys = set().union(*(set(values) for values in before.values()), *(set(values) for values in after.values()))
    changes: list[dict[str, Any]] = []
    for key in sorted(keys, key=str):
        source = _source(dict(zip(("doc_id", "slug", "chunk_index"), key)))
        before_raw = before["raw_candidates"].get(key)
        after_raw = after["raw_candidates"].get(key)
        before_final = before["final_blocks"].get(key)
        after_final = after["final_blocks"].get(key)
        before_top = before["top_k_blocks"].get(key)
        after_top = after["top_k_blocks"].get(key)
        event: str | None = None
        if before_raw and not after_raw:
            same_group_after = any(_group_key(item) == _group_key(source) for item in after["raw_candidates"].values())
            event = "sibling_change" if same_group_after else "raw_disappearance"
        elif not before_raw and after_raw:
            event = "new_source"
        elif before_final and not after_final and after_top:
            event = "context_filter"
        elif before_top and not after_top and after_raw:
            event = "top_k_exit"
        elif before_raw and after_raw and before_raw.get("rank") != after_raw.get("rank"):
            event = "rank_shift"
        if event is None:
            continue
        changes.append({
            "case_id": current.get("id", baseline.get("id")),
            "query": current.get("query", baseline.get("query")),
            "api": current.get("api", baseline.get("api")),
            "mode": current.get("mode", baseline.get("mode")),
            "canonical": [
                term.get("canonical")
                for term in (current.get("query_plan") or baseline.get("query_plan") or {}).get("applied_terms", [])
            ],
            "source": source,
            "title_off": (before_raw or before_final or {}).get("title", ""),
            "title_on": (after_raw or after_final or {}).get("title", ""),
            "raw_rank_off": (before_raw or {}).get("rank"),
            "raw_rank_on": (after_raw or {}).get("rank"),
            "final_rank_off": (before_final or {}).get("rank"),
            "final_rank_on": (after_final or {}).get("rank"),
            "matched_terms_off": (before_final or {}).get("matched_terms", []),
            "matched_terms_on": (after_final or {}).get("matched_terms", []),
            "event": event,
            "relevance": _relevance(current if current.get("relevance") else baseline, source),
        })
    return changes


def build_report(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    comparison = compare_results(current, baseline)
    baseline_cases = {case["id"]: case for case in baseline["cases"]}
    current_cases = {case["id"]: case for case in current["cases"]}
    changes: list[dict[str, Any]] = []
    for case_id in sorted(baseline_cases):
        changes.extend(_case_changes(current_cases[case_id], baseline_cases[case_id]))
    summary = {"total": len(changes), "relevant": 0, "irrelevant": 0, "unresolved": 0}
    for item in changes:
        summary[item["relevance"]] += 1
    return {
        "schema_version": 1,
        "comparison": comparison,
        "summary": summary,
        "changes": changes,
        "quality": compare_quality(current, baseline),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    current = json.loads(args.current.read_text(encoding="utf-8"))
    report = build_report(current, baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
