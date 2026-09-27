"""Real Qdrant pre-limit/filter and metadata-migration integration gates."""
from copy import deepcopy
from mail_filter_fixture import main_guard, owned_fixture, query


def probe():
    from app.services.retrieval_hydration import load_visible_retrieval_hits
    from app.services.context_builder import merge_and_format, format_context
    from scripts.backfill_mail_scope import run_backfill
    from app.services.fusion import Hit
    checks = 0
    with owned_fixture() as (settings, store):
        schema = store.client.get_collection(store.collection).payload_schema
        assert str(schema['mail_scope'].data_type) == 'keyword'
        assert str(schema['mail_scope_version'].data_type) == 'integer'
        for branches in ({'dense'}, {'bm25'}, {'dense', 'bm25'}):
            for mode in ('all', 'exclude', 'only'):
                hits = query(store, mode, branches)
                assert hits and all(not h.payload.get('deleted') and h.payload.get('generation_id') is None for h in hits)
                if mode != 'all':
                    expected = 'mail' if mode == 'only' else 'document'
                    assert all(h.payload['mail_scope'] == expected for h in hits)
                assert {h.payload['point_type'] for h in hits} == {'concept', 'chunk'}
                hydrated, lookup = load_visible_retrieval_hits(deepcopy(hits), mail_mode=mode)
                blocks = merge_and_format(hydrated, settings, mail_mode=mode)
                context = format_context(blocks)
                if mode == 'exclude':
                    assert 'DOC_MARKER' in context and 'MAIL_MARKER' not in context and 'FORGED_MARKER' not in context
                if mode == 'only':
                    assert 'MAIL_MARKER' in context and 'DOC_MARKER' not in context
                if mode == 'all':
                    assert hits[0].payload['mail_scope'] == 'mail'  # higher-ranked 40 mail sources
                # Canonical SQL recheck removes a payload forged to document.
                assert all(h.payload['chunk_index'] != 44 for h in hydrated) or mode == 'all'
                checks += 1
            for tags in (['shared'], ['module-doc'], ['module-mail'], ['absent']):
                for locales, unknown in ((['en'], False), (['ru'], False), (['en'], True), ([], True)):
                    for mode in ('exclude', 'only'):
                        hits = query(store, mode, branches, tags=tags, source_locales=locales,
                                     include_unknown_source_locale=unknown)
                        assert all(set(tags) & set(h.payload.get('tags', []) + h.payload.get('dev_tags', [])) for h in hits)
                        assert all(h.payload.get('source_locale') in locales or (unknown and h.payload.get('source_locale') is None) for h in hits)
                        checks += 1
        # Every graph scroll uses the same nested constraints as ranked branches.
        first = query(store, 'exclude', {'dense'}, tags=['module-doc'], source_locales=['en'])
        graph = store._graph_expansion([(first, 1.)], search_filter=store._build_search_filter(['module-doc'], ['en'], mail_mode='exclude'))
        assert graph and all(h.payload['mail_scope'] == 'document' and h.payload['source_locale'] == 'en' for h in graph)
        before, _ = store.client.scroll(store.collection, limit=1000, with_vectors=True)
        ids = [r.id for r in before]
        store.client.delete_payload(store.collection, ['mail_scope', 'mail_scope_version'], ids, wait=True)
        dry = run_backfill(store, settings, batch_size=13)
        assert dry['would_change'] > 0 and dry['updated'] == 0 and dry['failed_batches'] == 0
        applied = run_backfill(store, settings, apply=True, batch_size=13)
        again = run_backfill(store, settings, batch_size=13)
        assert applied['failed_batches'] == applied['missing_active_points'] == again['would_change'] == 0
        after = store.client.retrieve(store.collection, ids, with_vectors=True)
        original = {str(r.id): r for r in before}
        assert set(original) == {str(r.id) for r in after}
        for row in after:
            old = original[str(row.id)]
            assert row.vector == old.vector
            assert {k:v for k,v in row.payload.items() if not k.startswith('mail_scope')} == {k:v for k,v in old.payload.items() if not k.startswith('mail_scope')}
        # Old-generation points stay skipped and are never admitted.
        assert applied['skipped_nonactive'] > 0
        strict = query(store, 'exclude', {'dense', 'bm25'})
        assert strict and all(h.payload.get('chunk_index') in (40, 41) for h in strict)
        missing_id = next(row.id for row in after if row.payload.get('point_type') == 'chunk' and row.payload.get('chunk_index') == 40)
        store.client.delete(store.collection, points_selector=[missing_id], wait=True)
        missing = run_backfill(store, settings)
        assert missing['missing_active_points'] == 1
        # Startup recovery is also a full writer and must preserve strict scopes.
        from types import SimpleNamespace
        from app.services.vector_store import chunk_point_id
        mail_id = chunk_point_id('ab12cd34ef56ab78', 0)
        store.client.delete(store.collection, points_selector=[mail_id], wait=True)
        assert store.backfill_chunks(SimpleNamespace(embed_texts=lambda texts: [[1., 0.] for _ in texts])) == 2
        restored = store.client.retrieve(store.collection, [missing_id, mail_id])
        assert {r.payload['chunk_index']: (r.payload['mail_scope'], r.payload['mail_scope_version']) for r in restored} == {40: ('document', 1), 0: ('mail', 1)}
        for mode, point in [('exclude', missing_id), ('only', mail_id)]:
            rows, _ = store.client.scroll(store.collection, scroll_filter=store._build_search_filter([], mail_mode=mode), limit=100)
            assert str(point) in {str(r.id) for r in rows}
        # Stale point search survives but SQL hydration discards missing identity.
        stale = Hit('stale', 1., dict(point_type='chunk', doc_id='ab12cd34ef56ab78', chunk_index=999, content='FORBIDDEN', mail_scope='document', mail_scope_version=1))
        assert load_visible_retrieval_hits([stale], mail_mode='exclude')[0] == []
    return dict(status='PASS', matrix_checks=checks, forbidden_markers=0, preserved_vectors=True,
                migration_idempotent=True, missing_point_audit=True, startup_writer=True)


if __name__ == '__main__':
    raise SystemExit(main_guard(probe))
