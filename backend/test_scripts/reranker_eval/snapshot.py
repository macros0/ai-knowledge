"""Read-only capture using the chat API's actual retrieval/filter helpers."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
from time import perf_counter


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str, separators=(',', ':')).encode()).hexdigest()


def working_tree_fingerprint(root: Path) -> str:
    status = subprocess.check_output(['git', 'status', '--porcelain=v1', '-z'], cwd=root)
    paths = subprocess.check_output(['git', 'ls-files', '--modified', '--others',
                                      '--exclude-standard', '-z'], cwd=root)
    paths += subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z'], cwd=root)
    files = {}
    for name in sorted(set(paths.decode('utf-8').split('\0')) - {''}):
        path = root / name
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return digest({'status_sha256': hashlib.sha256(status).hexdigest(), 'files': files})


def source_key(block: dict) -> str:
    ref = block.get('_evidence')
    if not ref or not ref.get('doc_id') or not ref.get('digest'):
        raise ValueError('unproven canonical identity')
    identity = {k: ref.get(k) for k in ('doc_id', 'point_type', 'source_id', 'generation_id',
                                      'slug', 'chunk_index', 'start', 'length', 'digest')}
    if identity['point_type'] not in {'concept', 'chunk'}:
        raise ValueError('unproven canonical identity type')
    if (identity['point_type'] == 'concept' and not identity['slug']) or (
        identity['point_type'] == 'chunk' and identity['chunk_index'] is None
    ):
        raise ValueError('incomplete canonical identity')
    return digest({'identity': identity, 'kind': block.get('kind'),
                   'components': block.get('_mail_components'),
                   'mail_evidence': block.get('_mail_evidence')})


def corpus_fingerprint() -> str:
    from sqlalchemy import select
    from app.db.models import (Document, DocumentChunk, DocumentGenerationState,
                               OkfConcept, DomainTerm, DomainTermAlias, DocumentSource,
                               DomainTermTranslation, GlossaryInfotypeRule,
                               GlossaryInfotypePrefix, GlossaryState, Stopword)
    from app.db.session import session_scope
    result = {}
    with session_scope() as session:
        for model in (Document, DocumentChunk, DocumentGenerationState, OkfConcept,
                      DomainTerm, DomainTermAlias, DocumentSource, DomainTermTranslation,
                      GlossaryInfotypeRule, GlossaryInfotypePrefix, GlossaryState, Stopword):
            table = model.__table__
            rows = session.execute(select(table).order_by(*table.primary_key.columns)).all()
            result[table.name] = digest([tuple(row) for row in rows])
    return digest(result)


def capture_case(case: dict, *, user, settings) -> dict:
    before = corpus_fingerprint()
    captured = _capture(case, user=user, settings=settings)
    if corpus_fingerprint() != before:
        raise ValueError('corpus changed during capture')
    captured['manifest'] = {'corpus_sha256': before,
                            'settings_sha256': digest({k: v for k, v in settings.model_dump().items()
                                if k.startswith(('search_', 'chat_', 'glossary_'))})}
    return captured


def _capture(case: dict, *, user, settings) -> dict:
    from app.api import chat as api
    from app.models.schemas import ChatRequest
    from app.services.glossary.expansion import prepare_query
    from app.services.glossary.query_sparse import build_query_sparse
    from app.services.stopwords import KIND_BM25, get_stopwords
    from app.services.context_builder import resolve_branches, merge_and_format
    from app.services.retrieval_hydration import load_visible_retrieval_hits
    started = perf_counter()
    req = ChatRequest(**{**case['request'], 'query': case['query']})
    if req.source_selection:
        raise ValueError('selected sources must not be reranked')
    scope = None
    if req.search_doc_ids is not None:
        scope = [d.doc_id for d in api._search_scope_documents(req.search_doc_ids) if d.available]
        if not scope:
            raise ValueError('unavailable search scope')
    plan = prepare_query(req.query, ui_locale=req.locale,
                         enabled=settings.glossary_query_expansion_enabled and req.use_glossary,
                         settings=settings)
    branches = resolve_branches(req.mode, req.dense, req.bm25, settings)
    vector = api._get_embedder().embed(plan.dense_query) if 'dense' in branches else None
    sparse = build_query_sparse(plan, stopwords=get_stopwords(KIND_BM25), settings=settings
                                ) if 'bm25' in branches else None
    groups = plan.strict_groups or plan.match_groups
    depth = req.search_depth if req.search_depth is not None else (
        40 if req.response_mode is not None else settings.search_per_branch_top_k)
    ceiling = depth * (8 if req.response_mode is not None else 1)
    candidate_depth, rounds = depth, []
    while True:
        status = {}
        hits = api._get_vector_store().search_composite(
            dense_vec=vector, sparse_vec=sparse, tags=req.tags or None, branches=branches,
            source_locales=req.source_locales or None,
            include_unknown_source_locale=req.include_unknown_source_locale,
            mail_mode=req.mail_mode, top_k=candidate_depth, per_branch_top_k=candidate_depth,
            retrieval_status=status, **({'doc_ids': scope} if scope is not None else {}))
        raw = [asdict(hit) for hit in hits]
        if scope is not None:
            hits = [h for h in hits if h.payload.get('doc_id') in scope]
        # Preserve pre-filter stages independently: hydration mutates Hit payloads.
        hydrated, lookup = load_visible_retrieval_hits(deepcopy(hits), mail_mode=req.mail_mode,
                                          **({'exact_groups': groups} if groups else {}))
        merged_stage = merge_and_format(hydrated, settings, exact_groups=groups,
            filename_lookup={k: v.get('filename', '') for k, v in lookup.items()},
            mail_mode=req.mail_mode, limit_total_chars=False)
        _, final, _, _, _ = api._filtered_chat_blocks(hits, req, settings, groups)
        rounds.append({'candidate_depth': candidate_depth, 'raw_count': len(raw),
                       'merged_count': len(merged_stage), 'filtered_count': len(final)})
        limit = status.get('limit_reached', len(hits) >= candidate_depth)
        if req.response_mode is None or len(final) >= depth or not limit or candidate_depth >= ceiling:
            break
        candidate_depth = min(candidate_depth * 2, ceiling)
    if req.response_mode is not None:
        final = final[:depth]
    for block in final:
        block['eval_key'] = source_key(block)
    return {'id': case['id'], 'query': req.query, 'request': req.model_dump(mode='json'),
            'raw': raw, 'hydrated': [asdict(h) for h in hydrated], 'merged': merged_stage,
            'final': final, 'rounds': rounds, 'rules_version': plan.rules_version,
            'forms': list(dict.fromkeys(f.text for group in groups for f in group.resolved_forms
                                       if f.can_search)),
            'elapsed_ms': (perf_counter() - started) * 1000}
