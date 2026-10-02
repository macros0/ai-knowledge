"""Read-only profiler of the real documents-mode chat retrieval/widening path.

History writes are replaced locally; HTTP/auth, history persistence and browser
paint are outside these measurements. Only digests/counts/timings leave memory.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PROBE_DIGEST = sha256(Path(__file__).read_bytes()).hexdigest()
sys.path.insert(0, str(ROOT / "backend"))


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             default=str, separators=(",", ":")).encode()).hexdigest()


def summarize(values):
    ordered = sorted(values)
    result = {"n": len(values), "median": statistics.median(values),
              "min": min(values), "max": max(values)}
    if len(values) >= 30:
        result["p95"] = ordered[math.ceil(.95 * len(values)) - 1]
    return result


def finish_report(manifest, output, *, strict=False):
    """Preserve evidence before returning a failing acceptance exit status."""
    failures = []
    environment_failed = False
    for criterion in ("corpus_unchanged", "code_unchanged"):
        if manifest.get(criterion) is False:
            environment_failed = True
            failures.append({"criterion": criterion})
    for index, case in enumerate(manifest["cases"]):
        for criterion, passed in (("stable_sources", case["stable_sources"]),
                                  ("output_equivalence", case.get("output_equivalent", True))):
            if not passed:
                failures.append({"case": index, "query": case["query"], "criterion": criterion})
        if any(sample["reference_counts"]["found"] < sample["reference_counts"]["expected"]
               for sample in case["samples"]):
            failures.append({"case": index, "query": case["query"], "criterion": "mandatory_sources"})
    manifest["acceptance"] = {"passed": not failures, "strict": strict,
                              "failures": failures, "performance_budget": "not_set"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(environment_failed or (strict and bool(failures)))


def corpus_state(store):
    from sqlalchemy import select
    from app.db.models import Document, DocumentChunk, DocumentGenerationState, DocumentSource, OkfConcept
    from app.db.session import session_scope
    from app.services.glossary.snapshot import load_glossary_snapshot

    fingerprint = sha256()
    counts = {}
    with session_scope() as session:
        for model in (Document, DocumentGenerationState, DocumentSource, OkfConcept, DocumentChunk):
            columns = list(model.__table__.columns)
            statement = select(*columns).order_by(*model.__table__.primary_key.columns)
            count = 0
            for row in session.execute(statement):
                fingerprint.update(digest(list(row)).encode())
                count += 1
            counts[model.__tablename__] = count
    return {"sql_digest": fingerprint.hexdigest(), "counts": counts,
            "glossary_digest": digest(asdict(load_glossary_snapshot())),
            "qdrant_points": store.client.get_collection(store.collection).points_count}


def measure(request, settings, store, embedder, *, mandatory_sources=()):
    from sqlalchemy import event
    from app.api import chat as api
    from app.auth.models import User
    from app.db.session import get_engine
    from app.services import chat_history, retrieval_hydration
    from app.services.llm_profiles import request_scope

    durations = defaultdict(float)
    rounds = []
    counters = {"sql_count": 0, "source_tree_selects": 0, "hydrated_text_utf8_bytes": 0}
    snapshots = []
    blocks = []
    seen_candidates = set()
    sql_queries = {}
    sql_shapes = {}

    def timed(name, function, *, inspect=None):
        def run(*args, **kwargs):
            started = perf_counter()
            result = function(*args, **kwargs)
            elapsed = (perf_counter() - started) * 1000
            durations[name] += elapsed
            if inspect:
                inspect(result, kwargs, elapsed)
            return result
        return run

    def inspect_round(result, kwargs, elapsed):
        candidate_ids = {str(hit.point_id) for hit in result}
        rounds.append({"candidate_depth": kwargs["top_k"], "raw_count": len(result),
                       "qdrant_ms": elapsed,
                       "repeated_candidates": len(candidate_ids & seen_candidates)})
        seen_candidates.update(candidate_ids)

    def inspect_blocks(result, _kwargs, elapsed):
        blocks[:] = result[1]
        rounds[-1].update(filtered_count=len(blocks), filter_pipeline_ms=elapsed)

    def inspect_text(result, _kwargs, _elapsed):
        counters["hydrated_text_utf8_bytes"] += sum(
            len(hit.payload.get("content", "").encode()) for hit in result)

    def save_sources(_ref, _user, _sources, *, retrieval_metadata=None):
        if retrieval_metadata and "source_blocks" in retrieval_metadata:
            snapshots.append(digest(retrieval_metadata["source_blocks"]))

    def before_sql(_conn, _cursor, statement, _params, context, _many):
        # Reject writes even when a future caller accidentally adds one.
        if not statement.lstrip().upper().startswith(("SELECT", "BEGIN", "PRAGMA")):
            raise RuntimeError("Read-only probe rejected a non-read SQL statement")
        compiled = context.compiled
        if compiled not in sql_shapes:
            shape = str(compiled.statement) if compiled else statement
            table = re.search(r"\bFROM\s+([a-z_]+)", statement, re.I)
            sql_shapes[compiled] = (digest(shape), table.group(1) if table else "transaction",
                                    "text" if re.search(r"\bcontent\b", statement) else "metadata")
        context._source_perf_shape = sql_shapes[compiled]
        context._source_perf_bind_count = len(_params)
        context._source_perf_started = perf_counter()

    def after_sql(_conn, _cursor, statement, _params, context, _many):
        counters["sql_count"] += 1
        counters["source_tree_selects"] += int("FROM document_sources" in statement)
        elapsed = (perf_counter() - context._source_perf_started) * 1000
        durations["sql_execute_ms"] += elapsed
        fingerprint, table, purpose = context._source_perf_shape
        key = (len(rounds) - 1, fingerprint)
        entry = sql_queries.setdefault(key, {"round": key[0], "shape_digest": fingerprint,
                                             "table": table, "purpose": purpose,
                                             "count": 0, "execute_ms": 0.0,
                                             "max_bind_count": 0, "returned_rows": 0})
        entry["count"] += 1
        entry["execute_ms"] += elapsed
        entry["max_bind_count"] = max(entry["max_bind_count"], context._source_perf_bind_count)
        if _cursor.rowcount >= 0:
            entry["returned_rows"] += _cursor.rowcount

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", before_sql)
    event.listen(engine, "after_cursor_execute", after_sql)
    try:
        with ExitStack() as stack:
            for owner, name, metric, inspector in (
                (api, "prepare_query", "prepare_query_ms", None),
                (embedder, "embed", "embedding_ms", None),
                (store, "search_composite", "qdrant_ms", inspect_round),
                (api, "_filtered_chat_blocks", "filter_pipeline_ms", inspect_blocks),
                (api, "load_visible_retrieval_hits", "hydration_ms", None),
                (retrieval_hydration, "_enrich_retrieval_hits_in_session", "enrichment_ms", inspect_text),
                (api, "merge_and_format", "merge_ms", None),
                (api, "drop_unmatched_blocks", "lexical_filter_ms", None),
                (api, "drop_partial_title_matches", "title_filter_ms", None),
                (api, "snapshot_blocks", "snapshot_ms", None),
                (api, "ChatSource", "source_construction_ms", None),
            ):
                stack.enter_context(patch.object(owner, name, timed(metric, getattr(owner, name), inspect=inspector)))
            stack.enter_context(patch.object(chat_history, "save_attempt_sources", save_sources))
            stack.enter_context(patch.object(chat_history, "store_turn", side_effect=RuntimeError("History write forbidden")))
            stack.enter_context(patch.object(api, "_get_llm", side_effect=RuntimeError("LLM call forbidden")))
            stack.enter_context(patch.object(api, "_get_vector_store", return_value=store))
            stack.enter_context(patch.object(api, "_get_embedder", return_value=embedder))
            reference = chat_history.AttemptRef("performance-probe", 0, "performance-probe")
            started = perf_counter()
            with request_scope(on_sources=None, on_progress=None, deadline=None):
                response = api._answer(request, User(), settings, attempt_ref=reference)
            durations["answer_path_ms"] = (perf_counter() - started) * 1000
    finally:
        event.remove(engine, "before_cursor_execute", before_sql)
        event.remove(engine, "after_cursor_execute", after_sql)
    started = perf_counter()
    serialized = response.model_dump_json().encode()
    durations["json_serialization_ms"] = (perf_counter() - started) * 1000
    source_keys = {(source.doc_id, source.source_slug, source.chunk_index) for source in response.sources}
    mandatory_keys = {(source["doc_id"], source["slug"], source["chunk_index"]) for source in mandatory_sources}
    return {"timings_ms": dict(durations), "counts": {**counters, "sources": len(response.sources),
            "documents": len({source.doc_id for source in response.sources}), "response_bytes": len(serialized)},
            "reference_counts": {"expected": len(mandatory_keys), "found": len(mandatory_keys & source_keys)},
            "rounds": rounds, "sql_queries": list(sql_queries.values()),
            "sources_digest": digest([source.model_dump(mode="json") for source in response.sources]),
            "blocks_digest": digest(blocks), "snapshot_digests": snapshots,
            "limit_reached": response.search_limit_reached}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--query", nargs="+", default=["ЭЛН"])
    parser.add_argument("--depth", type=int, nargs="+", default=[40, 100, 200, 500])
    parser.add_argument("--mode", nargs="+", choices=["bm25", "hybrid"], default=["hybrid"])
    parser.add_argument("--glossary", nargs="+", choices=["off", "on"], default=["on"])
    parser.add_argument("--mail-mode", choices=["all", "exclude", "only"], default="all")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=2)
    comparison = parser.add_mutually_exclusive_group()
    comparison.add_argument("--compare-ref", help="Immutable Git baseline for alternating before/after hydration measurements")
    comparison.add_argument("--compare-hydration-file", type=Path,
                            help="Local frozen hydration source; its SHA256 is recorded as the baseline")
    parser.add_argument("--reference", type=Path, help="Optional existing mandatory-source reference; never overwritten")
    parser.add_argument("--strict", action="store_true", help="Fail on unstable/equivalent-output or mandatory-source gates")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0 or any(depth < 1 or depth > 500 for depth in args.depth):
        parser.error("repeat must be positive and depth must be within 1..500")
    if args.output.exists():
        parser.error("output already exists; preserve previous measurements")
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    from app.config import get_settings
    from app.db.session import get_engine
    from app.models.schemas import ChatRequest
    from app.services.embedder import Embedder
    from app.services.vector_store import VectorStore
    from app.services import retrieval_hydration

    baseline_function = None
    baseline_ref = None
    baseline_digest = None
    source = None
    if args.compare_ref:
        baseline_ref = subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{args.compare_ref}^{{commit}}"], cwd=ROOT, text=True).strip()
        source = subprocess.check_output(
            ["git", "show", f"{baseline_ref}:backend/app/services/retrieval_hydration.py"], cwd=ROOT)
    elif args.compare_hydration_file:
        source = args.compare_hydration_file.read_bytes()
    if source is not None:
        baseline_digest = sha256(source).hexdigest()
        namespace = {"__name__": "baseline_retrieval_hydration"}
        exec(compile(source, "baseline_retrieval_hydration.py", "exec"), namespace)
        baseline_function = namespace["_enrich_retrieval_hits_in_session"]
    git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

    # Block mutations in the canonical relational database for the whole run.
    from sqlalchemy import event
    engine = get_engine()

    def reject_writes(_conn, _cursor, statement, *_args):
        # PostgreSQL's read-only transaction mode also rejects FOR SHARE,
        # which the real hydration path needs to preserve generation consistency.
        if not statement.lstrip().upper().startswith(("SELECT", "BEGIN", "PRAGMA")):
            raise RuntimeError("Read-only probe rejected a non-read SQL statement")

    event.listen(engine, "before_cursor_execute", reject_writes)
    settings = get_settings()
    store, embedder = VectorStore(), Embedder()
    reference = json.loads(args.reference.read_text(encoding="utf-8")) if args.reference else {"cases": []}
    mandatory_by_query = {case["query"]: case.get("mandatory_sources", []) for case in reference["cases"]}
    if args.strict and args.reference and any(not mandatory_by_query.get(query) for query in args.query):
        parser.error("strict reference checking requires mandatory sources for every query")
    code_before = digest({str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
                          for path in sorted((ROOT / "backend/app").rglob("*.py"))})
    before = corpus_state(store)
    cases = []
    for query in args.query:
        for depth in args.depth:
            for mode in args.mode:
                for glossary in args.glossary:
                    request = ChatRequest(query=query, response_mode="documents", search_depth=depth,
                                          mode=mode, use_glossary=glossary == "on", mail_mode=args.mail_mode)
                    active_settings = settings.model_copy(update={"glossary_query_expansion_enabled": glossary == "on"})
                    # prepare_query and hydration use the same active Settings.
                    def once(baseline=False):
                        with ExitStack() as stack:
                            if baseline:
                                stack.enter_context(patch.object(retrieval_hydration, "_enrich_retrieval_hits_in_session",
                                                                 baseline_function))
                            return measure(request, active_settings, store, embedder,
                                           mandatory_sources=mandatory_by_query.get(query, ()))

                    first = once()
                    for _ in range(args.warmup):
                        if baseline_function:
                            once(True)
                        once()
                    samples, baseline_samples = [], []
                    for repeat in range(args.repeat):
                        order = [True, False] if repeat % 2 == 0 else [False, True]
                        for baseline in order if baseline_function else [False]:
                            (baseline_samples if baseline else samples).append(once(baseline))
                    cases.append({"query": query, "depth": depth, "mode": mode, "glossary": glossary,
                                  "first_call": first, "samples": samples,
                                  "stable_sources": len({sample["sources_digest"] for sample in samples}) == 1,
                                  "summary_ms": {metric: summarize([sample["timings_ms"].get(metric, 0) for sample in samples])
                                                 for metric in samples[0]["timings_ms"]}})
                    if baseline_samples:
                        cases[-1].update(
                            baseline_samples=baseline_samples,
                            output_equivalent=all(
                                all(before[key] == after[key] for key in
                                    ("sources_digest", "blocks_digest", "snapshot_digests", "limit_reached", "reference_counts"))
                                for before, after in zip(baseline_samples, samples)),
                            baseline_summary_ms={metric: summarize([sample["timings_ms"].get(metric, 0)
                                                                   for sample in baseline_samples])
                                                 for metric in baseline_samples[0]["timings_ms"]})
                    print(f"{query} depth={depth} {mode} glossary={glossary}: "
                          f"{samples[-1]['counts']['sources']} sources, "
                          f"median={cases[-1]['summary_ms']['answer_path_ms']['median']:.1f} ms", flush=True)
    after = corpus_state(store)
    code_after = digest({str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
                         for path in sorted((ROOT / "backend/app").rglob("*.py"))})
    event.remove(engine, "before_cursor_execute", reject_writes)
    manifest = {"recorded_at": datetime.now(timezone.utc).isoformat(),
                "git_head": git_head,
                "code_digest": code_before,
                "code_unchanged": code_before == code_after, "code_digest_after": code_after,
                "knowledge_profile": settings.knowledge_profile, "collection": store.collection,
                "baseline_hydration_ref": baseline_ref, "warmup_per_side": args.warmup,
                "baseline_hydration_sha256": baseline_digest,
                "probe_sha256": PROBE_DIGEST, "database": engine.url.database,
                "reference_digest": digest(reference) if args.reference else None,
                "corpus": before, "corpus_after": after, "corpus_unchanged": before == after,
                "mail_mode": args.mail_mode,
                "intervals": "Nested inclusive timings; do not sum parents and children. SQL excludes fetch/decoding.",
                "exclusions": "HTTP/auth/history persistence/LLM/browser. First call is not necessarily cold.",
                "settings": {key: getattr(settings, key) for key in (
                    "search_rrf_dense_weight", "search_rrf_bm25_weight", "search_rrf_k",
                    "glossary_sparse_expansion_weight", "chat_concept_max_chars", "chat_chunk_max_chars",
                    "qdrant_prefer_grpc", "chat_focus_named_objects")},
                "cases": cases}
    return finish_report(manifest, args.output, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
