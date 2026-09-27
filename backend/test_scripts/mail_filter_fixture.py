"""Owned disposable real-service resources; never uses production defaults."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import sys
import tempfile
import traceback

sys.path.insert(0, os.environ.get('MAIL_FILTER_BASELINE_BACKEND', str(Path(__file__).resolve().parents[1])))
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm


def targets():
    names = ('MAIL_FILTER_TEST_DATABASE_URL', 'MAIL_FILTER_TEST_QDRANT_URL', 'MAIL_FILTER_TEST_COLLECTION')
    values = [os.environ.get(name) for name in names]
    if not all(values):
        raise ValueError('explicit test targets required')
    database, qdrant, collection = values
    dbname = make_url(database).database
    if not re.fullmatch(r'mail_filter_test_[a-z0-9_]+', dbname or '') or not re.fullmatch(r'mail_filter_test_[a-z0-9_]+', collection):
        raise ValueError('owned test resource prefixes required')
    if make_url(database).get_backend_name() != 'postgresql':
        raise ValueError('real PostgreSQL required')
    return database, qdrant, collection


def configure(database, qdrant, collection, data_dir):
    from app.config import Settings
    import app.config as config
    settings = Settings(_env_file=None, database_url=database, database_url_dev='',
                        qdrant_url=qdrant, qdrant_collection=collection, qdrant_prefer_grpc=False,
                        embedding_dimensions=2, data_dir=Path(data_dir),
                        search_per_branch_top_k=40, search_graph_expansion_enabled=True,
                        glossary_query_expansion_enabled=False)
    config.get_settings = lambda: settings
    from app.db import session as db
    if db._engine is not None:
        db._engine.dispose()
    db._engine = create_engine(database, pool_pre_ping=True)
    db._session_factory = None
    from app.services.vector_store import VectorStore
    import app.services.vector_store as vector
    vector.get_settings = lambda: settings
    store = VectorStore()
    return settings, store


@contextmanager
def owned_fixture():
    database, qdrant, collection = targets()
    url = make_url(database)
    admin = create_engine(url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    client = QdrantClient(url=qdrant, timeout=10)
    created_db = created_collection = False
    try:
        with admin.connect() as connection:
            if connection.scalar(text('SELECT 1 FROM pg_database WHERE datname=:name'), {'name': url.database}):
                raise ValueError('test database already exists; refusing to reuse or delete')
            if client.collection_exists(collection):
                raise ValueError('test collection already exists; refusing to reuse or delete')
            connection.execute(text('CREATE DATABASE "' + url.database + '"'))
            created_db = True
        with tempfile.TemporaryDirectory(prefix='mail_filter_test_') as directory:
            settings, store = configure(database, qdrant, collection, directory)
            from app.db.models import Base
            from app.db.session import get_engine
            Base.metadata.create_all(get_engine())
            store.client.create_collection(collection, vectors_config=qm.VectorParams(size=2, distance=qm.Distance.COSINE),
                                           sparse_vectors_config=store._sparse_params())
            created_collection = True
            store.ensure_collection()
            seed(store)
            yield settings, store
            get_engine().dispose()
    finally:
        if created_collection:
            client.delete_collection(collection)
        client.close()
        if created_db:
            with admin.connect() as connection:
                connection.execute(text('DROP DATABASE "' + url.database + '" WITH (FORCE)'))
        admin.dispose()


def seed(store):
    from app.db.models import Document, DocumentSource, DocumentChunk, OkfConcept, DocumentGeneration
    from app.db.session import session_scope
    from app.services.vector_store import concept_point_id, chunk_point_id
    specs = [(i, 'mail' if i % 2 else 'deep', 'mail', 'ru', 'MAIL_MARKER', 10., [1., .005]) for i in range(40)]
    specs += [(40, 'root', 'document', 'en', 'DOC_MARKER', 1., [.5, .5]),
              (41, 'standalone', 'document', None, 'ATTACHMENT_DOC_MARKER', .8, [.4, .5]),
              (42, None, 'unknown', 'ru', 'UNKNOWN_MARKER', .7, [.3, .5]),
              (43, 'broken', 'unknown', 'en', 'BROKEN_MARKER', .7, [.3, .5]),
              (44, None, 'document', 'en', 'FORGED_MARKER', .6, [.3, .5])]
    points = []
    with session_scope() as session:
        session.add(Document(id='ab12cd34ef56ab78', filename='fixture.docx'))
        session.add(Document(id='deleted', filename='deleted.docx', deleted_at=datetime.now(timezone.utc)))
        session.flush()
        session.add_all([DocumentSource(doc_id='ab12cd34ef56ab78', source_id=sid, parent_source_id=parent, kind=kind,
                       display_name=sid) for sid, parent, kind in [('root', None, 'document'), ('mail', 'root', 'mail'),
                       ('attachment', 'mail', 'document'), ('deep', 'attachment', 'document'),
                       ('standalone', 'root', 'document'), ('broken', 'broken', 'mail')]])
        session.add(DocumentGeneration(id='a'*32, doc_id='ab12cd34ef56ab78', phase='preparing'))
        for i, source, scope, locale, marker, weight, vector in specs:
            slug = 'topic-' + str(i)
            content = 'topic ' + marker + ' ' + str(i)
            session.add(DocumentChunk(doc_id='ab12cd34ef56ab78', chunk_index=i, source_id=source,
                                      section_title='topic', content=content))
            session.add(OkfConcept(doc_id='ab12cd34ef56ab78', slug=slug, source_id=source, chunk_index=i,
                                  title='topic', content=content))
            for kind, pid in [('chunk', chunk_point_id('ab12cd34ef56ab78', i)), ('concept', concept_point_id('ab12cd34ef56ab78', slug))]:
                payload = dict(point_type=kind, doc_id='ab12cd34ef56ab78', chunk_index=i, slug=slug, title='topic',
                               section_title='topic', source_locale=locale, tags=['shared'],
                               dev_tags=['module-doc' if scope == 'document' else 'module-mail'],
                               relations=['topic-40', 'topic-1', 'topic-41'], mail_scope=scope, mail_scope_version=1)
                points.append(qm.PointStruct(id=pid, vector={'': vector, 'sparse': qm.SparseVector(indices=[7], values=[weight])}, payload=payload))
        for kind, gen, deleted in [('chunk', None, True), ('concept', 'a'*32, False)]:
            import uuid
            points.append(qm.PointStruct(id=str(uuid.uuid4()), vector={'': [1., 0.], 'sparse': qm.SparseVector(indices=[7], values=[20.])},
                payload=dict(doc_id='deleted' if deleted else 'ab12cd34ef56ab78', point_type=kind, slug='hidden',
                             generation_id=gen, deleted=deleted, mail_scope='document', mail_scope_version=1)))
    store.client.upsert(store.collection, points, wait=True)


def query(store, mode, branches, **filters):
    return store.search_composite(dense_vec=[1., 0.], sparse_vec=qm.SparseVector(indices=[7], values=[1.]),
                                  tags=filters.pop('tags', []), branches=branches, top_k=100, mail_mode=mode, **filters)


def main_guard(function):
    import json
    try:
        result = function()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        diagnostic = getattr(getattr(exc, 'orig', None), 'diag', None)
        print(json.dumps({'status': 'FAIL', 'error_type': type(exc).__name__,
                          'constraint': getattr(diagnostic, 'constraint_name', None),
                          'sqlstate': getattr(diagnostic, 'sqlstate', None),
                          'http_status': getattr(exc, 'status_code', None),
                          'location': str(traceback.extract_tb(exc.__traceback__)[-1].name) + ':' + str(traceback.extract_tb(exc.__traceback__)[-1].lineno)}))
        return 1
