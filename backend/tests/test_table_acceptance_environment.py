"""Operational resource adapter rejects incomplete/foreign environments before imports."""
import importlib.util
from pathlib import Path
import pytest


def api():
    path=Path(__file__).parents[1]/'test_scripts/table_acceptance_environment.py'
    assert path.exists(), 'private operational resource adapter is missing'
    spec=importlib.util.spec_from_file_location('r2_environment',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def protocol(tmp_path):
    root=tmp_path/'private';root.mkdir()
    return {'source':{'path':str(root/'control.xlsx')},
        'resource_environment':{'private_root':str(root),'provider_routes':{'llm_base_url':'http://model.fixture.invalid/v1','embedding_api_base':'http://127.0.0.1:16410'},'settings':{'database_url':'postgresql+psycopg://postgres@127.0.0.1:15432/r2_fixture',
            'data_dir':str(root/'data'),'qdrant_url':'http://127.0.0.1:16333','qdrant_collection':'r2_evaluation_fixture',
            'embedding_api_base':'http://127.0.0.1:16410','embedding_model':'ollama/bge-m3',
            'llm_base_url':'http://model.fixture.invalid/v1'}},
        'variants':{'baseline':{'collection_name':'r2_evaluation_fixture','table_structure_enabled':False},
                    'after':{'collection_name':'r2_evaluation_fixture','table_structure_enabled':True}}}


@pytest.mark.parametrize('field,value', [('database_url','postgresql://reader@127.0.0.1:5432/production'),
    ('qdrant_collection','okf_knowledge_base'),('embedding_api_base','http://127.0.0.1:16400'),
    ('llm_base_url','http://unapproved.invalid/v1')])
def test_private_config_rejects_primary_or_wrong_route_before_application_import(tmp_path,field,value):
    p=protocol(tmp_path);p['resource_environment']['settings'][field]=value
    m=api()
    with pytest.raises(m.EnvironmentUnknown):m.private_config(p,'baseline')


def test_private_config_preserves_profile_and_applies_only_feature_variant(tmp_path):
    p=protocol(tmp_path);p['resource_environment']['settings']['llm_chunk_budget_seconds']=600
    result=api().private_config(p,'after')
    assert result['table_structure_enabled'] is True and result['llm_chunk_budget_seconds']==600


def test_private_data_escape_rejected(tmp_path):
    p=protocol(tmp_path);p['resource_environment']['settings']['data_dir']=str(tmp_path/'outside')
    m=api()
    with pytest.raises(m.EnvironmentUnknown):m.private_config(p,'baseline')


def test_unfrozen_adapter_is_not_executed(tmp_path):
    p=tmp_path/'adapter.py';p.write_text("raise AssertionError('must not execute')")
    m=api()
    with pytest.raises(m.EnvironmentUnknown):m.load_frozen({'path':str(p),'sha256':'0'*64},'observer')


def test_failed_construction_closes_only_already_created_private_resources(monkeypatch):
    from types import SimpleNamespace
    m=api();events=[]
    class Broken:
        def __init__(self,*args,**kwargs):
            self.pipeline=SimpleNamespace(_executor=SimpleNamespace(shutdown=lambda **kw:events.append('executor')),
                vector_store=SimpleNamespace(_client=SimpleNamespace(close=lambda:events.append('client'))))
            self.db=SimpleNamespace(get_engine=lambda:SimpleNamespace(dispose=lambda:events.append('db')))
            raise RuntimeError('private snapshot failed')
    monkeypatch.setattr(m,'ResourceEnvironment',Broken)
    with pytest.raises(RuntimeError,match='private snapshot failed'):m.create_environment({},'baseline',None,observation=None)
    assert events==['executor','client','db']


def test_run_balances_actual_usage_and_closes_boundary_before_publication_read(monkeypatch,tmp_path):
    from contextlib import ExitStack,contextmanager
    from types import SimpleNamespace
    import litellm
    m=api();events=[]
    from app.services import embedder
    native=lambda **kw:[[1.0]]
    monkeypatch.setattr(embedder,'embed_ollama',native,raising=False)
    monkeypatch.setattr(litellm,'completion',lambda **kw:iter([SimpleNamespace(usage=SimpleNamespace(prompt_tokens=12,completion_tokens=4))]))
    monkeypatch.setattr(litellm,'embedding',lambda **kw:SimpleNamespace(data=[1]))
    monitor=SimpleNamespace(readiness=lambda:True,start=lambda:events.append('monitor-start'),
        stop=lambda:events.append('boundary-closed'),model_started=lambda:events.append('model-start'),
        model_finished=lambda:events.append('model-finish'),completed_embedding=lambda:events.append('embedding'),
        completed_qdrant=lambda:events.append('qdrant'))
    def process(*args,**kw):
        list(litellm.completion(stream=True));litellm.embedding(input=['synthetic']);embedder.embed_ollama(inputs=['native'])
    @contextmanager
    def scope():
        events.append('publication-read')
        yield SimpleNamespace(get=lambda *args:SimpleNamespace(active_generation_id='b'*32))
    env=m.ResourceEnvironment.__new__(m.ResourceEnvironment)
    env.monitor=monitor;env.observation=SimpleNamespace(close_counters=monitor.stop);env.stack=ExitStack();env.targets=[];env.doc_id='a'*16;env.source=tmp_path/'control.xlsx'
    env.closed=False;env.active_generation=None;env.db=SimpleNamespace(session_scope=scope)
    env.registry=SimpleNamespace(get=lambda doc:{'status':'done','problem':None})
    pipeline=SimpleNamespace(_process=process)
    with pytest.raises(m.EnvironmentUnknown):env.publication_complete(pipeline)
    env.run(pipeline);env.completion_close()
    assert env.publication_complete(pipeline)
    assert env.usage.result()['input_tokens']==12 and env.usage.result()['llm_usage_missing']==0
    assert events.index('boundary-closed')<events.index('publication-read')
    assert events.count('model-start')==events.count('model-finish')==1
    assert events.count('embedding')==2
    assert embedder.embed_ollama is native


def test_measurements_closes_real_client_without_context_manager_protocol(monkeypatch,tmp_path):
    from contextlib import contextmanager
    from types import SimpleNamespace
    import qdrant_client,table_acceptance_storage
    m=api();events=[]
    monkeypatch.setattr(qdrant_client,'QdrantClient',lambda **kw:SimpleNamespace(close=lambda:events.append('client-closed')))
    monkeypatch.setattr(table_acceptance_storage,'capture_resource_snapshot',lambda *a,**kw:{'snapshot_kind':'resource-outputs'})
    @contextmanager
    def connect():yield SimpleNamespace(execute=lambda *a:SimpleNamespace(scalar_one=lambda:4096))
    (tmp_path/'result.json').write_text('{}')
    env=m.ResourceEnvironment.__new__(m.ResourceEnvironment)
    env.closed=True;env.active_generation='b'*32;env.doc_id='a'*16;env.variant='baseline';env.protocol={}
    env.started=1;env.ended=2
    env.settings=SimpleNamespace(qdrant_url='http://127.0.0.1:16333',qdrant_collection='r2_evaluation_fixture',cache_dir=tmp_path,table_structure_enabled=False,data_dir=tmp_path)
    env.db=SimpleNamespace(get_engine=lambda:SimpleNamespace(connect=connect))
    env.oracle=SimpleNamespace(verify_publication=lambda *a:{'verified':True})
    env.usage=SimpleNamespace(result=lambda:{'llm_usage_missing':0,'llm_calls_inflight':0})
    env.cache=SimpleNamespace(result=lambda:{})
    env.monitor=SimpleNamespace(result=lambda:{'load_monitor_complete':True});env.observation=env.monitor
    assert env.measurements()['status']=='done'
    assert events==['client-closed']


def test_partial_invalid_preserves_actual_usage_without_output_storage_reads():
    from types import SimpleNamespace
    m=api();env=m.ResourceEnvironment.__new__(m.ResourceEnvironment)
    env.variant='baseline';env.closed=True;env.started=1;env.ended=2;env.active_generation='b'*32
    env.usage=SimpleNamespace(result=lambda:{'llm_completion_invocations':1,'input_tokens':7,'output_tokens':3,'llm_usage_missing':0,'llm_calls_inflight':0})
    env.cache=SimpleNamespace(result=lambda:{'legacy':{'hits':1,'misses':0}})
    env.monitor=SimpleNamespace(result=lambda:{'load_monitor_complete':True});env.observation=env.monitor
    result=env.partial_measurements()
    assert result['status']=='partial-invalid' and result['usage']['input_tokens']==7
    assert result['classifier_cache']['legacy']['hits']==1 and result['active_generation']=='b'*32


def test_private_pg_identity_and_statement_timeout_survive_connection_rollback():
    from contextlib import contextmanager
    class Connection:
        autocommit = False
        def __init__(self):
            self.committed = {}
            self.pending = {}
        @contextmanager
        def cursor(self):
            yield self
        def execute(self, sql):
            key, value = sql.removeprefix('SET ').split('=', 1)
            (self.committed if self.autocommit else self.pending)[key] = value.strip("'")
        def rollback(self):
            self.pending.clear()
    connection = Connection()
    api().configure_resource_connection(connection, None)
    connection.rollback()
    assert connection.committed == {'application_name': 'r2-benchmark-resources', 'statement_timeout': '10s'}
    assert connection.autocommit is False


def test_private_pg_configuration_failure_restores_connection_mode():
    from contextlib import contextmanager
    class Connection:
        autocommit = False
        @contextmanager
        def cursor(self):
            raise RuntimeError('session configuration unavailable')
            yield
    connection = Connection()
    with pytest.raises(RuntimeError, match='session configuration unavailable'):
        api().configure_resource_connection(connection, None)
    assert connection.autocommit is False



def test_completion_boundary_does_not_stop_continuous_load_monitor():
    from contextlib import ExitStack
    from types import SimpleNamespace
    m=api();events=[]
    env=m.ResourceEnvironment.__new__(m.ResourceEnvironment)
    env.closed=False;env.stack=ExitStack()
    env.stack.callback(events.append,'streams-close')
    env.observation=SimpleNamespace(close_counters=lambda:events.append('counter-close'))
    env.monitor=SimpleNamespace(stop=lambda:events.append('load-stop'))
    env.completion_close()
    assert events==['streams-close','counter-close']


def test_prepared_observation_owns_prestate_calls_and_stops_even_without_environment(monkeypatch):
    from types import SimpleNamespace
    from contextlib import contextmanager
    import table_acceptance_pipeline_capture as capture
    m=api();events=[]
    @contextmanager
    def transport(targets,completed):
        events.append('transport-start')
        completed()
        try:yield
        finally:events.append('transport-stop')
    monkeypatch.setattr(capture,'instrument_transport_calls',transport)
    monitor=SimpleNamespace(start=lambda:events.append('load-start'),
        completed_qdrant=lambda:events.append('owned-prestate'),
        close_counters=lambda:events.append('counter-close'),
        stop=lambda:events.append('load-stop'),result=lambda:{'load_monitor_complete':False})
    observation=m.ResourceObservation(monitor,SimpleNamespace(close=lambda:events.append('client-close')),[])
    observation.start();observation.close_counters();observation.close_counters()
    observation.finish({});observation.cleanup()
    assert events==['load-start','transport-start','owned-prestate','transport-stop','counter-close','load-stop','client-close']



def test_observer_preparation_does_not_import_application_and_cleans_failed_monitor(monkeypatch,tmp_path):
    from types import SimpleNamespace
    import builtins,table_acceptance_pipeline_capture as capture
    m=api();events=[];p=protocol(tmp_path)
    monkeypatch.setattr(m,'_checked_client',lambda settings:SimpleNamespace(close=lambda:events.append('client-close')))
    monkeypatch.setattr(capture,'qdrant_transport_targets',lambda clients:[])
    def failed(*args):raise RuntimeError('observer unavailable')
    monkeypatch.setattr(m,'load_frozen',lambda *args:SimpleNamespace(create_monitor=failed))
    p['resource_environment']['observer_adapter']={}
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name=='app' or name.startswith('app.'):
            raise AssertionError('Application import happened before RSS')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    with pytest.raises(RuntimeError,match='observer unavailable'):m.create_observation(p,'baseline')
    assert events==['client-close']
