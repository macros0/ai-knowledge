"""Frozen CT102 private Pipeline environment for the bounded resource collector.

Only measurement adapters are installed. No application budgets, saved chunks,
shared services, or control documents are modified by this adapter.
"""
from contextlib import ExitStack, closing
import hashlib
import importlib.util
from pathlib import Path
import re
import threading
import time


class EnvironmentUnknown(RuntimeError):
    pass


def private_config(protocol, variant):
    from sqlalchemy.engine import make_url
    try:
        environment = protocol['resource_environment']
        values = dict(environment['settings'])
        values.update(environment.get('variant_settings', {}).get(variant, {}))
        expected = protocol['variants'][variant]
        routes = environment['provider_routes']
        root = Path(environment['private_root']).resolve()
        data = Path(values['data_dir']).resolve()
        url = make_url(values['database_url'])
        if (not root.is_dir() or root == Path(root.anchor) or data == root
                or not data.is_relative_to(root) or url.get_backend_name() != 'postgresql'
                or url.host != '127.0.0.1' or url.port != 15432
                or not re.fullmatch(r'r2_[a-z0-9_]+', url.database or '')
                or not re.fullmatch(r'r2_evaluation_[a-z0-9_]+', values['qdrant_collection'])
                or values['qdrant_collection'] != expected['collection_name']
                or type(expected['table_structure_enabled']) is not bool
                or values['qdrant_url'] != 'http://127.0.0.1:16333'
                or values['embedding_api_base'] != 'http://127.0.0.1:16410'
                or values['embedding_api_base'] != routes['embedding_api_base']
                or values['embedding_model'] != 'ollama/bge-m3'
                or not isinstance(routes['llm_base_url'], str)
                or values['llm_base_url'] != routes['llm_base_url']):
            raise EnvironmentUnknown('Foreign private storage or provider route')
        values['table_structure_enabled'] = expected['table_structure_enabled']
        values['data_dir'] = str(data)
        return values
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise EnvironmentUnknown('Private resource configuration is incomplete') from error


