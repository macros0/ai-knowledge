"""Real PostgreSQL interleavings across hydration, publication and authorship."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
import json
import time
from mail_filter_fixture import main_guard, owned_fixture, query


def candidate(settings, store, marker, source='root'):
    from app.db.session import session_scope
    from app.services.generation_store import begin_generation, mark_generation_ready
    from app.services.generation_files import generation_paths
    from app.services.generation_artifacts import file_digest
    with session_scope() as session:
        generation = begin_generation(session, 'ab12cd34ef56ab78').id
    paths = generation_paths(settings, 'ab12cd34ef56ab78', generation)
    paths.attachments.mkdir(parents=True)
    paths.bundle.mkdir(parents=True)
    points = store.index_chunks('ab12cd34ef56ab78', 'fixture.docx', ['topic ' + marker], [], [[1., 0.]],
                                chunk_indices=[40], source_ids=[source], generation_id=generation)
    prepared = dict(doc_id='ab12cd34ef56ab78', generation_id=generation, artifacts={}, concepts=[], sources=None,
                    chunks=[dict(chunk_index=40, content='topic ' + marker, source_id=source)], attachments=[],
                    point_ids=sorted(points), document_fields={}, index_metadata=dict(global_tags=[], dev_tags=[], source_locale=None))
    path = paths.uploads_root / 'publication.json'
    path.write_text(json.dumps(prepared), encoding='utf-8')
    with session_scope() as session:
        mark_generation_ready(session, 'ab12cd34ef56ab78', generation, publication_hash=file_digest(path))
    return generation


def probe():
    from sqlalchemy import select, text
    from app.db.session import session_scope, get_engine
    from app.db.models import DocumentGenerationState, DocumentGeneration, DocumentChunk
    from app.services.generation_publication import publish_prepared_document
    from app.services.retrieval_hydration import load_visible_retrieval_hits
    from app.services.context_builder import merge_and_format
    from app.services.authorship_evidence import load_authorship_evidence, answer_authorship
    from app.services.generation_store import lock_generation_read
    from app.services.errors import VectorStoreError
    with owned_fixture() as (settings, store):
        assert get_engine().dialect.name == 'postgresql'
        old_hits = query(store, 'exclude', {'dense'})
        candidate_id = candidate(settings, store, 'NEW_MAIL_MARKER', source='mail')
        # H05: publisher runs between search and canonical hydration.
        assert publish_prepared_document(settings, store, 'ab12cd34ef56ab78', candidate_id)
        assert load_visible_retrieval_hits(deepcopy(old_hits), mail_mode='exclude')[0] == []
        assert load_visible_retrieval_hits(deepcopy(old_hits), mail_mode='only')[0] == []
        # H06/I04: shared snapshot barrier blocks publication until hydration ends.
        old = query(store, 'only', {'dense'})
        hydrated, _ = load_visible_retrieval_hits(deepcopy(old), mail_mode='only')
        blocks = merge_and_format(hydrated, settings, mail_mode='only')
        assert blocks and 'NEW_MAIL_MARKER' in str(blocks)
        next_id = candidate(settings, store, 'NEXT_DOCUMENT_MARKER')
        started, finished = Event(), Event()
        def publish():
            started.set()
            result = publish_prepared_document(settings, store, 'ab12cd34ef56ab78', next_id)
            finished.set()
            return result
        with ThreadPoolExecutor(max_workers=1) as pool:
            with session_scope() as reader:
                generations = lock_generation_read(reader, ['ab12cd34ef56ab78'])
                future = pool.submit(publish)
                assert started.wait(5)
                # Prove blocking in PostgreSQL rather than relying on sleep timing.
                deadline = time.monotonic() + 5
                blocked = False
                while time.monotonic() < deadline:
                    with get_engine().connect() as observer:
                        blocked = bool(observer.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'")))
                    if blocked:
                        break
                    time.sleep(.01)
                assert blocked and not finished.is_set()
                assert generations['ab12cd34ef56ab78'] == candidate_id
                assert reader.scalar(select(DocumentChunk.content).where(DocumentChunk.doc_id=='ab12cd34ef56ab78', DocumentChunk.chunk_index==40)) == 'topic NEW_MAIL_MARKER'
            assert future.result(timeout=10) and finished.is_set()
        # Authorship opens a new snapshot and refuses blocks from retired generation.
        assert load_authorship_evidence(blocks, 10000, mail_mode='only') == []
        new = query(store, 'exclude', {'dense'})
        new_hits, _ = load_visible_retrieval_hits(deepcopy(new), mail_mode='exclude')
        new_blocks = merge_and_format(new_hits, settings, mail_mode='exclude')
        assert new_blocks
        # No locks remain during the actual LLM callback: writer completes on another connection.
        llm_calls = []
        class LLM:
            def chat(self, system, user):
                llm_calls.append(user)
                with get_engine().begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout='1s'"))
                    connection.execute(text("UPDATE documents SET filename=filename WHERE id='ab12cd34ef56ab78'"))
                assert 'NEW_MAIL_MARKER' not in user and 'NEXT_DOCUMENT_MARKER' in user
                return '{}'
        answer_authorship('Who wrote topic?', new_blocks, LLM(), locale='en', max_chars=10000, mail_mode='exclude')
        assert len(llm_calls) == 1
        # A failed patch rolls back pointer + canonical rows and keeps the candidate ready.
        failed_id = candidate(settings, store, 'FAILED_MAIL_MARKER', source='mail')
        original = store.patch_mail_scopes
        def fail(_):
            raise VectorStoreError('Injected readback failure')
        store.patch_mail_scopes = fail
        try:
            publish_prepared_document(settings, store, 'ab12cd34ef56ab78', failed_id)
            raise AssertionError('failed patch was committed')
        except VectorStoreError:
            pass
        finally:
            store.patch_mail_scopes = original
        with session_scope() as session:
            state = session.get(DocumentGenerationState, 'ab12cd34ef56ab78')
            assert state.active_generation_id == next_id and state.candidate_generation_id == failed_id
            assert session.get(DocumentGeneration, failed_id).phase == 'ready'
            assert session.scalar(select(DocumentChunk.content).where(DocumentChunk.doc_id=='ab12cd34ef56ab78', DocumentChunk.chunk_index==40)) == 'topic NEXT_DOCUMENT_MARKER'
        assert publish_prepared_document(settings, store, 'ab12cd34ef56ab78', failed_id)
    return dict(status='PASS', separate_connections=True, shared_lock_barrier=True, stale_hits_rejected=True,
                stale_authorship_rejected=True, locks_released_before_llm=True, patch_rollback=True)


if __name__ == '__main__':
    raise SystemExit(main_guard(probe))
