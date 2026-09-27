"""Audit/backfill canonical mail scope without reindexing or parsing any document."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, tuple_
from app.config import get_settings
from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services.generation_store import lock_generation_read
from app.services.mail_scope import MAIL_SCOPE_VERSION, build_mail_scope_map
from app.services.source_store import fetch_source_trees
from app.services.vector_store import VectorStore, concept_point_id, chunk_point_id
from qdrant_client.http import models as qm

COUNTERS = ('scanned', 'active', 'skipped_nonactive', 'mail', 'document', 'unknown',
            'would_change', 'updated', 'unchanged', 'orphan', 'missing_canonical',
            'invalid_identity', 'broken_tree', 'failed_batches', 'readback_errors',
            'missing_active_points')
FIELDS = ['doc_id', 'point_type', 'slug', 'chunk_index', 'generation_id',
          'mail_scope', 'mail_scope_version']


def _call(method, **kwargs):
    """Bounded transient retries; never print exception/provider payload or DSN."""
    for attempt in range(3):
        try:
            return method(**kwargs)
        except Exception as exc:
            status = getattr(exc, 'status_code', None)
            if (attempt == 2 or isinstance(exc, (ValueError, TypeError))
                    or (status is not None and status < 500 and status != 429)):
                raise
            time.sleep(0.5 * (attempt + 1))


def _increment(report, kind, counter, amount=1):
    report[counter] += amount
    report['point_types'][kind][counter] += amount


def _kind(payload):
    value = payload.get('point_type')
    return value if value in ('concept', 'chunk') else 'other'


def _page_scopes(records, report):
    """Classify one page using batch SQL metadata under publication read locks."""
    doc_ids = {p.payload.get('doc_id') for p in records
               if isinstance((p.payload or {}).get('doc_id'), str) and p.payload['doc_id']}
    with session_scope() as session:
        generations = lock_generation_read(session, sorted(doc_ids))
        documents = set(session.scalars(select(Document.id).where(Document.id.in_(doc_ids))))
        concept_keys, chunk_keys = set(), set()
        for point in records:
            p = point.payload or {}
            if not isinstance(p.get('doc_id'), str) or p.get('doc_id') not in documents or p.get('generation_id') != generations.get(p.get('doc_id')):
                continue
            if p.get('point_type') == 'concept' and isinstance(p.get('slug'), str) and p['slug']:
                concept_keys.add((p['doc_id'], p['slug']))
            if p.get('point_type') == 'chunk' and type(p.get('chunk_index')) is int and p['chunk_index'] >= 0:
                chunk_keys.add((p['doc_id'], p['chunk_index']))
        concepts = {}
        if concept_keys:
            concepts = {(doc_id, slug): (source_id, index) for doc_id, slug, source_id, index
                        in session.execute(select(OkfConcept.doc_id, OkfConcept.slug,
                            OkfConcept.source_id, OkfConcept.chunk_index).where(
                            tuple_(OkfConcept.doc_id, OkfConcept.slug).in_(concept_keys)))}
        chunk_keys.update((doc_id, index) for (doc_id, _), (_, index) in concepts.items()
                          if index is not None)
        chunks = {}
        if chunk_keys:
            chunks = {(doc_id, index): source_id for doc_id, index, source_id in session.execute(
                select(DocumentChunk.doc_id, DocumentChunk.chunk_index, DocumentChunk.source_id)
                .where(tuple_(DocumentChunk.doc_id, DocumentChunk.chunk_index).in_(chunk_keys)))}
        trees = fetch_source_trees(session, doc_ids)
        maps = {doc_id: build_mail_scope_map(tree) for doc_id, tree in trees.items()}
        changes = {}
        for point in records:
            p = point.payload or {}
            kind = _kind(p)
            _increment(report, kind, 'scanned')
            doc_id = p.get('doc_id') if isinstance(p.get('doc_id'), str) else None
            if doc_id in documents and p.get('generation_id') != generations.get(doc_id):
                _increment(report, kind, 'skipped_nonactive')
                continue
            scope = 'unknown'
            reason = None
            if doc_id not in documents:
                _increment(report, kind, 'orphan')
                reason = 'orphan'
            else:
                _increment(report, kind, 'active')
                source_id, index = None, None
                generation = generations.get(doc_id)
                valid = False
                if kind == 'concept' and isinstance(p.get('slug'), str) and p['slug']:
                    valid = str(point.id) == concept_point_id(doc_id, p['slug'], generation_id=generation)
                    identity = concepts.get((doc_id, p['slug']))
                    if identity:
                        source_id, index = identity
                    else:
                        _increment(report, kind, 'missing_canonical')
                        reason = 'missing_canonical'
                elif kind == 'chunk' and type(p.get('chunk_index')) is int and p['chunk_index'] >= 0:
                    index = p['chunk_index']
                    valid = str(point.id) == chunk_point_id(doc_id, index, generation_id=generation)
                    if (doc_id, index) in chunks:
                        source_id = chunks[(doc_id, index)]
                    else:
                        _increment(report, kind, 'missing_canonical')
                        reason = 'missing_canonical'
                if not valid:
                    _increment(report, kind, 'invalid_identity')
                    reason = 'invalid_identity'
                elif reason is None:
                    scope = maps.get(doc_id, {}).get(source_id, 'unknown')
                    chunk_source = chunks.get((doc_id, index))
                    if kind == 'concept' and source_id and chunk_source and source_id != chunk_source:
                        scope, reason = 'unknown', 'source_mismatch'
                    if scope == 'unknown' and reason is None:
                        reason = 'source_missing' if not source_id else 'broken_tree'
                    if reason in ('broken_tree', 'source_mismatch'):
                        _increment(report, kind, 'broken_tree')
            _increment(report, kind, scope)
            if scope == 'unknown':
                reasons = report['unknown_reasons']
                reasons[reason or 'unproven'] = reasons.get(reason or 'unproven', 0) + 1
            if (p.get('mail_scope') == scope and type(p.get('mail_scope_version')) is int
                    and p['mail_scope_version'] == MAIL_SCOPE_VERSION):
                _increment(report, kind, 'unchanged')
            else:
                _increment(report, kind, 'would_change')
                changes[str(point.id)] = scope
        return changes


def _audit_missing(store, report, settings, *, batch_size, doc_id):
    """Reverse keyset pass: detect canonical identities absent from the active index."""
    last_doc = ''
    while True:
        with session_scope() as session:
            statement = select(Document.id).where(Document.id > last_doc).order_by(Document.id).limit(batch_size)
            if doc_id:
                statement = statement.where(Document.id == doc_id)
            ids = list(session.scalars(statement))
            if not ids:
                return
            generations = lock_generation_read(session, ids)
            for model, kind in ((OkfConcept, 'concept'), (DocumentChunk, 'chunk')):
                if kind == 'chunk' and not settings.search_index_chunks_enabled:
                    continue
                last_row = 0
                while True:
                    column = model.slug if kind == 'concept' else model.chunk_index
                    rows = session.execute(select(model.id, model.doc_id, column).where(
                        model.doc_id.in_(ids), model.id > last_row).order_by(model.id).limit(batch_size)).all()
                    if not rows:
                        break
                    expected = {concept_point_id(did, key, generation_id=generations.get(did)) if kind == 'concept'
                                else chunk_point_id(did, key, generation_id=generations.get(did)): did
                                for _, did, key in rows}
                    found = _call(store.client.retrieve, collection_name=store.collection,
                                  ids=list(expected), with_payload=['generation_id'], with_vectors=False)
                    actual = {str(row.id) for row in found if (row.payload or {}).get('generation_id') ==
                              generations.get(expected.get(str(row.id)))}
                    _increment(report, kind, 'missing_active_points', len(set(expected) - actual))
                    last_row = rows[-1][0]
            last_doc = ids[-1]


def run_backfill(store, settings, *, apply=False, batch_size=256, doc_id=None):
    report = {key: 0 for key in COUNTERS}
    report.update(algorithm='canonical-mail-ancestry', version=MAIL_SCOPE_VERSION,
                  started_at=datetime.now(timezone.utc).isoformat(), mode='apply' if apply else 'dry-run',
                  target={'knowledge_profile': settings.knowledge_profile,
                          'collection': store.collection,
                          'database_fingerprint': hashlib.sha256(str(settings.database_url).encode()).hexdigest()[:16]},
                  unknown_reasons={}, point_types={kind: {key: 0 for key in COUNTERS}
                  for kind in ('concept', 'chunk', 'other')})
    try:
        _call(store.client.get_collection, collection_name=store.collection)
        if apply:
            store.ensure_mail_scope_indexes()
        offset = None
        filter_ = qm.Filter(must=[qm.FieldCondition(key='doc_id', match=qm.MatchValue(value=doc_id))]) if doc_id else None
        while True:
            records, offset = _call(store.client.scroll, collection_name=store.collection,
                                   scroll_filter=filter_, limit=batch_size, offset=offset,
                                   with_payload=FIELDS, with_vectors=False)
            try:
                changes = _page_scopes(records, report)
                if apply and changes:
                    by_type = {'concept': {}, 'chunk': {}, 'other': {}}
                    for point in records:
                        point_id = str(point.id)
                        if point_id in changes:
                            kind = (point.payload or {}).get('point_type')
                            kind = kind if kind in ('concept', 'chunk') else 'other'
                            by_type[kind][point_id] = changes[point_id]
                    for kind, patch in by_type.items():
                        if patch:
                            updated = store.patch_mail_scopes(patch)
                            report['updated'] += updated
                            report['point_types'][kind]['updated'] += updated
            except Exception as exc:
                report['failed_batches'] += 1
                if 'readback' in str(exc).lower():
                    report['readback_errors'] += 1
                print('mail_scope_backfill: page operation failed', file=sys.stderr)
            if offset is None:
                break
        _audit_missing(store, report, settings, batch_size=batch_size, doc_id=doc_id)
    except Exception:
        report['failed_batches'] += 1
        print('mail_scope_backfill: dependency or index verification failed', file=sys.stderr)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Audit/patch canonical mail scope (writers must be stopped for apply).')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true')
    modes.add_argument('--apply', action='store_true')
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--doc-id')
    try:
        args = parser.parse_args(argv)
        if not 1 <= args.batch_size <= 1000:
            parser.error('--batch-size must be between 1 and 1000')
    except SystemExit as exc:
        return int(exc.code)
    try:
        settings = get_settings()
        report = run_backfill(VectorStore(), settings, apply=args.apply,
                              batch_size=args.batch_size, doc_id=args.doc_id)
    except Exception:
        print('mail_scope_backfill: initialization failed', file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report['failed_batches'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