def load_frozen(description, role):
    try:
        path = Path(description['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != description['sha256']:
            raise EnvironmentUnknown('Frozen measurement adapter changed')
        spec = importlib.util.spec_from_file_location('r2_resource_' + role, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (KeyError, OSError, TypeError) as error:
        raise EnvironmentUnknown('Frozen measurement adapter unavailable') from error


def _checked_client(settings):
    from qdrant_client import QdrantClient
    from qdrant_client.qdrant_remote import QdrantRemote
    entered = threading.Event()
    done = threading.Event()
    original = QdrantRemote._check_compatibility
    def probe(*args, **kwargs):
        entered.set()
        try:
            return original(*args, **kwargs)
        finally:
            done.set()
    QdrantRemote._check_compatibility = staticmethod(probe)
    client = None
    try:
        client = QdrantClient(url=settings.qdrant_url, prefer_grpc=settings.qdrant_prefer_grpc,
                              grpc_port=settings.qdrant_grpc_port)
        if not entered.wait(10) or not done.wait(10):
            raise EnvironmentUnknown('Initial compatibility probe was not drained')
        return client
    except BaseException:
        if client is not None:
            client.close()
        raise
    finally:
        QdrantRemote._check_compatibility = staticmethod(original)


def configure_resource_connection(connection, record):
    # SET inside a transaction is undone by SQLAlchemy's pool rollback.
    # Keep private ownership and the session timeout outside transactions.
    original = connection.autocommit
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SET application_name='r2-benchmark-resources'")
            cursor.execute("SET statement_timeout='10s'")
    finally:
        connection.autocommit = original


class ResourceObservation:
    """Prepared transport and continuous monitor, independent of Pipeline creation."""
    def __init__(self, monitor, client, targets):
        self.monitor = monitor
        self.client = client
        self.targets = targets
        self.stack = ExitStack()
        self.closed = False
        self.started = False
        self.stopped = False
        self.saved = None
        self.cleaned = False

    def readiness(self):
        return self.monitor.readiness() is True

    def start(self):
        from table_acceptance_pipeline_capture import instrument_transport_calls
        self.started = True  # A failed initial observation still needs cleanup.
        self.monitor.start()
        self.stack.enter_context(instrument_transport_calls(self.targets, self.monitor.completed_qdrant))

    def close_counters(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.stack.close()
        finally:
            self.monitor.close_counters()

    def finish(self, rss_coverage):
        if self.stopped:
            return
        self.stopped = True
        self.monitor.stop()
        self.saved = self.monitor.result()
        if rss_coverage:
            from table_acceptance_load import validate_resource_load_coverage
            validate_resource_load_coverage(self.saved, rss_coverage)

    def result(self):
        if self.saved is None:
            raise EnvironmentUnknown('Continuous load observation has not ended')
        return self.saved

    def cleanup(self):
        if self.cleaned:
            return
        self.cleaned = True
        try:
            if self.started:
                try:
                    self.close_counters()
                finally:
                    self.finish({})
        finally:
            self.client.close()


def create_observation(protocol, variant):
    # No app imports: the actual Settings/Pipeline lifecycle stays RSS-sampled.
    from types import SimpleNamespace
    from table_acceptance_pipeline_capture import qdrant_transport_targets
    values = private_config(protocol, variant)
    settings = SimpleNamespace(**values)
    settings.db_url = values['database_url']
    settings.qdrant_prefer_grpc = values.get('qdrant_prefer_grpc', False)
    settings.qdrant_grpc_port = values.get('qdrant_grpc_port', 6334)
    client = _checked_client(settings)  # Drain before cumulative counters open.
    try:
        targets = qdrant_transport_targets([client])
        observer = load_frozen(protocol['resource_environment']['observer_adapter'], 'observer')
        monitor = observer.create_monitor(settings, protocol)
        return ResourceObservation(monitor, client, targets)
    except BaseException:
        client.close()
        raise


class ResourceEnvironment:
    def __init__(self, protocol, variant, output_dir, *, observation):
        # Called only inside the RSS-sampled factory, never from module imports.
        from app.config import Settings
        values = private_config(protocol, variant)
        self.observation = observation
        self.monitor = observation.monitor
        self.protocol = protocol
        self.variant = variant
        self.output_dir = output_dir
        self.settings = Settings(_env_file=None, **values)
        from table_acceptance_storage import digest, capture_resource_snapshot
        if digest(self.settings.model_dump(mode='json')) != protocol['variants'][variant]['effective_settings_sha256']:
            raise EnvironmentUnknown('Effective settings differ from the frozen profile')
        import app.config
        app.config.get_settings = lambda: self.settings
        import app.db.session as db
        db.configure_for_tests(self.settings.db_url)
        from sqlalchemy import event
        event.listen(db.get_engine(), 'connect', configure_resource_connection)
        self.db = db
        # Preparer must already have created all schema/cache/collection state.
        # Missing state is invalid rather than repaired inside the measurement.
        if not self.settings.cache_dir.is_dir():
            raise EnvironmentUnknown('Prepared private cache is absent')
        from app.services.registry import DocumentRegistry
        from app.services import pipeline as pipeline_module
        self.registry = DocumentRegistry()
        pipeline_module.get_registry = lambda: self.registry
        self.pipeline = pipeline_module.Pipeline()
        self.pipeline.vector_store.client = observation.client
        self.inputs = capture_resource_snapshot(db.get_engine(), self.pipeline.vector_store.client,
            self.settings.qdrant_collection, self.settings.cache_dir,
            table_structure_enabled=self.settings.table_structure_enabled, kind='resource-inputs')
        from collect_table_resources import verify_resource_inputs
        verify_resource_inputs(protocol['variants'][variant], self.inputs)
        environment = protocol['resource_environment']
        self.doc_id = environment['doc_id']
        if not re.fullmatch('[a-f0-9]{16}', self.doc_id):
            raise EnvironmentUnknown('Private control document identity is unknown')
        self.source = Path(protocol['source']['path'])
        self.registry.create(self.doc_id, self.source.name, 'application/octet-stream', self.source.stat().st_size)
        self.oracle = load_frozen(environment['oracle_adapter'], 'oracle')
        self.stack = ExitStack()
        self.closed = False
        self.started = None
        self.ended = None
        self.active_generation = None
        self.usage = None
        self.cache = None

    def readiness(self):
        return self.monitor.readiness() is True

    def run(self, pipeline):
        if not self.readiness():
            raise EnvironmentUnknown('Independent service readiness unavailable')
        import litellm
        from table_acceptance_usage import CompletionUsageCapture
        from table_acceptance_cache import ClassifierCacheCapture
        from app.services import field_table, table_classification, table_pipeline
        self.usage = self.stack.enter_context(CompletionUsageCapture(litellm,
            started=self.monitor.model_started, finished=self.monitor.model_finished))
        self.cache = self.stack.enter_context(ClassifierCacheCapture(legacy=field_table,
            structural_targets=(table_classification, table_pipeline)))
        from app.services import embedder
        from table_acceptance_pipeline_capture import instrument_transport_calls, completed_ollama_http_error
        self.stack.enter_context(instrument_transport_calls(
            [(litellm, ['embedding'])], self.monitor.completed_embedding))
        self.stack.enter_context(instrument_transport_calls(
            [(embedder, ['embed_ollama'])], self.monitor.completed_embedding,
            completed_error=completed_ollama_http_error))
        self.started = time.monotonic()
        if not self.readiness():
            raise EnvironmentUnknown('Independent readiness lost before first provider call')
        pipeline._process(self.doc_id, self.source, self.source.name, [], resume=False, fresh_table_cache=False)

    def completion_close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.stack.close()  # Close streams before ending actual model ownership.
        finally:
            self.observation.close_counters()  # Continuous load runs through publication/cleanup.
            self.ended = time.monotonic()

    def publication_complete(self, pipeline):
        if not self.closed:
            raise EnvironmentUnknown('Completion boundary is still open')
        from app.db.models import DocumentGenerationState
        with self.db.session_scope() as session:
            state = session.get(DocumentGenerationState, self.doc_id)
            self.active_generation = state.active_generation_id if state else None
        document = self.registry.get(self.doc_id)
        return bool(self.active_generation and document['status'] == 'done' and document.get('problem') is None)

    def measurements(self):
        from table_acceptance_storage import capture_resource_snapshot
        from qdrant_client import QdrantClient
        from sqlalchemy import text
        if not self.closed or self.active_generation is None or self.usage is None or self.cache is None:
            raise EnvironmentUnknown('Full successful lifecycle is absent')
        with closing(QdrantClient(url=self.settings.qdrant_url, check_compatibility=False)) as reader:
            outputs = capture_resource_snapshot(self.db.get_engine(), reader, self.settings.qdrant_collection,
                self.settings.cache_dir, table_structure_enabled=self.settings.table_structure_enabled,
                kind='resource-outputs')
        correctness = self.oracle.verify_publication(self.db.get_engine(), self.settings,
            self.doc_id, self.active_generation, self.variant, self.protocol)
        if correctness.get('verified') is not True:
            raise EnvironmentUnknown('Independent publication oracle failed')
        with self.db.get_engine().connect() as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            database_bytes = connection.execute(text('SELECT pg_database_size(current_database())')).scalar_one()
        data_bytes = sum(path.stat().st_size for path in self.settings.data_dir.rglob('*') if path.is_file())
        usage = self.usage.result()
        if usage['llm_usage_missing'] or usage['llm_calls_inflight']:
            raise EnvironmentUnknown('Actual completion usage is incomplete')
        return {'status': 'done', 'problem': None, 'active_generation': self.active_generation,
                'elapsed_s': self.ended - self.started, 'data_bytes': data_bytes,
                'private_database_bytes': database_bytes, **usage,
                'classifier_cache': self.cache.result(), 'outputs': outputs,
                'correctness': correctness, 'load': self.observation.result()}

    def partial_measurements(self):
        # Preserve counters on invalid runs; no source/oracle/storage read is
        # allowed to turn a partial lifecycle into an accepted publication.
        result = {'status': 'partial-invalid', 'variant': self.variant,
                  'active_generation': self.active_generation,
                  'usage': self.usage.result() if self.usage is not None else None,
                  'classifier_cache': self.cache.result() if self.cache is not None else None}
        if self.closed and self.started is not None and self.ended is not None:
            result['elapsed_s'] = self.ended - self.started
            try:
                result['load'] = self.observation.result()
            except Exception as error:
                result['load'] = {'load_monitor_complete': False, 'error_type': type(error).__name__}
        return result

    def cleanup(self):
        try:
            self.completion_close()
        finally:
            self.pipeline._executor.shutdown(wait=False, cancel_futures=True)
            self.pipeline.vector_store.client.close()
            self.db.get_engine().dispose()


def create_environment(protocol, variant, output_dir, *, observation):
    environment = ResourceEnvironment.__new__(ResourceEnvironment)
    try:
        ResourceEnvironment.__init__(environment, protocol, variant, output_dir, observation=observation)
        return environment
    except BaseException as original_error:
        cleanup_errors = []
        pipeline = getattr(environment, 'pipeline', None)
        actions = []
        if pipeline is not None:
            executor = getattr(pipeline, '_executor', None)
            client = getattr(pipeline.vector_store, '_client', None)
            if executor is not None:
                actions.append(lambda: executor.shutdown(wait=False, cancel_futures=True))
            if client is not None:
                actions.append(client.close)
        database = getattr(environment, 'db', None)
        if database is not None:
            actions.append(lambda: database.get_engine().dispose())
        for action in actions:
            try:
                action()
            except BaseException as error:
                cleanup_errors.append(type(error).__name__)
        if cleanup_errors:
            raise EnvironmentUnknown('Private construction cleanup is incomplete') from original_error
        raise
