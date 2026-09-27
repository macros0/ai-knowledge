"""Local browser acceptance on owned synthetic resources, no real correspondence."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import json
import os
from pathlib import Path
import time
import threading
import uvicorn
from qdrant_client.http import models as qm
from mail_filter_fixture import owned_fixture


def serve():
    with owned_fixture() as (settings, store):
        settings.auth_provider = 'disabled'
        settings.chat_rate_limit_per_minute = 1000
        settings.knowledge_profile = 'Synthetic mail filter browser acceptance'
        settings.search_graph_expansion_enabled = True
        settings.glossary_query_expansion_enabled = True
        from app.db.session import init_db
        init_db()
        from app.services.glossary.registry import GlossaryRegistry
        GlossaryRegistry().create(None, 'business_term', 'topic', canonical_locale='en')
        from app.api import chat
        chat._get_vector_store = lambda: store
        chat._get_embedder = lambda: SimpleNamespace(embed=lambda _: [1., 0.])
        chat.build_query_sparse = lambda *args, **kwargs: qm.SparseVector(indices=[7], values=[1.])
        evidence_file = Path(os.environ['MAIL_FILTER_BROWSER_EVIDENCE_FILE'])
        class LLM:
            def chat(self, system, user):
                with evidence_file.open('a', encoding='utf-8') as output:
                    output.write(json.dumps({'system': system, 'user': user}, ensure_ascii=False)+'\n')
                if 'pending topic' in user:
                    time.sleep(8)
                if 'error topic' in user:
                    from app.services.errors import LLMError
                    raise LLMError('Synthetic test error')
                markers = [value for value in ('MAIL_MARKER', 'DOC_MARKER', 'UNKNOWN_MARKER', 'ATTACHMENT_DOC_MARKER') if value in user]
                return 'Synthetic answer: ' + ', '.join(markers) + ' [1]'
        chat._get_llm = lambda: LLM()
        from app.db.session import session_scope
        from app.db.models import Document
        with session_scope() as session:
            session.get(Document, 'ab12cd34ef56ab78').source_locale = 'en'
            session.add(Document(id='ab12cd34ef56ab79', filename='locale.docx', source_locale='ru'))
        from app.main import create_app
        application = create_app()
        @application.middleware('http')
        async def capture_synthetic_request(request, call_next):
            if request.url.path == '/api/chat' and request.method == 'POST':
                body = await request.json()
                with evidence_file.with_suffix('.requests.jsonl').open('a', encoding='utf-8') as output:
                    output.write(json.dumps(body, ensure_ascii=False)+'\n')
            return await call_next(request)
        @asynccontextmanager
        async def no_background_writers(_):
            yield
        application.router.lifespan_context = no_background_writers
        server = uvicorn.Server(uvicorn.Config(application, host='127.0.0.1',
                    port=int(os.environ.get('MAIL_FILTER_BROWSER_BACKEND_PORT', '18000')), log_level='warning'))
        def stop_when_requested():
            while not server.should_exit:
                if evidence_file.with_suffix('.stop').exists():
                    server.should_exit = True
                    break
                time.sleep(.25)
        threading.Thread(target=stop_when_requested, daemon=True).start()
        server.run()


if __name__ == '__main__':
    serve()
